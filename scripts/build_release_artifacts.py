#!/usr/bin/env python3
"""Build and validate deterministic vibe-view release artifacts.

The public wheel is committed under ``docs/_static/downloads`` and therefore
must never be reused after a failed build.  This script builds a wheel and an
sdist twice, normalises the sdist metadata that setuptools leaves dependent on
the host, verifies byte-for-byte reproducibility, checks packaged files against
the current source tree, runs ``twine check``, and only then replaces outputs.
"""

from __future__ import annotations

import argparse
import base64
import copy
import csv
import gzip
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from collections.abc import Iterable
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Final

PROJECT_DIR: Final = Path(__file__).resolve().parents[1]
ZIP_EPOCH: Final = 315532800  # 1980-01-01, the earliest timestamp ZIP supports.


class ArtifactError(RuntimeError):
    """A release artifact failed a build or validation contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _declared_version(project_dir: Path) -> str:
    data = tomllib.loads((project_dir / "pyproject.toml").read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise ArtifactError("pyproject.toml has no non-empty project.version")
    return version


def _source_date_epoch(project_dir: Path) -> int:
    raw = os.environ.get("SOURCE_DATE_EPOCH")
    if raw is None:
        try:
            proc = subprocess.run(
                ["git", "-C", str(project_dir), "log", "-1", "--format=%ct"],
                check=False,
                capture_output=True,
                text=True,
            )
            raw = proc.stdout.strip() if proc.returncode == 0 else ""
        except FileNotFoundError:
            raw = ""
    if not raw:
        package_dir = project_dir / "src" / "vibeview"
        candidates = [project_dir / "pyproject.toml", *package_dir.rglob("*")]
        raw = str(int(max(path.stat().st_mtime for path in candidates if path.is_file())))
    try:
        epoch = int(raw)
    except ValueError as exc:
        raise ArtifactError(f"SOURCE_DATE_EPOCH must be an integer, got {raw!r}") from exc
    if epoch < ZIP_EPOCH:
        raise ArtifactError("SOURCE_DATE_EPOCH must be on or after 1980-01-01")
    return epoch


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    print("+", " ".join(command), flush=True)
    try:
        subprocess.run(command, cwd=cwd, env=env, check=True)
    except subprocess.CalledProcessError as exc:
        raise ArtifactError(f"command failed with exit status {exc.returncode}") from exc


# The tooling this script needs, as (import probe, distribution name).
#
# Declared here rather than inline so the `release` extra in pyproject.toml has
# something to agree with: `tests/test_release_artifacts.py` asserts the two
# match. Before that extra existed, the versions were pinned only in
# .gitlab-ci.yml, so a developer following CONTRIBUTING.md into a `[test]` venv
# hit a missing tool that no metadata mentioned.
RELEASE_BUILD_TOOLS: Final = (
    ("build.__main__", "build"),
    ("twine.__main__", "twine"),
)


def _require_build_tools(project_dir: Path) -> None:
    missing: list[str] = []
    for module, distribution in RELEASE_BUILD_TOOLS:
        proc = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=Path(project_dir.anchor),
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            missing.append(distribution)
    if missing:
        raise ArtifactError(
            f"missing release build tool(s): {', '.join(missing)}; "
            f"run {sys.executable} -m pip install -e '{project_dir}[release]'"
        )


def _safe_archive_name(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ArtifactError(f"artifact contains unsafe path: {name!r}")


def _source_payloads(package_dir: Path) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    for path in sorted(package_dir.rglob("*")):
        relative = path.relative_to(package_dir)
        if any(
            part.startswith(".") or part in {"__pycache__", ".pytest_cache"}
            for part in relative.parts
        ):
            continue
        if path.is_file() and path.suffix not in {".pyc", ".pyo"}:
            payloads[relative.as_posix()] = path.read_bytes()
    if not payloads:
        raise ArtifactError(f"no package sources found below {package_dir}")
    return payloads


def _metadata_identity(raw: bytes, *, version: str, label: str) -> None:
    metadata = BytesParser().parsebytes(raw)
    if metadata.get("Name") != "vibeview":
        raise ArtifactError(f"{label} declares unexpected Name: {metadata.get('Name')!r}")
    if metadata.get("Version") != version:
        raise ArtifactError(
            f"{label} declares Version {metadata.get('Version')!r}, expected {version!r}"
        )


def _validate_record(archive: zipfile.ZipFile, record_name: str) -> None:
    members = {info.filename for info in archive.infolist() if not info.is_dir()}
    rows = list(csv.reader(io.StringIO(archive.read(record_name).decode("utf-8"))))
    recorded = {row[0] for row in rows}
    if recorded != members:
        missing = sorted(members - recorded)
        extra = sorted(recorded - members)
        raise ArtifactError(f"wheel RECORD drift (missing={missing}, extra={extra})")

    for name, encoded_hash, raw_size in rows:
        if name == record_name:
            if encoded_hash or raw_size:
                raise ArtifactError("wheel RECORD must not hash itself")
            continue
        data = archive.read(name)
        expected_hash = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(
            b"="
        ).decode("ascii")
        if encoded_hash != expected_hash:
            raise ArtifactError(f"wheel RECORD hash mismatch for {name}")
        if raw_size != str(len(data)):
            raise ArtifactError(f"wheel RECORD size mismatch for {name}")


def validate_wheel(wheel: Path, *, project_dir: Path = PROJECT_DIR) -> None:
    """Validate wheel identity, integrity, and current package-source content."""

    version = _declared_version(project_dir)
    expected_name = f"vibeview-{version}-py3-none-any.whl"
    if wheel.name != expected_name:
        raise ArtifactError(f"expected wheel {expected_name}, got {wheel.name}")
    expected = _source_payloads(project_dir / "src" / "vibeview")

    with zipfile.ZipFile(wheel) as archive:
        duplicate_names = {
            name for name in archive.namelist() if archive.namelist().count(name) > 1
        }
        if duplicate_names:
            raise ArtifactError(f"wheel has duplicate members: {sorted(duplicate_names)}")
        for name in archive.namelist():
            _safe_archive_name(name)
        corrupt = archive.testzip()
        if corrupt is not None:
            raise ArtifactError(f"wheel CRC check failed for {corrupt}")

        prefix = "vibeview/"
        actual = {
            name.removeprefix(prefix): archive.read(name)
            for name in archive.namelist()
            if name.startswith(prefix) and not name.endswith("/")
        }
        if actual.keys() != expected.keys():
            missing = sorted(expected.keys() - actual.keys())
            extra = sorted(actual.keys() - expected.keys())
            raise ArtifactError(f"wheel package content drift (missing={missing}, extra={extra})")
        stale = sorted(name for name, data in actual.items() if data != expected[name])
        if stale:
            raise ArtifactError(f"wheel contains stale source payloads: {stale}")

        dist_info = f"vibeview-{version}.dist-info"
        metadata_name = f"{dist_info}/METADATA"
        wheel_name = f"{dist_info}/WHEEL"
        entry_points_name = f"{dist_info}/entry_points.txt"
        record_name = f"{dist_info}/RECORD"
        required = {metadata_name, wheel_name, entry_points_name, record_name}
        absent = required - set(archive.namelist())
        if absent:
            raise ArtifactError(f"wheel is missing metadata files: {sorted(absent)}")
        _metadata_identity(archive.read(metadata_name), version=version, label=wheel.name)
        wheel_metadata = archive.read(wheel_name).decode("utf-8")
        if "Root-Is-Purelib: true" not in wheel_metadata:
            raise ArtifactError("wheel is not marked pure Python")
        if "Tag: py3-none-any" not in wheel_metadata:
            raise ArtifactError("wheel does not declare the py3-none-any tag")
        entry_points = archive.read(entry_points_name).decode("utf-8")
        if "vibe-view = vibeview.cli:main" not in entry_points:
            raise ArtifactError("wheel does not expose the vibe-view console command")
        _validate_record(archive, record_name)


def _tar_payload(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    stream = archive.extractfile(member)
    if stream is None:
        raise ArtifactError(f"could not read sdist member {member.name}")
    return stream.read()


def validate_sdist(sdist: Path, *, project_dir: Path = PROJECT_DIR) -> None:
    """Validate sdist identity and current package-source content."""

    version = _declared_version(project_dir)
    expected_name = f"vibeview-{version}.tar.gz"
    if sdist.name != expected_name:
        raise ArtifactError(f"expected sdist {expected_name}, got {sdist.name}")
    root = f"vibeview-{version}"
    expected = _source_payloads(project_dir / "src" / "vibeview")

    with tarfile.open(sdist, "r:gz") as archive:
        names = [member.name for member in archive.getmembers()]
        if len(names) != len(set(names)):
            raise ArtifactError("sdist contains duplicate members")
        for name in names:
            _safe_archive_name(name)
            if name != root and not name.startswith(f"{root}/"):
                raise ArtifactError(f"sdist member is outside {root}: {name}")

        package_prefix = f"{root}/src/vibeview/"
        actual = {
            member.name.removeprefix(package_prefix): _tar_payload(archive, member)
            for member in archive.getmembers()
            if member.isfile() and member.name.startswith(package_prefix)
        }
        if actual.keys() != expected.keys():
            missing = sorted(expected.keys() - actual.keys())
            extra = sorted(actual.keys() - expected.keys())
            raise ArtifactError(f"sdist package content drift (missing={missing}, extra={extra})")
        stale = sorted(name for name, data in actual.items() if data != expected[name])
        if stale:
            raise ArtifactError(f"sdist contains stale source payloads: {stale}")

        for relative in ("LICENSE", "MANIFEST.in", "README.md", "pyproject.toml"):
            name = f"{root}/{relative}"
            try:
                member = archive.getmember(name)
            except KeyError as exc:
                raise ArtifactError(f"sdist is missing {relative}") from exc
            if _tar_payload(archive, member) != (project_dir / relative).read_bytes():
                raise ArtifactError(f"sdist contains a stale {relative}")
        pkg_info = archive.getmember(f"{root}/PKG-INFO")
        _metadata_identity(_tar_payload(archive, pkg_info), version=version, label=sdist.name)


def normalise_sdist(sdist: Path, *, epoch: int) -> None:
    """Remove filesystem owner and build-time variation from a setuptools sdist."""

    entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with tarfile.open(sdist, "r:gz") as source:
        for original in source.getmembers():
            info = copy.copy(original)
            data = _tar_payload(source, original) if original.isfile() else None
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = epoch
            info.pax_headers = {}
            if info.isdir():
                info.mode = 0o755
            elif info.isfile():
                info.mode = 0o755 if info.mode & 0o111 else 0o644
            elif info.issym() or info.islnk():
                info.mode = 0o777
            entries.append((info, data))

    temporary = sdist.with_name(f".{sdist.name}.{os.getpid()}.tmp")
    try:
        with (
            temporary.open("wb") as raw,
            gzip.GzipFile(
                filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=epoch
            ) as compressed,
            tarfile.open(
                fileobj=compressed,
                mode="w",
                format=tarfile.PAX_FORMAT,
                pax_headers={},
            ) as output,
        ):
            for info, data in sorted(entries, key=lambda item: item[0].name):
                output.addfile(info, io.BytesIO(data) if data is not None else None)
        os.replace(temporary, sdist)
    finally:
        temporary.unlink(missing_ok=True)


def _artifact_pair(directory: Path, *, version: str) -> tuple[Path, Path]:
    wheel = directory / f"vibeview-{version}-py3-none-any.whl"
    sdist = directory / f"vibeview-{version}.tar.gz"
    missing = [path.name for path in (wheel, sdist) if not path.is_file()]
    extras = sorted(
        path.name
        for pattern in ("vibeview-*.whl", "vibeview-*.tar.gz")
        for path in directory.glob(pattern)
        if path not in {wheel, sdist}
    )
    if missing or extras:
        raise ArtifactError(f"unexpected build outputs (missing={missing}, extra={extras})")
    return wheel, sdist


def _build_once(
    project_dir: Path,
    output_dir: Path,
    *,
    epoch: int,
    version: str,
) -> tuple[Path, Path]:
    env = dict(os.environ, SOURCE_DATE_EPOCH=str(epoch), PYTHONHASHSEED="0")
    _run(
        [
            sys.executable,
            "-m",
            "build",
            "--quiet",
            "--outdir",
            str(output_dir),
            str(project_dir),
        ],
        cwd=Path(project_dir.anchor),
        env=env,
    )
    wheel, sdist = _artifact_pair(output_dir, version=version)
    normalise_sdist(sdist, epoch=epoch)
    validate_wheel(wheel, project_dir=project_dir)
    validate_sdist(sdist, project_dir=project_dir)
    return wheel, sdist


def _assert_reproducible(first: Iterable[Path], second: Iterable[Path]) -> None:
    first_by_name = {path.name: path for path in first}
    second_by_name = {path.name: path for path in second}
    if first_by_name.keys() != second_by_name.keys():
        raise ArtifactError("repeated builds produced different artifact names")
    mismatches = [
        name
        for name, first_path in first_by_name.items()
        if _sha256(first_path) != _sha256(second_by_name[name])
    ]
    if mismatches:
        raise ArtifactError(f"repeated builds were not byte-for-byte reproducible: {mismatches}")


def _replace_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _publish_outputs(artifacts: Iterable[Path], output_dir: Path) -> list[Path]:
    artifact_list = list(artifacts)
    output_dir.mkdir(parents=True, exist_ok=True)
    expected_names = {path.name for path in artifact_list}
    for pattern in ("vibeview-*.whl", "vibeview-*.tar.gz"):
        for old in output_dir.glob(pattern):
            if old.name not in expected_names and old.is_file():
                old.unlink()
    published: list[Path] = []
    for artifact in artifact_list:
        destination = output_dir / artifact.name
        _replace_file(artifact, destination)
        published.append(destination)

    checksum_text = "".join(f"{_sha256(path)}  {path.name}\n" for path in published)
    checksum_tmp = output_dir / f".SHA256SUMS.{os.getpid()}.tmp"
    checksum_tmp.write_text(checksum_text, encoding="utf-8")
    os.replace(checksum_tmp, output_dir / "SHA256SUMS")
    return published


def _stage_wheel(wheel: Path, downloads_dir: Path) -> Path:
    downloads_dir.mkdir(parents=True, exist_ok=True)
    destination = downloads_dir / wheel.name
    if destination.is_file():
        if _sha256(destination) != _sha256(wheel):
            raise ArtifactError(
                f"refusing to replace immutable released wheel {destination}; "
                "bump project.version before staging a changed artifact"
            )
    else:
        _replace_file(wheel, destination)
    if _sha256(destination) != _sha256(wheel):
        raise ArtifactError(f"staged wheel hash mismatch: {destination}")
    return destination


def build_release(
    *,
    project_dir: Path,
    output_dir: Path,
    stage_wheel: Path | None,
) -> list[Path]:
    """Build, validate, and publish the current wheel and sdist."""

    version = _declared_version(project_dir)
    epoch = _source_date_epoch(project_dir)
    _require_build_tools(project_dir)
    print(f"Building vibeview {version} with SOURCE_DATE_EPOCH={epoch}")
    with tempfile.TemporaryDirectory(prefix="vibeview-release-") as temporary:
        work = Path(temporary)
        first = _build_once(project_dir, work / "first", epoch=epoch, version=version)
        second = _build_once(project_dir, work / "second", epoch=epoch, version=version)
        _assert_reproducible(first, second)

        env = dict(os.environ, SOURCE_DATE_EPOCH=str(epoch), PYTHONHASHSEED="0")
        _run(
            [sys.executable, "-m", "twine", "check", *(str(path) for path in first)],
            cwd=Path(project_dir.anchor),
            env=env,
        )
        # Re-read the source after the repeated builds and twine check.  If a
        # concurrent edit landed during a long release build, do not publish
        # the now-stale artifacts even when the two earlier snapshots matched.
        validate_wheel(first[0], project_dir=project_dir)
        validate_sdist(first[1], project_dir=project_dir)
        published = _publish_outputs(first, output_dir)
        if stage_wheel is not None:
            wheel = next(path for path in published if path.suffix == ".whl")
            staged = _stage_wheel(wheel, stage_wheel)
            print(f"Staged {staged}")

    for path in published:
        print(f"Wrote {path} ({_sha256(path)})")
    print(f"Wrote {output_dir / 'SHA256SUMS'}")
    return published


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_DIR / "dist",
        help="validated artifact directory (default: vibe-view/dist)",
    )
    parser.add_argument(
        "--stage-wheel",
        type=Path,
        metavar="DOWNLOADS_DIR",
        help="stage a new-version website wheel (changed same-version bytes are refused)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        build_release(
            project_dir=PROJECT_DIR,
            output_dir=args.output_dir.resolve(),
            stage_wheel=args.stage_wheel.resolve() if args.stage_wheel else None,
        )
    except (ArtifactError, OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
