# What vibe-view can show you

A QVF archive is a set of independent **sections**, each declaring a *kind*.
The viewer keeps one explicit registry of the kinds it renders
(`vibeview.kinds.SUPPORTED_KINDS`), and every file you open prints a banner
listing its sections and what will happen to each one:

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  QVF file: h2o.qvf                                                           ║
║  Source:   vibe-qc 0.16.0 — RKS/PBE                                          ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Section ID          Kind                         Status                     ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  struct_0            structure                    rendered                   ║
║  vol_homo            volume.orbital               rendered                   ║
║  x_orca_gbw          x_orca.gbw                   skipped, vendor namespace  ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

That banner is the honest answer to *"is my data actually being displayed?"* —
nothing is silently dropped. A section is reported as one of:

| Status | Meaning |
|---|---|
| `rendered` | The viewer has a renderer for this kind |
| `skipped, not yet rendered` | The format defines the kind and the producer emits it, but no renderer exists yet |
| `skipped, unsupported` | The kind is not in the registry at all |
| `skipped, vendor namespace (X)` | An `x_<vendor>.*` extension section, by design not portable |
| `error, sha256 mismatch` | The section's payload does not match its declared digest, so it is not used |

## Structure and geometry

```{figure} images/14-structure-h2co.png
:alt: Formaldehyde in the browser viewer, with red oxygen, grey carbon and white hydrogen atoms beside the structure controls.
:width: 100%

A formaldehyde structure from a vibe-qc calculation, with the light background selected.
```


| Kind | What you get |
|---|---|
| `structure` | CPK-coloured atoms in ball-and-stick, space-filling or wireframe. Optional chains, residues and secondary structure drive cartoon rendering for biomolecules. |
| `bonds` | Folded into the structure section and drawn by the structure renderer — explicit connectivity is never dropped. |
| `structure.symmetry` | Detected point group, with the symmetry elements drawn in place. |
| `atom_properties` | Per-atom scalars (charges, spin densities, …) as a table and as atom colouring. |
| `bond_orders` | Mayer / Wiberg analysis. |
| `topology.qtaim` | Critical points and bond paths. |

Structures can be measured (distances, angles, dihedrals), clipped by a plane,
aligned across files with Kabsch superposition, replicated into a supercell,
and edited — the browser and desktop surfaces carry a full atom editor with
undo/redo, a fragment library, a crystal builder and SMILES-based construction
(via the `[smiles]` extra).

## QTAIM topology

A `topology.qtaim` section adds critical points and bond paths to the molecular
scene. Its table shows density, the density Laplacian and ellipticity where
the producer supplies them.

```{figure} images/19-qtaim.png
:alt: Three blue bond critical points and their bond paths on formaldehyde, above the QTAIM density and Laplacian table.
:width: 100%

The actual QTAIM viewer with an explicitly illustrative topology fixture; the values shown are demonstration data, not a calculated topology.
```

## Volumetric fields

```{figure} images/02-density.png
:alt: The formaldehyde electron-density isosurface surrounding the ball-and-stick geometry, with isovalue and opacity controls.
:width: 100%

Computed electron density from the formaldehyde showcase archive.
```


All of these have an adjustable isovalue and colormap. Payloads are
**lazy-loaded**: the binary grid is read on first activation, while the
initial file open reads the manifest and section metadata. A larger grid
still takes time to load and contour when selected.

**Try it:** run `vibe-view info FILE.qvf`, note the ID of a `volume.*`
section, and select it in the sidebar. Lower the isovalue to include more of
the field. A signed orbital or difference field can have two lobes; the
colours represent sign, not two different substances.

| Kind | What it is |
|---|---|
| `volume.density` | Electron density |
| `volume.orbital` | Molecular orbitals, with signed-lobe rendering |
| `volume.spin` | Spin density |
| `volume.difference` | Difference densities |
| `volume.elf` | Electron localization function |
| `volume.potential` | Electrostatic potential |
| `volume.rdg` | Reduced density gradient — non-covalent interaction analysis |
| `volume.generic` | Anything else on a grid |

## Wavefunctions, evaluated on demand

```{figure} images/15-orbital-homo.png
:alt: Positive and negative lobes of the formaldehyde HOMO in the browser, with the molecular-orbital evaluation controls.
:width: 100%

The formaldehyde HOMO, evaluated from the wavefunction stored in the QVF.
```


`wavefunction.gto` carries the GTO basis and MO coefficients rather than a
pre-computed grid, so the viewer can evaluate **any** orbital on demand
instead of only the ones the producer chose to ship. That covers canonical,
alpha/beta, natural and localized sets, plus computed density.

Two limits are worth knowing: on-demand evaluation covers shells through
`l = 3`, and periodic Gamma-point fields omit image-AO tails. The status line
says so when a surface is incomplete rather than quietly drawing a wrong one.

The related `basis.ao` kind carries atomic-orbital data and is lazy-loaded on
the same terms.

## Periodic systems

```{figure} images/08-bands-dos.png
:alt: Five band curves along Gamma–X–L and a density-of-states plot share a Fermi-referenced energy axis in the browser result panel.
:width: 100%

An illustrative band/DOS fixture demonstrates the combined panel; these curves are not a material calculation.
```


