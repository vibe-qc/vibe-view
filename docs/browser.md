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
| `-s ID`, `--section ID` | Activate a named section on startup. |
| `--auto-compare` | With several files, start in compare mode. |
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
  and vibrations; an interactive Plotly chart for bands, DOS, spectra and SCF
  history; a table or text pane for the tabular and provenance kinds.
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
| <kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>Z</kbd> | Undo (edit mode) |
| <kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd>, <kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>Y</kbd> | Redo (edit mode) |
| <kbd>Delete</kbd> | Delete the selected atoms |
| <kbd>Esc</kbd> | Leave presentation or edit mode |
| <kbd>←</kbd> / <kbd>→</kbd> | Previous / next slide in presentation mode |
| <kbd>?</kbd> | Show this list in the viewer |

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
the background follows. A separate **Toon / NPR shading** switch adds
outlines. **View preset** bundles a material, a background and label
defaults into three named looks: *Publication (white, clean)*,
*Presentation (dark, glossy)* and *Analysis (flat, labels)*.

**Show atom labels**, **Orthographic projection**, **Dark background**.

**Periodic Replication** (periodic files only). `Nx`, `Ny`, `Nz` replicate
the cell along its lattice vectors. Atoms, bonds, the cell wireframe and any
active isosurface expand together, and only axes flagged periodic are
replicated: a slab tiles in-plane and never along its vacuum direction.
Bonds that cross a cell face are drawn to the nearest periodic image rather
than stretched across the box.

**Auto-reload on file change.** Watch the open file and hot-reload the scene
when its content changes. This is how you follow a running calculation; see
[Jobs and live results](jobs.md).

**Element colour override** (in Settings) changes the colour of one element
everywhere, including headless captures and terminal frames, so a figure
made in any surface agrees with what you saw on screen.

## Measuring

Press <kbd>m</kbd> or the ruler icon, then click atoms: two give a distance,
three an angle, four a dihedral. The **Measurements** card lists what you
have measured and can list every bond distance and angle in the structure at
once. Edit mode and measure mode are mutually exclusive.

```{figure} images/06-measure-distance.png
:alt: Measure mode enabled in the browser structure controls, ready to select atoms and inspect distances.

Measure mode enabled in the structure controls, ready for atom selection.
```

## Clip plane

Enable a clip plane along X, Y or Z and drag its position to cut the scene
open. It applies to atoms and isosurfaces alike, which is the quickest way
to see the inside of a density surface or a cage.

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
  the first when the atom counts match, and reports the RMSD per file. With
  alignment off, structures sit at their stored coordinates.

Clicking any section leaves compare mode.

The **Symmetry** card detects the molecular point group (Schoenflies) from
the inertia tensor and the symmetry operations it finds. A
`structure.symmetry` section written by the producer is a different thing:
that is the producer's crystallographic analysis, shown on its own panel.

### `volume.*` and `basis.ao`

Every volumetric kind (`density`, `orbital`, `spin`, `elf`, `difference`,
`potential`, `rdg`, `generic`, and `basis.ao`) drives the same isosurface
renderer, marching cubes over the stored grid. The **Isosurface Controls**:

| Control | Effect |
|---|---|
| **Volume isovalue** | Slider over the data range. The default comes from the file's `viewer_defaults`, or a kind-specific heuristic: 0.05 e/bohr³ for densities, 0.04 for orbitals. |
| **Volume opacity** | Alpha of the surface. |
| **Show isosurface** | Hide the surface without losing its settings. |
| **Reduce detail (faster)** | Downsample the grid before contouring. Worth it above roughly a million voxels. |
| **Show as 2D slice** | Replace the isosurface with a coloured planar cut through the field, positioned with the clip-plane sliders. Good for nodal planes and bonding regions. |
| **Map ESP onto surface** | With a `volume.potential` section in the file, colour the active density surface by the electrostatic potential at each vertex. |
| **Wrap orbital to cell centre (cyclic cluster)** | For a localized periodic orbital grid, roll the field so the orbital sits in the middle of the cell. Delocalized or already-centred fields are left alone. |

Signed fields (`orbital`, `spin`, `difference`, `potential`) draw both lobes
in contrasting colours. Unsigned fields get one surface.

