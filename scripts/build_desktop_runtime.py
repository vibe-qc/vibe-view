#!/usr/bin/env python3
"""Build the relocatable macOS runtime from reviewed, hash-locked inputs.

Run on Apple Silicon with Python 3.11+; the build interpreter is never shipped.
Only the pinned standalone interpreter and binary wheels enter the runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "electron/runtime/macos-arm64.lock.json"
STAGE = ROOT / "electron/.runtime/macos-arm64"
CACHE = ROOT / ".cache/desktop-runtime"
MACHO = {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}
NOTICE = re.compile(r"license|licence|copyright|copying|notice|authors", re.I)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(args: list[str], **kwargs: Any) -> str:
    result = subprocess.run(args, check=True, text=True, capture_output=True, **kwargs)
    return str(result.stdout)


def clean_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "DYLD_"))
        and key not in {"VIRTUAL_ENV", "__PYVENV_LAUNCHER__", "RDBASE"}
    }


def fetch(item: dict[str, Any], cache: Path) -> Path:
    name = str(item["filename"])
    if Path(name).name != name:
        raise ValueError(f"Invalid input filename: {name}")
    dest = cache / name
    if not dest.exists():
        if not item["url"].startswith("https://"):
            raise ValueError("Runtime inputs require HTTPS")
        partial = dest.with_suffix(dest.suffix + ".partial")
        try:
            with (
                urllib.request.urlopen(item["url"], timeout=180) as response,
                partial.open("wb") as output,
            ):
                shutil.copyfileobj(response, output)
            if digest(partial) != item["sha256"]:
                raise ValueError(f"Checksum mismatch: {name}")
            partial.replace(dest)
        finally:
            partial.unlink(missing_ok=True)
    if digest(dest) != item["sha256"]:
        raise ValueError(f"Checksum mismatch in cache: {name}; remove it and retry")
    return dest


def collect_notices(archive: Path, destination: Path) -> None:
    """Copy license texts only, without following archive links."""
    count = 0
    with tarfile.open(archive) as source:
        for member in source:
            if not member.isfile() or not NOTICE.search(Path(member.name).name):
                continue
            relative = Path(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Unsafe archive path: {member.name}")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = source.extractfile(member)
            assert stream is not None
            with stream, target.open("wb") as output:
                shutil.copyfileobj(stream, output)
            count += 1
    if not count:
        raise ValueError(f"No license notices found in {archive.name}")


def check_native(root: Path) -> int:
    """Reject wrong architectures and references to build-machine libraries."""
    binaries = []
    for file in root.rglob("*"):
        if file.is_symlink():
            if not file.resolve().is_relative_to(root.resolve()) or not file.exists():
                raise ValueError(f"Non-relocatable symlink: {file.relative_to(root)}")
            continue
        if not file.is_file():
            continue
        with file.open("rb") as stream:
            if stream.read(4) not in MACHO:
                continue
        binaries.append(file)

    def check(file: Path) -> None:
        archs = run(["/usr/bin/lipo", "-archs", str(file)]).split()
        if "arm64" not in archs:
            raise ValueError(f"Missing arm64 code: {file.relative_to(root)}")
        # LC_ID_DYLIB can be a bare name (not a load dependency).
        identity = run(["/usr/bin/otool", "-arch", "arm64", "-D", str(file)]).splitlines()[1:]
        for line in run(["/usr/bin/otool", "-arch", "arm64", "-L", str(file)]).splitlines()[1:]:
            dependency = line.strip().split(" (", 1)[0]
            if dependency in identity:
                continue
            if not dependency.startswith(
                (
                    "@loader_path/",
                    "@rpath/",
                    "@executable_path/",
                    "/usr/lib/",
                    "/System/Library/",
                )
            ):
                raise ValueError(f"External library in {file.relative_to(root)}: {dependency}")
    if not binaries:
        raise ValueError("No native Python runtime found")
    # Independent native inspections dominate build time; bound their fan-out.
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(check, binaries))
    return len(binaries)


def verify(resources: Path, lock: dict[str, Any], *, native: bool = True) -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads((resources / "runtime.json").read_text())
    version = json.loads((ROOT / "electron/package.json").read_text())["version"]
    if manifest["appVersion"] != version or manifest["lockSha256"] != digest(LOCK):
        raise ValueError("Runtime is stale; rebuild it for this application and lock")
    python = resources / "python/bin/python3"
    code = """
import json, sys, pathlib, importlib.metadata as m
import vibeview, pyvista, vtk, trame, uvicorn, rdkit, numpy
root = pathlib.Path(sys.prefix).resolve()
assert all(pathlib.Path(x.__file__).resolve().is_relative_to(root)
           for x in (vibeview, pyvista, vtk, uvicorn, rdkit, numpy))
assert pyvista.Sphere().n_points > 0
print(json.dumps({'python': sys.version.split()[0],
                  'packages': {d.metadata['Name'].lower(): d.version for d in m.distributions()}}))
