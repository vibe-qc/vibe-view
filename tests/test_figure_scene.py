import io

import numpy as np
import pytest
from PIL import Image

from vibeview.figure_scene import FigureScene, draw_measurements, mapped_attribute, overlay
from vibeview.geometry_edit import geometry_archive
from vibeview.morph_movie import morph_frames, render_morph
from vibeview.qvf import Atom, QVFReader, StructureData


def structure(z=0):
    return StructureData(
        [Atom("H", np.array([0.0, 0.0, z]), 1), Atom("H", np.array([0.74, 0.0, 0.0]), 1)],
        (False, False, False),
        None,
        [(0, 1, 1.0, (0, 0, 0))],
    )


def scene():
    return FigureScene.from_dict(
        {
            "version": 1,
            "measurements": [
                {"refs": [["current", 0], ["current", 1]], "precision": 3, "colour": "blue"}
            ],
            "annotations": [
                {"kind": "text", "start": [0.1, 0.1], "text": "H2 bond", "colour": "black"},
                {"kind": "arrow", "start": [0.2, 0.3], "end": [0.8, 0.7], "colour": "red"},
                {"kind": "box", "start": [0.1, 0.1], "end": [0.9, 0.9]},
            ],
            "keys": [{"title": "Charge", "range": [-1, 1], "palette": "coolwarm"}],
        }
    )


def test_decorations_roundtrip_and_shared_pixel_layer():
    a = scene()
    b = FigureScene.from_dict(a.to_dict())
    first = overlay(a, (320, 240))
    second = overlay(b, (320, 240))
    np.testing.assert_array_equal(first, second)
    assert np.count_nonzero(np.asarray(first)[:, :, 3]) > 1000
    base = Image.new("RGBA", (320, 240), "white")
    assert np.asarray(Image.alpha_composite(base, first)).shape == (240, 320, 4)
    bad = a.to_dict()
    bad["annotations"][0]["start"] = [float("nan"), 0.1]
    with pytest.raises(ValueError):
        FigureScene.from_dict(bad)


def test_attribute_palette_range_and_radius():
    c, r = mapped_attribute(
        {
            "values": [-1, 0, 1],
            "range": [-1, 1],
            "palette": "viridis",
            "radii": [0.2, 0.8],
            "radius_enabled": True,
        },
        3,
    )
    assert r == {0: 0.2, 1: 0.5, 2: 0.8}
    assert c[0] != c[2]
    with pytest.raises(ValueError):
        mapped_attribute({"values": [1], "range": [0, 2]}, 3)


def test_morph_exact_count_endpoints_mapping_and_path():
    first, last = structure(), structure(1)
    frames = morph_frames(first, last, 7)
    assert frames.shape == (7, 2, 3)
    np.testing.assert_array_equal(frames[0], [a.position for a in first.atoms])
    np.testing.assert_array_equal(frames[-1], [a.position for a in last.atoms])
    middle = frames[3].copy()
    middle[:, 1] = 2
    curved = morph_frames(first, last, 9, path=np.stack([frames[0], middle, frames[-1]]))
    np.testing.assert_array_equal(curved[4], middle)
    with pytest.raises(ValueError, match="one-to-one"):
        morph_frames(first, last, 4, mapping=[0, 0])
    with pytest.raises(ValueError, match="endpoints"):
        morph_frames(first, last, 4, path=np.stack([middle, frames[-1]]))


def test_real_capture_labels_and_morph_frames(tmp_path):
    from vibeview.headless import headless_plotter

    a = scene()
    with headless_plotter((240, 180)) as plotter:
        draw_measurements(plotter, a, {"current": structure()})
        assert "figure_measure_0_label" in plotter.actors
        assert plotter.actors["figure_measure_0_label"].mapper.dataset.n_points > 0
    with QVFReader(io.BytesIO(geometry_archive(structure()))) as reader:
        result = render_morph(
            reader,
            structure(1),
            tmp_path / "morph",
            count=3,
            size=(240, 180),
            format="frames",
            scene=a,
        )
    frames = sorted(result.glob("frame_*.png"))
    assert len(frames) == 3
    assert frames[0].read_bytes() != frames[-1].read_bytes()


