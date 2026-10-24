"""Regression tests for the 2026 design refresh (docs/design_refresh_2026.md).

* SCF convergence can drop the initial-guess point (item 4).
* Same-kind spectra from several QVF files overlay with a legend (item 8).
* The Structure Library helper degrades to [] without vibeqc_naming (item 7).
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

_DIALOG_MODELS_AND_LABELS = {
    "palette_open": "Command palette",
    "builder_dialog": "Build molecule",
    "load_file_dialog": "Open chemistry files",
    "vq_submit_dialog": "Submit to vq",
    "build_supercell_dialog": "Build supercell",
    "screenshot_dialog": "Export screenshot",
    "video_export_dialog": "Export video",
    "hq_render_dialog": "High-quality raytrace render",
    "shortcuts_help_dialog": "Keyboard shortcuts",
    "about_dialog": "About vibe-view",
    "settings_dialog": "Settings",
    "download_manager_dialog": "Recent exports",
    "element_picker_open": "Choose element",
}

_SLIDER_MODELS_AND_NAMES = {
    "expmap_opacity": "Experimental map opacity",
    "mb_angle": "Bond rotation angle",
    "vibration_arrow_cutoff": "Vibration arrow cutoff",
    "vibration_arrow_thickness": "Vibration arrow radius",
    "isovalue": "Volume isovalue",
    "opacity": "Volume opacity",
    "vol_window_low": "Volume window low end",
    "vol_window_high": "Volume window high end",
    "vol_ramp_0": "Volume opacity at the window's start",
    "vol_ramp_1": "Volume opacity at 20% of the window",
    "vol_ramp_2": "Volume opacity at 40% of the window",
    "vol_ramp_3": "Volume opacity at 60% of the window",
    "vol_ramp_4": "Volume opacity at 80% of the window",
    "vol_ramp_5": "Volume opacity at the window's end",
    "clip_x": "Clip plane X position",
    "clip_y": "Clip plane Y position",
    "clip_z": "Clip plane Z position",
    "crossfade_blend": "Volume cross-fade blend",
    "trajectory_frame": "Trajectory frame",
    "mo_opacity": "Molecular orbital opacity",
    "wf_elf_iso": "ELF isovalue",
    "vibration_amplitude": "Vibration displacement amplitude",
    "vibration_phase_step": "Vibration phase",
}

_PANEL_SEPARATOR_CONTRACT = {
    "vv-left-panel-separator": {
        "ariaLabel": "Result sections",
        "ariaOrientation": "vertical",
        "ariaControls": "vv-left-panel",
        "ariaValueMin": 180,
        "ariaValueMax": 640,
    },
    "vv-right-panel-separator": {
        "ariaLabel": "Section controls",
        "ariaOrientation": "vertical",
        "ariaControls": "vv-right-panel",
        "ariaValueMin": 180,
        "ariaValueMax": 640,
    },
    "vv-hsplit": {
        "ariaLabel": "Result details",
        "ariaOrientation": "horizontal",
        "ariaControls": "vv-bottom-panel",
        "ariaValueMin": 80,
    },
}

_PLAYBACK_BUTTON_CONTRACT = {
    ("trajectory_step", "[-1]"): (
        "mdi-skip-previous",
        "Previous trajectory frame",
    ),
    ("trajectory_play_toggle", None): (
        ("trajectory_playing ? 'mdi-pause' : 'mdi-play'",),
        (
            "trajectory_playing ? 'Pause trajectory animation' : "
            "'Start trajectory animation'",
        ),
    ),
    ("trajectory_step", "[1]"): (
        "mdi-skip-next",
        "Next trajectory frame",
    ),
    ("step_mo_render", "[-1]"): (
        "mdi-skip-previous",
        "Previous molecular orbital",
    ),
    ("toggle_mo_animation", None): (
        ("wf_animating ? 'mdi-pause' : 'mdi-play'",),
        (
            "wf_animating ? 'Pause molecular orbital animation' : "
            "'Start molecular orbital animation'",
        ),
    ),
    ("step_mo_render", "[1]"): (
        "mdi-skip-next",
        "Next molecular orbital",
    ),
}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _qvf(label: str, freqs, ints, scf_iters=None) -> Path:
    structure = json.dumps(
        {
            "atoms": [{"symbol": "H", "position": [0, 0, 0], "atomic_number": 1}],
            "pbc": [False, False, False],
            "lattice_vectors": None,
        }
    ).encode()
    files = {"s.json": structure}
    secs = [
        {"id": "structure", "kind": "structure", "members": {
            "structure": {"path": "s.json", "format": "json", "sha256": _sha(structure)}}},
    ]
    if freqs is not None:
        sp = json.dumps({"frequencies": freqs, "intensities": ints}).encode()
        files["ir.json"] = sp
        secs.append({"id": "ir0", "kind": "spectra.ir", "members": {
            "spectrum": {"path": "ir.json", "format": "json", "sha256": _sha(sp)}}})
    if scf_iters is not None:
        sc = json.dumps({"iterations": scf_iters}).encode()
        files["scf.json"] = sc
        secs.append({"id": "scf0", "kind": "scf_history", "members": {
            "iterations": {"path": "scf.json", "format": "json", "sha256": _sha(sc)}}})
    man = {"qvf_version": 1,
           "source": {"program": "vibe-qc", "version": "0", "calculation": label},
           "sections": secs}
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)  # noqa: SIM115 — outlives fn
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(man))
        for p, d in files.items():
            zf.writestr(p, d)
    return Path(tmp.name)


def test_browser_landmarks_controls_and_statuses_have_a11y_contract():
    """Trame must explicitly forward ARIA attributes to the served DOM."""
    from tests._served import served_viewer

    served = served_viewer()

    for label in (
        "vq Job Manager",
        "Result sections",
        "Section controls",
        "Close vq Job Manager",
        "Close job details",
    ):
        assert f'aria-label="{label}"' in served

    assert "Interactive 3D molecular structure viewport" in served
    assert "el.setAttribute('aria-label'," in served

    assert served.count('role="status"') == 2
    assert served.count('aria-live="polite"') == 2
    assert served.count('aria-atomic="true"') == 2
    assert "Job status: {{ live_run_status }}" in served










def test_panel_resize_handles_share_a_keyboard_separator_contract():
    """All three resize paths must stay keyboard-operable and self-describing."""
    from tests._served import served_viewer

    source = served_viewer()

    for marker in (
        "attach('vv-left-panel','left','left_panel_width','Result sections',280)",
        "attach('vv-right-panel','right','right_panel_width','Section controls',300)",
        "h.id=id+'-separator'",
        "h.setAttribute('role','separator')",
        "h.setAttribute('aria-orientation','vertical')",
        "h.setAttribute('aria-controls',id)",
        "h.setAttribute('aria-valuemin','180')",
        "h.setAttribute('aria-valuemax','640')",
        "s.setAttribute('role','separator')",
        "s.setAttribute('aria-label','Result details')",
        "s.setAttribute('aria-orientation','horizontal')",
        "s.setAttribute('aria-controls','vv-bottom-panel')",
        "s.setAttribute('aria-valuemin','80')",
        "var step=e.shiftKey?50:10",
        "else if(e.key==='Home')",
        "else if(e.key==='End')",
        "else if(e.key==='Enter')",
        "ResizeObserver",
        "current&&current.splitter.isConnected&&current.panel.isConnected",
        "current={splitter:s,panel:p,refresh:function(){announce(read());}}",
        "window._vvOnDomChange(attachAll);",
        "window._vvOnDomChange(attach);",
        "Math.floor(vh*0.85)",
        "min-height: 80px; height: 35vh;",
        "max-height: 85vh; overflow: auto; resize: vertical;",
    ):
        assert marker in source

    assert source.count("var step=e.shiftKey?50:10") == 2
    assert source.count("new ResizeObserver(function()") == 2
    assert "Math.floor(vh*0.9)" not in source
    assert "min-height: 60px" not in source








def test_generated_vue_template_forwards_every_dialog_name():
    """Trame's runtime template must preserve every model/name mapping."""
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    qvf_path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
    reader = QVFReader(qvf_path)
    try:
        server = create_app(reader)
        template = server.state["trame__template_main"]
    finally:
        reader.close()

    tags = [
        line.strip()
        for line in template.splitlines()
        if line.lstrip().startswith("<VDialog ")
    ]
    assert len(tags) == len(_DIALOG_MODELS_AND_LABELS)
    for model, label in _DIALOG_MODELS_AND_LABELS.items():
        matches = [
            tag
            for tag in tags
            if f'v-model="{model}"' in tag and f'aria-label="{label}"' in tag
        ]
        assert len(matches) == 1, f"missing runtime VDialog mapping for {model!r}"
        tag = matches[0]
        assert (
            '@afterLeave="window._vvRestoreDialogFocus && '
            f"window._vvRestoreDialogFocus('{label}')\"" in tag
        )


def test_generated_vue_template_forwards_every_slider_name():
    """Trame's runtime template must preserve every slider model/name mapping."""
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    qvf_path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
    reader = QVFReader(qvf_path)
    try:
        server = create_app(reader)
        template = server.state["trame__template_main"]
    finally:
        reader.close()

    tags = [
        line.strip()
        for line in template.splitlines()
        if line.lstrip().startswith("<VSlider ")
    ]
    assert len(tags) == len(_SLIDER_MODELS_AND_NAMES)
    for model, name in _SLIDER_MODELS_AND_NAMES.items():
        matches = [
            tag
            for tag in tags
            if f'v-model="{model}"' in tag and f'aria-label="{name}"' in tag
        ]
        assert len(matches) == 1, f"missing runtime VSlider mapping for {model!r}"

    clip_tags = [
        tag
        for tag in tags
        if any(
            f'v-model="{model}"' in tag
            for model in ("clip_x", "clip_y", "clip_z")
        )
    ]
    assert len(clip_tags) == 3
    assert all('@update:modelValue="trigger(' in tag for tag in clip_tags)

    position_tags = [
        line.strip()
        for line in template.splitlines()
        if "clip_position_message" in line and line.lstrip().startswith("<div ")
    ]
    assert len(position_tags) == 1
    assert 'aria-live="off"' in position_tags[0]


def test_generated_vue_template_forwards_every_playback_button_name():
    """Trame must preserve all six static or state-driven playback names."""
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    qvf_path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
    reader = QVFReader(qvf_path)
    try:
        server = create_app(reader)
        template = server.state["trame__template_main"]
    finally:
        reader.close()

    tags = [
        line.strip()
        for line in template.splitlines()
        if line.lstrip().startswith("<VBtn ")
        and any(
            marker in line
            for marker in (
                "mdi-skip-previous",
                "mdi-skip-next",
                "trajectory_playing",
                "wf_animating",
            )
        )
    ]
    assert len(tags) == 6
    matched_tags = set()
    for icon, name in _PLAYBACK_BUTTON_CONTRACT.values():
        icon_value = icon[0] if isinstance(icon, tuple) else icon
        name_value = name[0] if isinstance(name, tuple) else name
        icon_binding = (
            f':icon="{icon_value}"' if isinstance(icon, tuple) else f'icon="{icon_value}"'
        )
        name_binding = (
            f':aria-label="{name_value}"'
            if isinstance(name, tuple)
            else f'aria-label="{name_value}"'
        )
        title_binding = (
            f':title="{name_value}"'
            if isinstance(name, tuple)
            else f'title="{name_value}"'
        )
        matches = [
            tag
            for tag in tags
            if icon_binding in tag and name_binding in tag and title_binding in tag
        ]
        assert len(matches) == 1, f"missing runtime playback mapping for {name_value!r}"
        matched_tags.add(matches[0])
    assert len(matched_tags) == 6


def test_generated_vue_template_names_the_visible_native_colour_input():
    """Trame must preserve the raw colour input's explicit ARIA mapping."""
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    qvf_path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
    reader = QVFReader(qvf_path)
    try:
        server = create_app(reader)
        template = server.state["trame__template_main"]
    finally:
        reader.close()

    tags = [
        line.strip()
        for line in template.splitlines()
        if line.lstrip().startswith("<input ")
    ]
    file_tags = [tag for tag in tags if 'id="qvf-file-input"' in tag]
    colour_tags = [tag for tag in tags if 'v-model="element_color_value"' in tag]
    assert len(file_tags) == len(colour_tags) == 1
    file_tag, colour_tag = file_tags[0], colour_tags[0]
    assert 'style="display: none;"' in file_tag
    assert 'v-model="element_color_value"' in colour_tag
    assert 'title="Element colour override"' in colour_tag
    assert 'aria-label="Element colour override"' in colour_tag


def _serve_dialogs_open(
    qvf_path: str,
    port: int,
    dialog_models: tuple[str, ...],
    edit_mode: bool,
    open_initially: bool,
    presentation_scenario: bool = False,
    slider_scenario: bool = False,
    playback_scenario: str = "",
    focus_scenario: bool = False,
    label_follower_scenario: bool = False,
    silhouette_scenario: bool = False,
) -> None:
    """Child-process entry point for the trusted-browser DOM regression."""
    import os
    import sys

    os.environ["PYVISTA_OFF_SCREEN"] = "true"
    # ``spawn`` restores the parent pytest command line in the child.  Trame
    # parses that global argv even when ``server.start`` receives an explicit
    # port, so pytest's ``-p no:cacheprovider`` otherwise becomes Trame's port.
    del sys.argv[1:]

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(qvf_path)
    server = create_app(reader)
    with server.state:
        server.state.edit_mode = edit_mode
        server.state.selected_section = next(
            section.id for section in reader.sections if section.kind == "structure"
        )
        if presentation_scenario or focus_scenario:
            server.state.user_bookmarks = [
                {
                    "name": "Structure slide one",
                    "camera": None,
                    "section_id": server.state.selected_section,
                    "isovalue": 0.02,
                    "colormap": "viridis",
                    "opacity": 0.4,
                },
                {
                    "name": "Structure slide two",
                    "camera": None,
                    "section_id": server.state.selected_section,
                    "isovalue": 0.07,
                    "colormap": "plasma",
                    "opacity": 0.8,
                },
            ]
            server.state.presentation_slide_duration = 0.2
        if slider_scenario:
            server.controller.activate_section("density")
            server.controller.toggle_clip(True)
            server.controller.toggle_show_slice(True)
        if playback_scenario:
            target_kind = {
                "trajectory": "trajectory",
                "molecular-orbital": "wavefunction.gto",
                "molecular-orbital-progress": "wavefunction.gto",
            }.get(playback_scenario)
            if target_kind is None:
                raise ValueError(f"unknown playback scenario: {playback_scenario!r}")
            section = next(
                section for section in reader.sections if section.kind == target_kind
            )
            if playback_scenario == "molecular-orbital":
                server.state.wf_anim_speed = 60.0
            server.controller.activate_section(section.id)
            if playback_scenario == "molecular-orbital-progress":
                server.state.wf_anim_speed = 0.8
                server.state.wf_selected_mo = server.state.wf_mo_rows[0]["value"]
                server.controller.render_mo(server.state.wf_selected_mo)
        if label_follower_scenario:
            server.controller.toggle_atom_labels(True)
        if silhouette_scenario:
            # An orbital isosurface in the Publication look: white
            # background, outlines on, chrome hidden (#112).
            wavefunction = next(
                section for section in reader.sections if section.kind == "wavefunction.gto"
            )
            server.controller.activate_section(wavefunction.id)
            server.state.wf_selected_mo = server.state.wf_mo_rows[0]["value"]
            server.controller.render_mo(server.state.wf_selected_mo)
            server.controller.apply_view_preset("publication")
        if open_initially:
            for model in dialog_models:
                setattr(server.state, model, True)
    server.start(
        host="127.0.0.1",
        port=port,
        open_browser=False,
        show_connection_info=False,
        timeout=0,
    )


