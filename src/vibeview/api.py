"""High-level Python API for vibe-view — programmatic access to all QVF operations.

Import with ``from vibeview.api import *`` or use individual functions.

All functions accept ``QVFReader`` instances or file paths.  They are the
programmatic equivalents of the ``vibe-view`` CLI commands.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vibeview.qvf import QVFReader, QVFSource

# ── Metadata / inspection ────────────────────────────────────────────────


def info(source: QVFSource) -> dict[str, Any]:
    """Return full metadata for a QVF file as a dict.

    Equivalent to ``vibe-view info --json``.
    """
    reader, should_close = _reader(source)
    try:
        src = reader.manifest.source
        prov = _get_prov(reader)
        sections_out = []
        total_size = 0
        for sec in reader.sections:
            sec_size = 0
            for m in sec.members.values():
                try:
                    sec_size += reader._zf.getinfo(m.path).file_size
                except Exception:
                    pass
            total_size += sec_size
            sections_out.append(
                {
                    "id": sec.id,
                    "kind": sec.kind,
                    "n_members": len(sec.members),
                    "size_bytes": sec_size,
                }
            )
        energy = prov.get("scf_energy", {})
        if isinstance(energy, dict):
            energy = energy.get("value")
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
            "scf_energy_eh": energy,
            "wall_seconds": prov.get("wall_seconds"),
            "hostname": prov.get("hostname"),
            "dimensionality": prov.get("dimensionality"),
            "n_sections": len(reader.sections),
            "sections": sections_out,
        }
    finally:
        if should_close:
            reader.close()


def sections(source: QVFSource) -> list[dict[str, str]]:
    """Return a list of ``{id, kind}`` dicts for all sections."""
    reader, should_close = _reader(source)
    try:
        return [{"id": s.id, "kind": s.kind} for s in reader.sections]
    finally:
        if should_close:
            reader.close()


def has_section(source: QVFSource, section_id: str) -> bool:
    """Check if a section exists in the QVF."""
    reader, should_close = _reader(source)
    try:
        return reader.has_section(section_id)
    finally:
        if should_close:
            reader.close()


# ── Geometry ─────────────────────────────────────────────────────────────


def get_structure(source: QVFSource) -> dict[str, Any]:
    """Return structure data as a dict with atoms, pbc, and lattice_vectors."""
    import numpy as np

    reader, should_close = _reader(source)
    try:
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
    finally:
        if should_close:
            reader.close()


def export_xyz(source: QVFSource) -> str:
    """Return the structure as an XYZ string."""
    reader, should_close = _reader(source)
    try:
        sdata = reader.read_structure()
        lines = [str(len(sdata.atoms)), "vibe-view export"]
        for a in sdata.atoms:
            x, y, z = a.position
            lines.append(f"{a.symbol:<2} {x:12.6f} {y:12.6f} {z:12.6f}")
        return "\n".join(lines) + "\n"
    finally:
        if should_close:
            reader.close()


# ── Comparison ───────────────────────────────────────────────────────────


def diff(source_a: QVFSource, source_b: QVFSource) -> dict[str, Any]:
    """Compare two QVFs: return energy delta, section overlap, geometry RMSD."""
    import numpy as np

    from vibeview.align import rmsd

    ra, ca = _reader(source_a)
    rb, cb = _reader(source_b)
    try:
        pa = _get_prov(ra)
        pb = _get_prov(rb)
        ea = _energy_eh(pa)
        eb = _energy_eh(pb)

        kinds_a = {s.kind for s in ra.sections}
        kinds_b = {s.kind for s in rb.sections}

        geo_rmsd = None
        try:
            sa = ra.read_structure()
            sb = rb.read_structure()
            pos_a = np.array([a.position for a in sa.atoms])
            pos_b = np.array([a.position for a in sb.atoms])
            if pos_a.shape == pos_b.shape:
                geo_rmsd = float(rmsd(pos_a, pos_b))
        except Exception:
            pass

        return {
            "energy_a_eh": ea,
            "energy_b_eh": eb,
            "delta_e_eh": (ea - eb) if ea is not None and eb is not None else None,
            # `is not None`, not truthiness: 0.0 is a valid energy and must
            # still produce converted deltas.
            "delta_e_kcal_mol": (
                (ea - eb) * 627.509 if ea is not None and eb is not None else None
            ),
            "delta_e_ev": ((ea - eb) * 27.2114 if ea is not None and eb is not None else None),
            "geo_rmsd_a": geo_rmsd,
            "n_sections_a": len(ra.sections),
            "n_sections_b": len(rb.sections),
            "kinds_only_a": sorted(kinds_a - kinds_b),
            "kinds_only_b": sorted(kinds_b - kinds_a),
            "kinds_common": sorted(kinds_a & kinds_b),
            "converged_a": pa.get("scf_converged"),
            "converged_b": pb.get("scf_converged"),
        }
    finally:
        if ca:
            ra.close()
        if cb:
            rb.close()


# ── Volume data ──────────────────────────────────────────────────────────


def get_volume(source: QVFSource, section_id: str) -> dict[str, Any] | None:
    """Return a JSON-serializable summary of a volume section's grid.

    Gives the grid geometry (``origin``, ``voxel_vectors``, ``shape``) plus
    the value range (``data_shape``, ``data_min``, ``data_max``) -- **not**
    the voxels themselves, so the result stays serializable. For the
    ``ndarray`` use :meth:`vibeview.QVFReader.read_volume_data`.

    Returns ``None`` if the section is absent or is not a volume.
    """
    import numpy as np

    reader, should_close = _reader(source)
    try:
        if not reader.has_section(section_id):
            return None
        sec = reader.get_section(section_id)
        if not sec.kind.startswith("volume.") and sec.kind != "basis.ao":
            return None
        grid = reader.read_volume_grid(section_id)
        data = reader.read_volume_data(section_id)
        return {
            "kind": sec.kind,
            "origin": np.asarray(grid.origin).tolist(),
            "voxel_vectors": np.asarray(grid.voxel_vectors).tolist(),
            "shape": list(grid.shape),
            "data_shape": list(data.shape),
            "data_min": float(data.min()),
            "data_max": float(data.max()),
        }
    finally:
        if should_close:
            reader.close()


# ── Tabular data ─────────────────────────────────────────────────────────


def get_table(source: QVFSource, kind: str) -> tuple[list[str], list[list]]:
    """Extract tabular data (same as ``vibe-view table --kind``)."""
    from vibeview.tables import extract_table

    reader, should_close = _reader(source)
    try:
        return extract_table(reader, kind)
    finally:
        if should_close:
            reader.close()


# ── Validation ───────────────────────────────────────────────────────────


def validate(source: QVFSource) -> dict[str, Any]:
    """Validate QVF integrity: schema + all SHA-256 hashes.

    Returns ``{"valid": True, "n_sections": N, "n_members": M}`` on success,
    or ``{"valid": False, "error": "..."}`` on failure.
    """
    from vibeview.qvf import QVFError, SHA256MismatchError

    reader, should_close = _reader(source)
    try:
        n_checked = 0
        for sec in reader.sections:
            for name, member in sec.members.items():
                try:
                    reader._verify_and_read(member)
                    n_checked += 1
                except SHA256MismatchError as e:
                    return {"valid": False, "error": str(e), "section": sec.id, "member": name}
        return {"valid": True, "n_sections": len(reader.sections), "n_members": n_checked}
    except QVFError as e:
        return {"valid": False, "error": str(e)}
    finally:
        if should_close:
            reader.close()


# ── Capture ──────────────────────────────────────────────────────────────


def capture_structure(source: QVFSource, path: str | Path, **kwargs) -> bool:
    """Render the structure to a PNG file."""
    reader, should_close = _reader(source)
    from vibeview.capture import capture_structure as _cap

    # Close readers we opened ourselves (path sources) — like every other
    # api function — or the zip handle leaks.
    try:
        return _cap(reader, Path(path), **kwargs)
    finally:
        if should_close:
            reader.close()


def capture_volume(source: QVFSource, section_id: str, path: str | Path, **kwargs) -> bool:
    """Render a volume section to a PNG file."""
    reader, should_close = _reader(source)
    from vibeview.capture import capture_volume as _cap

    try:
        return _cap(reader, section_id, Path(path), **kwargs)
    finally:
        if should_close:
            reader.close()


def render_terminal(
    source: QVFSource,
    section_id: str | None = None,
    *,
    size: tuple[int, int] = (100, 30),
    plain: bool = False,
    **kwargs,
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
    reader, should_close = _reader(source)
    from vibeview.tui.show import default_section, render_section

    try:
        target = section_id or default_section(reader)
        if target is None:
            return "archive contains no sections"
        grid = render_section(reader, target, size[0], size[1], **kwargs)
        if grid is None:
            kind = reader.get_section(target).kind
            return f"section {target!r} ({kind}) has no graphical form"
        return grid.to_plain() if plain else grid.to_ansi()
    finally:
        if should_close:
            reader.close()


# ── Slicing / merging ────────────────────────────────────────────────────


def slice_qvf(
    source: QVFSource,
    output: str | Path,
    *,
    keep: list[str] | None = None,
    drop: list[str] | None = None,
) -> Path:
    """Extract sections without discarding metadata. Refuse dangling references.

    Viewer hints for removed sections are pruned. All other source fields and
    kept member bytes are preserved; the output is validated before replacement.
    """
    import json as _json
    import os
    import tempfile
    import zipfile

    if keep and drop:
        raise ValueError("keep and drop are mutually exclusive")
    reader, should_close = _reader(source)
    out_path = Path(output)
    if out_path.suffix != ".qvf":
        out_path = out_path.with_suffix(".qvf")

    keep_ids = set(keep or [])
    drop_ids = set(drop or [])

    try:
        to_keep = []
        for sec in reader.sections:
            if drop_ids:
                if sec.id in drop_ids or sec.kind in drop_ids:
                    continue
            elif keep_ids:
                if sec.id not in keep_ids and sec.kind not in keep_ids:
                    continue
            to_keep.append(sec)

        if not to_keep:
            raise ValueError("No sections to keep")
        # Read the original JSON: model round-tripping loses unknown source
        # and member fields even though they are allowed by the QVF schema.
        manifest = _json.loads(reader._zf.read("manifest.json"))
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
        # A temporary sibling also makes slicing onto the input path safe.
        with tempfile.NamedTemporaryFile(dir=out_path.parent, suffix=".qvf", delete=False) as f:
            temporary = Path(f.name)
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as zf_out:
                zf_out.writestr("manifest.json", _json.dumps(manifest),
                                compress_type=zipfile.ZIP_STORED)
                for mp in sorted(kept_paths):
                    zf_out.writestr(mp, reader._zf.read(mp))
            result = validate(temporary)
            if not result["valid"]:
                raise ValueError(f"Invalid slice: {result['error']}")
            os.replace(temporary, out_path)
        finally:
            temporary.unlink(missing_ok=True)
        return out_path
    finally:
        if should_close:
            reader.close()


# ── Helpers ──────────────────────────────────────────────────────────────


def _reader(source: QVFSource) -> tuple[QVFReader, bool]:
    """Coerce a source into a QVFReader. Returns (reader, should_close)."""
    if isinstance(source, QVFReader):
        return source, False
    return QVFReader(source), True


def _close(reader: QVFReader, should_close: bool) -> None:
    if should_close:
        reader.close()


def _get_prov(reader: QVFReader) -> dict:
    prov = getattr(reader.manifest, "provenance", None) or {}
    if hasattr(prov, "model_dump"):
        prov = prov.model_dump()
    return prov if isinstance(prov, dict) else {}


def _energy_eh(prov: dict) -> float | None:
    e = prov.get("scf_energy", {})
    if isinstance(e, dict):
        e = e.get("value")
    return float(e) if e is not None else None
