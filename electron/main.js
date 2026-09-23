// vibe-view Electron desktop app
//
// Launched two ways:
//   1. `vibe-view desktop [file] [--port N]` — the Python CLI writes a
//      JSON handshake config and points VIBEVIEW_DESKTOP_CONFIG at it:
//      { port, file, python, cwd, version, codename }. That tells us which port to
//      use, which interpreter runs the server (the venv that launched
//      us), which directory to browse, and which file to open at startup.
//   2. `npm start` from this directory — no config; falls back to
//      auto-detected python3 + port 8765 + cwd.
const {
  app,
  BrowserWindow,
  Menu,
  Tray,
  dialog,
  shell,
  screen,
  ipcMain,
  clipboard,
} = require("electron");
const path = require("path");
const fs = require("fs");
const os = require("os");
const { spawn } = require("child_process");
const http = require("http");
let autoUpdater = null; try { autoUpdater = require("electron-updater").autoUpdater; } catch(e) {}
const HAS_SOURCE_APP_MARKER = fs.existsSync(
  path.join(process.resourcesPath, "app", "vibe-view-source-app.json"),
);
// The published feed currently contains macOS arm64 artifacts only. Keep
// local Intel, Linux, and Windows packages away from an incompatible or
// missing feed until their replacement artifacts are actually published.
const HAS_PUBLISHED_UPDATE_FEED =
  process.platform === "darwin" && process.arch === "arm64";
const USE_PACKAGED_UPDATER =
  app.isPackaged && !HAS_SOURCE_APP_MARKER && HAS_PUBLISHED_UPDATE_FEED;

// ── Launcher handshake config ─────────────────────────────────────────

function loadDesktopConfig() {
  const configPath = process.env.VIBEVIEW_DESKTOP_CONFIG;
  if (!configPath) return {};
  try {
    return JSON.parse(fs.readFileSync(configPath, "utf8"));
  } catch (e) {
    console.error(`Could not read desktop config ${configPath}: ${e.message}`);
    return {};
  }
}

const CONFIG = loadDesktopConfig();
let SERVER_PORT = CONFIG.port || 8765;
let SERVER_URL = `http://127.0.0.1:${SERVER_PORT}`;

// A crashed or tray-forgotten session can leave an old `vibe-view serve`
// squatting on the default port for days; a fresh launch then collides and
// dies silently (seen 2026-07-16: a 6-day-old server on 8765 blocked every
// dock launch). If the port is taken, move to an OS-assigned free one.
function ensureFreePort() {
  const net = require("net");
  return new Promise((resolve) => {
    const probe = net
      .createServer()
      .once("error", () => {
        // Port taken (stale/foreign server) — grab an ephemeral one instead.
        const eph = net.createServer();
        eph.once("listening", () => {
          const port = eph.address().port;
          eph.close(() => {
            console.warn(
              `port ${SERVER_PORT} is already in use (stale vibe-view server?) — using ${port}`,
            );
            SERVER_PORT = port;
            SERVER_URL = `http://127.0.0.1:${SERVER_PORT}`;
            resolve(true);
          });
        });
        eph.once("error", () => resolve(false)); // give up, keep original port
        eph.listen(0, "127.0.0.1");
      })
      .once("listening", () => probe.close(() => resolve(true)));
    probe.listen(SERVER_PORT, "127.0.0.1");
  });
}
// Prefer the version the launching CLI reports (the vibe-view Python package);
// otherwise fall back to the packaged app's own version (from this directory's
// package.json) rather than a hardcoded string that silently goes stale.
const APP_VERSION = CONFIG.version || app.getVersion();

// The release codename. `vibe-view desktop` puts it in the handshake config
// alongside the version, so Python's vibeview.codenames catalogue is the one
// source of truth for the About box too. FALLBACK_CODENAME covers a
// double-click launch, where there is no config at all and nothing set
// VIBEVIEW_DESKTOP_CONFIG -- it is the only copy of the string left in this
// file, and tests/test_release_codenames.py asserts it equals what the
// catalogue resolves for the current version.
const FALLBACK_CODENAME = "Richardson's Robin";
const APP_CODENAME = CONFIG.codename || FALLBACK_CODENAME;

app.setName("vibe-view");

let mainWindow = null;
let setupWindow = null; // first-run onboarding window (setup.html)
let tray = null;
let serverProcess = null;
let autoUpdaterStarted = false;
let currentFile = null; // absolute path of the loaded file, for the title
const STATE_FILE = path.join(app.getPath("userData"), "window-state.json");

// ── Recent files — shared with the Python CLI ────────────────────────
// Same file the `vibe-view recent` command and `vibeview.recent` module
// use: a JSON list of absolute paths, most recent first, capped at 50.

