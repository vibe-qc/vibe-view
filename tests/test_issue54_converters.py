"""Regressions for the converter and exporter refactor rows in #54.

Each test names the audit row it pins (#40-N in the issue body).
"""

from __future__ import annotations

import inspect
import io
from pathlib import Path

import numpy as np

from vibeview import converters
from vibeview.qvf import QVFReader
from vibeview.qvf_writer import structure_archive

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src" / "vibeview"
_WATER = [
    {"symbol": "O", "atomic_number": 8, "position": [0.0, 0.0, 0.1173]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, 0.7572, -0.4692]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, -0.7572, -0.4692]},
]


def _water(tmp_path: Path) -> Path:
    return structure_archive(_WATER, (False, False, False), calculation="w").write(
        tmp_path / "w.qvf"
    )


# ── #40-18 / #40-24: one element table, one flat figure ─────────────────


def test_exporters_take_their_tables_from_vibeview_elements():
    for name in ("export_svg", "export_pdf", "povray_export", "blender_export", "build_tools",
                 "export_html"):
        source = (_SRC / f"{name}.py").read_text()
        assert "vibeview.elements" in source or "vibeview.figure2d" in source, name
        assert "_project_2d" not in source, name
        tables = ("VDW_RADII: dict", "_BALL_RADII", "CPK_COLORS: dict")
        assert not any(t in source for t in tables), name


def test_svg_and_pdf_share_the_flat_figure(tmp_path):
    from vibeview import export_pdf, export_svg, figure2d
    from vibeview.elements import cpk_hex

    assert "flat_figure(" in inspect.getsource(export_svg.export_svg)
    assert "flat_figure(" in inspect.getsource(export_pdf.export_pdf)
    with QVFReader(_water(tmp_path)) as reader:
        circles, segments = figure2d.flat_figure(reader.read_structure(), "ball_and_stick")
        svg = export_svg.export_svg(reader, str(tmp_path / "w.svg"))
    assert [c[2] for c in circles] == [cpk_hex(8), cpk_hex(1), cpk_hex(1)]
    assert len(segments) == 2
    assert f'fill="{cpk_hex(8)}"' in svg and svg.count("<line") == 2


def test_projection_keeps_planar_molecules_spread():
    from vibeview.figure2d import projection

    planar = np.array([[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [0.0, 1.0, 1.0]])
    centre, axes = projection(planar)
    flat = (planar - centre) @ axes
    assert np.ptp(flat[:, 0]) > 0.5 and np.ptp(flat[:, 1]) > 0.5


def test_blender_and_html_embed_only_the_elements_present(tmp_path):
    from vibeview.blender_export import export_blender_script
    from vibeview.elements import COVALENT_RADII, cpk_hex
    from vibeview.export_html import export_html

    with QVFReader(_water(tmp_path)) as reader:
        script = export_blender_script(reader, str(tmp_path / "w.py"))
        export_html(reader, tmp_path / "w.html")
    assert "RADII_JSON" in script and "CPK_JSON" in script
    assert "6: 0.76" not in script  # no carbon in water, no carbon in the table
    html = (tmp_path / "w.html").read_text()
    assert f'"8": {COVALENT_RADII[8]}' in html and '"6":' not in html
    assert str(int(cpk_hex(8).lstrip("#"), 16)) in html
    assert "atomMats[z]" in html  # one material per element


# ── #40-21: one cell builder ────────────────────────────────────────────


def test_converters_build_cells_with_the_crystal_builder():
    source = inspect.getsource(converters)
    assert source.count("cell_from_abc(") == 2  # CIF and PDB
    assert "vol_factor" not in source


def test_cif_cell_matches_cell_from_abc():
    from vibeview.crystal_builder import cell_from_abc

    cif = (
        "data_x\n_cell_length_a 5.0\n_cell_length_b 6.0\n_cell_length_c 7.0\n"
        "_cell_angle_alpha 80\n_cell_angle_beta 85\n_cell_angle_gamma 95\n"
        "loop_\n_atom_site_label\n_atom_site_fract_x\n_atom_site_fract_y\n_atom_site_fract_z\n"
        "Na1 0.0 0.0 0.0\nCl1 0.5 0.5 0.5\n"
    )
    with QVFReader(converters.cif_to_qvf(cif.encode())) as reader:
        lattice = np.asarray(reader.read_structure().lattice_vectors)
    np.testing.assert_allclose(lattice, cell_from_abc(5.0, 6.0, 7.0, 80, 85, 95), atol=1e-9)


# ── #40-23: one format registry ─────────────────────────────────────────


def test_extensions_and_detection_derive_from_the_capability_table():
    table = {
        ext: cap.format_name
        for cap in converters._BUILTIN_FORMATS
        if cap.provider in ("built-in", "Gemmi optional extra")
        for ext in cap.extensions
    }
    assert table == converters._EXTENSION_TO_FORMAT
    assert set(converters._NATIVE_EXTENSIONS) == set(table)
    assert converters.detect_format("thing.com") == "gjf"
    assert converters.detect_format("POSCAR") == "ase"
    assert converters.detect_format("x.py") == "py"
    assert "known = {" not in inspect.getsource(converters.detect_format)
    doc = converters.detect_format.__doc__ or ""
    assert ".py" in doc and "POSCAR" in doc and "plugin:" in doc


# ── #40-25 / #40-29: one source reader, CIF loops as rows ───────────────


def test_converters_read_their_source_through_one_helper():
    source = inspect.getsource(converters)
    assert source.count("Path(source).read_bytes()") == 1
    for name in ("xyz", "cif", "cube", "pdb", "mol2", "gjf", "gro", "sdf"):
        converter = getattr(converters, f"{name}_to_qvf")
        assert "_read_source(source)" in inspect.getsource(converter), name
    assert converters._read_source(b"abc") == "abc"
    assert converters._read_source(io.BytesIO(b"xyz")) == "xyz"


def test_cif_loops_are_rows():
    items, loops = converters._cif_items(
        "data_t\n_cell_length_a 5\nloop_\n_a\n_b\n1 2\n3 4\n"
    )
    assert items == {"_cell_length_a": "5"}
    assert loops == [[{"_a": "1", "_b": "2"}, {"_a": "3", "_b": "4"}]]
    assert not any("_0" in key for key in items)


# ── #40-31 / #40-33 / #40-34 / #40-35 / #40-36 / #40-39: docs and pages ──


def test_manual_rows_match_the_converters():
    formats = (_ROOT / "docs" / "formats.md").read_text()
    assert "placeholder `CRYST1`" in formats or "placeholder" in formats
    assert "all nine box values" in formats
    assert "numbered label" in formats
    headless = (_ROOT / "docs" / "headless.md").read_text()
    assert "connectivity the viewer shows" in headless
    from vibeview import export_pdf

    assert "vector PDF" in (export_pdf.export_pdf.__doc__ or "")
