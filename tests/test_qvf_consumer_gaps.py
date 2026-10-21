"""Consumer regressions from the master's v0.3.0 corpus (vibe-view #91)."""

import json

import numpy as np
import pytest

from tests._qvf_builders import build_qvf
from tests.test_natural_transition_orbitals import _archive
from tests.test_qvf_schema_identity import _conformance_module
from vibeview.qvf import QVFError, QVFReader, ViewerDefaults
from vibeview.renderers.vibrations import VibrationsRenderer
from vibeview.renderers.wavefunction import WavefunctionRenderer, frontier_orbitals
from vibeview.viewer_defaults import ViewerState


def _vibrations(tmp_path, *, reference=None, symbols=None, structure=True):
    atoms = [{"symbol": "H", "atomic_number": 1, "position": [0., 0., 1.]}]
    other = [{**atoms[0], "position": [3., 0., 1.]}]
    sections = []
    if structure:
        for name, value in (("first", atoms), ("second", other)):
            data = json.dumps({"atoms": value, "pbc": [False] * 3}).encode()
            sections.append((name, "structure", {"structure": ("json", f"{name}.json", data)}))
    meta = {"frequencies": [100.]}
    if symbols is not None:
        meta["symbols"] = symbols
    sections.append(("vib", "vibrations", {
        "metadata": ("json", "vib.json", json.dumps(meta).encode()),
        "displacements": ("binary", "d.bin", np.ones((1, 1, 3), dtype="<f8").tobytes(),
                          "float64", [1, 1, 3]),
    }))
    extra = {"vib": {"structure_ref": reference}} if reference else None
    return build_qvf(tmp_path / "vibrations.qvf", sections, extra=extra)


@pytest.mark.parametrize("reference,expected", [(None, [0, 0, 1]), ("second", [3, 0, 1])])
def test_optional_vibration_atoms_use_archived_reference(tmp_path, reference, expected):
    with QVFReader(_vibrations(tmp_path, reference=reference, symbols=["H"])) as reader:
        reader.show_structure("second")
        reader.set_edit_overlay([[9, 9, 9]], ["H"], section_id="first")
        reader.set_edit_overlay([[8, 8, 8]], ["H"], section_id="second")
        data = reader.read_vibrations("vib")
        np.testing.assert_array_equal(data.atoms[0].position, expected)
        renderer = VibrationsRenderer(reader.get_section("vib"), reader)
        np.testing.assert_array_equal(renderer.get_mode(0)[1], [expected])


@pytest.mark.parametrize("options,match", [
    ({"structure": False}, "reference structure"),
    ({"symbols": ["O"]}, "symbols do not match"),
])
def test_missing_or_inconsistent_vibration_identity_is_a_payload_error(tmp_path, options, match):
    with (
        QVFReader(_vibrations(tmp_path, **options)) as reader,
        pytest.raises(QVFError, match=match),
    ):
        reader.read_vibrations("vib")


@pytest.mark.parametrize("kind", ["localized", "ibo", "iao", "boys", "pipek_mezey", "nbo"])
def test_localized_kind_does_not_name_frontiers_or_fake_eigenvalues(tmp_path, kind):
    path = _archive(tmp_path, {"orbital_kind": kind, "occupations": [2, 0]})
    with QVFReader(path) as reader:
        renderer = WavefunctionRenderer(reader.get_section("nto"), reader)
        assert renderer.load().orbital_kind == kind
        rows = renderer.mo_table()
        assert len(rows) == 2
        assert all("HOMO" not in row["title"] and "LUMO" not in row["title"] for row in rows)
        assert all(row["energy_eh"] is None for row in rows)
        assert "not energy ordered" in renderer.render_energy_diagram()
        homo, lumo, reason = frontier_orbitals(np.array([2, 0]), 2, orbital_kind=kind)
        assert homo is None and lumo is None
        assert "localized" in reason


CAMERA = {"position": [1, 2, 3], "focal_point": [0, 0, 0],
          "view_up": [0, 1, 0], "view_angle": 30}


