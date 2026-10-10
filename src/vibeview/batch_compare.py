"""Batch comparison tool for vibe-view.

Compares multiple QVF files side by side, computing RMSD,
energy differences, and section availability across a dataset.
Useful for screening calculations or benchmarking.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from vibeview.units import HARTREE_TO_KCAL_MOL, format_length, length_from_angstrom, length_label

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader, StructureData


def _energy_eh(reader: QVFReader) -> float | None:
    """SCF energy (Eh) from a reader's provenance."""
    return reader.scf_energy_eh()


def _describe(exc: BaseException) -> str:
    """An exception as a one-line reason; the type name when it has no message."""
    text = str(exc)
    return text if text else type(exc).__name__


def _positions(sdata: StructureData) -> np.ndarray:
    """Atom positions as ``[N, 3]``; ``[0, 3]`` for a structure with no atoms."""
    return np.array([a.position for a in sdata.atoms], dtype=float).reshape(-1, 3)


def _rmsd_rows(readers: list[tuple[str, QVFReader]], ref_row: int) -> list[dict[str, Any]]:
    """One ``{path, rmsd_angstrom, note}`` per reader, against ``readers[ref_row]``.

    ``note`` says why ``rmsd_angstrom`` is None: the structure, or the
    reference structure, cannot be read; either side has no atoms; the atom
    counts differ; or the superposition itself failed (#67). It is None when
    the RMSD was taken. The fit is the superposing Kabsch fit, so a rigid
    rotation or translation does not count.
    """
    from vibeview.align import kabsch_fit

    _ref_path, ref_reader = readers[ref_row]
    ref_pos = np.zeros((0, 3))
    ref_error: str | None = None
    try:
        ref_pos = _positions(ref_reader.read_structure())
    except Exception as exc:  # noqa: BLE001 — reported on every row, the batch goes on
        ref_error = _describe(exc)

    rows: list[dict[str, Any]] = []
    for i, (path, reader) in enumerate(readers):
        row: dict[str, Any] = {"path": str(path), "rmsd_angstrom": None, "note": None}
        rows.append(row)
        if i == ref_row:
            if ref_error is not None:
                row["note"] = f"cannot read structure: {ref_error}"
            elif len(ref_pos) == 0:
                row["note"] = "no atoms"
            else:
                row["rmsd_angstrom"] = 0.0
            continue
        try:
            pos = _positions(reader.read_structure())
        except Exception as exc:  # noqa: BLE001 — reported on this row, the batch goes on
            row["note"] = f"cannot read structure: {_describe(exc)}"
            continue
        if ref_error is not None:
            row["note"] = "reference structure cannot be read"
        elif len(pos) == 0 or len(ref_pos) == 0:
            row["note"] = f"no atoms (file: {len(pos)}, reference: {len(ref_pos)})"
        elif len(pos) != len(ref_pos):
            row["note"] = f"atom counts differ (file: {len(pos)}, reference: {len(ref_pos)})"
        else:
            try:
                _aligned, r = kabsch_fit(pos, ref_pos)
            except Exception as exc:  # noqa: BLE001 — an SVD that does not converge
                row["note"] = f"superposition failed: {_describe(exc)}"
            else:
                row["rmsd_angstrom"] = float(r)
    return rows


