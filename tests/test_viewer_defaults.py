"""Tests for viewer_defaults parsing and ViewerState."""

from __future__ import annotations

from vibeview.qvf import Manifest, ViewerDefaults
from vibeview.viewer_defaults import ViewerState, VolumeHints


class TestViewerStateFromManifest:
    def test_none_defaults_returns_empty_state(self) -> None:
        state = ViewerState.from_manifest(None)
        assert state.auto_open == []
        assert state.replication == (1, 1, 1)
        assert state.volume_hints == {}

    def test_empty_defaults_returns_empty_state(self) -> None:
        state = ViewerState.from_manifest(ViewerDefaults(auto_open=[]))
        assert state.auto_open == []
        assert state.replication == (1, 1, 1)

    def test_auto_open_is_captured(self) -> None:
        defaults = ViewerDefaults(auto_open=["density", "homo"])
        state = ViewerState.from_manifest(defaults)
        assert state.auto_open == ["density", "homo"]

    def test_per_section_volume_hints_are_captured(self) -> None:
        manifest_dict = {
            "qvf_version": 1,
            "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": "test"},
            "sections": [],
            "viewer_defaults": {
                "auto_open": ["density"],
                "density": {"isovalue": 0.05, "colormap": "plasma", "opacity": 0.7},
            },
        }
        manifest = Manifest.model_validate(manifest_dict)
        defaults = manifest.viewer_defaults
        assert defaults is not None

        state = ViewerState.from_manifest(defaults)
        assert state.auto_open == ["density"]
        assert "density" in state.volume_hints
        hints = state.volume_hints["density"]
        assert hints.isovalue == 0.05
        assert hints.colormap == "plasma"
        assert hints.opacity == 0.7

    def test_replication_hint_is_captured(self) -> None:
        manifest_dict = {
            "qvf_version": 1,
            "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": "test"},
            "sections": [],
            "viewer_defaults": {
                "auto_open": [],
                "density": {"isovalue": 0.03, "replication": [2, 2, 1]},
            },
        }
        manifest = Manifest.model_validate(manifest_dict)
        defaults = manifest.viewer_defaults
        assert defaults is not None

        state = ViewerState.from_manifest(defaults)
        assert state.replication == (2, 2, 1)
        assert "density" in state.volume_hints
        assert state.volume_hints["density"].isovalue == 0.03

    def test_multiple_sections_replication_last_wins(self) -> None:
        manifest_dict = {
            "qvf_version": 1,
            "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": "test"},
            "sections": [],
            "viewer_defaults": {
                "density": {"replication": [3, 3, 1]},
                "homo": {"replication": [2, 2, 2]},
            },
        }
        manifest = Manifest.model_validate(manifest_dict)
        defaults = manifest.viewer_defaults
        assert defaults is not None

        state = ViewerState.from_manifest(defaults)
        # Last one wins (iteration order of model_extra dict)
        assert len(state.volume_hints) == 2


class TestVolumeHintsDefaults:
    def test_default_values(self) -> None:
        hints = VolumeHints()
        assert hints.isovalue == 0.05
        assert hints.colormap == "viridis"
        assert hints.opacity == 0.6


class TestViewerStateGetVolumeHints:
    def test_returns_existing_hints(self) -> None:
        state = ViewerState()
        state.volume_hints["density"] = VolumeHints(isovalue=0.1)
        hints = state.get_volume_hints("density")
        assert hints.isovalue == 0.1

    def test_creates_default_hints_for_unknown_section(self) -> None:
        state = ViewerState()
        hints = state.get_volume_hints("unknown")
        assert hints.isovalue == 0.05
        assert hints.colormap == "viridis"
        assert hints.opacity == 0.6
        # Should also have been stored
        assert "unknown" in state.volume_hints


class TestKindDefaultMerge:
    """A partial manifest hint must not erase the kind-specific defaults
    (audit: viewer_defaults kind-default loss)."""

    def test_partial_hint_keeps_kind_default_for_unset_keys(self) -> None:
        from vibeview.qvf import ViewerDefaults

        # Difference volume: manifest only sets opacity. isovalue + colormap
        # must still come from the volume.difference kind default (0.005/RdBu),
        # NOT the generic 0.05/viridis.
        defaults = ViewerDefaults.model_validate(
            {"auto_open": [], "diff": {"opacity": 0.8}}
        )
        state = ViewerState.from_manifest(defaults)
        hints = state.get_volume_hints("diff", kind="volume.difference")
        assert hints.opacity == 0.8        # provided
        assert hints.isovalue == 0.005     # kind default, not 0.05
        assert hints.colormap == "RdBu"    # kind default, not viridis

    def test_full_hint_overrides_kind_default(self) -> None:
        from vibeview.qvf import ViewerDefaults

        defaults = ViewerDefaults.model_validate(
            {"auto_open": [], "spin": {"isovalue": 0.02, "colormap": "seismic", "opacity": 0.5}}
        )
        state = ViewerState.from_manifest(defaults)
        hints = state.get_volume_hints("spin", kind="volume.spin")
        assert (hints.isovalue, hints.colormap, hints.opacity) == (0.02, "seismic", 0.5)


class TestFromManifestIdempotent:
    def test_second_call_keeps_all_hints(self) -> None:
        """from_manifest must not mutate the pydantic model_extra in place —
        a second call on the same manifest (app._reload_active_file) used
        to lose camera, bookmarks, crossfade, and section hints."""
        defaults = ViewerDefaults.model_validate(
            {
                "auto_open": ["d"],
                "camera": {"position": [1, 2, 3]},
                "bookmarks": [{"name": "b1", "camera": {"position": [0, 0, 1]}}],
                "crossfade": {"a": "v1", "b": "v2", "blend": 0.3},
                "d": {"isovalue": 0.01},
            }
        )
        first = ViewerState.from_manifest(defaults)
        second = ViewerState.from_manifest(defaults)

        assert second.camera == first.camera == {"position": [1, 2, 3]}
        assert len(second.bookmarks) == 1
        assert second.bookmarks[0].name == "b1"
        assert second.crossfade_volumes == ("v1", "v2")
        assert second.crossfade_blend == 0.3
        assert "d" in second.volume_hints
        assert second.volume_hints["d"].isovalue == 0.01
