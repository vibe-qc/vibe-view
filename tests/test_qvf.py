"""Tests for the QVF reader — manifest parsing, schema validation, sha256."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import pytest

from vibeview.qvf import (
    ManifestValidationError,
    MemberSpec,
    QVFError,
    QVFOpenError,
    QVFReader,
    SHA256MismatchError,
)


def _make_qvf(
    sections: list[dict],
    files: dict[str, bytes] | None = None,
    viewer_defaults: dict | None = None,
) -> Path:
    """Create a minimal valid .qvf file in a temp directory.

    sections: list of section dicts for the manifest.
    files: extra files to include in the zip (keyed by path inside zip).
    viewer_defaults: optional viewer_defaults dict.
    """
    manifest = {
        "qvf_version": 1,
        "source": {
            "program": "vibe-qc",
            "version": "0.9.0",
            "calculation": "test",
        },
        "sections": sections,
    }
    if viewer_defaults is not None:
        manifest["viewer_defaults"] = viewer_defaults

    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        if files:
            for path, data in files.items():
                zf.writestr(path, data)
    return Path(tmp.name)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestQVFOpen:
    def test_nonexistent_file(self) -> None:
        with pytest.raises(QVFOpenError, match="file not found"):
            QVFReader("/nonexistent/path.qvf")

    def test_wrong_extension(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            f.write(b"not a qvf")
            path = Path(f.name)
        try:
            with pytest.raises(QVFOpenError, match="expected .qvf"):
                QVFReader(path)
        finally:
            path.unlink()

    def test_bad_zip_path_is_wrapped(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".qvf", delete=False) as f:
            f.write(b"not a zip")
            path = Path(f.name)
        try:
            with pytest.raises(QVFOpenError, match="not a valid zip archive"):
                QVFReader(path)
        finally:
            path.unlink()

    def test_no_manifest(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".qvf", delete=False) as f:
            with zipfile.ZipFile(f, "w") as zf:
                zf.writestr("other.txt", "hello")
            path = Path(f.name)
        try:
            with pytest.raises(QVFOpenError, match="manifest.json not found"):
                QVFReader(path)
        finally:
            path.unlink()

    def test_invalid_json_manifest(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".qvf", delete=False) as f:
            with zipfile.ZipFile(f, "w") as zf:
                zf.writestr("manifest.json", "{not valid json")
            path = Path(f.name)
        try:
            with pytest.raises(QVFOpenError, match="not valid JSON"):
                QVFReader(path)
        finally:
            path.unlink()


class TestSchemaValidation:
    def test_valid_manifest(self) -> None:
        path = _make_qvf(
            [
                {
                    "id": "structure",
                    "kind": "structure",
                    "members": {
                        "structure": {
                            "path": "sections/structure.json",
                            "format": "json",
                            "sha256": _sha256(b"{}"),
                        }
                    },
                }
            ]
        )
        try:
            reader = QVFReader(path)
            assert reader.manifest.qvf_version == 1
            assert len(reader.manifest.sections) == 1
        finally:
            path.unlink()

    def test_duplicate_section_ids_are_rejected(self) -> None:
        structure = json.dumps(
            {
                "atoms": [
                    {"symbol": "H", "position": [0, 0, 0], "atomic_number": 1}
                ]
            }
        ).encode()
        section = {
            "id": "structure",
            "kind": "structure",
            "members": {
                "structure": {
                    "path": "sections/structure.json",
                    "format": "json",
                    "sha256": _sha256(structure),
                }
            },
        }
        path = _make_qvf(
            [section, section],
            files={"sections/structure.json": structure},
        )
        try:
            with pytest.raises(ManifestValidationError, match="duplicate section id"):
                QVFReader(path)
        finally:
            path.unlink()

    def test_missing_required_field(self) -> None:
        manifest = {
            "qvf_version": 1,
            # missing "source" and "sections"
        }
        with tempfile.NamedTemporaryFile(suffix=".qvf", delete=False) as f:
            with zipfile.ZipFile(f, "w") as zf:
                zf.writestr("manifest.json", json.dumps(manifest))
            path = Path(f.name)
        try:
            with pytest.raises(ManifestValidationError):
                QVFReader(path)
        finally:
            path.unlink()

    def test_unsupported_qvf_version(self) -> None:
        # v2 is now valid (periodic reaction.path). The reader must
        # still reject versions outside the supported set.
        manifest = {
            "qvf_version": 99,
            "source": {"program": "x", "version": "1", "calculation": "y"},
            "sections": [],
        }
        with tempfile.NamedTemporaryFile(suffix=".qvf", delete=False) as f:
            with zipfile.ZipFile(f, "w") as zf:
                zf.writestr("manifest.json", json.dumps(manifest))
            path = Path(f.name)
        try:
            with pytest.raises(ManifestValidationError):
                QVFReader(path)
        finally:
            path.unlink()

    def test_invalid_sha256_format(self) -> None:
        manifest = {
            "qvf_version": 1,
            "source": {"program": "x", "version": "1", "calculation": "y"},
            "sections": [
                {
                    "id": "s",
                    "kind": "structure",
                    "members": {
                        "s": {
                            "path": "x.json",
                            "format": "json",
                            "sha256": "not-a-hex-string",
                        }
                    },
                }
            ],
        }
        with tempfile.NamedTemporaryFile(suffix=".qvf", delete=False) as f:
            with zipfile.ZipFile(f, "w") as zf:
                zf.writestr("manifest.json", json.dumps(manifest))
            path = Path(f.name)
        try:
            with pytest.raises(ManifestValidationError):
                QVFReader(path)
        finally:
            path.unlink()


class TestSHA256Verification:
    """Rule 4: sha256 verified before use."""

    def test_matching_sha256_passes(self) -> None:
        data = b'{"hello": "world"}'
        expected = _sha256(data)
        path = _make_qvf(
            [
                {
                    "id": "test",
                    "kind": "structure",
                    "members": {
                        "structure": {
                            "path": "sections/test.json",
                            "format": "json",
                            "sha256": expected,
                        }
                    },
                }
            ],
            files={"sections/test.json": data},
        )
        try:
            reader = QVFReader(path)
            # Verification happens during read_structure
            result = reader._verify_and_read(
                MemberSpec(
                    path="sections/test.json",
                    format="json",
                    sha256=expected,
                )
            )
            assert result == data
        finally:
            path.unlink()

    def test_mismatched_sha256_raises(self) -> None:
        data = b"correct data"
        wrong_hash = _sha256(b"wrong data")
        path = _make_qvf(
            [
                {
                    "id": "test",
                    "kind": "structure",
                    "members": {
                        "structure": {
                            "path": "sections/test.json",
                            "format": "json",
                            "sha256": wrong_hash,
                        }
                    },
                }
            ],
            files={"sections/test.json": data},
        )
        try:
            reader = QVFReader(path)
            with pytest.raises(SHA256MismatchError, match="sha256 mismatch"):
                reader._verify_and_read(
                    MemberSpec(
                        path="sections/test.json",
                        format="json",
                        sha256=wrong_hash,
                    )
                )
        finally:
            path.unlink()


class TestQVFReaderProperties:
    def test_source_access(self) -> None:
        path = _make_qvf([])
        try:
            reader = QVFReader(path)
            assert reader.source.program == "vibe-qc"
            assert reader.source.version == "0.9.0"
            assert reader.source.calculation == "test"
        finally:
            path.unlink()

    def test_sections_access(self) -> None:
        # Under the canonical schema every kind has a required member
        # set. We use vendor (x_*) kinds here because the test exercises
        # the section-listing API, not the per-kind contract.
        path = _make_qvf(
            [
                {"id": "s1", "kind": "x_test.alpha", "members": {}},
                {"id": "s2", "kind": "x_test.beta", "members": {}},
            ]
        )
        try:
            reader = QVFReader(path)
            assert len(reader.sections) == 2
            assert reader.has_section("s1")
            assert reader.has_section("s2")
            assert not reader.has_section("nonexistent")
        finally:
            path.unlink()

    def test_viewer_defaults_none(self) -> None:
        path = _make_qvf([])
        try:
            reader = QVFReader(path)
            assert reader.viewer_defaults is None
        finally:
            path.unlink()

    def test_viewer_defaults_present(self) -> None:
        path = _make_qvf(
            [],
            viewer_defaults={"auto_open": ["density"]},
        )
        try:
            reader = QVFReader(path)
            assert reader.viewer_defaults is not None
            assert reader.viewer_defaults.auto_open == ["density"]
        finally:
            path.unlink()

    def test_viewer_defaults_with_section_hints(self) -> None:
        """Per-section hints like "density": {"isovalue": ...} are captured."""
        path = _make_qvf(
            [],
            viewer_defaults={
                "auto_open": ["density"],
                "density": {"isovalue": 0.05, "colormap": "plasma", "opacity": 0.7},
                "homo": {"isovalue": 0.03, "colormap": "RdBu"},
            },
        )
        try:
            reader = QVFReader(path)
            defaults = reader.viewer_defaults
            assert defaults is not None
            assert defaults.auto_open == ["density"]
            # Extra per-section hints are in model_extra
            extras = getattr(defaults, "model_extra", None) or {}
            assert "density" in extras
            assert "homo" in extras
            assert extras["density"]["isovalue"] == 0.05
            assert extras["density"]["colormap"] == "plasma"
            assert extras["density"]["opacity"] == 0.7
            assert extras["homo"]["isovalue"] == 0.03
            assert extras["homo"]["colormap"] == "RdBu"
        finally:
            path.unlink()

    def test_context_manager(self) -> None:
        path = _make_qvf([])
        with QVFReader(path) as reader:
            assert reader.manifest.qvf_version == 1
        path.unlink()

    def test_path_property(self) -> None:
        path = _make_qvf([])
        try:
            reader = QVFReader(path)
            assert reader.path == path
            assert reader.path.name.endswith(".qvf")
        finally:
            path.unlink()

    def test_no_structure_section_opens(self) -> None:
        """A QVF without a structure section should still open. A
        well-formed bands section is the smallest "real" example."""
        kpath_data = _sha256(b"{}")
        eig_data = _sha256(b"\x00" * 8)
        path = _make_qvf(
            [
                {
                    "id": "bands",
                    "kind": "bands",
                    "members": {
                        "kpath": {
                            "path": "bands/kpath.json",
                            "format": "json",
                            "sha256": kpath_data,
                        },
                        "eigenvalues": {
                            "path": "bands/eig.bin",
                            "format": "binary",
                            "dtype": "float64",
                            "shape": [1, 1, 1],
                            "sha256": eig_data,
                        },
                    },
                },
            ],
            files={"bands/kpath.json": b"{}", "bands/eig.bin": b"\x00" * 8},
        )
        try:
            reader = QVFReader(path)
            assert len(reader.sections) == 1
            assert not reader.has_section("structure")
        finally:
            path.unlink()


class TestQVFReaderPayloadErrors:
    def test_invalid_json_member_raises_qvf_error_on_read(self) -> None:
        bad_json = b"{not valid json"
        path = _make_qvf(
            [
                {
                    "id": "structure",
                    "kind": "structure",
                    "members": {
                        "structure": {
                            "path": "sections/structure.json",
                            "format": "json",
                            "sha256": _sha256(bad_json),
                        }
                    },
                }
            ],
            files={"sections/structure.json": bad_json},
        )
        try:
            reader = QVFReader(path)
            with pytest.raises(QVFError, match="not valid JSON"):
                reader.read_structure()
        finally:
            path.unlink()

    def test_structure_uses_canonical_bonds_section(self) -> None:
        structure = json.dumps(
            {
                "atoms": [
                    {"symbol": "H", "position": [0.0, 0.0, 0.0], "atomic_number": 1},
                    {"symbol": "H", "position": [10.0, 0.0, 0.0], "atomic_number": 1},
                ],
                "pbc": [False, False, False],
            }
        ).encode()
        bonds = json.dumps({"pairs": [{"i": 0, "j": 1, "order": 1.0}]}).encode()
        path = _make_qvf(
            [
                {
                    "id": "structure",
                    "kind": "structure",
                    "members": {
                        "structure": {
                            "path": "sections/structure.json",
                            "format": "json",
                            "sha256": _sha256(structure),
                        }
                    },
                },
                {
                    "id": "bonds0",
                    "kind": "bonds",
                    "members": {
                        "bonds": {
                            "path": "bonds/connectivity.json",
                            "format": "json",
                            "sha256": _sha256(bonds),
                        }
                    },
                },
            ],
            files={
                "sections/structure.json": structure,
                "bonds/connectivity.json": bonds,
            },
        )
        try:
            reader = QVFReader(path)
            structure_data = reader.read_structure()
            assert structure_data.bonds == [(0, 1, 1.0, (0, 0, 0))]
            assert reader.infer_bonds(structure_data) == [(0, 1, 1.0, (0, 0, 0))]
        finally:
            path.unlink()


class TestSHA256MismatchNotSwallowed:
    """SHA256MismatchError is a QVFError subclass; optional-member handlers
    must re-raise it instead of silently dropping corrupt data
    (verify-before-use contract)."""

    def test_read_phonon_dos_meta_mismatch_raises(self) -> None:
        import numpy as np

        freqs = np.linspace(0.0, 100.0, 5).astype(np.float64).tobytes()
        dos = np.ones(5, dtype=np.float64).tobytes()
        meta = json.dumps({"smearing": 1.0}).encode()
        path = _make_qvf(
            sections=[
                {
                    "id": "pdos",
                    "kind": "phonon_dos",
                    "members": {
                        "frequencies": {
                            "path": "f.bin",
                            "format": "binary",
                            "dtype": "float64",
                            "shape": [5],
                            "sha256": _sha256(freqs),
                        },
                        "dos": {
                            "path": "d.bin",
                            "format": "binary",
                            "dtype": "float64",
                            "shape": [5],
                            "sha256": _sha256(dos),
                        },
                        # Corrupt: declared hash does not match the payload.
                        "meta": {
                            "path": "m.json",
                            "format": "json",
                            "sha256": "0" * 64,
                        },
                    },
                }
            ],
            files={"f.bin": freqs, "d.bin": dos, "m.json": meta},
        )
        try:
            reader = QVFReader(path)
            with pytest.raises(SHA256MismatchError):
                reader.read_phonon_dos("pdos")
        finally:
            path.unlink()

    def test_infer_bonds_bond_orders_mismatch_raises(self) -> None:
        structure = json.dumps(
            {
                "atoms": [
                    {"symbol": "H", "position": [0, 0, 0], "atomic_number": 1},
                    {"symbol": "H", "position": [0.7, 0, 0], "atomic_number": 1},
                ],
                "pbc": [False, False, False],
            }
        ).encode()
        bond_orders = json.dumps({"pairs": [{"i": 0, "j": 1, "order": 2.0}]}).encode()
        path = _make_qvf(
            sections=[
                {
                    "id": "structure",
                    "kind": "structure",
                    "members": {
                        "structure": {
                            "path": "s.json",
                            "format": "json",
                            "sha256": _sha256(structure),
                        }
                    },
                },
                {
                    "id": "bo",
                    "kind": "bond_orders",
                    "members": {
                        # Corrupt: declared hash does not match the payload.
                        "bond_orders": {
                            "path": "bo.json",
                            "format": "json",
                            "sha256": "0" * 64,
                        }
                    },
                },
            ],
            files={"s.json": structure, "bo.json": bond_orders},
        )
        try:
            reader = QVFReader(path)
            structure_data = reader.read_structure()
            with pytest.raises(SHA256MismatchError):
                reader.infer_bonds(structure_data)
        finally:
            path.unlink()
