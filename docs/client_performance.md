# Client performance

The browser viewer renders in your browser, with vtk.js on your graphics
card, so how fast a drag feels depends on your machine more than on the
server. This page lists the machines the viewer has been measured on, the
limits a release candidate is held to, and how to measure your own.

## Which renderer your browser uses

Choose **Info** > **About vibe-view** in the toolbar. The dialog ends with a
line such as

```text
Browser graphics: WebGL2, ANGLE (Apple, ANGLE Metal Renderer: Apple M4 Pro, Unspecified Version)
```

The first part is the WebGL version; the viewer needs WebGL2 for the outline
pass of figure outlines, and works without it otherwise. The rest is the
renderer. A name with **SwiftShader**, **llvmpipe** or **Basic Render** is a
software renderer: the browser found no usable graphics card, and drawing
runs on the CPU. Everything still works, at the speeds of the software rows
below. A browser running remotely, in a virtual machine or with hardware
acceleration switched off usually reports one of these.

## What is measured

`scripts/client_qualification.py` opens the real viewer in Chromium and does
what a user does. A **drag** step is one mouse move, timed until the browser
has drawn the frame and the graphics card has finished it; a **switch** is
timed from the click until the new scene has arrived from the server and
been drawn. Every case runs at a 1280 × 800 window, a device pixel ratio of
1 and with the display's refresh cap off, so the numbers are what the
machine can do rather than its refresh rate.

| Case | What it does |
|---|---|
| `drag.structure` | Drag a 150-atom molecule in ball and stick |
| `switch.space_filling` | Switch it to space filling |
| `switch.ball_and_stick` | Switch it back |
| `drag.labels` | Drag it with atom labels on, which follow the camera on the graphics card |
| `drag.isosurface` | Drag a water density on a 64³ grid, drawn as a translucent isosurface |
| `drag.outlines` | Drag it in the *Publication* look, with the outline pass on |
| `switch.volume` | Switch the density to direct volume rendering |
| `drag.volume` | Drag the volume |

## The measured matrix

Median and 95th-percentile drag step in milliseconds, switches in seconds,
Apple measurements from 2026-10-20 and Linux measurements from the
v2.20.20 candidate on 2026-10-21:

| Case | Apple M4 Pro, Chrome 155, GPU | Apple M4 Pro, Chromium 156, SwiftShader | Linux x86-64, Chromium 156, SwiftShader |
|---|---|---|---|
| `drag.structure` | 1.2 / 1.5 ms | 10.3 / 11.0 ms | 35.0 / 42.5 ms |
| `switch.space_filling` | 0.35 s | 0.33 s | 0.45 s |
| `switch.ball_and_stick` | 0.51 s | 0.47 s | 0.90 s |
| `drag.labels` | 2.0 / 2.4 ms | 33.9 / 35.0 ms | 48.1 / 54.4 ms |
| `drag.isosurface` | 1.7 / 2.0 ms | 13.7 / 14.9 ms | 54.4 / 67.4 ms |
| `drag.outlines` | 3.1 / 3.4 ms | 18.9 / 20.0 ms | 59.4 / 65.3 ms |
| `switch.volume` | 0.36 s | 0.35 s | 0.48 s |
| `drag.volume` | 1.2 / 1.4 ms | 11.9 / 13.4 ms | 28.4 / 34.5 ms |

| Environment | Class | Status |
|---|---|---|
| Apple Silicon (M4 Pro), macOS, Chrome | GPU | Measured above; qualified |
| Apple Silicon (M4 Pro), macOS, headless Chromium | Software (SwiftShader) | Measured above; qualified; a repeat run agreed within 2 % |
| Linux x86-64, headless Chromium, the candidate pipeline | Software (SwiftShader) | Measured above; qualified on Linux 6.8 / Chromium 156. The `client-performance` job retains each candidate measurement as `test-results/client-performance.json` |
| Windows with a graphics card | GPU | Qualification deferred until requested by users; not a release gate and no PASS claimed |

The switches take the same time on both renderers because they are the
server's work: rebuilding the scene and sending it. A software renderer
shares the CPU with everything else on the machine, so its numbers rise
sharply under load; a run while a test suite used the same machine was
several times slower.

## The limits

`scripts/client_thresholds.json` holds the limits, per renderer class. A
drag is judged on its 95th-percentile step, a switch on its seconds:

| Class | Drags | Direct volume rendering | Labels | Switches |
|---|---|---|---|---|
| GPU | 16.7 ms (60 frames a second) | 33.4 ms (30 frames a second) | 16.7 ms | 10 s |
| Software | 150 ms | 75 ms | reported only | 2 s |

The GPU limits are the interactive bar every machine with a graphics card
should meet. The software limits allow about twice the measured Linux cost:
the slowest regular drag was 67.4 ms, volume was 34.5 ms and the slowest switch was
0.90 s. They catch regressions on a machine without a graphics card, where
a frame rate is not a promise; labels there are reported only,
because the extruded text of many labels is a lot of triangles for a CPU to
draw, whatever the viewer does.

