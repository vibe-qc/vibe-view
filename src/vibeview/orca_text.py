"""Read ORCA's typed property text into the ORCA 6.1 JSON representation.

The grammar and geometry mapping follow the official property-file reference:
https://www.faccts.de/docs/orca/6.1/manual/contents/utilitiesvisualization/property_file.html
https://www.faccts.de/docs/orca/6.1/manual/contents/utilitiesvisualization/orca_2json.html

Matrices carry explicit row and column indices and may span multiple column
blocks. Preserve all repeated property records, including spectrum gauges and
relativistic variants, instead of treating a property name as a unique key.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator
from typing import Any

_COMPONENT = re.compile(r"^\s*&([A-Za-z0-9_]+)\s*(?:\[([^\]]*)\])?(.*)$")
_TYPE = re.compile(r'&Type\s+"([^"]+)"')
_DIMENSION = re.compile(r"&Dim\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)")
_UNITS = re.compile(r'&Units\s+"([^"]+)"')
_GLOBAL_PROPERTIES = {"Calculation_Info", "Calculation_Status", "Calculation_Timings"}
_LIST_PROPERTIES = (
    "Absorption_Spectrum",
    "ECD_Spectrum",
    "Dipole_Moment",
    "Mulliken_Population_Analysis",
    "Loewdin_Population_Analysis",
    "Mayer_Population_Analysis",
)
_ENERGY_PROPERTIES = {"SCF_Energy", "CIS_Energies", "CAS_SCF_Energies", "Energy"}


def _sections(text: str) -> Iterator[tuple[str, list[str]]]:
    name: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("$"):
            marker = stripped[1:]
            if marker.lower() == "end":
                if name is None:
                    raise ValueError("ORCA property text has an unmatched $End")
                yield name, lines
                name, lines = None, []
            elif name is not None:
                raise ValueError(f"ORCA property {name!r} is missing $End")
            else:
                name = marker
        elif name is not None:
            lines.append(line)
    if name is not None:
        raise ValueError(f"ORCA property {name!r} is missing $End")


def _number(value: str, integer: bool = False) -> int | float:
    if integer:
        return int(value)
    result = float(value.replace("D", "E").replace("d", "e"))
    if not math.isfinite(result):
        raise ValueError("ORCA property values must be finite")
    return result


def _boolean(value: str) -> bool:
    if value.lower() not in {"true", "false"}:
        raise ValueError(f"Invalid ORCA Boolean value {value!r}")
    return value.lower() == "true"


def _matrix(lines: list[str], shape: tuple[int, int], kind: str) -> list[list[Any]]:
    """Assemble indexed column blocks without confusing headers with data."""
    rows, columns = shape
    data = [line.split() for line in lines if line.strip()]
    if rows and columns == 0:
        raise ValueError("ORCA property matrix with rows must have at least one column")
    if rows and columns and rows > len(data):
        raise ValueError("ORCA property matrix has fewer rows than its declared dimensions")
    result: list[list[Any]] = [[] for _ in range(rows)]
    offset, column = 0, 0
    while column < columns:
        if offset >= len(data):
            if rows == 0:
                return result
            raise ValueError("ORCA property matrix is missing columns")
        header = [int(value) for value in data[offset]]
        width = len(header)
        if column + width > columns or header != list(range(column, column + width)):
            raise ValueError("Invalid ORCA property matrix column indices")
        offset += 1
        for row in range(rows):
            if offset >= len(data):
                raise ValueError("ORCA property matrix is missing rows")
            tokens = data[offset]
            if len(tokens) != width + 1 or int(tokens[0]) != row:
                raise ValueError("Invalid ORCA property matrix row indices or width")
            values = tokens[1:]
            if kind == "ArrayOfBooleans":
                result[row].extend(_boolean(value) for value in values)
            else:
                result[row].extend(
                    _number(value, integer=kind == "ArrayOfIntegers") for value in values
                )
            offset += 1
        column += width
    if offset != len(data):
        raise ValueError("ORCA property matrix has unexpected trailing values")
    return result


def _value(metadata: str, inline: str, lines: list[str]) -> Any:
    type_match = _TYPE.search(metadata)
    if type_match is None:
        raise ValueError("ORCA property component is missing its Type")
    kind = type_match[1]
    if kind == "String":
        value, _ = json.JSONDecoder().raw_decode(inline.strip())
        if not isinstance(value, str):
            raise ValueError("Invalid ORCA String value")
        return value
    if kind in {"Integer", "Double", "Boolean"}:
        tokens = inline.split()
        if not tokens:
            raise ValueError(f"Missing ORCA {kind} value")
        if kind == "Boolean":
            return _boolean(tokens[0])
        return _number(tokens[0], integer=kind == "Integer")
    dimension_match = _DIMENSION.search(metadata)
    if dimension_match is None:
        raise ValueError("ORCA property array is missing its Dim")
    shape = (int(dimension_match[1]), int(dimension_match[2]))
    if kind == "Coordinates":
        data = [line.split() for line in lines if line.strip()]
        if shape[1] != 4 or len(data) != shape[0] or any(len(row) != 4 for row in data):
            raise ValueError("ORCA coordinates do not match their declared dimensions")
        unit_match = _UNITS.search(metadata)
        if unit_match is None:
            raise ValueError("ORCA coordinates are missing their Units")
        units = unit_match[1]
        return {
            "Type": "Cartesians",
            "Units": "a.u." if units.lower() == "bohr" else units,
            "Cartesians": [[row[0], *(_number(value) for value in row[1:])] for row in data],
        }
    if kind in {"ArrayOfDoubles", "ArrayOfIntegers", "ArrayOfBooleans"}:
        return _matrix(lines, shape, kind)
    raise ValueError(
        f"Unsupported ORCA property type {kind!r}; export property JSON with orca_2json"
    )


def _property(lines: list[str]) -> tuple[int, dict[str, Any]]:
    components = [(index, _COMPONENT.match(line)) for index, line in enumerate(lines)]
    headers = [(index, match) for index, match in components if match is not None]
    result: dict[str, Any] = {}
    geometry_index: int | None = None
    for position, (index, match) in enumerate(headers):
        name, metadata, inline = match.groups()
        end = headers[position + 1][0] if position + 1 < len(headers) else len(lines)
        if name == "GeometryIndex":
            geometry_index = int(inline.strip())
            if geometry_index < 1:
                raise ValueError("ORCA GeometryIndex must be positive")
        elif name == "ListStatus":
            # ORCA 6.0 also includes this bookkeeping field.
            continue
        else:
            key = "Coordinates" if name == "CartesianCoordinates" else name
            if key in result:
                raise ValueError(f"Duplicate ORCA property component {name!r}")
            result[key] = _value(metadata or "", inline, lines[index + 1 : end])
    if geometry_index is None:
        raise ValueError("ORCA property is missing its GeometryIndex")
    return geometry_index, result


def read_property_text(text: str) -> dict[str, Any]:
    """Parse typed ORCA property text, keeping each geometry and property variant.

    Return the ``Calculation_*`` and ``Geometries`` layout used by ORCA 6.1.
    Numeric matrices retain both dimensions, including one-column vectors.
    Invalid/truncated arrays raise ``ValueError`` rather than supplying zeros
    that could be mistaken for calculated results.
    """
    result: dict[str, Any] = {}
    geometries: dict[int, dict[str, Any]] = {}
    for name, lines in _sections(text):
        try:
            geometry_index, value = _property(lines)
        except ValueError as exc:
            raise ValueError(f"Invalid ORCA property {name!r}: {exc}") from exc
        if name in _GLOBAL_PROPERTIES:
            result[name] = value
            continue
        geometry = geometries.setdefault(geometry_index, {})
        if name in _ENERGY_PROPERTIES:
            geometry.setdefault("Energy", []).append(value)
            continue
        canonical = next(
            (key for key in _LIST_PROPERTIES if name == key or name.endswith("_" + key)), None
        )
        if canonical is not None:
            geometry.setdefault(canonical, []).append(value)
        elif name in geometry:
            # Preserve repeated future property types without guessing a schema.
            previous = geometry[name]
            geometry[name] = [*previous, value] if isinstance(previous, list) else [previous, value]
        else:
            geometry[name] = value
    if not geometries:
        raise ValueError("No geometries or properties found in ORCA property text")
    result["Geometries"] = [geometries[index] for index in sorted(geometries)]
    return result
