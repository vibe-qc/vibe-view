"""Antiparallel Watson–Crick duplexes from CC0 experimental helical steps.

The repeated rigid step is measured from 1BNA A5/B20 → A6/B19 (B-DNA)
and 1EHZ A3/A70 → A4/A69 (A-family RNA). These are template-derived
starting geometries, not ideal-helix coordinates or equilibrium predictions.
Rigid base placement follows the matrix rebuilding approach described by
Lu & Olson, NAR 31 (2003), doi:10.1093/nar/gkg680; no 3DNA code is used.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

import numpy as np

from vibeview.coordination_builder import align_vectors
from vibeview.molecular_builder import rdkit


def complement(sequence: str, kind: str) -> str:
    alphabet = "ACGT" if kind == "DNA" else "ACGU"
    if kind not in ("DNA", "RNA") or not sequence or set(sequence) - set(alphabet):
        raise ValueError("Duplex requires unambiguous A/C/G/T DNA or A/C/G/U RNA")
    return sequence.translate(str.maketrans(alphabet, "TGCA" if kind == "DNA" else "UGCA"))[::-1]


def _ring(base: str) -> tuple[str, str, str]:
    return ("N9", "C4", "C8") if base in "AG" else ("N1", "C2", "C6")


def _template_pair(base: str, kind: str, record: dict[str, Any]) -> list[dict[str, np.ndarray]]:
    templates = json.loads(files("vibeview").joinpath("nucleotide_templates.json").read_text())[
        kind
    ]
    pair_key = ("AT" if kind == "DNA" else "AU") if base in "ATU" else "GC"
    reference = [
        {k: np.array(v) for k, v in row.items()} for row in record["base_pairs"][pair_key]["pair"]
    ]
    # Idealize the paired bases into one common plane before repeating a
    # helical step: experimental propeller/buckle is sequence dependent and
    # cannot safely be repeated at arbitrary sequence boundaries.
    primary = [{k: np.array(v) for k, v in row.items()} for row in record["pair"]]
    points = np.array(
        [
            v
            for row in primary
            for k, v in row.items()
            if "'" not in k and k not in ("P", "OP1", "OP2", "OP3")
        ]
    )
    plane_origin = points.mean(0)
    _, _, axes = np.linalg.svd(points - plane_origin)
    normal = axes[-1]
    for row in reference:
        keys = [k for k in row if "'" not in k and k not in ("P", "OP1", "OP2", "OP3")]
        base_xyz = np.array([row[k] for k in keys])
        origin = base_xyz.mean(0)
        _, _, base_axes = np.linalg.svd(base_xyz - origin)
        base_normal = base_axes[-1]
        if np.dot(base_normal, normal) < 0:
            base_normal = -base_normal
        turn = align_vectors(base_normal[None], normal[None])
        flat_origin = origin - np.dot(origin - plane_origin, normal) * normal
        for k in keys:
            row[k] = (row[k] - origin) @ turn + flat_origin
    wanted = [base, complement(base, kind)]
    result = []
    for code in wanted:
        source = {k: np.array(v) for k, v in templates[code].items()}
        target = reference[0 if code in "AG" else 1]
        names = _ring(code)
        a, b = np.array([source[k] for k in names]), np.array([target[k] for k in names])
        rotation = align_vectors(a - a.mean(0), b - b.mean(0))
        placed = {k: (v - a.mean(0)) @ rotation + b.mean(0) for k, v in source.items()}
        # Keep actual paired base atoms, not a mutation of another base's ring.
        placed.update(
            {
                k: v.copy()
                for k, v in target.items()
                if "'" not in k and k not in ("P", "OP1", "OP2", "OP3")
            }
        )
        result.append(placed)
    if base not in "AG":
        # Watson–Crick dyad: interchange strands with a proper half turn
        # about the in-plane groove axis, also reversing their 5′→3′ directions.
        left, right = primary
        centre = (left["C1'"] + right["C1'"]) / 2
        centre -= np.dot(centre - plane_origin, normal) * normal
        across = right["C1'"] - left["C1'"]
        across -= np.dot(across, normal) * normal
        across /= np.linalg.norm(across)
        groove = np.cross(normal, across)
        dyad = 2 * np.outer(groove, groove) - np.eye(3)
        result = [{k: (v - centre) @ dyad + centre for k, v in row.items()} for row in result]
    return result


def duplex(sequence: str, kind: str = "DNA") -> Any:
    sequence = "".join(sequence.split()).upper()
    other = complement(sequence, kind)
    if len(sequence) > 100:
        raise ValueError("Duplex builder supports at most 100 base pairs")
    record = json.loads(files("vibeview").joinpath("duplex_templates.json").read_text())[kind]
    rotation, translation = np.array(record["rotation"]), np.array(record["translation"])
    coordinates: list[list[dict[str, np.ndarray]]] = [[], []]
    transform, offset = np.eye(3), np.zeros(3)
    for base in sequence:
        pair = _template_pair(base, kind, record)
        for strand in range(2):
            coordinates[strand].append({k: v @ transform + offset for k, v in pair[strand].items()})
        transform, offset = transform @ rotation, offset @ rotation + translation
    coordinates[1].reverse()
    Chem, AllChem = rdkit()
    strands = []
    for chain, text, rows in zip(("A", "B"), (sequence, other), coordinates, strict=True):
        mol = Chem.MolFromFASTA(text, flavor=6 if kind == "DNA" else 2)
        if mol is None:
            raise ValueError("Nucleotide topology construction failed")
        conf = Chem.Conformer(mol.GetNumAtoms())
        for atom in mol.GetAtoms():
            info = atom.GetPDBResidueInfo()
            info.SetChainId(chain)
            conf.SetAtomPosition(
                atom.GetIdx(), rows[info.GetResidueNumber() - 1][info.GetName().strip()].tolist()
            )
        mol.AddConformer(conf)
        strands.append(mol)
    mol = Chem.AddHs(Chem.CombineMols(*strands), addCoords=True, addResidueInfo=True)
    if not AllChem.MMFFHasAllMoleculeParams(mol):
        raise ValueError("Duplex cleanup requires complete MMFF94 parameters")
    # Fixed base heavy atoms preserve experimental pairing and helical step;
    # backbone and hydrogens relax. Preserve each observed sugar delta torsion.
    from rdkit.Chem import rdMolTransforms

    residues: dict[tuple[str, int], dict[str, int]] = {}
    for atom in mol.GetAtoms():
        info = atom.GetPDBResidueInfo()
        residues.setdefault((info.GetChainId(), info.GetResidueNumber()), {})[
            info.GetName().strip()
        ] = atom.GetIdx()
    targets = []
    for row in residues.values():
        ids = tuple(row[k] for k in ("C5'", "C4'", "C3'", "O3'"))
        targets.append((ids, rdMolTransforms.GetDihedralDeg(mol.GetConformer(), *ids)))
    fixed_positions = np.array(mol.GetConformer().GetPositions(), copy=True)
    fixed_indices = []
    props = AllChem.MMFFGetMoleculeProperties(mol)
    code = 1
    for strength in (10.0, 100.0, 1000.0):
        ff = AllChem.MMFFGetMoleculeForceField(mol, props, ignoreInterfragInteractions=False)
        for atom in mol.GetAtoms():
            name = atom.GetPDBResidueInfo().GetName().strip()
            if (
                atom.GetAtomicNum() > 1
                and "'" not in name
                and name not in ("P", "OP1", "OP2", "OP3")
            ):
                ff.AddFixedPoint(atom.GetIdx())
                if strength == 10.0:
                    fixed_indices.append(atom.GetIdx())
        for ids, angle in targets:
            ff.MMFFAddTorsionConstraint(*ids, False, angle - 0.001, angle + 0.001, strength)
        ff.Initialize()
        code = ff.Minimize(maxIts=4000, forceTol=1e-4, energyTol=1e-7)
    if code:
        raise ValueError("Duplex backbone cleanup did not converge; no geometry accepted")
    from vibeview.builder_relaxation import validate_geometry

    validate_geometry(mol, Chem.CombineMols(*strands))
    for ids, target in targets:
        measured = rdMolTransforms.GetDihedralDeg(mol.GetConformer(), *ids)
        if abs((measured - target + 180) % 360 - 180) > 0.1:
            raise ValueError("Duplex sugar conformation could not be retained")
    if not np.allclose(
        mol.GetConformer().GetPositions()[fixed_indices],
        fixed_positions[fixed_indices],
        atol=1e-8,
        rtol=0,
    ):
        raise ValueError("Duplex cleanup moved fixed base geometry")
    contacts = pairing_contacts(mol, sequence)
    mol.SetProp("builder_pairing", json.dumps(contacts))
    mol.SetProp("builder_sequence", sequence)
    mol.SetProp("builder_complement", other)
    mol.SetProp(
        "builder_nucleic_form", "B-family DNA duplex" if kind == "DNA" else "A-family RNA duplex"
    )
    mol.SetProp("builder_reference", record["source"])
    mol.SetProp(
        "builder_helix_step",
        json.dumps({"rise_angstrom": record["rise"], "twist_degrees": record["twist"]}),
    )
    return mol


def pairing_contacts(mol: Any, sequence: str) -> list[dict[str, Any]]:
    """Measure the intended Watson–Crick network, not nearest arbitrary contacts.

    Require the existing interaction convention D–H···A >=120° and a
    conservative 2.4–3.5 Å donor/acceptor range. Fixed base coordinates are
    separately required to remain unchanged by cleanup.
    """
    from rdkit.Chem import rdMolTransforms

    rows: dict[tuple[str, int], dict[str, int]] = {}
    for atom in mol.GetAtoms():
        info = atom.GetPDBResidueInfo()
        rows.setdefault((info.GetChainId(), info.GetResidueNumber()), {})[
            info.GetName().strip()
        ] = atom.GetIdx()
    conf = mol.GetConformer()
    xyz = np.asarray(conf.GetPositions())
    contacts = []
    for i, base in enumerate(sequence):
        a, b = rows["A", i + 1], rows["B", len(sequence) - i]
        if base not in "AG":
            a, b = b, a
        names = (
            [("N1", "N3"), ("N6", "O4")]
            if base in "ATU"
            else [("N1", "N3"), ("O6", "N4"), ("N2", "O2")]
        )
        for left, right in names:
            first, second = a[left], b[right]
            distance = float(np.linalg.norm(xyz[first] - xyz[second]))
            angles = [
                rdMolTransforms.GetAngleDeg(conf, donor, atom.GetIdx(), acceptor)
                for donor, acceptor in [(first, second), (second, first)]
                for atom in mol.GetAtomWithIdx(donor).GetNeighbors()
                if atom.GetAtomicNum() == 1
            ]
            angle = max(angles, default=0.0)
            if not 2.4 <= distance <= 3.5 or angle < 120:
                raise ValueError("Duplex failed the intended Watson–Crick contact geometry")
            contacts.append(
                {
                    "pair": i + 1,
                    "atoms": [first, second],
                    "distance_angstrom": distance,
                    "DHA_degrees": angle,
                }
            )
    return contacts
