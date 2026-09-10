"""The release codename catalogue, and the surfaces that must agree with it.

Before ``vibeview.codenames`` existed the codename was pasted into four
places by hand -- ``__init__.py``'s version comment, the CLI's ``--version``,
the browser About box and the Electron About box -- with a fifth copy
asserted in ``tests/test_setup_scripts.py``. Nothing coupled them, so a
rename that missed one would have shipped green.

These tests are the coupling. The resolution tests pin the semantics
(inheritance, dev-suffix stripping); the drift tests pin every surface to the
catalogue.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from vibeview import __version__
from vibeview.codenames import (
    RELEASE_CODENAMES,
    codename_for_current,
    codename_for_version,
    version_label,
)

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src" / "vibeview"


# ── Resolution semantics ──────────────────────────────────────────────────


def test_the_shipped_release_has_a_codename():
    """A release with no name would make every surface print a bare version.
    That is a supported state for an unnamed dev line, but not for a tag."""
    assert codename_for_current() is not None, (
        f"vibe-view {__version__} resolves to no codename. Add the "
        f"'{'.'.join(__version__.split('.')[:2])}.0' entry to RELEASE_CODENAMES."
    )


def test_a_patch_release_inherits_its_parent_minor():
    assert codename_for_version("2.15.0") == "Roothaan's Roadrunner"
    assert codename_for_version("2.15.2") == "Roothaan's Roadrunner"
    assert codename_for_version("2.15.99") == "Roothaan's Roadrunner"


@pytest.mark.parametrize(
    "version",
    ["2.15.0.dev0", "2.15.0.dev17", "2.15.0a1", "2.15.0b2", "2.15.0rc1"],
)
def test_a_prerelease_inherits_the_release_it_leads_up_to(version):
    """PEP-440 dev/alpha/beta/rc builds carry the upcoming release's name, so
    a dev banner is not anonymous for the whole cycle."""
    assert codename_for_version(version) == "Roothaan's Roadrunner"


def test_a_local_version_label_is_stripped():
    assert codename_for_version("2.15.2+local.1") == "Roothaan's Roadrunner"
    assert codename_for_version("2.15.0.dev1+g1234abc") == "Roothaan's Roadrunner"


def test_an_unregistered_minor_resolves_to_none():
    assert codename_for_version("99.99.99") is None
    assert codename_for_version("2.99.0") is None


def test_version_label_omits_the_dash_when_there_is_no_codename():
    """An unnamed line must not print `1.2.3 — ` with nothing after it."""
    assert version_label("99.99.99") == "99.99.99"
    assert version_label("2.15.2") == "2.15.2 — Roothaan's Roadrunner"


def test_every_catalogue_key_is_a_minor_or_patch_release():
    """A key like '2.15' or 'v2.15.0' would never match a resolved version and
    would sit in the catalogue looking registered while resolving nothing."""
    for key in RELEASE_CODENAMES:
        assert re.fullmatch(r"\d+\.\d+\.\d+", key), f"bad catalogue key: {key!r}"


def test_every_codename_uses_the_family_form():
    """"Person's Animal" is what makes a vibe-* release recognisable across
    the three products. Enforce the shape, not the pool."""
    for version, name in RELEASE_CODENAMES.items():
        assert re.fullmatch(r"[^']+'s [A-Z][a-z]+", name), (
            f"{version} -> {name!r} does not read as \"Person's Animal\""
        )


def test_the_catalogue_never_reuses_an_animal():
    animals = [name.rsplit(" ", 1)[1] for name in RELEASE_CODENAMES.values()]
    assert len(animals) == len(set(animals)), f"animal reused: {sorted(animals)}"


# ── The module must stay standard-library-only ────────────────────────────


def test_codenames_imports_nothing_outside_the_standard_library():
    """docs/conf.py loads this module BY PATH, outside the package, in a CI
    container where vibeview is not installed and pyvista does not exist. A
    module-scope import of anything from vibeview (or of a third-party
    package) breaks the documentation build, and the docs job is the only
    place that would notice."""
    tree = ast.parse((_SRC / "codenames.py").read_text(encoding="utf-8"))
    stdlib_only = {"re", "annotations", "__future__"}
    offenders = []
    for node in ast.walk(tree):
        # Only module-scope imports matter; the lazy ones inside functions are
        # deliberate and never run during a docs build.
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if node.col_offset != 0:
            continue
        names = (
            [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else [a.name for a in node.names]
        )
        offenders += [n for n in names if n.split(".")[0] not in stdlib_only]
    assert not offenders, (
        f"codenames.py imports {offenders} at module scope; docs/conf.py loads "
        "this file standalone and would fail to resolve them"
    )


# ── Drift: every surface must agree with the catalogue ────────────────────


def _current() -> str:
    name = codename_for_current()
    assert name is not None
    return name


def test_the_version_comment_in_init_names_the_current_codename():
    """A convenience comment, but a stale one is worse than none: it is the
    first thing a reader checks."""
    text = (_SRC / "__init__.py").read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if ln.startswith("__version__"))
    assert _current() in line, (
        f"__init__.py's __version__ comment does not name {_current()!r}: {line!r}"
    )


def test_the_cli_reads_the_catalogue_rather_than_a_literal():
    """`vibe-view --version` must resolve the name, not carry a copy."""
    text = (_SRC / "cli.py").read_text(encoding="utf-8")
    assert "from vibeview.codenames import version_label" in text
    assert 'click.echo(f"vibe-view {version_label()}")' in text
    assert _current() not in text, (
        "cli.py contains a literal codename; it must resolve one instead"
    )


def test_the_browser_about_box_reads_the_catalogue():
    text = (_SRC / "app.py").read_text(encoding="utf-8")
    assert 'server.state.about_version = f"vibe-view {version_label()}"' in text
    assert _current() not in text, (
        "app.py contains a literal codename; it must resolve one instead"
    )


def test_the_cli_actually_prints_version_and_codename():
    """The two source assertions above are structural. This one runs it."""
    from click.testing import CliRunner

    from vibeview import cli

    result = CliRunner().invoke(cli.main, ["--version"])
    assert result.exit_code == 0, result.output
    assert f"vibe-view {__version__} — {_current()}" in result.output


def test_the_desktop_handshake_config_carries_the_codename():
    """main.js cannot import Python, so the launcher hands it the name. This
    is the channel that keeps the desktop About box off a pasted string."""
    from vibeview import cli

    path = cli._write_desktop_config(8765, None)
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    finally:
        path.unlink(missing_ok=True)
    assert config["version"] == __version__
    assert config["codename"] == _current()


def test_the_electron_fallback_codename_matches_the_catalogue():
    """A double-click launch sets no handshake config, so main.js needs one
    literal. It is the only copy left, and it has to be the right one."""
    text = (_ROOT / "electron" / "main.js").read_text(encoding="utf-8")

    match = re.search(r'const FALLBACK_CODENAME = "([^"]+)";', text)
    assert match, "main.js no longer declares FALLBACK_CODENAME"
    assert match.group(1) == _current(), (
        f"main.js FALLBACK_CODENAME is {match.group(1)!r}, catalogue says "
        f"{_current()!r}"
    )

    # And the About box must use the config-or-fallback constant, not a
    # literal of its own.
    assert "const APP_CODENAME = CONFIG.codename || FALLBACK_CODENAME;" in text
    assert "message: `vibe-view ${APP_VERSION} — ${APP_CODENAME}`," in text
    assert text.count(_current()) == 1, (
        "main.js carries more than one copy of the codename; FALLBACK_CODENAME "
        "should be the only one"
    )


def test_the_changelog_names_the_codename_on_every_released_section():
    """A released section header carries `## [vX.Y.Z] - DATE - *Codename*`, so
    the CHANGELOG says which series a release belongs to. `[Unreleased]` has
    no codename yet and is exempt."""
    text = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    headers = re.findall(r"^## \[(?!Unreleased)([^\]]+)\](.*)$", text, re.MULTILINE)
    for version, rest in headers:
        expected = codename_for_version(version.lstrip("v"))
        if expected is None:
            continue
        assert expected in rest, (
            f"CHANGELOG section [{version}] does not name its codename "
            f"{expected!r}: {rest!r}"
        )


def test_the_docs_site_reads_the_same_catalogue():
    """docs/conf.py loads codenames.py by path so the site's {{codename}}
    substitution and the runtime surfaces cannot disagree. Assert it still
    loads the module rather than defining its own map."""
    text = (_ROOT / "docs" / "conf.py").read_text(encoding="utf-8")
    assert 'module_path = _REPO_ROOT / "src" / "vibeview" / "codenames.py"' in text
    assert "codename = _codename_lookup(release)" in text
    assert _current() not in text, (
        "docs/conf.py contains a literal codename; it must resolve one instead"
    )


def test_the_catalogue_table_in_the_docs_lists_every_registered_name():
    """docs/codenames.md carries the catalogue as a table a reader can scan.
    A release that added a name to codenames.py and forgot the row would
    publish a site claiming the release is unnamed."""
    text = (_ROOT / "docs" / "codenames.md").read_text(encoding="utf-8")
    _, _, after = text.partition("## The catalogue")
    table, _, _ = after.partition("## Proposed pool")
    missing = [
        f"{version} -> {name}"
        for version, name in RELEASE_CODENAMES.items()
        if version not in table or name not in table
    ]
    assert not missing, (
        "docs/codenames.md's catalogue table is missing: "
        + ", ".join(missing)
        + ". Add the row in the same commit that registers the name."
    )
