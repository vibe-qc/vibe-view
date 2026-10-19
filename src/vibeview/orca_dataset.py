"""Assemble same-basename ORCA result companions as one QVF dataset.

ORCA 6.1 manual §§9.3–9.4 describe the separate wavefunction and property
exports. MCD file families are documented in §5.31. Compound steps have their
own basenames; geometry equality alone never establishes job identity.
https://www.faccts.de/docs/orca/6.1/manual/contents/utilitiesvisualization/orca_2json.html
https://www.faccts.de/docs/orca/6.1/manual/contents/spectroscopyproperties/mcd.html
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import numpy as np

from vibeview import orca_import
from vibeview.orca_hessian import add_hessian, add_vcd_output
from vibeview.orca_orbitals import add_molecule
from vibeview.orca_properties import add_properties
from vibeview.orca_text import read_property_text
from vibeview.qvf_writer import QVFArchiveBuilder

_MCD = re.compile(
    r"^(?P<base>.+)\.(?P<family>[^.]+-el\.[^.]+\.[^.]+)"
    r"(?P<transition>\.\d+_\d+)?\.mcd$", re.IGNORECASE,
)
_PROPERTY_SUFFIXES = (".property.json", ".property.txt")


def _result_key(path: Path) -> Path | None:
    """Identify a result without consulting siblings (basenames are exact)."""
    name = path.name
    lower = name.lower()
    for suffix in _PROPERTY_SUFFIXES:
        if lower.endswith(suffix):
            return path.with_name(name[:-len(suffix)])
    if path.suffix.lower() in {".gbw", ".hess"}:
        return path.with_suffix("")
    if path.suffix.lower() == ".mcd":
        match = _MCD.fullmatch(name)
        return path.with_name(match["base"]) if match else path.with_suffix("")
    if path.suffix.lower() == ".json":
        try:
            with path.open("rb") as stream:
                if orca_import.is_orca_json(stream.read(65536)):
                    return path.with_suffix("")
        except OSError:
            pass
    return None


def _companions(key: Path) -> list[Path]:
    # The prefix is only a cheap filter; _result_key must match exactly, so
    # job.extra and job.TDDFTGuess can never be folded into job.
    if not key.parent.is_dir():
        return []
    return sorted(
        (
            path for path in key.parent.iterdir()
            if path.name.startswith(key.name + ".") and path.is_file()
            and _result_key(path) == key
        ), key=lambda path: path.name,
    )


def orca_dataset_key(path: str | Path) -> Path | None:
    """Return the resolved calculation basename, or None for unrelated files.

    Input/output files locate exact companions; VCD tables supplement a Hessian.
    A key is session-local identity and is never written into archive metadata.
    """
    source = Path(path).resolve()
    key = _result_key(source)
    if key is not None:
        return key
    if source.suffix.lower() in {".out", ".inp"}:
        key = source.with_suffix("")
        if _companions(key):
            return key
    return None


def is_orca_auxiliary_path(path: str | Path) -> bool:
    """Skip TDDFT guess exports in automatic scans when primary results exist."""
    source = Path(path)
    key = _result_key(source)
    if key is None or not key.name.lower().endswith(".tddftguess"):
        return False
    primary = key.with_name(key.name[:-len(".TDDFTGuess")])
    return any(
        primary.with_name(primary.name + suffix).is_file()
        for suffix in (*_PROPERTY_SUFFIXES, ".gbw", ".json")
    )


def _choose(paths: list[Path], source: Path) -> Path | None:
    if source in paths:
        return source
    # An equally recent JSON export avoids an unnecessary binary conversion.
    return max(paths, key=lambda path: (path.stat().st_mtime_ns, path.suffix.lower() == ".json"),
               default=None)


def _is_property(path: Path) -> bool:
    if path.name.lower().endswith(_PROPERTY_SUFFIXES):
        return True
    if path.suffix.lower() != ".json":
        return False
    # Classify without parsing a potentially huge, unselected orbital export.
    # This is the same bounded header probe used for format recognition.
    with path.open("rb") as stream:
        header = stream.read(65536).decode("utf-8-sig", errors="replace")
    return bool(re.search(r'"Geometries"\s*:\s*\[', header)
                and not re.search(r'"Molecule"\s*:\s*\{', header))


def orca_dataset_entry(path: str | Path) -> Path:
    """Choose a fresh representative for automatic folder/glob discovery.

    Directory traversal order is not an explicit user choice of representation
    or transition. Prefer the newest property representation, then orbitals,
    then an aggregate MCD so opening it applies the ordinary dataset defaults.
    """
    source = Path(path).resolve()
    key = orca_dataset_key(source)
    if key is None:
        return source
    paths = _companions(key)
    properties = [item for item in paths if _is_property(item)]
    orbitals = [item for item in paths if item.suffix.lower() in {".gbw", ".json"}
                and item not in properties]
    # The basename is not itself a candidate, so _choose only uses freshness.
    preferred = _choose(properties, key) or _choose(orbitals, key)
    if preferred is not None:
        return preferred
    hessians = [item for item in paths if item.suffix.lower() == ".hess"]
    if hessian := _choose(hessians, key):
        return hessian
    spectra = [item for item in paths if item.suffix.lower() == ".mcd"]
    aggregates = [item for item in spectra if not (
        (match := _MCD.fullmatch(item.name)) and match["transition"]
    )]
    return next(iter(aggregates or spectra), source)


def _data(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    if path.suffix.lower() == ".gbw":
        return orca_import._gbw_json(content)
    if path.name.lower().endswith(".property.txt"):
        return read_property_text(content.decode("utf-8-sig"))
    return orca_import._json_object(content)


def _structure(builder: QVFArchiveBuilder) -> dict[str, Any]:
    section = next(item for item in builder.manifest["sections"] if item["id"] == "structure")
    payload = dict(builder.members())[section["members"]["structure"]["path"]]
    value: dict[str, Any] = json.loads(payload)
    return value


def _require_matching_geometry(properties: QVFArchiveBuilder, orbitals: QVFArchiveBuilder) -> None:
    left, right = _structure(properties)["atoms"], _structure(orbitals)["atoms"]
    if [atom["atomic_number"] for atom in left] != [atom["atomic_number"] for atom in right]:
        raise ValueError("ORCA property and orbital atom identities/order do not match")
    if not np.allclose(
        [atom["position"] for atom in left], [atom["position"] for atom in right],
        atol=1e-5, rtol=0,
    ):
        raise ValueError(
            "ORCA orbital geometry does not match the final property geometry "
            "(tolerance 0.00001 angstrom); open matching results from the same calculation"
        )
    for field in ("charge", "multiplicity"):
        a, b = properties.manifest.get("provenance", {}), orbitals.manifest.get("provenance", {})
        if field in a and field in b and a[field] != b[field]:
            raise ValueError(f"ORCA property and orbital {field} disagree")


def _merge(
    target: QVFArchiveBuilder, source: QVFArchiveBuilder, *, skip_structure: bool = False,
) -> None:
    members = dict(source.members())
    for section in source.manifest["sections"]:
        if skip_structure and section["id"] == "structure":
            continue
        extra = {
            key: value for key, value in section.items() if key not in {"id", "kind", "members"}
        }
        for name, spec in section["members"].items():
            payload = members[spec["path"]]
            if spec["format"] == "json":
                target.add_json(
                    section["id"], section["kind"], name, json.loads(payload), section_extra=extra,
                )
            else:
                array = np.frombuffer(payload, dtype=np.dtype(spec["dtype"]).newbyteorder("<"))
                target.add_binary(
                    section["id"], section["kind"], name, array.reshape(spec["shape"]),
                    dtype=spec["dtype"], section_extra=extra,
                )
    for key, value in source.manifest.items():
        if key == "provenance":
            target.set_provenance({**target.manifest.get("provenance", {}), **value})
        elif key not in {"qvf_version", "source", "sections"}:
            target.set_root(key, value)


def _match_hessian_geometry(target: QVFArchiveBuilder, hessian: QVFArchiveBuilder) -> None:
    """ORCA recenters Hessian coordinates; a translation leaves mode vectors unchanged.

    Accept only a common translation, never an atom permutation or rotation.
    Embed the shared geometry in the animation metadata and retain the applied
    translation so the stored Hessian coordinates remain recoverable.
    """
    a = target.manifest.get("provenance", {}).get("multiplicity")
    b = hessian.manifest.get("provenance", {}).get("multiplicity")
    if a is not None and b is not None and a != b:
        raise ValueError("ORCA calculation and Hessian multiplicity disagree")
    reference, original = _structure(target), _structure(hessian)
    left, right = reference["atoms"], original["atoms"]
    if [a["atomic_number"] for a in left] != [a["atomic_number"] for a in right]:
        raise ValueError("ORCA calculation and Hessian atom identities/order do not match")
    delta = np.asarray([a["position"] for a in left]) - np.asarray([a["position"] for a in right])
    if not np.allclose(delta, delta[0], atol=1e-5, rtol=0):
        raise ValueError("ORCA Hessian geometry does not match the calculation up to translation "
                         "(tolerance 0.00001 angstrom); rotation/reordering is not supported")
    section = next(s for s in hessian.manifest["sections"] if s["id"] == "orca_vibrations")
    payload = dict(hessian.members())[section["members"]["metadata"]["path"]]
    metadata = json.loads(payload)
    metadata["atoms"] = left
    metadata["orca"]["geometry_translation_angstrom"] = delta[0].tolist()
    hessian.add_json("orca_vibrations", "vibrations", "metadata", metadata)


def _locator_error(source: Path) -> ValueError:
    # An output can describe several Compound steps; never merge these simply
    # because the wrapper or geometries are shared. Only inspect plain basenames.
    names: list[str] = []
    if source.suffix.lower() == ".out":
        with source.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                match = re.search(r"\bBasename:\s+(\S+)", line)
                if (
                    match and re.fullmatch(r"[\w.-]+", match[1])
                    and match[1] not in {".", ".."} and match[1] not in names
                    and _companions(source.with_name(match[1]))
                ):
                    names.append(match[1])
    if names:
        return ValueError(
            "This ORCA output references separate calculations. Open a result file for one "
            "calculation, or open the directory to load each separately: " + ", ".join(names)
        )
    return ValueError(
        "No matching ORCA results beside this input/output file. Open its .property.json, "
        ".property.txt, .gbw, .hess, exported JSON or .mcd file (using the calculation basename)."
    )


def _vcd_output(key: Path, source: Path) -> tuple[Path, str] | None:
    """Prefer the named output; accept vq stdout only with explicit job identity.

    A queue directory may contain several jobs. A generic stdout.log must name
    exactly this input and cannot override its basename or contain Compound
    steps. Geometry/mode matching and normal completion are checked separately.
    """
    output = source if source.suffix.lower() == ".out" else key.with_suffix(key.suffix + ".out")
    if output.is_file():
        text = output.read_text(encoding="utf-8-sig", errors="replace")
        if "VCD SPECTRUM CALCULATION" in text:
            return output, text
        return None
    output = key.with_name("stdout.log")
    if not output.is_file():
        return None
    text = output.read_text(encoding="utf-8-sig", errors="replace")
    names = re.findall(r"^NAME\s*=\s*(.*?)\s*$", text, re.MULTILINE)
    if (names != [key.name + ".inp"] or re.search(
            r"^\|\s*\d+>.*(?:%base\b|\bCompound\b)", text, re.IGNORECASE | re.MULTILINE,
        )
            or "VCD SPECTRUM CALCULATION" not in text):
        return None
    return output, text


def orca_calculation_to_qvf(path: str | Path) -> io.BytesIO:
    """Combine a calculation's structure, orbitals, properties, Hessian and MCD files.

    An explicit representation wins; otherwise newest mtime wins, with JSON
    preferred on ties. Every chosen companion must parse and agree. A failure
    is reported instead of silently opening an incomplete or mismatched dataset.
    """
    source = Path(path).resolve()
    if not source.is_file():
        raise ValueError(f"ORCA result file does not exist: {source.name}")
    key = orca_dataset_key(source)
    if key is None:
        raise _locator_error(source)
    companions = _companions(key)
    property_paths: list[Path] = []
    orbital_paths: list[Path] = []
    mcd_paths: list[Path] = []
    hessian_paths: list[Path] = []
    for companion in companions:
        if _is_property(companion):
            property_paths.append(companion)
        elif companion.suffix.lower() == ".mcd":
            mcd_paths.append(companion)
        elif companion.suffix.lower() == ".hess":
            hessian_paths.append(companion)
        else:
            orbital_paths.append(companion)
    property_path = _choose(property_paths, source)
    orbital_path = _choose(orbital_paths, source)
    builder = QVFArchiveBuilder(calculation=key.name)
    selected: list[Path] = []
    property_builder: QVFArchiveBuilder | None = None
    for component, is_property in ((property_path, True), (orbital_path, False)):
        if component is None:
            continue
        part = QVFArchiveBuilder()
        try:
            data = _data(component)
            if is_property:
                add_properties(part, data)
                property_builder = part
            else:
                add_molecule(part, data)
                if property_builder is not None:
                    _require_matching_geometry(property_builder, part)
            _merge(builder, part, skip_structure=not is_property and property_builder is not None)
        except (ValueError, OSError) as exc:
            raise ValueError(f"Could not combine {component.name}: {exc}") from exc
        selected.append(component)
    hessian_path = _choose(hessian_paths, source)
    vcd_output = _vcd_output(key, source)
    if vcd_output is not None and hessian_path is None:
        raise ValueError("ORCA VCD output requires its matching .hess file")
    if hessian_path is not None:
        part = QVFArchiveBuilder()
        try:
            add_hessian(part, hessian_path.read_bytes())
            if vcd_output is not None:
                add_vcd_output(part, vcd_output[1])
            if selected:
                _match_hessian_geometry(builder, part)
            _merge(builder, part, skip_structure=bool(selected))
        except (ValueError, OSError) as exc:
            raise ValueError(f"Could not combine {hessian_path.name}: {exc}") from exc
        selected.append(hessian_path)
        if vcd_output is not None:
            selected.append(vcd_output[0])
    # Aggregate files contain the same orientation rows as their transition
    # family. Retain method/operator/field variants without duplicating rows.
    aggregate_names = {item.name for item in mcd_paths}
    standalone_mcd = len(mcd_paths) == 1 and not selected
    for mcd in mcd_paths:
        match = _MCD.fullmatch(mcd.name)
        if match and match["transition"] and mcd != source:
            aggregate = f"{match['base']}.{match['family']}.mcd"
            if aggregate in aggregate_names:
                continue
        try:
            orca_import._add_mcd(
                builder, mcd.read_bytes(),
                prefix="" if standalone_mcd else f"orca_mcd_{len(selected)}_",
                source_name=mcd.name,
            )
        except (ValueError, OSError) as exc:
            raise ValueError(f"Could not combine {mcd.name}: {exc}") from exc
        selected.append(mcd)
    builder.set_provenance({
        **builder.manifest.get("provenance", {}),
        "orca_calculation": {
            "basename": key.name,
            "files": [item.name for item in selected],
            "skipped_representations": [item.name for item in companions if item not in selected],
        },
    })
    return builder.to_bytesio()
