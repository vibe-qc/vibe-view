"""Text panes for the QVF kinds that are tables, not pictures.

Citations, run records, job specs, charges, bond orders, symmetry, NMR/EPR
tensors and the MO listing carry their meaning as text. Each builder returns
a Rich-markup string; every value that came out of the archive goes through
:func:`rich.markup.escape`, because a producer is free to put a literal
``[bold]`` in a residue name or a log line and markup injection from file
content would corrupt the display.
"""

from __future__ import annotations

import numpy as np
from rich.markup import escape

from vibeview.kinds import classify_section

_H = "bold #d7dce8"  # section heading
_K = "#8a93a8"  # key
_V = "#e6eaf2"  # value
_WARN = "bold #f0b050"

_MAX_LOG_LINES = 400
_MAX_ROWS = 500

# How far an occupation may sit from an integer and still be treated as one.
# Covers float round-trip through the archive's JSON, nothing more: a genuine
# fractional occupation is a statement about the wavefunction, not noise, and
# is handled by _frontier_orbitals rather than absorbed by a tolerance.
_OCC_INTEGER_TOL = 1e-6

# Pulay & Hamilton, J. Chem. Phys. 88, 4926 (1988), doi:10.1063/1.454704, § II:
# natural orbitals with occupation numbers between 0.02 and 1.98 are the
# fractionally occupied ones and constitute the MC-SCF active space. Their
# paper calls the window "somewhat arbitrary", so it is reported to the user
# with the count rather than used to decide anything silently.
_NO_ACTIVE_LO = 0.02
_NO_ACTIVE_HI = 1.98

# How far the occupations may sum from the system's electron count and still
# be read as occupancies. Loose enough for accumulated float error over a few
# hundred orbitals, far tighter than the gap between an electron count and an
# NTO's transition weights (16 versus 1.0042 on the H2CO TDDFT archive).
_OCC_ELECTRON_TOL = 0.05
_ELECTRON_OCCUPATION = "electron_occupation"
_TRANSITION_WEIGHT = "transition_weight"


def _kv(key: str, value, width: int = 22) -> str:
    if value is None or value == "":
        return ""
    return f"[{_K}]{key:<{width}}[/] [{_V}]{escape(str(value))}[/]"


def _lines(*parts: str) -> str:
    return "\n".join(p for p in parts if p)


def _table(headers: list[str], rows: list[list[str]], widths: list[int] | None = None) -> str:
    if widths is None:
        widths = [
            max(len(headers[i]), *(len(r[i]) for r in rows)) if rows else len(headers[i])
            for i in range(len(headers))
        ]
    head = "  ".join(f"[{_K}]{h:<{w}}[/]" for h, w in zip(headers, widths))
    body = [
        "  ".join(f"[{_V}]{escape(cell):<{w}}[/]" for cell, w in zip(row, widths)) for row in rows
    ]
    if len(rows) >= _MAX_ROWS:
        body.append(f"[{_K}]… truncated at {_MAX_ROWS} rows[/]")
    return _lines(head, *body)


# ── overview ──────────────────────────────────────────────────────────────


def overview(reader) -> str:
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

    warnings = reader.lifecycle_warnings()
    if warnings:
        out.append("")
        out.append(f"[{_H}]Lifecycle warnings[/]")
        out.extend(f"[{_WARN}]! {escape(str(w))}[/]" for w in warnings)

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
        note = detail or ""
        error = reader.section_error(section.id)
        if error:
            status_text, note = "error", error
        if reader.section_is_partial(section.id):
            note = (note + " · partial").strip(" ·")
        rows.append([section.id, section.kind, status_text, note])
    out.append(_table(["id", "kind", "status", "note"], rows))
    return _lines(*out)


# ── structure ─────────────────────────────────────────────────────────────


def structure_table(reader, limit: int = _MAX_ROWS) -> str:
    """Cartesian geometry, cell parameters, and the bond list."""
    structure = reader.read_structure()
    atoms = structure.atoms
    out = [f"[{_H}]Structure[/]", _kv("atoms", len(atoms))]

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
            out.append(
                _kv(
                    f"  {names[i]}",
                    f"{vec[0]:9.4f} {vec[1]:9.4f} {vec[2]:9.4f}   |{names[i]}| ="
                    f" {np.linalg.norm(vec):8.4f} Å",
                )
            )
    if structure.has_residues:
        out.append(_kv("chains", ", ".join(structure.chain_ids())))

    out.append("")
    rows = []
    biomolecular = structure.has_residues
    headers = ["#", "el", "x (Å)", "y (Å)", "z (Å)"]
    if biomolecular:
        headers += ["res", "seq", "chain", "name"]
    for i, atom in enumerate(atoms[:limit]):
        row = [
            str(i + 1),
            atom.symbol,
            f"{atom.position[0]:10.5f}",
            f"{atom.position[1]:10.5f}",
            f"{atom.position[2]:10.5f}",
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
                    f"{dist:.4f}",
                ]
            )
        out.append(_table(["i", "j", "order", "dist (Å)"], brows))
    return _lines(*out)


