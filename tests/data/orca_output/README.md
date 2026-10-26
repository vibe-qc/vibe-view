# ORCA printed-result fixtures

These are numerical scientific table excerpts from genuine completed ORCA
6.1.1 calculations, not invented outputs or copies of program source.
Operational paths, author/program banners and unrelated timings were removed;
table values and mode indices were retained. They are distributed as test data
under the repository license.

- `water.out`: PBE/def2-SVP optimization and harmonic frequencies for the
  water input in `docs/examples/orca-water/frequencies.inp`. Compare with its
  independent `frequencies.hess`.
- `peroxide.out`: PBE/def2-SVP optimization, analytical frequencies, polarizability
  derivatives and VCD for `docs/examples/orca-raman-vcd/calculation.inp`.
  Compare with the independent `calculation.hess` there.
- `water-cis.out`: water CIS/def2-SVP electronic transitions, length and velocity
  absorption and CD tables. `water-cis.property.json` retains only geometry,
  calculation identity and electronic spectra from the actual matching typed
  property file, parsed using the existing property reader.

Output frequencies have two decimals; displacement vectors have six.
The Raman Hessian has four-decimal activities whereas stdout prints six, so
comparison permits the sum of their half-last-digit rounding errors.
No extra mass weighting is applied: ORCA's printed vectors already include
its inverse-square-root-mass transformation and are Cartesian displacements.
Tests compare numeric arrays and exercise failures as well as successful import.
