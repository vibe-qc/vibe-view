# Python SDK

The supported SDK covers archive inspection, validation, geometry, tables,
rendering and launch. The supported
surface is what the package root re-exports, so `from vibeview import ...`
is the whole import story:

```python
from vibeview import (
    QVFReader, QVFSource, QVFOpenError,
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

The core install covers this entire page except `launch_qvf`, which needs
the `[viewer]` extra. Reading, inspecting, comparing, slicing and headless
PNG capture need no extra at all.

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

Every package-root function listed above takes a `QVFSource` first. It is a
union, not a class, and anything in it works interchangeably:

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
An unreadable or corrupt file raises `QVFOpenError`.

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
and locations. An unreadable or invalid archive returns `valid: False` and
`error`; missing validator support raises `QVFError` with an install hint.
A subsequent viewer-readability failure also returns `valid: False` with
`reader_error`, separate from qvf findings. Hash/layout diagnostics retain `section`, `member` and `reason` where
available. Success includes `n_sections` and `n_members`; the example above
shows those summary fields only.

Pass resource-policy overrides as `validate(source, limits={"max_json_depth": 32})`.
These use qvf's own limits, before payload decoding. Paths, bytes, streams
and open readers are supported; borrowed streams/readers stay open and retain
their stream position. Reader validation checks the original stored archive,
independently of unsaved edits. Normal reading stays lazy.

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

The value range is that of the field the viewer draws. A real array, the
only kind QVF v1 allows, is reported as stored. A complex array is reduced
by the section's `component` label first, as the browser and terminal do.

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

Four kinds are tabulatable: `vibrations`, `atom_properties`,
`wavefunction.gto` and `excited_states` (one row per state, with energies
in eV and nm, strengths, symmetry, contributions and linked section ids).
Any other kind raises `ValueError` naming the valid set.
A missing value is `None`, not a blank string.

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
reader's error) or atom counts that differ.

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
open("frame.txt", "w").write(render_terminal("h2o.qvf", plain=True))
```

`capture_structure` takes `size`, `representation`, `show_labels`,
`replication` and `publication`; `capture_volume` takes `isovalue`,
`colormap`, `opacity`, `size`, `replication`, `render` (`"auto"`, the
section's hint; `"isosurface"`; or `"volume"`, the browser's volume mode)
and `transfer_function`, a function in the hint's JSON shape that overrides
the section's in volume mode. Both return `True` on success. `render_terminal`
takes the `show` flags as keywords and returns an explanation rather than
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
`read_volume_data`, `read_bands`, `read_phonon_bands`, `read_phonon_dos`,
`read_equation_of_state`, `read_spectra`, `read_trajectory`,
`read_vibrations`, `read_wavefunction_gto`, `read_reaction_path`,
`read_reaction_waypoints`, `read_scan_surface`, `read_atom_properties`,
`read_nmr`, `read_epr`, `read_symmetry`, `read_scf_history`,
`read_citations`, `read_run_record`, `read_job_spec`, `read_bond_orders`,
`read_topology_qtaim`, `read_dos_coop`, `read_reaction_scheme` and
`read_preview`. Each returns a typed data object
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