def test_served_dialog_and_presentation_dom_contracts(
    tmp_path: Path, sample_qvf: Path, water_qvf: Path
):
    """A real Chromium DOM must expose all conditional accessibility names."""
    import contextlib
    import json
    import multiprocessing
    import os
    import shutil
    import signal
    import socket
    import subprocess
    import time

    import pytest

    chrome_candidates = [
        shutil.which(name)
        for name in ("google-chrome", "chromium", "chromium-browser", "chrome")
    ]
    chrome_candidates.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    chrome = next(
        (
            path
            for path in chrome_candidates
            if path and Path(path).is_file() and os.access(path, os.X_OK)
        ),
        None,
    )
    node = shutil.which("node")
    if chrome is None or node is None:
        pytest.skip(
            "Chrome/Chromium and Node are required for the served-DOM accessibility lane"
        )
    node_browser_apis = subprocess.run(
        [
            node,
            "-e",
            "process.exit(typeof fetch === 'function' && "
            "typeof WebSocket === 'function' ? 0 : 1)",
        ],
        capture_output=True,
        timeout=5,
        check=False,
    )
    if node_browser_apis.returncode:
        pytest.skip("the served-DOM accessibility lane requires Node with fetch/WebSocket")

    browser_probe = r"""
const { spawn } = require("node:child_process");
const { createHash } = require("node:crypto");
const { writeFileSync } = require("node:fs");
const chrome = process.argv[1];
const url = process.argv[2];
const profile = process.argv[3];
const expectedCount = Number(process.argv[4]);
const editMode = process.argv[5] === "true";
const presentationScenario = process.argv[6] === "true";
const sliderScenario = process.argv[7] === "true";
const playbackScenario = process.argv[8] || "";
const colorInputScenario = process.argv[9] === "true";
const focusScenario = process.argv[10] || "";
const cdpTimeoutScenario = process.argv[11] === "true";
const labelFollowerScenario = process.argv[12] === "true";
const silhouetteScenario = process.argv[13] === "true";
const browserScreenshotScenario = process.argv[14] === "true";
const profileName = profile.split(/[\\/]/).pop();
const child = spawn(chrome, [
  "--headless",
  "--disable-background-networking",
  "--disable-dev-shm-usage",
  "--enable-unsafe-swiftshader",
  "--enable-webgl",
  "--no-first-run",
  "--no-default-browser-check",
  "--remote-debugging-port=0",
  ...(playbackScenario === "molecular-orbital-progress" || labelFollowerScenario ||
    silhouetteScenario || browserScreenshotScenario
    ? ["--window-size=1400,1000"]
    : []),
  `--user-data-dir=${profile}`,
  "about:blank",
], { stdio: ["ignore", "ignore", "pipe"] });

let browserStderr = "";
let started = false;
let finished = false;
let focusStage = "startup";
const CDP_COMMAND_TIMEOUT_MS = 6000;
const CDP_TIMEOUT_REGRESSION_MS = 250;
const CONNECTION_TIMEOUT_MS = 10000;
let commandTimeoutMs = CDP_COMMAND_TIMEOUT_MS;
const probeDiagnostic = {
  profile: profileName,
  action: "Chromium startup",
  method: null,
  lastState: null,
  lastCommand: null,
  pending: [],
  inputResponseTimeouts: [],
  javascriptErrors: [],
};
const hardTimeout = setTimeout(
  () => finish(2, {
    error: "Chromium DOM probe timed out",
    focusStage,
    diagnostic: probeDiagnostic,
    browserStderr,
  }),
  focusScenario || sliderScenario || browserScreenshotScenario ? 90000 : 45000,
);

function finish(code, payload) {
  if (finished) return;
  finished = true;
  clearTimeout(hardTimeout);
  const finalPayload = { ...payload, diagnostic: snapshotDiagnostic() };
  let emitted = false;
  const emitResult = () => {
    if (emitted) return;
    emitted = true;
    const exitFallback = setTimeout(() => process.exit(code), 1000);
    process.stdout.write(JSON.stringify(finalPayload), () => {
      clearTimeout(exitFallback);
      process.exit(code);
    });
  };
  if (child.exitCode !== null || child.signalCode !== null) {
    emitResult();
    return;
  }
  const killTimeout = setTimeout(() => child.kill("SIGKILL"), 3000);
  const emitTimeout = setTimeout(emitResult, 5000);
  child.once("exit", () => {
    clearTimeout(killTimeout);
    clearTimeout(emitTimeout);
    emitResult();
  });
  child.kill("SIGTERM");
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// The keyDown a real keyboard sends: a printable key carries its text, and a
// Ctrl/Alt/Meta chord does not (Puppeteer's rule). Headless Chrome 155 on
// macOS turns the page hidden on a printable keyDown without text, which
// stops requestAnimationFrame and freezes every Vue transition (#83).
function keyDown(params) {
  const printable = params.key.length === 1 && !(params.modifiers & ~8);
  return printable
    ? { type: "keyDown", text: params.key, unmodifiedText: params.key, ...params }
    : { type: "keyDown", ...params };
}

function recordProbeState(action, state) {
  probeDiagnostic.action = action;
  if (state !== undefined && state !== null) probeDiagnostic.lastState = state;
}

function snapshotDiagnostic() {
  const now = Date.now();
  const lastCommand = probeDiagnostic.lastCommand
    ? {
        ...probeDiagnostic.lastCommand,
        elapsedMs: probeDiagnostic.lastCommand.status === "pending"
          ? now - probeDiagnostic.lastCommand.startedAt
          : probeDiagnostic.lastCommand.elapsedMs,
      }
    : null;
  if (lastCommand) delete lastCommand.startedAt;
  return {
    ...probeDiagnostic,
    lastCommand,
    pending: probeDiagnostic.pending.map((entry) => ({ ...entry })),
    inputResponseTimeouts: probeDiagnostic.inputResponseTimeouts.map(
      (entry) => ({ ...entry })
    ),
    javascriptErrors: [...probeDiagnostic.javascriptErrors],
  };
}

function bounded(promise, label, timeoutMs = CONNECTION_TIMEOUT_MS) {
  recordProbeState(label);
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(
      () => reject(new Error(`${label} timed out after ${timeoutMs} ms`)),
      timeoutMs,
    );
    Promise.resolve(promise).then(
      (value) => {
        clearTimeout(timeout);
        resolve(value);
      },
      (error) => {
        clearTimeout(timeout);
        reject(error);
      },
    );
  });
}

async function inspectPage(browserWebSocket) {
  const browserUrl = new URL(browserWebSocket);
  const endpoint = `http://${browserUrl.host}/json/new?${encodeURIComponent(url)}`;
  const response = await bounded(
    fetch(endpoint, { method: "PUT" }),
    "create Chromium page",
  );
  if (!response.ok) throw new Error(`could not create page: HTTP ${response.status}`);
  const target = await bounded(response.json(), "decode Chromium page target");
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await bounded(
    new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve, { once: true });
      socket.addEventListener("error", reject, { once: true });
    }),
    "open Chromium CDP socket",
  );

  let nextId = 0;
  let suppressedResponseId = null;
  const pending = new Map();
  const javascriptErrors = probeDiagnostic.javascriptErrors;
  socket.addEventListener("close", () => {
    rejectPendingCommands(new Error("Chromium CDP socket closed"));
  });
  socket.addEventListener("error", () => {
    rejectPendingCommands(new Error("Chromium CDP socket failed"));
  });
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.method === "Runtime.exceptionThrown") {
      const details = message.params.exceptionDetails || {};
      javascriptErrors.push(
        (details.exception && details.exception.description) || details.text || "exception"
      );
    }
    if (message.method === "Runtime.consoleAPICalled" && message.params.type === "error") {
      javascriptErrors.push(
        (message.params.args || []).map(
          (arg) => arg.value || arg.description || arg.type
        ).join(" ")
      );
    }
    if (message.id && message.id === suppressedResponseId) return;
    if (!message.id || !pending.has(message.id)) return;
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    refreshPendingDiagnostic();
    if (message.error) reject(new Error(message.error.message));
    else resolve(message.result);
  });

  function refreshPendingDiagnostic() {
    const now = Date.now();
    probeDiagnostic.pending = Array.from(pending.entries()).map(([id, entry]) => ({
      id,
      method: entry.method,
      action: entry.action,
      elapsedMs: now - entry.startedAt,
    }));
  }

  function rejectPendingCommands(error) {
    for (const [id, entry] of pending.entries()) {
      pending.delete(id);
      entry.reject(error);
    }
    refreshPendingDiagnostic();
  }

  function command(method, params = {}, options = {}) {
    const id = ++nextId;
    return new Promise((resolve, reject) => {
      const tolerateResponseTimeout = Boolean(options.tolerateResponseTimeout);
      if (tolerateResponseTimeout && !method.startsWith("Input.")) {
        reject(new Error(`response-timeout tolerance is invalid for ${method}`));
        return;
      }
      const eventType = typeof params.type === "string" ? ` (${params.type})` : "";
      const action = `${probeDiagnostic.action || method}${eventType}`;
      const startedAt = Date.now();
      const timeoutMs = commandTimeoutMs;
      probeDiagnostic.method = method;
      probeDiagnostic.lastCommand = {
        id,
        method,
        action,
        startedAt,
        elapsedMs: 0,
        timeoutMs,
        status: "pending",
      };
      function settle(status, error = null) {
        if (probeDiagnostic.lastCommand?.id !== id) return;
        probeDiagnostic.lastCommand = {
          ...probeDiagnostic.lastCommand,
          elapsedMs: Date.now() - startedAt,
          status,
          ...(error ? { error: String(error) } : {}),
        };
      }
      const timeout = setTimeout(() => {
        if (!pending.has(id)) return;
        const error = new Error(
          `CDP command timed out after ${timeoutMs} ms: ` +
          `${method}; action=${action}; lastState=${JSON.stringify(
            probeDiagnostic.lastState
          )}`
        );
        pending.delete(id);
        settle("timed-out", error);
        refreshPendingDiagnostic();
        if (tolerateResponseTimeout) {
          const timeoutRecord = {
            ...probeDiagnostic.lastCommand,
            lastState: probeDiagnostic.lastState,
          };
          delete timeoutRecord.startedAt;
          probeDiagnostic.inputResponseTimeouts.push(timeoutRecord);
          resolve({ responseTimedOut: true });
        } else {
          reject(error);
        }
      }, timeoutMs);
      pending.set(id, {
        method,
        action,
        startedAt,
        resolve(value) {
          clearTimeout(timeout);
          settle("completed");
          resolve(value);
        },
        reject(error) {
          clearTimeout(timeout);
          settle("failed", error);
          reject(error);
        },
      });
      refreshPendingDiagnostic();
      try {
        socket.send(JSON.stringify({ id, method, params }));
      } catch (error) {
        pending.delete(id);
        refreshPendingDiagnostic();
        settle("failed", error);
        clearTimeout(timeout);
        reject(error);
      }
    });
  }

  // Headless Chromium can apply a physical input event while failing to
  // acknowledge Input.dispatch* over CDP.  The following DOM-state assertion
  // remains authoritative, so retain the timeout as evidence and continue.
  function inputCommand(method, params) {
    return command(method, params, { tolerateResponseTimeout: true });
  }

  await command("Runtime.enable");
  if (cdpTimeoutScenario) {
    recordProbeState("read CDP-timeout regression sentinel");
    const sentinel = await command("Runtime.evaluate", {
      expression: `({ readyState: document.readyState, marker: "before-timeout" })`,
      returnByValue: true,
    });
    recordProbeState(
      "intentional suppressed Runtime.evaluate response",
      sentinel.result.value,
    );
    commandTimeoutMs = CDP_TIMEOUT_REGRESSION_MS;
    suppressedResponseId = nextId + 1;
    await command("Runtime.evaluate", {
      expression: "({ marker: 'suppressed-response' })",
      returnByValue: true,
    });
    throw new Error("suppressed CDP response unexpectedly resolved");
  }
  if (focusScenario) {
    async function evaluate(expression, action) {
      recordProbeState(action);
      const evaluation = await command("Runtime.evaluate", {
        expression,
        returnByValue: true,
      });
      if (evaluation.exceptionDetails) {
        throw new Error(
          evaluation.exceptionDetails.exception?.description ||
          evaluation.exceptionDetails.text ||
          "focus probe evaluation failed"
        );
      }
      return evaluation.result ? evaluation.result.value : undefined;
    }

    async function focusState(action = "read focus state") {
      return evaluate(`(() => {
        const active = document.activeElement;
        const activeDialog = active && active.closest('[role="dialog"]');
        const visible = (element) => {
          if (!element || !element.getClientRects().length) return false;
          const style = getComputedStyle(element);
          return style.display !== 'none' && style.visibility !== 'hidden';
        };
        const presentation = document.querySelector(
          '[role="region"][aria-label="Presentation mode"]'
        );
        return {
          readyState: document.readyState,
          focusBound: window._vibe_dialog_focus_bound === 1,
          activeProbe: active && active.dataset
            ? active.dataset.focusProbe || null
            : null,
          activeTag: active ? active.tagName : null,
          activeInsideDialog: Boolean(activeDialog),
          activeDialog: activeDialog
            ? activeDialog.getAttribute('aria-label')
            : null,
          presentationVisible: visible(presentation),
          capturedOrigins: Object.fromEntries(
            Array.from(document.querySelectorAll('[role="dialog"]')).map((dialog) => [
              dialog.getAttribute('aria-label'),
              dialog._vvFocusOrigin && dialog._vvFocusOrigin.dataset
                ? dialog._vvFocusOrigin.dataset.focusProbe || null
                : null,
            ])
          ),
          visibleDialogs: Array.from(document.querySelectorAll('[role="dialog"]'))
            .filter(visible)
            .map((dialog) => dialog.getAttribute('aria-label')),
        };
      })()`, action);
    }

    async function waitForFocus(check, label, timeout = 12000) {
      const deadline = Date.now() + timeout;
      let last = null;
      recordProbeState(label);
      while (Date.now() < deadline) {
        last = await focusState(label);
        recordProbeState(label, last);
        if (check(last)) return last;
        await delay(40);
      }
      throw new Error(`${label}: ${JSON.stringify(last)}`);
    }

    async function physicalClick(elementExpression, label) {
      const deadline = Date.now() + 3000;
      let point = null;
      recordProbeState(label);
      while (Date.now() < deadline) {
        point = await evaluate(`(() => {
          const element = ${elementExpression};
          if (!element) return null;
          element.scrollIntoView({ block: 'center', inline: 'nearest' });
          const rect = element.getBoundingClientRect();
          const x = rect.left + rect.width / 2;
          const y = rect.top + rect.height / 2;
          const hit = document.elementFromPoint(x, y);
          return {
            x,
            y,
            visible: rect.width > 0 && rect.height > 0,
            receivesPointer: Boolean(hit && (hit === element || element.contains(hit))),
          };
        })()`, `locate ${label}`);
        recordProbeState(label, point);
        if (point && point.visible && point.receivesPointer) break;
        await delay(40);
      }
      if (!point || !point.visible || !point.receivesPointer) {
        throw new Error(`${label} is not physically clickable: ${JSON.stringify(point)}`);
      }
      await inputCommand("Input.dispatchMouseEvent", {
        type: "mouseMoved",
        x: point.x,
        y: point.y,
      });
      await inputCommand("Input.dispatchMouseEvent", {
        type: "mousePressed",
        x: point.x,
        y: point.y,
        button: "left",
        clickCount: 1,
      });
      await inputCommand("Input.dispatchMouseEvent", {
        type: "mouseReleased",
        x: point.x,
        y: point.y,
        button: "left",
        clickCount: 1,
      });
    }

    async function pressKey(key, code, virtualKeyCode, modifiers = 0) {
      recordProbeState(`press ${key} during ${focusStage}`);
      const params = {
        key,
        code,
        modifiers,
        windowsVirtualKeyCode: virtualKeyCode,
        nativeVirtualKeyCode: virtualKeyCode,
      };
      await inputCommand("Input.dispatchKeyEvent", keyDown(params));
      await inputCommand("Input.dispatchKeyEvent", { type: "keyUp", ...params });
    }

    await waitForFocus(
      (state) => state.readyState === "complete" && state.focusBound &&
        state.visibleDialogs.length === 0,
      "focus-restoration viewer never became ready",
    );
    const errorsBefore = javascriptErrors.length;
    const focus = {};
    function finishFocus() {
      focusStage = "complete";
      socket.close();
      finish(0, {
        focus: {
          ...focus,
          javascriptErrors: javascriptErrors.slice(errorsBefore),
        },
      });
    }
    if (![
      'core',
      'disabled-origin',
      'presentation-origin',
      'removed-origin',
    ].includes(focusScenario)) {
      throw new Error(`unknown focus scenario: ${focusScenario}`);
    }
    if (focusScenario === "core") {
      focusStage = "screenshot";

    await evaluate(`(() => {
      const launcher = document.querySelector('button[title="Export screenshot"]');
      if (!launcher) return false;
      launcher.dataset.focusProbe = 'screenshot-launcher';
      return true;
    })()`, "tag screenshot launcher");
    await physicalClick(
      `document.querySelector('button[data-focus-probe="screenshot-launcher"]')`,
      "screenshot launcher",
    );
    await waitForFocus(
      (state) => state.visibleDialogs.includes("Export screenshot") &&
        state.capturedOrigins["Export screenshot"] === "screenshot-launcher",
      "screenshot dialog did not open",
    );
    await physicalClick(
      `Array.from(document.querySelectorAll('button')).find(
        (button) => button.textContent.trim() === 'Cancel' &&
          button.getClientRects().length
      )`,
      "screenshot Cancel button",
    );
    const screenshot = await waitForFocus(
      (state) => state.visibleDialogs.length === 0 &&
        state.activeProbe === "screenshot-launcher",
      "screenshot dialog did not return focus to its launcher",
    );

    focusStage = "menu";
    await evaluate(`(() => {
      const launcher = document.querySelector(
        'button[title="Diagnostics, settings & help"]'
      );
      if (!launcher) return false;
      launcher.dataset.focusProbe = 'info-launcher';
      return true;
    })()`, "tag diagnostics and settings launcher");
    await physicalClick(
      `document.querySelector('button[data-focus-probe="info-launcher"]')`,
      "Info launcher",
    );
    await physicalClick(
      `Array.from(document.querySelectorAll('.v-list-item')).find(
        (item) => item.textContent.trim().startsWith('About vibe-view')
      )`,
      "About menu item",
    );
    await waitForFocus(
      (state) => state.visibleDialogs.includes("About vibe-view") &&
        state.capturedOrigins["About vibe-view"] === "info-launcher",
      "About dialog did not open from the transient menu",
    );
    await physicalClick(
      `Array.from(document.querySelectorAll('button')).find(
        (button) => button.textContent.trim() === 'Close' &&
          button.getClientRects().length
      )`,
      "About Close button",
    );
    const menu = await waitForFocus(
      (state) => state.visibleDialogs.length === 0 &&
        state.activeProbe === "info-launcher",
      "menu dialog did not return focus to the connected menu activator",
    );

    focusStage = "nested";
    await physicalClick(
      `document.querySelector('button[data-focus-probe="info-launcher"]')`,
      "Info launcher for Settings",
    );
    await physicalClick(
      `Array.from(document.querySelectorAll('.v-list-item')).find(
        (item) => item.textContent.trim().startsWith('Settings')
      )`,
      "Settings menu item",
    );
    await waitForFocus(
      (state) => state.visibleDialogs.includes("Settings") &&
        state.capturedOrigins.Settings === "info-launcher",
      "Settings dialog did not open from the transient menu",
    );
    const settingsFocused = await evaluate(`(() => {
      const dialog = Array.from(document.querySelectorAll('[role="dialog"]')).find(
        (element) => element.getAttribute('aria-label') === 'Settings'
      );
      const button = dialog && Array.from(dialog.querySelectorAll('button')).find(
        (element) => element.textContent.trim() === 'Cancel'
      );
      if (!button) return false;
      button.dataset.focusProbe = 'settings-cancel';
      button.focus();
      return document.activeElement === button;
    })()`, "focus Settings cancel button");
    if (!settingsFocused) throw new Error("Settings Cancel button was not focusable");
    await pressKey("k", "KeyK", 75, 2);
    await waitForFocus(
      (state) => state.visibleDialogs.includes("Settings") &&
        state.visibleDialogs.includes("Command palette") &&
        state.activeDialog === "Command palette" &&
        state.capturedOrigins["Command palette"] === "settings-cancel",
      "Ctrl+K did not stack the palette over Settings",
    );
    await pressKey("Escape", "Escape", 27);
    const nested = await waitForFocus(
      (state) => state.visibleDialogs.length === 1 &&
        state.visibleDialogs[0] === "Settings" &&
        state.activeProbe === "settings-cancel" &&
        state.activeDialog === "Settings",
      "closing the palette did not restore focus inside Settings",
    );
    await physicalClick(
      `document.querySelector('button[data-focus-probe="settings-cancel"]')`,
      "Settings Cancel button",
    );
    const nestedClosed = await waitForFocus(
      (state) => state.visibleDialogs.length === 0 &&
        state.activeProbe === "info-launcher",
      "closing Settings did not restore its menu activator",
    );
      focus.screenshot = screenshot;
      focus.menu = menu;
      focus.nested = nested;
      focus.nestedClosed = nestedClosed;
    }

    focusStage = "shortcut";
    const exportFocused = await evaluate(`(() => {
      const launcher = document.querySelector('button[title="Export geometry / scene…"]');
      if (!launcher) return false;
      launcher.dataset.focusProbe = 'export-launcher';
      launcher.focus();
      return document.activeElement === launcher;
    })()`, "tag and focus export launcher");
    if (!exportFocused) throw new Error("Export launcher was not focusable");
    if (focusScenario === "core") {
      await pressKey("k", "KeyK", 75, 2);
      await waitForFocus(
        (state) => state.visibleDialogs.includes("Command palette") &&
          state.activeInsideDialog &&
          state.capturedOrigins["Command palette"] === "export-launcher",
        "Ctrl+K did not open and focus the command palette",
      );
      await pressKey("Escape", "Escape", 27);
      focus.shortcut = await waitForFocus(
        (state) => state.visibleDialogs.length === 0 &&
          state.activeProbe === "export-launcher",
        "keyboard dialog did not return focus to the exact prior control",
      );
      finishFocus();
      return;
    }

    if (focusScenario === "disabled-origin") {
    focusStage = "disabled-origin";
    const disabledFocused = await evaluate(`(() => {
      const launcher = document.querySelector(
        'button[data-focus-probe="export-launcher"]'
      );
      if (!launcher) return false;
      launcher.focus();
      return document.activeElement === launcher;
    })()`, "refocus export launcher before disabling it");
    if (!disabledFocused) throw new Error("Export launcher could not be refocused");
    await pressKey("k", "KeyK", 75, 2);
    await waitForFocus(
      (state) => state.visibleDialogs.includes("Command palette") &&
        state.capturedOrigins["Command palette"] === "export-launcher",
      "disabled-origin command palette did not open",
    );
    await evaluate(`document.querySelector(
      'button[data-focus-probe="export-launcher"]'
    ).disabled = true`, "disable export launcher while palette is open");
    await pressKey("Escape", "Escape", 27);
    await waitForFocus(
      (state) => state.visibleDialogs.length === 0 && state.activeProbe === null,
      "disabled palette origin received focus or the dialog stayed open",
    );
    await delay(100);
    const disabledOrigin = await focusState("read settled disabled-origin focus");
    if (disabledOrigin.visibleDialogs.length || disabledOrigin.activeProbe !== null ||
        disabledOrigin.activeTag !== "BODY" || disabledOrigin.activeInsideDialog) {
      throw new Error(
        `disabled palette origin changed after settling: ${JSON.stringify(disabledOrigin)}`
      );
    }
    await evaluate(`document.querySelector(
      'button[data-focus-probe="export-launcher"]'
    ).disabled = false`, "re-enable export launcher");
      focus.disabledOrigin = disabledOrigin;
      finishFocus();
      return;
    }

    if (focusScenario === "presentation-origin") {
    focusStage = "presentation";
    await pressKey("p", "KeyP", 80);
    await waitForFocus(
      (state) => state.presentationVisible,
      "presentation overlay did not open for focus restoration",
    );
    const presentationFocused = await evaluate(`(() => {
      const button = document.querySelector('button[aria-label="Next slide"]');
      if (!button) return false;
      button.dataset.focusProbe = 'presentation-next';
      button.focus();
      return document.activeElement === button;
    })()`, "focus presentation Next button");
    if (!presentationFocused) throw new Error("presentation Next button was not focusable");
    await pressKey("k", "KeyK", 75, 2);
    await waitForFocus(
      (state) => state.presentationVisible &&
        state.visibleDialogs.includes("Command palette") &&
        state.activeDialog === "Command palette" &&
        state.capturedOrigins["Command palette"] === "presentation-next",
      "Ctrl+K did not open from the presentation overlay",
    );
    await pressKey("Escape", "Escape", 27);
    const presentation = await waitForFocus(
      (state) => state.presentationVisible &&
        state.visibleDialogs.length === 0 &&
        state.activeProbe === "presentation-next",
      "palette did not restore the presentation control",
    );
    await pressKey("Escape", "Escape", 27);
    await waitForFocus(
      (state) => !state.presentationVisible && state.visibleDialogs.length === 0,
      "presentation overlay did not close after the focus regression",
    );
      focus.presentation = presentation;
      finishFocus();
      return;
    }

    if (focusScenario === "removed-origin") {
    focusStage = "removed-origin";
    await evaluate(`(() => {
      const launcher = document.querySelector('button[title="Export screenshot"]');
      if (!launcher) return false;
      launcher.dataset.focusProbe = 'removed-launcher';
      return true;
    })()`, "tag removable screenshot launcher");
    await physicalClick(
      `document.querySelector('button[data-focus-probe="removed-launcher"]')`,
      "removable screenshot launcher",
    );
    await waitForFocus(
      (state) => state.visibleDialogs.includes("Export screenshot") &&
        state.capturedOrigins["Export screenshot"] === "removed-launcher",
      "removed-origin screenshot dialog did not open",
    );
    const removed = await evaluate(`(() => {
      const launcher = document.querySelector(
        'button[data-focus-probe="removed-launcher"]'
      );
      if (!launcher) return false;
      launcher.remove();
      return !launcher.isConnected;
    })()`, "remove screenshot launcher while dialog is open");
    if (!removed) throw new Error("screenshot launcher could not be removed");
    await physicalClick(
      `Array.from(document.querySelectorAll('button')).find(
        (button) => button.textContent.trim() === 'Cancel' &&
          button.getClientRects().length
      )`,
      "removed-origin screenshot Cancel button",
    );
    await waitForFocus(
      (state) => state.visibleDialogs.length === 0 && state.activeProbe === null,
      "removed launcher received focus or focus moved to an older origin",
    );
    await delay(100);
    const removedOrigin = await focusState("read settled removed-origin focus");
    if (removedOrigin.visibleDialogs.length || removedOrigin.activeProbe !== null ||
        removedOrigin.activeTag !== "BODY" || removedOrigin.activeInsideDialog) {
      throw new Error(
        `removed launcher changed focus after settling: ${JSON.stringify(removedOrigin)}`
      );
    }
      focus.removedOrigin = removedOrigin;
      finishFocus();
      return;
    }
  }
  if (colorInputScenario) {
    const colorInputExpression = `JSON.stringify((() => {
      const trameState = window.trame && window.trame.state;
      return {
        readyState: document.readyState,
        selectedSection: trameState
          ? String(trameState.get('selected_section') || '')
          : '',
        inputs: Array.from(document.querySelectorAll('input[type="color"]')).map(
          (input) => {
            const rect = input.getBoundingClientRect();
            const style = getComputedStyle(input);
            return {
              tagName: input.tagName,
              type: input.type,
              visible: Boolean(
                rect.width > 0 && rect.height > 0 &&
                style.display !== 'none' && style.visibility !== 'hidden'
              ),
              disabled: input.disabled,
              tabIndex: input.tabIndex,
              value: input.value,
              ariaLabel: input.getAttribute('aria-label'),
              ariaLabelledby: input.getAttribute('aria-labelledby'),
              title: input.getAttribute('title'),
            };
          }
        ),
      };
    })())`;
    const deadline = Date.now() + 12000;
    let state = { readyState: "unknown", selectedSection: "", inputs: [] };
    recordProbeState("wait for element-colour input", state);
    while (Date.now() < deadline) {
      const evaluation = await command("Runtime.evaluate", {
        expression: colorInputExpression,
        returnByValue: true,
      });
      if (evaluation.result && evaluation.result.value) {
        state = JSON.parse(evaluation.result.value);
        recordProbeState("wait for element-colour input", state);
      }
      if (
        state.readyState === "complete" &&
        state.selectedSection === "structure" &&
        state.inputs.length === 1 &&
        state.inputs[0].visible &&
        !state.inputs[0].disabled &&
        state.inputs[0].tabIndex === 0
      ) {
        await delay(300);
        const retained = await command("Runtime.evaluate", {
          expression: colorInputExpression,
          returnByValue: true,
        });
        socket.close();
        finish(0, { colorInput: JSON.parse(retained.result.value) });
        return;
      }
      await delay(100);
    }
    socket.close();
    finish(3, {
      error: "visible element-colour input never reached the served DOM",
      state,
      browserStderr,
    });
    return;
  }
  if (browserScreenshotScenario) {
    // #116: Export screenshot takes the frame the browser draws, so an
    // exported figure keeps the silhouette pass's outlines. The probe
    // catches the downloads the export starts and reads the images back:
    // at scale 1 with the pass on and off, whose difference must be the
    // outline of the orbital, and at scale 2 on a transparent background.
    async function evaluateExport(expression, action, awaitPromise = false) {
      recordProbeState(action);
      const evaluation = await command("Runtime.evaluate", {
        expression,
        returnByValue: true,
        awaitPromise,
      });
      if (evaluation.exceptionDetails) {
        throw new Error(
          evaluation.exceptionDetails.exception?.description ||
          evaluation.exceptionDetails.text ||
          "browser screenshot probe evaluation failed"
        );
      }
      return evaluation.result ? evaluation.result.value : undefined;
    }
    const exportStateExpression = `(() => {
      let view = null;
      for (const name of Object.keys((window.trame && window.trame.refs) || {})) {
        try {
          const candidate = window.trame.refs[name].$.provides.view;
          if (candidate && candidate.openglRenderWindow) {
            view = candidate;
            break;
          }
        } catch (error) {
          // not a vtk view
        }
      }
      const rect = view && view.container ? view.container.getBoundingClientRect() : null;
      return {
        readyState: document.readyState,
        browserReady: Boolean(window.trame && window.trame.state &&
          window.trame.state.get('browser_capture_ready')),
        silhouette: window.vvSilhouette ? { ...window.vvSilhouette.stats } : null,
        capture: window.vvCapture ? { ...window.vvCapture.stats } : null,
        css: rect && rect.width > 0 ? [rect.width, rect.height] : null,
        live: view ? Array.from(view.openglRenderWindow.getSize()) : null,
      };
    })()`;
    const deadline = Date.now() + 30000;
    let ready = null;
    while (Date.now() < deadline) {
      ready = await evaluateExport(exportStateExpression, "wait for the outlined orbital");
      recordProbeState("wait for the outlined orbital", ready);
      if (ready.readyState === "complete" && ready.browserReady && ready.css &&
          ready.silhouette && ready.silhouette.frames > 0 && ready.silhouette.targets > 0) break;
      ready = null;
      await delay(150);
    }
    if (!ready) throw new Error("the browser never reported an outlined view to capture");
    await evaluateExport(`(() => {
      const record = window.__vvExport = { downloads: [], shots: {} };
      const click = HTMLElement.prototype.click;
      HTMLAnchorElement.prototype.click = function () {
        if (!this.download) return click.call(this);
        record.downloads.push({ name: this.download, href: this.href });
      };
      return true;
    })()`, "catch the export downloads");
    async function exportShot(label, scale, transparent, outlines) {
      const start = await evaluateExport(`(async () => {
        const count = window.__vvExport.downloads.length;
        await window.trame.state.update({
          screenshot_scale: ${scale},
          screenshot_transparent: ${transparent},
        });
        await window.trame.state.flush();
        window.vvSilhouette.stats.enabled = ${outlines};
        window.trame.trigger('save_screenshot');
        return count;
      })()`, `export ${label}`, true);
      const exportDeadline = Date.now() + 30000;
      let polled = null;
      while (Date.now() < exportDeadline) {
        polled = await evaluateExport(`({
          downloads: window.__vvExport.downloads.length,
          status: window.trame.state.get('status_message'),
          capture: { ...window.vvCapture.stats },
        })`, `wait for export ${label}`);
        recordProbeState(`wait for export ${label}`, polled);
        if (polled.downloads > start) break;
        await delay(150);
      }
      if (!(polled && polled.downloads > start)) {
        throw new Error(`export ${label} never downloaded: ${JSON.stringify(polled)}`);
      }
      return evaluateExport(`(async () => {
        window.vvSilhouette.stats.enabled = true;
        const download = window.__vvExport.downloads[${start}];
        const image = new Image();
        image.src = download.href;
        await image.decode();
        const canvas = document.createElement('canvas');
        canvas.width = image.naturalWidth;
        canvas.height = image.naturalHeight;
        const context = canvas.getContext('2d');
        context.drawImage(image, 0, 0);
        window.__vvExport.shots['${label}'] = {
          width: canvas.width,
          height: canvas.height,
          data: context.getImageData(0, 0, canvas.width, canvas.height).data,
        };
        return {
          name: download.name,
          width: canvas.width,
          height: canvas.height,
          status: window.trame.state.get('status_message'),
          capture: { ...window.vvCapture.stats.last },
        };
      })()`, `read export ${label}`, true);
    }
    const shots = [
      await exportShot("outlined", 1, false, true),
      await exportShot("plain", 1, false, false),
      await exportShot("transparent", 2, true, true),
    ];
    // The dark pixels the pass adds to the export lie on the boundary of the
    // orbital in the export without it, and cover most of that boundary.
    const analysis = await evaluateExport(`(() => {
      const shots = window.__vvExport.shots;
      const a = shots.outlined, b = shots.plain, c = shots.transparent;
      const W = a.width, H = a.height, N = W * H;
      const dark = (px, i) => px[4 * i] < 70 && px[4 * i + 1] < 70 && px[4 * i + 2] < 70 &&
        px[4 * i + 3] > 200;
      const bg = [b.data[0], b.data[1], b.data[2]];
      const covered = new Uint8Array(N), extra = new Uint8Array(N), boundary = new Uint8Array(N);
      let nExtra = 0, nBoundary = 0;
      for (let i = 0; i < N; i++) {
        const distance = Math.abs(b.data[4 * i] - bg[0]) + Math.abs(b.data[4 * i + 1] - bg[1]) +
          Math.abs(b.data[4 * i + 2] - bg[2]);
        covered[i] = distance > 30 ? 1 : 0;
        if (dark(a.data, i) && !dark(b.data, i)) {
          extra[i] = 1;
          nExtra++;
        }
      }
      for (let y = 1; y < H - 1; y++) {
        for (let x = 1; x < W - 1; x++) {
          const i = y * W + x;
          if (covered[i] &&
              (!covered[i - 1] || !covered[i + 1] || !covered[i - W] || !covered[i + W])) {
            boundary[i] = 1;
            nBoundary++;
          }
        }
      }
      const near = (mask, i, r) => {
        const px = i % W, py = (i - px) / W;
        for (let dy = -r; dy <= r; dy++) {
          const qy = py + dy;
          if (qy < 0 || qy >= H) continue;
          for (let dx = -r; dx <= r; dx++) {
            const qx = px + dx;
            if (qx >= 0 && qx < W && mask[qy * W + qx]) return true;
          }
        }
        return false;
      };
      let extraNearBoundary = 0, boundaryOutlined = 0;
      for (let i = 0; i < N; i++) {
        if (extra[i] && near(boundary, i, 4)) extraNearBoundary++;
        if (boundary[i] && near(extra, i, 4)) boundaryOutlined++;
      }
      let clear = 0, transparentDark = 0;
      for (let i = 0; i < c.width * c.height; i++) {
        if (c.data[4 * i + 3] === 0) clear++;
        else if (dark(c.data, i)) transparentDark++;
      }
      return {
        extra: nExtra,
        boundary: nBoundary,
        precision: nExtra ? extraNearBoundary / nExtra : 0,
        recall: nBoundary ? boundaryOutlined / nBoundary : 0,
        corner: Array.from(c.data.slice(0, 4)),
        clear,
        transparentDark,
        // Twice the size and twice the line width: about four times the
        // outline pixels, if the line scales with the export.
        scaleRatio: nExtra ? transparentDark / nExtra : 0,
      };
    })()`, "compare the exports");
    const after = await evaluateExport(exportStateExpression, "read the view after the exports");
    socket.close();
    finish(0, { browserScreenshot: { ready, shots, analysis, after } });
    return;
  }
  if (silhouetteScenario) {
    // #112: the client outlines the orbital isosurface. A frame read back
    // with the pass on and off: the dark pixels the pass adds must sit on the
    // boundary of the outlined surface, rendered alone, and cover most of
    // it, before and during a trusted drag; picking must still work.
    async function evaluateSilhouette(expression, action) {
      recordProbeState(action);
      const evaluation = await command("Runtime.evaluate", {
        expression,
        returnByValue: true,
      });
      if (evaluation.exceptionDetails) {
        throw new Error(
          evaluation.exceptionDetails.exception?.description ||
          evaluation.exceptionDetails.text ||
          "silhouette probe evaluation failed"
        );
      }
      return evaluation.result ? evaluation.result.value : undefined;
    }
    const silhouetteExpression = `(() => {
      const sil = window.vvSilhouette;
      let view = null;
      for (const name of Object.keys(window.trame.refs || {})) {
        try {
          const candidate = window.trame.refs[name].$.provides.view;
          if (candidate && candidate.openglRenderWindow) {
            view = candidate;
            break;
          }
        } catch (error) {
          // not a vtk view
        }
      }
      const result = {
        readyState: document.readyState,
        installed: Boolean(sil && sil.stats.installed),
        stats: sil ? { ...sil.stats } : null,
        camera: null,
        frame: null,
      };
      if (!view || !result.installed) return result;
      const ogl = view.openglRenderWindow;
      const renderer = view.renderer;
      const canvas = ogl.getCanvas();
      const W = canvas.width, H = canvas.height, N = W * H;
      const gl = canvas.getContext('webgl2');
      const camera = renderer.getActiveCamera();
      result.camera = {
        position: Array.from(camera.getPosition()),
        focalPoint: Array.from(camera.getFocalPoint()),
      };
      // vtk.js skips an explicit render while it animates a drag, so draw
      // through the view and read the pixels in the same task.
      const draw = () => ogl.traverseAllPasses();
      const read = () => {
        const pixels = new Uint8Array(N * 4);
        gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
        return pixels;
      };
      const dark = (px, i) => px[4 * i] < 70 && px[4 * i + 1] < 70 && px[4 * i + 2] < 70;
      sil.stats.enabled = true;
      draw();
      const on = read();
      const outlined = sil.outlined();
      sil.stats.enabled = false;
      draw();
      const off = read();
      // The outlined surfaces alone, without the pass: their coverage.
      const hidden = renderer.getViewProps()
        .filter((prop) => prop.getVisibility() && outlined.indexOf(prop) < 0);
      hidden.forEach((prop) => prop.setVisibility(false));
      draw();
      const cover = read();
      hidden.forEach((prop) => prop.setVisibility(true));
      sil.stats.enabled = true;
      draw();
      const bg = renderer.getBackground().map((c) => c * 255);
      const covered = new Uint8Array(N), extra = new Uint8Array(N), boundary = new Uint8Array(N);
      let nExtra = 0, nOnDark = 0, nOffDark = 0, nBoundary = 0;
      for (let i = 0; i < N; i++) {
        const distance = Math.abs(cover[4 * i] - bg[0]) + Math.abs(cover[4 * i + 1] - bg[1]) +
          Math.abs(cover[4 * i + 2] - bg[2]);
        covered[i] = distance > 30 ? 1 : 0;
        const withPass = dark(on, i), withoutPass = dark(off, i);
        if (withPass) nOnDark++;
        if (withoutPass) nOffDark++;
        if (withPass && !withoutPass) {
          extra[i] = 1;
          nExtra++;
        }
      }
      for (let yy = 1; yy < H - 1; yy++) {
        for (let xx = 1; xx < W - 1; xx++) {
          const i = yy * W + xx;
          if (covered[i] &&
              (!covered[i - 1] || !covered[i + 1] || !covered[i - W] || !covered[i + W])) {
            boundary[i] = 1;
            nBoundary++;
          }
        }
      }
      const near = (mask, i, r) => {
        const px = i % W, py = (i - px) / W;
        for (let dy = -r; dy <= r; dy++) {
          const qy = py + dy;
          if (qy < 0 || qy >= H) continue;
          for (let dx = -r; dx <= r; dx++) {
            const qx = px + dx;
            if (qx >= 0 && qx < W && mask[qy * W + qx]) return true;
          }
        }
        return false;
      };
      let extraNearBoundary = 0, boundaryOutlined = 0;
      for (let i = 0; i < N; i++) {
        if (extra[i] && near(boundary, i, 4)) extraNearBoundary++;
        if (boundary[i] && near(extra, i, 4)) boundaryOutlined++;
      }
      result.frame = {
        width: W,
        height: H,
        outlined: outlined.length,
        onDark: nOnDark,
        offDark: nOffDark,
        extra: nExtra,
        boundary: nBoundary,
        precision: nExtra ? extraNearBoundary / nExtra : 0,
        recall: nBoundary ? boundaryOutlined / nBoundary : 0,
      };
      return result;
    })()`;
    const readyExpression = `(() => {
      const sil = window.vvSilhouette;
      const canvas = Array.from(document.querySelectorAll('canvas'))
        .sort((a, b) => b.width * b.height - a.width * a.height)[0];
      const rect = canvas ? canvas.getBoundingClientRect() : null;
      return {
        readyState: document.readyState,
        stats: sil ? { ...sil.stats } : null,
        outlines: window.trame && window.trame.state
          ? Boolean(window.trame.state.get('figure_outlines'))
          : false,
        targets: window.trame && window.trame.state
          ? (window.trame.state.get('silhouette_targets') || []).length
          : 0,
        canvas: rect && rect.width > 0
          ? { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
          : null,
      };
    })()`;
    async function waitForSilhouette(check, label, timeout = 30000) {
      const deadline = Date.now() + timeout;
      let last = null;
      while (Date.now() < deadline) {
        last = await evaluateSilhouette(readyExpression, label);
        recordProbeState(label, last);
        if (check(last)) return last;
        await delay(150);
      }
      throw new Error(`${label}: ${JSON.stringify(last)}`);
    }
    const ready = await waitForSilhouette(
      (state) => state.readyState === "complete" && state.canvas && state.stats &&
        state.stats.installed && state.stats.frames > 0 && state.stats.targets > 0,
      "the silhouette pass never outlined the orbital",
    );
    const before = await evaluateSilhouette(silhouetteExpression, "read outlines before the drag");
    const cx = ready.canvas.x + ready.canvas.width / 2;
    const cy = ready.canvas.y + ready.canvas.height / 2;
    await inputCommand("Input.dispatchMouseEvent", { type: "mouseMoved", x: cx, y: cy });
    await inputCommand("Input.dispatchMouseEvent", {
      type: "mousePressed",
      x: cx,
      y: cy,
      button: "left",
      clickCount: 1,
    });
    let x = cx, y = cy;
    for (let step = 0; step < 12; step++) {
      x += 14; y += 9;
      await inputCommand("Input.dispatchMouseEvent", {
        type: "mouseMoved",
        x,
        y,
        button: "left",
        buttons: 1,
      });
      await delay(30);
    }
    await delay(200);
    const during = await evaluateSilhouette(silhouetteExpression, "read outlines during the drag");
    await inputCommand("Input.dispatchMouseEvent", {
      type: "mouseReleased",
      x,
      y,
      button: "left",
      buttons: 0,
      clickCount: 1,
    });
    await delay(1500);
    // Hardware picking renders through the same passes; the outline pass
    // must leave its framebuffer alone, or a click picks nothing.
    await evaluateSilhouette(
      "window.trame.trigger('toggle_measure_mode', [true])",
      "enter measure mode",
    );
    await delay(800);
    await inputCommand("Input.dispatchMouseEvent", { type: "mouseMoved", x: cx, y: cy });
    await inputCommand("Input.dispatchMouseEvent", {
      type: "mousePressed",
      x: cx,
      y: cy,
      button: "left",
      clickCount: 1,
    });
    await inputCommand("Input.dispatchMouseEvent", {
      type: "mouseReleased",
      x: cx,
      y: cy,
      button: "left",
      clickCount: 1,
    });
    let picked = [];
    const pickDeadline = Date.now() + 8000;
    while (Date.now() < pickDeadline) {
      picked = await evaluateSilhouette(
        "window.trame.state.get('selected_atoms') || []",
        "wait for the measure-mode pick",
      );
      if (picked.length) break;
      await delay(150);
    }
    const after = await evaluateSilhouette(readyExpression, "read the pass after picking");
    const direction = (state) => {
      const d = state.camera.position.map((v, i) => v - state.camera.focalPoint[i]);
      const norm = Math.hypot(...d);
      return d.map((v) => v / norm);
    };
    const b = direction(before), a = direction(during);
    const cosine = b[0] * a[0] + b[1] * a[1] + b[2] * a[2];
    socket.close();
    finish(0, {
      silhouette: {
        ready,
        before,
        during,
        after,
        picked,
        cameraTurnDeg: Math.acos(Math.max(-1, Math.min(1, cosine))) * 180 / Math.PI,
      },
    });
    return;
  }
  if (labelFollowerScenario) {
    // #111: while a trusted drag rotates the structure, and before the
    // server hears of it, the frame must show every label facing the camera.
    // The probe reads back a frame of the labels alone and compares it with
    // the glyphs rasterised for the camera-facing basis and with the
    // geometry the server sent.
    async function evaluateFollower(expression, action) {
      recordProbeState(action);
      const evaluation = await command("Runtime.evaluate", {
        expression,
        returnByValue: true,
      });
      if (evaluation.exceptionDetails) {
        throw new Error(
          evaluation.exceptionDetails.exception?.description ||
          evaluation.exceptionDetails.text ||
          "label follower probe evaluation failed"
        );
      }
      return evaluation.result ? evaluation.result.value : undefined;
    }
    const followerExpression = `(() => {
      const follow = window.vvLabelFollow;
      const canvas = Array.from(document.querySelectorAll('canvas'))
        .sort((a, b) => b.width * b.height - a.width * a.height)[0];
      const rect = canvas ? canvas.getBoundingClientRect() : null;
      const renderer = follow && follow.renderer ? follow.renderer() : null;
      const result = {
        readyState: document.readyState,
        scriptInstalled: Boolean(follow),
        rendererKnown: Boolean(renderer),
        canvas: rect && rect.width > 0
          ? { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
          : null,
        stats: follow
          ? { configured: follow.stats.configured, draws: follow.stats.draws }
          : null,
        camera: null,
        glyphs: 0,
        sample: null,
        frame: null,
      };
      if (!renderer) return result;
      const camera = renderer.getActiveCamera();
      const eye = Array.from(camera.getPosition());
      const up = Array.from(camera.getViewUp());
      result.camera = { position: eye, focalPoint: Array.from(camera.getFocalPoint()), viewUp: up };
      const renderWindow = renderer.getRenderWindow();
      const view = renderWindow.getViews()[0];
      const glCanvas = (view.getCanvas && view.getCanvas()) || canvas;
      const W = glCanvas.width, H = glCanvas.height;
      const predicted = new Uint8Array(W * H), stale = new Uint8Array(W * H);
      // Fill a projected triangle into a mask: pixel centres inside all three
      // edges (either winding). Display coordinates start bottom-left, as do
      // readPixels rows.
      function fill(mask, xy, a, b, c) {
        const ax = xy[2 * a], ay = xy[2 * a + 1], bx = xy[2 * b], by = xy[2 * b + 1];
        const cx = xy[2 * c], cy = xy[2 * c + 1];
        const area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
        if (!(Math.abs(area) > 1e-9)) return;
        const x0 = Math.max(0, Math.floor(Math.min(ax, bx, cx)));
        const x1 = Math.min(W - 1, Math.ceil(Math.max(ax, bx, cx)));
        const y0 = Math.max(0, Math.floor(Math.min(ay, by, cy)));
        const y1 = Math.min(H - 1, Math.ceil(Math.max(ay, by, cy)));
        for (let y = y0; y <= y1; y++) {
          const py = y + 0.5;
          for (let x = x0; x <= x1; x++) {
            const px = x + 0.5;
            const w0 = ((bx - px) * (cy - py) - (by - py) * (cx - px)) / area;
            const w1 = ((cx - px) * (ay - py) - (cy - py) * (ax - px)) / area;
            if (w0 >= 0 && w1 >= 0 && w0 + w1 <= 1) mask[y * W + x] = 1;
          }
        }
      }
      const hidden = [];
      let glyphs = 0;
      renderer.getActors().forEach((actor) => {
        const mapper = actor.getMapper && actor.getMapper();
        const polydata = mapper && mapper.getInputData && mapper.getInputData();
        const pointData = polydata && polydata.getPointData ? polydata.getPointData() : null;
        const anchorArray = pointData && pointData.getArrayByName('vv_label_anchor');
        const localArray = pointData && pointData.getArrayByName('vv_label_local');
        if (!anchorArray || !localArray) {
          if (actor.getVisibility()) hidden.push(actor);
          return;
        }
        if (!actor.getVisibility()) return;
        const anchors = anchorArray.getData(), local = localArray.getData();
        const points = polydata.getPoints().getData();
        const n = polydata.getPoints().getNumberOfPoints();
        if (!result.sample) result.sample = [points[0], points[1], points[2]];
        // Where each point should be drawn (the camera-facing basis for this
        // camera) and where the geometry the server sent would put it.
        const facing = new Float64Array(2 * n), sent = new Float64Array(2 * n);
        for (let p = 0; p < n; p++) {
          const ax = anchors[3 * p], ay = anchors[3 * p + 1], az = anchors[3 * p + 2];
          if (p === 0 || ax !== anchors[3 * p - 3] || ay !== anchors[3 * p - 2] ||
              az !== anchors[3 * p - 1]) glyphs++;
          let fx = eye[0] - ax, fy = eye[1] - ay, fz = eye[2] - az;
          const fn = Math.hypot(fx, fy, fz);
          fx /= fn; fy /= fn; fz /= fn;
          let rx = up[1] * fz - up[2] * fy;
          let ry = up[2] * fx - up[0] * fz;
          let rz = up[0] * fy - up[1] * fx;
          const rn = Math.hypot(rx, ry, rz);
          rx /= rn; ry /= rn; rz /= rn;
          const ux = fy * rz - fz * ry, uy = fz * rx - fx * rz, uz = fx * ry - fy * rx;
          const lx = local[3 * p], ly = local[3 * p + 1], lz = local[3 * p + 2];
          const d = view.worldToDisplay(
            ax + rx * lx + ux * ly + fx * lz,
            ay + ry * lx + uy * ly + fy * lz,
            az + rz * lx + uz * ly + fz * lz,
            renderer,
          );
          facing[2 * p] = d[0]; facing[2 * p + 1] = d[1];
          const s = view.worldToDisplay(
            points[3 * p], points[3 * p + 1], points[3 * p + 2], renderer,
          );
          sent[2 * p] = s[0]; sent[2 * p + 1] = s[1];
        }
        const polys = polydata.getPolys().getData();
        for (let i = 0; i < polys.length; i += polys[i] + 1) {
          for (let k = 2; k < polys[i]; k++) {
            fill(predicted, facing, polys[i + 1], polys[i + k], polys[i + k + 1]);
            fill(stale, sent, polys[i + 1], polys[i + k], polys[i + k + 1]);
          }
        }
      });
      result.glyphs = glyphs;
      // Read back a frame of the labels alone: everything else is hidden for
      // this one render, so atoms, bonds and the grid cannot be mistaken for ink.
      // vtk.js skips an explicit render while its own animation loop runs (a
      // held drag), so draw through the view directly, and read in the same task.
      const draw = () => (
        view.traverseAllPasses ? view.traverseAllPasses() : renderWindow.render()
      );
      hidden.forEach((actor) => actor.setVisibility(false));
      draw();
      const gl = glCanvas.getContext('webgl2') || glCanvas.getContext('webgl');
      const pixels = new Uint8Array(W * H * 4);
      gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
      hidden.forEach((actor) => actor.setVisibility(true));
      draw();
      const background = renderer.getBackground().map((v) => v * 255);
      const lit = new Uint8Array(W * H);
      let litCount = 0;
      for (let i = 0; i < W * H; i++) {
        const distance = Math.abs(pixels[4 * i] - background[0]) +
          Math.abs(pixels[4 * i + 1] - background[1]) + Math.abs(pixels[4 * i + 2] - background[2]);
        if (distance > 192) { lit[i] = 1; litCount++; }
      }
      // One pixel of tolerance for anti-aliased edges.
      function near(mask, i) {
        const x = i % W, y = (i - x) / W;
        for (let dy = -1; dy <= 1; dy++) {
          const yy = y + dy;
          if (yy < 0 || yy >= H) continue;
          for (let dx = -1; dx <= 1; dx++) {
            const xx = x + dx;
            if (xx >= 0 && xx < W && mask[yy * W + xx]) return true;
          }
        }
        return false;
      }
      function agreement(mask) {
        let count = 0, litNear = 0, maskNear = 0;
        for (let i = 0; i < W * H; i++) {
          if (mask[i]) { count++; if (near(lit, i)) maskNear++; }
          if (lit[i] && near(mask, i)) litNear++;
        }
        return {
          pixels: count,
          precision: litCount ? litNear / litCount : 0,
          recall: count ? maskNear / count : 0,
        };
      }
      let both = 0, either = 0;
      for (let i = 0; i < W * H; i++) {
        if (predicted[i] && stale[i]) both++;
        if (predicted[i] || stale[i]) either++;
      }
      result.frame = {
        width: W,
        height: H,
        lit: litCount,
        facing: agreement(predicted),
        sent: agreement(stale),
        facingSentIoU: either ? both / either : 0,
      };
      return result;
    })()`;
    async function followerState(action) {
      return evaluateFollower(followerExpression, action);
    }
    async function waitForFollower(check, label, timeout = 20000) {
      const deadline = Date.now() + timeout;
      let last = null;
      while (Date.now() < deadline) {
        last = await followerState(label);
        recordProbeState(label, last);
        if (check(last)) return last;
        await delay(100);
      }
      throw new Error(`${label}: ${JSON.stringify(last)}`);
    }
    const loaded = await waitForFollower(
      (state) => state.readyState === "complete" && state.scriptInstalled && state.canvas,
      "label follower never reached the served page",
    );
    // A hover over the canvas hands the follower its renderer.
    const cx = loaded.canvas.x + loaded.canvas.width / 2;
    const cy = loaded.canvas.y + loaded.canvas.height / 2;
    await inputCommand("Input.dispatchMouseEvent", { type: "mouseMoved", x: cx, y: cy });
    await delay(100);
    await inputCommand("Input.dispatchMouseEvent", { type: "mouseMoved", x: cx + 1, y: cy });
    const initial = await waitForFollower(
      (state) => state.rendererKnown && state.stats.configured >= 1 &&
        state.frame && state.frame.lit > 0,
      "label meshes never reached the vtk.js shader",
    );
    recordProbeState("trusted drag on the viewport", initial);
    await inputCommand("Input.dispatchMouseEvent", {
      type: "mousePressed",
      x: cx + 1,
      y: cy,
      button: "left",
      clickCount: 1,
    });
    let x = cx + 1, y = cy;
    for (let step = 0; step < 12; step++) {
      x += 14; y += 9;
      await inputCommand("Input.dispatchMouseEvent", {
        type: "mouseMoved",
        x,
        y,
        button: "left",
        buttons: 1,
      });
      await delay(30);
    }
    await delay(200);
    const dragging = await followerState("read labels while the drag is held");
    await inputCommand("Input.dispatchMouseEvent", {
      type: "mouseReleased",
      x,
      y,
      button: "left",
      buttons: 0,
      clickCount: 1,
    });
    // The release ends the animation; the server then syncs its own camera.
    await delay(1500);
    const released = await followerState("read labels after the release");
    const direction = (state) => {
      const d = state.camera.position.map((v, i) => v - state.camera.focalPoint[i]);
      const norm = Math.hypot(...d);
      return d.map((v) => v / norm);
    };
    const before = direction(initial), after = direction(dragging);
    const cosine = before[0] * after[0] + before[1] * after[1] + before[2] * after[2];
    socket.close();
    finish(0, {
      labelFollowers: {
        initial,
        dragging,
        released,
        cameraTurnDeg: Math.acos(Math.max(-1, Math.min(1, cosine))) * 180 / Math.PI,
      },
    });
    return;
  }
  if (playbackScenario) {
    const isTrajectory = playbackScenario === "trajectory";
    const isProgress = playbackScenario === "molecular-orbital-progress";
    if (!isTrajectory && !isProgress && playbackScenario !== "molecular-orbital") {
      throw new Error(`unknown playback scenario: ${playbackScenario}`);
    }
    const previousLabel = isTrajectory
      ? "Previous trajectory frame"
      : "Previous molecular orbital";
    const startLabel = isTrajectory
      ? "Start trajectory animation"
      : "Start molecular orbital animation";
    const pauseLabel = isTrajectory
      ? "Pause trajectory animation"
      : "Pause molecular orbital animation";
    const nextLabel = isTrajectory
      ? "Next trajectory frame"
      : "Next molecular orbital";
    const initialLabels = [previousLabel, startLabel, nextLabel];
    const allPlaybackLabels = [
      "Previous trajectory frame",
      "Start trajectory animation",
      "Pause trajectory animation",
      "Next trajectory frame",
      "Previous molecular orbital",
      "Start molecular orbital animation",
      "Pause molecular orbital animation",
      "Next molecular orbital",
    ];
    const playbackExpression = `JSON.stringify((() => {
      const trameState = window.trame && window.trame.state;
      const knownLabels = ${JSON.stringify(allPlaybackLabels)};
      const buttons = Array.from(document.querySelectorAll('button'))
        .filter((button) => knownLabels.includes(button.getAttribute('aria-label')))
        .map((button) => ({
          tagName: button.tagName,
          role: button.getAttribute('role') ||
            (button.tagName === 'BUTTON' ? 'button' : ''),
          ariaLabel: button.getAttribute('aria-label'),
          title: button.getAttribute('title'),
          tabIndex: button.tabIndex,
          visibleText: (button.innerText || '').trim(),
          iconAriaHidden: Array.from(button.querySelectorAll('.v-icon')).every(
            (icon) => icon.getAttribute('aria-hidden') === 'true'
          ),
        }));
      return {
        readyState: document.readyState,
        selectedSection: trameState
          ? String(trameState.get('selected_section') || '')
          : '',
        trajectoryFrames: trameState
          ? Number(trameState.get('trajectory_n_frames') || 0)
          : 0,
        wavefunctionSection: trameState
          ? String(trameState.get('wf_section_id') || '')
          : '',
        wavefunctionRows: trameState
          ? (trameState.get('wf_mo_rows') || []).length
          : 0,
        selectedMO: trameState ? trameState.get('wf_selected_mo') : null,
        lastMO: trameState ? trameState.get('mo_last_index') : null,
        surfaceKind: trameState ? trameState.get('wf_surface_kind') : null,
        moVisible: trameState ? Boolean(trameState.get('mo_visible')) : false,
        statusMessage: trameState ? trameState.get('status_message') : '',
        animating: trameState
          ? Boolean(trameState.get(
              ${JSON.stringify(isTrajectory ? "trajectory_playing" : "wf_animating")}
            ))
          : null,
        buttons,
      };
    })())`;

    async function playbackState() {
      const evaluation = await command("Runtime.evaluate", {
        expression: playbackExpression,
        returnByValue: true,
      });
      return JSON.parse(evaluation.result.value);
    }

    async function waitForPlayback(check, label, timeout = 15000) {
      const deadline = Date.now() + timeout;
      let last = null;
      recordProbeState(label);
      while (Date.now() < deadline) {
        last = await playbackState();
        recordProbeState(label, last);
        if (check(last)) return last;
        await delay(40);
      }
      throw new Error(`${label}: ${JSON.stringify(last)}`);
    }

    async function clickPlaybackButton(label) {
      recordProbeState(`click playback control: ${label}`);
      const selector = `button[aria-label="${label}"]`;
      await command("Runtime.evaluate", {
        expression: `(() => {
          const button = document.querySelector(${JSON.stringify(selector)});
          if (!button) return false;
          button.scrollIntoView({ block: 'center', inline: 'nearest' });
          return true;
        })()`,
        returnByValue: true,
      });
      await delay(150);
      const target = await command("Runtime.evaluate", {
        expression: `(() => {
          const button = document.querySelector(${JSON.stringify(selector)});
          if (!button) return null;
          const rect = button.getBoundingClientRect();
          const x = rect.left + rect.width / 2;
          const y = rect.top + rect.height / 2;
          const hit = document.elementFromPoint(x, y);
          return {
            x,
            y,
            visible: rect.width > 0 && rect.height > 0,
            receivesPointer: Boolean(hit && (hit === button || button.contains(hit))),
            hitTag: hit ? hit.tagName : null,
            hitClass: hit ? hit.className : null,
          };
        })()`,
        returnByValue: true,
      });
      const point = target.result.value;
      if (!point || !point.visible || !point.receivesPointer) {
        throw new Error(
          `playback button is not physically clickable: ${label}: ${JSON.stringify(point)}`
        );
      }
      await inputCommand("Input.dispatchMouseEvent", {
        type: "mouseMoved",
        x: point.x,
        y: point.y,
      });
      await inputCommand("Input.dispatchMouseEvent", {
        type: "mousePressed",
        x: point.x,
        y: point.y,
        button: "left",
        clickCount: 1,
      });
      await inputCommand("Input.dispatchMouseEvent", {
        type: "mouseReleased",
        x: point.x,
        y: point.y,
        button: "left",
        clickCount: 1,
      });
    }

    const initial = await waitForPlayback(
      (state) => state.readyState === "complete" && !state.animating &&
        (isTrajectory
          ? state.trajectoryFrames > 0
          : state.wavefunctionSection && state.wavefunctionRows > 0) &&
        state.buttons.length === 3 &&
        initialLabels.every(
          (label) => state.buttons.some((button) => button.ariaLabel === label)
        ),
      `${playbackScenario} controls never reached the served DOM`,
    );
    async function canvasSnapshot(name) {
      // Capture only the VTK canvas: picker/status changes cannot satisfy this
      // geometry assertion, and CDP reads do not send a Trame state update.
      const evaluation = await command("Runtime.evaluate", {
        expression: `(() => {
          const canvas = Array.from(document.querySelectorAll('canvas'))
            .sort((a, b) => b.width * b.height - a.width * a.height)[0];
          if (!canvas) return null;
          const r = canvas.getBoundingClientRect();
          return { x: r.x, y: r.y, width: r.width, height: r.height, scale: 1 };
        })()`,
        returnByValue: true,
      });
      const clip = evaluation.result.value;
      if (!clip || clip.width < 100 || clip.height < 100) {
        throw new Error(`missing molecular canvas: ${JSON.stringify(clip)}`);
      }
      const screenshot = await command("Page.captureScreenshot", {
        format: "png", clip,
      });
      writeFileSync(`${profile}/${name}.png`, Buffer.from(screenshot.data, "base64"));
      return createHash("sha256").update(screenshot.data).digest("hex");
    }
    // DOM controls can be ready before vtk.js consumes the initial scene.
    if (isProgress) await delay(1000);
    const initialCanvas = isProgress ? await canvasSnapshot("initial-orbital") : null;
    await clickPlaybackButton(startLabel);
    const playing = await waitForPlayback(
      (state) => state.animating && state.buttons.length === 3 &&
        state.buttons.some((button) => button.ariaLabel === pauseLabel) &&
        !state.buttons.some((button) => button.ariaLabel === startLabel),
      `${playbackScenario} control did not expose its Pause name`,
    );
    if (isProgress) {
      const observed = [playing];
      const finished = await waitForPlayback(
        (state) => {
          observed.push(state);
          return !state.animating && state.statusMessage === "MO animation finished";
        },
        "MO playback did not advance and terminate without further input",
      );
      // Let the last vtk.js render settle, using no mouse/key/state mutation.
      await delay(250);
      const finalCanvas = await canvasSnapshot("final-orbital");
      await delay(1000);
      const settled = await playbackState();
      socket.close();
      finish(0, { playback: {
        scenario: playbackScenario, initial, playing, observed, finished, settled,
        initialCanvas, finalCanvas,
      } });
      return;
    }
    await clickPlaybackButton(pauseLabel);
    const paused = await waitForPlayback(
      (state) => !state.animating && state.buttons.length === 3 &&
        state.buttons.some((button) => button.ariaLabel === startLabel) &&
        !state.buttons.some((button) => button.ariaLabel === pauseLabel),
      `${playbackScenario} control did not restore its Start name`,
    );
    socket.close();
    finish(0, { playback: { scenario: playbackScenario, initial, playing, paused } });
    return;
  }
  if (sliderScenario) {
    const sliderExpression = `JSON.stringify((() => {
      const trameState = window.trame && window.trame.state;
      const position = document.querySelector('#vv-clip-position');
      const separators = Array.from(
        document.querySelectorAll('.vv-panel-separator[role="separator"]')
      ).map((element) => {
        const controlled = document.getElementById(element.getAttribute('aria-controls'));
        const rect = controlled ? controlled.getBoundingClientRect() : null;
        const orientation = element.getAttribute('aria-orientation');
        const numberAttribute = (name) => {
          const value = element.getAttribute(name);
          return value === null ? null : Number(value);
        };
        return {
          id: element.id,
          role: element.getAttribute('role'),
          ariaLabel: element.getAttribute('aria-label'),
          ariaOrientation: orientation,
          ariaControls: element.getAttribute('aria-controls'),
          ariaValueMin: numberAttribute('aria-valuemin'),
          ariaValueMax: numberAttribute('aria-valuemax'),
          ariaValueNow: numberAttribute('aria-valuenow'),
          ariaValueText: element.getAttribute('aria-valuetext'),
          tabIndex: element.tabIndex,
          size: rect
            ? Math.round(orientation === 'vertical' ? rect.width : rect.height)
            : null,
        };
      }).sort((left, right) => left.id.localeCompare(right.id));
      return {
        readyState: document.readyState,
        selectedSection: trameState
          ? String(trameState.get('selected_section') || '')
          : '',
        activeVolume: trameState
          ? String(trameState.get('active_volume_id') || '')
          : '',
        clipEnabled: trameState
          ? Boolean(trameState.get('clip_enabled'))
          : null,
        showSlice: trameState
          ? Boolean(trameState.get('show_slice'))
          : null,
        clipX: trameState ? Number(trameState.get('clip_x')) : null,
        positionMessage: position ? position.textContent.trim() : '',
        positionRole: position ? position.getAttribute('role') : null,
        positionLive: position ? position.getAttribute('aria-live') : null,
        liveMessages: Array.from(document.querySelectorAll('[role="status"]')).map(
          (element) => element.textContent.trim()
        ),
        sliders: Array.from(document.querySelectorAll('[role="slider"]')).map(
          (element) => ({
            role: element.getAttribute('role'),
            ariaLabel: element.getAttribute('aria-label'),
            ariaValueNow: element.getAttribute('aria-valuenow'),
            tabIndex: element.tabIndex,
          })
        ),
        separators,
        separatorIds: separators.map((separator) => separator.id),
        uniqueSeparatorIds: new Set(
          separators.map((separator) => separator.id)
        ).size,
        sideState: {
          left: trameState ? Number(trameState.get('left_panel_width')) : null,
          right: trameState ? Number(trameState.get('right_panel_width')) : null,
        },
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
      };
    })())`;
    const deadline = Date.now() + 12000;
    let state = {
      readyState: "unknown",
      activeVolume: "",
      clipEnabled: null,
      showSlice: null,
      clipX: null,
      positionMessage: "",
      positionRole: null,
      positionLive: null,
      liveMessages: [],
      sliders: [],
      separators: [],
      separatorIds: [],
      uniqueSeparatorIds: 0,
      sideState: { left: null, right: null },
      viewportWidth: 0,
      viewportHeight: 0,
    };
    async function evaluateValue(expression) {
      const evaluation = await command("Runtime.evaluate", {
        expression,
        returnByValue: true,
      });
      if (evaluation.exceptionDetails) {
        throw new Error(
          evaluation.exceptionDetails.exception?.description ||
          evaluation.exceptionDetails.text ||
          "slider probe evaluation failed"
        );
      }
      return evaluation.result ? evaluation.result.value : undefined;
    }
    async function readSliderState() {
      return JSON.parse(await evaluateValue(sliderExpression));
    }
    async function waitForSlider(check, label) {
      const deadline = Date.now() + 12000;
      let last = state;
      recordProbeState(label, last);
      while (Date.now() < deadline) {
        last = await readSliderState();
        recordProbeState(label, last);
        if (check(last)) return last;
        await delay(100);
      }
      throw new Error(`${label}: ${JSON.stringify(last)}`);
    }
    function separator(state, id) {
      return state.separators.find((candidate) => candidate.id === id);
    }
    async function focusSeparator(id) {
      recordProbeState(`focus separator: ${id}`);
      const focused = await evaluateValue(`(() => {
        const handle = document.getElementById(${JSON.stringify(id)});
        if (!handle) return false;
        handle.focus();
        return document.activeElement === handle;
      })()`);
      if (!focused) throw new Error(`separator was not focusable: ${id}`);
    }
    async function pressKey(key, shift = false) {
      recordProbeState(`press ${shift ? "Shift+" : ""}${key} in slider profile`);
      const virtualKeyCodes = {
        ArrowLeft: 37,
        ArrowUp: 38,
        ArrowRight: 39,
        ArrowDown: 40,
        Home: 36,
        End: 35,
        Enter: 13,
      };
      const keyEvent = {
        key,
        code: key,
        modifiers: shift ? 8 : 0,
        windowsVirtualKeyCode: virtualKeyCodes[key],
        nativeVirtualKeyCode: virtualKeyCodes[key],
      };
      await inputCommand("Input.dispatchKeyEvent", keyDown(keyEvent));
      await inputCommand("Input.dispatchKeyEvent", { type: "keyUp", ...keyEvent });
    }
    async function dragSeparator(id, deltaX, deltaY) {
      recordProbeState(`drag separator: ${id}`);
      const point = await evaluateValue(`(() => {
        const handle = document.getElementById(${JSON.stringify(id)});
        if (!handle) return null;
        handle.scrollIntoView({ block: 'center', inline: 'nearest' });
        const rect = handle.getBoundingClientRect();
        const x = rect.left + rect.width / 2;
        const y = rect.top + rect.height / 2;
        const hit = document.elementFromPoint(x, y);
        return {
          x,
          y,
          visible: rect.width > 0 && rect.height > 0,
          receivesPointer: Boolean(hit && (hit === handle || handle.contains(hit))),
        };
      })()`);
      if (!point || !point.visible || !point.receivesPointer) {
        throw new Error(`separator is not physically draggable: ${id}: ${JSON.stringify(point)}`);
      }
      await inputCommand("Input.dispatchMouseEvent", {
        type: "mouseMoved",
        x: point.x,
        y: point.y,
      });
      await inputCommand("Input.dispatchMouseEvent", {
        type: "mousePressed",
        x: point.x,
        y: point.y,
        button: "left",
        clickCount: 1,
      });
      await inputCommand("Input.dispatchMouseEvent", {
        type: "mouseMoved",
        x: point.x + deltaX,
        y: point.y + deltaY,
        button: "left",
        buttons: 1,
      });
      await inputCommand("Input.dispatchMouseEvent", {
        type: "mouseReleased",
        x: point.x + deltaX,
        y: point.y + deltaY,
        button: "left",
        buttons: 0,
        clickCount: 1,
      });
    }
    async function setTrameState(key, value) {
      recordProbeState(`set Trame state: ${key}`);
      const changed = await evaluateValue(`(() => {
        const trameState = window.trame && window.trame.state;
        if (!trameState) return false;
        trameState.set(${JSON.stringify(key)}, ${JSON.stringify(value)});
        trameState.flush();
        return true;
      })()`);
      if (!changed) throw new Error(`could not set trame state: ${key}`);
    }
    while (Date.now() < deadline) {
      state = await readSliderState();
      recordProbeState("wait for sliders and separators", state);
      if (
        state.readyState === "complete" &&
        state.activeVolume === "density" &&
        state.clipEnabled === true &&
        state.showSlice === true &&
        state.positionMessage === "2D slice at x=0.50" &&
        state.sliders.length === expectedCount &&
        state.separatorIds.join(',') ===
          'vv-hsplit,vv-left-panel-separator,vv-right-panel-separator' &&
        state.uniqueSeparatorIds === 3
      ) {
        const initial = state;
        const focused = await evaluateValue(`(() => {
            const slider = document.querySelector(
              '[role="slider"][aria-label="Clip plane X position"]'
            );
            if (!slider) return false;
            slider.focus();
            return document.activeElement === slider;
          })()`);
        if (!focused) throw new Error("X clip slider was not focusable");
        const errorsBefore = javascriptErrors.length;
        await pressKey("ArrowRight");
        const changed = await waitForSlider(
          (next) => next.clipX === 0.51 &&
            next.positionMessage === "2D slice at x=0.51",
          "keyboard clip change never reached the server-rendered slice",
        );

        const initialLeft = separator(initial, 'vv-left-panel-separator');
        await focusSeparator('vv-left-panel-separator');
        await pressKey('ArrowRight');
        const leftArrow = await waitForSlider(
          (next) => separator(next, 'vv-left-panel-separator')?.size ===
              initialLeft.size + 10 &&
            next.sideState.left === initialLeft.size + 10,
          "left separator ArrowRight did not grow by 10 pixels",
        );
        await pressKey('ArrowRight', true);
        const leftShiftArrow = await waitForSlider(
          (next) => separator(next, 'vv-left-panel-separator')?.size ===
              initialLeft.size + 60 &&
            next.sideState.left === initialLeft.size + 60,
          "left separator Shift+ArrowRight did not grow by 50 pixels",
        );
        await pressKey('Home');
        const leftHome = await waitForSlider(
          (next) => separator(next, 'vv-left-panel-separator')?.size === 180 &&
            next.sideState.left === 180,
          "left separator Home did not reach its minimum",
        );
        await pressKey('Enter');
        const leftHomeRestored = await waitForSlider(
          (next) => separator(next, 'vv-left-panel-separator')?.size ===
              initialLeft.size + 60 &&
            next.sideState.left === initialLeft.size + 60,
          "left separator Enter did not restore its pre-Home width",
        );
        await pressKey('End');
        const leftEnd = await waitForSlider(
          (next) => separator(next, 'vv-left-panel-separator')?.size === 640 &&
            next.sideState.left === 640,
          "left separator End did not reach its maximum",
        );
        await pressKey('Enter');
        const leftCollapsed = await waitForSlider(
          (next) => separator(next, 'vv-left-panel-separator')?.size === 180 &&
            next.sideState.left === 180,
          "left separator Enter did not collapse",
        );
        await pressKey('Enter');
        const leftMaxRestored = await waitForSlider(
          (next) => separator(next, 'vv-left-panel-separator')?.size === 640 &&
            next.sideState.left === 640,
          "left separator Enter did not restore the maximum width",
        );
        await pressKey('ArrowLeft');
        const leftBeforeDrag = await waitForSlider(
          (next) => separator(next, 'vv-left-panel-separator')?.size === 630 &&
            next.sideState.left === 630,
          "left separator ArrowLeft did not shrink by 10 pixels",
        );
        await dragSeparator('vv-left-panel-separator', -20, 0);
        const leftDragged = await waitForSlider(
          (next) => separator(next, 'vv-left-panel-separator')?.size === 610 &&
            next.sideState.left === 610,
          "left separator mouse drag did not commit its width",
        );

        const initialRight = separator(initial, 'vv-right-panel-separator');
        await focusSeparator('vv-right-panel-separator');
        await pressKey('ArrowLeft');
        const rightGrown = await waitForSlider(
          (next) => separator(next, 'vv-right-panel-separator')?.size ===
              initialRight.size + 10 &&
            next.sideState.right === initialRight.size + 10,
          "right separator ArrowLeft did not grow by 10 pixels",
        );

        await evaluateValue(`(() => {
          const panel = document.getElementById('vv-bottom-panel');
          if (!panel) return false;
          panel.style.height = '123px';
          return true;
        })()`);
        const bottomObserved = await waitForSlider(
          (next) => separator(next, 'vv-hsplit')?.size === 123 &&
            separator(next, 'vv-hsplit')?.ariaValueNow === 123,
          "bottom ResizeObserver did not synchronize an external height change",
        );
        const resizedViewportHeight = initial.viewportHeight + 120;
        await command('Emulation.setDeviceMetricsOverride', {
          width: initial.viewportWidth,
          height: resizedViewportHeight,
          deviceScaleFactor: 1,
          mobile: false,
        });
        const bottomViewportResized = await waitForSlider(
          (next) => separator(next, 'vv-hsplit')?.size === 123 &&
            next.viewportHeight === resizedViewportHeight &&
            separator(next, 'vv-hsplit')?.ariaValueMax ===
              Math.floor(resizedViewportHeight * 0.85),
          "bottom separator maximum did not follow the viewport height",
        );
        await focusSeparator('vv-hsplit');
        await pressKey('ArrowUp');
        const bottomArrow = await waitForSlider(
          (next) => separator(next, 'vv-hsplit')?.size === 133,
          "bottom separator ArrowUp did not grow by 10 pixels",
        );
        await pressKey('ArrowDown', true);
        const bottomShiftArrow = await waitForSlider(
          (next) => separator(next, 'vv-hsplit')?.size === 83,
          "bottom separator Shift+ArrowDown did not shrink by 50 pixels",
        );
        await pressKey('Home');
        const bottomHome = await waitForSlider(
          (next) => separator(next, 'vv-hsplit')?.size === 80,
          "bottom separator Home did not reach its minimum",
        );
        await pressKey('Enter');
        const bottomHomeRestored = await waitForSlider(
          (next) => separator(next, 'vv-hsplit')?.size === 83,
          "bottom separator Enter did not restore its pre-Home height",
        );
        await pressKey('End');
        const bottomEnd = await waitForSlider(
          (next) => {
            const handle = separator(next, 'vv-hsplit');
            return handle && handle.size === handle.ariaValueMax;
          },
          "bottom separator End did not reach its dynamic maximum",
        );
        const bottomMaximum = separator(bottomEnd, 'vv-hsplit').ariaValueMax;
        await pressKey('Enter');
        const bottomCollapsed = await waitForSlider(
          (next) => separator(next, 'vv-hsplit')?.size === 80,
          "bottom separator Enter did not collapse",
        );
        await pressKey('Enter');
        const bottomMaxRestored = await waitForSlider(
          (next) => separator(next, 'vv-hsplit')?.size === bottomMaximum,
          "bottom separator Enter did not restore its maximum height",
        );

        await setTrameState('selected_section', '');
        const unmounted = await waitForSlider(
          (next) => next.selectedSection === '' &&
            next.separatorIds.join(',') === 'vv-left-panel-separator' &&
            next.uniqueSeparatorIds === 1,
          "conditional panels did not unmount without leaving resize handles",
        );
        await setTrameState('selected_section', 'density');
        const remounted = await waitForSlider(
          (next) => next.selectedSection === 'density' &&
            next.separatorIds.join(',') ===
              'vv-hsplit,vv-left-panel-separator,vv-right-panel-separator' &&
            next.uniqueSeparatorIds === 3 &&
            separator(next, 'vv-right-panel-separator')?.size ===
              initialRight.size + 10 &&
            next.sideState.right === initialRight.size + 10,
          "conditional panel resize handles did not reattach exactly once",
        );
        await focusSeparator('vv-right-panel-separator');
        await pressKey('ArrowRight');
        const remountChanged = await waitForSlider(
          (next) => separator(next, 'vv-right-panel-separator')?.size ===
              initialRight.size &&
            next.sideState.right === initialRight.size,
          "remounted right separator did not retain keyboard behavior",
        );
        await delay(300);
        const retained = await readSliderState();
        socket.close();
        finish(0, {
          sliders: {
            initial,
            changed,
            retained,
            separators: {
              leftArrow,
              leftShiftArrow,
              leftHome,
              leftHomeRestored,
              leftEnd,
              leftCollapsed,
              leftMaxRestored,
              leftBeforeDrag,
              leftDragged,
              rightGrown,
              bottomObserved,
              bottomViewportResized,
              bottomArrow,
              bottomShiftArrow,
              bottomHome,
              bottomHomeRestored,
              bottomEnd,
              bottomCollapsed,
              bottomMaxRestored,
              unmounted,
              remounted,
              remountChanged,
            },
            javascriptErrors: javascriptErrors.slice(errorsBefore),
          },
        });
        return;
      }
      await delay(100);
    }
    socket.close();
    finish(3, {
      error: "expected sliders never reached the served DOM",
      state,
      browserStderr,
    });
    return;
  }
  if (presentationScenario) {
    const presentationExpression = `JSON.stringify((() => {
      const trameState = window.trame && window.trame.state;
      const overlay = document.querySelector(
        '[role="region"][aria-label="Presentation mode"]'
      );
      const rect = overlay ? overlay.getBoundingClientRect() : null;
      const style = overlay ? getComputedStyle(overlay) : null;
      const overlayContent = overlay
        ? (overlay.matches('.v-overlay__content')
            ? overlay
            : overlay.querySelector('.v-overlay__content'))
        : null;
      const overlaySurface = overlayContent
        ? overlayContent.firstElementChild
        : null;
      const surfaceRect = overlaySurface
        ? overlaySurface.getBoundingClientRect()
        : null;
      const centerElement = overlay
        ? document.elementFromPoint(window.innerWidth / 2, window.innerHeight / 2)
        : null;
      return {
        readyState: document.readyState,
        keyboardBound: window._vibe_keyboard_bound === 1,
        bookmarkCount: trameState
          ? (trameState.get('user_bookmarks') || []).length
          : 0,
        presentationMode: trameState
          ? Boolean(trameState.get('presentation_mode'))
          : null,
        presentationSlide: trameState
          ? Number(trameState.get('presentation_slide'))
          : null,
        presentationAuto: trameState
          ? Boolean(trameState.get('presentation_auto_advance'))
          : null,
        editMode: trameState ? Boolean(trameState.get('edit_mode')) : null,
        statusMessage: trameState ? String(trameState.get('status_message') || '') : '',
        // A hidden page gets no animation frames, so overlays cannot finish
        // their transitions; name that instead of a stuck overlay (#83).
        visibility: document.visibilityState,
        overlayVisible: Boolean(
          overlay && rect && rect.width > 0 && rect.height > 0 &&
          style.display !== 'none' && style.visibility !== 'hidden'
        ),
        overlayViewport: Boolean(
          surfaceRect &&
          surfaceRect.width >= document.documentElement.clientWidth - 2 &&
          surfaceRect.height >= document.documentElement.clientHeight - 2
        ),
        overlayPointerEvents: overlayContent
          ? getComputedStyle(overlayContent).pointerEvents
          : null,
        centerBlockedByOverlay: Boolean(
          overlayContent && centerElement && overlayContent.contains(centerElement)
        ),
        overlayText: overlay ? overlay.innerText : '',
        overlayButtons: overlay
          ? Array.from(overlay.querySelectorAll('button[aria-label]')).map(
              (button) => button.getAttribute('aria-label')
            )
          : [],
        appBarVisible: Boolean(document.querySelector('.v-app-bar')),
        leftDrawerVisible: Boolean(document.querySelector('#vv-left-panel')),
        rightDrawerVisible: Boolean(document.querySelector('#vv-right-panel')),
        bottomPanelVisible: Boolean(document.querySelector('#vv-bottom-panel')),
      };
    })())`;

    async function presentationState(action = "read presentation state") {
      recordProbeState(action);
      const evaluation = await command("Runtime.evaluate", {
        expression: presentationExpression,
        returnByValue: true,
      });
      const state = JSON.parse(evaluation.result.value);
      recordProbeState(action, state);
      return state;
    }

    async function waitFor(check, label, timeout = 12000) {
      const deadline = Date.now() + timeout;
      let last = null;
      recordProbeState(label);
      while (Date.now() < deadline) {
        last = await presentationState(label);
        if (check(last)) return last;
        await delay(40);
      }
      throw new Error(`${label}: ${JSON.stringify(last)}`);
    }

    async function pressKey(key, code, virtualKeyCode, modifiers = 0) {
      recordProbeState(`press ${key} in presentation profile`);
      const params = {
        key,
        code,
        modifiers,
        windowsVirtualKeyCode: virtualKeyCode,
        nativeVirtualKeyCode: virtualKeyCode,
      };
      await inputCommand("Input.dispatchKeyEvent", keyDown(params));
      await inputCommand("Input.dispatchKeyEvent", { type: "keyUp", ...params });
    }

    async function clickNamed(label) {
      recordProbeState(`click presentation control: ${label}`);
      const target = await command("Runtime.evaluate", {
        expression: `(() => {
          const button = document.querySelector(
            'button[aria-label="${label}"]'
          );
          if (!button) return null;
          const rect = button.getBoundingClientRect();
          const x = rect.left + rect.width / 2;
          const y = rect.top + rect.height / 2;
          const hit = document.elementFromPoint(x, y);
          return {
            x,
            y,
            receivesPointer: Boolean(hit && (hit === button || button.contains(hit))),
          };
        })()`,
        returnByValue: true,
      });
      if (!target.result.value) throw new Error(`button not found: ${label}`);
      if (!target.result.value.receivesPointer) {
        throw new Error(`button does not receive pointer events: ${label}`);
      }
      const { x, y } = target.result.value;
      await inputCommand("Input.dispatchMouseEvent", {
        type: "mouseMoved",
        x,
        y,
      });
      await inputCommand("Input.dispatchMouseEvent", {
        type: "mousePressed",
        x,
        y,
        button: "left",
        clickCount: 1,
      });
      await inputCommand("Input.dispatchMouseEvent", {
        type: "mouseReleased",
        x,
        y,
        button: "left",
        clickCount: 1,
      });
    }

    const ready = await waitFor(
      (state) => state.readyState === "complete" && state.keyboardBound &&
        state.bookmarkCount === 2,
      "presentation viewer never became ready",
    );
    recordProbeState("focus presentation launcher", ready);
    const focused = await command("Runtime.evaluate", {
      expression: `(() => {
        const button = Array.from(document.querySelectorAll('button')).find(
          (item) => (item.getAttribute('title') || '').startsWith('Presentation mode')
        );
        if (!button) return false;
        button.focus();
        return document.activeElement === button;
      })()`,
      returnByValue: true,
    });
    if (!focused.result.value) {
      throw new Error(`presentation launcher was not focusable: ${JSON.stringify(ready)}`);
    }

    await pressKey("p", "KeyP", 80);
    const entered = await waitFor(
      (state) => state.presentationMode && state.presentationSlide === 0 &&
        state.overlayVisible && state.overlayText.includes("Structure slide one"),
      "trusted P did not enter a visible presentation",
    );

    await pressKey("e", "KeyE", 69);
    await pressKey("z", "KeyZ", 90, 2);
    await pressKey("Delete", "Delete", 46);
    await delay(300);
    const editKeysSuppressed = await presentationState(
      "read presentation after suppressed edit keys"
    );

    await pressKey("ArrowRight", "ArrowRight", 39);
    const afterRight = await waitFor(
      (state) => state.presentationSlide === 1 &&
        state.overlayText.includes("Structure slide two"),
      "trusted ArrowRight did not advance",
    );
    await pressKey("ArrowLeft", "ArrowLeft", 37);
    const afterLeft = await waitFor(
      (state) => state.presentationSlide === 0 &&
        state.overlayText.includes("Structure slide one"),
      "trusted ArrowLeft did not go back",
    );

    await clickNamed("Start automatic slide advance");
    await waitFor(
      (state) => state.presentationAuto &&
        state.overlayButtons.includes("Pause automatic slide advance"),
      "automatic slide advance did not start",
    );
    const autoAdvanced = await waitFor(
      (state) => state.presentationSlide === 1,
      "automatic slide advance did not move",
    );
    await clickNamed("Pause automatic slide advance");
    const autoPaused = await waitFor(
      (state) => !state.presentationAuto,
      "automatic slide advance did not pause",
    );
    await delay(450);
    const autoRetained = await presentationState(
      "read presentation after pausing automatic advance"
    );

    await pressKey("Escape", "Escape", 27);
    const exited = await waitFor(
      (state) => !state.presentationMode && !state.overlayVisible && !state.editMode,
      "trusted Escape did not exit presentation cleanly",
    );
    await pressKey("Escape", "Escape", 27);
    await delay(300);
    const idleEscape = await presentationState("read presentation after idle Escape");

    socket.close();
    finish(0, {
      presentation: {
        entered,
        editKeysSuppressed,
        afterRight,
        afterLeft,
        autoAdvanced,
        autoPaused,
        autoRetained,
        exited,
        idleEscape,
      },
    });
    return;
  }

  const expression = `JSON.stringify({
    readyState: document.readyState,
    editMode: window.trame && window.trame.state
      ? window.trame.state.get('edit_mode')
      : null,
    hasElementButton: Array.from(document.querySelectorAll('button')).some((el) =>
      el.textContent.includes('New:')
    ),
    dialogs: Array.from(document.querySelectorAll('[role="dialog"]')).map((el) => ({
      role: el.getAttribute('role'),
      ariaModal: el.getAttribute('aria-modal'),
      ariaLabel: el.getAttribute('aria-label'),
    })),
  })`;
  const deadline = Date.now() + 12000;
  let state = { readyState: "unknown", dialogs: [] };
  let pickerRequested = false;
  recordProbeState("wait for named dialogs", state);
  while (Date.now() < deadline) {
    const evaluation = await command("Runtime.evaluate", {
      expression,
      returnByValue: true,
    });
    const value = evaluation.result && evaluation.result.value;
    if (value) {
      state = JSON.parse(value);
      recordProbeState("wait for named dialogs", state);
    }
    if (editMode && !pickerRequested && state.readyState === "complete") {
      const request = await command("Runtime.evaluate", {
        expression: `(() => {
          const button = Array.from(document.querySelectorAll('button')).find((el) =>
            el.textContent.includes('New:')
          );
          if (!button) return false;
          button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
          return true;
        })()`,
        returnByValue: true,
      });
      pickerRequested = Boolean(request.result.value);
    }
    if (state.dialogs.length === expectedCount) {
      await delay(500);
      const retained = await command("Runtime.evaluate", {
        expression,
        returnByValue: true,
      });
      const retainedState = JSON.parse(retained.result.value);
      if (retainedState.dialogs.length === expectedCount) {
        socket.close();
        finish(0, retainedState);
        return;
      }
    }
    await delay(100);
  }
  socket.close();
  finish(3, { error: "expected dialogs never reached the served DOM", state, browserStderr });
}

child.stderr.on("data", (chunk) => {
  browserStderr += chunk.toString();
  const match = browserStderr.match(/DevTools listening on (ws:\/\/\S+)/);
  if (!started && match) {
    started = true;
    inspectPage(match[1]).catch((error) =>
      finish(4, {
        error: String(error),
        focusStage,
        diagnostic: probeDiagnostic,
        browserStderr,
      }),
    );
  }
});
child.on("exit", (code) => {
  if (!finished) finish(5, {
    error: `Chromium exited early with ${code}`,
    focusStage,
    diagnostic: probeDiagnostic,
    browserStderr,
  });
});
"""

    transport_retries: list[dict] = []
    input_response_timeouts: list[dict] = []

    def probe(
        dialog_models: tuple[str, ...],
        profile_name: str,
        *,
        edit_mode=False,
        open_initially=True,
        presentation_scenario=False,
        slider_scenario=False,
        playback_scenario="",
        color_input_scenario=False,
        focus_scenario="",
        cdp_timeout_scenario=False,
        label_follower_scenario=False,
        silhouette_scenario=False,
        browser_screenshot_scenario=False,
        qvf_override: Path | None = None,
        _transport_retry=False,
    ):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        process = multiprocessing.get_context("spawn").Process(
            target=_serve_dialogs_open,
            args=(
                str(qvf_override or qvf_path),
                port,
                dialog_models,
                edit_mode,
                open_initially,
                presentation_scenario,
                slider_scenario,
                playback_scenario,
                bool(focus_scenario),
                label_follower_scenario,
                # The export scenario serves the silhouette scene (#116).
                silhouette_scenario or browser_screenshot_scenario,
            ),
        )
        process.start()
        node_process = None
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.exitcode is not None:
                    pytest.fail(f"served-dialog test server exited with {process.exitcode}")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                pytest.fail("served-dialog test server did not start within 30 seconds")

            profile_dir = tmp_path / (
                f"{profile_name}-transport-retry"
                if _transport_retry
                else profile_name
            )
            command = [
                node,
                "-e",
                browser_probe,
                chrome,
                f"http://127.0.0.1:{port}/",
                str(profile_dir),
                str(6 if slider_scenario else len(dialog_models)),
                str(edit_mode).lower(),
                str(presentation_scenario).lower(),
                str(slider_scenario).lower(),
                playback_scenario,
                str(color_input_scenario).lower(),
                str(focus_scenario).lower(),
                str(cdp_timeout_scenario).lower(),
                str(label_follower_scenario).lower(),
                str(silhouette_scenario).lower(),
                str(browser_screenshot_scenario).lower(),
            ]
            node_started_at = time.monotonic()
            node_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            node_timeout = (
                105 if focus_scenario or slider_scenario or browser_screenshot_scenario else 60
            )
            try:
                stdout, stderr = node_process.communicate(timeout=node_timeout)
            except subprocess.TimeoutExpired as exc:
                partial_stdout = exc.stdout or ""
                partial_stderr = exc.stderr or ""
                if isinstance(partial_stdout, bytes):
                    partial_stdout = partial_stdout.decode(errors="replace")
                if isinstance(partial_stderr, bytes):
                    partial_stderr = partial_stderr.decode(errors="replace")
                pytest.fail(
                    f"{profile_name} Node/Chromium probe exceeded "
                    f"{node_timeout} seconds; partial stdout={partial_stdout!r}; "
                    f"partial stderr={partial_stderr!r}"
                )
            node_elapsed = time.monotonic() - node_started_at
            result = subprocess.CompletedProcess(
                command,
                node_process.returncode,
                stdout,
                stderr,
            )
        finally:
            try:
                if node_process is not None:
                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        os.killpg(node_process.pid, signal.SIGTERM)
                    if node_process.poll() is None:
                        with contextlib.suppress(subprocess.TimeoutExpired):
                            node_process.wait(timeout=5)
                    # The Node parent may have exited before a stubborn Chrome
                    # descendant.  The dedicated process group keeps this
                    # unconditional final cleanup scoped to the probe.
                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        os.killpg(node_process.pid, signal.SIGKILL)
                    if node_process.poll() is None:
                        node_process.wait(timeout=5)
            finally:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=10)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=10)
                if process.is_alive():
                    pytest.fail("served-dialog test server could not be stopped")
                process.close()

        payload = json.loads(result.stdout)
        for timeout_record in payload.get("diagnostic", {}).get(
            "inputResponseTimeouts", []
        ):
            input_response_timeouts.append(
                {"profile": profile_name, **timeout_record}
            )
        if cdp_timeout_scenario:
            return {
                "returncode": result.returncode,
                "elapsedSeconds": node_elapsed,
                "payload": payload,
                "stderr": result.stderr,
            }

        diagnostic = payload.get("diagnostic", {})
        last_command = diagnostic.get("lastCommand") or {}
        transport_timeout = (
            result.returncode != 0
            and str(payload.get("error", "")).startswith(
                "Error: CDP command timed out after"
            )
            and last_command.get("status") == "timed-out"
        )
        if transport_timeout and not _transport_retry:
            transport_retries.append(
                {"profile": profile_name, "diagnostic": diagnostic}
            )
            return probe(
                dialog_models,
                profile_name,
                edit_mode=edit_mode,
                open_initially=open_initially,
                presentation_scenario=presentation_scenario,
                slider_scenario=slider_scenario,
                playback_scenario=playback_scenario,
                color_input_scenario=color_input_scenario,
                focus_scenario=focus_scenario,
                cdp_timeout_scenario=False,
                label_follower_scenario=label_follower_scenario,
                silhouette_scenario=silhouette_scenario,
                browser_screenshot_scenario=browser_screenshot_scenario,
                qvf_override=qvf_override,
                _transport_retry=True,
            )

        assert result.returncode == 0, result.stdout or result.stderr
        if presentation_scenario:
            return payload["presentation"]
        if slider_scenario:
            return payload["sliders"]
        if playback_scenario:
            return payload["playback"]
        if color_input_scenario:
            return payload["colorInput"]
        if focus_scenario:
            return payload["focus"]
        if label_follower_scenario:
            return payload["labelFollowers"]
        if silhouette_scenario:
            return payload["silhouette"]
        if browser_screenshot_scenario:
            return payload["browserScreenshot"]
        return payload["dialogs"]

    qvf_path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
    unconditional_models = tuple(
        model for model in _DIALOG_MODELS_AND_LABELS if model != "element_picker_open"
    )
    probe_failures: dict[str, str] = {}

    def collect_probe(name, dialog_models, profile_name, **kwargs):
        try:
            return probe(dialog_models, profile_name, **kwargs)
        except (Exception, pytest.fail.Exception) as exc:
            probe_failures[name] = str(exc)
            return None

    # Run every independent browser profile before asserting any payload.
    # A transport or product failure in one profile must not hide later
    # accessibility regressions behind the first exception.
    timeout_regression = collect_probe(
        "bounded CDP timeout regression",
        (),
        "chrome-profile-cdp-timeout",
        open_initially=False,
        cdp_timeout_scenario=True,
    )
    dialogs_main = collect_probe(
        "named dialogs",
        unconditional_models,
        "chrome-profile-main",
    )
    dialogs_picker = collect_probe(
        "element picker dialog",
        ("element_picker_open",),
        "chrome-profile-element-picker",
        edit_mode=True,
        open_initially=False,
    )
    focus_core = collect_probe(
        "dialog focus core",
        (),
        "chrome-profile-dialog-focus-core",
        open_initially=False,
        focus_scenario="core",
    )
    focus_disabled_origin = collect_probe(
        "disabled dialog focus origin",
        (),
        "chrome-profile-dialog-focus-disabled-origin",
        open_initially=False,
        focus_scenario="disabled-origin",
    )
    focus_presentation_origin = collect_probe(
        "presentation dialog focus origin",
        (),
        "chrome-profile-dialog-focus-presentation-origin",
        open_initially=False,
        focus_scenario="presentation-origin",
    )
    focus_removed_origin = collect_probe(
        "removed dialog focus origin",
        (),
        "chrome-profile-dialog-focus-removed-origin",
        open_initially=False,
        focus_scenario="removed-origin",
    )
    slider_state = collect_probe(
        "volume sliders and separators",
        (),
        "chrome-profile-volume-sliders",
        open_initially=False,
        slider_scenario=True,
        qvf_override=sample_qvf,
    )
    colour_state = collect_probe(
        "element colour input",
        (),
        "chrome-profile-element-colour-input",
        open_initially=False,
        color_input_scenario=True,
        qvf_override=water_qvf,
    )
    trajectory = collect_probe(
        "trajectory controls",
        (),
        "chrome-profile-trajectory-controls",
        open_initially=False,
        playback_scenario="trajectory",
        qvf_override=water_qvf,
    )
    orbitals = collect_probe(
        "molecular-orbital controls",
        (),
        "chrome-profile-orbital-controls",
        open_initially=False,
        playback_scenario="molecular-orbital",
    )
    orbital_progress = collect_probe(
        "molecular-orbital progression",
        (),
        "chrome-profile-orbital-progress",
        open_initially=False,
        playback_scenario="molecular-orbital-progress",
    )
    presentation = collect_probe(
        "presentation mode",
        (),
        "chrome-profile-presentation",
        open_initially=False,
        presentation_scenario=True,
    )
    label_followers = collect_probe(
        "label followers",
        (),
        "chrome-profile-label-followers",
        open_initially=False,
        label_follower_scenario=True,
    )
    silhouette = collect_probe(
        "silhouette pass",
        (),
        "chrome-profile-silhouette",
        open_initially=False,
        silhouette_scenario=True,
    )
    # The export is also saved beside the archive: give it a copy.
    export_qvf = tmp_path / "browser-screenshot" / qvf_path.name
    export_qvf.parent.mkdir()
    export_qvf.write_bytes(qvf_path.read_bytes())
    browser_screenshot = collect_probe(
        "browser screenshot",
        (),
        "chrome-profile-browser-screenshot",
        open_initially=False,
        browser_screenshot_scenario=True,
        qvf_override=export_qvf,
    )

    if probe_failures:
        details = "\n\n".join(
            f"[{name}]\n{message}" for name, message in probe_failures.items()
        )
        pytest.fail(f"served-DOM browser profiles failed independently:\n\n{details}")

    assert len({retry["profile"] for retry in transport_retries}) == len(
        transport_retries
    )
    for timeout_record in input_response_timeouts:
        assert timeout_record["method"].startswith("Input.")
        assert timeout_record["status"] == "timed-out"
        assert timeout_record["timeoutMs"] == 6000
        assert timeout_record["lastState"] is not None

    timeout_payload = timeout_regression["payload"]
    timeout_diagnostic = timeout_payload["diagnostic"]
    timeout_command = timeout_diagnostic["lastCommand"]
    assert timeout_regression["returncode"] == 4
    assert timeout_regression["elapsedSeconds"] < 15
    assert "CDP command timed out after 250 ms" in timeout_payload["error"]
    assert timeout_diagnostic["profile"] == "chrome-profile-cdp-timeout"
    assert timeout_diagnostic["action"] == (
        "intentional suppressed Runtime.evaluate response"
    )
    assert timeout_diagnostic["lastState"]["marker"] == "before-timeout"
    assert timeout_diagnostic["lastState"]["readyState"] in {
        "loading",
        "interactive",
        "complete",
    }
    assert timeout_diagnostic["pending"] == []
    assert timeout_command["method"] == "Runtime.evaluate"
    assert timeout_command["action"] == (
        "intentional suppressed Runtime.evaluate response"
    )
    assert timeout_command["timeoutMs"] == 250
    assert timeout_command["elapsedMs"] >= 200
    assert timeout_command["status"] == "timed-out"

    dialogs = dialogs_main + dialogs_picker

    assert {dialog["ariaLabel"] for dialog in dialogs} == set(
        _DIALOG_MODELS_AND_LABELS.values()
    )
    assert len(dialogs) == len(_DIALOG_MODELS_AND_LABELS)
    assert all(dialog["role"] == "dialog" for dialog in dialogs)
    assert all(dialog["ariaModal"] == "true" for dialog in dialogs)

    assert focus_core["javascriptErrors"] == []
    assert focus_disabled_origin["javascriptErrors"] == []
    assert focus_presentation_origin["javascriptErrors"] == []
    assert focus_removed_origin["javascriptErrors"] == []
    focus = {
        **focus_core,
        **focus_disabled_origin,
        **focus_presentation_origin,
        **focus_removed_origin,
    }
    for path, expected_probe in (
        ("screenshot", "screenshot-launcher"),
        ("menu", "info-launcher"),
        ("nestedClosed", "info-launcher"),
        ("shortcut", "export-launcher"),
    ):
        assert focus[path]["visibleDialogs"] == []
        assert focus[path]["activeProbe"] == expected_probe
        assert focus[path]["activeTag"] == "BUTTON"
        assert focus[path]["activeInsideDialog"] is False
    assert focus["nested"]["visibleDialogs"] == ["Settings"]
    assert focus["nested"]["activeProbe"] == "settings-cancel"
    assert focus["nested"]["activeDialog"] == "Settings"
    assert focus["presentation"]["presentationVisible"] is True
    assert focus["presentation"]["visibleDialogs"] == []
    assert focus["presentation"]["activeProbe"] == "presentation-next"
    for path in ("disabledOrigin", "removedOrigin"):
        assert focus[path]["activeProbe"] is None
        assert focus[path]["activeTag"] == "BODY"
        assert focus[path]["activeInsideDialog"] is False

    initial_slider_state = slider_state["initial"]
    changed_slider_state = slider_state["changed"]
    retained_slider_state = slider_state["retained"]

    assert initial_slider_state["activeVolume"] == "density"
    assert initial_slider_state["clipEnabled"] is True
    assert initial_slider_state["showSlice"] is True
    assert initial_slider_state["clipX"] == 0.5
    assert initial_slider_state["positionMessage"] == "2D slice at x=0.50"
    assert initial_slider_state["positionRole"] is None
    assert initial_slider_state["positionLive"] == "off"
    assert all(
        "2D slice at" not in message
        for message in initial_slider_state["liveMessages"]
    )

    sliders = initial_slider_state["sliders"]
    expected_slider_names = {
        _SLIDER_MODELS_AND_NAMES[model]
        for model in ("mb_angle", "isovalue", "opacity", "clip_x", "clip_y", "clip_z")
    }
    assert {slider["ariaLabel"] for slider in sliders} == expected_slider_names
    assert len(sliders) == len(expected_slider_names)
    assert all(slider["role"] == "slider" for slider in sliders)
    assert all(slider["tabIndex"] == 0 for slider in sliders)

    def panel_separator(snapshot, separator_id):
        return next(
            separator
            for separator in snapshot["separators"]
            if separator["id"] == separator_id
        )

    assert initial_slider_state["separatorIds"] == [
        "vv-hsplit",
        "vv-left-panel-separator",
        "vv-right-panel-separator",
    ]
    assert initial_slider_state["uniqueSeparatorIds"] == 3
    for separator_id, expected in _PANEL_SEPARATOR_CONTRACT.items():
        actual = panel_separator(initial_slider_state, separator_id)
        for field, value in expected.items():
            assert actual[field] == value
        assert actual["role"] == "separator"
        assert actual["tabIndex"] == 0
        assert actual["ariaValueNow"] == actual["size"]
        assert actual["ariaValueText"] == f'{actual["size"]} pixels'

    initial_bottom = panel_separator(initial_slider_state, "vv-hsplit")
    expected_bottom_maximum = max(
        80, int(initial_slider_state["viewportHeight"] * 0.85)
    )
    assert initial_bottom["ariaValueMax"] == expected_bottom_maximum

    separator_states = slider_state["separators"]
    initial_left = panel_separator(
        initial_slider_state, "vv-left-panel-separator"
    )["size"]
    assert panel_separator(
        separator_states["leftArrow"], "vv-left-panel-separator"
    )["size"] == initial_left + 10
    assert separator_states["leftArrow"]["sideState"]["left"] == initial_left + 10
    assert panel_separator(
        separator_states["leftShiftArrow"], "vv-left-panel-separator"
    )["size"] == initial_left + 60
    assert separator_states["leftShiftArrow"]["sideState"]["left"] == (
        initial_left + 60
    )
    assert panel_separator(
        separator_states["leftHome"], "vv-left-panel-separator"
    )["size"] == 180
    assert panel_separator(
        separator_states["leftHomeRestored"], "vv-left-panel-separator"
    )["size"] == initial_left + 60
    assert panel_separator(
        separator_states["leftEnd"], "vv-left-panel-separator"
    )["size"] == 640
    assert panel_separator(
        separator_states["leftCollapsed"], "vv-left-panel-separator"
    )["size"] == 180
    assert panel_separator(
        separator_states["leftMaxRestored"], "vv-left-panel-separator"
    )["size"] == 640
    assert panel_separator(
        separator_states["leftBeforeDrag"], "vv-left-panel-separator"
    )["size"] == 630
    assert panel_separator(
        separator_states["leftDragged"], "vv-left-panel-separator"
    )["size"] == 610
    assert separator_states["leftDragged"]["sideState"]["left"] == 610

    initial_right = panel_separator(
        initial_slider_state, "vv-right-panel-separator"
    )["size"]
    assert panel_separator(
        separator_states["rightGrown"], "vv-right-panel-separator"
    )["size"] == initial_right + 10
    assert separator_states["rightGrown"]["sideState"]["right"] == (
        initial_right + 10
    )

    assert panel_separator(
        separator_states["bottomObserved"], "vv-hsplit"
    )["size"] == 123
    assert panel_separator(
        separator_states["bottomObserved"], "vv-hsplit"
    )["ariaValueNow"] == 123
    bottom_viewport_resized = separator_states["bottomViewportResized"]
    assert bottom_viewport_resized["viewportHeight"] == (
        initial_slider_state["viewportHeight"] + 120
    )
    resized_bottom_maximum = int(bottom_viewport_resized["viewportHeight"] * 0.85)
    resized_bottom = panel_separator(bottom_viewport_resized, "vv-hsplit")
    assert resized_bottom["size"] == 123
    assert resized_bottom["ariaValueMax"] == resized_bottom_maximum
    assert panel_separator(
        separator_states["bottomArrow"], "vv-hsplit"
    )["size"] == 133
    assert panel_separator(
        separator_states["bottomShiftArrow"], "vv-hsplit"
    )["size"] == 83
    assert panel_separator(
        separator_states["bottomHome"], "vv-hsplit"
    )["size"] == 80
    assert panel_separator(
        separator_states["bottomHomeRestored"], "vv-hsplit"
    )["size"] == 83
    bottom_end = panel_separator(separator_states["bottomEnd"], "vv-hsplit")
    assert bottom_end["size"] == bottom_end["ariaValueMax"]
    assert bottom_end["ariaValueMax"] == resized_bottom_maximum
    assert panel_separator(
        separator_states["bottomCollapsed"], "vv-hsplit"
    )["size"] == 80
    assert panel_separator(
        separator_states["bottomMaxRestored"], "vv-hsplit"
    )["size"] == resized_bottom_maximum

    assert separator_states["unmounted"]["separatorIds"] == [
        "vv-left-panel-separator"
    ]
    assert separator_states["unmounted"]["uniqueSeparatorIds"] == 1
    assert separator_states["remounted"]["separatorIds"] == [
        "vv-hsplit",
        "vv-left-panel-separator",
        "vv-right-panel-separator",
    ]
    assert separator_states["remounted"]["uniqueSeparatorIds"] == 3
    assert panel_separator(
        separator_states["remounted"], "vv-right-panel-separator"
    )["size"] == initial_right + 10
    assert separator_states["remounted"]["sideState"]["right"] == initial_right + 10
    assert panel_separator(
        separator_states["remountChanged"], "vv-right-panel-separator"
    )["size"] == initial_right
    assert separator_states["remountChanged"]["sideState"]["right"] == initial_right

    assert changed_slider_state["clipX"] == 0.51
    assert changed_slider_state["positionMessage"] == "2D slice at x=0.51"
    assert retained_slider_state["positionMessage"] == "2D slice at x=0.51"
    assert all(
        "2D slice at" not in message
        for message in retained_slider_state["liveMessages"]
    )
    assert slider_state["javascriptErrors"] == []

    assert colour_state["selectedSection"] == "structure"
    assert len(colour_state["inputs"]) == 1
    colour_input = colour_state["inputs"][0]
    assert colour_input == {
        "tagName": "INPUT",
        "type": "color",
        "visible": True,
        "disabled": False,
        "tabIndex": 0,
        "value": "#ff8800",
        "ariaLabel": "Element colour override",
        "ariaLabelledby": None,
        "title": "Element colour override",
    }

    def assert_playback_snapshot(snapshot, expected_names):
        buttons = snapshot["buttons"]
        assert {button["ariaLabel"] for button in buttons} == set(expected_names)
        assert len(buttons) == 3
        assert all(button["tagName"] == "BUTTON" for button in buttons)
        assert all(button["role"] == "button" for button in buttons)
        assert all(button["tabIndex"] == 0 for button in buttons)
        assert all(button["title"] == button["ariaLabel"] for button in buttons)
        assert all(button["visibleText"] == "" for button in buttons)
        assert all(button["iconAriaHidden"] is True for button in buttons)

    for playback, scenario, previous, start, pause, next_name in (
        (
            trajectory,
            "trajectory",
            "Previous trajectory frame",
            "Start trajectory animation",
            "Pause trajectory animation",
            "Next trajectory frame",
        ),
        (
            orbitals,
            "molecular-orbital",
            "Previous molecular orbital",
            "Start molecular orbital animation",
            "Pause molecular orbital animation",
            "Next molecular orbital",
        ),
    ):
        assert playback["scenario"] == scenario
        assert playback["initial"]["animating"] is False
        assert_playback_snapshot(playback["initial"], (previous, start, next_name))
        assert playback["playing"]["animating"] is True
        assert_playback_snapshot(playback["playing"], (previous, pause, next_name))
        assert playback["paused"]["animating"] is False
        assert_playback_snapshot(playback["paused"], (previous, start, next_name))

    # #112: the browser outlines the orbital isosurface in the Publication
    # look, and the outline follows a trusted drag frame by frame. The dark
    # pixels the pass adds lie on the boundary of the surface rendered alone
    # (the rest is the outline of a fold) and cover most of that boundary.
    assert silhouette["ready"]["outlines"] and silhouette["ready"]["targets"] >= 1
    for phase in ("before", "during"):
        frame = silhouette[phase]["frame"]
        assert frame["outlined"] >= 1, (phase, silhouette[phase])
        assert frame["extra"] > 200, (phase, frame)
        assert frame["precision"] > 0.7, (phase, frame)
        assert frame["recall"] > 0.7, (phase, frame)
    assert silhouette["cameraTurnDeg"] > 10.0, silhouette["cameraTurnDeg"]
    assert silhouette["during"]["stats"]["frames"] > silhouette["before"]["stats"]["frames"]
    assert silhouette["picked"], "a measure-mode click picked no atom with the pass installed"
    assert silhouette["after"]["stats"]["error"] is None, silhouette["after"]["stats"]
    # #116: in the Publication look, Export screenshot takes the frame the
    # browser draws, at the view's size times the scale, so the exported
    # image has the silhouette pass's outline: the dark pixels the pass adds
    # lie on the orbital's boundary in the export without it and cover most
    # of that boundary. The line scales with the export, a transparent
    # export keys out the background, and the live view is left as it was.
    export_ready = browser_screenshot["ready"]
    assert export_ready["browserReady"] and export_ready["silhouette"]["targets"] >= 1
    css_width, css_height = export_ready["css"]
    outlined_shot, plain_shot, transparent_shot = browser_screenshot["shots"]
    for shot, scale in ((outlined_shot, 1), (plain_shot, 1), (transparent_shot, 2)):
        assert shot["name"] == "vibe-view-screenshot.png", shot
        assert (shot["width"], shot["height"]) == (
            int(css_width * scale + 0.5),
            int(css_height * scale + 0.5),
        ), (shot, export_ready["css"])
        assert "browser view" in shot["status"], shot["status"]
    assert plain_shot["capture"]["outlineRadius"] is None, plain_shot
    assert outlined_shot["capture"]["outlineRadius"] >= 2, outlined_shot
    assert (
        transparent_shot["capture"]["outlineRadius"]
        == 2 * outlined_shot["capture"]["outlineRadius"]
    ), transparent_shot
    export_analysis = browser_screenshot["analysis"]
    assert export_analysis["extra"] > 200, export_analysis
    assert export_analysis["precision"] > 0.7, export_analysis
    assert export_analysis["recall"] > 0.7, export_analysis
    assert export_analysis["corner"][3] == 0 and export_analysis["clear"] > 0, export_analysis
    assert 3.0 < export_analysis["scaleRatio"] < 6.5, export_analysis
    export_after = browser_screenshot["after"]
    assert export_after["live"] == export_ready["live"], export_after
    assert export_after["silhouette"]["error"] is None, export_after
    assert export_after["capture"]["error"] is None, export_after
    # #111: during a trusted drag, before the release tells the server
    # anything, the labels on screen are the camera-facing glyphs for the
    # current camera (frame readback against the rasterised prediction),
    # while the camera has turned around the structure.
    assert label_followers["initial"]["scriptInstalled"]
    assert label_followers["initial"]["rendererKnown"]
    for phase in ("initial", "dragging", "released"):
        snapshot = label_followers[phase]
        frame = snapshot["frame"]
        assert snapshot["glyphs"] == 2, (phase, snapshot)
        assert snapshot["stats"]["configured"] == 1, (phase, snapshot["stats"])
        assert frame["lit"] > 500, (phase, frame)
        assert frame["facing"]["precision"] > 0.97, (phase, frame)
        assert frame["facing"]["recall"] > 0.97, (phase, frame)
    assert label_followers["cameraTurnDeg"] > 10.0, label_followers["cameraTurnDeg"]
    dragging, initial = label_followers["dragging"], label_followers["initial"]
    # The comparison discriminates: the geometry as sent, which is what a
    # drag drew before #111, would put the labels elsewhere on this frame.
    assert dragging["frame"]["facingSentIoU"] < 0.5, dragging["frame"]
    assert dragging["frame"]["sent"]["precision"] < 0.5, dragging["frame"]
    # The GPU does the turning: the client's mesh is still the one the server
    # sent, and the shader's camera uniforms were set on every frame.
    assert dragging["sample"] == initial["sample"]
    assert dragging["stats"]["draws"] > initial["stats"]["draws"]
    assert trajectory["initial"]["selectedSection"] == "traj0"
    assert trajectory["initial"]["trajectoryFrames"] == 3
    assert trajectory["initial"]["wavefunctionSection"] == ""
    assert orbitals["initial"]["selectedSection"] == "wf"
    assert orbitals["initial"]["trajectoryFrames"] == 0
    assert orbitals["initial"]["wavefunctionSection"] == "wf"
    assert orbitals["initial"]["wavefunctionRows"] > 0

    assert orbital_progress["initial"]["selectedMO"] == "restricted:0"
    assert orbital_progress["initial"]["moVisible"] is True
    assert orbital_progress["initial"]["surfaceKind"] == "mo"
    progressed = {
        state["selectedMO"] for state in orbital_progress["observed"]
        if state["moVisible"] and state["surfaceKind"] == "mo"
    }
    # MO 1 is rendered by Start; MO 2 and 3 require scheduled callbacks.
    assert {"restricted:2", "restricted:3"} <= progressed
    for phase in ("finished", "settled"):
        snapshot = orbital_progress[phase]
        assert snapshot["selectedMO"] == "restricted:3"
        assert snapshot["lastMO"] == 3
        assert snapshot["moVisible"] is True
        assert snapshot["surfaceKind"] == "mo"
        assert snapshot["animating"] is False
        assert snapshot["statusMessage"] == "MO animation finished"
        assert_playback_snapshot(snapshot, (
            "Previous molecular orbital", "Start molecular orbital animation",
            "Next molecular orbital",
        ))
    assert orbital_progress["initialCanvas"] != orbital_progress["finalCanvas"]
    import numpy as np
    from PIL import Image

    for name in ("initial-orbital", "final-orbital"):
        with Image.open(tmp_path / "chrome-profile-orbital-progress" / f"{name}.png") as image:
            # Reject a blank/loading canvas even when its PNG differs from the
            # final frame. Real phase-coloured orbital surfaces are chromatic.
            pixels = np.asarray(image.convert("RGB"), dtype=int)
            coloured = np.count_nonzero(np.ptp(pixels, axis=2) > 40)
            assert coloured > image.width * image.height * 0.01, (
                f"{name} has no rendered orbital"
            )


    entered = presentation["entered"]
    # Trusted keys must leave the page visible, or no transition can finish (#83).
    assert entered["visibility"] == "visible"
    assert entered["overlayVisible"] is True
    assert entered["overlayViewport"] is True
    assert entered["overlayPointerEvents"] == "none"
    assert entered["centerBlockedByOverlay"] is False
    assert entered["presentationSlide"] == 0
    assert "Structure slide one" in entered["overlayText"]
    assert "1 / 2" in entered["overlayText"]
    assert {
        "Previous slide",
        "Start automatic slide advance",
        "Next slide",
        "Exit presentation",
    } <= set(entered["overlayButtons"])
    assert entered["appBarVisible"] is False
    assert entered["leftDrawerVisible"] is False
    assert entered["rightDrawerVisible"] is False
    assert entered["bottomPanelVisible"] is False

    assert presentation["editKeysSuppressed"]["presentationMode"] is True
    assert presentation["editKeysSuppressed"]["editMode"] is False
    assert presentation["editKeysSuppressed"]["statusMessage"] == entered[
        "statusMessage"
    ]

    assert presentation["afterRight"]["presentationSlide"] == 1
    assert "Structure slide two" in presentation["afterRight"]["overlayText"]
    assert presentation["afterLeft"]["presentationSlide"] == 0
    assert "Structure slide one" in presentation["afterLeft"]["overlayText"]

    assert presentation["autoAdvanced"]["presentationSlide"] == 1
    assert presentation["autoPaused"]["presentationAuto"] is False
    assert presentation["autoRetained"]["presentationSlide"] == presentation[
        "autoPaused"
    ]["presentationSlide"]
    assert presentation["autoRetained"]["presentationAuto"] is False

    assert presentation["exited"]["presentationMode"] is False
    assert presentation["exited"]["overlayVisible"] is False
    assert presentation["exited"]["visibility"] == "visible"
    assert presentation["exited"]["editMode"] is False
    assert presentation["idleEscape"]["presentationMode"] is False
    assert presentation["idleEscape"]["editMode"] is False


