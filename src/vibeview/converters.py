"""Format converters — convert non-QVF files into in-memory QVF archives.

Each converter takes raw file content (bytes or path) and returns a
``BytesIO`` containing a valid .qvf zip that can be fed to ``QVFReader``.
This lets vibe-view open .xyz, .cif, and other common formats without
requiring a particular quantum-chemistry producer.
"""

from __future__ import annotations

import contextlib
import io
import re
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from vibeview.crystal_builder import cell_from_abc
from vibeview.elements import SYMBOL_TO_Z as _SYMBOL_TO_Z
from vibeview.elements import Z_TO_SYMBOL as _Z_TO_SYMBOL
from vibeview.qvf_writer import structure_archive
from vibeview.trexio_import import TREXIO_EXTENSIONS, is_trexio_directory, trexio_to_qvf
from vibeview.units import BOHR_TO_ANGSTROM

#: Per-axis periodicity of a molecule: the converters' most common case.
MOLECULE: tuple[bool, bool, bool] = (False, False, False)

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import IO


def _read_source(source: str | Path | IO[bytes] | bytes) -> str:
    """The text of a converter source: a path, raw bytes or a binary stream.

    Every text converter reads through here, decoding as UTF-8 with
    replacement so a stray byte in a comment cannot fail the whole file.
    """
    if isinstance(source, str | Path):
        content = Path(source).read_bytes()
    elif isinstance(source, bytes):
        content = source
    else:
        content = source.read()
    return content.decode("utf-8", errors="replace")


def xyz_to_qvf(source: str | Path | IO[bytes] | bytes) -> io.BytesIO:
    """Convert an XYZ file to an in-memory .qvf archive.

    The XYZ format is::

        <n_atoms>
        <comment line>
        <symbol>  <x>  <y>  <z>
        ...

    Reads the first complete frame only; later frames and extra per-atom
    columns are ignored. Positions are assumed to be in angstroms.
    No bonding or lattice
    information is inferred — the resulting QVF is a molecular structure
    with no PBC flags.
    """
    # XYZ has an atom count, a comment (possibly empty), and that many rows.
    # https://openbabel.org/docs/FileFormats/XYZ_cartesian_coordinates_format.html
    lines = _read_source(source).splitlines()
    if not lines:
        raise ValueError("empty XYZ file")
    try:
        n_atoms = int(lines[0])
    except ValueError as exc:
        raise ValueError("invalid XYZ atom count: expected a positive integer") from exc
    if n_atoms <= 0:
        raise ValueError("no atoms in XYZ first frame: atom count must be positive")
    if len(lines) < 2:
        raise ValueError("missing XYZ comment line")
    if len(lines) < n_atoms + 2:
        raise ValueError(f"truncated XYZ first frame: expected {n_atoms} atom records")

    atoms: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines[2:n_atoms + 2], 3):
        parts = line.split()
        if len(parts) < 4:
            raise ValueError(f"invalid XYZ atom record at line {line_number}")
        symbol = parts[0].capitalize()
        try:
            x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
        except ValueError as exc:
            raise ValueError(f"invalid XYZ coordinates at line {line_number}") from exc
        if not np.isfinite([x, y, z]).all():
            raise ValueError(f"non-finite XYZ coordinates at line {line_number}")
        atoms.append({
            "symbol": symbol,
            "position": [x, y, z],
            "atomic_number": _SYMBOL_TO_Z.get(symbol, 0),
        })
    name = Path(source).stem if isinstance(source, (str, Path)) else "xyz"
    return structure_archive(atoms, MOLECULE, calculation=f"xyz:{name}").to_bytesio()


# Fixed so the same SMILES always yields the same structure. ETKDG is
# stochastic; an unseeded embed would hand the user a different geometry
# every time they typed the same molecule, and would make any test of this
# path flaky rather than wrong.
_SMILES_EMBED_SEED = 0xF00D


def smiles_to_qvf(smiles: str, *, add_hydrogens: bool = True) -> io.BytesIO:
    """Build a 3D structure from a SMILES string, as an in-memory QVF.

    RDKit parses the SMILES and embeds it with ETKDG (maintainer decision,
    2026-07-28). Deliberately **no force-field cleanup here**: the geometry
    a user ends up looking at should come from vibe-qc, so the caller hands
    the ETKDG structure to the existing MSINDO live-opt (roadmap decision 2).
    ETKDG alone is already a chemically sensible starting geometry, so the
    result is usable even where vibe-qc is not installed.

    RDKit is an optional extra. It is a large dependency that nobody opening
    a QVF needs, and it is already used the same way for IUPAC naming.
    """
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except ImportError:
        from vibeview.install_hints import install_hint

        raise ValueError(
            "Building from SMILES needs RDKit, which is an optional extra. "
            f"Install it with:  {install_hint('smiles')}"
        ) from None

    text = (smiles or "").strip()
    if not text:
        raise ValueError("no SMILES string given")

    # RDKit reports a bad SMILES by returning None and writing to its own
    # logger, so the reason never reaches the caller. Say which input failed.
    mol = Chem.MolFromSmiles(text)
    if mol is None:
        raise ValueError(f"could not parse SMILES: {text!r}")

    # A SMILES carries no explicit hydrogens; embedding without them gives a
    # heavy-atom skeleton with wrong geometry at every sp3 centre.
    if add_hydrogens:
        mol = Chem.AddHs(mol)

    params = AllChem.ETKDGv3()
    params.randomSeed = _SMILES_EMBED_SEED
    if AllChem.EmbedMolecule(mol, params) != 0:
        # Strained cages and some macrocycles fail the distance-geometry
        # bounds; retrying without them is RDKit's documented escape hatch
        # and beats refusing to draw the molecule at all.
        params.useRandomCoords = True
        if AllChem.EmbedMolecule(mol, params) != 0:
            raise ValueError(
                f"RDKit could not generate 3D coordinates for {text!r}"
            )

    conf = mol.GetConformer()
    atoms: list[tuple[int, float, float, float]] = []
    for i, atom in enumerate(mol.GetAtoms()):
        pos = conf.GetAtomPosition(i)
        atoms.append((int(atom.GetAtomicNum()), float(pos.x), float(pos.y), float(pos.z)))
    if not atoms:
        raise ValueError(f"SMILES produced no atoms: {text!r}")
    return atoms_to_qvf(atoms, label=text)


def atoms_to_qvf(
    atoms: list[tuple[int, float, float, float]],
    label: str = "molecule",
) -> io.BytesIO:
    """Pack ``(Z, x, y, z)`` tuples into an in-memory molecular .qvf archive.

    Positions are in angstroms. This is the entry point for structures that
    are generated rather than read from a file — the toolbar molecule builder
    feeds it the output of ``vibeqc_naming.structure_from_name()``.

    The archive is tagged ``builder:<label>`` in ``manifest.source.calculation``
    so the Files dropdown can show the molecule's name instead of the generic
    ``<in-memory>`` placeholder.
    """
    atom_dicts: list[dict[str, Any]] = []
    for z, x, y, z_coord in atoms:
        z_int = int(z)
        atom_dicts.append(
            {
                "symbol": _Z_TO_SYMBOL.get(z_int, "X"),
                "position": [float(x), float(y), float(z_coord)],
                "atomic_number": z_int,
            }
        )
    if not atom_dicts:
        raise ValueError("cannot build a QVF from zero atoms")
    return structure_archive(atom_dicts, MOLECULE, calculation=f"builder:{label}").to_bytesio()


