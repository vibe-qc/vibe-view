"""Release artifact determinism and fail-closed website staging guards."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import re
import tarfile
import tomllib
from pathlib import Path

import pytest

VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "scripts" / "build_release_artifacts.py"


def _release_module():
    spec = importlib.util.spec_from_file_location("vibeview_release_artifacts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_sdist(path: Path, *, mtime: int, uid: int, reverse: bool) -> None:
    members = [
        ("vibeview-1.0.0", None),
        ("vibeview-1.0.0/README.md", b"same payload\n"),
        ("vibeview-1.0.0/src", None),
    ]
    if reverse:
        members.reverse()
    with (
        path.open("wb") as raw,
        gzip.GzipFile(filename="host-specific.tar", mode="wb", fileobj=raw, mtime=mtime) as gz,
        tarfile.open(fileobj=gz, mode="w") as archive,
    ):
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.mtime = mtime
            info.uid = uid
            info.gid = uid
            info.uname = "local-user"
            info.gname = "local-group"
            if data is None:
                info.type = tarfile.DIRTYPE
                info.mode = 0o700
                archive.addfile(info)
            else:
                info.size = len(data)
                info.mode = 0o600
                archive.addfile(info, io.BytesIO(data))


def test_sdist_normalisation_removes_host_and_build_time_variation(tmp_path: Path) -> None:
    release = _release_module()
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"
    _write_sdist(first, mtime=1_700_000_001, uid=501, reverse=False)
    _write_sdist(second, mtime=1_800_000_002, uid=1001, reverse=True)

    release.normalise_sdist(first, epoch=1_600_000_000)
    release.normalise_sdist(second, epoch=1_600_000_000)

    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first, "r:gz") as archive:
        assert [member.name for member in archive.getmembers()] == sorted(
            member.name for member in archive.getmembers()
        )
        assert all(member.mtime == 1_600_000_000 for member in archive.getmembers())
        assert all(member.uid == member.gid == 0 for member in archive.getmembers())
        assert all(not member.uname and not member.gname for member in archive.getmembers())


def test_staging_refuses_to_replace_released_bytes_at_the_same_version(
    tmp_path: Path,
) -> None:
    release = _release_module()
    built = tmp_path / "built" / "vibeview-1.0.0-py3-none-any.whl"
    staged = tmp_path / "downloads" / built.name
    built.parent.mkdir()
    staged.parent.mkdir()
    built.write_bytes(b"new bytes")
    staged.write_bytes(b"released bytes")

    with pytest.raises(release.ArtifactError, match="immutable released wheel"):
        release._stage_wheel(built, staged.parent)

    assert staged.read_bytes() == b"released bytes"


def test_staging_a_new_version_retains_the_previous_released_wheel(tmp_path: Path) -> None:
    release = _release_module()
    built = tmp_path / "built" / "vibeview-2.0.0-py3-none-any.whl"
    downloads = tmp_path / "downloads"
    old = downloads / "vibeview-1.0.0-py3-none-any.whl"
    built.parent.mkdir()
    downloads.mkdir()
    built.write_bytes(b"version two")
    old.write_bytes(b"version one")

    staged = release._stage_wheel(built, downloads)

    assert staged.read_bytes() == b"version two"
    assert old.read_bytes() == b"version one"


def test_published_artifacts_have_verified_standard_checksums(tmp_path: Path) -> None:
    release = _release_module()
    inputs = tmp_path / "inputs"
    output = tmp_path / "output"
    inputs.mkdir()
    wheel = inputs / "vibeview-1.0.0-py3-none-any.whl"
    sdist = inputs / "vibeview-1.0.0.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")

    published = release._publish_outputs((wheel, sdist), output)

    checksums = (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert checksums == [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in published
    ]


# ── the `release` extra and the script must agree ─────────────────────────
#
# build and twine were pinned only inline in .gitlab-ci.yml, so the requirement
# appeared in no package metadata: a developer following CONTRIBUTING.md into a
# `[test]` venv ran the script and hit a missing tool that nothing declared.
# Now the extra exists, and these keep it honest.


def _release_extra() -> list[str]:
    data = tomllib.loads((VIEWER_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["optional-dependencies"]["release"]


def _requirement_name(requirement: str) -> str:
    """The distribution name from a PEP 508 requirement string."""
    return re.split(r"[<>=!~\[;\s]", requirement, maxsplit=1)[0].strip()


def test_the_release_extra_declares_every_tool_the_script_requires() -> None:
    release = _release_module()
    required = {distribution for _, distribution in release.RELEASE_BUILD_TOOLS}
    declared = {_requirement_name(r) for r in _release_extra()}
    assert required <= declared, (
        f"{sorted(required - declared)} are required by "
        "scripts/build_release_artifacts.py but missing from the [release] "
        "extra; a `pip install -e '.[release]'` would not be enough to run it"
    )


def test_the_release_extra_declares_nothing_the_script_does_not_need() -> None:
    """An extra that over-declares is how a stale pin survives unnoticed."""
    release = _release_module()
    required = {distribution for _, distribution in release.RELEASE_BUILD_TOOLS}
    declared = {_requirement_name(r) for r in _release_extra()}
    assert declared <= required, (
        f"{sorted(declared - required)} are declared in the [release] extra but "
        "scripts/build_release_artifacts.py does not require them"
    )


def test_every_release_requirement_is_version_pinned() -> None:
    """Release tooling decides what bytes ship; an unpinned major is a
    reproducibility risk, not a convenience."""
    unpinned = [r for r in _release_extra() if not re.search(r"[<>=~]", r)]
    assert not unpinned, f"unpinned release requirements: {unpinned}"


def test_ci_installs_the_extra_rather_than_repeating_its_pins() -> None:
    """The pins lived in .gitlab-ci.yml and nowhere else. If they come back
    there, the extra has quietly stopped being the source of truth."""
    ci = (VIEWER_DIR / ".gitlab-ci.yml").read_text(encoding="utf-8")
    assert "pip install -e '.[test,release]'" in ci
    for tool in ("build>=", "twine>="):
        assert tool not in ci, (
            f"{tool!r} is pinned in .gitlab-ci.yml again; it belongs in the "
            "[release] extra so the pin lives with the code that needs it"
        )


def test_the_missing_tool_message_points_at_the_extra() -> None:
    """The old remediation was `pip install build twine`, which works but
    leaves the venv undeclared next time."""
    source = SCRIPT.read_text(encoding="utf-8")
    assert "[release]" in source


def test_wheel_wrapper_stages_inside_standalone_checkout(tmp_path: Path) -> None:
    """The pre-split wrapper escaped into the parent repository's docs tree."""
    import os
    import shutil
    import subprocess
    import sys

    checkout = tmp_path / "viewer checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(VIEWER_DIR / "scripts" / "make_wheel.sh", scripts)
    # Replace the expensive builder, retaining the wrapper's real path handling.
    (scripts / "build_release_artifacts.py").write_text(
        "import argparse, pathlib\n"
        "p=argparse.ArgumentParser()\n"
        "p.add_argument('--output-dir');p.add_argument('--stage-wheel')\n"
        "a=p.parse_args()\n"
        "for folder in [a.output_dir,a.stage_wheel]:\n"
        " d=pathlib.Path(folder);d.mkdir(parents=True,exist_ok=True)\n"
        " (d/'vibeview-9.8.7-py3-none-any.whl').write_bytes(b'fixture')\n"
    )
    result = subprocess.run(
        ["bash", str(scripts / "make_wheel.sh")],
        cwd=tmp_path,
        env={**os.environ, "VIBE_VIEW_RELEASE_PYTHON": sys.executable},
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    wheel = checkout / "docs/_static/downloads/vibeview-9.8.7-py3-none-any.whl"
    assert wheel.read_bytes() == b"fixture"
    assert not (tmp_path / "docs").exists()
