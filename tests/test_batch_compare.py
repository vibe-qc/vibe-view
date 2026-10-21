"""Batch side-by-side comparison of several archives."""

from __future__ import annotations

import json
import os

import numpy as np
import pytest


@pytest.mark.skipif(os.name == "nt", reason="These filenames require a POSIX filesystem.")
@pytest.mark.parametrize("readable", [True, False], ids=["loaded", "skipped"])
@pytest.mark.parametrize(
    ("filename", "cell"),
    [
        ("water|dft.qvf", r"water\|dft.qvf"),
        ("line\nbreak.qvf", "line break.qvf"),
        (r"water\|dft.qvf", r"water\\\|dft.qvf"),
    ],
)
def test_table_filenames_stay_in_one_markdown_cell(sample_qvf, tmp_path, readable, filename, cell):
    from vibeview.batch_compare import batch_compare_to_table, compare_batch

    path = tmp_path / filename
    path.write_bytes(sample_qvf.read_bytes() if readable else b"not a QVF archive")

    result = compare_batch([str(sample_qvf), str(path)])
    table = batch_compare_to_table(result)

    assert len(table.splitlines()) == 4, "two header lines and one row per input file"
    assert table.splitlines()[-1].startswith(f"| {cell} | ")
    if readable:
        assert result["files"][-1] == str(path)
    else:
        assert result["skipped"][-1]["path"] == str(path)


@pytest.mark.skipif(os.name == "nt", reason="These filenames require a POSIX filesystem.")
def test_cli_table_escapes_filename_while_json_keeps_original_path(sample_qvf, tmp_path):
    from click.testing import CliRunner

    from vibeview.cli import main

    path = tmp_path / "water|dft.qvf"
    path.write_bytes(sample_qvf.read_bytes())
    args = ["batch-compare", str(sample_qvf), str(path)]

    text = CliRunner().invoke(main, args)
    payload = CliRunner().invoke(main, [*args, "--json"])

    assert text.exit_code == 0, text.output
    assert r"| water\|dft.qvf | " in text.stdout
    assert payload.exit_code == 0, payload.output
    assert json.loads(payload.stdout)["files"][-1] == str(path)


def test_table_note_escapes_backslash_before_pipe():
    from vibeview.batch_compare import batch_compare_to_table

    table = batch_compare_to_table(
        {"skipped": [{"path": "broken.qvf", "reason": r"cannot read a\|b"}]}
    )

    assert r"skipped: cannot read a\\\|b |" in table


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
