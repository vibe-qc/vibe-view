# Figures without a display

Everything on this page is the **core install**: no `[viewer]` extra, no
browser, and no display server. It is what a CI job, a batch pipeline or a
figure script for a paper runs, and every renderer is the one the browser
uses, so a figure captured on a compute node matches what the viewer shows.

One caveat separates the two halves of the page. PNG capture, video and the
scene exporters render through VTK, which needs an offscreen OpenGL context
even with no display attached. Terminal rendering and the vector and data
exporters need nothing at all.

## Setting up a headless host

```sh
vibe-view capture-selftest
```

renders a tiny built-in structure through the real capture path, checks the
PNG is not blank, and reports the render backend. Exit 0 means capture works
here; non-zero means the GL stack is missing, and the message says what it
found. Run it before the first real capture on a new host, and as a
healthcheck in a queue or CI job.

On a bare Linux container the usual fixes are a Mesa runtime and a virtual
framebuffer:

```sh
apt-get install libgl1 libglx-mesa0 libxrender1 libxext6 libsm6 libglib2.0-0 xvfb xauth
PYVISTA_OFF_SCREEN=True xvfb-run -a vibe-view capture job.qvf -o structure.png
```

`PYVISTA_OFF_SCREEN=True` tells PyVista not to look for a window; set it in
the environment of any script that captures. If a GL context is impossible
on a host, `vibe-view show` and `render_terminal` still work, because they
never touch one.

## `vibe-view capture`

```sh
vibe-view capture job.qvf -o structure.png                     # the structure
vibe-view capture job.qvf -s vol_dens_0 -o density.png         # a volume section
vibe-view capture job.qvf -s vol_mo_0 --isovalue 0.03 --colormap RdBu -o homo.png
vibe-view capture job.qvf -s bands -o bands.png --size 1600x1000
```

