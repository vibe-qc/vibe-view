"""Desktop application packaging for vibe-view (v1.8).

Provides utilities for bundling vibe-view as a native desktop application
using Electron or PyInstaller.  Also handles file associations, system
tray integration, and auto-update checks.
"""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path
from typing import Any


def generate_electron_package_json(
    output_dir: str = "electron-build",
    app_name: str = "vibe-view",
    app_version: str = "2.1.0.dev0",
) -> str:
    """Generate a package.json for Electron-based desktop packaging.

    Returns the path to the generated file.
    """
    package = {
        "name": app_name,
        "version": app_version,
        "description": "GPU-accelerated 3D viewer for quantum-chemistry data",
        "main": "main.js",
        "scripts": {
            "start": "electron .",
            "pack": "electron-builder --dir",
            "dist": "electron-builder",
        },
        "build": {
            "appId": "com.vibeqc.vibeview",
            "productName": "vibe-view",
            "directories": {"output": "dist"},
            "files": [
                "main.js",
                "preload.js",
                "assets/**/*",
            ],
            "mac": {
                "category": "public.app-category.science",
                "target": ["dmg", "zip"],
                "icon": "assets/icon.icns",
                "hardenedRuntime": True,
                "entitlements": "assets/entitlements.plist",
            },
            "win": {
                "target": ["nsis", "msi"],
                "icon": "assets/icon.ico",
            },
            "linux": {
                "target": ["AppImage", "deb"],
                "icon": "assets/icon.png",
                "category": "Science",
            },
            "fileAssociations": [
                {"ext": "qvf", "name": "vibe-qc Visualization File", "description": "QVF Archive"},
                {"ext": "py", "name": "vibe-qc Input Script", "description": "Python Input"},
            ],
        },
        "devDependencies": {
            "electron": "^28.0.0",
            "electron-builder": "^24.0.0",
        },
    }

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    pkg_path = out / "package.json"
    pkg_path.write_text(json.dumps(package, indent=2))
    return str(pkg_path)


def generate_electron_main_js(output_dir: str = "electron-build", port: int = 8080) -> str:
    """Generate the Electron main process script.

    Returns the path to the generated file.
    """
    main_js = f"""// vibe-view Electron desktop app (v1.8)
const {{ app, BrowserWindow, Tray, Menu, shell, dialog }} = require('electron');
const path = require('path');
const {{ spawn }} = require('child_process');

let mainWindow = null;
let tray = null;
let serverProcess = null;
const SERVER_PORT = {port};

function startServer() {{
    // Launch the vibe-view Python server
    const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
    serverProcess = spawn(pythonCmd, [
        '-m', 'vibeview.cli', 'serve',
        '--port', String(SERVER_PORT),
    ], {{
        stdio: 'pipe',
        env: {{ ...process.env, PYTHONUNBUFFERED: '1' }},
    }});

    serverProcess.stderr.on('data', (data) => {{
        console.log(`[server] ${{data}}`);
    }});
}}

function createWindow() {{
    mainWindow = new BrowserWindow({{
        width: 1400,
        height: 900,
        minWidth: 800,
        minHeight: 600,
        title: 'vibe-view',
        icon: path.join(__dirname, 'assets', 'icon.png'),
        webPreferences: {{
            preload: path.join(__dirname, 'preload.js'),
            nodeIntegration: false,
            contextIsolation: true,
        }},
    }});

    mainWindow.loadURL(`http://localhost:${{SERVER_PORT}}`);

    mainWindow.on('closed', () => {{
        mainWindow = null;
    }});
}}

function createTray() {{
    const trayIcon = path.join(__dirname, 'assets', 'tray-icon.png');
    tray = new Tray(trayIcon);
    const contextMenu = Menu.buildFromTemplate([
        {{
            label: 'Show vibe-view',
            click: () => {{
                if (mainWindow) {{
                    mainWindow.show();
                    mainWindow.focus();
                }}
            }},
        }},
        {{
            label: 'Open File...',
            click: async () => {{
                const result = await dialog.showOpenDialog(mainWindow, {{
                    filters: [{{ name: 'QVF Files', extensions: ['qvf'] }}],
                    properties: ['openFile'],
                }});
                if (!result.canceled && result.filePaths.length > 0) {{
                    mainWindow.loadURL(
                        `http://localhost:${{SERVER_PORT}}?file=${{encodeURIComponent(result.filePaths[0])}}`
                    );
                }}
            }},
        }},
        {{ type: 'separator' }},
        {{
            label: 'Quit',
            click: () => {{
                app.quit();
            }},
        }},
    ]);
    tray.setToolTip('vibe-view');
    tray.setContextMenu(contextMenu);
}}

// Handle file association (macOS open-file event)
app.on('open-file', (event, filePath) => {{
    event.preventDefault();
    if (mainWindow) {{
        mainWindow.loadURL(
            `http://localhost:${{SERVER_PORT}}?file=${{encodeURIComponent(filePath)}}`
        );
    }}
}});

app.whenReady().then(() => {{
    startServer();

    // Wait for server to be ready
    setTimeout(() => {{
        createWindow();
        createTray();
    }}, 2000);
}});

app.on('window-all-closed', () => {{
    // Don't quit on macOS — keep the tray icon alive
    if (process.platform !== 'darwin') {{
        app.quit();
    }}
}});

app.on('activate', () => {{
    if (mainWindow === null) {{
        createWindow();
    }} else {{
        mainWindow.show();
    }}
}});

app.on('before-quit', () => {{
    if (serverProcess) {{
        serverProcess.kill();
    }}
}});
"""

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    main_path = out / "main.js"
    main_path.write_text(main_js)
    return str(main_path)


