"""CML (Chemical Markup Language) export for vibe-view.

CML is an XML-based format for chemical information, widely used
in cheminformatics and interoperable with tools like OpenBabel,
RDKit, and Jmol.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from xml.dom import minidom
from xml.etree import ElementTree as ET

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader

import numpy as np

from vibeview.bonds import in_cell, structure_bonds


def _cml_order(order: float) -> str:
    """CML bond order: ``1``/``2``/``3``, ``A`` for aromatic (1.5), else the number."""
    if order == 1.5:
        return "A"
    if float(order).is_integer():
        return str(int(order))
    return f"{order:g}"


def export_cml(reader: QVFReader, output_path: str) -> str:
    """Export molecular structure as a CML file.

    Parameters
    ----------
    reader : QVFReader
    output_path : str

    Returns
    -------
    str
        Pretty-printed CML XML string.
    """
    try:
        sdata = reader.read_structure()
    except Exception:
        root = ET.Element("cml", xmlns="http://www.xml-cml.org/schema")
        tree = ET.ElementTree(root)
        tree.write(output_path, encoding="utf-8", xml_declaration=True)
        return ""

    symbols = [a.symbol for a in sdata.atoms]
    positions = np.array([a.position for a in sdata.atoms], dtype=float)

    # Build CML document
    root = ET.Element("cml", xmlns="http://www.xml-cml.org/schema")
    mol = ET.SubElement(root, "molecule", id="m1")

    # Atom array
    atom_array = ET.SubElement(mol, "atomArray")
    for i, (symbol, pos) in enumerate(zip(symbols, positions, strict=True)):
        ET.SubElement(
            atom_array,
            "atom",
            id=f"a{i + 1}",
            elementType=symbol,
            x3=f"{pos[0]:.6f}",
            y3=f"{pos[1]:.6f}",
            z3=f"{pos[2]:.6f}",
        )

    # Bond array: the viewer's connectivity (explicit bonds when the archive
    # has them, otherwise the shared covalent-radius inference). CML addresses
    # atoms by id and has no periodic images, so bonds across a cell face are
    # left out.
    bond_array = ET.SubElement(mol, "bondArray")
    for bond_idx, (i, j, order, _image) in enumerate(in_cell(structure_bonds(sdata)), start=1):
        ET.SubElement(
            bond_array,
            "bond",
            id=f"b{bond_idx}",
            atomRefs2=f"a{i + 1} a{j + 1}",
            order=_cml_order(order),
        )

    # Pretty-print XML
    xml_str = ET.tostring(root, encoding="utf-8")
    dom = minidom.parseString(xml_str)
    pretty = dom.toprettyxml(indent="  ")

    Path(output_path).write_text(pretty, encoding="utf-8")
    return pretty
