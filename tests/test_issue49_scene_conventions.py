"""vibe-view#49 audit rows #38-19, #38-21 and #38-22: one colour convention,
one chain ordering and one b-factor ramp across the terminal and the browser.
"""

from __future__ import annotations

import types

import numpy as np
import pytest

from vibeview.qvf import Atom, QVFReader, StructureData
from vibeview.renderers import palette
from vibeview.renderers import structure as rs
from vibeview.tui import scene as scenelib

_POS = palette.rgb(palette.ISO_POSITIVE)
_NEG = palette.rgb(palette.ISO_NEGATIVE)


def _actor_rgb(plotter, name: str) -> tuple[int, int, int]:
    color = plotter.actors[name].prop.color
    return tuple(int(round(c * 255)) for c in color.float_rgb)  # type: ignore[return-value]


# ── #38-19: + blue / − red everywhere (maintainer decision) ─────────────────


def test_terminal_lobes_use_the_shared_iso_palette():
    assert tuple(scenelib._ISO_POSITIVE) == _POS
    assert tuple(scenelib._ISO_NEGATIVE) == _NEG


def test_density_difference_accumulation_is_blue_depletion_red():
    import pyvista as pv

    from tests.test_plot_renderers import _density_qvf
    from vibeview.app import _activate_density_diff
    from vibeview.viewer_defaults import ViewerState

    xs = np.linspace(-2, 2, 16)
    g = np.meshgrid(xs, xs, xs, indexing="ij")
    bump = np.exp(-(g[0] ** 2 + g[1] ** 2 + g[2] ** 2)).astype(np.float32)
    pa = _density_qvf(bump, voxel=4 / 16)
    pb = _density_qvf(np.zeros_like(bump), voxel=4 / 16)
    try:
        readers = [QVFReader(pa), QVFReader(pb)]
        plotter = pv.Plotter(off_screen=True)
        state = types.SimpleNamespace(
            status_message="", compare_mode=False, compare_legend=[],
            active_volume_id=None, structure_hidden=False,
        )
        # A − B > 0: accumulation lobe only.
        _activate_density_diff(plotter, ViewerState(), state, readers, 0, 1)
        assert _actor_rgb(plotter, "volume_diff_pos") == _POS
        assert "blue = accumulation" in state.status_message
        # B − A < 0: depletion lobe only.
        plotter2 = pv.Plotter(off_screen=True)
        _activate_density_diff(plotter2, ViewerState(), state, readers, 1, 0)
        assert _actor_rgb(plotter2, "volume_diff_neg") == _NEG
        assert "red = depletion" in state.status_message
    finally:
        pa.unlink()
        pb.unlink()


def test_spin_density_excess_alpha_is_blue_excess_beta_red(tables_qvf, monkeypatch):
    import pyvista as pv

    from vibeview.app import _render_wf_density
    from vibeview.renderers.wavefunction import WavefunctionRenderer
    from vibeview.viewer_defaults import ViewerState

    n = 16
    xs = np.linspace(-2, 2, n)
    g = np.meshgrid(xs, xs, xs, indexing="ij")
    spin = (
        np.exp(-((g[0] - 1) ** 2 + g[1] ** 2 + g[2] ** 2))
        - np.exp(-((g[0] + 1) ** 2 + g[1] ** 2 + g[2] ** 2))
    )
    grid = types.SimpleNamespace(
        shape=spin.shape,
        voxel_vectors=np.eye(3) * (4 / n),
        to_angstrom=1.0,
        origin=np.array([-2.0, -2.0, -2.0]),
    )
    monkeypatch.setattr(
        WavefunctionRenderer,
        "evaluate_spin_density",
        lambda self, n_per_dim=60: (grid, spin, 0.0),
    )
    reader = QVFReader(tables_qvf)
    section = next(s for s in reader.sections if s.kind == "wavefunction.gto")
    plotter = pv.Plotter(off_screen=True)
    state = types.SimpleNamespace(
        wf_density_spin=True, wf_n_per_dim=n, isovalue=0.05,
        mo_visible=False, volume_loaded=False, status_message="",
    )
    message = _render_wf_density(reader, plotter, ViewerState(), state, section)
    assert _actor_rgb(plotter, "mo_iso_pos") == _POS
    assert _actor_rgb(plotter, "mo_iso_neg") == _NEG
    assert "blue = excess α, red = excess β" in message


