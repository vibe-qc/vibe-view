"""Tests for the QVF reader — manifest parsing, schema validation, sha256."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import pytest

from tests._qvf_builders import vibe_qc_source, write_archive
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
    """A version-1 archive with these sections, member payloads and optional
    ``viewer_defaults``, in a temporary file."""
    return write_archive(
        sections, files, source=vibe_qc_source("0.9.0", "test"), viewer_defaults=viewer_defaults
    )


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
            assert reader.connectivity(structure_data) == [(0, 1, 1.0, (0, 0, 0))]
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
                reader.connectivity(structure_data)
        finally:
            path.unlink()


class TestQVFReaderEdgeCases:
    def test_reader_hash(self, sample_qvf):
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        h = hash(reader)
        assert isinstance(h, int)
        reader.close()

    def test_reader_repr(self, sample_qvf):
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        r = repr(reader)
        assert "QVFReader" in r
        reader.close()

    def test_reader_context_manager(self, sample_qvf):
        from vibeview.qvf import QVFReader

        with QVFReader(sample_qvf) as reader:
            assert reader.sections is not None

    def test_manifest_source(self, sample_qvf):
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        src = reader.manifest.source
        assert src.program == "vibe-qc"
        reader.close()

    def test_manifest_qvf_version(self, sample_qvf):
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        version = reader.manifest.qvf_version
        assert isinstance(version, int)
        reader.close()

    def test_reader_sections_iterable(self, sample_qvf):
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        sections_list = list(reader.sections)
        assert len(sections_list) >= 1
        reader.close()

    def test_reader_has_section(self, sample_qvf):
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        assert reader.has_section("structure") is True
        assert reader.has_section("nonexistent") is False
        reader.close()


class TestQVFSource:
    def test_reader_from_path(self, sample_qvf):
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        assert reader is not None
        reader.close()

    def test_reader_from_bytes(self, sample_qvf):
        from vibeview.qvf import QVFReader

        data = sample_qvf.read_bytes()
        reader = QVFReader(data)
        assert reader is not None
        reader.close()


class TestReaderSurface:
    def test_reader_manifest_to_dict(self, sample_qvf):
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        manifest = reader.manifest
        assert hasattr(manifest, "source")
        reader.close()

    def test_section_attributes(self, sample_qvf):
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        for section in reader.sections:
            assert hasattr(section, "id")
            assert hasattr(section, "kind")
            break
        reader.close()



class TestWithdrawnVersion:
    """``qvf_version`` 2 is read and checked as 1, with a warning (#88).

    vibe-qc 0.10-0.15 stamped 2 on archives with a periodic
    ``reaction.path``; the bump was withdrawn. QVF spec § 3.1: a consumer
    reads such an archive as version 1 for every check, the JSON Schema
    included, and reports ``W_VERSION_WITHDRAWN``. vibe-view carries no v2
    schema; the v1 schema of the pinned qvf release governs both.
    """

    _STRUCTURE = json.dumps(
        {
            "atoms": [{"symbol": "H", "atomic_number": 1, "position": [0.0, 0.0, 0.0]}],
            "pbc": [False, False, False],
        }
    ).encode()

    def _archive(self, tmp_path: Path, qvf_version, *, member_extra=None, **extra) -> Path:
        member = {
            "path": "structure/structure.json",
            "format": "json",
            "sha256": _sha256(self._STRUCTURE),
            **(member_extra or {}),
        }
        return write_archive(
            [{"id": "structure", "kind": "structure", "members": {"structure": member}}],
            {"structure/structure.json": self._STRUCTURE},
            dest=tmp_path / "archive.qvf",
            source=vibe_qc_source("0.12.0", "test"),
            qvf_version=qvf_version,
            **extra,
        )

    def test_a_version_2_archive_opens_with_the_warning(self, tmp_path) -> None:
        with QVFReader(self._archive(tmp_path, 2)) as reader:
            assert reader.read_structure().atoms[0].symbol == "H"
            # The manifest still says what the file says.
            assert reader.manifest.qvf_version == 2
            [warning] = reader.manifest_warnings()
        assert warning.startswith("W_VERSION_WITHDRAWN:")
        assert "read as version 1" in warning

    def test_a_version_1_archive_has_no_manifest_warning(self, tmp_path) -> None:
        with QVFReader(self._archive(tmp_path, 1)) as reader:
            assert reader.manifest_warnings() == []

    def test_version_2_is_checked_against_the_current_v1_schema(self, tmp_path) -> None:
        """Root ``byte_order`` and member ``size`` arrived with qvf v0.2.0's v1
        schema; the old vendored v2 schema refused both."""
        path = self._archive(
            tmp_path, 2,
            member_extra={"size": len(self._STRUCTURE)},
            byte_order="little",
        )
        with QVFReader(path) as reader:
            assert [w.split(":")[0] for w in reader.manifest_warnings()] == [
                "W_VERSION_WITHDRAWN"
            ]

    def test_version_2_is_not_exempt_from_the_schema(self, tmp_path) -> None:
        path = self._archive(tmp_path, 2, member_extra={"format": "yaml"})
        with pytest.raises(ManifestValidationError):
            QVFReader(path)

    @pytest.mark.parametrize("version", [0, 3, 99, "2", True])
    def test_other_versions_are_still_refused(self, tmp_path, version) -> None:
        with pytest.raises(ManifestValidationError):
            QVFReader(self._archive(tmp_path, version))

    def test_the_qvf_legacy_archive_opens_with_the_warning(self) -> None:
        """qvf's own ``conformance/legacy`` archive, from the checkout at QVF_TAG."""
        import os

        from tests._qvf_corpus import qvf_corpus_dir

        corpus = qvf_corpus_dir()
        legacy = corpus.parent / "legacy" if corpus is not None else None
        archive = legacy / "reaction_path_v2.qvf" if legacy is not None else None
        if archive is None or not archive.is_file():
            if os.environ.get("VIBEVIEW_REQUIRE_QVF_CORPUS"):
                raise RuntimeError(
                    "VIBEVIEW_REQUIRE_QVF_CORPUS is set, but the qvf checkout has no "
                    f"conformance/legacy/reaction_path_v2.qvf (corpus: {corpus})"
                )
            pytest.skip("qvf checkout unavailable; set QVF_CORPUS or check out qvf")
        expected = json.loads((legacy / "reaction_path_v2.expected.json").read_text())
        codes = [item["code"] for item in expected["expect"]]
        with QVFReader(archive) as reader:
            assert [w.split(":")[0] for w in reader.manifest_warnings()] == codes
            # Its metadata names atoms by symbols and atomic_numbers (spec § 5.5);
            # the path is periodic by its lattice member, not by the version.
            path = reader.read_reaction_path("path")
            assert [a.symbol for a in path.atoms] == ["C", "C"]
            assert path.lattice is not None


@pytest.mark.parametrize("value", [None, ""])
def test_job_spec_reads_an_empty_functional_or_basis_as_null(tmp_path, value) -> None:
    """qvf v0.3.0, spec § 5.9: null says the method takes none, and "" is its
    deprecated spelling, read as null (#88)."""
    spec = json.dumps({"job_type": "molecular", "method": "gfn2_xtb",
                       "basis": value, "functional": value}).encode()
    path = write_archive(
        [{"id": "job", "kind": "job.spec", "members": {"spec": {
            "path": "job/spec.json", "format": "json", "sha256": _sha256(spec)}}}],
        {"job/spec.json": spec},
        dest=tmp_path / "job.qvf",
        source=vibe_qc_source("0.17.0", "job"),
    )
    with QVFReader(path) as reader:
        job = reader.read_job_spec("job")
    assert (job.basis, job.functional) == (None, None)