def test_toolbar_tooltip_keeps_accessible_name_when_title_is_removed():
    """The custom tooltip must not delete the control's accessible name."""
    from tests._served import served_viewer

    source = served_viewer()
    guard = "!el.hasAttribute('aria-label')&&!el.hasAttribute('aria-labelledby')"
    copy = "el.setAttribute('aria-label',t)"
    remove = "el.removeAttribute('title')"

    assert guard in source
    assert source.index(guard) < source.index(copy) < source.index(remove)


def test_scf_history_skip_first_drops_the_guess():
    from vibeview.qvf import QVFReader
    from vibeview.renderers.scf_history import SCFHistoryRenderer

    path = _qvf("t", None, None, scf_iters=[
        {"iteration": 0, "energy_eh": -40.0},
        {"iteration": 1, "energy_eh": -81.0},
        {"iteration": 2, "energy_eh": -81.002},
    ])
    try:
        r = QVFReader(path)
        sec = next(s for s in r.sections if s.kind == "scf_history")
        assert "-40.0" in SCFHistoryRenderer(sec, r).render_to_html()
        skipped = SCFHistoryRenderer(sec, r).render_to_html(skip_first=True)
        assert "-40.0" not in skipped
        assert "-81.002" in skipped
    finally:
        path.unlink()


