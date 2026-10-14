# Input formats and interoperability

QVF is vibe-view's richest input, but the producer does not have to be
vibe-qc, and the file does not have to be a QVF. Common structure and volume
files open directly, and any of them can be turned into a persistent,
checksummed archive.

For a complete import with a bundled XYZ input, expected section inventory
and exports, follow [the worked workflow](workflows.md).

## Open now, or import once

Use `open` when you only want to look:

```sh
vibe-view open molecule.xyz
vibe-view open density.cube
vibe-view open POSCAR
vibe-view open calculation.hdf5                 # needs the [trexio] extra
vibe-view open calculation.property.json
vibe-view open calculation.gbw                  # needs the matching orca_2json utility
```

The conversion happens in memory for that session. The source file is not
changed and no archive is written. A file uploaded through the browser
viewer's **Load file** dialog is converted the same way, by the same
importers, so any single-file format `open` reads also opens as an upload.

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
| XYZ (`.xyz`) | none | The first complete frame, coordinates in Å | Later frames, extra per-atom columns and comment metadata are ignored. No cell or explicit bonds are imported. |
| CIF (`.cif`) | none | Explicit atom sites and cell parameters, read with CIF 1.1 tokenization (quoted values, loop rows spanning lines, `;` text fields, comments) | Symmetry operations are not expanded. Supply an expanded cell when the file stores only the asymmetric unit. A non-numeric site coordinate or an incomplete loop is an error. |
| Gaussian cube (`.cube`) | none | Structure and one scalar volume | The volume kind is inferred from the comment lines and defaults to density. No wavefunction, spectra or provenance. `.cub` is not recognized. |
| ORCA property text/JSON (`.property.txt`, `.property.json`) | none | Structures, absorption and ECD spectra, Mulliken/Löwdin charges, Mayer bond orders, available SCF energy and dipole | Each method, gauge and state variant remains separate. Other property data is retained as unrendered metadata; see below. |
| ORCA exported JSON (`.json`) and GBW (`.gbw`) | none for JSON; matching `orca_2json` for GBW | Structure and supported molecular Gaussian orbitals | Real spherical s–f basis functions; unsupported wavefunctions raise an error. JSON must contain an ORCA `Molecule` or property object. |
| ORCA MCD (`.mcd`) | none | Raw wavenumber, L−R and L+R strengths, signs and repeated energies | Reads three-column stick data; does not reproduce thermally averaged `orca_mapspc` spectra. |
| TREXIO (`.h5`, `.hdf5`, `.trexio`, or a text-backend directory) | `[trexio]` | Geometry, cell, electron count, real molecular Gaussian basis and MOs, available energies, occupations and spin/symmetry labels | Orbital evaluation covers spherical or Cartesian s/p/d/f shells. Unsupported or incomplete wavefunctions raise an error; see below. |
| PDB (`.pdb`) | none | Coordinates, atom, residue and chain names, B factors, the `CRYST1` cell | Explicit connectivity is not imported. Only the first `MODEL` is read, and only the first alternate location (altLoc) of each atom. The wwPDB placeholder `CRYST1` of a non-crystallographic entry (a = b = c = 1 Å, 90° angles, space group P 1, Z = 1) is not a cell. |
| Tripos Mol2 (`.mol2`) | none | The first molecule and its explicit bond orders | Later molecules, charges, force-field atom types beyond the element, and substructure metadata are dropped. |
| Gaussian input (`.gjf`, `.com`) | none | The Cartesian geometry, with element labels given as a symbol, an atomic number, a numbered label (`C1`), parameters (`C(Fragment=1)`) or an MM type (`C-CA-0.25`), and an optional freeze code | Route, basis and constraints are not interpreted. Z-matrix input is refused with an error. Gaussian output, `.chk` and `.fchk` are not supported. |
| GROMACS (`.gro`) | none | Coordinates and the box of the first complete frame, orthorhombic or triclinic (all nine box values); an exactly zero box is not periodic | Later frames, velocities and residue metadata are dropped. Variable coordinate precision is supported. Malformed first-frame records and nonzero singular boxes are rejected. A `.gro` has no element column, so elements come from atom names: the first letter (`CA` is a C-alpha, `HE1` a hydrogen), a two-letter element only for a one-atom residue named after it (the ions `NA`, `CL`, `CA`) or a name that cannot start with an organic element (`FE`, `ZN`). |
| MDL Mol / SDF (`.mol`, `.sdf`) | none | The first V2000 molecule and its bond orders | Later SDF records and data fields are ignored; V3000 is not supported. |
| vibe-qc Python input (`.py`) | none | The structure and selected run parameters, parsed statically | The script is not executed. This is a parser for vibe-qc input syntax, not for arbitrary Python. |
| ASE trajectory (`.traj`), extended XYZ (`.extxyz`), VASP (`.vasp`, `.poscar`, `POSCAR`, `CONTCAR`) | `[ase]` | One ASE-readable geometry, cell and periodic flags | Only these names are routed to ASE. Per-atom arrays, trajectories, calculator results and other ASE formats are not retained. |

