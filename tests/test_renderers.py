"""Tests for renderer dispatch and individual renderers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from tests._qvf_builders import build_qvf, vibe_qc_source
from vibeview.kinds import SUPPORTED_KINDS
from vibeview.qvf import QVFReader
from vibeview.renderers import get_renderer


def _make_qvf_with_sections(
    sections_data: list[tuple[str, str, dict]],
    *,
    extra: dict[str, dict] | None = None,
) -> Path:
    """A .qvf from ``(section_id, kind, members)`` tuples, in a temporary file.

    Member specs are those of :func:`tests._qvf_builders.build_qvf`; ``extra``
    is keyed by section id and merged into each section dict (used for
    ``operand_a``/``operand_b``, ``trajectory_ref``, etc.).
    """
    return build_qvf(None, sections_data, extra=extra, source=vibe_qc_source("0.9.0", "test"))


class TestRendererDispatch:
    def test_structure_dispatches(self) -> None:
        atoms = json.dumps(
            {
                "atoms": [{"symbol": "H", "position": [0, 0, 0], "atomic_number": 1}],
                "pbc": [False, False, False],
            }
        ).encode()
        path = _make_qvf_with_sections(
            [
                (
                    "structure",
                    "structure",
                    {
                        "structure": ("json", "sections/structure.json", atoms),
                    },
                ),
            ]
        )
        try:
            reader = QVFReader(path)
            section = reader.get_section("structure")
            renderer = get_renderer(section, reader)
            assert renderer is not None
            assert renderer.kind == "structure"
        finally:
            path.unlink()

    def test_volume_density_dispatches_to_volume(self) -> None:
        grid = json.dumps(
            {
                "origin": [0, 0, 0],
                "voxel_vectors": [[0.2, 0, 0], [0, 0.2, 0], [0, 0, 0.2]],
                "shape": [10, 10, 10],
            }
        ).encode()
        data = np.zeros((10, 10, 10), dtype=np.float32).tobytes()
        path = _make_qvf_with_sections(
            [
                (
                    "density",
                    "volume.density",
                    {
                        "grid": ("json", "sections/density.grid.json", grid),
                        "data": (
                            "binary",
                            "sections/density.dat",
                            data,
                            "float32",
                            [10, 10, 10],
                        ),
                    },
                ),
            ]
        )
        try:
            reader = QVFReader(path)
            section = reader.get_section("density")
            renderer = get_renderer(section, reader)
            assert renderer is not None
            assert renderer.kind == "volume.density"
        finally:
            path.unlink()

    def test_unsupported_kind_returns_none(self) -> None:
        # get_renderer returns None for any kind not in SUPPORTED_KINDS.
        # Vendor namespaces (x_<vendor>.*) always reach this path. Deferred
        # kinds would too, but as of 2026-06 DEFERRED_KINDS is empty (all six
        # promoted kinds now render), so this case exercises a vendor section.
        path = _make_qvf_with_sections(
            [("vendor_sec", "x_acme.thing", {})],
        )
        try:
            reader = QVFReader(path)
            section = reader.get_section("vendor_sec")
            renderer = get_renderer(section, reader)
            assert renderer is None
        finally:
            path.unlink()

    def test_vendor_namespace_returns_none(self) -> None:
        path = _make_qvf_with_sections(
            [
                ("vendor_ext", "x_orca.custom", {}),
            ]
        )
        try:
            reader = QVFReader(path)
            section = reader.get_section("vendor_ext")
            renderer = get_renderer(section, reader)
            assert renderer is None
        finally:
            path.unlink()

    def test_all_supported_kinds_have_renderers(self) -> None:
        """Every kind in SUPPORTED_KINDS must dispatch to a non-None renderer.

        We build a minimal valid section for each kind and verify dispatch.
        """
        from vibeview.renderers import get_renderer

        # Minimal data per kind
        kind_data: dict[str, tuple[str, dict]] = {
            "structure": (
                "structure",
                {
                    "structure": (
                        "json",
                        "sections/structure.json",
                        json.dumps(
                            {
                                "atoms": [
                                    {
                                        "symbol": "H",
                                        "position": [0, 0, 0],
                                        "atomic_number": 1,
                                    }
                                ],
                            }
                        ).encode(),
                    ),
                },
            ),
            "volume.density": (
                "vol_d",
                {
                    "grid": (
                        "json",
                        "sections/vol_d.grid.json",
                        json.dumps(
                            {
                                "origin": [0, 0, 0],
                                "voxel_vectors": [
                                    [0.2, 0, 0],
                                    [0, 0.2, 0],
                                    [0, 0, 0.2],
                                ],
                                "shape": [5, 5, 5],
                            }
                        ).encode(),
                    ),
                    "data": (
                        "binary",
                        "sections/vol_d.dat",
                        np.zeros((5, 5, 5), dtype=np.float32).tobytes(),
                        "float32",
                        [5, 5, 5],
                    ),
                },
            ),
            "basis.ao": (
                "ao0",
                {
                    "grid": (
                        "json",
                        "sections/ao0.grid.json",
                        json.dumps(
                            {
                                "origin": [0, 0, 0],
                                "voxel_vectors": [
                                    [0.2, 0, 0],
                                    [0, 0.2, 0],
                                    [0, 0, 0.2],
                                ],
                                "shape": [5, 5, 5],
                            }
                        ).encode(),
                    ),
                    "data": (
                        "binary",
                        "sections/ao0.dat",
                        np.zeros((5, 5, 5), dtype=np.float32).tobytes(),
                        "float32",
                        [5, 5, 5],
                    ),
                },
            ),
            "volume.orbital": (
                "vol_o",
                {
                    "grid": (
                        "json",
                        "sections/vol_o.grid.json",
                        json.dumps(
                            {
                                "origin": [0, 0, 0],
                                "voxel_vectors": [
                                    [0.2, 0, 0],
                                    [0, 0.2, 0],
                                    [0, 0, 0.2],
                                ],
                                "shape": [5, 5, 5],
                            }
                        ).encode(),
                    ),
                    "data": (
                        "binary",
                        "sections/vol_o.dat",
                        np.zeros((5, 5, 5), dtype=np.float32).tobytes(),
                        "float32",
                        [5, 5, 5],
                    ),
                },
            ),
            "bands": (
                "bands",
                {
                    "kpath": (
                        "json",
                        "sections/kpath.json",
                        json.dumps({"segments": [], "fermi": 0.0}).encode(),
                    ),
                    "eigenvalues": (
                        "binary",
                        "sections/eigenvalues.dat",
                        np.zeros((1, 3, 5), dtype=np.float64).tobytes(),
                        "float64",
                        [1, 3, 5],
                    ),
                },
            ),
            "spectra.ir": (
                "ir",
                {
                    "spectrum": (
                        "json",
                        "sections/ir_spectrum.json",
                        json.dumps(
                            {
                                "frequencies": [100.0, 200.0],
                                "intensities": [1.0, 2.0],
                            }
                        ).encode(),
                    ),
                },
            ),
            "spectra.uvvis": (
                "uv",
                {
                    "spectrum": (
                        "json",
                        "sections/uvvis_spectrum.json",
                        json.dumps(
                            {
                                "frequencies": [3.5, 4.2, 5.8],
                                "intensities": [0.1, 0.4, 0.05],
                            }
                        ).encode(),
                    ),
                },
            ),
            "spectra.raman": (
                "raman",
                {
                    "spectrum": (
                        "json",
                        "sections/raman_spectrum.json",
                        json.dumps(
                            {
                                "frequencies": [800.0, 1200.0, 3100.0],
                                "intensities": [10.0, 50.0, 20.0],
                            }
                        ).encode(),
                    ),
                },
            ),
            "spectra.ecd": (
                "ecd",
                {
                    "spectrum": (
                        "json",
                        "sections/ecd_spectrum.json",
                        json.dumps(
                            {
                                "frequencies": [3.5, 4.2],
                                "intensities": [-0.2, 0.4],
                            }
                        ).encode(),
                    ),
                },
            ),
            "spectra.vcd": (
                "vcd",
                {
                    "spectrum": (
                        "json",
                        "sections/vcd_spectrum.json",
                        json.dumps(
                            {
                                "frequencies": [1000.0, 1500.0],
                                "intensities": [-0.05, 0.10],
                            }
                        ).encode(),
                    ),
                },
            ),
            "spectra.generic": (
                "gen",
                {
                    "spectrum": (
                        "json",
                        "sections/gen_spectrum.json",
                        json.dumps(
                            {
                                "frequencies": [1.0, 2.0, 3.0],
                                "intensities": [0.5, 1.0, 0.5],
                            }
                        ).encode(),
                    ),
                },
            ),
            "trajectory": (
                "traj",
                {
                    "metadata": (
                        "json",
                        "sections/trajectory.json",
                        json.dumps(
                            {
                                "atoms": [{"symbol": "H", "atomic_number": 1}],
                                "energies": [0.0, -1.0],
                            }
                        ).encode(),
                    ),
                    "coords": (
                        "binary",
                        "sections/traj_coords.dat",
                        np.zeros((2, 1, 3), dtype=np.float64).tobytes(),
                        "float64",
                        [2, 1, 3],
                    ),
                },
            ),
            "vibrations": (
                "vib",
                {
                    "metadata": (
                        "json",
                        "sections/vib_modes.json",
                        json.dumps(
                            {
                                "atoms": [
                                    {
                                        "symbol": "H",
                                        "position": [0, 0, 0],
                                        "atomic_number": 1,
                                    },
                                ],
                                "frequencies": [100.0],
                            }
                        ).encode(),
                    ),
                    "displacements": (
                        "binary",
                        "sections/vib_displacements.dat",
                        np.zeros((1, 1, 3), dtype=np.float64).tobytes(),
                        "float64",
                        [1, 1, 3],
                    ),
                },
            ),
            "atom_properties": (
                "props",
                {
                    "mulliken_charge": (
                        "binary",
                        "sections/mulliken.bin",
                        np.array([-0.5, 0.25, 0.25], dtype=np.float64).tobytes(),
                        "float64",
                        [3],
                    ),
                },
            ),
            "volume.spin": (
                "vol_s",
                {
                    "grid": (
                        "json",
                        "sections/vol_s.grid.json",
                        json.dumps(
                            {
                                "origin": [0, 0, 0],
                                "voxel_vectors": [
                                    [0.2, 0, 0],
                                    [0, 0.2, 0],
                                    [0, 0, 0.2],
                                ],
                                "shape": [5, 5, 5],
                            }
                        ).encode(),
                    ),
                    "data": (
                        "binary",
                        "sections/vol_s.dat",
                        np.zeros((5, 5, 5), dtype=np.float32).tobytes(),
                        "float32",
                        [5, 5, 5],
                    ),
                },
            ),
            "volume.elf": (
                "vol_e",
                {
                    "grid": (
                        "json",
                        "sections/vol_e.grid.json",
                        json.dumps(
                            {
                                "origin": [0, 0, 0],
                                "voxel_vectors": [
                                    [0.2, 0, 0],
                                    [0, 0.2, 0],
                                    [0, 0, 0.2],
                                ],
                                "shape": [5, 5, 5],
                            }
                        ).encode(),
                    ),
                    "data": (
                        "binary",
                        "sections/vol_e.dat",
                        np.zeros((5, 5, 5), dtype=np.float32).tobytes(),
                        "float32",
                        [5, 5, 5],
                    ),
                },
            ),
            "volume.difference": (
                "vol_df",
                {
                    "grid": (
                        "json",
                        "sections/vol_df.grid.json",
                        json.dumps(
                            {
                                "origin": [0, 0, 0],
                                "voxel_vectors": [
                                    [0.2, 0, 0],
                                    [0, 0.2, 0],
                                    [0, 0, 0.2],
                                ],
                                "shape": [5, 5, 5],
                            }
                        ).encode(),
                    ),
                    "data": (
                        "binary",
                        "sections/vol_df.dat",
                        np.zeros((5, 5, 5), dtype=np.float32).tobytes(),
                        "float32",
                        [5, 5, 5],
                    ),
                },
            ),
            "volume.transition_density": (
                "vol_td",
                {
                    "grid": (
                        "json",
                        "sections/vol_td.grid.json",
                        json.dumps(
                            {
                                "origin": [0, 0, 0],
                                "voxel_vectors": [
                                    [0.2, 0, 0],
                                    [0, 0.2, 0],
                                    [0, 0, 0.2],
                                ],
                                "shape": [5, 5, 5],
                            }
                        ).encode(),
                    ),
                    "data": (
                        "binary",
                        "sections/vol_td.dat",
                        np.zeros((5, 5, 5), dtype=np.float32).tobytes(),
                        "float32",
                        [5, 5, 5],
                    ),
                },
            ),
            "volume.potential": (
                "vol_esp",
                {
                    "grid": (
                        "json",
                        "sections/vol_esp.grid.json",
                        json.dumps(
                            {
                                "origin": [0, 0, 0],
                                "voxel_vectors": [
                                    [0.2, 0, 0],
                                    [0, 0.2, 0],
                                    [0, 0, 0.2],
                                ],
                                "shape": [5, 5, 5],
                            }
                        ).encode(),
                    ),
                    "data": (
                        "binary",
                        "sections/vol_esp.dat",
                        np.zeros((5, 5, 5), dtype=np.float32).tobytes(),
                        "float32",
                        [5, 5, 5],
                    ),
                },
            ),
            "volume.rdg": (
                "vol_rdg",
                {
                    "grid": (
                        "json",
                        "sections/vol_rdg.grid.json",
                        json.dumps(
                            {
                                "origin": [0, 0, 0],
                                "voxel_vectors": [
                                    [0.2, 0, 0],
                                    [0, 0.2, 0],
                                    [0, 0, 0.2],
                                ],
                                "shape": [5, 5, 5],
                            }
                        ).encode(),
                    ),
                    "data": (
                        "binary",
                        "sections/vol_rdg.dat",
                        np.zeros((5, 5, 5), dtype=np.float32).tobytes(),
                        "float32",
                        [5, 5, 5],
                    ),
                },
            ),
            "volume.generic": (
                "vol_gn",
                {
                    "grid": (
                        "json",
                        "sections/vol_gn.grid.json",
                        json.dumps(
                            {
                                "origin": [0, 0, 0],
                                "voxel_vectors": [
                                    [0.2, 0, 0],
                                    [0, 0.2, 0],
                                    [0, 0, 0.2],
                                ],
                                "shape": [5, 5, 5],
                            }
                        ).encode(),
                    ),
                    "data": (
                        "binary",
                        "sections/vol_gn.dat",
                        np.zeros((5, 5, 5), dtype=np.float32).tobytes(),
                        "float32",
                        [5, 5, 5],
                    ),
                },
            ),
            "reaction.path": (
                "rxn",
                {
                    "metadata": (
                        "json",
                        "sections/reaction.json",
                        json.dumps(
                            {
                                "atoms": [{"symbol": "H", "atomic_number": 1}],
                                "energies": [0.0, -0.5, -1.0],
                                "waypoints": [
                                    {"frame_index": 0, "label": "R", "kind": "reactant"},
                                    {"frame_index": 2, "label": "P", "kind": "product"},
                                ],
                            }
                        ).encode(),
                    ),
                    "coords": (
                        "binary",
                        "sections/reaction.coords.dat",
                        np.zeros((3, 1, 3), dtype=np.float64).tobytes(),
                        "float64",
                        [3, 1, 3],
                    ),
                },
            ),
            "reaction.waypoints": (
                "rxn_wp",
                {
                    "waypoints": (
                        "json",
                        "sections/reaction_wp.json",
                        json.dumps(
                            {
                                "waypoints": [
                                    {"frame_index": 0, "label": "R", "kind": "reactant"},
                                ]
                            }
                        ).encode(),
                    ),
                },
            ),
            "scan.surface": (
                "scan",
                {
                    "metadata": (
                        "json",
                        "sections/scan_meta.json",
                        json.dumps(
                            {
                                "shape": [2, 2],
                                "coordinate_a_label": "bond 0–1",
                                "coordinate_a_unit": "bohr",
                                "coordinate_b_label": "bond 0–2",
                                "coordinate_b_unit": "bohr",
                            }
                        ).encode(),
                    ),
                    "axis_a": (
                        "binary",
                        "sections/scan_a.dat",
                        np.array([1.4, 1.8], dtype=np.float64).tobytes(),
                        "float64",
                        [2],
                    ),
                    "axis_b": (
                        "binary",
                        "sections/scan_b.dat",
                        np.array([1.4, 1.8], dtype=np.float64).tobytes(),
                        "float64",
                        [2],
                    ),
                    "energies": (
                        "binary",
                        "sections/scan_e.dat",
                        np.zeros((2, 2), dtype=np.float64).tobytes(),
                        "float64",
                        [2, 2],
                    ),
                },
            ),
            "wavefunction.gto": (
                "wf",
                {
                    "basis": (
                        "json",
                        "sections/wf_basis.json",
                        json.dumps(
                            {
                                "structure_ref": "structure",
                                "pure": True,
                                "n_ao": 1,
                                "shells": [
                                    {
                                        "center": 0,
                                        "l": 0,
                                        "exponents": [1.0],
                                        "coefficients": [1.0],
                                    }
                                ],
                            }
                        ).encode(),
                    ),
                    "mo_metadata": (
                        "json",
                        "sections/wf_mo.json",
                        json.dumps(
                            {
                                "n_mo": 1,
                                "n_ao": 1,
                                "spin": "restricted",
                                "orbital_kind": "canonical",
                                "energies": [-0.5],
                                "occupations": [2.0],
                            }
                        ).encode(),
                    ),
                    "mo_coefficients": (
                        "binary",
                        "sections/wf_mo.dat",
                        np.array([[1.0]], dtype=np.float64).tobytes(),
                        "float64",
                        [1, 1],
                    ),
                },
            ),
            "spectra.nmr": (
                "nmr",
                {
                    "spectrum": (
                        "json",
                        "sections/nmr.json",
                        json.dumps(
                            {
                                "isotope": "1H",
                                "reference": "TMS",
                                "chemical_shifts": [
                                    {"atom_index": 0, "symbol": "H", "isotropic_shift_ppm": 4.65},
                                    {"atom_index": 1, "symbol": "H", "isotropic_shift_ppm": 4.65},
                                ],
                            }
                        ).encode(),
                    ),
                },
            ),
            "spectra.epr": (
                "epr",
                {
                    "spectrum": (
                        "json",
                        "sections/epr.json",
                        json.dumps(
                            {
                                "g_tensor": {"g_iso": 2.0023, "principal": [2.002, 2.002, 2.003]},
                                "hyperfine": [
                                    {"atom_index": 0, "symbol": "H", "isotope": "1H",
                                     "a_iso_mhz": 1420.4}
                                ],
                                "zero_field_splitting": {"d_mhz": 0.0, "e_mhz": 0.0},
                            }
                        ).encode(),
                    ),
                },
            ),
            "structure.symmetry": (
                "sym",
                {
                    "data": (
                        "json",
                        "sections/sym.json",
                        json.dumps(
                            {
                                "space_group_number": 225,
                                "space_group_symbol": "Fm-3m",
                                "point_group": "m-3m",
                                "number_of_symmetry_operations": 48,
                            }
                        ).encode(),
                    ),
                },
            ),
            "scf_history": (
                "scf",
                {
                    "iterations": (
                        "json",
                        "sections/scf_iter.json",
                        json.dumps(
                            {
                                "iterations": [
                                    {
                                        "iter": 1,
                                        "energy_eh": -75.5,
                                        "delta_e": 1.0,
                                        "diis_error": 1e-2,
                                    },
                                    {
                                        "iter": 2,
                                        "energy_eh": -75.9,
                                        "delta_e": 0.4,
                                        "diis_error": 1e-4,
                                    },
                                    {
                                        "iter": 3,
                                        "energy_eh": -75.95,
                                        "delta_e": 0.05,
                                        "diis_error": 1e-7,
                                    },
                                ]
                            }
                        ).encode(),
                    ),
                },
            ),
            "citations": (
                "cites",
                {
                    # The canonical CitationsReferences schema branch
                    # disallows dtype/shape (additionalProperties:false),
                    # so we hand-build the member spec instead of going
                    # through the helper's 3-tuple auto-fill.
                    "references": {
                        "path": "sections/refs.bib",
                        "format": "binary",
                        "sha256": hashlib.sha256(
                            b"@article{stub2026,\n  title = {Stub},\n  year = 2026,\n}\n"
                        ).hexdigest(),
                        "_bytes": b"@article{stub2026,\n  title = {Stub},\n  year = 2026,\n}\n",
                    },
                },
            ),
            "dos.total": (
                "dos_total",
                {
                    "energies": (
                        "binary",
                        "sections/dos_energies.dat",
                        np.linspace(-10, 10, 100, dtype=np.float64).tobytes(),
                    ),
                    "dos": (
                        "binary",
                        "sections/dos_total.dat",
                        np.exp(
                            -((np.linspace(-10, 10, 100, dtype=np.float64)) ** 2) / 4.0
                        ).tobytes(),
                    ),
                },
            ),
            "dos.projected": (
                "dos_pdos",
                {
                    "energies": (
                        "binary",
                        "sections/dos_pdos_energies.dat",
                        np.linspace(-10, 10, 100, dtype=np.float64).tobytes(),
                    ),
                    "projections": (
                        "binary",
                        "sections/dos_projections.dat",
                        np.stack(
                            [
                                np.exp(-((np.linspace(-10, 10, 100) - 2) ** 2) / 2.0),
                                np.exp(-((np.linspace(-10, 10, 100) + 1) ** 2) / 3.0),
                            ],
                            axis=0,
                        )
                        .astype(np.float64)
                        .tobytes(),
                        "float64",
                        [2, 100],
                    ),
                },
            ),
            "phonon_bands": (
                "phonon_bands",
                {
                    "qpath": (
                        "json",
                        "sections/phonon_qpath.json",
                        json.dumps({"segments": [], "n_modes": 3}).encode(),
                    ),
                    "frequencies": (
                        "binary",
                        "sections/phonon_freq.dat",
                        np.zeros((4, 3), dtype=np.float64).tobytes(),
                        "float64",
                        [4, 3],
                    ),
                },
            ),
            "phonon_dos": (
                "phonon_dos",
                {
                    "meta": (
                        "json",
                        "sections/phonon_dos_meta.json",
                        json.dumps({"n_atoms": 1}).encode(),
                    ),
                    "frequencies": (
                        "binary",
                        "sections/phonon_dos_freq.dat",
                        np.linspace(0, 300, 50, dtype=np.float64).tobytes(),
                    ),
                    "dos": (
                        "binary",
                        "sections/phonon_dos_total.dat",
                        np.ones(50, dtype=np.float64).tobytes(),
                    ),
                },
            ),
            "equation_of_state": (
                "eos",
                {
                    "volumes": (
                        "binary",
                        "sections/eos_volumes.dat",
                        np.linspace(100.0, 120.0, 7, dtype=np.float64).tobytes(),
                    ),
                    "energies": (
                        "binary",
                        "sections/eos_energies.dat",
                        (-5000.0 + 0.01 * (np.linspace(100.0, 120.0, 7) - 110.0) ** 2)
                        .astype(np.float64)
                        .tobytes(),
                    ),
                    "fit": (
                        "json",
                        "sections/eos_fit.json",
                        json.dumps(
                            {
                                "model": "birch_murnaghan",
                                "V0": 110.0,
                                "E0": -5000.0,
                                "B0": 75.0,
                                "B0_prime": 4.0,
                            }
                        ).encode(),
                    ),
                },
            ),
            "fermi_surface": (
                "fermi0",
                {
                    "mesh": (
                        "json",
                        "sections/fermi_mesh.json",
                        json.dumps(
                            {
                                "nk1": 4,
                                "nk2": 4,
                                "nk3": 4,
                                "n_spin": 1,
                                "fermi_energy_ev": -4.7,
                                "band_indices": [3],
                                "lattice_vectors": [[4, 0, 0], [0, 4, 0], [0, 0, 4]],
                            }
                        ).encode(),
                    ),
                    "energies": (
                        "binary",
                        "sections/fermi_energies.dat",
                        np.zeros((4, 4, 4, 1), dtype=np.float64).tobytes(),
                        "float64",
                        [4, 4, 4, 1],
                    ),
                },
            ),
            "bond_orders": (
                "bo",
                {
                    "bond_orders": (
                        "json",
                        "sections/bo.json",
                        json.dumps(
                            {
                                "method": "mayer",
                                "pairs": [{"i": 0, "j": 1, "order": 0.98}],
                            }
                        ).encode(),
                    ),
                },
            ),
            "topology.qtaim": (
                "qtaim",
                {
                    "critical_points": (
                        "json",
                        "sections/qtaim.json",
                        json.dumps(
                            {
                                "points": [
                                    {
                                        "type": "bcp",
                                        "position": [0.7, 0.0, 0.0],
                                        "rho": 0.26,
                                        "laplacian": -0.54,
                                    }
                                ],
                            }
                        ).encode(),
                    ),
                },
            ),
            "dos.coop": (
                "coop0",
                {
                    "energies": (
                        "binary",
                        "sections/coop_e.bin",
                        np.zeros(100, dtype=np.float64).tobytes(),
                        "float64",
                        [100],
                    ),
                    "projections": (
                        "binary",
                        "sections/coop_p.bin",
                        np.zeros((3, 100), dtype=np.float64).tobytes(),
                        "float64",
                        [3, 100],
                    ),
                    "integrated": (
                        "binary",
                        "sections/coop_i.bin",
                        np.zeros(3, dtype=np.float64).tobytes(),
                        "float64",
                        [3],
                    ),
                    "meta": (
                        "json",
                        "sections/coop_m.json",
                        json.dumps({"pair_labels": ["A-B", "A-C", "B-C"]}).encode(),
                    ),
                },
            ),
            "dos.cohp": (
                "cohp0",
                {
                    "energies": (
                        "binary",
                        "sections/cohp_e.bin",
                        np.zeros(100, dtype=np.float64).tobytes(),
                        "float64",
                        [100],
                    ),
                    "projections": (
                        "binary",
                        "sections/cohp_p.bin",
                        np.zeros((2, 100), dtype=np.float64).tobytes(),
                        "float64",
                        [2, 100],
                    ),
                    "integrated": (
                        "binary",
                        "sections/cohp_i.bin",
                        np.zeros(2, dtype=np.float64).tobytes(),
                        "float64",
                        [2],
                    ),
                    "meta": (
                        "json",
                        "sections/cohp_m.json",
                        json.dumps({"pair_labels": ["Si-Si"]}).encode(),
                    ),
                },
            ),
            "job.spec": (
                "job_spec",
                {
                    "spec": (
                        "json",
                        "job_spec/spec.json",
                        json.dumps(
                            {"job_type": "molecular", "method": "rhf"}
                        ).encode(),
                    ),
                },
            ),
            "run.record": (
                "run_rec",
                {
                    # run.record text members are plain UTF-8 blobs whose
                    # canonical schema branch (like citations) carries no
                    # dtype/shape — hand-build the member specs instead of
                    # the helper's 3-tuple auto-fill.
                    "input": {
                        "path": "run_rec/input.txt",
                        "format": "binary",
                        "sha256": hashlib.sha256(b"! water single point\n").hexdigest(),
                        "_bytes": b"! water single point\n",
                    },
                    "log": {
                        "path": "run_rec/log.txt",
                        "format": "binary",
                        "sha256": hashlib.sha256(b"SCF converged.\n").hexdigest(),
                        "_bytes": b"SCF converged.\n",
                    },
                },
            ),
            # Descriptive kinds (#90). A scheme's participants are checked
            # when it is read, not at dispatch.
            "reaction.scheme": (
                "scheme",
                {
                    "scheme": (
                        "json",
                        "scheme/scheme.json",
                        json.dumps(
                            {
                                "steps": [
                                    {
                                        "id": "r1",
                                        "reactants": [{"structure_ref": "a"}],
                                        "products": [{"structure_ref": "b"}],
                                    }
                                ]
                            }
                        ).encode(),
                    ),
                },
            ),
            "preview": (
                "preview",
                {
                    "thumbnail": {
                        "path": "preview/thumbnail.png",
                        "format": "binary",
                        "media_type": "image/png",
                        "sha256": hashlib.sha256(b"\x89PNG\r\n\x1a\n").hexdigest(),
                        "_bytes": b"\x89PNG\r\n\x1a\n",
                    },
                },
            ),
            "excited_states": (
                "excited",
                {
                    "states": (
                        "json",
                        "excited/states.json",
                        json.dumps(
                            {
                                "states": [
                                    {
                                        "index": 1,
                                        "excitation_energy": {"value": 7.4, "units": "ev"},
                                    }
                                ]
                            }
                        ).encode(),
                    ),
                },
            ),
        }

        # `reaction.waypoints` requires a `trajectory_ref` at the
        # section level — set via the `extra` parameter.
        extra = {
            "rxn_wp": {"trajectory_ref": "rxn_wp_traj"},
            # run.record carries its program identity at section level.
            "run_rec": {"program": "democode"},
        }

        for kind in SUPPORTED_KINDS:
            sid, members = kind_data[kind]
            path = _make_qvf_with_sections(
                [(sid, kind, members)],
                extra={sid: extra[sid]} if sid in extra else None,
            )
            try:
                reader = QVFReader(path)
                section = reader.get_section(sid)
                renderer = get_renderer(section, reader)
                assert renderer is not None, f"Kind {kind!r} returned None renderer"
            finally:
                path.unlink()


class TestWavefunctionRenderer:
    def test_frontier_indices(self):
        from vibeview.renderers.wavefunction import _block_frontier

        occs = [2.0, 2.0, 1.0, 0.0, 0.0]
        homo, lumo = _block_frontier("canonical", occs, 5, natural_confirmed=False)
        assert homo == 2
        assert lumo == 3

    def test_frontier_indices_no_occ(self):
        from vibeview.renderers.wavefunction import _block_frontier

        homo, lumo = _block_frontier("canonical", None, 5, natural_confirmed=False)
        assert homo == -1
        assert lumo == -1

    def test_frontier_indices_empty(self):
        from vibeview.renderers.wavefunction import _block_frontier

        homo, lumo = _block_frontier("canonical", [], 5, natural_confirmed=False)
        assert homo == -1
        assert lumo == -1

    def test_frontier_indices_partially_occupied(self):
        from vibeview.renderers.wavefunction import _block_frontier

        # All MOs occupied (up to n_mo-1)
        homo, lumo = _block_frontier("canonical", [2.0, 2.0, 2.0], 3, natural_confirmed=False)
        assert homo == 2
        assert lumo == -1  # no virtuals

    def test_frontier_indices_all_virtual(self):
        from vibeview.renderers.wavefunction import _block_frontier

        # No occupied MOs
        homo, lumo = _block_frontier("canonical", [0.0, 0.0, 0.0], 3, natural_confirmed=False)
        assert homo == -1
        assert lumo == 0  # first virtual is LUMO

    def test_mo_row_title(self):
        from vibeview.renderers.wavefunction import _mo_row_title

        title = _mo_row_title("restricted", 5, -0.314, 2.0, "a1", "HOMO")
        assert "#5" in title
        assert "HOMO" in title
        assert "a1" in title

    def test_mo_row_title_no_sym_label(self):
        from vibeview.renderers.wavefunction import _mo_row_title

        title = _mo_row_title("alpha", 3, -0.200, 1.0, "", "LUMO")
        assert "#3" in title
        assert "LUMO" in title

    def test_spin_short_labels(self):
        from vibeview.renderers.wavefunction import _SPIN_SHORT

        assert _SPIN_SHORT["restricted"] == ""
        assert _SPIN_SHORT["alpha"] == "\u03b1"
        assert _SPIN_SHORT["beta"] == "\u03b2"


class TestDOSRenderer:
    def test_dos_import(self):
        from vibeview.renderers.dos import DOSRenderer

        assert DOSRenderer is not None

    def test_dos_renderer_class(self):
        from vibeview.renderers import BaseRenderer
        from vibeview.renderers.dos import DOSRenderer

        assert issubclass(DOSRenderer, BaseRenderer)


class TestStructureRenderer:
    def test_bond_order_color_returns_hex_string(self):
        from vibeview.renderers.structure import _bond_order_color

        c = _bond_order_color(0.5)
        assert c.startswith("#"), f"Expected hex color, got {c!r}"
        c = _bond_order_color(1.5)
        assert c.startswith("#")
        c = _bond_order_color(2.5)
        assert c.startswith("#")

    def test_cpk_radius_positive(self):
        from vibeview.renderers.structure import cpk_radius

        assert cpk_radius(6) > 0  # Carbon
        assert cpk_radius(1) > 0  # Hydrogen
        assert cpk_radius(26) > 0  # Iron

    def test_cpk_radius_with_scale(self):
        from vibeview.renderers.structure import cpk_radius

        r1 = cpk_radius(6)
        r2 = cpk_radius(6, scale=2.0)
        assert r2 == pytest.approx(r1 * 2.0)

    def test_cpk_color_returns_hex_string(self):
        from vibeview.renderers.structure import cpk_color

        c = cpk_color(6)
        assert c.startswith("#"), f"Expected hex color, got {c!r}"

    def test_cpk_color_unknown_element(self):
        from vibeview.renderers.structure import _DEFAULT_COLOR, cpk_color

        c = cpk_color(999)
        assert c == _DEFAULT_COLOR

    def test_default_color_constant(self):
        from vibeview.renderers.structure import _DEFAULT_COLOR

        assert _DEFAULT_COLOR.startswith("#")
