"""Regressions for the infrastructure, test-suite and docs rows in #56.

Each test names the #41 finding it pins. The CI rows (15, 16, 34, 38) are in
``test_ci_rules.py`` and the changelog row (2) in
``test_changelog_fragments.py``, beside the machinery they check.
"""

from __future__ import annotations

import ast
import os
import re
import tempfile
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_TESTS = _ROOT / "tests"


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def _test_modules() -> list[Path]:
    return sorted(_TESTS.glob("test_*.py"))


# ── #41-7: the release checklist ──────────────────────────────────────────


def test_release_checklist_names_every_version_site():
    checklist = _read("docs/release_process.md")
    for site in (
        "pyproject.toml",
        "src/vibeview/__init__.py",
        "CITATION.cff",
        "electron/package.json",
        "electron/package-lock.json",
        "RELEASE_CODENAMES",
        "FALLBACK_CODENAME",
        "scripts/fold_changelog.py",
        "release-candidate/vX.Y.Z",
    ):
        assert site in checklist, site
    codenames = _read("docs/codenames.md")
    section = codenames[codenames.index("## Adding a name at release time") :]
    section = section[: section.index("\n## ", 3)]
    assert "release_process.md" in section
    assert "1. Add the" not in section  # the list lives in one place now


# ── #41-17 / #41-18: AGENTS.md and CONTRIBUTING.md facts ──────────────────


def test_agents_rules_match_the_repository():
    agents = _read("AGENTS.md")
    assert "DEPLOY_PATH" not in agents
    assert "external deployment include" in agents
    assert "about 100 files" not in agents
    extras = tomllib.loads(_read("pyproject.toml"))["project"]["optional-dependencies"]
    assert any(req.startswith("pyyaml") for req in extras["test"])
    assert "pytest, scipy and pyyaml" in _read("CONTRIBUTING.md")


# ── #41-19 / #41-24: no skip that hides a test where its dependency exists ─


def _skipif_conditions():
    for path in _test_modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and ast.unparse(node.func).endswith("skipif")
                and node.args
            ):
                yield path.name, node.lineno, ast.unparse(node.args[0])


def test_no_skip_is_true_when_the_dependency_is_present():
    trap = re.compile(r"find_spec\([^)]*\) is not None|^(HAVE|HAS|_HAVE|_HAS)_\w+$")
    offenders = [f"{name}:{line}: {cond}" for name, line, cond in _skipif_conditions()
                 if trap.search(cond)]
    assert not offenders, offenders
    contributing = _read("CONTRIBUTING.md")
    assert "seven** hits" not in contributing and "single hit" not in contributing


# ── #41-20 / #41-35: dangling monorepo references ─────────────────────────


def test_no_dangling_monorepo_references():
    assert "HANDOVER.md" not in _read("docs/conf.py")
    assert "AUDIT_2026" not in _read("tests/test_renderer_regressions.py")
    assert "vibe-view/tests/" not in _read("tests/test_benchmarks.py")
    assert ".gitignore:" not in _read("tests/conftest.py")
    assert "monorepo" not in _read("scripts/_lifecycle_lock.sh")


# ── #41-21 / #41-23: docs quote what the product says ─────────────────────


def test_readme_dashboard_line_matches_the_cli():
    from click.testing import CliRunner

    from vibeview.cli import main

    help_text = CliRunner().invoke(main, ["--help"]).output
    assert "Render a grid preview of multiple QVF files." in help_text
    readme = _read("README.md")
    assert "Multi-QVF web dashboard" not in readme
    assert "Grid preview of several QVF files" in readme


def test_manual_names_the_replication_button_as_labelled():
    from tests._sources import app_source

    assert '"Apply Replication"' in app_source()
    browser = _read("docs/browser.md")
    assert "**Apply Replication**" in browser and "**Apply replication**" not in browser


# ── #41-22 / #41-37: the Electron directory ───────────────────────────────


def test_legacy_launcher_is_gone_and_the_version_bump_is_documented():
    assert not (_ROOT / "electron" / "desktop-launcher.py").exists()
    publishing = _read("electron/PUBLISHING.md")
    assert "package-lock.json" in publishing and "docs/release_process.md" in publishing
    assert "PUBLISHING.md" in _read("electron/README.md")


# ── #41-25: every lane either runs in CI or is documented as local ────────


def test_smiles_runs_in_the_gate_and_local_lanes_are_listed():
    extras = tomllib.loads(_read("pyproject.toml"))["project"]["optional-dependencies"]
    assert "vibeview[smiles]" in extras["test"]
    contributing = _read("CONTRIBUTING.md")
    for lane in ("VIBEQC_RELOCALIZE_PYTHON", "Chrome or Chromium", "test_trexio_vibeqc_parity"):
        assert lane in contributing, lane


