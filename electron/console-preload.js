"use strict";
const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("pythonConsole", {
  run: (source) => ipcRenderer.invoke("console:run", source),
  reset: () => ipcRenderer.invoke("console:reset"),
});
