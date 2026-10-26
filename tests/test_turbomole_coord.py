"""Turbomole/xtb ``coord`` files open as structures (#145)."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from vibeview.converters import (
    convert_bytes_to_qvf,
    convert_to_qvf,
    detect_format,
    turbomole_to_qvf,
)
from vibeview.input_writers import InputSettings, write_input
from vibeview.qvf import Atom, QVFReader, StructureData
from vibeview.units import BOHR_TO_ANGSTROM

# xtb's water example (xtb documentation, "Geometry input"), in bohr.
XTB_WATER = b"""$coord
 0.00000000000000      0.00000000000000     -0.73578586109551      o
 1.44183152868459      0.00000000000000      0.36789293054775      h
-1.44183152868459      0.00000000000000      0.36789293054775      h f
$end
"""


def _read(buffer):
    reader = QVFReader(buffer)
    return reader, reader.read_structure()


def _water():
    return StructureData(
        atoms=[
            Atom(symbol="O", atomic_number=8, position=[0.0, 0.0, 0.0]),
            Atom(symbol="H", atomic_number=1, position=[0.758, 0.0, 0.504]),
            Atom(symbol="H", atomic_number=1, position=[-0.758, 0.0, 0.504]),
        ],
        lattice_vectors=[[12.0, 0.0, 0.0], [0.0, 12.0, 0.0], [0.0, 0.0, 12.0]],
        pbc=(False, False, False),
        bonds=None,
    )


@pytest.mark.parametrize("name,fmt", [
    ("coord", "turbomole"), ("run/xtbopt.coord", "turbomole"), ("water.tmol", "turbomole"),
    ("coordinates.txt", None), ("COORD", None),
])
def test_coord_files_are_detected(name, fmt):
    assert detect_format(name) == fmt


def test_bohr_coordinates_convert_to_angstrom():
    _, structure = _read(turbomole_to_qvf(XTB_WATER))
    assert [a.symbol for a in structure.atoms] == ["O", "H", "H"]
    assert structure.atoms[1].position[0] == pytest.approx(1.44183152868459 * BOHR_TO_ANGSTROM)
    assert not any(structure.pbc)


def test_the_viewers_own_xtb_coord_round_trips(tmp_path):
    water = _water()
    molecule = write_input(water, InputSettings("xtb")).files["coord"]
    path = tmp_path / "coord"
    path.write_text(molecule)
    _, structure = _read(convert_to_qvf(path))
    for atom, original in zip(structure.atoms, water.atoms, strict=True):
        assert atom.symbol == original.symbol
        assert atom.position == pytest.approx(original.position, abs=1e-9)

    periodic = replace(water, pbc=(True, True, True), dim=3)
    crystal = write_input(periodic, InputSettings("xtb", method="GFN-FF")).files["coord"]
    _, structure = _read(convert_bytes_to_qvf(crystal.encode(), "xtbopt.coord"))
    assert all(structure.pbc)
    assert np.allclose(structure.lattice_vectors, water.lattice_vectors)


def test_cell_parameters_and_fractional_coordinates():
    text = b"""$coord frac
0.0 0.0 0.0 na
0.5 0.5 0.5 cl
$periodic 3
$cell angs
5.64 5.64 5.64 90 90 90
$end
"""
    _, structure = _read(turbomole_to_qvf(text))
    assert [a.symbol for a in structure.atoms] == ["Na", "Cl"]
    assert structure.atoms[1].position == pytest.approx([2.82, 2.82, 2.82])
    assert np.allclose(structure.lattice_vectors, np.eye(3) * 5.64)


def test_charge_and_multiplicity_are_read():
    xtb = XTB_WATER.replace(b"$end", b"$chrg 1\n$spin 1\n$end")
    reader, _ = _read(turbomole_to_qvf(xtb))
    assert reader.provenance["charge"] == 1
    assert reader.provenance["multiplicity"] == 2
    turbomole = XTB_WATER.replace(b"$end", b"$eht charge=-1 unpaired=0\n$end")
    reader, _ = _read(turbomole_to_qvf(turbomole))
    assert (reader.provenance["charge"], reader.provenance["multiplicity"]) == (-1, 1)
    reader, _ = _read(turbomole_to_qvf(XTB_WATER))
    assert "charge" not in (reader.provenance or {})


@pytest.mark.parametrize("text,message", [
    (b"$title\nwater\n$end\n", "no \\$coord group"),
    (b"$coord file=coord\n$end\n", "open that file instead"),
    (b"$coord\n0 0 0 xx\n$end\n", "unknown element"),
    (b"$coord\n0 0 o\n$end\n", "invalid \\$coord line"),
    (b"$coord\n$end\n", "no atoms"),
    (b"$coord frac\n0 0 0 o\n$end\n", "needs a periodic cell"),
    (b"$coord\n0 0 0 o\n$periodic 2\n$lattice\n5 0\n0 5\n$end\n", "periodic 2 is not supported"),
    (b"$coord\n0 0 0 o\n$periodic 3\n$end\n", "needs a \\$lattice or \\$cell"),
    (b"$coord\n0 0 0 o\n$coord\n1 0 0 h\n$end\n", "duplicate \\$coord"),
    (b"$coord\n0 0 0 o\n$spin -1\n$end\n", "must not be negative"),
])
def test_malformed_files_are_refused(text, message):
    with pytest.raises(ValueError, match=message):
        turbomole_to_qvf(text)
