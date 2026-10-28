"""Replay a hash-bound scalar-field figure with released vibe-view 3.0.0 APIs.

Run with the intended viewer's Python, not necessarily this checkout's Python.
See docs/publication_figures.md. No calculation or QVF write is performed.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import platform
from pathlib import Path
from typing import Any

import numpy as np
import pyvista as pv
import vtk

import vibeview
from vibeview.headless import headless_plotter, snapshot
from vibeview.plotter_compat import remove_actor
from vibeview.qvf import QVFReader
from vibeview.renderers.structure import StructureRenderer
from vibeview.renderers.volume import VolumeRenderer
from vibeview.viewer_defaults import VolumeHints


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_viewer(identity_path: Path) -> dict[str, Any]:
    """Compare all imported package Python files with an independently trusted manifest."""
    manifest = json.loads(identity_path.read_text())
    package = Path(vibeview.__file__).resolve().parent
    actual = {
        "src/vibeview/" + p.relative_to(package).as_posix(): sha(p.read_bytes())
        for p in package.rglob("*.py")
    }
    if actual != manifest["files_sha256"] or vibeview.__version__ != manifest["version"]:
        raise ValueError("imported viewer Python sources differ from the release identity")
    return {
        "version": vibeview.__version__,
        "source_commit": manifest["source_commit"],
        "source_archive_sha256": manifest["source_archive_sha256"],
        "identity_sha256": sha(identity_path.read_bytes()),
        "verified_python_files": len(actual),
        "scope": "Python package source only; dependencies identified separately",
    }


def validate_recipe(recipe: dict[str, Any]) -> None:
    keys = {
        "version", "source_sha256", "structure", "field", "field_units", "isovalue",
        "signed", "positive_color", "negative_color", "opacity", "element_colors",
        "size", "replication", "cell_outline", "cell_color", "background", "camera",
    }
    if set(recipe) != keys or recipe["version"] != 1:
        raise ValueError("recipe must have exactly the documented version-1 keys")
    for key in ("signed", "cell_outline"):
        if type(recipe[key]) is not bool:
            raise ValueError(f"{key} must be boolean")
    for key, length, minimum, maximum in (("size", 2, 64, 8192), ("replication", 3, 1, 8)):
        value = recipe[key]
        if not isinstance(value, list) or len(value) != length or any(
            type(x) is not int or not minimum <= x <= maximum for x in value
        ):
            raise ValueError(f"invalid {key}")
    for key, lower, upper in (("isovalue", 0, float("inf")), ("opacity", 0, 1)):
        value = recipe[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"invalid {key}")
        if not np.isfinite(value) or not lower < value <= upper:
            raise ValueError(f"invalid {key}")
    camera = recipe["camera"]
    if set(camera) != {"position", "focal_point", "view_up", "parallel_scale"}:
        raise ValueError("camera requires position, focal_point, view_up and parallel_scale")
    vectors = np.asarray([camera[k] for k in ("position", "focal_point", "view_up")], float)
    if vectors.shape != (3, 3) or not np.isfinite(vectors).all():
        raise ValueError("camera vectors must be finite xyz triples in angstrom")
    if np.linalg.norm(np.cross(vectors[0] - vectors[1], vectors[2])) < 1e-12:
        raise ValueError("camera view and up vectors must be nonzero and independent")
    scale = camera["parallel_scale"]
    if isinstance(scale, bool) or not np.isfinite(scale) or scale <= 0:
        raise ValueError("parallel_scale must be positive")
    for color in [recipe[k] for k in (
        "positive_color", "negative_color", "background", "cell_color"
    )] + list(recipe["element_colors"].values()):
        pv.Color(color)  # Reject invalid colours before producing an image.


def render(source: Path, recipe_path: Path, identity_path: Path, output: Path) -> dict[str, Any]:
    runner_sha256 = sha(Path(__file__).read_bytes())
    recipe_bytes = recipe_path.read_bytes()
    recipe = json.loads(recipe_bytes)
    validate_recipe(recipe)
    identity = verify_viewer(identity_path)
    source_bytes = source.read_bytes()
    if sha(source_bytes) != recipe["source_sha256"]:
        raise ValueError("source QVF SHA256 differs from recipe")
    if output.exists():
        raise ValueError("output directory already exists; preserve earlier evidence")
    with QVFReader(source_bytes) as reader:
        manifest = reader.manifest_json()
        section = reader.get_section(recipe["field"])
        if section.kind not in {"volume.density", "volume.orbital"}:
            raise ValueError("recipe supports stored density and real orbital fields only")
        expected_units = "electron/bohr^3" if section.kind == "volume.density" else "bohr^(-3/2)"
        if recipe["field_units"] != expected_units:
            raise ValueError("field units differ from the QVF v1 kind convention")
        if recipe["signed"] != (section.kind == "volume.orbital"):
            raise ValueError("density needs one positive surface; orbital needs both signed lobes")
        if section.kind == "volume.orbital" and section.component not in {None, "real"}:
            raise ValueError("orbital component must be real amplitude, not magnitude or density")
        extra = section.model_extra or {}
        if extra.get("structure_ref", recipe["structure"]) != recipe["structure"]:
            raise ValueError("field structure_ref conflicts with recipe")
        reader.show_structure(recipe["structure"])
        structure = reader.read_structure(recipe["structure"])
        replication = tuple(recipe["replication"])
        if any(n > 1 and not periodic
               for n, periodic in zip(replication, structure.pbc, strict=True)):
            raise ValueError("cannot replicate a nonperiodic axis")
        lattice = structure.lattice_vectors
        if (recipe["cell_outline"] or any(n > 1 for n in replication)) and lattice is None:
            raise ValueError("cell or replication requested without a lattice")
        if recipe["cell_outline"] and not all(structure.pbc):
            raise ValueError("this example's cell outline requires a 3D periodic cell")
        renderer = VolumeRenderer(section, reader)
        original = reader.read_volume_data(section.id)
        data = renderer.load_data()
        grid = renderer.load_grid()
        if grid.units != "bohr" or not np.isfinite(data).all():
            raise ValueError("expected a finite QVF field on a bohr grid")
        if data.dtype != original.dtype or not np.array_equal(data, original):
            raise ValueError("renderer changed the stored field")
        # Validate payload checksums before rendering; preserve the original member bytes.
        members = {}
        import zipfile
        from io import BytesIO

        with zipfile.ZipFile(BytesIO(source_bytes)) as archive:
            for s in manifest["sections"]:
                if s["id"] not in {recipe["structure"], recipe["field"]}:
                    continue
                for role, member in s["members"].items():
                    digest = sha(archive.read(member["path"]))
                    if digest != member.get("sha256"):
                        raise ValueError("selected QVF member hash missing or mismatched")
                    members[f"{s['id']}/{role}"] = {**member, "verified_sha256": digest}
        positions = np.asarray([atom.position for atom in structure.atoms])
        atoms = [atom.symbol for atom in structure.atoms]
        if set(atoms) != set(recipe["element_colors"]):
            raise ValueError("explicit element_colors must cover exactly the structure elements")
        surfaces = []
        with headless_plotter(recipe["size"], background=recipe["background"]) as plotter:
            sr = StructureRenderer(reader.get_section(recipe["structure"]), reader)
            if not np.array_equal([a.position for a in sr.load().atoms], positions):
                raise ValueError("renderer structure differs from the selected source")
            sr.add_to_plotter(
                plotter, replication=replication,
                atom_colors={i: pv.Color(recipe["element_colors"][a]).float_rgb
                             for i, a in enumerate(atoms)},
            )
            # Replace the renderer's primitive-cell outline with an explicit supercell outline.
            remove_actor(plotter, "cell_wireframe")
            if recipe["cell_outline"]:
                corners = np.asarray(list(itertools.product(*[(0, n) for n in replication])))
                vertices = corners @ lattice
                for i, j in itertools.combinations(range(8), 2):
                    if np.count_nonzero(corners[i] != corners[j]) == 1:
                        plotter.add_lines(vertices[[i, j]], color=recipe["cell_color"], width=2)
            for sign in ([1, -1] if recipe["signed"] else [1]):
                level = sign * recipe["isovalue"]
                mesh = renderer.make_mesh(
                    VolumeHints(isovalue=level), replication=replication,
                    lattice_vectors=lattice, wrap_to_center=False, pbc=structure.pbc,
                )
                if mesh is None or mesh.n_points == 0:
                    raise ValueError(f"empty surface at {level}; choose an explicit valid isovalue")
                color = recipe["positive_color" if sign == 1 else "negative_color"]
                plotter.add_mesh(mesh, color=color, opacity=recipe["opacity"],
                                 smooth_shading=True, show_scalar_bar=False)
                surfaces.append({"level": level, "color": color, "points": mesh.n_points,
                                 "cells": mesh.n_cells, "bounds_angstrom": list(mesh.bounds)})
            camera = recipe["camera"]
            plotter.camera_position = [camera[k] for k in ("position", "focal_point", "view_up")]
            plotter.camera.parallel_projection = True
            plotter.camera.parallel_scale = camera["parallel_scale"]
            plotter.reset_camera_clipping_range()
            output.mkdir(parents=True)
            snapshot(plotter, output / "image.png")
            actual_camera = {"position_focal_up": [list(plotter.camera.position),
                                                   list(plotter.camera.focal_point),
                                                   list(plotter.camera.up)],
                             "parallel_scale": plotter.camera.parallel_scale,
                             "clipping_range": list(plotter.camera.clipping_range)}
        if not np.array_equal(renderer.load_data(), original):
            raise ValueError("rendering mutated field samples")
        offsets = (np.asarray(list(itertools.product(*[range(n) for n in replication]))) @ lattice
                   if lattice is not None else np.zeros((1, 3)))
        report = {
            "recipe": recipe, "recipe_sha256": sha(recipe_bytes),
            "runner_sha256": runner_sha256, "viewer": identity,
            "dependencies": {"python": platform.python_version(), "numpy": np.__version__,
                             "pyvista": pv.__version__, "vtk": vtk.vtkVersion.GetVTKVersion()},
            "source": {"sha256": sha(source_bytes), "producer": manifest["source"],
                       "members": members},
            "structure": {"symbols": atoms, "positions_angstrom": positions.tolist(),
                          "exact_renderer_position_equality": True,
                          "representation": "ball_and_stick; released renderer bond-order colors",
                          "pbc": list(structure.pbc), "replica_offsets_angstrom": offsets.tolist(),
                          "displayed_atom_count": len(atoms) * len(offsets)},
            "field": {"section": section.id, "label": extra.get("label"), "kind": section.kind,
                      "dtype": str(data.dtype), "shape": list(data.shape),
                      "units": expected_units, "units_basis": "QVF v1 kind convention",
                      "origin_bohr": grid.origin.tolist(),
                      "voxel_vectors_bohr": grid.voxel_vectors.tolist(),
                      "grid_to_angstrom": grid.to_angstrom,
                      "min": float(data.min()), "max": float(data.max()),
                      "exact_sample_equality": True, "wrap_to_center": False},
            "surfaces": surfaces, "camera": actual_camera,
            "image_sha256": sha((output / "image.png").read_bytes()),
            "limitations": ("Rendering qualification, not scientific acceptance. Contours "
                            "interpolate stored samples; replication copies surfaces without "
                            "filling "
                            "periodic seams. PNG pixels may vary across VTK/GPU/platform versions. "
                            "Legacy fields without structure_ref use the explicit recipe binding, "
                            "which requires user review."),
        }
    (output / "recipe.json").write_bytes(recipe_bytes)
    (output / "evidence.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("qvf", type=Path)
    parser.add_argument("recipe", type=Path)
    parser.add_argument("--viewer-identity", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    render(args.qvf, args.recipe, args.viewer_identity, args.output)
