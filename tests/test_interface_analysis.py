"""Tube–metal interface analysis from QVF tables (roadmap: carbon nanotubes)."""

from __future__ import annotations

import numpy as np
import pytest

from vibeview import interface_analysis as ia
from vibeview import interface_builder as ib
from vibeview import nanotube as nt
from vibeview.qvf import QVFReader
from vibeview.qvf_writer import structure_archive
from vibeview.units import BOHR_TO_ANGSTROM


@pytest.fixture(scope="module")
def contact():
    tube = nt.single_wall(6, 6, 12.0, end="open")
    return ib.add_slab(tube.symbols, tube.positions, metal="Cu", facet="111", distance=1.95,
                       layers=2)


def _archive(tmp_path, contact, *, orders=None, channels=None, volume=None):
    atoms = [{"symbol": s, "atomic_number": 29 if s == "Cu" else 6, "position": p.tolist()}
             for s, p in zip(contact.symbols, contact.positions, strict=True)]
    builder = structure_archive(atoms, (False, False, False))
    if orders is not None:
        builder.add_json("bo", "bond_orders", "bond_orders", {"method": "mayer", "pairs": orders})
    if channels is not None:
        energies = np.linspace(-5, 5, 21)
        projections = np.ones((len(channels), 21)) * np.arange(1, len(channels) + 1)[:, None]
        builder.add_binary("pdos", "dos.projected", "energies", energies)
        builder.add_binary("pdos", "dos.projected", "projections", projections,
                           section_extra={"channels": channels})
    if volume is not None:
        grid, data = volume
        builder.add_json("dq", "volume.difference", "grid", grid)
        builder.add_binary("dq", "volume.difference", "data", data.astype(np.float32), "float32")
    return QVFReader(builder.write(tmp_path / "interface.qvf"))


def test_contacts_list_the_interface_bonds_shortest_first(contact, tmp_path):
    m, c = contact.contact[1], contact.contact[0]
    reader = _archive(tmp_path, contact, orders=[{"i": c, "j": m, "order": 0.71}])
    try:
        rows = ia.metal_carbon_contacts(reader.read_structure(), reader)
    finally:
        reader.close()
    assert rows[0]["distance"] == pytest.approx(1.95, abs=1e-6)
    assert rows == sorted(rows, key=lambda r: r["distance"])
    first = next(r for r in rows if (r["metal"], r["carbon"]) == (m, c))
    assert first["order"] == pytest.approx(0.71) and first["method"] == "mayer"
    assert first["metal_label"] == f"Cu{m + 1}" and first["carbon_label"] == f"C{c + 1}"
    assert all(r["order"] is None for r in rows if r is not first)
    limit = ib.bond_distance("C", "Cu") + ia.CONTACT_PAD
    assert all(r["distance"] < limit for r in rows)
    assert set(ia.interface_atoms(_structure(contact))) == (
        {r["metal"] for r in rows} | {r["carbon"] for r in rows})


def _structure(contact):
    from vibeview.qvf import Atom, StructureData

    return StructureData([Atom(s, p, 29 if s == "Cu" else 6)
                          for s, p in zip(contact.symbols, contact.positions, strict=True)],
                         (False, False, False), None, None)


def test_no_metal_means_no_contacts():
    tube = nt.single_wall(5, 5, 8.0)
    from vibeview.qvf import Atom, StructureData

    structure = StructureData([Atom("C", p, 6) for p in tube.positions], (False,) * 3, None, None)
    assert ia.metal_carbon_contacts(structure) == []


def test_group_pdos_sums_the_atoms_channels(contact, tmp_path):
    channels = [{"atom": 0, "l": 1}, {"atom": 1, "l": 1}, {"atom": 0, "l": 0},
                {"label": "total"}]
    reader = _archive(tmp_path, contact, channels=channels)
    try:
        dos = reader.read_dos("pdos")
    finally:
        reader.close()
    group = ia.group_pdos(dos, {0})
    assert group.channels == 2
    np.testing.assert_allclose(group.values, 1.0 + 3.0)  # channel rows 0 and 2
    with pytest.raises(ValueError, match="no projected-DOS channel"):
        ia.group_pdos(dos, {7})


def test_group_pdos_needs_atom_indices(contact, tmp_path):
    reader = _archive(tmp_path, contact, channels=[{"label": "C p"}, {"label": "Cu d"}])
    try:
        with pytest.raises(ValueError, match="no atom index"):
            ia.group_pdos(reader.read_dos("pdos"), {0})
    finally:
        reader.close()


def test_axial_profile_integrates_a_sheet_of_charge(contact, tmp_path):
    # A Gaussian sheet of -0.5 e perpendicular to the tube axis (z), centred
    # 3 A above the frame centre, on a grid in bohr.
    frame = ib.tube_frame(contact.symbols, contact.positions)
    step = 0.2 / BOHR_TO_ANGSTROM
    shape = (30, 30, 120)
    origin_a = frame.center - np.array([3.0, 3.0, 12.0])
    grid = {"origin": (origin_a / BOHR_TO_ANGSTROM).tolist(),
            "voxel_vectors": (np.eye(3) * step).tolist(), "shape": list(shape)}
    z = origin_a[2] + np.arange(shape[2]) * 0.2 - frame.center[2]
    sigma = 0.6 / BOHR_TO_ANGSTROM
    line = np.exp(-((z - 3.0) / BOHR_TO_ANGSTROM) ** 2 / (2 * sigma**2))
    area = (shape[0] * step) * (shape[1] * step)
    line *= -0.5 / (line.sum() * step * area)  # e/bohr^3, total -0.5 e
    data = np.broadcast_to(line, shape).copy()
    reader = _archive(tmp_path, contact, volume=(grid, data))
    try:
        profile = ia.axial_profile(reader, "dq", _structure(contact), bin_width=0.2)
    finally:
        reader.close()
    assert profile.reduction == "integrated"
    assert profile.cumulative[-1] == pytest.approx(-0.5, rel=1e-3)
    sign = np.sign(profile.frame.axis[2])
    peak = profile.position[np.argmin(profile.value)] * sign
    assert peak == pytest.approx(3.0, abs=0.2)
    with pytest.raises(ValueError):
        ia.axial_profile(reader, "dq", bin_width=0.0)


def test_report_has_every_part_the_archive_supports(contact, tmp_path):
    pytest.importorskip("plotly")
    channels = [{"atom": i, "l": 1} for i in range(len(contact.symbols))]
    grid = {"origin": [-20.0, -20.0, -20.0], "voxel_vectors": (np.eye(3) * 4.0).tolist(),
            "shape": [10, 10, 10]}
    reader = _archive(tmp_path, contact, channels=channels,
                      volume=(grid, np.zeros((10, 10, 10))))
    try:
        report = ia.report_html(reader)
    finally:
        reader.close()
    assert "metal–C contacts" in report
    assert "PDOS of the interface atoms" in report
    assert "along the tube axis" in report
    assert "class='note'" not in report
