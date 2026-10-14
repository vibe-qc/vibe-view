# The browser viewer

`vibe-view open` starts a local web server and opens the interactive viewer
in your browser. This page is the reference for that surface: what is on
screen, what every panel does, and the controls each section kind exposes.
For a guided first look, read the [walkthrough](walkthrough.md) instead.

```sh
vibe-view open water.qvf
vibe-view open structure.xyz density.cube     # loose files convert in memory
vibe-view open a.qvf b.qvf                    # a Files dropdown switches between them
vibe-view compare a.qvf b.qvf                 # the same, with compare mode on
```

Needs the `[viewer]` extra (the default `modes` profile has it) and a browser
with WebGL. The server binds to `127.0.0.1:8080` and runs until you press
<kbd>Ctrl</kbd>+<kbd>C</kbd> in the terminal; it has no idle timeout.

```{figure} images/01-structure.png
:alt: Formaldehyde displayed as red oxygen, grey carbon and white hydrogen atoms in the browser structure view.

The browser viewer: the section sidebar on the left, the 3-D viewport in the
centre, the per-section controls in the right drawer, and the source banner
across the top.
```

The figures below are captures of the real viewer. Captions distinguish
computed showcase data from illustrative panel fixtures.

## Launch options

| Flag | Effect |
|---|---|
| `--port N` | Bind to another port. `8080` is the default, and `open` refuses to start on a port something else already holds. |
| `--host ADDR` | Bind to another interface. The default is localhost only; see the warning below. |
| `--no-browser` | Start the server without opening a browser tab. |
| `-s ID`, `--section ID` | Open this section of the first file on startup, as a click in the sidebar would. An id the file does not have prints the ids it does have, and the viewer opens as usual. |
| `--auto-compare` | With two or more files, start with compare mode switched on. With one file it does nothing. |
| `--log-file PATH` | Write the server log somewhere other than `~/.cache/vibe-view/vibe-view.log`, which rotates at 5 MiB with three backups. |

:::{warning}
`--host 0.0.0.0` exposes the server on every reachable interface, and the
server has **no authentication**. On a remote machine, keep the default bind
and tunnel instead:

```sh
ssh -L 8080:127.0.0.1:8080 you@remote-host
# on the remote host:
vibe-view open results.qvf --no-browser
```

then open `http://127.0.0.1:8080` in your own browser. If you only need to
*look*, [terminal mode](terminal.md) needs no tunnel at all.
:::

