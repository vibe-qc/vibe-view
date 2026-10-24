"""One scene path for atom-derived surface and interaction actors and captures."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import numpy as np

from vibeview.interactions import find_interactions
from vibeview.molecular_surfaces import Surface, build_surface, mesh_style
from vibeview.qvf import GridData, QVFReader, StructureData

# Kyte & Doolittle, J Mol Biol 157:105 (1982), doi:10.1016/0022-2836(82)90515-0.
HYDROPATHY = dict(
    zip(
        [
            "ILE",
            "VAL",
            "LEU",
            "PHE",
            "CYS",
            "MET",
            "ALA",
            "GLY",
            "THR",
            "SER",
            "TRP",
            "TYR",
            "PRO",
            "HIS",
            "GLU",
            "GLN",
            "ASP",
            "ASN",
            "LYS",
            "ARG",
        ],
        [
            4.5,
            4.2,
            3.8,
            2.8,
            2.5,
            1.9,
            1.8,
            -0.4,
            -0.7,
            -0.8,
            -0.9,
            -1.3,
            -1.6,
            -3.2,
            -3.5,
            -3.5,
            -3.5,
            -3.5,
            -3.9,
            -4.5,
        ],
        strict=True,
    )
)


def sample_potential(points: np.ndarray, grid: GridData, values: np.ndarray) -> np.ndarray:
    """Trilinear sampling in the full (possibly skew) grid basis; no extrapolation."""
    if values.shape != grid.shape or min(grid.shape) < 2:
        raise ValueError(
            "Potential interpolation needs the declared 3-D grid with at least 2 points per axis"
        )
    frac = (np.asarray(points) / grid.to_angstrom - grid.origin) @ np.linalg.inv(grid.voxel_vectors)
    if np.any(frac < -1e-8) or np.any(frac > np.array(grid.shape) - 1 + 1e-8):
        raise ValueError("Potential grid does not cover the entire selected surface")
    frac = np.clip(frac, 0, np.array(grid.shape) - 1)
    lo = np.minimum(np.floor(frac).astype(int), np.array(grid.shape) - 2)
    t = frac - lo
    result = np.zeros(len(points))
    for a in (0, 1):
        for b in (0, 1):
            for c in (0, 1):
                weight = np.prod(np.where(np.array([a, b, c]), t, 1 - t), axis=1)
                result += weight * values[lo[:, 0] + a, lo[:, 1] + b, lo[:, 2] + c]
    if not np.isfinite(result).all():
        raise ValueError("Potential samples must be finite")
    return result


def _category_colours(structure: StructureData, mode: str, keys: list[str]) -> list[str]:
    """The atoms' own colours for each key: CPK by element, the ribbon
    palette by chain in file order (as the structure renderer assigns it)."""
    from vibeview.renderers import palette
    from vibeview.renderers.structure import cpk_color

    if mode == "element":
        numbers = {a.symbol: a.atomic_number for a in structure.atoms}
        return [cpk_color(numbers[key]) for key in keys]
    order = list(dict.fromkeys(a.chain_id for a in structure.atoms if a.chain_id))
    return [palette.chain_color(order.index(key)) if key in order else palette.GUIDE_GREY
            for key in keys]


def colour_surface(
    surface: Surface, structure: StructureData, mode: str, reader: QVFReader | None = None
) -> tuple[str, str | list[str], dict[float, str]]:
    """Attach scalar values and return title, colour map, categorical key.

    A categorical mode returns one colour per key, in key order, so the
    surface carries the same element or chain colours as the atoms.
    """
    atoms = [structure.atoms[i] for i in surface.atom_indices]
    annotations: dict[float, str] = {}
    cmap: str | list[str]
    if mode in {"element", "chain"}:
        labels = [a.symbol if mode == "element" else (a.chain_id or "(no chain)") for a in atoms]
        keys = sorted(set(labels))
        values = np.array([keys.index(label) for label in labels], dtype=float)
        annotations = {float(i): key for i, key in enumerate(keys)}
        title, cmap = mode.title(), _category_colours(structure, mode, keys)
    elif mode == "hydrophobicity":
        if any(a.residue_name not in HYDROPATHY for a in atoms):
            raise ValueError("Hydrophobicity requires standard amino-acid residue names")
        values = np.array([HYDROPATHY[a.residue_name or ""] for a in atoms])
        title, cmap = "Kyte-Doolittle hydropathy", "BrBG"
    elif mode == "potential":
        if reader is None:
            raise ValueError("Stored potential requires an archive reader")
        section = next((s for s in reader.sections if s.kind == "volume.potential"), None)
        if section is None:
            raise ValueError("No stored volume.potential is available")
        values = sample_potential(
            surface.mesh.points,
            reader.read_volume_grid(section.id),
            reader.read_volume_data(section.id),
        )
        title, cmap = "Stored potential (a.u.)", "RdBu_r"
    elif mode in {"mulliken", "loewdin", "hirshfeld", "iao"}:
        if reader is None:
            raise ValueError("Atomic charges require an archive reader")
        section = next((s for s in reader.sections if s.kind == "atom_properties"), None)
        data = None if section is None else reader.read_atom_properties(section.id)
        charges = getattr(data, mode + "_charges", None)
        if charges is None or len(charges) != len(structure.atoms):
            raise ValueError(f"No aligned {mode} charges are available")
        values = np.asarray(charges)[surface.atom_indices]
        title, cmap = mode.title() + " charge (e)", "RdBu_r"
    else:
        raise ValueError("Unknown surface colour property")
    if not np.isfinite(values).all():
        raise ValueError("Surface properties must be finite")
    surface.mesh.point_data["property"] = values
    return title, cmap, annotations


def add_inspection(
    plotter: Any, structure: StructureData, options: dict[str, Any], reader: QVFReader | None = None
) -> dict[str, Any]:
    """Build polygonal actors supported by vtk.js and screenshot/movie capture."""
    import pyvista as pv

    result: dict[str, Any] = {"surface": None, "interactions": []}
    selection = str(options.get("selection", "all"))
    kind = options.get("surface", "off")
    if kind != "off":
        surface = build_surface(
            structure,
            kind=kind,
            selection=selection,
            probe=float(options.get("probe", 1.4)),
            spacing=float(options.get("spacing", 0.25)),
        )
        title, cmap, annotations = colour_surface(
            surface, structure, options.get("colour", "element"), reader
        )
        opacity = float(options.get("opacity", 0.7))
        if not np.isfinite(opacity) or not 0 <= opacity <= 1:
            raise ValueError("Surface opacity must be between zero and one")
        # Categories get one table entry each, centred on its index, and the
        # vertices are coloured before interpolation: interpolating the index
        # across an element or chain boundary swept through every colour of
        # the table, a rainbow fringe between two atoms (#125 acceptance).
        categorical = dict(
            n_colors=len(annotations),
            clim=[-0.5, len(annotations) - 0.5],
            interpolate_before_map=False,
        ) if annotations else {}
        plotter.add_mesh(
            surface.mesh,
            name="molecular_surface",
            scalars="property",
            cmap=cmap,
            annotations=annotations or None,
            **categorical,
            show_scalar_bar=True,
            scalar_bar_args={
                "title": title,
                "color": "white"
                if sum(pv.Color(plotter.background_color).float_rgb) < 1.5
                else "black",
                # A category index is not a quantity; the key names it.
                **({"n_labels": 0} if annotations else {}),
            },
            opacity=opacity,
            style=mesh_style(options.get("style", "solid")),
            point_size=3,
        )
        result["surface"] = dict(
            kind=kind,
            area_A2=surface.area,
            volume_A3=surface.volume,
            spacing_A=surface.spacing,
            probe_A=surface.probe,
        )
    kinds = tuple(options.get("interactions", ()))
    if kinds:
        rows = find_interactions(
            structure,
            selection=selection,
            partner=options.get("partner"),
            kinds=kinds,
            hbond_distance=float(options.get("hbond_distance", 3.5)),
            hbond_angle=float(options.get("hbond_angle", 110)),
            contact_gap=float(options.get("contact_gap", 0.5)),
            clash_overlap=float(options.get("clash_overlap", 0.4)),
            rings=tuple(tuple(r) for r in options.get("rings", ())),
            positive=tuple(options.get("positive", ())),
            negative=tuple(options.get("negative", ())),
        )
        colours = dict(
            hbond="#27b6c8",
            contact="#999999",
            clash="#ef3b2c",
            salt="#e6c229",
            halogen="#9b59b6",
            pi="#2ca25f",
        )
        for n, row in enumerate(rows):
            first = row.atoms[-2] if row.kind in {"hbond", "halogen"} else row.atoms[0]
            start, end = structure.atoms[first].position, structure.atoms[row.atoms[-1]].position
            if row.kind == "pi":
                half = row.ring_split
                assert half is not None
                start = np.mean([structure.atoms[i].position for i in row.atoms[:half]], axis=0)
                end = np.mean([structure.atoms[i].position for i in row.atoms[half:]], axis=0)
            plotter.add_mesh(
                pv.Line(start, end),
                color=colours[row.kind],
                line_width=3,
                name=f"interaction_{n}",
                lighting=False,
                pickable=False,
            )
        result["interactions"] = [{**asdict(r), "atoms": [i + 1 for i in r.atoms]} for r in rows]
    return result
