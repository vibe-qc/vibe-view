"""GRO uses fixed fields and a cell belonging to the first declared frame."""

import io

import numpy as np
import pytest
from click.testing import CliRunner

from vibeview.cli import main
from vibeview.converters import convert_to_qvf, gro_to_qvf
from vibeview.qvf import QVFReader


def atom(residue, name, xyz=(0, 0, 0), *, number=1, resid=1, precision=3, velocity=False):
    width = precision + 5
    text = f"{resid:5d}{residue:<5}{name:>5}{number:5d}"
    text += "".join(f"{value:{width}.{precision}f}" for value in xyz)
    if velocity:
        text += "".join(f"{value:{width}.{precision+1}f}" for value in [0.125, -0.25, 0.5])
    return text


def gro(atoms=None, box="3 4 5", *, count=None, title="frame"):
    atoms = atoms if atoms is not None else [atom("ALA", "CA"), atom("ASN", "ND2", (0.1, 0.2, 0.3))]
    return "\n".join([title, str(len(atoms) if count is None else count), *atoms, box]) + "\n"


def converted(tmp_path, text):
    source = tmp_path / "structure.gro"
    source.write_text(text)
    return convert_to_qvf(source)


@pytest.mark.parametrize("precision", [1, 2, 3, 5])
@pytest.mark.parametrize("velocity", [False, True])
def test_fixed_fields_and_variable_precision(tmp_path, precision, velocity):
    text = gro([atom("CA", "CA", (-123.5, -234.5, -345.5),
                     precision=precision, velocity=velocity)])
    with QVFReader(converted(tmp_path, text)) as reader:
        np.testing.assert_allclose(reader.read_structure().atoms[0].position,
                                   [-1235, -2345, -3455], rtol=0, atol=1e-12)


def test_extra_precision_is_not_rounded_to_standard(tmp_path):
    text = gro([atom("CA", "CA", (1.23456, -2.34567, 3.45678), precision=5)])
    with QVFReader(converted(tmp_path, text)) as reader:
        np.testing.assert_allclose(reader.read_structure().atoms[0].position,
                                   [12.3456, -23.4567, 34.5678], rtol=0, atol=1e-12)


@pytest.mark.parametrize("box,expected", [
    ("3 4 5", [[30, 0, 0], [0, 40, 0], [0, 0, 50]]),
    ("3 4 5 0 0 1 0 -0.5 1.5", [[30, 0, 0], [10, 40, 0], [-5, 15, 50]]),
    ("3 4 5 0.2 -0.1 1 0.3 -0.5 1.5", [[30, 2, -1], [10, 40, 3], [-5, 15, 50]]),
    ("0.01 0.02 0.03", [[0.1, 0, 0], [0, 0.2, 0], [0, 0, 0.3]]),
])
def test_literal_cell_vectors_in_angstroms(tmp_path, box, expected):
    with QVFReader(converted(tmp_path, gro(box=box))) as reader:
        # Check the imported payload independently of rendering density limits.
        structure = reader._read_json_member("structure", "structure")
        assert structure["pbc"] == [True, True, True]
        np.testing.assert_allclose(structure["lattice_vectors"], expected, rtol=0, atol=1e-14)


@pytest.mark.parametrize("box", ["0 0 0", "0 -0.0 0 0 0 0 0 0 0"])
def test_exactly_zero_box_means_nonperiodic(tmp_path, box):
    with QVFReader(converted(tmp_path, gro(box=box))) as reader:
        structure = reader.read_structure()
        assert structure.pbc == (False, False, False)
        assert structure.lattice_vectors is None


@pytest.mark.parametrize("later", [
    gro(box="9 10 11"), gro(box="bad later box"), "incomplete later frame",
])
def test_first_frame_owns_its_cell(tmp_path, later):
    with QVFReader(converted(tmp_path, gro(box="3 4 5") + later)) as reader:
        structure = reader.read_structure()
        assert len(structure.atoms) == 2
        np.testing.assert_array_equal(
            structure.lattice_vectors, [[30, 0, 0], [0, 40, 0], [0, 0, 50]],
        )
        np.testing.assert_allclose(structure.atoms[1].position, [1, 2, 3])


