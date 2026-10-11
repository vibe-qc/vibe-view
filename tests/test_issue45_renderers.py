"""Regressions for the renderer-correctness findings in issue #45.

Each test names the audit row it pins (#36-N in the issue body).
"""

from __future__ import annotations

import types
from dataclasses import fields
from pathlib import Path

import numpy as np
import pytest

from vibeview.qvf import Atom, GridData, QVFReader, ReactionPathData, TrajectoryData
from vibeview.qvf_writer import QVFArchiveBuilder

_DATA = Path(__file__).resolve().parent / "data"


def _archive(tmp_path: Path, builder: QVFArchiveBuilder) -> QVFReader:
    return QVFReader(builder.write(tmp_path / "t.qvf"))


# ── #36-4: bands axis title without segments ─────────────────────────────


def test_bands_axis_title_survives_a_kpath_without_segments(tmp_path):
    from vibeview.renderers.bands import BandsRenderer

    eig = np.linspace(-3.0, 3.0, 5 * 2).reshape(1, 5, 2)
    b = QVFArchiveBuilder(calculation="bands")
    b.add_json(
        "bands0", "bands", "kpath",
        {"n_kpoints": 5, "n_bands": 2, "n_spin": 1, "segments": [], "fermi": 0.0},
    )
    b.add_binary("bands0", "bands", "eigenvalues", eig)
    reader = _archive(tmp_path, b)
    html = BandsRenderer(reader.get_section("bands0"), reader).render_to_html(
        include_plotlyjs=False
    )
    assert "k-point" in html


# ── #36-5: one frontier rule for the picker and the diagram ──────────────


def _fake_wavefunction_renderer(**wf_fields):
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    defaults = dict(
        spin="restricted",
        alpha_energies=None,
        alpha_occupations=None,
        beta_energies=None,
        beta_occupations=None,
        orbital_kind="canonical",
        occupation_semantics=None,
    )
    defaults.update(wf_fields)
    r = WavefunctionRenderer.__new__(WavefunctionRenderer)
    r._wf = None
    r.reader = types.SimpleNamespace(provenance={})
    r.load = lambda: types.SimpleNamespace(**defaults)  # type: ignore[method-assign]
    return r


def test_energy_diagram_integer_occupations_mark_the_frontier():
    r = _fake_wavefunction_renderer(
        energies=np.array([-1.0, -0.5, -0.2, 0.1, 0.3]),
        occupations=np.array([2.0, 2.0, 2.0, 0.0, 0.0]),
    )
    html = r.render_energy_diagram(include_plotlyjs=False)
    assert '"HOMO"' in html and '"LUMO"' in html
    assert "gap" in html


def test_energy_diagram_fractional_occupations_have_no_frontier():
    """Smearing has a Fermi level, not a HOMO; the old ``occ > 0.001`` rule
    still crowned the last partially filled level."""
    r = _fake_wavefunction_renderer(
        energies=np.array([-1.0, -0.5, -0.2, 0.1, 0.3]),
        occupations=np.array([2.0, 2.0, 1.5, 0.5, 0.0]),
    )
    html = r.render_energy_diagram(include_plotlyjs=False)
    assert '"HOMO"' not in html and '"LUMO"' not in html
    assert "gap" not in html


def test_energy_diagram_natural_orbitals_need_confirmed_occupations():
    energies = np.array([-1.0, -0.5, 0.2, 0.4])
    counts = np.array([1.98, 1.90, 0.10, 0.02])
    unmarked = _fake_wavefunction_renderer(
        energies=energies, occupations=counts, orbital_kind="natural"
    )
    html = unmarked.render_energy_diagram(include_plotlyjs=False)
    assert "HONO" not in html and "HOMO" not in html

    confirmed = _fake_wavefunction_renderer(
        energies=energies,
        occupations=counts,
        orbital_kind="natural",
        occupation_semantics="electron_occupation",
    )
    confirmed.reader = types.SimpleNamespace(provenance={"n_electrons": 4})
    html = confirmed.render_energy_diagram(include_plotlyjs=False)
    assert '"HONO"' in html and '"LUNO"' in html
    assert "restricted:1" in html  # the HONO is the last integer-rounded occupied level


