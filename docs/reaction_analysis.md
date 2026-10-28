# Correlated reaction endpoint analysis

`vibe-view reaction` selects correlated **electronic total** energies from
QVF and computes the signed stoichiometric sum. This is separate from
`batch-compare`, which reads SCF reference energies. Neither route substitutes
correlation energy, a triples correction, enthalpy or Gibbs energy for an
electronic total. MMFF/MACE traces stay on their own energy surfaces.

This feature currently supports molecular `dlpno-ccsd(t)` results with the
explicit `real_restricted_cc` reference contract. It requires QVF root
`energies`, resolved `provenance.correlation_settings`, independently true
SCF/correlation convergence and a usable internally stable restricted
reference. An externally unstable RHF reference is not silently declared
universally stable: both channel verdicts remain in the recorded provenance.
The selected result structure and run must match the producer's
`result_structure_ref` and `result_run_ref`, including latest-run ordering.
Historical archives containing only RHF `scf_energy` are refused. Copying
values from an acceptance JSON or output log is not this analysis operation.

## Select and review

First inspect the drawing's `reaction.scheme` and each result:

```sh
vibe-view info scheme.qvf --json
vibe-view reaction inspect butadiene.qvf > butadiene-inspection.json
vibe-view reaction inspect ethene.qvf > ethene-inspection.json
vibe-view reaction inspect cyclohexene.qvf > cyclohexene-inspection.json
```

Inspect output structures, optimization histories, vibrational modes,
method/settings, producer verdicts and runtime identities before recording
scientific acceptance. Convergence alone does not establish a minimum or
validate an energy/force surface. Keep rejected attempts and original
artifacts. For the Digital Discovery replay use a **released** vibe-qc
`>=0.18,<0.19`; no particular patch is required. The publication owner decides
whether earlier geometries/Hessians remain acceptable after numerical fixes.
A saved analysis is evidence of the review recorded in it, not a new QC validation.

Create `selections.json`. This file contains selection/review controls, not
energies or method settings. Keys under `endpoints` must be the exact structure
IDs in the selected scheme. Paths are relative to this selection file. Repeat
the endpoint entry for all three species, replacing the digest and review note
with the actual archive SHA256 and scientific decision:

```json
{
  "scheme_section": "reaction_scheme",
  "step": "reaction1",
  "endpoints": {
    "structure_butadiene": {
      "path": "butadiene.qvf",
      "structure": "structure",
      "quantity": "correlated_total",
      "method": "dlpno-ccsd(t)",
      "property": "/energies/total",
      "review": {
        "status": "accepted",
        "source_sha256": "REPLACE_WITH_ACTUAL_SHA256",
        "note": "REPLACE_WITH_SCIENTIFIC_REVIEW_AND_EVIDENCE_REFERENCE"
      }
    }
  }
}
```

The incomplete example above deliberately cannot produce a reaction result.
Use `/energies/by_method/N/total` only for an explicitly inspected zero-based
entry of that method. Conflicting totals are refused. The original energy
units and their conversion to hartree are stored alongside the selection.

```sh
vibe-view reaction build scheme.qvf selections.json -o reaction-analysis.qvf
vibe-view reaction export reaction-analysis.qvf --step reaction1 \
  --csv reaction-table.csv --figure reaction-endpoints.svg > reaction-analysis.json
vibe-view open reaction-analysis.qvf
```

Select **Reaction Scheme** in the viewer's sidebar. Its panel reopens the
embedded source QVFs, checks their hashes, selections, method and acceptance,
and recomputes the sum before displaying it. Expand the provenance disclosure
to inspect method settings, units, SCF/reference verdicts and source properties.
Select each structure section to inspect the corresponding endpoint geometry.
The CLI export uses the same checked calculation. New output names preserve
previous analyses and exports. CSV contains signed coefficients, source hashes,
property and structure selectors, complete methods, review and conversion.
SVG metadata contains the complete replay result; the figure has two
**disconnected endpoint levels** and no inferred barrier or reaction path.

