"""QTAIM renderer — critical points and bond paths in 3D."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pyvista as pv

from vibeview.renderers import BaseRenderer
from vibeview.renderers._coerce import as_float, as_vector
from vibeview.renderers._html import cell, empty_panel, panel_document

if TYPE_CHECKING:
    from vibeview.qvf import QTAIMData, QVFReader, Section


# CP type → colour mapping (standard QTAIM convention).
_CP_COLOURS: dict[str, str] = {
    "bcp": "#FF8800",  # orange — bond critical point (3,-1)
    "rcp": "#FFDD00",  # yellow — ring critical point (3,+1)
    "ccp": "#00CC44",  # green — cage critical point (3,+3)
    "ncp": "#CC44FF",  # purple — nuclear critical point (3,-3)
}

_CP_RADIUS: dict[str, float] = {
    "bcp": 0.15,
    "rcp": 0.12,
    "ccp": 0.10,
    "ncp": 0.08,
}

# Bond critical points are recoloured by the SIGN of the density Laplacian,
# which is the actual QTAIM verdict on an interaction and is otherwise
# invisible: rho, laplacian and ellipticity are written into every
# topology.qtaim section and were previously all discarded by this renderer.
#
#   Laplacian < 0  charge concentrated at the BCP -> shared-shell (covalent)
#   Laplacian > 0  charge depleted   at the BCP -> closed-shell (ionic,
#                                                  hydrogen bond, vdW)
#
# Bader, "Atoms in Molecules: A Quantum Theory" (1990); the sign convention
# and its interpretation are § 7.2 there. Cited in vibe-qc's citation
# database as bader_qtaim_1985.
_SHARED_SHELL_COLOUR = "#1E88E5"  # blue  — covalent
_CLOSED_SHELL_COLOUR = "#E53935"  # red   — ionic / H-bond / vdW


def _interaction_label(laplacian: float | None) -> str:
    """Bader's shared- vs closed-shell classification from the Laplacian."""
    if laplacian is None:
        return ""
    return "shared-shell" if laplacian < 0.0 else "closed-shell"


class QTAIMRenderer(BaseRenderer):
    """Renders QTAIM critical points + bond paths in the 3D viewport."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: QTAIMData | None = None

    def load(self) -> QTAIMData:
        if self._data is None:
            self._data = self.reader.read_topology_qtaim(self.section_id)
        return self._data

    def add_to_plotter(self, plotter: pv.Plotter) -> str:
        """Add CP spheres and bond paths to the 3D viewport.

        Returns a status-message string.
        """
        data = self.load()
        n_pts = 0
        n_paths = 0

        # ── Critical-point spheres ──────────────────────────────────
        for pt in data.points:
            cp_type = str(pt.get("type", "bcp"))
            pos = as_vector(pt.get("position"))
            if pos is None or len(pos) != 3:
                continue
            colour = _CP_COLOURS.get(cp_type, "#888888")
            radius = _CP_RADIUS.get(cp_type, 0.12)
            # A BCP's Laplacian sign is the interaction type; show it rather
            # than colouring every BCP the same orange.
            laplacian = as_float(pt.get("laplacian"))
            if cp_type == "bcp" and laplacian is not None:
                colour = _SHARED_SHELL_COLOUR if laplacian < 0.0 else _CLOSED_SHELL_COLOUR
            sphere = pv.Sphere(center=pos, radius=radius, theta_resolution=8, phi_resolution=8)
            plotter.add_mesh(
                sphere,
                color=colour,
                name=f"qtaim_cp_{n_pts}",
                show_scalar_bar=False,
            )
            n_pts += 1

        # ── Bond-path polylines ─────────────────────────────────────
        if data.bond_paths:
            for bp in data.bond_paths:
                path = bp.get("path")
                if not isinstance(path, list | tuple) or len(path) < 2:
                    continue
                pts = np.array(path, dtype=float)
                # Create a polyline from the vertices.
                n_seg = len(pts) - 1
                lines = np.column_stack(
                    [
                        np.full(n_seg, 2, dtype=int),
                        np.arange(n_seg),
                        np.arange(1, n_seg + 1),
                    ]
                ).ravel()
                poly = pv.PolyData(pts, lines=lines)
                plotter.add_mesh(
                    poly,
                    color="#FF8800",
                    opacity=0.6,
                    line_width=2,
                    name=f"qtaim_path_{n_paths}",
                    show_scalar_bar=False,
                )
                n_paths += 1

        status = f"QTAIM: {n_pts} critical points"
        if n_paths:
            status += f", {n_paths} bond paths"
        bcp_laplacians = [
            value
            for p in data.points
            if str(p.get("type")) == "bcp"
            for value in [as_float(p.get("laplacian"))]
            if value is not None
        ]
        n_shared = len([value for value in bcp_laplacians if value < 0.0])
        n_closed = len(bcp_laplacians) - n_shared
        if n_shared or n_closed:
            status += f" ({n_shared} shared-shell, {n_closed} closed-shell)"
        return status

    def render_to_html(self, symbols: list[str] | None = None) -> str:
        """Table of the bond critical points and their scalars.

        The dots in the viewport say *where* the critical points are; these
        numbers say what they mean, and they are already in every file:

        * **rho** at the BCP tracks bond strength, correlating with bond
          order for a given pair of elements.
        * **Laplacian** sign is Bader's classification -- negative is
          shared-shell (covalent), positive is closed-shell (ionic,
          hydrogen bond, van der Waals).
        * **Ellipticity** is pi-character / asymmetry: near zero for a
          cylindrically symmetric bond, appreciable where a pi system or a
          strained bond breaks that symmetry.

        Units follow the producer (``vibeqc.qtaim.CriticalPoint``): rho in
        e/A^3, Laplacian in e/A^5, ellipticity dimensionless.
        """
        data = self.load()

        def _atom(index: int) -> str:
            if symbols is not None and 0 <= index < len(symbols):
                return f"{symbols[index]}{index + 1}"
            return f"atom {index + 1}"

        rows: list[str] = []
        for point in data.points:
            if str(point.get("type")) != "bcp":
                continue
            pair = as_vector(point.get("atom_pair"))
            label = (
                f"{_atom(int(pair[0]))}–{_atom(int(pair[1]))}"
                if pair is not None and len(pair) == 2
                else "—"
            )
            rho = as_float(point.get("rho"))
            laplacian = as_float(point.get("laplacian"))
            ellipticity = as_float(point.get("ellipticity"))
            verdict = _interaction_label(laplacian)
            colour = (
                _SHARED_SHELL_COLOUR
                if verdict == "shared-shell"
                else _CLOSED_SHELL_COLOUR
            )
            rows.append(
                "<tr>"
                f"<td>{cell(label)}</td>"
                f"<td>{'' if rho is None else f'{rho:.4f}'}</td>"
                f"<td>{'' if laplacian is None else f'{laplacian:+.4f}'}</td>"
                f"<td>{'' if ellipticity is None else f'{ellipticity:.3f}'}</td>"
                f"<td style='text-align:left;color:{colour}'>{cell(verdict)}</td>"
                "</tr>"
            )

        if not rows:
            return empty_panel("No bond critical points in this section.")
        return panel_document(
            "<table class='data'><thead><tr>"
            "<th>Bond</th>"
            "<th>&rho; (e/&#8491;<sup>3</sup>)</th>"
            "<th>&nabla;<sup>2</sup>&rho; (e/&#8491;<sup>5</sup>)</th>"
            "<th>&epsilon;</th>"
            "<th>Interaction</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        )
