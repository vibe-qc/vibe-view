"""Replayable multi-model working scenes, independent of the browser renderer.

Scene scripts are versioned JSON events, never executable Python. Embedded atom
snapshots make replay independent of changed source paths. Model coordinates and
periodic cells undergo the same proper rigid transform; selections use displayed
coordinates and stable (model identifier, zero-based atom index) references.
"""
from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any

import numpy as np

from vibeview.align import align
from vibeview.molecular_selection import positions, select
from vibeview.qvf import Atom, StructureData

AtomRef = tuple[str, int]
_STYLES = {"ball_and_stick", "space_filling", "sticks_only", "wireframe", "cartoon"}


def _plain(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    return value


def _structure_dict(structure: StructureData) -> dict[str, Any]:
    snapshot = {item.name: _plain(copy.deepcopy(getattr(structure, item.name)))
                for item in fields(structure) if item.init and item.name != "atoms"}
    snapshot["atoms"] = [_plain(asdict(atom)) for atom in structure.atoms]
    return snapshot


def _structure_from_dict(data: dict[str, Any]) -> StructureData:
    raw = copy.deepcopy(data)
    raw['atoms'] = [Atom(**{**atom, 'position': np.asarray(atom['position'], dtype=float)})
                    for atom in raw['atoms']]
    raw['pbc'] = tuple(raw['pbc'])
    if len(raw['pbc']) != 3 or any(type(flag) is not bool for flag in raw['pbc']):
        raise ValueError("Model periodicity must contain three booleans")
    if raw.get('lattice_vectors') is not None:
        lattice = np.asarray(raw['lattice_vectors'], dtype=float)
        if lattice.shape != (3, 3) or not np.isfinite(lattice).all():
            raise ValueError("Model lattice must be a finite 3 by 3 array")
        raw['lattice_vectors'] = lattice
    if raw.get('bonds') is not None:
        raw['bonds'] = [(i, j, order, tuple(image)) for i, j, order, image in raw['bonds']]
    structure = StructureData(**raw)
    positions(structure)
    for i, j, order, image in structure.bonds or []:
        if not (0 <= i < len(structure.atoms) and 0 <= j < len(structure.atoms)):
            raise ValueError("Model bond index is out of range")
        if len(image) != 3 or not np.isfinite(order):
            raise ValueError("Invalid model bond")
    return structure


@dataclass
class SceneModel:
    structure: StructureData
    visible: bool = True
    colour: str | None = None
    style: str = 'ball_and_stick'
    cell_origin: np.ndarray = field(default_factory=lambda: np.zeros(3))


@dataclass
class ModelScene:
    models: dict[str, SceneModel] = field(default_factory=dict)
    selected: set[AtomRef] = field(default_factory=set)
    groups: dict[str, set[AtomRef]] = field(default_factory=dict)
    journal: list[dict[str, Any]] = field(default_factory=list)

    def _refs(self, refs: set[AtomRef]) -> set[AtomRef]:
        for model, index in refs:
            if model not in self.models or type(index) is not int or not (
                0 <= index < len(self.models[model].structure.atoms)
            ):
                raise ValueError("Selection contains an unknown model or atom")
        return set(refs)

    def _record(self, action: str, **payload: Any) -> None:
        self.journal.append({'action': action, **_plain(copy.deepcopy(payload))})

    def add(self, identifier: str, structure: StructureData) -> None:
        if not identifier.strip() or identifier in self.models:
            raise ValueError("Model identifier must be nonempty and unique")
        # Roundtrip validates and detaches the caller's mutable arrays/metadata.
        snapshot = _structure_dict(structure)
        model = SceneModel(_structure_from_dict(snapshot))
        self.models[identifier] = model
        self._record('add', identifier=identifier, structure=snapshot)

    def remove(self, identifier: str) -> None:
        if identifier not in self.models:
            raise ValueError("Unknown model")
        del self.models[identifier]
        self.selected = {ref for ref in self.selected if ref[0] != identifier}
        self.groups = {name: {ref for ref in refs if ref[0] != identifier}
                       for name, refs in self.groups.items()}
        self._record('remove', identifier=identifier)

    def appearance(self, identifier: str, *, visible: bool | None = None,
                   colour: str | None = None, style: str | None = None) -> None:
        if identifier not in self.models:
            raise ValueError("Unknown model")
        if visible is not None and type(visible) is not bool:
            raise ValueError("Visibility must be boolean")
        if style is not None and style not in _STYLES:
            raise ValueError("Unknown model style")
        if colour is not None:
            from matplotlib.colors import is_color_like
            if not is_color_like(colour):
                raise ValueError("Invalid model colour")
        model = self.models[identifier]
        if visible is not None:
            model.visible = visible
        if colour is not None:
            model.colour = None if colour == 'none' else colour
        if style is not None:
            model.style = style
        self._record('appearance', identifier=identifier, visible=visible,
                     colour=colour, style=style)

    def transform(self, identifier: str, rotation: np.ndarray, translation: np.ndarray) -> None:
        if identifier not in self.models:
            raise ValueError("Unknown model")
        r, t = np.asarray(rotation, dtype=float), np.asarray(translation, dtype=float)
        if (r.shape != (3, 3) or t.shape != (3,)
                or not np.isfinite(r).all() or not np.isfinite(t).all()):
            raise ValueError("Rigid transform needs finite (3,3) rotation and (3,) translation")
        if not np.allclose(r @ r.T, np.eye(3), atol=1e-10, rtol=0) or not np.isclose(
            np.linalg.det(r), 1, atol=1e-10, rtol=0
        ):
            raise ValueError("Transform must be a proper rigid rotation")
        model = self.models[identifier]
        structure = model.structure
        moved = positions(structure) @ r.T + t
        if not np.isfinite(moved).all():
            raise ValueError("Rigid transform produced nonfinite coordinates")
        for atom, point in zip(structure.atoms, moved, strict=True):
            atom.position = point
        model.cell_origin = model.cell_origin @ r.T + t
        if structure.lattice_vectors is not None:
            structure.lattice_vectors = structure.lattice_vectors @ r.T
        self._record('transform', identifier=identifier, rotation=r, translation=t)

    def evaluate(self, expression: str) -> set[AtomRef]:
        """Evaluate a selection without changing current selection or journal."""
        refs = [(name, i) for name, model in self.models.items()
                for i in range(len(model.structure.atoms))]
        atoms = [atom for model in self.models.values() for atom in model.structure.atoms]
        flat = StructureData(atoms, (False, False, False), None, None)
        index = {ref: i for i, ref in enumerate(refs)}
        groups = {name: {index[ref] for ref in self._refs(group)}
                  for name, group in self.groups.items()}
        chosen = select(flat, expression, groups=groups, model_ids=[ref[0] for ref in refs])
        return {refs[i] for i in chosen}

    def select(self, expression: str) -> set[AtomRef]:
        selected = self.evaluate(expression)
        self.selected = selected
        self._record('select', expression=expression)
        return set(selected)

    def name_group(self, name: str, refs: set[AtomRef] | None = None) -> None:
        if not name.strip():
            raise ValueError("Group name must be nonempty")
        group = self._refs(self.selected if refs is None else refs)
        self.groups[name] = group
        self._record('group', name=name, refs=sorted(group))

    def coordinates(self, refs: list[AtomRef]) -> np.ndarray:
        self._refs(set(refs))
        return np.asarray([self.models[name].structure.atoms[i].position for name, i in refs],
                          dtype=float).reshape(-1, 3).copy()

    def align_model(self, identifier: str, mobile: list[int], target: list[AtomRef]) -> float:
        if not mobile or len(set(mobile)) != len(mobile) or len(set(target)) != len(target):
            raise ValueError("Alignment requires nonempty unique ordered correspondences")
        result = align(self.coordinates([(identifier, i) for i in mobile]),
                       self.coordinates(target))
        self.transform(identifier, result.rotation, result.translation)
        return result.rmsd_after

    def to_dict(self) -> dict[str, Any]:
        return {'version': 1, 'models': [
            {'identifier': name, 'structure': _structure_dict(model.structure),
             'visible': model.visible, 'colour': model.colour, 'style': model.style,
             'cell_origin': model.cell_origin.tolist()}
            for name, model in self.models.items()],
            'selected': sorted(self.selected),
            'groups': {name: sorted(refs) for name, refs in self.groups.items()}}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelScene:
        if data.get('version') != 1:
            raise ValueError("Unsupported scene version")
        scene = cls()
        for item in data['models']:
            scene.add(item['identifier'], _structure_from_dict(item['structure']))
            origin = np.asarray(item.get('cell_origin', [0., 0., 0.]), dtype=float)
            if origin.shape != (3,) or not np.isfinite(origin).all():
                raise ValueError('Cell origin must be a finite 3-vector')
            scene.models[item['identifier']].cell_origin = origin
            scene.appearance(item['identifier'], visible=item['visible'],
                             colour=item['colour'], style=item['style'])
        scene.selected = scene._refs({(name, index) for name, index in data['selected']})
        scene.groups = {name: scene._refs({(model, index) for model, index in refs})
                        for name, refs in data['groups'].items()}
        # A loaded scene is itself a replayable starting point.
        scene.journal = [{'action': 'restore', 'scene': copy.deepcopy(scene.to_dict())}]
        return scene

    def script(self) -> str:
        return json.dumps({'version': 1, 'events': self.journal}, allow_nan=False, indent=2)

    @classmethod
    def replay(cls, script: str) -> ModelScene:
        data = json.loads(script)
        if data.get('version') != 1:
            raise ValueError("Unsupported scene script version")
        scene = cls()
        for raw in data['events']:
            event = copy.deepcopy(raw)
            action = event.pop('action')
            if action == 'add':
                scene.add(event['identifier'], _structure_from_dict(event['structure']))
            elif action == 'remove':
                scene.remove(event['identifier'])
            elif action == 'appearance':
                scene.appearance(**event)
            elif action == 'transform':
                scene.transform(event['identifier'], np.asarray(event['rotation']),
                                np.asarray(event['translation']))
            elif action == 'select':
                scene.select(event['expression'])
            elif action == 'group':
                scene.name_group(event['name'], {(model, index) for model, index in event['refs']})
            elif action == 'restore':
                scene = cls.from_dict(event['scene'])
            else:
                raise ValueError(f"Unknown scene event {action!r}")
        return scene
