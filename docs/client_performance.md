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