def _cif_tokens(text: str) -> list[tuple[str, bool]]:
    """Split CIF text into ``(token, quoted)`` pairs per the CIF 1.1 syntax.

    CIF 1.1 (IUCr, "CIF 1.1 syntax specification", paragraphs 15-19 and
    the formal grammar): values are separated by whitespace; a value may be
    delimited by matching single or double quotes, where a quote closes the
    value only when followed by whitespace or the end of the line, so
    ``'O'Neil'`` style apostrophes inside a value survive; a text field is a
    run of lines opened and closed by a semicolon in column 1; ``#`` starts a
    comment outside a quoted value. ``quoted`` marks a delimited value, which
    is data even when it looks like a data name or ``?``.
    """
    tokens: list[tuple[str, bool]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(";"):
            field = [line[1:]]
            i += 1
            while i < len(lines) and not lines[i].startswith(";"):
                field.append(lines[i])
                i += 1
            if i == len(lines):
                raise ValueError("CIF text field opened with ';' is never closed")
            tokens.append(("\n".join(field).strip(), True))
            i += 1
            continue
        pos = 0
        n = len(line)
        while pos < n:
            ch = line[pos]
            if ch.isspace():
                pos += 1
                continue
            if ch == "#":
                break
            if ch in "'\"":
                end = pos + 1
                while True:
                    end = line.find(ch, end)
                    if end == -1:
                        raise ValueError(f"unterminated {ch}-quoted CIF value on line {i + 1}")
                    if end + 1 == n or line[end + 1].isspace():
                        break
                    end += 1
                tokens.append((line[pos + 1 : end], True))
                pos = end + 1
                continue
            end = pos
            while end < n and not line[end].isspace():
                end += 1
            tokens.append((line[pos:end], False))
            pos = end
        i += 1
    return tokens


_CIF_RESERVED = ("data_", "loop_", "save_", "global_", "stop_")


def _cif_items(
    text: str,
) -> tuple[dict[str, str | None], list[list[dict[str, str | None]]]]:
    """Scalar data items and loop tables of a CIF text.

    Returns ``(items, loops)``: the ``_name: value`` pairs outside loops, and
    each ``loop_`` as a list of row dicts keyed by data name, so a consumer
    reads a site as ``row["_atom_site_fract_x"]`` rather than assembling a
    string key per cell.

    Data names are case-insensitive (CIF 1.1), so keys are lower-cased. A
    loop's values are one whitespace-separated stream that may wrap across
    lines, and must fill whole packets. Unquoted ``?`` and ``.`` (unknown,
    inapplicable) become None.
    """
    tokens = _cif_tokens(text)
    items: dict[str, str | None] = {}
    loops: list[list[dict[str, str | None]]] = []

    def _is_keyword(tok: tuple[str, bool]) -> bool:
        value, quoted = tok
        return not quoted and (
            value.startswith("_") or value.lower().startswith(_CIF_RESERVED)
        )

    def _value(tok: tuple[str, bool]) -> str | None:
        value, quoted = tok
        return None if not quoted and value in ("?", ".") else value

    k = 0
    while k < len(tokens):
        value, quoted = tokens[k]
        low = value.lower()
        if not quoted and low == "loop_":
            k += 1
            names: list[str] = []
            while k < len(tokens) and not tokens[k][1] and tokens[k][0].startswith("_"):
                names.append(tokens[k][0].lower())
                k += 1
            values: list[str | None] = []
            while k < len(tokens) and not _is_keyword(tokens[k]):
                values.append(_value(tokens[k]))
                k += 1
            if not names:
                continue
            if len(values) % len(names):
                raise ValueError(
                    f"CIF loop over {names[0]} has {len(values)} values, "
                    f"not a multiple of its {len(names)} data names"
                )
            rows = [
                dict(zip(names, values[row * len(names) : (row + 1) * len(names)], strict=True))
                for row in range(len(values) // len(names))
            ]
            loops.append(rows)
            continue
        if not quoted and value.startswith("_"):
            if k + 1 < len(tokens) and not _is_keyword(tokens[k + 1]):
                items[low] = _value(tokens[k + 1])
                k += 2
            else:
                items[low] = None
                k += 1
            continue
        k += 1  # data_/save_ headers and anything outside an item
    return items, loops


def _cif_number(value: str | None) -> float | None:
    """A CIF numeric value, its standard uncertainty ``5.4300(2)`` dropped."""
    if value is None:
        return None
    return float(value.split("(", 1)[0])


def _cif_element(type_symbol: str | None, label: str | None) -> str | None:
    """Element from ``_atom_site_type_symbol`` (``Na1+``, ``O2-``), else the label.

    Both start with the element symbol; what follows (an oxidation state,
    a site number) is dropped. A two-letter reading wins only when it is a
    real element, so ``CA1`` stays carbon-free guessing aside.
    """
    for raw in (type_symbol, label):
        if not raw:
            continue
        letters = re.match(r"[A-Za-z]+", raw.strip())
        if not letters:
            continue
        run = letters.group(0)
        two = run[:2].capitalize()
        if len(run) >= 2 and two in _SYMBOL_TO_Z:
            return two
        return run[0].upper()
    return None


def cif_to_qvf(source: str | Path | IO[bytes] | bytes) -> io.BytesIO:
    """Convert a CIF file to an in-memory .qvf archive.

    Reads crystallographic information: cell parameters and atom sites
    with fractional coordinates.  Builds lattice vectors, converts
    to Cartesian positions, and sets all PBC flags to True.
    """
    text = _read_source(source)
    # CIF 1.1 tokens, not whitespace-split lines: a quoted label with a
    # space, a loop packet wrapped over two lines, a ';' text field or a
    # '#' comment each misaligned every later column before (#40-3).
    kv, loops = _cif_items(text)

    # ── Cell parameters ────────────────────────────────────────────
    def _get_float(key: str) -> float | None:
        try:
            return _cif_number(kv.get(key))
        except ValueError:
            return None

    a_val = _get_float("_cell_length_a")
    b_val = _get_float("_cell_length_b")
    c_val = _get_float("_cell_length_c")
    alpha = _get_float("_cell_angle_alpha")
    beta = _get_float("_cell_angle_beta")
    gamma = _get_float("_cell_angle_gamma")

    # Build lattice vectors from cell parameters.
    lattice = np.eye(3)
    pbc = [False, False, False]
    if (
        a_val is not None
        and b_val is not None
        and c_val is not None
        and alpha is not None
        and beta is not None
        and gamma is not None
    ):
        lattice = cell_from_abc(a_val, b_val, c_val, alpha, beta, gamma)
        pbc = [True, True, True]

    # ── Atom sites ─────────────────────────────────────────────────
    # Collect all atom-site entries.  CIF can have them in any order;
    # we need to pair _atom_site_type_symbol with _atom_site_fract_x/y/z.
    symbols: list[str] = []
    frac_x: list[float] = []
    frac_y: list[float] = []
    frac_z: list[float] = []
    cartesian_sites = False
    sites = next(
        (
            rows
            for rows in loops
            if rows and ("_atom_site_fract_x" in rows[0] or "_atom_site_cartn_x" in rows[0])
        ),
        [],
    )
    for idx, row in enumerate(sites):
        label = row.get("_atom_site_label")
        sym = _cif_element(row.get("_atom_site_type_symbol"), label)
        fx, fy, fz = (row.get(f"_atom_site_fract_{axis}") for axis in "xyz")
        # A CIF may give sites in Cartesian angstroms instead. That is what a
        # molecule with no cell must use — labelling Cartesian coordinates
        # fract_* would make every consumer misread the file — and it is what
        # this package's own CIF export writes for a non-periodic structure.
        site_is_cartesian = fx is None
        if site_is_cartesian:
            fx, fy, fz = (row.get(f"_atom_site_cartn_{axis}") for axis in "xyz")
        if sym is None or fx is None:
            continue
        # Parse the whole site before keeping any of it, and refuse a bad
        # coordinate: truncating at it silently dropped every later site,
        # and appending the symbol first left the lists unequal (#40-3).
        try:
            xyz = [_cif_number(v) or 0.0 for v in (fx, fy, fz)]
        except ValueError:
            raise ValueError(
                f"CIF atom site {label or idx + 1!s} has a non-numeric coordinate: "
                f"{fx!r} {fy!r} {fz!r}"
            ) from None
        symbols.append(sym)
        frac_x.append(xyz[0])
        frac_y.append(xyz[1])
        frac_z.append(xyz[2])
        cartesian_sites = site_is_cartesian
    if not symbols:
        raise ValueError("no atom sites found in CIF file")

    coords = np.column_stack([frac_x, frac_y, frac_z])
    # Cartesian sites are already angstroms; multiplying them by the lattice
    # would scale a molecule by the (possibly synthesized) cell.
    cart = coords if cartesian_sites else coords @ lattice

    atoms: list[dict[str, Any]] = []
    for i, sym in enumerate(symbols):
        atom: dict[str, Any] = {
            "symbol": sym,
            "position": cart[i].tolist(),
            # QVFReader.read_structure requires atomic_number; 0 = unknown.
            "atomic_number": _SYMBOL_TO_Z.get(sym, 0),
        }
        atoms.append(atom)

    name = Path(source).stem if isinstance(source, (str, Path)) else "cif"
    return structure_archive(atoms, pbc, lattice, calculation=f"cif:{name}").to_bytesio()


def cube_to_qvf(source: str | Path | IO[bytes] | bytes) -> io.BytesIO:
    """Convert a Gaussian cube file to an in-memory .qvf archive.

    Cube files carry both a molecular structure and a 3D volumetric
    scalar field (density, orbital, electrostatic potential, etc.) on
    a regular grid.  The field type is auto-detected from the title
    comment line. For multiple interleaved datasets, import the first one.
    """
    text = _read_source(source)
    lines = text.strip().splitlines()
    if len(lines) < 6:
        raise ValueError("cube file too short")

    # Read both comment lines for field-type detection.
    title = lines[0].strip() if len(lines) > 0 else "cube"
    comment2 = lines[1].strip() if len(lines) > 1 else ""
    combined_comments = f"{title} {comment2}".lower()
    line_idx = 2

    # Auto-detect the volume kind from the title/comments.
    _kind = "volume.density"  # default
    if any(
        kw in combined_comments
        for kw in (
            "orbital",
            " mo ",
            "homo",
            "lumo",
            "molecular orbital",
            "alpha mo",
            "beta mo",
        )
    ):
        _kind = "volume.orbital"
    elif any(kw in combined_comments for kw in ("potential", " esp ", "electrostatic")):
        _kind = "volume.potential"
    elif any(kw in combined_comments for kw in ("spin",)):
        _kind = "volume.spin"
    elif any(kw in combined_comments for kw in ("difference", " diff ", "deformation")):
        _kind = "volume.difference"
    elif any(kw in combined_comments for kw in ("elf", "localization")):
        _kind = "volume.elf"

    # Line 3: n_atoms, origin_x, origin_y, origin_z, optional NVAL.
    parts = lines[line_idx].split()
    line_idx += 1
    if len(parts) not in (4, 5):
        raise ValueError("cube atom/origin record requires four fields and optional NVAL")
    n_atoms = int(parts[0])
    origin = np.array([float(parts[1]), float(parts[2]), float(parts[3])])
    try:
        n_fields = int(parts[4]) if len(parts) == 5 else 1
    except ValueError:
        raise ValueError("cube NVAL must be a positive integer") from None
    if n_fields < 1:
        raise ValueError("cube NVAL must be a positive integer")
    if n_atoms < 0 and n_fields != 1:
        raise ValueError("cube NVAL must be absent or 1 with negative atom count")

    # Lines 4-6: signed counts and row-major voxel vectors. Gaussian cubes
    # use bohr; support the negative-count angstrom extension described by
    # Paul Bourke, "Gaussian Cube Files" (2003):
    # https://paulbourke.net/dataformats/cube/
    voxel_vectors = np.zeros((3, 3))
    shape = [0, 0, 0]
    negative_counts = []
    for axis in range(3):
        parts = lines[line_idx].split()
        line_idx += 1
        n_pts = int(parts[0])
        if n_pts == 0:
            raise ValueError("cube voxel counts must be nonzero")
        shape[axis] = abs(n_pts)
        negative_counts.append(n_pts < 0)
        voxel_vectors[axis] = [float(parts[1]), float(parts[2]), float(parts[3])]
    if any(negative_counts) and not all(negative_counts):
        raise ValueError("cube voxel counts have mixed unit signs")
    angstrom_units = all(negative_counts)
    if angstrom_units:
        origin = origin / BOHR_TO_ANGSTROM
        voxel_vectors = voxel_vectors / BOHR_TO_ANGSTROM

    # Atom lines use the same length unit; QVF atoms are in angstroms.
    atoms: list[dict[str, Any]] = []
    for _ in range(abs(n_atoms)):
        parts = lines[line_idx].split()
        line_idx += 1
        z = int(float(parts[0]))
        pos = np.array([float(parts[2]), float(parts[3]), float(parts[4])])
        pos_ang = pos if angstrom_units else pos * BOHR_TO_ANGSTROM
        symbol = _Z_TO_SYMBOL.get(z, "X")
        atoms.append({"symbol": symbol, "position": pos_ang.tolist(), "atomic_number": z})

    # Negative NATOMS uses DSET_IDS, not NVAL: a positive count and exactly
    # that many integer IDs, possibly wrapped across lines. h5cube's format
    # description also specifies NVAL above (not an official Gaussian spec):
    # https://h5cube-spec.readthedocs.io/en/latest/cubeformat.html
    if n_atoms < 0:
        if line_idx >= len(lines) or not lines[line_idx].split():
            raise ValueError("cube DSET record is missing")
        dset = lines[line_idx].split()
        line_idx += 1
        try:
            n_fields = int(dset[0])
        except ValueError:
            raise ValueError("cube DSET count must be a positive integer") from None
        if n_fields < 1:
            raise ValueError("cube DSET count must be a positive integer")
        ids = dset[1:]
        while len(ids) < n_fields and line_idx < len(lines):
            ids.extend(lines[line_idx].split())
            line_idx += 1
        if len(ids) != n_fields:
            raise ValueError(f"cube DSET record has {len(ids)} IDs, expected {n_fields}")
        try:
            for dataset_id in ids:
                int(dataset_id)
        except ValueError:
            raise ValueError("cube DSET IDs must be integers") from None

    # VMD's cube plugin describes x outermost, then y, z and field innermost.
    # Keep the converter's one-volume contract by selecting the first dataset.
    # https://www.ks.uiuc.edu/Research/vmd/plugins/molfile/cubeplugin.html
    data_parts: list[str] = []
    for remaining_line in lines[line_idx:]:
        data_parts.extend(remaining_line.split())
    expected = shape[0] * shape[1] * shape[2] * n_fields
    if len(data_parts) < expected:
        raise ValueError(f"cube file has {len(data_parts)} data points, expected {expected}")
    data = np.array(data_parts[:expected], dtype=np.float64).reshape(*shape, n_fields)[..., 0]

    # ── Build the QVF archive ────────────────────────────────────
    name = Path(source).stem if isinstance(source, (str, Path)) else "cube"
    source_label = f"cube:{name}"
    if title:
        source_label = f"cube:{title[:40]}"
    builder = structure_archive(atoms, MOLECULE, calculation=source_label)
    builder.add_json(
        "vol_0",
        _kind,
        "grid",
        {
            "origin": origin.tolist(),
            "voxel_vectors": voxel_vectors.tolist(),
            "shape": list(shape),
        },
    )
    builder.add_binary("vol_0", _kind, "data", data, "float32")
    return builder.to_bytesio()


def _pdb_element(line: str) -> str | None:
    """Element symbol for one PDB ATOM/HETATM record, or None if unknown.

    Columns 77-78 carry the element and are authoritative when present.
    Many real files omit them (older entries, MD engine output), so the
    fallback reads the atom name in columns 13-16 using the convention
    that gives that field its meaning: **the element is right-justified
    in columns 13-14**. A one-letter element therefore leaves column 13
    blank, and that single column is what separates a protein backbone
    alpha-carbon ``" CA "`` from a calcium ion ``"CA  "``.

    The previous fallback stripped digits from the whole name, which
    read every backbone ``CA`` as calcium and minted symbols out of
    hydrogen names — on a 150k-atom protein: 885 spurious calciums (the
    file contained none) and ~35,700 atoms typed as ``Hr``/``Hs``/
    ``Hg`` (mercury) and friends. Wrong colours, radii, bonding, and
    wrong chemistry in anything exported downstream.

    A two-character guess is only accepted when it is a real element, so
    hydrogens named ``HR1``/``HG2`` fall back to ``H`` instead of
    inventing one.
    """
    elem = line[76:78].strip()
    if elem:
        # Unknown symbols are preserved, not dropped: every converter
        # must still emit the atom with atomic_number 0 (see
        # TestUnknownElementAtomicNumber). The element table is used to
        # *choose between* candidate readings below, never to reject.
        return elem.capitalize()

    name = line[12:16]
    if len(name) < 4:
        name = name.ljust(4)
    col13, col14 = name[0], name[1]

    if col13 in " 0123456789":
        # Right-justified one-letter element (" CA ", " N  ", " H1 ").
        cand = col14.strip().capitalize()
        return cand or None

    # Column 13 occupied: a genuine two-letter element ("FE", "ZN",
    # "CA" as calcium) — but also 4-character hydrogen names ("HG21").
    # A real two-letter element leaves columns 15-16 blank; a hydrogen
    # spends them on its position suffix. Without that distinction the
    # histidine hydrogens HE1/HE2 read as helium and HG* as mercury.
    if col13 == "H" and name[2:4].strip():
        return "H"
    two = (col13 + col14).strip().capitalize()
    if two in _SYMBOL_TO_Z:
        return two
    one = col13.strip().capitalize()
    if one in _SYMBOL_TO_Z:
        return one
    # Neither reading is a known element: keep the two-letter guess so
    # the atom survives with atomic_number 0 rather than vanishing.
    return two or one or None


def _is_pdb_unit_cell_placeholder(
    a: float, b: float, c: float, alpha: float, beta: float, gamma: float,
    space_group: str, z: int | None,
) -> bool:
    """True for the CRYST1 record of a structure that has no crystal.

    wwPDB Format v3.3 §8 (CRYST1): "If the structure was not determined by
    crystallographic means, CRYST1 simply provides the unitary values", i.e.
    a = b = c = 1.0 Å, alpha = beta = gamma = 90°, space group P 1, Z = 1.
    NMR and cryo-EM entries carry it; it is not a 1 Å periodic cell (#40-2).
    """
    return (
        (a, b, c) == (1.0, 1.0, 1.0)
        and (alpha, beta, gamma) == (90.0, 90.0, 90.0)
        and "".join(space_group.split()) == "P1"
        and z == 1
    )


def pdb_to_qvf(source: str | Path | IO[bytes] | bytes) -> io.BytesIO:
    """Convert a PDB file to an in-memory .qvf archive.

    Reads ATOM/HETATM records for atomic positions and optional CRYST1
    record for unit cell parameters.  PDB coordinates are in angstroms.
    """
    text = _read_source(source)

    atoms: list[dict[str, Any]] = []
    a_val = b_val = c_val = None
    alpha = beta = gamma = None
    space_group = ""
    unit_cell_z = None

    secondary_structure = []
    # wwPDB v3.3 §9: MODEL/ENDMDL bracket one coordinate set each. An NMR
    # ensemble or a trajectory is several copies of the same atoms; overlaying
    # them all drew one molecule per model on top of each other (#40-15). The
    # first model is the structure.
    in_later_model = False
    seen_model = False
    # ATOM/HETATM column 17 (altLoc): alternate conformations of one atom.
    # Keep the first location met for each atom and drop the others.
    seen_alt: set[tuple[str, str, str, str, str]] = set()
    for line in text.splitlines():
        if line.startswith("MODEL "):
            if seen_model:
                in_later_model = True
            seen_model = True
            continue
        if line.startswith("ENDMDL"):
            in_later_model = True
            continue
        if in_later_model and line.startswith(("ATOM", "HETATM", "ANISOU", "TER")):
            continue
        # wwPDB format 3.3, Secondary Structure Section: HELIX class 1/3/5
        # denotes alpha/pi/3-10. Insertion-code ranges cannot be represented
        # by our integer residue keys; do not silently assign them incorrectly.
        if line.startswith(("HELIX ", "SHEET ")):
            helix = line.startswith("HELIX ")
            chain = line[19:20] if helix else line[21:22]
            end_chain = line[31:32] if helix else line[32:33]
            start_code = line[25:26] if helix else line[26:27]
            if chain != end_chain or start_code.strip() or line[37:38].strip():
                continue
            try:
                start = int(line[21:25] if helix else line[22:26])
                end = int(line[33:37])
                entry = {"type": "helix" if helix else "sheet", "chain": chain.strip(),
                         "start_seq": start, "end_seq": end}
                if helix:
                    subtype = {1: "alpha", 3: "pi", 5: "3_10"}.get(int(line[38:40].strip() or "0"))
                    if subtype:
                        entry["subtype"] = subtype
                secondary_structure.append(entry)
            except ValueError:
                pass
            continue

        if line.startswith("CRYST1"):
            try:
                a_val = float(line[6:15])
                b_val = float(line[15:24])
                c_val = float(line[24:33])
                alpha = float(line[33:40])
                beta = float(line[40:47])
                gamma = float(line[47:54])
                space_group = line[55:66]
                unit_cell_z = None
                with contextlib.suppress(ValueError):
                    unit_cell_z = int(line[66:70])
            except (ValueError, IndexError):
                pass
            continue

        if line.startswith(("ATOM", "HETATM")):
            alt_loc = line[16:17].strip()
            if alt_loc:
                # atom name, residue name, chain, residue number, insertion code
                identity = (line[12:16], line[17:20], line[21:22], line[22:26], line[26:27])
                if identity in seen_alt:
                    continue
                seen_alt.add(identity)
            try:
                x = float(line[30:38])
                y = float(line[38:46])
                z = float(line[46:54])
            except (ValueError, IndexError):
                continue
            # Element is in columns 77-78 when present.
            elem = _pdb_element(line)
            if not elem:
                continue
            atom: dict[str, Any] = {
                "symbol": elem,
                "position": [x, y, z],
                # QVFReader.read_structure requires atomic_number; 0 = unknown.
                "atomic_number": _SYMBOL_TO_Z.get(elem, 0),
            }
            # Biomolecular identity (roadmap D1). Kept only when the record
            # actually carries it, so a PDB used as a plain coordinate
            # container does not gain empty keys.
            atom_name = line[12:16].strip()
            residue_name = line[17:20].strip()
            chain_id = line[21:22].strip()
            residue_seq = line[22:26].strip()
            b_factor = line[60:66].strip()
            if atom_name:
                atom["atom_name"] = atom_name
            if residue_name:
                atom["residue_name"] = residue_name
            if chain_id:
                atom["chain_id"] = chain_id
            if residue_seq:
                # A non-numeric residue id is skipped, not fatal.
                with contextlib.suppress(ValueError):
                    atom["residue_seq"] = int(residue_seq)
            if b_factor:
                # Temperature factor, cols 61-66. Predicted structures reuse
                # the field for a per-residue confidence (AlphaFold pLDDT),
                # so it is worth keeping even where crystallography is not
                # what produced the file. Kept per atom rather than as a
                # parallel array so it cannot desynchronize from its atom
                # (QVF spec § 5.1). Unparseable values are skipped, not fatal.
                with contextlib.suppress(ValueError):
                    atom["b_factor"] = float(b_factor)
            atoms.append(atom)

    if not atoms:
        raise ValueError("no ATOM/HETATM records found in PDB file")

    # Build lattice if CRYST1 was present.
    pbc: list[bool] = [False, False, False]
    lattice: list[list[float]] | None = None
    if (
        a_val is not None
        and b_val is not None
        and c_val is not None
        and alpha is not None
        and beta is not None
        and gamma is not None
    ):
        lattice = cell_from_abc(a_val, b_val, c_val, alpha, beta, gamma).tolist()
        pbc = [True, True, True]
        if _is_pdb_unit_cell_placeholder(
            a_val, b_val, c_val, alpha, beta, gamma, space_group, unit_cell_z
        ):
            lattice = None
            pbc = [False, False, False]

    name = Path(source).stem if isinstance(source, (str, Path)) else "pdb"
    return structure_archive(
        atoms,
        pbc,
        lattice,
        secondary_structure=secondary_structure,
        calculation=f"pdb:{name}",
    ).to_bytesio()


def mol2_to_qvf(source: str | Path | IO[bytes] | bytes) -> io.BytesIO:
    """Convert a Tripos Mol2 file to an in-memory .qvf archive.

    Reads the first molecule only. Its ``@<TRIPOS>ATOM`` section supplies
    atomic positions (in Å), and its ``@<TRIPOS>BOND`` section supplies
    explicit connectivity through the source atom IDs.
    """
    text = _read_source(source)

    atoms: list[dict[str, Any]] = []
    atom_indices: dict[int, int] = {}
    raw_bonds: list[tuple[int, int, float]] = []
    section = ""
    molecule_seen = False
    header_rows = 0
    n_atoms: int | None = None
    n_bonds: int | None = None
    order_map = {"1": 1.0, "2": 2.0, "3": 3.0, "ar": 1.5, "am": 1.5}

    # Tripos SYBYL Mol2 specification: counts belong to each MOLECULE;
    # BOND endpoints reference arbitrary integer ATOM IDs, not row numbers.
    # https://www.aideepmed.com/DockRMSD/mol2.pdf
    for line_number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "@<TRIPOS>MOLECULE":
            if molecule_seen:
                break  # Later molecules have independent atom-ID namespaces.
            molecule_seen = True
        if line.startswith("@<TRIPOS>"):
            section = line
            continue

        parts = line.split()
        if section == "@<TRIPOS>MOLECULE":
            if header_rows == 1:
                try:
                    n_atoms = int(parts[0])
                    n_bonds = int(parts[1]) if len(parts) > 1 else None
                except ValueError as exc:
                    raise ValueError("invalid Mol2 molecule counts") from exc
                if n_atoms < 0 or (n_bonds is not None and n_bonds < 0):
                    raise ValueError("invalid Mol2 molecule counts: negative count")
            header_rows += 1
        elif section == "@<TRIPOS>ATOM":
            if len(parts) < 6:
                raise ValueError(f"invalid Mol2 atom record at line {line_number}")
            try:
                atom_id = int(parts[0])
                x, y, z = (float(value) for value in parts[2:5])
            except ValueError as exc:
                raise ValueError(f"invalid Mol2 atom record at line {line_number}") from exc
            if not np.isfinite([x, y, z]).all():
                raise ValueError(f"non-finite Mol2 atom coordinates at line {line_number}")
            if atom_id in atom_indices:
                raise ValueError(f"duplicate Mol2 atom ID {atom_id}")
            # Atom types often include hybridization, e.g. C.3, N.am, O.2.
            atype = parts[5].split(".")[0].capitalize()
            if len(atype) > 2 or atype not in _SYMBOL_TO_Z:
                name_clean = "".join(c for c in parts[1] if not c.isdigit()).strip()
                if name_clean.capitalize() in _SYMBOL_TO_Z:
                    atype = name_clean.capitalize()
            if not atype:
                raise ValueError(f"empty Mol2 atom label at line {line_number}")
            atom_indices[atom_id] = len(atoms)
            atoms.append({
                "symbol": atype,
                "position": [x, y, z],
                "atomic_number": _SYMBOL_TO_Z.get(atype, 0),
            })
        elif section == "@<TRIPOS>BOND":
            if len(parts) < 4:
                raise ValueError(f"invalid Mol2 bond record at line {line_number}")
            try:
                int(parts[0])  # Bond IDs are integers, but need not be sequential.
                origin, target = int(parts[1]), int(parts[2])
            except ValueError as exc:
                raise ValueError(f"invalid Mol2 bond record at line {line_number}") from exc
            raw_bonds.append((origin, target, order_map.get(parts[3], 1.0)))

    if molecule_seen and n_atoms is None:
        raise ValueError("missing Mol2 molecule counts")
    if n_atoms is not None and len(atoms) != n_atoms:
        raise ValueError(f"Mol2 atom count mismatch: expected {n_atoms}, found {len(atoms)}")
    if n_bonds is not None and len(raw_bonds) != n_bonds:
        raise ValueError(f"Mol2 bond count mismatch: expected {n_bonds}, found {len(raw_bonds)}")
    if not atoms:
        raise ValueError("no atoms found in Mol2 file")
    bond_pairs: list[dict[str, Any]] = []
    for origin, target, order in raw_bonds:
        if origin not in atom_indices or target not in atom_indices:
            raise ValueError(f"Mol2 bond references unknown atom ID: {origin}, {target}")
        if origin == target:
            raise ValueError(f"Mol2 bond references the same atom ID: {origin}")
        bond_pairs.append({"i": atom_indices[origin], "j": atom_indices[target], "order": order})

    name = Path(source).stem if isinstance(source, (str, Path)) else "mol2"
    return structure_archive(
        atoms, MOLECULE, bonds=bond_pairs or None, calculation=f"mol2:{name}"
    ).to_bytesio()


def _gaussian_element(label: str) -> str | None:
    """Element of a Gaussian molecule-specification label, or None.

    Gaussian "Molecule Specification": the element label is the chemical
    symbol or the atomic number, optionally followed by other alphanumeric
    characters (``C1``, ``C2``), by parenthesised parameters
    (``C(Fragment=1)``, ``C(Iso=13)``), or by an MM atom type and charge
    (``C-CA-0.25``). Every labelled form was dropped before (#40-6).
    """
    core = label.split("(", 1)[0].split("-", 1)[0]
    if core.isdigit():
        return _Z_TO_SYMBOL.get(int(core))
    letters = re.match(r"[A-Za-z]+", core)
    if not letters:
        return None
    run = letters.group(0)
    if len(run) >= 2 and run[:2].capitalize() in _SYMBOL_TO_Z:
        return run[:2].capitalize()
    one = run[0].upper()
    return one if one in _SYMBOL_TO_Z else None


def gjf_to_qvf(source: str | Path | IO[bytes] | bytes) -> io.BytesIO:
    """Convert a Gaussian input file (.gjf/.com) to a QVF.

    Extracts atom coordinates from the molecule specification section.
    Link0 commands (%mem, %chk, etc.) and route lines (#p ...) are
    skipped.  The charge/multiplicity line is skipped.  Coordinates
    are in angstroms.
    """
    text = _read_source(source)

    atoms: list[dict[str, Any]] = []
    # State machine: skip Link0 commands, route line, title, charge/mult,
    # then read atoms until a blank line or end.
    reading_atoms = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if reading_atoms:
                break  # blank line ends atom list
            continue
        if stripped.startswith("%"):
            continue  # Link0 command
        if stripped.startswith("#"):
            continue  # route line
        if not reading_atoms:
            # Title line or charge/multiplicity.
            parts = stripped.split()
            if len(parts) == 2 and all(p.lstrip("-").isdigit() for p in parts):
                reading_atoms = True  # charge/mult → start reading atoms next line
            continue
        # Atom line: Element-label [Freeze-code] x y z (Cartesian).
        parts = stripped.split()
        symbol = _gaussian_element(parts[0])
        numbers: list[float] = []
        for part in parts[1:]:
            try:
                numbers.append(float(part))
            except ValueError:
                break
        # A freeze code (0 or -1) may precede the three coordinates.
        if len(numbers) == 4 and numbers[0] in (0.0, -1.0) and "." not in parts[1]:
            numbers = numbers[1:]
        if symbol is None or len(numbers) != 3:
            raise ValueError(
                f"Gaussian input atom line {len(atoms) + 1} is not a Cartesian "
                f"'Element-label x y z' line (Z-matrix input is not supported): {stripped!r}"
            )
        atoms.append(
            {"symbol": symbol, "position": numbers, "atomic_number": _SYMBOL_TO_Z[symbol]}
        )

    if not atoms:
        raise ValueError("no atoms found in Gaussian input file")

    name = Path(source).stem if isinstance(source, (str, Path)) else "gjf"
    return structure_archive(atoms, MOLECULE, calculation=f"gjf:{name}").to_bytesio()


# Elements whose atom names in a force field begin with a two-letter symbol
# that is also a one-letter organic element followed by a position letter.
# CA is a C-alpha, NA a heme nitrogen, HE a histidine hydrogen: only a
# one-atom residue (an ion) reads those as calcium, sodium or helium.
_GRO_ORGANIC_FIRST_LETTERS = frozenset("CHNOSP")


def _gro_element(atom_name: str, residue_name: str, *, single_atom_residue: bool) -> str:
    """Element for a GROMACS atom name, which is a force-field name.

    A .gro file has no element column (GROMACS manual, File formats, gro),
    and names such as ``CA``, ``HE1`` and ``ND2`` are a C-alpha, a
    histidine hydrogen and an asparagine nitrogen, not calcium, helium and
    neodymium (#40-1). The first letter is the element, except that a
    two-letter element is accepted for an ion -- a one-atom residue named
    after it (``NA``/``NA``, ``CL``/``CLA``, ``CA``/``CAL``) -- or when the
    first letter cannot start an organic force-field name (``FE``, ``ZN``,
    ``MG``, ``BR``).
    """
    letters = re.match(r"\d*([A-Za-z]+)", atom_name)
    if not letters:
        return "X"
    run = letters.group(1)
    two = run[:2].capitalize()
    if len(run) >= 2 and two in _SYMBOL_TO_Z:
        is_ion = single_atom_residue and residue_name.upper().startswith(run[:2].upper())
        if is_ion or run[0].upper() not in _GRO_ORGANIC_FIRST_LETTERS:
            return two
    one = run[0].upper()
    return one if one in _SYMBOL_TO_Z else "X"


def gro_to_qvf(source: str | Path | IO[bytes] | bytes) -> io.BytesIO:
    """Convert a GROMACS .gro file to a QVF.

    Format: title line, n_atoms, then per-atom lines:
    residue_number residue_name atom_name atom_number x y z [vx vy vz]
    Coordinates and the 3- or 9-value box of the first complete frame are
    converted from nm to Å. Later frames and velocities are ignored.
    """
    text = _read_source(source)
    lines = text.splitlines()
    if len(lines) < 3:
        raise ValueError("GRO file too short")
    try:
        n_atoms = int(lines[1])
    except ValueError as exc:
        raise ValueError("invalid GRO atom count") from exc
    if n_atoms <= 0:
        raise ValueError("no atoms in GRO first frame: count must be positive")
    if len(lines) < n_atoms + 3:
        raise ValueError("truncated GRO first frame: missing atom records or box")

    # GROMACS file-format manual: fixed 5-character metadata fields, then
    # three coordinate fields of width precision+5, inferred from decimal
    # spacing. Packed negative values need not have whitespace separators.
    # https://manual.gromacs.org/current/reference-manual/file-formats.html#gro
    parsed: list[tuple[str, str, str, list[float]]] = []
    coordinate_width: int | None = None
    for line_number, line in enumerate(lines[2:n_atoms + 2], 3):
        if len(line) < 20 or not line[5:10].strip() or not line[10:15].strip():
            raise ValueError(f"invalid GRO atom record at line {line_number}")
        try:
            int(line[:5])  # IDs may wrap and need not be unique or sequential.
            int(line[15:20])
        except ValueError as exc:
            raise ValueError(f"invalid GRO atom identifiers at line {line_number}") from exc
        decimals = [i for i, char in enumerate(line[20:]) if char == "."][:3]
        if len(decimals) != 3:
            raise ValueError(f"invalid GRO coordinate fields at line {line_number}")
        width = decimals[1] - decimals[0]
        if (width < 6 or decimals != [4, 4 + width, 4 + 2 * width]
                or len(line) < 20 + 3 * width
                or (coordinate_width is not None and coordinate_width != width)):
            raise ValueError(f"inconsistent GRO coordinate precision at line {line_number}")
        coordinate_width = width
        try:
            position = [float(line[20 + j * width:20 + (j + 1) * width]) * 10.0
                        for j in range(3)]
        except ValueError as exc:
            raise ValueError(f"invalid GRO coordinates at line {line_number}") from exc
        if not np.isfinite(position).all():
            raise ValueError(f"non-finite GRO coordinates at line {line_number}")
        parsed.append((line[:5].strip(), line[5:10].strip(), line[10:15].strip(), position))

    residue_sizes: dict[tuple[str, str], int] = {}
    for resnr, resname, _name, _pos in parsed:
        residue_sizes[(resnr, resname)] = residue_sizes.get((resnr, resname), 0) + 1

    atoms: list[dict[str, Any]] = []
    for resnr, resname, atom_name, position in parsed:
        elem = _gro_element(
            atom_name, resname, single_atom_residue=residue_sizes[(resnr, resname)] == 1
        )
        atoms.append(
            {"symbol": elem, "position": position, "atomic_number": _SYMBOL_TO_Z.get(elem, 0)}
        )

    # The box follows this frame's atoms, never the final line of a trajectory.
    box_parts = lines[n_atoms + 2].split()
    if len(box_parts) not in {3, 9}:
        raise ValueError("GRO box must contain exactly 3 or 9 values")
    try:
        box = [float(value) * 10.0 for value in box_parts]
    except ValueError as exc:
        raise ValueError("invalid GRO box values") from exc
    if not np.isfinite(box).all():
        raise ValueError("GRO box values must be finite")
    if len(box) == 3:
        cell = [[box[0], 0.0, 0.0], [0.0, box[1], 0.0], [0.0, 0.0, box[2]]]
    else:
        # GRO order: v1x v2y v3z v1y v1z v2x v2z v3x v3y.
        cell = [[box[0], box[3], box[4]], [box[5], box[1], box[6]],
                [box[7], box[8], box[2]]]
    lattice: list[list[float]] | None = None
    pbc = [False, False, False]
    # Exact zero represents no box, consistent with GROMACS guessPbcType:
    # https://github.com/gromacs/gromacs/blob/main/src/gromacs/pbcutil/pbc.cpp
    if any(value != 0.0 for value in box):
        try:
            np.linalg.inv(cell)
        except np.linalg.LinAlgError as exc:
            # QVF requires a full invertible lattice for any periodic axis.
            raise ValueError("singular GRO box: periodic QVF cells must be invertible") from exc
        lattice = cell
        pbc = [True, True, True]

    name = Path(source).stem if isinstance(source, (str, Path)) else "gro"
    return structure_archive(atoms, pbc, lattice, calculation=f"gro:{name}").to_bytesio()


def sdf_to_qvf(source: str | Path | IO[bytes] | bytes) -> io.BytesIO:
    """Convert an MDL SDFile to an in-memory .qvf archive.

    Reads the first molecule only (multi-molecule SDFs take the first).
    Atom block columns: x y z symbol (Å).  Bond block columns:
    i j type (1=single, 2=double, 3=triple, 4=aromatic).
    """
    text = _read_source(source)

    # BIOVIA CTfile Formats: three header lines precede fixed-width V2000
    # counts, followed by exactly that many atom and bond records. Bond
    # endpoints are one-based atom row numbers. Read only the first SD record.
    # https://discover.3ds.com/sites/default/files/2020-08/biovia_ctfileformats_2020.pdf
    lines = text.splitlines()
    if "$$$$" in lines:
        lines = lines[:lines.index("$$$$")]
    if len(lines) < 4:
        raise ValueError("no counts line found in SDF file")
    counts = lines[3]
    if "V3000" in counts:
        raise ValueError("SDF V3000 is not supported")
    try:
        n_atoms = int(counts[:3])
        n_bonds = int(counts[3:6])
    except ValueError as exc:
        raise ValueError("no counts line with valid atom/bond counts found in SDF file") from exc
    if n_atoms < 0 or n_bonds < 0:
        raise ValueError("invalid SDF counts: negative count")
    if len(lines) < 4 + n_atoms + n_bonds:
        raise ValueError("truncated SDF atom or bond block")

    atoms: list[dict[str, Any]] = []
    for i in range(4, 4 + n_atoms):
        line = lines[i]
        if len(line) < 32 or not line[31:34].strip():
            raise ValueError(f"invalid SDF atom record at line {i + 1}")
        try:
            x, y, z = float(line[:10]), float(line[10:20]), float(line[20:30])
        except ValueError as exc:
            raise ValueError(f"invalid SDF atom record at line {i + 1}") from exc
        if not np.isfinite([x, y, z]).all():
            raise ValueError(f"non-finite SDF atom coordinates at line {i + 1}")
        symbol = line[31:34].strip().capitalize()
        atoms.append({
            "symbol": symbol, "position": [x, y, z],
            "atomic_number": _SYMBOL_TO_Z.get(symbol, 0),
        })
    if not atoms:
        raise ValueError("no atoms found in SDF file")

    bond_pairs: list[dict[str, Any]] = []
    bond_start = 4 + n_atoms
    bond_type_map = {1: 1.0, 2: 2.0, 3: 3.0, 4: 1.5}
    for i in range(bond_start, bond_start + n_bonds):
        line = lines[i]
        if len(line) < 9:
            raise ValueError(f"invalid SDF bond record at line {i + 1}")
        try:
            a1, a2, btype = int(line[:3]), int(line[3:6]), int(line[6:9])
        except ValueError as exc:
            raise ValueError(f"invalid SDF bond record at line {i + 1}") from exc
        if not (1 <= a1 <= n_atoms and 1 <= a2 <= n_atoms) or a1 == a2:
            raise ValueError(f"invalid SDF bond atom references at line {i + 1}: {a1}, {a2}")
        bond_pairs.append({"i": a1 - 1, "j": a2 - 1, "order": bond_type_map.get(btype, 1.0)})

    # An extra atom/bond before properties means the declared counts are wrong.
    # Check only the first nonblank trailing record: legacy atom lists and
    # M/A/V/G/S property records start non-connectivity content, whose payload
    # may legitimately look numeric (e.g. an atom alias). Do not parse it.
    for line in lines[bond_start + n_bonds:]:
        if not line.strip():
            continue
        extra_atom = extra_bond = False
        if len(line) >= 32 and line[31:34].strip():
            try:
                float(line[:10]), float(line[10:20]), float(line[20:30])
                extra_atom = True
            except ValueError:
                pass
        if len(line) >= 9:
            try:
                int(line[:3]), int(line[3:6]), int(line[6:9])
                extra_bond = True
            except ValueError:
                pass
        if extra_atom or extra_bond:
            raise ValueError("SDF count mismatch: undeclared atom or bond record")
        break

    name = Path(source).stem if isinstance(source, (str, Path)) else "sdf"
    return structure_archive(
        atoms, MOLECULE, bonds=bond_pairs or None, calculation=f"sdf:{name}"
    ).to_bytesio()


# All supported import extensions (used by directory scanner + filter).
# Formats vibe-view reads itself.

# Formats delegated to ``ase.io.read`` (roadmap decision 4, F1). Listing one
# here is a claim that ``detect_format`` routes it to ASE — it is not a claim
# that ASE is installed; that is checked at conversion time with a message
# naming the extra.
_ASE_EXTENSIONS = (
    ".traj",
    ".extxyz",
    ".vasp",
    ".poscar",
)

# VASP's own files carry no extension at all, and POSCAR/CONTCAR are the most
# common ASE-read inputs in this domain. Matched on the exact stem, uppercase
# as VASP writes them, so an unrelated `contcar.txt` is not swept in.
_ASE_STEMS = ("POSCAR", "CONTCAR")



@dataclass(frozen=True)
class FormatCapability:
    """One format shown by ``vibe-view formats``."""

    format_name: str
    extensions: tuple[str, ...]
    provider: str
    available: bool
    data_kinds: tuple[str, ...]
    description: str
    install_hint: str | None = None
    error: str | None = None


_BUILTIN_FORMATS: tuple[FormatCapability, ...] = (
    FormatCapability("qvf", (".qvf",), "built-in", True, ("all QVF sections",), "QVF archive"),
    FormatCapability("xyz", (".xyz",), "built-in", True, ("structure",), "XYZ structure"),
    FormatCapability(
        "cif", (".cif",), "built-in", True, ("structure", "lattice"), "CIF structure"
    ),
    FormatCapability(
        "cube", (".cube",), "built-in", True, ("structure", "volume"), "Gaussian cube"
    ),
    FormatCapability(
        "pdb", (".pdb",), "built-in", True, ("structure", "biomolecule"), "PDB structure"
    ),
    FormatCapability(
        "mol2", (".mol2",), "built-in", True, ("structure", "bonds"), "Tripos MOL2"
    ),
    FormatCapability(
        "gjf", (".gjf", ".com"), "built-in", True, ("structure", "input"), "Gaussian input"
    ),
    FormatCapability(
        "gro", (".gro",), "built-in", True, ("structure", "lattice"), "GROMACS structure"
    ),
    FormatCapability(
        "sdf", (".sdf", ".mol"), "built-in", True, ("structure", "bonds"), "SDF/Molfile"
    ),
    FormatCapability(
        "py", (".py",), "built-in", True, ("structure", "input"), "vibe-qc Python input"
    ),
)

#: Every extension a built-in converter claims, and the format each names.
_EXTENSION_TO_FORMAT: dict[str, str] = {
    ext: capability.format_name
    for capability in _BUILTIN_FORMATS
    if capability.provider == "built-in"
    for ext in capability.extensions
}
_NATIVE_EXTENSIONS: tuple[str, ...] = tuple(_EXTENSION_TO_FORMAT)
# What the browse page globs for. It previously listed only the native set,
# so files `detect_format` could happily open were never offered — the
# inconsistency the 2026-07-02 audit flagged (F2).
_SUPPORTED_EXTENSIONS = _NATIVE_EXTENSIONS + _ASE_EXTENSIONS + TREXIO_EXTENSIONS


def format_capabilities() -> tuple[FormatCapability, ...]:
    """Describe built-in, optional, and installed plugin importers."""
    from vibeview.install_hints import install_hint

    capabilities = list(_BUILTIN_FORMATS)
    ase_available = find_spec("ase") is not None
    capabilities.append(
        FormatCapability(
            "ase",
            _ASE_EXTENSIONS + _ASE_STEMS,
            "ASE optional extra",
            ase_available,
            ("structure", "lattice"),
            "ASE-supported structure formats",
            None if ase_available else install_hint("ase"),
        )
    )

    trexio_available = find_spec("trexio") is not None
    capabilities.append(
        FormatCapability(
            "trexio",
            TREXIO_EXTENSIONS,
            "TREXIO optional extra",
            trexio_available,
            ("structure", "lattice", "wavefunction.gto"),
            "TREXIO HDF5 files and text directories; real molecular Gaussian orbitals (s–f)",
            None if trexio_available else install_hint("trexio"),
        )
    )

    from vibeview.importers import discover_importers

    for status in discover_importers():
        if status.spec is None:
            capabilities.append(
                FormatCapability(
                    status.entry_point,
                    (),
                    status.distribution or "third-party plugin",
                    False,
                    (),
                    "Plugin failed to load",
                    error=status.error,
                )
            )
            continue
        spec = status.spec
        capabilities.append(
            FormatCapability(
                spec.format_name,
                spec.extensions + spec.stems,
                status.distribution or "third-party plugin",
                True,
                spec.data_kinds,
                spec.description,
            )
        )
    return tuple(capabilities)


def is_supported_path(path: str | Path) -> bool:
    """True when ``convert_to_qvf`` would accept this path.

    The one predicate for "can we open this". Callers that filtered on
    ``endswith(ext)`` silently rejected the extensionless VASP files
    ``detect_format`` accepts, which is how the browse page came to offer a
    different set of files than the opener could actually read.
    """
    return detect_format(path) is not None


def detect_format(path: str | Path) -> str | None:
    """Return the format string for a file, or None if unknown.

    Detects by extension, in this order:

    - a TREXIO text directory, or ``.trexio`` / ``.h5`` / ``.hdf5`` → ``"trexio"``
    - a built-in extension (``vibe-view formats`` lists them: ``.qvf``,
      ``.xyz``, ``.cif``, ``.cube``, ``.pdb``, ``.mol2``, ``.gjf`` / ``.com``,
      ``.gro``, ``.sdf`` / ``.mol``, ``.py``) → that format's name
    - an ASE extension, or the stems ``POSCAR`` / ``CONTCAR`` → ``"ase"``
    - an extension an installed ``vibeview.importers`` plugin claims →
      ``"plugin:<name>"``

    Returns None for anything else.
    """
    if is_trexio_directory(path):
        return "trexio"
    ext = Path(path).suffix.lower()
    if ext in TREXIO_EXTENSIONS:
        return "trexio"
    result = _EXTENSION_TO_FORMAT.get(ext)
    if result:
        return result
    if ext in _ASE_EXTENSIONS:
        return "ase"
    # VASP writes POSCAR/CONTCAR with no extension, so suffix matching alone
    # misses the commonest ASE input in a materials workflow.
    if Path(path).name in _ASE_STEMS:
        return "ase"
    # Built-ins always win extension conflicts. Third-party importers extend
    # the accepted set without being able to replace trusted core parsers.
    from vibeview.importers import detect_importer

    importer = detect_importer(path)
    if importer is not None:
        return f"plugin:{importer.format_name}"
    return None


def _builtin_converters() -> dict[str, Callable[[Any], io.BytesIO]]:
    """Built-in format name -> converter; one table for paths and uploads."""
    return {
        "xyz": xyz_to_qvf,
        "cif": cif_to_qvf,
        "cube": cube_to_qvf,
        "pdb": pdb_to_qvf,
        "mol2": mol2_to_qvf,
        "gjf": gjf_to_qvf,
        "gro": gro_to_qvf,
        "sdf": sdf_to_qvf,
        "qvf": lambda p: io.BytesIO(Path(p).read_bytes()),
        "py": py_to_qvf,
        "ase": ase_to_qvf,
        "trexio": trexio_to_qvf,
    }


# Converters that read their input from bytes as well as from a path. The
# rest (the Python-input parser, ASE, importer plugins) need a file, so an
# upload in one of those formats goes through a temporary copy.
_BYTES_FORMATS = frozenset({"xyz", "cif", "cube", "pdb", "mol2", "gjf", "gro", "sdf", "trexio"})


def convert_to_qvf(path: str | Path, *, format_name: str | None = None) -> io.BytesIO:
    """Auto-detect format and convert to an in-memory QVF.

    ``format_name`` overrides suffix detection and accepts a built-in format
    name or the name of an installed ``vibeview.importers`` plugin. Raises
    ``ValueError`` for unsupported formats.
    """
    fmt = format_name or detect_format(path)
    if fmt is None:
        raise ValueError(f"unsupported file format: {Path(path).suffix}")
    converter = _builtin_converters().get(fmt)
    if converter is not None:
        return converter(path)

    from vibeview.importers import convert_with_importer, get_importer

    importer = get_importer(fmt)
    if importer is None:
        raise ValueError(
            f"unknown importer {fmt!r}; run 'vibe-view formats' to list available formats"
        )
    return convert_with_importer(importer, path)


def convert_bytes_to_qvf(
    data: bytes, filename: str, *, format_name: str | None = None
) -> io.BytesIO:
    """Convert an uploaded file's ``data`` to an in-memory QVF.

    ``filename`` supplies the format (as :func:`convert_to_qvf` detects it
    from a path) unless ``format_name`` is given. The browser upload used its
    own nine-entry converter table, which knew nothing of Python inputs, ASE
    formats or importer plugins (#37-29); it goes through the same table as
    a path now. Raises ``ValueError`` for an unsupported format.
    """
    fmt = format_name or detect_format(filename)
    if fmt is None:
        raise ValueError(f"unsupported file format: {Path(filename).suffix}")
    if fmt == "qvf":
        return io.BytesIO(data)
    if fmt in _BYTES_FORMATS:
        return _builtin_converters()[fmt](data)
    import tempfile

    with tempfile.TemporaryDirectory(prefix="vibeview-upload-") as directory:
        path = Path(directory) / (Path(filename).name or "upload")
        path.write_bytes(data)
        return convert_to_qvf(path, format_name=fmt)


def py_to_qvf(path: str | Path) -> io.BytesIO:
    """Convert a vibe-qc Python input file to an in-memory QVF."""

    from vibeview.input_parser import parse_input_file

    result = parse_input_file(path)
    if result.is_periodic and result.lattice_vectors is None:
        raise ValueError(
            "periodic lattice could not be resolved statically; "
            "provide an explicit 3x3 lattice matrix or import a structure file"
        )

    # vibe-qc inputs carry positions in BOHR and a lattice matrix whose
    # COLUMNS are the lattice vectors (PeriodicSystem convention); the
    # QVF structure contract is Å with lattice vectors as ROWS. Convert,
    # or the viewer renders the geometry inflated 1.89x with no bonds.
    atoms = [
        {**a, "position": [c * BOHR_TO_ANGSTROM for c in a["position"]]} for a in result.atoms
    ]
    lattice = None
    if result.lattice_vectors is not None:
        cols = result.lattice_vectors
        lattice = [
            [cols[j][i] * BOHR_TO_ANGSTROM for j in range(3)] for i in range(3)
        ]

    dimensionality = 0
    if result.is_periodic:
        # Preserve the historical assumption for input styles whose
        # PeriodicSystem dimension the restricted parser cannot resolve.
        dimensionality = 3 if result.dimensionality is None else int(result.dimensionality)
    builder = structure_archive(
        atoms,
        [axis < dimensionality for axis in range(3)],
        lattice,
        calculation=Path(path).stem,
    )
    builder.set_provenance(
        {
            "method": result.method,
            "functional": result.functional,
            "basis": result.basis,
            "charge": result.charge,
            "multiplicity": result.multiplicity,
        }
    )
    return builder.to_bytesio()


def ase_to_qvf(path: str | Path) -> io.BytesIO:
    """Convert any ASE-supported format to an in-memory QVF."""

    try:
        from ase.io import read as ase_read
    except ImportError:
        from vibeview.install_hints import install_hint

        raise ValueError(
            f"{Path(path).name} needs ASE, which is an optional extra. "
            f"Install it with:  {install_hint('ase')}"
        ) from None
    atoms = ase_read(str(path))
    records = [
        {
            "symbol": atoms.symbols[i],
            "atomic_number": int(atoms.numbers[i]),
            "position": [float(atoms.positions[i][j]) for j in range(3)],
        }
        for i in range(len(atoms))
    ]
    pbc = [bool(atoms.pbc[0]), bool(atoms.pbc[1]), bool(atoms.pbc[2])]
    # Carry the cell. Without it a POSCAR arrived flagged periodic with no
    # lattice, which is not a structure anyone can use: `is_periodic` tests
    # for a lattice *and* a pbc flag, so the file rendered as a molecule with
    # no unit cell, no periodic bonding and no replication -- silently, since
    # the atoms all came through and looked right.
    #
    # ASE rows are the lattice vectors, which is the convention the rest of
    # the reader uses (frac = cart @ inv(cell)).
    cell = np.asarray(atoms.cell, dtype=float)
    lattice = cell if cell.shape == (3, 3) and bool(np.any(cell)) else None
    return structure_archive(records, pbc, lattice, calculation=Path(path).stem).to_bytesio()
