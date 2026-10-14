"""Terminal-viewer correctness contracts (vibe-view#49, follow-up to audit #38).

Each test names the #38 finding id it pins.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import build_qvf
from vibeview.qvf import QVFReader
from vibeview.tui import plots
from vibeview.tui.plots import Plot

_FERMI_RGB = (240, 120, 120)


def _lines(grid) -> list[str]:
    return grid.to_plain().splitlines()


def _sine_plot(**kwargs) -> Plot:
    plot = Plot(40, 10, **kwargs)
    plot.add(np.linspace(0, 10, 50), np.sin(np.linspace(0, 6, 50)))
    return plot


# ── #38-1: tick labels, captions and the hline legend ────────────────


def test_x_tick_labels_are_drawn_without_a_caption():
    """#38-1: with no xlabel/legend the tick-label row was off the grid."""
    lines = _lines(_sine_plot().render())
    tick_row = next(i for i, line in enumerate(lines) if "└" in line) + 1
    assert tick_row < len(lines)
    row = lines[tick_row]
    assert row.split()[0] == "0" and "5.00" in row and "10.0" in row


def test_x_caption_does_not_overwrite_the_tick_labels():
    """#38-1: the caption was written on the tick row ('0  5.x0  10.0')."""
    lines = _lines(_sine_plot(xlabel="x").render())
    tick_row = next(i for i, line in enumerate(lines) if "└" in line) + 1
    assert "5.00" in lines[tick_row]
    assert lines[tick_row + 1].strip() == "x"


def test_categorical_ticks_and_hline_legend_are_shown():
    """#38-1: Γ/X/M labels were absent and a labelled hline got no legend row."""
    plot = Plot(60, 12, ylabel="E − E_F (eV)")
    kx = np.arange(30, dtype=float)
    plot.add(kx, np.sin(kx / 5))
    plot.xticks = [(0.0, "Γ"), (10.0, "X"), (20.0, "M"), (29.0, "Γ")]
    plot.hlines.append((0.0, _FERMI_RGB, "E_F"))
    text = plot.render().to_plain()
    for label in ("Γ", "X", "M"):
        assert label in text
    assert "-- E_F" in text


# ── #38-2: the y caption is not cut to the margin width ──────────────


def test_y_caption_is_not_truncated_to_the_margin():
    plot = Plot(60, 12, ylabel="E − E_F (eV)", title="Band structure")
    plot.add([0, 1, 2], [0, 1, 0])
    row0 = _lines(plot.render())[0]
    assert row0.startswith("E − E_F (eV)")
    assert "Band structure" in row0


# ── #38-10: charts honour the pixel mode ─────────────────────────────


def test_plot_render_takes_the_pixel_mode():
    """#38-10: ``Plot.render(mode="half")`` raised TypeError."""
    grid = _sine_plot().render(mode="half")
    assert "▀" in grid.to_plain()
    assert "▀" not in _sine_plot().render().to_plain()


def test_show_passes_the_pixel_mode_to_charts(tmp_path):
    from vibeview.tui.show import render_section

    path = _bands_archive(tmp_path, -0.5)
    with QVFReader(path) as reader:
        half = render_section(reader, "b", 60, 16, mode="half").to_plain()
        braille = render_section(reader, "b", 60, 16, mode="braille").to_plain()
    assert "▀" in half and "▀" not in braille


# ── #38-13: near-zero ticks print 0 ──────────────────────────────────


def test_middle_y_tick_of_a_symmetric_axis_is_zero():
    """#38-13: the sine plot's middle y tick read ``7.7e-04``."""
    plot = Plot(40, 13)  # ten data rows -> five ticks, one in the middle
    plot.add(np.linspace(0, 10, 50), np.sin(np.linspace(0, 6, 50)))
    ticks = [line.split("│")[0].strip() for line in _lines(plot.render()) if "│" in line]
    ticks = [t for t in ticks if t]
    assert "0" in ticks
    assert not any("e-0" in t for t in ticks), ticks


# ── #38-3: Fermi convention matches the browser ──────────────────────


