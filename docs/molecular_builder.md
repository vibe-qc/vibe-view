# Molecular construction

The construction API prepares starting geometries; it does not predict a
stable conformation or a metal's preferred coordination geometry. Coordinates
are angstroms and rotations are degrees.

`molecular_builder.rotate_bond(structure, [i,j], angle, frozen=...)` cuts the
selected bond and rotates the smaller connected component around i→j. Ties
move the j-side, matching exact parameter edits. Ring bonds and movements of
frozen atoms refuse without modifying the source.

`coordination_builder.coordinate` fills open sites of linear, trigonal planar,
tetrahedral, square planar, trigonal bipyramidal, square pyramidal and octahedral
templates. Choose the metal–donor distance explicitly. Existing ligands orient
the template and remain unchanged; optionally replace directly bonded hydrogen
atoms. Water, ammonia, chloride, carbon monoxide, pyridine, cyanide,
ethylenediamine and oxalate are available. Chelates are placed rigidly: a mismatch
between their donor separation and the selected sites refuses instead of
stretching bonds. Template placement is not a metal force-field calculation.

`molecular_builder.conformers(molecule, count, seed)` uses RDKit ETKDGv3, one
thread and a fixed seed, followed by MMFF94 or UFF when fully parameterised.
Reproducibility is scoped to the recorded RDKit version. It retains optimizer convergence flags, ranks energies in kcal/mol, and writes
all conformers as selectable structures and trajectory frames. Unparameterised
molecules refuse energy ranking. The current-structure adapter requires complete
hydrogens and valences; it does not infer missing atom-local formal charges.

`peptide_builder.peptide(sequence, preset, phi, psi)` supplies standard L-amino
acid topology and imposes alpha-helix (-57,-47), sheet (-135,135), or custom
phi/psi angles after embedding. Peptide omega is trans. Non-proline chains use rigid torsion edits. Internal proline uses constrained
MMFF94 cleanup, with independently measured torsions within 0.1 degree,
ring-angle changes at most 10 degrees, peptide-nitrogen angle sum at least
350 degrees, preserved specified stereocentres and no heavy-atom clash below
1.8 Å beyond two bonds. An infeasible requested angle refuses without returning
a geometry; proline is not excluded categorically.
These APIs require the optional `[smiles]` extra where RDKit is involved.

Methods: [RDKit conformer documentation](https://www.rdkit.org/docs/GettingStartedInPython.html),
[Riniker and Landrum, ETKDG](https://doi.org/10.1021/acs.jcim.5b00654), and
[RDKit FASTA topology API](https://rdkit.org/docs/source/rdkit.Chem.rdmolfiles.html).

## Builder panel

The **Molecular builder** card provides typed or slider bond rotation (slider
release commits one undo step; the bond stays selected for further steps),
coordination-site filling on a selected metal,
sequence construction, current-molecule conformers and a searchable library of
42 small fragments with generated 2D thumbnails. **Open** starts a new molecule;
**Attach** replaces one explicit hydrogen at the selected atom and one at the
fragment's first atom. Atom/bond edits preserve explicit connectivity. Unfreeze
atoms before topology-changing operations; rigid rotation can retain frozen
atoms on its axis. All builder edits stop automatic optimization.

Sequences use the RDKit FASTA topology and retain atom/residue identities.
Choose **single** (the compatible default) or **duplex** for DNA/RNA.
Single strands use the backbone construction below; duplexes add the
antiparallel reverse complement as a distinct chain. Neither mode predicts
secondary structure. Local heavy-atom templates are CC0 wwPDB coordinates from
[1BNA](https://www.rcsb.org/structure/1BNA) (A6 adenine, A9 cytosine,
A2 guanine, B20 thymine) and the [1EHZ](https://www.rcsb.org/structure/1EHZ)
RNA stem (A5 adenine, A2 cytosine, A3 guanine, A6 uracil).
Hydrogens are added from explicit topology. DNA epsilon/zeta/alpha values
(-178.3/-96.8/-61 degrees) follow the BI means in
[Svozil et al. (2008), Table 3](https://doi.org/10.1093/nar/gkn260);
RNA values (-150/-73/-65 degrees) follow
[Schneider et al. (2004), Table 2](https://pmc.ncbi.nlm.nih.gov/articles/PMC390331/).
Local sugar conformations begin from the observed templates. Joining uses a 1.607 Å O3′–P bond, 120° C3′–O3′–P angle,
104° O3′–P–O5′ angle and tetrahedral nonbridging phosphate oxygens at 1.48 Å.
A staged constrained MMFF94 cleanup removes base clashes while retaining
alpha/epsilon/zeta and each template delta torsion within 0.1 degree. Other
local geometry may relax. The result refuses if optimization fails, a specified
stereocentre changes, a heavy bond falls outside 1.1–1.85 Å, or nonbonded heavy
atoms separated by more than two bonds remain closer than 1.8 Å. These are
construction safeguards, not an equilibrium-structure claim.
The resulting geometries still need inspection before calculation.

### Complementary duplexes

The entered sequence is chain A in the 5′→3′ direction. Chain B is its reverse
complement, also numbered 5′→3′: A_i pairs with B_(n+1−i). The two chains have
separate phosphodiester topology and no covalent bonds between them. Only
unambiguous A/C/G/T (DNA) or A/C/G/U (RNA), up to 100 base pairs, are accepted.
Duplex cleanup runs in the background and opens a new structure when accepted.
The default RDKit FASTA protonation is retained; choose the required protonation
and charge before a subsequent calculation.

Duplexes repeat an experimentally derived rigid helical step, fitted jointly to
both strands: 1BNA A5/B20 → A6/B19 for B-family DNA (3.129153 Å helical rise,
37.444813° twist); 1EHZ A3/A70 → A4/A69 for A-family RNA (3.005436 Å,
32.778421°). These particular measured steps are not universal ideal B/A
parameters. Sugar delta conformations remain those of the nucleotide templates.
The matrix rebuilding formulation is described by
[Lu and Olson (2003)](https://doi.org/10.1093/nar/gkg680); no 3DNA software or
parameters distributed under its licence are included.

Watson–Crick base-pair coordinates come separately from 1BNA A5/B20 (AT) and
A4/B21 (GC), or 1EHZ A5/A68 (AU) and A3/A70 (GC). They are aligned to the
common pair frame. Each base is rigidly rotated to a common pair plane while
preserving all its internal distances: sequence-specific experimental buckle
and propeller cannot safely be repeated at arbitrary sequence boundaries.
A proper half-turn exchanges purine/pyrimidine strand assignments. The JSON
resource retains the original CC0 coordinates, residue identifiers, and fitted
rotation/translation so the construction is reproducible.

Base heavy atoms remain fixed during MMFF94 cleanup; only backbone/hydrogen
coordinates relax, with interstrand interactions enabled. The same convergence,
0.1° sugar-delta, heavy-bond, heavy-clash and stereochemistry checks used by the
single-strand builder are retained. Each intended AT/AU pair must have two and
GC three donor/acceptor contacts, at 2.4–3.5 Å with D–H···A angles at least 120°.
Moved fixed bases or failed checks return no accepted structure. Recorded
`builder_pairing`, `builder_complement` and `builder_helix_step` provenance follows
the structure into QVF. These are inspectable starting geometries; this method
does not predict solvent, ions, sequence-dependent helix parameters, or stability.

Conformer generation retains all requested frames and records energy units,
force field, seed and individual minimizer convergence. Select a structure
section or trajectory frame to inspect the energy-ranked alternatives.
Conformer energies are force-field energies, not quantum results.
