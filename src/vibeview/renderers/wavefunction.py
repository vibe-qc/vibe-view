"""Wavefunction renderer — evaluates molecular orbitals from a GTO basis on
a Cartesian grid (`wavefunction.gto`, § 1.5 of the design doc).

The Molden / libint convention is enforced by the file format: contraction
coefficients apply to primitives normalized by QVF spec Appendix A.1's
``N_i``, exponents are in bohr^-2, the spherical (pure) ordering is
m = -l, ..., +l, and Cartesian is libint lexicographic.

``N_i`` is one factor per shell, derived from the total ``l``, so for
Cartesian shells the primitives are not individually unit-normalized
(``<xy|xy> = 1/3`` for a d shell). This reader must not add a
per-component correction on top of it -- see :func:`_angular_terms`.

This renderer evaluates orbitals up to l = 3 (s, p, d, f) in either pure
or Cartesian form. Shells with l > 3 are silently skipped — that loses
accuracy on high-l basis sets but keeps the viewer usable for the
overwhelming majority of files.

Every field is evaluated in atomic units and returned as a dense
``[nx, ny, nz]`` float32 volume on a :class:`~vibeview.qvf.GridData` laid
out in Å (``units="angstrom"``), so the same isosurface helpers that draw a
stored ``volume.*`` section draw these.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from vibeview.qvf import GridData
from vibeview.renderers import BaseRenderer
from vibeview.renderers._plotly import DEFAULT_HEIGHT, chart_layout, figure_to_html
from vibeview.renderers.palette import ACCENT_BLUE, ACCENT_RED
from vibeview.renderers.volume import apply_component
from vibeview.units import ANGSTROM_TO_BOHR as _ANGSTROM_TO_BOHR
from vibeview.units import BOHR_TO_ANGSTROM as _BOHR_TO_ANGSTROM
from vibeview.units import (
    HARTREE_TO_EV,
    energy_factor,
    energy_from_hartree,
    energy_label,
    format_energy,
)
from vibeview.units import energy_unit as _energy_unit_key

if TYPE_CHECKING:
    from vibeview.qvf import BasisShell, QVFReader, Section, StructureData, WavefunctionGTOData



@dataclass(frozen=True)
class _MolecularBox:
    """A sampling box around a molecule: axes in bohr, the grid in Å."""

    xs: np.ndarray
    ys: np.ndarray
    zs: np.ndarray
    step_bohr: np.ndarray
    grid: GridData


def _molecular_grid(
    positions_bohr: np.ndarray, padding_bohr: float, n_per_dim: int
) -> _MolecularBox:
    """The one bounding box every molecular field is sampled on.

    ``n_per_dim`` points per axis over the atoms' bounding box padded by
    ``padding_bohr``; the returned ``grid`` places those samples in Å.
    """
    lo = positions_bohr.min(axis=0) - padding_bohr
    hi = positions_bohr.max(axis=0) + padding_bohr
    n = int(n_per_dim)
    step_bohr = (hi - lo) / max(n - 1, 1)
    grid = GridData(
        origin=(lo * _BOHR_TO_ANGSTROM).astype(np.float64),
        voxel_vectors=np.diag(step_bohr * _BOHR_TO_ANGSTROM).astype(np.float64),
        shape=(n, n, n),
        units="angstrom",
    )
    return _MolecularBox(
        np.linspace(lo[0], hi[0], n), np.linspace(lo[1], hi[1], n), np.linspace(lo[2], hi[2], n),
        step_bohr, grid,
    )


@dataclass(frozen=True)
class _ShellBlock:
    """One shell's share of an evaluation, from :meth:`_shell_blocks`."""

    shell: BasisShell
    ao_index: int
    deltas: tuple[np.ndarray, np.ndarray, np.ndarray]
    radial: np.ndarray
    radial_d: np.ndarray | None
    radial_dd: np.ndarray | None


# Which part of a (possibly complex) orbital value to draw: the shared
# vocabulary of renderers.volume, minus ``density`` (an orbital's density is
# a different field, evaluate_density).
_ORBITAL_COMPONENTS = ("real", "imag", "magnitude")

_OCC_THRESHOLD = 1e-3  # occupation above which an MO counts as occupied
#: Tolerance on the occupation sum against the provenance electron count.
OCC_ELECTRON_TOL = 0.05
#: Tolerance for "is an integer" on a canonical occupation.
OCC_INTEGER_TOL = 1e-6
_OCC_ELECTRON_TOL = OCC_ELECTRON_TOL
_OCC_INTEGER_TOL = OCC_INTEGER_TOL
_SPIN_SHORT = {"restricted": "", "alpha": "α", "beta": "β"}
#: The two meanings a natural-orbital section may declare for ``occupations``.
ELECTRON_OCCUPATION = "electron_occupation"
TRANSITION_WEIGHT = "transition_weight"
_ELECTRON_OCCUPATION = ELECTRON_OCCUPATION
_TRANSITION_WEIGHT = TRANSITION_WEIGHT


def natural_value_semantics(data: object) -> str | None:
    """The declared meaning of a natural set's values, or None when unmarked.

    New archives state it in ``occupation_semantics``. An unmarked natural
    set is deliberately ``None``: its values may be electron occupations or
    transition weights, and neither a section id nor their sum can tell.
    """
    explicit = getattr(data, "occupation_semantics", None)
    if isinstance(explicit, str) and explicit in {ELECTRON_OCCUPATION, TRANSITION_WEIGHT}:
        return explicit
    return None


def frontier_orbitals(
    occ: np.ndarray | None,
    n_orbitals: int,
    orbital_kind: str | None,
    n_electrons: int | float | None = None,
    occupation_semantics: str | None = None,
) -> tuple[int | None, int | None, str | None]:
    """``(homo, lumo, undefined_reason)`` for one spin block.

    The one frontier rule for the browser picker, the energy diagram and
    the terminal panes. An orbital is occupied or it is not: canonical
    sets and natural sets with explicit electron-occupation semantics hold
    an electron count per MO, and rounding to nearest is the frontier test
    (robust to a JSON round trip: 1.9999999 is occupied, 0.4 is not).

    Where a canonical set's occupations are not integers, the HOMO and
    LUMO do not exist: smeared or finite-temperature occupations have a
    Fermi level, not an orbital index. Natural orbitals are fractionally
    occupied by construction, and the literature does speak of a highest
    occupied one: Pulay & Hamilton, J. Chem. Phys. 88, 4926 (1988),
    doi:10.1063/1.454704, § II, classify UHF natural orbitals by occupancy
    into doubly occupied, fractionally occupied and virtual with a window
    they call "somewhat arbitrary"; rounding to nearest is looser and only
    moves the frontier outward. A natural set names a frontier only when
    its values are declared electron occupations and, when the provenance
    records ``n_electrons``, sum to it. Localized orbitals are not energy
    ordered, so "highest" has no referent for them.

    ``undefined_reason`` says why no frontier is named; the LUMO is the
    lowest virtual *above* the HOMO, so a non-aufbau hole below a filled
    level cannot invert the reported gap.
    """
    if occ is None or n_orbitals == 0:
        return None, None, None
    n = min(len(occ), n_orbitals)
    if n == 0:
        return None, None, None
    if orbital_kind == "localized":
        return None, None, "localized orbitals are not energy ordered"

    counts = np.asarray(occ[:n], dtype=float)
    fractional = not np.allclose(counts, np.rint(counts), atol=OCC_INTEGER_TOL)
    if orbital_kind == "natural":
        if occupation_semantics == TRANSITION_WEIGHT:
            return None, None, (
                "transition weights (natural transition orbitals), not electron "
                "occupations, so no frontier is named"
            )
        if occupation_semantics != ELECTRON_OCCUPATION:
            return None, None, (
                "the archive does not declare whether these natural-orbital values "
                "are electron occupations or transition weights, so no frontier is named"
            )
        if n_electrons is not None:
            total = float(np.sum(counts))
            if abs(total - float(n_electrons)) > OCC_ELECTRON_TOL:
                return None, None, (
                    f"occupations sum to {total:.4g}, not the {n_electrons} electrons in "
                    "this system: this is a partial or inconsistent occupation set"
                )
    elif fractional:
        return None, None, (
            "fractional occupations (smearing or an ensemble), so the frontier is a Fermi"
            " level rather than an orbital"
        )

    filled = np.rint(counts) > 0
    occupied = np.flatnonzero(filled)
    homo = int(occupied[-1]) if occupied.size else None
    search_from = 0 if homo is None else homo + 1
    empty = np.flatnonzero(~filled[search_from:])
    lumo = int(empty[0] + search_from) if empty.size else None
    return homo, lumo, None


