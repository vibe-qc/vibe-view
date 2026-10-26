# Queue integration — the `[queue]` extra

vibe-view can talk to [vibe-queue](https://github.com/vibe-qc/vibe-queue),
the job scheduler, so you can watch a queue and pull finished results straight
into the viewer without leaving it.

This is entirely optional. Viewing a `.qvf` file needs nothing from the queue,
and the whole integration is behind lazy imports: with the extra absent, the
viewer starts, opens files and renders exactly as it otherwise would.

## What it gives you

* **The vq Job Manager panel** in `vibe-view open` and `vibe-view desktop` — a
  live cockpit listing jobs by state, with a per-job detail view carrying the
  state and a tail of the log, refreshed while you watch.
* **Live checkpoints.** Open a running job's checkpoint QVF and watch the
  calculation progress in the viewer.
* **Submission.** Send a structure or a QVF container to the queue from
  inside the viewer. Native ORCA input submission and completed-result opening
  are covered in the [ORCA tutorial](orca.md#prepare-save-and-submit-an-orca-input).
* **`vibe-view from-vq JOB...`** — fetch local queue workspaces, discover
  their QVF files and launch the browser viewer. This needs `[viewer]` as
  well as `[queue]`; it is not a fetch-only command.
* **`vibe-view vq-features`** — report which visualizations a producer has
  to write what for. This one is a static table and needs no queue.

What the panel shows, how submission and QVF containers work, and how live
reload follows a running job are on [Jobs and live results](jobs.md). This
page is about the extra itself. Setting up vq itself, on a workstation with no batch
system or in front of SLURM and PBS/Torque, is on
[Clusters and remote machines](cluster.md), and registering programs is on
[Supported programs and calculations](calculations.md#register-a-program-with-vq).

## Installing it

Clone the public queue source next to the viewer checkout.

```sh
# From the vibe-view repository root, using Python 3.12 or newer:
source .venv/bin/activate
git clone https://github.com/vibe-qc/vibe-queue.git ../vibe-queue
python -m pip install ../vibe-queue
python -m pip install -e '.[queue]'
```

The `.[queue]` path refers to this viewer checkout, so it does not ask a
package index for the unpublished viewer.

Install the queue from its local path before adding the viewer extra. Reuse
an existing queue checkout if you already have one. The import must work in
the environment that starts the viewer: a `vq` executable on `PATH` from a
different virtualenv is not sufficient.

Check the two installations separately:

```sh
python -c "import sys, vq; print(sys.executable); print(vq.__file__)"
vibe-view doctor
vq daemon status
vq programs localhost
```

The Python check and `doctor` establish import availability; they do not
start a daemon or prove that a calculation engine is healthy. Run daemon
and program checks with the intended queue configuration. A self-contained
desktop package uses its embedded interpreter, so installing vq into an
unrelated shell environment does not add it to that package.

## Which queue and files are used?

The Job Manager, viewer submission buttons and `from-vq` use the local queue
on the machine running the viewer server. Setting a remote default host in
vq does not turn these controls into a remote-host picker. For a scheduler
or another machine, use `vq submit HOST` and `vq fetch HOST JOBID`, then open
the fetched result; see [cluster workflows](cluster.md#what-the-viewer-can-and-cannot-do-with-a-cluster).

| Route | Results it opens automatically |
|---|---|
| Job Manager **open** | QVF archives and recognized ORCA datasets in the fetched workspace |
| `vibe-view from-vq JOB...` | `.qvf` archives only; no loose-file conversion |
| `vq fetch HOST JOBID -o results/` | Fetches files; choose a result and open it yourself |

Fetch destinations are on the server's machine. Relative directories are
resolved against the viewer's launch directory, not the browser's Downloads
folder. vq may include the job name in the destination directory; use the
path printed by the fetch rather than assuming it is `results/JOBID`.

A fetch can succeed without producing a view: the job may have written no
QVF, the ORCA utility may be unavailable, or a result may be unreadable.
Inspect the fetched directory and the reported error before resubmitting.
Current vq refreshes a recognized same-job destination; an unrelated existing
directory is refused. If the viewer reports a destination collision, select
a fresh output directory to preserve the existing files.

## It is not in `[all]`

`python -m pip install -e '.[all]'` from the viewer checkout installs
the general viewing and format extras, but **not** `queue`. The full list
is in [Installation](installation.md#optional-extras).

The extra requires the distribution `vibe-queue` (version 0.26.10 or newer),
which is what vibe-queue's own `pyproject.toml` names it. It is published on
**no package index**. If it were listed in `all`, then
`pip install vibeview[all]` would fail to resolve for every user, whether or
not they wanted the queue. So it stays out until vibe-queue publishes.

:::{note}
The requirement names the **distribution**, `vibe-queue`. Through 0.26.9
vibe-queue shipped as the distribution `vq`, and the extra required `vq`; a
current vibe-queue install does not satisfy that name. The import package and
the `vq` command are still `vq`. If vibe-queue ever publishes under a
different name, it renames there first and this pin follows; the two have to
agree.
:::

## Without the extra

Queue entry points import `vq` lazily and report missing dependencies with
an installation hint. You get a message naming the extra and the install command — not a traceback, not a
blank panel:

```
vq (vibe-queue) is not installed — cannot fetch jobs. Install vibe-queue
from its checkout (pip install <path-to-vibe-queue>), then:
/path/to/python -m pip install -e '/path/to/vibe-view[queue]'
```

`vibe-view from-vq` prints that to stderr and exits `1`. The Job Manager and
the queue-overview strip put it in their status line. That last one is worth a
note: the overview strip used to swallow the `ImportError` and simply go
blank, which is indistinguishable from *the daemon is down* — a missing
package and a dead daemon are different problems and now say so.

`vibe-view doctor` reports `queue` as a capability row like every other
extra, with a note explaining that the extra alone is not actionable for a
dependency that is on no index.

Missing-package handling is covered by the queue integration tests. Treat
an import error, a daemon connection error and a rejected program registration
as separate failures: installing the extra only addresses the first.
