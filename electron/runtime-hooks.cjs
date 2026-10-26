const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { Arch } = require("builder-util");

function supported(context) {
  if (context.electronPlatformName === "linux") {
    if (process.platform !== "linux" || process.arch !== "x64" || Arch[context.arch] !== "x64")
      throw new Error("Linux packages require a native x86-64 builder");
    return true;
  }
  if (context.electronPlatformName !== "darwin") return false;
  if (process.platform !== "darwin" || process.arch !== "arm64" || Arch[context.arch] !== "arm64") {
    throw new Error("The bundled macOS runtime currently requires a native Apple Silicon build");
  }
  return true;
}

function runtime(args) {
  execFileSync(process.env.VIBEVIEW_BUILD_PYTHON || "python3", [
    path.join(__dirname, "../scripts/build_desktop_runtime.py"), ...args,
  ], { stdio: "inherit" });
}

exports.beforePack = (context) => {
  if (supported(context)) runtime([]);
};

exports.afterPack = (context) => {
  if (!supported(context)) return;
  const resources = context.electronPlatformName === "linux" ? path.join(context.appOutDir, "resources") : path.join(context.appOutDir,
    `${context.packager.appInfo.productFilename}.app`, "Contents", "Resources");
  runtime(["--verify", resources]);
  const result = require("./python-runtime.cjs").resolveBundledPython(
    resources, context.packager.appInfo.version, Arch[context.arch]);
  if (!result.ok) throw new Error("Packaged runtime cannot start at its installed path");
};
