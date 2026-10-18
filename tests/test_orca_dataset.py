"""Synthetic integration contracts for opening an ORCA calculation together."""

from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from vibeview.converters import convert_bytes_to_qvf, convert_to_qvf, detect_format
from vibeview.orca_dataset import orca_calculation_to_qvf, orca_dataset_entry, orca_dataset_key
from vibeview.orca_import import orca_to_qvf
from vibeview.qvf import QVFReader
from vibeview.units import BOHR_TO_ANGSTROM


def _molecule(*, energy: float = -0.5) -> dict[str, Any]:
    return {
        "Molecule": {
            "Atoms": [
                {
                    "ElementNumber": number,
                    "Coords": [0.0, 0.0, position],
                    "Basis": [{"Shell": "s", "Exponents": [1.0], "Coefficients": [1.0]}],
                }
                for number, position in ((1, 0.0), (2, 1.1))
            ],
            "Charge": 1,
            "Multiplicity": 1,
            "CoordinateUnits": "Angs",
            "HFTyp": "RHF",
            "MolecularOrbitals": {
                "EnergyUnit": "Eh",
                "MOs": [
                    {
                        "MOCoefficients": [1.0, 0.0],
                        "Occupancy": 2.0,
                        "OrbitalEnergy": energy,
                    },
                    {
                        "MOCoefficients": [0.0, 1.0],
                        "Occupancy": 0.0,
                        "OrbitalEnergy": 0.1,
                    },
                ],
            },
        },
    }


def _properties(*, energy: float = -1.1) -> dict[str, Any]:
    return {
        "Calculation_Info": {"Charge": 1, "Mult": 1, "NumOfElectrons": 2},
        "Calculation_Status": {"version": "6.1.1", "Status": "NORMAL TERMINATION"},
        "Geometries": [{
            "Geometry": {
                "NAtoms": 2,
                "Coordinates": {
                    "Type": "Cartesians",
                    "Units": "Angstrom",
                    "Cartesians": [["H", 0.0, 0.0, 0.0], ["He", 0.0, 0.0, 1.1]],
                },
            },
            "Absorption_Spectrum": [{
                "Method": 20,
                "Representation": "Length",
                "NTrans": 1,
                "ExcitationEnergies": [[5.0, 40327.7, 247.97, 0.2, 0.0]],
            }],
            "Energy": [{"Method": "SCF", "totalEnergy": [[energy]]}],
            "Mulliken_Population_Analysis": [{"AtomicCharges": [[0.4], [0.6]]}],
            "Dipole_Moment": [{
                "Method": "SCF", "State": -1, "dipoleTotal": [[0.0], [0.0], [1.0]],
            }],
        }],
    }


def _property_text(*, energy: float = -2.2) -> str:
    return f'''$Geometry
 &GeometryIndex 1
 &NAtoms [&Type "Integer"] 2
 &CartesianCoordinates [&Type "Coordinates", &Dim(2,4), &Units "Angstrom"]
 H 0.0 0.0 0.0
 He 0.0 0.0 1.1
$End
$Calculation_Info
 &GeometryIndex 1
 &Charge [&Type "Integer"] 1
 &Mult [&Type "Integer"] 1
$End
$SCF_Energy
 &GeometryIndex 1
 &Method [&Type "String"] "SCF"
 &totalEnergy [&Type "ArrayOfDoubles", &Dim(1,1)]
 0
0 {energy}
$End
'''


