"""#119: transition densities open with the difference-density defaults, and an
isosurface that comes out empty says so.

The shipped TD-DFT example's transition density peaks at ±0.047. It opened
with the generic isovalue 0.05, so the browser and headless capture drew the
atoms alone while the status line reported the volume as loaded.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner

from vibeview.qvf import QVFReader
from vibeview.qvf_writer import QVFArchiveBuilder, structure_payload
from vibeview.viewer_defaults import section_volume_hints

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "docs" / "examples" / "tddft-water" / "tddft-water.qvf"
SECTION = "tden_s1"


@pytest.fixture
def plotters(monkeypatch):
    """Every plotter the app creates, closed afterwards (as in the #106 tests)."""
    import vibeview.app.helpers as helpers

    made = []
    create = helpers.new_plotter

    def record(*args, **kwargs):
        plotter = create(*args, **kwargs)
        made.append(plotter)
        return plotter

    monkeypatch.setattr(helpers, "new_plotter", record)
    yield made
    for plotter in made:
        plotter.close()


def _open(path: Path, plotters):
    from vibeview.app import create_app

    server = create_app(QVFReader(path))
    server.controller.view_update = lambda *a, **k: None
    return server, plotters[-1]


def _field_peak(path: Path, section_id: str) -> float:
    with QVFReader(path) as reader:
        volume = reader.read_volume_data(section_id)
        values = getattr(volume, "data", None)
        if values is None:
            values = volume.values
        return float(np.max(np.abs(np.asarray(values))))


def test_the_kind_opens_like_a_difference_density():
    with QVFReader(EXAMPLE) as reader:
        hints = section_volume_hints(reader, SECTION)
    assert (hints.isovalue, hints.colormap, hints.opacity) == (0.005, "RdBu", 0.6)


def test_the_default_isovalue_lies_inside_the_example_field():
    with QVFReader(EXAMPLE) as reader:
        isovalue = section_volume_hints(reader, SECTION).isovalue
    assert 0 < isovalue < _field_peak(EXAMPLE, SECTION)


def test_the_browser_draws_both_lobes(plotters):
    server, plotter = _open(EXAMPLE, plotters)
    server.controller.activate_section(SECTION)
    names = {str(name) for name in plotter.actors}
    assert {f"volume_{SECTION}_pos", f"volume_{SECTION}_neg"} <= names
    assert server.state.status_message == f"Loaded volume: {SECTION}"
    assert server.state.isovalue == 0.005


def _lobe_pixels(png: Path) -> tuple[int, int]:
    """Bluish and reddish pixels away from the atoms' own colours."""
    from PIL import Image

    rgb = np.asarray(Image.open(png).convert("RGB")).astype(int)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return int(((b > r + 40) & (b > g + 20)).sum()), int(((r > b + 60) & (g < 90)).sum())


def test_headless_capture_draws_the_lobes_by_default(tmp_path):
    from vibeview.cli import main

    default, generic = tmp_path / "default.png", tmp_path / "generic.png"
    for out, extra in ((default, []), (generic, ["--isovalue", "0.05"])):
        result = CliRunner().invoke(
            main,
            ["capture", str(EXAMPLE), "-s", SECTION, "-o", str(out), "--size", "320x240", *extra],
        )
        assert result.exit_code == 0, result.output
    blue, _red = _lobe_pixels(default)
    assert blue > 200, "the default capture shows no positive lobe"
    # The generic isovalue the kind used to open with draws no lobe at all.
    assert _lobe_pixels(generic)[0] < 20


def _weak_density(path: Path) -> Path:
    """A density whose largest value, 0.01, lies below the kind default 0.05."""
    builder = QVFArchiveBuilder(program="test", version="0", calculation="weak")
    atoms = [{"symbol": "H", "atomic_number": 1, "position": [0.0, 0.0, 0.0]}]
    builder.add_json("structure", "structure", "structure", structure_payload(atoms, [False] * 3))
    data = np.zeros((8, 8, 8), dtype=np.float32)
    data[3:5, 3:5, 3:5] = 0.01
    builder.add_json(
        "rho",
        "volume.density",
        "grid",
        {
            "origin": [-3.0, -3.0, -3.0],
            "voxel_vectors": [[0.75, 0, 0], [0, 0.75, 0], [0, 0, 0.75]],
            "shape": [8, 8, 8],
        },
    )
    builder.add_binary("rho", "volume.density", "data", data)
    return Path(builder.write(path))


def test_an_empty_isosurface_is_reported(tmp_path, plotters):
    server, plotter = _open(_weak_density(tmp_path / "weak.qvf"), plotters)
    server.controller.activate_section("rho")
    assert not any(str(name).startswith("volume_") for name in plotter.actors)
    assert server.state.status_message == (
        "Loaded volume: rho, but no surface at isovalue 0.05: "
        "its largest value 0.01; lower the isovalue"
    )


def test_a_signed_empty_isosurface_names_the_magnitude():
    from types import SimpleNamespace

    from vibeview.app.helpers import _empty_surface_message

    section = SimpleNamespace(id="tden", kind="volume.transition_density")
    data = np.array([[-0.047, 0.02], [0.03, np.nan]])
    assert _empty_surface_message(section, 0.05, data) == (
        "Loaded volume: tden, but no surface at isovalue ±0.05: "
        "its largest |value| 0.047; lower the isovalue"
    )
    assert "no finite values" in _empty_surface_message(section, 0.05, np.full(3, np.nan))


def test_raising_the_isovalue_past_the_field_does_not_raise(tmp_path, plotters):
    """The slider path rebuilt the contour into an empty mesh and add_mesh
    raised ValueError out of the controller."""
    server, plotter = _open(_weak_density(tmp_path / "weak.qvf"), plotters)
    server.controller.activate_section("rho")
    server.controller.update_isovalue(0.005)
    assert any(str(name).startswith("volume_") for name in plotter.actors)
    server.controller.update_isovalue(0.5)
    assert not any(str(name).startswith("volume_") for name in plotter.actors)