`-s` picks any section; volumes take `--isovalue` and `--colormap`, and
`--size WxH` sets the image size. The default is `900x600`, and can be
changed in the [configuration file](cli.md#configuration).

`capture` renders **stored** sections. It has no flag for evaluating an
orbital from a `wavefunction.gto` section on demand; the
[Python API](#the-capture-api) can do that.

## `vibe-view batch`

```sh
vibe-view batch runs/*.qvf                       # one structure PNG per archive
vibe-view batch runs/*.qvf --volumes -o gallery/ # plus one PNG per volume section
vibe-view batch a.qvf --size 1200x800
```

Renders a gallery from many archives with the same view defaults, one PNG
per section, into `./vibe-view-gallery` unless `-o` says otherwise. No
server is started. `vibe-view dashboard PATTERNS...` builds a grid preview
of many files instead, as one image or, with `--html`, one page.

## `vibe-view animate`

```sh
vibe-view animate opt.qvf                              # auto-detect: trajectory, reaction path, vibrations or orbitals
vibe-view animate neb.qvf --kind reaction              # reactant → transition state → product
vibe-view animate vib.qvf --kind vibration --mode 7    # one normal mode, 0-based
vibe-view animate opt.qvf -k trajectory --fps 8 --format gif
vibe-view animate si.qvf  --kind turntable -o rotation.mp4
```

MP4 needs `ffmpeg` on `PATH`; without it the command falls back to GIF when
Pillow is installed. `--format frames` writes the individual PNGs instead.
`--kind auto`, the default, takes the first of `trajectory`,
`reaction.path`, `vibrations` or `wavefunction.gto` it finds;
`--kind orbital` sweeps every orbital of a wavefunction at `--isovalue`.

## Export

```sh
vibe-view export job.qvf -f xyz -o geometry.xyz
vibe-view export job.qvf -f html -o viewer.html
vibe-view export job.qvf -f pov -o scene.pov
```

Twelve formats, none of which needs a display:

| `-f` | Writes | Notes |
|---|---|---|
| `xyz`, `cif`, `cml`, `json` | The structure | CIF carries fractional coordinates and the cell; a molecule is wrapped in a padded box. |
| `py` | A vibe-qc input script | Reuses the archive's own provenance (method, basis, functional) so the script reproduces the calculation, and carries the lattice so a periodic file exports a periodic system. |
| `obj`, `gltf` | A mesh of atoms and bonds | glTF 2.0 with PBR materials, for any 3-D tool. |
| `pov` | A POV-Ray scene | CPK spheres and bond cylinders with finish blocks, three-point studio lighting, the cell wireframe for a crystal. Render with `povray +W3840 +H2160 +A +Q11 scene.pov`. |
| `blend` | A Blender Python script | Builds the scene when run with `blender --python scene.py`: Cycles, principled materials, three area lights, an auto-framed camera, a transparent film. `blender --background --python scene.py -o //out.png -f 1` renders it headlessly. |
| `html` | A standalone page | A self-contained 3-D viewer of the structure, rendered with Three.js from a CDN. No server, no Python: open it in any browser, or attach it to an email. |
| `svg` | Vector graphics | CPK circles and bond lines, for journals that want line art. |
| `pdf` | A vector PDF | For `\includegraphics`. |

The browser's *Export geometry / scene* menu offers the same formats. The
interactive **High-Quality Raytrace Render** is browser-only; from a script,
`vibeview.raytrace.RaytraceEngine` drives the same OSPRay path tracer when
the installed VTK ships it.

## Tables and data

```sh
vibe-view table job.qvf                                  # what is tabulatable
vibe-view table job.qvf --kind atom_properties --format csv > charges.csv
vibe-view table job.qvf --kind wavefunction.gto --format json
vibe-view info  job.qvf --json
```

Three kinds are tabulatable: `vibrations`, `atom_properties` and
`wavefunction.gto`. See the [CLI reference](cli.md) for `diff`,
`batch-compare` and `stats`, and the [Python API](python_api.md) for the
same data as Python objects.

## The capture API

Every function on the public SDK takes a path, bytes, a file object or an
open reader, and returns `True` on success:

```python
from vibeview import capture_structure, capture_volume

capture_structure("job.qvf", "structure.png",
                  representation="space_filling", show_labels=True, replication=(2, 2, 1))
capture_volume("job.qvf", "vol_dens_0", "density.png",
               isovalue=0.05, colormap="viridis", opacity=0.6, size=(1600, 1000))
```

The lower-level module covers the chart and table kinds too. These take an
open `QVFReader` and auto-detect the section when none is named:

| Function | Output |
|---|---|
| `capture_structure(reader, path, ...)` | PNG |
| `capture_volume(reader, section_id, path, ...)` | PNG |
| `capture_bands(reader, path)` | PNG |
| `capture_dos(reader, path)` | HTML |
| `capture_spectra(reader, path)` | HTML |
| `capture_scf_history(reader, path)` | HTML |
| `capture_bond_orders(reader, path)` | HTML |
| `capture_energy_diagram(reader, path)` | HTML |
| `capture_selftest(output=None, size=(400, 300))` | a dict describing the render backend |

```python
from vibeview import QVFReader
from vibeview.capture import capture_bands, capture_dos, capture_scf_history

with QVFReader("crystal.qvf") as r:
    capture_bands(r, "bands.png")
    capture_dos(r, "dos.html")
    capture_scf_history(r, "scf.html")
```

The chart kinds other than bands write interactive HTML through Plotly; for
a PNG of one of those, save the figure with matplotlib from your own script,
or screenshot the page.

To render an orbital that is not stored as a grid, evaluate it first:
`vibeview.renderers.wavefunction.WavefunctionRenderer` has `evaluate_mo`,
`evaluate_density`, `evaluate_spin_density`, `evaluate_elf` and
`evaluate_nci`. That module is below the supported surface, so pin the
version you script against.

## The animation API

```python
from vibeview import QVFReader
from vibeview.animation import (
    render_trajectory_video, render_reaction_video,
    render_vibration_video, render_orbital_animation,
)

with QVFReader("neb.qvf") as r:
    render_reaction_video(r, "neb.mp4", fps=5, format="mp4")
with QVFReader("vib.qvf") as r:
    render_vibration_video(r, "mode7.gif", mode=7, n_frames=60, fps=15, format="gif")
```

Each returns the output path, or `None` when the archive has no matching
section. `render_turntable` takes a PyVista plotter rather than a reader and
orbits the camera through a full rotation.

## Recipes

**A figure set for a paper**

```sh
export PYVISTA_OFF_SCREEN=True
vibe-view capture result.qvf -o figs/structure.png --size 1800x1200
vibe-view capture result.qvf -s vol_dens_0 -o figs/density.png
vibe-view capture result.qvf -s vol_mo_0 -o figs/homo.png --colormap RdBu
vibe-view export  result.qvf -f svg -o figs/structure.svg
```

**A regression check in CI**

```sh
vibe-view validate result.qvf
vibe-view diff result.qvf reference.qvf --json | jq '.delta_e_kcal_mol'
vibe-view capture-selftest && vibe-view batch results/*.qvf -o gallery/
```

**Everything about one file, as text**

```sh
vibe-view show result.qvf --all --plain > report.txt
```

That last one needs no GL at all, so it is the one to attach to a job's log.