def _write_json(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _set_time(path: Path, seconds: int) -> None:
    os.utime(path, (seconds, seconds))


def _mcd_intensities(reader: QVFReader) -> list[float]:
    return sorted(
        float(reader.read_spectra(section.id).intensities[0])
        for section in reader.sections
        if section.kind.startswith("spectra.")
        and "L-R" in section.model_extra.get("spectrum_style", {}).get("y_label", "")
    )


@pytest.mark.parametrize("entry", ["property.json", "json", "gbw", "out", "inp"])
def test_open_calculation_from_each_entry_combines_one_coherent_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str,
) -> None:
    property_path = _write_json(tmp_path / "job.property.json", _properties())
    _write_json(tmp_path / "job.json", _molecule())
    (tmp_path / "job.gbw").write_bytes(b"synthetic GBW")
    for suffix in ("out", "inp"):
        (tmp_path / f"job.{suffix}").write_text("Synthetic locator, not executable input.")
    (tmp_path / "job.casscf-el.dip.1000.mcd").write_text("10000 -0.3 0.5\n")
    monkeypatch.setattr("vibeview.orca_import._gbw_json", lambda _source: _molecule())
    with QVFReader(convert_to_qvf(tmp_path / f"job.{entry}")) as reader:
        kinds = [section.kind for section in reader.sections]
        assert kinds.count("structure") == 1
        assert kinds.count("wavefunction.gto") == 1
        assert kinds.count("x_orca.properties") == 1
        assert len({section.id for section in reader.sections}) == len(reader.sections)
        wavefunction = reader.read_wavefunction_gto("wavefunction")
        assert wavefunction.structure_ref == "structure"
        np.testing.assert_array_equal(wavefunction.mo_coefficients, np.eye(2))
        assert [atom.symbol for atom in reader.read_structure().atoms] == ["H", "He"]
        assert reader.provenance["scf_energy"]["value"] == -1.1
        assert reader.provenance["charge"] == 1
        assert reader.provenance["multiplicity"] == 1
        assert reader.provenance["orca"]["Calculation_Status"]["version"] == "6.1.1"
        assert reader.dipole_moment["vector_debye"] == [0.0, 0.0, 2.541746473]
        np.testing.assert_allclose(
            reader.read_atom_properties("orca_mulliken_charge_1").mulliken_charges,
            [0.4, 0.6],
        )
        assert _mcd_intensities(reader) == [-0.3]
        assert sum(section.kind.startswith("spectra.") for section in reader.sections) == 3
    assert property_path.exists()


def test_direct_orca_import_path_assembles_companions(tmp_path: Path) -> None:
    source = _write_json(tmp_path / "job.property.json", _properties())
    _write_json(tmp_path / "job.json", _molecule())
    with QVFReader(orca_to_qvf(source)) as reader:
        assert any(section.kind == "wavefunction.gto" for section in reader.sections)
        assert reader.provenance["scf_energy"]["value"] == -1.1


@pytest.mark.parametrize(
    "entry,text_time,json_time,expected",
    [
        ("out", 100, 200, -1.1),
        ("out", 200, 100, -2.2),
        ("out", 200, 200, -1.1),
        ("property.txt", 100, 200, -2.2),
        ("property.json", 200, 100, -1.1),
    ],
)
def test_property_representation_selection_does_not_duplicate_properties(
    tmp_path: Path, entry: str, text_time: int, json_time: int, expected: float,
) -> None:
    text = tmp_path / "job.property.txt"
    text.write_text(_property_text())
    exported = _write_json(tmp_path / "job.property.json", _properties())
    (tmp_path / "job.out").write_text("Synthetic locator")
    _set_time(text, text_time)
    _set_time(exported, json_time)
    with QVFReader(orca_calculation_to_qvf(tmp_path / f"job.{entry}")) as reader:
        assert reader.provenance["scf_energy"]["value"] == expected
        assert sum(section.kind == "x_orca.properties" for section in reader.sections) == 1


def test_ordinary_json_property_export_participates_in_freshness_selection(tmp_path: Path) -> None:
    text = tmp_path / "job.property.txt"
    text.write_text(_property_text())
    exported = _write_json(tmp_path / "job.json", _properties())
    _set_time(text, 100)
    _set_time(exported, 200)
    entry = orca_dataset_entry(text)
    assert entry == exported
    with QVFReader(orca_calculation_to_qvf(entry)) as reader:
        assert reader.provenance["scf_energy"]["value"] == -1.1


def test_absent_locator_parent_does_not_break_format_detection(tmp_path: Path) -> None:
    source = tmp_path / "missing" / "job.out"
    assert orca_dataset_key(source) is None
    assert detect_format(source) != "orca"


@pytest.mark.parametrize(
    "entry,gbw_time,json_time,expected,calls",
    [
        ("property.json", 100, 200, -0.5, 0),
        ("property.json", 200, 200, -0.5, 0),
        ("property.json", 200, 100, -0.8, 1),
        ("json", 200, 100, -0.5, 0),
        ("gbw", 100, 200, -0.8, 1),
    ],
)
def test_orbital_representation_selection_respects_freshness_and_explicit_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str,
    gbw_time: int, json_time: int, expected: float, calls: int,
) -> None:
    _write_json(tmp_path / "job.property.json", _properties())
    exported = _write_json(tmp_path / "job.json", _molecule())
    gbw = tmp_path / "job.gbw"
    gbw.write_bytes(b"synthetic GBW")
    _set_time(gbw, gbw_time)
    _set_time(exported, json_time)
    exports: list[bytes] = []

    def export(source: bytes) -> dict[str, Any]:
        exports.append(source)
        return _molecule(energy=-0.8)

    monkeypatch.setattr("vibeview.orca_import._gbw_json", export)
    with QVFReader(orca_calculation_to_qvf(tmp_path / f"job.{entry}")) as reader:
        assert reader.read_wavefunction_gto("wavefunction").energies[0] == expected
    assert len(exports) == calls