def test_terminal_docs_state_the_shared_lobe_convention():
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[1] / "docs" / "terminal.md").read_text()
    assert "orange positive" not in doc
    assert "blue positive and red negative" in doc


# ── #38-21: one chain ordering (structure.chains()) ─────────────────────────


def _atom(symbol, z, chain, seq, name, x, b=None):
    return Atom(
        symbol, np.array([x, 0.0, 0.0]), z, atom_name=name, residue_name="GLY",
        residue_seq=seq, chain_id=chain, b_factor=b,
    )


def test_chain_colour_order_agrees_between_terminal_and_browser():
    """Supplied chains [A, B] while the atoms list B first: every surface
    must give chain B the same colour."""
    atoms = [
        _atom("C", 6, "B", 1, "CA", 0.0),
        _atom("N", 7, "B", 1, "N", 1.5),
        _atom("C", 6, "A", 1, "CA", 3.0),
        _atom("N", 7, "A", 1, "N", 4.5),
    ]
    st = StructureData(
        atoms=atoms, pbc=(False, False, False), lattice_vectors=None,
        bonds=None, supplied_chains=["A", "B"],
    )
    assert st.chain_ids() == ["A", "B"]  # the precondition that split them
    browser = {
        chain: tuple(int(v) for v in rs._hex_to_rgb(palette.chain_color(order)))
        for order, chain in enumerate(st.chains())
    }
    atom_rgb = scenelib._atom_colors(atoms, "chain", st)
    assert tuple(int(v) for v in atom_rgb[0]) == browser["B"]
    assert tuple(int(v) for v in atom_rgb[2]) == browser["A"]
    bb = scenelib._backbone_scene(st, "chain")
    assert bb is not None
    by_label = {lab: tuple(int(v) for v in c) for lab, c in zip(bb.labels, bb.colors, strict=True)}
    assert by_label["B1"] == browser["B"]
    assert by_label["A1"] == browser["A"]


# ── #38-22: the terminal b-factor ramp is the browser's ─────────────────────


def test_terminal_bfactor_uses_the_shared_ramp_and_grey_for_absent():
    atoms = [_atom("C", 6, "A", i + 1, "CA", 1.5 * i, b) for i, b in
             enumerate((10.0, 20.0, 30.0, None))]
    notes: list[str] = []
    colors = scenelib._atom_colors(atoms, "bfactor", None, notes)
    got = [tuple(int(v) for v in c) for c in colors]
    assert got[0] == palette.rgb(palette.BFACTOR_COLD)
    assert got[2] == palette.rgb(palette.BFACTOR_WARM)
    assert got[3] == palette.rgb(palette.BFACTOR_ABSENT)
    assert got == palette.sequential_ramp([10.0, 20.0, 30.0, float("nan")])


@pytest.mark.parametrize("value", [0.0, 15.0])
def test_constant_bfactor_sits_at_the_ramp_midpoint_like_the_browser(value):
    atoms = [_atom("C", 6, "A", i + 1, "CA", 1.5 * i, value) for i in range(4)]
    notes: list[str] = []
    colors = scenelib._atom_colors(atoms, "bfactor", None, notes)
    st = StructureData(atoms=atoms, pbc=(False, False, False), lattice_vectors=None, bonds=None)
    browser = tuple(int(v) for v in rs._cartoon_bfactor_colors(st, "A", 4, 4)[0])
    assert {tuple(int(v) for v in c) for c in colors} == {browser}
    assert any("b-factor" in n for n in notes), "a flat column is still reported"


