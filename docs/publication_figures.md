# Publication figures and morph movies

## Scriptable stored-field recipes

[`examples/render_recipe.py`](../examples/render_recipe.py) is a standalone
recipe runner for the **already released 3.0.0 Python APIs**. The runner is
distributed separately from that release; it does not patch the installed
viewer. It reads a density or real orbital QVF section, renders a PNG and
exports the unchanged recipe plus an evidence JSON. It never runs chemistry,
evaluates orbitals anew, normalizes arrays or writes the source QVF.

Use the Python belonging to the intended viewer installation:

```sh
python examples/render_recipe.py calculation.qvf recipe.json \
  --viewer-identity viewer-identity.json --output figure-01
python examples/render_recipe.py calculation.qvf figure-01/recipe.json \
  --viewer-identity viewer-identity.json --output figure-01-replay
```

Existing output directories are refused. This example is deliberately separate
from `vibe-view capture`: that command still uses its documented fitted camera.
The recipe explicitly specifies the orthographic camera in ångström; parallel
scale is half the viewport's vertical world span. Image size is in pixels.
Isovalues use the QVF v1 kind convention: electron/bohr³ for density and
bohr⁻³ᐟ² for a real orbital amplitude. Colors describe rendering, not charge.
Signed lobes use the stored phase without rephasing or squaring it.

All version-1 keys are required. Replace the example hash, section IDs and
element palette with those of the inspected archive:

```json
{
  "version": 1,
  "source_sha256": "REPLACE_WITH_ACTUAL_QVF_SHA256",
  "structure": "structure",
  "field": "vol_dens_0",
  "field_units": "electron/bohr^3",
  "isovalue": 0.05,
  "signed": false,
  "positive_color": "#2f92a3",
  "negative_color": "#df6b32",
  "opacity": 0.32,
  "element_colors": {"C": "#555555", "O": "#e54040", "H": "#e8e8e8"},
  "size": [3000, 2250],
  "replication": [1, 1, 1],
  "cell_outline": false,
  "cell_color": "#505050",
  "background": "#ffffff",
  "camera": {
    "position": [4, 3, 8],
    "focal_point": [0.4, 0, 0],
    "view_up": [0, 1, 0],
    "parallel_scale": 2.65
  }
}
```

For orbitals use `signed: true`, `field_units: "bohr^(-3/2)"`, an explicitly
selected orbital section and an appropriate isovalue, for example 0.03. Check
the orbital label against occupations before calling it HOMO or LUMO.
For periodic inputs, `replication: [2,2,2]` displays eight positive-lattice
translations, not a surrounding 27-cell neighborhood. The optional outline
encloses that supercell and requires a 3D periodic cell. Nonperiodic replication
is refused. Camera and image dimensions remain explicit, so choose framing
that includes the full replicated scene. Replication copies existing contours;
it does not fill missing samples or guarantee continuous seams.

### Identify the released software

Obtain the release manifest and source archive from the project's published
release. Verify the archive against `artifacts.source.sha256` before making a
source identity. For example, after saving them as `release.json` and
`viewer-source.tar.gz`, the following standard-library script records all
package Python sources without extracting or modifying them:

```python
import hashlib, io, json, tarfile
from pathlib import Path

release = json.loads(Path("release.json").read_text())
blob = Path("viewer-source.tar.gz").read_bytes()
digest = hashlib.sha256(blob).hexdigest()
assert digest == release["artifacts"]["source"]["sha256"]
with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
    files = {
        "src/vibeview/" + m.name.split("/src/vibeview/", 1)[1]:
        hashlib.sha256(archive.extractfile(m).read()).hexdigest()
        for m in archive.getmembers()
        if "/src/vibeview/" in m.name and m.name.endswith(".py")
    }
assert files
Path("viewer-identity.json").write_text(json.dumps({
    "version": release["version"], "source_commit": release["sourceCommit"],
    "source_archive_sha256": digest, "files_sha256": files,
}, indent=2))
```

The runner requires the imported package's complete Python inventory to match
this identity. A self-generated hash list from an unverified checkout does not
establish a release. Python, NumPy, PyVista and VTK versions are recorded
separately; source identity does not certify those dependencies or native
binaries. Keep the published manifest and artifact digest with the evidence.

### Qualification and limits

Each evidence record includes the source QVF and selected member hashes,
structure/field selectors, source coordinates, displayed replica offsets,
field dtype/shape/grid and units, exact renderer-input equality, contour
counts/bounds, actual camera, image digest and runner digest. A changed source
hash, missing/altered member, wrong unit declaration, incompatible structure
reference, empty contour or modified viewer source is refused. Legacy fields
without `structure_ref` require an explicit recipe binding and human review;
the script cannot infer scientific correspondence. Existing QVF validators
and the producer's scientific acceptance remain separate checks.