XYZ imports require a positive integer atom count, a comment line (which may
be empty), and that many valid atom rows with finite coordinates. A malformed
or truncated first frame raises an error before writing, preserving an existing
destination even with `--force`. Later frames are ignored rather than combined
into the first geometry. Extra per-atom columns remain accepted and ignored;
unknown nonempty element labels retain atomic number zero. The native `.xyz`
importer does not interpret extended-XYZ cell or property metadata; `.extxyz`
continues to use the optional ASE route.

Mol2 and V2000 Mol/SDF imports validate the first molecule’s declared atom
records and bond references before writing an archive. Malformed or truncated
records raise an import error; even `--force` leaves an existing destination
untouched on these errors. Mol2 bonds use the source atom IDs, which may be
nonsequential or zero; duplicate atom IDs are rejected as ambiguous. Blank
lines and comments do not end a Mol2 section, and its bond count may be omitted.
Both formats import only the first molecule, so later molecules have no effect
on its atom counts or connectivity. Unknown element labels retain atomic
number zero, and the existing bond-order mappings are preserved.

"Kept" means the information reaches the archive. It does not mean every
record in the source format is interpreted. The viewer can infer display
bonds for a format that carries no connectivity; those bonds are a visual
aid, not evidence that the source contained bond orders.

`[ase]` is optional because ASE is a large dependency that most people
opening a common format never need. When you open a file that needs it,
vibe-view names the extra and the exact install command.

