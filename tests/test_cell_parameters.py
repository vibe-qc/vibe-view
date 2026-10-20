"""Cell construction rejects invalid geometry before import can publish it."""

import numpy as np
import pytest
from click.testing import CliRunner

from vibeview.cli import main
from vibeview.converters import cif_to_qvf, pdb_to_qvf
from vibeview.crystal_builder import cell_from_abc


@pytest.mark.parametrize("axis", range(3))
@pytest.mark.parametrize("value", [0.0, -1.0, float("nan"), float("inf")])
def test_cell_lengths_must_be_finite_and_positive(axis, value):
    parameters = [5.0, 6.0, 7.0, 90.0, 90.0, 90.0]
    parameters[axis] = value
    with pytest.raises(ValueError, match="cell lengths"):
        cell_from_abc(*parameters)


@pytest.mark.parametrize("axis", range(3))
@pytest.mark.parametrize("value", [0.0, 180.0, float("nan"), float("inf")])
def test_cell_angles_must_be_finite_and_inside_the_open_range(axis, value):
    parameters = [5.0, 6.0, 7.0, 90.0, 90.0, 90.0]
    parameters[axis + 3] = value
    with pytest.raises(ValueError, match="cell angles"):
        cell_from_abc(*parameters)


@pytest.mark.parametrize(
    "angles", [(10, 10, 170), (60, 120, 60), (120, 60, 60), (60, 60, 120), (120, 120, 120)]
)
def test_cell_angles_must_define_a_nonzero_real_volume(angles):
    with pytest.raises(ValueError, match="cell angles"):
        cell_from_abc(5.0, 6.0, 7.0, *angles)


@pytest.mark.parametrize("angles", [(75, 85, 65), (90, 90, 120), (60, 60, 60)])
def test_valid_skewed_cells_preserve_the_requested_metric(angles):
    lengths = np.array([5.0, 6.0, 7.0])
    cell = cell_from_abc(*lengths, *angles)
    np.testing.assert_allclose(np.linalg.norm(cell, axis=1), lengths, rtol=1e-12)
    directions = cell / lengths[:, None]
    alpha, beta, gamma = np.cos(np.deg2rad(angles))
    np.testing.assert_allclose(
        directions @ directions.T,
        [[1, gamma, beta], [gamma, 1, alpha], [beta, alpha, 1]],
        atol=1e-12,
    )
    assert np.linalg.det(cell) > 0


def test_valid_narrow_angle_keeps_the_correct_nonzero_volume():
    gamma = 1e-6
    cell = cell_from_abc(5.0, 6.0, 7.0, 90.0, 90.0, gamma)
    # With c perpendicular to a and b, the volume is their base area times c.
    expected_volume = 5.0 * 6.0 * 7.0 * np.sin(np.deg2rad(gamma))
    assert np.linalg.det(cell) == pytest.approx(expected_volume, rel=1e-8)
    assert np.linalg.norm(cell[2]) == pytest.approx(7.0, rel=1e-12)


def _cell_text(fmt, parameters):
    a, b, c, alpha, beta, gamma = parameters
    if fmt == "pdb":
        return (
            f"CRYST1{a:9.3f}{b:9.3f}{c:9.3f}"
            f"{alpha:7.2f}{beta:7.2f}{gamma:7.2f} {'P 1':<11}{1:4d}\n"
            "ATOM      1  O   HOH A   1       1.250  -2.500   3.750  1.00 20.00           O  \n"
            "END\n"
        )
    names = ("length_a", "length_b", "length_c", "angle_alpha", "angle_beta", "angle_gamma")
    return (
        "data_cell\n"
        + "".join(f"_cell_{name} {value}\n" for name, value in zip(names, parameters, strict=True))
        + "loop_\n_atom_site_type_symbol\n"
        "_atom_site_fract_x\n_atom_site_fract_y\n_atom_site_fract_z\nO 0.25 0.5 0.75\n"
    )


@pytest.mark.parametrize("fmt", ["cif", "pdb"])
@pytest.mark.parametrize(
    "parameters",
    [(5, 6, 7, 10, 10, 170), (5, 6, 7, 120, 120, 120),
     (5, 6, 7, 90, 90, 0), (0, 6, 7, 90, 90, 90)],
    ids=["impossible", "coplanar", "zero-angle", "zero-length"],
)
def test_invalid_imported_cell_does_not_replace_an_existing_output(tmp_path, fmt, parameters):
    text = _cell_text(fmt, parameters)
    converter = cif_to_qvf if fmt == "cif" else pdb_to_qvf
    with pytest.raises(ValueError, match="cell (angles|lengths)"):
        converter(text.encode())

    source = tmp_path / f"cell.{fmt}"
    source.write_text(text, encoding="utf-8")
    output = tmp_path / "cell.qvf"
    output.write_bytes(b"existing output")
    result = CliRunner().invoke(main, ["import", str(source), "-o", str(output), "--force"])
    assert result.exit_code == 1, result.output
    assert "cell angles" in result.output or "cell lengths" in result.output
    assert output.read_bytes() == b"existing output"
