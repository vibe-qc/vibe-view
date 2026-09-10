"""Prepare reproducible, sanitized inputs for the documentation capture tools.

Set VIBE_VIEW_EXAMPLES to a directory containing the project-authored
vibe-qc examples/vibe_view archives. No producer installation is needed.
The computed water, formaldehyde and NaCl archives are copied into _build;
missing panel data is explicitly labelled as illustrative, never computed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "docs" / "_build" / "capture-inputs"
sys.path.insert(0, str(ROOT / "src"))


def _read(path):
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        files = {name: archive.read(name) for name in archive.namelist() if name != "manifest.json"}
    return manifest, files


def _write(path, manifest, files):
    # Provenance must not put a local username or hostname in a public capture.
    for section in manifest["sections"]:
        for member in section["members"].values():
            name = member["path"]
            raw = files[name]
            if member.get("format") == "json":
                text = raw.decode()
                text = re.sub(r'/(?:Users|home)/[^/"\\\s]+', "/home/user", text)
                text = re.sub(r'("(?:hostname|host)"\s*:\s*)"[^"]*"', r'\1"example-host"', text)
                raw = files[name] = text.encode()
            member["sha256"] = hashlib.sha256(raw).hexdigest()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, raw in files.items():
            archive.writestr(name, raw)
    from vibeview.qvf import QVFReader

    with QVFReader(path) as reader:
        for section in reader.sections:
            for member in section.members.values():
                reader._verify_and_read(member)
    return path


def prepare_inputs():
    """Return local paths; fail if a required computed archive is absent."""
    examples = Path(os.environ.get("VIBE_VIEW_EXAMPLES", ROOT / "examples"))
    sources = {
        "water": examples / "runs/qvf_showcase/water.qvf",
        "h2co": examples / "runs/h2co_showcase/h2co.qvf",
        "nacl": examples / "output-nacl-showcase.qvf",
    }
    missing = [str(p) for p in sources.values() if not p.is_file()]
    if missing:
        raise SystemExit(
            "Set VIBE_VIEW_EXAMPLES to the vibe-qc examples/vibe_view "
            "directory. Missing archives:\n" + "\n".join(missing)
        )
    WORK.mkdir(parents=True, exist_ok=True)
    paths = {}
    receipts = {}
    for name, source in sources.items():
        manifest, files = _read(source)
        paths[name] = _write(WORK / f"{name}.qvf", manifest, files)
        receipts[name] = hashlib.sha256(source.read_bytes()).hexdigest()

    from vibeview.onboarding import write_demo

    paths["demo"] = write_demo(WORK / "vibe-view-demo.qvf", force=True)
    manifest, files = _read(paths["h2co"])
    manifest["source"]["calculation"] = "H2CO + illustrative panel fixtures"
    manifest.pop("viewer_defaults", None)

    def add(sid, kind, values):
        members = {}
        for name, value in values.items():
            path = f"illustrative/{sid}/{name}"
            if isinstance(value, np.ndarray):
                value = np.asarray(value, dtype=np.float64)
                files[path] = value.tobytes()
                members[name] = {
                    "path": path,
                    "format": "binary",
                    "dtype": "float64",
                    "shape": list(value.shape),
                }
            else:
                files[path] = json.dumps(value).encode()
                members[name] = {"path": path, "format": "json"}
        manifest["sections"].append(
            {"id": sid, "kind": kind, "label": "Illustrative " + kind, "members": members}
        )

    axis = np.linspace(-4, 4, 40)
    x, y, z = np.meshgrid(axis, axis, axis, indexing="ij")
    gaussian = np.exp(-(x * x + y * y + z * z) / 2)
    grid = {
        "origin": [-4, -4, -4],
        "shape": [40, 40, 40],
        "voxel_vectors": (np.eye(3) * (8 / 39)).tolist(),
    }
    add("difference", "volume.difference", {"grid": grid, "data": x * gaussian * 0.08})
    add("elf", "volume.elf", {"grid": grid, "data": gaussian})
    k = np.linspace(0, 2 * np.pi, 81)
    bands = np.stack(
        [
            -6 + 0.9 * np.cos(k),
            -3 + 0.7 * np.sin(k),
            -1 - 0.6 * np.cos(k),
            2 + 0.8 * np.cos(k),
            4 + np.sin(k),
        ],
        axis=1,
    )
    add(
        "bands",
        "bands",
        {
            "kpath": {
                "fermi": 0,
                "n_kpoints": 81,
                "n_bands": 5,
                "segments": [
                    {"start": 0, "end": 40, "label_start": "Γ", "label_end": "X"},
                    {"start": 40, "end": 80, "label_start": "X", "label_end": "L"},
                ],
            },
            "eigenvalues": bands[None, :, :],
        },
    )
    energies = np.linspace(-8, 7, 300)
    dos = sum(np.exp(-(((energies - e) / 0.35) ** 2)) for e in bands.ravel()) / 81
    add("dos", "dos.total", {"energies": energies, "dos": dos})
    add(
        "ecd",
        "spectra.ecd",
        {
            "spectrum": {
                "frequencies": [2.5, 3.2, 4.1, 5.4, 6.2],
                "intensities": [-0.2, 0.4, -0.15, 0.3, -0.1],
            }
        },
    )
    add(
        "nmr",
        "spectra.nmr",
        {
            "spectrum": {
                "isotope": "1H",
                "reference": "Illustrative reference",
                "chemical_shifts": [
                    {"atom_index": 2, "symbol": "H", "isotropic_shift_ppm": 9.8},
                    {"atom_index": 3, "symbol": "H", "isotropic_shift_ppm": 9.8},
                ],
            }
        },
    )
    add("symmetry", "structure.symmetry", {"data": {"point_group": "C2v"}})
    structure_section = next(s for s in manifest["sections"] if s["kind"] == "structure")
    structure = json.loads(files[structure_section["members"]["structure"]["path"]])
    coords = np.array([a["position"] for a in structure["atoms"]])
    points, bonds = [], []
    for a, b in [(0, 1), (0, 2), (0, 3)]:
        midpoint = (coords[a] + coords[b]) / 2
        points.append(
            {
                "type": "bcp",
                "atom_pair": [a, b],
                "position": midpoint.tolist(),
                "rho": 0.2,
                "laplacian": -0.3,
            }
        )
        bonds.append(
            {"atoms": [a, b], "path": [coords[a].tolist(), midpoint.tolist(), coords[b].tolist()]}
        )
    add("qtaim", "topology.qtaim", {"critical_points": {"points": points, "bond_paths": bonds}})
    frames = np.array([coords * (1 + 0.15 * (1 - t)) for t in np.linspace(0, 1, 7)])
    add(
        "trajectory",
        "trajectory",
        {
            "metadata": {
                "atoms": structure["atoms"],
                "energies": [-113 - 0.5 * t for t in np.linspace(0, 1, 7)],
            },
            "coords": frames,
        },
    )
    add(
        "reaction",
        "reaction.path",
        {
            "metadata": {
                "atoms": structure["atoms"],
                "energies": [0, 0.01, 0.03, 0.04, 0.03, 0.01, 0],
                "reaction_coordinate": np.linspace(0, 1, 7).tolist(),
                "waypoints": [
                    {"frame_index": 0, "label": "Start", "kind": "reactant"},
                    {"frame_index": 3, "label": "Illustrative barrier", "kind": "transition_state"},
                    {"frame_index": 6, "label": "End", "kind": "product"},
                ],
            },
            "coords": frames,
        },
    )
    paths["all"] = _write(WORK / "illustrative-panels.qvf", manifest, files)
    (WORK / "input-sha256.json").write_text(json.dumps(receipts, indent=2) + "\n")
    return paths
