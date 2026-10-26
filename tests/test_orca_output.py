"""Actual ORCA 6.1.1 printed tables versus their independent result files."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from vibeview import QVFReader, validate
from vibeview.converters import convert_bytes_to_qvf, convert_to_qvf, detect_format
from vibeview.orca_import import orca_to_qvf

DATA = Path(__file__).parent / "data" / "orca_output"
EXAMPLES = Path(__file__).parents[1] / "docs" / "examples"


@pytest.mark.parametrize(
    "name,example",
    [
        ("water", "orca-water/frequencies.hess"),
        ("peroxide", "orca-raman-vcd/calculation.hess"),
    ],
)
@pytest.mark.parametrize("route", ["path", "upload", "bytes"])
def test_actual_output_modes_and_spectra_match_hessian(tmp_path, name, example, route):
    content = (DATA / f"{name}.out").read_bytes()
    if route == "path":
        path = tmp_path / "result.out"
        path.write_bytes(content)
        assert detect_format(path) == "orca"
        archive = convert_to_qvf(path)
    elif route == "upload":
        archive = convert_bytes_to_qvf(content, "result.out")
    else:
        archive = orca_to_qvf(content)
    with (
        QVFReader(archive) as actual,
        QVFReader(
            orca_to_qvf((EXAMPLES / example).read_bytes()),
        ) as expected,
    ):
        assert validate(actual)["valid"]
        left, right = [r.read_vibrations("orca_vibrations") for r in (actual, expected)]
        np.testing.assert_allclose(left.frequencies, right.frequencies, atol=0.005001, rtol=0)
        np.testing.assert_allclose(
            left.displacements, right.displacements, atol=0.0000005001, rtol=0
        )
        assert any(s.kind == "trajectory" for s in actual.sections)
        assert actual.provenance["charge"] == 0
        assert actual.provenance["multiplicity"] == 1
        for section in expected.sections:
            if not section.kind.startswith("spectra."):
                continue
            a, b = actual.read_spectra(section.id), expected.read_spectra(section.id)
            indices = [b.labels.index(label) for label in a.labels]
            np.testing.assert_allclose(a.frequencies, b.frequencies[indices], atol=0.005001, rtol=0)
            tol = 0.000050501 if section.kind == "spectra.raman" else 0.005001
            np.testing.assert_allclose(a.intensities, b.intensities[indices], atol=tol, rtol=0)
        if name == "peroxide":
            assert actual.get_section("orca_vcd").kind == "spectra.vcd"


def test_actual_electronic_output_matches_both_property_gauges(tmp_path):
    with (
        QVFReader(orca_to_qvf((DATA / "water-cis.out").read_bytes())) as actual,
        QVFReader(
            orca_to_qvf((DATA / "water-cis.property.json").read_bytes()),
        ) as expected,
    ):
        assert validate(actual)["valid"]
        spectra = [s for s in actual.sections if s.kind.startswith("spectra.")]
        assert len(spectra) == 4
        for section in spectra:
            a, b = actual.read_spectra(section.id), expected.read_spectra(section.id)
            np.testing.assert_allclose(a.frequencies, b.frequencies, atol=0.0000005001, rtol=0)
            np.testing.assert_allclose(a.intensities, b.intensities, atol=0.000005001, rtol=0)
    for name in ("water-cis.out", "water-cis.property.json"):
        shutil.copyfile(DATA / name, tmp_path / name)
    with QVFReader(convert_to_qvf(tmp_path / "water-cis.out")) as combined:
        assert len([s for s in combined.sections if s.kind.startswith("spectra.")]) == 4
        # Higher precision and method metadata from property file win.
        assert combined.read_json_member("orca_g1_uvvis_1", "spectrum")["orca"]["Method"] == 20


def test_companions_are_preferred_without_duplicate_sections(tmp_path):
    shutil.copyfile(DATA / "peroxide.out", tmp_path / "job.out")
    shutil.copyfile(EXAMPLES / "orca-raman-vcd/calculation.hess", tmp_path / "job.hess")
    with QVFReader(convert_to_qvf(tmp_path / "job.out")) as reader:
        ids = [s.id for s in reader.sections]
        assert len(ids) == len(set(ids))
        assert len(reader.read_spectra("orca_ir").frequencies) == 12
        assert reader.read_vibrations("orca_vibrations").frequencies[6] != 328.83


@pytest.mark.parametrize(
    "old,new,error",
    [
        ("328.83 cm**-1", "329.83 cm**-1", "frequency"),
        ("6.766645", "7.766645", "spectra.raman"),
        ("185.51", "195.51", "spectra.ir"),
        ("0.012535", "0.112535", "normal modes"),
        ("-0.725221", "-0.825221", "geometr"),
    ],
)
def test_output_disagreements_are_errors(tmp_path, old, new, error):
    text = (DATA / "peroxide.out").read_text()
    assert old in text
    (tmp_path / "job.out").write_text(text.replace(old, new))
    shutil.copyfile(EXAMPLES / "orca-raman-vcd/calculation.hess", tmp_path / "job.hess")
    with pytest.raises(ValueError, match=error):
        convert_to_qvf(tmp_path / "job.out")


def test_electronic_companion_conflict_is_not_silently_preferred(tmp_path):
    shutil.copyfile(DATA / "water-cis.out", tmp_path / "job.out")
    data = json.loads((DATA / "water-cis.property.json").read_text())
    data["Geometries"][-1]["Absorption_Spectrum"][0]["ExcitationEnergies"][0][3] += 1
    (tmp_path / "job.property.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="spectra.uvvis"):
        convert_to_qvf(tmp_path / "job.out")


@pytest.mark.parametrize(
    "transform",
    [
        lambda s: s.replace("ORCA TERMINATED NORMALLY", "ORCA TERMINATED ABNORMALLY"),
        lambda s: s + s,
        lambda s: s + "\n| 1> Compound\n",
        lambda s: s + "\n| 1> $new_job\n",
        lambda s: s.replace("     0:       0.00 cm**-1", ""),
        lambda s: s.replace("NORMAL MODES", "MISSING MODES"),
        lambda s: s.replace("km/mol", "unknown"),
    ],
)
def test_incomplete_or_multijob_output_is_rejected(transform):
    text = (DATA / "water.out").read_text()
    changed = transform(text)
    assert changed != text
    with pytest.raises(ValueError):
        orca_to_qvf(changed.encode())
