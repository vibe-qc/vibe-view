"""vibe-view#49, #38-19: a stored volume's ``component`` is a label only.

Maintainer decision (2026-09-26), after VERIFY_FAILED note 34785. QVF §4.1
calls a section's ``component`` a hint, and §4.2 allows only real binary
dtypes. The producer (vibe-qc ``qvf_mo_data``) writes the array already
reduced -- |psi| for ``abs``, psi**2 for ``density`` -- and labels the section
with that component. Reducing the stored array again by its label contoured
psi**4 for ``density`` and zeros for ``imag``.

So no path transforms a real stored ``volume.*`` array by its label: the
browser (open/click, clip, 2D slice, cross-fade, ``make_mesh``), the terminal
and every static capture contour the stored array itself. The archives here
are shaped like the producer's output: a p_y orbital on a 40**3 grid, each
section storing the array its label describes. The reference numbers at
isovalue 0.05 are the independent verifier's (note 34785): a ``density``
section stores psi**2, whose + surface has 1440 points reaching y = 1.12.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import numpy as np
import pytest

from vibeview.qvf import QVFReader
from vibeview.renderers import palette
from vibeview.renderers.volume import build_isosurface_mesh

_POS = palette.rgb(palette.ISO_POSITIVE)
_NEG = palette.rgb(palette.ISO_NEGATIVE)

ISO = 0.05
_N = 40
_XS = np.linspace(-4.0, 4.0, _N)
_X, _Y, _Z = np.meshgrid(_XS, _XS, _XS, indexing="ij")
#: p_y: + at y > 0, − at y < 0, max |psi| ~ 0.61.
_PSI = _Y * np.exp(-(_X**2 + _Y**2 + _Z**2) / 2)

#: label -> the array a producer stores under it (vibe-qc qvf_mo_data).
_STORED = {
    "density": _PSI**2,
    "abs": np.abs(_PSI),
    "imag": _PSI,  # e.g. Im psi of a complex orbital, stored as real floats
    "real": _PSI,
    None: _PSI,
}
_LABELS = list(_STORED)

_GRID = json.dumps({
    "origin": [-4.0, -4.0, -4.0],
    "voxel_vectors": (np.eye(3) * 8.0 / (_N - 1)).tolist(),
    "shape": [_N] * 3,
    "units": "angstrom",
}).encode()
_H = json.dumps({
    "atoms": [{"symbol": "H", "position": [0.0, 0.0, 0.0], "atomic_number": 1}],
    "pbc": [False] * 3,
}).encode()


def _qvf(tmp_path: Path, label: str | None, *, kind: str = "volume.orbital",
         ids: tuple[str, ...] = ("v",)) -> Path:
    data = _STORED[label].astype("<f8").tobytes()
    sections = [("structure", "structure", {"structure": ("json", "s.json", _H)})]
    for sid in ids:
        sections.append((sid, kind, {
            "grid": ("json", f"{sid}/g.json", _GRID),
            "data": ("binary", f"{sid}/d.bin", data, "float64", [_N] * 3),
        }))
    extra = {sid: {"component": label} for sid in ids} if label is not None else None
    from tests.conftest import build_qvf

    return build_qvf(tmp_path / f"{kind}-{label}.qvf", sections, extra=extra)


def _expected(reader, label: str | None) -> dict[str, tuple[int, float] | None]:
    """The ± contours of the STORED array, untransformed."""
    stored = _STORED[label]
    grid = reader.read_volume_grid("v")
    out: dict[str, tuple[int, float] | None] = {}
    for lobe, field in (("pos", np.clip(stored, 0, None)), ("neg", np.clip(-stored, 0, None))):
        out[lobe] = _summary(build_isosurface_mesh(field, grid, ISO), lobe)
    return out


@pytest.fixture
def archive(tmp_path):
    return lambda label, **kwargs: _qvf(tmp_path, label, **kwargs)


def _summary(mesh, lobe: str = "pos") -> tuple[int, float] | None:
    """Point count and the lobe's far extent: y max for +, y min for −."""
    if mesh is None or not mesh.n_points:
        return None
    return mesh.n_points, round(float(mesh.bounds[3] if lobe == "pos" else mesh.bounds[2]), 2)


