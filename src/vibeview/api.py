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

    Equivalent to ``vibe-view info --json``.
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
            sections_out.append(
                {
                    "id": sec.id,
                    "kind": sec.kind,
                    "n_members": len(sec.members),
                    "size_bytes": sec_size,
                }
            )
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


def get_structure(source: QVFSource | QVFReader) -> dict[str, Any]:
    """Return structure data as a dict with atoms, pbc, and lattice_vectors."""
    import numpy as np

    with closing_reader(source) as reader:
        sdata = reader.read_structure()
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


def export_xyz(source: QVFSource | QVFReader) -> str:
    """Return the structure as an XYZ string."""
    with closing_reader(source) as reader:
        sdata = reader.read_structure()
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


def validate(source: QVFSource | QVFReader) -> dict[str, Any]:
    """Validate QVF integrity: schema, every SHA-256 hash, and binary layouts.

    Returns ``{"valid": True, "n_sections": N, "n_members": M}`` on success,
    or ``{"valid": False, "error": "..."}`` on a failure. A failing member
    adds ``"section"``, ``"member"`` and ``"reason"``: ``"sha256"`` when its
    bytes do not match their digest, ``"layout"`` when they match it but do
    not fill the declared ``dtype`` and ``shape`` (#65). An archive that cannot be
    opened at all raises instead: ``QVFOpenError`` for an unreadable
    container or manifest, ``ManifestValidationError`` or
    ``CriticalSectionUnsupportedError`` for a manifest the reader refuses —
    every one a ``QVFError``.
    """
    from vibeview.qvf import QVFError, SHA256MismatchError

    with closing_reader(source) as reader:
        try:
            n_checked = 0
            for sec in reader.sections:
                for name, member in sec.members.items():
                    try:
                        data = reader._verify_and_read(member)
                        n_checked += 1
                    except SHA256MismatchError as e:
                        return {
                            "valid": False,
                            "error": str(e),
                            "section": sec.id,
                            "member": name,
                            "reason": "sha256",
                        }
                    problem = _binary_layout_problem(member, len(data))
                    if problem is not None:
                        # A hash only proves the bytes are the ones written;
                        # a declared shape of [10**12] over 3 values passed
                        # as OK (#63).
                        return {
                            "valid": False,
                            "error": f"member {member.path!r} in section {sec.id!r}: {problem}",
                            "section": sec.id,
                            "member": name,
                            "reason": "layout",
                        }
            return {"valid": True, "n_sections": len(reader.sections), "n_members": n_checked}
        except QVFError as e:
            return {"valid": False, "error": str(e)}


def _binary_layout_problem(member: Any, n_bytes: int) -> str | None:
    """Why a binary member's bytes do not fill its declared dtype and shape, or None."""
    import numpy as np

    if getattr(member, "format", None) != "binary":
        return None
    if member.dtype is None and member.shape is None:
        return None  # opaque bytes: a run log, a citation file
    # The reader's default when only a shape is declared.
    dtype_name = member.dtype or "float32"
    try:
        itemsize = np.dtype(dtype_name).itemsize
    except TypeError:
        return f"dtype {dtype_name!r} is not a numpy dtype"
    if member.shape is None:
        if itemsize and n_bytes % itemsize:
            return f"{n_bytes} bytes is not a whole number of {dtype_name} values"
        return None
    # Python integers: a declared shape can multiply past 64 bits.
    expected = itemsize
    for extent in member.shape:
        expected *= int(extent)
    if expected != n_bytes:
        return (
            f"declared dtype={dtype_name!r}, shape={list(member.shape)} needs "
            f"{expected:,} bytes; the member holds {n_bytes:,}"
        )
    return None


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
    ``show_labels``, ``frame``, ``chart``).

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
