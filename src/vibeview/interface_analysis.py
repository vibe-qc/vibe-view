"""Tube–metal interface analysis from existing QVF sections (roadmap: nanotubes).

* :func:`metal_carbon_contacts` lists metal–C pairs within bonding range
  with their lengths and, where the archive has a ``bond_orders`` section,
  their orders.
* :func:`group_pdos` sums a ``dos.projected`` section's channels over a
  group of atoms (an interface layer, an open end), using each channel's
  ``atom`` index (QVF spec § 5.6).
* :func:`axial_profile` reduces a volume onto the tube axis: a density
  difference is integrated over each plane perpendicular to the axis, which
  gives a line density in e/Å and, accumulated along the axis, the charge
  transferred across each plane; a potential is averaged over each plane.

No schema change is needed: everything reads tables and sections the
format already has.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from vibeview.elements import SYMBOL_TO_Z, covalent_radius
from vibeview.structure_views import AxisFrame, axis_frame, is_metal
from vibeview.units import BOHR_TO_ANGSTROM

if TYPE_CHECKING:
    from vibeview.qvf import DOSData, QVFReader, StructureData

#: Added to the covalent-radius sum to count a metal–C pair as a contact (Å).
CONTACT_PAD = 0.45
#: Volume kinds whose profile is an integral over each plane (charge-like).
INTEGRATED_KINDS = frozenset({"volume.density", "volume.difference", "volume.spin"})


def _z(atom: Any) -> int:
    return int(atom.atomic_number or SYMBOL_TO_Z.get(atom.symbol, 0))


def _bond_order_lookup(reader: QVFReader | None) -> tuple[dict[tuple[int, int], float], str]:
    if reader is None:
        return {}, ""
    for section in reader.sections:
        if section.kind == "bond_orders":
            data = reader.read_bond_orders(section.id)
            orders = {}
            for pair in data.pairs:
                i, j, order = pair.get("i"), pair.get("j"), pair.get("order")
                if (isinstance(i, int) and isinstance(j, int)
                        and isinstance(order, (int, float))):
                    orders[(min(i, j), max(i, j))] = float(order)
            return orders, str(data.method)
    return {}, ""


def metal_carbon_contacts(structure: StructureData, reader: QVFReader | None = None,
                          pad: float = CONTACT_PAD) -> list[dict[str, Any]]:
    """Metal–C pairs closer than their covalent radii plus ``pad``, shortest first.

    Each row has ``metal`` and ``carbon`` (0-based indices), their labels
    (``Cu12``, 1-based as everywhere in the viewer), ``distance`` in Å, and
    ``order`` and ``method`` from the archive's bond-order section, or None
    when it has none or does not list the pair.
    """
    zs = [_z(a) for a in structure.atoms]
    positions = np.array([a.position for a in structure.atoms], dtype=float).reshape(-1, 3)
    metals = [i for i, z in enumerate(zs) if is_metal(z)]
    carbons = np.array([i for i, z in enumerate(zs) if z == 6], dtype=int)
    if not metals or not len(carbons):
        return []
    orders, method = _bond_order_lookup(reader)
    r_c = covalent_radius(6, 0.76)
    rows: list[dict[str, Any]] = []
    for m in metals:
        limit = covalent_radius(zs[m], 1.5) + r_c + pad
        d = np.linalg.norm(positions[carbons] - positions[m], axis=1)
        for k in np.flatnonzero(d < limit):
            c = int(carbons[k])
            order = orders.get((min(m, c), max(m, c)))
            rows.append({
                "metal": m, "carbon": c,
                "metal_label": f"{structure.atoms[m].symbol}{m + 1}",
                "carbon_label": f"C{c + 1}",
                "distance": float(d[k]),
                "order": order,
                "method": method if order is not None else None,
            })
    rows.sort(key=lambda r: r["distance"])
    return rows


def interface_atoms(structure: StructureData, pad: float = CONTACT_PAD) -> list[int]:
    """Every atom in a metal–C contact: the interface layer on both sides."""
    found: set[int] = set()
    for row in metal_carbon_contacts(structure, None, pad):
        found.update((row["metal"], row["carbon"]))
    return sorted(found)


@dataclass
class GroupDOS:
    energies: np.ndarray
    values: np.ndarray
    channels: int
    atoms: list[int]


def group_pdos(dos: DOSData, atoms: list[int] | set[int]) -> GroupDOS:
    """Sum the projected DOS over the channels of ``atoms`` (0-based).

    Raises ``ValueError`` when the section is a total DOS, when its channels
    carry no ``atom`` index, or when none belongs to the group.
    """
    if not dos.projected:
        raise ValueError("a total DOS has no atom projections; choose a dos.projected section")
    group = {int(a) for a in atoms}
    rows = [k for k, ch in enumerate(dos.channels)
            if isinstance(ch, dict) and isinstance(ch.get("atom"), int) and ch["atom"] in group]
    if not any(isinstance(ch, dict) and isinstance(ch.get("atom"), int) for ch in dos.channels):
        raise ValueError("the projected DOS channels carry no atom index")
    if not rows:
        raise ValueError("no projected-DOS channel belongs to the chosen atoms")
    values = np.asarray(dos.values, dtype=float)[rows].sum(axis=0)
    return GroupDOS(np.asarray(dos.energies, dtype=float), values, len(rows), sorted(group))


@dataclass
class AxialProfile:
    """A volume reduced onto the axis; ``position`` is in Å from the frame centre."""

    position: np.ndarray
    value: np.ndarray
    #: ``integrated`` (e/Å, plane integral) or ``averaged`` (plane mean).
    reduction: str
    #: Running integral of an integrated profile along the axis (e); None otherwise.
    cumulative: np.ndarray | None
    frame: AxisFrame


def axial_profile(reader: QVFReader, section_id: str, structure: StructureData | None = None,
                  bin_width: float = 0.1) -> AxialProfile:
    """Reduce a volume section onto the structure's axis (see the module docstring).

    Voxels are binned by their position along the axis, so the grid need
    not be aligned with it. Density-like kinds (:data:`INTEGRATED_KINDS`)
    are integrated: each bin holds the sum of value times voxel volume in
    bohr³, so a density in e/bohr³ gives electrons, divided by the bin
    width. Other kinds, such as a potential, are averaged over the bin.
    """
    if bin_width <= 0:
        raise ValueError("the bin width must be positive")
    section = reader.get_section(section_id)
    grid = reader.read_volume_grid(section_id)
    data = np.asarray(reader.read_volume_data(section_id), dtype=float)
    frame = axis_frame(structure if structure is not None else reader.read_structure())
    scale = grid.to_angstrom
    i, j, k = np.meshgrid(*(np.arange(n) for n in grid.shape), indexing="ij")
    idx = np.column_stack((i.ravel(), j.ravel(), k.ravel())).astype(float)
    points = (np.asarray(grid.origin, dtype=float) + idx @ np.asarray(grid.voxel_vectors)) * scale
    along = (points - frame.center) @ frame.axis
    lo = float(along.min())
    bins = np.floor((along - lo) / bin_width).astype(int)
    n_bins = int(bins.max()) + 1
    values = data.ravel()
    centres = lo + (np.arange(n_bins) + 0.5) * bin_width
    if section.kind in INTEGRATED_KINDS:
        voxel = abs(float(np.linalg.det(np.asarray(grid.voxel_vectors, dtype=float))))
        if grid.units == "angstrom":
            voxel /= BOHR_TO_ANGSTROM ** 3  # into bohr³, the unit of the data
        sums = np.bincount(bins, weights=values * voxel, minlength=n_bins)
        return AxialProfile(centres, sums / bin_width, "integrated", np.cumsum(sums), frame)
    counts = np.bincount(bins, minlength=n_bins)
    sums = np.bincount(bins, weights=values, minlength=n_bins)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(counts > 0, sums / np.maximum(counts, 1), np.nan)
    return AxialProfile(centres, mean, "averaged", None, frame)


def report_html(reader: QVFReader, structure: StructureData | None = None, *,
                include_plotlyjs: str | bool = "cdn") -> str:
    """The interface report: contacts, interface-group PDOS, axial profile.

    Each part appears when the archive has what it needs; the report says
    which parts it left out and why.
    """
    from html import escape

    from vibeview.renderers._html import data_table, panel_document

    structure = structure if structure is not None else reader.read_structure()
    parts: list[str] = []
    notes: list[str] = []
    rows = metal_carbon_contacts(structure, reader)
    if rows:
        table_rows = [{"Metal": r["metal_label"], "Carbon": r["carbon_label"],
                       "Length (Å)": r["distance"],
                       "Order": r["order"] if r["order"] is not None else None}
                      for r in rows]
        caption = f"{len(rows)} metal–C contacts"
        method = next((r["method"] for r in rows if r["method"]), None)
        if method:
            caption += f"; orders: {method}"
        parts.append(data_table(["Metal", "Carbon", "Length (Å)", "Order"], table_rows,
                                caption=caption, numeric_format=".3f"))
    else:
        notes.append("No metal–C contacts within bonding range.")

    group = interface_atoms(structure)
    pdos: list[tuple[str, GroupDOS]] = []
    pdos_section = next((s for s in reader.sections if s.kind == "dos.projected"), None)
    if pdos_section is None:
        notes.append("No dos.projected section: no interface PDOS.")
    elif group:
        try:
            dos = reader.read_dos(pdos_section.id)
            metal = [i for i in group if is_metal(_z(structure.atoms[i]))]
            carbon = [i for i in group if i not in metal]
            for label, atoms in (("Interface metal", metal), ("Interface C", carbon)):
                if atoms:
                    pdos.append((label, group_pdos(dos, atoms)))
        except ValueError as exc:
            notes.append(f"Interface PDOS: {exc}.")
    volume = next((s for s in reader.sections
                   if s.kind in ("volume.difference", "volume.potential")), None)
    profile = None
    if volume is None:
        notes.append("No volume.difference or volume.potential section: no axial profile.")
    else:
        profile = axial_profile(reader, volume.id, structure)

    if pdos or profile is not None:
        import plotly.graph_objects as go

        from vibeview.renderers._plotly import chart_layout, figure_to_html

        figures = []
        if pdos:
            fig = go.Figure()
            for label, g in pdos:
                fig.add_trace(go.Scatter(x=g.energies, y=g.values, name=label, mode="lines"))
            chart_layout(fig, title="PDOS of the interface atoms", x_title="Energy (eV)",
                         y_title="States / eV", legend="bottom")
            figures.append(fig)
        if profile is not None and volume is not None:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=profile.position, y=profile.value, name=volume.id,
                                     mode="lines"))
            if profile.cumulative is not None:
                fig.add_trace(go.Scatter(x=profile.position, y=profile.cumulative,
                                         name="Charge transferred (e)", mode="lines",
                                         yaxis="y2"))
            chart_layout(fig, title=f"{volume.id} along the tube axis",
                         x_title="Position along the axis (Å)",
                         y_title=("Plane-integrated Δρ (e/Å)"
                                  if profile.reduction == "integrated"
                                  else "Plane-averaged value"),
                         legend="bottom",
                         yaxis2={"overlaying": "y", "side": "right", "title": "Cumulative (e)"})
            figures.append(fig)
        for fig in figures:
            parts.append(figure_to_html(fig, include_plotlyjs=include_plotlyjs))
            include_plotlyjs = False  # once per document
    if notes:
        parts.append("<p class='note'>" + "<br>".join(escape(n) for n in notes) + "</p>")
    return panel_document("".join(parts))
