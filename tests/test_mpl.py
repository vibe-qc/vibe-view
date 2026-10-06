"""renderers._mpl chooses the matplotlib backend once and rasterises once."""

from __future__ import annotations

import importlib
import sys

import pytest

from vibeview.renderers import _mpl

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_importing_a_renderer_does_not_select_a_backend(monkeypatch) -> None:
    """The backend is a process-wide setting; picking it belongs to first use."""
    import matplotlib

    calls: list[str] = []
    monkeypatch.setattr(matplotlib, "use", lambda name, *a, **k: calls.append(name))
    for name in ("bands", "reaction", "scan_surface", "spectra", "trajectory"):
        module = f"vibeview.renderers.{name}"
        monkeypatch.delitem(sys.modules, module, raising=False)
        importlib.import_module(module)
    assert calls == []


def test_agg_pyplot_selects_agg_once() -> None:
    import matplotlib

    plt = _mpl.agg_pyplot()
    assert matplotlib.get_backend().lower() == "agg"
    assert _mpl.agg_pyplot() is plt


def test_figure_to_png_encodes_and_closes() -> None:
    plt = _mpl.agg_pyplot()
    fig, ax = plt.subplots(figsize=(2, 1))
    ax.plot([0, 1], [0, 1])
    number = fig.number
    png = _mpl.figure_to_png(fig)
    assert png.startswith(PNG_MAGIC)
    assert not plt.fignum_exists(number)


@pytest.mark.parametrize("dpi", [_mpl.PNG_DPI, 50])
def test_figure_to_png_honours_dpi(dpi: int) -> None:
    import io

    from PIL import Image

    plt = _mpl.agg_pyplot()
    fig, _ax = plt.subplots(figsize=(2, 1))
    png = _mpl.figure_to_png(fig, dpi=dpi)
    assert Image.open(io.BytesIO(png)).size == (2 * dpi, 1 * dpi)


def test_no_renderer_keeps_its_own_png_block() -> None:
    import inspect

    for name in ("bands", "reaction", "scan_surface", "spectra", "trajectory"):
        source = inspect.getsource(importlib.import_module(f"vibeview.renderers.{name}"))
        assert "BytesIO" not in source, name
        assert 'matplotlib.use("Agg")' not in source, name