function recentFilePath() {
  const xdg = process.env.XDG_CACHE_HOME;
  const base = xdg || path.join(os.homedir(), ".cache");
  return path.join(base, "vibe-view", "recent.json");
}

function loadRecentFiles() {
  try {
    const data = JSON.parse(fs.readFileSync(recentFilePath(), "utf8"));
    if (Array.isArray(data)) {
      return data.filter((p) => typeof p === "string" && fs.existsSync(p));
    }
  } catch (e) {
    /* missing or unreadable → empty */
  }
  return [];
}

function addRecentFile(fp) {
  const abs = path.resolve(fp);
  let recent = loadRecentFiles().filter((p) => p !== abs);
  recent.unshift(abs);
  recent = recent.slice(0, 50);
  try {
    fs.mkdirSync(path.dirname(recentFilePath()), { recursive: true });
    fs.writeFileSync(recentFilePath(), JSON.stringify(recent, null, 2));
  } catch (e) {
    /* non-fatal */
  }
  app.addRecentDocument(abs); // native macOS dock menu / Windows jump list
}

// ── Window state persistence ──────────────────────────────────────────

function loadWindowState() {
  try {
    if (fs.existsSync(STATE_FILE)) {
      return JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
    }
  } catch (e) {
    /* ignore */
  }
  return null;
}

function saveWindowState() {
  if (!mainWindow) return;
  const bounds = mainWindow.getBounds();
  const state = {
    x: bounds.x,
    y: bounds.y,
    width: bounds.width,
    height: bounds.height,
    isMaximized: mainWindow.isMaximized(),
  };
  try {
    fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
  } catch (e) {
    /* ignore */
  }
}

// ── Python server management ──────────────────────────────────────────

// Persistent interpreter record, written by `vibe-view desktop` (see the
// Python CLI). Lets a plain double-click reuse the venv vibe-view was
// installed in, since double-click sets no VIBEVIEW_DESKTOP_CONFIG.
function interpreterConfigPath() {
  const xdg = process.env.XDG_CACHE_HOME;
  const base = xdg || path.join(os.homedir(), ".cache");
  return path.join(base, "vibe-view", "interpreter.json");
}

// App-managed virtualenv. The setup screen (setup.html) tells the user to
// create THIS venv and install vibe-view into it. A fresh venv is never
// PEP 668 "externally managed", so `pip install` works there even when the
// system Python (Homebrew / Debian / Fedora …) refuses it with
// "externally-managed-environment". findPython discovers it, so a Recheck
// picks it up automatically — no separate `vibe-view desktop` step needed.
function managedVenvDir() {
  const base =
    process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share");
  return path.join(base, "vibe-view", "venv");
}
function managedVenvPython() {
  return process.platform === "win32"
    ? path.join(managedVenvDir(), "Scripts", "python.exe")
    : path.join(managedVenvDir(), "bin", "python3");
}

// A candidate interpreter is only usable if `import vibeview` actually
// succeeds in it — checking `--version` (as before) picked the first
// python3 on PATH even when vibe-view was in a venv, which is exactly why
// the server failed to start on double-click.
function pythonHasVibeview(cmd) {
  if (!cmd) return false;
  try {
    const r = require("child_process").spawnSync(
      cmd,
      ["-c", "import sys; assert sys.version_info >= (3, 11); import vibeview"],
      { timeout: 10000 },
    );
    return r.status === 0;
  } catch (e) {
    return false;
  }
}

// Return details only for an interpreter new enough to run vibe-view. Keep
// probing after an old system `python3`: macOS and Linux commonly also have a
// usable versioned command such as python3.12.
function inspectSupportedPython(cmd) {
  try {
    const r = require("child_process").spawnSync(
      cmd,
      [
        "-c",
        "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')",
      ],
      { timeout: 8000 },
    );
    if (r.status !== 0) return null;
    const raw = (r.stdout || "").toString().trim();
    const parts = raw.split(".").map(Number);
    if (parts.length < 2 || parts.some((part) => !Number.isInteger(part))) return null;
    if (parts[0] < 3 || (parts[0] === 3 && parts[1] < 11)) return null;
    return { cmd, version: `Python ${raw}` };
  } catch (e) {
    return null;
  }
}

// Interpreter names to probe on PATH. Windows leads with the `py` launcher
// and plain `python`; there `python3` is usually absent (or a Microsoft Store
// stub), so it goes last. POSIX leads with `python3` + versioned names.
const PYTHON_CANDIDATES =
  process.platform === "win32"
    ? ["py", "python", "python3"]
    : ["python3", "python", "python3.13", "python3.12", "python3.11", "python3.14"];