def test_scf_history_log_energy_axis():
    """The log-scale view plots |E - E_final| on a log axis and drops the
    zero final point (design refresh 2026, SCF item)."""
    from vibeview.qvf import QVFReader
    from vibeview.renderers.scf_history import SCFHistoryRenderer

    path = _qvf("t", None, None, scf_iters=[
        {"iteration": 0, "energy_eh": -40.0},
        {"iteration": 1, "energy_eh": -75.9},
        {"iteration": 2, "energy_eh": -75.999},
        {"iteration": 3, "energy_eh": -76.0},
    ])
    try:
        r = QVFReader(path)
        sec = next(s for s in r.sections if s.kind == "scf_history")
        linear = SCFHistoryRenderer(sec, r).render_to_html()
        log = SCFHistoryRenderer(sec, r).render_to_html(log_energy=True)
        assert linear != log
        assert "E_final" in log  # |E - E_final| axis label
        assert '"type":"log"' in log  # log Y axis (minified plotly JSON)
        assert "E_final" not in linear  # linear view keeps the raw energy axis
    finally:
        path.unlink()


def test_spectra_comparison_overlays_all_files():
    from vibeview.qvf import QVFReader
    from vibeview.renderers.spectra import render_comparison_html

    pa = _qvf("fileA", [500.0, 1500.0], [10.0, 40.0])
    pb = _qvf("fileB", [520.0, 1480.0], [12.0, 35.0])
    try:
        ra, rb = QVFReader(pa), QVFReader(pb)
        ia = next(s for s in ra.sections if s.kind == "spectra.ir")
        ib = next(s for s in rb.sections if s.kind == "spectra.ir")
        html = render_comparison_html([("fileA", ia, ra), ("fileB", ib, rb)])
        assert "fileA" in html and "fileB" in html
        assert "2 files" in html
    finally:
        pa.unlink()
        pb.unlink()


