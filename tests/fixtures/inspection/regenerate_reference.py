"""Explicit reference regeneration, run from repository root. Not a pytest test."""

import json
from pathlib import Path

import numpy as np
from scipy.stats import qmc

from vibeview.converters import pdb_to_qvf
from vibeview.elements import vdw_radius
from vibeview.molecular_surfaces import build_surface
from vibeview.qvf import QVFReader

with QVFReader(pdb_to_qvf(Path("tests/fixtures/inspection/1crn.pdb"))) as reader:
    s = reader.read_structure()
xyz = np.array([a.position for a in s.atoms])
r = np.array([vdw_radius(a.atomic_number) for a in s.atoms])
result = {}
for kind in ["vdw", "sas"]:
    radii = r + (1.4 if kind == "sas" else 0)
    n = 4096
    i = np.arange(n)
    z = 1 - 2 * (i + 0.5) / n
    theta = i * np.pi * (3 - np.sqrt(5))
    sphere = np.column_stack(
        (np.sqrt(1 - z * z) * np.cos(theta), np.sqrt(1 - z * z) * np.sin(theta), z)
    )
    area = 0
    for j, (p, radius) in enumerate(zip(xyz, radii, strict=True)):
        points = p + radius * sphere
        visible = np.ones(n, dtype=bool)
        for k in np.flatnonzero(np.linalg.norm(xyz - p, axis=1) < radii + radius):
            if k != j:
                visible &= np.linalg.norm(points - xyz[k], axis=1) >= radii[k]
        area += visible.mean() * 4 * np.pi * radius**2
    low = np.min(xyz - radii[:, None], axis=0)
    high = np.max(xyz + radii[:, None], axis=0)
    points = qmc.Sobol(3, scramble=False).random_base2(18) * (high - low) + low
    inside = np.zeros(len(points), dtype=bool)
    for p, radius in zip(xyz, radii, strict=True):
        inside |= np.linalg.norm(points - p, axis=1) <= radius
    volume = float(inside.mean() * np.prod(high - low))
    mesh = build_surface(s, kind=kind, spacing=0.25)
    result[kind] = {
        "area_reference": area,
        "volume_reference": volume,
        "area": mesh.area,
        "volume": mesh.volume,
        "area_relative_error": mesh.area / area - 1,
        "volume_relative_error": mesh.volume / volume - 1,
        "volume_sampling_standard_error_A3": float(
            np.sqrt(inside.mean() * (1 - inside.mean()) / len(points)) * np.prod(high - low)
        ),
    }
    assert abs(mesh.area / area - 1) < 0.05 and abs(mesh.volume / volume - 1) < 0.05
    print(kind, result[kind], flush=True)
coarse = build_surface(s, kind="ses", spacing=0.25)
fine = build_surface(s, kind="ses", spacing=0.1, max_voxels=64_000_000)
result["ses"] = {
    "area_reference": fine.area,
    "volume_reference": fine.volume,
    "area": coarse.area,
    "volume": coarse.volume,
    "area_relative_error": coarse.area / fine.area - 1,
    "volume_relative_error": coarse.volume / fine.volume - 1,
    "reference": "same distance-transform method at0.1A; grid-convergence only",
}
print(result["ses"], flush=True)
assert abs(coarse.area / fine.area - 1) < 0.1 and abs(coarse.volume / fine.volume - 1) < 0.1
Path("protein-reference.json").write_text(json.dumps(result, indent=2))