def _block_frontier(
    orbital_kind: str | None,
    occupations: np.ndarray | None,
    n_mo: int,
    *,
    natural_confirmed: bool,
    n_electrons: int | float | None = None,
) -> tuple[int, int]:
    """Frontier pair as indices for one spin block, ``-1`` where none exists.

    A thin adapter over :func:`frontier_orbitals`, whose reasons the MO
    picker and the energy diagram do not display; ``natural_confirmed`` is
    the caller's answer to "are these natural values electron counts".
    """
    semantics = ELECTRON_OCCUPATION if natural_confirmed else None
    homo, lumo, _reason = frontier_orbitals(
        occupations, n_mo, orbital_kind, n_electrons, semantics
    )
    return (-1 if homo is None else homo, -1 if lumo is None else lumo)


def occupation_problem(
    wf: WavefunctionGTOData, semantics: str | None, n_electrons: int | float | None
) -> str | None:
    """Why ``wf``'s occupations are not electron counts, or None when they are.

    Shared by the browser's density and frontier logic and the terminal's
    density availability check, so both say the same thing about the same
    archive. Only a natural set can fail: canonical occupations are electron
    counts by definition.
    """
    blocks = (
        (wf.alpha_occupations, wf.beta_occupations)
        if wf.spin == "unrestricted"
        else (wf.occupations,)
    )
    if any(block is None for block in blocks):
        return "this wavefunction has no occupations"
    if wf.orbital_kind != "natural":
        return None
    if semantics == TRANSITION_WEIGHT:
        return (
            "natural transition orbitals carry transition weights, not electron occupations"
        )
    if semantics != ELECTRON_OCCUPATION:
        return (
            "this natural-orbital section does not declare whether its values are "
            "electron occupations or transition weights"
        )
    total = sum(float(np.asarray(block, dtype=float).sum()) for block in blocks)
    if n_electrons is not None and abs(total - float(n_electrons)) > OCC_ELECTRON_TOL:
        return (
            f"values sum to {total:.4g}, not {n_electrons} electrons; this is a partial or "
            "inconsistent occupation set"
        )
    return None


def _mo_row_title(
    spin_label: str,
    index: int,
    energy: float | None,
    occ: float | None,
    sym_label: str,
    marker: str,
    occupation_label: str = "occ",
    energy_display_unit: str = "hartree",
) -> str:
    """Compose a self-describing MO picker label, e.g.
    ``"α #12  −0.314 Eh  occ 1.00  HOMO  (a1)"``.

    ``energy`` is in Hartree; ``energy_display_unit`` picks how it reads."""
    prefix = _SPIN_SHORT.get(spin_label, "")
    head = f"{prefix} #{index}" if prefix else f"#{index}"
    parts = [head.strip()]
    if energy is not None:
        parts.append(format_energy(energy, energy_display_unit))
    if occ is not None:
        parts.append(f"{occupation_label} {occ:.2f}")
    if marker:
        parts.append(marker.strip())
    if sym_label:
        parts.append(f"({sym_label})")
    return "  ".join(parts)


def _localized_row_title(
    index: int,
    symbols: Sequence[str] | None,
    populations: np.ndarray | Sequence[float] | None,
    n_centres: int | None,
    threshold: float = 0.10,
) -> str:
    """Picker label for a localized orbital, e.g. ``"#3  C1-H4 bond  (2c)"``.

    A localized orbital has no orbital energy -- it is a unitary mixture of
    canonical orbitals spanning a range of eigenvalues -- so the energy that
    identifies a canonical MO is meaningless here. What identifies an IBO is
    *which atoms it sits on*, so the label is built from its atomic
    populations instead.
    """
    parts = [f"#{index}"]
    if populations is not None:
        weights = np.asarray(populations, dtype=float)
        order = np.argsort(weights)[::-1]
        centres = [int(a) for a in order if weights[a] > threshold]
        if centres:
            def _name(atom: int) -> str:
                if symbols is not None and atom < len(symbols):
                    return f"{symbols[atom]}{atom + 1}"
                return f"atom{atom + 1}"

            if len(centres) == 1:
                parts.append(f"{_name(centres[0])} core/lone pair")
            else:
                parts.append("-".join(_name(a) for a in centres[:3]) + " bond")
    if n_centres is not None:
        parts.append(f"({int(n_centres)}c)")
    return "  ".join(parts)


