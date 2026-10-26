"""``volume.transition_density`` is a supported signed volume (#115).

The kind was reported as "skipped, unsupported" although the schema defines
it with the members of a density (QVF spec § 5.2), an excited state links to
it through ``density_ref`` (#109), and the generic volume path already drew
it. It is now registered as a supported signed volume with a sidebar title,
the same correction #96 made for ``excited_states``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibeview.kinds import SIGNED_VOLUME_KINDS, SUPPORTED_KINDS, VOLUME_KINDS, classify_section
from vibeview.qvf import QVFReader

EXAMPLE = (
    Path(__file__).resolve().parents[1] / "docs" / "examples" / "tddft-water" / "tddft-water.qvf"
)
KIND = "volume.transition_density"


def test_the_kind_is_a_supported_signed_volume():
    assert KIND in SUPPORTED_KINDS
    assert KIND in VOLUME_KINDS
    assert KIND in SIGNED_VOLUME_KINDS
    assert classify_section(KIND) == ("rendered", None)


def test_the_renderer_registry_maps_it_to_the_volume_renderer():
    from vibeview.renderers import get_renderer
    from vibeview.renderers.volume import VolumeRenderer

    with QVFReader(EXAMPLE) as reader:
        section = reader.get_section("tden_s1")
        assert section.kind == KIND
        assert isinstance(get_renderer(section, reader), VolumeRenderer)


def test_the_example_archive_lists_it_as_rendered():
    from click.testing import CliRunner

    from vibeview.cli import main

    result = CliRunner().invoke(main, ["info", str(EXAMPLE)])
    assert result.exit_code == 0, result.output
    line = next(line for line in result.output.splitlines() if line.startswith("tden_s1"))
    assert "unsupported" not in line and "skipped" not in line


def test_headless_capture_draws_it(tmp_path):
    from vibeview.api import capture_volume

    out = tmp_path / "tden.png"
    capture_volume(EXAMPLE, "tden_s1", out)
    assert out.is_file() and out.stat().st_size > 1000


@pytest.fixture
def plotters(monkeypatch):
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


def test_the_browser_shows_it_as_a_signed_volume(plotters):
    from vibeview.app import create_app

    with QVFReader(EXAMPLE) as reader:
        server = create_app([reader])
        server.controller.view_update = lambda *a, **k: None
        server.controller.activate_section("tden_s1")
        state = server.state
        assert state.selected_section == "tden_s1"
        assert state.active_volume_id == "tden_s1"
        assert state.status_message.startswith("Loaded volume: tden_s1")
        # A transition density is a charge density (spec § 5.2), not an
        # arbitrary field, so the isovalue carries the density unit.
        assert state.isovalue_units == "e/bohr³"