Release candidates run the qualification in the `client-performance`
pipeline job, on Linux with SwiftShader. From 3.0 on it gates the candidate;
on 2.x candidates it measures and reports. A run on a machine with a
graphics card is a manual check: run the script there, as below.

## Measuring your machine

The script needs the viewer extras and Playwright:

```sh
pip install -e '.[viewer]' playwright
python -m playwright install chromium
python scripts/client_qualification.py --json my-machine.json
```

Add `--channel chrome` to use an installed Google Chrome instead of
Playwright's Chromium; headless Chromium often falls back to SwiftShader
even where a graphics card exists, and the renderer line it prints first
says which one ran. `--gate` makes the exit status 1 when a case is over its
limit. To add your machine to the matrix, open an issue with the JSON file
and the output of `vibe-view doctor`.

## Large structures

From 500 atoms the viewer draws atoms and bonds as instanced glyphs: one
sphere and one cylinder mesh that the graphics card repeats for every atom
and bond, in the browser as in headless capture. From 10 000 atoms the
browser draws coarse glyphs while the camera moves and the full ones when
it stops ([Nanotubes and tube–metal interfaces](nanotubes.md)).

`docs/bench_nanotube_scale.py` measures four carbon nanotube scenes: an
open (10,10) tube, a five-wall tube on a (5,5) core, a 19-tube bundle and
an aligned forest of three-wall tubes 5 nm apart. It times each from 10³ to
10⁶ atoms in the three front ends, every case in its own process with a
10-minute limit:

- **load**: from opening the archive until the structure is drawn. In the
  browser this runs from starting the server, which binds after its first
  scene build, until vtk.js has drawn it;
- **orbit**: one camera step. In the browser, one drag step until drawn
  (median / 95th percentile); in the terminal, one re-raster of a
  160 × 48 braille pane;
- **capture**: one image at the 1280 × 800 window. In the browser this is
  the screenshot export; headless, a PNG of an off-screen render. That
  render and its readback are the headless frame cost: an off-screen orbit
  step only queues work on the graphics card, so it is not reported.

Measured 2026-10-28 on an Apple M5 Max (macOS, installed Google Chrome,
ANGLE Metal), with the viewer from 3.0.x `main` before and after instancing.

### Before instancing

Every sphere and bond cylinder was a separate mesh merged on the server,
and building the bonds grew faster than the atom count:

| Scene | Atoms | Headless load | Browser load | Browser orbit |
|---|---:|---:|---:|---:|
| Tube | 1 000 | 1.9 s | 4.8 s | 0.6 / 0.9 ms |
| Tube | 10 000 | 116 s | 156 s | 1.1 / 1.5 ms |
| Multi-wall | 10 200 | 115 s | 190 s | 1.1 / 1.4 ms |
| Bundle | 10 640 | 141 s | 180 s | 1.2 / 2.3 ms |
| Forest | 10 800 | 176 s | 213 s | 1.2 / 1.6 ms |

The practical ceiling was about 10⁴ atoms: two to three and a half minutes
to open. 10⁵ atoms was not attempted.

### With instancing

| Scene | Atoms | Headless load / capture | Terminal load / orbit | Browser load | Browser orbit |
|---|---:|---:|---:|---:|---:|
| Tube | 1 000 | 1.7 s / 0.03 s | 0.2 s / 29 ms | 7.0 s | 2.4 / 5.8 ms |
| Tube | 10 000 | 1.4 s / 0.2 s | 0.6 s / 24 ms | 4.6 s | 4.9 / 8.3 ms |
| Tube | 100 000 | 5.4 s / 1.6 s | 4.2 s / 40 ms | 12 s | 2.4 / 8.6 ms |
| Tube | 1 000 000 | 55 s / 2.4 s | 73 s / 815 ms | 93 s | 15 / 21 ms |
| Multi-wall | 10 200 | 1.1 s / 0.3 s | 0.7 s / 148 ms | 4.5 s | 18 / 68 ms |
| Multi-wall | 100 200 | 4.8 s / 4.6 s | 4.2 s / 141 ms | 11 s | 2.2 / 8.0 ms |
| Multi-wall | 1 000 200 | 57 s / 1.6 s | 75 s / 958 ms | 107 s | 12 / 26 ms |
| Bundle | 10 640 | 1.9 s / 0.2 s | 1.1 s / 172 ms | 7.4 s | 9.7 / 13 ms |
| Bundle | 100 320 | 8.5 s / 1.5 s | 5.9 s / 473 ms | 11 s | 2.6 / 7.7 ms |
| Bundle | 1 000 160 | 68 s / 3.0 s | 76 s / 979 ms | 129 s | 9.6 / 14 ms |
| Forest | 10 800 | 1.8 s / 0.3 s | 1.0 s / 77 ms | 7.0 s | 10 / 14 ms |
| Forest | 108 000 | 9.3 s / 1.6 s | 9.0 s / 105 ms | 22 s | 4.4 / 8.8 ms |
| Forest | 1 008 000 | 86 s / 9.7 s | 81 s / 1019 ms | 132 s | 26 / 204 ms |

The ceilings this gives, on this machine:

