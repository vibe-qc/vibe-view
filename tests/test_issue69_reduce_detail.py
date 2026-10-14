"""Reduce detail draws the subsampled grid, not the full array (#69).

The reduced-detail path subsampled a local array and shrank the renderer's
grid, but the renderer kept its full-resolution array, so every unsigned
volume with 200 or more voxels along an axis failed on the point-array
length. Grids between 101 and 199 voxels drew at full resolution while the
status line announced a reduction that never happened.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from tests._qvf_builders import build_qvf

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


def _volume_archive(dest: Path, shape: tuple[int, int, int], *, signed: bool = False) -> Path:
    """A water structure plus one float32 volume spanning [-4, 4]³ on ``shape``."""
    axes = [np.linspace(-4.0, 4.0, n) for n in shape]
    spacing = [8.0 / (n - 1) for n in shape]
    grid = {
        "origin": [-4.0, -4.0, -4.0],
        "voxel_vectors": [[spacing[0], 0, 0], [0, spacing[1], 0], [0, 0, spacing[2]]],
        "shape": list(shape),
    }
    x, y, z = np.meshgrid(*axes, indexing="ij")

    def blob(cx: float) -> np.ndarray:
        return np.exp(-((x - cx) ** 2 + y**2 + z**2) / 2.0)

    field = (blob(1.0) - blob(-1.0)) if signed else blob(0.0)
    data = field.astype(np.float32).tobytes()
    kind, sid = ("volume.spin", "vol_spin_0") if signed else ("volume.density", "vol_dens_0")
    return build_qvf(
        dest,
        [
            (
                "structure",
                "structure",
                {"structure": ("json", "sections/structure.json", _STRUCTURE)},
            ),
            (
                sid,
                kind,
                {
                    "grid": ("json", f"sections/{sid}/grid.json", json.dumps(grid).encode()),
                    "data": ("binary", f"sections/{sid}/data.dat", data, "float32", list(shape)),
                },
            ),
        ],
    )


def _open(monkeypatch, path: Path):
    """``create_app`` on ``path`` plus the plotter it built."""
    import vibeview.app as appmod
    from vibeview.qvf import QVFReader

    created = []
    plotter_type = appmod.pv.Plotter

    def create_plotter(*args, **kwargs):
        plotter = plotter_type(*args, **kwargs)
        created.append(plotter)
        return plotter

    monkeypatch.setattr(appmod.pv, "Plotter", create_plotter)
    reader = QVFReader(path)
    server = appmod.create_app(reader)
    server.controller.view_update = lambda *_args, **_kwargs: None
    return server, created[0], reader


def _volume_actors(plotter) -> list[str]:
    return sorted(str(n) for n in plotter.actors if str(n).startswith("volume_"))


def test_a_large_unsigned_volume_opens_reduced(tmp_path, monkeypatch):
    server, plotter, reader = _open(
        monkeypatch, _volume_archive(tmp_path / "big.qvf", (200, 16, 16))
    )
    try:
        server.controller.activate_section("vol_dens_0")
        state = server.state
        assert "Could not open" not in str(state.status_message), state.status_message
        assert "200×16×16 → 100×8×8" in str(state.status_message), state.status_message
        assert state.volume_loaded is True
        assert _volume_actors(plotter) == ["volume_vol_dens_0"]
        assert plotter.actors["volume_vol_dens_0"].GetMapper().GetInput().GetNumberOfPoints() > 0
    finally:
        reader.close()


def test_a_moderate_grid_draws_at_full_resolution_and_says_nothing(tmp_path, monkeypatch):
    server, plotter, reader = _open(
        monkeypatch, _volume_archive(tmp_path / "mid.qvf", (150, 16, 16))
    )
    try:
        server.controller.activate_section("vol_dens_0")
        assert "reduced" not in str(server.state.status_message), server.state.status_message
        assert _volume_actors(plotter) == ["volume_vol_dens_0"]
    finally:
        reader.close()


def test_reduce_detail_off_draws_the_full_grid(tmp_path, monkeypatch):
    server, plotter, reader = _open(
        monkeypatch, _volume_archive(tmp_path / "big.qvf", (200, 16, 16))
    )
    try:
        server.state.reduce_detail = False
        server.controller.activate_section("vol_dens_0")
        assert "reduced" not in str(server.state.status_message), server.state.status_message
        assert "Could not open" not in str(server.state.status_message)
        assert _volume_actors(plotter) == ["volume_vol_dens_0"]
    finally:
        reader.close()


def test_signed_volumes_are_never_reduced(tmp_path, monkeypatch):
    server, plotter, reader = _open(
        monkeypatch, _volume_archive(tmp_path / "spin.qvf", (200, 16, 16), signed=True)
    )
    try:
        server.controller.activate_section("vol_spin_0")
        assert "reduced" not in str(server.state.status_message), server.state.status_message
        assert "Could not open" not in str(server.state.status_message)
        assert _volume_actors(plotter) == ["volume_vol_spin_0_neg", "volume_vol_spin_0_pos"]
    finally:
        reader.close()
