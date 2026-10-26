"""Renderers for the ``properties.*`` kinds (#161).

QVF spec § 5.13 defines three property kinds, each one JSON member with fixed
units: ``properties.electric`` (multipole moments and polarizabilities),
``properties.elastic`` (the elastic constants of a crystal) and
``properties.dielectric`` (dielectric tensors, Born charges and the
piezoelectric tensor). They are shown as tables, and an electric dipole is
also drawn as an arrow from its ``origin``.

Two derived numbers are shown beside the stored ones, as checks rather than
verdicts:

* **Mechanical stability.** A crystal is mechanically stable when its
  stiffness matrix is positive definite, the Born criterion in its general
  form (Mouhat & Coudert, *Phys. Rev. B* **90**, 224104, 2014). An unstable
  stiffness is legitimate data (§ 5.13); the panel only says which it is.
* **Acoustic sum rule.** The Born charges of all atoms sum to zero for a
  converged calculation (charge neutrality), so the panel shows Σ Z*.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    import pyvista as pv

    from vibeview.qvf import Atom, PropertiesData, QVFReader, Section

#: Actor-name prefix of a section's dipole arrow (``_Actors.PROPERTY``).
PROPERTY_PREFIX = "property_dipole_"
#: The root ``dipole_moment`` arrow drawn with the structure.
ROOT_DIPOLE_NAME = "dipole_arrow"
AXES = ("x", "y", "z")
VOIGT = ("xx", "yy", "zz", "yz", "xz", "xy")
_ELECTRIC_UNITS = {
    "dipole": "D", "quadrupole": "D·Å", "octupole": "D·Å²",
    "polarizability": "a.u.", "hyperpolarizability": "a.u.",
}
_MODULI = (
    ("bulk_modulus_gpa", "bulk modulus", "GPa"),
    ("shear_modulus_gpa", "shear modulus", "GPa"),
    ("youngs_modulus_gpa", "Young's modulus", "GPa"),
    ("poisson_ratio", "Poisson's ratio", ""),
)


# ── dipole arrow ────────────────────────────────────────────────────────────


def dipole_arrow_mesh(anchor: np.ndarray, vector_debye: np.ndarray) -> pv.PolyData | None:
    """The dipole arrow: 0.3 Å per debye, at most 3 Å, from ``anchor`` (Å).

    ``None`` for a dipole below 0.001 D. Shared by the root ``dipole_moment``
    arrow and a ``properties.electric`` section's, so both have one scale.
    """
    import pyvista as pv

    vector = np.asarray(vector_debye, dtype=float)
    magnitude = float(np.linalg.norm(vector))
    if magnitude <= 0.001:
        return None
    scale = min(0.3, 3.0 / magnitude) * magnitude
    mesh: pv.PolyData = pv.Arrow(
        start=np.asarray(anchor, dtype=float), direction=vector, scale=scale,
        tip_radius=0.15, shaft_radius=0.06,
    )
    return mesh


def dipole_anchor(origin: Any, atoms: list[Atom]) -> np.ndarray:
    """Where a dipole with this ``origin`` is drawn, in Å (QVF spec § 3.3).

    An explicit point is in bohr; ``"center_of_mass"`` uses standard masses,
    falling back to the centroid when an element has none;
    ``"center_of_nuclear_charge"`` weights by atomic number; ``"origin"`` is
    the coordinate origin. Anything else, or no atoms, is the centroid.
    """
    from vibeview.symmetry import standard_mass
    from vibeview.units import BOHR_TO_ANGSTROM

    if isinstance(origin, list) and len(origin) == 3:
        return np.asarray(origin, dtype=float) * BOHR_TO_ANGSTROM
    if origin == "origin":
        return np.zeros(3)
    if not atoms:
        return np.zeros(3)
    positions = np.asarray([a.position for a in atoms], dtype=float)
    weights: list[float] | None = None
    if origin == "center_of_mass":
        masses = [standard_mass(a.atomic_number) for a in atoms]
        weights = None if any(m is None for m in masses) else [float(m or 0) for m in masses]
    elif origin == "center_of_nuclear_charge":
        weights = [float(a.atomic_number) for a in atoms]
    if weights is None or sum(weights) <= 0:
        return positions.mean(axis=0)
    w = np.asarray(weights)
    return (positions * w[:, None]).sum(axis=0) / w.sum()


# ── checks ──────────────────────────────────────────────────────────────────


def is_mechanically_stable(stiffness: Any) -> bool:
    """Whether the stiffness is positive definite (Born criterion; see the module)."""
    matrix = np.asarray(stiffness, dtype=float)
    return bool(np.all(np.linalg.eigvalsh(0.5 * (matrix + matrix.T)) > 0))


def born_charge_sum(born_charges: Any) -> np.ndarray:
    """Σ Z* over the atoms, a 3 × 3 matrix that is zero when charge is conserved."""
    return np.asarray(born_charges, dtype=float).sum(axis=0)


# ── rows ────────────────────────────────────────────────────────────────────


def _atom(symbols: list[str] | None, index: int) -> str:
    if symbols is not None and 0 <= index < len(symbols):
        return f"{symbols[index]}{index + 1}"
    return f"atom {index + 1}"


def _matrix_rows(quantity: str, matrix: Any, labels: tuple[str, ...], unit: str,
                 prefix: str = "") -> list[list[Any]]:
    array = np.asarray(matrix, dtype=float)
    return [
        [quantity, f"{prefix}{labels[i]}{labels[j]}" if len(labels) == 3
         else f"{prefix}C{i + 1}{j + 1}", float(array[i, j]), unit]
        for i in range(array.shape[0]) for j in range(array.shape[1])
    ]


def _tensor_rows(quantity: str, tensor: Any, unit: str) -> list[list[Any]]:
    flat = np.asarray(tensor, dtype=float)
    return [
        [quantity, AXES[a] + AXES[b] + AXES[c], float(flat[9 * a + 3 * b + c]), unit]
        for a in range(3) for b in range(3) for c in range(3)
    ]


PROPERTY_COLUMNS = ["quantity", "component", "value", "unit"]


def property_rows(data: PropertiesData, symbols: list[str] | None = None) -> list[list[Any]]:
    """One row per stored number: quantity, component, value, unit."""
    p = data.payload
    rows: list[list[Any]] = []
    if data.kind == "properties.electric":
        dipole = p.get("dipole")
        if dipole:
            rows += [["dipole", a, float(v), "D"] for a, v in zip(AXES, dipole["vector"],
                                                                  strict=True)]
            total = dipole.get("total", float(np.linalg.norm(dipole["vector"])))
            rows.append(["dipole", "total", float(total), "D"])
        for key in ("quadrupole", "polarizability"):
            block = p.get(key)
            if block:
                rows += _matrix_rows(key, block["matrix"], AXES, _ELECTRIC_UNITS[key])
        if p.get("polarizability") and "isotropic" in p["polarizability"]:
            rows.append(["polarizability", "isotropic",
                         float(p["polarizability"]["isotropic"]), "a.u."])
        for key in ("octupole", "hyperpolarizability"):
            block = p.get(key)
            if block:
                rows += _tensor_rows(key, block["tensor"], _ELECTRIC_UNITS[key])
    elif data.kind == "properties.elastic":
        rows += _matrix_rows("stiffness", p["stiffness"], VOIGT, "GPa")
        if p.get("compliance") is not None:
            rows += _matrix_rows("compliance", p["compliance"], VOIGT, "1/GPa")
        for key, name, unit in _MODULI:
            if p.get(key) is not None:
                rows.append([name, p.get("average") or "", float(p[key]), unit])
    elif data.kind == "properties.dielectric":
        for key, name in (("electronic", "epsilon_inf"), ("ionic", "epsilon_ionic"),
                          ("static", "epsilon_0")):
            if p.get(key) is not None:
                rows += _matrix_rows(name, p[key], AXES, "")
        for index, tensor in enumerate(p.get("born_charges") or []):
            rows += _matrix_rows("born_charge", tensor, AXES, "e",
                                 prefix=f"Z*({_atom(symbols, index)}) ")
        if p.get("piezoelectric") is not None:
            array = np.asarray(p["piezoelectric"], dtype=float)
            rows += [["piezoelectric", f"{AXES[i]},{VOIGT[j]}", float(array[i, j]), "C/m2"]
                     for i in range(3) for j in range(6)]
    return rows


# ── summaries and panels ────────────────────────────────────────────────────


def summary(data: PropertiesData) -> str:
    """One status line: the headline numbers of the section."""
    p = data.payload
    parts: list[str] = []
    if data.kind == "properties.electric":
        if p.get("dipole"):
            parts.append(f"|μ| {np.linalg.norm(p['dipole']['vector']):.4g} D")
        if p.get("polarizability"):
            alpha = np.asarray(p["polarizability"]["matrix"], dtype=float)
            parts.append(f"isotropic α {np.trace(alpha) / 3:.4g} a.u.")
        for key in ("quadrupole", "octupole", "hyperpolarizability"):
            if p.get(key):
                parts.append(key)
    elif data.kind == "properties.elastic":
        stable = is_mechanically_stable(p["stiffness"])
        parts.append("mechanically stable" if stable else "not mechanically stable")
        if p.get("bulk_modulus_gpa") is not None:
            parts.append(f"K {float(p['bulk_modulus_gpa']):.4g} GPa")
    elif data.kind == "properties.dielectric":
        parts.append(f"ε∞ {np.trace(np.asarray(p['electronic'], dtype=float)) / 3:.4g}")
        if p.get("static") is not None:
            parts.append(f"ε₀ {np.trace(np.asarray(p['static'], dtype=float)) / 3:.4g}")
        if p.get("born_charges") is not None:
            parts.append(f"Born charges for {len(p['born_charges'])} atoms")
    return ", ".join(parts)


def _matrix_html(matrix: Any, labels: tuple[str, ...], caption: str) -> str:
    from vibeview.renderers._html import data_table

    array = np.asarray(matrix, dtype=float)
    return data_table(
        ["", *labels[: array.shape[1]]],
        [[labels[i], *array[i]] for i in range(array.shape[0])],
        caption=caption, numeric_format=".4g",
    )


def properties_html(data: PropertiesData, symbols: list[str] | None = None) -> str:
    """The details panel for any of the three kinds."""
    from vibeview.renderers._html import data_table, kv_table, note, panel_document

    p = data.payload
    blocks: list[str] = []
    if data.kind == "properties.electric":
        origin = p.get("origin")
        facts: list[tuple[str, object]] = [("Origin", origin if origin is not None else "—")]
        if p.get("dipole"):
            vector = np.asarray(p["dipole"]["vector"], dtype=float)
            facts.append(("Dipole (D)", ", ".join(f"{v:.4g}" for v in vector)))
            facts.append(("|μ| (D)", f"{np.linalg.norm(vector):.4g}"))
        blocks.append(kv_table(facts))
        if p.get("quadrupole"):
            q = p["quadrupole"]
            blocks.append(_matrix_html(q["matrix"], AXES, (
                f"Quadrupole (D·Å), {q.get('convention')}, "
                f"{'traceless' if q.get('traceless') else 'with trace'}"
            )))
        if p.get("polarizability"):
            a = p["polarizability"]
            alpha = np.asarray(a["matrix"], dtype=float)
            blocks.append(_matrix_html(alpha, AXES, (
                f"Polarizability α (a.u.), isotropic {np.trace(alpha) / 3:.4g}, "
                f"at {float(a.get('frequency_ev', 0.0)):g} eV"
            )))
        for key, caption in (("octupole", "Octupole (D·Å²)"),
                             ("hyperpolarizability", "Hyperpolarizability β (a.u.)")):
            if p.get(key):
                tensor = np.asarray(p[key]["tensor"], dtype=float).reshape(3, 3, 3)
                blocks += [
                    _matrix_html(tensor[a], AXES, f"{caption}, first index {AXES[a]}")
                    for a in range(3)
                ]
    elif data.kind == "properties.elastic":
        stable = is_mechanically_stable(p["stiffness"])
        blocks.append(note(
            "The stiffness is positive definite: mechanically stable (Born criterion)."
            if stable else
            "The stiffness is not positive definite: not mechanically stable (Born criterion)."
        ))
        blocks.append(_matrix_html(p["stiffness"], VOIGT, "Stiffness C (GPa), Voigt notation"))
        if p.get("compliance") is not None:
            blocks.append(_matrix_html(p["compliance"], VOIGT, "Compliance S (1/GPa)"))
        moduli = [(name + (f" ({unit})" if unit else ""), f"{float(p[key]):.4g}")
                  for key, name, unit in _MODULI if p.get(key) is not None]
        if moduli:
            scheme = p.get("average") or "not stated"
            blocks.append(kv_table([*moduli, ("Averaging", scheme)]))
    elif data.kind == "properties.dielectric":
        for key, caption in (("electronic", "ε∞ (electronic)"), ("ionic", "Ionic contribution"),
                             ("static", "ε₀ (static)")):
            if p.get(key) is not None:
                eps = np.asarray(p[key], dtype=float)
                isotropic = np.trace(eps) / 3
                blocks.append(_matrix_html(eps, AXES, f"{caption}, isotropic {isotropic:.4g}"))
        charges = p.get("born_charges")
        if charges is not None:
            rows = [[_atom(symbols, i), *np.asarray(z, dtype=float).ravel(),
                     float(np.trace(np.asarray(z, dtype=float)) / 3)]
                    for i, z in enumerate(charges)]
            blocks.append(data_table(
                ["Atom", *[a + b for a in AXES for b in AXES], "⅓ tr"], rows,
                caption="Born effective charges Z* (e)", numeric_format=".4g",
            ))
            total = born_charge_sum(charges)
            blocks.append(note(
                f"Acoustic sum Σ Z*: largest entry {np.abs(total).max():.3g} e "
                "(zero for a converged calculation)."
            ))
        if p.get("piezoelectric") is not None:
            array = np.asarray(p["piezoelectric"], dtype=float)
            blocks.append(data_table(
                ["", *VOIGT], [[AXES[i], *array[i]] for i in range(3)],
                caption="Piezoelectric tensor e (C/m²)", numeric_format=".4g",
            ))
    return panel_document("".join(blocks))


class PropertiesRenderer(BaseRenderer):
    """Tables (and, for the electric kind, a dipole arrow) of a ``properties.*`` section."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: PropertiesData | None = None

    def load(self) -> PropertiesData:
        if self._data is None:
            self._data = self.reader.read_properties(self.section_id)
        return self._data