function findPython() {
  // 1. Live launcher handshake (`vibe-view desktop`): its own interpreter.
  if (CONFIG.python && pythonHasVibeview(CONFIG.python)) return CONFIG.python;
  // 2. Interpreter recorded by a previous `vibe-view desktop` run.
  try {
    const rec = JSON.parse(fs.readFileSync(interpreterConfigPath(), "utf8"));
    if (rec.python && pythonHasVibeview(rec.python)) return rec.python;
  } catch (e) {
    /* none recorded yet */
  }
  // 3. App-managed venv the setup screen guides the user to create (PEP 668-
  //    safe; see managedVenvDir). Preferred over a bare PATH interpreter.
  const managed = managedVenvPython();
  if (pythonHasVibeview(managed)) return managed;
  // 4. Dev mode (npm start): sibling vibe-qc venv two levels up. The venv
  //    layout differs by OS: Scripts\python.exe on Windows, bin/python3 on POSIX.
  const venvPython =
    process.platform === "win32"
      ? path.join(__dirname, "..", "..", ".venv", "Scripts", "python.exe")
      : path.join(__dirname, "..", "..", ".venv", "bin", "python3");
  if (pythonHasVibeview(venvPython)) return venvPython;
  // 5. First PATH interpreter that actually imports vibeview.
  for (const cmd of PYTHON_CANDIDATES) {
    if (pythonHasVibeview(cmd)) return cmd;
  }
  return null; // no vibe-view-capable interpreter found
}

// Any supported Python on PATH (even without vibe-view), so the onboarding
// page can distinguish a missing package from no Python 3.11+ installation.
function findAnyPython() {
  for (const cmd of PYTHON_CANDIDATES) {
    const supported = inspectSupportedPython(cmd);
    if (supported) return supported;
  }
  return null;
}

// `serve` needs the [viewer] extra (interactive trame/uvicorn stack); a bare
// `import vibeview` (findPython) can succeed on the lean headless install and
// then the server fails to start. Detect that up front for a precise message.
function pythonHasViewer(cmd) {
  if (!cmd) return false;
  try {
    const r = require("child_process").spawnSync(
      cmd,
      ["-c", "import vibeview, trame, uvicorn"],
      { timeout: 12000 },
    );
    return r.status === 0;
  } catch (e) {
    return false;
  }
}

// Decide whether we can launch, or which onboarding state the setup page shows.
//   { ok: true, python }                       → ready to serve
//   { ok: false, reason: "no-python" }          → no interpreter at all
//   { ok: false, reason: "no-vibeview", ... }   → Python present, vibe-view not
//   { ok: false, reason: "no-viewer", python }  → vibe-view present, [viewer] not
function resolveEnvironment() {
  const py = findPython();
  if (!py) {
    const any = findAnyPython();
    return any
      ? { ok: false, reason: "no-vibeview", python: any.cmd, version: any.version }
      : { ok: false, reason: "no-python" };
  }
  if (!pythonHasViewer(py)) {
    return { ok: false, reason: "no-viewer", python: py };
  }
  return { ok: true, python: py };
}

// Pick the directory the browse page starts in. `vibe-view desktop` passes
// its own cwd through the handshake config; a Dock/Finder double-click has
// no config and the launcher cwd is the filesystem root, which is read-only
// (a New-structure click there fails with EROFS) — browse the home directory
// instead of presenting `/` as a work area.
function serveDirectory() {
  if (CONFIG.cwd && fs.existsSync(CONFIG.cwd)) return CONFIG.cwd;
  const cwd = process.cwd();
  if (cwd !== path.parse(cwd).root) return cwd;
  return os.homedir();
}

function startServer(pythonCmd) {
  if (!pythonCmd) return false; // caller resolves the interpreter first
  try {
    serverProcess = spawn(
      pythonCmd,
      ["-m", "vibeview.cli", "serve", "--port", String(SERVER_PORT)],
      {
        // Serve the directory the user launched from, so the browse
        // page lists *their* .qvf files — not the electron/ folder.
        cwd: serveDirectory(),
        stdio: ["pipe", "pipe", "pipe"],
        env: { ...process.env },
      },
    );
  } catch (e) {
    console.error("Failed to spawn vibe-view server:", e.message);
    return false;
  }

  serverProcess.stdout.on("data", (data) => {
    console.log(`[vibe-view] ${data.toString().trim()}`);
  });
  serverProcess.stderr.on("data", (data) => {
    console.log(`[vibe-view:err] ${data.toString().trim()}`);
  });
  // A spawn/runtime failure surfaces to the user through the onboarding window
  // (launch() falls back to it when the server never becomes ready); log here.
  serverProcess.on("error", (err) => {
    console.error("Failed to start vibe-view server:", err.message);
  });
  return true;
}

function stopServer() {
  if (serverProcess) {
    serverProcess.kill();
    serverProcess = null;
  }
}

