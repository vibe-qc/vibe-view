"""Contract tests for the standalone vibe-view setup scripts."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT_DIR = VIEWER_DIR / "scripts"
# Since the 2026-09 repository split vibe-view carries its own copy of the
# lifecycle lock; it used to live in a shared scripts/ directory one level
# above the viewer, alongside vibe-qc.
SHARED_LIFECYCLE_LOCK = VIEWER_DIR / "scripts" / "_lifecycle_lock.sh"
SHELL_SCRIPTS = (
    SCRIPT_DIR / "install.sh",
    SCRIPT_DIR / "reinstall.sh",
    SCRIPT_DIR / "uninstall.sh",
    SCRIPT_DIR / "update.sh",
    SCRIPT_DIR / "update-desktop.sh",
    SCRIPT_DIR / "build.sh",
    SCRIPT_DIR / "launch.sh",
)
SYNTAX_SCRIPTS = (*SHELL_SCRIPTS, SCRIPT_DIR / "_venv_helpers.sh")


@pytest.fixture(autouse=True)
def isolated_private_operator_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VIBE_PRIVATE_ROOT", str(tmp_path / "private-state"))


def _run(
    script: Path,
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script), *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o755)


def _copy_shared_lifecycle_lock(repo_root: Path) -> None:
    """Place the lifecycle lock where _venv_helpers.sh looks for it.

    That is now the viewer's own scripts/ directory. The fixtures build a
    checkout either as ``<root>/scripts`` or as ``<root>/vibe-view/scripts``,
    so populate every scripts/ directory one level down as well as the root's.
    """
    targets = [repo_root / "scripts"]
    targets += [p for p in repo_root.glob("*/scripts") if p.is_dir()]
    for scripts in targets:
        scripts.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SHARED_LIFECYCLE_LOCK, scripts / "_lifecycle_lock.sh")
        shutil.copy2(SCRIPT_DIR / "_private_state.py", scripts / "_private_state.py")


def _fake_venv(venv: Path) -> None:
    venv.mkdir(parents=True, exist_ok=True)
    (venv / "pyvenv.cfg").write_text("home = fake\n")
    _write_executable(
        venv / "bin" / "python",
        f"""#!{sys.executable}
from pathlib import Path
import os
import sys

args = sys.argv[1:]
while args and args[0] in ("-I", "-S"):
    args.pop(0)
if args and args[0] == "-c" and any(
    marker in args[1]
    for marker in ("fcntl.flock", "vibe-toolset-lifecycle-locks-")
):
    code = args[1]
    sys.argv = ["-c", *args[2:]]
    exec(compile(code, "<string>", "exec"), {{"__name__": "__main__"}})
    raise SystemExit(0)
elif args and args[0] == "-":
    code = sys.stdin.read()
    sys.argv = ["-", *args[1:]]
    exec(compile(code, "<stdin>", "exec"), {{"__name__": "__main__"}})
    raise SystemExit(0)
log = os.environ.get("FAKE_LOG")
if log:
    with open(log, "a") as stream:
        stream.write("python " + " ".join(args) + "\\n")
if args and args[0].endswith("install-electron.py"):
    raise SystemExit(int(os.environ.get("FAKE_INSTALLER_EXIT", "0")))
failure_match = os.environ.get("FAKE_PYTHON_FAIL_MATCH", "")
if failure_match and failure_match in " ".join(args):
    raise SystemExit(int(os.environ.get("FAKE_COMMAND_EXIT", "17")))
if args == ["--version"]:
    print("Python 3.13.0")
if args[:2] == ["-m", "build"]:
    Path("dist").mkdir(exist_ok=True)
    Path("dist/vibeview-test.whl").touch()
""",
    )
    _write_executable(
        venv / "bin" / "vibe-view",
        f"""#!{sys.executable}
import os
import sys

args = sys.argv[1:]
log = os.environ.get("FAKE_LOG")
if log:
    with open(log, "a") as stream:
        stream.write("cli " + " ".join(args) + "\\n")
failure_match = os.environ.get("FAKE_CLI_FAIL_MATCH", "")
if failure_match and failure_match in " ".join(args):
    raise SystemExit(int(os.environ.get("FAKE_COMMAND_EXIT", "17")))
if args == ["--version"]:
    print("vibe-view 9.9.9 -- test")
""",
    )


def _fake_venv_creator(path: Path) -> None:
    _write_executable(
        path,
        f"""#!{sys.executable}
from pathlib import Path
import os
import shutil
import sys

args = sys.argv[1:]
while args and args[0] in ("-I", "-S"):
    args.pop(0)
if args and args[0] == "-c" and any(
    marker in args[1]
    for marker in ("fcntl.flock", "vibe-toolset-lifecycle-locks-")
):
    code = args[1]
    sys.argv = ["-c", *args[2:]]
    exec(compile(code, "<string>", "exec"), {{"__name__": "__main__"}})
    raise SystemExit(0)
elif args and args[0] == "-":
    code = sys.stdin.read()
    sys.argv = ["-", *args[1:]]
    exec(compile(code, "<stdin>", "exec"), {{"__name__": "__main__"}})
    raise SystemExit(0)
elif args[:2] == ["-m", "venv"]:
    target = Path(args[-1])
    if os.environ.get("FAKE_CREATE_EXIT"):
        raise SystemExit(int(os.environ["FAKE_CREATE_EXIT"]))
    shutil.copytree(os.environ["FAKE_VENV_TEMPLATE"], target, dirs_exist_ok=True)
elif args == ["--version"]:
    print("Python 3.13.0")
""",
    )


@pytest.mark.parametrize("script", SYNTAX_SCRIPTS)
def test_shell_script_has_valid_bash_syntax(script: Path) -> None:
    result = subprocess.run(["bash", "-n", str(script)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", SHELL_SCRIPTS)
def test_user_facing_shell_scripts_are_executable(script: Path) -> None:
    assert script.stat().st_mode & stat.S_IXUSR


@pytest.mark.parametrize(
    "name",
    (
        "install.sh",
        "reinstall.sh",
        "uninstall.sh",
        "update.sh",
        "update-desktop.sh",
    ),
)
def test_setup_help_works_from_an_unrelated_directory(name: str, tmp_path: Path) -> None:
    result = _run(SCRIPT_DIR / name, "--help", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "USAGE" in result.stdout
    assert "--venv" in result.stdout
    assert f"./scripts/{name}" in result.stdout


def test_reinstall_help_describes_transaction_and_desktop_mode(tmp_path: Path) -> None:
    result = _run(SCRIPT_DIR / "reinstall.sh", "--help", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "previous environment is restored" in result.stdout
    assert "--desktop" in result.stdout
    assert "Git and" in result.stdout


def test_install_help_describes_desktop_integrations(tmp_path: Path) -> None:
    result = _run(SCRIPT_DIR / "install.sh", "--help", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "--dock" in result.stdout
    assert "--link-bin" in result.stdout
    assert "--adopt-desktop" in result.stdout
    assert "/opt/homebrew/bin" in result.stdout


def test_install_dry_run_previews_link_bin(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    result = _run(
        SCRIPT_DIR / "install.sh",
        "--dry-run",
        "--link-bin",
        str(bin_dir),
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert "Command" in result.stdout
    assert "[link-bin] would create" in result.stdout
    assert f"{bin_dir}/vibe-view" in result.stdout


def test_link_bin_creates_marked_launcher_and_unlink_removes_it(
    tmp_path: Path,
) -> None:
    viewer = tmp_path / "vibe-view"
    scripts = viewer / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPT_DIR / "_venv_helpers.sh", scripts / "_venv_helpers.sh")
    _copy_shared_lifecycle_lock(tmp_path)
    venv = viewer / ".venv"
    _fake_venv(venv)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    helper = shlex.quote(str(scripts / "_venv_helpers.sh"))
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    link = subprocess.run(
        [
            "bash",
            "-c",
            f". {helper}; vibe_view_link_bin "
            f"{shlex.quote(str(venv))} {shlex.quote(str(bin_dir))}",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert link.returncode == 0, link.stderr
    assert "Command link:" in link.stdout
    shim = bin_dir / "vibe-view"
    assert shim.is_file()
    content = shim.read_text()
    assert "# Generated by vibe-view install.sh --link-bin" in content
    assert f'exec "{venv}/bin/vibe-view"' in content
    key = hashlib.sha256(os.fsencode(viewer.resolve())).hexdigest()
    record = Path(env["VIBE_PRIVATE_ROOT"]) / "vibe-view" / "state" / key / "bin-links"
    assert not (viewer / ".vibe-view-bin-links").exists()
    assert record.read_text().strip() == str(bin_dir)

    unlink = subprocess.run(
        [
            "bash",
            "-c",
            f". {helper}; vibe_view_unlink_bin {shlex.quote(str(venv))} 0",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert unlink.returncode == 0, unlink.stderr
    assert "Removed command link" in unlink.stdout
    assert not shim.exists()
    assert not record.exists()


def test_link_bin_refuses_unmanaged_and_foreign_files(tmp_path: Path) -> None:
    viewer = tmp_path / "vibe-view"
    scripts = viewer / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPT_DIR / "_venv_helpers.sh", scripts / "_venv_helpers.sh")
    _copy_shared_lifecycle_lock(tmp_path)
    venv = viewer / ".venv"
    _fake_venv(venv)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "vibe-view").write_text("#!/bin/sh\necho user-owned\n")
    helper = shlex.quote(str(scripts / "_venv_helpers.sh"))

    result = subprocess.run(
        [
            "bash",
            "-c",
            f". {helper}; vibe_view_link_bin "
            f"{shlex.quote(str(venv))} {shlex.quote(str(bin_dir))}",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "was not created by vibe-view" in result.stderr
    assert (bin_dir / "vibe-view").read_text() == "#!/bin/sh\necho user-owned\n"


def test_install_naming_path_writes_pth_and_is_idempotent(tmp_path: Path) -> None:
    """The standalone environment must be able to import vibeqc_naming:
    the helper writes a .pth pointing at the checkout's python/ directory
    when the naming package is present, and does nothing when it is not."""
    viewer = tmp_path / "vibe-view"
    scripts = viewer / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPT_DIR / "_venv_helpers.sh", scripts / "_venv_helpers.sh")
    _copy_shared_lifecycle_lock(tmp_path)
    venv = viewer / ".venv"
    (venv / "lib" / "python3.14" / "site-packages").mkdir(parents=True)
    naming = tmp_path / "vibe-qc" / "python" / "vibeqc_naming"
    naming.mkdir(parents=True)
    (naming / "__init__.py").write_text('"""fake naming package"""\n')
    helper = shlex.quote(str(scripts / "_venv_helpers.sh"))

    first = subprocess.run(
        ["bash", "-c", f". {helper}; vibe_view_install_naming_path "
         f"{shlex.quote(str(venv))}"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    assert "Naming path:" in first.stdout
    pth = venv / "lib" / "python3.14" / "site-packages" / "vibe-view-repo-python.pth"
    assert pth.read_text().strip() == str(tmp_path / "vibe-qc" / "python")

    second = subprocess.run(
        ["bash", "-c", f". {helper}; vibe_view_install_naming_path "
         f"{shlex.quote(str(venv))}"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert second.returncode == 0
    assert "Naming path:" not in second.stdout  # idempotent: no rewrite
    assert pth.read_text().strip() == str(tmp_path / "vibe-qc" / "python")

    # Without the naming package in the checkout, the helper is a no-op.
    bare = tmp_path / "bare" / "vibe-view"
    (bare / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT_DIR / "_venv_helpers.sh", bare / "scripts" / "_venv_helpers.sh")
    _copy_shared_lifecycle_lock(bare.parent)
    bare_venv = bare / ".venv"
    (bare_venv / "lib" / "python3.14" / "site-packages").mkdir(parents=True)
    bare_helper = shlex.quote(str(bare / "scripts" / "_venv_helpers.sh"))
    absent = subprocess.run(
        ["bash", "-c", f". {bare_helper}; vibe_view_install_naming_path "
         f"{shlex.quote(str(bare_venv))}"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert absent.returncode == 0
    assert not (bare_venv / "lib" / "python3.14" / "site-packages" / "vibe-view-repo-python.pth").exists()


@pytest.mark.skipif(not Path("/bin/bash").exists(), reason="stock macOS Bash is unavailable")
@pytest.mark.parametrize(
    ("args", "expected"),
    (
        ((), "install --force"),
        (("--desktop",), "update --skip-git --recreate-venv --desktop"),
    ),
)
def test_reinstall_empty_passthrough_is_bash_3_safe(
    tmp_path: Path,
    args: tuple[str, ...],
    expected: str,
) -> None:
    scripts = tmp_path / "vibe-view" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPT_DIR / "reinstall.sh", scripts / "reinstall.sh")
    log = tmp_path / "wrapper.log"
    for name in ("install.sh", "update.sh"):
        _write_executable(
            scripts / name,
            f"#!/bin/sh\nprintf '{name[:-3]} %s\\n' \"$*\" > {str(log)!r}\n",
        )

    result = subprocess.run(
        ["/bin/bash", str(scripts / "reinstall.sh"), *args],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert log.read_text().strip() == expected


def _mark_owned_venv(venv: Path, project: Path) -> None:
    (venv / ".vibe-view-standalone.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "kind": "vibe-view-standalone-venv",
                "project": str(project.resolve()),
            }
        )
    )


def _add_vibeview_pep610_record(venv: Path, project: Path) -> None:
    site = (
        venv
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
        / "vibeview-test.dist-info"
    )
    site.mkdir(parents=True, exist_ok=True)
    (site / "direct_url.json").write_text(
        json.dumps({"url": project.resolve().as_uri(), "dir_info": {}})
    )


def _uninstall_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    viewer = tmp_path / "viewer copy"
    scripts = viewer / "scripts"
    scripts.mkdir(parents=True)
    for name in ("uninstall.sh", "_venv_helpers.sh"):
        shutil.copy2(SCRIPT_DIR / name, scripts / name)
    _copy_shared_lifecycle_lock(tmp_path)
    installer = viewer / "electron" / "install-electron.py"
    _write_executable(
        installer,
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

log = os.environ.get("FAKE_LOG")
if log:
    with Path(log).open("a") as stream:
        stream.write("installer " + " ".join(sys.argv[1:]) + "\\n")
""",
    )
    return viewer, scripts / "uninstall.sh", installer


