"""Synthetic contract checks for ORCA's typed property text grammar."""

from __future__ import annotations

import pytest

from vibeview.orca_text import read_property_text


def _section(name: str, components: str, geometry: int = 1) -> str:
    return f"${name}\n   &GeometryIndex {geometry}\n{components}\n$End\n"


def _geometry(index: int = 1, units: str = "Bohr") -> str:
    return _section(
        "Geometry",
        f'''   &NAtoms [&Type "Integer"] 2
   &NCorelessECP [&Type "Integer"] 0
   &NGhostAtoms [&Type "Integer"] 0
   &CartesianCoordinates [&Type "Coordinates", &Dim(2,4), &Units "{units}"]
     H 0.0 0.0 0.0
     H 0.0 0.0 {index * 2}.0''',
        index,
    )


def test_geometry_and_calculation_metadata_match_json_layout() -> None:
    text = "*** ORCA 6.1 ***\n" + _section(
        "Calculation_Status",
        '''   &version [&Type "String"] "6.1.1"
   &Status [&Type "String"] "NORMAL TERMINATION" "A comment"''',
    )
    text += _geometry()
    text += _section(
        "Calculation_Info",
        '''   &Charge [&Type "Integer"] 0
   &Mult [&Type "Integer"] 1''',
    )
    assert read_property_text(text) == {
        "Calculation_Status": {"version": "6.1.1", "Status": "NORMAL TERMINATION"},
        "Calculation_Info": {"Charge": 0, "Mult": 1},
        "Geometries": [
            {
                "Geometry": {
                    "NAtoms": 2,
                    "NCorelessECP": 0,
                    "NGhostAtoms": 0,
                    "Coordinates": {
                        "Type": "Cartesians",
                        "Units": "a.u.",
                        "Cartesians": [["H", 0.0, 0.0, 0.0], ["H", 0.0, 0.0, 2.0]],
                    },
                }
            }
        ],
    }


def test_spectrum_column_blocks_and_variants_are_preserved() -> None:
    components = '''   &Method [&Type "Integer"] 20
   &Representation [&Type "String"] "Length" "Possible values: Length, Velocity"
   &RelCorrection [&Type "Integer"] 1
   &DoHigherMoments [&Type "Boolean"] false
   &NTrans [&Type "Integer"] 2
   &ExcitationEnergies [&Type "ArrayOfDoubles", &Dim (2,11)] "Transitions"
                    0 1 2 3 4 5 6 7

0 1.0 2.0 3.0 4.0 5.0 6.0 7.0 8.0
1 11.0 12.0 13.0 14.0 15.0 16.0 17.0 18.0
                    8 9 10

0 9.0 10.0 11.0
1 19.0 20.0 21.0
   &States [&Type "ArrayOfIntegers", &Dim (2,4)]
                    0 1 2 3

0 0 0 1 0
1 0 0 2 0'''
    text = _geometry() + _section("CIS_Absorption_Spectrum", components)
    text += _section("CIS_Absorption_Spectrum", components.replace('"Length"', '"Velocity"'))
    text += _section("MRCI_ECD_Spectrum", components.replace("1.0 2.0", "-1.0 2.0"))
    geometry = read_property_text(text)["Geometries"][0]
    spectra = geometry["Absorption_Spectrum"]
    assert len(spectra) == 2
    assert [spectrum["Representation"] for spectrum in spectra] == ["Length", "Velocity"]
    assert spectra[0]["ExcitationEnergies"] == [list(range(1, 12)), list(range(11, 22))]
    assert spectra[0]["States"] == [[0, 0, 1, 0], [0, 0, 2, 0]]
    assert spectra[0]["DoHigherMoments"] is False
    assert geometry["ECD_Spectrum"][0]["ExcitationEnergies"][0][0] == -1.0


