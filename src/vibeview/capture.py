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
    add_volume_render,
    apply_publication_look,
    frame,
    headless_plotter,
    render_structure_frame,
    snapshot,
)
from vibeview.kinds import SIGNED_VOLUME_KINDS
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
    publication: bool = False,
    atom_color_mode: str = "element",
    cutaway: str = "off",
    cutaway_position: float = 0.5,
) -> bool:
    """Render the structure section to a PNG file.

    Returns True on success, False if no structure section exists.
    ``publication`` gives the browser's Publication view preset: the
    scientific material on a near-white background with outlined atoms and
    bonds (#68).
    """
    return bool(
        render_structure_frame(
            reader,
            path,
            size=size,
            representation=representation,
            show_labels=show_labels,
            replication=replication,
            publication=publication,
            atom_color_mode=atom_color_mode, cutaway=cutaway, cutaway_position=cutaway_position,
        )
    )


class CaptureError(Exception):
    """A section could not be captured; the message says why."""


#: How :func:`render_volume` draws a section: ``"auto"`` follows the section's
#: ``render`` hint (the isosurface when it has none), the other two force it.
RENDER_CHOICES: tuple[str, ...] = ("auto", "isosurface", "volume")


def capture_volume(
    reader: QVFReader,
    section_id: str,
    path: str | Path,
    isovalue: float = 0.05,
    colormap: str = "viridis",
    opacity: float = 0.6,
    size: tuple[int, int] = (900, 600),
    replication: tuple[int, int, int] = (1, 1, 1),
    render: str = "auto",
    transfer_function: dict[str, Any] | None = None,
    publication: bool = False,
) -> bool:
    """Render a volume section to a PNG file, as an isosurface or a volume.

    ``section_id`` must refer to a ``volume.*`` or ``basis.ao`` section.
    ``render`` is one of :data:`RENDER_CHOICES`; ``"auto"`` follows the
    section's ``render`` hint, so a file that opens in the browser in volume
    mode captures the same way. ``transfer_function`` overrides the
    section's hint in volume mode. ``publication`` gives the browser's
    Publication look, with the atoms and bonds outlined; the isosurface is
    not, since its outline is drawn by the browser's silhouette pass (#112).
    Returns False when the section cannot be read or drawn;
    :func:`render_volume` raises with the reason instead.
    """
    try:
        render_volume(
            reader,
            section_id,
            path,
            isovalue=isovalue,
            colormap=colormap,
            opacity=opacity,
            size=size,
            replication=replication,
            render=render,
            transfer_function=transfer_function,
            publication=publication,
        )
    except CaptureError:
        return False
    return True


def render_volume(
    reader: QVFReader,
    section_id: str,
    path: str | Path,
    isovalue: float = 0.05,
    colormap: str = "viridis",
    opacity: float = 0.6,
    size: tuple[int, int] = (900, 600),
    replication: tuple[int, int, int] = (1, 1, 1),
    render: str = "auto",
    transfer_function: dict[str, Any] | None = None,
    publication: bool = False,
) -> None:
    """:func:`capture_volume`, raising :class:`CaptureError` with the reason.

    A section that is missing or whose grid or data cannot be read raises
    ``CaptureError`` naming the section and the underlying error, which
    is chained as its cause (#60). In volume mode a grid the payload refuses
    (skewed, empty, no finite samples) raises ``CaptureError`` with the
    payload's reason; replication and clipping apply to the isosurface only.
    """
    import numpy as np

    from vibeview.renderers.volume import (
        VolumeRenderer,
    )
    from vibeview.viewer_defaults import VolumeHints, section_volume_hints

    if render not in RENDER_CHOICES:
        raise CaptureError(f"render must be one of {', '.join(RENDER_CHOICES)}, not {render!r}")

    try:
        section = reader.get_section(section_id)
    except Exception as exc:
        raise CaptureError(f"section {section_id!r}: {exc}") from exc
    if section is None:
        raise CaptureError(f"section {section_id!r} not found")

    renderer = VolumeRenderer(section, reader)
    try:
        renderer.load_grid()  # availability probe; the grid is re-read below
        data = renderer.load_data()
    except Exception as exc:
        raise CaptureError(f"cannot read section {section_id!r}: {exc}") from exc

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

    # ``data`` is the stored array as-is: a section's ``component`` is a
    # label, never a transform (stored_volume_field; #49, #38-19).
    _is_signed = section.kind in SIGNED_VOLUME_KINDS

    mode = render
    hinted = None
    if render == "auto" or transfer_function is None:
        hinted = section_volume_hints(reader, section_id)
    if render == "auto" and hinted is not None:
        mode = hinted.render
    if transfer_function is None and hinted is not None:
        transfer_function = hinted.transfer_function

    with headless_plotter(size) as plotter:
        # Also draw the structure for context.
        add_structure(plotter, reader, replication=replication)

        if mode == "volume":
            # The browser's volume mode, off-screen: the same payload, the
            # same transfer function, so figures and the viewer agree.
            from vibeview.renderers.volume_render import VolumeRenderError

            try:
                add_volume_render(
                    plotter,
                    reader,
                    section_id,
                    colormap=colormap,
                    opacity=opacity,
                    transfer_function=transfer_function,
                )
            except VolumeRenderError as exc:
                raise CaptureError(
                    f"cannot draw section {section_id!r} as a volume: {exc}"
                ) from exc
            if publication:
                apply_publication_look(plotter)
            frame(plotter)
            snapshot(plotter, path)
            return

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
        if publication:
            apply_publication_look(plotter)
        frame(plotter)
        snapshot(plotter, path)


