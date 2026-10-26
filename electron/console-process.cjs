"use strict";
const { spawn } = require("node:child_process");

class ConsoleProcess {
  constructor(launch) {
    this.launch = launch;
    this.child = null;
    this.pending = null;
    this.nextId = 1;
  }
  start() {
    if (this.child) return;
    const spec = this.launch();
    const child = spawn(spec.python,
      [...(spec.args || []), "-u", "-m", "vibeview.desktop_console"],
      { cwd: spec.cwd, env: spec.env, stdio: ["pipe", "pipe", "pipe"], shell: false });
    this.child = child;
    let buffer = "", extra = "";
    const finish = (error) => {
      if (this.child !== child) return;
      this.child = null;
      if (this.pending) {
        this.pending.reject(error);
        this.pending = null;
      }
    };
    child.on("error", (error) => finish(error));
    child.on("close", (code) => finish(new Error(`Console process exited (${code})`)));
    child.stdin.on("error", (error) => finish(error));
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => { extra = (extra + chunk).slice(-100000); });
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      if (this.child !== child) return;
      buffer += chunk;
      if (buffer.length > 2000000) {
        this.reset("Console output exceeded the protocol limit");
        return;
      }
      let newline;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        let response;
        try { response = JSON.parse(line); } catch (_) { extra = (extra + line + "\n").slice(-100000); continue; }
        if (response.vibeview_console !== 1 || !this.pending || response.id !== this.pending.id) continue;
        const request = this.pending;
        this.pending = null;
        response.stderr = extra + (response.stderr || "");
        extra = "";
        request.resolve(response);
      }
    });
  }
  run(source) {
    if (typeof source !== "string" || source.length > 1000000)
      return Promise.reject(new Error("Invalid console source"));
    if (this.pending) return Promise.reject(new Error("Console is busy; wait or reset it"));
    try { this.start(); } catch (error) { return Promise.reject(error); }
    return new Promise((resolve, reject) => {
      const id = this.nextId++;
      this.pending = { id, resolve, reject };
      this.child.stdin.write(JSON.stringify({ id, source }) + "\n");
    });
  }
  reset(message = "Console reset; variables cleared") {
    const child = this.child;
    this.child = null;
    if (this.pending) { this.pending.reject(new Error(message)); this.pending = null; }
    if (child) child.kill("SIGKILL");
  }
}
module.exports = { ConsoleProcess };