def test_activate_spectra_compare_branch_and_fallback():
    import types

    import vibeview.app as app
    from vibeview.qvf import QVFReader

    pa = _qvf("fileA", [500.0], [10.0])
    pb = _qvf("fileB", [520.0], [12.0])
    try:
        ra, rb = QVFReader(pa), QVFReader(pb)
        ia = next(s for s in ra.sections if s.kind == "spectra.ir")
        st = types.SimpleNamespace(
            spectra_compare=True, spectra_html=None, spectra_title="", status_message=""
        )
        app._activate_spectra(ra, st, ia, [ra, rb])
        assert st.spectra_html and "2 files" in st.spectra_title
        # Compare on, but only one file open -> plain single-file render.
        st1 = types.SimpleNamespace(
            spectra_compare=True, spectra_html=None, spectra_title="", status_message=""
        )
        app._activate_spectra(ra, st1, ia, [ra])
        assert st1.spectra_html and "2 files" not in st1.spectra_title
    finally:
        pa.unlink()
        pb.unlink()


def test_compare_without_a_partner_says_so():
    """#143: the no-partner note was overwritten by "Loaded spectra: ...",
    so ticking Compare across open files appeared to do nothing."""
    import types

    import vibeview.app as app
    from vibeview.qvf import QVFReader

    pa = _qvf("fileA", [500.0], [10.0])
    pb = _qvf("fileB", None, None, scf_iters=[{"iteration": 0, "energy_eh": -1.0}])
    try:
        ra, rb = QVFReader(pa), QVFReader(pb)
        assert not any(s.kind == "spectra.ir" for s in rb.sections)
        ia = next(s for s in ra.sections if s.kind == "spectra.ir")
        st = types.SimpleNamespace(
            spectra_compare=True, spectra_html=None, spectra_title="", status_message=""
        )
        app._activate_spectra(ra, st, ia, [ra, rb])
        assert st.spectra_html
        assert st.status_message == (
            f"Loaded spectra: {ia.id}; no other open file has a spectra.ir section to compare"
        )
    finally:
        pa.unlink()
        pb.unlink()