# ── per-kind panes ────────────────────────────────────────────────────────


def atom_properties(reader, section_id: str) -> str:
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


def bond_orders(reader, section_id: str) -> str:
    data = reader.read_bond_orders(section_id)
    rows = [
        [
            str(pair.get("i", "")),
            str(pair.get("j", "")),
            f"{float(pair.get('order', 0.0)):.4f}",
            f"{float(pair.get('distance_ang', 0.0)):.4f}" if "distance_ang" in pair else "-",
        ]
        for pair in (data.pairs or [])[:_MAX_ROWS]
    ]
    return _lines(
        f"[{_H}]Bond orders[/]",
        _kv("method", data.method),
        "",
        _table(["i", "j", "order", "dist (Å)"], rows),
    )


def _natural_value_semantics(data) -> str | None:
    """Resolve explicitly declared natural-orbital value semantics."""
    explicit = getattr(data, "occupation_semantics", None)
    if explicit in {_ELECTRON_OCCUPATION, _TRANSITION_WEIGHT}:
        return explicit
    return None


def _frontier_orbitals(
    occ,
    n_orbitals: int,
    orbital_kind: str,
    n_electrons=None,
    occupation_semantics: str | None = None,
):
    """``(homo, lumo, undefined_reason)`` for one spin block.

    An orbital is occupied or it is not. For canonical sets and natural sets
    carrying explicit electron-occupation semantics, the value is an electron
    count per MO. In the case where "highest occupied" means anything the
    canonical value is an integer (2 or 0 restricted, 1 or 0 per spin), while
    a natural occupation can be fractional. Natural transition weights use
    the same storage slot but are never treated as occupations. Rounding to
    nearest is the frontier test and is robust to float round-trip through
    JSON: 1.9999999 is occupied, 0.4 is not.

    Where the occupations are *not* integers, HOMO and LUMO are not merely
    hard to locate, they do not exist. Natural orbitals have genuinely
    fractional occupations (that is what natural orbitals are), and so do
    smeared / finite-temperature canonical orbitals. Picking the last one
    above an arbitrary cut would invent a frontier the wavefunction does not
    have, so nothing is labelled and the reason is surfaced instead. The
    occupation column already carries what there is to say.

    Localized orbitals are excluded for a different reason: they are not
    energy ordered, so "highest" has no referent even when the occupations
    are clean integers.
    """
    if occ is None or n_orbitals == 0:
        return None, None, None
    n = min(len(occ), n_orbitals)
    if n == 0:
        return None, None, None
    if orbital_kind == "localized":
        return None, None, "localized orbitals are not energy ordered"

    counts = np.asarray(occ[:n], dtype=float)
    fractional = not np.allclose(counts, np.rint(counts), atol=_OCC_INTEGER_TOL)
    if orbital_kind == "natural":
        if occupation_semantics == _TRANSITION_WEIGHT:
            return None, None, (
                "transition weights (natural transition orbitals), not electron "
                "occupations, so no frontier is named"
            )
        if occupation_semantics != _ELECTRON_OCCUPATION:
            return None, None, (
                "the archive does not declare whether these natural-orbital values "
                "are electron occupations or transition weights, so no frontier is named"
            )
        if n_electrons is not None:
            total = float(np.sum(counts))
            if abs(total - float(n_electrons)) > _OCC_ELECTRON_TOL:
                return None, None, (
                    f"occupations sum to {total:.4g}, not the {n_electrons} electrons in "
                    "this system: this is a partial or inconsistent occupation set"
                )
    elif fractional:
        # Canonical orbitals with non-integer occupations mean smearing or a
        # finite-temperature ensemble. There the frontier is the Fermi level,
        # not an orbital index, so naming one would be a category error.
        return None, None, (
            "fractional occupations (smearing or an ensemble), so the frontier is a Fermi"
            " level rather than an orbital"
        )

    # Natural orbitals are fractionally occupied by construction, and the
    # literature does still speak of a highest occupied one: Pulay & Hamilton,
    # J. Chem. Phys. 88, 4926 (1988), doi:10.1063/1.454704, § II, classify UHF
    # natural orbitals by occupancy into doubly occupied, fractionally occupied
    # (the active space), and virtual. Their window is 0.02 to 1.98, and they
    # call it "somewhat arbitrary" in as many words, which is why the cut is
    # reported next to the label rather than hidden inside it. Rounding to
    # nearest is a much looser cut than theirs and only ever moves the frontier
    # outward, so it cannot claim occupancy Pulay's window would deny.
    filled = np.rint(counts) > 0
    occupied = np.flatnonzero(filled)
    homo = int(occupied[-1]) if occupied.size else None
    # The LUMO is the lowest virtual *above* the HOMO, not merely the first
    # empty row: a non-aufbau occupation (a hole below a filled level) would
    # otherwise put the LUMO under the HOMO and invert the reported gap.
    search_from = 0 if homo is None else homo + 1
    empty = np.flatnonzero(~filled[search_from:])
    lumo = int(empty[0] + search_from) if empty.size else None
    return homo, lumo, None


