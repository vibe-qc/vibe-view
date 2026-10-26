"""Independent geometry oracles and real capture coverage for molecular inspection."""

from pathlib import Path

import numpy as np
import pytest

from vibeview.converters import pdb_to_qvf
from vibeview.elements import vdw_radius
from vibeview.headless import headless_plotter
from vibeview.interactions import find_interactions
from vibeview.molecular_inspection import add_inspection, sample_potential
from vibeview.molecular_selection import select, within
from vibeview.molecular_surfaces import build_surface
from vibeview.qvf import Atom, GridData, QVFReader, StructureData

FIXTURES = Path(__file__).parent / "fixtures/inspection"


def molecule(symbols, points, bonds=None):
    numbers = {"C": 6, "O": 8, "N": 7, "H": 1, "Cl": 17}
    return StructureData(
        [
            Atom(s, np.array(p, dtype=float), numbers[s])
            for s, p in zip(symbols, points, strict=True)
        ],
        (False,) * 3,
        None,
        bonds,
    )


@pytest.mark.parametrize("kind", ["vdw", "sas", "ses"])
def test_analytic_isolated_sphere(kind):
    s = molecule(["C"], [[0, 0, 0]], [])
    surface = build_surface(s, kind=kind, spacing=0.1)
    r = vdw_radius(6) + (1.4 if kind == "sas" else 0)
    assert surface.area == pytest.approx(4 * np.pi * r * r, rel=0.05)
    assert surface.volume == pytest.approx(4 * np.pi * r**3 / 3, rel=0.05)
    assert surface.mesh.n_open_edges == 0


@pytest.mark.parametrize("kind", ["vdw", "sas"])
def test_analytic_overlapping_spheres(kind):
    s = molecule(["C", "C"], [[0, 0, 0], [2, 0, 0]], [])
    surface = build_surface(s, kind=kind, spacing=0.1)
    r = vdw_radius(6) + (1.4 if kind == "sas" else 0)
    # Equal spheres d=2: subtract two spherical caps from area and lens from volume.
    d = 2
    area = 8 * np.pi * r * r - 4 * np.pi * r * (r - d / 2)
    volume = 8 * np.pi * r**3 / 3 - np.pi * (4 * r + d) * (2 * r - d) ** 2 / 12
    assert surface.area == pytest.approx(area, rel=0.03)
    assert surface.volume == pytest.approx(volume, rel=0.03)
    if kind == "sas":
        ses = build_surface(s, kind="ses", spacing=0.1)
        vdw = build_surface(s, kind="vdw", spacing=0.1)
        assert vdw.volume < ses.volume < surface.volume


def test_selection_and_refusals():
    s = molecule(["C", "C", "O"], [[0, 0, 0], [2, 0, 0], [9, 0, 0]], [])
    assert select(s, "within 2 of (index 1)") == {0, 1}
    assert within(s, set(), 4) == set()
    for bad in ["index 0", "index 8", "nonsense"]:
        with pytest.raises(ValueError):
            select(s, bad)
    for kwargs in [{"selection": "none"}, {"probe": -1}, {"spacing": 0}, {"max_voxels": 1}]:
        with pytest.raises(ValueError):
            build_surface(s, **kwargs)


def test_water_and_halogen_geometry():
    water = molecule(
        ["O", "H", "O"], [[0, 0, 0], [0.96, 0, 0], [2.8, 0, 0]], [(0, 1, 1.0, (0, 0, 0))]
    )
    (row,) = find_interactions(water)
    assert row.atoms == (0, 1, 2) and row.distance == pytest.approx(2.8)
    assert row.angle == pytest.approx(180)
    assert not find_interactions(water, hbond_distance=2.7)
    assert not find_interactions(water, selection="index 1,2")
    assert find_interactions(water, selection="index 1", partner="index 3") == [row]
    halogen = molecule(
        ["C", "Cl", "O"], [[0, 0, 0], [1.8, 0, 0], [4.8, 0, 0]], [(0, 1, 1.0, (0, 0, 0))]
    )
    (row,) = find_interactions(halogen, kinds=("halogen",))
    assert row.distance == pytest.approx(3) and row.angle == pytest.approx(180)
    halogen.atoms[2].position = np.array([1.8, 3, 0.0])
    assert not find_interactions(halogen, kinds=("halogen",))