def _actor_rgb(plotter, name: str) -> tuple[int, int, int]:
    color = plotter.actors[name].prop.color
    return tuple(int(round(c * 255)) for c in color.float_rgb)  # type: ignore[return-value]


class _State(types.SimpleNamespace):
    def __getattr__(self, name):  # unset controller state reads as None
        return None


def _browser_activate(reader):
    import pyvista as pv

    from vibeview.app import _activate_volume
    from vibeview.viewer_defaults import ViewerState

    plotter = pv.Plotter(off_screen=True)
    viewer_state = ViewerState()
    viewer_state.get_volume_hints("v", kind="volume.orbital").isovalue = ISO
    state = _State(status_message="", isovalue=ISO)
    _activate_volume(reader, plotter, viewer_state, state, reader.get_section("v"))
    return plotter, viewer_state, state


def _lobes(plotter, prefix: str) -> dict[str, tuple[int, float] | None]:
    out = {}
    for lobe in ("pos", "neg"):
        name = f"{prefix}_{lobe}"
        if name in plotter.actors:
            assert _actor_rgb(plotter, name) == (_POS if lobe == "pos" else _NEG)
            out[lobe] = _summary(plotter.actors[name].mapper.dataset, lobe)
        else:
            out[lobe] = None
    return out


# ── the reference numbers ───────────────────────────────────────────────────


def test_density_reference_is_the_verifiers_surface(archive):
    with QVFReader(archive("density")) as reader:
        assert _expected(reader, "density") == {"pos": (1440, 1.12), "neg": None}


# ── browser ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("label", _LABELS)
def test_browser_open_contours_the_stored_array(archive, label):
    with QVFReader(archive(label)) as reader:
        plotter, _vs, state = _browser_activate(reader)
        assert "error" not in str(state.status_message).lower()
        assert _lobes(plotter, "volume_v") == _expected(reader, label)


def test_browser_open_density_is_the_psi_squared_surface(archive):
    with QVFReader(archive("density")) as reader:
        plotter, _vs, _state = _browser_activate(reader)
        assert _lobes(plotter, "volume_v") == {"pos": (1440, 1.12), "neg": None}


@pytest.mark.parametrize("label", _LABELS)
def test_browser_clip_contours_the_stored_array(archive, label):
    from vibeview.app import _rebuild_clip

    with QVFReader(archive(label)) as reader:
        plotter, viewer_state, state = _browser_activate(reader)
        state.clip_enabled = True
        state.show_slice = False
        state.active_volume_id = "v"
        state.clip_x = state.clip_y = state.clip_z = 0.0
        _rebuild_clip(reader, plotter, viewer_state, state)
        drawn = {lobe: f"_volume_clipped_{lobe}" in plotter.actors for lobe in ("pos", "neg")}
        want = {lobe: v is not None for lobe, v in _expected(reader, label).items()}
        assert drawn == want
        for lobe, present in drawn.items():
            if present:
                assert _actor_rgb(plotter, f"_volume_clipped_{lobe}") == (
                    _POS if lobe == "pos" else _NEG
                )


@pytest.mark.parametrize("label", _LABELS)
def test_browser_2d_slice_shows_the_stored_array(archive, monkeypatch, label):
    import pyvista as pv

    from vibeview.app import _rebuild_clip

    stored = _STORED[label]
    added: dict[str, object] = {}
    real_add_mesh = pv.Plotter.add_mesh

    def spy(self, mesh, *args, **kwargs):
        added[str(kwargs.get("name"))] = mesh
        return real_add_mesh(self, mesh, *args, **kwargs)

    monkeypatch.setattr(pv.Plotter, "add_mesh", spy)
    with QVFReader(archive(label)) as reader:
        plotter, viewer_state, state = _browser_activate(reader)
        state.clip_enabled = True
        state.show_slice = True
        state.active_volume_id = "v"
        state.clip_x = state.clip_y = 0.5
        state.clip_z = 0.55
        _rebuild_clip(reader, plotter, viewer_state, state)
        values = np.asarray(added["_volume_slice"]["values"])
        assert values.min() >= float(stored.min()) - 1e-6
        assert values.max() <= float(stored.max()) + 1e-6
        # The plane near z = 0 holds most of the stored extremes, so it shows
        # neither psi**4 nor zeros.
        assert values.max() > 0.75 * float(stored.max())
        if stored.min() < 0:
            assert values.min() < 0.75 * float(stored.min())


