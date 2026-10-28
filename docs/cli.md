# CLI reference

`vibe-view --help` and `vibe-view COMMAND --help` are authoritative for the
flags. This page groups the commands by what you are trying to do, names
the extra each one needs, and calls out the ones with a caveat.

```sh
vibe-view --version      # version, codename, Python, PyVista and VTK
vibe-view --help
```

The product and the command are spelled `vibe-view`; the Python
distribution and import package are `vibeview`. Most commands run on the
core install. `open`, `compare`, `serve` and `desktop` need the browser
stack (`[viewer]`); `tui` needs Textual (`[tui]`). Run a command
whose extra is missing and vibe-view names the extra and the exact install
command rather than failing obscurely.

## Getting started

| Command | Needs | What it does |
|---|---|---|
| `doctor` | core | Diagnose the installation and every optional mode. `--json` for scripts. |
| `demo` | core | Write a bundled water-structure archive and check that its manifest opens. `-o FILE` names it, `--force` replaces it, `--open` launches the browser afterwards (with `--port`, `--host` and `--no-browser`). Needs no input and no vibe-qc. |
| `examples` | core | Show worked workflows. `--copy DIR` copies the bundled example files; `--force` replaces existing copies. |
| `formats` | core | List the built-in, optional and plugin importers this installation has, with their extensions and availability. `--json` for scripts. |
| `quickstart` | vibe-qc | Run a real calculation and open it. **The only command on this page that requires vibe-qc in the same environment.** |

## Looking at a file

| Command | Needs | What it does |
|---|---|---|
| `open INPUT...` | `[viewer]` | The interactive [browser viewer](browser.md). Loose structure files convert in memory. Several files give a Files dropdown; `--auto-compare` starts in compare mode; `-s ID` activates a section on startup; `--view KIND`, `--mode N` and `--orbital LABEL` open on a result view (orbitals, density, a vibration mode or a spectrum); `--return-dir DIR` enables checked geometry return to vibe-sketch; `--port`, `--host`, `--no-browser`, `--log-file`. |
| `compare A B` | `[viewer]` | `open A B --auto-compare`, including `-s ID`. |
| `desktop [FILE]` | `[viewer]` + source checkout | A native window; see [The desktop app](desktop.md). `--port`, `--no-sandbox` (Linux, last resort). |
| `tui FILE` | `[tui]` | The interactive [terminal viewer](terminal.md). `--mode braille\|half`. |
| `show FILE` | core | One frame as terminal braille, then exit. `-s ID`, `--all`, `--info`, `--size`, `--mode`, `--plain`, `--representation`, `--color-by`, `--rotate`, `--isovalue`, `--replicate`, `--labels`, `--frame`, `--normal-mode N`, `--chart`; for a spectrum, `--lineshape gaussian\|lorentzian`, `--fwhm` and `--unit`. |
| `serve [DIR]` | `[viewer]` | A web file browser over a directory of archives. |
| `dashboard PATTERNS...` | core | A grid preview of many archives, as an image or, with `--html`, a page. `--cols`, `--cell-size`. |