def test_desktop_wrapper_help_describes_implied_mode(tmp_path: Path) -> None:
    result = _run(SCRIPT_DIR / "update-desktop.sh", "--help", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "./scripts/update-desktop.sh [OPTIONS]" in result.stdout
    assert "Desktop mode is implied" in result.stdout
    assert "Requires --desktop" not in result.stdout


def test_linux_root_desktop_launch_is_refused_before_side_effects(monkeypatch) -> None:
    import vibeview.cli as cli

    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setattr(cli.os, "geteuid", lambda: 0, raising=False)

    def unexpected_electron_lookup() -> Path:
        raise AssertionError("root refusal must happen before Electron setup")

    monkeypatch.setattr(cli, "_electron_dir", unexpected_electron_lookup)

    result = CliRunner().invoke(cli.main, ["desktop"])

    assert result.exit_code == 1
    assert "regular Linux user, not as root" in result.output
    assert "without sudo" in result.output


def test_linux_desktop_no_sandbox_is_explicit_and_cleans_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import vibeview.cli as cli

    electron_dir = tmp_path / "electron"
    binary = electron_dir / "node_modules" / "electron" / "dist" / "electron"
    _write_executable(binary, "#!/bin/sh\nexit 0\n")
    (electron_dir / "install-electron.py").write_text("# installer\n")
    config = tmp_path / "desktop-config.json"
    config.write_text("{}\n")
    launches: list[list[str]] = []

    class Process:
        def wait(self) -> int:
            return 0

    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(cli, "_electron_dir", lambda: electron_dir)
    monkeypatch.setattr(cli, "_abort_if_port_in_use", lambda *_args: None)
    monkeypatch.setattr(cli, "_record_interpreter", lambda: None)
    monkeypatch.setattr(cli, "_write_desktop_config", lambda *_args: config)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0),
    )

    def popen(command, **_kwargs):
        launches.append(command)
        return Process()

    monkeypatch.setattr(subprocess, "Popen", popen)

    result = CliRunner().invoke(cli.main, ["desktop", "--no-sandbox"])

    assert result.exit_code == 0, result.output
    assert launches == [[str(binary), "--no-sandbox", str(electron_dir)]]
    assert "security sandbox is disabled" in result.output
    assert not config.exists()


def test_linux_desktop_propagates_electron_failure_and_cleans_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import vibeview.cli as cli

    electron_dir = tmp_path / "electron"
    binary = electron_dir / "node_modules" / "electron" / "dist" / "electron"
    _write_executable(binary, "#!/bin/sh\nexit 0\n")
    (electron_dir / "install-electron.py").write_text("# installer\n")
    config = tmp_path / "desktop-config.json"
    config.write_text("{}\n")

    class Process:
        def wait(self) -> int:
            return -5

    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(cli, "_electron_dir", lambda: electron_dir)
    monkeypatch.setattr(cli, "_abort_if_port_in_use", lambda *_args: None)
    monkeypatch.setattr(cli, "_record_interpreter", lambda: None)
    monkeypatch.setattr(cli, "_write_desktop_config", lambda *_args: config)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0),
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: Process())

    result = CliRunner().invoke(cli.main, ["desktop"])

    assert result.exit_code == 133
    assert "Electron exited after signal 5 (status 133)" in result.output
    assert "enable unprivileged user namespaces" in result.output
    assert "retry with --no-sandbox" in result.output
    assert not config.exists()


def test_desktop_repairs_present_but_unhealthy_runtime(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import vibeview.cli as cli

    electron_dir = tmp_path / "electron"
    binary = (
        electron_dir
        / "node_modules"
        / "electron"
        / "dist"
        / "Electron.app"
        / "Contents"
        / "MacOS"
        / "Electron"
    )
    _write_executable(binary, "#!/bin/sh\nexit 0\n")
    (electron_dir / "install-electron.py").write_text("# installer\n")
    config = tmp_path / "desktop-config.json"
    config.write_text("{}\n")
    commands: list[list[str]] = []

    class Process:
        def wait(self) -> int:
            return 0

    def run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 1 if "--check" in command else 0)

    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr(cli, "_electron_dir", lambda: electron_dir)
    monkeypatch.setattr(cli, "_abort_if_port_in_use", lambda *_args: None)
    monkeypatch.setattr(cli, "_record_interpreter", lambda: None)
    monkeypatch.setattr(cli, "_write_desktop_config", lambda *_args: config)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: Process())

    result = CliRunner().invoke(cli.main, ["desktop"])

    assert result.exit_code == 0, result.output
    assert commands == [
        [sys.executable, str(electron_dir / "install-electron.py"), "--check"],
        [sys.executable, str(electron_dir / "install-electron.py")],
    ]
    assert "Repairing the reviewed Electron runtime" in result.output
    assert not config.exists()


def test_electron_update_feed_is_gated_on_packaged_builds() -> None:
    main_js = (VIEWER_DIR / "electron" / "main.js").read_text()

    assert '"vibe-view-source-app.json"' in main_js
    assert 'process.platform === "darwin" && process.arch === "arm64"' in main_js
    assert "!HAS_SOURCE_APP_MARKER && HAS_PUBLISHED_UPDATE_FEED" in main_js
    assert "if (USE_PACKAGED_UPDATER && autoUpdater)" in main_js
    assert "if (!USE_PACKAGED_UPDATER || !autoUpdater) return;" in main_js
    # The About box no longer carries a pasted codename: it reads
    # CONFIG.codename from the launcher handshake, with FALLBACK_CODENAME for
    # a double-click launch that has no config. That the fallback matches the
    # catalogue is tests/test_release_codenames.py's job; here we only assert
    # the About box goes through the constant rather than a literal.
    assert "const APP_CODENAME = CONFIG.codename || FALLBACK_CODENAME;" in main_js
    assert "message: `vibe-view ${APP_VERSION} — ${APP_CODENAME}`," in main_js
    assert "vibe-view-<version>-arm64.dmg" not in main_js


def test_packaging_builder_detects_minimal_source_node_modules() -> None:
    builder = (VIEWER_DIR / "electron" / "build-desktop.sh").read_text()

    assert "node_modules/.bin/electron-builder" in builder
    assert "node_modules/electron-updater/package.json" in builder
    assert "npm ci" in builder
    assert 'if [ ! -d "node_modules" ]' not in builder
    assert "dist/mac-arm64/vibe-view.app" not in builder
    assert "dist/mac*/vibe-view.app" in builder
    assert "dist/linux*-unpacked/resources/app.asar" in builder
    assert 'if [ "${#ASAR_FILES[@]}" -eq 0 ]' in builder
    assert 'Darwin) BUILD_MODE="--mac"' in builder
    assert 'Linux)  BUILD_MODE="--linux"' in builder
    assert "${1:---mac}" not in builder


@pytest.mark.parametrize(
    ("kernel", "target", "asar_path"),
    (
        (
            "Darwin",
            "--mac",
            Path("dist/mac-arm64/vibe-view.app/Contents/Resources/app.asar"),
        ),
        ("Linux", "--linux", Path("dist/linux-unpacked/resources/app.asar")),
    ),
)
def test_packaging_builder_defaults_to_native_host(
    tmp_path: Path,
    kernel: str,
    target: str,
    asar_path: Path,
) -> None:
    electron = tmp_path / "electron"
    electron.mkdir()
    builder = electron / "build-desktop.sh"
    shutil.copy2(VIEWER_DIR / "electron" / "build-desktop.sh", builder)
    _write_executable(electron / "node_modules/.bin/electron-builder", "#!/bin/sh\n")
    (electron / "node_modules/electron-updater").mkdir(parents=True)
    (electron / "node_modules/electron-updater/package.json").write_text("{}\n")

    commands = tmp_path / "commands"
    commands.mkdir()
    for name in ("node", "npm"):
        _write_executable(commands / name, f"#!/bin/sh\necho {name}-test\n")
    _write_executable(commands / "uname", "#!/bin/sh\nprintf '%s\\n' \"$FAKE_KERNEL\"\n")
    log = tmp_path / "npx.log"
    _write_executable(
        commands / "npx",
        f"""#!{sys.executable}
from pathlib import Path
import os
import sys

with Path(os.environ["FAKE_NPX_LOG"]).open("a") as stream:
    stream.write(" ".join(sys.argv[1:]) + "\\n")
if sys.argv[1] == "electron-builder":
    output = Path(os.environ["FAKE_ASAR_PATH"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"fixture")
elif sys.argv[1:3] == ["asar", "list"]:
    print("node_modules/electron-updater/out/AppUpdater.js")
""",
    )
    env = dict(
        os.environ,
        FAKE_ASAR_PATH=str(electron / asar_path),
        FAKE_KERNEL=kernel,
        FAKE_NPX_LOG=str(log),
        PATH=f"{commands}{os.pathsep}{os.environ.get('PATH', '')}",
    )
    # A caller's deployment environment must not turn a local build into an upload.
    env["VIBEVIEW_UPDATE_RSYNC_TARGET"] = "deploy@example.invalid:/web/feed/"
    _write_executable(commands / "rsync", "#!/bin/sh\necho unexpected-upload >&2\nexit 91\n")

    result = _run(builder, cwd=tmp_path, env=env)

    assert result.returncode == 0, result.stderr
    assert f"electron-builder {target} --publish never" in log.read_text()
    assert "electron-updater is bundled" in result.stdout
    assert "Artifacts remain local" in result.stdout


