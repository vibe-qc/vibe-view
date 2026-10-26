"use strict";
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { ConsoleProcess } = require("./console-process.cjs");

function isConsoleSender(event, window, url) {
  return Boolean(window && !window.isDestroyed() && event.sender === window.webContents &&
    event.senderFrame && event.senderFrame === window.webContents.mainFrame && event.senderFrame.url === url);
}

function createPythonConsole({ BrowserWindow, ipcMain, launch, parent }) {
  let window = null;
  const process = new ConsoleProcess(launch);
  const filename = path.join(__dirname, "console.html");
  const url = pathToFileURL(filename).href;
  function check(event) {
    if (!isConsoleSender(event, window, url)) throw new Error("Console IPC denied");
  }
  ipcMain.handle("console:run", (event, source) => { check(event); return process.run(source); });
  ipcMain.handle("console:reset", (event) => { check(event); process.reset(); return true; });
  return {
    show() {
      if (window && !window.isDestroyed()) { window.show(); window.focus(); return; }
      window = new BrowserWindow({ width: 860, height: 650, parent: parent() || undefined,
        title: "vibe-view Python Console", backgroundColor: "#1a1a2e",
        webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true,
          preload: path.join(__dirname, "console-preload.js") } });
      window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
      window.webContents.on("will-navigate", (event) => event.preventDefault());
      window.on("closed", () => { process.reset(); window = null; });
      window.loadFile(filename);
    },
    close() { process.reset(); if (window && !window.isDestroyed()) window.close(); },
  };
}
module.exports = { createPythonConsole, isConsoleSender };
