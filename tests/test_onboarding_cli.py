"""Focused coverage for the standalone first-run CLI commands."""

from __future__ import annotations

import io
import json
import tomllib
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from vibeview.cli import main


def test_demo_archive_is_reproducible() -> None:
    from vibeview.onboarding import demo_qvf_bytes

    first = demo_qvf_bytes()
    second = demo_qvf_bytes()

    assert first == second
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        assert {member.date_time for member in archive.infolist()} == {
            (1980, 1, 1, 0, 0, 0)
        }


def test_demo_creates_valid_project_authored_qvf(tmp_path: Path) -> None:
    from vibeview.qvf import QVFReader

    output = tmp_path / "water-demo.qvf"
    result = CliRunner().invoke(main, ["demo", "--output", str(output)])

    assert result.exit_code == 0, result.output
    assert output.is_file()
    assert "without requiring vibe-qc" not in result.output
    assert "vibe-view open" in result.output
    assert "vibe-view tui" in result.output
    assert "vibe-view desktop" in result.output

    reader = QVFReader(output)
    structure = reader.read_structure()
    assert [atom.symbol for atom in structure.atoms] == ["O", "H", "H"]
    assert reader.manifest.source.program == "vibe-view"
    reader.close()


def test_demo_refuses_overwrite_unless_forced(tmp_path: Path) -> None:
    output = tmp_path / "demo.qvf"
    output.write_bytes(b"keep me")

    refused = CliRunner().invoke(main, ["demo", "-o", str(output)])
    assert refused.exit_code != 0
    assert "already exists" in refused.output
    assert output.read_bytes() == b"keep me"

    replaced = CliRunner().invoke(main, ["demo", "-o", str(output), "--force"])
    assert replaced.exit_code == 0, replaced.output
    assert output.read_bytes().startswith(b"PK")


def test_demo_force_write_failure_preserves_existing_file(
    monkeypatch, tmp_path: Path
) -> None:
    import vibeview.onboarding as onboarding

    output = tmp_path / "demo.qvf"
    output.write_bytes(b"existing archive")

    def partial_write(handle, payload):
        handle.write(payload[:8])
        raise OSError("simulated disk-full failure")

    monkeypatch.setattr(onboarding, "_write_and_sync", partial_write)

    with pytest.raises(OSError, match="disk-full"):
        onboarding.write_demo(output, force=True)

    assert output.read_bytes() == b"existing archive"
    assert list(tmp_path.glob(".demo.qvf.*.tmp")) == []


def test_demo_non_force_does_not_overwrite_a_racing_writer(
    monkeypatch, tmp_path: Path
) -> None:
    import vibeview.onboarding as onboarding

    output = tmp_path / "demo.qvf"
    real_write = onboarding._write_and_sync

    def racing_write(handle, payload):
        real_write(handle, payload)
        output.write_bytes(b"created by another process")

    monkeypatch.setattr(onboarding, "_write_and_sync", racing_write)

    with pytest.raises(FileExistsError):
        onboarding.write_demo(output)

    assert output.read_bytes() == b"created by another process"
    assert list(tmp_path.glob(".demo.qvf.*.tmp")) == []


def test_demo_no_browser_requires_open(tmp_path: Path) -> None:
    output = tmp_path / "demo.qvf"
    result = CliRunner().invoke(
        main, ["demo", "-o", str(output), "--no-browser"]
    )
    assert result.exit_code != 0
    assert "--no-browser requires --open" in result.output
    assert not output.exists()


def test_examples_copy_is_safe_and_self_contained(tmp_path: Path) -> None:
    from vibeview.qvf import QVFReader

    destination = tmp_path / "onboarding examples"
    result = CliRunner().invoke(main, ["examples", "--copy", str(destination)])
    assert result.exit_code == 0, result.output
    assert {path.name for path in destination.iterdir()} == {
        "README.txt",
        "water.qvf",
        "water.xyz",
    }
    assert "Mozilla Public License 2.0" in (destination / "README.txt").read_text()
    reader = QVFReader(destination / "water.qvf")
    assert len(reader.read_structure().atoms) == 3
    reader.close()

    (destination / "water.xyz").write_text("user data\n")
    refused = CliRunner().invoke(main, ["examples", "--copy", str(destination)])
    assert refused.exit_code != 0
    assert (destination / "water.xyz").read_text() == "user data\n"

    replaced = CliRunner().invoke(
        main, ["examples", "--copy", str(destination), "--force"]
    )
    assert replaced.exit_code == 0, replaced.output
    assert (destination / "water.xyz").read_text().startswith("3\n")


def test_examples_force_write_failure_preserves_existing_file(
    monkeypatch, tmp_path: Path
) -> None:
    import vibeview.onboarding as onboarding

    destination = tmp_path / "examples"
    destination.mkdir()
    water = destination / "water.xyz"
    water.write_bytes(b"existing example")

    def partial_write(handle, payload):
        handle.write(payload[:3])
        raise OSError("simulated example write failure")

    monkeypatch.setattr(onboarding, "_write_and_sync", partial_write)

    with pytest.raises(OSError, match="example write failure"):
        onboarding.copy_examples(destination, force=True)

    assert water.read_bytes() == b"existing example"
    assert list(destination.glob(".*.tmp")) == []


