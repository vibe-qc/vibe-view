#!/usr/bin/env python
"""Write ``timings.qvf``: a water molecule and an ``x_example.timings`` section.

    python make_archive.py [output.qvf]

Uses the standard library only, so it also shows what a vendor section is in
a QVF archive: a manifest entry with a kind in the ``x_<vendor>.`` namespace
and members whose SHA-256 the reader verifies before a renderer sees them.
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

STRUCTURE = {
    "atoms": [
        {"symbol": "O", "atomic_number": 8, "position": [0.0, 0.0, 0.1173]},
        {"symbol": "H", "atomic_number": 1, "position": [0.0, 0.7572, -0.4692]},
        {"symbol": "H", "atomic_number": 1, "position": [0.0, -0.7572, -0.4692]},
    ],
    "pbc": [False, False, False],
    "lattice_vectors": None,
}

STEPS = [
    {"step": "integrals", "seconds": 1.84},
    {"step": "SCF iterations", "seconds": 6.12},
    {"step": "gradient", "seconds": 2.37},
    {"step": "write results", "seconds": 0.21},
]


def _member(files: dict[str, bytes], path: str, payload: object) -> dict[str, str]:
    data = json.dumps(payload).encode("utf-8")
    files[path] = data
    return {"path": path, "format": "json", "sha256": hashlib.sha256(data).hexdigest()}


def build(path: Path) -> Path:
    files: dict[str, bytes] = {}
    manifest = {
        "qvf_version": 1,
        "source": {"program": "example", "version": "1.0", "calculation": "water timings"},
        "sections": [
            {
                "id": "structure",
                "kind": "structure",
                "members": {"structure": _member(files, "structure.json", STRUCTURE)},
            },
            {
                "id": "timings",
                "kind": "x_example.timings",
                "label": "Water single point, step timings",
                "members": {"steps": _member(files, "timings/steps.json", STEPS)},
            },
        ],
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        for name, data in files.items():
            archive.writestr(name, data)
    return path


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("timings.qvf")
    print(build(out))
