# Supported programs and calculations

vibe-view takes a structure through to a calculation and back:

1. prepare an input from the structure on screen;
2. submit it through the [vq](https://github.com/vibe-qc/vibe-queue) job queue;
3. open the results in the viewer.

This page lists what each program supports at each step, shows how to register
a program with vq, works through one submission, and explains how to add a
program the viewer does not know yet.

Where the queue runs (a workstation, a SLURM cluster or a PBS cluster) is
covered in [Clusters and remote machines](cluster.md). The vibe-qc-specific
features are in [vibe-view with vibe-sketch and vibe-qc](ecosystem.md).

## What each program supports

| Program | Prepare in the viewer | Submit from the viewer | Open the results |
|---|---|---|---|
| **vibe-qc** | Calculation Parameters, engine *vibe-qc*: a Python input or a [QVF job container](jobs.md#qvf-containers) | **Submit to vq cluster**: a container or a script, with live checkpoints | QVF, natively, including running checkpoints |
| **ORCA** | Calculation Parameters, engine *ORCA* | **Submit ORCA to vq**, with CPUs, `MaxCore` and wall time | The [ORCA dataset](orca.md): property files, `.gbw`, `.hess`, `.mcd` |
| **Psi4, NWChem, Q-Chem, CP2K, xtb** | [Input bundle](input_writers.md) | **Submit serial job to local vq**: 1 CPU, 2 GB | Molden or fchk orbitals, cube volumes, XYZ trajectories; `.out`/`.log` with the `[results]` extra |
| **Gaussian** | Input bundle | Save only | `.fchk`, cube; `.log` with `[results]` |
| **GAMESS (US)** | Input bundle | Save only | Molden; `.log` with `[results]` |
| **VASP** | POSCAR structure only | Save only | POSCAR/CONTCAR with the `[ase]` extra |
| **Any other program** | Save the structure (XYZ, CIF, PDB and others) | `vq submit` on the command line | Any [supported format](formats.md), or an importer plugin |

`vibe-view formats` lists what the current environment can read, and
`vibe-view doctor` reports which optional extras are installed.

The input writers prepare text. They don't check that the installed program
supports the method and basis you chose. Review the input before running it;
[Preparing native calculation inputs](input_writers.md) gives each writer's
defaults and limits.

The viewer runs nothing silently. A program that isn't registered, or a vq
that isn't installed, produces a status message, not a fallback. The one
fallback is documented: when vq is absent but vibe-qc is importable, a vibe-qc
job runs locally in the launch directory, and the status line says so.

## Register a program with vq

vq runs only programs it has been told about. Register each one in
`~/.config/vq/config.toml` **on the machine where it runs**: your workstation
for local jobs, or the cluster login node for scheduler jobs. There is no
`vq programs add` command; edit the file, then reload the daemon.

The viewer looks programs up by these names:

| Name | Kind | Used by |
|---|---|---|
| `orca` | `binary`, absolute path | **Submit ORCA to vq** |
| `psi4`, `nwchem`, `qchem`, `cp2k`, `xtb` | `binary`, absolute path | **Submit serial job to local vq** |
| any name, with `import_check = "vibeqc"` | `venv` | **Submit to vq cluster**: QVF containers. `VIBEVIEW_VQ_PROGRAM` chooses one when several are registered. |

```toml
[programs.orca]
kind = "binary"
binary = "/opt/orca/orca"            # your installation's absolute path

[programs.xtb]
kind = "binary"
binary = "/opt/xtb/bin/xtb"

[programs.vibeqc]
kind = "venv"
python = "/home/USER/vibe-qc/.venv/bin/python"
git_dir = "/home/USER/vibe-qc"
import_check = "vibeqc"
```

Add your own entries without replacing the ones already in the file. Then
check the registrations:

```sh
vq daemon reload
vq programs localhost          # each program: OK, MISSING or UNHEALTHY
```

`vq programs HOST --require NAME` fails unless NAME is healthy, which is
useful at the top of a script. To pin a vibe-qc runtime to a revision, add
`expected_git_sha` to its entry, or pass `--expected-sha` per job.

On a cluster, a program usually needs its module loaded inside the job. Put
that in a per-program hook on the cluster host, not in a wrapper script:

```toml
[hosts.slurm.scheduler_program_hooks.orca]
prologue = "module load orca/6.1.1 openmpi/4.1"
```

## Submit a calculation: a worked example

This example takes the bundled water molecule through xtb, and works the same
way for the other serial programs. For ORCA, follow
[Prepare, save and submit an ORCA input](orca.md#prepare-save-and-submit-an-orca-input),
and for vibe-qc, [Submitting from the viewer](jobs.md#submitting-from-the-viewer).

1. **Open a structure.** `vibe-view demo` writes a water archive, and
   `vibe-view open vibe-view-demo.qvf` opens it. Your own file works too; edit
   it first if needed (see [Building and editing](editing.md)).
2. **Set the calculation.** Open **Calculation Parameters**, choose **xtb**,
   and set charge 0, multiplicity 1 and job type *optimization*. Leave method
   and basis blank for the program's default, GFN2-xTB.
3. **Preview.** **Preview input bundle** shows every file and any warnings:
   the `coord` geometry, the `xcontrol` settings, and `command.json`, which
   records the argument list. **Save input bundle** downloads the same files
   as a ZIP.
4. **Submit.** With `xtb` registered and the local daemon running, set a wall
   time and click **Submit serial job to local vq**. The Job Manager opens with
   the live monitor on.
5. **Follow.** The job's row shows its state, and the magnifier shows the log
   tail as it grows.
6. **Open the result.** The panel's **open** action loads QVF archives and
   ORCA datasets. For other programs, fetch the job and open the files it
   wrote in a [format the viewer reads](formats.md):

   ```sh
   vq fetch localhost JOBID -o results/
   ls results/JOBID/
   ```

   xtb writes its optimisation steps to `xtbopt.log` as XYZ frames. Copy it to
   a `.xyz` name and open it as a trajectory:

   ```sh
   cp results/JOBID/xtbopt.log water-opt.xyz
   vibe-view open water-opt.xyz
   ```

   The final geometry, `xtbopt.coord`, is in Turbomole format, which the
   viewer doesn't read; the trajectory's last frame holds the same structure.

To run the same bundle on a cluster or with more CPUs, submit it with `vq`
yourself. Build the command from `command.json`, with the absolute path of
xtb on that machine in place of `xtb`:

```sh
unzip water-xtb.zip -d water-xtb/
cat water-xtb/command.json
vq submit slurm -d water-xtb/ --program xtb --cpus 8 --time-limit 01:00:00 \
    -- /path/to/xtb coord --gfn 2 --chrg 0 --uhf 0 --input xcontrol --opt
```

## vibe-qc jobs for a cluster

The viewer writes a QVF job container only when it submits to its own vq
daemon. For a SLURM or PBS host, prepare the job yourself in one of two ways.

**A script.** Use **Export vibe-qc input (.py)** in the viewer, or run
`vibe-view export structure.qvf -f py`. Then submit the script with the
cluster's vibe-qc Python:

```sh
vq submit slurm --python /home/USER/vibe-qc/.venv/bin/python --program vibeqc \
    --cpus 16 --time-limit 12:00:00 water.py
```

**A container.** In a vibe-qc environment, write a pending archive and submit
it. vq runs it in place with the registered program, and the results land in
the same file:

```python
from vibeqc import Atom, Molecule, write_pending_qvf

water = Molecule([Atom(8, [0.0, 0.0, 0.0]),
                  Atom(1, [0.0, 1.43, -0.98]),
                  Atom(1, [0.0, -1.43, -0.98])])      # bohr
write_pending_qvf(water, "water-job", method="rks", basis="6-31g*", functional="PBE")
```

```sh
vq submit slurm water-job.qvf --program vibeqc --cpus 16 --time-limit 12:00:00
vq fetch slurm JOBID -o results/
vibe-view open results/JOBID/water-job.qvf
```

A container can run only once; vq refuses a settled one unless you pass
`--qvf-force`.

## Add a program the viewer does not know

A program needs three things to work with vibe-view. Each one is independent,
so start with whichever you need.

### 1. Run it: nothing to write

vq runs any executable. Register it as a `binary` program (or a `venv` program
for a Python code), save the structure from the viewer in a format the program
reads, and submit with `vq submit HOST -d DIR --program NAME -- COMMAND`. vq
has no plugin or adapter interface for programs, and none is needed: the
registration plus the command is the whole contract.

### 2. Read its results: an importer plugin

If the program writes Molden, fchk, cube, XYZ, CIF or another
[supported format](formats.md), the viewer reads its results already. If its
output parses with cclib, the `[results]` extra reads its `.out` or `.log`.

Otherwise, write an **importer plugin**: a small package that turns the
program's files into a QVF archive. vibe-view finds it through the
`vibeview.importers` entry point, uses it for `vibe-view open` and
`vibe-view import`, and lists it in `vibe-view formats`. The contract and an
example are in [Writing an importer plugin](formats.md#writing-an-importer-plugin).

If the program has results that no QVF section kind covers, write them into a
vendor section (`x_<vendor>.<kind>`) and add a **renderer plugin** for that
kind. See [Extending vibe-view](python_api.md#extending-vibe-view). A complete,
installable example is in `docs/examples/renderer-plugin/` in the source
tree.

A producer that writes QVF directly needs no plugin at all. The
[QVF guide](qvf.md) describes the format and its conformance checks.

### 3. Write its inputs in the viewer: a contribution

The input writers in `vibeview.input_writers` are part of vibe-view; there is
no plugin interface for them. Adding a program means adding a writer and its
tests in the vibe-view source:

* a writer function for the program's syntax;
* the program's defaults in the capability table;
* references to the program's manual for every keyword it emits;
* tests in `tests/test_input_writers.py`.

The existing writers and their recorded references show the expected shape;
see [Recorded syntax references](input_writers.md#recorded-syntax-references-and-acceptance)
and [Contributing](contributing.md). Until a writer exists, the
[SDK](input_writers.md#sdk) and an exported structure cover scripted use.

Offering **Submit serial job to local vq** for a new program also needs its
argument list in the viewer. A registered binary alone isn't enough, because
the viewer never guesses a command line.
