"""Headless capture API — render QVF sections to PNG without a browser.

Provides per-kind capture functions that reuse the existing renderer
classes outside the interactive Trame event loop.  All functions use
``pyvista.Plotter(off_screen=True)`` so they work without a display
server (headless / CI / scripted).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from vibeview.qvf import clamp_replication

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader


def _default_plotter(size=(900, 600), background="#1a1a2e"):
    """Create a headless PyVista plotter with the vibe-view dark theme."""
    import pyvista as pv

    p = pv.Plotter(off_screen=True, window_size=[size[0], size[1]])
    p.set_background(background)
    return p


def capture_structure(
    reader: QVFReader,
    path: str | Path,
    size=(900, 600),
    representation="ball_and_stick",
    show_labels=False,
    replication=(1, 1, 1),
) -> bool:
    """Render the structure section to a PNG file.

    Returns True on success, False if no structure section exists.
    """
    import pyvista as pv

    from vibeview.renderers.structure import StructureRenderer

    section = next((s for s in reader.sections if s.kind == "structure"), None)
    if section is None:
        return False

    plotter = _default_plotter(size)
    try:
        StructureRenderer(section, reader).add_to_plotter(
            plotter,
            replication=replication,
            show_labels=show_labels,
            representation=representation,
        )
        plotter.view_isometric()
        plotter.reset_camera()
        plotter.screenshot(str(path))
    finally:
        plotter.close()
    return True


def capture_volume(
    reader: QVFReader,
    section_id: str,
    path: str | Path,
    isovalue=0.05,
    colormap="viridis",
    opacity=0.6,
    size=(900, 600),
    replication=(1, 1, 1),
) -> bool:
    """Render a volume isosurface to a PNG file.

    ``section_id`` must refer to a ``volume.*`` or ``basis.ao`` section.
    """
    import numpy as np
    import pyvista as pv

    from vibeview.renderers.volume import (
        _BOHR_TO_ANGSTROM,
        VolumeRenderer,
        _replicate_mesh,
        build_isosurface_mesh,
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
        grid = renderer.load_grid()
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

    plotter = _default_plotter(size)
    try:
        # Also draw the structure for context.
        from vibeview.renderers.structure import StructureRenderer

        struct_sec = next((s for s in reader.sections if s.kind == "structure"), None)
        if struct_sec is not None:
            StructureRenderer(struct_sec, reader).add_to_plotter(plotter, replication=replication)

        if _is_signed:
            pos_data = np.clip(data, 0, None)
            neg_data = np.clip(-data, 0, None)
            for d, color, suffix in [
                (pos_data, "#3366cc", "_pos"),
                (neg_data, "#cc3333", "_neg"),
            ]:
                mesh = _build_contour(renderer, d, isovalue, replication, lattice)
                if mesh is not None and mesh.n_points:
                    plotter.add_mesh(
                        mesh,
                        color=color,
                        opacity=opacity,
                        name=f"capture{suffix}",
                        show_scalar_bar=False,
                    )
        else:
            mesh = renderer.make_mesh(hints, replication=replication, lattice_vectors=lattice)
            if mesh is not None and mesh.n_points:
                plotter.add_mesh(
                    mesh,
                    scalars="values",
                    cmap=colormap,
                    opacity=opacity,
                    name="capture_vol",
                    show_scalar_bar=False,
                )
        plotter.view_isometric()
        plotter.reset_camera()
        plotter.screenshot(str(path))
    finally:
        plotter.close()
    return True


def _build_contour(renderer, data, isovalue, replication, lattice):
    """Build a signed contour mesh, replicating for periodic systems."""
    import numpy as np
    import pyvista as pv

    from vibeview.renderers.volume import _BOHR_TO_ANGSTROM, _replicate_mesh

    grid = renderer.load_grid()
    shape = grid.shape
    voxel_vecs = grid.voxel_vectors * _BOHR_TO_ANGSTROM
    origin_ang = grid.origin * _BOHR_TO_ANGSTROM

    is_orthogonal = all(abs(voxel_vecs[i, j]) < 1e-10 for i in range(3) for j in range(3) if i != j)
    if is_orthogonal:
        spacing = tuple(float(voxel_vecs[i, i]) for i in range(3))
        mesh = pv.ImageData(
            dimensions=shape,
            spacing=spacing,
            origin=tuple(float(origin_ang[i]) for i in range(3)),
        )
        mesh.point_data["values"] = data.ravel(order="F")
        contour = mesh.contour(isosurfaces=[isovalue], scalars="values")
    else:
        ii, jj, kk = np.meshgrid(
            np.arange(shape[0], dtype=np.float64),
            np.arange(shape[1], dtype=np.float64),
            np.arange(shape[2], dtype=np.float64),
            indexing="ij",
        )
        pts_x = (
            origin_ang[0] + ii * voxel_vecs[0, 0] + jj * voxel_vecs[1, 0] + kk * voxel_vecs[2, 0]
        )
        pts_y = (
            origin_ang[1] + ii * voxel_vecs[0, 1] + jj * voxel_vecs[1, 1] + kk * voxel_vecs[2, 1]
        )
        pts_z = (
            origin_ang[2] + ii * voxel_vecs[0, 2] + jj * voxel_vecs[1, 2] + kk * voxel_vecs[2, 2]
        )
        sgrid = pv.StructuredGrid(pts_x, pts_y, pts_z)
        sgrid.point_data["values"] = data.ravel(order="F")
        contour = sgrid.contour(isosurfaces=[isovalue], scalars="values")

    if contour.n_points == 0:
        return None
    if lattice is not None and any(r > 1 for r in replication):
        contour = _replicate_mesh(contour, lattice, replication)
    return contour


def capture_bands(
    reader: QVFReader,
    path: str | Path,
    section_id: str | None = None,
) -> bool:
    """Render a band structure chart to a PNG file."""
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
    png = renderer.render_to_bytes()
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
) -> bool:
    """Render a DOS chart to an HTML file."""
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
    html = renderer.render_to_html()
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


def capture_selftest(output: "str | Path | None" = None, *, size=(400, 300)) -> dict:
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
    out = Path(output) if output else Path(tempfile.mkdtemp(prefix="vibeview-selftest-")) / "selftest.png"
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