For butadiene + ethene → cyclohexene, chemical-forward coefficients are −1,
−1 and +1. Displaying a retrosynthetic arrow does not reverse the chemical
roles. Atoms and charge must balance, every species must be present once,
and source composition, atom ordering/connectivity, charge and multiplicity
must match the scheme. Reordered atoms are conservatively refused; this route
does not infer an atom map or assign stereochemistry. User review must check
stereochemistry and conformer identity. All resolved scientific settings and
producer versions must agree; the species-dependent frozen-core count is
retained separately from the common chemical-frozen-core policy (including
the producer's explicit `orca-6.1-table-2.69-count-only-default` convention). Unknown or
incomplete settings are not treated as equal methods.

The result is `Σ coefficient × energy`, evaluated with `math.fsum`. The
recorded conversion is 627.509474 kcal mol⁻¹ per hartree × 4.184 kJ/kcal =
2625.499639216 kJ mol⁻¹ per hartree, following QVF's CODATA 2018 convention.
The source-unit normalization is also recorded. Electronic endpoint energy is
not a thermal free energy, rate, yield or uncertainty estimate.

## Walk through the scientific evidence

Open original stage QVFs and the saved analysis in one viewer session:

```sh
vibe-view open preparation.qvf mace.qvf pbe-opt.qvf pbe-hessian.qvf \
  dlpno.qvf reaction-analysis.qvf
```

Use the **Files** dropdown to switch files, then the section sidebar to select
structures, trajectories, vibrations or Reaction Scheme. For each species:

1. Select its preparation and accepted optimized structures. Inspect identity,
   charge/spin, butadiene conformation and cyclohexene ring geometry.
2. Select each stored trajectory. Play and scrub its actual frames and energy
   history. Compare MACE only within MACE, and PBE only within PBE.
3. Select the Hessian/vibrations section. Inspect the frequency table, select
   the reported low-frequency modes and animate their displacements. Inspect
   both stencil results; absence of an imaginary mode is only one acceptance
   check, not proof of force consistency.
4. Open the result QVF's run record and job specification. Confirm DLPNO total,
   reference, CCSD correlation and triples components via `reaction inspect`.
   Orbitals in these archives may describe the RHF reference.
5. Open the saved analysis, select Reaction Scheme and inspect ΔE and its source
   selections. Export/reopen and compare the numerical JSON result. This final
   operation is an endpoint analysis, beyond a structure screenshot.

A publication walkthrough must use actual accepted artifacts and a containing
released viewer. Synthetic test fixtures prove software behavior only. An
original archive lacking the required fields should produce a recorded refusal;
it must not be relabelled as an integrated QVF workflow. Do not launch duplicate
publication calculations to fill missing metadata. Coordinate the final replay
with its existing owner after the producer and viewer releases are available.

## QVF representation and compatibility

With the pinned QVF toolkit 0.6, canonical step `energies` is reserved. This
implementation writes `reaction.scheme` → `steps[i].x_vibeview.energies`, using
the version-1 term/delta layout agreed in QVF #16, and declares the noncritical
`x_vibeview` extension version `1`. It does not modify the vendored schema.
A future released QVF pin can promote the same object to the canonical key.
A reader refuses conflicting canonical, `x_vibesketch.energies` and
`x_vibeview.energies` carriers. The older sketch energy report is not inferred
to be this replayable analysis.

Each term has its structure reference, signed coefficient, `energy_eh`, complete
method, `run_ref` and source basename/SHA256/property location. The energies
object's `x_vibeview` metadata records quantity, conversion and selections,
including the source structure/run, source energy and unit normalization, and
hash-bound review. A `run.record` attachment retains each complete source QVF
byte for byte. Another retains the original scheme. Original stage histories,
failed attempts and drawing information are not overwritten. Reopening needs
no external source path and rechecks saved structures, charges/spins, totals,
methods, settings and delta. Source attachments are bounded by the existing
64 MiB viewer attachment limit; larger inputs are refused.

Source implementation and focused tests are tracked in vibe-view #164.
Producer metadata is coordinated with vibe-qc #702; shared interchange and
profile validation are coordinated with QVF #16 and the sketch workflow owner.
