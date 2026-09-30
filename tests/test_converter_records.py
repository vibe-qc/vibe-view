"""Native molecule records must not silently drop atoms or remap bonds."""

import numpy as np
import pytest
from click.testing import CliRunner

from vibeview.cli import main
from vibeview.converters import convert_to_qvf
from vibeview.qvf import QVFReader


def sdf_atom(symbol, x):
    return f"{x:10.4f}{0:10.4f}{0:10.4f} {symbol:<3} 0  0  0  0"


def sdf(atoms=None, bonds=None, counts=None):
    atoms = atoms if atoms is not None else [sdf_atom("C", 0), sdf_atom("O", 2), sdf_atom("H", 5)]
    bonds = bonds if bonds is not None else ["  1  2  2", "  2  3  1"]
    counts = counts if counts is not None else f"{len(atoms):3d}{len(bonds):3d}  0  0  0  0 V2000"
    return "\n".join(["record", "program", "", counts, *atoms, *bonds, "M  END", "$$$$"]) + "\n"


def mol2(atoms=None, bonds=None, counts=None):
    atoms = atoms if atoms is not None else ["10 C 0 0 0 C.3", "30 O 2 0 0 O.2", "20 H 5 0 0 H"]
    bonds = bonds if bonds is not None else ["5 10 30 2", "9 30 20 1"]
    counts = counts if counts is not None else f"{len(atoms)} {len(bonds)}"
    return "\n".join(["@<TRIPOS>MOLECULE", "record", counts, "SMALL", "NO_CHARGES",
                      "@<TRIPOS>ATOM", *atoms, "@<TRIPOS>BOND", *bonds]) + "\n"


def convert(tmp_path, suffix, text):
    source = tmp_path / f"molecule.{suffix}"
    source.write_text(text)
    return convert_to_qvf(source)


@pytest.mark.parametrize("ids", [(10, 30, 20), (3, 1, 2), (0, 8, -2)])
def test_mol2_bonds_reference_file_ids(tmp_path, ids):
    i, j, k = ids
    text = mol2([f"{i} C 0 0 0 C.3", f"{j} O 2 0 0 O.2", f"{k} H 5 0 0 H"],
                [f"0 {i} {j} 2", f"8 {j} {k} 1"])
    with QVFReader(convert(tmp_path, "mol2", text)) as reader:
        structure = reader.read_structure()
        assert [a.symbol for a in structure.atoms] == ["C", "O", "H"]
        np.testing.assert_array_equal(
            [a.position for a in structure.atoms], [[0, 0, 0], [2, 0, 0], [5, 0, 0]],
        )
        assert structure.bonds == [(0, 1, 2.0, (0, 0, 0)), (1, 2, 1.0, (0, 0, 0))]


@pytest.mark.parametrize("suffix, factory", [("mol2", mol2), ("sdf", sdf)])
@pytest.mark.parametrize("later", ["valid", "invalid"])
def test_only_first_molecule_is_imported(tmp_path, suffix, factory, later):
    second = factory() if later == "valid" else factory(counts="invalid")
    with QVFReader(convert(tmp_path, suffix, factory() + second)) as reader:
        structure = reader.read_structure()
        assert [a.symbol for a in structure.atoms] == ["C", "O", "H"]
        assert structure.bonds == [(0, 1, 2.0, (0, 0, 0)), (1, 2, 1.0, (0, 0, 0))]


@pytest.mark.parametrize("counts", ["3", "3 2"])
def test_mol2_comments_blanks_and_optional_bond_count(tmp_path, counts):
    text = mol2(counts=counts).replace("record\n", "# comment\n\nrecord\n")
    text = text.replace("30 O", "# atom comment\n\n30 O").replace("9 30", "\n# bond comment\n9 30")
    with QVFReader(convert(tmp_path, "mol2", text)) as reader:
        assert len(reader.read_structure().atoms) == 3
        assert len(reader.read_structure().bonds) == 2


@pytest.mark.parametrize("suffix", ["sdf", "mol2"])
def test_unknown_elements_and_existing_order_mappings(tmp_path, suffix):
    if suffix == "sdf":
        text = sdf([sdf_atom("Qq", 0), sdf_atom("C", 2)],
                   [f"  1  2{o:3d}" for o in [1, 2, 3, 4, 8]])
        expected = [1.0, 2.0, 3.0, 1.5, 1.0]
    else:
        text = mol2(["0 Qq1 0 0 0 Qq", "8 C 2 0 0 C.3"],
                    [f"{i} 0 8 {o}" for i, o in enumerate(["1", "2", "3", "ar", "am", "du"])])
        expected = [1.0, 2.0, 3.0, 1.5, 1.5, 1.0]
    with QVFReader(convert(tmp_path, suffix, text)) as reader:
        structure = reader.read_structure()
        assert structure.atoms[0].symbol == "Qq"
        assert structure.atoms[0].atomic_number == 0
        assert [bond[2] for bond in structure.bonds] == expected


