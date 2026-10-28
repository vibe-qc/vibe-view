"""Shared native-writer panel, downloads and explicitly registered vq invocations."""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from vibeview.input_writers import InputBundle, InputSettings, write_input
from vibeview.orca_input import _integer


def prepare(viewer: Any) -> InputBundle:
    state = viewer.server.state
    potentials = json.loads(state.calc_writer_potentials or "{}")
    if not isinstance(potentials, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in potentials.items()
    ):
        raise ValueError("CP2K potentials must be a JSON object of element: potential strings")
    return write_input(
        viewer.reader.read_structure(),
        InputSettings(
            program=state.calc_engine,
            method=state.calc_writer_method.strip(),
            basis=state.calc_writer_basis.strip(),
            charge=_integer(state.calc_charge, "Charge"),
            multiplicity=_integer(state.calc_multiplicity, "Multiplicity", minimum=1),
            task=state.calc_writer_task,
            frozen=tuple(state.frozen_atoms),
            raw_keywords=state.calc_writer_raw,
            states=_integer(state.calc_writer_states, "States", minimum=1),
            potentials=potentials,
            cutoff=float(state.calc_writer_cutoff),
            xtb_orbitals=getattr(state, "calc_xtb_orbitals", True),
            xtb_bond_orders=getattr(state, "calc_xtb_bond_orders", True),
            xtb_density=getattr(state, "calc_xtb_density", False),
        ),
    )


def preview(viewer: Any) -> InputBundle | None:
    state = viewer.server.state
    state.calc_writer_preview = ""
    try:
        bundle = prepare(viewer)
        state.calc_writer_preview = "\n\n".join(
            f"--- {name} ---\n{text}" for name, text in bundle.files.items()
        )
        state.status_message = f"{bundle.program} input prepared; review before running"
        return bundle
    except (ValueError, TypeError, OverflowError) as exc:
        state.status_message = f"Cannot prepare input: {exc}"
        return None


def save(viewer: Any) -> None:
    bundle = preview(viewer)
    if bundle is not None:
        viewer.exports._offer_download(
            bundle.archive(),
            "application/zip",
            "calculation-input.zip",
            label=f"{bundle.program} input bundle",
            fmt="ZIP",
        )


def submit(viewer: Any) -> None:
    """Only known argv contracts with an explicitly registered local binary.

    Gaussian stdin and GAMESS version-specific launchers need separate adapters;
    POSCAR alone is not a runnable VASP input. Those are always save-only.
    """
    bundle = prepare(viewer)
    names = {"Psi4": "psi4", "NWChem": "nwchem", "Q-Chem": "qchem", "CP2K": "cp2k", "xtb": "xtb"}
    if bundle.program not in names:
        raise ValueError(f"{bundle.program} is save-only; no supported vq launch adapter")
    from vq.config import load_config
    from vq.submit import submit_local

    name = names[bundle.program]
    binary = getattr(load_config().programs.get(name), "binary", None)
    if not binary or not Path(binary).is_absolute() or not os.access(binary, os.X_OK):
        raise ValueError(f"Register an executable local '{name}' program in vq, or save the bundle")
    arguments = {
        "Psi4": [bundle.primary, "calculation.out"],
        "NWChem": [bundle.primary],
        "Q-Chem": [bundle.primary, "calculation.out"],
        "CP2K": ["-i", bundle.primary, "-o", "calculation.out"],
        "xtb": list(bundle.command[1:]),
    }[bundle.program]
    command = [binary, *arguments]
    if bundle.program == "xtb" and "xtb-job.json" in bundle.files:
        try:
            runner = importlib.import_module("qvf.adapters.xtb_run")
            if not callable(getattr(runner, "run_xtb", None)):
                raise ImportError("xTB runner unavailable")
        except ImportError as exc:
            from vibeview.install_hints import install_hint

            raise ValueError(
                "xTB result export needs qvf-writer with the run-xtb adapter. "
                "Install the current qvf/python source into this viewer environment, "
                f"then enable the extra: {install_hint('xtb')}"
            ) from exc
        command = [sys.executable, "-m", "qvf", "run-xtb", "xtb-job.json", "--binary", binary]
    state = viewer.server.state
    minutes = _integer(state.calc_orca_wall_minutes, "Wall time (minutes)", minimum=1)
    # A serial launch: no untested per-program parallel wrapper/OMP assumption.
    with tempfile.TemporaryDirectory(prefix="vibeview_input_") as directory:
        for filename, text in bundle.files.items():
            (Path(directory) / filename).write_text(text, encoding="utf-8")
        job_id = submit_local(
            host="localhost",
            directory=directory,
            command=command,
            program=name,
            cpus=1,
            mem_mb=2000,
            wall_time_seconds=minutes * 60,
            job_name=f"{name}-calculation",
            tags=["vibe-view", name],
        )
    state.vq_submit_dialog = False
    state.vq_panel_open = True
    state.vq_monitor_active = True
    viewer.queue.vq_refresh_jobs()
    state.status_message = f"Submitted {bundle.program} to local vq ({job_id[:12]})"


