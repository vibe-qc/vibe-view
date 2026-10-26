"""Read printed results of one completed ORCA calculation, without companions.

Conventions follow ORCA 6.1 sections 5.18 and 5.6:
https://www.faccts.de/docs/orca/6.1/manual/contents/spectroscopyproperties/vibrations.html
https://www.faccts.de/docs/orca/6.1/manual/contents/spectroscopyproperties/tddft.html
Printed normal modes are Cartesian displacements (the mass weighting has
already been removed by ORCA). Never apply an additional mass transformation.
"""

from __future__ import annotations

import json
import re
from typing import Any

import numpy as np

from vibeview.elements import SYMBOL_TO_Z
from vibeview.orca_hessian import _float, _normal_modes, add_vcd_output
from vibeview.orca_properties import add_properties
from vibeview.qvf_writer import QVFArchiveBuilder

_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?"
_GEOMETRY = re.compile(
    r"CARTESIAN COORDINATES \(ANGSTROEM\)\s*\n-+\s*\n(.*?)(?:\n\s*\n|\Z)",
    re.DOTALL,
)


def is_output(text: str) -> bool:
    """Recognize output without interpreting arbitrary .out files as ORCA."""
    return bool(
        re.search(r"O\s+R\s+C\s+A", text)
        or (
            "CARTESIAN COORDINATES (ANGSTROEM)" in text
            and ("ORCA TERMINATED" in text or "VIBRATIONAL FREQUENCIES" in text)
        )
    )


def _single_job(text: str) -> None:
    if text.count("ORCA TERMINATED NORMALLY") != 1 or re.search(
        r"(?:ORCA finished by error termination|ORCA TERMINATED ABNORMALLY|"
        r"^\s*COMPOUND JOB|JOB NUMBER\s*[2-9]|^\|\s*\d+>.*(?:\bCompound\b|\$new_job\b))",
        text,
        re.IGNORECASE | re.MULTILINE,
    ):
        raise ValueError(
            "Output import requires one normally terminated ORCA calculation; "
            "Compound/multiple jobs and abnormal termination are not supported"
        )
    if len(re.findall(r"^\s*\*?\s*O\s+R\s+C\s+A\b", text, re.MULTILINE)) > 1:
        raise ValueError("ORCA output contains multiple program invocations")
    names = set(re.findall(r"^\s*Basename:\s*(\S+)", text, re.MULTILINE))
    if len(names) > 1:
        raise ValueError("ORCA output contains multiple calculation basenames")


def _body(text: str, title: str) -> str | None:
    matches = list(re.finditer(r"^\s*" + re.escape(title) + r"\s*$", text, re.MULTILINE))
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(f"Repeated ORCA output table {title}; open one calculation")
    # A major heading is framed by dashed lines; the first line after this
    # heading is its own underline, so start searching after that underline.
    rest = text[matches[0].end() :].lstrip("\n")
    rest = re.sub(r"^-+\s*\n", "", rest, count=1)
    end = re.search(r"\n\s*[A-Z][A-Z /().0-9-]{3,}\s*\n\s*-{4,}", rest)
    return rest[: end.start()] if end else rest


