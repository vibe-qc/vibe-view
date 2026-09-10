"""Test the high-level Python API module."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import pytest


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_structure_qvf() -> Path:
    """Create a valid QVF with a structure section."""
    structure = json.dumps(
        {
            "atoms": [{"symbol": "He", "position": [0, 0, 0], "atomic_number": 2}],
            "pbc": [False, False, False],
        }
    ).encode()
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": "test"},
        "sections": [
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
            }
        ],
        "provenance": {"method": "RHF", "scf_converged": True},
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("sections/structure.json", structure)
    return Path(tmp.name)


class TestAPI:
    def test_info(self) -> None:
        from vibeview.api import info

        q = _make_structure_qvf()
        try:
            data = info(q)
            assert data["program"] == "vibe-qc"
            assert data["calculation"] == "test"
            assert data["method"] == "RHF"
            assert data["scf_converged"] is True
            assert data["n_sections"] == 1
        finally:
            q.unlink()

    def test_sections(self) -> None:
        from vibeview.api import sections

        q = _make_structure_qvf()
        try:
            secs = sections(q)
            assert secs == [{"id": "structure", "kind": "structure"}]
        finally:
            q.unlink()

    def test_has_section(self) -> None:
        from vibeview.api import has_section

        q = _make_structure_qvf()
        try:
            assert has_section(q, "structure") is True
            assert has_section(q, "nope") is False
        finally:
            q.unlink()

    def test_get_structure(self) -> None:
        from vibeview.api import get_structure

        q = _make_structure_qvf()
        try:
            s = get_structure(q)
            assert len(s["atoms"]) == 1
            assert s["atoms"][0]["symbol"] == "He"
            assert s["pbc"] == [False, False, False]
        finally:
            q.unlink()

    def test_export_xyz(self) -> None:
        from vibeview.api import export_xyz

        q = _make_structure_qvf()
        try:
            xyz = export_xyz(q)
            assert "He" in xyz
            assert "1\n" in xyz
        finally:
            q.unlink()

    def test_diff(self) -> None:
        from vibeview.api import diff

        qa = _make_structure_qvf()
        qb = _make_structure_qvf()
        try:
            d = diff(qa, qb)
            assert "structure" in d["kinds_common"]
        finally:
            qa.unlink()
            qb.unlink()

    def test_validate_ok(self) -> None:
        from vibeview.api import validate

        q = _make_structure_qvf()
        try:
            v = validate(q)
            assert v["valid"] is True
            assert v["n_sections"] == 1
            assert v["n_members"] == 1
        finally:
            q.unlink()

    def test_slice_qvf(self) -> None:
        from vibeview.api import slice_qvf

        q = _make_structure_qvf()
        out = q.parent / "sliced.qvf"
        try:
            result = slice_qvf(q, out, keep=["structure"])
            assert result.exists()
        finally:
            q.unlink()
            out.unlink(missing_ok=True)


def _make_qvf_bytes(energy: float | None = None) -> bytes:
    """In-memory QVF with provenance (optional scf_energy) + viewer_defaults."""
    import io

    structure = json.dumps(
        {
            "atoms": [{"symbol": "He", "position": [0, 0, 0], "atomic_number": 2}],
            "pbc": [False, False, False],
        }
    ).encode()
    prov: dict = {"method": "RHF", "scf_converged": True}
    if energy is not None:
        prov["scf_energy"] = {"value": energy, "units": "hartree"}
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": "test"},
        "sections": [
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
            }
        ],
        "provenance": prov,
        "viewer_defaults": {"auto_open": ["structure"]},
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("sections/structure.json", structure)
    return buf.getvalue()


class TestSliceRegressions:
    def test_slice_qvf_in_memory_source(self, tmp_path: Path) -> None:
        """In-memory readers (path None) must still get their members copied
        (regression: sliced QVF contained only manifest.json)."""
        from vibeview.api import slice_qvf
        from vibeview.qvf import QVFReader

        reader = QVFReader(_make_qvf_bytes())
        out = tmp_path / "sliced.qvf"
        try:
            slice_qvf(reader, out, keep=["structure"])
        finally:
            reader.close()

        with zipfile.ZipFile(out) as zf:
            assert "sections/structure.json" in zf.namelist()
        # And the result is a readable QVF, not manifest-only.
        with QVFReader(out) as sliced:
            assert len(sliced.read_structure().atoms) == 1

    def test_slice_preserves_provenance_and_viewer_defaults(self, tmp_path: Path) -> None:
        from vibeview.api import slice_qvf
        from vibeview.qvf import QVFReader

        reader = QVFReader(_make_qvf_bytes(energy=-2.85))
        out = tmp_path / "sliced.qvf"
        try:
            slice_qvf(reader, out, keep=["structure"])
        finally:
            reader.close()

        with zipfile.ZipFile(out) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        assert manifest["provenance"]["method"] == "RHF"
        assert manifest["provenance"]["scf_energy"]["value"] == -2.85
        assert manifest["viewer_defaults"]["auto_open"] == ["structure"]


class TestDiffZeroEnergy:
    def test_zero_energy_yields_converted_deltas(self) -> None:
        """0.0 Eh is a valid energy: converted deltas must not be nulled by
        a truthiness check while delta_e_eh is set."""
        from vibeview.api import diff
        from vibeview.qvf import QVFReader

        ra = QVFReader(_make_qvf_bytes(energy=0.0))
        rb = QVFReader(_make_qvf_bytes(energy=-1.0))
        try:
            d = diff(ra, rb)
        finally:
            ra.close()
            rb.close()
        assert d["delta_e_eh"] == 1.0
        assert d["delta_e_kcal_mol"] == pytest.approx(627.509)
        assert d["delta_e_ev"] == pytest.approx(27.2114)


class TestCaptureClosesReader:
    def test_capture_structure_closes_path_reader(self, monkeypatch, tmp_path: Path) -> None:
        """capture_structure must close the reader it opened from a path
        (regression: leaked zip handle)."""
        import vibeview.capture as capture_mod
        from vibeview import api as api_mod
        from vibeview.qvf import QVFReader

        q = tmp_path / "cap.qvf"
        q.write_bytes(_make_qvf_bytes())

        seen: list[QVFReader] = []
        monkeypatch.setattr(
            capture_mod,
            "capture_structure",
            lambda reader, path, **kw: seen.append(reader) or True,
        )
        assert api_mod.capture_structure(q, tmp_path / "o.png") is True
        assert len(seen) == 1
        # Closed zip handle raises on read.
        with pytest.raises(ValueError):
            seen[0]._zf.read("manifest.json")

    def test_capture_volume_closes_path_reader(self, monkeypatch, tmp_path: Path) -> None:
        import vibeview.capture as capture_mod
        from vibeview import api as api_mod
        from vibeview.qvf import QVFReader

        q = tmp_path / "cap.qvf"
        q.write_bytes(_make_qvf_bytes())

        seen: list[QVFReader] = []
        monkeypatch.setattr(
            capture_mod,
            "capture_volume",
            lambda reader, section_id, path, **kw: seen.append(reader) or True,
        )
        assert api_mod.capture_volume(q, "vol_0", tmp_path / "o.png") is True
        assert len(seen) == 1
        with pytest.raises(ValueError):
            seen[0]._zf.read("manifest.json")
