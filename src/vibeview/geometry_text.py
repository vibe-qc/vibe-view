"""Shared structure text for browser downloads and command-line exports."""

from __future__ import annotations

import numpy as np

from vibeview.crystal_builder import abc_from_cell
from vibeview.qvf import StructureData


def xyz_text(structure: StructureData, comment: str = "vibe-view export") -> str:
    """Write Cartesian angstrom coordinates with provenance on one comment line."""
    comment = " ".join(comment.splitlines())
    lines = [str(len(structure.atoms)), comment]
    for atom in structure.atoms:
        x, y, z = atom.position
        lines.append(f"{atom.symbol:<2} {x:12.6f} {y:12.6f} {z:12.6f}")
    return "\n".join(lines) + "\n"


def cif_text(structure: StructureData, comment: str = "CIF exported by vibe-view") -> str:
    """Write a periodic cell with fractional sites, or molecular Cartesian sites.

    A nonperiodic bounding cell does not make a molecule a crystal. The same
    condition controls both the cell block and coordinate convention. CIF
    cell parameters do not encode which individual QVF axes are periodic.
    Line breaks in provenance are replaced with spaces to keep it commented.
    """
    comment = " ".join(comment.splitlines())
    lines = ["data_vibe-view", f"# {comment}"]
    periodic = structure.lattice_vectors is not None and any(structure.pbc)
    positions = np.array([atom.position for atom in structure.atoms], dtype=float).reshape(-1, 3)
    if periodic:
        lattice = np.asarray(structure.lattice_vectors, dtype=float)
        parameters = abc_from_cell(lattice)
        names = ("length_a", "length_b", "length_c", "angle_alpha", "angle_beta", "angle_gamma")
        lines.extend(
            f"_cell_{name} {value:.4f}" for name, value in zip(names, parameters, strict=True)
        )
        # QVF lattice vectors are rows: cart = frac @ lattice.
        positions = np.linalg.solve(lattice.T, positions.T).T
    # IUCr core CIF dictionary: fract_* is dimensionless; Cartn_* is in Å.
    # https://www.iucr.org/__data/iucr/cifdic_html/1/cif_core.dic/Iatom_site_fract_.html
    prefix = "_atom_site_fract_" if periodic else "_atom_site_Cartn_"
    lines.extend(
        ["loop_", "_atom_site_label", "_atom_site_type_symbol", *(prefix + axis for axis in "xyz")]
    )
    for index, (atom, (x, y, z)) in enumerate(zip(structure.atoms, positions, strict=True), 1):
        lines.append(f"atom{index} {atom.symbol:<2} {x:10.6f} {y:10.6f} {z:10.6f}")
    return "\n".join(lines) + "\n"
