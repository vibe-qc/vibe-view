"""vibe-view#64: open-time errors are clipped like every other error.

#63 bounded the last-resort report and ``_report_error`` at about 2 kB, but
the open path had its own copy of the echo in
:func:`vibeview.cli_common.open_reader_or_exit` and stayed unbounded. A
manifest whose ``qvf_version`` or section id is 200,000 characters long put
roughly 400 kB of schema error on the terminal, because the validator quotes
the offending value back. Measured before the fix on this archive: 400,254 B
on stderr from ``info``, 400,268 B from ``info --json``, 400,290 B from
``validate``, and 203,753 B for the section-id case.

The bound is shared now, so every report that quotes an archive string is
covered by one constant.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from tests._qvf_builders import write_archive
from vibeview.cli import main
from vibeview.cli_common import _MAX_ERROR_CHARS, clip_message

HUGE = 200_000
#: The clip adds a short note; allow for it plus the command's own framing.
BUDGET = _MAX_ERROR_CHARS + 500


@pytest.fixture
def huge_version_qvf(tmp_path):
    """An archive whose ``qvf_version`` is 200,000 characters."""
    return write_archive([], dest=tmp_path / "huge_version.qvf", qvf_version="9" * HUGE)


@pytest.fixture
def huge_section_id_qvf(tmp_path):
    """An archive whose section id is 200,000 characters, and has no ``kind``.

    The schema error quotes the whole section object, so the id reaches the
    message even though the id itself is not what is invalid.
    """
    return write_archive(
        [{"id": "s" * HUGE, "members": {}}], dest=tmp_path / "huge_section.qvf"
    )


class TestClipMessage:
    def test_short_message_is_untouched(self):
        assert clip_message("no such file: a.qvf") == "no such file: a.qvf"

    def test_a_message_at_the_bound_is_untouched(self):
        exactly = "x" * _MAX_ERROR_CHARS
        assert clip_message(exactly) == exactly

    def test_a_longer_message_is_clipped_and_says_how_much_is_hidden(self):
        clipped = clip_message("x" * (_MAX_ERROR_CHARS + 1234))
        assert len(clipped) < _MAX_ERROR_CHARS + 200
        assert "1,234 more characters" in clipped
        assert "--debug" in clipped


class TestOpenTimeErrorsAreClipped:
    """Every command that opens an archive reports within the bound."""

    def test_info_text(self, huge_version_qvf):
        result = CliRunner().invoke(main, ["info", str(huge_version_qvf)])
        assert result.exit_code == 1
        assert len(result.output) < BUDGET, f"{len(result.output)} B reached the terminal"
        assert "more characters" in result.output
        assert "--debug" in result.output

    def test_info_json_stays_parseable(self, huge_version_qvf):
        result = CliRunner().invoke(main, ["info", "--json", str(huge_version_qvf)])
        assert result.exit_code == 1
        assert len(result.output) < BUDGET
        payload = json.loads(result.output)
        assert len(payload["error"]) < BUDGET
        assert "more characters" in payload["error"]

    def test_validate(self, huge_version_qvf):
        result = CliRunner().invoke(main, ["validate", str(huge_version_qvf)])
        assert result.exit_code == 1
        assert len(result.output) < BUDGET
        assert "INVALID" in result.output

    def test_show(self, huge_version_qvf):
        result = CliRunner().invoke(main, ["show", str(huge_version_qvf)])
        assert result.exit_code == 1
        assert len(result.output) < BUDGET

    def test_a_huge_section_id_is_clipped_too(self, huge_section_id_qvf):
        result = CliRunner().invoke(main, ["info", str(huge_section_id_qvf)])
        assert result.exit_code == 1
        assert len(result.output) < BUDGET, f"{len(result.output)} B reached the terminal"

    def test_the_reason_survives_the_clip(self, huge_version_qvf):
        """Clipping keeps the head of the message, which is what names the fault."""
        result = CliRunner().invoke(main, ["info", str(huge_version_qvf)])
        assert "Error:" in result.output
        assert result.output.index("Error:") < 200