def test_picker_and_diagram_share_the_frontier_rule():
    from vibeview.renderers import wavefunction as wf_mod

    occ = np.array([2.0, 2.0, 0.0])
    assert wf_mod._block_frontier("canonical", occ, 3, natural_confirmed=False) == (1, 2)
    assert wf_mod._block_frontier("localized", occ, 3, natural_confirmed=False) == (-1, -1)
    assert wf_mod._block_frontier("natural", occ, 3, natural_confirmed=False) == (-1, -1)
    assert wf_mod._block_frontier("natural", occ, 3, natural_confirmed=True) == (1, 2)


# ── #36-6: one NCI convention ────────────────────────────────────────────


def test_computed_nci_uses_the_stored_volume_convention(monkeypatch):
    """The wavefunction NCI path draws with nci.py's isovalue, colormap and
    colour range instead of its own 0.5 / coolwarm pair."""
    import pyvista as pv

    from vibeview import app as app_mod
    from vibeview.renderers.nci import COLOR_CLIM, RDG_ISOVALUE
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    assert app_mod.RDG_ISOVALUE == RDG_ISOVALUE

    shape = (7, 7, 7)
    x = np.linspace(-1.0, 1.0, 7)
    radius = np.sqrt(x[:, None, None] ** 2 + x[None, :, None] ** 2 + x[None, None, :] ** 2)
    rdg = radius.astype(np.float32)  # crosses s = 0.3 inside the box
    signed_rho = (x[:, None, None] * 0.1 * np.ones(shape)).astype(np.float32)
    grid = GridData(np.full(3, -1.0), np.eye(3) * 0.4, shape, units="angstrom")

    monkeypatch.setattr(
        WavefunctionRenderer, "evaluate_nci", lambda self, n_per_dim=60: (grid, rdg, signed_rho)
    )
    reader = QVFReader(_DATA / "h2_natural_orbitals.qvf")
    plotter = pv.Plotter(off_screen=True)
    state = types.SimpleNamespace(wf_n_per_dim=7, wf_nci_iso=None)
    viewer_state = types.SimpleNamespace(replication=(1, 1, 1))
    message = app_mod._render_wf_nci(
        reader, plotter, viewer_state, state, reader.get_section("wf"), update_state=False
    )
    assert f"s = {RDG_ISOVALUE:g}" in message
    actor = plotter.actors["mo_iso_nci"]
    lo, hi = actor.mapper.scalar_range
    assert (lo, hi) == pytest.approx((-COLOR_CLIM, COLOR_CLIM))
    assert actor.mapper.array_name == "color"


# ── #36-7: array energies ────────────────────────────────────────────────


def test_trajectory_energy_plot_accepts_array_energies():
    from vibeview.renderers.trajectory import TrajectoryRenderer

    r = TrajectoryRenderer.__new__(TrajectoryRenderer)
    r._frame_pngs = {}
    data = TrajectoryData(
        atoms=[Atom(symbol="H", position=np.zeros(3), atomic_number=1)],
        coords=np.zeros((3, 1, 3)),
        energies=np.array([0.0, -0.5, -1.0]),
    )
    r.load = lambda: data  # type: ignore[method-assign]
    assert r.has_energies()
    assert r.render_energy_plot(1).startswith(b"\x89PNG")

    r_empty = TrajectoryRenderer.__new__(TrajectoryRenderer)
    r_empty._frame_pngs = {}
    r_empty.load = lambda: TrajectoryData(  # type: ignore[method-assign]
        atoms=data.atoms, coords=data.coords, energies=np.array([])
    )
    assert r_empty.render_energy_plot(0) == b""


def test_reaction_energy_plot_accepts_array_energies():
    from vibeview.renderers.reaction import ReactionPathRenderer

    r = ReactionPathRenderer.__new__(ReactionPathRenderer)
    r._frame_pngs = {}
    data = ReactionPathData(
        atoms=[Atom(symbol="H", position=np.zeros(3), atomic_number=1)],
        coords=np.zeros((3, 1, 3)),
        energies=np.array([0.0, 0.7, -1.0]),
        reaction_coordinate=None,
        waypoints=[],
    )
    r.load = lambda: data  # type: ignore[method-assign]
    assert r.has_energies()
    assert r.render_energy_plot(2).startswith(b"\x89PNG")


# ── #36-8: COOP axis unit and label handling ─────────────────────────────


def _coop_reader(tmp_path: Path, meta: dict) -> QVFReader:
    b = QVFArchiveBuilder(calculation="coop")
    b.add_binary("coop0", "dos.coop", "energies", np.linspace(-5.0, 5.0, 11))
    b.add_binary("coop0", "dos.coop", "projections", np.ones((2, 11)) * 0.1)
    b.add_binary("coop0", "dos.coop", "integrated", np.array([0.5, -0.25]))
    b.add_json("coop0", "dos.coop", "meta", meta)
    return _archive(tmp_path, b)


