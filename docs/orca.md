# ORCA: from calculation to one viewer dataset

Open an ORCA result file and vibe-view brings its matching structure,
orbitals, spectra and properties into one dataset. You can inspect that
dataset immediately, or save it as a QVF archive for another computer,
terminal work or sharing.

This chapter walks through an existing calculation named `calculation`.
Replace that name with your own calculation basename. The files and names
below are illustrative; the data available in your viewer depends on what
ORCA calculated and wrote. The importer has been checked with ORCA 6.1.1
results. To start a new calculation from the structure in the viewer, use
[Prepare, save and submit an ORCA input](#prepare-save-and-submit-an-orca-input).
For a complete example with downloadable input and calculated data, follow
[Water through ORCA and vq](orca_water.md), including reference values and
screenshots from a real calculation. For normal modes and spectra, continue
with [peroxide Raman and VCD](orca_raman_vcd.md). Its
[selected-mode workflow](orca_raman_vcd.md#combine-selected-modes) takes a
combination of vibrational directions through input export, a new ORCA job
via vq, and inspection of the newly calculated structure and orbitals.

## 1. Prepare the viewer and ORCA results

Install vibe-view using the [installation guide](installation.md). For a
new source installation on macOS or Linux, with Git and Python 3.11 or newer:

```sh
git clone https://github.com/vibe-qc/vibe-view.git
cd vibe-view
./scripts/install.sh
source .venv/bin/activate
vibe-view doctor
vibe-view formats
```

The installer uses the checked-out source. Public snapshots can lag
development; check that `formats` lists the ORCA importer in the version
you installed. The default installation includes the browser and terminal
viewers. Windows and existing-installation updates are covered in the
installation guide.

Property text, property JSON, exported orbital JSON and MCD text need no
ORCA installation to read. A selected `.gbw` needs the `orca_2json` utility
from the ORCA version that produced it. The viewer searches `PATH`; you can
instead set its executable explicitly before launching the viewer:

```sh
export ORCA_2JSON=/opt/orca/orca_2json
```

Replace `/opt/orca/orca_2json` with your installed executable. This variable
selects the utility; vibe-view does not install or redistribute ORCA.

Keep the results for each calculation together, using the filenames ORCA
gave them. A calculation directory might contain:

```text
results/
  calculation.inp
  calculation.out
  calculation.property.txt
  calculation.property.json
  calculation.gbw
  calculation.json
  calculation.cis-el.dipole-length.1.mcd
```

You do not need every file in this example. Text and JSON property files
are alternative representations; a GBW and its orbital JSON are also
alternatives. MCD files are present only for calculations that write them.

| File | What it contributes |
|---|---|
| `calculation.property.txt` or `.property.json` | Geometries, supported electronic spectra, population analyses, bond orders and property metadata |
| `calculation.gbw` or its exported `calculation.json` | Geometry and supported molecular Gaussian orbitals |
| `calculation.hess` | Geometry, stored harmonic frequencies and normal modes, plus IR and Raman sticks when present |
| `calculation.<method>-el.<operator>.<field>.mcd` | Circular-dichroism and absorption sticks for that method, operator and field |
| `calculation.out` | Locates same-basename results; adds a completed VCD table when geometry and modes match its Hessian |
| `calculation.inp` | An entry point that locates same-basename results; the input is not executed |

The exact basename and directory establish the dataset. `calculation` and
`calculation_restart` stay separate even if their geometries are identical.
If the input uses `%base` to change the output basename, open a result file
under that basename directly.

## 2. Open the whole calculation

In a terminal with the viewer environment activated, change to your results
directory and open a property file:

```sh
cd results
vibe-view open calculation.property.json
```

The matching orbitals and MCD files are discovered automatically. Opening
one of these files instead reaches the same calculation:

```sh
vibe-view open calculation.property.txt
vibe-view open calculation.gbw
vibe-view open calculation.hess
vibe-view open calculation.json
vibe-view open calculation.out
```

Choose one command, rather than starting a new viewer for every file. The
conversion happens in memory; it leaves the input files unchanged and does
not create a QVF. Stop the browser server with **Ctrl+C** in its terminal
when finished. Closing the browser tab alone does not stop the server.

For a native window from a source installation, use:

```sh
vibe-view desktop calculation.property.json
```

See [Desktop](desktop.md) for Electron setup and operating-system file
associations. In an already open viewer, the **Load file** dialog can open a
path on the machine running the viewer. A browser upload transfers only the
selected file, so it cannot discover companions left on your computer. For
a complete upload, [import the calculation to QVF first](#6-save-a-reusable-qvf).

### Which representation is selected?

An explicitly selected property file wins over its alternative. Otherwise,
the newest property representation is selected, with JSON winning equal
timestamps. Orbitals follow the same rule: an explicit GBW or orbital JSON
wins; otherwise the newest is selected, with JSON winning a tie.

For example, opening `calculation.property.txt` explicitly selects the text
properties, but it still discovers and selects the matching orbitals. If
the GBW is newer than the orbital JSON, `orca_2json` is required. The viewer
does not quietly replace that GBW with an older export.

### Start with properties only

If you have no ORCA utility installed and want to inspect the properties,
put a copy of the property file in a separate directory without a GBW:

```sh
mkdir property-preview
cp calculation.property.txt property-preview/
vibe-view open property-preview/calculation.property.txt
```

This needs no JSON conversion and no ORCA installation. The structure,
supported spectra and properties appear; orbitals are absent because this
directory contains no wavefunction. Merely choosing the property file in
the original directory does not disable companion discovery.

## 3. Inspect the structure and properties

1. Select **Structure** in the left sidebar. With a property file, this is
   the last geometry in that file. Drag to rotate, use the wheel to zoom,
   and press **r** to reset the camera.
2. Press **m** for measure mode. Pick two atoms for a distance, three for an
   angle, or four for a dihedral. Leave measure mode with **Esc**.
3. Read the **Run Info** card for available charge, multiplicity, electron
   count, ground-state SCF energy and dipole. A value appears only when the
   imported data supplies it.
4. Select **Atomic Properties** for an imported population analysis. The
   result panel shows its table; the **Population Analysis** controls let
   you choose an available **Method** and enable **Color atoms by charge
   (red +, blue −)**. Separate source method/state records remain separate
   sections.
5. Select **Bond Orders** to inspect the imported Mayer table, when present.
   Display bonds inferred from distances are separate from calculated bond
   orders.

Earlier property-file geometries form a **Trajectory** when atom identities
and order agree. If they differ, they remain separate structure sections.
The imported population and bond-order tables belong to the final geometry.

Some ORCA blocks are retained in `x_orca.properties` sections without a
renderer. They appear as unrendered vendor metadata, not as new plots or
tables. Correlated energies and excited-state dipoles are retained there
with their original context; they are not presented as ground-state SCF
results. An absent panel does not mean the corresponding ORCA calculation
failed.

## 4. View molecular orbitals

This step needs a supported GBW export containing the basis and MO
coefficients. A property file alone does not contain them.

1. Select **Wavefunction** in the sidebar. Its controls are headed
   **Molecular Orbitals**.
2. Click **HOMO** or **LUMO** for a frontier orbital, or choose an entry in
   **Molecular orbital** and click **Render MO**. Entries carry the
   available energy, occupation and spin information.
3. Change **Orbital isovalue**, then click **Render MO** again. Blue and red
   lobes represent positive and negative wavefunction values, respectively.
4. Adjust **Grid points / axis** and render again if you need a finer sampled
   surface. More points take more time and memory. **Orbital opacity** changes
   the displayed surface without reevaluating the orbital.
5. Click **Show energy diagram** to inspect the available MO energies.

The current ORCA importer supports real molecular spherical Gaussian s, p,
d and f shells, with RHF, ROHF, UHF and CASSCF orbital sets and their stored
occupations. It rejects unsupported or incomplete wavefunctions.

For supported occupations, the viewer can derive fields through **Compute
total density** and, for an unrestricted set, **Compute spin density**.
These are evaluated from the imported orbitals and occupations; they are
not imported ORCA density matrices. Individual CASSCF orbitals can be
displayed, but derived density, ELF and NCI are unavailable because this
export does not establish the orbital representation needed to reconstruct
the correlated density. Use a density cube produced by ORCA for that case.

### Export orbitals once, then view without ORCA

Opening a GBW makes vibe-view copy it to a temporary directory, run the
matching utility with a small configuration, read the result and clean up.
It does not request density matrices or integrals.

For a persistent orbital JSON, you can make the export yourself. The
following commands use a new directory so an existing calculation's export
configuration remains intact. Run them beside `calculation.gbw`, with the
matching `orca_2json` on `PATH`:

```sh
mkdir orbital-export
cp calculation.gbw orbital-export/
cd orbital-export
cat > calculation.json.conf <<'EOF'
{
  "MOCoefficients": true,
  "Basisset": true
}
EOF
orca_2json calculation.gbw -json
cd ..
cp orbital-export/calculation.json calculation.json
vibe-view open calculation.json
```

If you selected an executable through `ORCA_2JSON`, replace the utility
command with `"$ORCA_2JSON" calculation.gbw -json`. Check before copying
over an existing `calculation.json`. Keep the exact `Basisset` spelling in
the configuration. This export requests the information the viewer needs
for orbitals and avoids potentially large integral exports. The
[FACCTs orca_2json chapter](https://www.faccts.de/docs/orca/6.1/manual/contents/utilitiesvisualization/orca_2json.html)
documents the configuration and command options.

Property text can also be converted by the ORCA utility:

```sh
orca_2json calculation -property
```

That writes `calculation.property.json`; the argument is the basename,
without `.property.txt`. This conversion is optional for vibe-view because
it reads the text property file directly.

## Harmonic vibrations, IR, Raman and VCD

Open `calculation.hess` from a frequency run, or keep it beside the matching
property file and GBW to add vibrations and spectra to the same dataset. A standalone
Hessian file, including a browser upload, needs no ORCA utility. A companion
GBW still needs the matching `orca_2json`.

Select **Vibrations**, choose a **Mode**, and use **Play**, **Pause** or the
phase filmstrip to inspect its motion. The amplitude control scales the
display excursion; it is not a temperature or a prediction of thermal motion.
Zero and negative frequencies remain in their original order. The viewer's
mode labels start at 1, whereas ORCA's indices start at 0.

To use a mode for a new starting geometry, enter **Signed peak displacement
(Å)** below the animation controls and click **Create displaced structure**.
For example, **+0.10** and **−0.10** create opposite distortions, each from
the original equilibrium. The viewer opens the edited **Structure** for XYZ
export, ORCA input preparation or vq submission. Each application is one
undoable edit and stops auto-optimisation. To combine directions, choose
several **Modes to displace** and enter a signed amplitude for each. Their
contributions are summed from the original equilibrium in the same edit. See
[mode displacement](editing.md#displace-along-a-normal-mode) for the amplitude
definition. Follow [Combine selected modes](orca_raman_vcd.md#combine-selected-modes)
for a two-mode example with independent signed amplitudes, then
[calculate the displaced structure through vq](orca_raman_vcd.md#calculate-the-displaced-structure-through-vq)
for the tested submission, fetch, import and browser checks. The supplied
new result includes orbitals and charges; its single-point calculation does
not produce new vibrational spectra.

Select **IR Spectrum** to see harmonic sticks and an adjustable broadened
curve. ORCA 6 six-column IR records supply intensities in km/mol; older
five-column records retain their squared transition moments in atomic units,
with an explicit axis label. Broadening is a display choice. Neither
anharmonic corrections nor experimental line shapes are inferred.

The importer reads `$atoms`, `$vibrational_frequencies`, `$normal_modes` and,
when present, `$ir_spectrum`. It converts bohr coordinates to angstrom and
keeps the stored Cartesian mode vectors. It does not diagonalize `$hessian`
or recompute isotope shifts. Required blocks must be complete; an IR block is
optional, and an absent block produces no IR section. Other Hessian-file
blocks are not used to recompute spectra.

When present, `$raman_spectrum` adds **Raman Spectrum** with scattering
activities in Å⁴/amu. These are not laser- and temperature-dependent Raman
intensities. The completed `calculation.out` adds **VCD Spectrum**, preserving
positive
and negative rotatory strengths in 10⁻⁴⁴ esu² cm². ORCA 6.1.1 writes these
strengths only to its output log, not to the Hessian or property file. The chart
labels the
imported quantity explicitly; it does not infer a molar Δε spectrum.
Leave **Normalize** off to compare source strengths, and use **Sticks only**
to remove the display broadening. Missing Raman or VCD data create no plot;
malformed tables or mismatched
frequencies produce an import error. VCD import also checks the final output
geometry against the Hessian and requires one normally terminated run.
The printed VCD strengths have two decimal places; higher precision is not
inferred.

For vq results, keep `stdout.log` in the result directory. It is considered
only when no named `.out` exists, its input header names exactly this
calculation, and no Compound or `%base` directive is present. Ambiguous
queue logs are ignored. Use a matching named output for those workflows;
multiple VCD tables in one output are rejected. A standalone Hessian upload
cannot supply VCD: import the whole calculation to QVF before uploading.

Follow [Raman and VCD through ORCA and vq](orca_raman_vcd.md) for a real
calculation, downloadable results and numerical comparisons.

ORCA can recenter Hessian coordinates relative to property/GBW coordinates.
The importer accepts a common translation within 0.00001 Å, records it in
the vibration metadata and uses the shared calculation geometry for animation.
It rejects changed bond geometry, reordered atoms, rotations and conflicting
multiplicities when both files provide them.

Try the [water frequency tutorial](orca_water.md#7-extend-the-example-to-vibrations-and-ir)
with its real ORCA 6.1.1 results. The source format and normal-mode convention
are described in the [FACCTs vibrational spectroscopy manual](https://www.faccts.de/docs/orca/6.1/manual/contents/spectroscopyproperties/vibrations.html).

## 5. Explore electronic spectra and MCD

1. Select **UV-Vis Spectrum** or **ECD Spectrum** in the sidebar for a
   property-file spectrum. MCD plots appear as **Spectrum**. Several rows
   may share a sidebar title; read the selected chart's title to identify
   its method, gauge, geometry or source filename.
2. Hover over a stick to inspect its coordinate and strength. Property-file
   transition labels retain available state, symmetry and multiplicity
   assignments.
3. Set **Display** to **Sticks only** to inspect the discrete source values,
   or **Envelope + sticks** to show broadening as well. Use **Broadening**
   to change the displayed envelope.
4. Leave **Normalize** off when comparing absolute strengths. Turn it on
   when you deliberately want to compare shapes.
5. Use **X unit** to select the native axis, energy, wavenumber or wavelength
   when available. Read the resulting axis label before exporting.
6. Click **Export CSV** to save the plotted envelope and peaks using the
   current broadening, normalization and X-unit settings.

| Source | Native X axis | Stick strength |
|---|---|---|
| Property-file absorption | eV | Oscillator strength |
| Property-file ECD | eV | Signed rotatory strength, in 10⁻⁴⁰ cgs |
| MCD circular-dichroism plot | cm⁻¹ | L−R oscillator strength |
| MCD absorption plot | cm⁻¹ | L+R oscillator strength |

The importer keeps distinct methods and length/velocity gauges, repeated
tables, negative strengths and repeated energies. Broadening is a display
operation. It does not convert ECD rotatory strengths to molar circular
dichroism or reproduce the temperature-dependent MCD processing of
`orca_mapspc`. The
[FACCTs MCD chapter](https://www.faccts.de/docs/orca/6.1/manual/contents/spectroscopyproperties/mcd.html)
describes ORCA's MCD files and spectrum processing.

### Aggregate and per-transition MCD files

ORCA may write an aggregate such as
`calculation.cis-el.dipole-length.1.mcd` alongside transition files such as
`calculation.cis-el.dipole-length.1.0_1.mcd`. The aggregate takes the place
of its transition files during normal dataset discovery, so the same data
is not counted twice. Other methods, operators and field variants remain
separate.

To inspect a transition deliberately, open that file explicitly:

```sh
vibe-view open calculation.cis-el.dipole-length.1.0_1.mcd
```

It is included as an additional plot alongside the aggregate; the viewer
does not add their strengths together. A standalone three-column `.mcd`
also opens without a structure or any other result file.

## 6. Save a reusable QVF

Import the calculation once, then inspect the resulting archive:

```sh
vibe-view import calculation.property.json -o calculation.qvf
vibe-view validate calculation.qvf
vibe-view info calculation.qvf
vibe-view table calculation.qvf
```

The import uses the same companion discovery as `open`. `validate` checks
archive integrity; it does not validate the underlying chemistry. `info`
lists the available sections. `table` lists which sections have scientific
tables. If the corresponding data is present, export it with:

```sh
vibe-view table calculation.qvf --kind wavefunction.gto --format csv > orbitals.csv
vibe-view table calculation.qvf --kind atom_properties --format csv > charges.csv
```

The QVF is self-contained for the data imported by the viewer. Send it to a
colleague, copy it from a compute node, or upload it through the browser's
**Load file** dialog. Opening it no longer needs the original companions
or an ORCA installation. Keep the original ORCA results too: the QVF does
not include every ORCA output, restart file or binary record.

For a record of the files selected and the alternatives omitted during
assembly, inspect the archive provenance from Python:

```python
from vibeview.qvf import QVFReader

with QVFReader("calculation.qvf") as reader:
    print(reader.provenance["orca_calculation"])
```

Without `-o`, a single calculation imports to `calculation.qvf` beside the
source. Existing archives are not overwritten unless you pass `--force`.
To refresh one after ORCA finishes another run, repeat the import with
`--force` once the source files are consistent. Loose ORCA companions are
not watched for automatic reload. **Save Session** saves viewer state; use
`import` to create the persistent scientific archive.

## 7. Work on a compute node or make a figure

The remaining commands take QVF files, so use the archive from step 6:

```sh
vibe-view show calculation.qvf
vibe-view tui calculation.qvf
```

`show` prints a structure view without a display server or OpenGL. `tui`
provides an interactive terminal interface when its optional dependency is
installed. See [Terminal mode](terminal.md) for navigation and supported
section views.

For a structure figure, check the offscreen renderer and capture a PNG:

```sh
vibe-view capture-selftest
vibe-view capture calculation.qvf --publication -o calculation-structure.png
```

PNG capture needs a working offscreen graphics backend. `capture` does not
directly render `wavefunction.gto` or spectra; use the interactive orbital
view and its screenshot control, spectrum CSV export, or the documented
[headless/Python chart routes](headless.md) for those tasks.

For a browser connected to a remote machine, keep the viewer bound to
loopback and follow the SSH tunnel steps in
[Work remotely](workflows.md#work-remotely). A QVF can instead be copied to
your workstation and opened locally.

## Build and pre-optimise an ORCA starting geometry

These steps work in the browser and desktop viewer. They prepare coordinates;
the subsequent ORCA calculation uses the method and basis you select in
**Calculation Parameters**.

1. Open a starting structure, use **Build molecule**, or draw in vibe-sketch
   and choose **Send to vibe-view**. See [Building and editing](editing.md)
   for the optional sketcher and SMILES dependencies.
2. Press **e** or click the pencil to open **Atom Editor**. Finish adding
   atoms, fragments and hydrogens. Check element identities and connectivity.
   Enable **Drag atoms** to position an atom with the mouse. Shift-click
   builds a multi-atom selection; **Select connected** expands it to a
   bonded group. Drag a selected atom to move that group together. See
   [Drag atoms and selections](editing.md#drag-atoms-and-selections) for
   camera controls, frozen atoms and undo.
3. For pre-optimisation, make vibe-qc available in the viewer's Python
   environment, select **Optimizer → FIRE (damped MD)** or the default
   **L-BFGS**, and enable **Auto-optimize**. The default force engine is
   MSINDO; MACE is offered when its optional stack is installed. This is
   an approximate builder relaxation, not an ORCA optimisation. If the
   engine is unavailable, the status explains why and the switch turns off.
4. Wait for **relaxed in N steps**, then turn **Auto-optimize** off. If the
   status reports **not converged**, inspect the geometry and start another
   run if appropriate. Freeze selected atoms before relaxing if they must
   stay fixed. Each relaxation run can be undone in one step.
5. If a slightly asymmetric starting geometry is wanted, leave no atoms
   selected, set **Maximum displacement (Å)** to **0.01**, then click
   **Random displacement**. Every unfrozen atom moves by at most that
   distance. To perturb only part of the molecule, select those atoms first.
   Undo reverses the change; Redo restores the same perturbation. The action
   also switches off Auto-optimize so the perturbation survives until export.
6. Click **Save geometry (XYZ)** in Atom Editor. Open the downloaded `.xyz`
   in a text editor: the first line is the atom count, the second a comment,
   followed by element labels and x/y/z coordinates in Å.
7. Select the **Structure** section and continue below to preview/save an
   ORCA `.inp` or submit through vq. Both routes use the current edited
   geometry, including the pre-optimisation and displacement.

Start with a neutral small molecule for this builder workflow. The live
MSINDO preview currently uses a neutral charge and chooses singlet/doublet
from electron parity; it does not consume the ORCA charge/multiplicity
settings. Set those explicitly for the final ORCA input. Random displacement
does not promise to fix a linear-dependence problem in a particular job.
For the sampling rule, limits and optimiser reference, see
[the editor guide](editing.md#save-coordinates-and-break-symmetry).

## Prepare, save and submit an ORCA input

The browser and desktop viewer can prepare a basic molecular ORCA input from
any loaded structure, including the geometry you have changed in
[Edit mode](editing.md). Saving an input needs neither ORCA nor vq installed.
Submitting it needs both, in the environment running the viewer.

### 1. Choose the structure and calculation

1. Open a molecule, for example `vibe-view open water.xyz`, or load an existing
   ORCA dataset and select **Structure** in the sidebar.
2. Make any geometry edits, then open **Calculation Parameters** and select
   **ORCA** under **Calculation engine**.
3. Select the method and basis. The basic form offers HF, PBE, PBE0, B3LYP,
   BP86, TPSS and r2SCAN, with common molecular basis sets. The defaults are
   PBE/def2-SVP; choose settings appropriate to your calculation.
4. Set **Charge** and **Multiplicity** explicitly. Neutral water uses `0` and
   `1`; a water radical cation uses `1` and `2`. The form checks electron-count
   parity but does not determine the physically correct spin state for you.
5. Choose **Single Point**, **Geometry Optimization**, **Frequencies**, or
   **Single Point + Freq + Opt**. The combined option writes `Opt Freq`.
6. Set **Processes (nprocs)** and **MaxCore (MB per process)**. Start with one
   process and 1000 MB for a small example. Parallel runs require a working
   ORCA MPI installation on the execution machine.
7. For a frequency calculation, optionally enable **Raman activities (ORCA
   6.1+)** and/or **VCD rotatory strengths**. Raman adds `%elprop Polar 1 end`;
   VCD adds `%freq doVCD true end` and selects `AnFreq`. Saving or submitting
   these options with a single point or optimization-only task is rejected.

This first input form supports isolated molecules and inline Cartesian
coordinates in angstrom. Periodic cells are rejected. It does not yet expose
solvents, dispersion corrections, constraints, custom bases, excited states,
Compound jobs or arbitrary ORCA blocks. You can edit the downloaded text in
an external editor for those workflows; the viewer's Submit action always
regenerates its input from the form and current geometry.

### 2. Preview and save

Click **Preview ORCA input** to inspect the generated text. Click Preview
again after changing the settings or geometry. **Save ORCA input (.inp)**
regenerates the input and downloads `calculation.inp`; the download also
appears in **Recent Exports**. Save it in a new calculation directory.
A water single point has this form (coordinates depend on your structure):

```text
# Generated by vibe-view; Cartesian coordinates in angstrom
! PBE def2-SVP TightSCF SP
%pal nprocs 1 end
%maxcore 1000
* xyz 0 1
O  0.0000000000 0.0000000000 0.0000000000
H  0.7586000000 0.0000000000 0.5043000000
H -0.7586000000 0.0000000000 0.5043000000
*
```

The input syntax follows the FACCTs [input-file reference](https://www.faccts.de/docs/orca/6.1/manual/contents/essentialelements/input.html)
and [parallel execution guide](https://www.faccts.de/docs/orca/6.1/manual/contents/essentialelements/parallel.html).
ORCA chooses the HF/DFT spin reference from the multiplicity. The generated
input contains no orbital coefficients, densities or integral exports.

### 3. Prepare vq once

Install vq into the viewer's Python environment using the
[queue installation instructions](queue.md#installing-it). Install ORCA
separately and register its **absolute executable path** under the name
`orca` in the local vq configuration. A binary entry looks like:

```toml
[programs.orca]
kind = "binary"
binary = "/opt/orca/orca"
```

Use your own installation path in the existing vq configuration; do not
replace unrelated entries. Run `vq programs` to check the registration and
start/configure the daemon using the
[vibe-queue documentation](https://github.com/vibe-qc/vibe-queue).
The viewer currently submits ORCA to the **local vq queue**. A remote-only
scheduler registration is not sufficient for this form.

### 4. Submit and monitor

Click **Submit ORCA to vq**, review the settings, enter a **Wall time
(minutes)** budget and click **Submit to vq**. The viewer stages
`calculation.inp` and submits a directory job tagged `vibe-view` and `orca`.
It calls the registered ORCA executable directly; ORCA starts its own MPI
processes. The queue CPU request equals `nprocs`.

`MaxCore` is per process and does not describe all memory used by ORCA.
The queue reservation is rounded up from `nprocs × MaxCore / 0.75`, following
the manual's recommended headroom. For four processes at 1200 MB each,
the viewer requests 6400 MB. This is a reservation, not a promise that every
calculation fits within that amount.

The **Job Manager** opens with live monitoring enabled. Inspect the log tail
for ORCA's output; vq captures stdout rather than creating `calculation.out`.
ORCA jobs do not offer QVF live checkpoints or pending QVF containers. If vq,
the executable or queue capacity is unavailable, the status bar reports the
problem. The viewer never runs an ORCA submission through vibe-qc or silently
launches it outside the queue.

### 5. Open the finished calculation

When the job completes, use **Fetch & open results** in the Job Manager. The viewer
fetches the job directory and opens matching ORCA property, orbital, Hessian
and MCD files as one dataset per calculation basename. A matching completed
output supplies VCD. It also opens QVF archives
from jobs that produce them. Keep the fetched companions together.

For GBW orbitals, the viewer still needs the matching `orca_2json` utility
as described in step 1 of this chapter. Property text and existing JSON can
be read without it. The spectra and properties available depend on what the
calculation requested; a basic HF/DFT single point does not produce every
spectrum. Continue with the inspection and QVF export steps above.

### 6. Check the round trip

Use the [worked water tutorial](orca_water.md) for the exact tested geometry,
downloadable QVF, screenshots and numerical comparisons.

For a small first test, use neutral singlet water with **PBE**, **def2-SVP**,
**Single point**, one process and 1000 MB MaxCore. A five-minute queue budget
is sufficient for this small example on a typical workstation. After fetching:

1. Select **Structure** and check the atom count and bond measurements against
   the geometry in the input preview. A single point preserves that geometry.
2. Compare **Run Info** energy, convergence, charge and multiplicity with
   `stdout.log` in the fetched directory.
3. Select **Wavefunction**, click **HOMO**, then **Render MO**. Repeat with
   **LUMO**. Use **Camera → Reset / fit all** if the orbital fills the viewport.
   Compare orbital energies and occupations with ORCA's orbital-energy table.
4. Select **Atomic Properties** and compare the Mulliken charges with the log.
   Compare dipoles in the same units; conversion constants can cause small
   differences in the last displayed Debye digits.

This submission, fetch and inspection sequence has been exercised with a real
ORCA 6.1.1 calculation. It checks the basic molecular workflow; calculations
requesting spectra or other methods need their own corresponding checks.

## Several calculations and Compound jobs

Open several explicit result files to switch between their datasets through
the **Files** dropdown:

```sh
vibe-view open first.property.json second.property.json
```

For an entire result tree, make persistent archives in one command:

```sh
vibe-view import results/ -o imported/
```

Directory import recursively discovers supported sources and deduplicates
ORCA companions. It creates one QVF per calculation, while unrelated
supported files still import separately. GUI folder/glob discovery and
multiple CLI arguments also deduplicate companions. Automatic scans skip
auxiliary `TDDFTGuess` wavefunctions when primary results exist; opening a
guess file explicitly still shows it as its own dataset.

Each ORCA Compound step has its own result basename. Open a step's property
file or GBW, or import the directory to keep all steps separate. An outer
`.out` naming several steps reports the available calculations rather than
merging their electronic states. See the
[FACCTs Compound documentation](https://www.faccts.de/docs/orca/6.1/manual/contents/workflowsautomatization/compound.html)
for the producer workflow.

## Troubleshooting and current limits

| What you see | What to do |
|---|---|
| `orca_2json` cannot be found | Install the matching ORCA utility, set `ORCA_2JSON`, or use a current exported orbital JSON. For properties alone, use the separate-directory workflow above. |
| An old JSON exists but opening still requests `orca_2json` | The GBW is newer. Export it again or explicitly open the JSON you intend to inspect; explicit selection does not prove it is scientifically current. |
| Property and orbital geometries do not match | Check for mixed runs, an old GBW, reordered atoms or different final geometries. Restore companions from the same calculation. Do not rename unrelated files just to make them combine. |
| Charge or multiplicity disagree | Check the selected property and wavefunction representations. They cannot form one dataset until their electronic-state metadata agrees. |
| Opening `.out` or `.inp` finds no results | Open the actual result file directly, especially after `%base`, or keep matching result files beside the locator. An output alone is not a full calculation import; VCD needs the matching Hessian. |
| Orbitals, charges or spectra are absent | Check the selected files and section inventory. A property file supplies no orbital coefficients; a GBW supplies no property-file spectrum; a spectrum must have been calculated and exported. |
| Several spectrum entries have the same sidebar title | Select them and inspect the chart labels and source filename. Methods, gauges and geometry records remain distinct. |
| A property section is greyed out | Its raw block is retained as vendor metadata but has no renderer yet. |
| A browser upload lacks companion data | Import locally to QVF and upload that archive. Uploading a single loose file does not transfer neighboring files. |
| Raman is missing after a frequency calculation | Request `%elprop Polar 1 end` and check for `$raman_spectrum` in the resulting Hessian. Ordinary frequencies alone do not request Raman. |
| VCD is missing although IR and Raman appear | Keep the completed output with the Hessian. For vq, check that `stdout.log` names this exact input; for a named output use the calculation basename. A Hessian alone has no VCD table in the tested ORCA 6.1.1 output. |
| VCD import reports a geometry, mode or frequency conflict | Fetch the whole completed job again into a new directory. Do not combine a restart Hessian with an earlier output or hand-edit frequencies to bypass the check. |
| The plotted spectrum differs from the output table | Select **Native unit**, turn **Normalize** off and choose **Sticks only**. Compare Raman activities and VCD rotatory strengths in the labelled units, allowing for the source's printed precision. |

Joining orbitals to properties requires identical ordered element identities
and coordinates within 0.00001 Å of the final property geometry. The viewer
does not rotate, align or reorder orbitals to make the files agree. Hessian
companions additionally allow the common translation described above.
Charge and multiplicity must agree when both sources provide them. A
selected unreadable or incompatible companion stops the import with an
error, rather than leaving a partial dataset that looks complete.

The native output parser currently reads only the completed VCD table and
the final coordinates needed to check it against a Hessian. General output
energies, orbitals and other properties still come from their dedicated
result files. The importer does not read scientific results from `.inp`,
Molden files, binary `.densities` or `.cis` amplitudes.
Export a cube from ORCA when you need a sampled orbital or
density unsupported by the wavefunction route; the cube opens as its own
structure/volume dataset and is not automatically folded into the ORCA
calculation. Native NTO/amplitude and density-matrix import remain outside
this workflow.

For the full importer contract, see [Input formats](formats.md#orca-results).
For the source property vocabulary, consult the
[FACCTs property-file documentation](https://www.faccts.de/docs/orca/6.1/manual/contents/utilitiesvisualization/property_file.html).
