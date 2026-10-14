"""Tests for viewer_defaults parsing and ViewerState."""

from __future__ import annotations

import pytest

from vibeview.qvf import Manifest, ViewerDefaults
from vibeview.viewer_defaults import ViewerState, VolumeHints


class TestViewerStateFromManifest:
    @pytest.mark.parametrize("node_key", ["color", "opacity"])
    @pytest.mark.parametrize(
        "nodes",
        [
            pytest.param(3, id="integer"),
            pytest.param(0.5, id="float"),
            pytest.param(True, id="boolean"),
            pytest.param("invalid", id="string"),
            pytest.param({"unexpected": [0, 1]}, id="object"),
            pytest.param(None, id="null"),
        ],
    )
    def test_malformed_transfer_function_nodes_are_advisory(
        self, node_key: str, nodes: object
    ) -> None:
        transfer_function = {
            "range": [0, 1],
            "color": [[0, 0, 0, 0], [1, 1, 1, 1]],
            "opacity": [[0, 0], [1, 1]],
            node_key: nodes,
        }
        defaults = ViewerDefaults.model_validate(
            {
                "auto_open": ["density"],
                "density": {
                    "render": "volume",
                    "transfer_function": transfer_function,
                    "isovalue": 0.03,
                },
                "other": {"opacity": 0.7},
            }
        )
        original = defaults.model_dump_json()

        state = ViewerState.from_manifest(defaults)

        assert state.get_volume_hints("density").transfer_function is None
        assert state.get_volume_hints("density").render == "volume"
        assert state.get_volume_hints("density").isovalue == 0.03
        assert state.get_volume_hints("other").opacity == 0.7
        assert state.auto_open == ["density"]
        assert ViewerState.from_manifest(defaults) == state
        assert defaults.model_dump_json() == original

    @pytest.mark.parametrize("container", [list, tuple])
    def test_transfer_function_node_sequences_still_sort_and_filter(self, container) -> None:
        defaults = ViewerDefaults.model_validate(
            {
                "density": {
                    "transfer_function": {
                        "range": [0, 1],
                        "color": container([[1, 1, 1, 1], None, [0, 0, 0, 0]]),
                        "opacity": container([[1, 1], "invalid", [0, 0]]),
                        "colormap": "custom",
                    }
                }
            }
        )
        original = defaults.model_dump_json()

        state = ViewerState.from_manifest(defaults)

        assert state.get_volume_hints("density").transfer_function == {
            "range": [0.0, 1.0],
            "color": [[0.0, 0.0, 0.0, 0.0], [1.0, 1.0, 1.0, 1.0]],
            "opacity": [[0.0, 0.0], [1.0, 1.0]],
            "colormap": "custom",
        }
        assert ViewerState.from_manifest(defaults) == state
        assert defaults.model_dump_json() == original

    @pytest.mark.parametrize(
        ("blend_hint", "expected"),
        [
            pytest.param({"blend": "invalid"}, 0.5, id="invalid-string"),
            pytest.param({"blend": None}, 0.5, id="null"),
            pytest.param({"blend": []}, 0.5, id="list"),
            pytest.param({"blend": {}}, 0.5, id="object"),
            pytest.param({"blend": float("nan")}, 0.5, id="nan"),
            pytest.param({"blend": float("inf")}, 0.5, id="infinity"),
            pytest.param({"blend": -float("inf")}, 0.5, id="negative-infinity"),
            pytest.param({"blend": 10**400}, 0.5, id="overflow"),
            pytest.param({"blend": -0.1}, 0.5, id="below-slider-range"),
            pytest.param({"blend": 1.1}, 0.5, id="above-slider-range"),
            pytest.param({"blend": 0.0}, 0.0, id="first-volume"),
            pytest.param({"blend": 1.0}, 1.0, id="second-volume"),
            pytest.param({"blend": 0.3}, 0.3, id="valid-blend"),
            pytest.param({"blend": "0.25"}, 0.25, id="numeric-string"),
            pytest.param({}, 0.5, id="omitted"),
        ],
    )
    def test_crossfade_blend_is_advisory(
        self, blend_hint: dict[str, object], expected: float
    ) -> None:
        defaults = ViewerDefaults.model_validate(
            {
                "auto_open": ["density"],
                "camera": {"position": [1, 2, 3]},
                "crossfade": {"a": "v1", "b": "v2", **blend_hint},
                "density": {"isovalue": 0.03, "opacity": 0.7},
            }
        )
        original = defaults.model_dump_json()

        state = ViewerState.from_manifest(defaults)

        assert state.crossfade_volumes == ("v1", "v2")
        assert state.crossfade_blend == expected
        assert state.auto_open == ["density"]
        assert state.camera == {"position": [1, 2, 3]}
        assert state.volume_hints["density"].isovalue == 0.03
        assert state.volume_hints["density"].opacity == 0.7
        assert ViewerState.from_manifest(defaults) == state
        assert defaults.model_dump_json() == original

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

    @pytest.mark.parametrize("separate_state", [False, True], ids=["section", "state"])
    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            ("volume.difference", (0.005, "RdBu", 0.6)),
            ("volume.spin", (0.005, "RdBu", 0.6)),
            ("volume.density", (0.05, "viridis", 0.6)),
            ("volume.orbital", (0.05, "coolwarm", 0.6)),
            ("volume.elf", (0.5, "plasma", 0.6)),
            ("volume.potential", (0.03, "coolwarm", 0.6)),
            ("volume.rdg", (0.3, "coolwarm", 0.65)),
            ("volume.generic", (0.05, "viridis", 0.6)),
            ("", (0.05, "viridis", 0.6)),
        ],
    )
    def test_volume_edits_are_isolated(
        self, kind: str, expected: tuple[float, str, float], separate_state: bool
    ) -> None:
        state = ViewerState.from_manifest(None)
        other = ViewerState.from_manifest(None) if separate_state else state
        edited = state.get_volume_hints("edited", kind=kind)
        existing = other.get_volume_hints("existing", kind=kind)

        edited.isovalue = 0.123
        edited.colormap = "magma"
        edited.opacity = 0.25

        # Existing and newly opened sections retain independently specified
        # defaults, including when a new file creates another ViewerState.
        later = other.get_volume_hints("later", kind=kind)
        for hints in (existing, later):
            assert (hints.isovalue, hints.colormap, hints.opacity) == expected
            assert hints is not edited
        assert state.get_volume_hints("edited", kind=kind) is edited
        assert (edited.isovalue, edited.colormap, edited.opacity) == (0.123, "magma", 0.25)


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
        # The merge happens once, at construction, from the section kinds (#44).
        state = ViewerState.from_manifest(defaults, {"diff": "volume.difference"})
        hints = state.get_volume_hints("diff")
        assert hints.opacity == 0.8        # provided
        assert hints.isovalue == 0.005     # kind default, not 0.05
        assert hints.colormap == "RdBu"    # kind default, not viridis

    def test_full_hint_overrides_kind_default(self) -> None:
        from vibeview.qvf import ViewerDefaults

        defaults = ViewerDefaults.model_validate(
            {"auto_open": [], "spin": {"isovalue": 0.02, "colormap": "seismic", "opacity": 0.5}}
        )
        state = ViewerState.from_manifest(defaults, {"spin": "volume.spin"})
        hints = state.get_volume_hints("spin")
        assert (hints.isovalue, hints.colormap, hints.opacity) == (0.02, "seismic", 0.5)

    @pytest.mark.parametrize("separate_state", [False, True], ids=["section", "state"])
    def test_partial_hints_ignore_edits_to_another_volume(self, separate_state: bool) -> None:
        defaults = ViewerDefaults.model_validate({"partial": {"opacity": 0.8}})
        kinds = {"partial": "volume.difference", "edited": "volume.difference"}
        state = ViewerState.from_manifest(defaults, kinds)
        other = ViewerState.from_manifest(defaults, kinds) if separate_state else state
        edited = state.get_volume_hints("edited", kind="volume.difference")
        edited.isovalue = 0.123
        edited.colormap = "magma"
        edited.opacity = 0.25

        partial = other.get_volume_hints("partial", kind="volume.difference")
        assert (partial.isovalue, partial.colormap, partial.opacity) == (0.005, "RdBu", 0.8)
        partial.isovalue = 0.02
        assert other.get_volume_hints("partial", kind="volume.difference") is partial
        assert partial.isovalue == 0.02
        fresh = ViewerState.from_manifest(defaults, kinds).get_volume_hints("partial")
        assert (fresh.isovalue, fresh.colormap, fresh.opacity) == (0.005, "RdBu", 0.8)


class TestReplicationHint:
    def test_first_section_with_a_replication_hint_wins(self) -> None:
        """#35-33: the loop let the last section overwrite the global supercell."""
        from vibeview.qvf import ViewerDefaults

        defaults = ViewerDefaults.model_validate(
            {"a": {"replication": [2, 2, 1]}, "b": {"replication": [3, 3, 3]}}
        )
        assert ViewerState.from_manifest(defaults).replication == (2, 2, 1)


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
