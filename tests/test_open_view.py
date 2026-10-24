"""``vibe-view open --view/--mode/--orbital`` opens on a chosen result view (#141)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from click.testing import CliRunner

from tests._qvf_builders import build_qvf
from vibeview import cli
from vibeview.app.helpers import _orbital_row
from vibeview.qvf import QVFReader


def _rows(spin: str, n: int, homo: int) -> list[dict[str, Any]]:
    marker = {homo: " HOMO", homo + 1: " LUMO"}
    return [{"index": i, "spin": spin, "value": f"{spin}:{i}",
             "title": f"#{i}  occ 1.00{marker.get(i, '')}"} for i in range(n)]


@pytest.mark.parametrize("label,value", [
    ("HOMO", "restricted:2"), ("homo-1", "restricted:1"), ("LUMO", "restricted:3"),
    ("LUMO+1", "restricted:4"), ("#0", "restricted:0"), ("4", "restricted:4"),
    ("HOMO-5", None), ("12", None), ("sigma", None),
])
def test_orbital_labels_resolve_against_the_picker_rows(label, value):
    row = _orbital_row(_rows("restricted", 5, homo=2), label)
    assert (row["value"] if row else None) == value


def test_orbital_spin_prefix_picks_the_block():
    rows = _rows("alpha", 5, homo=2) + _rows("beta", 5, homo=1)
    assert _orbital_row(rows, "HOMO")["value"] == "alpha:2"
    assert _orbital_row(rows, "beta:HOMO")["value"] == "beta:1"
    assert _orbital_row(rows, "beta:#4")["value"] == "beta:4"


def test_mode_view_selects_the_numbered_mode(tables_qvf):
    from vibeview.app import create_app

    app = create_app(QVFReader(tables_qvf))
    assert app.controller.open_view(view="mode", mode=3) == []
    assert app.state.selected_section == "vib0"
    assert app.state.vibration_mode == 2

    warnings = app.controller.open_view(view="mode", mode=99)
    assert warnings == ["no mode 99 (this file has modes 1-6)"]


def test_orbitals_view_renders_the_named_mo(tables_qvf):
    from vibeview.app import create_app

    app = create_app(QVFReader(tables_qvf))
    assert app.controller.open_view(view="orbitals", orbital="HOMO") == []
    assert app.state.selected_section == "wf"
    assert app.state.wf_selected_mo == "restricted:0"
    assert app.state.mo_last_index == 0  # the render committed
    [warning] = app.controller.open_view(view="orbitals", orbital="LUMO")
    assert warning.startswith("no orbital 'LUMO' in this wavefunction")


def test_missing_view_and_misplaced_options_warn(tables_qvf):
    from vibeview.app import create_app

    app = create_app(QVFReader(tables_qvf))
    assert app.controller.open_view(view="spectrum") == [
        "no spectrum section in this file (spectra.)"
    ]
    assert app.controller.open_view(section="structure", mode=1) == [
        "--mode 1 needs a vibrations section"
    ]
    assert app.state.selected_section == "structure"
    [warning] = app.controller.open_view(section="nope")
    assert warning.startswith("no section 'nope' in this file (have: structure,")


def test_precomputed_orbital_grids_are_named_by_section(tmp_path):
    from vibeview.app import create_app

    grid = json.dumps({"origin": [-1.0] * 3, "shape": [8, 8, 8],
                       "voxel_vectors": [[.25, 0, 0], [0, .25, 0], [0, 0, .25]]}).encode()
    data = np.random.default_rng(0).standard_normal((8, 8, 8)).astype(np.float32).tobytes()
    structure = json.dumps({"atoms": [{"symbol": "H", "atomic_number": 1,
                                       "position": [0.0, 0.0, 0.0]}],
                            "pbc": [False] * 3}).encode()

    def orbital(sid):
        return (sid, "volume.orbital", {
            "grid": ("json", f"{sid}/grid.json", grid),
            "data": ("binary", f"{sid}/data.bin", data, "float32", [8, 8, 8]),
        })

    path = build_qvf(tmp_path / "mos.qvf", [
        ("structure", "structure", {"structure": ("json", "s.json", structure)}),
        orbital("homo"), orbital("lumo"),
    ])
    app = create_app(QVFReader(path))
    assert app.controller.open_view(view="orbitals", orbital="LUMO") == []
    assert app.state.selected_section == "lumo"
    assert app.state.selected_orbital == "lumo"
    [warning] = app.controller.open_view(view="orbitals", orbital="sigma")
    assert warning == "no orbital 'sigma' (have: homo, lumo)"


def test_cli_open_reports_the_view_and_its_warnings(tables_qvf, tmp_path, monkeypatch):
    import vibeview.recent as recent

    monkeypatch.setattr(recent, "add_recent", lambda p: None)
    monkeypatch.setattr(cli, "_abort_if_port_in_use", lambda *a: None)
    monkeypatch.setattr(cli, "_announce_when_bound", lambda *a, **k: None)
    create_app, _ = cli._load_app()
    apps = []

    def keep(readers):
        apps.append(create_app(readers))
        return apps[-1]

    monkeypatch.setattr(cli, "_load_app", lambda: (keep, lambda *a, **k: None))
    result = CliRunner().invoke(cli.main, [
        "open", str(tables_qvf), "--view", "mode", "--mode", "2", "--orbital", "HOMO",
        "--no-browser", "--log-file", str(tmp_path / "v.log"),
    ])
    assert result.exit_code == 0, result.output
    assert "Auto-activating section: vib0" in result.output
    assert "Warning: --orbital HOMO needs a wavefunction or orbital section." in result.output
    assert apps[0].state.vibration_mode == 1

    rejected = CliRunner().invoke(cli.main, ["open", str(tables_qvf), "--mode", "0"])
    assert rejected.exit_code != 0


def test_cli_section_option_keeps_its_warning(tables_qvf, tmp_path, monkeypatch):
    """``-s`` now goes through open_view; its missing-section warning is unchanged."""
    import vibeview.recent as recent

    monkeypatch.setattr(recent, "add_recent", lambda p: None)
    monkeypatch.setattr(cli, "_abort_if_port_in_use", lambda *a: None)
    monkeypatch.setattr(cli, "_announce_when_bound", lambda *a, **k: None)
    create_app, _ = cli._load_app()
    monkeypatch.setattr(cli, "_load_app", lambda: (create_app, lambda *a, **k: None))
    result = CliRunner().invoke(cli.main, [
        "open", str(Path(tables_qvf)), "-s", "nope",
        "--no-browser", "--log-file", str(tmp_path / "v.log"),
    ])
    assert result.exit_code == 0, result.output
    assert "Warning: no section 'nope' in this file (have: structure," in result.output
    assert "Auto-activating" not in result.output
