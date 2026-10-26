"""Template-based B-family DNA / A-family RNA single-strand construction.

CC0 wwPDB local conformations (1BNA B-DNA and 1EHZ RNA stem) are joined
through phosphodiester internal coordinates and constrained MMFF94 cleanup;
no equilibrium or duplex prediction. Torsion sources: doi:10.1093/nar/gkn260
Table 3 (BI), and https://pmc.ncbi.nlm.nih.gov/articles/PMC390331/
Table 2 (A-RNA).
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

import numpy as np

from vibeview.coordination_builder import align_vectors
from vibeview.geometry_edit import parameter_value
from vibeview.molecular_builder import rdkit


def place(
    a: np.ndarray, b: np.ndarray, c: np.ndarray, length: float, angle: float, torsion: float
) -> np.ndarray:
    """Append d using |c-d|, angle(b,c,d), signed dihedral(a,b,c,d)."""
    axis = (b - c) / np.linalg.norm(b - c)
    side = a - b - np.dot(a - b, axis) * axis
    side /= np.linalg.norm(side)
    normal = np.cross(axis, side)
    theta, phi = np.radians([angle, -torsion])
    return np.asarray(
        c
        + length
        * (np.cos(theta) * axis + np.sin(theta) * (np.cos(phi) * side + np.sin(phi) * normal))
    )


def nucleic_acid(sequence: str, kind: str = "DNA", *, strands: str = "single") -> Any:
    if strands == "duplex":
        from vibeview.nucleic_duplex import duplex

        return duplex(sequence, kind)
    if strands != "single":
        raise ValueError("Choose single or duplex strands")
    sequence = "".join(sequence.split()).upper()
    allowed = {"DNA": "ACGT", "RNA": "ACGU"}
    if (
        kind not in allowed
        or not sequence
        or len(sequence) > 100
        or set(sequence) - set(allowed[kind])
    ):
        raise ValueError("Use 1–100 A/C/G/T DNA bases or A/C/G/U RNA bases")
    data = json.loads(files("vibeview").joinpath("nucleotide_templates.json").read_text())
    Chem, _ = rdkit()
    mol = Chem.MolFromFASTA(sequence, flavor=6 if kind == "DNA" else 2)
    if mol is None:
        raise ValueError("Nucleic-acid topology construction failed")
    coords: list[dict[str, np.ndarray]] = []
    for base in sequence:
        template = {k: np.asarray(v, dtype=float) for k, v in data[kind][base].items()}
        if coords:
            previous = coords[-1]
            # Svozil et al. 2008 Table 3 BI; Schneider et al. 2004 Table 2 A-RNA.
            # The local sugar pucker and beta/gamma remain observed template values.
            epsilon, zeta = (-178.3, -96.8) if kind == "DNA" else (-150.0, -73.0)
            p = place(previous["C4'"], previous["C3'"], previous["O3'"], 1.607, 120.0, epsilon)
            source = np.array([template["P"], template["O5'"], template["C5'"]])
            po = float(np.linalg.norm(source[1] - source[0]))
            oc = float(np.linalg.norm(source[2] - source[1]))
            poc = parameter_value(source, [0, 1, 2])
            o5 = place(previous["C3'"], previous["O3'"], p, po, 104.0, zeta)
            c5 = place(previous["O3'"], p, o5, oc, poc, -61.0 if kind == "DNA" else -65.0)
            target = np.array([p, o5, c5])
            rotation = align_vectors(source - source.mean(axis=0), target - target.mean(axis=0))
            template = {
                k: (v - source.mean(axis=0)) @ rotation + target.mean(axis=0)
                for k, v in template.items()
            }
            # Complete the tetrahedral phosphate around both bridging oxygens.
            u = previous["O3'"] - p
            v = o5 - p
            u /= np.linalg.norm(u)
            v /= np.linalg.norm(v)
            bisector = (u + v) / np.linalg.norm(u + v)
            normal = np.cross(u, v)
            normal /= np.linalg.norm(normal)
            for name, sign in (("OP1", 1), ("OP2", -1)):
                template[name] = p + 1.48 * (
                    -bisector / np.sqrt(3) + sign * normal * np.sqrt(2 / 3)
                )
        coords.append(template)
    conf = Chem.Conformer(mol.GetNumAtoms())
    for atom in mol.GetAtoms():
        info = atom.GetPDBResidueInfo()
        name = info.GetName().strip()
        position = coords[info.GetResidueNumber() - 1].get(name)
        if position is None:
            raise ValueError(f"Template lacks {name}; unsupported nucleotide topology")
        conf.SetAtomPosition(atom.GetIdx(), position.tolist())
    mol.AddConformer(conf)
    mol = Chem.AddHs(mol, addCoords=True, addResidueInfo=True)
    from vibeview.builder_relaxation import constrain_torsions

    residues: dict[int, dict[str, int]] = {}
    for atom in mol.GetAtoms():
        info = atom.GetPDBResidueInfo()
        residues.setdefault(info.GetResidueNumber(), {})[info.GetName().strip()] = atom.GetIdx()
    from rdkit.Chem import rdMolTransforms

    targets = []
    for row in residues.values():
        ids = (row["C5'"], row["C4'"], row["C3'"], row["O3'"])
        targets.append((ids, rdMolTransforms.GetDihedralDeg(mol.GetConformer(), *ids)))
    for i in range(1, len(sequence)):
        a, b = residues[i], residues[i + 1]
        epsilon, zeta, alpha = (-178.3, -96.8, -61.0) if kind == "DNA" else (-150.0, -73.0, -65.0)
        targets.extend(
            [
                ((a["C4'"], a["C3'"], a["O3'"], b["P"]), epsilon),
                ((a["C3'"], a["O3'"], b["P"], b["O5'"]), zeta),
                ((a["O3'"], b["P"], b["O5'"], b["C5'"]), alpha),
            ]
        )
    mol = constrain_torsions(mol, targets)
    mol.SetProp("builder_sequence", sequence)
    mol.SetProp("builder_nucleic_form", "B-family DNA" if kind == "DNA" else "A-family RNA")
    mol.SetProp("builder_reference", "1BNA" if kind == "DNA" else "1EHZ")
    return mol
