"""Tests for the headless capture API — verify each capture function works."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pytest


def _make_qvf(sections: list[dict], files: dict[str, bytes] | None = None) -> Path:
    """Create a minimal valid .qvf file in a temp directory."""
    manifest = {
        "qvf_version": 1,
        "source": {
            "program": "vibe-qc",
            "version": "0.14.1",
            "calculation": "RHF/sto-3g",
        },
        "sections": sections,
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        if files:
            for path, data in files.items():
                zf.writestr(path, data)
    return Path(tmp.name)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_structure_section() -> tuple[dict, bytes]:
    data = json.dumps(
        {
            "atoms": [
                {"symbol": "O", "position": [0.0, 0.0, 0.0], "atomic_number": 8},
                {"symbol": "H", "position": [0.0, 0.757, 0.587], "atomic_number": 1},
                {"symbol": "H", "position": [0.0, -0.757, 0.587], "atomic_number": 1},
            ],
            "pbc": [False, False, False],
        }
    ).encode()
    section = {
        "id": "structure",
        "kind": "structure",
        "members": {
            "structure": {
                "path": "sections/structure.json",
                "format": "json",
                "sha256": _sha256(data),
            }
        },
    }
    return section, data


def _make_volume_section() -> tuple[dict, bytes, bytes]:
    """Return (section_dict, grid.json bytes, data.dat bytes)."""
    grid = {
        "origin": [-4.0, -4.0, -4.0],
        "voxel_vectors": [
            [0.5, 0.0, 0.0],
            [0.0, 0.5, 0.0],
            [0.0, 0.0, 0.5],
        ],
        "shape": [16, 16, 16],
    }
    grid_bytes = json.dumps(grid).encode()
    # Create a small 3D gaussian blob in the centre
    x = np.linspace(-4, 4, 16)
    X, Y, Z = np.meshgrid(x, x, x, indexing="ij")
    r2 = X**2 + Y**2 + Z**2
    values = np.exp(-r2 / 2.0).astype(np.float32)
    dat_bytes = values.tobytes()
    section = {
        "id": "vol_dens_0",
        "kind": "volume.density",
        "members": {
            "grid": {
                "path": "sections/vol_dens_0/grid.json",
                "format": "json",
                "sha256": _sha256(grid_bytes),
            },
            "data": {
                "path": "sections/vol_dens_0/data.dat",
                "format": "binary",
                "sha256": _sha256(dat_bytes),
                "dtype": "float32",
                "shape": [16, 16, 16],
            },
        },
    }
    return section, grid_bytes, dat_bytes


# ═══════════════════════════════════════════════════════════════════
# Capture tests
# ═══════════════════════════════════════════════════════════════════


class TestCaptureStructure:
    def test_capture_structure(self) -> None:
        """capture_structure should produce a non-empty PNG."""
        from vibeview.capture import capture_structure
        from vibeview.qvf import QVFReader

        sec, data = _make_structure_section()
        path = _make_qvf([sec], {"sections/structure.json": data})
        out = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        out_path = Path(out.name)
        out.close()
        try:
            reader = QVFReader(path)
            try:
                ok = capture_structure(reader, out_path)
                assert ok is True
                assert out_path.stat().st_size > 100, "PNG too small"
            finally:
                reader.close()
        finally:
            path.unlink()
            out_path.unlink()

    def test_capture_structure_no_section(self) -> None:
        """Returns False when no structure section exists."""
        from vibeview.capture import capture_structure
        from vibeview.qvf import QVFReader

        # schema requires at least one section, so include a bonds section
        bonds_json = json.dumps({"pairs": []}).encode()
        bonds_sec = {
            "id": "bonds",
            "kind": "bonds",
            "members": {
                "bonds": {
                    "path": "sections/bonds.json",
                    "format": "json",
                    "sha256": _sha256(bonds_json),
                }
            },
        }
        path = _make_qvf([bonds_sec], {"sections/bonds.json": bonds_json})
        out = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        out_path = Path(out.name)
        out.close()
        try:
            reader = QVFReader(path)
            try:
                ok = capture_structure(reader, out_path)
                assert ok is False
            finally:
                reader.close()
        finally:
            path.unlink()
            out_path.unlink()

    def test_capture_structure_with_replication(self) -> None:
        """Replication should work (for periodic systems)."""
        from vibeview.capture import capture_structure
        from vibeview.qvf import QVFReader

        sec, data = _make_structure_section()
        path = _make_qvf([sec], {"sections/structure.json": data})
        out = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        out_path = Path(out.name)
        out.close()
        try:
            reader = QVFReader(path)
            try:
                ok = capture_structure(reader, out_path, replication=(2, 1, 1))
                assert ok is True
                assert out_path.stat().st_size > 100
            finally:
                reader.close()
        finally:
            path.unlink()
            out_path.unlink()


class TestCaptureVolume:
    def test_capture_volume_density(self) -> None:
        """capture_volume should render a density isosurface."""
        from vibeview.capture import capture_volume
        from vibeview.qvf import QVFReader

        struct_sec, struct_data = _make_structure_section()
        vol_sec, grid_data, values_data = _make_volume_section()
        files = {
            "sections/structure.json": struct_data,
            "sections/vol_dens_0/grid.json": grid_data,
            "sections/vol_dens_0/data.dat": values_data,
        }
        path = _make_qvf([struct_sec, vol_sec], files)
        out = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        out_path = Path(out.name)
        out.close()
        try:
            reader = QVFReader(path)
            try:
                ok = capture_volume(reader, "vol_dens_0", out_path, isovalue=0.1)
                assert ok is True
                assert out_path.stat().st_size > 100
            finally:
                reader.close()
        finally:
            path.unlink()
            out_path.unlink()

    def test_capture_volume_nonexistent(self) -> None:
        """Returns False for missing section."""
        from vibeview.capture import capture_volume
        from vibeview.qvf import QVFReader

        sec, data = _make_structure_section()
        path = _make_qvf([sec], {"sections/structure.json": data})
        out = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        out_path = Path(out.name)
        out.close()
        try:
            # capture_volume uses get_section which raises for missing sections
            # - should be caught and return False per the function signature
            reader = QVFReader(path)
            try:
                ok = capture_volume(reader, "nonexistent", out_path)
                assert ok is False
            finally:
                reader.close()
        finally:
            path.unlink()
            out_path.unlink()


class TestCaptureBands:
    def test_capture_bands(self) -> None:
        """capture_bands should produce a PNG."""
        from vibeview.capture import capture_bands
        from vibeview.qvf import QVFReader

        struct_sec, struct_data = _make_structure_section()

        # kpath member: JSON
        kpath_data = {
            "segments": [
                {"label_start": "G", "label_end": "X", "n_points": 5},
            ],
            "n_bands": 4,
            "n_spin": 1,
            "fermi": 0.0,
        }
        kpath_bytes = json.dumps(kpath_data).encode()

        # eigenvalues member: binary [n_spin, n_kpoints, n_bands]
        eigenvalues = np.array(
            [
                [
                    [-4.0, -2.0, 0.5, 3.0],
                    [-3.5, -1.5, 1.0, 3.5],
                    [-2.0, -0.5, 2.0, 4.0],
                    [-1.0, 1.0, 3.0, 5.0],
                    [0.0, 2.0, 4.0, 6.0],
                ]
            ],
            dtype=np.float64,
        )  # shape [1, 5, 4]
        eig_bytes = eigenvalues.tobytes()

        bands_sec = {
            "id": "bands_0",
            "kind": "bands",
            "members": {
                "kpath": {
                    "path": "sections/bands_0/kpath.json",
                    "format": "json",
                    "sha256": _sha256(kpath_bytes),
                },
                "eigenvalues": {
                    "path": "sections/bands_0/eigenvalues.dat",
                    "format": "binary",
                    "sha256": _sha256(eig_bytes),
                    "dtype": "float64",
                    "shape": [1, 5, 4],
                },
            },
        }
        files = {
            "sections/structure.json": struct_data,
            "sections/bands_0/kpath.json": kpath_bytes,
            "sections/bands_0/eigenvalues.dat": eig_bytes,
        }
        path = _make_qvf([struct_sec, bands_sec], files)
        out = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        out_path = Path(out.name)
        out.close()
        try:
            reader = QVFReader(path)
            try:
                ok = capture_bands(reader, out_path)
                assert ok is True
                assert out_path.stat().st_size > 100
            finally:
                reader.close()
        finally:
            path.unlink()
            out_path.unlink()


class TestCapture2DHtml:
    """Tests for capture functions that produce HTML."""

    def test_capture_dos(self) -> None:
        """capture_dos should produce an HTML file with Plotly content."""
        from vibeview.capture import capture_dos
        from vibeview.qvf import QVFReader

        struct_sec, struct_data = _make_structure_section()

        # DOS members: "energies" (binary) + "dos" (binary)
        energies = np.linspace(-10, 5, 200, dtype=np.float64)
        dos_vals = np.exp(-((energies + 2) ** 2) / 2.0) + np.exp(-((energies - 1) ** 2) / 2.0)
        energies_bytes = energies.tobytes()
        dos_bytes = dos_vals.astype(np.float64).tobytes()

        dos_sec = {
            "id": "dos_total",
            "kind": "dos.total",
            "members": {
                "energies": {
                    "path": "sections/dos_total/e.dat",
                    "format": "binary",
                    "sha256": _sha256(energies_bytes),
                    "dtype": "float64",
                    "shape": [200],
                },
                "dos": {
                    "path": "sections/dos_total/dos.dat",
                    "format": "binary",
                    "sha256": _sha256(dos_bytes),
                    "dtype": "float64",
                    "shape": [200],
                },
            },
        }
        files = {
            "sections/structure.json": struct_data,
            "sections/dos_total/e.dat": energies_bytes,
            "sections/dos_total/dos.dat": dos_bytes,
        }
        path = _make_qvf([struct_sec, dos_sec], files)
        out = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
        out_path = Path(out.name)
        out.close()
        try:
            reader = QVFReader(path)
            try:
                ok = capture_dos(reader, out_path)
                assert ok is True
                content = out_path.read_text()
                assert "plotly" in content.lower() or "<script" in content.lower(), (
                    f"Expected Plotly content, got {len(content)} chars starting: {content[:200]}"
                )
            finally:
                reader.close()
        finally:
            path.unlink()
            out_path.unlink()

    def test_capture_scf_history(self) -> None:
        """capture_scf_history should produce HTML."""
        import os

        from vibeview.capture import capture_scf_history
        from vibeview.qvf import QVFReader

        struct_sec, struct_data = _make_structure_section()

        # scf_history schema: members.iterations (json member)
        scf_data = {
            "iterations": [
                {"energy": -75.5, "delta_e": 0.5},
                {"energy": -75.8, "delta_e": 0.1},
                {"energy": -75.9, "delta_e": 0.05},
                {"energy": -75.95, "delta_e": 0.03},
                {"energy": -75.98, "delta_e": 0.01},
                {"energy": -75.99, "delta_e": 0.001},
            ]
        }
        scf_bytes = json.dumps(scf_data).encode()
        scf_sec = {
            "id": "scf_hist0",
            "kind": "scf_history",
            "members": {
                "iterations": {
                    "path": "sections/scf_hist0.json",
                    "format": "json",
                    "sha256": _sha256(scf_bytes),
                }
            },
        }
        files = {
            "sections/structure.json": struct_data,
            "sections/scf_hist0.json": scf_bytes,
        }
        path = _make_qvf([struct_sec, scf_sec], files)
        out = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
        out_path = Path(out.name)
        out.close()
        try:
            reader = QVFReader(path)
            try:
                os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
                ok = capture_scf_history(reader, out_path)
                assert ok is True
                content = out_path.read_text()
                assert len(content) > 100, f"HTML too short: {len(content)} chars"
            finally:
                reader.close()
        finally:
            path.unlink()
            out_path.unlink()

    def test_capture_bond_orders(self) -> None:
        """capture_bond_orders should produce HTML."""
        import os

        from vibeview.capture import capture_bond_orders
        from vibeview.qvf import QVFReader

        struct_sec, struct_data = _make_structure_section()

        # bond_orders schema: members.bond_orders (json member)
        bo_data = {
            "method": "mayer",
            "pairs": [
                {"i": 0, "j": 1, "order": 0.85, "label": "O-H"},
                {"i": 0, "j": 2, "order": 0.84, "label": "O-H"},
            ],
        }
        bo_bytes = json.dumps(bo_data).encode()
        bo_sec = {
            "id": "bond_orders",
            "kind": "bond_orders",
            "members": {
                "bond_orders": {
                    "path": "sections/bond_orders.json",
                    "format": "json",
                    "sha256": _sha256(bo_bytes),
                }
            },
        }
        files = {
            "sections/structure.json": struct_data,
            "sections/bond_orders.json": bo_bytes,
        }
        path = _make_qvf([struct_sec, bo_sec], files)
        out = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
        out_path = Path(out.name)
        out.close()
        try:
            reader = QVFReader(path)
            try:
                os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
                ok = capture_bond_orders(reader, out_path)
                assert ok is True
                content = out_path.read_text()
                assert len(content) > 100
            finally:
                reader.close()
        finally:
            path.unlink()
            out_path.unlink()

    def test_capture_spectra(self) -> None:
        """capture_spectra should produce HTML."""
        import os

        from vibeview.capture import capture_spectra
        from vibeview.qvf import QVFReader

        struct_sec, struct_data = _make_structure_section()

        # spectra.ir schema: members.spectrum (json member)
        spectra_data = {
            "frequencies": [100.0, 200.0, 350.0, 500.0],
            "intensities": [0.1, 10.0, 5.0, 0.5],
        }
        spec_bytes = json.dumps(spectra_data).encode()
        spec_sec = {
            "id": "spectra_ir",
            "kind": "spectra.ir",
            "members": {
                "spectrum": {
                    "path": "sections/spectra_ir.json",
                    "format": "json",
                    "sha256": _sha256(spec_bytes),
                }
            },
        }
        files = {
            "sections/structure.json": struct_data,
            "sections/spectra_ir.json": spec_bytes,
        }
        path = _make_qvf([struct_sec, spec_sec], files)
        out = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
        out_path = Path(out.name)
        out.close()
        try:
            reader = QVFReader(path)
            try:
                os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
                ok = capture_spectra(reader, out_path)
                assert ok is True
                content = out_path.read_text()
                assert len(content) > 100
            finally:
                reader.close()
        finally:
            path.unlink()
            out_path.unlink()

    def test_capture_energy_diagram(self) -> None:
        """capture_energy_diagram should produce HTML."""
        from vibeview.capture import capture_energy_diagram
        from vibeview.qvf import QVFReader

        struct_sec, struct_data = _make_structure_section()

        # wavefunction.gto schema requires mo_coefficients OR alpha+beta
        basis_data = {
            "atoms": [
                {
                    "symbol": "O",
                    "position": [0.0, 0.0, 0.0],
                    "shells": [{"ang_mom": 0, "primitives": [{"exp": 130.709, "coeff": 0.1543}]}],
                }
            ]
        }
        basis_bytes = json.dumps(basis_data).encode()
        mo_meta = {
            "n_electrons": 10,
            "n_basis": 1,
            "n_mo": 1,
            "spin": "restricted",
            "mo_energies": [-20.5],
            "mo_occupations": [2.0],
            "symmetry_labels": [],
        }
        mo_bytes = json.dumps(mo_meta).encode()
        # mo_coefficients: binary [n_mo, n_basis] = [1, 1]
        coef = np.array([[1.0]], dtype=np.float64)
        coef_bytes = coef.tobytes()
        wf_sec = {
            "id": "wf",
            "kind": "wavefunction.gto",
            "members": {
                "basis": {
                    "path": "sections/wf/basis.json",
                    "format": "json",
                    "sha256": _sha256(basis_bytes),
                },
                "mo_metadata": {
                    "path": "sections/wf/mo.json",
                    "format": "json",
                    "sha256": _sha256(mo_bytes),
                },
                "mo_coefficients": {
                    "path": "sections/wf/coef.dat",
                    "format": "binary",
                    "sha256": _sha256(coef_bytes),
                    "dtype": "float64",
                    "shape": [1, 1],
                },
            },
        }
        files = {
            "sections/structure.json": struct_data,
            "sections/wf/basis.json": basis_bytes,
            "sections/wf/mo.json": mo_bytes,
            "sections/wf/coef.dat": coef_bytes,
        }
        path = _make_qvf([struct_sec, wf_sec], files)
        out = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
        out_path = Path(out.name)
        out.close()
        try:
            reader = QVFReader(path)
            try:
                ok = capture_energy_diagram(reader, out_path)
                assert ok is True
                content = out_path.read_text()
                assert len(content) > 100
            finally:
                reader.close()
        finally:
            path.unlink()
            out_path.unlink()