def test_library_names_helper_is_import_safe():
    from vibeview.app import _library_structure_names

    names = _library_structure_names()
    assert isinstance(names, list)  # [] without vibeqc_naming, sorted names with
    if names:
        assert names == sorted(names)
        assert all(isinstance(n, str) for n in names)


def test_spectra_gamma_and_normalize_knobs():
    """Broadening multiplier changes the envelope; normalize caps it at 1."""
    from vibeview.qvf import QVFReader
    from vibeview.renderers.spectra import SpectraRenderer, render_comparison_html

    p = _qvf("f", [500.0, 1500.0], [10.0, 40.0])
    try:
        r = QVFReader(p)
        sec = next(s for s in r.sections if s.kind == "spectra.ir")
        base = SpectraRenderer(sec, r).render_to_html()
        wide = SpectraRenderer(sec, r).render_to_html(gamma_scale=3.0)
        assert base != wide  # broader envelope -> different plot payload
        norm = SpectraRenderer(sec, r).render_to_html(normalize=True)
        assert norm != base
        # comparison path accepts the same knobs
        html = render_comparison_html([("f", sec, r)], gamma_scale=2.0, normalize=True)
        assert "envelope" not in html or html  # smoke: renders without error
    finally:
        p.unlink()


def test_spectra_stick_vs_envelope_display_modes():
    """The display selector draws exactly what it names.

    ``both`` is the historical rendering (envelope + stick underlay);
    ``envelope`` drops the sticks; ``sticks`` drops the envelope and
    promotes the sticks to the primary hoverable trace. Unknown values
    fall back to ``both`` instead of rendering an empty plot.
    """
    from vibeview.qvf import QVFReader
    from vibeview.renderers.spectra import SpectraRenderer

    p = _qvf("f", [500.0, 1500.0, 3000.0], [10.0, 50.0, 25.0])
    try:
        r = QVFReader(p)
        sec = next(s for s in r.sections if s.kind == "spectra.ir")

        def probe(display):
            html = SpectraRenderer(sec, r).render_to_html(display=display)
            compact = html.replace(" ", "")
            has_envelope = '"name":"Lorentzianenvelope' in compact
            # the sticks are one None-separated trace named "Sticks" (#46)
            has_sticks = '"name":"Sticks"' in compact
            return has_envelope, has_sticks

        assert probe("both") == (True, True)
        assert probe("envelope") == (True, False)
        assert probe("sticks") == (False, True)
        assert probe("nonsense") == (True, True), "unknown mode must fall back"
    finally:
        p.unlink()


