"""Tool requirements belong in package extras, consumed by their CI jobs."""

from __future__ import annotations

import re
import shlex
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTRA_JOBS = [("docs", ".docs_deps"), ("release", ".test_env"), ("dev", ".lint_env")]


def _name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9_.-]*", requirement)
    return re.sub(r"[-_.]+", "-", match[0]).lower() if match else ""


def _check_constraints(extra: str, requirements: list[str]) -> None:
    for requirement in requirements:
        if _name(requirement) == "vibeview":
            continue
        if extra == "dev":
            assert re.fullmatch(r"[A-Za-z0-9_.-]+==[0-9][A-Za-z0-9.!+_-]*", requirement), (
                f"dev tool must have an exact version: {requirement}"
            )
        else:
            assert re.search(r"[<>=~]", requirement), f"unconstrained {extra} tool: {requirement}"


def _install_arguments(command: str) -> list[str]:
    # Recognize current CI installer forms, not arbitrary shell programs.
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    tokens = list(lexer)
    for prefix in (["pip", "install"], ["python", "-m", "pip", "install"]):
        if tokens[:len(prefix)] == prefix:
            assert not any(token and set(token) <= set(";&|") for token in tokens), (
                "unsupported compound CI installer command"
            )
            return tokens[len(prefix):]
    return []


def _commands(job: dict) -> list[str]:
    return [command for key in ("before_script", "script", "after_script")
            for command in job.get(key, []) if isinstance(command, str)]


def _check_ci_extra(document: dict, extra: str, job_name: str, requirements: list[str]) -> None:
    installs_extra = False
    for command in _commands(document[job_name]):
        arguments = _install_arguments(command)
        for option, argument in zip(arguments, arguments[1:], strict=False):
            if option not in {"-e", "--editable"}:
                continue
            match = re.fullmatch(r"\.\[([^]]+)\]", argument)
            if match and extra in match[1].split(","):
                installs_extra = True
    assert installs_extra, f"{job_name} must install the local [{extra}] extra"

    tools = {_name(requirement) for requirement in requirements} - {"vibeview"}
    for job in document.values():
        if not isinstance(job, dict):
            continue
        for command in _commands(job):
            duplicates = [arg for arg in _install_arguments(command) if _name(arg) in tools]
            assert not duplicates, f"CI repeats [{extra}] requirements: {duplicates}"


@pytest.mark.parametrize("extra,job_name", EXTRA_JOBS)
def test_tool_extra_declares_versions(extra, job_name):
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    _check_constraints(extra, config["project"]["optional-dependencies"][extra])


@pytest.mark.parametrize("extra,job_name", EXTRA_JOBS)
def test_ci_installs_tool_extra_without_repeating_requirements(extra, job_name):
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    # BaseLoader reads strings/lists only, including GitLab !reference values.
    document = yaml.load((ROOT / ".gitlab-ci.yml").read_text(), Loader=yaml.BaseLoader)
    _check_ci_extra(document, extra, job_name, config["project"]["optional-dependencies"][extra])


@pytest.mark.parametrize("extra,job_name", EXTRA_JOBS)
@pytest.mark.parametrize("broken", [
    "missing", "echo", "wrong-job", "bare", "lower-bound", "exact",
    "and-echo", "semicolon-echo", "log-option",
])
def test_extra_guard_rejects_broken_ci_coupling(extra, job_name, broken):
    install = f"pip install --quiet -e '.[test,{extra}]'"
    document = {job_name: {"before_script": [install]}, "other": {"script": []}}
    if broken == "missing":
        document[job_name]["before_script"] = ["pip install -e ."]
    elif broken == "echo":
        document[job_name]["before_script"] = [f'echo "{install}"']
    elif broken == "wrong-job":
        document[job_name]["before_script"] = []
        document["other"]["script"] = [install]
    elif broken in {"and-echo", "semicolon-echo", "log-option"}:
        command = {
            "and-echo": f'pip install wheel && echo ".[{extra}]"',
            "semicolon-echo": f'pip install wheel; echo ".[{extra}]"',
            "log-option": f'pip install wheel --log ".[{extra}]"',
        }[broken]
        document[job_name]["before_script"] = [command]
    else:
        requirement = {
            "bare": "sample-tool", "lower-bound": "sample-tool>=1", "exact": "sample-tool==1",
        }[broken]
        document["other"]["script"] = [f'python -m pip install "{requirement}"']
    with pytest.raises(AssertionError, match="must install|repeats|unsupported compound"):
        _check_ci_extra(document, extra, job_name, ["sample-tool==1"])


