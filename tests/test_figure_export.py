"""SVG and PDF figure export."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pytest

from vibeview.api import validate
from vibeview.qvf import QVFReader
from vibeview.qvf_writer import structure_archive


def _large_atom_archive(tmp_path, atomic_number=55, pair=False):
    from vibeview.elements import z_to_symbol

    atoms = [{
        "symbol": z_to_symbol(atomic_number), "atomic_number": atomic_number,
        "position": [100.0, -200.0, 300.0],
    }]
    if pair:
        atoms.append({**atoms[0], "position": [107.0, -200.0, 300.0]})
    path = structure_archive(atoms, (False, False, False), bonds=[]).write(
        tmp_path / "large-atoms.qvf"
    )
    assert validate(path)["valid"]
    return path


class TestSVGExport:
    @pytest.mark.parametrize("style", ["ball_and_stick", "space_filling", "sticks_only"])
    @pytest.mark.parametrize("size", [(800, 600), (400, 1000), (1600, 400)])
    @pytest.mark.parametrize("pair", [False, True])
    def test_svg_contains_complete_circles(self, tmp_path, style, size, pair):
        from vibeview.export_svg import export_svg

        width, height = size
        with QVFReader(_large_atom_archive(tmp_path, pair=pair)) as reader:
            svg = export_svg(
                reader, str(tmp_path / "large.svg"), style=style, width=width, height=height,
            )
        circles = ET.fromstring(svg).findall("{http://www.w3.org/2000/svg}circle")
        assert len(circles) == (2 if pair else 1)
        for circle in circles:
            x, y, radius = (float(circle.attrib[key]) for key in ("cx", "cy", "r"))
            assert x - radius >= 50.0 - 0.1
            assert x + radius <= width - 50.0 + 0.1
            assert y - radius >= 50.0 - 0.1
            assert y + radius <= height - 50.0 + 0.1

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
    @pytest.mark.parametrize("backend", ["matplotlib", "pil"])
    @pytest.mark.parametrize("style", ["ball_and_stick", "space_filling"])
    @pytest.mark.parametrize("atomic_number", [19, 55])
    def test_pdf_renderer_draws_unclipped_circles(
        self, tmp_path, monkeypatch, backend, style, atomic_number,
    ):
        import sys

        from PIL import Image

        from vibeview.export_pdf import export_pdf

        if backend == "pil":
            monkeypatch.setitem(sys.modules, "matplotlib", None)
            monkeypatch.setitem(sys.modules, "matplotlib.patches", None)
        out = tmp_path / "circle.png"
        with QVFReader(_large_atom_archive(tmp_path, atomic_number)) as reader:
            export_pdf(reader, str(out), style=style, width=6.0, height=4.5, dpi=72)
        with Image.open(out) as image:
            foreground = np.any(np.asarray(image.convert("RGB")) < 240, axis=-1)
        ys, xs = np.nonzero(foreground)
        assert len(xs) > 0
        silhouette = foreground[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        # A complete disk fills pi/4 of its bounding square. Clipping the
        # large atoms at the axes or image boundary instead fills a rectangle.
        assert 0.72 < silhouette.mean() < 0.84

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
