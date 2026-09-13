"""Third-party importer discovery for vibe-view.

Importer packages register one object in the ``vibeview.importers`` entry
point group.  The object may be an :class:`ImporterSpec` or a zero-argument
factory returning one.  Keeping the contract in this small, renderer-free
module lets chemistry codes add file support without depending on vibe-view's
web or desktop stack.
"""

from __future__ import annotations

import contextlib
import io
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import BinaryIO, cast

IMPORTER_API_VERSION = 1
IMPORTER_ENTRY_POINT_GROUP = "vibeview.importers"

# Plugin names share the ``--from`` namespace with converters shipped by
# vibe-view. Keeping this list in the lightweight contract module lets plugin
# discovery reject an ambiguous registration without importing NumPy or any
# of the renderer/converter stack.
RESERVED_FORMAT_NAMES = frozenset(
    {
        "ase",
        "cif",
        "cube",
        "gjf",
        "gro",
        "mol2",
        "pdb",
        "py",
        "qvf",
        "sdf",
        "trexio",
        "xyz",
    }
)

ImporterOutput = bytes | bytearray | BinaryIO
ImporterConverter = Callable[[Path], ImporterOutput]
ImporterProbe = Callable[[Path], bool]


class ImporterError(ValueError):
    """An importer could not be loaded or did not produce a valid QVF."""


@dataclass(frozen=True)
class ImporterSpec:
    """Versioned contract implemented by a third-party file importer.

    ``format_name`` is the stable CLI name used by ``--from``. Extensions
    include their leading dot and are matched case-insensitively. Exact
    ``stems`` cover extensionless formats such as ``POSCAR``. A custom
    ``probe`` may inspect a path when suffix matching is insufficient.

    ``convert`` must return a complete QVF archive as bytes or a readable
    binary file object positioned anywhere; vibe-view rewinds and validates
    the archive before it reaches the caller.
    """

    format_name: str
    description: str
    extensions: tuple[str, ...]
    convert: ImporterConverter
    stems: tuple[str, ...] = ()
    probe: ImporterProbe | None = None
    data_kinds: tuple[str, ...] = ("structure",)
    api_version: int = IMPORTER_API_VERSION

    def matches(self, path: Path) -> bool:
        """Return whether this importer accepts ``path``."""
        lower_name = path.name.lower()
        if any(lower_name.endswith(suffix.lower()) for suffix in self.extensions):
            return True
        if path.name in self.stems:
            return True
        return bool(self.probe and self.probe(path))


@dataclass(frozen=True)
class ImporterStatus:
    """One discovered entry point, including isolated load failures."""

    entry_point: str
    distribution: str | None
    spec: ImporterSpec | None
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.spec is not None and self.error is None


def _entry_points() -> tuple[metadata.EntryPoint, ...]:
    """Return importer entry points across supported importlib APIs."""
    discovered = metadata.entry_points()
    selected: Sequence[metadata.EntryPoint]
    if hasattr(discovered, "select"):
        selected = discovered.select(group=IMPORTER_ENTRY_POINT_GROUP)
    else:  # pragma: no cover - compatibility with older importlib-metadata
        legacy = cast(Mapping[str, Sequence[metadata.EntryPoint]], discovered)
        selected = legacy.get(IMPORTER_ENTRY_POINT_GROUP, ())
    return tuple(sorted(selected, key=lambda ep: ep.name))


def _distribution_name(entry_point: metadata.EntryPoint) -> str | None:
    dist = getattr(entry_point, "dist", None)
    if dist is None:
        return None
    try:
        return dist.metadata.get("Name") or str(dist)
    except Exception:  # pragma: no cover - defensive for third-party metadata
        return str(dist)


