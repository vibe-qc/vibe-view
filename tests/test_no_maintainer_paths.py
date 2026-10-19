"""No absolute home paths or employer names in tracked content.

vibe-view is destined to be public, and an absolute path into a developer's
home directory left in a docstring, a fixture or a doc example ships into
every wheel. `.githooks/pre-commit` blocks new ones at commit time, but a hook
is opt-in (`git config --local core.hooksPath .githooks`) and git skips a
missing hooks directory silently — so this test is the half that always runs.

The allowlist is read from the hook itself (``tests/_privacy_policy.py``);
keep the path pattern in sync with it.
"""

from __future__ import annotations

import re
import runpy
import subprocess
from pathlib import Path

import pytest

from tests._privacy_policy import allowed_users

_ROOT = Path(__file__).resolve().parent.parent

# Placeholders and system usernames that are legitimate references: CI and
# system accounts, and the documented "user" placeholder. Defined once, as
# ALLOWED_USERS in .githooks/pre-commit; add sparingly, since each entry
# weakens the gate.
_ALLOWED_USERS = allowed_users()

_HOME_PATH = re.compile(r"/(?:Users|home)/([A-Za-z][A-Za-z0-9_.-]*)")
# Private names are configured externally; never publish the denylist itself.
_PRIVATE_TERMS = runpy.run_path(str(_ROOT / ".githooks/private_terms.py"))["load_terms"](_ROOT)


def _private_match(line: str) -> bool:
    return any(term in line.casefold() for term in _PRIVATE_TERMS)


# Meta-files where the patterns legitimately appear.
_EXEMPT = {
    ".githooks/pre-commit",
    ".mailmap",
    "tests/test_no_maintainer_paths.py",
}


def _tracked_text_files() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(_ROOT), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [p for p in out.stdout.split("\0") if p and p not in _EXEMPT]


def test_no_absolute_home_paths_in_tracked_content():
    offenders: list[str] = []
    for rel in _tracked_text_files():
        path = _ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # binary or unreadable: nothing to leak in review terms
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in _HOME_PATH.finditer(line):
                if match.group(1) in _ALLOWED_USERS:
                    continue
                offenders.append(f"{rel}:{lineno}: [redacted home path]")
    assert not offenders, (
        "absolute home paths in tracked content (use ~/, /home/USER/ or "
        "<vibe-view-checkout> instead):\n  " + "\n  ".join(offenders)
    )


def test_no_private_terms_in_tracked_content():
    offenders: list[str] = []
    for rel in _tracked_text_files():
        try:
            text = (_ROOT / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _private_match(line):
                offenders.append(f"{rel}:{lineno}")
    assert not offenders, "employer name in tracked content:\n  " + "\n  ".join(offenders)


def test_the_hook_exists_and_is_executable():
    """CONTRIBUTING.md tells contributors to enable it; it has to be there."""
    hook = _ROOT / ".githooks" / "pre-commit"
    assert hook.is_file(), "CONTRIBUTING.md documents .githooks/pre-commit"
    import os

    assert os.access(hook, os.X_OK), f"{hook} is not executable; git will skip it"


def test_the_allowlist_has_one_definition():
    """Two copies of a security allowlist that can drift is worse than one:
    the tests read the hook's list, and no tracked file restates it."""
    assert "root" in _ALLOWED_USERS and "USER" in _ALLOWED_USERS
    literal = '", "'.join(_ALLOWED_USERS)
    copies = [
        rel
        for rel in _tracked_text_files()
        if rel.endswith(".py")
        and (_ROOT / rel).is_file()  # tracked but deleted in the working tree
        and literal in (_ROOT / rel).read_text(encoding="utf-8")
    ]
    assert not copies, f"a second copy of ALLOWED_USERS: {copies}"


@pytest.mark.skipif(
    __import__("shutil").which("git") is None, reason="git is not installed"
)
@pytest.mark.parametrize("external_policy", [False, True])
def test_the_hook_rejects_a_home_path(tmp_path, monkeypatch, external_policy):
    """The guard has to actually fire, not merely exist."""
    monkeypatch.delenv("VIBE_PRIVACY_TERMS_FILE", raising=False)
    if external_policy:
        policy = tmp_path / "private-terms.txt"
        policy.write_text("synthetic-private-policy-term\n", encoding="utf-8")
        monkeypatch.setenv("VIBE_PRIVACY_TERMS_FILE", str(policy))
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(  # noqa: E731 - terse local helper
        ["git", "-C", str(repo), *a], capture_output=True, text=True, check=False
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    run("config", "user.email", "t@example.invalid")
    run("config", "user.name", "T")
    hooks = repo / ".githooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text(
        (_ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (hooks / "private_terms.py").write_text(
        (_ROOT / ".githooks" / "private_terms.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (hooks / "pre-commit").chmod(0o755)
    run("config", "core.hooksPath", ".githooks")

    # Construct a synthetic forbidden path so source export cannot sanitize the
    # negative control into an allowed placeholder. This is not an author path.
    forbidden_path = "/" + "Users" + "/" + "privacy_fixture_person/secret"
    (repo / "leak.py").write_text(f'PATH = "{forbidden_path}"\n', encoding="utf-8")
    run("add", "leak.py")
    blocked = run("commit", "-m", "leak")
    assert blocked.returncode != 0, "the hook let an absolute home path through"
    assert "personal-info patterns" in blocked.stderr

    (repo / "leak.py").write_text('PATH = "~/secret"\n', encoding="utf-8")
    run("add", "leak.py")
    allowed = run("commit", "-m", "no leak")
    assert allowed.returncode == 0, (
        f"the hook blocked a clean commit:\n{allowed.stdout}{allowed.stderr}"
    )
