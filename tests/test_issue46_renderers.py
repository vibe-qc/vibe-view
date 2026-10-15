"""Regressions for the renderer refactor, documentation and performance rows in #46.

Each test names the audit row it pins (#36-N in the issue body).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np
import pytest

from vibeview.qvf import QVFReader
from vibeview.qvf_writer import QVFArchiveBuilder

_DATA = Path(__file__).resolve().parent / "data"
_WATER = [
    {"symbol": "O", "atomic_number": 8, "position": [0.0, 0.0, 0.1173]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, 0.7572, -0.4692]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, -0.7572, -0.4692]},
]


# ── #36-12 / #36-38: dead code is gone ───────────────────────────────────


def test_dead_dos_code_is_gone():
    from vibeview.renderers import dos

    assert not hasattr(dos, "make_bands_dos_html")
    assert "fermi_referenced" not in inspect.getsource(dos.DOSRenderer.render_to_html)


def test_renderer_registry_is_built_once():
    from vibeview import renderers

    first = renderers.renderer_registry()
    assert renderers.renderer_registry() is first
    assert first["structure"].__name__ == "StructureRenderer"
    assert "def get_renderer" in inspect.getsource(renderers)


# ── #36-13 / #36-14 / #36-30: typed readers, read once ───────────────────


def test_renderers_do_not_reach_into_private_reader_members():
    import vibeview.renderers as package

    root = Path(package.__file__).parent
    offenders = [
        path.name
        for path in root.glob("*.py")
        if "reader._read_" in path.read_text() or "self.reader._" in path.read_text()
    ]
    assert offenders == []


def test_bands_projections_are_read_through_the_reader_and_cached(tmp_path):
    from vibeview.renderers.bands import BandsRenderer

    eig = np.linspace(-3.0, 3.0, 4 * 2).reshape(1, 4, 2)
    proj = np.random.default_rng(0).random((4, 2, 3))
    b = QVFArchiveBuilder(calculation="bands")
    b.add_json(
        "bands0", "bands", "kpath",
        {"n_kpoints": 4, "n_bands": 2, "n_spin": 1, "segments": [], "channels": ["s", "p", "d"]},
    )
    b.add_binary("bands0", "bands", "eigenvalues", eig)
    b.add_binary("bands0", "bands", "projections", proj)
    reader = QVFReader(b.write(tmp_path / "b.qvf"))
    got, channels = reader.read_bands_projections("bands0")
    np.testing.assert_allclose(got, proj)
    assert channels == ["s", "p", "d"]

    renderer = BandsRenderer(reader.get_section("bands0"), reader)
    calls = []
    original = reader.read_bands_projections
    reader.read_bands_projections = lambda sid: calls.append(sid) or original(sid)  # type: ignore[method-assign]
    renderer._load_projections()
    renderer._load_projections()
    assert calls == ["bands0"]


def test_dos_and_fermi_readers_are_typed(tmp_path):
    b = QVFArchiveBuilder(calculation="dos")
    b.add_binary("dos0", "dos.total", "energies", np.linspace(-5, 5, 11))
    b.add_binary("dos0", "dos.total", "dos", np.ones(11))
    b.add_binary("pdos", "dos.projected", "energies", np.linspace(-5, 5, 11))
    b.add_binary(
        "pdos", "dos.projected", "projections", np.ones((2, 3, 11)),
        section_extra={"channels": [{"label": "a"}, {"label": "b"}, {"label": "c"}]},
    )
    b.add_json("fs", "fermi_surface", "mesh", {"lattice_vectors": np.eye(3).tolist()})
    b.add_binary("fs", "fermi_surface", "energies", np.zeros((2, 2, 2, 1)))
    with QVFReader(b.write(tmp_path / "d.qvf")) as reader:
        total = reader.read_dos("dos0")
        assert not total.projected and total.values.shape == (11,) and total.channels == []
        projected = reader.read_dos("pdos")
        assert projected.projected and projected.values.shape == (3, 11)
        assert projected.channels == [{"label": "a"}, {"label": "b"}, {"label": "c"}]
        np.testing.assert_allclose(projected.values, 2.0)  # spin summed
        fermi = reader.read_fermi_surface("fs")
        assert fermi.energies.shape == (2, 2, 2, 1)
        assert fermi.mesh["lattice_vectors"] == np.eye(3).tolist()


def test_wavefunction_centre_structure_is_parsed_once():
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    reader = QVFReader(_DATA / "h2_natural_orbitals.qvf")
    renderer = WavefunctionRenderer(reader.get_section("wf"), reader)
    calls = []
    original = reader.read_structure
    reader.read_structure = lambda sid="structure": calls.append(sid) or original(sid)  # type: ignore[method-assign]
    renderer.evaluate_mo(0, n_per_dim=6)
    renderer.evaluate_mo(1, n_per_dim=6)
    assert calls == ["structure"]


# ── #36-15 / #36-16 / #36-34: the frame renderers share one cache ────────


def test_frame_renderers_share_the_mixin():
    from vibeview.renderers._frames import FrameSequenceMixin
    from vibeview.renderers.reaction import ReactionPathRenderer
    from vibeview.renderers.trajectory import TrajectoryRenderer

    assert issubclass(TrajectoryRenderer, FrameSequenceMixin)
    assert issubclass(ReactionPathRenderer, FrameSequenceMixin)
    for name in ("render_energy_plot", "has_energies", "get_energy", "n_frames"):
        assert getattr(TrajectoryRenderer, name) is getattr(FrameSequenceMixin, name)


def test_reaction_volume_mesh_is_contoured_once_per_slab(monkeypatch):
    import types

    from vibeview.qvf import GridData, ReactionPathData
    from vibeview.renderers.reaction import ReactionPathRenderer

    field = np.zeros((4, 4, 4), dtype=np.float32)
    field[1:3, 1:3, 1:3] = 1.0
    data = ReactionPathData(
        atoms=[],
        coords=np.zeros((4, 1, 3)),
        energies=None,
        reaction_coordinate=None,
        waypoints=[],
        frame_volumes=np.stack([field, field * 2.0]),
        volume_grid=GridData(np.zeros(3), np.eye(3), (4, 4, 4)),
        volume_frame_index=[0, 2],
        volume_isovalue=0.5,
    )
    r = ReactionPathRenderer.__new__(ReactionPathRenderer)
    r._frame_meshes = {}
    r.load = lambda: data  # type: ignore[method-assign]
    calls = []
    from vibeview.renderers import volume

    def counting(*args, **kwargs):
        calls.append(args[2])
        return types.SimpleNamespace(n_points=1)

    monkeypatch.setattr(volume, "build_isosurface_mesh", counting)
    r.volume_mesh_for_frame(0)
    r.volume_mesh_for_frame(1)  # same slab as frame 0
    r.volume_mesh_for_frame(2)
    r.volume_mesh_for_frame(2, isovalue=0.9)
    assert calls == [0.5, 0.5, 0.9]


# ── #36-17 / #36-18 / #36-19: one trace or actor per family ──────────────


def test_energy_diagram_uses_one_marker_trace(tmp_path):
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    reader = QVFReader(_DATA / "h2_natural_orbitals.qvf")
    html = WavefunctionRenderer(reader.get_section("wf"), reader).render_energy_diagram(
        include_plotlyjs=False
    )
    assert html.count('"mode":"markers"') <= 3  # levels + HOMO/LUMO (HONO/LUNO) diamonds


def test_spectra_sticks_are_one_none_separated_trace():
    from vibeview.renderers.spectra import stick_segments

    xs, ys = stick_segments(np.array([1.0, 2.0, np.nan, 4.0]), np.array([0.5, 0.0, 1.0, -0.25]))
    assert xs == [1.0, 1.0, None, 4.0, 4.0, None]
    assert ys == [0.0, 0.5, None, 0.0, -0.25, None]


def test_qtaim_points_are_one_glyph_actor_per_class(tmp_path):
    import pyvista as pv

    from vibeview.renderers.qtaim import QTAIMRenderer

    points = [
        {"type": "bcp", "position": [0.5, 0, 0], "laplacian": -0.5},
        {"type": "bcp", "position": [1.5, 0, 0], "laplacian": -0.2},
        {"type": "bcp", "position": [2.5, 0, 0], "laplacian": 0.3},
        {"type": "rcp", "position": [0, 1, 0]},
        {"type": "rcp", "position": [0, 2, 0]},
    ]
    b = QVFArchiveBuilder(calculation="qtaim")
    b.add_json("qtaim", "topology.qtaim", "critical_points", {"points": points})
    with QVFReader(b.write(tmp_path / "q.qvf")) as reader:
        plotter = pv.Plotter(off_screen=True)
        QTAIMRenderer(reader.get_section("qtaim"), reader).add_to_plotter(plotter)
    names = [n for n in plotter.actors if str(n).startswith("qtaim_cp_")]
    assert len(names) == 3  # shared-shell BCPs, closed-shell BCPs, RCPs
    assert all(str(n).startswith("qtaim_") for n in names)


# ── #36-21: the middle eigenvalue without the (n, 3, 3) block ────────────


def test_middle_eigenvalue_matches_eigvalsh():
    from vibeview.renderers.nci import middle_eigenvalue

    rng = np.random.default_rng(3)
    a = rng.normal(size=(500, 3, 3))
    sym = (a + np.swapaxes(a, 1, 2)) / 2
    sym[0] = np.diag([3.0, -1.0, 2.0])  # diagonal: the p == 0 branch
    sym[1] = np.eye(3) * 4.0  # degenerate
    expected = np.linalg.eigvalsh(sym)[:, 1]
    got = middle_eigenvalue(
        sym[:, 0, 0], sym[:, 0, 1], sym[:, 0, 2], sym[:, 1, 1], sym[:, 1, 2], sym[:, 2, 2]
    )
    np.testing.assert_allclose(got, expected, atol=1e-9)


# ── #36-26 / #36-31 / #36-32 / #36-35 / #36-36 ──────────────────────────────


def test_nmr_and_epr_share_the_column_picker():
    from vibeview.renderers import epr, nmr
    from vibeview.renderers._html import column_order

    assert column_order([{"b": 1, "a": 2}, {"c": 3}], ("a", "z")) == ["a", "b", "c"]
    assert "column_order(" in inspect.getsource(nmr) and "column_order(" in inspect.getsource(epr)
    assert "len(tensors) if isinstance" not in inspect.getsource(nmr)


def test_component_vocabulary_is_shared():
    from vibeview.renderers.volume import COMPONENTS, apply_component
    from vibeview.renderers.wavefunction import _ORBITAL_COMPONENTS

    z = np.array([3.0 + 4.0j])
    assert apply_component(z, "magnitude") == apply_component(z, "abs") == 5.0
    assert set(_ORBITAL_COMPONENTS) <= set(COMPONENTS)


def test_band_axis_ticks_is_public():
    from vibeview.renderers import bands, dos, phonon
    from vibeview.tui import plots

    assert hasattr(bands, "band_axis_ticks") and not hasattr(bands, "_band_axis_ticks")
    for module in (dos, phonon, plots):
        assert "_band_axis_ticks" not in inspect.getsource(module)


def test_one_shell_walk_and_one_grid_builder():
    from vibeview.renderers import wavefunction as wf

    src = inspect.getsource(wf.WavefunctionRenderer)
    assert src.count("for shell in wf.shells:") == 1, "the three evaluators share _shell_blocks"
    assert src.count("_molecular_grid(") == 5
    assert not hasattr(wf, "_spherical_factors") and not hasattr(wf, "_cartesian_factors")


@pytest.mark.parametrize("index", [0, 1])
def test_evaluators_agree_after_the_walk_consolidation(index):
    """Value, gradient and Hessian walkers must give the same psi."""
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    reader = QVFReader(_DATA / "h2_natural_orbitals.qvf")
    r = WavefunctionRenderer(reader.get_section("wf"), reader)
    wf = r.load()
    coeffs = r._mo_row(wf, index, "restricted")
    pos = r._atom_positions_bohr(wf.structure_ref)
    xs = ys = zs = np.linspace(-2.0, 2.0, 5)
    psi = r._evaluate_on_grid(wf, coeffs, pos, xs, ys, zs)
    psi_g, _grad = r._evaluate_gradient_on_grid(wf, coeffs, pos, xs, ys, zs)
    psi_h, _g, _h = r._evaluate_hessian_on_grid(wf, coeffs, pos, xs, ys, zs)
    np.testing.assert_allclose(psi_g, psi, atol=1e-12)
    np.testing.assert_allclose(psi_h, psi, atol=1e-12)


# ── #36-41 / #36-45: documentation and citation ──────────────────────────


def test_envelope_names_itself_in_the_legend():
    from vibeview.renderers import spectra

    src = inspect.getsource(spectra.SpectraRenderer.render_to_html)
    assert "Lorentzian envelope" in src


def test_circular_mean_is_cited():
    from vibeview.renderers import volume

    doc = volume._circular_centroid_index.__doc__ or ""
    assert "Mardia" in doc and "Fisher" in doc
