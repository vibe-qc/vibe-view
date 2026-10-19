// Runtime selection for downloaded macOS applications. Source launchers keep
// their existing environment discovery in main.js.
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

function bundledEnvironment(environment = process.env) {
  const clean = { ...environment };
  for (const key of Object.keys(clean)) {
    if (key.startsWith("PYTHON") || key.startsWith("DYLD_") ||
        ["VIRTUAL_ENV", "__PYVENV_LAUNCHER__", "RDBASE", "VIBEVIEW_DESKTOP_CONFIG"].includes(key)) {
      delete clean[key];
    }
  }
  return clean;
}

function resolveBundledPython(resources, version, arch = process.arch) {
  const python = path.join(resources, "python", "bin", "python3");
  try {
    const manifest = JSON.parse(fs.readFileSync(path.join(resources, "runtime.json"), "utf8"));
    if (manifest.appVersion !== version || manifest.arch !== arch || manifest.platform !== "darwin") {
      throw new Error("Runtime manifest does not match this application");
    }
    const probe = spawnSync(python, ["-I", "-B", "-c", [
      "import sys, json, pathlib, importlib.metadata as m",
      "import vibeview, pyvista, vtk, trame, uvicorn, rdkit",
      "root = pathlib.Path(sys.prefix).resolve()",
      "assert all(pathlib.Path(x.__file__).resolve().is_relative_to(root) for x in (vibeview, pyvista, vtk, uvicorn, rdkit))",
      "print(json.dumps({'python': sys.version.split()[0], 'packages': {n: m.version(n) for n in ('vibeview', 'pyvista', 'vtk', 'rdkit')}}))",
    ].join("\n")], { env: bundledEnvironment(), encoding: "utf8", timeout: 60000 });
    if (probe.status !== 0) throw new Error("Embedded Python or its rendering libraries failed to load");
    const result = JSON.parse(probe.stdout.trim());
    if (result.python !== manifest.pythonVersion || result.packages.vibeview !== version ||
        Object.entries(result.packages).some(([name, value]) => name !== "vibeview" && manifest.packages[name] !== value)) {
      throw new Error("Embedded Python packages do not match the runtime manifest");
    }
    return { ok: true, python };
  } catch (error) {
    console.error(`Bundled runtime: ${error.message}`);
    return { ok: false, reason: "bundle-error" };
  }
}

module.exports = { bundledEnvironment, resolveBundledPython };
