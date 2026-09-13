# Input formats and interoperability

QVF is vibe-view's richest input, but the producer does not have to be
vibe-qc, and the file does not have to be a QVF. Common structure and volume
files open directly, and any of them can be turned into a persistent,
checksummed archive.

## Open now, or import once

Use `open` when you only want to look:

```sh
vibe-view open molecule.xyz
vibe-view open density.cube
vibe-view open POSCAR
vibe-view open calculation.hdf5                 # needs the [trexio] extra
```

The conversion happens in memory for that session. The source file is not
changed and no archive is written.

Use `import` when you want a reusable QVF:

```sh
vibe-view import molecule.xyz                    # writes molecule.qvf beside it
vibe-view import density.cube -o density.qvf
vibe-view import ./results/*.xyz -o ./qvf-results/
vibe-view import ./results -o ./qvf-results/      # a directory, searched recursively
vibe-view import calculation.data --from xyz      # force an importer when the extension is ambiguous
```

One input defaults to a sibling `.qvf`; several inputs, or a directory,
default to one archive per source under `./vibe-view-imports`. Existing
outputs are never replaced unless you pass `--force`.

Each source is converted independently. `import` does not merge an XYZ, a
cube, a log and a wavefunction sidecar into one calculation archive; a
producer that wants the full viewer surface writes QVF directly (see
[The QVF format](qvf.md)).

Commands other than `open` and `import` read archives only. Import first
when the source is a loose file:

```sh
vibe-view import calculation.cube -o calculation.qvf
vibe-view show calculation.qvf
vibe-view capture calculation.qvf -o calculation.png
vibe-view validate calculation.qvf
```

## What this installation reads

```sh
vibe-view formats
vibe-view formats --json
```

lists every importer: the built-ins, the optional ones whose dependency is
missing, and third-party plugins, each with its extensions, the data it
preserves and its availability. That command, not this page, is the source
of truth for an installed version.

| Input | Extra | Kept | Limits |
|---|---|---|---|
| QVF (`.qvf`) | none | Every valid section, its metadata and checksums | The only built-in path for a complete calculation record. |
| XYZ (`.xyz`) | none | One molecular geometry, coordinates in Å | No cell, bonds, properties, or reliable multi-frame import. |
| CIF (`.cif`) | none | Explicit atom sites and cell parameters | Symmetry operations are not expanded. Supply an expanded cell when the file stores only the asymmetric unit. |
| Gaussian cube (`.cube`) | none | Structure and one scalar volume | The volume kind is inferred from the comment lines and defaults to density. No wavefunction, spectra or provenance. `.cub` is not recognized. |
| TREXIO (`.h5`, `.hdf5`, `.trexio`, or a text-backend directory) | `[trexio]` | Geometry, cell, electron count, real molecular Gaussian basis and MOs, available energies, occupations and spin/symmetry labels | Orbital evaluation covers spherical or Cartesian s/p/d/f shells. Unsupported or incomplete wavefunctions raise an error; see below. |
| PDB (`.pdb`) | none | Coordinates, atom, residue and chain names, B factors, the `CRYST1` cell | Explicit connectivity is not imported. Use a cleaned single model; alternate locations and multiple models are not resolved. |
| Tripos Mol2 (`.mol2`) | none | Geometry and explicit bond orders | Charges, force-field atom types beyond the element, and substructure metadata are dropped. |
| Gaussian input (`.gjf`, `.com`) | none | The Cartesian geometry | Route, basis, constraints and Z-matrix input are not interpreted. Gaussian output, `.chk` and `.fchk` are not supported. |
| GROMACS (`.gro`) | none | Coordinates and a three-length orthorhombic box | Velocities and residue metadata are dropped; triclinic box terms are not imported. |
| MDL Mol / SDF (`.mol`, `.sdf`) | none | The first V2000 molecule and its bond orders | Later SDF records and data fields are ignored; V3000 is not supported. |
| vibe-qc Python input (`.py`) | none | The structure and selected run parameters, parsed statically | The script is not executed. This is a parser for vibe-qc input syntax, not for arbitrary Python. |
| ASE trajectory (`.traj`), extended XYZ (`.extxyz`), VASP (`.vasp`, `.poscar`, `POSCAR`, `CONTCAR`) | `[ase]` | One ASE-readable geometry, cell and periodic flags | Only these names are routed to ASE. Per-atom arrays, trajectories, calculator results and other ASE formats are not retained. |

"Kept" means the information reaches the archive. It does not mean every
record in the source format is interpreted. The viewer can infer display
bonds for a format that carries no connectivity; those bonds are a visual
aid, not evidence that the source contained bond orders.

`[ase]` is optional because ASE is a large dependency that most people
opening a common format never need. When you open a file that needs it,
vibe-view names the extra and the exact install command.

## TREXIO wavefunctions

Install the official TREXIO Python library through the optional extra (also
included in `[all]`):

```sh
python -m pip install -e '.[trexio]'             # from a source checkout
vibe-view formats                              # reports readiness and an install hint
vibe-view open calculation.hdf5
vibe-view import calculation.hdf5 -o calculation.qvf
vibe-view import ./calculation.trexio            # HDF5 file or TREXIO text directory
vibe-view import calculation.data --from trexio  # HDF5 with another extension
```

