"""CLI and browser geometry exports preserve coordinate meaning."""

import base64
import json
import os

import numpy as np
import pytest
from click.testing import CliRunner

from tests.conftest import build_qvf
from vibeview.cli import main
from vibeview.qvf import QVFReader


@pytest.mark.parametrize("frontend", ["cli", "browser"])
@pytest.mark.parametrize("cell_mode", ["none", "nonperiodic", "periodic", "partial"])
def test_cif_coordinate_columns_match_the_exported_cell(tmp_path, monkeypatch, frontend, cell_mode):
    lattice = np.array([[4.0, 0.0, 0.0], [1.0, 6.0, 0.0], [2.0, 1.0, 8.0]])
    fractional = np.array([[0.25, 0.5, 0.75], [-0.125, 1.25, 0.5]])
    positions = fractional @ lattice
    atoms = [
        {"symbol": s, "atomic_number": z, "position": p.tolist()}
        for s, z, p in zip(["C", "O"], [6, 8], positions, strict=True)
    ]
    structure = {"atoms": atoms, "pbc": [False, False, False]}
    if cell_mode != "none":
        structure["lattice_vectors"] = lattice.tolist()
    if cell_mode == "periodic":
        structure["pbc"] = [True, True, True]
    elif cell_mode == "partial":
        structure["pbc"] = [True, False, False]
    path = build_qvf(
        tmp_path / "geometry.qvf",
        [
            (
                "structure",
                "structure",
                {"structure": ("json", "structure.json", json.dumps(structure).encode())},
            )
        ],
    )
    if frontend == "cli":
        output = tmp_path / "geometry.cif"
        result = CliRunner().invoke(main, ["export", str(path), "-f", "cif", "-o", str(output)])
        assert result.exit_code == 0, result.output
        text = output.read_text()
    else:
        from trame.app import get_server

        import vibeview.app as appmod

        monkeypatch.setattr(appmod.asyncio, "ensure_future", lambda coroutine: coroutine.close())
        with QVFReader(path) as reader:
            appmod.create_app(reader)
            server = get_server(client_type="vue3")
            server.controller.export_geometry("cif")
            assert server.state.export_filename.endswith(".cif")
            text = base64.b64decode(server.state.export_data.split(",", 1)[1]).decode()
    has_cell = cell_mode in {"periodic", "partial"}
    prefix = "_atom_site_fract_" if has_cell else "_atom_site_Cartn_"
    assert ("_cell_length_a" in text) is has_cell
    assert all(prefix + axis in text for axis in "xyz")
    lines = text.splitlines()
    assert "_atom_site_label" in lines
    assert "_atom_site_type_symbol" in lines
    last_column = lines.index(prefix + "z")
    exported = np.array([[float(x) for x in row.split()[-3:]] for row in lines[last_column + 1 :]])
    np.testing.assert_allclose(exported, fractional if has_cell else positions, atol=5e-7)
    rows = [row.split() for row in lines[last_column + 1 :]]
    assert len({row[0] for row in rows}) == len(atoms)
    assert [row[1] for row in rows] == ["C", "O"]
    if has_cell:
        # Row-vector convention and negative/out-of-cell coordinates are preserved.
        np.testing.assert_allclose(exported @ lattice, positions, atol=4e-6)


def test_xyz_text_keeps_current_coordinates_and_caller_comment():
    from vibeview.geometry_text import xyz_text
    from vibeview.qvf import Atom, StructureData

    structure = StructureData(
        atoms=[Atom(symbol="O", atomic_number=8, position=np.array([1.25, -2.5, 3.75]))],
        pbc=(False, False, False),
        lattice_vectors=None,
        bonds=None,
    )
    before = structure.atoms[0].position.copy()
    assert xyz_text(structure, "chosen comment") == (
        "1\nchosen comment\nO      1.250000    -2.500000     3.750000\n"
    )
    np.testing.assert_array_equal(structure.atoms[0].position, before)


@pytest.mark.parametrize("fmt", ["xyz", "cif"])
@pytest.mark.parametrize(
    "separator", ["\n", "\r\n", "\r", "\u2028"], ids=["lf", "crlf", "cr", "unicode-line"]
)
def test_geometry_comment_line_breaks_do_not_create_records(fmt, separator):
    from vibeview.converters import cif_to_qvf, xyz_to_qvf
    from vibeview.geometry_text import cif_text, xyz_text
    from vibeview.qvf import Atom, StructureData

    structure = StructureData(
        atoms=[Atom(symbol="O", atomic_number=8, position=np.array([1.25, -2.5, 3.75]))],
        pbc=(False, False, False),
        lattice_vectors=None,
        bonds=None,
    )
    writer = xyz_text if fmt == "xyz" else cif_text
    converter = xyz_to_qvf if fmt == "xyz" else cif_to_qvf
    text = writer(structure, f"first{separator}second")
    assert text.splitlines()[1] == ("first second" if fmt == "xyz" else "# first second")
    with QVFReader(converter(text.encode())) as reader:
        exported = reader.read_structure()
    assert [atom.symbol for atom in exported.atoms] == ["O"]
    np.testing.assert_allclose(exported.atoms[0].position, structure.atoms[0].position)


@pytest.mark.skipif(os.name == "nt", reason="Windows filenames cannot contain line breaks")
@pytest.mark.parametrize("fmt", ["xyz", "cif"])
def test_cli_geometry_export_from_filename_with_line_break(tmp_path, fmt):
    from vibeview.converters import cif_to_qvf, xyz_to_qvf

    position = [1.25, -2.5, 3.75]
    structure = {
        "atoms": [{"symbol": "O", "atomic_number": 8, "position": position}],
        "pbc": [False, False, False],
    }
    source = build_qvf(
        tmp_path / "sample\nrelaxed.qvf",
        [
            (
                "structure",
                "structure",
                {"structure": ("json", "structure.json", json.dumps(structure).encode())},
            )
        ],
    )
    output = tmp_path / f"geometry.{fmt}"
    result = CliRunner().invoke(main, ["export", str(source), "-f", fmt, "-o", str(output)])
    assert result.exit_code == 0, result.output
    prefix = "vibe-view export from " if fmt == "xyz" else "# CIF exported by vibe-view from "
    assert output.read_text().splitlines()[1] == prefix + "sample relaxed.qvf"
    converter = xyz_to_qvf if fmt == "xyz" else cif_to_qvf
    with QVFReader(converter(output)) as reader:
        exported = reader.read_structure()
    assert [atom.symbol for atom in exported.atoms] == ["O"]
    np.testing.assert_allclose(exported.atoms[0].position, position)
