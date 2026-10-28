# Nanotubes and tube–metal interfaces

vibe-view builds carbon nanotubes and the cluster models of their contacts
with metals, draws structures of up to a million atoms, and analyses the
interface a calculation produced. Everything here makes starting
geometries: ideal cylinders and unrelaxed contacts to optimise before a
calculation, not converged structures.

The browser's tools are in the **Nanotube builder and views** card of the
side panel; the same functions are in Python as `vibeview.nanotube`,
`vibeview.interface_builder`, `vibeview.structure_views` and
`vibeview.interface_analysis`.

## Building a tube

Type the chiral indices **n** and **m**, the number of **walls**, the
**length** in Å and the **ends**, then choose **Build nanotube**. The tube
opens as a new structure along z. As you type, the card reads out each
wall's (n,m), diameter, chiral angle, whether it is metallic or
semiconducting by the zone-folding rule, and the spacing between walls:

```text
(5,5) armchair: d = 6.78 Å, θ = 30.0°, metallic
(10,10) armchair: d = 13.56 Å, θ = 30.0°, metallic
Wall spacing: 3.39 Å
```

| Ends | What you get |
|---|---|
| Open, H-terminated | Dangling atoms removed and every two-coordinated carbon capped with H at 1.09 Å |
| Open, bare | Dangling atoms removed; the edge carbons stay two-coordinated |
| Capped (C60 half) | Both ends closed with a piece of C60 scaled onto the tube. Only rims a C60 half fits, such as (5,5) and (9,0), can be capped; others are refused with a message |
| Periodic along the axis | A whole number of unit cells, periodic along z, in a box with 15 Å of vacuum around the tube |

A multi-wall tube grows outward from (n,m): each outer wall is the (n',m')
whose radius is nearest the previous one plus the wall spacing (3.4 Å by
default), with the chiral angle nearest the inner wall's breaking ties, so
an armchair core grows armchair walls. Multi-wall tubes are built with
open or hydrogen-terminated ends; walls of different chirality have
incommensurate periods, so they are neither capped nor periodic.

In Python:

```python
from vibeview import nanotube as nt

tube = nt.single_wall(10, 10, length=40.0, end="hydrogen")
mwnt = nt.multi_wall(5, 5, walls=4, length=30.0)
rope = nt.bundle(nt.single_wall(10, 10, 30.0), count=7)       # hexagonal, 3.4 Å gaps
forest = nt.forest(nt.multi_wall(5, 5, 3, 30.0), 4, 4, pitch=50.0)
print(nt.wall_info(10, 5))   # diameter, chiral angle, period, atoms per cell
```

`tube.atoms()` gives QVF structure atoms, ready for
`vibeview.qvf_writer.structure_archive`.

## Adding a metal or a linker

With a tube shown, choose a **partner**, its options and **Add partner to
shown tube**. The partner is added to the shown structure through the edit
overlay, like every builder edit, so one **Undo** removes it.

| Partner | Options |
|---|---|
| Metal slab | Al, Ni, Cu, Pd, Ag, Pt or Au; (111), (100) or (110); the number of layers. A square patch sized to the contact, cut from the bulk lattice by the slab builder |
| Nanoparticle | The metal and a radius: the fcc atoms within it, turned so a (111) layer faces the tube |
| Aminophenyl amide linker | –C(=O)–NH–C6H4– on a carbon of the tube, the linker of diazonium grafting; optionally with a metal slab on its free aryl carbon, the tube–linker–metal contact of the Alvarez group's Cu and Au electrodes |

**Site** puts the partner under the tube's lowest open end or against its
sidewall at mid-length; a capped or hydrogenated tube has no open end, and
the card says so. **Registry** puts the facing tube atom over a **top**,
**bridge** or **hollow** site of the partner's facing layer.
**M–C distance** is the distance from that atom to the nearest atoms of the
site, one for top, two for bridge, three for an fcc(111) hollow; left blank
it is the sum of the covalent radii, a bonded contact (2.08 Å for Cu–C,
2.12 Å for Au–C). A placement that would bring the partner within 1.5 Å of
another tube atom is refused: at a hollow site a flat open end may need a
longer distance than at a top site.

Partners are finite cluster models, so they need a structure that is not
periodic; build the tube with open, hydrogen-terminated or capped ends.

## Seeing a large structure

**Colour atoms by** replaces element colours, on atoms and bonds alike:

| Mode | Colour ramp | Needs |
|---|---|---|
| Coordination number | Sequential, over the structure's range; counts the bonds drawn, so bulk metal atoms, which get no metal–metal bonds, count only their bonds to C | Nothing |
| Distance from the axis | Sequential | Nothing |
| Partial charge | Diverging blue–white–red, symmetric about 0 e | An `atom_properties` section; IAO, Hirshfeld, Mulliken and Löwdin are used in that order |
| C–C bond stretch (%) | Diverging, mean C–C bond-length change relative to the builder's 1.42 Å reference; non-carbon atoms are zero | Nothing |
| Open ends, coordination defects and adducts | Grey ordinary; blue two-coordinated carbon within 2 Å of a finite axial end; orange other carbon coordination anomalies; magenta carbon and attached heavy non-carbon atoms | Nothing |
| Bond order to a metal | Sequential; the largest order of each atom to any metal | A `bond_orders` section |

