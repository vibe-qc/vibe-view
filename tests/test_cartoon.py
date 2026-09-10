"""D3: the cartoon/ribbon representation.

A ribbon is drawn per chain through the alpha-carbon trace. The
representation exists for biomolecules, so the interesting cases are the
ones where a structure is *not* one, and where a chain is too short to
spline.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import pyvista as pv

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")


def _actor_dataset(actor):
    """The dataset a ribbon actor will actually render.

    Reading ``mapper.dataset`` straight after ``add_mesh`` is **not** safe
    when the actor was given ``scalars=..., rgb=True``. PyVista splices an
    ``ActiveScalarsAlgorithm`` into the mapper's input in that case, and
    that algorithm's output is empty until the pipeline runs: a render
    runs it, a test reading the mapper does not. Worse, any later
    ``add_mesh`` on the same plotter invalidates it again, so a
    single-actor read can pass while the same read over two chains
    reports zero points and no arrays — which looks exactly like a
    renderer that dropped a chain.

    Calling ``Update()`` first is the whole fix. Every test that reads an
    rgb-coloured ribbon back must go through here.
    """
    mapper = actor.GetMapper()
    mapper.Update()
    return mapper.dataset


def _pdb(tmp_path, records):
    """records: (atom_name, resName, chain, resSeq, x)"""
    lines = []
    for i, (name, res, chain, seq, x) in enumerate(records, start=1):
        lines.append(
            "ATOM  "
            + f"{i:>5}"
            + " "
            + name.ljust(4)
            + " "
            + res.ljust(3)
            + " "
            + (chain or " ")
            + f"{seq:>4}"
            + "    "
            + f"{float(x):8.3f}{0.0:8.3f}{0.0:8.3f}"
            + f"{1.0:6.2f}{0.0:6.2f}"
        )
    p = tmp_path / "m.pdb"
    p.write_text("\n".join(lines) + "\n")
    return p


def _render(path, representation="cartoon"):
    from vibeview.converters import pdb_to_qvf, xyz_to_qvf
    from vibeview.qvf import QVFReader
    from vibeview.renderers.structure import StructureRenderer

    out = Path(tempfile.mktemp(suffix=".qvf"))
    if str(path).endswith(".pdb"):
        out.write_bytes(pdb_to_qvf(path).getvalue())
    else:
        out.write_bytes(xyz_to_qvf(path).getvalue())
    reader = QVFReader(out)
    plotter = pv.Plotter(off_screen=True)
    StructureRenderer(reader.get_section("structure"), reader).add_to_plotter(
        plotter, representation=representation
    )
    names = list(plotter.actors)
    out.unlink(missing_ok=True)
    return names


def _chain(chain_id, n, x0=0.0):
    return [
        (" CA ", "ALA", chain_id, i + 1, x0 + i * 3.8) for i in range(n)
    ]


class TestCartoonRepresentation:
    def test_one_ribbon_actor_per_chain(self, tmp_path):
        names = _render(_pdb(tmp_path, _chain("A", 8) + _chain("B", 8, 50.0)))
        ribbons = [n for n in names if n.startswith("cartoon_chain_")]
        assert len(ribbons) == 2, names
        assert "cartoon_chain_A" in names
        assert "cartoon_chain_B" in names

    def test_chains_are_separate_actors_not_one_spline(self, tmp_path):
        """A single spline over a concatenated trace would draw a ribbon
        leaping from one chain's C-terminus to the next chain's N-term."""
        names = _render(_pdb(tmp_path, _chain("A", 6) + _chain("B", 6, 99.0)))
        assert len([n for n in names if n.startswith("cartoon_")]) == 2

    def test_short_chain_is_skipped_not_approximated(self, tmp_path):
        """Under 4 alpha-carbons cannot be splined — an ion or a
        single-residue ligand must not become a stub ribbon."""
        names = _render(_pdb(tmp_path, _chain("A", 8) + _chain("L", 2, 80.0)))
        ribbons = {n for n in names if n.startswith("cartoon_chain_")}
        assert ribbons == {"cartoon_chain_A"}, ribbons

    def test_structure_without_residues_falls_back(self):
        """An XYZ has no backbone; the viewport must not come up empty."""
        names = _render(b"3\nc\nO 0 0 0\nH 0 0 1\nH 0 1 0\n")
        assert not any(n.startswith("cartoon_") for n in names)
        assert names, "fallback drew nothing at all"

    def test_controller_refuses_cartoon_without_a_backbone(self):
        """The picker must not read 'Cartoon' over an unchanged scene."""
        from vibeview.app import create_app
        from vibeview.converters import xyz_to_qvf
        from vibeview.qvf import QVFReader

        p = Path(tempfile.mktemp(suffix=".qvf"))
        p.write_bytes(xyz_to_qvf(b"2\nc\nO 0 0 0\nH 0 0 1\n").getvalue())
        try:
            app = create_app(QVFReader(p))
            state, ctrl = app.state, app.controller
            before = state.representation_style
            ctrl.set_representation_style("cartoon")
            assert state.representation_style == before, "switched anyway"
            assert "no residue/chain information" in state.status_message
        finally:
            p.unlink(missing_ok=True)

    def test_controller_accepts_cartoon_on_a_protein(self, tmp_path):
        from vibeview.app import create_app
        from vibeview.converters import pdb_to_qvf
        from vibeview.qvf import QVFReader

        out = tmp_path / "p.qvf"
        out.write_bytes(pdb_to_qvf(_pdb(tmp_path, _chain("A", 10))).getvalue())
        app = create_app(QVFReader(out))
        state, ctrl = app.state, app.controller
        ctrl.set_representation_style("cartoon")
        assert state.representation_style == "cartoon", state.status_message


class TestDefaultRepresentation:
    """A protein must not open ball-and-stick: bond inference costs
    158.7 s on 13,772 atoms, and the Trame port binds only after the
    first render, so the page is unreachable until it finishes."""

    def test_protein_opens_as_cartoon(self, tmp_path):
        from vibeview.app import _default_representation
        from vibeview.converters import pdb_to_qvf
        from vibeview.qvf import QVFReader

        out = tmp_path / "p.qvf"
        out.write_bytes(pdb_to_qvf(_pdb(tmp_path, _chain("A", 12))).getvalue())
        assert _default_representation(QVFReader(out)) == "cartoon"

    def test_metadata_load_and_cartoon_defer_bond_inference(self, tmp_path):
        from vibeview.converters import pdb_to_qvf
        from vibeview.qvf import QVFReader
        from vibeview.renderers.structure import StructureRenderer

        out = tmp_path / "p.qvf"
        out.write_bytes(pdb_to_qvf(_pdb(tmp_path, _chain("A", 12))).getvalue())
        delegate = QVFReader(out)

        class CountingReader:
            def __init__(self):
                self.structure_reads = 0
                self.bond_inferences = 0

            def read_structure(self):
                self.structure_reads += 1
                return delegate.read_structure()

            def infer_bonds(self, structure):
                self.bond_inferences += 1
                return delegate.infer_bonds(structure)

        reader = CountingReader()
        renderer = StructureRenderer(delegate.get_section("structure"), reader)

        # The app reads dimensionality and PBC metadata before building the
        # scene. That inspection and the subsequent ribbon must share one
        # structure parse without paying for bonds the ribbon never draws.
        structure = renderer.load_structure()
        plotter = pv.Plotter(off_screen=True)
        try:
            renderer.add_to_plotter(plotter, representation="cartoon")
        finally:
            plotter.close()

        assert renderer.load_structure() is structure
        assert reader.structure_reads == 1
        assert reader.bond_inferences == 0

        # Space-filling draws atoms only, so it stays on the geometry cache.
        atom_plotter = pv.Plotter(off_screen=True)
        try:
            renderer.add_to_plotter(atom_plotter, representation="space_filling")
        finally:
            atom_plotter.close()
        assert reader.structure_reads == 1
        assert reader.bond_inferences == 0

        # Switching later to a bond-bearing representation still initialises
        # connectivity, and repeated loads reuse even an empty result.
        renderer.load()
        renderer.load()
        assert reader.structure_reads == 1
        assert reader.bond_inferences == 1

    def test_create_app_protein_startup_does_not_infer_bonds(
        self, tmp_path, monkeypatch
    ):
        from vibeview.app import create_app
        from vibeview.converters import pdb_to_qvf
        from vibeview.qvf import QVFReader

        out = tmp_path / "p.qvf"
        out.write_bytes(pdb_to_qvf(_pdb(tmp_path, _chain("A", 12))).getvalue())
        reader = QVFReader(out)
        inferred = []
        original = reader.infer_bonds

        def record_inference(structure):
            inferred.append(structure)
            return original(structure)

        monkeypatch.setattr(reader, "infer_bonds", record_inference)
        create_app(reader)

        assert inferred == []

    def test_molecule_still_opens_ball_and_stick(self, tmp_path):
        from vibeview.app import _default_representation
        from vibeview.converters import xyz_to_qvf
        from vibeview.qvf import QVFReader

        out = tmp_path / "m.qvf"
        out.write_bytes(xyz_to_qvf(b"2\nc\nO 0 0 0\nH 0 0 1\n").getvalue())
        assert _default_representation(QVFReader(out)) == "ball_and_stick"

    def test_too_short_to_spline_stays_ball_and_stick(self, tmp_path):
        """Residue metadata alone is not a backbone — a 3-residue peptide
        cannot be splined, so a ribbon default would render nothing."""
        from vibeview.app import _default_representation
        from vibeview.converters import pdb_to_qvf
        from vibeview.qvf import QVFReader

        out = tmp_path / "s.qvf"
        out.write_bytes(pdb_to_qvf(_pdb(tmp_path, _chain("A", 3))).getvalue())
        assert _default_representation(QVFReader(out)) == "ball_and_stick"

    def test_unreadable_structure_does_not_raise(self):
        from vibeview.app import _default_representation

        class Boom:
            def read_structure(self):
                raise RuntimeError("no structure section")

        assert _default_representation(Boom()) == "ball_and_stick"

    def test_view_preset_keeps_the_ribbon(self, tmp_path):
        """'publication' styles the scene; it must not silently force a
        protein back onto the slow path."""
        from vibeview.app import create_app
        from vibeview.converters import pdb_to_qvf
        from vibeview.qvf import QVFReader

        out = tmp_path / "p.qvf"
        out.write_bytes(pdb_to_qvf(_pdb(tmp_path, _chain("A", 12))).getvalue())
        app = create_app(QVFReader(out))
        app.controller.apply_view_preset("publication")
        assert app.state.representation_style == "cartoon"


class TestSecondaryStructureRibbonWidth:
    """D2 feeding D3: the ribbon widens over helices and thins over loops."""

    def _sd(self, tmp_path, ca_by_chain):
        lines, serial, seq = [], 1, 0
        for chain_id, ca in ca_by_chain.items():
            for pos in ca:
                seq += 1
                lines.append(
                    "ATOM  " + f"{serial:>5}" + "  CA  ALA " + chain_id
                    + f"{seq:>4}" + "    "
                    + f"{pos[0]:8.3f}{pos[1]:8.3f}{pos[2]:8.3f}"
                    + f"{1.0:6.2f}{0.0:6.2f}"
                )
                serial += 1
        pdb = tmp_path / "p.pdb"
        pdb.write_text("\n".join(lines) + "\n")
        from vibeview.converters import pdb_to_qvf
        from vibeview.qvf import QVFReader

        q = tmp_path / "p.qvf"
        q.write_bytes(pdb_to_qvf(pdb).getvalue())
        return QVFReader(q).read_structure()

    def _helix(self, n):
        import numpy as np

        t = np.arange(n)
        a = np.deg2rad(100.0) * t
        return np.stack([2.3 * np.cos(a), 2.3 * np.sin(a), 1.5 * t], axis=1)

    def test_helix_is_wider_than_coil(self, tmp_path):
        import numpy as np

        from vibeview.renderers.structure import (
            _CARTOON_ARROW_TIP,
            _CARTOON_ARROW_WIDTH,
            _CARTOON_SS_WIDTH,
            _cartoon_ribbon_profile,
        )

        h = self._helix(14)
        # A straight, non-helical tail. D2 reads a perfectly extended
        # trace as strand, not coil, so this is a helix-vs-strand
        # comparison; the round-loop case is covered separately below.
        tail = h[-1] + np.stack(
            [np.zeros(14), np.zeros(14), 3.8 * np.arange(1, 15)], axis=1
        )
        sd = self._sd(tmp_path, {"A": np.vstack([h, tail])})
        n = len(sd.backbone_trace())
        profile = _cartoon_ribbon_profile(sd, "A", n, n * 4)
        assert profile is not None
        width, thickness = profile
        assert width.max() > width.min(), "profile is flat — D2 not reaching D3"
        # An arrow shoulder is the one place a width may exceed the
        # plain per-structure widths, so that is the ceiling here.
        assert width.max() <= max(_CARTOON_SS_WIDTH["H"], _CARTOON_ARROW_WIDTH) + 1e-9
        assert width.min() >= min(
            _CARTOON_SS_WIDTH["C"], _CARTOON_ARROW_TIP
        ) - 1e-9
        # The helix half must average wider than the tail half.
        half = len(width) // 2
        assert width[:half].mean() > width[half:].mean()
        # And the point of a ribbon rather than a tube: the structured
        # half is FLAT — much wider than it is thick.
        assert width[:half].mean() / thickness[:half].mean() > 3.0

    def test_structured_is_flat_and_loop_is_round(self):
        """The profile mapping itself, on a hand-written assignment, so
        the shape claim does not depend on what D2 makes of synthetic
        geometry. A tube can only be round; this is the parity gap."""
        import numpy as np

        from vibeview.renderers.structure import (
            _CARTOON_SS_THICKNESS,
            _CARTOON_SS_WIDTH,
            _cartoon_ss_profile,
        )

        for label, aspect in (("H", 3.0), ("E", 3.0), ("C", 1.01)):
            labels = label * 30
            n = len(labels) * 4
            width = _cartoon_ss_profile(labels, _CARTOON_SS_WIDTH, n)
            thickness = _cartoon_ss_profile(labels, _CARTOON_SS_THICKNESS, n)
            ratio = float(np.mean(width / thickness))
            if label == "C":
                assert ratio < aspect, f"loop is not round: {ratio}"
            else:
                assert ratio > aspect, f"{label} is not flat: {ratio}"

    def test_all_coil_chain_uses_the_uniform_cord(self, tmp_path):
        """No structure to show means no reason to shape the ribbon."""
        import numpy as np

        from vibeview.renderers.structure import _cartoon_ribbon_profile

        rng = np.random.default_rng(2)
        steps = rng.normal(size=(60, 3))
        steps /= np.linalg.norm(steps, axis=1, keepdims=True)
        sd = self._sd(tmp_path, {"A": np.cumsum(steps * 3.8, axis=0)})
        n = len(sd.backbone_trace())
        assert _cartoon_ribbon_profile(sd, "A", n, n * 4) is None

    def test_profile_length_matches_the_spline(self, tmp_path):
        from vibeview.renderers.structure import _cartoon_ribbon_profile

        sd = self._sd(tmp_path, {"A": self._helix(20)})
        n = len(sd.backbone_trace())
        for n_samples in (16, 40, n * 4, 401):
            profile = _cartoon_ribbon_profile(sd, "A", n, n_samples)
            assert profile is not None
            for prof in profile:
                assert len(prof) == n_samples

    def test_mismatched_label_count_falls_back(self, tmp_path):
        """A profile of the wrong length would mis-shape the whole chain,
        so it must be rejected rather than resampled."""
        from vibeview.renderers.structure import _cartoon_ribbon_profile

        sd = self._sd(tmp_path, {"A": self._helix(20)})
        assert _cartoon_ribbon_profile(sd, "A", 999, 80) is None

    def test_varying_profile_still_renders_one_actor_per_chain(self, tmp_path):
        import numpy as np
        import pyvista as pv

        from vibeview.converters import pdb_to_qvf
        from vibeview.qvf import QVFReader
        from vibeview.renderers.structure import StructureRenderer

        h = self._helix(14)
        chains = {"A": h, "B": h + np.array([40.0, 0.0, 0.0])}
        lines, serial, seq = [], 1, 0
        for chain_id, ca in chains.items():
            for pos in ca:
                seq += 1
                lines.append(
                    "ATOM  " + f"{serial:>5}" + "  CA  ALA " + chain_id
                    + f"{seq:>4}" + "    "
                    + f"{pos[0]:8.3f}{pos[1]:8.3f}{pos[2]:8.3f}"
                    + f"{1.0:6.2f}{0.0:6.2f}"
                )
                serial += 1
        pdb = tmp_path / "two.pdb"
        pdb.write_text("\n".join(lines) + "\n")
        q = tmp_path / "two.qvf"
        q.write_bytes(pdb_to_qvf(pdb).getvalue())
        reader = QVFReader(q)
        plotter = pv.Plotter(off_screen=True)
        StructureRenderer(reader.get_section("structure"), reader).add_to_plotter(
            plotter, representation="cartoon"
        )
        ribbons = [n for n in plotter.actors if n.startswith("cartoon_chain_")]
        assert sorted(ribbons) == ["cartoon_chain_A", "cartoon_chain_B"]


class TestRibbonColorMode:
    """D4 first slice: colour the ribbon by chain or by secondary structure."""

    def _helix(self, n, offset=0.0):
        import numpy as np

        t = np.arange(n)
        a = np.deg2rad(100.0) * t
        return np.stack(
            [2.3 * np.cos(a) + offset, 2.3 * np.sin(a), 1.5 * t], axis=1
        )

    def _reader(self, tmp_path, ca_by_chain):
        lines, serial, seq = [], 1, 0
        for chain_id, ca in ca_by_chain.items():
            for pos in ca:
                seq += 1
                lines.append(
                    "ATOM  " + f"{serial:>5}" + "  CA  ALA " + chain_id
                    + f"{seq:>4}" + "    "
                    + f"{pos[0]:8.3f}{pos[1]:8.3f}{pos[2]:8.3f}"
                    + f"{1.0:6.2f}{0.0:6.2f}"
                )
                serial += 1
        pdb = tmp_path / "c.pdb"
        pdb.write_text("\n".join(lines) + "\n")
        from vibeview.converters import pdb_to_qvf
        from vibeview.qvf import QVFReader

        q = tmp_path / "c.qvf"
        q.write_bytes(pdb_to_qvf(pdb).getvalue())
        return QVFReader(q)

    def test_structure_mode_puts_rgb_on_the_ribbon(self, tmp_path):
        """One colour per spline sample, held across that sample's whole
        cross-section ring, so the array length must match the extruded
        mesh rather than the spline that generated it."""
        import pyvista as pv

        from vibeview.renderers.structure import StructureRenderer

        reader = self._reader(tmp_path, {"A": self._helix(16)})
        plotter = pv.Plotter(off_screen=True)
        StructureRenderer(reader.get_section("structure"), reader).add_to_plotter(
            plotter, representation="cartoon", cartoon_color_mode="structure"
        )
        dataset = _actor_dataset(next(iter(plotter.actors.values())))
        assert "cartoon_rgb" in dataset.point_data
        assert len(dataset.point_data["cartoon_rgb"]) == dataset.n_points

    def test_chain_mode_does_not(self, tmp_path):
        import pyvista as pv

        from vibeview.renderers.structure import StructureRenderer

        reader = self._reader(tmp_path, {"A": self._helix(16)})
        plotter = pv.Plotter(off_screen=True)
        StructureRenderer(reader.get_section("structure"), reader).add_to_plotter(
            plotter, representation="cartoon", cartoon_color_mode="chain"
        )
        dataset = _actor_dataset(next(iter(plotter.actors.values())))
        assert "cartoon_rgb" not in dataset.point_data

    def test_helix_and_loop_get_different_colours(self, tmp_path):
        import numpy as np

        from vibeview.renderers.structure import (
            _CARTOON_SS_COLOR,
            _cartoon_ss_colors,
        )

        h = self._helix(14)
        tail = h[-1] + np.stack(
            [np.zeros(14), np.zeros(14), 3.8 * np.arange(1, 15)], axis=1
        )
        sd = self._reader(tmp_path, {"A": np.vstack([h, tail])}).read_structure()
        n = len(sd.backbone_trace())
        rgb = _cartoon_ss_colors(sd, "A", n, n * 4)
        assert rgb is not None
        present = {tuple(c) for c in np.unique(rgb, axis=0)}
        assert _CARTOON_SS_COLOR["H"] in present
        assert _CARTOON_SS_COLOR["C"] in present

    def test_colours_are_not_smoothed(self, tmp_path):
        """Unlike the radius profile, colour must stay categorical — a
        blended colour would imply a structure that is not assigned."""
        import numpy as np

        from vibeview.renderers.structure import (
            _CARTOON_SS_COLOR,
            _cartoon_ss_colors,
        )

        h = self._helix(14)
        tail = h[-1] + np.stack(
            [np.zeros(14), np.zeros(14), 3.8 * np.arange(1, 15)], axis=1
        )
        sd = self._reader(tmp_path, {"A": np.vstack([h, tail])}).read_structure()
        n = len(sd.backbone_trace())
        rgb = _cartoon_ss_colors(sd, "A", n, n * 4)
        allowed = {tuple(v) for v in _CARTOON_SS_COLOR.values()}
        for colour in np.unique(rgb, axis=0):
            assert tuple(colour) in allowed, f"blended colour {tuple(colour)}"

    def test_all_coil_chain_falls_back_to_chain_colour(self, tmp_path):
        import numpy as np

        from vibeview.renderers.structure import _cartoon_ss_colors

        rng = np.random.default_rng(5)
        steps = rng.normal(size=(60, 3))
        steps /= np.linalg.norm(steps, axis=1, keepdims=True)
        sd = self._reader(
            tmp_path, {"A": np.cumsum(steps * 3.8, axis=0)}
        ).read_structure()
        n = len(sd.backbone_trace())
        assert _cartoon_ss_colors(sd, "A", n, n * 4) is None

    def test_every_chain_keeps_its_colours_not_just_the_last(self, tmp_path):
        """Two chains in structure mode. This looked broken for a while and
        was not: an rgb actor's mapper input is an ActiveScalarsAlgorithm
        whose output is empty until the pipeline runs, and each subsequent
        add_mesh invalidates the earlier ones. Reading without Update()
        reports the first chain as zero points and no arrays. See
        ``_actor_dataset``."""
        import numpy as np
        import pyvista as pv

        from vibeview.renderers.structure import StructureRenderer

        reader = self._reader(
            tmp_path, {"A": self._helix(16), "B": self._helix(16, offset=60.0)}
        )
        plotter = pv.Plotter(off_screen=True)
        StructureRenderer(reader.get_section("structure"), reader).add_to_plotter(
            plotter, representation="cartoon", cartoon_color_mode="structure"
        )
        ribbons = {
            n: _actor_dataset(a)
            for n, a in plotter.actors.items()
            if n.startswith("cartoon_chain_")
        }
        assert set(ribbons) == {"cartoon_chain_A", "cartoon_chain_B"}
        for name, dataset in ribbons.items():
            assert dataset.n_points > 0, name
            rgb = dataset.point_data["cartoon_rgb"]
            assert len(rgb) == dataset.n_points, name
            assert np.asarray(rgb).any(), name

    def test_controller_rejects_an_unknown_mode(self, tmp_path):
        from vibeview.app import create_app

        reader = self._reader(tmp_path, {"A": self._helix(16)})
        app = create_app(reader)
        before = app.state.cartoon_color_mode
        app.controller.set_cartoon_color_mode("rainbow")
        assert app.state.cartoon_color_mode == before

    def test_controller_accepts_both_modes_including_list_form(self, tmp_path):
        """The VSelect passes `[$event]`, so a one-element list must work."""
        from vibeview.app import create_app

        app = create_app(self._reader(tmp_path, {"A": self._helix(16)}))
        app.controller.set_cartoon_color_mode(["structure"])
        assert app.state.cartoon_color_mode == "structure"
        app.controller.set_cartoon_color_mode("chain")
        assert app.state.cartoon_color_mode == "chain"


def _ideal_helix(n, start=(0.0, 0.0, 0.0)):
    """Right-handed alpha-helix: 2.3 A radius, 99.6 deg/residue, 1.5 A rise."""
    import numpy as np

    t = np.arange(n)
    a = np.deg2rad(99.6) * t
    return np.stack(
        [2.3 * np.cos(a), 2.3 * np.sin(a), 1.5 * t], axis=1
    ) + np.array(start, dtype=float)


def _ideal_strand(n, start=(0.0, 0.0, 0.0)):
    """Extended beta strand: 3.3 A rise along z, pleated +-0.95 A along x."""
    import numpy as np

    t = np.arange(n)
    return np.stack(
        [np.where(t % 2 == 0, 0.95, -0.95), np.zeros(n), 3.3 * t], axis=1
    ) + np.array(start, dtype=float)


class TestRibbonFrames:
    """The per-residue orientation frame that makes a ribbon flat.

    A tube has no orientation; an extruded profile does, and getting it
    wrong is not a subtle error — an unpropagated sign makes a strand
    twist a half turn per residue.
    """

    def test_frame_is_orthonormal_and_perpendicular_to_the_path(self):
        import numpy as np
        import pyvista as pv

        from vibeview.renderers.structure import _cartoon_ribbon_frames

        ca = _ideal_helix(20)
        pts = np.asarray(pv.Spline(ca, 80).points, dtype=float)
        side, normal = _cartoon_ribbon_frames(ca, pts)

        tangent = np.gradient(pts, axis=0)
        tangent /= np.linalg.norm(tangent, axis=1, keepdims=True)

        assert np.allclose(np.linalg.norm(side, axis=1), 1.0, atol=1e-9)
        assert np.allclose(np.linalg.norm(normal, axis=1), 1.0, atol=1e-9)
        assert np.abs(np.sum(side * tangent, axis=1)).max() < 1e-9
        assert np.abs(np.sum(side * normal, axis=1)).max() < 1e-9

    def test_helix_presents_its_face_away_from_the_axis(self):
        """The whole point of a flat helix ribbon: the thin direction must
        be radial, so the band's face is what you see side-on. If the
        frame were rotated 90 degrees the helix would render edge-on and
        read as a wire."""
        import numpy as np
        import pyvista as pv

        from vibeview.renderers.structure import _cartoon_ribbon_frames

        ca = _ideal_helix(24)  # axis along z, so radial is (x, y, 0)
        pts = np.asarray(pv.Spline(ca, 96).points, dtype=float)
        _side, normal = _cartoon_ribbon_frames(ca, pts)

        radial = pts.copy()
        radial[:, 2] = 0.0
        radial /= np.linalg.norm(radial, axis=1, keepdims=True)
        align = np.abs(np.sum(normal * radial, axis=1))
        assert np.median(align) > 0.95, np.median(align)

    def test_strand_frame_does_not_flip_between_residues(self):
        """A strand's alpha-carbons zig-zag, so the raw curvature vector
        reverses every residue. Without sign propagation the ribbon
        twists 180 degrees per residue instead of staying a plank."""
        import numpy as np
        import pyvista as pv

        from vibeview.renderers.structure import _cartoon_ribbon_frames

        ca = _ideal_strand(16)
        pts = np.asarray(pv.Spline(ca, 64).points, dtype=float)
        side, _normal = _cartoon_ribbon_frames(ca, pts)
        dots = np.sum(side[1:] * side[:-1], axis=1)
        assert dots.min() > 0.9, dots.min()

    def test_strand_plank_lies_across_the_pleat(self):
        """The pleat is the sheet normal, so the plank must be wide in the
        remaining direction — otherwise the strand is drawn standing on
        edge inside its own sheet."""
        import numpy as np
        import pyvista as pv

        from vibeview.renderers.structure import _cartoon_ribbon_frames

        ca = _ideal_strand(16)  # axis z, pleat x -> wide direction is y
        pts = np.asarray(pv.Spline(ca, 64).points, dtype=float)
        side, _normal = _cartoon_ribbon_frames(ca, pts)
        assert np.median(np.abs(side[:, 1])) > 0.95, np.median(np.abs(side[:, 1]))

    def test_a_perfectly_straight_trace_does_not_divide_by_zero(self):
        """Collinear alpha-carbons have no curvature and hence no
        preferred side; synthetic geometry hits this and must not produce
        NaN vertices."""
        import numpy as np
        import pyvista as pv

        from vibeview.renderers.structure import _cartoon_ribbon_frames

        ca = np.stack(
            [np.zeros(12), np.zeros(12), 3.8 * np.arange(12)], axis=1
        )
        pts = np.asarray(pv.Spline(ca, 48).points, dtype=float)
        side, normal = _cartoon_ribbon_frames(ca, pts)
        assert np.isfinite(side).all() and np.isfinite(normal).all()
        assert np.allclose(np.linalg.norm(side, axis=1), 1.0, atol=1e-9)


class TestRibbonPayload:
    """The extruded ribbon must stay as cheap on the wire as the tube it
    replaced. D3's go/no-go gate was a payload number, and three separate
    details each roughly doubled it while the render looked identical:
    polygons instead of strips, missing normals (which makes
    ``smooth_shading`` triangulate the strips away), and float64 arrays.
    """

    def _ribbon(self, n_residues=400):
        import numpy as np
        import pyvista as pv

        from vibeview.renderers.structure import (
            _CARTOON_SAMPLES_PER_RESIDUE,
            _cartoon_ribbon_frames,
            _extrude_ribbon,
        )

        ca = _ideal_helix(n_residues)
        n = n_residues * _CARTOON_SAMPLES_PER_RESIDUE
        pts = np.asarray(pv.Spline(ca, n).points, dtype=float)
        side, normal = _cartoon_ribbon_frames(ca, pts)
        return _extrude_ribbon(
            pts, side, normal, np.full(n, 1.10), np.full(n, 0.20)
        )

    def test_surface_is_triangle_strips(self):
        mesh = self._ribbon()
        from vibeview.renderers.structure import _CARTOON_SIDES

        assert mesh.n_strips == _CARTOON_SIDES, mesh.n_strips
        # Only the two end caps are polygons.
        assert mesh.n_faces_strict == 2, mesh.n_faces_strict

    def test_arrays_are_float32(self):
        import numpy as np

        mesh = self._ribbon()
        assert mesh.points.dtype == np.float32
        assert mesh.point_data["Normals"].dtype == np.float32

    def test_smooth_shading_does_not_triangulate_the_strips(self):
        """The trap: ``add_mesh(smooth_shading=True)`` computes normals for
        a mesh that has none, and that rewrites the strips as individual
        triangles. Same picture, 2.5x the payload."""
        import pyvista as pv

        from vibeview.renderers.structure import _CARTOON_SIDES

        plotter = pv.Plotter(off_screen=True)
        plotter.add_mesh(self._ribbon(), smooth_shading=True, name="r")
        dataset = _actor_dataset(next(iter(plotter.actors.values())))
        assert dataset.n_strips == _CARTOON_SIDES, dataset.n_strips

    def test_normals_point_outward(self):
        """Inward normals render the ribbon as a dark cavity."""
        import numpy as np
        import pyvista as pv

        from vibeview.renderers.structure import (
            _CARTOON_SIDES,
            _cartoon_ribbon_frames,
            _extrude_ribbon,
        )

        ca = _ideal_helix(30)
        pts = np.asarray(pv.Spline(ca, 120).points, dtype=float)
        side, normal = _cartoon_ribbon_frames(ca, pts)
        mesh = _extrude_ribbon(
            pts, side, normal, np.full(120, 1.10), np.full(120, 0.20)
        )
        ring = np.asarray(mesh.points).reshape(-1, _CARTOON_SIDES, 3)
        normals = mesh.point_data["Normals"].reshape(-1, _CARTOON_SIDES, 3)
        # Direction from the path to the surface point. On an ellipse the
        # normal is not parallel to this, but it must never oppose it.
        outward = ring - pts[:, None, :]
        outward /= np.linalg.norm(outward, axis=2, keepdims=True)
        assert np.sum(normals * outward, axis=2).min() > 0.0

    def test_payload_stays_within_the_tube_it_replaced(self):
        """Serialised through trame-vtk's own mesh serializer, which is
        what VtkLocalView pushes, so this is the real wire cost."""
        import json

        import numpy as np
        import pyvista as pv
        from trame_vtk.modules.vtk.serializers.mesh import mesh as vtk_mesh

        from vibeview.renderers.structure import (
            _CARTOON_SAMPLES_PER_RESIDUE,
            _CARTOON_SIDES,
        )

        n_residues = 400
        ca = _ideal_helix(n_residues)
        spline = pv.Spline(ca, n_residues * _CARTOON_SAMPLES_PER_RESIDUE)
        tube = spline.tube(radius=0.8, n_sides=_CARTOON_SIDES)

        def wire(mesh):
            return len(json.dumps(vtk_mesh(mesh), default=str).encode())

        ribbon_bytes = wire(self._ribbon(n_residues))
        tube_bytes = wire(tube)
        # 10% of headroom over the tube, not a factor of two.
        assert ribbon_bytes < tube_bytes * 1.1, (ribbon_bytes, tube_bytes)
        assert np.isclose(
            self._ribbon(n_residues).triangulate().n_cells,
            tube.triangulate().n_cells,
            rtol=0.01,
        )


class TestSheetArrows:
    """Strands end in an arrowhead: that is how a sheet shows direction."""

    def test_arrow_steps_out_then_tapers_to_a_point(self):
        import numpy as np

        from vibeview.renderers.structure import (
            _CARTOON_ARROW_TIP,
            _CARTOON_ARROW_WIDTH,
            _CARTOON_SS_WIDTH,
            _apply_sheet_arrows,
        )

        labels = "C" * 5 + "E" * 8 + "C" * 5
        n_samples = len(labels) * 4
        width = np.full(n_samples, _CARTOON_SS_WIDTH["E"])
        _apply_sheet_arrows(width, labels, n_samples)

        assert width.max() == pytest.approx(_CARTOON_ARROW_WIDTH)
        assert width.min() == pytest.approx(_CARTOON_ARROW_TIP)
        # The shoulder must be a step, not a ramp: the widest sample sits
        # immediately after a plain-strand sample.
        shoulder = int(np.argmax(width))
        assert width[shoulder - 1] < _CARTOON_SS_WIDTH["E"] + 1e-9
        # And from the shoulder the width only ever decreases.
        run = width[shoulder : shoulder + 9]
        assert (np.diff(run) < 0).all(), run

    def test_arrow_points_at_the_c_terminus(self):
        """An arrow drawn on the N-terminal end would be actively
        misleading — it is the direction of the chain."""
        import numpy as np

        from vibeview.renderers.structure import (
            _CARTOON_SS_WIDTH,
            _apply_sheet_arrows,
        )

        labels = "C" * 4 + "E" * 8 + "C" * 4
        n_samples = len(labels) * 4
        width = np.full(n_samples, _CARTOON_SS_WIDTH["E"])
        _apply_sheet_arrows(width, labels, n_samples)
        tip = int(np.argmin(width))
        shoulder = int(np.argmax(width))
        assert shoulder < tip, (shoulder, tip)
        # And the tip is at the strand's own C-terminal end, not past it.
        last_strand_sample = int(round(11 * (n_samples - 1) / (len(labels) - 1)))
        assert abs(tip - last_strand_sample) <= 1

    def test_every_strand_run_gets_its_own_arrow(self):
        import numpy as np

        from vibeview.renderers.structure import (
            _CARTOON_ARROW_WIDTH,
            _CARTOON_SS_WIDTH,
            _apply_sheet_arrows,
        )

        labels = "CC" + "E" * 6 + "CCCC" + "E" * 6 + "CC"
        n_samples = len(labels) * 4
        width = np.full(n_samples, _CARTOON_SS_WIDTH["E"])
        _apply_sheet_arrows(width, labels, n_samples)
        shoulders = np.flatnonzero(
            np.isclose(width, _CARTOON_ARROW_WIDTH, atol=1e-9)
        )
        # Two separated shoulders, not one.
        gaps = np.diff(shoulders)
        assert len(shoulders) == 2, shoulders
        assert gaps.min() > 4, shoulders

    def test_a_two_residue_bridge_gets_no_arrow(self):
        """D2 reads a two-residue run as a bridge; there is no room for a
        barb and drawing one would invent a sheet."""
        import numpy as np

        from vibeview.renderers.structure import (
            _CARTOON_SS_WIDTH,
            _apply_sheet_arrows,
        )

        labels = "CCCCEECCCC"
        n_samples = len(labels) * 4
        width = np.full(n_samples, _CARTOON_SS_WIDTH["E"])
        before = width.copy()
        _apply_sheet_arrows(width, labels, n_samples)
        assert np.array_equal(width, before)

    def test_helix_only_chain_gets_no_arrow(self):
        import numpy as np

        from vibeview.renderers.structure import (
            _CARTOON_SS_WIDTH,
            _apply_sheet_arrows,
        )

        labels = "CC" + "H" * 12 + "CC"
        width = np.full(len(labels) * 4, _CARTOON_SS_WIDTH["H"])
        before = width.copy()
        _apply_sheet_arrows(width, labels, len(width))
        assert np.array_equal(width, before)

    def test_strand_runs_are_found_at_both_ends(self):
        from vibeview.renderers.structure import _strand_runs

        assert _strand_runs("EEECCEE") == [(0, 2), (5, 6)]
        assert _strand_runs("CCC") == []
        assert _strand_runs("EEEE") == [(0, 3)]

    def test_arrow_reaches_the_rendered_mesh(self, tmp_path):
        """End to end: a strand's mesh must actually narrow to a point.
        The profile could be perfect and still never reach the geometry."""
        import numpy as np
        import pyvista as pv

        from vibeview.converters import pdb_to_qvf
        from vibeview.qvf import QVFReader
        from vibeview.renderers.structure import (
            _CARTOON_ARROW_WIDTH,
            StructureRenderer,
        )

        # Two ideal strands joined by a loop, so D2 assigns E and the
        # arrowheads have somewhere to point.
        ca = np.vstack(
            [
                _ideal_strand(9),
                _ideal_strand(9, start=(5.0, 0.0, 9 * 3.3 + 4.0))[::-1],
            ]
        )
        lines = [
            "ATOM  " + f"{seq:>5}" + "  CA  ALA A" + f"{seq:>4}" + "    "
            + f"{pos[0]:8.3f}{pos[1]:8.3f}{pos[2]:8.3f}"
            + f"{1.0:6.2f}{0.0:6.2f}"
            for seq, pos in enumerate(ca, start=1)
        ]
        pdb = tmp_path / "sheet.pdb"
        pdb.write_text("\n".join(lines) + "\n")
        q = tmp_path / "sheet.qvf"
        q.write_bytes(pdb_to_qvf(pdb).getvalue())
        reader = QVFReader(q)
        sd = reader.read_structure()
        # The geometry above is built in this test and is deterministic, so
        # failing to read as a strand is a regression in the assignment code,
        # not an environment we lack. A skip here would retire the test.
        assert "E" in sd.secondary_structure("A"), (
            "the synthetic beta geometry no longer reads as a strand; "
            "secondary-structure assignment regressed"
        )

        plotter = pv.Plotter(off_screen=True)
        StructureRenderer(reader.get_section("structure"), reader).add_to_plotter(
            plotter, representation="cartoon"
        )
        mesh = _actor_dataset(next(iter(plotter.actors.values())))
        # Per-ring radial extent about the ring centre: the widest ring
        # must be the arrow shoulder, and some ring must be near-zero.
        from vibeview.renderers.structure import _CARTOON_SIDES

        rings = np.asarray(mesh.points)[: -0 or None].reshape(-1, _CARTOON_SIDES, 3)
        spread = np.linalg.norm(
            rings - rings.mean(axis=1, keepdims=True), axis=2
        ).max(axis=1)
        assert spread.max() > _CARTOON_ARROW_WIDTH * 0.9, spread.max()
        assert spread.min() < 0.3, spread.min()


class TestResidueTypeColour:
    """D4 colour-by residue type, in the RasMol amino scheme."""

    def _reader(self, tmp_path, residues, chain="A"):
        """residues: list of 3-letter names, one per CA, along a helix."""
        import numpy as np

        from vibeview.converters import pdb_to_qvf
        from vibeview.qvf import QVFReader

        ca = _ideal_helix(len(residues))
        lines = [
            "ATOM  " + f"{i:>5}" + "  CA  " + res.ljust(3) + " " + chain
            + f"{i:>4}" + "    "
            + f"{pos[0]:8.3f}{pos[1]:8.3f}{pos[2]:8.3f}"
            + f"{1.0:6.2f}{0.0:6.2f}"
            for i, (res, pos) in enumerate(zip(residues, ca, strict=True), start=1)
        ]
        pdb = tmp_path / "res.pdb"
        pdb.write_text("\n".join(lines) + "\n")
        q = tmp_path / "res.qvf"
        q.write_bytes(pdb_to_qvf(pdb).getvalue())
        assert np is not None
        return QVFReader(q)

    def test_names_line_up_with_the_trace(self, tmp_path):
        from vibeview.renderers.structure import _ca_residue_names

        names = ["ALA", "GLY", "ASP", "LYS", "TRP", "PRO"] * 3
        sd = self._reader(tmp_path, names).read_structure()
        got = _ca_residue_names(sd, "A")
        assert got == names, got
        assert len(got) == len(sd.backbone_trace("A"))

    def test_each_residue_type_gets_its_own_colour(self, tmp_path):
        import numpy as np

        from vibeview.renderers.structure import (
            _CARTOON_RESIDUE_COLOR,
            _cartoon_residue_colors,
        )

        # One residue per distinct colour class in the scheme.
        names = ["ASP", "LYS", "PHE", "TRP", "CYS", "SER", "ASN", "LEU",
                 "ALA", "GLY", "PRO", "HIS"]
        sd = self._reader(tmp_path, names).read_structure()
        n = len(sd.backbone_trace("A"))
        # One sample per residue, so no resampling can drop one.
        rgb = _cartoon_residue_colors(sd, "A", n, n)
        assert rgb is not None
        present = {tuple(c) for c in np.unique(rgb, axis=0)}
        for name in names:
            assert _CARTOON_RESIDUE_COLOR[name] in present, name
        assert len(present) == len({_CARTOON_RESIDUE_COLOR[x] for x in names})

    def test_acidic_and_basic_are_not_the_same_colour(self, tmp_path):
        """The scheme's whole point is that a reader knows the mapping;
        acidic must read red and basic blue."""
        from vibeview.renderers.structure import _CARTOON_RESIDUE_COLOR

        assert _CARTOON_RESIDUE_COLOR["ASP"] == _CARTOON_RESIDUE_COLOR["GLU"]
        assert _CARTOON_RESIDUE_COLOR["LYS"] == _CARTOON_RESIDUE_COLOR["ARG"]
        asp, lys = _CARTOON_RESIDUE_COLOR["ASP"], _CARTOON_RESIDUE_COLOR["LYS"]
        assert asp[0] > asp[2], asp   # ASP/GLU red
        assert lys[2] > lys[0], lys   # LYS/ARG blue
        assert asp != lys
        assert len(_CARTOON_RESIDUE_COLOR) == 20, len(_CARTOON_RESIDUE_COLOR)

    def test_unknown_residue_takes_the_fallback(self, tmp_path):
        import numpy as np

        from vibeview.renderers.structure import (
            _CARTOON_RESIDUE_FALLBACK,
            _cartoon_residue_colors,
        )

        names = ["ALA"] * 6 + ["XYZ"] * 6
        sd = self._reader(tmp_path, names).read_structure()
        n = len(sd.backbone_trace("A"))
        rgb = _cartoon_residue_colors(sd, "A", n, n)
        assert _CARTOON_RESIDUE_FALLBACK in {
            tuple(c) for c in np.unique(rgb, axis=0)
        }

    def test_lowercase_names_still_resolve(self, tmp_path):
        import numpy as np

        from vibeview.renderers.structure import (
            _CARTOON_RESIDUE_COLOR,
            _CARTOON_RESIDUE_FALLBACK,
            _cartoon_residue_colors,
        )

        sd = self._reader(tmp_path, ["ala"] * 8 + ["asp"] * 8).read_structure()
        n = len(sd.backbone_trace("A"))
        rgb = _cartoon_residue_colors(sd, "A", n, n)
        present = {tuple(c) for c in np.unique(rgb, axis=0)}
        assert _CARTOON_RESIDUE_COLOR["ASP"] in present
        assert _CARTOON_RESIDUE_FALLBACK not in present

    def test_colours_are_not_smoothed(self, tmp_path):
        import numpy as np

        from vibeview.renderers.structure import (
            _CARTOON_RESIDUE_COLOR,
            _CARTOON_RESIDUE_FALLBACK,
            _cartoon_residue_colors,
        )

        names = ["ASP", "LYS"] * 10
        sd = self._reader(tmp_path, names).read_structure()
        n = len(sd.backbone_trace("A"))
        rgb = _cartoon_residue_colors(sd, "A", n, n * 4)
        allowed = set(_CARTOON_RESIDUE_COLOR.values()) | {
            _CARTOON_RESIDUE_FALLBACK
        }
        for colour in np.unique(rgb, axis=0):
            assert tuple(colour) in allowed, f"blended colour {tuple(colour)}"

    def test_a_chain_with_no_residue_names_falls_back(self, tmp_path):
        """Residue numbers without names would render one flat fallback
        colour, which says nothing and reads as a bug."""
        from vibeview.renderers.structure import _cartoon_residue_colors

        sd = self._reader(tmp_path, ["   "] * 12).read_structure()
        n = len(sd.backbone_trace("A"))
        assert _cartoon_residue_colors(sd, "A", n, n * 4) is None

    def test_mismatched_length_falls_back(self, tmp_path):
        from vibeview.renderers.structure import _cartoon_residue_colors

        sd = self._reader(tmp_path, ["ALA"] * 12).read_structure()
        assert _cartoon_residue_colors(sd, "A", 999, 80) is None

    def test_residue_mode_reaches_the_mesh(self, tmp_path):
        import numpy as np
        import pyvista as pv

        from vibeview.renderers.structure import (
            _CARTOON_RESIDUE_COLOR,
            StructureRenderer,
        )

        names = ["ASP", "LYS", "TRP", "GLY"] * 5
        reader = self._reader(tmp_path, names)
        plotter = pv.Plotter(off_screen=True)
        StructureRenderer(reader.get_section("structure"), reader).add_to_plotter(
            plotter, representation="cartoon", cartoon_color_mode="residue"
        )
        dataset = _actor_dataset(next(iter(plotter.actors.values())))
        rgb = dataset.point_data["cartoon_rgb"]
        assert len(rgb) == dataset.n_points
        present = {tuple(c) for c in np.unique(rgb, axis=0)}
        assert _CARTOON_RESIDUE_COLOR["ASP"] in present
        assert _CARTOON_RESIDUE_COLOR["LYS"] in present

    def test_selection_still_overrides_residue_colouring(self, tmp_path):
        import numpy as np
        import pyvista as pv

        from vibeview.renderers.structure import (
            _CARTOON_SELECTION_COLOR,
            StructureRenderer,
        )

        reader = self._reader(tmp_path, ["ALA", "ASP", "LYS"] * 6)
        plotter = pv.Plotter(off_screen=True)
        StructureRenderer(reader.get_section("structure"), reader).add_to_plotter(
            plotter,
            representation="cartoon",
            cartoon_color_mode="residue",
            residue_selection="A/5-9",
        )
        dataset = _actor_dataset(next(iter(plotter.actors.values())))
        present = {
            tuple(c)
            for c in np.unique(dataset.point_data["cartoon_rgb"], axis=0)
        }
        assert _CARTOON_SELECTION_COLOR in present
        assert len(present) >= 2

    def test_controller_accepts_residue_mode(self, tmp_path):
        from vibeview.app import create_app

        app = create_app(self._reader(tmp_path, ["ALA", "ASP"] * 8))
        app.controller.set_cartoon_color_mode("residue")
        assert app.state.cartoon_color_mode == "residue"
        app.controller.set_cartoon_color_mode(["structure"])
        assert app.state.cartoon_color_mode == "structure"

    def test_controller_still_rejects_an_unknown_mode(self, tmp_path):
        from vibeview.app import create_app

        app = create_app(self._reader(tmp_path, ["ALA", "ASP"] * 8))
        before = app.state.cartoon_color_mode
        # Sentinel must be a value that can never become a real mode.
        # This test used "bfactor" until 2026-07-26, chosen when b-factor
        # colouring did not exist; it then became valid and the test
        # started failing for the right reason.
        app.controller.set_cartoon_color_mode("not-a-colour-mode")
        assert app.state.cartoon_color_mode == before


class TestSelectionGrammar:
    """D4 selection: three forms, no operators. The grammar is small on
    purpose, so what it refuses matters as much as what it accepts."""

    def test_bare_chain_selects_the_whole_chain(self):
        from vibeview.renderers.structure import parse_residue_selection

        terms, rejected = parse_residue_selection("A")
        assert rejected == []
        assert len(terms) == 1
        assert terms[0].chain == "A"
        assert terms[0].start is None and terms[0].end is None
        assert terms[0].matches("A", 1)
        assert terms[0].matches("A", 9999)
        assert not terms[0].matches("B", 1)

    def test_range_and_single_residue(self):
        from vibeview.renderers.structure import parse_residue_selection

        terms, rejected = parse_residue_selection("A/24-38, B/7")
        assert rejected == []
        assert (terms[0].chain, terms[0].start, terms[0].end) == ("A", 24, 38)
        assert (terms[1].chain, terms[1].start, terms[1].end) == ("B", 7, 7)
        assert terms[0].matches("A", 24) and terms[0].matches("A", 38)
        assert not terms[0].matches("A", 23)
        assert not terms[0].matches("A", 39)

    def test_wildcard_chain_matches_every_chain(self):
        from vibeview.renderers.structure import parse_residue_selection

        terms, _ = parse_residue_selection("*/24-38")
        assert terms[0].matches("A", 30)
        assert terms[0].matches("Z", 30)
        assert not terms[0].matches("A", 39)

    def test_commas_and_whitespace_are_the_same_separator(self):
        from vibeview.renderers.structure import parse_residue_selection

        a, _ = parse_residue_selection("A/24-38,B")
        b, _ = parse_residue_selection("A/24-38 B")
        c, _ = parse_residue_selection("  A/24-38 ,  B  ")
        assert a == b == c

    def test_reversed_range_is_normalised(self):
        from vibeview.renderers.structure import parse_residue_selection

        terms, _ = parse_residue_selection("A/38-24")
        assert (terms[0].start, terms[0].end) == (24, 38)

    def test_a_bare_number_is_a_chain_id_not_a_residue(self):
        """mmCIF chain ids can be numeric, so guessing from the shape of
        the token would make a selection mean different things in
        different files. `24` is chain 24; `*/24` is residue 24."""
        from vibeview.renderers.structure import parse_residue_selection

        terms, rejected = parse_residue_selection("24")
        assert rejected == []
        assert terms[0].chain == "24"
        assert terms[0].start is None
        assert terms[0].matches("24", 5)
        assert not terms[0].matches("A", 24)

    def test_chain_ids_are_case_sensitive(self):
        """In a PDB, chain a and chain A are two different chains."""
        from vibeview.renderers.structure import parse_residue_selection

        terms, _ = parse_residue_selection("a")
        assert not terms[0].matches("A", 1)
        assert terms[0].matches("a", 1)

    def test_unreadable_terms_are_returned_not_raised(self):
        """A half-typed selection must narrow nothing and say so, not
        blank the viewport or throw."""
        from vibeview.renderers.structure import parse_residue_selection

        terms, rejected = parse_residue_selection("A/xx, /5, B")
        assert [t.chain for t in terms] == ["B"]
        assert set(rejected) == {"A/xx", "/5"}

    def test_empty_selection_is_no_terms(self):
        from vibeview.renderers.structure import parse_residue_selection

        for spec in ("", "   ", ",,", None):
            terms, rejected = parse_residue_selection(spec)
            assert terms == [] and rejected == []


class TestSelectionHighlight:
    """The selection has to reach the mesh, and only where it matches."""

    def _reader(self, tmp_path, chains):
        """chains: {chain_id: (n_residues, first_resSeq, x_offset)}"""
        import numpy as np

        from vibeview.converters import pdb_to_qvf
        from vibeview.qvf import QVFReader

        lines, serial = [], 0
        for chain_id, (n, first, dx) in chains.items():
            ca = _ideal_helix(n, start=(dx, 0.0, 0.0))
            for k, pos in enumerate(ca):
                serial += 1
                seq = first + k
                lines.append(
                    "ATOM  " + f"{serial:>5}" + "  CA  ALA " + chain_id
                    + f"{seq:>4}" + "    "
                    + f"{pos[0]:8.3f}{pos[1]:8.3f}{pos[2]:8.3f}"
                    + f"{1.0:6.2f}{0.0:6.2f}"
                )
        pdb = tmp_path / "sel.pdb"
        pdb.write_text("\n".join(lines) + "\n")
        q = tmp_path / "sel.qvf"
        q.write_bytes(pdb_to_qvf(pdb).getvalue())
        assert np is not None
        return QVFReader(q)

    def _render(self, reader, **kwargs):
        import pyvista as pv

        from vibeview.renderers.structure import StructureRenderer

        plotter = pv.Plotter(off_screen=True)
        StructureRenderer(reader.get_section("structure"), reader).add_to_plotter(
            plotter, representation="cartoon", **kwargs
        )
        return plotter

    def test_ca_keys_line_up_with_the_backbone_trace(self, tmp_path):
        """The mask is indexed by trace position, so a key list of the
        wrong length or order would highlight the wrong residues. This is
        the one invariant the highlight rests on."""
        from vibeview.renderers.structure import _ca_residue_keys

        sd = self._reader(
            tmp_path, {"A": (14, 1, 0.0), "B": (10, 501, 60.0)}
        ).read_structure()
        assert len(_ca_residue_keys(sd)) == len(sd.backbone_trace())
        for chain in sd.chains():
            keys = _ca_residue_keys(sd, chain)
            assert len(keys) == len(sd.backbone_trace(chain))
            assert {c for c, _s in keys} == {chain}
        # And the residue numbers come back in file order, not sorted.
        assert [s for _c, s in _ca_residue_keys(sd, "B")] == list(
            range(501, 511)
        )

    def test_selected_residues_render_white(self, tmp_path):
        import numpy as np

        from vibeview.renderers.structure import _CARTOON_SELECTION_COLOR

        reader = self._reader(tmp_path, {"A": (16, 1, 0.0)})
        plotter = self._render(reader, residue_selection="A/5-9")
        dataset = _actor_dataset(next(iter(plotter.actors.values())))
        rgb = dataset.point_data["cartoon_rgb"]
        colours = {tuple(c) for c in np.unique(rgb, axis=0)}
        assert _CARTOON_SELECTION_COLOR in colours
        # Not everything: the unselected part keeps its chain colour.
        assert len(colours) >= 2, colours

    def test_no_selection_leaves_chain_mode_without_an_rgb_array(self, tmp_path):
        """The unselected case has to stay byte-for-byte the old one, or
        every chain-coloured protein pays for a feature it is not using."""
        reader = self._reader(tmp_path, {"A": (16, 1, 0.0)})
        for spec in ("", "   "):
            plotter = self._render(reader, residue_selection=spec)
            dataset = _actor_dataset(next(iter(plotter.actors.values())))
            assert "cartoon_rgb" not in dataset.point_data, spec

    def test_a_selection_matching_nothing_changes_nothing(self, tmp_path):
        """Naming an absent chain must not blank or recolour the ribbon."""
        reader = self._reader(tmp_path, {"A": (16, 1, 0.0)})
        plotter = self._render(reader, residue_selection="Q/1-9")
        dataset = _actor_dataset(next(iter(plotter.actors.values())))
        assert "cartoon_rgb" not in dataset.point_data

    def test_selection_survives_secondary_structure_colouring(self, tmp_path):
        """A selection must be visible in both colour modes, so it
        overrides rather than blending with the structure colours."""
        import numpy as np

        from vibeview.renderers.structure import _CARTOON_SELECTION_COLOR

        reader = self._reader(tmp_path, {"A": (18, 1, 0.0)})
        plotter = self._render(
            reader, cartoon_color_mode="structure", residue_selection="A/6-10"
        )
        dataset = _actor_dataset(next(iter(plotter.actors.values())))
        rgb = dataset.point_data["cartoon_rgb"]
        colours = {tuple(c) for c in np.unique(rgb, axis=0)}
        assert _CARTOON_SELECTION_COLOR in colours
        assert len(colours) >= 2, colours

    def test_selecting_one_chain_leaves_the_other_alone(self, tmp_path):
        import numpy as np

        from vibeview.renderers.structure import _CARTOON_SELECTION_COLOR

        reader = self._reader(tmp_path, {"A": (14, 1, 0.0), "B": (14, 1, 60.0)})
        plotter = self._render(reader, residue_selection="B")
        by_name = {n: _actor_dataset(a) for n, a in plotter.actors.items()}
        assert "cartoon_rgb" not in by_name["cartoon_chain_A"].point_data
        rgb = by_name["cartoon_chain_B"].point_data["cartoon_rgb"]
        # A whole-chain selection is uniformly white.
        assert {tuple(c) for c in np.unique(rgb, axis=0)} == {
            _CARTOON_SELECTION_COLOR
        }

    def test_chains_with_reused_residue_numbers_do_not_bleed(self, tmp_path):
        """Both chains number from 1 here, so a mask keyed on residue
        number alone would light up both."""
        import numpy as np

        from vibeview.renderers.structure import _CARTOON_SELECTION_COLOR

        reader = self._reader(tmp_path, {"A": (14, 1, 0.0), "B": (14, 1, 60.0)})
        plotter = self._render(reader, residue_selection="A/3-6")
        by_name = {n: _actor_dataset(a) for n, a in plotter.actors.items()}
        assert "cartoon_rgb" not in by_name["cartoon_chain_B"].point_data
        rgb = by_name["cartoon_chain_A"].point_data["cartoon_rgb"]
        assert _CARTOON_SELECTION_COLOR in {
            tuple(c) for c in np.unique(rgb, axis=0)
        }

    def test_highlight_is_not_smoothed(self, tmp_path):
        """Every colour on the mesh is either the chain's or the
        selection's — a blend would imply a half-selected residue."""
        import numpy as np

        from vibeview.renderers.structure import (
            _CARTOON_SELECTION_COLOR,
            _CHAIN_COLORS,
            _hex_to_rgb,
        )

        reader = self._reader(tmp_path, {"A": (20, 1, 0.0)})
        plotter = self._render(reader, residue_selection="A/8-12")
        dataset = _actor_dataset(next(iter(plotter.actors.values())))
        rgb = dataset.point_data["cartoon_rgb"]
        allowed = {
            _CARTOON_SELECTION_COLOR,
            tuple(int(v) for v in _hex_to_rgb(_CHAIN_COLORS[0])),
        }
        for colour in np.unique(rgb, axis=0):
            assert tuple(colour) in allowed, f"blended colour {tuple(colour)}"

    def test_wildcard_selects_across_chains(self, tmp_path):
        import numpy as np

        from vibeview.renderers.structure import _CARTOON_SELECTION_COLOR

        reader = self._reader(tmp_path, {"A": (14, 1, 0.0), "B": (14, 1, 60.0)})
        plotter = self._render(reader, residue_selection="*/4-8")
        for name, actor in plotter.actors.items():
            if not name.startswith("cartoon_chain_"):
                continue
            rgb = _actor_dataset(actor).point_data["cartoon_rgb"]
            assert _CARTOON_SELECTION_COLOR in {
                tuple(c) for c in np.unique(rgb, axis=0)
            }, name


