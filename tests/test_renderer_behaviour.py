"""Tests for individual renderer behaviour — component selection, bond inference, etc."""

from __future__ import annotations

import numpy as np
import pytest

from vibeview.renderers.volume import VolumeRenderer


def test_failed_toon_enable_restores_actors_and_discards_snapshots() -> None:
    """A partial toon pass must not poison a later same-name actor."""
    from vibeview.renderers.structure import (
        disable_toon_rendering,
        enable_toon_rendering,
    )

    class Property:
        def __init__(self, interpolation: int, *, fail_outline: bool = False) -> None:
            self.interpolation = interpolation
            self.edge_visibility = 0
            self.fail_outline = fail_outline

        def GetInterpolation(self) -> int:
            return self.interpolation

        def GetEdgeVisibility(self) -> int:
            return self.edge_visibility

        def SetInterpolationToFlat(self) -> None:
            self.interpolation = 0

        def SetInterpolation(self, value: int) -> None:
            self.interpolation = value

        def SetEdgeVisibility(self, value: int) -> None:
            if self.fail_outline:
                self.fail_outline = False
                raise RuntimeError("outline failure")
            self.edge_visibility = int(value)

        def SetEdgeColor(self, *_args) -> None:
            pass

        def SetLineWidth(self, _value: float) -> None:
            pass

    class Actor:
        def __init__(self, prop: Property) -> None:
            self.prop = prop

        def GetProperty(self) -> Property:
            return self.prop

    class Plotter:
        def __init__(self) -> None:
            self.actors = {
                "good": Actor(Property(1)),
                "reused": Actor(Property(1, fail_outline=True)),
            }

        def render(self) -> None:
            pass

    plotter = Plotter()
    assert enable_toon_rendering(plotter) is False
    assert [a.GetProperty().GetInterpolation() for a in plotter.actors.values()] == [
        1,
        1,
    ]
    assert plotter._vibeview_toon_saved == {}

    replacement = Actor(Property(2))
    plotter.actors["reused"] = replacement
    assert enable_toon_rendering(plotter) is True
    disable_toon_rendering(plotter)
    assert replacement.GetProperty().GetInterpolation() == 2


class TestVolumeComponentSelection:
    """Test the _apply_component logic for orbital sections."""

    def test_real_component_real_data(self) -> None:
        data = np.array([1.0, -2.0, 3.0], dtype=np.float64)
        result = VolumeRenderer._apply_component(data, "real")
        np.testing.assert_array_equal(result, data)

    def test_real_component_complex_data(self) -> None:
        data = np.array([1.0 + 2.0j, -3.0 + 4.0j], dtype=np.complex128)
        result = VolumeRenderer._apply_component(data, "real")
        expected = np.array([1.0, -3.0])
        np.testing.assert_array_almost_equal(result, expected)

    def test_imag_component_complex_data(self) -> None:
        data = np.array([1.0 + 2.0j, -3.0 + 4.0j], dtype=np.complex128)
        result = VolumeRenderer._apply_component(data, "imag")
        expected = np.array([2.0, 4.0])
        np.testing.assert_array_almost_equal(result, expected)

    def test_abs_component(self) -> None:
        data = np.array([3.0 + 4.0j, -5.0 + 0.0j], dtype=np.complex128)
        result = VolumeRenderer._apply_component(data, "abs")
        expected = np.array([5.0, 5.0])
        np.testing.assert_array_almost_equal(result, expected)

    def test_density_component(self) -> None:
        data = np.array([2.0 + 0.0j, 3.0 + 0.0j], dtype=np.complex128)
        result = VolumeRenderer._apply_component(data, "density")
        expected = np.array([4.0, 9.0])
        np.testing.assert_array_almost_equal(result, expected)

    def test_unknown_component_returns_data(self) -> None:
        data = np.array([1.0, 2.0, 3.0], dtype=np.float64)
        result = VolumeRenderer._apply_component(data, "unknown")
        np.testing.assert_array_equal(result, data)