@pytest.mark.parametrize("installer", ["pip", "python -m pip"])
@pytest.mark.parametrize("editable", ["-e", "--editable"])
@pytest.mark.parametrize("extra,job_name", EXTRA_JOBS)
def test_extra_guard_accepts_current_install_forms(installer, editable, extra, job_name):
    command = f"{installer} install --quiet {editable} '.[viewer,{extra}]'"
    document = {job_name: {"before_script": [command]}}
    _check_ci_extra(document, extra, job_name, ["sample-tool==1"])


@pytest.mark.parametrize("requirement", [
    "ruff", "mypy>=1", "ruff~=1.0", "ruff==1.*", "mypy==1,<2", "mypy===1", "ruff==latest",
])
def test_dev_guard_rejects_floating_versions(requirement):
    with pytest.raises(AssertionError, match="exact version"):
        _check_constraints("dev", [requirement])


@pytest.mark.parametrize("extra,requirements", [
    ("dev", ["ruff==1.0", "mypy==2.0"]),
    ("docs", ["vibeview", "sphinx>=7.4"]),
    ("release", ["build>=1.2,<2", "twine>=6,<7"]),
])
def test_version_guard_preserves_each_extra_policy(extra, requirements):
    _check_constraints(extra, requirements)


# ── #55 (#41-14): the `all` extra is composed, complete and documented ──────

# Extras that are not runtime capabilities, or cannot be in `all`: `queue`
# and `sketch` are published on no index (docs/queue.md, docs/editing.md),
# the rest are tooling.
_NOT_IN_ALL = {"all", "queue", "sketch", "test", "dev", "release", "docs"}


def _extras() -> dict[str, list[str]]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]


def test_all_is_composed_of_named_extras_only():
    """#41-14: `all` spelled Jupyter's packages literally and added a
    `psutil` no extra declared. Every entry must name an extra, so a
    capability is declared once and `all` cannot drift from it."""
    extras = _extras()
    for requirement in extras["all"]:
        match = re.fullmatch(r"vibeview\[([a-z0-9_-]+)\]", requirement)
        assert match, f"`all` lists a bare requirement: {requirement!r}"
        assert match[1] in extras, f"`all` names an undefined extra: {requirement!r}"


def test_all_covers_every_runtime_extra_with_requirements():
    extras = _extras()
    named = {m[1] for r in extras["all"] if (m := re.fullmatch(r"vibeview\[(.+)\]", r))}
    runtime = {name for name, reqs in extras.items() if reqs and name not in _NOT_IN_ALL}
    assert named == runtime


def test_every_optional_import_of_psutil_is_a_declared_extra():
    """#41-14: `app.py` imports psutil for the About dialog."""
    extras = _extras()
    owners = [name for name, reqs in extras.items()
              if name != "all" and any(_name(r) == "psutil" for r in reqs)]
    assert owners, "psutil is imported by the viewer but declared by no extra"


def test_every_extra_is_documented_including_the_empty_ones():
    """#41-14: `capture`, `povray` and `blender` install nothing, and the
    extras table did not say so."""
    table = (ROOT / "docs" / "installation.md").read_text()
    for name in _extras():
        if name in {"test", "dev", "release", "docs"}:
            continue
        assert re.search(rf"^\| `{re.escape(name)}` \|", table, re.M), (
            f"extra [{name}] is missing from the docs/installation.md extras table"
        )


class TestViewerExtraGuard:
    def test_load_app_missing_viewer_gives_hint(self, monkeypatch):
        """Interactive commands import vibeview.app (needs [viewer]); a lean
        capture-only install must fail with an actionable hint, not a raw
        ModuleNotFoundError."""
        import sys

        import pytest

        from vibeview.cli import _load_app

        monkeypatch.setitem(sys.modules, "vibeview.app", None)  # -> ImportError on `from`
        with pytest.raises(SystemExit) as ei:
            _load_app()
        assert "viewer" in str(ei.value) and "pip install" in str(ei.value)