@pytest.mark.parametrize("camera", [
    None, [], "bad", {}, {"position": [1, 2, 3]},
    {**CAMERA, "position": [1, 2]}, {**CAMERA, "view_up": "up"},
    {**CAMERA, "position": [1, float("nan"), 3]},
    {**CAMERA, "view_angle": 180}, {**CAMERA, "view_angle": 0},
    {**CAMERA, "parallel_scale": 3}, {**CAMERA, "unexpected": 2},
    {**CAMERA, "view_up": [0, 10**400, 0]},
])
def test_malformed_camera_is_ignored_without_losing_other_hints(camera):
    defaults = ViewerDefaults.model_validate({"camera": camera, "density": {"opacity": 0.7}})
    state = ViewerState.from_manifest(defaults)
    assert state.camera is None
    assert state.get_volume_hints("density").opacity == 0.7


def test_camera_bookmark_fallback_and_independent_copy():
    defaults = ViewerDefaults.model_validate({"camera": "invalid", "bookmarks": [
        {"name": "bad", "camera": {"position": [1, 2]}},
        {"name": "good", "camera": CAMERA},
    ]})
    state = ViewerState.from_manifest(defaults)
    assert state.camera == CAMERA
    assert len(state.bookmarks) == 1
    state.camera["position"][0] = 99
    assert ViewerState.from_manifest(defaults).camera == CAMERA


def test_orthographic_camera_still_works():
    camera = {key: value for key, value in CAMERA.items() if key != "view_angle"}
    camera["parallel_scale"] = 2
    assert ViewerState.from_manifest(ViewerDefaults(camera=camera)).camera == camera


def test_negative_audit_reports_rejections_gaps_and_crashes(tmp_path, monkeypatch):
    import vibeview

    root = tmp_path / "conformance" / "negative"
    root.mkdir(parents=True)
    for name in ("accepted", "exception", "report", "crash"):
        (root / f"{name}.qvf").touch()
        (root / f"{name}.expected.json").write_text(json.dumps({
            "expect": [{"code": "E_TEST", "severity": "error"}],
        }))

    def validate(path, *, limits=None):
        if path.stem == "exception":
            raise QVFError("expected rejection")
        if path.stem == "crash":
            raise KeyError("unexpected")
        return {"valid": path.stem == "accepted",
                "findings": [{"code": "E_TEST", "severity": "error"}]}

    monkeypatch.setattr(vibeview, "validate", validate)
    rejected, tried, gaps, failures = _conformance_module().read_negatives(tmp_path)
    assert (rejected, tried, gaps) == (1, 4, ["accepted.qvf: accepted a must-fail fixture"])
    assert len(failures) == 2
    assert any("KeyError" in f for f in failures)
    assert any("QVFError" in f for f in failures)


def test_empty_negative_corpus_is_a_failure(tmp_path):
    assert _conformance_module().read_negatives(tmp_path)[3]


def test_warning_only_fixture_is_not_required_to_fail(tmp_path):
    root = tmp_path / "conformance" / "negative"
    root.mkdir(parents=True)
    build_qvf(root / "warning.qvf", [])
    (root / "warning.expected.json").write_text(json.dumps({
        "expect": [{"code": "W_ORPHAN_MEMBER", "severity": "warning"}],
    }))
    matched, tried, gaps, failures = _conformance_module().read_negatives(tmp_path)
    assert (matched, tried, failures) == (0, 1, [])
    assert len(gaps) == 1 and "missing expected finding" in gaps[0]
    assert "must-fail" not in gaps[0]


def test_strict_negative_lane_fails_on_a_gap(tmp_path, monkeypatch):
    module = _conformance_module()
    monkeypatch.setattr(module, "check_vendored_against_qvf", lambda _: [])
    monkeypatch.setattr(module, "check_pinned_digests", lambda: [])
    monkeypatch.setattr(module, "read_archives", lambda _: (1, 1, []))
    monkeypatch.setattr(module, "read_negatives", lambda _: (1, 2, ["gap.qvf"], []))
    assert module.main(["--qvf-root", str(tmp_path)]) == 0
    assert module.main(["--qvf-root", str(tmp_path), "--strict-negatives"]) == 1