def test_uninstall_removes_owned_venv_and_preserves_user_data(tmp_path: Path) -> None:
    viewer, script, _installer = _uninstall_fixture(tmp_path)
    target = viewer / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(target)], check=True)
    _mark_owned_venv(target, viewer)

    xdg_cache = tmp_path / "cache root"
    xdg_config = tmp_path / "config root"
    xdg_data = tmp_path / "data root"
    cache_dir = xdg_cache / "vibe-view"
    config_dir = xdg_config / "vibe-view"
    managed_venv = xdg_data / "vibe-view" / "venv"
    cache_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    managed_venv.mkdir(parents=True)
    (cache_dir / "interpreter.json").write_text(
        json.dumps({"python": str(target / "bin" / "python"), "version": "test"})
    )
    (cache_dir / "settings.json").write_text('{"dark_background": false}\n')
    (config_dir / "config.toml").write_text("[server]\nport = 9000\n")
    (managed_venv / "keep.txt").write_text("packaged-app environment\n")
    qvf = tmp_path / "calculation.qvf"
    qvf.write_bytes(b"user result")
    log = tmp_path / "uninstall.log"
    env = dict(
        os.environ,
        FAKE_LOG=str(log),
        XDG_CACHE_HOME=str(xdg_cache),
        XDG_CONFIG_HOME=str(xdg_config),
        XDG_DATA_HOME=str(xdg_data),
    )

    result = _run(script, cwd=tmp_path, env=env)

    assert result.returncode == 0, result.stderr
    assert "vibe-view uninstall complete" in result.stdout
    assert not target.exists()
    assert not (cache_dir / "interpreter.json").exists()
    assert (cache_dir / "settings.json").exists()
    assert (config_dir / "config.toml").exists()
    assert (managed_venv / "keep.txt").exists()
    assert qvf.read_bytes() == b"user result"
    assert log.read_text() == "installer --uninstall\n"

    second = _run(
        script,
        "--python",
        sys.executable,
        cwd=tmp_path,
        env=env,
    )
    assert second.returncode == 0, second.stderr
    assert "nothing to remove there" in second.stdout


def test_uninstall_dry_run_changes_nothing(tmp_path: Path) -> None:
    viewer, script, _installer = _uninstall_fixture(tmp_path)
    target = viewer / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(target)], check=True)
    _mark_owned_venv(target, viewer)
    cache = tmp_path / "cache"
    record = cache / "vibe-view" / "interpreter.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"python": str(target / "bin" / "python")}))
    log = tmp_path / "dry-run.log"
    env = dict(os.environ, FAKE_LOG=str(log), XDG_CACHE_HOME=str(cache))

    result = _run(script, "--dry-run", cwd=tmp_path, env=env)

    assert result.returncode == 0, result.stderr
    assert "Would remove standalone virtualenv" in result.stdout
    assert "Would remove stale desktop interpreter record" in result.stdout
    assert target.exists()
    assert record.exists()
    assert log.read_text() == "installer --uninstall --dry-run\n"


def test_lifecycle_uses_readonly_bash_euid_for_root_guard() -> None:
    source = (SCRIPT_DIR / "_venv_helpers.sh").read_text()

    assert 'vibe_view_refuse_privileged_lifecycle_for_uid "$EUID"' in source
    assert "id -u" not in source


def test_lifecycle_root_guard_allows_only_exact_gitlab_ci() -> None:
    helper = shlex.quote(str(SCRIPT_DIR / "_venv_helpers.sh"))
    program = f'. {helper}; vibe_view_refuse_privileged_lifecycle_for_uid "$1"'
    cases = (
        ("0", {"CI": "true", "GITLAB_CI": "true"}, 0),
        ("0", {"CI": "true", "GITLAB_CI": "false"}, 1),
        ("501", {}, 0),
    )
    for euid, additions, expected in cases:
        env = dict(os.environ)
        env.pop("SUDO_USER", None)
        env.pop("CI", None)
        env.pop("GITLAB_CI", None)
        env.update(additions)
        result = subprocess.run(
            ["/bin/bash", "-c", program, "guard-test", euid],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == expected, (euid, additions, result.stderr)


@pytest.mark.parametrize(
    ("name", "args"),
    (
        ("install.sh", ("--dry-run",)),
        ("update.sh", ("--skip-git", "--dry-run")),
        ("reinstall.sh", ("--dry-run",)),
        ("update-desktop.sh", ("--skip-git", "--dry-run")),
        ("uninstall.sh", ("--dry-run",)),
    ),
)
def test_every_lifecycle_command_refuses_sudo_before_mutation(
    name: str,
    args: tuple[str, ...],
    tmp_path: Path,
) -> None:
    env = dict(os.environ, SUDO_USER="regular-user")

    result = _run(SCRIPT_DIR / name, *args, cwd=tmp_path, env=env)

    assert result.returncode != 0
    assert "lifecycle commands as your regular user, without sudo" in result.stderr


def test_uninstall_refuses_sudo_before_any_cleanup(tmp_path: Path) -> None:
    viewer, script, _installer = _uninstall_fixture(tmp_path)
    target = viewer / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(target)], check=True)
    _mark_owned_venv(target, viewer)
    log = tmp_path / "must-not-run.log"
    env = dict(
        os.environ,
        CI="true",
        FAKE_LOG=str(log),
        GITLAB_CI="true",
        SUDO_USER="regular-user",
    )

    result = _run(script, cwd=tmp_path, env=env)

    assert result.returncode != 0
    assert "regular user, without sudo" in result.stderr
    assert target.exists()
    assert not log.exists()


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() != 0,
    reason="requires a real root process",
)
def test_uninstall_allows_real_root_in_gitlab_ci(tmp_path: Path) -> None:
    viewer, script, _installer = _uninstall_fixture(tmp_path)
    target = viewer / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(target)], check=True)
    _mark_owned_venv(target, viewer)
    log = tmp_path / "gitlab-root-uninstall.log"
    env = dict(
        os.environ,
        CI="true",
        FAKE_LOG=str(log),
        GITLAB_CI="true",
    )
    env.pop("SUDO_USER", None)

    result = _run(script, cwd=tmp_path, env=env)

    assert result.returncode == 0, result.stderr
    assert not target.exists()
    assert log.read_text() == "installer --uninstall\n"


def test_uninstall_explicit_missing_venv_refuses_before_desktop_cleanup(
    tmp_path: Path,
) -> None:
    _viewer, script, _installer = _uninstall_fixture(tmp_path)
    log = tmp_path / "must-not-clean-desktop.log"
    env = dict(os.environ, FAKE_LOG=str(log))

    result = _run(
        script,
        "--venv",
        str(tmp_path / "typo-does-not-exist"),
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode != 0
    assert "selected standalone virtualenv does not exist" in result.stderr
    assert not log.exists()


@pytest.mark.parametrize(
    "args",
    (
        ("--venv", ""),
        ("--venv", "--dry-run"),
        ("--venv", "--keep-desktop"),
        ("--python", ""),
        ("--python", "--dry-run"),
    ),
)
def test_uninstall_rejects_missing_or_option_like_values_before_cleanup(
    tmp_path: Path,
    args: tuple[str, str],
) -> None:
    viewer, script, _installer = _uninstall_fixture(tmp_path)
    target = viewer / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(target)], check=True)
    _mark_owned_venv(target, viewer)
    log = tmp_path / "must-not-clean.log"
    env = dict(os.environ, FAKE_LOG=str(log))

    result = _run(script, *args, cwd=tmp_path, env=env)

    assert result.returncode != 0
    assert "requires" in result.stderr
    assert target.exists()
    assert not log.exists()


def test_uninstall_refuses_unowned_or_symlinked_venv_before_cleanup(
    tmp_path: Path,
) -> None:
    viewer, script, _installer = _uninstall_fixture(tmp_path)
    unrelated = tmp_path / "unrelated environment"
    subprocess.run([sys.executable, "-m", "venv", str(unrelated)], check=True)
    log = tmp_path / "should-not-run.log"
    env = dict(os.environ, FAKE_LOG=str(log))

    unowned = _run(script, "--venv", str(unrelated), cwd=tmp_path, env=env)
    assert unowned.returncode != 0
    assert "ownership is not proven" in unowned.stderr
    assert unrelated.exists()
    assert not log.exists()

    link = tmp_path / "linked environment"
    link.symlink_to(unrelated, target_is_directory=True)
    for spelling in (str(link), f"{link}/", f"{link}/."):
        symlinked = _run(script, "--venv", spelling, cwd=tmp_path, env=env)
        assert symlinked.returncode != 0
        assert "symbolic link" in symlinked.stderr
        assert link.is_symlink() and unrelated.exists()
        assert not log.exists()


def test_uninstall_never_executes_python_from_untrusted_target(tmp_path: Path) -> None:
    viewer, script, _installer = _uninstall_fixture(tmp_path)
    target = tmp_path / "hostile environment"
    (target / "bin").mkdir(parents=True)
    (target / "pyvenv.cfg").write_text("home = hostile\n")
    executed = tmp_path / "untrusted-python-ran"
    _write_executable(
        target / "bin" / "python",
        f"#!/bin/sh\ntouch {str(executed)!r}\nexit 0\n",
    )

    result = _run(
        script,
        "--keep-desktop",
        "--python",
        sys.executable,
        "--venv",
        str(target),
        cwd=tmp_path,
    )

    assert result.returncode != 0
    assert "ownership is not proven" in result.stderr
    assert not executed.exists()
    assert target.exists()


def test_uninstall_rejects_default_python_resolved_inside_target(tmp_path: Path) -> None:
    viewer, script, _installer = _uninstall_fixture(tmp_path)
    target = viewer / ".venv"
    # Use regular fake executables here. A real venv's python3 is commonly a
    # symlink to the base interpreter; writing through it would corrupt that
    # shared interpreter instead of replacing a disposable fixture file.
    _fake_venv(target)
    _mark_owned_venv(target, viewer)
    executed = tmp_path / "activated-python-ran"
    recorded_executed = tmp_path / "recorded-python-ran"
    recorded = tmp_path / "recorded-python"
    _write_executable(
        recorded,
        f"#!/bin/sh\ntouch {str(recorded_executed)!r}\nexit 91\n",
    )
    (target / "pyvenv.cfg").write_text(f"home = hostile\nexecutable = {recorded}\n")
    _write_executable(
        target / "bin" / "python3",
        f"#!/bin/sh\ntouch {str(executed)!r}\nexit 0\n",
    )
    env = dict(
        os.environ,
        PATH=f"{target / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
    )

    result = _run(script, "--keep-desktop", cwd=tmp_path, env=env)

    assert result.returncode != 0
    assert "selected for replacement/removal" in result.stderr
    assert not executed.exists()
    assert not recorded_executed.exists()
    assert target.exists()

    (target / "child").mkdir()
    dotdot = _run(
        script,
        "--keep-desktop",
        "--venv",
        str(target / "child" / ".."),
        cwd=tmp_path,
        env=env,
    )
    assert dotdot.returncode != 0
    assert "selected for replacement/removal" in dotdot.stderr
    assert not executed.exists()
    assert not recorded_executed.exists()
    assert target.exists()


