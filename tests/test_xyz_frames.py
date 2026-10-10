"""XYZ imports one complete frame without merging or dropping atom rows."""

import io

import numpy as np
import pytest
from click.testing import CliRunner

from vibeview.cli import main
from vibeview.converters import convert_to_qvf, xyz_to_qvf
from vibeview.qvf import QVFReader

FIRST = "2\nfirst geometry\nC -1.25 2.5 3\nO 4 -5 6.75\n"
SECOND = "3\nsecond geometry\nN 20 30 40\nH 21 30 40\nH 20 31 40\n"
EXPECTED = [[-1.25, 2.5, 3], [4, -5, 6.75]]


def assert_first_geometry(payload):
    with QVFReader(payload) as reader:
        structure = reader.read_structure()
        assert [atom.symbol for atom in structure.atoms] == ["C", "O"]
        assert [atom.atomic_number for atom in structure.atoms] == [6, 8]
        np.testing.assert_array_equal([atom.position for atom in structure.atoms], EXPECTED)
        assert structure.pbc == (False, False, False)
        assert structure.lattice_vectors is None


@pytest.mark.parametrize("trailing", [SECOND, "N 20 30 40\n", "broken later frame\n", "\n"])
def test_registered_importer_uses_only_first_declared_frame(tmp_path, trailing):
    source = tmp_path / "trajectory.xyz"
    source.write_text(FIRST + trailing)
    assert_first_geometry(convert_to_qvf(source))


@pytest.mark.parametrize("source_kind", ["bytes", "stream", "path"])
@pytest.mark.parametrize("comment", [
    "", "   ", 'Lattice="8 0 0 0 8 0 0 0 8" Properties=species:S:1:pos:R:3',
])
def test_complete_frame_accepts_empty_comment_and_extra_columns(tmp_path, source_kind, comment):
    text = f"2\n{comment}\nQq -1e0 2.5 3 tag 7\nH 4 -5 6.75 ignored\n"
    if source_kind == "bytes":
        source = text.encode()
    elif source_kind == "stream":
        source = io.BytesIO(text.encode())
    else:
        source = tmp_path / "geometry.xyz"
        source.write_text(text)
    with QVFReader(xyz_to_qvf(source)) as reader:
        structure = reader.read_structure()
        assert [atom.symbol for atom in structure.atoms] == ["Qq", "H"]
        assert [atom.atomic_number for atom in structure.atoms] == [0, 1]
        np.testing.assert_array_equal(
            [a.position for a in structure.atoms], [[-1, 2.5, 3], [4, -5, 6.75]],
        )
        assert structure.pbc == (False, False, False)
        assert structure.lattice_vectors is None


@pytest.mark.parametrize("ending", ["\n", "\r\n", ""])
def test_frame_does_not_require_final_newline(ending):
    text = FIRST.rstrip("\n").replace("\n", "\r\n" if ending == "\r\n" else "\n") + ending
    assert_first_geometry(xyz_to_qvf(text.encode()))


INVALID = [
    ("noninteger-count", FIRST.replace("2\n", "atoms\n", 1)),
    ("fractional-count", FIRST.replace("2\n", "2.0\n", 1)),
    ("zero-count", FIRST.replace("2\n", "0\n", 1)),
    ("negative-count", FIRST.replace("2\n", "-2\n", 1)),
    ("blank-count", FIRST.replace("2\n", "\n", 1)),
    ("truncated-atoms", FIRST.replace("2\n", "3\n", 1)),
    ("missing-comment", "2\n"),
    ("blank-atom", FIRST.replace("C -1.25 2.5 3", "")),
    ("missing-coordinate", FIRST.replace("C -1.25 2.5 3", "C -1.25 2.5")),
    ("invalid-coordinate", FIRST.replace("C -1.25 2.5 3", "C invalid 2.5 3")),
    ("nan-coordinate", FIRST.replace("C -1.25 2.5 3", "C nan 2.5 3")),
    ("infinite-coordinate", FIRST.replace("C -1.25 2.5 3", "C -1.25 inf 3")),
    ("overflow-coordinate", FIRST.replace("C -1.25 2.5 3", "C -1.25 2.5 1e999")),
    ("missing-symbol", FIRST.replace("C -1.25 2.5 3", "-1.25 2.5 3")),
    ("next-frame-cannot-fill-first", FIRST.replace("2\n", "3\n", 1) + SECOND),
]


@pytest.mark.parametrize("name,text", INVALID, ids=[case[0] for case in INVALID])
def test_invalid_first_frame_raises_without_skipping_rows(tmp_path, name, text):
    source = tmp_path / f"{name}.xyz"
    source.write_text(text)
    with pytest.raises(ValueError, match="XYZ"):
        convert_to_qvf(source)


@pytest.mark.parametrize("name,text", INVALID, ids=[case[0] for case in INVALID])
@pytest.mark.parametrize("existing", [False, True])
def test_cli_invalid_first_frame_does_not_publish(tmp_path, name, text, existing):
    source = tmp_path / f"{name}.xyz"
    source.write_text(text)
    output = tmp_path / "geometry.qvf"
    if existing:
        output.write_bytes(b"existing destination")
    result = CliRunner().invoke(main, ["import", str(source), "-o", str(output), "--force"])
    assert result.exit_code != 0, result.output
    assert "XYZ" in result.output
    if existing:
        assert output.read_bytes() == b"existing destination"
    else:
        assert not output.exists()


def test_cli_publishes_first_geometry_only(tmp_path):
    source = tmp_path / "trajectory.xyz"
    source.write_text(FIRST + SECOND)
    output = tmp_path / "geometry.qvf"
    result = CliRunner().invoke(main, ["import", str(source), "-o", str(output)])
    assert result.exit_code == 0, result.output
    assert_first_geometry(output)