// ── First-run onboarding (setup.html) ────────────────────────────────────
//
// A downloaded app can land on a machine with no Python, or a Python without
// vibe-view / without the [viewer] extra. Rather than a dead-end error box +
// quit, we show an in-app page that says exactly what to install and lets the
// user fix it and retry in place (see setup.html / preload.js).

function showSetupWindow(state) {
  const query = {
    reason: state.reason || "no-vibeview",
    // The managed venv path + platform let the page render an OS-correct,
    // PEP 668-safe install recipe the user can copy.
    venv: managedVenvDir(),
    plat: process.platform,
    appVersion: app.getVersion(),
  };
  if (state.python) query.python = state.python;
  if (state.version) query.version = state.version;

  if (setupWindow) {
    setupWindow.loadFile(path.join(__dirname, "setup.html"), { query });
    setupWindow.show();
    setupWindow.focus();
    return;
  }

  setupWindow = new BrowserWindow({
    width: 720,
    height: 600,
    minWidth: 560,
    minHeight: 480,
    title: "Set up vibe-view",
    backgroundColor: "#1a1a2e",
    icon: path.join(__dirname, "icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      nodeIntegration: false,
      contextIsolation: true,
    },
    show: false,
  });
  setupWindow.once("ready-to-show", () => setupWindow.show());
  setupWindow.on("closed", () => {
    setupWindow = null;
  });
  setupWindow.loadFile(path.join(__dirname, "setup.html"), { query });
}

// Resolve the environment, then either start the server + main window, or fall
// back to the onboarding window. Safe to call repeatedly (e.g. from a recheck).
async function launch() {
  const env = resolveEnvironment();
  if (!env.ok) {
    showSetupWindow(env);
    return;
  }
  // Dodge stale/foreign servers before spawning ours (see ensureFreePort).
  try {
    await ensureFreePort();
  } catch (e) {
    /* keep the configured port; startServer/waitForServer will report */
  }
  if (setupWindow) {
    setupWindow.close();
    setupWindow = null;
  }
  if (!startServer(env.python)) {
    showSetupWindow({ reason: "server-error", python: env.python });
    return;
  }
  if (!autoUpdaterStarted) {
    setupAutoUpdater();
    autoUpdaterStarted = true;
  }
  try {
    await waitForServer(SERVER_URL, 40, 500);
    console.log("vibe-view server is ready");
  } catch (e) {
    console.error(e.message);
    stopServer();
    showSetupWindow({ reason: "server-error", python: env.python });
    return;
  }
  createWindow(pendingOpenFile);
  pendingOpenFile = null;
}

const ISSUES_URL = "https://github.com/vibe-qc/vibe-view/issues";

// Windows is a supported-but-unverified target: vibe-view is developed and
// tested on macOS + Linux, and no maintainer has a Windows machine. Tell each
// Windows user once, up front, that the build is untested and invite feedback.
// Gated by a flag file in userData so it shows only on the first Windows run.
function maybeShowWindowsNotice() {
  if (process.platform !== "win32") return;
  const flag = path.join(app.getPath("userData"), "windows-notice-shown");
  try {
    if (fs.existsSync(flag)) return;
  } catch (e) {
    /* if we can't stat it, still show once rather than never */
  }
  dialog
    .showMessageBox({
      type: "info",
      title: "vibe-view on Windows is experimental",
      message: "Windows support is untested — your feedback helps.",
      detail:
        "vibe-view is developed and tested on macOS and Linux. The Windows " +
        "build is shipped but has not yet been verified on Windows, and its " +
        "installer is not code-signed (SmartScreen may warn on first run).\n\n" +
        "The developer has no Windows machine and so cannot test or reproduce " +
        "Windows problems directly — but is happy to help anyone who wants to " +
        "try. Open an issue and you will get support working through whatever " +
        "you hit.\n\n" +
        "Please tell us how it goes — reporting what breaks (or what works) is " +
        "what will make Windows a first-class target.",
      buttons: ["OK", "Send feedback"],
      defaultId: 0,
      cancelId: 0,
    })
    .then(({ response }) => {
      if (response === 1) shell.openExternal(ISSUES_URL);
    })
    .catch(() => {});
  try {
    fs.mkdirSync(path.dirname(flag), { recursive: true });
    fs.writeFileSync(flag, "shown\n");
  } catch (e) {
    /* non-fatal: worst case the notice shows again next launch */
  }
}

// IPC from the onboarding page (preload.js). Registered once at startup.
function registerSetupIpc() {
  ipcMain.handle("setup:recheck", async () => {
    const env = resolveEnvironment();
    if (env.ok) {
      // Reply first, then start the server + close the setup window.
      setImmediate(() => launch());
      return { ok: true };
    }
    return {
      ok: false,
      reason: env.reason,
      python: env.python || "",
      version: env.version || "",
    };
  });
  ipcMain.handle("setup:copy", (_e, text) => {
    clipboard.writeText(String(text || ""));
    return true;
  });
  ipcMain.handle("setup:open-docs", () => {
    shell.openExternal(
      "https://vibe-qc.com/vibe-view/docs/desktop.html",
    );
    return true;
  });
  ipcMain.on("setup:quit", () => {
    app.isQuitting = true;
    app.quit();
  });
}

