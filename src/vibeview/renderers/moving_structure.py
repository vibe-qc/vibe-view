"""Reusable glyph inputs for live geometry and large filmstrip frames.

Connectivity follows each displayed geometry using the shared covalent-radius
rule (Cordero et al., doi:10.1039/B801115J); it is a display heuristic, not a
reaction or bond-order assignment. No frame meshes are retained.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pyvista as pv

from vibeview.bonds import infer_bonds
from vibeview.plotter_compat import remove_actor
from vibeview.renderers.cell import bond_far_endpoints
from vibeview.renderers.structure import (
    GLYPH_DIRECTION_ARRAY,
    GLYPH_RGB_ARRAY,
    GLYPH_SCALE_ARRAY,
    atom_display_radius,
    cpk_color,
    instanced_glyph_actor,
)


class MovingStructure:
    """Two persistent actors; replace only instance arrays as a frame changes."""

    def __init__(self, numbers: Sequence[int], representation: str = "ball_and_stick") -> None:
        self.numbers = list(numbers)
        self.representation = representation
        unique, inverse = np.unique(numbers, return_inverse=True)
        self.rgb = np.asarray([pv.Color(cpk_color(int(z))).int_rgb for z in unique],
                              dtype=np.uint8)[inverse]
        self.radii = np.asarray([atom_display_radius(int(z), representation)
                                 for z in unique], dtype=np.float32)[inverse]
        self.atoms: Any = None
        self.bonds: Any = None

    def update(self, plotter: pv.Plotter, positions: np.ndarray, *,
               prefix: str = "traj_atom_", lattice: np.ndarray | None = None,
               pbc: Sequence[bool] = (False, False, False)) -> None:
        # A volume-series frame can use this same actor prefix between two
        # visits to a cached trajectory. Do not leave those atoms behind.
        for name, actor in list(plotter.renderer.actors.items()):
            if (str(name).startswith(prefix) and actor is not self.atoms
                    and actor is not self.bonds):
                remove_actor(plotter, str(name))
        positions = np.asarray(positions, dtype=np.float32).reshape(-1, 3)
        if len(positions) != len(self.numbers):
            raise ValueError("Frame atom count differs from its reference")
        if self.representation in ("ball_and_stick", "space_filling"):
            if self.atoms is None:
                self.atoms = instanced_glyph_actor(
                    positions, self.rgb,
                    pv.Sphere(radius=1.0, theta_resolution=12, phi_resolution=8), self.radii)
            else:
                points = pv.wrap(self.atoms.GetMapper().GetInputDataObject(0, 0))
                points.points = positions
            if plotter.renderer.actors.get(prefix + "instances") is not self.atoms:
                plotter.add_actor(self.atoms, name=prefix + "instances", reset_camera=False)
        if self.representation not in ("ball_and_stick", "sticks_only", "wireframe"):
            return
        bonds = infer_bonds(positions, self.numbers, lattice=lattice, pbc=pbc)
        starts, ends = bond_far_endpoints(positions, bonds, lattice, pbc)
        direction = ends - starts
        lengths = np.linalg.norm(direction, axis=1)
        keep = lengths > 1e-6
        centers = (starts[keep] + ends[keep]) * 0.5
        directions = direction[keep] / lengths[keep, None]
        scales = np.column_stack((lengths[keep], np.full(keep.sum(), 0.08),
                                   np.full(keep.sum(), 0.08)))
        colors = np.full((len(centers), 3), 102, dtype=np.uint8)
        name = prefix + "bonds"
        if not len(centers):
            if self.bonds is not None:
                self.bonds.SetVisibility(False)
            return
        if self.bonds is None:
            self.bonds = instanced_glyph_actor(
                centers, colors, pv.Cylinder(direction=(1, 0, 0), radius=1, height=1,
                                               resolution=8), scales, directions)
        else:
            points = pv.wrap(self.bonds.GetMapper().GetInputDataObject(0, 0))
            # Topology can change at an open end; resize all arrays together.
            points.clear_data()
            points.points = np.asarray(centers, dtype=np.float32)
            points.point_data[GLYPH_RGB_ARRAY] = colors
            points.point_data[GLYPH_SCALE_ARRAY] = np.asarray(scales, dtype=np.float32)
            points.point_data[GLYPH_DIRECTION_ARRAY] = np.asarray(directions, dtype=np.float32)
        self.bonds.SetVisibility(True)
        if plotter.renderer.actors.get(name) is not self.bonds:
            plotter.add_actor(self.bonds, name=name, reset_camera=False)
