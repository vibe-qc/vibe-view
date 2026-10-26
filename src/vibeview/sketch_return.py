"""Geometry-only return to the originating sketch; transport never changes its graph."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4
from weakref import WeakKeyDictionary

from vibeview.qvf import QVFReader
from vibeview.qvf_writer import QVFArchiveBuilder

RETURN_ENV = "VIBEVIEW_SKETCH_RETURN"
RETURN_SCHEMA = "vibe-sketch-view-return/1"
ORIGIN_KIND = "x_vibesketch.origin"
_RELAXATIONS: WeakKeyDictionary[QVFReader, dict[str, Any]] = WeakKeyDictionary()


def record_relaxation(reader: QVFReader, details: dict[str, Any]) -> None:
    """Keep actual optimizer events with their reader, without changing source files."""
    _RELAXATIONS[reader] = {**_RELAXATIONS.get(reader, {}), **details}



def origin_bytes(reader: QVFReader) -> bytes | None:
    origins = [s for s in reader.sections if s.kind == ORIGIN_KIND]
    if not origins:
        return None
    if len(origins) != 1 or "origin" not in origins[0].members:
        raise ValueError("Return needs exactly one sketch origin with an origin member")
    return reader._verify_and_read(origins[0].members["origin"])


def preserve_origin(builder: QVFArchiveBuilder, reader: QVFReader) -> None:
    payload = origin_bytes(reader)
    if payload is None:
        return
    structures = [s for s in reader.sections if s.kind == "structure"]
    if len(structures) != 1:
        raise ValueError("Sketch geometry return needs exactly one structure")
    # These are graph metadata, never inferred from the edited distances.
    extra = structures[0].model_extra or {}
    for key in ("charge", "multiplicity", "smiles", "inchi", "label"):
        if key in extra:
            builder.manifest["sections"][0][key] = extra[key]
    builder.add_json_bytes("vibesketch_origin", ORIGIN_KIND, "origin", payload)
    provenance = dict(builder.manifest.get("provenance", {}))
    provenance["x_vibeview"] = {"edits": "geometry", "relaxation": _RELAXATIONS.get(reader)}
    if reader in _RELAXATIONS:
        provenance["x_vibeview"]["relaxation"] = dict(_RELAXATIONS[reader])
    elif "x_vibeview" in reader.provenance:
        provenance["x_vibeview"] = reader.provenance["x_vibeview"]
    builder.set_provenance(provenance)


def send_geometry(reader: QVFReader, return_dir: Path) -> Path:
    """Publish the QVF first and an atomic ready record last; never recreate a lost peer."""
    from vibeview.geometry_edit import geometry_archive

    if origin_bytes(reader) is None:
        raise ValueError("This structure has no vibe-sketch origin")
    if not return_dir.is_dir():
        raise ValueError("The sketcher return directory is gone; use Save geometry (QVF) instead")
    payload = geometry_archive(reader.read_structure(), provenance=reader.provenance,
                               source_reader=reader)
    name = uuid4().hex
    target = return_dir.resolve() / f"{name}.qvf"
    record = target.with_suffix(".json")
    temporary = target.with_suffix(".qvf.tmp")
    ready = record.with_suffix(".json.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, target)
        ready.write_text(json.dumps({"schema": RETURN_SCHEMA, "path": str(target)}),
                         encoding="utf-8")
        os.replace(ready, record)
    finally:
        temporary.unlink(missing_ok=True)
        ready.unlink(missing_ok=True)
    return target