def _vibrations(builder: QVFArchiveBuilder, text: str) -> None:
    body = _body(text, "VIBRATIONAL FREQUENCIES")
    if body is None:
        if _body(text, "NORMAL MODES") is not None:
            raise ValueError("ORCA normal modes have no vibrational frequencies")
        return
    structure = next(s for s in builder.manifest["sections"] if s["id"] == "structure")
    atoms = json.loads(dict(builder.members())[structure["members"]["structure"]["path"]])["atoms"]
    size = 3 * len(atoms)
    rows = re.findall(r"^\s*(\d+):\s*(" + _NUMBER + r")\s*cm\*\*-1", body, re.MULTILINE)
    if [int(i) for i, _ in rows] != list(range(size)):
        raise ValueError(
            "ORCA output requires all 3N frequencies with consecutive zero-based indices"
        )
    frequencies = np.asarray([_float(f) for _, f in rows])
    modes_body = _body(text, "NORMAL MODES")
    if modes_body is None:
        raise ValueError("ORCA frequencies are missing their printed NORMAL MODES")
    matrix_lines = []
    started = False
    for line in modes_body.splitlines():
        fields = line.split()
        if fields and all(x.isdigit() for x in fields):
            started = True
            matrix_lines.append(line)
        elif started and fields and fields[0].isdigit():
            matrix_lines.append(line)
        elif started and fields:
            break
    modes = _normal_modes([f"{size} {size}", *matrix_lines], size)
    builder.add_json(
        "orca_vibrations",
        "vibrations",
        "metadata",
        {
            "atoms": atoms,
            "frequencies": frequencies.tolist(),
            "orca": {
                "mode_indices": list(range(size)),
                "displacement_convention": "stored Cartesian normal modes",
                "source": "printed ORCA output",
            },
        },
        section_extra={"label": "ORCA harmonic vibrations", "structure_ref": "structure"},
    )
    builder.add_binary("orca_vibrations", "vibrations", "displacements", modes)
    for title, kind, unit, column in (
        ("IR SPECTRUM", "ir", "km/mol", 2),
        ("RAMAN SPECTRUM", "raman", "angstrom^4/amu", 1),
    ):
        table = _body(text, title)
        if table is None:
            continue
        if kind == "ir" and not re.search(r"km/mol", table):
            raise ValueError("Unsupported ORCA output IR intensity units")
        indices, strengths = [], []
        ratios = []
        for match in re.finditer(r"^\s*(\d+):\s*(.*)$", table, re.MULTILINE):
            index = int(match[1])
            values = [_float(v) for v in re.findall(_NUMBER, match[2])]
            if index >= size or index in indices or len(values) <= column:
                raise ValueError(f"Invalid ORCA output {title} row")
            if not np.isclose(values[0], frequencies[index], atol=0.005001, rtol=0):
                raise ValueError(f"ORCA output {title} frequency disagrees with normal modes")
            indices.append(index)
            strengths.append(values[column])
            if kind == "raman":
                if len(values) != 3:
                    raise ValueError(
                        "ORCA Raman row requires frequency, activity and depolarization"
                    )
                ratios.append(values[2])
        if not indices:
            raise ValueError(f"Empty ORCA output {title}")
        vendor: dict[str, Any] = {"mode_indices": indices, "source": "printed ORCA output"}
        if ratios:
            vendor["depolarization_ratios"] = ratios
        builder.add_json(
            f"orca_{kind}",
            f"spectra.{kind}",
            "spectrum",
            {
                "frequencies": frequencies[indices].tolist(),
                "intensities": strengths,
                "labels": [f"Mode {i}" for i in indices],
                "units": {"frequency": "cm^-1", "intensity": unit},
                "orca": vendor,
            },
            section_extra={"label": f"ORCA harmonic {kind.upper()}"},
        )
    add_vcd_output(builder, text)


