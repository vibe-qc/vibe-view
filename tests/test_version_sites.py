"""Release metadata agrees across Python, desktop, citation and release notes."""

from __future__ import annotations

import ast
import datetime as dt
import json
import re
import tomllib
from pathlib import Path

import yaml

from vibeview.codenames import codename_for_version

ROOT = Path(__file__).resolve().parents[1]


def _release_headings() -> list[tuple[str, dt.date]]:
    text = (ROOT / "CHANGELOG.md").read_text()
    return [
        (version, dt.date.fromisoformat(date))
        for version, date in re.findall(
            r"^## \[(v\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})", text, re.MULTILINE
        )
    ]


def test_all_version_sites_and_citation_date_agree() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    package = json.loads((ROOT / "electron/package.json").read_text())
    lock = json.loads((ROOT / "electron/package-lock.json").read_text())
    citation = yaml.safe_load((ROOT / "CITATION.cff").read_text())
    init = ast.parse((ROOT / "src/vibeview/__init__.py").read_text())
    runtime_version = next(
        ast.literal_eval(node.value)
        for node in init.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets)
    )
    expected = project["version"]
    assert {
        "runtime": runtime_version,
        "desktop": package["version"],
        "lock": lock["version"],
        "lock root": lock["packages"][""]["version"],
        "citation": citation["version"],
    } == dict.fromkeys(("runtime", "desktop", "lock", "lock root", "citation"), expected)
    latest_version, latest_date = _release_headings()[0]
    assert latest_version == f"v{expected}"
    assert dt.date.fromisoformat(str(citation["date-released"])) == latest_date
    assert codename_for_version(expected) is not None


def test_release_headings_are_unique_ordered_and_linked() -> None:
    text = (ROOT / "CHANGELOG.md").read_text()
    labels = re.findall(r"^## \[([^\]]+)\]", text, re.MULTILINE)
    assert labels[0] == "Unreleased"
    assert labels.count("Unreleased") == 1
    assert len(labels) == len(set(labels)), "duplicate release headings"
    assert all(re.fullmatch(r"v\d+\.\d+\.\d+", label) for label in labels[1:])
    releases = _release_headings()
    assert len(releases) == len(labels) - 1, "each release needs an ISO date"
    versions = [tuple(map(int, version[1:].split("."))) for version, _ in releases]
    assert versions == sorted(set(versions), reverse=True)
    dates = [date for _, date in releases]
    # Two versions can ship on the same day (v2.15.3 and v2.16.0 did).
    assert dates == sorted(dates, reverse=True)
    references = re.findall(r"^\[([^\]]+)\]: (\S+)$", text, re.MULTILINE)
    assert len(references) == len(dict(references)), "duplicate link definitions"
    assert set(labels) <= dict(references).keys(), "unresolved release links"


def test_changelog_uses_standard_change_categories() -> None:
    text = (ROOT / "CHANGELOG.md").read_text()
    allowed = {"Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"}
    for section in re.split(r"^## \[", text, flags=re.MULTILINE)[1:]:
        categories = re.findall(r"^### (.+)$", section, re.MULTILINE)
        assert set(categories) <= allowed
        assert len(categories) == len(set(categories)), "duplicate category in one release"