def _p_orbital_qvf(tmp_path):
    """One pure p shell on one atom and three MOs, each a single p AO, so
    every MO has a positive and a negative lobe. Identity coefficients do
    not depend on the [n_mo, n_ao] vs [n_ao, n_mo] layout."""
    import json

    from tests.conftest import _water_structure_bytes, build_qvf

    basis = json.dumps({
        "structure_ref": "structure", "pure": True, "n_ao": 3,
        "shells": [{"center": 0, "l": 1, "exponents": [1.0], "coefficients": [1.0]}],
    }).encode()
    meta = json.dumps({
        "n_mo": 3, "n_ao": 3, "spin": "restricted", "orbital_kind": "canonical",
        "energies": [-0.5, -0.5, -0.5], "occupations": [2.0, 2.0, 2.0],
    }).encode()
    coeffs = np.eye(3, dtype=np.float64).tobytes()
    return build_qvf(tmp_path / "p.qvf", [
        ("structure", "structure",
         {"structure": ("json", "sections/structure.json", _water_structure_bytes())}),
        ("wf", "wavefunction.gto", {
            "basis": ("json", "wf/basis.json", basis),
            "mo_metadata": ("json", "wf/mo.json", meta),
            "mo_coefficients": ("binary", "wf/mo.dat", coeffs, "float64", [3, 3]),
        }),
    ])


def test_computed_mo_lobes_use_the_shared_iso_palette(tmp_path):
    """#38-19 (re-verification FAIL at 8cfeade): the browser's computed-MO
    path, _render_mo_volume, drew + #1f77b4 / − #d62728 while every other
    surface used palette.ISO_POSITIVE/ISO_NEGATIVE."""
    import pyvista as pv

    from vibeview.app import _render_mo_volume
    from vibeview.viewer_defaults import ViewerState

    reader = QVFReader(_p_orbital_qvf(tmp_path))
    try:
        section = reader.get_section("wf")
        plotter = pv.Plotter(off_screen=True)
        state = types.SimpleNamespace(
            wf_n_per_dim=30, wf_component=None, mo_opacity=1.0, isovalue=0.02,
            status_message="", volume_loaded=False,
        )
        _render_mo_volume(reader, plotter, ViewerState(), state, section, 0, "alpha")
        assert _actor_rgb(plotter, "mo_iso_pos") == _POS
        assert _actor_rgb(plotter, "mo_iso_neg") == _NEG
    finally:
        reader.close()


# ── #38-19: the browser clip path draws both lobes for every signed kind ────


_SIGNED_KINDS = (
    "volume.difference", "volume.spin", "volume.orbital", "volume.potential", "basis.ao",
)


def _signed_volume_qvf(tmp_path, kind):
    """A p_y-like field on a centred grid: positive at y > 0, negative at y < 0."""
    import json

    from tests.conftest import build_qvf

    n = 16
    xs = np.linspace(-2.0, 2.0, n)
    x, y, z = np.meshgrid(xs, xs, xs, indexing="ij")
    data = (y * np.exp(-(x**2 + y**2 + z**2))).astype("<f8")
    step = float(xs[1] - xs[0])
    grid = json.dumps({
        "origin": [-2.0, -2.0, -2.0],
        "voxel_vectors": (np.eye(3) * step).tolist(),
        "shape": [n, n, n],
    }).encode()
    return build_qvf(tmp_path / f"{kind}.qvf", [("v", kind, {
        "grid": ("json", "grid.json", grid),
        "data": ("binary", "data.bin", data.tobytes(), "float64", [n, n, n]),
    })])


class _ClipState(types.SimpleNamespace):
    def __getattr__(self, name):  # unset controller state reads as None
        return None


def _clip(reader, section):
    import pyvista as pv

    from vibeview.app import _activate_volume, _rebuild_clip
    from vibeview.viewer_defaults import ViewerState

    plotter = pv.Plotter(off_screen=True)
    viewer_state = ViewerState()
    state = _ClipState(status_message="", isovalue=0.1)
    _activate_volume(reader, plotter, viewer_state, state, section)
    state.clip_enabled = True
    state.show_slice = False
    state.active_volume_id = section.id
    state.clip_x = state.clip_y = 0.0
    state.clip_z = 0.3
    state.opacity = 0.6
    _rebuild_clip(reader, plotter, viewer_state, state)
    return plotter, state


