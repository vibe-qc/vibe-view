"""Trajectory and reaction-path animation helpers."""

from __future__ import annotations


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


class TestAnimationToolsIntegration:
    def test_turntable_import(self):
        from vibeview.animation import render_turntable

        assert callable(render_turntable)

    def test_video_export_formats(self):
        from vibeview.animation import (
            render_orbital_animation,
            render_trajectory_video,
            render_vibration_video,
        )

        assert callable(render_trajectory_video)
        assert callable(render_vibration_video)
        assert callable(render_orbital_animation)
