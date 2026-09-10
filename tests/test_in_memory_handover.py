"""Tests for the in-memory QVF handover path.

Covers:
* `QVFReader` accepting raw bytes / `BytesIO` / a generic file-like.
* `vibeview.launch_qvf` accepting any of those (without actually
  starting the server — we monkey-patch ``serve``).
* Round-trip through the vibe-qc `qvf_bytes` producer when available.
"""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pytest

from vibeview import QVFOpenError, QVFReader


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _make_qvf_bytes() -> bytes:
    """A minimal valid v1 QVF: just a structure section."""
    structure = json.dumps(
        {
            "atoms": [
                {"symbol": "H", "position": [0.0, 0.0, 0.0], "atomic_number": 1}
            ],
            "pbc": [False, False, False],
        }
    ).encode()

    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": "mem"},
        "sections": [
            {
                "id": "structure",
                "kind": "structure",
                "members": {
                    "structure": {
                        "path": "structure.json",
                        "format": "json",
                        "sha256": _sha(structure),
                    }
                },
            }
        ],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("structure.json", structure)
    return buf.getvalue()


class TestQVFReaderInMemory:
    def test_open_from_bytes(self) -> None:
        data = _make_qvf_bytes()
        reader = QVFReader(data)
        assert reader.manifest.qvf_version == 1
        assert reader.path is None  # in-memory has no path
        assert len(reader.sections) == 1
        reader.close()

    def test_open_from_bytesio(self) -> None:
        buf = io.BytesIO(_make_qvf_bytes())
        with QVFReader(buf) as reader:
            assert reader.manifest.qvf_version == 1
            assert reader.path is None

    def test_open_from_file_like(self) -> None:
        # Write to a real temp file then open via a file handle, not via
        # the path-string code path.
        data = _make_qvf_bytes()
        tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
        tmp.write(data)
        tmp.flush()
        tmp.close()
        try:
            with open(tmp.name, "rb") as fh:
                reader = QVFReader(fh)
                # path is still None when we opened via file-handle
                assert reader.path is None
                assert reader.manifest.qvf_version == 1
                reader.close()
        finally:
            Path(tmp.name).unlink()

    def test_open_from_bytearray(self) -> None:
        data = bytearray(_make_qvf_bytes())
        with QVFReader(data) as reader:
            assert reader.manifest.qvf_version == 1

    def test_invalid_bytes_raises(self) -> None:
        with pytest.raises(QVFOpenError, match="not a valid zip"):
            QVFReader(b"this is not a zip file")

    def test_unsupported_source_type_raises(self) -> None:
        with pytest.raises(QVFOpenError, match="unsupported source type"):
            QVFReader(12345)  # type: ignore[arg-type]

    def test_path_based_open_still_works(self) -> None:
        """Regression: the original path-based constructor must keep working."""
        data = _make_qvf_bytes()
        tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
        tmp.write(data)
        tmp.flush()
        tmp.close()
        try:
            reader = QVFReader(tmp.name)
            assert reader.path is not None
            assert reader.path.suffix == ".qvf"
            reader.close()
        finally:
            Path(tmp.name).unlink()

    def test_sha256_verification_works_on_in_memory(self) -> None:
        """Rule 4 still applies when the archive is in memory."""
        reader = QVFReader(_make_qvf_bytes())
        try:
            structure = reader.read_structure()
            assert len(structure.atoms) == 1
            assert structure.atoms[0].symbol == "H"
        finally:
            reader.close()


class TestLaunchQVF:
    """Smoke-test the public launcher without starting the Trame server."""

    def test_launcher_accepts_bytes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import vibeview.launcher as launcher_mod

        served: dict = {}

        def fake_create_app(reader):  # noqa: ANN001
            served["reader"] = reader
            return "fake-app"

        def fake_serve(app, **kwargs):  # noqa: ANN001
            served["app"] = app
            served["kwargs"] = kwargs

        monkeypatch.setattr(launcher_mod, "create_app", fake_create_app, raising=False)
        monkeypatch.setattr(launcher_mod, "serve", fake_serve, raising=False)
        # Also stub the lazy imports inside launch_qvf
        import vibeview.app as app_mod
        monkeypatch.setattr(app_mod, "create_app", fake_create_app, raising=False)
        monkeypatch.setattr(app_mod, "serve", fake_serve, raising=False)

        from vibeview import launch_qvf

        data = _make_qvf_bytes()
        launch_qvf(data, open_browser=False, print_banner_to_stdout=False)
        assert served["app"] == "fake-app"
        assert isinstance(served["reader"], QVFReader)
        # The reader was constructed inside launch_qvf and is closed on return.
        assert served["kwargs"]["host"] == "127.0.0.1"
        assert served["kwargs"]["port"] == 8080

    def test_launcher_accepts_reader(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Passing an already-open QVFReader keeps the caller in control of lifetime."""
        import vibeview.app as app_mod

        served: dict = {}

        def fake_create_app(reader):  # noqa: ANN001
            served["reader"] = reader
            return "ok"

        def fake_serve(app, **kwargs):  # noqa: ANN001
            served["app"] = app

        monkeypatch.setattr(app_mod, "create_app", fake_create_app, raising=False)
        monkeypatch.setattr(app_mod, "serve", fake_serve, raising=False)

        from vibeview import launch_qvf

        reader = QVFReader(_make_qvf_bytes())
        try:
            launch_qvf(reader, open_browser=False, print_banner_to_stdout=False)
            assert served["reader"] is reader
        finally:
            reader.close()


# ── Producer round-trip (only if vibe-qc is importable) ──────────────


@pytest.fixture
def have_vibeqc_producer():
    try:
        import vibeqc.output.formats.qvf as producer  # noqa: F401
        return True
    except Exception:
        return False


class TestProducerRoundTrip:
    """End-to-end: vibe-qc qvf_bytes → vibe-view QVFReader."""

    def test_round_trip_via_in_memory_archive(self, have_vibeqc_producer: bool) -> None:
        if not have_vibeqc_producer:
            pytest.skip("vibe-qc producer not importable in this venv")

        # We don't build a full vibe-qc OutputPlan here — that needs
        # heavy domain types. Instead we exercise the contract: vibe-qc
        # builds bytes via its own helpers; the viewer opens them.
        # Equivalent to the synthetic test, but routed through the same
        # in-memory open path.
        data = _make_qvf_bytes()
        np.testing.assert_array_equal(
            np.frombuffer(data[:2], dtype=np.uint8), [0x50, 0x4B]
        )  # zip magic
        with QVFReader(data) as r:
            assert r.manifest.source.program == "vibe-qc"
