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
import weakref
from collections.abc import Sequence as Sequence
from dataclasses import dataclass as dataclass
from datetime import UTC as UTC
from pathlib import Path as Path
from typing import TYPE_CHECKING as TYPE_CHECKING
from typing import Any as Any
from typing import Literal as Literal
from typing import cast as cast

import numpy as np
import pyvista as pv
from pyvista.plotting.colors import get_cmap_safe as get_cmap_safe

from vibeview._fmt import human_size as human_size

# The package's other modules. Their names are re-exported here, so
# ``vibeview.app.<name>`` keeps working for every caller (#37-R6).
from vibeview.app.controls import (  # noqa: F401
    _DIALOG_FOCUS_JS as _DIALOG_FOCUS_JS,
)
from vibeview.app.controls import (
    _build_controls as _build_controls,
)
from vibeview.app.controls import (
    _dialog_focus_restore as _dialog_focus_restore,
)
from vibeview.app.controls import (
    _element_button_style as _element_button_style,
)
from vibeview.app.controls import (
    _periodic_table_cells as _periodic_table_cells,
)
from vibeview.app.layout import (  # noqa: F401
    _retaining_view_updater as _retaining_view_updater,
)
from vibeview.app.scene import (  # noqa: F401
    _ON_DOM_CHANGE_JS as _ON_DOM_CHANGE_JS,
)
from vibeview.app.scene import (
    _SCENE_HOOKS as _SCENE_HOOKS,
)
from vibeview.app.scene import (
    _Actors as _Actors,
)
from vibeview.app.scene import (
    _scene_hooks as _scene_hooks,
)
from vibeview.app.scene import (
    _SceneHooks as _SceneHooks,
)
from vibeview.app.sidebar import (  # noqa: F401
    _BASIS_AO_GROUP_ID as _BASIS_AO_GROUP_ID,
)
from vibeview.app.sidebar import (
    _DEFAULT_KIND_ICON as _DEFAULT_KIND_ICON,
)
from vibeview.app.sidebar import (
    _KIND_ICONS as _KIND_ICONS,
)
from vibeview.app.sidebar import (
    _KIND_TITLES as _KIND_TITLES,
)
from vibeview.app.sidebar import (
    _ORBITAL_GROUP_ID as _ORBITAL_GROUP_ID,
)
from vibeview.app.sidebar import (
    _build_section_list as _build_section_list,
)
from vibeview.app.sidebar import (
    _build_sidebar_entries as _build_sidebar_entries,
)
from vibeview.app.sidebar import (
    _builder_name as _builder_name,
)
from vibeview.app.sidebar import (
    _iupac_name_entry as _iupac_name_entry,
)
from vibeview.app.sidebar import (
    _kind_icon as _kind_icon,
)
from vibeview.app.sidebar import (
    _orbital_group_sidebar_title as _orbital_group_sidebar_title,
)
from vibeview.app.sidebar import (
    _orbital_label as _orbital_label,
)
from vibeview.app.sidebar import (
    _sidebar_group_entry as _sidebar_group_entry,
)
from vibeview.app.sidebar import (
    _sidebar_section_title as _sidebar_section_title,
)
from vibeview.app.sidebar import (
    _status_icon as _status_icon,
)
from vibeview.app.sidebar import (
    _supported_kinds_set as _supported_kinds_set,
)
from vibeview.app.state import (  # noqa: F401
    _first as _first,
)
from vibeview.app.state import (
    _ViewerUIState as _ViewerUIState,
)
from vibeview.headless import new_plotter as new_plotter
from vibeview.plotter_compat import (
    cmap_argument as cmap_argument,
)
from vibeview.plotter_compat import (
    hide_grid as hide_grid,
)
from vibeview.plotter_compat import (
    remove_actor as remove_actor,
)
from vibeview.plotter_compat import (
    reset_camera as reset_camera,
)
from vibeview.plotter_compat import (
    set_background as set_background,
)
from vibeview.plotter_compat import (
    show_grid as show_grid,
)
from vibeview.plotter_compat import (
    view_isometric as view_isometric,
)
from vibeview.plotter_compat import (
    view_xy as view_xy,
)
from vibeview.plotter_compat import (
    view_xz as view_xz,
)
from vibeview.plotter_compat import (
    view_yz as view_yz,
)
from vibeview.qvf import RELOCALIZED_PREFIX as RELOCALIZED_PREFIX
from vibeview.qvf import QVFError as QVFError
from vibeview.qvf import clamp_replication as clamp_replication
from vibeview.renderers import palette as _palette
from vibeview.renderers.cell import supercell_offsets as supercell_offsets
from vibeview.renderers.nci import RDG_ISOVALUE as RDG_ISOVALUE
from vibeview.units import ANGSTROM_TO_BOHR as _ANGSTROM_TO_BOHR
from vibeview.units import energy_from_ev as energy_from_ev
from vibeview.units import energy_label as energy_label
from vibeview.units import energy_to_ev as energy_to_ev
from vibeview.units import format_length as format_length
from vibeview.vibration_amplitude import (
    DEFAULT_PEAK_EXCURSION_ANGSTROM as DEFAULT_PEAK_EXCURSION_ANGSTROM,
)
from vibeview.viewer_defaults import ViewerState as ViewerState