def generate_electron_preload_js(output_dir: str = "electron-build") -> str:
    """Generate the Electron preload script for secure IPC."""
    preload = """// vibe-view Electron preload script (v1.8)
const {{ contextBridge, ipcRenderer }} = require('electron');

contextBridge.exposeInMainWorld('vibeview', {{
    openFile: (filePath) => ipcRenderer.invoke('open-file', filePath),
    saveFile: (data, defaultName) => ipcRenderer.invoke('save-file', {{ data, defaultName }}),
    getAppVersion: () => ipcRenderer.invoke('get-version'),
    checkForUpdates: () => ipcRenderer.invoke('check-updates'),
}});
"""

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    preload_path = out / "preload.js"
    preload_path.write_text(preload)
    return str(preload_path)


def check_for_updates(current_version: str = "2.1.0.dev0") -> dict[str, Any]:
    """Check vibe-qc.com for newer versions.

    In a real deployment, this would fetch a version manifest from
    the server.  This is a stub that returns the expected format.
    """
    return {
        "current_version": current_version,
        "latest_version": current_version,  # Stub: same version
        "update_available": False,
        "download_url": f"https://vibe-qc.com/download/vibe-view-{current_version}/",
        "changelog_url": "https://vibe-qc.com/changelog/",
    }


def get_system_info() -> dict[str, str]:
    """Return OS and Python info for debugging."""
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "python_version": sys.version,
        "python_implementation": platform.python_implementation(),
    }


def launch_desktop_app() -> bool:
    """Launch the Electron desktop app.

    Finds the ``electron/`` directory relative to the vibe-view source
    tree, then runs ``npm start`` to spawn Electron.  The Electron app
    in turn starts ``vibe-view serve`` as its backend.

    Returns True if the Electron process was spawned successfully,
    False otherwise (missing npm, missing electron directory, etc.).
    """
    import shutil
    import subprocess

    from vibeview.install_hints import source_project_dir

    project = source_project_dir()
    electron_dir = (
        project / "electron"
        if project is not None
        else Path(__file__).resolve().parent.parent.parent / "electron"
    )
    if not electron_dir.is_dir():
        print(
            "Electron app not found at",
            electron_dir,
            "\nRun from the vibe-view checkout root:  cd electron && npm install",
        )
        return False

    npm = shutil.which("npm")
    if not npm:
        print("npm not found. Install Node.js from https://nodejs.org/")
        return False

    # Check that node_modules exist; if not, try to install.
    if not (electron_dir / "node_modules").is_dir():
        print("Installing npm dependencies (one-time)...")
        try:
            subprocess.run(
                [npm, "install"],
                cwd=str(electron_dir),
                check=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"npm install failed: {e}")
            return False

    try:
        subprocess.Popen(
            [npm, "start"],
            cwd=str(electron_dir),
            start_new_session=True,
        )
        return True
    except Exception as e:
        print(f"Failed to launch desktop app: {e}")
        return False