def test_actual_controller_attribute_overlay_session_and_exports(water_qvf, monkeypatch):
    import base64
    from unittest.mock import Mock

    from vibeview import figure_panel
    from vibeview.app import create_app

    viewers = []
    original = figure_panel.bind

    def remember(viewer):
        viewers.append(viewer)
        original(viewer)

    monkeypatch.setattr(figure_panel, "bind", remember)
    with QVFReader(water_qvf) as reader:
        server = create_app([reader])
        viewer = viewers[-1]
        state, ctrl = server.state, server.controller
        viewer.exports._offer_download = Mock()
        try:
            state.figure_attribute = "custom"
            state.figure_custom_values = "[0, 0.5, 1]"
            state.figure_radius_enabled = True
            state.figure_radius_min = 0.2
            state.figure_radius_max = 0.6
            ctrl.figure_attribute()
            atoms = {
                k: a for k, a in viewer.plotter.actors.items() if k.startswith("figure_attr_atom_")
            }
            assert len(atoms) == 3
            assert not any(
                a.visibility for k, a in viewer.plotter.actors.items() if k.startswith("atom_")
            )
            a = atoms["figure_attr_atom_0_0"]
            assert (a.bounds[1] - a.bounds[0]) / 2 == pytest.approx(0.2, rel=0.01)
            assert (
                a.prop.color
                != next(
                    a for k, a in atoms.items() if k.startswith("figure_attr_atom_2_")
                ).prop.color
            )
            state.figure_text = "Persistent annotation"
            ctrl.figure_annotation()
            saved = viewer.sessions._saved_view_extras()
            state.figure_scene = FigureScene().to_dict()
            viewer.sessions._restore_view_appearance(saved)
            figure_panel.render(viewer)
            assert figure_panel.scene(viewer).annotations[0]["text"] == "Persistent annotation"
            encoded = state.figure_overlay_data.split(",", 1)[1]
            layer = Image.open(io.BytesIO(base64.b64decode(encoded)))
            base = Image.new("RGBA", layer.size, "white")
            np.testing.assert_array_equal(
                figure_panel.composite(viewer, base), Image.alpha_composite(base, layer)
            )
            state.figure_export_format = "svg"
            assert figure_panel.deliver_figure(viewer, base)
            assert b"data:image/png;base64" in viewer.exports._offer_download.call_args.args[0]
            ctrl.figure_clear()
            assert not any(k.startswith("figure_attr_") for k in viewer.plotter.actors)
        finally:
            viewer.plotter.close()


def test_morph_encoded_count_and_identical_endpoints(tmp_path):
    with QVFReader(io.BytesIO(geometry_archive(structure()))) as reader:
        output = render_morph(
            reader,
            structure(),
            tmp_path / "same.gif",
            count=3,
            fps=10,
            size=(120, 100),
            format="gif",
        )
        with Image.open(output) as image:
            assert image.n_frames == 3


def test_raw_atom_property_uses_actual_member(showcase_qvf):
    from types import SimpleNamespace

    from vibeview.figure_panel import property_values

    with QVFReader(showcase_qvf) as reader:
        values = property_values(SimpleNamespace(reader=reader), "atom_props/mulliken_charge")
        np.testing.assert_array_equal(
            values, reader.read_atom_properties("atom_props").mulliken_charges
        )


def test_exact_gif_frames_keep_pixels_and_timing(tmp_path):
    from vibeview.animation import _encode_frames

    directory = tmp_path / "frames"
    directory.mkdir()
    for i, colour in enumerate(["red", "red", "blue", "blue"]):
        Image.new("RGB", (20, 15), colour).save(directory / f"frame_{i:04d}.png")
    path = _encode_frames(
        directory, tmp_path / "test.gif", "gif", 10, loop=False, preserve_frames=True
    )
    with Image.open(path) as image:
        assert image.n_frames == 4 and "loop" not in image.info
        for i, expected in enumerate([(255, 0, 0)] * 2 + [(0, 0, 255)] * 2):
            image.seek(i)
            assert image.convert("RGB").getpixel((10, 10)) == expected
            assert image.info["duration"] == 100


