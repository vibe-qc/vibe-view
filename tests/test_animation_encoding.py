"""GIF encoding keeps source-file usage bounded, including on failures."""

from __future__ import annotations

import builtins
import errno

import pytest

from vibeview import animation


@pytest.fixture
def png_frames(tmp_path):
    from PIL import Image

    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    colours = [(index, 3 * index % 256, 5 * index % 256) for index in range(64)]
    for index, colour in enumerate(colours):
        with Image.new("RGB", (4, 4), colour) as image:
            image.save(frame_dir / f"frame_{index:04d}.png")
    return frame_dir, colours


def _track_sources(monkeypatch, limit=None):
    from PIL import Image

    opened = []
    original_open = Image.open

    def tracked_open(*args, **kwargs):
        live = sum(image.fp is not None for image in opened)
        if limit is not None and live >= limit:
            raise OSError(errno.EMFILE, "too many open input frames")
        image = original_open(*args, **kwargs)
        opened.append(image)
        return image

    monkeypatch.setattr(Image, "open", tracked_open)
    return opened, original_open


@pytest.mark.parametrize("format", ["gif", "mp4"])
def test_gif_encoding_does_not_exhaust_input_file_handles(
    tmp_path, png_frames, monkeypatch, format
):
    frame_dir, colours = png_frames
    opened, original_open = _track_sources(monkeypatch, limit=3)
    monkeypatch.setattr(animation, "_ffmpeg_available", lambda: False)

    output = animation._encode_frames(frame_dir, tmp_path / f"movie.{format}", format, fps=10)

    assert output == tmp_path / "movie.gif"
    assert len(opened) == len(colours)
    assert all(image.fp is None for image in opened)
    with original_open(output) as gif:
        assert gif.format == "GIF"
        assert gif.n_frames == len(colours)
        assert gif.info["loop"] == 0
        for index, colour in enumerate(colours):
            gif.seek(index)
            assert gif.info["duration"] == 100
            assert gif.convert("RGB").getpixel((0, 0)) == colour


def test_gif_encoding_closes_sources_after_decode_error(tmp_path, png_frames, monkeypatch):
    from PIL import PngImagePlugin

    frame_dir, _ = png_frames
    opened, _ = _track_sources(monkeypatch)
    original_load = PngImagePlugin.PngImageFile.load

    def failing_load(image, *args, **kwargs):
        if str(image.filename).endswith("frame_0001.png"):
            raise OSError("input frame decode failed")
        return original_load(image, *args, **kwargs)

    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", failing_load)

    with pytest.raises(OSError, match="input frame decode failed"):
        animation._frames_to_gif(frame_dir, tmp_path / "failed.gif")

    assert opened
    assert all(image.fp is None for image in opened)


def test_gif_encoding_closes_sources_after_save_error(tmp_path, png_frames, monkeypatch):
    from PIL import Image

    frame_dir, _ = png_frames
    opened, _ = _track_sources(monkeypatch)

    def failing_save(*args, **kwargs):
        raise OSError("GIF output write failed")

    monkeypatch.setattr(Image.Image, "save", failing_save)

    with pytest.raises(OSError, match="GIF output write failed"):
        animation._frames_to_gif(frame_dir, tmp_path / "failed.gif")

    assert len(opened) == 64
    assert all(image.fp is None for image in opened)


def test_gif_encoding_reports_missing_pillow(tmp_path, monkeypatch):
    original_import = builtins.__import__

    def without_pillow(name, *args, **kwargs):
        if name == "PIL":
            raise ModuleNotFoundError("Pillow is not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_pillow)

    output = tmp_path / "missing.gif"
    assert animation._frames_to_gif(tmp_path, output) is False
    assert not output.exists()