def _validate_spec(value: object, entry_point_name: str) -> ImporterSpec:
    if callable(value) and not isinstance(value, ImporterSpec):
        value = value()
    if not isinstance(value, ImporterSpec):
        raise TypeError("entry point must expose ImporterSpec or a zero-argument factory")

    if type(value.api_version) is not int:
        raise TypeError("api_version must be an integer")
    if value.api_version != IMPORTER_API_VERSION:
        raise ValueError(
            f"unsupported importer API {value.api_version}; "
            f"vibe-view supports {IMPORTER_API_VERSION}"
        )
    if not isinstance(value.format_name, str):
        raise TypeError("format_name must be a string")
    if not value.format_name or any(ch.isspace() for ch in value.format_name):
        raise ValueError("format_name must be a non-empty name without whitespace")
    if value.format_name.casefold() in RESERVED_FORMAT_NAMES:
        raise ValueError(f"format_name {value.format_name!r} is reserved by a built-in importer")
    if value.format_name != entry_point_name:
        raise ValueError(
            f"format_name {value.format_name!r} must match entry point name {entry_point_name!r}"
        )
    if not isinstance(value.description, str):
        raise TypeError("description must be a string")
    if not value.description.strip():
        raise ValueError("description must be non-empty")
    if not isinstance(value.extensions, tuple):
        raise TypeError("extensions must be a tuple of strings")
    if not isinstance(value.stems, tuple):
        raise TypeError("stems must be a tuple of strings")
    if not isinstance(value.data_kinds, tuple):
        raise TypeError("data_kinds must be a tuple of strings")
    if not callable(value.convert):
        raise TypeError("convert must be callable")
    if value.probe is not None and not callable(value.probe):
        raise TypeError("probe must be callable or None")
    if not value.extensions and not value.stems and value.probe is None:
        raise ValueError("declare at least one extension, stem, or probe")

    for suffix in value.extensions:
        if not isinstance(suffix, str):
            raise TypeError("extensions must contain only strings")
        if not suffix.startswith("."):
            raise ValueError(f"extension {suffix!r} must start with '.'")
        if len(suffix) == 1 or "/" in suffix or "\\" in suffix:
            raise ValueError(f"extension {suffix!r} is not a valid filename suffix")
    normalized_extensions = [suffix.casefold() for suffix in value.extensions]
    if len(set(normalized_extensions)) != len(normalized_extensions):
        raise ValueError("extensions must not contain duplicate suffixes")

    for stem in value.stems:
        if not isinstance(stem, str):
            raise TypeError("stems must contain only strings")
        if not stem or "/" in stem or "\\" in stem:
            raise ValueError(f"stem {stem!r} must be a non-empty filename")
    if len(set(value.stems)) != len(value.stems):
        raise ValueError("stems must not contain duplicate filenames")

    if not value.data_kinds:
        raise ValueError("data_kinds must declare at least one QVF data kind")
    for data_kind in value.data_kinds:
        if not isinstance(data_kind, str):
            raise TypeError("data_kinds must contain only strings")
        if not data_kind.strip():
            raise ValueError("data_kinds must contain only non-empty strings")
    return value


def _error_text(exc: BaseException) -> str:
    detail = str(exc)
    if detail:
        return f"{type(exc).__name__}: {detail}"
    return type(exc).__name__


def _claim_conflicts(
    statuses: list[ImporterStatus],
) -> dict[str, tuple[str, ...]]:
    """Return deterministic plugin-to-plugin extension/stem conflicts."""
    owners: dict[tuple[str, str], list[str]] = {}
    for status in statuses:
        if status.spec is None:
            continue
        for suffix in status.spec.extensions:
            owners.setdefault(("extension", suffix.casefold()), []).append(status.entry_point)
        for stem in status.spec.stems:
            owners.setdefault(("stem", stem), []).append(status.entry_point)

    conflicts: dict[str, list[str]] = {}
    for (claim_kind, claim), claim_owners in sorted(owners.items()):
        unique_owners = sorted(set(claim_owners))
        if len(unique_owners) < 2:
            continue
        for owner in unique_owners:
            others = ", ".join(repr(name) for name in unique_owners if name != owner)
            conflicts.setdefault(owner, []).append(
                f"{claim_kind} {claim!r} is also claimed by {others}"
            )
    return {name: tuple(messages) for name, messages in conflicts.items()}