function waitForServer(url, retries = 30, interval = 500) {
  return new Promise((resolve, reject) => {
    let attempts = 0;
    const check = () => {
      attempts++;
      http
        .get(url, (res) => {
          resolve(true);
        })
        .on("error", () => {
          if (attempts >= retries) {
            reject(new Error(`Server did not start after ${retries} attempts`));
          } else {
            setTimeout(check, interval);
          }
        });
    };
    check();
  });
}

// ── File opening ──────────────────────────────────────────────────────

function isTrexioDirectory(dir) {
  try {
    return ["metadata.txt", "nucleus.txt"].every((name) =>
      fs.statSync(path.join(dir, name)).isFile(),
    );
  } catch {
    return false;
  }
}

function openFolder(dir) {
  const abs = path.resolve(dir);
  if (isTrexioDirectory(abs)) {
    openFile(abs);
    return;
  }
  currentFile = null;
  createWindow();
  mainWindow.loadURL(`${SERVER_URL}/browse?dir=${encodeURIComponent(abs)}`);
  updateTitle();
}

function openFile(fp) {
  const abs = path.resolve(fp);
  if (!fs.existsSync(abs)) {
    dialog.showErrorBox("vibe-view", `File not found:\n${abs}`);
    return;
  }
  if (fs.statSync(abs).isDirectory() && !isTrexioDirectory(abs)) {
    openFolder(abs); // a dropped/opened folder browses, not /open
    return;
  }
  currentFile = abs;
  addRecentFile(abs);
  createMenu(); // refresh the Open Recent submenu
  createWindow();
  // The server's /open endpoint spawns a per-file 3D viewer process
  // and redirects there once it is up (loading page in between).
  mainWindow.loadURL(`${SERVER_URL}/open?file=${encodeURIComponent(abs)}`);
  updateTitle();
}

function updateTitle() {
  if (!mainWindow) return;
  mainWindow.setTitle(
    currentFile ? `${path.basename(currentFile)} — vibe-view` : "vibe-view",
  );
}

async function showOpenDialog(filters) {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Open File",
    filters,
    properties: ["openFile"],
  });
  if (!result.canceled && result.filePaths.length > 0) {
    openFile(result.filePaths[0]);
  }
}

// ── Window management ──────────────────────────────────────────────────

function createWindow(fileToOpen = null) {
  if (mainWindow) {
    if (fileToOpen) {
      openFile(fileToOpen);
      return;
    }
    mainWindow.show();
    mainWindow.focus();
    return;
  }

  const saved = loadWindowState();
  const winOpts = {
    width: saved?.width || 1400,
    height: saved?.height || 900,
    minWidth: 800,
    minHeight: 600,
    title: "vibe-view",
    backgroundColor: "#1a1a2e",
    icon: path.join(__dirname, "icon.png"),
    webPreferences: { nodeIntegration: false, contextIsolation: true },
    show: false,
  };
  if (saved?.x !== undefined && saved?.y !== undefined) {
    winOpts.x = saved.x;
    winOpts.y = saved.y;
  }

  mainWindow = new BrowserWindow(winOpts);
  if (saved?.isMaximized) mainWindow.maximize();

  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.on("close", () => saveWindowState());
  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  // ── Render-process hang / crash recovery ──
  // The 3D view renders client-side in WebGL (vtk.js via VtkLocalView).
  // A heavy scene — a large MO isosurface, a big supercell — can hang or
  // crash that render process, and without a handler the window is just
  // frozen with no way out. Turn both into a recoverable "Reload" prompt.
  let unresponsivePrompted = false;
  mainWindow.on("unresponsive", () => {
    if (unresponsivePrompted || !mainWindow) return;
    unresponsivePrompted = true;
    dialog
      .showMessageBox(mainWindow, {
        type: "warning",
        title: "vibe-view",
        message: "The 3D view stopped responding.",
        detail:
          "This usually means a heavy scene (a large orbital isosurface or " +
          "supercell) overloaded the graphics view. Reload the window, or " +
          "wait to see if it recovers.",
        buttons: ["Reload", "Wait"],
        defaultId: 0,
        cancelId: 1,
      })
      .then((r) => {
        unresponsivePrompted = false;
        if (r.response === 0 && mainWindow) mainWindow.webContents.reload();
      })
      .catch(() => {
        unresponsivePrompted = false;
      });
  });
  mainWindow.on("responsive", () => {
    unresponsivePrompted = false;
  });
  mainWindow.webContents.on("render-process-gone", (_event, details) => {
    if (!mainWindow) return;
    dialog
      .showMessageBox(mainWindow, {
        type: "error",
        title: "vibe-view",
        message: "The 3D view crashed and was reloaded.",
        detail: `Reason: ${details.reason}. If this keeps happening on a ` +
          "particular orbital or supercell, try turning off Ambient " +
          "occlusion (SSAO) in the Display panel or reducing the grid detail.",
        buttons: ["OK"],
      })
      .catch(() => {});
    mainWindow.webContents.reload();
  });

  // Keep our "<file> — vibe-view" title; server pages set their own.
  mainWindow.webContents.on("page-title-updated", (event) => {
    event.preventDefault();
    updateTitle();
  });

  // ── Drag-and-drop ──
  // Dropping a file onto the window makes Chromium navigate to its
  // file:// URL; intercept that and route through the /open endpoint.
  mainWindow.webContents.on("will-navigate", (event, url) => {
    const parsed = new URL(url);
    if (parsed.protocol === "file:") {
      event.preventDefault();
      openFile(decodeURIComponent(parsed.pathname));
      return;
    }
    // In-page navigation to /open?file=… (a click on the browse page):
    // let it through, but pick up the filename for the title + recents.
    const fileParam = parsed.searchParams && parsed.searchParams.get("file");
    if (fileParam && fs.existsSync(fileParam)) {
      currentFile = path.resolve(fileParam);
      addRecentFile(currentFile);
      createMenu();
      updateTitle();
    }
  });

  if (fileToOpen) {
    openFile(fileToOpen);
  } else {
    mainWindow.loadURL(SERVER_URL); // browse page with quickstart panel
    updateTitle();
  }
}