@pytest.mark.parametrize("stale_export", [False, True])
def test_discovered_gbw_without_converter_fails_instead_of_silently_omitting_orbitals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stale_export: bool,
) -> None:
    source = _write_json(tmp_path / "job.property.json", _properties())
    gbw = tmp_path / "job.gbw"
    gbw.write_bytes(b"synthetic GBW")
    _set_time(gbw, 200)
    if stale_export:
        exported = _write_json(tmp_path / "job.json", _molecule())
        _set_time(exported, 100)
    monkeypatch.setattr("vibeview.orca_import.shutil.which", lambda _name: None)
    with pytest.raises(ValueError, match="ORCA_2JSON"):
        convert_to_qvf(source)


@pytest.mark.parametrize("mismatch", ["order", "coordinates", "charge", "multiplicity"])
def test_incompatible_wavefunction_cannot_share_property_structure(
    tmp_path: Path, mismatch: str,
) -> None:
    source = _write_json(tmp_path / "job.property.json", _properties())
    molecule = _molecule()
    payload = molecule["Molecule"]
    if mismatch == "order":
        payload["Atoms"].reverse()
    elif mismatch == "coordinates":
        payload["Atoms"][1]["Coords"][2] += 0.01
    elif mismatch == "charge":
        payload["Charge"] = 0
    else:
        payload["Multiplicity"] = 3
    _write_json(tmp_path / "job.json", molecule)
    with pytest.raises(ValueError, match="(?i)(match|differ|incompatib|disagree)"):
        convert_to_qvf(source)


def test_compatible_geometry_is_compared_in_angstrom_with_rounding_tolerance(
    tmp_path: Path,
) -> None:
    source = _write_json(tmp_path / "job.property.json", _properties())
    molecule = _molecule()
    molecule["Molecule"]["CoordinateUnits"] = "Bohr"
    molecule["Molecule"]["Atoms"][1]["Coords"][2] = (1.1 + 5e-6) / BOHR_TO_ANGSTROM
    _write_json(tmp_path / "job.json", molecule)
    with QVFReader(convert_to_qvf(source)) as reader:
        assert reader.read_wavefunction_gto("wavefunction").structure_ref == "structure"
        assert len(reader.read_structure().atoms) == 2


@pytest.mark.parametrize("orbital_position", [1.1, 1.4])
def test_property_trajectory_requires_orbitals_to_match_its_final_geometry(
    tmp_path: Path, orbital_position: float,
) -> None:
    properties = _properties()
    earlier = copy.deepcopy(properties["Geometries"][0])
    earlier["Geometry"]["Coordinates"]["Cartesians"][1][3] = 1.4
    properties["Geometries"].insert(0, earlier)
    source = _write_json(tmp_path / "job.property.json", properties)
    molecule = _molecule()
    molecule["Molecule"]["Atoms"][1]["Coords"][2] = orbital_position
    _write_json(tmp_path / "job.json", molecule)
    if orbital_position != 1.1:
        with pytest.raises(ValueError, match="(?i)(match|differ|incompatib|disagree)"):
            convert_to_qvf(source)
        return
    with QVFReader(convert_to_qvf(source)) as reader:
        trajectory = reader.read_trajectory("orca_geometries")
        np.testing.assert_array_equal(trajectory.coords[:, 1, 2], [1.4, 1.1])
        np.testing.assert_array_equal(reader.read_structure().atoms[1].position, [0.0, 0.0, 1.1])
        assert reader.read_wavefunction_gto("wavefunction").structure_ref == "structure"


def test_geometry_tolerance_is_absolute_even_for_translated_calculations(tmp_path: Path) -> None:
    properties = _properties()
    properties["Geometries"][0]["Geometry"]["Coordinates"]["Cartesians"][0][1] = 1e6
    source = _write_json(tmp_path / "job.property.json", properties)
    molecule = _molecule()
    molecule["Molecule"]["Atoms"][0]["Coords"][0] = 1e6 + 0.001
    _write_json(tmp_path / "job.json", molecule)
    with pytest.raises(ValueError, match="(?i)(match|differ|incompatib|disagree)"):
        convert_to_qvf(source)


