"""Archive payload invariants before grid/frame data reaches viewer consumers."""

import hashlib
import io
import json
import zipfile

import numpy as np
import pytest

from vibeview.qvf import QVFError, QVFReader
from vibeview.renderers.reaction import ReactionPathRenderer
from vibeview.renderers.trajectory import TrajectoryRenderer
from vibeview.renderers.volume import build_isosurface_mesh

ATOMS = [{"symbol": "H", "atomic_number": 1}]
COORDS = np.array([[[0., 0., 0.]], [[1., 2., 3.]]])
GRID = {"origin": [0, 0, 0], "voxel_vectors": np.eye(3).tolist(), "shape": [2, 3, 4]}
FIELD = np.indices((2, 3, 4))[0].astype(np.float32)


def archive(kind="trajectory", *, metadata=None, coords=COORDS, grid=None,
            data=FIELD, waypoints=None, frame_volumes=None, version=1):
    blobs = {}

    def member(sid, name, value):
        binary = isinstance(value, np.ndarray)
        path = f"{sid}/{name}.dat" if binary else f"{sid}/{name}.json"
        raw = value.tobytes() if binary else json.dumps(value).encode()
        blobs[path] = raw
        result = {"path": path, "format": "binary" if binary else "json",
                  "sha256": hashlib.sha256(raw).hexdigest()}
        if binary:
            result.update(dtype=str(value.dtype), shape=list(value.shape))
        return result

    if kind == "volume.density":
        members = {"grid": member("data", "grid", GRID if grid is None else grid),
                   "data": member("data", "data", data)}
    else:
        members = {
            "metadata": member("data", "metadata", {"atoms": ATOMS}
                               if metadata is None else metadata),
            "coords": member("data", "coords", coords),
        }
        if frame_volumes is not None:
            members["frame_volumes"] = member("data", "frame_volumes", frame_volumes)
            members["volume_grid"] = member("data", "volume_grid", grid or GRID)
    sections = [{"id": "data", "kind": kind, "members": members}]
    if waypoints is not None:
        sections.append({"id": "annotations", "kind": "reaction.waypoints",
                         "trajectory_ref": "data", "members": {
                             "waypoints": member("annotations", "waypoints", waypoints)}})
    manifest = {"qvf_version": version, "source": {
        "program": "example", "version": "1", "calculation": "test"}, "sections": sections}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for path, raw in blobs.items():
            zf.writestr(path, raw)
    return out.getvalue()


def rejects(source, method, section="data", member=None):
    with QVFReader(source) as reader:
        with pytest.raises(QVFError) as error:
            getattr(reader, method)(section)
        assert section in str(error.value)
        if member:
            assert member in str(error.value)
        assert reader.section_error(section) == str(error.value)
        assert reader.section_error_kind(section) == "invalid payload"
        return str(error.value)


@pytest.mark.parametrize("patch", [
    {"origin": None}, {"origin": [0, 0]}, {"origin": [True, 0, 0]},
    {"origin": [float("nan"), 0, 0]}, {"voxel_vectors": [1, 1, 1]},
    {"voxel_vectors": [[1, 0, 0], [0, float("inf"), 0], [0, 0, 1]]},
    {"shape": [True, 3, 4]}, {"shape": [2.5, 3, 4]}, {"shape": [-2, 3, 4]},
    {"shape": [3, 2, 4]}, {"shape": [2, 3]},
])
@pytest.mark.parametrize("method", ["read_volume_grid", "read_volume_data"])
def test_grid_validation_cannot_be_bypassed(patch, method):
    rejects(archive("volume.density", grid={**GRID, **patch}), method, member="grid")


@pytest.mark.parametrize("kind,method", [
    ("trajectory", "read_trajectory"), ("reaction.path", "read_reaction_path"),
])
@pytest.mark.parametrize("metadata,coords", [
    ({}, COORDS), ({"atoms": [{}]}, COORDS), ({"atoms": [None]}, COORDS),
    ({"atoms": [{"symbol": "H", "atomic_number": True}]}, COORDS),
    ({"atoms": ATOMS * 2}, COORDS),
    ({"atoms": ATOMS}, np.concatenate([COORDS, COORDS + 10], axis=1)),
    ({"atoms": ATOMS}, COORDS[:, :, :2]),
    ({"atoms": ATOMS}, np.full((2, 1, 3), np.nan)),
    ({"atoms": ATOMS, "energies": [False, True]}, COORDS),
    ({"atoms": ATOMS, "energies": [0., float("inf")]}, COORDS),
])
def test_frame_payloads_reject_wrong_geometry_and_numbers(kind, method, metadata, coords):
    rejects(archive(kind, metadata=metadata, coords=coords), method)


@pytest.mark.parametrize("index", [True, -1, 0.9, "1", float("nan"), 2])
@pytest.mark.parametrize("annotation", [False, True])
def test_waypoint_indices_do_not_move_labels_to_another_frame(index, annotation):
    payload = {"waypoints": [{"frame_index": index, "label": "TS", "kind": "transition_state"}]}
    if annotation:
        source = archive(waypoints=payload)
        rejects(source, "read_reaction_waypoints", "annotations", "waypoints")
    else:
        source = archive("reaction.path", metadata={"atoms": ATOMS, **payload})
        rejects(source, "read_reaction_path", member="metadata")