def _bands_archive(tmp_path: Path, fermi: float | None) -> Path:
    k = np.arange(10, dtype=float)
    eigs = np.stack([-2.0 + 0.1 * k, 1.0 + 0.1 * k], axis=1)[None]
    kpath = {
        "segments": [
            {"label_start": "Γ", "label_end": "X", "n_points": 5},
            {"label_start": "X", "label_end": "M", "n_points": 5},
        ],
        "n_kpoints": 10,
        "n_bands": 2,
    }
    if fermi is not None:
        kpath["fermi"] = fermi
    name = f"bands_{'none' if fermi is None else fermi}.qvf"
    return build_qvf(
        tmp_path / name,
        [
            (
                "b",
                "bands",
                {
                    "kpath": ("json", "kpath.json", json.dumps(kpath).encode()),
                    "eigenvalues": (
                        "binary", "eig.bin", eigs.astype("<f8").tobytes(), "float64",
                        list(eigs.shape),
                    ),
                },
            )
        ],
    )


@pytest.mark.parametrize("fermi", [0.0, 5.0, -0.5, None])
def test_bands_fermi_reference_matches_the_browser(tmp_path, fermi):
    """#38-3: fermi 0.0 is the no-Fermi sentinel; any other value references
    the axis to E_F, in range or not (BandsRenderer._fermi)."""
    from vibeview.renderers.bands import BandsRenderer

    path = _bands_archive(tmp_path, fermi)
    with QVFReader(path) as reader:
        plot = plots.bands_plot(reader, "b", 80, 24)
        browser_fermi = BandsRenderer(reader.get_section("b"), reader)._fermi()
    referenced = plot.ylabel == "E − E_F (eV)"
    assert referenced == (browser_fermi is not None)
    assert bool(plot.hlines) == (browser_fermi is not None)
    if browser_fermi is not None:
        assert plot.series[0].y[0] == pytest.approx(-2.0 - browser_fermi)
    else:
        assert plot.series[0].y[0] == pytest.approx(-2.0)


# ── #38-17 / #38-18: one contour path, one signed-kind list ──────────

_VOL_SHAPE = (5, 5, 5)


def _volume_archive(tmp_path: Path, kind: str, data: np.ndarray) -> tuple[Path, str]:
    grid_json = json.dumps(
        {"origin": [0, 0, 0], "voxel_vectors": np.eye(3).tolist(), "shape": list(_VOL_SHAPE)}
    ).encode()
    sid = kind.split(".", 1)[-1]
    data = np.asarray(data, dtype="<f8")
    path = build_qvf(
        tmp_path / f"{sid}.qvf",
        [
            (
                sid,
                kind,
                {
                    "grid": ("json", "grid.json", grid_json),
                    "data": ("binary", "data.bin", data.tobytes(), "float64", list(_VOL_SHAPE)),
                },
            )
        ],
    )
    return path, sid


@pytest.fixture
def contour_levels(monkeypatch):
    import vibeview.renderers.volume as rv

    levels: list[float] = []
    original = rv.build_isosurface_mesh

    def counting(data, grid, level, **kwargs):
        levels.append(float(level))
        return original(data, grid, level, **kwargs)

    monkeypatch.setattr(rv, "build_isosurface_mesh", counting)
    return levels


def test_volume_with_a_nan_below_the_isovalue_draws_nothing(tmp_path, contour_levels):
    """#38-17: ``data.max() < level`` is False with a NaN present, so the
    stored-volume path contoured a level no finite value reaches."""
    from vibeview.tui import scene as scenelib

    data = np.full(_VOL_SHAPE, 0.01)
    data[2, 2, 2] = np.nan
    path, sid = _volume_archive(tmp_path, "volume.generic", data)
    with QVFReader(path) as reader:
        meshes = scenelib.volume_meshes(reader, sid, isovalue=0.05)
    assert meshes == []
    assert contour_levels == []


@pytest.mark.parametrize(
    ("kind", "low", "high", "expected_levels"),
    [
        # all-positive orbital: the browser requests both lobes (by kind)
        ("volume.orbital", 0.0, 0.5, [0.05, -0.05]),
        # potential is signed in the browser's list
        ("volume.potential", -0.5, 0.5, [0.05, -0.05]),
        # ELF is one surface even with negative float noise
        ("volume.elf", -1e-6, 0.9, [0.05]),
        # density is never signed
        ("volume.density", -0.1, 0.5, [0.05]),
    ],
)
def test_signed_volume_kinds_match_the_browser(
    tmp_path, contour_levels, kind, low, high, expected_levels
):
    """#38-18: the terminal decided "signed" from any negative value; the
    browser by kind (difference, spin, orbital, potential, basis.ao)."""
    from vibeview.tui import scene as scenelib

    data = np.random.default_rng(3).uniform(low, high, _VOL_SHAPE)
    data[0, 0, 0] = low
    data[4, 4, 4] = high
    path, sid = _volume_archive(tmp_path, kind, data)
    with QVFReader(path) as reader:
        meshes = scenelib.volume_meshes(reader, sid, isovalue=0.05)
    # A lobe whose level no value reaches is skipped before contouring.
    reachable = [lvl for lvl in expected_levels if low <= lvl <= high]
    assert contour_levels == [pytest.approx(v) for v in reachable]
    signed = len(expected_levels) == 2
    colours = {tuple(m.color) for m in meshes}
    if signed:
        assert tuple(scenelib._ISO_NEUTRAL) not in colours
        assert tuple(scenelib._ISO_POSITIVE) in colours
    else:
        assert colours == {tuple(scenelib._ISO_NEUTRAL)}


