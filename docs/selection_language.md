# Atom selection language

The Python function `vibeview.molecular_selection.select(structure, expression)`
returns a set of zero-based atom indices. Surface and interaction selection
fields use this same parser. Expressions use **one-based** atom numbers.

```text
expression  = conjunction { "or" conjunction }
conjunction = negation { "and" negation }
negation    = "not" negation | primary
primary     = "(" expression ")" | "all" | "none"
            | "index" index-list | "element" value | "chain" value
            | "residue" value | "model" value | "group" value
            | "within" radius "of" "(" expression ")"
index-list  = positive-integer [ "-" positive-integer ]
              { "," positive-integer [ "-" positive-integer ] }
```

`not` binds before `and`, which binds before `or`. Keywords are case-sensitive;
element symbols are case-insensitive. Values containing spaces need quotes.
Indices and inclusive ranges must be in bounds, and reversed ranges are refused.
Residues use `chain/sequence`, for example `residue A/24`. Missing identifiers
match no atoms. Blank expressions select none. Invalid syntax raises `ValueError`;
expressions are never executed as Python.

Examples:

- `element O or element N`
- `chain A and not element H`
- `index 1,3-8`
- `within 4 of (residue A/24) and not residue A/24`
- `(model protein and element C) or group "active site"`

Distances are in angstroms, inclusive at the cutoff, and include seed atoms.
They use the supplied coordinates without periodic images. A single structure
has model ID `1`. Callers representing several models supply `model_ids`, with
one string identifier per atom. Named groups are supplied with `groups`, a
mapping of names to sets of **zero-based** indices; invalid group indices are
refused. Neither context is modified. Unknown group names raise `ValueError`
to catch mistakes in scripts.

```python
from vibeview import QVFReader
from vibeview.molecular_selection import select

with QVFReader("molecule.qvf") as reader:
    structure = reader.read_structure()

# This example needs a structure with at least five atoms.
selected = select(structure, 'group "active site" and not element H',
                  groups={"active site": {0, 2, 4}})
```

The returned set has no atom ordering. Sort indices for a deterministic
single-structure report, but supply an explicit chemical correspondence for
paired measurements. An empty result is valid for an absent residue or
element; check it before fitting a plane or computing a centroid.

These functions are submodule APIs rather than package-root exports. Pin the
viewer version for scripts that depend on them; see [the SDK contract](python_api.md).
The grammar and context API are available independently of scene scripting.
See [working model scenes](model_scenes.md) for browser command history,
replayable models and scene measurements.

## Measurements in Python

`vibeview.selection_measurements` accepts finite `(N, 3)` coordinate arrays in
angstroms (or consistently in another length unit). `centroid` is unweighted;
`principal_axes` returns the centroid, descending population variances and unit
axes as rows. Its `unique` flag is false when eigenvalues are repeated: directions
within that subspace are not physically determined. This is coordinate covariance,
not a mass-weighted moment-of-inertia tensor.

`best_fit_plane` returns a centroid, unit normal and RMS perpendicular residual
from orthogonal least squares. It refuses collinear selections and other cases
without a unique normal. `point_plane_distance` is unsigned; `plane_angle`
returns the unoriented acute angle in degrees (0–90). Empty or nonfinite selections
are errors. No periodic images or minimum-image distances are inferred.

`selection_rmsd(first, second)` uses the supplied row correspondence; pass
`superpose=True` to remove translation and proper rotation with Kabsch alignment.
It never guesses atom pairing or mirrors one structure onto another. Order paired
selections deliberately; independently sorting two index sets need not represent
the same chemical correspondence.

```python
import numpy as np
from vibeview.molecular_selection import positions, select
from vibeview.selection_measurements import best_fit_plane, centroid

indices = sorted(select(structure, "residue A/24 and not element H"))
coordinates = positions(structure)[np.asarray(indices, dtype=int)]
centre = centroid(coordinates)
plane = best_fit_plane(coordinates)
```
