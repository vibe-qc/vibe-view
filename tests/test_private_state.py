"""External launcher-ledger storage and verified legacy migration."""

from __future__ import annotations

import importlib.util
import os
import stat
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "viewer_private_state", Path(__file__).resolve().parents[1] / "scripts/_private_state.py"
)
state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(state)


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "viewer"
    root.mkdir()
    (root / ".git").mkdir()
    monkeypatch.setenv("VIBE_PRIVATE_ROOT", str(tmp_path / "private"))
    return root


def test_external_record_migrates_exact_bytes_with_private_modes(checkout):
    legacy = checkout / ".vibe-view-bin-links"
    data = b"/opt/example-tools\n/opt/another-tools\n"
    legacy.write_bytes(data)
    target = state.resolve_record(checkout)
    assert target.read_bytes() == data
    assert not legacy.exists()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    for parent in [target.parent, *list(target.parents)[1:4]]:
        assert stat.S_IMODE(parent.stat().st_mode) == 0o700


def test_dry_run_does_not_move_or_create(checkout):
    legacy = checkout / ".vibe-view-bin-links"
    legacy.write_bytes(b"/opt/example-tools\n")
    target = state.record_path(checkout)
    assert state.resolve_record(checkout, dry_run=True) == legacy
    assert legacy.read_bytes() == b"/opt/example-tools\n"
    assert not target.parent.exists()


def test_merge_keeps_both_external_and_legacy_directories(checkout):
    target = state.record_path(checkout, create=True)
    target.write_bytes(b"/opt/existing-tools\n")
    target.chmod(0o600)
    legacy = checkout / ".vibe-view-bin-links"
    legacy.write_bytes(b"/opt/legacy-tools\n")
    assert state.resolve_record(checkout) == target
    assert set(target.read_bytes().splitlines()) >= {b"/opt/existing-tools", b"/opt/legacy-tools"}
    assert not legacy.exists()


@pytest.mark.parametrize("value", ["", "relative"])
def test_invalid_explicit_root_fails(checkout, monkeypatch, value):
    monkeypatch.setenv("VIBE_PRIVATE_ROOT", value)
    with pytest.raises(ValueError):
        state.record_path(checkout)


@pytest.mark.parametrize("kind", ["checkout", "linked", "bare", "object-store", "symlink"])
def test_root_in_any_git_storage_is_refused(checkout, tmp_path, monkeypatch, kind):
    other = tmp_path / "other"
    other.mkdir()
    if kind == "checkout":
        (other / ".git").mkdir()
    elif kind == "linked":
        (other / ".git").write_text("gitdir: external-store\n")
    elif kind == "bare":
        (other / "HEAD").write_text("ref: refs/heads/main\n")
        (other / "objects").mkdir()
        (other / "refs").mkdir()
    elif kind == "object-store":
        other = other / ".git"
        other.mkdir()
    else:
        alias = other / "alias"
        alias.symlink_to(checkout, target_is_directory=True)
        other = alias
    monkeypatch.setenv("VIBE_PRIVATE_ROOT", str(other / "private"))
    with pytest.raises(ValueError):
        state.record_path(checkout, create=True)
    assert not (other / "private").exists()


def test_symlink_out_of_checkout_is_not_a_private_interface(checkout, tmp_path, monkeypatch):
    external = tmp_path / "external"
    external.mkdir(mode=0o700)
    link = checkout / "private-link"
    link.symlink_to(external, target_is_directory=True)
    monkeypatch.setenv("VIBE_PRIVATE_ROOT", str(link / "state"))
    with pytest.raises(ValueError):
        state.record_path(checkout)


def test_xdg_default_and_home_fallback(checkout, tmp_path, monkeypatch):
    monkeypatch.delenv("VIBE_PRIVATE_ROOT")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert state.record_path(checkout).is_relative_to(tmp_path / "xdg/vibe-private")
    monkeypatch.setenv("XDG_STATE_HOME", "relative")
    with pytest.raises(ValueError):
        state.record_path(checkout)
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert state.record_path(checkout).is_relative_to(tmp_path / "home/.local/state/vibe-private")


def test_unsafe_existing_store_is_not_adopted(checkout, monkeypatch):
    root = Path(os.environ["VIBE_PRIVATE_ROOT"])
    root.mkdir(mode=0o755)
    with pytest.raises(ValueError):
        state.record_path(checkout, create=True)
    assert stat.S_IMODE(root.stat().st_mode) == 0o755


@pytest.mark.parametrize("which", ["legacy", "external"])
def test_record_symlink_is_refused_without_deleting_original(checkout, tmp_path, which):
    original = tmp_path / "original"
    original.write_bytes(b"/opt/example-tools\n")
    link = (
        checkout / ".vibe-view-bin-links"
        if which == "legacy"
        else state.record_path(checkout, create=True)
    )
    link.symlink_to(original)
    with pytest.raises(ValueError):
        state.resolve_record(checkout)
    assert original.read_bytes() == b"/opt/example-tools\n"
    assert link.is_symlink()


def test_failed_copy_keeps_legacy(checkout, monkeypatch):
    legacy = checkout / ".vibe-view-bin-links"
    legacy.write_bytes(b"/opt/example-tools\n")

    def refuse_replace(*args):
        raise OSError("injected filesystem failure")

    monkeypatch.setattr(state.os, "replace", refuse_replace)
    with pytest.raises(OSError):
        state.resolve_record(checkout)
    assert legacy.read_bytes() == b"/opt/example-tools\n"
    assert not state.record_path(checkout).exists()


@pytest.mark.parametrize("kind", ["worktree", "bare", "metadata"])
def test_private_guard_policy_rejects_other_git_storage(checkout, tmp_path, monkeypatch, kind):
    spec = importlib.util.spec_from_file_location(
        "viewer_private_terms", Path(__file__).resolve().parents[1] / ".githooks/private_terms.py"
    )
    terms = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(terms)
    other = tmp_path / "policy-repository"
    other.mkdir()
    if kind == "worktree":
        (other / ".git").write_text("gitdir: example-store\n")
    elif kind == "bare":
        (other / "HEAD").write_text("ref: refs/heads/main\n")
        (other / "objects").mkdir()
        (other / "refs").mkdir()
    else:
        other = other / ".git"
        other.mkdir()
    policy = other / "policy.txt"
    policy.write_text("synthetic-private-term\n")
    monkeypatch.setenv("VIBE_PRIVACY_TERMS_FILE", str(policy))
    with pytest.raises(ValueError):
        terms.load_terms(checkout)
    assert policy.read_text() == "synthetic-private-term\n"