def render_bloch_orbital(
    reader: QVFReader,
    section_id: str,
    path: str | Path,
    *,
    part: str = "real",
    isovalue: float | None = None,
    size: tuple[int, int] = (900, 600),
    publication: bool = False,
) -> str:
    """Draw a ``wavefunction.bloch`` section's opening orbital to PNG (#162).

    The highest occupied orbital at the first k-point, as the browser opens
    it; ``part`` and ``isovalue`` as for ``volume.complex``. Raises
    :class:`CaptureError` with the reason; returns the summary line.
    """
    from vibeview.qvf import ComplexFieldData
    from vibeview.renderers.bloch import BlochWavefunctionRenderer, kpoint_label, opening_orbital
    from vibeview.renderers.complex_field import add_complex_field, describe, opening_isovalue

    try:
        section = reader.get_section(section_id)
        if section.kind != "wavefunction.bloch":
            raise CaptureError(
                f"section {section_id!r} is a {section.kind}, not wavefunction.bloch"
            )
        renderer = BlochWavefunctionRenderer(section, reader)
        data = renderer.load()
        orbital = opening_orbital(data)
        grid, values, _dropped = renderer.evaluate(0, 0, orbital, n_per_dim=48)
    except CaptureError:
        raise
    except Exception as exc:
        raise CaptureError(f"cannot read section {section_id!r}: {exc}") from exc
    field = ComplexFieldData(grid=grid, values=values)
    level = isovalue if isovalue is not None else opening_isovalue(field, 0.05)
    with headless_plotter(size) as plotter:
        add_structure(plotter, reader)
        try:
            drawn = add_complex_field(plotter, field, part, level, name="capture_bloch")
        except ValueError as exc:
            raise CaptureError(f"cannot draw section {section_id!r}: {exc}") from exc
        text = (f"orbital {orbital + 1} at k = {kpoint_label(data, 0)}: "
                f"{describe(field, part, level, drawn)}")
        if not drawn:
            raise CaptureError(f"section {section_id!r}: {text}")
        if publication:
            apply_publication_look(plotter)
        frame(plotter)
        snapshot(plotter, path)
    return text