@pytest.mark.parametrize("stream", [False, True])
def test_empty_title_and_wrapped_identifiers_are_valid(stream):
    text = gro([atom("CA", "CA", number=0, resid=0),
                atom("CA", "CA", number=0, resid=0)], title="").encode()
    with QVFReader(gro_to_qvf(io.BytesIO(text) if stream else text)) as reader:
        assert len(reader.read_structure().atoms) == 2


BAD = [
    ("short-count", gro(count=1)), ("long-count", gro(count=3)),
    ("invalid-count", gro(count="bad")), ("zero-count", gro(count=0)),
    ("negative-count", gro(count=-2)),
    ("missing-box", "\n".join(gro().splitlines()[:-1]) + "\n"),
    ("missing-atom", "\n".join(gro().splitlines()[:3]) + "\n"),
    ("bad-atom", gro([atom("CA", "CA"), "broken atom"])),
    ("bad-atom-number", gro([atom("CA", "CA")[:15] + "  bad" + atom("CA", "CA")[20:]])),
    ("empty-name", gro([atom("CA", "")])), ("empty-residue", gro([atom("", "CA")])),
    ("nan-coordinate", gro([atom("CA", "CA", (float("nan"), 0, 0))])),
    ("infinite-coordinate", gro([atom("CA", "CA", (0, float("inf"), 0))])),
    ("shifted-fields", gro([atom("CA", "CA"), atom("CA", "CA")[1:]])),
    ("mixed-precision", gro([atom("CA", "CA"), atom("CA", "CA", precision=5)])),
    ("bad-box", gro(box="bad box")), ("partial-box", gro(box="3 4 5 0 0")),
    ("extra-box", gro(box="3 4 5 0 0 1 0 -0.5 1.5 99")),
    ("nan-box", gro(box="3 nan 5")), ("infinite-box", gro(box="3 4 inf")),
    ("xy-box", gro(box="3 4 0")), ("singular-box", gro(box="1 1 1 1 1 1 1 1 1")),
    ("overflow-after-units", gro(box="1e308 4 5")),
]


@pytest.mark.parametrize("name,text", BAD, ids=[case[0] for case in BAD])
def test_malformed_first_frame_is_rejected(tmp_path, name, text):
    with pytest.raises(ValueError, match="[Gg][Rr][Oo]"):
        converted(tmp_path, text)


@pytest.mark.parametrize("name,text", BAD, ids=[case[0] for case in BAD])
@pytest.mark.parametrize("existing", [False, True])
def test_cli_errors_preserve_destination(tmp_path, name, text, existing):
    source = tmp_path / f"{name}.gro"
    source.write_text(text)
    output = tmp_path / "output.qvf"
    if existing:
        output.write_bytes(b"keep existing archive")
    result = CliRunner().invoke(main, ["import", str(source), "-o", str(output), "--force"])
    assert result.exit_code != 0, result.output
    assert "gro" in result.output.lower()
    if existing:
        assert output.read_bytes() == b"keep existing archive"
    else:
        assert not output.exists()


def test_cli_valid_protein_and_ions_keep_triclinic_first_frame(tmp_path):
    source = tmp_path / "trajectory.gro"
    source.write_text(gro([atom("ALA", "CA"), atom("CA", "CA", number=2)],
                          "3 4 5 0 0 1 0 -0.5 1.5") + gro(box="9 9 9"))
    output = tmp_path / "output.qvf"
    result = CliRunner().invoke(main, ["import", str(source), "-o", str(output)])
    assert result.exit_code == 0, result.output
    with QVFReader(output) as reader:
        structure = reader.read_structure()
        assert [a.atomic_number for a in structure.atoms] == [6, 20]
        np.testing.assert_array_equal(
            structure.lattice_vectors, [[30, 0, 0], [10, 40, 0], [-5, 15, 50]],
        )
