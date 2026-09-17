"""Energy-window control for the bands / DOS charts (#26).

An all-electron periodic archive carries core eigenvalues hundreds or
thousands of eV below E_F. Autoscaled, the energy axis then spans the
whole spectrum and the valence and conduction states — the part anyone
actually reads — collapse into a few pixels around the E_F line.

The numbers in ``_silicon_bands`` and ``_silicon_pdos`` are the ones the
issue reports for silicon at STO-3G: Si 1s at -2431 eV, 2s/2p near
-400 eV, eight valence and conduction bands over -47 to +59 eV, and a
projected-DOS grid running down to -1837 eV.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import types
import zipfile
from pathlib import Path

import numpy as np
import pytest

from vibeview.qvf import QVFReader
from vibeview.renderers.energy_window import (
    auto_energy_window,
    dos_support,
    parse_energy_window,
)

# ── Archive builders ──────────────────────────────────────────────────


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _make_qvf(sections: list[dict], files: dict[str, bytes], **manifest_extra) -> Path:
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0", "calculation": "t"},
        "sections": sections,
        **manifest_extra,
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for p, d in files.items():
            zf.writestr(p, d)
    return Path(tmp.name)


def _silicon_bands(n_k: int = 24) -> np.ndarray:
    """Eigenvalues [1, n_k, 18] with two core shells below the valence bands."""
    rng = np.random.default_rng(0)
    core_1s = np.full((n_k, 2), -2431.0) + rng.normal(0, 0.05, (n_k, 2))
    core_2sp = np.full((n_k, 8), -400.0) + rng.normal(0, 2.0, (n_k, 8))
    valence = np.linspace(-47.0, 59.0, 8)[None, :] + rng.normal(0, 3.0, (n_k, 8))
    return np.concatenate([core_1s, core_2sp, valence], axis=1)[None].astype(np.float64)


def _bands_section(eig: np.ndarray, fermi: float | None) -> tuple[dict, dict[str, bytes]]:
    kpath: dict = {
        "n_kpoints": eig.shape[1],
        "n_bands": eig.shape[2],
        "n_spin": eig.shape[0],
        "segments": [],
    }
    if fermi is not None:
        kpath["fermi"] = fermi
    blob = json.dumps(kpath).encode()
    section = {
        "id": "bands0",
        "kind": "bands",
        "members": {
            "kpath": {"path": "b/k.json", "format": "json", "sha256": _sha(blob)},
            "eigenvalues": {
                "path": "b/e.bin",
                "format": "binary",
                "dtype": "float64",
                "shape": list(eig.shape),
                "sha256": _sha(eig.tobytes()),
            },
        },
    }
    return section, {"b/k.json": blob, "b/e.bin": eig.tobytes()}


def _silicon_pdos() -> tuple[np.ndarray, np.ndarray]:
    """A DOS grid spanning -1837 .. +38.5 eV with core peaks far from E_F."""
    e = np.linspace(-1837.0, 38.5, 3000)
    dos = np.zeros_like(e)
    for centre, width in ((-1837.0, 1.0), (-400.0, 4.0)):
        dos += np.exp(-((e - centre) ** 2) / (2 * width**2))
    dos += 3.0 * np.exp(-((e + 20.0) ** 2) / (2 * 12.0**2))
    dos += 2.0 * np.exp(-((e - 25.0) ** 2) / (2 * 8.0**2))
    return e, dos


def _dos_section(e: np.ndarray, dos: np.ndarray) -> tuple[dict, dict[str, bytes]]:
    e = e.astype(np.float64)
    dos = dos.astype(np.float64)
    section = {
        "id": "dos_total",
        "kind": "dos.total",
        "n_spin": 1,
        "members": {
            "energies": {
                "path": "d/e.bin",
                "format": "binary",
                "dtype": "float64",
                "shape": [len(e)],
                "sha256": _sha(e.tobytes()),
            },
            "dos": {
                "path": "d/d.bin",
                "format": "binary",
                "dtype": "float64",
                "shape": [len(dos)],
                "sha256": _sha(dos.tobytes()),
            },
        },
    }
    return section, {"d/e.bin": e.tobytes(), "d/d.bin": dos.tobytes()}


def _ranges(html: str) -> list[list[float]]:
    """Every explicit axis range Plotly serialized into the figure."""
    return [json.loads(m) for m in re.findall(r'"range":(\[[^\]]*\])', html)]


# ── The heuristic ─────────────────────────────────────────────────────


def test_core_states_are_left_out_of_the_default_window():
    eig = _silicon_bands()
    window = auto_energy_window(eig)
    assert window is not None
    lo, hi = window
    # The valence and conduction manifold is framed whole …
    assert lo < -47.0 and hi > 59.0
    # … and neither core shell is anywhere near the axis.
    assert lo > -100.0


def test_a_spectrum_without_core_states_keeps_its_autoscale():
    """A pseudopotential-style file must render exactly as it does today."""
    assert auto_energy_window(np.linspace(-15.0, 10.0, 200)) is None


def test_no_window_when_nothing_sits_near_the_fermi_level():
    """Core-only data gets no invented window."""
    assert auto_energy_window(np.linspace(-2431.0, -2430.0, 50)) is None


def test_a_real_band_gap_never_splits_the_window():
    """A 14 eV gap (LiF is the widest in nature) is not a core separation."""
    states = np.concatenate([np.linspace(-20.0, -7.0, 100), np.linspace(7.0, 20.0, 100)])
    window = auto_energy_window(states, full_range=(-2000.0, 20.0))
    assert window is not None
    assert window[0] < -20.0 and window[1] > 20.0


def test_dos_window_is_measured_against_the_whole_grid():
    e, dos = _silicon_pdos()
    window = auto_energy_window(dos_support(e, dos), full_range=(e.min(), e.max()))
    assert window is not None
    lo, hi = window
    assert lo > -200.0  # the -1837 and -400 peaks are excluded
    assert hi > 25.0  # the conduction feature is kept


def test_dos_support_ignores_the_empty_part_of_the_grid():
    e = np.linspace(-100.0, 100.0, 201)
    dos = np.zeros_like(e)
    dos[(e >= -10.0) & (e <= 10.0)] = 1.0
    support = dos_support(e, dos)
    assert support.min() >= -10.0 and support.max() <= 10.0


def test_dos_support_counts_a_spin_down_channel():
    """Spin channels are stored positive and mirrored only at draw time."""
    e = np.linspace(-50.0, 50.0, 101)
    up = np.where(e < 0, 1.0, 0.0)
    down = np.where(e > 0, 1.0, 0.0)
    support = dos_support(e, np.stack([up, down]))
    assert support.min() < 0.0 and support.max() > 0.0


@pytest.mark.parametrize(
    "value,expected",
    [
        ([-5.0, 5.0], (-5.0, 5.0)),
        ((-5, 5), (-5.0, 5.0)),
        ({"min": -5.0, "max": 5.0}, (-5.0, 5.0)),
        ({"emin": -5.0, "emax": 5.0}, (-5.0, 5.0)),
    ],
)
def test_parse_energy_window_accepts_the_documented_forms(value, expected):
    assert parse_energy_window(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "auto",
        [],
        [1.0],
        [1.0, 2.0, 3.0],
        [5.0, -5.0],  # inverted
        [5.0, 5.0],  # empty
        [float("nan"), 5.0],
        [float("-inf"), float("inf")],
        ["-5", "5"],  # a hint must carry numbers, not strings
        [True, False],
        {"min": -5.0},
    ],
)
def test_a_malformed_window_hint_cannot_blank_the_chart(value):
    assert parse_energy_window(value) is None


# ── Renderers ─────────────────────────────────────────────────────────


def test_bands_chart_opens_on_the_valence_window():
    section, files = _bands_section(_silicon_bands(), fermi=0.0001)
    path = _make_qvf([section], files)
    try:
        from vibeview.renderers.bands import BandsRenderer

        renderer = BandsRenderer(QVFReader(path).get_section("bands0"), QVFReader(path))
        html = renderer.render_to_html(include_plotlyjs=False)
        assert _ranges(html) == [list(renderer.default_energy_window())]
    finally:
        path.unlink()


def test_bands_chart_without_a_fermi_reference_is_not_windowed():
    """The window is defined relative to E_F, so no E_F means no window."""
    section, files = _bands_section(_silicon_bands(), fermi=None)
    path = _make_qvf([section], files)
    try:
        from vibeview.renderers.bands import BandsRenderer

        reader = QVFReader(path)
        renderer = BandsRenderer(reader.get_section("bands0"), reader)
        assert renderer.default_energy_window() is None
        assert _ranges(renderer.render_to_html(include_plotlyjs=False)) == []
    finally:
        path.unlink()


def test_explicit_window_overrides_the_default():
    section, files = _bands_section(_silicon_bands(), fermi=0.0001)
    path = _make_qvf([section], files)
    try:
        from vibeview.renderers.bands import BandsRenderer

        reader = QVFReader(path)
        renderer = BandsRenderer(reader.get_section("bands0"), reader)
        html = renderer.render_to_html(include_plotlyjs=False, energy_window=(-6.0, 4.0))
        assert _ranges(html) == [[-6.0, 4.0]]
    finally:
        path.unlink()


def test_auto_window_false_restores_the_full_autoscale():
    section, files = _bands_section(_silicon_bands(), fermi=0.0001)
    path = _make_qvf([section], files)
    try:
        from vibeview.renderers.bands import BandsRenderer

        reader = QVFReader(path)
        renderer = BandsRenderer(reader.get_section("bands0"), reader)
        assert _ranges(renderer.render_to_html(include_plotlyjs=False, auto_window=False)) == []
    finally:
        path.unlink()


def test_bands_png_is_drawn_inside_the_window():
    """The matplotlib path takes the same window as the Plotly one."""
    section, files = _bands_section(_silicon_bands(), fermi=0.0001)
    path = _make_qvf([section], files)
    try:
        from vibeview.renderers.bands import BandsRenderer

        reader = QVFReader(path)
        renderer = BandsRenderer(reader.get_section("bands0"), reader)
        windowed = renderer.render_to_bytes()
        full = renderer.render_to_bytes(auto_window=False)
        assert windowed and full and windowed != full
    finally:
        path.unlink()


def test_dos_chart_windows_its_energy_axis():
    e, dos = _silicon_pdos()
    section, files = _dos_section(e, dos)
    path = _make_qvf([section], files)
    try:
        from vibeview.renderers.dos import DOSRenderer

        reader = QVFReader(path)
        renderer = DOSRenderer(reader.get_section("dos_total"), reader)
        window = renderer.default_energy_window()
        assert window is not None and window[0] > -200.0
        assert _ranges(renderer.render_to_html(include_plotlyjs=False)) == [list(window)]
    finally:
        path.unlink()


def test_combined_panel_windows_both_energy_axes():
    """Bands and DOS share one energy axis, so one window has to frame both."""
    eig = _silicon_bands()
    b_section, b_files = _bands_section(eig, fermi=0.0001)
    e, dos = _silicon_pdos()
    d_section, d_files = _dos_section(e, dos)
    path = _make_qvf([b_section, d_section], {**b_files, **d_files})
    try:
        from vibeview.renderers.bands import BandsRenderer
        from vibeview.renderers.dos import (
            DOSRenderer,
            bands_dos_energy_window,
            render_bands_dos_combined,
        )

        reader = QVFReader(path)
        bands = BandsRenderer(reader.get_section("bands0"), reader)
        dos_r = DOSRenderer(reader.get_section("dos_total"), reader)
        window = bands_dos_energy_window(bands, dos_r)
        assert window is not None
        # Wide enough for the bands, but nowhere near the core states.
        assert window[0] < -47.0 and window[1] > 59.0 and window[0] > -200.0
        html = render_bands_dos_combined(bands, dos_r, include_plotlyjs=False)
        assert _ranges(html) == [list(window), list(window)]
    finally:
        path.unlink()


def test_combined_panel_is_not_windowed_without_a_fermi_reference():
    b_section, b_files = _bands_section(_silicon_bands(), fermi=None)
    e, dos = _silicon_pdos()
    d_section, d_files = _dos_section(e, dos)
    path = _make_qvf([b_section, d_section], {**b_files, **d_files})
    try:
        from vibeview.renderers.bands import BandsRenderer
        from vibeview.renderers.dos import (
            DOSRenderer,
            bands_dos_energy_window,
            render_bands_dos_combined,
        )

        reader = QVFReader(path)
        bands = BandsRenderer(reader.get_section("bands0"), reader)
        dos_r = DOSRenderer(reader.get_section("dos_total"), reader)
        assert bands_dos_energy_window(bands, dos_r) is None
        assert _ranges(render_bands_dos_combined(bands, dos_r, include_plotlyjs=False)) == []
    finally:
        path.unlink()


# ── viewer_defaults hint ──────────────────────────────────────────────


def test_viewer_defaults_energy_window_hint_is_parsed():
    from vibeview.viewer_defaults import ViewerState

    section, files = _bands_section(_silicon_bands(), fermi=0.0001)
    path = _make_qvf(
        [section],
        files,
        viewer_defaults={"bands0": {"energy_window": [-12.0, 8.0]}},
    )
    try:
        state = ViewerState.from_manifest(QVFReader(path).viewer_defaults)
        assert state.get_energy_window("bands0") == (-12.0, 8.0)
        assert state.get_energy_window("missing") is None
        assert state.get_energy_window("missing", "bands0") == (-12.0, 8.0)
    finally:
        path.unlink()


def test_a_malformed_viewer_defaults_hint_is_dropped():
    from vibeview.viewer_defaults import ViewerState

    section, files = _bands_section(_silicon_bands(), fermi=0.0001)
    path = _make_qvf(
        [section],
        files,
        viewer_defaults={"bands0": {"energy_window": [8.0, -12.0]}},
    )
    try:
        state = ViewerState.from_manifest(QVFReader(path).viewer_defaults)
        assert state.get_energy_window("bands0") is None
    finally:
        path.unlink()


def test_an_energy_window_hint_does_not_disturb_the_volume_hints():
    """energy_window rides on the same per-section hint object as isovalue."""
    from vibeview.viewer_defaults import ViewerState

    section, files = _bands_section(_silicon_bands(), fermi=0.0001)
    path = _make_qvf(
        [section],
        files,
        viewer_defaults={"dens": {"energy_window": [-1.0, 1.0], "isovalue": 0.02}},
    )
    try:
        state = ViewerState.from_manifest(QVFReader(path).viewer_defaults)
        assert state.get_energy_window("dens") == (-1.0, 1.0)
        assert state.get_volume_hints("dens", kind="volume.density").isovalue == 0.02
    finally:
        path.unlink()


# ── App wiring ────────────────────────────────────────────────────────


def _panel_state(**over) -> types.SimpleNamespace:
    base = {
        "bands_html": None,
        "bands_title": "Band Structure",
        "bands_window_available": False,
        "bands_window_auto": True,
        "bands_emin": None,
        "bands_emax": None,
        "status_message": "",
    }
    base.update(over)
    return types.SimpleNamespace(**base)


def test_activating_bands_reports_the_window_it_drew():
    """The fields must show the window in view, not sit blank."""
    from vibeview.app import _activate_bands

    section, files = _bands_section(_silicon_bands(), fermi=0.0001)
    path = _make_qvf([section], files)
    try:
        reader = QVFReader(path)
        state = _panel_state()
        _activate_bands(reader, state, reader.get_section("bands0"))
        assert state.bands_window_available is True
        assert state.bands_emin is not None and state.bands_emax is not None
        assert state.bands_emin < -47.0 < 59.0 < state.bands_emax
        assert _ranges(state.bands_html) == [[state.bands_emin, state.bands_emax]]
    finally:
        path.unlink()


def test_a_manifest_hint_reaches_the_chart():
    from vibeview.app import _activate_bands
    from vibeview.viewer_defaults import ViewerState

    section, files = _bands_section(_silicon_bands(), fermi=0.0001)
    path = _make_qvf(
        [section], files, viewer_defaults={"bands0": {"energy_window": [-12.0, 8.0]}}
    )
    try:
        reader = QVFReader(path)
        state = _panel_state()
        viewer_state = ViewerState.from_manifest(reader.viewer_defaults)
        _activate_bands(reader, state, reader.get_section("bands0"), viewer_state)
        assert _ranges(state.bands_html) == [[-12.0, 8.0]]
        assert (state.bands_emin, state.bands_emax) == (-12.0, 8.0)
    finally:
        path.unlink()


def test_a_reader_set_window_beats_the_manifest_hint():
    from vibeview.app import _activate_bands
    from vibeview.viewer_defaults import ViewerState

    section, files = _bands_section(_silicon_bands(), fermi=0.0001)
    path = _make_qvf(
        [section], files, viewer_defaults={"bands0": {"energy_window": [-12.0, 8.0]}}
    )
    try:
        reader = QVFReader(path)
        viewer_state = ViewerState.from_manifest(reader.viewer_defaults)
        state = _panel_state(bands_window_auto=False, bands_emin=-3.0, bands_emax=2.0)
        _activate_bands(reader, state, reader.get_section("bands0"), viewer_state)
        assert _ranges(state.bands_html) == [[-3.0, 2.0]]
    finally:
        path.unlink()


def test_an_unusable_reader_window_falls_back_rather_than_blanking():
    """A half-typed or inverted pair must not produce an empty chart."""
    from vibeview.app import _activate_bands

    section, files = _bands_section(_silicon_bands(), fermi=0.0001)
    path = _make_qvf([section], files)
    try:
        reader = QVFReader(path)
        state = _panel_state(bands_window_auto=False, bands_emin=5.0, bands_emax=None)
        _activate_bands(reader, state, reader.get_section("bands0"), None)
        drawn = _ranges(state.bands_html)
        assert drawn and drawn[0][0] < -47.0  # the valence default, not [5, None]
    finally:
        path.unlink()


def test_clearing_the_panel_drops_a_window_typed_for_another_section():
    from vibeview.app import _clear_output_panels

    state = _panel_state(
        bands_window_available=True,
        bands_window_auto=False,
        bands_emin=-3.0,
        bands_emax=2.0,
    )
    # _clear_output_panels only assigns, so a namespace takes every slot.
    _clear_output_panels(state)
    assert state.bands_window_available is False
    assert state.bands_window_auto is True
    assert state.bands_emin is None and state.bands_emax is None


@pytest.mark.parametrize(
    "value,expected",
    [("-12.5", -12.5), (-12.5, -12.5), ("", None), (None, None), ("abc", None), ("nan", None)],
)
def test_window_fields_coerce_their_string_values(value, expected):
    """A type="number" VTextField still v-models as a string."""
    from vibeview.app import _coerce_window_bound

    assert _coerce_window_bound(value) == expected


def test_dos_only_archive_is_windowed_too():
    from vibeview.app import _activate_dos

    e, dos = _silicon_pdos()
    section, files = _dos_section(e, dos)
    path = _make_qvf([section], files)
    try:
        reader = QVFReader(path)
        state = _panel_state()
        _activate_dos(reader, state, reader.get_section("dos_total"))
        assert state.bands_title == "Density of States"
        assert state.bands_window_available is True
        assert state.bands_emin is not None and state.bands_emin > -200.0
    finally:
        path.unlink()


def test_controller_round_trip_sets_and_resets_the_window():
    """Drive the real controllers the panel's fields and button are bound to."""
    from trame.app import get_server

    from vibeview.app import create_app

    section, files = _bands_section(_silicon_bands(), fermi=0.0001)
    path = _make_qvf([section], files)
    reader = QVFReader(path)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        state, ctrl = server.state, server.controller
        ctrl.activate_section("bands0")
        default = (state.bands_emin, state.bands_emax)
        assert default[0] is not None and state.bands_window_auto is True

        # The fields v-model onto state; the handler reads them from there.
        state.bands_emin, state.bands_emax = "-8", "6"
        ctrl.update_bands_energy_window()
        assert (state.bands_emin, state.bands_emax) == (-8.0, 6.0)
        assert state.bands_window_auto is False
        assert _ranges(state.bands_html) == [[-8.0, 6.0]]

        ctrl.reset_bands_energy_window()
        assert state.bands_window_auto is True
        assert (state.bands_emin, state.bands_emax) == default
        assert _ranges(state.bands_html) == [list(default)]
    finally:
        reader.close()
        path.unlink()