@lru_cache(maxsize=1)
def discover_importers() -> tuple[ImporterStatus, ...]:
    """Load installed importer plugins, isolating failures per plugin."""
    statuses: list[ImporterStatus] = []
    grouped: dict[str, list[metadata.EntryPoint]] = {}
    for entry_point in _entry_points():
        grouped.setdefault(entry_point.name, []).append(entry_point)

    for entry_point_name in sorted(grouped):
        entries = sorted(
            grouped[entry_point_name],
            key=lambda entry_point: _distribution_name(entry_point) or "",
        )
        if len(entries) > 1:
            registrants = ", ".join(
                repr(_distribution_name(entry_point) or "unknown distribution")
                for entry_point in entries
            )
            error = f"duplicate entry point name {entry_point_name!r}; registered by {registrants}"
            for entry_point in entries:
                statuses.append(
                    ImporterStatus(
                        entry_point=entry_point_name,
                        distribution=_distribution_name(entry_point),
                        spec=None,
                        error=error,
                    )
                )
            continue

        entry_point = entries[0]
        distribution = _distribution_name(entry_point)
        try:
            # Entry-point modules and factories are third-party code. Keep a
            # stray print from corrupting `vibe-view formats --json`; load
            # failures remain visible through ImporterStatus.error.
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                spec = _validate_spec(entry_point.load(), entry_point.name)
        except (Exception, SystemExit) as exc:
            statuses.append(
                ImporterStatus(
                    entry_point=entry_point.name,
                    distribution=distribution,
                    spec=None,
                    error=_error_text(exc),
                )
            )
            continue
        statuses.append(
            ImporterStatus(
                entry_point=entry_point.name,
                distribution=distribution,
                spec=spec,
            )
        )

    conflicts = _claim_conflicts(statuses)
    resolved: list[ImporterStatus] = []
    for status in statuses:
        messages = conflicts.get(status.entry_point)
        if messages is None:
            resolved.append(status)
            continue
        resolved.append(
            ImporterStatus(
                entry_point=status.entry_point,
                distribution=status.distribution,
                spec=None,
                error="conflicting importer claims: " + "; ".join(messages),
            )
        )
    return tuple(
        sorted(
            resolved,
            key=lambda status: (status.entry_point, status.distribution or ""),
        )
    )


def clear_importer_cache() -> None:
    """Clear discovery state after installing a plugin in this process."""
    discover_importers.cache_clear()


def available_importers() -> tuple[ImporterSpec, ...]:
    """Return successfully loaded importers in deterministic order."""
    return tuple(status.spec for status in discover_importers() if status.spec is not None)


def get_importer(format_name: str) -> ImporterSpec | None:
    """Return the installed importer named ``format_name``."""
    normalized = format_name.removeprefix("plugin:")
    return next(
        (spec for spec in available_importers() if spec.format_name == normalized),
        None,
    )


def detect_importer(path: str | Path) -> ImporterSpec | None:
    """Return the first installed plugin accepting ``path``."""
    candidate = Path(path)
    for spec in available_importers():
        try:
            if spec.matches(candidate):
                return spec
        except (Exception, SystemExit):
            # A broken probe must not stop built-in formats or other plugins
            # from being considered. The plugin remains visible in `formats`.
            continue
    return None


def _as_bytes(output: ImporterOutput, format_name: str) -> bytes:
    if isinstance(output, (bytes, bytearray)):
        return bytes(output)
    close = getattr(output, "close", None)
    try:
        read = getattr(output, "read", None)
        if not callable(read):
            raise ImporterError(
                f"importer {format_name!r} returned {type(output).__name__}; "
                "expected bytes or a readable binary file object"
            )
        seek = getattr(output, "seek", None)
        if callable(seek):
            seek(0)
        payload = read()
        if not isinstance(payload, (bytes, bytearray)):
            raise ImporterError(f"importer {format_name!r} returned non-binary data")
        return bytes(payload)
    finally:
        if callable(close):
            # The complete payload has already been copied into viewer-owned
            # memory. A broken plugin cleanup hook must not terminate the host.
            with contextlib.suppress(Exception, SystemExit):
                close()


def convert_with_importer(spec: ImporterSpec, path: str | Path) -> io.BytesIO:
    """Run one plugin and validate its QVF output before returning it."""
    source = Path(path)
    try:
        payload = _as_bytes(spec.convert(source), spec.format_name)
    except ImporterError:
        raise
    except (Exception, SystemExit) as exc:
        raise ImporterError(
            f"importer {spec.format_name!r} could not read {source.name}: {exc}"
        ) from exc

    from vibeview.qvf import QVFError, QVFReader

    buffer = io.BytesIO(payload)
    reader: QVFReader | None = None
    try:
        reader = QVFReader(buffer)
        for section in reader.sections:
            for member in section.members.values():
                reader._verify_and_read(member)
    except QVFError as exc:
        raise ImporterError(
            f"importer {spec.format_name!r} produced an invalid QVF: {exc}"
        ) from exc
    finally:
        if reader is not None:
            reader.close()
    buffer.seek(0)
    return buffer
