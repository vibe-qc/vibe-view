"""Keep installer operator records outside source checkouts and Git stores."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
import tempfile
from pathlib import Path


def outside_git(path: Path, checkout: Path) -> Path:
    if not path.is_absolute():
        raise ValueError("private storage needs an absolute path")
    resolved = path.resolve()
    for candidate in (path.absolute(), resolved):
        if candidate.is_relative_to(checkout.resolve()):
            raise ValueError("private storage must be outside the source checkout")
        for parent in (candidate, *candidate.parents):
            marker = parent / ".git"
            if (
                parent.name.casefold() == ".git"
                or marker.exists()
                or marker.is_symlink()
                or (
                    (parent / "HEAD").is_file()
                    and (parent / "objects").is_dir()
                    and (parent / "refs").is_dir()
                )
            ):
                raise ValueError("private storage must be outside Git worktrees and object stores")
    return resolved


def record_path(checkout: Path, *, create: bool = False) -> Path:
    configured = os.environ.get("VIBE_PRIVATE_ROOT")
    if configured is not None:
        if not configured:
            raise ValueError("VIBE_PRIVATE_ROOT must not be empty")
        supplied = Path(configured)
    else:
        state = os.environ.get("XDG_STATE_HOME")
        if state:
            supplied = Path(state) / "vibe-private"
        else:
            home = os.environ.get("HOME")
            if not home or not Path(home).is_absolute():
                raise ValueError("HOME must be absolute")
            supplied = Path(home) / ".local" / "state" / "vibe-private"
    root = outside_git(supplied, checkout)
    key = hashlib.sha256(os.fsencode(checkout.resolve())).hexdigest()
    directories = [
        root,
        root / "vibe-view",
        root / "vibe-view" / "state",
        root / "vibe-view" / "state" / key,
    ]
    # Reject symlink interfaces as well as destinations inside another checkout.
    if supplied.is_symlink():
        raise ValueError("private root must not be a symlink")
    for directory in directories:
        outside_git(directory, checkout)
        if directory.is_symlink():
            raise ValueError("private directory must not be a symlink")
        if create:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.exists():
            info = directory.stat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise ValueError("private directories must be caller-owned with mode 0700")
    outside_git(directories[-1], checkout)
    return directories[-1] / "bin-links"


def read_record(path: Path, *, legacy: bool = False) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("launcher record must be a caller-owned regular file")
    if not legacy and stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("private launcher record needs mode 0600")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        if os.fstat(stream.fileno()).st_ino != info.st_ino:
            raise ValueError("launcher record changed while opening")
        return stream.read()


def resolve_record(checkout: Path, *, dry_run: bool = False) -> Path:
    target = record_path(checkout)
    old = checkout / ".vibe-view-bin-links"
    current = read_record(target)
    previous = read_record(old, legacy=True)
    if dry_run:
        if current is not None and previous is not None:
            raise ValueError("migrate duplicate launcher records before a dry-run uninstall")
        return old if previous is not None else target
    if previous is None:
        return record_path(checkout, create=True)
    record_path(checkout, create=True)
    # Preserve exact old bytes when no external record exists. Merge only the
    # directory inventory when both exist; never overwrite either on failure.
    data = (
        previous
        if current is None
        else b"\n".join(dict.fromkeys((current + b"\n" + previous).splitlines())) + b"\n"
    )
    fd, temporary = tempfile.mkstemp(prefix=".bin-links-", dir=target.parent)
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if read_record(old, legacy=True) != previous or read_record(target) != current:
            raise ValueError("launcher records changed during migration")
        os.replace(temp, target)
        if read_record(target) != data:
            raise ValueError("external launcher record verification failed")
        # Deletion happens only after the external bytes have been verified.
        if read_record(old, legacy=True) != previous:
            raise ValueError("legacy launcher record changed during migration")
        old.unlink()
    finally:
        temp.unlink(missing_ok=True)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        print(resolve_record(args.checkout, dry_run=args.dry_run))
    except (OSError, ValueError):
        print(
            "Error: unsafe or invalid private launcher storage; no private paths disclosed.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
