"""Synthetic ORCA property contracts; no third-party calculation fixtures."""

from __future__ import annotations

import copy
import json
from importlib.resources import files
from typing import Any

import numpy as np
import pytest
from jsonschema import Draft202012Validator

from vibeview.orca_properties import add_properties
from vibeview.qvf import QVFReader
from vibeview.qvf_writer import QVFArchiveBuilder
from vibeview.units import BOHR_TO_ANGSTROM


def _geometry() -> dict[str, Any]:
    return {
        "Geometry": {
            "NAtoms": 2,
            "Coordinates": {
                "Type": "Cartesians",
                "Units": "a.u.",
                "Cartesians": [["H", 0.0, 0.0, 0.0], ["H", 0.0, 0.0, 1.4]],
            },
        }
    }


def _spectrum(*, strength: float = 0.2, gauge: str = "Length") -> dict[str, Any]:
    return {
        "Density_name": "Tdens-CIS",
        "Method": 20,
        "Representation": gauge,
        "NTrans": 1,
        "ExcitationEnergies": [[5.0, 40327.7, 247.97, strength, 0.0]],
        "States": [[0, 0, 1, -1]],
        "Multiplicities": [[1.0, 2.8]],
        "RelCorrection": 2,
    }


def _builder(*geometries: dict[str, Any]) -> QVFArchiveBuilder:
    builder = QVFArchiveBuilder()
    add_properties(
        builder,
        {
            "Calculation_Info": {"Charge": 0, "Mult": 1, "NumOfElectrons": 2},
            "Geometries": list(geometries),
        },
    )
    return builder


def _json_member(builder: QVFArchiveBuilder, section_id: str, member: str) -> Any:
    return json.loads(dict(builder.members())[f"sections/{section_id}/{member}.json"])


def test_structure_and_each_spectrum_variant_round_trip() -> None:
    geometry = _geometry()
    geometry["Absorption_Spectrum"] = [_spectrum(), _spectrum(gauge="Velocity"), _spectrum()]
    geometry["ECD_Spectrum"] = [_spectrum(strength=-3.0)]
    builder = _builder(geometry)
    with QVFReader(builder.to_bytesio()) as reader:
        np.testing.assert_allclose(
            reader.read_structure().atoms[1].position, [0, 0, 1.4 * BOHR_TO_ANGSTROM]
        )
        spectra = [s for s in reader.manifest.sections if s.kind.startswith("spectra.")]
        assert len(spectra) == 4
        assert len({s.id for s in spectra}) == 4
        assert "Velocity" in (spectra[1].label or "")
        assert reader.read_spectra(spectra[-1].id).intensities.tolist() == [-3.0]
        assert reader.read_spectra(spectra[-1].id).frequencies.tolist() == [5.0]
    payload = _json_member(builder, "orca_g1_ecd_1", "spectrum")
    assert payload["units"] == {"frequency": "eV", "intensity": "10^-40 cgs"}
    assert "irrep -1" in payload["labels"][0]
    assert "multiplicity 1 → 2.8" in payload["labels"][0]
    assert payload["orca"] == geometry["ECD_Spectrum"][0]
    assert (
        builder.manifest["sections"][-1]["spectrum_style"]["y_label"]
        == "Rotatory strength (10^-40 cgs)"
    )


def test_atom_analyses_preserve_state_variants_and_mayer_indices() -> None:
    geometry = _geometry()
    geometry["Mulliken_Population_Analysis"] = [
        {
            "Method": "SCF",
            "State": -1,
            "AtomicCharges": [[0.1], [-0.1]],
            "SpinPopulations": [[0.0], [0.0]],
        },
        {"Method": "CIS", "State": 2, "AtomicCharges": [[0.2], [-0.2]]},
    ]
    geometry["Loewdin_Population_Analysis"] = [{"AtomicCharges": [[0.05], [-0.05]]}]
    geometry["Mayer_Population_Analysis"] = [{"BondOrders": [[0.8]], "components": [[0, 1, 1, 1]]}]
    with QVFReader(_builder(geometry).to_bytesio()) as reader:
        ground = reader.read_atom_properties("orca_mulliken_charge_1")
        assert ground.mulliken_charges is not None
        assert ground.mulliken_charges.tolist() == [0.1, -0.1]
        assert ground.spin_populations is not None
        assert ground.spin_populations.tolist() == [0.0, 0.0]
        excited = reader.read_atom_properties("orca_mulliken_charge_2")
        assert excited.mulliken_charges is not None
        assert excited.mulliken_charges.tolist() == [0.2, -0.2]
        assert "State 2" in (reader.get_section("orca_mulliken_charge_2").label or "")
        assert reader.read_bond_orders("orca_mayer_1").pairs == [{"i": 0, "j": 1, "order": 0.8}]
        payload = reader.read_json_member("orca_mayer_1", "bond_orders")
        schema = json.loads(files("vibeview").joinpath("schema.json").read_text())
        Draft202012Validator(schema["$defs"]["BondOrdersPayload"]).validate(payload)
        assert payload["x_orca"] == geometry["Mayer_Population_Analysis"][0]