@pytest.mark.parametrize("label", _LABELS)
def test_browser_crossfade_contours_the_stored_array(archive, label):
    import pyvista as pv

    from vibeview.app import _rebuild_crossfade
    from vibeview.viewer_defaults import ViewerState

    with QVFReader(archive(label, ids=("v", "w"))) as reader:
        plotter = pv.Plotter(off_screen=True)
        viewer_state = ViewerState()
        for sid in ("v", "w"):
            viewer_state.get_volume_hints(sid, kind="volume.orbital").isovalue = ISO
        _rebuild_crossfade(reader, plotter, viewer_state, _State(), "v", "w", 0.5)
        for sid in ("v", "w"):
            assert f"volume_{sid}" not in plotter.actors
            assert _lobes(plotter, f"volume_{sid}") == _expected(reader, label)


@pytest.mark.parametrize("label", _LABELS)
def test_make_mesh_contours_the_stored_array(archive, label):
    from vibeview.renderers.volume import VolumeRenderer
    from vibeview.viewer_defaults import VolumeHints

    with QVFReader(archive(label)) as reader:
        mesh = VolumeRenderer(reader.get_section("v"), reader).make_mesh(VolumeHints(isovalue=ISO))
        want = build_isosurface_mesh(_STORED[label], reader.read_volume_grid("v"), ISO)
        assert _summary(mesh) == _summary(want)
    if label == "density":
        assert _summary(mesh) == (1440, 1.12)


# ── terminal ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("label", _LABELS)
def test_terminal_contours_the_stored_array(archive, label):
    from vibeview.tui import scene as scenelib

    with QVFReader(archive(label)) as reader:
        meshes = scenelib.volume_meshes(reader, "v", isovalue=ISO)
        want = _expected(reader, label)
    got: dict[str, tuple[int, float] | None] = {"pos": None, "neg": None}
    for mesh in meshes:
        lobe = {_POS: "pos", _NEG: "neg"}[tuple(mesh.color)]
        y = mesh.vertices[:, 1]
        got[lobe] = (len(mesh.vertices), round(float(y.max() if lobe == "pos" else y.min()), 2))
    assert got == want


# ── static captures ─────────────────────────────────────────────────────────


@pytest.fixture
def captured(monkeypatch):
    import vibeview.capture as capture

    drawn: dict[str, object] = {}

    def record(plotter, path=None):
        for name in plotter.actors:
            if name.startswith("capture"):
                lobe = "neg" if name.endswith("_neg") else "pos"
                drawn[name] = (
                    _actor_rgb(plotter, name),
                    _summary(plotter.actors[name].mapper.dataset, lobe),
                )
        if path is not None:
            Path(path).write_bytes(b"\x89PNG")

    monkeypatch.setattr(capture, "snapshot", record)
    return drawn


def _capture_lobes(drawn) -> dict[str, tuple[int, float] | None]:
    assert "capture_vol" not in drawn
    out = {}
    for lobe, color in (("pos", _POS), ("neg", _NEG)):
        entry = drawn.get(f"capture_{lobe}")
        if entry is None:
            out[lobe] = None
        else:
            assert entry[0] == color
            out[lobe] = entry[1]
    return out


@pytest.mark.parametrize("label", _LABELS)
def test_capture_volume_contours_the_stored_array(archive, captured, tmp_path, label):
    import vibeview.capture as capture

    with QVFReader(archive(label)) as reader:
        assert capture.capture_volume(reader, "v", tmp_path / "v.png", isovalue=ISO)
        assert _capture_lobes(captured) == _expected(reader, label)


@pytest.mark.parametrize("label", ["density", "imag", "abs"])
def test_batch_capture_contours_the_stored_array(archive, captured, tmp_path, label):
    from vibeview.batch import render_volume_png

    with QVFReader(archive(label)) as reader:
        assert render_volume_png(reader, tmp_path / "v.png", "v", isovalue=ISO)
        assert _capture_lobes(captured) == _expected(reader, label)


