# Clusters and remote machines

This chapter sets up vibe-view and the [vibe-queue](https://github.com/vibe-qc/vibe-queue)
job queue (`vq`) on the machines you compute on. It covers three kinds of
machine:

* a workstation or server with **no batch system**, where vq runs jobs itself;
* a cluster with **SLURM**;
* a cluster with **PBS/Torque**.

Viewing never needs a queue. Install the viewer wherever the results are, and
add vq only where you want to submit calculations.

:::{note}
Everything on this page installs into your home directory. No step needs
root, and none of the tools opens a network port: vq reaches other machines
over SSH, and the viewer listens on loopback unless you say otherwise.
:::

## 1. Install the viewer on a cluster

Login nodes rarely have a display or a GPU, and often an old system Python.
Bring your own Python 3.12 or newer from the module system, `uv` or conda,
then follow the [source installation](installation.md#install-from-a-checkout):

```sh
module load python/3.12          # or: uv python install 3.12
git clone https://github.com/vibe-qc/vibe-view.git ~/vibe-view
cd ~/vibe-view
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[tui]'
.venv/bin/vibe-view doctor
```

Module names differ between sites; use the one that provides Python 3.12 or
newer. Choose the extras by what you will do on that machine:

| On the cluster you want to | Install | Then use |
|---|---|---|
| Look at results in the SSH session | core, or `[tui]` | `vibe-view show FILE`, `vibe-view tui FILE` |
| Make figures and tables in batch jobs | core | `vibe-view capture`, `batch`, `export`, `table` |
| Use the browser viewer through a tunnel | `[viewer]` | `vibe-view open FILE --no-browser` |
| Submit from the viewer and follow jobs | `[viewer,queue]` plus vq | the Job Manager panel |

`vibe-view show` needs no OpenGL and works in any terminal. Offscreen PNG
capture needs a working offscreen renderer: check it with
`vibe-view capture-selftest` before relying on it in a batch job, and see
[setting up a headless host](headless.md#setting-up-a-headless-host) if it
fails.

To use the full browser viewer on the cluster, keep it bound to loopback and
forward the port, as described in [Work remotely](workflows.md#work-remotely):

```sh
# on your laptop
ssh -L 8090:127.0.0.1:8090 cluster-login
# in that session, on the cluster
~/vibe-view/.venv/bin/vibe-view open results/calculation.qvf --no-browser --port 8090
```

Then open `http://127.0.0.1:8090` locally. Never use `--host 0.0.0.0` on a
shared login node: the viewer has no authentication.

Copying a QVF to your laptop and opening it there is just as good. An archive
is one self-contained file.

## 2. Install vq

vq is installed the same way on every machine that takes part. The same
checkout serves as client, daemon host or scheduler host:

```sh
git clone https://github.com/vibe-qc/vibe-queue.git ~/vibe-queue
cd ~/vibe-queue && ./scripts/install.sh      # creates ~/vibe-queue/.venv
~/vibe-queue/.venv/bin/vq --version
```

vq's configuration lives in `~/.config/vq/config.toml` (or `$VQ_CONFIG_DIR`),
its state in `~/.local/share/vq` and its scratch in
`~/.local/share/vq-scratch` unless `scratch_root` says otherwise. Keep host
names, user names and keys in `~/.ssh/config` and refer to them by alias; vq's
configuration then contains no credentials.

To let the viewer talk to vq, install vq into the viewer's environment as
well, as described in [Queue integration](queue.md#installing-it):

```sh
cd ~/vibe-view
.venv/bin/python -m pip install ~/vibe-queue
.venv/bin/python -m pip install -e '.[queue]'
```

vq has three roles, and one machine can hold several of them:

| Role | What runs there |
|---|---|
| **Client** | Only the `vq` command, for example on your laptop. |
| **Daemon host** | The always-on vq daemon, which owns the job database and runs or drives jobs. |
| **Scheduler host** | A cluster login node with vq installed but no daemon. A daemon host drives it over SSH and submits through `sbatch` or `qsub`. |

The vibe-queue documentation (`docs/cluster_install.md`, `docs/backends.md`
and `docs/user/programs.md` in its checkout) is the reference for every key
used below.

## 3. A machine with no queue

On a workstation or a group server without a batch system, vq is the queue:
its daemon starts each job itself, within the limits you give it. This is
vq's default, `scheduler = "local"`.

Set the limits in that machine's `~/.config/vq/config.toml`:

```toml
[daemon]
max_cpus = 16          # CPU slots the daemon may hand out
max_jobs = 4           # jobs running at once
max_mem_mb = 64000
default_job_mem_mb = 4000
```

Install the daemon as a user service and check it:

```sh
loginctl enable-linger "$USER"      # Linux: keep user services after logout
vq daemon install                   # systemd user unit; launchd on macOS
vq daemon status
vq programs localhost
```

This is the setup the viewer's submit controls use directly. **Submit to vq
cluster**, **Submit ORCA to vq** and **Submit serial job to local vq** all
submit to the vq daemon on the machine where the viewer runs, and the Job
Manager lists that daemon's jobs. See [Jobs and live results](jobs.md).

If the viewer runs on your laptop and the daemon on a workstation, either run
the viewer on the workstation and tunnel the browser to it (section 1), or
use the `vq` client on the laptop:

```toml
# laptop: ~/.config/vq/config.toml
default_host = "workstation"

[hosts.workstation]
ssh = "workstation"                                  # an ~/.ssh/config alias
remote_vq = "/home/USER/vibe-queue/.venv/bin/vq"     # absolute: SSH loads no profile
```

```sh
vq doctor workstation
```

## 4. A SLURM or PBS/Torque cluster

A cluster job goes through the batch system. A vq daemon on a machine that
stays up (your workstation or a group server, the *driver*) stages each job
into the cluster's scratch space over SSH, writes the batch script and submits
it with `sbatch` or `qsub`. It then follows the job through to completion. The
cluster login node needs vq installed but runs no daemon.

First find out what the cluster runs. This command is read-only:

```sh
vq scheduler-probe cluster-login
```

Then describe the cluster as a host in the driver's configuration, and in
the configuration of every client that submits to it.

::::{tab-set}

:::{tab-item} SLURM

```toml
[hosts.slurm]
ssh = "slurm-login"                                  # ~/.ssh/config alias
remote_vq = "/home/USER/vibe-queue/.venv/bin/vq"
scheduler = "slurm"
scheduler_dialect = "slurm"
scheduler_driver = "workstation"                     # the host whose daemon drives it
scratch_root = "/work/USER/vq"
node_scratch_dir = "/tmp/$USER"
submit_extra = ["--account", "my-group", "--partition", "standard"]
scheduler_max_wall_time_seconds = 172800
```

:::

:::{tab-item} PBS/Torque

```toml
[hosts.pbs]
ssh = "pbs-login"
remote_vq = "/home/USER/vibe-queue/.venv/bin/vq"
scheduler = "pbs"
scheduler_dialect = "torque"
scheduler_driver = "workstation"
scratch_root = "/work/USER/vq"
scheduler_mem_directive = "omit"                     # if the site rejects -l mem=
submit_extra = ["-q", "batch"]
```

:::

::::

Account, partition and queue names are site-specific, and all of them go
through `submit_extra`. Module loads and environment setup that every job
needs go in `scheduler_prologue`, and the matching cleanup in
`scheduler_epilogue`.

:::{warning}
vq implements the **SLURM** and **Torque** dialects. **PBS Pro and SGE are
not implemented yet:** vq rejects `scheduler_dialect = "pbspro"` or `"sge"`
with *unsupported scheduler_dialect*. `vq scheduler-probe` tells you which
one a PBS site runs.
:::

Resources are requested per job, and vq translates them for the batch system:

| `vq submit` option | No queue (local) | PBS/Torque | SLURM |
|---|---|---|---|
| `--cpus N` | N daemon slots | `nodes=1:ppn=N` | `--cpus-per-task=N` |
| `--ntasks R` | — | — | `--ntasks=R` |
| `--mem-mb M` | the daemon's memory budget | `-l mem=` | `--mem` |
| `--time-limit HH:MM:SS` | a vq watchdog | `-l walltime=` | `--time` |
| `--gpus N` | N of the daemon's GPU devices, via `CUDA_VISIBLE_DEVICES` | `nodes=1:ppn=CPUS:gpus=N` | `--gpus=N` |

Programs on the cluster are registered in the **cluster's** vq configuration,
because that is where they run. Programs, and the per-cluster hooks that load
their modules, are covered in
[Supported programs and calculations](calculations.md#register-a-program-with-vq).

### What the viewer can and cannot do with a cluster

The viewer's submit buttons, the Job Manager and `vibe-view from-vq` work
with the vq daemon **on the machine running the viewer**. They do not choose
a remote host. For cluster jobs:

1. **Prepare the input in the viewer.** Use **Save input bundle** for a
   native program, or save the structure. For vibe-qc, export a script or
   write a QVF job container; see
   [vibe-qc jobs for a cluster](calculations.md#vibe-qc-jobs-for-a-cluster).
2. **Submit with `vq`** to the cluster host:

   ```sh
   vq submit slurm -d orca-water/ --program orca --cpus 8 --mem-mb 16000 \
       --time-limit 04:00:00 --tag vibe-view -- /path/to/orca calculation.inp
   vq submit slurm job.qvf --program vibeqc --cpus 16 --time-limit 12:00:00
   ```

   `--program` records which registered program the job uses, and the
   cluster-side hooks for it run around the job, but it does not change the
   command. Give the program's absolute path on the cluster after `--`. A QVF
   job container is the exception: vq runs it with the registered vibe-qc
   Python itself.

3. **Follow it** with `vq queue slurm`, `vq status slurm JOBID` and
   `vq logs slurm JOBID -f`.
4. **Fetch and open** the results:

   ```sh
   vq fetch slurm JOBID -o results/
   vibe-view open results/JOBID/
   ```

   `vibe-view open` takes the fetched files directly: QVF archives, ORCA
   calculation datasets and the other [supported formats](formats.md).

When the cluster is also the daemon host's own machine, which is common for a
group server with SLURM, the viewer's panel shows those jobs as usual.

## 5. Watch a running job

A calculation that writes a streaming checkpoint (vibe-qc does with
`checkpoint_qvf=...`) can be watched while it runs:

* **Through the panel.** On a daemon the viewer can see, **Watch live** on a
  running job opens its checkpoint with auto-reload on.
* **On a shared filesystem.** Open the checkpoint file directly, on the
  cluster through a tunnel or wherever the filesystem is mounted, and switch
  on **Auto-reload on file change** in the Display card. The watcher waits for
  each rewrite to settle before reloading, so a half-written archive is never
  shown. See [Live reload](jobs.md#live-reload).
* **In a terminal.** `vibe-view show checkpoint.qvf --info` prints the run
  status and the latest checkpoint without a display.

vq writes a job's checkpoint as `checkpoint.qvf` in the job's working
directory.

## 6. Figures as batch jobs

The headless commands need no display, so they run as ordinary jobs.
Register the viewer as a program on the host where the jobs run:

```toml
[programs.vibeview]
kind = "venv"
python = "/home/USER/vibe-view/.venv/bin/python"
import_check = "vibeview"
healthcheck_command = "xvfb-run -a vibe-view capture-selftest"
```

Then submit a capture like any other job:

```sh
vq submit workstation --program vibeview -d ./results/ -- \
    sh -c 'xvfb-run -a "$VQ_PROGRAM_BIN/vibe-view" capture water.qvf -o water.png'
```

Leave out `xvfb-run` where the offscreen renderer works without it; see
[Figures without a display](headless.md).

## Troubleshooting

| Symptom | Check |
|---|---|
| The panel says vq is not installed | Install vq into the *viewer's* environment, then `pip install -e '.[queue]'` ([Queue integration](queue.md#without-the-extra)). |
| The overview strip shows a red dot | The daemon is down: `vq daemon status`, `systemctl --user status vq-daemon`. |
| `vq doctor HOST` fails over SSH | Test `ssh ALIAS true` first; use an absolute `remote_vq` path. |
| A program shows `MISSING` or `UNHEALTHY` | `vq programs HOST --all`; fix the path in that host's config, then `vq daemon reload`. |
| `unsupported scheduler_dialect` | The site runs PBS Pro or SGE, which vq does not implement yet. |
| A cluster job never appears in the Job Manager | Expected: the panel lists the local daemon. Use `vq queue CLUSTER`. |
| A batch capture fails | Run `vibe-view capture-selftest` inside a job on the same nodes. |
