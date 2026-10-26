# Python SDK

The supported SDK covers archive inspection, validation, geometry, tables,
rendering and launch. The supported
surface is what the package root re-exports, so `from vibeview import ...`
is the whole import story:

```python
from vibeview import (
    QVFReader, QVFSource, QVFOpenError, ValidationUnavailableError,
    info, sections, has_section, validate, diff,
    get_structure, get_volume, get_table, export_xyz,
    capture_structure, capture_volume, render_terminal,
    slice_qvf, launch_qvf,
)
```

:::{important}
**`vibeview.__all__` is the API contract.** A name imported from the package
root is supported and will not move without a deprecation note in the
changelog. A name reached through a submodule (`vibeview.capture`,
`vibeview.animation`, `vibeview.file_watcher`, `vibeview.renderers.*`) is an
internal detail that can be renamed or restructured in any release. Several
pages on this site show submodule calls where the root has no equivalent;
pin the version you script against when you use one.
:::

Reading, inspecting, comparing, slicing and headless PNG capture use the
core install. PNG capture also needs a working offscreen graphics backend.
`launch_qvf` needs `[viewer]`, notebook integration needs `[jupyter]`, and
`validate` needs the optional [qvf reference validator](qvf.md#strict-validation).

For a complete script with a bundled input and expected output, follow
[the worked workflow](workflows.md#4-inspect-the-same-archive-from-python).

## Numerical conversion convention

Length and energy conversions share CODATA 2018 factors: one bohr is
0.529177210903 Å and one Hartree is 27.211386245988 eV or
627.509474 thermochemical kcal/mol. CLI, SDK and batch energy comparisons
use these same factors; printed decimal precision still depends on the output.
Live optimization uses the reciprocal bohr/Å factor for positions and gradients.
The source is the [NIST CODATA 2018 table](https://physics.nist.gov/cuu/Constants/ArchiveASCII/allascii_2018.txt).
This convention does not infer units for archive fields that omit them.

## One argument type for every function

The archive operations listed above take a `QVFSource` as their first
argument (`diff` takes two sources). `QVFSource` is a union, not a class;
its members work interchangeably:

```python
str | Path | bytes | bytearray | memoryview | IO[bytes]
```

```python
from pathlib import Path
from vibeview import info

info("water.qvf")                        # a path
info(Path("water.qvf"))
info(Path("water.qvf").read_bytes())     # raw bytes, no disk round-trip
with open("water.qvf", "rb") as fh:      # any seekable binary file object
    info(fh)
```

An open `QVFReader` is accepted anywhere a source is, which is how you avoid
reopening the archive; see [Reading a file repeatedly](#reading-a-file-repeatedly).
Functions close readers they create on return or failure. A reader or binary
stream supplied by the caller stays open, including when an operation fails.
Opening an unreadable archive raises `QVFOpenError`. Payloads are decoded
lazily, so a later read can fail even after the manifest opened successfully.
The validation API has the more detailed reporting contract below.

## Inspecting

```python
from vibeview import info, sections, has_section, validate

info("analysis.qvf")["scf_energy_eh"]
sections("analysis.qvf")
# [{'id': 'structure', 'kind': 'structure'},
#  {'id': 'atom_properties', 'kind': 'atom_properties'}, ...]
has_section("analysis.qvf", "scf_history")     # True
validate("analysis.qvf")
# {'valid': True, 'n_sections': 4, 'n_members': 5}
```

`validate` uses the optional qvf reference validator; see
[installation and strict validation](qvf.md#strict-validation). It returns
`valid`, `errors`, `warnings` and structured `findings` with qvf's exact codes
and locations. Most validation failures return `valid: False` and `error`.
An unreadable ZIP or missing/unreadable manifest instead raises
`QVFOpenError`, with the reference report in `exc.validation_report`. Missing
validator support raises `ValidationUnavailableError` (a `QVFError`) with an
install hint, because nothing was checked.
A subsequent viewer-readability failure also returns `valid: False` with
`reader_error`, separate from qvf findings. Hash/layout diagnostics retain `section`, `member` and `reason` where
available. Success includes `n_sections` and `n_members`; the example above
shows those summary fields only.

Pass resource-policy overrides as `validate(source, limits={"max_json_depth": 32})`.
These use qvf's own limits, before payload decoding. Paths, bytes, streams
and open readers are supported; borrowed streams/readers stay open and retain
their stream position. Reader validation checks the original stored archive,
independently of unsaved edits. Normal reading stays lazy.

Handle dependency failures, unreadable containers and completed reports
separately when validation is a prerequisite for further work:

```python
from vibeview import QVFOpenError, ValidationUnavailableError, validate

try:
    report = validate("analysis.qvf")
except ValidationUnavailableError as exc:
    raise SystemExit(f"Validation unavailable: {exc}") from exc
except QVFOpenError as exc:
    # exc.validation_report retains the reference findings when available.
    raise SystemExit(f"Cannot open archive: {exc}") from exc

if not report["valid"]:
    raise SystemExit(report["error"])
for warning in report["warnings"]:
    print(warning)
```

A nonempty report dictionary is truthy even when `valid` is false. Check the
field explicitly. Warnings can accompany a valid archive; retain them when
recording a validation result.

## The structure

```python
from vibeview import get_structure, export_xyz

get_structure("water.qvf")
# {'atoms': [{'symbol': 'O', 'atomic_number': 8, 'position': [0.0, 0.0, 0.1173]}, ...],
#  'pbc': [False, False, False]}
print(export_xyz("water.qvf"))     # the structure as an XYZ string
```

A periodic system also carries `lattice_vectors`, and `pbc` reports which
axes are periodic.

An archive can hold several structures under any section ids. Both functions
take the archive's **first structure** (the first one
`viewer_defaults.auto_open` lists, else the first structure section; QVF spec
§ 4.1), and `get_structure(source, "structure_m2")` or
`export_xyz(source, "structure_m2")` another. On a reader, `first_structure_id`
names the first structure, `structure_sections` lists them all,
`show_structure(id)` changes the one `read_structure()` returns without an
id, and `structure_identity(id)` gives the label, name, charge, multiplicity
and line notations a structure section states (§ 5.1).

## Volumes: summary or voxels

`get_volume` returns a JSON-serializable summary, the grid geometry and the
value range, not the voxels, and returns `None` rather than raising when the
section is absent or is not a volume, so it doubles as a probe:

```python
from vibeview import get_volume

get_volume("density.qvf", "rho")
# {'kind': 'volume.density', 'origin': [...], 'voxel_vectors': [[...], ...],
#  'shape': [6, 6, 6], 'data_min': 0.0027, 'data_max': 0.9972}
```

For a scalar volume, the value range describes the stored real array.
Legacy complex-dtype scalar payloads are reduced by the section's `component`
label for display; that compatibility path does not make them conforming QVF.
The portable `volume.complex` kind instead stores real/imaginary pairs in a
real-valued array, as described below.

For a `volume.vector` section, `data_shape` is `[nx, ny, nz, 3]`,
`data_min` and `data_max` are the range of |v|, and `unit` is its declared
unit or `None`; `read_volume_vector` returns the grid and the vectors. For
a `volume.complex` section, `data_shape` is `[nx, ny, nz, 2]` and the range
is that of |ψ|; `read_volume_complex` returns the grid and a complex array.
For a `volume.series` section, `data_shape` is `[n, nx, ny, nz]`, the range
covers every slab, and `axis` gives its `kind`, `values`, `units`, `labels`
and `trajectory_ref`; `read_volume_series` returns the grid, the slabs and
the axis.

For the array itself, go through a reader:

```python
from vibeview import QVFReader

with QVFReader("density.qvf") as r:
    grid = r.read_volume_grid("rho")     # origin, voxel_vectors, shape
    data = r.read_volume_data("rho")     # an ndarray in the stored dtype (float32 or float64)
```

## Tables

`get_table` returns `(headers, rows)`, the same data `vibe-view table`
prints, ready for `csv.writer` or a DataFrame:

```python
from vibeview import get_table

headers, rows = get_table("analysis.qvf", "atom_properties")
```

| Kind | Table contents |
|---|---|
| `vibrations` | Mode numbers, frequencies and available per-mode quantities, with units in the column names |
| `atom_properties` | Per-atom charges and available spin populations |
| `wavefunction.gto` | Molecular orbital energies and occupations |
| `wavefunction.bloch` | Orbital energies and occupations at each k-point |
| `excited_states` | State energies in eV and nm, strengths, symmetry, contributions and linked section IDs |
| `forces` | Per-atom forces, F = −∇E, in hartree/bohr |
| `hessian` | Harmonic frequencies derived from the mass-weighted Hessian |
| `properties.electric`, `properties.elastic`, `properties.dielectric` | Quantities, components, values and units |

`get_table` takes a **kind**, not a section ID, and selects the first section
of that kind in archive order. It does not concatenate multiple charge or
wavefunction sections. To inspect a particular section among several of the
same kind, use its ID with the corresponding typed reader method below.
An unsupported or absent kind raises `ValueError`. Missing values are `None`.
These are presentation tables; use typed readers for the underlying numerical
data when displayed rounding is unsuitable for analysis.

Write a CSV with the standard library:

```python
import csv
from pathlib import Path
from vibeview import get_table

headers, rows = get_table("analysis.qvf", "atom_properties")
with Path("charges.csv").open("w", encoding="utf-8", newline="") as output:
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
```

CSV writes `None` as an empty cell. If a downstream tool must distinguish
missing values from empty strings, use JSON, which preserves them as `null`.

## Comparing

```python
from vibeview import diff

diff("hf.qvf", "pbe.qvf")
# {'energy_a_eh': ..., 'energy_b_eh': ..., 'delta_e_eh': ..., 'delta_e_kcal_mol': ...,
#  'delta_e_ev': ..., 'geo_rmsd_a': ..., 'geo_rmsd_skipped': None,
#  'n_sections_a': ..., 'n_sections_b': ...,
#  'kinds_only_a': [], 'kinds_only_b': [], 'kinds_common': ['structure'],
#  'converged_a': ..., 'converged_b': ...}
```

Every numeric field is `None` when the inputs cannot support it. Two
structure-only files give `delta_e_eh: None` rather than a spurious zero, so
test for `None` before arithmetic. When `geo_rmsd_a` is `None`,
`geo_rmsd_skipped` says why: a structure that cannot be read (with the
reader's error), an empty structure or atom counts that differ.

Energy deltas are **A − B**: `diff("hf.qvf", "pbe.qvf")` subtracts PBE from
HF. The SDK's `geo_rmsd_a` is in ångström and uses the coordinates as stored,
paired by atom index, **without superposition**. It does not check that the
elements at corresponding indices match. Translation or rotation alone can
therefore produce a nonzero RMSD, and equal atom counts do not establish a
chemically meaningful comparison. Verify the atom order before interpreting
it; use an explicit alignment when you want to remove rigid motion.

For scripted superposition, the version-specific `vibeview.align` submodule
provides `align(P, Q)` for index-corresponded coordinates and
`align_by_species(p_symbols, P, q_symbols, Q)` for element-based pairing.
When the element sequences match by index, `align_by_species` compares the
indexed fit with its nearest-neighbour element pairing and keeps the lower
RMSD. Rotating a same-order structure therefore preserves an exact fit.
Results contain the proper rotation, translation, before/after RMSDs and
transformed coordinates in the original `P` row order. Element pairing for
permuted structures remains a heuristic; symmetric arrangements can be
ambiguous. The CLI and browser comparison paths use index correspondence.

## Rendering

```python
from vibeview import capture_structure, capture_volume, render_terminal

# PNG, offscreen. No display, but VTK wants a GL context; set PYVISTA_OFF_SCREEN=True.
capture_structure("h2o.qvf", "structure.png", representation="space_filling")
capture_volume("h2o.qvf", "vol_homo", "homo.png", isovalue=0.03, colormap="RdBu")

# The same renderers as text. No GL context at all, so this one works anywhere.
print(render_terminal("h2o.qvf", size=(80, 24)))
print(render_terminal("h2o.qvf", "vol_mo_3", isovalue=0.03))
from pathlib import Path
Path("frame.txt").write_text(render_terminal("h2o.qvf", plain=True), encoding="utf-8")
```

`capture_structure` takes `size`, `representation`, `show_labels`,
`replication` and `publication`; `capture_volume` takes `isovalue`,
`colormap`, `opacity`, `size`, `replication`, `render` (`"auto"`, the
section's hint; `"isosurface"`; or `"volume"`, the browser's volume mode)
and `transfer_function`, a dictionary in the hint’s JSON shape that overrides
the section's in volume mode. Both return `True` on success; check the
return value before treating the output as a completed figure. A missing
structure can return `False`, and an unreadable or undrawable volume returns
`False`; archive-opening and other failures can still raise exceptions.
`render_terminal` takes the `show` flags as keywords and returns an explanation rather than
raising for a section with no graphical form. The chart and table captures,
the animation renderers and the on-demand orbital evaluator are in
[Figures without a display](headless.md).

## Slicing

```python
from vibeview import slice_qvf

out = slice_qvf("analysis.qvf", "structure_only.qvf", keep=["structure"])
slice_qvf("analysis.qvf", "no-volumes.qvf", drop=["volume.density"])
```

Slicing preserves scientific metadata and kept member bytes. It prunes viewer
hints for removed sections and refuses to drop a section still referenced by a
kept section. The output is validated before it replaces the destination;
`keep` and `drop` are mutually exclusive.


`keep` and `drop` take section ids or kinds, and the function returns the
output path. This is the cheap way to hand a colleague a 2 MB structure out
of a 400 MB archive.

## Reading a file repeatedly

Each module-level call opens and closes the archive. For several calls
against one file, open a `QVFReader` once and pass **it** as the source:

```python
from vibeview import QVFReader, info, get_structure, export_xyz

with QVFReader("analysis.qvf") as r:
    meta = info(r)
    geom = get_structure(r)
    xyz = export_xyz(r)
```

The reader is also the lower-level surface, with one `read_*` method per
section kind: `read_structure`, `read_bonds`, `read_volume_grid`,
`read_volume_data`, `read_volume_vector`, `read_volume_complex`, `read_volume_series`, `read_forces`, `read_hessian`, `read_properties`, `read_wavefunction_bloch`, `read_bands`, `read_bands_projections`, `read_dos`,
`read_fermi_surface`, `read_phonon_bands`, `read_phonon_dos`,
`read_equation_of_state`, `read_spectra`, `read_excited_states`,
`read_trajectory`, `read_vibrations`, `read_wavefunction_gto`,
`read_reaction_path`, `read_reaction_waypoints`, `read_scan_surface`,
`read_atom_properties`, `read_nmr`, `read_epr`, `read_symmetry`,
`read_scf_history`, `read_citations`, `read_run_record`, `read_job_spec`,
`read_bond_orders`, `read_topology_qtaim`, `read_dos_coop`,
`read_reaction_scheme` and `read_preview`. Each returns a typed data object
and takes the **section id**, not the kind. Ids often match the kind in
small files, but not always; take them from `r.sections`, which on the
reader is a property returning rich `Section` objects rather than the plain
dicts the module-level `sections()` returns.

```python
with QVFReader("analysis.qvf") as r:
    [s.id for s in r.sections]
    r.read_scf_history("scf_history")     # SCFHistoryData(iterations=[...])
    r.read_bond_orders("bond_orders")     # BondOrdersData(method='mayer', pairs=[...])
    r.source.program, r.source.version    # who wrote it
```

For data no typed reader covers, such as a vendor section or a field the
typed object leaves out, read one member directly:
`read_json_member(section_id, member)`, `read_binary_member` (an array) and
`read_text_member` check the member against the manifest's SHA-256 first.
`read_run_record_attachment(section_id, role)` decodes an attached log or
input of a `run.record` as text, and `read_run_record_attachment_bytes`
returns its bytes. `read_member_bytes(path)` returns a zip entry's raw bytes
without that check.

Without the context manager, call `r.close()` yourself. A reader you pass
into any SDK function stays open; the SDK closes only readers it opened.

## Launching the viewer

```python
from vibeview import launch_qvf

launch_qvf("water.qvf", host="127.0.0.1", port=8080, open_browser=True,
           print_banner_to_stdout=True)
```

It boots the server and blocks until it stops. It takes any `QVFSource` or
an open reader, so an archive built in memory never has to touch disk. It closes
readers it creates when the server stops or fails; supplied readers stay open:

```python
import io
launch_qvf(io.BytesIO(qvf_bytes), open_browser=False)
```

Set `open_browser=False` on a remote host and forward the port.

## Jupyter

Needs the `[jupyter]` extra in the environment running the **kernel**.

```python
%load_ext vibeview.jupyter
%vibeview calculation.qvf                          # structure image, section list, provenance
%vibeview calculation.qvf --table atom_properties  # a charges table
%vibeview calculation.qvf --mo                     # the orbital table
%vibeview calculation.qvf --scf                    # SCF convergence chart
%vibeview calculation.qvf --bands                  # band structure
%vibeview calculation.qvf --capture density        # a density isosurface image
```

`%vibeview --help` lists the options. `vibeview.jupyter.qvf_to_image`
returns a structure PNG as bytes for `IPython.display.Image`, rendered
exactly like `vibe-view capture` output,
`qvf_to_html` an embeddable snippet, and `export_notebook_cell` generates a
starter cell for an archive. For the full interactive viewer from a
notebook, `launch_qvf(..., open_browser=False)` and connect to the port.

## Release codename

The codename catalogue is importable, which is what the CLI, the About boxes
and this site read:

```python
from vibeview.codenames import codename_for_version, version_label

codename_for_version("{{release}}")
version_label()                   # "<version> — <codename>", or the bare version
```

See [Release codenames](codenames.md).

## Extending vibe-view

vibe-view has two extension points, both Python entry-point groups that an
installed package declares in its `pyproject.toml`:

| Group | A plugin adds | Contract module | Documented under |
|---|---|---|---|
| `vibeview.importers` | A file format `open` and `import` read | `vibeview.importers` | [Writing an importer plugin](formats.md#writing-an-importer-plugin) |
| `vibeview.renderers` | A section kind the viewer renders | `vibeview.renderer_plugins` | [Writing a renderer plugin](#writing-a-renderer-plugin) below |

**Compatibility.** Each group has an API version, `IMPORTER_API_VERSION` and
`RENDERER_API_VERSION`, both 1. Throughout vibe-view 3.x a plugin written
against version 1 keeps working: the spec fields keep their meaning, new ones
arrive only with defaults, and the calls vibe-view makes into a plugin keep
their arguments. A change that would break a version 1 plugin needs a new API
version and a new major release, and vibe-view refuses a spec whose version it
does not support, by name, rather than misreading it. Everything outside the
two contracts, `vibeview.app` and the built-in renderers included, stays
internal and may move in any release. Representations and 3D actors are not
an extension point yet.

Failures stay contained. A plugin that does not import, raises while loading
or breaks the contract is reported with its error and ignored; the built-ins
and the other plugins work as before. Two packages claiming one importer name
or one section kind both fail closed, so the outcome never depends on install
order. `vibe-view formats` lists importer plugins and `vibe-view doctor` lists
renderer plugins, each with its error if it has one.

What you can also do is compose: the functions on this page are ordinary
Python, so a script that walks a directory, validates each archive, captures a
PNG and writes a summary CSV needs no plugin at all.

### Writing a renderer plugin

A renderer plugin renders one section kind, usually a vendor kind in the
`x_<vendor>.` namespace that vibe-view would otherwise list as skipped. It
declares one entry point in the `vibeview.renderers` group, named after the
kind:

```toml
[project.entry-points."vibeview.renderers"]
"x_example.timings" = "vibeview_example_renderer:renderer"
```

The entry point resolves to a `RendererSpec`, or to a zero-argument factory
that returns one:

```python
import html

from vibeview.renderer_plugins import RENDERER_API_VERSION, RendererSpec


class TimingsRenderer:
    def __init__(self, section, reader):
        self.section = section
        self.reader = reader

    def load(self):
        return self.reader.read_json_member(self.section.id, "steps")

    def render_to_html(self):
        rows = "".join(
            f"<tr><td>{html.escape(str(s['step']))}</td><td>{s['seconds']:.3f}</td></tr>"
            for s in self.load()
        )
        return f"<!doctype html><table>{rows}</table>"


def renderer():
    return RendererSpec(
        kind="x_example.timings",
        title="Step Timings",
        renderer=TimingsRenderer,
        icon="mdi-timer-outline",
        api_version=RENDERER_API_VERSION,
    )
```

* `kind` must equal the entry-point name, contain no whitespace, and not be
  a kind vibe-view renders itself.
* `title` names the section in the sidebar and heads its panel; `icon` is a
  [Material Design Icons](https://pictogrammers.com/library/mdi/) name
  starting with `mdi-`, and defaults to `mdi-puzzle-outline`.
* vibe-view constructs the renderer class as `renderer(section, reader)` and
  calls `render_to_html()` with no arguments when the section is opened.
  `load()` returns the section's data and is part of the contract so other
  surfaces can use it later. Subclassing `vibeview.renderers.BaseRenderer` is
  optional.
* A renderer reads its section through `section.id`, `section.kind`,
  `section.members` and `section.model_extra`, and through the reader's
  verified member methods `read_json_member`, `read_text_member` and
  `read_binary_member`, which check the manifest's SHA-256 before returning.
* `render_to_html()` returns a complete HTML document. The details panel
  shows it in a sandboxed frame with scripts disabled, so use static HTML and
  CSS, and escape any text that comes from the archive. Whatever the renderer
  raises, or a return value that is not a string, leaves the panel empty and
  names the plugin in the status line.

With the plugin installed, the kind is rendered everywhere vibe-view reports a
section: the browser lists it with the plugin's title and icon and opens its
panel, `vibe-view info` shows it as `rendered` with the detail `via plugin`,
the start-up banner counts it, and a section of that kind marked `critical`
no longer blocks the open. The terminal cannot show the plugin's HTML, so it
lists the section as skipped and shows its generic pane, which names the
status.

A complete sample package lives in
[`docs/examples/renderer-plugin`](https://github.com/vibe-qc/vibe-view/tree/main/docs/examples/renderer-plugin):
the renderer above with validation, its `pyproject.toml`, and a script that
writes a water archive carrying the section. The candidate pipeline installs
it and checks `info`, the browser panel and the terminal fallback against it.

```sh
pip install docs/examples/renderer-plugin
python docs/examples/renderer-plugin/make_archive.py timings.qvf
vibe-view info timings.qvf
vibe-view open timings.qvf
```

## Full surface

| Name | Purpose | Extra |
|---|---|---|
| `QVFReader` | Open once, read many sections | |
| `QVFSource` | The accepted source union (a type, not a class) | |
| `QVFOpenError` | Raised when a file cannot be opened | |
| `ValidationUnavailableError` | Raised by `validate` when the reference validator is not installed | |
| `info` | Full metadata dict | |
| `sections` | `{id, kind}` for every section | |
| `has_section` | Test one section id | |
| `validate` | Strict qvf reference validation | `validation` extra |
| `get_structure` | Atoms, `pbc`, `lattice_vectors` | |
| `export_xyz` | Structure as an XYZ string | |
| `get_volume` | Grid geometry and value range, no voxels | |
| `get_table` | `(headers, rows)` for tabulatable kinds | |
| `diff` | Energy delta, section overlap, geometry RMSD | |
| `capture_structure` | Structure to PNG, headless | |
| `capture_volume` | Volume to PNG, headless | |
| `render_terminal` | Section to braille text | |
| `slice_qvf` | Subset of sections to a new archive | |
| `launch_qvf` | The interactive browser viewer | `[viewer]` |
| `__version__` | The installed vibe-view version | |

## Specialised modules

The functions above are the stable top-level surface. The browser's building,
inspection and figure cards are built on modules that can also be imported
directly. Each is documented in its chapter, and
{download}`tour.py <examples/python-tour/tour.py>` runs most of them on one
structure ([Build, inspect and present](tutorial_build_inspect.md#7-the-same-steps-in-python)).

| Module | What it provides | Chapter |
|---|---|---|
| `vibeview.api`: `set_geometry_parameter`, `set_bond`, `export_geometry_qvf` | Exact distances, angles, dihedrals and bond orders on an open reader, saved as QVF | [Building and editing](editing.md#edit-bonds-explicitly) |
| `vibeview.molecular_builder`, `peptide_builder`, `coordination_builder` | Bond rotation, conformers, peptides, coordination templates (RDKit for all but rotation) | [Molecular construction](molecular_builder.md) |
| `vibeview.zmatrix`, `coordinate_tables` | Cartesian tables and Z-matrices with Gaussian, ORCA and MOPAC blocks | [Internal coordinates](internal_coordinates.md) |
| `vibeview.molecular_selection` | The atom selection language: `select`, `within`, `positions` | [Atom selection language](selection_language.md) |
| `vibeview.selection_measurements` | Centroids, principal axes, best-fit planes, plane angles and RMSD | [Measurements in Python](selection_language.md#measurements-in-python) |
| `vibeview.molecular_surfaces`, `interactions` | VdW, SAS and SES surfaces with area and volume; geometric interaction tables | [Molecular surfaces and interactions](molecular_inspection.md#python-use) |
| `vibeview.experimental_map` | Memory-mapped MRC/CCP4 maps, sigma levels and zone contours | [Experimental maps](experimental_maps.md) |
| `vibeview.model_scene`, `scene_commands` | Multi-model scenes, alignment, replayable scripts and the scene command line | [Model scenes](model_scenes.md) |
| `vibeview.morph_movie` | Morph movies between two structures | [Publication figures](publication_figures.md#morph-export) |
| `vibeview.spectrum`: `broaden_lines` | Unit-area Gaussian and Lorentzian broadening | [Explore electronic spectra](orca.md#5-explore-electronic-spectra-and-mcd) |
| `vibeview.input_writers`: `write_input` | Native inputs for Gaussian, Psi4, NWChem, Q-Chem, GAMESS, CP2K, xtb and VASP | [Preparing native calculation inputs](input_writers.md#sdk) |

These modules follow the same units as the viewer (Å, degrees) and refuse
ambiguous input rather than guessing; their chapters state each function's
indexing (selection expressions are one-based, Python indices zero-based).

## API reference

```{eval-rst}
.. autosummary::
   :toctree: api
   :recursive:

   vibeview.api
   vibeview.qvf
   vibeview.kinds
   vibeview.importers
   vibeview.renderer_plugins
   vibeview.codenames
```
