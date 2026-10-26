# Build, inspect and present

This tutorial takes one structure from a starting geometry to a figure and a
calculation input. It uses the builder, the exact-geometry tools, the
coordinate editor, surfaces and interactions, publication figures and the
working model scene. Each step names the browser card to use and links to
the chapter that describes it in full. The last section runs the same steps
from Python with a script you can download.

You need the `[viewer]` extra for the browser steps. Some steps need more:

| Step | Extra |
|---|---|
| The builder's fragments, sequences, coordination ligands and conformers | `[smiles]` (RDKit) |
| Solvent-excluded surfaces (SES) | `[surfaces]` (SciPy) |
| mmCIF files and fetched biomolecules | `[biomolecule]` (Gemmi) |

`vibe-view doctor` lists which extras are installed. Using a step whose
extra is missing reports the command that installs it.

## 1. Start a structure

Start the viewer with the bundled water molecule, or with any structure file:

```sh
vibe-view demo --open
```

Open the **Molecular builder** card:

- **Search fragments by name / SMILES**, pick a fragment, and click **Open**
  to start a new molecule from it, or select an atom and click **Attach at
  selected atom** to replace one hydrogen there with the fragment.
- Enter a **One-letter sequence**, choose the **Sequence type** and
  **Peptide backbone** or DNA/RNA strand construction, then **Build
  sequence**. DNA and RNA can be built as a single strand or a complementary
  duplex.
- Select a metal atom, choose a **Coordination geometry** and a **Ligand**,
  then **Fill open sites**.
- **Generate and rank conformers** writes energy-ranked conformers as
  structures and trajectory frames, with a **Fixed random seed** for
  reproducible results.

