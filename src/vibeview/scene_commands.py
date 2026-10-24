"""Shared browser/SDK command interpreter for working model scenes.

Only explicit commands are dispatched. File access and capture are injected by
an owner adapter, so the interpreter does not open paths or execute code itself.
"""
from __future__ import annotations

import json
import shlex
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from vibeview.model_scene import ModelScene, _plain
from vibeview.qvf import StructureData
from vibeview.selection_measurements import (
    best_fit_plane,
    centroid,
    plane_angle,
    point_plane_distance,
    principal_axes,
    selection_rmsd,
)


@dataclass
class SceneCommands:
    scene: ModelScene = field(default_factory=ModelScene)
    load_structure: Callable[[str], StructureData] | None = None
    write_text: Callable[[str, str], None] | None = None
    capture: Callable[[str], None] | None = None
    history: list[str] = field(default_factory=list)

    def execute(self, line: str) -> dict[str, Any]:
        """Execute one command; only successful commands enter history."""
        text = line.strip()
        if not text:
            raise ValueError("Enter a scene command")
        # Keep selection quoting intact; the selection parser owns its grammar.
        parts = text.split(maxsplit=1)
        verb, rest = parts[0], parts[1] if len(parts) == 2 else ''
        if verb == 'select':
            if not rest.strip():
                raise ValueError("select requires an expression")
            result: dict[str, Any] = {'selected': sorted(self.scene.select(rest))}
        else:
            tokens = shlex.split(text)
            verb, args = tokens[0], tokens[1:]
            result = self._dispatch(verb, args)
        self.history.append(text)
        return _plain(result)  # type: ignore[no-any-return]

    def _dispatch(self, verb: str, args: list[str]) -> dict[str, Any]:
        def arity(count: int) -> None:
            if len(args) != count:
                raise ValueError(f"{verb} requires {count} arguments; quote expressions or paths")

        if verb == 'open':
            arity(2)
            if self.load_structure is None:
                raise ValueError("This adapter does not support opening structures")
            if args[0] in self.scene.models:
                raise ValueError("Model identifier already exists")
            self.scene.add(args[0], self.load_structure(args[1]))
            return {'model': args[0]}
        if verb in {'show', 'hide', 'remove'}:
            arity(1)
            if verb == 'remove':
                self.scene.remove(args[0])
            else:
                self.scene.appearance(args[0], visible=verb == 'show')
            return {'model': args[0]}
        if verb in {'colour', 'color', 'style'}:
            arity(2)
            if verb == 'style':
                self.scene.appearance(args[0], style=args[1])
            else:
                self.scene.appearance(args[0], colour=args[1])
            return {'model': args[0]}
        if verb == 'group':
            arity(1)
            self.scene.name_group(args[0])
            return {'group': args[0], 'atoms': len(self.scene.selected)}
        if verb == 'move':
            arity(4)
            self.scene.transform(args[0], np.eye(3), np.asarray(args[1:], dtype=float))
            return {'model': args[0]}
        if verb == 'rotate':
            arity(5)
            axis = np.asarray(args[1:4], dtype=float)
            degrees = float(args[4])
            norm = float(np.linalg.norm(axis))
            if not np.isfinite(axis).all() or not np.isfinite(degrees) or norm == 0:
                raise ValueError("Rotation needs a finite nonzero axis and angle in degrees")
            axis /= norm
            x, y, z = axis
            skew = np.array([[0., -z, y], [z, 0., -x], [-y, x, 0.]])
            angle = np.radians(degrees)
            # Rodrigues axis-angle rotation about the scene origin.
            rotation = np.eye(3) + np.sin(angle) * skew + (1 - np.cos(angle)) * (skew @ skew)
            self.scene.transform(args[0], rotation, np.zeros(3))
            return {'model': args[0]}
        if verb == 'align':
            arity(3)
            mobile = sorted(self.scene.evaluate(args[1]))
            target = sorted(self.scene.evaluate(args[2]))
            if any(name != args[0] for name, _ in mobile):
                raise ValueError("Mobile alignment selection must belong to the named model")
            value = self.scene.align_model(args[0], [i for _, i in mobile], target)
            return {'rmsd': value, 'units': 'angstrom'}
        if verb == 'measure':
            return self._measure(args)
        if verb in {'save', 'save-script'}:
            arity(1)
            if self.write_text is None:
                raise ValueError("This adapter does not support saving files")
            contents = (json.dumps(self.scene.to_dict(), allow_nan=False, indent=2)
                        if verb == 'save' else self.scene.script())
            self.write_text(args[0], contents)
            return {'saved': args[0]}
        if verb == 'capture':
            arity(1)
            if self.capture is None:
                raise ValueError("This adapter does not support capture")
            self.capture(args[0])
            return {'captured': args[0]}
        raise ValueError(f"Unknown scene command {verb!r}")

    def _measure(self, args: list[str]) -> dict[str, Any]:
        if not args:
            raise ValueError("measure requires a measurement name")
        kind, expressions = args[0], args[1:]
        single = {'centroid', 'axes', 'plane'}
        paired = {'rmsd', 'rmsd-fit', 'plane-angle', 'point-plane'}
        if kind not in single | paired:
            raise ValueError("Unknown selection measurement")
        expected = 1 if kind in single else 2
        if len(expressions) != expected:
            raise ValueError(f"{kind} requires {expected} quoted selection expressions")
        points = [self.scene.coordinates(sorted(self.scene.evaluate(expression)))
                  for expression in expressions]
        if kind == 'centroid':
            return {'centroid': centroid(points[0]), 'units': 'angstrom'}
        if kind == 'axes':
            return {**asdict(principal_axes(points[0])), 'variance_units': 'angstrom^2'}
        if kind == 'plane':
            return {**asdict(best_fit_plane(points[0])), 'units': 'angstrom'}
        if kind in {'rmsd', 'rmsd-fit'}:
            return {'rmsd': selection_rmsd(points[0], points[1], superpose=kind == 'rmsd-fit'),
                    'units': 'angstrom'}
        if kind == 'plane-angle':
            return {'angle': plane_angle(best_fit_plane(points[0]), best_fit_plane(points[1])),
                    'units': 'degree'}
        if len(points[0]) != 1:
            raise ValueError("Point-plane measurement requires exactly one point")
        return {'distance': point_plane_distance(points[0][0], best_fit_plane(points[1])),
                'units': 'angstrom'}
