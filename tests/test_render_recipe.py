"""Scientific selection and replay boundaries for the released-API example."""

import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import vibeview
from examples.render_recipe import render, sha, validate_recipe, verify_viewer


def recipe(source):
    return {
        "version": 1, "source_sha256": sha(source.read_bytes()),
        "structure": "structure", "field": "vol_dens_0", "field_units": "electron/bohr^3",
        "isovalue": 0.5, "signed": False, "positive_color": "#227799",
        "negative_color": "#bb5533", "opacity": 0.4,
        "element_colors": {"Na": "#777777", "Cl": "#33bb55"},
        "size": [320, 240], "replication": [2, 2, 2], "cell_outline": True,
        "cell_color": "#444444", "background": "white",
        "camera": {"position": [20, 20, 20], "focal_point": [4, 4, 4],
                   "view_up": [0, 0, 1], "parallel_scale": 12},
    }


@pytest.fixture
def identity(tmp_path):
    root = Path(vibeview.__file__).parent
    path = tmp_path / "identity.json"
    path.write_text(json.dumps({
        "version": vibeview.__version__, "source_commit": "test-only",
        "source_archive_sha256": "test-only",
        "files_sha256": {"src/vibeview/" + p.relative_to(root).as_posix(): sha(p.read_bytes())
                         for p in root.rglob("*.py")},
    }))
    return path


def test_periodic_image_and_replay(showcase_qvf, identity, tmp_path):
    control = tmp_path / "recipe.json"
    control.write_text(json.dumps(recipe(showcase_qvf)))
    original = showcase_qvf.read_bytes()
    result = render(showcase_qvf, control, identity, tmp_path / "first")
    replay = render(showcase_qvf, tmp_path / "first/recipe.json", identity, tmp_path / "again")
    assert result["source"] == replay["source"]
    assert result["camera"] == replay["camera"]
    assert result["surfaces"] == replay["surfaces"]
    assert result["image_sha256"] == replay["image_sha256"]
    assert result["structure"]["displayed_atom_count"] == 16
    assert result["structure"]["exact_renderer_position_equality"]
    assert result["field"]["exact_sample_equality"]
    assert showcase_qvf.read_bytes() == original
    image = Image.open(tmp_path / "first/image.png")
    assert image.size == (320, 240)
    assert np.asarray(image).std() > 1
    with pytest.raises(ValueError, match="already exists"):
        render(showcase_qvf, control, identity, tmp_path / "first")


@pytest.mark.parametrize(("key", "value", "message"), [
    ("source_sha256", "wrong", "SHA256"),
    ("field_units", "hartree", "units"),
    ("signed", True, "density needs"),
    ("element_colors", {"Na": "red"}, "element_colors"),
    ("isovalue", 1e20, "empty surface"),
])
def test_refuse_wrong_scientific_selection(showcase_qvf, identity, tmp_path, key, value, message):
    config = recipe(showcase_qvf)
    config[key] = value
    control = tmp_path / "recipe.json"
    control.write_text(json.dumps(config))
    with pytest.raises(ValueError, match=message):
        render(showcase_qvf, control, identity, tmp_path / "invalid")
    assert not (tmp_path / "invalid").exists()


def test_refuse_source_identity_drift(identity):
    config = json.loads(identity.read_text())
    config["files_sha256"]["src/vibeview/__init__.py"] = "modified"
    identity.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="sources differ"):
        verify_viewer(identity)


def test_refuse_changed_member_even_with_new_archive_hash(showcase_qvf, identity, tmp_path):
    altered = tmp_path / "altered.qvf"
    with zipfile.ZipFile(showcase_qvf) as source, zipfile.ZipFile(altered, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "volumes/density.dat":
                data = bytes([data[0] ^ 1]) + data[1:]
            target.writestr(name, data)
    control = tmp_path / "recipe.json"
    control.write_text(json.dumps(recipe(altered)))
    with pytest.raises(Exception, match="[Hh]ash|[Ss][Hh][Aa]|checksum"):
        render(altered, control, identity, tmp_path / "invalid")
    assert not (tmp_path / "invalid").exists()


@pytest.mark.parametrize(("key", "value"), [
    ("replication", [0, 1, 1]), ("opacity", 2), ("isovalue", float("nan")),
    ("size", [300, True]),
    ("camera", {"position": [0, 0, 1], "focal_point": [0, 0, 0],
                "view_up": [0, 0, 1], "parallel_scale": 1}),
])
def test_refuse_invalid_render_controls(showcase_qvf, key, value):
    config = recipe(showcase_qvf)
    config[key] = value
    with pytest.raises(ValueError):
        validate_recipe(config)
