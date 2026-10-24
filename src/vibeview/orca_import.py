"""Read ORCA property files, exported wavefunctions and MCD transitions.

ORCA's supported exchange format is documented at
https://www.faccts.de/docs/orca/6.1/manual/contents/utilitiesvisualization/orca_2json.html
The proprietary GBW format is decoded only by the user's ORCA installation.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import IO, Any

import numpy as np

from vibeview.qvf_writer import QVFArchiveBuilder

ORCA_EXTENSIONS = (".property.json", ".property.txt", ".gbw", ".mcd", ".hess")


def is_orca_json(data: bytes) -> bool:
    """Conservatively recognize the headers of either ORCA JSON dialect."""
    text = data[:65536].decode("utf-8-sig", errors="replace")
    return bool(
        re.search(r'"Molecule"\s*:\s*\{', text)
        and re.search(r'"Atoms"\s*:\s*\[', text)
        or re.search(r'"Geometries"\s*:\s*\[', text)
        and re.search(r'"Calculation_(?:Status|Info)"\s*:', text)
    )


def is_orca_path(path: str | Path) -> bool:
    """Match compound suffixes, and probe otherwise ambiguous JSON files."""
    path = Path(path)
    if path.name.lower().endswith(ORCA_EXTENSIONS):
        return True
    if path.suffix.lower() == ".json":
        try:
            with path.open("rb") as stream:
                return is_orca_json(stream.read(65536))
        except OSError:
            return False
    if path.suffix.lower() in {".out", ".log", ".inp"}:
        from vibeview.orca_dataset import orca_dataset_key

        if orca_dataset_key(path) is not None:
            return True
        if path.suffix.lower() in {".out", ".log"}:
            try:
                with path.open("rb") as stream:
                    return bool(re.search(rb"O\s+R\s+C\s+A", stream.read(65536)))
            except OSError:
                return False
    return False


def _gbw_json(source: bytes) -> dict[str, Any]:
    executable = shutil.which(os.environ.get("ORCA_2JSON", "orca_2json"))
    if executable is None:
        raise ValueError(
            "Opening ORCA GBW files needs the matching ORCA orca_2json utility. "
            "Put it on PATH or set ORCA_2JSON to its executable path; alternatively "
            "run 'orca_2json calculation.gbw -json' and open calculation.json."
        )
    executable = str(Path(executable).resolve())
    # Do not inherit a nearby orca.json.conf: it can request enormous integral
    # exports. The source and its companions are never modified or executed.
    with tempfile.TemporaryDirectory(prefix="vibeview-orca-") as directory:
        work = Path(directory)
        (work / "wavefunction.gbw").write_bytes(source)
        (work / "wavefunction.json.conf").write_text(
            json.dumps({"MOCoefficients": True, "Basisset": True}), encoding="utf-8"
        )
        try:
            result = subprocess.run(
                [executable, "wavefunction.gbw", "-json"],
                cwd=work, capture_output=True, timeout=120, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("ORCA wavefunction export timed out after 120 seconds") from exc
        except OSError as exc:
            raise ValueError("Could not run the configured ORCA orca_2json utility") from exc
        output = work / "wavefunction.json"
        if result.returncode or not output.is_file():
            raise ValueError(
                "orca_2json could not export this GBW file. Use the ORCA version "
                "that produced the file, or export it to JSON with that version."
            )
        return _json_object(output.read_bytes())


def _json_object(content: bytes) -> dict[str, Any]:
    try:
        data = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Invalid ORCA JSON file") from exc
    if not isinstance(data, dict):
        raise ValueError("ORCA JSON must contain an object")
    return data


def _add_mcd(
    builder: QVFArchiveBuilder, content: bytes, *, prefix: str = "", source_name: str = "",
) -> None:
    """Preserve raw orientation transitions, without summing duplicate lines.

ORCA 6.1 manual §5.31: energy (cm^-1), difference in circular oscillator
strengths, sum of circular oscillator strengths. These are raw sticks,
not the molar spectrum produced by orca_mapspc's thermal averaging.
https://www.faccts.de/docs/orca/6.1/manual/contents/spectroscopyproperties/mcd.html
"""
    try:
        values = np.loadtxt(io.BytesIO(content), ndmin=2)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("ORCA MCD requires three numeric columns: cm^-1, L-R, L+R") from exc
    if values.ndim != 2 or values.shape[1] != 3 or not len(values):
        raise ValueError("ORCA MCD requires three numeric columns: cm^-1, L-R, L+R")
    if not np.isfinite(values).all():
        raise ValueError("ORCA MCD transitions must be finite")
    for column, section_id, label, intensity in (
        (1, "mcd", "ORCA circular dichroism (raw MCD sticks)", "L-R oscillator strength"),
        (2, "absorption", "ORCA absorption (raw MCD sticks)", "L+R oscillator strength"),
    ):
        builder.add_json(
            prefix + section_id, "spectra.generic", "spectrum",
            {
                "frequencies": values[:, 0].tolist(),
                "intensities": values[:, column].tolist(),
                "units": {"frequency": "cm^-1", "intensity": "dimensionless"},
                "orca": {"quantity": intensity, "sampling": "raw orientation transitions",
                         "source_file": source_name},
            },
            section_extra={
                "label": f"{label} — {source_name}" if source_name else label,
                "spectrum_style": {
                    "x_label": "Wavenumber (cm⁻¹)",
                    "y_label": intensity,
                    "x_unit_short": "cm⁻¹",
                },
            },
        )


def orca_to_qvf(source: str | Path | IO[bytes] | bytes) -> io.BytesIO:
    """Open a calculation from a path, or import isolated uploaded content."""
    if isinstance(source, str | Path):
        from vibeview.orca_dataset import orca_calculation_to_qvf

        return orca_calculation_to_qvf(source)
    content = source if isinstance(source, bytes) else source.read()
    builder = QVFArchiveBuilder(calculation="ORCA import")
    if b"$orca_hessian_file" in content:
        from vibeview.orca_hessian import add_hessian

        add_hessian(builder, content)
        return builder.to_bytesio()
    from vibeview.orca_output import add_output, is_output

    if is_output(content.decode("utf-8-sig", errors="replace")):
        add_output(builder, content.decode("utf-8-sig"))
        return builder.to_bytesio()
    if content.removeprefix(b"\xef\xbb\xbf").lstrip().startswith(b"{"):
        data = _json_object(content)
    elif b"$Geometry" in content:
        from vibeview.orca_text import read_property_text

        data = read_property_text(content.decode("utf-8-sig"))
    else:
        raise ValueError(
            "Expected an ORCA .property.json, .property.txt, orca_2json JSON, "
            ".gbw, .hess or .mcd file"
        )
    if "Molecule" in data:
        from vibeview.orca_orbitals import add_molecule

        add_molecule(builder, data)
    elif "Geometries" in data:
        from vibeview.orca_properties import add_properties

        add_properties(builder, data)
    else:
        raise ValueError("JSON contains neither ORCA Molecule nor Geometries data")
    return builder.to_bytesio()