def test_coop_axis_is_ev_without_a_fermi_tag(tmp_path):
    from vibeview.renderers.coop import COOPRenderer

    reader = _coop_reader(tmp_path, {"pair_labels": ["C1-C2", "C2-O3"]})
    html = COOPRenderer(reader.get_section("coop0"), reader).render_to_html(
        include_plotlyjs=False
    )
    assert "Energy (eV)" in html


def test_coop_labels_are_escaped_and_kept_out_of_the_template(tmp_path):
    from vibeview.renderers.coop import COOPRenderer

    hostile = "<b>C1-C2</b> %{y}"
    reader = _coop_reader(tmp_path, {"pair_labels": [hostile, "ok"], "fermi_energy_ev": 0.0})
    html = COOPRenderer(reader.get_section("coop0"), reader).render_to_html(
        include_plotlyjs=False
    )
    # Plotly's embedded JSON writes ``/`` as a unicode escape.
    decoded = html.replace("\\u002f", "/")
    assert hostile not in decoded
    assert "&lt;b&gt;C1-C2&lt;/b&gt;" in decoded
    assert "%{text}" in decoded


def test_pdos_channel_labels_are_escaped_and_kept_out_of_the_template(tmp_path):
    """#36-9: a producer-supplied PDOS channel label reaches Plotly's legend
    and hover, which render HTML and expand ``%{...}`` in templates."""
    import json
    import re

    from vibeview.renderers.dos import DOSRenderer

    hostile = "<img src=x onerror=alert(1)>%{x}"
    b = QVFArchiveBuilder(calculation="pdos")
    extra = {"n_spin": 1, "channels": [{"label": hostile}, {"label": "ok"}]}
    b.add_binary(
        "pdos0", "dos.projected", "energies", np.linspace(-5.0, 5.0, 11),
        section_extra=extra,
    )
    b.add_binary("pdos0", "dos.projected", "projections", np.ones((2, 11)) * 0.1)
    reader = _archive(tmp_path, b)
    html = DOSRenderer(reader.get_section("pdos0"), reader).render_to_html(
        include_plotlyjs=False
    )
    # Plotly's embedded JSON writes ``/``, ``<`` and ``>`` as unicode escapes.
    decoded = (
        html.replace("\\u002f", "/").replace("\\u003c", "<").replace("\\u003e", ">")
    )
    assert "<img" not in decoded
    assert "&lt;img src=x onerror=alert(1)&gt;%{x}" in decoded
    templates = [
        json.loads(f'"{t}"')
        for t in re.findall(r'"hovertemplate":"((?:[^"\\]|\\.)*)"', html)
    ]
    assert len(templates) == 2
    for template in templates:
        assert "onerror" not in template and "img" not in template
        assert "%{text}" in template


# ── #36-11: material presets ─────────────────────────────────────────────


def test_material_preset_has_no_dead_fields():
    import inspect

    from vibeview import material_presets
    from vibeview.material_presets import MaterialPreset

    consumed = inspect.getsource(material_presets.apply_material_to_actor)
    for field in fields(MaterialPreset):
        if field.name in {"name", "background_color"}:
            continue  # read by the preset list and the scene background
        assert f"preset.{field.name}" in consumed, f"{field.name} has no consumer"
    assert not {"atom_metallic", "bond_roughness", "iso_opacity", "toon_levels"} & {
        f.name for f in fields(MaterialPreset)
    }


def test_isosurface_actors_take_the_preset_lighting():
    import pyvista as pv

    from vibeview.material_presets import apply_material_to_actor, get_preset

    preset = get_preset("glass")
    plotter = pv.Plotter(off_screen=True)
    actor = plotter.add_mesh(pv.Sphere(), name="mo_iso_pos")
    apply_material_to_actor(actor, preset, base_color=(1.0, 0.0, 0.0), is_isosurface=True)
    prop = actor.GetProperty()
    assert prop.GetAmbient() == pytest.approx(preset.iso_ambient)
    assert prop.GetDiffuse() == pytest.approx(preset.iso_diffuse)
    assert prop.GetSpecular() == pytest.approx(preset.iso_specular)
    assert prop.GetSpecularPower() == pytest.approx(preset.iso_specular_power)

    atom = plotter.add_mesh(pv.Sphere(), name="atom_0_0")
    apply_material_to_actor(atom, preset, base_color=(1.0, 0.0, 0.0))
    assert atom.GetProperty().GetSpecular() == pytest.approx(preset.atom_specular)