def test_ordinary_update_accepts_activated_target_with_external_lock_python(
    tmp_path: Path,
) -> None:
    viewer, update_script = _copy_script_fixture(tmp_path, "update.sh")
    target = viewer / ".venv"
    _fake_venv(target)
    activated_executed = tmp_path / "activated-python-ran"
    recorded_executed = tmp_path / "recorded-python-ran"
    _write_executable(
        target / "bin" / "python3",
        f"#!/bin/sh\ntouch {str(activated_executed)!r}\nexit 90\n",
    )
    recorded = tmp_path / "recorded-python"
    _write_executable(
        recorded,
        f"#!/bin/sh\ntouch {str(recorded_executed)!r}\nexit 91\n",
    )
    (target / "pyvenv.cfg").write_text(f"home = hostile\nexecutable = {recorded}\n")
    log = tmp_path / "update.log"
    env = dict(
        os.environ,
        FAKE_LOG=str(log),
        PATH=f"{target / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
    )

    result = _run(
        update_script,
        "--skip-git",
        "--extras",
        "core",
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "vibe-view update complete" in result.stdout
    assert not activated_executed.exists()
    assert not recorded_executed.exists()


def test_uninstall_rejects_symlinked_ownership_files(tmp_path: Path) -> None:
    viewer, script, _installer = _uninstall_fixture(tmp_path)
    target = viewer / ".venv"
    (target / "bin").mkdir(parents=True)
    shutil.copy2(sys.executable, target / "bin" / "python")
    real_cfg = tmp_path / "real-pyvenv.cfg"
    real_cfg.write_text("home = external\n")
    (target / "pyvenv.cfg").symlink_to(real_cfg)
    _mark_owned_venv(target, viewer)

    cfg_result = _run(
        script,
        "--keep-desktop",
        "--python",
        sys.executable,
        cwd=tmp_path,
    )
    assert cfg_result.returncode != 0
    assert "not recognisably a virtualenv" in cfg_result.stderr
    assert target.exists()

    (target / "pyvenv.cfg").unlink()
    (target / "pyvenv.cfg").write_text("home = external\n")
    marker = target / ".vibe-view-standalone.json"
    marker_payload = marker.read_text()
    external_marker = tmp_path / "external-marker.json"
    external_marker.write_text(marker_payload)
    marker.unlink()
    marker.symlink_to(external_marker)

    marker_result = _run(
        script,
        "--keep-desktop",
        "--python",
        sys.executable,
        cwd=tmp_path,
    )
    assert marker_result.returncode != 0
    assert "ownership is not proven" in marker_result.stderr
    assert target.exists()

    marker.unlink()
    marker.write_text(
        json.dumps(
            {
                "schema": 1,
                "kind": "vibe-view-standalone-venv",
                "project": viewer.name,
            }
        )
    )
    relative_result = _run(
        script,
        "--keep-desktop",
        "--python",
        sys.executable,
        cwd=tmp_path,
    )
    assert relative_result.returncode != 0
    assert "ownership is not proven" in relative_result.stderr
    assert target.exists()


@pytest.mark.parametrize("target", (Path("/"), VIEWER_DIR, VIEWER_DIR.parent))
def test_uninstall_refuses_unsafe_venv_targets(target: Path) -> None:
    result = _run(
        SCRIPT_DIR / "uninstall.sh",
        "--keep-desktop",
        "--venv",
        str(target),
    )
    assert result.returncode != 0
    assert "refusing" in result.stderr and "virtualenv target" in result.stderr


def test_uninstall_refuses_target_that_contains_checkout(tmp_path: Path) -> None:
    _viewer, script, _installer = _uninstall_fixture(tmp_path)

    result = _run(
        script,
        "--keep-desktop",
        "--python",
        sys.executable,
        "--venv",
        str(tmp_path),
        cwd=tmp_path,
    )

    assert result.returncode != 0
    assert "refusing" in result.stderr and "virtualenv target" in result.stderr


def test_uninstall_keep_desktop_skips_electron_cleanup(tmp_path: Path) -> None:
    viewer, script, _installer = _uninstall_fixture(tmp_path)
    target = viewer / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(target)], check=True)
    _mark_owned_venv(target, viewer)
    log = tmp_path / "keep-desktop.log"
    env = dict(os.environ, FAKE_LOG=str(log))

    result = _run(script, "--keep-desktop", cwd=tmp_path, env=env)

    assert result.returncode == 0, result.stderr
    assert not target.exists()
    assert "Desktop:      preserved" in result.stdout
    assert not log.exists()


