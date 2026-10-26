"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const { ConsoleProcess } = require("./console-process.cjs");
const { isConsoleSender } = require("./python-console.cjs");

test("only the exact local console main frame may call Python", () => {
  const frame = { url: "file:///console.html" };
  const webContents = { mainFrame: frame };
  const window = { webContents, isDestroyed: () => false };
  const event = { sender: webContents, senderFrame: frame };
  assert.equal(isConsoleSender(event, window, frame.url), true);
  assert.equal(isConsoleSender({ ...event, sender: {} }, window, frame.url), false);
  assert.equal(isConsoleSender({ ...event, senderFrame: { url: frame.url } }, window, frame.url), false);
  assert.equal(isConsoleSender(event, window, "https://example.org/"), false);
  assert.equal(isConsoleSender({ sender: webContents }, window, frame.url), false);
  assert.equal(isConsoleSender(event, null, frame.url), false);
});

test("persistent worker handles errors, busy state and stop/reset", { timeout: 20000 }, async (t) => {
  const localPython = path.join(__dirname, "..", ".venv",
    process.platform === "win32" ? "Scripts" : "bin",
    process.platform === "win32" ? "python.exe" : "python");
  const python = process.env.VIBEVIEW_TEST_PYTHON ||
    (fs.existsSync(localPython) ? localPython : "python3");
  const worker = new ConsoleProcess(() => ({ python, cwd: path.join(__dirname, ".."), env: process.env }));
  t.after(() => worker.reset());
  assert.equal((await worker.run("x = 7\nx * 6")).result, "42");
  assert.equal((await worker.run("raise ValueError('expected')")).ok, false);
  assert.equal((await worker.run("x")).result, "7");
  const running = worker.run("while True: pass");
  const rejected = assert.rejects(running, /reset/);
  await assert.rejects(worker.run("1"), /busy/);
  await new Promise(resolve => setTimeout(resolve, 100));
  worker.reset();
  await rejected;
  assert.equal((await worker.run("'x' in globals()")).result, "False");
  assert.equal((await worker.run("len(scene.models)")).result, "0");
});
