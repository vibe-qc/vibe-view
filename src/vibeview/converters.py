"""Format converters — convert non-QVF files into in-memory QVF archives.

Each converter takes raw file content (bytes or path) and returns a
``BytesIO`` containing a valid .qvf zip that can be fed to ``QVFReader``.
This lets vibe-view open .xyz, .cif, and other common formats without
requiring a particular quantum-chemistry producer.
"""

from __future__ import annotations

import contextlib
import io
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from vibeview.elements import SYMBOL_TO_Z as _SYMBOL_TO_Z
from vibeview.elements import Z_TO_SYMBOL as _Z_TO_SYMBOL
from vibeview.qvf_writer import structure_archive
from vibeview.trexio_import import TREXIO_EXTENSIONS, is_trexio_directory, trexio_to_qvf
from vibeview.units import BOHR_TO_ANGSTROM

#: Per-axis periodicity of a molecule: the converters' most common case.
MOLECULE: tuple[bool, bool, bool] = (False, False, False)

if TYPE_CHECKING:
    from typing import IO


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
    if isinstance(source, (str, Path)):
        content = Path(source).read_bytes()
    elif isinstance(source, bytes):
        content = source
    else:
        content = source.read()
    # XYZ has an atom count, a comment (possibly empty), and that many rows.
    # https://openbabel.org/docs/FileFormats/XYZ_cartesian_coordinates_format.html
    lines = content.decode("utf-8", errors="replace").splitlines()
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


