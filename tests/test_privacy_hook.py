"""The staged-content privacy hook, exercised in disposable repositories.

Each case copies ``.githooks/`` into a fresh repository, stages one file and
runs the real ``pre-commit`` script. These were a unittest module inside
``.githooks/`` that the suite ran through a subprocess; as pytest tests they
report per case and share the suite's fixtures (#41-31).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._privacy_policy import allowed_users

HOOKS = Path(__file__).resolve().parent.parent / ".githooks"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def home_path(user: str) -> str:
    # Synthetic input assembled at runtime; no author's home path is stored.
    return "/" + "Users/" + user + "/example"


def check_staged(
    tmp_path: Path,
    content: str,
    expected: bool,
    policy: str | None = None,
    *,
    local: bool = False,
) -> subprocess.CompletedProcess[str]:
    repo = tmp_path / "repo"
    if repo.exists():
        shutil.rmtree(repo)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    shutil.copytree(HOOKS, repo / ".githooks", ignore=shutil.ignore_patterns("__pycache__"))
    (repo / "example.txt").write_text(content + "\n")
    subprocess.run(["git", "-C", str(repo), "add", "example.txt"], check=True)
    env = os.environ.copy()
    env.pop("VIBE_PRIVACY_TERMS_FILE", None)
    if policy is not None:
        if policy == "in-tree":
            policy_path = repo / "private-terms"
        else:
            policy_path = tmp_path / "repo.private-terms"
            policy_path.unlink(missing_ok=True)
        if policy != "missing":
            policy_path.write_text(policy)
        if local:
            cmd = ["git", "-C", str(repo), "config", "--local", "privacy.termsFile"]
            subprocess.run([*cmd, str(policy_path)], check=True)
        else:
            env["VIBE_PRIVACY_TERMS_FILE"] = str(policy_path)
    result = subprocess.run(
        ["sh", str(repo / ".githooks/pre-commit")],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is expected, result.stderr
    return result


def test_documented_placeholders_are_allowed(tmp_path):
    check_staged(tmp_path, " ".join(home_path(user) for user in allowed_users()), True)


@pytest.mark.parametrize(
    "content",
    [
        home_path("private-fixture"),
        home_path("PrivateFixture"),
        home_path("runner") + " " + home_path("private-fixture"),
        "+ " + home_path("private-fixture"),
        home_path("runner-private"),
    ],
    ids=["personal", "uppercase", "allowed-does-not-hide", "plus-line", "allowed-prefix"],
)
def test_personal_paths_are_blocked(tmp_path, content):
    check_staged(tmp_path, content, False)


def test_diagnostics_do_not_repeat_the_private_value(tmp_path):
    private = home_path("private-fixture")
    result = check_staged(tmp_path, private, False)
    assert private not in result.stderr
    assert "redacted" in result.stderr


@pytest.mark.skipif(
    "PRIVATE_IP_PATTERN=" not in (HOOKS / "pre-commit").read_text(),
    reason="no address rule in this repository",
)
@pytest.mark.parametrize("prefix", [(10, 0, 0), (192, 168, 1), (172, 31, 4)])
def test_rfc1918_addresses_are_not_example_exceptions(tmp_path, prefix):
    check_staged(tmp_path, ".".join(map(str, (*prefix, 9))), False)
    check_staged(tmp_path, "192.0.2.9 198.51.100.9 203.0.113.9", True)


def test_external_private_terms_are_blocked_and_redacted(tmp_path):
    term = "private-policy-fixture"
    result = check_staged(tmp_path, home_path("runner") + " " + term.upper(), False, term)
    assert term not in result.stderr.lower()
    check_staged(tmp_path, "portable example", True, term)


def test_clone_local_policy_uses_the_same_rules(tmp_path):
    check_staged(tmp_path, "private-policy-fixture", False, "private-policy-fixture", local=True)
    check_staged(tmp_path, "portable example", True, "private-policy-fixture", local=True)
    check_staged(tmp_path, "portable example", False, "", local=True)


@pytest.mark.parametrize("policy", ["missing", "", "in-tree"])
def test_missing_or_empty_configured_policy_fails_closed(tmp_path, policy):
    check_staged(tmp_path, "portable example", False, policy)
