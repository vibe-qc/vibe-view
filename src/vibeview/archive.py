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

from vibeview.qvf import MANIFEST_SCHEMA_URI, QVFReader

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
            # A basis that names no structure makes no statement (spec § 4.1);
            # there is no conventional id "structure" to check it against.
            reference = basis.get("structure_ref")
            if reference is not None:
                if not isinstance(reference, str):
                    raise ValueError(
                        f"Basis in {section.id!r} requires a string structure reference"
                    )
                target = reader.get_section(reference)
                if target.kind != "structure":
                    raise ValueError(f"Basis in {section.id!r} requires a structure target")
        if section.kind == "reaction.scheme":
            # Every participant names a structure section (spec § 5.14).
            reader.read_reaction_scheme(section.id)


def _remap_scheme(scheme: Any, mapping: dict[str, str], section_id: str) -> bool:
    """Rename the participants of a ``reaction.scheme`` payload in place.

    Returns whether any reference changed. A participant that names a
    section outside ``mapping`` is refused, as a dangling basis reference is.
    """
    changed = False
    steps = scheme.get("steps") if isinstance(scheme, dict) else None
    for step in steps if isinstance(steps, list) else ():
        for role in ("reactants", "products", "reagents"):
            entries = step.get(role) if isinstance(step, dict) else None
            for entry in entries if isinstance(entries, list) else ():
                reference = entry.get("structure_ref") if isinstance(entry, dict) else None
                if not isinstance(reference, str) or reference not in mapping:
                    raise ValueError(
                        f"Scheme in {section_id!r} references missing structure {reference!r}"
                    )
                if mapping[reference] != reference:
                    entry["structure_ref"] = mapping[reference]
                    changed = True
    return changed


def write_archive(
    output: Path,
    manifest: dict[str, Any],
    members: Iterable[tuple[str, bytes]],
) -> Path:
    """Validate every member before atomically replacing the destination."""
    # QVF spec § 3.1: version 2 was withdrawn, and a tool that writes an
    # archive it read writes qvf_version 1 and the v1 schema_uri, so a v2
    # input's schema_uri does not survive into a version-1 manifest.
    rewrites_uri = manifest.get("qvf_version") == 2 or "schema_uri" in manifest
    manifest = {**manifest, "qvf_version": 1}
    if rewrites_uri:
        manifest["schema_uri"] = MANIFEST_SCHEMA_URI
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


def _is_reference_key(key: str) -> bool:
    """A section-object field that names another section (spec § 4.1, § 5)."""
    return key.endswith("_ref") or key in ("operand_a", "operand_b")


def merge_archives(sources: Sequence[Path], output: Path) -> Path:
    """Merge sections while keeping each source's references in its namespace.

    Root metadata and viewer defaults come from the first archive. All sections
    are retained; generated IDs avoid original IDs from every input. Every
    section reference follows its target: the section-object fields
    (``structure_ref``, ``run_ref``, ``trajectory_ref``, ...), the structure
    reference inside a wavefunction's basis member (QVF §5.3), and the
    participants of a ``reaction.scheme`` (§5.14). A wavefunction that names
    no structure describes its archive's first structure, which a later
    archive's sections need named once they share an archive (§4.1).
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
                reference_keys = [key for key in section if _is_reference_key(key)]
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
                        implicit = "structure_ref" not in basis
                        reference = (
                            reader.first_structure_id if implicit else basis["structure_ref"]
                        )
                        if reference is not None:
                            if not isinstance(reference, str) or reference not in mapping:
                                raise ValueError(
                                    f"Basis in {original_id!r} references "
                                    f"missing structure {reference!r}"
                                )
                            if mapping[reference] != reference or (implicit and index > 0):
                                basis["structure_ref"] = mapping[reference]
                                replacement = json.dumps(basis).encode("utf-8")
                                member["sha256"] = hashlib.sha256(replacement).hexdigest()
                    elif section["kind"] == "reaction.scheme" and name == "scheme":
                        scheme = reader._read_json_member(original_id, name)
                        if _remap_scheme(scheme, mapping, original_id):
                            replacement = json.dumps(scheme).encode("utf-8")
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