| Kind | What you get |
|---|---|
| `bands` | Electronic band structure, interactive, with the Fermi level marked and an adjustable energy window |
| `dos.total`, `dos.projected` | Total and projected density of states, on the same energy window |
| `dos.coop`, `dos.cohp` | Crystal orbital overlap / Hamilton populations |
| `phonon_bands`, `phonon_dos` | Phonon dispersion and density of states |
| `fermi_surface` | The Fermi surface in reciprocal space |
| `equation_of_state` | E(V) curves and fitted parameters |

## Spectra

```{figure} images/20-ir-spectrum.png
:alt: The formaldehyde IR spectrum in the browser result panel, with frequency on the horizontal axis and intensity on the vertical axis.
:width: 100%

The computed IR spectrum from the formaldehyde showcase archive.
```


IR, UV/visible, Raman, ECD, VCD and generic spectra use interactive plots.
NMR and EPR also expose their nucleus/site and tensor data in dedicated
panels. Availability depends on which sections the producer stored.

**Read the axes first:** frequencies, excitation energies, shifts and field
values are different quantities. A broadening curve is a display aid laid
over the stored transitions; it is not an additional calculated result.
The [browser reference](browser.md) describes the controls.

## Things that change over a coordinate

```{figure} images/04-vibrations.png
:alt: The formaldehyde normal-mode viewer with mode frequencies and animation controls beside the molecular geometry.
:width: 100%

A normal mode from the formaldehyde calculation; playback moves the atoms along its displacement vectors.
```


| Kind | What you get |
|---|---|
| `vibrations` | Animated normal modes with a frequency selector |
| `trajectory` | Frame-by-frame geometry-optimisation playback with an energy plot |
| `reaction.path`, `reaction.waypoints` | Reaction paths, NEB images |
| `scan.surface` | Relaxed and rigid scan surfaces |
| `scf_history` | SCF convergence, iteration by iteration |

`vibe-view animate` renders any of the animated kinds to MP4 or GIF without a
display.

## Provenance

| Kind | What you get |
|---|---|
| `citations` | The references the producer says this calculation should cite |
| `run.record` | What produced the file, with what settings |
| `job.spec` | The job that was submitted |

## Which surface renders what

The archive is shared; the capabilities below are specific to each surface.
“Stored volume” means a sampled grid in the file. “On demand” means evaluating
an orbital from its basis and coefficient data.

| Task | Browser / desktop | TUI | One-shot `show` | Headless / Python |
|---|---|---|---|---|
| Structure and stored volume | Interactive scene | Interactive text scene | One text frame | PNG with `capture` or SDK |
| On-demand orbital or density | Wavefunction controls | Surface table | Not evaluated | Internal wavefunction API; see [headless](headless.md) |
| Bands, DOS, spectra, SCF | Interactive plots | Terminal charts | Terminal charts | Bands PNG; other chart helpers write HTML |
| Scientific tables | Result panel | Data panes | Use `table` instead | `table` CSV/JSON or SDK `get_table` |
| Editing and builders | Available | Read-only | Read-only | Separate structure commands; no interactive editor |
| Trajectories and normal modes | Playback | Playback | A selected frame | `animate` writes frames, GIF or MP4 |
| Graphics requirement | Browser WebGL | No OpenGL | No OpenGL | PNG needs offscreen OpenGL; text/data export does not |

`vibe-view capture` accepts a structure, stored `volume.*` / `basis.ao`, or
bands section. It does not evaluate orbitals on demand or capture every
kind in this catalogue. The [capture API](headless.md#the-capture-api) lists
other export routes, including the orbital energy diagram. Notebook image
capture has the same offscreen graphics requirements as any PNG capture.

The terminal surfaces deserve the emphasis: `vibe-view tui` gives you the 3-D
viewer, the charts and a selectable surface table as Unicode braille, over
plain SSH, with no display server, GL or X forwarding. It also plays
animations — step a trajectory or a normal mode with `[` / `]`, or press play.
`vibe-view show` renders one frame and exits, and needs nothing beyond the
core install.

Each surface has its own page: [the browser viewer](browser.md) and
[building and editing](editing.md), [terminal mode](terminal.md),
[the desktop app](desktop.md), and [figures without a display](headless.md)
for the headless commands and the capture API.

## Import and export

**Read:** QVF, vibe-qc Python inputs (`.py`), XYZ, CIF, Cube, PDB, Mol2,
Gaussian input, GRO, SDF/Mol — built in. The `[ase]` extra adds the long tail
via `ase.io.read`. Third-party importer plugins are supported.
`vibe-view formats` lists what *your* installation can read, and `--json`
makes that scriptable.

**Write:** XYZ, CIF, CML, JSON, Python input, OBJ, glTF, POV-Ray scene,
Blender scene, standalone HTML, SVG, PDF — twelve formats through
`vibe-view export`. Volumes and figures go out as PNG through `capture` and
`batch`, and animations as MP4 or GIF through `animate`.

**Convert:** `vibe-view import` turns any readable loose file into a
persistent QVF archive, which is worth doing when you want the integrity
guarantees and the section model rather than a bare geometry.

What each importer keeps, and how to write one for a format that is not in
the list, is on [Input formats and interoperability](formats.md). Cartoon
rendering, chains, residues and B factors are on [Biomolecules](biomolecules.md).
