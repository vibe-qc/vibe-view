# Direct volume rendering design

> **Design, 2026-10-06, decisions taken the same day.** This note sets out
> the first version of direct volume rendering for the browser viewer. It
> records what exists, what the client can draw, what the benchmarks allow,
> the design, the tests, the rollout and the decisions behind it. Step 1 of
> the rollout is implemented; the viewer does not change before step 2. The
> roadmap entry is
> [Transfer functions and volume rendering](roadmap.md#transfer-functions-and-volume-rendering).

## Purpose and scope

vibe-view shows a stored volume as an isosurface: one contour at an isovalue,
or two lobes for a signed field. A figure of a density shell or an orbital
lobe is well served by that. A diffuse field is not: a potential, a spin
density around a metal centre, or an ELF basin reads better when the whole
field is drawn with a colour and an opacity that vary with the value. That
is direct volume rendering, and the viewer has none.

The first version adds one thing to the browser viewer: a per-section
**Render as** choice between *Isosurface* (today's behaviour, unchanged, the
default for every kind) and *Volume*, with a colour and opacity transfer
function the user can edit and a producer can preset. It sends a capped,
quantised grid, as the roadmap concluded on 2026-10-02.

In:

- Stored `volume.*` sections and `basis.ao`, that is every kind in
  `VOLUME_KINDS` except `volume.rdg`, whose NCI surface has its own path.
- An editable transfer function with per-kind presets, kept in sessions and
  bookmarks, and settable from `viewer_defaults`.
- Orthogonal grids, including flipped axes. Skewed grids fall back to the
  isosurface with a status message (open question 1).

Out, each with a follow-up noted below:

- Computed wavefunction surfaces (`mo_iso_*`, the `wf_*` state), which are
  sampled on demand at `wf_n_per_dim` and have their own lifecycle.
- Headless capture, the terminal viewer and the Jupyter helper.
- Periodic replication, clip planes, cross-fade, compare mode, ESP mapping
  and extra layers while a section is in volume mode.
- Reaction-path per-frame volumes (`traj_volume_`).

## Decisions already taken

- Rendering stays on Trame and PyVista with the vtk.js client
  ([decision 6](ROADMAP_AVOGADRO_PARITY.md)). The volume is a server-side
  `vtkVolume` that trame-vtk serialises; the client draws it with its own
  volume mapper. Nothing is computed in JavaScript.
- A first version sends a capped, quantised grid by default (roadmap,
  measured 2026-10-02).
- A design note precedes the implementation (maintainer, 2026-10-05).
- No codename or version is assigned.

## What exists today

**Reader.** `QVFReader.read_volume_grid` returns a `GridData` with `origin`,
`voxel_vectors` (row *i* is the step along grid axis *i*), `shape` and
`units`, always bohr for a stored volume. `read_volume_data` returns the
C-order `[nx, ny, nz]` array and checks it against the grid shape. The
schemas admit only `float32` and `float64` data, three-element shapes, and
no compression or endianness field. There is no size limit on a volume
member, the binary payload is cached per section and member, and
validation does not reject non-finite samples, so every range below uses
`nanmin` and `nanmax`. A complex array can only arrive outside the schema;
`stored_volume_field` reduces it by its component label.

**Renderer.** `renderers/volume.py` builds the contour. `build_isosurface_mesh`
scales by `grid.to_angstrom`, tests the off-diagonals of the voxel vectors,
and makes a `pv.ImageData` for an orthogonal grid, flipping a negative
spacing and moving the origin to the low corner, or a `pv.StructuredGrid` of
explicit points for a skewed one. `replicate_volume_for_periodic` tiles the
samples exactly when the lattice is an integral number of voxel steps.
Signed kinds (`SIGNED_VOLUME_KINDS`) are split by every caller into
`clip(data, 0)` and `clip(-data, 0)` and contoured at `+iso` in the
`ISO_POSITIVE` and `ISO_NEGATIVE` colours.

**Browser.** `active_volume_id`, `isovalue`, `colormap`, `opacity`,
`extra_isosurfaces`, the clip keys and `volume_loaded` describe the active
volume. `_refresh_active_volume` is the one guard; it writes hints onto
`ViewerState.get_volume_hints` and calls `_rebuild_volume`, which looks up
the mesh cache, applies a stride for an unsigned field whose largest
dimension exceeds 100, wraps and tiles a periodic grid, contours, removes
every `volume_*` actor, adds the new ones, renders and pushes once, then
adds the extra layers and re-clips. `_push_view` hands the whole scene to
trame-vtk's serializer through the retaining view updater. The right
drawer's **Isosurface Controls** card holds the isovalue slider (linear,
0.001 to 1.0), the colormap, ESP mapping, reduce detail, extra layers,
opacity, cell replication and the clip planes. Sessions and bookmarks save
`isovalue`, `colormap` and `opacity`.

