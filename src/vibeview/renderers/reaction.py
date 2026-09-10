"""Reaction-path renderers (`reaction.path`, `reaction.waypoints`).

`reaction.path` is binary-layout-identical to `trajectory`, so we reuse
the trajectory animation machinery and layer waypoint annotations on
top. `reaction.waypoints` is a pure annotation that points at an
existing `trajectory` section; it carries no coords of its own.

For QVF v2 archives (periodic reaction paths) the renderer additionally
surfaces the unit-cell box (drawn once at activation) and wraps atom
positions into the central cell along the first `dim` lattice vectors —
the slab convention has `dim=2`, so a+b are wrapped and the non-physical
column c is left alone.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    from vibeview.qvf import (
        QVFReader,
        ReactionPathData,
        ReactionWaypoint,
        ReactionWaypointsData,
        Section,
    )

matplotlib.use("Agg")

# Bohr → Ångström (CODATA 2018). The QVF v2 schema stores lattice
# vectors in bohr to match ``vibeqc.PeriodicSystem.lattice`` exactly;
# the renderer converts to Å on output for consistency with the atom
# coords (which the writer has already converted to Å).
_BOHR_TO_ANGSTROM = 0.529177210903


_KIND_COLORS = {
    "reactant": "#2ca02c",
    "transition_state": "#d62728",
    "intermediate": "#ff7f0e",
    "product": "#1f77b4",
    "point": "#7f7f7f",
}


def _wrap_positions(
    coords: np.ndarray,
    lattice_angstrom: np.ndarray,
    wrap_axes: int,
) -> np.ndarray:
    """Wrap Cartesian atom positions into the central cell.

    Parameters
    ----------
    coords
        Atom positions, shape ``(n_atoms, 3)``, Å.
    lattice_angstrom
        3x3 lattice matrix, columns = a, b, c, Å.
    wrap_axes
        Number of leading lattice vectors to wrap along. ``wrap_axes=3``
        wraps fully periodic; ``wrap_axes=2`` (slab) wraps a + b and
        leaves c open; ``wrap_axes=1`` wraps only along a. ``0`` is a
        no-op.

    Returns
    -------
    np.ndarray
        Wrapped positions, same shape, Å. Fractional coords are folded
        into ``[0, 1)`` along the wrapped axes; the non-wrapped axes
        retain their original (possibly out-of-cell) fractional value.
    """
    if wrap_axes <= 0:
        return coords.copy()
    inv = np.linalg.inv(lattice_angstrom)
    # Fractional coords: f = L^-1 @ r (r is a column → L^-1 @ r;
    # here r is a row, so r @ (L^-1)^T)
    frac = coords @ inv.T
    # Wrap leading wrap_axes axes into [0, 1); leave the rest alone.
    frac_wrapped = frac.copy()
    frac_wrapped[:, :wrap_axes] = frac[:, :wrap_axes] - np.floor(
        frac[:, :wrap_axes]
    )
    return frac_wrapped @ lattice_angstrom.T


def _cell_edges(
    lattice_angstrom: np.ndarray,
    dim: int = 3,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Edges of the cell spanned by the first ``dim`` lattice vectors.

    Returns ``(p1, p2)`` pairs of corner positions in Å, with the cell origin at
    the world origin. ``dim=3`` gives the familiar 12-edge parallelepiped;
    ``dim=2`` gives the 4-edge in-plane parallelogram; ``dim=1`` a single edge.

    Columns beyond ``dim`` are **non-physical** — vibe-qc synthesizes them so
    the lattice stays full-rank for AO integrals and spglib, and the SCF energy
    is invariant to them. Drawing them would render a 2D slab as a sheet inside
    a phantom box.

    LATTICE CONVENTION (reaction.path): the binary ``lattice`` member stores
    vectors as **COLUMNS** (``lattice[:, 0]`` = a) — raw
    ``PeriodicSystem.lattice``, no transpose. This is the OPPOSITE of the
    ``structure`` section, which stores ROWS (see
    renderers/structure.py::_draw_unit_cell). Both are correct for their own
    source; never feed one renderer the other's lattice without transposing.
    """
    n = max(0, min(int(dim), 3))
    if n == 0:
        return []
    vecs = [lattice_angstrom[:, i] for i in range(n)]

    # Corners are every subset-sum of the periodic vectors; an edge joins two
    # corners differing by exactly one vector.
    corners = [
        sum((vecs[k] for k in range(n) if mask & (1 << k)), np.zeros(3))
        for mask in range(1 << n)
    ]
    edge_pairs = [
        (mask, mask | (1 << k))
        for mask in range(1 << n)
        for k in range(n)
        if not (mask & (1 << k))
    ]
    return [(corners[i], corners[j]) for i, j in edge_pairs]