def render_electric_dipole(
    reader: QVFReader,
    section_id: str,
    path: str | Path,
    *,
    size: tuple[int, int] = (900, 600),
    publication: bool = False,
) -> str:
    """Draw a ``properties.electric`` dipole from its origin on the structure (#161).

    The structure is drawn as sticks only: a dipole arrow at the browser's
    scale, 0.3 Å per debye from the centre of mass, would otherwise sit
    inside the atoms. Raises :class:`CaptureError` with the reason; returns
    the summary line.
    """
    import numpy as np

    from vibeview.renderers.palette import DIPOLE_ARROW
    from vibeview.renderers.properties import (
        PropertiesRenderer,
        dipole_anchor,
        dipole_arrow_mesh,
        summary,
    )

    try:
        section = reader.get_section(section_id)
        if section.kind != "properties.electric":
            raise CaptureError(
                f"section {section_id!r} is {section.kind}; "
                f"`vibe-view table FILE --kind {section.kind}` lists it"
            )
        data = PropertiesRenderer(section, reader).load()
        atoms = (
            reader.read_structure(data.structure_id).atoms
            if data.structure_id is not None else []
        )
    except CaptureError:
        raise
    except Exception as exc:
        raise CaptureError(f"cannot read section {section_id!r}: {exc}") from exc
    dipole = data.payload.get("dipole")
    arrow = None if not dipole else dipole_arrow_mesh(
        dipole_anchor(data.payload.get("origin"), list(atoms)),
        np.asarray(dipole["vector"], dtype=float),
    )
    if arrow is None:
        raise CaptureError(
            f"section {section_id!r} has no dipole to draw; "
            "`vibe-view table FILE --kind properties.electric` lists it"
        )
    with headless_plotter(size) as plotter:
        add_structure(plotter, reader, representation="sticks_only")
        plotter.add_mesh(arrow, color=DIPOLE_ARROW, name="capture_dipole", show_scalar_bar=False)
        if publication:
            apply_publication_look(plotter)
        frame(plotter)
        snapshot(plotter, path)
    return summary(data)


def render_forces(
    reader: QVFReader,
    section_id: str,
    path: str | Path,
    *,
    size: tuple[int, int] = (900, 600),
    publication: bool = False,
) -> str:
    """Draw a ``forces`` section's arrows (F = −∇E) on its structure to PNG (#159).

    Raises :class:`CaptureError` with the reason; returns the summary line.
    The arrows are the browser's at its default length.
    """
    import numpy as np

    from vibeview.renderers.derivatives import ForcesRenderer, add_force_arrows, forces_summary
    from vibeview.renderers.structure import atom_display_radius

    try:
        section = reader.get_section(section_id)
        if section.kind != "forces":
            raise CaptureError(f"section {section_id!r} is a {section.kind}, not forces")
        data = ForcesRenderer(section, reader).load()
        atoms = reader.read_structure(data.structure_id).atoms
    except CaptureError:
        raise
    except Exception as exc:
        raise CaptureError(f"cannot read section {section_id!r}: {exc}") from exc
    positions = np.asarray([a.position for a in atoms], dtype=float)
    radii = np.asarray([atom_display_radius(a.atomic_number) for a in atoms])
    with headless_plotter(size) as plotter:
        add_structure(plotter, reader)
        add_force_arrows(plotter, positions, data, atom_radii=radii)
        if publication:
            apply_publication_look(plotter)
        frame(plotter)
        snapshot(plotter, path)
    return forces_summary(data, [a.symbol for a in atoms])


def capture_volume_series(
    reader: QVFReader,
    section_id: str,
    path: str | Path,
    *,
    slab: int = 0,
    isovalue: float | None = None,
    size: tuple[int, int] = (900, 600),
    publication: bool = False,
) -> bool:
    """Render slab ``slab`` of a ``volume.series`` section to PNG (#158).

    Returns False when the section cannot be read or the slab has no
    surface at the isovalue; :func:`render_volume_series` raises instead.
    """
    try:
        render_volume_series(
            reader, section_id, path, slab=slab, isovalue=isovalue, size=size,
            publication=publication,
        )
    except CaptureError:
        return False
    return True