@pytest.mark.parametrize("label", ["density", "imag", "abs"])
def test_api_capture_contours_the_stored_array(archive, captured, tmp_path, label):
    from vibeview import api

    path = archive(label)
    assert api.capture_volume(path, "v", tmp_path / "v.png", isovalue=ISO)
    with QVFReader(path) as reader:
        assert _capture_lobes(captured) == _expected(reader, label)


@pytest.mark.parametrize("label", ["density", "imag", "abs"])
def test_jupyter_capture_draws_the_stored_lobes(archive, captured, label):
    from vibeview.jupyter import _capture_section

    path = archive(label)
    assert _capture_section(path, "volume.orbital")
    got = {lobe: v is not None for lobe, v in _capture_lobes(captured).items()}
    with QVFReader(path) as reader:
        assert got == {lobe: v is not None for lobe, v in _expected(reader, label).items()}


# ── a complex stored array (not valid QVF v1): reduced by the label ─────────
#
# QVF v1 volume data is float32/float64 by schema, so a reader never yields a
# complex array from a valid archive. If one arrives anyway (a hand-built or
# future archive), it is reduced by the label -- the only place the label
# acts -- and to its real part when there is no label.


def _complex_reader(monkeypatch, tmp_path, label, kind="volume.orbital"):
    path = _qvf(tmp_path, "real", kind=kind)
    reader = QVFReader(path)
    section = reader.get_section("v")
    object.__setattr__(section, "component", label)
    field = (_PSI * (0.6 + 0.8j)).astype(np.complex128)
    real_read = QVFReader.read_volume_data

    def complex_read(self, section_id):
        real_read(self, section_id)
        return field

    monkeypatch.setattr(QVFReader, "read_volume_data", complex_read)
    return reader, field


@pytest.mark.parametrize(
    ("label", "reduce"),
    [
        (None, np.real),
        ("real", np.real),
        ("imag", np.imag),
        ("abs", np.abs),
        ("density", lambda z: np.abs(z) ** 2),
    ],
)
@pytest.mark.parametrize("kind", ["volume.orbital", "volume.potential"])
def test_complex_stored_array_is_reduced_by_its_label(monkeypatch, tmp_path, label, reduce, kind):
    from vibeview.renderers.volume import VolumeRenderer
    from vibeview.tui import scene as scenelib

    reader, field = _complex_reader(monkeypatch, tmp_path, label, kind)
    try:
        data = VolumeRenderer(reader.get_section("v"), reader).load_data()
        assert not np.iscomplexobj(data)
        np.testing.assert_allclose(data, reduce(field))
        meshes = scenelib.volume_meshes(reader, "v", isovalue=ISO)
        assert meshes, "the reduced field contours"
    finally:
        reader.close()


def test_docs_state_the_label_only_rule():
    doc = (Path(__file__).resolve().parents[1] / "docs" / "qvf.md").read_text()
    assert "never transforms the stored data" in doc


# ── api.get_volume reports the drawn field's range (#61) ───────────────────


@pytest.mark.parametrize("label", _LABELS)
def test_get_volume_range_is_the_stored_real_array(archive, label) -> None:
    """A real array is summarised as stored, whatever its label."""
    from vibeview.api import get_volume

    summary = get_volume(archive(label), "v")
    assert summary is not None
    assert summary["data_min"] == pytest.approx(float(_STORED[label].min()))
    assert summary["data_max"] == pytest.approx(float(_STORED[label].max()))


def test_get_volume_reduces_a_complex_array_by_its_label(archive, monkeypatch) -> None:
    """#61 repro: p_y + 0.5i·p_x labelled ``density`` used to report the real
    part of NumPy's lexicographic complex min (−0.594). The range must be the
    one the viewer draws, |ψ|², which is never negative."""
    from vibeview.api import get_volume

    complex_psi = _PSI + 0.5j * (_X * np.exp(-(_X**2 + _Y**2 + _Z**2) / 2))
    monkeypatch.setattr(QVFReader, "read_volume_data", lambda self, sid: complex_psi)

    summary = get_volume(archive("density"), "v")
    assert summary is not None
    assert summary["data_min"] >= 0.0
    assert summary["data_min"] == pytest.approx(float((np.abs(complex_psi) ** 2).min()))
    assert summary["data_max"] == pytest.approx(float((np.abs(complex_psi) ** 2).max()))