@pytest.mark.parametrize("kind", _SIGNED_KINDS)
def test_clip_draws_both_lobes_in_the_shared_palette(tmp_path, kind):
    reader = QVFReader(_signed_volume_qvf(tmp_path, kind))
    try:
        plotter, state = _clip(reader, reader.get_section("v"))
        assert "Clip error" not in str(state.status_message)
        assert "_volume_clipped" not in plotter.actors, "single colormapped + lobe"
        pos, neg = plotter.actors["_volume_clipped_pos"], plotter.actors["_volume_clipped_neg"]
        assert _actor_rgb(plotter, "_volume_clipped_pos") == _POS
        assert _actor_rgb(plotter, "_volume_clipped_neg") == _NEG
        assert not pos.mapper.scalar_visibility and not neg.mapper.scalar_visibility
        # Sign placement: the + lobe sits at y > 0, the − lobe at y < 0.
        assert pos.mapper.dataset.center[1] > 0 > neg.mapper.dataset.center[1]
    finally:
        reader.close()


def test_clip_treats_the_component_as_a_label_like_the_full_render(tmp_path):
    """A stored section's ``component`` is a label only (maintainer decision
    2026-09-26): the clip contours the stored array, whatever it says."""
    reader = QVFReader(_signed_volume_qvf(tmp_path, "volume.orbital"))
    try:
        section = reader.get_section("v")
        object.__setattr__(section, "component", "abs")
        plotter, _state = _clip(reader, section)
        assert _actor_rgb(plotter, "_volume_clipped_pos") == _POS
        assert _actor_rgb(plotter, "_volume_clipped_neg") == _NEG
    finally:
        reader.close()


# ── #38-19: the browser cross-fade draws both lobes for every signed kind ───
#
# VERIFY_FAILED note 34594: `_rebuild_crossfade` contoured the + isovalue only
# and colour-mapped it, so a file whose viewer_defaults declare a cross-fade
# between two signed volumes lost every − lobe and the ISO palette. These
# tests open such a file through the real create_app (off-screen; the server
# is never started) and drive the reload and blend-slider paths too.


def _crossfade_qvf(tmp_path, kind, *, blend=0.5, opacity=0.8):
    """Two signed sections of ``kind``: ``a`` is + at y > 0 / − at y < 0 and
    ``b`` is + at x > 0 / − at x < 0; viewer_defaults cross-fades a -> b."""
    import json
    import zipfile

    from tests.conftest import build_qvf

    n = 20
    xs = np.linspace(-2.0, 2.0, n)
    x, y, z = np.meshgrid(xs, xs, xs, indexing="ij")
    field_a = np.exp(-(x**2 + (y - 1) ** 2 + z**2)) - np.exp(-(x**2 + (y + 1) ** 2 + z**2))
    field_b = np.exp(-((x - 1) ** 2 + y**2 + z**2)) - np.exp(-((x + 1) ** 2 + y**2 + z**2))
    grid = json.dumps({
        "origin": [-2.0, -2.0, -2.0],
        "voxel_vectors": (np.eye(3) * float(xs[1] - xs[0])).tolist(),
        "shape": [n, n, n],
    }).encode()
    structure = json.dumps({
        "atoms": [{"symbol": "H", "position": [0.0, 0.0, 0.0], "atomic_number": 1}],
        "pbc": [False, False, False],
    }).encode()

    def volume(tag, data):
        return {
            "grid": ("json", f"{tag}/grid.json", grid),
            "data": ("binary", f"{tag}/data.bin", data.astype("<f8").tobytes(),
                     "float64", [n, n, n]),
        }

    raw = build_qvf(tmp_path / f"raw-{kind}.qvf", [
        ("structure", "structure", {"structure": ("json", "structure.json", structure)}),
        ("a", kind, volume("a", field_a)),
        ("b", kind, volume("b", field_b)),
    ])
    with zipfile.ZipFile(raw) as zf:
        members = {name: zf.read(name) for name in zf.namelist()}
    manifest = json.loads(members["manifest.json"])
    manifest["viewer_defaults"] = {
        "crossfade": {"a": "a", "b": "b", "blend": blend},
        "a": {"isovalue": 0.3, "opacity": opacity},
        "b": {"isovalue": 0.3, "opacity": opacity},
    }
    members["manifest.json"] = json.dumps(manifest).encode()
    path = tmp_path / f"crossfade-{kind}.qvf"
    with zipfile.ZipFile(path, "w") as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)
    return path


