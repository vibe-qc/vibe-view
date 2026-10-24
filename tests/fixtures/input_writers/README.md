# Reviewed reference inputs

These synthetic neutral-singlet water inputs are recorded examples checked
against the authoritative manuals linked in docs/input_writers.md, not outputs
from running those programs. Atoms 1 and 3 are frozen. Coordinates are angstrom;
the explicit CP2K molecular box and VASP periodic cell are 12 angstrom cubic.
VASP is structure-only. The xtb argv array is not a shell script.

The golden comparison detects drift from these reviewed records; the other
writer tests independently check metadata, atom/coordinate indices, task
keywords, refusal paths, triclinic fractional transforms, ZIP contents, queue
staging and an ASE POSCAR round trip. No energy or browser PASS is implied.