class TestBondInference:
    """Test the covalent-radii bond inference in QVFReader."""

    def test_water_bonds_inferred(self) -> None:
        from vibeview.qvf import Atom, QVFReader, StructureData

        atoms = [
            Atom("O", np.array([0.0, 0.0, 0.1173]), 8),
            Atom("H", np.array([0.0, 0.7572, -0.4692]), 1),
            Atom("H", np.array([0.0, -0.7572, -0.4692]), 1),
        ]
        structure = StructureData(
            atoms=atoms, pbc=(False, False, False), lattice_vectors=None, bonds=None
        )
        # We need a mock reader — just test the inference logic
        reader = QVFReader.__new__(QVFReader)
        bonds = reader.infer_bonds(structure)
        assert len(bonds) == 2  # O-H1, O-H2 (not H-H)
        # Both bonds should involve O (index 0)
        indices = {i for bond in bonds for i in bond}
        assert 0 in indices

    def test_no_bonds_for_distant_atoms(self) -> None:
        from vibeview.qvf import Atom, QVFReader, StructureData

        atoms = [
            Atom("He", np.array([0.0, 0.0, 0.0]), 2),
            Atom("He", np.array([10.0, 0.0, 0.0]), 2),
        ]
        structure = StructureData(
            atoms=atoms, pbc=(False, False, False), lattice_vectors=None, bonds=None
        )
        reader = QVFReader.__new__(QVFReader)
        bonds = reader.infer_bonds(structure)
        assert len(bonds) == 0

    def test_explicit_bonds_not_overridden(self) -> None:
        from vibeview.qvf import Atom, QVFReader, StructureData

        atoms = [
            Atom("H", np.array([0.0, 0.0, 0.0]), 1),
            Atom("H", np.array([1.0, 0.0, 0.0]), 1),
        ]
        explicit = [(0, 1)]
        structure = StructureData(
            atoms=atoms, pbc=(False, False, False), lattice_vectors=None, bonds=explicit
        )
        reader = QVFReader.__new__(QVFReader)
        bonds = reader.infer_bonds(structure)
        assert bonds == explicit


