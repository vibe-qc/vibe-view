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

DEFAULT_SIZE: tuple[int, int] = (900, 600)


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
    """The primary structure section, or None when the archive has none."""
    return next((s for s in reader.sections if s.kind == "structure"), None)


def add_structure(
    plotter: pv.Plotter,
    reader: QVFReader,
    *,
    representation: str = "ball_and_stick",
    show_labels: bool = False,
    replication: tuple[int, int, int] = (1, 1, 1),
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
def structure_frame_writer(
    reader: QVFReader,
    *,
    size: Sequence[int] = DEFAULT_SIZE,
    representation: str = "ball_and_stick",
) -> Iterator[Callable[[str | Path, Any], bool] | None]:
    """A ``write(path, positions)`` callable that renders frames of one structure.

    One plotter and one :class:`StructureRenderer` serve every frame: the
    scene is cleared and redrawn at the frame's ``positions`` (bonds follow
    the displaced geometry), the camera is framed on the first frame and
    kept, so a long trajectory costs one window and one archive parse
    instead of one per frame. Yields None when the archive has no
    structure section.
    """
    from vibeview.renderers.structure import StructureRenderer

    section = structure_section(reader)
    if section is None:
        yield None
        return
    renderer = StructureRenderer(section, reader)
    with headless_plotter(size) as plotter:
        framed = False

        def write(path: str | Path, positions: Any = None) -> bool:
            nonlocal framed
            import numpy as np

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
            snapshot(plotter, path)
            return True

        yield write


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
        ):
            return False
        if publication:
            apply_publication_look(plotter)
        frame(plotter)
        if path is None:
            return snapshot(plotter)
        snapshot(plotter, path)
        return True


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
