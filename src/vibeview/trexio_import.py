"""Import TREXIO geometry and real molecular Gaussian orbitals into QVF.

The optional official Python API handles both HDF5 and text backends. See
https://trex-coe.github.io/trexio/trex.html for the stored AO conventions.
TREXIO's explicit primitive, shell and AO factors are preserved; they must
not be replaced with a guess based on the name of the producing program.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import tempfile
import zipfile
from pathlib import Path
from typing import IO, Any

import numpy as np

_BOHR_TO_ANGSTROM = 0.529177210903
TREXIO_EXTENSIONS = (".trexio", ".h5", ".hdf5")


def is_trexio_directory(path: str | Path) -> bool:
    """Recognize a text-backend dataset without loading the optional library."""
    path = Path(path)
    try:
        return (path / "nucleus.txt").is_file() and (path / "metadata.txt").is_file()
    except OSError:
        # Probing one unreadable directory must not abort a results scan.
        return False


class _Dataset:
    def __init__(self, api: Any, handle: Any) -> None:
        self.api = api
        self.handle = handle

    def has(self, name: str) -> bool:
        probe = getattr(self.api, f"has_{name}", None)
        return probe is not None and bool(probe(self.handle))

    def read(self, name: str) -> Any:
        if not self.has(name):
            raise ValueError(f"TREXIO is missing required field {name.replace('_', '.', 1)}")
        return getattr(self.api, f"read_{name}")(self.handle)

    def optional(self, name: str, default: Any = None) -> Any:
        return self.read(name) if self.has(name) else default

    def array(self, name: str, shape: tuple[int, ...], *, integer: bool = False) -> np.ndarray:
        value = np.asarray(self.read(name))
        if value.shape != shape or not np.all(np.isfinite(value)):
            raise ValueError(f"TREXIO {name} must contain finite values with shape {shape}")
        if integer and not np.all(value == np.rint(value)):
            raise ValueError(f"TREXIO {name} must contain integers")
        return value.astype(np.int64 if integer else np.float64)

    def count(self, name: str) -> int:
        value = int(self.read(name))
        if value <= 0:
            raise ValueError(f"TREXIO {name} must be positive")
        return value


def _structure(data: _Dataset) -> dict:
    from vibeview.converters import _SYMBOL_TO_Z, _Z_TO_SYMBOL

    count = data.count("nucleus_num")
    positions = data.array("nucleus_coord", (count, 3)) * _BOHR_TO_ANGSTROM
    charges = data.array("nucleus_charge", (count,)) if data.has("nucleus_charge") else None
    core = (
        data.array("ecp_z_core", (count,), integer=True)
        if data.has("ecp_z_core")
        else np.zeros(count, dtype=np.int64)
    )
    if np.any(core < 0):
        raise ValueError("TREXIO ecp_z_core must be nonnegative")
    labels = data.optional("nucleus_label", [""] * count)
    if len(labels) != count:
        raise ValueError("TREXIO nucleus_label does not match nucleus_num")
    atoms = []
    for index, label in enumerate(labels):
        # Labels can be C1, Cl2, etc. Prefer the chemical label to an ECP's
        # effective nuclear charge, which need not be the atomic number.
        match = re.fullmatch(r"([A-Za-z]{1,2})(?:[\d_].*)?", label.strip())
        symbol = match[1].capitalize() if match else ""
        number = _SYMBOL_TO_Z.get(symbol)
        if number is None:
            charge = float(charges[index]) if charges is not None else None
            if charge is None or charge < 0 or not charge.is_integer():
                raise ValueError(f"TREXIO nucleus {index} has no identifiable element")
            number = int(charge) + int(core[index])
            symbol = _Z_TO_SYMBOL.get(number, "X")
        atoms.append(
            {
                "symbol": symbol,
                "atomic_number": number,
                "position": positions[index].tolist(),
            }
        )

    periodic = data.optional("pbc_periodic", 0)
    if periodic not in (0, 1):
        raise ValueError("TREXIO pbc_periodic must be 0 or 1")
    structure = {"atoms": atoms, "pbc": [bool(periodic)] * 3}
    cell_fields = [data.has(f"cell_{axis}") for axis in "abc"]
    if any(cell_fields) or periodic:
        cell = np.stack([data.array(f"cell_{axis}", (3,)) for axis in "abc"])
        if periodic and abs(float(np.linalg.det(cell))) < 1e-12:
            raise ValueError("TREXIO periodic cell must be nonsingular")
        structure["lattice_vectors"] = (cell * _BOHR_TO_ANGSTROM).tolist()
        structure["dimensionality"] = 3 if periodic else 0
    return structure


def _wavefunction(data: _Dataset, atom_count: int, periodic: bool) -> tuple[dict, dict, dict]:
    if periodic or data.has("mo_k_point") or data.has("pbc_k_point"):
        raise ValueError("TREXIO periodic/k-point orbitals are not supported; export a cube volume")
    if data.read("basis_type").lower() != "gaussian":
        raise ValueError("TREXIO orbital import requires a Gaussian basis; export a cube volume")
    for name in (
        "mo_coefficient_im",
        "basis_coefficient_im",
        "basis_exponent_im",
        "basis_r_power",
        "basis_oscillation_arg",
    ):
        if data.has(name) and np.any(np.asarray(data.read(name)) != 0):
            raise ValueError(f"TREXIO {name} is not supported for real Gaussian orbitals")

    n_shell = data.count("basis_shell_num")
    n_prim = data.count("basis_prim_num")
    n_ao = data.count("ao_num")
    n_mo = data.count("mo_num")
    centers = data.array("basis_nucleus_index", (n_shell,), integer=True)
    angular = data.array("basis_shell_ang_mom", (n_shell,), integer=True)
    shell_index = data.array("basis_shell_index", (n_prim,), integer=True)
    ao_shell = data.array("ao_shell", (n_ao,), integer=True)
    for name, indices, limit in (
        ("basis_nucleus_index", centers, atom_count),
        ("basis_shell_index", shell_index, n_shell),
        ("ao_shell", ao_shell, n_shell),
    ):
        if np.any((indices < 0) | (indices >= limit)):
            raise ValueError(f"TREXIO {name} contains an out-of-range index")
    if np.any((angular < 0) | (angular > 3)):
        raise ValueError("TREXIO orbital rendering supports s, p, d and f shells (l <= 3)")
    cartesian = data.read("ao_cartesian")
    if cartesian not in (0, 1):
        raise ValueError("TREXIO ao_cartesian must be 0 or 1")
    pure = not bool(cartesian)
    exponents = data.array("basis_exponent", (n_prim,))
    if np.any(exponents <= 0):
        raise ValueError("TREXIO Gaussian exponents must be positive")
    coefficients = data.array("basis_coefficient", (n_prim,))
    prim_factor = data.array("basis_prim_factor", (n_prim,))
    shell_factor = data.array("basis_shell_factor", (n_shell,))
    ao_factor = data.array("ao_normalization", (n_ao,))
    mo_coefficients = data.array("mo_coefficient", (n_mo, n_ao))

    shells = []
    column_order = []
    for shell, momentum in enumerate(angular):
        ell = int(momentum)
        primitives = np.flatnonzero(shell_index == shell)
        aos = np.flatnonzero(ao_shell == shell)
        expected = 2 * ell + 1 if pure else (ell + 1) * (ell + 2) // 2
        if len(primitives) == 0 or len(aos) != expected:
            raise ValueError(f"TREXIO shell {shell} needs primitives and {expected} AOs")
        alpha = exponents[primitives]
        # QVF A.1 multiplies each primitive by N(l, alpha) during evaluation.
        # Undo only that implicit factor. Leave the TREXIO contraction as
        # stored, including any non-unit normalization of the contracted AO.
        norm = (2 * alpha / math.pi) ** 0.75 * np.sqrt(
            (8 * alpha) ** ell * math.factorial(ell) / math.factorial(2 * ell)
        )
        contraction = (
            coefficients[primitives] * prim_factor[primitives] * shell_factor[shell] / norm
        )
        shells.append(
            {
                "center": int(centers[shell]),
                "l": ell,
                "pure": pure,
                "exponents": alpha.tolist(),
                "coefficients": contraction.tolist(),
            }
        )
        if pure:
            # TREXIO: 0,+1,-1,+2,-2,...; QVF: -l,...,0,...,+l.
            order = [2 * abs(m) - (1 if m > 0 else 0) for m in range(-ell, ell + 1)]
            aos = aos[order]
        column_order.extend(aos.tolist())
    # AO-specific normalization cannot live in a QVF shell; absorb it into
    # the corresponding MO column, then gather shells in QVF basis order.
    converted = (mo_coefficients * ao_factor)[..., column_order]
    if not np.all(np.isfinite(converted)) or any(
        not np.all(np.isfinite(shell["coefficients"])) for shell in shells
    ):
        raise ValueError("TREXIO normalization produced non-finite orbital coefficients")

    mo_type = str(data.optional("mo_type", "canonical")).lower()
    kind = "natural" if "natural" in mo_type else "localized" if "local" in mo_type else "canonical"
    metadata: dict = {"n_ao": n_ao, "orbital_kind": kind}
    if kind == "natural":
        metadata["occupation_semantics"] = "electron_occupation"
    values = {}
    for field, key in (("mo_energy", "energies"), ("mo_occupation", "occupations")):
        if data.has(field):
            values[key] = data.array(field, (n_mo,))
    if "occupations" in values and np.any(values["occupations"] < 0):
        raise ValueError("TREXIO MO occupations must be nonnegative")
    if data.has("mo_symmetry"):
        labels = np.asarray(data.read("mo_symmetry"))
        if labels.shape != (n_mo,):
            raise ValueError("TREXIO mo_symmetry does not match mo_num")
        values["symmetry_labels"] = labels
    spin = data.array("mo_spin", (n_mo,), integer=True) if data.has("mo_spin") else None
    if spin is not None and np.any((spin != 0) & (spin != 1)):
        raise ValueError("TREXIO mo_spin must contain only 0 (alpha) and 1 (beta)")
    matrices = {}
    if spin is not None and np.any(spin == 1):
        if not np.any(spin == 0):
            raise ValueError("TREXIO unrestricted orbitals require an alpha block")
        metadata["spin"] = "unrestricted"
        for index, label in enumerate(("alpha", "beta")):
            mask = spin == index
            metadata[label] = {key: value[mask].tolist() for key, value in values.items()}
            matrices[f"mo_coefficients_{label}"] = converted[mask]
    else:
        metadata["spin"] = "restricted"
        metadata.update({key: value.tolist() for key, value in values.items()})
        matrices["mo_coefficients"] = converted
    basis = {"structure_ref": "structure", "pure": pure, "n_ao": n_ao, "shells": shells}
    return basis, metadata, matrices


def _convert_path(path: Path, api: Any) -> io.BytesIO:
    members: dict[str, bytes] = {}
    sections = []

    def add_section(section_id: str, kind: str, payloads: dict) -> None:
        descriptions = {}
        for name, value in payloads.items():
            binary = isinstance(value, np.ndarray)
            member_path = f"sections/{section_id}/{name}.{'bin' if binary else 'json'}"
            payload = (
                np.asarray(value, dtype="<f8").tobytes(order="C")
                if binary
                else json.dumps(value, allow_nan=False).encode()
            )
            description = {
                "path": member_path,
                "format": "binary" if binary else "json",
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            if binary:
                description.update(dtype="float64", shape=list(value.shape))
            descriptions[name] = description
            members[member_path] = payload
        sections.append({"id": section_id, "kind": kind, "members": descriptions})

    # Read-only mode also prevents the library from adding metadata to input.
    with api.File(str(path), mode="r", back_end=api.TREXIO_AUTO) as handle:
        data = _Dataset(api, handle)
        structure = _structure(data)
        add_section("structure", "structure", {"structure": structure})
        if data.has("mo_coefficient"):
            basis, metadata, matrices = _wavefunction(
                data, len(structure["atoms"]), any(structure["pbc"])
            )
            add_section(
                "wavefunction",
                "wavefunction.gto",
                {
                    "basis": basis,
                    "mo_metadata": metadata,
                    **matrices,
                },
            )
        provenance = {}
        if data.has("electron_num"):
            provenance["n_electrons"] = int(data.read("electron_num"))
        if data.has("nucleus_point_group"):
            provenance["point_group"] = data.read("nucleus_point_group")
        manifest = {
            "qvf_version": 1,
            "source": {
                "program": "TREXIO",
                "version": str(api.__version__),
                "calculation": path.stem,
            },
            "sections": sections,
            "provenance": provenance,
        }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, allow_nan=False))
        for name, payload in members.items():
            archive.writestr(name, payload)
    buffer.seek(0)
    return buffer


def trexio_to_qvf(source: str | Path | IO[bytes] | bytes) -> io.BytesIO:
    """Convert a TREXIO HDF5 file/upload or text directory to a QVF archive.

    Geometry-only files need no basis. When MO coefficients are present,
    import requires a complete real molecular Gaussian basis with l <= 3.
    Energies and occupations remain absent when the source omits them.
    """
    try:
        import trexio
    except ImportError:
        from vibeview.install_hints import install_hint

        raise ValueError(
            "TREXIO import needs the optional trexio extra. "
            f"Install it with: {install_hint('trexio')}"
        ) from None

    try:
        if isinstance(source, (str, Path)):
            return _convert_path(Path(source), trexio)
        raw = source if isinstance(source, bytes) else source.read()
        # TREXIO's HDF5 API requires a filesystem path. Close the temporary
        # upload before opening it so this works on Windows too.
        with tempfile.TemporaryDirectory(prefix="vibeview-trexio-") as directory:
            path = Path(directory) / "uploaded.hdf5"
            path.write_bytes(raw)
            return _convert_path(path, trexio)
    except (trexio.Error, OSError) as exc:
        raise ValueError(f"Could not read TREXIO input: {exc}") from exc