if TYPE_CHECKING:
    from collections.abc import Callable

    from vibeview.material_presets import MaterialPreset
    from vibeview.qvf import GridData, QVFReader, Section, StructureData
    from vibeview.renderers.volume import VolumeRenderer
    from vibeview.viewer_defaults import VolumeHints





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

    from vibeview.converters import detect_format, is_supported_path
    from vibeview.orca_dataset import is_orca_auxiliary_path, orca_dataset_entry, orca_dataset_key
    from vibeview.trexio_import import is_trexio_directory

    if max_entries < 1 or max_candidates < 1:
        raise ValueError("GUI scan limits must be positive")

    pending = [Path(root)]
    candidates: list[Path] = []
    seen: set[Path] = set()
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
                    key = orca_dataset_key(entry_path)
                    if is_orca_auxiliary_path(entry_path) or (
                        key is None
                        and entry_path.suffix.lower() in (".out", ".inp")
                        and detect_format(entry_path) == "orca"
                    ):
                        continue
                    identity = key or entry_path.resolve()
                    if identity in seen:
                        continue
                    seen.add(identity)
                    candidates.append(orca_dataset_entry(entry_path) if key else entry_path)
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

    from vibeview.converters import detect_format, is_supported_path
    from vibeview.orca_dataset import is_orca_auxiliary_path, orca_dataset_entry, orca_dataset_key
    from vibeview.trexio_import import is_trexio_directory

    if max_entries < 1 or max_candidates < 1:
        raise ValueError("GUI scan limits must be positive")
    if recursive and "**" in pattern:
        raise ValueError(
            "Recursive ** globs are not supported. Enter the directory path "
            "and enable 'Search subdirectories recursively' instead."
        )

    candidates: list[Path] = []
    seen: set[Path] = set()
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
        key = orca_dataset_key(path)
        if is_orca_auxiliary_path(path) or (
            key is None
            and path.suffix.lower() in (".out", ".inp")
            and detect_format(path) == "orca"
        ):
            continue
        identity = key or path.resolve()
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append(orca_dataset_entry(path) if key else path)
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
    "volume.transition_density": "e/bohr³",
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

    # Missing structure: found by kind, never by the id "structure" (spec § 4.1).
    if reader.shown_structure is None:
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
    update the runtime's ``structure_renderer`` and ``is_periodic``
    from the return value.

    Side-effects on ``state``: sets ``is_periodic``, ``orbital_panel_title``,
    ``esp_available``, and ``status_message``.
    """
    from vibeview.renderers.structure import StructureRenderer

    structure_section = reader.shown_structure
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




def _declared_charge_multiplicity(reader: QVFReader) -> tuple[int | None, int | None]:
    """The charge and multiplicity the file states for the shown structure.

    The structure's chemical identity first (spec § 5.1), then ``provenance``
    (§ 3.2), where vibe-qc, vibe-sketch and vibe-view's own converters record
    them; see :meth:`QVFReader.declared_charge_multiplicity`. A missing,
    non-integer or (for multiplicity) non-positive value is None.
    """
    return reader.declared_charge_multiplicity()


def _shown_structure_marker(reader: QVFReader) -> str:
    """The shown structure's id when the archive holds several, else ``""``.

    The sidebar marks that row, so the user can tell which of a scheme's
    species the 3D view shows.
    """
    if len(reader.structure_sections) < 2:
        return ""
    return reader.shown_structure_id or ""


def _structure_display_name(reader: QVFReader, section_id: str) -> str:
    """A structure as status lines name it: its label and name (spec § 5.1), else its id."""
    return reader.structure_identity(section_id).title or section_id


def _seed_calculation_charge(state: _ViewerUIState, reader: QVFReader) -> None:
    """Start the Calculation Parameters panel at the shown structure's charge
    and multiplicity.

    Without this a sketched anion, or any charged vibe-qc result, was submitted
    as a neutral singlet unless the user noticed the panel (#89). A file that
    declares neither leaves the panel as it was. Showing another structure of
    the archive seeds it again from that structure (#90).
    """
    charge, multiplicity = _declared_charge_multiplicity(reader)
    if charge is not None:
        state.calc_charge = charge
    if multiplicity is not None:
        state.calc_multiplicity = multiplicity


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










# ── Helpers ──────────────────────────────────────────────────────────────


_SUBSCRIPT = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def _present_elements(reader: QVFReader) -> list[dict[str, Any]]:
    """Elements present in the structure, as picker items.

    ``[{"title": "C", "value": 6}, ...]`` in ascending atomic number — the
    source list for the per-element colour override picker (design refresh
    2026). Empty when the file has no structure section.
    """
    structure_section = reader.shown_structure
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
    structure_section = reader.shown_structure
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
    {"id": "view_clear", "title": "View preset: restore the previous look", "sub": "Rendering",
     "icon": "mdi-restore"},
    {"id": "outlines", "title": "Toggle figure outlines on atoms and bonds", "sub": "Rendering",
     "icon": "mdi-vector-circle"},
    {"id": "chrome", "title": "Toggle viewport chrome (figure look)", "sub": "Display",
     "icon": "mdi-eye-off-outline"},
    {"id": "vol_mode", "title": "Toggle volume rendering of the active volume",
     "sub": "Rendering", "icon": "mdi-cube-scan"},
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
    # Listed with or without the [sketch] extra: without it the action
    # reports the install command (#77).
    {"id": "sketch", "title": "Sketch a molecule in vibe-sketch", "sub": "Build",
     "icon": "mdi-draw"},
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
    archive is on disk, the original calculation name for a converted path,
    or the uploaded file's stem; falls back to ``<in-memory>`` otherwise.

    Structures made by the toolbar molecule builder have no path, so they
    carry their name in ``manifest.source.calculation`` as ``builder:<name>``.
    """
    if reader.path is not None:
        return reader.path.stem
    source_path = getattr(reader, "source_path", None)
    if source_path is not None:
        from vibeview.orca_dataset import orca_dataset_key

        key = orca_dataset_key(source_path)
        return key.name if key is not None else str(source_path.stem)
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
    Warnings about the manifest itself, such as a withdrawn ``qvf_version``
    (spec § 3.1), lead the card, also when there is no provenance.
    """
    lines: list[str] = [
        f"WARNING      : {warning}" for warning in reader.manifest_warnings()
    ]
    prov = reader.provenance
    if not prov:
        return "\n".join(lines)
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
            "reaction.scheme",
            "preview",
            "excited_states",
        }
    ) | _renderer_plugin_kinds()


def _renderer_plugin_kinds() -> frozenset[str]:
    """Kinds installed renderer plugins claim: each is a details panel (#113)."""
    from vibeview.renderer_plugins import plugin_kinds

    return plugin_kinds()


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
    state.trajectory_thumbnails = []
    # Vibration state — `vibration_playing` MUST be reset here (mirroring
    # trajectory_playing) so the @state.change cancel handler tears down the
    # async animation loop on a section/file switch; otherwise the orphaned
    # loop keeps animating the next section's atoms (audit finding A1-01).
    state.vibration_n_modes = 0
    state.vibration_playing = False
    state.vibration_phase = 0.0
    state.vibration_phase_step = 0
    state.vibration_thumbnails = []
    state.vibration_frequencies = []
    state.vibration_mode_items = []
    state.vibration_displacement_modes = []
    state.vibration_displacement_amplitudes = {}
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
        data = renderer.load_data()
    except QVFError as e:
        state.status_message = f"Error loading volume data: {e}"
        return

    hints = viewer_state.get_volume_hints(section.id, kind=section.kind)
    state.isovalue = hints.isovalue
    state.colormap = hints.colormap
    state.opacity = hints.opacity
    # Direct volume rendering is a per-section choice like the rest; the
    # card's picker mirrors the active section's hint.
    state.vol_mode = str(getattr(hints, "render", "isosurface"))
    if state.vol_mode != "volume":
        state.vol_range = None
        state.vol_sent_shape = None
    state.active_volume_id = section.id
    state.volume_loaded = True
    state.isovalue_units = _ISOVALUE_UNITS.get(section.kind, "arb. units")
    loaded = f"Loaded volume: {section.id}"
    state.status_message = loaded

    _rebuild_volume(reader, plotter, viewer_state, state, section.id)
    # The rebuild clears every other volume's actors, so any `volume_` actor
    # now belongs to this section. None means the contour came out empty: say
    # why instead of reporting a loaded volume nobody can see (#119). NCI and
    # direct volume rendering have their own paths and messages.
    if (
        state.status_message == loaded
        and state.vol_mode != "volume"
        and section.kind != "volume.rdg"
        and not any(str(name).startswith(_Actors.VOLUME) for name in plotter.actors)
    ):
        state.status_message = _empty_surface_message(section, float(hints.isovalue), data)


def _empty_surface_message(section: Section, isovalue: float, data: Any) -> str:
    """``Loaded volume: <id>, but no surface at ...`` with the field's extreme."""
    from vibeview.kinds import SIGNED_VOLUME_KINDS

    values = np.asarray(data, dtype=float)
    finite = values[np.isfinite(values)]
    signed = section.kind in SIGNED_VOLUME_KINDS
    level = f"±{abs(isovalue):g}" if signed else f"{isovalue:g}"
    if not finite.size:
        return f"Loaded volume: {section.id}, but it holds no finite values to contour"
    peak = float(np.max(np.abs(finite))) if signed else float(np.max(finite))
    reach = f"|value| {peak:.3g}" if signed else f"value {peak:.3g}"
    return (
        f"Loaded volume: {section.id}, but no surface at isovalue {level}: "
        f"its largest {reach}; lower the isovalue"
    )


def _render_volume_mode(
    reader: QVFReader,
    plotter: pv.Plotter,
    state: _ViewerUIState,
    section: Section,
    hints: VolumeHints,
) -> bool:
    """Draw ``section`` as a volume; False when it fell back to the isosurface.

    The payload (``renderers.volume_render``) quantises the field to uint8
    over a per-kind range and caps the grid; the actor takes the section's
    colormap and the kind's opacity ramp, with the Opacity control as the
    ramp's peak. The volume replaces every ``volume_`` actor of the section
    and is one of them by name, so section and file switches drop it the
    way they drop the contour. One render, one push.
    """
    from vibeview.renderers.volume import VolumeRenderer
    from vibeview.renderers.volume_render import (
        DEFAULT_MAX_DIM,
        RAMP_POSITIONS,
        VolumeRenderError,
        add_volume_actor,
        resolve_transfer_function,
        volume_actor_name,
        volume_image,
        window_bounds,
    )
    from vibeview.viewer_defaults import validate_transfer_function

    renderer = VolumeRenderer(section, reader)
    existing = validate_transfer_function(hints.transfer_function)
    try:
        grid = renderer.load_grid()
        data = renderer.load_data()
        max_dim = int(state.vol_max_dim or DEFAULT_MAX_DIM)
        # The saved window is the quantisation range, so the 256 levels
        # cover what the transfer function spans; no window, the kind's rule.
        window = (float(existing["range"][0]), float(existing["range"][1])) if existing else None
        volume = volume_image(data, grid, section.kind, max_dim=max_dim, value_range=window)
        tf = resolve_transfer_function(
            kind=section.kind,
            window=volume.range,
            colormap=str(hints.colormap),
            opacity=float(hints.opacity),
            existing=existing,
        )
    except (QVFError, VolumeRenderError, ValueError) as e:
        hints.render = "isosurface"
        state.vol_mode = "isosurface"
        state.vol_range = None
        state.vol_sent_shape = None
        state.status_message = (
            f"Volume rendering unavailable for {section.id}: {e}; showing the isosurface"
        )
        return False
    hints.transfer_function = tf
    bounds = window_bounds(data, section.kind)
    # A producer's window may reach past the field; the sliders must hold it.
    bounds = (min(bounds[0], volume.range[0]), max(bounds[1], volume.range[1]))
    _remove_actors_by_prefix(plotter, _Actors.VOLUME)
    add_volume_actor(
        plotter,
        volume,
        tf,
        name=volume_actor_name(section.id),
        shade=bool(state.vol_shade),
    )
    state.vol_tf = tf
    state.vol_range = [volume.range[0], volume.range[1]]
    state.vol_sent_shape = list(volume.sent_shape)
    # The editor mirrors the function it is drawing with.
    state.vol_preset = str(tf.get("preset") or "ramp")
    state.vol_window = [volume.range[0], volume.range[1]]
    # The two sliders bind to their own keys; keep them on the window.
    state.vol_window_low = volume.range[0]
    state.vol_window_high = volume.range[1]
    state.vol_window_bounds = [bounds[0], bounds[1]]
    state.vol_window_step = (bounds[1] - bounds[0]) / 256.0
    ramp = tf.get("ramp")
    values = list(ramp) if isinstance(ramp, list) else [1.0] * len(RAMP_POSITIONS)
    for index, value in enumerate(values):
        setattr(state, f"vol_ramp_{index}", float(value))
    sent = "×".join(str(n) for n in volume.sent_shape)
    full = "×".join(str(n) for n in volume.full_shape)
    if volume.resampled:
        reduced = f" (a skewed {full} grid resampled onto an axis-aligned box)"
    elif volume.reduced:
        reduced = f" (reduced from {full})"
    else:
        reduced = ""
    state.status_message = (
        f"Volume: {section.id} as {sent} voxels{reduced}, "
        f"{volume.payload_bytes / 1e6:.1f} MB"
    )
    plotter.render()
    _push_view(plotter)
    return True


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

    # ── direct volume rendering ─────────────────────────────────────
    # "Render as: Volume" draws the field itself through a transfer
    # function instead of contouring it (docs/volume_rendering_design.md).
    # A grid the payload cannot build puts the mode back to the isosurface
    # with the reason in the status line and falls through to the contour.
    if str(getattr(hints, "render", "isosurface")) == "volume" and _render_volume_mode(
        reader, plotter, state, section, hints
    ):
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
            scale = max(grid.shape) // 100
            # A stride of 1 changes nothing, so say nothing: the old message
            # announced a reduction for every grid between 101 and 199 voxels.
            if scale > 1:
                full_shape = tuple(int(n) for n in grid.shape)
                raw_data = raw_data[::scale, ::scale, ::scale]
                # Update the grid to match the subsampled data, and hand the
                # renderer both halves. ``make_mesh`` reads the renderer's
                # memoised array, which stayed at full resolution while the
                # grid it shares shrank, so every unsigned grid of 200 voxels
                # or more failed on the point-array length (#69).
                grid.origin = grid.origin.copy()
                grid.voxel_vectors = grid.voxel_vectors * scale
                grid.shape = raw_data.shape
                renderer._grid = grid
                renderer._data = raw_data
                state.status_message = (
                    "Volume detail reduced ("
                    + "×".join(str(n) for n in full_shape)
                    + " → "
                    + "×".join(str(n) for n in raw_data.shape)
                    + f", {raw_data.size:,} voxels)"
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
        # An isovalue beyond the field's values leaves an empty contour, which
        # add_mesh refuses with a ValueError; draw nothing instead (#119).
        if mesh is not None and mesh.n_points > 0:
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


def _compare_label(name: str, rmsd: float | None, length_unit: str = "angstrom") -> str:
    """A compare-legend entry: the file's name, with its RMSD in ``length_unit`` (#58)."""
    if rmsd is None:
        return name
    return f"{name} (RMSD {format_length(rmsd, length_unit, digits=2)})"


def _render_compare_overlay(
    plotter: pv.Plotter,
    readers: Sequence[QVFReader],
    align: bool = False,
    highlight: int | None = None,
    length_unit: str = "angstrom",
) -> list[dict[str, Any]]:
    """Overlay the structures from all loaded files, one uniform colour per file
    (Phase D1 compare mode).

    Atoms are glyph-instanced into one translucent actor per file (``cmp_<i>``).
    With ``align=True`` each file is Kabsch-superposed onto the first file (when
    the atom counts match) so the residual displacement is what shows (Phase D4),
    and its RMSD to that reference is reported in the legend, in ``length_unit``
    (#58). When ``highlight`` is an index, only that file appears at full
    opacity (0.7); others are shown at 0.15 for context.  Returns
    ``[{"name", "color", "rmsd", "label"}]``, the RMSD in Å; files with no
    readable structure are skipped.
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
        label = _compare_label(name, rmsd_val, length_unit)
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

    struct_sec = ra.shown_structure
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
    structure_section = reader.shown_structure
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

    structure_section = reader.shown_structure
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

    structure_section = reader.shown_structure
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
    if section.kind in _KIND_TITLES:
        return _KIND_TITLES[section.kind]
    plugin = _plugin_panel_spec(section.kind)
    return str(plugin.title) if plugin is not None else section.kind


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


def _show_scene_grid(plotter: pv.Plotter, state: _ViewerUIState) -> None:
    """The bounds grid around the scene, unless the viewport chrome is hidden.

    Startup, the file reload and every rebuild draw the grid through here, so
    the chrome switch holds across all of them (#68). The grid is a gizmo,
    not content: a figure wants the structure alone.
    """
    if bool(getattr(state, "chrome_hidden", False)):
        with contextlib.suppress(Exception):
            hide_grid(plotter)
    else:
        show_grid(plotter)


def _apply_scene_appearance(plotter: pv.Plotter, state: _ViewerUIState) -> MaterialPreset:
    """Make actor properties and background agree with persistent UI state."""
    from vibeview.material_presets import (
        add_figure_outlines,
        apply_material_to_actor,
        get_preset,
        remove_figure_outlines,
    )
    from vibeview.renderers.structure import (
        disable_toon_rendering,
        enable_toon_rendering,
    )
    from vibeview.renderers.volume_render import is_volume_actor

    # Outline hulls copy the atoms and bonds they outline, so they are built
    # again after the material pass below rather than restyled by it (#68).
    remove_figure_outlines(plotter)
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
        if is_volume_actor(actor):
            # A rendered volume draws through its transfer function, not a
            # surface material; its name is in the volume_ family on purpose.
            continue
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

    if bool(getattr(state, "figure_outlines", False)):
        add_figure_outlines(plotter)
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

    structure_section = reader.shown_structure
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

    _show_scene_grid(plotter, state)
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
    changed and needs a push (#37-49). A figure outline is named after the
    actor it outlines, so a group's outline hulls go with the group (#68).
    """
    removed = 0
    prefixes = (prefix, _Actors.OUTLINE + prefix)
    for key in list(plotter.actors.keys()):
        if isinstance(key, str) and key.startswith(prefixes):
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
    from vibeview.renderers.wavefunction import (
        WavefunctionRenderer,
        describe_transition_orbitals,
    )

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
        # Natural transition orbitals name their state (spec § 5.3).
        transition = describe_transition_orbitals(wf)
        state.status_message = (
            f"Loaded {kind_display.lower()} wavefunction: {section_id}"
            f" ({len(rows)} MOs, spin={wf.spin})"
            + (f"; transition orbitals of {transition}" if transition else "")
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

    renderer: str | type  # "module:Class", or a plugin's class (#113)
    channel: str
    title: str | Callable[[Any], str]
    noun: str | Callable[[Any], str]
    status: Callable[[Any, Section], str] | None = None
    #: Keyword arguments for ``render_to_html``, read from the UI state: the
    #: display units a panel follows (#58).
    options: Callable[[_ViewerUIState], dict[str, Any]] | None = None


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


def _length_unit_options(state: _ViewerUIState) -> dict[str, Any]:
    """The Bond Orders panel's distance column follows the Length unit (#58)."""
    return {"length_unit": str(state.measure_unit)}


def _band_energy_unit_options(state: _ViewerUIState) -> dict[str, Any]:
    """COOP and COHP charts follow the Band / DOS energy unit (#58)."""
    return {"energy_unit": str(state.band_energy_unit)}


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
        options=_length_unit_options,
    ),
    "dos.coop": _PanelSpec(
        "vibeview.renderers.coop:COOPRenderer",
        "chart", lambda r: str(r.kind_label), lambda r: str(r.kind_label),
        options=_band_energy_unit_options,
    ),
    "dos.cohp": _PanelSpec(
        "vibeview.renderers.coop:COOPRenderer",
        "chart", lambda r: str(r.kind_label), lambda r: str(r.kind_label),
        options=_band_energy_unit_options,
    ),
    # Descriptive kinds (#90): a scheme's steps and the archive's picture are
    # details, never drawn in the 3D scene.
    "reaction.scheme": _PanelSpec(
        "vibeview.renderers.reaction_scheme:ReactionSchemeRenderer",
        "properties", lambda r: str(r.title), "reaction scheme",
    ),
    "preview": _PanelSpec(
        "vibeview.renderers.preview:PreviewRenderer", "properties", "Preview", "preview"
    ),
    # The excited-state table (#109): data about the calculation, drawn as
    # a table; the NTO and density sections it names have their own rows.
    "excited_states": _PanelSpec(
        "vibeview.renderers.excited_states:ExcitedStatesRenderer",
        "properties", lambda r: str(r.title), "excited states",
    ),
}


def _plugin_panel_spec(kind: str) -> _PanelSpec | None:
    """The side panel of a kind an installed renderer plugin claims (#113).

    Plugins fill the details panel, like the built-in table kinds; their
    ``render_to_html()`` takes no options in API version 1.
    """
    from vibeview.renderer_plugins import get_renderer_plugin

    try:
        plugin = get_renderer_plugin(kind)
    except Exception:  # noqa: BLE001 — discovery isolates plugins; stay safe
        return None
    if plugin is None:
        return None
    return _PanelSpec(plugin.renderer, "properties", plugin.title, plugin.title)


def _activate_panel(reader: QVFReader, state: _ViewerUIState, section: Section) -> None:
    """Fill the side panel for a :data:`_PANEL_SPECS` or plugin kind."""
    import importlib

    builtin = section.kind in _PANEL_SPECS
    spec = _PANEL_SPECS[section.kind] if builtin else _plugin_panel_spec(section.kind)
    if spec is None:
        state.status_message = f"No renderer for {section.kind}: {section.id}"
        return
    if not builtin:
        _activate_plugin_panel(reader, state, section, spec)
        return
    assert isinstance(spec.renderer, str)
    module, _, name = spec.renderer.partition(":")
    renderer = getattr(importlib.import_module(module), name)(section, reader)
    noun = spec.noun(renderer) if callable(spec.noun) else spec.noun
    try:
        html = renderer.render_to_html(**(spec.options(state) if spec.options else {}))
        title = spec.title(renderer) if callable(spec.title) else spec.title
        setattr(state, f"{spec.channel}_html", html)
        setattr(state, f"{spec.channel}_title", title)
        state.status_message = (
            spec.status(renderer, section) if spec.status else f"Loaded {noun}: {section.id}"
        )
    except QVFError as e:
        state.status_message = f"Error loading {noun}: {e}"


def _activate_plugin_panel(
    reader: QVFReader, state: _ViewerUIState, section: Section, spec: _PanelSpec
) -> None:
    """Run a plugin's renderer, containing whatever it raises.

    Plugin code is third-party: any failure, constructing the renderer
    included, leaves the panel empty and names the plugin in the status line
    instead of reaching the controller. A return value that is not text is
    a failure too, since the panel shows it as a document.
    """
    noun = str(spec.noun)
    renderer_class = spec.renderer
    assert isinstance(renderer_class, type)
    try:
        html = renderer_class(section, reader).render_to_html()
        if not isinstance(html, str):
            raise TypeError(f"render_to_html() returned {type(html).__name__}, not str")
    except Exception as e:  # noqa: BLE001 — plugin code must not break the viewer
        state.status_message = f"Renderer plugin for {section.kind} failed: {type(e).__name__}: {e}"
        return
    setattr(state, f"{spec.channel}_html", html)
    setattr(state, f"{spec.channel}_title", str(spec.title))
    state.status_message = f"Loaded {noun}: {section.id}"


def _open_structure(ctx: _SectionContext, section: Section) -> None:
    # Clear everything previous sections drew over the molecule (isosurfaces,
    # vibration ghosts, trajectory frames) so the bare structure shows again.
    # The display controller has already made ``section`` the shown structure
    # when it was another one (#90).
    for prefix in (_Actors.VOLUME, _Actors.VIB_ATOM, _Actors.TRAJ_ATOM):
        _remove_actors_by_prefix(ctx.plotter, prefix)
    ctx.plotter.render()
    _push_view(ctx.plotter)
    title = ctx.reader.structure_identity(section.id).title
    ctx.state.status_message = f"Structure {title}" if title else "Structure"


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
    from vibeview.renderer_plugins import plugin_kinds

    if kind in plugin_kinds():
        return _panel_handler()
    return None
