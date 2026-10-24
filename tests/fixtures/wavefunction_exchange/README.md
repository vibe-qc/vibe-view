# Producing-program wavefunction references

These are unmodified numerical outputs from small water calculations generated
for the viewer's exchange-reader tests. They contain no executable program code.
This generated test data is released under the repository's license.

- `psi4_pure`, `psi4_cartesian`: Psi4 1.9.1 RHF/cc-pVTZ, spherical or Cartesian,
  C1 symmetry, fixed orientation. Original `fchk`, `molden`, `cubeprop` exports;
  0.5 bohr grid spacing, 3 bohr overage. Orbital indices 1,5,6 (one-based),
  original alpha/beta/total/spin density cubes. `receipt.json` records energy.
- `orca`: ORCA 6.1.1 original water TDDFT/PBE/def2-SVP GBW exported separately
  through `orca_2mkl -molden` and `orca_2json` with basis/MO export enabled.
  Original `orca_plot` MO0, MO4, MO5 cubes on [-4,4] bohr, 17 points per axis.
  Exact 0.5 bohr increments avoid ambiguity from rounded cube grid headers.
  No GBW binary or ORCA code is included.

Reference thresholds, chosen before comparing: orbital max absolute <=2e-5
and relative L2 <=2e-5 atomic units. Global MO sign alignment is allowed;
per-AO/per-grid fitting and empirical renormalization are not. Density checks
also expose literal cube decimal-rounding intervals (not exact reference values).

Conventions and generating APIs:
https://psicode.org/psi4manual/master/fchk.html
https://psicode.org/psi4manual/master/cubeprop.html
https://www.theochem.ru.nl/molden/molden_format.html
https://www.faccts.de/docs/orca/6.1/manual/contents/utilitiesvisualization/orca_2json.html

- `orca_f`: ORCA 6.1.1 RHF/cc-pVTZ, same water coordinates as Psi4,
  spherical f shells; native Molden, GBW-to-JSON and native cube exports.
  Valence MO4/MO5 pass the original numerical bounds. Core MO0 from BOTH
  readers exceeds the raw absolute 2e-5 bound (3.8603e-5), while relative L2
  is 3.23e-6; the peak's six-significant-digit cube text cannot resolve that
  absolute threshold. This raw limitation is retained, not labelled PASS.
  The supplemental test checks the printed-token uncertainty independently.
