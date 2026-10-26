Python tour of the building, inspection and preparation APIs

tour.py walks one water dimer through the APIs that the browser's builder,
coordinate editor, inspection, figure and scene cards use: selections and
measurements, hydrogen bonds and contacts, VdW/SAS/SES surfaces, exact
geometry edits saved as QVF, a Z-matrix round trip, a replayable two-model
scene, spectrum broadening and a native xtb input bundle.

    python tour.py [OUTPUT_DIR]

It needs only the core vibe-view install; the solvent-excluded surface also
needs the "surfaces" extra and is skipped without it. The manual's tutorial
"Build, inspect and present" explains each step.

This example was written for the vibe-view project and is distributed under
the Mozilla Public License 2.0 with vibe-view.
