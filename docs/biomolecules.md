# Biomolecules

A `structure` section can carry optional **biomolecule metadata**: chain
ids, residues, secondary structure and B factors. When it does, the viewer
can draw the structure as a **cartoon**, a ribbon through the backbone,
colour that ribbon by chain, secondary structure, residue type or B factor,
and highlight a residue range you name.

Cartoon rendering is a viewer feature over optional format fields. A
`structure` section carrying none of them is a perfectly ordinary
structure, and nothing here changes how a molecular or periodic calculation
is written.

## Getting the metadata into an archive

**From a PDB file.** The simplest route: vibe-view reads `.pdb` directly,
and the importer carries the per-atom columns through: the atom name,
residue name, residue number, chain id and B factor. Open the file, or
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

### Ribbon colour

| Mode | Colours by |
|---|---|
| **Chain** (default) | one hue per chain |
| **Secondary structure** | helix, sheet, loop |
| **Residue type** | the residue's identity |
| **B-factor** | a ramp over this structure's B-factor range |

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
`residue_selection`. That module is below the supported surface.