def test_mcd_aggregate_prevents_transition_double_counting_but_keeps_gauges(tmp_path: Path) -> None:
    source = _write_json(tmp_path / "job.property.json", _properties())
    for suffix, value in (
        ("casscf-el.dip.1000", -0.3),
        ("casscf-el.dip.1000.0_1", -0.1),
        ("casscf-el.dip.1000.0_2", -0.2),
        ("casscf-el.vel.1000", -0.4),
        ("mrci-el.dip.1000.0_1", -0.5),
    ):
        (tmp_path / f"job.{suffix}.mcd").write_text(f"10000 {value} 0.5\n")
    with QVFReader(convert_to_qvf(source)) as reader:
        assert _mcd_intensities(reader) == [-0.5, -0.4, -0.3]
        assert len({section.id for section in reader.sections}) == len(reader.sections)
    transition = tmp_path / "job.casscf-el.dip.1000.0_1.mcd"
    with QVFReader(convert_to_qvf(transition)) as reader:
        assert _mcd_intensities(reader) == [-0.5, -0.4, -0.3, -0.1]
        assert any(section.kind == "structure" for section in reader.sections)
        selected = reader.provenance["orca_calculation"]["files"]
        assert "job.casscf-el.dip.1000.mcd" in selected
        assert transition.name in selected
        labels = [str(section.label) for section in reader.sections]
        for name in ("job.casscf-el.dip.1000.mcd", transition.name):
            assert any(name in label and "raw MCD" in label for label in labels)
        assert "job.casscf-el.dip.1000.0_2.mcd" not in selected


def test_exact_basename_discovery_excludes_other_jobs_and_tddft_guess(tmp_path: Path) -> None:
    source = _write_json(tmp_path / "job.property.json", _properties())
    _write_json(tmp_path / "job.json", _molecule())
    for name in ("job2.property.json", "job.extra.property.json", "job.TDDFTGuess.json"):
        _write_json(tmp_path / name, _properties(energy=-9.9))
    (tmp_path / "job.TDDFTGuess.gbw").write_bytes(b"not primary wavefunction")
    for name in ("job2.casscf-el.dip.1000.mcd", "job.extra.casscf-el.dip.1000.mcd"):
        (tmp_path / name).write_text("10000 -9 9\n")
    with QVFReader(convert_to_qvf(source)) as reader:
        assert _mcd_intensities(reader) == []
        assert reader.provenance["scf_energy"]["value"] == -1.1
        assert sum(section.kind == "x_orca.properties" for section in reader.sections) == 1


def test_calculation_keys_and_locators_require_recognized_exact_siblings(tmp_path: Path) -> None:
    source = _write_json(tmp_path / "my.job.property.json", _properties())
    exported = _write_json(tmp_path / "my.job.json", _molecule())
    spectrum = tmp_path / "my.job.casscf-el.dip.1000.mcd"
    spectrum.write_text("10000 -0.3 0.5\n")
    locator = tmp_path / "my.job.out"
    locator.write_text("Synthetic locator")
    unrelated = tmp_path / "my.out"
    unrelated.write_text("Unrelated calculation")
    generic = _write_json(tmp_path / "settings.json", {"theme": "dark"})
    for path in (source, exported, spectrum, locator):
        assert orca_dataset_key(path) == (tmp_path / "my.job").resolve()
        assert detect_format(path) == "orca"
    assert orca_dataset_key(unrelated) is None
    assert orca_dataset_key(generic) is None
    assert detect_format(unrelated) != "orca"


def test_byte_and_stream_imports_do_not_discover_local_companions(tmp_path: Path) -> None:
    properties = _properties()
    source = _write_json(tmp_path / "job.property.json", properties)
    _write_json(tmp_path / "job.json", _molecule())
    data = json.dumps(properties).encode()
    archives = (
        convert_bytes_to_qvf(data, str(source)),
        orca_to_qvf(data),
        orca_to_qvf(io.BytesIO(data)),
    )
    for archive in archives:
        with QVFReader(archive) as reader:
            assert all(section.kind != "wavefunction.gto" for section in reader.sections)
            assert reader.provenance["scf_energy"]["value"] == -1.1
