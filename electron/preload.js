// Preload for the first-run setup / onboarding page (setup.html).
//
// The main window runs the trusted vibe-view server pages with
// nodeIntegration:false + contextIsolation:true, so the setup page — which
// needs to talk back to the main process (recheck the environment, copy the
// install command, open the docs, quit) — gets this minimal, audited bridge
// instead of Node access. Everything here is a thin wrapper over an ipcRenderer
// call registered in main.js; no Node API is exposed to the page.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("vibeview", {
  // Re-scan for a vibe-view-capable interpreter. Resolves to either
  //   { ok: true }                       → main is starting the server + app
  //   { ok: false, reason, python, ... } → still not ready; update the page
  recheck: () => ipcRenderer.invoke("setup:recheck"),
  // Copy text (the install command) to the clipboard via the main process.
  copy: (text) => ipcRenderer.invoke("setup:copy", String(text)),
  // Open the online install docs in the user's real browser.
  openDocs: () => ipcRenderer.invoke("setup:open-docs"),
  // Quit the app from the setup page.
  quit: () => ipcRenderer.send("setup:quit"),
});