def test_protein_ligand_handchecked_contact_set():
    # 1STP biotin O3 against three named streptavidin donor atoms. These distances
    # are independently calculable from the deposited coordinates, not H-bonds:
    # no explicit hydrogens are present in this X-ray structure.
    with QVFReader(pdb_to_qvf(FIXTURES / "1stp.pdb")) as reader:
        s = reader.read_structure()
    target = next(
        i for i, a in enumerate(s.atoms) if a.residue_name == "BTN" and a.atom_name == "O3"
    )
    selected = [
        i
        for i, a in enumerate(s.atoms)
        if (a.residue_seq, a.atom_name) in {(23, "ND2"), (27, "OG"), (43, "OH")}
    ]
    rows = find_interactions(
        s,
        selection="index " + str(target + 1),
        partner="index " + ",".join(str(i + 1) for i in selected),
        kinds=("contact", "hbond"),
    )
    assert [r.kind for r in rows] == ["contact"] * 3
    np.testing.assert_allclose(
        [r.distance for r in rows], [2.9923806576, 2.7710461923, 2.5849104433], atol=1e-9
    )


def test_salt_and_explicit_aromatic_rings():
    theta = np.arange(6) * np.pi / 3
    ring = np.column_stack((np.cos(theta), np.sin(theta), np.zeros(6)))
    s = molecule(["C"] * 12, np.vstack((ring, ring + [0, 0, 3.5])), [])
    rings = (tuple(range(6)), tuple(range(6, 12)))
    (row,) = find_interactions(s, kinds=("pi",), rings=rings)
    assert row.distance == pytest.approx(3.5) and row.angle == pytest.approx(0)
    assert not find_interactions(s, kinds=("pi",), rings=rings, selection="index 1,7")
    charged = molecule(["N", "O"], [[0, 0, 0], [3.5, 0, 0]], [])
    (row,) = find_interactions(charged, kinds=("salt",), positive=(0,), negative=(1,))
    assert row.distance == 3.5


def test_skew_potential_grid_sampling_and_coverage():
    axes = np.array([[1.0, 0, 0], [0.2, 1, 0], [0, 0.3, 1]])
    grid = GridData(np.zeros(3), axes, (4, 4, 4), units="angstrom")
    values = np.indices((4, 4, 4)).sum(axis=0).astype(float)
    points = np.array([[0.5, 1.5, 2.5]]) @ axes
    np.testing.assert_allclose(sample_potential(points, grid, values), [4.5])
    with pytest.raises(ValueError, match="cover"):
        sample_potential(np.array([[99.0, 0, 0]]), grid, values)


@pytest.mark.parametrize("style,representation", [("solid", 2), ("mesh", 1), ("dots", 0)])
def test_same_mesh_capture_styles_and_colour_key(style, representation):
    s = molecule(["C"], [[0, 0, 0]], [])
    with headless_plotter((240, 180)) as plotter:
        result = add_inspection(plotter, s, {"surface": "vdw", "style": style, "spacing": 0.2})
        actor = plotter.actors["molecular_surface"]
        assert actor.GetProperty().GetRepresentation() == representation
        assert actor.mapper.dataset.n_cells > 100
        from trame_vtk.modules.vtk.serializers.mesh import mesh as vtk_mesh

        payload = vtk_mesh(actor.mapper.dataset)
        assert payload["mesh"]["points"] and payload["mesh"]["polys"]
        assert result["surface"]["area_A2"] > 30
        assert len(plotter.scalar_bars) == 1
        image = plotter.screenshot(return_img=True)
        assert np.std(image) > 5


def test_property_modes_and_missing_data():
    from vibeview.molecular_inspection import colour_surface

    s = molecule(["C"], [[0, 0, 0]], [])
    s.atoms[0].residue_name = "ALA"
    s.atoms[0].chain_id = "A"
    surface = build_surface(s, kind="vdw", spacing=0.3)
    assert colour_surface(surface, s, "chain")[2] == {0.0: "A"}
    colour_surface(surface, s, "hydrophobicity")
    np.testing.assert_allclose(surface.mesh["property"], 1.8)
    for mode in ["potential", "mulliken", "loewdin", "hirshfeld", "iao"]:
        with pytest.raises(ValueError):
            colour_surface(surface, s, mode)