class TestSelectionInAtomRepresentations:
    """The typed D4 selection is shared by all structure representations."""

    @staticmethod
    def _reader(tmp_path, records, stem="atoms"):
        from vibeview.converters import pdb_to_qvf
        from vibeview.qvf import QVFReader

        qvf = tmp_path / f"{stem}.qvf"
        qvf.write_bytes(pdb_to_qvf(_pdb(tmp_path, records)).getvalue())
        return QVFReader(qvf)

    @staticmethod
    def _render(reader, representation, selection):
        from vibeview.renderers.structure import StructureRenderer

        plotter = pv.Plotter(off_screen=True)
        StructureRenderer(reader.get_section("structure"), reader).add_to_plotter(
            plotter,
            representation=representation,
            residue_selection=selection,
        )
        return plotter

    @staticmethod
    def _actor_color(actor):
        return tuple(round(float(v), 6) for v in actor.GetProperty().GetColor())

    @staticmethod
    def _two_residues():
        # Two atoms per residue. The 1.5 A bond between atoms 1 and 2 crosses
        # the residue boundary; it must keep its bond-order colour when only
        # residue 1 is selected.
        return [
            (" CA ", "ALA", "A", 1, 0.0),
            (" N  ", "ALA", "A", 1, 1.2),
            (" CA ", "GLY", "A", 2, 2.7),
            (" N  ", "GLY", "A", 2, 3.9),
        ]

    @pytest.mark.parametrize("representation", ["ball_and_stick", "space_filling"])
    def test_selected_residue_atoms_are_white(self, tmp_path, representation):
        reader = self._reader(tmp_path, self._two_residues())
        plotter = self._render(reader, representation, "A/1")

        assert self._actor_color(plotter.actors["atom_0_0"]) == (1.0, 1.0, 1.0)
        assert self._actor_color(plotter.actors["atom_1_1"]) == (1.0, 1.0, 1.0)
        assert self._actor_color(plotter.actors["atom_2_2"]) != (1.0, 1.0, 1.0)
        assert self._actor_color(plotter.actors["atom_3_3"]) != (1.0, 1.0, 1.0)

    @pytest.mark.parametrize("representation", ["sticks_only", "wireframe"])
    def test_selected_internal_bonds_are_white_without_boundary_bleed(
        self, tmp_path, representation
    ):
        reader = self._reader(tmp_path, self._two_residues())
        plotter = self._render(reader, representation, "A/1")

        assert self._actor_color(plotter.actors["bond_0_1"]) == (1.0, 1.0, 1.0)
        assert self._actor_color(plotter.actors["bond_1_2"]) != (1.0, 1.0, 1.0)
        assert self._actor_color(plotter.actors["bond_2_3"]) != (1.0, 1.0, 1.0)

    def test_reused_residue_numbers_do_not_cross_chain_boundaries(self, tmp_path):
        records = [
            (" CA ", "ALA", "A", 1, 0.0),
            (" N  ", "ALA", "A", 1, 1.2),
            (" CA ", "ALA", "B", 1, 20.0),
            (" N  ", "ALA", "B", 1, 21.2),
        ]
        plotter = self._render(self._reader(tmp_path, records), "ball_and_stick", "A/1")

        assert self._actor_color(plotter.actors["atom_0_0"]) == (1.0, 1.0, 1.0)
        assert self._actor_color(plotter.actors["atom_1_1"]) == (1.0, 1.0, 1.0)
        assert self._actor_color(plotter.actors["atom_2_2"]) != (1.0, 1.0, 1.0)
        assert self._actor_color(plotter.actors["atom_3_3"]) != (1.0, 1.0, 1.0)

    def test_large_atom_selection_keeps_glyph_batching(self, tmp_path):
        records = [
            (" CA ", "ALA", "A", seq, float(seq * 4))
            for seq in range(1, 102)
        ]
        reader = self._reader(tmp_path, records, stem="large-atoms")

        selected = self._render(reader, "space_filling", "A/1")
        groups = [
            actor for name, actor in selected.actors.items() if name.startswith("atom_group_")
        ]
        assert len(groups) == 2
        assert {self._actor_color(actor) for actor in groups} >= {(1.0, 1.0, 1.0)}

        unmatched = self._render(reader, "space_filling", "Q/1")
        unmatched_groups = [
            actor
            for name, actor in unmatched.actors.items()
            if name.startswith("atom_group_")
        ]
        assert len(unmatched_groups) == 1
        assert self._actor_color(unmatched_groups[0]) != (1.0, 1.0, 1.0)

    def test_selected_atoms_stay_white_in_every_periodic_replica(self):
        from types import SimpleNamespace

        import numpy as np

        from vibeview.qvf import Atom, StructureData
        from vibeview.renderers.structure import StructureRenderer

        structure = StructureData(
            atoms=[
                Atom("C", np.array([0.0, 0.0, 0.0]), 6, "CA", "ALA", 1, "A"),
                Atom("N", np.array([1.2, 0.0, 0.0]), 7, "N", "ALA", 1, "A"),
                Atom("C", np.array([4.0, 0.0, 0.0]), 6, "CA", "GLY", 2, "A"),
                Atom("N", np.array([5.2, 0.0, 0.0]), 7, "N", "GLY", 2, "A"),
            ],
            pbc=(True, False, False),
            lattice_vectors=np.diag([10.0, 20.0, 20.0]),
            bonds=[],
            dim=1,
        )

        class Reader:
            @staticmethod
            def read_structure():
                return structure

            @staticmethod
            def infer_bonds(_structure):
                return []

        plotter = pv.Plotter(off_screen=True)
        StructureRenderer(
            SimpleNamespace(id="structure", kind="structure"), Reader()
        ).add_to_plotter(
            plotter,
            representation="space_filling",
            replication=(2, 1, 1),
            residue_selection="A/1",
        )
        white = next(
            actor
            for name, actor in plotter.actors.items()
            if name.startswith("atom_group_")
            and self._actor_color(actor) == (1.0, 1.0, 1.0)
        )
        white.GetMapper().Update()
        bounds = white.GetMapper().dataset.bounds
        assert bounds[1] - bounds[0] > 10.0

    def test_large_bond_selection_stays_two_batched_actors(self, tmp_path):
        records = []
        for seq in range(1, 61):
            start = float((seq - 1) * 4)
            records.extend(
                [
                    (" CA ", "ALA", "A", seq, start),
                    (" N  ", "ALA", "A", seq, start + 1.2),
                ]
            )
        reader = self._reader(tmp_path, records, stem="large-bonds")
        plotter = self._render(reader, "sticks_only", "A/1-55")

        bond_actors = {
            name: actor
            for name, actor in plotter.actors.items()
            if name.startswith("bonds_")
        }
        assert set(bond_actors) == {"bonds_batched_0", "bonds_selected_0"}
        assert self._actor_color(bond_actors["bonds_selected_0"]) == (1.0, 1.0, 1.0)
        assert self._actor_color(bond_actors["bonds_batched_0"]) == (
            0.533333,
            0.533333,
            0.533333,
        )
        assert not bond_actors["bonds_selected_0"].GetMapper().GetScalarVisibility()
        assert not bond_actors["bonds_batched_0"].GetMapper().GetScalarVisibility()

    def test_static_scene_cleanup_removes_selected_batched_bonds_and_cartoon(self):
        from vibeview.app import _remove_static_structure

        plotter = pv.Plotter(off_screen=True)
        mesh = pv.Sphere()
        for name in (
            "bonds_batched_0",
            "bonds_selected_0",
            "cartoon_chain_A",
            "traj_atom_0",
        ):
            plotter.add_mesh(mesh, name=name)

        _remove_static_structure(plotter)

        assert "bonds_batched_0" not in plotter.actors
        assert "bonds_selected_0" not in plotter.actors
        assert "cartoon_chain_A" not in plotter.actors
        assert "traj_atom_0" in plotter.actors

    def test_supplied_residue_membership_wins_over_atom_fields(self):
        import numpy as np

        from vibeview.qvf import Atom, StructureData
        from vibeview.renderers.structure import (
            _selected_atom_indices,
            parse_residue_selection,
        )

        atoms = [
            Atom("C", np.array([0.0, 0.0, 0.0]), 6, "CA", "ALA", 9, "X"),
            Atom("N", np.array([1.2, 0.0, 0.0]), 7, "N", "ALA", 9, "X"),
        ]
        structure = StructureData(
            atoms=atoms,
            pbc=(False, False, False),
            lattice_vectors=None,
            bonds=None,
            supplied_residues=[{"chain": "A", "seq": 5, "atom_indices": [0, 1]}],
        )

        terms, _ = parse_residue_selection("A/5")
        assert _selected_atom_indices(structure, terms) == {0, 1}
        contradictory, _ = parse_residue_selection("X/9")
        assert _selected_atom_indices(structure, contradictory) == set()


