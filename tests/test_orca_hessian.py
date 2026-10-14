"""Native Hessian parsing and calculation/animation contracts, without ORCA."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from vibeview import QVFReader, validate
from vibeview.converters import convert_bytes_to_qvf, convert_to_qvf, detect_format
from vibeview.import_workflow import plan_imports
from vibeview.orca_import import orca_to_qvf
from vibeview.renderers.vibrations import VibrationsRenderer
from vibeview.units import BOHR_TO_ANGSTROM


def _hessian(*, legacy=False, ir=True):
    frequencies = [-80.124, 0, 0, 0, 0, 1000.126]
    matrix = np.arange(36).reshape(6, 6) / 100
    lines = ["$orca_hessian_file", "$atoms", "2", "H 1.008 0 0 0", "He 4.003 0 0 2",
             "$vibrational_frequencies", "6"]
    lines += [f"{i} {f}" for i, f in enumerate(frequencies)]
    lines += ["$normal_modes", "6 6"]
    for start, stop in ((0, 4), (4, 6)):
        lines += [" ".join(map(str, range(start, stop)))]
        lines += [f"{i} " + " ".join(f"{x:.8E}" for x in row[start:stop])
                  for i, row in enumerate(matrix)]
    if ir:
        lines += ["$ir_spectrum", "6"]
        lines += [f"{f:.2f} " + (f"{i / 10} 0 0 0" if legacy
                                else f"{i / 10} {i + 10} 0 0 0")
                  for i, f in enumerate(frequencies)]
    return ("\n".join(lines) + "\n$end\n").encode()


@pytest.mark.parametrize("route", ["path", "upload", "bytes"])
def test_native_hessian_modes_units_and_ir_column(tmp_path, route):
    content = _hessian().replace(b"E+", b"D+")
    if route == "path":
        path = tmp_path / "molecule.HESS"
        path.write_bytes(content)
        assert detect_format(path) == "orca"
        archive = convert_to_qvf(path)
    elif route == "upload":
        archive = convert_bytes_to_qvf(content, "molecule.hess")
    else:
        archive = orca_to_qvf(content)
    with QVFReader(archive) as reader:
        assert validate(reader)["valid"]
        vib = reader.read_vibrations("orca_vibrations")
        assert vib.displacements.shape == (6, 2, 3)
        np.testing.assert_allclose(
            vib.displacements, (np.arange(36) / 100).reshape(6, 6).T.reshape(6, 2, 3),
        )
        assert vib.frequencies.tolist() == [-80.124, 0, 0, 0, 0, 1000.126]
        assert vib.atoms[1].position[2] == 2 * BOHR_TO_ANGSTROM
        spectrum = reader.read_spectra("orca_ir")
        np.testing.assert_array_equal(spectrum.intensities, np.arange(6) + 10)
        np.testing.assert_array_equal(spectrum.frequencies, vib.frequencies)
        assert spectrum.labels[0] == "Mode 0"
        assert reader.get_section("orca_ir").kind == "spectra.ir"


def test_optional_ir_and_legacy_transition_moment_units():
    with QVFReader(orca_to_qvf(_hessian(ir=False))) as reader:
        assert not any(s.kind.startswith("spectra") for s in reader.sections)
        assert len(reader.read_vibrations("orca_vibrations").frequencies) == 6
    with QVFReader(orca_to_qvf(_hessian(legacy=True))) as reader:
        section = reader.get_section("orca_ir")
        assert section.kind == "spectra.generic"
        assert "a.u." in section.model_extra["spectrum_style"]["y_label"]
        np.testing.assert_allclose(reader.read_spectra("orca_ir").intensities, np.arange(6) / 10)


@pytest.mark.parametrize("old,new,message", [
    (b"$atoms", b"$missing_atoms", "Missing.*atoms"),
    (b"$normal_modes", b"$missing_modes", "normal_modes dimensions"),
    (b"H 1.008", b"H -1.008", "masses must be positive"),
    (b"He 4.003 0 0 2", b"He 4.003 0 0 nan", "finite"),
    (b"6 6", b"6 5", "normal_modes dimensions"),
    (b"0 1 2 3", b"0 1 2 2", "duplicate.*column"),
    (b"1 0\n2 0", b"0 0\n2 0", "duplicate.*frequency"),
    (b"-80.12 0.0 10", b"-81.12 0.0 10", "frequencies disagree"),
    (b"$end", b"$atoms\n1\nH 1 0 0 0", "Duplicate.*atoms"),
])
def test_malformed_hessian_reports_actionable_errors(old, new, message):
    with pytest.raises(ValueError, match=message):
        orca_to_qvf(_hessian().replace(old, new))


def test_truncated_normal_modes_are_rejected():
    data = _hessian().split(b"$normal_modes")[0] + b"$normal_modes\n6 6\n0 1\n0 0.1 0.2\n"
    with pytest.raises(ValueError, match="Incomplete"):
        orca_to_qvf(data)


def _properties(tmp_path, *, distorted=False, reversed_atoms=False):
    positions = [[1, 2, 3], [1, 2, 3 + 2 * BOHR_TO_ANGSTROM]]
    if distorted:
        positions[1][0] += 0.01
    elements = ["He", "H"] if reversed_atoms else ["H", "He"]
    path = tmp_path / "job.property.json"
    path.write_text(json.dumps({
        "Calculation_Info": {"Mult": 1}, "Calculation_Status": {},
        "Geometries": [{"Geometry": {"NAtoms": 2, "Coordinates": {
            "Type": "Cartesians", "Units": "Angstrom",
            "Cartesians": [[el, *pos] for el, pos in zip(elements, positions, strict=True)],
        }}}],
    }))
    return path


@pytest.mark.parametrize("entry", ["hess", "property.json", "out"])
def test_dataset_rebases_translation_and_preserves_mode_vectors(tmp_path, entry):
    hessian = tmp_path / "job.hess"
    hessian.write_bytes(_hessian())
    _properties(tmp_path)
    (tmp_path / "job.out").write_text("Synthetic calculation locator")
    with QVFReader(convert_to_qvf(tmp_path / f"job.{entry}")) as reader:
        assert sum(s.kind == "structure" for s in reader.sections) == 1
        assert "job.hess" in reader.provenance["orca_calculation"]["files"]
        vibration = reader.read_vibrations("orca_vibrations")
        np.testing.assert_allclose([a.position for a in vibration.atoms],
                                   [a.position for a in reader.read_structure().atoms])
        renderer = VibrationsRenderer(reader.get_section("orca_vibrations"), reader)
        before = np.array([pos for _, pos in renderer.displace_atoms(5, 1, phase=np.pi / 2)])
        after = np.array([pos for _, pos in renderer.displace_atoms(5, 1, phase=0)])
        np.testing.assert_allclose(before, [a.position for a in vibration.atoms])
        assert np.linalg.norm(after - before) > 0.1
    assert len(plan_imports([tmp_path], output=tmp_path / "imported")) == 1


@pytest.mark.parametrize("kwargs,message", [
    ({"distorted": True}, "geometry does not match"),
    ({"reversed_atoms": True}, "identities/order"),
])
def test_dataset_rejects_incompatible_hessian(tmp_path, kwargs, message):
    path = _properties(tmp_path, **kwargs)
    (tmp_path / "job.hess").write_bytes(_hessian())
    with pytest.raises(ValueError, match=message):
        convert_to_qvf(path)


def test_hessian_multiplicity_conflict_is_not_silently_merged(tmp_path):
    path = _properties(tmp_path)
    (tmp_path / "job.hess").write_bytes(
        _hessian().replace(b"$atoms", b"$multiplicity\n3\n$atoms"),
    )
    with pytest.raises(ValueError, match="multiplicity disagree"):
        convert_to_qvf(path)


def test_hessian_only_directory_import_and_geometry_metadata(tmp_path):
    (tmp_path / "job.hess").write_bytes(_hessian())
    (tmp_path / "job.inp").write_text("Synthetic locator")
    plans = plan_imports([tmp_path], output=tmp_path / "imported")
    assert len(plans) == 1 and plans[0].source.name == "job.hess"


def test_real_orca_611_example_matches_output_table():
    path = Path(__file__).resolve().parents[1] / "docs/examples/orca-water/frequencies.hess"
    with QVFReader(convert_to_qvf(path)) as reader:
        vibration = reader.read_vibrations("orca_vibrations")
        np.testing.assert_allclose(vibration.frequencies[6:],
                                   [1607.9680791, 3689.7495621, 3788.9093136])
        np.testing.assert_allclose(reader.read_spectra("orca_ir").intensities[6:],
                                   [47.49250585, 2.20520296, 19.72152928])
        # Coordinate row 3, mode column 6: first H x displacement, not its mass-weighted value.
        assert vibration.displacements[6, 1, 0] == pytest.approx(0.70536512707)
        assert vibration.atoms[0].position[0] == pytest.approx(-0.102521683142 * BOHR_TO_ANGSTROM)