**Hints.** `VolumeHints` has `isovalue`, `colormap` and `opacity`, with
per-kind defaults in `viewer_defaults.py`. The `ViewerHint` schema object
has `additionalProperties: true`, so a new hint key validates without a
schema change, and `from_manifest` ignores keys it does not know.

**Headless and terminal.** `capture.render_volume` draws the same
isosurface off-screen. The terminal viewer contours the field and
rasterises the mesh in numpy; it cannot draw a volume.

**The client, as trame-vtk 2.11.16 serialises it.** Volumes are supported:
`vtkVolume`, every VTK volume mapper including `vtkSmartVolumeMapper` (all
aliased to the vtk.js `vtkVolumeMapper`), `vtkVolumeProperty`,
`vtkColorTransferFunction`, `vtkPiecewiseFunction` and `vtkImageData` all
have serializers. What reaches the client decides the design:

| Serialised | Not serialised |
|---|---|
| Mapper: `sampleDistance`, `imageSampleDistance`, `autoAdjustSampleDistances`, `blendMode` | Mapper clipping planes, cropping, maximum samples |
| Property: `shade`, `interpolationType`, `ambient`, `diffuse`, `specular`, `specularPower`, `independentComponents`; the colour and opacity functions of component 0 | `scalarOpacityUnitDistance`, gradient opacity, label outline |
| Image: `spacing`, `origin`, `extent`, `direction`, and **every** point and cell array | A volume mapper for an unstructured or structured grid |
| Colour function nodes `[x, r, g, b, midpoint, sharpness]`; opacity nodes `[x, y, midpoint, sharpness]` | |

Three consequences. The clip sliders cannot cut a volume on the client, so
clipping is out of the first version. The dataset must carry exactly one
array, because the serializer ships them all. A skewed grid has no client
mapper, so it either falls back or is resampled onto an orthogonal box;
`pv.ImageData` accepts a direction matrix, but whether the vtk.js mapper
honours a non-orthogonal one is unverified.

## Measured constraints

| Measurement | Result | Source |
|---|---|---|
| Payload, the 19 volume sections in ten example archives (largest 75×75×30) | ≤ 0.26 MB float32, ≤ 0.06 MB uint8 | `bench_volume_payload.py` |
| Payload, synthetic 128³ and 256³ float32 | ≈ 8 MB and 67 MB; the isosurface 1.0 and 4.0 MB | same |
| Payload, uint8 capped at 128³ | ≈ 2 MB | same |
| Frame time, volume against surface, Apple M4 Pro, Chrome, 64³ to 256³ | 2.7 to 3.1 ms against 1.8 to 2.0 ms | `bench_volume_fps.py` |
| Not measured | production-sized cubes, weak or software GPUs, the full viewer under mouse drags | roadmap |

Both benchmarks used `plotter.add_volume` with the default smart mapper and
a `pv.ImageData` carrying one array, which is the path proposed here.
The payload benchmark quantised with `round((v - lo) / (hi - lo) * 255)`
and capped by plain stride decimation; the frame-rate benchmark used a
six-node U-shaped opacity ramp over a diverging colormap.

## Design

### Payload: range, quantisation, cap

A new module, `renderers/volume_render.py`, turns a `GridData` and its array
into the dataset the volume actor needs. It is pure numpy and pyvista, so
headless capture and the tests use it without the browser.

**Range.** The transfer function is defined in data units, so the range
must be stable and must not be dominated by cusps. Each kind gets a rule:

