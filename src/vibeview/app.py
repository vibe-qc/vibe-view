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
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pyvista as pv
from pyvista.plotting.colors import get_cmap_safe

from vibeview.kinds import classify_section
from vibeview.qvf import QVFError, clamp_replication
from vibeview.viewer_defaults import ViewerState

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader


# Friendly display titles for the sidebar — raw section.kind strings are
# fine in logs and the manifest but read poorly to chemists scanning the UI.
_KIND_TITLES: dict[str, str] = {
    "structure": "Structure",
    "structure.symmetry": "Symmetry",
    "volume.density": "Electron Density",
    "volume.orbital": "Molecular Orbital",
    "volume.spin": "Spin Density",
    "volume.elf": "ELF",
    "volume.difference": "Difference Density",
    "volume.generic": "Volume",
    "volume.potential": "Electrostatic Potential",
    "volume.rdg": "NCI (Reduced Density Gradient)",
    "fermi_surface": "Fermi Surface",
    "atom_properties": "Atomic Properties",
    "vibrations": "Vibrations",
    "spectra.ir": "IR Spectrum",
    "spectra.raman": "Raman Spectrum",
    # NOTE: the canonical kind is `spectra.uvvis` (not `spectra.uv`); the old
    # key never matched, so UV-Vis sections showed the raw kind in the sidebar.
    "spectra.uvvis": "UV-Vis Spectrum",
    "spectra.ecd": "ECD Spectrum",
    "spectra.vcd": "VCD Spectrum",
    "spectra.generic": "Spectrum",
    "spectra.nmr": "NMR Shifts",
    "spectra.epr": "EPR Parameters",
    "bands": "Band Structure",
    "dos.total": "Density of States",
    "dos.projected": "Projected DOS",
    "phonon_bands": "Phonon Bands",
    "phonon_dos": "Phonon DOS",
    "equation_of_state": "Equation of State",
    "trajectory": "Trajectory",
    "reaction.path": "Reaction Path",
    "reaction.waypoints": "Reaction Waypoints",
    "scan.surface": "Scan Surface",
    "wavefunction.gto": "Wavefunction",
    "basis.ao": "Basis Function",
    "citations": "Citations",
    "run.record": "Run Record",
    "job.spec": "Job Spec",
    "scf_history": "SCF Convergence",
    "bond_orders": "Bond Orders",
    "topology.qtaim": "QTAIM Topology",
    "dos.coop": "COOP",
    "dos.cohp": "COHP",
}

# Per-kind sidebar icons (design refresh 2026). With eight-plus sections in a
# file the list was a wall of identical status ticks; a kind glyph makes it
# scannable. Families share a glyph on purpose — every spectrum reads as a
# wave, every volume as a blob — so the eye groups them.
_KIND_ICONS: dict[str, str] = {
    "structure": "mdi-molecule",
    "structure.symmetry": "mdi-mirror-rectangle",
    "volume.density": "mdi-blur",
    "volume.orbital": "mdi-orbit",
    "volume.spin": "mdi-magnet",
    "volume.elf": "mdi-blur-radial",
    "volume.difference": "mdi-delta",
    "volume.generic": "mdi-blur",
    "volume.potential": "mdi-flash",
    "volume.rdg": "mdi-blur-radial",
    "fermi_surface": "mdi-sphere",
    "atom_properties": "mdi-table",
    "vibrations": "mdi-waveform",
    "spectra.ir": "mdi-chart-bell-curve",
    "spectra.raman": "mdi-chart-bell-curve",
    "spectra.uvvis": "mdi-chart-bell-curve",
    "spectra.ecd": "mdi-chart-bell-curve",
    "spectra.vcd": "mdi-chart-bell-curve",
    "spectra.generic": "mdi-chart-bell-curve",
    "spectra.nmr": "mdi-magnet-on",
    "spectra.epr": "mdi-magnet-on",
    "bands": "mdi-chart-multiline",
    "dos.total": "mdi-chart-areaspline",
    "dos.projected": "mdi-chart-areaspline",
    "dos.coop": "mdi-chart-areaspline",
    "dos.cohp": "mdi-chart-areaspline",
    "phonon_bands": "mdi-chart-multiline",
    "phonon_dos": "mdi-chart-areaspline",
    "equation_of_state": "mdi-chart-scatter-plot",
    "trajectory": "mdi-play-circle-outline",
    "reaction.path": "mdi-source-branch",
    "reaction.waypoints": "mdi-map-marker-path",
    "scan.surface": "mdi-grid",
    "wavefunction.gto": "mdi-atom",
    "basis.ao": "mdi-function-variant",
    "citations": "mdi-book-open-variant",
    "run.record": "mdi-console-line",
    "job.spec": "mdi-clipboard-text-clock",
    "scf_history": "mdi-chart-line",
    "bond_orders": "mdi-vector-line",
    "topology.qtaim": "mdi-vector-triangle",
}

_DEFAULT_KIND_ICON = "mdi-file-document-outline"

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


def _sidebar_section_title(section: dict) -> str:
    """Sidebar title for one section.

    Mostly a ``_KIND_TITLES`` lookup, but several sections legitimately share
    the ``wavefunction.gto`` kind — the canonical set (``wf``), a localized
    set (``wf_localized``) and any NTO pairs (``wf_nto_*``) — and the plain
    lookup gave all of them the identical title "Wavefunction", leaving the
    sidebar with indistinguishable rows. Section ids are unique by spec, so
    they are the reliable discriminator.
    """
    kind = section.get("kind", "")
    if kind == "wavefunction.gto":
        section_id = str(section.get("id", ""))
        # ``wf_relocalized_*`` are computed in this session by the re-localize
        # feature; ``wf_localized_*`` came from the file. Distinguished in the
        # title so a user can tell which they are looking at.
        for prefix, label in (
            ("wf_relocalized", "Re-localized"),
            ("wf_localized", "Localized Orbitals"),
        ):
            if not section_id.startswith(prefix):
                continue
            criterion = section_id[len(prefix) :].lstrip("_")
            pretty = {
                "": "",
                "ibo": "IBO",
                "boys": "Boys",
                "pipek_mezey": "Pipek-Mezey",
            }.get(criterion, criterion.replace("_", "-").title())
            return f"{label} ({pretty})" if pretty else label
        if section_id.startswith("wf_nto"):
            if section_id.endswith("_hole"):
                return "NTO (hole)"
            if section_id.endswith("_electron"):
                return "NTO (electron)"
            return "Natural Transition Orbitals"
    return _KIND_TITLES.get(kind, kind)


def _kind_icon(kind: str) -> str:
    """Sidebar glyph for a section kind (falls back to a generic document)."""
    return _KIND_ICONS.get(kind, _DEFAULT_KIND_ICON)


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


def build_pending_container(sdata, params: dict, stem):
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
        _ANGSTROM_TO_BOHR,
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
    return _vq.write_pending_qvf(
        system,
        stem,
        method=params.get("method") or None,
        basis=params.get("basis") or None,
        functional=params.get("functional") or None,
    )


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

# Synthetic sidebar id used to collapse all volume.orbital sections under a
# single orbital-group entry; clicking it activates the first orbital
# and lets a right-panel select switch between the rest.
_ORBITAL_GROUP_ID = "__orbitals__"
# Same pattern for basis.ao sections — collapsed into "Basis Functions (N)".
_BASIS_AO_GROUP_ID = "__basis_ao__"


def _orbital_label(is_periodic: bool, *, plural: bool = False) -> str:
    """Return the correct orbital terminology for the current system.

    Molecular calculations get "Molecular Orbital(s)";
    periodic calculations get "Crystalline Orbital(s)".
    """
    prefix = "Crystalline" if is_periodic else "Molecular"
    return f"{prefix} Orbitals" if plural else f"{prefix} Orbital"


def _orbital_group_sidebar_title(n_orbital_sections: int, is_periodic: bool) -> str:
    """Sidebar group title for collapsed orbital sections, e.g.
    ``"Crystalline Orbitals (12)"``."""
    return f"{_orbital_label(is_periodic, plural=True)} ({n_orbital_sections})"


def _qa_validate(reader: "QVFReader") -> list[str]:
    """Check the QVF for common issues and return warning messages.

    Checks: SCF convergence, negative total energy (impossible),
    unusually high energy, missing structure section.
    """
    warnings: list[str] = []
    prov = getattr(reader.manifest, "provenance", None) or {}
    if hasattr(prov, "model_dump"):
        prov = prov.model_dump()
    if not isinstance(prov, dict):
        return warnings

    # SCF convergence check.
    if prov.get("scf_converged") is False:
        warnings.append("⚠ SCF did not converge — energies and properties may be unreliable")

    # Energy sanity checks.
    energy = prov.get("scf_energy")
    if isinstance(energy, dict):
        energy_val = energy.get("value")
    else:
        energy_val = energy
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


def _clamp_viewer_replication(reader: "QVFReader", viewer_state) -> None:
    """Clamp a file's replication hint to its periodic axes, in place.

    A QVF may carry a ``replication`` viewer hint, and it lands in
    ``viewer_state`` before the user touches any control. For a 2D slab an
    ``Nz > 1`` hint would stack phantom sheets along the synthesized normal at
    load time, so clamp it the same way ``update_replication`` clamps the UI.
    """
    try:
        pbc = reader.read_structure().pbc
    except Exception:
        return
    viewer_state.replication = clamp_replication(viewer_state.replication, pbc)


def _build_structure_scene(
    reader: "QVFReader",
    plotter: "pv.Plotter",
    state,
    replication: tuple[int, int, int],
    show_labels: bool,
    representation: str = "ball_and_stick",
    cartoon_color_mode: str = "chain",
    residue_selection: str = "",
) -> "tuple[object | None, bool]":
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
    _remove_actors_by_prefix(plotter, "dipole_arrow")
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
                plotter.add_mesh(arrow, color="#ff6600", name="dipole_arrow", show_scalar_bar=False)

    # ── Wannier-centre overlay (cyclic cluster / CCM) ───────────────
    _draw_wannier_overlay(reader, plotter, getattr(state, "show_wannier_centers", False))

    return renderer, is_periodic


def _draw_wannier_overlay(reader: "QVFReader", plotter, show: bool) -> int:
    """Draw (or clear) the Wannier-centre markers overlay.

    Returns the number of centres drawn. Always clears the previous actor
    first so a toggle-off or a file-switch removes stale markers.
    """
    _remove_actors_by_prefix(plotter, "wannier_center")
    if not show:
        return 0
    try:
        from vibeview.renderers.wannier import build_wannier_glyphs, read_wannier_centres

        centres = read_wannier_centres(reader)
        glyphs = build_wannier_glyphs(centres)
        if glyphs is not None:
            plotter.add_mesh(
                glyphs, color="#ffcc33", name="wannier_center", show_scalar_bar=False
            )
        return len(centres)
    except Exception:
        return 0


def serve(app, *, host: str = "127.0.0.1", port: int = 8080) -> None:
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
    from vibeview.cli import _disable_http_caching  # noqa: PLC0415

    _disable_http_caching()
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


def _builder_name(reader: "QVFReader") -> "str | None":
    """The molecule name a builder-made archive was created from, else None.

    ``converters.atoms_to_qvf`` tags these ``builder:<name>`` in
    ``manifest.source.calculation``; they have no path on disk.
    """
    if reader.path is not None:
        return None
    try:
        calculation = reader.source.calculation
    except Exception:  # noqa: BLE001 — a malformed manifest is just "not a build"
        return None
    if not calculation.startswith("builder:"):
        return None
    return calculation.split(":", 1)[1] or None


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


def _iupac_name_entry(reader: "QVFReader") -> "dict | None":
    """Optional IUPAC-name header entry for the top of the section sidebar.

    Uses the standalone, pure-Python ``vibeqc_naming`` package (no C++
    extension needed). Naming is optional: any failure — the package not
    installed, an unreadable structure, an empty name — returns None and the
    sidebar renders normally without it. The entry is non-clickable
    (``disabled``) because naming has no panel of its own, and prepended so
    it shows before the Structure section.

    Structures made by the molecule builder have no path for ``name_from_qvf``
    to open. They already carry the name that produced them, so it is shown
    directly rather than re-derived — re-deriving would route a built THF
    through the systematic organic namer and label it "1-methyl methanal"
    (see handovers/HANDOVER_IUPAC_NAMING.md § B2). Loose files converted on
    the fly (.py inputs, .xyz, ...) also arrive as in-memory QVFs; those are
    named from their structure atoms, the same route ``name_from_qvf`` uses
    internally for on-disk archives.
    """
    built = _builder_name(reader)
    if built is not None:
        return {
            "id": "__iupac_name__",
            "title": built,
            "subtitle": "IUPAC — builder",
            "status": "ok",
            "supported": True,
            "icon": "mdi-molecule",
            "kind_icon": "mdi-tag-text-outline",
            "warn": False,
            "disabled": True,
            "kind": "iupac_name",
        }
    try:
        from vibeqc_naming import (
            name_from_atoms_detailed,
            name_from_atoms_with_lattice,
            name_from_qvf,
        )

        if reader.path:
            named = name_from_qvf(str(reader.path))
        else:
            # In-memory QVF (a loose file converted on the fly — an opened
            # .py input, .xyz, ...) has no archive path for name_from_qvf.
            # Name the structure directly, mirroring what name_from_qvf does
            # with the on-disk archive: atoms in Angstrom, lattice rows when
            # the structure is periodic.
            structure = reader.read_structure()
            atoms = [
                (
                    a.atomic_number,
                    float(a.position[0]),
                    float(a.position[1]),
                    float(a.position[2]),
                )
                for a in structure.atoms
            ]
            lattice = getattr(structure, "lattice_vectors", None)
            pbc = getattr(structure, "pbc", (False, False, False))
            if (
                lattice is not None
                and len(lattice) > 0
                and any(bool(flag) for flag in pbc)
            ):
                rows = [
                    (
                        float(lattice[i][0]),
                        float(lattice[i][1]),
                        float(lattice[i][2]),
                    )
                    for i in range(3)
                ]
                named = name_from_atoms_with_lattice(atoms, rows)
                if not named.name:
                    # A pure bulk crystal (no surface or adsorbate) yields no
                    # slab-style name; fall back to the structure route
                    # ("octac" for diamond, ...) instead of hiding the entry.
                    named = name_from_atoms_detailed(atoms)
            else:
                named = name_from_atoms_detailed(atoms)
        if not named.name:
            return None
        icon = {
            "high": "mdi-check-circle",
            "medium": "mdi-information",
            "low": "mdi-alert-circle",
        }.get(named.confidence.value, "mdi-help-circle")
        return {
            "id": "__iupac_name__",
            "title": named.name,
            "subtitle": f"IUPAC — {named.source.value}",
            "status": "ok",
            "supported": True,
            "icon": icon,
            "kind_icon": "mdi-tag-text-outline",
            "warn": False,
            "disabled": True,  # informational only; no panel to open
            "kind": "iupac_name",
        }
    except Exception:
        return None  # naming is optional — the sidebar works fine without it


def _bfactor_range_message(reader) -> str:
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
        a.b_factor for a in structure.atoms if getattr(a, "b_factor", None) is not None
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


def _default_representation(reader) -> str:
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
        structure = reader.read_structure()
    except Exception:  # noqa: BLE001 — a non-structure file just uses the default
        return "ball_and_stick"
    try:
        if structure.has_residues and len(structure.backbone_trace()) >= 4:
            return "cartoon"
    except Exception:  # noqa: BLE001 — malformed residue metadata is not fatal
        pass
    return "ball_and_stick"


def _selectable_chains(reader) -> list[str]:
    """Chain ids a residue selection can name, for the UI's hint text.

    A chainless PDB groups under the empty chain id, which cannot be typed,
    so it is dropped here and reached with the ``*`` wildcard instead.
    Control visibility is tracked separately because that legitimate
    chainless case also produces an empty list.
    """
    try:
        structure = reader.read_structure()
        chains: list[str] = []
        for chain, _seq, _index in structure.ca_residues():
            if chain and chain not in chains:
                chains.append(chain)
        return chains
    except Exception:  # noqa: BLE001 — no structure section, or unreadable
        return []


def _residue_selection_available(reader) -> bool:
    """Whether the loaded structure has at least one selectable CA residue."""
    try:
        return bool(reader.read_structure().ca_residues())
    except Exception:  # noqa: BLE001 — no structure section, or unreadable
        return False


def _residue_selection_summary(reader, spec: str) -> str:
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


_DIALOG_FOCUS_JS = (
    "typeof document !== 'undefined' && typeof requestAnimationFrame !== 'undefined' && "
    "(function(){"
    "if(window._vibe_dialog_focus_bound)return;"
    "window._vibe_dialog_focus_bound=1;"
    "var origins=Object.create(null);var stable=null;"
    "var selector='button,[href],input,select,textarea,[role=button],'"
    "+'[tabindex]:not([tabindex=\"-1\"])';"
    "function candidate(el){return el&&el.closest?el.closest(selector):null;}"
    "function dialog(el){return el&&el.closest?el.closest('[role=dialog]'):null;}"
    "function transientOverlay(el){return !!(el&&el.closest&&el.closest('.v-overlay')&&"
    "!dialog(el)&&!el.closest('.vv-presentation-overlay-content'));}"
    "function visible(el){if(!el||!el.isConnected||!el.getClientRects().length)return false;"
    "var style=getComputedStyle(el);return style.display!=='none'&&style.visibility!=='hidden';}"
    "function usable(el){return !!(visible(el)&&typeof el.focus==='function'&&!el.disabled&&"
    "el.getAttribute('aria-disabled')!=='true'&&!el.closest('[inert]')&&"
    "!el.closest('[aria-hidden=\"true\"]')&&!transientOverlay(el));}"
    "function remember(el){el=candidate(el);"
    "if(!el||transientOverlay(el)||!usable(el))return;stable=el;}"
    "remember(document.activeElement);"
    "document.addEventListener('focusin',function(event){"
    "var modal=event.target&&event.target.closest?event.target.closest('[role=dialog]'):null;"
    "var key=modal&&modal.getAttribute('aria-label');"
    "if(!key){remember(event.target);return;}"
    "if(modal._vvFocusCaptured){remember(event.target);return;}"
    "modal._vvFocusCaptured=true;"
    "var related=candidate(event.relatedTarget);"
    "var origin=related&&!transientOverlay(related)&&usable(related)?related:stable;"
    "modal._vvFocusOrigin=origin||null;"
    "if(!origins[key])origins[key]=[];"
    "origins[key].push({modal:modal,target:origin||null});"
    "remember(event.target);"
    "},true);"
    "document.addEventListener('pointerdown',function(event){remember(event.target);},true);"
    "window._vvRestoreDialogFocus=function(key){var stack=origins[key]||[];"
    "var entry=stack.length?stack[stack.length-1]:null;var attempts=0;"
    "if(entry&&entry.modal){entry.modal._vvFocusCaptured=false;"
    "entry.modal._vvFocusOrigin=null;}"
    "function discard(){var current=origins[key]||[];var index=current.indexOf(entry);"
    "if(index>=0)current.splice(index,1);if(!current.length)delete origins[key];}"
    "function restore(){var current=origins[key]||[];"
    "if(entry&&current.length&&current[current.length-1]!==entry){discard();return;}"
    "var same=document.querySelectorAll('[role=dialog][aria-label=\"'+key+'\"]');"
    "for(var s=0;s<same.length;s++){if(visible(same[s])){"
    "if(++attempts<8){requestAnimationFrame(restore);return;}discard();return;}}"
    "discard();"
    "var target=entry&&entry.target;var open=document.querySelectorAll('[role=dialog]');"
    "var top=null;for(var d=0;d<open.length;d++){if(visible(open[d]))top=open[d];}"
    "if(top){if(target&&top.contains(target)&&usable(target)){focus(target);return;}"
    "var active=candidate(document.activeElement);"
    "if(active&&top.contains(active)&&usable(active))return;"
    "var choices=top.querySelectorAll(selector);"
    "for(var c=0;c<choices.length;c++){if(usable(choices[c])){focus(choices[c]);return;}}"
    "if(typeof top.focus==='function')focus(top);return;}"
    "if(usable(target))focus(target);"
    "}requestAnimationFrame(restore);};"
    "function focus(target){try{target.focus({preventScroll:true});}"
    "catch(error){target.focus();}}"
    "})()"
)


def _dialog_focus_restore(label: str) -> str:
    return f"window._vvRestoreDialogFocus && window._vvRestoreDialogFocus('{label}')"