The status line names the range of the ramp. A mode whose section the
archive lacks is refused with a message, and the colours stay as they
were.

**Cut-away** removes part of the structure so you can see inside it:

| Cut-away | Keeps |
|---|---|
| Half wall | The half of the wall away from the viewer of the side view |
| Cross-section slice | A slice 5 Å thick across the axis, at **Cut position** |
| Cut at a point on the axis | Everything below **Cut position** |

Atoms close to the axis, an encapsulated molecule or a filled core, are
always kept. **Down the axis** and **Side view** point an orthographic
camera along the tube or at the face a half cut opens, for figures. The
axis is the principal axis of the carbon atoms, so a metal slab under the
tube does not tilt it.

A structure of 500 atoms or more is drawn with instanced glyphs: one
sphere and one cylinder mesh, repeated on the graphics card for every atom
and bond. A scene of 10 000 atoms or more also draws coarse spheres and
bonds while you turn it and the full ones once you let go. Large scene construction uses vectorised atom, bond and neighbour batches. The
[client performance](client_performance.md) page lists the measured limits.

## Interface analysis

**Interface analysis** reads the archive the calculation wrote and reports:

- every metal–C pair within the sum of the covalent radii plus 0.45 Å, with
  its length and, if the archive has a `bond_orders` section, its order and
  method;
- the projected DOS of the interface atoms, summed separately over the
  metal atoms and the carbons in those contacts, when a `dos.projected`
  section labels its channels with `atom` indices;
- the first `volume.difference` or `volume.potential` along the tube axis:
  a density difference integrated over each plane perpendicular to the
  axis, in e/Å, with the charge transferred across each plane accumulated
  along it; a potential averaged over each plane.

Each part appears when its section is present, and the report says which
are missing. In Python:

```python
from vibeview.qvf import QVFReader
from vibeview import interface_analysis as ia

with QVFReader("cnt-cu.qvf") as reader:
    structure = reader.read_structure()
    for row in ia.metal_carbon_contacts(structure, reader):
        print(row["metal_label"], row["carbon_label"], f"{row['distance']:.3f}", row["order"])
    profile = ia.axial_profile(reader, "density_difference", structure)
    print(profile.cumulative[-1], "e transferred")
```

None of this needs a new QVF field: the analysis reads `bond_orders`,
`dos.projected` and volume sections as the format defines them.

## Growth and reaction-path playback

Trajectory and reaction-path frames now reuse sphere and cylinder prototypes;
only their instance positions, scales and directions change. Live frames draw
bonds inferred from each displayed geometry, including bond formation and
breaking. These are covalent-radius display contacts, not computed bond orders.
Periodic reaction paths use their per-frame cells and periodic images.
Returning from a volume-series frame removes its intermediate atoms.

Automatic playback strides over intervening frames when a render exceeds its
0.2 s budget; the last frame remains reachable. Manual steps, the frame slider
and the NEB energy profile still select individual stored frames. This reduces
rendering work, not archive memory: QVF coordinate arrays are still decoded in
full. At 100,000 atoms the filmstrip samples four frames instead of sixteen.
Large molecular thumbnails reuse glyph inputs; explicit-bond, periodic and
wireframe thumbnails retain the established structure renderer.

The coordinate editor displays 100 atoms per page. **Previous atoms** and
**Next atoms** change the visible rows; **Apply table** applies the complete
edited table in one transaction, including edits on other pages.

## Figure cues and capture

**Depth cue (browser figure)** blends distant fragments toward the background
in the vtk.js fragment shader. It follows the camera and works with **Down the
axis** and **Side view**. Toggle it off for unmodified property colours. It is
a browser figure effect, including browser screenshots; headless capture does
not apply it.

Bond stretch is a geometric proxy, not a strain tensor or a force calculation.
Site highlights are coordination heuristics, not ring-topology or chemical
assignments: three-coordinated pentagon/heptagon defects are not identified.
Hydrogen termination does not count as a heavy-atom adduct; periodic structures
have no highlighted open rims. Inspect and verify sites before modelling them.

Cut-aways and property colours also work in command-line and Python captures:

```sh
vibe-view capture tube.qvf --cutaway half --atom-color-mode local_strain -o tube.png
```

```python
from vibeview.api import capture_structure
capture_structure("tube.qvf", "tube.png", cutaway="section", cutaway_position=0.3,
                  atom_color_mode="carbon_sites")
```

`cutaway`, `cutaway_position` and `atom_color_mode` have the same meaning in
`vibeview.headless.add_structure` and `render_structure_frame`. The defaults
remain an uncut structure in element colours.

## Terminal scale

At 10,000 atoms and above, the terminal rasterizer uses one depth-tested
point sprite for an atom whose projected radius is at most 0.75 pixel. It
keeps the nearest atom at each pixel, rather than discarding an arbitrary
stride of atoms. Off-screen atoms are culled. Subpixel bonds covered by their
endpoint atoms are omitted; sticks-only views retain their usual bonds.
Zooming in restores shaded spheres and resolved bond cylinders automatically.
The million-atom forest still takes roughly 0.64 s per orbit step in the
latest diagnostic; this does not promise smooth million-atom interaction.
