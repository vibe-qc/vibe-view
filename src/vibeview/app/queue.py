"""Queue controllers for one browser viewer instance."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vibeview.app import helpers as _helpers
from vibeview.qvf import QVFError

if TYPE_CHECKING:
    from vibeview.app.runtime import ViewerRuntime

_VQ_TERMINAL = {"completed", "failed", "killed", "interrupted"}
_VQ_STATE_COLOR = {
    "pending": "grey",
    "running": "info",
    "suspended": "warning",
    "completed": "success",
    "failed": "error",
    "killed": "error",
    "interrupted": "error",
}


class QueueControllers:
    def __init__(self, viewer: ViewerRuntime) -> None:
        self.viewer = viewer

    def _vq_elapsed(self, spec: Any) -> str:
        """Human elapsed time for a job: running → since start; terminal →
        start→finish; else blank. Robust to missing/one-off timestamps."""
        from datetime import datetime

        def _parse(ts: Any) -> datetime | None:
            if not ts:
                return None
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                return None

        start = _parse(getattr(spec, "started_at", None))
        if start is None:
            return ""
        end = _parse(getattr(spec, "finished_at", None))
        if end is None:
            if spec.state.value != "running":
                return ""
            end = datetime.now(UTC)
        secs = max(0, int((end - start).total_seconds()))
        if secs < 60:
            return f"{secs}s"
        if secs < 3600:
            return f"{secs // 60}m {secs % 60}s"
        return f"{secs // 3600}h {(secs % 3600) // 60}m"

    def _vq_job_dict(self, spec: Any) -> dict[str, Any]:
        """Rich per-job row for the Job Manager UI."""
        st = spec.state.value
        name = spec.job_name or spec.id[:12]
        return {
            "id": spec.id,
            "name": name,
            "label": f"{name}  ({spec.id[:12]})",
            "state": st,
            "color": _VQ_STATE_COLOR.get(st, "grey"),
            "elapsed": self.viewer.queue._vq_elapsed(spec),
            "tags": list(getattr(spec, "tags", []) or []),
            "terminal": st in _VQ_TERMINAL,
            "openable": st == "completed",
            # A running job may expose a live checkpoint QVF to watch (M4);
            # vq_watch_live checks the daemon's status JSON on click.
            "watchable": st == "running",
            "submitted_at": getattr(spec, "submitted_at", "") or "",
        }

    def _vq_collect_jobs(self, all_states: bool) -> tuple[list[dict[str, Any]], str]:
        """Return (items, error). ``items`` is a list of job dicts newest-first;
        ``error`` is a user-facing string or None. Never raises."""
        try:
            from vq.listing import list_jobs as _list_jobs

            # "localhost" is a recognized loopback alias; the shipped code
            # passed "local", which vq.host.is_local_host rejects →
            # NotImplementedError, so job listing never actually worked.
            specs = _list_jobs("localhost")
        except ImportError:
            from vibeview.install_hints import queue_missing_message

            return [], queue_missing_message("list jobs")
        except Exception as e:  # noqa: BLE001 — surface any queue error to the UI
            return [], f"Error listing jobs: {e}"

        if not all_states:
            specs = [s for s in specs if s.state.value == "completed"]
        # Newest activity first: finished, else started, else submitted.
        specs.sort(
            key=lambda s: (
                getattr(s, "finished_at", None)
                or getattr(s, "started_at", None)
                or getattr(s, "submitted_at", "")
                or ""
            ),
            reverse=True,
        )
        return [self.viewer.queue._vq_job_dict(s) for s in specs[:100]], ""

    def list_vq_jobs(self) -> None:
        """Open the Job Manager panel and refresh it."""
        self.viewer.server.state.vq_panel_open = True
        self.viewer.queue.vq_refresh_jobs()

    def open_vq_job(self, job_id: str) -> None:
        """Fetch a vq job and open its .qvf files in the viewer."""

        if not job_id:
            return
        try:
            from vq.fetch import fetch_local
        except ImportError:
            from vibeview.install_hints import queue_missing_message

            self.viewer.server.state.status_message = queue_missing_message("fetch jobs")
            return

        output_dir = Path(self.viewer.server.state.vq_fetch_output_dir or "vq-fetched")
        if not output_dir.is_absolute():
            output_dir = Path.cwd() / output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            dst = fetch_local(job_id, output_dir)
        except FileNotFoundError:
            self.viewer.server.state.status_message = f"Job {job_id[:12]} not found in local queue"
            return
        except FileExistsError:
            self.viewer.server.state.status_message = (
                f"Job {job_id[:12]} already fetched — remove it first or change output dir"
            )
            return
        except Exception as e:
            self.viewer.server.state.status_message = f"Error fetching {job_id[:12]}: {e}"
            return

        # Find .qvf files and open them.
        from vibeview.qvf import QVFReader

        qvf_files = sorted(dst.rglob("*.qvf"))
        if not qvf_files:
            self.viewer.server.state.status_message = (
                f"Fetched {dst.name} but no .qvf files found. Did the job have output_qvf=True?"
            )
            return

        for qvf_path in qvf_files:
            try:
                new_reader = QVFReader(qvf_path)
            except QVFError as e:
                self.viewer.server.state.status_message = f"Error opening {qvf_path.name}: {e}"
                continue
            self.viewer._all_readers.append(new_reader)

        if not self.viewer._all_readers:
            return
        self.viewer.files._activate_file(len(self.viewer._all_readers) - 1)
        self.viewer.server.state.status_message = (
            f"Fetched {dst.name} — {len(qvf_files)} .qvf file(s) loaded"
        )

    def vq_watch_live(self, job_id: str) -> None:
        """Open a *running* job's live checkpoint QVF and auto-reload it as
        the job rewrites it (M4 live streaming). The producer must have been
        started with ``checkpoint_qvf=…``; the daemon advertises the path in
        its status JSON (``checkpoint_qvf_path`` / ``_exists``)."""
        import json as _json

        if not job_id:
            return
        state = self.viewer.server.state
        try:
            from vq.status import show_status_json
        except ImportError:
            from vibeview.install_hints import queue_missing_message

            state.status_message = queue_missing_message("open a live checkpoint")
            return
        try:
            info = _json.loads(show_status_json("localhost", job_id, tail=0))
        except Exception as e:  # noqa: BLE001 — surface any queue error
            state.status_message = f"Error querying {job_id[:12]}: {e}"
            return
        ckpt = info.get("checkpoint_qvf_path")
        if not ckpt or not Path(ckpt).exists():
            state.status_message = (
                f"Job {job_id[:12]} has written no live checkpoint yet — "
                "submit with checkpoint_qvf=… (or wait for its first snapshot)"
            )
            return

        from vibeview.qvf import QVFReader

        # Already open? Switch to it instead of opening a duplicate.
        for i, r in enumerate(self.viewer._all_readers):
            if r.path is not None and str(r.path) == str(ckpt):
                state.active_file_idx = i
                self.viewer.files._reload_active_file()
                break
        else:
            try:
                new_reader = QVFReader(ckpt)
            except QVFError as e:
                state.status_message = f"Error opening checkpoint: {e}"
                return
            self.viewer.files._adopt_reader(new_reader)
        self.viewer.files.toggle_file_watcher(True)
        if state.file_watcher_enabled:
            state.status_message = f"Watching {info.get('job_name') or job_id[:12]} live"

    def vq_submit_job(self) -> None:
        """Submit the current structure as a vq job."""
        import subprocess
        import sys
        import tempfile

        state = self.viewer.server.state
        state.vq_submitting = True
        state.status_message = "Submitting to vq..."
        input_path = ""

        try:
            # Read structure data from the active reader
            sdata = self.viewer.reader.read_structure()

            from vibeview.input_generator import (
                _structure_calculation_params,
                generate_input_script,
            )

            params = {
                **_structure_calculation_params(sdata),
                "basis": state.calc_basis or "sto-3g",
                "method": state.calc_method or "rhf",
                "functional": state.calc_functional or "",
                "charge": int(state.calc_charge or 0),
                "multiplicity": int(state.calc_multiplicity or 1),
                "title": "vibe-view submission",
                # Live checkpoint QVF → the Job Manager's "Watch live" (M4).
                "live_checkpoint": bool(state.vq_submit_live_checkpoint),
            }
            template = state.calc_template or "single_point"

            # Human-readable job name: <stem>-<method>, sanitized to vq's
            # charset (alnum + - _ ., <=50 chars). Derived before the
            # payload because container mode names the archive after it.
            import re as _re

            stem = Path(self.viewer.reader.path).stem if self.viewer.reader.path else "structure"
            raw_name = f"vibeview-{stem}-{params['method']}"
            job_name = _re.sub(r"[^A-Za-z0-9._-]", "-", raw_name)[:50]

            # Container mode: the payload is one pending .qvf carrying the
            # structure + job.spec. vq recognizes the suffix and runs
            # `vibeqc run job.qvf`, which settles that same file in place --
            # so one file goes out, the settled one comes back, and it is
            # directly openable here. Falls back to the generated script
            # when vibeqc is not importable.
            container_mode = bool(state.vq_submit_container and _helpers._vibeqc_importable())
            if container_mode:
                container_dir = tempfile.mkdtemp(prefix="vq_container_")
                input_path = str(
                    _helpers.build_pending_container(
                        sdata, params, str(Path(container_dir) / job_name)
                    )
                )
            else:
                if state.vq_submit_container:
                    state.status_message = (
                        "vibeqc not importable - submitting a generated "
                        "script instead of a QVF container."
                    )
                script = generate_input_script(template, params)

                # Write to temp file
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".py", prefix="vq_input_", delete=False
                ) as f:
                    f.write(script)
                    input_path = f.name

            # vq-first (decision 1): submit through the queue via the
            # programmatic API so we can attach a job name + tag and get the
            # jobid back. Submitting via `vq` respects the shared-host
            # protocol — vibe-view never writes to the managed checkouts.
            try:
                from vq.submit import submit_local

                # A .qvf payload resolves its own runtime from the
                # program pin (vq builds `vibeqc run job.qvf`), so passing
                # an interpreter is rejected for container jobs.
                extra = {} if container_mode else {"python": sys.executable}
                job_id = submit_local(
                    "localhost",
                    input_file=input_path,
                    job_name=job_name,
                    tags=(["vibe-view", "qvf-container"] if container_mode else ["vibe-view"]),
                    **extra,
                )
                _kind = "container" if container_mode else "script"
                state.status_message = f"Submitted to vq ({_kind}): {job_name} ({job_id[:12]})"
                state.vq_submit_dialog = False
                # Surface it immediately in the Job Manager, monitoring live.
                state.vq_panel_open = True
                state.vq_monitor_active = True
                self.viewer.queue.vq_refresh_jobs()
            except ImportError:
                # Fallback (decision 1): no queue here, but if vibe-qc is
                # importable run the job locally in the launch directory.
                import importlib.util

                if importlib.util.find_spec("vibeqc") is None:
                    from vibeview.install_hints import queue_missing_message

                    state.status_message = (
                        "Neither vq nor vibe-qc is installed — cannot run the "
                        f"job. {queue_missing_message()}"
                    )
                else:
                    run_dir = Path.cwd()
                    # A container settles in place, so run it where it sits;
                    # a script is executed in the launch directory.
                    if container_mode:
                        argv = [
                            sys.executable,
                            "-m",
                            "vibeqc._cli",
                            "run",
                            input_path,
                        ]
                        cwd = str(Path(input_path).parent)
                        where = f"the container settles at {input_path}"
                    else:
                        argv = [sys.executable, input_path]
                        cwd = str(run_dir)
                        where = f"output .qvf will appear in {run_dir}"
                    subprocess.Popen(
                        argv,
                        cwd=cwd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    input_path = ""  # keep the payload; the run needs it
                    state.status_message = f"vq not found — running locally with vibe-qc; {where}."
                    state.vq_submit_dialog = False
        except Exception as e:  # noqa: BLE001 — surface any submit error
            state.status_message = f"Submit error: {e}"
        finally:
            state.vq_submitting = False
            if input_path:
                with contextlib.suppress(Exception):
                    Path(input_path).unlink(missing_ok=True)

    def _vq_load_overview(self) -> None:
        """Populate the queue-overview strip (host / daemon / capacity /
        load) via vq.overview — the A5 'what can the queue do' view. Never
        raises; clears the fields on any error."""
        state = self.viewer.server.state
        try:
            from vq import config as _vqcfg
            from vq.overview import format_overview_json, gather_overview_local

            cfg = _vqcfg.load() if hasattr(_vqcfg, "load") else _vqcfg.Config()
            d = format_overview_json(gather_overview_local("localhost", cfg))
            ver = d.get("vq_version") or "?"
            state.vq_ov_host = f"{d.get('host', 'localhost')} · vq {ver}"
            health = d.get("daemon_health") or {}
            state.vq_ov_daemon = bool(health.get("ok"))
            cap = []
            if d.get("max_cpus"):
                cap.append(f"max {d['max_cpus']} CPU")
            if d.get("max_jobs"):
                cap.append(f"{d['max_jobs']} jobs")
            state.vq_ov_capacity = " · ".join(cap)
            state.vq_ov_load = (
                f"running {d.get('running_cpus', 0)} · pending {d.get('pending_cpus', 0)} CPU"
            )
        except ImportError:
            # A blank strip cannot be told apart from "the daemon is down",
            # so name the actual cause here; every other queue surface does.
            from vibeview.install_hints import queue_missing_message

            state.vq_ov_host = queue_missing_message()
            state.vq_ov_daemon = False
            state.vq_ov_capacity = ""
            state.vq_ov_load = ""
        except Exception:  # noqa: BLE001 — overview is best-effort
            state.vq_ov_host = ""
            state.vq_ov_daemon = False
            state.vq_ov_capacity = ""
            state.vq_ov_load = ""

    def vq_refresh_jobs(self) -> None:
        """Refresh the Job Manager list. Shows all states when monitoring is
        on (so running/pending jobs appear), completed-only otherwise."""
        state = self.viewer.server.state
        state.vq_jobs_loading = True
        items, error = self.viewer.queue._vq_collect_jobs(all_states=bool(state.vq_monitor_active))
        state.vq_jobs_error = error or ""
        state.vq_jobs_list = items
        self.viewer.queue._vq_load_overview()
        state.vq_jobs_loading = False
        # If a job's detail is open, refresh it too so a running job's
        # state/log tail advance live alongside the list.
        if state.vq_detail_job_id:
            self.viewer.queue._vq_load_job_detail(state.vq_detail_job_id, state.vq_detail_name)

    def _vq_load_job_detail(self, job_id: str, name: str) -> None:
        """Populate the detail pane with a job's state + stdout/stderr tail
        via vq.status.show_status_json (never raises)."""
        state = self.viewer.server.state
        state.vq_detail_job_id = job_id
        state.vq_detail_name = name
        try:
            import json as _json

            from vq.status import show_status_json

            raw = show_status_json("localhost", job_id, tail=40)
            data = _json.loads(raw)
            state.vq_detail_state = str(data.get("state", "?"))
            out = (data.get("stdout") or "").strip()
            err = (data.get("stderr") or "").strip()
            parts = []
            if out:
                parts.append(out)
            if err:
                parts.append("stderr:\n" + err)
            state.vq_detail_log = "\n\n".join(parts) or "(no output yet)"
        except ImportError:
            from vibeview.install_hints import queue_missing_message

            state.vq_detail_log = queue_missing_message("read job status")
        except FileNotFoundError:
            state.vq_detail_log = f"Job {job_id[:12]} not found in the queue."
        except Exception as e:  # noqa: BLE001 — surface any status error
            state.vq_detail_log = f"Error reading status: {e}"

    def vq_show_job_status(self, job_id: str, name: str = "") -> None:
        """Expand a job's live status + log tail in the panel."""
        state = self.viewer.server.state
        # Toggle off if the same job is clicked again.
        if state.vq_detail_job_id == job_id:
            state.vq_detail_job_id = ""
            state.vq_detail_log = ""
            state.vq_detail_state = ""
            return
        state.vq_detail_loading = True
        self.viewer.queue._vq_load_job_detail(job_id, name or job_id[:12])
        state.vq_detail_loading = False

    # Watches the drawer too: the poll stops when the drawer closes, and
    # reopening it with the switch still on must restart it (#37-14).
    def _on_vq_monitor_toggle(self, **kwargs: Any) -> None:
        want = bool(self.viewer.server.state.vq_monitor_active) and bool(
            self.viewer.server.state.vq_panel_open
        )
        if want:
            if self.viewer._vq_monitor_task is not None and not self.viewer._vq_monitor_task.done():
                return

            async def _poll() -> None:
                # Refresh once now, so switching the monitor on shows the
                # current jobs without a 5 s wait, then poll until the switch
                # is turned off or the panel closes. (The switch's old
                # `change=` handler never fired: Vuetify 3 has no such event.)
                self.viewer.queue.vq_refresh_jobs()
                self.viewer.server.state.flush()
                while (
                    self.viewer.server.state.vq_monitor_active
                    and self.viewer.server.state.vq_panel_open
                ):
                    await asyncio.sleep(5)
                    if not (
                        self.viewer.server.state.vq_monitor_active
                        and self.viewer.server.state.vq_panel_open
                    ):
                        break
                    self.viewer.queue.vq_refresh_jobs()
                    self.viewer.server.state.flush()

            self.viewer._vq_monitor_task = _helpers._spawn_task(_poll())
        elif self.viewer._vq_monitor_task is not None:
            self.viewer._vq_monitor_task.cancel()
            self.viewer._vq_monitor_task = None