def create_app(readers):
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

    # ── Wrap with lazy loader for performance ──────────────────────
    def _open_lazy_reader(active_reader):
        if active_reader.path is None:
            return None
        try:
            from vibeview.lazy_loader import QVFLazyReader

            return QVFLazyReader(str(active_reader.path))
        except Exception:
            return None

    lazy_reader = _open_lazy_reader(reader)

    def _replace_lazy_reader(active_reader) -> None:
        """Bind lazy-backed controllers to ``active_reader`` and retire the old one."""
        nonlocal lazy_reader

        retired = lazy_reader
        lazy_reader = _open_lazy_reader(active_reader)
        if retired is not None:
            with contextlib.suppress(Exception):
                retired.close()

    try:
        from trame.app import get_server
        from trame.ui.vuetify3 import VAppLayout
        from trame.widgets import client, html
        from trame.widgets import vuetify3 as v
        from trame_vtk.modules import vtk as vtk_module
        from trame_vtk.widgets.vtk import VtkLocalView
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
    viewer_state = ViewerState.from_manifest(reader.viewer_defaults)
    _clamp_viewer_replication(reader, viewer_state)
    replication = list(viewer_state.replication)

    # Prepare section list for the sidebar
    section_list: list[dict] = []
    for s in reader.sections:
        status, detail = classify_section(s.kind)
        err = reader.section_error(s.id)
        if err:
            status = "error"
            detail = err
        section_list.append(
            {
                "id": s.id,
                "kind": s.kind,
                "status": status,
                "detail": detail or "",
                "supported": s.kind in _supported_kinds_set(),
            }
        )

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
    is_periodic = False
    _structure_probe = next((s for s in reader.sections if s.kind == "structure"), None)
    if _structure_probe is not None:
        try:
            # The probe needs only the lattice. Bond inference costs 158.7 s
            # on a measured 13,772-atom protein and must stay off the startup
            # path until a bond-bearing representation is actually rendered.
            _sd = reader.read_structure()
            is_periodic = bool(_sd.lattice_vectors is not None and any(_sd.pbc))
        except Exception:
            is_periodic = False

    sidebar_entries: list[dict] = []
    # IUPAC molecule name header (optional; standalone vibeqc_naming). Prepend
    # so it is the first entry, before Structure.
    _iupac_entry = _iupac_name_entry(reader)
    if _iupac_entry is not None:
        sidebar_entries.append(_iupac_entry)
    for s in section_list:
        # volume.orbital and basis.ao are each collapsed into one group
        # entry; bonds has no panel of its own (folded into the structure view).
        if s["kind"] in ("volume.orbital", "basis.ao", "bonds"):
            continue
        sidebar_entries.append(
            {
                "id": s["id"],
                "title": _sidebar_section_title(s),
                "subtitle": f"{s['kind']} — {s['status']}",
                "status": s["status"],
                "supported": s["supported"],
                "icon": _status_icon(s["status"]),
                "kind_icon": _kind_icon(s["kind"]),
                "warn": s["status"] == "error" or not s["supported"],
                "disabled": not s["supported"],
            }
        )
    if orbital_sections:
        supported = all(s["supported"] for s in orbital_sections)
        worst_status = (
            "error"
            if any(s["status"] == "error" for s in orbital_sections)
            else orbital_sections[0]["status"]
        )
        sidebar_entries.append(
            {
                "id": _ORBITAL_GROUP_ID,
                "title": _orbital_group_sidebar_title(len(orbital_sections), is_periodic),
                "subtitle": f"volume.orbital — {worst_status}",
                "status": worst_status,
                "supported": supported,
                "icon": _status_icon(worst_status),
                "kind_icon": _kind_icon("volume.orbital"),
                "warn": worst_status == "error" or not supported,
                "disabled": not supported,
            }
        )
    if basis_ao_sections:
        supported = all(s["supported"] for s in basis_ao_sections)
        worst_status = (
            "error"
            if any(s["status"] == "error" for s in basis_ao_sections)
            else basis_ao_sections[0]["status"]
        )
        sidebar_entries.append(
            {
                "id": _BASIS_AO_GROUP_ID,
                "title": f"Basis Functions ({len(basis_ao_sections)})",
                "subtitle": f"basis.ao — {worst_status}",
                "status": worst_status,
                "supported": supported,
                "icon": _status_icon(worst_status),
                "kind_icon": _kind_icon("basis.ao"),
                "warn": worst_status == "error" or not supported,
                "disabled": not supported,
            }
        )
    # Options for the right-panel orbital picker.
    orbital_options = [{"title": s["id"], "value": s["id"]} for s in orbital_sections]
    # Options for the right-panel AO picker.
    ao_options = [{"title": s["id"], "value": s["id"]} for s in basis_ao_sections]

    # ── Build PyVista scene ────────────────────────────────────────────
    plotter = pv.Plotter(off_screen=True, window_size=(900, 600))
    plotter.set_background("#1a1a2e")

    # ── SSAO for server-side renders (screenshots, video frames) ──
    # A renderer pass never reaches the client's live viewport — vtk.js
    # redraws from serialized actors — so this shapes exports only.
    # Matches the ssao_enabled state default (True); radius/samples come
    # from ambient_occlusion_pass's molecular-scale defaults.
    try:
        from vibeview.material_presets import ambient_occlusion_pass

        ambient_occlusion_pass(plotter)
    except Exception:
        pass  # SSAO not available on this VTK build

    # ── Trame server setup (must come before structure loading so
    #     we can report errors through the UI status bar) ──────────────
    server = get_server(client_type="vue3")
    server.enable_module(vtk_module)
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
            "wf_selected_mo": "restricted:0",
            "wf_selected_spin": "restricted",
            # Re-localize (vibeview.relocalize): ask vibe-qc for a
            # localization criterion the file does not already contain.
            # ``None`` = not yet probed, mirroring ``live_opt_available``.
            "relocalize_available": None,
            "relocalize_running": False,
            "relocalize_status": "",
            "relocalize_method": "ibo",
            "relocalize_method_options": [],
            "wf_n_per_dim": 60,
            # Total density vs spin density (rho_alpha - rho_beta). The
            # button below drives both; spin needs an unrestricted section.
            "wf_density_spin": False,
            # Gates the spin-density button; set per activated section.
            "wf_spin_unrestricted": False,
            # ELF isosurface level. 0.8 is the conventional choice for
            # showing shells / bonds / lone pairs; the density's 0.05 default
            # would be meaningless for a function bounded in [0, 1].
            "wf_elf_iso": 0.8,
            # NCI reduced-gradient isosurface level; 0.5 au is the value
            # Johnson et al. use throughout (their Fig. 3).
            "wf_nci_iso": 0.5,
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
            # Residue/chain selection (D4). Empty selects nothing; matching
            # residues render white over the active ribbon colour.
            "residue_selection": "",
            "residue_selection_summary": "",
            "residue_selection_chains": _selectable_chains(reader),
            "residue_selection_available": _residue_selection_available(reader),
            # Material presets (v1.6)
            "material_preset": "cpk_glossy",
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
            "settings_dark_background": True,
            "settings_material_preset": "cpk_glossy",
            "settings_auto_save_interval": 60,
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
            "dark_background": True,
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
                "Escape": "Exit presentation or edit mode",
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
            "export_ready": False,
            # Per-element colour overrides (design refresh 2026). The picker
            # lists only the elements actually present; overrides are keyed by
            # atomic number and applied inside cpk_color().
            "element_color_options": _present_elements(reader),
            "element_colors": {},
            "element_color_z": None,
            "element_color_value": "#FF8800",
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
            "vq_jobs_dialog": False,  # legacy; kept for back-compat
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
            "vq_submit_input": "",  # path to .py input file
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
            "file_picker_value": None,
            # Lazy section loading for faster startup
            "lazy_loading": True,
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
        replication=tuple(replication),
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

    plotter.view_isometric()
    plotter.show_grid()

    # ── Apply camera bookmark from viewer_defaults ────────────────
    _apply_camera(plotter, viewer_state.camera)

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
    for section_id in viewer_state.auto_open:
        if not reader.has_section(section_id):
            continue
        section = reader.get_section(section_id)
        if section.kind.startswith("volume."):
            hints = viewer_state.get_volume_hints(section_id, kind=section.kind)
            server.state.isovalue = hints.isovalue
            server.state.colormap = hints.colormap
            server.state.opacity = hints.opacity
            server.state.active_volume_id = section_id
            server.state.selected_section = section_id
            server.state.volume_loaded = False
            server.state.status_message = f"Auto-opened: {section_id}"
        elif section.kind == "bands":
            _activate_bands(reader, server.state, section)
            server.state.selected_section = section_id
        elif section.kind in _plot_spectra_kinds():
            _activate_spectra(reader, server.state, section, _all_readers)
            server.state.selected_section = section_id
        elif section.kind == "spectra.nmr":
            _activate_nmr(reader, server.state, section)
            server.state.selected_section = section_id
        elif section.kind == "spectra.epr":
            _activate_epr(reader, server.state, section)
            server.state.selected_section = section_id
        elif section.kind == "trajectory":
            _activate_trajectory(reader, server.state, section)
            server.state.selected_section = section_id
        elif section.kind == "reaction.path":
            _activate_reaction_path(reader, server.state, section)
            server.state.selected_section = section_id
        elif section.kind == "scan.surface":
            _activate_scan_surface(reader, server.state, section)
            server.state.selected_section = section_id
        elif section.kind == "wavefunction.gto":
            _activate_wavefunction(reader, server.state, section)
            server.state.selected_section = section_id
        elif section.kind == "citations":
            _activate_citations(reader, server.state, section)
            server.state.selected_section = section_id
        elif section.kind == "run.record":
            _activate_run_record(reader, server.state, section)
            server.state.selected_section = section_id
        elif section.kind == "job.spec":
            _activate_job_spec(reader, server.state, section)
            server.state.selected_section = section_id
        elif section.kind == "bond_orders":
            _activate_bond_orders(reader, server.state, section)
            server.state.selected_section = section_id
        elif section.kind == "topology.qtaim":
            # `server.state`, not a bare `state`: there is no such local in
            # this scope, so auto-opening one of these sections raised
            # NameError. The duplicate dos.coop branch that used to follow
            # this one was unreachable behind it.
            _activate_topology_qtaim(reader, plotter, server.state, section)
        elif section.kind in ("dos.coop", "dos.cohp"):
            _activate_dos_coop(reader, server.state, section)
            server.state.selected_section = section_id
        elif section.kind == "scf_history":
            _activate_scf_history(reader, server.state, section)
            server.state.selected_section = section_id
        elif section.kind == "structure.symmetry":
            _activate_symmetry(reader, server.state, section)
            server.state.selected_section = section_id
        elif section.kind == "basis.ao":
            hints = viewer_state.get_volume_hints(section_id, kind="basis.ao")
            server.state.isovalue = hints.isovalue
            server.state.colormap = hints.colormap
            server.state.opacity = hints.opacity
            server.state.active_volume_id = section_id
            server.state.selected_section = section_id
            server.state.volume_loaded = False
            server.state.status_message = f"Auto-opened: {section_id}"

    # ── Reactive handlers ─────────────────────────────────────────────
    ctrl = server.controller

    # ── Multi-file support ───────────────────────────────────────────

    # Computed MO renders may run on the animation timer's worker thread.
    # Keep expensive render work serialized, while a separate short-held epoch
    # lock lets section/file switches invalidate an in-flight render without
    # waiting for its grid evaluation to finish.
    _wf_render_lock = threading.RLock()
    _wf_epoch_lock = threading.RLock()
    _wf_render_epoch = 0
    _wf_anim_timer = None
    plotter._vibeview_wf_render_lock = _wf_render_lock

    def _wavefunction_render_is_current(
        epoch: int,
        section_id: str,
        source_reader,
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
        _remove_actors_by_prefix(plotter, "edit_highlight_")
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

    def _update_live_status(r) -> None:
        """Reflect a streaming checkpoint's provenance in the app bar."""
        status, text = _checkpoint_summary(r)
        server.state.live_run_status = status
        server.state.live_checkpoint_text = text

    def _reload_active_file(preserve_camera: bool = False):
        """Invalidate worker renders, then replace the VTK scene exclusively."""
        _invalidate_wavefunction_renders()
        with _wf_render_lock:
            return _reload_active_file_unlocked(preserve_camera)

    def _reload_active_file_unlocked(preserve_camera: bool = False):
        """Rebuild the entire UI for the currently-active reader.

        Called once during initialisation (index 0) and again whenever
        the user switches files via the AppBar dropdown.  Uses
        ``nonlocal`` to rebind every closure-captured name so all
        handlers and helpers see the new reader and its data.

        ``preserve_camera=True`` keeps the user's current viewpoint
        instead of applying the file's camera bookmark — the file
        watcher's hot-reload path, where yanking the camera on every
        checkpoint would make live monitoring unusable.
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

        # Reset to a known-molecular default so a structure-less file can't
        # inherit the previous file's periodic flag (stale-state bug).
        is_periodic = False

        reader = incoming_reader
        _replace_lazy_reader(reader)
        viewer_state = ViewerState.from_manifest(reader.viewer_defaults)
        _clamp_viewer_replication(reader, viewer_state)
        server.state.residue_selection_chains = _selectable_chains(reader)
        server.state.residue_selection_available = _residue_selection_available(reader)
        server.state.residue_selection_summary = _residue_selection_summary(
            reader, str(server.state.residue_selection or "")
        )
        replication = list(viewer_state.replication)
        server.state.replication_nx = replication[0]
        server.state.replication_ny = replication[1]
        server.state.replication_nz = replication[2]

        # Rebuild section list
        section_list.clear()
        for s in reader.sections:
            status, detail = classify_section(s.kind)
            err = reader.section_error(s.id)
            if err:
                status = "error"
                detail = err
            section_list.append(
                {
                    "id": s.id,
                    "kind": s.kind,
                    "status": status,
                    "detail": detail or "",
                    "supported": s.kind in _supported_kinds_set(),
                    # producer marked it still growing (live checkpoint, M4)
                    "partial": reader.section_is_partial(s.id),
                }
            )
        server.state.section_list = section_list
        # section_list is a closure-held list mutated in place; trame's
        # setattr drops the push when the assigned object IS the pushed one
        # (`value == pushed_state[key]` — identity ⇒ equal), so the client
        # would never see the rebuilt list without an explicit dirty().
        server.state.dirty("section_list")

        # Rebuild sidebar entries (with orbital + basis.ao collapsing). The
        # basis.ao half used to be omitted here, so a file switch left the
        # Basis Functions group + AO picker pointing at the previous file.
        orbital_sections = [s for s in section_list if s["kind"] == "volume.orbital"]
        basis_ao_sections = [s for s in section_list if s["kind"] == "basis.ao"]
        sidebar_entries.clear()
        # IUPAC name header first (optional), so it survives a file switch too.
        _iupac_entry = _iupac_name_entry(reader)
        if _iupac_entry is not None:
            sidebar_entries.append(_iupac_entry)
        for s in section_list:
            if s["kind"] in ("volume.orbital", "basis.ao", "bonds"):
                continue
            subtitle = f"{s['kind']} — {s['status']}"
            if s.get("partial"):
                subtitle += " · streaming"
            sidebar_entries.append(
                {
                    "id": s["id"],
                    "title": _sidebar_section_title(s),
                    "subtitle": subtitle,
                    "status": s["status"],
                    "supported": s["supported"],
                    "icon": _status_icon(s["status"]),
                    "kind_icon": _kind_icon(s["kind"]),
                    "warn": s["status"] == "error" or not s["supported"],
                    "disabled": not s["supported"],
                }
            )
        if orbital_sections:
            supported = all(s["supported"] for s in orbital_sections)
            worst_status = (
                "error"
                if any(s["status"] == "error" for s in orbital_sections)
                else orbital_sections[0]["status"]
            )
            sidebar_entries.append(
                {
                    "id": _ORBITAL_GROUP_ID,
                    "title": _orbital_group_sidebar_title(len(orbital_sections), is_periodic),
                    "subtitle": f"volume.orbital — {worst_status}",
                    "status": worst_status,
                    "supported": supported,
                    "icon": _status_icon(worst_status),
                    "kind_icon": _kind_icon("volume.orbital"),
                    "warn": worst_status == "error" or not supported,
                    "disabled": not supported,
                }
            )
        if basis_ao_sections:
            supported = all(s["supported"] for s in basis_ao_sections)
            worst_status = (
                "error"
                if any(s["status"] == "error" for s in basis_ao_sections)
                else basis_ao_sections[0]["status"]
            )
            sidebar_entries.append(
                {
                    "id": _BASIS_AO_GROUP_ID,
                    "title": f"Basis Functions ({len(basis_ao_sections)})",
                    "subtitle": f"basis.ao — {worst_status}",
                    "status": worst_status,
                    "supported": supported,
                    "icon": _status_icon(worst_status),
                    "kind_icon": _kind_icon("basis.ao"),
                    "warn": worst_status == "error" or not supported,
                    "disabled": not supported,
                }
            )
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
        _prepare_scene_actor_replacement(plotter, server.state)
        plotter.clear()
        plotter.set_background("#1a1a2e")

        # Re-apply SSAO after plotter.clear() (VTK 9.2+) — but only if it
        # is enabled: this used to re-apply unconditionally, so switching
        # SSAO off lasted exactly until the next scene rebuild.
        if server.state.ssao_enabled:
            try:
                from vibeview.material_presets import ambient_occlusion_pass

                ambient_occlusion_pass(plotter)
            except Exception:
                pass
        _reapply_raytrace(plotter, server.state)  # keep the raytrace indicator truthful (L2)

        structure_renderer, is_periodic = _build_structure_scene(
            reader,
            plotter,
            server.state,
            replication=tuple(replication),
            show_labels=bool(server.state.show_atom_labels),
            representation=str(getattr(server.state, "representation_style", "ball_and_stick")),
            cartoon_color_mode=str(
                getattr(server.state, "cartoon_color_mode", "chain")
            ),
            residue_selection=str(
                getattr(server.state, "residue_selection", "") or ""
            ),
        )

        # QA validation: check SCF convergence, energy sanity, etc.
        qa_warnings = _qa_validate(reader)
        if qa_warnings:
            server.state.qa_warnings = qa_warnings
            if not server.state.status_message:
                server.state.status_message = qa_warnings[0]

        plotter.show_grid()
        _apply_scene_appearance(plotter, server.state)
        if saved_camera is not None:
            plotter.camera_position = saved_camera
        else:
            plotter.view_isometric()
            # Apply the camera BEFORE pushing so the new file's bookmark
            # reaches the client (previously the push happened first, so the
            # camera was set server-side but never sent — A1-04).
            _apply_camera(plotter, viewer_state.camera)
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

        _all_readers.append(new_reader)
        server.state.file_names = [_job_name(r) for r in _all_readers]
        server.state.active_file_idx = len(_all_readers) - 1
        _reload_active_file()
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

        _all_readers.append(new_reader)
        server.state.file_names = [_job_name(r) for r in _all_readers]
        server.state.active_file_idx = len(_all_readers) - 1
        _reload_active_file()
        n_atoms = len(new_reader.read_structure().atoms)
        server.state.builder_smiles = ""
        server.state.builder_dialog = False
        server.state.live_opt_enabled = True
        _live_opt_schedule()
        server.state.status_message = (
            f"Built {smiles} — {n_atoms} atoms (ETKDG start; relaxing with vibe-qc)"
        )

    @ctrl.set("switch_file")
    def switch_file(file_idx: int) -> None:
        """Switch the active file when the user picks from the Files dropdown."""
        # Robust to either an integer index or a filename string.
        try:
            idx = int(file_idx)
        except (TypeError, ValueError):
            names = [_job_name(r) for r in _all_readers]
            idx = names.index(file_idx) if file_idx in names else -1
        if idx < 0 or idx >= len(_all_readers):
            return
        server.state.active_file_idx = idx
        # Re-pick the representation for the incoming file: a molecule
        # opens ball-and-stick, a biomolecule as a ribbon. Only on an
        # explicit file switch — the watcher's hot-reload path keeps
        # whatever the user selected.
        server.state.representation_style = _default_representation(_all_readers[idx])
        _reload_active_file()
        server.state.status_message = (
            f"Switched to {_job_name(reader)} (file {idx + 1}/{len(_all_readers)})"
        )

    @ctrl.set("toggle_compare_mode")
    def toggle_compare_mode(on=None) -> None:
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
            for prefix in ("volume_", "vib_atom_", "traj_atom_", "mo_iso_", "fermi_"):
                _remove_actors_by_prefix(plotter, prefix)
            state.structure_hidden = True
            state.compare_legend = _render_compare_overlay(
                plotter,
                _all_readers,
                align=state.compare_align,
                highlight=state.compare_highlight,
            )
            plotter.view_isometric()
            plotter.reset_camera()
            state.status_message = f"Compare mode: {len(state.compare_legend)} structures overlaid"
        else:
            _remove_actors_by_prefix(plotter, "cmp_")
            state.compare_legend = []
            # Keep the hidden flag set until the restore helper consumes it.
            # Clearing it first makes the helper return immediately, leaving
            # compare mode's static-scene removal permanent.
            _ensure_static_structure_shown()
            state.status_message = "Compare mode off"
        plotter.render()
        _push_view(plotter)

    @ctrl.set("toggle_compare_align")
    def toggle_compare_align(on=None) -> None:
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
    def set_compare_highlight(idx) -> None:
        """Highlight a specific file in compare mode, dimming the rest."""
        if isinstance(idx, (list, tuple)):
            idx = idx[0] if idx else None
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
            # Auto-detect format from filename extension.
            from vibeview.converters import detect_format

            fmt = detect_format(fname)
            if fmt is not None and fmt != "qvf":
                from vibeview.converters import (
                    cif_to_qvf,
                    cube_to_qvf,
                    gjf_to_qvf,
                    gro_to_qvf,
                    mol2_to_qvf,
                    pdb_to_qvf,
                    sdf_to_qvf,
                    trexio_to_qvf,
                    xyz_to_qvf,
                )

                _CONV = {
                    "xyz": xyz_to_qvf,
                    "cif": cif_to_qvf,
                    "cube": cube_to_qvf,
                    "pdb": pdb_to_qvf,
                    "mol2": mol2_to_qvf,
                    "gjf": gjf_to_qvf,
                    "gro": gro_to_qvf,
                    "sdf": sdf_to_qvf,
                    "trexio": trexio_to_qvf,
                }
                conv = _CONV.get(fmt)
                if conv is None:
                    server.state.status_message = f"Unsupported format: {fname}"
                    return
                qvf_buf = conv(raw)
                new_reader = QVFReader(qvf_buf)
            else:
                new_reader = QVFReader(io.BytesIO(raw))
        except (QVFError, ValueError) as e:
            server.state.status_message = f"Error opening {fname}: {e}"
            return

        _all_readers.append(new_reader)
        server.state.active_file_idx = len(_all_readers) - 1
        server.state.file_names = [_job_name(r) for r in _all_readers]
        _reload_active_file()
        server.state.status_message = (
            f"Loaded {fname} (file {len(_all_readers)}/{len(_all_readers)})"
        )

    @ctrl.set("close_load_dialog")
    def close_load_dialog() -> None:
        server.state.load_file_dialog = False
        server.state.file_picker_value = None  # reset so same file can be re-selected

    @ctrl.set("file_picker_value")
    def on_file_picked(files) -> None:
        """Handle browser file-picker: Trame's VFileInput sends
        [{name, size, content}] with content as base64 bytes."""
        import logging

        _log = logging.getLogger("vibeview")
        _log.info("file_picker_value=%s", repr(files)[:200] if files is not None else "None")
        if not files:
            return
        # Trame may send a single dict or a list of dicts.
        if isinstance(files, dict):
            file_list = [files]
        elif isinstance(files, (list, tuple)):
            file_list = list(files)
        else:
            server.state.status_message = f"Unexpected file-picker data: {type(files).__name__}"
            return
        if len(file_list) == 0:
            return
        f = file_list[0]
        if not isinstance(f, dict):
            server.state.status_message = f"Unexpected file object: {type(f).__name__}"
            return
        content = f.get("content", "")
        if not content:
            server.state.status_message = "File picker: no content received"
            return
        server.state.load_file_bytes = str(content)
        server.state.load_file_name = str(f.get("name", "uploaded.qvf"))
        load_file_from_bytes()

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
        server.state.file_names = [_job_name(r) for r in _all_readers]
        server.state.active_file_idx = len(_all_readers) - 1
        _reload_active_file()
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

    def _vq_elapsed(spec) -> str:
        """Human elapsed time for a job: running → since start; terminal →
        start→finish; else blank. Robust to missing/one-off timestamps."""
        from datetime import datetime, timezone

        def _parse(ts):
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
            end = datetime.now(timezone.utc)
        secs = max(0, int((end - start).total_seconds()))
        if secs < 60:
            return f"{secs}s"
        if secs < 3600:
            return f"{secs // 60}m {secs % 60}s"
        return f"{secs // 3600}h {(secs % 3600) // 60}m"

    def _vq_job_dict(spec) -> dict:
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

    def _vq_collect_jobs(all_states: bool):
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
        return [_vq_job_dict(s) for s in specs[:100]], None

    @ctrl.set("list_vq_jobs")
    def list_vq_jobs() -> None:
        """Open the Job Manager panel and refresh it."""
        server.state.vq_panel_open = True
        vq_refresh_jobs()

    @ctrl.set("open_vq_job")
    def open_vq_job(job_id: str) -> None:
        """Fetch a vq job and open its .qvf files in the viewer."""
        from pathlib import Path

        server.state.vq_jobs_dialog = False
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
        server.state.active_file_idx = len(_all_readers) - 1
        server.state.file_names = [_job_name(r) for r in _all_readers]
        _reload_active_file()
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
            _all_readers.append(new_reader)
            state.active_file_idx = len(_all_readers) - 1
            state.file_names = [_job_name(r) for r in _all_readers]
            _reload_active_file()
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
                        sdata, params, Path(container_dir) / job_name
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
                try:
                    Path(input_path).unlink(missing_ok=True)
                except Exception:
                    pass

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

    @server.state.change("vq_monitor_active")
    def _on_vq_monitor_toggle(vq_monitor_active, **kwargs):
        nonlocal _vq_monitor_task
        if vq_monitor_active:

            async def _poll():
                # Poll until the switch is turned off or the panel closes.
                while server.state.vq_monitor_active and server.state.vq_panel_open:
                    await asyncio.sleep(5)
                    if not (server.state.vq_monitor_active and server.state.vq_panel_open):
                        break
                    vq_refresh_jobs()
                    server.state.flush()

            _vq_monitor_task = asyncio.ensure_future(_poll())
        elif _vq_monitor_task is not None:
            _vq_monitor_task.cancel()
            _vq_monitor_task = None

    def _ensure_static_structure_shown() -> None:
        """Rebuild the static structure if a previous animation hid it.

        The animation render paths (trajectory / vibrations / reaction.path)
        call ``_remove_static_structure`` and set ``structure_hidden`` so the
        frozen equilibrium geometry doesn't ghost behind the moving atoms
        (A5-03). When the user returns to any non-animation section we drop
        the stale animation actors and redraw the structure.
        """
        nonlocal structure_renderer, is_periodic
        if not server.state.structure_hidden:
            return
        for prefix in ("vib_atom_", "traj_atom_", "traj_cell_"):
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
                replication=tuple(viewer_state.replication),
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
        # ── Rebuild the active volume ──────────────────────────────────
        server.state.structure_hidden = False

    def _activate_section_impl(section_id: str) -> None:
        """Invalidate worker renders, then mutate the VTK scene exclusively."""
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
        push_generation = int(
            getattr(plotter, "_vibe_view_push_generation", 0) or 0
        )
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
        state.show_param_panel = section_id == "structure"
        if section_id == "structure" and state.is_periodic:
            state.calc_template = "periodic"
        # Remember whether we're switching away from atom_properties so we
        # can restore the CPK colour palette on the 3D atoms below.
        _was_atom_props = bool(state.atom_properties_active)
        _clear_output_panels(state)
        # Drop any 3D overlay actors from a previously activated section
        # when their per-section replay state is cleared. Without removing the
        # computed surface too, a 2D or different wavefunction section could
        # show stale MO geometry with no visibility control left to remove it.
        _remove_atom_charge_labels(plotter)
        _remove_actors_by_prefix(plotter, "qtaim_")
        _remove_actors_by_prefix(plotter, "mo_iso_")
        # A previously shown Fermi surface lives in reciprocal space; drop its
        # sheets + cell wireframe whenever we switch to anything else.
        _remove_actors_by_prefix(plotter, "fermi_")
        # A QVF-backed volume has its own active-volume lifecycle and may stay
        # beside a 2D panel. It must still be removed when entering a computed
        # wavefunction view, or an MO would render inside an uncontrolled old
        # density (reported 2026-07-19).
        if section.kind == "wavefunction.gto":
            _remove_actors_by_prefix(plotter, "volume_")
        # Navigating into a section exits compare/overlay mode (a distinct mode).
        if state.compare_mode:
            state.compare_mode = False
            state.compare_legend = []
            _remove_actors_by_prefix(plotter, "cmp_")
        # If we're leaving an animation, restore the static structure that
        # the trajectory/vibration/reaction path hid (A5-03). fermi_surface
        # replaces the real-space scene entirely, so it stays hidden there too.
        if section.kind not in ("trajectory", "reaction.path", "vibrations", "fermi_surface"):
            _ensure_static_structure_shown()

        if section.kind == "structure":
            # Clear everything that previous sections may have drawn on top
            # of the molecule (isosurfaces, vibration ghosts, MO surfaces,
            # trajectory frames, …) so the user sees the bare structure
            # again when they click the Structure entry.
            _remove_actors_by_prefix(plotter, "volume_")
            _remove_actors_by_prefix(plotter, "vib_atom_")
            _remove_actors_by_prefix(plotter, "traj_atom_")
            _remove_actors_by_prefix(plotter, "mo_iso_")
            # When switching away from atom_properties, the atom spheres
            # may still carry the charge-tinted red/blue palette instead
            # of the default CPK colours.  Rebuild the structure scene
            # to restore the correct colours (and atom labels if enabled).
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            plotter.render()
            _push_view(plotter)
            state.status_message = "Structure"
        elif section.kind.startswith("volume.") or section.kind == "basis.ao":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_volume(reader, plotter, viewer_state, state, section)
        elif section.kind == "fermi_surface":
            _activate_fermi(reader, plotter, viewer_state, state, section)
        elif section.kind == "bands":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_bands(reader, state, section)
        elif section.kind == "dos.total":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            # Render the combined bands+DOS figure when a companion bands
            # section exists, but keep ``selected_section`` on the DOS id the
            # user actually clicked — repointing it at the bands section
            # desynced the app's notion of the active section from the UI.
            bands_section = next((s for s in reader.sections if s.kind == "bands"), None)
            if bands_section is not None:
                _activate_bands(reader, state, bands_section)
            else:
                _activate_dos(reader, state, section)
        elif section.kind == "dos.projected":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_dos(reader, state, section)
        elif section.kind == "phonon_bands":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_phonon_bands(reader, state, section)
        elif section.kind == "phonon_dos":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_phonon_dos(reader, state, section)
        elif section.kind == "equation_of_state":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_eos(reader, state, section)
        elif section.kind in _plot_spectra_kinds():
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_spectra(reader, state, section, _all_readers)
        elif section.kind == "spectra.nmr":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_nmr(reader, state, section)
        elif section.kind == "spectra.epr":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_epr(reader, state, section)
        elif section.kind == "trajectory":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_trajectory(reader, state, section)
        elif section.kind == "reaction.path":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_reaction_path(reader, state, section)
        elif section.kind == "reaction.waypoints":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_reaction_waypoints(reader, state, section)
        elif section.kind == "scan.surface":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_scan_surface(reader, state, section)
        elif section.kind == "vibrations":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_vibrations(reader, state, section)
        elif section.kind == "atom_properties":
            # Hide structure index labels while charge labels are showing
            # — both render at the same positions and look cluttered.
            _remove_atom_index_labels(plotter)
            _activate_atom_properties(reader, plotter, state, section)
        elif section.kind == "wavefunction.gto":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_wavefunction(reader, state, section)
        elif section.kind == "citations":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_citations(reader, state, section)
        elif section.kind == "run.record":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_run_record(reader, state, section)
        elif section.kind == "job.spec":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_job_spec(reader, state, section)
        elif section.kind == "scf_history":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_scf_history(reader, state, section)
        elif section.kind in ("dos.coop", "dos.cohp"):
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_dos_coop(reader, state, section)
        elif section.kind == "topology.qtaim":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_topology_qtaim(reader, plotter, state, section)
        elif section.kind == "bond_orders":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_bond_orders(reader, state, section)
        elif section.kind == "structure.symmetry":
            if _was_atom_props:
                _restore_cpk_atoms(reader, plotter, state)
            _activate_symmetry(reader, state, section)
        elif _was_atom_props:
            # Any other section kind (bands, spectra, trajectory, …) —
            # still need to restore CPK palette when leaving atom_props.
            _restore_cpk_atoms(reader, plotter, state)

        # Cleanup at the top of every section dispatch mutates the server-side
        # scene. Sync it when the target activator did not already do so;
        # otherwise VtkLocalView can retain removed charge or computed-surface
        # actors until a later 3D action. Avoid a duplicate push for volume and
        # other mesh activators because serializing those scenes is expensive.
        if int(getattr(plotter, "_vibe_view_push_generation", 0) or 0) == (
            push_generation
        ):
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

    @ctrl.set("update_isovalue")
    def update_isovalue(isovalue: float) -> None:
        state = server.state
        state.isovalue = isovalue
        vol_id = state.active_volume_id
        if vol_id:
            hints = viewer_state.get_volume_hints(vol_id)
            hints.isovalue = isovalue
            _rebuild_volume(reader, plotter, viewer_state, state, vol_id)

    @ctrl.set("update_fermi_bands")
    def update_fermi_bands(selected) -> None:
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
        vol_id = state.active_volume_id
        if vol_id:
            hints = viewer_state.get_volume_hints(vol_id)
            hints.colormap = colormap
            _rebuild_volume(reader, plotter, viewer_state, state, vol_id)

    @ctrl.set("toggle_scf_skip_guess")
    def toggle_scf_skip_guess(value=None) -> None:
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
    def toggle_scf_log_energy(value=None) -> None:
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
        gamma_scale=None, normalize=None, x_unit=None, display=None
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

    @ctrl.set("download_run_attachment")
    def download_run_attachment(role=None) -> None:
        """Offer one run.record attachment as a browser download.

        Attachments are arbitrary bytes by spec, so they are never
        rendered or interpreted — this is the only path that touches
        their payload, and it hands the bytes straight to a download.
        The media type is forced to ``application/octet-stream`` so the
        browser saves rather than displays: serving an archive-supplied
        type could invite a text/html attachment to be rendered as a
        document.
        """
        import base64
        from datetime import datetime

        if isinstance(role, (list, tuple)):
            role = role[0] if role else None
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
        b64 = base64.b64encode(payload).decode()
        state.export_data = f"data:application/octet-stream;base64,{b64}"
        state.export_filename = entry["filename"]
        state.export_ready = True
        state.status_message = f"Downloading {entry['filename']}"
        state.download_history.insert(
            0,
            {
                "name": f"Attachment ({entry['filename']})",
                "path": str(reader.path) if reader.path else "",
                "format": "BYTES",
                "time": datetime.now().strftime("%H:%M:%S"),
            },
        )
        state.download_history = state.download_history[:50]
        asyncio.ensure_future(_reset_export_flag())

    @ctrl.set("export_spectra_csv")
    def export_spectra_csv() -> None:
        """Export the active spectrum's broadened envelope + peaks as CSV,
        offered as a browser download (design refresh 2026, spectra controls).
        """
        import base64
        from datetime import datetime

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
        b64 = base64.b64encode(csv.encode("utf-8")).decode()
        state.export_data = f"data:text/csv;base64,{b64}"
        stem = _job_name(reader) or "spectrum"
        state.export_filename = f"{stem}-{section.id}.csv"
        state.export_ready = True
        state.status_message = f"Exported {state.export_filename}"
        state.download_history.insert(
            0,
            {
                "name": f"Spectrum CSV ({section.id})",
                "path": str(reader.path) if reader.path else "",
                "format": "CSV",
                "time": datetime.now().strftime("%H:%M:%S"),
            },
        )
        state.download_history = state.download_history[:50]
        asyncio.ensure_future(_reset_export_flag())

    @ctrl.set("toggle_spectra_compare")
    def toggle_spectra_compare(value=None) -> None:
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
    def toggle_color_by_esp(on=None) -> None:
        """Enable/disable ESP colour-mapping on the density isosurface."""
        state = server.state
        state.color_by_esp = (not state.color_by_esp) if on is None else bool(on)
        if state.color_by_esp:
            state.colormap = "RdBu"
        vol_id = state.active_volume_id
        if vol_id and state.volume_loaded:
            _rebuild_volume(reader, plotter, viewer_state, state, vol_id)

    @ctrl.set("toggle_reduce_detail")
    def toggle_reduce_detail(on=None) -> None:
        """Toggle LOD subsampling for large volume grids."""
        state = server.state
        state.reduce_detail = (not state.reduce_detail) if on is None else bool(on)
        vol_id = state.active_volume_id
        if vol_id and state.volume_loaded:
            viewer_state.invalidate_mesh_cache(vol_id)
            _rebuild_volume(reader, plotter, viewer_state, state, vol_id)

    @ctrl.set("add_extra_isosurface")
    def add_extra_isosurface() -> None:
        """Add a secondary isosurface layer at half the current isovalue."""
        state = server.state
        iso = float(state.isovalue or 0.05)
        extras = list(state.extra_isosurfaces or [])
        palette = ["#66aa55", "#cc8833", "#aa66cc"]
        colour = palette[len(extras) % len(palette)]
        extras.append({"isovalue": iso * 0.5, "opacity": 0.35, "colour": colour})
        state.extra_isosurfaces = extras
        vol_id = state.active_volume_id
        if vol_id and state.volume_loaded:
            _rebuild_volume(reader, plotter, viewer_state, state, vol_id)

    @ctrl.set("remove_extra_isosurface")
    def remove_extra_isosurface(index: int) -> None:
        """Remove an extra isosurface layer by index."""
        state = server.state
        extras = list(state.extra_isosurfaces or [])
        if 0 <= index < len(extras):
            extras.pop(index)
            state.extra_isosurfaces = extras
        vol_id = state.active_volume_id
        if vol_id and state.volume_loaded:
            _rebuild_volume(reader, plotter, viewer_state, state, vol_id)

    @ctrl.set("update_opacity")
    def update_opacity(opacity: float) -> None:
        state = server.state
        state.opacity = opacity
        vol_id = state.active_volume_id
        if vol_id:
            hints = viewer_state.get_volume_hints(vol_id)
            hints.opacity = opacity
            _rebuild_volume(reader, plotter, viewer_state, state, vol_id)
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
        for _name in ("mo_iso_pos", "mo_iso_neg"):
            _actor = plotter.actors.get(_name)
            _prop = _actor.GetProperty() if _actor is not None else None
            if _prop is not None:
                _prop.SetOpacity(float(opacity))
                retinted = True
        if retinted:
            plotter.render()
            _push_view(plotter)

    @ctrl.set("set_periodic_replication")
    def set_periodic_replication(value) -> None:
        """Set grid-level periodic orbital replication.

        When > 0 the volume grid is tiled N×N×N into a (2N+1)³
        supercell *before* marching cubes, so orbital isosurfaces
        repeat seamlessly across adjacent unit cells.
        """
        state = server.state
        state.periodic_replication = int(value or 0)
        vol_id = state.active_volume_id
        if vol_id and state.volume_loaded:
            _rebuild_volume(reader, plotter, viewer_state, state, vol_id)
        state.status_message = f"Cell replication: {state.periodic_replication}"

    @ctrl.set("toggle_wrap_periodic")
    def toggle_wrap_periodic(on=None) -> None:
        """Toggle minimum-image recentre of the active periodic orbital.

        Rolls a face-straddling orbital / Wannier function whole into the
        cell. The mesh cache is keyed on (isovalue, replication), not the
        wrap flag, so invalidate it before rebuilding.
        """
        state = server.state
        state.wrap_periodic_orbital = (
            (not state.wrap_periodic_orbital) if on is None else bool(on)
        )
        vol_id = state.active_volume_id
        if vol_id and state.volume_loaded:
            viewer_state.invalidate_mesh_cache(vol_id)
            _rebuild_volume(reader, plotter, viewer_state, state, vol_id)
            _push_view(plotter)
        state.status_message = (
            "Wrap to cell centre: on" if state.wrap_periodic_orbital else "Wrap to cell centre: off"
        )

    @ctrl.set("toggle_wannier_centers")
    def toggle_wannier_centers(on=None) -> None:
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
        state.replication_nx = nx
        state.replication_ny = ny
        state.replication_nz = nz
        viewer_state.replication = (nx, ny, nz)
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
        try:
            ctrl.view_reset_camera()
        except Exception:
            pass

    @ctrl.set("toggle_atom_labels")
    def toggle_atom_labels(show=None) -> None:
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
        _rebuild_scene(reader, plotter, viewer_state, server.state)

    @ctrl.set("set_cartoon_color_mode")
    def set_cartoon_color_mode(mode: str) -> None:
        """Colour the cartoon ribbon by chain, secondary structure,
        residue type, or b-factor."""
        if isinstance(mode, (list, tuple)):
            mode = mode[0] if mode else None
        if mode not in ("chain", "structure", "residue", "bfactor"):
            return
        server.state.cartoon_color_mode = mode
        # Only the cartoon reads this; rebuilding for any other
        # representation would be a visible no-op costing a full scene.
        rebuild_error = None
        if server.state.representation_style == "cartoon":
            rebuild_error = _rebuild_scene(
                reader, plotter, viewer_state, server.state
            )
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
    def set_residue_selection(spec=None, *_ignored) -> None:
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
        if isinstance(spec, (list, tuple)):
            spec = spec[0] if spec else ""
        if spec is None or isinstance(spec, dict):
            # No argument, or a DOM event from the blur / Enter handler.
            spec = server.state.residue_selection or ""
        spec = str(spec)
        server.state.residue_selection = spec
        server.state.residue_selection_summary = _residue_selection_summary(
            reader, spec
        )

        # Every representation now draws the selection. Avoid a rebuild only
        # on structures that have no residue membership and therefore cannot
        # match any term.
        if server.state.residue_selection_available:
            _rebuild_scene(reader, plotter, viewer_state, server.state)

    @ctrl.set("set_element_color")
    def set_element_color(z=None, color=None) -> None:
        """Override the render colour of one element (design refresh 2026).

        Keyed by atomic number; ``set_color_overrides`` feeds cpk_color(), so
        every render path — glyph batching, per-atom actors, replicated cells,
        compare mode — picks the new colour up on the rebuild below.
        """
        from vibeview.renderers.structure import set_color_overrides

        state = server.state
        if isinstance(z, (list, tuple)):
            z = z[0] if z else None
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
        preset = _apply_scene_appearance(plotter, server.state)
        server.state.status_message = f"Material: {preset.name}"
        ctrl.view_update()

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
                try:
                    server.state.properties_html = renderer.render_to_html(
                        charge_kind=(
                            server.state.charge_kind
                            if server.state.charge_kind
                            else None
                        )
                    )
                except QVFError:
                    pass
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

    @ctrl.set("run_relocalize")
    def run_relocalize() -> None:
        """Ask vibe-qc for a localization criterion the file does not carry.

        The work happens in a subprocess (:mod:`vibeview.relocalize`) so
        vibe-qc is never imported into the viewer process. The result is
        registered as a wavefunction overlay under a synthetic
        ``wf_relocalized_*`` section, which then behaves like any archived
        one -- same renderer, same picker, same isosurface path.
        """
        import asyncio as _asyncio

        from vibeview.relocalize import (
            METHOD_LABELS,
            METHODS,
            overlay_section_id,
            probe_worker,
            request_from_reader,
            run_relocalization,
            wavefunction_from_result,
        )

        state = server.state
        if state.relocalize_running:
            return
        method = str(state.relocalize_method or "ibo")

        async def _work() -> None:
            state.relocalize_running = True
            try:
                if state.relocalize_available is None:
                    state.relocalize_status = "checking vibe-qc availability …"
                    state.flush()
                    probed = await probe_worker()
                    state.relocalize_available = bool(probed.get("available"))
                    offered = [m for m in probed.get("methods", []) if m in METHODS]
                    state.relocalize_method_options = [
                        {"title": METHOD_LABELS.get(m, m), "value": m}
                        for m in offered
                    ]
                    if not state.relocalize_available:
                        state.relocalize_status = (
                            "re-localization needs vibe-qc: "
                            f"{probed.get('reason', 'unavailable')}"
                        )
                        return

                request = request_from_reader(reader, method)
                if request is None:
                    state.relocalize_status = (
                        "this file does not record the basis-set name, so it "
                        "cannot be re-localized"
                    )
                    return

                state.relocalize_status = f"re-localizing ({method}) …"
                state.flush()
                result = await run_relocalization(request)
                if "error" in result:
                    state.relocalize_status = f"failed: {result['error']}"
                    return

                canonical = reader.read_wavefunction_gto("wf")
                section_id = overlay_section_id(method)
                reader.set_wavefunction_overlay(
                    section_id, wavefunction_from_result(result, canonical)
                )
                state.relocalize_status = (
                    f"{METHOD_LABELS.get(method, method)}: "
                    f"{result.get('n_occ', '?')} orbitals"
                )

                # Give the overlay a sidebar row so it is reachable like any
                # archived section. Appended rather than going through the
                # full rebuild in _reload_active_file, which re-reads the
                # file and would be a much larger hammer for one new row.
                entry = {
                    "id": section_id,
                    "title": _sidebar_section_title(
                        {"id": section_id, "kind": "wavefunction.gto"}
                    ),
                    "subtitle": "wavefunction.gto — computed in this session",
                    "status": "ok",
                    "supported": True,
                    "icon": _status_icon("ok"),
                    "kind_icon": _kind_icon("wavefunction.gto"),
                    "warn": False,
                    "disabled": False,
                }
                existing = next(
                    (i for i, e in enumerate(sidebar_entries)
                     if e.get("id") == section_id),
                    None,
                )
                if existing is None:
                    sidebar_entries.append(entry)
                else:
                    sidebar_entries[existing] = entry
                state.sidebar_entries = sidebar_entries
                # Closure-held list mutated in place: trame drops the push
                # when the assigned object *is* the pushed one, so an
                # explicit dirty() is required (same reason as section_list).
                state.dirty("sidebar_entries")

                _activate_wavefunction(reader, state, reader.get_section(section_id))
            except Exception as exc:  # keep a viewer failure out of the UI
                state.relocalize_status = f"failed: {type(exc).__name__}: {exc}"
            finally:
                state.relocalize_running = False
                state.flush()

        _asyncio.ensure_future(_work())

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

    @server.state.change("trajectory_playing")
    def _on_play_toggle(trajectory_playing, **kwargs):
        nonlocal _anim_task, _anim_epoch
        _anim_epoch += 1
        if trajectory_playing:
            my_epoch = _anim_epoch

            async def _loop():
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

            _anim_task = asyncio.ensure_future(_loop())
        else:
            if _anim_task is not None:
                _anim_task.cancel()
                _anim_task = None

    @ctrl.set("vibration_play_toggle")
    def vibration_play_toggle() -> None:
        server.state.vibration_playing = not server.state.vibration_playing

    _vib_anim_task = None
    _vib_epoch = 0  # bumped on every play/stop; stale iterations bail (audit L3)

    @server.state.change("vibration_playing")
    def _on_vibration_play_toggle(vibration_playing, **kwargs):
        nonlocal _vib_anim_task, _vib_epoch
        _vib_epoch += 1
        if vibration_playing:
            my_epoch = _vib_epoch

            async def _vib_loop():
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

            _vib_anim_task = asyncio.ensure_future(_vib_loop())
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
    def render_mo(mo_key, _expected_epoch: int | None = None) -> None:
        state = server.state
        section_id = state.wf_section_id
        if not section_id:
            if _expected_epoch is None:
                state.status_message = "No wavefunction section active."
            return
        source_reader = reader
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
                        _remove_actors_by_prefix(plotter, "mo_iso_")
                return

            # The section/file may have changed while the grid was evaluated.
            # Helpers create server actors but no longer push them, so a stale
            # render can be removed here without ever reaching the client.
            with _wf_epoch_lock:
                if not _wavefunction_render_is_current(
                    render_epoch, section_id, source_reader
                ):
                    _remove_actors_by_prefix(plotter, "mo_iso_")
                    return
                _finalize_wavefunction_surface_appearance(plotter, state)
                # Store the recipe only at the same guarded commit boundary as
                # the final client push.
                state.wf_selected_spin = spin
                state.mo_last_spin = spin
                state.mo_last_index = index
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
                _remove_actors_by_prefix(plotter, "mo_iso_")
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
                        _remove_actors_by_prefix(plotter, "mo_iso_")
                        return
                    if restored:
                        _finalize_wavefunction_surface_appearance(plotter, state)
                        state.mo_visible = True
                        state.volume_loaded = True
                    else:
                        _fail_wavefunction_surface(plotter, state, message)

    @ctrl.set("render_frontier")
    def render_frontier(which) -> None:
        """One-click HOMO/LUMO render: pick the marked row and render it."""
        if isinstance(which, (list, tuple)):
            which = which[0]
        state = server.state
        marker = str(which).upper()
        row = next(
            (r for r in (state.wf_mo_rows or []) if marker in (r.get("title") or "")),
            None,
        )
        if row is None:
            state.status_message = f"No {marker} in this wavefunction's MO list"
            return
        state.wf_selected_mo = row["value"]
        render_mo(row["value"])

    @server.state.change("mo_click_request")
    def _on_mo_click_request(mo_click_request=None, **_kwargs) -> None:
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
                        _remove_actors_by_prefix(plotter, "mo_iso_")
                        return
                    if restored:
                        if not has_surface_actor and state.mo_visible:
                            _finalize_wavefunction_surface_appearance(plotter, state)
                        state.status_message = "MO animation stopped"
                    else:
                        _fail_wavefunction_surface(plotter, state, message)
            return

        epoch = _invalidate_wavefunction_renders()
        with _wf_epoch_lock:
            state.wf_animating = True
        state.status_message = "MO animation started"
        _run_mo_animation_step(epoch)

    def _run_mo_animation_step(epoch: int) -> None:
        """Render one guarded animation frame, then schedule its successor."""
        with _wf_epoch_lock:
            if epoch != _wf_render_epoch or not server.state.wf_animating:
                return
            # Keep invalidation out of the gap between checking the epoch and
            # advancing the picker. Otherwise an old timer can step the newly
            # activated wavefunction even though its render is rejected.
            mo_key = _step_mo_selection(server.state, 1)
        if mo_key is None:
            with _wf_epoch_lock:
                if epoch == _wf_render_epoch:
                    server.state.wf_animating = False
                    server.state.status_message = "MO animation stopped: no orbitals"
            return
        render_mo(mo_key, epoch)
        _schedule_mo_step(epoch)

    def _schedule_mo_step(epoch: int) -> None:
        """Own one cancellable timer for the next MO animation frame."""
        nonlocal _wf_anim_timer
        state = server.state
        with _wf_epoch_lock:
            if epoch != _wf_render_epoch or not state.wf_animating:
                return
            timer = threading.Timer(
                state.wf_anim_speed,
                _run_mo_animation_step,
                args=(epoch,),
            )
            timer.daemon = True
            _wf_anim_timer = timer
        timer.start()

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
            state.chart_html = renderer.render_energy_diagram()
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
        render_surface,
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
                        _remove_actors_by_prefix(plotter, "mo_iso_")
                return

            # Section/file switches invalidate immediately while waiting for
            # the render lock. Keep stale helper-created actors server-side
            # only, then remove them without ever pushing to the client.
            with _wf_epoch_lock:
                if not _wavefunction_render_is_current(
                    render_epoch, sid, source_reader
                ):
                    _remove_actors_by_prefix(plotter, "mo_iso_")
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
    def sync_client_camera(position=None, focal_point=None, view_up=None,
                           parallel_scale=None) -> None:
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
                section_id, _section = _prepare_saved_view_restore(bm)
                if section_id:
                    state.selected_section = section_id
                    activate_section(section_id)
                # Section activation may rebuild the scene or reset its
                # camera, so the saved pose must be the final render change.
                if _apply_saved_view_camera(bm):
                    _push_view(plotter)
                state.status_message = f"Restored: {name}"
                return
        state.status_message = f"Unknown bookmark: {name}"

    _presentation_auto_task: asyncio.Task | None = None
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

    def _saved_view_setting(saved_view: dict, name: str):
        """Read a saved visual setting, including the legacy slide shape."""
        if name in saved_view:
            return saved_view[name]
        legacy_state = saved_view.get("state")
        if isinstance(legacy_state, dict):
            return legacy_state.get(name)
        return None

    def _prepare_saved_view_restore(saved_view: dict):
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
            candidate = str(saved_colormap)
            with contextlib.suppress(ImportError, TypeError, ValueError):
                get_cmap_safe(candidate)
                restored_colormap = candidate
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

    def _apply_saved_view_camera(saved_view: dict) -> bool:
        """Apply a saved camera after any section rebuild; report success."""
        camera = saved_view.get("camera")
        if not camera:
            return False
        try:
            _apply_camera(plotter, camera)
            plotter.render()
        except Exception:  # noqa: BLE001 - malformed saved views stay recoverable
            return False
        return True

    def _apply_slide(slide: dict) -> None:
        """Apply a presentation slide: restore camera and section selection."""
        state = server.state
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

    def _session_payload() -> dict:
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
        }

    def _saved_session_replication(session: dict) -> tuple[int, int, int] | None:
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

    def _restore_session_replication(session: dict) -> None:
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

    @ctrl.set("save_session")
    def save_session(path: str | None = None) -> None:
        import json as _json
        from pathlib import Path as _Path  # not in scope here; NameError without it

        state = server.state
        # state.session_path is a str, so the old `Path(path) if path else
        # state.session_path` left sp a plain string on the no-argument call
        # (the Save Session button) and sp.write_text() below would fail.
        sp = _Path(path or state.session_path or "session.vibe-session")
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
        sp = _Path(path)
        if not sp.exists():
            state.status_message = f"Not found: {path}"
            return
        try:
            session = _json.loads(sp.read_text())
        except Exception as e:
            state.status_message = f"Load error: {e}"
            return
        if session.get("version") != 1:
            state.status_message = "Bad version"
            return
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
                plotter.set_background("white")
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
                img = img.convert("RGBA")
                data = img.getdata()
                new_data = []
                for item in data:
                    if item[0] > 250 and item[1] > 250 and item[2] > 250:
                        new_data.append((255, 255, 255, 0))
                    else:
                        new_data.append(item)
                img.putdata(new_data)  # type: ignore[arg-type]
        finally:
            if transparent:
                plotter.set_background(orig_bg)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        png_bytes = buf.getvalue()
        b64 = base64.b64encode(png_bytes).decode()
        server.state.screenshot_data = f"data:image/png;base64,{b64}"
        server.state.screenshot_ready = True

        _save_screenshot_disk(png_bytes, reader)

        server.state.status_message = f"Screenshot captured ({target_w}\u00d7{target_h}, SSAA 2x)"
        # Track in download history
        from datetime import datetime

        server.state.download_history.insert(
            0,
            {
                "name": "Screenshot",
                "path": str(reader.path) if reader.path else "",
                "format": "PNG",
                "time": datetime.now().strftime("%H:%M:%S"),
            },
        )
        server.state.download_history = server.state.download_history[:50]
        # Reset ready flag after a short delay so next click works
        asyncio.ensure_future(_reset_screenshot_flag())

    @ctrl.set("apply_view_preset")
    def apply_view_preset(name) -> None:
        """One-click view presets (design refresh 2026, items 3+6): each sets
        representation, material (incl. background), SSAO, and atom labels
        together. Order matters — the label/representation rebuilds recreate
        the actors, so the material is applied after the last rebuild and the
        SSAO pass is (re)configured last.
        """
        if isinstance(name, (list, tuple)):
            name = name[0] if name else None
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
        server.state.view_preset = name
        set_representation_style(rep)
        toggle_atom_labels(labels)
        set_material_preset(mat)
        toggle_ssao(ssao)
        server.state.status_message = f"View preset: {name}"

    @ctrl.set("run_palette_action")
    def run_palette_action(action_id) -> None:
        """Dispatch a command-palette action to its controller, then close
        the palette (design refresh 2026). Keep the ids in sync with
        _PALETTE_ACTIONS.
        """
        if isinstance(action_id, (list, tuple)):
            action_id = action_id[0] if action_id else None
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

            # Unsaved editor geometry lives only on the active raw reader.
            # Otherwise retain the lazy path for archive-backed detection.
            _r = (
                reader
                if getattr(reader, "has_edit_overlay", False)
                else (lazy_reader if lazy_reader is not None else reader)
            )
            pg = detect_from_qvf(_r)
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

    # ── Controller: geometry export ───────────────────────────────────
    @ctrl.set("export_geometry")
    def export_geometry(fmt) -> None:
        """Export the current structure geometry to a file.

        Receives a plain string from the client
        (``'obj'``, ``'gltf'``, ``'xyz'``).
        Also handles list-wrapped values for robustness.
        """
        if isinstance(fmt, (list, tuple)):
            fmt = fmt[0]
        import tempfile
        from pathlib import Path

        # XYZ is a text format — build from structure data directly.
        if fmt == "xyz":
            try:
                sdata = reader.read_structure()
                lines = [str(len(sdata.atoms)), "vibe-view export"]
                for a in sdata.atoms:
                    x, y, z = a.position
                    lines.append(f"{a.symbol:<2} {x:12.6f} {y:12.6f} {z:12.6f}")
                xyz_text = "\n".join(lines) + "\n"
                export_bytes = xyz_text.encode()
            except Exception as e:
                server.state.status_message = f"XYZ export error: {e}"
                return
            ext = ".xyz"
            mime = "chemical/x-xyz"
        elif fmt == "cif":
            try:
                sdata = reader.read_structure()
                lines = ["data_vibe-view", "# CIF exported by vibe-view"]
                if sdata.lattice_vectors is not None and any(sdata.pbc):
                    lat = sdata.lattice_vectors
                    a = float(np.linalg.norm(lat[0]))
                    b = float(np.linalg.norm(lat[1]))
                    c = float(np.linalg.norm(lat[2]))
                    al = np.degrees(np.arccos(np.dot(lat[1], lat[2]) / (b * c)))
                    be = np.degrees(np.arccos(np.dot(lat[0], lat[2]) / (a * c)))
                    ga = np.degrees(np.arccos(np.dot(lat[0], lat[1]) / (a * b)))
                    lines.append(f"_cell_length_a {a:.4f}")
                    lines.append(f"_cell_length_b {b:.4f}")
                    lines.append(f"_cell_length_c {c:.4f}")
                    lines.append(f"_cell_angle_alpha {al:.4f}")
                    lines.append(f"_cell_angle_beta {be:.4f}")
                    lines.append(f"_cell_angle_gamma {ga:.4f}")
                    # Fractional coordinates for periodic systems.
                    inv_lat = np.linalg.inv(lat)
                    lines.append("loop_")
                    lines.append("_atom_site_type_symbol")
                    lines.append("_atom_site_fract_x")
                    lines.append("_atom_site_fract_y")
                    lines.append("_atom_site_fract_z")
                    for a in sdata.atoms:
                        frac = np.array(a.position) @ inv_lat
                        lines.append(
                            f"{a.symbol:<2} {frac[0]:10.6f} {frac[1]:10.6f} {frac[2]:10.6f}"
                        )
                else:
                    lines.append("loop_")
                    lines.append("_atom_site_type_symbol")
                    lines.append("_atom_site_fract_x")
                    lines.append("_atom_site_fract_y")
                    lines.append("_atom_site_fract_z")
                    for a in sdata.atoms:
                        x, y, z = a.position
                        lines.append(f"{a.symbol:<2} {x:10.6f} {y:10.6f} {z:10.6f}")
                cif_text = "\n".join(lines) + "\n"
                export_bytes = cif_text.encode()
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
            ext = {"obj": ".obj", "gltf": ".gltf", "stl": ".stl"}.get(fmt, ".obj")
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                tmp = f.name
            try:
                # save_graphic only writes vector formats (svg/eps/pdf/ps/tex) —
                # use the dedicated scene exporters for obj/gltf. export_obj
                # writes <stem>.obj (+ .mtl), so strip the suffix for it.
                if fmt == "gltf":
                    plotter.export_gltf(tmp)
                else:  # obj
                    plotter.export_obj(tmp)
                with open(tmp, "rb") as f:
                    export_bytes = f.read()
                mime = {"obj": "model/obj", "gltf": "model/gltf+json", "stl": "model/stl"}[fmt]
            finally:
                Path(tmp).unlink(missing_ok=True)

        b64 = base64.b64encode(export_bytes).decode()
        server.state.export_data = f"data:{mime};base64,{b64}"
        server.state.export_filename = f"vibe-view-structure{ext}"
        server.state.export_ready = True

        # Also write to disk next to the QVF file.
        _save_export_disk(export_bytes, reader, fmt)

        server.state.status_message = f"Exported as {fmt.upper()}"
        # Track in download history
        from datetime import datetime

        server.state.download_history.insert(
            0,
            {
                "name": f"Export ({fmt.upper()})",
                "path": str(reader.path) if reader.path else "",
                "format": fmt.upper(),
                "time": datetime.now().strftime("%H:%M:%S"),
            },
        )
        server.state.download_history = server.state.download_history[:50]
        asyncio.ensure_future(_reset_export_flag())

    async def _reset_export_flag() -> None:
        await asyncio.sleep(0.5)
        server.state.export_ready = False

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

        state.video_export_running = True
        state.video_export_dialog = False

        stem = Path(reader.path).stem if reader.path else "vibe-view"
        ext = fmt if fmt != "frames" else ""
        out = Path(f"{stem}_{kind}.{ext}") if ext else Path(f"{stem}_{kind}")

        def _render():
            from vibeview.animation import (
                render_orbital_animation,
                render_trajectory_video,
                render_turntable,
                render_vibration_video,
            )

            if kind == "trajectory":
                return render_trajectory_video(reader, out, format=fmt, fps=fps)
            if kind == "vibration":
                return render_vibration_video(reader, out, format=fmt, fps=fps)
            if kind == "orbital":
                return render_orbital_animation(reader, out, format=fmt, fps=fps)
            if kind == "turntable":
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

        asyncio.ensure_future(_run_and_finalise())

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

        # We'll write to a temp file, then read it back for download.
        tmp_path = Path(tempfile.mkdtemp()) / "vibe-view-hq-render.png"

        def _progress_callback(fraction: float, message: str) -> None:
            """Called from the worker thread."""
            server.state.hq_render_progress = fraction
            server.state.hq_render_progress_msg = message
            # Trame flushes state changes to the client on the next
            # event-loop tick — the async polling below will pick them up.

        def _render_worker() -> str | None:
            """Run the render in a background thread.  Returns None on
            success, or an error message string."""
            try:
                render_high_quality(
                    plotter,
                    str(tmp_path),
                    quality=quality,
                    resolution=(width, height),
                    environment=environment,
                    material_style=material,
                    dof_enabled=bool(server.state.hq_render_dof),
                    progress_callback=_progress_callback,
                )
                return None
            except Exception as exc:
                return str(exc)

        async def _run_and_finalise() -> None:
            loop = asyncio.get_running_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                error = await loop.run_in_executor(pool, _render_worker)

            if error:
                server.state.hq_render_running = False
                server.state.hq_render_progress_msg = f"Render failed: {error}"
                server.state.status_message = f"HQ render error: {error}"
                return

            save_disk = bool(server.state.hq_render_save_disk)
            try:
                png_bytes = tmp_path.read_bytes()
            except OSError as exc:
                server.state.hq_render_running = False
                server.state.hq_render_progress_msg = f"Read error: {exc}"
                server.state.status_message = f"HQ render read error: {exc}"
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
                asyncio.ensure_future(_reset_screenshot_flag())

        asyncio.ensure_future(_run_and_finalise())

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
        atoms = [
            {
                "atomic_number": int(a.atomic_number),
                "position": [float(c) for c in a.position],
            }
            for a in sdata.atoms
        ]
        n = len(atoms)
        symbols = [
            "H",
            "He",
            "Li",
            "Be",
            "B",
            "C",
            "N",
            "O",
            "F",
            "Ne",
            "Na",
            "Mg",
            "Al",
            "Si",
            "P",
            "S",
            "Cl",
            "Ar",
        ]
        cov_r = {
            1: 0.31,
            5: 0.84,
            6: 0.76,
            7: 0.71,
            8: 0.66,
            9: 0.57,
            14: 1.11,
            15: 1.07,
            16: 1.05,
            17: 1.02,
        }
        # First pass: find all bonded pairs.
        bonds: list[tuple[int, int, float]] = []
        for i in range(n):
            zi = atoms[i].get("atomic_number", 0)
            pi = atoms[i].get("position", [0, 0, 0])
            ri = cov_r.get(zi, 1.0)
            for j in range(i + 1, n):
                zj = atoms[j].get("atomic_number", 0)
                pj = atoms[j].get("position", [0, 0, 0])
                rj = cov_r.get(zj, 1.0)
                d = math.sqrt(sum((pi[k] - pj[k]) ** 2 for k in range(3)))
                if d < 1.3 * (ri + rj):
                    bonds.append((i, j, d))

        # Build neighbor lists from the bond table.
        neighbors: dict[int, list[tuple[int, float]]] = {i: [] for i in range(n)}
        for i, j, d, *_image in bonds:
            neighbors[i].append((j, d))
            neighbors[j].append((i, d))

        def _sym(idx: int) -> str:
            z = atoms[idx].get("atomic_number", 0)
            s = symbols[z - 1] if 1 <= z <= len(symbols) else str(z)
            return f"{s}{idx + 1}"

        rows: list[str] = []
        # Distances.
        if bonds:
            rows.append("── Bond Distances ──")
            for i, j, d, *_image in sorted(bonds, key=lambda x: x[2]):
                rows.append(f"  {_sym(i)}–{_sym(j)}: {d:.3f} Å")

        # Angles: for every atom with ≥2 neighbors, show all angles.
        angles: list[str] = []
        for center in range(n):
            nbrs = neighbors[center]
            if len(nbrs) < 2:
                continue
            pc = atoms[center].get("position", [0, 0, 0])
            for a in range(len(nbrs)):
                for b in range(a + 1, len(nbrs)):
                    ja, _ = nbrs[a]
                    jb, _ = nbrs[b]
                    pa = atoms[ja].get("position", [0, 0, 0])
                    pb = atoms[jb].get("position", [0, 0, 0])
                    v1 = [pa[k] - pc[k] for k in range(3)]
                    v2 = [pb[k] - pc[k] for k in range(3)]
                    dot = sum(v1[k] * v2[k] for k in range(3))
                    n1 = math.sqrt(sum(v1[k] ** 2 for k in range(3)))
                    n2 = math.sqrt(sum(v2[k] ** 2 for k in range(3)))
                    angle_deg = math.degrees(math.acos(max(-1.0, min(1.0, dot / (n1 * n2)))))
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
    def toggle_measure_mode(on=None) -> None:
        state = server.state
        state.measure_mode = (not state.measure_mode) if on is None else bool(on)
        state.selected_atoms = []
        _remove_actors_by_prefix(plotter, "pick_marker_")
        if state.measure_mode:
            state.measure_result = _MEASURE_HINT
        else:
            state.measure_result = ""
        _push_view(plotter)

    @ctrl.set("clear_picks")
    def clear_picks() -> None:
        server.state.selected_atoms = []
        _remove_actors_by_prefix(plotter, "pick_marker_")
        server.state.measure_result = (
            _MEASURE_HINT if server.state.measure_mode else ""
        )
        _push_view(plotter)

    @ctrl.set("on_pick")
    def on_pick(event=None) -> None:
        """Handle viewport clicks: measure mode (distance/angle/dihedral)
        or edit mode (atom selection)."""
        state = server.state
        if not isinstance(event, dict):
            return
        if not state.measure_mode and not state.edit_mode:
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
                try:
                    server.state.edit_history.append(_structure_edit_snapshot(sdata))
                except Exception:
                    pass
                server.state.edit_future.clear()
                # In-place list mutations are invisible to trame's dirty
                # tracking (the pushed snapshot aliases the same list
                # object), so mark them or Undo/Redo stay disabled client-side.
                server.state.dirty("edit_history", "edit_future")
                _add_atom_to_structure(reader, plotter, wp_np, new_element)
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
            state.measure_result = _measure_text(sdata, picked)
            _push_view(plotter)

    # ── Controller: periodic-table element picker ────────────────────
    @ctrl.set("open_element_picker")
    def open_element_picker(target="new") -> None:
        """Open the periodic-table dialog for 'new' or 'change' picking."""
        if isinstance(target, (list, tuple)):
            target = target[0] if target else "new"
        server.state.element_picker_target = (
            "change" if str(target) == "change" else "new"
        )
        server.state.element_picker_open = True

    @ctrl.set("pick_element")
    def pick_element(symbol=None) -> None:
        """Apply the clicked element and close the periodic table.

        Target 'new' sets the element used for added atoms; 'change'
        routes through edit_change_element (undoable, needs a selection).
        """
        if isinstance(symbol, (list, tuple)):
            symbol = symbol[0] if symbol else None
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
    _hover_state: dict = {"world": None, "atom_idx": -1, "at": float("-inf")}
    # Snapshot taken by context_menu_opened, consumed by the menu actions.
    _ctx_menu_target: dict = {"world": None, "atom_idx": -1}

    @ctrl.set("on_hover")
    def on_hover(event=None) -> None:
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
            sdata = reader.read_structure()
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
    def toggle_clip(on=None) -> None:
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
        ctrl.view_update()

    @ctrl.set("toggle_show_slice")
    def toggle_show_slice(on=None) -> None:
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
        ctrl.view_update()

    @ctrl.set("update_clip")
    def update_clip(axis=None, value=None) -> None:
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
            ctrl.view_update()

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
            state.status_message = "Edit mode ON — click atoms to select, click empty space to add"
        else:
            state.edit_selected = []
            _remove_actors_by_prefix(plotter, "edit_highlight_")
            _reset_frozen(plotter, state)  # freezing is an edit-mode concept
            state.status_message = "Edit mode OFF"
            # Leaving the editor is a session boundary, not just a worker
            # cancellation: a delayed availability probe must not re-enable
            # and schedule a new optimization after the editor has closed.
            _disable_live_opt(clear_status=True)
        _push_view(plotter)

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
        n = len(sdata.atoms)
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
        state.edit_history.append(_structure_edit_snapshot(sdata))
        state.edit_future.clear()
        # In-place list mutations are invisible to trame's dirty
        # tracking (the pushed snapshot aliases the same list
        # object), so mark them or Undo/Redo stay disabled client-side.
        state.dirty("edit_history", "edit_future")
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
        state.edit_history.append(_structure_edit_snapshot(sdata))
        state.edit_future.clear()
        # In-place list mutations are invisible to trame's dirty
        # tracking (the pushed snapshot aliases the same list
        # object), so mark them or Undo/Redo stay disabled client-side.
        state.dirty("edit_history", "edit_future")
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
        # Save current to undo stack
        try:
            sdata = reader.read_structure()
            state.edit_history.append(_structure_edit_snapshot(sdata))
        except Exception:
            pass
        _cancel_live_opt_intent(clear_status=True)
        # Both stacks were mutated in place; see the note on the clear() sites.
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

    def _live_opt_run_is_current(run_epoch, source_reader) -> bool:
        """Authorize a callback only for the immutable current run."""
        return (
            run_epoch == _live_opt_run_epoch
            and source_reader is reader
            and _live_opt_source_reader is source_reader
            and server.state.live_opt_enabled
        )

    def _live_opt_on_step(event, run_epoch=None, source_reader=None) -> None:
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
            state.edit_history.append(_structure_edit_snapshot(sdata))
            state.edit_future.clear()
            # In-place list mutations are invisible to trame's dirty
            # tracking (the pushed snapshot aliases the same list
            # tracking — mark them so the Undo button enables client-side.
            state.dirty("edit_history", "edit_future")
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
        run_epoch=None,
        source_reader=None,
    ) -> None:
        if not _live_opt_run_is_current(run_epoch, source_reader):
            return
        server.state.live_opt_status = (
            f"relaxed in {steps} steps"
            if converged
            else f"not converged after {steps} steps"
        )
        server.state.flush()

    def _live_opt_on_error(msg: str, run_epoch=None, source_reader=None) -> None:
        if not _live_opt_run_is_current(run_epoch, source_reader):
            return
        server.state.live_opt_status = f"live-opt error: {msg}"
        server.state.flush()

    def _live_opt_on_note(msg: str, run_epoch=None, source_reader=None) -> None:
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

    def _live_opt_apply_probe(result: dict) -> None:
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
    def toggle_live_opt(on=None) -> None:
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

            asyncio.ensure_future(_probe_then_start())
            return
        state.live_opt_status = "auto-optimize on"
        _live_opt_schedule()

    @ctrl.set("set_live_opt_engine")
    def set_live_opt_engine(engine=None) -> None:
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
        import contextlib

        with contextlib.suppress(Exception):
            state.edit_history.append(_structure_edit_snapshot(sdata))
        state.edit_future.clear()
        # In-place list mutations are invisible to trame's dirty
        # tracking (the pushed snapshot aliases the same list
        # object), so mark them or Undo/Redo stay disabled client-side.
        state.dirty("edit_history", "edit_future")
        _add_atom_to_structure(reader, plotter, world, new_element)
        state.edit_selected = []
        state.status_message = f"Added {new_element} atom"
        _push_view(plotter)
        _live_opt_schedule()

    @ctrl.set("context_center_view")
    def context_center_view() -> None:
        """Center the camera on the right-clicked position."""
        plotter.reset_camera()
        plotter.render()
        _push_view(plotter)
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
        state.edit_history.append(_structure_edit_snapshot(sdata))
        state.edit_future.clear()
        # In-place list mutations are invisible to trame's dirty
        # tracking (the pushed snapshot aliases the same list
        # object), so mark them or Undo/Redo stay disabled client-side.
        state.dirty("edit_history", "edit_future")

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
        from vibeview.converters import _Z_TO_SYMBOL

        try:
            sdata = reader.read_structure()
        except Exception:
            return
        state.edit_history.append(_structure_edit_snapshot(sdata))
        state.edit_future.clear()
        state.dirty("edit_history", "edit_future")

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
            cur_sym.append(_Z_TO_SYMBOL.get(int(z), "X"))
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
    def use_library_recent(name=None) -> None:
        """Re-select a recently used library structure from its chip."""
        if isinstance(name, (list, tuple)):
            name = name[0] if name else None
        if name:
            server.state.library_query = str(name)

    @ctrl.set("toggle_library_favorite")
    def toggle_library_favorite(name=None) -> None:
        """Pin/unpin a library structure (star next to the search box)."""
        state = server.state
        if isinstance(name, (list, tuple)):
            name = name[0] if name else None
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
        state.edit_history.append(_structure_edit_snapshot(sdata))
        state.edit_future.clear()
        # In-place list mutations are invisible to trame's dirty
        # tracking (the pushed snapshot aliases the same list
        # object), so mark them or Undo/Redo stay disabled client-side.
        state.dirty("edit_history", "edit_future")

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

        new_atoms = _add_H(atoms)

        positions = [a["position"] for a in new_atoms]
        symbols = [a["symbol"] for a in new_atoms]

        _rebuild_structure_from_positions(reader, plotter, positions, symbols)
        state.edit_selected = []
        state.status_message = f"Added hydrogens ({len(new_atoms)} atoms total)"
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
        state.edit_history.append(_structure_edit_snapshot(sdata))
        state.edit_future.clear()
        # In-place list mutations are invisible to trame's dirty
        # tracking (the pushed snapshot aliases the same list
        # object), so mark them or Undo/Redo stay disabled client-side.
        state.dirty("edit_history", "edit_future")
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

        state.edit_history.append(_structure_edit_snapshot(sdata))
        state.edit_future.clear()
        # In-place list mutations are invisible to trame's dirty
        # tracking (the pushed snapshot aliases the same list
        # object), so mark them or Undo/Redo stay disabled client-side.
        state.dirty("edit_history", "edit_future")

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
        viewer_state.replication = (1, 1, 1)
        state.replication_nx = 1
        state.replication_ny = 1
        state.replication_nz = 1
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
    @ctrl.set("show_param_panel_for_structure")
    def show_param_panel_for_structure() -> None:
        """Show the parameter panel when a structure section is active."""
        server.state.show_param_panel = server.state.selected_section == "structure"

    @ctrl.set("set_calc_method")
    def set_calc_method(method: str) -> None:
        server.state.calc_method = method

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

        # Send as download
        server.state.export_data = (
            f"data:text/plain;base64,{base64.b64encode(script.encode()).decode()}"
        )
        server.state.export_filename = "vibe-qc_input.py"
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
            ctrl.view_update()
        except Exception as e:
            server.state.status_message = f"Ray tracing unavailable: {e}"
            server.state.raytrace_enabled = False

    @ctrl.set("toggle_ssao")
    def toggle_ssao(enabled=None) -> None:
        """Toggle screen-space ambient occlusion."""
        state = server.state
        state.ssao_enabled = (not state.ssao_enabled) if enabled is None else bool(enabled)
        if state.ssao_enabled:
            try:
                from vibeview.material_presets import ambient_occlusion_pass

                ambient_occlusion_pass(plotter)
                state.status_message = "Ambient occlusion on (exported images)"
            except Exception:
                state.status_message = "Ambient occlusion unavailable on this VTK build"
                state.ssao_enabled = False
        else:
            # Restore default render pass
            try:
                plotter.renderer.SetPass(None)
            except Exception:
                pass
            state.status_message = "Ambient occlusion off"
        # Refresh the framebuffer now: screenshots taken from the current
        # frame otherwise show the pre-toggle pass state.
        with contextlib.suppress(Exception):
            plotter.render()
        _push_view(plotter)

    @ctrl.set("toggle_toon")
    def toggle_toon(enabled=None) -> None:
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
    def toggle_orthographic(enabled=None) -> None:
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
            push = getattr(plotter, "_vibe_view_push_camera", None)
            if push is not None:
                push()
        state.status_message = (
            "Orthographic projection"
            if state.orthographic_projection
            else "Perspective projection"
        )

    @ctrl.set("show_memory")
    def show_memory() -> None:
        """Display memory usage by section kind."""
        if lazy_reader is not None:
            usage = lazy_reader.get_memory_usage()
            lines = ["Memory usage by section kind:"]
            total = 0
            for kind, size in sorted(usage.items(), key=lambda x: -x[1]):
                lines.append(f"  {kind}: {size // 1024} KB")
                total += size
            lines.append(f"  Total: {total // 1024} KB")
            server.state.memory_usage = "\n".join(lines)
        else:
            server.state.memory_usage = "Memory info unavailable"
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
        server.state.dark_background = dark
        plotter.set_background("#1a1a2e" if dark else "#f0f0f0")
        server.state.status_message = f"Background: {'dark' if dark else 'light'}"
        ctrl.view_update()

    # @ctrl.set alone is not client-callable; the keyboard-shortcut JS
    # invokes trame.trigger("camera_preset"), which needs a trigger registration.
    @ctrl.trigger("camera_preset")
    @ctrl.set("camera_preset")
    def camera_preset(view) -> None:
        """Set the camera to a named preset view.

        Receives a plain string from the client (JS ``'isometric'``,
        not ``['isometric']``).  Also handles the list-wrapped legacy
        path for robustness during migration.
        """
        if isinstance(view, (list, tuple)):
            view = view[0]
        presets = {
            "isometric": plotter.view_isometric,
            "xy": plotter.view_xy,
            "xz": plotter.view_xz,
            "yz": plotter.view_yz,
            "reset": plotter.reset_camera,
        }
        fn = presets.get(view)
        if fn:
            fn()
            plotter.render()
            server.state.status_message = f"Camera: {view}"
            ctrl.view_update()
            ctrl.view_push_camera()  # move the client's own camera, not just geometry (L1)

    # ── Controller: file watcher / hot-reload ─────────────────────────
    # (These were accidentally nested inside camera_preset's body, so they
    # only registered after the user first hit a camera preset button.)

    # Readers replaced during hot-reload stay alive only until the fresh
    # reader owns every controller and any viewer-side overlays. Keeping them
    # beyond that handoff leaks one archive handle per panel-only refresh.
    _retired_readers: list = []

    def _close_retired_readers() -> None:
        while _retired_readers:
            with contextlib.suppress(Exception):
                _retired_readers.pop().close()

    @ctrl.set("apply_watcher_event")
    def _apply_qvf_change(event) -> None:
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
                if reader is active_reader:
                    reader = fresh  # handlers read the closure-captured nonlocal
                # Panel-only reloads intentionally skip _reload_active_file,
                # but lazy-backed controllers (symmetry and memory usage) must
                # still stop reading the replaced archive.
                _replace_lazy_reader(fresh)
                if previously_active and previously_active in touched:
                    activate_section(previously_active)
                _update_live_status(fresh)
            else:
                _reload_active_file(preserve_camera=True)  # also updates live status
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

    def _watcher_task_is_running(task) -> bool:
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
        if _watcher_task_is_running(task):
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

        async def _watch():
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

        def _watch_finished(task) -> None:
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
    def toggle_file_watcher(enabled=None) -> None:
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
    def toggle_dark_ui(enabled=None) -> None:
        """Toggle dark mode for the UI chrome (not the 3D viewport)."""
        state = server.state
        state.ui_dark_mode = (not state.ui_dark_mode) if enabled is None else bool(enabled)
        state.status_message = f"UI theme: {'dark' if state.ui_dark_mode else 'light'}"

    # ── UI Layout ─────────────────────────────────────────────────────

    # Canonical Vuetify 3 layout: <v-app-bar>, drawers, and <v-main> are all
    # *siblings* of <v-app>. Vuetify then auto-sizes <v-main> to fill the
    # viewport between the drawers and the app bar, which is what makes the
    # 3D canvas resize cleanly with the browser window.
    with VAppLayout(server):
        # Theme CSS + light/dark switch are injected by _THEME_JS below (a
        # <style> element appended at runtime). An html.Style() block here
        # renders no <style> tag under trame's VAppLayout — which is why the
        # old .dark-mode override never took effect and the toggle did
        # nothing (design refresh 2026 theme pass).
        with v.VAppBar(
            color="primary",
            density="compact",
            elevation=2,
            v_if=("!presentation_mode",),
        ):
            v.VAppBarTitle("{{ header_title }}")
            # Streaming-checkpoint status of the active file (M4): pulses
            # while the producer is running, settles green/red at the end.
            # Announce only state transitions. The visible chip also carries
            # sequence/iteration/energy text that changes every checkpoint;
            # putting the live region on the whole chip would read every one.
            html.Span(
                "Job status: {{ live_run_status }}",
                v_if=("live_run_status",),
                classes="d-sr-only",
                role="status",
                aria_live="polite",
                aria_atomic="true",
                __properties=[
                    "role",
                    ("aria_live", "aria-live"),
                    ("aria_atomic", "aria-atomic"),
                ],
            )
            with v.VChip(
                v_if=("live_run_status",),
                size="small",
                variant="elevated",
                color=(
                    "live_run_status === 'pending' ? 'warning' : "
                    "(live_run_status === 'running' ? 'info' : "
                    "(live_run_status === 'converged' ? 'success' : 'error'))",
                ),
                classes="ml-3",
            ):
                v.VIcon(
                    "{{ live_run_status === 'pending' ? 'mdi-clock-outline' : "
                    "(live_run_status === 'running' ? 'mdi-progress-clock' : "
                    "(live_run_status === 'converged' ? 'mdi-check-circle' : "
                    "'mdi-alert-circle')) }}",
                    size="small",
                    classes="mr-1",
                )
                html.Span(
                    "{{ live_run_status }}"
                    "{{ live_checkpoint_text ? ' · ' + live_checkpoint_text : '' }}"
                )
            v.VSpacer()
            # Files dropdown (shown when multiple files are loaded)
            with v.VSelect(
                v_if=("file_names.length > 1",),
                v_model=("active_file_idx",),
                # Object items so the select emits the integer index, not
                # the filename string (int(filename) used to crash).
                items=("file_names.map((n, i) => ({ title: n, value: i }))",),
                label="Files",
                density="compact",
                hide_details=True,
                style="max-width: 180px; margin-right: 12px;",
                update_modelValue=(ctrl.switch_file, "[$event]"),
            ):
                pass
            # Camera presets — tooltips via native HTML title attribute.
            # Camera views collapse into one labelled menu — four unlabelled
            # arrow icons read as "some arrows" in a 24-icon bar (design
            # refresh 2026, item 1).
            with v.VMenu():
                with v.Template(v_slot_activator="{ props }"):
                    v.VBtn(
                        "Camera",
                        v_bind="props",
                        prepend_icon="mdi-video-switch-outline",
                        size="small",
                        variant="text",
                        title="Camera views",
                    )
                with v.VList(density="compact"):
                    for _label, _icon, _view in (
                        ("Isometric", "mdi-axis-arrow", "isometric"),
                        ("Top (XY plane)", "mdi-arrow-down-bold", "xy"),
                        ("Front (XZ plane)", "mdi-arrow-right-bold", "xz"),
                        ("Side (YZ plane)", "mdi-arrow-left-bold", "yz"),
                        ("Reset / fit all", "mdi-fit-to-page-outline", "reset"),
                    ):
                        v.VListItem(
                            title=_label,
                            prepend_icon=_icon,
                            click=(ctrl.camera_preset, f"['{_view}']"),
                        )
            # Symmetry detection
            v.VBtn(
                icon="mdi-axis-arrow-info",
                small=True,
                title="Detect point group",
                click=ctrl.detect_symmetry,
            )
            # Molecule builder. An icon + dialog rather than an inline text
            # field: the app bar already overflows its 29 buttons at 1500 px,
            # and an inline field gets flex-shrunk to a few pixels wide.
            v.VBtn(
                v_if=("builder_available",),
                icon="mdi-molecule",
                small=True,
                title="Build molecule from name",
                click="builder_dialog = true",
            )
            # Screenshot
            v.VBtn(
                icon="mdi-camera",
                small=True,
                title="Export screenshot",
                click="screenshot_dialog = true",
            )
            v.VBtn(
                icon="mdi-presentation",
                small=True,
                title="Presentation mode (fullscreen slideshow)",
                click=ctrl.toggle_presentation,
            )
            # Video export (shown when animatable sections exist)
            v.VBtn(
                icon="mdi-video",
                small=True,
                title="Export video (turntable / trajectory / vibration / orbital)",
                click=ctrl.open_video_export_dialog,
                v_if=("has_animatable",),
            )
            # Diagnostics / info collapse into one labelled menu. This also
            # retires a duplicate: "Memory usage" and "Memory usage by
            # section" were two buttons firing the same ctrl.show_memory, and
            # the former shared the mdi-information icon with About (design
            # refresh 2026, item 1).
            with v.VMenu():
                with v.Template(v_slot_activator="{ props }"):
                    v.VBtn(
                        "Info",
                        v_bind="props",
                        prepend_icon="mdi-information-outline",
                        size="small",
                        variant="text",
                        title="Diagnostics, settings & help",
                    )
                with v.VList(density="compact"):
                    v.VListItem(
                        title="Keyboard shortcuts",
                        subtitle="? for help",
                        prepend_icon="mdi-keyboard",
                        click="shortcuts_help_dialog = true",
                    )
                    v.VListItem(
                        title="Memory usage by section",
                        prepend_icon="mdi-memory",
                        click=ctrl.show_memory,
                    )
                    v.VListItem(
                        title="Performance profile",
                        prepend_icon="mdi-chart-timeline-variant",
                        click=ctrl.show_profile,
                    )
                    v.VListItem(
                        title="Download manager",
                        subtitle="recent exports",
                        prepend_icon="mdi-download",
                        click="download_manager_dialog = true",
                    )
                    v.VDivider()
                    v.VListItem(
                        title="Settings",
                        prepend_icon="mdi-cog",
                        click="settings_dialog = true",
                    )
                    v.VListItem(
                        title="About vibe-view",
                        prepend_icon="mdi-information",
                        click="about_dialog = true",
                    )
            # Geometry / scene export — one labeled menu instead of eight
            # cryptic buttons (design refresh 2026, item 1). All entries
            # route through the same ctrl.export_geometry handler.
            with v.VMenu():
                with v.Template(v_slot_activator="{ props }"):
                    v.VBtn(
                        "Export",
                        v_bind="props",
                        prepend_icon="mdi-export-variant",
                        size="small",
                        variant="text",
                        title="Export geometry / scene…",
                    )
                with v.VList(density="compact"):
                    for _label, _icon, _fmt in (
                        ("Wavefront OBJ (mesh)", "mdi-cube-outline", "obj"),
                        ("glTF (mesh)", "mdi-rotate-3d", "gltf"),
                        ("XYZ coordinates", "mdi-axis-arrow", "xyz"),
                        ("CIF (crystal)", "mdi-diamond-stone", "cif"),
                        ("POV-Ray scene", "mdi-camera-iris", "pov"),
                        ("Blender scene script", "mdi-blender-software", "blend"),
                        ("SVG vector graphic", "mdi-svg", "svg"),
                        ("CML markup", "mdi-xml", "cml"),
                    ):
                        v.VListItem(
                            title=_label,
                            prepend_icon=_icon,
                            click=(ctrl.export_geometry, f"['{_fmt}']"),
                        )
                    v.VDivider()
                    # Render setting for server-side exports. It lives here
                    # rather than among the viewport toggles because a
                    # renderer pass shapes screenshots and video frames
                    # only — the live viewport is drawn client-side from
                    # serialized actors and can never show it.
                    v.VListItem(
                        title=(
                            "ssao_enabled ? 'Ambient occlusion: on "
                            "(screenshots & video)' : 'Ambient occlusion: "
                            "off (screenshots & video)'",
                        ),
                        prepend_icon="mdi-box-shadow",
                        click=ctrl.toggle_ssao,
                    )
            # Display toggles
            v.VBtn(
                icon="mdi-format-text",
                small=True,
                title="Toggle atom labels",
                click=ctrl.toggle_atom_labels,
            )
            v.VBtn(
                icon="mdi-ruler",
                small=True,
                title="List all bond distances & angles",
                click=ctrl.compute_bonds,
            )
            v.VBtn(
                icon="mdi-pencil",
                small=True,
                title="Edit mode",
                click=ctrl.toggle_edit_mode,
            )
            # Open another QVF file from disk
            v.VBtn(
                icon="mdi-folder-open",
                small=True,
                title="Open .qvf file from disk",
                click="load_file_dialog = true",
            )
            # vq Job Manager
            v.VBtn(
                icon="mdi-server-network",
                small=True,
                title="vq Job Manager — monitor & fetch jobs",
                click=ctrl.list_vq_jobs,
            )
            # Submit to vq
            v.VBtn(
                icon="mdi-cloud-upload",
                small=True,
                title="Submit to vq cluster",
                click="vq_submit_dialog = true",
                v_if=("calc_template",),
            )
            v.VBtn(
                icon=("dark_background ? 'mdi-weather-night' : 'mdi-weather-sunny'",),
                small=True,
                title="Toggle dark/light background",
                click=ctrl.toggle_background,
            )
            v.VBtn(
                icon=("raytrace_enabled ? 'mdi-lightbulb-on' : 'mdi-lightbulb'",),
                small=True,
                title="Toggle OSPRay ray tracing",
                click=ctrl.toggle_raytrace,
                v_if=("raytrace_available",),
            )
            # High-quality raytrace render (publication / magazine-cover)
            v.VBtn(
                icon="mdi-image-filter-hdr",
                small=True,
                title="High-quality raytrace render",
                click="hq_render_dialog = true",
                v_if=("raytrace_available",),
            )
            # Client-side download triggers. A bare <script> in a Vue
            # template is NOT executed by Vue, so the old setInterval
            # auto-clicker never ran — screenshot/export "succeeded"
            # server-side but nothing ever downloaded. ClientStateChange
            # runs JS whenever the watched state var changes; we build a
            # transient <a download> and click it. The data URL is passed
            # as an argument so it resolves in the Vue expression scope.
            # Guard against running before the DOM is available (SSR /
            # early mount edge case that throws "document is undefined").
            _DL_JS = (
                "typeof document !== 'undefined' && {var} && ((d, n) => {{"
                "const a = document.createElement('a');"
                "a.href = d; a.download = n;"
                "document.body.appendChild(a); a.click(); a.remove();"
                "}})({var}, {name})"
            )
            client.ClientStateChange(
                value="export_data",
                change=_DL_JS.format(var="export_data", name="export_filename"),
            )
            client.ClientStateChange(
                value="screenshot_data",
                change=_DL_JS.format(var="screenshot_data", name="'vibe-view-screenshot.png'"),
            )
            # File-input setup: when the load dialog opens, attach the
            # onchange handler to the hidden <input type=file>.  Trame's
            # Vue3 build re-renders the DOM, so the inline ``onchange``
            # attribute alone may not survive.  This runs every time
            # ``load_file_dialog`` flips to true.
            _FILE_JS = (
                "if(!{var})return;"
                "setTimeout(function(){{"
                "var el=document.getElementById('qvf-file-input');"
                "if(!el||el._vq_wired)return;"
                "el._vq_wired=1;"
                "el.addEventListener('change',function(){{"
                "var f=this.files[0];if(!f)return;"
                "var r=new FileReader();"
                "r.onload=function(e){{"
                "var b=e.target.result.split(',')[1];"
                "trame.state.load_file_bytes=b;"
                "trame.state.load_file_name=f.name;"
                "trame.state.flush();"
                "trame.trigger('load_file_from_bytes')"
                "}};"
                "r.readAsDataURL(f);"
                "this.value=''"
                "}})"
                "}},50)"
            )
            client.ClientStateChange(
                value="load_file_dialog",
                change=_FILE_JS.format(var="load_file_dialog"),
            )

            # State-driven dialogs have no Vuetify activator slot, so Vuetify
            # cannot return focus when their focused close button unmounts.
            # Capture a keyed origin when focus first enters each modal, then
            # restore it after that dialog leaves the DOM. Transient menu
            # items retain their stable activator, while stacked dialogs keep
            # focus inside the modal that remains visible.
            client.Script(_DIALOG_FOCUS_JS)

            # Keyboard shortcut handler via client-side JS.
            # Listens for global keydown events and triggers server-side
            # actions via trame.trigger().  Bound once (window._vibe_keyboard_bound).
            _SHORTCUT_JS = (
                "typeof document !== 'undefined' && (function(){"
                "if(window._vibe_keyboard_bound)return;"
                "window._vibe_keyboard_bound=1;"
                "document.addEventListener('keydown',function(e){"
                "var t=window.trame;if(!t||!t.trigger||!t.state||e.defaultPrevented)return;"
                "var k=e.key.toLowerCase();"
                "var command=e.ctrlKey||e.metaKey;"
                # Command palette: Ctrl/Cmd+K opens from anywhere (before the
                # input-field guard below), design refresh 2026.
                "if(k==='k'&&command&&!e.altKey){e.preventDefault();"
                "t.state.set('palette_open',true);t.state.flush();return;}"
                "var target=e.target;"
                "if(target&&(target.tagName==='INPUT'||target.tagName==='TEXTAREA'||"
                "target.tagName==='SELECT'||target.isContentEditable||"
                "(target.closest&&target.closest('[contenteditable=true]'))))return;"
                # A modal owns its keyboard interaction, especially Escape.
                # Do not run viewer actions behind it.
                "if(document.querySelector('[role=dialog]'))return;"
                "if(k==='escape'){if(e.repeat)return;"
                "if(t.state.get('presentation_mode')){e.preventDefault();"
                "t.trigger('toggle_presentation');return;}"
                "if(t.state.get('edit_mode')){e.preventDefault();"
                "t.trigger('toggle_edit_mode');return;}return;}"
                "if((k==='arrowleft'||k==='arrowright')&&"
                "t.state.get('presentation_mode')&&!command&&!e.altKey){"
                "e.preventDefault();t.trigger(k==='arrowleft' ? "
                "'presentation_prev' : 'presentation_next');return;}"
                # Editing is deliberately unavailable while its controls are
                # hidden by presentation mode. Keep undo/delete from mutating
                # geometry invisibly, and do not let `e` re-enter edit mode.
                "if(t.state.get('presentation_mode')&&(k==='e'||k==='delete'||"
                "k==='backspace'||(command&&(k==='z'||k==='y')))){"
                "e.preventDefault();return;}"
                "if(command&&!e.altKey&&k==='z'){e.preventDefault();"
                "t.trigger(e.shiftKey?'edit_redo':'edit_undo');return;}"
                "if(command&&!e.altKey&&k==='y'){e.preventDefault();"
                "t.trigger('edit_redo');return;}"
                "if(command||e.altKey||e.repeat)return;"
                "if(k==='?'){e.preventDefault();t.state.set('shortcuts_help_dialog',true);"
                "t.state.flush();return;}"
                "if(k==='e'){t.trigger('toggle_edit_mode');return;}"
                "if(k==='m'){t.trigger('toggle_measure_mode');return;}"
                "if(k==='r'){t.trigger('camera_preset',['reset']);return;}"
                "if(k==='s'){t.trigger('save_screenshot');return;}"
                "if(k==='v'){t.state.set('video_export_dialog',true);t.state.flush();return;}"
                "if(k==='p'){t.trigger('toggle_presentation');return;}"
                "if(k==='delete'||k==='backspace'){e.preventDefault();"
                "t.trigger('edit_delete_selected');return;}"
                "});"
                "})()"
            )
            # Install via a global script tag: a ClientStateChange handler
            # only runs when its state CHANGES, and nothing ever changes
            # these states before the listener exists — so the listener was
            # never installed and every keyboard shortcut was dead in the
            # browser (the handler itself reads window.trame lazily).
            client.Script(_SHORTCUT_JS)

            # Side panels are user-resizable: a slim col-resize separator on
            # the inner edge live-resizes during a drag, then commits the width
            # to trame state so Vuetify reflows the main layout. The same
            # separator is keyboard-operable and exposes its rendered width as
            # an ARIA range value. A 1s re-attach interval survives either
            # drawer's v_if unmount/remount cycle (design refresh 2026, item 2).
            _PANEL_RESIZE_JS = (
                "typeof document !== 'undefined' && (function(){"
                "if(window._vibe_panel_resize)return;window._vibe_panel_resize=1;"
                "var observers={};"
                "var style=document.createElement('style');"
                "style.textContent='.vv-panel-separator:focus-visible{'"
                "+'outline:3px solid #82b1ff;outline-offset:-3px;'"
                "+'background:rgba(130,177,255,.35)!important;}';"
                "document.head.appendChild(style);"
                "function clamp(value,min,max){return Math.min(max,Math.max(min,value));}"
                "function commit(key,value){var t=window.trame;"
                "if(t&&t.state){t.state.set(key,value);t.state.flush();}}"
                "function attach(id,side,key,label,fallback){"
                "var d=document.getElementById(id);"
                "if(!d||d._vvresz)return;d._vvresz=1;"
                "var h=document.createElement('div');"
                "h.id=id+'-separator';h.className='vv-panel-separator';"
                "h.style.cssText='position:absolute;top:0;bottom:0;'"
                "+(side==='left'?'right':'left')+':0;width:6px;cursor:col-resize;z-index:1007;';"
                "h.tabIndex=0;h.setAttribute('role','separator');"
                "h.setAttribute('aria-label',label);"
                "h.setAttribute('aria-orientation','vertical');"
                "h.setAttribute('aria-controls',id);"
                "h.setAttribute('aria-valuemin','180');"
                "h.setAttribute('aria-valuemax','640');"
                "h.title=label+' (use Left and Right arrow keys)';"
                "d.appendChild(h);"
                "function read(){var width=Math.round("
                "d.getBoundingClientRect().width||d.offsetWidth);"
                "if(!width){var t=window.trame;"
                "width=Number(t&&t.state&&t.state.get(key))||fallback;}"
                "return clamp(width,180,640);}"
                "function announce(width){h.setAttribute('aria-valuenow',String(width));"
                "h.setAttribute('aria-valuetext',String(width)+' pixels');}"
                "function apply(width,save){width=clamp(Math.round(width),180,640);"
                "d.style.width=width+'px';announce(width);"
                "if(save)commit(key,width);return width;}"
                "var initial=read();announce(initial);"
                "h._vvrestore=initial>180?initial:fallback;"
                "h.addEventListener('mousedown',function(e){"
                "if(e.button!==0)return;e.preventDefault();h.focus({preventScroll:true});"
                "var sx=e.clientX,sw=read(),last=sw;"
                "function mm(ev){last=apply("
                "sw+(side==='left'?ev.clientX-sx:sx-ev.clientX),false);}"
                "function mu(){window.removeEventListener('mousemove',mm);"
                "window.removeEventListener('mouseup',mu);"
                "commit(key,last);}"
                "window.addEventListener('mousemove',mm);"
                "window.addEventListener('mouseup',mu);});"
                "h.addEventListener('keydown',function(e){"
                "var step=e.shiftKey?50:10,current=read(),next=null;"
                "if(e.key==='ArrowLeft')next=current+(side==='left'?-step:step);"
                "else if(e.key==='ArrowRight')next=current+(side==='left'?step:-step);"
                "else if(e.key==='Home')next=180;"
                "else if(e.key==='End')next=640;"
                "else if(e.key==='Enter'){if(current<=180){next=h._vvrestore||fallback;}"
                "else{h._vvrestore=current;next=180;}}else{return;}"
                "if(e.key!=='Enter'&&next<=180&&current>180)h._vvrestore=current;"
                "e.preventDefault();apply(next,true);});"
                "if(observers[id])observers[id].disconnect();"
                "if(typeof ResizeObserver!=='undefined'){"
                "observers[id]=new ResizeObserver(function(){"
                "var width=Math.round(d.getBoundingClientRect().width||d.offsetWidth);"
                "if(width)announce(clamp(width,180,640));});"
                "observers[id].observe(d);}}"
                "function attachAll(){"
                "attach('vv-left-panel','left','left_panel_width','Result sections',280);"
                "attach('vv-right-panel','right','right_panel_width','Section controls',300);}"
                "attachAll();setInterval(attachAll,1000);"
                "})()"
            )
            client.Script(_PANEL_RESIZE_JS)

            # Bridge clicks on the orbital energy diagram (in a sandboxed
            # iframe) back to the server: relay the posted render key into
            # mo_click_request state, with a nonce so re-clicking the same
            # level still fires the @state.change watcher (design refresh
            # 2026: click a level to render that orbital).
            _MO_CLICK_JS = (
                "typeof window !== 'undefined' && (function(){"
                "if(window._vibe_mo_click)return;window._vibe_mo_click=1;"
                "window.addEventListener('message',function(e){"
                "var d=e&&e.data;"
                "if(!d||d.type!=='vibeview-mo-click'||d.key==null)return;"
                "var t=window.trame;if(!t||!t.state)return;"
                "t.state.set('mo_click_request',String(d.key)+'@'+Date.now());"
                "t.state.flush();});})()"
            )
            client.Script(_MO_CLICK_JS)

            # Horizontal separator between the 3D viewport and bottom panel.
            # The panel keeps its native resize:vertical corner grip; the bar
            # adds mouse and keyboard resizing, with a ResizeObserver keeping
            # its ARIA value synchronized with the rendered height. A
            # full-window overlay during drag stops the VTK canvas from
            # receiving the move and rotating the molecule (reported
            # 2026-07-16). Re-attaches on the 1s tick because both elements are
            # v_if-gated on selected_section.
            _BOTTOM_RESIZE_JS = (
                "typeof document !== 'undefined' && (function(){"
                "if(window._vibe_hsplit)return;window._vibe_hsplit=1;"
                "var observer=null,current=null;"
                "window.addEventListener('resize',function(){"
                "if(current&&current.splitter.isConnected&&current.panel.isConnected)"
                "current.refresh();});"
                "function maxHeight(){var vh=window.innerHeight||"
                "document.documentElement.clientHeight||2000;"
                "return Math.max(80,Math.floor(vh*0.85));}"
                "function attach(){"
                "var s=document.getElementById('vv-hsplit'),"
                "p=document.getElementById('vv-bottom-panel');"
                "if(!s||!p||s._vvhs)return;s._vvhs=1;"
                "s.classList.add('vv-panel-separator');s.tabIndex=0;"
                "s.setAttribute('role','separator');"
                "s.setAttribute('aria-label','Result details');"
                "s.setAttribute('aria-orientation','horizontal');"
                "s.setAttribute('aria-controls','vv-bottom-panel');"
                "s.setAttribute('aria-valuemin','80');"
                "s.title='Result details (use Up and Down arrow keys)';"
                "function read(){return Math.round(p.getBoundingClientRect().height||"
                "p.offsetHeight||80);}"
                "function announce(height){var maximum=maxHeight();"
                "height=Math.min(maximum,Math.max(80,Math.round(height)));"
                "s.setAttribute('aria-valuemax',String(maximum));"
                "s.setAttribute('aria-valuenow',String(height));"
                "s.setAttribute('aria-valuetext',String(height)+' pixels');}"
                "function apply(height){var maximum=maxHeight();"
                "height=Math.min(maximum,Math.max(80,Math.round(height)));"
                "p.style.height=height+'px';announce(height);return height;}"
                "var initial=read();announce(initial);"
                "s._vvrestore=initial>80?initial:Math.round(maxHeight()*0.35);"
                "s.addEventListener('mousedown',function(e){"
                "if(e.button!==0)return;e.preventDefault();s.focus({preventScroll:true});"
                "var sy=e.clientY,sh=read();"
                "var ov=document.createElement('div');"
                "ov.style.cssText='position:fixed;inset:0;z-index:3000;cursor:row-resize;';"
                "document.body.appendChild(ov);"
                "function mm(ev){apply(sh+(sy-ev.clientY));}"
                "function mu(){document.removeEventListener('mousemove',mm);"
                "document.removeEventListener('mouseup',mu);ov.remove();}"
                "document.addEventListener('mousemove',mm);"
                "document.addEventListener('mouseup',mu);});"
                "s.addEventListener('keydown',function(e){"
                "var step=e.shiftKey?50:10,current=read(),next=null;"
                "if(e.key==='ArrowUp')next=current+step;"
                "else if(e.key==='ArrowDown')next=current-step;"
                "else if(e.key==='Home')next=80;"
                "else if(e.key==='End')next=maxHeight();"
                "else if(e.key==='Enter'){if(current<=80){next=s._vvrestore||"
                "Math.round(maxHeight()*0.35);}else{s._vvrestore=current;next=80;}}"
                "else{return;}"
                "if(e.key!=='Enter'&&next<=80&&current>80)s._vvrestore=current;"
                "e.preventDefault();apply(next);});"
                "if(observer)observer.disconnect();"
                "if(typeof ResizeObserver!=='undefined'){observer=new ResizeObserver(function(){"
                "announce(read());});observer.observe(p);}"
                "current={splitter:s,panel:p,refresh:function(){announce(read());}};}"
                "attach();setInterval(attach,1000);})()"
            )
            client.Script(_BOTTOM_RESIZE_JS)

            # Instant toolbar tooltips. The app-bar buttons carry a title=
            # attribute, but the native title tooltip is unreliable/slow in
            # the Electron shell (reported 2026-07-16: no tooltip on hover).
            # Move each title into data-vtip (so the native one never fires)
            # and show a lightweight custom tooltip on hover instead.
            _TOOLBAR_TIPS_JS = (
                "typeof document !== 'undefined' && (function(){"
                "if(window._vibe_tips)return;window._vibe_tips=1;"
                "var tip=document.createElement('div');"
                "tip.style.cssText='position:fixed;z-index:3001;"
                "background:rgba(33,33,33,.96);color:#fff;padding:4px 8px;"
                "border-radius:4px;font-size:12px;line-height:1.3;"
                "pointer-events:none;opacity:0;transition:opacity .08s;"
                "white-space:nowrap;box-shadow:0 2px 8px rgba(0,0,0,.4);';"
                "document.body.appendChild(tip);"
                "var sel='.v-app-bar [title],.v-app-bar [data-vtip]';"
                "function show(el){var t=el.getAttribute('title');"
                "if(t){"
                "if(!el.hasAttribute('aria-label')&&!el.hasAttribute('aria-labelledby')){"
                "el.setAttribute('aria-label',t);}"
                "el.setAttribute('data-vtip',t);el.removeAttribute('title');}"
                "t=el.getAttribute('data-vtip');if(!t)return;"
                "tip.textContent=t;var r=el.getBoundingClientRect();"
                "var vw=window.innerWidth||document.documentElement.clientWidth||2000;"
                "var half=tip.offsetWidth/2;"
                "var cx=Math.max(4+half,Math.min(r.left+r.width/2,vw-4-half));"
                "tip.style.left=cx+'px';tip.style.top=(r.bottom+6)+'px';"
                "tip.style.transform='translateX(-50%)';tip.style.opacity='1';}"
                "document.addEventListener('mouseover',function(e){"
                "var el=e.target.closest&&e.target.closest(sel);if(el)show(el);});"
                "document.addEventListener('mouseout',function(e){"
                "var el=e.target.closest&&e.target.closest(sel);"
                "if(el)tip.style.opacity='0';});})()"
            )
            client.Script(_TOOLBAR_TIPS_JS)

            # Apply the Vuetify theme class from ui_dark_mode. The toggle used
            # to only flip state (the old .dark-mode class was never bound),
            # so the chrome never actually changed; this swaps
            # v-theme--light / v-theme--dark on <div class="v-application">,
            # which drives every component's surface + accent (design refresh
            # 2026 theme pass). Polls so it applies on first paint and tracks
            # later toggles without a per-change trame binding.
            _THEME_JS = (
                "typeof document !== 'undefined' && (function(){"
                "if(window._vibe_theme)return;window._vibe_theme=1;"
                # Inject the theme stylesheet once: one shared indigo accent
                # over both Vuetify themes (.v-theme--light/.v-theme--dark,
                # two classes so it beats Vuetify's single-class rule), a
                # deep-navy dark surface matching the 3D viewport, and denser
                # card headers. Vars are RGB triplets (Vuetify wraps them).
                "var css=document.createElement('style');"
                "css.textContent="
                "'.v-application.v-theme--light,.v-application.v-theme--dark{"
                "--v-theme-primary:67,97,238;--v-theme-on-primary:255,255,255;}'"
                "+'.v-application.v-theme--dark{--v-theme-background:15,15,35;"
                "--v-theme-surface:26,26,46;--v-theme-on-surface:228,230,245;"
                "--v-theme-on-background:228,230,245;"
                "--v-theme-surface-variant:40,42,66;}'"
                "+'.v-application .v-card-title{font-weight:600;"
                "letter-spacing:.01em;font-size:1.02rem;}'"
                "+'.v-application .v-card-subtitle{opacity:.75;}'"
                "+'.vv-presentation-panel{flex:1 1 auto!important;"
                "min-height:0!important;height:100%!important;"
                "max-height:none!important;resize:none!important;}'"
                "+'.vv-presentation-overlay-content{pointer-events:none!important;}';"
                "document.head.appendChild(css);"
                # Swap the theme from ui_dark_mode on first paint and on later
                # toggles (the toggle used to only flip state). Vuetify stamps
                # a v-theme--light/dark class on EVERY themed component, not
                # just the root, so flip the class on all of them; the poll
                # re-themes components that (re)render after a toggle.
                "function apply(){var t=window.trame;"
                "if(!t||!t.state)return;"
                "var dark=!!t.state.get('ui_dark_mode');"
                "var want=dark?'v-theme--dark':'v-theme--light';"
                "var other=dark?'v-theme--light':'v-theme--dark';"
                "document.querySelectorAll('.'+other).forEach(function(el){"
                "el.classList.remove(other);el.classList.add(want);});}"
                "setInterval(apply,400);apply();})()"
            )
            client.Script(_THEME_JS)

            v.VChip(
                "{{ status_message }}",
                color="warning",
                small=True,
                v_if=("status_message",),
                role="status",
                aria_live="polite",
                aria_atomic="true",
                __properties=[
                    "role",
                    ("aria_live", "aria-live"),
                    ("aria_atomic", "aria-atomic"),
                ],
            )
            # Auto-save session indicator
            v.VChip(
                "Session: {{ session_path.split('/').pop() }}",
                v_if=("session_path",),
                size="x-small",
                color="success",
                variant="outlined",
                classes="ml-2",
            )

        # ── File-open dialog ─────────────────────────────────────────────
        # Command palette (Ctrl/Cmd+K): fuzzy-search quick actions
        # (design refresh 2026).
        with v.VDialog(
            v_model=("palette_open",),
            max_width="520px",
            scrim=True,
            afterLeave=_dialog_focus_restore("Command palette"),
            aria_label="Command palette",
            __properties=[("aria_label", "aria-label")],
        ):
            with v.VCard():
                v.VTextField(
                    v_model=("palette_query",),
                    placeholder="Type a command… (camera, view, HOMO, screenshot)",
                    prepend_inner_icon="mdi-console-line",
                    variant="solo",
                    density="compact",
                    hide_details=True,
                    clearable=True,
                    autofocus=True,
                )
                with v.VList(
                    density="compact",
                    nav=True,
                    max_height="360px",
                    style="overflow-y:auto",
                ):
                    v.VListItem(
                        v_for=(
                            "a in palette_actions.filter(x => !palette_query || "
                            "(x.title + ' ' + x.sub).toLowerCase()"
                            ".includes(palette_query.toLowerCase()))",
                        ),
                        key=("a.id",),
                        title=("a.title",),
                        subtitle=("a.sub",),
                        prepend_icon=("a.icon",),
                        click=(ctrl.run_palette_action, "[a.id]"),
                    )

        with v.VDialog(
            v_model=("builder_dialog",),
            max_width="420px",
            afterLeave=_dialog_focus_restore("Build molecule"),
            aria_label="Build molecule",
            __properties=[("aria_label", "aria-label")],
        ):
            with v.VCard():
                v.VCardTitle("Build molecule")
                v.VCardText(
                    "Generates 3D coordinates and loads them as a new "
                    "structure. Either way the geometry is a starting point, "
                    "not an optimized one."
                )
                with v.VCardText():
                    # `keyup__enter=` is silently dropped: trame only emits
                    # listeners whose name it was told about, and no widget
                    # declares that key. The modifier has to be registered
                    # explicitly through __events for Enter to reach Python.
                    v.VTextField(
                        v_model=("builder_name",),
                        label="Molecule name",
                        placeholder="pyridine",
                        prepend_inner_icon="mdi-molecule",
                        density="compact",
                        hide_details=True,
                        clearable=True,
                        autofocus=True,
                        __events=[("keyup_enter", "keyup.enter")],
                        keyup_enter=ctrl.build_molecule,
                    )
                    v.VDivider(classes="my-3")
                    # SMILES reaches molecules the curated name database does
                    # not have. Enter is registered the same way -- trame only
                    # emits listeners it was told about, so keyup__enter alone
                    # is silently dropped.
                    v.VTextField(
                        v_model=("builder_smiles",),
                        label="or SMILES",
                        placeholder="c1ccccc1O",
                        prepend_inner_icon="mdi-graph-outline",
                        density="compact",
                        hide_details=True,
                        clearable=True,
                        __events=[("keyup_enter", "keyup.enter")],
                        keyup_enter=ctrl.build_from_smiles,
                    )
                    v.VCardText(
                        "SMILES needs an optional extra. Run vibe-view doctor "
                        "for the install command.",
                        classes="text-caption text-medium-emphasis pa-0 pt-2",
                        v_if=("!smiles_available",),
                    )
                with v.VCardActions():
                    v.VSpacer()
                    v.VBtn("Cancel", variant="text", click="builder_dialog = false")
                    v.VBtn("Build", variant="tonal", click=ctrl.build_molecule)
                    v.VBtn(
                        "Build SMILES",
                        variant="tonal",
                        click=ctrl.build_from_smiles,
                        disabled=("!builder_smiles",),
                    )

        with v.VDialog(
            v_model=("load_file_dialog",),
            max_width="400px",
            afterLeave=_dialog_focus_restore("Open chemistry files"),
            aria_label="Open chemistry files",
            __properties=[("aria_label", "aria-label")],
        ):
            with v.VCard():
                v.VCardTitle("Open chemistry file(s)")
                v.VCardText(
                    "Choose a QVF, structure, cube or TREXIO HDF5 file, or enter a "
                    "server-side path / directory / glob below."
                )
                # Browser-native file picker via hidden HTML input.
                # VFileInput's content serialisation varies across Trame
                # versions; a raw <input type=file> with FileReader is
                # reliable everywhere.  The onchange handler reads the
                # file as base64 and pushes it into Trame state.
                html.Input(
                    id="qvf-file-input",
                    type="file",
                    accept=".qvf,.xyz,.cif,.cube,.pdb,.mol2,.gjf,.com,.gro,.sdf,.mol,.trexio,.h5,.hdf5",
                    style="display: none;",
                )
                v.VBtn(
                    "Browse chemistry file\u2026",
                    prepend_icon="mdi-file-upload",
                    block=True,
                    variant="tonal",
                    click="document.getElementById('qvf-file-input').click()",
                )
                v.VCardText("Server-side path:", classes="text-caption mt-2")
                v.VTextField(
                    v_model=("open_path",),
                    label="Path to chemistry file or directory",
                    placeholder="~/jobs/  or  /path/to/calc.qvf",
                    density="compact",
                    hide_details=True,
                    clearable=True,
                    __events=[("keyup_enter", "keyup.enter")],
                    keyup_enter=ctrl.open_path,
                )
                v.VCheckbox(
                    v_model=("open_recursive",),
                    label="Search subdirectories recursively",
                    density="compact",
                    hide_details=True,
                )
                with v.VCardActions():
                    v.VSpacer()
                    v.VBtn("Close", click=ctrl.close_load_dialog)
                    v.VBtn(
                        "Open",
                        color="primary",
                        variant="flat",
                        click=ctrl.open_path,
                    )

        # ── vq Job Manager (M1/A1) — a real, always-available panel ─────
        # Temporary right drawer so it overlays without disturbing the
        # permanent sections/controls drawers.
        with v.VNavigationDrawer(
            v_model=("vq_panel_open",),
            v_if=("!presentation_mode",),
            location="right",
            temporary=True,
            width=440,
            aria_label="vq Job Manager",
            __properties=[("aria_label", "aria-label")],
        ):
            with html.Div(classes="pa-3"):
                with v.VRow(dense=True, align="center", classes="mb-1"):
                    v.VIcon("mdi-server-network", classes="mr-2")
                    html.Span("vq Job Manager", classes="text-h6")
                    v.VSpacer()
                    v.VBtn(
                        icon="mdi-refresh",
                        size="small",
                        variant="text",
                        title="Refresh now",
                        click=ctrl.vq_refresh_jobs,
                    )
                    v.VBtn(
                        icon="mdi-close",
                        size="small",
                        variant="text",
                        aria_label="Close vq Job Manager",
                        __properties=[("aria_label", "aria-label")],
                        click="vq_panel_open = false",
                    )
                # ── Queue overview strip (A5): host · vq version · daemon
                # health · capacity · current load ──
                with html.Div(
                    v_if=("vq_ov_host",),
                    classes="d-flex align-center flex-wrap mb-1 text-caption",
                    style="gap: 6px; color: var(--v-theme-on-surface);",
                ):
                    v.VIcon(
                        "mdi-circle",
                        size="10",
                        color=("vq_ov_daemon ? 'success' : 'error'",),
                        title=("vq_ov_daemon ? 'daemon healthy' : 'daemon down'",),
                    )
                    html.Span("{{ vq_ov_host }}", classes="font-weight-medium")
                    v.VChip(
                        "{{ vq_ov_capacity }}",
                        v_if=("vq_ov_capacity",),
                        size="x-small",
                        label=True,
                        variant="tonal",
                    )
                    v.VChip(
                        "{{ vq_ov_load }}",
                        v_if=("vq_ov_load",),
                        size="x-small",
                        label=True,
                        variant="tonal",
                        color="info",
                    )
                with v.VRow(dense=True, align="center"):
                    v.VSwitch(
                        v_model=("vq_monitor_active",),
                        label="Live monitor (all states, auto-refresh 5s)",
                        density="compact",
                        color="primary",
                        hide_details=True,
                        change=ctrl.vq_refresh_jobs,
                    )
                v.VTextField(
                    v_model=("vq_fetch_output_dir",),
                    label="Fetch results into",
                    density="compact",
                    hide_details=True,
                    prepend_inner_icon="mdi-folder-download",
                    classes="mb-2",
                )
                v.VDivider()
                v.VProgressLinear(
                    v_if=("vq_jobs_loading",), indeterminate=True, color="primary"
                )
                # Error banner (vq missing / queue error)
                v.VAlert(
                    "{{ vq_jobs_error }}",
                    v_if=("vq_jobs_error",),
                    type="warning",
                    density="compact",
                    variant="tonal",
                    classes="my-2 text-caption",
                )
                # Job rows — explicit flex layout (avoids fragile v-slots)
                # ── Live status + log tail for the expanded job (A3) ──
                # Placed ABOVE the list so it's visible the moment a job is
                # clicked; the list scrolls below it.
                with v.VCard(
                    v_if=("vq_detail_job_id",),
                    variant="tonal",
                    classes="mb-2",
                ):
                    with v.VCardTitle(classes="d-flex align-center text-body-2"):
                        html.Span("{{ vq_detail_name }}")
                        v.VChip(
                            "{{ vq_detail_state }}",
                            size="x-small",
                            label=True,
                            classes="ml-2",
                        )
                        v.VSpacer()
                        v.VBtn(
                            icon="mdi-close",
                            size="x-small",
                            variant="text",
                            aria_label="Close job details",
                            __properties=[("aria_label", "aria-label")],
                            click="vq_detail_job_id = ''",
                        )
                    html.Pre(
                        "{{ vq_detail_log }}",
                        style=(
                            "max-height: 24vh; overflow: auto; margin: 0 12px 12px;"
                            "font-size: 0.68rem; white-space: pre-wrap;"
                            "font-family: 'JetBrains Mono', monospace;"
                            "color: var(--v-theme-on-surface);"
                        ),
                    )

                with html.Div(
                    v_if=("!vq_jobs_loading && !vq_jobs_error",),
                    style="overflow-y: auto; max-height: calc(100vh - 240px);",
                ):
                    with html.Div(
                        v_for=("job in vq_jobs_list",),
                        key=("job.id",),
                        classes="d-flex align-center py-2",
                        style="border-bottom: 1px solid rgba(255,255,255,0.08);",
                    ):
                        v.VChip(
                            "{{ job.state }}",
                            color=("job.color",),
                            size="x-small",
                            label=True,
                            classes="mr-3",
                            style="min-width: 78px; justify-content: center;",
                        )
                        with html.Div(style="flex: 1 1 auto; min-width: 0;"):
                            html.Div(
                                "{{ job.name }}",
                                classes="text-body-2 text-truncate",
                            )
                            html.Div(
                                "{{ job.id.slice(0,12) }}"
                                "{{ job.elapsed ? '  ·  ' + job.elapsed : '' }}",
                                classes="text-caption text-medium-emphasis",
                            )
                        v.VBtn(
                            icon="mdi-text-box-search-outline",
                            size="small",
                            variant="text",
                            title="Show status + log tail",
                            click=(ctrl.vq_show_job_status, "[job.id, job.name]"),
                        )
                        v.VBtn(
                            icon="mdi-monitor-eye",
                            size="small",
                            variant="text",
                            title="Watch live (checkpoint QVF)",
                            v_if=("job.watchable",),
                            click=(ctrl.vq_watch_live, "[job.id]"),
                        )
                        v.VBtn(
                            icon="mdi-open-in-app",
                            size="small",
                            variant="text",
                            title="Fetch & open results",
                            disabled=("!job.openable",),
                            click=(ctrl.open_vq_job, "[job.id]"),
                        )
                    html.Div(
                        "No jobs found. Turn on Live monitor to see "
                        "queued and running jobs.",
                        v_if=("vq_jobs_list.length === 0",),
                        classes="text-caption text-medium-emphasis pa-4 text-center",
                    )

        # ── vq Submit dialog ──────────────────────────────────────────
        with v.VDialog(
            v_model=("vq_submit_dialog",),
            max_width="450px",
            afterLeave=_dialog_focus_restore("Submit to vq"),
            aria_label="Submit to vq",
            __properties=[("aria_label", "aria-label")],
        ):
            with v.VCard():
                v.VCardTitle("Submit to vq")
                v.VCardText(
                    "{{ vq_submit_container"
                    " ? 'This will submit one pending QVF container "
                    "(structure + job.spec). vq runs it and settles the same "
                    "file in place, so the archive you get back is the whole "
                    "calculation.'"
                    " : 'This will generate a vibe-qc input script and submit "
                    "it to the vq job queue for execution.' }}"
                )
                v.VCardText(
                    "Template: {{ calc_template }}  |  Method: {{ calc_method }}  "
                    "|  Basis: {{ calc_basis }}",
                    classes="text-caption",
                )
                v.VCardSubtitle(
                    "vibe-qc input file to submit:",
                    v_if=("!vq_submit_container",),
                )
                v.VTextField(
                    v_model=("vq_submit_input",),
                    label=".py input path",
                    density="compact",
                    hide_details=True,
                    v_if=("!vq_submit_container",),
                )
                v.VSwitch(
                    v_model=("vq_submit_container",),
                    label=(
                        "Submit as QVF container (one file out, "
                        "same file back)"
                    ),
                    density="compact",
                    color="primary",
                    hide_details=True,
                    classes="px-4",
                    disabled=("!vq_submit_container_available",),
                )
                v.VSwitch(
                    v_model=("vq_submit_live_checkpoint",),
                    label=(
                        "vq_submit_container"
                        " ? 'Stream live checkpoints"
                        " (containers settle in place instead)'"
                        " : 'Stream live checkpoints"
                        " (watchable while running)'",
                    ),
                    density="compact",
                    color="primary",
                    hide_details=True,
                    classes="px-4",
                    disabled=("vq_submit_container",),
                )
                with v.VProgressLinear(
                    v_if=("vq_submitting",),
                    indeterminate=True,
                    color="primary",
                ):
                    pass
                with v.VCardActions():
                    v.VSpacer()
                    v.VBtn("Cancel", click="vq_submit_dialog = false", disabled=("vq_submitting",))
                    v.VBtn(
                        "Submit to Cluster",
                        color="primary",
                        variant="flat",
                        click=ctrl.vq_submit_job,
                        disabled=("vq_submitting",),
                    )

        # ── Supercell dialog (v1.3) ────────────────────────────────────
        with v.VDialog(
            v_model=("build_supercell_dialog",),
            max_width="400px",
            afterLeave=_dialog_focus_restore("Build supercell"),
            aria_label="Build supercell",
            __properties=[("aria_label", "aria-label")],
        ):
            with v.VCard():
                v.VCardTitle("Build Supercell")
                v.VCardText("Specify replication factors Nx, Ny, Nz:")
                with v.VRow(dense=True):
                    v.VTextField(
                        v_model=("build_supercell_nx", 1),
                        v_if=("!is_periodic || pbc_a",),
                        label="Nx",
                        type="number",
                        min=1,
                        density="compact",
                        hide_details=True,
                        style="max-width: 80px",
                    )
                    v.VTextField(
                        v_model=("build_supercell_ny", 1),
                        v_if=("!is_periodic || pbc_b",),
                        label="Ny",
                        type="number",
                        min=1,
                        density="compact",
                        hide_details=True,
                        style="max-width: 80px",
                    )
                    v.VTextField(
                        v_model=("build_supercell_nz", 1),
                        v_if=("!is_periodic || pbc_c",),
                        label="Nz",
                        type="number",
                        min=1,
                        density="compact",
                        hide_details=True,
                        style="max-width: 80px",
                    )
                with v.VCardActions():
                    v.VSpacer()
                    v.VBtn("Cancel", click="build_supercell_dialog = false")
                    v.VBtn(
                        "Build",
                        color="primary",
                        click=ctrl.build_supercell,
                    )

        # ── Screenshot dialog ─────────────────────────────────────────
        with v.VDialog(
            v_model=("screenshot_dialog",),
            max_width="400px",
            afterLeave=_dialog_focus_restore("Export screenshot"),
            aria_label="Export screenshot",
            __properties=[("aria_label", "aria-label")],
        ):
            with v.VCard():
                v.VCardTitle("Export Screenshot")
                v.VCardText("Resolution multiplier")
                v.VSelect(
                    v_model=("screenshot_scale",),
                    items=("screenshot_scale_options",),
                    label="Scale",
                    density="compact",
                    hide_details=True,
                )
                v.VSwitch(
                    v_model=("screenshot_transparent",),
                    label="Transparent background",
                    density="compact",
                    color="primary",
                )
                with v.VCardActions():
                    v.VSpacer()
                    v.VBtn("Cancel", click="screenshot_dialog = false")
                    v.VBtn(
                        "Capture",
                        color="primary",
                        click=ctrl.save_screenshot,
                    )

        # ── Video export dialog ──────────────────────────────────────
        with v.VDialog(
            v_model=("video_export_dialog",),
            max_width="450px",
            persistent=("video_export_running",),
            afterLeave=_dialog_focus_restore("Export video"),
            aria_label="Export video",
            __properties=[("aria_label", "aria-label")],
        ):
            with v.VCard():
                v.VCardTitle("Export Video")
                v.VCardText("Format, frame rate, and animation type for video export")
                v.VSelect(
                    v_model=("video_export_format", "mp4"),
                    items=("video_export_format_options",),
                    item_title="title",
                    item_value="value",
                    label="Format",
                    density="compact",
                    hide_details=True,
                )
                v.VSelect(
                    v_model=("video_export_fps", 30),
                    items=("video_export_fps_options",),
                    item_title="title",
                    item_value="value",
                    label="Frame rate",
                    density="compact",
                    hide_details=True,
                )
                v.VSelect(
                    v_model=("video_export_kind", "turntable"),
                    items=("video_export_kind_options",),
                    item_title="title",
                    item_value="value",
                    label="Animation type",
                    density="compact",
                    hide_details=True,
                )
                v.VProgressLinear(
                    v_if=("video_export_running",),
                    indeterminate=True,
                    color="primary",
                )
                with v.VCardActions():
                    v.VSpacer()
                    v.VBtn(
                        "Cancel",
                        click="video_export_dialog = false",
                        disabled=("video_export_running",),
                    )
                    v.VBtn(
                        "Export",
                        color="primary",
                        click=ctrl.do_video_export,
                        disabled=("video_export_running",),
                    )

        # ── High-quality raytrace render dialog ────────────────────────
        with v.VDialog(
            v_model=("hq_render_dialog",),
            max_width="520px",
            persistent=("hq_render_running",),
            afterLeave=_dialog_focus_restore("High-quality raytrace render"),
            aria_label="High-quality raytrace render",
            __properties=[("aria_label", "aria-label")],
        ):
            with v.VCard():
                v.VCardTitle("High-Quality Raytrace Render")
                v.VCardSubtitle("Publication / magazine-cover quality output")
                with v.VCardText():
                    # Quality preset
                    v.VSelect(
                        v_model=("hq_render_quality",),
                        items=("hq_render_quality_options",),
                        label="Quality",
                        density="compact",
                        hide_details=True,
                        disabled=("hq_render_running",),
                    )
                    # Resolution
                    v.VSelect(
                        v_model=("hq_render_resolution",),
                        items=("hq_render_resolution_options",),
                        label="Resolution",
                        density="compact",
                        hide_details=True,
                        disabled=("hq_render_running",),
                    )
                    # Environment / lighting
                    v.VSelect(
                        v_model=("hq_render_environment",),
                        items=("hq_render_environment_options",),
                        label="Lighting",
                        density="compact",
                        hide_details=True,
                        disabled=("hq_render_running",),
                    )
                    # Material style
                    v.VSelect(
                        v_model=("hq_render_material",),
                        items=("hq_render_material_options",),
                        label="Material",
                        density="compact",
                        hide_details=True,
                        disabled=("hq_render_running",),
                    )
                    # Depth of field toggle
                    v.VSwitch(
                        v_model=("hq_render_dof",),
                        label="Depth of field",
                        density="compact",
                        color="primary",
                        disabled=("hq_render_running",),
                    )
                    # Save to disk alongside QVF file
                    v.VSwitch(
                        v_model=("hq_render_save_disk",),
                        label="Save next to .qvf file",
                        density="compact",
                        color="primary",
                        disabled=("hq_render_running",),
                    )
                    v.VCardText(
                        "{{ hq_render_saved_path }}",
                        v_if=("hq_render_saved_path",),
                        classes="text-caption text-green",
                    )
                    # Progress bar (shown during render)
                    v.VProgressLinear(
                        v_model=("hq_render_progress",),
                        color="primary",
                        height="8",
                        indeterminate=("hq_render_running && hq_render_progress < 0.05",),
                        v_if=("hq_render_running",),
                    )
                    v.VCardText(
                        "{{ hq_render_progress_msg }}",
                        v_if=("hq_render_running",),
                        classes="text-caption",
                    )
                with v.VCardActions():
                    v.VSpacer()
                    v.VBtn(
                        "Cancel",
                        click="hq_render_dialog = false",
                        disabled=("hq_render_running",),
                    )
                    v.VBtn(
                        "Render",
                        color="primary",
                        click=ctrl.start_hq_render,
                        disabled=("hq_render_running",),
                    )

        # ── Keyboard shortcuts dialog ────────────────────────────────
        with v.VDialog(
            v_model=("shortcuts_help_dialog",),
            max_width="400px",
            afterLeave=_dialog_focus_restore("Keyboard shortcuts"),
            aria_label="Keyboard shortcuts",
            __properties=[("aria_label", "aria-label")],
        ):
            with v.VCard():
                v.VCardTitle("Keyboard Shortcuts")
                with v.VCardText():
                    with v.VList(density="compact"):
                        with v.VListItem(
                            v_for=("(key, desc) in Object.entries(shortcut_keys)",),
                            key=("key",),
                        ):
                            with v.VListItemTitle():
                                v.VChip(
                                    "{{ key }}",
                                    size="x-small",
                                    color="primary",
                                    variant="outlined",
                                    classes="mr-2",
                                    label=True,
                                )
                                html.Span("{{ desc }}")
                with v.VCardActions():
                    v.VSpacer()
                    v.VBtn("Close", click="shortcuts_help_dialog = false")

        # ── About dialog ────────────────────────────────────────────
        with v.VDialog(
            v_model=("about_dialog",),
            max_width="450px",
            afterLeave=_dialog_focus_restore("About vibe-view"),
            aria_label="About vibe-view",
            __properties=[("aria_label", "aria-label")],
        ):
            with v.VCard():
                v.VCardTitle("vibe-view")
                v.VCardSubtitle("{{ about_version }}")
                v.VCardText(
                    "GPU-accelerated interactive 3D viewer and molecular editor "
                    "for QVF and quantum-chemistry data from many codes.\n\n"
                    "Features: structure editing, fragment building, crystal builder, "
                    "POV-Ray/Blender export, vq integration, material presets, "
                    "presentation mode, and more."
                )
                v.VDivider()
                v.VCardText(
                    "{{ about_system_info }}",
                    classes="text-caption",
                    style="white-space: pre; font-family: monospace;",
                )
                v.VDivider()
                v.VCardText("License: MPL 2.0", classes="text-caption")
                v.VCardText("https://vibe-qc.com", classes="text-caption")
                with v.VCardActions():
                    v.VSpacer()
                    v.VBtn("Close", click="about_dialog = false")

        # ── Settings dialog ───────────────────────────────────────────
        with v.VDialog(
            v_model=("settings_dialog",),
            max_width="450px",
            afterLeave=_dialog_focus_restore("Settings"),
            aria_label="Settings",
            __properties=[("aria_label", "aria-label")],
        ):
            with v.VCard():
                v.VCardTitle("Settings")
                v.VSwitch(
                    v_model=("settings_dark_background",),
                    label="Dark background (default)",
                    density="compact",
                    color="primary",
                    hide_details=True,
                )
                v.VSwitch(
                    v_model=("ui_dark_mode", True),
                    label="Dark UI theme",
                    density="compact",
                    color="primary",
                    hide_details=True,
                    update_modelValue=(ctrl.toggle_dark_ui, "[$event]"),
                )
                v.VSelect(
                    v_model=("settings_material_preset",),
                    items=("material_preset_options",),
                    item_title="title",
                    item_value="value",
                    label="Default material style",
                    density="compact",
                    hide_details=True,
                )
                v.VSelect(
                    v_model=("settings_auto_save_interval", 60),
                    items=("settings_interval_options",),
                    item_title="title",
                    item_value="value",
                    label="Auto-save interval",
                    density="compact",
                    hide_details=True,
                )
                with v.VCardActions():
                    v.VSpacer()
                    v.VBtn("Cancel", click="settings_dialog = false")
                    v.VBtn(
                        "Save",
                        color="primary",
                        click=ctrl.save_settings,
                    )

        # ── Download manager dialog ──────────────────────────────────
        with v.VDialog(
            v_model=("download_manager_dialog",),
            max_width="500px",
            afterLeave=_dialog_focus_restore("Recent exports"),
            aria_label="Recent exports",
            __properties=[("aria_label", "aria-label")],
        ):
            with v.VCard():
                v.VCardTitle("Recent Exports")
                with v.VList(density="compact", v_if=("download_history.length > 0",)):
                    v.VListItem(
                        v_for=("item in download_history",),
                        key=("item.time",),
                        title=("item.name",),
                        subtitle=("item.format + ' — ' + item.time",),
                        prepend_icon="mdi-file-download",
                    )
                v.VCardText(
                    "No exports yet. Use the export buttons or take a screenshot.",
                    v_if=("download_history.length === 0",),
                    classes="text-caption",
                )
                with v.VCardActions():
                    v.VSpacer()
                    v.VBtn(
                        "Clear History", size="small", variant="text", click="download_history = []"
                    )
                    v.VBtn("Close", click="download_manager_dialog = false")

        # Left sidebar — sibling of v-main so Vuetify offsets the content.
        with v.VNavigationDrawer(
            permanent=True,
            v_if=("!presentation_mode",),
            width=("left_panel_width",),
            id="vv-left-panel",
            aria_label="Result sections",
            __properties=[("aria_label", "aria-label")],
        ):
            v.VListSubheader("Sections")
            # Filter box (design refresh 2026 roadmap): narrows the section
            # list by title/subtitle, client-side.
            v.VTextField(
                v_model=("section_filter", ""),
                placeholder="Filter sections…",
                prepend_inner_icon="mdi-magnify",
                density="compact",
                hide_details=True,
                clearable=True,
                classes="mx-2 mb-1",
                v_if=("sidebar_entries.length > 6",),
            )
            v.VDivider()
            with v.VList(density="compact", nav=True):
                v.VListItem(
                    v_for=(
                        "entry in sidebar_entries.filter(e => !section_filter || "
                        "((e.title || '') + ' ' + (e.subtitle || ''))"
                        ".toLowerCase().includes(section_filter.toLowerCase()))",
                    ),
                    key=("entry.id",),
                    title=("entry.title",),
                    subtitle=("entry.subtitle",),
                    # Kind glyph leads (scannable); the status tick is only
                    # worth pixels when something is wrong, so a warning badge
                    # trails on error/unsupported (design refresh 2026). The
                    # exact status stays in the subtitle either way.
                    prepend_icon=("entry.kind_icon || entry.icon",),
                    append_icon=("entry.warn ? 'mdi-alert-circle' : null",),
                    disabled=("entry.disabled",),
                    click=(
                        ctrl.activate_section,
                        "[entry.id]",
                    ),
                )

        # Right control panel — sibling, gated by selected_section.
        with v.VNavigationDrawer(
            location="right",
            permanent=True,
            width=("right_panel_width",),
            v_if=("selected_section && !presentation_mode",),
            id="vv-right-panel",
            aria_label="Section controls",
            __properties=[("aria_label", "aria-label")],
        ):
            _build_controls(server, ctrl)

        with v.VMain():
            # Inside main: viewport on top, bottom panel below. Use flex column
            # so the viewport fills the available height while the bottom panel
            # only takes the space its cards need.
            #
            # The bottom panel is user-resizable: drag its top edge or the
            # handle in its bottom-right corner.  CSS ``resize: vertical``
            # with a fixed ``height`` gives the browser-native resize widget.
            with html.Div(
                style="height: 100%; display: flex; flex-direction: column;",
            ):
                with html.Div(
                    # position:relative anchors the absolutely-positioned
                    # welcome card to the viewport — without it the card
                    # anchors to the page and slides under the 280px
                    # sections drawer (left half hidden, text truncated).
                    style="flex: 1 1 auto; min-height: 0; padding: 4px; position: relative;",
                    # Keep vtk.js mounted while a 2D slide owns the surface.
                    # Unmounting here can drop a camera push during fast or
                    # automatic panel-to-3D navigation.
                    v_show=("!presentation_mode || !presentation_panel_slide",),
                ):
                    local_view = VtkLocalView(
                        plotter.ren_win,
                        # Hardware-pick on click so measure mode can map a
                        # click to the nearest atom (handler is a no-op
                        # unless measure mode is on, so rotate/zoom are
                        # unaffected). Hover picks are debounced client-side
                        # (~10 ms after the pointer pauses, not per
                        # mousemove), so adding them costs one pick per
                        # hover stop; they feed the atom tooltip and give
                        # the right-click menu its target.
                        picking_modes=("['click','hover']",),
                        click=(ctrl.on_pick, "[$event]"),
                        hover=(ctrl.on_hover, "[$event]"),
                        # Client -> server camera readback. In VtkLocalView
                        # the client owns the camera, and nothing reported
                        # it back, so every feature that persists "the
                        # current view" silently stored the server's stale
                        # camera instead (see sync_client_camera).
                        #
                        # $event carries {type, pokedRenderer, firstRenderer};
                        # the renderers hold functions, so passing $event
                        # itself fails to encode ("Unrecognized object:
                        # [object Function]") and the handler never runs.
                        # Pull the plain number arrays out in JS instead.
                        interactor_events=("vv_camera_events", ["EndAnimation"]),
                        EndAnimation=(
                            ctrl.sync_client_camera,
                            "[$event.pokedRenderer.getActiveCamera().getPosition(),"
                            " $event.pokedRenderer.getActiveCamera().getFocalPoint(),"
                            " $event.pokedRenderer.getActiveCamera().getViewUp(),"
                            " $event.pokedRenderer.getActiveCamera().getParallelScale()]",
                        ),
                    )
                    # Bind controller method so handlers can push the mesh
                    # state to the client after plotter edits.
                    ctrl.view_update = local_view.update
                    ctrl.view_reset_camera = local_view.reset_camera
                    # push_camera sends the server plotter's exact camera to the
                    # client, unlike update() (geometry only — the client keeps
                    # its own camera) and reset_camera() (fits-all, loses zoom).
                    # This is what makes camera presets/bookmarks move the live
                    # view (audit L1).
                    ctrl.view_push_camera = local_view.push_camera
                    # Stash on the plotter so module-level _rebuild_* helpers
                    # can reach the updater without taking ctrl as an extra
                    # positional arg through 12 call sites.
                    plotter._vibe_view_update = local_view.update
                    plotter._vibe_view_push_camera = local_view.push_camera

                    # Right-click context menu: JavaScript intercepts
                    # right-clicks on the VTK canvas and opens the Vuetify
                    # context menu at the click position.
                    _CONTEXT_JS = (
                        # Bind on document: vtk.js overlays an interaction
                        # layer above the canvas, so contextmenu events never
                        # reach canvas-level listeners (verified: the event
                        # bubbles to document but 'canvas' is not in its
                        # path). Bounds-check against the canvas instead.
                        "typeof document !== 'undefined' && (function(){"
                        "if(window._vibe_ctx_wired)return;"
                        "window._vibe_ctx_wired=1;"
                        "document.addEventListener('contextmenu',function(e){"
                        "var el=document.querySelector('.vtk-view canvas, .js-vtk-view canvas, canvas');"
                        "if(!el)return;"
                        "var r=el.getBoundingClientRect();"
                        "if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)return;"
                        "e.preventDefault();"
                        "var t=window.trame;if(!t)return;"
                        "t.state.set('context_menu_x',e.clientX);"
                        "t.state.set('context_menu_y',e.clientY);"
                        "t.state.set('context_menu_show',true);"
                        "t.state.flush();"
                        # The server resolves what the menu points at from
                        # the last debounced hover pick (the pointer pauses
                        # to right-click, so one just fired); it sets
                        # context_menu_atom_idx, which gates 'Select Atom'.
                        "if(t.trigger)t.trigger('context_menu_opened');"
                        "});"
                        "})()"
                    )
                    # Script-tag install — see the keyboard-shortcut
                    # comment: state-change installers never fired.
                    client.Script(_CONTEXT_JS)

                    # Hover tooltip: track mouse position on VTK canvas
                    _VIEWPORT_ARIA_LABEL = "Interactive 3D molecular structure viewport"
                    _TOOLTIP_JS = (
                        "typeof document !== 'undefined' && (function(){"
                        "var _tries=0;var _iv=setInterval(function(){"
                        "if(++_tries>40){clearInterval(_iv);return;}"
                        "var el=document.querySelector('.vtk-view canvas, .js-vtk-view canvas, canvas');"
                        "if(!el)return;"
                        f"el.setAttribute('aria-label',{_VIEWPORT_ARIA_LABEL!r});"
                        "if(el._vibe_tt_wired)return;"
                        "clearInterval(_iv);"
                        "el._vibe_tt_wired=1;"
                        # Throttled to 80 ms: unthrottled this flushed a
                        # websocket state update on EVERY mousemove over the
                        # canvas (~60/s while interacting) — for a tooltip
                        # that only repositions. Content comes from the
                        # debounced hover pick (on_hover), not from here.
                        "el.addEventListener('mousemove',function(e){"
                        "var t=window.trame;if(!t)return;"
                        "var n=Date.now();"
                        "if(n-(el._vv_tt_last||0)<80)return;"
                        "el._vv_tt_last=n;"
                        "t.state.set('hover_tooltip_x',e.clientX);"
                        "t.state.set('hover_tooltip_y',e.clientY);"
                        "t.state.flush();"
                        "});"
                        "el.addEventListener('mouseleave',function(){"
                        "var t=window.trame;if(!t)return;"
                        "t.state.set('hover_tooltip_visible',false);"
                        "t.state.flush();"
                        "});"
                        "},250);"
                        "})()"
                    )
                    # Script-tag install — the old trigger state was only
                    # ever set by the very handler this JS installs.
                    client.Script(_TOOLTIP_JS)

                    # Welcome / quickstart panel
                    with v.VCard(
                        v_if=(
                            "!selected_section && welcome_visible && sidebar_entries.length > 0",
                        ),
                        classes="ma-4 pa-4",
                        style="position: absolute; top: 20px; left: 20px; z-index: 50; max-width: 400px; opacity: 0.95;",
                    ):
                        v.VCardTitle("👋 Welcome to vibe-view")
                        v.VCardText(
                            "Click a section in the left sidebar to start exploring your data. "
                            "Here are some quick tips:"
                        )
                        with v.VList(density="compact"):
                            v.VListItem(
                                title="Structure",
                                subtitle="Click to see atoms and bonds in 3D",
                                prepend_icon="mdi-molecule",
                            )
                            v.VListItem(
                                title="Molecular Orbitals",
                                subtitle="View HOMO, LUMO, and other orbitals as isosurfaces",
                                prepend_icon="mdi-orbit",
                            )
                            v.VListItem(
                                title="Press ? for keyboard shortcuts",
                                subtitle="e=edit, m=measure, r=reset, s=screenshot",
                                prepend_icon="mdi-keyboard",
                            )
                        v.VBtn(
                            "Got it",
                            size="small",
                            variant="text",
                            click="welcome_visible = false",
                        )

                # Resize handle — a grabber bar between viewport and bottom
                # panel. Drag it to resize (see _BOTTOM_RESIZE_JS); a
                # full-window overlay during the drag keeps the VTK canvas
                # from swallowing the gesture and rotating the molecule.
                with html.Div(
                    id="vv-hsplit",
                    style=(
                        "flex: 0 0 8px; background: #444; cursor: row-resize;"
                        "border-top: 1px solid #666; border-bottom: 1px solid #333;"
                    ),
                    v_if=("selected_section && !presentation_mode",),
                ):
                    pass

                # Bottom panel: 2D plots. Hidden when no section is selected
                # so the viewport gets the entire main area.
                # User-resizable via the native ``resize: vertical`` handle
                # (bottom-right corner).  The explicit ``height`` (not
                # flex-driven) is required for the resize widget to appear.
                with html.Div(
                    id="vv-bottom-panel",
                    style=(
                        "flex: 0 0 auto; min-height: 80px; height: 35vh;"
                        "max-height: 85vh; overflow: auto; resize: vertical;"
                        "box-sizing: border-box;"
                        "padding: 8px;"
                        # Flex column so a chart card can be told to fill the
                        # panel. Without it the card is auto-height, `height:
                        # 100%` on the iframe resolves against nothing, and the
                        # iframe falls back to its min-height: title + 300px
                        # does not fit in 35vh, so the chart hung past the
                        # bottom of the window with its x-axis clipped.
                        "display: flex; flex-direction: column;"
                    ),
                    v_if=(
                        "selected_section && "
                        "(!presentation_mode || presentation_panel_slide)",
                    ),
                    classes=("presentation_mode ? 'vv-presentation-panel' : ''",),
                ):
                    # Spectra/bands HTML comes from Plotly
                    # (Renderer.render_to_html) and carries inline <script>
                    # tags. Vue's v-html refuses to execute those (XSS
                    # protection), so we host each snippet in a sandboxed
                    # iframe via srcdoc — iframes evaluate their own scripts.
                    with v.VCard(
                        v_if=("bands_html",),
                        style=(
                            "flex: 1 1 auto; min-height: 0;"
                            "display: flex; flex-direction: column;"
                        ),
                    ):
                        v.VCardTitle("{{ bands_title }}")
                        html.Iframe(
                            srcdoc=("bands_html",),
                            style=(
                                "width: 100%; flex: 1 1 auto; min-height: 160px;"
                                " border: none;"
                            ),
                            sandbox="allow-scripts",
                        )

                    with v.VCard(
                        v_if=("phonon_html",),
                        style=(
                            "flex: 1 1 auto; min-height: 0;"
                            "display: flex; flex-direction: column;"
                        ),
                    ):
                        v.VCardTitle("{{ phonon_title }}")
                        html.Iframe(
                            srcdoc=("phonon_html",),
                            style=(
                                "width: 100%; flex: 1 1 auto; min-height: 160px;"
                                " border: none;"
                            ),
                            sandbox="allow-scripts",
                        )

                    with v.VCard(
                        v_if=("eos_html",),
                        style=(
                            "flex: 1 1 auto; min-height: 0;"
                            "display: flex; flex-direction: column;"
                        ),
                    ):
                        v.VCardTitle("{{ eos_title }}")
                        html.Iframe(
                            srcdoc=("eos_html",),
                            style=(
                                "width: 100%; flex: 1 1 auto; min-height: 160px;"
                                " border: none;"
                            ),
                            sandbox="allow-scripts",
                        )

                    with v.VCard(
                        v_if=("spectra_html",),
                        style=(
                            "flex: 1 1 auto; min-height: 0;"
                            "display: flex; flex-direction: column;"
                        ),
                    ):
                        v.VCardTitle("{{ spectra_title }}")
                        v.VCheckbox(
                            v_model=("spectra_compare",),
                            label="Compare across open files",
                            density="compact",
                            hide_details=True,
                            classes="px-4",
                            v_if=("file_names.length > 1",),
                            update_modelValue=(ctrl.toggle_spectra_compare, "[$event]"),
                        )
                        with v.VRow(
                            dense=True, classes="px-4 align-center", style="flex: 0 0 auto;"
                        ):
                            with v.VCol(cols=8):
                                v.VSlider(
                                    v_model=("spectra_gamma_scale",),
                                    label="Broadening",
                                    aria_label="Spectrum broadening",
                                    __properties=[("aria_label", "aria-label")],
                                    min=0.2,
                                    max=4.0,
                                    step=0.1,
                                    density="compact",
                                    hide_details=True,
                                    thumb_label=True,
                                    update_modelValue=(
                                        ctrl.update_spectra_view,
                                        "[$event, null]",
                                    ),
                                )
                            with v.VCol(cols=4):
                                v.VCheckbox(
                                    v_model=("spectra_normalize",),
                                    label="Normalize",
                                    density="compact",
                                    hide_details=True,
                                    update_modelValue=(
                                        ctrl.update_spectra_view,
                                        "[null, $event]",
                                    ),
                                )
                        with v.VRow(
                            dense=True, classes="px-4 align-center", style="flex: 0 0 auto;"
                        ):
                            with v.VCol(cols=7):
                                v.VSelect(
                                    v_model=("spectra_x_unit",),
                                    items=(
                                        "spectra_x_unit_options",
                                        [
                                            {"title": "Native unit", "value": "native"},
                                            {"title": "Wavenumber (cm⁻¹)", "value": "cm⁻¹"},
                                            {"title": "Energy (eV)", "value": "eV"},
                                            {"title": "Wavelength (nm)", "value": "nm"},
                                        ],
                                    ),
                                    item_title="title",
                                    item_value="value",
                                    label="X unit",
                                    density="compact",
                                    hide_details=True,
                                    update_modelValue=(
                                        ctrl.update_spectra_view,
                                        "[null, null, $event]",
                                    ),
                                )
                            with v.VCol(cols=5):
                                v.VSelect(
                                    v_model=("spectra_display",),
                                    items=(
                                        "spectra_display_options",
                                        [
                                            {"title": "Envelope + sticks", "value": "both"},
                                            {"title": "Envelope only", "value": "envelope"},
                                            {"title": "Sticks only", "value": "sticks"},
                                        ],
                                    ),
                                    item_title="title",
                                    item_value="value",
                                    label="Display",
                                    density="compact",
                                    hide_details=True,
                                    update_modelValue=(
                                        ctrl.update_spectra_view,
                                        "[null, null, null, $event]",
                                    ),
                                )
                        with (
                            v.VRow(
                                dense=True, classes="px-4 align-center", style="flex: 0 0 auto;"
                            ),
                            v.VCol(cols=12, classes="text-right"),
                        ):
                                v.VBtn(
                                    "Export CSV",
                                    size="small",
                                    variant="tonal",
                                    prepend_icon="mdi-file-delimited-outline",
                                    click=(ctrl.export_spectra_csv,),
                                )
                        html.Iframe(
                            srcdoc=("spectra_html",),
                            style=(
                                "width: 100%; flex: 1 1 auto; min-height: 140px;"
                                " border: none;"
                            ),
                            sandbox="allow-scripts",
                        )

                    with v.VCard(v_if=("trajectory_energy_image",)):
                        v.VCardTitle("Energy Profile")
                        html.Img(
                            src=("`data:image/png;base64,${trajectory_energy_image}`",),
                            style="max-width: 100%; max-height: 100%;",
                        )

                    # Charts that land in the bottom panel (SCF convergence,
                    # COOP/COHP, orbital energies). Separate card + separate
                    # state key from the properties panel below, because
                    # Plotly needs `allow-scripts` and the properties panel
                    # carries untrusted archive text that must never run.
                    with v.VCard(
                        v_if=("chart_html",),
                        style=(
                            "flex: 1 1 auto; min-height: 0;"
                            "display: flex; flex-direction: column;"
                        ),
                    ):
                        v.VCardTitle("{{ chart_title }}")
                        v.VCheckbox(
                            v_model=("scf_skip_guess",),
                            label="Hide initial guess (first SCF point)",
                            density="compact",
                            hide_details=True,
                            classes="px-4",
                            v_if=("chart_title === 'SCF Convergence'",),
                            update_modelValue=(ctrl.toggle_scf_skip_guess, "[$event]"),
                        )
                        v.VCheckbox(
                            v_model=("scf_log_energy",),
                            label="Log scale (|E − E_final|)",
                            density="compact",
                            hide_details=True,
                            classes="px-4",
                            v_if=("chart_title === 'SCF Convergence'",),
                            update_modelValue=(ctrl.toggle_scf_log_energy, "[$event]"),
                        )
                        html.Iframe(
                            srcdoc=("chart_html",),
                            style=(
                                "width: 100%; flex: 1 1 auto; min-height: 160px;"
                                " border: none;"
                            ),
                            # Plotly is JavaScript. Safe here because this
                            # channel only ever carries renderer-built
                            # figures: archive strings reach it through
                            # Plotly's JSON serialisation, which escapes
                            # '<' and '/' (pinned in
                            # tests/test_details_panel_sandbox.py).
                            sandbox="allow-scripts",
                        )

                    with v.VCard(v_if=("properties_html",)):
                        v.VCardTitle("{{ properties_title }}")
                        # The bottom panel is now user-resizable, so use
                        # ``height: 100%`` + ``min-height`` instead of
                        # viewport-relative ``vh`` units that overflow the
                        # panel.  The iframe scrolls internally when content
                        # exceeds the card.
                        # Attachment downloads live OUT here, not in the
                        # panel: that iframe is script-free by
                        # construction (empty sandbox, no opt-in), so it
                        # cannot call a controller. Bytes never enter it
                        # either — this row is the only way to reach them.
                        with v.VRow(
                            dense=True,
                            classes="px-4 pb-1 align-center",
                            v_if=("run_record_attachments.length > 0",),
                        ):
                            v.VBtn(
                                "{{ a.filename }} ({{ a.size }} B)",
                                v_for=("a in run_record_attachments",),
                                key=("a.role",),
                                size="x-small",
                                variant="tonal",
                                classes="mr-2 mb-1",
                                prepend_icon="mdi-download",
                                disabled=("a.too_large",),
                                title=(
                                    "a.too_large ? 'Too large to download from "
                                    "the viewer — extract it from the archive' "
                                    ": (a.description || a.role)"
                                ),
                                click=(ctrl.download_run_attachment, "[a.role]"),
                            )
                        html.Iframe(
                            srcdoc=("properties_html",),
                            style="width: 100%; min-height: 300px; height: 100%; border: none;",
                            # Deny, with no way to ask. This panel carries
                            # untrusted archive content (run logs, embedded
                            # attachments, citation text); an empty sandbox
                            # attribute is the most restrictive one, so such
                            # content cannot execute even if it ever escaped
                            # escaping. A renderer that needs scripts writes
                            # to chart_html instead — it cannot grant them
                            # here.
                            sandbox="",
                        )

        # ── Context menu (right-click on viewport) ──
        with v.VMenu(
            v_model=("context_menu_show",),
            position_x=("context_menu_x",),
            position_y=("context_menu_y",),
            absolute=True,
            close_on_content_click=True,
        ):
            with v.VList(density="compact"):
                v.VListItem(
                    "Select Atom",
                    prepend_icon="mdi-cursor-pointer",
                    click=ctrl.context_select_atom,
                    v_if=("context_menu_atom_idx >= 0",),
                )
                v.VListItem(
                    "Add Atom Here",
                    prepend_icon="mdi-atom",
                    click=ctrl.context_add_atom,
                )
                v.VListItem(
                    "Center View Here",
                    prepend_icon="mdi-crosshairs-gps",
                    click=ctrl.context_center_view,
                )
                v.VDivider()
                v.VListItem(
                    "Screenshot",
                    prepend_icon="mdi-camera",
                    click=ctrl.save_screenshot,
                )

        # ── Hover tooltip ──
        with html.Div(
            v_if=("hover_tooltip_visible",),
            style=(
                "position: fixed; z-index: 10000; background: rgba(0,0,0,0.85); color: white; "
                "padding: 8px 12px; border-radius: 6px; font-size: 13px; font-family: monospace; "
                "pointer-events: none; white-space: pre; max-width: 250px;"
            ),
            left=("hover_tooltip_x + 'px'",),
            top=("hover_tooltip_y + 10 + 'px'",),
        ):
            "{{ hover_tooltip }}"

        # ── Presentation overlay (v1.3) ──────────────────────────────────
        with v.VOverlay(
            v_model=("presentation_mode",),
            contained=False,
            persistent=True,
            retain_focus=False,
            scrim=False,
            content_class="vv-presentation-overlay-content",
            role="region",
            aria_label="Presentation mode",
            __properties=["role", ("aria_label", "aria-label")],
        ):
            with html.Div(
                style=(
                    "width: 100vw; height: 100vh; position: relative;"
                    " color: white; pointer-events: none;"
                ),
            ):
                # The live VTK viewport stays visible underneath. App chrome
                # is v-if-gated while presentation_mode is true, so the
                # viewport expands to the full browser before these controls
                # are painted over it.
                v.VCardText(
                    "{{ presentation_slides.length > 0 ? "
                    "presentation_slides[presentation_slide].name : '' }}",
                    classes="text-h3 text-white text-center",
                    style=(
                        "position: absolute; top: 24px; left: 50%;"
                        " transform: translateX(-50%); z-index: 100;"
                        " width: max-content; max-width: calc(100vw - 48px);"
                        " padding: 8px 18px; border-radius: 10px;"
                        " background: rgba(0,0,0,0.68);"
                        " text-shadow: 0 1px 3px #000;"
                    ),
                )
                v.VChip(
                    "{{ presentation_slide + 1 }} / {{ presentation_slides.length }}",
                    color="white",
                    variant="outlined",
                    style=(
                        "position: absolute; bottom: 24px; right: 24px;"
                        " z-index: 100; background: rgba(0,0,0,0.68);"
                    ),
                )
                # Navigation controls
                with html.Div(
                    style=(
                        "position: absolute; bottom: 24px; left: 50%;"
                        " transform: translateX(-50%); display: flex; gap: 12px;"
                        " z-index: 200; padding: 4px 8px; border-radius: 24px;"
                        " background: rgba(0,0,0,0.68); pointer-events: auto;"
                    ),
                ):
                    v.VBtn(
                        icon="mdi-chevron-left",
                        size="large",
                        color="white",
                        variant="text",
                        title="Previous slide",
                        aria_label="Previous slide",
                        __properties=[("aria_label", "aria-label")],
                        disabled=("presentation_slides.length < 2",),
                        click=ctrl.presentation_prev,
                    )
                    v.VBtn(
                        icon=(
                            "presentation_auto_advance ? 'mdi-pause' : 'mdi-play'",
                        ),
                        size="large",
                        color="white",
                        variant="text",
                        title=(
                            "presentation_auto_advance ? "
                            "'Pause automatic slide advance' : "
                            "'Start automatic slide advance'",
                        ),
                        aria_label=(
                            "presentation_auto_advance ? "
                            "'Pause automatic slide advance' : "
                            "'Start automatic slide advance'",
                        ),
                        __properties=[("aria_label", "aria-label")],
                        disabled=("presentation_slides.length < 2",),
                        click=ctrl.toggle_presentation_auto_advance,
                    )
                    v.VBtn(
                        icon="mdi-chevron-right",
                        size="large",
                        color="white",
                        variant="text",
                        title="Next slide",
                        aria_label="Next slide",
                        __properties=[("aria_label", "aria-label")],
                        disabled=("presentation_slides.length < 2",),
                        click=ctrl.presentation_next,
                    )
                    v.VBtn(
                        icon="mdi-close",
                        size="large",
                        color="red",
                        variant="text",
                        title="Exit presentation",
                        aria_label="Exit presentation",
                        __properties=[("aria_label", "aria-label")],
                        click=ctrl.toggle_presentation,
                    )

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
            # No session path — create one in the QVF directory
            if reader.path:
                state.session_path = str(
                    reader.path.parent / f"{reader.path.stem}.vibe-view-session.json"
                )
            else:
                state.session_path = str(Path.home() / ".cache" / "vibe-view" / "auto-session.json")

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
    @ctrl.set("save_settings")
    def save_settings() -> None:
        """Persist user settings to disk."""
        from vibeview.settings import save_settings as _save

        settings = {
            "dark_background": server.state.settings_dark_background,
            "material_preset": server.state.settings_material_preset,
            "auto_save_interval": int(server.state.settings_auto_save_interval or 60),
        }
        _save(settings)
        server.state.settings_dialog = False
        server.state.status_message = "Settings saved"

        # Apply immediately
        if server.state.settings_dark_background != server.state.dark_background:
            server.state.dark_background = server.state.settings_dark_background
            plotter.set_background(
                "#1a1a2e" if server.state.settings_dark_background else "#f0f0f0"
            )
            _push_view(plotter)

    async def _auto_save_loop():
        """Auto-save session every 60 seconds."""
        while True:
            await asyncio.sleep(60)
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


# ── Periodic-table element picker (design refresh 2026, editor) ──────
# (Z, symbol, grid row, grid column) for H..Cm — the range both the CPK
# colour table and the converters cover. Standard 18-column layout;
# lanthanides/actinides sit in their customary detached rows (9 and 10;
# row 8 is a thin spacer in the grid template).
def _periodic_table_cells() -> list[tuple[int, str, int, int]]:
    from vibeview.converters import _Z_TO_SYMBOL

    cells: list[tuple[int, str, int, int]] = []
    for z in range(1, 97):
        sym = _Z_TO_SYMBOL.get(z)
        if sym is None:
            continue
        if z == 1:
            row, col = 1, 1
        elif z == 2:
            row, col = 1, 18
        elif z <= 4:
            row, col = 2, z - 2
        elif z <= 10:
            row, col = 2, z + 8
        elif z <= 12:
            row, col = 3, z - 10
        elif z <= 18:
            row, col = 3, z
        elif z <= 36:
            row, col = 4, z - 18
        elif z <= 54:
            row, col = 5, z - 36
        elif z <= 56:
            row, col = 6, z - 54
        elif z <= 71:
            row, col = 9, z - 54   # La..Lu -> cols 3..17
        elif z <= 86:
            row, col = 6, z - 68   # Hf..Rn -> cols 4..18
        elif z <= 88:
            row, col = 7, z - 86
        else:
            row, col = 10, z - 86  # Ac..Cm -> cols 3..10
        cells.append((z, sym, row, col))
    return cells


def _element_button_style(z: int, row: int, col: int) -> str:
    """Inline style for one periodic-table cell, CPK-tinted."""
    from vibeview.renderers.structure import cpk_color

    color = cpk_color(z)
    # Perceived luminance decides the text colour so e.g. nitrogen blue
    # stays readable.
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    text = "#000" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "#fff"
    return (
        f"grid-row:{row}; grid-column:{col}; background:{color}; color:{text}; "
        "border:1px solid rgba(0,0,0,0.25); border-radius:3px; "
        "font: 600 11px/1.9 system-ui, sans-serif; cursor:pointer; "
        "padding:2px 0; text-align:center;"
    )


def _build_controls(server, ctrl) -> None:
    """Build the contextual control panel based on the selected section."""
    try:
        from trame.widgets import html
        from trame.widgets import vuetify3 as v
    except ImportError:
        return

    # Run info (total energy, SCF convergence, method/basis …) — surfaced
    # from the manifest provenance block. Always visible when present.
    with v.VCard(classes="pa-2 mb-2", v_if=("run_info",)):
        v.VCardTitle("Run Info", classes="text-subtitle-1")
        v.VCardText(
            "{{ run_info }}",
            classes="text-caption",
            style="white-space: pre; font-family: monospace;",
        )

    # Display options (always present — toggles affecting the structure itself)
    with v.VCard(classes="pa-2 mb-2"):
        v.VCardTitle("Display")
        v.VSwitch(
            v_model=("show_atom_labels",),
            label="Show atom labels",
            density="compact",
            color="primary",
            update_modelValue=(ctrl.toggle_atom_labels, "[$event]"),
        )
        v.VSwitch(
            v_model=("show_wannier_centers",),
            v_if=("has_wannier_centers",),
            label="Show Wannier centres",
            density="compact",
            color="primary",
            hide_details=True,
            update_modelValue=(ctrl.toggle_wannier_centers, "[$event]"),
        )
        # One-click view presets (design refresh 2026): set representation,
        # material/background, SSAO, and labels together.
        v.VSelect(
            v_model=("view_preset", None),
            items=(
                "view_preset_options",
                [
                    {"title": "Publication (white, clean)", "value": "publication"},
                    {"title": "Presentation (dark, glossy)", "value": "presentation"},
                    {"title": "Analysis (flat, labels)", "value": "analysis"},
                ],
            ),
            item_title="title",
            item_value="value",
            label="View preset",
            density="compact",
            hide_details=True,
            clearable=True,
            classes="mb-1",
            update_modelValue=(ctrl.apply_view_preset, "[$event]"),
        )
        v.VSelect(
            v_model=("representation_style",),
            items=(
                "representation_style_options",
                [
                    {"title": "Ball & Stick", "value": "ball_and_stick"},
                    {"title": "Space Filling (vdW)", "value": "space_filling"},
                    {"title": "Sticks Only", "value": "sticks_only"},
                    {"title": "Wireframe", "value": "wireframe"},
                    {"title": "Cartoon (biomolecule)", "value": "cartoon"},
                ],
            ),
            item_title="title",
            item_value="value",
            label="Representation",
            density="compact",
            hide_details=True,
            update_modelValue=(ctrl.set_representation_style, "[$event]"),
        )
        # Cartoon-only: what the ribbon is coloured by. Hidden for every
        # other representation, where it would do nothing.
        v.VSelect(
            v_model=("cartoon_color_mode",),
            items=(
                "cartoon_color_mode_options",
                [
                    {"title": "Chain", "value": "chain"},
                    {"title": "Secondary structure", "value": "structure"},
                    {"title": "Residue type", "value": "residue"},
                    {"title": "B-factor", "value": "bfactor"},
                ],
            ),
            item_title="title",
            item_value="value",
            label="Ribbon colour",
            density="compact",
            hide_details=True,
            v_if=("representation_style === 'cartoon'",),
            update_modelValue=(ctrl.set_cartoon_color_mode, "[$event]"),
        )
        # Residue/chain selection (D4), shared by every representation and
        # shown only when the structure has a CA-bearing residue. Committed
        # on Enter or blur rather than per keystroke — every commit rebuilds
        # the scene, and "A/2" is a different selection from "A/24" that the
        # user never meant to see rendered.
        v.VTextField(
            v_model=("residue_selection",),
            label="Select residues",
            placeholder="A/24-38, B",
            prepend_inner_icon="mdi-select-search",
            density="compact",
            hide_details=True,
            v_if=("residue_selection_available",),
            # Commit on Enter or on leaving the field, not per keystroke:
            # every commit rebuilds the scene, and "A/2" is a different
            # selection from "A/24" that the user never asked to see. The
            # handler reads the committed value off state, so both events
            # can share it.
            __events=["blur", ("keyup_enter", "keyup.enter")],
            keyup_enter=ctrl.set_residue_selection,
            blur=ctrl.set_residue_selection,
        )
        html.Div(
            "{{ residue_selection_summary }}",
            classes="text-caption text-medium-emphasis mb-1 px-1",
            v_if=("residue_selection_available && residue_selection_summary",),
        )
        # ── Material presets (v1.6) ──
        v.VSelect(
            v_model=("material_preset", "cpk_glossy"),
            items=("material_preset_options",),
            item_title="title",
            item_value="value",
            label="Material Style",
            density="compact",
            hide_details=True,
            update_modelValue=(ctrl.set_material_preset, "[$event]"),
        )
        # Per-element colour override (design refresh 2026): pick an element
        # present in this structure, pick a colour, apply. Hidden when the
        # file carries no structure.
        with v.VRow(
            dense=True,
            classes="align-center mt-1",
            v_if=("element_color_options.length > 0",),
        ):
            with v.VCol(cols=5):
                v.VSelect(
                    v_model=("element_color_z",),
                    items=("element_color_options",),
                    item_title="title",
                    item_value="value",
                    label="Element",
                    density="compact",
                    hide_details=True,
                )
            with v.VCol(cols=4):
                html.Input(
                    type="color",
                    v_model=("element_color_value",),
                    title="Element colour override",
                    aria_label="Element colour override",
                    __properties=[("aria_label", "aria-label")],
                    style="width: 100%; height: 34px; border: none; background: none;",
                )
            with v.VCol(cols=3):
                v.VBtn(
                    "Set",
                    size="small",
                    variant="tonal",
                    block=True,
                    click=(ctrl.set_element_color, "[element_color_z, element_color_value]"),
                )
        v.VBtn(
            "Reset element colours",
            size="small",
            variant="text",
            block=True,
            v_if=("Object.keys(element_colors || {}).length > 0",),
            click=ctrl.reset_element_colors,
        )
        v.VSwitch(
            v_model=("measure_mode",),
            label="Measure mode (click atoms)",
            density="compact",
            color="primary",
            update_modelValue=(ctrl.toggle_measure_mode, "[$event]"),
        )
        # The ambient-occlusion toggle lives in the toolbar Export menu:
        # a renderer pass only shapes server-side renders (screenshots,
        # video frames), never the live viewport, and sitting here among
        # viewport toggles it read as a broken switch (maintainer report
        # 2026-07-24).
        v.VSwitch(
            v_model=("toon_mode",),
            label="Toon / NPR shading",
            density="compact",
            color="primary",
            hide_details=True,
            update_modelValue=(ctrl.toggle_toon, "[$event]"),
        )
        v.VSwitch(
            v_model=("orthographic_projection",),
            label="Orthographic projection",
            density="compact",
            color="primary",
            hide_details=True,
            update_modelValue=(ctrl.toggle_orthographic, "[$event]"),
        )
        v.VSwitch(
            v_model=("file_watcher_enabled",),
            label="Auto-reload on file change",
            density="compact",
            color="primary",
            hide_details=True,
            update_modelValue=(ctrl.toggle_file_watcher, "[$event]"),
        )
        # Bond-order colour legend — coloured chips matching the renderer.
        v.VCardText("Bond order", classes="text-caption mt-1 mb-0")
        with v.VRow(dense=True, classes="mt-0"):
            for _colour, _label in [
                ("#888888", "1"),
                ("#9933cc", "1.5"),
                ("#3366cc", "2"),
                ("#cc3333", "3"),
            ]:
                v.VChip(
                    _label,
                    size="x-small",
                    variant="flat",
                    color=_colour,
                    text_color="white",
                    label=True,
                    classes="mr-1",
                )
        v.VBtn(
            "Clear selection",
            v_if=("measure_mode",),
            size="small",
            variant="tonal",
            click=ctrl.clear_picks,
        )

    # ── Symmetry card ──
    with v.VCard(v_if=("point_group",), classes="pa-2 mb-2"):
        v.VCardTitle("Symmetry")
        v.VCardText(
            "Point group: {{ point_group }}",
            classes="text-h6",
        )
        v.VCardText(
            "{{ point_group_details }}",
            classes="text-caption",
            style="white-space: pre; font-family: monospace;",
        )
        v.VBtn(
            "Symmetrize to {{ point_group }}",
            size="small",
            variant="tonal",
            block=True,
            prepend_icon="mdi-mirror",
            title=(
                "Project the geometry exactly onto the detected point group "
                "(undoable)"
            ),
            click=ctrl.edit_symmetrize,
        )

    # ── Edit Elements palette (v1.2) ──
    with v.VCard(v_if=("edit_mode",), classes="pa-2 mb-2"):
        v.VCardTitle("Atom Editor")
        # Periodic-table picker replaces the two element dropdowns
        # (design refresh 2026: "Periodic-table element picker for the
        # editor"). One dialog serves both targets.
        with v.VRow(dense=True, classes="align-center"):
            with v.VCol(cols=6):
                v.VBtn(
                    "New: {{ edit_new_element || 'C' }}",
                    size="small",
                    variant="tonal",
                    block=True,
                    prepend_icon="mdi-periodic-table",
                    title="Element used for newly added atoms",
                    click=(ctrl.open_element_picker, "['new']"),
                )
            with v.VCol(cols=6):
                v.VBtn(
                    "Change to…",
                    size="small",
                    variant="tonal",
                    block=True,
                    prepend_icon="mdi-periodic-table",
                    title="Change every selected atom's element",
                    disabled=("edit_selected.length === 0",),
                    click=(ctrl.open_element_picker, "['change']"),
                )
        with v.VRow(dense=True):
            v.VBtn(
                "Undo",
                icon="mdi-undo",
                size="small",
                variant="tonal",
                click=ctrl.edit_undo,
                disabled=("edit_history.length === 0",),
            )
            v.VBtn(
                "Redo",
                icon="mdi-redo",
                size="small",
                variant="tonal",
                click=ctrl.edit_redo,
                disabled=("edit_future.length === 0",),
            )
        with v.VRow(dense=True, classes="mt-2"):
            v.VBtn(
                "Delete Selected",
                icon="mdi-delete",
                size="small",
                color="error",
                variant="tonal",
                click=ctrl.edit_delete_selected,
                disabled=("edit_selected.length === 0",),
            )
        # B3 freeze-atom constraints — hold selected atoms fixed during live-opt.
        with v.VRow(dense=True, classes="mt-2"):
            v.VBtn(
                "Freeze Selected",
                icon="mdi-lock",
                size="small",
                variant="tonal",
                click=ctrl.edit_freeze_selected,
                disabled=("edit_selected.length === 0",),
            )
            v.VBtn(
                "Unfreeze All",
                icon="mdi-lock-open-variant",
                size="small",
                variant="tonal",
                click=ctrl.edit_unfreeze_all,
                disabled=("frozen_atoms.length === 0",),
            )
        v.VCardText(
            "{{ frozen_atoms.length }} atom(s) frozen"
            " — held fixed while auto-optimize relaxes the rest",
            v_if=("frozen_atoms.length > 0",),
            classes="py-0 text-caption",
        )
        # M3 live geometry optimization — Avogadro-parity auto-relax.
        # v_model already writes live_opt_enabled, so the handler must
        # *set* from the event, not flip (the toggle_clip lesson,
        # UI-OBS-G).
        v.VSwitch(
            v_model=("live_opt_enabled",),
            label="Auto-optimize",
            density="compact",
            hide_details=True,
            color="primary",
            classes="mt-1",
            disabled=("live_opt_available === false",),
            update_modelValue=(ctrl.toggle_live_opt, "[$event]"),
        )
        # Engine picker — only once the probe found more than one engine
        # (MACE appears when vibe-qc's [mace] stack is installed).
        v.VSelect(
            v_if=("live_opt_engine_options.length > 1",),
            v_model=("live_opt_engine",),
            items=("live_opt_engine_options",),
            item_title="title",
            item_value="value",
            label="Engine",
            density="compact",
            hide_details=True,
            update_modelValue=(ctrl.set_live_opt_engine, "[$event]"),
        )
        v.VCardText(
            "{{ live_opt_status }}",
            v_if=("live_opt_status",),
            classes="py-1 text-caption",
        )

    # Keep the modal outside the edit-only card. Its launchers remain gated by
    # edit mode, while an already-open picker retains its dialog semantics if
    # edit mode changes underneath it (for example via a keyboard shortcut).
    with (
        v.VDialog(
            v_model=("element_picker_open",),
            width="min(1000px, 96vw)",
            afterLeave=_dialog_focus_restore("Choose element"),
            aria_label="Choose element",
            __properties=[("aria_label", "aria-label")],
        ),
        v.VCard(classes="pa-3"),
    ):
        v.VCardTitle(
            "{{ element_picker_target === 'change' ? "
            "'Change selected atoms to…' : 'Element for new atoms' }}"
        )
        with html.Div(
            style=(
                "display:grid; grid-template-columns: repeat(18, 1fr); "
                "grid-template-rows: repeat(7, auto) 8px repeat(2, auto); "
                "gap: 3px; padding: 4px;"
            ),
        ):
            for _z, _sym, _row, _col in _periodic_table_cells():
                html.Button(
                    _sym,
                    title=f"{_sym} · Z={_z}",
                    style=_element_button_style(_z, _row, _col),
                    click=(ctrl.pick_element, f"['{_sym}']"),
                )

    # ── Structure Library (design refresh 2026, item 7) ──
    with v.VCard(v_if=("edit_mode && library_names.length > 0",), classes="pa-2 mb-2"):
        v.VCardTitle("Structure Library")
        v.VCardSubtitle(
            "{{ library_names.length }} molecules from the qc input library"
        )
        with v.VRow(dense=True, classes="align-center"):
            with v.VCol(cols=10):
                v.VAutocomplete(
                    v_model=("library_query",),
                    items=("library_names",),
                    label="Molecule name",
                    density="compact",
                    hide_details=True,
                    clearable=True,
                )
            with v.VCol(cols=2):
                v.VBtn(
                    icon=(
                        "(library_favorites || []).includes(library_query) "
                        "? 'mdi-star' : 'mdi-star-outline'",
                    ),
                    size="small",
                    variant="text",
                    title="Pin this molecule to your favourites",
                    disabled=("!library_query",),
                    click=(ctrl.toggle_library_favorite, "[library_query]"),
                )
        # Pinned favourites first — deliberate picks, persisted with the
        # session — then the implicit recents below.
        with html.Div(
            v_if=("library_favorites.length > 0",),
            classes="mt-2",
            style="display: flex; flex-wrap: wrap; gap: 4px;",
        ):
            v.VChip(
                "{{ fname }}",
                v_for=("fname in library_favorites",),
                key=("fname",),
                size="x-small",
                variant="tonal",
                color="primary",
                label=True,
                prepend_icon="mdi-star",
                title="Pinned — click to use",
                click=(ctrl.use_library_recent, "[fname]"),
            )
        # Recently used picks — one click re-selects, instead of retyping into
        # an 83-entry autocomplete for the handful you actually reuse.
        with html.Div(
            v_if=("library_recent.length > 0",),
            classes="mt-2",
            style="display: flex; flex-wrap: wrap; gap: 4px;",
        ):
            v.VChip(
                "{{ rname }}",
                v_for=("rname in library_recent",),
                key=("rname",),
                size="x-small",
                variant="tonal",
                label=True,
                title="Use this structure again",
                click=(ctrl.use_library_recent, "[rname]"),
            )
        with v.VRow(dense=True, classes="mt-2"):
            v.VBtn(
                "Insert",
                prepend_icon="mdi-plus",
                size="small",
                variant="tonal",
                title="Insert into the current structure (next to the selection)",
                click=ctrl.insert_library_structure,
                disabled=("!library_query",),
            )
            v.VBtn(
                "Open as file",
                prepend_icon="mdi-file-plus",
                size="small",
                variant="tonal",
                title="Open as a separate file in the Files dropdown",
                click=ctrl.open_library_structure,
                disabled=("!library_query",),
            )

    # ── Fragment Library (v1.3) ──
    with v.VCard(v_if=("edit_mode",), classes="pa-2 mb-2"):
        v.VCardTitle("Fragment Library")
        v.VSelect(
            v_model=("build_fragment",),
            items=("fragment_options",),
            item_title="title",
            item_value="value",
            label="Insert fragment",
            density="compact",
            hide_details=True,
            clearable=True,
            update_modelValue=(ctrl.insert_fragment, "[$event]"),
        )
        v.VBtn(
            "Add Hydrogens",
            icon="mdi-atom-variant",
            size="small",
            block=True,
            variant="tonal",
            classes="mt-2",
            click=ctrl.add_hydrogens,
            disabled=("edit_selected.length === 0",),
        )
        v.VBtn(
            "Supercell Build",
            icon="mdi-grid",
            size="small",
            block=True,
            variant="tonal",
            classes="mt-2",
            click="build_supercell_dialog = true",
        )

    # ── Calculation Parameters (v1.1: shown when structure is present) ──
    with v.VCard(v_if=("show_param_panel",), classes="pa-2 mb-2"):
        v.VCardTitle("Calculation Parameters")
        # Method
        v.VSelect(
            v_model=("calc_method",),
            items=("method_options",),
            item_title="title",
            item_value="value",
            label="Method",
            density="compact",
            hide_details=True,
        )
        # Functional (shown only for DFT methods)
        v.VSelect(
            v_if=("calc_method === 'rks' || calc_method === 'uks'",),
            v_model=("calc_functional",),
            items=("functional_options",),
            item_title="title",
            item_value="value",
            label="Functional",
            density="compact",
            hide_details=True,
        )
        # Basis set
        v.VSelect(
            v_model=("calc_basis",),
            items=("basis_options",),
            item_title="title",
            item_value="value",
            label="Basis Set",
            density="compact",
            hide_details=True,
        )
        # Charge
        v.VTextField(
            v_model=("calc_charge", 0),
            label="Charge",
            type="number",
            density="compact",
            hide_details=True,
        )
        # Multiplicity
        v.VTextField(
            v_model=("calc_multiplicity", 1),
            label="Multiplicity",
            type="number",
            min=1,
            density="compact",
            hide_details=True,
        )
        # Calculation type template (molecular systems)
        v.VSelect(
            v_if=("!is_periodic",),
            v_model=("calc_template",),
            items=("calc_template_options",),
            item_title="title",
            item_value="value",
            label="Calculation Type",
            density="compact",
            hide_details=True,
        )
        # Calculation type template (periodic systems — periodicsingle-point only)
        v.VSelect(
            v_if=("is_periodic",),
            v_model=("calc_template",),
            items=("periodic_template_options",),
            item_title="title",
            item_value="value",
            label="Calculation Type",
            density="compact",
            hide_details=True,
        )
        # Export as .py button
        v.VBtn(
            "Export vibe-qc input (.py)",
            block=True,
            color="primary",
            variant="tonal",
            size="small",
            classes="mt-2",
            click=ctrl.export_py,
        )

    # Compare/overlay mode (Phase D1): overlay all loaded structures, one colour
    # per file. Only shown when more than one file is open.
    with v.VCard(v_if=("file_names.length > 1",), classes="pa-2 mb-2"):
        v.VCardTitle("Compare Files")
        v.VSwitch(
            v_model=("compare_mode",),
            label="Overlay all structures",
            density="compact",
            color="primary",
            update_modelValue=(ctrl.toggle_compare_mode, "[$event]"),
        )
        v.VSwitch(
            v_if=("compare_mode",),
            v_model=("compare_align",),
            label="Align (RMSD fit to first file)",
            density="compact",
            color="primary",
            update_modelValue=(ctrl.toggle_compare_align, "[$event]"),
        )
        v.VSelect(
            v_if=("compare_mode",),
            v_model=("compare_highlight",),
            items=("compare_highlight_options",),
            item_title="title",
            item_value="value",
            label="Highlight",
            density="compact",
            hide_details=True,
            clearable=True,
            update_modelValue=(ctrl.set_compare_highlight, "[$event]"),
        )
        v.VChip(
            "{{ f.label }}",
            v_for=("(f, i) in compare_legend",),
            key=("i",),
            color=("f.color",),
            size="small",
            variant="flat",
            classes="ma-1",
        )

    # Density difference (Phase D2): A - B between two files that both carry a
    # volume.density section.
    with v.VCard(v_if=("density_file_options.length > 1",), classes="pa-2 mb-2"):
        v.VCardTitle("Density Difference")
        v.VSelect(
            v_model=("diff_a",),
            items=("density_file_options",),
            item_title="title",
            item_value="value",
            label="File A",
            density="compact",
        )
        v.VSelect(
            v_model=("diff_b",),
            items=("density_file_options",),
            item_title="title",
            item_value="value",
            label="File B (subtracted)",
            density="compact",
        )
        v.VBtn(
            "Show A − B",
            size="small",
            variant="tonal",
            color="primary",
            click=ctrl.show_density_diff,
        )

    # Atomic charge controls (only when atom_properties is the active section).
    with v.VCard(v_if=("atom_properties_active",), classes="pa-2 mb-2"):
        v.VCardTitle("Population Analysis")
        v.VSelect(
            v_model=("charge_kind",),
            items=("charge_kind_options",),
            item_title="title",
            item_value="value",
            label="Method",
            density="compact",
            update_modelValue=(ctrl.set_charge_kind, "[$event]"),
        )
        v.VSwitch(
            v_model=("color_by_charge",),
            label="Color atoms by charge (red +, blue −)",
            density="compact",
            color="primary",
            update_modelValue=(ctrl.toggle_color_by_charge, "[$event]"),
        )

    # Orbital picker (shown when the file has more than one volume.orbital
    # section — the sidebar collapses them into one "Molecular Orbitals"
    # entry, this dropdown switches between the individual orbitals).
    # Gated on the currently-active volume actually being an orbital so it
    # doesn't show up next to unrelated sections (atom_properties, etc.) and
    # imply a relationship that isn't there.
    with v.VCard(
        v_if=(
            "orbital_options.length > 1 && orbital_options.some(o => o.value === active_volume_id)",
        ),
        classes="pa-2 mb-2",
    ):
        v.VCardTitle("{{ orbital_panel_title }}")
        v.VSelect(
            v_model=("selected_orbital",),
            items=("orbital_options",),
            item_title="title",
            item_value="value",
            label="Orbital",
            update_modelValue=(ctrl.activate_section, "[$event]"),
        )

    # Fermi-surface band selector (shown when a fermi_surface section is active
    # and exposes more than one band sheet). Multi-select; toggling re-renders
    # only the chosen bands.
    with v.VCard(v_if=("fermi_band_options.length > 1",), classes="pa-2 mb-2"):
        v.VCardTitle("Fermi Surface Bands")
        v.VSelect(
            v_model=("fermi_selected_bands",),
            items=("fermi_band_options",),
            item_title="title",
            item_value="value",
            label="Bands shown",
            multiple=True,
            chips=True,
            density="compact",
            update_modelValue=(ctrl.update_fermi_bands, "[$event]"),
        )

    # AO picker (shown when the file has basis.ao sections and a basis.ao
    # section is the active volume — same pattern as the orbital picker).
    with v.VCard(
        v_if=("ao_options.length > 0 && ao_options.some(o => o.value === active_volume_id)",),
        classes="pa-2 mb-2",
    ):
        v.VCardTitle("Basis Functions")
        v.VSelect(
            v_model=("selected_ao",),
            items=("ao_options",),
            item_title="title",
            item_value="value",
            label="AO",
            update_modelValue=(ctrl.activate_section, "[$event]"),
        )

    # Volume controls (shown when a volume section is selected)
    with v.VCard(
        v_if=("active_volume_id",),
        classes="pa-2",
    ):
        v.VCardTitle("Isosurface Controls")
        v.VCardText("Isovalue ({{ isovalue_units }})")
        v.VSlider(
            v_model=("isovalue",),
            aria_label="Volume isovalue",
            __properties=[("aria_label", "aria-label")],
            min=0.001,
            max=1.0,
            step=0.001,
            thumb_label=True,
            update_modelValue=(ctrl.update_isovalue, "[$event]"),
        )
        v.VCardText("Colormap")
        # ``items=("colormap_options",)`` binds to the state list; a literal
        # multi-element tuple here would be parsed as a (varname, default)
        # binding pair, leaving the dropdown unusable.
        v.VSelect(
            v_model=("colormap",),
            items=("colormap_options",),
            update_modelValue=(ctrl.update_colormap, "[$event]"),
        )
        v.VSwitch(
            v_if=("esp_available",),
            v_model=("color_by_esp",),
            label="Map ESP onto surface",
            density="compact",
            color="primary",
            hide_details=True,
            update_modelValue=(ctrl.toggle_color_by_esp, "[$event]"),
        )
        v.VSwitch(
            v_model=("reduce_detail",),
            label="Reduce detail (faster)",
            density="compact",
            color="primary",
            hide_details=True,
            update_modelValue=(ctrl.toggle_reduce_detail, "[$event]"),
        )
        v.VCardText("Extra Isosurfaces", classes="text-caption mt-2")
        v.VBtn(
            "Add layer",
            small=True,
            variant="tonal",
            block=True,
            click=ctrl.add_extra_isosurface,
        )
        v.VChip(
            "iso {{ e.isovalue }} ({{ (e.opacity * 100).toFixed(0) }}%)",
            v_for=("(e, i) in extra_isosurfaces",),
            closable=True,
            size="small",
            classes="mt-1",
            update_modelValue=(ctrl.remove_extra_isosurface, "[i]"),
        )
        v.VCardText("Opacity")
        v.VSlider(
            v_model=("opacity",),
            aria_label="Volume opacity",
            __properties=[("aria_label", "aria-label")],
            min=0.0,
            max=1.0,
            step=0.05,
            thumb_label=True,
            update_modelValue=(ctrl.update_opacity, "[$event]"),
        )
        # Grid-level periodic orbital replication — tiles the scalar field
        # before marching cubes so orbitals repeat seamlessly across cells.
        v.VSelect(
            v_if=("is_periodic",),
            v_model=("periodic_replication", 0),
            items=("periodic_replication_options",),
            item_title="title",
            item_value="value",
            label="Cell replication",
            density="compact",
            hide_details=True,
            update_modelValue=(ctrl.set_periodic_replication, "[$event]"),
        )
        v.VDivider()
        v.VCardText("Clip Planes")
        v.VSwitch(
            v_model=("clip_enabled",),
            label="Enable",
            update_modelValue=(ctrl.toggle_clip, "[$event]"),
        )
        v.VSwitch(
            v_if=("clip_enabled",),
            v_model=("show_slice",),
            label="Show as 2D slice",
            density="compact",
            color="primary",
            hide_details=True,
            update_modelValue=(ctrl.toggle_show_slice, "[$event]"),
        )
        with v.VRow(v_if=("clip_enabled",), dense=True):
            with v.VCol(cols=4):
                v.VCardText("X", classes="text-caption")
                v.VSlider(
                    v_model=("clip_x",),
                    aria_label="Clip plane X position",
                    __properties=[("aria_label", "aria-label")],
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    hide_details=True,
                    dense=True,
                    update_modelValue=(ctrl.update_clip, "['x', $event]"),
                )
            with v.VCol(cols=4):
                v.VCardText("Y", classes="text-caption")
                v.VSlider(
                    v_model=("clip_y",),
                    aria_label="Clip plane Y position",
                    __properties=[("aria_label", "aria-label")],
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    hide_details=True,
                    dense=True,
                    update_modelValue=(ctrl.update_clip, "['y', $event]"),
                )
            with v.VCol(cols=4):
                v.VCardText("Z", classes="text-caption")
                v.VSlider(
                    v_model=("clip_z",),
                    aria_label="Clip plane Z position",
                    __properties=[("aria_label", "aria-label")],
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    hide_details=True,
                    dense=True,
                    update_modelValue=(ctrl.update_clip, "['z', $event]"),
                )
        html.Div(
            "{{ clip_position_message }}",
            id="vv-clip-position",
            classes="text-caption px-4 pb-2",
            v_if=("clip_enabled && show_slice && clip_position_message",),
            aria_live="off",
            __properties=[("aria_live", "aria-live")],
        )

    # Cross-fade control (shown when two volume sections are selected)
    with v.VCard(
        classes="pa-2 mt-2",
        v_if=("crossfade_active",),
    ):
        v.VCardTitle("Cross-Fade Blend")
        v.VCardText("Blend between two volumes")
        v.VSlider(
            v_model=("crossfade_blend",),
            aria_label="Volume cross-fade blend",
            __properties=[("aria_label", "aria-label")],
            min=0.0,
            max=1.0,
            step=0.01,
            thumb_label=True,
            update_modelValue=(ctrl.update_crossfade, "[$event]"),
        )

    # Replication controls — only meaningful for periodic systems
    # (gated by `is_periodic` flag set in create_app from the structure).
    with v.VCard(classes="pa-2 mt-2", v_if=("is_periodic",)):
        v.VCardTitle("Periodic Replication")
        # One input per PERIODIC axis. A 2D slab (pbc = T,T,F) shows Nx/Ny only:
        # lattice[2] is a synthesized normal, so an Nz control would tile the
        # structure along a direction the calculation never treated as periodic.
        with v.VRow():
            with v.VCol(cols=4, v_if=("pbc_a",)):
                v.VTextField(
                    v_model=("replication_nx",),
                    label="Nx",
                    type="number",
                    min=1,
                )
            with v.VCol(cols=4, v_if=("pbc_b",)):
                v.VTextField(
                    v_model=("replication_ny",),
                    label="Ny",
                    type="number",
                    min=1,
                )
            with v.VCol(cols=4, v_if=("pbc_c",)):
                v.VTextField(
                    v_model=("replication_nz",),
                    label="Nz",
                    type="number",
                    min=1,
                )
        v.VBtn(
            "Apply Replication",
            block=True,
            color="primary",
            click=(
                ctrl.update_replication,
                "[replication_nx, replication_ny, replication_nz]",
            ),
        )
        v.VSwitch(
            v_model=("wrap_periodic_orbital",),
            label="Wrap orbital to cell centre (cyclic cluster)",
            density="compact",
            color="primary",
            hide_details=True,
            update_modelValue=(ctrl.toggle_wrap_periodic, "[$event]"),
        )

    # Trajectory controls (shown when trajectory is selected)
    with v.VCard(
        classes="pa-2 mt-2",
        v_if=("trajectory_n_frames > 0",),
    ):
        v.VCardTitle("Trajectory Animation")
        v.VCardText("Frame {{ trajectory_frame + 1 }} / {{ trajectory_n_frames }}")
        v.VSlider(
            v_model=("trajectory_frame",),
            aria_label="Trajectory frame",
            __properties=[("aria_label", "aria-label")],
            min=0,
            max=("trajectory_n_frames - 1",),
            step=1,
            thumb_label=True,
            update_modelValue=(ctrl.trajectory_frame, "[$event]"),
        )
        with v.VRow(justify="center"):
            v.VBtn(
                icon="mdi-skip-previous",
                small=True,
                title="Previous trajectory frame",
                aria_label="Previous trajectory frame",
                __properties=[("aria_label", "aria-label")],
                click=(ctrl.trajectory_step, "[-1]"),
            )
            v.VBtn(
                icon=("trajectory_playing ? 'mdi-pause' : 'mdi-play'",),
                color="primary",
                title=(
                    "trajectory_playing ? 'Pause trajectory animation' : "
                    "'Start trajectory animation'",
                ),
                aria_label=(
                    "trajectory_playing ? 'Pause trajectory animation' : "
                    "'Start trajectory animation'",
                ),
                __properties=[("aria_label", "aria-label")],
                click=ctrl.trajectory_play_toggle,
            )
            v.VBtn(
                icon="mdi-skip-next",
                small=True,
                title="Next trajectory frame",
                aria_label="Next trajectory frame",
                __properties=[("aria_label", "aria-label")],
                click=(ctrl.trajectory_step, "[1]"),
            )

    # Reaction-path waypoint label (shown when a waypoint exists at the
    # current trajectory frame)
    with v.VCard(
        classes="pa-2 mt-2",
        v_if=("reaction_current_label",),
    ):
        v.VCardTitle("Reaction Waypoint")
        v.VChip(
            "{{ reaction_current_label }}",
            color="primary",
            label=True,
            size="small",
        )

    # Wavefunction (MO list) controls (shown when a wavefunction section
    # is the active selection)
    with v.VCard(
        classes="pa-2 mt-2",
        v_if=("wf_section_id",),
    ):
        v.VCardTitle("{{ wf_panel_title }}")
        v.VCardText(
            "Pick an MO and click Render. The isosurface uses |iso| from "
            "the isovalue slider (positive lobe blue, negative red)."
        )
        # One dropdown keyed on a composite "{spin}:{index}" value with a
        # rich title (energy / occupation / HOMO-LUMO). Spin is encoded in
        # the value, so there is no separate (desyncable) spin selector —
        # see wavefunction.mo_table / audit finding A2-03.
        v.VSelect(
            v_model=("wf_selected_mo",),
            items=("wf_mo_rows",),
            item_title="title",
            item_value="value",
            label="Molecular orbital",
        )
        # Re-localize: ask vibe-qc for a criterion this file does not carry.
        # Runs in a subprocess; the result lands as its own sidebar section.
        with v.VRow(dense=True, classes="mt-2", align="center"):
            with v.VCol(cols=7, classes="py-0"):
                v.VSelect(
                    v_model=("relocalize_method",),
                    items=(
                        "relocalize_method_options.length "
                        "? relocalize_method_options "
                        ": [{title:'IBO (intrinsic bond orbitals)',value:'ibo'},"
                        "{title:'Foster-Boys',value:'boys'},"
                        "{title:'Pipek-Mezey',value:'pipek-mezey'}]",
                    ),
                    item_title="title",
                    item_value="value",
                    label="Re-localize with",
                    density="compact",
                    hide_details=True,
                )
            with v.VCol(cols=5, classes="py-0"):
                v.VBtn(
                    "Localize",
                    click=ctrl.run_relocalize,
                    loading=("relocalize_running",),
                    disabled=("relocalize_running || relocalize_available === false",),
                    size="small",
                    block=True,
                )
        v.VAlert(
            "{{ relocalize_status }}",
            v_if=("relocalize_status",),
            density="compact",
            variant="tonal",
            type=(
                "relocalize_status.startsWith('failed') "
                "|| relocalize_status.includes('cannot') "
                "|| relocalize_status.includes('needs vibe-qc') "
                "? 'warning' : 'info'",
            ),
            classes="mt-1 text-caption",
        )
        with v.VRow(dense=True, classes="mt-1"):
            v.VBtn(
                icon="mdi-skip-previous",
                small=True,
                variant="tonal",
                title="Previous molecular orbital",
                aria_label="Previous molecular orbital",
                __properties=[("aria_label", "aria-label")],
                click=(ctrl.step_mo_render, "[-1]"),
            )
            v.VBtn(
                icon=("wf_animating ? 'mdi-pause' : 'mdi-play'",),
                small=True,
                variant="tonal",
                color=("wf_animating ? 'primary' : ''",),
                title=(
                    "wf_animating ? 'Pause molecular orbital animation' : "
                    "'Start molecular orbital animation'",
                ),
                aria_label=(
                    "wf_animating ? 'Pause molecular orbital animation' : "
                    "'Start molecular orbital animation'",
                ),
                __properties=[("aria_label", "aria-label")],
                click=ctrl.toggle_mo_animation,
            )
            v.VBtn(
                icon="mdi-skip-next",
                small=True,
                variant="tonal",
                title="Next molecular orbital",
                aria_label="Next molecular orbital",
                __properties=[("aria_label", "aria-label")],
                click=(ctrl.step_mo_render, "[1]"),
            )
        v.VTextField(
            v_model=("wf_n_per_dim",),
            label="Grid points / axis",
            type="number",
            min=20,
            max=120,
        )
        # One-click frontier orbitals (design refresh 2026 roadmap).
        with v.VRow(dense=True, classes="mb-1"):
            v.VBtn(
                "HOMO",
                size="small",
                variant="tonal",
                title="Render the highest occupied MO",
                click=(ctrl.render_frontier, "['HOMO']"),
            )
            v.VBtn(
                "LUMO",
                size="small",
                variant="tonal",
                title="Render the lowest unoccupied MO",
                click=(ctrl.render_frontier, "['LUMO']"),
            )
        v.VBtn(
            "Render MO",
            block=True,
            color="primary",
            click=(ctrl.render_mo, "[wf_selected_mo]"),
        )
        # Lobe opacity. The Opacity slider lives in the volume panel, which is
        # not shown while a wavefunction section is selected — so an orbital
        # could not be made opaque at all from here. Retints the existing
        # actors, so it is instant (no MO grid re-evaluation).
        v.VCardText("Orbital opacity", classes="pb-0")
        v.VSlider(
            v_model=("mo_opacity",),
            aria_label="Molecular orbital opacity",
            __properties=[("aria_label", "aria-label")],
            min=0.1,
            max=1.0,
            step=0.05,
            thumb_label=True,
            hide_details=True,
            update_modelValue=(ctrl.update_mo_opacity, "[$event]"),
        )
        # Phase E3: compute the total electron density ρ = Σ occ_i |ψ_i|² from
        # the MO coefficients and render it as an isosurface (no stored volume
        # needed). The status reports ∫ρ dV as a self-consistency check.
        v.VBtn(
            "Compute total density",
            block=True,
            variant="tonal",
            classes="mt-2",
            click=(ctrl.compute_wf_density, "[false]"),
        )
        # Only meaningful for an unrestricted wavefunction: a restricted one
        # has rho_alpha == rho_beta and the difference is identically zero.
        v.VBtn(
            "Compute spin density",
            block=True,
            variant="tonal",
            classes="mt-1",
            v_if=("wf_spin_unrestricted",),
            click=(ctrl.compute_wf_density, "[true]"),
        )
        v.VBtn(
            "Compute ELF",
            block=True,
            variant="tonal",
            classes="mt-1",
            click=ctrl.compute_wf_elf,
        )
        v.VSlider(
            v_model=("wf_elf_iso",),
            aria_label="ELF isovalue",
            __properties=[("aria_label", "aria-label")],
            min=0.05,
            max=0.95,
            step=0.05,
            label="ELF isovalue",
            thumb_label=True,
            density="compact",
            hide_details=True,
            classes="mt-1",
        )
        v.VBtn(
            "Compute NCI",
            block=True,
            variant="tonal",
            classes="mt-1",
            click=ctrl.compute_wf_nci,
        )
        v.VBtn(
            "Compute Laplacian",
            block=True,
            variant="tonal",
            classes="mt-1",
            click=ctrl.compute_wf_laplacian,
        )
        v.VBtn(
            "Show energy diagram",
            block=True,
            variant="tonal",
            classes="mt-1",
            click=ctrl.show_energy_diagram,
        )
        # Toggle to hide/show whichever computed wavefunction isosurface was
        # rendered most recently without leaving the section.
        v.VSwitch(
            v_model=("mo_visible", True),
            label="Show isosurface",
            density="compact",
            color="primary",
            hide_details=True,
            classes="mt-2",
            v_if=("wf_surface_kind !== null",),
            update_modelValue=(ctrl.toggle_mo_visibility, "[$event]"),
        )

    # Camera bookmarks (from manifest) + user bookmarks
    with v.VCard(classes="pa-2 mt-2"):
        v.VCardTitle("Bookmarks")
        # Manifest-defined bookmarks (read-only)
        v.VSelect(
            v_if=("bookmark_names.length > 0",),
            v_model=("selected_bookmark",),
            items=("bookmark_names",),
            label="Preset",
            update_modelValue=(ctrl.apply_bookmark, "[$event]"),
            density="compact",
        )
        # User bookmarks
        with v.VRow(classes="mt-2", v_if=("user_bookmark_names.length > 0",)):
            v.VSelect(
                v_model=("selected_user_bookmark",),
                items=("user_bookmark_names",),
                label="My bookmarks",
                density="compact",
                update_modelValue=(ctrl.apply_user_bookmark, "[$event]"),
            )
        with v.VRow(classes="mt-1", dense=True):
            v.VTextField(
                v_model=("new_bookmark_name", ""),
                label="Bookmark name",
                density="compact",
                hide_details=True,
                classes="mr-1",
            )
            v.VBtn(
                "Save View",
                color="primary",
                size="small",
                click=(ctrl.save_user_bookmark, "[new_bookmark_name]"),
            )
        # Session save/load
        with v.VRow(classes="mt-2", dense=True):
            v.VBtn(
                "Save Session",
                size="small",
                variant="tonal",
                click=ctrl.save_session,
            )
            v.VBtn(
                "Load Session",
                size="small",
                variant="tonal",
                classes="ml-1",
                click=(ctrl.load_session, "['']"),
            )
            v.VTextField(
                v_model=("session_path",),
                label="Path",
                density="compact",
                hide_details=True,
                classes="ml-1",
            )

    # Vibration controls (shown when vibrations section is selected)
    with v.VCard(
        classes="pa-2 mt-2",
        v_if=("vibration_n_modes > 0",),
    ):
        v.VCardTitle("Vibrational Modes")
        v.VSelect(
            v_model=("vibration_mode",),
            items=("vibration_mode_items",),
            label="Mode",
            item_title="title",
            item_value="value",
            update_modelValue=(ctrl.vibration_changed, "[vibration_mode, vibration_amplitude]"),
        )
        v.VCardText("{{ vibration_frequencies[vibration_mode] }} cm\u207b\u00b9")
        v.VCardText("Displacement amplitude")
        v.VSlider(
            v_model=("vibration_amplitude",),
            aria_label="Vibration displacement amplitude",
            __properties=[("aria_label", "aria-label")],
            min=0.0,
            max=3.0,
            step=0.1,
            thumb_label=True,
            update_modelValue=(ctrl.vibration_changed, "[vibration_mode, $event]"),
        )
        # Play/pause animates the displacement through sin(2π·phase).
        v.VBtn(
            "{{ vibration_playing ? 'Pause' : 'Play' }}",
            block=True,
            color="primary",
            prepend_icon=("vibration_playing ? 'mdi-pause' : 'mdi-play'",),
            click=ctrl.vibration_play_toggle,
        )

    # Bonds measurement result
    with v.VCard(
        classes="pa-2 mt-2",
        v_if=("measure_result",),
    ):
        v.VCardTitle("Measurements")
        v.VCardText(
            "{{ measure_result }}",
            classes="text-caption",
            style="white-space: pre-line; font-family: monospace;",
        )


# ── Helpers ──────────────────────────────────────────────────────────────


_SUBSCRIPT = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def _present_elements(reader: QVFReader) -> list[dict]:
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
        atoms = reader.read_structure().atoms
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
        atoms = reader.read_structure().atoms
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


def _palette_actions_for(reader) -> list[dict]:
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


def _remember_library_pick(state, name: str) -> None:
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


def _job_name(reader: QVFReader) -> str:
    """Best-effort short job name. Uses the QVF filename stem when the
    archive is on disk; falls back to ``<in-memory>`` otherwise.

    Structures made by the toolbar molecule builder have no path, so they
    carry their name in ``manifest.source.calculation`` as ``builder:<name>``.
    """
    if reader.path is not None:
        return reader.path.stem
    return _builder_name(reader) or "<in-memory>"


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
    prov = getattr(reader.manifest, "provenance", None) or {}
    if hasattr(prov, "model_dump"):
        prov = prov.model_dump()
    if not isinstance(prov, dict) or not prov:
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


def _status_color(status: str) -> str:
    if status == "rendered":
        return "success"
    if status.startswith("skipped"):
        return "warning"
    if status == "error":
        return "error"
    return "grey"


def _status_icon(status: str) -> str:
    if status == "rendered":
        return "mdi-check-circle"
    if status.startswith("skipped"):
        return "mdi-skip-next"
    if status == "error":
        return "mdi-alert-circle"
    return "mdi-help-circle"


def _supported_kinds_set() -> frozenset[str]:
    from vibeview.kinds import SUPPORTED_KINDS

    return SUPPORTED_KINDS


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


def _clear_output_panels(state) -> None:
    state.active_volume_id = None
    state.volume_loaded = False
    state.bands_html = None
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
    push = getattr(plotter, "_vibe_view_push_camera", None)
    if push is None:
        return
    # Best-effort: a disconnected client must not break a file switch.
    with contextlib.suppress(Exception):
        push()


def _push_view(plotter: pv.Plotter) -> None:
    """Push the latest plotter state to the client's VtkLocalView.

    ``create_app`` stashes ``local_view.update`` on the plotter as
    ``_vibe_view_update`` so module-level rebuilders can trigger a client
    refresh without taking the controller through every call signature.
    Silent no-op when running headless (e.g. in tests).
    """
    generation = int(
        getattr(plotter, "_vibe_view_push_generation", 0) or 0
    )
    plotter._vibe_view_push_generation = generation + 1
    update = getattr(plotter, "_vibe_view_update", None)
    if update is None:
        return
    try:
        update()
    except Exception:  # noqa: BLE001 — viewport push is best-effort
        pass


def _save_screenshot_disk(png_bytes: bytes, reader) -> None:
    """Write screenshot PNG next to the QVF file, if its path is known."""
    from datetime import datetime
    from pathlib import Path

    if reader.path is None:
        return
    stem = reader.path.stem
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = reader.path.parent / f"{stem}_screenshot_{ts}.png"
    out.write_bytes(png_bytes)


def _save_export_disk(data: bytes, reader, fmt: str) -> None:
    """Write exported geometry next to the QVF file."""
    from datetime import datetime
    from pathlib import Path

    if reader.path is None:
        return
    stem = reader.path.stem
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = {
        "obj": ".obj",
        "gltf": ".gltf",
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
    state,
    section,
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
    state,
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
    from vibeview.renderers.volume import VolumeRenderer

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
    _is_signed = section.kind in (
        "volume.difference",
        "volume.spin",
        "volume.orbital",
        "volume.potential",
        "basis.ao",
    )

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

        # ── Periodic grid-level replication ───────────────────────────
        # For periodic systems, tile the scalar field before marching
        # cubes so orbital isosurfaces repeat seamlessly across adjacent
        # unit cells. Suppress mesh-level replication when active.
        _use_grid_replication = periodic_rep > 0 and lattice is not None and state.is_periodic
        if _use_grid_replication:
            try:
                from vibeview.renderers.volume import replicate_volume_for_periodic

                sdata = reader.read_structure()
                if sdata.lattice_vectors is not None:
                    # Tile only along periodic axes. `replicate_volume_for_periodic`
                    # counts ADDITIONAL cells per direction, so 0 means "this axis
                    # stays one cell thick" — for a slab that keeps the isosurface
                    # from repeating into the vacuum along the synthesized normal.
                    grid_rep = tuple(
                        periodic_rep if p else 0 for p in sdata.pbc
                    )
                    raw_data, grid = replicate_volume_for_periodic(
                        raw_data,
                        grid,
                        sdata.lattice_vectors,
                        replication=grid_rep,
                    )
                    # Override the renderer's stored grid/data so
                    # make_mesh / _build_signed_contour pick up the
                    # replicated version.
                    renderer._grid = grid
                    renderer._data = raw_data
                    rep = (1, 1, 1)  # suppress mesh-level double-replication
                    state.status_message = (
                        f"Cell replication: {2 * periodic_rep + 1}³ ("
                        f"{raw_data.shape[0]}×{raw_data.shape[1]}×{raw_data.shape[2]} grid)"
                    )
            except Exception:
                pass  # Fall back to single-cell rendering

        if _is_signed:
            # Honour orbital component selection (real/imag/abs/density)
            # before splitting into ± lobes, mirroring make_mesh().
            signed_data = raw_data
            if section.kind == "volume.orbital" and section.component:
                signed_data = VolumeRenderer._apply_component(raw_data, section.component)
            pos_data = np.clip(signed_data, 0, None)
            pos_mesh = _build_signed_contour(renderer, pos_data, iso, rep, lattice)
            neg_data = np.clip(-signed_data, 0, None)
            neg_mesh = _build_signed_contour(renderer, neg_data, iso, rep, lattice)
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
            viewer_state.put_cached_mesh(
                section_id,
                iso,
                periodic_replication=periodic_rep,
                mesh=mesh,
            )
            cached = {"mesh": mesh}

    # ── render with cached mesh + current appearance hints ───────────
    _remove_actors_by_prefix(plotter, "volume_")

    if _is_signed:
        pos_mesh = cached.get("pos")
        if pos_mesh is not None:
            plotter.add_mesh(
                pos_mesh,
                color="#3366cc",
                opacity=hints.opacity,
                name=f"volume_{section_id}_pos",
                show_scalar_bar=False,
            )
        neg_mesh = cached.get("neg")
        if neg_mesh is not None:
            plotter.add_mesh(
                neg_mesh,
                color="#cc3333",
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
                            from vibeview.renderers.volume import _BOHR_TO_ANGSTROM

                            spacing = tuple(
                                float(esp_grid.voxel_vectors[i, i]) * _BOHR_TO_ANGSTROM
                                for i in range(3)
                            )
                            origin = tuple(float(x) * _BOHR_TO_ANGSTROM for x in esp_grid.origin)
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
                cmap=cmap,
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
                extra_colour = str(layer.get("colour", "#888888"))
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
    state,
    section,
    isovalue: float,
    lattice,
    replication,
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
    from vibeview.renderers.volume import VolumeRenderer, build_isosurface_mesh

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
            if tuple(rho.shape) == tuple(rdg_data.shape):
                color_data = sign_lambda2_rho(rho, dgrid.voxel_vectors)
            else:
                note = " (density grid mismatch; uncolored)"

    state.active_volume_id = section.id
    state.volume_loaded = True
    _remove_actors_by_prefix(plotter, "volume_")

    try:
        if color_data is not None:
            mesh = build_nci_mesh(rdg_data, color_data, grid, isovalue)
        else:
            mesh = build_isosurface_mesh(
                rdg_data,
                grid,
                isovalue,
                replication=replication,
                lattice_vectors=lattice,
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
            color="#888888",
            opacity=0.65,
            name=f"volume_{section.id}",
            show_scalar_bar=False,
        )
    state.status_message = f"Loaded NCI (RDG s={isovalue:.2g}){note}: {section.id}"
    plotter.render()
    _push_view(plotter)


# Per-file tints for Phase D compare/overlay mode (one colour per loaded file).
_COMPARE_PALETTE = [
    "#3366cc",
    "#cc3333",
    "#66aa55",
    "#cc8833",
    "#aa66cc",
    "#33aaaa",
    "#cccc33",
    "#cc6699",
]


def _render_compare_overlay(
    plotter: pv.Plotter, readers, align: bool = False, highlight: int | None = None
) -> list[dict]:
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

    _remove_actors_by_prefix(plotter, "cmp_")
    ref_pts = None
    legend: list[dict] = []
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


def _grids_match(grid_a, grid_b, atol: float = 1e-6) -> bool:
    """True when two volume grids are voxel-for-voxel comparable."""
    import numpy as _np

    return (
        tuple(grid_a.shape) == tuple(grid_b.shape)
        and _np.allclose(grid_a.origin, grid_b.origin, atol=atol)
        and _np.allclose(grid_a.voxel_vectors, grid_b.voxel_vectors, atol=atol)
    )


def compute_volume_difference(grid_a, data_a, grid_b, data_b):
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
    return _np.asarray(data_a, dtype=float) - _np.asarray(data_b, dtype=float)


def _activate_density_diff(plotter, viewer_state, state, readers, idx_a, idx_b) -> None:
    """Render the signed density difference ρ_A − ρ_B between two files (Phase D2).

    Draws a two-colour isosurface (red = accumulation where A > B, blue =
    depletion) over file A's structure. Same-grid only for now; mismatched grids
    surface a clear status message rather than rendering.
    """
    import numpy as _np

    from vibeview.renderers.structure import StructureRenderer
    from vibeview.renderers.volume import VolumeRenderer, build_isosurface_mesh

    try:
        ra, rb = readers[int(idx_a)], readers[int(idx_b)]
    except (IndexError, ValueError, TypeError):
        state.status_message = "Density difference: invalid file selection"
        return

    def _density(r):
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
    try:
        delta = compute_volume_difference(grid_a, data_a, grid_b, data_b)
    except ValueError as e:
        state.status_message = f"Density difference: {e}"
        return

    _remove_static_structure(plotter)
    for prefix in ("volume_", "cmp_", "fermi_", "vib_atom_", "traj_atom_", "mo_iso_"):
        _remove_actors_by_prefix(plotter, prefix)
    state.compare_mode = False
    state.compare_legend = []
    state.active_volume_id = None
    state.structure_hidden = False

    struct_sec = next((s for s in ra.sections if s.kind == "structure"), None)
    if struct_sec is not None:
        try:
            StructureRenderer(struct_sec, ra).add_to_plotter(
                plotter,
                representation=str(
                    getattr(state, "representation_style", "ball_and_stick")
                ),
                cartoon_color_mode=str(
                    getattr(state, "cartoon_color_mode", "chain")
                ),
                residue_selection=str(getattr(state, "residue_selection", "") or ""),
            )
        except QVFError:
            pass

    iso = 0.005
    lattice = _get_lattice_vectors(ra)
    pos = build_isosurface_mesh(_np.clip(delta, 0, None), grid_a, iso, lattice_vectors=lattice)
    neg = build_isosurface_mesh(_np.clip(-delta, 0, None), grid_a, iso, lattice_vectors=lattice)
    if pos is not None and pos.n_points:
        plotter.add_mesh(
            pos, color="#cc3333", opacity=0.6, name="volume_diff_pos", show_scalar_bar=False
        )
    if neg is not None and neg.n_points:
        plotter.add_mesh(
            neg, color="#3366cc", opacity=0.6, name="volume_diff_neg", show_scalar_bar=False
        )

    state.status_message = (
        f"Density difference: {_job_name(ra)} − {_job_name(rb)} "
        f"(red = accumulation, blue = depletion, iso {iso})"
    )
    plotter.view_isometric()
    plotter.reset_camera()
    plotter.render()
    _push_view(plotter)


def _activate_fermi(reader, plotter, viewer_state, state, section) -> None:
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
    for prefix in ("volume_", "vib_atom_", "traj_atom_", "mo_iso_", "fermi_"):
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

    palette = ["#3366cc", "#cc8833", "#66aa55", "#aa66cc", "#cc3333", "#33aaaa"]
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
        color="#888888",
        line_width=1,
        name="fermi_cell",
        show_scalar_bar=False,
    )
    plotter.view_isometric()
    plotter.reset_camera()

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
    renderer,
    data: "np.ndarray",
    isovalue: float,
    replication: tuple[int, int, int],
    lattice_vectors,
) -> "pv.PolyData | None":
    """Build a contour from pre-processed signed data on the renderer's grid."""
    import numpy as _np
    import pyvista as _pv

    grid = renderer.load_grid()
    from vibeview.renderers.volume import _BOHR_TO_ANGSTROM, _replicate_mesh

    shape = grid.shape
    nx, ny, nz = shape
    voxel_vecs = grid.voxel_vectors * _BOHR_TO_ANGSTROM
    origin = grid.origin * _BOHR_TO_ANGSTROM

    is_orthogonal = True
    for i in range(3):
        for j in range(3):
            if i != j and abs(voxel_vecs[i, j]) > 1e-10:
                is_orthogonal = False
                break

    if is_orthogonal:
        spacing = tuple(float(voxel_vecs[i, i]) for i in range(3))
        mesh = _pv.ImageData(
            dimensions=(nx, ny, nz),
            spacing=spacing,
            origin=tuple(float(origin[i]) for i in range(3)),
        )
        mesh.point_data["values"] = data.ravel(order="F")
        contour = mesh.contour(isosurfaces=[isovalue], scalars="values")
    else:
        ii, jj, kk = _np.meshgrid(
            _np.arange(nx, dtype=_np.float64),
            _np.arange(ny, dtype=_np.float64),
            _np.arange(nz, dtype=_np.float64),
            indexing="ij",
        )
        pts_x = origin[0] + ii * voxel_vecs[0, 0] + jj * voxel_vecs[1, 0] + kk * voxel_vecs[2, 0]
        pts_y = origin[1] + ii * voxel_vecs[0, 1] + jj * voxel_vecs[1, 1] + kk * voxel_vecs[2, 1]
        pts_z = origin[2] + ii * voxel_vecs[0, 2] + jj * voxel_vecs[1, 2] + kk * voxel_vecs[2, 2]
        sgrid = _pv.StructuredGrid(pts_x, pts_y, pts_z)
        sgrid.point_data["values"] = data.ravel(order="F")
        contour = sgrid.contour(isosurfaces=[isovalue], scalars="values")

    if contour.n_points == 0:
        return None
    if lattice_vectors is not None and any(r > 1 for r in replication):
        contour = _replicate_mesh(contour, lattice_vectors, replication)
    return contour


def _activate_bands(reader: QVFReader, state, section) -> None:
    """Lazy activation of a bands section.

    When the archive also carries a ``dos.total`` section, the bands
    and DOS are rendered side-by-side in a single combined panel.
    """
    from vibeview.renderers.bands import BandsRenderer

    renderer = BandsRenderer(section, reader)
    try:
        bands_html = renderer.render_to_html()
    except QVFError as e:
        state.status_message = f"Error loading bands: {e}"
        return

    # Check for a companion DOS section.
    dos_section = next((s for s in reader.sections if s.kind == "dos.total"), None)
    if dos_section is not None:
        try:
            from vibeview.renderers.dos import DOSRenderer, render_bands_dos_combined

            dos_renderer = DOSRenderer(dos_section, reader)
            # Single figure with a shared energy axis (bands left, DOS rotated
            # right) — the standard solid-state plot (A4-04).
            state.bands_html = render_bands_dos_combined(
                renderer, dos_renderer, title="Band Structure & DOS"
            )
            state.bands_title = "Band Structure & DOS"
            state.status_message = f"Loaded bands + DOS: {section.id}, {dos_section.id}"
        except Exception as e:
            state.bands_html = bands_html
            state.bands_title = "Band Structure"
            state.status_message = f"Loaded bands: {section.id} (DOS unavailable: {e})"
    else:
        state.bands_html = bands_html
        state.bands_title = "Band Structure"
        state.status_message = f"Loaded bands: {section.id}"


def _activate_dos(reader: QVFReader, state, section) -> None:
    """Lazy activation of a standalone DOS section (no bands available)."""
    from vibeview.renderers.dos import DOSRenderer

    renderer = DOSRenderer(section, reader)
    try:
        state.bands_html = renderer.render_to_html()
        # The bands_html slot is shared with the (absent) bands panel; title it
        # for what's actually shown so a DOS-only view isn't mislabeled
        # "Band Structure" (live-review finding).
        state.bands_title = (
            "Projected DOS" if section.kind == "dos.projected" else "Density of States"
        )
        state.status_message = f"Loaded DOS: {section.id}"
    except Exception as e:
        state.status_message = f"Error loading DOS: {e}"


def _activate_phonon_bands(reader: QVFReader, state, section) -> None:
    """Lazy activation of a phonon_bands section (2D side panel)."""
    from vibeview.renderers.phonon import PhononBandsRenderer

    renderer = PhononBandsRenderer(section, reader)
    try:
        state.phonon_html = renderer.render_to_html()
        state.phonon_title = "Phonon Band Structure"
        state.status_message = f"Loaded phonon bands: {section.id}"
    except QVFError as e:
        state.status_message = f"Error loading phonon bands: {e}"


def _activate_phonon_dos(reader: QVFReader, state, section) -> None:
    """Lazy activation of a phonon_dos section (2D side panel)."""
    from vibeview.renderers.phonon import PhononDOSRenderer

    renderer = PhononDOSRenderer(section, reader)
    try:
        state.phonon_html = renderer.render_to_html()
        state.phonon_title = "Phonon DOS"
        state.status_message = f"Loaded phonon DOS: {section.id}"
    except QVFError as e:
        state.status_message = f"Error loading phonon DOS: {e}"


def _activate_eos(reader: QVFReader, state, section) -> None:
    """Lazy activation of an equation_of_state section (2D side panel)."""
    from vibeview.renderers.eos import EquationOfStateRenderer

    renderer = EquationOfStateRenderer(section, reader)
    try:
        state.eos_html = renderer.render_to_html()
        state.eos_title = "Equation of State"
        state.status_message = f"Loaded equation of state: {section.id}"
    except QVFError as e:
        state.status_message = f"Error loading equation of state: {e}"


def _activate_spectra(reader: QVFReader, state, section, all_readers=None) -> None:
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


def _activate_trajectory(reader: QVFReader, state, section) -> None:
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


def _activate_vibrations(reader: QVFReader, state, section) -> None:
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


def _activate_atom_properties(reader: QVFReader, plotter: pv.Plotter, state, section) -> None:
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
    state,
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
    state,
    data=None,
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
    state,
    data=None,
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
    def _nonempty(arr):
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
    if not _nonempty(charges):
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


def _remove_atom_charge_labels(plotter: pv.Plotter) -> None:
    """Strip any prior charge-label overlay before drawing a new one."""
    try:
        plotter.remove_actor("atom_charge_labels")
    except Exception:  # noqa: BLE001 — no-op when not yet present
        pass


def _remove_atom_index_labels(plotter: pv.Plotter) -> None:
    """Hide structure index labels when charge labels are about to be
    drawn at the same positions — avoids two overlapping label sets."""
    try:
        plotter.remove_actor("atom_index_labels")
    except Exception:
        pass


def _restore_cpk_atoms(reader: QVFReader, plotter: pv.Plotter, state) -> None:
    """Re-draw atom spheres with their default CPK colour palette.

    Called when the user switches away from an atom_properties section
    whose ``color_by_charge`` toggle left the atoms in the charge-tinted
    red/blue scheme instead of the per-element Jmol colours.
    """
    from vibeview.renderers.structure import StructureRenderer

    structure_section = next((s for s in reader.sections if s.kind == "structure"), None)
    if structure_section is None:
        return
    # Clear the charge-tinted per-atom spheres AND any replica / glyph actors
    # from the overlay, then rebuild the structure in CPK at the *current*
    # replication. Delegating to the structure renderer means a replicated
    # periodic cell is restored via glyph instancing (matching the default
    # view) instead of leaving the overlay's per-replica spheres behind.
    _prepare_scene_actor_replacement(plotter, state)
    _remove_actors_by_prefix(plotter, "atom_")
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
    )
    _apply_scene_appearance(plotter, state)


