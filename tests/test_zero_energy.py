"""A physical zero and absent energy metadata have different meanings."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from vibeview.api import diff
from vibeview.cli import main
from vibeview.serve import _browse_dir, _view_page


def energy_archive(path: Path, energy: float | None) -> Path:
    provenance = {} if energy is None else {"scf_energy": {"value": energy, "units": "Eh"}}
    manifest = {
        "qvf_version": 1,
        "source": {"program": "test", "version": "1", "calculation": "energy"},
        "sections": [],
        "provenance": provenance,
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
    return path


@pytest.mark.parametrize(("a", "b"), [(0.0, 0.0), (-1.0, -1.0), (0.0, -1.0), (None, -1.0)])
def test_cli_diff_matches_api_for_zero_and_missing_energy(tmp_path, a, b) -> None:
    qa = energy_archive(tmp_path / "a.qvf", a)
    qb = energy_archive(tmp_path / "b.qvf", b)
    result = CliRunner().invoke(main, ["diff", str(qa), str(qb), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    expected = diff(qa, qb)
    for key in ("energy_a_eh", "energy_b_eh", "delta_e_eh", "delta_e_ev", "delta_e_kcal_mol"):
        assert payload[key] == expected[key]
    if a is None:
        assert payload["delta_e_ev"] is None
    elif a == b:
        assert payload["delta_e_eh"] == payload["delta_e_ev"] == payload["delta_e_kcal_mol"] == 0.0


def test_cli_prints_equal_energy_delta(tmp_path) -> None:
    q = energy_archive(tmp_path / "zero.qvf", 0.0)
    result = CliRunner().invoke(main, ["diff", str(q), str(q)])
    assert result.exit_code == 0, result.output
    assert "Delta (A-B)" in result.output
    assert "+0.0000000000 Eh" in result.output
    assert "equal energy" in result.output
    assert "higher than" not in result.output
    assert "lower than" not in result.output


@pytest.mark.parametrize("energy", [0.0, -1.25, None])
def test_file_pages_distinguish_zero_from_missing(tmp_path, energy) -> None:
    q = energy_archive(tmp_path / "energy.qvf", energy)
    listing = _browse_dir(tmp_path)
    details = _view_page(q.name, tmp_path)
    if energy is None:
        assert "<td>--</td>" in listing
        assert "N/A" in details
    else:
        assert f"<td>{energy:.8f}</td>" in listing
        assert f"{energy:.8f} Eh" in details