class TestSelectionSummary:
    """A selection that matches nothing must say so, or it reads as a bug."""

    def _sd(self, tmp_path, chains):
        return TestSelectionHighlight()._reader(tmp_path, chains).read_structure()

    def test_counts_residues_and_names_chains(self, tmp_path):
        from vibeview.renderers.structure import summarize_selection

        sd = self._sd(tmp_path, {"A": (14, 1, 0.0), "B": (14, 1, 60.0)})
        matched, chains, complaints = summarize_selection(sd, "A/3-6, B")
        assert matched == 4 + 14
        assert chains == ["A", "B"]
        assert complaints == []

    def test_absent_chain_is_called_out_separately(self, tmp_path):
        from vibeview.renderers.structure import summarize_selection

        sd = self._sd(tmp_path, {"A": (14, 1, 0.0)})
        matched, chains, complaints = summarize_selection(sd, "Q/1-5")
        assert matched == 0
        assert chains == []
        assert any("no such chain" in c and "Q" in c for c in complaints)

    def test_a_file_with_no_chain_ids_points_at_the_wildcard(self, tmp_path):
        """A single-chain PDB with blank cols 22 groups under the empty
        chain id, which no selection can name. DHFR from the RCSB is one.
        Telling the user their chain does not exist, with no way to find
        the one that does, is the unhelpful failure to avoid."""
        from vibeview.converters import pdb_to_qvf
        from vibeview.qvf import QVFReader
        from vibeview.renderers.structure import summarize_selection

        out = tmp_path / "nochain.qvf"
        out.write_bytes(pdb_to_qvf(_pdb(tmp_path, _chain("", 12))).getvalue())
        sd = QVFReader(out).read_structure()
        assert list(sd.chains()) == [""]

        matched, _chains, complaints = summarize_selection(sd, "A/1-5")
        assert matched == 0
        assert any("no chain ids" in c and "*" in c for c in complaints)
        # And the wildcard actually works on it.
        matched, chains, complaints = summarize_selection(sd, "*/3-6")
        assert matched == 4
        assert chains == [""]
        assert complaints == []

    def test_named_chains_do_not_get_the_wildcard_hint(self, tmp_path):
        """The hint is for blank chain ids only — on a file that has real
        chain ids, a typo'd chain is just a typo."""
        from vibeview.renderers.structure import summarize_selection

        sd = self._sd(tmp_path, {"A": (14, 1, 0.0)})
        _matched, _chains, complaints = summarize_selection(sd, "Q")
        assert not any("no chain ids" in c for c in complaints)

    def test_unreadable_term_is_reported(self, tmp_path):
        from vibeview.renderers.structure import summarize_selection

        sd = self._sd(tmp_path, {"A": (14, 1, 0.0)})
        _matched, _chains, complaints = summarize_selection(sd, "A/nope")
        assert any("could not read" in c for c in complaints)

    def test_in_range_chain_with_no_matching_residues(self, tmp_path):
        """The chain exists but the range misses it — that is a different
        mistake from naming an absent chain, and must not be reported as
        one."""
        from vibeview.renderers.structure import summarize_selection

        sd = self._sd(tmp_path, {"A": (14, 1, 0.0)})
        matched, chains, complaints = summarize_selection(sd, "A/900-999")
        assert (matched, chains) == (0, [])
        assert complaints == []