def _activate_citations(reader: QVFReader, state, section) -> None:
    """Lazy activation of a citations section."""
    from vibeview.renderers.citations import CitationsRenderer

    renderer = CitationsRenderer(section, reader)
    try:
        state.properties_html = renderer.render_to_html()
        state.properties_title = "Citations"
        state.status_message = f"Loaded citations: {section.id}"
    except QVFError as e:
        state.status_message = f"Error loading citations: {e}"


def _activate_run_record(reader: QVFReader, state, section) -> None:
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


def _activate_job_spec(reader: QVFReader, state, section) -> None:
    """Lazy activation of a job.spec section (declarative job request)."""
    from vibeview.renderers.job_spec import JobSpecRenderer

    renderer = JobSpecRenderer(section, reader)
    try:
        data = renderer.load()
        state.properties_html = renderer.render_to_html()
        title = f"Job Spec ({data.job_type}"
        if data.method:
            title += f", {data.method}"
        title += ")"
        state.properties_title = title
        state.status_message = f"Loaded job spec: {section.id}"
    except QVFError as e:
        state.status_message = f"Error loading job spec: {e}"


def _activate_bond_orders(reader: QVFReader, state, section) -> None:
    """Lazy activation of a bond_orders section."""
    from vibeview.renderers.bond_orders import BondOrdersRenderer

    renderer = BondOrdersRenderer(section, reader)
    try:
        data = renderer.load()
        state.properties_html = renderer.render_to_html()
        state.properties_title = f"Bond Orders ({data.method.capitalize()})"
        state.status_message = (
            f"Loaded bond orders ({data.method}): {section.id} — {len(data.pairs)} pairs"
        )
    except QVFError as e:
        state.status_message = f"Error loading bond orders: {e}"