class ReactionPathRenderer(BaseRenderer):
    """Renderer for the self-contained `reaction.path` kind."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: ReactionPathData | None = None
        self._frame_pngs: list[bytes] | None = None  # pre-rendered (A7-04)

    def load(self) -> ReactionPathData:
        if self._data is None:
            self._data = self.reader.read_reaction_path(self.section_id)
        return self._data

    @property
    def n_frames(self) -> int:
        return self.load().coords.shape[0]

    @property
    def n_atoms(self) -> int:
        return len(self.load().atoms)

    @property
    def has_cell(self) -> bool:
        """True iff this is a v2 periodic reaction.path archive."""
        return self.load().lattice is not None

    @property
    def dim(self) -> int | None:
        """Shared periodic dimensionality (1 / 2 / 3) for v2 archives;
        None for molecular (v1) paths *and* for variable-dim paths
        (which carry per-frame dims — use :meth:`dim_for_frame`). The
        slab convention uses ``dim=2`` with a + b in-plane and c a
        synthesized, non-physical column."""
        return self.load().dim

    def dim_for_frame(self, index: int) -> int | None:
        """Periodic dimensionality at frame ``index``.

        Honours ``dim_per_frame`` (variable-dim paths, forward-compat
        with variable-cell scans) when present, otherwise the shared
        ``dim``. None for molecular paths. This is what wrapping must
        key off — keying off the scalar :attr:`dim` alone silently
        mis-wraps variable-dim frames as ``dim=3``.
        """
        data = self.load()
        if data.dim_per_frame is not None:
            return int(data.dim_per_frame[index])
        if data.dim is not None:
            return int(data.dim)
        return None

    def cell_for_frame(self, index: int) -> np.ndarray | None:
        """Per-frame 3x3 lattice matrix in **Ångström**, columns = a,
        b, c. None for molecular reaction paths.

        v2 archives store lattices as either shape ``(3, 3)`` (shared
        across frames — the fixed-cell common case) or shape
        ``(n_frames, 3, 3)`` (per-frame, forward-compat with
        variable-cell scans). The renderer normalises both into the
        per-frame access pattern callers expect.
        """
        lattice = self.load().lattice
        if lattice is None:
            return None
        arr = np.asarray(lattice, dtype=np.float64)
        if arr.ndim == 2:
            return arr * _BOHR_TO_ANGSTROM
        if arr.ndim == 3:
            return arr[index] * _BOHR_TO_ANGSTROM
        raise ValueError(
            f"reaction.path: lattice has unexpected ndim={arr.ndim} "
            f"(expected 2 or 3)"
        )

    def get_frame(self, index: int) -> list[tuple[str, np.ndarray]]:
        """Return the atoms at frame ``index`` as ``(symbol, position)``
        tuples in Å.

        Periodic reaction paths additionally wrap atom positions into
        the central cell along the first :attr:`dim` lattice vectors
        — for a slab (``dim=2``) that wraps a + b and leaves the
        non-physical column c untouched. Molecular reaction paths
        return positions verbatim.
        """
        data = self.load()
        coords = data.coords[index]
        if not self.has_cell:
            return [
                (data.atoms[i].symbol, coords[i].copy())
                for i in range(self.n_atoms)
            ]

        lattice = self.cell_for_frame(index)
        assert lattice is not None  # guarded by has_cell
        wrap_axes = int(self.dim_for_frame(index) or 3)
        wrap_axes = max(0, min(3, wrap_axes))
        wrapped = _wrap_positions(coords, lattice, wrap_axes)
        return [
            (data.atoms[i].symbol, wrapped[i])
            for i in range(self.n_atoms)
        ]

    def cell_edges_for_frame(
        self, index: int
    ) -> list[tuple[np.ndarray, np.ndarray]] | None:
        """Return the edges of the unit cell at frame ``index`` (each edge as a
        ``(p1, p2)`` pair of corner positions in Å), or None for molecular
        reaction paths.

        Only the first ``dim`` lattice vectors are drawn: 12 edges for a bulk
        crystal, 4 for a slab, 1 for a polymer. The caller draws these as lines
        in the 3D scene. The cell is re-emitted per frame so variable-cell paths
        (forward-compat) animate the box too; for the fixed-cell common case
        every frame returns the same edges.
        """
        lattice = self.cell_for_frame(index)
        if lattice is None:
            return None
        dim = self.dim_for_frame(index)
        return _cell_edges(lattice, 3 if dim is None else dim)

    def has_energies(self) -> bool:
        data = self.load()
        return data.energies is not None and len(data.energies) > 0

    # ── per-frame volumes (W1) ────────────────────────────────────────

    @property
    def has_volumes(self) -> bool:
        """True iff this archive carries per-frame volumetric data."""
        data = self.load()
        return data.frame_volumes is not None and data.volume_grid is not None

    def volume_label(self) -> str | None:
        return self.load().volume_label

    def volume_isovalue(self) -> float | None:
        return self.load().volume_isovalue

    def volume_grid(self):  # -> GridData | None
        """Shared real-space grid (bohr) for the per-frame volumes."""
        return self.load().volume_grid

    def volume_data_for_frame(self, index: int) -> np.ndarray | None:
        """Return the 3D scalar field [nx, ny, nz] to display at path
        frame ``index``, or None if this archive has no volumes.

        Volumes are decimated (a cube every Nth frame), so for a frame
        between two emitted slabs we hold the most recent emitted slab
        at or before ``index`` — the field visibly updates when the next
        emitted frame is reached, rather than flickering to blank. For
        frames before the first emitted slab we show the first one.
        """
        data = self.load()
        if data.frame_volumes is None:
            return None
        vfi = data.volume_frame_index or list(range(data.frame_volumes.shape[0]))
        # Largest emitted-slab position whose frame index <= `index`.
        slab = 0
        for slab_pos, frame_idx in enumerate(vfi):
            if frame_idx <= index:
                slab = slab_pos
            else:
                break
        return np.asarray(data.frame_volumes[slab])

    def volume_mesh_for_frame(
        self,
        index: int,
        isovalue: float | None = None,
    ):  # -> pv.PolyData | None
        """Marching-cubes isosurface of the density at path frame
        ``index``, ready to add to the 3D scene. None if this archive
        has no volumes. ``isovalue`` defaults to the archive's stored
        hint, else 0.02 e/bohr³ (a reasonable density isosurface)."""
        data = self.load()
        if data.frame_volumes is None or data.volume_grid is None:
            return None
        from vibeview.renderers.volume import build_isosurface_mesh

        field = self.volume_data_for_frame(index)
        iso = (
            isovalue
            if isovalue is not None
            else (data.volume_isovalue if data.volume_isovalue is not None else 0.02)
        )
        return build_isosurface_mesh(field, data.volume_grid, float(iso))

    def waypoints(self) -> list[ReactionWaypoint]:
        return self.load().waypoints

    def waypoint_for_frame(self, frame: int) -> ReactionWaypoint | None:
        for wp in self.waypoints():
            if wp.frame_index == frame:
                return wp
        return None

    def _has_reaction_coordinate(self) -> bool:
        data = self.load()
        n = len(data.energies or [])
        return (
            data.reaction_coordinate is not None
            and len(data.reaction_coordinate) == n
        )

    def energy_plot_x_label(self) -> str:
        """X-axis label for the energy plot.

        Uses the coordinate label + unit when the writer supplied them
        (e.g. ``"bond 0–1 (bohr)"``), else the generic ``"Reaction
        coordinate"`` when per-frame coordinate values exist, else
        ``"Frame"``. Exposed as a method so it's unit-testable without
        rasterising the plot.
        """
        data = self.load()
        if not self._has_reaction_coordinate():
            return "Frame"
        if data.reaction_coordinate_label:
            if data.reaction_coordinate_unit:
                return (
                    f"{data.reaction_coordinate_label} "
                    f"({data.reaction_coordinate_unit})"
                )
            return data.reaction_coordinate_label
        return "Reaction coordinate"

    def render_energy_plot(self, current_frame: int) -> bytes:
        """Return a pre-rendered PNG of the energy plot for ``current_frame``.

        All N frame variants are rendered once and cached on first call
        (A7-04); subsequent calls are O(1) lookups.
        """
        data = self.load()
        if not data.energies:
            return b""
        if self._frame_pngs is None:
            self._frame_pngs = self._prerender_all_frames(data)
        n = len(self._frame_pngs)
        if n == 0:
            return b""
        idx = max(0, min(current_frame, n - 1))
        return self._frame_pngs[idx]

    def _prerender_all_frames(self, data) -> list[bytes]:
        """Render one PNG per frame (frame indicator moves; everything else fixed)."""
        if not data.energies:
            return []
        energies = np.array(data.energies, dtype=np.float64)
        n = len(energies)
        if n == 0:
            return []

        if self._has_reaction_coordinate():
            assert data.reaction_coordinate is not None
            xs = np.array(data.reaction_coordinate, dtype=np.float64)
        else:
            xs = np.arange(n)
        x_label = self.energy_plot_x_label()

        pngs: list[bytes] = []
        for frame in range(n):
            fig, ax = plt.subplots(figsize=(6, 2.8))
            ax.plot(xs, energies, "-", color="#3366CC", linewidth=1.0, zorder=1)
            ax.plot(xs, energies, "o", color="#3366CC", markersize=3, zorder=2)

            for wp in data.waypoints:
                if not 0 <= wp.frame_index < n:
                    continue
                color = _KIND_COLORS.get(wp.kind, "#444444")
                ax.plot(
                    xs[wp.frame_index], energies[wp.frame_index],
                    marker="*", color=color, markersize=14,
                    markeredgecolor="black", markeredgewidth=0.5, zorder=4,
                )
                ax.annotate(
                    wp.label,
                    xy=(xs[wp.frame_index], energies[wp.frame_index]),
                    xytext=(6, 8), textcoords="offset points",
                    fontsize=8, color=color,
                )

            ax.plot(
                xs[frame], energies[frame], "o",
                color="#CC3333", markersize=8, zorder=5,
            )
            ax.set_xlabel(x_label)
            ax.set_ylabel("Energy (a.u.)")
            ax.grid(True, alpha=0.3)
            fig.tight_layout()

            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=100)
            plt.close(fig)
            buf.seek(0)
            pngs.append(buf.read())
        return pngs


class ReactionWaypointsRenderer(BaseRenderer):
    """Renderer for the `reaction.waypoints` annotation kind.

    Does not own frames — it points at a referenced `trajectory` section
    via `trajectory_ref`. The app layers the waypoint markers onto the
    referenced trajectory's energy plot.
    """

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: ReactionWaypointsData | None = None

    def load(self) -> ReactionWaypointsData:
        if self._data is None:
            self._data = self.reader.read_reaction_waypoints(self.section_id)
        return self._data

    @property
    def trajectory_ref(self) -> str:
        return self.load().trajectory_ref

    def waypoints(self) -> list[ReactionWaypoint]:
        return self.load().waypoints
