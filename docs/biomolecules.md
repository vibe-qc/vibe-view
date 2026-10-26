# Database structures, ensembles and sequences

The **Database & sequence** card opens structures through an explicit **Fetch
and open** action. Choose RCSB and a four-character PDB ID (`1CRN`), AlphaFold
and a UniProt accession (`P69905`), or a PubChem CID/name (`702`, `ethanol`).
RCSB and AlphaFold supply mmCIF; PubChem is requested explicitly as 3D SDF.
The field is sent only to the selected public service. No local structure,
filename, account name or application state is uploaded. Do not enter personal
information. Opening a local file, previewing the panel and starting the viewer
never contact these services. Offline/not-found errors leave the current
structure intact; local files remain available. Responses are limited to
32 MiB and requests time out after 20 seconds. Ambiguous AlphaFold predictions
or PubChem conformers require a more specific identifier or explicit local
file download; the viewer does not silently choose one.

## Files, models and alternate locations

PDB remains built in. PDBx/mmCIF (`.mmcif`, `.mcif`, and `_atom_site.` data in
`.cif`) uses the optional [Gemmi](https://gemmi.readthedocs.io/en/latest/mol.html)
reader, licensed MPL-2.0. Install `vibeview[biomolecule]` using the interpreter
that runs the viewer; a missing dependency gives the source-install-aware
installation command. Ordinary small-molecule crystallographic CIF remains
on its existing parser.

Every model is a separate selectable structure. **Model / alternate location**
selects the model and an alternate label. `first` preserves the historical PDB
first-location display. A labelled choice uses that location when present,
common atoms otherwise; atoms that have neither fall back to their first
location, and the panel reports the fallback count. This is a global alternate
label selector, not an assertion that all residue-level disorder is correlated.
It does not enumerate every combinatorial mixture of independently disordered
residues. Original records (including every alternate coordinate, occupancy,
insertion code, author identity and hetero flag), selected-record maps and the
original source text are retained in the archive's open provenance block.

Models with identical ordered atom identities also appear as trajectory frames,
separately for each alternate choice. Existing playback controls animate them.
Identity differences produce an explicit notice and separate structures, not a
forced trajectory or discarded atoms. Malformed PDB coordinate records remain
in the original source with a warning identifying the line; they are not
silently rendered as zero coordinates. The old unknown-element convention
(atomic number zero) remains supported.

The crystal unit cell is retained where present. AlphaFold/molecular unit-cell
placeholders are not invented periodic boxes. All coordinates are angstrom.

## Residue selection and secondary structure

The sequence card lists **observed coordinate-bearing residues**, with author
chain, residue number and insertion code. It is not a reconstruction of missing
SEQRES/entity-polymer residues. Click a residue to select/highlight its atoms;
3D selections are reflected in outlined sequence entries. Ligands and waters
are individually selectable, and **Select ligands** / **Select waters** select
those complete groups. Protein letters are displayed beside residue labels;
unknown polymer residues use `X`.

Helices are red, sheets blue and coils/unknown assignments grey. Deposited HELIX/SHEET assignments
are preferred; otherwise the existing CA-geometry assignment is used. This is
not a DSSP calculation. QVF's current typed generic residue API lacks insertion
codes: this panel reads retained source metadata to keep those residues separate.
Other generic residue/cartoon consumers may still group insertion-code residues;
no author renumbering or vendored schema change is made here. After atom editing,
sequence operations refuse stale metadata until a labelled structure is reopened.

## Sequence-based superposition

Choose a reference among open structures and specify current/reference chain
IDs. **Align chains → molecular copy** aligns their observed protein sequences
using global dynamic programming: match +2, mismatch −1, each gap −2; unknown
`X` matches score as mismatches. Ties prefer diagonal, then deletion, then
insertion. This is a linear-gap Needleman–Wunsch alignment, not a homology
significance test or structural outlier search. Up to four million alignment
cells are allowed; larger chains require an explicit smaller selection.

Aligned positions with a CA in both chains enter a proper-rotation Kabsch fit.
At least three non-collinear pairs are required. The panel reports each residue
pair, CA indices, omitted pairs, rotation, translation and RMSD in angstrom.
Mismatched residue types can pair through the sequence alignment and are marked
as such; nothing is discarded based on a desired RMSD. All atoms in the current
structure undergo the same transform. The result opens as a new molecular
copy, leaving both input files and their ensembles intact. Its periodicity is
explicitly removed: this is a molecular overlay, not a crystallographic cell
alignment. Fit provenance remains attached to the copy.

The implementation follows [Needleman and Wunsch (1970), J. Mol. Biol. 48,
443–453](https://doi.org/10.1016/0022-2836(70)90057-4) and the existing
[Kabsch (1976), Acta Cryst. A32, 922–923](https://doi.org/10.1107/S0567739476001873)
proper-rotation implementation. Scoring and tie rules above are explicit
implementation choices, not attributed as unique parameters of those papers.

## Reproducible source checks

Recorded RCSB 1CRN, AlphaFold P69905 and PubChem ethanol responses under
`tests/fixtures/biomolecule` exercise retrieval and opening without live network
access. 1CRN PDB/mmCIF atom identities, coordinates, cell and deposited secondary
structure agree; the 46 matched CA pairs give RMSD below `1e-10 Å` (identical
rounded coordinates). A synthetic regular tetrahedron expanded by 10% has the
analytical post-fit RMSD `sqrt(3) × 0.1 Å = 0.1732050807568877 Å`; the test uses
`1e-12 Å` absolute numerical tolerance. These are geometry/software checks,
not validation of a predicted protein's accuracy. Browser interaction remains
a separate acceptance check.

Public services: [RCSB downloads](https://www.rcsb.org/docs/programmatic-access/file-download-services),
[AlphaFold API](https://alphafold.ebi.ac.uk/api-docs),
[PubChem PUG-REST](https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest).


# Cartoon display and producer metadata


A `structure` section can carry optional **biomolecule metadata**: chain
ids, residues, secondary structure and B factors. When it does, the viewer
can draw the structure as a **cartoon**, a ribbon through the backbone,
colour ribbons, atoms and bonds by chain, secondary structure, residue type
or B factor, and select, isolate or hide residue ranges.

Cartoon rendering is a viewer feature over optional format fields. A
`structure` section carrying none of them is a perfectly ordinary
structure, and nothing here changes how a molecular or periodic calculation
is written.

## Getting the metadata into an archive

**From a PDB file.** The simplest route: vibe-view reads `.pdb` directly,
and the importer carries the per-atom columns through: the atom name,
residue name, residue number, chain id and B factor. HELIX and SHEET ranges
are retained, including alpha, pi and 3₁₀ HELIX classes. Ranges containing
insertion codes are not assigned because selection currently uses integer
residue numbers. Open the file, or
import it, and the cartoon is available at once.

```sh
vibe-view open complex.pdb
vibe-view import complex.pdb -o complex.qvf
```

**From a producer.** A QVF writer can supply four independently optional
keys on the structure section: `chains`, `residues` (each with a name, a
sequence number, a chain and 0-based `atom_indices` into the payload, listed
in chain order), `secondary_structure` (inclusive `helix`, `sheet` or `coil`
ranges per chain) and `b_factors` (one per atom). The normative contract is
section 5.1 of the QVF specification in the
[qvf repository](https://github.com/vibe-qc/qvf); vibe-qc's writer
takes them as a `biomolecule_data` argument.

Three rules the viewer applies, so a producer should know them:

* **Supplied beats inferred.** The viewer can derive chains, residues and
  secondary structure on its own, from the per-atom fields and from
  alpha-carbon geometry, and does so when the archive supplies nothing.
  Where a producer supplies them, the supplied values win: a producer knows
  what geometry cannot reveal, such as an alpha from a 3₁₀ helix.
* **A per-atom `b_factor` beats the section-level `b_factors` array**,
  because it cannot desynchronize from its own atom.
* **A `b_factors` array whose length differs from the atom count is
  ignored outright** rather than applied partially.

Residue numbers are not a global key. PDB numbering is four columns wide and
wraps, so a solvated system reuses them; identify a residue by chain plus
number, never by number alone.

## Drawing the cartoon

In the browser's **Display** card set **Representation** to **Cartoon
(biomolecule)**. The ribbon replaces the spheres and bonds. On a structure
with no residue identity, an XYZ, say, the picker refuses the switch and
says so in the status line; through the Python API such a structure falls
back to ball-and-stick.

Opening a protein does not build connectivity the ribbon never draws.
Geometry loading and bond inference are separate; the bonds are computed
once, and cached, only when you switch to a representation that shows them.

### Biomolecule colour

| Mode | Colours by |
|---|---|
| **Element (cartoon: chain)** | element colours in atom views; chain colours in cartoons |
| **Chain** | one hue per chain |
| **Secondary structure** | helix, sheet, loop, plus supplied helix/bridge subtypes |
| **Residue type** | the residue's identity |
| **B-factor** | a ramp over this structure's B-factor range |

The **Biomolecule colour** control applies in every representation. Atom views
start with element colours; cartoons start with chain colours. Changing the
palette carries it across representation changes. Bond colours blend their
endpoint residue colours; selected internal bonds remain white. Atoms outside
CA-bearing residues retain their element colours.

The B-factor ramp is normalized over the structure you have open, so the
colours mean nothing without the range they span: selecting the mode
reports that range in the status line. A file with no B factors falls back
to chain colour and says so.

### Highlighting residues

**Select residues** takes a selection string and works in every
representation. In the cartoon, matching ribbon samples render white over
the active colour mode; in ball-and-stick and space-filling every atom of a
matching residue renders white; in sticks-only and wireframe a bond renders
white when both its atoms belong to matching residues, so a boundary bond
does not bleed.

| Selection | Selects |
|---|---|
| `A` | all of chain A |
| `A/24-38` | residues 24 to 38 of chain A |
| `*/24-38` | residues 24 to 38 in every chain |
| `A/24-38, B/10` | comma- or space-separated terms |

The field reports what it matched, the residue count and the chains
involved, or *nothing selected*, and calls out a chain id it does not know
or a term it cannot read. A chainless PDB groups its atoms under an empty
chain id you cannot type; reach those residues with `*`.

### Isolating, hiding and picking

After selecting residues, use **Residue visibility** to **Isolate selection**
or **Hide selection**. **Show all** restores the structure. Clearing the
selection also restores visibility. These controls affect the displayed
structure. Source coordinates and coordinate exports remain intact; scene
exports reflect the displayed geometry.
Cartoons are cut into separate capped spans; hidden residues are never joined
by an artificial ribbon. Atom labels and bonds follow the visible atoms.

Enable **Pick residues in viewport**, then click an atom or ribbon to replace
the selection with that residue. Use the selection field for multiple ranges.
Picking uses the visible ribbon's residue membership, so an isolated segment
can still be selected. Enabling residue picking leaves measurement and editing
mode. Residue selection currently covers CA-bearing residues, not nucleic
acids, solvent or ligands without an alpha carbon. Chainless selections use
`*`, which also matches named chains with the same residue numbers.

### Secondary-structure detail

Strands have a rectangular cross-section and an arrow toward the C-terminus;
bridges use a narrower profile without an arrow. End caps have independent
normals. Alpha, pi and 3₁₀ helices have distinct display widths. These widths
are display choices, not measured molecular dimensions.

The geometric assignment remains a coarse CA-only H/E/C approximation, not
DSSP. More detailed labels require supplied annotations. Within the QVF
schema's extensible range objects, vibe-view recognizes the optional viewer
convention `subtype: "alpha"`, `"pi"` or `"3_10"` on `type: "helix"`, and
`subtype: "bridge"` on `type: "sheet"`. These are viewer extensions, not new
normative QVF types. Without a subtype, the original helix/sheet/coil behaviour
is retained. The Python reader keeps those coarse labels by default;
`secondary_structure(detailed=True)` exposes H/G/I/E/B/C.

### Hydrogen-bond contacts

**Hydrogen-bond contacts (explicit H)** draws cyan dashes between explicit
hydrogens and candidate N/O acceptors. The geometric screen uses an N/O donor
within 1.2 Å of H, donor–acceptor distance at most 3.0 Å and D–H–A angle at
least 150°, following the distance/angle criteria documented by
[MDAnalysis HydrogenBondAnalysis](https://docs.mdanalysis.org/stable/documentation_pages/analysis/hydrogenbonds.html).
No additional dependency is required.

This is a geometric contact display: it does not infer missing hydrogens,
protonation, acceptor chemistry, hydrogen-bond energies or periodic images.
The status reports candidate contacts in the input cell; visibility filters
can hide some of them. A PDB without hydrogens produces no contacts.

## Outside the browser

**Terminal mode** does not draw a ribbon, but reads the same metadata for
the backbone trace and for colouring:

```sh
vibe-view show protein.qvf --representation backbone
vibe-view show protein.qvf --color-by chain
vibe-view show protein.qvf --color-by secondary
vibe-view show protein.qvf --color-by bfactor
```

**Headless capture** takes the representation as a keyword:

```python
from vibeview import capture_structure

capture_structure("protein.qvf", "ribbon.png", representation="cartoon")
```

That renders with the default chain colouring and no selection; those two
are viewer controls. For full control from a script, drive
`StructureRenderer.add_to_plotter` in `vibeview.renderers.structure`
directly, which takes `representation`, `cartoon_color_mode` and
`residue_selection`, `atom_color_mode`, `residue_visibility` and
`show_hydrogen_bonds`. That module is below the supported surface.
