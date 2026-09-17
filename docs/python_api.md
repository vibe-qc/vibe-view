# Python SDK

Everything the CLI does is available programmatically. The supported
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

## One argument type for every function

Every function here takes a `QVFSource` first. It is a union, not a class,
and anything in it works interchangeably:

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

`validate` hashes every member and reports `valid: False` rather than
raising, so it is safe to run over a directory. It is the same check the
viewer applies before rendering anything: no section is drawn from bytes
that failed their own digest.

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

For the array itself, go through a reader:

```python
from vibeview import QVFReader

with QVFReader("density.qvf") as r:
    grid = r.read_volume_grid("rho")     # origin, voxel_vectors, shape
    data = r.read_volume_data("rho")     # a float32 ndarray
```

## Tables

`get_table` returns `(headers, rows)`, the same data `vibe-view table`
prints, ready for `csv.writer` or a DataFrame:

```python
from vibeview import get_table

headers, rows = get_table("analysis.qvf", "atom_properties")
```

Three kinds are tabulatable: `vibrations`, `atom_properties` and
`wavefunction.gto`. Any other kind raises `ValueError` naming the valid set.
A missing value is `None`, not a blank string.

## Comparing

```python
from vibeview import diff

diff("hf.qvf", "pbe.qvf")
# {'energy_a_eh': ..., 'energy_b_eh': ..., 'delta_e_eh': ..., 'delta_e_kcal_mol': ...,
#  'delta_e_ev': ..., 'geo_rmsd_a': ..., 'n_sections_a': ..., 'n_sections_b': ...,
#  'kinds_only_a': [], 'kinds_only_b': [], 'kinds_common': ['structure'],
#  'converged_a': ..., 'converged_b': ...}
```

Every numeric field is `None` when the inputs cannot support it. Two
structure-only files give `delta_e_eh: None` rather than a spurious zero, so
test for `None` before arithmetic.

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

`capture_structure` takes `size`, `representation`, `show_labels` and
`replication`; `capture_volume` takes `isovalue`, `colormap`, `opacity`,
`size` and `replication`. Both return `True` on success. `render_terminal`
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
`read_topology_qtaim` and `read_dos_coop`. Each returns a typed data object
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
an open reader, so an archive built in memory never has to touch disk:

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
returns a structure PNG as bytes for `IPython.display.Image`,
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

There is one extension point today: **importer plugins**, declared through
the `vibeview.importers` entry-point group and described under
[Input formats](formats.md#writing-an-importer-plugin). There is no plugin
API for renderers, representations or panels. Those seams exist inside the
codebase but are internal, undocumented and free to move.

What you can do is compose: the functions on this page are ordinary Python,
so a script that walks a directory, validates each archive, captures a PNG
and writes a summary CSV needs nothing beyond them. For most "extend
vibe-view" requests, batch rendering, custom reports, feeding another tool,
that is the whole answer, and it is stable. If you need an in-process seam
stabilized, say what you are building in an
[issue](https://github.com/vibe-qc/vibe-view/issues); which one
comes first should follow a real use case.

## Full surface

| Name | Purpose | Extra |
|---|---|---|
| `QVFReader` | Open once, read many sections | |
| `QVFSource` | The accepted source union (a type, not a class) | |
| `QVFOpenError` | Raised when a file cannot be opened | |
| `info` | Full metadata dict | |
| `sections` | `{id, kind}` for every section | |
| `has_section` | Test one section id | |
| `validate` | Schema and SHA-256 integrity check | |
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
   vibeview.codenames
```
