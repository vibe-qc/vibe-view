"""ASE-backed format import (workstream F1/F2).

The extra and the extension mapping were already in place; what was missing
was the cell. A POSCAR arrived flagged periodic with no lattice, which is not
a usable structure: `is_periodic` wants a lattice *and* a pbc flag, so the
file rendered as a molecule with no unit cell, no periodic bonding and no
replication. Silently, because every atom came through and looked right.
"""

from __future__ import annotations

import numpy as np
import pytest

from vibeview.converters import (
    _ASE_EXTENSIONS,
    _SUPPORTED_EXTENSIONS,
    convert_to_qvf,
    detect_format,
)
from vibeview.qvf import QVFReader, _infer_bonds_by_radii

ase = pytest.importorskip("ase", reason="needs the [ase] extra")

DIAMOND_FRAC = [
    (0, 0, 0), (0, 0.5, 0.5), (0.5, 0, 0.5), (0.5, 0.5, 0),
    (0.25, 0.25, 0.25), (0.25, 0.75, 0.75),
    (0.75, 0.25, 0.75), (0.75, 0.75, 0.25),
]


def _poscar(tmp_path, a=5.43, frac=None):
    frac = DIAMOND_FRAC if frac is None else frac
    lines = ["Si", "1.0", f"{a} 0.0 0.0", f"0.0 {a} 0.0", f"0.0 0.0 {a}",
             "Si", str(len(frac)), "Direct"]
    lines += [f"{x} {y} {z}" for x, y, z in frac]
    p = tmp_path / "POSCAR.vasp"
    p.write_text("\n".join(lines) + "\n")
    return p


def _structure(path, tmp_path, name="o.qvf"):
    q = tmp_path / name
    q.write_bytes(convert_to_qvf(path).getvalue())
    return QVFReader(q).read_structure()


class TestExtensionMapping:
    def test_ase_extensions_are_offered(self):
        for ext in _ASE_EXTENSIONS:
            assert ext in _SUPPORTED_EXTENSIONS

    @pytest.mark.parametrize("ext", [".vasp", ".poscar", ".extxyz", ".traj"])
    def test_detected_as_ase(self, tmp_path, ext):
        p = tmp_path / f"f{ext}"
        p.write_text("")
        assert detect_format(p) == "ase"


class TestCellSurvivesImport:
    def test_poscar_keeps_its_lattice(self, tmp_path):
        sd = _structure(_poscar(tmp_path), tmp_path)
        assert sd.lattice_vectors is not None, "the cell was dropped"
        np.testing.assert_allclose(
            np.diag(np.asarray(sd.lattice_vectors)), [5.43] * 3, atol=1e-9
        )

    def test_poscar_is_marked_periodic(self, tmp_path):
        sd = _structure(_poscar(tmp_path), tmp_path)
        assert tuple(sd.pbc) == (True, True, True)

    def test_a_periodic_import_is_self_consistent(self, tmp_path):
        """pbc set with no lattice is the state that rendered as a molecule."""
        sd = _structure(_poscar(tmp_path), tmp_path)
        assert not (any(sd.pbc) and sd.lattice_vectors is None)

    def test_a_molecule_stays_non_periodic(self, tmp_path):
        p = tmp_path / "m.extxyz"
        p.write_text("2\nProperties=species:S:1:pos:R:3\nO 0 0 0\nH 0.96 0 0\n")
        sd = _structure(p, tmp_path, "m.qvf")
        assert not any(sd.pbc)

    def test_extxyz_lattice_is_read(self, tmp_path):
        p = tmp_path / "c.extxyz"
        p.write_text(
            '2\nLattice="10 0 0 0 10 0 0 0 10" '
            'Properties=species:S:1:pos:R:3 pbc="T T T"\n'
            "O 0 0 0\nH 0.96 0 0\n"
        )
        sd = _structure(p, tmp_path, "c.qvf")
        assert sd.lattice_vectors is not None
        np.testing.assert_allclose(
            np.diag(np.asarray(sd.lattice_vectors)), [10.0] * 3, atol=1e-9
        )


class TestTheCellIsActuallyUsed:
    """The point of carrying it: everything periodic downstream depends on it."""

    def test_diamond_silicon_is_four_coordinate(self, tmp_path):
        sd = _structure(_poscar(tmp_path), tmp_path)
        bonds = _infer_bonds_by_radii(sd)
        degree = {i: 0 for i in range(len(sd.atoms))}
        for i, j, _order, _image in bonds:
            degree[i] += 1
            degree[j] += 1
        assert sorted(set(degree.values())) == [4], degree

    def test_bonds_are_at_the_real_si_si_distance(self, tmp_path):
        sd = _structure(_poscar(tmp_path), tmp_path)
        lattice = np.asarray(sd.lattice_vectors)
        for i, j, _order, image in _infer_bonds_by_radii(sd):
            p1 = np.asarray(sd.atoms[i].position)
            p2 = np.asarray(sd.atoms[j].position) + np.asarray(image) @ lattice
            assert float(np.linalg.norm(p2 - p1)) == pytest.approx(2.351, abs=1e-3)
