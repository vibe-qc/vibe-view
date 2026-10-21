"""The browser's Open dialog loads a chosen file (#117).

Its handlers ran in Vue 3 template scope, which cannot see ``setTimeout``
or ``document``: opening the dialog threw, the hidden file input was never
wired, and Browse opened no chooser. The page also handed the bytes over
through state and then triggered ``load_file_from_bytes``, but a trigger
does not wait for a state flush, so the server read empty state. The
handlers now call page-script helpers, and the bytes travel as the
trigger's arguments.
"""

from __future__ import annotations

import base64
import re
import shutil
from pathlib import Path

import pytest

_H2 = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
_XYZ = b"2\nH2\nH 0 0 0\nH 0 0 0.74\n"
_PAGE_GLOBALS = re.compile(r"\b(document|window|setTimeout)\b")


@pytest.fixture(scope="module")
def served():
    from tests._served import served_viewer

    return served_viewer()


def test_the_dialog_handlers_call_page_script_helpers(served):
    assert "window._vvWireFileInput=function" in served
    assert "window._vvBrowseFile=function" in served
    opened = re.search(
        r'<trame-client-state-change :value="load_file_dialog" @change="([^"]*)"', served
    )
    browse = re.search(r'@click="([^"]*)">\s*Browse chemistry file', served)
    assert opened and browse
    for handler in (opened.group(1), browse.group(1)):
        assert not _PAGE_GLOBALS.search(handler), handler
    assert opened.group(1).endswith("_vvWireFileInput()")
    assert browse.group(1).endswith("_vvBrowseFile()")


def test_an_upload_arrives_as_trigger_arguments(tmp_path):
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    qvf = tmp_path / _H2.name
    shutil.copyfile(_H2, qvf)
    reader = QVFReader(qvf)
    try:
        server = create_app(reader)
        server.state.load_file_dialog = True
        server.controller.trigger_fn("load_file_from_bytes")(
            base64.b64encode(_XYZ).decode(), "h2.xyz"
        )
        assert server.state.status_message.startswith("Loaded h2.xyz")
        assert server.state.file_names[1] == "h2"
        assert server.state.load_file_dialog is False
    finally:
        reader.close()


def _serve(qvf_path: str, port: int) -> None:
    """Child-process entry point for the served-dialog check."""
    import os
    import sys

    os.environ["PYVISTA_OFF_SCREEN"] = "true"
    # ``spawn`` restores pytest's command line, which trame would parse.
    del sys.argv[1:]

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    server = create_app(QVFReader(qvf_path))
    server.start(
        host="127.0.0.1", port=port, open_browser=False, show_connection_info=False, timeout=0
    )