def build_controls(v: Any, html: Any, ctrl: Any) -> None:
    condition = "!['vibe-qc', 'ORCA'].includes(calc_engine)"
    with html.Div(v_if=(condition,)):
        html.Div(
            "Blank method/basis uses program defaults. Freeze atoms in Edit mode before export. "
            "All bundles can be saved; local submission requires a registered vq program.",
            classes="text-caption",
        )
        v.VTextField(
            v_model=("calc_writer_method",),
            label="Program method (blank = default)",
            density="compact",
        )
        v.VTextField(
            v_model=("calc_writer_basis",),
            label="Program basis (blank = default)",
            density="compact",
        )
        v.VSelect(
            v_model=("calc_writer_task",),
            items=("['single_point', 'optimization', 'frequencies', 'tddft']",),
            label="Job type",
            density="compact",
        )
        v.VTextField(
            v_model=("calc_writer_states",),
            label="TD excited states",
            type="number",
            min=1,
            density="compact",
        )
        v.VTextField(
            v_if=("['CP2K','NWChem'].includes(calc_engine)",),
            v_model=("calc_writer_cutoff",),
            label="Plane-wave cutoff (Ry)",
            type="number",
            density="compact",
        )
        v.VTextarea(
            v_if=("calc_engine === 'CP2K'",),
            v_model=("calc_writer_potentials",),
            label='Potentials JSON, e.g. {"H":"GTH-PBE-q1","O":"GTH-PBE-q6"}',
            rows=2,
        )
        v.VTextarea(
            v_model=("calc_writer_raw",),
            label="Raw native keywords / blocks (Gaussian: route keywords)",
            rows=3,
        )
        with html.Div(v_if=("calc_engine === 'xtb'",)):
            for key, label in (("orbitals", "Orbitals"), ("bond_orders", "Bond orders"),
                               ("density", "Electron density")):
                v.VCheckbox(v_model=(f"calc_xtb_{key}",), label=label, density="compact",
                            hide_details=True)
            html.Div("Molecular GFN0/1/2 jobs produce a combined QVF result. "
                     "GFN-FF uses the native-file workflow.", classes="text-caption")
        v.VBtn("Preview input bundle", click=ctrl.preview_native_input, block=True, size="small")
        v.VTextarea(
            v_model=("calc_writer_preview",),
            label="Preview (refresh after edits)",
            readonly=True,
            rows=10,
        )
        v.VBtn("Save input bundle (.zip)", click=ctrl.export_native_input, block=True, size="small")
        v.VTextField(
            v_if=("['Psi4','NWChem','Q-Chem','CP2K','xtb'].includes(calc_engine)",),
            v_model=("calc_orca_wall_minutes",),
            label="Local queue wall time (minutes)",
            type="number",
            min=1,
        )
        v.VBtn(
            "Submit serial job to local vq (1 CPU, 2 GB)",
            click=ctrl.submit_native_input,
            v_if=("['Psi4','NWChem','Q-Chem','CP2K','xtb'].includes(calc_engine)",),
            block=True,
            size="small",
        )


def bind(viewer: Any) -> None:
    viewer.ctrl.set("preview_native_input")(lambda: preview(viewer))
    viewer.ctrl.set("export_native_input")(lambda: save(viewer))

    def submit_safe() -> None:
        try:
            submit(viewer)
        except ImportError:
            from vibeview.install_hints import queue_missing_message

            viewer.server.state.status_message = queue_missing_message("submit native inputs")
        except Exception as exc:
            viewer.server.state.status_message = f"Cannot submit native input: {exc}"

    viewer.ctrl.set("submit_native_input")(submit_safe)
