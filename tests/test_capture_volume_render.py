"""``capture --render volume``: the browser's volume mode, off-screen.

``render="auto"`` follows the section's ``render`` hint, so a file that opens
in volume mode captures the same way; ``"volume"`` and ``"isosurface"`` force
a look. The hint's transfer function and window are honoured, a grid the
payload refuses is a clean capture error, and batch follows the hint too.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tests._qvf_builders import sha256, write_archive

_STRUCTURE = json.dumps(
    {
        "atoms": [
            {"symbol": "O", "position": [0.0, 0.0, 0.0], "atomic_number": 8},
            {"symbol": "H", "position": [0.0, 0.757, 0.587], "atomic_number": 1},
            {"symbol": "H", "position": [0.0, -0.757, 0.587], "atomic_number": 1},
        ],
        "pbc": [False, False, False],
    }
).encode()


def _archive(dest: Path, *, hint: dict | None = None, skew: float = 0.0) -> Path:
    shape = (16, 16, 16)
    axes = [np.linspace(-4.0, 4.0, n) for n in shape]
    spacing = [8.0 / (n - 1) for n in shape]
    grid = json.dumps(
        {
            "origin": [-4.0, -4.0, -4.0],
            "voxel_vectors": [[spacing[0], skew, 0], [0, spacing[1], 0], [0, 0, spacing[2]]],
            "shape": list(shape),
        }
    ).encode()
    x, y, z = np.meshgrid(*axes, indexing="ij")
    data = np.exp(-(x**2 + y**2 + z**2) / 2.0).astype(np.float32).tobytes()
    sections = [
        {
            "id": "structure",
            "kind": "structure",
            "members": {
                "structure": {
                    "path": "sections/structure.json",
                    "format": "json",
                    "sha256": sha256(_STRUCTURE),
                }
            },
        },
        {
            "id": "dens",
            "kind": "volume.density",
            "members": {
                "grid": {
                    "path": "sections/dens/grid.json",
                    "format": "json",
                    "sha256": sha256(grid),
                },
                "data": {
                    "path": "sections/dens/data.dat",
                    "format": "binary",
                    "sha256": sha256(data),
                    "dtype": "float32",
                    "shape": list(shape),
                },
            },
        },
    ]
    files = {
        "sections/structure.json": _STRUCTURE,
        "sections/dens/grid.json": grid,
        "sections/dens/data.dat": data,
    }
    defaults = {"dens": hint} if hint else None
    return write_archive(sections, files, dest=dest, viewer_defaults=defaults)


def test_add_volume_render_draws_one_volume_actor_through_the_hint(tmp_path):
    from vibeview.headless import add_structure, add_volume_render, headless_plotter
    from vibeview.qvf import QVFReader
    from vibeview.renderers.volume_render import is_volume_actor

    reader = QVFReader(_archive(tmp_path / "d.qvf"))
    try:
        with headless_plotter((160, 120)) as plotter:
            assert add_structure(plotter, reader)
            hint = {
                "range": [0.0, 0.5],
                "color": [[0.0, 0, 0, 1], [0.5, 1, 0, 0]],
                "opacity": [[0.0, 0.0], [0.5, 0.8]],
                "colormap": "custom",
            }
            volume = add_volume_render(plotter, reader, "dens", transfer_function=hint)
            assert volume.range == (0.0, 0.5), "the hint's window is the quantisation range"
            actor = plotter.actors["capture_volume"]
            assert is_volume_actor(actor)
            assert actor.GetProperty().GetScalarOpacity(0).GetValue(255.0) == pytest.approx(
                0.8, abs=0.02
            )
            assert not any(str(n).startswith("capture_vol") and n != "capture_volume"
                           for n in plotter.actors), "no isosurface beside the volume"
    finally:
        reader.close()


def test_render_auto_follows_the_hint_and_the_modes_force_it(tmp_path):
    from vibeview.capture import capture_volume
    from vibeview.qvf import QVFReader

    plain = QVFReader(_archive(tmp_path / "plain.qvf"))
    hinted = QVFReader(_archive(tmp_path / "hinted.qvf", hint={"render": "volume"}))
    try:
        out = {}
        for name, reader, render in (
            ("plain_auto", plain, "auto"),
            ("plain_volume", plain, "volume"),
            ("plain_iso", plain, "isosurface"),
            ("hinted_auto", hinted, "auto"),
            ("hinted_iso", hinted, "isosurface"),
        ):
            path = tmp_path / f"{name}.png"
            assert capture_volume(reader, "dens", path, size=(160, 120), render=render) is True
            out[name] = path.read_bytes()
        assert out["plain_auto"] == out["plain_iso"], "no hint: the isosurface, as before"
        assert out["hinted_auto"] == out["plain_volume"], "the hint opens the volume"
        assert out["hinted_iso"] == out["plain_iso"], "the mode can be forced either way"
        assert out["plain_volume"] != out["plain_iso"], "the two looks differ"
    finally:
        plain.close()
        hinted.close()


def test_a_skewed_grid_captures_as_a_volume_and_refusals_are_clean(tmp_path, monkeypatch):
    from vibeview.capture import CaptureError, capture_volume, render_volume
    from vibeview.qvf import QVFReader

    reader = QVFReader(_archive(tmp_path / "skew.qvf", skew=0.1))
    try:
        # A skewed grid is resampled onto its bounding box and drawn.
        assert capture_volume(
            reader, "dens", tmp_path / "v.png", size=(160, 120), render="volume"
        ) is True
        assert capture_volume(reader, "dens", tmp_path / "i.png", size=(160, 120)) is True
        with pytest.raises(CaptureError, match="render must be one of"):
            render_volume(reader, "dens", tmp_path / "x.png", render="cloud")
        # A field the payload refuses is a capture error with the reason.
        import vibeview.renderers.volume as volume_module

        real_load = volume_module.VolumeRenderer.load_data
        monkeypatch.setattr(
            volume_module.VolumeRenderer, "load_data",
            lambda self: np.full_like(real_load(self), np.nan),
        )
        with pytest.raises(CaptureError, match="finite"):
            render_volume(reader, "dens", tmp_path / "n.png", size=(160, 120), render="volume")
        assert capture_volume(reader, "dens", tmp_path / "n.png", render="volume") is False
    finally:
        reader.close()


def test_the_cli_takes_render_and_batch_follows_the_hint(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from vibeview import batch
    from vibeview.cli import main
    from vibeview.qvf import QVFReader

    hinted = _archive(tmp_path / "hinted.qvf", hint={"render": "volume"})
    out = tmp_path / "cli.png"
    result = CliRunner().invoke(
        main, ["capture", str(hinted), "-s", "dens", "--render", "volume", "-o", str(out),
               "--size", "160x120"]
    )
    assert result.exit_code == 0, result.output
    assert out.stat().st_size > 100
    bad = CliRunner().invoke(main, ["capture", str(hinted), "-s", "dens", "--render", "cloud"])
    assert bad.exit_code == 2

    seen: list[dict] = []
    monkeypatch.setattr(
        "vibeview.capture.capture_volume", lambda *_a, **kw: seen.append(kw) or True
    )
    monkeypatch.setattr(batch, "render_structure_png", lambda *_a, **_k: True)
    with QVFReader(hinted) as reader:
        batch.render_all_png(reader, tmp_path, include_volumes=True)
    assert seen and seen[0]["render"] == "volume"
