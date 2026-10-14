#!/usr/bin/env python3
"""Fold ``changelog.d/`` fragments into ``CHANGELOG.md``'s ``[Unreleased]``.

vibe-view records a user-visible change as one small file per change,
``changelog.d/<slug>.<type>.md``, so parallel merge requests never conflict
on ``CHANGELOG.md``. At release time this script moves every fragment into
the ``## [Unreleased]`` section as a bullet under its ``### <Type>`` heading
and deletes the fragment files. ``docs/release_process.md`` runs it before the
``[Unreleased]`` heading is renamed to the version.

``<type>`` is one of the Keep a Changelog headings: ``added``, ``changed``,
``deprecated``, ``removed``, ``fixed`` or ``security``. A fragment's text is
one paragraph, or several separated by blank lines; the first becomes the
bullet and the rest are indented under it.

Usage::

    python scripts/fold_changelog.py            # fold and delete fragments
    python scripts/fold_changelog.py --check    # validate names only
    python scripts/fold_changelog.py --dry-run  # print the new section
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Keep a Changelog order.
TYPES = ("added", "changed", "deprecated", "removed", "fixed", "security")

_NAME = re.compile(r"^(?P<slug>[A-Za-z0-9][A-Za-z0-9_.-]*)\.(?P<type>[a-z]+)\.md$")
_UNRELEASED = re.compile(r"^## \[Unreleased\][^\n]*\n", re.MULTILINE)
_NEXT_RELEASE = re.compile(r"^## ", re.MULTILINE)
_SUBHEADING = re.compile(r"^### (?P<name>.+?)\s*$", re.MULTILINE)
WIDTH = 78


class FragmentError(ValueError):
    """A fragment file whose name or content cannot be folded."""


@dataclass(frozen=True)
class Fragment:
    path: Path
    kind: str
    text: str

    def bullet(self) -> str:
        paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", self.text.strip())]
        wrap = textwrap.TextWrapper(
            width=WIDTH, break_long_words=False, break_on_hyphens=False
        )
        wrap.initial_indent, wrap.subsequent_indent = "- ", "  "
        lines = [wrap.fill(paragraphs[0])]
        wrap.initial_indent = "  "
        lines.extend("\n" + wrap.fill(p) for p in paragraphs[1:])
        return "\n".join(lines)


def read_fragments(directory: Path) -> list[Fragment]:
    """Every fragment in ``directory``, sorted by file name. Raises on a bad one."""
    fragments: list[Fragment] = []
    if not directory.is_dir():
        return fragments
    for path in sorted(directory.iterdir()):
        if path.name.startswith(".") or path.name == "README.md":
            continue
        match = _NAME.match(path.name)
        if match is None or match["type"] not in TYPES:
            raise FragmentError(
                f"{path.name}: expected <slug>.<type>.md with type one of {', '.join(TYPES)}"
            )
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            raise FragmentError(f"{path.name}: the fragment is empty")
        fragments.append(Fragment(path, match["type"], text))
    return fragments


def fold(changelog: str, fragments: list[Fragment]) -> str:
    """``changelog`` with the fragments added to its ``[Unreleased]`` section."""
    head = _UNRELEASED.search(changelog)
    if head is None:
        raise FragmentError("CHANGELOG.md has no '## [Unreleased]' heading")
    rest = changelog[head.end() :]
    nxt = _NEXT_RELEASE.search(rest)
    body, tail = (rest[: nxt.start()], rest[nxt.start() :]) if nxt else (rest, "")

    # Split the existing section into its preamble and ### subsections.
    marks = list(_SUBHEADING.finditer(body))
    preamble = body[: marks[0].start()] if marks else body
    sections: dict[str, list[str]] = {}
    order: list[str] = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        name = mark["name"].strip()
        order.append(name)
        sections[name] = [body[mark.end() : end].strip("\n")]
    for fragment in fragments:
        name = fragment.kind.capitalize()
        if name not in sections:
            sections[name] = []
            order.append(name)
        sections[name].append(fragment.bullet())

    rank = {t.capitalize(): i for i, t in enumerate(TYPES)}
    order.sort(key=lambda name: rank.get(name, len(rank)))
    parts = [preamble.strip("\n")] if preamble.strip() else []
    for name in order:
        entries = "\n".join(e for e in sections[name] if e)
        parts.append(f"### {name}\n\n{entries}")
    section = "\n\n".join(parts)
    return changelog[: head.end()] + "\n" + section + ("\n\n" if tail else "\n") + tail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="validate fragment names only")
    mode.add_argument("--dry-run", action="store_true", help="print, change nothing")
    args = parser.parse_args(argv)

    changelog_path = args.root / "CHANGELOG.md"
    try:
        fragments = read_fragments(args.root / "changelog.d")
        if args.check:
            print(f"{len(fragments)} fragment(s) OK")
            return 0
        if not fragments:
            print("no fragments to fold")
            return 0
        folded = fold(changelog_path.read_text(encoding="utf-8"), fragments)
    except FragmentError as exc:
        print(f"fold_changelog: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(folded[: folded.find("\n## ", folded.find("## [Unreleased]") + 1)])
        return 0
    changelog_path.write_text(folded, encoding="utf-8")
    for fragment in fragments:
        fragment.path.unlink()
    print(f"folded {len(fragments)} fragment(s) into [Unreleased]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