def render_volume_series(
    reader: QVFReader,
    section_id: str,
    path: str | Path,
    *,
    slab: int = 0,
    isovalue: float | None = None,
    size: tuple[int, int] = (900, 600),
    publication: bool = False,
) -> str:
    """:func:`capture_volume_series`, raising :class:`CaptureError`; returns the summary.

    ``slab`` is 0-based (``vibe-view capture --frame``). A slab that belongs
    to a trajectory frame is drawn on that frame's geometry; unset, ``isovalue`` is the browser's
    opening value (:func:`vibeview.renderers.volume_series.opening_isovalue`).
    """
    from vibeview.renderers.volume_series import (
        VolumeSeriesRenderer,
        add_series_slab,
        describe,
        frame_atoms,
        opening_isovalue,
    )
    from vibeview.viewer_defaults import section_volume_hints

    try:
        section = reader.get_section(section_id)
        data = VolumeSeriesRenderer(section, reader).load()
        atoms = frame_atoms(reader, data, slab) if section.kind == "volume.series" else None
    except ValueError as exc:
        raise CaptureError(f"section {section_id!r}: {exc}") from exc
    except Exception as exc:
        raise CaptureError(f"cannot read section {section_id!r}: {exc}") from exc
    if section.kind != "volume.series":
        raise CaptureError(f"section {section_id!r} is a {section.kind}, not a volume.series")
    level = (
        isovalue if isovalue is not None
        else opening_isovalue(data, section_volume_hints(reader, section_id).isovalue)
    )
    with headless_plotter(size) as plotter:
        positions = None
        if atoms is not None:
            try:
                n_atoms = len(reader.read_structure().atoms)
            except Exception:  # noqa: BLE001 — no structure: draw the field alone
                n_atoms = -1
            if n_atoms == len(atoms):
                positions = [position for _symbol, position in atoms]
        add_structure(plotter, reader, positions=positions)
        try:
            drawn = add_series_slab(plotter, data, slab, level, name="capture_series")
        except ValueError as exc:
            raise CaptureError(f"cannot draw section {section_id!r}: {exc}") from exc
        summary = describe(data, slab, level, drawn)
        if not drawn:
            raise CaptureError(f"section {section_id!r}: {summary}")
        if publication:
            apply_publication_look(plotter)
        frame(plotter)
        snapshot(plotter, path)
    return summary


def capture_complex_field(
    reader: QVFReader,
    section_id: str,
    path: str | Path,
    *,
    part: str = "real",
    isovalue: float | None = None,
    size: tuple[int, int] = (900, 600),
    publication: bool = False,
) -> bool:
    """Render one part of a ``volume.complex`` section over the structure (#157).

    Returns False when the section cannot be read or ``part`` has no surface
    at the isovalue; :func:`render_complex_field` raises with the reason.
    """
    try:
        render_complex_field(
            reader, section_id, path, part=part, isovalue=isovalue, size=size,
            publication=publication,
        )
    except CaptureError:
        return False
    return True


def render_complex_field(
    reader: QVFReader,
    section_id: str,
    path: str | Path,
    *,
    part: str = "real",
    isovalue: float | None = None,
    size: tuple[int, int] = (900, 600),
    publication: bool = False,
) -> str:
    """:func:`capture_complex_field`, raising :class:`CaptureError`; returns the summary.

    Unset, ``isovalue`` is the section's hint lowered to fit the field, as
    the browser opens it (:func:`vibeview.renderers.complex_field.opening_isovalue`).
    """
    from vibeview.renderers.complex_field import (
        ComplexFieldRenderer,
        add_complex_field,
        describe,
        opening_isovalue,
    )
    from vibeview.viewer_defaults import section_volume_hints

    try:
        section = reader.get_section(section_id)
        data = ComplexFieldRenderer(section, reader).load()
    except Exception as exc:
        raise CaptureError(f"cannot read section {section_id!r}: {exc}") from exc
    if section.kind != "volume.complex":
        raise CaptureError(f"section {section_id!r} is a {section.kind}, not a volume.complex")
    level = (
        isovalue if isovalue is not None
        else opening_isovalue(data, section_volume_hints(reader, section_id).isovalue)
    )
    with headless_plotter(size) as plotter:
        add_structure(plotter, reader)
        try:
            drawn = add_complex_field(plotter, data, part, level, name="capture_complex")
        except ValueError as exc:
            raise CaptureError(f"cannot draw section {section_id!r}: {exc}") from exc
        summary = describe(data, part, level, drawn)
        if not drawn:
            raise CaptureError(f"section {section_id!r}: {summary}")
        if publication:
            apply_publication_look(plotter)
        frame(plotter)
        snapshot(plotter, path)
    return summary


