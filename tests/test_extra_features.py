"""Tests for file watcher and CML export."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


class TestFileWatcher:
    def test_import(self):
        from vibeview.file_watcher import QVFFileWatcher, watch_qvf

        assert QVFFileWatcher is not None
        assert callable(watch_qvf)

    def test_watcher_create(self, tmp_path):
        from vibeview.file_watcher import QVFFileWatcher

        f = tmp_path / "test.qvf"
        f.write_text("test")
        calls = []
        w = QVFFileWatcher(str(f), lambda p: calls.append(p), interval=0.1)
        assert not w.is_running
        w.start()
        assert w.is_running
        w.stop()
        assert not w.is_running

    def test_watcher_detect_change(self, tmp_path):
        from vibeview.file_watcher import QVFFileWatcher

        f = tmp_path / "test.qvf"
        f.write_text("initial")
        calls = []
        # settle_delay: the hardened watcher waits for the file to stop
        # moving before firing (see test_file_watcher.py for full coverage).
        w = QVFFileWatcher(str(f), lambda p: calls.append(p), interval=0.05, settle_delay=0.05)
        w.start()
        import time

        time.sleep(0.15)
        f.write_text("modified")
        deadline = time.monotonic() + 3.0
        while not calls and time.monotonic() < deadline:
            time.sleep(0.05)
        w.stop()
        assert len(calls) >= 1

    def test_watcher_stop_idempotent(self, tmp_path):
        from vibeview.file_watcher import QVFFileWatcher

        f = tmp_path / "test.qvf"
        f.write_text("test")
        w = QVFFileWatcher(str(f), lambda p: None)
        w.start()
        w.stop()
        w.stop()  # Should not raise


class TestCMLExport:
    def test_import(self):
        from vibeview.export_cml import export_cml

        assert callable(export_cml)

    def test_export_creates_file(self, sample_qvf, tmp_path):
        from vibeview.export_cml import export_cml
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        out = tmp_path / "test.cml"
        cml = export_cml(reader, str(out))
        assert out.exists()
        content = out.read_text()
        assert "<cml" in content
        assert "atomArray" in content
        reader.close()

    def test_export_valid_xml(self, sample_qvf, tmp_path):
        from xml.etree import ElementTree as ET

        from vibeview.export_cml import export_cml
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        out = tmp_path / "test.cml"
        cml = export_cml(reader, str(out))
        # Should parse as valid XML
        tree = ET.parse(out)
        assert tree.getroot().tag.endswith("cml")
        reader.close()