# ── #41-26: markers, and no wall clock in the gate ────────────────────────


def test_markers_are_strict_and_benchmarks_leave_the_gate():
    options = tomllib.loads(_read("pyproject.toml"))["tool"]["pytest"]["ini_options"]
    assert "--strict-markers" in options["addopts"]
    assert options["addopts"][-2:] == ["-m", "not benchmark"]
    assert any(marker.startswith("benchmark:") for marker in options["markers"])
    assert "pytestmark = pytest.mark.benchmark" in _read("tests/test_benchmarks.py")
    serve = _read("tests/test_serve.py")
    assert "time.monotonic() - t0 <" not in serve


# ── #41-27: one builder, one sample_qvf, no temp-file leaks ───────────────


def test_one_sample_qvf_fixture():
    defining = [
        p.name
        for p in _test_modules()
        if any(
            isinstance(n, ast.FunctionDef) and n.name == "sample_qvf"
            for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
        )
    ]
    assert defining == []
    assert "def sample_qvf(" in _read("tests/conftest.py")


def test_local_builders_delegate_to_the_shared_one():
    leaky = []
    for path in _test_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if (
                isinstance(node, ast.FunctionDef)
                and node.name.startswith("_make_qvf")
                and "NamedTemporaryFile" in ast.unparse(node)
            ):
                leaky.append(f"{path.name}:{node.name}")
    assert not leaky, leaky


def test_the_run_never_writes_into_the_account_or_system_temp(tmp_path_factory):
    base = tmp_path_factory.getbasetemp().resolve()
    assert Path(tempfile.gettempdir()).resolve().is_relative_to(base)
    for variable in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        assert Path(os.environ[variable]).resolve().is_relative_to(base), variable
    from vibeview.paths import settings_path

    assert settings_path().resolve().is_relative_to(base)


# ── #41-28: no machine-local layout ───────────────────────────────────────


def test_corpus_lookup_guesses_no_machine_layout():
    source = _read("tests/_qvf_corpus.py")
    assert "qvf-writer" not in source and "glob(" not in source
    serve = _read("tests/test_serve.py")
    assert "_find_qvfs(Path.home()" not in serve


# ── #41-29: source-text checks live in one place ──────────────────────────


_APP_SOURCE_READERS_ALLOWED = {
    "test_structural_contracts.py",
    # One of the seven files vibe-qc runs at its pinned VIBE_VIEW_TAG; changing
    # it needs that pin moved, so it keeps its one check for now.
    "test_details_panel_sandbox.py",
}


def _reads_app_source(tree: ast.AST) -> bool:
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases |= {a.asname or a.name for a in node.names if a.name == "vibeview.app"}
        elif isinstance(node, ast.ImportFrom) and node.module == "vibeview":
            aliases |= {a.asname or a.name for a in node.names if a.name == "app"}
        elif isinstance(node, ast.ImportFrom) and node.module == "vibeview.app":
            aliases |= {a.asname or a.name for a in node.names}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "inspect.getsource":
            root = node.args[0] if node.args else None
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in aliases:
                return True
    return False


def test_only_the_structural_contracts_read_the_app_source():
    offenders = [
        path.name
        for path in _test_modules()
        if path.name not in _APP_SOURCE_READERS_ALLOWED
        and _reads_app_source(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert not offenders, offenders


# ── #41-30: files are named for what they cover ───────────────────────────


@pytest.mark.parametrize(
    "pattern",
    # test_queue_extra is named for the [queue] extra, not a batch.
    [
        r"milestone",
        r"(?<!queue)_extra\b",
        r"_final\b",
        r"_v2\b",
        r"_v2_",
        r"^test_a\d+_",
        r"audit_\d{4}",
    ],
)
def test_no_milestone_named_test_files(pattern):
    names = [p.stem for p in _test_modules() if re.search(pattern, p.stem)]
    assert not names, names


# ── #41-31 / #41-36: the privacy hook's tests and allowlist ───────────────


def test_hook_regressions_are_pytest_and_the_allowlist_has_one_home():
    assert not (_ROOT / ".githooks" / "test_privacy_hook.py").exists()
    assert (_TESTS / "test_privacy_hook.py").is_file()
    assert "ALLOWED_USERS=" in _read(".githooks/pre-commit")
    assert "_ALLOWED_USERS = (" not in _read("tests/test_no_maintainer_paths.py")
    wrapper = "subprocess.run(\n        [sys.executable"
    assert wrapper not in _read("tests/test_no_maintainer_paths.py")


# ── #41-32: optional-extra gates stated once ──────────────────────────────


def test_textual_gate_is_module_level():
    tui = _read("tests/test_tui_app.py")
    assert tui.count('importorskip("textual"') == 1
    assert "\n    pytest.importorskip" not in tui