BAD_MOL2 = [
    mol2().replace("30 O 2 0 0 O.2", "30 Qq1 2 0 0 ."),
    mol2().replace("30 O 2 0 0 O.2", "30 O bad 0 0 O.2"),
    mol2().replace("30 O 2 0 0 O.2", "malformed atom"),
    mol2().replace("30 O 2", "10 O 2"),
    mol2().replace("10 C 0", "bad C 0"),
    mol2().replace("30 O 2", "30 O nan"),
    mol2().replace("30 O 2", "30 O inf"),
    mol2().replace("5 10 30 2", "5 10 99 2"),
    mol2().replace("5 10 30 2", "5 10 10 2"),
    mol2().replace("5 10 30 2", "bad bond"),
    mol2().replace("5 10 30 2", "5 bad 30 2"),
    mol2().replace("5 10 30 2", "bad 10 30 2"),
    mol2(counts="4 2"), mol2(counts="2 2"), mol2(counts="3 3"), mol2(counts="3 1"),
    mol2(counts="-3 2"), mol2(counts="3 -2"), mol2(counts="bad 2"), mol2(counts="3 bad"),
    "@<TRIPOS>MOLECULE\nrecord\n@<TRIPOS>ATOM\n1 C 0 0 0 C.3\n",
]
BAD_SDF = [
    sdf(counts="  3  1"),
    sdf(bonds=[], counts="  2  0"),
    sdf().replace(sdf_atom("O", 2), "malformed atom"),
    sdf().replace(sdf_atom("O", 2), sdf_atom("O", float("nan"))),
    sdf().replace(sdf_atom("O", 2), sdf_atom("O", float("inf"))),
    sdf().replace(sdf_atom("O", 2), sdf_atom("", 2)),
    sdf().replace("  1  2  2", "  1  4  2"),
    sdf().replace("  1  2  2", "  0  2  2"),
    sdf().replace("  1  2  2", "  1  1  2"),
    sdf().replace("  1  2  2", "bad bond"),
    sdf().replace("  1  2  2", "  1bad  2"),
    sdf().replace("  1  2  2", "  1  2bad"),
    sdf(counts="  4  2"), sdf(counts="  2  2"), sdf(counts="  3  3"),
    sdf(counts=" -3  2"), sdf(counts="  3 -2"), sdf(counts="bad  2"), sdf(counts="  3bad"),
    sdf(counts="  0  0  0 V3000"),
    "record\nprogram\n\n  3  2\n" + sdf_atom("C", 0) + "\n$$$$\n",
]
BAD_INPUTS = [("mol2", text) for text in BAD_MOL2] + [("sdf", text) for text in BAD_SDF]


@pytest.mark.parametrize("suffix,text", BAD_INPUTS)
def test_malformed_record_fails_before_serialization(tmp_path, suffix, text):
    # A following valid molecule must never hide a malformed first record.
    with pytest.raises(ValueError, match="Mol2|SDF"):
        convert(tmp_path, suffix, text + (mol2() if suffix == "mol2" else sdf()))


@pytest.mark.parametrize("suffix,text", [
    ("mol2", BAD_MOL2[0]), ("mol2", BAD_MOL2[1]),
    ("sdf", BAD_SDF[0]), ("sdf", BAD_SDF[2]),
])
@pytest.mark.parametrize("existing", [False, True])
def test_cli_errors_do_not_publish_even_with_force(tmp_path, suffix, text, existing):
    source = tmp_path / f"molecule.{suffix}"
    source.write_text(text)
    destination = tmp_path / "output.qvf"
    if existing:
        destination.write_bytes(b"existing destination must survive")
    result = CliRunner().invoke(main, ["import", str(source), "-o", str(destination), "--force"])
    assert result.exit_code != 0, result.output
    assert "Mol2" in result.output or "SDF" in result.output
    if existing:
        assert destination.read_bytes() == b"existing destination must survive"
    else:
        assert not destination.exists()


@pytest.mark.parametrize("suffix,factory", [("sdf", sdf), ("mol2", mol2)])
def test_cli_import_preserves_connectivity(tmp_path, suffix, factory):
    source = tmp_path / f"molecule.{suffix}"
    source.write_text(factory())
    destination = tmp_path / "output.qvf"
    result = CliRunner().invoke(main, ["import", str(source), "-o", str(destination)])
    assert result.exit_code == 0, result.output
    with QVFReader(destination) as reader:
        assert reader.read_structure().bonds == [(0, 1, 2.0, (0, 0, 0)), (1, 2, 1.0, (0, 0, 0))]


@pytest.mark.parametrize("properties", [
    "M  CHG  1   1  -1", "A    1\n  1  2  1", "V    1\n  1  2  1",
    "G    1  2\n  1  2  1", "S  SKP  1\n  1  2  1",
    "  1 F    2   6   8",  # Legacy atom-list record, not a bond.
])
def test_sdf_property_or_alias_payload_is_not_surplus_connectivity(tmp_path, properties):
    text = sdf().replace("M  END", properties + "\nM  END")
    if properties.startswith("  1 F"):
        text = text.replace("  3  2  0", "  3  2  1", 1)
    with QVFReader(convert(tmp_path, "sdf", text)) as reader:
        assert reader.read_structure().bonds == [(0, 1, 2.0, (0, 0, 0)), (1, 2, 1.0, (0, 0, 0))]


def test_mol2_empty_type_can_use_existing_known_name_fallback(tmp_path):
    text = mol2().replace("30 O 2 0 0 O.2", "30 O2 2 0 0 .")
    with QVFReader(convert(tmp_path, "mol2", text)) as reader:
        assert reader.read_structure().atoms[1].symbol == "O"
