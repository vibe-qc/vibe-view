"""The `vibe-view` launcher module."""

from __future__ import annotations


class TestLauncherModule:
    def test_launch_import(self):
        from vibeview.launcher import launch_qvf

        assert callable(launch_qvf)
