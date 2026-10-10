"""Metadata-preserving archive transformations with atomic, verified output."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from vibeview.qvf import QVFReader

_SECTION_REFERENCES = {
    "reaction.waypoints": ("trajectory_ref",),
    "volume.difference": ("operand_a", "operand_b"),
}


def _validate_references(reader: QVFReader) -> None:
    for section in reader.sections:
        for key in _SECTION_REFERENCES.get(section.kind, ()):
            reference = getattr(section, key, None)
            if isinstance(reference, str):
                target = reader.get_section(reference)
                if key == "trajectory_ref" and target.kind != "trajectory":
                    raise ValueError(f"Section {section.id!r} requires a trajectory target")
        if section.kind == "wavefunction.gto":
            basis = reader._read_json_member(section.id, "basis")
            if not isinstance(basis, dict):
                raise ValueError(f"Basis in {section.id!r} must be a JSON object")
            reference = basis.get("structure_ref", "structure")
            if not isinstance(reference, str):
                raise ValueError(f"Basis in {section.id!r} requires a string structure reference")
            target = reader.get_section(reference)
            if target.kind != "structure":
                raise ValueError(f"Basis in {section.id!r} requires a structure target")


def write_archive(
    output: Path,
    manifest: dict[str, Any],
    members: Iterable[tuple[str, bytes]],
) -> Path:
    """Validate every member before atomically replacing the destination."""
    # QVF v0.1.0 §3.1: v2 was withdrawn; producers emit v1, readers accept both.
    manifest = {**manifest, "qvf_version": 1}
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".qvf", delete=False) as f:
        temporary = Path(f.name)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json", json.dumps(manifest), compress_type=zipfile.ZIP_STORED
            )
            written: set[str] = set()
            for path, data in members:
                if path not in written:
                    archive.writestr(path, data)
                    written.add(path)
        with QVFReader(temporary) as reader:
            for section in reader.sections:
                for member in section.members.values():
                    reader._verify_and_read(member)
            _validate_references(reader)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def merge_archives(sources: Sequence[Path], output: Path) -> Path:
    """Merge sections while keeping each source's references in its namespace.

    Root metadata and viewer defaults come from the first archive. All sections
    are retained; generated IDs avoid original IDs from every input. QVF §5.3
    also defines the structure reference inside a wavefunction's basis member.
    """
    if len(sources) < 2:
        raise ValueError("Need at least two .qvf files to merge.")
    with ExitStack() as stack:
        readers = [stack.enter_context(QVFReader(path)) for path in sources]
        manifests = [reader.manifest_json() for reader in readers]
        reserved = {section.id for reader in readers for section in reader.sections}
        used: set[str] = set()
        maps: list[dict[str, str]] = []
        for reader in readers:
            mapping: dict[str, str] = {}
            for section in reader.sections:
                new_id = section.id
                if new_id in used:
                    suffix = 1
                    while f"{section.id}_{suffix}" in used | reserved:
                        suffix += 1
                    new_id = f"{section.id}_{suffix}"
                mapping[section.id] = new_id
                used.add(new_id)
            maps.append(mapping)

        result = manifests[0]
        result["source"]["calculation"] = f"merge of {len(sources)} files"
        sections: list[dict[str, Any]] = []
        paths: set[str] = {"manifest.json"}
        copies: list[tuple[QVFReader, str, str, str, bytes | None]] = []
        for index, (reader, manifest, mapping) in enumerate(
            zip(readers, manifests, maps, strict=True)
        ):
            path_map: dict[tuple[str, str], str] = {}
            for section in manifest["sections"]:
                original_id = section["id"]
                section["id"] = mapping[original_id]
                reference_keys = _SECTION_REFERENCES.get(section["kind"], ())
                for key in reference_keys:
                    value = section.get(key)
                    if isinstance(value, str):
                        if value not in mapping:
                            raise ValueError(
                                f"Section {original_id!r} references missing section {value!r}"
                            )
                        if (
                            key == "trajectory_ref"
                            and reader.get_section(value).kind != "trajectory"
                        ):
                            raise ValueError(
                                f"Section {original_id!r} requires a trajectory target"
                            )
                        section[key] = mapping[value]
                for name, member in section["members"].items():
                    original_path = member["path"]
                    replacement = None
                    if section["kind"] == "wavefunction.gto" and name == "basis":
                        basis = reader._read_json_member(original_id, name)
                        if not isinstance(basis, dict):
                            raise ValueError(f"Basis in {original_id!r} must be a JSON object")
                        reference = basis.get("structure_ref", "structure")
                        if not isinstance(reference, str) or reference not in mapping:
                            raise ValueError(
                                f"Basis in {original_id!r} references "
                                f"missing structure {reference!r}"
                            )
                        if mapping[reference] != reference:
                            basis["structure_ref"] = mapping[reference]
                            replacement = json.dumps(basis).encode("utf-8")
                            member["sha256"] = hashlib.sha256(replacement).hexdigest()
                    path_key = (original_path, member["sha256"])
                    if path_key not in path_map:
                        candidate = original_path if index == 0 else f"{index}/{original_path}"
                        suffix = 1
                        while candidate in paths:
                            candidate = f"{index}_{suffix}/{original_path}"
                            suffix += 1
                        path_map[path_key] = candidate
                        paths.add(candidate)
                    new_path = path_map[path_key]
                    member["path"] = new_path
                    copies.append((reader, original_id, name, new_path, replacement))
                sections.append(section)
        result["sections"] = sections

        def members() -> Iterator[tuple[str, bytes]]:
            for reader, sid, name, path, replacement in copies:
                # Verify even aliases sharing an output path: conflicting source
                # hashes must not be hidden by output-member deduplication.
                data = reader._verify_and_read(reader.get_section(sid).members[name])
                yield path, replacement if replacement is not None else data

        return write_archive(output, result, members())