def test_energy_population_and_dipole_grouping_preserves_method_and_units() -> None:
    text = _geometry(units="Angstrom")
    for name in ("SCF_Energy", "CIS_Energies", "CAS_SCF_Energies"):
        text += _section(
            name,
            '''   &totalEnergy [&Type "ArrayOfDoubles", &Dim (1,1)]
                       0

0 -1.25D+00
   &Method [&Type "String"] "SCF"''',
        )
    for method in ("SCF", "MRCI"):
        text += _section(
            method + "_Dipole_Moment",
            f'''   &dipoleMagnitude [&Type "Double", &Units "a.u."] 1.5
   &Method [&Type "String"] "{method}"
   &dipoleTotal [&Type "ArrayOfDoubles", &Dim (3,1)]
                       0

0 0.0
1 0.0
2 1.5''',
        )
    for population in ("Mulliken", "Loewdin", "Mayer"):
        text += _section(
            "SCF_" + population + "_Population_Analysis",
            '''   &AtomicCharges [&Type "ArrayOfDoubles", &Dim (2,1)]
                       0

0 -0.2
1 0.2''',
        )
    text += _section(
        "Single_Point_Data",
        '''   &Converged [&Type "Boolean"] true
   &FinalEnergy [&Type "Double"] -1.25''',
    )
    geometry = read_property_text(text)["Geometries"][0]
    assert geometry["Geometry"]["Coordinates"]["Units"] == "Angstrom"
    assert len(geometry["Energy"]) == 3
    assert geometry["Energy"][0]["totalEnergy"] == [[-1.25]]
    assert [entry["Method"] for entry in geometry["Dipole_Moment"]] == ["SCF", "MRCI"]
    assert geometry["Dipole_Moment"][1]["dipoleTotal"] == [[0.0], [0.0], [1.5]]
    for population in ("Mulliken", "Loewdin", "Mayer"):
        assert geometry[population + "_Population_Analysis"][0]["AtomicCharges"] == [[-0.2], [0.2]]
    assert geometry["Single_Point_Data"] == {"Converged": True, "FinalEnergy": -1.25}


def test_multiple_geometries_are_sorted_and_properties_stay_attached() -> None:
    text = _geometry(index=2) + _geometry(index=1)
    text += _section("SCF_Energy", '&totalEnergy [&Type "Double"] -2.0', geometry=2)
    text += _section("SCF_Energy", '&totalEnergy [&Type "Double"] -1.0', geometry=1)
    geometries = read_property_text(text)["Geometries"]
    assert [entry["Energy"][0]["totalEnergy"] for entry in geometries] == [-1.0, -2.0]
    assert [entry["Geometry"]["Coordinates"]["Cartesians"][1][3] for entry in geometries] == [2, 4]


@pytest.mark.parametrize(
    ("shape", "data", "message"),
    [
        ("2,1", "0\n0 1.0", "missing rows"),
        ("2,2", "0\n0 1.0\n1 2.0", "missing columns"),
        ("2,1", "0\n1 1.0\n0 2.0", "row indices"),
        ("1,1", "1\n0 1.0", "column indices"),
        ("1,1", "0\n0 1.0 2.0", "width"),
        ("1,1", "0\n0 nan", "finite"),
        ("1,1", "0\n0 1.0\n1 2.0", "trailing"),
        ("1000000000,1", "0\n0 1.0", "fewer rows"),
        ("1000000000,0", "", "at least one column"),
    ],
)
def test_malformed_matrices_are_rejected(shape: str, data: str, message: str) -> None:
    text = _geometry() + _section(
        "SCF_Energy", f'&totalEnergy [&Type "ArrayOfDoubles", &Dim ({shape})]\n{data}'
    )
    with pytest.raises(ValueError, match=message):
        read_property_text(text)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("ordinary text", "No geometries"),
        ("$Geometry\n&GeometryIndex 1", r"missing \$End"),
        ("$Geometry\n$SCF_Energy\n$End", r"missing \$End"),
        ("$End", r"unmatched \$End"),
        ("$Geometry\n&NAtoms [&Type \"Integer\"] 2\n$End", "GeometryIndex"),
        (_section("Geometry", "&NAtoms 2"), "missing its Type"),
        (_section("SCF_Energy", '&Converged [&Type "Boolean"] maybe'), "Boolean"),
        (_geometry().replace('Units "Bohr"', 'Unit "Bohr"'), "Units"),
        (_geometry().replace("Dim(2,4)", "Dim(3,4)"), "dimensions"),
        (_geometry().replace("GeometryIndex 1", "GeometryIndex 0"), "positive"),
    ],
)
def test_invalid_property_text_is_rejected(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        read_property_text(text)


def test_empty_arrays_and_boolean_arrays() -> None:
    text = _geometry() + _section(
        "Other_Property",
        '''   &Empty [&Type "ArrayOfDoubles", &Dim (0,1)]
   &Flags [&Type "ArrayOfBooleans", &Dim (2,1)]
                     0

0 true
1 false''',
    )
    assert read_property_text(text)["Geometries"][0]["Other_Property"] == {
        "Empty": [],
        "Flags": [[True], [False]],
    }
