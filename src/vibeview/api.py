"""High-level Python API for vibe-view — programmatic access to all QVF operations.

Import with ``from vibeview.api import *`` (``__all__`` lists the public
functions) or use individual functions.

All functions accept ``QVFReader`` instances or file paths.  They are the
programmatic equivalents of the ``vibe-view`` CLI commands.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from vibeview.kinds import classify_section
from vibeview.qvf import QVFReader, QVFSource

__all__ = (
    "capture_structure",
    "capture_volume",
    "closing_reader",
    "diff",
    "export_xyz",
    "get_structure",
    "get_table",
    "get_volume",
    "has_section",
    "info",
    "render_terminal",
    "sections",
    "slice_qvf",
    "validate",
)
from vibeview.units import (
    HARTREE_TO_EV,
    HARTREE_TO_KCAL_MOL,
)

# ── Metadata / inspection ────────────────────────────────────────────────


def info(source: QVFSource | QVFReader) -> dict[str, Any]:
    """Return full metadata for a QVF file as a dict.

    Equivalent to ``vibe-view info --json``. A section's generic ``label``
    is included when it has one, and a ``structure`` section carries its
    chemical identity (``name``, ``charge``, ``multiplicity``, ``smiles``,
    ``inchi``, ``inchikey``; QVF spec § 5.1) as far as it states it.
    ``first_structure`` is the structure a viewer shows first and an export
    without a section takes (spec § 4.1), or None.
    """
    with closing_reader(source) as reader:
        src = reader.manifest.source
        prov = reader.provenance
        sections_out = []
        total_size = 0
        for sec in reader.sections:
            sec_size = 0
            for m in sec.members.values():
                sec_size += reader.stored_size(m) or 0
            total_size += sec_size
            # The status the viewer gives the section: rendered (with a
            # detail such as "via plugin") or skipped with its reason (#113).
            status, detail = classify_section(sec.kind)
            entry: dict[str, Any] = {
                "id": sec.id,
                "kind": sec.kind,
                "n_members": len(sec.members),
                "size_bytes": sec_size,
                "status": status,
                "status_detail": detail,
            }
            label = (sec.model_extra or {}).get("label")
            if isinstance(label, str) and label:
                entry["label"] = label
            if sec.kind == "structure":
                entry.update(reader.structure_identity(sec.id).as_dict())
            sections_out.append(entry)
        return {
            "program": src.program,
            "version": src.version,
            "calculation": src.calculation,
            "qvf_version": reader.manifest.qvf_version,
            "method": prov.get("method"),
            "functional": prov.get("functional"),
            "basis": prov.get("basis"),
            "charge": prov.get("charge"),
            "multiplicity": prov.get("multiplicity"),
            "n_electrons": prov.get("n_electrons"),
            "scf_converged": prov.get("scf_converged"),
            "n_scf_iterations": prov.get("n_scf_iterations"),
            "scf_energy_eh": reader.scf_energy_eh(),
            "wall_seconds": prov.get("wall_seconds"),
            "hostname": prov.get("hostname"),
            "dimensionality": prov.get("dimensionality"),
            "n_sections": len(reader.sections),
            "first_structure": reader.first_structure_id,
            "sections": sections_out,
        }


def sections(source: QVFSource | QVFReader) -> list[dict[str, str]]:
    """Return a list of ``{id, kind}`` dicts for all sections."""
    with closing_reader(source) as reader:
        return [{"id": s.id, "kind": s.kind} for s in reader.sections]


def has_section(source: QVFSource | QVFReader, section_id: str) -> bool:
    """Check if a section exists in the QVF."""
    with closing_reader(source) as reader:
        return reader.has_section(section_id)


# ── Geometry ─────────────────────────────────────────────────────────────


def get_structure(
    source: QVFSource | QVFReader, section_id: str | None = None
) -> dict[str, Any]:
    """Return structure data as a dict with atoms, pbc, and lattice_vectors.

    ``section_id`` names a structure section; by default the shown one, the
    archive's first structure (QVF spec § 4.1) for a fresh reader.
    """
    import numpy as np

    with closing_reader(source) as reader:
        sdata = reader.read_structure(section_id)
        atoms = [
            {
                "symbol": a.symbol,
                "atomic_number": a.atomic_number,
                "position": [float(a.position[0]), float(a.position[1]), float(a.position[2])],
            }
            for a in sdata.atoms
        ]
        result: dict[str, Any] = {"atoms": atoms, "pbc": list(sdata.pbc)}
        if sdata.lattice_vectors is not None:
            result["lattice_vectors"] = np.asarray(sdata.lattice_vectors).tolist()
        return result


def export_xyz(source: QVFSource | QVFReader, section_id: str | None = None) -> str:
    """Return the structure as an XYZ string (``section_id`` as in :func:`get_structure`)."""
    with closing_reader(source) as reader:
        sdata = reader.read_structure(section_id)
        lines = [str(len(sdata.atoms)), "vibe-view export"]
        for a in sdata.atoms:
            x, y, z = a.position
            lines.append(f"{a.symbol:<2} {x:12.6f} {y:12.6f} {z:12.6f}")
        return "\n".join(lines) + "\n"


# ── Comparison ───────────────────────────────────────────────────────────


def diff(source_a: QVFSource | QVFReader, source_b: QVFSource | QVFReader) -> dict[str, Any]:
    """Compare two QVFs: return energy delta, section overlap, geometry RMSD.

    ``geo_rmsd_a`` is ``None`` when the RMSD cannot be taken, and
    ``geo_rmsd_skipped`` then says why: a structure that cannot be read, a
    structure with no atoms, or atom counts that differ (#60). It is ``None``
    when the RMSD was taken.
    """
    import numpy as np

    from vibeview.align import rmsd

    with closing_reader(source_a) as ra, closing_reader(source_b) as rb:
        pa = ra.provenance
        pb = rb.provenance
        ea = ra.scf_energy_eh()
        eb = rb.scf_energy_eh()

        kinds_a = {s.kind for s in ra.sections}
        kinds_b = {s.kind for s in rb.sections}

        geo_rmsd: float | None = None
        rmsd_skipped: str | None = None
        structures = []
        for label, reader in (("A", ra), ("B", rb)):
            try:
                structures.append(reader.read_structure())
            except Exception as exc:  # noqa: BLE001 — reported, the rest of the diff stands
                rmsd_skipped = f"structure {label}: {exc}"
                break
        if rmsd_skipped is None:
            n_a, n_b = len(structures[0].atoms), len(structures[1].atoms)
            if n_a == 0 or n_b == 0:
                # np.array([]) has shape (0,), not (0, 3): rmsd() would raise
                # AxisError, and there is nothing to compare anyway.
                rmsd_skipped = f"no atoms (A: {n_a}, B: {n_b})"
            elif n_a != n_b:
                rmsd_skipped = f"atom counts differ ({n_a} vs {n_b})"
            else:
                pos_a = np.array([a.position for a in structures[0].atoms]).reshape(-1, 3)
                pos_b = np.array([a.position for a in structures[1].atoms]).reshape(-1, 3)
                geo_rmsd = float(rmsd(pos_a, pos_b))

        return {
            "energy_a_eh": ea,
            "energy_b_eh": eb,
            "delta_e_eh": (ea - eb) if ea is not None and eb is not None else None,
            # `is not None`, not truthiness: 0.0 is a valid energy and must
            # still produce converted deltas.
            "delta_e_kcal_mol": (
                (ea - eb) * HARTREE_TO_KCAL_MOL if ea is not None and eb is not None else None
            ),
            "delta_e_ev": (
                (ea - eb) * HARTREE_TO_EV if ea is not None and eb is not None else None
            ),
            "geo_rmsd_a": geo_rmsd,
            "geo_rmsd_skipped": rmsd_skipped,
            "n_sections_a": len(ra.sections),
            "n_sections_b": len(rb.sections),
            "kinds_only_a": sorted(kinds_a - kinds_b),
            "kinds_only_b": sorted(kinds_b - kinds_a),
            "kinds_common": sorted(kinds_a & kinds_b),
            "converged_a": pa.get("scf_converged"),
            "converged_b": pb.get("scf_converged"),
        }


# ── Volume data ──────────────────────────────────────────────────────────


def get_volume(source: QVFSource | QVFReader, section_id: str) -> dict[str, Any] | None:
    """Return a JSON-serializable summary of a volume section's grid.

    Gives the grid geometry (``origin``, ``voxel_vectors``, ``shape``) plus
    the value range (``data_shape``, ``data_min``, ``data_max``) -- **not**
    the voxels themselves, so the result stays serializable. For the
    ``ndarray`` use :meth:`vibeview.QVFReader.read_volume_data`.

    The range is that of the field the viewer draws: the array goes through
    :func:`vibeview.renderers.volume.stored_volume_field`, as in the browser
    and the terminal. A real array is summarised as stored. A complex one,
    which is not valid QVF v1, is reduced by the section's ``component``
    label first (#61).

    Returns ``None`` if the section is absent or is not a volume.
    """
    import numpy as np

    with closing_reader(source) as reader:
        if not reader.has_section(section_id):
            return None
        sec = reader.get_section(section_id)
        if not sec.kind.startswith("volume.") and sec.kind != "basis.ao":
            return None
        from vibeview.renderers.volume import stored_volume_field

        grid = reader.read_volume_grid(section_id)
        data = stored_volume_field(reader.read_volume_data(section_id), sec.component)
        return {
            "kind": sec.kind,
            "origin": np.asarray(grid.origin).tolist(),
            "voxel_vectors": np.asarray(grid.voxel_vectors).tolist(),
            "shape": list(grid.shape),
            "data_shape": list(data.shape),
            "data_min": float(data.min()),
            "data_max": float(data.max()),
        }


# ── Tabular data ─────────────────────────────────────────────────────────


def get_table(source: QVFSource | QVFReader, kind: str) -> tuple[list[str], list[list[Any]]]:
    """Extract tabular data (same as ``vibe-view table --kind``)."""
    from vibeview.tables import extract_table

    with closing_reader(source) as reader:
        return extract_table(reader, kind)


# ── Validation ───────────────────────────────────────────────────────────


def validate(
    source: QVFSource | QVFReader, *, limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate with qvf's reference validator (requires the ``validation`` extra).

    Returns ``valid``, ``errors``, ``warnings`` and structured ``findings``
    (severity, code, where, message). Invalid archives include ``error``;
    valid archives include ``n_sections`` and ``n_members``. Hash/layout
    failures retain the member diagnostic fields when available. A viewer
    readability failure adds ``reader_error`` separately from qvf findings.
    An unreadable container or manifest raises ``QVFOpenError`` with the
    reference report retained in its ``validation_report`` attribute.

    ``limits`` supplies overrides to qvf's ``Limits.from_dict``; unknown keys
    raise. The reference validator checks resource limits before reading
    payloads. Caller-owned streams and readers remain open and keep their
    stream position. An existing reader validates its original archive,
    without applying geometry edits. Missing validation support raises
    ``QVFError`` rather than silently performing a partial check.
    """
    from vibeview.qvf_validation import validate_archive

    if isinstance(source, QVFReader):
        return source.validation_report(limits=limits)
    return validate_archive(source, limits=limits)


# ── Capture ──────────────────────────────────────────────────────────────


def capture_structure(source: QVFSource | QVFReader, path: str | Path, **kwargs: Any) -> bool:
    """Render the structure to a PNG file."""
    with closing_reader(source) as reader:
        from vibeview.capture import capture_structure as _cap

        return _cap(reader, Path(path), **kwargs)


def capture_volume(
    source: QVFSource | QVFReader, section_id: str, path: str | Path, **kwargs: Any
) -> bool:
    """Render a volume section to a PNG file."""
    with closing_reader(source) as reader:
        from vibeview.capture import capture_volume as _cap

        return _cap(reader, section_id, Path(path), **kwargs)


def render_terminal(
    source: QVFSource | QVFReader,
    section_id: str | None = None,
    *,
    size: tuple[int, int] = (100, 30),
    plain: bool = False,
    **kwargs: Any,
) -> str:
    """Render a section as terminal text and return it.

    The text counterpart of :func:`capture_structure` / :func:`capture_volume`:
    same renderers, same colours, but the output is a string of braille
    characters instead of a PNG — and unlike the capture functions it needs no
    OpenGL context, so it works on a headless compute node.

    ``section_id`` defaults to the structure section. ``plain=True`` drops the
    ANSI colour for logs and pipes. Extra keyword arguments are passed to
    :func:`vibeview.tui.show.render_section` (``mode``, ``representation``,
    ``color_mode``, ``replication``, ``isovalue``, ``rotation``,
    ``show_labels``, ``frame``, ``chart``, ``band_energy_unit``).

    Returns a human-readable explanation rather than raising when the section
    has no graphical form (a citations block, say), so a caller sweeping every
    section never has to pre-filter by kind.
    """
    with closing_reader(source) as reader:
        from vibeview.tui.show import default_section, render_section

        target = section_id or default_section(reader)
        if target is None:
            return "archive contains no sections"
        grid = render_section(reader, target, size[0], size[1], **kwargs)
        if grid is None:
            kind = reader.get_section(target).kind
            return f"section {target!r} ({kind}) has no graphical form"
        return grid.to_plain() if plain else grid.to_ansi()


# ── Slicing / merging ────────────────────────────────────────────────────


def slice_qvf(
    source: QVFSource | QVFReader,
    output: str | Path,
    *,
    keep: list[str] | None = None,
    drop: list[str] | None = None,
) -> Path:
    """Extract sections without discarding metadata. Refuse dangling references.

    Viewer hints for removed sections are pruned. All other source fields and
    kept member bytes are preserved; the output is validated before replacement.
    """

    from vibeview.archive import write_archive

    if keep and drop:
        raise ValueError("keep and drop are mutually exclusive")
    with closing_reader(source) as reader:
        out_path = Path(output)
        if out_path.suffix != ".qvf":
            out_path = out_path.with_suffix(".qvf")

        keep_ids = set(keep or [])
        drop_ids = set(drop or [])

        to_keep = []
        for sec in reader.sections:
            if drop_ids:
                if sec.id in drop_ids or sec.kind in drop_ids:
                    continue
            elif keep_ids and sec.id not in keep_ids and sec.kind not in keep_ids:
                continue
            to_keep.append(sec)

        if not to_keep:
            raise ValueError("No sections to keep")
        # Read the original JSON: model round-tripping loses unknown source
        # and member fields even though they are allowed by the QVF schema.
        manifest = reader.manifest_json()
        kept_ids = {sec.id for sec in to_keep}
        removed_ids = {sec.id for sec in reader.sections} - kept_ids
        manifest["sections"] = [s for s in manifest["sections"] if s["id"] in kept_ids]
        for section in manifest["sections"]:
            for key, value in section.items():
                if (
                    (key.endswith("_ref") or key in ("operand_a", "operand_b"))
                    and isinstance(value, str) and value in removed_ids
                ):
                    raise ValueError(f"Section {section['id']!r} still references {value!r}")
        hints = manifest.get("viewer_defaults", {})
        if "auto_open" in hints:
            hints["auto_open"] = [sid for sid in hints["auto_open"] if sid not in removed_ids]
        for sid in removed_ids:
            hints.pop(sid, None)
        kept_paths = {m.path for sec in to_keep for m in sec.members.values()}
        from vibeview.qvf import QVFError

        unreadable: list[QVFError] = []

        def members() -> Iterator[tuple[str, bytes]]:
            for member_path in sorted(kept_paths):
                try:
                    yield member_path, reader.read_member_bytes(member_path)
                except QVFError as exc:
                    unreadable.append(exc)
                    raise

        try:
            return write_archive(out_path, manifest, members())
        except QVFError as exc:
            # A source member that cannot be read is the source's error, not
            # an invalid slice: report it as the QVFError it is (#39-35).
            if unreadable:
                raise unreadable[0] from None
            raise ValueError(f"Invalid slice: {exc}") from exc


# ── Helpers ──────────────────────────────────────────────────────────────


@contextlib.contextmanager
def closing_reader(source: QVFSource | QVFReader) -> Iterator[QVFReader]:
    """Borrow an existing reader, or close a reader opened from a source.

    Only readers created by this context are closed, on both normal return
    and exceptions. Caller-owned readers and binary streams remain open.
    """
    if isinstance(source, QVFReader):
        yield source
    else:
        with QVFReader(source) as reader:
            yield reader
