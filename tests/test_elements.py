"""Independent identity, radius and rendering contracts for shared element data."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from vibeview.elements import (
    COVALENT_RADII,
    CPK_HEX,
    SYMBOLS,
    VDW_RADII,
    covalent_radius,
    cpk_hex,
    symbol_to_z,
    vdw_radius,
    z_to_symbol,
)


@pytest.mark.parametrize(
    "z,symbol,covalent,color",
    [
        (1, "H", 0.31, "#FFFFFF"),
        (6, "C", 0.76, "#909090"),
        (8, "O", 0.66, "#FF0D0D"),
        (26, "Fe", 1.32, "#E06633"),
        (57, "La", 2.07, "#70D4FF"),
        (78, "Pt", 1.36, "#D0D0E0"),
        (79, "Au", 1.36, "#FFD123"),
        (92, "U", 1.96, "#008FFF"),
        (96, "Cm", 1.69, "#785CE3"),
    ],
)
def test_reference_element_values(z, symbol, covalent, color):
    # Selected Cordero 2008 radii and Jmol colors, independently specified.
    assert z_to_symbol(z) == symbol
    assert symbol_to_z(symbol) == z
    assert covalent_radius(z, default=-1) == covalent
    assert cpk_hex(z) == color


def test_coverage_and_symbol_roundtrip():
    assert len(SYMBOLS) == 97
    assert SYMBOLS[0] == ""
    assert set(COVALENT_RADII) == set(CPK_HEX) == set(range(1, 97))
    assert set(VDW_RADII) == set(range(1, 21))
    for z in range(1, 97):
        assert symbol_to_z(z_to_symbol(z)) == z


@pytest.mark.parametrize("z", [-1, 0, 97, 999])
def test_unknown_elements_do_not_gain_a_false_identity(z):
    assert z_to_symbol(z) == "X"
    assert z_to_symbol(z, default=f"Z{z}") == f"Z{z}"
    assert cpk_hex(z) == "#FF1493"
    assert covalent_radius(z, default=1.5) == 1.5
    assert covalent_radius(z, default=0.8) == 0.8
    assert vdw_radius(z) == 1.7


def test_lookup_does_not_interpret_format_specific_atom_names():
    assert symbol_to_z("Ca") == 20
    assert symbol_to_z("CA") == 0
    assert symbol_to_z("HE1") == 0
    assert symbol_to_z("unknown", default=6) == 6


@pytest.mark.parametrize("z,radius", [(1, 1.20), (3, 1.82), (6, 1.70), (20, 2.31)])
def test_retained_vdw_display_values(z, radius):
    # Historical viewer values are deliberately retained, including H and Li.
    assert vdw_radius(z) == radius


def test_radius_estimate_and_renderer_scaling_are_preserved():
    from vibeview.renderers.structure import cpk_radius

    assert vdw_radius(26) == pytest.approx(2.112)
    assert vdw_radius(92) == pytest.approx(3.136)
    assert cpk_radius(92, scale=0.35) == pytest.approx(1.0976)
    assert cpk_radius(999, scale=0.35) == pytest.approx(0.595)


def test_stock_palette_remains_separate_from_renderer_overrides():
    from vibeview.renderers.structure import (
        cpk_color,
        get_color_overrides,
        set_color_overrides,
    )

    previous = get_color_overrides()
    try:
        set_color_overrides({6: "#123456", 92: "#ABCDEF"})
        assert cpk_color(6) == "#123456"
        assert cpk_color(92) == "#ABCDEF"
        assert cpk_color(8) == "#FF0D0D"
        assert cpk_hex(6) == "#909090"
        snapshot = get_color_overrides()
        snapshot[6] = "#000000"
        assert cpk_color(6) == "#123456"
        set_color_overrides(None)
        assert cpk_color(6) == "#909090"
        assert cpk_color(92) == "#008FFF"
    finally:
        set_color_overrides(previous)


@pytest.mark.parametrize("z,symbol", [(57, "La"), (78, "Pt"), (79, "Au"), (92, "U"), (96, "Cm")])
@pytest.mark.parametrize("use_symbol", [False, True])
def test_python_input_preserves_heavy_element_identity(tmp_path, z, symbol, use_symbol):
    from vibeview.converters import py_to_qvf
    from vibeview.qvf import QVFReader

    # Lowercase names retain the Python parser's existing case normalization.
    label = repr(symbol.lower()) if use_symbol else str(z)
    path = tmp_path / "heavy.py"
    path.write_text(f"mol = Molecule([Atom({label}, [0.0, 0.0, 2.0])])\n")
    with QVFReader(py_to_qvf(path)) as reader:
        (atom,) = reader.read_structure().atoms
        assert (atom.atomic_number, atom.symbol) == (z, symbol)
        assert atom.position.tolist() == pytest.approx([0.0, 0.0, 1.058354421806])


@pytest.mark.parametrize("z", [0, 97, 999])
def test_python_input_keeps_unknown_numeric_labels(tmp_path, z):
    from vibeview.converters import py_to_qvf
    from vibeview.qvf import QVFReader

    path = tmp_path / "unknown.py"
    path.write_text(f"mol = Molecule([Atom({z}, [0.0, 0.0, 0.0])])\n")
    with QVFReader(py_to_qvf(path)) as reader:
        (atom,) = reader.read_structure().atoms
        assert (atom.atomic_number, atom.symbol) == (z, f"Z{z}")


@pytest.mark.parametrize(
    "symbol,z,style,radius,color",
    [
        ("U", 92, "ball_and_stick", 1.0976, "#008fff"),
        ("U", 92, "space_filling", 3.43, "#008fff"),
        ("Xx", 0, "ball_and_stick", 0.595, "#ff1493"),
        ("Xx", 0, "space_filling", 2.625, "#ff1493"),
    ],
)
def test_symbol_only_rendering_and_style_radius_policies(symbol, z, style, radius, color):
    import pyvista as pv

    from vibeview.converters import atoms_to_qvf
    from vibeview.qvf import QVFReader
    from vibeview.renderers.structure import StructureRenderer

    with QVFReader(atoms_to_qvf([(z, 0, 0, 0)])) as reader:
        renderer = StructureRenderer(reader.sections[0], reader)
        (atom,) = renderer.load_structure().atoms
        atom.symbol = symbol
        atom.atomic_number = 0  # exercise identity fallback, not the parsed-Z path
        plotter = pv.Plotter(off_screen=True)
        try:
            renderer.add_to_plotter(plotter, representation=style)
            (actor,) = plotter.actors.values()
            assert actor.prop.color.hex_rgb == color
            # The sphere has exact poles along z, independent of mesh resolution.
            assert actor.bounds.z_max == pytest.approx(radius)
            assert actor.bounds.z_min == pytest.approx(-radius)
        finally:
            plotter.close()


def test_element_module_loads_without_reader_or_rendering_dependencies():
    import vibeview.elements

    # Load the module in a fresh interpreter without importing the package's
    # convenience API, which has its own reader dependencies.
    path = str(Path(vibeview.elements.__file__).resolve())
    script = (
        "import runpy, sys\n"
        "data = runpy.run_path(sys.argv[1])\n"
        "assert data['symbol_to_z']('U') == 92\n"
        "assert not {'numpy', 'pyvista', 'vibeview.qvf', 'vibeview.converters'}"
        ".intersection(sys.modules)\n"
    )
    subprocess.run([sys.executable, "-c", script, path], check=True)