def wavefunction(reader, section_id: str) -> str:
    """MO listing — energies, occupations, symmetry labels, HOMO/LUMO gap."""
    data = reader.read_wavefunction_gto(section_id)
    # The electron count is what separates an occupancy vector from a set of
    # transition weights; see _frontier_orbitals.
    try:
        n_electrons = (reader.provenance or {}).get("n_electrons")
    except Exception:  # noqa: BLE001 — provenance is optional, never fatal here
        n_electrons = None
    natural_semantics = _natural_value_semantics(data)
    out = [
        f"[{_H}]Wavefunction (GTO)[/]",
        _kv("spin", data.spin),
        _kv("orbital kind", data.orbital_kind),
        _kv("AO basis functions", data.n_ao),
        _kv("shells", len(data.shells)),
        _kv("spherical (pure)", data.pure),
    ]

    def block(title, energies, occupations, labels):
        if energies is None:
            return ""
        energies = np.asarray(energies, dtype=float)
        occ = np.asarray(occupations, dtype=float) if occupations is not None else None
        # Resolve the frontier orbitals *before* rendering any row. Deciding
        # the marker inside the accumulating loop labelled every occupied
        # orbital "HOMO", because `homo` had been advanced to the current
        # index by the time that row was written. Only the last one is the
        # highest occupied.
        homo, lumo, undefined = _frontier_orbitals(
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
                    f"{energy:+12.6f}",
                    f"{energy * 27.211386245988:+10.4f}",
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
                if natural_semantics == _ELECTRON_OCCUPATION
                else (
                    "weight"
                    if natural_semantics == _TRANSITION_WEIGHT
                    else "value"
                )
            )
        text = _table(["#", "ε (Ha)", "ε (eV)", value_heading, "sym", ""], rows)
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
            if data.orbital_kind == "natural":
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
                gap_ev = (energies[lumo] - energies[homo]) * 27.211386245988
                gap = _kv(f"{frontier_names[0]}-{frontier_names[1]} gap", f"{gap_ev:.4f} eV")
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
    return _lines(*out)


def citations(reader, section_id: str) -> str:
    data = reader.read_citations(section_id)
    return _lines(
        f"[{_H}]Citations (BibTeX)[/]",
        f"[{_K}]copy this into your bibliography — see CLAUDE.md § 8[/]",
        "",
        f"[{_V}]{escape(data.bibtex)}[/]",
    )


def run_record(reader, section_id: str, log_tail: int = _MAX_LOG_LINES) -> str:
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


def job_spec(reader, section_id: str) -> str:
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


def symmetry(reader, section_id: str) -> str:
    data = reader.read_symmetry(section_id)
    return _lines(
        f"[{_H}]Symmetry[/]",
        *(_kv(str(k), v) for k, v in sorted((data.raw or {}).items())),
    )


def tensor_pane(reader, section_id: str, title: str) -> str:
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


def qtaim(reader, section_id: str) -> str:
    data = reader.read_topology_qtaim(section_id)
    rows = []
    for point in (data.points or [])[:_MAX_ROWS]:
        position = point.get("position") or [0, 0, 0]
        rows.append(
            [
                str(point.get("type", "")),
                " ".join(f"{float(c):8.4f}" for c in position),
                f"{float(point.get('rho', 0.0)):.5f}",
                f"{float(point.get('laplacian', 0.0)):+.5f}",
            ]
        )
    out = [f"[{_H}]QTAIM topology[/]", _table(["type", "position (Å)", "ρ", "∇²ρ"], rows)]
    if data.bond_paths:
        out.append("")
        out.append(_kv("bond paths", len(data.bond_paths)))
    return _lines(*out)


def vibrations_table(reader, section_id: str) -> str:
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


def generic_section(reader, section_id: str) -> str:
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