def test_comparison_overlay_honours_the_display_selector():
    """The multi-file overlay follows the same display modes as the single
    view — with per-file colours on the sticks — while a parameterless call
    keeps the historical envelopes-only overlay (sticks across several
    files read as a picket fence unless explicitly requested).

    Envelopes are 2000-point traces (plotly binary-encodes them as
    ``"y": {"dtype": ...}``); sticks are two-point JSON lists — that is
    how the two trace families are counted apart.
    """
    import re

    from vibeview.qvf import QVFReader
    from vibeview.renderers.spectra import render_comparison_html

    p1 = _qvf("a", [500.0, 1500.0], [10.0, 40.0])
    p2 = _qvf("b", [600.0, 1600.0], [20.0, 30.0])
    try:
        r1, r2 = QVFReader(p1), QVFReader(p2)
        entries = [
            ("file-a", next(s for s in r1.sections if s.kind == "spectra.ir"), r1),
            ("file-b", next(s for s in r2.sections if s.kind == "spectra.ir"), r2),
        ]

        def probe(**kw):
            html = render_comparison_html(entries, **kw).replace(" ", "")
            n_env = len(re.findall(r'"y":\{"dtype":', html))
            # one None-separated stick trace per file (#46): its y list
            # carries the null separators
            n_stick = len(re.findall(r'"y":\[[^\]]*null', html))
            return n_env, n_stick

        assert probe() == (2, 0), "parameterless default must stay envelopes-only"
        assert probe(display="both") == (2, 2)
        assert probe(display="envelope") == (2, 0)
        assert probe(display="sticks") == (0, 2)
        assert probe(display="junk") == (2, 0), "unknown mode falls back"
    finally:
        p1.unlink()
        p2.unlink()