def test_controller_inspection_rebuild_and_refusal(water_qvf):
    from trame.app import get_server

    from vibeview.app import create_app

    with QVFReader(water_qvf) as reader:
        create_app(reader)
        server = get_server(client_type="vue3")
        state = server.state
        state.inspection_surface = "sas"
        state.inspection_spacing = 0.3
        server.controller.apply_inspection()
        assert "Å²" in state.inspection_summary
        state.inspection_selection = "index 999999"
        server.controller.apply_inspection()
        assert "out of range" in state.inspection_summary
        assert state.inspection_rows == []


@pytest.mark.parametrize("kind", ["vdw", "sas", "ses"])
def test_crambin_reference_area_volume(kind):
    import json

    references = json.loads((FIXTURES / "protein-reference.json").read_text())
    with QVFReader(pdb_to_qvf(FIXTURES / "1crn.pdb")) as reader:
        structure = reader.read_structure()
    surface = build_surface(structure, kind=kind, spacing=0.25)
    tolerance = 0.1 if kind == "ses" else 0.05
    assert surface.area == pytest.approx(references[kind]["area_reference"], rel=tolerance)
    assert surface.volume == pytest.approx(references[kind]["volume_reference"], rel=tolerance)


def test_ses_missing_optional_dependency(monkeypatch):
    import sys

    s = molecule(["C"], [[0, 0, 0]], [])
    monkeypatch.setitem(sys.modules, "scipy.ndimage", None)
    with pytest.raises(ImportError, match="SES requires scipy"):
        build_surface(s, kind="ses", spacing=0.3)
    assert build_surface(s, kind="sas", spacing=0.3).volume > 0


WATER_DIMER = (
    ["O", "H", "H", "O", "H", "H"],
    [[-1.551007, -0.114520, 0.0], [-1.934259, 0.762503, 0.0], [-0.599677, 0.040712, 0.0],
     [1.350625, 0.111469, 0.0], [1.680398, -0.373741, -0.758561],
     [1.680398, -0.373741, 0.758561]],
)


def test_element_surface_uses_the_atoms_cpk_colours_without_a_rainbow_seam():
    """Served-browser acceptance of #125: element colouring drew H blue and O
    light cyan (tab20 by sorted symbol), and interpolating the category index
    across each O/H seam swept through the whole table."""
    import pyvista as pv

    from vibeview.renderers.structure import cpk_color

    s = molecule(*WATER_DIMER)
    with headless_plotter((480, 360)) as plotter:
        plotter.set_background("black")
        add_inspection(plotter, s, {"surface": "vdw", "colour": "element", "opacity": 1.0})
        mapper = plotter.actors["molecular_surface"].mapper
        lut = mapper.lookup_table
        table = [lut.GetTableValue(i)[:3] for i in range(lut.GetNumberOfTableValues())]
        np.testing.assert_allclose(
            table, [pv.Color(cpk_color(1)).float_rgb, pv.Color(cpk_color(8)).float_rgb]
        )
        assert tuple(mapper.scalar_range) == (-0.5, 1.5)
        assert not mapper.GetInterpolateScalarsBeforeMapping()
        plotter.view_isometric()
        image = plotter.screenshot(return_img=True).astype(int)
    lit = image[image.max(axis=2) > 60]
    # Every lit surface pixel is a shade of red or of white: none is bluish
    # or greenish, as the old table and its seams were.
    tinted = (lit[:, 2] > lit[:, 0] + 30) | (lit[:, 1] > lit[:, 0] + 30)
    assert len(lit) > 1000
    assert tinted.mean() < 0.01


def test_chain_surface_colours_follow_the_ribbon_palette_in_file_order():
    from vibeview.molecular_inspection import colour_surface
    from vibeview.renderers import palette

    s = molecule(["C", "C", "C"], [[0, 0, 0], [4, 0, 0], [8, 0, 0]], [])
    for atom, chain in zip(s.atoms, ["B", "A", None], strict=True):
        atom.chain_id = chain
    surface = build_surface(s, kind="vdw", spacing=0.3)
    _, colours, key = colour_surface(surface, s, "chain")
    assert key == {0.0: "(no chain)", 1.0: "A", 2.0: "B"}
    assert colours == [palette.GUIDE_GREY, palette.chain_color(1), palette.chain_color(0)]
