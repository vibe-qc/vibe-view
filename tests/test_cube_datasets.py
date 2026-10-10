"""Cube datasets are interleaved per voxel, with the field index fastest."""

import json
import struct
import zipfile

import numpy as np
import pytest
from click.testing import CliRunner

from vibeview.cli import main
from vibeview.converters import convert_to_qvf, cube_to_qvf
from vibeview.qvf import QVFReader

# Independent, asymmetric first-field values in cube x/y/z order.
EXPECTED = np.array([
    [[-3.25, -2.75], [6.75, 7.25], [16.75, 17.25]],
    [[96.75, 97.25], [106.75, 107.25], [116.75, 117.25]],
])


def cube_text(*, fields=2, orbital=False, nval=None, dset=None, values=None, negative=False):
    counts = (-2, -3, -2) if negative else (2, 3, 2)
    header_nval = ("" if orbital else f" {fields}") if nval is None else nval
    lines = ["orbital cube" if orbital else "density cube", "multiple scalar datasets",
             f"{-1 if orbital else 1} 0 0 0{header_nval}",
             f"{counts[0]} 1 0 0", f"{counts[1]} 0 1 0", f"{counts[2]} 0 0 -1",
             "6 6 0 0 0"]
    if orbital:
        ids = [str(7 + 4*f) for f in range(fields)]
        lines.extend(dset if dset is not None else [str(fields) + " " + " ".join(ids)])
    if values is None:
        values = [-3.25 + 100*i + 10*j + 0.5*k + 1000*f
                  for i in range(2) for j in range(3) for k in range(2)
                  for f in range(fields)]
    # Deliberately wrap inside a voxel's fields, independent of metadata lines.
    lines.extend(" ".join(str(v) for v in values[i:i+5]) for i in range(0, len(values), 5))
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("orbital", [False, True])
@pytest.mark.parametrize("fields", [1, 2, 3])
@pytest.mark.parametrize("negative", [False, True])
def test_first_complete_dataset_is_selected(orbital, fields, negative):
    output = cube_to_qvf(cube_text(fields=fields, orbital=orbital, negative=negative).encode())
    with QVFReader(output) as reader:
        assert len([s for s in reader.sections if s.kind.startswith("volume.")]) == 1
        assert reader.read_volume_grid("vol_0").shape == (2, 3, 2)
        np.testing.assert_array_equal(reader.read_volume_data("vol_0"), EXPECTED)
    output.seek(0)
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        member = manifest["sections"][1]["members"]["data"]
        assert member["dtype"] == "float32"
        assert archive.read(member["path"]) == struct.pack("<12f", *EXPECTED.flat)


@pytest.mark.parametrize("nval", ["", " 1"])
def test_wrapped_orbital_ids_define_field_count(nval):
    text = cube_text(fields=12, orbital=True, nval=nval,
                     dset=["12 90 4", "72 81 5 0", "8 15 61 3 22 9"])
    with QVFReader(cube_to_qvf(text.encode())) as reader:
        np.testing.assert_array_equal(reader.read_volume_data("vol_0"), EXPECTED)


@pytest.mark.parametrize("nval", [" 0", " -2", " 2.5", " two"])
def test_invalid_nval_is_rejected(nval):
    with pytest.raises(ValueError, match="cube.*NVAL"):
        cube_to_qvf(cube_text(nval=nval).encode())


def test_negative_atom_count_refuses_conflicting_nval():
    with pytest.raises(ValueError, match="cube.*NVAL"):
        cube_to_qvf(cube_text(orbital=True, nval=" 2").encode())


@pytest.mark.parametrize("dset", [[], ["0"], ["-2"], ["2.5 7 8"], ["two 7 8"],
                                  ["2 7"], ["2 7 wrong"], ["2 7", "3.5"], ["2 7 8 9"]])
def test_missing_or_invalid_orbital_metadata_is_rejected(dset):
    with pytest.raises(ValueError, match="cube.*(DSET|dataset)"):
        cube_to_qvf(cube_text(orbital=True, dset=dset, values=[]).encode())


def test_scalar_payload_cannot_replace_missing_orbital_metadata():
    with pytest.raises(ValueError, match="cube.*DSET"):
        cube_to_qvf(cube_text(orbital=True, dset=[]).encode())


@pytest.mark.parametrize("orbital", [False, True])
def test_all_declared_datasets_must_be_complete(orbital):
    # More than one grid of samples, but not the two declared fields.
    with pytest.raises(ValueError, match="expected 24"):
        cube_to_qvf(cube_text(orbital=orbital, values=list(range(23))).encode())


@pytest.mark.parametrize("orbital", [False, True])
def test_registered_importer_and_cli_use_first_dataset(tmp_path, orbital):
    source = tmp_path / "fields.cube"
    source.write_text(cube_text(orbital=orbital))
    with QVFReader(convert_to_qvf(source)) as reader:
        np.testing.assert_array_equal(reader.read_volume_data("vol_0"), EXPECTED)
    output = tmp_path / "fields.qvf"
    result = CliRunner().invoke(main, ["import", str(source), "-o", str(output)])
    assert result.exit_code == 0, result.output
    with QVFReader(output) as reader:
        np.testing.assert_array_equal(reader.read_volume_data("vol_0"), EXPECTED)


def test_cli_reports_incomplete_datasets_without_output(tmp_path):
    source = tmp_path / "truncated.cube"
    source.write_text(cube_text(values=list(range(23))))
    output = tmp_path / "out.qvf"
    result = CliRunner().invoke(main, ["import", str(source), "-o", str(output)])
    assert result.exit_code != 0
    assert "expected 24" in result.output
    assert not output.exists()
