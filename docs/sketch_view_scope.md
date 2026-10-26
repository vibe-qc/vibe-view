# vibe-sketch and vibe-view: responsibilities

Maintainer decision, 2026-10-15. This decision is recorded in both repositories;
keep the copies aligned when the agreed scope changes. Requirements below are
the product direction; the implementation status at the end distinguishes
existing functionality from planned work.

## Two complementary working environments

**vibe-sketch specialises in 2D chemical drawing and publication-quality
reaction graphics. vibe-view specialises in 3D structure work for calculations
and detailed inspection of calculated results.**

Both applications must accept molecular input, let users draw and edit
structures, prepare calculation inputs and submit calculations through vq.
Neither application becomes a read-only front end, and ordinary use of one
must not require the other application's UI.

| Responsibility | vibe-sketch | vibe-view |
|---|---|---|
| Input and drawing | Names, SMILES and imported structures; chemical graphs, skeletal formulae, bond orders, charges and stereochemistry | Names, SMILES and imported structures; atoms and fragments positioned and edited in 3D |
| Main editing work | Complex molecules, mechanisms and multi-step reaction schemes; arrows, annotations, layout and figure styles for papers | Coordinates, atom and group selection/dragging, constraints, displacement and pre-optimisation |
| Geometry preparation | Basic cleanup, reference geometry and generation of useful 3D starting structures | Detailed 3D inspection and manipulation before a calculation |
| Calculations | Prepare and submit calculations for species and reaction workflows | Prepare and submit calculations for the current structure |
| Results | Associate calculations and useful result summaries with the species and reaction steps in the drawing | Inspect structures, orbitals, vibrations, spectra, properties and calculated reaction paths |

Overlap in basic input, editing and optimisation is intentional. A full
skeletal-formula and publication reaction editor belongs in vibe-sketch;
the 3D atom editor stays in vibe-view.

## Basic cleanup belongs in vibe-sketch too

vibe-sketch needs a small, predictable cleanup workflow without requiring a
full quantum-chemistry calculation:

1. Use a reference database/table for preferred bond lengths, angles and
   ring geometry, selected by chemical environment rather than element pair
   alone. Relevant context includes bond order, aromaticity, coordination
   and charge. Record the source, version, units, coverage and redistribution
   licence. The actual data source is still to be selected; this decision
   does not approve a particular database or licence.
2. Support **FIRE** as a cleanup/relaxation optimiser. FIRE needs an energy
   and force model: it does not supply chemistry by itself. For 2D cleanup,
   reference-geometry restraints and overlap penalties define the drawing
   objective. For basic 3D relaxation, use an available force field, initially
   the existing RDKit MMFF94/UFF route where it has parameters. These are
   separate objectives, even when the numerical optimiser is shared.
3. Keep publication layout units separate from physical coordinates in Å.
   Normalised drawing bond lengths and chosen 2D angles serve legibility;
   they must not silently become physical 3D distances and angles.
4. Preserve connectivity, bond orders, charge and specified stereochemistry.
   Respect pinned atoms and user-selected regions; keep cleanup undoable.
   Local molecule cleanup must not rearrange the whole reaction scheme or
   move its arrows and annotations without an explicit layout action.
5. Bound the work and report convergence, unsupported chemistry and missing
   parameters honestly. Keep the input drawing/geometry available to undo;
   do not present an unsupported or unconverged structure as optimised.

This basic cleanup supports drawing and preparing starting geometries.
More detailed coordinate work and investigation of a calculated structure
remain vibe-view's focus.

## Shared capabilities and exchange

Share numerical optimisation, geometry-parameter access, input-writing
contracts and queue integration through reusable APIs where practical.
Share tested logic without requiring the other application's UI or importing
its rendering stack. The package location and migration of existing code
remain implementation decisions; a shared service is not yet delivered.

QVF is the family interchange format, governed by the qvf repository. The
intended round trip is: draw in vibe-sketch, prepare or inspect coordinates
in vibe-view, and return the edited geometry to the originating species.
Keep the 2D drawing and 3D geometry as distinct representations. Preserve
species and atom identity, chemical graph, charge, multiplicity,
stereochemistry and calculation provenance through a defined mapping.
Never silently replace a drawn bond order with one inferred from distance.
If the atom mapping cannot be preserved, report the conflict rather than
overwriting the wrong species. XYZ remains a useful coordinate export, but
is insufficient on its own for this complete round trip.

## Implementation status at this decision

* **vibe-sketch:** 2D cleanup and RDKit ETKDG embedding followed by MMFF94/UFF
  optimisation already exist. Database-driven cleanup and FIRE remain
  planned work (roadmap WP 3.6 and 3.7; development issues #17, #18 and #19).
  Existing 2D cleanup is not the proposed database-driven FIRE implementation.
* **vibe-view:** main contains 3D atom/selection dragging, frozen atoms,
  undo/redo, XYZ export, random displacement and optional live relaxation
  using L-BFGS or FIRE with MSINDO/MACE. Source availability does not imply
  inclusion in every released binary.
* **Exchange:** launching vibe-sketch from vibe-view and sending a structure
  to vibe-view exist. Returning a 3D edit to its original sketch species with
  the full mapping described above remains a target, not a completed contract.

This decision adds no new hard dependency and does not select or license a
geometry database. Those choices must be resolved before shipping the new
cleanup implementation.
