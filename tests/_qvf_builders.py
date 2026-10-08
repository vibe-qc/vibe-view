"""One way to write a synthetic QVF archive for a test (#41-27).

Test files used to carry their own copy of the same builder: a manifest,
a zip, and a ``NamedTemporaryFile(delete=False)`` nobody removed. They now
delegate here. ``conftest.py`` points :mod:`tempfile` at pytest's own
temporary tree for the session, so an archive written without an explicit
destination is cleaned up with the rest of the run.

* :func:`archive_bytes` builds an archive in memory from manifest sections
  and member payloads.
* :func:`write_archive` writes the same archive to ``dest``, or to a fresh
  temporary file.
* :func:`build_qvf` builds the sections too, from
  ``(section_id, kind, members)`` tuples, hashing each payload.
"""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

DEFAULT_SOURCE: dict[str, str] = {
    "program": "vibe-qc",
    "version": "0.0.0-test",
    "calculation": "synthesized fixture",
}


def vibe_qc_source(version: str, calculation: str) -> dict[str, str]:
    """A ``source`` block naming vibe-qc as the producer."""
    return {"program": "vibe-qc", "version": version, "calculation": calculation}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def archive_bytes(
    sections: Sequence[Mapping[str, Any]],
    files: Mapping[str, bytes] | None = None,
    *,
    source: Mapping[str, Any] | None = None,
    **manifest_extra: Any,
) -> bytes:
    """A version-1 archive: ``manifest.json`` plus each of ``files``.

    ``manifest_extra`` adds top-level manifest keys such as
    ``viewer_defaults`` or ``provenance``; a ``None`` value is left out.
    """
    manifest: dict[str, Any] = {
        "qvf_version": 1,
        "source": dict(source or DEFAULT_SOURCE),
        "sections": list(sections),
    }
    manifest.update({k: v for k, v in manifest_extra.items() if v is not None})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for path, data in (files or {}).items():
            archive.writestr(path, data)
    return buffer.getvalue()


def write_archive(
    sections: Sequence[Mapping[str, Any]],
    files: Mapping[str, bytes] | None = None,
    *,
    dest: Path | None = None,
    source: Mapping[str, Any] | None = None,
    **manifest_extra: Any,
) -> Path:
    """Write :func:`archive_bytes` to ``dest``, or to a new temporary file."""
    data = archive_bytes(sections, files, source=source, **manifest_extra)
    if dest is None:
        handle, name = tempfile.mkstemp(suffix=".qvf")
        with open(handle, "wb") as out:
            out.write(data)
        return Path(name)
    dest.write_bytes(data)
    return dest


def build_qvf(dest: Path | None, sections_data, *, extra=None, source=None) -> Path:
    """Write a .qvf from ``(section_id, kind, members)`` tuples.

    ``dest`` ``None`` writes a new temporary file, as :func:`write_archive`.

    Member specs mirror ``tests/test_renderers.py``:
    ``(format, path, data)``, ``(format, path, data, dtype, shape)``, or an
    explicit dict (with optional ``_bytes`` payload) for members the strict
    schema forbids ``dtype``/``shape`` on, such as ``citations.references``.
    """
    sections: list[dict] = []
    files: dict[str, bytes] = {}

    for sid, kind, members in sections_data:
        sec_members: dict[str, dict] = {}
        for mname, spec in members.items():
            if isinstance(spec, dict):
                entry = dict(spec)
                payload = entry.pop("_bytes", None)
                sec_members[mname] = entry
                if payload is not None:
                    files[entry["path"]] = payload
                continue
            if len(spec) == 3:
                fmt, mpath, mdata = spec
                dtype = shape = None
            else:
                fmt, mpath, mdata, dtype, shape = spec
            entry = {
                "path": mpath,
                "format": fmt,
                "sha256": sha256(mdata),
            }
            if fmt == "binary":
                entry["dtype"] = dtype or "uint8"
                entry["shape"] = (
                    list(shape) if shape is not None else [len(mdata)]
                )
            sec_members[mname] = entry
            files[mpath] = mdata
        section = {"id": sid, "kind": kind, "members": sec_members}
        if extra and sid in extra:
            section.update(extra[sid])
        sections.append(section)

    return write_archive(sections, files, dest=dest, source=source)