def _activate_dos_coop(reader: QVFReader, state, section) -> None:
    """Lazy activation of a dos.coop or dos.cohp section."""
    from vibeview.renderers.coop import COOPRenderer

    renderer = COOPRenderer(section, reader)
    try:
        # Plotly figure → chart channel (scripted iframe).
        state.chart_html = renderer.render_to_html()
        state.chart_title = renderer.kind_label
        state.status_message = f"Loaded {renderer.kind_label}: {section.id}"
    except QVFError as e:
        state.status_message = f"Error loading {renderer.kind_label}: {e}"


def _activate_topology_qtaim(reader: QVFReader, plotter: pv.Plotter, state, section) -> None:
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


def _activate_scan_surface(reader: QVFReader, state, section) -> None:
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


def _activate_scf_history(reader: QVFReader, state, section) -> None:
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


def _activate_symmetry(reader: QVFReader, state, section) -> None:
    """Lazy activation of a structure.symmetry section."""
    from vibeview.renderers.symmetry import SymmetryRenderer

    renderer = SymmetryRenderer(section, reader)
    try:
        state.properties_html = renderer.render_to_html()
        state.properties_title = "Symmetry"
        state.status_message = f"Loaded symmetry: {section.id}"
    except QVFError as e:
        state.status_message = f"Error loading symmetry: {e}"


