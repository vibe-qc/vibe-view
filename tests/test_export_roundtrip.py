"""F3: export breadth parity pass.

Every CLI export format is exercised on a molecule and on a crystal, and
each output is checked in a way appropriate to the format rather than
just "a file appeared". The bug this class of test exists to catch was a
real one: vibe-view exported a CIF its own reader then refused.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

FORMATS = [
    "xyz", "cif", "obj", "gltf", "html", "json",
    "py", "pov", "blend", "svg", "pdf", "cml",
]

MOLECULE = b"3\nwater\nO 0.0 0.0 0.0\nH 0.76 0.59 0.0\nH -0.76 0.59 0.0\n"

CRYSTAL_CIF = """data_test
_cell_length_a 5.6400
_cell_length_b 5.6400
_cell_length_c 5.6400
_cell_angle_alpha 90.0
_cell_angle_beta 90.0
_cell_angle_gamma 90.0
loop_
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Na 0.0 0.0 0.0
Cl 0.5 0.5 0.5
"""


@pytest.fixture(scope="module")
def molecule_qvf(tmp_path_factory):
    from vibeview.converters import xyz_to_qvf

    p = tmp_path_factory.mktemp("mol") / "m.qvf"
    p.write_bytes(xyz_to_qvf(MOLECULE).getvalue())
    return p


@pytest.fixture(scope="module")
def crystal_qvf(tmp_path_factory):
    from vibeview.converters import cif_to_qvf

    d = tmp_path_factory.mktemp("cry")
    (d / "c.cif").write_text(CRYSTAL_CIF)
    p = d / "c.qvf"
    p.write_bytes(cif_to_qvf(d / "c.cif").getvalue())
    return p


def _export(qvf: Path, fmt: str, dest: Path) -> None:
    r = subprocess.run(
        [sys.executable, "-m", "vibeview.cli", "export",
         str(qvf), "-f", fmt, "-o", str(dest)],
        capture_output=True, text=True, timeout=300,
    )
    assert r.returncode == 0, f"{fmt} export failed: {(r.stderr or r.stdout)[-400:]}"
    assert dest.exists() and dest.stat().st_size > 0, f"{fmt} produced nothing"


def _assert_wellformed(fmt: str, p: Path) -> None:
    raw = p.read_bytes()
    txt = raw.decode("utf-8", errors="replace")
    if fmt == "xyz":
        lines = [ln for ln in txt.splitlines() if ln.strip()]
        assert int(lines[0].split()[0]) == len(lines) - 2
    elif fmt == "cif":
        assert "loop_" in txt and "_atom_site" in txt
    elif fmt == "obj":
        assert "\nv " in "\n" + txt, "no vertices"
    elif fmt == "gltf":
        assert raw[:4] == b"glTF" or "asset" in json.loads(txt)
    elif fmt == "html":
        assert "<html" in txt.lower() or "<!doctype" in txt.lower()
    elif fmt == "json":
        json.loads(txt)
    elif fmt == "py":
        ast.parse(txt)          # must be runnable Python, not a template
    elif fmt in ("svg", "cml"):
        ET.fromstring(txt)      # must be well-formed XML
    elif fmt == "pdf":
        assert raw[:5] == b"%PDF-"
    elif fmt in ("pov", "blend"):
        assert len(txt.strip()) > 40


class TestEveryFormatExportsFromAMolecule:
    @pytest.mark.parametrize("fmt", FORMATS)
    def test_export(self, molecule_qvf, tmp_path, fmt):
        dest = tmp_path / f"m.{fmt}"
        _export(molecule_qvf, fmt, dest)
        _assert_wellformed(fmt, dest)


class TestEveryFormatExportsFromACrystal:
    @pytest.mark.parametrize("fmt", FORMATS)
    def test_export(self, crystal_qvf, tmp_path, fmt):
        dest = tmp_path / f"c.{fmt}"
        _export(crystal_qvf, fmt, dest)
        _assert_wellformed(fmt, dest)


class TestCifRoundTrip:
    """Regression: the molecular CIF export used Cartesian atom sites, which
    is correct — labelling them fract_* would make consumers misread the
    file — but the reader only understood fract_*, so vibe-view rejected its
    own export with "no atom sites found"."""

    def test_molecule_survives_export_and_reimport(self, molecule_qvf, tmp_path):
        from vibeview.converters import cif_to_qvf
        from vibeview.qvf import QVFReader

        cif = tmp_path / "m.cif"
        _export(molecule_qvf, "cif", cif)
        back = tmp_path / "back.qvf"
        back.write_bytes(cif_to_qvf(cif).getvalue())
        sd = QVFReader(back).read_structure()
        assert [a.symbol for a in sd.atoms] == ["O", "H", "H"]
        np.testing.assert_allclose(
            [a.position for a in sd.atoms],
            [[0.0, 0.0, 0.0], [0.76, 0.59, 0.0], [-0.76, 0.59, 0.0]],
            atol=1e-6,
        )

    def test_cartesian_sites_are_not_scaled_by_a_cell(self, tmp_path):
        """A Cartesian site is already angstroms; multiplying it by the
        lattice would silently resize the molecule."""
        from vibeview.converters import cif_to_qvf
        from vibeview.qvf import QVFReader

        cif = tmp_path / "cart.cif"
        cif.write_text(
            "data_x\nloop_\n_atom_site_label\n_atom_site_Cartn_x\n"
            "_atom_site_Cartn_y\n_atom_site_Cartn_z\n"
            "C 1.500000 0.000000 0.000000\n"
        )
        q = tmp_path / "cart.qvf"
        q.write_bytes(cif_to_qvf(cif).getvalue())
        sd = QVFReader(q).read_structure()
        np.testing.assert_allclose(sd.atoms[0].position, [1.5, 0.0, 0.0], atol=1e-9)

    def test_crystal_survives_export_and_reimport(self, crystal_qvf, tmp_path):
        """The fractional path must keep working — this is the one the
        Cartesian branch could have broken."""
        from vibeview.converters import cif_to_qvf
        from vibeview.qvf import QVFReader

        cif = tmp_path / "c.cif"
        _export(crystal_qvf, "cif", cif)
        back = tmp_path / "back.qvf"
        back.write_bytes(cif_to_qvf(cif).getvalue())
        sd = QVFReader(back).read_structure()
        assert [a.symbol for a in sd.atoms] == ["Na", "Cl"]
        np.testing.assert_allclose(sd.atoms[1].position, [2.82] * 3, atol=1e-3)
        np.testing.assert_allclose(
            np.diag(sd.lattice_vectors), [5.64] * 3, atol=1e-3
        )

    def test_fractional_still_takes_precedence(self, tmp_path):
        """A CIF carrying both must use fract_*, the crystallographic
        convention, not the Cartesian fallback."""
        from vibeview.converters import cif_to_qvf
        from vibeview.qvf import QVFReader

        cif = tmp_path / "both.cif"
        cif.write_text(
            "data_x\n_cell_length_a 10.0\n_cell_length_b 10.0\n_cell_length_c 10.0\n"
            "_cell_angle_alpha 90.0\n_cell_angle_beta 90.0\n_cell_angle_gamma 90.0\n"
            "loop_\n_atom_site_label\n_atom_site_fract_x\n_atom_site_fract_y\n"
            "_atom_site_fract_z\n_atom_site_Cartn_x\n_atom_site_Cartn_y\n"
            "_atom_site_Cartn_z\n"
            "C 0.500000 0.000000 0.000000 9.900000 0.000000 0.000000\n"
        )
        q = tmp_path / "both.qvf"
        q.write_bytes(cif_to_qvf(cif).getvalue())
        sd = QVFReader(q).read_structure()
        np.testing.assert_allclose(sd.atoms[0].position, [5.0, 0.0, 0.0], atol=1e-6)
