"use strict";
const source = document.getElementById("source");
const output = document.getElementById("output");
const run = document.getElementById("run");
const status = document.getElementById("status");
function append(text) { output.textContent = (output.textContent + text).slice(-500000); output.scrollTop = output.scrollHeight; }
async function execute() {
  if (run.disabled) return;
  run.disabled = true;
  status.textContent = "Running";
  append(`>>> ${source.value}\n`);
  try {
    const result = await window.pythonConsole.run(source.value);
    append((result.stdout || "") + (result.stderr || "") + (result.result === null ? "" : result.result + "\n"));
    status.textContent = result.ok ? "Ready" : "Python error";
  } catch (error) { append(error.message + "\n"); status.textContent = "Stopped"; }
  finally { run.disabled = false; }
}
run.addEventListener("click", execute);
source.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); execute(); }
});
document.getElementById("reset").addEventListener("click", async () => {
  await window.pythonConsole.reset(); status.textContent = "Reset; variables cleared";
});
document.getElementById("clear").addEventListener("click", () => { output.textContent = ""; });