def test_ground_scf_properties_are_selected_by_method_and_state() -> None:
    geometry = _geometry()
    geometry["Energy"] = [
        {"Method": "CIS", "totalEnergy": [[-0.8], [-0.7]]},
        {"Method": "SCF", "totalEnergy": [[-1.1]]},
    ]
    geometry["Dipole_Moment"] = [
        {"Method": "CIS", "State": 1, "dipoleTotal": [[9.0], [8.0], [7.0]]},
        {"Method": "SCF", "State": -1, "dipoleTotal": [[0.0], [0.0], [1.0]]},
    ]
    geometry["Single_Point_Data"] = {"Converged": True, "FinalEnergy": -1.2}
    builder = _builder(geometry)
    with QVFReader(builder.to_bytesio()) as reader:
        assert reader.provenance["scf_energy"] == {"value": -1.1, "units": "hartree"}
        assert reader.provenance["scf_converged"] is True
        assert reader.dipole_moment["vector_debye"] == [0.0, 0.0, 2.541746473]
        assert reader.dipole_moment["total_debye"] == 2.541746473
    retained = _json_member(builder, "orca_properties_1", "properties")
    assert retained["properties"]["Energy"] == geometry["Energy"]
    assert retained["properties"]["Dipole_Moment"] == geometry["Dipole_Moment"]


def test_post_scf_energy_is_not_invented_as_scf_energy() -> None:
    geometry = _geometry()
    geometry["Energy"] = [{"Method": "CASSCF", "totalEnergy": [[-1.2], [-1.0]]}]
    geometry["Dipole_Moment"] = [
        {"Method": "CASSCF", "State": 0, "dipoleTotal": [[1.0], [2.0], [3.0]]}
    ]
    with QVFReader(_builder(geometry).to_bytesio()) as reader:
        assert "scf_energy" not in reader.provenance
        assert reader.dipole_moment == {}


def test_final_geometry_is_default_and_earlier_geometries_form_trajectory() -> None:
    first, last = _geometry(), _geometry()
    last["Geometry"]["Coordinates"]["Cartesians"][1][-1] = 1.6
    first["Mulliken_Population_Analysis"] = [{"AtomicCharges": [[0.3], [-0.3]]}]
    last["Mulliken_Population_Analysis"] = [{"AtomicCharges": [[0.1], [-0.1]]}]
    with QVFReader(_builder(first, last).to_bytesio()) as reader:
        assert reader.read_structure().atoms[1].position[-1] == pytest.approx(
            1.6 * BOHR_TO_ANGSTROM
        )
        trajectory = reader.read_trajectory("orca_geometries")
        np.testing.assert_allclose(
            trajectory.coords[:, 1, 2], np.array([1.4, 1.6]) * BOHR_TO_ANGSTROM
        )
        charges = reader.read_atom_properties("orca_mulliken_charge_1").mulliken_charges
        assert charges is not None
        assert charges.tolist() == [0.1, -0.1]


def test_different_atom_identities_remain_separate_structures() -> None:
    first, last = _geometry(), _geometry()
    first["Geometry"]["Coordinates"]["Cartesians"][0][0] = "He"
    with QVFReader(_builder(first, last).to_bytesio()) as reader:
        assert reader.read_structure("orca_structure_1").atoms[0].symbol == "He"
        assert reader.read_structure().atoms[0].symbol == "H"
        assert all(section.kind != "trajectory" for section in reader.manifest.sections)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("ExcitationEnergies", [[5.0, 40000.0, 250.0]], "energy, wavenumber"),
        ("ExcitationEnergies", [[True, 40000.0, 250.0, 0.2]], "finite number"),
        ("ExcitationEnergies", [[float("inf"), 40000.0, 250.0, 0.2]], "finite number"),
        ("NTrans", 2, "NTrans"),
        ("NTrans", 1.5, "integer"),
        ("States", [], "one row per transition"),
        ("States", [[0, 0, 0.5, 0]], "integers"),
        ("Multiplicities", [[1.0]], "2 values"),
    ],
)
def test_malformed_spectrum_is_rejected(field: str, value: Any, match: str) -> None:
    geometry = _geometry()
    spectrum = _spectrum()
    spectrum[field] = value
    geometry["Absorption_Spectrum"] = [spectrum]
    with pytest.raises(ValueError, match=match):
        _builder(geometry)


@pytest.mark.parametrize("unit", ["", "furlong"])
def test_unknown_coordinate_units_are_not_guessed(unit: str) -> None:
    geometry = _geometry()
    geometry["Geometry"]["Coordinates"]["Units"] = unit
    with pytest.raises(ValueError, match="coordinate units"):
        _builder(geometry)


@pytest.mark.parametrize("indices", [[0, 1, 2, 1], [True, 1, 1, 1], [0.5, 1, 1, 1]])
def test_malformed_bond_indices_are_not_coerced(indices: list[Any]) -> None:
    geometry = _geometry()
    geometry["Mayer_Population_Analysis"] = [{"BondOrders": [[1.0]], "components": [indices]}]
    with pytest.raises(ValueError, match="ORCA Mayer"):
        _builder(geometry)


def test_population_length_must_match_structure_and_input_is_unchanged() -> None:
    geometry = _geometry()
    geometry["Mulliken_Population_Analysis"] = [{"AtomicCharges": [[0.3]]}]
    original = copy.deepcopy(geometry)
    with pytest.raises(ValueError, match="2 values"):
        _builder(geometry)
    assert geometry == original