# ── #38-45 / #38-46 / #38-47: raster and cell grid ───────────────────


def test_translucent_mesh_blends_over_what_is_behind_it():
    """#38-45: opacity<1 blended with the background and wrote depth, so a
    0.5-opacity red mesh in front of a white sphere read (91, 10, 10) and
    hid the sphere."""
    from vibeview.tui import raster

    c = raster.Canvas(20, 20, background=(0, 0, 0))
    c.draw_spheres(np.array([[0.0, 0.0, 10.0]]), np.array([2.0]), np.array([[255, 255, 255]]))
    behind = c.pixels[10, 10].astype(int)
    depth_behind = c.zbuf[10, 10]
    verts = np.array([[-5.0, -5.0, 5.0], [5.0, -5.0, 5.0], [0.0, 5.0, 5.0]])
    c.draw_mesh(verts, np.array([[0, 1, 2]]), np.array([[0.0, 0.0, -1.0]] * 3),
                (200, 0, 0), opacity=0.5)
    after = c.pixels[10, 10].astype(int)
    assert c.zbuf[10, 10] == depth_behind  # a translucent surface writes no depth
    assert after[1] >= behind[1] // 2 - 2 and after[1] > 60  # the white shows through
    assert after[0] > after[1]  # tinted red


@pytest.mark.parametrize("width", [60, 100, 200])
def test_large_triangles_have_no_pinholes(width):
    """#38-46: sample density capped at 48 left 2512 of 4463 interior pixels
    uncovered in a 95 px triangle."""
    from vibeview.tui import raster

    c = raster.Canvas(width, width)
    s, z = c.scale, 1.0

    def cam(sx, sy):
        return [(sx - width / 2) / s * z, (width / 2 - sy) / s * z, z]

    p0, p1, p2 = (2, 2), (width - 3, 2), (width / 2, width - 3)
    verts = np.array([cam(*p0), cam(*p1), cam(*p2)])
    c.draw_mesh(verts, np.array([[0, 1, 2]]), np.array([[0, 0, -1.0]] * 3),
                (255, 255, 255), fov=1.0)
    yy, xx = np.mgrid[0:width, 0:width]

    def edge(a, b):
        return (b[0] - a[0]) * (yy - a[1]) - (b[1] - a[1]) * (xx - a[0])

    e0, e1, e2 = edge(p0, p1), edge(p1, p2), edge(p2, p0)
    inside = ((e0 > 1) & (e1 > 1) & (e2 > 1)) | ((e0 < -1) & (e1 < -1) & (e2 < -1))
    assert int((inside & ~c.covered).sum()) == 0


def test_half_mode_blit_of_an_empty_buffer_keeps_text():
    """#38-47: blitting an empty buffer in half mode painted '▀' over text."""
    from vibeview.tui import braille

    g = braille.CellGrid(4, 2)
    g.text(0, 0, "AB")
    g.blit(np.zeros((4, 4, 3), dtype=np.uint8), np.zeros((4, 4), dtype=bool), 0, 0, mode="half")
    assert g.to_plain().splitlines()[0].startswith("AB")
    # Covered pixels still draw half blocks.
    cov = np.zeros((4, 4), dtype=bool)
    cov[2, 3] = True
    g.blit(np.full((4, 4, 3), 200, dtype=np.uint8), cov, 0, 0, mode="half")
    assert g.to_plain().splitlines()[1][3] == "▀"


# ── #38-33: the wavefunction's own geometry ──────────────────────────