def capture_vector_field(
    reader: QVFReader,
    section_id: str,
    path: str | Path,
    *,
    colormap: str = "viridis",
    size: tuple[int, int] = (900, 600),
    publication: bool = False,
) -> bool:
    """Render a ``volume.vector`` section's arrows over the structure to PNG (#156).

    Returns False when the section cannot be read or has no arrow above the
    cutoff; :func:`render_vector_field` raises with the reason instead.
    """
    try:
        render_vector_field(
            reader, section_id, path, colormap=colormap, size=size, publication=publication
        )
    except CaptureError:
        return False
    return True


def render_vector_field(
    reader: QVFReader,
    section_id: str,
    path: str | Path,
    *,
    colormap: str = "viridis",
    size: tuple[int, int] = (900, 600),
    publication: bool = False,
) -> str:
    """:func:`capture_vector_field`, raising :class:`CaptureError`; returns the summary.

    The arrows are the browser's at its default spacing, length and cutoff
    (:mod:`vibeview.renderers.vector_field`).
    """
    from vibeview.renderers.vector_field import VectorFieldRenderer, add_vector_glyphs, describe

    try:
        section = reader.get_section(section_id)
        renderer = VectorFieldRenderer(section, reader)
        glyphs = renderer.glyphs()
    except Exception as exc:
        raise CaptureError(f"cannot read section {section_id!r}: {exc}") from exc
    if section.kind != "volume.vector":
        raise CaptureError(f"section {section_id!r} is a {section.kind}, not a volume.vector")
    if not len(glyphs):
        raise CaptureError(f"section {section_id!r} has no vector above the cutoff to draw")
    with headless_plotter(size) as plotter:
        add_structure(plotter, reader)
        add_vector_glyphs(plotter, glyphs, colormap=colormap, name="capture_vectors")
        if publication:
            apply_publication_look(plotter)
        frame(plotter)
        snapshot(plotter, path)
    return describe(glyphs, renderer.load().unit)


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
    energy_unit: str = "ev",
) -> bool:
    """Render a band structure chart to a PNG file.

    ``energy_window`` limits the energy axis to ``(min, max)`` in eV
    relative to E_F; unset, the figure opens on the same valence window
    the viewer uses, and ``auto_window=False`` restores the full
    autoscale (#26). ``energy_unit`` is the axis unit, eV unless given;
    ``vibe-view capture`` passes the saved Band / DOS energy unit (#58).
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
    png = renderer.render_to_bytes(
        energy_window=energy_window, auto_window=auto_window, energy_unit=energy_unit
    )
    Path(path).write_bytes(png)
    return True


def capture_bond_orders(
    reader: QVFReader,
    path: str | Path,
    section_id: str | None = None,
    *,
    length_unit: str = "angstrom",
) -> bool:
    """Render the bond-orders table as an HTML file (viewable in browser).

    ``length_unit`` is the unit of the distance column (#58); the CSV the
    page offers stays in Å.
    """
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
    html = renderer.render_to_html(length_unit=length_unit)
    Path(path).write_text(html)
    return True


def capture_dos(
    reader: QVFReader,
    path: str | Path,
    section_id: str | None = None,
    *,
    energy_window: tuple[float, float] | None = None,
    auto_window: bool = True,
    energy_unit: str = "ev",
) -> bool:
    """Render a DOS chart to an HTML file.

    Takes the same ``energy_window`` / ``auto_window`` and ``energy_unit``
    arguments as :func:`capture_bands` (#26, #58); the DOS is rescaled
    with its axis.
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
    html = renderer.render_to_html(
        energy_window=energy_window, auto_window=auto_window, energy_unit=energy_unit
    )
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
    *,
    energy_unit: str = "hartree",
) -> bool:
    """Render the orbital energy diagram to an HTML file.

    ``energy_unit`` (``vibeview.units.ENERGY_UNITS``) is the axis unit,
    Hartree unless given (#58); the archive stays in Hartree.
    """
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
    html = renderer.render_energy_diagram(energy_unit=energy_unit)
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
