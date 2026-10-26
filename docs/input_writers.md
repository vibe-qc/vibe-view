# Preparing native calculation inputs

Open **Calculation Parameters**, choose a program, and enter charge,
multiplicity, method, basis and job type. Blank method/basis fields select the
program defaults below. **Edit → Freeze selected** supplies the frozen atoms
from the current geometry, including edited positions. **Preview input bundle**
regenerates all files; **Save input bundle** downloads a ZIP. Save regenerates
from the current form, so an old preview is never silently reused.

These are input writers, not executable discovery or predictions of convergence.
Review native keywords and the installed program's method/basis support before
running. The previews include complete auxiliary files and warnings. Unknown
keywords pass through unchanged: Gaussian text extends its route section; Psi4
and NWChem text precedes the calculation call; Q-Chem, GAMESS and CP2K accept
additional native blocks; xtb text goes into `xcontrol`. Raw keywords can change
or override physics and are deliberately not interpreted or sanitized. Do not
paste untrusted code into Psi4 inputs. Multiline route text must obey Gaussian's
route continuation rules; blank lines end the route.

## Program capabilities and defaults

| Program | Method / basis defaults | Jobs | Periodicity and constraints |
| --- | --- | --- | --- |
| Gaussian | PBE1PBE / def2SVP | SP, optimization, frequencies, TD-DFT | Molecular jobs; 1–3 translation vectors for periodic SP/optimization; Cartesian freeze codes |
| Psi4 | pbe0 / def2-svp | All four molecular jobs | No periodic writer; optking frozen Cartesian atom coordinates |
| NWChem | pbe0 / def2-svp | All four molecular jobs | Three-dimensional periodic SP/optimization with **explicit basis `PLANE_WAVE`**, NWPW/PSPW Gamma point; frozen atom list |
| Q-Chem | PBE0 / def2-svp | All four molecular jobs | No periodic writer; `$opt FIXED` atom list |
| GAMESS (US) | PBE0 / N31 (6-31G) | All four molecular jobs | No periodic writer; Cartesian `$STATPT IFREEZ` coordinate indices |
| CP2K | PBE / DZVP-MOLOPT-SR-GTH | All four jobs | Explicit simulation cell required, including for molecules; periodic axes retained; fixed fractional coordinates in a fixed cell |
| xtb | GFN2-xTB / intrinsic basis | SP, optimization, Hessian | GFN0/1/2-xTB molecules; periodic GFN-FF requires xtb ≥6.7 and three periodic axes; exact `$fix` atoms |
| VASP structure | No electronic settings | POSCAR only | Three periodic axes, species grouping and selective-dynamics flags; original atom order recorded separately |

There is no TD-DFT method in xtb: sTDA is not substituted. POSCAR does not encode
charge, spin, method, basis or job type. Its writer requires the default neutral
singlet form values, blank method/basis/raw fields and `single_point` as the
structure-export selector. These values do **not** assert the electronic state
of the eventual VASP run. Provide matching POTCAR, INCAR and KPOINTS separately;
the writer never guesses valence electron counts for NELECT.

Frozen indices in the SDK are zero based. Native writers translate them to
one-based atom/coordinate indices. Except POSCAR selective dynamics, frozen
atoms currently require the optimization job type. A constrained Hessian is a
different physical calculation and is rejected rather than silently exported
as a full unconstrained Hessian. Freeze all three coordinates per selected atom.

CP2K supports PBE, BLYP and PADE in this interface. Supply exact per-element
potential names as JSON, for example `{"H":"GTH-PBE-q1","O":"GTH-PBE-q6"}`,
and install matching `BASIS_MOLOPT` and `GTH_POTENTIALS` files. Hybrid setup needs
native editing. The cutoff field is **Ry** (default 400); NWChem converts this
to its hartree cutoff. NWPW uses the installed pseudopotential library and
requires convergence checks; selecting a Gaussian basis for a periodic
NWChem job is an error. Triclinic NWChem cells become lengths/angles with
fractional coordinates, preserving geometry up to rigid rotation.

For CP2K's 0D/1D/2D electrostatics the writer uses ANALYTIC and requires an
axis-aligned orthorhombic simulation cell. Converge vacuum extent and cutoff.
For 3D, the normal PERIODIC solver retains arbitrary nonsingular cells. No
vacuum box is guessed. A QVF synthetic nonperiodic lattice axis is not evidence
of an adequate physical simulation box: set a suitable explicit cell first.

GAMESS basis choices are STO-3G, N31/6-31G, 6-31G*, CCD and CCT; these map to
GBASIS/NGAUSS/NDFUNC directives. Other custom basis definitions need native
editing. Open-shell electronic writers use an unrestricted reference where a
reference keyword is required. Charge/multiplicity parity is checked against
the all-electron atom count before generation.

## Queue submission

