"""Shared pytest fixtures for vibe-view integration tests."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pytest


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(scope="session")
def sample_qvf() -> Path:
    """Build a realistic water molecule .qvf with structure + density.

    Session-scoped so all integration tests share the same archive.
    """
    structure_json = json.dumps(
        {
            "atoms": [
                {"symbol": "O", "position": [0.0, 0.0, 0.1173], "atomic_number": 8},
                {"symbol": "H", "position": [0.0, 0.7572, -0.4692], "atomic_number": 1},
                {"symbol": "H", "position": [0.0, -0.7572, -0.4692], "atomic_number": 1},
            ],
            "pbc": [False, False, False],
            "lattice_vectors": None,
        }
    ).encode()

    density_grid_json = json.dumps(
        {
            "origin": [-4.0, -4.0, -4.0],
            "voxel_vectors": [[0.2, 0, 0], [0, 0.2, 0], [0, 0, 0.2]],
            "shape": [40, 40, 40],
        }
    ).encode()
    density_data = np.random.randn(40, 40, 40).astype(np.float32) * 0.01
    density_data[20, 20, 21] = 1.0

    sections = [
        {
            "id": "structure",
            "kind": "structure",
            "members": {
                "structure": {
                    "path": "sections/structure.json",
                    "format": "json",
                    "sha256": _sha256(structure_json),
                }
            },
        },
        {
            "id": "density",
            "kind": "volume.density",
            "members": {
                "grid": {
                    "path": "sections/density.grid.json",
                    "format": "json",
                    "sha256": _sha256(density_grid_json),
                },
                "data": {
                    "path": "sections/density.dat",
                    "format": "binary",
                    "dtype": "float32",
                    "shape": [40, 40, 40],
                    "sha256": _sha256(density_data.tobytes()),
                },
            },
        },
    ]

    manifest = {
        "qvf_version": 1,
        "source": {
            "program": "vibe-qc",
            "version": "0.9.0",
            "calculation": "h2o_sample",
        },
        "sections": sections,
        "viewer_defaults": {
            "auto_open": ["density"],
            "density": {"isovalue": 0.05, "colormap": "viridis", "opacity": 0.6},
        },
    }

    files: dict[str, bytes] = {
        "sections/structure.json": structure_json,
        "sections/density.grid.json": density_grid_json,
        "sections/density.dat": density_data.tobytes(),
    }

    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for path, data in files.items():
            zf.writestr(path, data)

    qvf_path = Path(tmp.name)
    yield qvf_path
    qvf_path.unlink(missing_ok=True)


# ── synthesized stand-ins for the gitignored example archives ──────────
#
# `examples/vibe_view/**` is gitignored (.gitignore:139 and :157), so tests
# guarded on those archives *skipped* in CI and could only fail on a
# developer's machine after they had generated the showcase. A regression
# shipped through that hole on 2026-07-26. These fixtures synthesize
# equivalent archives in-process instead: no generated artifact, no vibeqc
# dependency, and the tests run everywhere.


def build_qvf(dest: Path, sections_data, *, extra=None, source=None) -> Path:
    """Write a .qvf from ``(section_id, kind, members)`` tuples.

    Member specs mirror ``tests/test_renderers.py``:
    ``(format, path, data)``, ``(format, path, data, dtype, shape)``, or an
    explicit dict (with optional ``_bytes`` payload) for members the strict
    schema forbids ``dtype``/``shape`` on, such as ``citations.references``.
    """
    sections: list[dict] = []
    files: dict[str, bytes] = {}

    for sid, kind, members in sections_data:
        sec_members: dict[str, dict] = {}
        for mname, spec in members.items():
            if isinstance(spec, dict):
                entry = dict(spec)
                payload = entry.pop("_bytes", None)
                sec_members[mname] = entry
                if payload is not None:
                    files[entry["path"]] = payload
                continue
            if len(spec) == 3:
                fmt, mpath, mdata = spec
                dtype = shape = None
            else:
                fmt, mpath, mdata, dtype, shape = spec
            entry = {
                "path": mpath,
                "format": fmt,
                "sha256": _sha256(mdata),
            }
            if fmt == "binary":
                entry["dtype"] = dtype or "uint8"
                entry["shape"] = (
                    list(shape) if shape is not None else [len(mdata)]
                )
            sec_members[mname] = entry
            files[mpath] = mdata
        section = {"id": sid, "kind": kind, "members": sec_members}
        if extra and sid in extra:
            section.update(extra[sid])
        sections.append(section)

    manifest = {
        "qvf_version": 1,
        "source": source
        or {
            "program": "vibe-qc",
            "version": "0.0.0-test",
            "calculation": "synthesized fixture",
        },
        "sections": sections,
    }
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for path, data in files.items():
            zf.writestr(path, data)
    return dest


def _water_structure_bytes() -> bytes:
    return json.dumps(
        {
            "atoms": [
                {"symbol": "O", "position": [0.0, 0.0, 0.1173],
                 "atomic_number": 8},
                {"symbol": "H", "position": [0.0, 0.7572, -0.4692],
                 "atomic_number": 1},
                {"symbol": "H", "position": [0.0, -0.7572, -0.4692],
                 "atomic_number": 1},
            ],
            "pbc": [False, False, False],
            "lattice_vectors": None,
        }
    ).encode()


@pytest.fixture(scope="session")
def water_qvf(tmp_path_factory) -> Path:
    """Minimal molecular archive: what the vq Job Manager tests need.

    Stands in for ``examples/vibe_view/runs/qvf_showcase/water.qvf``.
    """
    dest = tmp_path_factory.mktemp("water") / "water.qvf"

    # Section ids matter: the watcher / hot-reload tests activate
    # "scf_hist0" and "traj0" by name and assert on partial-section
    # marking, so the stand-in has to carry those ids, not just those kinds.
    iterations = json.dumps(
        {
            "iterations": [
                {"iter": 1, "energy_eh": -74.9, "delta_e": 1.0,
                 "diis_error": 1e-1},
                {"iter": 2, "energy_eh": -74.96, "delta_e": -0.06,
                 "diis_error": 1e-4},
            ]
        }
    ).encode()
    frames = 3
    coords = (
        np.tile(
            np.array(
                [
                    [0.0, 0.0, 0.1173],
                    [0.0, 0.7572, -0.4692],
                    [0.0, -0.7572, -0.4692],
                ],
                dtype=np.float64,
            ),
            (frames, 1, 1),
        )
        + np.arange(frames, dtype=np.float64)[:, None, None] * 0.01
    )
    # QVFReader.read_trajectory indexes meta["atoms"] (objects), not a
    # bare symbol list -- a symbols-only payload raises KeyError.
    traj_meta = json.dumps(
        {
            "atoms": [
                {"symbol": "O", "atomic_number": 8},
                {"symbol": "H", "atomic_number": 1},
                {"symbol": "H", "atomic_number": 1},
            ],
            "energies": [-74.90, -74.95, -74.96],
            "n_frames": frames,
        }
    ).encode()

    return build_qvf(
        dest,
        [
            (
                "structure",
                "structure",
                {
                    "structure": (
                        "json",
                        "sections/structure.json",
                        _water_structure_bytes(),
                    )
                },
            ),
            (
                "scf_hist0",
                "scf_history",
                {"iterations": ("json", "analysis/scf.json", iterations)},
            ),
            (
                "traj0",
                "trajectory",
                {
                    "metadata": ("json", "traj/meta.json", traj_meta),
                    "coords": (
                        "binary", "traj/coords.bin",
                        coords.tobytes(), "float64", [frames, 3, 3],
                    ),
                },
            ),
        ],
    )


@pytest.fixture(scope="session")
def showcase_qvf(tmp_path_factory) -> Path:
    """Multi-section archive for the controller smoke tests.

    Stands in for ``examples/vibe_view/output-nacl-showcase.qvf``. Carries a
    3-D viewport kind (``volume.density``), an ``atom_properties`` overlay,
    and two 2-D panel kinds, which together exercise the activate_section
    dispatch and the panel-switch scene push.
    """
    dest = tmp_path_factory.mktemp("showcase") / "showcase.qvf"

    grid = json.dumps(
        {
            "origin": [-2.0, -2.0, -2.0],
            "voxel_vectors": [[0.5, 0, 0], [0, 0.5, 0], [0, 0, 0.5]],
            "shape": [8, 8, 8],
        }
    ).encode()
    density = (
        np.abs(np.random.default_rng(0).standard_normal((8, 8, 8)))
        .astype(np.float32)
        .tobytes()
    )
    charges = np.array([-0.68, 0.34, 0.34], dtype=np.float64).tobytes()
    bib = b"@article{demo2026, title={Demo}, author={A}, year={2026}}\n"
    iterations = json.dumps(
        {
            "iterations": [
                {"iter": 1, "energy_eh": -74.9, "delta_e": 1.0,
                 "diis_error": 1e-1},
                {"iter": 2, "energy_eh": -74.96, "delta_e": -0.06,
                 "diis_error": 1e-4},
            ]
        }
    ).encode()

    return build_qvf(
        dest,
        [
            (
                "structure",
                "structure",
                {
                    "structure": (
                        "json",
                        "sections/structure.json",
                        json.dumps(
                            {
                                "atoms": [
                                    {"symbol": "Na", "atomic_number": 11,
                                     "position": [0.0, 0.0, 0.0]},
                                    {"symbol": "Cl", "atomic_number": 17,
                                     "position": [2.82, 0.0, 0.0]},
                                ],
                                # Periodic on every axis: this stands in for
                                # the NaCl showcase, and the reload test
                                # asserts is_periodic survives a rebuild.
                                "pbc": [True, True, True],
                                "lattice_vectors": [
                                    [5.64, 0.0, 0.0],
                                    [0.0, 5.64, 0.0],
                                    [0.0, 0.0, 5.64],
                                ],
                                "dimensionality": 3,
                            }
                        ).encode(),
                    )
                },
            ),
            (
                "vol_dens_0",
                "volume.density",
                {
                    "grid": ("json", "volumes/density_grid.json", grid),
                    "data": (
                        "binary", "volumes/density.dat", density,
                        "float32", [8, 8, 8],
                    ),
                },
            ),
            (
                "atom_props",
                "atom_properties",
                {
                    "mulliken_charge": (
                        "binary", "analysis/mulliken.bin", charges,
                        "float64", [3],
                    )
                },
            ),
            (
                "scf_history",
                "scf_history",
                {"iterations": ("json", "analysis/scf.json", iterations)},
            ),
            (
                "citations",
                "citations",
                {
                    "references": {
                        "path": "citations/references.bib",
                        "format": "binary",
                        "sha256": _sha256(bib),
                        "_bytes": bib,
                    }
                },
            ),
        ],
    )


@pytest.fixture(scope="session")
def tables_qvf(tmp_path_factory) -> Path:
    """Four-atom archive with the kinds the table/CLI tests extract.

    Stands in for ``examples/vibe_view/runs/h2co_showcase/h2co.qvf`` and the
    water archive: ``vibrations``, ``atom_properties`` carrying *both*
    Mulliken and Loewdin charges, and a minimal ``wavefunction.gto`` with
    energies + occupations.
    """
    dest = tmp_path_factory.mktemp("tables") / "tables.qvf"

    # H2CO: four atoms, so the atom_properties table has four rows.
    structure = json.dumps(
        {
            "atoms": [
                {"symbol": "C", "atomic_number": 6,
                 "position": [0.0, 0.0, 0.0]},
                {"symbol": "O", "atomic_number": 8,
                 "position": [0.0, 0.0, 1.21]},
                {"symbol": "H", "atomic_number": 1,
                 "position": [0.0, 0.94, -0.54]},
                {"symbol": "H", "atomic_number": 1,
                 "position": [0.0, -0.94, -0.54]},
            ],
            "pbc": [False, False, False],
            "lattice_vectors": None,
        }
    ).encode()

    n_modes, n_atoms = 6, 4
    # read_vibrations builds Atom objects from these entries, so each needs
    # a position as well as the symbol/Z (a symbol-only payload raises
    # KeyError: 'position').
    vib_meta = json.dumps(
        {
            "atoms": [
                {"symbol": "C", "atomic_number": 6,
                 "position": [0.0, 0.0, 0.0]},
                {"symbol": "O", "atomic_number": 8,
                 "position": [0.0, 0.0, 1.21]},
                {"symbol": "H", "atomic_number": 1,
                 "position": [0.0, 0.94, -0.54]},
                {"symbol": "H", "atomic_number": 1,
                 "position": [0.0, -0.94, -0.54]},
            ],
            "frequencies": [1180.0, 1265.0, 1530.0, 1780.0, 2900.0, 2960.0],
        }
    ).encode()
    displacements = (
        np.random.default_rng(1)
        .standard_normal((n_modes, n_atoms, 3))
        .astype(np.float64)
        .tobytes()
    )

    mulliken = np.array([0.12, -0.38, 0.13, 0.13], dtype=np.float64).tobytes()
    loewdin = np.array([0.10, -0.34, 0.12, 0.12], dtype=np.float64).tobytes()

    wf_basis = json.dumps(
        {
            "structure_ref": "structure",
            "pure": True,
            "n_ao": 1,
            "shells": [
                {"center": 0, "l": 0, "exponents": [1.0],
                 "coefficients": [1.0]}
            ],
        }
    ).encode()
    wf_meta = json.dumps(
        {
            "n_mo": 1,
            "n_ao": 1,
            "spin": "restricted",
            "orbital_kind": "canonical",
            "energies": [-0.5],
            "occupations": [2.0],
        }
    ).encode()
    wf_coeffs = np.array([[1.0]], dtype=np.float64).tobytes()

    return build_qvf(
        dest,
        [
            ("structure", "structure",
             {"structure": ("json", "sections/structure.json", structure)}),
            (
                "vib0",
                "vibrations",
                {
                    "metadata": ("json", "vib/meta.json", vib_meta),
                    "displacements": (
                        "binary", "vib/disp.bin", displacements,
                        "float64", [n_modes, n_atoms, 3],
                    ),
                },
            ),
            (
                "atom_props",
                "atom_properties",
                {
                    "mulliken_charge": (
                        "binary", "analysis/mulliken.bin", mulliken,
                        "float64", [n_atoms],
                    ),
                    "loewdin_charge": (
                        "binary", "analysis/loewdin.bin", loewdin,
                        "float64", [n_atoms],
                    ),
                },
            ),
            (
                "wf",
                "wavefunction.gto",
                {
                    "basis": ("json", "wf/basis.json", wf_basis),
                    "mo_metadata": ("json", "wf/mo.json", wf_meta),
                    "mo_coefficients": (
                        "binary", "wf/mo.dat", wf_coeffs, "float64", [1, 1],
                    ),
                },
            ),
        ],
    )


@pytest.fixture(scope="session")
def reaction_qvf(tmp_path_factory) -> Path:
    """Molecular reaction.path archive for the reaction-video test.

    Stands in for ``examples/vibe_view/runs/neb_reaction/neb_h3.qvf``: a
    linear H3 transfer, five frames, with reactant / TS / product
    waypoints so the video renderer has labels to transition between.
    """
    dest = tmp_path_factory.mktemp("reaction") / "reaction.qvf"

    frames = 5
    coords = np.zeros((frames, 3, 3), dtype=np.float64)
    for i in range(frames):
        shift = 0.9 * i / (frames - 1)
        coords[i, 0] = [0.0, 0.0, 0.0]
        coords[i, 1] = [0.9 + shift, 0.0, 0.0]  # the transferred H
        coords[i, 2] = [2.7, 0.0, 0.0]

    meta = json.dumps(
        {
            "atoms": [
                {"symbol": "H", "atomic_number": 1},
                {"symbol": "H", "atomic_number": 1},
                {"symbol": "H", "atomic_number": 1},
            ],
            "energies": [0.0, 0.011, 0.019, 0.010, -0.001],
            "reaction_coordinate": [0.0, 0.25, 0.5, 0.75, 1.0],
            "waypoints": [
                {"frame_index": 0, "label": "Reactant", "kind": "reactant"},
                {"frame_index": 2, "label": "TS", "kind": "transition_state"},
                {"frame_index": 4, "label": "Product", "kind": "product"},
            ],
        }
    ).encode()

    return build_qvf(
        dest,
        [
            (
                "structure",
                "structure",
                {
                    "structure": (
                        "json",
                        "sections/structure.json",
                        json.dumps(
                            {
                                "atoms": [
                                    {"symbol": "H", "atomic_number": 1,
                                     "position": [0.0, 0.0, 0.0]},
                                    {"symbol": "H", "atomic_number": 1,
                                     "position": [0.9, 0.0, 0.0]},
                                    {"symbol": "H", "atomic_number": 1,
                                     "position": [2.7, 0.0, 0.0]},
                                ],
                                "pbc": [False, False, False],
                                "lattice_vectors": None,
                            }
                        ).encode(),
                    )
                },
            ),
            (
                "rxn0",
                "reaction.path",
                {
                    "metadata": ("json", "reaction/meta.json", meta),
                    "coords": (
                        "binary", "reaction/coords.bin", coords.tobytes(),
                        "float64", [frames, 3, 3],
                    ),
                },
            ),
        ],
    )
