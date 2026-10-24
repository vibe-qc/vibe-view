# Molecular surfaces and geometric interactions

Open **Molecular surfaces and interactions** in the structure controls. Choose
settings, then **Apply inspection**. The panel reports surface area (Å²), enclosed
volume (Å³), grid spacing and an interaction table. Screenshot capture uses these
same polygonal actors; solid, mesh and dot styles also apply to existing field
isosurfaces. Translucency is controlled by surface opacity. Browser transport uses
ordinary vtk.js geometry; live-browser qualification is tracked separately.

## Selection

Both tools use the same finite input-cell selection:

- `all` or `none`
- `index 1,2,3` (one-based atom numbers)
- `chain A` or `residue A/24`
- `within 4 of (chain A)` (Å, inclusive, includes the seed)

A surface is built from only the selected atoms. Interactions are restricted to
that selection, or to pairs crossing it and an optional partner selection. No
periodic images or crystal neighbours are implicitly included. A molecular
surface around a cut-out selection is its own boundary, not a cropped portion
of the whole molecule's surface.

## Surfaces, colours and numerical meaning

VdW is the boundary of the union of atom-radius spheres. SAS expands every
radius by the probe radius (default 1.4 Å). SES first constructs the expanded
union and then erodes it by the probe radius using a Euclidean distance
transform. The boundary location is interpolated from the sphere-union signed
distance field before contouring. This is an independent implementation of the
published distance-transform construction of [Xu and Zhang (2009)](https://doi.org/10.1371/journal.pone.0008140),
using NumPy, BSD-licensed SciPy and BSD-licensed VTK/PyVista. No EDTSurf or GPL
source code is used. SES requires the optional `surfaces` extra. VdW and SAS do not.

SES includes reentrant regions; it is not SAS with a different label. Internal
void boundaries large enough to accommodate the probe can remain. Grid spacing
(default 0.25 Å) is an explicit numerical approximation, not an analytical
Connolly surface. Small cavities below the grid resolution can disappear.
Area is the sum of mesh triangle areas; volume is the signed closed-mesh volume
including cavity orientation. Open meshes refuse a volume. A 16-million-voxel
limit refuses oversized requests rather than silently coarsening them.

Element and chain colours match the atoms (CPK by element, the ribbon palette
by chain) and have categorical keys; each surface vertex takes its nearest
atom's colour, so a seam between two categories blends only those two colours.
The browser's colour bar shows the category index, while screenshots and
exports label each colour. Atomic charges (Mulliken,
Löwdin, Hirshfeld, IAO) require aligned stored atom properties. Hydrophobicity
uses the [Kyte–Doolittle scale](https://web.expasy.org/protscale/pscale/Hphob.Doolittle.html)
for standard amino-acid residue names. Vertex attribution uses the closest atom
sphere surface. Stored `volume.potential` is trilinearly sampled in its full
lattice basis, including skew grids and bohr-to-Å conversion. Missing properties,
nonfinite values and insufficient potential-grid coverage refuse colouring;
they are not replaced with invented zero values.

### Numerical checks

Tests fix tolerances before measuring: 5% for isolated-sphere VdW/SAS/SES area
and volume at 0.1 Å, and 3% for analytic overlapping-sphere VdW/SAS unions.
For deposited crambin **1CRN**, using the viewer's radii, 0.25 Å VdW/SAS meshes
are compared with independent 4096-point-per-atom Shrake–Rupley area sampling
and 262144-point deterministic Sobol volume sampling, at 5% tolerance. The
reference sampling uncertainties are retained with the fixture.

| 1CRN, 0.25 Å | Area Å² | Reference area Å² | Volume Å³ | Reference volume Å³ |
|---|---:|---:|---:|---:|
| VdW | 4108.132 | 4248.324 | 3568.034 | 3570.137 |
| SAS, probe 1.4 Å | 2921.604 | 2971.440 | 8355.575 | 8349.968 |
| SES, probe 1.4 Å | 2295.159 | 2307.259 | 4779.138 | 4705.374 |

The SES protein reference is the same construction at **0.1 Å**, with a 10%
coarse/fine tolerance. It demonstrates discretization convergence, not an
independent analytical protein-surface benchmark. Do not interpret these
case-specific checks as a universal error bound; refine spacing for quantitative
work. The fixture records and explicit regeneration script are under
`tests/fixtures/inspection/`.

## Interaction definitions

These are geometric candidates, not computed interaction energies or inferred
protonation states. Explicit hydrogens are required for hydrogen bonds. The
existing simple hydrogen-bond toggle retains its older 3 Å/150° display rule;
use this panel when choosing criteria or obtaining a table.

| Kind | Definition |
|---|---|
| H-bond | N/O donor with a bonded explicit H, N/O acceptor, D–A < configured distance (default 3.5 Å), D–H–A ≥ configured angle (110°). |
| Contact | Nonbonded heavy-atom distance ≤ summed VdW radii + configured gap (0.5 Å). |
| Clash | Nonbonded heavy-atom VdW overlap ≥ configured overlap (0.4 Å). |
| Salt bridge | Oppositely charged atoms < 4 Å. Standard ASP OD1/OD2, GLU OE1/OE2, LYS NZ, ARG NH1/NH2 annotations plus explicitly selected charged ligand atoms. |
| Halogen bond | Covalent C–Cl/Br/I donor to O, X–O < summed VdW radii, C–X–O ≥ 120°. |
| Parallel π-stack | Aromatic ring centres < 7 Å, unoriented plane-normal angle < 30°, each normal within 45° of the centre–centre direction. |

H-bond, salt and parallel-stack definitions follow the published
[GetContacts criteria](https://getcontacts.github.io/interactions.html).
Halogen candidates follow the short directional contact survey of
[Auffinger et al. (2004)](https://doi.org/10.1073/pnas.0403723101).
The halogen definition deliberately identifies that survey's candidates, not
all chemically possible halogen acceptors. Fluorine is excluded. Covalent and
1–3 neighbours are excluded from contact/clash lists. Supplied bonds take
precedence; inferred connectivity can obscure exceptionally short clashes,
so provide correct bonds when investigating those cases.

PHE/TYR/TRP residue annotations supply protein aromatic rings. Enter ligand
aromatic rings as complete atom selections separated by `;`; planarity alone
never establishes aromaticity. Explicit ligand charge selections represent
formal charged sites, not fractional population-analysis charges. Review
protein protonation assumptions; the standard residue-name convention is not
a pKa calculation. π candidates require complete selected rings.

Tests include a straight water donor–H–acceptor, collinear and rejected bent
C–Cl···O pairs, salt and parallel-ring examples, and deposited **1STP** biotin O3
contacts to ASN23 ND2, SER27 OG and TYR43 OH (2.992381, 2.771046, 2.584910 Å).
The latter are contacts, not claimed hydrogen bonds: the input lacks explicit H.

## Python use

```python
from vibeview.molecular_inspection import add_inspection
from vibeview.molecular_selection import select, within
from vibeview.molecular_surfaces import build_surface
from vibeview.interactions import find_interactions

surface = build_surface(structure, kind="ses", probe=1.4, spacing=0.25)
print(surface.area, surface.volume)
rows = find_interactions(structure, selection="chain A", partner="residue B/1",
                         kinds=("hbond", "contact", "clash"))
result = add_inspection(plotter, structure,
                        {"surface": "sas", "style": "mesh", "colour": "chain"}, reader)
```

Python interaction atom indices and explicit charged/ring tuples are zero-based;
the panel and selection expressions display one-based numbers. `within()` is the
shared selection operator intended for scripting and subsequent selection tools.
