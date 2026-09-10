# Building and editing structures

The browser and desktop surfaces are an editor as well as a viewer. You can
change a structure atom by atom, grow it from fragments, build one from a
name or a SMILES string, replicate a cell or cut a slab, and hand the result
to a calculation: exported as an input script, or submitted to a queue
without leaving the window.

Everything on this page runs in `vibe-view open` and `vibe-view desktop`.
The terminal and headless surfaces read structures; they do not edit them.

## Edit mode

Press <kbd>e</kbd> or the pencil icon. The **Atom Editor** card opens and
the viewport starts taking clicks as edits:

* **Select** an atom by clicking it; a selected atom carries an orange
  wireframe highlight. Click again to deselect, <kbd>Delete</kbd> to remove
  the selection.
* **Add** an atom by clicking empty space. The element comes from the
  *Element used for newly added atoms* picker.
* **Change every selected atom's element** with the element picker in the
  card.
* **Undo / redo** with <kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>Z</kbd> and
  <kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd>. Every edit is
  one undo step.

Bonds are recomputed from covalent radii after each edit. Edits compound in
place: the geometry you export or submit is the geometry as edited, and the
original archive on disk is not touched.

Edit mode and measure mode are mutually exclusive; <kbd>Esc</kbd> leaves
either.

## Fragments and hydrogens

The **Fragment Library** attaches a group to the selected atom, or places it
at the molecule's centre when nothing is selected:

| Fragment | Group |
|---|---|
| `CH3` | methyl |
| `NH2` | amino |
| `OH` | hydroxyl |
| `COOH` | carboxyl |
| `Ph` | phenyl |
| `CHO` | aldehyde |
| `NO2` | nitro |
| `CN` | cyano |
| `CF3` | trifluoromethyl |
| `SO3H` | sulfonic acid |

**Add Hydrogens** saturates the open valences of the selected atoms with
tetrahedral geometry where it applies. The same operation is available for a
whole file from the command line:

```sh
vibe-view h-add structure.qvf -o structure_h.qvf
```

## Building from a name or a SMILES string

The **Build molecule** dialog takes either:

* **A molecule name.** Geometries come from the curated structure database
  of the `vibeqc_naming` package, which ships with vibe-qc. When that
  package is not importable in the viewer's environment the dialog says so;
  a standalone viewer install does not have it.
* **A SMILES string.** Needs the `[smiles]` extra (RDKit). The geometry is
  an RDKit ETKDG embedding with no force-field cleanup: a chemically
  sensible starting point, not an optimised structure.

The **Structure Library** card remembers what you built, and lets you pin
favourites to reuse.

From Python the SMILES route is a function that returns an in-memory
archive, so it can be handed straight to the viewer:

```python
from vibeview import launch_qvf
from vibeview.converters import smiles_to_qvf

launch_qvf(smiles_to_qvf("c1ccccc1"))                 # benzene
launch_qvf(smiles_to_qvf("CCO", add_hydrogens=False))
```

There is no `vibe-view` subcommand for SMILES and no SMILES file format the
open dialog accepts; the dialog and the function are the two routes.

## Live optimisation while you build

**Auto-optimize**, in the Atom Editor card, relaxes the sketch in the
background after every pause in editing and streams each optimiser step into
the viewport, so the atoms settle toward a relaxed geometry as you work. The
status line shows the step, energy and maximum gradient, then *relaxed in N
steps*. Editing again mid-relax cancels and reschedules, and each relax run
is one undo entry.

This needs vibe-qc importable in the viewer's environment: the default
engine is its MSINDO semi-empirical model, and when its `[mace]` extra is
installed an **Engine** picker offers the MACE foundation potential as well.
Without vibe-qc the switch snaps back off with the reason and the editor
works exactly as before. Structures beyond 80 atoms are skipped to keep the
background evaluations interactive.

Selected atoms can be **frozen** so a relaxation moves everything but them.

## Periodic structures

**Build Supercell** replicates the cell `Nx × Ny × Nz` into a new structure;
the command-line equivalent is:

```sh
vibe-view supercell si.qvf --nx 2 --ny 2 --nz 2 -o si_2x2x2.qvf
```

The crystal-builder helpers behind it are importable for scripts: a
space-group search, conversion between a lattice matrix and cell parameters,
Miller-plane slab cutting with a vacuum gap, and supercell replication.

```python
from vibeview.crystal_builder import (
    search_space_groups, cell_from_abc, abc_from_cell, miller_slab, replicate_cell,
)

search_space_groups("Fm-3m")                          # [{'number': 225, ...}]
cell = cell_from_abc(5.43, 5.43, 5.43, 90, 90, 90)
a, b, c, alpha, beta, gamma = abc_from_cell(cell)
new_cell, slab_atoms = miller_slab(cell, atoms, hkl=(0, 0, 1), n_layers=3, vacuum=15.0)
super_cell, super_atoms = replicate_cell(cell, atoms, nx=2, ny=2, nz=2)
```

Cell edges are only drawn along periodic axes, so a slab gets its in-plane
parallelogram and never a box around the vacuum.

## From structure to calculation

### The Calculation Parameters panel

With a structure active, the right drawer shows **Calculation Parameters**:
method (RHF, UHF, RKS, UKS, RMP2, UMP2), functional (PBE, PBE0, B3LYP, BLYP,
BP86, TPSS, M06-2X, ωB97X-D, CAM-B3LYP, LDA, r²SCAN, HSE06), basis set
(STO-3G through aug-cc-pVTZ and the def2 family), charge, multiplicity, and
the calculation type: single point, geometry optimisation, frequencies, all
three, or a periodic single point for a periodic structure.

**Export vibe-qc input (.py)** writes a vibe-qc input script with those
settings and the geometry as edited. The same generator is behind
`vibe-view export FILE -f py`, which additionally reuses the archive's own
provenance so the regenerated script reproduces the original calculation,
and carries the lattice so a periodic file exports a periodic system rather
than silently degrading to a molecule.

The panel's settings are also what a queue submission uses.

### Submitting to the queue

**Submit to vq cluster** sends the current structure to a
[vibe-queue](https://github.com/vibe-qc/vibe-queue) daemon with the
parameters above and opens the Job Manager so the job appears immediately.
That needs the `[queue]` extra and vibe-queue itself; see
[Queue integration](queue.md) for the install and [Jobs and live
results](jobs.md) for what happens after you press the button.

### Reading an input script back in

The reverse direction works too. Open a vibe-qc Python input directly:

```sh
vibe-view open input.py
```

The parser walks the file's syntax tree without executing it and extracts
the structure and the run parameters, so the structure appears in the
viewport and the Calculation Parameters panel is pre-filled from the script.
It understands vibe-qc's input syntax and the input library's builder
functions; it is not a general Python interpreter, and arbitrary code in an
input resolves to nothing rather than running.

## What the file on disk sees

None of this writes to the archive you opened. To keep an edited structure:

* **Export** it from the toolbar as XYZ, CIF, CML, JSON or a Python input,
  or as a mesh or scene; see [Figures without a display](headless.md#export)
  for the formats.
* **Save a session** to keep views of it; see
  [Bookmarks and sessions](browser.md#bookmarks-sessions-and-presentation-mode).
* **Submit** it, in which case the settled result comes back as its own
  archive.
