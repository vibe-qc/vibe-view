"""D4 colour-by-b-factor.

Unlike the categorical schemes, this one is a continuous normalised ramp,
which brings two failure modes the others do not have: a zero-span
structure that would divide by zero, and an unmeasured atom that must not
be painted as though it were rigid.
"""

from __future__ import annotations

import numpy as np
import pytest

from vibeview.renderers.structure import (
    _CARTOON_BFACTOR_ABSENT,
    _CARTOON_BFACTOR_COLD,
    _CARTOON_BFACTOR_WARM,
    _cartoon_bfactor_colors,
)


def _helix(n):
    t = np.arange(n)
    a = np.deg2rad(100.0) * t
    return np.stack([2.3 * np.cos(a), 2.3 * np.sin(a), 1.5 * t], axis=1)


def _structure(tmp_path, b_factors, chain="A"):
    """Build a CA-only chain whose b-factor column carries *b_factors*;
    a None entry leaves columns 61-66 blank, which is not the same as 0.00."""
    ca = _helix(len(b_factors))
    lines = []
    for i, (pos, b) in enumerate(zip(ca, b_factors, strict=True), start=1):
        col = "      " if b is None else f"{b:6.2f}"
        lines.append(
            "ATOM  " + f"{i:>5}" + "  CA  ALA " + chain + f"{i:>4}" + "    "
            + f"{pos[0]:8.3f}{pos[1]:8.3f}{pos[2]:8.3f}" + f"{1.0:6.2f}" + col
        )
    pdb = tmp_path / "b.pdb"
    pdb.write_text("\n".join(lines) + "\n")

    from vibeview.converters import pdb_to_qvf
    from vibeview.qvf import QVFReader

    q = tmp_path / "b.qvf"
    q.write_bytes(pdb_to_qvf(pdb).getvalue())
    reader = QVFReader(q)
    return reader, reader.read_structure()


class TestRamp:
    def test_endpoints_are_the_scale_endpoints(self, tmp_path):
        _r, sd = _structure(tmp_path, [10, 20, 30, 40, 50, 60, 70, 80])
        n = len(sd.backbone_trace())
        rgb = _cartoon_bfactor_colors(sd, "A", n, n * 4)
        assert tuple(rgb[0]) == _CARTOON_BFACTOR_COLD
        assert tuple(rgb[-1]) == _CARTOON_BFACTOR_WARM

    def test_is_monotonic_along_a_monotonic_ramp(self, tmp_path):
        """Red channel rises and blue falls as B rises; a scale that is not
        monotonic is not readable as a scale."""
        _r, sd = _structure(tmp_path, list(range(10, 90, 10)))
        n = len(sd.backbone_trace())
        rgb = _cartoon_bfactor_colors(sd, "A", n, n * 4).astype(int)
        assert np.all(np.diff(rgb[:, 0]) >= 0), rgb[:, 0]
        assert np.all(np.diff(rgb[:, 2]) <= 0), rgb[:, 2]

    def test_normalised_per_structure_not_absolute(self, tmp_path):
        """A narrow-range structure still spans the full ramp. This is the
        deliberate trade: informative within a file, not comparable across
        files, which is why the UI states the range."""
        _r, tight = _structure(tmp_path, [30.0, 30.5, 31.0, 31.5])
        n = len(tight.backbone_trace())
        rgb = _cartoon_bfactor_colors(tight, "A", n, n * 4)
        assert tuple(rgb[0]) == _CARTOON_BFACTOR_COLD
        assert tuple(rgb[-1]) == _CARTOON_BFACTOR_WARM

    def test_is_interpolated_unlike_the_categorical_schemes(self, tmp_path):
        """B-factor is continuous, so intermediate colours are real values
        rather than invented categories."""
        _r, sd = _structure(tmp_path, list(range(10, 90, 10)))
        n = len(sd.backbone_trace())
        rgb = _cartoon_bfactor_colors(sd, "A", n, n * 4)
        endpoints = {_CARTOON_BFACTOR_COLD, _CARTOON_BFACTOR_WARM}
        distinct = {tuple(c) for c in np.unique(rgb, axis=0)}
        assert len(distinct - endpoints) > 0


