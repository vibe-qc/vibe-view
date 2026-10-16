"""Shared pytest fixtures for vibe-view integration tests."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pytest

from tests._qvf_builders import build_qvf


@pytest.fixture(scope="session", autouse=True)
def _isolate_the_run(tmp_path_factory):
    """Keep the suite out of the real account and out of the system temp dir.

    * ``tempfile`` writes under pytest's own temporary tree, which pytest
      prunes after a few runs. A ``NamedTemporaryFile(delete=False)`` or
      ``mkdtemp()`` a test forgets to remove no longer piles up in the system
      temp directory (#41-27).
    * ``XDG_CACHE_HOME`` and ``XDG_CONFIG_HOME`` point at scratch
      directories, so settings, recent files and caches a test writes never
      reach the ``~/.cache`` and ``~/.config`` of whoever runs it (#41-28).
      A test that needs the fallback removes them with ``monkeypatch``.
    """
    import os

    root = tmp_path_factory.mktemp("isolated")
    saved_tempdir = tempfile.tempdir
    saved_env = {name: os.environ.get(name) for name in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME")}
    (root / "tmp").mkdir()
    tempfile.tempdir = str(root / "tmp")
    os.environ["XDG_CACHE_HOME"] = str(root / "cache")
    os.environ["XDG_CONFIG_HOME"] = str(root / "config")
    try:
        yield
    finally:
        tempfile.tempdir = saved_tempdir
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


_DATA = Path(__file__).parent / "data"


@pytest.fixture(autouse=True)
def _keep_tests_data_clean():
    """Fail a test that leaves new files under tests/data.

    The directory holds committed fixtures. A browser export writes next to
    the archive it came from, so a test that opens a fixture in place and
    exports leaves a stray file behind, which then gets committed. Listing a
    dozen paths per test is cheap; the strays are removed so they do not
    outlive the failure.
    """
    before = set(_DATA.rglob("*"))
    yield
    added = sorted(set(_DATA.rglob("*")) - before)
    if added:
        for path in added:
            if path.is_file():
                path.unlink()
        names = ", ".join(str(path.relative_to(_DATA)) for path in added)
        pytest.fail(
            f"test left new files in tests/data: {names}. Copy the fixture "
            "into tmp_path before anything writes next to it.",
            pytrace=False,
        )


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

    with (
        tempfile.NamedTemporaryFile(suffix=".qvf", delete=False) as tmp,
        zipfile.ZipFile(tmp, "w") as zf,
    ):
        zf.writestr("manifest.json", json.dumps(manifest))
        for path, data in files.items():
            zf.writestr(path, data)

    qvf_path = Path(tmp.name)
    yield qvf_path
    qvf_path.unlink(missing_ok=True)


# ── synthesized stand-ins for the gitignored example archives ──────────
#
# The generated showcase archives were never tracked (in the monorepo they
# were gitignored), so tests guarded on those archives *skipped* in CI and
# could only fail on a developer's machine after they had generated them. A regression
# shipped through that hole on 2026-07-26. These fixtures synthesize
# equivalent archives in-process instead: no generated artifact, no vibeqc
# dependency, and the tests run everywhere.




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