def _activate_nmr(reader: QVFReader, state, section) -> None:
    """Lazy activation of a spectra.nmr section."""
    from vibeview.renderers.nmr import NMRRenderer

    renderer = NMRRenderer(section, reader)
    try:
        state.properties_html = renderer.render_to_html()
        state.properties_title = "NMR"
        state.status_message = f"Loaded NMR data: {section.id}"
    except QVFError as e:
        state.status_message = f"Error loading NMR data: {e}"


def _activate_epr(reader: QVFReader, state, section) -> None:
    """Lazy activation of a spectra.epr section."""
    from vibeview.renderers.epr import EPRRenderer

    renderer = EPRRenderer(section, reader)
    try:
        state.properties_html = renderer.render_to_html()
        state.properties_title = "EPR"
        state.status_message = f"Loaded EPR data: {section.id}"
    except QVFError as e:
        state.status_message = f"Error loading EPR data: {e}"


def _section_title(section) -> str:
    label = getattr(section, "label", None)
    if label:
        return str(label)
    return {
        "spectra.ir": "IR Spectrum",
        "spectra.uvvis": "UV-Vis Spectrum",
        "spectra.raman": "Raman Spectrum",
        "spectra.ecd": "ECD Spectrum",
        "spectra.vcd": "VCD Spectrum",
        "spectra.generic": "Spectrum",
    }.get(section.kind, section.kind)


