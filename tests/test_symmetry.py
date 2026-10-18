"""Linear point groups and their molecular axes are independent of atom count."""

from __future__ import annotations

import json

import numpy as np
import pytest

from tests.conftest import build_qvf
from vibeview.qvf import QVFReader
from vibeview.symmetry import detect_from_qvf, detect_point_group, pg_summary

_ROTATIONS = [
    np.eye(3),
    np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
    np.array([[1.0, -2.0, 2.0], [2.0, 2.0, 1.0], [-2.0, 1.0, 2.0]]) / 3.0,
]


@pytest.mark.parametrize("rotation", _ROTATIONS, ids=["z-axis", "x-axis", "oblique"])
@pytest.mark.parametrize(
    ("symbols", "axial_positions", "expected"),
    [
        pytest.param(["O", "C", "O"], [-1.16, 0.0, 1.16], "D∞h", id="carbon-dioxide"),
        pytest.param(["H", "C", "N"], [-1.06, 0.0, 1.16], "C∞v", id="hydrogen-cyanide"),
        pytest.param(
            ["H", "C", "C", "H"], [-1.66, -0.60, 0.60, 1.66], "D∞h", id="acetylene"
        ),
        pytest.param(["N", "N", "O"], [-1.128, 0.0, 1.184], "C∞v", id="nitrous-oxide"),
        pytest.param(["O", "C", "O"], [-1.0, 0.0, 1.4], "C∞v", id="unequal-terminal-bonds"),
        pytest.param(["H", "H"], [-0.37, 0.37], "D∞h", id="hydrogen"),
        pytest.param(["H", "F"], [-0.46, 0.46], "C∞v", id="hydrogen-fluoride"),
    ],
)
def test_linear_group_and_axis(symbols, axial_positions, expected, rotation):
    positions = np.zeros((len(symbols), 3))
    positions[:, 2] = axial_positions
    positions = positions @ rotation.T + [2.5, -3.0, 4.25]
    original = positions.copy()

    group = detect_point_group(symbols, positions)

    assert group.symbol == expected
    assert group.order == 0  # Existing convention for an infinite group.
    assert group.rotor_type == "symmetric"
    assert group.principal_axis is not None
    assert abs(float(group.principal_axis @ rotation[:, 2])) == pytest.approx(1.0)
    assert group.moments[0] == pytest.approx(0.0, abs=1e-10)
    assert group.moments[1] == pytest.approx(group.moments[2])
    np.testing.assert_array_equal(positions, original)


@pytest.mark.parametrize("rotation", _ROTATIONS, ids=["z-axis", "x-axis", "oblique"])
def test_bent_carbon_dioxide_keeps_its_finite_group(rotation):
    positions = np.array([[0.0, 0.0, -1.16], [0.5, 0.0, 0.0], [0.0, 0.0, 1.16]])
    positions = positions @ rotation.T + [2.5, -3.0, 4.25]

    assert detect_point_group(["O", "C", "O"], positions).symbol == "C2v"


def test_linear_detection_uses_the_existing_position_tolerance():
    positions = np.array([[0.0, 0.0, -1.16], [0.02, 0.0, 0.0], [0.0, 0.0, 1.16]])

    assert detect_point_group(["O", "C", "O"], positions).symbol == "D∞h"


@pytest.mark.parametrize(
    ("symbols", "numbers", "axial_positions", "expected"),
    [
        (["O", "C", "O"], [8, 6, 8], [-1.16, 0.0, 1.16], "D∞h"),
        (["H", "C", "N"], [1, 6, 7], [-1.06, 0.0, 1.16], "C∞v"),
    ],
    ids=["carbon-dioxide", "hydrogen-cyanide"],
)
def test_qvf_detection_and_viewer_summary_use_the_linear_group(
    tmp_path, symbols, numbers, axial_positions, expected
):
    axis = _ROTATIONS[-1][:, 2]
    atoms = [
        {"symbol": symbol, "atomic_number": number, "position": (position * axis).tolist()}
        for symbol, number, position in zip(symbols, numbers, axial_positions, strict=True)
    ]
    data = json.dumps({"atoms": atoms, "pbc": [False, False, False]}).encode()
    path = build_qvf(
        tmp_path / "linear.qvf",
        [("molecule", "structure", {"structure": ("json", "structure.json", data)})],
    )

    with QVFReader(path) as reader:
        group = detect_from_qvf(reader)

    assert group is not None
    assert group.symbol == expected
    assert f"Point group: {expected}" in pg_summary(group)
    assert group.principal_axis is not None
    assert abs(float(group.principal_axis @ axis)) == pytest.approx(1.0)