def test_toon_rendering_takes_no_levels():
    import inspect

    from vibeview.renderers.structure import enable_toon_rendering

    assert "levels" not in inspect.signature(enable_toon_rendering).parameters


# ── #36-33: GridData knows its unit ──────────────────────────────────────


def test_griddata_defaults_to_bohr_and_rejects_other_units():
    from vibeview.units import BOHR_TO_ANGSTROM

    stored = GridData(np.zeros(3), np.eye(3), (2, 2, 2))
    assert stored.units == "bohr"
    assert stored.to_angstrom == pytest.approx(BOHR_TO_ANGSTROM)
    sampled = GridData(np.zeros(3), np.eye(3), (2, 2, 2), units="angstrom")
    assert sampled.to_angstrom == 1.0
    with pytest.raises(ValueError, match="grid units"):
        GridData(np.zeros(3), np.eye(3), (2, 2, 2), units="nm")  # type: ignore[arg-type]


@pytest.mark.parametrize("units", ["bohr", "angstrom"])
def test_isosurface_mesh_reads_the_unit_from_the_grid(units):
    from vibeview.renderers.volume import build_isosurface_mesh

    shape = (5, 5, 5)
    x = np.linspace(-1.0, 1.0, 5)
    field = -(x[:, None, None] ** 2 + x[None, :, None] ** 2 + x[None, None, :] ** 2)
    grid = GridData(np.full(3, -2.0), np.eye(3), shape, units=units)
    mesh = build_isosurface_mesh(field, grid, -0.5)
    assert mesh is not None and mesh.n_points > 0
    # The contour sits at the same grid coordinates; only the scale differs.
    centre = mesh.points.mean(axis=0)
    assert centre == pytest.approx(np.zeros(3) * grid.to_angstrom, abs=1e-6)
    extent = mesh.points.max(axis=0) - mesh.points.min(axis=0)
    assert extent[0] == pytest.approx(extent[0] / grid.to_angstrom * grid.to_angstrom)
    assert extent[0] < 4.0 * grid.to_angstrom + 1e-6


def test_sampled_wavefunction_grids_are_tagged_angstrom():
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    reader = QVFReader(_DATA / "h2_natural_orbitals.qvf")
    renderer = WavefunctionRenderer(reader.get_section("wf"), reader)
    grid, _ = renderer.evaluate_mo(0, n_per_dim=8)
    assert grid.units == "angstrom"


def test_replicated_grid_keeps_its_unit():
    from vibeview.renderers.volume import replicate_volume_for_periodic

    shape = (4, 4, 4)
    grid = GridData(np.zeros(3), np.eye(3) * 0.5, shape, units="angstrom")
    data = np.arange(np.prod(shape), dtype=float).reshape(shape)
    _, new_grid = replicate_volume_for_periodic(data, grid, np.eye(3) * 2.0, (1, 0, 0))
    assert new_grid.units == "angstrom"


# ── #36-43: QTAIM units come from the producer ───────────────────────────


def _qtaim_reader(tmp_path: Path, program: str) -> QVFReader:
    b = QVFArchiveBuilder(program=program, version="1", calculation="qtaim")
    b.add_json(
        "qtaim", "topology.qtaim", "critical_points",
        {"points": [{"type": "bcp", "position": [0.7, 0.0, 0.0], "rho": 0.26,
                     "laplacian": -0.54, "ellipticity": 0.02, "atom_pair": [0, 1]}]},
    )
    return _archive(tmp_path, b)


def test_qtaim_table_names_units_only_for_a_known_producer(tmp_path):
    from vibeview.renderers.qtaim import QTAIMRenderer, critical_point_units

    assert critical_point_units("vibe-qc") is not None
    assert critical_point_units("vibeqc") is not None
    assert critical_point_units("someone-else") is None

    reader = _qtaim_reader(tmp_path, "vibe-qc")
    html = QTAIMRenderer(reader.get_section("qtaim"), reader).render_to_html(["H", "H"])
    assert "e/&#8491;<sup>3</sup>" in html
    assert "as written by the producer" not in html

    reader = _qtaim_reader(tmp_path, "someone-else")
    html = QTAIMRenderer(reader.get_section("qtaim"), reader).render_to_html(["H", "H"])
    assert "e/&#8491;" not in html
    assert "as written by the producer (someone-else)" in html