Defaults for the port, the bind address and whether a browser opens can be
set once in the [configuration file](cli.md#configuration).

## The startup banner

Before the server starts, the terminal prints one line per section saying
what the viewer will do with it:

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  QVF file: water.qvf                                                         ║
║  Source:   vibe-qc <version> - water                                         ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Section ID         Kind                         Status                      ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  structure          structure                    rendered                    ║
║  density            volume.density               rendered                    ║
║  homo               volume.orbital               rendered                    ║
║  ir                 spectra.ir                   rendered                    ║
║  x_custom.notes     x_custom.notes               skipped, vendor namespace   ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

The statuses and what they mean are listed under
[What vibe-view can show you](capabilities.md). A section the viewer cannot
draw still appears in the sidebar, greyed, with that reason; nothing is
silently dropped, and an unknown kind never stops the rest of the file from
rendering.

## What is on screen

* **App bar.** The source banner (program, version, calculation), the
  **Files** dropdown when more than one archive is open, and the toolbar
  icons: open a file, edit mode, measure mode, camera views, screenshot,
  video export, presentation mode, the vq Job Manager, settings, and
  <kbd>?</kbd> for the shortcut list.
* **Sidebar** (left). The section tree. Clicking a section makes it the
  active section and, for volumetric kinds, reads its payload from the
  archive for the first time. The **Display** card at the top holds the
  controls that apply to the whole scene.
* **Viewport** (centre). The 3-D scene for structures, volumes, trajectories
  and vibrations.
* **Result panel** (below the viewport). Plots, tables and provenance for the
  active section. Drag the horizontal splitter to give a spectrum or table
  more space; the structure remains visible above it.
* **Right drawer.** The controls for the active section, and the cards that
  are only relevant in some states: Compare Files, Density Difference,
  Bookmarks, Calculation Parameters, Submit to vq.
* **Status line** (bottom). What the viewer just did, and why it refused
  when it did. Many controls report through it rather than through a dialog:
  a representation the structure cannot support, a colour mode with no data
  behind it, a selection that matched nothing.

A right-click on an atom opens a context menu for it, and hovering an atom
shows a tooltip for it.

### Keyboard shortcuts

| Key | Action |
|---|---|
| <kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>K</kbd> | Command palette: fuzzy-search every quick action and every section by name |
| <kbd>e</kbd> | Toggle edit mode |
| <kbd>m</kbd> | Toggle measure mode |
| <kbd>r</kbd> | Reset the camera |
| <kbd>s</kbd> | Screenshot |
| <kbd>v</kbd> | Video export dialog |
| <kbd>p</kbd> | Presentation mode |
| <kbd>Space</kbd> | Play / pause the trajectory or vibration on show |
| <kbd>[</kbd> / <kbd>]</kbd> | Previous / next frame, or phase step of a vibration |
| <kbd>&lt;</kbd> / <kbd>&gt;</kbd> | Previous / next normal mode |
| <kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>Z</kbd> | Undo (edit mode) |
| <kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd>, <kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>Y</kbd> | Redo (edit mode) |
| <kbd>Delete</kbd> | Delete the selected atoms |
| <kbd>Esc</kbd> | Leave presentation, edit or measure mode |
| <kbd>←</kbd> / <kbd>→</kbd> | Previous / next slide in presentation mode |
| <kbd>?</kbd> | Show this list in the viewer |

When the command palette is open, <kbd>Esc</kbd> closes it and returns focus
to the control that launched it, including during presentation mode.

Rotate with a left drag, pan with a middle drag, zoom with the wheel.

## The Display card

These controls apply to the scene as a whole and stay put while you switch
sections.

**Representation.** Ball-and-stick (default), space-filling, sticks only,
wireframe, and cartoon for a structure that carries residues (see
[Biomolecules](biomolecules.md)). The picker refuses a cartoon on a structure
with no backbone and says so in the status line.

**Material Style.** Six presets: *CPK Glossy* (default), *Matte*, *Glass /
Translucent*, *Metallic*, *Toon / NPR* and *Scientific / Publication*.
Changing the preset re-styles every atom, bond and isosurface at once, and
the background follows. Atoms and bonds take the preset's highlight; an
isosurface takes its own ambient, diffuse and specular terms, while its
opacity stays with the isosurface sliders. A separate **Toon / NPR
shading** switch flattens the shading and draws the mesh edges.

**View preset** bundles a material, a background, labels, outlines and the
viewport chrome into three named looks: *Publication (white, clean)*,
*Presentation (dark, glossy)* and *Analysis (flat, labels)*. *Publication*
is the one-click figure look: the *Scientific* material on its near-white
background, ambient occlusion and labels off, a black outline around every
atom and bond, and the viewport chrome hidden. The first preset you apply
remembers the look it replaced; clearing the picker restores that look. A
preset survives switching or reloading files, and the command palette has
an entry for each preset and for the restore. The same look is available
headlessly as `vibe-view capture --publication`.

**Outline atoms and bonds** draws the outline on its own, with any material.
Each atom and bond gets a slightly larger black copy with its front faces
culled, so the outline is geometry rather than a render pass: it reaches the
live viewport and stays right as you rotate. Its width is fixed in ångström,
so it scales with the molecule rather than with the window. Isosurfaces,
ribbons, the unit cell and labels are never outlined.

**Hide viewport chrome** hides what the page draws over, around or beside
the scene without being part of it: the bounds grid with its axis labels,
the status, session and job chips in the app bar, the atom hover tooltip and
the welcome card. It also leaves measure and edit mode, since their markers
are chrome inside the scene; entering either mode again draws them. The
drawers, the result panel, the unit cell and measurements stay. For a
full-screen view use presentation mode.

**Show atom labels**, **Orthographic projection**. The background is switched
from the toolbar; **Settings** holds the default it starts with
(*Dark background (default)*).

**Periodic Replication** (periodic files only). `Nx`, `Ny`, `Nz` replicate
the cell along its lattice vectors. Atoms, bonds, the cell wireframe and any
active isosurface expand together, and only axes flagged periodic are
replicated: a slab tiles in-plane and never along its vacuum direction.
Bonds that cross a cell face are drawn to the nearest periodic image rather
than stretched across the box.

**Auto-reload on file change.** Watch the open file and hot-reload the scene
when its content changes. This is how you follow a running calculation; see
[Jobs and live results](jobs.md).

**Element colour override**, also in the Display card, changes the colour of
one element everywhere, including headless captures and terminal frames, so a figure
made in any surface agrees with what you saw on screen.

## Measuring

Press <kbd>m</kbd> or the ruler icon, then click atoms: two give a distance,
three an angle, four a dihedral. The **Measurements** card lists what you
have measured and can list every bond distance and angle in the structure at
once. Edit mode and measure mode are mutually exclusive.

Distances read in Ångström by default. **Settings → Length unit** switches
them to bohr. The choice persists across restarts, and saving it re-measures
the open readout, both picked atoms and the full bond list, without changing
the selection. The same unit reads out the RMSD in the **Compare Files**
legend, the distance column of the **Bond Orders** panel and the largest
shift **Symmetrize** reports, and saving it relabels those too. Structures
stay in Ångström; angles are unaffected.

```{figure} images/06-measure-distance.png
:alt: Measure mode enabled in the browser structure controls, ready to select atoms and inspect distances.

Measure mode enabled in the structure controls, ready for atom selection.
```

## Clip plane

**Clip Planes** in the Isosurface Controls cuts the active isosurface with
three axis-aligned planes at once: the X, Y and Z sliders each place one, as a
fraction of the surface's extent, and what stays is the part on the high
side of all three (larger x, y and z). It
cuts the isosurface only; atoms and bonds stay whole. This is the quickest
way to see the inside of a density surface. Without an active volume section
there is nothing to clip.

```{figure} images/07-clip-plane.png
:alt: An illustrative difference-density isosurface cut by an enabled clip plane.

A clip plane through a density isosurface. This capture uses illustrative panel data.
```

## Section by section

### `structure`

Atoms as CPK spheres, bonds as cylinders. Connectivity comes from an
explicit `bonds` section when the producer wrote one; otherwise it is
inferred from covalent radii, and inferred bonds are a visual aid, not
evidence that the source carried bond orders. Periodic structures show the
unit-cell wireframe.

When several files are open, the **Compare Files** card appears:

* **Overlay all structures** draws every loaded structure in one viewport,
  one translucent colour per file, with a colour-keyed legend.
* **Align (RMSD fit to first file)** Kabsch-superposes each structure onto
  the first when the atom counts match, and reports the RMSD per file in the
  [Length unit](#measuring). With alignment off, structures sit at their
  stored coordinates.

Clicking any section leaves compare mode.

The **Symmetry** card detects the molecular point group (Schoenflies) from
the inertia tensor and the symmetry operations it finds. Its **Symmetrize**
button projects the geometry onto that group, undoably, and reports the
number of operations and the largest atom shift, in the Length unit. A
`structure.symmetry` section written by the producer is a different thing:
that is the producer's crystallographic analysis, shown on its own panel.

### `volume.*` and `basis.ao`

Every volumetric kind (`density`, `orbital`, `spin`, `elf`, `difference`,
`potential`, `rdg`, `generic`, and `basis.ao`) drives the same isosurface
renderer, marching cubes over the stored grid, and every kind but `rdg` can
be drawn as a volume instead. The **Isosurface Controls**:

| Control | Effect |
|---|---|
| **Render as** | *Isosurface* (the default) contours the field at the isovalue. *Volume* draws the field itself: every voxel takes a colour from the colormap and an opacity from a ramp chosen for the kind, so a diffuse potential, a spin density or an ELF basin reads as a cloud rather than a shell. See [Volume mode](#volume-mode) below. |
| **Volume isovalue** | Slider over the data range. The default comes from the file's `viewer_defaults`, or a kind-specific heuristic: 0.05 e/bohr³ for densities, 0.04 for orbitals. |
| **Volume opacity** | Alpha of the surface. |
| **Reduce detail (faster)** | Downsample the grid before contouring. On by default for a grid with 200 or more voxels along an axis, where the status line reports the reduced grid; worth keeping on above roughly a million voxels. Signed fields are never reduced. |
| **Show as 2D slice** | Replace the isosurface with a coloured planar cut through the field, positioned with the clip-plane sliders. Good for nodal planes and bonding regions. |
| **Map ESP onto surface** | With a `volume.potential` section in the file, colour the active density surface by the electrostatic potential at each vertex. |
| **Wrap orbital to cell centre (cyclic cluster)** | For a localized periodic orbital grid, roll the field so the orbital sits in the middle of the cell. Delocalized or already-centred fields are left alone. |

Isovalue, colormap and opacity settings belong to each volume section. Changing
one section's settings does not change another section's defaults, including
when opening another file. Partial `viewer_defaults` hints override only the
specified settings; the remaining settings use the volume kind's defaults.

(volume-mode)=
#### Volume mode

*Render as → Volume* sends the stored grid to the viewport as 256 levels
over a **window** in the section's units. The choice belongs to the
section, like its isovalue: each volume section remembers its own mode and
transfer function while the file is open. The window starts at a range
chosen for the kind: zero to the 99.5th percentile for a density, so a
handful of voxels at the nuclei do not claim the levels; 0 to 1 for ELF; a
symmetric range for a signed field, so zero sits on the middle level; the
finite extremes otherwise. The card shows the range and the grid it sent.
A grid with more than 128 voxels along an axis is resampled so its largest
axis is 128, keeping its box; the status line reports the full and the sent
grid.

The transfer function is edited in the card. **Preset** seeds the opacity
ramp for a look: a *density shell* rises towards the dense end, *ELF
basins* shows the basins above 0.8, *signed lobes* draws both lobes with
nothing at zero, *potential* is the same with gentler slopes, and a *plain
ramp* rises linearly. The two **window** sliders set the ends of the range
the levels cover, within the field's own extremes; moving them re-sends the
grid, which the other controls never do. The **opacity curve** draws the
colour nodes as a gradient and the opacity nodes as a line with a handle
each: drag a handle to move it, click the gradient to add a node there,
double-click a handle or press Delete on it to remove one, and use the
arrow keys on a focused handle to move it, Enter to insert a node after it.
Editing the curve makes the preset *Custom curve*. The six **opacity ramp**
sliders below it are the coarse path: they show the curve sampled at the
start, 20%, 40%, 60%, 80% and the end of the window, and moving one
replaces the curve with that six-point form. The colour follows the
**Colormap**. **Shade by gradient** lights the
volume like a surface; it costs a gradient per sample and is off by
default. Bookmarks, presentation slides and sessions keep the mode and the
transfer function with the section. The command palette's *Toggle volume
rendering of the active volume* flips the picker without a visit to the
card.

The contour's own controls, the isovalue, extra layers, ESP mapping, reduce
detail, cell replication and the clip planes, apply to the isosurface only
and hide in volume mode; entering volume mode turns an active clip off. A
skewed grid is interpolated onto the axis-aligned box that bounds it before
it is sent, at the same cap, because the client can only place an
axis-aligned image; the status line says so, the corners of a triclinic
cell's box stay empty, and periodic tiling is not applied. A field without
finite samples returns the picker to *Isosurface* with the reason. The
volume is not pickable, so
hovering and clicking still reach the atoms behind it. A producer can open
a section as a volume with its own transfer function through
`viewer_defaults`; see [the hints](#viewer-defaults-hints-from-the-producer).
`vibe-view capture --render volume` draws the same look headlessly, and
`capture` follows a section's `render` hint by default; see
[Figures without a display](headless.md). The
[design note](volume_rendering_design.md) records how volume mode came to
be.

Signed fields (`orbital`, `spin`, `difference`, `potential`) draw both lobes
in contrasting colours. Unsigned fields get one surface. Signed surfaces preserve
the full voxel-vector geometry, including skewed grids and reversed axes, in
both the browser and headless captures.

The periodic volume control adds cells on both sides of the original, only
along periodic axes. When lattice translations match integral steps of the
voxel grid and overlapping samples agree, the viewer tiles the scalar field
before contouring. This retains skewed and reversed voxel vectors. Otherwise
it contours the original field and places surface copies at the exact lattice
translations. These copies retain any boundary seams: the viewer does not
invent missing samples, interpolate between cells, or overwrite inconsistent
periodic samples. The periodic volume control supersedes the ordinary `Nx`,
`Ny`, `Nz` surface replication while active.

```{figure} images/02-density.png
:alt: A computed formaldehyde electron-density surface surrounding its ball-and-stick geometry, beside the volume controls.

An electron-density isosurface, translucent over the structure.
```

```{figure} images/02b-density-volume.png
:alt: The same electron density drawn as a volume: a cloud that brightens and thickens towards the atoms, beside the volume controls.

The same density in volume mode, through the density-shell preset: the
field itself, denser where it is brighter, with no contour drawn.
```

```{figure} images/03-difference-density.png
:alt: Opposite signed lobes of an illustrative difference-density field displayed around a molecular structure.

A difference density: accumulation and depletion in two colours. This capture uses illustrative panel data.
```

```{figure} images/04-elf.png
:alt: An illustrative electron-localization field displayed as an isosurface, with its threshold controls.

An electron localization function surface. The default isovalue of 0.8
picks out the localization basins: lone pairs, bonding regions, core shells. This capture uses illustrative panel data.
```

**NCI.** A `volume.rdg` section contours the reduced density gradient at
s = 0.3 and colours the surface by sign(λ₂)ρ computed from a co-present
`volume.density` section: blue for attractive, green for van der Waals, red
for steric repulsion, clamped to ±0.05 a.u. An NCI surface computed from an
embedded wavefunction uses the same level, colour map and range, so the two
are comparable. With no density in the archive the stored surface renders
uncoloured. Density on a different grid (shape, origin or
voxel vectors) also leaves the surface uncoloured and reports the mismatch.
Both forms follow the full voxel vectors,
including skewed grids and reversed axes, and use the same periodic replicas.
The colour field is interpolated at the surface vertices.

**Cross-Fade Blend.** With two volumes loaded, blend between them with a
slider, which is the honest way to compare two orbitals or two densities on
the same grid without flipping back and forth.

Volumetric payloads are lazy: nothing is read from the archive until you
click the section, so a file with forty orbitals opens as fast as one with
none.

### `wavefunction.gto`

The **Molecular Orbitals** panel lists every orbital with its energy and
occupation. Click a row, or use the HOMO / LUMO buttons and the previous /
next arrows, and the viewer evaluates that orbital from the basis and the
coefficients on a grid of its own choosing. Nothing has to be pre-computed
by the producer, which is why a `wavefunction.gto` section is a far better
way to ship orbitals than forty `volume.orbital` sections.

Orbital energies read in Hartree (Eh) by default. **Settings → Energy unit**
switches the list, the energy diagram and its HOMO–LUMO gap to eV or
kcal/mol; the choice persists across restarts and applies to an open list as
soon as you save. The archive itself stays in Hartree. With the axis in
Hartree, the gap is still quoted in eV.

* **Grid points / axis** sets the evaluation resolution.
* **Orbital isovalue** sets the surface level; click **Render MO** to apply.
* **Orbital component** appears for complex coefficients: choose the real
  part (default), imaginary part, or magnitude |ψ|, then click **Render MO**.
  Signed parts use blue/red lobes; magnitude has only a positive surface.
  The file's phase is preserved. An empty contour reports that the isovalue
  is too high for that component. A stored `volume.orbital` section has no
  such choice: its `component` only labels what the file stores, and the
  viewer draws the stored values as they are.
* **Compute total density** sums the occupied orbitals into a density
  surface and reports ∫ρ dV as an electron-count check.
* **Show isosurface** hides the most recently computed surface (orbital,
  density, ELF, NCI or Laplacian) without leaving the section, and shows it
  again.
* Canonical, alpha/beta, natural and localized sets each get their own
  list. Localized orbitals are labelled by the atoms they sit on, because a
  localized orbital has no meaningful energy ordering.
* **Re-localize with** sends the occupied subspace to a separately installed
  vibe-qc backend. Configure it as described below. IBO, Foster–Boys and
  Pipek–Mezey appear only when their backend readiness checks pass and the
  selected archive supplies compatible data.

#### Configure re-localization

Install vibe-qc with its native core in its own Python environment. The viewer
does not need vibe-qc installed in its environment, and the backend does not
need vibe-view. This integration requires the standalone worker's **API 1.0.0
or later in major version 1, JSON protocol 1**, introduced for vibe-qc 0.17.5.
Before that release is available, use a vibe-qc source installation containing
commit `0830147b8a09f2263901960101ab571fa5e96957` or a later descendant.
Follow the [vibe-qc installation instructions](https://github.com/vibe-qc/vibe-qc)
for the native build; a Python package import alone does not establish readiness.

1. Open **Settings**, enter the absolute path to that environment's **Python
   executable**, and select **Save backend and check**. Choose the Python
   executable itself, without shell arguments or a wrapper command.
2. The setting persists globally across files and viewer restarts. On startup,
   the viewer checks the native core and individual localization methods.
   **Check backend** repeats that probe after an installation or upgrade.
3. Select a wavefunction, keep **Archived occupied orbitals** selected, choose
   **Re-localize with**, then select **Localize**. The viewer passes exact
   archived shells, real occupied coefficients, occupations and structure data
   as JSON. It does not pass a QVF filename or reconstruct a basis by name.

For a backend Python at `/path/to/backend/bin/python`, these are the commands
used by the viewer:

```sh
/path/to/backend/bin/python -I -m vibeqc_relocalize --probe
/path/to/backend/bin/python -I -m vibeqc_relocalize
```

The worker uses stdin/stdout for versioned JSON and stderr for diagnostics.
The probe times out after 60 seconds; localization after 300 seconds. **Cancel**
terminates the worker. A missing executable, incompatible protocol, unavailable
native core or backend refusal produces an explanatory status in the panel.

The molecular adapter requires a restricted singlet with explicit charge,
multiplicity, exact QVF GTO shells and electron occupations of 0 or 2. The
occupied coefficients must cover the complete all-electron occupied subspace.
No density matrix is required. A density-only archive cannot preserve the
archived occupied orbitals through this interface. ECP calculations, fractional
occupations, unrestricted/spinor orbitals, complex coefficients, and periodic
systems (including Gamma-only archives) are not supported by this adapter.
The limit is 512 AOs and elements through radon. Unsupported or incomplete
archives show a reason instead of an enabled action.

**New RHF + localize** explicitly calculates a new wavefunction before
localizing. It requires a compatible archived all-electron basis, occupations,
charge and multiplicity, but does not require archived coefficients. Worker
API 1.0.0 also needs the archived basis name for its atomic initial guess;
exact shells still define the calculation basis. It may
use edited positions of the same atoms. Changes to atom identities require a
new calculation archive. Selecting this option never preserves the previous
calculation's occupied subspace, even if that calculation also used RHF.
There is no automatic SCF fallback when localization fails or data is missing.

Results appear as session wavefunction overlays in the sidebar. They include
localized orbital surfaces, atom populations, centroids and centre counts,
plus an IAO charge table and the backend's subspace/orthonormality errors.
The viewer also checks the returned rotation and its action on the supplied
coefficients. Localized orbitals have no canonical orbital energies. The
panel identifies whether the source was the archive or a new RHF calculation;
the current kernel does not certify localization convergence.

**Clear re-localized overlays** returns to the archived wavefunction. Geometry
edits remove existing localization overlays. File/section changes, edits and
backend changes discard any in-flight result belonging to the old state.
The original QVF file is never modified. The backend's experimental periodic
finite-torus output needs additional archive metadata and a separate rendering
adapter; it is deliberately not offered as molecular localization.

#### Orbital evaluation limits

The on-demand evaluator covers shells through `l = 3`. An orbital with more
than 0.5 % of its weight in g or higher shells is drawn and the status line
says the surface is incomplete. A wavefunction explicitly tagged with
`k_point: [0, 0, 0]` is evaluated as a Gamma-point lattice sum in a fully
periodic 3D primitive cell, including neighboring atomic-orbital tails.
Skew cells keep their actual shape. **Apply Replication** repeats that cell
and its orbital surface. Very diffuse bases have a work limit; use a stored
orbital grid if evaluation exceeds it.

Nonzero k-points and partially periodic Bloch evaluation are not supported
and produce an explanatory error. A single stored k-point block also does
not establish the weights needed for a total periodic density or its
ELF/NCI/Laplacian; select the producer's stored fields instead. Older periodic
archives without explicit k-point metadata retain central-cell/cluster
sampling and do not acquire a Bloch interpretation from their filename.

```{figure} images/15-orbital-homo.png
:alt: The positive and negative formaldehyde HOMO lobes evaluated from the stored wavefunction.

An orbital evaluated on demand from `wavefunction.gto`, both lobes drawn.
```

The **Basis Functions** panel does the same for a `basis.ao` section: pick
an atomic orbital and see it.

(bands-dos-panel)=
### `bands`, `dos.total`, `dos.projected`, `dos.coop`, `dos.cohp`

Interactive Plotly charts. A `bands` section plots every band along the
k-path with the Fermi level as a horizontal reference; hover any band for
its energy. When a `dos.total` section is also present the two are drawn as
one figure on a shared, Fermi-referenced energy axis. Bands are shifted by
their own `kpath.fermi`; QVF DOS grids already use E_F = 0 and are never shifted
again by an absolute Fermi tag. If bands lack a Fermi reference, the panels
use separate energy axes and labels. Projected DOS, COOP
and COHP add per-channel traces you can toggle in the legend.

**Energy window.** An all-electron periodic archive carries core states
hundreds or thousands of eV below E_F, and an axis autoscaled to all of
them squeezes the valence and conduction bands into a few pixels. The
panel therefore opens on a valence window: the states grouped around E_F,
with the core shells left off the axis. **E − E_F min** and **E − E_F max**
move it — commit with Enter or by leaving the field — and **Reset window**
returns to the default. Double-clicking the chart is Plotly's own
autoscale, which shows the full range including the core states.

A file whose spectrum has no core states is not windowed at all, so
pseudopotential archives keep the axis they have always had. A producer
can ship its own first view with an `energy_window` hint in
`viewer_defaults`, as `[min, max]` in eV relative to E_F:

```json
"viewer_defaults": {
  "bands": {"energy_window": [-15.0, 10.0]}
}
```

**Energy unit.** The band and DOS axes read in eV by default.
**Settings → Band / DOS energy unit** switches them to Hartree or kcal/mol.
The DOS is rescaled with the axis, so it stays in states per displayed unit
and the area under it is unchanged. The window fields follow the unit too,
and a window you typed is carried over when you save, so the view stays put.
This is a separate setting from **Energy unit**, which applies to orbital
energies and defaults to Hartree. The archive, and an `energy_window` hint,
stay in eV. The COOP and COHP panels follow the same setting, as do the
terminal viewer's band, DOS, COOP and COHP plots and the band structure
`vibe-view capture` writes; the [capture API](headless.md) takes an
`energy_unit` argument instead.

```{figure} images/08-bands-dos.png
:alt: Illustrative electronic band curves along Gamma–X–L beside density of states on a shared energy axis.

Band structure and density of states on one Fermi-referenced axis. This capture uses illustrative panel data.
```

### `fermi_surface`

The Fermi surface in reciprocal space, one sheet per band crossing E_F,
inside the reciprocal-cell wireframe. Because this is k-space, the
real-space structure is hidden while it is shown; click any other section
to bring it back. **Bands shown** in the **Fermi Surface Bands** card
isolates individual sheets.

### `phonon_bands`, `phonon_dos`, `equation_of_state`

Phonon dispersion and phonon DOS share the chart panel with the electronic
bands, in cm⁻¹, and draw imaginary modes below zero rather than hiding them.
An equation-of-state section plots the sampled (V, E) points, overlays the
producer's fitted Birch–Murnaghan or Murnaghan curve, marks V₀ and annotates
B₀ and B₀′.

### `spectra.*`

`spectra.ir`, `raman`, `uvvis`, `ecd`, `vcd` and `generic` render as stem
charts of intensity against frequency with hover tooltips, in each kind's
native unit. **Spectrum broadening** draws a Lorentzian envelope under the
sticks; the legend names it *Lorentzian envelope* with its width, so the
convenience is never mistaken for the data. **X unit** and **Normalize**
adjust the axis. Signed spectra keep their negative bands.

`spectra.nmr` and `spectra.epr` are tables: shifts and couplings, g-tensors
and hyperfine values.

```{figure} images/09-ecd-spectrum.png
:alt: An illustrative ECD spectrum with positive and negative transitions in the browser chart panel.

An ECD spectrum. Cotton bands of both signs are kept. This capture uses illustrative panel data.
```

```{figure} images/10-nmr.png
:alt: The browser NMR panel displaying illustrative chemical-shift data.

The NMR panel. This capture uses illustrative panel data.
```

### `trajectory` and `reaction.path`

Frame-by-frame playback with a play / pause / step strip, a frame slider
and, under it, a **filmstrip**: thumbnails of up to sixteen evenly spaced
frames, the first and the last always among them, rendered in the
background as the section opens. Click a thumbnail to jump to its frame, or
hold the mouse button and drag across the strip to scrub; the thumbnail of
the current frame is outlined. The thumbnails and exported videos re-infer
bonds per frame; the live playback draws the atoms alone. The **Energy
Profile** card plots energy against frame and tracks the current frame, and
a click on it jumps to the frame under the pointer; for a reaction path the
**Reaction Waypoint** card names the reactant, transition state,
intermediates and product as you pass them. <kbd>Space</kbd> plays and
pauses, <kbd>[</kbd> and <kbd>]</kbd> step a frame.

```{figure} images/anim-trajectory.gif
:alt: An illustrative seven-frame geometry trajectory playing beside its energy profile.

Trajectory playback with the energy profile tracking the frame. This capture uses illustrative panel data.
```

```{figure} images/anim-reaction.gif
:alt: An illustrative reaction path advancing through start, barrier and end waypoints.

A reaction path: the waypoint label changes as the band is played. This capture uses illustrative panel data.
```

Periodic reaction paths carry the lattice per frame; the viewer draws the
cell and wraps atoms across periodic boundaries, and a variable-cell path
animates the box with the geometry.

Paths carrying per-frame volumetric data also animate the isosurface.
Fields with negative values, such as orbitals, show both signs: positive
lobes in blue and negative lobes in red. Nonnegative densities keep a single
green surface. The archive's isovalue hint sets the contour magnitude
(default 0.02). If volumes are emitted only for selected frames, the latest
emitted volume stays visible until the next one arrives.

### `vibrations`

Pick a normal mode by frequency from the **Vibrational Modes** list and the
structure oscillates along its displacement vector. Every mode is drawn with
its largest atomic excursion at 0.6 Å, the same convention the terminal
viewer uses, so a stiff X-H stretch and a soft torsion are equally visible
whatever normalisation the producer wrote. The **Displacement amplitude**
slider multiplies that (×1.0 by default), and the card shows the resulting
peak excursion. The same scale applies to an exported vibration video. The
stored displacement vectors are not changed. Mode labels carry the IR
intensity when a companion `spectra.ir` section is present.

**Play** animates the mode and **Pause** freezes it where it is, at the
nearest of the sixteen steps of the **Phase** slider, which scrubs through
one period. The filmstrip under the slider shows the mode at every second
step; a click on a thumbnail, or a drag across the strip, freezes the motion
there. The strip is re-rendered when the mode or the amplitude changes.
<kbd>Space</kbd> plays and pauses, <kbd>[</kbd> and <kbd>]</kbd> step the
phase, <kbd>&lt;</kbd> and <kbd>&gt;</kbd> change the mode, as in the
terminal viewer.

```{figure} images/anim-vibration.gif
:alt: Animation of a formaldehyde normal mode, with the atoms moving along their displacement vectors.

A normal mode, animated about the equilibrium geometry.
```

### `scan.surface`

A relaxed or rigid two-dimensional scan, drawn as a contour surface with the
two scan coordinates on the axes.

### `atom_properties`, `bond_orders`, `topology.qtaim`

**Population Analysis** tabulates whatever the producer wrote: Mulliken,
Löwdin, Hirshfeld charges, spin populations. **Color atoms by charge** tints
each atom in the viewport, red positive and blue negative, and the tint
follows replicas in a replicated cell.

```{figure} images/05-atomic-properties.png
:alt: The formaldehyde population-analysis table and molecular structure in the browser.

Charges as a table and as an atom overlay.
```

`bond_orders` is a table of atom pairs sorted by bond order, with pairs
below 0.05 left out as noise and a CSV export of what is shown, and its
values colour the bonds in the structure. `topology.qtaim` overlays critical points
as spheres and draws bond paths through them. A bond critical point is blue
where the density Laplacian is negative (shared-shell, covalent) and red where
it is positive (closed-shell: ionic, hydrogen bond or van der Waals), and the
details panel tabulates each bond critical point's density, Laplacian and
ellipticity. Clicking a point in the viewport does not select it. QVF does
not fix the units of those values. The table names them for a producer whose convention is documented
(vibe-qc writes e/Å³ and e/Å⁵) and otherwise says the numbers are as the
producer wrote them.

### `scf_history`

One chart: energy against iteration on the left axis, DIIS error and |ΔE|
on a logarithmic axis on the right. **Log scale (|E − E_final|)** and
**Hide initial guess** reshape the energy trace when the first point
dwarfs the rest.

```{figure} images/12-scf-convergence.png
:alt: The formaldehyde SCF convergence chart showing energy and error versus iteration.

SCF convergence, iteration by iteration.
```

### `structure.symmetry`

The producer's crystallographic analysis: space group, Hall number,
international symbol, Wyckoff positions, equivalent atoms.

```{figure} images/11-symmetry.png
:alt: The symmetry details panel displaying an illustrative C2v point-group assignment.

The symmetry panel. This capture uses illustrative panel data.
```

### `citations`, `run.record`, `job.spec`

The embedded BibTeX bundle as a copy-paste block; the **Run Info** panel
with the executed input, the log tail and the system manifest; and the job
specification a [container](jobs.md#qvf-containers) was built from. A
container that has not yet run opens with an amber *pending* chip and a Job
Spec panel saying so.

```{figure} images/13-citations.png
:alt: The calculation citation bundle displayed in the browser details panel.

The citations panel.
```

## Bookmarks, sessions and presentation mode

The **Bookmarks** card saves the current camera, active section, isovalue,
colormap, opacity, representation, material and any computed orbital or
density surface under a name (**Save View**). Selecting a saved bookmark
restores them, recomputing the surface. **Save Session** writes every
bookmark plus the current state to `<name>.vibe-view-session.json` beside
the archive, where `<name>` is the archive's file name without `.qvf` (the
path field overrides it; the periodic auto-save writes the same file, and a
structure with no file on disk uses the cache's auto-session instead), and **Load Session** restores it; the file is plain JSON and can be
version-controlled or generated by a script.

Presentation mode (<kbd>p</kbd> or the toolbar icon) turns the bookmarks into
a full-screen slideshow: <kbd>←</kbd> / <kbd>→</kbd> step through them,
<kbd>Esc</kbd> leaves. Each slide restores its bookmark as above.

## Screenshots, video and high-quality renders

**Export Screenshot** (<kbd>s</kbd>) writes a PNG at a chosen resolution and
scale, optionally with a transparent background, and can save it next to the
archive; **Recent Exports** lists what you have written. **Export Video**
(<kbd>v</kbd>) renders a turntable, a trajectory, a vibration or an orbital
sweep to MP4 or GIF at a chosen frame rate. Both are
server-side renders, so ambient occlusion and the material presets apply to
them exactly as they do to `vibe-view capture`. A screenshot in the
*Publication* view preset and `vibe-view capture --publication` give the
same look.

**High-Quality Raytrace Render** is a path-traced render with physically
based materials, environment lighting and depth of field, meant for a cover
figure. It appears only when the installed VTK build ships OSPRay; the card
is absent otherwise rather than failing on use.

For rendering outside the browser entirely, including POV-Ray and Blender
scenes, see [Figures without a display](headless.md).

(viewer-defaults-hints-from-the-producer)=
## `viewer_defaults`: hints from the producer

A producer can suggest how a file should open by writing `viewer_defaults`
into the manifest: which section to activate, per-section isovalues,
colormaps and opacities, camera bookmarks, and the bands/DOS
[energy window](#bands-dos-panel).
vibe-view applies them before rendering anything. They are hints: every one can be overridden from the
UI, and unknown fields are ignored rather than rejected. The keys are
described in the QVF specification in the
[qvf repository](https://github.com/vibe-qc/qvf).

Two per-section keys belong to the viewer's [volume mode](#volume-mode)
and are read here ahead of the specification: `render`, `"isosurface"`
(the default) or `"volume"`, and `transfer_function`, the function volume
mode draws with. Its `range` is the window in the section's units, `color`
holds a list of `[x, r, g, b]` nodes with the colour in 0 to 1, `opacity` holds
a list of `[x, a]` nodes, and `preset` and `colormap` are optional names; `colormap`
set to `"custom"` keeps the producer's colour nodes until the user picks a
colormap. A function that fails these checks, including a malformed node
list, is ignored; other valid hints still apply and the volume uses its
default transfer function.

The top-level `crossfade` hint selects two volumes with `a` and `b` and
an optional `blend` between 0 and 1. A missing, malformed, non-finite or
out-of-range blend keeps the default 0.5; the volume pair and other valid
hints still apply.

```json
"viewer_defaults": {
  "spin": {
    "render": "volume",
    "transfer_function": {
      "range": [-0.02, 0.02],
      "color": [[-0.02, 0.23, 0.30, 0.75], [0.0, 0.95, 0.95, 0.95], [0.02, 0.71, 0.02, 0.15]],
      "opacity": [[-0.02, 0.5], [-0.002, 0.0], [0.002, 0.0], [0.02, 0.5]],
      "colormap": "custom"
    }
  }
}
```

## Opening files from inside the viewer

The open dialog takes a path to a `.qvf` file or a directory, searches
subdirectories on request, and can open a file as a separate entry in the
Files dropdown rather than replacing the current one. It accepts the same
loose formats as the command line; see [Input formats](formats.md).

## When something looks wrong

The browser-specific failure modes, from a stale server behind a tab that
no longer responds to the mouse to Firefox on macOS 15 refusing to connect
to localhost, are collected in [Troubleshooting](troubleshooting.md).
