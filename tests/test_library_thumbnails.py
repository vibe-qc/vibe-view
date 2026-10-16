"""Thumbnails in the structure library.

The library card previews the current pick and shows a picture in every
favourite and recent chip. The thumbnails come from ``vibeqc_naming``'s
geometry through an in-memory archive, are rendered in the background once
a name is picked, pinned or used, and are pruned to the names the card
shows. Without the package there is nothing to draw and nothing breaks.
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import sys
import types

import pytest
from PIL import Image

from vibeview.headless import THUMBNAIL_SIZE, molecule_thumbnail
from vibeview.qvf import QVFReader

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

_WATER = [(8, 0.0, 0.0, 0.1173), (1, 0.0, 0.7572, -0.4692), (1, 0.0, -0.7572, -0.4692)]
_METHANE = [
    (6, 0.0, 0.0, 0.0),
    (1, 0.629, 0.629, 0.629),
    (1, -0.629, -0.629, 0.629),
    (1, -0.629, 0.629, -0.629),
    (1, 0.629, -0.629, -0.629),
]


def _fake_naming(monkeypatch):
    """A stand-in vibeqc_naming with two molecules; records the names asked for."""
    db = {"water": _WATER, "methane": _METHANE}
    asked: list[str] = []
    mod = types.ModuleType("vibeqc_naming")
    mod.known_names = lambda: sorted(db)
    mod.structure_from_name = lambda name: (asked.append(name), db.get(name))[1]
    mod._asked = asked
    monkeypatch.setitem(sys.modules, "vibeqc_naming", mod)
    return mod


def _png_size(src: str) -> tuple[int, int]:
    assert src.startswith("data:image/png;base64,")
    return Image.open(io.BytesIO(base64.b64decode(src.split(",", 1)[1]))).size


@pytest.fixture
def app(sample_qvf):
    from vibeview.app import create_app

    reader = QVFReader(sample_qvf)
    app = create_app(reader)
    app.controller.view_update = lambda *args, **kwargs: None
    yield app
    reader.close()


def _run(app, start, until, seconds: float = 60.0):
    """Call ``start`` on a running loop, then wait until ``until()`` holds."""

    async def run():
        start()
        for _ in range(int(seconds / 0.05)):
            if until():
                return
            await asyncio.sleep(0.05)
        raise AssertionError("the thumbnails did not arrive")

    asyncio.run(run())


def test_molecule_thumbnail_is_a_small_png_and_none_without_atoms():
    png = molecule_thumbnail(_WATER, label="water")
    assert png is not None and png.startswith(b"\x89PNG")
    assert Image.open(io.BytesIO(png)).size == THUMBNAIL_SIZE
    assert molecule_thumbnail([]) is None
    assert molecule_thumbnail(_METHANE) != png


def test_thumbnails_follow_the_pick_the_favourites_and_the_recents(app, monkeypatch):
    fake = _fake_naming(monkeypatch)
    app.state.library_query = "water"
    _run(app, app.controller.refresh_library_thumbnails,
         lambda: "water" in app.state.library_thumbnails)
    assert _png_size(app.state.library_thumbnails["water"]) == THUMBNAIL_SIZE
    assert fake._asked == ["water"]

    app.controller.toggle_library_favorite("methane")
    assert app.state.library_favorites == ["methane"]
    _run(app, app.controller.refresh_library_thumbnails,
         lambda: "methane" in app.state.library_thumbnails)
    assert set(app.state.library_thumbnails) == {"water", "methane"}
    assert fake._asked == ["water", "methane"], "a cached name is not rendered again"

    # Dropping the pick and the pin prunes the cache to what the card shows.
    app.state.library_query = None
    app.controller.toggle_library_favorite("methane")
    app.controller.refresh_library_thumbnails()
    assert app.state.library_thumbnails == {}


def test_an_unknown_name_and_a_missing_package_draw_nothing(app, monkeypatch):
    _fake_naming(monkeypatch)
    app.state.library_query = "unobtainium"
    app.controller.refresh_library_thumbnails()

    async def settle():
        await asyncio.sleep(0.5)

    asyncio.run(settle())
    assert app.state.library_thumbnails == {}

    monkeypatch.setitem(sys.modules, "vibeqc_naming", None)  # -> ImportError
    app.state.library_query = "water"
    app.controller.refresh_library_thumbnails()
    assert app.state.library_thumbnails == {}


def test_the_library_keys_are_watched(app):
    """A pick from the autocomplete, a pin or a recent use refreshes the cache."""
    watched = {
        key for key, func in app._vibeview_state_watchers if func.__name__ == "_on_library_change"
    }
    assert watched == {"library_query", "library_favorites", "library_recent"}


def test_the_template_previews_the_pick_and_pictures_the_chips(app):
    template = app.state["trame__template_main"]
    assert 'class="vv-library-preview"' in template
    assert ':src="library_thumbnails[library_query]"' in template
    assert template.count('class="vv-chip-thumb"') == 2
    assert ':src="library_thumbnails[fname]"' in template
    assert ':src="library_thumbnails[rname]"' in template