For a directory of independent results, use `serve DIR` for a browsable
listing or `import DIR` to convert its contents. A TREXIO text-backend
directory is one dataset and can be passed directly to `open` with the
`[trexio]` extra; see [TREXIO wavefunctions](formats.md#trexio-wavefunctions).

For ORCA, `open calculation.property.json` (or another result companion)
opens matching structure, orbitals, spectra and properties together.
`open calculation.out` and `.inp` locate same-basename result files. Repeated
companions in the argument list are deduplicated. See the [ORCA chapter](orca.md)
for GBW prerequisites and explicit representation selection. Use `import`
first for commands such as `info`, `table`, `tui` and `capture` that read QVF:

```sh
vibe-view import calculation.property.json -o calculation.qvf
vibe-view info calculation.qvf
vibe-view table calculation.qvf --kind wavefunction.gto
```

## Asking questions

| Command | Needs | What it does |
|---|---|---|
| `info FILE` | core | Metadata, and each section's status (`rendered` or `skipped`, with the reason, or `via plugin` when an installed renderer plugin draws it) and size, with each structure's label and name and any other section's label, and the first structure of an archive with several. `--short` is one line per archive, for a loop; `--json` for scripts, where a structure carries its whole chemical identity (`label`, `name`, `charge`, `multiplicity`, `smiles`, `inchi`, `inchikey`) and `first_structure` names the first structure, and every section carries `status` and `status_detail`. |
| `table FILE` | core | Tabular sections, `--format table\|csv\|json`. With no `--kind`, lists what the file offers; the kinds are `vibrations`, `atom_properties`, `wavefunction.gto`, `excited_states` (one row per state: energy in eV and nm, oscillator and rotatory strengths, multiplicity, symmetry, orbital contributions and the linked NTO and density sections), `forces` (F = −∇E per atom, Eh/bohr), `hessian` (harmonic frequencies from the mass-weighted Hessian), `properties.electric`, `properties.elastic` and `properties.dielectric` (one row per number: quantity, component, value, unit), and `wavefunction.bloch` (each orbital at each k-point with its energy and occupation). |
| `validate FILE...` | validation | Strict validation through qvf's reference package: container/resource checks, schemas, hashes, layouts, references and semantic rules. Prints finding codes and warnings; exits 1 on invalid archives. Without the `validation` extra it reports each file as `NOT CHECKED`, prints the installation hint once and exits 1. See [setup](qvf.md#strict-validation). |
| `diff A B` | core | Energy delta (including zero), section overlap, geometry RMSD in the browser's saved Length unit, or why the RMSD was skipped (an unreadable structure, no atoms, differing atom counts). `--json` keeps the RMSD in Å and reports every error as JSON on stdout: an archive that cannot be opened as `{"error", "file", "path"}`, any other error as `{"error"}`. |
| `batch-compare FILE...` | core | The same across a set, against `-r` as the reference, counting the files as given from 0. The summary and table give the RMSD in the saved Length unit. A file that does not open is listed as skipped with the reason, and a missing RMSD says why (an unreadable structure, no atoms, differing atom counts); a reference file that does not open is an error. `--json` keeps `rmsd_angstrom` in Å and reports every error as `{"error"}` on stdout, including invalid arguments (exit 2); runtime errors exit 1. |
| `stats [DIR]` | core | Aggregate statistics over every archive in a directory. Files it cannot read, a dangling symlink included, are counted and named apart. |
| `recent` | core | Recently opened files. `-n` limits the list. |
| `vq-features [FEATURE]` | core | Which visualizations a producer has to write what for. No queue needed; it is a static table. |

`table --kind` selects the first section of that kind in archive order.
It does not combine several charge schemes or wavefunctions. Use
`info` to inspect the section inventory and the
[typed Python readers](python_api.md#reading-a-file-repeatedly) to select
a specific section by ID.

`table --kind atom_properties` includes IAO charges and spin populations when
the archive supplies them, including sections containing only either array.
It keeps the `atom`, `element`, `mulliken`, `loewdin` and `hirshfeld` columns,
then appends `iao` and `spin_population` when present. Missing charge values
are `null` in JSON and empty cells in CSV; population values are rounded to
four decimal places.

`table --kind vibrations` starts with `mode` and `frequency_cm-1`, then adds
a column for each per-mode quantity the archive has, named with its unit:
`symmetry`, `ir_intensity_km/mol`, `raman_activity_angstrom^4/amu`,
`vcd_rotatory_strength`, `reduced_mass_amu` and `force_constant_mdyn/angstrom`.
Values stored in the vibrations section come first. A companion `spectra.ir`,
`spectra.raman` or `spectra.vcd` section fills a quantity the section lacks,
under the unit it declares. A spectrum that lists every mode is matched by
position; a shorter one by its peaks' `mode_index`, or else by an exact
frequency. A mode with no value has an empty cell, `null` in JSON: ORCA's
VCD table, for example, omits the translations and rotations.

The `batch-compare` Markdown table keeps each file in one row, including
filenames with pipes, backslashes or line breaks. Whitespace in table cells
is normalized for display; `--json` preserves the original filesystem paths.

Energy comparisons and file-browser pages distinguish a physical zero from
missing metadata. Equal energies produce zero deltas in all JSON units and
an explicit equal-energy result in text; missing energies remain `null` in
JSON and use a placeholder in file-browser pages.

`diff A B` reports energy differences as A − B. Its geometry RMSD pairs
atoms by index in their stored coordinate frame, without alignment or an
element-identity check. Verify ordering before comparing; see
[SDK comparison semantics](python_api.md#comparing) for explicit alignment.

## Figures, without a display

These are the core install: no `[viewer]` extra, no browser. They do want
an offscreen GL context for PNGs and animation. Data and vector exports do
not need one. [Figures without a display](headless.md) covers
setting one up, and `show` is the fallback when there is none.

| Command | What it does |
|---|---|
| `capture FILE` | Render a section to PNG, by default the archive's first structure. `-s ID`, `-o PATH`, `--isovalue`, `--colormap`, `--size WxH`, `--publication` (the browser's Publication look) and `--part real\|imag\|modulus` for a complex field, `--frame N` for a slab of a volume series (from 0), `--render auto\|isosurface\|volume` for a volume ([details](headless.md)). A band structure draws its axis in the saved Band / DOS energy unit. |
| `batch PATTERNS...` | A PNG gallery from many archives. `--volumes` includes volume sections; `-o DIR`, `--size`. |
| `animate FILE` | A trajectory, reaction path, vibration, orbital sweep or turntable as MP4 or GIF. `-k KIND` (`auto` picks the first of trajectory, reaction, vibration, orbital, then a turntable of the structure), `--mode N` (0-based; by default the first positive-frequency mode, and a mode outside the section is refused), `--fps`, `-f mp4\|gif\|frames` (by default from the `-o` suffix, `.gif` or `.mp4`, else MP4), `--size`, `--isovalue`. For a vibration, `--amplitude`, `--frames-per-cycle`, `--arrows` with `--arrow-cutoff` (Å), `--arrow-color` and `--arrow-thickness`, and `--loop`/`--no-loop` for GIFs. |
| `export FILE` | The structure as `xyz`, `cif`, `cml`, `json` or a `py` input script; meshes as `obj` or `gltf`; scenes for `pov` (POV-Ray) or `blend` (Blender); pages and figures as `html`, `svg` or `pdf`. `-f FORMAT`, `-o PATH`, `-s ID` for a structure other than the first. |
| `spectrum FILE` | A display-broadened spectrum as a whitespace-delimited text file, from a QVF or a supported ORCA dataset. `--section ID` (required), `-o PATH` (required), `--lineshape gaussian\|lorentzian`, `--fwhm` with `--unit native\|eV\|cm-1`, `--x-unit`, `--normalize`, and the grid in native energy units: `--grid-min`, `--grid-max`, `--grid-step`. A wrong `--section` lists the file's spectrum sections. The file records its settings and the original sticks; see [Explore electronic spectra](orca.md#5-explore-electronic-spectra-and-mcd). Needs no GL context. |
| `capture-selftest` | Prove the offscreen pipeline renders a non-blank image, and report the backend. `-o FILE` keeps the test PNG. Run it first on a new host. |

An archive can hold several structures under any section ids, such as the
species of a vibe-sketch document (`structure_m1`, `structure_m2`, ...). A
command that needs one structure takes the **first structure**: the first one
`viewer_defaults.auto_open` lists, else the first structure section (QVF spec
§ 4.1). `export -s ID` and `capture -s ID` take another. A wrong `-s ID`
on any command lists the file's section ids and kinds. A `py` input script
takes the structure's own charge and multiplicity when the archive states
them, else the provenance's.

Standalone HTML exports initially fit the camera and orbit target to the
rendered structure, including atom sizes and bonds across periodic cell faces.
The fit accounts for the page's aspect ratio, so translated coordinates and
large structures remain visible in both landscape and portrait windows.
Archived coordinates and connectivity are preserved in the page.

Without `-o`, a command that transforms one archive (`capture`, `animate`,
`export`, `slice`, `h-add`, `supercell`) writes its result beside that
archive, named `<stem>_<what>.<ext>`; `--help` shows the exact name. Commands
that gather several inputs (`batch`, `merge`, `dashboard`, `import` with more
than one source) write in the working directory.

`batch` continues after individual file failures; a successful exit means
at least one file rendered, not that every requested image exists. Output
names use archive basenames, so equally named files from different input
directories need separate galleries. See
[batch output and partial failures](headless.md#output-names-and-partial-failures).

## Building and reshaping archives

| Command | What it does |
|---|---|
| `import INPUT...` | Convert loose files into persistent archives. One input writes a sibling `.qvf`; several, or a directory searched recursively, write one archive per source into `./vibe-view-imports` or `-o DIR`. `--from FORMAT` forces an importer; `--force` replaces outputs. See [Input formats](formats.md). |
| `slice FILE` | Keep (`-k`) or drop (`-d`) sections by id or kind into a new archive. Preserves metadata and validates the output before replacing it; removed viewer hints are pruned and dangling section references are refused. |
| `merge FILE...` | Combine sections from several archives. Root metadata and viewer defaults come from the first. All sections are retained; duplicate ids and their QVF-defined references are remapped together. Member hashes and the output manifest are checked before atomic replacement, including when writing over an input. |
| `supercell FILE` | Replicate a periodic structure `--nx --ny --nz` times, each at least 1, along periodic axes only: a slab cannot be replicated along its vacuum axis. Writes the structure only and names what it leaves behind. |
| `h-add FILE` | Saturate open valences with hydrogens; writes `<stem>_h.qvf` beside the input unless `-o`. Other sections and the provenance describe the input geometry and are not carried over; the command names them. |

`slice` and `merge` accept legacy version-2 archives but write QVF version 1,
following the current format specification. Both check the output before replacing
an existing file, including section references stored inside wavefunction bases.

ORCA directory imports produce one QVF per calculation basename, with Compound
steps kept separate. Use `vibe-view import results/ -o converted/`; there is no
need to merge the resulting ORCA companion sections manually.

Python-input imports resolve atom-list comprehensions over literal lists and
tuples, including multiple generators and filters with comparisons, membership,
`and`, `or` and `not`. Filters run in order and skip excluded atoms; loop variables
stay local to the comprehension. An atom list with an unsupported filter, such
as a call to a custom selection function, raises an error without writing an
archive. Supply explicit atoms or supported literal filters. Input code is never
executed.

Every atom and position in a literal list must also resolve statically. An
unsupported coordinate or atom expression raises an error for the whole list;
the importer does not substitute zero coordinates or omit unresolved atoms.
Supply explicit coordinates or import a structure file with the geometry.

Atom constructors accept positional arguments, named `Z` and `xyz` arguments,
or a mixture: `Atom(8, [1, 2, 3])`, `Atom(Z=8, xyz=[1, 2, 3])` and
`Atom(8, xyz=[1, 2, 3])` import the same oxygen position in bohr. This also
applies to module-qualified calls such as `vq.Atom(...)` and atoms built in
supported comprehensions, for both molecules and periodic unit cells.

## Queue

| Command | Needs | What it does |
|---|---|---|
| `from-vq JOB...` | `[viewer]`, `vq` | Fetch finished jobs' archives into `-o DIR` (`./vq-fetched` by default) and open them. |

Without the extra it prints the exact install remediation and exits 1. The
queue panel inside the viewer is described in [Jobs and live
results](jobs.md), and the extra's peculiarities in
[Queue integration](queue.md).

## Configuration

```sh
vibe-view config --path      # where the file is
vibe-view config --init      # write it with the defaults
```

The file is `~/.config/vibe-view/config.toml` (`$XDG_CONFIG_HOME` is
honoured) and carries two tables:

```toml
[server]
port = 8080               # open, compare, serve, from-vq and demo --open
host = "127.0.0.1"        # localhost only
open_browser = true

[display]
size = "900x600"          # vibe-view batch / capture, unless --size is given
representation = "ball_and_stick"   # batch / capture structure images: ball_and_stick, space_filling, sticks_only, wireframe
```

Everything else the viewer remembers lives under the cache directory,
`~/.cache/vibe-view/` (`$XDG_CACHE_HOME`): the browser settings and
per-element colour overrides in `settings.json`, the recent-files list in
`recent.json`, the server log in `vibe-view.log`, the interpreter the
desktop app last launched from in `interpreter.json`, and the auto-saved
session of a viewer opened without a file in `auto-session.json`.
`uninstall.sh` leaves all of it in place. `capture`, `show`, `diff` and
`batch-compare` read the display units saved in `settings.json`, so a
figure or a report matches the browser; JSON output keeps the archive's
units.

`settings.json` must contain a UTF-8 JSON object. If it is unreadable, has
invalid JSON or text encoding, or contains a different JSON type, the viewer
and commands use their default preferences. Loading preserves the file;
saving preferences replaces it with a valid settings object.

## Exit codes

`0` on success, non-zero on failure. A missing optional dependency is a
failure with a remediation message naming the extra to install, not a
silent degradation. `capture-selftest` is non-zero when the environment
cannot render, which is what makes it usable as a healthcheck.

## Errors and debugging

A command reports what it cannot do as one line on stderr,
`Error: <reason>`, and exits with status 1. A usage mistake (an unknown
flag, a missing argument) is click's usage message with status 2.

Every command also runs inside one last boundary. An error that no command
anticipated, which would otherwise have printed a Python traceback, is
reported as `Error: <type>: <message>` (for example
`Error: ZeroDivisionError: float division by zero`), again with status 1.
Any file the command had opened is already closed at that point. Such a
line means vibe-view met something it did not expect, so it is worth a bug
report with the file that caused it.

A message longer than 2000 characters is cut there, with a note of how much
`--debug` would show. Text output, error lines included, shows control
characters from an archive as escapes such as `\x1b`, so a crafted string
cannot recolour, clear or retitle your terminal; `--json` output keeps the
original strings, escaped by JSON.

To see the full traceback instead, for that bug report or while developing,
pass `--debug` before the command or set `VIBEVIEW_DEBUG=1`:

```sh
vibe-view --debug info broken.qvf
VIBEVIEW_DEBUG=1 vibe-view info broken.qvf
```

`VIBEVIEW_DEBUG` is off when unset, empty, `0`, `false`, `no` or `off`.

With `--json`, a command that reports its errors as JSON reports this one
the same way: `info --json` prints `{"error": "<type>: <message>"}` on
stdout, like its error for a file it cannot open. Other `--json` commands
keep errors on stderr, so their stdout is JSON or empty.

Ctrl+C prints `Aborted!` and exits with status 1, and a closed output pipe
(`vibe-view info x.qvf | head -1`) ends the command quietly; neither is
reported as an error. An explicit exit status is kept.

### Selected-mode animation

The vibration controls also have CLI equivalents (mode indices are zero-based).
The example uses the ORCA water frequency dataset,
{download}`water-frequencies.qvf <examples/orca-water/water-frequencies.qvf>`, at
its source-checkout path:

```sh
vibe-view animate docs/examples/orca-water/water-frequencies.qvf -k vibration \
  --mode 6 --amplitude 1 --arrows --arrow-cutoff 0.01 \
  --arrow-color '#ffb300' --arrow-thickness 0.025 \
  --frames-per-cycle 24 --fps 12 --loop -f gif -o water-bend.gif
```

Use `-f mp4` for MP4 (requires ffmpeg; otherwise a GIF is written),
`--no-loop` for a GIF that plays once, and `--no-arrows` to omit arrows.
Arrows show positive-phase Cartesian directions, with the longest displacement
0.6 Å times `--amplitude`; the cutoff and shaft radius are in Å.


Mode-arrow tails are anchored just outside the displayed atom spheres in the
positive-phase direction, so small arrows are not hidden inside their own atoms.
This annotation offset does not enlarge the arrow vector or change the mode,
Cartesian positions, amplitude, phase or motion. Other geometry can still occlude
an arrow along a particular viewing direction; rotate the camera when needed.
