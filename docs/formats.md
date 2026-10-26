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

For archive-oriented commands such as `info`, `show`, `table`, `capture`
and `validate`, import a loose file first. The desktop launcher also accepts
loose inputs directly. Strict validation needs the optional
[reference validator](qvf.md#strict-validation):

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
| XYZ (`.xyz`, `.extxyz`) | none | Every complete frame, coordinates in Å, extended-XYZ cell and property layout, declared charge/multiplicity and energy, and xtb's `energy: … gnorm: …` frame comments as hartree energies | Matching identities form a trajectory; xtb's XYZ-frame logs (`xtbopt.log`, `xtbscan.log`) open as XYZ despite their `.log` name, without the `[results]` extra; variable cells remain available in individual structures. Unused per-atom columns are ignored. |
| CIF (`.cif`) | none | Explicit atom sites and cell parameters, read with CIF 1.1 tokenization (quoted values, loop rows spanning lines, `;` text fields, comments) | Symmetry operations are not expanded. Supply an expanded cell when the file stores only the asymmetric unit. A non-numeric site coordinate or an incomplete loop is an error. |
| Gaussian cube (`.cube`) | none | Structure and one scalar volume | The volume kind is inferred from the comment lines and defaults to density. No wavefunction, spectra or provenance. `.cub` is not recognized. |
| ORCA property text/JSON (`.property.txt`, `.property.json`) | none | Structures, absorption and ECD spectra, Mulliken/Löwdin charges, Mayer bond orders, available SCF energy and dipole | Each method, gauge and state variant remains separate. Other property data is retained as unrendered metadata; see below. |
| ORCA exported JSON (`.json`) and GBW (`.gbw`) | none for JSON; matching `orca_2json` for GBW | Structure and supported molecular Gaussian orbitals | Real spherical s–f basis functions; unsupported wavefunctions raise an error. JSON must contain an ORCA `Molecule` or property object. |
| ORCA MCD (`.mcd`) | none | Raw wavenumber, L−R and L+R strengths, signs and repeated energies | Reads three-column stick data; does not reproduce thermally averaged `orca_mapspc` spectra. |
| ORCA Hessian (`.hess`) | none | Geometry, stored harmonic frequencies and normal modes, optional IR and Raman sticks; matched output adds signed VCD | Includes zero/imaginary modes; Raman activities and VCD rotatory strengths retain native units; VCD requires a matching completed output log. No Hessian diagonalization or isotope recalculation. |
| TREXIO (`.h5`, `.hdf5`, `.trexio`, or a text-backend directory) | `[trexio]` | Geometry, cell, electron count, real molecular Gaussian basis and MOs, available energies, occupations and spin/symmetry labels | Orbital evaluation covers spherical or Cartesian s/p/d/f shells. Unsupported or incomplete wavefunctions raise an error; see below. |
| PDB (`.pdb`) | none | Coordinates, atom, residue and chain names, B factors, the `CRYST1` cell | Explicit connectivity is not imported. Only the first `MODEL` is read, and only the first alternate location (altLoc) of each atom. The wwPDB placeholder `CRYST1` of a non-crystallographic entry (a = b = c = 1 Å, 90° angles, space group P 1, Z = 1) is not a cell. |
| PDBx/mmCIF (`.mmcif`, `.mcif`; `_atom_site` data in `.cif`) | `[biomolecule]` (Gemmi) | Every model as a selectable structure, alternate locations, chains, residues, deposited secondary structure and the cell | Small-molecule CIF stays on the built-in CIF parser. See [Biomolecules](biomolecules.md). |
| MRC2014/CCP4 map (`.mrc`, `.ccp4`, `.map`) | none | One real scalar map as a `volume.generic` snapshot of at most two million voxels, keeping its placement, axis order and header statistics | Stride-subsampled, not averaged; complex, packed and stacked maps are refused. See [Experimental maps](experimental_maps.md). |
| Z-matrix (`.zmat`, `.zmt`) | none | One molecule in Gaussian, ORCA or MOPAC internal-coordinate layout, converted to Cartesian | Expressions and mixed Cartesian/internal rows are refused. See [Internal coordinates](internal_coordinates.md). |
| Tripos Mol2 (`.mol2`) | none | The first molecule and its explicit bond orders | Later molecules, charges, force-field atom types beyond the element, and substructure metadata are dropped. |
| Gaussian input (`.gjf`, `.com`) | none | The charge and multiplicity, and the Cartesian geometry, with element labels given as a symbol, an atomic number, a numbered label (`C1`), parameters (`C(Fragment=1)`) or an MM type (`C-CA-0.25`), and an optional freeze code | Route, basis and constraints are not interpreted. Z-matrix geometry with numeric variables is supported; binary `.chk` requires external `formchk`. Native `.fchk` wavefunctions are described below. |
| GROMACS (`.gro`) | none | Coordinates and the box of the first complete frame, orthorhombic or triclinic (all nine box values); an exactly zero box is not periodic | Later frames, velocities and residue metadata are dropped. Variable coordinate precision is supported. Malformed first-frame records and nonzero singular boxes are rejected. A `.gro` has no element column, so elements come from atom names: the first letter (`CA` is a C-alpha, `HE1` a hydrogen), a two-letter element only for a one-atom residue named after it (the ions `NA`, `CL`, `CA`) or a name that cannot start with an organic element (`FE`, `ZN`). |
| Turbomole / xtb (`coord`, `.coord`, `.tmol`) | none | The `$coord` geometry in bohr, ångström (`$coord angs`) or fractional (`$coord frac`) coordinates; a 3D cell from `$periodic 3` with `$lattice` or `$cell`; charge and multiplicity from xtb's `$chrg` and `$spin` or Turbomole's `$eht charge= unpaired=` | Other data groups and fixed-atom flags are ignored. 1D and 2D periodicity (`$periodic 1` or `2`) is rejected. A control file whose `$coord file=…` points elsewhere is rejected with the name of the file to open. |
| MDL Mol / SDF (`.mol`, `.sdf`) | none | The first V2000 molecule, its bond orders, its net formal charge (`M  CHG`, or the atom-block charges) and a declared radical multiplicity (`M  RAD`) | Later SDF records and data fields are ignored; V3000 is not supported. |
| vibe-qc Python input (`.py`) | none | The structure and selected run parameters, parsed statically | The script is not executed. This is a parser for vibe-qc input syntax, not for arbitrary Python. |
| ASE trajectory (`.traj`), VASP (`.vasp`, `.poscar`, `POSCAR`, `CONTCAR`) | `[ase]` | One ASE-readable geometry, cell and periodic flags | Only these names are routed to ASE. Per-atom arrays, trajectories, calculator results and other ASE formats are not retained. |

XYZ imports require a positive integer atom count, a comment line (which may
be empty), and that many valid atom rows with finite coordinates. A malformed
or truncated first frame raises an error before writing, preserving an existing
destination even with `--force`. The same strict check applies to later frames.
Unknown nonempty element labels retain atomic number zero. The native reader
handles `.xyz` and `.extxyz`, including declared cell and property metadata
(see [XYZ trajectories](#xyz-trajectories-and-extended-xyz)). Declared charge
must be an integer and multiplicity a positive integer; malformed declarations
are refused. Missing declarations remain absent, while original comments and
per-frame state are retained in provenance.

Gaussian inputs use blank lines to separate the route, title and molecule
sections. Numeric and multi-line titles are kept separate from the actual
charge/multiplicity line. Wrapped routes and Windows line endings are supported;
only the first molecule specification is imported.

CIF atom sites need a complete, finite coordinate triple. Missing columns,
unknown (`?`) or inapplicable (`.`) coordinates, and non-finite values raise an
error naming the site before an output is written or replaced. The importer
uses a complete Cartesian triple when the site's fractional coordinates are
unavailable; it never fills a missing coordinate with zero or omits that site.
Explicit zero coordinates and values with standard uncertainties remain valid.

A complete numeric CIF or PDB cell must have finite positive lengths and
finite angles strictly between 0° and 180° that together form a nondegenerate
3D cell. Invalid cells raise an error before an output is written or replaced.

For a CIF containing several data blocks, the importer reads the first block
with an atom-site coordinate loop and takes cell parameters only from that
block. Leading blocks without atom-site coordinates are skipped. To import a
later structure, save its data block separately. Quoted values and text fields
that contain `data_` text remain part of their original block.

Wherever a source states a charge or multiplicity (an XYZ comment, a Gaussian
charge/multiplicity line, SDF charge and radical records, a SMILES string or
a vibe-qc input), the archive records it in `provenance`. A source that states
nothing gets no value, not a neutral singlet. That is what vibe-sketch reads
to draw an ion, and what the Calculation Parameters panel starts from.

Mol2 and V2000 Mol/SDF imports validate the first molecule’s declared atom
records and bond references before writing an archive. Malformed or truncated
records raise an import error; even `--force` leaves an existing destination
untouched on these errors. Mol2 bonds use the source atom IDs, which may be
nonsequential or zero; duplicate atom IDs are rejected as ambiguous. Blank
lines and comments do not end a Mol2 section, and its bond count may be omitted.
Both formats import only the first molecule, so later molecules have no effect
on its atom counts or connectivity. Unknown element labels retain atomic
number zero, and the existing bond-order mappings are preserved.
An explicitly declared bond count of zero remains an empty connectivity list:
the viewer and geometry exports do not add bonds based on atom distances.
Distance-based inference still applies when connectivity is unspecified,
including XYZ inputs and Mol2 inputs with neither a bond count nor bond records.

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
vibe-view open calculation.hess
vibe-view open calculation.out                  # printed results and matching companions
vibe-view import results/ -o converted/          # one QVF per calculation
```

For example, `calculation.property.json`, `calculation.gbw` (or its exported
`calculation.json`) and `calculation.cis-el.dipole-length.1.mcd` share one
dataset. Opening the matching `.out` or `.inp` also locates these companions;
an output alone also supplies its printed structure and spectroscopy (see
[ORCA output inspection](orca.md#open-an-output-without-companion-files)).
Input files remain locators. If `%base` changes the
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

Same-basename `.hess` files add harmonic `vibrations` and optional IR/Raman spectra.
A matching completed output adds signed VCD rotatory strengths after checking
geometry, mode indices, frequencies and units. vq's `stdout.log` is accepted
only when its input header identifies this calculation unambiguously.
They can also open alone or through a browser upload without ORCA. Coordinates
are converted from bohr; stored Cartesian mode columns become per-mode atomic
displacements. All modes, including zero and imaginary frequencies, retain
their order. ORCA's Hessian recentering is handled as a common translation
within 0.00001 Å; rotations, reordered atoms and geometry/multiplicity conflicts
are rejected. ORCA 6 IR intensities use km/mol; legacy five-column IR data use
explicitly labelled squared transition moments in atomic units. Frequency
comparisons allow the IR block's two-decimal rounding. The importer requires
stored frequencies and modes; it does not recompute them from `$hessian`.
See [vibrational spectra](orca.md#harmonic-vibrations-ir-raman-and-vcd) for controls and limits.

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
Native output parsing also reads printed geometries, normal modes and
IR/Raman/UV-Vis/CD spectra when present. VCD requires a completed table and
a final geometry matching the Hessian. Output-text energies and orbitals,
and `.inp` scientific results, are not inferred. Molden orbitals use the
native reader below; binary density/CIS fields require the ORCA utility
routes in the ORCA manual.
The desktop shell registers GBW, MCD and HESS as viewer file types.

Property parsing has been checked against supplied ORCA 6.1.1 text/JSON
pairs; Hessian import has also been checked with the supplied worked water
frequency example. Most other regression fixtures are synthetic. The
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
| Gaussian | `.fchk` / `.fch` for orbitals and supplied properties; `.gjf` / `.com` for Cartesian or Z-matrix geometry; cube for sampled fields | Binary `.chk` (run `formchk` first); generic log routes require `[results]` |
| ORCA | Open a property file, `.hess`, `.mcd`, exported `orca_2json` JSON or `.gbw` to combine same-basename results; `.out` also reads printed geometry, modes and IR/Raman/UV-Vis/CD/VCD; `.inp` locates companions; `.cube` for one scalar field | Output-text energies/orbitals, `.inp` results without companions; binary density/CIS evaluation requires ORCA utilities |
| VASP | Install `[ase]`, then open or import `POSCAR` / `CONTCAR` | `CHGCAR`, `LOCPOT`, `WAVECAR`, `DOSCAR`, `PROCAR` |
| CP2K, Quantum ESPRESSO, Psi4, PySCF and similar | QVF for a complete result; XYZ/CIF for geometry or cube for a sampled field. The existing `[results]` reader handles supported text outputs; see below. | Restart files and code-specific wavefunction files without a supported exchange route |
| TREXIO-producing programs | Open a TREXIO dataset with `[trexio]` for geometry and supported Gaussian orbitals | Correlated many-body data, unsupported orbital bases and periodic wavefunctions |
| Molecular-dynamics tools | XYZ/extended XYZ for matching multi-frame trajectories; PDB, GRO, Mol2, SDF or listed ASE routes for structures | Native binary trajectory formats; later frames or records in the single-structure PDB/GRO/Mol2/SDF readers |

`.molden` and `.molden.input` open supported real Gaussian orbitals directly.
A converted file only carries what
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

The importer contract is one of vibe-view's two extension points; the other
renders section kinds, and both are described, with their compatibility
promise, under [Extending vibe-view](python_api.md#extending-vibe-view). A
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

### XYZ trajectories and extended XYZ

Opening `.xyz` or `.extxyz` reads every complete frame. The first frame stays
as the default structure; each later frame is also available as its own
structure. Frames with identical atom identities and order additionally form
an animated trajectory. Different atom counts or identities are kept as
separate structures rather than being joined into an invalid trajectory.
A malformed later frame is an error, not silently discarded trailing text.

The native reader accepts the extended-XYZ `Properties` column layout,
`Lattice` (three row vectors in angstroms), and `pbc`. Each individual structure
retains its frame's cell. The trajectory currently animates coordinates only;
inspect the individual structures for variable-cell trajectories. Unused
per-atom columns are ignored. `charge` and `multiplicity` are retained per
frame in provenance, along with the original comments.

An explicit `energy=...` comment field is in eV by the extended-XYZ convention;
`energy_unit=Ha` (also `hartree` or `au`) declares hartree instead. Values are
converted to hartree for the trajectory energy plot. If any frame lacks energy,
the plot is omitted and the available values remain in provenance. Free-text
numbers are not guessed to be energies. See the
[extended-XYZ specification](https://github.com/libAtoms/extxyz).

### Other quantum-chemistry outputs

The optional `results` extra installs the BSD-3-Clause cclib parser. Open a
supported `.out` or `.log`, or select `--from results` during import. Supported
programs depend on cclib's parser inventory, including Gaussian, GAMESS,
NWChem, Psi4, Q-Chem, Molpro and Turbomole. Native ORCA output import takes
precedence for recognized ORCA files, including uploads. A `.out` or `.log`
that holds XYZ frames, as xtb's `xtbopt.log` does, opens with the XYZ reader
instead and needs no extra. Files are parsed as
data; no producing program is launched. An absent extra gives an installation
hint, and an unrecognized output gives a clear error.

The final geometry is the default structure. Multiple geometries form a
trajectory, with SCF energies only when their counts match the geometry count.
Frequencies, displacement modes, IR/Raman activities, excitation energies and
oscillator strengths become the corresponding sections when present. Charges
retain their method names: standard QVF roles are used where available and
other schemes use described custom charge roles. MO energies are available as
labelled spectrum markers, with spin channels and HOMO indices in provenance;
this output importer does not invent a wavefunction from an energy table.
Thermochemistry is converted into QVF's standard root fields.

SCF convergence diagnostics are retained in the SCF-history iteration records.
They are not relabelled as per-iteration energies: cclib's `scfenergies` are
converged energies of successive calculations. The generic SCF chart may show
only iteration counts when energy/DIIS fields are unavailable. The SDK exposes
the original diagnostic vectors as `convergence_values`.

A parsed one-dimensional scan with parameter values, geometries and energies
becomes a reaction path. A complete two-dimensional rectangular scan becomes
a scan surface, reordered by its coordinate values. Missing coordinates are
not inferred as IRC progress; such outputs remain geometry trajectories.
Incomplete or duplicate scan grids are rejected instead of being interpolated.

Conversions follow [cclib's attribute units](https://cclib.github.io/data.html):
SCF/MO/scan energies are eV, excitation energies are cm⁻¹, coordinates are Å.
Four pinned real-output regressions and their reproduction command are recorded
in `tests/data/cclib/README.md`.


## Native formatted checkpoint and Molden wavefunctions

Open `.fchk`/`.fch`, `.molden`, or `.molden.input` directly, or use
`converters.fchk_to_qvf` / `molden_to_qvf`. Both produce structure and
`wavefunction.gto` sections for the existing orbital, energy and density tools.
Real spherical and Cartesian s, p, d and f shells, split SP contractions,
restricted, unrestricted and restricted-open-shell occupations are supported.
Higher angular momentum, ghosts, absent coefficients, duplicate indices,
inconsistent dimensions and incomplete files fail explicitly. No partial
orbital is displayed after silently dropping a shell.

Gaussian exchange AO ordering is mapped to QVF's lexicographic Cartesian or
ascending-m spherical ordering. FCHK's individually normalized Cartesian
components require coefficient scaling; Molden's shell-wide convention does
not. Molden contractions are normalized analytically. Identified `orca_2mkl`
files additionally embed primitive normalization in their contraction
coefficients, which is removed before applying the QVF evaluator. No empirical
scale is fitted to a cube. Unidentified nonstandard Molden dialects cannot be
reliably inferred; export through a conforming writer or use a producing-program
cube. Source definitions: [Gaussian formchk documentation](https://gaussian.com/formchk/),
[Molden format](https://www.theochem.ru.nl/molden/molden_format.html), and
[Schaftenaar and Noordik (2000)](https://doi.org/10.1023/A:1008193805436).

Molden Occup fields are preserved, including fractional values. When an external
producer identifies them as natural orbitals, the Python API's explicit
`molden_to_qvf(source, natural=True)` records electron-occupation semantics.
The importer does not infer natural-orbital identity from fractional values or
mislabel transition weights as electrons.

FCHK imports Mulliken/ESP/NPA charges, the dipole vector, stored normal modes,
and Cartesian force constants when supplied. A Hessian without stored modes
uses the supplied real atomic masses to derive all 3N mass-weighted modes;
translations, rotations and imaginary frequencies are retained rather than
silently filtered. Hessian values and their hartree/bohr² units remain in the
vibration metadata. Stored mode displacement conventions are labelled.

Supplied total density matrices (SCF and correlated records) are preserved in
source-order packed form and transformed into the supplied orthonormal MO
space. Symmetric diagonalization produces separate, explicitly derived natural
orbital sections; the original canonical MOs remain intact. An AO reconstruction
check rejects densities outside the supplied MO span. Positive-density bounds
allow at most 1e-6 electron roundoff from printed fchk records, and every clipping
adjustment is recorded. Available matching spin densities produce alpha/beta
natural sections. Neither a correlated method name nor SCF occupations replace
a missing correlated density. See also [Psi4's fchk export caveats](https://psicode.org/psi4manual/master/fchk.html).

Recorded original Psi4 1.9.1 and ORCA 6.1.1 cubes exercise numerical orbital values,
including Cartesian and spherical cc-pVTZ in Psi4 and ORCA Molden versus the GBW
JSON route. Grid checks use each cube's actual coordinates and a predeclared
2e-5 absolute and relative-L2 bound; only a global MO sign may change. These are
field-value checks, not coefficient-only comparisons. Density cube text has
finite decimal precision; tests account explicitly for each printed value's
rounding interval and preserve the unadjusted residual separately.

A supplementary ORCA cc-pVTZ core-orbital cube exceeds the unadjusted absolute
2e-5 bound in both the established GBW and new Molden routes (3.8603e-5), while
relative L2 is 3.23e-6. Its peak is limited by six-significant-digit cube text;
the original raw cube comparison remains a failure. Independent native binary
output from the same GBW resolves the field-value question: both import routes
agree within 3.29e-7 absolute. Native Psi4 float64 AO-grid references likewise
verify both exchange routes and supplied densities within 6.38e-8. These checks
retain the same 2e-5 absolute and relative limits; they do not relabel rounded
cube failures as passes.
The same calculation's valence orbitals verify the ORCA-specific f-shell phases.
