"""QVF reader — the core file-open lifecycle (§2.4 of the design doc).

Responsibilities:
- Open .qvf zip archive, extract + validate manifest.json
- Parse sections into pydantic models
- Verify sha256 of every binary member before use (Rule 4)
- Lazy extraction of volumetric .dat blobs
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, cast

import jsonschema
import numpy as np
from jsonschema.protocols import Validator
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from vibeview.bonds import Bond, structure_bonds
from vibeview.elements import SYMBOL_TO_Z

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
    """Cannot open or read the .qvf file."""


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
    vibe-qc core uses for its lattice matrix (``cpp/include/vibeqc/periodic.hpp``).

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


@dataclass
class StructureData:
    """A unit cell, or a molecule when ``pbc`` is all-False.

    For ``dim < 3`` the lattice columns ``dim..2`` are **non-physical**: vibe-qc
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
    bonds: list[tuple[int, int, float, tuple[int, int, int]]] | None
    dim: int = 3  # periodic-axis count; columns dim..2 are non-physical

    # Producer-supplied biomolecule metadata from the `structure` section
    # object (QVF spec § 5.1). None when the archive carries none of it,
    # which is the case for every plain chemistry structure. Where these
    # ARE supplied they take precedence over this class's own derivation:
    # a producer knows what CA geometry cannot reveal (an alpha from a pi
    # from a 3-10 helix, a beta bridge from a sheet).
    supplied_residues: list[dict[str, Any]] | None = None
    supplied_chains: list[str] | None = None
    supplied_secondary_structure: list[dict[str, Any]] | None = None

    @property
    def has_residues(self) -> bool:
        """True when this structure carries biomolecular identity.

        Either carrier counts: per-atom residue numbers, or a
        producer-supplied ``residues`` list on the section object. A
        producer may legitimately supply the second without the first.
        """
        if self.supplied_residues:
            return True
        return any(a.residue_seq is not None for a in self.atoms)

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
        supplied = self._supplied_chain_map()
        if supplied is not None:
            return supplied
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
        picked: list[tuple[str, int, int]] = []
        for chain, residues in self.chains().items():
            if chain_id is not None and chain != chain_id:
                continue
            for seq, indices in residues:
                for i in indices:
                    if self.atoms[i].atom_name == "CA":
                        picked.append((chain, seq, i))
                        break
        return picked

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
    # Both ``origin`` and ``voxel_vectors`` are in **bohr** per the QVF
    # v1 contract (design § 1.3a). Renderers that draw alongside atoms
    # (Å) must convert. The :class:`VolumeRenderer` does this at its
    # PyVista boundary; the wavefunction renderer evaluates internally
    # in bohr and converts when emitting the mesh.
    origin: np.ndarray  # [3], bohr
    voxel_vectors: np.ndarray  # [3,3], bohr
    shape: tuple[int, int, int]


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
    frequencies: np.ndarray  # [n_modes] cm⁻¹
    intensities: np.ndarray  # [n_modes] km/mol


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

    Periodic reaction paths (qvf_version >= 2) additionally carry the
    per-frame lattice + dimensionality so the renderer can draw the
    cell and wrap atoms across periodic boundaries:

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


def _frame_atoms_from_json(meta: dict[str, Any]) -> list[Atom]:
    raw = meta.get("atoms")
    if not isinstance(raw, list):
        raise QVFError("atoms must be a list")
    atoms = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise QVFError(f"atoms[{i}] must be a JSON object")
        symbol = entry.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise QVFError(f"atoms[{i}].symbol must be a nonempty string")
        number = _json_integer(entry.get("atomic_number"), f"atoms[{i}].atomic_number")
        if number < 0:
            raise QVFError(f"atoms[{i}].atomic_number must be nonnegative")
        # Frame metadata carries identities, not positions. Keep legacy display
        # labels (including unknown elements) as read_structure does.
        atoms.append(Atom(symbol=symbol, position=np.zeros(3), atomic_number=number))
    return atoms


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
_SCHEMA_PATH_V2 = Path(__file__).parent / "schema_v2.json"

# Load both schemas once at module level. v2 ships periodic reaction.path
# with per-frame lattice + dim; v1 is the original molecular contract.
with open(_SCHEMA_PATH) as f:
    _MANIFEST_SCHEMA: dict[str, Any] = json.load(f)
