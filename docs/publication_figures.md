# Publication figures and morph movies

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
label scale, then **Label selected atoms** to create a distance (Å), angle or
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
