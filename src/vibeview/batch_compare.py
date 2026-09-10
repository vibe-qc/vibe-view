"""Batch comparison tool for vibe-view.

Compares multiple QVF files side by side, computing RMSD,
energy differences, and section availability across a dataset.
Useful for screening calculations or benchmarking.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np


def _energy_eh(reader) -> float | None:
    """SCF energy (Eh) from a reader's provenance, mirroring cli._energy_eh."""
    prov = getattr(reader.manifest, "provenance", None) or {}
    if hasattr(prov, "model_dump"):
        prov = prov.model_dump()
    if not isinstance(prov, dict):
        return None
    e = prov.get("scf_energy", {})
    if isinstance(e, dict):
        e = e.get("value")
    return float(e) if e is not None else None


def compare_batch(
    qvf_paths: list[str],
    reference_idx: int = 0,
    align: bool = True,
) -> dict[str, Any]:
    """Compare a batch of QVF files.

    Parameters
    ----------
    qvf_paths : list of str
        Paths to .qvf files.
    reference_idx : int
        Index of the reference file (for RMSD alignment).
    align : bool
        Whether to RMSD-align structures before comparison.

    Returns
    -------
    dict
        Comparison results with keys:
        - files: list of filenames
        - energies: list of {path, scf_energy_eh}
        - rmsd: list of {path, rmsd_angstrom} (relative to reference)
        - sections: dict of section_kind -> list of bool (present in each file)
        - summary: human-readable summary string
    """
    from vibeview.qvf import QVFReader

    results: dict[str, Any] = {
        "files": [],
        "energies": [],
        "rmsd": [],
        "sections": {},
        "summary": "",
    }

    readers = []
    for p in qvf_paths:
        try:
            reader = QVFReader(p)
            readers.append((p, reader))
        except Exception:
            continue

    if not readers:
        results["summary"] = "No valid QVF files found."
        return results

    # Validate the reference index against the readers that actually loaded,
    # so an out-of-range -r/reference produces a clean error rather than a raw
    # IndexError deep inside the RMSD/summary code.
    if not (0 <= reference_idx < len(readers)):
        raise ValueError(
            f"reference index {reference_idx} out of range "
            f"(0..{len(readers) - 1} for {len(readers)} valid file(s))"
        )

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
        for path, reader in readers:
            has_it = any(s.kind == kind for s in reader.sections)
            results["sections"][kind].append(has_it)

    # RMSD (if structures available)
    if align:
        try:
            # doc says "RMSD-align": use the superposing Kabsch fit, not the
            # non-superposing index-wise rmsd (which would report frame
            # differences, not the residual displacement).
            from vibeview.align import kabsch_fit

            _ref_path, ref_reader = readers[reference_idx]
            ref_sdata = ref_reader.read_structure()
            ref_pos = np.array([a.position for a in ref_sdata.atoms])

            for i, (path, reader) in enumerate(readers):
                if i == reference_idx:
                    results["rmsd"].append({"path": str(path), "rmsd_angstrom": 0.0})
                    continue
                try:
                    sdata = reader.read_structure()
                    pos = np.array([a.position for a in sdata.atoms])
                    if len(pos) == len(ref_pos):
                        _aligned, r = kabsch_fit(pos, ref_pos)
                        results["rmsd"].append({"path": str(path), "rmsd_angstrom": float(r)})
                    else:
                        results["rmsd"].append(
                            {
                                "path": str(path),
                                "rmsd_angstrom": None,
                                "note": "atom count mismatch",
                            }
                        )
                except Exception:
                    results["rmsd"].append({"path": str(path), "rmsd_angstrom": None})
        except ImportError:
            pass

    # Build summary
    lines = [f"Batch comparison: {len(readers)} files"]
    ref_energy = results["energies"][reference_idx].get("scf_energy_eh")
    if ref_energy is not None:
        lines.append(f"Reference energy: {ref_energy:.8f} Eh")
        for i, e in enumerate(results["energies"]):
            if i == reference_idx:
                continue
            if e.get("scf_energy_eh") is not None:
                delta = (e["scf_energy_eh"] - ref_energy) * 627.509  # kcal/mol
                lines.append(f"  {Path(e['path']).name}: ΔE = {delta:.2f} kcal/mol")

    if results["rmsd"]:
        for r in results["rmsd"]:
            if r["rmsd_angstrom"] is not None:
                lines.append(f"  RMSD to ref: {r['rmsd_angstrom']:.4f} Å")

    common_sections = [k for k, v in results["sections"].items() if all(v)]
    if common_sections:
        lines.append(
            f"Common sections ({len(common_sections)}): {', '.join(sorted(common_sections)[:10])}"
        )

    results["summary"] = "\n".join(lines)

    # Close readers
    for _, reader in readers:
        try:
            reader.close()
        except Exception:
            pass

    return results


def batch_compare_to_table(results: dict) -> str:
    """Format batch comparison results as a markdown table."""
    files = results.get("files", [])
    energies = results.get("energies", [])
    rmsd = results.get("rmsd", [])

    lines = ["| File | Energy (Eh) | RMSD (Å) |"]
    lines.append("|------|-------------|----------|")

    for i, f in enumerate(files):
        name = Path(f).name
        e = energies[i].get("scf_energy_eh") if i < len(energies) else None
        e_str = f"{e:.8f}" if e is not None else "—"
        r = rmsd[i].get("rmsd_angstrom") if i < len(rmsd) else None
        r_str = f"{r:.4f}" if r is not None else "—"
        lines.append(f"| {name} | {e_str} | {r_str} |")

    return "\n".join(lines)