def cif_to_qvf(source: str | Path | IO[bytes] | bytes) -> io.BytesIO:
    """Convert a CIF file to an in-memory .qvf archive.

    Reads crystallographic information: cell parameters and atom sites
    with fractional coordinates.  Builds lattice vectors, converts
    to Cartesian positions, and sets all PBC flags to True.
    """
    if isinstance(source, (str, Path)):
        content = Path(source).read_bytes()
    elif isinstance(source, bytes):
        content = source
    else:
        content = source.read()
    text = content.decode("utf-8", errors="replace")
    # Parse CIF key-value pairs.  CIF is loosely structured; we handle
    # both simple key-value lines and loop_ blocks.
    kv: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#") or line.startswith("data_"):
            continue
        if line.startswith("loop_"):
            # Collect the loop header keys.
            loop_keys: list[str] = []
            while i < len(lines):
                hdr = lines[i].strip()
                if not hdr or not hdr.startswith("_"):
                    break
                loop_keys.append(hdr)
                i += 1
            if not loop_keys:
                continue
            # Read data rows until we hit a blank line or another keyword.
            row_idx = 0
            while i < len(lines):
                data_line = lines[i].strip()
                if (
                    not data_line
                    or data_line.startswith("_")
                    or data_line.startswith("loop_")
                    or data_line.startswith("data_")
                ):
                    break
                parts = data_line.split()
                if len(parts) < len(loop_keys):
                    i += 1
                    continue
                for ki, key in enumerate(loop_keys):
                    kv[f"{key}_{row_idx}"] = parts[ki]
                row_idx += 1
                i += 1
        elif line.startswith("_"):
            # Simple key-value line: "_key value" or just "_key"
            parts = line.split(None, 1)
            key = parts[0]
            val = parts[1].strip().strip("'\"") if len(parts) > 1 else "?"
            if val in ("?", "."):
                continue
            kv[key] = val

    # ── Cell parameters ────────────────────────────────────────────
    def _get_float(*keys: str) -> float | None:
        for k in keys:
            v = kv.get(k)
            if v is not None:
                # Remove parenthesised uncertainties like 5.4300(2)
                if "(" in v:
                    v = v.split("(")[0]
                try:
                    return float(v)
                except ValueError:
                    continue
        return None

    a_val = _get_float("_cell_length_a", "_cell_length_a")
    b_val = _get_float("_cell_length_b", "_cell_length_b")
    c_val = _get_float("_cell_length_c", "_cell_length_c")
    alpha = _get_float("_cell_angle_alpha", "_cell_angle_alpha")
    beta = _get_float("_cell_angle_beta", "_cell_angle_beta")
    gamma = _get_float("_cell_angle_gamma", "_cell_angle_gamma")

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
        a_, b_, c_ = a_val, b_val, c_val
        al = np.radians(alpha)
        be = np.radians(beta)
        ga = np.radians(gamma)
        cos_a, cos_b, cos_g = np.cos(al), np.cos(be), np.cos(ga)
        sin_g = np.sin(ga)
        lattice[0] = [a_, 0.0, 0.0]
        lattice[1] = [b_ * cos_g, b_ * sin_g, 0.0]
        vol_factor = max(1.0 - cos_a**2 - cos_b**2 - cos_g**2 + 2 * cos_a * cos_b * cos_g, 0.0)
        lattice[2] = [
            c_ * cos_b,
            c_ * (cos_a - cos_b * cos_g) / max(sin_g, 1e-10),
            c_ * np.sqrt(vol_factor) / max(sin_g, 1e-10),
        ]
        pbc = [True, True, True]

    # ── Atom sites ─────────────────────────────────────────────────
    # Collect all atom-site entries.  CIF can have them in any order;
    # we need to pair _atom_site_type_symbol with _atom_site_fract_x/y/z.
    symbols: list[str] = []
    frac_x: list[float] = []
    frac_y: list[float] = []
    frac_z: list[float] = []
    cartesian_sites = False
    idx = 0
    while True:
        sym = kv.get(f"_atom_site_type_symbol_{idx}")
        # Fall back to _atom_site_label if type_symbol is missing.
        if sym is None:
            sym = kv.get(f"_atom_site_label_{idx}")
            if sym is not None:
                # Labels often look like "Si1", "O2" — strip digits.
                sym = "".join(c for c in sym if not c.isdigit()).strip()
        fx = kv.get(f"_atom_site_fract_x_{idx}")
        fy = kv.get(f"_atom_site_fract_y_{idx}")
        fz = kv.get(f"_atom_site_fract_z_{idx}")
        # A CIF may give sites in Cartesian angstroms instead. That is what a
        # molecule with no cell must use — labelling Cartesian coordinates
        # fract_* would make every consumer misread the file — and it is what
        # this package's own CIF export writes for a non-periodic structure.
        # Reading only fract_* meant vibe-view rejected its own export with
        # "no atom sites found".
        if fx is None:
            fx = kv.get(f"_atom_site_Cartn_x_{idx}")
            fy = kv.get(f"_atom_site_Cartn_y_{idx}")
            fz = kv.get(f"_atom_site_Cartn_z_{idx}")
            site_is_cartesian = True
        else:
            site_is_cartesian = False
        if sym is None or fx is None:
            break
        try:
            symbols.append(sym.strip().capitalize())
            frac_x.append(float(fx.split("(")[0]))
            frac_y.append(float(fy.split("(")[0]) if fy else 0.0)
            frac_z.append(float(fz.split("(")[0]) if fz else 0.0)
        except (ValueError, IndexError):
            break
        cartesian_sites = site_is_cartesian
        idx += 1

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
    if isinstance(source, (str, Path)):
        content = Path(source).read_bytes()
    elif isinstance(source, bytes):
        content = source
    else:
        content = source.read()
    text = content.decode("utf-8", errors="replace")
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


def pdb_to_qvf(source: str | Path | IO[bytes] | bytes) -> io.BytesIO:
    """Convert a PDB file to an in-memory .qvf archive.

    Reads ATOM/HETATM records for atomic positions and optional CRYST1
    record for unit cell parameters.  PDB coordinates are in angstroms.
    """
    if isinstance(source, (str, Path)):
        content = Path(source).read_bytes()
    elif isinstance(source, bytes):
        content = source
    else:
        content = source.read()
    text = content.decode("utf-8", errors="replace")

    atoms: list[dict[str, Any]] = []
    a_val = b_val = c_val = None
    alpha = beta = gamma = None

    secondary_structure = []
    for line in text.splitlines():
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
            except (ValueError, IndexError):
                pass
            continue

        if line.startswith(("ATOM", "HETATM")):
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
        al = np.radians(alpha)
        be = np.radians(beta)
        ga = np.radians(gamma)
        cos_a, cos_b, cos_g = np.cos(al), np.cos(be), np.cos(ga)
        sin_g = np.sin(ga)
        lat = np.zeros((3, 3))
        lat[0] = [a_val, 0.0, 0.0]
        lat[1] = [b_val * cos_g, b_val * sin_g, 0.0]
        vol_factor = max(1.0 - cos_a**2 - cos_b**2 - cos_g**2 + 2 * cos_a * cos_b * cos_g, 0.0)
        lat[2] = [
            c_val * cos_b,
            c_val * (cos_a - cos_b * cos_g) / max(sin_g, 1e-10),
            c_val * np.sqrt(vol_factor) / max(sin_g, 1e-10),
        ]
        lattice = lat.tolist()
        pbc = [True, True, True]

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
    if isinstance(source, (str, Path)):
        content = Path(source).read_bytes()
    elif isinstance(source, bytes):
        content = source
    else:
        content = source.read()
    text = content.decode("utf-8", errors="replace")

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