def _plain_qvf(tmp_path):
    from tests.conftest import _water_structure_bytes, build_qvf

    return build_qvf(tmp_path / "plain.qvf", [
        ("structure", "structure", {"structure": ("json", "structure.json",
                                                  _water_structure_bytes())}),
    ])


def _open_app(monkeypatch, readers):
    """create_app on ``readers``; returns (server, the scene plotter)."""
    import vibeview.app as appmod

    plotters = []
    real_new_plotter = appmod.new_plotter

    def recording_new_plotter(*args, **kwargs):
        plotter = real_new_plotter(*args, **kwargs)
        plotters.append(plotter)
        return plotter

    monkeypatch.setattr(appmod, "new_plotter", recording_new_plotter)
    server = appmod.create_app(readers)
    assert plotters, "create_app built no plotter"
    return server, plotters[0]


def _assert_crossfade_lobes(plotter, *, opacity_a, opacity_b):
    for section_id, axis, opacity in (("a", 1, opacity_a), ("b", 0, opacity_b)):
        assert f"volume_{section_id}" not in plotter.actors, (
            f"volume_{section_id}: one colour-mapped + surface instead of two lobes"
        )
        if opacity < 0.01:
            assert f"volume_{section_id}_pos" not in plotter.actors
            assert f"volume_{section_id}_neg" not in plotter.actors
            continue
        pos = plotter.actors[f"volume_{section_id}_pos"]
        neg = plotter.actors[f"volume_{section_id}_neg"]
        assert _actor_rgb(plotter, f"volume_{section_id}_pos") == _POS
        assert _actor_rgb(plotter, f"volume_{section_id}_neg") == _NEG
        assert not pos.mapper.scalar_visibility and not neg.mapper.scalar_visibility
        # The + lobe sits where the field is positive, the − lobe opposite.
        assert pos.mapper.dataset.center[axis] > 0.3
        assert neg.mapper.dataset.center[axis] < -0.3
        assert pos.prop.opacity == pytest.approx(opacity)
        assert neg.prop.opacity == pytest.approx(opacity)


@pytest.mark.parametrize("kind", _SIGNED_KINDS)
def test_crossfade_at_open_draws_both_lobes_in_the_shared_palette(tmp_path, monkeypatch, kind):
    reader = QVFReader(_crossfade_qvf(tmp_path, kind))
    try:
        server, plotter = _open_app(monkeypatch, [reader])
        assert server.state.crossfade_active
        _assert_crossfade_lobes(plotter, opacity_a=0.8 * 0.5, opacity_b=0.8 * 0.5)
    finally:
        reader.close()


@pytest.mark.parametrize("kind", _SIGNED_KINDS)
def test_crossfade_on_file_switch_draws_both_lobes(tmp_path, monkeypatch, kind):
    """The reload path (_reload_active_file_unlocked) re-bootstraps the
    cross-fade for the incoming file; it must draw the same two lobes."""
    plain = QVFReader(_plain_qvf(tmp_path))
    reader = QVFReader(_crossfade_qvf(tmp_path, kind, blend=0.25))
    try:
        server, plotter = _open_app(monkeypatch, [plain, reader])
        assert not server.state.crossfade_active
        server.controller.switch_file(1)
        assert server.state.crossfade_active
        _assert_crossfade_lobes(plotter, opacity_a=0.8 * 0.75, opacity_b=0.8 * 0.25)
    finally:
        reader.close()
        plain.close()


