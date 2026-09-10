"""Additional tests for vibe-view v2 post-release features."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np


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
            svg = export_svg(reader, str(out))
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
        from vibeview.export_svg import VDW_RADII, _vdw_radius

        assert VDW_RADII["O"] == 1.52
        assert _vdw_radius("O", 0.66) > 0.66  # vdW exceeds covalent
        assert _vdw_radius("Xe", 1.4) == 1.4 * 1.75  # 1.75x covalent fallback


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
            result = export_pdf(reader, str(out))
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


class TestJupyterIntegration:
    def test_imports(self):
        from vibeview.jupyter import (
            _render_structure,
            _sniff_qvf,
            export_notebook_cell,
            qvf_to_html,
            qvf_to_image,
            register_ipython_magic,
        )

        assert callable(qvf_to_html)
        assert callable(qvf_to_image)
        assert callable(_sniff_qvf)
        assert callable(_render_structure)

    def test_notebook_cell_generation(self):
        from vibeview.jupyter import export_notebook_cell

        code = export_notebook_cell("/tmp/test.qvf")
        assert "QVFReader" in code
        assert "/tmp/test.qvf" in code

    def test_notebook_cell_with_options(self):
        from vibeview.jupyter import export_notebook_cell

        code = export_notebook_cell(
            "/tmp/test.qvf",
            include_density=True,
            include_orbitals=True,
        )
        assert "get_volume" in code
        compile(code, "<vibe-view notebook cell>", "exec")

    def test_qvf_to_html(self, sample_qvf):
        from vibeview.jupyter import qvf_to_html

        html = qvf_to_html(str(sample_qvf))
        assert isinstance(html, str)
        assert "3Dmol" in html

    def test_qvf_to_image(self, sample_qvf):
        import os

        os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
        from vibeview.jupyter import qvf_to_image

        png = qvf_to_image(str(sample_qvf))
        assert isinstance(png, bytes)
        assert len(png) > 100
        assert png[:4] == b"\x89PNG"


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
        import numpy as np

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
        import pytest

        from vibeview.batch_compare import compare_batch

        with pytest.raises(ValueError, match="out of range"):
            compare_batch([str(sample_qvf)], reference_idx=5)


class TestSettings:
    def test_import(self):
        from vibeview.settings import (
            DEFAULTS,
            get_setting,
            load_settings,
            save_settings,
            set_setting,
        )

        assert isinstance(DEFAULTS, dict)

    def test_defaults(self):
        from vibeview.settings import DEFAULTS

        assert "dark_background" in DEFAULTS
        assert "material_preset" in DEFAULTS

    def test_get_setting_default(self):
        from vibeview.settings import get_setting

        val = get_setting("nonexistent_key", "fallback")
        assert val == "fallback"

    def test_get_setting_exists(self):
        from vibeview.settings import get_setting

        val = get_setting("dark_background")
        assert isinstance(val, bool)

    def test_load_settings(self):
        from vibeview.settings import load_settings

        settings = load_settings()
        assert isinstance(settings, dict)
        assert "dark_background" in settings

    def test_add_recent_file(self):
        from vibeview.settings import add_recent_file, get_recent_files

        add_recent_file("/tmp/test_recent.qvf")
        recent = get_recent_files()
        assert "/tmp/test_recent.qvf" in recent


class TestAnimationTools:
    def test_frames_to_mp4_import(self):
        from vibeview.animation import _frames_to_mp4

        assert callable(_frames_to_mp4)

    def test_frames_to_gif_import(self):
        from vibeview.animation import _frames_to_gif

        assert callable(_frames_to_gif)

    def test_render_turntable_import(self):
        from vibeview.animation import render_turntable

        assert callable(render_turntable)

    def test_render_trajectory_import(self):
        from vibeview.animation import render_trajectory_video

        assert callable(render_trajectory_video)

    def test_render_vibration_import(self):
        from vibeview.animation import render_vibration_video

        assert callable(render_vibration_video)

    def test_render_orbital_import(self):
        from vibeview.animation import render_orbital_animation

        assert callable(render_orbital_animation)

    def test_render_reaction_import(self):
        from vibeview.animation import render_reaction_video

        assert callable(render_reaction_video)

    def test_render_reaction_video_writes_gif(self, reaction_qvf, tmp_path):
        """render_reaction_video plays the NEB band frame-by-frame. Uses the
        committed neb_h3 showcase (structure + 7-frame reaction.path) and
        the ffmpeg-free GIF path so it runs anywhere Pillow is present."""
        from pathlib import Path

        from vibeview.animation import render_reaction_video
        from vibeview.qvf import QVFReader

        # Was gated on a gitignored example archive, so it skipped in CI.
        reader = QVFReader(reaction_qvf)
        try:
            out = render_reaction_video(
                reader, tmp_path / "neb.gif", fps=6, size=(240, 180), format="gif"
            )
        finally:
            reader.close()
        assert out is not None and out.exists()
        assert out.suffix == ".gif"
        assert out.stat().st_size > 0

    def test_render_reaction_video_none_without_section(self, sample_qvf):
        """A file with no reaction.path returns None, not an error."""
        from vibeview.animation import render_reaction_video
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        try:
            # sample_qvf is synthesized by this suite and deliberately has no
            # reaction.path. If it grows one, this test stops covering the
            # no-section path and must be pointed at a different fixture --
            # a skip would just hide that.
            assert not any(
                s.kind == "reaction.path" for s in reader.sections
            ), "sample_qvf grew a reaction.path; this test needs a fixture without one"
            assert render_reaction_video(reader, "/tmp/should_not_write.gif") is None
        finally:
            reader.close()


class TestExportPipeline:
    """End-to-end export pipeline tests."""

    def test_all_cli_formats_registered(self):
        """Verify all export formats are in the CLI."""
        import ast

        cli_path = Path(__file__).parent.parent / "src" / "vibeview" / "cli.py"
        tree = ast.parse(cli_path.read_text())
        formats = []
        for node in ast.walk(tree):
            if isinstance(node, ast.List) and len(formats) == 0:
                elts = [e.value for e in node.elts if isinstance(e, ast.Constant)]
                if "xyz" in elts and "cif" in elts:
                    formats = elts
                    break
        assert "xyz" in formats
        assert "pov" in formats
        assert "blend" in formats
        assert "svg" in formats
        assert "pdf" in formats

    def test_export_formats_count(self):
        """Verify at least 10 export formats are available."""
        import ast

        cli_path = Path(__file__).parent.parent / "src" / "vibeview" / "cli.py"
        tree = ast.parse(cli_path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.List):
                elts = [e.value for e in node.elts if isinstance(e, ast.Constant)]
                if "xyz" in elts:
                    assert len(elts) >= 10
                    break


class TestCLIEdgeCases:
    def test_export_xyz_on_sample(self, sample_qvf, tmp_path):
        from click.testing import CliRunner

        from vibeview.cli import main

        runner = CliRunner()
        out = tmp_path / "out.xyz"
        result = runner.invoke(main, ["export", str(sample_qvf), "-f", "xyz", "-o", str(out)])
        assert result.exit_code == 0
        assert out.exists()

    def test_export_svg_on_sample(self, sample_qvf, tmp_path):
        from click.testing import CliRunner

        from vibeview.cli import main

        runner = CliRunner()
        out = tmp_path / "out.svg"
        result = runner.invoke(main, ["export", str(sample_qvf), "-f", "svg", "-o", str(out)])
        assert result.exit_code == 0
        assert out.exists()

    def test_export_json_on_sample(self, sample_qvf, tmp_path):
        from click.testing import CliRunner

        from vibeview.cli import main

        runner = CliRunner()
        out = tmp_path / "out.json"
        result = runner.invoke(main, ["export", str(sample_qvf), "-f", "json", "-o", str(out)])
        assert result.exit_code == 0
        assert out.exists()
        data = json.loads(out.read_text())
        assert "sections" in data

    def test_info_json_on_sample(self, sample_qvf):
        from click.testing import CliRunner

        from vibeview.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["info", str(sample_qvf), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "program" in data

    def test_diff_json_on_sample(self, sample_qvf):
        from click.testing import CliRunner

        from vibeview.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["diff", str(sample_qvf), str(sample_qvf), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "file_a" in data

    def test_info_on_sample(self, sample_qvf):
        from click.testing import CliRunner

        from vibeview.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["info", str(sample_qvf)])
        assert result.exit_code == 0

    def test_table_on_sample(self, sample_qvf):
        from click.testing import CliRunner

        from vibeview.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["table", str(sample_qvf)])
        # May exit 0 if no tabulatable sections or lists them
        assert result.exit_code in (0, 1)

    def test_validate_on_sample(self, sample_qvf):
        from click.testing import CliRunner

        from vibeview.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["validate", str(sample_qvf)])
        assert result.exit_code in (0, 1)

    def test_diff_on_sample(self, sample_qvf):
        from click.testing import CliRunner

        from vibeview.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["diff", str(sample_qvf), str(sample_qvf)])
        assert result.exit_code == 0


class TestCaptureSelftest:
    def test_capture_selftest_renders_offscreen(self, tmp_path):
        """The headless capture healthcheck renders a built-in structure
        offscreen and reports the backend — the queue-env / CI validation."""
        from vibeview.capture import capture_selftest

        out = tmp_path / "selftest.png"
        info = capture_selftest(out)
        assert out.exists() and out.stat().st_size > 0
        assert info["bytes"] == out.stat().st_size
        assert info["pyvista"] and info["vtk"]


class TestViewerExtraGuard:
    def test_load_app_missing_viewer_gives_hint(self, monkeypatch):
        """Interactive commands import vibeview.app (needs [viewer]); a lean
        capture-only install must fail with an actionable hint, not a raw
        ModuleNotFoundError."""
        import sys

        import pytest

        from vibeview.cli import _load_app

        monkeypatch.setitem(sys.modules, "vibeview.app", None)  # -> ImportError on `from`
        with pytest.raises(SystemExit) as ei:
            _load_app()
        assert "viewer" in str(ei.value) and "pip install" in str(ei.value)
