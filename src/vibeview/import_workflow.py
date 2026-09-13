"""Persistent loose-file to QVF import workflow used by the CLI and GUI."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from vibeview.converters import convert_to_qvf, detect_format
from vibeview.importers import get_importer
from vibeview.qvf import QVFReader
from vibeview.trexio_import import is_trexio_directory


class ImportWorkflowError(ValueError):
    """A persistent import could not be planned or completed safely."""


@dataclass(frozen=True)
class ImportPlan:
    source: Path
    destination: Path


@dataclass(frozen=True)
class ImportResult:
    source: Path
    destination: Path
    detected_format: str
    source_program: str
    section_kinds: tuple[str, ...]
    size_bytes: int
    sha256: str


_SKIPPED_DIRECTORY_NAMES = frozenset(
    {"__pycache__", "node_modules", "venv", "env"}
)


def _visible_files(
    directory: Path,
    *,
    excluded_directories: Sequence[Path] = (),
) -> Iterable[Path]:
    active_exclusions = tuple(
        excluded
        for excluded in excluded_directories
        if excluded != directory and directory in excluded.parents
    )
    for root, directories, filenames in os.walk(directory, topdown=True):
        root_path = Path(root)
        directories[:] = sorted(
            name
            for name in directories
            if not name.startswith(".")
            and name not in _SKIPPED_DIRECTORY_NAMES
            and not any(
                root_path / name == excluded or excluded in (root_path / name).parents
                for excluded in active_exclusions
            )
        )
        # A text-backend TREXIO directory is one dataset. Do not descend
        # into its group files or treat it as a batch-import root.
        for name in directories[:]:
            candidate = root_path / name
            if is_trexio_directory(candidate):
                directories.remove(name)
                yield candidate
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            candidate = root_path / name
            if any(
                candidate == excluded or excluded in candidate.parents
                for excluded in active_exclusions
            ):
                continue
            # Directory imports create QVFs. Do not feed those outputs back in
            # on a later run; native QVFs remain available as explicit inputs.
            if candidate.suffix.lower() == ".qvf":
                continue
            if candidate.is_file():
                yield candidate


def collect_import_inputs(
    inputs: Sequence[Path],
    *,
    format_name: str | None = None,
    excluded_directories: Sequence[Path] = (),
) -> tuple[list[Path], bool]:
    """Expand directories and return supported, de-duplicated files.

    Explicit files are retained when ``format_name`` is supplied, allowing a
    plugin to import formats with no distinctive suffix. Directory scans stay
    conservative and include only paths a built-in or plugin claims.
    """
    files: list[Path] = []
    saw_directory = False
    requested_importer = get_importer(format_name) if format_name is not None else None
    for raw in inputs:
        path = raw.resolve()
        if path.is_dir() and detect_format(path) == "trexio":
            if format_name not in (None, "trexio"):
                raise ImportWorkflowError(f"{raw} is a TREXIO dataset")
            files.append(path)
        elif path.is_dir():
            saw_directory = True
            for candidate in _visible_files(
                path,
                excluded_directories=excluded_directories,
            ):
                if requested_importer is not None:
                    try:
                        if requested_importer.matches(candidate):
                            files.append(candidate)
                    except (Exception, SystemExit):
                        # Third-party probes must not abort a whole directory
                        # import; another candidate may still be valid.
                        pass
                    continue
                detected = detect_format(candidate)
                if detected is None:
                    continue
                if format_name is not None:
                    normalized = detected.removeprefix("plugin:")
                    requested = format_name.removeprefix("plugin:")
                    if normalized != requested:
                        continue
                files.append(candidate)
        elif path.is_file():
            if format_name is None and detect_format(path) is None:
                raise ImportWorkflowError(
                    f"unsupported file format for {raw}; run 'vibe-view formats'"
                )
            files.append(path)
        else:
            raise ImportWorkflowError(f"input does not exist: {raw}")

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in files:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    if not unique:
        suffix = f" for format {format_name!r}" if format_name else ""
        raise ImportWorkflowError(f"no supported input files found{suffix}")
    return unique, saw_directory


def plan_imports(
    inputs: Sequence[Path],
    *,
    output: Path | None = None,
    format_name: str | None = None,
) -> tuple[ImportPlan, ...]:
    """Resolve output paths without writing anything."""
    prospective_output = (output or (Path.cwd() / "vibe-view-imports")).resolve()
    sources, saw_directory = collect_import_inputs(
        inputs,
        format_name=format_name,
        excluded_directories=(prospective_output,),
    )
    multiple = saw_directory or len(sources) > 1

    if not multiple:
        source = sources[0]
        destination = (output or source.with_suffix(".qvf")).resolve()
        if destination.is_dir():
            destination = destination / f"{source.stem}.qvf"
        if destination.suffix.lower() != ".qvf":
            destination = destination.with_suffix(".qvf")
        if destination == source:
            raise ImportWorkflowError(
                f"{source.name} is already a QVF; choose a different --output path"
            )
        return (ImportPlan(source, destination),)

    destination_root = (output or (Path.cwd() / "vibe-view-imports")).resolve()
    if destination_root.exists() and not destination_root.is_dir():
        raise ImportWorkflowError("--output must be a directory for multiple inputs")
    if destination_root.suffix.lower() == ".qvf":
        raise ImportWorkflowError("--output must be a directory for multiple inputs")

    plans: list[ImportPlan] = []
    used: set[str] = set()
    for source in sources:
        stem = source.stem or source.name
        candidate = f"{stem}.qvf"
        sequence = 2
        while candidate.casefold() in used:
            candidate = f"{stem}_{sequence}.qvf"
            sequence += 1
        used.add(candidate.casefold())
        destination = destination_root / candidate
        if destination == source:
            raise ImportWorkflowError(
                f"{source.name} would overwrite itself; choose a different --output directory"
            )
        plans.append(ImportPlan(source, destination))
    return tuple(plans)


def execute_import(
    plan: ImportPlan,
    *,
    format_name: str | None = None,
    force: bool = False,
) -> ImportResult:
    """Convert, validate, then atomically publish one planned QVF."""
    destination = plan.destination
    if destination.exists() and not force:
        raise ImportWorkflowError(
            f"output already exists: {destination}; pass --force to replace it"
        )

    detected = format_name or detect_format(plan.source)
    if detected is None:
        raise ImportWorkflowError(f"unsupported file format: {plan.source}")
    reader: QVFReader | None = None
    try:
        payload = convert_to_qvf(plan.source, format_name=format_name).getvalue()
        reader = QVFReader(payload)
        for section in reader.sections:
            for member in section.members.values():
                reader._verify_and_read(member)
        source_program = reader.source.program
        section_kinds = tuple(section.kind for section in reader.sections)
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        raise ImportWorkflowError(detail) from exc
    finally:
        if reader is not None:
            reader.close()

    temporary_path: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if force:
            os.replace(temporary_path, destination)
        else:
            try:
                # The temporary file lives beside its destination, so a hard
                # link publishes the completed inode atomically without an
                # overwrite race on macOS or Linux.
                os.link(temporary_path, destination)
            except FileExistsError as exc:
                raise ImportWorkflowError(
                    f"output already exists: {destination}; pass --force to replace it"
                ) from exc
            temporary_path.unlink()
        temporary_path = None
    except ImportWorkflowError:
        raise
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise ImportWorkflowError(f"could not write {destination}: {detail}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return ImportResult(
        source=plan.source,
        destination=destination,
        detected_format=detected,
        source_program=source_program,
        section_kinds=section_kinds,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
