"""Regressions for the CLI, API and headless refactor rows in #52.

Each test names the audit row it pins (#39-N in the issue body).
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner

from vibeview.cli import main
from vibeview.qvf import QVFReader, scf_energy_eh
from vibeview.qvf_writer import structure_archive

_ROOT = Path(__file__).resolve().parent.parent
_WATER = [
    {"symbol": "O", "atomic_number": 8, "position": [0.0, 0.0, 0.1173]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, 0.7572, -0.4692]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, -0.7572, -0.4692]},
]


def _water(tmp_path: Path, name: str = "water.qvf", **provenance) -> Path:
    builder = structure_archive(_WATER, (False, False, False), calculation="water")
    if provenance:
        builder.set_provenance(provenance)
    return builder.write(tmp_path / name)


def _periodic(tmp_path: Path) -> Path:
    atoms = [{"symbol": "Na", "atomic_number": 11, "position": [0.0, 0.0, 0.0]}]
    lattice = [[4.0, 0.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 4.0]]
    return structure_archive(atoms, (True, True, True), lattice, calculation="na").write(
        tmp_path / "na.qvf"
    )


# ── #39-22: one provenance energy helper ────────────────────────────────


def test_scf_energy_is_read_through_one_helper(tmp_path):
    assert scf_energy_eh({"scf_energy": {"value": -76.4}}) == -76.4
    assert scf_energy_eh({"scf_energy": -76.4}) == -76.4
    assert scf_energy_eh({"scf_energy": {"unit": "hartree"}}) is None
    assert scf_energy_eh({}) is None
    with QVFReader(_water(tmp_path, scf_energy={"value": -76.4, "units": "hartree"})) as reader:
        assert reader.scf_energy_eh() == -76.4
    for module in ("cli", "api", "batch_compare", "serve", "app"):
        from tests._sources import read_source

        source = read_source(f"{module}.py")
        assert "model_dump" not in source, module
        assert 'getattr(reader.manifest, "provenance"' not in source, module


# ── #39-23: the CLI is built on the API ─────────────────────────────────


def test_cli_commands_delegate_to_the_api():
    import vibeview.cli as cli

    for command, function in (
        ("info_cmd", "info("),
        ("diff_cmd", "diff("),
        ("validate_cmd", "validate("),
        ("table_cmd", "get_table("),
    ):
        source = inspect.getsource(getattr(cli, command).callback)
        assert "from vibeview.api import" in source and function in source, command
    assert not hasattr(cli, "_get_prov") and not hasattr(cli, "_energy_eh")


def test_info_and_diff_json_keep_their_keys(tmp_path):
    energy = {"value": -76.0, "units": "hartree"}
    a = _water(tmp_path, "a.qvf", scf_energy=energy, scf_converged=True)
    b = _water(tmp_path, "b.qvf", scf_energy={"value": -75.5, "units": "hartree"})
    info = json.loads(CliRunner().invoke(main, ["info", str(a), "--json"]).output)
    for key in ("file", "program", "scf_energy_eh", "sections", "total_size", "archive_size"):
        assert key in info, key
    assert info["scf_energy_eh"] == -76.0 and info["sections"][0]["size"]
    diff = json.loads(CliRunner().invoke(main, ["diff", str(a), str(b), "--json"]).output)
    assert diff["file_a"] == "a.qvf" and diff["delta_e_eh"] == pytest.approx(-0.5)
    assert diff["converged_a"] is True and diff["converged_b"] is None
    text = CliRunner().invoke(main, ["diff", str(a), str(b)]).output
    assert "B is" in text and "higher than A" in text
    assert CliRunner().invoke(main, ["validate", str(a)]).output.startswith("a.qvf: OK")


# ── #39-26: the signed set is shared ────────────────────────────────────


def test_capture_uses_the_shared_signed_kind_set():
    from vibeview import capture

    # capture_volume wraps render_volume, which draws the surfaces (#60).
    assert "SIGNED_VOLUME_KINDS" in inspect.getsource(capture.render_volume)
    assert '"volume.difference",' not in inspect.getsource(capture)


# ── #39-17 / #39-41: animation frames ───────────────────────────────────


def test_turntable_uses_a_temporary_directory():
    from vibeview import animation

    source = inspect.getsource(animation.render_turntable)
    assert "TemporaryDirectory" in source and ".turntable_" not in source
    assert "plotter.camera_position = plotter.camera_position" not in source


def test_frame_sequences_reuse_one_plotter(tmp_path, monkeypatch):
    import vibeview.headless as headless
    from vibeview import animation

    made: list[object] = []
    original = headless.new_plotter

    def counting(*args, **kwargs):
        plotter = original(*args, **kwargs)
        made.append(plotter)
        return plotter

    monkeypatch.setattr(headless, "new_plotter", counting)
    monkeypatch.setattr(animation, "_encode_frames", lambda frame_dir, out, fmt, fps: out)
    builder = structure_archive(_WATER, (False, False, False), calculation="traj")
    coords = np.tile(np.array([a["position"] for a in _WATER]), (4, 1, 1))
    coords[:, 0, 0] = np.linspace(0.0, 0.3, 4)
    builder.add_json(
        "traj", "trajectory", "metadata",
        {"atoms": [{"symbol": a["symbol"], "atomic_number": a["atomic_number"]}
                   for a in _WATER], "energies": [0.0, 0.1, 0.2, 0.3]},
    )
    builder.add_binary("traj", "trajectory", "coords", coords)
    with QVFReader(builder.write(tmp_path / "traj.qvf")) as reader:
        animation.render_trajectory_video(reader, tmp_path / "t.mp4", size=(120, 90))
    assert len(made) == 1, "four frames, one plotter"


# ── #39-33: the dead desktop module is gone ─────────────────────────────


def test_dead_desktop_module_is_gone():
    assert not (_ROOT / "src" / "vibeview" / "desktop.py").exists()
    assert "desktop.py" not in (_ROOT / "pyproject.toml").read_text()


# ── #39-37 / #39-39: imports and re-reads ───────────────────────────────


def test_cli_imports_json_once_and_export_reads_the_structure_once(tmp_path):
    import vibeview.cli as cli

    source = inspect.getsource(cli)
    assert "import json as _json" not in source and "Step 7" not in source
    assert source.count("import threading") <= 1
    qvf = _water(tmp_path)
    calls = []
    original = QVFReader.read_structure

    def counting(self, *args, **kwargs):
        calls.append(args)
        return original(self, *args, **kwargs)

    QVFReader.read_structure = counting  # type: ignore[method-assign]
    try:
        assert CliRunner().invoke(main, ["export", str(qvf), "-f", "json"]).exit_code == 0
    finally:
        QVFReader.read_structure = original  # type: ignore[method-assign]
    assert len(calls) == 1


# ── #39-40: the download route streams ──────────────────────────────────


def test_serve_download_route_streams_from_disk():
    from vibeview import serve

    source = inspect.getsource(serve)
    assert "copyfileobj" in source
    assert "target.read_bytes()" not in source


# ── #39-42 / #39-47: manual and API surface ─────────────────────────────


def test_manual_says_dashboard_is_core():
    intro = (_ROOT / "docs" / "cli.md").read_text()[:1500]
    assert "`serve` and `desktop` need the browser" in intro


def test_api_declares_its_public_names():
    import vibeview.api as api

    assert set(api.__all__) >= {"info", "diff", "validate", "get_table", "slice_qvf"}
    for name in api.__all__:
        assert callable(getattr(api, name)), name
    leaked = {n for n in ("Path", "Any", "contextlib", "QVFReader", "Iterator")}
    assert not (set(api.__all__) & leaked)


# ── #39-48: commands without CliRunner coverage ─────────────────────────


def test_dashboard_and_supercell_run_from_the_cli(tmp_path, monkeypatch):
    import vibeview.dashboard as dashboard_mod

    written: list[str] = []
    monkeypatch.setattr(
        dashboard_mod, "render_dashboard",
        lambda paths, out, cols, cell_size: written.append(out) or out,
    )
    qvf = _water(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["dashboard", str(qvf)])
    assert result.exit_code == 0, result.output
    assert written and written[0].endswith("dashboard.png")

    result = CliRunner().invoke(main, ["supercell", str(_periodic(tmp_path)), "--nx", "2"])
    assert result.exit_code == 0, result.output
    with QVFReader(tmp_path / "na_2x1x1.qvf") as out:
        structure = out.read_structure()
        assert len(structure.atoms) == 2
        assert structure.lattice_vectors[0][0] == pytest.approx(8.0)