def test_uninstall_uses_fallback_python_for_broken_owned_venv(tmp_path: Path) -> None:
    viewer, script, _installer = _uninstall_fixture(tmp_path)
    target = viewer / ".venv"
    (target / "bin").mkdir(parents=True)
    (target / "pyvenv.cfg").write_text("home = missing\n")
    _write_executable(target / "bin" / "python", "#!/bin/sh\nexit 23\n")
    _mark_owned_venv(target, viewer)

    result = _run(
        script,
        "--keep-desktop",
        "--python",
        sys.executable,
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert not target.exists()


def test_uninstall_keeps_interpreter_record_when_venv_removal_fails(
    tmp_path: Path,
) -> None:
    viewer, script, _installer = _uninstall_fixture(tmp_path)
    target = viewer / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(target)], check=True)
    _mark_owned_venv(target, viewer)
    cache = tmp_path / "cache"
    record = cache / "vibe-view" / "interpreter.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"python": str(target / "bin" / "python")}))
    commands = tmp_path / "commands"
    commands.mkdir()
    real_rm = shutil.which("rm")
    assert real_rm is not None
    _write_executable(
        commands / "rm",
        f"""#!{sys.executable}
import os
import sys

if {str(target)!r} in sys.argv[1:]:
    raise SystemExit(23)
os.execv({real_rm!r}, [{real_rm!r}, *sys.argv[1:]])
""",
    )
    env = dict(
        os.environ,
        PATH=f"{commands}{os.pathsep}{os.environ.get('PATH', '')}",
        XDG_CACHE_HOME=str(cache),
    )

    result = _run(
        script,
        "--keep-desktop",
        "--python",
        sys.executable,
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode == 23
    assert target.exists()
    assert record.exists()


def test_reinstall_transactionally_replaces_environment_and_maps_desktop(
    tmp_path: Path,
) -> None:
    template = tmp_path / "replacement template"
    _fake_venv(template)
    (template / "new-environment").write_text("candidate\n")
    creator = tmp_path / "fake creator"
    _fake_venv_creator(creator)
    target = tmp_path / "viewer environment"
    _fake_venv(target)
    _mark_owned_venv(target, VIEWER_DIR)
    (target / "old-environment").write_text("working\n")
    log = tmp_path / "reinstall.log"
    env = dict(
        os.environ,
        FAKE_LOG=str(log),
        FAKE_VENV_TEMPLATE=str(template),
    )

    result = _run(
        SCRIPT_DIR / "reinstall.sh",
        "--python",
        str(creator),
        "--venv",
        str(target),
        "--desktop",
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "vibe-view reinstall complete" in result.stdout
    assert (target / "new-environment").exists()
    assert not (target / "old-environment").exists()
    assert "install-electron.py --refresh-app" in log.read_text()


@pytest.mark.parametrize(
    "args",
    (
        ("--python", "--desktop"),
        ("--venv", "--dry-run"),
        ("--extras", "--desktop"),
        ("--adopt-desktop",),
        ("--recreate-venv",),
    ),
)
def test_reinstall_rejects_invalid_option_contract(
    args: tuple[str, ...],
    tmp_path: Path,
) -> None:
    result = _run(SCRIPT_DIR / "reinstall.sh", *args, cwd=tmp_path)

    assert result.returncode != 0
    assert "Error:" in result.stderr


def test_reinstall_failure_restores_previous_environment(tmp_path: Path) -> None:
    template = tmp_path / "replacement template"
    _fake_venv(template)
    creator = tmp_path / "fake creator"
    _fake_venv_creator(creator)
    target = tmp_path / "viewer environment"
    _fake_venv(target)
    _mark_owned_venv(target, VIEWER_DIR)
    sentinel = target / "old-environment"
    sentinel.write_text("working\n")
    env = dict(
        os.environ,
        FAKE_LOG=str(tmp_path / "failed-reinstall.log"),
        FAKE_PYTHON_FAIL_MATCH="-m pip install --upgrade",
        FAKE_VENV_TEMPLATE=str(template),
    )

    result = _run(
        SCRIPT_DIR / "reinstall.sh",
        "--python",
        str(creator),
        "--venv",
        str(target),
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode != 0
    assert sentinel.read_text() == "working\n"
    assert not list(target.parent.glob(f"{target.name}.previous.*"))


def test_reinstall_desktop_failure_restores_previous_environment(
    tmp_path: Path,
) -> None:
    template = tmp_path / "replacement template"
    _fake_venv(template)
    creator = tmp_path / "fake creator"
    _fake_venv_creator(creator)
    target = tmp_path / "viewer environment"
    _fake_venv(target)
    _mark_owned_venv(target, VIEWER_DIR)
    sentinel = target / "old-environment"
    sentinel.write_text("working\n")
    env = dict(
        os.environ,
        FAKE_INSTALLER_EXIT="29",
        FAKE_LOG=str(tmp_path / "failed-desktop-reinstall.log"),
        FAKE_VENV_TEMPLATE=str(template),
    )

    result = _run(
        SCRIPT_DIR / "reinstall.sh",
        "--python",
        str(creator),
        "--venv",
        str(target),
        "--desktop",
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode == 29
    assert sentinel.read_text() == "working\n"
    assert not list(target.parent.glob(f"{target.name}.previous.*"))


@pytest.mark.parametrize("select_with_env", (False, True))
@pytest.mark.parametrize("dry_run", (False, True))
def test_reinstall_refuses_unowned_explicit_or_environment_venv(
    tmp_path: Path,
    select_with_env: bool,
    dry_run: bool,
) -> None:
    template = tmp_path / "replacement template"
    _fake_venv(template)
    creator = tmp_path / "fake creator"
    _fake_venv_creator(creator)
    target = tmp_path / "shared environment"
    _fake_venv(target)
    sentinel = target / "must-survive"
    sentinel.write_text("shared\n")
    env = dict(os.environ, FAKE_VENV_TEMPLATE=str(template))
    args = ["--python", str(creator)]
    if select_with_env:
        env["VIBE_VIEW_VENV"] = str(target)
    else:
        args.extend(("--venv", str(target)))
    if dry_run:
        args.append("--dry-run")

    result = _run(
        SCRIPT_DIR / "reinstall.sh",
        *args,
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode != 0
    assert "ownership is not proven" in result.stderr
    assert sentinel.read_text() == "shared\n"
    assert not list(target.parent.glob(f"{target.name}.previous.*"))


def test_install_dry_run_resolves_spaceful_path_without_creating_it(tmp_path: Path) -> None:
    target = tmp_path / "viewer environment"
    result = _run(
        SCRIPT_DIR / "install.sh",
        "--dry-run",
        "--venv",
        str(target),
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert str(target) in result.stdout
    assert "vibe-view[viewer,tui]" in result.stdout
    assert "would be created" in result.stdout
    assert not target.exists()


@pytest.mark.parametrize("target", (Path("/"), VIEWER_DIR, VIEWER_DIR.parent))
def test_install_refuses_unsafe_venv_targets(target: Path) -> None:
    result = _run(
        SCRIPT_DIR / "install.sh",
        "--dry-run",
        "--venv",
        str(target),
    )
    assert result.returncode != 0
    assert "refusing" in result.stderr and "virtualenv target" in result.stderr


@pytest.mark.parametrize(
    "target",
    (
        "/etc/hosts",
        "/etc/vibe-view-environment-that-does-not-exist",
        "/boot/vibe-view-environment-that-does-not-exist",
        "/nix/vibe-view-environment-that-does-not-exist",
        "/snap/vibe-view-environment-that-does-not-exist",
        "/usr/local/share/vibe-view-environment-that-does-not-exist",
        "/private/etc/vibe-view-environment-that-does-not-exist",
    ),
)
def test_view_lifecycle_refuses_protected_system_descendants(target: str) -> None:
    helper = shlex.quote(str(SCRIPT_DIR / "_venv_helpers.sh"))
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            f". {helper}; vibe_view_assert_safe_venv_target {shlex.quote(target)}",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "protected system location" in result.stderr


def test_view_lifecycle_refuses_foreign_git_metadata_existing_or_missing(
    tmp_path: Path,
) -> None:
    helper = shlex.quote(str(SCRIPT_DIR / "_venv_helpers.sh"))
    metadata = tmp_path / "other-repository" / ".git"
    existing = metadata / "existing-environment"
    existing.mkdir(parents=True)
    missing = metadata / "missing-environment"

    for target in (existing, missing):
        result = subprocess.run(
            [
                "/bin/bash",
                "-c",
                f". {helper}; vibe_view_assert_safe_venv_target {shlex.quote(str(target))}",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0
        assert "inside Git metadata" in result.stderr


def test_view_lifecycle_keeps_temp_user_and_xdg_descendants_safe(
    tmp_path: Path,
) -> None:
    helper = shlex.quote(str(SCRIPT_DIR / "_venv_helpers.sh"))
    home = tmp_path / "user-home"
    xdg = home / ".local" / "share"
    existing = xdg / "existing-environment"
    existing.mkdir(parents=True)
    missing = xdg / "missing-environment"
    env = dict(os.environ, HOME=str(home), XDG_DATA_HOME=str(xdg))

    for target in (existing, missing, tmp_path / "ordinary-temp-environment"):
        result = subprocess.run(
            [
                "/bin/bash",
                "-c",
                f". {helper}; vibe_view_assert_safe_venv_target {shlex.quote(str(target))}",
            ],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_view_lifecycle_does_not_trust_poisoned_home_or_xdg() -> None:
    helper = shlex.quote(str(SCRIPT_DIR / "_venv_helpers.sh"))
    poisoned_home = Path("/etc").resolve()
    target = poisoned_home / "vibe-view-environment-that-does-not-exist"
    env = dict(os.environ, HOME=str(poisoned_home), XDG_DATA_HOME=str(poisoned_home))
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            f". {helper}; vibe_view_assert_safe_venv_target {shlex.quote(str(target))}",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "protected system location" in result.stderr


def test_view_lifecycle_does_not_trust_another_uids_home(tmp_path: Path) -> None:
    home = Path.home().resolve()
    home_text = str(home)
    if not (home_text == "/root" or home_text.startswith(("/Users/", "/home/", "/var/home/"))):
        pytest.skip("test account does not use a standard account-home path")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_stat = fake_bin / "stat"
    fake_stat.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"${FAKE_STAT_UID:?}\"\n",
        encoding="utf-8",
    )
    fake_stat.chmod(0o755)
    target = home / f"vibe-view-other-owner-probe-{os.getpid()}"
    env = dict(
        os.environ,
        HOME=str(home),
        PATH=f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        FAKE_STAT_UID=str(os.geteuid() + 1),
    )
    helper = shlex.quote(str(SCRIPT_DIR / "_venv_helpers.sh"))
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            f". {helper}; vibe_view_assert_safe_venv_target {shlex.quote(str(target))}",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "protected system location" in result.stderr


def test_view_target_lock_contends_across_different_checkouts(tmp_path: Path) -> None:
    checkout_a = tmp_path / "checkout-a" / "vibe-view" / "scripts"
    checkout_b = tmp_path / "checkout-b" / "vibe-view" / "scripts"
    checkout_a.mkdir(parents=True)
    checkout_b.mkdir(parents=True)
    helper_a = checkout_a / "_venv_helpers.sh"
    helper_b = checkout_b / "_venv_helpers.sh"
    shutil.copy2(SCRIPT_DIR / "_venv_helpers.sh", helper_a)
    shutil.copy2(SCRIPT_DIR / "_venv_helpers.sh", helper_b)
    for checkout in (checkout_a, checkout_b):
        shutil.copy2(SHARED_LIFECYCLE_LOCK, checkout / "_lifecycle_lock.sh")
    target = tmp_path / "shared-environment"
    target_arg = shlex.quote(str(target))
    holder = subprocess.Popen(
        [
            "/bin/bash",
            "-c",
            f". {shlex.quote(str(helper_a))}; "
            f"vibe_view_acquire_target_lock {target_arg} test; "
            "trap 'vibe_view_release_target_lock' EXIT; "
            "printf 'ready\\n'; IFS= read -r _",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline() == "ready\n"
    contender = (
        f". {shlex.quote(str(helper_b))}; "
        f"if vibe_view_acquire_target_lock {target_arg} test; then "
        "vibe_view_release_target_lock; exit 0; fi; exit 1"
    )

    try:
        blocked = subprocess.run(
            ["/bin/bash", "-c", contender],
            text=True,
            capture_output=True,
            check=False,
        )
        assert blocked.returncode != 0
        assert "lifecycle operation already active" in blocked.stderr
    finally:
        holder.communicate("\n", timeout=10)
    assert holder.returncode == 0

    acquired = subprocess.run(
        ["/bin/bash", "-c", contender],
        text=True,
        capture_output=True,
        check=False,
    )
    assert acquired.returncode == 0, acquired.stderr


def test_install_force_refuses_foreign_virtualenv_before_replacement(
    tmp_path: Path,
) -> None:
    target = tmp_path / "shared environment"
    _fake_venv(target)
    sentinel = target / "must-survive"
    sentinel.write_text("foreign\n")

    for extra in ((), ("--dry-run",)):
        result = _run(
            SCRIPT_DIR / "install.sh",
            "--force",
            "--python",
            sys.executable,
            "--venv",
            str(target),
            *extra,
            cwd=tmp_path,
        )

        assert result.returncode != 0
        assert "ownership is not proven" in result.stderr
        assert sentinel.read_text() == "foreign\n"
        assert not list(target.parent.glob(f"{target.name}.previous.*"))


def test_install_force_requires_explicit_proven_legacy_adoption(
    tmp_path: Path,
) -> None:
    viewer, script = _copy_script_fixture(tmp_path, "install.sh")
    target = viewer / ".venv"
    _fake_venv(target)
    _add_vibeview_pep610_record(target, viewer)
    template = tmp_path / "replacement template"
    _fake_venv(template)
    creator = tmp_path / "trusted creator"
    _fake_venv_creator(creator)
    env = dict(
        os.environ,
        FAKE_LOG=str(tmp_path / "legacy-adoption.log"),
        FAKE_VENV_TEMPLATE=str(template),
    )

    refused = _run(
        script,
        "--force",
        "--python",
        str(creator),
        "--venv",
        str(target),
        "--dry-run",
        cwd=tmp_path,
    )
    assert refused.returncode != 0
    assert "ownership is not proven" in refused.stderr

    adopted = _run(
        script,
        "--force",
        "--adopt-legacy",
        "--python",
        str(creator),
        "--venv",
        str(target),
        cwd=tmp_path,
        env=env,
    )
    assert adopted.returncode == 0, adopted.stderr
    assert "trusted PEP 610 ownership proof required" in adopted.stdout
    marker = json.loads((target / ".vibe-view-standalone.json").read_text())
    assert marker["project"] == str(viewer.resolve())


def test_install_never_adopts_foreign_marker_even_with_matching_pep610(
    tmp_path: Path,
) -> None:
    viewer, script = _copy_script_fixture(tmp_path, "install.sh")
    target = viewer / ".venv"
    _fake_venv(target)
    _mark_owned_venv(target, tmp_path / "other viewer")
    _add_vibeview_pep610_record(target, viewer)

    result = _run(
        script,
        "--force",
        "--adopt-legacy",
        "--python",
        sys.executable,
        "--venv",
        str(target),
        "--dry-run",
        cwd=tmp_path,
    )

    assert result.returncode != 0
    assert "ownership is not proven" in result.stderr
    assert target.exists()


@pytest.mark.parametrize(
    ("script_name", "mode_args"),
    (
        ("install.sh", ("--force",)),
        ("update.sh", ("--skip-git", "--recreate-venv")),
    ),
)
def test_destructive_setup_modes_refuse_symlinked_venv_target(
    tmp_path: Path,
    script_name: str,
    mode_args: tuple[str, ...],
) -> None:
    real_target = tmp_path / "real environment"
    _fake_venv(real_target)
    link = tmp_path / "linked environment"
    link.symlink_to(real_target, target_is_directory=True)

    result = _run(
        SCRIPT_DIR / script_name,
        *mode_args,
        "--dry-run",
        "--python",
        sys.executable,
        "--venv",
        f"{link}/.",
        cwd=tmp_path,
    )

    assert result.returncode != 0
    assert "symbolic link" in result.stderr
    assert link.is_symlink() and real_target.exists()


@pytest.mark.parametrize(
    ("script_name", "mode_args"),
    (
        ("install.sh", ("--force",)),
        ("update.sh", ("--skip-git", "--recreate-venv")),
    ),
)
def test_destructive_setup_modes_never_use_python_inside_target(
    tmp_path: Path,
    script_name: str,
    mode_args: tuple[str, ...],
) -> None:
    target = tmp_path / "viewer environment"
    _fake_venv(target)
    executed = tmp_path / "target-python-ran"
    _write_executable(
        target / "bin" / "python3",
        f"#!/bin/sh\ntouch {str(executed)!r}\nexit 0\n",
    )
    env = dict(
        os.environ,
        PATH=f"{target / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
    )

    result = _run(
        SCRIPT_DIR / script_name,
        *mode_args,
        "--dry-run",
        "--venv",
        str(target),
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode != 0
    assert "selected for replacement/removal" in result.stderr
    assert not executed.exists()
    assert target.exists()


@pytest.mark.parametrize(
    ("script_name", "mode_args"),
    (
        ("install.sh", ("--force",)),
        ("reinstall.sh", ()),
        ("update.sh", ("--skip-git", "--recreate-venv")),
        ("uninstall.sh", ("--keep-desktop",)),
    ),
)
def test_destructive_inspectors_ignore_target_pythonpath_sitecustomize(
    tmp_path: Path,
    script_name: str,
    mode_args: tuple[str, ...],
) -> None:
    target = tmp_path / f"foreign-{Path(script_name).stem}"
    _fake_venv(target)
    executed = tmp_path / f"{Path(script_name).stem}-sitecustomize-ran"
    (target / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(executed)!r}).touch()\n"
    )
    env = dict(
        os.environ,
        PYTHONPATH=str(target),
        CI="true",
        GITLAB_CI="true",
    )

    result = _run(
        SCRIPT_DIR / script_name,
        *mode_args,
        "--dry-run",
        "--python",
        sys.executable,
        "--venv",
        str(target),
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode != 0
    assert "ownership is not proven" in result.stderr
    assert not executed.exists()
    assert target.exists()


def test_install_rejects_missing_and_unknown_arguments() -> None:
    missing = _run(SCRIPT_DIR / "install.sh", "--venv")
    unknown = _run(SCRIPT_DIR / "install.sh", "--definitely-not-a-flag")
    assert missing.returncode != 0
    assert "requires an argument" in missing.stderr
    assert unknown.returncode != 0
    assert "unknown argument" in unknown.stderr


def test_install_runs_all_default_modes_with_fake_venv_creator(tmp_path: Path) -> None:
    template = tmp_path / "venv template"
    _fake_venv(template)
    creator = tmp_path / "fake python"
    _fake_venv_creator(creator)
    target = tmp_path / "installed viewer environment"
    log = tmp_path / "install.log"
    env = dict(
        os.environ,
        FAKE_LOG=str(log),
        FAKE_VENV_TEMPLATE=str(template),
    )

    result = _run(
        SCRIPT_DIR / "install.sh",
        "--python",
        str(creator),
        "--venv",
        str(target),
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "vibe-view install complete" in result.stdout
    calls = log.read_text()
    assert "-e " not in calls and "[viewer,tui]" in calls
    assert "trame" in calls and "textual" in calls
    assert "cli open --help" in calls
    assert "cli desktop --help" in calls
    assert "cli tui --help" in calls
    assert "_record_interpreter" in calls


@pytest.mark.parametrize("script_name", ("install.sh", "update.sh"))
@pytest.mark.parametrize("failure", ("creation", "pip", "verification", "electron"))
def test_venv_replacement_failure_restores_previous_environment(
    tmp_path: Path,
    script_name: str,
    failure: str,
) -> None:
    viewer, script = _copy_script_fixture(tmp_path, script_name)
    installer = viewer / "electron" / "install-electron.py"
    installer.parent.mkdir()
    installer.write_text("# fake reviewed Electron installer\n")

    target = viewer / ".venv"
    _fake_venv(target)
    _mark_owned_venv(target, viewer)
    (target / "old-environment").write_text("working\n")
    template = tmp_path / "replacement template"
    _fake_venv(template)
    (template / "new-environment").write_text("candidate\n")
    creator = tmp_path / "fake creator"
    _fake_venv_creator(creator)
    env = dict(
        os.environ,
        FAKE_VENV_TEMPLATE=str(template),
        FAKE_LOG=str(tmp_path / f"{script_name}-{failure}.log"),
    )
    if failure == "creation":
        env["FAKE_CREATE_EXIT"] = "21"
    elif failure == "pip":
        env["FAKE_PYTHON_FAIL_MATCH"] = "-m pip install --upgrade"
    elif failure == "verification":
        env["FAKE_CLI_FAIL_MATCH"] = "--version"
    else:
        env["FAKE_INSTALLER_EXIT"] = "24"

    args = ["--python", str(creator), "--venv", str(target)]
    if script_name == "install.sh":
        args.append("--force")
    else:
        args.extend(("--skip-git", "--recreate-venv"))
    if failure == "electron":
        args.append("--with-electron")

    result = _run(script, *args, cwd=tmp_path, env=env)

    assert result.returncode != 0
    assert (target / "old-environment").read_text() == "working\n"
    assert not (target / "new-environment").exists()
    assert not list(target.parent.glob(f"{target.name}.previous.*"))
    if failure == "electron":
        assert "restoring the previous state" in result.stderr
        assert "_record_interpreter" not in Path(env["FAKE_LOG"]).read_text()


@pytest.mark.parametrize("script_name", ("install.sh", "update.sh"))
def test_venv_replacement_commits_verified_candidate(
    tmp_path: Path,
    script_name: str,
) -> None:
    viewer, script = _copy_script_fixture(tmp_path, script_name)
    target = viewer / ".venv"
    _fake_venv(target)
    _mark_owned_venv(target, viewer)
    (target / "old-environment").write_text("old\n")
    template = tmp_path / "replacement template"
    _fake_venv(template)
    (template / "new-environment").write_text("new\n")
    creator = tmp_path / "fake creator"
    _fake_venv_creator(creator)
    env = dict(
        os.environ,
        FAKE_VENV_TEMPLATE=str(template),
        FAKE_LOG=str(tmp_path / f"{script_name}-success.log"),
    )
    args = ["--python", str(creator), "--venv", str(target)]
    if script_name == "install.sh":
        args.append("--force")
    else:
        args.extend(("--skip-git", "--recreate-venv"))

    result = _run(script, *args, cwd=tmp_path, env=env)

    assert result.returncode == 0, result.stderr
    assert (target / "new-environment").read_text() == "new\n"
    assert not (target / "old-environment").exists()
    ownership = json.loads((target / ".vibe-view-standalone.json").read_text())
    assert ownership["project"] == str(viewer.resolve())
    assert not list(target.parent.glob(f"{target.name}.previous.*"))


def test_real_replacement_venv_is_created_at_final_path(tmp_path: Path) -> None:
    target = tmp_path / "real viewer environment"
    script = r"""
set -euo pipefail
. "$1"
vibe_view_begin_venv_replacement "$2" 0
trap 'vibe_view_abort_venv_replacement' EXIT
vibe_view_start_venv_replacement "$2" "$3" 0
"$3" -m venv "$2"
"$2/bin/pip" --version >/dev/null
vibe_view_commit_venv_replacement
trap - EXIT
"""
    result = subprocess.run(
        [
            "bash",
            "-c",
            script,
            "transaction-test",
            str(SCRIPT_DIR / "_venv_helpers.sh"),
            str(target),
            sys.executable,
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (target / "bin" / "pip").exists()
    assert not (target / ".vibe-view-venv-transaction").exists()
    assert not list(target.parent.glob(f"{target.name}.previous.*"))


def test_replacement_refuses_foreign_target_appearing_after_begin(
    tmp_path: Path,
) -> None:
    target = tmp_path / "late foreign viewer environment"
    sentinel = target / "foreign-state"
    script = r"""
set -euo pipefail
. "$1"
vibe_view_begin_venv_replacement "$2" 0
mkdir -p "$2"
printf 'version = 3.12\n' > "$2/pyvenv.cfg"
printf foreign > "$2/foreign-state"
rc=0
vibe_view_start_venv_replacement "$2" "$3" 0 || rc=$?
vibe_view_abort_venv_replacement
printf 'RC=%s\n' "$rc"
"""
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            script,
            "transaction-test",
            str(SCRIPT_DIR / "_venv_helpers.sh"),
            str(target),
            str(Path(sys.executable).resolve()),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "RC=0" not in result.stdout
    assert "target changed before replacement mutation" in result.stderr
    assert sentinel.read_text() == "foreign"
    assert not list(target.parent.glob(f"{target.name}.previous.*"))


def test_abort_before_old_venv_move_leaves_it_intact(tmp_path: Path) -> None:
    target = tmp_path / "existing viewer environment"
    _fake_venv(target)
    sentinel = target / "old-environment"
    sentinel.write_text("working\n")
    script = r"""
set -euo pipefail
. "$1"
vibe_view_begin_venv_replacement "$2" 1
VIBE_VIEW_VENV_TX_MUTATION_STARTED=1
vibe_view_abort_venv_replacement
"""

    result = subprocess.run(
        [
            "bash",
            "-c",
            script,
            "transaction-test",
            str(SCRIPT_DIR / "_venv_helpers.sh"),
            str(target),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert sentinel.read_text() == "working\n"
    assert "unowned replacement target" not in result.stderr
    assert not list(target.parent.glob(f"{target.name}.previous.*"))


def test_regular_source_install_resolves_checkout_electron_companion(
    tmp_path: Path,
) -> None:
    target = tmp_path / "installed viewer"
    subprocess.run(
        [sys.executable, "-m", "venv", "--system-site-packages", str(target)],
        text=True,
        capture_output=True,
        check=True,
    )
    install = subprocess.run(
        [
            str(target / "bin" / "python"),
            "-m",
            "pip",
            "install",
            "--no-deps",
            str(VIEWER_DIR),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert install.returncode == 0, install.stderr

    target_site = subprocess.run(
        [
            str(target / "bin" / "python"),
            "-c",
            "import sysconfig; print(sysconfig.get_paths()['purelib'])",
        ],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    probe = subprocess.run(
        [
            str(target / "bin" / "python"),
            "-S",
            "-c",
            (
                "import importlib.util, json, sys; from pathlib import Path; "
                "site=Path(sys.argv[1]); sys.path.insert(0, str(site)); "
                "p=site/'vibeview'/'install_hints.py'; "
                "s=importlib.util.spec_from_file_location('installed_hints', p); "
                "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
                "d=next(site.glob('vibeview-*.dist-info/direct_url.json')); "
                "u=json.loads(d.read_text()); "
                "print(bool(u.get('dir_info', {}).get('editable'))); "
                "print((m.source_project_dir()/'electron').resolve())"
            ),
            target_site,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.splitlines() == ["False", str((VIEWER_DIR / "electron").resolve())]


def test_legacy_pep610_uninstall_requires_explicit_adoption(
    tmp_path: Path,
) -> None:
    viewer, script, _installer = _uninstall_fixture(tmp_path)

    def add_direct_url(venv: Path) -> None:
        site = (
            venv
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages"
            / "vibeview-test.dist-info"
        )
        site.mkdir(parents=True)
        (site / "direct_url.json").write_text(
            json.dumps({"url": viewer.resolve().as_uri(), "dir_info": {}})
        )

    auto_target = viewer / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(auto_target)], check=True)
    add_direct_url(auto_target)
    refused_auto = _run(
        script,
        "--keep-desktop",
        "--python",
        sys.executable,
        cwd=tmp_path,
    )
    assert refused_auto.returncode != 0
    assert "ownership is not proven" in refused_auto.stderr
    assert auto_target.exists()

    adopted_auto = _run(
        script,
        "--keep-desktop",
        "--adopt-legacy",
        "--python",
        sys.executable,
        cwd=tmp_path,
    )
    assert adopted_auto.returncode == 0, adopted_auto.stderr
    assert not auto_target.exists()

    explicit_target = tmp_path / "possibly shared environment"
    subprocess.run([sys.executable, "-m", "venv", str(explicit_target)], check=True)
    add_direct_url(explicit_target)
    explicit = _run(
        script,
        "--keep-desktop",
        "--python",
        sys.executable,
        "--venv",
        str(explicit_target),
        cwd=tmp_path,
    )
    assert explicit.returncode != 0
    assert "ownership is not proven" in explicit.stderr
    assert explicit_target.exists()


def test_update_dry_run_can_preview_missing_environment_without_git(tmp_path: Path) -> None:
    target = tmp_path / "not-created"
    result = _run(
        SCRIPT_DIR / "update.sh",
        "--skip-git",
        "--dry-run",
        "--venv",
        str(target),
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert "Git:          unchanged" in result.stdout
    assert "none found" in result.stdout
    assert "vibe-view[viewer,tui]" in result.stdout
    assert not target.exists()


@pytest.mark.parametrize("target", (Path("/"), VIEWER_DIR.parent))
def test_update_dry_run_refuses_same_unsafe_targets_as_real_run(target: Path) -> None:
    result = _run(
        SCRIPT_DIR / "update.sh",
        "--skip-git",
        "--recreate-venv",
        "--dry-run",
        "--python",
        sys.executable,
        "--venv",
        str(target),
    )

    assert result.returncode != 0
    assert "refusing" in result.stderr and "virtualenv target" in result.stderr


def test_desktop_update_dry_run_is_explicit_about_app_ownership(
    tmp_path: Path,
) -> None:
    target = tmp_path / "not-created"
    result = _run(
        SCRIPT_DIR / "update-desktop.sh",
        "--skip-git",
        "--dry-run",
        "--venv",
        str(target),
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert "sync reviewed runtime + source app" in result.stdout
    assert "packaged apps stay untouched" in result.stdout
    assert not target.exists()


def test_desktop_update_requires_viewer_profile() -> None:
    result = _run(
        SCRIPT_DIR / "update-desktop.sh",
        "--skip-git",
        "--dry-run",
        "--extras",
        "core",
    )

    assert result.returncode != 0
    assert "require an extras profile containing the viewer" in result.stderr


def test_adopt_desktop_requires_desktop_mode() -> None:
    result = _run(
        SCRIPT_DIR / "update.sh",
        "--skip-git",
        "--dry-run",
        "--adopt-desktop",
    )

    assert result.returncode != 0
    assert "--adopt-desktop requires --desktop" in result.stderr


def test_update_rejects_conflicting_branch_selectors() -> None:
    result = _run(SCRIPT_DIR / "update.sh", "--dev", "--release", "--dry-run")
    assert result.returncode != 0
    assert "conflicts" in result.stderr


@pytest.mark.parametrize("option", ("--branch", "--ref"))
def test_update_rejects_empty_or_option_like_ref_values(option: str) -> None:
    empty = _run(SCRIPT_DIR / "update.sh", option, "", "--dry-run")
    assert empty.returncode != 0
    assert "non-empty" in empty.stderr

    option_like = _run(SCRIPT_DIR / "update.sh", option, "--dry-run")
    assert option_like.returncode != 0
    assert "not option" in option_like.stderr


def test_update_rejects_branch_selector_with_skip_git() -> None:
    result = _run(SCRIPT_DIR / "update.sh", "--skip-git", "--dev", "--dry-run")
    assert result.returncode != 0
    assert "cannot be combined" in result.stderr


def test_update_does_not_auto_select_shared_repo_venv(tmp_path: Path) -> None:
    viewer, update_script = _copy_script_fixture(tmp_path, "update.sh")
    shared_python = tmp_path / ".venv" / "bin" / "python"
    shared_python.parent.mkdir(parents=True)
    shared_python.write_text("#!/usr/bin/env bash\nexit 0\n")
    shared_python.chmod(0o755)

    result = _run(update_script, "--skip-git", "--dry-run", cwd=viewer)

    assert result.returncode == 0, result.stderr
    assert "venv:         <not found>" in result.stdout
    assert "none found; update would refuse" in result.stdout


def test_update_recreate_defaults_to_viewer_owned_venv(tmp_path: Path) -> None:
    viewer, update_script = _copy_script_fixture(tmp_path, "update.sh")
    shared_python = tmp_path / ".venv" / "bin" / "python"
    shared_python.parent.mkdir(parents=True)
    shared_python.write_text("#!/usr/bin/env bash\nexit 0\n")
    shared_python.chmod(0o755)

    result = _run(
        update_script,
        "--skip-git",
        "--recreate-venv",
        "--dry-run",
        cwd=viewer,
    )

    assert result.returncode == 0, result.stderr
    assert f"venv:         {viewer / '.venv'}" in result.stdout
    assert f"[venv] would create: {viewer / '.venv'}" in result.stdout


def test_update_recreate_honours_missing_venv_environment_override(tmp_path: Path) -> None:
    target = tmp_path / "custom viewer environment"
    env = dict(os.environ, VIBE_VIEW_VENV=str(target))

    result = _run(
        SCRIPT_DIR / "update.sh",
        "--skip-git",
        "--recreate-venv",
        "--dry-run",
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert f"venv:         {target}" in result.stdout
    assert f"[venv] would create: {target}" in result.stdout
    assert not target.exists()


def test_update_rejects_creator_python_without_recreation(tmp_path: Path) -> None:
    viewer, update_script = _copy_script_fixture(tmp_path, "update.sh")
    _fake_venv(viewer / ".venv")
    log = tmp_path / "update.log"
    env = dict(os.environ, FAKE_LOG=str(log))

    result = _run(
        update_script,
        "--skip-git",
        "--python",
        str(tmp_path / "missing creator"),
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode != 0
    assert "--python applies only when --recreate-venv" in result.stderr
    assert not log.exists()


def test_desktop_update_invokes_source_app_refresh(tmp_path: Path) -> None:
    viewer = tmp_path / "viewer copy"
    scripts = viewer / "scripts"
    scripts.mkdir(parents=True)
    for name in ("update-desktop.sh", "update.sh", "_venv_helpers.sh"):
        shutil.copy2(SCRIPT_DIR / name, scripts / name)
    _copy_shared_lifecycle_lock(tmp_path)
    installer = viewer / "electron" / "install-electron.py"
    installer.parent.mkdir()
    installer.write_text("SOURCE_DESKTOP_UPDATE_PROTOCOL = 1\n")
    _fake_venv(viewer / ".venv")
    log = tmp_path / "desktop-update.log"
    env = dict(os.environ, FAKE_LOG=str(log))

    result = _run(
        scripts / "update-desktop.sh",
        "--skip-git",
        "--extras",
        "viewer",
        "--adopt-desktop",
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "vibe-view update complete" in result.stdout
    calls = log.read_text()
    installer_call = "install-electron.py --refresh-app --adopt-app"
    assert installer_call in calls
    assert calls.index(installer_call) < calls.index("_record_interpreter")


def test_desktop_update_rejects_legacy_installer_protocol(tmp_path: Path) -> None:
    viewer = tmp_path / "viewer"
    scripts = viewer / "scripts"
    scripts.mkdir(parents=True)
    for name in ("update-desktop.sh", "update.sh", "_venv_helpers.sh"):
        shutil.copy2(SCRIPT_DIR / name, scripts / name)
    _copy_shared_lifecycle_lock(tmp_path)
    installer = viewer / "electron" / "install-electron.py"
    installer.parent.mkdir()
    installer.write_text("# legacy installer without safety protocol\n")
    _fake_venv(viewer / ".venv")
    log = tmp_path / "legacy-update.log"
    env = dict(os.environ, FAKE_LOG=str(log))

    result = _run(
        scripts / "update-desktop.sh",
        "--skip-git",
        "--extras",
        "viewer",
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode != 0
    assert "does not support safe desktop updates" in result.stderr
    assert not log.exists()


def test_refused_desktop_refresh_does_not_record_interpreter(
    tmp_path: Path,
) -> None:
    viewer = tmp_path / "viewer"
    scripts = viewer / "scripts"
    scripts.mkdir(parents=True)
    for name in ("update-desktop.sh", "update.sh", "_venv_helpers.sh"):
        shutil.copy2(SCRIPT_DIR / name, scripts / name)
    _copy_shared_lifecycle_lock(tmp_path)
    installer = viewer / "electron" / "install-electron.py"
    installer.parent.mkdir()
    installer.write_text("SOURCE_DESKTOP_UPDATE_PROTOCOL = 1\n")
    _fake_venv(viewer / ".venv")
    log = tmp_path / "refused-update.log"
    env = dict(os.environ, FAKE_LOG=str(log), FAKE_INSTALLER_EXIT="2")

    result = _run(
        scripts / "update-desktop.sh",
        "--skip-git",
        "--extras",
        "viewer",
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode == 2
    assert "vibe-view update complete" not in result.stdout
    calls = log.read_text()
    assert "install-electron.py --refresh-app" in calls
    assert "_record_interpreter" not in calls


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_default_update_explains_branch_without_upstream(tmp_path: Path) -> None:
    checkout = tmp_path / "local checkout"
    scripts = checkout / "vibe-view" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("update.sh", "_venv_helpers.sh"):
        shutil.copy2(SCRIPT_DIR / name, scripts / name)
    _copy_shared_lifecycle_lock(checkout)
    _git(checkout, "init")
    _git(checkout, "checkout", "-b", "local-work")
    _git(checkout, "config", "user.name", "Setup Script Test")
    _git(checkout, "config", "user.email", "mpei@vibe-qc.com")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", "local work")

    result = _run(scripts / "update.sh", "--dry-run", cwd=tmp_path)

    assert result.returncode != 0
    assert "has no configured remote upstream" in result.stderr
    assert "branch --set-upstream-to REMOTE/BRANCH" in result.stderr
    assert "update.sh --skip-git" in result.stderr


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_default_update_honours_local_only_upstream_without_fetch(tmp_path: Path) -> None:
    checkout = tmp_path / "local checkout"
    scripts = checkout / "vibe-view" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("update.sh", "_venv_helpers.sh"):
        shutil.copy2(SCRIPT_DIR / name, scripts / name)
    _copy_shared_lifecycle_lock(checkout)
    marker = checkout / "marker.txt"
    marker.write_text("old\n")
    _git(checkout, "init")
    _git(checkout, "checkout", "-b", "base")
    _git(checkout, "config", "user.name", "Setup Script Test")
    _git(checkout, "config", "user.email", "mpei@vibe-qc.com")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", "base")
    _git(checkout, "checkout", "-b", "local-work")
    _git(checkout, "branch", "--set-upstream-to", "base", "local-work")
    _git(checkout, "checkout", "base")
    marker.write_text("new\n")
    _git(checkout, "add", "marker.txt")
    _git(checkout, "commit", "-m", "advance local upstream")
    _git(checkout, "checkout", "local-work")
    _fake_venv(checkout / "vibe-view" / ".venv")
    log = tmp_path / "local-upstream.log"
    env = dict(os.environ, FAKE_LOG=str(log))

    result = _run(scripts / "update.sh", cwd=tmp_path, env=env)

    assert result.returncode == 0, result.stderr
    assert "Git upstream: base" in result.stdout
    assert "local configured upstream 'base' (no fetch)" in result.stdout
    assert marker.read_text() == "new\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_default_update_uses_configured_renamed_upstream(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    scripts = seed / "vibe-view" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("update.sh", "_venv_helpers.sh"):
        shutil.copy2(SCRIPT_DIR / name, scripts / name)
    _copy_shared_lifecycle_lock(seed)
    (seed / ".gitignore").write_text("vibe-view/.venv/\n")
    marker = seed / "marker.txt"
    marker.write_text("old\n")
    _git(seed, "init")
    _git(seed, "checkout", "-b", "published-name")
    _git(seed, "config", "user.name", "Setup Script Test")
    _git(seed, "config", "user.email", "mpei@vibe-qc.com")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "initial")

    remote = tmp_path / "remote.git"
    _git(tmp_path, "clone", "--bare", str(seed), str(remote))
    consumer = tmp_path / "consumer"
    _git(tmp_path, "clone", str(remote), str(consumer))
    _git(consumer, "branch", "-m", "local-install")
    _git(consumer, "remote", "rename", "origin", "reviewed")
    _fake_venv(consumer / "vibe-view" / ".venv")

    _git(seed, "remote", "add", "origin", str(remote))
    marker.write_text("new\n")
    _git(seed, "add", "marker.txt")
    _git(seed, "commit", "-m", "advance renamed upstream")
    _git(seed, "push", "origin", "published-name")

    log = tmp_path / "renamed-upstream.log"
    env = dict(os.environ, FAKE_LOG=str(log))
    update_script = consumer / "vibe-view" / "scripts" / "update.sh"
    result = _run(update_script, cwd=tmp_path, env=env)

    assert result.returncode == 0, result.stderr
    assert (consumer / "marker.txt").read_text() == "new\n"
    assert _git(consumer, "branch", "--show-current").stdout.strip() == "local-install"
    assert "reviewed/published-name" in result.stdout
    assert "configured upstream" in result.stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_desktop_update_refuses_historical_unsafe_branch(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    scripts = seed / "vibe-view" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("update-desktop.sh", "update.sh", "_venv_helpers.sh"):
        shutil.copy2(SCRIPT_DIR / name, scripts / name)
    _copy_shared_lifecycle_lock(seed)
    installer = seed / "vibe-view" / "electron" / "install-electron.py"
    installer.parent.mkdir()
    installer.write_text("# legacy installer without safety protocol\n")
    (seed / ".gitignore").write_text("vibe-view/.venv/\n")
    _git(seed, "init")
    _git(seed, "checkout", "-b", "main")
    _git(seed, "config", "user.name", "Setup Script Test")
    _git(seed, "config", "user.email", "mpei@vibe-qc.com")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "legacy desktop installer")
    _git(seed, "branch", "legacy")
    installer.write_text("SOURCE_DESKTOP_UPDATE_PROTOCOL = 1\n")
    _git(seed, "add", str(installer.relative_to(seed)))
    _git(seed, "commit", "-m", "safe desktop installer")

    remote = tmp_path / "remote.git"
    _git(tmp_path, "clone", "--bare", str(seed), str(remote))
    consumer = tmp_path / "consumer"
    _git(tmp_path, "clone", str(remote), str(consumer))
    _fake_venv(consumer / "vibe-view" / ".venv")
    log = tmp_path / "historical-update.log"
    env = dict(os.environ, FAKE_LOG=str(log))

    result = _run(
        consumer / "vibe-view" / "scripts" / "update-desktop.sh",
        "--branch",
        "legacy",
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode != 0
    assert "does not support safe desktop updates" in result.stderr
    assert not log.exists()
    assert _git(consumer, "branch", "--show-current").stdout.strip() == "main"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_recreate_failure_after_fast_forward_restores_installed_environment(
    tmp_path: Path,
) -> None:
    seed = tmp_path / "seed"
    scripts = seed / "vibe-view" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("update.sh", "_venv_helpers.sh"):
        shutil.copy2(SCRIPT_DIR / name, scripts / name)
    _copy_shared_lifecycle_lock(seed)
    (seed / ".gitignore").write_text("vibe-view/.venv/\n")
    marker = seed / "marker.txt"
    marker.write_text("old source\n")
    _git(seed, "init")
    _git(seed, "checkout", "-b", "main")
    _git(seed, "config", "user.name", "Setup Script Test")
    _git(seed, "config", "user.email", "mpei@vibe-qc.com")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "initial")

    remote = tmp_path / "remote.git"
    _git(tmp_path, "clone", "--bare", str(seed), str(remote))
    consumer = tmp_path / "consumer"
    _git(tmp_path, "clone", str(remote), str(consumer))
    target = consumer / "vibe-view" / ".venv"
    _fake_venv(target)
    _mark_owned_venv(target, consumer / "vibe-view")
    (target / "installed-version").write_text("old package\n")

    _git(seed, "remote", "add", "origin", str(remote))
    marker.write_text("new source\n")
    _git(seed, "add", "marker.txt")
    _git(seed, "commit", "-m", "advance")
    _git(seed, "push", "origin", "main")

    template = tmp_path / "candidate environment"
    _fake_venv(template)
    creator = tmp_path / "fake creator"
    _fake_venv_creator(creator)
    env = dict(
        os.environ,
        FAKE_LOG=str(tmp_path / "failed-fast-forward.log"),
        FAKE_PYTHON_FAIL_MATCH="-m pip install --upgrade",
        FAKE_VENV_TEMPLATE=str(template),
    )
    update_script = consumer / "vibe-view" / "scripts" / "update.sh"
    result = _run(
        update_script,
        "--recreate-venv",
        "--python",
        str(creator),
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode != 0
    assert (consumer / "marker.txt").read_text() == "new source\n"
    assert (target / "installed-version").read_text() == "old package\n"
    old_cli = subprocess.run(
        [str(target / "bin" / "vibe-view"), "--version"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert old_cli.returncode == 0
    assert "vibe-view 9.9.9" in old_cli.stdout
    assert not list(target.parent.glob(f"{target.name}.previous.*"))


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_update_fast_forwards_current_branch_then_refuses_dirty_tree(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    scripts = seed / "vibe-view" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("update.sh", "_venv_helpers.sh"):
        shutil.copy2(SCRIPT_DIR / name, scripts / name)
    _copy_shared_lifecycle_lock(seed)
    (seed / ".gitignore").write_text("vibe-view/.venv/\n")
    marker = seed / "marker.txt"
    marker.write_text("old\n")
    _git(seed, "init")
    _git(seed, "checkout", "-b", "main")
    _git(seed, "config", "user.name", "Setup Script Test")
    _git(seed, "config", "user.email", "mpei@vibe-qc.com")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "initial")

    remote = tmp_path / "remote.git"
    _git(tmp_path, "clone", "--bare", str(seed), str(remote))
    consumer = tmp_path / "consumer checkout"
    _git(tmp_path, "clone", str(remote), str(consumer))
    _fake_venv(consumer / "vibe-view" / ".venv")

    _git(seed, "remote", "add", "origin", str(remote))
    marker.write_text("new\n")
    _git(seed, "add", "marker.txt")
    _git(seed, "commit", "-m", "advance")
    _git(seed, "push", "origin", "main")

    log = tmp_path / "git-update.log"
    env = dict(os.environ, FAKE_LOG=str(log))
    update_script = consumer / "vibe-view" / "scripts" / "update.sh"
    result = _run(update_script, cwd=tmp_path, env=env)

    assert result.returncode == 0, result.stderr
    assert (consumer / "marker.txt").read_text() == "new\n"
    assert "fast-forwarding" in result.stdout
    assert "vibe-view update complete" in result.stdout

    (consumer / "marker.txt").write_text("dirty\n")
    dirty = _run(update_script, cwd=tmp_path, env=env)
    assert dirty.returncode != 0
    assert "uncommitted tracked changes" in dirty.stderr


def _copy_script_fixture(tmp_path: Path, script_name: str) -> tuple[Path, Path]:
    viewer = tmp_path / "viewer copy"
    scripts = viewer / "scripts"
    scripts.mkdir(parents=True)
    for name in (script_name, "_venv_helpers.sh"):
        shutil.copy2(SCRIPT_DIR / name, scripts / name)
    _copy_shared_lifecycle_lock(tmp_path)
    return viewer, scripts / script_name


def test_build_discovers_installer_created_project_venv(tmp_path: Path) -> None:
    viewer, build_script = _copy_script_fixture(tmp_path, "build.sh")
    bindir = viewer / ".venv" / "bin"
    bindir.mkdir(parents=True)
    log = tmp_path / "python-args.log"
    python = bindir / "python"
    python.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$FAKE_LOG"\n'
        'if [ "${1:-}" = "--version" ]; then echo "Python 3.13.0"; fi\n'
        'if [ "${1:-}" = "-m" ] && [ "${2:-}" = "build" ]; then\n'
        "  mkdir -p dist\n"
        "  : > dist/vibeview-test.whl\n"
        "fi\n"
    )
    python.chmod(0o755)

    env = dict(os.environ, FAKE_LOG=str(log))
    result = _run(build_script, cwd=tmp_path, env=env)

    assert result.returncode == 0, result.stderr
    assert "Python 3.13.0" in result.stdout
    assert "-m build --wheel" in log.read_text()


def test_build_accepts_python_command_name_override(tmp_path: Path) -> None:
    _viewer, build_script = _copy_script_fixture(tmp_path, "build.sh")
    commands = tmp_path / "commands"
    fake = tmp_path / "fake venv"
    _fake_venv(fake)
    commands.mkdir()
    shutil.copy2(fake / "bin" / "python", commands / "python3.13")
    log = tmp_path / "build-command.log"
    env = dict(
        os.environ,
        FAKE_LOG=str(log),
        PATH=f"{commands}{os.pathsep}{os.environ.get('PATH', '')}",
        VIBE_VIEW_VENV_PYTHON="python3.13",
    )

    result = _run(build_script, cwd=tmp_path, env=env)

    assert result.returncode == 0, result.stderr
    assert "-m build --wheel" in log.read_text()


def test_launch_discovers_installer_created_project_venv(tmp_path: Path) -> None:
    viewer, launch_script = _copy_script_fixture(tmp_path, "launch.sh")
    bindir = viewer / ".venv" / "bin"
    bindir.mkdir(parents=True)
    (bindir / "python").write_text("#!/usr/bin/env bash\nexit 0\n")
    (bindir / "python").chmod(0o755)
    log = tmp_path / "vibe-view-args.log"
    cli = bindir / "vibe-view"
    cli.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" > "$FAKE_LOG"\n')
    cli.chmod(0o755)
    qvf = tmp_path / "sample.qvf"
    qvf.write_bytes(b"test")

    env = dict(os.environ, FAKE_LOG=str(log))
    result = _run(
        launch_script,
        str(qvf),
        "--no-browser",
        "--port",
        "9090",
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    args = log.read_text()
    assert args.startswith(f"open {qvf}")
    assert "--no-browser --port 9090" in args


def test_launch_preserves_equals_style_port_and_log_file(tmp_path: Path) -> None:
    viewer, launch_script = _copy_script_fixture(tmp_path, "launch.sh")
    _fake_venv(viewer / ".venv")
    log = tmp_path / "vibe-view-equals.log"
    qvf = tmp_path / "sample.qvf"
    qvf.write_bytes(b"test")
    env = dict(
        os.environ,
        FAKE_LOG=str(log),
        VIBE_VIEW_LOG_FILE=str(tmp_path / "unwanted-default.log"),
    )

    result = _run(
        launch_script,
        str(qvf),
        "--port=9090",
        "--log-file=chosen.log",
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    args = log.read_text()
    assert "--port=9090" in args
    assert "--log-file=chosen.log" in args
    assert "--port 8080" not in args
    assert "unwanted-default.log" not in args


def test_launch_without_extra_flags_works_in_macos_bash_3(tmp_path: Path) -> None:
    viewer, launch_script = _copy_script_fixture(tmp_path, "launch.sh")
    _fake_venv(viewer / ".venv")
    log = tmp_path / "vibe-view-no-extras.log"
    qvf = tmp_path / "sample.qvf"
    qvf.write_bytes(b"test")
    env = dict(os.environ, FAKE_LOG=str(log))

    result = subprocess.run(
        ["/bin/bash", str(launch_script), str(qvf)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert f"cli open {qvf} --port 8080" in log.read_text()