Every result opens as a structure you can edit. The force-field energies
and template placements are starting points, not calculated results; see
[Molecular construction](molecular_builder.md#builder-panel).

## 2. Set the geometry exactly

To set a measured value:

1. Switch on **Measure** and click two, three or four atoms in order.
2. In **Measurements**, type the new distance (Å), angle or dihedral (°).
3. Choose which side moves, then click **Apply exact value**.

Each change is one undo step, and a change that would distort a ring or move
a frozen atom is refused rather than applied. In the **Atom Editor**, select
two atoms and use **Add / set bond** to make a bond order explicit. Both are
described in [Building and editing](editing.md#set-exact-distances-angles-and-dihedrals).

The **Coordinate editor** shows the same geometry as numbers:

- Edit x, y and z or the **Frozen** column, then **Apply table**.
- Paste one XYZ frame and choose **Replace XYZ** or **Append XYZ**.
- **Generate Z-matrix** builds internal coordinates from the current bonds.
  Edit them, then apply them. **Export coordinate block** writes Gaussian,
  ORCA or MOPAC syntax.

See [Internal coordinates](internal_coordinates.md#coordinate-editor).
**Bond rotation angle** in the builder card turns the smaller side of a
selected bond; the bond stays selected for further steps.

## 3. Inspect surfaces and interactions

Open **Molecular surfaces and interactions**:

1. Set **Selection** to the atoms you want, for example `all`,
   `chain A and not element H` or `within 4 of (residue A/24)`. The syntax is
   in [Atom selection language](selection_language.md).
2. Choose the **Molecular surface** kind (VdW, SAS or SES), the **Probe
   radius (Å)**, **Grid spacing (Å)**, **Surface colour** and **Surface /
   isosurface style** (solid, mesh or dots).
3. Under **Geometric interactions**, choose the kinds and their criteria, and
   optionally a **Partner selection**.
4. Click **Apply inspection**. The card reports the surface area in Å², the
   enclosed volume in Å³ and a table of interactions.

The interactions are geometric candidates, not interaction energies. The
definitions, their published sources and the accuracy checks are in
[Molecular surfaces and interactions](molecular_inspection.md).

For a protein, fetch it by identifier and use the sequence panel, as
described in [Biomolecules](biomolecules.md). An experimental density map
opens in its own **Experimental map** card, with sigma levels and a zone
around selected atoms ([Experimental maps](experimental_maps.md#map-control-panel)).

## 4. Present the result

Open **Publication figure**:

- Select two, three or four atoms and click **Label selected 2 / 3 / 4
  atoms** for a distance, angle or dihedral label that follows the geometry.
- Add text, arrows and boxes with **Add overlay**, and a colour key with
  **Add colour key**.
- Map a **Per-atom property**, such as a charge or B-factor, to colour and
  optionally radius with **Apply attribute**.
- Under **Morph movie**, choose a **Morph target** and export a movie that
  interpolates between two structures.

Decorations are saved with the session and with bookmarks, and appear in
screenshots and PDF or SVG figures. See
[Publication figures](publication_figures.md).

Results add their own presentation controls:

- **Vibrations:** **Show displacement arrows** draws the mode, and
  **Export selected mode** writes it as a GIF or MP4
  ([vibrations](browser.md#vibrations)).
- **Spectra:** choose a **Line shape** and **FWHM**, then **Export ASCII**
  ([spectra](browser.md#spectra)).
- **The command line:** `vibe-view animate --arrows` and
  `vibe-view spectrum` produce the same outputs ([CLI reference](cli.md)).

## 5. Compare models in one scene

The **Working model scene** card holds several structures in one coordinate
frame:

1. Click **Add current structure** with a **New model name**.
2. Open a second file and add it under another name.
3. Switch on **Show working scene**.
4. Type commands in **Command / history** and press Enter.

```text
colour mobile orange
align mobile "model mobile and element C" "model reference and element C"
measure rmsd "model reference" "model mobile"
measure plane "model reference and not element H"
save scene.json
```

`save` and `save-script` download JSON that **Restore scene / replay script**
rebuilds later, including the camera. See [Model scenes](model_scenes.md).

## 6. Prepare the calculation

Open **Calculation Parameters**, choose the program, method and task, and
preview the input. vibe-qc and ORCA submit through vq. Psi4, NWChem, Q-Chem,
CP2K and xtb produce an input bundle that can be submitted as a serial job,
and Gaussian, GAMESS and VASP inputs are saved for you to run. The edited
coordinates, frozen atoms, charge and multiplicity carry into the input. The
whole path, including registering programs and running on a cluster, is in
[Supported programs and calculations](calculations.md) and
[Clusters and remote machines](cluster.md).

## 7. The same steps in Python

{download}`tour.py <examples/python-tour/tour.py>` runs the steps above on a
water dimer, using the functions behind each card. It needs only the core
install (SES is skipped without the `[surfaces]` extra):

```sh
python tour.py tour-output
```

Each numbered line of its output corresponds to one API:

| Output | Function | Chapter |
|---|---|---|
| 2. centroid, plane, RMSD | `molecular_selection.select`, `selection_measurements` | [Atom selection language](selection_language.md#measurements-in-python) |
| 3. hydrogen bond and contact | `interactions.find_interactions` | [Molecular surfaces and interactions](molecular_inspection.md#python-use) |
| 4. VdW, SAS and SES area and volume | `molecular_surfaces.build_surface` | [Molecular surfaces and interactions](molecular_inspection.md#surfaces-colours-and-numerical-meaning) |
| 5. exact distance and angle, saved QVF | `api.set_geometry_parameter`, `set_bond`, `export_geometry_qvf` | [Building and editing](editing.md#edit-bonds-explicitly) |
| 6. Z-matrix round trip | `zmatrix.from_cartesian`, `parse_zmatrix` | [Internal coordinates](internal_coordinates.md) |
| 7. aligned two-model scene, replayed | `model_scene.ModelScene`, `scene_commands.SceneCommands` | [Model scenes](model_scenes.md) |
| 8. broadened spectrum keeps its area | `spectrum.broaden_lines` | [Explore electronic spectra](orca.md#5-explore-electronic-spectra-and-mcd) |
| 9. and 10. xtb input bundle, reopened | `input_writers.write_input`, `converters.convert_to_qvf` | [Preparing native calculation inputs](input_writers.md#sdk) |

The expected output starts like this:

```text
1. Opened a water dimer with 6 atoms
2. Donor water centroid [-1.362  0.23   0.   ] Å, plane residual 0.00e+00 Å, fitted O-H-H RMSD between the waters 0.002 Å
3. contact: atoms [1, 4], 2.910 Å
3. hbond: atoms [1, 3, 4], 2.910 Å
4. VDW: area 65.3 Å², volume 37.3 Å³
4. SAS: area 180.5 Å², volume 204.9 Å³
4. SES: area 66.2 Å², volume 39.7 Å³
5. O1-H2 is now 1.0000 Å; saved dimer-edited.qvf
6. Gaussian Z-matrix written; round-trip RMSD after alignment 4.3e-12 Å
```

The fragment, sequence, coordination and conformer builders need RDKit, so
the script leaves them out; their Python functions are described in
[Molecular construction](molecular_builder.md).

## Where next

* [The browser viewer](browser.md#building-inspecting-and-presenting), for
  every card and where it is documented.
* [Python SDK](python_api.md#specialised-modules), for the modules used above.
* [A tour of the viewer](walkthrough.md), for reading calculated results:
  densities, orbitals, spectra and periodic data.