@pytest.mark.parametrize("entry", [{"frame_index": 0, "kind": "point"},
                                     {"frame_index": 0, "label": "R", "kind": "point",
                                      "energy_eh": float("nan")}])
def test_waypoint_records_raise_contextual_errors(entry):
    rejects(archive(waypoints={"waypoints": [entry]}), "read_reaction_waypoints", "annotations")


@pytest.mark.parametrize("shape", [(0, 3, 4), (1, 3, 4)])
def test_empty_and_singleton_grid_dimensions_are_preserved(shape):
    with QVFReader(archive("volume.density", grid={**GRID, "shape": list(shape)},
                          data=np.zeros(shape, dtype=np.float32))) as reader:
        assert reader.read_volume_grid("data").shape == shape
        assert reader.read_volume_data("data").shape == shape


@pytest.mark.parametrize("vectors,x", [
    (np.eye(3), 0.2645886054515),
    (np.diag([-1, 1, 1]), -0.2645886054515),
    (np.array([[1, .2, 0], [0, 1, .3], [0, 0, 1]]), 0.2645886054515),
])
def test_real_contour_geometry_has_independently_expected_coordinates(vectors, x):
    source = archive("volume.density", grid={**GRID, "voxel_vectors": vectors.tolist()})
    with QVFReader(source) as r:
        mesh = build_isosurface_mesh(r.read_volume_data("data"), r.read_volume_grid("data"), .5)
        assert mesh.n_points == 12
        np.testing.assert_allclose(mesh.points[:, 0], x, atol=1e-7, rtol=0)


@pytest.mark.parametrize("energies", [None, [], [0.], [0., -1.]])
def test_optional_energy_prefix_and_actual_frame_consumer(energies):
    with QVFReader(archive(metadata={"atoms": ATOMS, "energies": energies})) as r:
        renderer = TrajectoryRenderer(r.get_section("data"), r)
        assert renderer.load().energies == energies
        np.testing.assert_array_equal(renderer.get_frame(1)[0][1], [1, 2, 3])
        if energies == [0.]:
            assert renderer.get_energy(0) == 0.
            assert renderer.get_energy(1) is None


@pytest.mark.parametrize("kind,method", [
    ("trajectory", "read_trajectory"), ("reaction.path", "read_reaction_path"),
])
def test_zero_frames_long_labels_and_unknown_elements_are_preserved(kind, method):
    atoms = [{"symbol": "unknown display label", "atomic_number": 999}]
    with QVFReader(archive(kind, metadata={"atoms": atoms}, coords=np.empty((0, 1, 3)))) as r:
        data = getattr(r, method)("data")
        assert data.coords.shape == (0, 1, 3)
        assert data.atoms[0].symbol == "unknown display label"
        assert data.atoms[0].atomic_number == 999


@pytest.mark.parametrize("version", [1, 2])
def test_integral_waypoints_and_optional_path_volumes_preserve_shared_geometry(version):
    meta = {"atoms": ATOMS, "waypoints": [{"frame_index": 1., "label": "", "kind": "custom"}]}
    source = archive("reaction.path", metadata=meta, frame_volumes=np.stack([FIELD, FIELD]),
                     version=version)
    with QVFReader(source) as r:
        renderer = ReactionPathRenderer(r.get_section("data"), r)
        assert renderer.waypoints()[0].frame_index == 1
        assert renderer.waypoints()[0].kind == "custom"
        assert renderer.load().volume_grid.shape == (2, 3, 4)
        np.testing.assert_array_equal(renderer.volume_data_for_frame(1), FIELD)


def test_path_volume_grid_is_checked_against_trailing_binary_dimensions():
    rejects(archive("reaction.path", frame_volumes=np.stack([FIELD, FIELD]),
                    grid={**GRID, "shape": [3, 2, 4]}), "read_reaction_path", member="volume_grid")


def test_success_cache_requires_immutable_backing_bytes(monkeypatch):
    with QVFReader(archive()) as r:
        coords = r._read_binary_member("data", "coords")
        assert not coords.flags.writeable
        with pytest.raises(ValueError):
            coords.setflags(write=True)
        scans = []
        original = np.isfinite

        def finite(value, *args, **kwargs):
            if value is coords:
                scans.append(value)
            return original(value, *args, **kwargs)

        monkeypatch.setattr(np, "isfinite", finite)
        first = r.read_trajectory("data")
        first.atoms[0].position[:] = 99
        second = r.read_trajectory("data")
        assert len(scans) == 1
        np.testing.assert_array_equal(second.atoms[0].position, [0, 0, 0])
        assert second.coords is coords
        # A read-only view over mutable storage cannot use that success cache.
        mutable = coords.copy()
        readonly = mutable.view()
        readonly.setflags(write=False)
        r._binary_cache[("data", "coords")] = readonly
        r.read_trajectory("data")
        mutable[0, 0, 0] = np.nan
        with pytest.raises(QVFError, match="finite positions"):
            r.read_trajectory("data")


def test_cached_array_shape_mutation_is_not_trusted():
    with QVFReader(archive()) as r:
        r.read_trajectory("data").coords.shape = (1, 2, 3)
        with pytest.raises(QVFError, match="coords shape"):
            r.read_trajectory("data")
