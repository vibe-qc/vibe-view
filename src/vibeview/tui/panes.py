"""Text panes for the QVF kinds that are tables, not pictures.

Citations, run records, job specs, charges, bond orders, symmetry, NMR/EPR
tensors and the MO listing carry their meaning as text. Each builder returns
a Rich-markup string; every value that came out of the archive goes through
:func:`rich.markup.escape`, because a producer is free to put a literal
``[bold]`` in a residue name or a log line and markup injection from file
content would corrupt the display.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

import numpy as np
from rich.markup import escape

from vibeview.kinds import classify_section
from vibeview.renderers._coerce import as_float
from vibeview.renderers.wavefunction import (
    ELECTRON_OCCUPATION,
    TRANSITION_WEIGHT,
    describe_transition_orbitals,
    frontier_orbitals,
    natural_value_semantics,
)
from vibeview.units import (
    HARTREE_TO_EV,
    energy_factor,
    energy_label,
    length_from_angstrom,
    length_label,
)
from vibeview.units import energy_unit as _energy_unit_key

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader


_H = "bold #d7dce8"  # section heading
_K = "#8a93a8"  # key
_V = "#e6eaf2"  # value
_WARN = "bold #f0b050"

_MAX_LOG_LINES = 400
_MAX_ROWS = 500
# Longest archive string a pane shows in one cell. Rich wraps a long cell
# in time quadratic in its length: 80,000 characters took 23 s, and a
# 1,000,000-character section id kept `show --info` busy for over an hour
# (#62).
_MAX_CELL_CHARS = 200

# The frontier rule, its tolerances and the occupation semantics live in
# renderers.wavefunction (frontier_orbitals), shared with the browser.
#
# Pulay & Hamilton, J. Chem. Phys. 88, 4926 (1988), doi:10.1063/1.454704, § II:
# natural orbitals with occupation numbers between 0.02 and 1.98 are the
# fractionally occupied ones and constitute the MC-SCF active space. Their
# paper calls the window "somewhat arbitrary", so it is reported to the user
# with the count rather than used to decide anything silently.
_NO_ACTIVE_LO = 0.02
_NO_ACTIVE_HI = 1.98


def _clip(text: str) -> str:
    """``text`` cut to :data:`_MAX_CELL_CHARS`, saying how much was left out."""
    if len(text) <= _MAX_CELL_CHARS:
        return text
    return f"{text[:_MAX_CELL_CHARS]}… (+{len(text) - _MAX_CELL_CHARS:,} chars)"


def _kv(key: str, value: object, width: int = 22) -> str:
    if value is None or value == "":
        return ""
    return f"[{_K}]{escape(_clip(key)):<{width}}[/] [{_V}]{escape(_clip(str(value)))}[/]"


def _lines(*parts: str) -> str:
    return "\n".join(p for p in parts if p)


def _table(headers: list[str], rows: list[list[str]], widths: list[int] | None = None) -> str:
    rows = [[_clip(cell) for cell in row] for row in rows]
    if widths is None:
        widths = [
            max(len(headers[i]), *(len(r[i]) for r in rows)) if rows else len(headers[i])
            for i in range(len(headers))
        ]
    head = "  ".join(f"[{_K}]{h:<{w}}[/]" for h, w in zip(headers, widths, strict=True))
    body = [
        "  ".join(f"[{_V}]{escape(cell):<{w}}[/]" for cell, w in zip(row, widths, strict=True))
        for row in rows
    ]
    if len(rows) >= _MAX_ROWS:
        body.append(f"[{_K}]… truncated at {_MAX_ROWS} rows[/]")
    return _lines(head, *body)


# ── overview ──────────────────────────────────────────────────────────────


def overview(reader: QVFReader) -> str:
    """File-level summary: provenance, lifecycle, and the section inventory."""
    manifest = reader.manifest
    source = reader.source
    out = [f"[{_H}]Archive[/]"]
    out.append(_kv("path", reader.path or "<in memory>"))
    out.append(_kv("qvf version", getattr(manifest, "qvf_version", None)))
    out.append(_kv("producer", getattr(source, "program", None)))
    out.append(_kv("producer version", getattr(source, "program_version", None)))
    out.append(_kv("created", getattr(source, "created_utc", None)))
    status = reader.run_status
    if status:
        out.append(_kv("run status", status))

    for heading, warnings in (
        ("Manifest warnings", reader.manifest_warnings()),
        ("Lifecycle warnings", reader.lifecycle_warnings()),
    ):
        if warnings:
            out.append("")
            out.append(f"[{_H}]{heading}[/]")
            out.extend(f"[{_WARN}]! {escape(_clip(str(w)))}[/]" for w in warnings)

    provenance = reader.provenance or {}
    if provenance:
        out.append("")
        out.append(f"[{_H}]Provenance[/]")
        out.extend(_kv(str(k), v) for k, v in sorted(provenance.items()))

    out.append("")
    out.append(f"[{_H}]Sections[/]")
    rows = []
    for section in reader.sections:
        status_text, detail = classify_section(section.kind)
        # A structure's compound label and name (spec § 5.1), another
        # section's label, before the registry's detail.
        if section.kind == "structure":
            title = reader.structure_identity(section.id).title
        else:
            label = (section.model_extra or {}).get("label")
            title = label if isinstance(label, str) else ""
        note = " · ".join(part for part in (title, detail or "") if part)
        error = reader.section_error(section.id)
        if error:
            status_text, note = "error", error
        if reader.section_is_partial(section.id):
            note = (note + " · partial").strip(" ·")
        rows.append([section.id, section.kind, status_text, note])
    out.append(_table(["id", "kind", "status", "note"], rows))
    return _lines(*out)


# ── structure ─────────────────────────────────────────────────────────────


def structure_table(
    reader: QVFReader,
    limit: int = _MAX_ROWS,
    structure_id: str | None = None,
    *,
    length_unit: str = "angstrom",
) -> str:
    """Cartesian geometry, cell parameters, and the bond list.

    ``structure_id`` selects a structure section other than the shown one
    (the first structure unless another was selected), such as the geometry
    a ``wavefunction.gto`` names in ``structure_ref``. The title names the
    structure by its label and name (spec § 5.1), else by its id, whenever
    that tells it apart from another.
    ``length_unit`` (a ``vibeview.units.LENGTH_UNITS`` key, the browser's
    ``measure_unit`` setting) sets the unit of every length shown; the
    structure stays in Å (#58).
    """
    unit = length_label(length_unit)

    def _len(value: float) -> float:
        return length_from_angstrom(float(value), length_unit)

    shown = reader.shown_structure_id
    section_id = structure_id or shown
    structure = reader.read_structure(section_id)
    atoms = structure.atoms
    identity = reader.structure_identity(section_id) if section_id else None
    name = identity.title if identity is not None else ""
    if section_id != shown or len(reader.structure_sections) > 1:
        name = name or str(section_id)
    title = f"Structure · {name}" if name else "Structure"
    out = [f"[{_H}]{escape(_clip(title))}[/]", _kv("atoms", len(atoms))]
    if identity is not None:
        out.extend(
            _kv(key, value)
            for key, value in identity.as_dict().items()
            if key not in ("label", "name")
        )

    lattice = structure.lattice_vectors
    if lattice is not None and any(structure.pbc):
        out.append(_kv("periodicity", f"dim={structure.dim}  pbc={structure.pbc}"))
        # Only the periodic axes are real cell edges; the rest are
        # synthesized for the integrals and would be a lie to report.
        names = "abc"
        for i, flag in enumerate(structure.pbc):
            if not flag:
                continue
            vec = np.asarray(lattice[i], dtype=float)
            x, y, z = (_len(c) for c in vec)
            out.append(
                _kv(
                    f"  {names[i]}",
                    f"{x:9.4f} {y:9.4f} {z:9.4f}   |{names[i]}| ="
                    f" {_len(np.linalg.norm(vec)):8.4f} {unit}",
                )
            )
    if structure.has_residues:
        out.append(_kv("chains", ", ".join(structure.chain_ids())))

    out.append("")
    rows = []
    biomolecular = structure.has_residues
    headers = ["#", "el", f"x ({unit})", f"y ({unit})", f"z ({unit})"]
    if biomolecular:
        headers += ["res", "seq", "chain", "name"]
    for i, atom in enumerate(atoms[:limit]):
        row = [
            str(i + 1),
            atom.symbol,
            f"{_len(atom.position[0]):10.5f}",
            f"{_len(atom.position[1]):10.5f}",
            f"{_len(atom.position[2]):10.5f}",
        ]
        if biomolecular:
            row += [
                atom.residue_name or "-",
                str(atom.residue_seq) if atom.residue_seq is not None else "-",
                atom.chain_id or "-",
                atom.atom_name or "-",
            ]
        rows.append(row)
    out.append(_table(headers, rows))

    bonds = structure.bonds
    if bonds:
        out.append("")
        out.append(f"[{_H}]Bonds[/] ({len(bonds)})")
        positions = np.array([a.position for a in atoms])
        brows = []
        for i, j, order, *_image in bonds[:limit]:
            if i >= len(atoms) or j >= len(atoms):
                continue
            dist = float(np.linalg.norm(positions[i] - positions[j]))
            brows.append(
                [
                    f"{atoms[i].symbol}{i + 1}",
                    f"{atoms[j].symbol}{j + 1}",
                    f"{order:.2f}",
                    f"{_len(dist):.4f}",
                ]
            )
        out.append(_table(["i", "j", "order", f"dist ({unit})"], brows))
    return _lines(*out)


# ── per-kind panes ────────────────────────────────────────────────────────


def atom_properties(reader: QVFReader, section_id: str) -> str:
    data = reader.read_atom_properties(section_id)
    structure = reader.read_structure()
    symbols = [a.symbol for a in structure.atoms]
    columns = [
        ("Mulliken", data.mulliken_charges),
        ("Löwdin", data.loewdin_charges),
        ("Hirshfeld", data.hirshfeld_charges),
        ("spin", data.spin_populations),
    ]
    present = [(name, np.asarray(values)) for name, values in columns if values is not None]
    if not present:
        return f"[{_H}]Atom properties[/]\n[{_K}]no populated arrays in this section[/]"

    headers = ["#", "el"] + [name for name, _ in present]
    rows = []
    for i, symbol in enumerate(symbols):
        row = [str(i + 1), symbol]
        for _name, values in present:
            row.append(f"{values[i]:+8.4f}" if i < len(values) else "-")
        rows.append(row)
    totals = ["", "Σ"] + [f"{values.sum():+8.4f}" for _name, values in present]
    return _lines(f"[{_H}]Atom properties[/]", _table(headers, rows + [totals]))


def bond_orders(reader: QVFReader, section_id: str, *, length_unit: str = "angstrom") -> str:
    """Bond-order pairs; distances in ``length_unit`` (#58)."""
    data = reader.read_bond_orders(section_id)
    unit = length_label(length_unit)
    rows = [
        [
            str(pair.get("i", "")),
            str(pair.get("j", "")),
            f"{as_float(pair.get('order'), 0.0):.4f}",
            (
                f"{length_from_angstrom(as_float(pair.get('distance_ang'), 0.0), length_unit):.4f}"
                if "distance_ang" in pair
                else "-"
            ),
        ]
        for pair in (data.pairs or [])[:_MAX_ROWS]
    ]
    return _lines(
        f"[{_H}]Bond orders[/]",
        _kv("method", data.method),
        "",
        _table(["i", "j", "order", f"dist ({unit})"], rows),
    )


#: Fixed-width orbital-energy columns per display unit: (heading, format).
#: Hartree keeps the terminal's historical "Ha" heading and six places.
_ENERGY_COLUMNS = {
    "hartree": ("ε (Ha)", "+12.6f"),
    "ev": ("ε (eV)", "+10.4f"),
    "kcal_mol": ("ε (kcal/mol)", "+12.2f"),
}


def wavefunction(
    reader: QVFReader,
    section_id: str,
    energy_unit: str = "hartree",
    *,
    length_unit: str = "angstrom",
) -> str:
    """MO listing — energies, occupations, symmetry labels, HOMO/LUMO gap.

    ``energy_unit`` (a ``vibeview.units.ENERGY_UNITS`` key; the browser's
    ``energy_unit`` setting) picks the first energy column and the gap's
    unit (#58). The second column keeps a reference: eV beside Hartree, as
    before, and the stored Hartree value beside any other unit. With the
    display in Hartree the gap is quoted in eV, as in the browser.
    """
    unit = _energy_unit_key(energy_unit)
    columns = (unit, "ev" if unit == "hartree" else "hartree")
    data = reader.read_wavefunction_gto(section_id)
    # The electron count is what separates an occupancy vector from a set of
    # transition weights; see _frontier_orbitals.
    try:
        n_electrons = (reader.provenance or {}).get("n_electrons")
    except Exception:  # noqa: BLE001 — provenance is optional, never fatal here
        n_electrons = None
    natural_semantics = natural_value_semantics(data)
    out = [
        f"[{_H}]Wavefunction (GTO)[/]",
        _kv("spin", data.spin),
        _kv("orbital kind", data.orbital_kind),
        _kv("transition orbitals of", describe_transition_orbitals(data)),
        _kv("AO basis functions", data.n_ao),
        _kv("shells", len(data.shells)),
        _kv("spherical (pure)", data.pure),
    ]

    def block(
        title: str,
        energies: Sequence[float] | np.ndarray | None,
        occupations: Sequence[float] | np.ndarray | None,
        labels: Sequence[str] | None,
    ) -> str:
        if energies is None:
            return ""
        energies = np.asarray(energies, dtype=float)
        occ = np.asarray(occupations, dtype=float) if occupations is not None else None
        # Resolve the frontier orbitals *before* rendering any row. Deciding
        # the marker inside the accumulating loop labelled every occupied
        # orbital "HOMO", because `homo` had been advanced to the current
        # index by the time that row was written. Only the last one is the
        # highest occupied.
        homo, lumo, undefined = frontier_orbitals(
            occ,
            len(energies),
            data.orbital_kind,
            n_electrons,
            natural_semantics,
        )
        # HOMO/LUMO carry a Koopmans flavour that a natural orbital does not
        # have: its eigenvalue is an occupation number, not an orbital energy.
        # The natural-orbital literature says "highest occupied natural
        # orbital", so say that.
        frontier_names = ("HONO", "LUNO") if data.orbital_kind == "natural" else ("HOMO", "LUMO")

        rows = []
        for i, energy in enumerate(energies[:_MAX_ROWS]):
            occupancy = occ[i] if occ is not None and i < len(occ) else float("nan")
            marker = ""
            if i == homo:
                marker = frontier_names[0]
            elif i == lumo:
                marker = frontier_names[1]
            rows.append(
                [
                    str(i + 1),
                    *(
                        format(energy * energy_factor(column), _ENERGY_COLUMNS[column][1])
                        for column in columns
                    ),
                    # round-then-normalize: a natural occupation of -6.6e-17
                    # (a diagonalizer's numerical zero) formats as "-0.000",
                    # which reads as a defect rather than as zero.
                    "-" if np.isnan(occupancy) else f"{round(float(occupancy), 3) + 0.0:.3f}",
                    str(labels[i]) if labels and i < len(labels) else "",
                    marker,
                ]
            )
        value_heading = "occ"
        if data.orbital_kind == "natural":
            value_heading = (
                "occ"
                if natural_semantics == ELECTRON_OCCUPATION
                else (
                    "weight"
                    if natural_semantics == TRANSITION_WEIGHT
                    else "value"
                )
            )
        headings = [_ENERGY_COLUMNS[column][0] for column in columns]
        text = _table(["#", *headings, value_heading, "sym", ""], rows)
        # For natural orbitals the informative statement is not where the
        # frontier sits but how many orbitals are genuinely fractional, which
        # is the active space in Pulay's classification.
        # Gated on `not undefined` for the same reason the frontier is:
        # Pulay's window classifies *occupancies*, and counting transition
        # weights inside it would repeat the category error one line down.
        active = ""
        if occ is not None and data.orbital_kind == "natural" and not undefined:
            counts = np.asarray(occ[: len(energies)], dtype=float)
            n_active = int(np.count_nonzero((counts > _NO_ACTIVE_LO) & (counts < _NO_ACTIVE_HI)))
            active = _kv(
                "fractionally occupied",
                f"{n_active} of {len(counts)} in {_NO_ACTIVE_LO}-{_NO_ACTIVE_HI}"
                " (Pulay 1988 active-space window)",
            )

        gap = ""
        if homo is not None and lumo is not None and lumo < len(energies):
            if data.orbital_kind == "natural" and occ is not None:
                # No energy gap here. A natural orbital's eigenvalue is its
                # occupation number, not an orbital energy, so subtracting two
                # of whatever a producer put in `energies` would dress a
                # category error up as an eV. The occupations are the quantity
                # the natural-orbital literature actually reads off this pair.
                gap = _kv(
                    f"{frontier_names[0]} / {frontier_names[1]} occupancy",
                    f"{occ[homo]:.4f} / {occ[lumo]:.4f}",
                )
            else:
                gap_eh = float(energies[lumo] - energies[homo])
                if unit == "hartree":
                    gap_text = f"{gap_eh * HARTREE_TO_EV:.4f} eV"
                else:
                    gap_text = f"{gap_eh * energy_factor(unit):.4f} {energy_label(unit)}"
                gap = _kv(f"{frontier_names[0]}-{frontier_names[1]} gap", gap_text)
        elif undefined:
            gap = _kv("frontier orbitals", undefined)
        return _lines("", f"[{_H}]{title}[/]", gap, active, text)

    if data.spin == "unrestricted":
        out.append(
            block(
                "Alpha orbitals",
                data.alpha_energies,
                data.alpha_occupations,
                data.symmetry_labels,
            )
        )
        out.append(
            block(
                "Beta orbitals",
                data.beta_energies,
                data.beta_occupations,
                data.symmetry_labels_beta,
            )
        )
    else:
        out.append(block("Orbitals", data.energies, data.occupations, data.symmetry_labels))
    # The viewport draws this wavefunction on the geometry its structure_ref
    # names; show that geometry too, not only the primary structure's
    # table, which describes a different geometry (#38-33).
    structure_ref = str(getattr(data, "structure_ref", "") or "")
    if structure_ref and structure_ref != reader.shown_structure_id:
        try:
            out.append("")
            out.append(
                structure_table(reader, structure_id=structure_ref, length_unit=length_unit)
            )
        except Exception as exc:  # noqa: BLE001 — the MO listing stays usable
            out.append(f"[bold #eb6e6e]geometry {structure_ref!r}: {exc}[/]")
    return _lines(*out)


def citations(reader: QVFReader, section_id: str) -> str:
    data = reader.read_citations(section_id)
    return _lines(
        f"[{_H}]Citations (BibTeX)[/]",
        f"[{_K}]copy this into your bibliography[/]",
        "",
        f"[{_V}]{escape(data.bibtex)}[/]",
    )


def run_record(reader: QVFReader, section_id: str, log_tail: int = _MAX_LOG_LINES) -> str:
    """Program invocation, its verbatim input, and the tail of its log."""
    data = reader.read_run_record(section_id)
    sequence, total = reader.run_record_position(section_id)
    out = [
        f"[{_H}]Run record[/] ({sequence}/{total})",
        _kv("program", f"{data.program} {data.program_version or ''}".strip()),
        _kv("command", data.command),
        _kv("exit status", data.exit_status),
        _kv("started", data.started_utc),
        _kv("finished", data.finished_utc),
        _kv("input size", f"{data.input_size:,} B"),
        _kv("log size", f"{data.log_size:,} B"),
    ]
    if data.attachment_roles:
        out.append(_kv("attachments", ", ".join(data.attachment_roles)))

    if data.input_text:
        out.append("")
        out.append(f"[{_H}]Input[/]")
        out.append(f"[{_V}]{escape(data.input_text)}[/]")
    if data.log_text:
        log_lines = data.log_text.splitlines()
        clipped = len(log_lines) > log_tail
        shown = log_lines[-log_tail:] if clipped else log_lines
        out.append("")
        clip_note = f" [{_K}](last {log_tail} of {len(log_lines)} lines)[/]" if clipped else ""
        out.append(f"[{_H}]Log[/]{clip_note}")
        out.append(f"[{_V}]{escape(chr(10).join(shown))}[/]")
    return _lines(*out)


def job_spec(reader: QVFReader, section_id: str) -> str:
    data = reader.read_job_spec(section_id)
    out = [
        f"[{_H}]Job spec[/]",
        _kv("job type", data.job_type),
        _kv("method", data.method),
        _kv("functional", data.functional),
        _kv("basis", data.basis),
        _kv("charge", data.charge),
        _kv("multiplicity", data.multiplicity),
        _kv("k-points", data.kpoints),
    ]
    if data.tasks:
        out.append("")
        out.append(f"[{_H}]Tasks[/]")
        out.extend(f"[{_V}]  · {escape(str(task))}[/]" for task in data.tasks)
    if data.options:
        out.append("")
        out.append(f"[{_H}]Options[/]")
        out.extend(_kv(f"  {k}", v) for k, v in sorted(data.options.items()))
    return _lines(*out)


def reaction_scheme(reader: QVFReader, section_id: str) -> str:
    """The steps of a reaction scheme as equations (spec § 5.14)."""
    from vibeview.renderers.reaction_scheme import (
        ReactionSchemeRenderer,
        species_text,
        step_equation,
        step_yield,
    )

    renderer = ReactionSchemeRenderer(reader.get_section(section_id), reader)
    data = renderer.load()
    out = [f"[{_H}]{escape(_clip(renderer.title))}[/]", _kv("steps", len(data.steps))]
    for step in data.steps:
        out.append("")
        out.append(f"[{_H}]Step {escape(_clip(step.label or step.id))}[/]")
        out.append(f"[{_V}]  {escape(_clip(step_equation(reader, step)))}[/]")
        if step.reagents:
            out.append(
                _kv(
                    "  reagents",
                    ", ".join(species_text(reader, p.structure_ref) for p in step.reagents),
                )
            )
        out.append(_kv("  conditions", "; ".join(step.conditions)))
        out.append(_kv("  yield", step_yield(step)))
    return _lines(*out)


def excited_states(reader: QVFReader, section_id: str) -> str:
    """The states of an ``excited_states`` section as a table (spec § 5.12, #109).

    Excitation energies in eV with their wavelength in nm, the spectroscopic
    convention, whatever the energy preference for orbitals is.
    """
    from vibeview.tables import contribution_text

    data = reader.read_excited_states(section_id)
    meta = data.meta
    out = [f"[{_H}]Excited states[/]"]
    method = meta.get("method")
    if isinstance(method, str) and method:
        out.append(_kv("method", method))
    reference = meta.get("reference_state")
    if isinstance(reference, str) and reference:
        out.append(_kv("reference state", reference))
    out.append(_kv("states", len(data.states)))
    if data.wavefunction_ref:
        out.append(_kv("wavefunction", data.wavefunction_ref))
    if not data.states:
        out.append(f"[{_K}]no states in this section[/]")
        return _lines(*out)
    headers = ["#", "E (eV)", "λ (nm)", "f", "R (cgs)", "mult", "sym", "contributions"]
    rows = []
    for state in data.states:
        wavelength = state.wavelength_nm
        rows.append([
            str(state.index),
            f"{state.excitation_energy_ev:.3f}",
            "-" if wavelength is None else f"{wavelength:.1f}",
            "-" if state.oscillator_strength is None else f"{state.oscillator_strength:.4f}",
            "-" if state.rotatory_strength_cgs is None else f"{state.rotatory_strength_cgs:.3f}",
            "-" if state.multiplicity is None else str(state.multiplicity),
            state.symmetry or "-",
            "; ".join(contribution_text(c) for c in state.contributions) or "-",
        ])
    out.append("")
    out.append(_table(headers, rows))
    references = [
        f"  {state.index}: " + ", ".join(
            part for part in (
                f"NTO {state.nto_ref}" if state.nto_ref else "",
                f"density {state.density_ref}" if state.density_ref else "",
            ) if part
        )
        for state in data.states
        if state.nto_ref or state.density_ref
    ]
    if references:
        out.append("")
        out.append(f"[{_H}]Linked sections[/]")
        out.extend(f"[{_V}]{escape(_clip(line))}[/]" for line in references)
    return _lines(*out)


def symmetry(reader: QVFReader, section_id: str) -> str:
    data = reader.read_symmetry(section_id)
    return _lines(
        f"[{_H}]Symmetry[/]",
        *(_kv(str(k), v) for k, v in sorted((data.raw or {}).items())),
    )


def tensor_pane(reader: QVFReader, section_id: str, title: str) -> str:
    """NMR / EPR sections — schema-free dicts, so surface every key found."""
    read = reader.read_nmr if title == "NMR" else reader.read_epr
    raw = read(section_id).raw or {}
    out = [f"[{_H}]{title}[/]"]
    for key, value in raw.items():
        array = np.asarray(value) if isinstance(value, (list, tuple)) else None
        if array is not None and array.ndim >= 2:
            out.append("")
            out.append(f"[{_K}]{escape(str(key))}[/]")
            flat = array.reshape(-1, array.shape[-1])[:_MAX_ROWS]
            for row in flat:
                out.append(
                    f"[{_V}]  " + "  ".join(f"{float(v):+10.5f}" for v in np.ravel(row)) + "[/]"
                )
        elif array is not None and array.ndim == 1:
            out.append(_kv(str(key), "  ".join(f"{float(v):+.5f}" for v in array[:16])))
        else:
            out.append(_kv(str(key), value))
    return _lines(*out)


def qtaim(reader: QVFReader, section_id: str) -> str:
    data = reader.read_topology_qtaim(section_id)
    rows = []
    for point in (data.points or [])[:_MAX_ROWS]:
        raw_position = point.get("position")
        position = raw_position if isinstance(raw_position, list | tuple) else [0, 0, 0]
        rows.append(
            [
                str(point.get("type", "")),
                " ".join(f"{as_float(c, 0.0):8.4f}" for c in position),
                f"{as_float(point.get('rho'), 0.0):.5f}",
                f"{as_float(point.get('laplacian'), 0.0):+.5f}",
            ]
        )
    out = [f"[{_H}]QTAIM topology[/]", _table(["type", "position (Å)", "ρ", "∇²ρ"], rows)]
    if data.bond_paths:
        out.append("")
        out.append(_kv("bond paths", len(data.bond_paths)))
    return _lines(*out)


def vibrations_table(reader: QVFReader, section_id: str) -> str:
    data = reader.read_vibrations(section_id)
    frequencies = np.asarray(data.frequencies, dtype=float)
    rows = []
    for i, freq in enumerate(frequencies[:_MAX_ROWS]):
        displacement = np.asarray(data.displacements[i], dtype=float)
        rows.append(
            [
                str(i + 1),
                f"{freq:10.2f}",
                "imaginary" if freq < 0 else "",
                f"{np.abs(displacement).max():.4f}",
            ]
        )
    imaginary = int((frequencies < 0).sum())
    return _lines(
        f"[{_H}]Normal modes[/]",
        _kv("modes", len(frequencies)),
        _kv("imaginary", imaginary) if imaginary else "",
        "",
        _table(["#", "ω (cm⁻¹)", "note", "max |d| (Å)"], rows),
    )


def generic_section(reader: QVFReader, section_id: str) -> str:
    """Fallback: whatever the manifest records about a section we can't chart."""
    section = reader.get_section(section_id)
    status, detail = classify_section(section.kind)
    out = [
        f"[{_H}]{escape(section.kind)}[/]",
        _kv("id", section.id),
        _kv("status", f"{status}{f' ({detail})' if detail else ''}"),
    ]
    extra = getattr(section, "model_extra", None) or {}
    for key, value in sorted(extra.items()):
        if key in {"members"}:
            continue
        out.append(_kv(str(key), value))
    members = getattr(section, "members", None) or []
    if members:
        out.append("")
        out.append(f"[{_H}]Members[/]")
        for member in members:
            name = getattr(member, "name", None) or getattr(member, "path", "?")
            shape = getattr(member, "shape", None)
            out.append(_kv(f"  {name}", f"shape={shape}" if shape else ""))
    error = reader.section_error(section_id)
    if error:
        out.append("")
        out.append(f"[{_WARN}]! {escape(error)}[/]")
    return _lines(*out)


HELP = f"""[{_H}]vibe-view terminal mode[/]

[{_H}]View[/]
  [{_K}]arrows / h j k l[/]   rotate            [{_K}]H J K L[/]        pan
  [{_K}]+ -[/]                zoom              [{_K}]r[/]              reset camera
  [{_K}]m[/]                  representation    [{_K}]c[/]              colour scheme
  [{_K}]b[/]                  bonds on/off      [{_K}]u[/]              unit cell on/off
  [{_K}]#[/]                  atom indices      [{_K}]d[/]              braille / half-block

[{_H}]Periodic[/]
  [{_K}]x y z[/]              replicate along a / b / c
  [{_K}]X Y Z[/]              un-replicate

[{_H}]Volumes[/]
  [{_K}]n p[/]                next / previous stored volume section
  [{_K}]i I[/]                isovalue down / up
  [{_K}]o[/]                  isosurface on/off

[{_H}]Wavefunctions[/]
  [{_K}]up down + Enter[/]     choose a surface in the right-hand table
  [{_K}]n p[/]                next / previous orbital
  [{_K}]D[/]                  total density    [{_K}]S[/]              spin density
  [{_K}]e[/]                  energy unit: Eh / eV / kcal/mol (saved as a setting)

[{_H}]Frames[/]
  [{_K}]space[/]              play / pause      [{_K}][ ][/]            step frame
  [{_K}]g[/]                  geometry / energy-profile view
  [{_K}]< >[/]                previous / next normal mode

[{_H}]Navigation[/]
  [{_K}]tab / shift+tab[/]    next / previous section
  [{_K}]s[/]                  sidebar on/off    [{_K}]t[/]              data table on/off
  [{_K}]w[/]                  write current frame to a .txt next to the archive
  [{_K}]?[/]                  this help         [{_K}]q[/]              quit
"""
