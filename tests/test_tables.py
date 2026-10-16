"""Tabular exports preserve every supported per-atom population array."""

from __future__ import annotations

import csv
import io
import json

import numpy as np
import pytest
from click.testing import CliRunner

from tests._qvf_builders import build_qvf
from vibeview.api import get_table, validate
from vibeview.cli import main
from vibeview.qvf import QVFReader
from vibeview.tables import extract_table

_VALUES = {
    "mulliken_charge": [-0.1, 0.1],
    "loewdin_charge": [-0.2, 0.2],
    "hirshfeld_charge": [-0.3, 0.3],
    "iao_charge": [-0.123456, 0.123456],
    "spin_population": [0.75, 0.25],
}
_COLUMNS = ["atom", "element", "mulliken", "loewdin", "hirshfeld"]


def _population_archive(tmp_path, names):
    structure = json.dumps({
        "atoms": [
            {"symbol": "H", "atomic_number": 1, "position": [0.0, 0.0, 0.0]},
            {"symbol": "H", "atomic_number": 1, "position": [0.74, 0.0, 0.0]},
        ],
        "pbc": [False, False, False],
    }).encode()
    members = {
        name: (
            "binary", f"populations/{name}.bin",
            np.asarray(_VALUES[name], dtype="<f8").tobytes(), "float64", [2],
        )
        for name in names
    }
    path = build_qvf(tmp_path / "populations.qvf", [
        ("structure", "structure", {"structure": ("json", "structure.json", structure)}),
        ("populations", "atom_properties", members),
    ])
    assert validate(path)["valid"]
    return path


def _expected_records(names):
    columns = [*_COLUMNS]
    if "iao_charge" in names:
        columns.append("iao")
    if "spin_population" in names:
        columns.append("spin_population")
    records = []
    for i in range(2):
        record = {"atom": i + 1, "element": "H"}
        for column in columns[2:]:
            member = column if column == "spin_population" else f"{column}_charge"
            record[column] = round(_VALUES[member][i], 4) if member in names else None
        records.append(record)
    return columns, records


@pytest.mark.parametrize("names", [
    ("mulliken_charge",),
    ("loewdin_charge",),
    ("hirshfeld_charge",),
    ("iao_charge",),
    ("spin_population",),
    ("iao_charge", "spin_population"),
    tuple(_VALUES),
])
def test_extract_population_arrays(tmp_path, names):
    path = _population_archive(tmp_path, names)
    expected_columns, expected_records = _expected_records(names)
    with QVFReader(path) as reader:
        columns, rows = extract_table(reader, "atom_properties")
    assert columns == expected_columns
    assert [dict(zip(columns, row, strict=True)) for row in rows] == expected_records


@pytest.mark.parametrize("names", [
    ("iao_charge",),
    ("spin_population",),
    tuple(_VALUES),
])
@pytest.mark.parametrize("consumer", ["sdk", "json", "csv", "table"])
def test_population_table_consumers(tmp_path, names, consumer):
    path = _population_archive(tmp_path, names)
    expected_columns, expected_records = _expected_records(names)
    if consumer == "sdk":
        columns, rows = get_table(path, "atom_properties")
        assert columns == expected_columns
        assert [dict(zip(columns, row, strict=True)) for row in rows] == expected_records
        return

    result = CliRunner().invoke(main, [
        "table", str(path), "--kind", "atom_properties", "--format", consumer,
    ])
    assert result.exit_code == 0, result.output
    if consumer == "json":
        assert json.loads(result.output) == expected_records
    elif consumer == "csv":
        table = csv.DictReader(io.StringIO(result.output))
        assert table.fieldnames == expected_columns
        assert list(table) == [
            {key: "" if value is None else str(value) for key, value in record.items()}
            for record in expected_records
        ]
    else:
        for column in expected_columns:
            assert column in result.output
        for record in expected_records:
            for column in expected_columns[2:]:
                if record[column] is not None:
                    assert str(record[column]) in result.output