| Kind | Range rule | Why |
|---|---|---|
| `volume.density` | `[0, p99.5]`, clipped | the nuclear cusps hold a few voxels at values hundreds of times the valence density |
| `volume.elf` | `[0, 1]` | ELF is bounded by construction |
| `volume.orbital`, `volume.spin`, `volume.difference`, `volume.potential`, `basis.ao` | `[-m, +m]` with `m = p99.5(|v|)` | signed: zero must map to the middle level so the diverging preset stays honest |
| anything else | `[nanmin, nanmax]` | |

The rule and the resulting range are kept in state as `vol_range`, so the
editor labels its axis in the section's own units (`_ISOVALUE_UNITS` has
them) and a producer can override the range through a hint.

**Quantisation.** `uint8`, 256 levels, `q = round((v - lo) / (hi - lo) * 255)`
clipped; non-finite samples take the level of zero, which is 0 for an
unsigned field and 128 for a signed one. The payload benchmark shows uint8
is what makes a 128³ grid affordable (2 MB against 8 MB). `uint16` stays a
follow-up for fields that need it; the module takes a `dtype` argument so
that costs nothing later.

**Cap.** The largest dimension is limited to `vol_max_dim`, default 128,
by pyvista's `ImageData.resample` with linear interpolation and no border
extension, not by stride decimation: a stride aliases a diffuse field into
stripes, and the benchmark's decimation was for size measurement only. The
factor is uniform, rounded up per axis, and the spacing scales with it, so
the voxels keep their aspect and the box the volume occupies does not
change. Resampling runs on the float field before quantisation. The status
line reports the sent dimensions and the factor whenever one applied.

**Geometry.** The dataset is a `pv.ImageData` in ångström from the same
`orthogonal_image` construction the isosurface builder uses, with a
negative spacing flipped and the origin moved, and with exactly one point
array, `"q"`. A skewed grid is detected by the same off-diagonal test and,
in the first version, falls back to the isosurface with a status message.

### Scene: the volume actor