with open(_SCHEMA_PATH_V2) as f:
    _MANIFEST_SCHEMA_V2: dict[str, Any] = json.load(f)

_SCHEMAS_BY_VERSION: dict[int, dict[str, Any]] = {
    1: _MANIFEST_SCHEMA,
    2: _MANIFEST_SCHEMA_V2,
}

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


def _load_manifest_schema(qvf_version: int = 1) -> dict[str, Any]:
    """Return the JSON Schema for manifest.json (cached, per version)."""
    schema = _SCHEMAS_BY_VERSION.get(qvf_version)
    if schema is None:
        raise ManifestValidationError(
            f"unsupported qvf_version {qvf_version!r}; vibe-view "
            f"supports versions {sorted(_SCHEMAS_BY_VERSION)}"
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
    from vibeview.kinds import SUPPORTED_KINDS

    return SUPPORTED_KINDS | {"bonds"}


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

        if isinstance(source, (str, Path)):
            self._path = Path(source)
            if not self._path.exists():
                raise QVFOpenError(f"file not found: {self._path}")
            if self._path.suffix != ".qvf":
                raise QVFOpenError(f"expected .qvf extension, got: {self._path.suffix}")
            try:
                self._zf = zipfile.ZipFile(self._path, "r")
            except zipfile.BadZipFile as e:
                raise QVFOpenError(f"not a valid zip archive: {e}") from None
        elif isinstance(source, (bytes, bytearray, memoryview)):
            self._path = None
            self._buffer = io.BytesIO(bytes(source))
            try:
                self._zf = zipfile.ZipFile(self._buffer, "r")
            except zipfile.BadZipFile as e:
                raise QVFOpenError(f"not a valid zip archive: {e}") from None
        elif hasattr(source, "read"):
            # Generic seekable file-like
            self._path = None
            try:
                self._zf = zipfile.ZipFile(source, "r")
            except zipfile.BadZipFile as e:
                raise QVFOpenError(f"not a valid zip archive: {e}") from None
        else:
            raise QVFOpenError(
                f"QVFReader: unsupported source type {type(source).__name__}; "
                "expected path, bytes, or seekable file-like object"
            )

        # ── Step 1: extract + parse manifest.json ──
        try:
            raw = self._zf.read("manifest.json").decode("utf-8")
        except KeyError:
            self.close()
            raise QVFOpenError("manifest.json not found in archive") from None

        try:
            manifest_dict = json.loads(raw)
        except json.JSONDecodeError as e:
            self.close()
            raise QVFOpenError(f"manifest.json is not valid JSON: {e}") from None

        # ── Step 2: validate against JSON Schema ──
        # Pick the schema by the manifest's declared qvf_version so v2
        # periodic reaction.path archives validate against the v2
        # schema. An unknown version is reported as such instead of
        # mis-validating against the v1 schema.
        try:
            declared_version = int(manifest_dict.get("qvf_version", 1))
        except (TypeError, ValueError):
            declared_version = 1
        try:
            chosen_schema = _load_manifest_schema(declared_version)
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
        self._unknown_kind_ids: set[str] = set()
        raw_sections = manifest_dict.get("sections")
        if isinstance(raw_sections, list):
            for sec in raw_sections:
                if not isinstance(sec, dict):
                    continue
                kind = sec.get("kind")
                if sec.get("critical") is True and kind not in usable:
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
                    unknown.append(sec)
                    if isinstance(sec, dict) and isinstance(sec.get("id"), str):
                        self._unknown_kind_ids.add(sec["id"])
                else:
                    kept.append(sec)
            to_validate = manifest_dict if not unknown else {**manifest_dict, "sections": kept}
        else:
            to_validate = manifest_dict

        try:
            jsonschema.validate(instance=to_validate, schema=chosen_schema)
            # The model still checks the common shape of unknown sections;
            # retain their existing tolerance for future identifier conventions.
            self._manifest = Manifest.model_validate(manifest_dict)
        except (jsonschema.ValidationError, ValidationError) as e:
            self.close()
            raise ManifestValidationError(str(e)) from None

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
        self._validated_structures: set[tuple[str, str, int]] = set()

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
        self._edit_overlay: list[Atom] | None = None
        self._edit_lattice_overlay: np.ndarray | None = None
        # Viewer-computed wavefunctions, keyed by a synthetic section id.
        # Same contract as ``_edit_overlay``: it shadows the archive for the
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
        try:
            data = self._zf.read(member.path)
        except KeyError:
            raise QVFError(
                f"member file {member.path!r} declared in manifest but missing from zip"
            ) from None
        except zipfile.BadZipFile as e:
            raise QVFError(f"cannot read zip member {member.path!r}: {e}") from None
        actual = hashlib.sha256(data).hexdigest()
        if actual != member.sha256:
            raise SHA256MismatchError(
                f"sha256 mismatch for {member.path!r}: expected {member.sha256}, got {actual}"
            )
        return data

    def _read_member(self, section_id: str, member_name: str) -> bytes:
        """Read verified member bytes and record failures for every section kind."""
        section = self.get_section(section_id)
        try:
            member = section.members.get(member_name)
            if member is None:
                raise QVFError(f"member {member_name!r} not found in section {section_id!r}")
            return self._verify_and_read(member)
        except QVFError as error:
            self._record_section_error(section_id, error, "member read error")
            raise

    def _read_text_member(
        self, section_id: str, member_name: str, *, errors: str = "strict"
    ) -> str:
        raw = self._read_member(section_id, member_name)
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
        raw = self._read_text_member(section_id, member_name)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            member = self.get_section(section_id).members[member_name]
            failure = QVFError(
                f"member {member.path!r} in section {section_id!r} is not valid JSON: {error}"
            )
            self._record_section_error(section_id, failure)
            raise failure from None

    def _read_binary_member(self, section_id: str, member_name: str) -> np.ndarray:
        """Read + verify a binary member. Returns numpy array (cached)."""
        cache_key = (section_id, member_name)
        cached = self._binary_cache.get(cache_key)
        if cached is not None:
            return cached
        raw = self._read_member(section_id, member_name)
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

    # ── Eager data extraction ─────────────────────────────────────────

    def set_edit_overlay(
        self,
        positions: np.ndarray | Sequence[Sequence[float]],
        symbols: Sequence[str],
        *,
        lattice_vectors: np.ndarray | Sequence[Sequence[float]] | None = None,
    ) -> None:
        """Record viewer-side edited geometry (see ``_edit_overlay``).

        ``positions`` is an (n, 3) sequence in angstroms, ``symbols`` the
        matching element symbols. Subsequent :meth:`read_structure` calls
        return this geometry (with explicit bonds dropped — atom indices
        from the file no longer apply) instead of the archive's.

        ``lattice_vectors`` replaces the current edit-overlay lattice when
        supplied. Omitting it preserves the current lattice, which lets
        ordinary atom edits made after a supercell build keep that expanded
        cell. The lattice is reset together with the atoms by
        :meth:`clear_edit_overlay`.
        """

        lattice = None
        if lattice_vectors is not None:
            lattice = np.asarray(lattice_vectors, dtype=np.float64)
            if lattice.shape != (3, 3) or not np.all(np.isfinite(lattice)):
                raise ValueError("lattice_vectors must be a finite 3x3 matrix")

        atoms = [
            Atom(
                symbol=str(sym),
                position=np.asarray(pos, dtype=np.float64),
                atomic_number=SYMBOL_TO_Z.get(str(sym), 0),
            )
            for sym, pos in zip(symbols, positions, strict=True)
        ]
        self._edit_overlay = atoms
        self.geometry_revision += 1
        self.clear_wavefunction_overlays(
            [sid for sid in self.wavefunction_overlay_ids if sid.startswith("wf_relocalized_")]
        )
        if lattice is not None:
            self._edit_lattice_overlay = lattice.copy()

    def clear_edit_overlay(self) -> None:
        """Drop viewer-side edits; read_structure() returns the file again."""
        self._edit_overlay = None
        self._edit_lattice_overlay = None
        self.geometry_revision += 1
        self.clear_wavefunction_overlays(
            [sid for sid in self.wavefunction_overlay_ids if sid.startswith("wf_relocalized_")]
        )

    @property
    def has_edit_overlay(self) -> bool:
        return self._edit_overlay is not None

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
        """Validate file geometry before constructing atoms or resolving indices."""
        raw = self._read_json_member(section_id, "structure")
        member = self.get_section(section_id).members["structure"]
        validation_key = (section_id, member.sha256, self.manifest.qvf_version)
        if isinstance(raw, dict) and "pbc" not in raw:
            dim = raw.get("dimensionality")
            # QVF v0.1.0 §5.1 permits this pre-conformance accommodation.
            # Explicit pbc remains authoritative; archive bytes are unchanged.
            if not isinstance(dim, bool) and isinstance(dim, int | float) and dim in (0, 1, 2, 3):
                raw = {**raw, "pbc": [axis < dim for axis in range(3)]}
        if validation_key in self._validated_structures:
            return cast(dict[str, Any], raw)
        try:
            _STRUCTURE_VALIDATORS[self.manifest.qvf_version].validate(raw)
            assert isinstance(raw, dict)  # guaranteed by StructurePayload
            positions = np.asarray([atom["position"] for atom in raw["atoms"]], dtype=float)
            if not np.all(np.isfinite(positions)):
                raise QVFError("atom positions must be finite")
            lattice = raw.get("lattice_vectors")
            if lattice is not None:
                cell = np.asarray(lattice, dtype=float)
                if not np.all(np.isfinite(cell)):
                    raise QVFError("lattice_vectors must be finite")
                if any(raw.get("pbc", [])):
                    try:
                        np.linalg.inv(cell)
                    except np.linalg.LinAlgError:
                        raise QVFError("periodic lattice_vectors must be invertible") from None
        except (jsonschema.ValidationError, QVFError, OverflowError) as exc:
            detail = (
                f"{exc.json_path}: {exc.message}"
                if isinstance(exc, jsonschema.ValidationError) else str(exc)
            )
            error = QVFError(f"structure member in section {section_id!r}: {detail}")
            self._record_section_error(section_id, error)
            raise error from None
        self._validated_structures.add(validation_key)
        return raw

    def _file_atom_count(self) -> int | None:
        """Original primary geometry, never the edited or caller-supplied atoms."""
        section = self._section_by_id.get("structure")
        if section is None or section.kind != "structure":
            return None  # standalone analysis has no declared atom-count context
        return len(self._read_structure_payload("structure")["atoms"])

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

    def read_structure(self, section_id: str = "structure") -> StructureData:
        """Read + verify a structure section. Always eager.

        ``section_id`` defaults to the primary ``"structure"`` section.
        Wavefunctions may reference a different structure, which callers can
        request explicitly so the displayed atoms, cell, and AO centres stay
        registered.

        When an edit overlay is set (:meth:`set_edit_overlay`), it applies
        only to the primary structure.  Its returned atoms are the edited
        geometry and ``bonds`` is None (auto-bonding applies).
        PBC/dimensionality still come from the file; the lattice comes from
        the overlay when an edit replaced it.
        """
        raw = self._read_structure_payload(section_id)

        atoms = [
            Atom(
                symbol=a["symbol"],
                position=np.array(a["position"], dtype=np.float64),
                atomic_number=a["atomic_number"],
                atom_name=_opt_str(a.get("atom_name")),
                residue_name=_opt_str(a.get("residue_name")),
                residue_seq=_opt_int(a.get("residue_seq")),
                chain_id=_opt_str(a.get("chain_id")),
                b_factor=_opt_float(a.get("b_factor")),
            )
            for a in raw["atoms"]
        ]
        pbc, dim = _resolve_pbc(raw)
        lattice = raw.get("lattice_vectors")
        if lattice is not None:
            lattice = np.array(lattice, dtype=np.float64)

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
        elif section_id == "structure":
            bond_section = next(
                (s for s in self._manifest.sections if s.kind == "bonds"),
                None,
            )
            if bond_section is not None:
                try:
                    bonds = self.read_bonds(bond_section.id, n_atoms=len(atoms))
                except QVFError as e:
                    self._record_section_error(bond_section.id, e)
                    raise QVFError(
                        f"explicit bonds section {bond_section.id!r} could not be read: {e}"
                    ) from None

        if section_id == "structure" and self._edit_overlay is not None:
            return StructureData(
                atoms=list(self._edit_overlay),
                pbc=pbc,
                lattice_vectors=(
                    self._edit_lattice_overlay.copy()
                    if self._edit_lattice_overlay is not None
                    else lattice
                ),
                bonds=None,  # file bond indices are stale after an edit
                dim=dim,
                # Supplied residue atom_indices index the file's atom list,
                # which the overlay has replaced, so they are as stale as the
                # bond indices above.
            )
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
                if section.kind == "structure" else self._file_atom_count()
            )
        _, pairs = self._read_pair_payload(section_id, "bonds", n_atoms)
        return [(p["i"], p["j"], p.get("order", 1.0), (0, 0, 0)) for p in pairs]

    def infer_bonds(self, structure: StructureData) -> list[Bond]:
        """Infer bonds from covalent radii when no explicit bonds section.

        Inferred bonds carry order 1.0 (covalent-radius inference cannot
        recover multiplicity); explicit bonds keep the producer's order.

        When a ``bond_orders`` section is present in the same file, its
        per-pair order values are used to override the bond orders —
        Mayer/Wiberg bond orders are more accurate than the simple
        single/double/triple from the bonds section.
        """
        bonds = structure_bonds(structure)

        # Enrich with bond_orders data if available.
        try:
            _sections = self.manifest.sections if self.manifest else []
        except AttributeError:
            _sections = []
        bo_section = next(
            (s for s in _sections if s.kind == "bond_orders"),
            None,
        )
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
        raw = self._read_json_member(section_id, "spectrum")
        return SpectraData(
            frequencies=np.array(raw["frequencies"], dtype=np.float64),
            intensities=np.array(raw["intensities"], dtype=np.float64),
        )

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
        atoms = [
            Atom(
                symbol=a["symbol"],
                position=np.array(a["position"], dtype=np.float64),
                atomic_number=a["atomic_number"],
            )
            for a in meta["atoms"]
        ]
        return VibrationsData(
            atoms=atoms,
            frequencies=np.array(meta["frequencies"], dtype=np.float64),
            displacements=displacements,
        )

    def read_wavefunction_gto(self, section_id: str) -> WavefunctionGTOData:
        """Read a `wavefunction.gto` section (basis + MO coefficients)."""
        overlay = self._wavefunction_overlays.get(section_id)
        if overlay is not None:
            return overlay
        basis_raw = self._read_json_member(section_id, "basis")
        meta_raw = self._read_json_member(section_id, "mo_metadata")

        global_pure = bool(basis_raw.get("pure", True))
        shells = [
            BasisShell(
                center=int(sh["center"]),
                l=int(sh["l"]),
                exponents=np.asarray(sh["exponents"], dtype=np.float64),
                coefficients=np.asarray(sh["coefficients"], dtype=np.float64),
                pure=bool(sh.get("pure", global_pure)),
            )
            for sh in basis_raw.get("shells", [])
        ]
        n_ao = int(basis_raw.get("n_ao", 0))
        structure_ref = str(basis_raw.get("structure_ref", "structure"))

        spin = str(meta_raw.get("spin", "restricted"))
        orbital_kind = str(meta_raw.get("orbital_kind", "canonical"))
        occupation_semantics = _opt_str(meta_raw.get("occupation_semantics"))

        energies: np.ndarray | None = None
        occupations: np.ndarray | None = None
        sym_labels: list[str] | None = None
        sym_labels_beta: list[str] | None = None
        a_e = a_o = b_e = b_o = None
        coeffs = a_c = b_c = None

        if spin == "unrestricted":
            alpha = meta_raw.get("alpha", {}) or {}
            beta = meta_raw.get("beta", {}) or {}
            a_e = np.asarray(alpha.get("energies", []), dtype=np.float64)
            a_o = np.asarray(alpha.get("occupations", []), dtype=np.float64)
            b_e = np.asarray(beta.get("energies", []), dtype=np.float64)
            b_o = np.asarray(beta.get("occupations", []), dtype=np.float64)
            # Per-spin labels (audit L7): the α list must not stand in for β.
            sym_labels = alpha.get("symmetry_labels")
            sym_labels_beta = beta.get("symmetry_labels")
            if "mo_coefficients_alpha" in self.get_section(section_id).members:
                a_c = self._read_binary_member(section_id, "mo_coefficients_alpha")
            if "mo_coefficients_beta" in self.get_section(section_id).members:
                b_c = self._read_binary_member(section_id, "mo_coefficients_beta")
        else:
            energies = np.asarray(meta_raw.get("energies", []), dtype=np.float64)
            occupations = np.asarray(meta_raw.get("occupations", []), dtype=np.float64)
            sym_labels = meta_raw.get("symmetry_labels")
            if "mo_coefficients" in self.get_section(section_id).members:
                coeffs = self._read_binary_member(section_id, "mo_coefficients")

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
            return arr if arr.size else None

        return WavefunctionGTOData(
            atom_populations=_optional_array("atom_populations", np.float64),
            centroids_bohr=_optional_array("centroids_bohr", np.float64),
            n_centres=_optional_array("n_centres", np.int64),
            localization_method=(
                str(meta_raw["localization_method"])
                if meta_raw.get("localization_method") is not None
                else None
            ),
            occupation_semantics=occupation_semantics,
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

        # v2 periodic fields. The lattice binary member is optional —
        # absent for molecular paths (qvf_version=1).
        section = self.get_section(section_id)
        lattice = None
        if section.members.get("lattice") is not None:
            lattice = self._read_binary_member(section_id, "lattice")
        dim_field = meta.get("dim")
        dim_per_frame = meta.get("dim_per_frame")

        # Optional per-frame volumes (W1). The grid descriptor is tiny
        # (read eagerly); the 4D blob is decimation-bounded so we read
        # it here too. Absent on archives without volumes.
        frame_volumes = None
        volume_grid = None
        if section.members.get("frame_volumes") is not None:
            frame_volumes = self._read_binary_member(section_id, "frame_volumes")
            volume_grid = self._read_grid_payload(
                section_id, "volume_grid", "frame_volumes", frame_axis=True,
            )
        vol_frame_index = meta.get("volume_frame_index")

        return ReactionPathData(
            atoms=atoms,
            coords=coords,
            energies=energies,
            reaction_coordinate=list(rxn_coord) if rxn_coord is not None else None,
            waypoints=waypoints,
            lattice=lattice,
            dim=int(dim_field) if dim_field is not None else None,
            dim_per_frame=[int(d) for d in dim_per_frame] if dim_per_frame else None,
            reaction_coordinate_label=(
                str(meta["reaction_coordinate_label"])
                if meta.get("reaction_coordinate_label") is not None
                else None
            ),
            reaction_coordinate_unit=(
                str(meta["reaction_coordinate_unit"])
                if meta.get("reaction_coordinate_unit") is not None
                else None
            ),
            frame_volumes=frame_volumes,
            volume_grid=volume_grid,
            volume_frame_index=(
                [int(i) for i in vol_frame_index] if vol_frame_index is not None else None
            ),
            volume_label=(
                str(meta["volume_label"]) if meta.get("volume_label") is not None else None
            ),
            volume_isovalue=(
                float(meta["volume_isovalue"]) if meta.get("volume_isovalue") is not None else None
            ),
        )

    def read_reaction_waypoints(self, section_id: str) -> ReactionWaypointsData:
        """Read a `reaction.waypoints` annotation that points at a trajectory."""
        section = self.get_section(section_id)
        traj_ref = section.trajectory_ref
        if traj_ref is None:
            extras = getattr(section, "model_extra", None) or {}
            traj_ref = extras.get("trajectory_ref")
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
        if section.members.get("geometries") is not None:
            geometries = self._read_binary_member(section_id, "geometries")
            atoms = [
                Atom(
                    symbol=a["symbol"],
                    position=np.zeros(3),
                    atomic_number=a["atomic_number"],
                )
                for a in meta.get("atoms", [])
            ]

        def _opt(key: str) -> str | None:
            v = meta.get(key)
            return str(v) if v is not None else None

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
            try:
                size = int(self._zf.getinfo(member.path).file_size)
            except KeyError:
                size = 0
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
        try:
            size = int(self._zf.getinfo(member.path).file_size)
        except KeyError:
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
            try:
                size = int(self._zf.getinfo(member.path).file_size)
            except KeyError:
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
        try:
            size = int(self._zf.getinfo(member.path).file_size)
        except KeyError:
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
            basis=raw.get("basis"),
            functional=raw.get("functional"),
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

    def read_bond_orders(self, section_id: str) -> BondOrdersData:
        """Read analysis pairs validated against original file geometry.

        Edit overlays do not change the atom identities of archived analyses.
        Standalone analysis sections still validate without an upper bound.
        """
        raw, pairs = self._read_pair_payload(section_id, "bond_orders", self._file_atom_count())
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

    def read_dos_coop(self, section_id: str) -> DOSCOOPData:
        """Read a dos.coop or dos.cohp section."""
        energies = self._read_binary_member(section_id, "energies")
        projections = self._read_binary_member(section_id, "projections")
        integrated = self._read_binary_member(section_id, "integrated")
        meta = self._read_json_member(section_id, "meta")
        return DOSCOOPData(
            energies=energies,
            projections=projections,
            integrated=integrated,
            meta=meta if isinstance(meta, dict) else {},
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