class TestPlotlyRendering:
    """Test that Plotly render_to_html() methods produce valid HTML."""

    def test_bands_plotly_html(self) -> None:
        import hashlib
        import json
        import tempfile
        import zipfile
        from pathlib import Path

        import numpy as np

        from vibeview.qvf import QVFReader
        from vibeview.renderers.bands import BandsRenderer

        kpath = json.dumps(
            {
                "segments": [
                    {"label_start": "G", "label_end": "X", "n_points": 3},
                    {"label_start": "X", "label_end": "M", "n_points": 3},
                ],
                "n_kpoints": 5,
                "n_bands": 3,
                "fermi": 0.0,
                "title": "Test",
            }
        ).encode()
        # Schema requires bands.eigenvalues shape = [n_spin, n_kpoints, n_bands]
        # (rank 3). Use [1, 5, 3] for one spin, five k-points, three bands.
        evals = np.zeros((1, 5, 3), dtype=np.float64).tobytes()

        sections = [
            {
                "id": "bands",
                "kind": "bands",
                "members": {
                    "kpath": {
                        "path": "s/k.json",
                        "format": "json",
                        "sha256": hashlib.sha256(kpath).hexdigest(),
                    },
                    "eigenvalues": {
                        "path": "s/e.dat",
                        "format": "binary",
                        "dtype": "float64",
                        "shape": [1, 5, 3],
                        "sha256": hashlib.sha256(evals).hexdigest(),
                    },
                },
            }
        ]
        manifest = {
            "qvf_version": 1,
            "source": {"program": "x", "version": "1", "calculation": "t"},
            "sections": sections,
        }
        tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
        with zipfile.ZipFile(tmp, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("s/k.json", kpath)
            zf.writestr("s/e.dat", evals)
        try:
            reader = QVFReader(Path(tmp.name))
            data = reader.read_bands("bands")
            assert data.eigenvalues.shape == (1, 5, 3)

            renderer = BandsRenderer(reader.get_section("bands"), reader)
            html = renderer.render_to_html()
            assert "<div" in html
            assert "plotly" in html.lower() or "Plotly" in html
            assert "Band" in html
            assert "Band 3" in html
            assert "Band 5" not in html
        finally:
            Path(tmp.name).unlink()

    def test_spectra_plotly_html(self) -> None:
        import hashlib
        import json
        import tempfile
        import zipfile
        from pathlib import Path

        from vibeview.qvf import QVFReader
        from vibeview.renderers.spectra import SpectraRenderer

        spectrum = json.dumps(
            {
                "frequencies": [100.0, 200.0, 300.0],
                "intensities": [1.0, 2.0, 0.5],
            }
        ).encode()

        sections = [
            {
                "id": "ir",
                "kind": "spectra.ir",
                "members": {
                    "spectrum": {
                        "path": "s/sp.json",
                        "format": "json",
                        "sha256": hashlib.sha256(spectrum).hexdigest(),
                    },
                },
            }
        ]
        manifest = {
            "qvf_version": 1,
            "source": {"program": "x", "version": "1", "calculation": "t"},
            "sections": sections,
        }
        tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
        with zipfile.ZipFile(tmp, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("s/sp.json", spectrum)
        try:
            reader = QVFReader(Path(tmp.name))
            renderer = SpectraRenderer(reader.get_section("ir"), reader)
            html = renderer.render_to_html()
            assert "<div" in html
            assert "plotly" in html.lower() or "Plotly" in html
            assert "Spectrum" in html
        finally:
            Path(tmp.name).unlink()


class TestMeshReplication:
    """Test the _replicate_mesh function for periodic isosurfaces."""

    def test_unit_replication_is_identity(self) -> None:
        import pyvista as pv

        from vibeview.renderers.volume import _replicate_mesh

        sphere = pv.Sphere(radius=1.0)
        lattice = np.array([[2.0, 0, 0], [0, 2.0, 0], [0, 0, 2.0]])
        result = _replicate_mesh(sphere, lattice, (1, 1, 1))
        assert result.n_points == sphere.n_points

    def test_2x1x1_replication_doubles_points(self) -> None:
        import pyvista as pv

        from vibeview.renderers.volume import _replicate_mesh

        sphere = pv.Sphere(radius=1.0, theta_resolution=8, phi_resolution=8)
        n_orig = sphere.n_points
        lattice = np.array([[3.0, 0, 0], [0, 2.0, 0], [0, 0, 2.0]])
        result = _replicate_mesh(sphere, lattice, (2, 1, 1))
        # Two replicas = 2x points (approximately, may vary slightly)
        assert result.n_points == pytest.approx(n_orig * 2, rel=0.1)

    def test_empty_mesh_returns_itself(self) -> None:
        import pyvista as pv

        from vibeview.renderers.volume import _replicate_mesh

        empty = pv.PolyData()
        lattice = np.eye(3)
        result = _replicate_mesh(empty, lattice, (2, 2, 2))
        assert result.n_points == 0


class TestVolumeUnitConversion:
    """Regression: the volume renderer must convert grid origin +
    voxel_vectors from bohr (QVF v1 contract, design § 1.3a) to Å
    before handing them to PyVista. Atoms are in Å on disk; if the
    renderer used the raw bohr values, isosurfaces would render
    ~1.89× too large relative to atoms — the visible interoperability
    bug surfaced by the code review."""

    def _build_qvf(
        self,
        tmp_path,
        origin_bohr,
        spacing_bohr,
        n_voxels,
        voxel_vectors=None,
    ):
        """Build a minimal QVF with a single density volume and an
        atom at the origin. Returns the path."""
        import hashlib
        import json
        import zipfile

        # Atom at the bohr-frame origin → at (0,0,0) Å in the
        # structure section (already converted by the writer).
        struct = json.dumps(
            {
                "atoms": [
                    {"symbol": "H", "position": [0.0, 0.0, 0.0], "atomic_number": 1}
                ],
                "pbc": [False, False, False],
            }
        ).encode()
        if voxel_vectors is None:
            voxel_vectors = [
                [spacing_bohr, 0.0, 0.0],
                [0.0, spacing_bohr, 0.0],
                [0.0, 0.0, spacing_bohr],
            ]
        grid = json.dumps(
            {
                "origin": list(origin_bohr),
                "voxel_vectors": voxel_vectors,
                "shape": [n_voxels, n_voxels, n_voxels],
            }
        ).encode()
        # A gaussian-ish blob centred on the bohr-frame origin so
        # marching cubes finds an isosurface.
        idx = np.arange(n_voxels) - n_voxels // 2
        XX, YY, ZZ = np.meshgrid(idx, idx, idx, indexing="ij")
        data = np.exp(-(XX**2 + YY**2 + ZZ**2) / 10.0).astype(np.float32)
        data_bytes = data.tobytes()

        manifest = {
            "qvf_version": 1,
            "source": {"program": "x", "version": "1", "calculation": "t"},
            "sections": [
                {
                    "id": "structure",
                    "kind": "structure",
                    "members": {
                        "structure": {
                            "path": "s/struct.json",
                            "format": "json",
                            "sha256": hashlib.sha256(struct).hexdigest(),
                        }
                    },
                },
                {
                    "id": "dens",
                    "kind": "volume.density",
                    "members": {
                        "grid": {
                            "path": "s/g.json",
                            "format": "json",
                            "sha256": hashlib.sha256(grid).hexdigest(),
                        },
                        "data": {
                            "path": "s/d.dat",
                            "format": "binary",
                            "dtype": "float32",
                            "shape": list(data.shape),
                            "sha256": hashlib.sha256(data_bytes).hexdigest(),
                        },
                    },
                },
            ],
        }
        path = tmp_path / "align.qvf"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("s/struct.json", struct)
            zf.writestr("s/g.json", grid)
            zf.writestr("s/d.dat", data_bytes)
        return path

    def test_isosurface_bbox_lies_around_atom_in_angstrom(self, tmp_path) -> None:
        """Build a QVF where the grid is centred at (0,0,0) bohr with
        spacing 0.4 bohr over 21 voxels (extent ±4 bohr ≈ ±2.12 Å).
        The atom is at (0,0,0) Å. After bohr→Å conversion the rendered
        isosurface's bbox should be ~ ±2 Å, not ~ ±4 (which it would
        be if the renderer used the raw bohr values)."""
        from vibeview.qvf import QVFReader
        from vibeview.renderers.volume import _BOHR_TO_ANGSTROM, VolumeRenderer

        n_voxels = 21
        spacing_bohr = 0.4
        origin_bohr = (-n_voxels // 2 * spacing_bohr,) * 3
        path = self._build_qvf(tmp_path, origin_bohr, spacing_bohr, n_voxels)

        reader = QVFReader(path)
        renderer = VolumeRenderer(reader.get_section("dens"), reader)
        hints = VolumeHints(isovalue=0.5, colormap="viridis", opacity=0.6)
        mesh = renderer.make_mesh(hints)

        assert mesh is not None and mesh.n_points > 0, "no isosurface"
        x_min, x_max, y_min, y_max, z_min, z_max = mesh.bounds

        # Expected extent in Å is the bohr extent × _BOHR_TO_ANGSTROM.
        expected_half_ang = (n_voxels // 2) * spacing_bohr * _BOHR_TO_ANGSTROM
        # If the conversion were skipped the bbox would be ~1.89× larger.
        # Assert all extents are within the Å scale (not bohr).
        assert abs(x_min) <= expected_half_ang + 1e-3
        assert abs(x_max) <= expected_half_ang + 1e-3
        assert abs(y_min) <= expected_half_ang + 1e-3
        assert abs(z_max) <= expected_half_ang + 1e-3

        # Specifically: a bohr-rendered bbox would put x_min near
        # -(n//2 * spacing) = -4.0; the Å-rendered bbox sits near
        # -4 * 0.529 = -2.12. Assert we're well clear of the bohr value.
        assert x_min > -3.0, (
            f"x_min={x_min} suggests the renderer is using raw bohr "
            f"(would land near {-(n_voxels//2)*spacing_bohr})"
        )

    def test_non_orthogonal_grid_contours_without_shape_mismatch(self, tmp_path) -> None:
        """Non-orthogonal QVF grids use the same point-centered data
        contract as orthogonal grids, so StructuredGrid dimensions must
        match the data shape exactly."""
        from vibeview.qvf import QVFReader
        from vibeview.renderers.volume import VolumeRenderer

        n_voxels = 9
        spacing_bohr = 0.4
        origin_bohr = (-n_voxels // 2 * spacing_bohr,) * 3
        voxel_vectors = [
            [spacing_bohr, 0.0, 0.0],
            [0.12, spacing_bohr, 0.0],
            [0.0, 0.08, spacing_bohr],
        ]
        path = self._build_qvf(
            tmp_path,
            origin_bohr,
            spacing_bohr,
            n_voxels,
            voxel_vectors=voxel_vectors,
        )

        reader = QVFReader(path)
        renderer = VolumeRenderer(reader.get_section("dens"), reader)
        hints = VolumeHints(isovalue=0.5, colormap="viridis", opacity=0.6)
        mesh = renderer.make_mesh(hints)

        assert mesh is not None
        assert mesh.n_points > 0

    def test_VolumeHints_import(self) -> None:
        """Smoke: the VolumeHints dataclass is importable from the same
        module the renderer reads from. Catches the next-most-likely
        breakage if someone moves the dataclass."""
        from vibeview.renderers.volume import VolumeRenderer  # noqa: F401

        assert VolumeHints is not None


class TestSpectraPerKindLabels:
    """The SpectraRenderer is dispatched for IR / Raman / UV-Vis / ECD /
    VCD / generic. Pre-fix it had IR labels hardcoded (cm⁻¹ X-axis,
    km/mol Y-axis, "IR Spectrum" title), so e.g. a UV-Vis section
    rendered with frequencies in eV showed up with a "cm⁻¹" axis.
    Per-kind label registry now drives the axes."""

    def _build_spectrum_qvf(self, tmp_path, kind: str, freqs, intens):
        import hashlib
        import json
        import zipfile
        from pathlib import Path

        spec = json.dumps({"frequencies": list(freqs), "intensities": list(intens)}).encode()
        manifest = {
            "qvf_version": 1,
            "source": {"program": "x", "version": "1", "calculation": "t"},
            "sections": [
                {
                    "id": "sp",
                    "kind": kind,
                    "members": {
                        "spectrum": {
                            "path": "s/sp.json",
                            "format": "json",
                            "sha256": hashlib.sha256(spec).hexdigest(),
                        }
                    },
                }
            ],
        }
        path = tmp_path / f"{kind.replace('.', '_')}.qvf"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("s/sp.json", spec)
        return Path(path)

    @pytest.mark.parametrize(
        "kind, expected_x_unit, expected_title_fragment",
        [
            ("spectra.ir", "cm⁻¹", "IR"),
            ("spectra.raman", "cm⁻¹", "Raman"),
            ("spectra.uvvis", "eV", "UV-Vis"),
            ("spectra.ecd", "eV", "ECD"),
            ("spectra.vcd", "cm⁻¹", "VCD"),
        ],
    )
    def test_per_kind_style_resolved(
        self, kind, expected_x_unit, expected_title_fragment
    ) -> None:
        """The _style_for resolver picks the right axis labels and
        title for each kind. Pre-fix everything used IR labels."""
        from vibeview.qvf import Section
        from vibeview.renderers.spectra import _style_for

        section = Section(id="sp", kind=kind)
        style = _style_for(section, np.array([100.0, 200.0]))
        assert expected_x_unit in style.x_label, (
            f"{kind}: x_label={style.x_label!r} missing unit {expected_x_unit!r}"
        )
        assert expected_x_unit == style.x_unit_short, (
            f"{kind}: x_unit_short={style.x_unit_short!r} != {expected_x_unit!r}"
        )
        assert expected_title_fragment in style.title, (
            f"{kind}: title={style.title!r} missing fragment "
            f"{expected_title_fragment!r}"
        )

    def test_uvvis_does_not_mislabel_as_cm1(self) -> None:
        """Regression: pre-fix, spectra.uvvis rendered with a cm⁻¹
        X-axis even though the data is in eV. The resolved style for
        uvvis must use eV, not cm⁻¹."""
        from vibeview.qvf import Section
        from vibeview.renderers.spectra import _style_for

        style = _style_for(
            Section(id="sp", kind="spectra.uvvis"), np.array([3.5, 4.2])
        )
        assert style.x_unit_short == "eV"
        assert "cm" not in style.x_label.lower(), (
            f"uvvis x_label={style.x_label!r} still mentions cm"
        )
        assert style.x_label == "Energy (eV)"

    def test_generic_falls_back_to_section_label(self, tmp_path) -> None:
        """spectra.generic uses Section.label for the title; the X-axis
        falls back to a unit-free placeholder."""
        import hashlib
        import json
        import zipfile

        from vibeview.qvf import QVFReader
        from vibeview.renderers.spectra import SpectraRenderer

        spec = json.dumps(
            {"frequencies": [1.0, 2.0], "intensities": [0.5, 1.0]}
        ).encode()
        manifest = {
            "qvf_version": 1,
            "source": {"program": "x", "version": "1", "calculation": "t"},
            "sections": [
                {
                    "id": "sp",
                    "kind": "spectra.generic",
                    "label": "Photoemission",
                    "members": {
                        "spectrum": {
                            "path": "s/sp.json",
                            "format": "json",
                            "sha256": hashlib.sha256(spec).hexdigest(),
                        }
                    },
                }
            ],
        }
        path = tmp_path / "gen.qvf"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("s/sp.json", spec)

        reader = QVFReader(path)
        renderer = SpectraRenderer(reader.get_section("sp"), reader)
        html = renderer.render_to_html()
        assert "Photoemission" in html


class TestCitationsRenderer:
    """The citations renderer reads embedded BibTeX bytes and emits an
    HTML panel with the text escaped + an entry count."""

    def _build_citations_qvf(self, tmp_path, bibtex: bytes):
        import hashlib
        import json
        import zipfile
        from pathlib import Path

        manifest = {
            "qvf_version": 1,
            "source": {"program": "x", "version": "1", "calculation": "t"},
            "sections": [
                {
                    "id": "cites",
                    "kind": "citations",
                    "members": {
                        "references": {
                            "path": "s/refs.bib",
                            "format": "binary",
                            "sha256": hashlib.sha256(bibtex).hexdigest(),
                        }
                    },
                }
            ],
        }
        path = tmp_path / "cites.qvf"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("s/refs.bib", bibtex)
        return Path(path)

    def test_renders_entry_count_and_escapes(self, tmp_path) -> None:
        from vibeview.qvf import QVFReader
        from vibeview.renderers.citations import CitationsRenderer

        # Two entries + one HTML-active char to confirm escaping.
        bib = (
            "@article{aaa2026,\n"
            "  title = {Some <Important> Paper},\n"
            "  year = 2026,\n"
            "}\n"
            "@book{bbb2025,\n"
            "  title = {A Book},\n"
            "  year = 2025,\n"
            "}\n"
        )
        path = self._build_citations_qvf(tmp_path, bib.encode("utf-8"))
        reader = QVFReader(path)
        renderer = CitationsRenderer(reader.get_section("cites"), reader)
        html = renderer.render_to_html()
        assert "2 bibliography entries" in html
        # Raw HTML metacharacters from the title must be escaped, not
        # passed through.
        assert "&lt;Important&gt;" in html
        assert "<Important>" not in html

    def test_renders_singular_for_one_entry(self, tmp_path) -> None:
        from vibeview.qvf import QVFReader
        from vibeview.renderers.citations import CitationsRenderer

        path = self._build_citations_qvf(
            tmp_path, b"@misc{one,\n  title = {Single},\n}\n"
        )
        reader = QVFReader(path)
        renderer = CitationsRenderer(reader.get_section("cites"), reader)
        html = renderer.render_to_html()
        assert "1 bibliography entry" in html


class TestRunRecordRenderer:
    """The run.record renderer shows program metadata plus the verbatim
    input and log as escaped monospace text, size-gating huge logs."""

    def _build_run_record_qvf(
        self,
        tmp_path,
        *,
        input_text: str | None,
        log_text: str | None,
        files: dict | None = None,
        section_extra: dict | None = None,
    ):
        import hashlib
        import json
        import zipfile
        from pathlib import Path

        members: dict = {}
        blobs: dict[str, bytes] = {}
        if input_text is not None:
            data = input_text.encode("utf-8")
            members["input"] = {
                "path": "run/input.txt",
                "format": "binary",
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            blobs["run/input.txt"] = data
        if log_text is not None:
            data = log_text.encode("utf-8")
            members["log"] = {
                "path": "run/log.txt",
                "format": "binary",
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            blobs["run/log.txt"] = data
        if files is not None:
            data = json.dumps(files).encode("utf-8")
            members["files"] = {
                "path": "run/files.json",
                "format": "json",
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            blobs["run/files.json"] = data
        section = {
            "id": "run_record0",
            "kind": "run.record",
            "program": "orca",
            "members": members,
        }
        section.update(section_extra or {})
        manifest = {
            "qvf_version": 1,
            "source": {"program": "x", "version": "1", "calculation": "t"},
            "sections": [section],
        }
        path = tmp_path / "run.qvf"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            for zpath, data in blobs.items():
                zf.writestr(zpath, data)
        return Path(path)

    def test_renders_metadata_input_and_log_escaped(self, tmp_path) -> None:
        from vibeview.qvf import QVFReader
        from vibeview.renderers.run_record import RunRecordRenderer

        path = self._build_run_record_qvf(
            tmp_path,
            input_text="! RHF <STO-3G>\n* xyz 0 1\n",
            log_text="FINAL SINGLE POINT ENERGY -74.96\n",
            files={
                "input": {"filename": "water.inp"},
                "log": {"filename": "water.out"},
            },
            section_extra={
                "program_version": "6.0.1",
                "exit_status": 0,
                "started_utc": "2026-07-24T12:00:00Z",
            },
        )
        reader = QVFReader(path)
        renderer = RunRecordRenderer(reader.get_section("run_record0"), reader)
        html = renderer.render_to_html()
        assert "orca" in html
        assert "6.0.1" in html
        assert "water.inp" in html
        assert "water.out" in html
        assert "FINAL SINGLE POINT ENERGY" in html
        # HTML metacharacters in the input must be escaped, not passed
        # through into the srcdoc document.
        assert "&lt;STO-3G&gt;" in html
        assert "<STO-3G>" not in html

    def test_log_only_section_renders(self, tmp_path) -> None:
        from vibeview.qvf import QVFReader
        from vibeview.renderers.run_record import RunRecordRenderer

        path = self._build_run_record_qvf(
            tmp_path, input_text=None, log_text="only a log\n"
        )
        reader = QVFReader(path)
        renderer = RunRecordRenderer(reader.get_section("run_record0"), reader)
        html = renderer.render_to_html()
        assert "only a log" in html
        assert "Input" not in html

    def test_huge_log_is_sliced_for_display(self, tmp_path) -> None:
        from vibeview.qvf import QVFReader
        from vibeview.renderers import run_record as rr_mod
        from vibeview.renderers.run_record import RunRecordRenderer

        head_marker = "HEAD-MARKER\n"
        tail_marker = "TAIL-MARKER\n"
        filler = "x" * (rr_mod._LOG_RENDER_CAP + 1024)
        log = head_marker + filler + tail_marker
        path = self._build_run_record_qvf(
            tmp_path, input_text=None, log_text=log
        )
        reader = QVFReader(path)
        renderer = RunRecordRenderer(reader.get_section("run_record0"), reader)
        html = renderer.render_to_html()
        assert "HEAD-MARKER" in html
        assert "TAIL-MARKER" in html
        assert "elided for display" in html
        # The middle filler must not be shipped whole.
        assert len(html) < len(log)

    def test_truncated_flag_is_surfaced(self, tmp_path) -> None:
        from vibeview.qvf import QVFReader
        from vibeview.renderers.run_record import RunRecordRenderer

        path = self._build_run_record_qvf(
            tmp_path,
            input_text=None,
            log_text="partial\n",
            files={"log": {"filename": "job.out", "truncated": True}},
        )
        reader = QVFReader(path)
        renderer = RunRecordRenderer(reader.get_section("run_record0"), reader)
        html = renderer.render_to_html()
        assert "truncated at archive time" in html


class TestSCFHistoryRenderer:
    """Plotly convergence plot for the SCF iteration trail."""

    def _build_scf_qvf(self, tmp_path, iterations: list[dict]):
        import hashlib
        import json
        import zipfile
        from pathlib import Path

        payload = json.dumps({"iterations": iterations}).encode()
        manifest = {
            "qvf_version": 1,
            "source": {"program": "x", "version": "1", "calculation": "t"},
            "sections": [
                {
                    "id": "scf",
                    "kind": "scf_history",
                    "members": {
                        "iterations": {
                            "path": "s/iter.json",
                            "format": "json",
                            "sha256": hashlib.sha256(payload).hexdigest(),
                        }
                    },
                }
            ],
        }
        path = tmp_path / "scf.qvf"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("s/iter.json", payload)
        return Path(path)

    def test_renders_energy_and_diis_axes(self, tmp_path) -> None:
        from vibeview.qvf import QVFReader
        from vibeview.renderers.scf_history import SCFHistoryRenderer

        path = self._build_scf_qvf(
            tmp_path,
            [
                {"iter": 1, "energy_eh": -75.5, "diis_error": 1e-2},
                {"iter": 2, "energy_eh": -75.9, "diis_error": 1e-5},
                {"iter": 3, "energy_eh": -75.95, "diis_error": 1e-8},
            ],
        )
        reader = QVFReader(path)
        r = SCFHistoryRenderer(reader.get_section("scf"), reader)
        html = r.render_to_html()
        assert "SCF Convergence" in html
        # Both axes should appear in the layout.
        assert "Energy (Eh)" in html
        # DIIS axis is log + appears on the right
        assert "DIIS error" in html or "diis" in html.lower()

    def test_renders_energy_only_when_no_diis(self, tmp_path) -> None:
        """Non-DIIS solvers (e.g. damped SCF) don't ship `diis_error`;
        the renderer should fall back to an energy-only plot rather
        than blowing up."""
        from vibeview.qvf import QVFReader
        from vibeview.renderers.scf_history import SCFHistoryRenderer

        path = self._build_scf_qvf(
            tmp_path,
            [
                {"iter": 1, "energy_eh": -75.5},
                {"iter": 2, "energy_eh": -75.9},
            ],
        )
        reader = QVFReader(path)
        r = SCFHistoryRenderer(reader.get_section("scf"), reader)
        html = r.render_to_html()
        assert "Energy (Eh)" in html
        # No DIIS axis label when the field is absent.
        assert "DIIS error" not in html

    def test_empty_iterations_renders_message(self, tmp_path) -> None:
        from vibeview.qvf import QVFReader
        from vibeview.renderers.scf_history import SCFHistoryRenderer

        path = self._build_scf_qvf(tmp_path, [])
        reader = QVFReader(path)
        r = SCFHistoryRenderer(reader.get_section("scf"), reader)
        html = r.render_to_html()
        assert "empty" in html.lower() or "no iteration" in html.lower()


class TestSymmetryRenderer:
    """spglib-style symmetry summary panel."""

    def _build_sym_qvf(self, tmp_path, sym_dict: dict):
        import hashlib
        import json
        import zipfile
        from pathlib import Path

        payload = json.dumps(sym_dict).encode()
        manifest = {
            "qvf_version": 1,
            "source": {"program": "x", "version": "1", "calculation": "t"},
            "sections": [
                {
                    "id": "sym",
                    "kind": "structure.symmetry",
                    "members": {
                        "data": {
                            "path": "s/sym.json",
                            "format": "json",
                            "sha256": hashlib.sha256(payload).hexdigest(),
                        }
                    },
                }
            ],
        }
        path = tmp_path / "sym.qvf"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("s/sym.json", payload)
        return Path(path)

    def test_renders_conventional_spglib_keys(self, tmp_path) -> None:
        from vibeview.qvf import QVFReader
        from vibeview.renderers.symmetry import SymmetryRenderer

        path = self._build_sym_qvf(
            tmp_path,
            {
                "space_group_number": 225,
                "space_group_symbol": "Fm-3m",
                "point_group": "m-3m",
                "number_of_symmetry_operations": 48,
            },
        )
        reader = QVFReader(path)
        r = SymmetryRenderer(reader.get_section("sym"), reader)
        html = r.render_to_html()
        # All four keys + values present.
        for key in (
            "space_group_number",
            "space_group_symbol",
            "point_group",
            "number_of_symmetry_operations",
        ):
            assert key in html, f"missing key {key} in rendered panel"
        assert "225" in html
        assert "Fm-3m" in html

    def test_matrix_value_rendered_as_block(self, tmp_path) -> None:
        """Multi-row matrices like the spglib transformation matrix go
        into a <pre> block so they don't blow out the layout."""
        from vibeview.qvf import QVFReader
        from vibeview.renderers.symmetry import SymmetryRenderer

        path = self._build_sym_qvf(
            tmp_path,
            {
                "space_group_number": 1,
                "transformation_matrix": [
                    [1.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0],
                    [0.0, 0.0, 1.0],
                ],
            },
        )
        reader = QVFReader(path)
        r = SymmetryRenderer(reader.get_section("sym"), reader)
        html = r.render_to_html()
        assert "<pre" in html
        assert "transformation_matrix" in html

    def test_suppresses_producer_bookkeeping(self, tmp_path) -> None:
        """`kind` and `version` keys that the writer adds for its own
        bookkeeping should not appear in the panel."""
        from vibeview.qvf import QVFReader
        from vibeview.renderers.symmetry import SymmetryRenderer

        path = self._build_sym_qvf(
            tmp_path,
            {
                "kind": "structure.symmetry",
                "version": "1.0",
                "space_group_number": 1,
            },
        )
        reader = QVFReader(path)
        r = SymmetryRenderer(reader.get_section("sym"), reader)
        html = r.render_to_html()
        # The row labels live in <td> cells; check the literal key name
        # doesn't appear as a row label.
        assert ">version<" not in html
        # `kind` is harder to assert in isolation; check that the
        # producer-bookkeeping value 'structure.symmetry' doesn't
        # appear as a stand-alone <td>.
        assert ">structure.symmetry<" not in html


class TestNMRRenderer:
    """spectra.nmr panel — metadata + chemical-shift table + optional
    J-coupling table + collapsed shielding-tensor summary."""

    def _build_nmr_qvf(self, tmp_path, nmr_dict: dict):
        import hashlib
        import json
        import zipfile
        from pathlib import Path

        payload = json.dumps(nmr_dict).encode()
        manifest = {
            "qvf_version": 1,
            "source": {"program": "x", "version": "1", "calculation": "t"},
            "sections": [
                {
                    "id": "nmr",
                    "kind": "spectra.nmr",
                    "members": {
                        "spectrum": {
                            "path": "s/nmr.json",
                            "format": "json",
                            "sha256": hashlib.sha256(payload).hexdigest(),
                        }
                    },
                }
            ],
        }
        path = tmp_path / "nmr.qvf"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("s/nmr.json", payload)
        return Path(path)

    def test_chemical_shifts_table(self, tmp_path) -> None:
        from vibeview.qvf import QVFReader
        from vibeview.renderers.nmr import NMRRenderer

        path = self._build_nmr_qvf(
            tmp_path,
            {
                "isotope": "1H",
                "reference": "TMS",
                "solvent": "CDCl3",
                "chemical_shifts": [
                    {"atom_index": 0, "symbol": "H",
                     "isotropic_shift_ppm": 4.65},
                    {"atom_index": 1, "symbol": "C",
                     "isotropic_shift_ppm": 120.3},
                ],
            },
        )
        reader = QVFReader(path)
        r = NMRRenderer(reader.get_section("nmr"), reader)
        html = r.render_to_html()
        # Metadata strip
        assert "1H" in html and "TMS" in html and "CDCl3" in html
        # Chemical-shifts caption + values
        assert "Chemical shifts" in html
        assert "4.6500" in html and "120.3000" in html

    def test_j_couplings_table(self, tmp_path) -> None:
        from vibeview.qvf import QVFReader
        from vibeview.renderers.nmr import NMRRenderer

        path = self._build_nmr_qvf(
            tmp_path,
            {
                "chemical_shifts": [{"atom_index": 0, "isotropic_shift_ppm": 1.0}],
                "j_couplings": [
                    {"i": 0, "j": 1, "j_hz": 7.5},
                    {"i": 0, "j": 2, "j_hz": 2.1},
                ],
            },
        )
        reader = QVFReader(path)
        r = NMRRenderer(reader.get_section("nmr"), reader)
        html = r.render_to_html()
        assert "J-couplings" in html
        assert "7.5000" in html and "2.1000" in html

    def test_shielding_tensors_collapsed(self, tmp_path) -> None:
        """3×3 tensors per atom would dwarf the chemical-shift table
        if rendered inline. Collapse into a <details> summary."""
        from vibeview.qvf import QVFReader
        from vibeview.renderers.nmr import NMRRenderer

        path = self._build_nmr_qvf(
            tmp_path,
            {
                "shielding_tensors": [
                    [[31.5, 0, 0], [0, 31.5, 0], [0, 0, 31.5]],
                    [[31.6, 0, 0], [0, 31.6, 0], [0, 0, 31.6]],
                ],
            },
        )
        reader = QVFReader(path)
        r = NMRRenderer(reader.get_section("nmr"), reader)
        html = r.render_to_html()
        assert "<details" in html
        assert "Shielding tensors" in html
        assert "2 tensors recorded" in html

    def test_empty_section_renders_message(self, tmp_path) -> None:
        from vibeview.qvf import QVFReader
        from vibeview.renderers.nmr import NMRRenderer

        path = self._build_nmr_qvf(tmp_path, {})
        reader = QVFReader(path)
        r = NMRRenderer(reader.get_section("nmr"), reader)
        html = r.render_to_html()
        assert "empty" in html.lower() or "no chemical" in html.lower()


from vibeview.viewer_defaults import VolumeHints  # noqa: E402 — used by tests above