// ── System tray ────────────────────────────────────────────────────────

function createTray() {
  const { nativeImage } = require("electron");
  // macOS menu-bar icons must be small (~22px). Passing the full 512x512
  // app icon straight to Tray renders it oversized and lets it claim the
  // whole status-menu area — resize to the menu-bar size first.
  let trayIcon = nativeImage.createEmpty();
  try {
    const img = nativeImage.createFromPath(path.join(__dirname, "icon.png"));
    if (!img.isEmpty()) trayIcon = img.resize({ width: 22, height: 22 });
  } catch (e) {
    /* fall back to an empty (invisible) tray icon */
  }
  try {
    tray = new Tray(trayIcon);
  } catch (e) {
    tray = new Tray(nativeImage.createEmpty());
  }

  const contextMenu = Menu.buildFromTemplate([
    {
      label: "Show vibe-view",
      click: () => createWindow(),
    },
    {
      label: "Open QVF or TREXIO File...",
      click: () => showOpenDialog([
        { name: "QVF and TREXIO Files", extensions: ["qvf", "trexio", "h5", "hdf5"] },
      ]),
    },
    { type: "separator" },
    {
      label: "Check for Updates",
      click: () => checkForUpdates(),
    },
    { type: "separator" },
    {
      label: "Quit vibe-view",
      click: () => {
        app.isQuitting = true;
        app.quit();
      },
    },
  ]);
  tray.setToolTip("vibe-view");
  tray.setContextMenu(contextMenu);
  tray.on("double-click", () => createWindow());
}

// ── Menu ───────────────────────────────────────────────────────────────

function buildRecentSubmenu() {
  const recent = loadRecentFiles().slice(0, 10);
  const items = recent.map((fp) => ({
    label: path.basename(fp),
    sublabel: path.dirname(fp),
    click: () => openFile(fp),
  }));
  if (items.length === 0) {
    items.push({ label: "No Recent Files", enabled: false });
  } else {
    items.push({ type: "separator" });
    items.push({
      label: "Clear Recent",
      click: () => {
        try {
          fs.writeFileSync(recentFilePath(), "[]");
        } catch (e) {
          /* ignore */
        }
        app.clearRecentDocuments();
        createMenu();
      },
    });
  }
  return items;
}

