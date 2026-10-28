"""Off-screen rendering shared by every headless surface.

The CLI capture and batch commands, the animation frames, the dashboard and
Jupyter thumbnails and the browser viewer's server-side scene all build the
same PyVista scene: an off-screen plotter on the dark vibe-view background,
the structure renderer, an isometric view framed to the actors, then a
screenshot. This module is that sequence, written once, so every PNG the
project produces shares one theme and one framing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vibeview.plotter_compat import reset_camera, set_background, view_isometric
from vibeview.renderers.palette import DARK_BACKGROUND, ISO_NEGATIVE, ISO_POSITIVE

if TYPE_CHECKING:
    import numpy as np
    import pyvista as pv

    from vibeview.qvf import QVFReader, Section
    from vibeview.renderers.volume_render import VolumeImage

DEFAULT_SIZE: tuple[int, int] = (900, 600)
#: The browser's filmstrip thumbnails: small enough to carry a strip of them
#: in viewer state as data URLs, large enough to tell frames apart.
THUMBNAIL_SIZE: tuple[int, int] = (96, 64)


def new_plotter(
    size: Sequence[int] = DEFAULT_SIZE, background: str = DARK_BACKGROUND
) -> pv.Plotter:
    """An off-screen plotter on the vibe-view background. The caller closes it."""
    import pyvista as pv

    plotter = pv.Plotter(off_screen=True, window_size=[int(size[0]), int(size[1])])
    set_background(plotter, background)
    return plotter


@contextmanager
def headless_plotter(
    size: Sequence[int] = DEFAULT_SIZE, background: str = DARK_BACKGROUND
) -> Iterator[pv.Plotter]:
    """:func:`new_plotter` that closes itself, however the block ends."""
    plotter = new_plotter(size, background)
    try:
        yield plotter
    finally:
        plotter.close()


def frame(plotter: pv.Plotter) -> None:
    """The one camera every headless render uses: isometric, fitted to the scene."""
    view_isometric(plotter)
    reset_camera(plotter)


def png_bytes(image: np.ndarray) -> bytes:
    """The RGB(A) array :func:`snapshot` returns, encoded as a PNG."""
    import io

    import numpy as np
    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(np.asarray(image)).save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def snapshot(plotter: pv.Plotter, path: str | Path | None = None) -> np.ndarray | None:
    """Write a PNG to ``path``, or return the RGB(A) array when ``path`` is None."""
    if path is None:
        import numpy as np

        plotter.render()
        image = plotter.screenshot(return_img=True)
        return None if image is None else np.asarray(image)
    plotter.screenshot(str(path))
    return None


def structure_section(reader: QVFReader) -> Section | None:
    """The shown structure section, or None when the archive has none.

    The archive's first structure (QVF spec § 4.1) unless
    :meth:`QVFReader.show_structure` picked another; never a section found
    by its id.
    """
    return reader.shown_structure


def add_structure(
    plotter: pv.Plotter,
    reader: QVFReader,
    *,
    representation: str = "ball_and_stick",
    show_labels: bool = False,
    replication: tuple[int, int, int] = (1, 1, 1),
    atom_color_mode: str = "element",
    cutaway: str = "off",
    cutaway_position: float = 0.5,
    positions: Sequence[Sequence[float] | np.ndarray] | np.ndarray | None = None,
) -> bool:
    """Draw the archive's structure, optionally at displaced ``positions``.

    Returns False when there is no structure section. ``positions`` is a
    trajectory or vibration frame; bonds follow the displaced geometry.
    """
    from vibeview.renderers.structure import StructureRenderer

    section = structure_section(reader)
    if section is None:
        return False
    renderer = StructureRenderer(section, reader)
    if positions is not None:
        import numpy as np

        data = renderer.load()
        # strict=False: a frame and the structure section can come from a
        # producer at different lengths. Render what lines up rather than
        # aborting the whole video.
        for atom, pos in zip(data.atoms, positions, strict=False):
            atom.position = np.asarray(pos, dtype=float)
        renderer._bonds = reader.connectivity(data)
    renderer.add_to_plotter(
        plotter,
        representation=representation,
        show_labels=show_labels,
        replication=replication,
        atom_color_mode=atom_color_mode, cutaway=cutaway, cutaway_position=cutaway_position,
    )
    return True


def apply_publication_look(plotter: pv.Plotter) -> None:
    """The browser's Publication view preset for a headless scene (#68).

    The scientific material and its near-white background on every atom
    and bond, then black inverted-hull outlines around them, so a
    ``vibe-view capture --publication`` PNG matches a browser screenshot
    taken in that preset.
    """
    from vibeview.material_presets import (
        OUTLINE_ACTOR_PREFIX,
        add_figure_outlines,
        apply_material_to_actor,
        get_preset,
    )

    preset = get_preset("scientific")
    set_background(plotter, preset.background_color)
    for name, actor in list(plotter.actors.items()):
        label = str(name)
        if "labels" in label or label.startswith(OUTLINE_ACTOR_PREFIX):
            continue
        prop = actor.GetProperty() if hasattr(actor, "GetProperty") else None
        current = tuple(prop.GetColor()) if prop is not None else None
        apply_material_to_actor(
            actor, preset, base_color=current, is_bond=label.startswith("bond")
        )
    add_figure_outlines(plotter)


@contextmanager
def _frame_scene(
    reader: QVFReader, *, size: Sequence[int], representation: str
) -> Iterator[tuple[pv.Plotter, Callable[[Any], None]] | None]:
    """One plotter and one :class:`StructureRenderer` for a sequence of frames.

    Yields ``(plotter, draw)``, where ``draw(positions)`` clears the scene
    and redraws the structure at ``positions`` (bonds follow the displaced
    geometry; None keeps the stored geometry), framing the camera on the
    first call and keeping it. The renderer works on its own copy of the
    structure, so a browser server rendering a filmstrip never moves the
    atoms of the scene it serves. Yields None without a structure section.
    """
    import copy

    from vibeview.renderers.structure import StructureRenderer

    section = structure_section(reader)
    if section is None:
        yield None
        return
    renderer = StructureRenderer(section, reader)
    renderer._structure = copy.deepcopy(renderer.load_structure())
    with headless_plotter(size) as plotter:
        framed = False
        from vibeview.renderers.moving_structure import MovingStructure
        from vibeview.renderers.structure import INSTANCED_ATOM_THRESHOLD

        reference = renderer.load_structure()
        moving = (MovingStructure([a.atomic_number for a in reference.atoms], representation)
                  if (len(reference.atoms) >= INSTANCED_ATOM_THRESHOLD
                      and reference.bonds is None and not any(reference.pbc)
                      and not any(s.kind == "bond_orders" for s in reader.sections)
                      and representation != "wireframe") else None)

        def draw(positions: Any = None) -> None:
            nonlocal framed
            import numpy as np

            if moving is not None:
                coords = (np.asarray([a.position for a in reference.atoms])
                          if positions is None else np.asarray(positions))
                moving.update(plotter, coords, lattice=reference.lattice_vectors, pbc=reference.pbc)
                if not framed:
                    frame(plotter)
                    framed = True
                return
            plotter.clear_actors()
            data = renderer.load()
            if positions is not None:
                for atom, pos in zip(data.atoms, positions, strict=False):
                    atom.position = np.asarray(pos, dtype=float)
                renderer._bonds = reader.connectivity(data)
            renderer.add_to_plotter(plotter, representation=representation)
            if not framed:
                frame(plotter)
                framed = True

        yield plotter, draw


@contextmanager
def structure_frame_writer(
    reader: QVFReader,
    *,
    size: Sequence[int] = DEFAULT_SIZE,
    representation: str = "ball_and_stick",
    decorate: Callable[[pv.Plotter, Any], None] | None = None,
) -> Iterator[Callable[[str | Path, Any], bool] | None]:
    """A ``write(path, positions)`` callable that renders frames of one structure.

    One plotter and one :class:`StructureRenderer` serve every frame: the
    scene is cleared and redrawn at the frame's ``positions`` (bonds follow
    the displaced geometry), the camera is framed on the first frame and
    kept, so a long trajectory costs one window and one archive parse
    instead of one per frame. Yields None when the archive has no
    structure section.
    """
    with _frame_scene(reader, size=size, representation=representation) as scene:
        if scene is None:
            yield None
            return
        plotter, draw = scene

        def write(path: str | Path, positions: Any = None) -> bool:
            draw(positions)
            if decorate is not None:
                decorate(plotter, positions)
            snapshot(plotter, path)
            return True

        yield write


@contextmanager
def structure_thumbnail_writer(
    reader: QVFReader,
    *,
    size: Sequence[int] = THUMBNAIL_SIZE,
    representation: str = "ball_and_stick",
) -> Iterator[Callable[[Any], bytes] | None]:
    """A ``thumbnail(positions)`` callable returning a small PNG of one frame.

    The browser's filmstrip renderer: :func:`structure_frame_writer` with
    the PNG returned as bytes instead of written, at :data:`THUMBNAIL_SIZE`
    unless ``size`` says otherwise. Yields None when the archive has no
    structure section.
    """
    with _frame_scene(reader, size=size, representation=representation) as scene:
        if scene is None:
            yield None
            return
        plotter, draw = scene

        def thumbnail(positions: Any = None) -> bytes:
            draw(positions)
            image = snapshot(plotter)
            return png_bytes(image) if image is not None else b""

        yield thumbnail


def render_structure_frame(
    reader: QVFReader,
    path: str | Path | None = None,
    *,
    size: Sequence[int] = DEFAULT_SIZE,
    representation: str = "ball_and_stick",
    show_labels: bool = False,
    replication: tuple[int, int, int] = (1, 1, 1),
    positions: Sequence[Sequence[float] | np.ndarray] | np.ndarray | None = None,
    publication: bool = False,
    atom_color_mode: str = "element",
    cutaway: str = "off",
    cutaway_position: float = 0.5,
) -> np.ndarray | bool | None:
    """One structure PNG: to ``path`` (returns True/False), or as an array.

    Returns False when the archive has no structure section; with a path,
    True once the file is written; without one, the image array.
    ``publication`` applies :func:`apply_publication_look`.
    """
    with headless_plotter(size) as plotter:
        if not add_structure(
            plotter,
            reader,
            representation=representation,
            show_labels=show_labels,
            replication=replication,
            positions=positions,
            atom_color_mode=atom_color_mode, cutaway=cutaway, cutaway_position=cutaway_position,
        ):
            return False
        if publication:
            apply_publication_look(plotter)
        frame(plotter)
        if path is None:
            return snapshot(plotter)
        snapshot(plotter, path)
        return True


def molecule_thumbnail(
    atoms: Sequence[tuple[int, float, float, float]],
    *,
    label: str = "molecule",
    size: Sequence[int] = THUMBNAIL_SIZE,
) -> bytes | None:
    """A small PNG of a generated molecule, ``(Z, x, y, z)`` atoms in Å.

    The structure library's thumbnails: the atoms are packed into an
    in-memory archive (:func:`vibeview.converters.atoms_to_qvf`) and drawn
    by :func:`structure_thumbnail_writer`. None when there are no atoms.
    """
    if not atoms:
        return None
    from vibeview.converters import atoms_to_qvf
    from vibeview.qvf import QVFReader

    reader = QVFReader(atoms_to_qvf(list(atoms), label=label))
    try:
        with structure_thumbnail_writer(reader, size=size) as thumbnail:
            return thumbnail(None) if thumbnail is not None else None
    finally:
        reader.close()


def add_volume_render(
    plotter: pv.Plotter,
    reader: QVFReader,
    section_id: str,
    *,
    colormap: str = "viridis",
    opacity: float = 0.6,
    transfer_function: dict[str, Any] | None = None,
    max_dim: int | None = None,
    name: str = "capture_volume",
) -> VolumeImage:
    """Draw ``section_id`` as a volume, the way the browser's volume mode does.

    The payload and the transfer function come from ``renderers.volume_render``:
    the field quantised over the hint's window (or the kind's range), capped
    at ``max_dim``, through the hint's function or the kind's preset with
    ``opacity`` as the ramp's peak. Raises ``VolumeRenderError`` for a grid
    the payload refuses (skewed, empty, no finite samples) and ``QVFError``
    when the section cannot be read; the caller decides how to report.
    """
    from vibeview.renderers.volume import VolumeRenderer
    from vibeview.renderers.volume_render import (
        DEFAULT_MAX_DIM,
        add_volume_actor,
        resolve_transfer_function,
        volume_image,
    )
    from vibeview.viewer_defaults import validate_transfer_function

    section = reader.get_section(section_id)
    renderer = VolumeRenderer(section, reader)
    grid = renderer.load_grid()
    data = renderer.load_data()
    existing = validate_transfer_function(transfer_function)
    window = (float(existing["range"][0]), float(existing["range"][1])) if existing else None
    volume = volume_image(
        data,
        grid,
        section.kind,
        max_dim=DEFAULT_MAX_DIM if max_dim is None else int(max_dim),
        value_range=window,
    )
    tf = resolve_transfer_function(
        kind=section.kind,
        window=volume.range,
        colormap=colormap,
        opacity=opacity,
        existing=existing,
    )
    add_volume_actor(plotter, volume, tf, name=name)
    return volume


def add_signed_isosurfaces(
    plotter: pv.Plotter,
    positive: Any,
    negative: Any,
    *,
    opacity: float,
    name: str,
) -> None:
    """Draw the two lobes of a signed field in the shared phase colours."""
    for mesh, color, suffix in ((positive, ISO_POSITIVE, "_pos"), (negative, ISO_NEGATIVE, "_neg")):
        if mesh is not None and mesh.n_points:
            plotter.add_mesh(
                mesh, color=color, opacity=opacity, name=f"{name}{suffix}", show_scalar_bar=False
            )
