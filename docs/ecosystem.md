# vibe-view with vibe-sketch and vibe-qc

vibe-view works on its own, and also next to two related programs:

* **vibe-sketch** draws molecules and reaction schemes in 2D, and prepares
  publication graphics.
* **vibe-qc** is a quantum-chemistry program that writes its results as QVF.

vibe-view owns 3D structure work and result inspection. The split between it
and vibe-sketch is recorded in the [scope decision](sketch_view_scope.md). All
three can prepare inputs and submit through the same
[vq](https://github.com/vibe-qc/vibe-queue) queue. None of them needs the
others to start, and a QVF file is the common format between them.

This page shows how to install them side by side, move structures between
vibe-view and vibe-sketch, and use the vibe-qc features vibe-view offers.

## Install them side by side

| Program | How it installs | What vibe-view needs from it |
|---|---|---|
| vibe-view | Pure Python 3.12+, no compiler ([Installation](installation.md)) | — |
| vibe-sketch | Pure Python 3.12+, from its checkout; distribution `vibesketch`, command `vibe-sketch` | The `[sketch]` extra, for **Sketch in vibe-sketch** |
| vibe-qc | A compiled C++ core; built with its own `scripts/install.sh` ([vibe-qc installation](https://vibe-qc.com/docs/)) | Optional. See [the features table](#features-that-use-vibe-qc) for what each feature needs. |
| vq | Pure Python 3.12+ ([Clusters and remote machines](cluster.md#2-install-vq)) | The `[queue]` extra, for the Job Manager and submission |

vibe-sketch and vq are on no package index, so install each from its checkout
before adding the matching vibe-view extra:

```sh
cd ~/vibe-view
.venv/bin/python -m pip install ~/vibe-sketch
.venv/bin/python -m pip install -e '.[sketch]'
```

**Keep vibe-qc in its own environment.** It has a native build with its own
dependencies, and vibe-view talks to it through files and separate
processes. Only two features, live optimisation and `vibe-view quickstart`,
need vibe-qc importable inside the viewer's environment. If you want those,
install the viewer into the vibe-qc environment instead:

```sh
source ~/vibe-qc/.venv/bin/activate      # Python 3.12 or newer
python -m pip install -e '../vibe-view[viewer,tui]'
```

On a cluster, build vibe-qc inside a job allocation rather than on the login
node, as its cluster guide describes, and register the finished environment
with vq (see [Register a program with vq](calculations.md#register-a-program-with-vq)).

## Draw in vibe-sketch, continue in 3D

### Start from vibe-view

1. Open the **Build molecule** dialog and click **Sketch in vibe-sketch** at
   the top, or choose *Sketch a molecule in vibe-sketch* from the command
   palette (Ctrl/Cmd+K).
2. vibe-sketch opens in a new browser tab. Clicking again while it runs
   brings that tab back instead of starting a second copy.
3. Draw the molecule. Set its charge and multiplicity on the species card.
4. Click **Send to vibe-view** on the species card. vibe-view opens the
   structure as a new file, with its charge and multiplicity, and the status
   bar says *Received NAME (N atoms) from vibe-sketch*. Each send opens
   another file; earlier ones are kept.
5. Continue in vibe-view: [edit the geometry](editing.md), relax it with
   [live optimisation](editing.md#live-optimisation-while-you-build), prepare
   a calculation and [submit it](calculations.md).

Closing vibe-view stops the sketcher it started. Save your drawing in
vibe-sketch first. How the two programs exchange files is described in
[The hand-off between the two programs](editing.md#the-hand-off-between-the-two-programs).

Without the `[sketch]` extra, the button is still shown, and clicking it puts
the install command in the status bar.

### Start from vibe-sketch

When vibe-sketch runs on its own and vibe-view is installed, the species card
offers **View in 3D**. It runs `vibe-view open` on that species as a QVF file
and opens the viewer in your browser. From the command line:

```sh
vibe-sketch view DOCUMENT --species ID
```

### Calculations started in vibe-sketch

vibe-sketch's **Inputs** section writes ORCA or vibe-qc inputs, and **Submit
to vibe-queue** submits checked species, one reaction, or a whole scheme
through vq. It uses the same program registrations (`orca`, `vibeqc`) as
vibe-view. vibe-sketch does not open results in vibe-view by itself. Open the
finished files with vibe-view, and use `--view` to land on the result you want:

```sh
vq fetch localhost JOBID -o results/
vibe-view open results/JOBID/result.qvf --view orbitals --orbital HOMO
vibe-view open results/JOBID/result.qvf --view mode --mode 7
vibe-view open results/JOBID/result.qvf --view spectrum
```

`--view` takes `orbitals`, `density`, `mode` or `spectrum`. See
[Launch options](browser.md#launch-options).

### Not available yet

Two directions are proposed but not implemented:

* returning a geometry edited in vibe-view to the same species in the sketch,
  without moving its 2D drawing;
* opening a result view from a calculation in vibe-sketch with one click.

Until then, save the edited structure from vibe-view and add it in vibe-sketch
as a new structure. The scope decision's
[implementation status](sketch_view_scope.md) records what is done.

## Get results from vibe-qc

vibe-qc writes a QVF archive by default. A `.qvf` from a vibe-qc run opens in
every vibe-view surface with no further setup:

```python
from vibeqc import Atom, Molecule, run_job

water = Molecule([Atom(8, [0.0, 0.0, 0.0]),
                  Atom(1, [0.0, 1.43, -0.98]),
                  Atom(1, [0.0, -1.43, -0.98])])      # bohr
run_job(water, basis="6-31g*", method="rks", functional="PBE",
        optimize=True, hessian=True, write_molden_file=True,
        write_cube=["density", "homo", "lumo"],
        output="water", checkpoint_qvf="water.live.qvf")
```

```sh
vibe-view open water.qvf --view mode --mode 7     # an animated vibration
vibe-view show water.qvf                          # in a terminal
```

What ends up in the archive follows the options you set: the optimisation
trajectory, orbitals (`write_molden_file`), densities and orbital grids
(`write_cube`), vibrations with an IR spectrum (`hessian`), and UV-Vis spectra
and transition orbitals from TD-DFT. The vibe-qc documentation lists every
option. [Producing QVF files](qvf.md#producing-qvf-files) covers what any
producer must write.

`checkpoint_qvf=` streams a live archive while the job runs. Open it with
**Auto-reload on file change** on, or with **Watch live** in the Job Manager,
to follow the calculation; see [Streaming checkpoints](jobs.md#streaming-checkpoints).
Molecular jobs write a start and a final checkpoint. The periodic routes also
write one per iteration.

## Features that use vibe-qc

| Feature | Where | What it needs | Without it |
|---|---|---|---|
| **Export vibe-qc input** (`.py`), `vibe-view export FILE -f py` | Export menu, CLI | Nothing; text only | — |
| Read a vibe-qc input script | `vibe-view open input.py` | Nothing; the script is parsed, never executed | — |
| **Submit to vq cluster** | App bar → submit | vq with a registered vibe-qc program | A missing vq gives a status message |
| **Submit as QVF container** | The submit dialog | vibe-qc importable next to the viewer, to write the pending archive | The switch is disabled and a generated script is submitted instead |
| Run without a queue | The submit dialog | vibe-qc importable, vq absent | The job runs locally in the launch directory, and the status line says so |
| **Re-localize with** IBO, Foster-Boys or Pipek-Mezey; **New RHF + localize** | The wavefunction panel | A vibe-qc environment named under **Settings → vibe-qc Python executable** (**Save backend and check**) | The action stays disabled and the panel explains why |
| **Auto-optimize**, with the MSINDO or MACE engine | The atom editor | vibe-qc importable in the viewer's environment (MACE also needs its model) | The switch turns itself off and reports *vibe-qc not importable* |
| `vibe-view quickstart` | CLI | vibe-qc importable | Prints an install hint and exits |

The details are in [Submitting from the viewer](jobs.md#submitting-from-the-viewer),
[QVF containers](jobs.md#qvf-containers),
{ref}`Configure re-localization <configure-relocalization>` and
[Live optimisation while you build](editing.md#live-optimisation-while-you-build).

### Choosing the vibe-qc runtime

QVF containers run on a vibe-qc environment registered with vq. The viewer
picks it in this order:

1. the program named by `VIBEVIEW_VQ_PROGRAM`, if set;
2. the registered program whose environment is the one running the viewer;
3. the only registered program with `import_check = "vibeqc"`.

When none qualifies, or several do and nothing chooses, nothing is submitted
and the status line says why. Pin a runtime to a revision with
`expected_git_sha` in its vq entry, so a container always runs on the vibe-qc
build it was prepared for.

## A complete round trip

This sequence uses all three programs, and every step is covered above:

1. Draw the molecule in vibe-sketch and click **Send to vibe-view**.
2. In vibe-view, adjust the geometry and relax it with **Auto-optimize**.
3. In **Calculation Parameters**, choose the vibe-qc engine, a method, a
   basis and *frequencies*. Click **Submit to vq cluster** with **Submit as
   QVF container** on.
4. Watch the job in the Job Manager. When it completes, **open** loads the
   settled container with its results, Job Spec and Run Info panels.
5. Inspect the vibrations and the IR spectrum, then make a figure (see
   [Publication figures](publication_figures.md)) or save the archive to share.

For a job on a SLURM or PBS cluster, replace step 3's submit button with a
script or container submitted by `vq`, as described in
[vibe-qc jobs for a cluster](calculations.md#vibe-qc-jobs-for-a-cluster), and
fetch the result with `vq fetch`.