def gjf_to_qvf(source: str | Path | IO[bytes] | bytes) -> io.BytesIO:
    """Convert a Gaussian input file (.gjf/.com) to a QVF.

    Extracts atom coordinates from the molecule specification section.
    Link0 commands (%mem, %chk, etc.) and route lines (#p ...) are
    skipped.  The charge/multiplicity line is skipped.  Coordinates
    are in angstroms.
    """
    if isinstance(source, (str, Path)):
        content = Path(source).read_bytes()
    elif isinstance(source, bytes):
        content = source
    else:
        content = source.read()
    text = content.decode("utf-8", errors="replace")

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
        # Atom line: element x y z
        parts = stripped.split()
        if len(parts) < 4:
            continue
        symbol = parts[0].capitalize()
        if symbol not in _SYMBOL_TO_Z:
            continue
        try:
            x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
        except ValueError:
            continue
        z_num = _SYMBOL_TO_Z.get(symbol)
        atom: dict[str, Any] = {"symbol": symbol, "position": [x, y, z]}
        if z_num is not None:
            atom["atomic_number"] = z_num
        atoms.append(atom)

    if not atoms:
        raise ValueError("no atoms found in Gaussian input file")

    name = Path(source).stem if isinstance(source, (str, Path)) else "gjf"
    return structure_archive(atoms, MOLECULE, calculation=f"gjf:{name}").to_bytesio()


def gro_to_qvf(source: str | Path | IO[bytes] | bytes) -> io.BytesIO:
    """Convert a GROMACS .gro file to a QVF.

    Format: title line, n_atoms, then per-atom lines:
    residue_number residue_name atom_name atom_number x y z [vx vy vz]
    Positions are in nm; converted to Å.  Unit cell vector on final line.
    """
    if isinstance(source, (str, Path)):
        content = Path(source).read_bytes()
    elif isinstance(source, bytes):
        content = source
    else:
        content = source.read()
    text = content.decode("utf-8", errors="replace")
    lines = text.strip().splitlines()
    if len(lines) < 3:
        raise ValueError("gro file too short")

    # Lines: title, n_atoms, atoms..., box vector
    n_atoms = int(lines[1].strip())
    atoms: list[dict[str, Any]] = []
    for i in range(2, min(2 + n_atoms, len(lines) - 1)):
        line = lines[i]
        if len(line) < 36:  # minimum for x/y/z columns
            continue
        # GROMACS .gro columns (fixed-width):
        # 1-5   residue number
        # 6-10  residue name
        # 11-15 atom name
        # 16-20 atom number
        # 21-28 x (nm)
        # 29-36 y (nm)
        # 37-44 z (nm)
        try:
            x = float(line[20:28]) * 10.0  # nm → Å
            y = float(line[28:36]) * 10.0
            z = float(line[36:44]) * 10.0
        except (ValueError, IndexError):
            continue
        atom_name = line[10:15].strip()
        # Extract element from atom name (e.g. "OW" → "O", "HW1" → "H", "NA" → "Na").
        # Try the full name first, then first letter.
        name_clean = "".join(c for c in atom_name if c.isalpha())
        elem = name_clean.capitalize()
        if elem not in _SYMBOL_TO_Z:
            # Try just the first letter.
            first = name_clean[0].upper() if name_clean else "X"
            elem = first if first in _SYMBOL_TO_Z else "X"
        z_num = _SYMBOL_TO_Z.get(elem)
        atom: dict[str, Any] = {"symbol": elem, "position": [x, y, z]}
        if z_num is not None:
            atom["atomic_number"] = z_num
        else:
            atom["atomic_number"] = 0  # unknown element
        atoms.append(atom)

    if not atoms:
        raise ValueError("no atoms found in gro file")

    # Check for unit cell on the last line.
    pbc: list[bool] = [False, False, False]
    lattice = None
    box_line = lines[-1].strip()
    box_parts = box_line.split()
    if len(box_parts) >= 3:
        try:
            vx = float(box_parts[0]) * 10.0
            vy = float(box_parts[1]) * 10.0
            vz = float(box_parts[2]) * 10.0
            lattice = [[vx, 0.0, 0.0], [0.0, vy, 0.0], [0.0, 0.0, vz]]
            pbc = [True, True, True]
        except (ValueError, IndexError):
            pass

    name = Path(source).stem if isinstance(source, (str, Path)) else "gro"
    return structure_archive(atoms, pbc, lattice, calculation=f"gro:{name}").to_bytesio()