# Opens the dialog from the toolbar, clicks Browse, answers the file chooser
# through CDP and waits for the server's "Loaded" status. Prints one JSON
# object; JavaScript errors are recorded per step.
_PROBE = r"""
const { spawn } = require("node:child_process");
const [chrome, url, profile, upload] = process.argv.slice(1);
const child = spawn(chrome, [
  "--headless", "--disable-background-networking", "--disable-dev-shm-usage",
  "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0",
  "--window-size=1400,1000", `--user-data-dir=${profile}`, "about:blank",
], { stdio: ["ignore", "ignore", "pipe"] });
const result = { errors: [] };
let finished = false;
function finish(code) {
  if (finished) return;
  finished = true;
  child.kill("SIGKILL");
  process.stdout.write(JSON.stringify(result), () => process.exit(code));
}
setTimeout(() => { result.error = "probe timed out"; finish(2); }, 90000);
child.on("exit", (code) => { result.error = `Chrome exited with ${code}`; finish(3); });
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

let stderr = "";
child.stderr.on("data", (chunk) => {
  stderr += chunk;
  const match = stderr.match(/DevTools listening on (ws:\/\/\S+)/);
  if (!match || result.started) return;
  result.started = true;
  run(match[1]).then(() => finish(0), (error) => {
    result.error = String(error.stack || error);
    finish(1);
  });
});

async function run(browserWs) {
  const host = new URL(browserWs).host;
  const page = await fetch(`http://${host}/json/new?about:blank`, { method: "PUT" });
  const target = await page.json();
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let nextId = 0;
  const pending = new Map();
  const events = [];
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.method === "Runtime.exceptionThrown") {
      const details = message.params.exceptionDetails || {};
      result.errors.push((details.exception && details.exception.description) || details.text);
    } else if (message.method === "Runtime.consoleAPICalled" && message.params.type === "error") {
      result.errors.push((message.params.args || []).map(
        (arg) => arg.value || arg.description || arg.type).join(" "));
    } else if (message.method) {
      events.push(message);
    }
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) reject(new Error(JSON.stringify(message.error)));
      else resolve(message.result);
    }
  };
  const command = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression) => {
    const answer = await command("Runtime.evaluate", { expression, returnByValue: true });
    if (answer.exceptionDetails) throw new Error(answer.exceptionDetails.text);
    return answer.result.value;
  };
  const waitFor = async (expression, ms, label) => {
    const start = Date.now();
    while (Date.now() - start < ms) {
      if (await evaluate(expression).catch(() => false)) return true;
      await delay(100);
    }
    throw new Error(`timed out waiting for ${label}`);
  };
  // A real mouse click, so Browse runs with the user activation a file
  // chooser needs.
  const click = async (selector) => {
    const point = await evaluate(`(() => {
      const element = document.querySelector(${JSON.stringify(selector)});
      if (!element) return null;
      const box = element.getBoundingClientRect();
      return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    })()`);
    if (!point) throw new Error(`no element matches ${selector}`);
    for (const type of ["mouseMoved", "mousePressed", "mouseReleased"]) {
      await command("Input.dispatchMouseEvent", {
        type, ...point, button: "left", clickCount: type === "mouseMoved" ? 0 : 1,
      });
    }
  };

  await command("Runtime.enable");
  await command("Page.enable");
  await command("DOM.enable");
  await command("Page.setInterceptFileChooserDialog", { enabled: true });
  await command("Page.navigate", { url });
  const OPEN = 'button[aria-label="Open .qvf file from disk"], ' +
    'button[title="Open .qvf file from disk"]';
  await waitFor(
    `!!(window.trame && window.trame.state &&
       window.trame.state.get('load_file_dialog') === false &&
       document.querySelector(${JSON.stringify(OPEN)}))`,
    60000, "the viewer",
  );
  await delay(1000);
  result.startupErrors = result.errors.splice(0);
  await evaluate(`(() => {
    const trame = window.trame;
    const trigger = trame.trigger.bind(trame);
    window.__triggers = [];
    trame.trigger = (name, args) => {
      window.__triggers.push([name, (args || []).map((arg) => String(arg).slice(0, 64))]);
      return trigger(name, args);
    };
  })()`);

  await click(OPEN);
  await waitFor(
    `!!document.querySelector('[role=dialog][aria-label="Open chemistry files"]')`,
    10000, "the dialog",
  );
  await waitFor(`!!document.getElementById('qvf-file-input')?._vq_wired`, 3000, "the wired input")
    .catch(() => {});
  result.opened = await evaluate(`(() => {
    const input = document.getElementById('qvf-file-input');
    return { input: !!input, wired: !!(input && input._vq_wired) };
  })()`);
  result.openErrors = result.errors.splice(0);

  const firstEvent = events.length;
  await evaluate(`[...document.querySelectorAll('[role=dialog] button')]
    .find((button) => /Browse chemistry file/.test(button.textContent))
    ?.setAttribute('data-probe-browse', '')`);
  await click("[data-probe-browse]");
  const start = Date.now();
  let chooser;
  while (!chooser && Date.now() - start < 5000) {
    chooser = events.slice(firstEvent).find((event) => event.method === "Page.fileChooserOpened");
    if (!chooser) await delay(50);
  }
  result.chooserOpened = Boolean(chooser);
  result.browseErrors = result.errors.splice(0);
  if (!chooser) return;

  await command("DOM.setFileInputFiles", {
    files: [upload], backendNodeId: chooser.params.backendNodeId,
  });
  const loadedStatus = JSON.stringify("Loaded " + upload.split(/[\\/]/).pop());
  await waitFor(
    `String(window.trame.state.get('status_message')).startsWith(${loadedStatus})`,
    20000, "the server to load the file",
  ).catch(() => {});
  result.loaded = await evaluate(`({
    status: window.trame.state.get('status_message'),
    dialog: window.trame.state.get('load_file_dialog'),
    files: window.trame.state.get('file_names'),
    triggers: window.__triggers,
  })`);
  result.loadErrors = result.errors.splice(0);
}
"""


def test_served_dialog_loads_a_chosen_file(tmp_path):
    import json
    import multiprocessing
    import os
    import socket
    import subprocess
    import time

    chrome_candidates = [
        shutil.which(name) for name in ("google-chrome", "chromium", "chromium-browser", "chrome")
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
        pytest.skip("Chrome/Chromium and Node are required for the served-dialog check")
    apis = subprocess.run(
        [node, "-e", "process.exit(typeof fetch === 'function' && "
         "typeof WebSocket === 'function' ? 0 : 1)"],
        capture_output=True,
        timeout=5,
        check=False,
    )
    if apis.returncode:
        pytest.skip("the served-dialog check requires Node with fetch/WebSocket")

    qvf = tmp_path / _H2.name
    shutil.copyfile(_H2, qvf)
    upload = tmp_path / "upload_h2.xyz"
    upload.write_bytes(_XYZ)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    process = multiprocessing.get_context("spawn").Process(target=_serve, args=(str(qvf), port))
    process.start()
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.exitcode is not None:
                pytest.fail(f"the viewer exited with {process.exitcode}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("the viewer did not start within 60 seconds")
        probe = subprocess.run(
            [node, "-e", _PROBE, chrome, f"http://127.0.0.1:{port}/",
             str(tmp_path / "chrome-profile"), str(upload)],
            capture_output=True,
            text=True,
            timeout=150,
            check=False,
        )
    finally:
        process.terminate()
        process.join(10)
        if process.is_alive():
            process.kill()
            process.join()

    result = json.loads(probe.stdout or "{}")
    assert probe.returncode == 0 and "error" not in result, (probe.stderr, result)
    # Opening the dialog wires the input without an error.
    assert result["opened"] == {"input": True, "wired": True}, result
    assert result["openErrors"] == [], result
    # Browse opens the browser's file chooser.
    assert result["chooserOpened"], result
    assert result["browseErrors"] == [], result
    # The chosen file reaches load_file_from_bytes, with its bytes and name.
    loaded = result["loaded"]
    assert loaded["status"].startswith("Loaded upload_h2.xyz"), result
    assert loaded["dialog"] is False
    assert loaded["files"][-1] == "upload_h2"
    assert loaded["triggers"] == [
        ["load_file_from_bytes", [base64.b64encode(_XYZ).decode(), "upload_h2.xyz"]]
    ]
    assert result["loadErrors"] == [], result