def _update_trajectory_plot(reader: QVFReader, state) -> None:
    """Update the energy plot for the current trajectory / reaction-path frame."""
    from vibeview.renderers.reaction import ReactionPathRenderer
    from vibeview.renderers.trajectory import TrajectoryRenderer

    section = next((s for s in reader.sections if s.id == state.selected_section), None)
    if section is None:
        return

    if section.kind == "reaction.path":
        renderer = ReactionPathRenderer(section, reader)
        try:
            renderer.load()
            frame = state.trajectory_frame
            img_bytes = renderer.render_energy_plot(frame)
            if img_bytes:
                state.trajectory_energy_image = base64.b64encode(img_bytes).decode("utf-8")
        except QVFError:
            pass
        return

    if section.kind != "trajectory":
        return

    renderer = TrajectoryRenderer(section, reader)
    try:
        renderer.load()
        frame = state.trajectory_frame
        img_bytes = renderer.render_energy_plot(frame)
        state.trajectory_energy_image = base64.b64encode(img_bytes).decode("utf-8")
    except QVFError:
        pass


def _prepare_scene_actor_replacement(plotter: pv.Plotter, state) -> None:
    """Discard toon snapshots before their actors are removed or replaced."""
    if not bool(getattr(state, "toon_mode", False)):
        return
    from vibeview.renderers.structure import disable_toon_rendering

    # The toon renderer records pre-toon properties by actor name. Restore the
    # current actors now so replacement actors with reused names cannot consume
    # stale snapshots from the scene that is about to disappear.
    disable_toon_rendering(plotter)


