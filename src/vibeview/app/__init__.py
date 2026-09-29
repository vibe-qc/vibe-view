"""Trame web application for vibe-view.

Composes all renderers into a single-page interactive viewer:
- Left sidebar: section tree with lazy activation
- Central 3D viewport: structure + isosurfaces (PyVista/VTK)
- Right panel: per-section controls (sliders, dropdowns, etc.)
- Bottom panel: 2D plots (bands, spectra, trajectory energy)

The application state is driven by Trame's reactive state management.
Each UI control writes to a shared state dict; VTK widgets read from
it and re-render on change.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import math
import threading
import weakref
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import numpy as np
import pyvista as pv
from pyvista.plotting.colors import get_cmap_safe

from vibeview._fmt import human_size

# The package's other modules. Their names are re-exported here, so
# ``vibeview.app.<name>`` keeps working for every caller (#37-R6).
from vibeview.app.controls import (  # noqa: F401
    _DIALOG_FOCUS_JS,
    _build_controls,
    _dialog_focus_restore,
    _element_button_style,
    _periodic_table_cells,
)
from vibeview.app.layout import (  # noqa: F401
    _retaining_view_updater,
)
from vibeview.app.scene import (  # noqa: F401
    _ON_DOM_CHANGE_JS,
    _SCENE_HOOKS,
    _Actors,
    _scene_hooks,
    _SceneHooks,
)
from vibeview.app.sidebar import (  # noqa: F401
    _BASIS_AO_GROUP_ID,
    _DEFAULT_KIND_ICON,
    _KIND_ICONS,
    _KIND_TITLES,
    _ORBITAL_GROUP_ID,
    _build_section_list,
    _build_sidebar_entries,
    _builder_name,
    _iupac_name_entry,
    _kind_icon,
    _orbital_group_sidebar_title,
    _orbital_label,
    _sidebar_group_entry,
    _sidebar_section_title,
    _status_icon,
    _supported_kinds_set,
)
from vibeview.app.state import (  # noqa: F401
    _first,
    _ViewerUIState,
)
from vibeview.headless import new_plotter
from vibeview.plotter_compat import (
    cmap_argument,
    remove_actor,
    reset_camera,
    set_background,
    show_grid,
    view_isometric,
    view_xy,
    view_xz,
    view_yz,
)
from vibeview.qvf import RELOCALIZED_PREFIX, QVFError, clamp_replication
from vibeview.renderers import palette as _palette
from vibeview.renderers.cell import supercell_offsets
from vibeview.renderers.nci import RDG_ISOVALUE
from vibeview.units import ANGSTROM_TO_BOHR as _ANGSTROM_TO_BOHR
from vibeview.units import band_energy_unit as _band_energy_unit_key
from vibeview.units import energy_from_ev, energy_label, energy_to_ev, format_length
from vibeview.units import energy_unit as _energy_unit_key
from vibeview.units import length_unit as _length_unit_key
from vibeview.vibration_amplitude import DEFAULT_PEAK_EXCURSION_ANGSTROM
from vibeview.viewer_defaults import ViewerState

if TYPE_CHECKING:
    from collections.abc import Callable

    from vibeview.material_presets import MaterialPreset
    from vibeview.qvf import GridData, QVFReader, Section, StructureData
    from vibeview.renderers.volume import VolumeRenderer





# The server-side file dialog is synchronous, so a recursive scan must have a
# hard ceiling.  Ten thousand directory entries is enough for ordinary result
# trees while preventing a click on $HOME, a shared filesystem, or / from
# monopolising the Trame event loop indefinitely.  Opening a hundred archives
# in one action is already expensive; users can narrow the directory to load
# another batch.
_GUI_SCAN_MAX_ENTRIES = 10_000
_GUI_SCAN_MAX_CANDIDATES = 100
_GUI_SCAN_ERROR_SAMPLES = 3

# Directory names that are both high-volume and never useful as chemistry
# inputs. Hidden directories are handled separately, which covers .git,
# .cache, .venv, .tox, and editor metadata without maintaining a second list.
_GUI_SCAN_PRUNED_DIR_NAMES = frozenset(
    {
        "__pycache__",
        "cache",
        "caches",
        "env",
        "node_modules",
        "site-packages",
        "venv",
    }
)


@dataclass(frozen=True)
class _GuiDirectoryScanResult:
    """Bounded server-side directory scan returned to the file dialog."""

    candidates: tuple[Path, ...]
    scanned_entries: int
    pruned_directories: int
    traversal_error_count: int
    traversal_error_samples: tuple[str, ...]
    truncated: bool


def _is_pruned_gui_directory(path: Path) -> bool:
    """Whether a directory is hidden, a cache, or a virtual environment."""
    name = path.name
    if name.startswith(".") or name.casefold() in _GUI_SCAN_PRUNED_DIR_NAMES:
        return True
    # Virtual environments can have arbitrary directory names. Their root
    # marker is more reliable than guessing every possible spelling.
    try:
        return (path / "pyvenv.cfg").is_file()
    except OSError:
        # The traversal itself will report an unreadable directory if it is
        # visited; failing this optional marker check must not abort the scan.
        return False


def _scan_gui_directory(
    root: Path,
    *,
    recursive: bool,
    max_entries: int = _GUI_SCAN_MAX_ENTRIES,
    max_candidates: int = _GUI_SCAN_MAX_CANDIDATES,
) -> _GuiDirectoryScanResult:
    """Find supported files without unbounded or fragile traversal.

    ``os.scandir`` is used directly instead of ``Path.rglob`` so traversal is
    lazy, hidden/cache/venv subtrees can be pruned before descent, and errors
    from one directory do not discard files found elsewhere. Symlinks are not
    followed, avoiding cycles and surprising scans outside ``root``.
    """
    import os

    from vibeview.converters import is_supported_path
    from vibeview.trexio_import import is_trexio_directory

    if max_entries < 1 or max_candidates < 1:
        raise ValueError("GUI scan limits must be positive")

    pending = [Path(root)]
    candidates: list[Path] = []
    scanned_entries = 0
    pruned_directories = 0
    traversal_error_count = 0
    traversal_error_samples: list[str] = []
    truncated = False

    def record_error(path: Path, exc: BaseException) -> None:
        nonlocal traversal_error_count
        traversal_error_count += 1
        if len(traversal_error_samples) < _GUI_SCAN_ERROR_SAMPLES:
            detail = getattr(exc, "strerror", None) or str(exc) or type(exc).__name__
            traversal_error_samples.append(f"{path}: {detail}")

    while pending and not truncated:
        directory = pending.pop()
        child_directories: list[Path] = []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if scanned_entries >= max_entries:
                        truncated = True
                        break
                    scanned_entries += 1
                    entry_path = Path(entry.path)
                    try:
                        is_directory = entry.is_dir(follow_symlinks=False)
                    except OSError as exc:
                        record_error(entry_path, exc)
                        continue

                    if is_directory:
                        if _is_pruned_gui_directory(entry_path):
                            pruned_directories += 1
                        elif is_trexio_directory(entry_path):
                            candidates.append(entry_path)
                            if len(candidates) >= max_candidates:
                                truncated = True
                                break
                        elif recursive:
                            child_directories.append(entry_path)
                        continue

                    # Hidden files are OS/editor metadata much more often than
                    # useful chemistry inputs. Skip them consistently with
                    # hidden directories.
                    if entry.name.startswith("."):
                        continue
                    try:
                        if not entry.is_file(follow_symlinks=False):
                            continue
                    except OSError as exc:
                        record_error(entry_path, exc)
                        continue
                    try:
                        supported = is_supported_path(entry_path)
                    except Exception as exc:  # plugin probes are third-party code
                        record_error(entry_path, exc)
                        continue
                    if not supported:
                        continue
                    candidates.append(entry_path)
                    if len(candidates) >= max_candidates:
                        truncated = True
                        break
        except OSError as exc:
            record_error(directory, exc)
            continue

        if recursive and not truncated:
            # A stable order makes the capped subset predictable. Reverse for
            # the LIFO stack so the alphabetically first directory is visited
            # next.
            child_directories.sort(
                key=lambda path: str(path).casefold(), reverse=True
            )
            pending.extend(child_directories)

    return _GuiDirectoryScanResult(
        candidates=tuple(sorted(candidates, key=lambda path: str(path).casefold())),
        scanned_entries=scanned_entries,
        pruned_directories=pruned_directories,
        traversal_error_count=traversal_error_count,
        traversal_error_samples=tuple(traversal_error_samples),
        truncated=truncated,
    )


def _scan_gui_glob(
    pattern: str,
    *,
    recursive: bool,
    max_entries: int = _GUI_SCAN_MAX_ENTRIES,
    max_candidates: int = _GUI_SCAN_MAX_CANDIDATES,
) -> _GuiDirectoryScanResult:
    """Expand a file-picker glob lazily and with the directory-scan limits.

    Python's recursive glob follows directory symlinks and materialises an
    unbounded tree when used through :func:`glob.glob`.  The picker therefore
    sends recursive work through :func:`_scan_gui_directory`, whose traversal
    is bounded and symlink-safe.  Ordinary (non-``**``) patterns remain useful
    for selecting a small batch in one directory and are consumed lazily.
    """
    import glob

    from vibeview.converters import is_supported_path
    from vibeview.trexio_import import is_trexio_directory

    if max_entries < 1 or max_candidates < 1:
        raise ValueError("GUI scan limits must be positive")
    if recursive and "**" in pattern:
        raise ValueError(
            "Recursive ** globs are not supported. Enter the directory path "
            "and enable 'Search subdirectories recursively' instead."
        )

    candidates: list[Path] = []
    scanned_entries = 0
    error_count = 0
    error_samples: list[str] = []
    truncated = False

    for match in glob.iglob(pattern, recursive=recursive):
        if scanned_entries >= max_entries:
            truncated = True
            break
        scanned_entries += 1
        path = Path(match)
        try:
            if not (path.is_file() or is_trexio_directory(path)) or not is_supported_path(path):
                continue
        except Exception as exc:  # plugin probes are third-party code
            error_count += 1
            if len(error_samples) < _GUI_SCAN_ERROR_SAMPLES:
                detail = str(exc) or type(exc).__name__
                error_samples.append(f"{path}: {detail}")
            continue
        candidates.append(path)
        if len(candidates) >= max_candidates:
            truncated = True
            break

    return _GuiDirectoryScanResult(
        candidates=tuple(sorted(candidates, key=lambda path: str(path).casefold())),
        scanned_entries=scanned_entries,
        pruned_directories=0,
        traversal_error_count=error_count,
        traversal_error_samples=tuple(error_samples),
        truncated=truncated,
    )


def _gui_directory_scan_notice(result: _GuiDirectoryScanResult) -> str:
    """Short user-visible qualification for a directory-scan result."""
    notes: list[str] = []
    if result.pruned_directories:
        notes.append(
            f"{result.pruned_directories} hidden/cache/venv director"
            f"{'y' if result.pruned_directories == 1 else 'ies'} skipped"
        )
    if result.traversal_error_count:
        note = (
            f"{result.traversal_error_count} unreadable path"
            f"{'' if result.traversal_error_count == 1 else 's'} skipped"
        )
        if result.traversal_error_samples:
            note += f" ({'; '.join(result.traversal_error_samples)})"
        notes.append(note)
    if result.truncated:
        notes.append(
            f"scan limit reached after {result.scanned_entries} entries/"
            f"{len(result.candidates)} supported files; narrow the path for complete results"
        )
    return "; ".join(notes)












def _vibeqc_importable() -> bool:
    """Is the QVF *producer* available next to the viewer?

    vibe-view is a consumer and never writes result archives, but
    submitting a job as a QVF container means writing a *pending* one, and
    that producer lives in vibeqc (``write_pending_qvf``). vibeqc is not a
    vibe-view dependency, so container submission is offered only when the
    two happen to be installed together.

    Answers the question the submit path actually asks -- *will*
    ``from vibeqc import ...`` work -- rather than only the finder's version
    of it:

    * Already in ``sys.modules``? Then it imports, whatever its ``__spec__``
      says. A stub injected by a test or a conftest is importable.
    * Otherwise ask the finders, and treat any refusal as "not available".

    ``importlib.util.find_spec`` is not total: it raises ``ValueError`` for a
    ``sys.modules`` entry whose ``__spec__`` is ``None`` -- which is exactly
    what a bare ``types.ModuleType("vibeqc")`` is, and what
    ``tests/test_container_submission.py`` injects -- and it propagates
    whatever a meta-path finder raises. Unguarded, that turned a per-test skip
    into a collection error taking a whole file with it, and at runtime it
    would take ``create_app`` down (this is called from two places in it)
    with "vibeqc.__spec__ is None" rather than degrading to "no container
    submission". Found by the vibe-qc release chat, 2026-09-10.
    """
    import importlib.util
    import sys

    if "vibeqc" in sys.modules:
        return True
    try:
        return importlib.util.find_spec("vibeqc") is not None
    except (ImportError, ValueError):
        return False


def build_pending_container(sdata: StructureData, params: dict[str, Any], stem: str) -> Path:
    """Write a pending QVF job container for a viewer structure.

    Returns the archive path. Raises if vibeqc is unavailable or the
    structure cannot be expressed as a vibe-qc system.

    Units: QVF stores positions and lattice rows in Angstrom, while
    ``vibeqc.Atom`` / ``PeriodicSystem`` take **bohr** and PeriodicSystem
    wants a matrix whose COLUMNS are the lattice vectors -- the same two
    conversions ``input_generator`` documents for the generated-script
    path. Emitting Angstrom verbatim would shrink every geometry by
    0.529x.
    """
    import numpy as np
    import vibeqc as _vq

    from vibeview.input_generator import (
        _structure_calculation_params,
    )

    structure_params = _structure_calculation_params(sdata, atoms=params["atoms"])
    atoms = [
        _vq.Atom(
            int(a["atomic_number"]),
            [float(c) * _ANGSTROM_TO_BOHR for c in a["position"]],
        )
        for a in structure_params["atoms"]
    ]
    lattice = structure_params.get("lattice_vectors")
    if lattice is not None:
        rows = np.asarray(lattice, dtype=float)[:3] * _ANGSTROM_TO_BOHR
        system = _vq.PeriodicSystem(
            int(structure_params["dimensionality"]),
            rows.T.copy(),
            atoms,
            charge=int(params.get("charge", 0)),
            multiplicity=int(params.get("multiplicity", 1)),
        )
    else:
        system = _vq.Molecule(
            atoms,
            charge=int(params.get("charge", 0)),
            multiplicity=int(params.get("multiplicity", 1)),
        )
    return Path(_vq.write_pending_qvf(
        system,
        stem,
        method=params.get("method") or None,
        basis=params.get("basis") or None,
        functional=params.get("functional") or None,
    ))


def _triple(values: Sequence[int] | Sequence[float]) -> tuple[int, int, int]:
    """A replication request as the fixed-size tuple the renderers take."""
    return (int(values[0]), int(values[1]), int(values[2]))


# Units hint for the isovalue slider, per volume kind.
_ISOVALUE_UNITS: dict[str, str] = {
    "volume.density": "e/bohr³",
    "volume.spin": "e/bohr³ (α−β)",
    "volume.potential": "Eh/e",
    "volume.elf": "dimensionless",
    "volume.orbital": "e/bohr³",
    "volume.difference": "e/bohr³",
    "volume.generic": "arb. units",
    "volume.rdg": "dimensionless",
    "basis.ao": "e/bohr³",
}







def _qa_validate(reader: QVFReader) -> list[str]:
    """Check the QVF for common issues and return warning messages.

    Checks: SCF convergence, negative total energy (impossible),
    unusually high energy, missing structure section.
    """
    warnings: list[str] = []
    prov = reader.provenance

    # SCF convergence check.
    if prov.get("scf_converged") is False:
        warnings.append("⚠ SCF did not converge — energies and properties may be unreliable")

    # Energy sanity checks.
    energy = prov.get("scf_energy")
    energy_val = energy.get("value") if isinstance(energy, dict) else energy
    if energy_val is not None:
        try:
            e = float(energy_val)
            if e > 0:
                warnings.append("⚠ Total energy is positive — check charge/multiplicity")
            if e < -10000:
                warnings.append(
                    "⚠ Total energy is very large (< -10 000 Eh) — "
                    "check for basis-set linear dependence"
                )
        except (TypeError, ValueError):
            pass

    # Missing structure.
    if not reader.has_section("structure"):
        warnings.append("ℹ No structure section — 3D viewport will be empty")

    return warnings


_STRUCTURE_MEMO: weakref.WeakKeyDictionary[Any, tuple[int, StructureData]] = (
    weakref.WeakKeyDictionary()
)


def _current_structure(reader: QVFReader) -> StructureData:
    """``reader.read_structure()``, read once per geometry revision.

    For the read-only consumers that ran on every hover and several times
    over at startup (nine full reads to open a three-atom file, #37-45).
    The reader bumps ``geometry_revision`` on every edit, so an edit is
    never served stale. The result is shared: callers must not mutate it,
    and anything that edits geometry reads its own copy from the reader.
    """
    revision = getattr(reader, "geometry_revision", None)
    try:
        hit = _STRUCTURE_MEMO.get(reader)
    except TypeError:  # a test double that cannot be weakly referenced
        return reader.read_structure()
    if hit is not None and revision is not None and hit[0] == revision:
        return hit[1]
    structure = reader.read_structure()
    if revision is not None:
        _STRUCTURE_MEMO[reader] = (revision, structure)
    return structure


def _clamp_viewer_replication(reader: QVFReader, viewer_state: ViewerState) -> None:
    """Clamp a file's replication hint to its periodic axes, in place.

    A QVF may carry a ``replication`` viewer hint, and it lands in
    ``viewer_state`` before the user touches any control. For a 2D slab an
    ``Nz > 1`` hint would stack phantom sheets along the synthesized normal at
    load time, so clamp it the same way ``update_replication`` clamps the UI.
    """
    try:
        pbc = _current_structure(reader).pbc
    except Exception:
        return
    viewer_state.replication = clamp_replication(viewer_state.replication, pbc)


def _pick_residue(
    structure: StructureData,
    plotter: pv.Plotter,
    state: _ViewerUIState,
    position: Any,
    replication: tuple[int, int, int] = (1, 1, 1),
) -> tuple[Any, Any] | None:
    """Resolve a visible atom or ribbon surface hit to canonical residue identity."""
    import numpy as np

    from vibeview.renderers.structure import (
        _selected_atom_indices,
        _visible_atom_indices,
        parse_residue_selection,
    )

    point = np.asarray(position, dtype=float)
    if point.shape != (3,) or not np.isfinite(point).all():
        return None
    eligible = {(chain, seq) for chain, seq, _ in structure.ca_residues()}
    membership = {index: (chain, seq) for chain, residues in structure.chains().items()
                  for seq, indices in residues if (chain, seq) in eligible for index in indices}
    best: tuple[float, int | None] = (float("inf"), None)
    if state.representation_style == "cartoon":
        for name, actor in plotter.actors.items():
            if not name.startswith("cartoon_chain_"):
                continue
            mapper = actor.GetMapper()
            mapper.Update()
            mesh = mapper.GetInput()
            array = mesh.GetPointData().GetArray("residue_atom_index")
            if array is None or not mesh.GetNumberOfPoints():
                continue
            index = mesh.FindPoint(point)
            if index < 0:
                continue
            distance = float(np.linalg.norm(np.asarray(mesh.GetPoint(index)) - point))
            if distance < best[0]:
                best = (distance, int(array.GetTuple1(index)))
    else:
        terms, _ = parse_residue_selection(state.residue_selection or "")
        visible = _visible_atom_indices(structure, _selected_atom_indices(structure, terms),
                                        state.residue_visibility)
        for offset in supercell_offsets(structure.lattice_vectors, structure.pbc, replication):
            for index in visible & membership.keys():
                distance = float(np.linalg.norm(structure.atoms[index].position + offset - point))
                if distance < best[0]:
                    best = (distance, index)
    return membership.get(best[1]) if best[1] is not None and best[0] <= 2.5 else None


def _structure_display_options(state: _ViewerUIState) -> dict[str, Any]:
    """Persistent biomolecule controls shared by all scene rebuild paths."""
    return {
        "atom_color_mode": str(getattr(state, "atom_color_mode", "element") or "element"),
        "residue_visibility": str(getattr(state, "residue_visibility", "all") or "all"),
        "show_hydrogen_bonds": bool(getattr(state, "show_hydrogen_bonds", False)),
    }


def _build_structure_scene(
    reader: QVFReader,
    plotter: pv.Plotter,
    state: _ViewerUIState,
    replication: tuple[int, int, int],
    show_labels: bool,
    representation: str = "ball_and_stick",
    cartoon_color_mode: str = "chain",
    residue_selection: str = "",
) -> tuple[object | None, bool]:
    """Render the structure into ``plotter`` and set periodic flags.

    Returns ``(structure_renderer, is_periodic)``.  The caller must
    rebind any closure-captured ``structure_renderer`` / ``is_periodic``
    nonlocals from the return value.

    Side-effects on ``state``: sets ``is_periodic``, ``orbital_panel_title``,
    ``esp_available``, and ``status_message``.
    """
    from vibeview.renderers.structure import StructureRenderer

    structure_section = next((s for s in reader.sections if s.kind == "structure"), None)
    is_periodic = False
    structure_data = None
    if structure_section is not None:
        renderer = StructureRenderer(structure_section, reader)
        try:
            # The scene needs geometry and periodic metadata before it renders,
            # but a cartoon never needs atom-pair bond inference. Keep that
            # expensive work lazy until an atom/bond representation asks for it.
            structure_data = renderer.load_structure()
            is_periodic = bool(
                structure_data.lattice_vectors is not None and any(structure_data.pbc)
            )
            # Per-axis flags drive the replication UI: a 2D slab must not offer
            # an Nz control, because lattice[2] is a synthesized normal.
            pbc_a, pbc_b, pbc_c = (bool(p) for p in structure_data.pbc)
            state.pbc_a, state.pbc_b, state.pbc_c = pbc_a, pbc_b, pbc_c
            state.structure_dim = int(structure_data.dim)
            renderer.add_to_plotter(
                plotter,
                replication=replication,
                show_labels=show_labels,
                representation=representation,
                cartoon_color_mode=cartoon_color_mode,
                residue_selection=residue_selection,
                **_structure_display_options(state),
            )
        except QVFError as e:
            state.status_message = f"Structure load error: {e}"
            return None, False
    else:
        renderer = None
        state.status_message = "No structure section in file"

    state.is_periodic = is_periodic
    state.orbital_panel_title = _orbital_label(is_periodic, plural=True)
    _has_density = any(s.kind == "volume.density" for s in reader.sections)
    _has_esp = any(s.kind == "volume.potential" for s in reader.sections)
    state.esp_available = _has_density and _has_esp

    # Check for animatable sections
    state.has_animatable = any(
        s.kind in ("trajectory", "vibrations", "wavefunction.gto") for s in reader.sections
    )
    anim_kinds = {s.kind for s in reader.sections}
    if "trajectory" in anim_kinds:
        state.video_kind = "trajectory"
    elif "vibrations" in anim_kinds:
        state.video_kind = "vibration"
    elif "wavefunction.gto" in anim_kinds:
        state.video_kind = "orbital"
    else:
        state.video_kind = ""

    # ── Dipole moment arrow (J3) ────────────────────────────────────
    _remove_actors_by_prefix(plotter, _Actors.DIPOLE)
    if structure_data is not None:
        # Root, not provenance: QVF spec § 4.7. Reading it off provenance
        # returned None for every file ever written, so this arrow had never
        # once been drawn.
        dipole = reader.dipole_moment
        vec = dipole.get("vector_debye")
        if vec and len(vec) == 3:
            # Anchor at the molecular centroid.
            positions = np.array([a.position for a in structure_data.atoms])
            centroid = positions.mean(axis=0)
            vec_arr = np.array(vec, dtype=float)
            mag = float(np.linalg.norm(vec_arr))
            if mag > 0.001:
                # Scale: 1 D ≈ 0.3 Å visual length, cap at 3 Å.
                scale = min(0.3, 3.0 / max(mag, 0.001))
                arrow = pv.Arrow(
                    start=centroid,
                    direction=vec_arr,
                    scale=scale,
                    tip_radius=0.15,
                    shaft_radius=0.06,
                )
                plotter.add_mesh(
                    arrow, color=_palette.DIPOLE_ARROW, name="dipole_arrow", show_scalar_bar=False
                )

    # ── Wannier-centre overlay (cyclic cluster / CCM) ───────────────
    _draw_wannier_overlay(reader, plotter, getattr(state, "show_wannier_centers", False))

    return renderer, is_periodic


def _draw_wannier_overlay(reader: QVFReader, plotter: pv.Plotter, show: bool) -> int:
    """Draw (or clear) the Wannier-centre markers overlay.

    Returns the number of centres drawn. Always clears the previous actor
    first so a toggle-off or a file-switch removes stale markers.
    """
    _remove_actors_by_prefix(plotter, _Actors.WANNIER)
    if not show:
        return 0
    try:
        from vibeview.renderers.wannier import build_wannier_glyphs, read_wannier_centres

        centres = read_wannier_centres(reader)
        glyphs = build_wannier_glyphs(centres)
        if glyphs is not None:
            plotter.add_mesh(
                glyphs, color=_palette.WANNIER_CENTRE, name="wannier_center", show_scalar_bar=False
            )
        return len(centres)
    except Exception:
        return 0


def serve(app: Any, *, host: str = "127.0.0.1", port: int = 8080) -> None:
    """Start the Trame server. Called from the CLI after the app is built.

    ``app`` is the Trame ``Server`` object returned by :func:`create_app`,
    not a bare ASGI app. Trame's ``Server.start`` owns its own backend
    (uvicorn-driven under the hood) and shapes the request lifecycle
    around Trame's reactive state, so we delegate to it directly. An
    earlier version of this function passed the server to
    ``uvicorn.run`` which treated it as an ASGI 2.0 callable and
    returned 500s on every request (uvicorn's asgi2 middleware called
    ``self.app(scope)`` and the Trame ``Server`` is not callable).
    """
    # Disable HTTP caching so iterating on the template/JS bundle takes
    # effect on plain reload — otherwise Firefox + Chrome 304 the whole
    # bundle even after Cmd+Shift+R, masking server-side changes. The
    # patch is applied right before app.start so the WebAppServer's
    # constructor sees a dict-shaped HTTP_HEADERS and registers the
    # response-header middleware.
    from vibeview.netutil import disable_http_caching  # noqa: PLC0415

    disable_http_caching()
    # ``timeout=0`` disables wslink's default 300 s idle-reap. vibe-view is
    # interactive — users routinely Cmd+Tab away or reload the browser,
    # and an auto-shutdown surprises them ("server died"). Run until the
    # user hits Ctrl+C.
    # Note: an earlier theory blamed ``show_connection_info=False`` for the
    # "blank Vue viewport" bug. The real cause was Vue-3 template bugs (an
    # unparseable ``:src=`` binding and a duplicate ``<vtk-local-view>``
    # ref). Those are fixed below, so the flag is functionally harmless
    # either way — keeping it at the default keeps Trame's startup banner
    # visible and satisfies the regression test that pins it
    # (test_serve_does_not_suppress_connection_info).
    app.start(host=host, port=port, open_browser=False, timeout=0)


def _smiles_available() -> bool:
    """Is the optional [smiles] extra installed?

    Only gates the hint text, not the field: a user who types a SMILES
    without the extra should get the install command, not a control that
    silently is not there.
    """
    try:
        import rdkit  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True




def _naming_available() -> bool:
    """Whether the optional ``vibeqc_naming`` package can be imported.

    It is not declared as a vibe-view dependency and is not published in any
    wheel (vibe-qc's ``wheel.packages`` ships only ``python/vibeqc``), so it
    resolves when running from a repo checkout with ``python/`` on the path.
    The standalone installer wires exactly that for the viewer environment
    (a ``.pth`` file pointing at the checkout's ``python/`` directory), so
    naming works in the source-installed desktop app too. Naming-dependent
    UI hides itself when this is False rather than offering controls that
    cannot work.
    """
    from importlib.util import find_spec

    try:
        return find_spec("vibeqc_naming") is not None
    except (ImportError, ValueError):
        return False




def _bfactor_range_message(reader: QVFReader) -> str:
    """What the b-factor ramp spans for this file, as a status line.

    The ramp is normalised per structure (b-factors from different
    refinements are not comparable), so the colours are uninterpretable
    without their range. When nothing carries a b-factor the renderer
    falls back to chain colour, which looks like the mode failed unless
    it is named.
    """
    try:
        structure = reader.read_structure()
    except Exception:  # noqa: BLE001 — a status line must never be the thing that raises
        return "B-factor colouring: no structure to read."
    measured = [
        float(b) for a in structure.atoms if (b := getattr(a, "b_factor", None)) is not None
    ]
    if not measured:
        return (
            "B-factor colouring: this file carries no b-factors "
            "(showing chain colour). Computed structures have none; "
            "they come from an experimental PDB."
        )
    return (
        f"B-factor colouring: {min(measured):.2f} to {max(measured):.2f} "
        f"over {len(measured):,} atoms, blue is low."
    )


def _default_representation(reader: QVFReader) -> str:
    """Pick the representation a file should open in.

    Ball-and-stick is the right default for a molecule and the wrong one
    for a protein, and not merely on aesthetics: it needs inferred bonds,
    and bond inference is superlinear. Measured on a 13,772-atom protein,
    ``StructureRenderer.load()`` takes 158.7 s against 1.2 s to spline the
    same file's backbone; at 150k atoms it does not finish. Because the
    Trame server binds its port only after the first render, that cost is
    paid before the page is reachable at all.

    So a structure that carries a splineable backbone opens as a ribbon,
    matching what PyMOL, ChimeraX and VMD do with a protein. Anything
    else is unaffected and still opens ball-and-stick. The user can
    switch either way from the Representation picker.
    """
    try:
        structure = _current_structure(reader)
    except Exception:  # noqa: BLE001 — a non-structure file just uses the default
        return "ball_and_stick"
    try:
        if structure.has_residues and len(structure.backbone_trace()) >= 4:
            return "cartoon"
    except Exception:  # noqa: BLE001 — malformed residue metadata is not fatal
        pass
    return "ball_and_stick"


def _selectable_chains(reader: QVFReader) -> list[str]:
    """Chain ids a residue selection can name, for the UI's hint text.

    A chainless PDB groups under the empty chain id, which cannot be typed,
    so it is dropped here and reached with the ``*`` wildcard instead.
    Control visibility is tracked separately because that legitimate
    chainless case also produces an empty list.
    """
    try:
        structure = _current_structure(reader)
        chains: list[str] = []
        for chain, _seq, _index in structure.ca_residues():
            if chain and chain not in chains:
                chains.append(chain)
        return chains
    except Exception:  # noqa: BLE001 — no structure section, or unreadable
        return []


def _residue_selection_available(reader: QVFReader) -> bool:
    """Whether the loaded structure has at least one selectable CA residue."""
    try:
        return bool(_current_structure(reader).ca_residues())
    except Exception:  # noqa: BLE001 — no structure section, or unreadable
        return False


def _residue_selection_summary(reader: QVFReader, spec: str) -> str:
    """Human-readable result of applying a residue selection to ``reader``."""
    if not spec.strip():
        return ""

    from vibeview.renderers.structure import summarize_selection

    try:
        structure = reader.read_structure()
    except Exception:  # noqa: BLE001 — no structure to select in
        return "no structure to select in"

    matched, chains, complaints = summarize_selection(structure, spec)
    parts: list[str] = []
    if matched:
        # A PDB with blank cols 22 has one chain whose id is the empty
        # string; printing it leaves a dangling "in chain " with nothing
        # after it.
        named = [c or "(unnamed)" for c in chains]
        parts.append(
            f"{matched} residue{'s' if matched != 1 else ''} in "
            f"chain{'s' if len(named) != 1 else ''} " + ", ".join(named)
        )
    else:
        parts.append("nothing selected")
    parts.extend(complaints)
    return " — ".join(parts)






# One ambient-occlusion configuration for every path that applies the pass
# (startup, file switch, toggle, scene rebuild); they used to differ (#37-32).
_SSAO_PASS: dict[str, Any] = {"radius": 2.0, "samples": 16}


def _probe_is_periodic(reader: QVFReader) -> bool:
    """Cheap periodicity probe for sidebar titles built before the scene.

    Only the structure is parsed (no bonds, no rendering). Shared by the
    initial sidebar and the file-switch rebuild, which used the reset
    ``False`` and so titled a crystal's orbitals "Molecular" (#37-6).
    """
    if not any(s.kind == "structure" for s in reader.sections):
        return False
    try:
        # The probe needs only the lattice. Bond inference costs 158.7 s
        # on a measured 13,772-atom protein and must stay off the startup
        # path until a bond-bearing representation is actually rendered.
        sdata = _current_structure(reader)
        return bool(sdata.lattice_vectors is not None and any(sdata.pbc))
    except Exception:
        return False


# Natural-orbital lists mark the frontier as HONO/LUNO
# (renderers/wavefunction.py); the HOMO/LUMO buttons and the picker default
# accept either spelling (#37-22).
_FRONTIER_ALIASES: dict[str, tuple[str, ...]] = {
    "HOMO": ("HOMO", "HONO"),
    "LUMO": ("LUMO", "LUNO"),
    "HONO": ("HONO", "HOMO"),
    "LUNO": ("LUNO", "LUMO"),
}


def _frontier_row(rows: Sequence[dict[str, Any]] | None, which: str) -> dict[str, Any] | None:
    """First MO row whose title carries the frontier marker ``which``.

    Matches whole title tokens, so ``HOMO`` finds a canonical HOMO or a
    natural-orbital HONO row.
    """
    marker = str(which).upper()
    names = _FRONTIER_ALIASES.get(marker, (marker,))
    for name in names:
        for row in rows or []:
            if name in str(row.get("title") or "").split():
                return row
    return None


def _spawn_task(coro: Any, *, run_if_no_loop: bool = False) -> Any:
    """Schedule ``coro`` on the running loop; return the task (or ``None``).

    ``asyncio.ensure_future`` outside a running loop raises (Python 3.14:
    "There is no current event loop") after the controller had already done
    its work, so a headless call crashed at the very end (#37-40). Without a
    loop the coroutine is either run to completion (``run_if_no_loop``: a
    finalizer whose state the caller needs) or closed unawaited.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        if run_if_no_loop:
            asyncio.run(coro)
        else:
            coro.close()
        return None
    return loop.create_task(coro)






def _auto_save_interval_seconds(value: Any) -> int:
    """A usable session auto-save interval in seconds (default 60, at least 5)."""
    value = _first(value)
    try:
        seconds = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 60
    return max(5, seconds)


def _scene_surface_mesh(plotter: Any) -> Any:
    """Merge the plotter's visible mesh actors into one world-space triangle mesh.

    Used by the STL export, which (unlike OBJ/glTF) has no scene exporter.
    Each actor's user/position transform is applied so the merged mesh
    matches what is on screen. Raises ``ValueError`` when nothing is drawn.
    """
    parts = []
    for actor in list(plotter.actors.values()):
        try:
            if not actor.GetVisibility():
                continue
            mapper = actor.GetMapper()
            data = mapper.GetInput() if mapper is not None else None
        except AttributeError:
            continue  # text/scalar-bar actors carry no mesh
        if data is None or data.GetNumberOfPoints() == 0:
            continue
        mesh = pv.wrap(data)
        if not isinstance(mesh, pv.PolyData):
            mesh = mesh.extract_surface()
        mesh = mesh.triangulate()
        matrix = actor.GetMatrix()
        transform = np.array([[matrix.GetElement(i, j) for j in range(4)] for i in range(4)])
        if not np.allclose(transform, np.eye(4)):
            mesh = mesh.transform(transform, inplace=False)
        if mesh.n_cells:
            parts.append(mesh)
    if not parts:
        raise ValueError("nothing to export: the scene has no mesh actors")
    merged = parts[0].merge(parts[1:]) if len(parts) > 1 else parts[0]
    if not isinstance(merged, pv.PolyData):
        merged = merged.extract_surface()
    return merged.triangulate()


def create_app(readers: QVFReader | Sequence[QVFReader]) -> Any:
    """Build the Trame application.

    Parameters
    ----------
    readers
        A single :class:`QVFReader` or a list of them. When more than
        one, a Files dropdown appears in the app bar for switching
        between files in the same session.

    Returns a Trame-compatible ASGI app.
    """
    if isinstance(readers, (list, tuple)):
        if not readers:
            raise ValueError("create_app requires at least one QVFReader")
        _all_readers = list(readers)
    else:
        _all_readers = [readers]
    reader = _all_readers[0]  # rebindable via nonlocal in switch_file below

    try:
        # The widget modules (F401) are imported only so a missing [viewer]
        # extra fails with the hint below rather than inside the layout;
        # vibeview.app.layout imports them itself.
        from trame.app import get_server
        from trame.ui.vuetify3 import VAppLayout  # noqa: F401
        from trame.widgets import client, html  # noqa: F401
        from trame.widgets import vuetify3 as v  # noqa: F401
        from trame_vtk.modules import vtk as vtk_module
        from trame_vtk.widgets.vtk import VtkLocalView  # noqa: F401
    except ImportError as e:
        from vibeview.install_hints import install_hint

        # Name an extra that exists: this used to say ``.[gpu]``, which is not
        # one of vibe-view's extras (viewer/capture/tui/ase/... ), so the one
        # message a newcomer sees pointed at an install command that fails.
        raise ImportError(
            f"the interactive viewer needs the [viewer] extra ({e}).\n"
            f"  {install_hint('viewer')}\n"
            f"`vibe-view show` and `vibe-view capture` need no extra."
        ) from None

    # ── Application state ─────────────────────────────────────────────
    viewer_state = ViewerState.from_manifest(
        reader.viewer_defaults, {s.id: s.kind for s in reader.sections}
    )
    _clamp_viewer_replication(reader, viewer_state)
    replication = list(viewer_state.replication)

    # Prepare section list for the sidebar
    section_list: list[dict[str, Any]] = _build_section_list(reader)

    # Collapse multiple volume.orbital sections into one sidebar entry; the
    # right-panel select then switches between them. Other kinds (density,
    # spectra, …) each keep their own entry with a friendly title.
    orbital_sections = [s for s in section_list if s["kind"] == "volume.orbital"]
    # Same pattern for basis.ao sections.
    basis_ao_sections = [s for s in section_list if s["kind"] == "basis.ao"]

    # Cheap early periodicity probe — the orbital sidebar-group title
    # ("Molecular Orbitals" vs "Crystalline Orbitals") needs the flag
    # before _build_structure_scene runs and rebinds it authoritatively
    # below. Only the structure JSON is parsed here; no rendering.
    # (Regression: a782ed40 referenced is_periodic here while it was
    # first assigned ~500 lines later → UnboundLocalError on every QVF
    # with volume.orbital sections.)
    is_periodic = _probe_is_periodic(reader)

    sidebar_entries: list[dict[str, Any]] = _build_sidebar_entries(
        reader, section_list, is_periodic
    )
    # Options for the right-panel orbital picker.
    orbital_options = [{"title": s["id"], "value": s["id"]} for s in orbital_sections]
    # Options for the right-panel AO picker.
    ao_options = [{"title": s["id"], "value": s["id"]} for s in basis_ao_sections]

    # ── Build PyVista scene ────────────────────────────────────────────
    plotter = new_plotter()

    # ── SSAO for server-side renders (screenshots, video frames) ──
    # A renderer pass never reaches the client's live viewport — vtk.js
    # redraws from serialized actors — so this shapes exports only.
    # Matches the ssao_enabled state default (True); radius/samples are the
    # shared _SSAO_PASS every other path uses.
    try:
        from vibeview.material_presets import ambient_occlusion_pass

        ambient_occlusion_pass(plotter, **_SSAO_PASS)
    except Exception:
        pass  # SSAO not available on this VTK build

    # ── Trame server setup (must come before structure loading so
    #     we can report errors through the UI status bar) ──────────────
    server = get_server(client_type="vue3")
    server.enable_module(vtk_module)

    # trame's server is a process-wide singleton, so every create_app call
    # appended its state watchers to the previous app's: a second viewer in
    # one process ran each play/stop, monitor and MO-click handler twice,
    # once against the dead app's plotter (#37-13). Unregister the previous
    # app's watchers and register through _watch_state from here on.
    for _key, _func in getattr(server, "_vibeview_state_watchers", ()):
        _callbacks = server.state._change_callbacks.get(_key)
        if _callbacks:
            _callbacks[:] = [entry for entry in _callbacks if entry[0] is not _func]
    server._vibeview_state_watchers = []

    def _watch_state(*keys: str) -> Callable[[Any], Any]:
        """``@server.state.change`` that the next create_app can undo."""

        def register(func: Any) -> Any:
            server.state.change(*keys)(func)
            for key in keys:
                server._vibeview_state_watchers.append(
                    (server.state._translator.translate_key(key), func)
                )
            return func

        return register

    def _counted_view_update() -> None:
        """``ctrl.view_update()`` that also bumps the push-generation counter.

        Seven controllers called ``ctrl.view_update()`` directly, bypassing
        the counter ``_push_view`` keeps, so the section dispatcher could not
        tell that they had already pushed (#37-21). Best-effort like
        ``_push_view``: a client that went away must not break the caller.
        """
        _scene_hooks(plotter).push_generation += 1
        with contextlib.suppress(Exception):
            server.controller.view_update()
    from vibeview.settings import load_settings

    persisted_settings = load_settings()
    # Saved settings seed the session they were saved for (#37-3): the
    # dialog persisted them but startup hard-coded the defaults, so the
    # dark-background, material and auto-save choices never survived a
    # restart.
    _startup_dark = bool(persisted_settings.get("dark_background", True))
    _startup_preset = str(persisted_settings.get("material_preset") or "cpk_glossy")
    from vibeview.material_presets import MATERIAL_PRESETS

    if _startup_preset not in MATERIAL_PRESETS:
        _startup_preset = "cpk_glossy"
    _startup_autosave = _auto_save_interval_seconds(persisted_settings.get("auto_save_interval"))
    _startup_energy_unit = _energy_unit_key(persisted_settings.get("energy_unit"))
    _startup_length_unit = _length_unit_key(persisted_settings.get("measure_unit"))
    _startup_band_energy_unit = _band_energy_unit_key(persisted_settings.get("band_energy_unit"))
    server.state.update(
        {
            "section_list": section_list,
            "sidebar_entries": sidebar_entries,
            "selected_section": None,
            "active_volume_id": None,
            "isovalue": 0.05,
            "colormap": "viridis",
            "opacity": 0.6,
            # Orbital lobes keep their own opacity, separate from the volume
            # isosurface one above: volume activation overwrites `opacity`
            # from the volume hints, which clobbered the orbital setting.
            # Defaults opaque — any value < 1.0 puts vtk.js into
            # depth-peeling, which is what hung the client on MO renders
            # (confirmed 2026-07-19: opaque renders fine, translucent hangs).
            "mo_opacity": 1.0,
            # ESP-mapped density: when colour_by_esp is True, the active
            # volume.density isosurface is coloured by electrostatic-potential
            # values from a companion volume.potential section instead of the
            # density scalar field.
            "color_by_esp": False,
            "esp_available": False,
            # Multi-isosurface layers — each dict has isovalue, opacity, colour.
            "extra_isosurfaces": [],
            "replication_nx": replication[0],
            "replication_ny": replication[1],
            "replication_nz": replication[2],
            "is_periodic": False,
            # Per-axis periodicity. Non-periodic axes get no replication control:
            # their lattice column is synthesized bookkeeping, not a cell edge.
            "pbc_a": False,
            "pbc_b": False,
            "pbc_c": False,
            "structure_dim": 3,
            # Grid-level periodic orbital replication (v2.0).
            # 0 = single cell, N = (2N+1)³ supercell; tiles the
            # scalar field before marching cubes for seamless orbitals.
            "periodic_replication": 0,
            "periodic_replication_options": [
                {"title": "Single cell", "value": 0},
                {"title": "3×3×3", "value": 1},
                {"title": "5×5×5", "value": 2},
                {"title": "7×7×7", "value": 3},
            ],
            # Cyclic-cluster wrap: roll a face-straddling orbital / Wannier
            # function whole into the cell (minimum-image). Periodic-only.
            "wrap_periodic_orbital": False,
            # Wannier-centre overlay (x_ccm.wannier_centers): shown-when-present
            # gate + user toggle.
            "has_wannier_centers": False,
            "show_wannier_centers": False,
            # ``show_atom_labels`` lives lower next to the other display toggles.
            # Atomic-properties overlay state. ``atom_properties_active`` is
            # the gate the right-panel controls watch; ``charge_kind`` picks
            # between Mulliken / Löwdin / Hirshfeld / IAO; ``color_by_charge``
            # swaps the CPK palette for a sign-based red/blue tint.
            "atom_properties_active": False,
            "atom_properties_section_id": None,
            "charge_kind": "mulliken",
            "charge_kind_options": [
                {"title": "Mulliken", "value": "mulliken"},
                {"title": "Löwdin", "value": "loewdin"},
                {"title": "Hirshfeld", "value": "hirshfeld"},
                {"title": "IAO", "value": "iao"},
            ],
            "color_by_charge": False,
            "colormap_options": [
                "viridis",
                "plasma",
                "inferno",
                "magma",
                "cividis",
                "coolwarm",
                "RdBu",
                "PiYG",
                "seismic",
                "Spectral",
            ],
            "volume_loaded": False,
            "bands_image": None,
            "bands_html": None,
            "bands_title": "Band Structure",
            # Energy window for the bands / DOS charts (#26). All-electron
            # periodic archives carry core states thousands of eV below E_F,
            # and an autoscaled axis flattens the valence region into a few
            # pixels around the E_F line. The activators resolve a window —
            # the manifest hint, else the renderer's valence default — and
            # write it back here, so the fields always show what is drawn.
            # `bands_window_auto` records whether that resolved window is
            # still the default or the reader has typed one in.
            "bands_window_available": False,
            "bands_window_auto": True,
            # Display unit of the bands / DOS energy axis and of the window
            # fields (#58); the archive and the window logic stay in eV.
            "band_energy_unit": _startup_band_energy_unit,
            "band_energy_label": energy_label(_startup_band_energy_unit),
            "bands_emin": None,
            "bands_emax": None,
            "phonon_html": None,
            "phonon_title": "Phonon",
            "eos_html": None,
            "eos_title": "Equation of State",
            "spectra_image": None,
            "spectra_html": None,
            "spectra_title": "Spectrum",
            # Multi-file spectrum comparison (design refresh 2026, item 8).
            "spectra_compare": False,
            # Spectra display controls: broadening multiplier + normalization.
            "spectra_gamma_scale": 1.0,
            "spectra_normalize": False,
            "spectra_x_unit": "native",
            "spectra_display": "both",  # both | envelope | sticks
            # Structure Library (design refresh 2026, item 7): curated
            # molecule DB from vibeqc_naming; [] when not installed.
            "library_query": None,
            "library_names": _library_structure_names(),
            # Recently used library structures, most-recent-first. Picking from
            # 83 names via autocomplete is tedious when you keep reaching for
            # the same handful, so used names come back as one-click chips.
            "library_recent": [],
            # Pinned library structures. Unlike library_recent (implicit,
            # session-scoped) these are chosen deliberately, so they ride in
            # the saved session alongside bookmarks.
            "library_favorites": [],
            # The details panel is a srcdoc iframe fed by renderers, and
            # much of what it shows is untrusted archive content: run
            # logs, embedded attachments, .bib text. Its iframe carries an
            # empty sandbox and there is no way to ask for scripts —
            # anything that needs them uses the chart channel below.
            "properties_html": None,
            "properties_title": "Details",
            # Chart channel: renderer-built Plotly figures that happen to
            # land in the bottom panel (SCF convergence, COOP/COHP, the
            # orbital energy diagram). Plotly is JavaScript, so this
            # iframe runs with `allow-scripts` — which is why it is a
            # channel of its own and no renderer of archive *text* can
            # reach it. Same arrangement as bands_html / eos_html /
            # spectra_html, which are scripted for the same reason.
            "chart_html": None,
            "chart_title": "Chart",
            # Attachments of the active run.record, for the download row.
            # Metadata only — attachment bytes are read on demand and go
            # straight to a download, never into the panel DOM.
            "run_record_attachments": [],
            "run_record_attachment_section": "",
            # SCF convergence plot: drop the initial-guess point, whose energy
            # dominates the Y scale and flattens the converged tail.
            "scf_skip_guess": False,
            # Plot |E - E_final| on a log axis so the convergence tail is
            # readable (design refresh 2026, SCF item).
            "scf_log_energy": False,
            # User-resizable side panels (drag the inner edge; px).
            "left_panel_width": 280,
            "right_panel_width": 300,
            "trajectory_energy_image": None,
            "trajectory_frame": 0,
            "trajectory_n_frames": 0,
            "trajectory_playing": False,
            "vibration_mode": 0,
            "vibration_n_modes": 0,
            "vibration_frequencies": [],
            "vibration_mode_items": [],
            "vibration_ir_intensities": [],
            # Orbital picker (right-panel dropdown for switching between
            # volume.orbital sections when they're collapsed in the sidebar).
            "orbital_options": orbital_options,
            "selected_orbital": (orbital_options[0]["value"] if orbital_options else None),
            "ao_options": ao_options,
            "selected_ao": (ao_options[0]["value"] if ao_options else None),
            # Fermi-surface band selector (populated when a fermi_surface
            # section is active; None selection means "all bands").
            "fermi_band_options": [],
            "fermi_selected_bands": None,
            # Phase D1: compare/overlay mode (active when >1 file is loaded).
            "compare_mode": False,
            "compare_align": False,
            "compare_legend": [],
            "compare_highlight": None,  # index of file to highlight, or None for all
            "compare_highlight_options": [],  # populated when files change
            # Phase D2: density-difference (A - B) between two loaded files.
            "density_file_options": [
                {"title": _job_name(r), "value": i}
                for i, r in enumerate(_all_readers)
                if any(s.kind == "volume.density" for s in r.sections)
            ],
            "diff_a": None,
            "diff_b": None,
            "vibration_amplitude": 1.0,
            # Peak atomic excursion (Å) at amplitude 1.0, shared with the
            # terminal (vibeview.vibration_amplitude, #38-28).
            "vibration_peak_default": DEFAULT_PEAK_EXCURSION_ANGSTROM,
            "vibration_playing": False,
            "vibration_phase": 0.0,
            "status_message": "",
            "crossfade_active": False,
            "crossfade_blend": viewer_state.crossfade_blend,
            # Wavefunction (MO) state
            "wf_section_id": None,
            # Click-to-render from the orbital energy diagram: the sandboxed
            # diagram iframe postMessages "{spin}:{index}@{nonce}" here; a
            # state-change watcher renders that MO (design refresh 2026).
            "mo_click_request": "",
            "wf_mo_rows": [],
            # Display unit for orbital energies (#58); archives stay in Eh.
            "energy_unit": _startup_energy_unit,
            # Display unit for measured lengths (#58); structures stay in Å.
            "measure_unit": _startup_length_unit,
            "wf_selected_mo": "restricted:0",
            "wf_selected_spin": "restricted",
            # Re-localize (vibeview.relocalize): ask vibe-qc for a
            # localization criterion the file does not already contain.
            # ``None`` = not yet probed, mirroring ``live_opt_available``.
            "relocalize_available": None,
            "relocalize_backend_python": persisted_settings.get("relocalize_backend_python", ""),
            "settings_relocalize_backend_python": persisted_settings.get(
                "relocalize_backend_python", ""
            ),
            "relocalize_capabilities": {},
            "relocalize_probe_running": False,
            "relocalize_backend_status": "Backend not checked",
            "relocalize_source": "supplied",
            "relocalize_reason": "Check the vibe-qc backend in Settings",
            "relocalize_result_summary": "",
            "relocalize_charge_rows": [],
            "relocalize_has_overlay": False,
            "relocalize_running": False,
            "relocalize_status": "",
            "relocalize_method": "ibo",
            "relocalize_method_options": [],
            "wf_n_per_dim": 60,
            "wf_component": "real",
            "wf_is_complex": False,
            "mo_last_component": "real",
            "relocalize_supported": False,
            # Total density vs spin density (rho_alpha - rho_beta). The
            # button below drives both; spin needs an unrestricted section.
            "wf_density_spin": False,
            # Gates the spin-density button; set per activated section.
            "wf_spin_unrestricted": False,
            # ELF isosurface level. 0.8 is the conventional choice for
            # showing shells / bonds / lone pairs; the density's 0.05 default
            # would be meaningless for a function bounded in [0, 1].
            "wf_elf_iso": 0.8,
            # NCI reduced-gradient isosurface level: the NCIPLOT plotting
            # convention, shared with the stored volume.rdg path (nci.py).
            "wf_nci_iso": RDG_ISOVALUE,
            # del^2 rho isosurface level (e/bohr^5). Drawn as a signed pair;
            # the NEGATIVE lobe is the chemically interesting one (Bader's
            # charge concentration), which is why the default is modest.
            "wf_laplacian_iso": 1.0,
            "wf_orbital_kind": "canonical",
            "wf_animating": False,
            "wf_anim_speed": 0.5,  # seconds per frame
            # Panel titles — dynamic based on is_periodic + orbital_kind
            "orbital_panel_title": "Molecular Orbitals",
            "wf_panel_title": "Molecular Orbitals",
            # Structure representation style. Biomolecules open as a
            # ribbon: see _default_representation().
            "representation_style": _default_representation(reader),
            # Cartoon ribbon colouring: "chain" or "structure".
            "cartoon_color_mode": "chain",
            "atom_color_mode": "element",
            "structure_color_mode": (
                "chain" if _default_representation(reader) == "cartoon" else "element"
            ),
            "residue_visibility": "all",
            "residue_pick_mode": False,
            "show_hydrogen_bonds": False,
            # Residue/chain selection (D4). Empty selects nothing; matching
            # residues render white over the active ribbon colour.
            "residue_selection": "",
            "residue_selection_summary": "",
            "residue_selection_chains": _selectable_chains(reader),
            "residue_selection_available": _residue_selection_available(reader),
            # Material presets (v1.6)
            "material_preset": _startup_preset,
            "material_preset_options": [
                {"title": "CPK Glossy", "value": "cpk_glossy"},
                {"title": "Matte", "value": "matte"},
                {"title": "Glass", "value": "glass"},
                {"title": "Metallic", "value": "metallic"},
                {"title": "Toon / NPR", "value": "toon"},
                {"title": "Scientific", "value": "scientific"},
            ],
            # Persistent settings dialog state
            "settings_dialog": False,
            "settings_dark_background": _startup_dark,
            "settings_material_preset": _startup_preset,
            "settings_auto_save_interval": _startup_autosave,
            "settings_energy_unit": _startup_energy_unit,
            "settings_measure_unit": _startup_length_unit,
            "settings_measure_unit_options": [
                {"title": "Ångström (Å)", "value": "angstrom"},
                {"title": "Bohr (a₀)", "value": "bohr"},
            ],
            "settings_band_energy_unit": _startup_band_energy_unit,
            "settings_band_energy_unit_options": [
                {"title": "Electronvolt (eV)", "value": "ev"},
                {"title": "Hartree (Eh)", "value": "hartree"},
                {"title": "kcal/mol", "value": "kcal_mol"},
            ],
            "settings_energy_unit_options": [
                {"title": "Hartree (Eh)", "value": "hartree"},
                {"title": "Electronvolt (eV)", "value": "ev"},
                {"title": "kcal/mol", "value": "kcal_mol"},
            ],
            "settings_interval_options": [
                {"title": "30 seconds", "value": 30},
                {"title": "60 seconds", "value": 60},
                {"title": "5 minutes", "value": 300},
                {"title": "15 minutes", "value": 900},
                {"title": "Disabled", "value": 0},
            ],
            # QA validation warnings surfaced in the status bar.
            "qa_warnings": [],
            # Isovalue units label — set when a volume section is activated.
            "isovalue_units": "e/bohr³",
            # LOD: subsample large grids for performance.
            "reduce_detail": True,
            # MO visibility toggle — lets the user hide the isosurface without
            # navigating away from the wavefunction section.
            "mo_visible": False,
            "mo_last_spin": None,
            "mo_last_index": None,
            # Exact recipe for the last successfully rendered computed
            # wavefunction surface. QVF-backed volume sections use the
            # separate active_volume_id lifecycle above.
            "wf_surface_kind": None,
            # Reaction-path waypoint state (mirrors trajectory state).
            "reaction_waypoints": [],
            "reaction_current_label": "",
            # Bookmarks (from manifest) + user bookmarks
            "bookmark_names": [bm.name for bm in viewer_state.bookmarks],
            "selected_bookmark": (
                viewer_state.bookmarks[0].name if viewer_state.bookmarks else None
            ),
            "user_bookmarks": [],  # [{name, camera, section_id, isovalue, colormap}]
            "user_bookmark_names": [],
            # v1.3 Presentation mode
            "presentation_mode": False,
            "presentation_slide": 0,
            "presentation_slides": [],  # list of {name, camera, sections, duration}
            "presentation_slide_duration": 5,  # seconds auto-advance
            "presentation_auto_advance": False,
            # True when the active slide's primary rendering lives in the
            # lower 2D panel rather than the VTK viewport.
            "presentation_panel_slide": False,
            # Session save/restore
            "session_path": "",
            "session_dirty": False,
            # About dialog
            "about_dialog": False,
            "about_version": "",
            "about_system_info": "",
            # Point group symmetry detection
            "point_group": "",
            "point_group_details": "",
            # Download manager
            "download_manager_dialog": False,
            "download_history": [],
            # Screenshot
            "screenshot_data": None,
            "screenshot_ready": False,
            "screenshot_dialog": False,
            "screenshot_scale": 2,
            "screenshot_scale_options": [1, 2, 4],
            "screenshot_transparent": False,
            # Video export
            "has_animatable": False,
            "video_kind": "",  # "trajectory", "vibration", "orbital"
            "video_export_dialog": False,
            "video_export_format": "mp4",
            "video_export_format_options": [
                {"title": "MP4 (H.264)", "value": "mp4"},
                {"title": "GIF (animated)", "value": "gif"},
                {"title": "PNG frames", "value": "frames"},
            ],
            "video_export_fps": 30,
            "video_export_fps_options": [
                {"title": "15 fps", "value": 15},
                {"title": "24 fps (film)", "value": 24},
                {"title": "30 fps", "value": 30},
                {"title": "60 fps", "value": 60},
            ],
            "video_export_kind": "turntable",
            "video_export_kind_options": [
                {"title": "360° Turntable", "value": "turntable"},
                {"title": "Trajectory", "value": "trajectory"},
                {"title": "Vibration", "value": "vibration"},
                {"title": "Orbital sweep", "value": "orbital"},
            ],
            "video_export_running": False,
            # High-quality raytrace render
            "hq_render_dialog": False,
            "hq_render_quality": "high",
            "hq_render_quality_options": [
                {"title": "Draft (fast, 16 spp)", "value": "draft"},
                {"title": "Standard (64 spp)", "value": "standard"},
                {"title": "High (256 spp, denoised)", "value": "high"},
                {"title": "Publication (512 spp, denoised)", "value": "publication"},
            ],
            "hq_render_resolution": "3840x2160",
            "hq_render_resolution_options": [
                {"title": "HD (1920×1080)", "value": "1920x1080"},
                {"title": "4K (3840×2160)", "value": "3840x2160"},
                {"title": "8K (7680×4320)", "value": "7680x4320"},
            ],
            "hq_render_environment": "studio",
            "hq_render_environment_options": [
                {"title": "Dark", "value": "dark"},
                {"title": "Light", "value": "light"},
                {"title": "Studio", "value": "studio"},
                {"title": "Sunset", "value": "sunset"},
                {"title": "Scientific", "value": "scientific"},
            ],
            "hq_render_material": "cpk",
            "hq_render_material_options": [
                {"title": "CPK (semi-gloss)", "value": "cpk"},
                {"title": "Metallic", "value": "metallic"},
                {"title": "Glass", "value": "glass"},
                {"title": "Ceramic", "value": "ceramic"},
            ],
            "hq_render_dof": False,
            "hq_render_save_disk": False,
            "hq_render_saved_path": "",
            "hq_render_progress": 0.0,
            "hq_render_progress_msg": "",
            "hq_render_running": False,
            # Display toggles
            "show_atom_labels": False,
            "dark_background": _startup_dark,
            "background_override": _startup_background_override(
                _startup_dark, MATERIAL_PRESETS[_startup_preset].background_color
            ),
            "raytrace_enabled": False,
            "toon_mode": False,
            # OSPRay ray tracing is only meaningful when (a) the VTK build
            # ships OSPRay and (b) we're rendering server-side. Detected at
            # startup; the app-bar button is hidden when unavailable so it
            # can't throw "object has no attribute 'enable_ray_tracing'".
            "raytrace_available": _ospray_available(),
            # Screen-space ambient occlusion (VTK 9.2+)
            "ssao_enabled": True,
            # Orthographic (parallel) projection: no perspective distortion,
            # for crystallography / publication figures (design refresh 2026).
            "orthographic_projection": False,
            # Volume clip planes (0.0-1.0 fractional position)
            "clip_x": 0.5,
            "clip_y": 0.5,
            "clip_z": 0.5,
            "clip_enabled": False,
            # 2D cross-section slice mode — when enabled, replaces the
            # clipped isosurface with a colour-mapped plane at the clip
            # position sampling the volume data values.
            "show_slice": False,
            # Visual-only feedback for high-frequency clip-slider updates.
            # The focused slider already exposes aria-valuenow, so routing
            # every tick through the global polite status region would make
            # assistive technology announce an avoidable stream of updates.
            "clip_position_message": "",
            # True while an animation (trajectory/vibration/reaction.path)
            # has hidden the static structure actors; gates the rebuild on
            # return to a non-animation section (A5-03).
            "structure_hidden": False,
            # Measurement
            "measure_mode": False,
            "selected_atoms": [],
            "measure_result": "",
            # Calculation Parameters (v1.1: shown when structure is present)
            "show_param_panel": False,
            "calc_method": "rhf",
            "calc_functional": "pbe",
            "calc_basis": "sto-3g",
            "calc_charge": 0,
            "calc_multiplicity": 1,
            "calc_template": "single_point",
            "method_options": [
                {"title": "RHF", "value": "rhf"},
                {"title": "UHF", "value": "uhf"},
                {"title": "RKS (DFT)", "value": "rks"},
                {"title": "UKS (DFT)", "value": "uks"},
                {"title": "RMP2", "value": "rmp2"},
                {"title": "UMP2", "value": "ump2"},
            ],
            "functional_options": [
                {"title": "PBE", "value": "pbe"},
                {"title": "PBE0", "value": "pbe0"},
                {"title": "B3LYP", "value": "b3lyp"},
                {"title": "BLYP", "value": "blyp"},
                {"title": "BP86", "value": "bp86"},
                {"title": "TPSS", "value": "tpss"},
                {"title": "M06-2X", "value": "m06-2x"},
                {"title": "\u03c9B97X-D", "value": "wb97x-d"},
                {"title": "CAM-B3LYP", "value": "cam-b3lyp"},
                {"title": "LDA", "value": "lda"},
                {"title": "r\u00b2SCAN", "value": "r2scan"},
                {"title": "HSE06", "value": "hse06"},
            ],
            "basis_options": [
                {"title": "STO-3G", "value": "sto-3g"},
                {"title": "3-21G", "value": "3-21g"},
                {"title": "6-31G", "value": "6-31g"},
                {"title": "6-31G(d)", "value": "6-31gd"},
                {"title": "6-31G(d,p)", "value": "6-31gdp"},
                {"title": "6-311G(d,p)", "value": "6-311gdp"},
                {"title": "cc-pVDZ", "value": "cc-pvdz"},
                {"title": "cc-pVTZ", "value": "cc-pvtz"},
                {"title": "aug-cc-pVDZ", "value": "aug-cc-pvdz"},
                {"title": "aug-cc-pVTZ", "value": "aug-cc-pvtz"},
                {"title": "def2-SVP", "value": "def2-svp"},
                {"title": "def2-TZVP", "value": "def2-tzvp"},
                {"title": "def2-QZVP", "value": "def2-qzvp"},
            ],
            "calc_template_options": [
                {"title": "Single Point", "value": "single_point"},
                {"title": "Geometry Optimization", "value": "optimization"},
                {"title": "Frequencies", "value": "frequencies"},
                {"title": "Single Point + Freq + Opt", "value": "full"},
                {"title": "Periodic Single Point", "value": "periodic"},
            ],
            "periodic_template_options": [
                {"title": "Periodic Single Point", "value": "periodic"},
            ],
            # v1.2 Editor state
            "edit_mode": False,
            "edit_selected": [],  # atom indices currently selected for editing
            "frozen_atoms": [],  # atom indices held fixed during live-opt (B3)
            "edit_history": [],  # undo stack: previous atoms+lattice transactions
            "edit_future": [],  # redo stack
            "edit_new_element": "C",
            "element_picker_open": False,
            "element_picker_target": "new",  # new | change
            # M3 live geometry optimization (auto-relax while building)
            "live_opt_enabled": False,
            "live_opt_available": None,  # None = not yet probed
            "live_opt_status": "",
            "live_opt_engine": "msindo",
            "live_opt_engine_options": [],  # populated by the worker probe
            # Keyboard shortcuts
            "shortcuts_help_dialog": False,
            # Command palette (Ctrl/Cmd+K): fuzzy-search every quick action
            # (design refresh 2026). Static actions plus per-section "open"
            # entries; filtering is client-side on palette_query.
            "palette_open": False,
            "palette_query": "",
            "palette_actions": _palette_actions_for(reader),
            "shortcut_keys": {
                "Ctrl/Cmd+K": "Command palette",
                "e": "Toggle edit mode",
                "m": "Toggle measure mode",
                "r": "Reset camera",
                "s": "Screenshot",
                "v": "Video export dialog",
                "p": "Presentation mode",
                "Ctrl/Cmd+Z": "Undo (edit mode)",
                "Ctrl/Cmd+Shift+Z or Ctrl/Cmd+Y": "Redo (edit mode)",
                "Delete": "Delete selected atoms",
                "Escape": "Exit presentation, edit or measure mode",
                "ArrowLeft/Right": "Previous/next slide (presentation mode)",
                "?": "Show this help",
            },
            # Right-click context menu
            "context_menu_show": False,
            "context_menu_x": 0,
            "context_menu_y": 0,
            "context_menu_atom_idx": -1,
            # Hover tooltip
            "hover_tooltip": "",
            "hover_tooltip_visible": False,
            "hover_tooltip_x": 0,
            "hover_tooltip_y": 0,
            # Welcome / quickstart panel
            "welcome_visible": True,
            # File watcher for auto-reload on QVF changes
            "file_watcher_enabled": False,
            "file_watcher_interval": 5.0,
            # Streaming-checkpoint provenance of the active file (M4):
            # "" (not a live checkpoint) / "running" / "converged" / "failed",
            # plus a short "#seq · iter N · E ... Eh" summary for the app bar.
            "live_run_status": _checkpoint_summary(reader)[0],
            "live_checkpoint_text": _checkpoint_summary(reader)[1],
            "file_last_modified": "",
            # UI theme toggle. Defaults light to match the long-standing
            # visible default (the old dark-mode class was never applied, so
            # the chrome has always rendered light); the toggle now actually
            # switches the Vuetify theme (design refresh 2026 theme pass).
            "ui_dark_mode": False,
            # v1.3 Fragment Library state
            "build_fragment": None,
            "fragment_options": [
                {"title": "CH3  (methyl)", "value": "CH3"},
                {"title": "NH2  (amino)", "value": "NH2"},
                {"title": "OH   (hydroxyl)", "value": "OH"},
                {"title": "COOH (carboxyl)", "value": "COOH"},
                {"title": "Ph   (phenyl)", "value": "Ph"},
                {"title": "CHO  (aldehyde)", "value": "CHO"},
                {"title": "NO2  (nitro)", "value": "NO2"},
                {"title": "CN   (cyano)", "value": "CN"},
                {"title": "CF3  (trifluoromethyl)", "value": "CF3"},
                {"title": "SO3H (sulfonic acid)", "value": "SO3H"},
            ],
            # v1.3 Supercell dialog state
            "build_supercell_dialog": False,
            "build_supercell_nx": 1,
            "build_supercell_ny": 1,
            "build_supercell_nz": 1,
            # Export
            "export_data": None,
            "export_filename": "vibe-view-structure.obj",
            # Per-element colour overrides (design refresh 2026). The picker
            # lists only the elements actually present; overrides are keyed by
            # atomic number and applied inside cpk_color().
            "element_color_options": _present_elements(reader),
            "element_colors": {},
            "element_color_z": None,
            "element_color_value": _palette.ELEMENT_OVERRIDE_DEFAULT,
            # Multi-file support
            "header_title": _header_title(reader),
            "run_info": _run_info_text(reader),
            "file_names": [_job_name(r) for r in _all_readers],
            # NOTE: must NOT start with an underscore — this key is bound in
            # the Vue template (the Files dropdown, v_model="active_file_idx"),
            # and Vue 3 refuses to expose `_`/`$`-prefixed identifiers to
            # template expressions, which threw ReferenceError on every render
            # and wiped out the whole app bar in multi-file mode (UI-01).
            "active_file_idx": 0,
            # File-open dialog (server-side path / directory open)
            "load_file_dialog": False,
            "load_file_bytes": "",
            "load_file_name": "",
            "open_path": "",
            "open_recursive": False,
            # Molecule builder (toolbar): name -> 3D structure, no file needed
            "builder_dialog": False,
            "builder_name": "",
            "builder_smiles": "",
            # RDKit is the optional [smiles] extra; the field is shown
            # either way so the install hint is discoverable.
            "smiles_available": _smiles_available(),
            "builder_available": _naming_available(),
            # vq Job Manager (M1/A1)
            "vq_panel_open": False,  # right-side Job Manager drawer
            "vq_jobs_list": [],
            "vq_jobs_loading": False,
            "vq_jobs_error": "",
            # vq feature/host overview (M1/A5)
            "vq_ov_host": "",  # e.g. "localhost · vq 0.15.x"
            "vq_ov_daemon": False,  # daemon healthy?
            "vq_ov_capacity": "",  # e.g. "max 12 CPU · 8 jobs"
            "vq_ov_load": "",  # e.g. "running 5 · pending 3 CPU"
            "vq_fetch_output_dir": "vq-fetched",
            # vq live monitor (M1/A3) — per-job status + log tail
            "vq_detail_job_id": "",  # job currently expanded in the panel
            "vq_detail_name": "",
            "vq_detail_state": "",
            "vq_detail_log": "",  # stdout/stderr tail text
            "vq_detail_loading": False,
            # v1.4 vq integration — submit + monitor
            "vq_submit_dialog": False,
            # Generated inputs stream a live checkpoint QVF by default so a
            # submitted job is immediately watchable from the Job Manager.
            "vq_submit_live_checkpoint": True,
            # Submit the job as a pending QVF *container* (structure +
            # job.spec, run_status="pending") instead of a generated .py
            # script, so the queue moves one file each way and the artifact
            # that comes back is the same file, settled. Needs vibeqc (the
            # QVF producer) importable next to vibeview; falls back to the
            # script path with a status message when it is not.
            "vq_submit_container": True,
            "vq_submit_container_available": _vibeqc_importable(),
            "vq_submit_output_dir": "",
            "vq_submitting": False,
            "vq_monitor_active": False,
            "vq_monitor_jobs": [],  # list of {id, label, state, progress}
            "vq_auto_open": True,
            # State slots for @ctrl.set one-shot actions (camera views,
            # geometry export).  Trame's Vue3 client requires these to be
            # initialised — without them, the JS setter crashes with
            # ``u.map is not a function``.  The values are never read.
            "camera_preset": None,
            "export_geometry": None,
            # File-picker v-model — must be initialised for @ctrl.set.
            # Lazy section loading for faster startup
            "memory_usage": "",
        }
    )

    # Overlay availability for the initial file (the reload path recomputes
    # this on every file switch).
    from vibeview.renderers.wannier import find_wannier_section as _find_wan

    server.state.has_wannier_centers = _find_wan(reader) is not None

    # ── Load structure eagerly (errors reported via status bar) ───
    structure_renderer, is_periodic = _build_structure_scene(
        reader,
        plotter,
        server.state,
        replication=_triple(replication),
        show_labels=bool(server.state.show_atom_labels),
        representation=str(getattr(server.state, "representation_style", "ball_and_stick")),
        cartoon_color_mode=str(getattr(server.state, "cartoon_color_mode", "chain")),
        residue_selection=str(getattr(server.state, "residue_selection", "") or ""),
    )

    # QA validation: check SCF convergence, energy sanity, etc.
    qa_warnings = _qa_validate(reader)
    if qa_warnings:
        server.state.qa_warnings = qa_warnings
        if not server.state.status_message:
            server.state.status_message = qa_warnings[0]

    view_isometric(plotter)
    show_grid(plotter)

    # ── Apply camera bookmark from viewer_defaults ────────────────
    _apply_camera(plotter, viewer_state.camera)
    _sync_orthographic_switch(plotter, server.state)

    # ── Cross-fade between two volumes (if configured) ───────────
    if viewer_state.crossfade_volumes:
        server.state.crossfade_active = True
        server.state.crossfade_blend = viewer_state.crossfade_blend
        va, vb = viewer_state.crossfade_volumes
        _rebuild_crossfade(
            reader,
            plotter,
            viewer_state,
            server.state,
            va,
            vb,
            viewer_state.crossfade_blend,
        )

    # Actor defaults are renderer implementation details; make the declared
    # application appearance authoritative on the initial scene too.
    _apply_scene_appearance(plotter, server.state)

    # ── Auto-open viewer_defaults sections ───────────────────────
    # Deferred to the end of create_app: auto-open goes through the same
    # guarded activate_section a sidebar click uses (#37-4). The hand-rolled
    # per-kind copy that stood here left a volume unrendered
    # (volume_loaded=False, no actors), omitted selected_section for qtaim,
    # and knew nothing of dos.*, fermi_surface, phonon_*, equation_of_state,
    # vibrations or atom_properties.

    # ── Reactive handlers ─────────────────────────────────────────────
    ctrl = server.controller

    # ── Multi-file support ───────────────────────────────────────────

    # Keep computed render work serialized, while a separate short-held epoch
    # lock lets section/file switches invalidate an in-flight render without
    # waiting for its grid evaluation to finish.
    _wf_render_lock = threading.RLock()
    _wf_epoch_lock = threading.RLock()
    _wf_render_epoch = 0
    _wf_anim_timer: asyncio.TimerHandle | None = None
    _scene_hooks(plotter).wf_render_lock = _wf_render_lock
    _scene_hooks(plotter).state = server.state
    _relocalize_task = None
    _relocalize_probe_task = None
    _relocalize_generation = 0

    def _wavefunction_render_is_current(
        epoch: int,
        section_id: str,
        source_reader: QVFReader | None,
    ) -> bool:
        with _wf_epoch_lock:
            return (
                epoch == _wf_render_epoch
                and reader is source_reader
                and server.state.wf_section_id == section_id
            )

    def _invalidate_wavefunction_renders() -> int:
        """Cancel animation and make every already-running MO render stale."""
        nonlocal _wf_render_epoch, _wf_anim_timer
        with _wf_epoch_lock:
            _wf_render_epoch += 1
            timer = _wf_anim_timer
            _wf_anim_timer = None
            server.state.wf_animating = False
            epoch = _wf_render_epoch
        if timer is not None:
            timer.cancel()
        return epoch

    def _cancel_live_opt_for_reader_change() -> None:
        """Stop geometry callbacks that belong to a replaced reader."""
        _cancel_live_opt_intent(clear_status=True)

    def _clear_reader_scoped_edit_state() -> None:
        """Discard atom-indexed edit state before binding another structure."""
        state = server.state
        state.edit_history.clear()
        state.edit_future.clear()
        # Both stacks are mutated in place. Without dirty(), Trame's client
        # snapshot aliases these lists and leaves Undo/Redo enabled for the
        # previous reader.
        state.dirty("edit_history", "edit_future")
        state.edit_selected = []
        state.dirty("edit_selected")
        _remove_actors_by_prefix(plotter, _Actors.EDIT_HIGHLIGHT)
        _reset_frozen(plotter, state)

        # These interaction targets all contain atom indices or world-space
        # coordinates from the outgoing structure.
        state.element_picker_open = False
        state.element_picker_target = "new"
        state.context_menu_show = False
        state.context_menu_atom_idx = -1
        state.hover_tooltip = ""
        state.hover_tooltip_visible = False
        _ctx_menu_target.update(world=None, atom_idx=-1)
        _hover_state.update(world=None, atom_idx=-1, at=float("-inf"))

    def _update_live_status(r: QVFReader) -> None:
        """Reflect a streaming checkpoint's provenance in the app bar."""
        status, text = _checkpoint_summary(r)
        server.state.live_run_status = status
        server.state.live_checkpoint_text = text

    def _reload_active_file(
        preserve_camera: bool = False, same_file: bool = False
    ) -> None:
        """Invalidate worker renders, then replace the VTK scene exclusively."""
        _cancel_relocalization()
        _invalidate_wavefunction_renders()
        with _wf_render_lock:
            return _reload_active_file_unlocked(preserve_camera, same_file)

    def _reload_active_file_unlocked(
        preserve_camera: bool = False, same_file: bool = False
    ) -> None:
        """Rebuild the entire UI for the currently-active reader.

        Called once during initialisation (index 0) and again whenever
        the user switches files via the AppBar dropdown.  Uses
        ``nonlocal`` to rebind every closure-captured name so all
        handlers and helpers see the new reader and its data.

        ``preserve_camera=True`` keeps the user's current viewpoint
        instead of applying the file's camera bookmark — the file
        watcher's hot-reload path, where yanking the camera on every
        checkpoint would make live monitoring unusable.

        ``same_file=True`` marks that hot reload: the incoming reader is a
        fresh object for the SAME archive, so state scoped to the file
        (the residue selection) survives even though the reader changed.
        """
        nonlocal reader, viewer_state, is_periodic
        nonlocal section_list, sidebar_entries, orbital_sections, orbital_options
        nonlocal basis_ao_sections, ao_options
        nonlocal structure_renderer

        incoming_reader = _all_readers[server.state.active_file_idx]
        reader_changed = incoming_reader is not reader
        if incoming_reader is not reader:
            _cancel_live_opt_for_reader_change()
            _clear_reader_scoped_edit_state()
            # A residue selection names one file's chains and residues;
            # re-applying it to another structure highlighted arbitrary
            # residues. Maintainer decision (#37-19): a switch to another
            # file clears it; a hot reload of the same file keeps it. The
            # watcher always hands over a fresh reader object, so reader
            # identity cannot tell the two apart; its same_file flag does.
            if not same_file:
                server.state.residue_selection = ""

        # Probe the incoming file (a structure-less file must not inherit
        # the previous file's periodic flag), so the orbital group title
        # below is right before _build_structure_scene rebinds it (#37-6).
        is_periodic = _probe_is_periodic(incoming_reader)

        reader = incoming_reader
        viewer_state = ViewerState.from_manifest(
            reader.viewer_defaults, {s.id: s.kind for s in reader.sections}
        )
        _clamp_viewer_replication(reader, viewer_state)
        server.state.residue_visibility = "all"
        server.state.residue_pick_mode = False
        server.state.residue_selection_chains = _selectable_chains(reader)
        server.state.residue_selection_available = _residue_selection_available(reader)
        server.state.residue_selection_summary = _residue_selection_summary(
            reader, str(server.state.residue_selection or "")
        )
        _set_replication(viewer_state.replication)
        replication = list(viewer_state.replication)

        # Rebuild section list, in place: the list is closure-held.
        section_list[:] = _build_section_list(reader)
        server.state.section_list = section_list
        # section_list is a closure-held list mutated in place; trame's
        # setattr drops the push when the assigned object IS the pushed one
        # (`value == pushed_state[key]` — identity ⇒ equal), so the client
        # would never see the rebuilt list without an explicit dirty().
        server.state.dirty("section_list")

        # Rebuild sidebar entries with the same builder the first load uses
        # (#37-25); the copy that stood here once dropped the basis.ao group.
        orbital_sections = [s for s in section_list if s["kind"] == "volume.orbital"]
        basis_ao_sections = [s for s in section_list if s["kind"] == "basis.ao"]
        sidebar_entries[:] = _build_sidebar_entries(reader, section_list, is_periodic)
        server.state.sidebar_entries = sidebar_entries
        server.state.dirty("sidebar_entries")  # same in-place-mutation issue
        # The palette's "Open section" entries belong to the loaded file.
        server.state.palette_actions = _palette_actions_for(reader)

        orbital_options = [{"title": s["id"], "value": s["id"]} for s in orbital_sections]
        server.state.orbital_options = orbital_options
        server.state.selected_orbital = orbital_options[0]["value"] if orbital_options else None
        ao_options = [{"title": s["id"], "value": s["id"]} for s in basis_ao_sections]
        server.state.ao_options = ao_options
        server.state.selected_ao = ao_options[0]["value"] if ao_options else None

        # Update header title (derived state, not stored).
        server.state.header_title = _header_title(reader)
        server.state.run_info = _run_info_text(reader)
        _update_live_status(reader)
        from vibeview.renderers.wannier import find_wannier_section

        server.state.has_wannier_centers = find_wannier_section(reader) is not None
        server.state.file_names = [_job_name(r) for r in _all_readers]
        server.state.compare_highlight_options = (
            [
                {"title": f"{n} (file {i + 1})", "value": i}
                for i, n in enumerate(server.state.file_names)
            ]
            if len(_all_readers) > 1
            else []
        )

        # Clear all active panel state.
        _clear_output_panels(server.state)
        server.state.selected_section = None
        server.state.status_message = ""
        server.state.is_periodic = False
        # File-switch-only resets (not in _clear_output_panels, which also
        # runs on every section click): a stale atom selection from the
        # previous file would otherwise index the new (possibly smaller)
        # structure and crash _measure_text (A1-02). The scene is rebuilt
        # below, so the static structure is shown (A5-03).
        server.state.measure_mode = False
        server.state.selected_atoms = []
        server.state.measure_result = ""
        server.state.structure_hidden = False
        _reset_frozen(plotter, server.state)  # frozen indices belong to the old file

        # Rebuild the 3D scene.
        saved_camera = plotter.camera_position if preserve_camera else None
        _reset_scene(plotter, server.state)

        structure_renderer, is_periodic = _build_structure_scene(
            reader,
            plotter,
            server.state,
            replication=_triple(replication),
            show_labels=bool(server.state.show_atom_labels),
            representation=str(getattr(server.state, "representation_style", "ball_and_stick")),
            cartoon_color_mode=str(
                getattr(server.state, "cartoon_color_mode", "chain")
            ),
            residue_selection=str(
                getattr(server.state, "residue_selection", "") or ""
            ),
        )

        # QA validation: check SCF convergence, energy sanity, etc. Always
        # assigned: the previous file's warnings must not outlive it (#37-5).
        qa_warnings = _qa_validate(reader)
        server.state.qa_warnings = list(qa_warnings or [])
        if qa_warnings and not server.state.status_message:
            server.state.status_message = qa_warnings[0]

        show_grid(plotter)
        _apply_scene_appearance(plotter, server.state)
        if saved_camera is not None:
            plotter.camera_position = saved_camera
        else:
            view_isometric(plotter)
            # Apply the camera BEFORE pushing so the new file's bookmark
            # reaches the client (previously the push happened first, so the
            # camera was set server-side but never sent — A1-04).
            _apply_camera(plotter, viewer_state.camera)
            _sync_orthographic_switch(plotter, server.state)
        _push_view(plotter)
        if saved_camera is None:
            # _push_view sends geometry; in VtkLocalView the client owns the
            # camera and keeps its own until one is pushed explicitly. Without
            # this the server framed the new structure correctly and the
            # browser went on showing the previous file's zoom -- building
            # phenol while water was open left the ring far outside the
            # viewport. Skipped when preserving the camera, which is the
            # hot-reload path that deliberately keeps the user's viewpoint.
            _push_camera(plotter)

        # Re-bootstrap the cross-fade card for the new file: it must turn ON
        # for a file that declares a crossfade and OFF for one that doesn't,
        # rather than inheriting the previous file's state (A1-03).
        if viewer_state.crossfade_volumes:
            server.state.crossfade_active = True
            server.state.crossfade_blend = viewer_state.crossfade_blend
            va, vb = viewer_state.crossfade_volumes
            _rebuild_crossfade(
                reader,
                plotter,
                viewer_state,
                server.state,
                va,
                vb,
                viewer_state.crossfade_blend,
            )
            # Cross-fade actors are created after the first scene push.
            _apply_scene_appearance(plotter, server.state)
            plotter.render()
            _push_view(plotter)
        else:
            server.state.crossfade_active = False

        if reader_changed:
            _sync_file_watcher_for_active_reader()

    @ctrl.set("build_molecule")
    def build_molecule() -> None:
        """Build a structure from a molecule name typed into the toolbar.

        The generated geometry comes from ``vibeqc_naming``'s curated
        structure database, not from a conformer search — it is a starting
        point for a calculation, not a converged geometry.
        """
        name = (server.state.builder_name or "").strip()
        if not name:
            return
        try:
            from vibeqc_naming import known_names, structure_from_name
        except ImportError:
            server.state.status_message = (
                "Molecule builder needs the vibeqc_naming package, which is not installed"
            )
            return

        try:
            atoms = structure_from_name(name)
        except Exception as e:  # noqa: BLE001 — a bad name must not kill the session
            server.state.status_message = f"Could not build {name!r}: {e}"
            return
        if not atoms:
            server.state.status_message = (
                f"Unknown molecule {name!r} — {len(known_names())} names are available"
            )
            return

        from vibeview.converters import atoms_to_qvf
        from vibeview.qvf import QVFError, QVFReader

        try:
            new_reader = QVFReader(atoms_to_qvf(atoms, label=name))
        except (QVFError, ValueError) as e:
            server.state.status_message = f"Could not build {name!r}: {e}"
            return

        _adopt_reader(new_reader)
        server.state.builder_name = ""
        server.state.builder_dialog = False
        server.state.status_message = f"Built {name} — {len(atoms)} atoms (idealized geometry)"

    @ctrl.set("build_from_smiles")
    def build_from_smiles() -> None:
        """Build a 3D structure from a SMILES string typed into the dialog.

        RDKit embeds the initial geometry (ETKDG); the relax that makes it a
        real structure is vibe-qc's, per roadmap decision 2. Auto-optimize is
        switched on for the new file rather than run once here, so the user
        watches it converge in the viewport and can undo it — the same path
        every other editor mutation takes.
        """
        smiles = (server.state.builder_smiles or "").strip()
        if not smiles:
            return

        from vibeview.converters import smiles_to_qvf
        from vibeview.qvf import QVFError, QVFReader

        try:
            new_reader = QVFReader(smiles_to_qvf(smiles))
        except (QVFError, ValueError) as e:
            # smiles_to_qvf raises ValueError for a missing extra, an
            # unparseable string, and a failed embed alike, and each message
            # already names the cause — including the pip command.
            server.state.status_message = str(e)
            return

        _adopt_reader(new_reader)
        n_atoms = len(new_reader.read_structure().atoms)
        server.state.builder_smiles = ""
        server.state.builder_dialog = False
        server.state.live_opt_enabled = True
        _live_opt_schedule()
        server.state.status_message = (
            f"Built {smiles} — {n_atoms} atoms (ETKDG start; relaxing with vibe-qc)"
        )

    def _activate_file(idx: int) -> None:
        """Make ``_all_readers[idx]`` the active file and show it.

        The one copy of what file switching, uploads, builds, path opens,
        vq fetches and live checkpoints each did (#37-28). All of them now
        re-pick the representation for the incoming file (a molecule opens
        ball-and-stick, a biomolecule as a ribbon), which only the Files
        dropdown used to do. The watcher's hot reload does not come through
        here, and keeps whatever the user selected.
        """
        state = server.state
        state.active_file_idx = idx
        state.file_names = [_job_name(r) for r in _all_readers]
        state.representation_style = _default_representation(_all_readers[idx])
        state.structure_color_mode = (
            state.cartoon_color_mode if state.representation_style == "cartoon"
            else state.atom_color_mode
        )
        _reload_active_file()

    def _adopt_reader(new_reader: QVFReader) -> None:
        """Add ``new_reader`` to the open files and show it."""
        _all_readers.append(new_reader)
        _activate_file(len(_all_readers) - 1)

    @ctrl.set("switch_file")
    def switch_file(file_idx: Any) -> None:
        """Switch the active file when the user picks from the Files dropdown."""
        # Robust to either an integer index or a filename string.
        try:
            idx = int(file_idx)
        except (TypeError, ValueError):
            names = [_job_name(r) for r in _all_readers]
            idx = names.index(file_idx) if file_idx in names else -1
        if idx < 0 or idx >= len(_all_readers):
            return
        _activate_file(idx)
        server.state.status_message = (
            f"Switched to {_job_name(reader)} (file {idx + 1}/{len(_all_readers)})"
        )

    @ctrl.set("toggle_compare_mode")
    def toggle_compare_mode(on: bool | None = None) -> None:
        """Phase D1: overlay all loaded structures, colour-coded by file.

        Called from the switch (``[$event]`` → the new bool) or with no arg to
        flip. Entering clears the single-file scene and draws the overlay;
        leaving restores the active file's structure.
        """
        state = server.state
        enabled = (not state.compare_mode) if on is None else bool(on)
        state.compare_mode = enabled
        if enabled:
            _remove_static_structure(plotter)
            for prefix in (
                _Actors.VOLUME,
                _Actors.VIB_ATOM,
                _Actors.TRAJ_ATOM,
                _Actors.MO_SURFACE,
                _Actors.FERMI,
            ):
                _remove_actors_by_prefix(plotter, prefix)
            state.structure_hidden = True
            state.compare_legend = _render_compare_overlay(
                plotter,
                _all_readers,
                align=state.compare_align,
                highlight=state.compare_highlight,
            )
            view_isometric(plotter)
            reset_camera(plotter)
            state.status_message = f"Compare mode: {len(state.compare_legend)} structures overlaid"
        else:
            _remove_actors_by_prefix(plotter, _Actors.COMPARE)
            state.compare_legend = []
            # Keep the hidden flag set until the restore helper consumes it.
            # Clearing it first makes the helper return immediately, leaving
            # compare mode's static-scene removal permanent.
            _ensure_static_structure_shown()
            state.status_message = "Compare mode off"
        plotter.render()
        _push_view(plotter)

    @ctrl.set("toggle_compare_align")
    def toggle_compare_align(on: bool | None = None) -> None:
        """Phase D4: toggle Kabsch RMSD-fit alignment of the overlaid structures
        onto the first file; re-renders the overlay when compare mode is active.
        """
        state = server.state
        state.compare_align = (not state.compare_align) if on is None else bool(on)
        if state.compare_mode:
            state.compare_legend = _render_compare_overlay(
                plotter,
                _all_readers,
                align=state.compare_align,
                highlight=state.compare_highlight,
            )
            plotter.render()
            _push_view(plotter)

    @ctrl.set("set_compare_highlight")
    def set_compare_highlight(idx: Any) -> None:
        """Highlight a specific file in compare mode, dimming the rest."""
        idx = _first(idx)
        state = server.state
        state.compare_highlight = idx if idx is not None and idx != "" else None
        if state.compare_mode:
            state.compare_legend = _render_compare_overlay(
                plotter,
                _all_readers,
                align=state.compare_align,
                highlight=state.compare_highlight,
            )
            plotter.render()
            _push_view(plotter)

    @ctrl.set("show_density_diff")
    def show_density_diff() -> None:
        """Phase D2: render the density difference between the two selected files."""
        state = server.state
        opts = state.density_file_options or []
        a = state.diff_a if state.diff_a is not None else (opts[0]["value"] if opts else None)
        b = (
            state.diff_b
            if state.diff_b is not None
            else (opts[1]["value"] if len(opts) > 1 else None)
        )
        if a is None or b is None or a == b:
            state.status_message = (
                "Density difference: pick two distinct files with a density section"
            )
            return
        _activate_density_diff(plotter, viewer_state, state, _all_readers, a, b)

    @ctrl.trigger("load_file_from_bytes")
    def load_file_from_bytes() -> None:
        """Load a file uploaded from the browser's file picker.

        Auto-detects supported structure, cube and TREXIO HDF5 files and converts them
        to QVF in-memory before opening.
        """
        import base64
        import io

        server.state.load_file_dialog = False
        b64_str = server.state.load_file_bytes or ""
        fname = server.state.load_file_name or "uploaded.qvf"
        server.state.load_file_bytes = ""
        server.state.load_file_name = ""
        if not b64_str:
            return

        try:
            raw = base64.b64decode(b64_str)
        except Exception as e:
            server.state.status_message = f"Error decoding file: {e}"
            return

        from vibeview.qvf import QVFError, QVFReader

        try:
            # One converter table for uploads and paths (#37-29).
            from vibeview.converters import convert_bytes_to_qvf, detect_format

            if detect_format(fname) in (None, "qvf"):
                new_reader = QVFReader(io.BytesIO(raw))
            else:
                new_reader = QVFReader(convert_bytes_to_qvf(raw, fname))
        except (QVFError, ValueError) as e:
            server.state.status_message = f"Error opening {fname}: {e}"
            return

        # Name the upload after its file; it has no path, and showed as
        # "<in-memory>" in the Files list.
        _READER_NAMES[new_reader] = Path(fname).stem
        _adopt_reader(new_reader)
        server.state.status_message = (
            f"Loaded {fname} (file {len(_all_readers)}/{len(_all_readers)})"
        )

    @ctrl.set("close_load_dialog")
    def close_load_dialog() -> None:
        server.state.load_file_dialog = False

    @ctrl.set("open_path")
    def open_path() -> None:
        """Open a supported file, a glob, or supported files in a directory.

        Server-side filesystem access (vibe-view is a local/LAN server and
        the files live where the jobs ran). Directory input scans for
        recursively when the box is ticked, which is how you sweep a whole
        batch of jobs. Candidates are checked through the shared importer
        registry so installed third-party plugins work here too. New readers
        are appended to the existing multi-file list, so the Files dropdown
        switches between them.
        """
        import os

        from vibeview.qvf import QVFReader

        raw = (server.state.open_path or "").strip()
        if not raw:
            server.state.status_message = "Enter a .qvf or .xyz file or a directory path"
            return
        target = Path(os.path.expanduser(raw))
        recursive = bool(server.state.open_recursive)
        scan_result: _GuiDirectoryScanResult | None = None

        from vibeview.trexio_import import is_trexio_directory

        if is_trexio_directory(target):
            candidates = [str(target)]
        elif target.is_dir():
            server.state.status_message = f"Scanning {target}..."
            scan_result = _scan_gui_directory(target, recursive=recursive)
            candidates = [str(path) for path in scan_result.candidates]
        elif any(ch in raw for ch in "*?["):
            try:
                scan_result = _scan_gui_glob(
                    os.path.expanduser(raw), recursive=recursive
                )
            except ValueError as exc:
                server.state.status_message = str(exc)
                return
            candidates = [str(path) for path in scan_result.candidates]
        elif target.is_file():
            candidates = [str(target)]
        else:
            server.state.status_message = f"Path not found: {raw}"
            return

        # Directory scanning already applies the shared importer predicate as
        # it walks, which lets the scan stop once it has enough supported
        # files. File and glob input still need the same filter here.
        if scan_result is None:
            from vibeview.converters import is_supported_path

            candidates = [c for c in candidates if is_supported_path(c)]
        scan_notice = (
            _gui_directory_scan_notice(scan_result)
            if scan_result is not None
            else ""
        )
        if not candidates:
            message = f"No supported structure files found under {raw}"
            if scan_notice:
                message += f" ({scan_notice})"
            server.state.status_message = message
            return

        # Skip files already loaded (by resolved path).
        loaded = {str(r.path.resolve()) for r in _all_readers if r.path is not None}
        added = 0
        open_errors = 0
        for path in candidates:
            if str(Path(path).resolve()) in loaded:
                continue
            try:
                from vibeview.converters import convert_to_qvf, detect_format

                fmt = detect_format(path)
                if fmt is not None and fmt != "qvf":
                    qvf_buf = convert_to_qvf(path)
                    _all_readers.append(QVFReader(qvf_buf))
                else:
                    _all_readers.append(QVFReader(path))
                added += 1
            except Exception:  # noqa: BLE001 — skip unreadable files
                open_errors += 1

        if added == 0:
            message = (
                f"No new files added ({len(candidates)} found, "
                f"{open_errors} unreadable, rest already open)"
            )
            if scan_notice:
                message += f" ({scan_notice})"
            server.state.status_message = message
            return
        _activate_file(len(_all_readers) - 1)
        server.state.load_file_dialog = False
        server.state.open_path = ""
        msg = f"Opened {added} file(s) — {len(_all_readers)} total"
        details: list[str] = []
        if open_errors:
            details.append(f"{open_errors} unreadable file(s) skipped")
        if scan_notice:
            details.append(scan_notice)
        if details:
            msg += f" ({'; '.join(details)})"
        server.state.status_message = msg

    # ── vq Job Manager (M1/A1) ────────────────────────────────────────
    # Terminal states from vq.spec.JobState — a job in any of these is done
    # and (if completed) has fetchable results.
    _VQ_TERMINAL = {"completed", "failed", "killed", "interrupted"}
    _VQ_STATE_COLOR = {
        "pending": "grey",
        "running": "info",
        "suspended": "warning",
        "completed": "success",
        "failed": "error",
        "killed": "error",
        "interrupted": "error",
    }

    def _vq_elapsed(spec: Any) -> str:
        """Human elapsed time for a job: running → since start; terminal →
        start→finish; else blank. Robust to missing/one-off timestamps."""
        from datetime import datetime

        def _parse(ts: Any) -> datetime | None:
            if not ts:
                return None
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                return None

        start = _parse(getattr(spec, "started_at", None))
        if start is None:
            return ""
        end = _parse(getattr(spec, "finished_at", None))
        if end is None:
            if spec.state.value != "running":
                return ""
            end = datetime.now(UTC)
        secs = max(0, int((end - start).total_seconds()))
        if secs < 60:
            return f"{secs}s"
        if secs < 3600:
            return f"{secs // 60}m {secs % 60}s"
        return f"{secs // 3600}h {(secs % 3600) // 60}m"

    def _vq_job_dict(spec: Any) -> dict[str, Any]:
        """Rich per-job row for the Job Manager UI."""
        st = spec.state.value
        name = spec.job_name or spec.id[:12]
        return {
            "id": spec.id,
            "name": name,
            "label": f"{name}  ({spec.id[:12]})",
            "state": st,
            "color": _VQ_STATE_COLOR.get(st, "grey"),
            "elapsed": _vq_elapsed(spec),
            "tags": list(getattr(spec, "tags", []) or []),
            "terminal": st in _VQ_TERMINAL,
            "openable": st == "completed",
            # A running job may expose a live checkpoint QVF to watch (M4);
            # vq_watch_live checks the daemon's status JSON on click.
            "watchable": st == "running",
            "submitted_at": getattr(spec, "submitted_at", "") or "",
        }

    def _vq_collect_jobs(all_states: bool) -> tuple[list[dict[str, Any]], str]:
        """Return (items, error). ``items`` is a list of job dicts newest-first;
        ``error`` is a user-facing string or None. Never raises."""
        try:
            from vq.listing import list_jobs as _list_jobs

            # "localhost" is a recognized loopback alias; the shipped code
            # passed "local", which vq.host.is_local_host rejects →
            # NotImplementedError, so job listing never actually worked.
            specs = _list_jobs("localhost")
        except ImportError:
            from vibeview.install_hints import queue_missing_message

            return [], queue_missing_message("list jobs")
        except Exception as e:  # noqa: BLE001 — surface any queue error to the UI
            return [], f"Error listing jobs: {e}"

        if not all_states:
            specs = [s for s in specs if s.state.value == "completed"]
        # Newest activity first: finished, else started, else submitted.
        specs.sort(
            key=lambda s: (
                getattr(s, "finished_at", None)
                or getattr(s, "started_at", None)
                or getattr(s, "submitted_at", "")
                or ""
            ),
            reverse=True,
        )
        return [_vq_job_dict(s) for s in specs[:100]], ""

    @ctrl.set("list_vq_jobs")
    def list_vq_jobs() -> None:
        """Open the Job Manager panel and refresh it."""
        server.state.vq_panel_open = True
        vq_refresh_jobs()

    @ctrl.set("open_vq_job")
    def open_vq_job(job_id: str) -> None:
        """Fetch a vq job and open its .qvf files in the viewer."""
        from pathlib import Path

        if not job_id:
            return
        try:
            from vq.fetch import fetch_local
        except ImportError:
            from vibeview.install_hints import queue_missing_message

            server.state.status_message = queue_missing_message("fetch jobs")
            return

        output_dir = Path(server.state.vq_fetch_output_dir or "vq-fetched")
        if not output_dir.is_absolute():
            output_dir = Path.cwd() / output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            dst = fetch_local(job_id, output_dir)
        except FileNotFoundError:
            server.state.status_message = f"Job {job_id[:12]} not found in local queue"
            return
        except FileExistsError:
            server.state.status_message = (
                f"Job {job_id[:12]} already fetched — remove it first or change output dir"
            )
            return
        except Exception as e:
            server.state.status_message = f"Error fetching {job_id[:12]}: {e}"
            return

        # Find .qvf files and open them.
        from vibeview.qvf import QVFError, QVFReader

        qvf_files = sorted(dst.rglob("*.qvf"))
        if not qvf_files:
            server.state.status_message = (
                f"Fetched {dst.name} but no .qvf files found. Did the job have output_qvf=True?"
            )
            return

        for qvf_path in qvf_files:
            try:
                new_reader = QVFReader(qvf_path)
            except QVFError as e:
                server.state.status_message = f"Error opening {qvf_path.name}: {e}"
                continue
            _all_readers.append(new_reader)

        if not _all_readers:
            return
        _activate_file(len(_all_readers) - 1)
        server.state.status_message = f"Fetched {dst.name} — {len(qvf_files)} .qvf file(s) loaded"

    @ctrl.set("vq_watch_live")
    def vq_watch_live(job_id: str) -> None:
        """Open a *running* job's live checkpoint QVF and auto-reload it as
        the job rewrites it (M4 live streaming). The producer must have been
        started with ``checkpoint_qvf=…``; the daemon advertises the path in
        its status JSON (``checkpoint_qvf_path`` / ``_exists``)."""
        import json as _json
        from pathlib import Path

        if not job_id:
            return
        state = server.state
        try:
            from vq.status import show_status_json
        except ImportError:
            from vibeview.install_hints import queue_missing_message

            state.status_message = queue_missing_message("open a live checkpoint")
            return
        try:
            info = _json.loads(show_status_json("localhost", job_id, tail=0))
        except Exception as e:  # noqa: BLE001 — surface any queue error
            state.status_message = f"Error querying {job_id[:12]}: {e}"
            return
        ckpt = info.get("checkpoint_qvf_path")
        if not ckpt or not Path(ckpt).exists():
            state.status_message = (
                f"Job {job_id[:12]} has written no live checkpoint yet — "
                "submit with checkpoint_qvf=… (or wait for its first snapshot)"
            )
            return

        from vibeview.qvf import QVFError, QVFReader

        # Already open? Switch to it instead of opening a duplicate.
        for i, r in enumerate(_all_readers):
            if r.path is not None and str(r.path) == str(ckpt):
                state.active_file_idx = i
                _reload_active_file()
                break
        else:
            try:
                new_reader = QVFReader(ckpt)
            except QVFError as e:
                state.status_message = f"Error opening checkpoint: {e}"
                return
            _adopt_reader(new_reader)
        toggle_file_watcher(True)
        if state.file_watcher_enabled:
            state.status_message = (
                f"Watching {info.get('job_name') or job_id[:12]} live"
            )

    @ctrl.set("vq_submit_job")
    def vq_submit_job() -> None:
        """Submit the current structure as a vq job."""
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        state = server.state
        state.vq_submitting = True
        state.status_message = "Submitting to vq..."
        input_path = ""

        try:
            # Read structure data from the active reader
            sdata = reader.read_structure()

            from vibeview.input_generator import (
                _structure_calculation_params,
                generate_input_script,
            )

            params = {
                **_structure_calculation_params(sdata),
                "basis": state.calc_basis or "sto-3g",
                "method": state.calc_method or "rhf",
                "functional": state.calc_functional or "",
                "charge": int(state.calc_charge or 0),
                "multiplicity": int(state.calc_multiplicity or 1),
                "title": "vibe-view submission",
                # Live checkpoint QVF → the Job Manager's "Watch live" (M4).
                "live_checkpoint": bool(state.vq_submit_live_checkpoint),
            }
            template = state.calc_template or "single_point"

            # Human-readable job name: <stem>-<method>, sanitized to vq's
            # charset (alnum + - _ ., <=50 chars). Derived before the
            # payload because container mode names the archive after it.
            import re as _re

            stem = Path(reader.path).stem if reader.path else "structure"
            raw_name = f"vibeview-{stem}-{params['method']}"
            job_name = _re.sub(r"[^A-Za-z0-9._-]", "-", raw_name)[:50]

            # Container mode: the payload is one pending .qvf carrying the
            # structure + job.spec. vq recognizes the suffix and runs
            # `vibeqc run job.qvf`, which settles that same file in place --
            # so one file goes out, the settled one comes back, and it is
            # directly openable here. Falls back to the generated script
            # when vibeqc is not importable.
            container_mode = bool(
                state.vq_submit_container and _vibeqc_importable()
            )
            if container_mode:
                container_dir = tempfile.mkdtemp(prefix="vq_container_")
                input_path = str(
                    build_pending_container(
                        sdata, params, str(Path(container_dir) / job_name)
                    )
                )
            else:
                if state.vq_submit_container:
                    state.status_message = (
                        "vibeqc not importable - submitting a generated "
                        "script instead of a QVF container."
                    )
                script = generate_input_script(template, params)

                # Write to temp file
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".py", prefix="vq_input_", delete=False
                ) as f:
                    f.write(script)
                    input_path = f.name

            # vq-first (decision 1): submit through the queue via the
            # programmatic API so we can attach a job name + tag and get the
            # jobid back. Submitting via `vq` respects the shared-host
            # protocol — vibe-view never writes to the managed checkouts.
            try:
                from vq.submit import submit_local

                # A .qvf payload resolves its own runtime from the
                # program pin (vq builds `vibeqc run job.qvf`), so passing
                # an interpreter is rejected for container jobs.
                extra = {} if container_mode else {"python": sys.executable}
                job_id = submit_local(
                    "localhost",
                    input_file=input_path,
                    job_name=job_name,
                    tags=(
                        ["vibe-view", "qvf-container"]
                        if container_mode
                        else ["vibe-view"]
                    ),
                    **extra,
                )
                _kind = "container" if container_mode else "script"
                state.status_message = (
                    f"Submitted to vq ({_kind}): {job_name} "
                    f"({job_id[:12]})"
                )
                state.vq_submit_dialog = False
                # Surface it immediately in the Job Manager, monitoring live.
                state.vq_panel_open = True
                state.vq_monitor_active = True
                vq_refresh_jobs()
            except ImportError:
                # Fallback (decision 1): no queue here, but if vibe-qc is
                # importable run the job locally in the launch directory.
                import importlib.util

                if importlib.util.find_spec("vibeqc") is None:
                    from vibeview.install_hints import queue_missing_message

                    state.status_message = (
                        "Neither vq nor vibe-qc is installed — cannot run the "
                        f"job. {queue_missing_message()}"
                    )
                else:
                    run_dir = Path.cwd()
                    # A container settles in place, so run it where it sits;
                    # a script is executed in the launch directory.
                    if container_mode:
                        argv = [
                            sys.executable, "-m", "vibeqc._cli", "run",
                            input_path,
                        ]
                        cwd = str(Path(input_path).parent)
                        where = f"the container settles at {input_path}"
                    else:
                        argv = [sys.executable, input_path]
                        cwd = str(run_dir)
                        where = f"output .qvf will appear in {run_dir}"
                    subprocess.Popen(
                        argv,
                        cwd=cwd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    input_path = ""  # keep the payload; the run needs it
                    state.status_message = (
                        f"vq not found — running locally with vibe-qc; "
                        f"{where}."
                    )
                    state.vq_submit_dialog = False
        except Exception as e:  # noqa: BLE001 — surface any submit error
            state.status_message = f"Submit error: {e}"
        finally:
            state.vq_submitting = False
            if input_path:
                with contextlib.suppress(Exception):
                    Path(input_path).unlink(missing_ok=True)

    def _vq_load_overview() -> None:
        """Populate the queue-overview strip (host / daemon / capacity /
        load) via vq.overview — the A5 'what can the queue do' view. Never
        raises; clears the fields on any error."""
        state = server.state
        try:
            from vq import config as _vqcfg
            from vq.overview import format_overview_json, gather_overview_local

            cfg = _vqcfg.load() if hasattr(_vqcfg, "load") else _vqcfg.Config()
            d = format_overview_json(gather_overview_local("localhost", cfg))
            ver = d.get("vq_version") or "?"
            state.vq_ov_host = f"{d.get('host', 'localhost')} · vq {ver}"
            health = d.get("daemon_health") or {}
            state.vq_ov_daemon = bool(health.get("ok"))
            cap = []
            if d.get("max_cpus"):
                cap.append(f"max {d['max_cpus']} CPU")
            if d.get("max_jobs"):
                cap.append(f"{d['max_jobs']} jobs")
            state.vq_ov_capacity = " · ".join(cap)
            state.vq_ov_load = (
                f"running {d.get('running_cpus', 0)} · "
                f"pending {d.get('pending_cpus', 0)} CPU"
            )
        except ImportError:
            # A blank strip cannot be told apart from "the daemon is down",
            # so name the actual cause here; every other queue surface does.
            from vibeview.install_hints import queue_missing_message

            state.vq_ov_host = queue_missing_message()
            state.vq_ov_daemon = False
            state.vq_ov_capacity = ""
            state.vq_ov_load = ""
        except Exception:  # noqa: BLE001 — overview is best-effort
            state.vq_ov_host = ""
            state.vq_ov_daemon = False
            state.vq_ov_capacity = ""
            state.vq_ov_load = ""

    @ctrl.set("vq_refresh_jobs")
    def vq_refresh_jobs() -> None:
        """Refresh the Job Manager list. Shows all states when monitoring is
        on (so running/pending jobs appear), completed-only otherwise."""
        state = server.state
        state.vq_jobs_loading = True
        items, error = _vq_collect_jobs(all_states=bool(state.vq_monitor_active))
        state.vq_jobs_error = error or ""
        state.vq_jobs_list = items
        _vq_load_overview()
        state.vq_jobs_loading = False
        # If a job's detail is open, refresh it too so a running job's
        # state/log tail advance live alongside the list.
        if state.vq_detail_job_id:
            _vq_load_job_detail(state.vq_detail_job_id, state.vq_detail_name)

    def _vq_load_job_detail(job_id: str, name: str) -> None:
        """Populate the detail pane with a job's state + stdout/stderr tail
        via vq.status.show_status_json (never raises)."""
        state = server.state
        state.vq_detail_job_id = job_id
        state.vq_detail_name = name
        try:
            import json as _json

            from vq.status import show_status_json

            raw = show_status_json("localhost", job_id, tail=40)
            data = _json.loads(raw)
            state.vq_detail_state = str(data.get("state", "?"))
            out = (data.get("stdout") or "").strip()
            err = (data.get("stderr") or "").strip()
            parts = []
            if out:
                parts.append(out)
            if err:
                parts.append("stderr:\n" + err)
            state.vq_detail_log = "\n\n".join(parts) or "(no output yet)"
        except ImportError:
            from vibeview.install_hints import queue_missing_message

            state.vq_detail_log = queue_missing_message("read job status")
        except FileNotFoundError:
            state.vq_detail_log = f"Job {job_id[:12]} not found in the queue."
        except Exception as e:  # noqa: BLE001 — surface any status error
            state.vq_detail_log = f"Error reading status: {e}"

    @ctrl.set("vq_show_job_status")
    def vq_show_job_status(job_id: str, name: str = "") -> None:
        """Expand a job's live status + log tail in the panel."""
        state = server.state
        # Toggle off if the same job is clicked again.
        if state.vq_detail_job_id == job_id:
            state.vq_detail_job_id = ""
            state.vq_detail_log = ""
            state.vq_detail_state = ""
            return
        state.vq_detail_loading = True
        _vq_load_job_detail(job_id, name or job_id[:12])
        state.vq_detail_loading = False

    # Live monitor: poll the queue every 5 s while the switch is on.
    _vq_monitor_task = None

    # Watches the drawer too: the poll stops when the drawer closes, and
    # reopening it with the switch still on must restart it (#37-14).
    @_watch_state("vq_monitor_active", "vq_panel_open")
    def _on_vq_monitor_toggle(**kwargs: Any) -> None:
        nonlocal _vq_monitor_task
        want = bool(server.state.vq_monitor_active) and bool(server.state.vq_panel_open)
        if want:
            if _vq_monitor_task is not None and not _vq_monitor_task.done():
                return

            async def _poll() -> None:
                # Refresh once now, so switching the monitor on shows the
                # current jobs without a 5 s wait, then poll until the switch
                # is turned off or the panel closes. (The switch's old
                # `change=` handler never fired: Vuetify 3 has no such event.)
                vq_refresh_jobs()
                server.state.flush()
                while server.state.vq_monitor_active and server.state.vq_panel_open:
                    await asyncio.sleep(5)
                    if not (server.state.vq_monitor_active and server.state.vq_panel_open):
                        break
                    vq_refresh_jobs()
                    server.state.flush()

            _vq_monitor_task = _spawn_task(_poll())
        elif _vq_monitor_task is not None:
            _vq_monitor_task.cancel()
            _vq_monitor_task = None

    def _ensure_static_structure_shown() -> bool:
        """Rebuild the static structure if a previous animation hid it.

        The animation render paths (trajectory / vibrations / reaction.path)
        call ``_remove_static_structure`` and set ``structure_hidden`` so the
        frozen equilibrium geometry doesn't ghost behind the moving atoms
        (A5-03). When the user returns to any non-animation section we drop
        the stale animation actors and redraw the structure. Returns whether
        it redrew, so the dispatcher does not redraw the atoms a second time
        (#37-53).
        """
        nonlocal structure_renderer, is_periodic
        if not server.state.structure_hidden:
            return False
        rebuilt = False
        for prefix in (_Actors.VIB_ATOM, _Actors.TRAJ_ATOM, _Actors.TRAJ_CELL):
            _remove_actors_by_prefix(plotter, prefix)
        if structure_renderer is not None:
            _remove_static_structure(plotter)  # avoid duplicate actors
            # StructureRenderer caches its first StructureData. Reusing the
            # pre-edit instance after compare/animation mode would therefore
            # redraw the archive atoms and cell over a newer edit overlay.
            # Rebuild from the active reader so the restored scene sees the
            # same current atoms+lattice as export and input generation.
            structure_renderer, is_periodic = _build_structure_scene(
                reader,
                plotter,
                server.state,
                replication=_triple(viewer_state.replication),
                show_labels=bool(server.state.show_atom_labels),
                representation=str(
                    getattr(server.state, "representation_style", "ball_and_stick")
                ),
                cartoon_color_mode=str(
                    getattr(server.state, "cartoon_color_mode", "chain")
                ),
                residue_selection=str(
                    getattr(server.state, "residue_selection", "") or ""
                ),
            )
            rebuilt = True
        # ── Rebuild the active volume ──────────────────────────────────
        server.state.structure_hidden = False
        return rebuilt

    def _activate_section_impl(section_id: str) -> None:
        """Invalidate worker renders, then mutate the VTK scene exclusively."""
        if server.state.relocalize_running:
            _cancel_relocalization()
        _invalidate_wavefunction_renders()
        with _wf_render_lock:
            _activate_section_impl_unlocked(section_id)

    def _activate_section_impl_unlocked(section_id: str) -> None:
        """Lazy activation: called when user clicks a section in the sidebar."""
        state = server.state
        # Synthetic "Molecular Orbitals" sidebar entry: route to the first
        # orbital section and let the right-panel picker switch between them.
        if section_id == _ORBITAL_GROUP_ID:
            if not orbital_sections:
                state.status_message = "No orbital sections in this file."
                return
            first_id = orbital_sections[0]["id"]
            state.selected_orbital = first_id
            activate_section(first_id)
            return
        if section_id == _BASIS_AO_GROUP_ID:
            if not basis_ao_sections:
                state.status_message = "No basis function sections in this file."
                return
            first_id = basis_ao_sections[0]["id"]
            state.selected_ao = first_id
            activate_section(first_id)
            return
        section = next((s for s in reader.sections if s.id == section_id), None)
        if section is None:
            state.status_message = f"Section {section_id!r} not found."
            return
        push_generation = _scene_hooks(plotter).push_generation
        # Keep the orbital picker in sync when the user activates an orbital
        # directly (e.g. via the right-panel select).
        if section.kind == "volume.orbital":
            state.selected_orbital = section_id
        # Same for basis.ao.
        if section.kind == "basis.ao":
            state.selected_ao = section_id

        state.selected_section = section_id
        # Show the calculation-parameter panel only when the structure
        # section is active; default calc_template to "periodic" for
        # periodic systems.
        # By kind, like every other structure test: a file whose structure
        # section has another id lost the panel (#37-43).
        is_structure = section.kind == "structure"
        state.show_param_panel = is_structure
        if is_structure and state.is_periodic:
            state.calc_template = "periodic"
        # Remember whether we're switching away from atom_properties so we
        # can restore the CPK colour palette on the 3D atoms below.
        _was_atom_props = bool(state.atom_properties_active)
        _clear_output_panels(state)
        # Whether this switch changed the server-side scene. A 2D panel whose
        # cleanup removed nothing needs no push: serializing the whole 3D
        # scene on every chart click was the cost (#37-49).
        scene_changed = _remove_atom_charge_labels(plotter)
        # Drop any 3D overlay actors from a previously activated section
        # when their per-section replay state is cleared. Without removing the
        # computed surface too, a 2D or different wavefunction section could
        # show stale MO geometry with no visibility control left to remove it.
        # A previously shown Fermi surface lives in reciprocal space; drop its
        # sheets + cell wireframe whenever we switch to anything else.
        for prefix in (_Actors.QTAIM, _Actors.MO_SURFACE, _Actors.FERMI):
            scene_changed |= bool(_remove_actors_by_prefix(plotter, prefix))
        # A QVF-backed volume has its own active-volume lifecycle and may stay
        # beside a 2D panel. It must still be removed when entering a computed
        # wavefunction view, or an MO would render inside an uncontrolled old
        # density (reported 2026-07-19).
        if section.kind == "wavefunction.gto":
            scene_changed |= bool(_remove_actors_by_prefix(plotter, _Actors.VOLUME))
        # Navigating into a section exits compare/overlay mode (a distinct mode).
        if state.compare_mode:
            state.compare_mode = False
            state.compare_legend = []
            _remove_actors_by_prefix(plotter, _Actors.COMPARE)
            scene_changed = True
        handler = _section_handler(section.kind)
        # If we're leaving an animation, restore the static structure that
        # the trajectory/vibration/reaction path hid (A5-03). fermi_surface
        # replaces the real-space scene entirely, so it stays hidden there too.
        rebuilt = False
        if handler is None or not handler.keeps_structure_hidden:
            rebuilt = _ensure_static_structure_shown()
            scene_changed |= rebuilt
        # Leaving atom_properties: the atoms may still carry the charge tint.
        # One step for every kind that shows the atoms as they are (#37-27);
        # a structure the line above just redrew is already in CPK (#37-53).
        if _was_atom_props and not rebuilt and (handler is None or handler.restores_cpk):
            _restore_cpk_atoms(reader, plotter, state)
            scene_changed = True
        if handler is not None:
            handler.activate(
                _SectionContext(reader, plotter, state, viewer_state, _all_readers), section
            )

        # Sync the scene when the target activator did not already do so;
        # otherwise VtkLocalView can retain removed charge or computed-surface
        # actors until a later 3D action. Avoid a duplicate push for volume and
        # other mesh activators because serializing those scenes is expensive,
        # and skip it for a 2D panel that left the scene as it was.
        pushed = _scene_hooks(plotter).push_generation != push_generation
        if not pushed and (scene_changed or section.kind not in _two_d_panel_kinds()):
            plotter.render()
            _push_view(plotter)

    @ctrl.set("activate_section")
    def activate_section(section_id: str) -> None:
        """Guarded entry point for section switching.

        The per-kind render dispatch (`_activate_section_impl`) does a lot of
        synchronous work in a dozen renderers; a single one raising must not
        take down or freeze the whole viewer. Catch it, log it, surface a
        status message, and leave the 3D viewport usable so the user can just
        switch to another section instead of a dead/frozen app.
        """
        try:
            _activate_section_impl(section_id)
        except Exception as e:  # noqa: BLE001 — a bad section must stay recoverable
            import traceback

            traceback.print_exc()
            server.state.status_message = (
                f"Could not open section {section_id!r}: {type(e).__name__}: {e}"
            )
            # Best-effort: restore a viewable static structure + resync client.
            try:
                _ensure_static_structure_shown()
                plotter.render()
                _push_view(plotter)
            except Exception:  # noqa: BLE001 — recovery is best-effort
                pass

    def _refresh_active_volume(
        *, hints: dict[str, Any] | None = None, invalidate_mesh: bool = False
    ) -> None:
        """Rebuild the active volume after one of its controls changed.

        One guard for every volume control, which used three (#37-30): the
        control's ``hints`` are recorded on the active volume, and the
        surface is rebuilt only while that volume is loaded.
        ``invalidate_mesh`` drops the cached mesh for a change the cache key
        does not see. ``_rebuild_volume`` pushes the view itself, so no
        control pushes again.
        """
        state = server.state
        vol_id = state.active_volume_id
        if not vol_id:
            return
        if hints:
            volume_hints = viewer_state.get_volume_hints(vol_id)
            for name, value in hints.items():
                setattr(volume_hints, name, value)
        if not state.volume_loaded:
            return
        if invalidate_mesh:
            viewer_state.invalidate_mesh_cache(vol_id)
        _rebuild_volume(reader, plotter, viewer_state, state, vol_id)

    @ctrl.set("update_isovalue")
    def update_isovalue(isovalue: float) -> None:
        state = server.state
        state.isovalue = isovalue
        _refresh_active_volume(hints={"isovalue": isovalue})

    @ctrl.set("update_fermi_bands")
    def update_fermi_bands(selected: Any) -> None:
        """Re-render the Fermi surface showing only the selected band indices."""
        state = server.state
        state.fermi_selected_bands = list(selected) if selected else []
        section = next((s for s in reader.sections if s.id == state.selected_section), None)
        if section is not None and section.kind == "fermi_surface":
            _activate_fermi(reader, plotter, viewer_state, state, section)

    @ctrl.set("update_colormap")
    def update_colormap(colormap: str) -> None:
        state = server.state
        state.colormap = colormap
        _refresh_active_volume(hints={"colormap": colormap})

    @ctrl.set("toggle_scf_skip_guess")
    def toggle_scf_skip_guess(value: Any = None) -> None:
        """Re-render the SCF convergence plot with/without the initial guess."""
        state = server.state
        state.scf_skip_guess = (
            bool(value) if value is not None else not state.scf_skip_guess
        )
        section = next(
            (s for s in reader.sections if s.id == state.selected_section), None
        )
        if section is not None and section.kind == "scf_history":
            _activate_scf_history(reader, state, section)

    @ctrl.set("toggle_scf_log_energy")
    def toggle_scf_log_energy(value: Any = None) -> None:
        """Re-render the SCF plot on a linear or log |E − E_final| axis."""
        state = server.state
        state.scf_log_energy = (
            bool(value) if value is not None else not state.scf_log_energy
        )
        section = next(
            (s for s in reader.sections if s.id == state.selected_section), None
        )
        if section is not None and section.kind == "scf_history":
            _activate_scf_history(reader, state, section)

    @ctrl.set("update_spectra_view")
    def update_spectra_view(
        gamma_scale: Any = None,
        normalize: Any = None,
        x_unit: Any = None,
        display: Any = None,
    ) -> None:
        """Re-render the active spectrum after a display-control change."""
        state = server.state
        if isinstance(gamma_scale, (int, float)) and not isinstance(gamma_scale, bool):
            state.spectra_gamma_scale = max(0.05, float(gamma_scale))
        if normalize is not None:
            state.spectra_normalize = bool(normalize)
        if x_unit is not None:
            state.spectra_x_unit = str(x_unit) or "native"
        if display is not None:
            state.spectra_display = str(display) or "both"
        section = next(
            (s for s in reader.sections if s.id == state.selected_section), None
        )
        if section is not None and section.kind in _plot_spectra_kinds():
            _activate_spectra(reader, state, section, _all_readers)

    def _redraw_bands_panel() -> None:
        """Re-run whichever activator owns the bottom panel right now."""
        state = server.state
        section = next(
            (s for s in reader.sections if s.id == state.selected_section), None
        )
        if section is None or section.kind not in ("bands", "dos.total", "dos.projected"):
            return
        handler = _section_handler(section.kind)
        assert handler is not None
        handler.activate(
            _SectionContext(reader, plotter, state, viewer_state, _all_readers), section
        )

    @ctrl.set("update_bands_energy_window")
    def update_bands_energy_window(*_ignored: Any) -> None:
        """Redraw the bands / DOS chart on a reader-set energy window (#26).

        Called with nothing: the fields v-model straight onto state, so
        both the Enter and the blur binding read the committed values from
        there. A pair that does not make a range — a blank field, an
        inverted pair, a half-typed minus sign — falls back to the
        previous window rather than blanking the chart; that call lives in
        ``_requested_energy_window``.
        """
        state = server.state
        # VTextField with type="number" still v-models as a string.
        state.bands_emin = _coerce_window_bound(state.bands_emin)
        state.bands_emax = _coerce_window_bound(state.bands_emax)
        state.bands_window_auto = True
        if state.bands_emin is not None and state.bands_emax is not None:
            state.bands_window_auto = False
        _redraw_bands_panel()

    @ctrl.set("reset_bands_energy_window")
    def reset_bands_energy_window() -> None:
        """Drop a reader-set window, back to the default view (#26)."""
        state = server.state
        state.bands_window_auto = True
        state.bands_emin = None
        state.bands_emax = None
        _redraw_bands_panel()

    @ctrl.set("download_run_attachment")
    def download_run_attachment(role: str | None = None) -> None:
        """Offer one run.record attachment as a browser download.

        Attachments are arbitrary bytes by spec, so they are never
        rendered or interpreted — this is the only path that touches
        their payload, and it hands the bytes straight to a download.
        The media type is forced to ``application/octet-stream`` so the
        browser saves rather than displays: serving an archive-supplied
        type could invite a text/html attachment to be rendered as a
        document.
        """

        role = _first(role)
        state = server.state
        section_id = str(state.run_record_attachment_section or "")
        if not role or not section_id:
            return
        entry = next(
            (
                a
                for a in (state.run_record_attachments or [])
                if a.get("role") == role
            ),
            None,
        )
        if entry is None:
            state.status_message = f"Unknown attachment: {role}"
            return
        if entry.get("too_large"):
            state.status_message = (
                f"{entry['filename']} is too large to download from the "
                "viewer — extract it from the archive directly."
            )
            return
        try:
            payload = reader.read_run_record_attachment_bytes(
                section_id, str(role)
            )
        except QVFError as e:
            state.status_message = f"Attachment unreadable: {e}"
            return
        if payload is None:
            state.status_message = f"Attachment unavailable: {role}"
            return
        _offer_download(
            payload,
            "application/octet-stream",
            entry["filename"],
            label=f"Attachment ({entry['filename']})",
            fmt="BYTES",
        )
        state.status_message = f"Downloading {entry['filename']}"

    @ctrl.set("export_spectra_csv")
    def export_spectra_csv() -> None:
        """Export the active spectrum's broadened envelope + peaks as CSV,
        offered as a browser download (design refresh 2026, spectra controls).
        """

        from vibeview.renderers.spectra import SpectraRenderer

        state = server.state
        section = next(
            (s for s in reader.sections if s.id == state.selected_section), None
        )
        if section is None or section.kind not in _plot_spectra_kinds():
            return
        csv = SpectraRenderer(section, reader).to_csv(
            gamma_scale=float(getattr(state, "spectra_gamma_scale", 1.0)),
            normalize=bool(getattr(state, "spectra_normalize", False)),
            x_unit=str(getattr(state, "spectra_x_unit", "native")),
        )
        stem = _job_name(reader) or "spectrum"
        _offer_download(
            csv.encode("utf-8"),
            "text/csv",
            f"{stem}-{section.id}.csv",
            label=f"Spectrum CSV ({section.id})",
            fmt="CSV",
        )
        state.status_message = f"Exported {state.export_filename}"

    @ctrl.set("toggle_spectra_compare")
    def toggle_spectra_compare(value: Any = None) -> None:
        """Re-render the active spectrum with/without the multi-file overlay."""
        state = server.state
        state.spectra_compare = (
            bool(value) if value is not None else not state.spectra_compare
        )
        section = next(
            (s for s in reader.sections if s.id == state.selected_section), None
        )
        if section is not None and section.kind in _plot_spectra_kinds():
            _activate_spectra(reader, state, section, _all_readers)

    @ctrl.set("toggle_color_by_esp")
    def toggle_color_by_esp(on: bool | None = None) -> None:
        """Enable/disable ESP colour-mapping on the density isosurface."""
        state = server.state
        state.color_by_esp = (not state.color_by_esp) if on is None else bool(on)
        if state.color_by_esp:
            state.colormap = "RdBu"
        _refresh_active_volume()

    @ctrl.set("toggle_reduce_detail")
    def toggle_reduce_detail(on: bool | None = None) -> None:
        """Toggle LOD subsampling for large volume grids."""
        state = server.state
        state.reduce_detail = (not state.reduce_detail) if on is None else bool(on)
        _refresh_active_volume(invalidate_mesh=True)

    @ctrl.set("add_extra_isosurface")
    def add_extra_isosurface() -> None:
        """Add a secondary isosurface layer at half the current isovalue."""
        state = server.state
        iso = float(state.isovalue or 0.05)
        extras = list(state.extra_isosurfaces or [])
        colour = _palette.EXTRA_ISOSURFACES[len(extras) % len(_palette.EXTRA_ISOSURFACES)]
        extras.append({"isovalue": iso * 0.5, "opacity": 0.35, "colour": colour})
        state.extra_isosurfaces = extras
        _refresh_active_volume()

    @ctrl.set("remove_extra_isosurface")
    def remove_extra_isosurface(index: int) -> None:
        """Remove an extra isosurface layer by index."""
        state = server.state
        extras = list(state.extra_isosurfaces or [])
        if 0 <= index < len(extras):
            extras.pop(index)
            state.extra_isosurfaces = extras
        _refresh_active_volume()

    @ctrl.set("update_opacity")
    def update_opacity(opacity: float) -> None:
        state = server.state
        state.opacity = opacity
        _refresh_active_volume(hints={"opacity": opacity})

    @ctrl.set("update_mo_opacity")
    def update_mo_opacity(opacity: float) -> None:
        """Retint the orbital lobes.

        Kept separate from update_opacity: MO lobes are their own actors
        (mo_iso_pos/neg) rather than an "active volume", and activating a
        volume section overwrites ``state.opacity`` from that volume's hints,
        which used to clobber whatever the user had set for the orbital.
        Retints in place — re-evaluating the MO grid to change one number
        costs ~1 s.
        """
        state = server.state
        state.mo_opacity = float(opacity)
        retinted = False
        # Every computed surface, not only the orbital's two lobes (#37-36).
        for _name, _actor in list(plotter.actors.items()):
            if not str(_name).startswith(_Actors.MO_SURFACE):
                continue
            _prop = _actor.GetProperty() if _actor is not None else None
            if _prop is not None:
                _prop.SetOpacity(_base_opacity(plotter, str(_name)) * float(opacity))
                retinted = True
        if retinted:
            plotter.render()
            _push_view(plotter)

    @ctrl.set("set_periodic_replication")
    def set_periodic_replication(value: Any) -> None:
        """Set grid-level periodic orbital replication.

        When > 0 the volume grid is tiled N×N×N into a (2N+1)³
        supercell *before* marching cubes, so orbital isosurfaces
        repeat seamlessly across adjacent unit cells.
        """
        state = server.state
        state.periodic_replication = int(value or 0)
        _refresh_active_volume()
        state.status_message = f"Cell replication: {state.periodic_replication}"

    @ctrl.set("toggle_wrap_periodic")
    def toggle_wrap_periodic(on: bool | None = None) -> None:
        """Toggle minimum-image recentre of the active periodic orbital.

        Rolls a face-straddling orbital / Wannier function whole into the
        cell. The mesh cache is keyed on (isovalue, replication), not the
        wrap flag, so invalidate it before rebuilding.
        """
        state = server.state
        state.wrap_periodic_orbital = (
            (not state.wrap_periodic_orbital) if on is None else bool(on)
        )
        _refresh_active_volume(invalidate_mesh=True)
        state.status_message = (
            "Wrap to cell centre: on" if state.wrap_periodic_orbital else "Wrap to cell centre: off"
        )

    @ctrl.set("toggle_wannier_centers")
    def toggle_wannier_centers(on: bool | None = None) -> None:
        """Show/hide the Wannier-centre marker overlay."""
        state = server.state
        state.show_wannier_centers = (
            (not state.show_wannier_centers) if on is None else bool(on)
        )
        n = _draw_wannier_overlay(reader, plotter, state.show_wannier_centers)
        _push_view(plotter)
        if state.show_wannier_centers:
            state.status_message = f"Wannier centres: {n} shown"
        else:
            state.status_message = "Wannier centres: hidden"

    def _set_replication(counts: Sequence[int]) -> None:
        """Set the displayed supercell, in the one place that writes it.

        The renderers read ``viewer_state.replication`` and the Nx/Ny/Nz
        inputs show ``replication_n*``. Four writers kept the two in step by
        hand, and a session restore needed its own re-sync (#37-20).
        """
        nx, ny, nz = (int(n) for n in counts)
        viewer_state.replication = (nx, ny, nz)
        state = server.state
        state.replication_nx, state.replication_ny, state.replication_nz = nx, ny, nz

    @ctrl.set("update_replication")
    def update_replication(nx: int, ny: int, nz: int) -> None:
        # VTextField with type="number" still v-models as a string.
        nx, ny, nz = int(nx), int(ny), int(nz)
        state = server.state
        # Stop any playing animation first. Each animation loop
        # (trajectory / vibration) rebuilds + pushes a frame on a timer;
        # left running, those frames race the replication rebuild below and
        # keep overwriting it with single-cell geometry, so the supercell
        # flickers or never appears ("multiplying with animations is buggy",
        # reported 2026-07-16). This controller is synchronous, so clearing
        # the flags here means the loops see them false and exit before they
        # can push again.
        if getattr(state, "trajectory_playing", False):
            state.trajectory_playing = False
        if getattr(state, "vibration_playing", False):
            state.vibration_playing = False
        # Clamp non-periodic axes to 1. The UI hides those inputs, but a stale
        # client value or a scripted call must not tile along a synthesized
        # lattice column (for a 2D slab that would stack phantom sheets).
        nx, ny, nz = clamp_replication(
            (nx, ny, nz),
            (bool(state.pbc_a), bool(state.pbc_b), bool(state.pbc_c)),
        )
        _set_replication((nx, ny, nz))
        # Rebuild the whole 3D scene with new replication
        rebuild_error = _rebuild_scene(reader, plotter, viewer_state, state)
        if rebuild_error is None:
            state.status_message = f"Replication: {nx}×{ny}×{nz}"
        # Refit the CLIENT camera to the grown lattice. The scene now spans
        # nx*ny*nz cells but VtkLocalView keeps its own camera, so without
        # this the view stays zoomed into the original single cell (a
        # replicated crystal renders as a few clipped giant atoms). The
        # server-side reset in camera_preset('reset') doesn't reach the
        # local view; local_view.reset_camera does.
        with contextlib.suppress(Exception):
            ctrl.view_reset_camera()

    @ctrl.set("toggle_atom_labels")
    def toggle_atom_labels(show: bool | None = None) -> None:
        # Called with an explicit bool from the right-panel switch
        # (update_modelValue → [$event]) and with no args from the app-bar
        # icon button. The latter flips the current state.
        if show is None:
            show = not bool(server.state.show_atom_labels)
        server.state.show_atom_labels = bool(show)
        _rebuild_scene(reader, plotter, viewer_state, server.state)

    @ctrl.set("set_representation_style")
    def set_representation_style(style: str) -> None:
        """Switch structure representation: ball_and_stick, space_filling,
        sticks_only, wireframe, or cartoon.

        ``cartoon`` needs residue identity (a PDB, not an XYZ). The
        renderer falls back to ball-and-stick without it; say so here
        rather than leaving the picker reading "Cartoon" over an
        unchanged scene.
        """
        valid = {
            "ball_and_stick",
            "space_filling",
            "sticks_only",
            "wireframe",
            "cartoon",
        }
        if style not in valid:
            return
        if style == "cartoon":
            try:
                structure = reader.read_structure()
            except Exception:  # noqa: BLE001 — fall through to the plain switch
                structure = None
            if structure is not None and not (
                structure.has_residues and len(structure.backbone_trace())
            ):
                server.state.status_message = (
                    "Cartoon needs a backbone: this structure carries no "
                    "residue/chain information (open a PDB to use it)."
                )
                return
        server.state.representation_style = style
        server.state.structure_color_mode = (
            server.state.cartoon_color_mode if style == "cartoon" else server.state.atom_color_mode
        )
        _rebuild_scene(reader, plotter, viewer_state, server.state)

    @ctrl.set("set_cartoon_color_mode")
    def set_cartoon_color_mode(mode: str) -> None:
        """Colour the cartoon ribbon by chain, secondary structure,
        residue type, or b-factor."""
        mode = _first(mode)
        if mode not in ("element", "chain", "structure", "residue", "bfactor"):
            return
        server.state.cartoon_color_mode = mode
        server.state.atom_color_mode = mode
        server.state.structure_color_mode = mode
        rebuild_error = _rebuild_scene(reader, plotter, viewer_state, server.state)
        if mode == "bfactor" and rebuild_error is None:
            # The b-factor ramp is normalised over this structure, so the
            # colours mean nothing without the range they span. Say it
            # rather than leaving the user to guess the scale, and say so
            # plainly when the file carries no b-factors at all — the
            # ribbon falls back to chain colour and would otherwise look
            # like the mode silently failed.
            #
            # AFTER a successful rebuild, not before: otherwise a failure
            # message is hidden by the colour-range summary.
            server.state.status_message = _bfactor_range_message(reader)

    @ctrl.set("set_residue_selection")
    def set_residue_selection(spec: Any = None, *_ignored: Any) -> None:
        """Select residues or chains in every structure representation (D4).

        Takes a selection string — ``A``, ``A/24-38``, ``*/24-38``, comma
        or space separated. See
        ``vibeview.renderers.structure.parse_residue_selection``. Called
        with nothing it reads the field's committed value off state, which
        is how the Enter and blur handlers share one entry point; the DOM
        event object those pass is ignored.

        The summary is the whole point of the control: a selection that
        matches nothing must say so, or it looks like a render bug. So
        this reports the residue count, and calls out an absent chain id
        or an unreadable term separately.
        """
        spec = _first(spec, "")
        if spec is None or isinstance(spec, dict):
            # No argument, or a DOM event from the blur / Enter handler.
            spec = server.state.residue_selection or ""
        spec = str(spec)
        server.state.residue_selection = spec
        if not spec.strip():
            server.state.residue_visibility = "all"
        server.state.residue_selection_summary = _residue_selection_summary(
            reader, spec
        )

        # Every representation now draws the selection. Avoid a rebuild only
        # on structures that have no residue membership and therefore cannot
        # match any term.
        if server.state.residue_selection_available:
            _rebuild_scene(reader, plotter, viewer_state, server.state)

    @ctrl.set("set_residue_visibility")
    def set_residue_visibility(mode: str = "all") -> None:
        if mode not in ("all", "isolate", "hide"):
            return
        if mode != "all":
            from vibeview.renderers.structure import _selected_atom_indices, parse_residue_selection

            terms, _ = parse_residue_selection(server.state.residue_selection or "")
            if not _selected_atom_indices(reader.read_structure(), terms):
                server.state.residue_visibility = "all"
                server.state.status_message = "Select residues before hiding or isolating them."
                return
        server.state.residue_visibility = mode
        _rebuild_scene(reader, plotter, viewer_state, server.state)

    @ctrl.set("set_residue_pick_mode")
    def set_residue_pick_mode(enabled: bool = False) -> None:
        server.state.residue_pick_mode = bool(enabled)
        if enabled:
            server.state.measure_mode = False
            server.state.edit_mode = False
            server.state.status_message = (
                "Click a residue to select it; use the selection field for ranges."
            )
        else:
            server.state.status_message = "Residue picking off"

    @ctrl.set("toggle_hydrogen_bonds")
    def toggle_hydrogen_bonds(enabled: bool = False) -> None:
        server.state.show_hydrogen_bonds = bool(enabled)
        _rebuild_scene(reader, plotter, viewer_state, server.state)
        if enabled:
            from vibeview.renderers.structure import hydrogen_bond_contacts

            count = len(hydrogen_bond_contacts(reader.read_structure()))
            server.state.status_message = (
                f"{count} geometric hydrogen-bond contact(s) in the input cell; "
                "explicit H required."
            )

    @ctrl.set("set_element_color")
    def set_element_color(z: Any = None, color: Any = None) -> None:
        """Override the render colour of one element (design refresh 2026).

        Keyed by atomic number; ``set_color_overrides`` feeds cpk_color(), so
        every render path — glyph batching, per-atom actors, replicated cells,
        compare mode — picks the new colour up on the rebuild below.
        """
        from vibeview.renderers.structure import set_color_overrides

        state = server.state
        z = _first(z)
        if z is None:
            z = state.element_color_z
        if not color:
            color = state.element_color_value
        if z is None or not color:
            state.status_message = "Pick an element and a colour first"
            return
        # dict keys round-trip through the client as strings; normalise.
        overrides = {int(k): v for k, v in (state.element_colors or {}).items()}
        overrides[int(z)] = str(color)
        state.element_colors = {str(k): v for k, v in overrides.items()}
        set_color_overrides(overrides)
        rebuild_error = _rebuild_scene(reader, plotter, viewer_state, state)
        sym = next(
            (e["title"] for e in (state.element_color_options or []) if e["value"] == int(z)),
            str(z),
        )
        if rebuild_error is None:
            state.status_message = f"{sym} colour → {color}"

    @ctrl.set("reset_element_colors")
    def reset_element_colors() -> None:
        """Drop all per-element colour overrides (back to the CPK palette)."""
        from vibeview.renderers.structure import set_color_overrides

        state = server.state
        state.element_colors = {}
        set_color_overrides(None)
        rebuild_error = _rebuild_scene(reader, plotter, viewer_state, state)
        if rebuild_error is None:
            state.status_message = "Element colours reset to CPK"

    @ctrl.set("set_material_preset")
    def set_material_preset(name: str) -> None:
        """Apply a material preset to the scene."""
        server.state.material_preset = name
        server.state.background_override = None
        preset = _apply_scene_appearance(plotter, server.state)
        server.state.status_message = f"Material: {preset.name}"
        _counted_view_update()

    @ctrl.set("set_charge_kind")
    def set_charge_kind(kind: str) -> None:
        server.state.charge_kind = kind or "mulliken"
        if server.state.atom_properties_active:
            # Re-render both the HTML table (filtered to selected method)
            # and the 3D colour overlay.
            overlay_attempted = False
            try:
                section = reader.get_section(
                    server.state.atom_properties_section_id
                )
                from vibeview.renderers.atom_properties import AtomPropertiesRenderer

                renderer = AtomPropertiesRenderer(section, reader)
                with contextlib.suppress(QVFError):
                    server.state.properties_html = renderer.render_to_html(
                        charge_kind=(
                            server.state.charge_kind
                            if server.state.charge_kind
                            else None
                        )
                    )
                overlay_attempted = True
                _render_atom_properties_overlay(reader, plotter, server.state)
            except Exception as e:  # noqa: BLE001 — keep UI state truthful
                if not overlay_attempted:
                    _rollback_atom_properties_overlay(
                        reader, plotter, server.state, push=True
                    )
                server.state.atom_properties_active = False
                server.state.atom_properties_section_id = None
                server.state.status_message = f"Charge overlay error: {e}"

    def _cancel_relocalization(message: str = "") -> None:
        nonlocal _relocalize_generation, _relocalize_task
        _relocalize_generation += 1
        task, _relocalize_task = _relocalize_task, None
        if task is not None and not task.done():
            task.get_loop().call_soon_threadsafe(task.cancel)
        server.state.relocalize_running = False
        server.state.relocalize_status = message

    @ctrl.set("cancel_relocalize")
    def cancel_relocalize() -> None:
        _cancel_relocalization("Re-localization cancelled")

    @ctrl.set("clear_relocalization")
    def clear_relocalization() -> None:
        _cancel_relocalization()
        _invalidate_wavefunction_renders()
        with _wf_render_lock:
            state = server.state
            ids = [
                sid
                for sid in reader.wavefunction_overlay_ids
                if sid.startswith(RELOCALIZED_PREFIX)
            ]
            source_id = None
            if state.wf_section_id in ids:
                source_id = _relocalize_source_section(reader, state.wf_section_id)
                _remove_actors_by_prefix(plotter, _Actors.MO_SURFACE)
                state.mo_visible = False
                state.wf_surface_kind = None
            reader.clear_wavefunction_overlays(ids)
            sidebar_entries[:] = [e for e in sidebar_entries if e["id"] not in ids]
            section_list[:] = [e for e in section_list if e["id"] not in ids]
            state.sidebar_entries = sidebar_entries
            state.section_list = section_list
            state.dirty("sidebar_entries", "section_list")
            state.relocalize_has_overlay = False
            state.relocalize_result_summary = ""
            state.relocalize_charge_rows = []
            if source_id:
                state.selected_section = source_id
                _activate_wavefunction(reader, state, reader.get_section(source_id))
            # Nothing relocalized, nothing in the scene changed. Every edit
            # and every live-optimization step comes through here, and the
            # unconditional push doubled their scene serializations (#37-47).
            if ids:
                _push_view(plotter)

    def _geometry_invalidates_relocalization() -> None:
        clear_relocalization()
        server.state.relocalize_supported = False
        server.state.relocalize_reason = (
            "Geometry edits invalidate archived orbitals; choose New RHF + localize explicitly"
        )

    _scene_hooks(plotter).invalidate_relocalization = _geometry_invalidates_relocalization
    _scene_hooks(plotter).refresh_relocalization = lambda: _refresh_relocalize_state(
        reader, server.state
    )

    @ctrl.set("check_relocalize_backend")
    def check_relocalize_backend(**_: Any) -> None:
        nonlocal _relocalize_probe_task
        from vibeview.relocalize import probe_worker

        state = server.state
        path = str(state.relocalize_backend_python or "")
        if _relocalize_probe_task is not None and not _relocalize_probe_task.done():
            _relocalize_probe_task.cancel()
        state.relocalize_probe_running = True
        state.relocalize_backend_status = "Checking native backend and localization methods…"
        state.relocalize_available = None
        state.relocalize_capabilities = {}
        _refresh_relocalize_state(reader, state)

        async def work() -> None:
            try:
                result = await probe_worker(path)
                if (
                    path != state.relocalize_backend_python
                    or asyncio.current_task() is not _relocalize_probe_task
                ):
                    return
                state.relocalize_available = result["available"]
                state.relocalize_capabilities = (
                    result["capabilities"] if result["available"] else {}
                )
                if result["available"]:
                    cap = result["capabilities"]
                    ready = [name for name, spec in cap["methods"].items() if spec.get("ready")]
                    state.relocalize_backend_status = (
                        f"vibe-qc {cap.get('backend_version', '')}: " + ", ".join(ready) + " ready"
                    )
                else:
                    state.relocalize_backend_status = result["reason"]
                _refresh_relocalize_state(reader, state)
            finally:
                if asyncio.current_task() is _relocalize_probe_task:
                    state.relocalize_probe_running = False
                    state.flush()

        try:
            _relocalize_probe_task = asyncio.get_running_loop().create_task(work())
        except RuntimeError:
            state.relocalize_probe_running = False
            state.relocalize_backend_status = "Check the backend after the viewer starts"

    ctrl.on_server_ready.add(check_relocalize_backend)

    @_watch_state("relocalize_source")
    def _relocalize_source_changed(**_: Any) -> None:
        _cancel_relocalization()
        _refresh_relocalize_state(reader, server.state)

    @ctrl.set("run_relocalize")
    def run_relocalize() -> None:
        nonlocal _relocalize_task
        from vibeview.relocalize import (
            METHOD_LABELS,
            overlay_section_id,
            request_fingerprint,
            request_from_reader,
            run_relocalization,
            wavefunction_from_result,
        )

        state = server.state
        if state.relocalize_running:
            return
        _refresh_relocalize_state(reader, state)
        if not state.relocalize_supported:
            state.relocalize_status = state.relocalize_reason
            return
        method, source = str(state.relocalize_method), str(state.relocalize_source)
        if method not in [o["value"] for o in state.relocalize_method_options]:
            state.relocalize_status = "Selected method is unavailable"
            return
        source_reader = reader
        source_id = _relocalize_source_section(reader, state.wf_section_id)
        canonical = reader.read_wavefunction_gto(source_id)
        path = str(state.relocalize_backend_python or "")
        revision = reader.geometry_revision
        generation = _relocalize_generation
        try:
            request = request_from_reader(
                reader, method, canonical_section_id=source_id, source=source
            )
        except (QVFError, ValueError) as exc:
            state.relocalize_status = str(exc)
            return
        fingerprint = request_fingerprint(request)
        state.relocalize_running = True
        state.relocalize_status = (
            "Calculating new RHF orbitals and localizing…"
            if source == "fresh_rhf"
            else "Re-localizing the archived occupied subspace…"
        )

        def current() -> bool:
            return bool(
                generation == _relocalize_generation
                and reader is source_reader
                and reader.geometry_revision == revision
                and state.relocalize_backend_python == path
            )

        async def work() -> None:
            try:
                result = await run_relocalization(request, backend_python=path)
                with _wf_render_lock:
                    if not current():
                        return
                    if "error" in result:
                        state.relocalize_status = f"Failed: {result['error']}"
                        return
                    latest = request_from_reader(
                        reader, method, canonical_section_id=source_id, source=source
                    )
                    if request_fingerprint(latest) != fingerprint:
                        state.relocalize_status = "Discarded result: source data changed"
                        return
                    overlay = wavefunction_from_result(result, canonical, request)
                    overlay.relocalization["source_section_id"] = source_id
                    section_id = overlay_section_id(method, source)
                    # Never shadow a section that came from the archive.
                    if section_id not in reader.wavefunction_overlay_ids and any(
                        s.id == section_id for s in reader.sections
                    ):
                        section_id += "_" + request["id"][:8]
                    reader.set_wavefunction_overlay(section_id, overlay)
                    entry = {
                        "id": section_id,
                        "title": _sidebar_section_title(
                            {"id": section_id, "kind": "wavefunction.gto"}
                        ),
                        "subtitle": "wavefunction.gto — new RHF"
                        if source == "fresh_rhf"
                        else "wavefunction.gto — archived subspace",
                        "status": "ok",
                        "supported": True,
                        "icon": _status_icon("ok"),
                        "kind_icon": _kind_icon("wavefunction.gto"),
                        "warn": False,
                        "disabled": False,
                    }
                    sidebar_entries[:] = [e for e in sidebar_entries if e["id"] != section_id] + [
                        entry
                    ]
                    section_list[:] = [e for e in section_list if e["id"] != section_id] + [
                        {
                            "id": section_id,
                            "kind": "wavefunction.gto",
                            "status": "ok",
                            "detail": "Session overlay",
                            "supported": True,
                        }
                    ]
                    state.sidebar_entries = sidebar_entries
                    state.section_list = section_list
                    state.dirty("sidebar_entries", "section_list")
                    state.relocalize_running = False
                    activate_section(section_id)
                    state.relocalize_status = (
                        f"{METHOD_LABELS[method]}: {len(result['occupations'])} localized orbitals"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if current():
                    state.relocalize_status = f"Failed: {exc}"
            finally:
                if generation == _relocalize_generation:
                    state.relocalize_running = False
                    state.flush()

        try:
            _relocalize_task = asyncio.get_running_loop().create_task(work())
        except RuntimeError:
            state.relocalize_running = False
            state.relocalize_status = "Start the viewer before localizing"

    @ctrl.set("toggle_color_by_charge")
    def toggle_color_by_charge(on: bool) -> None:
        server.state.color_by_charge = bool(on)
        if server.state.atom_properties_active:
            try:
                _render_atom_properties_overlay(reader, plotter, server.state)
            except Exception as e:  # noqa: BLE001 — keep UI state truthful
                server.state.atom_properties_active = False
                server.state.atom_properties_section_id = None
                server.state.status_message = f"Charge overlay error: {e}"

    @ctrl.set("trajectory_frame")
    def set_trajectory_frame(frame: int) -> None:
        state = server.state
        state.trajectory_frame = frame
        _update_trajectory_plot(reader, state)
        _render_trajectory_atoms(reader, plotter, state)
        _update_reaction_label(reader, state)

    @ctrl.set("trajectory_play_toggle")
    def toggle_play() -> None:
        state = server.state
        state.trajectory_playing = not state.trajectory_playing

    @ctrl.set("trajectory_step")
    def step_frame(delta: int) -> None:
        state = server.state
        n_frames = state.trajectory_n_frames
        if n_frames > 0:
            new_frame = state.trajectory_frame + delta
            if new_frame < 0:
                new_frame = n_frames - 1
            elif new_frame >= n_frames:
                new_frame = 0
            state.trajectory_frame = new_frame
            _update_trajectory_plot(reader, state)
            _render_trajectory_atoms(reader, plotter, state)
            _update_reaction_label(reader, state)

    # ── Animation loop (async, runs on Trame's event loop) ─────────
    _anim_task = None
    _anim_epoch = 0  # bumped on every play/stop; stale iterations bail (audit L3)

    @_watch_state("trajectory_playing")
    def _on_play_toggle(trajectory_playing: bool, **kwargs: Any) -> None:
        nonlocal _anim_task, _anim_epoch
        _anim_epoch += 1
        if trajectory_playing:
            my_epoch = _anim_epoch

            async def _loop() -> None:
                while server.state.trajectory_playing:
                    await asyncio.sleep(0.2)
                    # A section switch / reset can flip playing off and rebuild
                    # the scene during the sleep. `.cancel()` only fires at the
                    # next await, so without this guard the current iteration
                    # would render + flush one stale frame onto the reset scene
                    # (audit L3, one-frame flicker). Bail if superseded.
                    if my_epoch != _anim_epoch or not server.state.trajectory_playing:
                        return
                    frame = server.state.trajectory_frame
                    n = server.state.trajectory_n_frames or 1
                    server.state.trajectory_frame = (frame + 1) % n
                    _update_trajectory_plot(reader, server.state)
                    _render_trajectory_atoms(reader, plotter, server.state)
                    _update_reaction_label(reader, server.state)
                    server.state.flush()

            _anim_task = _spawn_task(_loop())
        else:
            if _anim_task is not None:
                _anim_task.cancel()
                _anim_task = None

    @ctrl.set("vibration_play_toggle")
    def vibration_play_toggle() -> None:
        server.state.vibration_playing = not server.state.vibration_playing

    _vib_anim_task = None
    _vib_epoch = 0  # bumped on every play/stop; stale iterations bail (audit L3)

    @_watch_state("vibration_playing")
    def _on_vibration_play_toggle(vibration_playing: bool, **kwargs: Any) -> None:
        nonlocal _vib_anim_task, _vib_epoch
        _vib_epoch += 1
        if vibration_playing:
            my_epoch = _vib_epoch

            async def _vib_loop() -> None:
                import math

                phase = 0.0
                while server.state.vibration_playing:
                    await asyncio.sleep(0.05)
                    # Bail if a section switch / reset superseded us during the
                    # sleep, before painting a stale frame (audit L3).
                    if my_epoch != _vib_epoch or not server.state.vibration_playing:
                        return
                    phase = (phase + 0.25) % (2 * math.pi)
                    server.state.vibration_phase = phase
                    _render_vibration_atoms(reader, plotter, server.state, phase=phase)
                    server.state.flush()

            _vib_anim_task = _spawn_task(_vib_loop())
        elif _vib_anim_task is not None:
            _vib_anim_task.cancel()
            _vib_anim_task = None
            # Snap back to phase 0 (rest position)
            server.state.vibration_phase = 0.0
            _render_vibration_atoms(reader, plotter, server.state, phase=0.0)

    @ctrl.set("vibration_changed")
    def on_vibration_changed(mode: int, amplitude: float) -> None:
        state = server.state
        state.vibration_mode = mode
        state.vibration_amplitude = amplitude
        _render_vibration_atoms(reader, plotter, state)

    @ctrl.set("render_mo")
    def render_mo(mo_key: Any, _expected_epoch: int | None = None) -> None:
        state = server.state
        section_id = state.wf_section_id
        if not section_id:
            if _expected_epoch is None:
                state.status_message = "No wavefunction section active."
            return
        source_reader = reader
        component = str(state.wf_component or "real")
        with _wf_epoch_lock:
            render_epoch = (
                _wf_render_epoch
                if _expected_epoch is None
                else int(_expected_epoch)
            )
        # mo_key is the composite "{spin}:{index}" from the picker. Fall
        # back gracefully to a bare integer (restricted) for robustness.
        spin, index = _parse_mo_key(mo_key)
        with _wf_render_lock:
            if not _wavefunction_render_is_current(
                render_epoch, section_id, source_reader
            ):
                return
            try:
                section = source_reader.get_section(section_id)
                message = _render_mo_volume(
                    source_reader,
                    plotter,
                    viewer_state,
                    state,
                    section,
                    index,
                    spin,
                    update_state=False,
                    component=component,
                )
            except Exception as e:  # noqa: BLE001
                with _wf_epoch_lock:
                    if _wavefunction_render_is_current(
                        render_epoch, section_id, source_reader
                    ):
                        state.wf_animating = False
                        _fail_wavefunction_surface(
                            plotter, state, f"MO render error: {e}"
                        )
                    else:
                        _remove_actors_by_prefix(plotter, _Actors.MO_SURFACE)
                return

            # The section/file may have changed while the grid was evaluated.
            # Helpers create server actors but no longer push them, so a stale
            # render can be removed here without ever reaching the client.
            with _wf_epoch_lock:
                if not _wavefunction_render_is_current(
                    render_epoch, section_id, source_reader
                ):
                    _remove_actors_by_prefix(plotter, _Actors.MO_SURFACE)
                    return
                _finalize_wavefunction_surface_appearance(plotter, state)
                # Store the recipe only at the same guarded commit boundary as
                # the final client push.
                state.wf_selected_spin = spin
                state.mo_last_spin = spin
                state.mo_last_index = index
                state.mo_last_component = component
                state.wf_surface_kind = "mo"
                state.mo_visible = True
                state.volume_loaded = True
                if message is not None:
                    state.status_message = message

    @ctrl.set("toggle_mo_visibility")
    def toggle_mo_visibility(visible: bool) -> None:
        state = server.state
        if not bool(visible):
            state.mo_visible = False
            _invalidate_wavefunction_renders()
            with _wf_render_lock:
                _remove_actors_by_prefix(plotter, _Actors.MO_SURFACE)
                _push_view(plotter)
            state.status_message = "Computed isosurface hidden"
        else:
            render_epoch = _invalidate_wavefunction_renders()
            source_reader = reader
            section_id = state.wf_section_id
            with _wf_render_lock:
                if not _wavefunction_render_is_current(
                    render_epoch, section_id, source_reader
                ):
                    return
                try:
                    restored = _replay_wavefunction_surface(
                        source_reader, plotter, viewer_state, state
                    )
                except Exception as e:  # noqa: BLE001
                    restored = False
                    message = f"Isosurface re-render error: {e}"
                else:
                    message = "No computed isosurface is available to show"

                # Replays create actors but do not push. Commit visibility and
                # the one client update only while the reader/section epoch is
                # still current; otherwise discard the unseen stale actors.
                with _wf_epoch_lock:
                    if not _wavefunction_render_is_current(
                        render_epoch, section_id, source_reader
                    ):
                        _remove_actors_by_prefix(plotter, _Actors.MO_SURFACE)
                        return
                    if restored:
                        _finalize_wavefunction_surface_appearance(plotter, state)
                        state.mo_visible = True
                        state.volume_loaded = True
                    else:
                        _fail_wavefunction_surface(plotter, state, message)

    @ctrl.set("render_frontier")
    def render_frontier(which: str) -> None:
        """One-click HOMO/LUMO render: pick the marked row and render it."""
        which = _first(which)
        state = server.state
        marker = str(which).upper()
        row = _frontier_row(state.wf_mo_rows, marker)
        if row is None:
            state.status_message = f"No {marker} in this wavefunction's MO list"
            return
        state.wf_selected_mo = row["value"]
        render_mo(row["value"])

    @_watch_state("mo_click_request")
    def _on_mo_click_request(mo_click_request: Any = None, **_kwargs: Any) -> None:
        """Render the MO clicked in the orbital energy diagram.

        The value is ``"{spin}:{index}@{nonce}"``; the nonce only forces the
        change event on repeat clicks and is stripped before dispatch.
        """
        if not mo_click_request:
            return
        key = str(mo_click_request).split("@", 1)[0]
        if not key:
            return
        server.state.wf_selected_mo = key
        render_mo(key)

    @ctrl.set("step_mo")
    def step_mo(direction: int) -> None:
        """Move to the previous (-1) or next (+1) MO in the picker."""
        _step_mo_selection(server.state, direction)

    @ctrl.set("step_mo_render")
    def step_mo_render(direction: int) -> None:
        """Step to the prev/next MO and immediately render it."""
        step_mo(direction)
        render_mo(server.state.wf_selected_mo)

    @ctrl.set("toggle_mo_animation")
    def toggle_mo_animation() -> None:
        """Start/stop auto-advancing through MOs."""
        state = server.state
        if state.wf_animating:
            stop_epoch = _invalidate_wavefunction_renders()
            source_reader = reader
            section_id = state.wf_section_id
            with _wf_render_lock:
                if not _wavefunction_render_is_current(
                    stop_epoch, section_id, source_reader
                ):
                    return

                has_surface_actor = any(
                    str(name).startswith("mo_iso_")
                    for name in plotter.actors
                )
                restored = True
                message = None
                if state.mo_visible and not has_surface_actor:
                    # A canceled worker may have removed the last committed
                    # actor before noticing its stale epoch. Recreate that
                    # committed recipe so the stopped/visible flags remain
                    # truthful on both server and client.
                    if not state.wf_surface_kind:
                        restored = False
                        message = "Animation stopped; no surface recipe is available"
                    else:
                        try:
                            restored = _replay_wavefunction_surface(
                                source_reader, plotter, viewer_state, state
                            )
                        except Exception as e:  # noqa: BLE001
                            restored = False
                            message = f"Animation stop restore error: {e}"
                        else:
                            if not restored:
                                message = (
                                    "Animation stopped; surface could not be restored"
                                )

                with _wf_epoch_lock:
                    if not _wavefunction_render_is_current(
                        stop_epoch, section_id, source_reader
                    ):
                        _remove_actors_by_prefix(plotter, _Actors.MO_SURFACE)
                        return
                    if restored:
                        if not has_surface_actor and state.mo_visible:
                            _finalize_wavefunction_surface_appearance(plotter, state)
                        state.status_message = "MO animation stopped"
                    else:
                        _fail_wavefunction_surface(plotter, state, message or "")
            return

        epoch = _invalidate_wavefunction_renders()
        with _wf_epoch_lock:
            state.wf_animating = True
        state.status_message = "MO animation started"
        _run_mo_animation_step(epoch, initial=True)

    def _run_mo_animation_step(epoch: int, *, initial: bool = False) -> None:
        """Render one guarded animation frame, then schedule its successor."""
        with _wf_epoch_lock:
            if epoch != _wf_render_epoch or not server.state.wf_animating:
                return
            # Keep invalidation out of the gap between checking the epoch and
            # advancing the picker. Otherwise an old timer can step the newly
            # activated wavefunction even though its render is rejected.
            previous_key = server.state.wf_selected_mo
            mo_key = _step_mo_selection(server.state, 1)
        # Starting at the final (or only) orbital still renders it once.
        # Subsequent ticks stop at that boundary instead of evaluating forever.
        if mo_key is None or (mo_key == previous_key and not initial):
            with _wf_epoch_lock:
                if epoch == _wf_render_epoch:
                    server.state.wf_animating = False
                    server.state.status_message = (
                        "MO animation stopped: no orbitals"
                        if mo_key is None else "MO animation finished"
                    )
                    server.state.flush()
            return
        render_mo(mo_key, epoch)
        with _wf_epoch_lock:
            if epoch == _wf_render_epoch:
                server.state.flush()
        _schedule_mo_step(epoch)

    def _schedule_mo_step(epoch: int) -> None:
        """Keep scheduled VTK work and state updates on the server event loop."""
        nonlocal _wf_anim_timer
        state = server.state
        with _wf_epoch_lock:
            if epoch != _wf_render_epoch or not state.wf_animating:
                return
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                state.wf_animating = False
                state.status_message = "MO animation unavailable: no running event loop"
                state.flush()
                return
            _wf_anim_timer = loop.call_later(
                float(state.wf_anim_speed), _run_mo_animation_step, epoch
            )

    @ctrl.set("show_energy_diagram")
    def show_energy_diagram() -> None:
        """Render the orbital energy-level diagram in the bottom panel."""
        state = server.state
        section_id = state.wf_section_id
        if not section_id:
            return
        from vibeview.renderers.wavefunction import WavefunctionRenderer

        section = reader.get_section(section_id)
        renderer = WavefunctionRenderer(section, reader)
        try:
            # Chart channel: the diagram is scripted (clicking a level
            # posts a message back to render that orbital).
            state.chart_html = renderer.render_energy_diagram(energy_unit=state.energy_unit)
            state.chart_title = "Orbital Energies"
            state.status_message = "Energy diagram rendered."
        except Exception as e:  # noqa: BLE001
            state.status_message = f"Diagram error: {e}"

    @ctrl.set("compute_wf_density")
    def compute_wf_density(spin: bool = False) -> None:
        """Compute + render the total density (Σ occ_i |ψ_i|²) or, with
        ``spin=True``, the spin density ρ_α − ρ_β from the active
        wavefunction.gto section."""
        requested_spin = bool(spin)
        _compute_wavefunction_surface(
            "spin_density" if requested_spin else "density",
            _render_wf_density,
            "Density",
            density_spin=requested_spin,
        )

    def _compute_wavefunction_surface(
        surface_kind: str,
        render_surface: Callable[..., Any],
        error_label: str,
        *,
        density_spin: bool | None = None,
    ) -> None:
        """Run one computed-surface action as a guarded render transaction."""
        state = server.state
        previous_spin = bool(state.wf_density_spin)
        if density_spin is not None:
            state.wf_density_spin = density_spin
        sid = state.wf_section_id
        if not sid:
            state.wf_density_spin = previous_spin
            state.status_message = "No wavefunction section active."
            return
        render_epoch = _invalidate_wavefunction_renders()
        source_reader = reader
        with _wf_render_lock:
            if not _wavefunction_render_is_current(
                render_epoch, sid, source_reader
            ):
                return
            try:
                message = render_surface(
                    source_reader,
                    plotter,
                    viewer_state,
                    state,
                    source_reader.get_section(sid),
                    update_state=False,
                )
            except Exception as e:  # noqa: BLE001
                with _wf_epoch_lock:
                    if _wavefunction_render_is_current(
                        render_epoch, sid, source_reader
                    ):
                        state.wf_density_spin = previous_spin
                        _fail_wavefunction_surface(
                            plotter, state, f"{error_label} compute error: {e}"
                        )
                    else:
                        _remove_actors_by_prefix(plotter, _Actors.MO_SURFACE)
                return

            # Section/file switches invalidate immediately while waiting for
            # the render lock. Keep stale helper-created actors server-side
            # only, then remove them without ever pushing to the client.
            with _wf_epoch_lock:
                if not _wavefunction_render_is_current(
                    render_epoch, sid, source_reader
                ):
                    _remove_actors_by_prefix(plotter, _Actors.MO_SURFACE)
                    return
                _finalize_wavefunction_surface_appearance(plotter, state)
                state.wf_surface_kind = surface_kind
                state.mo_visible = True
                state.volume_loaded = True
                if message is not None:
                    state.status_message = message

    @ctrl.set("compute_wf_elf")
    def compute_wf_elf() -> None:
        """Compute + render the electron localization function from the
        active wavefunction.gto section (Becke & Edgecombe 1990)."""
        _compute_wavefunction_surface("elf", _render_wf_elf, "ELF")

    @ctrl.set("compute_wf_nci")
    def compute_wf_nci() -> None:
        """Compute + render the NCI reduced-gradient isosurface, coloured by
        sign(lambda2)*rho (Johnson et al. 2010)."""
        _compute_wavefunction_surface("nci", _render_wf_nci, "NCI")

    @ctrl.set("compute_wf_laplacian")
    def compute_wf_laplacian() -> None:
        """Compute + render del^2 rho from the active wavefunction section."""
        _compute_wavefunction_surface(
            "laplacian", _render_wf_laplacian, "Laplacian"
        )

    @ctrl.set("apply_bookmark")
    def apply_bookmark(name: str) -> None:
        for bm in viewer_state.bookmarks:
            if bm.name == name:
                _apply_camera(plotter, bm.camera)
                _sync_orthographic_switch(plotter, server.state)
                plotter.render()
                server.state.status_message = f"Camera → {name}"
                return
        server.state.status_message = f"Unknown bookmark: {name}"

    @ctrl.set("update_crossfade")
    def update_crossfade(blend: float) -> None:
        state = server.state
        state.crossfade_blend = blend
        viewer_state.crossfade_blend = blend
        if viewer_state.crossfade_volumes:
            va, vb = viewer_state.crossfade_volumes
            # Build a blended view: rebuild both volumes with blend opacity
            _rebuild_crossfade(reader, plotter, viewer_state, state, va, vb, blend)

    @ctrl.set("sync_client_camera")
    def sync_client_camera(
        position: Any = None,
        focal_point: Any = None,
        view_up: Any = None,
        parallel_scale: Any = None,
    ) -> None:
        """Copy the client's camera onto the server plotter after a drag.

        VtkLocalView renders client-side and the client owns the camera;
        ``push_camera`` only goes the other way. Without this, everything
        that reads ``plotter`` for "the current view" saw whatever the last
        server-side ``reset_camera`` left — measured: rotating until 18% of
        the viewport's pixels changed, then saving two bookmarks either
        side of the rotation, produced byte-identical cameras.

        Fires once per interaction end, not per mouse move, so writing the
        plotter here is cheap. Kept deliberately tolerant: a malformed
        payload must never break interaction, and the previous camera is a
        safe thing to keep.
        """
        try:
            pos = [float(v) for v in position]
            foc = [float(v) for v in focal_point]
            up = [float(v) for v in view_up]
        except (TypeError, ValueError):
            return
        if not (len(pos) == len(foc) == len(up) == 3):
            return
        plotter.camera_position = [pos, foc, up]
        # Labels are polygonal text, so facing the viewer is geometry, not a
        # render flag: they have to be rebuilt when the viewpoint moves
        # (roadmap D5). Only the label actors are re-meshed, and only once
        # per interaction end.
        from vibeview.renderers.structure import reorient_labels

        if reorient_labels(plotter):
            _push_view(plotter)
        if parallel_scale is not None:
            # A junk scale must not discard an otherwise good pose.
            with contextlib.suppress(TypeError, ValueError):
                plotter.camera.parallel_scale = float(parallel_scale)

    @ctrl.set("save_user_bookmark")
    def save_user_bookmark(name: str) -> None:
        state = server.state
        if not name or not name.strip():
            state.status_message = "Bookmark name required"
            return
        name = name.strip()
        cam = _camera_to_dict(plotter)
        bm = {
            "name": name,
            "camera": cam,
            # The open section is `selected_section`; `active_section` is not a
            # declared state key, so this recorded "" in every bookmark — which
            # also starved the presentation slides that read section_id.
            "section_id": state.selected_section or "",
            "isovalue": state.isovalue,
            "colormap": state.colormap,
            "opacity": state.opacity,
            **_saved_view_extras(),
        }
        existing = [i for i, b in enumerate(state.user_bookmarks) if b["name"] == name]
        if existing:
            state.user_bookmarks[existing[0]] = bm
        else:
            state.user_bookmarks.append(bm)
        # Both branches mutate the list in place, which trame cannot see:
        # the snapshot it pushed to the client aliases this same list, so
        # nothing is dirty and the client's copy stays at whatever it was
        # (measured: names showed ['before', 'after'] while the client's
        # user_bookmarks was still []). `user_bookmark_names` below is a
        # fresh assignment, which is why the dropdown looked correct and
        # hid this. Every read of user_bookmarks is server-side today, so
        # there is no visible symptom yet — but this is the same defect
        # class as the edit_history/edit_future stacks, and the next
        # client-side read would inherit it silently.
        state.dirty("user_bookmarks")
        state.user_bookmark_names = [b["name"] for b in state.user_bookmarks]
        state.session_dirty = True
        state.status_message = f"Bookmark saved: {name}"

    @ctrl.set("apply_user_bookmark")
    def apply_user_bookmark(name: str) -> None:
        state = server.state
        for bm in state.user_bookmarks:
            if bm["name"] == name:
                _restore_view_appearance(bm)
                section_id, _section = _prepare_saved_view_restore(bm)
                if section_id:
                    state.selected_section = section_id
                    activate_section(section_id)
                    _restore_computed_surface(bm)
                # Section activation may rebuild the scene or reset its
                # camera, so the saved pose must be the final render change.
                if _apply_saved_view_camera(bm):
                    _push_view(plotter)
                state.status_message = f"Restored: {name}"
                return
        state.status_message = f"Unknown bookmark: {name}"

    _presentation_auto_task: asyncio.Task[Any] | None = None
    _presentation_auto_epoch = 0

    def _stop_presentation_auto_advance() -> None:
        """Cancel presentation playback without leaving a stale iteration."""
        nonlocal _presentation_auto_task, _presentation_auto_epoch
        _presentation_auto_epoch += 1
        if _presentation_auto_task is not None:
            _presentation_auto_task.cancel()
            _presentation_auto_task = None
        server.state.presentation_auto_advance = False

    def _presentation_delay_seconds() -> float:
        try:
            return max(0.1, float(server.state.presentation_slide_duration or 5))
        except (TypeError, ValueError):
            return 5.0

    # @ctrl.set alone is not client-callable; the keyboard-shortcut JS
    # invokes trame.trigger("toggle_presentation"), which needs a trigger registration.
    @ctrl.trigger("toggle_presentation")
    @ctrl.set("toggle_presentation")
    def toggle_presentation() -> None:
        """Enter/exit fullscreen presentation mode using bookmarks as slides."""
        state = server.state
        if not state.presentation_mode:
            if not state.user_bookmarks:
                state.status_message = "Create bookmarks first to use as slides"
                return
            # Presentation has no edit chrome. Leave edit mode through its
            # normal cleanup path so selections, highlights, constraints, and
            # pending live optimization cannot mutate a hidden structure.
            if state.edit_mode:
                toggle_edit_mode()
            _stop_presentation_auto_advance()
            state.presentation_mode = True
            # Preserve the complete bookmark record. Rebuilding a narrower
            # dict used to discard the saved section and render settings, so
            # every slide showed whichever section happened to be open.
            state.presentation_slides = [dict(bookmark) for bookmark in state.user_bookmarks]
            state.presentation_slide = 0
            if state.presentation_slides:
                _apply_slide(state.presentation_slides[0])
            state.status_message = (
                f"Presentation mode: {len(state.presentation_slides)} slides. "
                "Arrow keys to navigate."
            )
            _push_view(plotter)
        else:
            _stop_presentation_auto_advance()
            state.presentation_mode = False
            state.status_message = "Presentation mode exited"

    def _saved_view_setting(saved_view: dict[str, Any], name: str) -> Any:
        """Read a saved visual setting, including the legacy slide shape."""
        if name in saved_view:
            return saved_view[name]
        legacy_state = saved_view.get("state")
        if isinstance(legacy_state, dict):
            return legacy_state.get(name)
        return None

    def _prepare_saved_view_restore(
        saved_view: dict[str, Any],
    ) -> tuple[str | None, Section | None]:
        """Restore controls and volume hints before section activation.

        Volume activation treats ``ViewerState`` hints as authoritative and
        copies them back to the UI while rebuilding the mesh. Updating only
        Trame state, or doing so after activation, creates a split view where
        the controls claim saved values but the live actor still uses stale
        ones. Bookmarks, sessions, and presentation slides all pass through
        this helper so their restore order cannot drift again.
        """
        state = server.state
        saved_isovalue = _saved_view_setting(saved_view, "isovalue")
        saved_colormap = _saved_view_setting(saved_view, "colormap")
        saved_opacity = _saved_view_setting(saved_view, "opacity")
        restored_isovalue = None
        restored_colormap = None
        restored_opacity = None
        if saved_isovalue is not None:
            with contextlib.suppress(TypeError, ValueError):
                candidate = float(saved_isovalue)
                if math.isfinite(candidate) and candidate > 0:
                    restored_isovalue = candidate
                    state.isovalue = restored_isovalue
        if saved_opacity is not None:
            with contextlib.suppress(TypeError, ValueError):
                candidate = float(saved_opacity)
                if math.isfinite(candidate) and 0 <= candidate <= 1:
                    restored_opacity = candidate
                    state.opacity = restored_opacity
        if saved_colormap is not None:
            colormap_name = str(saved_colormap)
            with contextlib.suppress(ImportError, TypeError, ValueError):
                get_cmap_safe(cmap_argument(colormap_name))
                restored_colormap = colormap_name
                state.colormap = restored_colormap

        legacy_state = saved_view.get("state")
        section_id = saved_view.get("section_id") or saved_view.get("active_section")
        if not section_id and isinstance(legacy_state, dict):
            section_id = legacy_state.get("section_id") or legacy_state.get(
                "active_section"
            )
        section = next(
            (item for item in reader.sections if item.id == section_id),
            None,
        )
        if section is not None and (
            section.kind.startswith("volume.") or section.kind == "basis.ao"
        ):
            hints = viewer_state.get_volume_hints(section.id, kind=section.kind)
            if restored_isovalue is not None:
                hints.isovalue = restored_isovalue
            if restored_opacity is not None:
                hints.opacity = restored_opacity
            if restored_colormap is not None:
                hints.colormap = restored_colormap
        return section_id, section

    def _apply_saved_view_camera(saved_view: dict[str, Any]) -> bool:
        """Apply a saved camera after any section rebuild; report success."""
        camera = saved_view.get("camera")
        if not camera:
            return False
        try:
            _apply_camera(plotter, camera)
            _sync_orthographic_switch(plotter, server.state)
            plotter.render()
        except Exception:  # noqa: BLE001 - malformed saved views stay recoverable
            return False
        return True

    def _apply_slide(slide: dict[str, Any]) -> None:
        """Apply a presentation slide: restore camera and section selection."""
        state = server.state
        _restore_view_appearance(slide)
        section_id, section = _prepare_saved_view_restore(slide)
        if section_id:
            state.presentation_panel_slide = bool(
                section is not None
                and (
                    section.kind in _two_d_panel_kinds()
                    or section.kind in {"scan.surface", "reaction.waypoints"}
                )
            )
            state.selected_section = section_id
            # Trigger section activation so volume/isosurface/etc. reappear.
            activate_section(section_id)
            _restore_computed_surface(slide)
        else:
            state.presentation_panel_slide = False

        # Apply the camera last so any section rebuild cannot replace the
        # bookmarked pose before the client receives it.
        _apply_saved_view_camera(slide)
        _push_view(plotter)

    # These named triggers are required by the global ArrowLeft/ArrowRight
    # handler. A plain @ctrl.set controller is callable by Python widgets but
    # is absent from the client trigger registry.
    @ctrl.trigger("presentation_next")
    @ctrl.set("presentation_next")
    def presentation_next() -> None:
        """Advance to the next slide."""
        state = server.state
        if not state.presentation_mode or not state.presentation_slides:
            return
        state.presentation_slide = (state.presentation_slide + 1) % len(
            state.presentation_slides
        )
        _apply_slide(state.presentation_slides[state.presentation_slide])
        state.status_message = (
            f"Slide {state.presentation_slide + 1}/{len(state.presentation_slides)}"
        )

    @ctrl.trigger("presentation_prev")
    @ctrl.set("presentation_prev")
    def presentation_prev() -> None:
        """Go to previous slide."""
        state = server.state
        if not state.presentation_mode or not state.presentation_slides:
            return
        state.presentation_slide = (state.presentation_slide - 1) % len(
            state.presentation_slides
        )
        _apply_slide(state.presentation_slides[state.presentation_slide])
        state.status_message = (
            f"Slide {state.presentation_slide + 1}/{len(state.presentation_slides)}"
        )

    @ctrl.set("toggle_presentation_auto_advance")
    def toggle_presentation_auto_advance() -> None:
        """Start or stop timed cyclic slide advancement."""
        nonlocal _presentation_auto_task, _presentation_auto_epoch
        state = server.state
        if state.presentation_auto_advance:
            _stop_presentation_auto_advance()
            state.status_message = "Automatic slide advance paused"
            return
        _stop_presentation_auto_advance()
        if not state.presentation_mode or len(state.presentation_slides) < 2:
            state.status_message = "Automatic advance needs at least two slides"
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            state.status_message = "Automatic slide advance needs the running viewer"
            return

        state.presentation_auto_advance = True
        _presentation_auto_epoch += 1
        my_epoch = _presentation_auto_epoch

        async def _loop() -> None:
            nonlocal _presentation_auto_task
            try:
                while state.presentation_mode and state.presentation_auto_advance:
                    await asyncio.sleep(_presentation_delay_seconds())
                    if (
                        my_epoch != _presentation_auto_epoch
                        or not state.presentation_mode
                        or not state.presentation_auto_advance
                    ):
                        return
                    presentation_next()
                    state.flush()
            finally:
                if my_epoch == _presentation_auto_epoch:
                    _presentation_auto_task = None
                    state.presentation_auto_advance = False
                    with contextlib.suppress(Exception):
                        state.flush()

        _presentation_auto_task = loop.create_task(_loop())
        state.status_message = (
            f"Automatic slide advance every {_presentation_delay_seconds():g} s"
        )

    def _session_payload() -> dict[str, Any]:
        """The on-disk session document. Single source of truth.

        Both the explicit save and the auto-save write through this. They used
        to build their own dicts: the auto-save emitted ``version: "2.0"`` with
        differently-named fields, while ``load_session`` accepts only
        ``version == 1``. Since the auto-save loop rewrites the *same*
        ``session_path``, an explicitly saved session was silently replaced
        within 60 s by a document the loader then refused ("Bad version") —
        and the fields the auto-save omitted (replication, library_favorites)
        were dropped with it. Keeping one builder makes that drift impossible.
        """
        state = server.state
        return {
            "version": 1,
            # The open section lives in `selected_section`; `active_section` is
            # never declared in the state defaults, so reading it here recorded
            # null. Field name kept for compatibility with existing session
            # files and the bookmark/presentation readers. Session loading
            # routes it through the same guarded activation as interactive
            # section changes.
            "active_section": state.selected_section,
            "isovalue": state.isovalue,
            "colormap": state.colormap,
            "opacity": state.opacity,
            "camera": _camera_to_dict(plotter),
            # Read the declared per-axis keys. `state.replication` is not a
            # state key at all, and a trame state returns None for an
            # undeclared name rather than raising — so getattr's [1, 1, 1]
            # default never applied and list(None) raised TypeError, taking
            # down every session save and autosave with it.
            "replication": [
                int(state.replication_nx or 1),
                int(state.replication_ny or 1),
                int(state.replication_nz or 1),
            ],
            "user_bookmarks": state.user_bookmarks,
            # Pinned library structures are a deliberate choice, so they
            # persist with the session (recents are implicit, and don't).
            "library_favorites": list(state.library_favorites or []),
            **_saved_view_extras(),
        }

    def _saved_view_extras() -> dict[str, Any]:
        """View fields beyond camera/section/volume hints (#37-16).

        Bookmarks, slides and sessions kept only the camera, the section and
        the volume hints, so restoring one dropped the representation, the
        material and any computed wavefunction surface (the HOMO you saved
        came back as a bare structure).
        """
        state = server.state
        extras: dict[str, Any] = {
            "representation_style": state.representation_style,
            "material_preset": state.material_preset,
        }
        kind = state.wf_surface_kind
        if kind and state.mo_visible:
            surface: dict[str, Any] = {"kind": str(kind)}
            if kind == "mo":
                surface.update(
                    {
                        "index": state.mo_last_index,
                        "spin": state.mo_last_spin,
                        "component": state.mo_last_component,
                    }
                )
            extras["surface"] = surface
        return extras

    def _restore_view_appearance(saved_view: dict[str, Any]) -> None:
        """Restore representation and material before the section rebuild."""
        state = server.state
        style = saved_view.get("representation_style")
        if isinstance(style, str) and style != state.representation_style:
            set_representation_style(style)
        preset = saved_view.get("material_preset")
        if isinstance(preset, str) and preset != state.material_preset:
            from vibeview.material_presets import MATERIAL_PRESETS

            if preset in MATERIAL_PRESETS:
                set_material_preset(preset)

    def _restore_computed_surface(saved_view: dict[str, Any]) -> None:
        """Replay a saved computed surface after its section is active."""
        surface = saved_view.get("surface")
        state = server.state
        if not isinstance(surface, dict) or not state.wf_section_id:
            return
        kind = surface.get("kind")
        if kind not in ("mo", "density", "spin_density", "elf", "nci", "laplacian"):
            return
        if kind == "mo":
            raw_index = surface.get("index")
            if isinstance(raw_index, bool) or not isinstance(raw_index, (int, float, str)):
                return
            try:
                index = int(raw_index)
            except ValueError:
                return
            state.mo_last_index = index
            state.mo_last_spin = str(surface.get("spin") or "restricted")
            state.mo_last_component = str(surface.get("component") or "real")
        state.wf_surface_kind = kind
        try:
            with _wf_render_lock:
                restored = _replay_wavefunction_surface(reader, plotter, viewer_state, state)
                if restored:
                    _finalize_wavefunction_surface_appearance(plotter, state)
        except Exception as e:  # noqa: BLE001 - a saved view stays recoverable
            restored = False
            state.status_message = f"Saved surface could not be restored: {e}"
        if restored:
            state.mo_visible = True
            state.volume_loaded = True
            _push_view(plotter)
        else:
            state.wf_surface_kind = None

    def _saved_session_replication(session: dict[str, Any]) -> tuple[int, int, int] | None:
        """Return a complete integral replication triple, or ignore it safely."""
        saved = session.get("replication")
        if not isinstance(saved, (list, tuple)) or len(saved) != 3:
            return None

        restored: list[int] = []
        for value in saved:
            if isinstance(value, bool):
                return None
            try:
                candidate = float(value)
            except (OverflowError, TypeError, ValueError):
                return None
            if not math.isfinite(candidate) or not candidate.is_integer():
                return None
            restored.append(int(candidate))
        return restored[0], restored[1], restored[2]

    def _restore_session_replication(session: dict[str, Any]) -> None:
        """Synchronize a saved supercell before activating its target section."""
        saved = _saved_session_replication(session)
        if saved is None:
            return

        state = server.state
        target = clamp_replication(
            saved,
            (bool(state.pbc_a), bool(state.pbc_b), bool(state.pbc_c)),
        )
        try:
            current_ui = (
                int(state.replication_nx),
                int(state.replication_ny),
                int(state.replication_nz),
            )
            current_render = tuple(int(value) for value in viewer_state.replication)
        except (OverflowError, TypeError, ValueError):
            current_ui = None
            current_render = None
        if current_ui == target and current_render == target:
            return

        # Reuse the interactive path so periodic-axis clamping, animation
        # cancellation, ViewerState synchronization, and the rendered
        # supercell cannot drift between session and UI restores.
        update_replication(*target)

    def _default_session_path() -> Path:
        """Where a session lives when the user names no path.

        One location for the explicit save, the load button and the auto-save:
        ``<stem>.vibe-view-session.json`` beside the archive (what the manual
        promises), or the cache's auto-session for an in-memory structure. The
        explicit save used to write ``session.vibe-session`` into the process
        cwd while the auto-save wrote beside the archive (#37-2).
        """
        if reader.path:
            return Path(reader.path.parent) / f"{reader.path.stem}.vibe-view-session.json"
        from vibeview.paths import auto_session_path

        return auto_session_path()

    @ctrl.set("save_session")
    def save_session(path: str | None = None) -> None:
        import json as _json
        from pathlib import Path as _Path  # not in scope here; NameError without it

        state = server.state
        # state.session_path is a str, so the old `Path(path) if path else
        # state.session_path` left sp a plain string on the no-argument call
        # (the Save Session button) and sp.write_text() below would fail.
        sp = _Path(path or state.session_path or _default_session_path())
        session = _session_payload()
        sp.write_text(_json.dumps(session, indent=2))
        state.session_path = str(sp)
        state.session_dirty = False
        state.status_message = f"Session saved: {sp}"

    @ctrl.set("load_session")
    def load_session(path: str) -> None:
        import json as _json
        from pathlib import Path as _Path  # not in scope here; NameError without it

        state = server.state
        # The Load Session button sends '' (the path lives in the text field
        # bound to session_path); Path('') is '.', a directory, which read as
        # "Load error: [Errno 21] Is a directory" (#37-1).
        path = _first(path, "")
        path = str(path or state.session_path or _default_session_path())
        sp = _Path(path)
        if not sp.exists():
            state.status_message = f"Not found: {path}"
            return
        if not sp.is_file():
            state.status_message = f"Not a session file: {path}"
            return
        try:
            session = _json.loads(sp.read_text())
        except Exception as e:
            state.status_message = f"Load error: {e}"
            return
        if session.get("version") != 1:
            state.status_message = "Bad version"
            return
        _restore_view_appearance(session)
        section_id, _section = _prepare_saved_view_restore(session)
        # Replication rebuilds the base scene. Apply it before activating the
        # saved section so specialized Fermi, animation, and overlay renderers
        # are not immediately erased, and volume activation sees the target
        # ViewerState replication from its first mesh build.
        _restore_session_replication(session)
        if section_id:
            # Field name kept for existing session files; the state key that
            # drives the UI is selected_section, and activation is what
            # actually re-opens the section.
            state.selected_section = section_id
            activate_section(section_id)
            _restore_computed_surface(session)
        # A section rebuild may reset its camera (for example reciprocal-space
        # surfaces), and replication resets the client camera to fit the grown
        # scene, so the saved pose is deliberately applied last.
        if _apply_saved_view_camera(session):
            _push_view(plotter)
        state.user_bookmarks = session.get("user_bookmarks", [])
        state.library_favorites = session.get("library_favorites", [])
        state.user_bookmark_names = [b["name"] for b in state.user_bookmarks]
        state.session_path = str(sp)
        state.session_dirty = False
        state.status_message = f"Session loaded: {path}"

    # ── Controller: screenshot export ─────────────────────────────────
    # @ctrl.set alone is not client-callable; the keyboard-shortcut JS
    # invokes trame.trigger("save_screenshot"), which needs a trigger registration.
    @ctrl.trigger("save_screenshot")
    @ctrl.set("save_screenshot")
    def save_screenshot() -> None:
        """Capture viewport with 2x supersampling anti-aliasing.

        Renders at double the target resolution, then Lanczos-
        downsamples for publication-quality output."""
        import io

        import numpy as _np
        from PIL import Image as _Image
        from PIL import ImageFilter as _ImageFilter

        scale = max(1, min(4, int(server.state.screenshot_scale or 2)))
        transparent = bool(server.state.screenshot_transparent)
        server.state.screenshot_dialog = False

        win_size = plotter.window_size
        orig_bg = plotter.background_color
        target_w = win_size[0] * scale
        target_h = win_size[1] * scale
        ssaa = 2  # supersample 2x for anti-aliasing
        try:
            if transparent:
                set_background(plotter, "white")
            # plotter.screenshot(return_img=True) returns a NumPy (H, W, 3|4)
            # array, NOT a PIL Image — wrap it before using PIL methods.
            arr = plotter.screenshot(
                return_img=True,
                window_size=(target_w * ssaa, target_h * ssaa),
            )
            img = _Image.fromarray(_np.asarray(arr))
            # Lanczos downscale + unsharp mask for crisp edges.
            img = img.resize((target_w, target_h), _Image.Resampling.LANCZOS)
            img = img.filter(_ImageFilter.UnsharpMask(radius=0.5, percent=60, threshold=2))
            if transparent:
                # Make white background transparent.
                img = _white_to_transparent(img)
        finally:
            if transparent:
                set_background(plotter, orig_bg)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        png_bytes = buf.getvalue()
        b64 = base64.b64encode(png_bytes).decode()
        server.state.screenshot_data = f"data:image/png;base64,{b64}"
        server.state.screenshot_ready = True

        _save_screenshot_disk(png_bytes, reader)

        server.state.status_message = f"Screenshot captured ({target_w}\u00d7{target_h}, SSAA 2x)"
        _record_download("Screenshot", "PNG")
        # Reset ready flag after a short delay so next click works
        _spawn_task(_reset_screenshot_flag())

    @ctrl.set("apply_view_preset")
    def apply_view_preset(name: Any) -> None:
        """One-click view presets (design refresh 2026, items 3+6): each sets
        representation, material (incl. background), SSAO, and atom labels
        together. Order matters — the label/representation rebuilds recreate
        the actors, so the material is applied after the last rebuild and the
        SSAO pass is (re)configured last.
        """
        name = _first(name)
        # A preset styles the scene; it does not decide that a protein
        # should be drawn atom-by-atom. Fall back to whatever this file
        # opens in, so applying a preset to a biomolecule keeps the
        # ribbon instead of forcing a 158 s bond inference.
        rep_default = _default_representation(reader)
        presets = {
            # White background, publication-grade material, no chrome.
            "publication": (rep_default, "scientific", False, False),
            # Dark, glossy, depth-cued — for talks and screenshots.
            "presentation": (rep_default, "cpk_glossy", True, False),
            # Flat material + atom labels — for working analysis.
            "analysis": (rep_default, "matte", False, True),
        }
        p = presets.get(str(name))
        if p is None:
            return  # cleared selection — leave the scene as-is
        rep, mat, ssao, labels = p
        # Set the whole look, then rebuild once: the rebuild applies the
        # SSAO pass and the material and pushes the view. Chaining the four
        # controllers rebuilt the scene twice and pushed it five times
        # (#37-48). The preset's representation is the file's own default,
        # which a cartoon-less structure never gets, so no backbone check.
        state = server.state
        state.view_preset = name
        state.representation_style = rep
        state.structure_color_mode = (
            state.cartoon_color_mode if rep == "cartoon" else state.atom_color_mode
        )
        state.show_atom_labels = bool(labels)
        state.material_preset = mat
        state.background_override = None  # the material's background, as a preset pick does
        state.ssao_enabled = bool(ssao)
        _rebuild_scene(reader, plotter, viewer_state, state)
        state.status_message = f"View preset: {name}"

    @ctrl.set("run_palette_action")
    def run_palette_action(action_id: Any) -> None:
        """Dispatch a command-palette action to its controller, then close
        the palette (design refresh 2026). Keep the ids in sync with
        _PALETTE_ACTIONS.
        """
        action_id = _first(action_id)
        aid = str(action_id)
        if aid.startswith("open:"):
            server.state.palette_open = False
            server.state.palette_query = ""
            activate_section(aid[len("open:"):])
            return
        dispatch = {
            "cam_iso": lambda: camera_preset("isometric"),
            "cam_top": lambda: camera_preset("xy"),
            "cam_front": lambda: camera_preset("xz"),
            "cam_side": lambda: camera_preset("yz"),
            "cam_reset": lambda: camera_preset("reset"),
            "view_pub": lambda: apply_view_preset("publication"),
            "view_pres": lambda: apply_view_preset("presentation"),
            "view_ana": lambda: apply_view_preset("analysis"),
            "mo_homo": lambda: render_frontier("HOMO"),
            "mo_lumo": lambda: render_frontier("LUMO"),
            "energy_diagram": show_energy_diagram,
            "labels": toggle_atom_labels,
            "background": toggle_background,
            "ui_theme": toggle_dark_ui,
            "orthographic": toggle_orthographic,
            "screenshot": save_screenshot,
            "symmetry": detect_symmetry,
            "symmetrize": edit_symmetrize,
            "exp_obj": lambda: export_geometry("obj"),
            "exp_gltf": lambda: export_geometry("gltf"),
            "exp_xyz": lambda: export_geometry("xyz"),
            "exp_cif": lambda: export_geometry("cif"),
            "exp_pov": lambda: export_geometry("pov"),
            "exp_blend": lambda: export_geometry("blend"),
            "exp_svg": lambda: export_geometry("svg"),
            "exp_cml": lambda: export_geometry("cml"),
            "exp_py": lambda: export_py(),
        }
        fn = dispatch.get(str(action_id))
        server.state.palette_open = False
        server.state.palette_query = ""
        if fn is not None:
            fn()

    @ctrl.set("show_profile")
    def show_profile() -> None:
        """Display performance profile statistics."""
        from vibeview.profiler import profiler

        server.state.status_message = profiler.get_summary()

    @ctrl.set("detect_symmetry")
    def detect_symmetry() -> None:
        """Detect and display the molecular point group."""
        try:
            from vibeview.symmetry import detect_from_qvf, pg_summary

            # The active reader, which carries any unsaved editor geometry.
            pg = detect_from_qvf(reader)
            if pg:
                server.state.point_group = pg.symbol
                server.state.point_group_details = pg_summary(pg)
                server.state.status_message = f"Point group: {pg.symbol}"
            else:
                server.state.status_message = "Could not detect point group"
        except Exception as e:
            server.state.status_message = f"Symmetry detection error: {e}"

    async def _reset_screenshot_flag() -> None:
        await asyncio.sleep(0.5)
        server.state.screenshot_ready = False
        # The client downloaded the data URL when it changed; keeping a
        # multi-megabyte base64 PNG in state afterwards only bloats every
        # later state sync (#37-51). None also lets an identical next
        # capture re-trigger the download.
        server.state.screenshot_data = None
        server.state.flush()

    # ── Controller: geometry export ───────────────────────────────────
    @ctrl.set("export_geometry")
    def export_geometry(fmt: Any) -> None:
        """Export the current structure geometry to a file.

        Receives a plain string from the client
        (``'obj'``, ``'gltf'``, ``'xyz'``).
        Also handles list-wrapped values for robustness.
        """
        fmt = _first(fmt)
        import tempfile
        from pathlib import Path

        # XYZ is a text format — build from structure data directly.
        if fmt == "xyz":
            try:
                sdata = reader.read_structure()
                from vibeview.geometry_text import xyz_text

                export_bytes = xyz_text(sdata).encode()
            except Exception as e:
                server.state.status_message = f"XYZ export error: {e}"
                return
            ext = ".xyz"
            mime = "chemical/x-xyz"
        elif fmt == "cif":
            try:
                sdata = reader.read_structure()
                from vibeview.geometry_text import cif_text

                export_bytes = cif_text(sdata).encode()
            except Exception as e:
                server.state.status_message = f"CIF export error: {e}"
                return
            ext = ".cif"
            mime = "chemical/x-cif"
        elif fmt == "pov":
            from vibeview.povray_export import export_povray

            with tempfile.NamedTemporaryFile(suffix=".pov", delete=False, mode="w") as f:
                tmp = f.name
            try:
                export_povray(reader, tmp, style="ball_and_stick")
                with open(tmp, "rb") as f:
                    export_bytes = f.read()
            finally:
                Path(tmp).unlink(missing_ok=True)
            ext = ".pov"
            mime = "text/plain"
        elif fmt == "blend":
            from vibeview.blender_export import export_blender_script

            with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as f:
                tmp = f.name
            try:
                export_blender_script(reader, tmp, style="ball_and_stick")
                with open(tmp, "rb") as f:
                    export_bytes = f.read()
            finally:
                Path(tmp).unlink(missing_ok=True)
            ext = "_blender.py"
            mime = "text/plain"
        elif fmt == "svg":
            from vibeview.export_svg import export_svg

            with tempfile.NamedTemporaryFile(suffix=".svg", delete=False, mode="w") as f:
                tmp = f.name
            try:
                export_svg(reader, tmp, style="ball_and_stick")
                with open(tmp, "rb") as f:
                    export_bytes = f.read()
            finally:
                Path(tmp).unlink(missing_ok=True)
            ext = ".svg"
            mime = "image/svg+xml"
        elif fmt == "cml":
            from vibeview.export_cml import export_cml

            with tempfile.NamedTemporaryFile(suffix=".cml", delete=False, mode="w") as f:
                tmp = f.name
            try:
                export_cml(reader, tmp)
                with open(tmp, "rb") as f:
                    export_bytes = f.read()
            finally:
                Path(tmp).unlink(missing_ok=True)
            ext = ".cml"
            mime = "chemical/x-cml"
        else:
            if fmt not in ("obj", "gltf", "stl"):
                fmt = "obj"
            ext = {"obj": ".obj", "gltf": ".gltf", "stl": ".stl"}[fmt]
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                tmp = f.name
            try:
                # save_graphic only writes vector formats (svg/eps/pdf/ps/tex) —
                # use the dedicated scene exporters for obj/gltf. export_obj
                # writes <stem>.obj (+ .mtl), so strip the suffix for it.
                if fmt == "gltf":
                    plotter.export_gltf(tmp)
                elif fmt == "stl":
                    # STL has no scene exporter: merge the visible actors'
                    # surfaces (in world coordinates) into one triangle mesh.
                    # export_obj refuses a .stl name, and would write the
                    # wrong format under it anyway (#37-23).
                    _scene_surface_mesh(plotter).save(tmp, binary=True)
                else:  # obj
                    plotter.export_obj(tmp)
                with open(tmp, "rb") as f:
                    export_bytes = f.read()
                mime = {"obj": "model/obj", "gltf": "model/gltf+json", "stl": "model/stl"}[fmt]
            finally:
                Path(tmp).unlink(missing_ok=True)

        _offer_download(
            export_bytes,
            mime,
            f"vibe-view-structure{ext}",
            label=f"Export ({fmt.upper()})",
            fmt=fmt.upper(),
        )
        # Also write to disk next to the QVF file.
        _save_export_disk(export_bytes, reader, fmt)
        server.state.status_message = f"Exported as {fmt.upper()}"

    def _record_download(label: str, fmt: str) -> None:
        """List a delivered file under Recent Exports (newest first, 50 kept)."""
        from datetime import datetime

        state = server.state
        entry = {
            "name": label,
            "path": str(reader.path) if reader.path else "",
            "format": fmt,
            "time": datetime.now().strftime("%H:%M:%S"),
        }
        state.download_history = [entry, *(state.download_history or [])][:50]

    def _offer_download(payload: bytes, mime: str, filename: str, *, label: str, fmt: str) -> None:
        """Hand ``payload`` to the browser as a download named ``filename``.

        The one copy of what four exporters spelled out (#37-39): the data
        URL the client watches (``export_data``), the file name, the Recent
        Exports entry, and the reset that drops the payload after delivery.
        """
        state = server.state
        state.export_data = f"data:{mime};base64,{base64.b64encode(payload).decode()}"
        state.export_filename = filename
        _record_download(label, fmt)
        _schedule_export_reset()

    def _schedule_export_reset() -> None:
        # Headless controller calls have no running loop. Do not create a
        # coroutine that can never be awaited, and leave the download ready.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.call_later(0.5, _clear_export_payload)

    def _clear_export_payload() -> None:
        """Drop a delivered download from state (#37-51)."""
        server.state.export_data = None
        server.state.flush()

    # ── Controller: video export dialog launcher ────────────────────
    @ctrl.set("open_video_export_dialog")
    def open_video_export_dialog() -> None:
        """Open the video export dialog, auto-selecting kind from file."""
        state = server.state
        # Default kind to the first animatable section found, or turntable
        if state.video_kind in ("trajectory", "vibration", "orbital"):
            state.video_export_kind = state.video_kind
        else:
            state.video_export_kind = "turntable"
        state.video_export_dialog = True

    # ── Controller: video export (background thread) ────────────────
    @ctrl.set("do_video_export")
    def do_video_export() -> None:
        """Run video export off the event loop, then update the UI.

        Structured like ``start_hq_render``: the blocking render runs in an
        executor so the viewport stays responsive, and the final state
        (spinner off + status) is written from the async finalize and
        flushed. The previous version ran the render in a bare
        ``threading.Thread`` and set ``video_export_running = False`` /
        ``status_message`` from inside it — but a plain thread's writes are
        never pushed to the client (see ``_live_opt_on_done``: "nothing else
        pushes to the client"), so the export dialog is ``persistent`` on
        ``video_export_running`` and stayed open with the spinner turning
        forever, and the completion/error message never appeared, even though
        the file had already been written.
        """
        import concurrent.futures
        from pathlib import Path

        state = server.state
        fmt = state.video_export_format or "mp4"
        fps = int(state.video_export_fps or 30)
        kind = state.video_export_kind or "turntable"
        # Read on the event loop, not in the render worker.
        vib_mode = int(state.vibration_mode or 0)
        vib_amplitude = float(state.vibration_amplitude or 1.0)

        state.video_export_running = True
        state.video_export_dialog = False

        stem = Path(reader.path).stem if reader.path else "vibe-view"
        ext = fmt if fmt != "frames" else ""
        out = Path(f"{stem}_{kind}.{ext}") if ext else Path(f"{stem}_{kind}")

        def _render() -> Path | None:
            from vibeview.animation import (
                render_orbital_animation,
                render_trajectory_video,
                render_turntable,
                render_vibration_video,
            )

            if kind == "trajectory":
                return render_trajectory_video(reader, out, format=fmt, fps=fps)
            if kind == "vibration":
                return render_vibration_video(
                    reader,
                    out,
                    mode=vib_mode,
                    format=fmt,
                    fps=fps,
                    amplitude=vib_amplitude,
                )
            if kind == "orbital":
                return render_orbital_animation(reader, out, format=fmt, fps=fps)
            if kind == "turntable":
                # Orbits the live plotter's camera: hold the render lock so a
                # section switch cannot rebuild the scene between frames (#37-11).
                with _wf_render_lock:
                    return render_turntable(plotter, out, format=fmt, fps=fps)
            return None

        async def _run_and_finalise() -> None:
            loop = asyncio.get_running_loop()
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    result = await loop.run_in_executor(pool, _render)
                if result:
                    if fmt == "frames":
                        n_frames = len(list(result.glob("frame_*.png")))
                        state.status_message = (
                            f"Video exported: {n_frames} frames in {result}/"
                        )
                    else:
                        state.status_message = f"Video exported: {result.name}"
                else:
                    state.status_message = "Video export failed"
            except Exception as e:  # noqa: BLE001
                state.status_message = f"Video export error: {e}"
            finally:
                state.video_export_running = False
                state.flush()  # async context — nothing else pushes to the client

        _spawn_task(_run_and_finalise(), run_if_no_loop=True)

    # ── Controller: high-quality raytrace render ───────────────────────
    @ctrl.set("start_hq_render")
    def start_hq_render() -> None:
        """Launch a high-quality OSPRay path-traced render in a background
        thread, updating progress in the UI.  When complete, triggers a
        client-side download of the resulting PNG.

        This is the magazine-cover workflow: the user dials in quality /
        resolution / lighting / material, clicks render, and waits for
        the path tracer to accumulate.  Progress is shown in the dialog;
        the live viewport is untouched.
        """
        import concurrent.futures
        import tempfile
        from pathlib import Path

        from vibeview.raytrace import render_high_quality

        # Parse resolution string "WxH".
        res_str = server.state.hq_render_resolution or "3840x2160"
        try:
            w_str, h_str = res_str.split("x")
            width, height = int(w_str), int(h_str)
        except (ValueError, AttributeError):
            width, height = 3840, 2160

        quality = server.state.hq_render_quality or "high"
        environment = server.state.hq_render_environment or "studio"
        material = server.state.hq_render_material or "cpk"

        server.state.hq_render_running = True
        server.state.hq_render_progress = 0.0
        server.state.hq_render_progress_msg = "Starting render …"

        dof_enabled = bool(server.state.hq_render_dof)
        # We'll write to a temp file, then read it back for download.
        tmp_path = Path(tempfile.mkdtemp()) / "vibe-view-hq-render.png"

        progress: dict[str, Any] = {}

        def _progress_callback(fraction: float, message: str) -> None:
            """Called from the worker thread: record only. Trame state is
            written and flushed by the polling loop on the event loop, since
            a worker thread's state writes never reach the client (#37-10)."""
            progress["fraction"] = fraction
            progress["message"] = message

        def _render_worker() -> str | None:
            """Run the render in a background thread.  Returns None on
            success, or an error message string."""
            try:
                # The path tracer renders the live plotter: hold the render
                # lock so a section switch cannot rebuild the scene
                # mid-render (#37-11).
                with _wf_render_lock:
                    render_high_quality(
                        plotter,
                        str(tmp_path),
                        quality=quality,
                        resolution=(width, height),
                        environment=environment,
                        material_style=material,
                        dof_enabled=dof_enabled,
                        progress_callback=_progress_callback,
                    )
                return None
            except Exception as exc:
                return str(exc)

        async def _run_and_finalise() -> None:
            loop = asyncio.get_running_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = loop.run_in_executor(pool, _render_worker)
                # Relay worker progress to the client while it renders.
                while True:
                    done, _pending = await asyncio.wait({future}, timeout=0.25)
                    if progress:
                        server.state.hq_render_progress = float(progress.get("fraction", 0.0))
                        server.state.hq_render_progress_msg = str(progress.get("message", ""))
                        server.state.flush()
                    if done:
                        break
                error = future.result()

            if error:
                server.state.hq_render_running = False
                server.state.hq_render_progress_msg = f"Render failed: {error}"
                server.state.status_message = f"HQ render error: {error}"
                server.state.flush()
                return

            save_disk = bool(server.state.hq_render_save_disk)
            try:
                png_bytes = tmp_path.read_bytes()
            except OSError as exc:
                server.state.hq_render_running = False
                server.state.hq_render_progress_msg = f"Read error: {exc}"
                server.state.status_message = f"HQ render read error: {exc}"
                server.state.flush()
                return
            finally:
                # Clean up the temp file.
                try:
                    tmp_path.unlink(missing_ok=True)
                    tmp_path.parent.rmdir()
                except OSError:
                    pass

            server.state.hq_render_running = False
            server.state.hq_render_progress_msg = f"Done — {width}×{height}"

            if save_disk and reader.path is not None:
                # Write alongside the QVF file.
                from datetime import datetime

                stem = reader.path.stem
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                out = reader.path.parent / f"{stem}_render_{quality}_{ts}.png"
                out.write_bytes(png_bytes)
                server.state.hq_render_saved_path = str(out)
                server.state.status_message = f"HQ render saved → {out.name}"
            else:
                # Browser download (reuse screenshot download trigger).
                b64 = base64.b64encode(png_bytes).decode()
                server.state.screenshot_data = f"data:image/png;base64,{b64}"
                server.state.screenshot_ready = True
                server.state.status_message = (
                    f"HQ render complete ({width}×{height}, {quality} quality)"
                )
                if reader.path is None:
                    server.state.status_message += " — downloaded"
                _spawn_task(_reset_screenshot_flag())
            server.state.flush()  # async context — push the finished state

        _spawn_task(_run_and_finalise(), run_if_no_loop=True)

    # ── Controller: measurement ──────────────────────────────────────
    @ctrl.set("compute_bonds")
    def compute_bonds() -> None:
        """Compute and display bond distances + angles."""
        structure = next((s for s in reader.sections if s.kind == "structure"), None)
        if structure is None:
            server.state.measure_result = "No structure loaded"
            return
        import math

        # Section has no ``.payload`` attribute — read the structure
        # through the reader (positions are in Å, matching the covalent
        # radii below). The old json.loads(structure.payload) raised
        # AttributeError, so the ruler button silently did nothing.
        try:
            sdata = reader.read_structure()
        except Exception as e:  # noqa: BLE001
            server.state.measure_result = f"Could not read structure: {e}"
            return
        import numpy as np

        from vibeview.bonds import bond_endpoints, structure_bonds
        from vibeview.elements import z_to_symbol

        n = len(sdata.atoms)
        positions = np.asarray([a.position for a in sdata.atoms], dtype=float).reshape(n, 3)
        # The bonds the viewer draws, measured through their periodic images.
        # Neighbour positions are the far endpoints, so an angle at an atom
        # bonded across a cell face is the real angle, not one to the
        # in-cell copy.
        bonds: list[tuple[int, int, float]] = []
        neighbors: dict[int, list[tuple[int, np.ndarray]]] = {i: [] for i in range(n)}
        for bond in structure_bonds(sdata):
            i, j = bond[0], bond[1]
            start, end = bond_endpoints(positions, bond, sdata.lattice_vectors)
            d = float(np.linalg.norm(end - start))
            bonds.append((i, j, d))
            neighbors[i].append((j, end))
            neighbors[j].append((i, start + (start - end)))

        def _sym(idx: int) -> str:
            z = int(sdata.atoms[idx].atomic_number)
            return f"{z_to_symbol(z, str(z))}{idx + 1}"

        rows: list[str] = []
        # Distances.
        if bonds:
            rows.append(_BOND_DISTANCES_HEADER)
            unit = server.state.measure_unit
            for i, j, d in sorted(bonds, key=lambda x: x[2]):
                rows.append(f"  {_sym(i)}–{_sym(j)}: {format_length(d, unit)}")

        # Angles: for every atom with ≥2 neighbors, show all angles.
        angles: list[str] = []
        for center in range(n):
            nbrs = neighbors[center]
            if len(nbrs) < 2:
                continue
            pc = positions[center]
            for a in range(len(nbrs)):
                for b in range(a + 1, len(nbrs)):
                    ja, pa = nbrs[a]
                    jb, pb = nbrs[b]
                    v1 = pa - pc
                    v2 = pb - pc
                    n1 = float(np.linalg.norm(v1))
                    n2 = float(np.linalg.norm(v2))
                    if n1 < 1e-9 or n2 < 1e-9:
                        continue
                    cos = float(np.dot(v1, v2)) / (n1 * n2)
                    angle_deg = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
                    angles.append(f"  {_sym(ja)}–{_sym(center)}–{_sym(jb)}: {angle_deg:.1f}°")
        if angles:
            if rows:
                rows.append("")
            rows.append("── Bond Angles ──")
            rows.extend(angles)

        if rows:
            server.state.measure_result = "\n".join(rows)
            dist_count = len(bonds)
            ang_count = len(angles)
            parts = [f"{dist_count} bond(s)"]
            if ang_count:
                parts.append(f"{ang_count} angle(s)")
            server.state.status_message = ", ".join(parts)
        else:
            server.state.measure_result = "No bonds or angles detected"

    # ── Controller: interactive atom picking + measurement ────────────
    # @ctrl.set alone is not client-callable; the keyboard-shortcut JS
    # invokes trame.trigger("toggle_measure_mode"), which needs a trigger registration.
    @ctrl.trigger("toggle_measure_mode")
    @ctrl.set("toggle_measure_mode")
    def toggle_measure_mode(on: bool | None = None) -> None:
        state = server.state
        on = _first(on)
        state.measure_mode = (not state.measure_mode) if on is None else bool(on)
        if state.measure_mode and state.edit_mode:
            # Measure and edit both consume atom clicks; they are mutually
            # exclusive, as the manual says (#37-18).
            _leave_edit_mode()
        state.selected_atoms = []
        _remove_actors_by_prefix(plotter, _Actors.PICK_MARKER)
        if state.measure_mode:
            state.measure_result = _MEASURE_HINT
        else:
            state.measure_result = ""
        _push_view(plotter)

    @ctrl.set("clear_picks")
    def clear_picks() -> None:
        server.state.selected_atoms = []
        _remove_actors_by_prefix(plotter, _Actors.PICK_MARKER)
        server.state.measure_result = (
            _MEASURE_HINT if server.state.measure_mode else ""
        )
        _push_view(plotter)

    @ctrl.set("on_pick")
    def on_pick(event: Any = None) -> None:
        """Handle viewport clicks: measure mode (distance/angle/dihedral)
        or edit mode (atom selection)."""
        state = server.state
        if not isinstance(event, dict):
            return
        if not state.measure_mode and not state.edit_mode and not state.residue_pick_mode:
            return
        # Find nearest atom to click point
        wp = None
        for key in ("worldPosition", "position3D", "position3d", "pickedPoint"):
            val = event.get(key)
            if val and len(val) >= 3:
                wp = val
                break
        if wp is None and isinstance(event.get("ray"), dict):
            wp = event["ray"].get("position")
        if not wp or len(wp) < 3:
            return
        try:
            sdata = reader.read_structure()
        except Exception:
            return
        import numpy as _np

        if not sdata.atoms:
            return
        if state.residue_pick_mode and not state.measure_mode and not state.edit_mode:
            residue_key = _pick_residue(sdata, plotter, state, wp[:3], viewer_state.replication)
            if residue_key is not None:
                chain, seq = residue_key
                set_residue_selection(f"{chain or '*'}/{seq}")
            return

        pos = _np.array([a.position for a in sdata.atoms], dtype=float)
        click = _np.array([float(c) for c in wp[:3]], dtype=float)
        dists = _np.linalg.norm(pos - click, axis=1)
        idx = int(_np.argmin(dists))
        min_dist = float(dists[idx])

        if state.edit_mode:
            if min_dist <= 1.5:
                # Near an existing atom — toggle selection
                if idx in state.edit_selected:
                    state.edit_selected.remove(idx)
                else:
                    state.edit_selected.append(idx)
                # In-place mutation, so the pushed snapshot aliases this same
                # list and compares equal — the client would keep its stale
                # copy. Three buttons bind to `edit_selected.length === 0`
                # (Delete Selected, Change Element, Fragment insert), so
                # without this they stay disabled after a viewport click.
                # Same issue as section_list / sidebar_entries above.
                state.dirty("edit_selected")
                _update_edit_highlights(reader, plotter, state.edit_selected)
                state.status_message = f"Selected {len(state.edit_selected)} atom(s)"
                _push_view(plotter)
            else:
                # Clicked empty space — place a new atom
                wp_np = _np.array([float(c) for c in wp[:3]], dtype=float)
                new_element = server.state.edit_new_element or "C"
                # Save current state for undo
                _record_undo(sdata, tolerate_failure=True)
                _add_atom_to_structure(reader, plotter, wp_np.tolist(), new_element)
                server.state.edit_selected = []
                server.state.status_message = f"Added {new_element} atom"
                _push_view(plotter)
                _live_opt_schedule()
            return

        if dists[idx] > 2.0:
            return

        if state.measure_mode:
            picked = [i for i in state.selected_atoms if 0 <= i < len(sdata.atoms)]
            if idx in picked:
                picked.remove(idx)
            else:
                picked.append(idx)
            state.selected_atoms = picked
            _update_pick_markers(reader, plotter, picked)
            state.measure_result = _measure_text(sdata, picked, state.measure_unit)
            _push_view(plotter)

    # ── Controller: periodic-table element picker ────────────────────
    @ctrl.set("open_element_picker")
    def open_element_picker(target: str = "new") -> None:
        """Open the periodic-table dialog for 'new' or 'change' picking."""
        target = _first(target, "new")
        server.state.element_picker_target = (
            "change" if str(target) == "change" else "new"
        )
        server.state.element_picker_open = True

    @ctrl.set("pick_element")
    def pick_element(symbol: Any = None) -> None:
        """Apply the clicked element and close the periodic table.

        Target 'new' sets the element used for added atoms; 'change'
        routes through edit_change_element (undoable, needs a selection).
        """
        symbol = _first(symbol)
        if not symbol:
            return
        state = server.state
        state.element_picker_open = False
        if state.element_picker_target == "change":
            edit_change_element(str(symbol))
        else:
            state.edit_new_element = str(symbol)
            state.status_message = f"New atoms will be {symbol}"

    # ── Controller: hover picking (tooltip + context-menu targeting) ──
    # vtk.js debounces hover picks (they fire ~10 ms after the pointer
    # pauses, not per mousemove), so this is one pick per hover stop —
    # cheap. The last pick is remembered server-side so the right-click
    # menu can act on "the spot under the cursor": the raw DOM
    # contextmenu event only carries screen coordinates, and the server
    # cannot unproject those (the client owns the camera in VtkLocalView).
    # "at" starts at -inf so "no hover yet" always reads stale, even on a
    # freshly booted machine where time.monotonic() itself is near zero.
    _hover_state: dict[str, Any] = {"world": None, "atom_idx": -1, "at": float("-inf")}
    # Snapshot taken by context_menu_opened, consumed by the menu actions.
    _ctx_menu_target: dict[str, Any] = {"world": None, "atom_idx": -1}

    @ctrl.set("on_hover")
    def on_hover(event: Any = None) -> None:
        """Remember the world position under the cursor + show the tooltip."""
        import time as _time

        state = server.state
        if not isinstance(event, dict):
            return
        wp = None
        for key in ("worldPosition", "position3D", "position3d", "pickedPoint"):
            val = event.get(key)
            if val and len(val) >= 3:
                wp = val
                break
        if not wp or len(wp) < 3:
            return
        try:
            sdata = _current_structure(reader)
        except Exception:
            return
        import numpy as _np

        pos = _np.array([a.position for a in sdata.atoms], dtype=float)
        point = _np.array([float(c) for c in wp[:3]], dtype=float)
        dists = _np.linalg.norm(pos - point, axis=1)
        idx = int(_np.argmin(dists))
        # Same near-atom threshold as on_pick.
        atom_idx = idx if float(dists[idx]) <= 1.5 else -1

        _hover_state["world"] = [float(c) for c in wp[:3]]
        _hover_state["atom_idx"] = atom_idx
        _hover_state["at"] = _time.monotonic()

        # Tooltip: element + index over an atom, hidden over empty space.
        # Only write on change — every write is a client push.
        if atom_idx >= 0:
            text = f"{sdata.atoms[atom_idx].symbol} · atom {atom_idx}"
            if state.hover_tooltip != text:
                state.hover_tooltip = text
            if not state.hover_tooltip_visible:
                state.hover_tooltip_visible = True
        elif state.hover_tooltip_visible:
            state.hover_tooltip_visible = False

    # The contextmenu JS calls this right after opening the menu. A recent
    # hover pick (the pointer pauses to right-click, and the debounced pick
    # fires on that pause) tells us what the menu is pointing at; without
    # one the menu opens untargeted, exactly as before.
    # Trigger + set, like edit_redo: the JS invokes trame.trigger(...),
    # which needs the trigger registration; tests reach it via ctrl.
    @ctrl.trigger("context_menu_opened")
    @ctrl.set("context_menu_opened")
    def context_menu_opened() -> None:
        import time as _time

        # While the pointer rests on the canvas the last pick stays valid —
        # nothing moved, so nothing changed. Users pause to read the tooltip
        # before right-clicking, and on a busy scene the pick round trip
        # alone approaches seconds; the original 3 s window discarded
        # perfectly good targets (measured 3.95 s in the QA harness, with
        # the correct atom in the snapshot). 30 s only guards against truly
        # ancient state, e.g. long after the pointer left the canvas.
        fresh = (_time.monotonic() - _hover_state["at"]) < 30.0
        _ctx_menu_target["world"] = _hover_state["world"] if fresh else None
        _ctx_menu_target["atom_idx"] = _hover_state["atom_idx"] if fresh else -1
        server.state.context_menu_atom_idx = _ctx_menu_target["atom_idx"]

    # ── Controller: volume clip planes ────────────────────────────────
    @ctrl.set("toggle_clip")
    def toggle_clip(on: bool | None = None) -> None:
        """Enable/disable volume clip planes.

        Wired from the Enable switch as
        ``update_modelValue=(ctrl.toggle_clip, "[$event]")``, so it is called
        with the switch's new boolean — *set* (don't flip) it, since ``v_model``
        has already written ``clip_enabled`` and a second flip would cancel the
        toggle out. Called with no argument it flips (so an icon/keyboard
        trigger still works). Mirrors ``toggle_measure_mode`` /
        ``toggle_atom_labels``.

        Before this took ``on``, the ``"[$event]"`` payload hit a zero-arg
        signature and every toggle raised ``TypeError`` server-side
        (``toggle_clip() takes 0 positional arguments but 1 was given``); the
        clip never applied through the switch even though ``clip_enabled``
        flipped via ``v_model`` (audit finding UI-OBS-G).
        """
        state = server.state
        state.clip_enabled = (not state.clip_enabled) if on is None else bool(on)
        if not state.clip_enabled:
            state.show_slice = False  # slice depends on clip axes
            state.clip_position_message = ""
        # Optimistic status first; _rebuild_clip overwrites it with "Clip
        # error: …" if the rebuild fails, so an error still wins.
        state.status_message = "Clip plane enabled" if state.clip_enabled else "Clip plane disabled"
        _rebuild_clip(reader, plotter, viewer_state, state)
        _counted_view_update()

    @ctrl.set("toggle_show_slice")
    def toggle_show_slice(on: bool | None = None) -> None:
        """Toggle 2D cross-section slice mode."""
        state = server.state
        state.show_slice = (not state.show_slice) if on is None else bool(on)
        if state.show_slice and not state.clip_enabled:
            state.clip_enabled = True  # slice needs clip-position sliders
        if not state.show_slice:
            state.clip_position_message = ""
        state.status_message = (
            "2D slice enabled" if state.show_slice else "2D slice disabled"
        )
        _rebuild_clip(reader, plotter, viewer_state, state)
        _counted_view_update()

    @ctrl.set("update_clip")
    def update_clip(axis: Any = None, value: Any = None) -> None:
        """Update clip planes from slider values.

        Client-side ``v_model`` changes can reach the browser before the
        corresponding state sync reaches Python.  Each slider therefore sends
        both its axis and ``$event`` so this callback updates the authoritative
        server state before rebuilding the scene.
        """
        state = server.state
        if axis in {"x", "y", "z"} and value is not None:
            try:
                setattr(state, f"clip_{axis}", min(1.0, max(0.0, float(value))))
            except (TypeError, ValueError):
                return
        if state.clip_enabled:
            _rebuild_clip(reader, plotter, viewer_state, state)
            _counted_view_update()

    # ── Edit transactions ──────────────────────────────────────────────
    def _record_undo(
        sdata: StructureData, *, clear_redo: bool = True, tolerate_failure: bool = False
    ) -> None:
        """Push ``sdata`` onto the undo stack before an edit changes it.

        The one copy of what eleven edit controllers each spelled out
        (#37-34, #37-R5): snapshot, drop the redo stack (a new edit forks
        history), and mark both lists dirty. The lists are mutated in place,
        which trame's change tracking cannot see (the pushed snapshot aliases
        the same object), so without ``dirty`` Undo and Redo stay disabled in
        the client. ``tolerate_failure`` keeps the edit going without an undo
        entry when the snapshot itself fails, as the atom-placing paths do.
        """
        state = server.state
        try:
            state.edit_history.append(_structure_edit_snapshot(sdata))
        except Exception:
            if not tolerate_failure:
                raise
        if clear_redo:
            state.edit_future.clear()
        state.dirty("edit_history", "edit_future")

    # ── Controller: edit mode ──────────────────────────────────────────
    # @ctrl.set alone is not client-callable; the keyboard-shortcut JS
    # invokes trame.trigger("toggle_edit_mode"), which needs a trigger registration.
    @ctrl.trigger("toggle_edit_mode")
    @ctrl.set("toggle_edit_mode")
    def toggle_edit_mode() -> None:
        """Toggle edit mode on/off."""
        state = server.state
        state.edit_mode = not state.edit_mode
        if state.edit_mode:
            state.edit_selected = []
            if state.measure_mode:
                # Mutually exclusive with measuring (#37-18).
                state.measure_mode = False
                state.selected_atoms = []
                state.measure_result = ""
                _remove_actors_by_prefix(plotter, _Actors.PICK_MARKER)
            state.status_message = "Edit mode ON — click atoms to select, click empty space to add"
        else:
            _leave_edit_mode()
        _push_view(plotter)

    def _leave_edit_mode() -> None:
        """Turn edit mode off and drop its selection, freezes and live opt."""
        state = server.state
        state.edit_mode = False
        state.edit_selected = []
        _remove_actors_by_prefix(plotter, _Actors.EDIT_HIGHLIGHT)
        _reset_frozen(plotter, state)  # freezing is an edit-mode concept
        state.status_message = "Edit mode OFF"
        # Leaving the editor is a session boundary, not just a worker
        # cancellation: a delayed availability probe must not re-enable
        # and schedule a new optimization after the editor has closed.
        _disable_live_opt(clear_status=True)

    # @ctrl.set alone is not client-callable; the keyboard-shortcut JS
    # invokes trame.trigger("edit_delete_selected"), which needs a trigger registration.
    @ctrl.trigger("edit_delete_selected")
    @ctrl.set("edit_delete_selected")
    def edit_delete_selected() -> None:
        """Delete the currently selected atoms from the structure."""
        state = server.state
        if not state.edit_selected:
            state.status_message = "No atoms selected to delete"
            return
        try:
            sdata = reader.read_structure()
        except Exception:
            return
        selected_set = set(state.edit_selected)
        # Build new lists excluding selected
        new_positions = [
            a.position.tolist() for i, a in enumerate(sdata.atoms) if i not in selected_set
        ]
        new_symbols = [a.symbol for i, a in enumerate(sdata.atoms) if i not in selected_set]
        # Refuse before touching the stacks. This check used to sit after the
        # history push and future.clear() below, so selecting every atom and
        # pressing delete left a no-op undo entry behind and threw away the
        # redo stack, despite the delete itself being refused.
        if not new_symbols:
            state.status_message = "Cannot delete all atoms"
            return
        # Save current state for undo
        _record_undo(sdata)
        _rebuild_structure_from_positions(reader, plotter, new_positions, new_symbols)
        state.edit_selected = []
        # Deleting reindexes the atoms, so any frozen indices are now stale.
        _reset_frozen(plotter, state)
        state.status_message = f"Deleted {len(selected_set)} atom(s)"
        _push_view(plotter)
        _live_opt_schedule()

    # ── B3: freeze-atom constraints for live-opt ──
    # The wire protocol (live_opt.build_request) already carries `frozen`;
    # these controllers are the UI that populates it. Frozen atoms are held
    # fixed while the auto-optimize relaxes the rest.
    @ctrl.trigger("edit_freeze_selected")
    @ctrl.set("edit_freeze_selected")
    def edit_freeze_selected() -> None:
        """Hold the currently selected atoms fixed during live-opt."""
        state = server.state
        if not state.edit_selected:
            state.status_message = "Select atoms first, then freeze them"
            return
        try:
            natoms = len(reader.read_structure().atoms)
        except Exception:
            return
        already = set(state.frozen_atoms)
        selected = {i for i in state.edit_selected if 0 <= i < natoms}
        frozen = already | selected
        state.frozen_atoms = sorted(frozen)
        # Count what this call actually froze, not what was selected: an
        # atom that was already frozen, or an out-of-range index, is not a
        # new constraint and reporting it as one overstates the effect.
        n_new = len(selected - already)
        state.edit_selected = []
        _update_edit_highlights(reader, plotter, state.edit_selected)
        _update_frozen_highlights(reader, plotter, state.frozen_atoms)
        state.status_message = (
            f"Froze {n_new} atom(s) — {len(frozen)} held fixed during auto-optimize"
            if n_new
            else f"Already frozen — {len(frozen)} held fixed during auto-optimize"
        )
        _push_view(plotter)
        _live_opt_schedule()  # re-relax with the new constraint

    @ctrl.set("edit_unfreeze_all")
    def edit_unfreeze_all() -> None:
        """Release every frozen atom."""
        state = server.state
        if not state.frozen_atoms:
            return
        _reset_frozen(plotter, state)
        state.status_message = "Unfroze all atoms"
        _push_view(plotter)
        _live_opt_schedule()  # re-relax now that everything is free

    @ctrl.set("edit_change_element")
    def edit_change_element(new_element: str) -> None:
        """Change the element of all selected atoms."""
        state = server.state
        if not state.edit_selected or not new_element:
            state.status_message = "Select atoms and choose an element first"
            return
        try:
            sdata = reader.read_structure()
        except Exception:
            return
        # Save current state for undo
        _record_undo(sdata)
        # Build new symbol list
        new_symbols = (
            list(sdata.symbols) if hasattr(sdata, "symbols") else [a.symbol for a in sdata.atoms]
        )
        for idx in state.edit_selected:
            if 0 <= idx < len(new_symbols):
                new_symbols[idx] = new_element
        positions_list = [a.position.tolist() for a in sdata.atoms]
        _rebuild_structure_from_positions(reader, plotter, positions_list, new_symbols)
        n_changed = len(state.edit_selected)
        state.edit_selected = []
        state.status_message = f"Changed {n_changed} atom(s) to {new_element}"
        _push_view(plotter)
        _live_opt_schedule()

    # @ctrl.set alone is not client-callable; the keyboard-shortcut JS
    # invokes trame.trigger("edit_undo"), which needs a trigger registration.
    @ctrl.trigger("edit_undo")
    @ctrl.set("edit_undo")
    def edit_undo() -> None:
        """Restore the previous state from the undo stack."""
        state = server.state
        if not state.edit_history:
            state.status_message = "Nothing to undo"
            return
        # Save current state to redo stack
        try:
            sdata = reader.read_structure()
        except Exception:
            return
        _cancel_live_opt_intent(clear_status=True)
        state.edit_future.append(_structure_edit_snapshot(sdata))
        # Pop and restore previous state
        prev = state.edit_history.pop()
        # Both stacks were mutated in place; see the note on the clear() sites.
        state.dirty("edit_history", "edit_future")
        _rebuild_structure_from_positions(
            reader,
            plotter,
            prev["positions"],
            prev.get("symbols"),
            lattice_vectors=prev.get("lattice_vectors"),
        )
        state.edit_selected = []
        _reset_frozen(plotter, state)  # restored geometry may reindex atoms
        state.status_message = "Undo"
        _push_view(plotter)

    # @ctrl.set alone is not client-callable; the keyboard-shortcut JS
    # invokes trame.trigger("edit_redo"), which needs a trigger registration.
    @ctrl.trigger("edit_redo")
    @ctrl.set("edit_redo")
    def edit_redo() -> None:
        """Re-apply the most recently undone edit."""
        state = server.state
        if not state.edit_future:
            state.status_message = "Nothing to redo"
            return
        future = state.edit_future.pop()
        # Save current to undo stack, keeping the rest of the redo stack.
        with contextlib.suppress(Exception):
            _record_undo(reader.read_structure(), clear_redo=False)
        _cancel_live_opt_intent(clear_status=True)
        # The pop above mutated the redo stack in place.
        state.dirty("edit_history", "edit_future")
        _rebuild_structure_from_positions(
            reader,
            plotter,
            future["positions"],
            future.get("symbols"),
            lattice_vectors=future.get("lattice_vectors"),
        )
        state.edit_selected = []
        _reset_frozen(plotter, state)  # restored geometry may reindex atoms
        state.status_message = "Redo"
        _push_view(plotter)

    # ── Controller: live geometry optimization (M3, roadmap §4) ───────
    # A debounced background relax of the current sketch: every editor
    # mutation schedules a subprocess MSINDO optimization after an edit
    # pause; each optimizer step streams back and eases the atoms toward
    # the relaxed geometry. Engine survey + wire protocol:
    # vibeview/live_opt.py.
    from vibeview.live_opt import (
        MAX_LIVE_OPT_ATOMS,
        LiveOptService,
        build_request,
        probe_worker,
    )

    _live_opt_undo_pushed = False
    _live_opt_source_reader = None
    _live_opt_run_epoch = 0
    _live_opt_probe_epoch = 0

    def _live_opt_run_is_current(run_epoch: int | None, source_reader: QVFReader | None) -> bool:
        """Authorize a callback only for the immutable current run."""
        return (
            run_epoch == _live_opt_run_epoch
            and source_reader is reader
            and _live_opt_source_reader is source_reader
            and server.state.live_opt_enabled
        )

    def _live_opt_on_step(
        event: Any, run_epoch: int | None = None, source_reader: QVFReader | None = None
    ) -> None:
        nonlocal _live_opt_undo_pushed
        if not _live_opt_run_is_current(run_epoch, source_reader):
            return
        state = server.state
        positions = event.get("positions") or []
        try:
            sdata = reader.read_structure()
        except Exception:
            return
        if len(positions) != len(sdata.atoms):
            return  # stale event from a pre-edit snapshot — ignore
        if not _live_opt_undo_pushed:
            # One undo entry per relax run: undo returns to the sketch as
            # drawn, not to every intermediate optimizer step.
            _record_undo(sdata)
            _live_opt_undo_pushed = True
        _rebuild_structure_from_positions(reader, plotter, positions)
        # Frozen atoms keep their indices across a relax; re-draw their markers
        # since the scene rebuild dropped custom actors.
        _update_frozen_highlights(reader, plotter, state.frozen_atoms)
        state.live_opt_status = (
            f"step {event.get('step', '?')} · E = {event.get('energy', 0.0):.4f} Ha"
            f" · gₘₐₓ = {event.get('gmax', 0.0):.4f} Ha/bohr"
        )
        _push_view(plotter)
        state.flush()  # async context — nothing else pushes to the client

    def _live_opt_on_done(
        converged: bool,
        steps: int,
        run_epoch: int | None = None,
        source_reader: QVFReader | None = None,
    ) -> None:
        if not _live_opt_run_is_current(run_epoch, source_reader):
            return
        server.state.live_opt_status = (
            f"relaxed in {steps} steps"
            if converged
            else f"not converged after {steps} steps"
        )
        server.state.flush()

    def _live_opt_on_error(
        msg: str, run_epoch: int | None = None, source_reader: QVFReader | None = None
    ) -> None:
        if not _live_opt_run_is_current(run_epoch, source_reader):
            return
        server.state.live_opt_status = f"live-opt error: {msg}"
        server.state.flush()

    def _live_opt_on_note(
        msg: str, run_epoch: int | None = None, source_reader: QVFReader | None = None
    ) -> None:
        if not _live_opt_run_is_current(run_epoch, source_reader):
            return
        # Non-geometry progress (e.g. MACE model loading / weight download).
        server.state.live_opt_status = msg
        server.state.flush()

    live_opt = LiveOptService(
        on_step=_live_opt_on_step,
        on_done=_live_opt_on_done,
        on_error=_live_opt_on_error,
        on_note=_live_opt_on_note,
    )

    def _cancel_live_opt_run(*, clear_status: bool = False) -> None:
        """Cancel the worker and invalidate every callback from its run."""
        nonlocal _live_opt_run_epoch, _live_opt_source_reader
        nonlocal _live_opt_undo_pushed
        live_opt.cancel()
        _live_opt_run_epoch += 1
        _live_opt_source_reader = None
        _live_opt_undo_pushed = False
        if clear_status:
            server.state.live_opt_status = ""

    def _cancel_live_opt_intent(*, clear_status: bool = False) -> None:
        """Invalidate both a pending capability probe and the current run."""
        nonlocal _live_opt_probe_epoch
        _live_opt_probe_epoch += 1
        _cancel_live_opt_run(clear_status=clear_status)

    def _disable_live_opt(*, clear_status: bool = False) -> None:
        """Turn auto-opt off and invalidate both pending probes and runs."""
        server.state.live_opt_enabled = False
        _cancel_live_opt_intent(clear_status=clear_status)

    _LIVE_OPT_ENGINE_TITLES = {
        "msindo": "MSINDO (semi-empirical)",
        "mace": "MACE (ML potential, MACE-MPA-0)",
    }

    def _live_opt_apply_probe(result: dict[str, Any]) -> None:
        """Record probe outcome: availability + the engine picker options."""
        state = server.state
        engines = [e for e in result.get("engines", []) if e in _LIVE_OPT_ENGINE_TITLES]
        state.live_opt_available = bool(result.get("available")) and bool(engines)
        state.live_opt_engine_options = [
            {"title": _LIVE_OPT_ENGINE_TITLES[e], "value": e} for e in engines
        ]
        if engines and state.live_opt_engine not in engines:
            state.live_opt_engine = engines[0]

    def _live_opt_schedule() -> None:
        """Debounce-start a relax of the current geometry (cancel-on-edit).

        Called after every editor mutation; a no-op unless the
        Auto-optimize switch is on.
        """
        nonlocal _live_opt_source_reader
        state = server.state
        # Cancel before *every* eligibility guard. Otherwise an edit that
        # becomes too small, too large, or all-frozen leaves the previous
        # worker authorized against this same mutable reader.
        _cancel_live_opt_run()
        if not state.live_opt_enabled:
            return
        try:
            sdata = reader.read_structure()
        except Exception:
            state.live_opt_status = "live-opt unavailable for the current structure"
            return
        if len(sdata.atoms) > MAX_LIVE_OPT_ATOMS:
            state.live_opt_status = (
                f"structure too large for live-opt (>{MAX_LIVE_OPT_ATOMS} atoms)"
            )
            return
        if len(sdata.atoms) < 2:
            state.live_opt_status = "add at least 2 atoms to optimize"
            return
        # B3: atoms the user froze are held fixed during the relax. Clamp to the
        # current atom count (indices can go stale after a structural edit).
        natoms = len(sdata.atoms)
        frozen = sorted(i for i in state.frozen_atoms if 0 <= i < natoms)
        if len(frozen) >= natoms:
            state.live_opt_status = "all atoms frozen — unfreeze some to optimize"
            return
        run_epoch = _live_opt_run_epoch
        source_reader = reader
        _live_opt_source_reader = source_reader
        engine = state.live_opt_engine or "msindo"
        frozen_note = f" ({len(frozen)} frozen)" if frozen else ""
        state.live_opt_status = (
            f"optimizing after edit pause ({engine}){frozen_note} …"
        )
        live_opt.schedule(
            build_request(
                [a.symbol for a in sdata.atoms],
                [a.position.tolist() for a in sdata.atoms],
                engine=engine,
                frozen=frozen,
            ),
            on_step=lambda event: _live_opt_on_step(
                event, run_epoch, source_reader
            ),
            on_done=lambda converged, steps: _live_opt_on_done(
                converged, steps, run_epoch, source_reader
            ),
            on_error=lambda message: _live_opt_on_error(
                message, run_epoch, source_reader
            ),
            on_note=lambda message: _live_opt_on_note(
                message, run_epoch, source_reader
            ),
        )

    @ctrl.set("toggle_live_opt")
    def toggle_live_opt(on: bool | None = None) -> None:
        """Auto-optimize switch. Wired like ``toggle_clip``: *set* (don't
        flip) when called with the switch's new value, flip when called
        with no argument. First enable probes the worker subprocess; if
        vibe-qc is not importable there, the switch snaps back off with
        the reason in the status line (decision 2: no hard dependency).
        """
        nonlocal _live_opt_probe_epoch
        state = server.state
        _live_opt_probe_epoch += 1
        probe_epoch = _live_opt_probe_epoch
        state.live_opt_enabled = (
            (not state.live_opt_enabled) if on is None else bool(on)
        )
        if not state.live_opt_enabled:
            _cancel_live_opt_run(clear_status=True)
            return
        if state.live_opt_available is False:
            _disable_live_opt()
            return  # live_opt_status already carries the probe reason
        if state.live_opt_available is None:
            _cancel_live_opt_run()
            state.live_opt_status = "checking vibe-qc availability …"
            probe_reader = reader

            async def _probe_then_start() -> None:
                result = await probe_worker()
                request_is_current = (
                    probe_epoch == _live_opt_probe_epoch
                    and probe_reader is reader
                    and state.live_opt_enabled
                )
                # Probe generations are last-writer-wins. A result from an
                # older toggle or reader must not overwrite newer capability,
                # intent, status, or a newly scheduled run.
                if not request_is_current:
                    state.flush()
                    return
                _live_opt_apply_probe(result)
                if not state.live_opt_available:
                    _disable_live_opt()
                    state.live_opt_status = (
                        f"live-opt unavailable: {result.get('reason', 'unknown')}"
                    )
                    state.flush()
                    return
                state.live_opt_status = "auto-optimize on"
                _live_opt_schedule()
                state.flush()  # async context — push the probe outcome

            _spawn_task(_probe_then_start(), run_if_no_loop=True)
            return
        state.live_opt_status = "auto-optimize on"
        _live_opt_schedule()

    @ctrl.set("set_live_opt_engine")
    def set_live_opt_engine(engine: Any = None) -> None:
        """Engine picker handler. Wired like ``toggle_clip``: ``v_model``
        already wrote ``live_opt_engine``, so with an argument we just
        record it; either way, switching engine while auto-optimize is on
        re-relaxes with the new engine. A controller (not a
        ``@state.change`` watcher) so repeated ``create_app`` calls rebind
        one handler instead of accumulating stale-closure watchers.
        """
        if engine:
            server.state.live_opt_engine = str(engine)
        if server.state.live_opt_enabled and server.state.live_opt_engine:
            _live_opt_schedule()

    # ── Controller: context menu actions ─────────────────────────────
    @ctrl.set("context_select_atom")
    def context_select_atom() -> None:
        """Toggle selection of the atom the context menu was opened over."""
        idx = server.state.context_menu_atom_idx
        if idx < 0:
            return
        if not server.state.edit_mode:
            server.state.status_message = "Enable edit mode to select atoms"
            return
        if idx not in server.state.edit_selected:
            server.state.edit_selected.append(idx)
        else:
            server.state.edit_selected.remove(idx)
        server.state.dirty("edit_selected")  # in-place; see the click handler
        _update_edit_highlights(reader, plotter, server.state.edit_selected)
        server.state.status_message = (
            f"Selected {len(server.state.edit_selected)} atom(s)"
        )
        _push_view(plotter)

    @ctrl.set("context_add_atom")
    def context_add_atom() -> None:
        """Add an atom at the position the context menu was opened over.

        The target comes from the hover-pick snapshot taken when the menu
        opened (``context_menu_opened``) — the DOM contextmenu event itself
        has no world position. Mirrors the empty-space branch of ``on_pick``.
        """
        state = server.state
        if not state.edit_mode:
            state.status_message = "Enable edit mode to add atoms"
            return
        world = _ctx_menu_target["world"]
        if world is None:
            state.status_message = "Hover the spot in the viewport, then right-click"
            return
        if _ctx_menu_target["atom_idx"] >= 0:
            state.status_message = "Too close to an existing atom — pick empty space"
            return
        try:
            sdata = reader.read_structure()
        except Exception:
            return
        new_element = state.edit_new_element or "C"
        _record_undo(sdata, tolerate_failure=True)
        _add_atom_to_structure(reader, plotter, world, new_element)
        state.edit_selected = []
        state.status_message = f"Added {new_element} atom"
        _push_view(plotter)
        _live_opt_schedule()

    @ctrl.set("context_center_view")
    def context_center_view() -> None:
        """Center the camera on the right-clicked position.

        Pans (keeps direction and distance) so the targeted point — the
        hovered atom's centre, or the picked world position — becomes the
        focal point. This used to call the fit-all ``reset_camera`` and
        ignore the target entirely (#37-8).
        """
        target = _ctx_menu_target["world"]
        atom_idx = _ctx_menu_target["atom_idx"]
        if atom_idx is not None and atom_idx >= 0:
            with contextlib.suppress(Exception):
                target = list(reader.read_structure().atoms[atom_idx].position)
        if target is None:
            server.state.status_message = "Hover the spot in the viewport, then right-click"
            return
        cam = plotter.camera
        focal = np.asarray(cam.focal_point, dtype=float)
        shift = np.asarray(target, dtype=float)[:3] - focal
        cam.position = tuple(np.asarray(cam.position, dtype=float) + shift)
        cam.focal_point = tuple(focal + shift)
        plotter.render()
        _push_camera(plotter)
        server.state.status_message = "View centered"

    # ── Controller: fragment insertion (v1.3) ────────────────────────
    @ctrl.set("insert_fragment")
    def insert_fragment(name: str) -> None:
        """Insert a molecular fragment at the selected atom position."""
        if not name:
            return
        try:
            from vibeview.build_tools import FRAGMENTS
        except ImportError:
            server.state.status_message = "Build tools unavailable"
            return

        state = server.state
        try:
            sdata = reader.read_structure()
        except Exception:
            return

        if name not in FRAGMENTS:
            state.status_message = f"Unknown fragment: {name}"
            return

        fragment = FRAGMENTS[name]

        # Save undo state
        _record_undo(sdata)

        # Determine attachment point. Every FRAGMENTS entry has its base
        # atom at the relative origin, so the fragment must be offset from
        # the anchor by a bond length — the old code placed the base atom
        # exactly ON the anchor (or, unselected, on the molecule centre,
        # which for symmetric molecules is itself an atom site), and the
        # coincident pair crashed the scene rebuild with a NaN bond.
        allpos = np.array([a.position for a in sdata.atoms], dtype=float)
        if state.edit_selected:
            anchor_idx = state.edit_selected[-1]
            atom_pos = np.array(sdata.atoms[anchor_idx].position, dtype=float)
            # Grow outward: away from the centroid of the other atoms.
            centroid = allpos.mean(axis=0)
            outward = atom_pos - centroid
            norm = float(np.linalg.norm(outward))
            direction = outward / norm if norm > 1e-6 else np.array([1.0, 0.0, 0.0])
            anchor_pos = atom_pos + direction * 1.5
        else:
            # No anchor: set it down beside the structure, like the
            # library insert does.
            anchor_pos = np.array(
                [allpos[:, 0].max() + 2.5, allpos[:, 1].mean(), allpos[:, 2].mean()]
            )

        # Build new atom lists
        current_positions = [a.position.tolist() for a in sdata.atoms]
        current_symbols = [a.symbol for a in sdata.atoms]

        for frag_symbol, frag_rel_pos in fragment:
            abs_pos = (anchor_pos + np.array(frag_rel_pos, dtype=float)).tolist()
            current_positions.append(abs_pos)
            current_symbols.append(frag_symbol)

        _rebuild_structure_from_positions(reader, plotter, current_positions, current_symbols)
        state.edit_selected = []
        state.status_message = f"Inserted {name} fragment"
        _push_view(plotter)
        _live_opt_schedule()

    @ctrl.set("insert_library_structure")
    def insert_library_structure() -> None:
        """Insert a molecule from the qc structure library into the scene.

        Geometry comes from vibeqc_naming's curated DB (idealized, a starting
        point — same source as the toolbar molecule builder). Placed next to
        the last selected atom, or to the +x side of the current structure.
        """
        state = server.state
        name = (state.library_query or "").strip()
        if not name:
            return
        try:
            from vibeqc_naming import structure_from_name
        except ImportError:
            state.status_message = (
                "Structure library needs the vibeqc_naming package, which is not installed"
            )
            return
        try:
            atoms = structure_from_name(name)
        except Exception as e:  # noqa: BLE001 — a bad name must not kill the session
            state.status_message = f"Could not build {name!r}: {e}"
            return
        if not atoms:
            state.status_message = f"Unknown structure {name!r}"
            return
        from vibeview.elements import Z_TO_SYMBOL

        try:
            sdata = reader.read_structure()
        except Exception:
            return
        _record_undo(sdata)

        new_pos = np.array([[x, y, z] for _z, x, y, z in atoms], dtype=float)
        if state.edit_selected:
            anchor = np.array(
                sdata.atoms[state.edit_selected[-1]].position, dtype=float
            )
            target = anchor + np.array([2.5, 0.0, 0.0])
        else:
            allpos = np.array([a.position for a in sdata.atoms], dtype=float)
            target = np.array(
                [allpos[:, 0].max() + 3.0, allpos[:, 1].mean(), allpos[:, 2].mean()]
            )
        shifted = new_pos + (target - new_pos.mean(axis=0))

        cur_pos = [a.position.tolist() for a in sdata.atoms]
        cur_sym = [a.symbol for a in sdata.atoms]
        for (z, _x, _y, _zz), p in zip(atoms, shifted, strict=True):
            cur_sym.append(Z_TO_SYMBOL.get(int(z), "X"))
            cur_pos.append([float(p[0]), float(p[1]), float(p[2])])

        _rebuild_structure_from_positions(reader, plotter, cur_pos, cur_sym)
        state.edit_selected = []
        _remember_library_pick(state, name)
        state.status_message = (
            f"Inserted {name} ({len(atoms)} atoms) from the structure library"
        )
        _push_view(plotter)
        _live_opt_schedule()

    @ctrl.set("open_library_structure")
    def open_library_structure() -> None:
        """Open a library structure as a new file in the Files dropdown."""
        name = (server.state.library_query or "").strip()
        server.state.builder_name = server.state.library_query
        build_molecule()
        if name:
            _remember_library_pick(server.state, name)

    @ctrl.set("use_library_recent")
    def use_library_recent(name: str | None = None) -> None:
        """Re-select a recently used library structure from its chip."""
        name = _first(name)
        if name:
            server.state.library_query = str(name)

    @ctrl.set("toggle_library_favorite")
    def toggle_library_favorite(name: str | None = None) -> None:
        """Pin/unpin a library structure (star next to the search box)."""
        state = server.state
        name = _first(name)
        name = (str(name) if name else (state.library_query or "")).strip()
        if not name:
            state.status_message = "Pick a molecule to pin first"
            return
        favs = list(state.library_favorites or [])
        if name in favs:
            favs.remove(name)
            state.status_message = f"Unpinned {name}"
        else:
            favs.append(name)
            favs.sort()
            state.status_message = f"Pinned {name}"
        state.library_favorites = favs

    @ctrl.set("add_hydrogens")
    def add_hydrogens() -> None:
        """Saturate open valences on selected atoms with hydrogen."""
        from vibeview.build_tools import add_hydrogens as _add_H

        state = server.state
        try:
            sdata = reader.read_structure()
        except Exception:
            return

        # Save undo
        _record_undo(sdata)

        # Build atom dicts
        atoms = []
        for a in sdata.atoms:
            atoms.append(
                {
                    "symbol": a.symbol,
                    "position": a.position.tolist(),
                    "atomic_number": a.atomic_number or 0,
                }
            )

        # The button requires a selection and the manual promises "the
        # selected atoms"; every atom was saturated regardless (#37-17). An
        # empty selection (scripted/headless use) still saturates all.
        selection = [
            int(i) for i in (state.edit_selected or []) if 0 <= int(i) < len(atoms)
        ]
        new_atoms = _add_H(atoms, only=selection or None)
        added = len(new_atoms) - len(atoms)

        positions = [a["position"] for a in new_atoms]
        symbols = [a["symbol"] for a in new_atoms]

        _rebuild_structure_from_positions(reader, plotter, positions, symbols)
        state.edit_selected = []
        scope = f" to {len(selection)} selected atom(s)" if selection else ""
        state.status_message = (
            f"Added hydrogens{scope}: {added} added ({len(new_atoms)} atoms total)"
        )
        _push_view(plotter)
        _live_opt_schedule()

    @ctrl.set("edit_symmetrize")
    def edit_symmetrize() -> None:
        """Project the geometry onto its detected point group (undoable).

        Refuses honestly when no operations are found or the projection
        does not converge to an exactly invariant geometry. Deliberately
        does NOT reschedule live-opt: the optimizer knows nothing about
        symmetry and would immediately re-break what was just imposed.
        """
        from vibeview.symmetry import symmetrize_to_group

        state = server.state
        try:
            sdata = reader.read_structure()
        except Exception:
            return
        symbols = [a.symbol for a in sdata.atoms]
        positions = np.array([a.position for a in sdata.atoms], dtype=float)
        numbers = [a.atomic_number or 0 for a in sdata.atoms]
        res = symmetrize_to_group(symbols, positions, numbers)
        if not res.ok:
            state.status_message = f"Symmetrize: {res.reason}"
            return
        _cancel_live_opt_intent(clear_status=True)
        _record_undo(sdata)
        if res.positions is None:
            return
        _rebuild_structure_from_positions(
            reader, plotter, res.positions.tolist(), symbols
        )
        state.edit_selected = []
        detect_symmetry()  # refresh the Symmetry card for the new geometry
        # after the refresh — detect_symmetry writes its own status line
        state.status_message = (
            f"Symmetrized {res.symbol_before} → {res.symbol_after} "
            f"({res.n_operations} operations, max shift {res.max_shift:.3f} Å)"
        )
        _push_view(plotter)

    @ctrl.set("build_supercell")
    def build_supercell() -> None:
        """Replicate the structure into a supercell."""
        from vibeview.build_tools import build_supercell as _sc

        state = server.state
        nx = max(1, int(state.build_supercell_nx or 1))
        ny = max(1, int(state.build_supercell_ny or 1))
        nz = max(1, int(state.build_supercell_nz or 1))
        state.build_supercell_dialog = False

        try:
            sdata = reader.read_structure()
        except Exception:
            return
        _cancel_live_opt_run(clear_status=True)

        _record_undo(sdata)

        atoms = []
        for a in sdata.atoms:
            atoms.append(
                {
                    "symbol": a.symbol,
                    "position": a.position.tolist(),
                    "atomic_number": a.atomic_number or 0,
                }
            )

        scaled_lattice = None
        if sdata.lattice_vectors is not None:
            lattice_array = np.asarray(sdata.lattice_vectors, dtype=float)
            if any(sdata.pbc):
                # A lower-dimensional QVF still carries three lattice rows,
                # but the non-periodic rows are synthesized bookkeeping. Do
                # not duplicate atoms or scale a vacuum/normal direction the
                # calculation did not treat as periodic.
                nx, ny, nz = clamp_replication((nx, ny, nz), sdata.pbc)
                scaled_lattice = lattice_array * np.asarray(
                    (nx, ny, nz), dtype=float
                )[:, None]
            lattice = lattice_array.tolist()
        else:
            # Estimate a bounding box for non-periodic systems
            positions_arr = np.array([a["position"] for a in atoms], dtype=float)
            box_min = positions_arr.min(axis=0)
            box_max = positions_arr.max(axis=0)
            cell = box_max - box_min + 5.0  # padding
            lattice = [
                [float(cell[0]), 0.0, 0.0],
                [0.0, float(cell[1]), 0.0],
                [0.0, 0.0, float(cell[2])],
            ]

        new_atoms = _sc(atoms, lattice, (nx, ny, nz))

        positions = [a["position"] for a in new_atoms]
        symbols = [a["symbol"] for a in new_atoms]

        # ``viewer_state.replication`` is a display-only tiling of the current
        # structure. Building a supercell commits that tiling into new atoms
        # and lattice vectors, so retaining an earlier display replication
        # would apply it a second time on the next canonical scene rebuild.
        # The immediate editor renderer always shows one committed cell; keep
        # controller state and every later renderer on that same definition.
        _set_replication((1, 1, 1))
        _rebuild_structure_from_positions(
            reader,
            plotter,
            positions,
            symbols,
            lattice_vectors=scaled_lattice,
        )
        state.edit_selected = []
        state.status_message = f"Supercell {nx}x{ny}x{nz}: {len(new_atoms)} atoms"
        _push_view(plotter)
        _live_opt_schedule()

    # ── Controller: calculation-parameter panel ────────────────────────
    @ctrl.set("export_py")
    def export_py() -> None:
        """Export current structure + parameters as a vibe-qc .py input script."""
        try:
            from vibeview.input_generator import (
                _structure_calculation_params,
                generate_input_script,
            )
        except ImportError:
            server.state.status_message = "Input generator not available"
            return

        # Build atoms list from the structure reader
        try:
            sdata = reader.read_structure()
        except Exception:
            server.state.status_message = "No structure data to export"
            return

        template = server.state.calc_template or "single_point"

        try:
            params = {
                **_structure_calculation_params(sdata),
                "basis": server.state.calc_basis or "sto-3g",
                "method": server.state.calc_method or "rhf",
                "functional": server.state.calc_functional or "",
                "charge": int(server.state.calc_charge or 0),
                "multiplicity": int(server.state.calc_multiplicity or 1),
                "title": "vibe-view generated input",
            }
            script = generate_input_script(template, params)
        except Exception as e:
            server.state.status_message = f"Failed to generate script: {e}"
            return

        # Offered like every other download, so it is listed under Recent
        # Exports and cleared after delivery; it used to skip both (#37-39).
        _offer_download(
            script.encode(),
            "text/plain",
            "vibe-qc_input.py",
            label="vibe-qc input script",
            fmt="PY",
        )
        server.state.status_message = "Input script ready for download"

    # ── Controller: raytracing toggle ─────────────────────────────────
    @ctrl.set("toggle_raytrace")
    def toggle_raytrace() -> None:
        """Toggle OSPRay raytraced rendering on/off.

        PyVista has no ``enable_ray_tracing`` method — OSPRay is wired by
        attaching a ``vtkOSPRayPass`` to the renderer. Guarded by the
        startup availability probe so it never throws on builds without
        OSPRay (the button is also hidden in that case).
        """
        if not server.state.raytrace_available:
            server.state.status_message = "Ray tracing unavailable: this VTK build has no OSPRay"
            server.state.raytrace_enabled = False
            return
        enabled = not server.state.raytrace_enabled
        try:
            import vtk

            renderer = plotter.renderer
            if enabled:
                plotter._ospray_pass = vtk.vtkOSPRayPass()
                renderer.SetPass(plotter._ospray_pass)
                server.state.status_message = "Ray tracing enabled (OSPRay)"
            else:
                renderer.SetPass(None)
                server.state.status_message = "Ray tracing disabled"
            server.state.raytrace_enabled = enabled
            _counted_view_update()
        except Exception as e:
            server.state.status_message = f"Ray tracing unavailable: {e}"
            server.state.raytrace_enabled = False

    @ctrl.set("toggle_ssao")
    def toggle_ssao(enabled: bool | None = None) -> None:
        """Toggle screen-space ambient occlusion."""
        state = server.state
        state.ssao_enabled = (not state.ssao_enabled) if enabled is None else bool(enabled)
        if state.ssao_enabled:
            try:
                from vibeview.material_presets import ambient_occlusion_pass

                ambient_occlusion_pass(plotter, **_SSAO_PASS)
                state.status_message = "Ambient occlusion on (exported images)"
            except Exception:
                state.status_message = "Ambient occlusion unavailable on this VTK build"
                state.ssao_enabled = False
        else:
            # Restore default render pass
            with contextlib.suppress(Exception):
                plotter.renderer.SetPass(None)
            state.status_message = "Ambient occlusion off"
        # Refresh the framebuffer now: screenshots taken from the current
        # frame otherwise show the pre-toggle pass state.
        with contextlib.suppress(Exception):
            plotter.render()
        _push_view(plotter)

    @ctrl.set("toggle_toon")
    def toggle_toon(enabled: bool | None = None) -> None:
        """Toggle non-photorealistic toon rendering.

        Wired like ``toggle_clip``: the switch sends
        ``update_modelValue=(ctrl.toggle_toon, "[$event]")`` *after* its
        ``v_model`` already wrote ``toon_mode``, so with an argument we
        set (never flip — a second flip would cancel the toggle out).
        The old zero-argument signature made every switch flip raise
        TypeError server-side, which is why the switch visibly did
        nothing. No argument (palette / keyboard use) still flips.
        """
        state = server.state
        state.toon_mode = (not state.toon_mode) if enabled is None else bool(enabled)
        if state.toon_mode:
            from vibeview.renderers.structure import enable_toon_rendering

            ok = enable_toon_rendering(plotter)
            state.status_message = (
                "Toon rendering enabled" if ok else "Toon rendering not available"
            )
            if not ok:
                state.toon_mode = False
        else:
            from vibeview.renderers.structure import disable_toon_rendering

            disable_toon_rendering(plotter)
            state.status_message = "Toon rendering disabled"
        _push_view(plotter)

    @ctrl.set("toggle_orthographic")
    def toggle_orthographic(enabled: bool | None = None) -> None:
        """Switch between perspective and orthographic (parallel) projection.

        Orthographic drops perspective foreshortening — parallel lattice
        edges stay parallel — which is what crystallography and publication
        figures want (design refresh 2026, rendering options).
        """
        state = server.state
        state.orthographic_projection = (
            (not state.orthographic_projection) if enabled is None else bool(enabled)
        )
        # Best-effort: no camera headless, and the client push mirrors
        # camera_preset so VtkLocalView picks up the projection (audit L1).
        with contextlib.suppress(Exception):
            plotter.camera.parallel_projection = bool(state.orthographic_projection)
            plotter.render()
            push = _scene_hooks(plotter).push_camera
            if push is not None:
                push()
        state.status_message = (
            "Orthographic projection"
            if state.orthographic_projection
            else "Perspective projection"
        )

    @ctrl.set("show_memory")
    def show_memory() -> None:
        """Display the decoded arrays the active reader holds, by section kind."""
        kinds = {s.id: s.kind for s in reader.sections}
        usage: dict[str, int] = {}
        for section_id, size in reader.cached_bytes().items():
            kind = kinds.get(section_id, section_id)
            usage[kind] = usage.get(kind, 0) + size
        lines = ["Memory usage by section kind:"]
        for kind, size in sorted(usage.items(), key=lambda x: -x[1]):
            lines.append(f"  {kind}: {human_size(size)}")
        lines.append(f"  Total: {human_size(sum(usage.values()))}")
        server.state.memory_usage = "\n".join(lines)
        server.state.status_message = server.state.memory_usage.split("\n")[0]

    # ── Controller: display toggles ───────────────────────────────────
    # ``toggle_atom_labels`` is wired earlier via ``@ctrl.set`` so the
    # tuple-form ``update_modelValue=(ctrl.toggle_atom_labels, "[$event]")``
    # binding on the right-panel switch actually routes to the function.
    # The previous ``@ctrl.trigger`` registration + ``_add_atom_labels``
    # helper here was a no-op (FunctionNotImplementedError on click — the
    # proxy was registered as a trigger without a func, AND the helper
    # read ``structure.payload`` which doesn't exist on Section).
    @ctrl.set("toggle_background")
    def toggle_background() -> None:
        """Toggle between dark and light 3D viewport background."""
        dark = not server.state.dark_background
        _set_background_mode(plotter, server.state, dark)
        server.state.status_message = f"Background: {'dark' if dark else 'light'}"
        _counted_view_update()

    # @ctrl.set alone is not client-callable; the keyboard-shortcut JS
    # invokes trame.trigger("camera_preset"), which needs a trigger registration.
    @ctrl.trigger("camera_preset")
    @ctrl.set("camera_preset")
    def camera_preset(view: Any) -> None:
        """Set the camera to a named preset view.

        Receives a plain string from the client (JS ``'isometric'``,
        not ``['isometric']``).  Also handles the list-wrapped legacy
        path for robustness during migration.
        """
        view = _first(view)
        presets: dict[str, Callable[[], Any]] = {
            "isometric": lambda: view_isometric(plotter),
            "xy": lambda: view_xy(plotter),
            "xz": lambda: view_xz(plotter),
            "yz": lambda: view_yz(plotter),
            "reset": lambda: reset_camera(plotter),
        }
        fn = presets.get(view)
        if fn is not None:
            fn()
            plotter.render()
            server.state.status_message = f"Camera: {view}"
            _counted_view_update()
            ctrl.view_push_camera()  # move the client's own camera, not just geometry (L1)

    # ── Controller: file watcher / hot-reload ─────────────────────────
    # (These were accidentally nested inside camera_preset's body, so they
    # only registered after the user first hit a camera preset button.)

    # Readers replaced during hot-reload stay alive only until the fresh
    # reader owns every controller and any viewer-side overlays. Keeping them
    # beyond that handoff leaks one archive handle per panel-only refresh.
    _retired_readers: list[Any] = []

    def _close_retired_readers() -> None:
        while _retired_readers:
            with contextlib.suppress(Exception):
                _retired_readers.pop().close()

    @ctrl.set("apply_watcher_event")
    def _apply_qvf_change(event: Any) -> None:
        """Hot-reload the viewer from a settled on-disk change.

        Swaps in a fresh :class:`QVFReader` — the old reader holds an open
        zip handle on the *replaced* archive and caches decoded members,
        so reusing it after a rewrite renders stale data. Then reloads
        only what the per-section diff says moved (roadmap C1):

        * changes confined to 2D-panel sections (SCF history growing, a
          spectrum updating) refresh just the active panel — the 3D scene,
          camera, and selection aren't touched at all;
        * anything else (structure moved, sections added/removed, 3D
          sections changed, non-QVF content) runs the full reload with the
          user's camera preserved, then restores the active section.
        """
        nonlocal reader
        from vibeview.qvf import QVFError, QVFReader

        state = server.state
        idx = state.active_file_idx
        active_reader = _all_readers[idx]
        if active_reader.path is None or str(active_reader.path) != event.path:
            return  # the user switched files since the watcher started
        expected_reader = active_reader
        try:
            fresh = QVFReader(event.path)
        except QVFError as e:
            state.status_message = f"Auto-reload failed: {e}"
            return
        # Opening the replacement archive does not touch shared render state,
        # but swapping the closure-held reader does. Serialize that swap with
        # computed surfaces so their final reader/section guard cannot become
        # stale between validation and actor cleanup. Revalidate after taking
        # the lock because a file switch may have completed while ``fresh``
        # was opening or while this callback waited for a grid evaluation.
        with _wf_render_lock:
            state = server.state
            idx = state.active_file_idx
            active_reader = _all_readers[idx]
            if (
                active_reader is not expected_reader
                or active_reader.path is None
                or str(active_reader.path) != event.path
            ):
                fresh.close()
                return

            kinds = {s.id: s.kind for s in fresh.sections}
            # Light path only when every changed section drives a 2D side
            # panel: those re-materialize fully on activate. 3D kinds
            # (volume.*, trajectory, vibrations, …) re-read from the reader on
            # later interactions, so they need the full rebuild.
            panel_kinds = _two_d_panel_kinds()
            panel_only = (
                event.is_qvf
                and not event.added_sections
                and not event.removed_sections
                and all(
                    kinds.get(sid) in panel_kinds
                    for sid in event.changed_sections
                )
            )
            if panel_only:
                # Re-localized wavefunctions exist only in this viewer
                # session. A panel-only archive refresh cannot invalidate
                # them, so carry their synthetic sections and payloads onto
                # the fresh reader before swapping the closure. Full reloads
                # intentionally discard them because 3D/source data changed.
                try:
                    # The panel-only path deliberately keeps the current 3D
                    # scene. Carry its viewer-side structure overlay onto the
                    # fresh archive reader too, or export and the next edit
                    # would silently snap back to the pristine file while the
                    # viewport still showed the edited geometry. The matching
                    # undo/redo stacks stay valid for this same logical file.
                    if active_reader.has_edit_overlay:
                        snapshot = _structure_edit_snapshot(
                            active_reader.read_structure()
                        )
                        fresh.set_edit_overlay(
                            snapshot["positions"],
                            snapshot["symbols"],
                            lattice_vectors=snapshot["lattice_vectors"],
                        )
                    for overlay_id in active_reader.wavefunction_overlay_ids:
                        fresh.set_wavefunction_overlay(
                            overlay_id,
                            active_reader.read_wavefunction_gto(overlay_id),
                        )
                except QVFError as e:
                    fresh.close()
                    state.status_message = f"Auto-reload failed: {e}"
                    return

            _all_readers[idx] = fresh
            _retired_readers.append(active_reader)

            previously_active = state.selected_section
            touched = set(event.touched_sections)
            prev_run_status = state.live_run_status
            # Follow the head of a growing trajectory: if the user sat on the
            # last frame before the reload, jump to the new last frame after.
            was_at_head = (
                state.trajectory_n_frames > 0
                and state.trajectory_frame >= state.trajectory_n_frames - 1
            )
            if panel_only:
                _cancel_live_opt_for_reader_change()
                _cancel_relocalization()
                if reader is active_reader:
                    reader = fresh  # handlers read the closure-captured nonlocal
                if previously_active and previously_active in touched:
                    activate_section(previously_active)
                _update_live_status(fresh)
            else:
                # also updates live status
                _reload_active_file(preserve_camera=True, same_file=True)
                if previously_active and previously_active in kinds:
                    activate_section(previously_active)
                    if (
                        was_at_head
                        and kinds.get(previously_active) == "trajectory"
                        and state.trajectory_n_frames > 0
                    ):
                        set_trajectory_frame(state.trajectory_n_frames - 1)
            _close_retired_readers()
            what = ", ".join(sorted(touched)) if touched else "manifest"
            if prev_run_status == "running" and state.live_run_status in (
                "converged",
                "failed",
            ):
                state.status_message = (
                    f"Job {state.live_run_status} — final results loaded ({what})"
                )
            else:
                state.status_message = f"Reloaded from disk ({what})"

    _watcher_task = None
    _watcher_path: str | None = None

    def _watcher_task_is_running(task: asyncio.Task[Any] | None) -> bool:
        """Return whether *task* still owns an active polling coroutine."""
        if task is None:
            return False
        done = getattr(task, "done", None)
        return not bool(done()) if callable(done) else True

    def _stop_file_watcher() -> None:
        """Cancel the task that owns the previous archive path, if any."""
        nonlocal _watcher_task, _watcher_path
        task = _watcher_task
        _watcher_task = None
        _watcher_path = None
        if task is not None and _watcher_task_is_running(task):
            task.cancel()

    def _sync_file_watcher_for_active_reader() -> str | None:
        """Make watcher ownership match the current reader and UI state."""
        nonlocal _watcher_task, _watcher_path
        state = server.state
        desired_path = str(reader.path) if reader.path is not None else None

        if not state.file_watcher_enabled:
            _stop_file_watcher()
            return None
        if desired_path is None:
            # An in-memory reader has no file the server can poll. Leaving the
            # switch enabled would claim that auto-reload is active when the
            # old path is still the only one being watched.
            _stop_file_watcher()
            state.file_watcher_enabled = False
            return "Auto-reload unavailable for an in-memory file"
        if (
            _watcher_task_is_running(_watcher_task)
            and _watcher_path == desired_path
        ):
            return None

        _stop_file_watcher()

        from vibeview.file_watcher import QVFChangeTracker

        tracker = QVFChangeTracker(desired_path)
        watch_path = desired_path
        _watcher_path = watch_path

        async def _watch() -> None:
            while (
                server.state.file_watcher_enabled
                and _watcher_path == watch_path
            ):
                # Poll fast while a write is settling so the reload lands
                # ~settle_delay after the writer finishes.
                interval = float(server.state.file_watcher_interval or 5.0)
                await asyncio.sleep(0.25 if tracker.has_pending else interval)
                if (
                    not server.state.file_watcher_enabled
                    or _watcher_path != watch_path
                ):
                    break
                event = tracker.poll()
                if event is not None:
                    _apply_qvf_change(event)
                    server.state.flush()

        def _watch_finished(task: asyncio.Task[Any]) -> None:
            """Release ownership and report an unexpected watcher exit."""
            nonlocal _watcher_task, _watcher_path
            cancelled = getattr(task, "cancelled", None)
            if callable(cancelled) and cancelled():
                return
            error = None
            exception = getattr(task, "exception", None)
            if callable(exception):
                try:
                    error = exception()
                except asyncio.CancelledError:
                    return
            if task is not _watcher_task:
                return
            _watcher_task = None
            _watcher_path = None
            state.file_watcher_enabled = False
            if error is None:
                state.status_message = "Auto-reload stopped"
            else:
                state.status_message = f"Auto-reload stopped: {error}"
            state.flush()

        coroutine = _watch()
        try:
            task = asyncio.ensure_future(coroutine)
        except RuntimeError as error:
            # A watcher cannot truthfully remain enabled without a scheduler.
            coroutine.close()
            _watcher_task = None
            _watcher_path = None
            state.file_watcher_enabled = False
            return f"Auto-reload unavailable: {error}"
        _watcher_task = task
        add_done_callback = getattr(task, "add_done_callback", None)
        if callable(add_done_callback):
            add_done_callback(_watch_finished)
        return None

    @ctrl.set("toggle_file_watcher")
    def toggle_file_watcher(enabled: bool | None = None) -> None:
        """Enable/disable auto-reload of the active file on disk changes."""
        state = server.state
        state.file_watcher_enabled = (
            (not state.file_watcher_enabled) if enabled is None else bool(enabled)
        )
        sync_status = _sync_file_watcher_for_active_reader()
        if sync_status is not None:
            state.status_message = sync_status
        elif state.file_watcher_enabled:
            state.status_message = "Auto-reload enabled"
        else:
            state.status_message = "Auto-reload disabled"

    # ── Controller: dark UI theme ─────────────────────────────────────
    @ctrl.set("toggle_dark_ui")
    def toggle_dark_ui(enabled: bool | None = None) -> None:
        """Toggle dark mode for the UI chrome (not the 3D viewport)."""
        state = server.state
        state.ui_dark_mode = (not state.ui_dark_mode) if enabled is None else bool(enabled)
        state.status_message = f"UI theme: {'dark' if state.ui_dark_mode else 'light'}"

    # ── UI Layout ─────────────────────────────────────────────────────

    from vibeview.app.layout import build_layout

    build_layout(server, ctrl, plotter)

    # ── About info ──────────────────────────────────────────────────
    import platform

    import pyvista

    from vibeview.codenames import version_label

    server.state.about_version = f"vibe-view {version_label()}"
    server.state.about_system_info = (
        f"Python {platform.python_version()}\n"
        f"PyVista {pyvista.__version__}\n"
        f"OS {platform.system()} {platform.release()}"
    )
    try:
        import psutil

        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.1)
        server.state.about_system_info += (
            f"\nCPU: {cpu:.0f}% | RAM: {mem.used // (1024**3)}/{mem.total // (1024**3)} GB"
        )
    except ImportError:
        pass  # psutil not available

    # ── Auto-save session ───────────────────────────────────────────
    @ctrl.set("auto_save_session")
    def auto_save_session() -> None:
        """Auto-save the current session state periodically."""
        import json
        import time
        from pathlib import Path

        state = server.state
        if not state.session_path:
            state.session_path = str(_default_session_path())

        # Same document the explicit save writes (see _session_payload): this
        # rewrites the very file the user saved, so emitting a different
        # schema/version here silently destroyed their session.
        session_data = _session_payload()
        session_data["timestamp"] = time.time()

        try:
            Path(state.session_path).write_text(json.dumps(session_data, indent=2))
            state.session_dirty = False
        except OSError:
            pass  # Silently skip if file can't be written

    # ── Controller: persistent settings ────────────────────────────
    @ctrl.set("save_relocalize_backend")
    def save_relocalize_backend() -> bool:
        from vibeview.settings import set_setting

        state = server.state
        path = str(state.settings_relocalize_backend_python or "").strip()
        if path and not Path(path).expanduser().is_absolute():
            state.relocalize_backend_status = "The backend Python path must be absolute"
            return False
        try:
            set_setting("relocalize_backend_python", path)
        except OSError as exc:
            state.relocalize_backend_status = f"Could not save backend setting: {exc}"
            return False
        _cancel_relocalization()
        state.relocalize_backend_python = path
        check_relocalize_backend()
        return True

    def _apply_energy_unit(unit: str) -> None:
        """Re-label every open energy readout in ``unit`` (#58).

        The picker keeps its selection: row values are ``"{spin}:{index}"``
        keys, which a unit change does not touch.
        """
        state = server.state
        state.energy_unit = unit
        section_id = state.wf_section_id
        if not section_id or not reader.has_section(section_id):
            return
        from vibeview.renderers.wavefunction import WavefunctionRenderer

        renderer = WavefunctionRenderer(reader.get_section(section_id), reader)
        try:
            state.wf_mo_rows = renderer.mo_table(energy_unit=unit)
        except QVFError as exc:
            state.status_message = f"Error loading wavefunction: {exc}"
            return
        if state.chart_title == "Orbital Energies":
            show_energy_diagram()

    def _apply_length_unit(unit: str) -> None:
        """Re-render an open measurement readout in ``unit`` (#58).

        Interactive picks are re-measured from the current geometry; the
        bond table from the ruler button is rebuilt. Anything else in the
        panel (the hint, an error) has no length in it and is left alone.
        """
        state = server.state
        state.measure_unit = unit
        if state.measure_mode and state.selected_atoms:
            try:
                sdata = reader.read_structure()
            except QVFError:
                return
            picked = [i for i in state.selected_atoms if 0 <= i < len(sdata.atoms)]
            state.measure_result = _measure_text(sdata, picked, unit)
        elif str(state.measure_result or "").startswith(_BOND_DISTANCES_HEADER):
            compute_bonds()

    def _apply_band_energy_unit(unit: str) -> None:
        """Redraw the bands / DOS panel with its energy axis in ``unit`` (#58).

        A window the reader typed is carried over into the new unit first,
        so the chart keeps the same view.
        """
        state = server.state
        old = state.band_energy_unit
        for key in ("bands_emin", "bands_emax"):
            value = _coerce_window_bound(getattr(state, key))
            if value is not None:
                setattr(state, key, energy_from_ev(energy_to_ev(value, old), unit))
        state.band_energy_unit = unit
        state.band_energy_label = energy_label(unit)
        _redraw_bands_panel()

    @ctrl.set("save_settings")
    def save_settings() -> None:
        """Persist user settings to disk."""
        from vibeview.settings import load_settings
        from vibeview.settings import save_settings as _save

        if not save_relocalize_backend():
            return
        settings = load_settings()
        settings.update(
            {
                "dark_background": server.state.settings_dark_background,
                "material_preset": server.state.settings_material_preset,
                "auto_save_interval": _auto_save_interval_seconds(
                    server.state.settings_auto_save_interval
                ),
                "energy_unit": _energy_unit_key(server.state.settings_energy_unit),
                "measure_unit": _length_unit_key(server.state.settings_measure_unit),
                "band_energy_unit": _band_energy_unit_key(
                    server.state.settings_band_energy_unit
                ),
            }
        )
        _save(settings)
        server.state.settings_dialog = False

        # Apply immediately
        new_unit = _energy_unit_key(server.state.settings_energy_unit)
        if new_unit != server.state.energy_unit:
            _apply_energy_unit(new_unit)
        new_length_unit = _length_unit_key(server.state.settings_measure_unit)
        if new_length_unit != server.state.measure_unit:
            _apply_length_unit(new_length_unit)
        new_band_unit = _band_energy_unit_key(server.state.settings_band_energy_unit)
        if new_band_unit != server.state.band_energy_unit:
            _apply_band_energy_unit(new_band_unit)
        server.state.status_message = "Settings saved"

        if server.state.settings_dark_background != server.state.dark_background:
            _set_background_mode(
                plotter, server.state, bool(server.state.settings_dark_background)
            )
            _push_view(plotter)

    for _auto_id in viewer_state.auto_open:
        if not reader.has_section(_auto_id):
            continue
        activate_section(_auto_id)
        if server.state.selected_section == _auto_id and not str(
            server.state.status_message or ""
        ).startswith("Could not open"):
            server.state.status_message = f"Auto-opened: {_auto_id}"

    async def _auto_save_loop() -> None:
        """Auto-save the session at the configured interval (Settings dialog).

        The interval is re-read every cycle, so a saved change applies from
        the next save on; it was a hard-coded 60 s (#37-3).
        """
        while True:
            await asyncio.sleep(
                _auto_save_interval_seconds(server.state.settings_auto_save_interval)
            )
            try:
                if server.state.session_path:
                    auto_save_session()
            except Exception:
                pass

    # Start auto-save loop
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_auto_save_loop())
    except RuntimeError:
        pass  # No event loop (e.g. in offscreen tests)

    return server








# ── Helpers ──────────────────────────────────────────────────────────────


_SUBSCRIPT = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def _present_elements(reader: QVFReader) -> list[dict[str, Any]]:
    """Elements present in the structure, as picker items.

    ``[{"title": "C", "value": 6}, ...]`` in ascending atomic number — the
    source list for the per-element colour override picker (design refresh
    2026). Empty when the file has no structure section.
    """
    structure_section = next((s for s in reader.sections if s.kind == "structure"), None)
    if structure_section is None:
        return []
    try:
        # Picker metadata must not trigger bond inference on a large protein.
        atoms = _current_structure(reader).atoms
    except Exception:  # noqa: BLE001 — picker is cosmetic; never block load
        return []
    seen: dict[int, str] = {}
    for a in atoms:
        z = int(getattr(a, "atomic_number", 0) or 0)
        if z and z not in seen:
            seen[z] = str(a.symbol)
    return [{"title": seen[z], "value": z} for z in sorted(seen)]


def _chemical_formula(reader: QVFReader) -> str:
    """Hill-order chemical formula (C, H, then alphabetical) with Unicode
    subscripts. Empty string if no structure section is present.
    """
    structure_section = next((s for s in reader.sections if s.kind == "structure"), None)
    if structure_section is None:
        return ""
    try:
        atoms = _current_structure(reader).atoms
    except QVFError:
        return ""
    counts: dict[str, int] = {}
    for a in atoms:
        counts[a.symbol] = counts.get(a.symbol, 0) + 1
    # Hill: C first, then H, then the rest alphabetical
    ordered: list[str] = []
    if "C" in counts:
        ordered.append("C")
        if "H" in counts:
            ordered.append("H")
        ordered.extend(sorted(s for s in counts if s not in ("C", "H")))
    else:
        ordered.extend(sorted(counts))
    parts = []
    for sym in ordered:
        n = counts[sym]
        parts.append(sym if n == 1 else f"{sym}{str(n).translate(_SUBSCRIPT)}")
    return "".join(parts)


# Command-palette actions (design refresh 2026). Each entry dispatches to an
# existing controller in run_palette_action; keep the ids in sync there.
_PALETTE_ACTIONS = [
    {"id": "cam_iso", "title": "Camera: Isometric view", "sub": "Camera", "icon": "mdi-axis-arrow"},
    {"id": "cam_top", "title": "Camera: Top (XY)", "sub": "Camera", "icon": "mdi-arrow-down-bold"},
    {"id": "cam_front", "title": "Camera: Front (XZ)", "sub": "Camera",
     "icon": "mdi-arrow-right-bold"},
    {"id": "cam_side", "title": "Camera: Side (YZ)", "sub": "Camera",
     "icon": "mdi-arrow-left-bold"},
    {"id": "cam_reset", "title": "Camera: Reset / fit", "sub": "Camera", "icon": "mdi-fit-to-page"},
    {"id": "view_pub", "title": "View preset: Publication", "sub": "Rendering",
     "icon": "mdi-white-balance-sunny"},
    {"id": "view_pres", "title": "View preset: Presentation", "sub": "Rendering",
     "icon": "mdi-projector-screen"},
    {"id": "view_ana", "title": "View preset: Analysis", "sub": "Rendering",
     "icon": "mdi-magnify-scan"},
    {"id": "mo_homo", "title": "Render HOMO", "sub": "Orbitals", "icon": "mdi-atom"},
    {"id": "mo_lumo", "title": "Render LUMO", "sub": "Orbitals", "icon": "mdi-atom-variant"},
    {"id": "energy_diagram", "title": "Show orbital energy diagram", "sub": "Orbitals",
     "icon": "mdi-chart-timeline-variant"},
    {"id": "labels", "title": "Toggle atom labels", "sub": "Display", "icon": "mdi-label-outline"},
    {"id": "background", "title": "Toggle dark / light background", "sub": "Display",
     "icon": "mdi-theme-light-dark"},
    {"id": "ui_theme", "title": "Toggle dark / light UI theme", "sub": "Display",
     "icon": "mdi-palette-outline"},
    {"id": "orthographic", "title": "Toggle orthographic projection", "sub": "Rendering",
     "icon": "mdi-cube-outline"},
    {"id": "screenshot", "title": "Export screenshot", "sub": "Export", "icon": "mdi-camera"},
    # Geometry / scene export formats (same handler as the toolbar Export
    # menu) + the vibe-qc input script.
    {"id": "exp_obj", "title": "Export scene: Wavefront OBJ", "sub": "Export",
     "icon": "mdi-cube-outline"},
    {"id": "exp_gltf", "title": "Export scene: glTF", "sub": "Export", "icon": "mdi-rotate-3d"},
    {"id": "exp_xyz", "title": "Export geometry: XYZ", "sub": "Export", "icon": "mdi-axis-arrow"},
    {"id": "exp_cif", "title": "Export crystal: CIF", "sub": "Export",
     "icon": "mdi-diamond-stone"},
    {"id": "exp_pov", "title": "Export scene: POV-Ray", "sub": "Export", "icon": "mdi-camera-iris"},
    {"id": "exp_blend", "title": "Export scene: Blender script", "sub": "Export",
     "icon": "mdi-blender-software"},
    {"id": "exp_svg", "title": "Export vector graphic: SVG", "sub": "Export", "icon": "mdi-svg"},
    {"id": "exp_cml", "title": "Export markup: CML", "sub": "Export", "icon": "mdi-xml"},
    {"id": "exp_py", "title": "Export vibe-qc input (.py)", "sub": "Export",
     "icon": "mdi-language-python"},
    {"id": "symmetry", "title": "Detect point group", "sub": "Analysis",
     "icon": "mdi-mirror-rectangle"},
    {"id": "symmetrize", "title": "Symmetrize geometry to point group", "sub": "Analysis",
     "icon": "mdi-mirror"},
]


def _palette_actions_for(reader: QVFReader) -> list[dict[str, Any]]:
    """The static quick actions plus one 'Open section' entry per section
    of the loaded file, so Ctrl/Cmd+K can jump anywhere by name (design
    refresh 2026: palette open-section actions)."""
    return _PALETTE_ACTIONS + [
        {
            "id": f"open:{sec.id}",
            "title": f"Open section: {sec.id} ({sec.kind})",
            "sub": "Sections",
            "icon": _kind_icon(sec.kind),
        }
        for sec in reader.sections
    ]


_LIBRARY_RECENT_MAX = 6


def _remember_library_pick(state: _ViewerUIState, name: str) -> None:
    """Push *name* onto the most-recently-used library list.

    Most-recent-first, de-duplicated, capped at _LIBRARY_RECENT_MAX so the
    chip row stays one line. Session-scoped — deliberately not persisted to
    disk, since the viewer keeps no per-user settings file today.
    """
    name = (name or "").strip()
    if not name:
        return
    recent = [n for n in (state.library_recent or []) if n != name]
    recent.insert(0, name)
    state.library_recent = recent[:_LIBRARY_RECENT_MAX]


def _library_structure_names() -> list[str]:
    """Molecule names in vibeqc_naming's curated structure DB ([] if absent)."""
    try:
        from vibeqc_naming import known_names
    except ImportError:
        return []
    return sorted(known_names())


#: Display names for readers with no path of their own: a browser upload
#: keeps the name of the file the user picked.
_READER_NAMES: weakref.WeakKeyDictionary[Any, str] = weakref.WeakKeyDictionary()


def _job_name(reader: QVFReader) -> str:
    """Best-effort short job name. Uses the QVF filename stem when the
    archive is on disk, else the uploaded file's stem; falls back to
    ``<in-memory>`` otherwise.

    Structures made by the toolbar molecule builder have no path, so they
    carry their name in ``manifest.source.calculation`` as ``builder:<name>``.
    """
    if reader.path is not None:
        return reader.path.stem
    try:
        uploaded = _READER_NAMES.get(reader)
    except TypeError:  # a test double that cannot be weakly referenced
        uploaded = None
    return uploaded or _builder_name(reader) or "<in-memory>"


def _system_descriptor(reader: QVFReader) -> str:
    """Describe what the file represents, in parentheses next to the job
    name: ``reaction`` / ``trajectory`` when the file has the corresponding
    section, otherwise the chemical formula. Empty when neither is known.
    """
    kinds = {s.kind for s in reader.sections}
    if "reaction.path" in kinds or "reaction.waypoints" in kinds:
        return "reaction"
    if "trajectory" in kinds:
        return "trajectory"
    return _chemical_formula(reader)


def _header_title(reader: QVFReader) -> str:
    """Compose the AppBar title from job + system + calculation."""
    bits = ["vibe-view", _job_name(reader)]
    descriptor = _system_descriptor(reader)
    if descriptor:
        bits[-1] = f"{bits[-1]} ({descriptor})"
    bits.append(reader.source.calculation)
    return " — ".join(bits)


def _run_info_text(reader: QVFReader) -> str:
    """Human-readable run summary from the manifest ``provenance`` block.

    The QVF already carries total energy, SCF convergence, method/basis,
    charge/multiplicity, electron count and wall time — vibe-view just
    wasn't surfacing them. Reads whatever keys are present so it degrades
    gracefully as the writer adds more (e.g. SCF iteration count).
    """
    prov = reader.provenance
    if not prov:
        return ""
    lines: list[str] = []
    # Lifecycle status (spec § 3.2) — leads the card so a pending
    # container reads as "not yet run" before any result fields.
    run_status = prov.get("run_status")
    if run_status == "pending":
        lines.append("Status       : pending — job not yet run")
    elif isinstance(run_status, str) and run_status:
        lines.append(f"Status       : {run_status}")
    # "Done" = terminal status + a complete latest run.record; surface
    # any inconsistency instead of silently reading as done.
    for warning in reader.lifecycle_warnings():
        lines.append(f"WARNING      : {warning}")
    history = reader.run_record_sections()
    if len(history) > 1:
        lines.append(
            f"Runs         : {len(history)} (latest: {history[-1].id})"
        )
    method = prov.get("method")
    basis = prov.get("basis")
    func = prov.get("functional")
    if method:
        label = str(method).upper()
        if func:
            label += f" ({func})"
        if basis:
            label += f" / {basis}"
        lines.append(label)
    energy = prov.get("scf_energy")
    if isinstance(energy, dict) and energy.get("value") is not None:
        lines.append(f"Total energy : {energy['value']:.8f} {energy.get('units', 'Eh')}")
    elif isinstance(energy, (int, float)):
        lines.append(f"Total energy : {energy:.8f} Eh")
    if "scf_converged" in prov:
        lines.append(f"SCF          : {'converged' if prov['scf_converged'] else 'NOT CONVERGED'}")
    for key, label in (
        ("n_scf_iterations", "SCF iters"),
        ("n_iterations", "SCF iters"),
    ):
        if prov.get(key) is not None:
            lines.append(f"{label:<13}: {prov[key]}")
            break
    if prov.get("charge") is not None or prov.get("multiplicity") is not None:
        lines.append(f"Charge/mult  : {prov.get('charge', '?')} / {prov.get('multiplicity', '?')}")
    if prov.get("n_electrons") is not None:
        lines.append(f"Electrons    : {prov['n_electrons']}")
    if prov.get("wall_seconds") is not None:
        lines.append(f"Wall time    : {float(prov['wall_seconds']):.2f} s")
    # Thermochemistry and dipole live at the MANIFEST ROOT (QVF spec § 4.7),
    # not inside provenance. Both blocks were previously read off provenance
    # with key names that never matched the producer either, so none of these
    # lines had ever appeared.
    thermo = reader.thermochemistry
    for key, label, unit, precision in (
        ("zpve_eh", "ZPVE", "Eh", 8),
        ("enthalpy_eh", "H", "Eh", 8),
        # cal/mol/K, not Eh/K -- the one non-atomic unit in the block.
        ("entropy_cal_mol_k", "S", "cal/mol/K", 4),
        ("gibbs_free_energy_eh", "G", "Eh", 8),
    ):
        val = thermo.get(key)
        if val is not None:
            lines.append(f"{label:<13}: {float(val):.{precision}f} {unit}")
    # Thermochemistry is meaningless without the state it was evaluated at.
    if thermo.get("temperature_k") is not None:
        conditions = f"{float(thermo['temperature_k']):.2f} K"
        if thermo.get("pressure_atm") is not None:
            conditions += f", {float(thermo['pressure_atm']):.3f} atm"
        lines.append(f"{'at':<13}: {conditions}")
    # Dipole moment (total magnitude + optional vector).
    dipole = reader.dipole_moment
    if dipole.get("total_debye") is not None:
        lines.append(f"Dipole       : {dipole['total_debye']:.4f} D")
    if prov.get("hostname"):
        lines.append(f"Host         : {prov['hostname']}")
    return "\n".join(lines)


def _checkpoint_summary(reader: QVFReader) -> tuple[str, str]:
    """(run_status, short checkpoint text) for any lifecycle-stamped QVF.

    ``("", "")`` for an ordinary file. Covers the full § 3.2 value set —
    a ``pending`` job container gets its chip with no checkpoint text;
    a streaming checkpoint compresses ``provenance.checkpoint`` to what
    fits an app-bar chip: ``"#seq · iter N · E -75.98 Eh"``.
    """
    status = reader.run_status or ""
    if not status:
        return "", ""
    ck = reader.checkpoint_info
    parts = []
    if ck.get("seq") is not None:
        parts.append(f"#{ck['seq']}")
    if ck.get("scf_iteration") is not None:
        parts.append(f"iter {ck['scf_iteration']}")
    energy = ck.get("energy_eh")
    if isinstance(energy, (int, float)):
        parts.append(f"E {energy:.6f} Eh")
    return status, " · ".join(parts)






def _plot_spectra_kinds() -> frozenset[str]:
    return frozenset(
        {
            "spectra.ir",
            "spectra.uvvis",
            "spectra.raman",
            "spectra.ecd",
            "spectra.vcd",
            "spectra.generic",
        }
    )


def _two_d_panel_kinds() -> frozenset[str]:
    """Section kinds whose ``activate_section`` branch only refreshes the
    HTML side panel and never pushes the 3D viewport.

    ``activate_section`` runs unconditional 3D-scene cleanup on every
    switch (charge-label removal, static-structure / CPK restore — see
    ``_remove_atom_charge_labels`` at the top of the dispatch). The
    Mesh-rendering branches such as structure and volume push inside their
    own ``_activate_*`` helpers; these 2D panels do not. With VtkLocalView the
    client renders its *own* local copy of the geometry, so the section
    dispatcher guarantees one push whenever an activator did not already
    provide one. This set remains the hot-reload classifier for sections that
    can be rematerialized without a full 3D rebuild. (audit UI-OBS-F)
    """
    return _plot_spectra_kinds() | frozenset(
        {
            "bands",
            "dos.total",
            "dos.projected",
            "phonon_bands",
            "phonon_dos",
            "equation_of_state",
            "spectra.nmr",
            "spectra.epr",
            "citations",
            "run.record",
            "job.spec",
            "scf_history",
            "dos.coop",
            "dos.cohp",
            "bond_orders",
            "structure.symmetry",
        }
    )


def _clear_output_panels(state: _ViewerUIState) -> None:
    state.active_volume_id = None
    state.volume_loaded = False
    state.bands_html = None
    # A window typed for one section must not carry into the next (#26).
    state.bands_window_available = False
    state.bands_window_auto = True
    state.bands_emin = None
    state.bands_emax = None
    state.phonon_html = None
    state.eos_html = None
    state.fermi_band_options = []
    state.fermi_selected_bands = None
    state.spectra_html = None
    state.properties_html = None
    state.chart_html = None
    state.run_record_attachments = []
    state.run_record_attachment_section = ""
    state.trajectory_energy_image = None
    state.trajectory_n_frames = 0
    state.trajectory_frame = 0
    state.trajectory_playing = False
    # Vibration state — `vibration_playing` MUST be reset here (mirroring
    # trajectory_playing) so the @state.change cancel handler tears down the
    # async animation loop on a section/file switch; otherwise the orphaned
    # loop keeps animating the next section's atoms (audit finding A1-01).
    state.vibration_n_modes = 0
    state.vibration_playing = False
    state.vibration_phase = 0.0
    state.vibration_frequencies = []
    state.vibration_mode_items = []
    state.vibration_ir_intensities = []
    # MO picker — clear the previous section's orbital rows (A1-06 / file
    # switch staleness) so the dropdown can't list a stale wavefunction.
    state.wf_section_id = None
    state.wf_mo_rows = []
    state.relocalize_supported = False
    state.relocalize_method_options = []
    state.relocalize_has_overlay = False
    state.relocalize_result_summary = ""
    state.relocalize_charge_rows = []
    # Computed surfaces are scoped to that wavefunction section. Ordinary
    # same-file scene rebuilds preserve this recipe; section and file switches
    # deliberately discard it here.
    state.wf_surface_kind = None
    state.wf_animating = False
    state.mo_visible = False
    state.mo_last_index = None
    state.mo_last_spin = None
    state.wf_density_spin = False
    # Clip plane is bound to the active volume; disable it when that changes
    # (A1-07) so a stale clip can't apply to the next section.
    state.clip_enabled = False
    state.clip_position_message = ""
    # Grid-level periodic replication is per-volume; reset on section switch.
    state.periodic_replication = 0
    # Extra isosurface layers are per-volume; clear them on section switch.
    state.extra_isosurfaces = []
    state.reaction_current_label = ""
    state.reaction_waypoints = []
    state.atom_properties_active = False
    state.atom_properties_section_id = None


def _ospray_available() -> bool:
    """True iff the installed VTK build ships OSPRay (path-tracing) support.

    Most wheel builds of VTK do NOT bundle OSPRay, so ``vtkOSPRayPass`` is
    absent. We use this to hide the ray-tracing toggle rather than let it
    raise at click time.
    """
    try:
        import vtk

        return hasattr(vtk, "vtkOSPRayPass")
    except Exception:
        return False


def _push_camera(plotter: pv.Plotter) -> None:
    """Send the server plotter's camera to the client's VtkLocalView.

    Separate from :func:`_push_view`, which sends the scene: the client owns
    the camera and ignores a server-side change until it is pushed. Silent
    no-op when headless.
    """
    push = _scene_hooks(plotter).push_camera
    if push is None:
        return
    # Best-effort: a disconnected client must not break a file switch.
    with contextlib.suppress(Exception):
        push()












def _push_view(plotter: pv.Plotter) -> None:
    """Push the latest plotter state to the client's VtkLocalView.

    ``create_app`` registers ``local_view.update`` as the plotter's
    :class:`_SceneHooks` ``update`` so module-level rebuilders can trigger a
    client refresh without taking the controller through every call
    signature.
    Silent no-op when running headless (e.g. in tests).
    """
    hooks = _scene_hooks(plotter)
    hooks.push_generation += 1
    update = hooks.update
    if update is None:
        return
    # The viewport push is best-effort: a client that has gone away must not
    # take the render path down with it.
    with contextlib.suppress(Exception):
        update()


def _save_screenshot_disk(png_bytes: bytes, reader: QVFReader) -> None:
    """Write screenshot PNG next to the QVF file, if its path is known."""
    from datetime import datetime

    if reader.path is None:
        return
    stem = reader.path.stem
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = reader.path.parent / f"{stem}_screenshot_{ts}.png"
    out.write_bytes(png_bytes)


def _save_export_disk(data: bytes, reader: QVFReader, fmt: str) -> None:
    """Write exported geometry next to the QVF file."""
    from datetime import datetime

    if reader.path is None:
        return
    stem = reader.path.stem
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = {
        "obj": ".obj",
        "gltf": ".gltf",
        "stl": ".stl",
        "xyz": ".xyz",
        "cif": ".cif",
        "pov": ".pov",
        "blend": ".py",
        "svg": ".svg",
        "cml": ".cml",
    }[fmt]
    out = reader.path.parent / f"{stem}_export_{ts}{ext}"
    out.write_bytes(data)


def _activate_volume(
    reader: QVFReader,
    plotter: pv.Plotter,
    viewer_state: ViewerState,
    state: _ViewerUIState,
    section: Section,
) -> None:
    """Lazy activation of a volume section."""
    from vibeview.renderers.volume import VolumeRenderer

    renderer = VolumeRenderer(section, reader)
    # Load grid eagerly (tiny JSON)
    try:
        renderer.load_grid()
    except QVFError as e:
        state.status_message = f"Error loading grid: {e}"
        return

    # Load data lazily (from zip, with sha256 verification)
    try:
        renderer.load_data()
    except QVFError as e:
        state.status_message = f"Error loading volume data: {e}"
        return

    hints = viewer_state.get_volume_hints(section.id, kind=section.kind)
    state.isovalue = hints.isovalue
    state.colormap = hints.colormap
    state.opacity = hints.opacity
    state.active_volume_id = section.id
    state.volume_loaded = True
    state.isovalue_units = _ISOVALUE_UNITS.get(section.kind, "arb. units")
    state.status_message = f"Loaded volume: {section.id}"

    _rebuild_volume(reader, plotter, viewer_state, state, section.id)


def _rebuild_volume(
    reader: QVFReader,
    plotter: pv.Plotter,
    viewer_state: ViewerState,
    state: _ViewerUIState,
    section_id: str,
) -> None:
    """Rebuild the isosurface for a given volume section.

    For signed scalar fields (``volume.difference``, ``volume.spin``,
    ``volume.orbital``, ``basis.ao``), renders two isosurfaces: positive
    lobes in blue, negative lobes in red.

    **Mesh cache (A7-02):** marching cubes is O(grid_size) and only needs
    to re-run when the isovalue or replication changes — *not* when only
    colormap or opacity changes (those are actor-property updates). The
    last-marched mesh is cached in ``viewer_state`` keyed by
    ``(isovalue, replication)``; a cache hit skips ``make_mesh()`` /
    ``_build_signed_contour()`` entirely, reducing colormap/opacity slider
    latency to the cost of one ``plotter.add_mesh`` call.
    """
    from vibeview.renderers.volume import (
        PeriodicGridAlignmentError,
        VolumeRenderer,
        replicate_mesh_for_periodic,
        replicate_volume_for_periodic,
    )

    section = next((s for s in reader.sections if s.id == section_id), None)
    if section is None:
        return

    hints = viewer_state.get_volume_hints(section_id, kind=section.kind)
    lattice = _get_lattice_vectors(reader)
    rep = viewer_state.replication
    iso = hints.isovalue
    # Grid-level periodic orbital replication (v2.0).
    periodic_rep = int(getattr(state, "periodic_replication", 0) or 0)

    # NCI (volume.rdg) takes a distinct path: contour the RDG field s(r) at
    # `iso` and color the surface by sign(\u03bb\u2082)\u03c1 from a co-present density
    # section (see _render_nci_surface + renderers/nci.py).
    if section.kind == "volume.rdg":
        _render_nci_surface(reader, plotter, viewer_state, state, section, iso, lattice, rep)
        return

    # ── mesh cache lookup ────────────────────────────────────────────
    # Only do the expensive march when isovalue or replication changed.
    # Colormap / opacity changes hit the cache and skip straight to
    # plotter.add_mesh with the same geometry but new appearance.
    cached = viewer_state.get_cached_mesh(section_id, iso, periodic_replication=periodic_rep)
    # Orbitals and basis AOs are *signed* real fields (+/− lobes), exactly
    # like difference and spin densities — they must render both lobes. They
    # used to fall through to the single-positive-isovalue path below, which
    # silently dropped the entire negative lobe (and flattened the diverging
    # colormap to one tone). The on-demand wavefunction.gto render path always
    # drew both lobes, so a stored MO looked correct live but half-missing
    # when pre-computed.
    from vibeview.kinds import SIGNED_VOLUME_KINDS

    _is_signed = section.kind in SIGNED_VOLUME_KINDS

    if cached is None:
        # Cache miss — need to load data and march.
        renderer = VolumeRenderer(section, reader)
        try:
            grid = renderer.load_grid()
            raw_data = renderer.load_data()
        except QVFError as e:
            state.status_message = f"Volume data error ({section_id}): {e}"
            return

        # LOD: subsample large unsigned volume grids for performance.
        # Auto-enables when grid exceeds 100 voxels in any dimension;
        # the user toggle (reduce_detail) disables it when set to False.
        _manual_off = not getattr(state, "reduce_detail", True)
        if not _manual_off and not _is_signed:
            _max_dim = max(grid.shape)
            if _max_dim > 100:
                scale = max(1, _max_dim // 100)
                raw_data = raw_data[::scale, ::scale, ::scale]
                # Update the grid to match the subsampled data.
                grid.origin = grid.origin.copy()
                grid.voxel_vectors = grid.voxel_vectors * scale
                grid.shape = raw_data.shape
                state.status_message = (
                    f"Volume detail reduced ({_max_dim}→{raw_data.shape[0]}³, "
                    f"{raw_data.size:,} voxels)"
                )

        # ── Cyclic-cluster minimum-image recentre ─────────────────────
        # Roll a face-straddling orbital / Wannier function whole into the
        # cell before contouring (and before any tiling below). Overrides
        # the renderer's stored grid/data only when it actually rolls, so
        # non-periodic / non-torus / delocalized cases are untouched. A
        # section flagged orbital_kind != localized (a canonical / Bloch-like
        # crystalline orbital) is never recentred, even when the toggle is on.
        if (
            getattr(state, "wrap_periodic_orbital", False)
            and lattice is not None
            and state.is_periodic
            and _orbital_is_wrappable(section)
        ):
            try:
                from vibeview.renderers.volume import wrap_grid_to_center

                sdata_wrap = reader.read_structure()
                raw_data, _rolled = wrap_grid_to_center(raw_data, grid, sdata_wrap.pbc, lattice)
                if _rolled:
                    renderer._grid = grid
                    renderer._data = raw_data
            except Exception:
                pass  # fall back to un-wrapped rendering

        # Tile scalar samples only when the lattice is exactly representable.
        # Otherwise preserve the original contour and copy it by the physical
        # lattice vectors. Both paths supersede ordinary mesh replication.
        mesh_rep = (0, 0, 0)
        if periodic_rep > 0 and lattice is not None and state.is_periodic:
            sdata = reader.read_structure()
            if sdata.lattice_vectors is not None:
                grid_rep = _triple([periodic_rep if p else 0 for p in sdata.pbc])
                try:
                    raw_data, grid = replicate_volume_for_periodic(
                        raw_data, grid, sdata.lattice_vectors, replication=grid_rep
                    )
                except PeriodicGridAlignmentError as exc:
                    mesh_rep = grid_rep
                    state.status_message = f"Periodic mesh copies: {exc}; boundary seams retained"
                else:
                    state.status_message = (
                        f"Cell replication: {2 * periodic_rep + 1}³ ("
                        f"{raw_data.shape[0]}×{raw_data.shape[1]}×{raw_data.shape[2]} grid)"
                    )
                renderer._grid = grid
                renderer._data = raw_data
                rep = (1, 1, 1)

        if _is_signed:
            # Split the stored array into ± lobes as-is: a section's
            # ``component`` is a label, never a transform (stored_volume_field).
            pos_data = np.clip(raw_data, 0, None)
            pos_mesh = _build_signed_contour(renderer, pos_data, iso, rep, lattice)
            neg_data = np.clip(-raw_data, 0, None)
            neg_mesh = _build_signed_contour(renderer, neg_data, iso, rep, lattice)
            if lattice is not None and any(mesh_rep):
                pos_mesh = replicate_mesh_for_periodic(pos_mesh, lattice, mesh_rep)
                neg_mesh = replicate_mesh_for_periodic(neg_mesh, lattice, mesh_rep)
            viewer_state.put_cached_mesh(
                section_id,
                iso,
                periodic_replication=periodic_rep,
                pos=pos_mesh,
                neg=neg_mesh,
            )
            cached = {"pos": pos_mesh, "neg": neg_mesh}
        else:
            mesh = renderer.make_mesh(hints, replication=rep, lattice_vectors=lattice)
            if lattice is not None and any(mesh_rep):
                mesh = replicate_mesh_for_periodic(mesh, lattice, mesh_rep)
            viewer_state.put_cached_mesh(
                section_id,
                iso,
                periodic_replication=periodic_rep,
                mesh=mesh,
            )
            cached = {"mesh": mesh}

    # ── render with cached mesh + current appearance hints ───────────
    _remove_actors_by_prefix(plotter, _Actors.VOLUME)

    if _is_signed:
        from vibeview.renderers.palette import ISO_NEGATIVE, ISO_POSITIVE

        pos_mesh = cached.get("pos")
        if pos_mesh is not None:
            plotter.add_mesh(
                pos_mesh,
                color=ISO_POSITIVE,
                opacity=hints.opacity,
                name=f"volume_{section_id}_pos",
                show_scalar_bar=False,
            )
        neg_mesh = cached.get("neg")
        if neg_mesh is not None:
            plotter.add_mesh(
                neg_mesh,
                color=ISO_NEGATIVE,
                opacity=hints.opacity,
                name=f"volume_{section_id}_neg",
                show_scalar_bar=False,
            )
    else:
        mesh = cached.get("mesh")
        if mesh is not None:
            # ESP-on-density colour-mapping: probe electrostatic-potential
            # values from a companion volume.potential section onto the
            # density isosurface vertices, then colour by ESP instead of ρ.
            _esp_mapped = getattr(state, "color_by_esp", False) and section.kind == "volume.density"
            if _esp_mapped:
                esp_sec = next((s for s in reader.sections if s.kind == "volume.potential"), None)
                if esp_sec is not None:
                    try:
                        esp_renderer = VolumeRenderer(esp_sec, reader)
                        esp_grid = esp_renderer.load_grid()
                        density_grid = VolumeRenderer(section, reader).load_grid()
                        if (
                            esp_grid.shape == density_grid.shape
                            and np.allclose(esp_grid.origin, density_grid.origin, atol=1e-6)
                            and np.allclose(
                                esp_grid.voxel_vectors, density_grid.voxel_vectors, atol=1e-6
                            )
                        ):
                            esp_data = esp_renderer.load_data()
                            # Build a probe grid matching the density grid.
                            spacing = tuple(
                                float(esp_grid.voxel_vectors[i, i]) * esp_grid.to_angstrom
                                for i in range(3)
                            )
                            origin = tuple(float(x) * esp_grid.to_angstrom for x in esp_grid.origin)
                            probe_grid = pv.ImageData(
                                dimensions=esp_grid.shape,
                                spacing=spacing,
                                origin=origin,
                            )
                            probe_grid.point_data["esp"] = esp_data.ravel(order="F")
                            probed = mesh.sample(probe_grid, pass_point_data=True)
                            if probed.n_points:
                                mesh = probed
                                scalars_name = "esp"
                                cmap = "RdBu"
                            else:
                                scalars_name = "values"
                                cmap = hints.colormap
                        else:
                            scalars_name = "values"
                            cmap = hints.colormap
                    except QVFError:
                        scalars_name = "values"
                        cmap = hints.colormap
                else:
                    scalars_name = "values"
                    cmap = hints.colormap
            else:
                scalars_name = "values"
                cmap = hints.colormap

            plotter.add_mesh(
                mesh,
                scalars=scalars_name,
                cmap=cmap_argument(cmap),
                opacity=hints.opacity,
                name=f"volume_{section_id}",
                show_scalar_bar=(scalars_name == "esp"),
                scalar_bar_args=(
                    {"title": "ESP (a.u.)", "vertical": True} if scalars_name == "esp" else None
                ),
            )

    plotter.render()
    _push_view(plotter)

    # ── Extra isosurface layers ────────────────────────────────────
    # March additional contours at user-specified isovalues from the
    # loaded volume data, with per-layer colour and opacity.
    extras = list(getattr(state, "extra_isosurfaces", None) or [])
    if extras and not _is_signed and section.kind not in ("volume.rdg",):
        from vibeview.viewer_defaults import VolumeHints

        # Use the same renderer if available (cache-miss path), otherwise
        # reload data for the extra layers.
        try:
            extra_renderer = VolumeRenderer(section, reader)
            extra_renderer.load_grid()
            extra_data = extra_renderer.load_data()
        except QVFError:
            extra_data = None
        if extra_data is not None:
            for idx, layer in enumerate(extras):
                extra_iso = float(layer.get("isovalue", 0.01))
                extra_opacity = float(layer.get("opacity", 0.35))
                extra_colour = str(layer.get("colour", _palette.GUIDE_GREY))
                extra_mesh = extra_renderer.make_mesh(
                    VolumeHints(isovalue=extra_iso),
                    replication=rep,
                    lattice_vectors=lattice,
                )
                if extra_mesh is not None and extra_mesh.n_points:
                    plotter.add_mesh(
                        extra_mesh,
                        color=extra_colour,
                        opacity=extra_opacity,
                        name=f"volume_{section_id}_extra_{idx}",
                        show_scalar_bar=False,
                    )
            plotter.render()
            _push_view(plotter)

    # If this volume is the one being clipped, re-apply the clip so a rebuild
    # (isovalue / colormap / replication change) shows the clipped slice rather
    # than the full isosurface reappearing on top of it (UI-OBS-E). Safe: the
    # clip's "off" path only calls back here when clip is disabled, so there's
    # no recursion.
    if state.clip_enabled and state.active_volume_id == section_id:
        _rebuild_clip(reader, plotter, viewer_state, state)


def _render_nci_surface(
    reader: QVFReader,
    plotter: pv.Plotter,
    viewer_state: ViewerState,
    state: _ViewerUIState,
    section: Section,
    isovalue: float,
    lattice: np.ndarray | None,
    replication: tuple[int, int, int],
) -> None:
    """Render a volume.rdg (NCI) section.

    Contours the reduced density gradient s(r) at ``isovalue`` and colors the
    surface by sign(λ₂)ρ, computed from a co-present volume.density section
    (QVF spec §4.11). With no usable density the RDG surface renders uncolored.
    Method + citations: vibeview.renderers.nci.

    Note: sign(λ₂)ρ is recomputed per call (it is independent of ``isovalue``);
    for the typical NCI grid (≤100³) the Hessian eigensolve is sub-second, so
    this is left uncached for now.
    """
    from vibeview.renderers.nci import (
        COLOR_CLIM,
        build_nci_mesh,
        nci_colormap,
        sign_lambda2_rho,
    )
    from vibeview.renderers.volume import VolumeRenderer

    rdg_r = VolumeRenderer(section, reader)
    try:
        grid = rdg_r.load_grid()
        rdg_data = rdg_r.load_data()
    except QVFError as e:
        state.status_message = f"NCI: error loading RDG ({section.id}): {e}"
        return

    color_data = None
    note = ""
    dens_sec = next((s for s in reader.sections if s.kind == "volume.density"), None)
    if dens_sec is None:
        note = " (no density section; uncolored)"
    else:
        dens_r = VolumeRenderer(dens_sec, reader)
        try:
            dgrid = dens_r.load_grid()
            rho = dens_r.load_data()
        except QVFError:
            rho = None
            note = " (density unreadable; uncolored)"
        if rho is not None:
            if (
                tuple(rho.shape) == tuple(rdg_data.shape)
                and np.array_equal(dgrid.origin, grid.origin)
                and np.array_equal(dgrid.voxel_vectors, grid.voxel_vectors)
            ):
                color_data = sign_lambda2_rho(rho, dgrid.voxel_vectors)
            else:
                note = " (density grid mismatch; uncolored)"

    state.active_volume_id = section.id
    state.volume_loaded = True
    _remove_actors_by_prefix(plotter, _Actors.VOLUME)

    pbc = None
    if lattice is not None:
        with contextlib.suppress(QVFError):
            pbc = reader.read_structure().pbc
    try:
        mesh = build_nci_mesh(
            rdg_data, color_data, grid, isovalue,
            replication=replication, lattice_vectors=lattice, pbc=pbc,
        )
    except Exception as e:  # noqa: BLE001 — surface a render failure, don't crash the app
        state.status_message = f"NCI: render failed ({section.id}): {e}"
        return

    if mesh is None or mesh.n_points == 0:
        state.status_message = f"NCI: no RDG surface at s={isovalue:.2g} (try a higher isovalue)"
        plotter.render()
        _push_view(plotter)
        return

    if color_data is not None:
        plotter.add_mesh(
            mesh,
            scalars="color",
            cmap=nci_colormap(),
            clim=[-COLOR_CLIM, COLOR_CLIM],
            opacity=0.65,
            name=f"volume_{section.id}",
            show_scalar_bar=False,
        )
    else:
        plotter.add_mesh(
            mesh,
            color=_palette.GUIDE_GREY,
            opacity=0.65,
            name=f"volume_{section.id}",
            show_scalar_bar=False,
        )
    state.status_message = f"Loaded NCI (RDG s={isovalue:.2g}){note}: {section.id}"
    plotter.render()
    _push_view(plotter)


# Per-file tints for Phase D compare/overlay mode (one colour per loaded file).
_COMPARE_PALETTE = list(_palette.COMPARE_FILES)


def _render_compare_overlay(
    plotter: pv.Plotter,
    readers: Sequence[QVFReader],
    align: bool = False,
    highlight: int | None = None,
) -> list[dict[str, Any]]:
    """Overlay the structures from all loaded files, one uniform colour per file
    (Phase D1 compare mode).

    Atoms are glyph-instanced into one translucent actor per file (``cmp_<i>``).
    With ``align=True`` each file is Kabsch-superposed onto the first file (when
    the atom counts match) so the residual displacement is what shows (Phase D4),
    and its RMSD to that reference is reported in the legend. When ``highlight``
    is an index, only that file appears at full opacity (0.7); others are shown
    at 0.15 for context.  Returns ``[{"name", "color", "rmsd", "label"}]``;
    files with no readable structure are skipped.
    """
    import numpy as _np

    from vibeview.align import kabsch_fit

    _remove_actors_by_prefix(plotter, _Actors.COMPARE)
    ref_pts = None
    legend: list[dict[str, Any]] = []
    for i, r in enumerate(readers):
        try:
            sdata = r.read_structure()
        except QVFError:
            continue
        pts = _np.array([a.position for a in sdata.atoms], dtype=float)
        if pts.size == 0:
            continue
        rmsd_val = None
        if ref_pts is None:
            ref_pts = pts  # first structure = the alignment reference
            if align:
                rmsd_val = 0.0
        elif align and pts.shape == ref_pts.shape:
            try:
                pts, rmsd_val = kabsch_fit(pts, ref_pts)
            except ValueError:
                rmsd_val = None
        color = _COMPARE_PALETTE[i % len(_COMPARE_PALETTE)]
        name = _job_name(r)
        label = name if rmsd_val is None else f"{name} (RMSD {rmsd_val:.2f} Å)"
        legend.append({"name": name, "color": color, "rmsd": rmsd_val, "label": label})
        # Highlight mode: full opacity for selected file, dim for others.
        opacity = 0.7
        if highlight is not None:
            opacity = 0.7 if i == highlight else 0.15
        glyphed = pv.PolyData(pts).glyph(
            geom=pv.Sphere(radius=0.3, theta_resolution=12, phi_resolution=12),
            scale=False,
            orient=False,
            progress_bar=False,
        )
        plotter.add_mesh(
            glyphed,
            color=color,
            opacity=opacity,
            smooth_shading=True,
            name=f"cmp_{i}",
            show_scalar_bar=False,
        )
    return legend


def _grids_match(grid_a: GridData, grid_b: GridData, atol: float = 1e-6) -> bool:
    """True when two volume grids are voxel-for-voxel comparable."""
    import numpy as _np

    return (
        tuple(grid_a.shape) == tuple(grid_b.shape)
        and _np.allclose(grid_a.origin, grid_b.origin, atol=atol)
        and _np.allclose(grid_a.voxel_vectors, grid_b.voxel_vectors, atol=atol)
    )


def compute_volume_difference(
    grid_a: GridData, data_a: np.ndarray, grid_b: GridData, data_b: np.ndarray
) -> np.ndarray:
    """ρ_A − ρ_B on grid A (Phase D2).

    Requires matching grids (same shape, origin, and voxel vectors); raises
    ``ValueError`` otherwise. Trilinear interpolation for mismatched grids is a
    planned follow-up.
    """
    import numpy as _np

    if not _grids_match(grid_a, grid_b):
        raise ValueError(
            f"density grids differ (shapes {tuple(grid_a.shape)} vs "
            f"{tuple(grid_b.shape)}); matching grids are required"
        )
    delta: np.ndarray = _np.asarray(data_a, dtype=float) - _np.asarray(data_b, dtype=float)
    return delta


def _activate_density_diff(
    plotter: pv.Plotter,
    viewer_state: ViewerState,
    state: _ViewerUIState,
    readers: Sequence[QVFReader],
    idx_a: int,
    idx_b: int,
) -> None:
    """Render the signed density difference ρ_A − ρ_B between two files (Phase D2).

    Draws a two-colour isosurface (blue = accumulation where A > B, red =
    depletion, the shared lobe convention of #38-19) over file A's
    structure. Same-grid only for now; mismatched grids surface a clear
    status message rather than rendering.
    """
    import numpy as _np

    from vibeview.renderers.structure import StructureRenderer
    from vibeview.renderers.volume import VolumeRenderer, build_isosurface_mesh

    try:
        ra, rb = readers[int(idx_a)], readers[int(idx_b)]
    except (IndexError, ValueError, TypeError):
        state.status_message = "Density difference: invalid file selection"
        return

    def _density(r: QVFReader) -> tuple[GridData | None, np.ndarray | None]:
        sec = next((s for s in r.sections if s.kind == "volume.density"), None)
        if sec is None:
            return None, None
        vr = VolumeRenderer(sec, r)
        return vr.load_grid(), vr.load_data()

    try:
        grid_a, data_a = _density(ra)
        grid_b, data_b = _density(rb)
    except QVFError as e:
        state.status_message = f"Density difference: load error ({e})"
        return
    if grid_a is None or grid_b is None:
        state.status_message = "Density difference: both files need a volume.density section"
        return
    if data_a is None or data_b is None:
        raise ValueError("Both volumes must be loaded before they can be subtracted")
    try:
        delta = compute_volume_difference(grid_a, data_a, grid_b, data_b)
    except ValueError as e:
        state.status_message = f"Density difference: {e}"
        return

    _remove_static_structure(plotter)
    for prefix in (
        _Actors.VOLUME,
        _Actors.COMPARE,
        _Actors.FERMI,
        _Actors.VIB_ATOM,
        _Actors.TRAJ_ATOM,
        _Actors.MO_SURFACE,
    ):
        _remove_actors_by_prefix(plotter, prefix)
    state.compare_mode = False
    state.compare_legend = []
    state.active_volume_id = None
    state.structure_hidden = False

    struct_sec = next((s for s in ra.sections if s.kind == "structure"), None)
    if struct_sec is not None:
        with contextlib.suppress(QVFError):
            StructureRenderer(struct_sec, ra).add_to_plotter(
                plotter,
                representation=str(
                    getattr(state, "representation_style", "ball_and_stick")
                ),
                cartoon_color_mode=str(
                    getattr(state, "cartoon_color_mode", "chain")
                ),
                residue_selection=str(getattr(state, "residue_selection", "") or ""),
                **_structure_display_options(state),
            )

    iso = 0.005
    lattice = _get_lattice_vectors(ra)
    pos = build_isosurface_mesh(_np.clip(delta, 0, None), grid_a, iso, lattice_vectors=lattice)
    neg = build_isosurface_mesh(_np.clip(-delta, 0, None), grid_a, iso, lattice_vectors=lattice)
    from vibeview.renderers.palette import ISO_NEGATIVE, ISO_POSITIVE

    if pos is not None and pos.n_points:
        plotter.add_mesh(
            pos, color=ISO_POSITIVE, opacity=0.6, name="volume_diff_pos", show_scalar_bar=False
        )
    if neg is not None and neg.n_points:
        plotter.add_mesh(
            neg, color=ISO_NEGATIVE, opacity=0.6, name="volume_diff_neg", show_scalar_bar=False
        )

    state.status_message = (
        f"Density difference: {_job_name(ra)} − {_job_name(rb)} "
        f"(blue = accumulation, red = depletion, iso {iso})"
    )
    view_isometric(plotter)
    reset_camera(plotter)
    plotter.render()
    _push_view(plotter)


def _activate_fermi(
    reader: QVFReader,
    plotter: pv.Plotter,
    viewer_state: ViewerState,
    state: _ViewerUIState,
    section: Section,
) -> None:
    """Activate a fermi_surface section.

    Renders the Fermi sheets (E − E_F = 0 isosurfaces) in reciprocal space
    (Å⁻¹), one per band near E_F, plus the reciprocal-cell wireframe. This is a
    different coordinate system from the real-space structure, so the molecular
    scene is cleared while it is shown. Method: vibeview.renderers.fermi.
    """
    from vibeview.renderers.fermi import FermiSurfaceRenderer, reciprocal_cell_edges

    renderer = FermiSurfaceRenderer(section, reader)
    try:
        sheets, recip, meta = renderer.build_sheets()
    except (QVFError, ValueError, KeyError, TypeError) as e:
        state.status_message = f"Fermi surface: error ({section.id}): {e}"
        return

    # Reciprocal space: clear the real-space structure + any field overlays.
    _remove_static_structure(plotter)
    for prefix in (
        _Actors.VOLUME,
        _Actors.VIB_ATOM,
        _Actors.TRAJ_ATOM,
        _Actors.MO_SURFACE,
        _Actors.FERMI,
    ):
        _remove_actors_by_prefix(plotter, prefix)
    state.structure_hidden = True
    state.active_volume_id = None

    # Band selection: the right-panel selector (ctrl.update_fermi_bands) toggles
    # which bands are drawn; a fresh activation (fermi_selected_bands is None)
    # defaults to all bands.
    all_labels = [bl for bl, _ in sheets]
    state.fermi_band_options = [{"title": f"Band {bl}", "value": bl} for bl in all_labels]
    if state.fermi_selected_bands is None:
        state.fermi_selected_bands = list(all_labels)
    selected_set = set(state.fermi_selected_bands)

    palette = _palette.FERMI_SHEETS
    for n, (band_label, sheet) in enumerate(sheets):
        if band_label not in selected_set:
            continue
        plotter.add_mesh(
            sheet,
            color=palette[n % len(palette)],
            opacity=0.55,
            name=f"fermi_band_{band_label}",
            show_scalar_bar=False,
        )
    plotter.add_mesh(
        reciprocal_cell_edges(recip),
        color=_palette.GUIDE_GREY,
        line_width=1,
        name="fermi_cell",
        show_scalar_bar=False,
    )
    view_isometric(plotter)
    reset_camera(plotter)

    fermi_ev = meta.get("fermi_energy_ev")
    ef = f", E_F={fermi_ev:.2f} eV" if isinstance(fermi_ev, (int, float)) else ""
    if sheets:
        n_shown = sum(1 for bl, _ in sheets if bl in selected_set)
        state.status_message = (
            f"Fermi surface: {n_shown}/{len(sheets)} band sheet(s) shown{ef} "
            f"in reciprocal space (Å⁻¹)"
        )
    else:
        state.status_message = f"Fermi surface: no band crosses E_F in the stored window{ef}"
    plotter.render()
    _push_view(plotter)


def _build_signed_contour(
    renderer: VolumeRenderer,
    data: np.ndarray,
    isovalue: float,
    replication: tuple[int, int, int],
    lattice_vectors: np.ndarray | None,
) -> pv.PolyData | None:
    """Build a signed surface using the shared QVF grid geometry."""
    from vibeview.renderers.volume import build_isosurface_mesh

    contour = build_isosurface_mesh(
        data,
        renderer.load_grid(),
        isovalue,
        replication=replication,
        lattice_vectors=lattice_vectors,
    )
    return contour if contour is not None and contour.n_points else None


def _coerce_window_bound(value: Any) -> float | None:
    """A number from an energy-window field, or ``None`` when it is blank.

    A ``type="number"`` VTextField still v-models as a string, and a
    cleared field arrives as ``""``. Anything that is not a finite number
    reads as "not set" — the caller then keeps the previous window.
    """
    import math

    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _requested_energy_window(
    state: _ViewerUIState, viewer_state: ViewerState | None, *section_ids: str
) -> tuple[float, float] | None:
    """The energy window the reader or the producer asked for (#26).

    A window the reader typed into the panel controls wins; failing that,
    a ``viewer_defaults`` hint on any of ``section_ids``. ``None`` leaves
    the renderer on its own valence default. The result is in eV; the
    fields hold the display unit, ``band_energy_unit`` (#58).
    """
    from vibeview.energy_window import parse_energy_window

    if not getattr(state, "bands_window_auto", True):
        window = parse_energy_window(
            [getattr(state, "bands_emin", None), getattr(state, "bands_emax", None)]
        )
        if window is not None:
            unit = state.band_energy_unit
            return energy_to_ev(window[0], unit), energy_to_ev(window[1], unit)
    if viewer_state is not None:
        return viewer_state.get_energy_window(*section_ids)
    return None


def _publish_energy_window(
    state: _ViewerUIState,
    window: Any,
    compute_default: Callable[[],
    Any],
) -> None:
    """Show the window a chart was actually drawn with in the panel controls.

    When the caller passed no window the renderer used its own default, so
    ask for the same value rather than leaving the fields blank — they are
    the reader's starting point for a manual adjustment. Blank fields mean
    the chart really is on full autoscale.
    """
    state.bands_window_available = True
    if window is None:
        try:
            window = compute_default()
        except Exception:
            window = None
    if window is None:
        state.bands_emin = None
        state.bands_emax = None
    else:
        # The window is in eV; the fields show the axis unit (#58).
        state.bands_emin = energy_from_ev(float(window[0]), state.band_energy_unit)
        state.bands_emax = energy_from_ev(float(window[1]), state.band_energy_unit)


def _activate_bands(
    reader: QVFReader,
    state: _ViewerUIState,
    section: Section,
    viewer_state: ViewerState | None = None,
) -> None:
    """Lazy activation of a bands section.

    When the archive also carries a ``dos.total`` section, the bands
    and DOS are rendered side-by-side in a single combined panel.

    Either form opens on an energy window (#26): the reader's own min/max,
    else a ``viewer_defaults`` hint, else the renderer's valence default.
    Without one, an all-electron archive's core states dominate the
    autoscale and squeeze the valence region into a few pixels.
    """
    from vibeview.renderers.bands import BandsRenderer

    renderer = BandsRenderer(section, reader)
    # Check for a companion DOS section.
    dos_section = next((s for s in reader.sections if s.kind == "dos.total"), None)
    ids = [section.id] + ([dos_section.id] if dos_section is not None else [])
    window = _requested_energy_window(state, viewer_state, *ids)
    try:
        bands_html = renderer.render_to_html(
            energy_window=window, energy_unit=state.band_energy_unit
        )
    except QVFError as e:
        state.status_message = f"Error loading bands: {e}"
        return

    if dos_section is not None:
        try:
            from vibeview.renderers.dos import (
                DOSRenderer,
                bands_dos_energy_window,
                render_bands_dos_combined,
            )

            dos_renderer = DOSRenderer(dos_section, reader)
            # Single figure with a shared energy axis (bands left, DOS rotated
            # right) — the standard solid-state plot (A4-04).
            state.bands_html = render_bands_dos_combined(
                renderer,
                dos_renderer,
                title="Band Structure & DOS",
                energy_window=window,
                energy_unit=state.band_energy_unit,
            )
            state.bands_title = "Band Structure & DOS"
            state.status_message = f"Loaded bands + DOS: {section.id}, {dos_section.id}"
            _publish_energy_window(
                state, window, lambda: bands_dos_energy_window(renderer, dos_renderer)
            )
            return
        except Exception as e:
            state.status_message = f"Loaded bands: {section.id} (DOS unavailable: {e})"
    else:
        state.status_message = f"Loaded bands: {section.id}"
    state.bands_html = bands_html
    state.bands_title = "Band Structure"
    _publish_energy_window(state, window, renderer.default_energy_window)


def _activate_dos(
    reader: QVFReader,
    state: _ViewerUIState,
    section: Section,
    viewer_state: ViewerState | None = None,
) -> None:
    """Lazy activation of a standalone DOS section (no bands available)."""
    from vibeview.renderers.dos import DOSRenderer

    renderer = DOSRenderer(section, reader)
    window = _requested_energy_window(state, viewer_state, section.id)
    try:
        state.bands_html = renderer.render_to_html(
            energy_window=window, energy_unit=state.band_energy_unit
        )
        # The bands_html slot is shared with the (absent) bands panel; title it
        # for what's actually shown so a DOS-only view isn't mislabeled
        # "Band Structure" (live-review finding).
        state.bands_title = (
            "Projected DOS" if section.kind == "dos.projected" else "Density of States"
        )
        state.status_message = f"Loaded DOS: {section.id}"
        _publish_energy_window(state, window, renderer.default_energy_window)
    except Exception as e:
        state.status_message = f"Error loading DOS: {e}"


def _activate_spectra(
    reader: QVFReader,
    state: _ViewerUIState,
    section: Section,
    all_readers: Sequence[QVFReader] | None = None,
) -> None:
    """Lazy activation of a spectra section.

    With ``spectra_compare`` on and several files open, same-kind spectra
    from every open QVF are overlaid with a per-file legend instead of
    rendering only the active file's spectrum.
    """
    from vibeview.renderers.spectra import SpectraRenderer, render_comparison_html

    try:
        if getattr(state, "spectra_compare", False) and all_readers and len(all_readers) > 1:
            entries = [
                (_job_name(r), sec, r)
                for r in all_readers
                if (sec := next((s for s in r.sections if s.kind == section.kind), None))
                is not None
            ]
            if len(entries) > 1:
                state.spectra_html = render_comparison_html(
                    entries,
                    gamma_scale=float(getattr(state, "spectra_gamma_scale", 1.0)),
                    normalize=bool(getattr(state, "spectra_normalize", False)),
                    x_unit=str(getattr(state, "spectra_x_unit", "native")),
                    display=str(getattr(state, "spectra_display", "both") or "both"),
                )
                state.spectra_title = f"{_section_title(section)} — {len(entries)} files"
                state.status_message = (
                    f"Comparing {section.kind} across {len(entries)} open files"
                )
                return
            state.status_message = "No other open file has a matching spectrum section"
        renderer = SpectraRenderer(section, reader)
        state.spectra_html = renderer.render_to_html(
            gamma_scale=float(getattr(state, "spectra_gamma_scale", 1.0)),
            normalize=bool(getattr(state, "spectra_normalize", False)),
            x_unit=str(getattr(state, "spectra_x_unit", "native")),
            display=str(getattr(state, "spectra_display", "both") or "both"),
        )
        state.spectra_title = _section_title(section)
        state.status_message = f"Loaded spectra: {section.id}"
    except QVFError as e:
        state.status_message = f"Error loading spectra: {e}"


def _activate_trajectory(reader: QVFReader, state: _ViewerUIState, section: Section) -> None:
    """Lazy activation of a trajectory section."""
    from vibeview.renderers.trajectory import TrajectoryRenderer

    renderer = TrajectoryRenderer(section, reader)
    try:
        renderer.load()
        state.trajectory_frame = 0
        state.trajectory_n_frames = renderer.n_frames
        state.trajectory_playing = False
        state.status_message = f"Loaded trajectory: {section.id} ({renderer.n_frames} frames)"
        _update_trajectory_plot(reader, state)
    except QVFError as e:
        state.status_message = f"Error loading trajectory: {e}"


def _activate_vibrations(reader: QVFReader, state: _ViewerUIState, section: Section) -> None:
    """Lazy activation of a vibrations section.

    When the archive also carries a ``spectra.ir`` section, each mode
    label includes the IR intensity so the user can pick the brightest
    peaks directly from the dropdown.
    """
    from vibeview.renderers.vibrations import VibrationsRenderer

    renderer = VibrationsRenderer(section, reader)
    try:
        renderer.load()
        state.vibration_n_modes = renderer.n_modes
        freqs = [round(float(f), 1) for f in renderer.get_frequencies()]
        state.vibration_frequencies = freqs

        # Try to read companion IR intensities for richer labels.
        ir_intens = None
        ir_section = next((s for s in reader.sections if s.kind == "spectra.ir"), None)
        if ir_section is not None:
            try:
                from vibeview.renderers.spectra import SpectraRenderer

                ir_renderer = SpectraRenderer(ir_section, reader)
                ir_data = ir_renderer.load()
                if len(ir_data.intensities) == len(freqs):
                    ir_intens = [float(v) for v in ir_data.intensities]
            except Exception:
                pass

        if ir_intens is not None:
            # Surface the IR intensity in each real mode's label so the user
            # can pick the brightest peaks straight from the dropdown.
            state.vibration_mode_items = [
                {
                    "title": (
                        f"Mode {i + 1} — {f} cm⁻¹ (IR {ir_intens[i]:.1f} km/mol)"
                        if f > 0.0
                        else f"Mode {i + 1} — {f} cm⁻¹ (trans/rot)"
                    ),
                    "value": i,
                }
                for i, f in enumerate(freqs)
            ]
            state.vibration_ir_intensities = ir_intens
        else:
            state.vibration_ir_intensities = []
            state.vibration_mode_items = [
                {
                    "title": (
                        f"Mode {i + 1} — {f} cm⁻¹"
                        if f > 0.0
                        else f"Mode {i + 1} — {f} cm⁻¹ (trans/rot)"
                    ),
                    "value": i,
                }
                for i, f in enumerate(freqs)
            ]

        # Default to the first non-zero (real vibrational) mode.
        first_real = next((i for i, f in enumerate(freqs) if f > 0.0), 0)
        state.vibration_mode = first_real
        state.status_message = f"Loaded vibrations: {section.id} ({renderer.n_modes} modes)"
    except QVFError as e:
        state.status_message = f"Error loading vibrations: {e}"


def _activate_atom_properties(
    reader: QVFReader, plotter: pv.Plotter, state: _ViewerUIState, section: Section
) -> None:
    """Lazy activation of an atom_properties section.

    Renders the Mulliken / Löwdin / Hirshfeld table in the bottom panel (via
    ``properties_html``) AND overlays per-atom labels and charge-tinted
    spheres in the 3D viewport so the spatial pattern of charge transfer
    is readable at a glance. Which population analysis is shown is
    controlled by ``state.charge_kind``; ``state.color_by_charge`` swaps
    the CPK palette for a red/blue sign-based tint.
    """
    from vibeview.renderers.atom_properties import AtomPropertiesRenderer

    renderer = AtomPropertiesRenderer(section, reader)
    try:
        data = renderer.load()
        state.properties_html = renderer.render_to_html(
            charge_kind=state.charge_kind if state.charge_kind else None
        )
        state.properties_title = "Atomic Charges"
        state.status_message = f"Loaded charges: {section.id}"
        state.atom_properties_active = True
        state.atom_properties_section_id = section.id
    except QVFError as e:
        state.status_message = f"Error loading charges: {e}"
        return

    try:
        _render_atom_properties_overlay(reader, plotter, state, data)
    except Exception as e:  # noqa: BLE001 — renderer failures stay in the viewer
        state.atom_properties_active = False
        state.atom_properties_section_id = None
        state.status_message = f"Charge overlay error: {e}"


def _rollback_atom_properties_overlay(
    reader: QVFReader,
    plotter: pv.Plotter,
    state: _ViewerUIState,
    *,
    push: bool,
) -> None:
    """Remove a failed charge overlay and restore neutral structure if possible."""
    _remove_static_structure(plotter)
    structure_section = next(
        (section for section in reader.sections if section.kind == "structure"),
        None,
    )
    if structure_section is not None:
        from vibeview.renderers.structure import StructureRenderer

        try:
            StructureRenderer(structure_section, reader).add_to_plotter(
                plotter,
                replication=(
                    int(getattr(state, "replication_nx", 1) or 1),
                    int(getattr(state, "replication_ny", 1) or 1),
                    int(getattr(state, "replication_nz", 1) or 1),
                ),
                show_labels=bool(getattr(state, "show_atom_labels", False)),
                representation=str(
                    getattr(state, "representation_style", "ball_and_stick")
                ),
                cartoon_color_mode=str(
                    getattr(state, "cartoon_color_mode", "chain")
                ),
                residue_selection=str(
                    getattr(state, "residue_selection", "") or ""
                ),
                **_structure_display_options(state),
            )
        except Exception:  # noqa: BLE001 — leave no partial fallback
            _remove_static_structure(plotter)
    if push:
        with contextlib.suppress(Exception):
            _apply_scene_appearance(plotter, state)
        with contextlib.suppress(Exception):
            plotter.render()
        _push_view(plotter)


def _render_atom_properties_overlay(
    reader: QVFReader,
    plotter: pv.Plotter,
    state: _ViewerUIState,
    data: Any = None,
    *,
    push: bool = True,
) -> bool:
    """Render an atomic-property overlay with one failure rollback boundary."""
    try:
        return _render_atom_properties_overlay_impl(
            reader, plotter, state, data, push=push
        )
    except Exception:
        # Preflight (load/labels/colour conversion) can fail before the old
        # overlay is touched, while StructureRenderer can fail after adding a
        # partial replacement. In both cases clear every owned actor, then
        # reconstruct the neutral structure when its source remains readable.
        # Callers clear the active recipe after this scene rollback completes.
        _rollback_atom_properties_overlay(
            reader, plotter, state, push=push
        )
        raise


def _render_atom_properties_overlay_impl(
    reader: QVFReader,
    plotter: pv.Plotter,
    state: _ViewerUIState,
    data: Any = None,
    *,
    push: bool = True,
) -> bool:
    """Draw the per-atom labels + (optional) charge-tinted spheres for the
    currently-active atom_properties section. Used both by the initial
    activation and by the right-panel selectors when the user changes
    ``charge_kind`` or ``color_by_charge`` after the fact.

    Scene rebuilds pass ``push=False`` so material/toon replay happens before
    the rebuild boundary serializes the replacement actors once.
    """
    from vibeview.renderers.atom_properties import AtomPropertiesRenderer
    from vibeview.renderers.structure import StructureRenderer

    if data is None:
        section_id = state.atom_properties_section_id
        if not section_id:
            raise ValueError("no active atom-properties section")
        section = next((s for s in reader.sections if s.id == section_id), None)
        if section is None:
            raise ValueError(f"atom-properties section {section_id!r} not found")
        data = AtomPropertiesRenderer(section, reader).load()

    structure_section = next((s for s in reader.sections if s.kind == "structure"), None)
    if structure_section is None:
        raise ValueError("no structure section for atomic-property overlay")
    # Labels need the original atom positions; the representation rebuild
    # below delegates any required connectivity to StructureRenderer.
    structure_data = reader.read_structure()

    # Pick the charge array the user asked for; fall back to whichever is
    # populated if their preferred kind isn't in this file.
    # NOTE: these are NumPy arrays — ``a or b`` raises "truth value of an
    # array is ambiguous", which used to crash the whole overlay (so the
    # charge spheres + labels never rendered). Use explicit None/len checks.
    def _nonempty(arr: Any) -> bool:
        return arr is not None and len(arr) > 0

    choices = {
        "mulliken": data.mulliken_charges,
        "loewdin": data.loewdin_charges,
        "hirshfeld": data.hirshfeld_charges,
    }
    preferred = choices.get(state.charge_kind)
    charges = preferred
    if not _nonempty(charges):
        charges = next((value for value in choices.values() if _nonempty(value)), None)
    if charges is None or not _nonempty(charges):
        raise ValueError("atom-properties section has no supported charge array")

    atoms = structure_data.atoms[: len(charges)]
    points = np.asarray([a.position for a in atoms], dtype=float)
    labels = [
        f"{a.symbol}{i + 1} {float(q):+.2f}"
        for i, (a, q) in enumerate(zip(atoms, charges, strict=False))
    ]
    # 3D polygonal text — vtk.js (VtkLocalView) does not render
    # add_point_labels' 2D actors. See structure.build_label_mesh.
    from vibeview.renderers.structure import (
        build_label_mesh,
        register_label_actor,
    )

    try:
        _cam = (
            tuple(plotter.camera.position),
            tuple(plotter.camera.focal_point),
            tuple(plotter.camera.up),
        )
    except Exception:  # noqa: BLE001 — unoriented is still readable head-on
        _cam = None
    charge_label_mesh = build_label_mesh(points, labels, scale=0.4, camera=_cam)

    # Rebuild the current representation through StructureRenderer so charge
    # colours preserve space-filling radii, glyph replication, and the
    # intentional absence of atom spheres in sticks/wireframe/cartoon modes.
    # Per-atom colours are keyed by original atom index, so every periodic
    # replica receives the same charge tint without collapsing same-element
    # atoms onto one colour.
    atom_colors = None
    if state.color_by_charge:
        q_max = max((abs(float(q)) for q in charges), default=1.0) or 1.0
        atom_colors = {
            i: _charge_color(float(q), q_max) for i, q in enumerate(charges)
        }

    try:
        _prepare_scene_actor_replacement(plotter, state)
        _remove_static_structure(plotter)
        rep = (
            int(getattr(state, "replication_nx", 1) or 1),
            int(getattr(state, "replication_ny", 1) or 1),
            int(getattr(state, "replication_nz", 1) or 1),
        )
        StructureRenderer(structure_section, reader).add_to_plotter(
            plotter,
            replication=rep,
            show_labels=False,
            representation=str(
                getattr(state, "representation_style", "ball_and_stick")
            ),
            cartoon_color_mode=str(getattr(state, "cartoon_color_mode", "chain")),
            residue_selection=str(getattr(state, "residue_selection", "") or ""),
            atom_colors=atom_colors,
            **_structure_display_options(state),
        )

        # Structure replacement removes the previous charge labels along with
        # all other atom-prefixed actors. Add the property labels only after it.
        if charge_label_mesh is not None:
            plotter.add_mesh(
                charge_label_mesh,
                color="white",
                name="atom_charge_labels",
                lighting=False,
                show_scalar_bar=False,
            )
            register_label_actor(
                plotter, "atom_charge_labels", points, labels, scale=0.4
            )
    except Exception:
        # The wrapper owns neutral-structure restoration, appearance, and the
        # single failure push; strip partial actors before control returns to it.
        _remove_static_structure(plotter)
        raise

    if push:
        _apply_scene_appearance(plotter, state)
        plotter.render()
        _push_view(plotter)
    return True


def _charge_color(q: float, q_max: float) -> tuple[float, float, float]:
    """Map a signed charge to an RGB tint: negative→blue, positive→red,
    intensity scales with ``|q| / q_max``. Returned as 0-1 floats so
    pyvista's ``add_mesh(color=...)`` accepts it directly.
    """
    if q_max <= 0:
        return (0.7, 0.7, 0.7)
    t = max(-1.0, min(1.0, q / q_max))
    if t >= 0:
        return (1.0, 1.0 - 0.7 * t, 1.0 - 0.7 * t)  # white→red
    return (1.0 + 0.7 * t, 1.0 + 0.7 * t, 1.0)  # white→blue


def _remove_atom_charge_labels(plotter: pv.Plotter) -> bool:
    """Strip any prior charge-label overlay; return whether there was one."""
    if "atom_charge_labels" not in plotter.actors:
        return False
    with contextlib.suppress(Exception):
        remove_actor(plotter, "atom_charge_labels")
    return True


def _remove_atom_index_labels(plotter: pv.Plotter) -> None:
    """Hide structure index labels when charge labels are about to be
    drawn at the same positions — avoids two overlapping label sets."""
    with contextlib.suppress(Exception):
        remove_actor(plotter, "atom_index_labels")


def _draw_structure(reader: QVFReader, plotter: pv.Plotter, state: _ViewerUIState) -> None:
    """Draw the reader's current structure, edit overlay included, the way
    every scene path draws it: through :class:`StructureRenderer`, at the
    state's replication, representation, labels and biomolecule options
    (defaults when ``state`` is ``None``, as headless callers pass).

    The CPK restore and the editor both draw through here. The editor used
    to build one sphere actor per atom and one cylinder per bond by hand,
    ignoring the representation and the replication (#37-46, #37-R5).
    """
    from vibeview.renderers.structure import StructureRenderer

    structure_section = next((s for s in reader.sections if s.kind == "structure"), None)
    if structure_section is None:
        return
    rep = (
        int(getattr(state, "replication_nx", 1) or 1),
        int(getattr(state, "replication_ny", 1) or 1),
        int(getattr(state, "replication_nz", 1) or 1),
    )
    StructureRenderer(structure_section, reader).add_to_plotter(
        plotter,
        replication=rep,
        show_labels=bool(getattr(state, "show_atom_labels", False)),
        representation=str(getattr(state, "representation_style", "ball_and_stick")),
        cartoon_color_mode=str(getattr(state, "cartoon_color_mode", "chain")),
        residue_selection=str(getattr(state, "residue_selection", "") or ""),
        **_structure_display_options(state),
    )


def _restore_cpk_atoms(reader: QVFReader, plotter: pv.Plotter, state: _ViewerUIState) -> None:
    """Re-draw atom spheres with their default CPK colour palette.

    Called when the user switches away from an atom_properties section
    whose ``color_by_charge`` toggle left the atoms in the charge-tinted
    red/blue scheme instead of the per-element Jmol colours.
    """
    if not any(s.kind == "structure" for s in reader.sections):
        return
    # Clear the charge-tinted per-atom spheres AND any replica / glyph actors
    # from the overlay, then rebuild the structure in CPK at the *current*
    # replication. Delegating to the structure renderer means a replicated
    # periodic cell is restored via glyph instancing (matching the default
    # view) instead of leaving the overlay's per-replica spheres behind.
    _prepare_scene_actor_replacement(plotter, state)
    _remove_actors_by_prefix(plotter, _Actors.ATOM)
    _draw_structure(reader, plotter, state)
    _apply_scene_appearance(plotter, state)


def _activate_run_record(reader: QVFReader, state: _ViewerUIState, section: Section) -> None:
    """Lazy activation of a run.record section (input + log panel)."""
    from vibeview.renderers.run_record import RunRecordRenderer

    renderer = RunRecordRenderer(section, reader)
    try:
        data = renderer.load()
        state.properties_html = renderer.render_to_html()
        title = f"Run Record ({data.program}"
        if data.program_version:
            title += f" {data.program_version}"
        title += ")"
        state.properties_title = title
        state.run_record_attachments = reader.run_record_attachments(section.id)
        state.run_record_attachment_section = section.id
        state.status_message = f"Loaded run record: {section.id}"
    except QVFError as e:
        state.status_message = f"Error loading run record: {e}"


def _activate_topology_qtaim(
    reader: QVFReader, plotter: pv.Plotter, state: _ViewerUIState, section: Section
) -> None:
    """Lazy activation of a topology.qtaim section."""
    from vibeview.renderers.qtaim import QTAIMRenderer

    renderer = QTAIMRenderer(section, reader)
    state.properties_title = "QTAIM Topology"
    try:
        msg = renderer.add_to_plotter(plotter)
        state.status_message = msg
        # The scalars (rho, Laplacian, ellipticity) are in every
        # topology.qtaim section and are the actual QTAIM answer; the dots
        # alone only say where the critical points are. Reuses the
        # properties-panel iframe.
        try:
            symbols = [str(a.symbol) for a in reader.read_structure().atoms]
        except Exception:
            symbols = None
        state.properties_html = renderer.render_to_html(symbols)
    except QVFError as e:
        state.status_message = f"Error loading QTAIM: {e}"


def _activate_scan_surface(reader: QVFReader, state: _ViewerUIState, section: Section) -> None:
    """Lazy activation of a scan.surface section — render the 2D energy
    heat-map into the plot panel (reuses the trajectory plot image)."""
    import base64

    from vibeview.renderers.scan_surface import ScanSurfaceRenderer

    renderer = ScanSurfaceRenderer(section, reader)
    try:
        png = renderer.render_surface(renderer.min_node())
        if png:
            state.trajectory_energy_image = base64.b64encode(png).decode("utf-8")
        n_a, n_b = renderer.shape
        state.status_message = f"Loaded scan surface: {section.id} ({n_a}×{n_b} grid)"
    except QVFError as e:
        state.status_message = f"Error loading scan surface: {e}"


def _activate_scf_history(reader: QVFReader, state: _ViewerUIState, section: Section) -> None:
    """Lazy activation of an scf_history section."""
    from vibeview.renderers.scf_history import SCFHistoryRenderer

    renderer = SCFHistoryRenderer(section, reader)
    try:
        # Plotly figure → chart channel (scripted iframe).
        state.chart_html = renderer.render_to_html(
            skip_first=bool(getattr(state, "scf_skip_guess", False)),
            log_energy=bool(getattr(state, "scf_log_energy", False)),
        )
        state.chart_title = "SCF Convergence"
        state.status_message = f"Loaded SCF history: {section.id}"
    except QVFError as e:
        state.status_message = f"Error loading SCF history: {e}"


def _section_title(section: Section) -> str:
    """A section's own label, else its kind's title from ``_KIND_TITLES``
    (the one title map; the spectra panel kept a private copy, #37-38)."""
    label = getattr(section, "label", None)
    if label:
        return str(label)
    return _KIND_TITLES.get(section.kind, section.kind)


#: Frame-plot renderers kept per reader and section: each caches its data and
#: one PNG per frame, which a renderer built afresh on every playback step
#: threw away (#37-50).
_FRAME_PLOT_RENDERERS: weakref.WeakKeyDictionary[Any, dict[str, Any]] = (
    weakref.WeakKeyDictionary()
)


def _frame_plot_renderer(reader: QVFReader, section: Section) -> Any:
    """The trajectory or reaction-path renderer for ``section``, reused."""
    from vibeview.renderers.reaction import ReactionPathRenderer
    from vibeview.renderers.trajectory import TrajectoryRenderer

    make = ReactionPathRenderer if section.kind == "reaction.path" else TrajectoryRenderer
    try:
        per_reader = _FRAME_PLOT_RENDERERS.setdefault(reader, {})
    except TypeError:  # a test double that cannot be weakly referenced
        return make(section, reader)
    renderer = per_reader.get(section.id)
    if renderer is None or not isinstance(renderer, make):
        renderer = per_reader[section.id] = make(section, reader)
    return renderer


def _update_trajectory_plot(reader: QVFReader, state: _ViewerUIState) -> None:
    """Update the energy plot for the current trajectory / reaction-path frame."""
    section = next((s for s in reader.sections if s.id == state.selected_section), None)
    if section is None or section.kind not in ("trajectory", "reaction.path"):
        return
    renderer = _frame_plot_renderer(reader, section)
    try:
        img_bytes = renderer.render_energy_plot(state.trajectory_frame)
    except QVFError:
        return
    if img_bytes:
        state.trajectory_energy_image = base64.b64encode(img_bytes).decode("utf-8")


def _prepare_scene_actor_replacement(plotter: pv.Plotter, state: _ViewerUIState) -> None:
    """Discard toon snapshots before their actors are removed or replaced."""
    if not bool(getattr(state, "toon_mode", False)):
        return
    from vibeview.renderers.structure import disable_toon_rendering

    # The toon renderer records pre-toon properties by actor name. Restore the
    # current actors now so replacement actors with reused names cannot consume
    # stale snapshots from the scene that is about to disappear.
    disable_toon_rendering(plotter)


# Actor names under which the viewer registers isosurfaces: sampled
# wavefunction fields and stored volume sections.
_ISOSURFACE_ACTOR_PREFIXES = ("mo_iso_", "volume_")


def _default_background(dark: bool) -> str:
    """The viewer's plain dark or light background colour."""
    return _palette.DARK_BACKGROUND if dark else _palette.LIGHT_BACKGROUND


def _scene_background(state: _ViewerUIState) -> Any:
    """The background the scene should show: an explicit override (a toggle,
    a saved setting or a view preset), else the plain one for the dark flag.
    Every rebuild reads this instead of repeating the two literals (#37-31)."""
    override = getattr(state, "background_override", None)
    if override is not None:
        return override
    return _default_background(bool(getattr(state, "dark_background", True)))


def _set_background_mode(plotter: pv.Plotter, state: _ViewerUIState, dark: bool) -> None:
    """Switch to the plain dark or light background and remember it as the
    override, so a later rebuild or material change keeps it."""
    state.dark_background = dark
    state.background_override = _default_background(dark)
    set_background(plotter, state.background_override)


def _startup_background_override(dark: bool, preset_background: Any) -> str | None:
    """The override that makes the saved dark/light preference win at startup.

    ``None`` when the material preset's own background already agrees; the
    preference was otherwise ignored whenever the two differed (#37-R4).
    """
    preset_dark = sum(pv.Color(preset_background).float_rgb) < 1.5
    return None if preset_dark == dark else _default_background(dark)


def _apply_scene_appearance(plotter: pv.Plotter, state: _ViewerUIState) -> MaterialPreset:
    """Make actor properties and background agree with persistent UI state."""
    from vibeview.material_presets import apply_material_to_actor, get_preset
    from vibeview.renderers.structure import (
        disable_toon_rendering,
        enable_toon_rendering,
    )

    toon_enabled = bool(getattr(state, "toon_mode", False))
    if toon_enabled:
        # Material changes while toon is active need a fresh pre-toon snapshot;
        # otherwise disabling toon later restores properties from the previous
        # material or from actors that a rebuild already removed.
        disable_toon_rendering(plotter)

    preset = get_preset(str(getattr(state, "material_preset", "cpk_glossy")))
    override = getattr(state, "background_override", None)
    background = override if override is not None else preset.background_color
    set_background(plotter, background)  # a hex string or an RGB triple; both are colours
    state.dark_background = sum(pv.Color(background).float_rgb) < 1.5

    # Preserve scalar/CPK colours. Without base_color the material helper
    # deliberately supplies neutral grey, which is not what a scene-wide
    # appearance replay wants.
    preserve_residue_bonds = (
        getattr(state, "atom_color_mode", "element") in ("chain", "structure", "residue", "bfactor")
        or bool(getattr(state, "residue_selection", ""))
    )
    for actor_name, actor in getattr(plotter, "actors", {}).items():
        try:
            prop = actor.GetProperty() if hasattr(actor, "GetProperty") else None
            current = (
                tuple(prop.GetColor())
                if prop is not None and hasattr(prop, "GetColor")
                else None
            )
            apply_material_to_actor(
                actor,
                preset,
                base_color=current,
                is_bond=str(actor_name).startswith("bond") and not preserve_residue_bonds,
                is_isosurface=str(actor_name).startswith(_ISOSURFACE_ACTOR_PREFIXES),
            )
        except Exception:
            pass

    if toon_enabled and not enable_toon_rendering(plotter):
        state.toon_mode = False
    return preset


def _replay_wavefunction_surface(
    reader: QVFReader,
    plotter: pv.Plotter,
    viewer_state: ViewerState,
    state: _ViewerUIState,
) -> bool:
    """Re-render the exact computed surface recorded by the active section."""
    kind = str(getattr(state, "wf_surface_kind", "") or "")
    section_id = getattr(state, "wf_section_id", None)
    if not kind or not section_id:
        return False
    section = next(
        (
            candidate
            for candidate in reader.sections
            if candidate.id == section_id and candidate.kind == "wavefunction.gto"
        ),
        None,
    )
    if section is None:
        return False

    if kind == "mo":
        index = getattr(state, "mo_last_index", None)
        if index is None:
            return False
        _render_mo_volume(
            reader,
            plotter,
            viewer_state,
            state,
            section,
            int(index),
            str(getattr(state, "mo_last_spin", None) or "restricted"),
            component=str(getattr(state, "mo_last_component", None) or "real"),
        )
    elif kind in ("density", "spin_density"):
        state.wf_density_spin = kind == "spin_density"
        _render_wf_density(reader, plotter, viewer_state, state, section)
    elif kind == "elf":
        _render_wf_elf(reader, plotter, viewer_state, state, section)
    elif kind == "nci":
        _render_wf_nci(reader, plotter, viewer_state, state, section)
    elif kind == "laplacian":
        _render_wf_laplacian(reader, plotter, viewer_state, state, section)
    else:
        return False
    return True


def _finalize_wavefunction_surface_appearance(
    plotter: pv.Plotter,
    state: _ViewerUIState,
) -> None:
    """Realize persistent material/toon state on newly created iso actors."""
    _apply_scene_appearance(plotter, state)
    plotter.render()
    _push_view(plotter)


def _fail_wavefunction_surface(
    plotter: pv.Plotter,
    state: _ViewerUIState,
    message: str,
    *,
    push: bool = True,
) -> None:
    """Make a failed computed render leave scene and visibility consistent."""
    with contextlib.suppress(Exception):
        _remove_actors_by_prefix(plotter, _Actors.MO_SURFACE)
    state.mo_visible = False
    state.volume_loaded = False
    state.status_message = message
    if push:
        # A failed renderer may have replaced actors while toon snapshots still
        # described their predecessors. Refresh the surviving scene so
        # appearance state remains truthful and stale snapshots are discarded.
        with contextlib.suppress(Exception):
            _apply_scene_appearance(plotter, state)
        with contextlib.suppress(Exception):
            plotter.render()
        _push_view(plotter)


def _reapply_raytrace(plotter: pv.Plotter, state: _ViewerUIState) -> None:
    """Re-attach the OSPRay render pass after a scene rebuild dropped it.

    `plotter.clear()` + the SSAO re-setup replace the renderer's render pass, so
    a rebuild silently drops OSPRay while `raytrace_enabled` stays True and the
    indicator lies (audit L2). Re-attach the pass to match the flag; if re-attach
    fails, clear the flag instead so the indicator never claims a pass that
    isn't there. No-op when raytrace is off.
    """
    if not getattr(state, "raytrace_enabled", False):
        return
    try:
        import vtk

        plotter._ospray_pass = vtk.vtkOSPRayPass()
        plotter.renderer.SetPass(plotter._ospray_pass)
    except Exception:
        state.raytrace_enabled = False


def _rebuild_scene(
    reader: QVFReader,
    plotter: pv.Plotter,
    viewer_state: ViewerState,
    state: _ViewerUIState,
) -> str | None:
    """Serialize scene replacement against worker-thread computed renders."""
    render_lock = _scene_hooks(plotter).wf_render_lock
    if render_lock is None:
        return _rebuild_scene_unlocked(reader, plotter, viewer_state, state)
    with render_lock:
        return _rebuild_scene_unlocked(reader, plotter, viewer_state, state)


def _reset_scene(plotter: pv.Plotter, state: _ViewerUIState) -> None:
    """Empty the scene for a full rebuild and restore its render setup.

    Shared by the scene rebuild and the file-switch reload, which kept
    separate copies (#37-33, #37-R2). The reload's copy never removed a
    stale SSAO pass: PyVista's ``clear()`` keeps the renderer pass, so
    ambient occlusion switched off came back on the next file.
    """
    _prepare_scene_actor_replacement(plotter, state)
    plotter.clear()
    set_background(plotter, _scene_background(state))
    if getattr(state, "ssao_enabled", False):
        try:
            from vibeview.material_presets import ambient_occlusion_pass

            ambient_occlusion_pass(plotter, **_SSAO_PASS)
        except Exception:
            pass
    else:
        with contextlib.suppress(Exception):
            plotter.renderer.SetPass(None)
    _reapply_raytrace(plotter, state)  # keep the raytrace indicator truthful (L2)


def _rebuild_scene_unlocked(
    reader: QVFReader,
    plotter: pv.Plotter,
    viewer_state: ViewerState,
    state: _ViewerUIState,
) -> str | None:
    """Rebuild the 3D scene; return an error message when replay is incomplete."""
    rebuild_error = None
    _reset_scene(plotter, state)

    from vibeview.renderers.structure import StructureRenderer

    structure_section = next((s for s in reader.sections if s.kind == "structure"), None)
    if structure_section is not None:
        structure_renderer = StructureRenderer(structure_section, reader)
        try:
            structure_renderer.add_to_plotter(
                plotter,
                replication=viewer_state.replication,
                show_labels=bool(state.show_atom_labels),
                representation=str(getattr(state, "representation_style", "ball_and_stick")),
                cartoon_color_mode=str(getattr(state, "cartoon_color_mode", "chain")),
                residue_selection=str(getattr(state, "residue_selection", "") or ""),
                **_structure_display_options(state),
            )
        except Exception as e:
            rebuild_error = f"Structure rebuild error: {e}"
            state.status_message = rebuild_error
    else:
        rebuild_error = "No structure section in file"
        state.status_message = rebuild_error

    # Re-add active volume if any
    vol_id = state.active_volume_id
    if vol_id:
        _rebuild_volume(reader, plotter, viewer_state, state, vol_id)
        if getattr(state, "wf_surface_kind", None):
            # Section activation normally makes these mutually exclusive.
            # Prefer the archive-backed volume if stale state presents both,
            # and retire the stale computed recipe so its switch cannot add a
            # second surface beside the archive-backed volume later.
            state.wf_surface_kind = None
            state.wf_section_id = None
            state.wf_animating = False
            state.mo_last_index = None
            state.mo_last_spin = None
            state.wf_density_spin = False
            state.mo_visible = False
    elif bool(getattr(state, "mo_visible", False)) and getattr(
        state, "wf_surface_kind", None
    ):
        # Computed wavefunction surfaces are not QVF-backed volumes, so replay
        # their explicit recipe after actor replacement. A stored volume takes
        # precedence if inconsistent state somehow presents both.
        try:
            restored = _replay_wavefunction_surface(
                reader, plotter, viewer_state, state
            )
        except Exception as e:
            restored = False
            failure_message = f"Computed surface rebuild error: {e}"
        else:
            failure_message = "Computed surface could not be restored"
        if not restored:
            # Keep the recipe for an explicit retry, but never leave the
            # visibility control claiming actors that were not restored.
            _fail_wavefunction_surface(
                plotter, state, failure_message, push=False
            )
            rebuild_error = failure_message

    if bool(getattr(state, "atom_properties_active", False)) and getattr(
        state, "atom_properties_section_id", None
    ):
        # The structure rebuild may have recreated index labels. Charge labels
        # occupy the same positions, so remove the former before replaying the
        # active property overlay and its optional atom tint.
        _remove_atom_index_labels(plotter)
        try:
            _render_atom_properties_overlay(reader, plotter, state, push=False)
        except Exception as e:
            rebuild_error = f"Atomic-property overlay rebuild error: {e}"
            state.atom_properties_active = False
            state.atom_properties_section_id = None
            _remove_atom_charge_labels(plotter)
            state.status_message = rebuild_error

    show_grid(plotter)
    _apply_scene_appearance(plotter, state)
    # Successful overlay helpers update status themselves. If an earlier
    # rebuild stage failed, restore that diagnostic after every later replay
    # so the UI never replaces an incomplete-scene error with false success.
    if rebuild_error is not None:
        state.status_message = rebuild_error
    view_isometric(plotter)
    plotter.render()
    # VtkLocalView caches geometry on the client; a plotter.clear() + re-add
    # leaves the client looking at the old scene unless we explicitly push.
    _push_view(plotter)
    return rebuild_error


def _get_lattice_vectors(reader: QVFReader) -> np.ndarray | None:
    """Return the [3,3] lattice vectors from the structure section, or None."""
    try:
        structure = reader.read_structure()
        if structure.lattice_vectors is not None and any(structure.pbc):
            return structure.lattice_vectors
    except QVFError:
        pass
    return None


def _orbital_is_wrappable(section: Section) -> bool:
    """Whether a volume section may be minimum-image recentred.

    Producers tag a localized orbital / Wannier function with
    ``orbital_kind = "localized"``; a canonical, Bloch-like crystalline
    orbital (``orbital_kind = "canonical"`` / ``"delocalized"``) must never
    be recentred, since it has no single well-defined centre. An unmarked
    section returns True and falls back to the wrap function's own
    localization guard (the circular-resultant threshold).
    """
    extra = getattr(section, "model_extra", None) or {}
    kind = extra.get("orbital_kind")
    if kind is None:
        return True
    return str(kind).strip().lower() in ("localized", "local", "wannier")




def _remove_actors_by_prefix(plotter: pv.Plotter, prefix: str) -> int:
    """Remove all actors whose name starts with the given prefix.

    Returns how many were removed, so a caller can tell whether the scene
    changed and needs a push (#37-49).
    """
    removed = 0
    for key in list(plotter.actors.keys()):
        if isinstance(key, str) and key.startswith(prefix):
            remove_actor(plotter, key)
            removed += 1
    return removed


def _remove_static_structure(plotter: pv.Plotter) -> None:
    """Remove static atoms, bonds, ribbons, unit-cell wireframe, and labels.

    Used to hide the frozen equilibrium geometry while an animation
    (trajectory / vibrations / reaction.path) draws its own per-frame atoms,
    so the two don't ghost on top of each other (audit finding A5-03). The
    ``atom_`` prefix also matches the ``atom_index_labels`` /
    ``atom_charge_labels`` overlays (intended); it does NOT match
    ``traj_atom_`` / ``vib_atom_`` (which start with traj_/vib_).
    Editor rebuilds use the separate ``structure_`` namespace, which is
    static too and must disappear before compare or animation actors render.
    """
    # ``bond_`` is the per-bond path; ``bonds_`` covers the large-system
    # batched path and its selected/unselected partitions. Cartoon ribbons
    # are static structure too and otherwise ghost behind animated atoms.
    for prefix in (
        _Actors.ATOM,
        _Actors.BOND,
        _Actors.BONDS,
        _Actors.CARTOON,
        _Actors.HYDROGEN_BOND,
        _Actors.CELL,
        _Actors.EDITOR,
    ):
        _remove_actors_by_prefix(plotter, prefix)


def _update_pick_markers(reader: QVFReader, plotter: pv.Plotter, picked: list[int]) -> None:
    """Draw translucent yellow highlight spheres on the selected atoms."""
    _remove_actors_by_prefix(plotter, _Actors.PICK_MARKER)
    if not picked:
        return
    try:
        sdata = reader.read_structure()
    except Exception:  # noqa: BLE001
        return
    from vibeview.renderers.structure import cpk_radius

    for order, idx in enumerate(picked):
        if idx >= len(sdata.atoms):
            continue
        atom = sdata.atoms[idx]
        radius = cpk_radius(atom.atomic_number, 0.35 * 1.5)
        sphere = pv.Sphere(
            radius=radius, center=atom.position, theta_resolution=16, phi_resolution=16
        )
        plotter.add_mesh(
            sphere,
            color=_palette.PICK_MARKER,
            opacity=0.4,
            name=f"pick_marker_{order}",
            show_scalar_bar=False,
        )


def _update_edit_highlights(
    reader: QVFReader, plotter: pv.Plotter, selected: list[int]
) -> None:
    """Draw orange wireframe highlight spheres on edit-selected atoms."""
    _remove_actors_by_prefix(plotter, _Actors.EDIT_HIGHLIGHT)
    if not selected:
        return
    try:
        sdata = reader.read_structure()
    except Exception:
        return
    from vibeview.renderers.structure import cpk_radius

    for order, idx in enumerate(selected):
        if idx >= len(sdata.atoms):
            continue
        atom = sdata.atoms[idx]
        radius = cpk_radius(atom.atomic_number, 0.35) * 1.6
        sphere = pv.Sphere(
            radius=radius, center=atom.position, theta_resolution=12, phi_resolution=12
        )
        plotter.add_mesh(
            sphere,
            color=_palette.EDIT_HIGHLIGHT,
            opacity=0.3,
            style="wireframe",
            name=f"edit_highlight_{order}",
            show_scalar_bar=False,
        )


def _update_frozen_highlights(
    reader: QVFReader, plotter: pv.Plotter, frozen: list[int]
) -> None:
    """Draw blue 'locked' wireframe spheres on atoms held fixed during live-opt.

    Distinct from the orange edit-selection highlight (``_update_edit_highlights``)
    so a frozen atom reads differently from a merely-selected one.
    """
    _remove_actors_by_prefix(plotter, _Actors.FROZEN_HIGHLIGHT)
    if not frozen:
        return
    try:
        sdata = reader.read_structure()
    except Exception:
        return
    from vibeview.renderers.structure import cpk_radius

    for order, idx in enumerate(frozen):
        if idx >= len(sdata.atoms):
            continue
        atom = sdata.atoms[idx]
        radius = cpk_radius(atom.atomic_number, 0.35) * 1.9
        sphere = pv.Sphere(
            radius=radius, center=atom.position, theta_resolution=12, phi_resolution=12
        )
        plotter.add_mesh(
            sphere,
            color=_palette.FROZEN_HIGHLIGHT,
            opacity=0.28,
            style="wireframe",
            line_width=2,
            name=f"frozen_highlight_{order}",
            show_scalar_bar=False,
        )


def _reset_frozen(plotter: pv.Plotter, state: _ViewerUIState) -> None:
    """Clear the frozen-atom set and its markers.

    Called wherever atom indices are reindexed (delete / undo / redo / reload),
    since a frozen index no longer points at the same atom afterward.
    """
    if state.frozen_atoms:
        state.frozen_atoms = []
    _remove_actors_by_prefix(plotter, _Actors.FROZEN_HIGHLIGHT)


def _structure_edit_snapshot(structure: StructureData) -> dict[str, Any]:
    """Return one serializable atoms+lattice undo/redo transaction."""
    lattice = structure.lattice_vectors
    return {
        "positions": [atom.position.tolist() for atom in structure.atoms],
        "symbols": [atom.symbol for atom in structure.atoms],
        "lattice_vectors": (
            None if lattice is None else np.asarray(lattice, dtype=float).tolist()
        ),
    }


def _rebuild_structure_from_positions(
    reader: QVFReader,
    plotter: pv.Plotter,
    positions_list: Sequence[Sequence[float]] | np.ndarray,
    symbols_list: Sequence[str] | None = None,
    *,
    lattice_vectors: np.ndarray | Sequence[Sequence[float]] | None = None,
) -> None:
    """Record an edited geometry on the reader and redraw the structure.

    ``symbols_list`` defaults to the current symbols; ``lattice_vectors``,
    when supplied, replaces the edit-overlay cell in the same operation. The
    scene is drawn by :func:`_draw_structure`, as every other structure
    path draws it, so an edited crystal keeps its bonds through the cell
    faces, its wireframe, its representation and its replication.
    """
    import numpy as np

    # Remove old structure actors (original and any previous edit-mode actors)
    invalidate = _scene_hooks(plotter).invalidate_relocalization
    if invalidate is not None:
        invalidate()
    _remove_static_structure(plotter)

    if not positions_list:
        return

    positions = np.array(positions_list, dtype=float)
    n = len(positions)

    if symbols_list is None:
        # Keep current symbols from reader
        try:
            sdata = reader.read_structure()
            symbols_list = [a.symbol for a in sdata.atoms]
        except Exception:
            return

    # Truncate symbols to match positions length
    symbols_list = list(symbols_list)[:n]
    if len(symbols_list) < n:
        # Pad with carbon if symbols_list is shorter
        symbols_list = list(symbols_list) + ["C"] * (n - len(symbols_list))

    # Record the edited geometry on the reader so the next handler (and
    # every other read_structure consumer) sees it — without this, each
    # edit re-read the pristine file and dropped all earlier edits.
    if hasattr(reader, "set_edit_overlay"):
        reader.set_edit_overlay(
            positions,
            symbols_list,
            lattice_vectors=lattice_vectors,
        )
    refresh = _scene_hooks(plotter).refresh_relocalization
    if refresh is not None:
        refresh()

    _draw_structure(reader, plotter, _scene_hooks(plotter).state)
    plotter.render()


def _add_atom_to_structure(
    reader: QVFReader, plotter: pv.Plotter, position: Sequence[float], symbol: str = "C"
) -> None:
    """Add a new atom to the structure visualization.

    Reads current structure, appends the new atom, rebuilds the scene.
    """
    import numpy as np

    try:
        sdata = reader.read_structure()
        new_positions = [a.position.tolist() for a in sdata.atoms]
        new_symbols = [a.symbol for a in sdata.atoms]
    except Exception:
        new_positions = []
        new_symbols = []

    new_positions.append(np.asarray(position, dtype=float).tolist())
    new_symbols.append(symbol)
    _rebuild_structure_from_positions(reader, plotter, new_positions, new_symbols)


# Interactive-measure hint: 2 atoms → distance, 3 → angle, 4 → dihedral.
_MEASURE_HINT = "Measure: click 2 atoms → distance, 3 → angle, 4 → dihedral."
# First line of the ruler button's bond table; a unit change rebuilds a
# readout that starts with it.
_BOND_DISTANCES_HEADER = "── Bond Distances ──"


def _measure_text(sdata: StructureData, picked: list[int], length_unit: str = "angstrom") -> str:
    """Build the measurement readout from the picked-atom order:
    1 → label, 2 → distance, 3 → distance chain + angle, 4 → + dihedral.

    ``length_unit`` (a ``vibeview.units.LENGTH_UNITS`` key) sets the unit of
    the distance; positions stay in Angstrom (#58)."""
    import numpy as _np

    def _lbl(i: int) -> str:
        return f"{sdata.atoms[i].symbol}{i + 1}"

    if not picked:
        return _MEASURE_HINT
    pts = [_np.asarray(sdata.atoms[i].position, dtype=float) for i in picked]
    sel = " · ".join(_lbl(i) for i in picked)
    lines = [f"Selected: {sel}", "(click an atom again to deselect)"]
    if len(picked) >= 2:
        d = float(_np.linalg.norm(pts[1] - pts[0]))
        lines.append(
            f"Distance {_lbl(picked[0])}–{_lbl(picked[1])}: {format_length(d, length_unit)}"
        )
    if len(picked) >= 3:
        v1 = pts[0] - pts[1]
        v2 = pts[2] - pts[1]
        cos = float(_np.dot(v1, v2) / (_np.linalg.norm(v1) * _np.linalg.norm(v2) + 1e-12))
        ang = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
        lines.append(f"Angle {_lbl(picked[0])}–{_lbl(picked[1])}–{_lbl(picked[2])}: {ang:.1f}°")
    if len(picked) >= 4:
        b1 = pts[1] - pts[0]
        b2 = pts[2] - pts[1]
        b3 = pts[3] - pts[2]
        n1 = _np.cross(b1, b2)
        n2 = _np.cross(b2, b3)
        m = _np.cross(n1, b2 / (_np.linalg.norm(b2) + 1e-12))
        x = float(_np.dot(n1, n2))
        y = float(_np.dot(m, n2))
        dih = math.degrees(math.atan2(y, x))
        lines.append(
            f"Dihedral {_lbl(picked[0])}–{_lbl(picked[1])}–"
            f"{_lbl(picked[2])}–{_lbl(picked[3])}: {dih:.1f}°"
        )
    return "\n".join(lines)


def _rebuild_crossfade(
    reader: QVFReader,
    plotter: pv.Plotter,
    viewer_state: ViewerState,
    state: _ViewerUIState,
    section_id_a: str,
    section_id_b: str,
    blend: float,
) -> None:
    """Render two volume isosurfaces with blended opacity.

    ``blend=0`` shows only section A, ``blend=1`` shows only section B.
    Both isosurfaces are rendered simultaneously with adjusted opacity.

    A signed kind (:data:`vibeview.kinds.SIGNED_VOLUME_KINDS`) draws both
    lobes of the stored array exactly as :func:`_rebuild_volume` does (the
    section's ``component`` is a label only): ``volume_{id}_pos`` in
    ``palette.ISO_POSITIVE`` and
    ``volume_{id}_neg`` in ``palette.ISO_NEGATIVE`` (#49, #38-19). Other
    kinds keep the single colour-mapped surface.
    """
    from vibeview.kinds import SIGNED_VOLUME_KINDS
    from vibeview.renderers.palette import ISO_NEGATIVE, ISO_POSITIVE
    from vibeview.renderers.volume import VolumeRenderer

    _remove_actors_by_prefix(plotter, _Actors.VOLUME)
    lattice = _get_lattice_vectors(reader)
    rep = viewer_state.replication

    for section_id, opacity_scale in [(section_id_a, 1.0 - blend), (section_id_b, blend)]:
        section = next((s for s in reader.sections if s.id == section_id), None)
        if section is None:
            continue

        renderer = VolumeRenderer(section, reader)
        try:
            renderer.load_grid()
            raw_data = renderer.load_data()
        except QVFError:
            continue

        hints = viewer_state.get_volume_hints(section_id, kind=section.kind)
        opacity = hints.opacity * opacity_scale
        if opacity < 0.01:
            continue

        if section.kind in SIGNED_VOLUME_KINDS:
            iso = hints.isovalue
            for data, color, suffix in (
                (np.clip(raw_data, 0, None), ISO_POSITIVE, "pos"),
                (np.clip(-raw_data, 0, None), ISO_NEGATIVE, "neg"),
            ):
                lobe = _build_signed_contour(renderer, data, iso, rep, lattice)
                if lobe is None:
                    continue
                plotter.add_mesh(
                    lobe,
                    color=color,
                    opacity=opacity,
                    name=f"volume_{section_id}_{suffix}",
                    show_scalar_bar=False,
                )
            continue

        mesh = renderer.make_mesh(hints, replication=rep, lattice_vectors=lattice)
        if mesh is None:
            continue

        plotter.add_mesh(
            mesh,
            scalars="values",
            cmap=cmap_argument(hints.colormap),
            opacity=opacity,
            name=f"volume_{section_id}",
            show_scalar_bar=False,
        )

    plotter.render()
    _push_view(plotter)


def _render_trajectory_atoms(reader: QVFReader, plotter: pv.Plotter, state: _ViewerUIState) -> None:
    """Render atoms for the current trajectory / reaction-path frame."""

    from vibeview.renderers.reaction import ReactionPathRenderer
    from vibeview.renderers.structure import cpk_color, cpk_radius
    from vibeview.renderers.trajectory import TrajectoryRenderer

    section_id = state.selected_section
    if not section_id:
        return
    section = next((s for s in reader.sections if s.id == section_id), None)
    if section is None or section.kind not in ("trajectory", "reaction.path"):
        return

    if section.kind == "reaction.path":
        renderer: TrajectoryRenderer | ReactionPathRenderer = ReactionPathRenderer(section, reader)
    else:
        renderer = TrajectoryRenderer(section, reader)
    try:
        renderer.load()
    except QVFError:
        return

    frame = state.trajectory_frame
    if frame < 0 or frame >= renderer.n_frames:
        return

    atoms = renderer.get_frame(frame)
    if not atoms:
        return

    # Hide the static equilibrium structure so it doesn't ghost behind the
    # animated frame (A5-03); remove previous trajectory atoms + cell + iso.
    _remove_static_structure(plotter)
    state.structure_hidden = True
    _remove_actors_by_prefix(plotter, _Actors.TRAJ_ATOM)
    _remove_actors_by_prefix(plotter, _Actors.TRAJ_CELL)
    _remove_actors_by_prefix(plotter, _Actors.TRAJ_VOLUME)

    znums = [a.atomic_number for a in renderer.load().atoms]
    ball_scale = 0.35
    for i, (_symbol, pos) in enumerate(atoms):
        z = znums[i] if i < len(znums) else 0
        sphere = pv.Sphere(
            radius=cpk_radius(z, ball_scale),
            center=pos,
            theta_resolution=10,
            phi_resolution=10,
        )
        plotter.add_mesh(
            sphere,
            color=cpk_color(z),
            smooth_shading=True,
            name=f"traj_atom_{i}",
        )

    # Cell wireframe for periodic reaction.path (QVF v2). The
    # ReactionPathRenderer exposes cell_edges_for_frame; the
    # TrajectoryRenderer doesn't (returns None on getattr). Variable-
    # cell forward-compat: emit per frame so the box can animate.
    cell_edges_fn = getattr(renderer, "cell_edges_for_frame", None)
    if cell_edges_fn is not None:
        edges = cell_edges_fn(frame)
        if edges is not None:
            for i, (p1, p2) in enumerate(edges):
                line = pv.Line(p1, p2)
                plotter.add_mesh(
                    line,
                    color=_palette.FRAME_CELL_EDGE,
                    line_width=2,
                    name=f"traj_cell_{i}",
                )

    # Per-frame volume surfaces for reaction.path archives (W1), including
    # both lobes of signed fields. Absent on geometry-only paths.
    # Wrapped defensively — a contouring
    # failure must not break the geometry animation.
    if isinstance(renderer, ReactionPathRenderer):
        try:
            for suffix, color, iso_mesh in renderer.volume_surfaces_for_frame(frame):
                plotter.add_mesh(
                    iso_mesh,
                    color=color,
                    opacity=0.45,
                    smooth_shading=True,
                    name=f"traj_volume_{suffix}",
                )
        except Exception:  # noqa: BLE001 — viz overlay must not crash anim
            pass

    plotter.render()
    _push_view(plotter)


def _activate_reaction_path(reader: QVFReader, state: _ViewerUIState, section: Section) -> None:
    """Lazy activation of a reaction.path section.

    Reaction paths share the trajectory binary layout, so we reuse the
    trajectory animation state. The energy plot below the viewport
    swaps from the trajectory renderer to the reaction renderer so the
    waypoint markers show up.
    """
    from vibeview.renderers.reaction import ReactionPathRenderer

    renderer = ReactionPathRenderer(section, reader)
    try:
        renderer.load()
        state.trajectory_frame = 0
        state.trajectory_n_frames = renderer.n_frames
        state.trajectory_playing = False
        state.reaction_waypoints = [
            {
                "frame_index": wp.frame_index,
                "label": wp.label,
                "kind": wp.kind,
                "energy_eh": wp.energy_eh,
            }
            for wp in renderer.waypoints()
        ]
        state.reaction_current_label = ""
        state.trajectory_energy_image = ""
        state.status_message = (
            f"Loaded reaction path: {section.id} "
            f"({renderer.n_frames} frames, {len(renderer.waypoints())} waypoints)"
        )
        img_bytes = renderer.render_energy_plot(0)
        if img_bytes:
            state.trajectory_energy_image = base64.b64encode(img_bytes).decode("utf-8")
    except QVFError as e:
        state.status_message = f"Error loading reaction path: {e}"


def _activate_reaction_waypoints(
    reader: QVFReader,
    state: _ViewerUIState,
    section: Section,
) -> None:
    """Lazy activation of a reaction.waypoints annotation.

    Resolves the referenced trajectory, switches the trajectory state
    to that section, and overlays the waypoint markers.
    """
    from vibeview.renderers.reaction import ReactionWaypointsRenderer
    from vibeview.renderers.trajectory import TrajectoryRenderer

    renderer = ReactionWaypointsRenderer(section, reader)
    try:
        data = renderer.load()
    except QVFError as e:
        state.status_message = f"Error loading waypoints: {e}"
        return

    if not reader.has_section(data.trajectory_ref):
        state.status_message = (
            f"reaction.waypoints {section.id!r}: trajectory_ref "
            f"{data.trajectory_ref!r} not found in archive."
        )
        return
    traj_section = reader.get_section(data.trajectory_ref)
    if traj_section.kind != "trajectory":
        state.status_message = (
            f"reaction.waypoints {section.id!r}: trajectory_ref "
            f"{data.trajectory_ref!r} resolves to non-trajectory section."
        )
        return

    traj = TrajectoryRenderer(traj_section, reader)
    try:
        traj.load()
    except QVFError as e:
        state.status_message = f"Error loading referenced trajectory: {e}"
        return

    state.selected_section = data.trajectory_ref
    state.trajectory_frame = 0
    state.trajectory_n_frames = traj.n_frames
    state.trajectory_playing = False
    state.reaction_waypoints = [
        {
            "frame_index": wp.frame_index,
            "label": wp.label,
            "kind": wp.kind,
            "energy_eh": wp.energy_eh,
        }
        for wp in data.waypoints
    ]
    state.status_message = f"Loaded waypoints: {section.id} (over trajectory {data.trajectory_ref})"
    _update_trajectory_plot(reader, state)


def _relocalize_source_section(reader: QVFReader, section_id: str) -> str:
    wf = reader.read_wavefunction_gto(section_id)
    return str((wf.relocalization or {}).get("source_section_id", section_id))


def _refresh_relocalize_state(reader: QVFReader, state: _ViewerUIState) -> None:
    from vibeview.relocalize import method_options

    options: list[dict[str, str]] = []
    reason = "Select a wavefunction with archived occupied coefficients"
    if state.wf_section_id:
        try:
            sid = _relocalize_source_section(reader, state.wf_section_id)
            options, reason = method_options(
                reader,
                state.relocalize_capabilities,
                sid,
                source=state.relocalize_source or "supplied",
            )
        except (QVFError, ValueError) as exc:
            reason = str(exc)
    state.relocalize_method_options = options
    state.relocalize_supported = bool(options)
    state.relocalize_reason = reason
    if options and state.relocalize_method not in [o["value"] for o in options]:
        state.relocalize_method = options[0]["value"]


def _activate_wavefunction(reader: QVFReader, state: _ViewerUIState, section: Section) -> None:
    """Lazy activation of a wavefunction.gto section."""
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    renderer = WavefunctionRenderer(section, reader)
    try:
        wf = renderer.load()
        section_id = section.id
        rows = renderer.mo_table(energy_unit=state.energy_unit)
        state.wf_section_id = section_id
        state.wf_component = "real"
        state.wf_is_complex = any(
            c is not None and np.iscomplexobj(c)
            for c in (wf.mo_coefficients, wf.mo_coefficients_alpha, wf.mo_coefficients_beta)
        )
        _refresh_relocalize_state(reader, state)
        metadata = wf.relocalization or {}
        state.relocalize_has_overlay = bool(metadata)
        state.relocalize_result_summary = ""
        state.relocalize_charge_rows = []
        if metadata:
            source_label = (
                "New RHF calculation"
                if metadata.get("scf_performed")
                else "Archived occupied subspace"
            )
            audit = metadata.get("validation", {})
            state.relocalize_result_summary = (
                f"{source_label}. Charges/populations: {metadata.get('population_model')}. "
                f"Orthonormality error: {audit.get('orthonormality_error', 0):.2g}; "
                f"subspace residual: {audit.get('subspace_residual', 0):.2g}. "
                "Localization convergence is not certified by the kernel. "
                + " ".join(metadata.get("warnings") or [])
            )
            atoms = reader.read_structure(wf.structure_ref).atoms
            state.relocalize_charge_rows = [
                {"atom": f"{i + 1} {a.symbol}", "charge": f"{q:+.6f}"}
                for i, (a, q) in enumerate(zip(atoms, metadata["charges"], strict=True))
            ]
        state.wf_mo_rows = rows
        # Default the picker to the HOMO (most-requested orbital), keyed on
        # the composite "{spin}:{index}" value the dropdown now uses.
        default = _frontier_row(rows, "HOMO") or (rows[0] if rows else None)
        state.wf_selected_mo = default["value"] if default else "restricted:0"
        state.wf_selected_spin = default["spin"] if default else "restricted"
        # Surface orbital_kind (canonical/natural/localized) and set
        # a periodicity-aware panel title.
        orbital_kind = str(getattr(wf, "orbital_kind", "canonical") or "canonical")
        kind_display = orbital_kind.capitalize()
        state.wf_orbital_kind = orbital_kind
        # Spin density only exists for an unrestricted set.
        state.wf_spin_unrestricted = str(getattr(wf, "spin", "")) == "unrestricted"
        state.wf_panel_title = f"{kind_display} {_orbital_label(state.is_periodic, plural=True)}"
        state.status_message = (
            f"Loaded {kind_display.lower()} wavefunction: {section_id}"
            f" ({len(rows)} MOs, spin={wf.spin})"
        )
    except QVFError as e:
        state.status_message = f"Error loading wavefunction: {e}"


def _parse_mo_key(mo_key: Any) -> tuple[str, int]:
    """Parse a picker value into ``(spin, index)``.

    Accepts the composite ``"{spin}:{index}"`` form emitted by
    ``mo_table()``, and degrades gracefully to a bare integer (treated as
    restricted) so older state or hand-built calls still work.
    """
    if isinstance(mo_key, str) and ":" in mo_key:
        spin, _, idx = mo_key.partition(":")
        try:
            return (spin or "restricted", int(idx))
        except ValueError:
            return ("restricted", 0)
    try:
        return ("restricted", int(mo_key))
    except (TypeError, ValueError):
        return ("restricted", 0)


def _step_mo_selection(state: _ViewerUIState, direction: int) -> str | None:
    """Advance the wavefunction picker and return its composite MO key."""
    rows = state.wf_mo_rows or []
    if not rows:
        return None
    current = state.wf_selected_mo or "restricted:0"
    try:
        idx = next(i for i, row in enumerate(rows) if row.get("value") == current)
    except StopIteration:
        idx = 0
    new_idx = max(0, min(len(rows) - 1, idx + int(direction)))
    selected = str(rows[new_idx].get("value", current))
    state.wf_selected_mo = selected
    return selected


def _render_mo_volume(
    reader: QVFReader,
    plotter: pv.Plotter,
    viewer_state: ViewerState,
    state: _ViewerUIState,
    section: Section,
    mo_index: int,
    spin: str,
    *,
    update_state: bool = True,
    component: str | None = None,
) -> str:
    """Evaluate an MO on a grid and render the resulting isosurface."""
    import logging
    import time

    from vibeview.renderers.wavefunction import WavefunctionRenderer
    from vibeview.viewer_defaults import VolumeHints

    # Timed breadcrumbs: an MO render can appear to "freeze" the viewer if any
    # step is slow (grid eval, contouring, or the client-side push). Logging
    # each stage with its duration turns a hang into a diagnosable last-step.
    _log = logging.getLogger("vibeview.render")
    n_per_dim = int(state.wf_n_per_dim or 60)
    _log.info("render_mo: start mo=#%d spin=%s grid=%d^3", mo_index, spin, n_per_dim)

    wf_renderer = WavefunctionRenderer(section, reader)
    _t = time.perf_counter()
    component = component or getattr(state, "wf_component", None) or "real"
    grid, values = wf_renderer.evaluate_mo(
        mo_index,
        spin=spin,
        n_per_dim=n_per_dim,
        component=component,
    )
    _log.info(
        "render_mo: evaluate_mo done in %.2fs (grid %s)",
        time.perf_counter() - _t,
        tuple(int(s) for s in values.shape),
    )

    # Build an ad-hoc VolumeRenderer-compatible mesh by stuffing the
    # grid+data into a thin shim — we can't use the lazy QVF path
    # because this MO doesn't exist as a stored volume section.
    hints = VolumeHints(isovalue=0.05, colormap="coolwarm", opacity=0.6)
    # Honour the Opacity slider. MO lobes are their own actors, so they
    # were pinned at 0.6 while the slider only ever drove "volume" ones.
    _mo_opacity = float(getattr(state, "mo_opacity", 1.0) or 1.0)
    # Use a positive + negative isosurface pair to render MO lobes.
    iso_pos = float(state.isovalue or hints.isovalue)
    iso_pos = max(iso_pos, 0.001)

    _remove_actors_by_prefix(plotter, _Actors.MO_SURFACE)

    # The shared contour helper handles both Cartesian and skew cell grids.
    from vibeview.renderers.volume import build_isosurface_mesh

    _t = time.perf_counter()
    contour_pos = build_isosurface_mesh(values, grid, +iso_pos)
    contour_neg = build_isosurface_mesh(values, grid, -iso_pos)
    if contour_pos is None or contour_neg is None:
        raise ValueError("MO isosurface extraction returned no mesh")
    _log.info(
        "render_mo: contour done in %.2fs (|iso|=%.3f, %d + %d points)",
        time.perf_counter() - _t,
        iso_pos,
        contour_pos.n_points,
        contour_neg.n_points,
    )

    # Replicate across periodic images for crystalline systems.
    lattice = _get_lattice_vectors(reader)
    rep = viewer_state.replication
    if lattice is not None and any(r > 1 for r in rep):
        from vibeview.renderers.volume import _replicate_mesh

        if contour_pos.n_points:
            contour_pos = _replicate_mesh(contour_pos, lattice, rep)
        if contour_neg.n_points:
            contour_neg = _replicate_mesh(contour_neg, lattice, rep)

    # One lobe convention everywhere (#38-19): + blue, − red.
    from vibeview.renderers.palette import ISO_NEGATIVE, ISO_POSITIVE

    if contour_pos.n_points:
        plotter.add_mesh(
            contour_pos,
            color=ISO_POSITIVE,
            opacity=_mo_opacity,
            name="mo_iso_pos",
            show_scalar_bar=False,
        )
    if contour_neg.n_points:
        plotter.add_mesh(
            contour_neg,
            color=ISO_NEGATIVE,
            opacity=_mo_opacity,
            name="mo_iso_neg",
            show_scalar_bar=False,
        )
    _log.info(
        "render_mo: actors ready (%d + %d isosurface points)",
        contour_pos.n_points,
        contour_neg.n_points,
    )
    msg = f"Rendered MO #{mo_index} ({spin}, {component}) at |iso|={iso_pos:.3f}"
    if not contour_pos.n_points and not contour_neg.n_points:
        msg = (
            f"No {component} orbital surface at |iso|={iso_pos:.3f}; "
            "lower the isovalue or choose another component"
        )
    if wf_renderer.load().k_point is not None:
        msg += " — Gamma-point lattice sum"

    frac = getattr(wf_renderer, "last_dropped_l_fraction", 0.0)
    if frac > 0.005:
        lmax = getattr(wf_renderer, "last_dropped_l_max", 0)
        msg += (
            f" — ⚠ {frac * 100:.0f}% of this orbital is in l={lmax} "
            "(g+) shells not yet rendered; isosurface is incomplete"
        )
    if update_state:
        state.status_message = msg
        state.volume_loaded = True
    return msg


def _computed_surface_opacity(state: _ViewerUIState, base: float) -> float:
    """``base`` scaled by the orbital-opacity slider (``mo_opacity``).

    Density, ELF, NCI and the Laplacian were drawn at fixed opacities no
    control reached; only the orbital lobes followed the slider (#37-36).
    """
    return base * float(getattr(state, "mo_opacity", 1.0) or 1.0)


def _remember_base_opacity(plotter: pv.Plotter, name: str, base: float) -> None:
    """Record a computed surface's own opacity at a slider setting of 1, by
    actor name: VTK hands out a new Python wrapper for the same actor, so
    the actor object itself cannot key it."""
    _scene_hooks(plotter).surface_base_opacity[name] = base


def _base_opacity(plotter: pv.Plotter, name: str) -> float:
    """A computed surface's base opacity; 1 for the orbital lobes."""
    return _scene_hooks(plotter).surface_base_opacity.get(name, 1.0)


def _draw_computed_surfaces(
    reader: QVFReader,
    plotter: pv.Plotter,
    viewer_state: ViewerState,
    state: _ViewerUIState,
    grid: Any,
    values: np.ndarray,
    levels: Sequence[tuple[float, str, str]],
    *,
    base_opacity: float,
) -> int:
    """Contour a field evaluated on ``grid`` at each ``(level, colour, tag)``.

    Each non-empty contour is replicated across the periodic images the
    viewer shows and drawn as ``mo_iso_<tag>`` at the shared computed-surface
    opacity. Returns how many surfaces were drawn. The density, ELF and
    Laplacian renderers each carried a copy of this block (#37-36).
    """
    pv_grid = pv.ImageData(
        dimensions=(grid.shape[0], grid.shape[1], grid.shape[2]),
        spacing=(
            float(grid.voxel_vectors[0, 0]) * grid.to_angstrom,
            float(grid.voxel_vectors[1, 1]) * grid.to_angstrom,
            float(grid.voxel_vectors[2, 2]) * grid.to_angstrom,
        ),
        origin=tuple(float(x) * grid.to_angstrom for x in grid.origin),
    )
    pv_grid.point_data["values"] = np.asarray(values).ravel(order="F")
    lattice = _get_lattice_vectors(reader)
    rep = viewer_state.replication
    opacity = _computed_surface_opacity(state, base_opacity)
    drawn = 0
    for level, colour, tag in levels:
        contour = pv_grid.contour(isosurfaces=[level], scalars="values")
        # Replicate across periodic images for crystalline systems.
        if lattice is not None and any(r > 1 for r in rep) and contour.n_points:
            from vibeview.renderers.volume import _replicate_mesh

            contour = _replicate_mesh(contour, lattice, rep)
        if contour.n_points:
            name = f"{_Actors.MO_SURFACE}{tag}"
            plotter.add_mesh(
                contour, color=colour, opacity=opacity, name=name, show_scalar_bar=False
            )
            _remember_base_opacity(plotter, name, base_opacity)
            drawn += 1
    return drawn


def _render_wf_density(
    reader: QVFReader,
    plotter: pv.Plotter,
    viewer_state: ViewerState,
    state: _ViewerUIState,
    section: Section,
    *,
    update_state: bool = True,
) -> str:
    """Phase E3: compute ρ(r) = Σ occ_i |ψ_i|² from the wavefunction.gto MO
    coefficients and render it as an (unsigned) isosurface.

    Reports the integrated electron count ∫ρ dV in the status as a
    self-consistency check (it equals the electron count for a converged,
    correctly-normalized wavefunction; coarse grids undercount the sharp core).
    The isosurface uses the ``mo_iso_`` prefix so the MO visibility toggle +
    section switches clear it like an MO surface.
    """
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    wf_renderer = WavefunctionRenderer(section, reader)
    spin_mode = bool(getattr(state, "wf_density_spin", False))
    if spin_mode:
        grid, rho, integral = wf_renderer.evaluate_spin_density(
            n_per_dim=int(state.wf_n_per_dim or 60)
        )
    else:
        grid, rho, integral = wf_renderer.evaluate_density(
            n_per_dim=int(state.wf_n_per_dim or 60)
        )

    _remove_actors_by_prefix(plotter, _Actors.MO_SURFACE)
    iso = max(float(state.isovalue or 0.05), 0.001)

    # The total density is positive everywhere, so one unsigned surface says
    # it all. The spin density is signed -- excess alpha against excess beta,
    # and the negative lobes from spin polarisation are the interesting part
    # -- so it gets a pair, coloured like an MO's phases (+ blue, − red:
    # the shared lobe convention of #38-19).
    from vibeview.renderers.palette import ISO_NEGATIVE, ISO_POSITIVE

    levels = (
        ((iso, ISO_POSITIVE, "pos"), (-iso, ISO_NEGATIVE, "neg"))
        if spin_mode
        else ((iso, _palette.ACCENT_BLUE, "density"),)
    )

    drawn = _draw_computed_surfaces(
        reader, plotter, viewer_state, state, grid, rho, levels, base_opacity=0.55
    )

    if spin_mode:
        message = (
            "Computed spin density (blue = excess α, red = excess β): "
            f"∫(ρα−ρβ) dV = {integral:.2f} unpaired electrons (iso ±{iso:g})"
        )
        if not drawn:
            message += " — nothing above the isovalue"
    else:
        message = (
            f"Computed density from {wf_renderer.n_mo} MOs: "
            f"∫ρ dV = {integral:.2f} electrons (iso {iso:g})"
        )
    if update_state:
        state.mo_visible = True
        state.volume_loaded = True
        state.status_message = message
    return message


def _render_wf_elf(
    reader: QVFReader,
    plotter: pv.Plotter,
    viewer_state: ViewerState,
    state: _ViewerUIState,
    section: Section,
    *,
    update_state: bool = True,
) -> str:
    """Compute ELF from the wavefunction.gto MO coefficients and render it
    as an isosurface.

    Unlike the density, ELF is bounded in [0, 1] and its *high* values are
    the interesting ones -- shells, bonds and lone pairs sit near 1, so the
    surface is drawn at ``wf_elf_iso`` (0.8 by convention) rather than at the
    density's isovalue.
    """
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    wf_renderer = WavefunctionRenderer(section, reader)
    grid, elf = wf_renderer.evaluate_elf(n_per_dim=int(state.wf_n_per_dim or 60))

    _remove_actors_by_prefix(plotter, _Actors.MO_SURFACE)
    iso = min(max(float(state.wf_elf_iso or 0.8), 0.05), 0.95)
    drawn = _draw_computed_surfaces(
        reader,
        plotter,
        viewer_state,
        state,
        grid,
        elf,
        ((iso, _palette.ELF_SURFACE, "elf"),),
        base_opacity=0.6,
    )
    message = (
        f"Computed ELF (Becke-Edgecombe): isosurface at {iso:g}; "
        f"max {float(elf.max()):.3f}"
    )
    if not drawn:
        message += " — nothing at this level, try lowering it"
    if update_state:
        state.mo_visible = True
        state.volume_loaded = True
        state.status_message = message
    return message


def _render_wf_nci(
    reader: QVFReader,
    plotter: pv.Plotter,
    viewer_state: ViewerState,
    state: _ViewerUIState,
    section: Section,
    *,
    update_state: bool = True,
) -> str:
    """Render the NCI surface: the reduced-gradient isosurface, coloured by
    sign(lambda2)*rho.

    Johnson et al. 2010. The surface locates the noncovalent interactions;
    the colour says what kind. The isosurface level, the colour range and the
    blue-green-red map are the ones :mod:`vibeview.renderers.nci` uses for a
    stored ``volume.rdg`` section, so a computed and a stored NCI surface of
    the same molecule look the same.
    """
    from vibeview.renderers.nci import (
        COLOR_CLIM,
        RDG_ISOVALUE,
        build_nci_mesh,
        nci_colormap,
    )
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    wf_renderer = WavefunctionRenderer(section, reader)
    grid, rdg, signed_rho = wf_renderer.evaluate_nci(
        n_per_dim=int(state.wf_n_per_dim or 60)
    )

    _remove_actors_by_prefix(plotter, _Actors.MO_SURFACE)
    iso = min(max(float(state.wf_nci_iso or RDG_ISOVALUE), 0.05), 2.0)
    lattice = _get_lattice_vectors(reader)
    pbc = None
    if lattice is not None:
        with contextlib.suppress(QVFError):
            pbc = reader.read_structure().pbc
    contour = build_nci_mesh(
        rdg,
        signed_rho,
        grid,
        iso,
        replication=viewer_state.replication,
        lattice_vectors=lattice,
        pbc=pbc,
    )

    if contour is not None:
        plotter.add_mesh(
            contour,
            scalars="color",
            cmap=nci_colormap(),
            clim=(-COLOR_CLIM, COLOR_CLIM),
            opacity=_computed_surface_opacity(state, 0.75),
            name="mo_iso_nci",
            show_scalar_bar=False,
        )
        _remember_base_opacity(plotter, "mo_iso_nci", 0.75)
    message = (
        f"Computed NCI (Johnson 2010): s = {iso:g} isosurface, "
        "blue attractive / green vdW / red steric"
    )
    if contour is None:
        message += " — no surface at this level"
    if update_state:
        state.mo_visible = True
        state.volume_loaded = True
        state.status_message = message
    return message


def _render_wf_laplacian(
    reader: QVFReader,
    plotter: pv.Plotter,
    viewer_state: ViewerState,
    state: _ViewerUIState,
    section: Section,
    *,
    update_state: bool = True,
) -> str:
    """Render del^2 rho as a signed isosurface pair.

    Bader: the NEGATIVE lobes are charge concentration -- bonding regions,
    atomic shells, and the valence-shell charge concentrations that lone
    pairs appear as -- so they get the saturated colour. Positive is
    depletion. An unsigned surface would hide the half that carries the
    chemistry.
    """
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    wf_renderer = WavefunctionRenderer(section, reader)
    grid, laplacian = wf_renderer.evaluate_density_laplacian(
        n_per_dim=int(state.wf_n_per_dim or 60)
    )

    _remove_actors_by_prefix(plotter, _Actors.MO_SURFACE)
    iso = max(float(state.wf_laplacian_iso or 1.0), 1e-3)
    drawn = _draw_computed_surfaces(
        reader,
        plotter,
        viewer_state,
        state,
        grid,
        laplacian,
        (
            (-iso, _palette.LAPLACIAN_CONCENTRATION, "lapneg"),
            (iso, _palette.LAPLACIAN_DEPLETION, "lappos"),
        ),
        base_opacity=0.55,
    )
    message = (
        f"Computed del^2 rho: isosurfaces at +/-{iso:g} e/bohr^5 "
        "(purple = charge concentration, orange = depletion)"
    )
    if not drawn:
        message += " — nothing at this level"
    if update_state:
        state.mo_visible = True
        state.volume_loaded = True
        state.status_message = message
    return message


def _update_reaction_label(reader: QVFReader, state: _ViewerUIState) -> None:
    """Set state.reaction_current_label to the waypoint at the current frame, if any."""
    frame = int(state.trajectory_frame or 0)
    waypoints = state.reaction_waypoints or []
    for wp in waypoints:
        if int(wp.get("frame_index", -1)) == frame:
            state.reaction_current_label = f"{wp.get('label', '')} ({wp.get('kind', '')})"
            return
    state.reaction_current_label = ""


def _camera_to_dict(plotter: pv.Plotter) -> dict[str, Any]:
    """Serialise the plotter camera into the shape ``_apply_camera`` reads.

    PyVista's ``Camera`` has no ``to_dict``. Bookmark and session save both
    called ``plotter.camera.to_dict()`` behind a ``hasattr(plotter, "camera")``
    guard — which checks the wrong object, since the plotter always has a
    camera and it is ``to_dict`` that is missing. Every save therefore raised
    AttributeError, so no bookmark was ever stored (and the empty section_id
    they were meant to carry went unnoticed as a result).

    Emits the keys ``_apply_camera`` consumes, and the projection-specific one
    it branches on, so a saved view round-trips.
    """
    try:
        cam = plotter.camera
        out: dict[str, Any] = {
            "position": [float(v) for v in cam.position],
            "focal_point": [float(v) for v in cam.focal_point],
            "view_up": [float(v) for v in cam.up],
        }
        if bool(cam.parallel_projection):
            out["parallel_scale"] = float(cam.parallel_scale)
        else:
            out["view_angle"] = float(cam.view_angle)
        return out
    except Exception:  # noqa: BLE001 — a view without a camera must still save
        return {}


def _apply_camera(plotter: pv.Plotter, camera: dict[str, Any] | None) -> None:
    """Apply a VTK camera dict to the plotter, if well-formed.

    Callers in the app follow with :func:`_sync_orthographic_switch`.
    """
    if not camera:
        return
    pos = camera.get("position")
    fp = camera.get("focal_point")
    vu = camera.get("view_up")
    if pos and fp:
        plotter.camera_position = [
            tuple(pos),
            tuple(fp),
            tuple(vu) if vu else (0, 0, 1),
        ]
    cam = plotter.camera
    if "view_angle" in camera:
        cam.view_angle = float(camera["view_angle"])
        cam.parallel_projection = False
    elif "parallel_scale" in camera:
        cam.parallel_scale = float(camera["parallel_scale"])
        cam.parallel_projection = True
    # Push the exact camera to the client's VtkLocalView, which otherwise keeps
    # its own camera and would ignore this server-side change (audit L1). The
    # callable is stashed by create_app once the local view exists; no-op
    # headless / before the view is built. Mirrors _push_view.
    push = _scene_hooks(plotter).push_camera
    if push is not None:
        # The camera push is best-effort, as in _push_view above.
        with contextlib.suppress(Exception):
            push()


def _sync_orthographic_switch(plotter: pv.Plotter, state: _ViewerUIState) -> None:
    """Make the Orthographic switch match the camera's projection.

    Restoring a perspective view with the switch on left the switch lying
    about the camera (#37-15).
    """
    with contextlib.suppress(Exception):
        state.orthographic_projection = bool(plotter.camera.parallel_projection)


def _render_vibration_atoms(
    reader: QVFReader,
    plotter: pv.Plotter,
    state: _ViewerUIState,
    phase: float = 0.0,
) -> None:
    """Render atoms displaced along the selected normal mode."""
    from vibeview.renderers.structure import cpk_color, cpk_radius
    from vibeview.renderers.vibrations import VibrationsRenderer

    section_id = state.selected_section
    if not section_id:
        return
    section = next((s for s in reader.sections if s.id == section_id), None)
    if section is None or section.kind != "vibrations":
        return

    renderer = VibrationsRenderer(section, reader)
    try:
        renderer.load()
    except QVFError:
        return

    mode = state.vibration_mode
    amp = state.vibration_amplitude
    if mode < 0 or mode >= renderer.n_modes:
        return

    atoms = renderer.displace_atoms(mode, amp, phase=phase)
    if not atoms:
        return

    # Hide the static equilibrium structure so it doesn't ghost behind the
    # displaced atoms (A5-03).
    _remove_static_structure(plotter)
    state.structure_hidden = True
    _remove_actors_by_prefix(plotter, _Actors.VIB_ATOM)

    # Atomic numbers (for correct CPK colour/radius) from the loaded
    # vibrations atoms, index-aligned with displace_atoms' output.
    znums = [a.atomic_number for a in renderer.load().atoms]
    ball_scale = 0.35
    for i, (_symbol, pos) in enumerate(atoms):
        z = znums[i] if i < len(znums) else 0
        sphere = pv.Sphere(
            radius=cpk_radius(z, ball_scale),
            center=pos,
            theta_resolution=10,
            phi_resolution=10,
        )
        plotter.add_mesh(
            sphere,
            color=cpk_color(z),
            smooth_shading=True,
            name=f"vib_atom_{i}",
        )

    plotter.render()
    _push_view(plotter)


_CLIP_ACTOR_NAMES = (
    "_volume_clipped",
    "_volume_clipped_pos",
    "_volume_clipped_neg",
    "_volume_slice",
    "_volume_slice_outline",
)


def _white_to_transparent(img: Any) -> Any:
    """``img`` as RGBA with its near-white pixels (every channel above 250)
    turned transparent white. One array mask instead of a Python loop over
    ``getdata()``, which took seconds on a 4K screenshot (#37-52)."""
    import numpy as _np
    from PIL import Image as _Image

    rgba = _np.array(img.convert("RGBA"))
    white = (rgba[..., :3] > 250).all(axis=-1)
    rgba[white] = (255, 255, 255, 0)
    return _Image.fromarray(rgba)


def _clip_to_planes(
    mesh: pv.DataSet, fx: float, fy: float, fz: float, bounds: Sequence[float]
) -> pv.DataSet:
    """Clip ``mesh`` by three axis-aligned planes positioned at the
    fractional ``(fx, fy, fz)`` of ``bounds`` (xmin,xmax,ymin,ymax,zmin,zmax).
    Returns the clipped mesh (possibly empty)."""
    cx = bounds[0] + fx * (bounds[1] - bounds[0])
    cy = bounds[2] + fy * (bounds[3] - bounds[2])
    cz = bounds[4] + fz * (bounds[5] - bounds[4])
    out = mesh.clip(normal="x", origin=(cx, 0, 0), invert=False)
    out = out.clip(normal="y", origin=(0, cy, 0), invert=False)
    out = out.clip(normal="z", origin=(0, 0, cz), invert=False)
    return out


def _rebuild_clip(
    reader: QVFReader, plotter: pv.Plotter, viewer_state: ViewerState, state: _ViewerUIState
) -> None:
    """Apply axis-aligned clip planes to the active volume isosurface.

    For every signed kind (:data:`vibeview.kinds.SIGNED_VOLUME_KINDS`:
    difference, spin, orbital, potential, basis AO) both the positive
    (ISO_POSITIVE, blue) and negative (ISO_NEGATIVE, red) lobes are clipped
    and shown, as the unclipped render draws them. The single-mesh path only
    contours the +isovalue, which silently dropped the negative lobe
    (audit finding A2-05; for orbital/potential/basis.ao, #38-19).
    """
    # trame-vtk derives browser-side object IDs from VTK native addresses. If
    # an actor graph dies immediately after removal, VTK can reuse one of its
    # addresses for a different object type in the replacement graph. The
    # browser then applies an actor update to its cached property (or vice
    # versa) and aborts the scene delta. Keep every current graph root alive
    # until replacements have been allocated: PyVista may retire a generated
    # scalar-bar actor as a side effect of removing its named volume actor.
    retired_actors = list(plotter.actors.values())
    for nm in _CLIP_ACTOR_NAMES:
        with contextlib.suppress(KeyError, ValueError):
            remove_actor(plotter, nm)
    section = (
        next((s for s in reader.sections if s.id == state.active_volume_id), None)
        if state.active_volume_id
        else None
    )
    if not state.clip_enabled or section is None:
        state.clip_position_message = ""
        # Clip off (or nothing active): restore the full isosurface we hide
        # while clipping, so the volume reappears instead of leaving the clip
        # looking like a no-op / the viewport empty (live finding UI-OBS-E).
        if section is not None:
            _rebuild_volume(reader, plotter, viewer_state, state, section.id)
        return

    # ── 2D cross-section slice mode ─────────────────────────────────
    if getattr(state, "show_slice", False):
        _remove_actors_by_prefix(plotter, f"{_Actors.VOLUME}{state.active_volume_id}")
        for nm in _CLIP_ACTOR_NAMES:
            with contextlib.suppress(KeyError, ValueError):
                remove_actor(plotter, nm)
        try:
            from vibeview.renderers.volume import VolumeRenderer

            renderer = VolumeRenderer(section, reader)
            grid = renderer.load_grid()
            data = renderer.load_data()
            hints = viewer_state.get_volume_hints(state.active_volume_id, kind=section.kind)
            fx, fy, fz = state.clip_x, state.clip_y, state.clip_z
            # Build a PyVista ImageData for the full volume, then use
            # its built-in .slice() to extract an orthogonal 2D plane.
            vox = grid.voxel_vectors * grid.to_angstrom
            origin_ang = grid.origin * grid.to_angstrom
            shape = grid.shape
            vol = pv.ImageData(
                dimensions=shape,
                spacing=(float(vox[0, 0]), float(vox[1, 1]), float(vox[2, 2])),
                origin=tuple(float(x) for x in origin_ang),
            )
            vol.point_data["values"] = np.asarray(data, dtype=np.float32).ravel(order="F")
            # Pick the dominant axis from the clip sliders.
            dists = [abs(fx - 0.5), abs(fy - 0.5), abs(fz - 0.5)]
            axis_names = ["x", "y", "z"]
            axis_label = axis_names[int(np.argmax(dists))]
            frac = {"x": fx, "y": fy, "z": fz}[axis_label]
            # Compute the slice origin along the chosen axis.
            b = vol.bounds
            idx = {"x": 0, "y": 2, "z": 4}[axis_label]
            pos = b[idx] + frac * (b[idx + 1] - b[idx])
            origin = list(vol.center)
            origin[{"x": 0, "y": 1, "z": 2}[axis_label]] = pos
            slc = vol.slice(normal=cast("Literal['x', 'y', 'z']", axis_label), origin=origin)
            if slc.n_points > 0:
                plotter.add_mesh(
                    slc,
                    name="_volume_slice",
                    scalars="values",
                    cmap=cmap_argument(state.colormap or hints.colormap),
                    opacity=0.85,
                    show_scalar_bar=False,
                )
                plotter.add_mesh(
                    slc.outline(),
                    name="_volume_slice_outline",
                    color=_palette.SLICE_OUTLINE,
                    opacity=0.3,
                    line_width=1,
                )
            state.clip_position_message = f"2D slice at {axis_label}={frac:.2f}"
        except Exception as e:  # noqa: BLE001
            state.clip_position_message = ""
            state.status_message = f"Slice error: {e}"
        return

    state.clip_position_message = ""
    try:
        from vibeview.renderers.volume import VolumeRenderer

        # Hide the full isosurface so only the clipped slice shows; otherwise
        # the un-clipped volume sits on top and clipping appears to do nothing.
        _remove_actors_by_prefix(plotter, f"{_Actors.VOLUME}{state.active_volume_id}")
        renderer = VolumeRenderer(section, reader)
        hints = viewer_state.get_volume_hints(state.active_volume_id, kind=section.kind)
        if state.isovalue:
            hints.isovalue = state.isovalue
        fx, fy, fz = state.clip_x, state.clip_y, state.clip_z

        from vibeview.kinds import SIGNED_VOLUME_KINDS

        if section.kind in SIGNED_VOLUME_KINDS:
            # The stored array as-is, exactly as in _activate_volume: the
            # ``component`` is a label only (stored_volume_field).
            raw = renderer.load_data()
            iso = hints.isovalue
            pos = _build_signed_contour(renderer, np.clip(raw, 0, None), iso, (1, 1, 1), None)
            neg = _build_signed_contour(renderer, np.clip(-raw, 0, None), iso, (1, 1, 1), None)
            meshes = [m for m in (pos, neg) if m is not None and m.n_points]
            if not meshes:
                return
            # Shared clip-plane frame across both lobes (union of bounds).
            all_bounds = [m.bounds for m in meshes]
            bounds = (
                min(x[0] for x in all_bounds),
                max(x[1] for x in all_bounds),
                min(x[2] for x in all_bounds),
                max(x[3] for x in all_bounds),
                min(x[4] for x in all_bounds),
                max(x[5] for x in all_bounds),
            )
            from vibeview.renderers.palette import ISO_NEGATIVE, ISO_POSITIVE

            for mesh, color, name in (
                (pos, ISO_POSITIVE, "_volume_clipped_pos"),
                (neg, ISO_NEGATIVE, "_volume_clipped_neg"),
            ):
                if mesh is None or not mesh.n_points:
                    continue
                clipped = _clip_to_planes(mesh, fx, fy, fz, bounds)
                if clipped.n_points > 0:
                    plotter.add_mesh(
                        clipped,
                        name=name,
                        color=color,
                        opacity=state.opacity or hints.opacity,
                        show_scalar_bar=False,
                    )
        else:
            mesh = renderer.make_mesh(hints)
            if mesh is None or mesh.n_points == 0:
                return
            clipped = _clip_to_planes(mesh, fx, fy, fz, mesh.bounds)
            if clipped.n_points > 0:
                plotter.add_mesh(
                    clipped,
                    name="_volume_clipped",
                    scalars=clipped.active_scalars_name,
                    cmap=cmap_argument(state.colormap or hints.colormap),
                    opacity=state.opacity or hints.opacity,
                    show_scalar_bar=False,
                )
    except Exception as e:  # noqa: BLE001
        state.status_message = f"Clip error: {e}"
    retired_actors.clear()



# ── Section activation registry (#37-R1) ────────────────────────────────
#
# At the end of the module because the table holds the activators, which are
# defined above it.
#
# One table says what opening a section of each kind does. The sidebar click,
# the viewer_defaults auto-open (which calls the same activate_section) and
# the bands/DOS energy-window redraw all resolve a kind here, instead of each
# carrying its own if/elif chain, and the "restore the CPK palette when
# leaving atom_properties" step is one flag rather than a line per branch
# (#37-26, #37-27).


@dataclass
class _SectionContext:
    """What an activator may touch: the active reader and its scene."""

    reader: QVFReader
    plotter: pv.Plotter
    state: Any
    viewer_state: ViewerState
    all_readers: Sequence[QVFReader]


@dataclass(frozen=True)
class _SectionHandler:
    """How to open one section kind.

    ``restores_cpk``: leaving ``atom_properties`` for this kind redraws the
    atoms in their element colours first. Only kinds that replace or restyle
    the atoms themselves opt out.
    ``keeps_structure_hidden``: an animation or a reciprocal-space scene that
    draws its own geometry, so a hidden static structure stays hidden.
    """

    activate: Callable[[_SectionContext, Section], None]
    restores_cpk: bool = True
    keeps_structure_hidden: bool = False


@dataclass(frozen=True)
class _PanelSpec:
    """A section that only fills one side panel from ``render_to_html()``.

    ``channel`` names the state pair it fills (``<channel>_html`` and
    ``<channel>_title``); ``title`` and ``noun`` may read the renderer, and
    ``status`` overrides the "Loaded <noun>: <id>" line. These were a dozen
    copies of one function (#37-35).
    """

    renderer: str  # "module:Class"
    channel: str
    title: str | Callable[[Any], str]
    noun: str | Callable[[Any], str]
    status: Callable[[Any, Section], str] | None = None


def _bond_orders_title(renderer: Any) -> str:
    return f"Bond Orders ({renderer.load().method.capitalize()})"


def _bond_orders_status(renderer: Any, section: Section) -> str:
    data = renderer.load()
    return f"Loaded bond orders ({data.method}): {section.id} — {len(data.pairs)} pairs"


def _job_spec_title(renderer: Any) -> str:
    data = renderer.load()
    title = f"Job Spec ({data.job_type}"
    if data.method:
        title += f", {data.method}"
    return title + ")"


_PANEL_SPECS: dict[str, _PanelSpec] = {
    "phonon_bands": _PanelSpec(
        "vibeview.renderers.phonon:PhononBandsRenderer",
        "phonon", "Phonon Band Structure", "phonon bands",
    ),
    "phonon_dos": _PanelSpec(
        "vibeview.renderers.phonon:PhononDOSRenderer", "phonon", "Phonon DOS", "phonon DOS"
    ),
    "equation_of_state": _PanelSpec(
        "vibeview.renderers.eos:EquationOfStateRenderer",
        "eos", "Equation of State", "equation of state",
    ),
    "citations": _PanelSpec(
        "vibeview.renderers.citations:CitationsRenderer", "properties", "Citations", "citations"
    ),
    "structure.symmetry": _PanelSpec(
        "vibeview.renderers.symmetry:SymmetryRenderer", "properties", "Symmetry", "symmetry"
    ),
    "spectra.nmr": _PanelSpec(
        "vibeview.renderers.nmr:NMRRenderer", "properties", "NMR", "NMR data"
    ),
    "spectra.epr": _PanelSpec(
        "vibeview.renderers.epr:EPRRenderer", "properties", "EPR", "EPR data"
    ),
    "job.spec": _PanelSpec(
        "vibeview.renderers.job_spec:JobSpecRenderer", "properties", _job_spec_title, "job spec"
    ),
    "bond_orders": _PanelSpec(
        "vibeview.renderers.bond_orders:BondOrdersRenderer",
        "properties", _bond_orders_title, "bond orders", _bond_orders_status,
    ),
    "dos.coop": _PanelSpec(
        "vibeview.renderers.coop:COOPRenderer",
        "chart", lambda r: str(r.kind_label), lambda r: str(r.kind_label),
    ),
    "dos.cohp": _PanelSpec(
        "vibeview.renderers.coop:COOPRenderer",
        "chart", lambda r: str(r.kind_label), lambda r: str(r.kind_label),
    ),
}


def _activate_panel(reader: QVFReader, state: _ViewerUIState, section: Section) -> None:
    """Fill the side panel for a :data:`_PANEL_SPECS` kind."""
    import importlib

    spec = _PANEL_SPECS[section.kind]
    module, _, name = spec.renderer.partition(":")
    renderer = getattr(importlib.import_module(module), name)(section, reader)
    noun = spec.noun(renderer) if callable(spec.noun) else spec.noun
    try:
        html = renderer.render_to_html()
        title = spec.title(renderer) if callable(spec.title) else spec.title
        setattr(state, f"{spec.channel}_html", html)
        setattr(state, f"{spec.channel}_title", title)
        state.status_message = (
            spec.status(renderer, section) if spec.status else f"Loaded {noun}: {section.id}"
        )
    except QVFError as e:
        state.status_message = f"Error loading {noun}: {e}"


def _open_structure(ctx: _SectionContext, section: Section) -> None:
    # Clear everything previous sections drew over the molecule (isosurfaces,
    # vibration ghosts, trajectory frames) so the bare structure shows again.
    for prefix in (_Actors.VOLUME, _Actors.VIB_ATOM, _Actors.TRAJ_ATOM):
        _remove_actors_by_prefix(ctx.plotter, prefix)
    ctx.plotter.render()
    _push_view(ctx.plotter)
    ctx.state.status_message = "Structure"


def _open_dos_total(ctx: _SectionContext, section: Section) -> None:
    # Render the combined bands+DOS figure when a companion bands section
    # exists, but keep ``selected_section`` on the DOS id the user clicked.
    bands = next((s for s in ctx.reader.sections if s.kind == "bands"), None)
    if bands is not None:
        _activate_bands(ctx.reader, ctx.state, bands, ctx.viewer_state)
    else:
        _activate_dos(ctx.reader, ctx.state, section, ctx.viewer_state)


def _open_atom_properties(ctx: _SectionContext, section: Section) -> None:
    # Structure index labels and charge labels sit at the same positions.
    _remove_atom_index_labels(ctx.plotter)
    _activate_atom_properties(ctx.reader, ctx.plotter, ctx.state, section)


def _panel_handler() -> _SectionHandler:
    return _SectionHandler(lambda ctx, section: _activate_panel(ctx.reader, ctx.state, section))


def _reader_state_handler(
    activate: Callable[[QVFReader, Any, Section], None], **traits: bool
) -> _SectionHandler:
    return _SectionHandler(lambda ctx, section: activate(ctx.reader, ctx.state, section), **traits)


_SECTION_HANDLERS: dict[str, _SectionHandler] = {
    "structure": _SectionHandler(_open_structure),
    "volume.*": _SectionHandler(
        lambda ctx, section: _activate_volume(
            ctx.reader, ctx.plotter, ctx.viewer_state, ctx.state, section
        )
    ),
    "fermi_surface": _SectionHandler(
        lambda ctx, section: _activate_fermi(
            ctx.reader, ctx.plotter, ctx.viewer_state, ctx.state, section
        ),
        restores_cpk=False,
        keeps_structure_hidden=True,
    ),
    "bands": _SectionHandler(
        lambda ctx, section: _activate_bands(ctx.reader, ctx.state, section, ctx.viewer_state)
    ),
    "dos.total": _SectionHandler(_open_dos_total),
    "dos.projected": _SectionHandler(
        lambda ctx, section: _activate_dos(ctx.reader, ctx.state, section, ctx.viewer_state)
    ),
    "spectra.*": _SectionHandler(
        lambda ctx, section: _activate_spectra(
            ctx.reader, ctx.state, section, list(ctx.all_readers)
        )
    ),
    "trajectory": _reader_state_handler(_activate_trajectory, keeps_structure_hidden=True),
    "reaction.path": _reader_state_handler(_activate_reaction_path, keeps_structure_hidden=True),
    "vibrations": _reader_state_handler(_activate_vibrations, keeps_structure_hidden=True),
    "reaction.waypoints": _reader_state_handler(_activate_reaction_waypoints),
    "scan.surface": _reader_state_handler(_activate_scan_surface),
    "wavefunction.gto": _reader_state_handler(_activate_wavefunction),
    "run.record": _reader_state_handler(_activate_run_record),
    "scf_history": _reader_state_handler(_activate_scf_history),
    "atom_properties": _SectionHandler(_open_atom_properties, restores_cpk=False),
    "topology.qtaim": _SectionHandler(
        lambda ctx, section: _activate_topology_qtaim(
            ctx.reader, ctx.plotter, ctx.state, section
        )
    ),
    **{kind: _panel_handler() for kind in _PANEL_SPECS},
}


def _section_handler(kind: str) -> _SectionHandler | None:
    """The handler that opens ``kind``, or ``None`` for a kind with no view."""
    if kind in _SECTION_HANDLERS:
        return _SECTION_HANDLERS[kind]
    if kind.startswith("volume.") or kind == "basis.ao":
        return _SECTION_HANDLERS["volume.*"]
    if kind in _plot_spectra_kinds():
        return _SECTION_HANDLERS["spectra.*"]
    return None