The existing vibe-qc and ORCA paths remain unchanged. Native Psi4, NWChem,
Q-Chem, CP2K and xtb bundles can use **Submit serial job to local vq** only when
vq is installed and an absolute executable binary is registered under `psi4`,
`nwchem`, `qchem`, `cp2k` or `xtb`. The launch is one process, 1 CPU, 2 GB, with
an explicit wall-time field; no shell interpolation or implicit fallback to
vibe-qc occurs. vq owns staging and execution. These controls do not infer a
remote runtime from a local path. Parallel or remote launches can be prepared
from the saved bundle using the queue's own supported configuration.

Gaussian's stdin launcher, GAMESS's installation-specific launcher and the
incomplete VASP structure bundle are save-only in this panel. A registered
binary alone is insufficient to invent a valid command for these programs.
`command.json` in an xtb bundle records an argv array; it is not executed by
preview or download.

## SDK

```python
from vibeview.input_writers import InputSettings, write_input

bundle = write_input(structure, InputSettings(
    program="Q-Chem", method="PBE0", basis="def2-svp",
    charge=0, multiplicity=1, task="optimization", frozen=(0,),
    raw_keywords="$comment\nPrepared constrained geometry\n$end",
))
# bundle.primary, bundle.files and bundle.warnings are available for inspection.
# bundle.archive() returns ZIP bytes; generation never writes or executes files.
```

## Recorded syntax references and acceptance

The development check is **manual-reference validation**, not an installed
program execution or a scientific accuracy claim. The following authoritative
references were checked for the generated syntax; `test_input_writers.py`
exercises charge +1/multiplicity 2 water, tasks, atoms 1 and 3 frozen,
triclinic periodic cells, rejection cases and exact queue staging. POSCAR also
round-trips through ASE's independent VASP parser. Other engines are not
required dependencies of the viewer, and their input-check execution remains
an installation-specific acceptance step. Live-browser acceptance is separate.

| Writer | Recorded authoritative reference and checked syntax |
| --- | --- |
| Gaussian 16 | [Molecule specification](https://gaussian.com/molspec/): charge/multiplicity, Cartesian freeze code −1, `Tv` translation vectors; [Opt](https://gaussian.com/opt/), [Freq](https://gaussian.com/freq/), [TD](https://gaussian.com/td/) route jobs |
| Psi4 | [Optking](https://psi4.github.io/psi4docs/master/optking.html): `frozen_cartesian` lines `1 xyz`; [TD-SCF](https://psi4.github.io/psi4docs/master/tdscf.html): energy-returned wavefunction, `tdscf_excitations`, `states`, `tda=False`; [PSIthon](https://psicode.org/psi4manual/master/psithoninput.html) |
| NWChem | [Constraints](https://nwchemgit.github.io/Constraints.html): `fix atom 1 3`; [lattice parameters](https://nwchemgit.github.io/SYSTEM----Lattice-parameters-for-periodic-systems.html): fractional crystal geometry; [NWPW](https://nwchemgit.github.io/Plane-Wave-Density-Functional-Theory.html): cutoff in hartree, mult, PSPW tasks; [TDDFT](https://nwchemgit.github.io/TDDFT.html) |
| Q-Chem 6 | [Frozen atoms](https://manual.q-chem.com/6.0/Ch9.S4.SS3.html): `$opt` FIXED/ENDFIXED, contiguous XYZ; [manual](https://manual.q-chem.com/6.3/): `$molecule`, `$rem` METHOD/BASIS/JOBTYPE, CIS_N_ROOTS and RPA for full TD-DFT |
| GAMESS US | [Input manual](https://www.msg.chem.iastate.edu/gamess/GAMESS_Manual/input.pdf): `$CONTRL` ICHARG/MULT/RUNTYP/DFTTYP/TDDFT, `$BASIS`, `$DATA`; `$STATPT` IFREEZ with default NZVAR=0 gives Cartesian coordinates; `$TDDFT` NSTATE excludes reference |
| CP2K | [Input tree](https://manual.cp2k.org/trunk/CP2K_INPUT.html), [fixed atoms](https://manual.cp2k.org/trunk/CP2K_INPUT/MOTION/CONSTRAINT/FIXED_ATOMS.html), [TDDFPT](https://manual.cp2k.org/trunk/CP2K_INPUT/FORCE_EVAL/PROPERTIES/TDDFPT.html), [Poisson](https://manual.cp2k.org/trunk/CP2K_INPUT/FORCE_EVAL/DFT/POISSON.html): explicit CHARGE/MULTIPLICITY/UKS, KIND basis/potential, fixed-cell fractional LIST, activated TDDFPT NSTATES |
| xtb | [Geometry](https://xtb-docs.readthedocs.io/en/latest/geometry.html): `$coord angs`, `$periodic`, `$lattice angs`; [xcontrol](https://xtb-docs.readthedocs.io/en/latest/xcontrol.html): exact `$fix`; [command line](https://xtb-docs.readthedocs.io/en/latest/commandline.html): --chrg, --uhf unpaired electrons, --opt/--hess |
| VASP | [POSCAR specification](https://vasp.at/wiki/index.php/POSCAR): row lattice, species/count ordering, Cartesian coordinates, selective dynamics; no charge/spin fields |
