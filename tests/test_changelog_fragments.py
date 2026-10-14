"""The changelog policy: fragments in changelog.d/, folded at release (#41-2).

The maintainer decided on 2026-09-25 that ``changelog.d/`` fragments are the
changelog policy. These tests keep the tree, the tool and the docs agreeing.
"""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_FOLD = runpy.run_path(str(_ROOT / "scripts" / "fold_changelog.py"))

_CHANGELOG = """# Changelog

Intro.

## [Unreleased]

### Fixed

- An existing entry.

## [v1.0.0] - 2026-01-01

### Added

- Old.
"""


def _tree(tmp_path: Path, fragments: dict[str, str], changelog: str = _CHANGELOG) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    directory = tmp_path / "changelog.d"
    directory.mkdir()
    for name, text in fragments.items():
        (directory / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_every_fragment_in_the_tree_is_foldable():
    fragments = _FOLD["read_fragments"](_ROOT / "changelog.d")
    changelog = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    folded = _FOLD["fold"](changelog, fragments)
    # Folding only adds to [Unreleased]; every released section is untouched.
    released = changelog[changelog.index("\n## [v") :]
    assert folded.endswith(released)


def test_fold_groups_orders_and_deletes(tmp_path):
    root = _tree(
        tmp_path,
        {
            "60-b.fixed.md": "Second fix. (#60)\n",
            "59-a.fixed.md": "First fix\nover two lines. (#59)\n",
            "61-new.added.md": "A new thing.\n\nWith a second paragraph.\n",
            "62-gone.removed.md": "Dropped the old flag.\n",
        },
    )
    assert _FOLD["main"](["--root", str(root)]) == 0
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = text[text.index("## [Unreleased]") : text.index("## [v1.0.0]")]
    assert unreleased == (
        "## [Unreleased]\n\n"
        "### Added\n\n- A new thing.\n\n  With a second paragraph.\n\n"
        "### Removed\n\n- Dropped the old flag.\n\n"
        "### Fixed\n\n- An existing entry.\n- First fix over two lines. (#59)\n"
        "- Second fix. (#60)\n\n"
    )
    assert text.endswith("### Added\n\n- Old.\n")
    assert not list((root / "changelog.d").iterdir())
    # Nothing left: a second run is a no-op.
    assert _FOLD["main"](["--root", str(root)]) == 0
    assert (root / "CHANGELOG.md").read_text(encoding="utf-8") == text


def test_long_entries_wrap_like_the_hand_written_ones(tmp_path):
    root = _tree(tmp_path, {"1-long.changed.md": "word " * 60})
    _FOLD["main"](["--root", str(root)])
    body = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    lines = body[body.index("### Changed") :].splitlines()[2:]
    bullet = lines[: lines.index("")]
    assert bullet[0].startswith("- ") and all(line.startswith("  ") for line in bullet[1:])
    assert max(map(len, bullet)) <= _FOLD["WIDTH"]


@pytest.mark.parametrize("name", ["no-type.md", "1-x.fix.md", "1-x.fixed.txt", "_x.fixed.md"])
def test_bad_fragment_names_fail_check(tmp_path, capsys, name):
    root = _tree(tmp_path, {name: "Text.\n"})
    assert _FOLD["main"](["--root", str(root), "--check"]) == 1
    assert name in capsys.readouterr().err
    assert (root / "changelog.d" / name).exists()


def test_empty_fragment_and_missing_heading_fail_without_changes(tmp_path, capsys):
    root = _tree(tmp_path, {"1-x.fixed.md": "   \n"})
    assert _FOLD["main"](["--root", str(root)]) == 1
    root2 = _tree(tmp_path / "b", {"1-x.fixed.md": "Text.\n"}, changelog="# Changelog\n")
    assert _FOLD["main"](["--root", str(root2)]) == 1
    assert (root2 / "changelog.d" / "1-x.fixed.md").exists()
    assert "Unreleased" in capsys.readouterr().err


def test_the_policy_is_documented_where_contributors_look():
    for rel in ("AGENTS.md", "CONTRIBUTING.md", "docs/release_process.md"):
        text = (_ROOT / rel).read_text(encoding="utf-8")
        assert "changelog.d/" in text, rel
    assert "fold_changelog.py" in (_ROOT / "docs" / "release_process.md").read_text()