class WavefunctionRenderer(BaseRenderer):
    """`wavefunction.gto` renderer.

    Use `evaluate_mo(index)` to sample an MO on a grid that this renderer
    auto-sizes from the parent structure's atomic bounding box, then hand
    the resulting `(GridData, np.ndarray)` to `VolumeRenderer` (via the
    app) for isosurface rendering.
    """

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._wf: WavefunctionGTOData | None = None
        self._centre_structures: dict[str, StructureData] = {}
        # Diagnostics from the most recent evaluate_mo() call. l>3 shells
        # are not yet evaluated (renderer scope is s,p,d,f), so a fraction
        # of the orbital's weight may be dropped; we surface it (A2-04).
        self.last_dropped_l_fraction: float = 0.0
        self.last_dropped_l_max: int = 0

    def load(self) -> WavefunctionGTOData:
        if self._wf is None:
            self._wf = self.reader.read_wavefunction_gto(self.section_id)
        return self._wf

    @property
    def n_mo(self) -> int:
        wf = self.load()
        if wf.spin == "unrestricted":
            return 0 if wf.mo_coefficients_alpha is None else int(wf.mo_coefficients_alpha.shape[0])
        return 0 if wf.mo_coefficients is None else int(wf.mo_coefficients.shape[0])

    def _natural_occupations_confirmed(self, wf: WavefunctionGTOData) -> bool:
        """True when a natural set's ``occupations`` are electron counts.

        Requires the producer's explicit ``electron_occupation`` semantics
        and, when the provenance records ``n_electrons``, a matching sum;
        transition weights or an unmarked set never confirm.
        """
        if wf.orbital_kind != "natural":
            return False
        return occupation_problem(wf, self.natural_value_semantics(), self.electron_count()) is None

    def electron_count(self) -> int | float | None:
        """The provenance's ``n_electrons``, when the archive records one."""
        value = self.reader.provenance.get("n_electrons")
        return value if isinstance(value, int | float) else None

    def natural_value_semantics(self) -> str | None:
        """Meaning of ``occupations`` for a natural-orbital section.

        New archives state this explicitly. An unmarked natural set is
        deliberately ``None``: its values may be electron occupations or
        transition weights, and neither a section id nor their sum can
        distinguish those cases reliably.
        """
        return natural_value_semantics(self.load())

    def mo_table(self, energy_unit: str = "hartree") -> list[dict[str, Any]]:
        """Rows describing every MO, for the UI picker.

        ``energy_unit`` (a ``vibeview.units.ENERGY_UNITS`` key) sets the unit
        of the energy in each ``title``; ``energy_eh`` stays in Hartree.

        Each row carries a **composite** ``value`` of the form
        ``"{spin}:{index}"`` and a human-readable ``title`` (energy,
        occupation, HOMO/LUMO marker). The composite value is essential for
        unrestricted wavefunctions: alpha and beta blocks both run
        ``index = 0..N``, so keying the dropdown on the bare integer made
        the alpha-N and beta-N rows indistinguishable and let the picker
        render the wrong-spin orbital (audit finding A2-03). ``render_mo``
        parses spin + index back out of ``value``.
        """
        wf = self.load()
        rows: list[dict[str, Any]] = []
        blocks: tuple[tuple[str, np.ndarray | None, np.ndarray | None, np.ndarray | None], ...]
        if wf.spin == "unrestricted":
            blocks = (
                ("alpha", wf.alpha_energies, wf.alpha_occupations, wf.mo_coefficients_alpha),
                ("beta", wf.beta_energies, wf.beta_occupations, wf.mo_coefficients_beta),
            )
        else:
            blocks = (("restricted", wf.energies, wf.occupations, wf.mo_coefficients),)

        # A localized set is all-occupied by construction, so the
        # occupation-based frontier detection would tag the last orbital as
        # "HOMO" — meaningless for an IBO, and actively misleading in the
        # picker. Suppress it, and label by atomic composition instead.
        localized = wf.orbital_kind == "localized"
        natural = wf.orbital_kind == "natural"
        natural_semantics = self.natural_value_semantics() if natural else None
        symbols = self._structure_symbols(wf.structure_ref) if localized else None

        natural_occupations_confirmed = self._natural_occupations_confirmed(wf)

        for spin_label, energies, occs, coeffs in blocks:
            # β orbitals carry their own symmetry labels; α + restricted use
            # symmetry_labels. Previously both spins indexed the single collapsed
            # list, so β showed α's labels (audit L7).
            labels = (
                wf.symmetry_labels_beta if spin_label == "beta" else wf.symmetry_labels
            )
            n = 0 if coeffs is None else int(coeffs.shape[0])
            homo, lumo = _block_frontier(
                wf.orbital_kind, occs, n, natural_confirmed=natural_occupations_confirmed
            )
            for i in range(n):
                e = float(energies[i]) if energies is not None and i < len(energies) else None
                occ = float(occs[i]) if occs is not None and i < len(occs) else None
                lbl = labels[i] if labels and i < len(labels) else ""
                frontier_names = ("HONO", "LUNO") if natural else ("HOMO", "LUMO")
                marker = (
                    f" {frontier_names[0]}"
                    if i == homo
                    else (f" {frontier_names[1]}" if i == lumo else "")
                )
                if localized:
                    populations = (
                        wf.atom_populations[i]
                        if wf.atom_populations is not None
                        and i < len(wf.atom_populations)
                        else None
                    )
                    centres = (
                        wf.n_centres[i]
                        if wf.n_centres is not None and i < len(wf.n_centres)
                        else None
                    )
                    title = _localized_row_title(i, symbols, populations, centres)
                    # A localized orbital has no eigenvalue; don't imply one.
                    e = None
                else:
                    # A natural orbital's eigenvalue is its occupation, not
                    # the explicit zero placeholder carried in QVF metadata.
                    if natural:
                        e = None
                    title = _mo_row_title(
                        spin_label,
                        i,
                        e,
                        occ,
                        lbl,
                        marker,
                        occupation_label=(
                            "occ"
                            if natural_semantics == _ELECTRON_OCCUPATION
                            else (
                                "weight"
                                if natural_semantics == _TRANSITION_WEIGHT
                                else "value"
                            )
                        )
                        if natural
                        else "occ",
                        energy_display_unit=energy_unit,
                    )
                rows.append(
                    {
                        "index": i,
                        "spin": spin_label,
                        "value": f"{spin_label}:{i}",
                        "energy_eh": e,
                        "occupation": occ,
                        "label": lbl,
                        "title": title,
                    }
                )
        return rows

    def render_energy_diagram(
        self, include_plotlyjs: str | bool = "cdn", energy_unit: str = "hartree"
    ) -> str:
        """Build an orbital energy-level diagram as an HTML chart.

        Returns an HTML string with an inline Plotly figure showing each
        MO as a horizontal line at its energy, coloured by occupation
        (blue = occupied, red = virtual).  HOMO and LUMO are highlighted
        and the HOMO-LUMO gap is annotated. ``energy_unit`` (a
        ``vibeview.units.ENERGY_UNITS`` key) sets the axis unit; the gap
        reads in that unit, or in eV when the axis is in Hartree.
        """
        import plotly.graph_objects as go

        wf = self.load()
        if wf.orbital_kind == "localized":
            # Localized orbitals carry no eigenvalue -- the emitter writes
            # explicit zeros -- so an energy-level diagram would be a row of
            # coincident lines at 0 Eh masquerading as a spectrum. Say what
            # the set actually is instead of drawing a misleading axis.
            method = (wf.localization_method or "localized").upper()
            n_orb = 0 if wf.mo_coefficients is None else int(wf.mo_coefficients.shape[0])
            return (
                '<div style="padding:1.5rem;font-family:sans-serif;color:#555">'
                f"<b>{method} orbitals have no orbital energies.</b><br>"
                f"These {n_orb} localized occupied orbitals are a unitary "
                "mixture of the canonical set, so no eigenvalue spectrum "
                "exists for them. Use the orbital picker, which labels each "
                "one by the atoms it sits on."
                "</div>"
            )

        blocks: list[tuple[str, np.ndarray | None, np.ndarray | None]] = []
        if wf.spin == "unrestricted":
            blocks = [
                ("α", wf.alpha_energies, wf.alpha_occupations),
                ("β", wf.beta_energies, wf.beta_occupations),
            ]
        else:
            blocks = [("", wf.energies, wf.occupations)]
        natural = wf.orbital_kind == "natural"
        natural_confirmed = self._natural_occupations_confirmed(wf)
        frontier_names = ("HONO", "LUNO") if natural else ("HOMO", "LUMO")

        unit = _energy_unit_key(energy_unit)
        fig = go.Figure()
        gap_eh: float | None = None
        # Track the largest block for the figure height; also guards the
        # NameError when every block has energies=None (n was unbound).
        n_max = 0
        for spin_label, energies, occs in blocks:
            if energies is None:
                continue
            e_eh = np.asarray(energies, dtype=float)
            e = e_eh * energy_factor(unit)
            occ = np.asarray(occs, dtype=float) if occs is not None else np.zeros_like(e)
            n = len(e)
            if n == 0:
                continue
            n_max = max(n_max, n)
            # The same rule as the MO picker: fractional canonical occupations
            # (smearing, ensembles) and unconfirmed natural sets have no
            # frontier pair, so they get no diamonds and no gap annotation.
            homo_idx, lumo_idx = _block_frontier(
                wf.orbital_kind, occs, n, natural_confirmed=natural_confirmed
            )
            if homo_idx >= 0 and lumo_idx >= 0:
                gap_eh = float(e_eh[lumo_idx] - e_eh[homo_idx])
            colours = [ACCENT_BLUE if occ_i > 0.001 else ACCENT_RED for occ_i in occ]
            prefix = f"{spin_label} " if spin_label else ""
            label = f"{prefix}MO" if spin_label else "MO"
            # Composite render key per point ("{spin}:{index}"), carried as
            # customdata so a click on any marker can drive render_mo. The
            # y-labels use α/β display prefixes; the render layer wants the
            # spin names alpha/beta/restricted.
            spin_key = {"α": "alpha", "β": "beta", "": "restricted"}.get(spin_label, "restricted")
            keys = [f"{spin_key}:{i}" for i in range(n)]
            fig.add_trace(
                go.Scatter(
                    x=e,
                    y=[f"{prefix}#{i}" for i in range(n)],
                    mode="lines+markers",
                    marker=dict(size=6),
                    line=dict(width=2),
                    name=label,
                    customdata=keys,
                )
            )
            # One marker trace for every level, coloured per point: a
            # 500-orbital diagram used to be a 500-trace figure.
            fig.add_trace(
                go.Scatter(
                    x=e,
                    y=[f"{prefix}#{i}" for i in range(n)],
                    mode="markers",
                    marker=dict(size=8, color=colours, line=dict(width=1, color="#333")),
                    showlegend=False,
                    hoverinfo="text",
                    text=[
                        f"{prefix}#{i}: {format_energy(e_eh[i], unit, extra_digits=1)}"
                        f" (occ {occ[i]:.2f})"
                        for i in range(n)
                    ],
                    customdata=keys,
                )
            )
            # HOMO / LUMO highlight markers
            if homo_idx >= 0:
                fig.add_trace(
                    go.Scatter(
                        x=[e[homo_idx]],
                        y=[f"{prefix}#{homo_idx}"],
                        mode="markers",
                        marker=dict(
                            size=12,
                            color=ACCENT_BLUE,
                            symbol="diamond",
                            line=dict(width=2, color="#fff"),
                        ),
                        showlegend=False,
                        hoverinfo="skip",
                        name=f"{prefix}{frontier_names[0]}",
                        customdata=[keys[homo_idx]],
                    )
                )
            if lumo_idx >= 0:
                fig.add_trace(
                    go.Scatter(
                        x=[e[lumo_idx]],
                        y=[f"{prefix}#{lumo_idx}"],
                        mode="markers",
                        marker=dict(
                            size=12,
                            color=ACCENT_RED,
                            symbol="diamond",
                            line=dict(width=2, color="#fff"),
                        ),
                        showlegend=False,
                        hoverinfo="skip",
                        name=f"{prefix}{frontier_names[1]}",
                        customdata=[keys[lumo_idx]],
                    )
                )

        title = "Orbital Energy Diagram"
        if wf.orbital_kind and wf.orbital_kind != "canonical":
            title = f"{wf.orbital_kind.capitalize()} Orbital Energy Diagram"
        if gap_eh is not None:
            if unit == "hartree":
                title += f" (gap {gap_eh * HARTREE_TO_EV:.2f} eV)"
            else:
                gap = energy_from_hartree(gap_eh, unit)
                title += f" (gap {gap:.2f} {energy_label(unit)})"

        # One row per level, so the diagram grows with the orbital count.
        chart_layout(
            fig,
            title=title,
            x_title=f"Energy ({energy_label(unit)})",
            yaxis={"showticklabels": False, "title": None},
            height=max(DEFAULT_HEIGHT, n_max * 8),
        )
        # Click-to-render: a click on any level posts its "{spin}:{index}"
        # render key to the parent window, which drives render_mo (design
        # refresh 2026: click a level in the energy diagram to render that
        # orbital). The diagram renders in a sandbox="allow-scripts" iframe,
        # so postMessage to the parent is the available channel.
        click_js = (
            "<script>(function(){function bind(){"
            "var gd=document.querySelector('.plotly-graph-div');"
            "if(!gd||!gd.on){setTimeout(bind,200);return;}"
            "gd.on('plotly_click',function(ev){"
            "var p=ev&&ev.points&&ev.points[0];if(!p)return;"
            "var key=p.customdata;if(Array.isArray(key))key=key[0];"
            "if(key==null)return;"
            "window.parent.postMessage("
            "{type:'vibeview-mo-click',key:String(key)},'*');});}"
            "bind();})();</script>"
        )
        return figure_to_html(fig, include_plotlyjs=include_plotlyjs, extra_script=click_js)

    def evaluate_mo(
        self,
        index: int,
        *,
        spin: str = "restricted",
        padding_bohr: float = 4.0,
        n_per_dim: int = 60,
        component: str = "real",
    ) -> tuple[GridData, np.ndarray]:
        """Evaluate an MO on a molecular box or explicitly tagged Gamma cell.

        Parameters
        ----------
        index:
            0-based row index into the MO coefficient matrix.
        spin:
            ``"restricted"`` (default) or ``"alpha"``/``"beta"`` for
            unrestricted wavefunctions.
        padding_bohr:
            Padding (in bohr) added to the atomic bounding box.
        n_per_dim:
            Grid resolution; total volume size is ``n_per_dim^3`` voxels.
        component:
            ``real`` (default), ``imag``, or ``magnitude``. Complex phase is
            preserved; only the selected display component is returned.

        Returns a `(GridData, np.ndarray)` pair whose ``GridData`` is in
        **angstroms** — DELIBERATELY different from the bohr convention of
        stored ``volume.*`` grids (see ``qvf.GridData``). This grid is
        consumed directly by ``app._render_mo_volume`` (which builds an
        ImageData straight from these Å spacings); it must NOT be fed to
        ``VolumeRenderer.make_mesh``, which multiplies by bohr→Å again and
        would double-convert. The MO isosurface is placed in Å world space
        so it co-registers with the Å atom coordinates.
        """
        wf = self.load()
        coeffs = self._mo_row(wf, index, spin)
        if component not in _ORBITAL_COMPONENTS:
            raise ValueError(f"Orbital component must be one of {', '.join(_ORBITAL_COMPONENTS)}")
        if wf.k_point is not None:
            from vibeview.renderers.periodic_wavefunction import evaluate_gamma_mo

            grid, values = evaluate_gamma_mo(self, wf, coeffs, n_per_dim)
            values = apply_component(values, component)
            return grid, values.astype(np.float32)

        positions_bohr = self._atom_positions_bohr(wf.structure_ref)
        # The evaluator works in bohr to match the exponents; the grid it
        # returns is laid out in Å beside the atoms.
        box = _molecular_grid(positions_bohr, padding_bohr, n_per_dim)
        values = self._evaluate_on_grid(wf, coeffs, positions_bohr, box.xs, box.ys, box.zs)
        grid = box.grid
        values = apply_component(values, component)
        return grid, values.astype(np.float32, copy=False)

    def evaluate_density(
        self, *, padding_bohr: float = 4.0, n_per_dim: int = 60
    ) -> tuple[GridData, np.ndarray, float]:
        """Compute the electron density ρ(r) = Σ_i occ_i |ψ_i(r)|² (Phase E3).

        Sums the occupied MOs across spin channels on a single auto-sized grid,
        reusing the same per-MO evaluator as :meth:`evaluate_mo`. Returns
        ``(grid [Å], density [nx, ny, nz], n_electrons)`` where
        ``n_electrons = ∫ρ dV`` (atomic units) is a self-consistency check
        against the sum of occupations; for a converged wavefunction it equals
        the electron count, slightly undercounting on coarse grids that
        undersample the sharp nuclear-cusp core orbitals.
        """
        return self._accumulate_density(
            beta_weight=1.0, padding_bohr=padding_bohr, n_per_dim=n_per_dim
        )

    def evaluate_spin_density(
        self, *, padding_bohr: float = 4.0, n_per_dim: int = 60
    ) -> tuple[GridData, np.ndarray, float]:
        """Compute the spin density ρ_α(r) − ρ_β(r).

        Where the unpaired electrons actually are: the positive lobes carry
        excess α spin, the negative lobes excess β. For a radical this is the
        picture that answers "where is the radical centre", and for an
        antiferromagnetically coupled pair it shows the alternation directly.

        Same machinery as :meth:`evaluate_density` with the β block entering
        at −1, so it needs no derivatives and no new data -- any unrestricted
        ``wavefunction.gto`` already in a file can produce it.

        Returns ``(grid [Å], spin density [nx, ny, nz], n_unpaired)`` where
        ``n_unpaired = ∫(ρ_α − ρ_β) dV`` should come out at N_α − N_β: 1 for
        a doublet, 2 for a triplet. That integral is the self-consistency
        check, exactly as the electron count is for the total density.

        Raises
        ------
        ValueError
            For a restricted wavefunction, where the spin density is
            identically zero and a plot of it would be noise about 0.
        """
        wf = self.load()
        if wf.spin != "unrestricted":
            raise ValueError(
                "spin density requires an unrestricted wavefunction; a "
                "restricted one has rho_alpha == rho_beta everywhere"
            )
        return self._accumulate_density(
            beta_weight=-1.0, padding_bohr=padding_bohr, n_per_dim=n_per_dim
        )

    def evaluate_elf(
        self,
        *,
        padding_bohr: float = 4.0,
        n_per_dim: int = 60,
        density_floor: float = 1e-8,
    ) -> tuple[GridData, np.ndarray]:
        """Compute the electron localization function.

        Becke & Edgecombe, *J. Chem. Phys.* **92**, 5397 (1990),
        doi:10.1063/1.458517 -- their eqs 9-13, transcribed at the call
        sites below. ELF is 1 where an electron pair is perfectly localized,
        1/2 where the pair probability is uniform-electron-gas-like, and
        falls to 0 in the tails; the shells, bonds and lone pairs a chemist
        expects show up as its maxima.

        **The convention matters and is easy to get wrong.** Becke's ELF is
        sigma-spin resolved: his eq 9 is ``tau_sigma = sum_i |grad psi_i|^2``
        with **no factor of 1/2**, and it pairs with
        ``D0 = (3/5)(6 pi^2)^(2/3) rho_sigma^(5/3)`` (eq 13), *not* the more
        familiar Thomas-Fermi ``(3/10)(3 pi^2)^(2/3)`` that goes with the
        half-convention and the total density. Mixing the two rescales chi
        and silently moves every contour.

        Returns ``(grid [Å], elf [nx, ny, nz])`` with ELF in [0, 1].

        Below ``density_floor`` the ratio is 0/0 -- both D and D0 vanish in
        vacuum -- so ELF is set to 0 there, which is also its asymptotic
        value (Becke Figs. 1-4).
        """
        wf = self.load()
        self._require_molecular_field(wf, real_only=True)
        positions_bohr = self._atom_positions_bohr(wf.structure_ref)
        box = _molecular_grid(positions_bohr, padding_bohr, n_per_dim)
        xs, ys, zs = box.xs, box.ys, box.zs
        nx = ny = nz = int(n_per_dim)

        # One spin channel. For a restricted wavefunction the doubly-occupied
        # spatial orbitals each hold exactly one alpha electron, so the
        # channel weight is occ/2; for an unrestricted one the alpha
        # occupations are already per-spin.
        if wf.spin == "unrestricted":
            coefficients = wf.mo_coefficients_alpha
            occupations = wf.alpha_occupations
            spin_scale = 1.0
        else:
            coefficients = wf.mo_coefficients
            occupations = wf.occupations
            spin_scale = 0.5
        if coefficients is None or occupations is None:
            raise ValueError("ELF needs MO coefficients and occupations")

        rho = np.zeros((nx, ny, nz), dtype=np.float64)
        grad_rho = np.zeros((3, nx, ny, nz), dtype=np.float64)
        tau = np.zeros((nx, ny, nz), dtype=np.float64)

        occ = np.asarray(occupations, dtype=float)
        for i in range(int(coefficients.shape[0])):
            if i >= occ.size or occ[i] <= _OCC_THRESHOLD:
                continue
            weight = float(occ[i]) * spin_scale
            psi, grad_psi = self._evaluate_gradient_on_grid(
                wf, coefficients[i], positions_bohr, xs, ys, zs
            )
            rho += weight * psi * psi
            # grad(rho_sigma) = sum_i w_i * 2 psi_i grad(psi_i)
            for axis in range(3):
                grad_rho[axis] += weight * 2.0 * psi * grad_psi[axis]
            # Becke eq 9: tau_sigma = sum_i |grad psi_i|^2, no 1/2.
            tau += weight * np.sum(grad_psi * grad_psi, axis=0)

        safe_rho = np.maximum(rho, density_floor)
        grad_rho_sq = np.sum(grad_rho * grad_rho, axis=0)
        # Becke eq 10: D_sigma = tau_sigma - (1/4) |grad rho_sigma|^2 / rho_sigma
        d_sigma = tau - 0.25 * grad_rho_sq / safe_rho
        # Becke eq 13: the uniform-gas reference at the local spin density.
        d_uniform = (3.0 / 5.0) * (6.0 * math.pi**2) ** (2.0 / 3.0) * safe_rho ** (
            5.0 / 3.0
        )
        # Becke eq 12 then eq 11.
        chi = d_sigma / np.maximum(d_uniform, 1e-300)
        elf = 1.0 / (1.0 + chi * chi)

        # D_sigma is non-negative in exact arithmetic (Becke, after eq 10);
        # grid noise can push it slightly negative in the tails, which would
        # read as spurious localization. Clamp rather than let it show.
        elf = np.where(rho > density_floor, elf, 0.0)
        elf = np.clip(elf, 0.0, 1.0)

        grid = box.grid
        return grid, elf.astype(np.float32)

    def evaluate_nci(
        self,
        *,
        padding_bohr: float = 4.0,
        n_per_dim: int = 60,
        density_floor: float = 1e-8,
    ) -> tuple[GridData, np.ndarray, np.ndarray]:
        """Reduced density gradient and sign(lambda2)*rho, for NCI analysis.

        Johnson, Keinan, Mori-Sanchez, Contreras-Garcia, Cohen & Yang,
        *J. Am. Chem. Soc.* **132**, 6498 (2010), doi:10.1021/ja100936w.

        Noncovalent interactions show up as regions of **low density and low
        reduced gradient** -- the spikes in their Fig. 1 that a monomer does
        not have and a dimer does. The reduced gradient (their § 2.1) is

            s = |grad rho| / (2 (3 pi^2)^(1/3) rho^(4/3))

        and the *kind* of interaction is read off the sign of lambda2, the
        middle eigenvalue of the density Hessian (their § 2.3):

            lambda2 < 0   attractive -- hydrogen bond, dipole-dipole
            lambda2 ~ 0   weak van der Waals
            lambda2 > 0   nonbonded, steric repulsion

        which is why the surface is coloured by ``sign(lambda2) * rho``
        rather than by rho alone: density gives the strength, the sign gives
        the character.

        Returns ``(grid [Å], s, signed_rho)``. Draw the isosurface of ``s``
        at 0.5 au and colour it by ``signed_rho`` over roughly [-0.05, 0.05]
        -- both from the paper (§ 3, Fig. 3).
        """
        wf = self.load()
        self._require_molecular_field(wf, real_only=True)
        positions_bohr = self._atom_positions_bohr(wf.structure_ref)
        box = _molecular_grid(positions_bohr, padding_bohr, n_per_dim)
        xs, ys, zs = box.xs, box.ys, box.zs
        nx = ny = nz = int(n_per_dim)

        blocks: tuple[tuple[np.ndarray | None, np.ndarray | None], ...]
        if wf.spin == "unrestricted":
            blocks = (
                (wf.mo_coefficients_alpha, wf.alpha_occupations),
                (wf.mo_coefficients_beta, wf.beta_occupations),
            )
        else:
            blocks = ((wf.mo_coefficients, wf.occupations),)

        rho = np.zeros((nx, ny, nz), dtype=np.float64)
        grad_rho = np.zeros((3, nx, ny, nz), dtype=np.float64)
        hess_rho = np.zeros((6, nx, ny, nz), dtype=np.float64)
        pairs = ((0, 0), (0, 1), (0, 2), (1, 1), (1, 2), (2, 2))

        for coefficients, occupations in blocks:
            if coefficients is None or occupations is None:
                continue
            occ = np.asarray(occupations, dtype=float)
            for i in range(int(coefficients.shape[0])):
                if i >= occ.size or occ[i] <= _OCC_THRESHOLD:
                    continue
                weight = float(occ[i])
                psi, grad_psi, hess_psi = self._evaluate_hessian_on_grid(
                    wf, coefficients[i], positions_bohr, xs, ys, zs
                )
                rho += weight * psi * psi
                for axis in range(3):
                    grad_rho[axis] += weight * 2.0 * psi * grad_psi[axis]
                # rho = sum w psi^2, so d2(rho) = 2 w (psi_a psi_b + psi psi_ab)
                for idx, (a, b) in enumerate(pairs):
                    hess_rho[idx] += weight * 2.0 * (
                        grad_psi[a] * grad_psi[b] + psi * hess_psi[idx]
                    )

        safe_rho = np.maximum(rho, density_floor)
        grad_norm = np.sqrt(np.sum(grad_rho * grad_rho, axis=0))
        prefactor = 2.0 * (3.0 * math.pi**2) ** (1.0 / 3.0)
        s = grad_norm / (prefactor * safe_rho ** (4.0 / 3.0))

        # Middle eigenvalue of the symmetric 3x3 Hessian at every point,
        # from the six unique components (renderers.nci.middle_eigenvalue).
        from vibeview.renderers.nci import middle_eigenvalue

        lambda2 = middle_eigenvalue(*hess_rho)
        signed_rho = np.sign(lambda2) * rho

        # In vacuum s blows up (0/0 with both going to zero); the paper only
        # ever reads s where rho is meaningful, so clamp rather than emit inf.
        s = np.where(rho > density_floor, s, 100.0)
        return (
            box.grid,
            s.astype(np.float32),
            signed_rho.astype(np.float32),
        )

    def evaluate_density_laplacian(
        self, *, padding_bohr: float = 4.0, n_per_dim: int = 60
    ) -> tuple[GridData, np.ndarray]:
        """Compute the Laplacian of the electron density, del^2 rho.

        Bader's classification (already cited here as ``bader_qtaim_1985``
        for the critical-point work): del^2 rho < 0 marks **charge
        concentration** -- covalent bonding regions, atomic shells, and the
        valence-shell charge concentrations that lone pairs show up as --
        while del^2 rho > 0 marks depletion.

        It is the trace of the density Hessian the NCI path already builds,
        so this shares that machinery rather than deriving anything new.

        Returns ``(grid [Å], laplacian [nx, ny, nz])`` in atomic units
        (e/bohr^5).
        """
        wf = self.load()
        self._require_molecular_field(wf, real_only=True)
        positions_bohr = self._atom_positions_bohr(wf.structure_ref)
        box = _molecular_grid(positions_bohr, padding_bohr, n_per_dim)
        xs, ys, zs = box.xs, box.ys, box.zs
        nx = ny = nz = int(n_per_dim)

        blocks: tuple[tuple[np.ndarray | None, np.ndarray | None], ...]
        if wf.spin == "unrestricted":
            blocks = (
                (wf.mo_coefficients_alpha, wf.alpha_occupations),
                (wf.mo_coefficients_beta, wf.beta_occupations),
            )
        else:
            blocks = ((wf.mo_coefficients, wf.occupations),)

        laplacian = np.zeros((nx, ny, nz), dtype=np.float64)
        # Only the three diagonal Hessian components contribute to a trace.
        diagonal = (0, 3, 5)  # xx, yy, zz in the packed ordering
        for coefficients, occupations in blocks:
            if coefficients is None or occupations is None:
                continue
            occ = np.asarray(occupations, dtype=float)
            for i in range(int(coefficients.shape[0])):
                if i >= occ.size or occ[i] <= _OCC_THRESHOLD:
                    continue
                weight = float(occ[i])
                psi, grad_psi, hess_psi = self._evaluate_hessian_on_grid(
                    wf, coefficients[i], positions_bohr, xs, ys, zs
                )
                # del^2 (psi^2) = 2 |grad psi|^2 + 2 psi del^2 psi
                laplacian += weight * 2.0 * (
                    np.sum(grad_psi * grad_psi, axis=0)
                    + psi * sum(hess_psi[k] for k in diagonal)
                )

        return (
            box.grid,
            laplacian.astype(np.float32),
        )

    def _accumulate_density(
        self, *, beta_weight: float, padding_bohr: float, n_per_dim: int
    ) -> tuple[GridData, np.ndarray, float]:
        """Occupation-weighted sum of |ψ|² over the occupied MOs.

        ``beta_weight`` is +1 for the total density and −1 for the spin
        density; the two differ only in that sign, so they share one grid
        pass rather than two near-identical ones.
        """
        wf = self.load()
        self._require_molecular_field(wf)
        positions_bohr = self._atom_positions_bohr(wf.structure_ref)
        box = _molecular_grid(positions_bohr, padding_bohr, n_per_dim)
        xs, ys, zs, step_bohr = box.xs, box.ys, box.zs, box.step_bohr
        nx = ny = nz = int(n_per_dim)

        blocks: tuple[tuple[np.ndarray | None, np.ndarray | None, float], ...]
        if wf.spin == "unrestricted":
            blocks = (
                (wf.mo_coefficients_alpha, wf.alpha_occupations, 1.0),
                (wf.mo_coefficients_beta, wf.beta_occupations, beta_weight),
            )
        else:
            blocks = ((wf.mo_coefficients, wf.occupations, 1.0),)

        rho = np.zeros((nx, ny, nz), dtype=np.float64)
        max_dropped_fraction = 0.0
        max_dropped_l = 0
        for coeffs, occs, weight in blocks:
            if coeffs is None or occs is None:
                continue
            occ = np.asarray(occs, dtype=float)
            for i in range(int(coeffs.shape[0])):
                if i >= occ.size or occ[i] <= _OCC_THRESHOLD:
                    continue
                psi = self._evaluate_on_grid(wf, coeffs[i], positions_bohr, xs, ys, zs)
                dropped_fraction = self.last_dropped_l_fraction
                if dropped_fraction > max_dropped_fraction:
                    max_dropped_fraction = dropped_fraction
                    max_dropped_l = self.last_dropped_l_max
                elif dropped_fraction == max_dropped_fraction:
                    max_dropped_l = max(max_dropped_l, self.last_dropped_l_max)
                rho += weight * occ[i] * np.abs(psi) ** 2

        # Preserve an aggregate diagnostic for density callers instead of
        # exposing only whichever occupied MO happened to be evaluated last.
        self.last_dropped_l_fraction = max_dropped_fraction
        self.last_dropped_l_max = max_dropped_l

        grid = box.grid
        # ∫ρ dV in bohr³ (the evaluator + density are in atomic units).
        integral = float(rho.sum() * float(np.prod(step_bohr)))
        return grid, rho.astype(np.float32), integral

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _require_molecular_field(wf: WavefunctionGTOData, *, real_only: bool = False) -> None:
        if wf.k_point is not None:
            raise ValueError(
                "A single k-point orbital block cannot supply the total periodic field. "
                "Select a stored density/ELF grid from the QVF instead."
            )
        if real_only and any(
            c is not None and np.iscomplexobj(c)
            for c in (wf.mo_coefficients, wf.mo_coefficients_alpha, wf.mo_coefficients_beta)
        ):
            raise ValueError("Density derivatives for complex orbitals are not supported")

    def _mo_row(self, wf: WavefunctionGTOData, index: int, spin: str) -> np.ndarray:
        if wf.spin == "unrestricted":
            coeffs = wf.mo_coefficients_beta if spin == "beta" else wf.mo_coefficients_alpha
        else:
            coeffs = wf.mo_coefficients
        if coeffs is None:
            raise ValueError(f"No MO coefficients present for spin={spin!r}")
        if index < 0 or index >= coeffs.shape[0]:
            raise IndexError(f"MO index {index} out of range [0, {coeffs.shape[0]})")
        row: np.ndarray = coeffs[index]
        return row

    def _structure_symbols(self, structure_ref: str) -> list[str] | None:
        """Element symbols from the referenced structure, for orbital labels.

        Best-effort: a localized orbital is still renderable without them, so
        a malformed or missing structure degrades to positional atom names
        rather than failing the picker.
        """
        try:
            return [str(a.symbol) for a in self._centre_structure(structure_ref).atoms]
        except Exception:
            return None

    def _centre_structure(self, structure_ref: str) -> StructureData:
        """The structure section the basis is centred on, parsed once.

        A missing or unknown ``structure_ref`` falls back to the primary
        ``structure`` section, as the format allows.
        """
        if not self.reader.has_section(structure_ref):
            structure_ref = "structure"
        cached = self._centre_structures.get(structure_ref)
        if cached is None:
            cached = self.reader.read_structure(structure_ref)
            self._centre_structures[structure_ref] = cached
        return cached

    def _atom_positions_bohr(self, structure_ref: str) -> np.ndarray:
        """Atomic positions in bohr from the referenced structure section."""
        structure = self._centre_structure(structure_ref)
        arr = np.array([a.position for a in structure.atoms], dtype=np.float64)
        return arr * _ANGSTROM_TO_BOHR

    def _evaluate_on_grid(
        self,
        wf: WavefunctionGTOData,
        mo_coeffs: np.ndarray,
        atom_pos_bohr: np.ndarray,
        xs: np.ndarray,
        ys: np.ndarray,
        zs: np.ndarray,
    ) -> np.ndarray:
        """Sum c_µ * χ_µ(r) over AOs at every grid point, where χ_µ are
        the AOs produced by the shell list.

        The AO order matches the file-format contract: shells iterated in
        list order; within a shell, m = -l..+l for pure, or libint
        lexicographic for Cartesian.
        """
        # Pre-compute relative coordinates (grid x atom)
        gx, gy, gz = np.meshgrid(xs, ys, zs, indexing="ij")
        return self._evaluate_at_points(wf, mo_coeffs, atom_pos_bohr, gx, gy, gz)

    def _evaluate_at_points(
        self,
        wf: WavefunctionGTOData,
        mo_coeffs: np.ndarray,
        atom_pos_bohr: np.ndarray,
        gx: np.ndarray,
        gy: np.ndarray,
        gz: np.ndarray,
    ) -> np.ndarray:
        """Evaluate a coefficient row at Cartesian points (all coordinates in bohr)."""
        result = np.zeros(gx.shape, dtype=np.result_type(mo_coeffs.dtype, np.float64))
        for block in self._shell_blocks(wf, mo_coeffs, atom_pos_bohr, gx, gy, gz):
            dx, dy, dz = block.deltas
            for k, terms in enumerate(_angular_terms(block.shell)):
                coeff = mo_coeffs[block.ao_index + k]
                if coeff != 0.0:
                    result += coeff * block.radial * _evaluate_terms(terms, dx, dy, dz)
        return result

    def _evaluate_gradient_on_grid(
        self,
        wf: WavefunctionGTOData,
        mo_coeffs: np.ndarray,
        atom_pos_bohr: np.ndarray,
        xs: np.ndarray,
        ys: np.ndarray,
        zs: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Evaluate an MO and its gradient on the grid.

        Returns ``(psi, grad)`` with ``grad`` of shape ``(3, nx, ny, nz)`` in
        atomic units (bohr^-1 relative to psi).

        Each AO is ``chi = f(r^2) * S(x,y,z)`` with ``f`` the contracted
        radial part and ``S`` a homogeneous angular polynomial, so

            grad chi = 2 f'(r^2) * (x, y, z) * S  +  f(r^2) * grad S

        The first term needs no angular derivative; the second comes from
        :func:`_differentiate_terms`, which differentiates the monomial
        expansion rather than a hand-written closed form.

        Same shell-skipping and validation contract as
        :meth:`_evaluate_on_grid`, including the l > 3 drop -- so a gradient
        carries the same ``last_dropped_l_fraction`` caveat as a value.
        """
        nx, ny, nz = len(xs), len(ys), len(zs)
        gx, gy, gz = np.meshgrid(xs, ys, zs, indexing="ij")
        value = np.zeros((nx, ny, nz), dtype=np.float64)
        grad = np.zeros((3, nx, ny, nz), dtype=np.float64)
        for block in self._shell_blocks(wf, mo_coeffs, atom_pos_bohr, gx, gy, gz, derivatives=1):
            dx, dy, dz = block.deltas
            radial, radial_d = block.radial, block.radial_d
            assert radial_d is not None
            for k, terms in enumerate(_angular_terms(block.shell)):
                coeff = mo_coeffs[block.ao_index + k]
                if coeff == 0.0:
                    continue
                ang = _evaluate_terms(terms, dx, dy, dz)
                value += coeff * radial * ang
                for axis in range(3):
                    d_ang = _differentiate_terms(terms, axis)
                    grad[axis] += coeff * (
                        2.0 * radial_d * block.deltas[axis] * ang
                        + radial * _evaluate_terms(d_ang, dx, dy, dz)
                    )
        return value, grad

    def _evaluate_hessian_on_grid(
        self,
        wf: WavefunctionGTOData,
        mo_coeffs: np.ndarray,
        atom_pos_bohr: np.ndarray,
        xs: np.ndarray,
        ys: np.ndarray,
        zs: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Evaluate an MO, its gradient and its Hessian on the grid.

        Returns ``(psi, grad[3], hess[6])``; the Hessian carries the six
        unique components in the order xx, xy, xz, yy, yz, zz.

        With ``chi = f(u) S`` and ``u = r^2``, writing ``d`` for the
        displacement from the shell centre:

            d(chi)/da       = 2 f' d_a S + f S_a
            d2(chi)/da db   = 4 f'' d_a d_b S
                              + 2 f' delta_ab S
                              + 2 f' (d_a S_b + d_b S_a)
                              + f S_ab

        where ``S_a`` and ``S_ab`` are derivatives of the angular monomials
        and ``f' = df/du``. For a primitive ``f = N exp(-alpha u)`` this is
        ``f' = -alpha f`` and ``f'' = alpha^2 f``.
        """
        nx, ny, nz = len(xs), len(ys), len(zs)
        gx, gy, gz = np.meshgrid(xs, ys, zs, indexing="ij")
        value = np.zeros((nx, ny, nz), dtype=np.float64)
        grad = np.zeros((3, nx, ny, nz), dtype=np.float64)
        hess = np.zeros((6, nx, ny, nz), dtype=np.float64)
        pairs = ((0, 0), (0, 1), (0, 2), (1, 1), (1, 2), (2, 2))
        for block in self._shell_blocks(wf, mo_coeffs, atom_pos_bohr, gx, gy, gz, derivatives=2):
            dx, dy, dz = block.deltas
            deltas = block.deltas
            radial, radial_d, radial_dd = block.radial, block.radial_d, block.radial_dd
            assert radial_d is not None and radial_dd is not None
            for k, terms in enumerate(_angular_terms(block.shell)):
                coeff = mo_coeffs[block.ao_index + k]
                if coeff == 0.0:
                    continue
                ang = _evaluate_terms(terms, dx, dy, dz)
                d_terms = [_differentiate_terms(terms, a) for a in range(3)]
                d_ang = [_evaluate_terms(t, dx, dy, dz) for t in d_terms]

                value += coeff * radial * ang
                for a in range(3):
                    grad[a] += coeff * (
                        2.0 * radial_d * deltas[a] * ang + radial * d_ang[a]
                    )
                for idx, (a, b) in enumerate(pairs):
                    dd_ang = _evaluate_terms(
                        _differentiate_terms(d_terms[a], b), dx, dy, dz
                    )
                    contribution = (
                        4.0 * radial_dd * deltas[a] * deltas[b] * ang
                        + 2.0 * radial_d * (
                            deltas[a] * d_ang[b] + deltas[b] * d_ang[a]
                        )
                        + radial * dd_ang
                    )
                    if a == b:
                        contribution = contribution + 2.0 * radial_d * ang
                    hess[idx] += coeff * contribution
        return value, grad, hess

    def _shell_blocks(
        self,
        wf: WavefunctionGTOData,
        mo_coeffs: np.ndarray,
        atom_pos_bohr: np.ndarray,
        gx: np.ndarray,
        gy: np.ndarray,
        gz: np.ndarray,
        *,
        derivatives: int = 0,
    ) -> Iterator[_ShellBlock]:
        """Walk the shells once for every evaluator.

        Yields one :class:`_ShellBlock` per shell with ``l <= 3``: the AO
        offset, the displacements from the centre and the contracted radial
        part ``f(r²)`` with, for ``derivatives >= 1``, ``f'`` and, for
        ``derivatives >= 2``, ``f''`` (derivatives with respect to ``r²``).
        The validation and the ``l > 3`` bookkeeping (``last_dropped_l_*``)
        happen here, so a value, a gradient and a Hessian cannot disagree
        about which AOs they cover.
        """
        total_w = float(np.sum(np.abs(mo_coeffs) ** 2)) or 1.0
        dropped_w = 0.0
        dropped_lmax = 0
        n_atoms = len(atom_pos_bohr)
        n_coeff = len(mo_coeffs)
        ao_index = 0
        for shell in wf.shells:
            n_ao_shell = self._n_ao_per_shell(shell)
            # A malformed wavefunction whose shells claim more AOs than the MO
            # vector carries would otherwise slice past the end and silently
            # drop trailing AOs (wrong orbital). Fail loudly instead.
            if ao_index + n_ao_shell > n_coeff:
                raise ValueError(
                    f"wavefunction MO vector too short: shells need AO index "
                    f"{ao_index + n_ao_shell - 1} but only {n_coeff} "
                    "coefficients are present (basis / MO-width mismatch)."
                )
            if shell.l > 3:
                # Skip high-l (g and beyond) shells but keep ao_index
                # consistent. Track the orbital weight we drop so the caller
                # can warn that the rendered field is incomplete (A2-04).
                block = mo_coeffs[ao_index : ao_index + n_ao_shell]
                dropped_w += float(np.sum(np.abs(block) ** 2))
                dropped_lmax = max(dropped_lmax, int(shell.l))
                ao_index += n_ao_shell
                continue
            if not 0 <= shell.center < n_atoms:
                raise ValueError(
                    f"wavefunction shell references atom index {shell.center} "
                    f"but the structure has only {n_atoms} atoms."
                )
            if len(shell.exponents) != len(shell.coefficients):
                raise ValueError(
                    f"wavefunction shell (l={shell.l}, center={shell.center}) "
                    f"has {len(shell.exponents)} exponents but "
                    f"{len(shell.coefficients)} contraction coefficients."
                )
            center = atom_pos_bohr[shell.center]
            dx = gx - center[0]
            dy = gy - center[1]
            dz = gz - center[2]
            r2 = dx * dx + dy * dy + dz * dz

            # Contracted radial part: sum_p c_p N(α_p) exp(-α_p r²), and its
            # derivatives with respect to r² when asked for.
            radial = np.zeros_like(r2)
            radial_d = np.zeros_like(r2) if derivatives >= 1 else None
            radial_dd = np.zeros_like(r2) if derivatives >= 2 else None
            for alpha, c in zip(shell.exponents, shell.coefficients, strict=True):
                a = float(alpha)
                gauss = c * _primitive_norm(int(shell.l), a) * np.exp(-a * r2)
                radial += gauss
                if radial_d is not None:
                    radial_d += -a * gauss
                if radial_dd is not None:
                    radial_dd += a * a * gauss
            yield _ShellBlock(shell, ao_index, (dx, dy, dz), radial, radial_d, radial_dd)
            ao_index += n_ao_shell

        self.last_dropped_l_fraction = dropped_w / total_w
        self.last_dropped_l_max = dropped_lmax

    @staticmethod
    def _n_ao_per_shell(shell: BasisShell) -> int:
        if shell.pure:
            return 2 * shell.l + 1
        return (shell.l + 1) * (shell.l + 2) // 2

    def _angular_factors(
        self,
        shell: BasisShell,
        dx: np.ndarray,
        dy: np.ndarray,
        dz: np.ndarray,
    ) -> list[np.ndarray]:
        """The angular factors of one shell, in the file's AO order.

        Cartesian shells: ``x^i y^j z^k`` in libint lexicographic order with
        no per-component normalisation (spec Appendix A.1 defines one
        ``N_i`` per shell, which ``_primitive_norm`` supplies). Pure shells:
        the real regular solid harmonics with the Condon-Shortley phase,
        Racah-normalised so ``_primitive_norm`` is the complete
        normalisation. Both come from the one monomial table
        :func:`_angular_terms`; the derivative evaluators differentiate the
        same table, so a value and its gradient share one definition.
        """
        return [_evaluate_terms(terms, dx, dy, dz) for terms in _angular_terms(shell)]


# ── Primitive normalisation ───────────────────────────────────────────────


def _primitive_norm(l: int, alpha: float) -> float:  # noqa: E741 — angular momentum
    """Normalisation constant for a real primitive Gaussian.

    N = (2α/π)^(3/4) · sqrt[(8α)^l · l! / (2l)!]

    This is QVF spec Appendix A.1's ``N_i`` written a different way -- the
    two are algebraically identical, since
    ``(2l)! = (2l-1)!! · 2^l · l!`` turns ``sqrt[(8α)^l l!/(2l)!]`` into
    ``(4α)^(l/2)/sqrt((2l-1)!!)``.

    It applies unchanged to both shell kinds: it is the full normalisation
    of a spherical GTO, and for Cartesian shells it is the shell-wide
    factor A.1 specifies (the norm of the axial ``(l, 0, 0)`` component).
    The angular factors carry no further per-component normalisation --
    see :func:`_angular_terms` for why that is deliberate.
    """
    pref = float((2.0 * alpha / math.pi) ** 0.75)
    if l == 0:
        return pref
    fac = (8.0 * alpha) ** l * math.factorial(l) / math.factorial(2 * l)
    return pref * math.sqrt(fac)


# ── Angular factors as monomial tables ───────────────────────────────────


def _solid_harmonic_terms(l: int) -> list[list[tuple[float, int, int, int]]]:  # noqa: E741 — angular momentum
    """Real solid harmonics as monomial tables, ``[(coefficient, i, j, k), ...]``
    per m with ``x^i y^j z^k``, ordered m = -l, ..., +l.

    Why a second representation of the same functions: an angular factor's
    *gradient* is needed for every density-derivative property (ELF, LOL,
    NCI/RDG, the density Laplacian), and differentiating a closed-form
    expression by hand for each (l, m) is 4 shells x (2l+1) x 3 chances to
    get a sign wrong. Differentiating a monomial is mechanical and cannot be
    got wrong, so the table is written once and both the value and the
    gradient are derived from it.

    Normalisation matches the real solid harmonics term for term, and
    ``tests/test_wavefunction_gradient.py`` pins that: the table is only
    trustworthy insofar as it reproduces the function already covered by
    ``tests/test_wavefunction_normalization.py``.

    The r^2 in expressions like ``3z^2 - r^2`` is expanded, so every entry is
    a genuine homogeneous polynomial of degree l.
    """
    if l == 0:
        return [[(1.0 / math.sqrt(4.0 * math.pi), 0, 0, 0)]]

    if l == 1:
        c = math.sqrt(3.0 / (4.0 * math.pi))
        return [
            [(c, 0, 1, 0)],  # m=-1: y
            [(c, 0, 0, 1)],  # m= 0: z
            [(c, 1, 0, 0)],  # m=+1: x
        ]

    if l == 2:
        c2 = math.sqrt(15.0 / (4.0 * math.pi))
        c1 = c2
        c0 = math.sqrt(5.0 / (16.0 * math.pi))
        return [
            [(c2, 1, 1, 0)],  # m=-2: xy
            [(c1, 0, 1, 1)],  # m=-1: yz
            # m=0: 3z^2 - r^2 = 2z^2 - x^2 - y^2
            [(2.0 * c0, 0, 0, 2), (-c0, 2, 0, 0), (-c0, 0, 2, 0)],
            [(c1, 1, 0, 1)],  # m=+1: xz
            # m=+2: (x^2 - y^2)/2
            [(0.5 * c2, 2, 0, 0), (-0.5 * c2, 0, 2, 0)],
        ]

    if l == 3:
        c3 = math.sqrt(35.0 / (32.0 * math.pi))
        c2 = math.sqrt(105.0 / (4.0 * math.pi))
        c1 = math.sqrt(21.0 / (32.0 * math.pi))
        c0 = math.sqrt(7.0 / (16.0 * math.pi))
        return [
            # m=-3: y(3x^2 - y^2)
            [(3.0 * c3, 2, 1, 0), (-c3, 0, 3, 0)],
            [(c2, 1, 1, 1)],  # m=-2: xyz
            # m=-1: y(5z^2 - r^2) = y(4z^2 - x^2 - y^2)
            [(4.0 * c1, 0, 1, 2), (-c1, 2, 1, 0), (-c1, 0, 3, 0)],
            # m= 0: z(5z^2 - 3r^2) = z(2z^2 - 3x^2 - 3y^2)
            [(2.0 * c0, 0, 0, 3), (-3.0 * c0, 2, 0, 1), (-3.0 * c0, 0, 2, 1)],
            # m=+1: x(5z^2 - r^2) = x(4z^2 - x^2 - y^2)
            [(4.0 * c1, 1, 0, 2), (-c1, 3, 0, 0), (-c1, 1, 2, 0)],
            # m=+2: z(x^2 - y^2)/2
            [(0.5 * c2, 2, 0, 1), (-0.5 * c2, 0, 2, 1)],
            # m=+3: x(x^2 - 3y^2)
            [(c3, 3, 0, 0), (-3.0 * c3, 1, 2, 0)],
        ]

    raise ValueError(f"_solid_harmonic_terms: l={l} not supported")


def _angular_terms(shell: BasisShell) -> list[list[tuple[float, int, int, int]]]:
    """Monomial terms for every AO of ``shell``, in the file's AO order.

    Cartesian shells in libint lexicographic order; pure shells as the
    solid harmonics with the Racah factor that strips the surface
    normalisation back out (``_primitive_norm`` already carries the
    complete normalisation of a spherical GTO; see
    ``tests/test_wavefunction_normalization.py``).
    """
    l = int(shell.l)  # noqa: E741 — angular momentum
    if not shell.pure:
        terms: list[list[tuple[float, int, int, int]]] = []
        for i in range(l, -1, -1):
            for j in range(l - i, -1, -1):
                terms.append([(1.0, i, j, l - i - j)])
        return terms
    racah = math.sqrt(4.0 * math.pi / (2 * l + 1))
    return [
        [(racah * c, i, j, k) for (c, i, j, k) in per_m]
        for per_m in _solid_harmonic_terms(l)
    ]


def _evaluate_terms(
    terms: list[tuple[float, int, int, int]],
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
) -> np.ndarray:
    """Evaluate a monomial list on the grid."""
    out = np.zeros_like(x)
    for coef, i, j, k in terms:
        out += coef * (x**i) * (y**j) * (z**k)
    return out


def _differentiate_terms(
    terms: list[tuple[float, int, int, int]], axis: int
) -> list[tuple[float, int, int, int]]:
    """Analytic partial derivative of a monomial list.

    ``d/dx [c x^i y^j z^k] = c i x^(i-1) y^j z^k``; terms whose exponent on
    the differentiated axis is zero vanish.
    """
    out: list[tuple[float, int, int, int]] = []
    for coef, i, j, k in terms:
        power = (i, j, k)[axis]
        if power == 0:
            continue
        exps = [i, j, k]
        exps[axis] -= 1
        out.append((coef * power, exps[0], exps[1], exps[2]))
    return out


