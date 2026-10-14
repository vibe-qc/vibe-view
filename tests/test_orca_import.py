"""ORCA dispatch, isolated GBW export and raw MCD rendering regressions."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner

from vibeview.cli import main
from vibeview.converters import convert_bytes_to_qvf, convert_to_qvf, detect_format
from vibeview.import_workflow import collect_import_inputs
from vibeview.qvf import QVFReader
from vibeview.renderers.spectra import SpectraRenderer, spectrum_style


def _molecule() -> bytes:
    return json.dumps({"Molecule": {
        "Atoms": [{"ElementNumber": 2, "Coords": [0, 0, 0],
                   "Basis": [{"Shell": "s", "Exponents": [1.0], "Coefficients": [1.0]}]}],
        "Charge": 0, "Multiplicity": 1, "CoordinateUnits": "Angs", "HFTyp": "RHF",
        "MolecularOrbitals": {"EnergyUnit": "Eh", "MOs": [
            {"MOCoefficients": [1.0], "Occupancy": 2.0, "OrbitalEnergy": -0.5},
        ]},
    }}).encode()


def test_orca_detection_is_specific_and_upload_probes_its_own_data(tmp_path):
    generic = tmp_path / "settings.json"
    generic.write_text('{"theme": "dark"}')
    exported = tmp_path / "orbitals.JSON"
    exported.write_bytes(_molecule())
    assert detect_format(generic) is None
    assert detect_format(exported) == "orca"
    for suffix in (".property.json", ".property.txt", ".gbw", ".mcd"):
        assert detect_format("sample" + suffix.upper()) == "orca"
    paths, _ = collect_import_inputs([tmp_path])
    assert paths == [exported]
    with QVFReader(convert_bytes_to_qvf(_molecule(), "upload.json")) as reader:
        assert "wavefunction.gto" in [section.kind for section in reader.sections]


def test_mcd_preserves_sign_duplicates_and_units_in_browser_and_terminal(tmp_path, monkeypatch):
    from vibeview.tui.plots import spectra_plot

    source = tmp_path / "sample.mcd"
    source.write_text("10000 -0.2 0.5\n10000 -0.2 0.5\n20000 0.1 0.4\n")
    figures = []
    monkeypatch.setattr(
        "plotly.graph_objects.Figure.to_html",
        lambda self, **kwargs: figures.append(self) or "<div>plot</div>",
    )
    with QVFReader(convert_to_qvf(source)) as reader:
        data = reader.read_spectra("mcd")
        np.testing.assert_array_equal(data.frequencies, [10000, 10000, 20000])
        np.testing.assert_array_equal(data.intensities, [-0.2, -0.2, 0.1])
        np.testing.assert_array_equal(
            reader.read_spectra("absorption").intensities, [0.5, 0.5, 0.4]
        )
        section = reader.get_section("mcd")
        style = spectrum_style(section, data.frequencies)
        assert style.x_unit_short == "cm⁻¹"
        assert style.y_label == "L-R oscillator strength"
        SpectraRenderer(section, reader).render_to_html(display="sticks", x_unit="eV")
        assert figures[0].layout.xaxis.title.text == "Energy (eV)"
        assert figures[0].layout.yaxis.title.text == "L-R oscillator strength"
        assert spectra_plot(reader, "mcd", 80, 24).ylabel == "L-R oscillator strength"


@pytest.mark.parametrize("content", ["1 2", "1 2 3 4", "1 nan 3", "not a spectrum"])
def test_invalid_mcd_fails_before_replacing_import_destination(tmp_path, content):
    source = tmp_path / "sample.mcd"
    source.write_text(content)
    target = tmp_path / "sample.qvf"
    target.write_bytes(b"keep original")
    result = CliRunner().invoke(main, ["import", str(source), "-o", str(target), "--force"])
    assert result.exit_code != 0
    assert target.read_bytes() == b"keep original"


def test_gbw_export_uses_private_copy_and_explicit_minimal_config(tmp_path, monkeypatch):
    source = tmp_path / "sample.gbw"
    source.write_bytes(b"synthetic GBW")
    nearby_config = tmp_path / "orca.json.conf"
    nearby_config.write_text('{"2elIntegrals": ["MO_IJKL"]}')
    calls = []

    def export(args, *, cwd, **kwargs):
        calls.append(Path(cwd))
        assert args == ["/synthetic/orca_2json", "wavefunction.gbw", "-json"]
        assert cwd != source.parent
        assert (cwd / "wavefunction.gbw").read_bytes() == b"synthetic GBW"
        assert json.loads((cwd / "wavefunction.json.conf").read_text()) == {
            "MOCoefficients": True, "Basisset": True,
        }
        assert not (cwd / "orca.json.conf").exists()
        assert kwargs["timeout"] == 120
        (cwd / "wavefunction.json").write_bytes(_molecule())
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("vibeview.orca_import.shutil.which", lambda _name: "/synthetic/orca_2json")
    monkeypatch.setattr("vibeview.orca_import.subprocess.run", export)
    with QVFReader(convert_to_qvf(source)) as reader:
        assert any(section.kind == "wavefunction.gto" for section in reader.sections)
    assert calls and not calls[0].exists()
    assert source.read_bytes() == b"synthetic GBW"
    assert list(sorted(p.name for p in tmp_path.iterdir())) == ["orca.json.conf", "sample.gbw"]


def test_missing_orca_utility_is_actionable_even_when_installed(tmp_path, monkeypatch):
    source = tmp_path / "sample.gbw"
    source.write_bytes(b"synthetic GBW")
    monkeypatch.setattr("vibeview.orca_import.shutil.which", lambda _name: None)
    with pytest.raises(ValueError, match="ORCA_2JSON"):
        convert_to_qvf(source)


@pytest.mark.parametrize("failure", ["exit", "timeout", "missing"])
def test_gbw_export_failures_clean_temporary_files(tmp_path, monkeypatch, failure):
    source = tmp_path / "sample.gbw"
    source.write_bytes(b"synthetic GBW")
    work = []

    def fail(args, *, cwd, **kwargs):
        work.append(cwd)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 120)
        return subprocess.CompletedProcess(args, 1 if failure == "exit" else 0)

    monkeypatch.setattr("vibeview.orca_import.shutil.which", lambda _name: "/synthetic/orca_2json")
    monkeypatch.setattr("vibeview.orca_import.subprocess.run", fail)
    with pytest.raises(ValueError, match="ORCA|orca_2json"):
        convert_to_qvf(source)
    assert work and not work[0].exists()


def test_mcd_upload_and_cli_persistent_import(tmp_path):
    data = b"10000 -0.2 0.5\n20000 0.1 0.4\n"
    with QVFReader(convert_bytes_to_qvf(data, "upload.mcd")) as reader:
        assert len(reader.read_spectra("mcd").frequencies) == 2
    source = tmp_path / "sample.mcd"
    source.write_bytes(data)
    result = CliRunner().invoke(main, ["import", str(source)])
    assert result.exit_code == 0, result.output
    with QVFReader(source.with_suffix(".qvf")) as reader:
        assert len(reader.read_spectra("mcd").frequencies) == 2


def test_tiny_signed_mcd_transitions_are_visible_and_broadened(monkeypatch):
    from vibeview.renderers.spectra import broadened_envelope

    figures = []
    monkeypatch.setattr(
        "plotly.graph_objects.Figure.to_html",
        lambda self, **kwargs: figures.append(self) or "<div>plot</div>",
    )
    with QVFReader(convert_bytes_to_qvf(b"10000 -4e-14 5e-14\n", "tiny.mcd")) as reader:
        SpectraRenderer(reader.get_section("mcd"), reader).render_to_html(display="sticks")
    assert list(figures[0].data[0].y) == [0, -4e-14, None]
    assert ".4g" in figures[0].data[0].hovertemplate
    np.testing.assert_allclose(
        broadened_envelope(np.array([10000]), np.array([-4e-14]), 5, np.array([10000.])),
        [-4e-14], atol=0,
    )


def test_transition_assignments_reach_hover_and_incompatible_quantities_do_not_overlay(monkeypatch):
    from vibeview.qvf_writer import QVFArchiveBuilder
    from vibeview.renderers.spectra import render_comparison_html

    builder = QVFArchiveBuilder()
    builder.add_json("orca", "spectra.ecd", "spectrum", {
        "frequencies": [2, 3], "intensities": [0, -1e-12], "labels": ["forbidden", "0 → 2"],
    }, section_extra={"spectrum_style": {"y_label": "Rotatory strength (10^-40 cgs)"}})
    builder.add_json("other", "spectra.ecd", "spectrum", {
        "frequencies": [2, 3], "intensities": [1, -1],
    })
    figures = []
    monkeypatch.setattr(
        "plotly.graph_objects.Figure.to_html",
        lambda self, **kwargs: figures.append(self) or "<div>plot</div>",
    )
    with QVFReader(builder.to_bytesio()) as reader:
        SpectraRenderer(reader.get_section("orca"), reader).render_to_html()
        assert list(figures[0].data[-1].text) == ["0 → 2"] * 3
        assert "%{text}" in figures[0].data[-1].hovertemplate
        assert "different units" in render_comparison_html([
            ("ORCA", reader.get_section("orca"), reader),
            ("other", reader.get_section("other"), reader),
        ])


@pytest.mark.parametrize("labels", [["one"], ["one", 2], "not a list"])
def test_invalid_spectrum_assignments_are_reported(labels):
    from vibeview.qvf import QVFError
    from vibeview.qvf_writer import QVFArchiveBuilder

    builder = QVFArchiveBuilder()
    builder.add_json("spectrum", "spectra.ecd", "spectrum", {
        "frequencies": [2, 3], "intensities": [1, -1], "labels": labels,
    })
    with QVFReader(builder.to_bytesio()) as reader, pytest.raises(QVFError, match="labels"):
        reader.read_spectra("spectrum")