def test_wavefunction_pane_shows_its_structure_ref_geometry():
    """#38-33: the table showed the primary structure (H at x=9) while the
    viewport drew the wavefunction on its ``structure_ref`` geometry."""
    from tests.test_new_kinds import _wf_qvf
    from vibeview.tui import panes

    path, sid = _wf_qvf("orbital_geometry")
    try:
        with QVFReader(path) as reader:
            wf_id = next(s.id for s in reader.sections if s.kind == "wavefunction.gto")
            text = panes.wavefunction(reader, wf_id)
            ref = panes.structure_table(reader, structure_id="orbital_geometry")
            primary = panes.structure_table(reader)
    finally:
        path.unlink(missing_ok=True)
    assert "Structure · orbital_geometry" in text
    assert ref.splitlines()[0] in text
    assert "9.00000" in primary and "9.00000" not in ref


# ── #38-34 / #38-35 / #38-36 / #38-39 / #38-41: the Textual app ──────


def _drive(coro_factory):
    import asyncio

    pytest.importorskip("textual")
    return asyncio.run(coro_factory())


def _tui_atoms(symbols):
    numbers = {"H": 1, "C": 6, "O": 8}
    return [
        {"symbol": s, "atomic_number": numbers[s], "position": [5 + 1.2 * i, 1, 0]}
        for i, s in enumerate(symbols)
    ]


def _binary(name, data):
    data = np.asarray(data, dtype="<f8")
    return "binary", name, data.tobytes(), "float64", list(data.shape)


def _vib_archive(tmp_path: Path, n_modes: int = 6) -> Path:
    meta = {
        "atoms": _tui_atoms(["C", "O"]),
        "frequencies": [1000.0 + 100 * i for i in range(n_modes)],
    }
    disp = np.zeros((n_modes, 2, 3))
    disp[:, 0, 0] = 0.1
    disp[:, 1, 0] = -0.1
    members = {
        "displacements": _binary("disp.bin", disp),
        "metadata": ("json", "meta.json", json.dumps(meta).encode()),
    }
    return build_qvf(tmp_path / "vib6.qvf", [("vib", "vibrations", members)])


def _many_sections_archive(tmp_path: Path, n: int = 40) -> Path:
    entries = []
    for i in range(n):
        meta = {"atoms": _tui_atoms(["C", "O"])}
        coords = np.array([a["position"] for a in meta["atoms"]], dtype=float)
        members = {
            "coords": _binary(f"t{i}-coords.bin", [coords, coords]),
            "metadata": ("json", f"t{i}-meta.json", json.dumps(meta).encode()),
        }
        entries.append((f"traj{i:02d}", "trajectory", members))
    return build_qvf(tmp_path / "many.qvf", entries)


def _status(app) -> str:
    bar = app.status_bar
    for attr in ("content", "renderable", "_content"):
        if hasattr(bar, attr):
            return str(getattr(bar, attr))
    return str(bar.render())