def _apply_scene_appearance(plotter: pv.Plotter, state):
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
    background = preset.background_color
    plotter.set_background(background)
    state.dark_background = sum(background) < 1.5

    # Preserve scalar/CPK colours. Without base_color the material helper
    # deliberately supplies neutral grey, which is not what a scene-wide
    # appearance replay wants.
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
                is_bond=str(actor_name).startswith("bond"),
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
    state,
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
    state,
) -> None:
    """Realize persistent material/toon state on newly created iso actors."""
    _apply_scene_appearance(plotter, state)
    plotter.render()
    _push_view(plotter)


def _fail_wavefunction_surface(
    plotter: pv.Plotter,
    state,
    message: str,
    *,
    push: bool = True,
) -> None:
    """Make a failed computed render leave scene and visibility consistent."""
    with contextlib.suppress(Exception):
        _remove_actors_by_prefix(plotter, "mo_iso_")
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


def _reapply_raytrace(plotter: pv.Plotter, state) -> None:
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
    state,
) -> str | None:
    """Serialize scene replacement against worker-thread computed renders."""
    render_lock = getattr(plotter, "_vibeview_wf_render_lock", None)
    if render_lock is None:
        return _rebuild_scene_unlocked(reader, plotter, viewer_state, state)
    with render_lock:
        return _rebuild_scene_unlocked(reader, plotter, viewer_state, state)


