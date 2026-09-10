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


def export_cml(reader: "QVFReader", output_path: str) -> str:
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
    for i, (symbol, pos) in enumerate(zip(symbols, positions)):
        ET.SubElement(
            atom_array,
            "atom",
            id=f"a{i + 1}",
            elementType=symbol,
            x3=f"{pos[0]:.6f}",
            y3=f"{pos[1]:.6f}",
            z3=f"{pos[2]:.6f}",
        )

    # Bond array (distance-based detection)
    cov_radii = {
        "H": 0.31,
        "C": 0.76,
        "N": 0.71,
        "O": 0.66,
        "F": 0.57,
        "P": 1.07,
        "S": 1.05,
        "Cl": 1.02,
        "Br": 1.20,
        "I": 1.39,
    }
    radii = [cov_radii.get(s, 0.8) for s in symbols]

    bond_array = ET.SubElement(mol, "bondArray")
    bond_idx = 1
    for i in range(len(symbols)):
        for j in range(i + 1, len(symbols)):
            d = np.linalg.norm(positions[i] - positions[j])
            threshold = (radii[i] + radii[j]) * 1.2
            if 0.4 < d < threshold:
                ET.SubElement(
                    bond_array,
                    "bond",
                    id=f"b{bond_idx}",
                    atomRefs2=f"a{i + 1} a{j + 1}",
                    order="1",
                )
                bond_idx += 1

    # Pretty-print XML
    xml_str = ET.tostring(root, encoding="utf-8")
    dom = minidom.parseString(xml_str)
    pretty = dom.toprettyxml(indent="  ")

    Path(output_path).write_text(pretty, encoding="utf-8")
    return pretty