Focused tests exercise a replicated synthetic scene, exact replay, and refusal
boundaries. An actual 3000×2250 molecular density/HOMO/LUMO qualification uses
unchanged retained arrays with the released 3.0.0 package; the test fixtures
themselves are not chemistry evidence. Pixel equality is checked on a fixed
runtime, but VTK, GPU and platform differences can change rasterization.
High resolution does not improve the underlying field grid. This path does
not claim browser silhouette parity or scientific convergence. Accepted
periodic publication figures additionally require accepted producer arrays,
mesh/grid checks and visual inspection; a diagnostic or synthetic density
does not meet that requirement.

## Interactive figures

The **Publication figure** card keeps decorations in the view session, bookmarks
and slides. Working-model scene JSON also includes them. PNG capture composites
the same RGBA annotation layer displayed above the browser canvas; world-space
measurement labels use the existing camera-facing mesh renderer. PDF and SVG
exports embed that viewport image: they are portable figure containers, not
vector molecular geometry. Browser capture retains browser-only rendering effects;
server capture retains its own renderer effects as described in the capture dialog.

The colour field applies to new measurement labels, overlays and colour-key
text. Its default, `auto`, picks white on a dark background and black on a light
one when each decoration is created; the chosen colour is stored with it, so a
later background change does not recolour saved decorations.

Select two, three or four atoms in order, choose decimal precision, colour and
label scale, then **Label selected 2 / 3 / 4 atoms** to create a distance (Å), angle or
signed dihedral (degrees). The 3D text font has ASCII glyphs only, so scene
labels spell the units `A` and `deg`. Each label floats clear of its atoms'
displayed spheres, above the measured centre. Working-model selections use model/name and atom index;
their deterministic order is shown in the scene JSON and can be edited there.
Labels follow coordinate changes, including model transforms. References are
indices within the current structure or named model, not chemical identifiers.
Clear or edit them when changing to an unrelated structure.

Choose text, arrow or box and set start/end coordinates between 0 and 1, measured
from the top-left of the image. Font and stroke sizes scale from the viewport
size when exporting larger figures. The editable scene JSON supports `width`
for lines, `prefix` for measurement labels and individual colours/sizes. Applying
JSON validates the whole scene before replacing it. **Clear decorations** restores
the original structure rendering. Changing the camera does not move 2D overlays.

For a colour key, set the same Matplotlib palette and numerical range as the
mapped property, enter its name and units, and choose its image rectangle.
Keys can describe charges, ESP, B-factor, or a displayed surface; setting a key
alone does not change the underlying property's mapping.

Per-atom mapping accepts scalar `atom_properties` members, B-factor, residue
sequence number, atomic number, or an explicit JSON array in current atom order.
For another residue property, repeat each residue's value for its atoms in that
array. Missing/nonfinite values or a mismatched atom count are refused. Values
outside the selected range clamp to its ends. Optional display radii interpolate
between positive Å bounds; these are graphical radii, not changes to coordinates
or covalent radii. Current-structure mapping uses the standard structure renderer,
including periodic replicas. Disable working-model mode to apply this mapping.
Styles with no atom spheres naturally do not display sphere radii.

## Morph export

Open both endpoint structures, select the destination, and provide a one-based
bijection of target atom indices if the atom order differs. The default asserts
same-order correspondence; matching element identities is required. The viewer
does not infer chemically ambiguous atom mappings. Linear interpolation samples
straight Cartesian paths. Path interpolation resamples the current trajectory by
Cartesian arc length; its endpoints must exactly equal the chosen structures in
source atom order. This is geometry interpolation, not a calculated reaction path.
Periodic cells are refused until a cell-path model is supplied.

The requested 2–2000 frames include the exact endpoints. Export freezes source
geometry, bonds, annotations and camera before running in the background. Existing
MP4/ffmpeg and GIF encoding paths are reused; absent ffmpeg falls back to GIF.
Morph GIF output retains identical adjacent frames without changing their pixels.
GIF timing uses centiseconds (minimum 10 ms), so high requested FPS is quantized;
use MP4 for finer timing. PNG frame sequences are available through
`morph_movie.render_morph(..., format="frames")`.
Morph labels must reference the current structure. The camera remains fixed,
connectivity remains that of the source, and intermediate geometry is not relaxed.

## Acceptance checks

Focused tests exercise actual renderer colour/radius changes, measurement meshes,
saved-scene restoration, shared overlay pixels, exact interpolation endpoints,
path refusal and GIF frame count even for equal endpoints. Live browser parity
and interactive placement still require visual acceptance on the target client;
headless and controller checks do not establish that acceptance.