def test_doctor_json_tolerates_all_optional_modules_missing(monkeypatch) -> None:
    import vibeview.onboarding as onboarding

    optional_modules = {
        requirement.module
        for requirements in onboarding._OPTIONAL_REQUIREMENTS.values()
        for requirement in requirements
    }
    monkeypatch.setattr(
        onboarding,
        "_module_available",
        lambda module: module not in optional_modules,
    )
    monkeypatch.setattr(onboarding, "_resource_available", lambda _parts: True)

    result = CliRunner().invoke(main, ["doctor", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["healthy"] is True
    assert report["capabilities"]["browser"]["available"] is False
    assert report["capabilities"]["tui"]["available"] is False
    assert report["capabilities"]["ase_import"]["available"] is False
    assert "[viewer]" in report["capabilities"]["browser"]["install_hint"]


def test_doctor_human_reports_optional_remediation_without_failure(monkeypatch) -> None:
    import vibeview.onboarding as onboarding

    monkeypatch.setattr(
        onboarding,
        "_module_available",
        lambda module: module not in {"trame", "trame_vtk", "trame_vuetify", "uvicorn"},
    )
    monkeypatch.setattr(onboarding, "_resource_available", lambda _parts: True)

    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "Core install: ready" in result.output
    assert "browser      unavailable" in result.output
    assert "[viewer]" in result.output


def test_doctor_json_remains_parseable_when_core_is_unhealthy(monkeypatch) -> None:
    import vibeview.onboarding as onboarding

    monkeypatch.setattr(onboarding, "_module_available", lambda module: module != "numpy")
    monkeypatch.setattr(onboarding, "_resource_available", lambda _parts: True)

    result = CliRunner().invoke(main, ["doctor", "--json"])
    assert result.exit_code == 1
    report = json.loads(result.output)
    assert report["healthy"] is False
    assert "numpy" in report["capabilities"]["core"]["missing"]


def test_formats_json_uses_public_capability_registry() -> None:
    result = CliRunner().invoke(main, ["formats", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    formats = {row["format_name"]: row for row in report["formats"]}
    assert formats["qvf"]["provider"] == "built-in"
    assert formats["qvf"]["available"] is True
    assert ".qvf" in formats["qvf"]["extensions"]
    assert "ase" in formats
    if not formats["ase"]["available"]:
        assert "[ase]" in formats["ase"]["install_hint"]


def test_formats_human_shows_provider_and_availability() -> None:
    result = CliRunner().invoke(main, ["formats"])
    assert result.exit_code == 0, result.output
    assert "qvf [ready]" in result.output
    assert "provider:   built-in" in result.output
    assert "extensions: .qvf" in result.output


def test_onboarding_resources_are_declared_as_package_data() -> None:
    viewer_dir = Path(__file__).resolve().parents[1]
    config = tomllib.loads((viewer_dir / "pyproject.toml").read_text())
    package_data = config["tool"]["setuptools"]["package-data"]["vibeview"]
    assert "resources/examples/*.xyz" in package_data
    assert "resources/examples/*.txt" in package_data
    manifest = (viewer_dir / "MANIFEST.in").read_text()
    assert "*.txt *.xyz" in manifest


def test_install_hint_uses_editable_checkout_when_present(monkeypatch, tmp_path: Path) -> None:
    import vibeview.install_hints as hints

    monkeypatch.setattr(hints, "source_project_dir", lambda: tmp_path / "vibe view")
    command = hints.install_hint("viewer")
    assert " -m pip install -e " in command
    assert "vibe view[viewer]" in command


def test_install_hint_preserves_regular_local_source_install(
    monkeypatch, tmp_path: Path
) -> None:
    import vibeview.install_hints as hints

    project = (tmp_path / "vibe view").resolve()
    monkeypatch.setattr(hints, "source_project_dir", lambda: project)
    monkeypatch.setattr(hints, "_direct_source_install", lambda: (project, False))

    command = hints.install_hint("viewer")

    assert " -m pip install " in command
    assert " -e " not in command
    assert "vibe view[viewer]" in command


def test_pep610_regular_install_recovers_its_source_checkout(
    monkeypatch, tmp_path: Path
) -> None:
    import vibeview.install_hints as hints

    project = tmp_path / "vibe view"
    (project / "scripts").mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname='vibeview'\n")

    class InstalledDistribution:
        @staticmethod
        def read_text(name: str) -> str | None:
            assert name == "direct_url.json"
            return json.dumps(
                {
                    "url": project.resolve().as_uri(),
                    "dir_info": {"editable": False},
                }
            )

    monkeypatch.setattr(
        hints.metadata,
        "distribution",
        lambda _name: InstalledDistribution(),
    )

    assert hints._direct_source_install() == (project.resolve(), False)


def test_pep610_windows_drive_url_uses_platform_path_conversion(monkeypatch) -> None:
    import vibeview.install_hints as hints

    def windows_conversion(url: str, *, require_scheme: bool = False) -> str:
        assert url == "file:///C:/Users/USER/vibe%20view"
        assert require_scheme is True
        return r"C:\Users\USER\vibe view"

    monkeypatch.setattr(hints, "url2pathname", windows_conversion)

    converted = hints._file_url_path("file:///C:/Users/USER/vibe%20view")

    assert str(converted) == r"C:\Users\USER\vibe view"


def test_install_hint_uses_hosted_wheel_outside_checkout(monkeypatch) -> None:
    import vibeview.install_hints as hints

    monkeypatch.setattr(hints, "source_project_dir", lambda: None)
    monkeypatch.setattr(hints, "hosted_wheel_url", lambda: "https://example.test/vibeview.whl")
    command = hints.install_hint("tui")
    assert "vibeview[tui] @ https://example.test/vibeview.whl" in command
    assert " -e " not in command
