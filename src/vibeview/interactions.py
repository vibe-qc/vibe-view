"""Geometric interaction candidates, not electronic bond assignments.

H-bonds and salt/parallel-pi criteria: GetContacts published definitions,
https://getcontacts.github.io/interactions.html (accessed 2026-10-22).
Halogen: Auffinger et al., PNAS 101,16789 (2004), doi:10.1073/pnas.0403723101:
C-X...O separation below summed vdW radii and C-X...O angle >=120 degrees.
No external interaction-detection source code is incorporated.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from vibeview.bonds import structure_bonds
from vibeview.elements import vdw_radius
from vibeview.molecular_selection import neighbour_pairs, positions, select
from vibeview.qvf import StructureData


@dataclass(frozen=True)
class Interaction:
    kind: str
    atoms: tuple[int, ...]
    distance: float
    angle: float | None = None
    overlap: float | None = None
    ring_split: int | None = None


def angle(a: np.ndarray, b: np.ndarray) -> float:
    norm = float(np.linalg.norm(a) * np.linalg.norm(b))
    if norm <= 1e-12:
        raise ValueError("Interaction angle has coincident points")
    return float(np.degrees(np.arccos(np.clip(np.dot(a, b) / norm, -1.0, 1.0))))


def find_interactions(
    structure: StructureData,
    *,
    selection: str = "all",
    partner: str | None = None,
    kinds: tuple[str, ...] = ("hbond",),
    hbond_distance: float = 3.5,
    hbond_angle: float = 110.0,
    contact_gap: float = 0.5,
    clash_overlap: float = 0.4,
    positive: tuple[int, ...] = (),
    negative: tuple[int, ...] = (),
    rings: tuple[tuple[int, ...], ...] = (),
) -> list[Interaction]:
    """Input-cell only; explicit H required. Optional ligand charges/rings are explicit.

    Selection alone restricts both endpoints. With partner, report cross-selection
    pairs only. Covalent and 1-3 neighbours are excluded from contacts/clashes.
    Residue charge/ring annotations use standard ASP/GLU/ARG/LYS/PHE/TYR/TRP names;
    protonation states must be reviewed by the user. No charge is inferred from
    fractional population analyses.
    """
    allowed = {"hbond", "contact", "clash", "salt", "halogen", "pi"}
    if set(kinds) - allowed:
        raise ValueError("Unknown interaction kind")
    if (
        not np.isfinite([hbond_distance, hbond_angle, contact_gap, clash_overlap]).all()
        or hbond_distance <= 0
        or not 0 <= hbond_angle <= 180
        or clash_overlap < 0
    ):
        raise ValueError("Invalid interaction distance, angle or overlap criterion")
    xyz = positions(structure)
    left = select(structure, selection)
    right = left if partner is None else select(structure, partner)

    def included(a: int, b: int) -> bool:
        return (a in left and b in right) or (b in left and a in right)

    neighbours: list[set[int]] = [set() for _ in xyz]
    for a, b, _, image in structure_bonds(structure):
        if not any(image):
            neighbours[a].add(b)
            neighbours[b].add(a)
    z = [a.atomic_number for a in structure.atoms]
    radii = [vdw_radius(i) for i in z]
    pos, neg = set(positive), set(negative)
    for i, atom in enumerate(structure.atoms):
        if (atom.residue_name == "LYS" and atom.atom_name == "NZ") or (
            atom.residue_name == "ARG" and atom.atom_name in {"NH1", "NH2"}
        ):
            pos.add(i)
        if (atom.residue_name == "ASP" and atom.atom_name in {"OD1", "OD2"}) or (
            atom.residue_name == "GLU" and atom.atom_name in {"OE1", "OE2"}
        ):
            neg.add(i)
    if any(i < 0 or i >= len(xyz) for i in pos | neg):
        raise ValueError("Charged atom index is out of range")
    if pos & neg:
        raise ValueError("An atom cannot have both charge signs")
    found: list[Interaction] = []
    cutoff = max(hbond_distance, 4.0, 2 * max(radii, default=0.0) + max(contact_gap, 0.0))
    for i, j in neighbour_pairs(xyz, left | right, cutoff):
        if not included(i, j):
            continue
        distance = float(np.linalg.norm(xyz[i] - xyz[j]))
        if distance < 1e-10:
            if "clash" in kinds:
                found.append(Interaction("clash", (i, j), distance, overlap=radii[i] + radii[j]))
            continue
        bonded = j in neighbours[i] or bool(neighbours[i] & neighbours[j])
        overlap = radii[i] + radii[j] - distance
        if not bonded and z[i] != 1 and z[j] != 1:
            if "contact" in kinds and overlap >= -contact_gap:
                found.append(Interaction("contact", (i, j), distance, overlap=overlap))
            if "clash" in kinds and overlap >= clash_overlap:
                found.append(Interaction("clash", (i, j), distance, overlap=overlap))
        if (
            "salt" in kinds
            and distance < 4
            and ((i in pos and j in neg) or (j in pos and i in neg))
        ):
            found.append(Interaction("salt", (i, j), distance))
        for donor, acceptor in ((i, j), (j, i)):
            if (
                "hbond" in kinds
                and z[donor] in {7, 8}
                and z[acceptor] in {7, 8}
                and distance < hbond_distance
                and not bonded
            ):
                for h in sorted(neighbours[donor]):
                    if z[h] == 1:
                        theta = angle(xyz[donor] - xyz[h], xyz[acceptor] - xyz[h])
                        if theta >= hbond_angle:
                            found.append(
                                Interaction("hbond", (donor, h, acceptor), distance, theta)
                            )
            if (
                "halogen" in kinds
                and z[donor] in {17, 35, 53}
                and z[acceptor] == 8
                and not bonded
                and distance < radii[donor] + radii[acceptor]
            ):
                for c in sorted(neighbours[donor]):
                    if z[c] == 6:
                        theta = angle(xyz[c] - xyz[donor], xyz[acceptor] - xyz[donor])
                        if theta >= 120:
                            found.append(
                                Interaction("halogen", (c, donor, acceptor), distance, theta)
                            )
    if "pi" in kinds:
        # Explicit ligand aromatic rings avoid silently mistaking any planar cycle
        # for an aromatic system. Standard residue annotations supply protein rings.
        groups: dict[tuple[str | None, int | None], list[int]] = {}
        names = {
            "PHE": {"CG", "CD1", "CD2", "CE1", "CE2", "CZ"},
            "TYR": {"CG", "CD1", "CD2", "CE1", "CE2", "CZ"},
            "TRP": {"CD2", "CE2", "CE3", "CZ2", "CZ3", "CH2"},
        }
        for i, atom in enumerate(structure.atoms):
            if atom.atom_name in names.get(atom.residue_name or "", set()):
                groups.setdefault((atom.chain_id, atom.residue_seq), []).append(i)
        ring_list = list(rings) + [tuple(v) for v in groups.values() if len(v) == 6]
        frames = []
        for ring in ring_list:
            if len(set(ring)) < 3 or any(i < 0 or i >= len(xyz) for i in ring):
                raise ValueError("Aromatic ring requires at least three distinct valid atoms")
            points = xyz[list(ring)]
            center = points.mean(axis=0)
            _, singular, axes = np.linalg.svd(points - center)
            if singular[1] < 1e-6 or singular[-1] > 0.15:
                raise ValueError("Aromatic ring coordinates must be planar and non-collinear")
            frames.append((ring, center, axes[-1]))
        for n, (r1, c1, normal1) in enumerate(frames):
            for r2, c2, normal2 in frames[n + 1 :]:
                if set(r1) & set(r2) or not (
                    (set(r1) <= left and set(r2) <= right) or (set(r2) <= left and set(r1) <= right)
                ):
                    continue
                delta = c2 - c1
                distance = float(np.linalg.norm(delta))
                if 0 < distance < 7:
                    theta = min(angle(normal1, normal2), angle(normal1, -normal2))
                    tilt1 = min(angle(normal1, delta), angle(normal1, -delta))
                    tilt2 = min(angle(normal2, delta), angle(normal2, -delta))
                    if theta < 30 and tilt1 < 45 and tilt2 < 45:
                        found.append(
                            Interaction("pi", (*r1, *r2), distance, theta, ring_split=len(r1))
                        )
    return found
