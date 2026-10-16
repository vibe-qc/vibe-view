"""QVF reader — the core file-open lifecycle.

Responsibilities:
- Open .qvf zip archive, extract + validate manifest.json
- Parse sections into pydantic models
- Verify sha256 of every binary member before use (Rule 4)
- Lazy extraction of volumetric .dat blobs
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import io
import json
import math
import re
import sys
import zipfile
import zlib
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Literal, cast

import jsonschema
import numpy as np
from jsonschema.protocols import Validator
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from vibeview.bonds import (
    Bond,
    BondSearchLimitError,
    DegenerateLatticeError,
    check_bond_search,
    interplanar_spacings,
    periodic_image_shells,
    structure_bonds,
)
from vibeview.elements import SYMBOL_TO_Z, z_to_symbol
from vibeview.units import BOHR_TO_ANGSTROM, HARTREE_TO_EV

# A bond as every reader path emits it: ``(i, j, order, image)``, where
# ``image`` is the integer lattice translation applied to ``j``. In-cell bonds
# carry ``(0, 0, 0)``; see ``vibeview.bonds`` for why the image is identity.

# ── Pydantic models for manifest.json ─────────────────────────────────────


class Source(BaseModel):
    program: str
    version: str
    calculation: str


class MemberSpec(BaseModel):
    """One member of a section (a file inside the zip)."""

    path: str
    format: str  # "json" or "binary"
    sha256: str
    dtype: str | None = None
    shape: list[int] | None = None
    # The declared type of an opaque binary member, such as a ``preview``
    # image (QVF spec § 4.2, § 5.15); a reader learns it here, never by
    # sniffing the bytes.
    media_type: str | None = None

    @field_validator("sha256")
    @classmethod
    def _validate_sha256(cls, v: str) -> str:
        if len(v) != 64 or not all(c in "0123456789abcdef" for c in v):
            raise ValueError(f"sha256 must be 64 hex chars, got: {v!r}")
        return v


class Section(BaseModel):
    """One section from the manifest."""

    model_config = ConfigDict(extra="allow")
    id: str
    kind: str
    members: dict[str, MemberSpec] = Field(default_factory=dict)
    component: str | None = None
    # Section-level cross-reference for `reaction.waypoints`. Other
    # cross-refs (e.g. volume.difference.operand_a/_b) come through
    # model_extra.
    trajectory_ref: str | None = None


class ViewerDefaults(BaseModel):
    """Viewer defaults from manifest.json.

    Known fields (auto_open) are explicit. All other keys are per-section
    hints (e.g. ``"density": {"isovalue": 0.05, ...}``) and are captured
    via Pydantic's extra="allow" in ``model_extra``.
    """

    model_config = ConfigDict(extra="allow")
    auto_open: list[str] = Field(default_factory=list)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="allow")
    qvf_version: int
    source: Source
    sections: list[Section]
    viewer_defaults: ViewerDefaults | None = None


# ── Exceptions ────────────────────────────────────────────────────────────


class QVFError(Exception):
    """Base for all QVF reader errors."""


class QVFOpenError(QVFError):
    """Cannot open/read the file; validation may attach its reference report."""

    def __init__(
        self, message: str, *, validation_report: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.validation_report = validation_report


# What opening an archive or reading one of its entries can raise besides the
# QVF-level checks: an unreadable path or a directory (OSError), a damaged
# container or entry (BadZipFile, including "Bad CRC-32"; zlib.error; EOFError
# on truncated data), an unsupported compression method (NotImplementedError)
# and an encrypted entry (RuntimeError). ``QVFReader`` reports each as
# ``QVFOpenError`` so every caller that handles ``QVFError`` fails cleanly
# (#39-18, #39-35).
_ARCHIVE_READ_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    EOFError,
    zipfile.BadZipFile,
    zlib.error,
    NotImplementedError,
    RuntimeError,
)


# What ``json.loads`` can raise on hostile input besides JSONDecodeError (a
# ValueError subclass): RecursionError on deep nesting, MemoryError on a huge
# document, and ValueError for e.g. an integer beyond the digit limit.
_JSON_PARSE_ERRORS: tuple[type[BaseException], ...] = (RecursionError, MemoryError, ValueError)


# Deepest JSON nesting a reader accepts in manifest.json or a JSON member.
# Real archives stay below ten levels; the bound only keeps hostile input
# away from the recursive walkers in json, jsonschema and pprint.
_MAX_JSON_DEPTH = 256


def _json_depth_exceeds(value: Any, limit: int) -> bool:
    """Whether parsed JSON ``value`` nests more than ``limit`` containers deep.

    Iterative, so it cannot itself run out of recursion.
    """
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            children: Any = item.values()
        elif isinstance(item, list):
            children = item
        else:
            continue
        if depth > limit:
            return True
        stack.extend((child, depth + 1) for child in children if isinstance(child, (dict, list)))
    return False


_DOUBLE_MAX = sys.float_info.max


def _first_nonfinite_number(value: Any) -> tuple[str, str] | None:
    """(JSON pointer, description) of the first number no double can hold.

    ``json.loads`` accepts ``NaN``, ``Infinity`` and ``1e400`` (inf), none of
    which is JSON (RFC 8259 § 6), and integers of any size. Every consumer of
    manifest numbers converts with ``float()``/``int()`` or numpy, which
    raises OverflowError/ValueError on these (#39-35). No real archive
    carries one. Iterative, like :func:`_json_depth_exceeds`.
    """
    stack: list[tuple[Any, str]] = [(value, "")]
    while stack:
        item, where = stack.pop()
        if isinstance(item, dict):
            stack.extend((child, f"{where}/{key}") for key, child in item.items())
        elif isinstance(item, list):
            stack.extend((child, f"{where}/{i}") for i, child in enumerate(item))
        elif isinstance(item, float) and not np.isfinite(item):
            return where or "/", repr(item)
        elif isinstance(item, int) and not isinstance(item, bool) and abs(item) > _DOUBLE_MAX:
            return where or "/", f"an integer of {len(str(abs(item)))} digits"
    return None


# A ``\\uD800``-``\\uDFFF`` escape in JSON text. Only an escape can put a
# surrogate code point into a parsed string: a strict UTF-8 decode refuses
# encoded surrogates, and ``json.loads`` joins a valid escaped pair into one
# character. So text without a match needs no walk.
_SURROGATE_ESCAPE = re.compile(r"\\u[dD][89abcdefABCDEF]")
_SURROGATE = re.compile("[\ud800-\udfff]")


def _first_lone_surrogate(value: Any) -> str | None:
    """JSON pointer of the first string or key holding an unpaired surrogate.

    ``json.loads`` accepts an unpaired UTF-16 surrogate escape such as
    ``"\\ud800"``, but the result is not Unicode text: no UTF-8 encoding
    exists, so printing, writing or serving the string raises
    UnicodeEncodeError wherever it goes (#39-35). RFC 8259 § 8.2 leaves the
    behaviour undefined, and I-JSON (RFC 7493 § 2.1) forbids it. A key is
    named by its parent's pointer, since the key itself cannot be shown.
    Iterative, like :func:`_json_depth_exceeds`.
    """
    stack: list[tuple[Any, str]] = [(value, "")]
    while stack:
        item, where = stack.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                if _SURROGATE.search(key):
                    return f"{where or '/'} (an object key)"
                stack.append((child, f"{where}/{key}"))
        elif isinstance(item, list):
            stack.extend((child, f"{where}/{i}") for i, child in enumerate(item))
        elif isinstance(item, str) and _SURROGATE.search(item):
            return where or "/"
    return None


def _lone_surrogate_in(text: str, value: Any) -> str | None:
    """:func:`_first_lone_surrogate` of ``value``, parsed from ``text``."""
    return _first_lone_surrogate(value) if _SURROGATE_ESCAPE.search(text) else None


#: Largest ``atomic_number`` the reader accepts: the int64 maximum.
#: Numbers beyond the element table are kept on purpose (a converter labels
#: ``Atom(999)`` "Z999", and frame metadata keeps unknown display labels);
#: the bond search stores atomic numbers as int64.
MAX_ATOMIC_NUMBER = 2**63 - 1


def _atomic_number(value: Any, field: str) -> int:
    """An atomic number from 0 (the schema's minimum) to the int64 maximum.

    The schema bounds ``atomic_number`` below only. 2**63 or more used to
    open, and then made the bond search raise OverflowError converting it to
    int64 in ``h-add`` (#39-35).
    """
    number = _json_integer(value, field)
    if not 0 <= number <= MAX_ATOMIC_NUMBER:
        shown = str(number) if abs(number) < 10**30 else f"a {number.bit_length()}-bit integer"
        raise QVFError(
            f"{field} is {shown}, outside 0..{MAX_ATOMIC_NUMBER} (a 64-bit signed integer)"
        )
    return number


def _validation_message(error: Exception) -> str:
    """``str(error)`` for a schema failure, or a short form if that fails.

    jsonschema's message pretty-prints the offending instance, which
    recurses without bound on a deeply nested manifest value (#39-35).
    """
    try:
        return str(error)
    except RecursionError:
        path = "/".join(str(p) for p in getattr(error, "absolute_path", ()))
        return (
            f"manifest.json failed schema validation at /{path}"
            " (the offending value is nested too deeply to show)"
        )


def _describe_exception(error: BaseException, limit: int = 300) -> str:
    """``"Type: message"`` for an unexpected error, bounded and never raising."""
    try:
        text = str(error)
    except Exception:  # e.g. RecursionError rendering a deep value
        text = ""
    if len(text) > limit:
        text = text[:limit] + "..."
    return f"{type(error).__name__}: {text}" if text else type(error).__name__


class ManifestValidationError(QVFError):
    """manifest.json failed JSON Schema validation."""


class SHA256MismatchError(QVFError):
    """sha256 of a member does not match the manifest. Hard error for that section."""


@dataclass(frozen=True)
class _SectionError:
    message: str
    kind: str


class SectionNotFoundError(QVFError):
    """Requested section id not found in manifest."""


class CriticalSectionUnsupportedError(QVFError):
    """A section (or root extension) flagged ``critical: true`` has a kind the
    viewer cannot render. Per the QVF spec (§2.2 / §5.5 / §7) the consumer
    MUST refuse to open such a file rather than silently render a partial,
    misleading view."""


# Minimum run lengths. One residue cannot be a helix: 3.6 residues is a
# single turn, and a "strand" of two is just two adjacent atoms. Shorter
# runs than these are noise from the per-residue test and become coil.
_MIN_HELIX_RUN = 4
_MIN_STRAND_RUN = 3


def _virtual_angle(p0: np.ndarray, p1: np.ndarray, p2: np.ndarray) -> float:
    """CA(i-1)-CA(i)-CA(i+1) angle, degrees."""
    a, b = p0 - p1, p2 - p1
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0.0 or nb == 0.0:
        return float("nan")
    return float(np.degrees(np.arccos(np.clip(np.dot(a, b) / (na * nb), -1.0, 1.0))))


def _virtual_dihedral(p0: np.ndarray, p1: np.ndarray, p2: np.ndarray, p3: np.ndarray) -> float:
    """CA(i)-CA(i+1)-CA(i+2)-CA(i+3) dihedral, degrees, signed.

    The sign convention here is fixed by measurement, not by assertion:
    an ideal right-handed alpha-helix gives +50 and its left-handed
    mirror -50 (pinned in ``tests/test_secondary_structure.py``). Getting
    this backwards would make every helix read as a left-handed one.
    """
    b0, b1, b2 = p0 - p1, p2 - p1, p3 - p2
    n1 = np.linalg.norm(b1)
    if n1 == 0.0:
        return float("nan")
    b1 = b1 / n1
    v = b0 - np.dot(b0, b1) * b1
    w = b2 - np.dot(b2, b1) * b1
    x, y = np.dot(v, w), np.dot(np.cross(b1, v), w)
    if x == 0.0 and y == 0.0:
        return float("nan")
    return float(np.degrees(np.arctan2(y, x)))


def _collapse_short_runs(labels: list[str]) -> list[str]:
    """Demote runs shorter than their minimum to coil."""
    out = list(labels)
    i = 0
    while i < len(out):
        j = i
        while j < len(out) and out[j] == out[i]:
            j += 1
        need = {"H": _MIN_HELIX_RUN, "E": _MIN_STRAND_RUN}.get(out[i])
        if need is not None and (j - i) < need:
            for k in range(i, j):
                out[k] = "C"
        i = j
    return out


def _assign_secondary_structure(trace: np.ndarray) -> list[str]:
    """Per-CA "H"/"E"/"C" for one chain's CA trace.

    See :meth:`StructureData.secondary_structure` for the provenance of
    the descriptors and of the windows below.
    """
    n = len(trace)
    if n == 0:
        return []
    labels = ["C"] * n

    def dist(i: int, k: int) -> float:
        if i + k >= n:
            return float("nan")
        return float(np.linalg.norm(trace[i + k] - trace[i]))

    for i in range(n):
        d2, d3, d4 = dist(i, 2), dist(i, 3), dist(i, 4)
        tau = (
            _virtual_angle(trace[i - 1], trace[i], trace[i + 1])
            if 0 < i < n - 1
            else float("nan")
        )
        alpha = (
            _virtual_dihedral(trace[i], trace[i + 1], trace[i + 2], trace[i + 3])
            if i + 3 < n
            else float("nan")
        )

        # Helix: the i/i+3 and i/i+4 contacts are the signature — a helix
        # brings residues one turn apart back into van der Waals contact,
        # which nothing else does. Ideal: d3 5.05, d4 6.20, tau 90.4,
        # alpha +50.0.
        if (
            not np.isnan(d3)
            and 4.4 <= d3 <= 6.0
            # d4 runs off the end for the last four residues of a chain.
            # Requiring it there would clip the final turn of a helix that
            # ends at the C-terminus.
            and (np.isnan(d4) or 4.8 <= d4 <= 7.0)
            and (np.isnan(tau) or 78.0 <= tau <= 105.0)
            and (np.isnan(alpha) or 25.0 <= alpha <= 80.0)
        ):
            labels[i] = "H"
            continue

        # Strand: extended, so every separation grows nearly linearly and
        # the dihedral sits near 180. Ideal: d2 6.68, d3 10.20, d4 13.36,
        # tau 120.7, alpha 180.0.
        if (
            not np.isnan(d2)
            and not np.isnan(d3)
            and d2 >= 6.2
            and d3 >= 9.5
            and (np.isnan(d4) or d4 >= 12.0)
            and (np.isnan(tau) or tau >= 108.0)
            and (np.isnan(alpha) or abs(alpha) >= 135.0)
        ):
            labels[i] = "E"

    # A detection at i describes the whole window starting at i, so the
    # residues that window covers inherit the label; without this the last
    # turn of every helix reads as coil.
    #
    # This reads `core` and writes `spread`. Bleeding in place instead
    # makes the loop read labels it has just written, so a single core
    # detection chain-reacts forward three residues at a time until it
    # meets a non-coil label. Measured on DHFR, that inflated a sane
    # 12% H / 14% E / 74% C core into 57% H / 33% E / 10% C, with a
    # 24-residue "strand" at the N-terminus.
    core = list(labels)
    spread = list(labels)
    for i, label in enumerate(core):
        reach = {"H": 4, "E": 4}.get(label)
        if reach is None:
            continue
        for k in range(i + 1, min(i + reach, n)):
            if spread[k] == "C":
                spread[k] = label

    return _collapse_short_runs(spread)


@dataclass
class Atom:
    """One atom. The biomolecular fields are optional and default to None.

    Chemistry files (XYZ, a computed structure) carry none of them; a PDB
    carries all five, and the cartoon/ribbon work (roadmap workstream D)
    is built on them — ``atom_name`` identifies the backbone trace,
    ``chain_id`` + ``residue_seq`` order it, ``residue_name`` colours it,
    ``b_factor`` carries the temperature factor (or, for a predicted
    structure, the per-residue confidence a predictor writes into that
    column). Optional rather than required so every existing archive, and
    every non-biomolecular producer, keeps working untouched.
    """

    symbol: str
    position: np.ndarray  # [3] float64, angstroms
    atomic_number: int
    atom_name: str | None = None      # PDB cols 13-16, e.g. "CA" (unpadded)
    residue_name: str | None = None   # cols 18-20, e.g. "ALA"
    residue_seq: int | None = None    # cols 23-26
    chain_id: str | None = None       # col 22
    b_factor: float | None = None     # cols 61-66



def _opt_str(value: Any) -> str | None:
    """A trimmed string, or None when absent/blank."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


#: Section-id prefix of wavefunction overlays computed by re-localization
#: (``vibeview.relocalize``); a geometry edit invalidates every one of them.
RELOCALIZED_PREFIX = "wf_relocalized_"


def _atom_from_json(entry: Mapping[str, Any], position: np.ndarray | None = None) -> Atom:
    """One :class:`Atom` from a validated JSON atom entry.

    ``position`` overrides the entry's (frame and scan metadata carry
    identities, not coordinates). The optional biomolecular fields are read
    when present, so a vibrations or scan entry that carries none gets None.
    """
    if position is None:
        position = np.array(entry["position"], dtype=np.float64)
        # The schema pins a structure atom's position; a vibrations entry is
        # not schema-checked, and a two-number position broke ``animate``
        # outside the reader (#39-35).
        if position.shape != (3,) or not np.all(np.isfinite(position)):
            raise QVFError(
                f"atom position must be three finite numbers, got shape {position.shape}"
            )
    return Atom(
        symbol=entry["symbol"],
        position=position,
        atomic_number=_atomic_number(entry["atomic_number"], "atomic_number"),
        atom_name=_opt_str(entry.get("atom_name")),
        residue_name=_opt_str(entry.get("residue_name")),
        residue_seq=_opt_int(entry.get("residue_seq")),
        chain_id=_opt_str(entry.get("chain_id")),
        b_factor=_opt_float(entry.get("b_factor")),
    )


def _str_or_none(value: Any) -> str | None:
    """``str(value)``, or None when the value is absent (no trimming)."""
    return None if value is None else str(value)


def _opt_int(value: Any) -> int | None:
    """An int, or None when absent/unparseable (never raises on bad input:
    a malformed residue number must not sink the whole structure)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_float(value: Any) -> float | None:
    """A float, or None when absent/unparseable (never raises: one bad
    b-factor must not sink the whole structure)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# QVF `secondary_structure[].type` (spec § 5.1) to this module's per-CA
# descriptor. Anything else falls back to coil rather than raising: the
# schema pins the enum, but a hand-built archive is not obliged to pass it.
_SS_TYPE_TO_LABEL = {"helix": "H", "sheet": "E", "coil": "C"}


def _resolve_pbc(raw: dict[str, Any]) -> tuple[tuple[bool, bool, bool], int]:
    """Resolve per-axis periodicity from a ``structure`` payload.

    ``pbc`` is normative when present: it is per-axis, so it is strictly more
    expressive than the scalar ``dimensionality``. When only ``dimensionality``
    is given, the first ``dim`` axes are the periodic ones — the convention the
    the QVF format uses for its lattice matrix (rows are the cell vectors).

    ``dimensionality`` is therefore *derived*, never read alongside ``pbc``: the
    QVF invariant is ``dimensionality == sum(pbc)`` (spec § 5.1). An archive that
    carries both and disagrees is self-contradictory, and nothing here can tell
    which field is the lie. Guessing one is exactly how a 2D slab came to be
    drawn inside a phantom vacuum box, so we refuse the archive instead.

    Note ``pbc`` need not be a contiguous prefix — ``[True, False, True]`` is a
    legal slab periodic in x and z, and the spec deliberately does not require
    the periodic axes to be the leading ones. ``dim`` then only counts the
    periodic axes; it does not say *which*. Consumers that need the axes must
    read ``pbc``, never reconstruct them from ``dim``.
    """
    raw_pbc = raw.get("pbc")
    raw_dim = raw.get("dimensionality")
    dim = int(raw_dim) if raw_dim is not None else None

    if raw_pbc is not None:
        flags = [bool(x) for x in raw_pbc][:3]
        flags += [False] * (3 - len(flags))
        n_periodic = sum(flags)
        if dim is not None and dim != n_periodic:
            raise QVFError(
                f"structure payload is self-contradictory: pbc={flags} has "
                f"{n_periodic} periodic axes but dimensionality={dim}; the QVF "
                "invariant is dimensionality == sum(pbc) (spec § 5.1)"
            )
        return (flags[0], flags[1], flags[2]), n_periodic

    if dim is not None:
        flags = [i < dim for i in range(3)]
        return (flags[0], flags[1], flags[2]), dim

    return (False, False, False), 0


def clamp_replication(
    replication: tuple[int, int, int], pbc: tuple[bool, bool, bool]
) -> tuple[int, int, int]:
    """Force the replication count to 1 on every non-periodic axis.

    Replicating along a non-periodic axis would tile the structure along a
    synthesized, non-physical lattice column — for a 2D slab that fabricates a
    stack of sheets that the calculation never contained.
    """
    return tuple(  # type: ignore[return-value]
        max(1, int(n)) if p else 1 for n, p in zip(replication, pbc, strict=True)
    )


#: Sentinel default for :attr:`StructureData.dim`: derive it from ``pbc``.
_DIM_FROM_PBC = -1


@dataclass
class StructureData:
    """A unit cell, or a molecule when ``pbc`` is all-False.

    For ``dim < 3`` the lattice rows ``dim..2`` are **non-physical**: the producer
    synthesizes them so AO integrals and spglib always see a full-rank 3x3
    matrix, and the SCF energy is provably invariant to their length. They are
    NOT cell edges and must never be drawn, replicated along, or used as a
    geometric extent. ``pbc`` is the only signal that separates a synthesized
    column from a real lattice vector — a synthesized 30-bohr normal and a real
    30-bohr vacuum gap are numerically identical in ``lattice_vectors``.
    """

    atoms: list[Atom]
    pbc: tuple[bool, bool, bool]
    lattice_vectors: np.ndarray | None  # [3,3] or None for molecules
    # explicit (i, j, order, image) where image is the integer lattice
    # translation applied to j; (0,0,0) for an ordinary in-cell bond.
    bonds: list[Bond] | None
    # Periodic-axis count, always ``sum(pbc)`` (QVF spec § 5.1: the derived
    # ``dimensionality`` invariant). Omit it and it is derived; pass it and it
    # must agree with ``pbc`` (#35-16). It carries no axis identity: which
    # axes are periodic is ``pbc``'s alone.
    dim: int = _DIM_FROM_PBC

    # Producer-supplied biomolecule metadata from the `structure` section
    # object (QVF spec § 5.1). None when the archive carries none of it,
    # which is the case for every plain chemistry structure. Where these
    # ARE supplied they take precedence over this class's own derivation:
    # a producer knows what CA geometry cannot reveal (an alpha from a pi
    # from a 3-10 helix, a beta bridge from a sheet).
    supplied_residues: list[dict[str, Any]] | None = None
    supplied_chains: list[str] | None = None
    supplied_secondary_structure: list[dict[str, Any]] | None = None

    # Biomolecular identity derived once per instance: the cartoon and the
    # colour schemes ask for the chain map per chain and per frame, and on a
    # 20k-atom protein each walk costs milliseconds. The cache assumes what
    # every caller does, that residue identities are not edited in place
    # after a read; positions may change freely (they are not part of it).
    _has_residues: bool | None = field(default=None, init=False, repr=False, compare=False)
    _chain_map: dict[str, list[tuple[int, list[int]]]] | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _ca_all: list[tuple[str, int, int]] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        n_periodic = sum(bool(flag) for flag in self.pbc)
        if self.dim == _DIM_FROM_PBC:
            self.dim = n_periodic
        elif self.dim != n_periodic:
            raise ValueError(
                f"dim={self.dim} contradicts pbc={tuple(self.pbc)}: dim must equal "
                f"the number of periodic axes ({n_periodic})"
            )

    @property
    def has_residues(self) -> bool:
        """True when this structure carries biomolecular identity.

        Either carrier counts: per-atom residue numbers, or a
        producer-supplied ``residues`` list on the section object. A
        producer may legitimately supply the second without the first.
        """
        if self._has_residues is None:
            self._has_residues = bool(self.supplied_residues) or any(
                a.residue_seq is not None for a in self.atoms
            )
        return self._has_residues

    def chain_ids(self) -> list[str]:
        """Chain ids, in presentation order.

        The producer-supplied ``chains`` list when the archive has one
        (it may order chains for presentation, and may name a chain that
        contributes no residue), otherwise the chains :meth:`chains`
        derived.
        """
        if self.supplied_chains:
            return [str(c) for c in self.supplied_chains]
        return list(self.chains().keys())

    def _supplied_chain_map(self) -> dict[str, list[tuple[int, list[int]]]] | None:
        """:meth:`chains` shape built from ``supplied_residues``, or None.

        Returns None when nothing usable was supplied, so the caller falls
        back to derivation rather than rendering an empty ribbon: an
        out-of-range ``atom_indices`` entry is a producer bug, and blanking
        the cartoon over it would be the wrong failure.
        """
        if not self.supplied_residues:
            return None
        n = len(self.atoms)
        out: dict[str, list[tuple[int, list[int]]]] = {}
        for residue in self.supplied_residues:
            if not isinstance(residue, dict):
                continue
            seq = _opt_int(residue.get("seq"))
            if seq is None:
                continue
            indices = [
                i
                for i in (_opt_int(v) for v in residue.get("atom_indices") or ())
                if i is not None and 0 <= i < n
            ]
            if not indices:
                continue
            out.setdefault(str(residue.get("chain", "")), []).append((seq, indices))
        return out or None

    def chains(self) -> dict[str, list[tuple[int, list[int]]]]:
        """``{chain_id: [(residue_seq, [atom indices]), ...]}``.

        A producer-supplied ``residues`` list wins over the derivation
        below (spec § 5.1 precedence). Everything after this paragraph
        describes that derivation, which is what runs for a PDB import and
        for every archive whose producer supplied nothing.

        Residues appear in the order the file lists them, which for a PDB
        is the order along the chain — the ribbon must follow the actual
        connectivity, and sorting by residue number would silently
        reorder insertion codes or non-monotonic numbering. Atoms with no
        residue identity are skipped rather than lumped into a fake
        chain; a file with none simply yields ``{}``.

        Grouping is by **contiguous run** of (chain, sequence, name), not
        by a global key. PDB residue numbers are only four columns wide
        and wrap at 9999, so a solvated system reuses them: keying
        globally merged a lipid and a water that happened to share
        number 1001 into one 80-atom "residue" (measured: 1,256 such
        collisions on a 150k-atom membrane protein, 9,999 buckets where
        the file holds 12,010 residues). Contiguity is also what makes
        insertion codes behave, since those too are listed in order.
        """
        if self._chain_map is None:
            supplied = self._supplied_chain_map()
            self._chain_map = supplied if supplied is not None else self._derive_chains()
        return self._chain_map

    def _derive_chains(self) -> dict[str, list[tuple[int, list[int]]]]:
        """The :meth:`chains` derivation from per-atom residue identity."""
        out: dict[str, list[tuple[int, list[int]]]] = {}
        bucket: list[int] | None = None
        prev_key: tuple[str, int, str | None] | None = None
        for i, atom in enumerate(self.atoms):
            if atom.residue_seq is None:
                bucket, prev_key = None, None  # a gap ends the current run
                continue
            chain = atom.chain_id or ""
            key = (chain, atom.residue_seq, atom.residue_name)
            if key != prev_key:
                bucket = []
                out.setdefault(chain, []).append((atom.residue_seq, bucket))
                prev_key = key
            assert bucket is not None
            bucket.append(i)
        return out

    def ca_residues(
        self, chain_id: str | None = None
    ) -> list[tuple[str, int, int]]:
        """``(chain, residue_seq, CA atom index)`` for each CA-bearing residue.

        The single ordering used by :meth:`backbone_trace` and by the
        supplied-assignment branch of :meth:`secondary_structure`, so the
        two cannot drift apart: a per-CA label list that did not line up
        with the trace it is zipped against would mislabel a whole chain.

        **Public because renderers need it.** Anything building a per-CA
        array to zip against ``backbone_trace()`` — a selection mask, a
        colour array — must walk this exact ordering rather than
        re-deriving it, since a second copy of the walk can drift. It was
        private until 2026-07-26, which forced the cartoon renderer to
        reach in through ``getattr`` and carry a duplicated fallback.
        """
        if self._ca_all is None:
            picked: list[tuple[str, int, int]] = []
            for chain, residues in self.chains().items():
                for seq, indices in residues:
                    for i in indices:
                        if self.atoms[i].atom_name == "CA":
                            picked.append((chain, seq, i))
                            break
            self._ca_all = picked
        if chain_id is None:
            return list(self._ca_all)
        return [entry for entry in self._ca_all if entry[0] == chain_id]

    def backbone_trace(self, chain_id: str | None = None) -> np.ndarray:
        """Alpha-carbon coordinates, in chain order — the ribbon spine.

        ``[N, 3]``; empty when the structure has no CA atoms. Pass
        ``chain_id`` for a single chain, or leave it None to concatenate
        every chain (callers drawing one ribbon per chain should ask per
        chain, so the spline does not jump between them).
        """
        picked = [self.atoms[i].position for _c, _s, i in self.ca_residues(chain_id)]
        if not picked:
            return np.zeros((0, 3), dtype=np.float64)
        return np.asarray(picked, dtype=np.float64)

    def _supplied_labels(self, chain: str, *, detailed: bool = False) -> list[str] | None:
        """Per-CA labels for ``chain`` from the supplied ranges, or None.

        None when nothing was supplied for this chain, so the caller falls
        back to its own geometric assignment. Precedence is decided **per
        chain**: a producer that annotates chain A and says nothing about
        chain B means B is unannotated, not that B is entirely coil.
        Residues the supplied ranges do not cover are coil, which is what
        a range list omitting the loops between its helices means.
        """
        if not self.supplied_secondary_structure:
            return None
        ranges: list[tuple[int, int, str]] = []
        for entry in self.supplied_secondary_structure:
            if not isinstance(entry, dict) or str(entry.get("chain", "")) != chain:
                continue
            start = _opt_int(entry.get("start_seq"))
            end = _opt_int(entry.get("end_seq"))
            if start is None or end is None:
                continue
            label = _SS_TYPE_TO_LABEL.get(str(entry.get("type", "")), "C")
            if detailed:
                # Optional viewer extension within the schema's open range
                # object. Keep the normative helix/sheet/coil type unchanged.
                subtype = str(entry.get("subtype", ""))
                if label == "H":
                    label = {"alpha": "H", "pi": "I", "3_10": "G"}.get(subtype, "H")
                elif label == "E" and subtype == "bridge":
                    label = "B"
            ranges.append((min(start, end), max(start, end), label))
        if not ranges:
            return None
        out: list[str] = []
        for _chain, seq, _i in self.ca_residues(chain):
            label = "C"
            for start, end, ranged_label in ranges:
                if start <= seq <= end:
                    label = ranged_label
                    break
            out.append(label)
        return out

    def secondary_structure(
        self, chain_id: str | None = None, *, detailed: bool = False
    ) -> list[str]:
        """Per-alpha-carbon secondary structure, aligned with
        :meth:`backbone_trace`.

        One character per CA: ``"H"`` helix, ``"E"`` extended strand,
        ``"C"`` coil. The list is always the same length as the trace for
        the same ``chain_id``, so a renderer can zip the two.

        With ``detailed=True``, supplied subtype annotations additionally
        return ``G`` (3-10 helix), ``I`` (pi helix) and ``B`` (beta bridge).
        Geometric assignment always returns the coarse H/E/C labels.

        A producer-supplied ``secondary_structure`` range list wins, per
        chain, over the geometric assignment described below (spec § 5.1
        precedence). That is not a preference for tidiness: the CA-only
        assignment below genuinely cannot tell an alpha- from a pi- from a
        3-10 helix, or a beta-bridge from a sheet, and a producer can.

        Assignment is from the CA trace alone. Real DSSP (Kabsch &
        Sander, Biopolymers 22, 2577 (1983)) reads backbone N/CA/C/O and
        assigns from hydrogen-bond energies; a QVF is not guaranteed to
        carry O at all, and vibe-view needs this only to decide ribbon
        geometry. CA-only assignment is an established family of
        approximations to it, the reference implementation being P-SEA
        (Labesse, Colloc'h, Pothier & Mornon, Comput. Appl. Biosci. 13,
        291 (1997)), which discriminates on CA(i)-CA(i+k) distances plus
        the virtual CA angle and dihedral. This uses those same
        descriptors.

        The numeric windows below are **this implementation's**, chosen
        to bracket ideal geometry rather than transcribed from the paper.
        ``tests/test_secondary_structure.py`` constructs an ideal
        right-handed alpha-helix (1.5 A rise, 100 deg/residue, CA on a
        2.3 A radius) and an ideal extended strand (3.34 A rise) and
        measures, under this module's dihedral sign convention:

            helix   d2 5.43  d3  5.05  d4  6.20  tau  90.4  alpha   50.0
            strand  d2 6.68  d3 10.20  d4 13.36  tau 120.7  alpha  180.0

        A left-handed helix measures alpha -50.0, so the dihedral sign is
        what keeps one from being read as the other.

        Assignment is per chain even when ``chain_id`` is None: running
        the window across a chain boundary would invent structure out of
        two unrelated termini that happen to be adjacent in the file.
        """
        chains = self.chains()
        out: list[str] = []
        for chain in chains:
            if chain_id is not None and chain != chain_id:
                continue
            supplied = self._supplied_labels(chain, detailed=detailed)
            if supplied is not None:
                out.extend(supplied)
                continue
            out.extend(_assign_secondary_structure(self.backbone_trace(chain)))
        return out


@dataclass
class GridData:
    """Geometry of a scalar field on a regular grid.

    A stored QVF volume keeps ``origin`` and ``voxel_vectors`` in **bohr**
    (design § 1.3a), the default. Fields the viewer samples itself, such as
    an orbital evaluated from the GTO wavefunction, are laid out in Å
    alongside the atoms and say so with ``units="angstrom"``. Anything that
    places a grid in the Å scene scales by :attr:`to_angstrom` instead of
    assuming one convention.
    """

    origin: np.ndarray  # [3]
    voxel_vectors: np.ndarray  # [3,3], row i is the step along grid axis i
    shape: tuple[int, int, int]
    units: Literal["bohr", "angstrom"] = "bohr"

    def __post_init__(self) -> None:
        if self.units not in ("bohr", "angstrom"):
            raise ValueError(f"grid units must be 'bohr' or 'angstrom', not {self.units!r}")

    @property
    def to_angstrom(self) -> float:
        """Factor that takes this grid's coordinates into Å."""
        return 1.0 if self.units == "angstrom" else BOHR_TO_ANGSTROM


@dataclass
class BandsData:
    kpath: dict[str, Any]  # raw kpath.json
    eigenvalues: np.ndarray  # [n_spin, n_kpoints, n_bands]
    fermi: float | None


@dataclass
class PhononBandsData:
    qpath: dict[str, Any]  # raw qpath.json (segments, n_modes, …)
    frequencies: np.ndarray  # [n_qpoints, n_modes], cm^-1


@dataclass
class PhononDOSData:
    frequencies: np.ndarray  # [n_points], cm^-1
    dos: np.ndarray  # [n_points], states / cm^-1
    meta: dict[str, Any]


@dataclass
class EquationOfStateData:
    volumes: np.ndarray  # [n_points], Angstrom^3
    energies: np.ndarray  # [n_points], eV
    fit: dict[str, Any]  # model, V0, E0, B0, B0_prime, units, …


@dataclass
class SpectraData:
    frequencies: np.ndarray  # [n_modes], native axis unit (eV, cm⁻¹, ...)
    intensities: np.ndarray  # [n_modes], in the spectrum kind's unit (km/mol for IR)
    labels: list[str] | None = None  # optional per-transition assignments


@dataclass
class TrajectoryData:
    atoms: list[Atom]  # atom types (fixed across frames)
    coords: np.ndarray  # [n_frames, n_atoms, 3]
    energies: list[float] | None  # per-frame energies


@dataclass
class VibrationsData:
    atoms: list[Atom]
    frequencies: np.ndarray  # [n_modes] cm⁻¹
    displacements: np.ndarray  # [n_modes, n_atoms, 3]


@dataclass
class AtomPropertiesData:
    mulliken_charges: np.ndarray | None  # [n_atoms] float64
    loewdin_charges: np.ndarray | None  # [n_atoms] float64
    spin_populations: np.ndarray | None = None  # [n_atoms] float64
    hirshfeld_charges: np.ndarray | None = None  # [n_atoms] float64
    # Knizia IAO partial charges. Unlike the schemes above these are
    # basis-set stable, so they are the ones to trust when comparing
    # charges computed in different basis sets.
    iao_charges: np.ndarray | None = None  # [n_atoms] float64


@dataclass
class CitationsData:
    """BibTeX bibliography embedded in the QVF.

    The writer stores `references.bib` as a binary member (utf-8 bytes
    + sha256). We decode here so renderers see a string.
    """

    bibtex: str



def _safe_basename(name: Any, fallback: str) -> str:
    """A producer-supplied filename reduced to a safe bare basename.

    The files index travels inside the archive, so its ``filename`` is
    untrusted input on a path that ends in a browser "save as". Strip
    any directory component (including Windows separators) and reject
    the traversal names outright, so a crafted archive cannot propose
    ``../../.bashrc`` as a download name.
    """
    text = str(name or "").strip().replace("\\", "/")
    base = text.rsplit("/", 1)[-1]
    if not base or base in (".", ".."):
        return fallback
    return base

@dataclass
class RunRecordData:
    """run.record section payload — the self-contained record of one
    program invocation (verbatim input + full log, spec § 5.8).

    ``input_text`` / ``log_text`` are decoded UTF-8 (None when the
    member is absent); ``input_size`` / ``log_size`` are the
    uncompressed byte counts from the ZIP central directory so a
    renderer can size-gate what it pushes to the DOM. ``files`` is the
    optional filename index; ``attachment_roles`` lists attachment
    members present (their bytes are not read here).
    """

    program: str
    program_version: str | None
    command: str | None
    exit_status: int | None
    started_utc: str | None
    finished_utc: str | None
    sequence: int | None
    input_text: str | None
    input_size: int
    log_text: str | None
    log_size: int
    files: dict[str, Any]
    attachment_roles: list[str]


@dataclass
class JobSpecData:
    """job.spec section payload — the declarative specification of the
    calculation the archive *requests* (spec § 5.9). ``raw`` keeps the
    full JobSpecPayload (open for forward growth); the typed fields
    mirror its portable top level."""

    job_type: str
    method: str | None
    basis: str | None
    functional: str | None
    charge: int | None
    multiplicity: int | None
    kpoints: list[Any] | None
    tasks: list[Any]
    options: dict[str, Any]
    raw: dict[str, Any]


#: The identity fields a ``structure`` section object may carry (QVF spec
#: § 5.1, "Chemical identity"), besides the generic ``label``.
STRUCTURE_IDENTITY_FIELDS: tuple[str, ...] = (
    "name", "charge", "multiplicity", "smiles", "inchi", "inchikey",
)


@dataclass(frozen=True)
class StructureIdentity:
    """Which chemical species a ``structure`` section holds (QVF spec § 5.1).

    Read from the section object, never from a member. Every field is
    optional, and an absent one states nothing: a structure without
    ``charge`` is not declared neutral. ``label`` is the generic section
    label, the short compound label a scheme shows ("1", "3a"); ``name`` is
    the chemical name. A value of the wrong type reads as absent.
    """

    label: str | None = None
    name: str | None = None
    charge: int | None = None
    multiplicity: int | None = None
    smiles: str | None = None
    inchi: str | None = None
    inchikey: str | None = None

    @property
    def title(self) -> str:
        """``"<label> · <name>"``, either alone, or ``""`` when neither is given."""
        return " · ".join(part for part in (self.label, self.name) if part)

    def as_dict(self) -> dict[str, Any]:
        """The fields that are present, for JSON output."""
        return {
            key: value
            for key in ("label", *STRUCTURE_IDENTITY_FIELDS)
            if (value := getattr(self, key)) is not None
        }


#: The arrows of a reaction step (QVF spec § 5.14); absent means forward.
REACTION_ARROWS: tuple[str, ...] = ("forward", "equilibrium", "retrosynthetic", "resonance")


@dataclass(frozen=True)
class ReactionParticipant:
    """One species of a reaction step: a ``structure`` section and its coefficient."""

    structure_ref: str
    coefficient: float = 1.0


@dataclass(frozen=True)
class ReactionStep:
    """One step of a ``reaction.scheme`` (QVF spec § 5.14).

    Roles are chemical: ``reactants`` are consumed and ``products`` formed in
    the step's forward direction whatever the ``arrow``.
    """

    id: str
    reactants: list[ReactionParticipant]
    products: list[ReactionParticipant]
    reagents: list[ReactionParticipant] = field(default_factory=list)
    label: str | None = None
    arrow: str = "forward"
    conditions: list[str] = field(default_factory=list)
    yield_percent: float | None = None
    yield_text: str | None = None


@dataclass(frozen=True)
class ReactionSchemeData:
    """A ``reaction.scheme`` payload: its steps in reading order."""

    steps: list[ReactionStep]


#: Raster types a ``preview`` thumbnail may have (QVF spec § 5.15).
PREVIEW_RASTER_TYPES: frozenset[str] = frozenset({"image/png", "image/jpeg"})


@dataclass(frozen=True)
class PreviewImage:
    """One verified ``preview`` member: its declared type and its bytes."""

    role: str
    media_type: str
    data: bytes


@dataclass
class NMRData:
    """NMR section payload.

    Writer (qvf.py::_write_spectra_nmr_section) pass-through of a dict
    with conventional keys: chemical_shifts, shielding_tensors,
    j_couplings, isotope, reference, solvent. Shape of each field is
    not schema-enforced, so we keep the raw dict and let the renderer
    decide what to surface.
    """

    raw: dict[str, Any]


@dataclass
class EPRData:
    """EPR section payload.

    Writer (qvf.py::_write_spectra_epr_section) pass-through of a dict with
    conventional keys: g_tensor, hyperfine, zero_field_splitting. Shape of each
    field is not schema-enforced, so we keep the raw dict and let the renderer
    decide what to surface.
    """

    raw: dict[str, Any]


@dataclass
class SymmetryData:
    """spglib-style symmetry analysis embedded in the QVF.

    Writer (qvf.py::_write_symmetry_section) passes through whatever
    dict the producer hands it (space group number, symbol, point
    group, Hall symbol, …). Keys are conventional, not enforced by
    schema. The renderer surfaces every key it finds.
    """

    raw: dict[str, Any]


@dataclass
class SCFHistoryData:
    """Per-iteration SCF trail.

    Writer (qvf.py::_write_scf_history_section) stores a JSON document
    `{"iterations": [{"iter", "energy_eh", "delta_e", "diis_error"},
    ...]}`. Field set is conventional, not enforced by schema — keep
    them as plain dicts so the renderer can degrade gracefully when
    a key is missing (e.g. non-DIIS solvers have no `diis_error`).
    """

    iterations: list[dict[str, float | int]]


@dataclass
class BondOrdersData:
    """Bond-order analysis section."""

    method: str  # "mayer", "wiberg", etc.
    pairs: list[dict[str, object]]  # each has i, j, order, distance_ang, ...


@dataclass
class QTAIMData:
    """QTAIM topological analysis section."""

    points: list[dict[str, object]]  # type, position, rho, laplacian, ...
    bond_paths: list[dict[str, object]] | None  # atoms, path


@dataclass
class DOSData:
    """A ``dos.total`` or ``dos.projected`` section (spec § 4.8 / § 4.9).

    ``values`` is the DOS ``[n_pts]`` or ``[2, n_pts]`` (spin axis leading)
    for a total DOS, and the per-channel projections ``[n_ch, n_pts]`` for a
    projected one, with a spin axis already summed away. ``channels`` is
    the section's channel list, empty for a total DOS.
    """

    energies: np.ndarray  # [n_pts], eV
    values: np.ndarray
    channels: list[object]
    projected: bool


@dataclass
class FermiSurfaceData:
    """A ``fermi_surface`` section: the energy mesh and its metadata."""

    energies: np.ndarray  # [nk1, nk2, nk3, n_bands], E - E_F
    mesh: dict[str, object]  # lattice_vectors, labels, ...


@dataclass
class DOSCOOPData:
    """COOP/COHP bonding analysis section (dos.coop or dos.cohp)."""

    energies: np.ndarray  # [n_pts]
    projections: np.ndarray  # [n_pairs, n_pts]
    integrated: np.ndarray  # [n_pairs]
    meta: dict[str, object]  # pair_labels, fermi_energy_ev, ...


@dataclass
class BasisShell:
    """One contracted shell of the GTO basis (§ 1.5)."""

    center: int  # 0-based atom index
    l: int  # noqa: E741 — `l` is the QVF field name for angular momentum
    exponents: np.ndarray  # [n_prim] bohr^-2
    coefficients: np.ndarray  # [n_prim] contraction coefficients (normalized primitives)
    pure: bool = True  # spherical (True) or Cartesian (False)


@dataclass
class TransitionOrbitals:
    """The excited state a set of natural transition orbitals describes (spec § 5.3).

    Read from the canonical carriers first: ``state_index`` and the
    ``excitation_energy`` of that state in the ``excited_states`` section
    ``state_ref`` names. Archives written before those existed carry the
    deprecated ``excitation_index`` and ``excitation_energy_ev`` instead,
    which are read when the canonical carrier is absent, so both show the
    same state. Each field is None when the archive does not say.
    """

    #: ``"hole"`` or ``"particle"``, from ``nto_role``.
    role: str | None = None
    #: The state's number: ``state_index``, else ``excitation_index``.
    state_index: int | None = None
    #: The state's excitation energy in eV.
    excitation_energy_ev: float | None = None
    #: The ``excited_states`` section that ``state_ref`` names.
    states_ref: str | None = None


LOCALIZED_ORBITAL_KINDS = frozenset({"localized", "ibo", "iao", "boys", "pipek_mezey", "nbo"})


@dataclass
class WavefunctionGTOData:
    """`wavefunction.gto` payload.

    For restricted: `mo_coefficients` is set, `mo_coefficients_alpha`
    and `mo_coefficients_beta` are None.

    For unrestricted: the per-spin variants are set, and
    `mo_coefficients` is None.

    Each coefficient matrix is row-major [n_mo, n_ao].
    """

    structure_ref: str
    pure: bool
    n_ao: int
    shells: list[BasisShell]
    spin: str  # "restricted" | "unrestricted"
    orbital_kind: str  # "canonical" | "natural" | "localized"
    energies: np.ndarray | None  # [n_mo] (restricted only)
    occupations: np.ndarray | None  # [n_mo] (restricted only)
    symmetry_labels: list[str] | None
    alpha_energies: np.ndarray | None
    alpha_occupations: np.ndarray | None
    beta_energies: np.ndarray | None
    beta_occupations: np.ndarray | None
    mo_coefficients: np.ndarray | None  # [n_mo, n_ao] float64
    mo_coefficients_alpha: np.ndarray | None
    mo_coefficients_beta: np.ndarray | None
    # Unrestricted β symmetry labels. α (and restricted) labels live in
    # `symmetry_labels`; keeping β separate stops β orbitals from inheriting
    # α's labels (audit L7). Optional with a default so existing readers that
    # predate the split still construct.
    symmetry_labels_beta: list[str] | None = None
    # Localization payload, present only when `orbital_kind == "localized"`.
    # A localized orbital has no orbital energy, so these carry what actually
    # identifies it: which atoms it sits on, and where it sits.
    #   atom_populations  [n_mo, n_atoms] — n_A(i); each row sums to 1
    #   centroids_bohr    [n_mo, 3]       — <i|r|i>
    #   n_centres         [n_mo]          — atoms above the centre threshold
    atom_populations: np.ndarray | None = None
    centroids_bohr: np.ndarray | None = None
    n_centres: np.ndarray | None = None
    localization_method: str | None = None
    # Meaning of the numeric entries stored under ``occupations``.  Natural
    # orbitals and natural *transition* orbitals both use
    # ``orbital_kind="natural"``, but the former carry electron occupations
    # while the latter carry transition weights.  Consumers must not infer
    # that distinction from a section id or from a coincidental sum.
    occupation_semantics: str | None = None
    # Explicit Bloch wavevector metadata; None retains legacy cluster semantics.
    k_point: np.ndarray | None = None
    # Session-only provenance/descriptors returned by the external worker.
    relocalization: dict[str, Any] | None = None
    # Some imports lack the density matrix or orbital convention needed to
    # reconstruct an electron density, while individual MOs remain valid.
    density_unavailable_reason: str | None = None
    # Set for natural transition orbitals: the state they describe.
    transition_orbitals: TransitionOrbitals | None = None


@dataclass
class ReactionWaypoint:
    """One reaction-path waypoint annotation."""

    frame_index: int
    label: str
    kind: str  # "reactant" | "transition_state" | "intermediate" | "product" | "point"
    energy_eh: float | None = None


@dataclass
class ReactionPathData:
    """Self-contained reaction path (`reaction.path`).

    Binary layout matches `trajectory`: coords[n_frames, n_atoms, 3] in Å.

    Periodic reaction paths additionally carry the per-frame lattice +
    dimensionality so the renderer can draw the cell and wrap atoms across
    periodic boundaries. A path is periodic when it has the ``lattice``
    member, never because of the manifest's ``qvf_version`` (QVF spec § 3.1):

    * ``lattice`` is None for molecular paths. Otherwise a float64
      array of shape (3, 3) when every frame shares the same lattice
      (the fixed-cell common case) or (n_frames, 3, 3) for variable-
      cell paths. Columns are a, b, c, in **bohr** — matching
      ``vibeqc.PeriodicSystem.lattice``. The renderer is responsible
      for the bohr→Å conversion (coords are already Å).
    * ``dim`` is None for molecular paths. Otherwise an int in
      {1, 2, 3}; ``dim_per_frame`` is set instead when frames carry
      different dimensionalities.
    """

    atoms: list[Atom]
    coords: np.ndarray  # [n_frames, n_atoms, 3] float64 Å
    energies: list[float] | None
    reaction_coordinate: list[float] | None
    waypoints: list[ReactionWaypoint]
    lattice: np.ndarray | None = None  # bohr; None or (3,3) or (n_frames,3,3)
    dim: int | None = None  # 1/2/3; shared across frames
    dim_per_frame: list[int] | None = None  # variable-dim case
    # Optional human-readable annotations for the energy-plot x-axis.
    reaction_coordinate_label: str | None = None  # e.g. "O–H distance"
    reaction_coordinate_unit: str | None = None  # e.g. "bohr", "rad"
    # Optional per-frame volumetric data (W1). frame_volumes is a 4D
    # array [n_emitted, nx, ny, nz]; volume_frame_index maps each
    # emitted slab to a path frame (decimation); volume_grid is the
    # shared real-space grid (bohr). None on archives without volumes.
    frame_volumes: np.ndarray | None = None
    volume_grid: GridData | None = None
    volume_frame_index: list[int] | None = None
    volume_label: str | None = None
    volume_isovalue: float | None = None


@dataclass
class ReactionWaypointsData:
    """Waypoint annotations layered on a referenced trajectory."""

    trajectory_ref: str
    waypoints: list[ReactionWaypoint]
    reaction_coordinate: list[float] | None


@dataclass
class ScanSurfaceData:
    """2D relaxed-scan energy surface (`scan.surface`).

    ``energies`` is ``[nA, nB]`` (Hartree); ``axis_a`` / ``axis_b`` are
    the 1D driven-coordinate values. Optional ``geometries`` is the
    relaxed structure at each node, flattened ``[nA*nB, n_atoms, 3]`` in
    Å (row-major over ``(a, b)``); ``atoms`` carries the element symbols.
    """

    axis_a: np.ndarray  # (nA,)
    axis_b: np.ndarray  # (nB,)
    energies: np.ndarray  # (nA, nB)
    coordinate_a_label: str | None = None
    coordinate_a_unit: str | None = None
    coordinate_b_label: str | None = None
    coordinate_b_unit: str | None = None
    atoms: list[Atom] | None = None
    geometries: np.ndarray | None = None  # (nA*nB, n_atoms, 3) Å


def _json_object(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise QVFError("payload is not a JSON object")
    return cast(dict[str, Any], raw)


def _json_integer(value: Any, field: str) -> int:
    # Match JSON Schema integer semantics, including 1.0 but excluding bool.
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or (isinstance(value, float) and (not np.isfinite(value) or not value.is_integer()))
    ):
        raise QVFError(f"{field} must be an integer")
    return int(value)


def _json_finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise QVFError(f"{field} must be a finite number")
    try:
        number = float(value)
    except OverflowError:
        raise QVFError(f"{field} must be a finite number") from None
    if not np.isfinite(number):
        raise QVFError(f"{field} must be a finite number")
    return number


def _grid_from_json(raw: Any, data_shape: Sequence[int]) -> GridData:
    payload = _json_object(raw)

    def vector(value: Any, field: str) -> list[float]:
        if not isinstance(value, list) or len(value) != 3:
            raise QVFError(f"{field} must contain exactly three numbers")
        return [_json_finite_number(v, f"{field}[{i}]") for i, v in enumerate(value)]

    origin = vector(payload.get("origin"), "origin")
    vectors = payload.get("voxel_vectors")
    if not isinstance(vectors, list) or len(vectors) != 3:
        raise QVFError("voxel_vectors must be a 3x3 matrix")
    voxel_vectors = [vector(v, f"voxel_vectors[{i}]") for i, v in enumerate(vectors)]
    shape_raw = payload.get("shape")
    if not isinstance(shape_raw, list) or len(shape_raw) != 3:
        raise QVFError("shape must contain exactly three dimensions")
    shape = tuple(_json_integer(v, f"shape[{i}]") for i, v in enumerate(shape_raw))
    if any(n < 0 for n in shape):
        raise QVFError("shape dimensions must be nonnegative")
    if shape != tuple(data_shape):
        raise QVFError(f"grid shape {shape} does not match binary shape {tuple(data_shape)}")
    return GridData(
        origin=np.asarray(origin, dtype=np.float64),
        voxel_vectors=np.asarray(voxel_vectors, dtype=np.float64),
        shape=cast(tuple[int, int, int], shape),
    )


def _frame_atoms_from_lists(symbols: Any, numbers: Any) -> list[Atom]:
    """Atom identities from ``symbols`` and/or ``atomic_numbers`` (spec § 5.5).

    Either list alone names the atoms; when both are present they must have
    one entry per atom each and agree wherever the symbol is an element.
    A missing symbol is taken from the atomic number (``Z<n>`` beyond the
    table), a missing atomic number from the symbol (0 for a symbol that is
    no element).
    """
    for name, value in (("symbols", symbols), ("atomic_numbers", numbers)):
        if value is not None and not isinstance(value, list):
            raise QVFError(f"{name} must be a list")
    if symbols is not None and numbers is not None and len(symbols) != len(numbers):
        raise QVFError(
            f"symbols has {len(symbols)} entries and atomic_numbers {len(numbers)}; "
            "they must name the same atoms"
        )
    count = len(symbols) if symbols is not None else len(numbers or [])
    atoms = []
    for i in range(count):
        symbol: str | None = None
        number: int | None = None
        if symbols is not None:
            symbol = symbols[i]
            if not isinstance(symbol, str) or not symbol:
                raise QVFError(f"symbols[{i}] must be a nonempty string")
        if numbers is not None:
            number = _atomic_number(numbers[i], f"atomic_numbers[{i}]")
        if symbol is None:
            assert number is not None
            symbol = z_to_symbol(number, default=f"Z{number}")
        elif number is None:
            number = SYMBOL_TO_Z.get(symbol, 0)
        elif SYMBOL_TO_Z.get(symbol, number) != number:
            raise QVFError(
                f"symbols[{i}] {symbol!r} and atomic_numbers[{i}] {number} "
                "name different atoms"
            )
        atoms.append(Atom(symbol=symbol, position=np.zeros(3), atomic_number=number))
    return atoms


def _frame_atoms_from_json(meta: dict[str, Any]) -> list[Atom]:
    """Atom identities of ``trajectory`` and ``reaction.path`` metadata.

    The metadata names its atoms through at least one of ``atoms``,
    ``symbols`` and ``atomic_numbers`` (spec § 5.5). ``atoms`` is read when
    present; otherwise the two lists are.
    """
    raw = meta.get("atoms")
    if raw is None and ("symbols" in meta or "atomic_numbers" in meta):
        return _frame_atoms_from_lists(meta.get("symbols"), meta.get("atomic_numbers"))
    if not isinstance(raw, list):
        raise QVFError("atoms must be a list")
    atoms = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise QVFError(f"atoms[{i}] must be a JSON object")
        symbol = entry.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise QVFError(f"atoms[{i}].symbol must be a nonempty string")
        number = _atomic_number(entry.get("atomic_number"), f"atoms[{i}].atomic_number")
        # Frame metadata carries identities, not positions. Keep legacy display
        # labels (including unknown elements) as read_structure does.
        atoms.append(Atom(symbol=symbol, position=np.zeros(3), atomic_number=number))
    return atoms


def scf_energy_eh(provenance: Mapping[str, Any]) -> float | None:
    """``provenance["scf_energy"]`` as a float in hartree, or None.

    Producers write the energy either as a bare number or as
    ``{"value": E, "unit": "hartree"}``; every surface that prints or
    compares energies reads it through here.
    """
    energy = provenance.get("scf_energy")
    if isinstance(energy, Mapping):
        energy = energy.get("value")
    if isinstance(energy, int | float) and not isinstance(energy, bool):
        return float(energy)
    return None


def _frame_energies(meta: dict[str, Any]) -> list[float] | None:
    values = meta.get("energies")
    if values is None:
        return None
    if not isinstance(values, list):
        raise QVFError("energies must be a list or null")
    # Empty/short prefixes remain supported: not every archived frame has an
    # energy, and get_energy explicitly returns None beyond the available list.
    return [_json_finite_number(v, f"energies[{i}]") for i, v in enumerate(values)]


def _waypoints_from_json(payload: dict[str, Any], n_frames: int) -> list[ReactionWaypoint]:
    raw = payload.get("waypoints", [])
    if not isinstance(raw, list):
        raise QVFError("waypoints must be a list")
    result = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise QVFError(f"waypoints[{i}] must be a JSON object")
        index = _json_integer(entry.get("frame_index"), f"waypoints[{i}].frame_index")
        if not 0 <= index < n_frames:
            raise QVFError(
                f"waypoints[{i}].frame_index {index} is out of range for {n_frames} frames"
            )
        label, kind = entry.get("label"), entry.get("kind")
        if not isinstance(label, str) or not isinstance(kind, str):
            raise QVFError(f"waypoints[{i}].label and kind must be strings")
        energy = (
            _json_finite_number(entry["energy_eh"], f"waypoints[{i}].energy_eh")
            if "energy_eh" in entry else None
        )
        result.append(ReactionWaypoint(index, label, kind, energy))
    return result


def _has_immutable_bytes(array: np.ndarray) -> bool:
    """Read-only flags alone do not prove a view's backing storage immutable."""
    base: object = array
    while isinstance(base, np.ndarray):
        if base.flags.writeable:
            return False
        base = base.base
    return isinstance(base, bytes)


def _parse_ij_pairs(raw: Any, n_atoms: int | None = None) -> list[dict[str, Any]]:
    """Validate pair indices and present orders without losing analysis metadata."""
    if not isinstance(raw, dict):
        raise QVFError("payload is not a JSON object")
    pairs = raw.get("pairs", [])
    if not isinstance(pairs, list):
        raise QVFError("field 'pairs' is not a list")
    result: list[dict[str, Any]] = []
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            raise QVFError(f"pair {index} is not a JSON object")
        normalized = dict(pair)
        for key in ("i", "j"):
            number = _json_integer(pair.get(key), f"pair {index} field {key!r}")
            if number < 0 or (n_atoms is not None and number >= n_atoms):
                bound = f" for {n_atoms} atoms" if n_atoms is not None else ""
                raise QVFError(f"pair {index} field {key!r} index {number} is out of range{bound}")
            normalized[key] = number
        if "order" in pair:
            normalized["order"] = _json_finite_number(
                pair["order"], f"pair {index} field 'order'",
            )
        result.append(normalized)
    return result



# ── Manifest loader ───────────────────────────────────────────────────────

_SCHEMA_PATH = Path(__file__).parent / "schema.json"

# The manifest schema of the pinned qvf release, vendored byte for byte and
# loaded once at module level.
with open(_SCHEMA_PATH) as f:
    _MANIFEST_SCHEMA: dict[str, Any] = json.load(f)

#: The ``$id`` of the vendored v1 schema, the ``schema_uri`` of a v1 manifest.
MANIFEST_SCHEMA_URI: str = _MANIFEST_SCHEMA["$id"]

# One schema per version a manifest is *read as*. qvf_version 2 is not here:
# it was withdrawn, and an archive stamped with it (vibe-qc 0.10-0.15, a
# periodic reaction.path) is read and checked as version 1 against this same
# schema, with the warning W_VERSION_WITHDRAWN (QVF spec § 3.1). There is no
# v2 schema to carry.
_SCHEMAS_BY_VERSION: dict[int, dict[str, Any]] = {
    1: _MANIFEST_SCHEMA,
}

# The withdrawn version and the version it is read as (QVF spec § 3.1).
_WITHDRAWN_VERSION = 2
_WITHDRAWN_READ_AS = 1

def _structure_payload_validator(schema: dict[str, Any]) -> Validator:
    # Legacy viewer symbols are display labels: imports preserve Z999, and
    # notebook exports safely escape arbitrary text. Retain those contracts
    # while requiring a nonempty string. Never mutate the pinned schema or
    # the definitions used to validate manifests.
    definitions = deepcopy(schema["$defs"])
    definitions["StructureAtom"]["properties"]["symbol"].pop("maxLength")
    return jsonschema.Draft202012Validator({
        "$ref": "#/$defs/StructurePayload", "$defs": definitions,
    })


_STRUCTURE_VALIDATORS = {
    version: _structure_payload_validator(schema)
    for version, schema in _SCHEMAS_BY_VERSION.items()
}


_MANIFEST_VALIDATORS: dict[int, Validator] = {}


def _validate_manifest(instance: Any, qvf_version: int, schema: dict[str, Any]) -> None:
    """``jsonschema.validate`` with the schema checked once, not per open.

    ``jsonschema.validate`` re-validates the schema against its meta-schema
    on every call, which took ~0.15 s of every archive open. Same errors:
    the best match of ``iter_errors``, raised as ``ValidationError``.
    """
    validator = _MANIFEST_VALIDATORS.get(qvf_version)
    if validator is None:
        cls = jsonschema.validators.validator_for(schema)
        cls.check_schema(schema)
        validator = _MANIFEST_VALIDATORS[qvf_version] = cls(schema)
    error = jsonschema.exceptions.best_match(validator.iter_errors(instance))
    if error is not None:
        raise error


def _load_manifest_schema(qvf_version: int = 1) -> dict[str, Any]:
    """Return the vendored JSON Schema for manifest.json at ``qvf_version``."""
    schema = _SCHEMAS_BY_VERSION.get(qvf_version)
    if schema is None:
        raise ManifestValidationError(
            f"unsupported qvf_version {qvf_version!r}; vibe-view reads version 1 "
            f"and the withdrawn version {_WITHDRAWN_VERSION} (QVF spec § 3.1)"
        )
    return schema


def _schema_known_kinds(schema: dict[str, Any]) -> set[str]:
    """Collect the section kinds the schema defines a branch for.

    Each ``$defs/Section*`` constrains ``kind`` to a ``const`` (the vendor
    branch uses a pattern instead). We harvest those consts so the reader
    can tell a *schema-unknown* kind (forward-compat / reserved-but-unwritten,
    e.g. ``fermi_surface``) apart from a genuinely malformed section, and
    open the archive instead of rejecting it wholesale (audit A3-01).
    """
    known: set[str] = set()
    for sub in (schema.get("$defs") or {}).values():
        if not isinstance(sub, dict):
            continue
        kind = (sub.get("properties") or {}).get("kind")
        if isinstance(kind, dict) and isinstance(kind.get("const"), str):
            known.add(kind["const"])
    return known


# Kinds the consumer can actually act on (render directly, or — for
# ``bonds`` — fold into the structure view). Used to decide whether a
# ``critical: true`` section must block the open (§ 5.5 of the spec).
def _consumer_usable_kinds() -> frozenset[str]:
    from vibeview.kinds import RENDERED_VIA_STRUCTURE, SUPPORTED_KINDS

    return SUPPORTED_KINDS | RENDERED_VIA_STRUCTURE


def _mo_vector(raw: Any, name: str) -> np.ndarray:
    """One value per orbital (or mode). A bare number parsed as a 0-d array,
    which the MO table and picker then failed to ``len()`` outside the
    reader (#39-35)."""
    arr = np.asarray(raw, dtype=np.float64)
    if arr.ndim != 1:
        raise QVFError(f"{name} must be a flat list of numbers")
    return arr


def _spectra_from_json(raw: dict[str, Any]) -> SpectraData:
    """``frequencies`` and ``intensities``: flat, finite, one shared length.

    Lists of different lengths passed the reader and made every spectrum
    plot fail while broadening, as a ``ValueError`` from ``zip`` (#66).
    """
    frequencies = _mo_vector(raw["frequencies"], "frequencies")
    intensities = _mo_vector(raw["intensities"], "intensities")
    if len(frequencies) != len(intensities):
        raise QVFError(
            f"frequencies [{len(frequencies)}] and intensities [{len(intensities)}] "
            "must have one shared length"
        )
    if not np.isfinite(frequencies).all():
        raise QVFError("frequencies must be finite")
    if not np.isfinite(intensities).all():
        raise QVFError("intensities must be finite")
    labels = raw.get("labels")
    if labels is not None and (
        not isinstance(labels, list) or len(labels) != len(frequencies)
        or not all(isinstance(label, str) for label in labels)
    ):
        raise QVFError("spectrum labels must contain one string per transition")
    return SpectraData(frequencies=frequencies, intensities=intensities, labels=labels)


def _shell_n_ao(l: int, pure: bool) -> int:  # noqa: E741 — angular momentum
    """Atomic orbitals of one shell: ``2l+1`` spherical, ``(l+1)(l+2)/2`` Cartesian."""
    return 2 * l + 1 if pure else (l + 1) * (l + 2) // 2


def _shell_from_json(raw: Any, index: int, global_pure: bool) -> BasisShell:
    """One basis shell; a malformed one is a :class:`QVFError` naming it.

    A shell whose exponent and coefficient counts differed passed the
    reader and made the first orbital evaluation raise ``ValueError`` (#66).
    """
    sh = _json_object(raw)
    center = _json_integer(sh["center"], f"shells[{index}].center")
    l = _json_integer(sh["l"], f"shells[{index}].l")  # noqa: E741 — angular momentum
    label = f"shell {index} (l={l}, center={center})"
    if center < 0:
        raise QVFError(f"{label}: center must be a non-negative atom index")
    if l < 0:
        raise QVFError(f"{label}: l must be non-negative")
    exponents = np.asarray(sh["exponents"], dtype=np.float64)
    coefficients = np.asarray(sh["coefficients"], dtype=np.float64)
    if exponents.ndim != 1 or coefficients.ndim != 1:
        raise QVFError(f"{label}: exponents and coefficients must be flat lists of numbers")
    if len(exponents) != len(coefficients):
        raise QVFError(
            f"{label}: {len(exponents)} exponents but "
            f"{len(coefficients)} contraction coefficients"
        )
    if len(exponents) == 0:
        raise QVFError(f"{label}: needs at least one primitive")
    if not np.isfinite(exponents).all() or not (exponents > 0).all():
        raise QVFError(f"{label}: exponents must be positive finite numbers")
    if not np.isfinite(coefficients).all():
        raise QVFError(f"{label}: contraction coefficients must be finite")
    return BasisShell(
        center=center,
        l=l,
        exponents=exponents,
        coefficients=coefficients,
        pure=bool(sh.get("pure", global_pure)),
    )


def _transition_orbitals(meta: Mapping[str, Any], orbital_kind: str) -> TransitionOrbitals | None:
    """The state of a natural-transition-orbital set from its ``mo_metadata``.

    None unless the orbitals are NTOs: ``orbital_kind`` ``"nto"``, or
    ``"natural"`` with any NTO key (``nto_role``, ``state_index`` or the
    deprecated ``excitation_index`` / ``excitation_energy_ev``). These keys
    only label the set, so a malformed value is left out, not an error.
    The excitation energy here is the deprecated key's; the reader replaces
    it with the canonical one from the ``excited_states`` section.
    """
    keys = ("nto_role", "state_index", "excitation_index", "excitation_energy_ev")
    if orbital_kind != "nto" and not (
        orbital_kind == "natural" and any(key in meta for key in keys)
    ):
        return None

    def integer(key: str, minimum: int) -> int | None:
        value = meta.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            return None
        return value

    role = meta.get("nto_role")
    energy = meta.get("excitation_energy_ev")
    reference = meta.get("state_ref")
    state_index = integer("state_index", 1)
    return TransitionOrbitals(
        role=role if role in ("hole", "particle") else None,
        state_index=state_index if state_index is not None else integer("excitation_index", 0),
        excitation_energy_ev=(
            float(energy)
            if isinstance(energy, int | float) and not isinstance(energy, bool)
            and math.isfinite(energy)
            else None
        ),
        states_ref=reference if isinstance(reference, str) and reference else None,
    )


def _wavefunction_from_json(
    basis_raw: dict[str, Any],
    meta_raw: dict[str, Any],
    binaries: dict[str, np.ndarray],
) -> WavefunctionGTOData:
    """Build :class:`WavefunctionGTOData` from parsed members; raises on malformed input."""
    global_pure = bool(basis_raw.get("pure", True))
    shells = [
        _shell_from_json(sh, index, global_pure)
        for index, sh in enumerate(basis_raw.get("shells", []))
    ]
    n_ao = int(basis_raw.get("n_ao", 0))
    # The basis's own reference, or "" when it names none; the reader resolves
    # it against the section's ``structure_ref`` and the first structure.
    structure_ref = str(basis_raw.get("structure_ref") or "")

    spin = str(meta_raw.get("spin", "restricted"))
    orbital_kind = str(meta_raw.get("orbital_kind", "canonical"))
    occupation_semantics = _opt_str(meta_raw.get("occupation_semantics"))
    transition_orbitals = _transition_orbitals(meta_raw, orbital_kind)
    if transition_orbitals is not None:
        # Natural transition orbitals take vibe-view's natural-orbital path
        # with transition weights, whether the archive says
        # ``orbital_kind: "nto"`` (spec § 5.3) or, as vibe-qc wrote them
        # before, ``"natural"`` with the deprecated excitation keys.
        orbital_kind = "natural"
        occupation_semantics = occupation_semantics or "transition_weight"

    energies: np.ndarray | None = None
    occupations: np.ndarray | None = None
    sym_labels: list[str] | None = None
    sym_labels_beta: list[str] | None = None
    a_e = a_o = b_e = b_o = None
    coeffs = a_c = b_c = None

    if spin == "unrestricted":
        alpha = meta_raw.get("alpha", {}) or {}
        beta = meta_raw.get("beta", {}) or {}
        a_e = _mo_vector(alpha.get("energies", []), "alpha.energies")
        a_o = _mo_vector(alpha.get("occupations", []), "alpha.occupations")
        b_e = _mo_vector(beta.get("energies", []), "beta.energies")
        b_o = _mo_vector(beta.get("occupations", []), "beta.occupations")
        # Per-spin labels (audit L7): the α list must not stand in for β.
        sym_labels = alpha.get("symmetry_labels")
        sym_labels_beta = beta.get("symmetry_labels")
        a_c = binaries.get("mo_coefficients_alpha")
        b_c = binaries.get("mo_coefficients_beta")
    else:
        energies = _mo_vector(meta_raw.get("energies", []), "energies")
        occupations = _mo_vector(meta_raw.get("occupations", []), "occupations")
        sym_labels = meta_raw.get("symmetry_labels")
        coeffs = binaries.get("mo_coefficients")

    def _decode_coefficients(values: np.ndarray | None) -> np.ndarray | None:
        if values is None:
            return None
        encoding = meta_raw.get("coefficient_encoding")
        if encoding == "complex_split_last_axis":
            if values.ndim != 3 or values.shape[-1] != 2:
                raise QVFError("Complex MO coefficients must have shape [n_mo, n_ao, 2]")
            components = meta_raw.get("coefficient_components", ["real", "imag"])
            if components != ["real", "imag"]:
                raise QVFError("Complex MO components must be ordered real, imag")
            values = values[..., 0] + 1j * values[..., 1]
        elif encoding not in (None, "real"):
            raise QVFError(f"Unsupported MO coefficient encoding: {encoding}")
        if values.ndim != 2 or (n_ao and values.shape[1] != n_ao):
            raise QVFError("MO coefficients must have shape [n_mo, n_ao]")
        if not np.isfinite(values).all():
            raise QVFError("MO coefficients must be finite")
        return values

    coeffs = _decode_coefficients(coeffs)
    a_c = _decode_coefficients(a_c)
    b_c = _decode_coefficients(b_c)
    # The metadata cannot list more orbitals than the coefficient matrix
    # holds: the orbital animation counted them from the energies and
    # indexed past a [0, n_ao] matrix (#66). A shorter prefix stays allowed,
    # as for trajectory energies.
    for matrix, name, vectors in (
        (coeffs, "mo_coefficients", (("energies", energies), ("occupations", occupations))),
        (a_c, "mo_coefficients_alpha", (("alpha.energies", a_e), ("alpha.occupations", a_o))),
        (b_c, "mo_coefficients_beta", (("beta.energies", b_e), ("beta.occupations", b_o))),
    ):
        if matrix is None:
            continue
        for vector_name, vector in vectors:
            if vector is not None and len(vector) > matrix.shape[0]:
                raise QVFError(
                    f"{vector_name} lists {len(vector)} orbitals but {name} has "
                    f"{matrix.shape[0]} rows"
                )
    # Shells that span more AOs than the coefficients are wide made the
    # renderer raise "MO vector too short" on the first evaluation (#66).
    n_ao_shells = sum(_shell_n_ao(shell.l, shell.pure) for shell in shells)
    width = n_ao or next((int(m.shape[1]) for m in (coeffs, a_c, b_c) if m is not None), 0)
    if width and n_ao_shells > width:
        what = f"n_ao is {n_ao}" if n_ao else f"the MO coefficients have width {width}"
        raise QVFError(f"basis shells span {n_ao_shells} AOs but {what}")
    k_point = meta_raw.get("k_point")
    if k_point is not None:
        k_point = np.asarray(k_point, dtype=float)
        if k_point.shape != (3,) or not np.isfinite(k_point).all():
            raise QVFError("k_point must contain three finite numbers")

    def _optional_array(key: str, dtype: type[np.generic]) -> np.ndarray | None:
        raw = meta_raw.get(key)
        if raw is None:
            return None
        arr = np.asarray(raw, dtype=dtype)
        if arr.ndim == 0:
            # Per-orbital data; a bare number made every consumer's len() fail.
            raise QVFError(f"{key} must be a list")
        return arr if arr.size else None

    return WavefunctionGTOData(
        atom_populations=_optional_array("atom_populations", np.float64),
        centroids_bohr=_optional_array("centroids_bohr", np.float64),
        n_centres=_optional_array("n_centres", np.int64),
        localization_method=(
            _str_or_none(meta_raw.get("localization_method"))
        ),
        occupation_semantics=occupation_semantics,
        density_unavailable_reason=_str_or_none(meta_raw.get("density_unavailable_reason")),
        transition_orbitals=transition_orbitals,
        k_point=k_point,
        structure_ref=structure_ref,
        pure=global_pure,
        n_ao=n_ao,
        shells=shells,
        spin=spin,
        orbital_kind=orbital_kind,
        energies=energies,
        occupations=occupations,
        symmetry_labels=sym_labels,
        alpha_energies=a_e,
        alpha_occupations=a_o,
        beta_energies=b_e,
        beta_occupations=b_o,
        mo_coefficients=coeffs,
        mo_coefficients_alpha=a_c,
        mo_coefficients_beta=b_c,
        symmetry_labels_beta=sym_labels_beta,
    )



# ── QVFReader ─────────────────────────────────────────────────────────────


QVFSource = str | Path | bytes | bytearray | memoryview | IO[bytes]
"""Anything `QVFReader` can open: a filesystem path, raw zip bytes, or a
seekable binary file-like object (BytesIO, an opened file, etc.).

Used by vibe-qc to hand a freshly built QVF over to the viewer without
touching disk."""



class QVFReader:
    """Read a .qvf archive and provide lazy access to sections.

    The archive can come from any of:

    * a filesystem path (``str`` / :class:`pathlib.Path` ending in ``.qvf``)
    * raw zip bytes (``bytes`` / ``bytearray`` / ``memoryview``) — vibe-qc
      can build a QVF in memory via ``qvf_bytes(...)`` and pass them in
    * a seekable binary file-like object (``BytesIO``, opened binary
      file, …)

    The zip handle is kept open for the lifetime of the reader so lazy
    binary blobs can be read on demand. ``close()`` (or use as a context
    manager) releases it; if the reader owns an in-memory buffer that is
    released too.
    """

    def __init__(self, source: QVFSource) -> None:
        self._path: Path | None
        self._buffer: io.BytesIO | None = None
        # Loose-file conversion keeps the archive in memory. Remember its
        # origin separately so the UI can identify an already-open dataset
        # without pretending the original file is an on-disk QVF to watch.
        # This is session state only and is never written into an archive.
        self.source_path: Path | None = None

        if isinstance(source, (str, Path)):
            self._path = Path(source)
            try:
                exists = self._path.exists()
            except OSError as e:
                raise QVFOpenError(f"cannot access {self._path}: {e}") from None
            if not exists:
                raise QVFOpenError(f"file not found: {self._path}")
            # Case-insensitive: ``.QVF`` from a case-preserving filesystem
            # or a Windows share is the same format (#35-36).
            if self._path.suffix.lower() != ".qvf":
                raise QVFOpenError(f"expected .qvf extension, got: {self._path.suffix}")
            if self._path.is_dir():
                raise QVFOpenError(f"not a file but a directory: {self._path}")
            self._zf = self._open_zip(self._path)
        elif isinstance(source, (bytes, bytearray, memoryview)):
            self._path = None
            self._buffer = io.BytesIO(bytes(source))
            try:
                self._zf = self._open_zip(self._buffer)
            except QVFOpenError:
                self._buffer.close()
                self._buffer = None
                raise
        elif hasattr(source, "read"):
            # Generic seekable file-like
            self._path = None
            self._zf = self._open_zip(source)
        else:
            raise QVFOpenError(
                f"QVFReader: unsupported source type {type(source).__name__}; "
                "expected path, bytes, or seekable file-like object"
            )

        # From here on the reader owns an open ZipFile: release it on ANY
        # failure, so a constructor that raises never leaves a handle behind
        # for the garbage collector (#39-18).
        try:
            self._load_manifest()
        except QVFError:
            self.close()
            raise
        except Exception as e:
            # Structural guarantee (#39-35): a manifest that parses but has a
            # shape no check above anticipated must still fail as a QVFError,
            # never as whatever the code that tripped over it raised
            # (OverflowError from ``int(inf)``, TypeError from an unhashable
            # kind, ...). Callers handle QVFError; anything else escaped as a
            # traceback from every command. KeyboardInterrupt and SystemExit
            # are not Exceptions and pass through the clause below.
            self.close()
            if getattr(self, "_manifest_text", None) is None:
                raise QVFOpenError(
                    f"cannot read manifest.json: {_describe_exception(e)}"
                ) from e
            raise ManifestValidationError(
                f"manifest.json has an unsupported shape: {_describe_exception(e)}"
            ) from e
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _open_zip(target: Path | IO[bytes]) -> zipfile.ZipFile:
        """``ZipFile(target)``, with every open failure as ``QVFOpenError``."""
        try:
            return zipfile.ZipFile(target, "r")
        except zipfile.BadZipFile as e:
            raise QVFOpenError(f"not a valid zip archive: {e}") from None
        except _ARCHIVE_READ_ERRORS as e:
            raise QVFOpenError(f"cannot open archive: {e}") from None
        except Exception as e:
            # E.g. a file-like source whose read/seek misbehaves (#39-35).
            raise QVFOpenError(f"cannot open archive: {_describe_exception(e)}") from e

    def _load_manifest(self) -> None:
        """Parse, validate and index manifest.json of the opened archive."""
        # ── Step 1: extract + parse manifest.json ──
        try:
            raw = self._zf.read("manifest.json").decode("utf-8")
        except KeyError:
            self.close()
            raise QVFOpenError("manifest.json not found in archive") from None
        except UnicodeDecodeError as e:
            self.close()
            raise QVFOpenError(f"manifest.json is not valid UTF-8: {e}") from None
        except _ARCHIVE_READ_ERRORS as e:
            self.close()
            raise QVFOpenError(f"cannot read manifest.json: {e}") from None

        try:
            manifest_dict = json.loads(raw)
        except json.JSONDecodeError as e:
            self.close()
            raise QVFOpenError(f"manifest.json is not valid JSON: {e}") from None
        except _JSON_PARSE_ERRORS as e:
            # A deeply nested document exhausts the parser's recursion limit
            # (RecursionError) instead of raising JSONDecodeError (#39-35).
            self.close()
            raise QVFOpenError(
                f"manifest.json cannot be parsed: {type(e).__name__}: {e}"
            ) from None
        if _json_depth_exceeds(manifest_dict, _MAX_JSON_DEPTH):
            # Parseable, but every later consumer that walks it recursively
            # (schema messages, json.dumps in h-add/slice/merge, the info
            # views) would hit RecursionError instead (#39-35).
            self.close()
            raise QVFOpenError(f"manifest.json nests deeper than {_MAX_JSON_DEPTH} levels")
        self._manifest_text = raw
        if not isinstance(manifest_dict, dict):
            # A JSON array or scalar used to reach ``.get`` below and escape
            # as AttributeError from every command but ``open`` (#39-35).
            self.close()
            raise ManifestValidationError(
                f"manifest.json must be a JSON object, got {type(manifest_dict).__name__}"
            )
        # Before the number check and the schema, whose messages quote keys
        # and values: a message holding a lone surrogate cannot be printed.
        bad_string = _lone_surrogate_in(raw, manifest_dict)
        if bad_string is not None:
            self.close()
            raise ManifestValidationError(
                "manifest.json has a string with an unpaired UTF-16 surrogate "
                f"at {bad_string}, which is not Unicode text"
            )
        bad_number = _first_nonfinite_number(manifest_dict)
        if bad_number is not None:
            where, what = bad_number
            self.close()
            raise ManifestValidationError(
                f"manifest.json has a number that is not a finite double at {where}: {what}"
            )

        # ── Step 2: validate against JSON Schema ──
        # Pick the schema by the manifest's declared qvf_version. The
        # withdrawn version 2 is read as version 1 for every check, the
        # schema included, and leaves the warning W_VERSION_WITHDRAWN
        # (QVF spec § 3.1), as qvf's own validators do. Any other version
        # is reported as unsupported instead of mis-validating against v1.
        # Non-finite numbers are refused above; lists, dicts and strings make
        # ``int()`` raise TypeError/ValueError, and OverflowError stays
        # listed in case that check ever moves. Each falls back to v1, whose
        # schema then rejects the value (#39-35).
        self._manifest_warnings: list[str] = []
        raw_version = manifest_dict.get("qvf_version", 1)
        if raw_version == _WITHDRAWN_VERSION and not isinstance(raw_version, bool):
            read_version = _WITHDRAWN_READ_AS
            manifest_dict_checked = {**manifest_dict, "qvf_version": read_version}
            self._manifest_warnings.append(
                f"W_VERSION_WITHDRAWN: qvf_version {_WITHDRAWN_VERSION} is withdrawn; "
                f"read as version {read_version} (QVF spec § 3.1)"
            )
        else:
            try:
                read_version = int(raw_version)
            except (TypeError, ValueError, OverflowError):
                read_version = 1
            manifest_dict_checked = manifest_dict
        # The version the archive is read as; payload checks use its schema.
        self._read_version = read_version
        try:
            chosen_schema = _load_manifest_schema(read_version)
        except ManifestValidationError:
            self.close()
            raise

        # ── Step 2a: enforce the `critical` contract BEFORE tolerating
        # unknown kinds. Any section flagged `critical: true` whose kind the
        # consumer cannot act on MUST block the open — including vendor
        # `x_<vendor>.*` kinds, which are never renderable (QVF spec §5.3:
        # vendor sections are listed-but-skipped *unless* critical; §5.5 / §7:
        # an unsupported critical section MUST refuse the open). A critical
        # root extension (below) blocks the open too.
        usable = _consumer_usable_kinds()
        raw_sections = manifest_dict.get("sections")
        if isinstance(raw_sections, list):
            for sec in raw_sections:
                if not isinstance(sec, dict):
                    continue
                kind = sec.get("kind")
                # ``kind`` is unvalidated here: a list or dict would make the
                # set lookup raise TypeError (unhashable) (#39-35). A critical
                # section whose kind is not even a string is unsupported.
                if sec.get("critical") is True and not (isinstance(kind, str) and kind in usable):
                    self.close()
                    raise CriticalSectionUnsupportedError(
                        f"section {sec.get('id')!r} is marked critical but its "
                        f"kind {kind!r} is not supported by vibe-view; refusing "
                        "to open (a partial render would be misleading)."
                    )
        extensions = manifest_dict.get("extensions")
        if isinstance(extensions, dict):
            for ext_name, ext in extensions.items():
                if isinstance(ext, dict) and ext.get("critical") is True:
                    self.close()
                    raise CriticalSectionUnsupportedError(
                        f"manifest declares critical extension {ext_name!r} that "
                        "vibe-view does not support; refusing to open."
                    )

        # ── Step 2b: validate, tolerating schema-unknown section kinds.
        # The schema constrains `kind` to a fixed set of consts; a forward-
        # compat / reserved kind the writer might add later (fermi_surface,
        # phonon_bands, …) would otherwise fail the whole-manifest validation
        # and lose every section, including structure (audit A3-01). We set
        # those sections aside, validate the remainder, and still open —
        # surfacing the unknown sections as "skipped, unsupported".
        known_kinds = _schema_known_kinds(chosen_schema)
        if isinstance(raw_sections, list):
            kept, unknown = [], []
            for sec in raw_sections:
                kind = sec.get("kind") if isinstance(sec, dict) else None
                is_vendor = isinstance(kind, str) and kind.startswith("x_")
                if isinstance(kind, str) and kind not in known_kinds and not is_vendor:
                    # The schema requires ``members`` on every section (QVF
                    # spec § 4.1), but it never sees an unknown kind, and the
                    # model defaults a missing map to {}. ``merge`` then
                    # failed on the manifest copy with KeyError (#39-35).
                    if not isinstance(sec.get("members"), dict):
                        self.close()
                        raise ManifestValidationError(
                            f"section {sec.get('id')!r} of kind {kind!r} has no 'members' "
                            "object; every section needs one (QVF spec § 4.1)"
                        )
                    unknown.append(sec)
                else:
                    kept.append(sec)
            to_validate = (
                manifest_dict_checked
                if not unknown
                else {**manifest_dict_checked, "sections": kept}
            )
        else:
            to_validate = manifest_dict_checked

        try:
            _validate_manifest(to_validate, read_version, chosen_schema)
            # The model still checks the common shape of unknown sections;
            # retain their existing tolerance for future identifier conventions.
            self._manifest = Manifest.model_validate(manifest_dict)
        except (jsonschema.ValidationError, ValidationError) as e:
            self.close()
            raise ManifestValidationError(_validation_message(e)) from None
        except RecursionError:
            # A manifest nested just shallowly enough to parse can still
            # exhaust the validator's recursion (#39-35).
            self.close()
            raise ManifestValidationError(
                "manifest.json is nested too deeply to validate"
            ) from None

        # Build section lookup
        self._section_by_id: dict[str, Section] = {}
        for s in self._manifest.sections:
            if s.id in self._section_by_id:
                self.close()
                raise ManifestValidationError(f"duplicate section id in manifest: {s.id!r}")
            self._section_by_id[s.id] = s

        # First member failure per section; archive bytes are immutable.
        self._section_errors: dict[str, _SectionError] = {}
        # Cache validation success, not mutable decoded geometry. Each read
        # still verifies the member bytes and creates fresh atoms/arrays.
        # Validated structure payloads, keyed by (section, member sha256,
        # qvf_version): the JSON is parsed and validated once per archive
        # content, not on every read_structure. Treated as read-only.
        self._structure_payloads: dict[tuple[str, str, int], dict[str, Any]] = {}

        # Decoded-binary cache, keyed (section_id, member_name). The archive
        # is opened read-only and members are immutable for the reader's
        # lifetime, so re-reading a member can return the cached ndarray
        # instead of re-DEFLATE-decompressing + re-SHA-256-hashing it. This
        # is what makes the isovalue / clip sliders and trajectory/vibration
        # playback responsive — each previously re-read the whole blob on
        # every tick because the renderer (and its cache) was rebuilt each
        # call (audit findings A7-01 / A7-03). frombuffer arrays are already
        # read-only, so sharing them is safe.
        self._binary_cache: dict[tuple[str, str], np.ndarray] = {}
        self._validated_frame_arrays: dict[tuple[str, str], np.ndarray] = {}

        # Viewer-side edit overlay: the current *edited* geometry, or None
        # when the archive is unmodified. The archive itself is immutable;
        # edits (add/delete/move atoms, fragments, supercell, …) live here
        # so every consumer of read_structure() — edit handlers, renderers,
        # export, input generation — sees the same current structure and
        # sequential edits compound instead of each one resetting to the
        # file's pristine geometry. A supercell edit can also replace the
        # lattice; keeping that beside the atoms makes the edit one atomic
        # structure overlay rather than an out-of-cell atom list paired with
        # the archive's old unit cell. A fresh reader starts with no overlay;
        # the controller may explicitly migrate one across a panel-only hot
        # reload when the unchanged 3D scene still represents that edit.
        #
        # Keyed by structure section id: an edit belongs to the structure it
        # was made on (QVF spec § 4.1: an archive may hold several), so
        # showing another structure shows that one as the file has it, and
        # showing the edited one again shows the edit. Each entry is the
        # edited atoms and the replacing lattice (None keeps the file's).
        self._edit_overlays: dict[str, tuple[list[Atom], np.ndarray | None]] = {}
        # The structure a consumer that shows one structure shows; None
        # means the first structure (see :attr:`shown_structure_id`).
        self._shown_structure_id: str | None = None
        # Viewer-computed wavefunctions, keyed by a synthetic section id.
        # Same contract as ``_edit_overlays``: it shadows the archive for the
        # lifetime of this reader, and a fresh reader starts empty so
        # re-reading the file always wins. Used by the re-localize feature
        # (:mod:`vibeview.relocalize`), which asks vibe-qc for a
        # localization criterion the file does not contain.
        self._wavefunction_overlays: dict[str, WavefunctionGTOData] = {}
        self.geometry_revision = 0

    @property
    def manifest(self) -> Manifest:
        return self._manifest

    @property
    def path(self) -> Path | None:
        """Path to the .qvf file on disk, or ``None`` for in-memory archives."""
        return self._path

    @property
    def source(self) -> Source:
        return self._manifest.source

    @property
    def sections(self) -> list[Section]:
        return self._manifest.sections

    @property
    def viewer_defaults(self) -> ViewerDefaults | None:
        return self._manifest.viewer_defaults

    # ── live / streaming checkpoint fields (consumer_qvf_reference.md
    #    § "Live / streaming checkpoints") ───────────────────────────────

    @property
    def provenance(self) -> dict[str, Any]:
        """The manifest's open ``provenance`` block ({} when absent)."""
        extra = self._manifest.model_extra or {}
        prov = extra.get("provenance")
        return prov if isinstance(prov, dict) else {}

    def scf_energy_eh(self) -> float | None:
        """The provenance's SCF energy in hartree, or None when it records none."""
        return scf_energy_eh(self.provenance)

    @property
    def dipole_moment(self) -> dict[str, Any]:
        """The manifest's root ``dipole_moment`` block ({} when absent).

        **Root, not ``provenance``** -- QVF spec § 4.7 puts it there and the
        schema declares it as a root property. Reading it off ``provenance``
        silently yields ``None`` for every file ever written, which is what
        left the dipole arrow and the dipole readout dead.

        Keys: ``total_debye``, ``vector_debye`` (3), ``origin`` (3).
        """
        extra = self._manifest.model_extra or {}
        value = extra.get("dipole_moment")
        return value if isinstance(value, dict) else {}

    @property
    def thermochemistry(self) -> dict[str, Any]:
        """The manifest's root ``thermochemistry`` block ({} when absent).

        Root, for the same reason as :attr:`dipole_moment`. Producer keys
        (``python/vibeqc/runner.py``) are ``zpve_eh``, ``enthalpy_eh``,
        ``entropy_cal_mol_k``, ``gibbs_free_energy_eh``, ``temperature_k``
        and ``pressure_atm``.

        Note the entropy is **cal/mol/K**, not Eh/K -- the one field here
        whose unit is not atomic.
        """
        extra = self._manifest.model_extra or {}
        value = extra.get("thermochemistry")
        return value if isinstance(value, dict) else {}

    @property
    def run_status(self) -> str | None:
        """The archive's place in a calculation lifecycle (spec § 3.2):
        ``"pending"`` for a job container that has not yet run,
        ``"running"`` while the job is in flight (live checkpoint), then
        ``"converged"`` or ``"failed"``. None when not stated (an
        ordinary results archive)."""
        v = self.provenance.get("run_status")
        return v if isinstance(v, str) else None

    @property
    def checkpoint_info(self) -> dict[str, Any]:
        """``provenance.checkpoint`` ({} when absent): ``seq`` (monotonic),
        ``wall_time_s``, ``written_at``, optionally ``scf_iteration`` /
        ``energy_eh`` on mid-SCF snapshots."""
        v = self.provenance.get("checkpoint")
        return v if isinstance(v, dict) else {}

    def run_record_sections(self) -> list[Section]:
        """All ``run.record`` sections in run-history order.

        Ordered by their ``sequence`` field (spec § 5.8; a record without
        one sorts before sequenced records, keeping manifest order among
        themselves). The **last** entry is the latest run — the one whose
        results the archive's other sections describe.
        """
        records = [s for s in self.sections if s.kind == "run.record"]

        def _seq(item: tuple[int, Section]) -> tuple[int, int]:
            index, section = item
            extras = getattr(section, "model_extra", None) or {}
            seq = extras.get("sequence")
            if isinstance(seq, int):
                return (seq, index)
            return (-1, index)

        return [s for _, s in sorted(enumerate(records), key=_seq)]

    def run_record_position(self, section_id: str) -> tuple[int, int]:
        """(1-based position, total) of a run.record in run history.

        Position ``total`` is the latest run. ``(0, 0)`` when the section
        is not a run.record of this archive.
        """
        history = self.run_record_sections()
        for i, section in enumerate(history):
            if section.id == section_id:
                return i + 1, len(history)
        return 0, 0

    def validation_report(self, *, limits: dict[str, Any] | None = None) -> dict[str, Any]:
        """Validate the open archive with qvf; preserve its handle and edit overlay."""
        from vibeview.qvf_validation import validate_archive

        stream = self._zf.fp
        if stream is None:
            raise QVFOpenError("cannot validate a closed reader")
        return validate_archive(stream, limits=limits)

    def manifest_warnings(self) -> list[str]:
        """Non-fatal findings about the manifest, made when the archive opened.

        Each warning starts with its QVF warning code. The one there is today
        is ``W_VERSION_WITHDRAWN``: the manifest declares the withdrawn
        ``qvf_version`` 2, which vibe-view reads and checks as version 1
        (spec § 3.1). The archive is valid; the warning records that its
        producer wrote a version producers must not write. Empty for an
        archive with nothing to report.
        """
        return list(self._manifest_warnings)

    def lifecycle_warnings(self) -> list[str]:
        """Container-lifecycle consistency warnings (spec § 3.2, § 5.9).

        "Done" means a **terminal** ``run_status`` (``converged`` /
        ``failed``) *plus* a complete latest ``run.record`` — one that
        carries both its ``input`` and ``log`` members (an empty log is
        complete: a run that failed before output began legitimately
        logged zero bytes). Returns human-readable warnings, empty when
        the archive is consistent. Non-terminal and unstated statuses
        never warn.
        """
        status = self.run_status
        if status not in ("converged", "failed"):
            return []
        warnings: list[str] = []
        history = self.run_record_sections()
        if not history:
            warnings.append(
                f"terminal status '{status}' but the archive carries no "
                f"run.record — the run that settled it left no record"
            )
            return warnings
        latest = history[-1]
        missing = [
            role
            for role in ("input", "log")
            if role not in latest.members
        ]
        if missing:
            warnings.append(
                f"terminal status '{status}' but the latest run.record "
                f"({latest.id}) lacks its {' and '.join(missing)} "
                f"member{'s' if len(missing) > 1 else ''} — the record "
                f"of this run is incomplete"
            )
        return warnings

    def section_is_partial(self, section_id: str) -> bool:
        """True when the producer marked this section ``partial`` — still
        growing in a live checkpoint (e.g. an optimization trajectory)."""
        section = self._section_by_id.get(section_id)
        if section is None:
            return False
        extras = getattr(section, "model_extra", None) or {}
        return extras.get("partial") is True

    def has_section(self, section_id: str) -> bool:
        return section_id in self._section_by_id

    def get_section(self, section_id: str) -> Section:
        try:
            return self._section_by_id[section_id]
        except KeyError:
            raise SectionNotFoundError(f"section not found: {section_id!r}") from None

    # ── Structures: found by kind and reference, never by id ──────────────
    #
    # QVF spec § 4.1: section ids carry no meaning. ``structure`` is only the
    # id the reference writers use by default; an archive may hold several
    # structures under any ids (vibe-sketch writes ``structure_m1``, ...).
    # A consumer that shows one structure, or needs one for an operation
    # that names none, uses the *first structure*.

    def _is_structure(self, section_id: object) -> bool:
        section = self._section_by_id.get(section_id) if isinstance(section_id, str) else None
        return section is not None and section.kind == "structure"

    def _named_structure(self, section: Section) -> str | None:
        """The section's own ``structure_ref`` when it names a structure, else None."""
        reference = (section.model_extra or {}).get("structure_ref")
        return reference if self._is_structure(reference) else None

    @property
    def structure_sections(self) -> list[Section]:
        """Every ``structure`` section, in ``sections`` order."""
        return [s for s in self._manifest.sections if s.kind == "structure"]

    @property
    def first_structure_id(self) -> str | None:
        """The archive's first structure (QVF spec § 4.1), or None without one.

        The first ``structure`` section that ``viewer_defaults.auto_open``
        lists, else the first section of kind ``structure`` in ``sections``
        order.
        """
        defaults = self._manifest.viewer_defaults
        for section_id in defaults.auto_open if defaults is not None else ():
            if self._is_structure(section_id):
                return section_id
        return next((s.id for s in self._manifest.sections if s.kind == "structure"), None)

    @property
    def shown_structure_id(self) -> str | None:
        """The structure shown and used where none is named, or None without one.

        The first structure until :meth:`show_structure` picks another.
        ``read_structure()`` without an id reads it, and every consumer that
        shows or exports "the structure" goes through that.
        """
        if self._shown_structure_id is not None and self._is_structure(self._shown_structure_id):
            return self._shown_structure_id
        return self.first_structure_id

    @property
    def shown_structure(self) -> Section | None:
        """The section of :attr:`shown_structure_id`, or None."""
        section_id = self.shown_structure_id
        return None if section_id is None else self._section_by_id[section_id]

    def show_structure(self, section_id: str | None) -> None:
        """Make ``section_id`` the shown structure; None returns to the first one.

        Raises :class:`SectionNotFoundError` for an unknown id and
        :class:`QVFError` for a section of another kind, leaving the choice
        unchanged. An edit made on a structure stays with it (see
        :meth:`set_edit_overlay`). Changing the choice bumps
        :attr:`geometry_revision`, since ``read_structure()`` now returns
        other atoms.
        """
        if section_id is not None:
            section = self.get_section(section_id)
            if section.kind != "structure":
                raise QVFError(f"section {section_id!r} is a {section.kind!r}, not a structure")
        previous = self.shown_structure_id
        self._shown_structure_id = section_id
        if self.shown_structure_id != previous:
            self.geometry_revision += 1

    def structure_ref_of(self, section_id: str) -> str | None:
        """The structure a section describes (QVF spec § 4.1), or None.

        A structure describes itself. Any other section describes the
        structure its ``structure_ref`` names; one without a usable
        reference makes no statement, and the reader keeps its usual
        behaviour of relating it to the first structure.
        """
        section = self.get_section(section_id)
        if section.kind == "structure":
            return section.id
        return self._named_structure(section) or self.first_structure_id

    def structure_identity(self, section_id: str | None = None) -> StructureIdentity:
        """The chemical identity a ``structure`` section object states (spec § 5.1).

        ``section_id`` defaults to the shown structure. Without a structure,
        or for fields of the wrong type, the identity states nothing.
        """
        section_id = self.shown_structure_id if section_id is None else section_id
        if section_id is None or not self._is_structure(section_id):
            return StructureIdentity()
        section = self._section_by_id[section_id]
        extra = section.model_extra or {}

        def text(key: str) -> str | None:
            value = extra.get(key)
            return value if isinstance(value, str) and value.strip() else None

        def integer(key: str) -> int | None:
            value = extra.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) else None

        multiplicity = integer("multiplicity")
        return StructureIdentity(
            label=text("label"),
            name=text("name"),
            charge=integer("charge"),
            multiplicity=multiplicity if multiplicity is None or multiplicity >= 1 else None,
            smiles=text("smiles"),
            inchi=text("inchi"),
            inchikey=text("inchikey"),
        )

    def declared_charge_multiplicity(
        self, section_id: str | None = None
    ) -> tuple[int | None, int | None]:
        """The charge and multiplicity the archive states for a structure.

        Each from the structure's identity (spec § 5.1, ``section_id``
        defaulting to the shown structure), else from ``provenance``
        (§ 3.2) when the archive has at most one structure section: with
        several, the provenance describes none of them in particular. A
        missing, non-integer or (for multiplicity) non-positive value is
        None.
        """
        identity = self.structure_identity(section_id)
        charge, multiplicity = identity.charge, identity.multiplicity
        if len(self.structure_sections) <= 1:
            provenance = self.provenance

            def integer(key: str) -> int | None:
                value = provenance.get(key)
                return value if isinstance(value, int) and not isinstance(value, bool) else None

            if charge is None:
                charge = integer("charge")
            if multiplicity is None:
                stated = integer("multiplicity")
                multiplicity = stated if stated is None or stated >= 1 else None
        return charge, multiplicity

    def section_error(self, section_id: str) -> str | None:
        """Return the first recorded member error for this section, or None."""
        error = self._section_errors.get(section_id)
        return error.message if error is not None else None

    def section_error_kind(self, section_id: str) -> str | None:
        """Return the recorded error category for a section, or None."""
        error = self._section_errors.get(section_id)
        return error.kind if error is not None else None

    def _record_section_error(
        self, section_id: str, error: QVFError, kind: str = "invalid payload"
    ) -> None:
        if isinstance(error, SHA256MismatchError):
            kind = "sha256 mismatch"
        self._section_errors.setdefault(section_id, _SectionError(str(error), kind))

    # ── sha256 verification (Rule 4) ──────────────────────────────────

    def _verify_and_read(self, member: MemberSpec) -> bytes:
        """Read a member from the zip and verify its sha256.

        Returns the raw bytes. Raises SHA256MismatchError on mismatch.
        """
        data = self.read_member_bytes(member.path)
        actual = hashlib.sha256(data).hexdigest()
        if actual != member.sha256:
            raise SHA256MismatchError(
                f"sha256 mismatch for {member.path!r}: expected {member.sha256}, got {actual}"
            )
        return data

    def read_member_bytes(self, path: str) -> bytes:
        """The raw bytes of zip entry ``path``, unverified.

        Every failure to read the entry is a ``QVFError`` naming it: a
        missing entry, a CRC or deflate error, an encrypted entry or an
        unsupported compression method. Only ``BadZipFile`` was mapped
        before, so the rest reached the CLI as tracebacks (#39-35).
        """
        try:
            return self._zf.read(path)
        except KeyError:
            raise QVFError(
                f"member file {path!r} declared in manifest but missing from zip"
            ) from None
        except _ARCHIVE_READ_ERRORS as e:
            raise QVFError(f"cannot read zip member {path!r}: {e}") from None

    def manifest_json(self) -> dict[str, Any]:
        """A fresh copy of manifest.json as parsed JSON, unknown fields included.

        The reader parsed it once while opening, so this does not read the
        archive again (and cannot fail on it).
        """
        return cast(dict[str, Any], json.loads(self._manifest_text))

    def _read_member(
        self, section_id: str, member_name: str, *, expect_format: str | None = None
    ) -> bytes:
        """Read verified member bytes and record failures for every section kind.

        ``expect_format`` checks the manifest's declared ``format`` before the
        bytes are decoded, so a member declared ``binary`` is never parsed as
        JSON and vice versa (#35-27). The schema pins the format of every
        canonical member, but vendor sections are unpinned.
        """
        section = self.get_section(section_id)
        try:
            member = section.members.get(member_name)
            if member is None:
                raise QVFError(f"member {member_name!r} not found in section {section_id!r}")
            if expect_format is not None and member.format != expect_format:
                raise QVFError(
                    f"member {member.path!r} in section {section_id!r} is declared "
                    f"format {member.format!r}, but is read as {expect_format!r}"
                )
            return self._verify_and_read(member)
        except QVFError as error:
            self._record_section_error(section_id, error, "member read error")
            raise

    def _read_text_member(
        self,
        section_id: str,
        member_name: str,
        *,
        errors: str = "strict",
        expect_format: str | None = None,
    ) -> str:
        raw = self._read_member(section_id, member_name, expect_format=expect_format)
        try:
            return raw.decode("utf-8", errors=errors)
        except UnicodeDecodeError as error:
            member = self.get_section(section_id).members[member_name]
            failure = QVFError(
                f"member {member.path!r} in section {section_id!r} is not UTF-8: {error}"
            )
            self._record_section_error(section_id, failure)
            raise failure from None

    def _read_json_member(self, section_id: str, member_name: str) -> Any:
        """Read + verify a JSON member. Returns parsed JSON."""
        raw = self._read_text_member(section_id, member_name, expect_format="json")
        try:
            value = json.loads(raw)
        except _JSON_PARSE_ERRORS as error:
            member = self.get_section(section_id).members[member_name]
            failure = QVFError(
                f"member {member.path!r} in section {section_id!r} is not valid JSON: {error}"
            )
            self._record_section_error(section_id, failure)
            raise failure from None
        if _json_depth_exceeds(value, _MAX_JSON_DEPTH):
            member = self.get_section(section_id).members[member_name]
            failure = QVFError(
                f"member {member.path!r} in section {section_id!r} nests deeper than "
                f"{_MAX_JSON_DEPTH} levels"
            )
            self._record_section_error(section_id, failure)
            raise failure
        bad_string = _lone_surrogate_in(raw, value)
        if bad_string is not None:
            member = self.get_section(section_id).members[member_name]
            failure = QVFError(
                f"member {member.path!r} in section {section_id!r} has a string with an "
                f"unpaired UTF-16 surrogate at {bad_string}, which is not Unicode text"
            )
            self._record_section_error(section_id, failure)
            raise failure
        return value

    def _read_binary_member(self, section_id: str, member_name: str) -> np.ndarray:
        """Read + verify a binary member. Returns numpy array (cached)."""
        cache_key = (section_id, member_name)
        cached = self._binary_cache.get(cache_key)
        if cached is not None:
            return cached
        raw = self._read_member(section_id, member_name, expect_format="binary")
        member = self.get_section(section_id).members[member_name]
        dtype = member.dtype or "float32"
        try:
            arr = np.frombuffer(raw, dtype=np.dtype(dtype))
            if member.shape is not None:
                arr = arr.reshape(member.shape)
        except ValueError as e:
            error = QVFError(
                f"member {member.path!r} in section {section_id!r} cannot be "
                f"read as dtype={dtype!r}, shape={member.shape!r}: {e}"
            )
            self._record_section_error(section_id, error)
            raise error from None
        self._binary_cache[cache_key] = arr
        return arr

    def stored_size(self, member: MemberSpec) -> int | None:
        """Uncompressed size in bytes of ``member`` in the archive, or None if absent."""
        try:
            return int(self._zf.getinfo(member.path).file_size)
        except KeyError:
            return None

    def cached_bytes(self) -> dict[str, int]:
        """Bytes of decoded binary members this reader holds, per section id.

        The binary cache is where a volume's or trajectory's arrays live once
        read, so this is the figure a memory readout wants (#35-12); the
        wrapper objects around it are a few dozen bytes.
        """
        usage: dict[str, int] = {}
        for (section_id, _member), arr in self._binary_cache.items():
            usage[section_id] = usage.get(section_id, 0) + int(arr.nbytes)
        return usage

    def drop_cached(self, section_id: str) -> int:
        """Release the cached arrays of one section; return the bytes released.

        The next read decodes the member from the archive again. Arrays a
        caller still references stay alive until the caller lets go.
        """
        freed = 0
        for key in [k for k in self._binary_cache if k[0] == section_id]:
            freed += int(self._binary_cache.pop(key).nbytes)
        for key in [k for k in self._validated_frame_arrays if k[0] == section_id]:
            del self._validated_frame_arrays[key]
        return freed

    # ── Eager data extraction ─────────────────────────────────────────

    def set_edit_overlay(
        self,
        positions: np.ndarray | Sequence[Sequence[float]],
        symbols: Sequence[str],
        *,
        lattice_vectors: np.ndarray | Sequence[Sequence[float]] | None = None,
        section_id: str | None = None,
    ) -> None:
        """Record viewer-side edited geometry (see ``_edit_overlays``).

        ``positions`` is an (n, 3) sequence in angstroms, ``symbols`` the
        matching element symbols. Raises ``ValueError``, leaving the current
        overlay untouched, for a wrong shape, a non-finite coordinate, a
        symbol outside the element table, or an archive without the
        structure. Subsequent :meth:`read_structure` calls for that
        structure return this geometry (with explicit bonds dropped — atom
        indices from the file no longer apply) instead of the archive's.

        The edit belongs to ``section_id``, by default the shown structure,
        and stays with it while another structure is shown.

        ``lattice_vectors`` replaces the current edit-overlay lattice when
        supplied. Omitting it preserves the current lattice, which lets
        ordinary atom edits made after a supercell build keep that expanded
        cell. The lattice is reset together with the atoms by
        :meth:`clear_edit_overlay`.
        """
        target = self.shown_structure_id if section_id is None else section_id
        if target is None or not self._is_structure(target):
            raise ValueError(
                "no structure section to edit"
                if target is None
                else f"section {target!r} is not a structure section"
            )

        lattice = None
        if lattice_vectors is not None:
            lattice = np.asarray(lattice_vectors, dtype=np.float64)
            if lattice.shape != (3, 3) or not np.all(np.isfinite(lattice)):
                raise ValueError("lattice_vectors must be a finite 3x3 matrix")
            # DegenerateLatticeError is a ValueError. A singular cell keeps the
            # bond search's single-shell fallback.
            with contextlib.suppress(np.linalg.LinAlgError):
                interplanar_spacings(lattice)

        coords = np.asarray(positions, dtype=np.float64)
        symbol_list = [str(sym) for sym in symbols]
        # Validate before touching any state (#35-13). An (n, 3) finite array
        # only: a 2-component position used to be stored and crashed later in
        # a renderer. An unknown symbol used to become Z=0, which the bond
        # rule treats as a metal with the 1.5 A fallback radius, so one typo
        # could switch metal-metal bonding for the whole structure.
        if coords.ndim != 2 or coords.shape[1:] != (3,) or len(coords) != len(symbol_list):
            raise ValueError(
                f"positions must be an (n, 3) array matching {len(symbol_list)} symbols, "
                f"got shape {coords.shape}"
            )
        if not np.all(np.isfinite(coords)):
            raise ValueError("positions must be finite")
        unknown = sorted({sym for sym in symbol_list if sym not in SYMBOL_TO_Z})
        if unknown:
            raise ValueError(f"unknown element symbol(s) {unknown}")
        atoms = [
            Atom(symbol=sym, position=coords[i].copy(), atomic_number=SYMBOL_TO_Z[sym])
            for i, sym in enumerate(symbol_list)
        ]
        previous = self._edit_overlays.get(target)
        if lattice is not None:
            edited_lattice: np.ndarray | None = lattice.copy()
        else:
            edited_lattice = previous[1] if previous is not None else None
        self._edit_overlays[target] = (atoms, edited_lattice)
        self.geometry_revision += 1
        self.clear_wavefunction_overlays(
            [sid for sid in self.wavefunction_overlay_ids if sid.startswith(RELOCALIZED_PREFIX)]
        )

    def clear_edit_overlay(self, section_id: str | None = None) -> None:
        """Drop viewer-side edits; read_structure() returns the file again.

        ``section_id`` drops only that structure's edit; by default every
        structure's edit is dropped.
        """
        if section_id is None:
            self._edit_overlays.clear()
        else:
            self._edit_overlays.pop(section_id, None)
        self.geometry_revision += 1
        self.clear_wavefunction_overlays(
            [sid for sid in self.wavefunction_overlay_ids if sid.startswith(RELOCALIZED_PREFIX)]
        )

    @property
    def has_edit_overlay(self) -> bool:
        """Whether any structure carries viewer-side edits."""
        return bool(self._edit_overlays)

    @property
    def edited_structure_ids(self) -> list[str]:
        """The structure sections that carry viewer-side edits, in edit order."""
        return list(self._edit_overlays)

    def set_wavefunction_overlay(
        self, section_id: str, wavefunction: WavefunctionGTOData
    ) -> None:
        """Register a viewer-computed wavefunction under ``section_id``.

        The id also gets a synthetic :class:`Section` so the sidebar and
        every ``reader.sections`` consumer see it exactly like an archived
        one. The section carries no members: nothing will read them, because
        :meth:`read_wavefunction_gto` short-circuits on the overlay first.

        Registering the same id twice replaces the payload and leaves the
        single synthetic section in place, so re-running a criterion does not
        accumulate sidebar rows.
        """
        self._wavefunction_overlays[section_id] = wavefunction
        if section_id not in self._section_by_id:
            section = Section(id=section_id, kind="wavefunction.gto")
            self._section_by_id[section_id] = section
            self._manifest.sections.append(section)

    def clear_wavefunction_overlays(self, section_ids: list[str] | None = None) -> None:
        """Drop selected (or all) viewer-computed wavefunctions and their sections."""
        for section_id in list(self._wavefunction_overlays) if section_ids is None else section_ids:
            if section_id not in self._wavefunction_overlays:
                continue
            section = self._section_by_id.pop(section_id, None)
            if section is not None and section in self._manifest.sections:
                self._manifest.sections.remove(section)
            self._wavefunction_overlays.pop(section_id)

    @property
    def wavefunction_overlay_ids(self) -> list[str]:
        """Section ids currently backed by a viewer-computed wavefunction."""
        return list(self._wavefunction_overlays)

    def _read_structure_payload(self, section_id: str) -> dict[str, Any]:
        """Validate file geometry before constructing atoms or resolving indices.

        The member's bytes are read and sha256-verified on every call, so a
        changed archive is always caught. Once verified bytes match a
        payload already parsed and validated for that digest, the parse and
        the validation are not repeated: the cached payload is returned, and
        callers must not mutate it (``read_structure`` builds fresh atoms).
        """
        member = self.get_section(section_id).members["structure"]
        validation_key = (section_id, member.sha256, self._read_version)
        cached = self._structure_payloads.get(validation_key)
        if cached is not None:
            self._read_member(section_id, "structure", expect_format="json")
            return cached
        raw = self._read_json_member(section_id, "structure")
        if isinstance(raw, dict) and "pbc" not in raw:
            dim = raw.get("dimensionality")
            # QVF v0.1.0 §5.1 permits this pre-conformance accommodation.
            # Explicit pbc remains authoritative; archive bytes are unchanged.
            if not isinstance(dim, bool) and isinstance(dim, int | float) and dim in (0, 1, 2, 3):
                raw = {**raw, "pbc": [axis < dim for axis in range(3)]}
        try:
            _STRUCTURE_VALIDATORS[self._read_version].validate(raw)
            assert isinstance(raw, dict)  # guaranteed by StructurePayload
            positions = np.asarray([atom["position"] for atom in raw["atoms"]], dtype=float)
            if not np.all(np.isfinite(positions)):
                raise QVFError("atom positions must be finite")
            numbers = [
                _atomic_number(atom["atomic_number"], f"atoms[{i}].atomic_number")
                for i, atom in enumerate(raw["atoms"])
            ]
            lattice = raw.get("lattice_vectors")
            if lattice is not None:
                cell = np.asarray(lattice, dtype=float)
                if not np.all(np.isfinite(cell)):
                    raise QVFError("lattice_vectors must be finite")
                pbc = [bool(flag) for flag in raw.get("pbc", [])][:3]
                if any(pbc):
                    try:
                        np.linalg.inv(cell)
                    except np.linalg.LinAlgError:
                        raise QVFError("periodic lattice_vectors must be invertible") from None
                    # Finite, invertible vectors can still give a zero or
                    # infinite interplanar spacing (1e200, 1e-300, -DBL_MAX),
                    # or atoms too many cells apart to search for bonds; the
                    # bond search then raised ZeroDivisionError (#39-35).
                    try:
                        periodic_image_shells(positions, numbers, cell, pbc)
                    except DegenerateLatticeError as exc:
                        raise QVFError(f"periodic lattice_vectors: {exc}") from None
            # Geometry the bond search would refuse (a supercell written with
            # its unit cell, overlapping atoms) is a section error now, not a
            # process that allocates until it is killed when first drawn
            # (#62). Bounded work: no distance is computed here.
            flags = [bool(flag) for flag in raw.get("pbc", [])][:3]
            try:
                check_bond_search(
                    positions,
                    numbers,
                    lattice=np.asarray(lattice, dtype=float) if lattice is not None else None,
                    pbc=flags,
                )
            except BondSearchLimitError as exc:
                raise QVFError(str(exc)) from None
        except (jsonschema.ValidationError, QVFError, OverflowError) as exc:
            detail = (
                f"{exc.json_path}: {exc.message}"
                if isinstance(exc, jsonschema.ValidationError) else str(exc)
            )
            error = QVFError(f"structure member in section {section_id!r}: {detail}")
            self._record_section_error(section_id, error)
            raise error from None
        self._structure_payloads[validation_key] = raw
        return raw

    def _file_atom_count(self, section_id: str) -> int | None:
        """Atoms of the geometry ``section_id`` certainly describes, as the file has them.

        Never the edited or caller-supplied atoms. The bound that rejects an
        out-of-range index comes from the structure the section's
        ``structure_ref`` names, or from the archive's only structure. A
        section without a reference in an archive with several structures
        makes no statement (spec § 4.1), so its indices are not bounded by a
        guess, and neither are those of a standalone analysis.
        """
        section = self.get_section(section_id)
        structure_id = self._named_structure(section)
        if structure_id is None:
            structures = self.structure_sections
            structure_id = structures[0].id if len(structures) == 1 else None
        if structure_id is None:
            return None
        return len(self._read_structure_payload(structure_id)["atoms"])

    def _describing(self, kind: str, structure_id: str) -> Section | None:
        """The first section of ``kind`` that describes ``structure_id``."""
        return next(
            (
                s for s in self._manifest.sections
                if s.kind == kind and self.structure_ref_of(s.id) == structure_id
            ),
            None,
        )

    def _read_pair_payload(
        self, section_id: str, member_name: str, n_atoms: int | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        raw = self._read_json_member(section_id, member_name)
        try:
            pairs = _parse_ij_pairs(raw, n_atoms)
        except QVFError as exc:
            error = QVFError(f"{member_name} member in section {section_id!r}: {exc}")
            self._record_section_error(section_id, error)
            raise error from None
        return raw, pairs

    def read_structure(
        self, section_id: str | None = None, *, apply_edits: bool = True
    ) -> StructureData:
        """Read + verify a structure section. Always eager.

        ``section_id`` defaults to the shown structure
        (:attr:`shown_structure_id`, the first structure unless
        :meth:`show_structure` chose another); an archive without one raises
        :class:`SectionNotFoundError`. Wavefunctions may reference a
        different structure, which callers can request explicitly so the
        displayed atoms, cell, and AO centres stay registered.

        Set ``apply_edits=False`` to read the archived reference geometry,
        for example when constructing a displacement from a normal mode.
        When an edit overlay is set (:meth:`set_edit_overlay`), it applies
        only to the structure it was made on. Its returned atoms are the
        edited geometry and ``bonds`` is None (auto-bonding applies).
        PBC/dimensionality still come from the file; the lattice comes from
        the overlay when an edit replaced it. The file's biomolecular metadata
        does not survive an edit: the overlay atoms carry no residue, chain or
        B-factor fields, and the producer-supplied residues, chains and
        secondary structure index the replaced atom list, so they are dropped.
        """
        if section_id is None:
            section_id = self.shown_structure_id
            if section_id is None:
                raise SectionNotFoundError("the archive has no structure section")
        raw = self._read_structure_payload(section_id)
        pbc, dim = _resolve_pbc(raw)
        lattice = raw.get("lattice_vectors")
        if lattice is not None:
            lattice = np.array(lattice, dtype=np.float64)

        overlay = self._edit_overlays.get(section_id) if apply_edits else None
        if overlay is not None:
            # The overlay replaces the atoms wholesale, so the file's atom
            # list, bonds and biomolecular metadata are never built for it.
            edited_atoms, edited_lattice = overlay
            return StructureData(
                atoms=list(edited_atoms),
                pbc=pbc,
                lattice_vectors=edited_lattice.copy() if edited_lattice is not None else lattice,
                bonds=None,  # file bond indices are stale after an edit
                dim=dim,
                # Supplied residue atom_indices index the file's atom list,
                # which the overlay has replaced, so they are as stale as the
                # bond indices above.
            )

        atoms = [_atom_from_json(a) for a in raw["atoms"]]

        # Try to read explicit bonds if present. Early draft archives
        # embedded a `bonds` member in the structure section; canonical
        # QVF v1 archives carry a separate `kind: bonds` section.
        bonds = None
        structure_section = self.get_section(section_id)

        # Producer-supplied biomolecule metadata, peer keys on the section
        # object (QVF spec § 5.1). Captured through Pydantic's extra="allow",
        # so an archive predating the fields simply has none of them.
        extra = structure_section.model_extra or {}
        supplied_residues = extra.get("residues")
        supplied_chains = extra.get("chains")
        supplied_ss = extra.get("secondary_structure")

        # A section-level b_factors array fills in only where the atom did
        # not carry its own: the per-atom field cannot desynchronize from its
        # atom, so it wins. A length mismatch means the array does not
        # describe this atom list, and applying it partially would silently
        # mislabel every atom past the first divergence, so drop it whole.
        b_factors = extra.get("b_factors")
        if isinstance(b_factors, list) and len(b_factors) == len(atoms):
            for atom, value in zip(atoms, b_factors, strict=True):
                if atom.b_factor is None:
                    atom.b_factor = _opt_float(value)

        if "bonds" in structure_section.members:
            try:
                bonds = self.read_bonds(section_id, n_atoms=len(atoms))
            except QVFError as exc:
                self._record_section_error(section_id, exc)
                raise
        else:
            # A standalone ``bonds`` section belongs to the structure it
            # names, or, naming none, to the first structure (spec § 4.1).
            bond_section = self._describing("bonds", section_id)
            if bond_section is not None:
                try:
                    bonds = self.read_bonds(bond_section.id, n_atoms=len(atoms))
                except QVFError as e:
                    self._record_section_error(bond_section.id, e)
                    raise QVFError(
                        f"explicit bonds section {bond_section.id!r} could not be read: {e}"
                    ) from None

        return StructureData(
            atoms=atoms,
            pbc=pbc,
            lattice_vectors=lattice,
            bonds=bonds,
            dim=dim,
            supplied_residues=(
                supplied_residues if isinstance(supplied_residues, list) else None
            ),
            supplied_chains=(
                [str(c) for c in supplied_chains]
                if isinstance(supplied_chains, list)
                else None
            ),
            supplied_secondary_structure=(
                supplied_ss if isinstance(supplied_ss, list) else None
            ),
        )

    def read_bonds(self, section_id: str, *, n_atoms: int | None = None) -> list[Bond]:
        """Read explicit pairs, checking their original structure when available.

        Omitted orders retain 1.0. Explicit payload images are not specified
        by QVF v0.1.0 §5.1; retain the existing zero-image convention.
        """
        if n_atoms is None:
            section = self.get_section(section_id)
            n_atoms = (
                len(self._read_structure_payload(section_id)["atoms"])
                if section.kind == "structure" else self._file_atom_count(section_id)
            )
        _, pairs = self._read_pair_payload(section_id, "bonds", n_atoms)
        return [(p["i"], p["j"], p.get("order", 1.0), (0, 0, 0)) for p in pairs]

    def connectivity(
        self, structure: StructureData, section_id: str | None = None
    ) -> list[Bond]:
        """The bonds every surface draws for ``structure``.

        The producer's explicit bonds when the structure carries them,
        otherwise the covalent-radius inference (:func:`vibeview.bonds.
        structure_bonds`); inferred bonds carry order 1.0, since radii
        cannot recover multiplicity. When the archive has a ``bond_orders``
        section for the structure section ``structure`` was read from
        (``section_id``, by default the shown structure), its per-pair
        orders (Mayer/Wiberg, computed from the wavefunction) replace the
        orders for the pairs it lists.
        """
        bonds = structure_bonds(structure)

        # Enrich with bond_orders data if available.
        structure_id = self.shown_structure_id if section_id is None else section_id
        if structure_id is None:
            # A caller-supplied structure in an archive without one: the
            # archive's analysis is the only candidate.
            bo_section = next(
                (s for s in self._manifest.sections if s.kind == "bond_orders"), None
            )
        else:
            bo_section = self._describing("bond_orders", structure_id)
        if bo_section is not None:
            bo_data = self.read_bond_orders(bo_section.id)
            # Build a lookup map: (i, j) → order
            order_map: dict[tuple[int, int], float] = {}
            for p in bo_data.pairs:
                i, j = cast(int, p["i"]), cast(int, p["j"])
                order_map[(min(i, j), max(i, j))] = cast(float, p.get("order", 1.0))
            # Override bond orders where we have bond_orders data.
            bonds = [
                (i, j, order_map.get((min(i, j), max(i, j)), order), image)
                for i, j, order, image in bonds
            ]

        return bonds

    # ── Lazy binary extraction for volumes ────────────────────────────

    def _payload_failure(self, section_id: str, member_name: str, error: QVFError) -> QVFError:
        failure = QVFError(f"{member_name} member in section {section_id!r}: {error}")
        self._record_section_error(section_id, failure)
        return failure

    def _read_grid_payload(
        self, section_id: str, member_name: str, data_name: str, *, frame_axis: bool = False,
    ) -> GridData:
        raw = self._read_json_member(section_id, member_name)
        member = self.get_section(section_id).members.get(data_name)
        try:
            if member is None or member.shape is None:
                raise QVFError(f"missing binary shape for {data_name!r}")
            shape = member.shape[1:] if frame_axis else member.shape
            return _grid_from_json(raw, shape)
        except QVFError as error:
            if member is not None:
                error = QVFError(f"{error}; paired binary member {member.path!r}")
            raise self._payload_failure(section_id, member_name, error) from None

    def read_volume_grid(self, section_id: str) -> GridData:
        """Read a finite grid descriptor consistent with its lazy binary member."""
        return self._read_grid_payload(section_id, "grid", "data")

    def read_volume_data(self, section_id: str) -> np.ndarray:
        """Read + verify volume data, including its pairing with the grid."""
        grid = self.read_volume_grid(section_id)
        data = self._read_binary_member(section_id, "data")
        if data.shape != grid.shape:
            raise self._payload_failure(
                section_id, "data", QVFError("cached data shape differs from grid shape"),
            )
        return data

    # ── Other section readers ─────────────────────────────────────────

    def read_bands(self, section_id: str) -> BandsData:
        kpath = self._read_json_member(section_id, "kpath")
        eigenvalues = self._read_binary_member(section_id, "eigenvalues")
        if eigenvalues.ndim == 2:
            eigenvalues = eigenvalues[np.newaxis, :, :]
        elif eigenvalues.ndim != 3:
            raise QVFError(
                f"bands section {section_id!r}: eigenvalues must have rank 3 "
                f"[n_spin, n_kpoints, n_bands], got shape {eigenvalues.shape}"
            )
        n_kpoints = kpath.get("n_kpoints")
        if n_kpoints is not None and int(n_kpoints) != eigenvalues.shape[1]:
            raise QVFError(
                f"bands section {section_id!r}: kpath n_kpoints={n_kpoints} "
                f"does not match eigenvalues shape {eigenvalues.shape}"
            )
        n_bands = kpath.get("n_bands")
        if n_bands is not None and int(n_bands) != eigenvalues.shape[2]:
            raise QVFError(
                f"bands section {section_id!r}: kpath n_bands={n_bands} "
                f"does not match eigenvalues shape {eigenvalues.shape}"
            )
        fermi = kpath.get("fermi")
        if fermi is not None and (
            isinstance(fermi, bool)
            or not isinstance(fermi, int | float)
            or not math.isfinite(float(fermi))
        ):
            raise QVFError(
                f"bands section {section_id!r}: kpath fermi must be a finite number, "
                f"got {fermi!r}"
            )
        # A non-finite eigenvalue cannot be drawn, and one near the limit of a
        # double overflows every axis range: +-DBL_MAX made matplotlib raise
        # on the E - E_F span (#63). Sixteen times the span must still be
        # finite, which leaves room for the margins a plot adds.
        with np.errstate(over="ignore", invalid="ignore"):
            shifted = np.asarray(eigenvalues, dtype=float) - float(fermi or 0.0)
            span_ok = shifted.size == 0 or bool(np.isfinite(np.ptp(shifted) * 16.0))
        if not np.all(np.isfinite(shifted)) or not span_ok:
            error = QVFError(
                f"bands section {section_id!r}: eigenvalues must be finite and "
                "span a range a plot can draw (well inside double precision)"
            )
            self._record_section_error(section_id, error)
            raise error
        return BandsData(kpath=kpath, eigenvalues=eigenvalues, fermi=fermi)

    def read_phonon_bands(self, section_id: str) -> PhononBandsData:
        """Read a phonon_bands section (QVF spec §4.13).

        ``qpath`` mirrors the electronic kpath (labeled segments with
        ``n_points``); ``frequencies`` is ``[n_qpoints, n_modes]`` in cm^-1.
        """
        qpath = self._read_json_member(section_id, "qpath")
        frequencies = self._read_binary_member(section_id, "frequencies")
        if frequencies.ndim != 2:
            raise QVFError(
                f"phonon_bands section {section_id!r}: frequencies must have rank 2 "
                f"[n_qpoints, n_modes], got shape {frequencies.shape}"
            )
        return PhononBandsData(qpath=qpath, frequencies=frequencies)

    def read_phonon_dos(self, section_id: str) -> PhononDOSData:
        """Read a phonon_dos section (QVF spec §4.13).

        ``frequencies`` and ``dos`` are ``[n_points]``; ``meta`` is required
        producer metadata (smearing, n_atoms, …), possibly an empty object.
        """
        frequencies = self._read_binary_member(section_id, "frequencies")
        dos = self._read_binary_member(section_id, "dos")
        meta = self._read_json_member(section_id, "meta")
        return PhononDOSData(frequencies=frequencies, dos=dos, meta=meta)

    def read_equation_of_state(self, section_id: str) -> EquationOfStateData:
        """Read an equation_of_state section (QVF spec §4.14).

        ``volumes`` / ``energies`` are the sampled V-E points (Angstrom^3 / eV);
        ``fit`` carries the producer's fitted EOS params (``model``, ``V0``,
        ``E0``, ``B0`` in GPa, ``B0_prime``, optional unit strings).
        """
        volumes = self._read_binary_member(section_id, "volumes")
        energies = self._read_binary_member(section_id, "energies")
        fit = self._read_json_member(section_id, "fit")
        return EquationOfStateData(volumes=volumes, energies=energies, fit=fit)

    def read_spectra(self, section_id: str) -> SpectraData:
        """Read a ``spectra.*`` section.

        ``frequencies`` and ``intensities`` must be flat lists of finite
        numbers with one shared length; anything else is a :class:`QVFError`
        recorded against the section (#66).
        """
        raw = self._read_json_member(section_id, "spectrum")
        try:
            return _spectra_from_json(_json_object(raw))
        except QVFError as error:
            raise self._payload_failure(section_id, "spectrum", error) from None
        except (KeyError, TypeError, ValueError) as error:
            raise self._payload_failure(
                section_id,
                "spectrum",
                QVFError(f"malformed payload ({type(error).__name__}: {error})"),
            ) from None

    def _read_frame_payload(
        self, section_id: str,
    ) -> tuple[dict[str, Any], list[Atom], np.ndarray, list[float] | None]:
        raw = self._read_json_member(section_id, "metadata")
        coords = self._read_binary_member(section_id, "coords")
        try:
            meta = _json_object(raw)
            atoms = _frame_atoms_from_json(meta)
            energies = _frame_energies(meta)
        except QVFError as error:
            raise self._payload_failure(section_id, "metadata", error) from None
        member = self.get_section(section_id).members["coords"]
        key = (section_id, member.sha256)
        try:
            if coords.ndim != 3 or coords.shape[1:] != (len(atoms), 3):
                raise QVFError(
                    f"coords shape {coords.shape} must be (n_frames, {len(atoms)}, 3)"
                )
            if coords.dtype != np.dtype(member.dtype or "float64"):
                raise QVFError("cached coords dtype differs from its declared binary dtype")
            if coords.shape != tuple(member.shape or []):
                raise QVFError("cached coords shape differs from its declared binary shape")
            immutable = _has_immutable_bytes(coords)
            if not immutable or self._validated_frame_arrays.get(key) is not coords:
                if not np.all(np.isfinite(coords)):
                    raise QVFError("coords must contain only finite positions")
                if immutable:
                    self._validated_frame_arrays[key] = coords
        except QVFError as error:
            raise self._payload_failure(section_id, "coords", error) from None
        return meta, atoms, coords, energies

    def read_trajectory(self, section_id: str) -> TrajectoryData:
        _, atoms, coords, energies = self._read_frame_payload(section_id)
        return TrajectoryData(atoms=atoms, coords=coords, energies=energies)

    def read_vibrations(self, section_id: str) -> VibrationsData:
        meta = self._read_json_member(section_id, "metadata")
        displacements = self._read_binary_member(section_id, "displacements")
        try:
            meta = _json_object(meta)
            if "atoms" in meta:
                atoms = [_atom_from_json(a) for a in meta["atoms"]]
            else:
                # QVF § 5.5 makes the embedded reference geometry optional.
                # Modes belong to the referenced (or first) archived structure,
                # independent of the currently shown structure and its edits.
                reference = self.structure_ref_of(section_id)
                if reference is None:
                    raise QVFError("vibrations without atoms need a reference structure")
                atoms = self.read_structure(reference, apply_edits=False).atoms
                if "symbols" in meta and meta["symbols"] != [a.symbol for a in atoms]:
                    raise QVFError("vibration symbols do not match the reference structure")
            frequencies = _mo_vector(meta["frequencies"], "frequencies")
        except (QVFError, KeyError, TypeError, ValueError) as error:
            raise self._payload_failure(
                section_id, "metadata", QVFError(str(error))
            ) from None
        # A bare number, or displacements of another shape, used to pass the
        # reader and then fail in ``table --kind vibrations`` and ``animate``
        # (#39-35). All 512 local vibrations sections have this shape.
        expected = (len(frequencies), len(atoms), 3)
        if displacements.shape != expected:
            raise QVFError(
                f"vibrations section {section_id!r}: displacements have shape "
                f"{displacements.shape}, expected (n_modes, n_atoms, 3) = {expected}"
            )
        return VibrationsData(atoms=atoms, frequencies=frequencies, displacements=displacements)

    def read_wavefunction_gto(self, section_id: str) -> WavefunctionGTOData:
        """Read a `wavefunction.gto` section (basis + MO coefficients).

        A malformed ``basis`` or ``mo_metadata`` payload (a missing shell key,
        a non-object, a non-numeric value) raises :class:`QVFError` and is
        recorded against the section, never a bare ``KeyError`` (#35-3).
        """
        overlay = self._wavefunction_overlays.get(section_id)
        if overlay is not None:
            return overlay
        basis_raw = self._read_json_member(section_id, "basis")
        meta_raw = self._read_json_member(section_id, "mo_metadata")
        members = self.get_section(section_id).members
        # Member reads record their own failures (sha256, missing, shape), so
        # they happen before the payload parsing that is wrapped below.
        binaries: dict[str, np.ndarray] = {
            name: self._read_binary_member(section_id, name)
            for name in ("mo_coefficients", "mo_coefficients_alpha", "mo_coefficients_beta")
            if name in members
        }
        try:
            data = _wavefunction_from_json(
                _json_object(basis_raw), _json_object(meta_raw), binaries
            )
        except QVFError as error:
            raise self._payload_failure(section_id, "basis/mo_metadata", error) from None
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise self._payload_failure(
                section_id,
                "basis/mo_metadata",
                QVFError(f"malformed payload ({type(error).__name__}: {error})"),
            ) from None
        # The geometry the shell centres index (QVF spec § 4.1): the section's
        # ``structure_ref`` takes precedence over the basis's, and a
        # wavefunction that names no structure describes the first one.
        own = self._named_structure(self.get_section(section_id))
        if own is not None:
            data.structure_ref = own
        elif not self._is_structure(data.structure_ref):
            data.structure_ref = self.first_structure_id or data.structure_ref
        nto = data.transition_orbitals
        if nto is not None and nto.states_ref is not None:
            # The canonical carrier wins over a deprecated excitation_energy_ev.
            index = meta_raw.get("state_index") if isinstance(meta_raw, dict) else None
            if isinstance(index, int) and not isinstance(index, bool) and index >= 1:
                energy = self._excitation_energy_ev(nto.states_ref, index)
                if energy is not None:
                    nto.excitation_energy_ev = energy
        return data

    def _excitation_energy_ev(self, states_ref: str, index: int) -> float | None:
        """The ``excitation_energy`` in eV of state ``index`` of an ``excited_states``
        section (spec § 5.12), or None when the section, the state or a known unit
        is missing. Used only to label orbitals, so it never raises."""
        section = self._section_by_id.get(states_ref)
        if section is None or section.kind != "excited_states" or "states" not in section.members:
            return None
        try:
            payload = self._read_json_member(states_ref, "states")
        except QVFError:
            return None
        states = payload.get("states") if isinstance(payload, dict) else None
        for state in states if isinstance(states, list) else ():
            if not isinstance(state, dict) or state.get("index") != index:
                continue
            energy = state.get("excitation_energy")
            if not isinstance(energy, dict):
                return None
            value = energy.get("value")
            if isinstance(value, bool) or not isinstance(value, int | float):
                return None
            factor = {"ev": 1.0, "hartree": HARTREE_TO_EV}.get(str(energy.get("units")))
            if factor is None or not math.isfinite(value):
                return None
            return float(value) * factor
        return None

    def read_reaction_path(self, section_id: str) -> ReactionPathData:
        """Read a `reaction.path` section (self-contained: frames + waypoints).

        v2 archives may carry a `lattice` binary member + a `dim`
        integer in the metadata JSON; both surface on the returned
        ``ReactionPathData`` (None on v1 / molecular).
        """
        meta, atoms, coords, energies = self._read_frame_payload(section_id)
        try:
            waypoints = _waypoints_from_json(meta, coords.shape[0])
        except QVFError as error:
            raise self._payload_failure(section_id, "metadata", error) from None
        rxn_coord = meta.get("reaction_coordinate")

        # Periodic fields. The lattice binary member is optional: absent for
        # molecular paths. Its presence, not qvf_version, makes a path
        # periodic (QVF spec § 3.1).
        section = self.get_section(section_id)
        lattice = None
        if "lattice" in section.members:
            lattice = self._read_binary_member(section_id, "lattice")
        dim_field = meta.get("dim")
        dim_per_frame = meta.get("dim_per_frame")

        # Optional per-frame volumes (W1). The grid descriptor is tiny
        # (read eagerly); the 4D blob is decimation-bounded so we read
        # it here too. Absent on archives without volumes.
        frame_volumes = None
        volume_grid = None
        if "frame_volumes" in section.members:
            frame_volumes = self._read_binary_member(section_id, "frame_volumes")
            volume_grid = self._read_grid_payload(
                section_id, "volume_grid", "frame_volumes", frame_axis=True,
            )
        vol_frame_index = meta.get("volume_frame_index")

        # The scalar metadata is converted here; a wrong type is a malformed
        # payload, reported as QVFError and recorded, not a bare ValueError
        # (#35-3).
        try:
            return self._reaction_path_data(
                meta, atoms, coords, energies, waypoints, rxn_coord, lattice,
                dim_field, dim_per_frame, frame_volumes, volume_grid, vol_frame_index,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise self._payload_failure(
                section_id,
                "metadata",
                QVFError(f"malformed payload ({type(error).__name__}: {error})"),
            ) from None

    @staticmethod
    def _reaction_path_data(
        meta: dict[str, Any],
        atoms: list[Atom],
        coords: np.ndarray,
        energies: list[float] | None,
        waypoints: list[ReactionWaypoint],
        rxn_coord: Any,
        lattice: np.ndarray | None,
        dim_field: Any,
        dim_per_frame: Any,
        frame_volumes: np.ndarray | None,
        volume_grid: GridData | None,
        vol_frame_index: Any,
    ) -> ReactionPathData:
        return ReactionPathData(
            atoms=atoms,
            coords=coords,
            energies=energies,
            reaction_coordinate=list(rxn_coord) if rxn_coord is not None else None,
            waypoints=waypoints,
            lattice=lattice,
            dim=int(dim_field) if dim_field is not None else None,
            dim_per_frame=[int(d) for d in dim_per_frame] if dim_per_frame else None,
            reaction_coordinate_label=_str_or_none(meta.get("reaction_coordinate_label")),
            reaction_coordinate_unit=_str_or_none(meta.get("reaction_coordinate_unit")),
            frame_volumes=frame_volumes,
            volume_grid=volume_grid,
            volume_frame_index=(
                [int(i) for i in vol_frame_index] if vol_frame_index is not None else None
            ),
            volume_label=_str_or_none(meta.get("volume_label")),
            volume_isovalue=(
                float(meta["volume_isovalue"]) if meta.get("volume_isovalue") is not None else None
            ),
        )

    def read_reaction_waypoints(self, section_id: str) -> ReactionWaypointsData:
        """Read a `reaction.waypoints` annotation that points at a trajectory."""
        section = self.get_section(section_id)
        traj_ref = section.trajectory_ref
        if not traj_ref:
            raise self._payload_failure(
                section_id, "waypoints", QVFError("missing trajectory_ref"),
            )
        raw = self._read_json_member(section_id, "waypoints")
        try:
            payload = _json_object(raw)
            target = self.get_section(str(traj_ref))
            if target.kind != "trajectory":
                raise QVFError(f"trajectory_ref {traj_ref!r} must reference a trajectory")
            coords_member = target.members.get("coords")
            if coords_member is None or coords_member.shape is None:
                raise QVFError(f"trajectory_ref {traj_ref!r} has no coords shape")
            waypoints = _waypoints_from_json(payload, coords_member.shape[0])
        except QVFError as error:
            raise self._payload_failure(section_id, "waypoints", error) from None
        rxn_coord = payload.get("reaction_coordinate")
        return ReactionWaypointsData(
            trajectory_ref=str(traj_ref),
            waypoints=waypoints,
            reaction_coordinate=list(rxn_coord) if rxn_coord is not None else None,
        )

    def read_scan_surface(self, section_id: str) -> ScanSurfaceData:
        """Read a `scan.surface` section (2D relaxed energy grid)."""
        meta = self._read_json_member(section_id, "metadata")
        axis_a = self._read_binary_member(section_id, "axis_a")
        axis_b = self._read_binary_member(section_id, "axis_b")
        energies = self._read_binary_member(section_id, "energies")
        section = self.get_section(section_id)

        atoms = None
        geometries = None
        if "geometries" in section.members:
            geometries = self._read_binary_member(section_id, "geometries")
            # Scan geometries live in the binary member; the metadata atoms
            # carry identities only.
            atoms = [_atom_from_json(a, position=np.zeros(3)) for a in meta.get("atoms", [])]

        def _opt(key: str) -> str | None:
            return _str_or_none(meta.get(key))

        return ScanSurfaceData(
            axis_a=axis_a,
            axis_b=axis_b,
            energies=energies,
            coordinate_a_label=_opt("coordinate_a_label"),
            coordinate_a_unit=_opt("coordinate_a_unit"),
            coordinate_b_label=_opt("coordinate_b_label"),
            coordinate_b_unit=_opt("coordinate_b_unit"),
            atoms=atoms,
            geometries=geometries,
        )

    def read_atom_properties(self, section_id: str) -> AtomPropertiesData:
        """Read population-analysis charges and spin populations."""
        mulliken = None
        loewdin = None
        hirshfeld = None
        spin_pop = None
        section = self.get_section(section_id)
        if "mulliken_charge" in section.members:
            mulliken = self._read_binary_member(section_id, "mulliken_charge")
        if "loewdin_charge" in section.members:
            loewdin = self._read_binary_member(section_id, "loewdin_charge")
        if "hirshfeld_charge" in section.members:
            hirshfeld = self._read_binary_member(section_id, "hirshfeld_charge")
        if "spin_population" in section.members:
            spin_pop = self._read_binary_member(section_id, "spin_population")
        iao = None
        if "iao_charge" in section.members:
            iao = self._read_binary_member(section_id, "iao_charge")
        # Every array is one value per atom, so they must agree. Arrays of
        # different lengths made the atom_properties table index past the
        # shorter one (#63).
        lengths = {
            name: arr.shape
            for name, arr in (
                ("mulliken_charge", mulliken),
                ("loewdin_charge", loewdin),
                ("hirshfeld_charge", hirshfeld),
                ("spin_population", spin_pop),
                ("iao_charge", iao),
            )
            if arr is not None
        }
        if any(len(shape) != 1 for shape in lengths.values()) or len(
            {shape[0] for shape in lengths.values()}
        ) > 1:
            detail = ", ".join(f"{name} {list(shape)}" for name, shape in lengths.items())
            error = QVFError(
                f"atom_properties section {section_id!r}: every array must be one "
                f"value per atom, with one shared length; got {detail}"
            )
            self._record_section_error(section_id, error)
            raise error
        return AtomPropertiesData(
            mulliken_charges=mulliken,
            loewdin_charges=loewdin,
            hirshfeld_charges=hirshfeld,
            spin_populations=spin_pop,
            iao_charges=iao,
        )

    def read_nmr(self, section_id: str) -> NMRData:
        """Read the NMR spectrum payload from a spectra.nmr section.

        The producer hands the writer an opaque dict with conventional
        keys (chemical_shifts, shielding_tensors, j_couplings,
        isotope, reference, solvent); we surface the dict as-is.
        """
        raw = self._read_json_member(section_id, "spectrum")
        if not isinstance(raw, dict):
            raise QVFError(
                f"spectra.nmr section {section_id!r}: 'spectrum' member is not a JSON object"
            )
        return NMRData(raw=raw)

    def read_epr(self, section_id: str) -> EPRData:
        """Read the EPR parameter payload from a spectra.epr section.

        The producer hands the writer an opaque dict with conventional keys
        (g_tensor, hyperfine, zero_field_splitting); we surface it as-is.
        """
        raw = self._read_json_member(section_id, "spectrum")
        if not isinstance(raw, dict):
            raise QVFError(
                f"spectra.epr section {section_id!r}: 'spectrum' member is not a JSON object"
            )
        return EPRData(raw=raw)

    def read_symmetry(self, section_id: str) -> SymmetryData:
        """Read the spglib-style symmetry summary from a
        structure.symmetry section."""
        raw = self._read_json_member(section_id, "data")
        if not isinstance(raw, dict):
            raise QVFError(
                f"structure.symmetry section {section_id!r}: 'data' member is not a JSON object"
            )
        return SymmetryData(raw=raw)

    def read_scf_history(self, section_id: str) -> SCFHistoryData:
        """Read the per-iteration SCF history from a scf_history section.

        Iteration record keys are conventional (see
        :class:`SCFHistoryData`); the renderer tolerates missing keys.
        """
        raw = self._read_json_member(section_id, "iterations")
        iters = raw.get("iterations") if isinstance(raw, dict) else None
        if not isinstance(iters, list):
            raise QVFError(
                f"scf_history section {section_id!r}: missing or malformed 'iterations' list"
            )
        return SCFHistoryData(iterations=iters)

    def read_citations(self, section_id: str) -> CitationsData:
        """Read the BibTeX bytes from a citations section.

        The writer stores them as a binary member (utf-8) per design
        § 1.4 / qvf.py::_write_citations_section. We decode to str
        here so renderers don't need to know the on-disk encoding.
        """
        return CitationsData(bibtex=self._read_text_member(section_id, "references"))

    def read_run_record(self, section_id: str) -> RunRecordData:
        """Read a run.record section — verbatim input + full log of one
        program invocation (spec § 5.8).

        Text members are decoded UTF-8 with ``errors="replace"`` (the
        format requires UTF-8; a lenient decode keeps a slightly broken
        producer viewable). Attachment members are listed but not read —
        they can be arbitrary bytes and are only surfaced by name.
        """
        section = self.get_section(section_id)
        extras = getattr(section, "model_extra", None) or {}

        def _text_and_size(role: str) -> tuple[str | None, int]:
            member = section.members.get(role)
            if member is None:
                return None, 0
            size = self.stored_size(member) or 0
            return self._read_text_member(section_id, role, errors="replace"), size

        input_text, input_size = _text_and_size("input")
        log_text, log_size = _text_and_size("log")
        files: dict[str, Any] = {}
        if "files" in section.members:
            raw_files = self._read_json_member(section_id, "files")
            if isinstance(raw_files, dict):
                files = raw_files
        attachment_roles = sorted(
            role for role in section.members if role.startswith("attachment.")
        )
        exit_status = extras.get("exit_status")
        sequence = extras.get("sequence")
        return RunRecordData(
            program=str(extras.get("program", "unknown")),
            program_version=extras.get("program_version"),
            command=extras.get("command"),
            exit_status=int(exit_status) if exit_status is not None else None,
            started_utc=extras.get("started_utc"),
            finished_utc=extras.get("finished_utc"),
            sequence=int(sequence) if sequence is not None else None,
            input_text=input_text,
            input_size=input_size,
            log_text=log_text,
            log_size=log_size,
            files=files,
            attachment_roles=attachment_roles,
        )

    def read_run_record_attachment(
        self, section_id: str, role: str, *, max_bytes: int = 4 * 1024 * 1024
    ) -> str | None:
        """Decode a run.record ``attachment.*`` member as UTF-8 text.

        Attachments are arbitrary bytes; this returns None (rather than
        raising) when the member is absent, larger than ``max_bytes``
        (uncompressed, checked from the ZIP central directory before
        reading), or not valid UTF-8 — the renderer then falls back to
        listing the attachment by name.
        """
        section = self.get_section(section_id)
        member = section.members.get(role)
        if member is None:
            return None
        size = self.stored_size(member)
        if size is None:
            return None
        if size > max_bytes:
            return None
        raw = self._read_member(section_id, role)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

    #: Hard ceiling on a single attachment download. The bytes are
    #: base64-encoded into a state value and pushed over the websocket,
    #: so this bounds memory and socket pressure, not disk.
    ATTACHMENT_DOWNLOAD_MAX_BYTES = 64 * 1024 * 1024

    def run_record_attachments(self, section_id: str) -> list[dict[str, Any]]:
        """Describe a run.record's ``attachment.*`` members for listing.

        Metadata only — no payload is read. Each entry carries ``role``,
        a ``filename`` (sanitised to a bare basename; the files index is
        producer-supplied and must never steer a write), ``size`` in
        bytes, and ``too_large`` against
        :attr:`ATTACHMENT_DOWNLOAD_MAX_BYTES`.
        """
        section = self.get_section(section_id)
        files: dict[str, Any] = {}
        if "files" in section.members:
            raw = self._read_json_member(section_id, "files")
            if isinstance(raw, dict):
                files = raw
        out: list[dict[str, Any]] = []
        for role in sorted(section.members):
            if not role.startswith("attachment."):
                continue
            member = section.members[role]
            size = self.stored_size(member)
            if size is None:
                continue
            raw_entry = files.get(role)
            entry: dict[str, Any] = raw_entry if isinstance(raw_entry, dict) else {}
            out.append(
                {
                    "role": role,
                    "filename": _safe_basename(
                        entry.get("filename"), role.split(".", 1)[-1]
                    ),
                    "description": str(entry.get("description") or ""),
                    "size": size,
                    "too_large": size > self.ATTACHMENT_DOWNLOAD_MAX_BYTES,
                }
            )
        return out

    def read_run_record_attachment_bytes(
        self, section_id: str, role: str
    ) -> bytes | None:
        """Raw bytes of one ``attachment.*`` member, for download only.

        Deliberately separate from
        :meth:`read_run_record_attachment`, which decodes text for
        rendering: attachments are arbitrary bytes by spec and callers
        must not be tempted to put these in the DOM. Returns None when
        the member is absent or exceeds
        :attr:`ATTACHMENT_DOWNLOAD_MAX_BYTES` (checked from the ZIP
        central directory *before* reading, so a zip bomb cannot be
        expanded into memory first).
        """
        if not role.startswith("attachment."):
            return None
        section = self.get_section(section_id)
        member = section.members.get(role)
        if member is None:
            return None
        size = self.stored_size(member)
        if size is None:
            return None
        if size > self.ATTACHMENT_DOWNLOAD_MAX_BYTES:
            return None
        return self._read_member(section_id, role)

    def read_job_spec(self, section_id: str) -> JobSpecData:
        """Read a job.spec section — the declarative request the archive
        carries (spec § 5.9). Pair with :attr:`run_status` to tell a
        pending container from a settled one."""
        raw = self._read_json_member(section_id, "spec")
        if not isinstance(raw, dict):
            raise QVFError(
                f"job.spec section {section_id!r}: 'spec' member is not "
                f"a JSON object"
            )
        charge = raw.get("charge")
        multiplicity = raw.get("multiplicity")
        kpoints = raw.get("kpoints")
        return JobSpecData(
            job_type=str(raw.get("job_type", "unknown")),
            method=raw.get("method"),
            # null means the method takes none; "" is its deprecated
            # spelling, which consumers read as null (spec § 5.9, qvf 0.3).
            basis=raw.get("basis") or None,
            functional=raw.get("functional") or None,
            charge=int(charge) if charge is not None else None,
            multiplicity=(
                int(multiplicity) if multiplicity is not None else None
            ),
            kpoints=list(kpoints) if isinstance(kpoints, list) else None,
            tasks=[str(t) for t in raw.get("tasks", [])],
            options=(
                dict(raw["options"])
                if isinstance(raw.get("options"), dict)
                else {}
            ),
            raw=raw,
        )

    def read_reaction_scheme(self, section_id: str) -> ReactionSchemeData:
        """Read a ``reaction.scheme`` section (QVF spec § 5.14).

        The payload relates the archive's ``structure`` sections as the
        reactants, reagents and products of each step. A participant that
        names no ``structure`` section, a repeated step id or a step without
        reactants or products raises :class:`QVFError`, recorded against the
        section. Vendor ``x_`` keys are ignored.
        """
        raw = self._read_json_member(section_id, "scheme")

        def fail(detail: str) -> QVFError:
            return self._payload_failure(section_id, "scheme", QVFError(detail))

        def participants(step: dict[str, Any], role: str, where: str) -> list[ReactionParticipant]:
            entries = step.get(role, [])
            if not isinstance(entries, list):
                raise fail(f"{where}.{role} is not an array")
            out: list[ReactionParticipant] = []
            for index, entry in enumerate(entries):
                ref = entry.get("structure_ref") if isinstance(entry, dict) else None
                if not isinstance(ref, str) or not self._is_structure(ref):
                    raise fail(f"{where}.{role}[{index}] names no structure section: {ref!r}")
                coefficient = entry.get("coefficient", 1.0)
                if (
                    isinstance(coefficient, bool)
                    or not isinstance(coefficient, int | float)
                    or not coefficient > 0
                ):
                    raise fail(f"{where}.{role}[{index}].coefficient must be a positive number")
                out.append(ReactionParticipant(ref, float(coefficient)))
            return out

        steps_raw = raw.get("steps") if isinstance(raw, dict) else None
        if not isinstance(steps_raw, list) or not steps_raw:
            raise fail("'steps' must be a non-empty array")
        steps: list[ReactionStep] = []
        seen: set[str] = set()
        for index, step in enumerate(steps_raw):
            where = f"steps[{index}]"
            if not isinstance(step, dict):
                raise fail(f"{where} is not an object")
            step_id = step.get("id")
            if not isinstance(step_id, str) or not step_id:
                raise fail(f"{where}.id must be a non-empty string")
            if step_id in seen:
                raise fail(f"step id {step_id!r} occurs twice")
            seen.add(step_id)
            reactants = participants(step, "reactants", where)
            products = participants(step, "products", where)
            if not reactants or not products:
                raise fail(f"{where} needs at least one reactant and one product")
            arrow = step.get("arrow", "forward")
            if arrow not in REACTION_ARROWS:
                raise fail(f"{where}.arrow {arrow!r} is not one of {', '.join(REACTION_ARROWS)}")
            conditions = step.get("conditions", [])
            if not isinstance(conditions, list) or not all(isinstance(c, str) for c in conditions):
                raise fail(f"{where}.conditions must be an array of strings")
            yield_percent = step.get("yield_percent")
            if yield_percent is not None and (
                isinstance(yield_percent, bool)
                or not isinstance(yield_percent, int | float)
                or not 0 <= yield_percent <= 100
            ):
                raise fail(f"{where}.yield_percent must be a number from 0 to 100")
            label, yield_text = step.get("label"), step.get("yield_text")
            steps.append(
                ReactionStep(
                    id=step_id,
                    reactants=reactants,
                    products=products,
                    reagents=participants(step, "reagents", where),
                    label=label if isinstance(label, str) else None,
                    arrow=str(arrow),
                    conditions=list(conditions),
                    yield_percent=None if yield_percent is None else float(yield_percent),
                    yield_text=yield_text if isinstance(yield_text, str) else None,
                )
            )
        return ReactionSchemeData(steps=steps)

    #: Largest preview member read for display: the spec recommends at most
    #: 1 MiB, and the bytes travel to the browser as a data URL.
    PREVIEW_MAX_BYTES = 4 * 1024 * 1024

    def read_preview(self, section_id: str, role: str = "thumbnail") -> PreviewImage | None:
        """One verified member of a ``preview`` section (QVF spec § 5.15).

        ``role`` is ``thumbnail`` or ``image``. Returns None when the member
        is absent, declares no ``media_type``, or exceeds
        :attr:`PREVIEW_MAX_BYTES` (checked from the ZIP directory before
        reading). The picture describes the archive and is not data: nothing
        reads chemistry from it, and it is never drawn in the 3D scene.
        """
        member = self.get_section(section_id).members.get(role)
        if member is None or not member.media_type:
            return None
        size = self.stored_size(member)
        if size is None or size > self.PREVIEW_MAX_BYTES:
            return None
        return PreviewImage(role, member.media_type, self._read_member(section_id, role))

    def read_bond_orders(self, section_id: str) -> BondOrdersData:
        """Read analysis pairs validated against original file geometry.

        Edit overlays do not change the atom identities of archived analyses.
        Standalone analysis sections still validate without an upper bound.
        """
        raw, pairs = self._read_pair_payload(
            section_id, "bond_orders", self._file_atom_count(section_id)
        )
        return BondOrdersData(method=str(raw.get("method", "unknown")), pairs=pairs)

    def read_topology_qtaim(self, section_id: str) -> QTAIMData:
        """Read a topology.qtaim section (critical points + bond paths)."""
        raw = self._read_json_member(section_id, "critical_points")
        if not isinstance(raw, dict):
            raise QVFError(
                f"topology.qtaim section {section_id!r}: "
                "'critical_points' member is not a JSON object"
            )
        raw_paths = raw.get("bond_paths")
        return QTAIMData(
            points=list(raw.get("points", [])),
            bond_paths=list(raw_paths) if raw_paths else None,
        )

    def read_dos(self, section_id: str) -> DOSData:
        """Read a ``dos.total`` or ``dos.projected`` section.

        A spin-projected ``[2, n_ch, n_pts]`` projection is summed over spin
        so a consumer sees one series per channel; a total DOS keeps its
        ``[2, n_pts]`` spin axis for the caller to split.
        """
        section = self.get_section(section_id)
        energies = self._read_binary_member(section_id, "energies")
        projected = section.kind == "dos.projected"
        extra = section.model_extra or {}
        if projected:
            values = self._read_binary_member(section_id, "projections")
            if values.ndim == 3:
                values = values.sum(axis=0)
            raw_channels = extra.get("channels")
            channels = list(raw_channels) if isinstance(raw_channels, list) else []
        else:
            values = self._read_binary_member(section_id, "dos")
            channels = []
        return DOSData(energies=energies, values=values, channels=channels, projected=projected)

    def read_bands_projections(self, section_id: str) -> tuple[np.ndarray | None, list[str]]:
        """Optional fat-band projections of a ``bands`` section.

        Returns ``(projections [n_kpoints, n_bands, n_channels], channel
        labels)``, or ``(None, [])`` when the section carries none.
        """
        section = self.get_section(section_id)
        if "projections" not in section.members:
            return None, []
        projections = self._read_binary_member(section_id, "projections")
        kpath = self._read_json_member(section_id, "kpath")
        raw = kpath.get("channels", []) if isinstance(kpath, dict) else []
        return projections, [str(c) for c in raw] if isinstance(raw, list) else []

    def read_fermi_surface(self, section_id: str) -> FermiSurfaceData:
        """Read a ``fermi_surface`` section: energy mesh plus its metadata."""
        raw_mesh = self._read_json_member(section_id, "mesh")
        try:
            mesh = _json_object(raw_mesh)
        except QVFError as error:
            raise self._payload_failure(section_id, "mesh", error) from None
        energies = self._read_binary_member(section_id, "energies")
        return FermiSurfaceData(energies=energies, mesh=mesh)

    def read_json_member(self, section_id: str, member_name: str) -> Any:
        """The parsed JSON of one member, verified against the manifest.

        For kinds without a typed reader (vendor sections, ``wannier``
        centres); the typed ``read_*`` methods are preferred where one
        exists.
        """
        return self._read_json_member(section_id, member_name)

    def read_dos_coop(self, section_id: str) -> DOSCOOPData:
        """Read a dos.coop or dos.cohp section."""
        energies = self._read_binary_member(section_id, "energies")
        projections = self._read_binary_member(section_id, "projections")
        integrated = self._read_binary_member(section_id, "integrated")
        raw_meta = self._read_json_member(section_id, "meta")
        try:
            meta = _json_object(raw_meta)
        except QVFError as error:
            # A non-object meta is malformed, not absent: the sibling readers
            # raise here too, rather than inventing an empty dict (#35-26).
            raise self._payload_failure(section_id, "meta", error) from None
        return DOSCOOPData(
            energies=energies,
            projections=projections,
            integrated=integrated,
            meta=meta,
        )

    def close(self) -> None:
        self._zf.close()
        if self._buffer is not None:
            self._buffer.close()
            self._buffer = None

    def __enter__(self) -> QVFReader:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _section_read(method: Any) -> Any:
    """Wrap a section reader so only QVFError leaves it (#39-35).

    A member can be valid JSON of an unexpected shape, and a per-kind reader
    that did not anticipate that shape raises whatever it tripped over
    (TypeError, KeyError, OverflowError, ...). The CLI, the API, the TUI and
    the app handle QVFError. Anything else is recorded as the section's
    ``invalid payload`` error and re-raised as QVFError, chained to the
    original. KeyboardInterrupt and SystemExit pass through.
    """

    @functools.wraps(method)
    def wrapper(self: QVFReader, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(self, *args, **kwargs)
        except QVFError:
            raise
        except Exception as error:
            section_id = args[0] if args else kwargs.get("section_id")
            if section_id is None:  # read_structure() reads the shown structure
                section_id = self.shown_structure_id
            failure = QVFError(
                f"section {section_id!r} has an invalid payload: {_describe_exception(error)}"
            )
            if isinstance(section_id, str) and section_id in self._section_by_id:
                self._record_section_error(section_id, failure)
            raise failure from error

    return wrapper


# Every public section reader: ``read_<kind>`` plus ``run_record_attachments``.
# ``read_member_bytes`` takes a zip path, not a section, and already maps
# every archive error to QVFError.
_SECTION_READERS: tuple[str, ...] = tuple(
    sorted(
        name
        for name in vars(QVFReader)
        if (name.startswith("read_") and name != "read_member_bytes")
        or name == "run_record_attachments"
    )
)
for _name in _SECTION_READERS:
    setattr(QVFReader, _name, _section_read(vars(QVFReader)[_name]))
del _name