class TestUnmeasured:
    def test_absent_bfactor_is_off_scale_not_cold(self, tmp_path):
        """`None` means unmeasured, not rigid. Painting it the coldest
        colour asserts a rigidity nobody recorded."""
        _r, sd = _structure(tmp_path, [10, 20, None, 40, 50, 60])
        n = len(sd.backbone_trace())
        rgb = _cartoon_bfactor_colors(sd, "A", n, n * 4)
        present = {tuple(c) for c in np.unique(rgb, axis=0)}
        assert _CARTOON_BFACTOR_ABSENT in present
        assert _CARTOON_BFACTOR_ABSENT != _CARTOON_BFACTOR_COLD

    def test_a_real_zero_is_data_not_absence(self, tmp_path):
        """0.00 is a recorded value and must land on the ramp, not on the
        unmeasured hue."""
        _r, sd = _structure(tmp_path, [0.0, 25.0, 50.0, 75.0])
        n = len(sd.backbone_trace())
        rgb = _cartoon_bfactor_colors(sd, "A", n, n * 4)
        assert tuple(rgb[0]) == _CARTOON_BFACTOR_COLD
        present = {tuple(c) for c in np.unique(rgb, axis=0)}
        assert _CARTOON_BFACTOR_ABSENT not in present

    def test_no_bfactors_at_all_falls_back(self, tmp_path):
        _r, sd = _structure(tmp_path, [None] * 8)
        n = len(sd.backbone_trace())
        assert _cartoon_bfactor_colors(sd, "A", n, n * 4) is None


class TestDegenerate:
    def test_uniform_bfactor_does_not_divide_by_zero(self, tmp_path):
        _r, sd = _structure(tmp_path, [25.0] * 8)
        n = len(sd.backbone_trace())
        rgb = _cartoon_bfactor_colors(sd, "A", n, n * 4)
        assert rgb is not None
        assert not np.isnan(rgb.astype(float)).any()
        assert len({tuple(c) for c in np.unique(rgb, axis=0)}) == 1

    def test_mismatched_residue_count_falls_back(self, tmp_path):
        _r, sd = _structure(tmp_path, [10.0, 20.0, 30.0, 40.0])
        assert _cartoon_bfactor_colors(sd, "A", 999, 40) is None

    @pytest.mark.parametrize("n_samples", [16, 40, 401])
    def test_profile_length_matches_the_spline(self, tmp_path, n_samples):
        _r, sd = _structure(tmp_path, list(range(10, 90, 10)))
        n = len(sd.backbone_trace())
        assert len(_cartoon_bfactor_colors(sd, "A", n, n_samples)) == n_samples


class TestRendersAndReports:
    def test_renders_one_actor_with_rgb_on_the_tube(self, tmp_path):
        import pyvista as pv

        from vibeview.renderers.structure import StructureRenderer

        reader, _sd = _structure(tmp_path, list(range(10, 90, 10)))
        plotter = pv.Plotter(off_screen=True)
        StructureRenderer(
            reader.get_section("structure"), reader
        ).add_to_plotter(
            plotter, representation="cartoon", cartoon_color_mode="bfactor"
        )
        assert len([n for n in plotter.actors if n.startswith("cartoon_")]) == 1

    def test_controller_accepts_bfactor_and_reports_the_range(self, tmp_path):
        """The ramp is per-structure, so the range is not optional detail."""
        from vibeview.app import create_app

        reader, _sd = _structure(tmp_path, [10.0, 20.0, 30.0, 40.0, 55.5])
        app = create_app(reader)
        app.controller.set_cartoon_color_mode("bfactor")
        assert app.state.cartoon_color_mode == "bfactor"
        msg = app.state.status_message
        assert "10.00" in msg and "55.50" in msg, msg

    def test_controller_says_so_when_the_file_has_none(self, tmp_path):
        """Falling back to chain colour silently looks like a broken mode."""
        from vibeview.app import create_app

        reader, _sd = _structure(tmp_path, [None] * 6)
        app = create_app(reader)
        app.controller.set_cartoon_color_mode("bfactor")
        assert "no b-factors" in app.state.status_message

    def test_controller_still_rejects_an_unknown_mode(self, tmp_path):
        from vibeview.app import create_app

        reader, _sd = _structure(tmp_path, [10.0, 20.0, 30.0, 40.0])
        app = create_app(reader)
        before = app.state.cartoon_color_mode
        app.controller.set_cartoon_color_mode("not-a-colour-mode")
        assert app.state.cartoon_color_mode == before