`_rebuild_volume` gains a branch at the point where it would contour. In
volume mode it removes the section's `volume_*` isosurface actors and adds
one volume actor through `plotter.add_volume(image, scalars="q",
mapper="smart", name=...)`, then sets the property from the transfer
function. The actor's name takes a new prefix, `_Actors.VOLUME_RENDER =
"volrender_"`: it must not match `volume_`, because the material-preset
replay in `_apply_scene_appearance` treats every `volume_` actor as an
isosurface and would hand a `vtkVolume` to `apply_material_to_actor`, where
the failure is swallowed by the surrounding `try`. The replay skips the new
prefix explicitly. Removal in `_rebuild_volume` and in section switching
names both prefixes, through `_remove_actors_by_prefix` as the actor-removal
contract requires.

Property defaults: linear interpolation, composite blending, shading off.
Shading reads well on an orbital and costs a gradient per sample; it is a
switch in the card, default off, because the slow GPUs are the unmeasured
case. Because the opacity unit distance never reaches the client, the
opacity values in the presets are tuned against the vtk.js default of one
world unit, which here is one ångström; the note's follow-ups include
asking trame-vtk to serialise the field.

Only one translucent thing is in the scene at a time in volume mode: the
section's isosurface actors are gone, extra layers are not drawn, and
orbitals stay opaque, so the depth-peeling stall that made orbitals opaque
by default (roadmap, "Not yet placed") has no new trigger.

### The transfer function

The transfer function is a state value, `vol_tf`, a JSON object the client
can round-trip:

```json
{
  "preset": "signed-lobes",
  "range": [-0.021, 0.021],
  "color": [[-0.021, 0.23, 0.30, 0.75], [0.0, 0.95, 0.95, 0.95], [0.021, 0.71, 0.02, 0.15]],
  "opacity": [[-0.021, 0.5], [-0.010, 0.25], [-0.002, 0.0], [0.002, 0.0], [0.010, 0.25], [0.021, 0.5]]
}
```

Node positions are in data units; the server maps them onto the quantised
levels when it builds the `vtkColorTransferFunction` and
`vtkPiecewiseFunction`, so editing a node never re-sends the grid. The
serializer deduplicates data arrays by digest, so a push after a
transfer-function edit carries the property and the two functions, not the
voxels; a test pins that (below).

Presets, one per kind, seed `vol_tf` when a section first enters volume
mode: *density shell* (viridis over the range, opacity rising from 0 at the
low end to 0.6 at the top), *ELF basins* (plasma, opacity 0 below 0.5 and
0.7 above 0.8), *signed lobes* (the diverging colormap of the kind's
isosurface preset, the U-shaped opacity above), *potential* (coolwarm,
symmetric, a shallower U). The colour nodes come from the chosen matplotlib
colormap sampled at eight positions, so the existing **Colormap** select
keeps working and a user who only wants a different colour never touches
the nodes.

The editor in the first version is deliberately small, because the card
already holds nine controls: the **Render as** switch; the preset select;
**Window** (two sliders for the range ends, in data units); the colormap
select; an **Opacity ramp** of six sliders at fixed positions across the
window, which is exactly the shape the benchmark used; and **Shade**. A
node-dragging curve editor is a follow-up and needs a client-side widget;
nothing in this design prevents it, since the state already holds the
nodes. Sessions and bookmarks save `vol_mode` and `vol_tf` beside
`isovalue`, `colormap` and `opacity`.

A producer presets the same thing through `viewer_defaults`, keyed by
section id like the existing hints: `"render": "volume"` and
`"transfer_function": {...}` in the shape above. The hint schema admits the
keys today; `from_manifest` learns them, and the format's own documentation
gets a `[for qvf]` ask so the keys are recorded where the format lives.

### Fit with the existing features

| Feature | In volume mode |
|---|---|
| Section switching, file switching, hot reload, `_reload_active_file` | The mode and the transfer function are state, and `_rebuild_volume` reads them, so they survive as the material preset does |
| Materials, toon, SSAO, ray tracing, figure outlines | Skipped for the volume actor; outlines apply to atoms and bonds only; SSAO is server-side anyway |
| Publication preset | Works; the presets are tuned for both backgrounds and the note's screenshots must show the white one |
| Clip planes, 2D slice | The card is hidden; the serializer carries no mapper clipping planes |
| Periodic replication | The control is disabled at 1×1×1; tiling multiplies the payload |
| Cross-fade, compare mode, ESP mapping, extra layers | Isosurface only; the card hides them |
| Bookmarks, sessions | Save and restore `vol_mode` and `vol_tf` |
| Orbital surfaces (`mo_iso_*`) | Unchanged, opaque |

### Headless and terminal

`capture --volume-render` is feasible: the smart mapper ray-casts
off-screen in VTK, the module above builds the dataset without the browser,
and the kind presets give a deterministic look. It stays a follow-up so the
first version can be measured in the browser before the headless and
`batch` surfaces commit to it. The terminal viewer does not change.

### Guards

- `vol_max_dim` (default 128) caps the sent grid; the hard limit on voxels
  sent is `128³`, about 2.1 MB as uint8.
- The status line states the sent dimensions and the resampling factor.
- A grid the module cannot build (skewed, empty, all non-finite) leaves the
  section in isosurface mode and says why, through the status line that the
  manual already describes for refused controls.
- Nothing is sent until the user chooses *Volume*; the default experience
  is unchanged, which is how the feature is gated while incomplete.

## Tests and contracts

New, in `tests/test_volume_render.py`:

- The range rules per kind, including non-finite samples and a signed
  field whose zero lands on level 128.
- Quantisation round trip within one level, and the cap: a 300×200×50 grid
  becomes 128×86×22 with the spacing scaled so the bounds are unchanged.
- The dataset carries one array, in ångström, with a flipped axis handled
  as `build_isosurface_mesh` handles it.
- The serialised scene for a volume holds one `vtkVolume`, a colour and an
  opacity function with the preset's nodes, and one uint8 array of the
  expected length; a transfer-function edit then pushes no new array.
- Controllers: entering volume mode removes the section's `volume_*` actors
  and adds one `volrender_` actor with one push; leaving it restores the
  isosurface; `switch_file` and a hot reload keep the mode; the material
  replay skips the volume actor; the new controllers join the smoke lists.
- Hints: `render` and `transfer_function` round-trip through `from_manifest`
  and sessions.

Keep green, by group, since the volume path touches them:

- Reader and schema: `test_integration.py` (volume grid tests),
  `test_kinds.py`, `test_qvf_schema_identity.py`, `test_cube_units.py`,
  `test_sdk_api.py`.
- Component and signed contracts: `test_issue49_component_label.py`,
  `test_issue49_scene_conventions.py`, `test_signed_contours.py`,
  `test_renderer_regressions.py`, and the shared signed-kind list pins in
  `test_structural_contracts.py`, `test_terminal_correctness.py`,
  `test_issue52_cli.py` and `test_headless.py`.
- Geometry and periodicity: `test_periodic_grid_geometry.py`,
  `test_periodic_replication.py`, `test_periodic_wrap.py`,
  `test_slab_2d_rendering.py`, `test_issue45_renderers.py`,
  `test_renderer_behaviour.py`.
- Hints and cache: `test_viewer_defaults.py`, `test_render_caching.py`,
  `test_energy_window.py`, `test_cli_correctness.py`.
- Browser: `test_issue48_browser.py` (the one-guard, one-push test and the
  manual pin for the isosurface card), `test_controller_smoke.py`,
  `test_controller_state.py`, `test_volume_clip.py`,
  `test_structural_contracts.py`, `test_design_refresh.py`,
  `test_bookmarks.py`, `test_browser_app_lifecycle.py`,
  `test_plot_renderers.py`.
- Capture, terminal, Jupyter: `test_capture.py`,
  `test_issue60_error_reporting.py`, `test_tui.py`, `test_tui_app.py`,
  `test_jupyter.py`, `test_api.py`.

## Rollout

1. `renderers/volume_render.py` with its tests: range, quantisation, cap,
   dataset. No viewer change yet.
2. The actor and the serializer contract test, then the mode switch in
   `_rebuild_volume` and the card, behind *Render as* with *Isosurface* the
   default.
3. Presets, the editor sliders, sessions and bookmarks, the hints, the
   `[for qvf]` ask.
4. The manual: a **Volume** subsection under the `volume.*` entry in
   [the browser guide](browser.md), the hint keys in the
   `viewer_defaults` section, and a changelog fragment.
5. Measure the full viewer under mouse drags with a production-sized cube
   on a weak GPU, the three cases the roadmap lists as unmeasured, before
   the headless follow-up.

Each step lands as its own merge request and keeps `main` working; the
feature is invisible until step 2 and defaults off from then on.

## Decisions, 2026-10-06

The maintainer delegated the six open questions on 2026-10-06; these are
the answers the implementation follows, each with its reason, and any of
them can be overruled before the step that depends on it lands.

1. **Skewed grids fall back to the isosurface** in the first version, with
   a status message. Resampling onto an orthogonal box is a follow-up: it
   wastes voxels on the empty corners of a triclinic cell and loses the
   exact periodic tiling, and the fallback keeps the first version honest
   about what it draws. The payload module refuses a skewed grid with a
   message that says so.
2. **Presets, a window and a six-slider opacity ramp** make the first
   editor. The state already holds the nodes, so a node-dragging curve
   editor can replace the sliders later without changing the state, the
   sessions or the hints.
3. **The cap stays a largest dimension of 128, as uint8.** A byte budget
   would send a 160×160×80 grid whole, but the frame-rate measurement only
   covers cubes up to 256³ on one fast machine, and a dimension cap keeps
   the sent grid's aspect and the reasoning simple. `vol_max_dim` is a
   state value, so a later Settings entry can raise it.
4. **Hint names `render` and `transfer_function`, keyed by section id,
   ship in the viewer first.** The `[for qvf]` ask follows once the viewer
   side has shipped and the names have survived use; the hint schema
   admits them today.
5. **Headless parity is a follow-up, not the first version.** The payload
   module is built to make `capture --volume-render` a small change, and
   the browser side should be measured first on the cases the roadmap lists
   as unmeasured.
6. **A minor release, unversioned until cut time.** The feature needs no
   breaking change and no client-side work, so *Levinthal's Lynx* stays
   held for the interactive viewer.

Step 1 of the rollout, `renderers/volume_render.py` with its tests, is
implemented alongside these decisions; the isosurface builder and the
payload share one grid-to-image construction, `orthogonal_image`, so a
stored grid lands in the same place on both paths.
