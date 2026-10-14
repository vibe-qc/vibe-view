# Worked example: Raman and VCD through ORCA and vq

This example uses one twisted hydrogen-peroxide conformer to exercise IR,
Raman and signed VCD spectra together. The supplied results were calculated
with ORCA 6.1.1 through vq, using PBE/def2-SVP and analytical frequencies.
A reflected geometry provides an independent check of the VCD sign.

## 1. Get the example

Download these files into a new directory:

- {download}`Starting geometry <examples/orca-raman-vcd/peroxide.xyz>`.
- {download}`Optimization and spectroscopy input <examples/orca-raman-vcd/calculation.inp>`.
- {download}`Native calculated Hessian <examples/orca-raman-vcd/calculation.hess>`.
- {download}`Scientific VCD output excerpt <examples/orca-raman-vcd/calculation.out>` — final geometry, printed table and completion marker from the real run, with operational details omitted.
- {download}`Complete viewer dataset <examples/orca-raman-vcd/peroxide.qvf>`.

To inspect the supplied results without installing ORCA or vq:

```sh
vibe-view open peroxide.qvf
```

For a new calculation, follow [ORCA and vq setup](orca.md#3-prepare-vq-once).
Raman analytical derivatives in this workflow require ORCA 6.1 or newer.
VCD requires analytical frequencies and is unavailable with `NumFreq`.
The [FACCTs vibrational spectroscopy chapter](https://www.faccts.de/docs/orca/6.1/manual/contents/spectroscopyproperties/vibrations.html)
describes both calculations and the meaning of Raman activity.

## 2. Prepare and submit

Open `peroxide.xyz`, select **Structure**, and open **Calculation Parameters**.
Choose **ORCA**, **PBE**, **def2-SVP**, charge **0**, multiplicity **1**, one
process, and **1000 MB** MaxCore. Choose **Single Point + Freq + Opt** and
enable **Raman activities (ORCA 6.1+)** and **VCD rotatory strengths**.
Preview the input: it includes `Opt AnFreq`, `%elprop Polar 1 end` and
`%freq doVCD true end`. Save a copy, then use **Submit ORCA to vq** and
**Fetch & open results**, as in the [water tutorial](orca_water.md#2-submit-fetch-and-open).

For the exact reference run, use the downloaded input below. It tightens the
optimization with `TightOpt`; the basic form uses `Opt`. Downloaded-file edits
do not change what the viewer form submits. Submit the edited input directory
through the vq command line:

```{literalinclude} examples/orca-raman-vcd/calculation.inp
:language: text
```

```sh
mkdir peroxide-run
cp calculation.inp peroxide-run/
vq submit localhost -d peroxide-run --program orca --cpus 1 --mem-mb 1334 \
  --wall-time-seconds 600 --job-name orca-peroxide -- /opt/orca/orca calculation.inp
```

Replace `/opt/orca/orca` with your installed executable. Use the job identifier
returned by your submission:

```sh
vq status localhost JOB_ID
vq logs localhost JOB_ID
vq fetch localhost JOB_ID -o fetched
```

Wait for completion and `ORCA TERMINATED NORMALLY`. In the fetched job
directory, keep `calculation.property.txt`, `calculation.gbw` and
`calculation.hess` together, including vq’s `stdout.log` for VCD. Set up the
matching `orca_2json`, then run:

```sh
vibe-view open calculation.hess
```

The viewer combines the structure, orbitals, properties, vibrations and all
three vibrational spectra. A standalone Hessian opens its geometry, modes
and IR/Raman spectra without an ORCA installation. VCD comes from the matching
completed output log; ORCA 6.1.1 does not
write it to the Hessian or property file. The downloadable output excerpt
can be paired with the supplied Hessian to exercise this import without GBW.

## 3. Inspect Raman and VCD

1. Select **Vibrations** to inspect the optimized structure and animate a mode.
   ORCA numbers modes from zero; the viewer's mode selector starts from one.
2. Select **Raman Spectrum**. Set **Display** to **Sticks only** and leave
   **Normalize** off. The vertical axis is scattering activity in Å⁴/amu.
   The imported JSON also retains each mode's depolarization ratio.
3. Select **VCD Spectrum**, again with normalization off. Positive and negative
   sticks must remain on opposite sides of zero. Read the rotatory-strength
   unit on the axis; these values are not molar Δε.
4. Switch to **Envelope + sticks** to inspect overlapping bands. Changing
   broadening changes the displayed curve, not the stored strengths. No
   laser-frequency, temperature or experimental-intensity correction is
   applied to Raman activities.
5. Select **Wavefunction** for the orbitals or **Atomic Properties** for the
   final-geometry charges. They belong to this same calculation dataset.

Drag the divider above **Result details** upward to enlarge the chart.

```{figure} images/orca-peroxide-raman.jpg
:alt: Real ORCA hydrogen-peroxide Raman activities in vibe-view, with activity and Raman-shift axes.
:width: 100%

Raman activities from the fetched Hessian. The broadened curve sums nearby
bands; switch to sticks when comparing the tabulated activities.
```

```{figure} images/orca-peroxide-vcd.jpg
:alt: Real ORCA hydrogen-peroxide VCD spectrum in vibe-view, showing both positive and negative rotatory strengths.
:width: 100%

The completed output's VCD strengths, with their signs and native units.
```

## 4. Compare the calculated results

The original run contains four atoms, 38 spatial orbitals and 18 electrons.
Its final SCF energy is **−151.270133127844 Eh**. The imported geometry agrees
with the optimized XYZ to better than `1e-8` Å. Archive validation passes for
both supplied QVFs.

| ORCA mode | Wavenumber / cm⁻¹ | Raman activity / Å⁴/amu | VCD / 10⁻⁴⁴ esu² cm² | Mirror VCD / same units |
|---:|---:|---:|---:|---:|
| 6 | 328.82511 | 6.7666 | +194.95 | -194.96 |
| 7 | 911.38108 | 9.6784 | -9.19 | +9.19 |
| 8 | 1263.81557 | 2.4918 | +7.24 | -7.24 |
| 9 | 1405.45587 | 9.6360 | -8.39 | +8.38 |
| 10 | 3637.41726 | 35.3440 | -54.89 | +54.90 |
| 11 | 3637.98379 | 90.8819 | +28.83 | -28.84 |

The Hessian prints Raman activities and depolarization ratios to four decimal
places; the main output prints six. Imported activities agree with that
output within `0.00005` Å⁴/amu. The VCD output prints strengths to two decimal
places, which the importer preserves. Frequencies use the more precise
Hessian values, matched to each printed table by mode index.

To repeat the mirror check, download the
{download}`mirrored optimized input <examples/orca-raman-vcd/mirror.inp>`
and {download}`mirror dataset <examples/orca-raman-vcd/mirror.qvf>`.
The input was generated by vibe-view with **Frequencies**, Raman and VCD
enabled, after reflecting every optimized z coordinate. Submit it in its
own directory as `calculation.inp`, using the same vq command and a new job
name. No second geometry optimization is needed for this comparison.

The maximum frequency difference was **0.01591 cm⁻¹**, the maximum Raman
activity difference **0.0081 Å⁴/amu**, and the largest residual after adding
opposite VCD strengths **0.01** in the table's units. These are observed
numerical differences for this setup, not universal acceptance tolerances.

Both outputs reached `ORCA TERMINATED NORMALLY` with exit code 0. The original
queue job completed; vq classified the mirror job as `time_exceeded` under
the initial five-minute budget despite complete scientific output. The
numerical comparison uses those complete files; it is not a claim that the
mirror queue lifecycle passed. The commands above allow ten minutes. Check
both queue status and output completeness for your own run.

## 5. Interpret the example

These are harmonic results for one conformer at a modest basis size. They
are an import and display example, not an experimental spectrum prediction.
The mirror calculation tests handedness: reflection leaves frequencies and
Raman activities unchanged within numerical tolerance, while signed VCD
rotatory strengths reverse. Equal populations of mirror conformers cancel
VCD; do not interpret this single-conformer plot as a net signal for an
unresolved mixture.

Keep the original ORCA files. The QVF contains the imported data, not every
response tensor or restart record. Neither isotope recalculation, resonance
Raman nor anharmonic spectra are implemented by this Hessian importer.

## 6. Compare both datasets and export spectra

Open the downloaded original and mirror archives together:

```sh
vibe-view open peroxide.qvf mirror.qvf
```

Select **VCD Spectrum**, enable **Compare across open files**, leave
**Normalize** off and use **Native unit**. Both archives have one VCD section
with identical units, so their signed strengths can be compared directly.
Switch to **Raman Spectrum**: the two curves should nearly coincide. These
are separate calculation datasets; opening them together does not merge
opposite-handed structures or average their strengths.

For a numerical export:

1. Turn **Compare across open files** off and select the intended dataset in
   **Files**. **Export CSV** exports the active dataset, not every overlaid file.
2. Select **Native unit**, leave **Normalize** off, and click **Export CSV**.
3. In the file, distinguish the `# envelope` block from the `# peaks` block.
   The first is the sampled broadened curve; the second contains the discrete
   source strengths. Broadening changes the envelope, not the peaks.
4. The CSV column is generically named `intensity`. For this Raman example it
   means Å⁴/amu activity; for VCD it means rotatory strength in 10⁻⁴⁴ esu² cm².
   Keep those units with your exported data when importing it into another tool.
5. Repeat for the other dataset. Compare the peak values with the reference
   table above; the CSV rounds coordinates and strengths to six significant
   figures, so the archive remains the more precise source.

You can read the stored arrays without a browser or plotting dependency:

```python
from vibeview import QVFReader

with QVFReader("peroxide.qvf") as reader:
    for section_id in ("orca_raman", "orca_vcd"):
        spectrum = reader.read_spectra(section_id)
        print(section_id)
        for label, wavenumber, strength in zip(
            spectrum.labels, spectrum.frequencies, spectrum.intensities
        ):
            print(label, wavenumber, strength)
```

The printed labels retain ORCA's zero-based mode indices. Raman includes
zero-frequency translational/rotational rows from the Hessian, while VCD
contains only the modes printed in its output table. Match by mode label,
not by assuming the two arrays have identical row numbers.

## 7. Resolve missing or unexpected spectra

| Symptom | Check and next step |
|---|---|
| The form rejects Raman/VCD | Select **Frequencies** or **Single Point + Freq + Opt**. These options cannot accompany a single point or optimization-only task. |
| Only IR appears | Check the submitted input, not a later preview: Raman needs `Polar 1`; VCD needs `doVCD true` and analytical frequencies. A change in the form does not modify a job already submitted. |
| Raman appears but VCD does not | Keep `stdout.log` with the fetched files, or pair a matching `calculation.out` with `calculation.hess`. A browser upload of the Hessian alone cannot include the output. |
| VCD import rejects the output | Check normal termination and fetch the matching Hessian/output from one run. Multiple VCD tables, unknown units and geometry/frequency mismatches are deliberately rejected. |
| The queue reports `time_exceeded` | Inspect the log and completeness of the expected files. If incomplete, resubmit in a fresh directory with a larger time budget. A normally terminated ORCA output and a successful queue lifecycle are distinct checks. |
| VCD is all zero | An achiral structure or cancellation can have zero VCD. Check the conformer and calculation; missing data and calculated zero strengths are different cases. |
| Peaks near 3638 cm⁻¹ seem to merge | The example has two nearby modes. Use **Sticks only** or reduce broadening; do not infer a missing mode from the envelope alone. |
| The chart is too small | Drag **Result details** upward. Current spectrum plots resize with the panel, including the negative VCD range and axis labels. |

If your installed viewer has neither the Raman/VCD input options nor these
spectrum sections, check which version you installed. This chapter follows
the source version; public release packages and the deployed handbook may
lag main. Use a source revision containing the feature, or wait for its
release, rather than changing a scientific input to work around an older viewer.