"""
    actual = json.loads(run([str(python), "-I", "-B", "-c", code], env=clean_environment()))
    expected = {item["name"].lower(): item["version"] for item in lock["wheels"]}
    expected["vibeview"] = version
    if actual != {"python": lock["pythonVersion"], "packages": expected}:
        raise ValueError("Installed Python distribution inventory differs from the lock")
    count = check_native(resources / "python") if native else 0
    print(
        f"Runtime verified: Python {actual['python']}, "
        f"{len(expected)} packages, {count} native files"
    )
    return manifest


def build(cache: Path, output: Path, lock: dict[str, Any]) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    items = [
        lock["python"],
        lock["pythonNotices"],
        *lock["wheels"],
        *lock["buildWheels"],
        *lock["nativeNotices"],
    ]
    inputs = {item["filename"]: fetch(item, cache) for item in items}
    output.parent.mkdir(parents=True, exist_ok=True)
    # A failed build cannot replace the previously verified runtime.
    with tempfile.TemporaryDirectory(prefix="runtime-build-", dir=output.parent) as temp:
        work = Path(temp)
        payload = work / "payload"
        payload.mkdir()
        with tarfile.open(inputs[lock["python"]["filename"]]) as archive:
            archive.extractall(payload, filter="data")
        python = payload / "python/bin/python3"
        pip = [str(python), "-I", "-B", "-m", "pip", "--isolated", "--disable-pip-version-check"]
        env = clean_environment()
        run(
            [
                *pip,
                "install",
                "--no-index",
                "--no-deps",
                "--no-compile",
                *[str(inputs[p["filename"]]) for p in lock["buildWheels"]],
            ],
            env=env,
        )
        wheels = work / "app-wheel"
        run(
            [
                *pip,
                "wheel",
                "--no-index",
                "--no-deps",
                "--no-build-isolation",
                "--wheel-dir",
                str(wheels),
                str(ROOT),
            ],
            env=env,
        )
        (app_wheel,) = wheels.glob("vibeview-*.whl")
        run(
            [
                *pip,
                "install",
                "--no-index",
                "--no-deps",
                "--no-compile",
                str(app_wheel),
                *[str(inputs[p["filename"]]) for p in lock["wheels"]],
            ],
            env=env,
        )
        # Also check dependency/extras closure; the final inventory below proves
        # build tools are absent and no unpinned package slipped in.
        run([*pip, "check"], env=env)
        extras = ",".join(lock["extras"])
        run([*pip, "install", "--dry-run", "--no-index", f"vibeview[{extras}]"], env=env)
        run([*pip, "uninstall", "-y", "pip", "setuptools", "wheel"], env=env)

        prefix = payload / "python"
        for path in (prefix / "bin").iterdir():
            if not re.fullmatch(r"python(?:3(?:\.\d+)?)?", path.name):
                path.unlink()
        for name in ("include", "share", "lib/pkgconfig"):
            shutil.rmtree(prefix / name, ignore_errors=True)
        for path in prefix.rglob("__pycache__"):
            shutil.rmtree(path)
        for pattern in ("*.pyc", "*.a", "direct_url.json"):
            for path in prefix.rglob(pattern):
                path.unlink()
        lib = prefix / "lib" / f"python{lock['pythonMinor']}"
        shutil.rmtree(lib / "test", ignore_errors=True)
        for path in lib.glob("config-*"):
            shutil.rmtree(path)

        notices = payload / "notices"
        notices.mkdir()
        # The full PBS archive carries native notices omitted by install_only.
        run(
            [
                "/usr/bin/tar",
                "-xf",
                str(inputs[lock["pythonNotices"]["filename"]]),
                "-C",
                str(notices),
                "python/PYTHON.json",
                "python/licenses",
            ]
        )
        for item in lock["nativeNotices"]:
            source = inputs[item["filename"]]
            target = notices / item["component"]
            target.mkdir()
            if item["archive"]:
                collect_notices(source, target)
            else:
                shutil.copyfile(source, target / source.name)
            if item.get("shipSource"):
                shutil.copyfile(source, target / source.name)
        shutil.copyfile(LOCK, notices / "runtime-lock.json")
        shutil.copyfile(ROOT / "electron/runtime/README.md", notices / "README.md")
        shutil.copyfile(ROOT / "LICENSE", notices / "vibe-view-MPL-2.0.txt")
        version = json.loads((ROOT / "electron/package.json").read_text())["version"]
        manifest = {
            "format": 1,
            "platform": "darwin",
            "arch": "arm64",
            "appVersion": version,
            "minimumMacOS": lock["minimumMacOS"],
            "pythonVersion": lock["pythonVersion"],
            "lockSha256": digest(LOCK),
            "appWheelSha256": digest(app_wheel),
            "packages": {p["name"].lower(): p["version"] for p in lock["wheels"]},
        }
        (payload / "runtime.json").write_text(json.dumps(manifest, indent=2) + "\n")
        verify(payload, lock)
        # No bytecode or local source references should enter distributed wheels.
        if output.exists():
            shutil.rmtree(output)
        payload.rename(output)
    print(f"Staged runtime: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path, help="Check an existing runtime resources directory")
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--output", type=Path, default=STAGE)
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("The macOS runtime must be built and checked on Apple Silicon")
    lock = json.loads(LOCK.read_text())
    if args.verify:
        verify(args.verify.resolve(), lock)
    else:
        build(args.cache.resolve(), args.output.resolve(), lock)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Runtime command failed:\n{exc.stdout}\n{exc.stderr}") from exc