def _rebuild_scene_unlocked(
    reader: QVFReader,
    plotter: pv.Plotter,
    viewer_state: ViewerState,
    state,
) -> str | None:
    """Rebuild the 3D scene; return an error message when replay is incomplete."""
    rebuild_error = None
    _prepare_scene_actor_replacement(plotter, state)
    plotter.clear()
    plotter.set_background("#1a1a2e")

    # Keep the renderer pass aligned with the switch. PyVista clear() retains
    # an existing pass, while other render setup can replace it, so handle
    # both states explicitly on every rebuild.
    if getattr(state, "ssao_enabled", False):
        try:
            from vibeview.material_presets import ambient_occlusion_pass

            ambient_occlusion_pass(plotter, radius=2.0, samples=16)
        except Exception:
            pass
    else:
        # PyVista clear() removes actors but retains the renderer pass. Clear
        # any stale SSAO pass before the optional ray-trace restore below.
        with contextlib.suppress(Exception):
            plotter.renderer.SetPass(None)
    _reapply_raytrace(plotter, state)  # keep the raytrace indicator truthful (L2)

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

    plotter.show_grid()
    _apply_scene_appearance(plotter, state)
    # Successful overlay helpers update status themselves. If an earlier
    # rebuild stage failed, restore that diagnostic after every later replay
    # so the UI never replaces an incomplete-scene error with false success.
    if rebuild_error is not None:
        state.status_message = rebuild_error
    plotter.view_isometric()
    plotter.render()
    # VtkLocalView caches geometry on the client; a plotter.clear() + re-add
    # leaves the client looking at the old scene unless we explicitly push.
    _push_view(plotter)
    return rebuild_error


def _get_lattice_vectors(reader: QVFReader):
    """Return the [3,3] lattice vectors from the structure section, or None."""
    try:
        structure = reader.read_structure()
        if structure.lattice_vectors is not None and any(structure.pbc):
            return structure.lattice_vectors
    except QVFError:
        pass
    return None


def _orbital_is_wrappable(section) -> bool:
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


def _remove_actors_by_prefix(plotter: pv.Plotter, prefix: str) -> None:
    """Remove all actors whose name starts with the given prefix."""
    for key in list(plotter.actors.keys()):
        if isinstance(key, str) and key.startswith(prefix):
            plotter.remove_actor(key)


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
        "atom_",
        "bond_",
        "bonds_",
        "cartoon_",
        "cell_",
        "structure_",
    ):
        _remove_actors_by_prefix(plotter, prefix)


def _update_pick_markers(reader: QVFReader, plotter: pv.Plotter, picked: list) -> None:
    """Draw translucent yellow highlight spheres on the selected atoms."""
    _remove_actors_by_prefix(plotter, "pick_marker_")
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
            color="#FFD400",
            opacity=0.4,
            name=f"pick_marker_{order}",
            show_scalar_bar=False,
        )


def _update_edit_highlights(reader: QVFReader, plotter: pv.Plotter, selected: list) -> None:
    """Draw orange wireframe highlight spheres on edit-selected atoms."""
    _remove_actors_by_prefix(plotter, "edit_highlight_")
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
            color="#FF6600",
            opacity=0.3,
            style="wireframe",
            name=f"edit_highlight_{order}",
            show_scalar_bar=False,
        )


def _update_frozen_highlights(reader: QVFReader, plotter: pv.Plotter, frozen: list) -> None:
    """Draw blue 'locked' wireframe spheres on atoms held fixed during live-opt.

    Distinct from the orange edit-selection highlight (``_update_edit_highlights``)
    so a frozen atom reads differently from a merely-selected one.
    """
    _remove_actors_by_prefix(plotter, "frozen_highlight_")
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
            color="#29B6F6",
            opacity=0.28,
            style="wireframe",
            line_width=2,
            name=f"frozen_highlight_{order}",
            show_scalar_bar=False,
        )


def _reset_frozen(plotter: pv.Plotter, state) -> None:
    """Clear the frozen-atom set and its markers.

    Called wherever atom indices are reindexed (delete / undo / redo / reload),
    since a frozen index no longer points at the same atom afterward.
    """
    if state.frozen_atoms:
        state.frozen_atoms = []
    _remove_actors_by_prefix(plotter, "frozen_highlight_")


def _structure_edit_snapshot(structure) -> dict:
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
    reader,
    plotter,
    positions_list,
    symbols_list=None,
    *,
    lattice_vectors=None,
) -> None:
    """Rebuild the structure renderer with new atom positions.

    Removes existing atom/bond actors and draws fresh spheres + bonds
    with the given positions and (optionally) symbols. When supplied,
    ``lattice_vectors`` replaces the edit-overlay cell in the same operation.
    """
    import numpy as np
    import pyvista as pv

    from vibeview.converters import _SYMBOL_TO_Z
    from vibeview.renderers.structure import _draw_unit_cell, cpk_color, cpk_radius

    # Remove old structure actors (original and any previous edit-mode actors)
    for prefix in ("atom_", "bond_", "cell_", "structure_"):
        _remove_actors_by_prefix(plotter, prefix)

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

    for i, (symbol, pos) in enumerate(zip(symbols_list, positions)):
        z = _SYMBOL_TO_Z.get(symbol, 6)
        radius = cpk_radius(z, 0.35)
        sphere = pv.Sphere(radius=radius, center=pos, theta_resolution=12, phi_resolution=12)
        plotter.add_mesh(
            sphere,
            color=cpk_color(z),
            smooth_shading=True,
            name=f"structure_atom_{i}",
            show_scalar_bar=False,
        )

    # Auto-bond
    _auto_bond(plotter, symbols_list, positions)
    # Ordinary edit rebuilds historically removed the unit-cell actors and
    # never put them back. A supercell therefore looked cell-less even after
    # its lattice became correct. Draw the reader's current periodic cell so
    # undo/redo and later atom edits keep the wireframe in sync with exports.
    structure = reader.read_structure()
    if structure.lattice_vectors is not None and any(structure.pbc):
        _draw_unit_cell(plotter, structure.lattice_vectors, structure.pbc)
    plotter.render()


def _auto_bond(plotter, symbols, positions, tolerance=1.2):
    """Auto-bond based on covalent radii."""
    import numpy as np
    import pyvista as pv

    from vibeview.converters import _SYMBOL_TO_Z
    from vibeview.qvf import _COVALENT_RADII

    z_list = [_SYMBOL_TO_Z.get(s, 6) for s in symbols]
    radii = [_COVALENT_RADII.get(z, 0.8) for z in z_list]
    pos = np.array(positions, dtype=float)
    n = len(symbols)
    for i in range(n):
        for j in range(i + 1, n):
            d = float(np.linalg.norm(pos[i] - pos[j]))
            # Coincident atoms have no bond direction: Cylinder(direction=0)
            # produces a NaN transform and ValueError('matrix must have
            # finite values'), killing the whole scene rebuild. Skip the
            # bond and keep rendering — wherever the overlap came from.
            if d < 1e-6:
                continue
            threshold = (radii[i] + radii[j]) * tolerance
            if d < threshold:
                start, end = pos[i], pos[j]
                direction = end - start
                center = (start + end) / 2.0
                cylinder = pv.Cylinder(center=center, direction=direction, radius=0.08, height=d)
                plotter.add_mesh(
                    cylinder,
                    color="#888888",
                    name=f"structure_bond_{i}_{j}",
                    show_scalar_bar=False,
                )


def _add_atom_to_structure(reader, plotter, position, symbol="C"):
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


def _measure_text(sdata, picked: list) -> str:
    """Build the measurement readout from the picked-atom order:
    1 → label, 2 → distance, 3 → distance chain + angle, 4 → + dihedral."""
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
        lines.append(f"Distance {_lbl(picked[0])}–{_lbl(picked[1])}: {d:.3f} Å")
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
            f"Dihedral {_lbl(picked[0])}–{_lbl(picked[1])}–{_lbl(picked[2])}–{_lbl(picked[3])}: {dih:.1f}°"
        )
    return "\n".join(lines)


def _rebuild_crossfade(
    reader: QVFReader,
    plotter: pv.Plotter,
    viewer_state: ViewerState,
    state,
    section_id_a: str,
    section_id_b: str,
    blend: float,
) -> None:
    """Render two volume isosurfaces with blended opacity.

    ``blend=0`` shows only section A, ``blend=1`` shows only section B.
    Both isosurfaces are rendered simultaneously with adjusted opacity.
    """
    from vibeview.renderers.volume import VolumeRenderer

    _remove_actors_by_prefix(plotter, "volume_")
    lattice = _get_lattice_vectors(reader)
    rep = viewer_state.replication

    for section_id, opacity_scale in [(section_id_a, 1.0 - blend), (section_id_b, blend)]:
        section = next((s for s in reader.sections if s.id == section_id), None)
        if section is None:
            continue

        renderer = VolumeRenderer(section, reader)
        try:
            renderer.load_grid()
            renderer.load_data()
        except QVFError:
            continue

        hints = viewer_state.get_volume_hints(section_id, kind=section.kind)
        mesh = renderer.make_mesh(hints, replication=rep, lattice_vectors=lattice)
        if mesh is None:
            continue

        opacity = hints.opacity * opacity_scale
        if opacity < 0.01:
            continue

        plotter.add_mesh(
            mesh,
            scalars="values",
            cmap=hints.colormap,
            opacity=opacity,
            name=f"volume_{section_id}",
            show_scalar_bar=False,
        )

    plotter.render()
    _push_view(plotter)


def _render_trajectory_atoms(reader: QVFReader, plotter: pv.Plotter, state) -> None:
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
    _remove_actors_by_prefix(plotter, "traj_atom_")
    _remove_actors_by_prefix(plotter, "traj_cell_")
    _remove_actors_by_prefix(plotter, "traj_volume_")

    znums = [a.atomic_number for a in renderer.load().atoms]
    ball_scale = 0.35
    for i, (symbol, pos) in enumerate(atoms):
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
                    color="#444444",
                    line_width=2,
                    name=f"traj_cell_{i}",
                )

    # Per-frame density isosurface for reaction.path archives carrying
    # volumes (W1). Morphs as the frame changes; absent (method returns
    # None) on geometry-only paths. Wrapped defensively — a contouring
    # failure must not break the geometry animation.
    vol_mesh_fn = getattr(renderer, "volume_mesh_for_frame", None)
    if vol_mesh_fn is not None:
        try:
            iso_mesh = vol_mesh_fn(frame)
            if iso_mesh is not None and iso_mesh.n_points > 0:
                plotter.add_mesh(
                    iso_mesh,
                    color="#3DDC84",
                    opacity=0.45,
                    smooth_shading=True,
                    name="traj_volume_0",
                )
        except Exception:  # noqa: BLE001 — viz overlay must not crash anim
            pass

    plotter.render()
    _push_view(plotter)


def _activate_reaction_path(reader: QVFReader, state, section) -> None:
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


def _activate_reaction_waypoints(reader: QVFReader, state, section) -> None:
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


def _activate_wavefunction(reader: QVFReader, state, section) -> None:
    """Lazy activation of a wavefunction.gto section."""
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    renderer = WavefunctionRenderer(section, reader)
    try:
        wf = renderer.load()
        section_id = section.id
        rows = renderer.mo_table()
        state.wf_section_id = section_id
        state.wf_mo_rows = rows
        # Default the picker to the HOMO (most-requested orbital), keyed on
        # the composite "{spin}:{index}" value the dropdown now uses.
        default = next((r for r in rows if " HOMO" in r["title"]), rows[0] if rows else None)
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


def _parse_mo_key(mo_key) -> tuple[str, int]:
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


def _step_mo_selection(state, direction: int) -> str | None:
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
    selected = rows[new_idx].get("value", current)
    state.wf_selected_mo = selected
    return selected


def _render_mo_volume(
    reader: QVFReader,
    plotter: pv.Plotter,
    viewer_state: ViewerState,
    state,
    section,
    mo_index: int,
    spin: str,
    *,
    update_state: bool = True,
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
    grid, values = wf_renderer.evaluate_mo(
        mo_index,
        spin=spin,
        n_per_dim=n_per_dim,
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

    _remove_actors_by_prefix(plotter, "mo_iso_")

    # PyVista renamed UniformGrid → ImageData (gone since 0.44). For
    # point-centred scalar data, ``dimensions`` must equal the grid shape
    # (one point per voxel), NOT shape+1 (that is cell-data sizing and
    # mismatches the point_data length below).
    pv_grid = pv.ImageData(
        dimensions=(grid.shape[0], grid.shape[1], grid.shape[2]),
        spacing=(
            float(grid.voxel_vectors[0, 0]),
            float(grid.voxel_vectors[1, 1]),
            float(grid.voxel_vectors[2, 2]),
        ),
        origin=tuple(float(x) for x in grid.origin),
    )
    pv_grid.point_data["values"] = values.ravel(order="F")
    _t = time.perf_counter()
    contour_pos = pv_grid.contour(isosurfaces=[+iso_pos], scalars="values")
    contour_neg = pv_grid.contour(isosurfaces=[-iso_pos], scalars="values")
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

    if contour_pos.n_points:
        plotter.add_mesh(
            contour_pos,
            color="#1f77b4",
            opacity=_mo_opacity,
            name="mo_iso_pos",
            show_scalar_bar=False,
        )
    if contour_neg.n_points:
        plotter.add_mesh(
            contour_neg,
            color="#d62728",
            opacity=_mo_opacity,
            name="mo_iso_neg",
            show_scalar_bar=False,
        )
    _log.info(
        "render_mo: actors ready (%d + %d isosurface points)",
        contour_pos.n_points,
        contour_neg.n_points,
    )
    msg = f"Rendered MO #{mo_index} ({spin}) at |iso|={iso_pos:.3f}"
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


def _render_wf_density(
    reader: QVFReader,
    plotter: pv.Plotter,
    viewer_state: ViewerState,
    state,
    section,
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

    _remove_actors_by_prefix(plotter, "mo_iso_")
    pv_grid = pv.ImageData(
        dimensions=(grid.shape[0], grid.shape[1], grid.shape[2]),
        spacing=(
            float(grid.voxel_vectors[0, 0]),
            float(grid.voxel_vectors[1, 1]),
            float(grid.voxel_vectors[2, 2]),
        ),
        origin=tuple(float(x) for x in grid.origin),
    )
    pv_grid.point_data["values"] = rho.ravel(order="F")
    iso = max(float(state.isovalue or 0.05), 0.001)

    # The total density is positive everywhere, so one unsigned surface says
    # it all. The spin density is signed -- excess alpha against excess beta,
    # and the negative lobes from spin polarisation are the interesting part
    # -- so it gets a pair, coloured like an MO's phases.
    levels = (
        ((iso, "#cc3333", "pos"), (-iso, "#3366cc", "neg"))
        if spin_mode
        else ((iso, "#3366cc", "density"),)
    )

    lattice = _get_lattice_vectors(reader)
    rep = viewer_state.replication
    drawn = 0
    for level, colour, tag in levels:
        contour = pv_grid.contour(isosurfaces=[level], scalars="values")
        # Replicate across periodic images for crystalline systems.
        if lattice is not None and any(r > 1 for r in rep) and contour.n_points:
            from vibeview.renderers.volume import _replicate_mesh

            contour = _replicate_mesh(contour, lattice, rep)
        if contour.n_points:
            plotter.add_mesh(
                contour,
                color=colour,
                opacity=0.55,
                name=f"mo_iso_{tag}",
                show_scalar_bar=False,
            )
            drawn += 1

    if spin_mode:
        message = (
            "Computed spin density (red = excess α, blue = excess β): "
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
    state,
    section,
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

    _remove_actors_by_prefix(plotter, "mo_iso_")
    pv_grid = pv.ImageData(
        dimensions=(grid.shape[0], grid.shape[1], grid.shape[2]),
        spacing=(
            float(grid.voxel_vectors[0, 0]),
            float(grid.voxel_vectors[1, 1]),
            float(grid.voxel_vectors[2, 2]),
        ),
        origin=tuple(float(x) for x in grid.origin),
    )
    pv_grid.point_data["values"] = elf.ravel(order="F")
    iso = min(max(float(state.wf_elf_iso or 0.8), 0.05), 0.95)
    contour = pv_grid.contour(isosurfaces=[iso], scalars="values")

    lattice = _get_lattice_vectors(reader)
    rep = viewer_state.replication
    if lattice is not None and any(r > 1 for r in rep) and contour.n_points:
        from vibeview.renderers.volume import _replicate_mesh

        contour = _replicate_mesh(contour, lattice, rep)

    if contour.n_points:
        plotter.add_mesh(
            contour,
            color="#00b894",
            opacity=0.6,
            name="mo_iso_elf",
            show_scalar_bar=False,
        )
    message = (
        f"Computed ELF (Becke-Edgecombe): isosurface at {iso:g}; "
        f"max {float(elf.max()):.3f}"
    )
    if not contour.n_points:
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
    state,
    section,
    *,
    update_state: bool = True,
) -> str:
    """Render the NCI surface: the reduced-gradient isosurface, coloured by
    sign(lambda2)*rho.

    Johnson et al. 2010. The surface locates the noncovalent interactions;
    the colour says what kind. Blue is attractive (hydrogen bonding), green
    is weak van der Waals, red is steric repulsion -- the field is clamped to
    [-0.05, +0.05] au, the range their figures use, so the scale means the
    same thing from one molecule to the next.
    """
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    wf_renderer = WavefunctionRenderer(section, reader)
    grid, rdg, signed_rho = wf_renderer.evaluate_nci(
        n_per_dim=int(state.wf_n_per_dim or 60)
    )

    _remove_actors_by_prefix(plotter, "mo_iso_")
    pv_grid = pv.ImageData(
        dimensions=(grid.shape[0], grid.shape[1], grid.shape[2]),
        spacing=(
            float(grid.voxel_vectors[0, 0]),
            float(grid.voxel_vectors[1, 1]),
            float(grid.voxel_vectors[2, 2]),
        ),
        origin=tuple(float(x) for x in grid.origin),
    )
    pv_grid.point_data["rdg"] = rdg.ravel(order="F")
    pv_grid.point_data["signed_rho"] = signed_rho.ravel(order="F")
    iso = min(max(float(state.wf_nci_iso or 0.5), 0.05), 2.0)
    contour = pv_grid.contour(isosurfaces=[iso], scalars="rdg")

    lattice = _get_lattice_vectors(reader)
    rep = viewer_state.replication
    if lattice is not None and any(r > 1 for r in rep) and contour.n_points:
        from vibeview.renderers.volume import _replicate_mesh

        contour = _replicate_mesh(contour, lattice, rep)

    if contour.n_points and "signed_rho" in contour.point_data:
        plotter.add_mesh(
            contour,
            scalars="signed_rho",
            cmap="coolwarm",
            clim=(-0.05, 0.05),
            opacity=0.75,
            name="mo_iso_nci",
            show_scalar_bar=False,
        )
    elif contour.n_points:
        plotter.add_mesh(
            contour, color="#00b894", opacity=0.7, name="mo_iso_nci",
            show_scalar_bar=False,
        )
    message = (
        f"Computed NCI (Johnson 2010): s = {iso:g} isosurface, "
        "blue attractive / green vdW / red steric"
    )
    if not contour.n_points:
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
    state,
    section,
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

    _remove_actors_by_prefix(plotter, "mo_iso_")
    pv_grid = pv.ImageData(
        dimensions=(grid.shape[0], grid.shape[1], grid.shape[2]),
        spacing=(
            float(grid.voxel_vectors[0, 0]),
            float(grid.voxel_vectors[1, 1]),
            float(grid.voxel_vectors[2, 2]),
        ),
        origin=tuple(float(x) for x in grid.origin),
    )
    pv_grid.point_data["values"] = laplacian.ravel(order="F")
    iso = max(float(state.wf_laplacian_iso or 1.0), 1e-3)

    lattice = _get_lattice_vectors(reader)
    rep = viewer_state.replication
    drawn = 0
    for level, colour, tag in (
        (-iso, "#8e44ad", "lapneg"),   # charge concentration
        (iso, "#f39c12", "lappos"),    # charge depletion
    ):
        contour = pv_grid.contour(isosurfaces=[level], scalars="values")
        if lattice is not None and any(r > 1 for r in rep) and contour.n_points:
            from vibeview.renderers.volume import _replicate_mesh

            contour = _replicate_mesh(contour, lattice, rep)
        if contour.n_points:
            plotter.add_mesh(
                contour, color=colour, opacity=0.55,
                name=f"mo_iso_{tag}", show_scalar_bar=False,
            )
            drawn += 1
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


def _update_reaction_label(reader: QVFReader, state) -> None:
    """Set state.reaction_current_label to the waypoint at the current frame, if any."""
    frame = int(state.trajectory_frame or 0)
    waypoints = state.reaction_waypoints or []
    for wp in waypoints:
        if int(wp.get("frame_index", -1)) == frame:
            state.reaction_current_label = f"{wp.get('label', '')} ({wp.get('kind', '')})"
            return
    state.reaction_current_label = ""


def _camera_to_dict(plotter: pv.Plotter) -> dict:
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
        out = {
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


def _apply_camera(plotter: pv.Plotter, camera: dict | None) -> None:
    """Apply a VTK camera dict to the plotter, if well-formed."""
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
    push = getattr(plotter, "_vibe_view_push_camera", None)
    if push is not None:
        try:
            push()
        except Exception:  # noqa: BLE001 — camera push is best-effort
            pass


def _render_vibration_atoms(
    reader: QVFReader,
    plotter: pv.Plotter,
    state,
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
    _remove_actors_by_prefix(plotter, "vib_atom_")

    # Atomic numbers (for correct CPK colour/radius) from the loaded
    # vibrations atoms, index-aligned with displace_atoms' output.
    znums = [a.atomic_number for a in renderer.load().atoms]
    ball_scale = 0.35
    for i, (symbol, pos) in enumerate(atoms):
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


def _clip_to_planes(mesh, fx: float, fy: float, fz: float, bounds):
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


def _rebuild_clip(reader, plotter, viewer_state, state) -> None:
    """Apply axis-aligned clip planes to the active volume isosurface.

    For signed kinds (``volume.difference`` / ``volume.spin``) both the
    positive (blue) and negative (red) lobes are clipped and shown — the
    old single-mesh path only contoured the +isovalue, silently dropping the
    negative lobe of a clipped difference/spin density (audit finding A2-05).
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
        try:
            plotter.remove_actor(nm)
        except (KeyError, ValueError):
            pass
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
        _remove_actors_by_prefix(plotter, f"volume_{state.active_volume_id}")
        for nm in _CLIP_ACTOR_NAMES:
            try:
                plotter.remove_actor(nm)
            except (KeyError, ValueError):
                pass
        try:
            from vibeview.renderers.volume import _BOHR_TO_ANGSTROM, VolumeRenderer

            renderer = VolumeRenderer(section, reader)
            grid = renderer.load_grid()
            data = renderer.load_data()
            hints = viewer_state.get_volume_hints(state.active_volume_id, kind=section.kind)
            fx, fy, fz = state.clip_x, state.clip_y, state.clip_z
            # Build a PyVista ImageData for the full volume, then use
            # its built-in .slice() to extract an orthogonal 2D plane.
            vox = grid.voxel_vectors * _BOHR_TO_ANGSTROM
            origin_ang = grid.origin * _BOHR_TO_ANGSTROM
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
            slc = vol.slice(normal=axis_label, origin=origin)
            if slc.n_points > 0:
                plotter.add_mesh(
                    slc,
                    name="_volume_slice",
                    scalars="values",
                    cmap=state.colormap or hints.colormap,
                    opacity=0.85,
                    show_scalar_bar=False,
                )
                plotter.add_mesh(
                    slc.outline(),
                    name="_volume_slice_outline",
                    color="#ffffff",
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
        _remove_actors_by_prefix(plotter, f"volume_{state.active_volume_id}")
        renderer = VolumeRenderer(section, reader)
        hints = viewer_state.get_volume_hints(state.active_volume_id, kind=section.kind)
        if state.isovalue:
            hints.isovalue = state.isovalue
        fx, fy, fz = state.clip_x, state.clip_y, state.clip_z

        if section.kind in ("volume.difference", "volume.spin"):
            raw = renderer.load_data()
            iso = hints.isovalue
            pos = _build_signed_contour(renderer, np.clip(raw, 0, None), iso, (1, 1, 1), None)
            neg = _build_signed_contour(renderer, np.clip(-raw, 0, None), iso, (1, 1, 1), None)
            meshes = [m for m in (pos, neg) if m is not None and m.n_points]
            if not meshes:
                return
            # Shared clip-plane frame across both lobes (union of bounds).
            b = [m.bounds for m in meshes]
            bounds = (
                min(x[0] for x in b),
                max(x[1] for x in b),
                min(x[2] for x in b),
                max(x[3] for x in b),
                min(x[4] for x in b),
                max(x[5] for x in b),
            )
            for mesh, color, name in (
                (pos, "#3366cc", "_volume_clipped_pos"),
                (neg, "#cc3333", "_volume_clipped_neg"),
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
                    cmap=state.colormap or hints.colormap,
                    opacity=state.opacity or hints.opacity,
                    show_scalar_bar=False,
                )
    except Exception as e:  # noqa: BLE001
        state.status_message = f"Clip error: {e}"
    retired_actors.clear()
