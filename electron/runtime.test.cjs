const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { bundledEnvironment, resolveBundledPython } = require("./python-runtime.cjs");

test("embedded Python cannot inherit interpreter or dynamic-loader overrides", () => {
  const original = { PATH: "/usr/bin:/bin", HOME: "/tmp/profile", PYTHONHOME: "/wrong",
    PYTHONPATH: "/wrong", PYTHONUSERBASE: "/wrong", DYLD_LIBRARY_PATH: "/wrong", LD_LIBRARY_PATH: "/wrong", LD_PRELOAD: "/wrong",
    __PYVENV_LAUNCHER__: "/wrong", VIRTUAL_ENV: "/wrong", RDBASE: "/wrong",
    VIBEVIEW_DESKTOP_CONFIG: "/wrong/config.json" };
  assert.deepEqual(bundledEnvironment(original), { PATH: original.PATH, HOME: original.HOME });
  assert.equal(original.PYTHONHOME, "/wrong");
});

test("a missing or mismatched bundle fails without searching the host", (t) => {
  const resources = fs.mkdtempSync(path.join(os.tmpdir(), "viewer runtime "));
  t.after(() => fs.rmSync(resources, { recursive: true, force: true }));
  assert.deepEqual(resolveBundledPython(resources, "1.0", "arm64"),
    { ok: false, reason: "bundle-error" });
  fs.writeFileSync(path.join(resources, "runtime.json"), JSON.stringify({
    appVersion: "0.9", platform: "darwin", arch: "arm64",
  }));
  assert.equal(resolveBundledPython(resources, "1.0", "arm64").ok, false);
  fs.writeFileSync(path.join(resources, "runtime.json"), JSON.stringify({
    appVersion: "1.0", platform: "darwin", arch: "x64",
  }));
  assert.equal(resolveBundledPython(resources, "1.0", "arm64").ok, false);
});