def _electronic(text: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    headings = re.findall(
        r"^\s*((?:ABSORPTION|CD) SPECTRUM VIA TRANSITION (?:ELECTRIC|VELOCITY|MAGNETIC) "
        r"DIPOLE MOMENTS)\s*$",
        text,
        re.MULTILINE,
    )
    for title in headings:
        table = _body(text, title)
        assert table is not None
        absorption = title.startswith("ABSORPTION")
        key = "Absorption_Spectrum" if absorption else "ECD_Spectrum"
        representation = "Velocity" if "VELOCITY" in title else "Length"
        if "(eV)" not in table or "(cm-1)" not in table or "(nm)" not in table:
            raise ValueError("Unsupported ORCA electronic spectrum units; expected eV/cm-1/nm")
        if not absorption and "(1e40*cgs)" not in table.lower():
            raise ValueError("Unsupported ORCA output CD strength units")
        rows: list[list[float]] = []
        states: list[str] = []
        for line in table.splitlines():
            match = re.match(r"\s*(\S+\s*->\s*\S+)\s+(.*)", line)
            if not match:
                continue
            values = [_float(v) for v in match[2].split()]
            if len(values) < 4:
                raise ValueError("Incomplete ORCA electronic spectrum row")
            states.append(match[1])
            rows.append(values)
        if not rows:
            raise ValueError(f"Empty or unsupported ORCA output {title}")
        # Retain the printed method heading rather than guessing a numerical
        # property-file Method enum or treating TDDFT data as SCF.
        context = text[: text.index(title)]
        methods = re.findall(
            r"^\s*((?:TD-DFT|TDDFT|CIS|TDA|ROCIS|STEOM|EOM)[^\n]*)", context, re.MULTILINE
        )
        result.setdefault(key, []).append(
            {
                "Representation": representation,
                "NTrans": len(rows),
                "ExcitationEnergies": rows,
                "OutputTransitions": states,
                "Method": methods[-1].strip() if methods else "not printed",
                "OutputHeading": title,
            }
        )
    return result


def add_output(builder: QVFArchiveBuilder, text: str) -> None:
    """Add only values actually printed in a single completed ORCA output."""
    _single_job(text)
    geometries: list[dict[str, Any]] = []
    previous: list[list[Any]] | None = None
    for match in _GEOMETRY.finditer(text):
        coords: list[list[Any]] = []
        for line in match[1].splitlines():
            fields = line.split()
            if len(fields) != 4 or fields[0] not in SYMBOL_TO_Z:
                raise ValueError("Invalid ORCA output Cartesian geometry")
            coords.append([fields[0], *[_float(v) for v in fields[1:]]])
        if not coords:
            raise ValueError("Empty ORCA output Cartesian geometry")
        if previous and [a[0] for a in coords] != [a[0] for a in previous]:
            raise ValueError("ORCA output changes atom identities/order between geometries")
        if coords != previous:
            geometries.append(
                {
                    "Geometry": {
                        "Coordinates": {
                            "Units": "Angstrom",
                            "Cartesians": coords,
                        }
                    }
                }
            )
        previous = coords
    if not geometries:
        raise ValueError("ORCA output has no printed Cartesian geometry")
    geometries[-1].update(_electronic(text))
    info = {}
    for label, key in (("Total Charge", "Charge"), ("Multiplicity", "Mult")):
        values = re.findall(r"^\s*" + label + r"\s+.*?([+-]?\d+)\s*$", text, re.MULTILINE)
        if values:
            if len(set(values)) != 1:
                raise ValueError(f"ORCA output has inconsistent {label}")
            info[key] = int(values[0])
    add_properties(builder, {"Geometries": geometries, "Calculation_Info": info})
    _vibrations(builder, text)


def check_companion(reference: QVFArchiveBuilder, output: QVFArchiveBuilder) -> set[str]:
    """Check printed precision against preferred companions; return redundant IDs.

    Compare mode-indexed subsets: ORCA omits translational/rotational spectrum
    rows from stdout, whereas its Hessian includes them. Gauge is identity,
    not an excuse to combine different transition moments.
    """

    def payload(builder: QVFArchiveBuilder, section: dict[str, Any], name: str) -> Any:
        spec = section["members"][name]
        raw = dict(builder.members())[spec["path"]]
        if spec["format"] == "json":
            return json.loads(raw)
        return np.frombuffer(raw, dtype=np.dtype(spec["dtype"]).newbyteorder("<")).reshape(
            spec["shape"]
        )

    skipped: set[str] = set()
    sections = reference.manifest["sections"]
    ref_structure = next((s for s in sections if s["id"] == "structure"), None)
    if ref_structure is None:
        return skipped
    out_structure = next(s for s in output.manifest["sections"] if s["id"] == "structure")
    a = payload(reference, ref_structure, "structure")["atoms"]
    b = payload(output, out_structure, "structure")["atoms"]
    if [v["atomic_number"] for v in a] != [v["atomic_number"] for v in b]:
        raise ValueError("ORCA output and companion atom identities/order disagree")
    delta = np.asarray([v["position"] for v in a]) - np.asarray([v["position"] for v in b])
    if not np.allclose(delta, delta[0], atol=1e-5, rtol=0):
        raise ValueError("ORCA output and companion final geometries disagree")
    for field in ("charge", "multiplicity"):
        left = reference.manifest.get("provenance", {}).get(field)
        right = output.manifest.get("provenance", {}).get(field)
        if left is not None and right is not None and left != right:
            raise ValueError(f"ORCA output and companion {field} disagree")
    skipped.add("structure")
    for section in output.manifest["sections"]:
        kind = section["kind"]
        candidates = [s for s in sections if s["kind"] == kind]
        if kind == "trajectory" and candidates:
            skipped.add(section["id"])
        elif kind == "vibrations" and not candidates:
            metadata = payload(output, section, "metadata")
            metadata["atoms"] = a
            output.add_json(section["id"], kind, "metadata", metadata)
        elif kind == "vibrations" and candidates:
            actual = payload(output, section, "metadata")
            expected = payload(reference, candidates[0], "metadata")
            if not np.allclose(
                actual["frequencies"], expected["frequencies"], atol=0.005001, rtol=0
            ):
                raise ValueError("ORCA output and companion frequencies disagree")
            left = payload(output, section, "displacements")
            right = payload(reference, candidates[0], "displacements")
            if left.shape != right.shape or not all(
                np.allclose(x, y, atol=0.0000005001, rtol=0)
                or np.allclose(x, -y, atol=0.0000005001, rtol=0)
                for x, y in zip(left, right, strict=True)
            ):
                raise ValueError("ORCA output and companion normal modes disagree")
            skipped.add(section["id"])
        elif kind.startswith("spectra.") and candidates:
            actual = payload(output, section, "spectrum")
            gauge = actual.get("orca", {}).get("Representation")
            matches = [(s, payload(reference, s, "spectrum")) for s in candidates]
            matches = [
                (s, p) for s, p in matches if p.get("orca", {}).get("Representation") == gauge
            ]
            # Property records may include spectra at earlier optimization steps.
            # Prefer the last geometry, exactly as the default structure does.
            if not matches:
                if any(s["id"] == section["id"] for s in sections):
                    raise ValueError("ORCA output and companion spectrum gauges disagree")
                continue
            selected, expected = max(matches, key=lambda row: row[0].get("geometry_index", 0))
            if (
                sum(
                    s.get("geometry_index", 0) == selected.get("geometry_index", 0)
                    for s, _ in matches
                )
                != 1
            ):
                raise ValueError("Ambiguous ORCA companion spectra for one gauge/geometry")
            indices = actual.get("orca", {}).get("mode_indices")
            if indices is not None:
                labels = expected.get("labels", [])
                try:
                    take = [labels.index(f"Mode {i}") for i in indices]
                except ValueError as exc:
                    raise ValueError("ORCA companion is missing printed spectrum modes") from exc
            else:
                take = list(range(len(expected["frequencies"])))
            if len(take) != len(actual["frequencies"]):
                raise ValueError("ORCA output and companion spectrum row counts disagree")
            frequency_tol = 0.005001 if indices is not None else 0.0000005001
            intensity_tol = {
                "spectra.ir": 0.005001,
                "spectra.raman": 0.000050501,
                "spectra.vcd": 0.005001,
                "spectra.ecd": 0.000005001,
            }.get(kind, 0.000000000501)
            if (
                actual["units"] != expected["units"]
                or not np.allclose(
                    actual["frequencies"],
                    np.asarray(expected["frequencies"])[take],
                    atol=frequency_tol,
                    rtol=0,
                )
                or not np.allclose(
                    actual["intensities"],
                    np.asarray(expected["intensities"])[take],
                    atol=intensity_tol,
                    rtol=0,
                )
            ):
                raise ValueError(f"ORCA output and companion {kind} values or units disagree")
            skipped.add(section["id"])
    return skipped