def test_clearing_a_field_returns_the_chart_to_its_default():
    """A blank bound is not a window; the chart must not go empty."""
    from trame.app import get_server

    from vibeview.app import create_app

    section, files = _bands_section(_silicon_bands(), fermi=0.0001)
    path = _make_qvf([section], files)
    reader = QVFReader(path)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        state, ctrl = server.state, server.controller
        ctrl.activate_section("bands0")
        default = (state.bands_emin, state.bands_emax)

        state.bands_emin, state.bands_emax = "-8", ""
        ctrl.update_bands_energy_window()
        assert state.bands_window_auto is True
        assert (state.bands_emin, state.bands_emax) == default
        assert _ranges(state.bands_html) == [list(default)]
    finally:
        reader.close()
        path.unlink()


def test_an_explicit_window_skips_the_unreferenced_bands_axis():
    """Without a Fermi energy the bands axis is absolute eV, not E − E_F.

    The two panels then carry different references by design, so a window
    given in E − E_F belongs only to the DOS axis.
    """
    b_section, b_files = _bands_section(_silicon_bands(), fermi=None)
    e, dos = _silicon_pdos()
    d_section, d_files = _dos_section(e, dos)
    path = _make_qvf([b_section, d_section], {**b_files, **d_files})
    try:
        from vibeview.renderers.bands import BandsRenderer
        from vibeview.renderers.dos import DOSRenderer, render_bands_dos_combined

        reader = QVFReader(path)
        html = render_bands_dos_combined(
            BandsRenderer(reader.get_section("bands0"), reader),
            DOSRenderer(reader.get_section("dos_total"), reader),
            include_plotlyjs=False,
            energy_window=(-10.0, 10.0),
        )
        assert _ranges(html) == [[-10.0, 10.0]]
    finally:
        path.unlink()