def test_vibration_status_shows_a_frame_counter_and_mode_clamps(tmp_path):
    """#38-34: no ``frame k/16`` for vibrations. #38-35: stepping past the
    last mode reached "mode 10" on a 6-mode archive."""
    from vibeview.tui.app import VibeViewTUI

    path = _vib_archive(tmp_path)

    async def body():
        app = VibeViewTUI(path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._build_3d(60, 20)
            app._update_status()
            assert "frame 1/16" in _status(app)
            for _ in range(9):
                app.action_step_mode(1)
            assert app.mode_index == 5
            app._build_3d(60, 20)
            app._update_status()
            assert "mode 6" in _status(app)
            app.action_step_mode(-1)
            assert app.mode_index == 4

    _drive(body)


def test_section_list_scrolls_to_the_selection(tmp_path):
    """#38-36: with 40 sections at height 20 the selection (35) was drawn
    nowhere and rows past 16 could not be clicked."""
    from vibeview.tui.app import VibeViewTUI

    path = _many_sections_archive(tmp_path)

    async def body():
        app = VibeViewTUI(path)
        async with app.run_test(size=(100, 20)) as pilot:
            await pilot.pause()
            for _ in range(35):
                app.action_next_section()
            await pilot.pause()
            sidebar = app.section_list
            assert app.selected == 35
            lines = [sidebar.render_line(y).text for y in range(sidebar.size.height)]
            marked = [line for line in lines if line.startswith("▸")]
            assert marked and "traj35" in marked[0]
            row = next(y for y, line in enumerate(lines) if "traj35" in line)
            assert sidebar.entry_at(row) == 35

    _drive(body)


def test_sidebar_status_is_the_terminals_own(tmp_path):
    """#38-39: the sidebar used the browser's registry, so fermi_surface read
    "rendered" though the terminal only dumps its manifest entry."""
    from vibeview.kinds import classify_section
    from vibeview.tui.app import terminal_renders

    assert classify_section("fermi_surface")[0] == "rendered"
    assert not terminal_renders("fermi_surface")
    assert not terminal_renders("reaction.waypoints")
    for kind in ("structure", "bands", "volume.orbital", "vibrations", "citations"):
        assert terminal_renders(kind), kind


def test_show_closes_its_reader(tmp_path, monkeypatch, sample_qvf):
    """#38-41: show()/show_all() opened a QVFReader and never closed it."""
    import vibeview.tui.show as showmod

    closed: list[int] = []
    real = showmod.QVFReader

    class Counting(real):  # type: ignore[misc, valid-type]
        def close(self) -> None:
            closed.append(1)
            super().close()

    monkeypatch.setattr(showmod, "QVFReader", Counting)
    showmod.show(sample_qvf, size=(40, 12), plain=True)
    showmod.show_all(sample_qvf, size=(40, 12), plain=True)
    assert len(closed) >= 2


def test_tui_closes_its_reader_on_exit(sample_qvf):
    from vibeview.tui.app import VibeViewTUI

    closed: list[int] = []

    async def body():
        app = VibeViewTUI(sample_qvf)
        real_close = app.reader.close
        app.reader.close = lambda: (closed.append(1), real_close())[1]  # type: ignore[method-assign]
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

    _drive(body)
    assert closed


# ── #38-50 / #38-52: `vibe-view show` options ────────────────────────


def test_show_normal_mode_selects_the_mode(tmp_path):
    """#38-50: `--frame` promised normal-mode frames but the mode was
    hard-coded to 0 and no option chose another."""
    from click.testing import CliRunner

    from vibeview.cli import main
    from vibeview.tui import scene as scenelib
    from vibeview.tui.show import render_section

    meta = {
        "atoms": _tui_atoms(["C", "O"]),
        "frequencies": [1000.0, 1100.0],
    }
    disp = np.zeros((2, 2, 3))
    disp[0, :, 0] = [0.1, -0.1]  # mode 1 stretches along x
    disp[1, :, 1] = [0.1, -0.1]  # mode 2 moves along y
    members = {
        "displacements": _binary("disp.bin", disp),
        "metadata": ("json", "meta.json", json.dumps(meta).encode()),
    }
    path = build_qvf(tmp_path / "two_modes.qvf", [("vib", "vibrations", members)])
    with QVFReader(path) as reader:
        m1 = render_section(reader, "vib", 40, 12, frame=4, normal_mode=0).to_plain()
        m2 = render_section(reader, "vib", 40, 12, frame=4, normal_mode=1).to_plain()
        c2, _ = scenelib.vibration_frames(reader, "vib", 1)
    assert m1 != m2
    assert np.ptp(c2[:, 0, 1]) > 0  # mode 2 really moves along y

    result = CliRunner().invoke(
        main, ["show", str(path), "--plain", "--size", "40x12", "--frame", "4",
               "--normal-mode", "2"],
    )
    assert result.exit_code == 0, result.output
    def norm(text: str) -> list[str]:
        return [line.rstrip() for line in text.strip("\n").splitlines()]

    assert norm(result.output) == norm(m2)
    assert "--normal-mode" in CliRunner().invoke(main, ["show", "--help"]).output


def test_show_defaults_are_pinned_to_the_terminal():
    """#38-52: the CLI's -14,-31 degrees and the built-in -0.25,-0.55 radians
    differed; --color-by choices were a literal nothing compared."""
    import inspect
    import math

    from vibeview.cli import _TUI_COLOR_MODES, _TUI_DEFAULT_ROTATE, _parse_rotation
    from vibeview.tui.app import COLOR_CYCLE
    from vibeview.tui.raster import DEFAULT_ROTATION, DEFAULT_ROTATION_DEG
    from vibeview.tui.show import render_section

    assert tuple(_TUI_COLOR_MODES) == tuple(COLOR_CYCLE)
    parsed = _parse_rotation(_TUI_DEFAULT_ROTATE)
    assert parsed == pytest.approx(DEFAULT_ROTATION)
    expected = tuple(math.radians(a) for a in DEFAULT_ROTATION_DEG)
    assert list(DEFAULT_ROTATION) == pytest.approx(list(expected))

    assert inspect.signature(render_section).parameters["rotation"].default == DEFAULT_ROTATION
