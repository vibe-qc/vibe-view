"""Explicit atom-matched interpolation, not a reaction-path calculation."""

from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from vibeview.figure_scene import FigureScene, draw_measurements, mapped_attribute, overlay
from vibeview.qvf import QVFReader, Section, StructureData


def morph_frames(
    start: StructureData,
    end: StructureData,
    count: int,
    *,
    mapping: list[int] | None = None,
    path: np.ndarray | None = None,
) -> np.ndarray:
    n = len(start.atoms)
    if type(count) is not int or not 2 <= count <= 2000 or n != len(end.atoms) or n == 0:
        raise ValueError("Morph needs equal nonempty atom counts and 2–2000 frames")
    if any(start.pbc) or any(end.pbc):
        raise ValueError("Morphing periodic cells requires an explicit cell-path model")
    match = list(range(n)) if mapping is None else mapping
    if len(match) != n or any(type(i) is not int for i in match) or set(match) != set(range(n)):
        raise ValueError("Atom mapping must be a complete one-to-one target permutation")
    if any(a.symbol != end.atoms[j].symbol for a, j in zip(start.atoms, match, strict=True)):
        raise ValueError("Matched atoms must have the same element")
    first = np.asarray([a.position for a in start.atoms], dtype=float)
    last = np.asarray([end.atoms[j].position for j in match], dtype=float)
    if path is None:
        images = np.stack([first, last])
    else:
        images = np.asarray(path, dtype=float)
        if images.ndim != 3 or images.shape[1:] != (n, 3) or len(images) < 2:
            raise ValueError("Path must contain at least two [atoms,3] images in source atom order")
        if not np.array_equal(images[0], first) or not np.array_equal(images[-1], last):
            raise ValueError("Path endpoints must exactly match the chosen structures and mapping")
    if not np.isfinite(images).all():
        raise ValueError("Morph coordinates must be finite")
    # Arc-length parameterization retains the supplied route, never invents
    # an energetic transition state. Repeated path points carry no distance.
    distances = np.linalg.norm(np.diff(images, axis=0).reshape(len(images) - 1, -1), axis=1)
    knots = np.r_[0.0, np.cumsum(distances)]
    if knots[-1] == 0:
        result = np.repeat(first[None, :, :], count, axis=0)
    else:
        keep = np.r_[True, np.diff(knots) > 0]
        flat = images[keep].reshape(sum(keep), -1)
        targets = np.linspace(0, knots[-1], count)
        result = np.stack(
            [np.interp(targets, knots[keep], flat[:, i]) for i in range(flat.shape[1])], axis=1
        ).reshape(count, n, 3)
    result[0] = first
    result[-1] = last
    return result


def render_morph(
    reader: QVFReader,
    end: StructureData,
    output: str | Path,
    *,
    count: int = 60,
    mapping: list[int] | None = None,
    path: np.ndarray | None = None,
    fps: float = 20,
    size: tuple[int, int] = (900, 600),
    format: str = "mp4",
    scene: FigureScene | None = None,
    camera: Any = None,
) -> Path:
    """Render exact requested frames through the existing MP4/GIF encoder."""
    from PIL import Image

    from vibeview.animation import _encode_frames
    from vibeview.headless import frame, headless_plotter, snapshot
    from vibeview.renderers.structure import StructureRenderer

    if not np.isfinite(fps) or not 0 < fps <= 240:
        raise ValueError("FPS must be positive and at most 240")
    original = reader.read_structure()
    frames = morph_frames(original, end, count, mapping=mapping, path=path)
    scene = scene or FigureScene()
    if any(ref[0] != "current" for item in scene.measurements for ref in item["refs"]):
        raise ValueError(
            "Morph labels must refer to the current structure, not separate scene models"
        )
    colors, radii = (
        ({}, {})
        if scene.attribute is None
        else mapped_attribute(scene.attribute, len(original.atoms))
    )
    with tempfile.TemporaryDirectory() as temp, headless_plotter(size) as plotter:
        for i, positions in enumerate(frames):
            plotter.clear()
            structure = copy.deepcopy(original)
            for atom, point in zip(structure.atoms, positions, strict=True):
                atom.position = point
            renderer = StructureRenderer(Section(id="morph", kind="structure"), reader)
            renderer._structure = structure
            renderer._bonds = reader.connectivity(original)
            renderer._bonds_loaded = True
            renderer.add_to_plotter(plotter, atom_colors=colors, atom_radii=radii)
            if i == 0:
                frame(plotter)
                if camera is not None:
                    plotter.camera_position = camera
                saved_camera = plotter.camera_position
            else:
                plotter.camera_position = saved_camera
            draw_measurements(plotter, scene, {"current": structure})
            filename = Path(temp) / f"frame_{i:04d}.png"
            snapshot(plotter, filename)
            with Image.open(filename) as base:
                result = Image.alpha_composite(base.convert("RGBA"), overlay(scene, size))
            result.save(filename)
        return _encode_frames(
            Path(temp), Path(output), format, fps, loop=False, preserve_frames=True
        )
