# Biomolecules

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