- **Browser**: every scene up to 10⁶ atoms opens and turns. A drag step stays
  under 30 ms at the median; the 10⁶-atom forest has slow outliers (95th
  percentile 204 ms). Opening 10⁶ atoms takes 1.5 to 2.2 minutes, three
  quarters of it the server's scene build and transfer. Between 10⁴ and
  10⁵ atoms, below the coarse-glyph threshold, the 95th percentile can
  reach 68 ms. The full-quality frame after a drag of a 2 × 10⁵-atom forest
  took about 1 s.
- **Headless**: 10⁶ atoms load in about a minute and capture in 2 to 10 s.
- **Terminal**: the braille rasterizer runs on the CPU and draws every
  atom. Turning stays under about 150 ms a step to 10⁵ atoms for a single
  tube and to about 10⁴ for denser scenes; at 10⁶ atoms each step takes
  about a second. It loads 10⁶ atoms in about 75 s.

The browser numbers depend on the graphics card; a software renderer is
much slower at these sizes and has not been measured.

### Synthetic trajectory diagnostic

Use `--scenes trajectory --sizes 100000 --frontends browser --channel chrome`
to measure twelve synthetic breathing frames of a five-wall tube. `playback`
reports six manual frame updates, including server inference, transfer and the
browser draw. It waits for the filmstrip before measuring. Coordinates are
synthetic and this is not a chemistry calculation.

On 2026-10-28, frozen commit `8d06e1a` displayed all 100,200 atoms, inferred
bonds and four thumbnails in Chrome 156 on the M5 Max. The observed frame
median / p95 was 2,785 / 2,919 ms; browser load was 15.3 s. Other machine
workloads were active, so these are diagnostic observations, not controlled
before/after results or an interactive-rate claim. The reader still loads the
whole coordinate array; playback strides rendering work rather than streaming
archive bytes. Coordinate-editor pagination also prevents its 500,000 HTML
inputs from stalling playback at this size.

### Vectorised loading diagnostic

Frozen commits `8d06e1a` and `33ada85` were compared on the same synthetic
1,008,000-atom forest, in that order, on 2026-10-28 (M5 Max, Chrome 156,
ANGLE Metal). Other workloads were active: these observations are not a
controlled speedup claim.

| Measurement | Before | After |
|---|---:|---:|
| Server ready | 63.5 s | 61.0 s |
| Browser loaded and drawn | 91.2 s | 84.4 s |
| Browser drag median / p95 | 11.6 / 13.3 ms | 10.8 / 11.6 ms |
| Profiled scene build, including read | 200.2 s | 124.0 s |
| Profiled connectivity | 39.1 s | 23.4 s |
| Profiled geometry after loading | 15.3 s | 2.6 s |

Profiling adds substantial overhead. Unchanged JSON-schema validation alone
accounted for 122.3 / 83.3 s in those profiled runs, showing the environment
variation and the remaining loading bottleneck. The changes vectorise replicated
atom positions and element attributes, bond selection/length filtering, and
cell-list neighbourhood distance calculations. The inference tolerance,
periodic-image handling and resource limits are unchanged; no dependency or
format change is involved.

### Motion detail and at-rest prototype experiment

A frozen comparison of `33ada85` and `420ec89` on the same M5 Max / Chrome
156 setup lowered the motion-detail threshold from 100,000 to 10,000
instances. For the 10,200-atom multi-wall tube, median / p95 drag steps changed
from 16.0 / 65.9 ms to 3.8 / 13.9 ms. The lower threshold is retained.

An 8 × 6 sphere prototype at 100,000 atoms was also tried against the usual
12 × 8. On a 204,000-atom forest the verified full-detail restoration measured
23.4 / 22.9 ms before/after, with browser captures at 0.237 / 0.120 s. These
runs did not reproduce the earlier approximately one-second at-rest frame,
and concurrent workloads limit comparison. The restoration result does not
justify reducing at-rest quality, so the 12 × 8 prototype is retained at all
counts. `rest_ms` now waits for the LOD restoration counters before completing
the GPU work; it includes the animation-end scheduling delay.

### Terminal point-sprite diagnostic

Frozen `33ada85` and `86cbb84`, the same forest geometries at a 160 × 48
braille pane, ten orbit steps per case, 2026-10-28. Concurrent workloads were
active, so these are diagnostic observations. Loading includes unchanged
archive validation and varied independently of raster cost.

| Forest atoms | Before load / orbit median / p95 | After load / orbit median / p95 |
|---|---:|---:|
| 108,000 | 6.0 s / 73.6 / 80.4 ms | 9.5 s / 50.2 / 51.0 ms |
| 1,008,000 | 96.3 s / 1,109 / 1,204 ms | 108.9 s / 638 / 703 ms |

From 10,000 atoms, projected radii of at most 0.75 pixel use depth-tested
sprites. The rasterizer culls off-screen atoms and omits subpixel bonds
covered by atom endpoints. Larger projected spheres and sticks-only views
retain their existing raster paths. Tests cover depth ordering and zoomed-in
shading, and the forest raster was visually inspected. The million-atom
case remains below a smooth interactive rate.
