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
  inside the viewer.
* **`vibe-view from-vq JOB...`** — fetch one or more finished jobs' QVF output
  to a local directory.
* **`vibe-view vq-features`** — report which visualizations a producer has
  to write what for. This one is a static table and needs no queue.

What the panel shows, how submission and QVF containers work, and how live
reload follows a running job are on [Jobs and live results](jobs.md). This
page is about the extra itself.

## Installing it

```sh
# 1. Install vibe-queue from its own checkout, into the same environment.
pip install /path/to/vibe-queue

# 2. Now the extra resolves against what is already there.
pip install 'vibeview[queue]'
```

The order matters, and so does the fact that step 1 is a path. Two things
about this extra are unusual, and both are deliberate.

### It is not in `[all]`

`pip install 'vibeview[all]'` installs viewer, tui, ase, smiles and jupyter —
but **not** `queue`.

The extra requires the distribution `vq`, which is what vibe-queue's own
`pyproject.toml` names it. `vq` is published on **no package index**. If it
were listed in `all`, then `pip install vibeview[all]` would fail to resolve
for every user, whether or not they wanted the queue. So it stays out until
vibe-queue publishes.

:::{note}
The requirement names the **distribution**, `vq` — not `vibe-queue`, which is
the pre-split monorepo *directory* name and is a package that exists nowhere.
It read `vibe-queue` until 2026-09-08, which is why
`pip install vibeview[queue]` used to fail even with a real vibe-queue
checkout already installed. If vibe-queue ever publishes under a different
name, it renames there first and this pin follows; the two have to agree.
:::

### It cannot resolve at all on Python 3.11

vibe-view supports Python **3.11** and newer. `vq` requires Python **3.12**
and newer. On a 3.11 interpreter the extra therefore cannot be satisfied by
anything, which is why the resolver error you get on 3.11 reads differently
from the one on 3.12.

That is vq's floor to relax, not vibe-view's to work around. If you need the
queue integration, run the viewer on Python 3.12 or newer.

## Without the extra

Every one of the seven places vibe-view reaches for `vq` is a lazy import
inside a try/except, and every one of them has a remediation path. You get a
message naming the extra and the install command — not a traceback, not a
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

Two tests keep this honest: one asserts that all seven `from vq...` sites are
inside a guard that catches `ImportError`, and one that every such site
reaches a `queue_missing_message()`. An unguarded eighth site would take the
whole viewer down when the extra is absent, so it fails the suite instead.
