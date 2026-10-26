"""Portable figure decorations shared by browser overlays and image exports."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from matplotlib import colormaps
from matplotlib.colors import to_rgba

from vibeview.geometry_edit import parameter_value


@dataclass
class FigureScene:
    measurements: list[dict[str, Any]] = field(default_factory=list)
    annotations: list[dict[str, Any]] = field(default_factory=list)
    keys: list[dict[str, Any]] = field(default_factory=list)
    attribute: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(
            {
                "version": 1,
                "measurements": self.measurements,
                "annotations": self.annotations,
                "keys": self.keys,
                "attribute": self.attribute,
            }
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FigureScene:
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("Unsupported figure scene version")
        if set(data) - {"version", "measurements", "annotations", "keys", "attribute"}:
            raise ValueError("Unknown figure scene field")
        result = cls(
            copy.deepcopy(data.get("measurements", [])),
            copy.deepcopy(data.get("annotations", [])),
            copy.deepcopy(data.get("keys", [])),
            copy.deepcopy(data.get("attribute")),
        )
        for rows in (result.measurements, result.annotations, result.keys):
            if not isinstance(rows, list) or len(rows) > 200:
                raise ValueError("Figure decoration list must contain at most 200 records")
        for item in result.measurements:
            refs = item["refs"]
            if len(refs) not in (2, 3, 4) or len({tuple(r) for r in refs}) != len(refs):
                raise ValueError("Measurements require two to four distinct atom references")
            if any(
                not isinstance(r, list)
                or len(r) != 2
                or not isinstance(r[0], str)
                or type(r[1]) is not int
                or r[1] < 0
                for r in refs
            ):
                raise ValueError("Invalid measurement atom reference")
            if type(item.get("precision", 2)) is not int or not 0 <= item.get("precision", 2) <= 8:
                raise ValueError("Measurement precision must be 0–8")
            _colour(item.get("colour", "black"))
            _positive(item.get("scale", 0.3), "label scale", 5)
            _positive(item.get("width", 2), "line width", 20)
        for item in result.annotations:
            if item["kind"] not in ("text", "arrow", "box"):
                raise ValueError("Annotation must be text, arrow or box")
            _point(item["start"])
            _point(item.get("end", item["start"]))
            _colour(item.get("colour", "black"))
            _positive(item.get("width", 2), "line width", 50)
            _positive(item.get("font_size", 20), "font size", 200)
            if not isinstance(item.get("text", ""), str) or len(item.get("text", "")) > 2000:
                raise ValueError("Annotation text must contain at most 2000 characters")
        for item in result.keys:
            _point(item.get("start", [0.8, 0.1]))
            _point(item.get("end", [0.85, 0.8]))
            _range(item["range"])
            colormaps[item.get("palette", "viridis")]
            _colour(item.get("colour", "black"))
        if result.attribute is not None:
            _range(result.attribute["range"])
            colormaps[result.attribute.get("palette", "viridis")]
            values = np.asarray(result.attribute["values"], dtype=float)
            if values.ndim != 1 or not np.isfinite(values).all():
                raise ValueError("Attributes need finite scalar values, one per atom")
            radii = result.attribute.get("radii", [0.15, 0.65])
            if len(radii) != 2 or not 0 < float(radii[0]) <= float(radii[1]) <= 5:
                raise ValueError("Display radii must be positive, ordered and at most 5 Å")
        return result


def _point(value: Any) -> tuple[float, float]:
    xy = np.asarray(value, dtype=float)
    if xy.shape != (2,) or not np.isfinite(xy).all() or np.any((xy < 0) | (xy > 1)):
        raise ValueError("Image coordinates must be two fractions between 0 and 1")
    return float(xy[0]), float(xy[1])


def _positive(value: Any, name: str, maximum: float) -> float:
    number = float(value)
    if not np.isfinite(number) or not 0 < number <= maximum:
        raise ValueError(f"{name} must be positive and at most {maximum}")
    return number


def _colour(value: Any) -> tuple[int, int, int, int]:
    return tuple(int(round(v * 255)) for v in to_rgba(value))  # type: ignore[return-value]


def _range(value: Any) -> tuple[float, float]:
    lo, hi = map(float, value)
    if not np.isfinite([lo, hi]).all() or lo >= hi:
        raise ValueError("Property range must be finite and increasing")
    return lo, hi


def mapped_attribute(
    spec: dict[str, Any], count: int
) -> tuple[dict[int, tuple[float, float, float]], dict[int, float]]:
    values = np.asarray(spec["values"], dtype=float)
    if values.shape != (count,) or not np.isfinite(values).all():
        raise ValueError("Attribute no longer matches the structure atom count")
    lo, hi = _range(spec["range"])
    fraction = np.clip((values - lo) / (hi - lo), 0, 1)
    colors = colormaps[spec.get("palette", "viridis")](fraction)
    radii = spec.get("radii", [0.15, 0.65])
    r = np.interp(fraction, [0, 1], radii)
    return (
        {i: (float(c[0]), float(c[1]), float(c[2])) for i, c in enumerate(colors)},
        {i: float(v) for i, v in enumerate(r)} if spec.get("radius_enabled", False) else {},
    )


def overlay(
    scene: FigureScene, size: tuple[int, int], *, reference_size: tuple[int, int] | None = None
) -> Any:
    """RGBA layer. Coordinates are image fractions; sizes are reference pixels."""
    from matplotlib.font_manager import findfont
    from PIL import Image, ImageDraw, ImageFont

    width, height = size
    if min(size) < 1 or max(size) > 16384:
        raise ValueError("Overlay image dimensions must be 1–16384")
    scale = min(width / (reference_size or size)[0], height / (reference_size or size)[1])
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def pixel(value: Any) -> tuple[float, float]:
        x, y = _point(value)
        return x * width, y * height

    for item in scene.annotations:
        start, end = pixel(item["start"]), pixel(item.get("end", item["start"]))
        color = _colour(item.get("colour", "black"))
        stroke = max(1, round(item.get("width", 2) * scale))
        if item["kind"] == "text":
            font = ImageFont.truetype(
                findfont("DejaVu Sans"), max(1, round(item.get("font_size", 20) * scale))
            )
            draw.text(start, item.get("text", ""), fill=color, font=font)
        elif item["kind"] == "box":
            draw.rectangle(
                (
                    min(start[0], end[0]),
                    min(start[1], end[1]),
                    max(start[0], end[0]),
                    max(start[1], end[1]),
                ),
                outline=color,
                width=stroke,
            )
        else:
            draw.line([start, end], fill=color, width=stroke)
            delta = np.asarray(end) - start
            norm = np.linalg.norm(delta)
            if norm > 1e-8:
                direction = delta / norm
                side = np.array([-direction[1], direction[0]])
                tip = np.asarray(end)
                length = max(8 * scale, stroke * 3)
                draw.polygon(
                    [
                        tuple(tip),
                        tuple(tip - length * direction + 0.4 * length * side),
                        tuple(tip - length * direction - 0.4 * length * side),
                    ],
                    fill=color,
                )
    for item in scene.keys:
        x0, y0 = pixel(item.get("start", [0.8, 0.1]))
        x1, y1 = pixel(item.get("end", [0.85, 0.8]))
        x0, x1 = sorted([round(x0), round(x1)])
        y0, y1 = sorted([round(y0), round(y1)])
        lo, hi = _range(item["range"])
        palette = colormaps[item.get("palette", "viridis")]
        for y in range(y0, y1 + 1):
            color = _colour(palette(1 - (y - y0) / max(1, y1 - y0)))
            draw.line([(x0, y), (x1, y)], fill=color, width=1)
        font = ImageFont.truetype(findfont("DejaVu Sans"), max(1, round(14 * scale)))
        # Scenes from before #138 have no key colour; their text stays black.
        ink = _colour(item.get("colour", "black"))
        draw.text((x0, y0 - 18 * scale), str(item.get("title", "Property")), fill=ink, font=font)
        draw.text((x1 + 4 * scale, y0), f"{hi:g}", fill=ink, font=font)
        draw.text((x1 + 4 * scale, y1 - 14 * scale), f"{lo:g}", fill=ink, font=font)
    return image


def draw_measurements(
    plotter: Any,
    scene: FigureScene,
    structures: dict[str, Any],
    representation: str = "ball_and_stick",
) -> None:
    import pyvista as pv

    from vibeview.renderers.structure import (
        atom_display_radius,
        build_label_mesh,
        register_label_actor,
    )

    for name in list(plotter.actors):
        if name.startswith("figure_measure_"):
            plotter.remove_actor(name, render=False)
    for index, item in enumerate(scene.measurements):
        points, radii = [], []
        for model, atom in item["refs"]:
            if model not in structures or not 0 <= atom < len(structures[model].atoms):
                raise ValueError("Measurement refers to an unavailable model/atom")
            points.append(structures[model].atoms[atom].position)
            radii.append(
                atom_display_radius(structures[model].atoms[atom].atomic_number, representation)
            )
        xyz = np.asarray(points)
        value = parameter_value(xyz, list(range(len(xyz))))
        unit = "Å" if len(points) == 2 else "°"
        text = f"{item.get('prefix', '')}{value:.{item.get('precision', 2)}f} {unit}"
        color = item.get("colour", "black")
        scale = float(item.get("scale", 0.3))
        plotter.add_mesh(
            pv.lines_from_points(xyz),
            color=color,
            line_width=float(item.get("width", 2)),
            name=f"figure_measure_{index}_line",
        )
        center = xyz.mean(axis=0)
        # Lift the label clear of the measured atoms' displayed spheres in any
        # view direction: its lower edge (half the text height below the lift)
        # stays a radius beyond the farthest atom. At a fixed 0.4 A an O-H
        # label sat inside the O sphere, hiding all but its first digit (#134).
        reach = float(np.linalg.norm(xyz - center, axis=1).max())
        lift = max(0.4, max(radii) + reach + 0.5 * scale + 0.1)
        mesh = build_label_mesh(
            [center], [text], scale=scale, lift=lift, camera=plotter.camera_position
        )
        if mesh is not None:
            name = f"figure_measure_{index}_label"
            plotter.add_mesh(mesh, color=color, name=name, lighting=False, show_scalar_bar=False)
            register_label_actor(plotter, name, [center], [text], scale=scale, lift=lift)