def sdf_to_qvf(source: str | Path | IO[bytes] | bytes) -> io.BytesIO:
    """Convert an MDL SDFile to an in-memory .qvf archive.

    Reads the first molecule only (multi-molecule SDFs take the first).
    Atom block columns: x y z symbol (Å).  Bond block columns:
    i j type (1=single, 2=double, 3=triple, 4=aromatic).
    """
    if isinstance(source, (str, Path)):
        content = Path(source).read_bytes()
    elif isinstance(source, bytes):
        content = source
    else:
        content = source.read()
    text = content.decode("utf-8", errors="replace")

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
_NATIVE_EXTENSIONS = (
    ".qvf",
    ".xyz",
    ".cif",
    ".cube",
    ".pdb",
    ".mol2",
    ".gjf",
    ".com",
    ".gro",
    ".sdf",
    ".mol",
    ".py",
)

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

# What the browse page globs for. It previously listed only the native set,
# so files `detect_format` could happily open were never offered — the
# inconsistency the 2026-07-02 audit flagged (F2).
_SUPPORTED_EXTENSIONS = _NATIVE_EXTENSIONS + _ASE_EXTENSIONS + TREXIO_EXTENSIONS


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

    Detects by extension:
    - ``.qvf`` → ``"qvf"``
    - ``.xyz`` → ``"xyz"``
    - ``.cif`` → ``"cif"``
    - ``.cube`` → ``"cube"``
    - ``.pdb`` → ``"pdb"``
    - ``.mol2`` → ``"mol2"``
    - ``.gjf`` / ``.com`` → ``"gjf"``
    - ``.gro`` → ``"gro"``
    - ``.sdf`` / ``.mol`` → ``"sdf"``
    - ``.trexio`` / ``.h5`` / ``.hdf5`` or a TREXIO text directory → ``"trexio"``
    """
    if is_trexio_directory(path):
        return "trexio"
    ext = Path(path).suffix.lower()
    if ext in TREXIO_EXTENSIONS:
        return "trexio"
    known = {
        ".qvf": "qvf",
        ".xyz": "xyz",
        ".cif": "cif",
        ".cube": "cube",
        ".pdb": "pdb",
        ".mol2": "mol2",
        ".gjf": "gjf",
        ".com": "gjf",
        ".gro": "gro",
        ".sdf": "sdf",
        ".mol": "sdf",
        ".py": "py",
    }
    result = known.get(ext)
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


def convert_to_qvf(path: str | Path, *, format_name: str | None = None) -> io.BytesIO:
    """Auto-detect format and convert to an in-memory QVF.

    ``format_name`` overrides suffix detection and accepts a built-in format
    name or the name of an installed ``vibeview.importers`` plugin. Raises
    ``ValueError`` for unsupported formats.
    """
    fmt = format_name or detect_format(path)
    if fmt is None:
        raise ValueError(f"unsupported file format: {Path(path).suffix}")
    _CONVERTERS = {
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
    converter = _CONVERTERS.get(fmt)
    if converter is not None:
        return converter(path)

    from vibeview.importers import convert_with_importer, get_importer

    importer = get_importer(fmt)
    if importer is None:
        raise ValueError(
            f"unknown importer {fmt!r}; run 'vibe-view formats' to list available formats"
        )
    return convert_with_importer(importer, path)


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
