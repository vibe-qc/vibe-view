# Chemical-space maps

The **Chemical space** panel consumes fingerprints produced by
`vibeqc.similarity` (QC source d6b3d9d5 or later). It never constructs graph
weights or substitutes a different fingerprint. The default variant is
`quantile-scale`; select `toney2026` explicitly for an upstream-compatible
collection. Parameters and the property-table identity appear with the map.
Mixed variants, mismatched geometry hashes and mixed parameters are refused.

Create a collection from your molecular structures or trajectory frames:

```python
import json
from vibeqc.similarity import spectral_fingerprint

records = []
for index, (symbols, coordinates_angstrom) in enumerate(frames):
    fingerprint = spectral_fingerprint(symbols, coordinates_angstrom)
    records.append({
        "id": f"frame-{index}",
        "symbols": list(symbols),
        "coordinates_angstrom": coordinates_angstrom.tolist(),
        "fingerprint": fingerprint.to_dict(),
        "group": "trajectory",
        "properties": {"frame": index},
    })
with open("chemical-space.json", "w") as stream:
    json.dump({"schema": "vibeview.chemical-space/1", "records": records}, stream)
```

This is a viewer collection format, not a new QVF section or a change to QVF.
Coordinates are angstrom. IDs must be unique. Optional numeric `properties`
and a `group` string control point colours. Keep producer payloads intact;
the geometry digest binds the displayed structure to its fingerprint.

Open a structure in View, expand **Chemical space**, enter the collection path
on the machine running View, and choose **Open chemical space**. PCA runs in a
background thread. A raster scatter plot keeps the browser element count
constant for large sets; clicking a point opens its structure through the
normal file loader without changing the previous model. Coincident projections
select the first nearest point; the zero-based index control selects any other
record, including overlapping points. PCA coordinates are a projection, not
similarity scores.

Choose a numeric property or group and **Apply colours**. For a selected record,
**Compare and find neighbours** shows overall similarity and electrostatic,
bonding, steric and dispersion scores, with up to 100 nearest neighbours in the
full fingerprint space. Overall similarity includes the producer's optional
size component; channel scores do not. Click a neighbour to open its structure.

The panel supports 2 to 100,000 records and collections up to 256 MiB. Geometry
and fingerprint semantics retain QC's limits: periodic inputs are finite
clusters, single atoms use a special convention, and the same-element bonding
channel does not decay with distance. Equal fingerprints do not prove chemical
identity. See Toney et al., *Chemical Science* (2026), DOI:10.1039/d6sc06309h.
