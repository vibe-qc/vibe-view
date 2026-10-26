"""The sidebar and section list: one row per section, the orbital and
basis-function groups, and the titles and icons they show.

Part of the ``vibeview.app`` package; see its ``__init__`` for ``create_app``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from vibeview.kinds import classify_section

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
    "volume.transition_density": "Transition Density",
    "volume.laplacian": "Laplacian of the Density",
    "volume.vector": "Vector Field",
    "volume.complex": "Complex Field",
    "volume.series": "Volume Series",
    "forces": "Forces",
    "hessian": "Hessian",
    "properties.electric": "Electric Properties",
    "properties.elastic": "Elastic Constants",
    "properties.dielectric": "Dielectric Properties",
    "wavefunction.bloch": "Bloch Orbitals",
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
    "reaction.scheme": "Reaction Scheme",
    "preview": "Preview",
    "excited_states": "Excited States",
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
    "volume.transition_density": "mdi-transition",
    "volume.laplacian": "mdi-delta",
    "volume.vector": "mdi-arrow-top-right-thin",
    "volume.complex": "mdi-orbit",
    "volume.series": "mdi-layers",
    "forces": "mdi-arrow-expand-all",
    "hessian": "mdi-grid",
    "properties.electric": "mdi-flash",
    "properties.elastic": "mdi-cube-outline",
    "properties.dielectric": "mdi-table",
    "wavefunction.bloch": "mdi-orbit",
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
    "reaction.scheme": "mdi-arrow-right-bold-outline",
    "preview": "mdi-image-outline",
    "excited_states": "mdi-lightbulb-on-outline",
}

_DEFAULT_KIND_ICON = "mdi-file-document-outline"

def _build_section_list(reader: QVFReader) -> list[dict[str, Any]]:
    """One row per section: kind, render status, detail and flags.

    Built by the first load and by every file switch or reload, which kept
    their own copies (#37-25).
    """
    rows: list[dict[str, Any]] = []
    n_structures = len(reader.structure_sections)
    for s in reader.sections:
        status, detail = classify_section(s.kind)
        err = reader.section_error(s.id)
        if err:
            status = "error"
            detail = err
        label = (s.model_extra or {}).get("label")
        row: dict[str, Any] = {
            "id": s.id,
            "kind": s.kind,
            "status": status,
            "detail": detail or "",
            "supported": s.kind in _supported_kinds_set(),
            # producer marked it still growing (live checkpoint, M4)
            "partial": reader.section_is_partial(s.id),
            # The generic display label (spec § 4.1); for a structure the
            # compound label of its chemical identity (§ 5.1).
            "label": label if isinstance(label, str) else "",
        }
        if s.kind == "structure":
            identity = reader.structure_identity(s.id)
            row["name"] = identity.name or ""
            row["charge"] = identity.charge
            row["multiplicity"] = identity.multiplicity
            row["n_structures"] = n_structures
        rows.append(row)
    return rows

def _sidebar_group_entry(
    group_id: str, title: str, kind: str, members: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """The one sidebar row several sections of ``kind`` collapse into."""
    supported = all(s["supported"] for s in members)
    worst_status = (
        "error" if any(s["status"] == "error" for s in members) else members[0]["status"]
    )
    return {
        "id": group_id,
        "title": title,
        "subtitle": f"{kind} — {worst_status}",
        "status": worst_status,
        "supported": supported,
        "icon": _status_icon(worst_status),
        "kind_icon": _kind_icon(kind),
        "warn": worst_status == "error" or not supported,
        "disabled": not supported,
    }

def _build_sidebar_entries(
    reader: QVFReader, section_list: Sequence[dict[str, Any]], is_periodic: bool
) -> list[dict[str, Any]]:
    """The sidebar: the optional IUPAC name header, one row per section, and
    one group row each for the orbital and basis-function sections, which the
    right-panel pickers then switch between. ``bonds`` has no row of its own:
    the structure view draws it."""
    entries: list[dict[str, Any]] = []
    iupac = _iupac_name_entry(reader)
    if iupac is not None:
        entries.append(iupac)
    for s in section_list:
        if s["kind"] in ("volume.orbital", "basis.ao", "bonds"):
            continue
        subtitle = f"{s['kind']} — {s['status']}"
        if s.get("charge"):
            subtitle += f" · charge {s['charge']:+d}"
        if s.get("multiplicity") not in (None, 1):
            subtitle += f" · multiplicity {s['multiplicity']}"
        if s.get("partial"):
            subtitle += " · streaming"
        entries.append(
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
    orbitals = [s for s in section_list if s["kind"] == "volume.orbital"]
    if orbitals:
        entries.append(
            _sidebar_group_entry(
                _ORBITAL_GROUP_ID,
                _orbital_group_sidebar_title(len(orbitals), is_periodic),
                "volume.orbital",
                orbitals,
            )
        )
    aos = [s for s in section_list if s["kind"] == "basis.ao"]
    if aos:
        entries.append(
            _sidebar_group_entry(
                _BASIS_AO_GROUP_ID, f"Basis Functions ({len(aos)})", "basis.ao", aos
            )
        )
    return entries

def _sidebar_section_title(section: dict[str, Any]) -> str:
    """Sidebar title for one section.

    Mostly a ``_KIND_TITLES`` lookup, but several sections legitimately share
    the ``wavefunction.gto`` kind — the canonical set (``wf``), a localized
    set (``wf_localized``) and any NTO pairs (``wf_nto_*``) — and the plain
    lookup gave all of them the identical title "Wavefunction", leaving the
    sidebar with indistinguishable rows. Section ids are unique by spec, so
    they are the reliable discriminator.
    """
    kind = section.get("kind", "")
    if kind == "structure":
        return _structure_title(section)
    if kind == "reaction.scheme" and section.get("label"):
        # A scheme's generic label titles it (spec § 5.14).
        return str(section["label"])
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
                "ibo_fresh_rhf": "IBO, new RHF",
                "boys_fresh_rhf": "Boys, new RHF",
                "pipek_mezey_fresh_rhf": "Pipek-Mezey, new RHF",
            }.get(criterion, criterion.replace("_", "-").title())
            return f"{label} ({pretty})" if pretty else label
        if section_id.startswith("wf_nto"):
            if section_id.endswith("_hole"):
                return "NTO (hole)"
            if section_id.endswith("_electron"):
                return "NTO (electron)"
            return "Natural Transition Orbitals"
    return str(_KIND_TITLES.get(kind) or _plugin_title(kind) or kind)


def _plugin_title(kind: str) -> str | None:
    """The title an installed renderer plugin gives ``kind`` (#113)."""
    from vibeview.renderer_plugins import get_renderer_plugin

    try:
        spec = get_renderer_plugin(kind)
    except Exception:  # noqa: BLE001 — a plugin never breaks the sidebar
        return None
    return spec.title if spec is not None else None

def _structure_title(section: dict[str, Any]) -> str:
    """Sidebar title for a structure: its compound label and name (spec § 5.1).

    ``"1 · methane"``, either part alone, or ``"Structure"``. An archive with
    several structures that state neither shows each one's id, so the rows
    can be told apart (a vibe-sketch document holds one per species).
    """
    title = " · ".join(str(part) for part in (section.get("label"), section.get("name")) if part)
    if title:
        return title
    if int(section.get("n_structures") or 0) > 1:
        return f"Structure · {section.get('id', '')}"
    return "Structure"


def _kind_icon(kind: str) -> str:
    """Sidebar glyph for a section kind: the built-in table, an installed
    renderer plugin's icon (#113), else a generic document."""
    icon = _KIND_ICONS.get(kind)
    if icon is not None:
        return icon
    from vibeview.renderer_plugins import get_renderer_plugin

    try:
        spec = get_renderer_plugin(kind)
    except Exception:  # noqa: BLE001 — a plugin never breaks the sidebar
        spec = None
    return spec.icon if spec is not None else _DEFAULT_KIND_ICON

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

def _builder_name(reader: QVFReader) -> str | None:
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

def _iupac_name_entry(reader: QVFReader) -> dict[str, Any] | None:
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
    from vibeview.renderer_plugins import plugin_kinds

    return SUPPORTED_KINDS | plugin_kinds()
