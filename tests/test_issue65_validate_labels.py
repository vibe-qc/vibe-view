"""vibe-view#65: ``validate`` names a layout error as one, not as a hash mismatch.

#63 added a check that a binary member's bytes fill its declared dtype and
shape. The CLI reported every member failure as ``SHA-256 MISMATCH``, so a
member whose hash is perfectly fine read as corrupt. The API now says which
check failed (``reason``) and the CLI labels each accordingly.
"""

from __future__ import annotations

import json

import numpy as np
from click.testing import CliRunner

from tests._qvf_builders import build_qvf, sha256
from vibeview.api import validate
from vibeview.cli import main

_STRUCTURE = json.dumps(
    {"atoms": [{"symbol": "He", "atomic_number": 2, "position": [0, 0, 0]}], "pbc": [False] * 3}
).encode()
_THREE = np.array([-0.8, 0.4, 0.4], dtype="<f8").tobytes()


def _archive(tmp_path, charge_member):
    return build_qvf(
        tmp_path / "a.qvf",
        [
            ("structure", "structure", {"structure": ("json", "s.json", _STRUCTURE)}),
            ("props", "atom_properties", {"mulliken_charge": charge_member}),
        ],
    )


def test_a_layout_error_is_labelled_as_one(tmp_path):
    # The hash is right; the declared shape asks for 10**12 values.
    path = _archive(tmp_path, ("binary", "p/m.bin", _THREE, "float64", [10**12]))

    report = validate(path)
    assert report["valid"] is False
    assert report["reason"] == "layout"
    assert (report["section"], report["member"]) == ("props", "mulliken_charge")

    result = CliRunner().invoke(main, ["validate", str(path)])
    assert result.exit_code == 1
    assert "LAYOUT ERROR in props/mulliken_charge" in result.output
    assert "SHA-256 MISMATCH" not in result.output


def test_a_hash_mismatch_keeps_its_label(tmp_path):
    # A correct layout whose recorded digest belongs to other bytes.
    member = {
        "path": "p/m.bin",
        "format": "binary",
        "dtype": "float64",
        "shape": [3],
        "sha256": sha256(b"something else"),
        "_bytes": _THREE,
    }
    path = _archive(tmp_path, member)

    report = validate(path)
    assert report["valid"] is False
    assert report["reason"] == "sha256"
    assert (report["section"], report["member"]) == ("props", "mulliken_charge")

    result = CliRunner().invoke(main, ["validate", str(path)])
    assert result.exit_code == 1
    assert "SHA-256 MISMATCH in props/mulliken_charge" in result.output
    assert "LAYOUT ERROR" not in result.output


def test_a_consistent_member_is_ok(tmp_path):
    path = _archive(tmp_path, ("binary", "p/m.bin", _THREE, "float64", [3]))
    assert validate(path)["valid"] is True
    result = CliRunner().invoke(main, ["validate", str(path)])
    assert result.exit_code == 0
    assert "OK (" in result.output
