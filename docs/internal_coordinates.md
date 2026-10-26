# Internal-coordinate API

`vibeview.zmatrix.from_cartesian(symbols, positions, bonds)` creates a Z-matrix
from angstrom coordinates and optional zero-based bond pairs. It prefers a
preceding bonded atom for each distance reference and retains real atom order.
Three explicit dummy `X` atoms establish a reference frame for linear and
disconnected fragments. Dummy atoms are omitted from `matrix.cartesian()` by
default; `include_dummies=True` retains them. Rings retain their coordinates;
non-tree bonds remain in the original structure's connectivity.

`matrix.text(style)` exports a coordinate block in `gaussian`, `orca` (internal
coordinates) or `mopac` layout. Gaussian export supports `variables=True`.
`parse_zmatrix(text, style)` reads those blocks and numeric variable definitions,
including signed variable references. Expressions are never executed.
The API uses angstrom distances and degree angles. MOPAC exports use zero
optimization flags; this coordinate export does not request an optimization.
Program headers, charge/spin and Gaussian route cards belong to the input writer.

Malformed references, coincident reference points and collinear reference planes
raise a clear error; insert a dummy atom rather than guessing a dihedral plane.
Conversion can change overall orientation and translation, so compare geometry
after a proper rigid alignment. The coordinate editor below uses these same
validated numerical API functions.

Conventions follow the [ORCA coordinate manual](https://www.faccts.de/docs/orca/6.0/manual/contents/input.html)
and MOPAC's [internal coordinate definition](https://openmopac.net/Manual/int_coord_def.html)
and [Gaussian-style Z-matrices](https://openmopac.net/Manual/gaussian_z.html).

## Coordinate editor

The **Coordinate editor** card provides editable element, x/y/z and frozen
columns. Its lengths follow **Settings → Length unit** (angstrom or bohr);
archives continue to store angstrom. **Apply table** commits all validated rows
as one undo transaction. Frozen flags are explicit preparation constraints, not
a prohibition on editing their numerical table values. Undo/redo restores both
geometry and frozen flags. Invalid elements, non-finite coordinates and malformed
XYZ input leave the geometry and history unchanged.

Paste exactly one standard XYZ frame, including atom count and comment line,
then choose **Replace XYZ** or **Append XYZ**. The pasted numbers use the current
Length unit displayed above the table. Replace clears stale connectivity and
frozen indices; append retains existing constraints and connectivity. Reloading,
changing the Length unit, or another geometry edit refreshes the table from the
current geometry and discards uncommitted table changes. Applying a table stops
auto-optimization so its coordinates are not immediately overwritten.

**Generate Z-matrix** uses the current bond connectivity and exposes editable
atom/reference/value cells plus numeric variable definitions. The accompanying
coordinate-block field also accepts Gaussian, ORCA or MOPAC layouts; choose the
style and **Load rows from block** before editing cells, or **Apply pasted block**
directly. Z-matrix distances are always angstrom and angles degrees, independently
of Cartesian display units. Applying the table is one undo step. Real-atom order
must remain unchanged, and changes that move frozen atoms refuse until those
atoms are explicitly unfrozen. Periodic structures require Cartesian editing;
Z-matrix application is molecular. The updated geometry is rigidly fitted to the
previous orientation, without altering its internal distances or angles.

**Export coordinate block** converts the current block to Gaussian (with numeric
variables), ORCA or MOPAC syntax for copying. These are coordinate blocks, not
complete calculation inputs. Standalone `.zmat`/`.zmt` files detect these three
layouts by their first atom row. Gaussian `.gjf`/`.com` inputs now accept a
Z-matrix molecule specification and numeric Variables/Constants sections.
Expressions and mixed Cartesian/internal-coordinate rows are deliberately refused.

Python equivalents are `coordinate_tables.table_rows`, `parse_rows`, `xyz_rows`,
`zmatrix.from_cartesian`, `parse_zmatrix`, and `converters.zmatrix_to_qvf` or
`gjf_to_qvf`. Numerical round trips for chains, rings and dummy-anchored linear
fragments are checked after alignment against the 1e-6 angstrom acceptance bound.
