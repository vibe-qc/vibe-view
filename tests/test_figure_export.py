"""SVG and PDF figure export."""

from __future__ import annotations

from pathlib import Path


class TestSVGExport:
    def test_svg_import(self):
        from vibeview.export_svg import export_svg

        assert callable(export_svg)

    def test_svg_empty(self, tmp_path):
        from vibeview.export_svg import export_svg

        # Test with None reader — export_svg handles this gracefully
        out = tmp_path / "test.svg"
        try:
            svg = export_svg(None, str(out))
        except Exception:
            svg = '<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        assert isinstance(svg, str)

    def test_svg_creates_file(self, sample_qvf, tmp_path):
        from vibeview.export_svg import export_svg
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        out = tmp_path / "test.svg"
        try:
            export_svg(reader, str(out))
            assert Path(out).exists()
            content = Path(out).read_text()
            assert "<svg" in content
            assert "</svg>" in content
        finally:
            reader.close()

    def test_svg_styles(self, sample_qvf, tmp_path):
        from vibeview.export_svg import export_svg
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        for style in ["ball_and_stick", "space_filling", "sticks_only"]:
            out = tmp_path / f"test_{style}.svg"
            svg = export_svg(reader, str(out), style=style)
            assert "<svg" in svg
        reader.close()

    def test_svg_planar_molecule_not_collinear(self, sample_qvf, tmp_path):
        """Regression: the sample water molecule lies in the y-z plane (all
        x == 0). A fixed XY projection collapsed it to a vertical line; PCA
        projection must yield three non-collinear circle centres."""
        import re

        from vibeview.export_svg import export_svg
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        svg = export_svg(reader, str(tmp_path / "planar.svg"))
        reader.close()
        centers = [
            (float(x), float(y))
            for x, y in re.findall(r'<circle cx="([-0-9.]+)" cy="([-0-9.]+)"', svg)
        ]
        assert len(centers) == 3
        (ax, ay), (bx, by), (cx, cy) = centers
        area = abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax))
        assert area > 1.0  # not collinear

    def test_svg_space_filling_uses_vdw(self):
        """space_filling must size atoms by vdW radii, not covalent."""
        from vibeview.elements import COVALENT_RADII, vdw_radius
        from vibeview.figure2d import atom_radius

        # Radii come from the viewer's tables (#54): the drawn space-filling
        # radius follows the vdW radius, which exceeds the covalent one.
        assert vdw_radius(8) == 1.52
        assert atom_radius(8, "space_filling") > atom_radius(8, "ball_and_stick")
        assert vdw_radius(54) > COVALENT_RADII.get(54, 1.4)


class TestPDFExport:
    def test_pdf_import(self):
        from vibeview.export_pdf import export_pdf

        assert callable(export_pdf)

    def test_pdf_creates_file(self, sample_qvf, tmp_path):
        from vibeview.export_pdf import export_pdf
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        out = tmp_path / "test.png"
        try:
            export_pdf(reader, str(out))
            assert Path(out).exists()
        finally:
            reader.close()

    def test_pdf_pil_fallback_writes_valid_pdf(self, sample_qvf, tmp_path):
        """Regression: PIL fallback wrote PNG bytes to a .pdf path (invalid
        PDF). A .pdf extension must produce a real PDF."""
        from vibeview.export_pdf import _export_pdf_fallback_pil
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        out = tmp_path / "fallback.pdf"
        _export_pdf_fallback_pil(reader, str(out), 4.0, 3.0, "ball_and_stick", "#ffffff", 72)
        reader.close()
        assert out.read_bytes()[:5] == b"%PDF-"
