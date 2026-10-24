"""Observed-sequence global alignment followed by matched-CA Kabsch fitting.

Needleman & Wunsch, J Mol Biol 48, 443 (1970),
doi:10.1016/0022-2836(70)90057-4. Linear-gap dynamic programming with explicit
scores; deterministic diagonal/up/left tie order. Kabsch (1976) is implemented
in vibeview.align. No homology significance or structural outlier rejection.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from vibeview.align import align
from vibeview.biomolecule import residues
from vibeview.qvf import QVFReader


def aligned_pairs(a: str, b: str) -> list[tuple[int, int]]:
    """Global alignment: match +2, mismatch -1, each gap -2; observed residues."""
    if (len(a) + 1) * (len(b) + 1) > 4_000_000:
        raise ValueError("Sequence alignment exceeds four million cells; select shorter chains")
    scores = np.zeros((len(a) + 1, len(b) + 1), dtype=np.int32)
    scores[:, 0] = -2 * np.arange(len(a) + 1)
    scores[0, :] = -2 * np.arange(len(b) + 1)
    for i, x in enumerate(a, 1):
        for j, y in enumerate(b, 1):
            scores[i, j] = max(
                scores[i - 1, j - 1] + (2 if x == y and x != "X" else -1),
                scores[i - 1, j] - 2,
                scores[i, j - 1] - 2,
            )
    pairs = []
    i, j = len(a), len(b)
    while i or j:
        if (
            i
            and j
            and scores[i, j]
            == scores[i - 1, j - 1] + (2 if a[i - 1] == b[j - 1] and a[i - 1] != "X" else -1)
        ):
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif i and scores[i, j] == scores[i - 1, j] - 2:
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


def superpose(
    mobile: QVFReader, reference: QVFReader, mobile_chain: str, reference_chain: str
) -> dict[str, Any]:
    mr = [r for r in residues(mobile) if r["chain"] == mobile_chain and r["kind"] == "polymer"]
    rr = [
        r for r in residues(reference) if r["chain"] == reference_chain and r["kind"] == "polymer"
    ]
    if not mr or not rr:
        raise ValueError("Select two chains with observed polymer residues")
    ms = "".join(r["letter"] for r in mr)
    rs = "".join(r["letter"] for r in rr)
    pairs = aligned_pairs(ms, rs)
    retained = [
        (i, j) for i, j in pairs if mr[i]["ca_index"] is not None and rr[j]["ca_index"] is not None
    ]
    if len(retained) < 3:
        raise ValueError("At least three aligned C-alpha pairs are required")
    mp = np.asarray([a.position for a in mobile.read_structure().atoms])
    rp = np.asarray([a.position for a in reference.read_structure().atoms])
    p = np.asarray([mp[mr[i]["ca_index"]] for i, j in retained])
    q = np.asarray([rp[rr[j]["ca_index"]] for i, j in retained])
    if not np.isfinite(p).all() or not np.isfinite(q).all():
        raise ValueError("CA coordinates must be finite")
    if min(np.linalg.matrix_rank(p - p.mean(0)), np.linalg.matrix_rank(q - q.mean(0))) < 2:
        raise ValueError("Aligned C-alpha points must be noncollinear")
    fit = align(p, q)
    return {
        "mobile_sequence": ms,
        "reference_sequence": rs,
        "rmsd_angstrom": fit.rmsd_after,
        "rotation": fit.rotation.tolist(),
        "translation": fit.translation.tolist(),
        "transformed": (mp @ fit.rotation.T + fit.translation).tolist(),
        "aligned_pairs": [
            {
                "mobile": mr[i]["label"],
                "reference": rr[j]["label"],
                "mobile_ca": mr[i]["ca_index"],
                "reference_ca": rr[j]["ca_index"],
                "identity": mr[i]["letter"] == rr[j]["letter"],
            }
            for i, j in retained
        ],
        "omitted_ca_pairs": len(pairs) - len(retained),
    }