The HDF5 and text backends use the same importer. Text datasets are directories
containing TREXIO group files such as `metadata.txt` and `nucleus.txt`; pass the
whole directory. A dataset counts as one input, including when a batch import
or the viewer's file dialog discovers it inside a results directory. HDF5 files
also work through browser uploads. For text datasets, use the server-side path
dialog or import them to QVF first. The desktop app also accepts TREXIO files
and opens a text dataset selected through Open Folder or drag and drop.

Nuclear coordinates and cell vectors are converted from bohr to Å. Gaussian
exponents and MO energies remain in atomic units. The importer translates the
TREXIO spherical AO order and all three normalization factors (primitive, shell
and AO) into QVF conventions, preserving orbital values. Restricted and
unrestricted sets retain their available energies, occupations and symmetry
labels. Natural occupations are identified as electron occupations. Missing
energies and occupations are left absent; the importer does not infer an SCF
occupation pattern from the electron count.

Files without MO coefficients open as structures. When MO coefficients are
present, they require a complete real, nonperiodic Gaussian basis, including
the explicit normalization factors. Slater, numerical and plane-wave bases,
complex or k-point orbitals, nonzero radial powers and shells above f are not
supported for orbital import. These cases raise an error instead of displaying
an altered wavefunction; export a cube volume from the producer to view a
sampled field. Periodic geometry without orbitals retains the cell and periodic
flags. Integrals, determinants, CI coefficients, ECP operators and linked state
files are not imported; computed density uses the stored MO occupations.

## Routes from other programs

| Producer | Best route today | Not imported directly |
|---|---|---|
| Gaussian | Open or import the `.gjf` / `.com` for the geometry; a `.cube` for one orbital, density or potential | `.log`, `.out`, `.chk`, `.fchk` |
| ORCA | Export `.xyz` for the geometry, `.cube` for one scalar field | `.out`, `.gbw`, `.hess`, `.molden.input` |
| VASP | Install `[ase]`, then open or import `POSCAR` / `CONTCAR` | `CHGCAR`, `LOCPOT`, `WAVECAR`, `DOSCAR`, `PROCAR` |
| CP2K, Quantum ESPRESSO, Psi4, PySCF and similar | Export XYZ or CIF for the structure, cube for one volume; or add a QVF writer | Native logs, restart files, code-specific wavefunction files |
| TREXIO-producing programs | Open a TREXIO dataset with `[trexio]` for geometry and supported Gaussian orbitals | Correlated many-body data, unsupported orbital bases and periodic wavefunctions |
| Molecular-dynamics tools | PDB, GRO, Mol2, SDF, XYZ, or one of the listed ASE routes | Multi-frame trajectories are not yet a general import path |

`.molden` is not among the built-in formats, so orbitals from a code that
writes Molden files do not open directly. A converted file only carries what
the file carries: a cube has no wavefunction, no spectra and no provenance,
so most of the viewer's panels stay empty for it. That gap is the point of
the format rather than a missing importer. If you maintain a code and want
its results to open with the full surface, the writer side is deliberately
small: the [qvf repository](https://github.com/vibe-qc/qvf) carries
the normative specification, Apache-2.0 reference writers in Python and
dependency-free C++17, a validator and the conformance corpus, so it can be
vendored into any code. A conforming producer is a few dozen lines on top of
the reference library, and the archives it writes open here with no
viewer-side change.

## Writing an importer plugin

The importer contract is the one extension point vibe-view exposes today. A
plugin declares one entry point in the `vibeview.importers` group; the
entry-point name is the stable format name `vibe-view import --from` uses.

```toml
[project.entry-points."vibeview.importers"]
orca-output = "my_orca_importer:importer"
```

The entry point resolves to an `ImporterSpec`, or to a zero-argument factory
that returns one:

```python
from pathlib import Path

from vibeview.importers import IMPORTER_API_VERSION, ImporterSpec


def convert_orca_output(path: Path) -> bytes:
    # Parse the file and return one complete QVF archive as bytes.
    return build_qvf_bytes(path)


def importer() -> ImporterSpec:
    return ImporterSpec(
        format_name="orca-output",
        description="ORCA text output",
        extensions=(".out",),
        convert=convert_orca_output,
        data_kinds=("structure", "properties"),
        api_version=IMPORTER_API_VERSION,
    )
```

* `extensions` include the leading dot and match case-insensitively. Use
  `stems=("POSCAR",)` for exact extensionless names, or a
  `probe(path) -> bool` callback when the name is not enough.
* `convert` receives a `pathlib.Path` and returns a complete QVF as bytes or
  as a readable binary file object; vibe-view rewinds, copies and closes a
  file object it is given.
* `api_version` is 1, the current contract.

vibe-view validates every member and checksum of the returned archive before
it writes or opens it. Format names used by the built-ins are reserved, and
a duplicate plugin name or a duplicate extension or stem claim fails closed
rather than picking one package silently. A plugin that fails to load is
isolated from the others and reported, with its error, by
`vibe-view formats`.

## Before blaming the file

```sh
vibe-view doctor              # the environment, the extras, the packaged resources
vibe-view formats             # what this environment can read
vibe-view capture-selftest    # whether the offscreen renderer works at all
```

Server logs go to `~/.cache/vibe-view/vibe-view.log` unless `XDG_CACHE_HOME`
is set.
