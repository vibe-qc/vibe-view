"""Test the standalone HTML export module."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_structure_qvf() -> Path:
    structure = json.dumps(
        {
            "atoms": [
                {"symbol": "O", "position": [0, 0, 0], "atomic_number": 8},
                {"symbol": "H", "position": [0, 0.757, 0.587], "atomic_number": 1},
                {"symbol": "H", "position": [0, -0.757, 0.587], "atomic_number": 1},
            ],
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
                        "path": "sections/s.json",
                        "format": "json",
                        "sha256": _sha256(structure),
                    }
                },
            }
        ],
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("sections/s.json", structure)
    return Path(tmp.name)


class TestExportHTML:
    def test_export_html_produces_valid_file(self) -> None:
        from vibeview.export_html import export_html
        from vibeview.qvf import QVFReader

        q = _make_structure_qvf()
        try:
            reader = QVFReader(q)
            out = q.parent / "test.html"
            result = export_html(reader, out)
            assert result.exists()
            html = result.read_text()
            assert "<!DOCTYPE html>" in html
            assert "three" in html.lower()
            assert "OrbitControls" in html
            assert "O" in html  # oxygen atom
            reader.close()
        finally:
            q.unlink()
            out.unlink(missing_ok=True)

    def test_atom_radius_uses_element_table(self) -> None:
        """Regression: atom sphere radius was hardcoded (z===1?0.25:0.4);
        it must derive from the per-element covalent radii table."""
        from vibeview.export_html import _HTML_TEMPLATE

        # Old hardcoded expression must be gone.
        assert "z === 1 ? 0.25 : 0.4" not in _HTML_TEMPLATE
        # Radius now comes from the shared per-element radii table.
        assert "radii[z]" in _HTML_TEMPLATE
        assert "atomScale" in _HTML_TEMPLATE

    def test_export_html_custom_title(self) -> None:
        from vibeview.export_html import export_html
        from vibeview.qvf import QVFReader

        q = _make_structure_qvf()
        try:
            reader = QVFReader(q)
            out = q.parent / "test2.html"
            export_html(reader, out, title="My Molecule")
            html = out.read_text()
            assert "My Molecule" in html
            reader.close()
        finally:
            q.unlink()
            out.unlink(missing_ok=True)

    def test_export_html_with_bonds(self) -> None:
        """H2O has O-H bonds that should be detected."""
        from vibeview.export_html import export_html
        from vibeview.qvf import QVFReader

        q = _make_structure_qvf()
        try:
            reader = QVFReader(q)
            out = q.parent / "test3.html"
            export_html(reader, out)
            html = out.read_text()
            # Should contain bond cylinder rendering code
            assert "CylinderGeometry" in html or "cylGeo" in html.lower()
            reader.close()
        finally:
            q.unlink()
            out.unlink(missing_ok=True)