def compare_batch(
    qvf_paths: list[str],
    reference_idx: int = 0,
    align: bool = True,
    *,
    length_unit: str = "angstrom",
) -> dict[str, Any]:
    """Compare a batch of QVF files.

    Parameters
    ----------
    qvf_paths : list of str
        Paths to .qvf files.
    reference_idx : int
        Index into ``qvf_paths`` of the reference file (for RMSD alignment).
        It counts the files as given, whether or not each one opens.
    align : bool
        Whether to RMSD-align structures before comparison.
    length_unit : str
        The unit of the RMSD in the human-readable summary, a
        ``vibeview.units.LENGTH_UNITS`` key (#58). The ``rmsd`` rows keep
        ``rmsd_angstrom`` in Å.

    Returns
    -------
    dict
        Comparison results with keys:
        - files: list of filenames that could be opened
        - skipped: list of {path, reason} for files that could not be opened
        - energies: list of {path, scf_energy_eh}
        - rmsd: list of {path, rmsd_angstrom, note} (relative to reference);
          ``note`` says why ``rmsd_angstrom`` is None, and is None otherwise
        - sections: dict of section_kind -> list of bool (present in each file)
        - summary: human-readable summary string

    Raises
    ------
    ValueError
        ``reference_idx`` is out of range, or the reference file cannot be
        opened. Any other file that cannot be opened is skipped with its
        reason (#67).
    """
    from vibeview.qvf import QVFReader

    results: dict[str, Any] = {
        "files": [],
        "skipped": [],
        "energies": [],
        "rmsd": [],
        "sections": {},
        "summary": "",
    }

    if not qvf_paths:
        results["summary"] = "No valid QVF files found."
        return results
    # The index counts the files as given, so ``-r 1`` names the second file
    # even when the first one does not open (#67). Validating it first keeps
    # an out-of-range index a clean error rather than a raw IndexError.
    if not (0 <= reference_idx < len(qvf_paths)):
        raise ValueError(
            f"reference index {reference_idx} out of range "
            f"(0..{len(qvf_paths) - 1} for {len(qvf_paths)} file(s))"
        )

    readers: list[tuple[str, QVFReader]] = []
    loaded: list[int] = []  # the input index of each reader
    # Every reader opened here belongs to this function: close each exactly
    # once on every exit -- an interrupt while a later file opens, the bad
    # reference ValueError, and any exception in the body (#39-18).
    try:
        for i, p in enumerate(qvf_paths):
            try:
                reader = QVFReader(p)
            except Exception as exc:  # noqa: BLE001 — every open failure is a skip with its reason
                if i == reference_idx:
                    raise ValueError(
                        f"reference file {p} cannot be read: {_describe(exc)}"
                    ) from exc
                results["skipped"].append({"path": str(p), "reason": _describe(exc)})
                continue
            loaded.append(i)
            readers.append((p, reader))
        ref_row = loaded.index(reference_idx)

        # Collect energies and sections
        all_section_kinds = set()
        for path, reader in readers:
            results["files"].append(str(path))
            # Energy lives in manifest.provenance["scf_energy"]["value"], not a
            # (nonexistent) manifest.scf_energy_eh attribute. Mirror cli._energy_eh.
            try:
                scf = _energy_eh(reader)
                results["energies"].append({"path": str(path), "scf_energy_eh": scf})
            except Exception:
                results["energies"].append({"path": str(path), "scf_energy_eh": None})

            for s in reader.sections:
                all_section_kinds.add(s.kind)

        # Section matrix
        for kind in sorted(all_section_kinds):
            results["sections"][kind] = []
            for _path, reader in readers:
                has_it = any(s.kind == kind for s in reader.sections)
                results["sections"][kind].append(has_it)

        # RMSD (if structures available)
        if align:
            results["rmsd"] = _rmsd_rows(readers, ref_row)

        # Build summary
        head = f"Batch comparison: {len(readers)} files"
        if results["skipped"]:
            head += f" ({len(results['skipped'])} skipped)"
        lines = [head, f"Reference: {Path(readers[ref_row][0]).name}"]
        ref_energy = results["energies"][ref_row].get("scf_energy_eh")
        if ref_energy is not None:
            lines.append(f"Reference energy: {ref_energy:.8f} Eh")
            for i, e in enumerate(results["energies"]):
                if i == ref_row:
                    continue
                if e.get("scf_energy_eh") is not None:
                    delta = (e["scf_energy_eh"] - ref_energy) * HARTREE_TO_KCAL_MOL  # kcal/mol
                    lines.append(f"  {Path(e['path']).name}: ΔE = {delta:.2f} kcal/mol")

        for i, r in enumerate(results["rmsd"]):
            name = Path(r["path"]).name
            if r["rmsd_angstrom"] is None:
                lines.append(f"  {name}: RMSD skipped: {r['note']}")
            elif i != ref_row:
                rmsd = format_length(r["rmsd_angstrom"], length_unit, digits=4)
                lines.append(f"  {name}: RMSD to ref = {rmsd}")

        for s in results["skipped"]:
            lines.append(f"  {Path(s['path']).name}: skipped: {s['reason']}")

        common_sections = [k for k, v in results["sections"].items() if all(v)]
        if common_sections:
            shown = ", ".join(sorted(common_sections)[:10])
            lines.append(f"Common sections ({len(common_sections)}): {shown}")

        results["summary"] = "\n".join(lines)
        return results
    finally:
        for _, reader in readers:
            with contextlib.suppress(Exception):
                reader.close()


def _cell(text: str) -> str:
    """``text`` as one markdown table cell: no line breaks, pipes escaped."""
    return " ".join(text.split()).replace("|", "\\|")


def batch_compare_to_table(results: dict[str, Any], *, length_unit: str = "angstrom") -> str:
    """Format batch comparison results as a markdown table.

    The ``Note`` column carries the reason for a missing RMSD, and a file
    that could not be opened is a ``skipped: <reason>`` row (#67). The RMSD
    column is in ``length_unit`` (#58).
    """
    files = results.get("files", [])
    energies = results.get("energies", [])
    rmsd = results.get("rmsd", [])
    skipped = results.get("skipped", [])

    lines = [f"| File | Energy (Eh) | RMSD ({length_label(length_unit)}) | Note |"]
    lines.append("|------|-------------|----------|------|")

    for i, f in enumerate(files):
        name = Path(f).name
        e = energies[i].get("scf_energy_eh") if i < len(energies) else None
        e_str = f"{e:.8f}" if e is not None else "—"
        row = rmsd[i] if i < len(rmsd) else {}
        r = row.get("rmsd_angstrom")
        r_str = f"{length_from_angstrom(r, length_unit):.4f}" if r is not None else "—"
        note = _cell(row.get("note") or "")
        lines.append(f"| {name} | {e_str} | {r_str} | {note} |")

    for s in skipped:
        lines.append(f"| {Path(s['path']).name} | — | — | skipped: {_cell(s['reason'])} |")

    return "\n".join(lines)
