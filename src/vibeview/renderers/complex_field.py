"""Complex-field renderer for the ``volume.complex`` kind (#157).

A ``volume.complex`` section holds a complex scalar on a grid, such as a
complex molecular orbital or a Bloch function at one k-point (QVF spec
§ 5.2). One of three parts is drawn, the choices the browser offers for
complex orbitals it evaluates itself:

* ``real`` (default) and ``imag``: a signed field, drawn as a ± lobe pair in
  the shared phase colours.
* ``modulus``: one |ψ| surface coloured by the phase arg ψ in (−π, π] with a
  cyclic colour map. Re and Im are interpolated onto the surface and the
  phase is taken per vertex, so it is never interpolated across the ±π wrap.

The opening isovalue is the section's hint (0.05 by default). When the field
never reaches it, the view opens at half the largest |ψ| instead, so a small
field does not open empty (the trap #119 fixed for transition densities).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    import pyvista as pv

    from vibeview.qvf import ComplexFieldData, QVFReader, Section

#: Actor-name prefix of the surfaces (``vibeview.app.scene._Actors.COMPLEX``).
COMPLEX_PREFIX = "complex_field_"
#: The parts that can be drawn, in the browser's order.
PARTS = ("real", "imag", "modulus")
#: The cyclic colour map the phase is drawn in.
PHASE_COLORMAP = "twilight"
_PART_NAMES = {"real": "real part", "imag": "imaginary part", "modulus": "|ψ| coloured by phase"}


def _check_part(part: str) -> str:
    if part not in PARTS:
        raise ValueError(f"part must be one of {', '.join(PARTS)}, not {part!r}")
    return part


def part_field(values: np.ndarray, part: str) -> np.ndarray:
    """The real array ``part`` draws: Re ψ, Im ψ or |ψ|."""
    name = _check_part(part)
    if name == "real":
        return np.asarray(values.real, dtype=float)
    if name == "imag":
        return np.asarray(values.imag, dtype=float)
    return np.abs(values).astype(float)


def max_modulus(data: ComplexFieldData) -> float:
    """The largest finite |ψ| of the field, 0.0 for an empty or all-NaN one."""
    modulus = np.abs(data.values)
    finite = modulus[np.isfinite(modulus)]
    return float(finite.max()) if finite.size else 0.0


def opening_isovalue(data: ComplexFieldData, hinted: float) -> float:
    """``hinted``, or half the largest |ψ| when the field never reaches it."""
    peak = max_modulus(data)
    if peak > 0 and hinted >= peak:
        return 0.5 * peak
    return hinted


def complex_meshes(
    data: ComplexFieldData, part: str, isovalue: float
) -> dict[str, pv.PolyData]:
    """The surfaces for ``part`` at ``isovalue``, keyed ``pos``/``neg`` or ``modulus``.

    A signed part contours +isovalue on the field and on its negation; the
    modulus surface carries a ``phase`` point array in radians. Empty
    contours are left out, so an isovalue above the field returns ``{}``.
    """
    from vibeview.renderers.volume import build_isosurface_mesh

    name = _check_part(part)
    if not math.isfinite(isovalue) or isovalue <= 0:
        raise ValueError("isovalue must be finite and positive")
    meshes: dict[str, pv.PolyData] = {}
    if name == "modulus":
        mesh = build_isosurface_mesh(
            part_field(data.values, "modulus"), data.grid, isovalue,
            extra_point_arrays={
                "re": np.asarray(data.values.real, dtype=float),
                "im": np.asarray(data.values.imag, dtype=float),
            },
        )
        if mesh is not None and mesh.n_points:
            mesh["phase"] = np.arctan2(np.asarray(mesh["im"]), np.asarray(mesh["re"]))
            meshes["modulus"] = mesh
        return meshes
    field = part_field(data.values, name)
    for key, signed in (("pos", field), ("neg", -field)):
        mesh = build_isosurface_mesh(signed, data.grid, isovalue)
        if mesh is not None and mesh.n_points:
            meshes[key] = mesh
    return meshes


def add_complex_field(
    plotter: pv.Plotter,
    data: ComplexFieldData,
    part: str,
    isovalue: float,
    *,
    opacity: float = 0.6,
    name: str = COMPLEX_PREFIX + "surface",
) -> int:
    """Draw ``part`` at ``isovalue`` into ``plotter``; return the surfaces drawn."""
    from vibeview.headless import add_signed_isosurfaces
    from vibeview.plotter_compat import cmap_argument

    meshes = complex_meshes(data, part, isovalue)
    if part == "modulus":
        mesh = meshes.get("modulus")
        if mesh is None:
            return 0
        plotter.add_mesh(
            mesh,
            scalars="phase",
            cmap=cmap_argument(PHASE_COLORMAP),
            clim=(-math.pi, math.pi),
            opacity=opacity,
            name=f"{name}_modulus",
            show_scalar_bar=False,
        )
        return 1
    add_signed_isosurfaces(
        plotter, meshes.get("pos"), meshes.get("neg"), opacity=opacity, name=name
    )
    return len(meshes)


def describe(data: ComplexFieldData, part: str, isovalue: float, drawn: int) -> str:
    """``real part at 0.024 (largest |ψ| 0.048)`` for a status line."""
    unit = f" {data.unit}" if data.unit else ""
    peak = f"largest |ψ| {max_modulus(data):.3g}{unit}"
    if not drawn:
        return f"no {_PART_NAMES[part]} surface at {isovalue:.3g}; {peak}, lower the isovalue"
    return f"{_PART_NAMES[part]} at {isovalue:.3g}{unit} ({peak})"


class ComplexFieldRenderer(BaseRenderer):
    """Real, imaginary or phase-coloured modulus surfaces of a ``volume.complex`` section."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: ComplexFieldData | None = None

    def load(self) -> ComplexFieldData:
        if self._data is None:
            self._data = self.reader.read_volume_complex(self.section_id)
        return self._data
