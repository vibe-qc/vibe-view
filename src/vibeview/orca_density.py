"""ORCA-owned density decoding and lossless cube attachment.

ORCA 6.1 utilities: https://www.faccts.de/docs/orca/6.1/manual/contents/
utilitiesvisualization/utilities.html#orca-plot
Only the user's executable reads GBW/density containers. Scratch copies isolate
ORCA's incidental writes from calculation files.
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

import numpy as np

from vibeview.qvf import QVFReader
from vibeview.qvf_writer import QVFArchiveBuilder


def executable() -> str:
    tool = shutil.which(os.environ.get("ORCA_PLOT", "orca_plot"))
    if tool is None:
        raise ValueError(
            "ORCA density plots need your matching ORCA orca_plot utility. "
            "Install ORCA, put orca_plot on PATH or set ORCA_PLOT to its path."
        )
    return tool


def _run(tool: str, directory: Path, arguments: list[str], input_text: str | None = None) -> str:
    # Some ORCA menus repeat indefinitely at EOF on invalid input. Bound both
    # runtime and diagnostics instead of accumulating that output in memory.
    try:
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as source:
            source.write((input_text or "").encode())
            source.seek(0)
            with subprocess.Popen(
                [tool, *arguments],
                cwd=directory,
                stdin=source,
                stdout=output,
                stderr=subprocess.STDOUT,
            ) as process:
                try:
                    for _ in range(1200):
                        try:
                            process.wait(timeout=0.1)
                            break
                        except subprocess.TimeoutExpired:
                            if output.tell() > 2_000_000:
                                raise ValueError(
                                    "ORCA plotting returned excessive diagnostics"
                                ) from None
                    else:
                        raise ValueError("ORCA density plot timed out after 120 seconds")
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                output.seek(0)
                text = output.read(2_000_000).decode(errors="replace")
                if process.returncode:
                    raise ValueError(
                        "ORCA could not plot this density; check that the GBW and density "
                        "container match and use the ORCA version that wrote them."
                    )
                return text
    except OSError as exc:
        raise ValueError("Could not run the configured ORCA plotting utility") from exc


def available_densities(gbw: Path) -> list[str]:
    """Ask ORCA for names, without decoding its proprietary container."""
    tool = executable()
    container = gbw.with_suffix(".densities")
    if not container.is_file():
        raise ValueError("No matching .densities file; retain densities in the ORCA calculation.")
    with tempfile.TemporaryDirectory(prefix="vibeview-orca-") as temporary:
        directory = Path(temporary)
        shutil.copy2(container, directory / container.name)
        info = gbw.with_suffix(".densitiesinfo")
        if info.is_file():
            shutil.copy2(info, directory / info.name)
        text = _run(tool, directory, [container.name])
    return re.findall(r"^\s*\d+:\s+(\S+)\s*$", text, re.MULTILINE)


def generate_cube(
    gbw: Path,
    density: str,
    positions_angstrom: np.ndarray,
    *,
    resolution: int = 40,
    margin: float = 3.7,
    kind: str = "density",
    orbital: int = 0,
    subtract_density: str = "",
) -> bytes:
    """Plot a named stored density or an orbital from a natural-orbital GBW.

    ORCA 6.1's native plot-input format gives explicit bounds in bohr. Density
    names are chosen from ORCA's list, never interpreted as filesystem paths.
    """
    tool = executable()
    positions = np.asarray(positions_angstrom, dtype=float)
    if (
        positions.ndim != 2
        or positions.shape[1] != 3
        or not len(positions)
        or not np.isfinite(positions).all()
    ):
        raise ValueError("Plotting requires finite Cartesian atom coordinates")
    if not 8 <= resolution <= 256 or not np.isfinite(margin) or not 0 < margin <= 20:
        raise ValueError("Use 8–256 grid points and a positive margin up to 20 Å")
    if kind not in {"density", "spin", "difference", "transition", "orbital"}:
        raise ValueError("Unknown density plot kind")
    if orbital < 0:
        raise ValueError("Orbital indices must be non-negative")
    available = available_densities(gbw) if kind != "orbital" else []
    if subtract_density and subtract_density not in available:
        raise ValueError("The reference density is absent from this ORCA container")
    if kind != "orbital" and density not in available:
        raise ValueError("The selected density is absent from this ORCA container")
    if kind == "orbital":
        density = "unused"
    if not re.fullmatch(r"[\w.\-]+", density):
        raise ValueError("Invalid ORCA density name")
    lower = (positions.min(axis=0) - margin) / 0.529177210903
    upper = (positions.max(axis=0) + margin) / 0.529177210903
    with tempfile.TemporaryDirectory(prefix="vibeview-orca-") as temporary:
        directory = Path(temporary)
        shutil.copy2(gbw, directory / gbw.name)
        for suffix in (".densities", ".densitiesinfo"):
            companion = gbw.with_suffix(suffix)
            if companion.is_file():
                shutil.copy2(companion, directory / companion.name)
        if subtract_density:
            # ORCA performs the matrix algebra and stores the resulting density;
            # the viewer never decodes or subtracts proprietary density matrices.
            transcript = _run(
                tool,
                directory,
                [gbw.name, "-i"],
                f"10\n2\n{density}\n1\n{subtract_density}\n1\n12\n",
            )
            names = re.findall(r"ElDens File\s+\.\.\.\s+(\S+)", transcript)
            if not names:
                raise ValueError("ORCA did not produce a difference density")
            density = names[-1]
        # PlotType, format (Gaussian cube), MO/operator, state-density flag,
        # input/output names, contour controls, grid, bounds and plane defaults.
        lines = [
            str(1 if kind == "orbital" else 3 if kind == "spin" else 2),
            "7",
            f"{orbital} 0",
            "0",
            density,
            "result.cube",
            "0",
            "0",
            "0",
            "0",
            "0",
            f"{resolution} {resolution} {resolution}",
        ]
        lines += [f"{a:.14g} {b:.14g}" for a, b in zip(lower, upper, strict=True)]
        lines += ["-1 -1 -1", "0 0 0", "1 0 0", "0 1 0"]
        (directory / "request.plot").write_text("\n".join(lines) + "\n")
        _run(tool, directory, [gbw.name, "request.plot"])
        output = directory / "result.cube"
        if not output.is_file():
            raise ValueError("ORCA returned no density cube")
        return output.read_bytes()


def _copy_reader(builder: QVFArchiveBuilder, reader: QVFReader) -> None:
    manifest = reader.manifest_json()
    for key, value in manifest.items():
        if key not in {"qvf_version", "source", "sections"}:
            builder.set_root(key, value)
    for section in manifest["sections"]:
        extras = {k: v for k, v in section.items() if k not in {"id", "kind", "members"}}
        for name, spec in section["members"].items():
            payload = reader.read_member_bytes(spec["path"])
            if spec["format"] == "json":
                builder.add_json(
                    section["id"], section["kind"], name, json.loads(payload), section_extra=extras
                )
            else:
                array = np.frombuffer(payload, dtype=np.dtype(spec["dtype"]).newbyteorder("<"))
                builder.add_binary(
                    section["id"],
                    section["kind"],
                    name,
                    array.reshape(spec["shape"]),
                    spec["dtype"],
                    section_extra=extras,
                )


def attach_cube(
    reader: QVFReader,
    cube: bytes,
    *,
    kind: str = "density",
    label: str = "ORCA density",
    state_index: int | None = None,
) -> tuple[io.BytesIO, bool, str]:
    """Preserve cube values in QVF; mismatched geometry remains a separate dataset."""
    from vibeview.converters import cube_to_qvf

    converted = cube_to_qvf(cube, dtype="float64")
    with QVFReader(converted) as volume:
        structure = next((s for s in reader.sections if s.kind == "structure"), None)
        left = reader.read_structure(structure.id).atoms if structure else []
        right = volume.read_structure("structure").atoms
        matches = (
            len(left) == len(right)
            and [a.atomic_number for a in left] == [a.atomic_number for a in right]
            and np.allclose(
                [a.position for a in left], [a.position for a in right], atol=1e-5, rtol=0
            )
        )
        builder = QVFArchiveBuilder(calculation="ORCA density plot")
        _copy_reader(builder, reader if matches else volume)
        if not matches:
            return (
                builder.to_bytesio(),
                False,
                "Cube geometry differs (0.00001 Å); opened separately",
            )
        if kind not in {"density", "spin", "difference", "transition", "orbital"}:
            raise ValueError("Unknown density plot kind")
        counter = len(reader.sections)
        section_id = "orca_density_" + str(counter)
        while any(item.id == section_id for item in reader.sections):
            counter += 1
            section_id = "orca_density_" + str(counter)
        volume_kind = "volume.difference" if kind == "transition" else "volume." + kind
        builder.add_json(
            section_id,
            volume_kind,
            "grid",
            volume._read_json_member("vol_0", "grid"),
            section_extra={"label": label},
        )
        builder.add_binary(
            section_id, volume_kind, "data", volume.read_volume_data("vol_0"), "float64"
        )
        if state_index is not None:
            for section in reader.sections:
                if section.kind != "excited_states":
                    continue
                payload = reader._read_json_member(section.id, "states")
                for state in payload["states"]:
                    if state["index"] == state_index:
                        state["density_ref"] = section_id
                builder.add_json(section.id, section.kind, "states", payload)
        return builder.to_bytesio(), True, section_id


def generate_cis_cube(
    gbw: Path, vector: int, *, resolution: int = 40, kind: str = "transition"
) -> bytes:
    """ORCA's CIS-vector utility, with its fixed seven-bohr padding.

    Full TDDFT uses X+Y vector 2*state-1; TDA/CIS uses vector=state. This
    explicit vector selection avoids silently interpreting X-Y as a state.
    """
    if vector < 1 or not 8 <= resolution <= 256 or kind not in {"transition", "difference"}:
        raise ValueError(
            "Select a positive CIS vector, 8–256 grid points and transition/difference"
        )
    tool = executable()
    cis = gbw.with_suffix(".cis")
    if not cis.is_file():
        raise ValueError("This calculation has no matching .cis amplitudes file")
    with tempfile.TemporaryDirectory(prefix="vibeview-orca-") as temporary:
        directory = Path(temporary)
        for source in (gbw, cis, gbw.with_suffix(".densities"), gbw.with_suffix(".densitiesinfo")):
            if source.is_file():
                shutil.copy2(source, directory / source.name)
        menu = 7 if kind == "transition" else 6
        _run(tool, directory, [gbw.name, "-i"], f"4\n{resolution}\n{menu}\ny\n{vector}\n12\n")
        prefix = "cistp" if kind == "transition" else "cisdp"
        cubes = list(directory.glob(f"{gbw.stem}.{prefix}*.cube"))
        if len(cubes) != 1:
            raise ValueError("ORCA returned no unique cube for that CIS vector")
        return cubes[0].read_bytes()
