"""Batch side-by-side comparison of several archives."""

from __future__ import annotations

import json

import numpy as np
import pytest


class TestBatchCompare:
    def test_import(self):
        from vibeview.batch_compare import batch_compare_to_table, compare_batch

        assert callable(compare_batch)
        assert callable(batch_compare_to_table)

    def test_compare_empty(self):
        from vibeview.batch_compare import compare_batch

        result = compare_batch([])
        assert "No valid" in result["summary"]

    def test_compare_single_file(self, sample_qvf):
        from vibeview.batch_compare import compare_batch

        result = compare_batch([str(sample_qvf)])
        assert len(result["files"]) == 1

    def test_compare_two_files(self, sample_qvf, tmp_path):
        from vibeview.batch_compare import compare_batch

        result = compare_batch([str(sample_qvf), str(sample_qvf)])
        assert len(result["files"]) == 2
        assert len(result["energies"]) == 2

    def test_batch_to_table(self, sample_qvf):
        from vibeview.batch_compare import batch_compare_to_table, compare_batch

        result = compare_batch([str(sample_qvf)])
        table = batch_compare_to_table(result)
        assert "| File |" in table

    @staticmethod
    def _make_qvf_with_energy(tmp_path, name, atoms, energy):
        import hashlib
        import zipfile

        structure = json.dumps({"atoms": atoms, "pbc": [False, False, False]}).encode()
        manifest = {
            "qvf_version": 1,
            "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": "t"},
            "provenance": {"scf_energy": {"value": energy, "units": "Eh"}},
            "sections": [
                {
                    "id": "structure",
                    "kind": "structure",
                    "members": {
                        "structure": {
                            "path": "sections/structure.json",
                            "format": "json",
                            "sha256": hashlib.sha256(structure).hexdigest(),
                        }
                    },
                }
            ],
        }
        p = tmp_path / name
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("sections/structure.json", structure)
        return str(p)

    def test_energy_read_from_provenance(self, tmp_path):
        """Regression: energies were always None (probed a nonexistent
        manifest.scf_energy_eh); must read provenance scf_energy.value."""
        from vibeview.batch_compare import compare_batch

        atoms = [
            {"symbol": "O", "position": [0.0, 0.0, 0.117], "atomic_number": 8},
            {"symbol": "H", "position": [0.0, 0.757, -0.469], "atomic_number": 1},
            {"symbol": "H", "position": [0.0, -0.757, -0.469], "atomic_number": 1},
        ]
        path = self._make_qvf_with_energy(tmp_path, "e.qvf", atoms, -76.123456)
        result = compare_batch([path])
        assert result["energies"][0]["scf_energy_eh"] == -76.123456

    def test_align_uses_kabsch_superposition(self, tmp_path):
        """Regression: align=True used non-superposing rmsd. A rigidly rotated
        copy of the same structure must give RMSD ~ 0 after Kabsch fit."""

        from vibeview.batch_compare import compare_batch

        base = [
            {"symbol": "O", "position": [0.0, 0.0, 0.117], "atomic_number": 8},
            {"symbol": "H", "position": [0.0, 0.757, -0.469], "atomic_number": 1},
            {"symbol": "H", "position": [0.0, -0.757, -0.469], "atomic_number": 1},
        ]
        # Rotate 90 degrees about z.
        rot = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
        rotated = [
            {
                "symbol": a["symbol"],
                "atomic_number": a["atomic_number"],
                "position": (rot @ np.array(a["position"])).tolist(),
            }
            for a in base
        ]
        p_ref = self._make_qvf_with_energy(tmp_path, "ref.qvf", base, -76.0)
        p_rot = self._make_qvf_with_energy(tmp_path, "rot.qvf", rotated, -76.0)
        result = compare_batch([p_ref, p_rot], reference_idx=0, align=True)
        rmsd_rot = result["rmsd"][1]["rmsd_angstrom"]
        assert rmsd_rot is not None
        assert rmsd_rot < 1e-6  # superposition removes the pure rotation

    def test_reference_index_out_of_range(self, sample_qvf):
        """Regression: an out-of-range reference index crashed with a raw
        IndexError; must raise a clean ValueError."""

        from vibeview.batch_compare import compare_batch

        with pytest.raises(ValueError, match="out of range"):
            compare_batch([str(sample_qvf)], reference_idx=5)