def test_capture_bands_takes_a_window(tmp_path):
    """Headless figures get the same framing, and can override it (#26)."""
    from vibeview.capture import capture_bands

    section, files = _bands_section(_silicon_bands(), fermi=0.0001)
    path = _make_qvf([section], files)
    try:
        reader = QVFReader(path)
        windowed = tmp_path / "windowed.png"
        full = tmp_path / "full.png"
        assert capture_bands(reader, windowed)
        assert capture_bands(reader, full, auto_window=False)
        assert windowed.read_bytes() != full.read_bytes()
    finally:
        path.unlink()


def test_capture_dos_takes_a_window(tmp_path):
    from vibeview.capture import capture_dos

    e, dos = _silicon_pdos()
    section, files = _dos_section(e, dos)
    path = _make_qvf([section], files)
    try:
        reader = QVFReader(path)
        out = tmp_path / "dos.html"
        assert capture_dos(reader, out, energy_window=(-20.0, 20.0))
        assert _ranges(out.read_text()) == [[-20.0, 20.0]]
    finally:
        path.unlink()


def test_a_window_narrower_than_the_rounding_is_dropped():
    """States packed into a fraction of a millielectronvolt are not an axis."""
    states = np.linspace(-0.0002, 0.0002, 50)
    assert auto_energy_window(states, full_range=(-100.0, 100.0)) is None