def test_spectra_unit_conversion_and_csv():
    """X-unit toggle converts the axis; to_csv emits envelope + peaks."""
    from vibeview.qvf import QVFReader
    from vibeview.renderers.spectra import SpectraRenderer

    # UV-Vis native eV -> nm is the standard spectroscopy conversion.
    p = _qvf("uv", [4.0, 5.0], [0.3, 0.6])
    try:
        r = QVFReader(p)
        # Force the section kind to uvvis so eV is the native unit.
        sec = next(s for s in r.sections if s.kind == "spectra.ir")
        native = SpectraRenderer(sec, r).render_to_html(x_unit="native")
        as_nm = SpectraRenderer(sec, r).render_to_html(x_unit="nm")
        assert native != as_nm  # relabelled + remapped axis
        csv = SpectraRenderer(sec, r).to_csv(x_unit="eV")
        assert "# envelope" in csv and "# peaks" in csv
        assert "eV,intensity" in csv
        # peaks block carries the two input modes
        assert csv.count("\n") > 100  # 2000-pt grid + peaks
    finally:
        p.unlink()


def test_energy_diagram_click_payload():
    """The orbital energy diagram embeds clickable render keys + the bridge
    script that posts them to the parent window (click-to-render MO). Uses a
    lightweight fake wavefunction so the test needs no on-disk QVF."""
    import types

    import numpy as np

    from vibeview.renderers.wavefunction import WavefunctionRenderer

    fake_wf = types.SimpleNamespace(
        spin="restricted",
        energies=np.array([-1.0, -0.5, -0.2, 0.1, 0.3]),
        occupations=np.array([2.0, 2.0, 2.0, 0.0, 0.0]),
        alpha_energies=None,
        alpha_occupations=None,
        beta_energies=None,
        beta_occupations=None,
        orbital_kind="canonical",
    )
    # Build a renderer without touching a file, then stub out load().
    r = WavefunctionRenderer.__new__(WavefunctionRenderer)
    r.load = lambda: fake_wf  # type: ignore[method-assign]
    html = r.render_energy_diagram()

    assert "customdata" in html
    assert "vibeview-mo-click" in html
    assert "window.parent.postMessage" in html
    # HOMO is index 2 (last occupied); its composite render key must be present.
    assert "restricted:2" in html

def test_command_palette_actions_well_formed():
    """Every command-palette entry has the fields the UI binds and a unique
    id, and every id is wired in run_palette_action's dispatch (design
    refresh 2026)."""

    import vibeview.app as app

    actions = app._PALETTE_ACTIONS
    assert actions, "palette action list is empty"
    ids = [a["id"] for a in actions]
    assert len(ids) == len(set(ids)), "duplicate palette action id"
    for a in actions:
        assert set(a) >= {"id", "title", "sub", "icon"}
        assert all(isinstance(a[k], str) and a[k] for k in ("id", "title", "sub", "icon"))
    # That every id is dispatched is a structural contract:
    # test_structural_contracts.py reads run_palette_action's dispatch table.

def test_element_color_overrides_reach_every_render_path():
    """A per-element colour override is honoured by cpk_color and therefore by
    every render path — molecular per-atom actors AND glyph-batched replicated
    cells (design refresh 2026)."""
    import pyvista as pv

    from vibeview.renderers.structure import (
        cpk_color,
        get_color_overrides,
        set_color_overrides,
    )

    stock_c = cpk_color(6)
    try:
        set_color_overrides({6: "#00FF00"})
        assert cpk_color(6) == "#00FF00"
        assert cpk_color(8) != "#00FF00", "override must not leak to other elements"
        assert get_color_overrides() == {6: "#00FF00"}
        # client round-trips dict keys as strings; they must normalise to int
        set_color_overrides({"8": "#123456"})
        assert cpk_color(8) == "#123456"
    finally:
        set_color_overrides(None)
    assert cpk_color(6) == stock_c, "reset must restore the CPK palette"

    # The override must reach actual actor colours, not just the lookup.
    struct = pv.Sphere()
    p = pv.Plotter(off_screen=True)
    p.add_mesh(struct, color=cpk_color(6), name="atom_probe")
    stock_rgb = p.actors["atom_probe"].GetProperty().GetColor()
    p.close()
    set_color_overrides({6: "#00FF00"})
    try:
        p2 = pv.Plotter(off_screen=True)
        p2.add_mesh(pv.Sphere(), color=cpk_color(6), name="atom_probe")
        over_rgb = p2.actors["atom_probe"].GetProperty().GetColor()
        p2.close()
    finally:
        set_color_overrides(None)
    assert over_rgb != stock_rgb
    assert tuple(round(x, 3) for x in over_rgb) == (0.0, 1.0, 0.0)

def test_sidebar_kind_icons_cover_every_titled_kind():
    """Every section kind with a sidebar title has a distinct kind glyph, and
    unknown kinds fall back rather than rendering blank (design refresh 2026,
    sidebar kind icons)."""
    import vibeview.app as app

    missing = [k for k in app._KIND_TITLES if k not in app._KIND_ICONS]
    assert not missing, f"section kinds without a sidebar icon: {missing}"
    stray = [k for k in app._KIND_ICONS if k not in app._KIND_TITLES]
    assert not stray, f"icons for kinds that have no title: {stray}"
    assert all(v.startswith("mdi-") for v in app._KIND_ICONS.values())
    # unknown kind must still yield a usable glyph
    assert app._kind_icon("totally.unknown") == app._DEFAULT_KIND_ICON
    assert app._kind_icon("structure") == "mdi-molecule"

def test_ambient_occlusion_pass_actually_applies():
    """SSAO must really attach to the renderer.

    Regression: ambient_occlusion_pass() called vtkSSAOPass.SetSamples(),
    which that class has never had (sample count is SetKernelSize), so it
    raised AttributeError before SetPass() — and its ``except ImportError``
    could not catch it. Callers swallow exceptions, so SSAO silently never
    applied while the UI switch read "on".
    """
    import pyvista as pv

    from vibeview.material_presets import ambient_occlusion_pass

    p = pv.Plotter(off_screen=True)
    p.add_mesh(pv.Sphere())
    try:
        assert p.renderer.GetPass() is None
        assert ambient_occlusion_pass(p) is True, "SSAO failed to apply"
        assert p.renderer.GetPass() is not None, "no render pass attached"
    finally:
        p.close()


def test_ambient_occlusion_is_server_side_only():
    """A render pass never reaches the client's VtkLocalView.

    The serializer emits actors/properties/camera/lights — not renderer
    passes — so the scene sent to vtk.js is identical with and without SSAO.
    This is why the toggle is labelled as affecting exported images, and why
    SSAO cannot explain a client-side render freeze.

    Serialize through **trame-vtk**, which is what actually feeds the client:
    ``create_app`` builds a ``VtkLocalView`` and wires ``local_view.update``
    to ``ctrl.view_update``. This test used to reach for VTK's own bundled
    ``vtkmodules.web.render_window_serializer`` instead, and from VTK 9.7 that
    module raises ``TypeError: unhashable type: 'VTKAOSArray_vtkFloatArray'``:
    its ``extract_required_fields`` collects candidate arrays in a ``set()``,
    and 9.7 gave ``vtkDataArray`` an ``__eq__`` without a ``__hash__``, so
    every array became unhashable. trame-vtk carries its own copy of that
    function which collects into a list and compares with ``is``, so the path
    vibe-view actually uses was never affected — the test was exercising a
    module the viewer does not import.
    """
    import json
    import re

    import pyvista as pv
    from trame_vtk.modules.vtk import serializers as tv

    from vibeview.material_presets import ambient_occlusion_pass

    tv.initialize_serializers()

    def payload(with_ssao: bool) -> str:
        p = pv.Plotter(off_screen=True)
        p.add_mesh(pv.Sphere(), color="#909090")
        if with_ssao:
            assert ambient_occlusion_pass(p) is True
        ctx = tv.SynchronizationContext()
        blob = json.dumps(
            tv.serialize(None, p.ren_win, tv.reference_id(p.ren_win), ctx, 0),
            sort_keys=True,
        )
        p.close()
        # Instance ids are memory addresses and mtime is VTK's modification
        # counter (it bumps whenever the renderer is touched at all) —
        # normalise both so only real scene content is compared.
        blob = re.sub(r"[0-9a-fA-F]{8,}", "ID", blob)
        return re.sub(r'"mtime":\s*\d+', '"mtime": N', blob)

    without_ssao = payload(False)
    with_ssao = payload(True)
    # A payload that serialized nothing would make this assertion vacuous.
    assert "vtkPolyData" in without_ssao
    assert without_ssao == with_ssao

def test_library_recent_picks_are_deduped_and_capped():
    """Recently-used library structures: most-recent-first, de-duplicated and
    capped so the chip row stays one line (design refresh 2026)."""
    import types

    from vibeview.app import _LIBRARY_RECENT_MAX, _remember_library_pick

    st = types.SimpleNamespace(library_recent=[])
    for name in ("water", "benzene", "water", "methane"):
        _remember_library_pick(st, name)
    # re-picking water moves it to the front rather than duplicating it
    assert st.library_recent == ["methane", "water", "benzene"]

    for i in range(_LIBRARY_RECENT_MAX + 3):
        _remember_library_pick(st, f"mol{i}")
    assert len(st.library_recent) == _LIBRARY_RECENT_MAX
    assert st.library_recent[0] == f"mol{_LIBRARY_RECENT_MAX + 2}"
    assert len(set(st.library_recent)) == len(st.library_recent)

    # blank / None must not pollute the list
    before = list(st.library_recent)
    _remember_library_pick(st, "   ")
    _remember_library_pick(st, None)
    assert st.library_recent == before

def test_scf_delta_e_bars_and_degradation():
    """|ΔE| per cycle is plotted on the log residual axis when present.

    vibe-qc's solvers record delta_e every cycle but emit no diis_error, so
    before this the right-hand axis stayed empty and the file's only
    convergence signal went unplotted. Zeros (log-undefined) and absent
    delta_e must degrade to no series rather than a broken axis.
    """
    from vibeview.qvf import QVFReader
    from vibeview.renderers.scf_history import SCFHistoryRenderer

    marker = "per cycle"  # the ΔE trace name / axis title

    def render(iters, **kw):
        path = _qvf("t", None, None, scf_iters=iters)
        try:
            r = QVFReader(path)
            sec = next(s for s in r.sections if s.kind == "scf_history")
            return SCFHistoryRenderer(sec, r).render_to_html(**kw)
        finally:
            path.unlink()

    # present -> plotted, and the axis is titled for ΔE (not a stale DIIS label)
    html = render([
        {"iter": 0, "energy_eh": -70.0, "delta_e": 5.0},
        {"iter": 1, "energy_eh": -75.9, "delta_e": 1e-3},
        {"iter": 2, "energy_eh": -76.0, "delta_e": 1e-7},
    ])
    assert marker in html
    assert "DIIS error (log)" not in html
    assert '"type":"log"' in html

    # magnitudes only — delta_e flips sign as the energy settles
    neg = render([
        {"iter": 0, "energy_eh": -70.0, "delta_e": -5.0},
        {"iter": 1, "energy_eh": -76.0, "delta_e": -1e-6},
    ])
    assert marker in neg

    # absent / all-zero -> no series, no residual axis
    assert marker not in render([{"iter": i, "energy_eh": e} for i, e in enumerate([-40.0, -76.0])])
    assert marker not in render([{"iter": i, "energy_eh": -76.0, "delta_e": 0.0} for i in range(3)])

    # coexists with the log-energy view
    assert marker in render(
        [{"iter": i, "energy_eh": -76.0 + 10.0 ** -i, "delta_e": 10.0 ** -i} for i in range(4)],
        log_energy=True,
    )

def test_library_favorites_persist_in_the_session_payload():
    """Pinned library structures ride in the saved session and a session
    written before the feature still loads (design refresh 2026).

    Favourites are deliberate picks so they persist; recents are implicit and
    deliberately do not.
    """
    import json

    favorites = ["benzene", "water"]
    session = {
        "version": 1,
        "active_section": "structure",
        "isovalue": 0.05,
        "colormap": "viridis",
        "opacity": 0.6,
        "camera": {},
        "replication": [1, 1, 1],
        "user_bookmarks": [],
        "library_favorites": list(favorites),
    }
    round_tripped = json.loads(json.dumps(session))
    assert round_tripped["library_favorites"] == favorites

    # a pre-feature session must default cleanly rather than KeyError
    legacy = {k: v for k, v in session.items() if k != "library_favorites"}
    assert legacy.get("library_favorites", []) == []

    # recents are NOT persisted — they are implicit
    assert "library_recent" not in session

def test_auto_save_writes_a_loadable_session(tmp_path):
    """The auto-save must emit the document load_session accepts.

    Regression: auto_save_session built its own dict with ``version: "2.0"``
    (a string) and differently-named fields, while load_session accepts only
    ``version == 1``. Because the auto-save loop rewrites the *same*
    session_path, an explicitly saved session was replaced within 60 s by a
    file the loader then refused ("Bad version") — destroying it — and the
    fields the auto-save omitted (camera, replication, library_favorites) were
    lost with it. Driven through the controllers: both writers produce the
    same document, and the loader accepts what the auto-save wrote.
    """
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(Path(__file__).parent / "data" / "h2_natural_orbitals.qvf")
    try:
        server = create_app(reader)
        state, ctrl = server.state, server.controller
        state.library_favorites = ["water"]
        saved, autosaved = tmp_path / "saved.json", tmp_path / "auto.json"
        ctrl.save_session(str(saved))
        state.session_path = str(autosaved)
        ctrl.auto_save_session()
        explicit = json.loads(saved.read_text())
        automatic = json.loads(autosaved.read_text())

        assert explicit["version"] == automatic["version"] == 1
        assert set(automatic) - {"timestamp"} == set(explicit)
        for field in ("camera", "replication", "library_favorites"):
            assert field in automatic, f"{field} missing from the auto-saved session"
        assert automatic["library_favorites"] == ["water"]

        state.status_message = ""
        ctrl.load_session(str(autosaved))
        assert state.status_message != "Bad version"
    finally:
        reader.close()


def test_switching_sections_drops_the_other_isosurface():
    """Entering Wavefunction clears the electron density, and vice versa.

    Reported 2026-07-19: clicking Electron Density, then Wavefunction, then
    rendering an MO left the density isosurface in the scene with the orbital
    drawn inside it — and no way to remove it, because the volume panel is
    gone once a wavefunction section is selected. The overlay cleanup only ran
    in the ``structure`` branch of _activate_section_impl.

    Scope note: the fixture here is structure-only, so this asserts the
    cleanup fires on section activation. The wavefunction<->volume directions
    that the report is actually about need a file carrying both kinds; those
    were verified against the MgO reference QVF by spying on
    _remove_actors_by_prefix (density->wavefunction removes "volume_",
    wavefunction->density removes "mo_iso_").
    """
    import os

    os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
    import vibeview.app as app
    from vibeview.qvf import QVFReader

    path = _qvf("t", None, None)  # structure-only file
    try:
        reader = QVFReader(path)
        calls: list[str] = []
        original = app._remove_actors_by_prefix

        def spy(plotter, prefix):
            calls.append(prefix)
            return original(plotter, prefix)

        app._helpers._remove_actors_by_prefix = spy
        try:
            built = app.create_app(reader)
            ctrl = built.controller

            # a structure-only file still exercises the dispatch guard: the
            # branch is keyed on section.kind, not on the file's contents.
            calls.clear()
            ctrl.activate_section("structure")
            # structure clears every overlay it may have inherited
            assert "volume_" in calls
            assert "mo_iso_" in calls
        finally:
            app._helpers._remove_actors_by_prefix = original
    finally:
        path.unlink()

def test_orbital_opacity_is_opaque_by_default_and_independent_of_volumes():
    """Orbital lobes default to opaque and keep their own opacity.

    Two things ride on this:

    * Any opacity < 1.0 puts vtk.js into depth-peeling, which is what hung the
      client on MO renders (confirmed 2026-07-19 from the desktop app: an
      opaque orbital renders fine where a translucent one froze). So the
      default must be 1.0.
    * ``state.opacity`` is overwritten from a volume's hints whenever a volume
      section activates, so sharing it clobbered whatever the user had chosen
      for the orbital. Volume isosurfaces keep their own 0.6 default, since a
      density surface is meant to be seen through.
    """
    import os

    os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
    import vibeview.app as app
    from vibeview.qvf import QVFReader

    path = _qvf("t", None, None)
    try:
        built = app.create_app(QVFReader(path))
        state, ctrl = built.state, built.controller

        assert state.mo_opacity == 1.0, "orbitals must default opaque (depth-peel hang)"
        assert state.opacity == 0.6, "volume isosurfaces should stay translucent"

        # the orbital slider drives its own key, not the volume one
        ctrl.update_mo_opacity(0.35)
        assert state.mo_opacity == 0.35
        assert state.opacity == 0.6, "orbital slider must not move volume opacity"
    finally:
        path.unlink()