```{figure} images/02-density.png
:alt: A computed formaldehyde electron-density surface surrounding its ball-and-stick geometry, beside the volume controls.

An electron-density isosurface, translucent over the structure.
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
for steric repulsion, clamped to ±0.05 a.u. With no density in the archive
the surface renders uncoloured.

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

* **Grid points / axis** sets the evaluation resolution.
* **Compute total density** sums the occupied orbitals into a density
  surface and reports ∫ρ dV as an electron-count check.
* Canonical, alpha/beta, natural and localized sets each get their own
  list. Localized orbitals are labelled by the atoms they sit on, because a
  localized orbital has no meaningful energy ordering.
* **Re-localize with** applies a different localization criterion than the
  producer chose. The viewer does not localize anything itself; this shells
  out to vibe-qc in a subprocess and is only offered when vibe-qc is
  importable in the viewer's environment.

The on-demand evaluator covers shells through `l = 3`. An orbital with more
than 0.5 % of its weight in g or higher shells is drawn and the status line
says the surface is incomplete. Periodic wavefunctions are Gamma-point fields
from central-cell atomic orbitals; image tails are not added.

```{figure} images/15-orbital-homo.png
:alt: The positive and negative formaldehyde HOMO lobes evaluated from the stored wavefunction.

An orbital evaluated on demand from `wavefunction.gto`, both lobes drawn.
```

The **Basis Functions** panel does the same for a `basis.ao` section: pick
an atomic orbital and see it.

### `bands`, `dos.total`, `dos.projected`, `dos.coop`, `dos.cohp`

Interactive Plotly charts. A `bands` section plots every band along the
k-path with the Fermi level as a horizontal reference; hover any band for
its energy. When a `dos.total` section is also present the two are drawn as
one figure on a shared, Fermi-referenced energy axis. Projected DOS, COOP
and COHP add per-channel traces you can toggle in the legend.

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
sticks, and it is named in the legend so the convenience is never mistaken
for the data; **X unit** and **Normalize** adjust the axis. Signed spectra
keep their negative bands.

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

Frame-by-frame playback with a play / pause / step strip and a frame slider.
Bonds are re-inferred per frame, so they follow the geometry. The **Energy
Profile** card plots energy against frame and tracks the current frame; for
a reaction path the **Reaction Waypoint** card names the reactant, transition
state, intermediates and product as you pass them.

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

### `vibrations`

Pick a normal mode by frequency from the **Vibrational Modes** list and the
structure oscillates along its displacement vector; **Vibration displacement
amplitude** scales the motion. Mode labels carry the IR intensity when a
companion `spectra.ir` section is present.

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

`bond_orders` is a triangular matrix table with CSV export, and its values
colour the bonds in the structure. `topology.qtaim` overlays critical points
as coloured spheres and draws bond paths through them; click a point for its
density, Laplacian and ellipticity.

### `scf_history`

Energy against iteration on top, DIIS error on a logarithmic axis below.
**Log scale (|E − E_final|)** and **Hide initial guess** reshape the top
pane when the first point dwarfs the rest.

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
colormap and opacity under a name (**Save View**). Selecting a saved bookmark
restores all five instantly. **Save Session** writes every bookmark plus the
current state to a `.vibe-session` JSON file next to the archive, and
**Load Session** restores it; the file is plain JSON and can be
version-controlled or generated by a script.

Presentation mode (<kbd>p</kbd> or the toolbar icon) turns the bookmarks into
a full-screen slideshow: <kbd>←</kbd> / <kbd>→</kbd> step through them,
<kbd>Esc</kbd> leaves. Each slide restores its camera and section.

## Screenshots, video and high-quality renders

**Export Screenshot** (<kbd>s</kbd>) writes a PNG at a chosen resolution and
scale, optionally with a transparent background, and can save it next to the
archive; **Recent Exports** lists what you have written. **Export Video**
(<kbd>v</kbd>) renders a turntable, a trajectory, a vibration or an orbital
sweep to MP4 or GIF with a chosen frame rate and quality. Both are
server-side renders, so ambient occlusion and the material presets apply to
them exactly as they do to `vibe-view capture`.

**High-Quality Raytrace Render** is a path-traced render with physically
based materials, environment lighting and depth of field, meant for a cover
figure. It appears only when the installed VTK build ships OSPRay; the card
is absent otherwise rather than failing on use.

For rendering outside the browser entirely, including POV-Ray and Blender
scenes, see [Figures without a display](headless.md).

## `viewer_defaults`: hints from the producer

A producer can suggest how a file should open by writing `viewer_defaults`
into the manifest: which section to activate, per-section isovalues,
colormaps and opacities, camera bookmarks. vibe-view applies them before
rendering anything. They are hints: every one can be overridden from the
UI, and unknown fields are ignored rather than rejected. The keys are
described in the QVF specification in the
[qvf repository](https://github.com/vibe-qc/qvf).

## Opening files from inside the viewer

The open dialog takes a path to a `.qvf` file or a directory, searches
subdirectories on request, and can open a file as a separate entry in the
Files dropdown rather than replacing the current one. It accepts the same
loose formats as the command line; see [Input formats](formats.md).

## When something looks wrong

The browser-specific failure modes, from a stale server behind a tab that
no longer responds to the mouse to Firefox on macOS 15 refusing to connect
to localhost, are collected in [Troubleshooting](troubleshooting.md).
