# Molecular inspection fixtures

Unmodified coordinate downloads from RCSB PDB:
- https://files.rcsb.org/download/1CRN.pdb — crambin, small whole-protein surface check.
- https://files.rcsb.org/download/1STP.pdb — streptavidin/biotin contact check.

Downloaded 2026-10-22. Coordinates retain deposited atom/residue labels. Tests
use the viewer converter's model/alternate-location policy. Chemical radii are
vibe-view's VdW table, not a reference program's default radii.

`protein-reference.json` retains independent sphere-sampled area and Sobol volume
references for VdW/SAS, and a fine-grid SES convergence reference. Regeneration:
run `python tests/fixtures/inspection/regenerate_reference.py` from the repository
root in the existing test environment; output is `protein-reference.json` in
that directory. This explicit calculation is not run as an extra pytest test.
The 0.1 Å protein SES calculation requests up to 64 million voxels and can use
substantial memory. Regular regression tests only build 0.25 Å meshes.
