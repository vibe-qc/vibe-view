# Experimental map coordinates

The Python API `vibeview.experimental_map.open_map(path)` opens a real scalar
MRC2014/CCP4 map with memory-mapped data. Data axes are columns, rows and
sections; the rows of `voxel_vectors` describe those same axes in angstrom.
A point with array index `i` has Cartesian coordinates `origin + i @ voxel_vectors`.
This preserves `MAPC/MAPR/MAPS`, cell sampling and non-orthogonal cell geometry.
A nonzero Cartesian `ORIGIN` takes precedence; otherwise the permuted
`NCSTART/NRSTART/NSSTART` locate the first grid point. `origin_convention`
identifies the rule used. No symmetry expansion or handedness inference occurs.

Modes 0, 1, 2, 6 and 12 are supported with declared little/big byte order.
Complex Fourier maps, packed four-bit data, ambiguous byte order, truncated
payloads and image/volume stacks are refused with an explanation.

`sampled(max_voxels=2_000_000)` returns a bounded interaction array, its
correspondingly enlarged voxel vectors and the integer stride. It retains the
first grid point and does not read the whole map into RAM. This is stride
subsampling, not an averaged or filtered density; small features can disappear.
`histogram()` describes the interaction sample. `sigma_level(s)` uses the
**original header** mean and RMS: `mean + s * rms`. Invalid header statistics
are refused, never replaced silently with statistics of the reduced grid.

Open `.mrc`, `.ccp4` or `.map` through the normal file opener or upload. The
import produces a `volume.generic` QVF interaction snapshot with at most two
million voxels. Its grid origin and enlarged voxel vectors preserve placement;
provenance records the original dimensions, header statistics, stride and
whether full resolution was retained. The source file remains unchanged.
`map_to_qvf(path, max_voxels=...)` exposes a larger explicit sampling budget;
use the original voxel count for a full-resolution conversion, which requires
corresponding memory. Uploads are already held in memory by the browser layer.
Specialized histogram/sigma, zone controls and live-browser acceptance remain
in development; the existing volume display opens the imported grid.

Format conventions follow the [CCP-EM MRC2014 specification](https://www.ccpem.ac.uk/mrc-format/mrc2014/).

`map_surface(map, level, sigma=True, selection=None, radius=3.0)` contours
in the original Cartesian frame. Pass `sigma=False` for an absolute level.
An optional `(n,3)` array of selected atom positions clips the contour to the
union of spheres of the specified angstrom radius. An empty selection yields
an empty zone. Recompute when the selected atoms move or the selection changes.
Distance is interpolated across contour triangles, so zone boundaries have
mesh-resolution error; they are not capped with an invented density surface.
The returned PyVista mesh can be captured or displayed as surface or wireframe.

## Map control panel

Open the experimental map and its model as separate files. In **Experimental
map**, refresh the list and select the map; keep the model active for atom
selection. Enable **Show controlled map**, choose sigma or absolute units,
level, surface/mesh style and opacity, then **Apply map controls**. This replaces
the generic active-volume contour to avoid displaying conflicting levels.
The sampled histogram has clickable bars that set an absolute level; the notice
always reports the original header mean/RMS and import stride.

Enable **Zone around selected atoms** and set a radius in angstrom. Measurement
or Edit selections in the current model update the zone automatically. Empty
selection means an empty zone. Switch models and select atoms anew rather than
assuming atom indices identify the same atoms in another file. The map is never
translated or fitted automatically. Disable the controlled map to remove its
actor; reselect a volume section to restore generic volume controls.
Live browser acceptance remains pending; controller/template and numerical
checks do not substitute for it.