class TestSelectionController:
    """Renderer-level tests all passed once while every create_app call
    raised TypeError, because the helper in the middle was missed. Both
    levels, always."""

    def _app(self, tmp_path, chains=None):
        from vibeview.app import create_app

        reader = TestSelectionHighlight()._reader(
            tmp_path, chains or {"A": (16, 1, 0.0), "B": (12, 1, 60.0)}
        )
        return create_app(reader)

    def test_app_builds_and_defaults_to_no_selection(self, tmp_path):
        app = self._app(tmp_path)
        assert app.state.residue_selection == ""
        assert app.state.residue_selection_summary == ""
        assert app.state.residue_selection_chains == ["A", "B"]
        assert app.state.residue_selection_available is True

    def test_setting_a_selection_reports_what_matched(self, tmp_path):
        app = self._app(tmp_path)
        app.controller.set_residue_selection("A/3-6")
        assert app.state.residue_selection == "A/3-6"
        assert "4 residues" in app.state.residue_selection_summary
        assert "chain A" in app.state.residue_selection_summary

    def test_singular_residue_reads_as_one(self, tmp_path):
        app = self._app(tmp_path)
        app.controller.set_residue_selection("A/3")
        assert "1 residue in" in app.state.residue_selection_summary

    def test_nothing_selected_says_so(self, tmp_path):
        app = self._app(tmp_path)
        app.controller.set_residue_selection("Q")
        assert "nothing selected" in app.state.residue_selection_summary
        assert "no such chain" in app.state.residue_selection_summary

    def test_clearing_clears_the_summary(self, tmp_path):
        app = self._app(tmp_path)
        app.controller.set_residue_selection("A")
        assert app.state.residue_selection_summary
        app.controller.set_residue_selection("")
        assert app.state.residue_selection == ""
        assert app.state.residue_selection_summary == ""

    def test_no_argument_commits_the_field_value(self, tmp_path):
        """The Enter and blur handlers pass no useful argument, so the
        controller has to read the committed value off state."""
        app = self._app(tmp_path)
        app.state.residue_selection = "A/2-4"
        app.controller.set_residue_selection()
        assert "3 residues" in app.state.residue_selection_summary

    def test_a_dom_event_argument_is_ignored(self, tmp_path):
        """Trame hands a serialised event object to a blur handler."""
        app = self._app(tmp_path)
        app.state.residue_selection = "A/2-4"
        app.controller.set_residue_selection({"type": "blur"})
        assert "3 residues" in app.state.residue_selection_summary

    def test_list_form_works(self, tmp_path):
        """A VTextField wired with [$event] passes a one-element list."""
        app = self._app(tmp_path)
        app.controller.set_residue_selection(["A/3-6"])
        assert app.state.residue_selection == "A/3-6"

    def test_atom_representation_selection_rebuilds(self, tmp_path, monkeypatch):
        import vibeview.app as app_module

        app = self._app(tmp_path)
        app.state.representation_style = "ball_and_stick"
        rebuilt = []
        monkeypatch.setattr(
            app_module,
            "_rebuild_scene",
            lambda *args, **kwargs: rebuilt.append((args, kwargs)),
        )

        app.controller.set_residue_selection("A/3-6")
        assert len(rebuilt) == 1

    def test_compare_mode_exit_restores_selected_cartoon(self, tmp_path, monkeypatch):
        from vibeview.renderers.structure import StructureRenderer

        rendered = []
        original = StructureRenderer.add_to_plotter

        def record_render(renderer, *args, **kwargs):
            rendered.append(kwargs.copy())
            return original(renderer, *args, **kwargs)

        monkeypatch.setattr(StructureRenderer, "add_to_plotter", record_render)
        app = self._app(tmp_path)
        app.controller.set_residue_selection("A/3-6")
        rendered.clear()

        app.controller.toggle_compare_mode(True)
        assert app.state.structure_hidden is True
        app.controller.toggle_compare_mode(False)

        assert app.state.structure_hidden is False
        assert len(rendered) == 1
        assert rendered[0]["representation"] == "cartoon"
        assert rendered[0]["residue_selection"] == "A/3-6"

    def test_selection_field_is_gated_by_residue_data_not_cartoon_mode(self):
        import inspect

        from vibeview.app import _build_controls

        source = inspect.getsource(_build_controls)
        start = source.index('label="Select residues"')
        field_and_summary = source[start : start + 1_200]
        assert 'v_if=("residue_selection_available",)' in field_and_summary
        assert "representation_style === 'cartoon'" not in field_and_summary

    def test_unnamed_chain_reads_as_unnamed_not_as_a_dangling_word(self, tmp_path):
        """A PDB with blank cols 22 has one chain whose id is the empty
        string, so a naive summary ends in "in chain " with nothing after."""
        from vibeview.app import create_app
        from vibeview.converters import pdb_to_qvf
        from vibeview.qvf import QVFReader

        out = tmp_path / "nochain.qvf"
        out.write_bytes(pdb_to_qvf(_pdb(tmp_path, _chain("", 12))).getvalue())
        app = create_app(QVFReader(out))
        assert app.state.residue_selection_available is True
        app.controller.set_residue_selection("*/3-6")
        summary = app.state.residue_selection_summary
        assert summary == "4 residues in chain (unnamed)", summary

    def test_selection_on_a_molecule_does_not_raise(self, tmp_path):
        """An XYZ has no residues; the control is hidden, but the action
        must not explode if it is reached."""
        from vibeview.app import create_app
        from vibeview.converters import xyz_to_qvf
        from vibeview.qvf import QVFReader

        out = tmp_path / "m.qvf"
        out.write_bytes(xyz_to_qvf(b"2\nc\nO 0 0 0\nH 0 0 1\n").getvalue())
        app = create_app(QVFReader(out))
        assert app.state.residue_selection_chains == []
        assert app.state.residue_selection_available is False
        app.controller.set_residue_selection("A/1-5")
        assert "nothing selected" in app.state.residue_selection_summary

    def test_file_switch_refreshes_selection_availability_and_summary(self, tmp_path):
        from vibeview.app import create_app
        from vibeview.converters import xyz_to_qvf
        from vibeview.qvf import QVFReader

        protein = TestSelectionHighlight()._reader(
            tmp_path, {"A": (12, 1, 0.0)}
        )
        molecule_path = tmp_path / "molecule.qvf"
        molecule_path.write_bytes(
            xyz_to_qvf(b"2\nc\nO 0 0 0\nH 0 0 1\n").getvalue()
        )
        app = create_app([protein, QVFReader(molecule_path)])
        app.controller.set_residue_selection("A/2-4")
        assert "3 residues" in app.state.residue_selection_summary

        app.controller.switch_file(1)
        assert app.state.residue_selection_available is False
        assert app.state.residue_selection_chains == []
        assert "nothing selected" in app.state.residue_selection_summary

        app.controller.switch_file(0)
        assert app.state.residue_selection_available is True
        assert app.state.residue_selection_chains == ["A"]
        assert "3 residues" in app.state.residue_selection_summary