@pytest.mark.parametrize("representation", ["ball_and_stick", "space_filling"])
@pytest.mark.parametrize(
    "camera",
    [
        [(0, 0, 10), (0, 0, 0), (0, 1, 0)],    # bond across the screen
        [(0, 0, 10), (0, 0, 0), (1, 0, 0)],    # camera up along the bond
        [(6, 6, 6), (0, 0, 0), (0, 0, 1)],
    ],
)
def test_measurement_label_clears_the_measured_atom_spheres(representation, camera):
    """#134 acceptance: an O-H label at a fixed 0.4 A lift sat inside the
    displayed O sphere, so only its first digit showed."""
    from vibeview.headless import headless_plotter
    from vibeview.renderers.structure import atom_display_radius

    water = StructureData(
        [Atom("O", np.zeros(3), 8), Atom("H", np.array([0.757, 0.586, 0.0]), 1)],
        (False, False, False),
        None,
        [(0, 1, 1.0, (0, 0, 0))],
    )
    spec = FigureScene.from_dict(
        {"version": 1, "measurements": [{"refs": [["current", 0], ["current", 1]]}]}
    )
    with headless_plotter((240, 180)) as plotter:
        plotter.camera_position = camera
        draw_measurements(plotter, spec, {"current": water}, representation)
        points = np.asarray(plotter.actors["figure_measure_0_label"].mapper.dataset.points)
    for atom in water.atoms:
        clearance = np.linalg.norm(points - atom.position, axis=1).min()
        assert clearance > atom_display_radius(atom.atomic_number, representation)


@pytest.mark.parametrize(
    "text,drawn", [("0.96 Å", "0.96 A"), ("104.50 °", "104.50 deg"), ("−0.50", "-0.50")]
)
def test_label_units_are_drawn_not_skipped(text, drawn):
    """#134 acceptance: vtkVectorText draws ASCII only and skipped Å and °, so
    measurement labels showed a bare number in the browser and in captures."""
    import pyvista as pv

    from vibeview.renderers.structure import build_label_mesh

    mesh = build_label_mesh([np.zeros(3)], [text], scale=1.0, lift=0.0)
    assert mesh.n_points == pv.Text3D(drawn, depth=0.05).n_points
    skipped = text.replace("Å", "").replace("°", "").replace("−", "").strip()
    assert mesh.n_points > pv.Text3D(skipped, depth=0.05).n_points


@pytest.mark.parametrize(
    "chosen,background,stored",
    [
        ("auto", "#1a1a2e", "white"),  # the viewer's default dark navy
        ("auto", "white", "black"),
        ("", "black", "white"),
        ("AUTO", "#f0f0f0", "black"),
        ("red", "#1a1a2e", "red"),  # an explicit colour is never overridden
    ],
)
def test_decoration_colour_contrasts_with_the_background(chosen, background, stored):
    """#138: the default black was nearly invisible on the dark background."""
    from types import SimpleNamespace

    import pyvista as pv

    from vibeview.figure_panel import decoration_colour

    viewer = SimpleNamespace(
        server=SimpleNamespace(state=SimpleNamespace(figure_colour=chosen)),
        plotter=SimpleNamespace(background_color=pv.Color(background)),
    )
    assert decoration_colour(viewer) == stored


def test_new_decorations_and_key_text_use_the_resolved_colour(water_qvf, monkeypatch):
    """Through the real controllers on the default dark background (#138)."""
    from vibeview import figure_panel
    from vibeview.app import create_app

    viewers = []
    original = figure_panel.bind

    def remember(viewer):
        viewers.append(viewer)
        original(viewer)

    monkeypatch.setattr(figure_panel, "bind", remember)
    with QVFReader(water_qvf) as reader:
        server = create_app([reader])
        viewer = viewers[-1]
        state, ctrl = server.state, server.controller
        try:
            viewer.plotter.set_background("#1a1a2e")
            assert state.figure_colour == "auto"
            state.selected_atoms = [0, 1]
            ctrl.figure_measurement()
            state.figure_text = "Annotated"
            ctrl.figure_annotation()
            state.figure_x, state.figure_y, state.figure_x2, state.figure_y2 = 0.7, 0.2, 0.8, 0.8
            ctrl.figure_key()
            spec = figure_panel.scene(viewer)
            assert [m["colour"] for m in spec.measurements] == ["white"]
            assert [a["colour"] for a in spec.annotations] == ["white"]
            assert [k["colour"] for k in spec.keys] == ["white"]
        finally:
            viewer.plotter.close()
    # The key's title and numbers are drawn in that colour, not fixed black.
    layer = np.asarray(overlay(FigureScene(keys=spec.keys), (400, 300)))
    beside = layer[:, 330:, :]
    ink = beside[beside[..., 3] > 0][:, :3]
    assert len(ink) and (ink.mean(axis=0) > 200).all()


def test_older_scenes_without_a_key_colour_keep_black_text():
    layer = np.asarray(
        overlay(FigureScene(keys=[{"title": "q", "range": [0, 1], "start": [0.7, 0.2],
                                   "end": [0.8, 0.8]}]), (400, 300))
    )
    beside = layer[:, 330:, :]
    ink = beside[beside[..., 3] > 0][:, :3]
    assert len(ink) and (ink.mean(axis=0) < 60).all()
