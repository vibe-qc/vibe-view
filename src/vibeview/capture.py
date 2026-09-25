"""Headless capture API — render QVF sections to PNG without a browser.

Provides per-kind capture functions that reuse the existing renderer
classes outside the interactive Trame event loop.  All functions use
``pyvista.Plotter(off_screen=True)`` so they work without a display
server (headless / CI / scripted).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from vibeview.headless import (
    add_signed_isosurfaces,
    add_structure,
    frame,
    headless_plotter,
    render_structure_frame,
    snapshot,
)
from vibeview.plotter_compat import cmap_argument
from vibeview.qvf import clamp_replication

if TYPE_CHECKING:
    import numpy as np
    import pyvista as pv

    from vibeview.qvf import QVFReader
    from vibeview.renderers.volume import VolumeRenderer


def capture_structure(
    reader: QVFReader,
    path: str | Path,
    size: tuple[int, int] = (900, 600),
    representation: str = "ball_and_stick",
    show_labels: bool = False,
    replication: tuple[int, int, int] = (1, 1, 1),
) -> bool:
    """Render the structure section to a PNG file.

    Returns True on success, False if no structure section exists.
    """
    return bool(
        render_structure_frame(
            reader,
            path,
            size=size,
            representation=representation,
            show_labels=show_labels,
            replication=replication,
        )
    )


def capture_volume(
    reader: QVFReader,
    section_id: str,
    path: str | Path,
    isovalue: float = 0.05,
    colormap: str = "viridis",
    opacity: float = 0.6,
    size: tuple[int, int] = (900, 600),
    replication: tuple[int, int, int] = (1, 1, 1),
) -> bool:
    """Render a volume isosurface to a PNG file.

    ``section_id`` must refer to a ``volume.*`` or ``basis.ao`` section.
    """
    import numpy as np

    from vibeview.renderers.volume import (
        VolumeRenderer,
    )
    from vibeview.viewer_defaults import VolumeHints

    try:
        section = reader.get_section(section_id)
    except Exception:
        return False
    if section is None:
        return False

    renderer = VolumeRenderer(section, reader)
    try:
        renderer.load_grid()  # availability probe; the grid is re-read below
        data = renderer.load_data()
    except Exception:
        return False

    hints = VolumeHints(isovalue=isovalue, colormap=colormap, opacity=opacity)
    lattice = None
    try:
        sdata = reader.read_structure()
        if sdata.lattice_vectors is not None and any(sdata.pbc):
            lattice = sdata.lattice_vectors
        # Tile the isosurface only along periodic axes; a slab's synthesized
        # normal is not a repeat vector, so tiling it smears the density into
        # the vacuum.
        replication = clamp_replication(replication, sdata.pbc)
    except Exception:
        pass

    _is_signed = section.kind in (
        "volume.difference",
        "volume.spin",
        "volume.orbital",
        "volume.potential",
        "basis.ao",
    )

    with headless_plotter(size) as plotter:
        # Also draw the structure for context.
        add_structure(plotter, reader, replication=replication)

        if _is_signed:
            add_signed_isosurfaces(
                plotter,
                _build_contour(renderer, np.clip(data, 0, None), isovalue, replication, lattice),
                _build_contour(renderer, np.clip(-data, 0, None), isovalue, replication, lattice),
                opacity=opacity,
                name="capture",
            )
        else:
            mesh = renderer.make_mesh(hints, replication=replication, lattice_vectors=lattice)
            if mesh is not None and mesh.n_points:
                plotter.add_mesh(
                    mesh,
                    scalars="values",
                    cmap=cmap_argument(colormap),
                    opacity=opacity,
                    name="capture_vol",
                    show_scalar_bar=False,
                )
        frame(plotter)
        snapshot(plotter, path)
    return True


def _build_contour(
    renderer: VolumeRenderer,
    data: np.ndarray,
    isovalue: float,
    replication: tuple[int, int, int],
    lattice: np.ndarray | None,
) -> pv.PolyData | None:
    """Build a signed surface using the shared QVF grid geometry."""
    from vibeview.renderers.volume import build_isosurface_mesh

    contour = build_isosurface_mesh(
        data,
        renderer.load_grid(),
        isovalue,
        replication=replication,
        lattice_vectors=lattice,
    )
    return contour if contour is not None and contour.n_points else None


def capture_bands(
    reader: QVFReader,
    path: str | Path,
    section_id: str | None = None,
    *,
    energy_window: tuple[float, float] | None = None,
    auto_window: bool = True,
) -> bool:
    """Render a band structure chart to a PNG file.

    ``energy_window`` limits the energy axis to ``(min, max)`` in eV
    relative to E_F; unset, the figure opens on the same valence window
    the viewer uses, and ``auto_window=False`` restores the full
    autoscale (#26).
    """
    from vibeview.renderers.bands import BandsRenderer

    sid = section_id
    if sid is None:
        sec = next((s for s in reader.sections if s.kind == "bands"), None)
        if sec is None:
            return False
        sid = sec.id
    try:
        section = reader.get_section(sid)
    except Exception:
        return False
    if section is None:
        return False

    renderer = BandsRenderer(section, reader)
    png = renderer.render_to_bytes(energy_window=energy_window, auto_window=auto_window)
    Path(path).write_bytes(png)
    return True


def capture_bond_orders(
    reader: QVFReader,
    path: str | Path,
    section_id: str | None = None,
) -> bool:
    """Render the bond-orders table as an HTML file (viewable in browser)."""
    from vibeview.renderers.bond_orders import BondOrdersRenderer

    sid = section_id
    if sid is None:
        sec = next((s for s in reader.sections if s.kind == "bond_orders"), None)
        if sec is None:
            return False
        sid = sec.id
    try:
        section = reader.get_section(sid)
    except Exception:
        return False
    if section is None:
        return False

    renderer = BondOrdersRenderer(section, reader)
    html = renderer.render_to_html()
    Path(path).write_text(html)
    return True


def capture_dos(
    reader: QVFReader,
    path: str | Path,
    section_id: str | None = None,
    *,
    energy_window: tuple[float, float] | None = None,
    auto_window: bool = True,
) -> bool:
    """Render a DOS chart to an HTML file.

    Takes the same ``energy_window`` / ``auto_window`` arguments as
    :func:`capture_bands` (#26).
    """
    from vibeview.renderers.dos import DOSRenderer

    sid = section_id
    if sid is None:
        sec = next((s for s in reader.sections if s.kind in ("dos.total", "dos.projected")), None)
        if sec is None:
            return False
        sid = sec.id
    try:
        section = reader.get_section(sid)
    except Exception:
        return False
    if section is None:
        return False

    renderer = DOSRenderer(section, reader)
    html = renderer.render_to_html(energy_window=energy_window, auto_window=auto_window)
    Path(path).write_text(html)
    return True


def capture_spectra(
    reader: QVFReader,
    path: str | Path,
    section_id: str | None = None,
) -> bool:
    """Render a spectrum to an HTML file."""
    from vibeview.renderers.spectra import SpectraRenderer

    sid = section_id
    if sid is None:
        sec = next(
            (s for s in reader.sections if s.kind.startswith("spectra.")),
            None,
        )
        if sec is None:
            return False
        sid = sec.id
    try:
        section = reader.get_section(sid)
    except Exception:
        return False
    if section is None:
        return False

    renderer = SpectraRenderer(section, reader)
    html = renderer.render_to_html()
    Path(path).write_text(html)
    return True


def capture_scf_history(
    reader: QVFReader,
    path: str | Path,
    section_id: str | None = None,
) -> bool:
    """Render SCF convergence history to an HTML file."""
    from vibeview.renderers.scf_history import SCFHistoryRenderer

    sid = section_id
    if sid is None:
        sec = next((s for s in reader.sections if s.kind == "scf_history"), None)
        if sec is None:
            return False
        sid = sec.id
    try:
        section = reader.get_section(sid)
    except Exception:
        return False
    if section is None:
        return False

    renderer = SCFHistoryRenderer(section, reader)
    html = renderer.render_to_html()
    Path(path).write_text(html)
    return True


def capture_energy_diagram(
    reader: QVFReader,
    path: str | Path,
    section_id: str | None = None,
) -> bool:
    """Render the orbital energy diagram to an HTML file."""
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    sid = section_id
    if sid is None:
        sec = next((s for s in reader.sections if s.kind == "wavefunction.gto"), None)
        if sec is None:
            return False
        sid = sec.id
    try:
        section = reader.get_section(sid)
    except Exception:
        return False
    if section is None:
        return False

    renderer = WavefunctionRenderer(section, reader)
    html = renderer.render_energy_diagram()
    Path(path).write_text(html)
    return True


def capture_selftest(
    output: str | Path | None = None, *, size: tuple[int, int] = (400, 300)
) -> dict[str, Any]:
    """Validate the headless screenshot-capture stack on this host.

    Builds a tiny water QVF in memory (no external files), renders its
    structure section offscreen to a PNG, and returns backend diagnostics.
    Raises on any failure. This is the environment healthcheck for
    docs-artifact / queue jobs: if it returns cleanly, the ``capture_*``
    functions will work on the host; if the host lacks offscreen GL
    (no OSMesa and no X / xvfb), the render fails here with a clear error
    rather than deep inside an example run.
    """
    import tempfile

    import pyvista as pv

    from vibeview.converters import xyz_to_qvf
    from vibeview.qvf import QVFReader

    xyz = (
        "3\nvibe-view capture selftest\n"
        "O  0.000  0.000  0.117\n"
        "H  0.000  0.757 -0.467\n"
        "H  0.000 -0.757 -0.467\n"
    )
    out = (
        Path(output)
        if output
        else Path(tempfile.mkdtemp(prefix="vibeview-selftest-")) / "selftest.png"
    )
    reader = QVFReader(xyz_to_qvf(xyz.encode()))
    try:
        ok = capture_structure(reader, str(out), size=size)
    finally:
        reader.close()
    if not ok or not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(f"offscreen render produced no output at {out}")

    try:
        import vtkmodules.vtkCommonCore as _vc

        vtk_version = _vc.vtkVersion.GetVTKVersion()
    except Exception:
        vtk_version = "unknown"
    return {
        "pyvista": pv.__version__,
        "vtk": vtk_version,
        "off_screen_default": bool(pv.OFF_SCREEN),
        "output": str(out),
        "bytes": out.stat().st_size,
    }
