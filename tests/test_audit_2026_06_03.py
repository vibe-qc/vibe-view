"""Regression tests for the 2026-06-03 correctness audit.

Each test pins one fix from that pass (see ``vibe-view/AUDIT_2026-06-03.md``):

* H1 — stored ``volume.orbital`` / ``basis.ao`` render BOTH lobes (were +only).
* H4 — bands and DOS respect their distinct QVF energy conventions (#25).
* M5 — a degenerate 1×N ``scan.surface`` plots a line instead of crashing.
* M6 — ``scf_history`` keeps the DIIS curve when one record omits ``diis_error``.
* M7 — periodic explicit-bond minimum image is exact in skewed cells.
* L5 — a negative-spacing (flipped-axis) volume grid renders instead of raising.
* L8 — ``dos.projected`` tolerates non-dict channel descriptors.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import types
import zipfile

import numpy as np
import pytest

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _make_qvf_bytes(sections: list[dict], files: dict[str, bytes]) -> bytes:
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0", "calculation": "audit"},
        "sections": sections,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for p, d in files.items():
            zf.writestr(p, d)
    return buf.getvalue()


# ── H1: stored orbital / AO render both lobes ──────────────────────────────


def _signed_field(n: int = 20) -> np.ndarray:
    """A signed scalar field with a clear positive lobe and negative lobe."""
    ii, jj, kk = np.meshgrid(np.arange(n), np.arange(n), np.arange(n), indexing="ij")
    data = np.zeros((n, n, n), dtype=np.float32)
    data += np.exp(-((ii - 13) ** 2 + (jj - 10) ** 2 + (kk - 10) ** 2) / 3.0)  # +lobe
    data -= np.exp(-((ii - 7) ** 2 + (jj - 10) ** 2 + (kk - 10) ** 2) / 3.0)   # -lobe
    return data


def _signed_volume_reader(kind: str, sec_id: str):
    from vibeview.qvf import QVFReader

    n = 20
    data = _signed_field(n)
    grid = json.dumps({
        "origin": [0, 0, 0],
        "voxel_vectors": [[0.4, 0, 0], [0, 0.4, 0], [0, 0, 0.4]],
        "shape": [n, n, n],
    }).encode()
    blob = data.tobytes()
    sec = {
        "id": sec_id, "kind": kind, "component": "real", "members": {
            "grid": {"path": "g.json", "format": "json", "sha256": _sha(grid)},
            "data": {"path": "d.bin", "format": "binary", "dtype": "float32",
                     "shape": [n, n, n], "sha256": _sha(blob)},
        }}
    return QVFReader(_make_qvf_bytes([sec], {"g.json": grid, "d.bin": blob}))


@pytest.mark.parametrize(
    "kind,sec_id",
    [("volume.orbital", "orb"), ("basis.ao", "ao"), ("volume.potential", "esp")],
)
def test_stored_signed_volume_renders_both_lobes(kind: str, sec_id: str) -> None:
    """H1: a stored MO / AO is a signed field — both the + and − lobe must be
    contoured. They used to fall through to the single positive-isovalue path,
    silently dropping the entire negative lobe."""
    import pyvista as pv

    from vibeview.app import _rebuild_volume
    from vibeview.viewer_defaults import ViewerState

    reader = _signed_volume_reader(kind, sec_id)
    plotter = pv.Plotter(off_screen=True)
    vs = ViewerState()
    state = types.SimpleNamespace(
        clip_enabled=False, active_volume_id=sec_id, status_message=""
    )
    _rebuild_volume(reader, plotter, vs, state, sec_id)

    iso = vs.get_volume_hints(sec_id, kind=kind).isovalue
    cached = vs.get_cached_mesh(sec_id, iso)
    assert cached is not None, "volume was not cached"
    assert "neg" in cached and "pos" in cached, (
        f"{kind} took the single-lobe path (no signed pos/neg split)"
    )
    assert cached["pos"] is not None and cached["pos"].n_points > 0, "positive lobe empty"
    assert cached["neg"] is not None and cached["neg"].n_points > 0, (
        "negative lobe dropped (H1 regression)"
    )
    names = set(plotter.actors.keys())
    assert f"volume_{sec_id}_pos" in names and f"volume_{sec_id}_neg" in names


# ── H4: combined bands+DOS share a single Fermi reference ──────────────────


def _bands_dos_reader(b_fermi: float | None, d_fermi: float):
    from vibeview.qvf import QVFReader

    n_k, n_b = 4, 2
    eig = np.array(
        [[[1.0, 2.0], [1.1, 2.1], [1.2, 2.2], [1.3, 2.3]]], dtype=np.float64
    )  # [1, n_k, n_b]
    kpath = json.dumps({
        "n_kpoints": n_k, "n_bands": n_b, "n_spin": 1, "fermi": b_fermi,
        "segments": [{"label_start": "G", "label_end": "X", "n_points": n_k}],
    }).encode()
    npts = 40
    en = np.linspace(-3.0, 3.0, npts).astype(np.float64)  # window includes d_fermi
    dos = np.exp(-en ** 2).astype(np.float64)
    sections = [
        {"id": "bands0", "kind": "bands", "members": {
            "kpath": {"path": "b/k.json", "format": "json", "sha256": _sha(kpath)},
            "eigenvalues": {"path": "b/e.bin", "format": "binary", "dtype": "float64",
                            "shape": [1, n_k, n_b], "sha256": _sha(eig.tobytes())}}},
        {"id": "dos0", "kind": "dos.total", "n_spin": 1,
         "fermi_energy_ev": float(d_fermi), "members": {
            "energies": {"path": "d/e.bin", "format": "binary", "dtype": "float64",
                         "shape": [npts], "sha256": _sha(en.tobytes())},
            "dos": {"path": "d/d.bin", "format": "binary", "dtype": "float64",
                    "shape": [npts], "sha256": _sha(dos.tobytes())}}},
    ]
    files = {"b/k.json": kpath, "b/e.bin": eig.tobytes(),
             "d/e.bin": en.tobytes(), "d/d.bin": dos.tobytes()}
    return QVFReader(_make_qvf_bytes(sections, files)), eig


@pytest.mark.parametrize("b_fermi", [0.0, -10491.3])
@pytest.mark.parametrize("d_fermi", [0.0, 0.5, 5475.1])
def test_combined_bands_dos_respects_each_section_energy_convention(b_fermi, d_fermi):
    """QVF DOS grids already reference E_F; absolute band energies do not (#25)."""
    from vibeview.renderers.bands import BandsRenderer
    from vibeview.renderers.dos import DOSRenderer, _bands_dos_figure

    reader, eig = _bands_dos_reader(b_fermi=b_fermi, d_fermi=d_fermi)
    try:
        fig = _bands_dos_figure(
            BandsRenderer(reader.get_section("bands0"), reader),
            DOSRenderer(reader.get_section("dos0"), reader),
        )
        band = next(t for t in fig.data if (t.name or "").startswith("spin"))
        dos = next(t for t in fig.data if t.name == "DOS")
        np.testing.assert_allclose(band.y, eig[0, :, 0] - b_fermi)
        np.testing.assert_allclose(dos.y, np.linspace(-3.0, 3.0, len(dos.y)))
        assert "E_F" in fig.layout.yaxis.title.text
    finally:
        reader.close()


def test_combined_without_band_fermi_keeps_axes_independent():
    from vibeview.renderers.bands import BandsRenderer
    from vibeview.renderers.dos import DOSRenderer, _bands_dos_figure

    reader, eig = _bands_dos_reader(b_fermi=None, d_fermi=5475.1)
    try:
        fig = _bands_dos_figure(
            BandsRenderer(reader.get_section("bands0"), reader),
            DOSRenderer(reader.get_section("dos0"), reader),
        )
        np.testing.assert_allclose(fig.data[0].y, eig[0, :, 0])
        assert fig.layout.yaxis.title.text == "Energy (eV)"
        assert "E_F" in fig.layout.yaxis2.title.text
        assert fig.layout.yaxis.matches is None
        assert fig.layout.yaxis2.matches is None
        fermi_lines = [s for s in fig.layout.shapes if s.line.color == "#CC3333"]
        assert len(fermi_lines) == 1
        assert fermi_lines[0].yref == "y2"
    finally:
        reader.close()


# ── M5: degenerate scan.surface plots a line, not a crash ──────────────────


def test_scan_surface_1xn_renders_line_not_crash() -> None:
    """M5: a 1×N scan is a valid archive but contourf needs a 2×2 grid; the
    renderer must fall back to a 1-D line plot instead of raising."""
    from vibeview.qvf import QVFReader
    from vibeview.renderers.scan_surface import ScanSurfaceRenderer

    nA, nB = 1, 6
    axis_a = np.array([0.0], dtype=np.float64)
    axis_b = np.linspace(0.0, 1.0, nB).astype(np.float64)
    energies = (np.linspace(0.0, 0.5, nB) ** 2).reshape(nA, nB).astype(np.float64)
    meta = json.dumps({"coordinate_b_label": "r", "coordinate_b_unit": "Å"}).encode()
    sec = {"id": "scan0", "kind": "scan.surface", "members": {
        "metadata": {"path": "s/m.json", "format": "json", "sha256": _sha(meta)},
        "axis_a": {"path": "s/a.bin", "format": "binary", "dtype": "float64",
                   "shape": [nA], "sha256": _sha(axis_a.tobytes())},
        "axis_b": {"path": "s/b.bin", "format": "binary", "dtype": "float64",
                   "shape": [nB], "sha256": _sha(axis_b.tobytes())},
        "energies": {"path": "s/e.bin", "format": "binary", "dtype": "float64",
                     "shape": [nA, nB], "sha256": _sha(energies.tobytes())}}}
    files = {"s/m.json": meta, "s/a.bin": axis_a.tobytes(),
             "s/b.bin": axis_b.tobytes(), "s/e.bin": energies.tobytes()}
    reader = QVFReader(_make_qvf_bytes([sec], files))
    png = ScanSurfaceRenderer(reader.get_section("scan0"), reader).render_surface()
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "1-D scan did not produce a PNG"


# ── M6: scf_history keeps DIIS curve when one record omits diis_error ──────


def test_scf_history_partial_diis_keeps_curve() -> None:
    """M6: a solver that omits diis_error on the first cycle must still yield a
    DIIS curve for the rest — the series used to be discarded all-or-nothing."""
    from vibeview.renderers.scf_history import _extract_xy

    iters = [
        {"iter": 1, "energy_eh": -10.0},                       # no diis_error
        {"iter": 2, "energy_eh": -10.5, "diis_error": 0.1},
        {"iter": 3, "energy_eh": -10.6, "diis_error": 0.01},
    ]
    e_x, e_y = _extract_xy(iters, "energy_eh")
    d_x, d_y = _extract_xy(iters, "diis_error")
    assert e_x == [1.0, 2.0, 3.0] and e_y == [-10.0, -10.5, -10.6]
    # DIIS present and aligned to the iterations that actually carry it.
    assert d_x == [2.0, 3.0] and d_y == [0.1, 0.01]


# ── M7: minimum image is exact in a skewed (hexagonal) cell ────────────────


def test_minimum_image_exact_in_hexagonal_cell() -> None:
    """M7: rounding the fractional offset picks a farther image in skewed
    cells; the true nearest image of frac (0.5, 0.5) in a hexagonal cell is
    0.5 Å away, not 0.866 Å."""
    from vibeview.renderers.structure import _minimum_image

    lattice = np.array(
        [[1.0, 0.0, 0.0], [0.5, np.sqrt(3) / 2, 0.0], [0.0, 0.0, 10.0]]
    )
    inv = np.linalg.inv(lattice)
    p1 = np.zeros(3)
    p2 = 0.5 * lattice[0] + 0.5 * lattice[1]  # frac (0.5, 0.5, 0)
    assert np.linalg.norm(p2 - p1) == pytest.approx(np.sqrt(0.75), abs=1e-9)  # 0.866
    img = _minimum_image(p1, p2, lattice, inv)
    assert np.linalg.norm(img - p1) == pytest.approx(0.5, abs=1e-9)


# ── L5: a negative-spacing (flipped-axis) grid renders ─────────────────────


def test_negative_spacing_grid_matches_positive() -> None:
    """L5: a grid whose voxel vector points opposite its axis (negative
    diagonal) is valid QVF; it must render (not raise ValueError) and produce
    geometry identical to the equivalent positive-spacing grid."""
    from vibeview.qvf import GridData
    from vibeview.renderers.volume import build_isosurface_mesh

    n = 16
    ii, jj, kk = np.meshgrid(np.arange(n), np.arange(n), np.arange(n), indexing="ij")
    data = np.exp(-((ii - 8) ** 2 + (jj - 8) ** 2 + (kk - 8) ** 2) / 4.0).astype(np.float32)

    grid_pos = GridData(
        origin=np.array([0.0, 0.0, 0.0]),
        voxel_vectors=np.diag([0.4, 0.4, 0.4]).astype(float),
        shape=(n, n, n),
    )
    m_pos = build_isosurface_mesh(data, grid_pos, 0.3)

    # z-axis flipped: step downward from the high corner, data reversed to match.
    grid_neg = GridData(
        origin=np.array([0.0, 0.0, (n - 1) * 0.4]),
        voxel_vectors=np.diag([0.4, 0.4, -0.4]).astype(float),
        shape=(n, n, n),
    )
    m_neg = build_isosurface_mesh(np.flip(data, axis=2), grid_neg, 0.3)

    assert m_pos is not None and m_pos.n_points > 0
    assert m_neg is not None and m_neg.n_points > 0, "flipped-axis grid produced nothing"
    assert np.allclose(np.asarray(m_pos.bounds), np.asarray(m_neg.bounds), atol=1e-6)


# ── L8: dos.projected tolerates non-dict channel descriptors ───────────────


def test_projected_dos_string_channels_do_not_crash() -> None:
    """L8: a third-party archive may list channels as plain strings rather
    than ``{"label": ...}`` dicts; the renderer must not raise AttributeError."""
    from vibeview.qvf import QVFReader
    from vibeview.renderers.dos import DOSRenderer

    npts, n_ch = 30, 2
    en = np.linspace(-5.0, 5.0, npts).astype(np.float64)
    proj = np.vstack([np.exp(-(en + 2) ** 2), np.exp(-(en - 2) ** 2)]).astype(np.float64)
    sec = {
        "id": "pdos0", "kind": "dos.projected", "n_spin": 1,
        "channels": ["s", "p"],  # strings, not dicts
        "members": {
            "energies": {"path": "p/e.bin", "format": "binary", "dtype": "float64",
                         "shape": [npts], "sha256": _sha(en.tobytes())},
            "projections": {"path": "p/p.bin", "format": "binary", "dtype": "float64",
                            "shape": [n_ch, npts], "sha256": _sha(proj.tobytes())},
        }}
    reader = QVFReader(_make_qvf_bytes([sec], {"p/e.bin": en.tobytes(), "p/p.bin": proj.tobytes()}))
    html = DOSRenderer(reader.get_section("pdos0"), reader).render_to_html()
    assert html and "Plotly" in html


# ── L4: vibration mode labels carry the IR intensity they promise ──────────


def test_vibration_labels_include_ir_intensity() -> None:
    """L4: the mode-selector label is documented to include the IR intensity
    when a companion spectra.ir section is present — both branches used to
    build the identical intensity-free label, so the feature was dead."""
    from vibeview.app import _activate_vibrations
    from vibeview.qvf import QVFReader

    freqs = [1600.0, 3700.0, 3800.0]
    n_modes, n_atoms = 3, 2
    disp = np.zeros((n_modes, n_atoms, 3), dtype=np.float64)
    disp[0, 0, 2] = 0.1
    meta = json.dumps({
        "frequencies": freqs,
        "atoms": [
            {"symbol": "O", "position": [0.0, 0.0, 0.0], "atomic_number": 8},
            {"symbol": "H", "position": [0.0, 0.0, 0.97], "atomic_number": 1},
        ],
    }).encode()
    spectrum = json.dumps(
        {"frequencies": freqs, "intensities": [12.0, 55.0, 88.0]}
    ).encode()
    sections = [
        {"id": "vib0", "kind": "vibrations", "members": {
            "metadata": {"path": "v/m.json", "format": "json", "sha256": _sha(meta)},
            "displacements": {"path": "v/d.bin", "format": "binary", "dtype": "float64",
                              "shape": [n_modes, n_atoms, 3],
                              "sha256": _sha(disp.tobytes())}}},
        {"id": "ir0", "kind": "spectra.ir", "members": {
            "spectrum": {"path": "s/ir.json", "format": "json", "sha256": _sha(spectrum)}}},
    ]
    files = {"v/m.json": meta, "v/d.bin": disp.tobytes(), "s/ir.json": spectrum}
    reader = QVFReader(_make_qvf_bytes(sections, files))
    state = types.SimpleNamespace()
    _activate_vibrations(reader, state, reader.get_section("vib0"))

    titles = [it["title"] for it in state.vibration_mode_items]
    assert "IR 12.0 km/mol" in titles[0], titles
    assert state.vibration_ir_intensities == [12.0, 55.0, 88.0]


def test_reapply_raytrace_never_lies() -> None:
    """L2 — a scene rebuild drops the OSPRay pass; ``_reapply_raytrace`` must
    keep ``raytrace_enabled`` consistent with the actual pass. When raytrace is
    off it is a strict no-op; when on but the pass can't be (re-)attached, the
    flag is cleared so the indicator never claims a pass that isn't there.
    """
    from vibeview.app import _reapply_raytrace

    class _Plotter:
        renderer = object()

    # Off -> untouched.
    s = types.SimpleNamespace(raytrace_enabled=False)
    _reapply_raytrace(_Plotter(), s)
    assert s.raytrace_enabled is False

    # On, but re-attach fails (no OSPRay/vtk in the test env) -> flag cleared.
    s = types.SimpleNamespace(raytrace_enabled=True)
    _reapply_raytrace(_Plotter(), s)
    assert s.raytrace_enabled is False


def test_apply_camera_pushes_client_camera() -> None:
    """L1 — applying a stored camera (bookmark/slide/session/load) must push the
    server camera to the client's VtkLocalView, which keeps its own camera and
    would otherwise ignore the change. `_apply_camera` calls the `push_camera`
    callable stashed on the plotter; absent (headless), it is a silent no-op.
    """
    from vibeview.app import _apply_camera

    class _Cam:
        view_angle = 30.0
        parallel_projection = False
        parallel_scale = 1.0

    class _Plotter:
        camera = _Cam()
        camera_position = None

    cam_dict = {
        "position": [1.0, 2.0, 3.0],
        "focal_point": [0.0, 0.0, 0.0],
        "view_up": [0.0, 0.0, 1.0],
        "view_angle": 25.0,
    }

    # No stashed pusher (headless) -> no crash, no push.
    p = _Plotter()
    _apply_camera(p, cam_dict)
    assert p.camera_position is not None  # camera still applied

    # With a stashed pusher -> it is invoked so the client view follows.
    p = _Plotter()
    calls = []
    p._vibe_view_push_camera = lambda: calls.append(1)
    _apply_camera(p, cam_dict)
    assert calls == [1], "applying a camera must push it to the client (L1)"

    # Empty camera -> early return, no push.
    p = _Plotter()
    calls = []
    p._vibe_view_push_camera = lambda: calls.append(1)
    _apply_camera(p, None)
    assert calls == []
