"""``validate`` without the validator says NOT CHECKED, not INVALID (#108).

Strict validation lives in qvf's optional reference package. When it is not
installed, the CLI used to label every file ``INVALID`` and count it as
``FAILED``, which is a statement about the file; the file was never checked.
The manual's contract is an installation hint and an unsuccessful exit. The
API raises :class:`ValidationUnavailableError`, a ``QVFError``, so callers
can tell the two apart.
"""

from __future__ import annotations

import json
import sys

import pytest
from click.testing import CliRunner

from tests._qvf_builders import build_qvf
from vibeview import ValidationUnavailableError, validate
from vibeview.cli import main
from vibeview.qvf import QVFError

_STRUCTURE = json.dumps(
    {"atoms": [{"symbol": "He", "atomic_number": 2, "position": [0, 0, 0]}], "pbc": [False] * 3}
).encode()


def _archive(path):
    return build_qvf(
        path, [("structure", "structure", {"structure": ("json", "s.json", _STRUCTURE)})]
    )


@pytest.fixture
def no_validator(monkeypatch):
    """Force the absent branch, whether or not the extra is installed."""
    monkeypatch.setitem(sys.modules, "qvf_reader", None)


def test_the_api_raises_a_distinct_qvf_error(tmp_path, no_validator):
    with pytest.raises(ValidationUnavailableError) as caught:
        validate(_archive(tmp_path / "a.qvf"))
    assert isinstance(caught.value, QVFError)
    assert "Strict validation requires" in str(caught.value)
    assert "[validation]" in str(caught.value)


def test_the_cli_reports_not_checked_and_the_hint_once(tmp_path, no_validator):
    a = _archive(tmp_path / "a.qvf")
    b = _archive(tmp_path / "b.qvf")

    result = CliRunner().invoke(main, ["validate", str(a), str(b)])

    assert result.exit_code == 1  # unsuccessful: nothing was validated
    lines = result.output.splitlines()
    assert lines[0] == "a.qvf: NOT CHECKED"
    assert lines[1] == "b.qvf: NOT CHECKED"
    assert "0 OK, 0 FAILED, 2 not checked" in result.output
    assert "INVALID" not in result.output
    assert result.output.count("Strict validation requires") == 1
    assert "[validation]" in result.output


def test_a_checked_file_keeps_its_verdict(tmp_path):
    """With the validator present nothing changes: OK stays OK."""
    pytest.importorskip("qvf_reader", reason="the validation extra is not installed")
    result = CliRunner().invoke(main, ["validate", str(_archive(tmp_path / "a.qvf"))])
    assert result.exit_code == 0
    assert "OK (" in result.output
    assert "not checked" not in result.output