@pytest.mark.parametrize("kind", _SIGNED_KINDS)
def test_crossfade_blend_slider_keeps_both_lobes(tmp_path, monkeypatch, kind):
    reader = QVFReader(_crossfade_qvf(tmp_path, kind))
    try:
        server, plotter = _open_app(monkeypatch, [reader])
        server.controller.update_crossfade(0.2)
        _assert_crossfade_lobes(plotter, opacity_a=0.8 * 0.8, opacity_b=0.8 * 0.2)
        # blend = 1 shows B only: A's lobes are dropped, B's are both drawn.
        server.controller.update_crossfade(1.0)
        _assert_crossfade_lobes(plotter, opacity_a=0.0, opacity_b=0.8)
    finally:
        reader.close()


def test_crossfade_treats_the_component_as_a_label_like_the_full_render(tmp_path, monkeypatch):
    """A stored section's ``component`` is a label only: the cross-fade
    contours the stored signed array, both lobes."""
    reader = QVFReader(_crossfade_qvf(tmp_path, "volume.orbital"))
    try:
        for section_id in ("a", "b"):
            object.__setattr__(reader.get_section(section_id), "component", "abs")
        _server, plotter = _open_app(monkeypatch, [reader])
        for section_id in ("a", "b"):
            assert _actor_rgb(plotter, f"volume_{section_id}_pos") == _POS
            assert _actor_rgb(plotter, f"volume_{section_id}_neg") == _NEG
            assert f"volume_{section_id}" not in plotter.actors
    finally:
        reader.close()


# ── #38-19: the terminal and the static capture ignore the component label ─
#
# Gen 6 made the terminal (tui.scene.volume_meshes, also `vibe-view show`) and
# the headless capture (capture.capture_volume, also batch, jupyter and the
# API) reduce the stored array by its ``component``, like the browser did. That
# was wrong for real archives (VERIFY_FAILED note 34785): QVF §4.1 calls the
# component a hint, §4.2 allows only real dtypes, and the producer stores the
# already-reduced array. Maintainer decision 2026-09-26: the label never
# transforms a real stored array, on any path. A stored signed field therefore
# draws both lobes whatever its label; tests/test_issue49_component_label.py
# covers producer-shaped archives (|psi| for abs, psi**2 for density).

# component label -> (+ lobe expected, − lobe expected) for a stored signed psi.
_COMPONENT_LOBES = {
    None: (True, True),
    "real": (True, True),
    "abs": (True, True),
    "magnitude": (True, True),
    "density": (True, True),
    "imag": (True, True),
}


def _orbital_with_component(tmp_path, component):
    reader = QVFReader(_signed_volume_qvf(tmp_path, "volume.orbital"))
    object.__setattr__(reader.get_section("v"), "component", component)
    return reader


@pytest.mark.parametrize("component", list(_COMPONENT_LOBES))
def test_terminal_volume_ignores_the_component_label(tmp_path, component):
    reader = _orbital_with_component(tmp_path, component)
    try:
        colors = {tuple(mesh.color) for mesh in scenelib.volume_meshes(reader, "v", isovalue=0.1)}
        want_pos, want_neg = _COMPONENT_LOBES[component]
        assert (_POS in colors) is want_pos
        assert (_NEG in colors) is want_neg
    finally:
        reader.close()


@pytest.mark.parametrize("component", list(_COMPONENT_LOBES))
def test_static_capture_ignores_the_component_label(tmp_path, monkeypatch, component):
    import vibeview.capture as capture

    drawn: dict[str, tuple[int, int, int]] = {}

    def record(plotter, path=None):
        drawn.update({
            name: _actor_rgb(plotter, name) for name in plotter.actors if name.startswith("capture")
        })

    monkeypatch.setattr(capture, "snapshot", record)
    reader = _orbital_with_component(tmp_path, component)
    try:
        assert capture.capture_volume(reader, "v", tmp_path / "v.png", isovalue=0.1)
        want_pos, want_neg = _COMPONENT_LOBES[component]
        assert ("capture_pos" in drawn) is want_pos
        assert ("capture_neg" in drawn) is want_neg
        assert "capture_vol" not in drawn
        if want_pos:
            assert drawn["capture_pos"] == _POS
    finally:
        reader.close()
