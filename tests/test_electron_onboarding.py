"""Static contracts for the packaged desktop setup flow."""

from __future__ import annotations

from pathlib import Path

ELECTRON = Path(__file__).resolve().parents[1] / "electron"


def test_desktop_probes_python_311_and_keeps_searching() -> None:
    main = (ELECTRON / "main.js").read_text(encoding="utf-8")

    assert "inspectSupportedPython(cmd)" in main
    assert "sys.version_info >= (3, 11)" in main
    assert "if (supported) return supported" in main


def test_desktop_serve_directory_falls_back_from_filesystem_root() -> None:
    """A Dock/Finder double-click has no handshake config and the launcher
    cwd is the filesystem root; New structure would then try to write
    /new-structure.qvf and fail with EROFS. The serve directory must fall
    back to the home directory instead."""
    main = (ELECTRON / "main.js").read_text(encoding="utf-8")

    assert "function serveDirectory()" in main
    assert "path.parse(cwd).root" in main
    assert "os.homedir()" in main
    assert "cwd: serveDirectory()" in main


def test_setup_uses_detected_interpreter_and_versioned_wheel() -> None:
    setup = (ELECTRON / "setup.html").read_text(encoding="utf-8")

    assert 'const bootstrapPython = python || (isWin ? "py" : "python3")' in setup
    assert "posixQuote(bootstrapPython) + \" -m venv \"" in setup
    assert "vibeview-\" +" in setup
    assert 'appVersion + "-py3-none-any.whl"' in setup
    assert "https://vibe-qc.com/vibe-view/docs/_static/downloads/vibeview-" in setup
    assert "https://vibe-qc.com/vibe-view/docs/desktop.html" in setup
    assert "https://vibe-qc.com/docs/" not in setup


def test_desktop_cli_uses_the_recorded_source_checkout(
    monkeypatch, tmp_path: Path
) -> None:
    import vibeview.install_hints as hints
    from vibeview.cli import _electron_dir

    project = tmp_path / "vibe view"
    monkeypatch.setattr(hints, "source_project_dir", lambda: project)

    assert _electron_dir() == project / "electron"