function createMenu() {
  const template = [
    {
      label: "File",
      submenu: [
        {
          label: "Open QVF or TREXIO File...",
          accelerator: "CmdOrCtrl+O",
          click: () =>
            showOpenDialog([
              { name: "QVF Files", extensions: ["qvf"] },
              { name: "TREXIO Files", extensions: ["trexio", "h5", "hdf5"] },
              { name: "All Files", extensions: ["*"] },
            ]),
        },
        {
          label: "Open vibe-qc Input (.py)...",
          click: () =>
            showOpenDialog([
              { name: "vibe-qc Input", extensions: ["py"] },
              {
                name: "Structures",
                extensions: ["xyz", "cif", "pdb", "mol2", "sdf", "mol", "gjf", "com", "cube"],
              },
            ]),
        },
        {
          label: "Open Folder...",
          accelerator: "CmdOrCtrl+Shift+O",
          click: async () => {
            const result = await dialog.showOpenDialog(mainWindow, {
              title: "Open Folder",
              properties: ["openDirectory"],
            });
            if (!result.canceled && result.filePaths.length > 0) {
              openFolder(result.filePaths[0]);
            }
          },
        },
        {
          label: "Open Recent",
          submenu: buildRecentSubmenu(),
        },
        { type: "separator" },
        {
          label: "Browse Directory",
          accelerator: "CmdOrCtrl+B",
          click: () => {
            currentFile = null;
            createWindow();
            mainWindow.loadURL(SERVER_URL);
            updateTitle();
          },
        },
        { type: "separator" },
        process.platform === "darwin" ? { role: "close" } : { role: "quit" },
      ],
    },
    {
      label: "View",
      submenu: [
        { role: "reload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { role: "resetZoom" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    {
      label: "Help",
      submenu: [
        {
          label: "vibe-view Documentation",
          click: () => shell.openExternal("https://vibe-qc.com/vibe-view/docs/"),
        },
        {
          label: "Check for Updates",
          click: () => checkForUpdates(),
        },
        { type: "separator" },
        {
          label: "About vibe-view",
          click: () => {
            dialog.showMessageBox(mainWindow, {
              type: "info",
              title: "About vibe-view",
              message: `vibe-view ${APP_VERSION} — ${APP_CODENAME}`,
              detail:
                "GPU-accelerated interactive 3D molecular viewer and editor.\nBuilt with PyVista + VTK + Trame + Electron.\n\nFeatures:\n• Interactive atom editor with undo/redo\n• Fragment library & crystal builder\n• 12 export formats (XYZ, POV-Ray, Blender, SVG, PDF, CML)\n• Symmetry detection (point groups)\n• POV-Ray/Blender scene export\n• vq job submission & monitoring\n• Jupyter notebook integration\n\nLicense: MPL 2.0",
            });
          },
        },
      ],
    },
  ];

  // macOS-specific menu adjustments
  if (process.platform === "darwin") {
    template.unshift({
      label: app.getName(),
      submenu: [
        { role: "about" },
        { type: "separator" },
        { role: "services" },
        { type: "separator" },
        { role: "hide" },
        { role: "hideOthers" },
        { role: "unhide" },
        { type: "separator" },
        { role: "quit" },
      ],
    });
  }

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ── Auto-updater ───────────────────────────────────────────────────────

// Menu "Check for Updates" handler. Packaged builds use their feed; source/dev
// builds always show the checkout updater instructions, even when npm install
// happened to make electron-updater importable.
function checkForUpdates() {
  if (USE_PACKAGED_UPDATER && autoUpdater) {
    autoUpdater.checkForUpdatesAndNotify();
    return;
  }
  dialog.showMessageBox(mainWindow, {
    type: "info",
    title: "vibe-view Updates",
    message: "Automatic updates are not configured in this build.",
    detail:
      "To update vibe-view:\n\n" +
      "•  From a source checkout: quit the desktop app, then run\n" +
      "   ./scripts/update-desktop.sh from the checkout root.\n\n" +
      "•  For a packaged app: use its published update feed or install\n" +
      "   the current replacement package for your platform.",
    buttons: ["OK"],
  });
}

// Base URL of the update feed. Must stay in sync with build.publish.url in
// package.json — electron-builder bakes that into app-update.yml for the actual
// update check; this copy is only used to build a human-facing download link
// for the non-Developer-ID macOS manual path below.
const UPDATE_FEED_URL = "https://vibe-qc.com/updates/vibe-view/";

// macOS only: can this build actually self-install an update? Squirrel.Mac
// refuses to APPLY an update unless the running app is signed with a
// "Developer ID Application" identity (ad-hoc builds are rejected),
// so offering "Restart to install" on such a build would be a dead button that
// silently does nothing. We probe the running bundle's signature; when it can't
// self-install we fall back to notify + manual download. Returns true on
// non-macOS, and lights up the real install path automatically once a
// Developer ID-signed build ships. Notarization remains a separate Gatekeeper
// requirement for distribution; no updater code change is needed then.
function macCanSelfInstall() {
  if (process.platform !== "darwin") return true;
  if (!app.isPackaged) return false; // dev / source launch: nothing to install
  try {
    const exe = app.getPath("exe"); // …/vibe-view.app/Contents/MacOS/vibe-view
    const appBundle = path.dirname(path.dirname(path.dirname(exe)));
    const { spawnSync } = require("child_process");
    // codesign writes the signing detail to stderr; capture both streams.
    const res = spawnSync("codesign", ["-dvv", appBundle], { encoding: "utf8" });
    const text = `${res.stdout || ""}${res.stderr || ""}`;
    return /Authority=Developer ID Application/.test(text);
  } catch (e) {
    return false; // codesign missing or errored → assume no self-install
  }
}

function setupAutoUpdater() {
  if (!USE_PACKAGED_UPDATER || !autoUpdater) return;

  // Feed URL is NOT set here. electron-builder bakes app-update.yml into the
  // packaged app from the build.publish config (generic provider →
  // UPDATE_FEED_URL), and electron-updater reads it automatically. A manual
  // setFeedURL() would only be needed for a build without that baked config.
  const canSelfInstall = macCanSelfInstall();

  autoUpdater.on("checking-for-update", () => {
    console.log("Checking for vibe-view updates...");
  });

  autoUpdater.on("update-not-available", () => {
    console.log("vibe-view is up to date.");
  });

  autoUpdater.on("error", (err) => {
    console.error("Auto-update error:", err.message);
  });

  if (!canSelfInstall) {
    // Unsigned / ad-hoc macOS build: Squirrel.Mac can't apply the update, so
    // don't background-download a payload we can't use. Detect + notify, and
    // point the user at the .dmg to reinstall by hand. (Once a Developer ID
    // build ships, macCanSelfInstall() returns true and the branch below runs
    // instead — real self-install with no code change.)
    autoUpdater.autoDownload = false;
    autoUpdater.on("update-available", (info) => {
      const dmg = (info.files || []).find((f) => f.url.endsWith(".dmg"));
      const dmgUrl = dmg ? UPDATE_FEED_URL + dmg.url : UPDATE_FEED_URL;
      dialog
        .showMessageBox(mainWindow, {
          type: "info",
          title: "Update Available",
          message: `vibe-view ${info.version} is available.`,
          detail:
            "This build isn't Developer ID-signed, so it can't install updates by " +
            "itself. Download the new version and replace the app to update.",
          buttons: ["Download…", "Later"],
          defaultId: 0,
          cancelId: 1,
        })
        .then(({ response }) => {
          if (response === 0) shell.openExternal(dmgUrl);
        });
    });
  } else {
    // Signed macOS build (or a non-macOS platform whose updater has no signing
    // gate): normal background download, then prompt to restart-and-install.
    autoUpdater.on("update-available", (info) => {
      dialog.showMessageBox(mainWindow, {
        type: "info",
        title: "Update Available",
        message: `vibe-view ${info.version} is available.`,
        detail: "It will be downloaded in the background.",
      });
    });

    autoUpdater.on("update-downloaded", () => {
      dialog
        .showMessageBox(mainWindow, {
          type: "info",
          title: "Update Ready",
          message: "vibe-view update downloaded.",
          detail: "Restart to apply the update.",
          buttons: ["Restart Now", "Later"],
          defaultId: 0,
          cancelId: 1,
        })
        .then(({ response }) => {
          if (response === 0) autoUpdater.quitAndInstall();
        });
    });
  }

  // Check for updates 5 seconds after startup
  setTimeout(() => {
    autoUpdater.checkForUpdates().catch(() => {});
  }, 5000);
}

// ── App lifecycle ──────────────────────────────────────────────────────

app.isQuitting = false;

if (process.platform === "darwin") {
  app.setAboutPanelOptions({
    applicationName: "vibe-view",
    applicationVersion: APP_VERSION,
    copyright: "MPL 2.0 — vibe-qc",
    credits: "GPU-accelerated 3D viewer for QVF and quantum-chemistry data.",
  });
}

// macOS: file dropped on the dock icon / opened via Finder association.
let pendingOpenFile = CONFIG.file || null;
app.on("open-file", (event, filePath) => {
  event.preventDefault();
  if (app.isReady() && serverProcess) {
    openFile(filePath);
  } else {
    pendingOpenFile = filePath; // picked up after the server is ready
  }
});

app.whenReady().then(async () => {
  if (process.platform === "darwin") {
    try {
      const { nativeImage } = require("electron");
      const dockIcon = nativeImage.createFromPath(path.join(__dirname, "icon.png"));
      if (!dockIcon.isEmpty()) app.dock.setIcon(dockIcon);
    } catch (e) {
      /* icon optional */
    }
  }

  createMenu();
  createTray();
  registerSetupIpc();
  // launch() starts the server + main window, or shows the onboarding window
  // (setup.html) when Python / vibe-view / the [viewer] extra is missing — the
  // app stays alive so the user can install and retry in place.
  await launch();
  maybeShowWindowsNotice(); // once-per-machine "Windows is untested" + feedback
});

app.on("window-all-closed", () => {
  // Keep running in tray on all platforms
  // (user must explicitly quit via tray or Cmd+Q)
});

app.on("activate", () => {
  createWindow();
});

app.on("before-quit", () => {
  app.isQuitting = true;
  stopServer();
  saveWindowState();
});
