"""One writer for every QVF archive vibe-view produces.

Nine sites hand-rolled a manifest and a zip: the converters, the TREXIO
importer, and the ``h-add`` and ``supercell`` commands, with three member
layouts (``sections/structure.json``, ``s.json``, ``bonds/connectivity.json``),
two compression choices and provenance that named vibe-qc, ASE or a stale
``"1.0"`` for archives this package wrote. :class:`QVFArchiveBuilder` is
that manifest and zip, written once: canonical member paths
``sections/<id>/<member>.json|.bin``, a sha256 per member, deflate, no NaN
in JSON, and ``source`` naming the program that actually wrote the file.
:func:`structure_archive` covers the common case of one structure with
optional bonds.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

import vibeview

PROGRAM = "vibe-view"


def member_path(section_id: str, member: str, *, binary: bool) -> str:
    """The canonical archive path of a member."""
    return f"sections/{section_id}/{member}.{'bin' if binary else 'json'}"


class QVFArchiveBuilder:
    """Accumulate sections and members, then serialise one valid archive."""

    def __init__(
        self,
        *,
        program: str = PROGRAM,
        version: str | None = None,
        calculation: str = "",
    ) -> None:
        self._source = {
            "program": program,
            "version": vibeview.__version__ if version is None else version,
            "calculation": calculation,
        }
        self._sections: list[dict[str, Any]] = []
        self._by_id: dict[str, dict[str, Any]] = {}
        self._members: dict[str, bytes] = {}
        self._root: dict[str, Any] = {}

    # ── sections and members ────────────────────────────────────────────

    def _section(
        self, section_id: str, kind: str, section_extra: dict[str, Any] | None
    ) -> dict[str, Any]:
        section = self._by_id.get(section_id)
        if section is None:
            section = {"id": section_id, "kind": kind, "members": {}}
            self._sections.append(section)
            self._by_id[section_id] = section
        elif section["kind"] != kind:
            raise ValueError(f"section {section_id!r} is {section['kind']!r}, not {kind!r}")
        if section_extra:
            for key, value in section_extra.items():
                if key in ("id", "kind", "members"):
                    raise ValueError(f"section field {key!r} is not an extra")
                section[key] = value
        return section

    def add_json(
        self,
        section_id: str,
        kind: str,
        member: str,
        obj: Any,
        *,
        section_extra: dict[str, Any] | None = None,
    ) -> str:
        """Add a JSON member; NaN and infinity are refused, as the spec requires."""
        payload = json.dumps(obj, allow_nan=False).encode("utf-8")
        path = member_path(section_id, member, binary=False)
        self._section(section_id, kind, section_extra)["members"][member] = {
            "path": path,
            "format": "json",
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        self._members[path] = payload
        return path

    def add_binary(
        self,
        section_id: str,
        kind: str,
        member: str,
        array: np.ndarray,
        dtype: str = "float64",
        *,
        section_extra: dict[str, Any] | None = None,
    ) -> str:
        """Add a binary member stored little-endian, C order, in ``dtype``."""
        data = np.ascontiguousarray(np.asarray(array), dtype=np.dtype(dtype).newbyteorder("<"))
        payload = data.tobytes(order="C")
        path = member_path(section_id, member, binary=True)
        self._section(section_id, kind, section_extra)["members"][member] = {
            "path": path,
            "format": "binary",
            "dtype": dtype,
            "shape": [int(n) for n in data.shape],
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        self._members[path] = payload
        return path

    def set_provenance(self, provenance: dict[str, Any]) -> None:
        """The root ``provenance`` object (method, basis, charge, ...); None values are dropped."""
        self._root["provenance"] = {k: v for k, v in provenance.items() if v is not None}

    def set_root(self, key: str, value: Any) -> None:
        """Another root manifest field, such as ``viewer_defaults``."""
        if key in ("qvf_version", "source", "sections"):
            raise ValueError(f"root field {key!r} is owned by the builder")
        self._root[key] = value

    # ── output ──────────────────────────────────────────────────────────

    @property
    def manifest(self) -> dict[str, Any]:
        """The manifest as it will be written."""
        # QVF v0.1.0 §3.1: producers emit qvf_version 1.
        return {
            "qvf_version": 1,
            "source": dict(self._source),
            **self._root,
            "sections": self._sections,
        }

    def members(self) -> list[tuple[str, bytes]]:
        return list(self._members.items())

    def to_bytesio(self) -> io.BytesIO:
        """The archive in memory, positioned at the start."""
        if not self._sections:
            raise ValueError("an archive needs at least one section")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(self.manifest, allow_nan=False),
                compress_type=zipfile.ZIP_STORED,
            )
            for path, payload in self._members.items():
                archive.writestr(path, payload)
        buffer.seek(0)
        return buffer

    def write(self, path: str | Path) -> Path:
        """Write the archive atomically and verify every member by reading it back."""
        from vibeview.archive import write_archive

        if not self._sections:
            raise ValueError("an archive needs at least one section")
        return write_archive(Path(path), self.manifest, self.members())


def structure_payload(
    atoms: Sequence[dict[str, Any]],
    pbc: Sequence[bool],
    lattice_vectors: Sequence[Sequence[float]] | np.ndarray | None = None,
) -> dict[str, Any]:
    """The ``structure`` member: atoms, per-axis periodicity, derived dimensionality.

    ``dimensionality`` is written as ``sum(pbc)``, the QVF invariant
    (spec § 5.1), so a molecule in a box is a molecule and a slab is a slab.
    """
    flags = [bool(v) for v in pbc][:3]
    flags += [False] * (3 - len(flags))
    payload: dict[str, Any] = {
        "atoms": list(atoms),
        "pbc": flags,
        "dimensionality": int(sum(flags)),
    }
    if lattice_vectors is not None:
        payload["lattice_vectors"] = [
            [float(v) for v in row]
            for row in np.asarray(lattice_vectors, dtype=float).reshape(3, 3)
        ]
    return payload


def structure_archive(
    atoms: Sequence[dict[str, Any]],
    pbc: Sequence[bool],
    lattice_vectors: Sequence[Sequence[float]] | np.ndarray | None = None,
    *,
    bonds: Sequence[dict[str, Any]] | None = None,
    secondary_structure: Sequence[dict[str, Any]] | None = None,
    calculation: str = "",
    program: str = PROGRAM,
    version: str | None = None,
) -> QVFArchiveBuilder:
    """A builder holding one ``structure`` section, ready to serialise.

    ``bonds`` are ``{"i", "j", "order"}`` pairs and become the structure
    section's ``bonds`` member, which the reader prefers over a separate
    ``bonds`` section. An empty list explicitly declares no bonds; ``None``
    leaves connectivity unspecified for display inference.
    ``secondary_structure`` ranges sit on the section object, where the
    schema puts them.
    """
    builder = QVFArchiveBuilder(program=program, version=version, calculation=calculation)
    extra: dict[str, Any] = {}
    if secondary_structure:
        extra["secondary_structure"] = list(secondary_structure)
    builder.add_json(
        "structure",
        "structure",
        "structure",
        structure_payload(atoms, pbc, lattice_vectors),
        section_extra=extra,
    )
    if bonds is not None:
        builder.add_json("structure", "structure", "bonds", {"pairs": list(bonds)})
    return builder