Cube grids with positive voxel counts use bohr for the origin, voxel vectors
and atom positions. The importer also accepts the negative-count ångström
extension described in [Bourke's cube format notes](https://paulbourke.net/dataformats/cube/):
all three counts must be negative. Zero counts and mixed signs raise a clear
error. A negative vector component reverses an axis without changing the unit.
Skewed grids retain their vectors, and samples keep cube's x-outermost,
z-innermost order. Only length coordinates are converted; scalar field values
are preserved.

For cubes with multiple datasets, the importer keeps the first dataset in
file order as its single scalar volume. It reads the per-voxel field count
from `NVAL`, or from the orbital ID record when the atom count is negative,
and separates the interleaved samples before choosing that field. Wrapped
orbital ID records are supported. Invalid counts, incomplete ID records and
truncated declared datasets raise an error; later datasets are not exported.

Python input is parsed without executing its code. A periodic import needs a
lattice that this static parser can resolve, such as a literal 3×3 matrix or
the supported array/transpose expressions. An unresolved lattice raises an
error instead of substituting a unit cube. Supply explicit lattice values or
import a structure file that carries the cell.

## Element identities

The built-in element table covers hydrogen through curium (atomic numbers
1–96). Python inputs retain the correct symbols for heavier elements such as
Pt and U, whether an `Atom` uses its atomic number or a symbol. Previously
this parser's table stopped at barium, so `Atom(78, ...)` was labeled `Z78`.
For an atomic number outside the supported table, Python input preserves its
numeric value and the `Z<number>` label. Other import formats retain their
existing unknown-element handling.

The structure viewer uses the same symbol coverage when an atom has a symbol
but no atomic number. Its stock Jmol colors and radius values are unchanged;
per-element color overrides still apply. Covalent radii use the selected
[Cordero et al. (2008) values](https://doi.org/10.1039/B801115J). The small
van der Waals display table and its style-dependent estimates are visual
approximations, not an inference of oxidation state, spin or bond order.

## ORCA results

For a guided walkthrough, see [Using vibe-view with ORCA](orca.md): install,
open a calculation, inspect each kind of result and export a reusable archive.
This section is the file-format reference.

Open a calculation through any of its result files. Files with the exact same
calculation basename in the same directory open together as one dataset,
containing the structure, available orbitals, spectra and properties:

```sh
vibe-view open calculation.property.txt
vibe-view open calculation.property.json
vibe-view import calculation.property.json -o calculation.qvf
vibe-view open calculation.mcd
vibe-view open calculation.gbw
vibe-view open calculation.out                  # locates same-basename results
vibe-view import results/ -o converted/          # one QVF per calculation
```

For example, `calculation.property.json`, `calculation.gbw` (or its exported
`calculation.json`) and `calculation.cis-el.dipole-length.1.mcd` share one
dataset. Opening the matching `.out` or `.inp` also locates these companions;
their contents are not a substitute for result files. If `%base` changes the
result basename, open a result file directly. Compound steps remain separate
calculations even when their structures are identical. A wrapper output with
several steps reports the available calculation names.

Selecting property text or JSON explicitly uses that representation;
otherwise the newest file is used, with JSON preferred when timestamps tie.
The same rule selects exported orbital JSON versus GBW. A newer GBW requires
the matching `orca_2json`; an older JSON export is not silently substituted.
Orbitals must match the final property geometry in atom order, elements and
coordinates (absolute tolerance 0.00001 Å), and charge/multiplicity must agree
when present in both files. A conflicting or unreadable selected companion
produces an error instead of a partial dataset.

GUI folder/glob opening, CLI import directories and multiple CLI file arguments
deduplicate companions. Folder discovery chooses fresh representations and
skips auxiliary `TDDFTGuess` wavefunctions when primary results exist; guesses
can still be opened explicitly. Default persistent output is `calculation.qvf`.
The dataset records selected source filenames and omitted representations in
its provenance. Browser uploads contain only the uploaded file; import the
calculation locally and upload its QVF to transfer the combined dataset.
Converted source files are not watched for automatic reload.

The text and JSON routes read the same ORCA property vocabulary; text import
does not require an ORCA installation. Absorption and ECD tables preserve
every method and length/velocity gauge variant, including repeated tables,
transition state/irrep labels and multiplicities. Energy coordinates are eV.
Absorption sticks are oscillator strengths; ECD sticks are signed rotatory
strengths in 10⁻⁴⁰ cgs. The displayed broadening is a plotting convenience,
not a conversion of rotatory strength to molar circular dichroism.

The last geometry is the default structure. Earlier geometries form a
trajectory when their atom identities agree, or remain separate structures
when they differ. Mulliken/Löwdin populations and Mayer bond orders for the
last geometry retain separate method and state records. Available ground-state
SCF energy and dipole are shown in the calculation metadata. Other energies,
state-specific dipoles and property blocks remain in `x_orca.properties`
sections, reported as unrendered vendor metadata. The importer does not
reinterpret correlated or excited-state energies as SCF results.

A standalone `.mcd` has three columns: wavenumber in cm⁻¹, L−R oscillator
strength, and L+R oscillator strength. These become separate circular-dichroism
and absorption plots. Signs and duplicate energies are retained, including
very small nonzero strengths; the file can open without a structure.
These sticks are not the thermally averaged molar spectrum
produced by ORCA's `orca_mapspc` utility.
When companions include both aggregate and per-transition MCD files, the
aggregate replaces its transition files. Method, operator and field variants
remain separate labelled plots. Explicitly opening a per-transition file
includes that plot as well as the aggregate, without summing the two.

For orbitals, open an exported `orca_2json` JSON file containing `Molecule`,
or open `.gbw` with a matching ORCA utility installed. vibe-view finds
`orca_2json` on `PATH`, or uses the executable selected by `ORCA_2JSON`.
It copies the GBW into a temporary directory and requests
`{"MOCoefficients": true, "Basisset": true}` there. It does not request
integrals or density matrices, change the source calculation or retain the
temporary export. Use a utility matching the GBW's ORCA version. The
[official orca_2json manual](https://www.faccts.de/docs/orca/6.1/manual/contents/utilitiesvisualization/orca_2json.html)
describes the export settings and manual invocation.

Orbital import supports real molecular spherical Gaussian s, p, d and f
shells. Unsupported basis types or incomplete orbital data raise an error;
a cube export remains the route for a sampled orbital or density field.
RHF, ROHF, UHF and CASSCF orbital sets retain their occupations. Individual
CASSCF orbitals can be viewed, but derived density, ELF and NCI calculations
are unavailable: the JSON export does not establish whether the orbitals
diagonalize the correlated density matrix. Use an ORCA density cube instead.
The importer does not extract scientific data from ORCA `.out` or `.inp`
files, `.hess`, Molden, binary `.densities` files or `.cis` amplitudes.
The desktop shell registers GBW and MCD as viewer file types.

Property parsing has been checked against supplied ORCA 6.1.1 text/JSON
pairs; repository regression fixtures are synthetic. The
[ORCA property-file manual](https://www.faccts.de/docs/orca/6.1/manual/contents/utilitiesvisualization/property_file.html)
describes the source format.

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
| ORCA | Open a property file, `.mcd`, exported `orca_2json` JSON or `.gbw` to combine same-basename results; `.out`/`.inp` locate companions; `.cube` for one scalar field | Scientific data parsed directly from `.out`/`.inp`, `.hess`, `.molden.input`, binary densities and CIS amplitudes |
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
