"""Edge-case and regression tests for vibe-view v2.0 (target: 500+ tests)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest


class TestInputParserEdgeCases:
    def test_parse_with_extra_kwargs(self):
        from vibeview.input_parser import parse_input_source

        source = """
from vibeqc import Atom, Molecule, run_job
mol = Molecule([Atom(6, [0,0,0])])
run_job(mol, basis="sto-3g", method="rhf", nroots=5, guess="sad")
"""
        result = parse_input_source(source)
        assert result.basis == "sto-3g"
        assert result.method == "rhf"

    def test_parse_with_atom_class(self):
        from vibeview.input_parser import parse_input_source

        # Test that various Atom constructor forms work
        source = """
from vibeqc import Atom, Molecule
mol = Molecule([Atom("C", [0,0,0], unit="angstrom")])
"""
        result = parse_input_source(source)
        assert len(result.atoms) >= 1

    def test_parse_with_tddft(self):
        from vibeview.input_parser import parse_input_source

        source = """
from vibeqc import Atom, Molecule, run_job
mol = Molecule([Atom(8, [0,0,0])])
run_job(mol, basis="6-31g", method="rks", tddft=True, nroots=10)
"""
        result = parse_input_source(source)
        assert result.method == "rks"


class TestInputGeneratorEdgeCases:
    def test_generate_with_charge_negative(self):
        from vibeview.input_generator import generate_input_script

        atoms = [{"symbol": "Cl", "position": [0, 0, 0], "atomic_number": 17}]
        params = {
            "atoms": atoms,
            "basis": "aug-cc-pvdz",
            "method": "rhf",
            "functional": "",
            "charge": -1,
            "multiplicity": 1,
            "title": "Cl-",
        }
        script = generate_input_script("single_point", params)
        assert "charge=-1" in script

    def test_generate_all_templates(self):
        from vibeview.input_generator import generate_input_script

        atoms = [{"symbol": "H", "position": [0, 0, 0], "atomic_number": 1}]
        base = {
            "atoms": atoms,
            "basis": "sto-3g",
            "method": "rhf",
            "functional": "",
            "charge": 0,
            "multiplicity": 1,
            "title": "test",
        }
        for tmpl in ["single_point", "optimization", "frequencies", "full", "periodic"]:
            params = base
            if tmpl == "periodic":
                params = {
                    **base,
                    "lattice_vectors": [
                        [5.0, 0.0, 0.0],
                        [0.0, 5.0, 0.0],
                        [0.0, 0.0, 5.0],
                    ],
                }
            script = generate_input_script(tmpl, params)
            assert "run_job" in script or "PeriodicSystem" in script
            assert len(script) > 100


class TestBuildToolsEdgeCases:
    def test_add_hydrogens_to_empty(self):
        from vibeview.build_tools import add_hydrogens

        result = add_hydrogens([])
        assert result == []

    def test_add_hydrogens_to_water(self):
        from vibeview.build_tools import add_hydrogens

        atoms = [
            {"symbol": "O", "position": [0, 0, 0], "atomic_number": 8},
            {"symbol": "H", "position": [0, 0.757, 0.586], "atomic_number": 1},
            {"symbol": "H", "position": [0, -0.757, 0.586], "atomic_number": 1},
        ]
        result = add_hydrogens(atoms)
        assert len(result) == 3  # Water is already saturated

    def test_fragment_ch3_valid(self):
        from vibeview.build_tools import FRAGMENTS

        ch3 = FRAGMENTS["CH3"]
        # C at origin, 3 H atoms at ~1.09 Angstrom
        assert ch3[0][0] == "C"
        for i in range(1, 4):
            assert ch3[i][0] == "H"
            # Distance from center to H
            pos = np.array(ch3[i][1])
            dist = np.linalg.norm(pos)
            assert 0.9 < dist < 1.3  # ~1.09 Angstrom CH bond

    def test_supercell_small(self):
        from vibeview.build_tools import build_supercell

        atoms = [{"symbol": "Li", "position": [0, 0, 0], "atomic_number": 3}]
        lattice = [[5.0, 0.0, 0.0], [0.0, 5.0, 0.0], [0.0, 0.0, 5.0]]
        result = build_supercell(atoms, lattice, (1, 1, 1))
        assert len(result) == 1

    def test_supercell_large(self):
        from vibeview.build_tools import build_supercell

        atoms = [{"symbol": "Au", "position": [0, 0, 0], "atomic_number": 79}]
        lattice = [[5.0, 0.0, 0.0], [0.0, 5.0, 0.0], [0.0, 0.0, 5.0]]
        result = build_supercell(atoms, lattice, (3, 3, 3))
        assert len(result) == 27


class TestCrystalBuilderEdgeCases:
    def test_search_empty_returns_all(self):
        from vibeview.crystal_builder import search_space_groups

        all_groups = search_space_groups("")
        assert len(all_groups) >= 23

    def test_search_by_system(self):
        from vibeview.crystal_builder import search_space_groups

        cubic = search_space_groups("cubic")
        assert len(cubic) >= 5
        assert all(g["crystal_system"] == "cubic" for g in cubic)

    def test_cell_from_abc_monoclinic(self):
        from vibeview.crystal_builder import abc_from_cell, cell_from_abc

        cell = cell_from_abc(5.0, 6.0, 7.0, 90, 105, 90)
        a, b, c, alpha, beta, gamma = abc_from_cell(cell)
        assert abs(beta - 105) < 0.1

    def test_cell_volume_triclinic(self):
        from vibeview.crystal_builder import cell_from_abc, cell_volume

        cell = cell_from_abc(5.0, 6.0, 7.0, 85, 92, 88)
        vol = cell_volume(cell)
        assert vol > 0

    def test_replicate_wrap_vs_unwrap(self):
        from vibeview.crystal_builder import cell_from_abc, replicate_cell

        cell = cell_from_abc(3.0, 3.0, 3.0, 90, 90, 90)
        atoms = [{"symbol": "X", "position": [0.5, 0.5, 0.5]}]
        _, wrapped = replicate_cell(cell, atoms, 2, 2, 2, wrap=True)
        _, unwrapped = replicate_cell(cell, atoms, 2, 2, 2, wrap=False)
        assert len(wrapped) == len(unwrapped)



class TestMaterialPresetsEdgeCases:
    def test_preset_background_valid_rgb(self):
        from vibeview.material_presets import MATERIAL_PRESETS

        for name, p in MATERIAL_PRESETS.items():
            assert len(p.background_color) == 3
            for c in p.background_color:
                assert 0.0 <= c <= 1.0, f"{name} background has out-of-range value {c}"

    def test_preset_roughness_range(self):
        from vibeview.material_presets import MATERIAL_PRESETS

        for name, p in MATERIAL_PRESETS.items():
            assert 0.0 <= p.atom_roughness <= 1.0, f"{name} roughness out of range"
            assert 0.0 <= p.atom_metallic <= 1.0, f"{name} metallic out of range"


class TestConverterEdgeCases:
    def test_xyz_to_qvf_empty_raises(self):
        from vibeview.converters import xyz_to_qvf

        # XYZ with 0 atoms should raise ValueError
        with pytest.raises(ValueError, match="no atoms"):
            xyz_to_qvf(b"0\n\n")

    def test_xyz_to_qvf_large(self):
        from vibeview.converters import xyz_to_qvf

        # 100-atom molecule
        xyz = b"100\nLarge molecule\n"
        for i in range(100):
            xyz += f"C    {i * 1.5:.6f}    0.000000    0.000000\n".encode()
        buf = xyz_to_qvf(xyz)
        import zipfile

        zf = zipfile.ZipFile(buf)
        assert "manifest.json" in zf.namelist()


class TestCLIIntegration:
    def test_cli_help_runs(self):
        from click.testing import CliRunner

        from vibeview.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "open" in result.output
        assert "compare" in result.output

    def test_cli_version(self):
        from click.testing import CliRunner

        from vibeview.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0

    def test_cli_open_requires_file(self):
        from click.testing import CliRunner

        from vibeview.cli import main

        runner = CliRunner()
        # "open" requires at least one file argument
        result = runner.invoke(main, ["open", "/nonexistent.qvf"])
        # Should error on missing file
        assert result.exit_code != 0

    def test_cli_open_help(self):
        from click.testing import CliRunner

        from vibeview.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["open", "--help"])
        assert result.exit_code == 0
        assert "--port" in result.output
        assert "--no-browser" in result.output
