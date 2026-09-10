"""Final batch of tests to push vibe-view past 600 (v2.x)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest


class TestWavefunctionRenderer:
    def test_frontier_indices(self):
        from vibeview.renderers.wavefunction import _frontier_indices

        occs = [2.0, 2.0, 1.0, 0.0, 0.0]
        homo, lumo = _frontier_indices(occs, 5)
        assert homo == 2
        assert lumo == 3

    def test_frontier_indices_no_occ(self):
        from vibeview.renderers.wavefunction import _frontier_indices

        homo, lumo = _frontier_indices(None, 5)
        assert homo == -1
        assert lumo == -1

    def test_frontier_indices_empty(self):
        from vibeview.renderers.wavefunction import _frontier_indices

        homo, lumo = _frontier_indices([], 5)
        assert homo == -1
        assert lumo == -1

    def test_frontier_indices_partially_occupied(self):
        from vibeview.renderers.wavefunction import _frontier_indices

        # All MOs occupied (up to n_mo-1)
        homo, lumo = _frontier_indices([2.0, 2.0, 2.0], 3)
        assert homo == 2
        assert lumo == -1  # no virtuals

    def test_frontier_indices_all_virtual(self):
        from vibeview.renderers.wavefunction import _frontier_indices

        # No occupied MOs
        homo, lumo = _frontier_indices([0.0, 0.0, 0.0], 3)
        assert homo == -1
        assert lumo == 0  # first virtual is LUMO

    def test_mo_row_title(self):
        from vibeview.renderers.wavefunction import _mo_row_title

        title = _mo_row_title("restricted", 5, -0.314, 2.0, "a1", "HOMO")
        assert "#5" in title
        assert "HOMO" in title
        assert "a1" in title

    def test_mo_row_title_no_sym_label(self):
        from vibeview.renderers.wavefunction import _mo_row_title

        title = _mo_row_title("alpha", 3, -0.200, 1.0, "", "LUMO")
        assert "#3" in title
        assert "LUMO" in title

    def test_spin_short_labels(self):
        from vibeview.renderers.wavefunction import _SPIN_SHORT

        assert _SPIN_SHORT["restricted"] == ""
        assert _SPIN_SHORT["alpha"] == "\u03b1"
        assert _SPIN_SHORT["beta"] == "\u03b2"


class TestDOSRenderer:
    def test_dos_import(self):
        from vibeview.renderers.dos import DOSRenderer

        assert DOSRenderer is not None

    def test_dos_renderer_class(self):
        from vibeview.renderers import BaseRenderer
        from vibeview.renderers.dos import DOSRenderer

        assert issubclass(DOSRenderer, BaseRenderer)


class TestStructureRenderer:
    def test_bond_order_color_returns_hex_string(self):
        from vibeview.renderers.structure import _bond_order_color

        c = _bond_order_color(0.5)
        assert c.startswith("#"), f"Expected hex color, got {c!r}"
        c = _bond_order_color(1.5)
        assert c.startswith("#")
        c = _bond_order_color(2.5)
        assert c.startswith("#")

    def test_cpk_radius_positive(self):
        from vibeview.renderers.structure import cpk_radius

        assert cpk_radius(6) > 0  # Carbon
        assert cpk_radius(1) > 0  # Hydrogen
        assert cpk_radius(26) > 0  # Iron

    def test_cpk_radius_with_scale(self):
        from vibeview.renderers.structure import cpk_radius

        r1 = cpk_radius(6)
        r2 = cpk_radius(6, scale=2.0)
        assert r2 == pytest.approx(r1 * 2.0)

    def test_cpk_color_returns_hex_string(self):
        from vibeview.renderers.structure import cpk_color

        c = cpk_color(6)
        assert c.startswith("#"), f"Expected hex color, got {c!r}"

    def test_cpk_color_unknown_element(self):
        from vibeview.renderers.structure import _DEFAULT_COLOR, cpk_color

        c = cpk_color(999)
        assert c == _DEFAULT_COLOR

    def test_default_color_constant(self):
        from vibeview.renderers.structure import _DEFAULT_COLOR

        assert _DEFAULT_COLOR.startswith("#")


class TestKindsModule:
    def test_classify_section_rendered(self):
        from vibeview.kinds import classify_section

        status, detail = classify_section("volume.density")
        assert status == "rendered"
        assert detail is None

    def test_classify_structure_rendered(self):
        from vibeview.kinds import classify_section

        status, detail = classify_section("structure")
        assert status == "rendered"
        assert detail is None

    def test_classify_unknown_returns_skipped(self):
        from vibeview.kinds import classify_section

        status, detail = classify_section("nonexistent.kind")
        assert status == "skipped"
        assert detail is not None

    def test_classify_vendor_namespace(self):
        from vibeview.kinds import classify_section

        status, detail = classify_section("vendor.something")
        assert status in ("skipped", "error")


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


class TestViewerDefaults:
    def test_viewer_state_defaults(self):
        from vibeview.viewer_defaults import ViewerState

        state = ViewerState()
        assert state.auto_open == []
        assert state.replication == (1, 1, 1)
        assert state.volume_hints == {}

    def test_viewer_state_get_volume_hints_default(self):
        from vibeview.viewer_defaults import ViewerState, VolumeHints

        state = ViewerState()
        hints = state.get_volume_hints("test_section")
        assert isinstance(hints, VolumeHints)
        assert hints.isovalue is not None
        assert hints.colormap is not None

    def test_mesh_cache_key_method(self):
        from vibeview.viewer_defaults import ViewerState

        state = ViewerState()
        key = state.mesh_cache_key(0.05, periodic_replication=0)
        assert isinstance(key, str)

    def test_mesh_cache_key_with_replication(self):
        from vibeview.viewer_defaults import ViewerState

        state = ViewerState()
        key1 = state.mesh_cache_key(0.05, periodic_replication=0)
        key2 = state.mesh_cache_key(0.05, periodic_replication=1)
        assert key1 != key2

    def test_volume_hints_default_values(self):
        from vibeview.viewer_defaults import VolumeHints

        hints = VolumeHints()
        assert hints.isovalue == 0.05
        assert hints.colormap == "viridis"
        assert hints.opacity == 0.6


class TestAlignModule:
    def test_rmsd_identical(self):
        from vibeview.align import rmsd

        pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
        assert rmsd(pos, pos) == pytest.approx(0.0)

    def test_rmsd_shifted(self):
        from vibeview.align import rmsd

        # This rmsd() is index-wise, no superposition — shifted != identical
        pos_a = np.array([[0, 0, 0], [1, 0, 0]], dtype=float)
        pos_b = np.array([[1, 0, 0], [2, 0, 0]], dtype=float)
        r = rmsd(pos_a, pos_b)
        # Each atom pair offset by (1,0,0) -> per-pair squared dist = 1.0
        assert r == pytest.approx(1.0)

    def test_rmsd_different(self):
        from vibeview.align import rmsd

        pos_a = np.array([[0, 0, 0], [1, 0, 0]], dtype=float)
        pos_b = np.array([[0, 0, 0], [0, 1, 0]], dtype=float)
        r = rmsd(pos_a, pos_b)
        assert r > 0.0

    def test_rmsd_shape_mismatch_raises(self):
        from vibeview.align import rmsd

        pos_a = np.array([[0, 0, 0], [1, 0, 0]], dtype=float)
        pos_b = np.array([[0, 0, 0]], dtype=float)
        with pytest.raises(ValueError):
            rmsd(pos_a, pos_b)


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


class TestConvertersExtended:
    def test_symbol_to_z(self):
        from vibeview.converters import _SYMBOL_TO_Z

        assert _SYMBOL_TO_Z["H"] == 1
        assert _SYMBOL_TO_Z["C"] == 6
        assert _SYMBOL_TO_Z["O"] == 8
        assert _SYMBOL_TO_Z["He"] == 2

    def test_xyz_to_qvf_with_helium(self):
        from vibeview.converters import xyz_to_qvf

        xyz = b"1\n\nHe  0.0  0.0  0.0\n"
        buf = xyz_to_qvf(xyz)
        import zipfile

        zf = zipfile.ZipFile(buf)
        assert "manifest.json" in zf.namelist()

    def test_xyz_to_qvf_water(self):
        from vibeview.converters import xyz_to_qvf

        xyz = b"3\n\nO   0.000   0.000   0.117\nH   0.000   0.757  -0.469\nH   0.000  -0.757  -0.469\n"
        buf = xyz_to_qvf(xyz)
        import zipfile

        zf = zipfile.ZipFile(buf)
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["qvf_version"] == 1


class TestBannerModule:
    def test_print_banner(self, sample_qvf, capsys):
        from vibeview.banner import print_banner
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        print_banner(reader)
        captured = capsys.readouterr()
        assert "vibe-view" in captured.out or "vibe-qc" in captured.out
        reader.close()


class TestExportHTMLExtended:
    def test_export_html_import(self):
        from vibeview.export_html import export_html

        assert callable(export_html)

    def test_export_html_creates_file(self, sample_qvf, tmp_path):
        from vibeview.export_html import export_html
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        out = tmp_path / "test.html"
        export_html(reader, out)
        assert out.exists()
        reader.close()


class TestCaptureModule:
    def test_capture_structure_import(self):
        from vibeview.api import capture_structure

        assert callable(capture_structure)

    def test_capture_volume_import(self):
        from vibeview.api import capture_volume

        assert callable(capture_volume)


class TestLauncherModule:
    def test_launch_import(self):
        from vibeview.launcher import launch_qvf

        assert callable(launch_qvf)
