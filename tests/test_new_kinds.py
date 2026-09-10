"""Tests for the v0.9 QVF additions: wavefunction.gto, reaction.path,
reaction.waypoints, and viewer_defaults.bookmarks.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pytest

from vibeview.qvf import QVFReader
from vibeview.viewer_defaults import Bookmark, ViewerState


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_qvf(
    sections: list[dict],
    files: dict[str, bytes],
    viewer_defaults: dict | None = None,
) -> Path:
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": "test"},
        "sections": sections,
    }
    if viewer_defaults is not None:
        manifest["viewer_defaults"] = viewer_defaults
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for p, d in files.items():
            zf.writestr(p, d)
    return Path(tmp.name)


# ── wavefunction.gto ─────────────────────────────────────────────────────


def _wf_qvf(
    structure_ref: str = "structure",
    *,
    occupation_semantics: str | None = None,
) -> tuple[Path, str]:
    """A minimal .qvf with one H atom + one s-shell + one occupied MO."""
    primary_position = (
        [9.0, 0.0, 0.0]
        if structure_ref != "structure"
        else [0.0, 0.0, 0.0]
    )
    structure = json.dumps(
        {
            "atoms": [
                {"symbol": "H", "position": primary_position, "atomic_number": 1}
            ],
            "pbc": [False, False, False],
        }
    ).encode()
    basis = json.dumps(
        {
            "structure_ref": structure_ref,
            "pure": True,
            "n_ao": 1,
            "shells": [
                {
                    "center": 0,
                    "l": 0,
                    "exponents": [0.5],
                    "coefficients": [1.0],
                }
            ],
        }
    ).encode()
    mo_metadata = {
        "n_mo": 1,
        "n_ao": 1,
        "spin": "restricted",
        "orbital_kind": "canonical",
        "energies": [-0.5],
        "occupations": [2.0],
        "symmetry_labels": ["A1"],
    }
    if occupation_semantics is not None:
        mo_metadata["occupation_semantics"] = occupation_semantics
    mo_meta = json.dumps(mo_metadata).encode()
    coeffs = np.array([[1.0]], dtype=np.float64).tobytes()

    sections = [
        {
            "id": "structure",
            "kind": "structure",
            "members": {
                "structure": {
                    "path": "structure.json",
                    "format": "json",
                    "sha256": _sha256(structure),
                }
            },
        },
    ]
    files = {"structure.json": structure}
    if structure_ref != "structure":
        referenced = json.dumps(
            {
                "atoms": [
                    {
                        "symbol": "H",
                        "position": [0.0, 0.0, 0.0],
                        "atomic_number": 1,
                    }
                ],
                "pbc": [True, False, False],
                "dimensionality": 1,
                "lattice_vectors": [
                    [5.0, 0.0, 0.0],
                    [0.0, 20.0, 0.0],
                    [0.0, 0.0, 30.0],
                ],
            }
        ).encode()
        sections.append(
            {
                "id": structure_ref,
                "kind": "structure",
                "members": {
                    "structure": {
                        "path": "referenced_structure.json",
                        "format": "json",
                        "sha256": _sha256(referenced),
                    }
                },
            }
        )
        files["referenced_structure.json"] = referenced

    sections.append(
        {
            "id": "wf",
            "kind": "wavefunction.gto",
            "members": {
                "basis": {
                    "path": "wf/basis.json",
                    "format": "json",
                    "sha256": _sha256(basis),
                },
                "mo_metadata": {
                    "path": "wf/mo.json",
                    "format": "json",
                    "sha256": _sha256(mo_meta),
                },
                "mo_coefficients": {
                    "path": "wf/coeffs.dat",
                    "format": "binary",
                    "dtype": "float64",
                    "shape": [1, 1],
                    "sha256": _sha256(coeffs),
                },
            },
        }
    )
    files.update(
        {
            "wf/basis.json": basis,
            "wf/mo.json": mo_meta,
            "wf/coeffs.dat": coeffs,
        }
    )
    path = _make_qvf(sections, files)
    return path, "wf"


class TestWavefunctionGTORead:
    def test_read_restricted_basics(self) -> None:
        path, sid = _wf_qvf(occupation_semantics="electron_occupation")
        try:
            reader = QVFReader(path)
            wf = reader.read_wavefunction_gto(sid)
            assert wf.spin == "restricted"
            assert wf.n_ao == 1
            assert wf.pure is True
            assert len(wf.shells) == 1
            shell = wf.shells[0]
            assert shell.l == 0
            assert shell.center == 0
            np.testing.assert_array_almost_equal(shell.exponents, [0.5])
            assert wf.mo_coefficients is not None
            assert wf.mo_coefficients.shape == (1, 1)
            np.testing.assert_array_almost_equal(wf.energies, [-0.5])
            np.testing.assert_array_almost_equal(wf.occupations, [2.0])
            assert wf.occupation_semantics == "electron_occupation"
        finally:
            path.unlink()

    def test_terminal_surface_uses_referenced_structure_and_periodicity(self) -> None:
        """Atoms, AO field, cell, and replicas share ``basis.structure_ref``."""
        import asyncio

        pytest.importorskip("textual")
        from vibeview.tui.app import VibeViewTUI

        path, _sid = _wf_qvf("orbital_geometry")

        async def exercise() -> None:
            app = VibeViewTUI(path)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.press("tab", "tab")
                await pilot.pause()
                assert app.current[:2] == ("wf", "wavefunction.gto")

                scene = app._current_scene()
                np.testing.assert_allclose(scene.positions, [[0.0, 0.0, 0.0]])
                assert scene.meshes
                assert "periodic Gamma field" in app.status_text

                # Only x is periodic in the referenced structure. Requests
                # for y/z replication must not duplicate atoms or surfaces
                # into the two vacuum lattice vectors.
                await pilot.press("x", "y", "z")
                replicated = app._current_scene()
                np.testing.assert_allclose(
                    replicated.positions,
                    [[0.0, 0.0, 0.0], [5.0, 0.0, 0.0]],
                )
                vertices = np.vstack(
                    [mesh.vertices for mesh in replicated.meshes]
                )
                assert vertices[:, 0].max() > 4.0
                assert np.ptp(vertices[:, 1]) < 10.0
                assert np.ptp(vertices[:, 2]) < 10.0

        try:
            reader = QVFReader(path)
            referenced = reader.read_structure("orbital_geometry")
            assert referenced.pbc == (True, False, False)
            np.testing.assert_allclose(
                [atom.position for atom in referenced.atoms], [[0.0, 0.0, 0.0]]
            )
            asyncio.run(exercise())
        finally:
            path.unlink()

    def test_evaluate_mo_produces_nonzero_density(self) -> None:
        """The s-orbital should peak at the atom and decay outward."""
        from vibeview.renderers.wavefunction import WavefunctionRenderer

        path, sid = _wf_qvf()
        try:
            reader = QVFReader(path)
            section = reader.get_section(sid)
            wf_r = WavefunctionRenderer(section, reader)
            grid, values = wf_r.evaluate_mo(0, n_per_dim=20)
            assert values.shape == (20, 20, 20)
            # s-orbital should be everywhere ≥ 0 (single primitive, c>0)
            assert (values >= 0).all()
            # Max should be near the centre (atom at origin)
            center = (10, 10, 10)
            assert values[center] > 0.0
            # The peak should be near the centre, not at a corner
            i_max = np.unravel_index(np.argmax(values), values.shape)
            for axis in range(3):
                assert abs(i_max[axis] - 10) <= 3
        finally:
            path.unlink()

    def test_mo_table_includes_energy_and_occupation(self) -> None:
        from vibeview.renderers.wavefunction import WavefunctionRenderer

        path, sid = _wf_qvf()
        try:
            reader = QVFReader(path)
            section = reader.get_section(sid)
            wf_r = WavefunctionRenderer(section, reader)
            rows = wf_r.mo_table()
            assert len(rows) == 1
            assert rows[0]["energy_eh"] == pytest.approx(-0.5)
            assert rows[0]["occupation"] == pytest.approx(2.0)
            assert rows[0]["label"] == "A1"
        finally:
            path.unlink()


# ── reaction.path / reaction.waypoints ─────────────────────────────────


def _rxn_path_qvf() -> tuple[Path, str]:
    meta = json.dumps(
        {
            "atoms": [{"symbol": "H", "atomic_number": 1}],
            "energies": [0.0, -0.5, -1.0],
            "reaction_coordinate": [0.0, 0.5, 1.0],
            "waypoints": [
                {"frame_index": 0, "label": "R", "kind": "reactant"},
                {"frame_index": 1, "label": "TS", "kind": "transition_state",
                 "energy_eh": -0.4},
                {"frame_index": 2, "label": "P", "kind": "product"},
            ],
        }
    ).encode()
    coords = np.zeros((3, 1, 3), dtype=np.float64).tobytes()
    sections = [
        {
            "id": "rxn",
            "kind": "reaction.path",
            "members": {
                "metadata": {"path": "rxn/meta.json", "format": "json", "sha256": _sha256(meta)},
                "coords": {
                    "path": "rxn/coords.dat",
                    "format": "binary",
                    "dtype": "float64",
                    "shape": [3, 1, 3],
                    "sha256": _sha256(coords),
                },
            },
        }
    ]
    files = {"rxn/meta.json": meta, "rxn/coords.dat": coords}
    return _make_qvf(sections, files), "rxn"


class TestReactionPath:
    def test_read_reaction_path(self) -> None:
        path, sid = _rxn_path_qvf()
        try:
            reader = QVFReader(path)
            data = reader.read_reaction_path(sid)
            assert data.coords.shape == (3, 1, 3)
            assert len(data.waypoints) == 3
            assert data.waypoints[0].kind == "reactant"
            assert data.waypoints[1].kind == "transition_state"
            assert data.waypoints[1].energy_eh == pytest.approx(-0.4)
            assert data.reaction_coordinate == [0.0, 0.5, 1.0]
        finally:
            path.unlink()

    def test_renderer_energy_plot_emits_bytes(self) -> None:
        from vibeview.renderers.reaction import ReactionPathRenderer

        path, sid = _rxn_path_qvf()
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            png = renderer.render_energy_plot(1)
            assert png[:8] == b"\x89PNG\r\n\x1a\n"
        finally:
            path.unlink()


def _rxn_waypoints_qvf() -> tuple[Path, str]:
    """A trajectory section + a reaction.waypoints pointing at it."""
    traj_meta = json.dumps(
        {
            "atoms": [{"symbol": "H", "atomic_number": 1}],
            "energies": [0.0, -0.5, -1.0],
        }
    ).encode()
    coords = np.zeros((3, 1, 3), dtype=np.float64).tobytes()
    wp_payload = json.dumps(
        {
            "waypoints": [
                {"frame_index": 0, "label": "R", "kind": "reactant"},
                {"frame_index": 2, "label": "P", "kind": "product"},
            ]
        }
    ).encode()
    sections = [
        {
            "id": "traj",
            "kind": "trajectory",
            "members": {
                "metadata": {"path": "traj/meta.json", "format": "json", "sha256": _sha256(traj_meta)},
                "coords": {
                    "path": "traj/coords.dat",
                    "format": "binary",
                    "dtype": "float64",
                    "shape": [3, 1, 3],
                    "sha256": _sha256(coords),
                },
            },
        },
        {
            "id": "rxn_wp",
            "kind": "reaction.waypoints",
            "trajectory_ref": "traj",
            "members": {
                "waypoints": {"path": "rxn/wp.json", "format": "json", "sha256": _sha256(wp_payload)},
            },
        },
    ]
    files = {
        "traj/meta.json": traj_meta,
        "traj/coords.dat": coords,
        "rxn/wp.json": wp_payload,
    }
    return _make_qvf(sections, files), "rxn_wp"


class TestReactionWaypoints:
    def test_read_reaction_waypoints(self) -> None:
        path, sid = _rxn_waypoints_qvf()
        try:
            reader = QVFReader(path)
            data = reader.read_reaction_waypoints(sid)
            assert data.trajectory_ref == "traj"
            assert len(data.waypoints) == 2
            assert data.waypoints[0].label == "R"
        finally:
            path.unlink()


# ── viewer_defaults.bookmarks ───────────────────────────────────────────


class TestBookmarks:
    def test_parses_bookmark_list(self) -> None:
        from vibeview.qvf import Manifest

        manifest_dict = {
            "qvf_version": 1,
            "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": "t"},
            "sections": [],
            "viewer_defaults": {
                "auto_open": [],
                "bookmarks": [
                    {
                        "name": "front",
                        "camera": {
                            "position": [0, 0, 12],
                            "focal_point": [0, 0, 0],
                            "view_up": [0, 1, 0],
                            "view_angle": 30.0,
                        },
                    },
                    {
                        "name": "side",
                        "camera": {
                            "position": [12, 0, 0],
                            "focal_point": [0, 0, 0],
                            "view_up": [0, 1, 0],
                            "parallel_scale": 6.0,
                        },
                    },
                ],
            },
        }
        manifest = Manifest.model_validate(manifest_dict)
        state = ViewerState.from_manifest(manifest.viewer_defaults)
        assert len(state.bookmarks) == 2
        assert isinstance(state.bookmarks[0], Bookmark)
        assert state.bookmarks[0].name == "front"
        # First bookmark becomes the initial camera if none was supplied.
        assert state.camera == state.bookmarks[0].camera

    def test_no_bookmarks_leaves_camera_none(self) -> None:
        from vibeview.qvf import Manifest

        manifest_dict = {
            "qvf_version": 1,
            "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": "t"},
            "sections": [],
            "viewer_defaults": {"auto_open": []},
        }
        manifest = Manifest.model_validate(manifest_dict)
        state = ViewerState.from_manifest(manifest.viewer_defaults)
        assert state.bookmarks == []
        assert state.camera is None


# ── kinds.SUPPORTED_KINDS coverage ──────────────────────────────────────


class TestSupportedKindsRegistry:
    def test_new_kinds_are_registered(self) -> None:
        from vibeview.kinds import SUPPORTED_KINDS

        for kind in ("wavefunction.gto", "reaction.path", "reaction.waypoints"):
            assert kind in SUPPORTED_KINDS, f"{kind} missing from SUPPORTED_KINDS"


# ── dos.total / dos.projected ───────────────────────────────────────────
#
# Regression guard: the DOS renderer used to call a nonexistent
# ``reader.read_binary(...)`` and subscript Pydantic member objects as
# dicts, so every DOS section (and any bands view with a companion DOS)
# raised at click time. These tests pin the real reader API + member
# layout (energies/dos for total, energies/projections for projected,
# with channel labels in section-level metadata).


def _dos_total_qvf(n_pts: int = 32) -> Path:
    energies = np.linspace(-5.0, 5.0, n_pts).astype(np.float64)
    dos = np.exp(-(energies**2)).astype(np.float64)
    sections = [
        {
            "id": "dos_total",
            "kind": "dos.total",
            "n_spin": 1,
            "fermi_energy_ev": -2.3,
            "members": {
                "energies": {
                    "path": "dos/energies.bin",
                    "format": "binary",
                    "dtype": "float64",
                    "shape": [n_pts],
                    "sha256": _sha256(energies.tobytes()),
                },
                "dos": {
                    "path": "dos/total.bin",
                    "format": "binary",
                    "dtype": "float64",
                    "shape": [n_pts],
                    "sha256": _sha256(dos.tobytes()),
                },
            },
        }
    ]
    files = {"dos/energies.bin": energies.tobytes(), "dos/total.bin": dos.tobytes()}
    return _make_qvf(sections, files)


def _dos_projected_qvf(n_pts: int = 32, n_ch: int = 3) -> Path:
    energies = np.linspace(-5.0, 5.0, n_pts).astype(np.float64)
    proj = np.abs(np.sin(np.outer(np.arange(1, n_ch + 1), energies))).astype(np.float64)
    channels = [
        {"atom_index": 0, "symbol": "C", "l": i, "label": f"C-l{i}"} for i in range(n_ch)
    ]
    sections = [
        {
            "id": "dos_pdos",
            "kind": "dos.projected",
            "n_spin": 1,
            "channels": channels,
            "members": {
                "energies": {
                    "path": "dos/energies.bin",
                    "format": "binary",
                    "dtype": "float64",
                    "shape": [n_pts],
                    "sha256": _sha256(energies.tobytes()),
                },
                "projections": {
                    "path": "dos/projections.bin",
                    "format": "binary",
                    "dtype": "float64",
                    "shape": [n_ch, n_pts],
                    "sha256": _sha256(proj.tobytes()),
                },
            },
        }
    ]
    files = {
        "dos/energies.bin": energies.tobytes(),
        "dos/projections.bin": proj.tobytes(),
    }
    return _make_qvf(sections, files)


def _structure_charges_qvf() -> Path:
    """Minimal QVF: a 3-atom structure + atom_properties (numpy charges)."""
    structure = json.dumps(
        {
            "atoms": [
                {"symbol": "O", "position": [0.0, 0.0, 0.0], "atomic_number": 8},
                {"symbol": "H", "position": [0.0, 0.96, 0.0], "atomic_number": 1},
                {"symbol": "H", "position": [0.93, -0.24, 0.0], "atomic_number": 1},
            ],
            "pbc": [False, False, False],
        }
    ).encode()
    mul = np.array([-0.70, 0.35, 0.35], dtype=np.float64)
    low = np.array([-0.62, 0.31, 0.31], dtype=np.float64)
    hir = np.array([-0.48, 0.24, 0.24], dtype=np.float64)
    sections = [
        {
            "id": "structure",
            "kind": "structure",
            "members": {
                "structure": {"path": "structure/structure.json", "format": "json", "sha256": _sha256(structure)}
            },
        },
        {
            "id": "props0",
            "kind": "atom_properties",
            "members": {
                "mulliken_charge": {"path": "ap/mul.bin", "format": "binary", "dtype": "float64", "shape": [3], "sha256": _sha256(mul.tobytes())},
                "loewdin_charge": {"path": "ap/low.bin", "format": "binary", "dtype": "float64", "shape": [3], "sha256": _sha256(low.tobytes())},
                "hirshfeld_charge": {"path": "ap/hir.bin", "format": "binary", "dtype": "float64", "shape": [3], "sha256": _sha256(hir.tobytes())},
            },
        },
    ]
    files = {
        "structure/structure.json": structure,
        "ap/mul.bin": mul.tobytes(),
        "ap/low.bin": low.tobytes(),
        "ap/hir.bin": hir.tobytes(),
    }
    return _make_qvf(sections, files)


class TestAtomPropertiesOverlay:
    """Regression: the charge overlay used to do ``a or b`` on NumPy arrays
    (ValueError: truth value of an array is ambiguous), crashing before it
    could recolor atoms or draw charge labels."""

    def test_hirshfeld_charge_round_trips(self) -> None:
        from vibeview.renderers.atom_properties import AtomPropertiesRenderer
        from vibeview.tables import extract_table

        reader = QVFReader(_structure_charges_qvf())
        data = reader.read_atom_properties("props0")
        np.testing.assert_allclose(data.hirshfeld_charges, [-0.48, 0.24, 0.24])
        renderer = AtomPropertiesRenderer(reader.get_section("props0"), reader)
        html = renderer.render_to_html(charge_kind="hirshfeld")
        assert "Hirshfeld" in html
        assert "Mulliken" not in html
        columns, rows = extract_table(reader, "atom_properties")
        assert columns[-1] == "hirshfeld"
        assert [row[-1] for row in rows] == [-0.48, 0.24, 0.24]

    def test_color_by_charge_recolors_and_labels(self) -> None:
        import pyvista as pv

        import vibeview.app as app_mod
        from vibeview.qvf import QVFReader
        from vibeview.renderers.structure import StructureRenderer

        reader = QVFReader(_structure_charges_qvf())
        plotter = pv.Plotter(off_screen=True)
        struct = next(s for s in reader.sections if s.kind == "structure")
        StructureRenderer(struct, reader).add_to_plotter(plotter, show_labels=False)

        class _State:
            charge_kind = "hirshfeld"
            color_by_charge = True
            atom_properties_section_id = "props0"
            material_preset = "matte"
            toon_mode = True
            dark_background = False
            representation_style = "ball_and_stick"
            cartoon_color_mode = "chain"
            residue_selection = ""
            replication_nx = 1
            replication_ny = 1
            replication_nz = 1

        # Must not raise (the bug raised ValueError on numpy `a or b`).
        pushes = []
        plotter._vibe_view_update = lambda: pushes.append("push")
        app_mod._render_atom_properties_overlay(reader, plotter, _State())

        # Negative O atom (atom_0_0) recoloured toward blue (B > R).
        rgb = plotter.actors["atom_0_0"].prop.color.float_rgb
        assert rgb[2] > rgb[0], f"expected blue-ish for negative charge, got {rgb}"
        # Charge labels drawn as real geometry (vtk.js-renderable), not 2D actors.
        assert "atom_charge_labels" in plotter.actors
        prop = plotter.actors["atom_0_0"].GetProperty()
        assert prop.GetSpecular() == pytest.approx(0.1)
        assert prop.GetInterpolation() == 0
        assert prop.GetEdgeVisibility() == 1
        assert pushes == ["push"]

    @pytest.mark.parametrize(
        "representation",
        ["ball_and_stick", "space_filling", "sticks_only", "wireframe"],
    )
    def test_scene_rebuild_preserves_charge_overlay(
        self, representation: str
    ) -> None:
        """A structure rebuild must not replace active charge data with CPK."""
        import types

        import pyvista as pv

        import vibeview.app as app_mod
        from vibeview.qvf import QVFReader
        from vibeview.renderers.structure import StructureRenderer

        path = _structure_charges_qvf()
        reader = QVFReader(path)
        plotter = pv.Plotter(off_screen=True)
        try:
            struct = next(s for s in reader.sections if s.kind == "structure")
            StructureRenderer(struct, reader).add_to_plotter(
                plotter, show_labels=True, representation=representation
            )
            baseline_extent = None
            if representation in ("ball_and_stick", "space_filling"):
                baseline_extent = (
                    plotter.actors["atom_0_0"].mapper.dataset.bounds[1]
                    - plotter.actors["atom_0_0"].mapper.dataset.bounds[0]
                )
            state = types.SimpleNamespace(
                active_volume_id=None,
                raytrace_enabled=False,
                ssao_enabled=False,
                toon_mode=False,
                material_preset="cpk_glossy",
                dark_background=True,
                status_message="",
                show_atom_labels=True,
                representation_style=representation,
                cartoon_color_mode="chain",
                residue_selection="",
                wf_surface_kind=None,
                wf_section_id=None,
                mo_visible=False,
                atom_properties_active=True,
                atom_properties_section_id="props0",
                charge_kind="hirshfeld",
                color_by_charge=True,
                replication_nx=1,
                replication_ny=1,
                replication_nz=1,
            )
            viewer_state = types.SimpleNamespace(replication=(1, 1, 1))
            pushes = []
            plotter._vibe_view_update = lambda: pushes.append("push")

            app_mod._rebuild_scene(reader, plotter, viewer_state, state)

            assert state.atom_properties_active is True
            assert state.atom_properties_section_id == "props0"
            assert "atom_charge_labels" in plotter.actors
            assert "atom_index_labels" not in plotter.actors
            physical_atoms = [
                str(name)
                for name in plotter.actors
                if str(name).startswith("atom_group_")
                or (
                    str(name).startswith("atom_")
                    and str(name) not in ("atom_charge_labels", "atom_index_labels")
                )
            ]
            if representation in ("ball_and_stick", "space_filling"):
                assert physical_atoms
                rgb = plotter.actors["atom_0_0"].prop.color.float_rgb
                assert rgb[2] > rgb[0], (
                    f"expected blue-ish for negative charge, got {rgb}"
                )
                rebuilt_extent = (
                    plotter.actors["atom_0_0"].mapper.dataset.bounds[1]
                    - plotter.actors["atom_0_0"].mapper.dataset.bounds[0]
                )
                assert rebuilt_extent == pytest.approx(baseline_extent)
            else:
                assert physical_atoms == []
                assert any(str(name).startswith("bond") for name in plotter.actors)
            assert pushes == ["push"]
        finally:
            plotter.close()
            reader.close()
            path.unlink(missing_ok=True)

    def test_scene_rebuild_reports_stale_charge_recipe(self) -> None:
        """Missing property data must fail visibly and clear active state."""
        import types

        import pyvista as pv

        import vibeview.app as app_mod
        from vibeview.qvf import QVFReader

        path = _structure_charges_qvf()
        reader = QVFReader(path)
        plotter = pv.Plotter(off_screen=True)
        try:
            state = types.SimpleNamespace(
                active_volume_id=None,
                raytrace_enabled=False,
                ssao_enabled=False,
                toon_mode=False,
                material_preset="cpk_glossy",
                dark_background=True,
                status_message="",
                show_atom_labels=False,
                representation_style="ball_and_stick",
                cartoon_color_mode="chain",
                residue_selection="",
                wf_surface_kind=None,
                wf_section_id=None,
                mo_visible=False,
                atom_properties_active=True,
                atom_properties_section_id="missing",
                charge_kind="hirshfeld",
                color_by_charge=True,
                replication_nx=1,
                replication_ny=1,
                replication_nz=1,
            )

            error = app_mod._rebuild_scene(
                reader,
                plotter,
                types.SimpleNamespace(replication=(1, 1, 1)),
                state,
            )

            assert error == (
                "Atomic-property overlay rebuild error: "
                "atom-properties section 'missing' not found"
            )
            assert state.status_message == error
            assert state.atom_properties_active is False
            assert state.atom_properties_section_id is None
            assert "atom_charge_labels" not in plotter.actors
        finally:
            plotter.close()
            reader.close()
            path.unlink(missing_ok=True)

    def test_direct_overlay_failure_restores_toon_and_discards_partial_scene(
        self, monkeypatch
    ) -> None:
        """A failed replacement must not leave toon or charge actors half-applied."""
        import types

        import pyvista as pv

        import vibeview.app as app_mod
        from vibeview.qvf import QVFReader
        from vibeview.renderers.structure import StructureRenderer

        path = _structure_charges_qvf()
        reader = QVFReader(path)
        plotter = pv.Plotter(off_screen=True)
        try:
            structure = next(s for s in reader.sections if s.kind == "structure")
            StructureRenderer(structure, reader).add_to_plotter(plotter)
            plotter.add_mesh(pv.Sphere(center=(3.0, 0.0, 0.0)), name="survivor")
            plotter.add_mesh(pv.Sphere(), name="atom_charge_labels")
            state = types.SimpleNamespace(
                charge_kind="hirshfeld",
                color_by_charge=True,
                atom_properties_section_id="props0",
                material_preset="matte",
                toon_mode=True,
                dark_background=False,
                representation_style="ball_and_stick",
                cartoon_color_mode="chain",
                residue_selection="",
                replication_nx=1,
                replication_ny=1,
                replication_nz=1,
            )
            app_mod._apply_scene_appearance(plotter, state)

            def fail_after_partial_replacement(
                _renderer, current_plotter, *_args, **_kwargs
            ) -> None:
                current_plotter.add_mesh(
                    pv.Sphere(center=(4.0, 0.0, 0.0)),
                    name="atom_partial_overlay",
                )
                raise RuntimeError("structure replacement failed")

            monkeypatch.setattr(
                StructureRenderer,
                "add_to_plotter",
                fail_after_partial_replacement,
            )

            with pytest.raises(RuntimeError, match="structure replacement failed"):
                app_mod._render_atom_properties_overlay(reader, plotter, state)

            survivor = plotter.actors["survivor"].GetProperty()
            assert state.toon_mode is True
            assert survivor.GetSpecular() == pytest.approx(0.1)
            assert survivor.GetSpecularPower() == pytest.approx(10.0)
            assert survivor.GetInterpolation() == 0
            assert survivor.GetEdgeVisibility() == 1
            assert "atom_charge_labels" not in plotter.actors
            assert "atom_partial_overlay" not in plotter.actors
        finally:
            plotter.close()
            reader.close()
            path.unlink(missing_ok=True)

    def test_activation_clears_recipe_after_generic_overlay_failure(
        self, monkeypatch
    ) -> None:
        """A renderer exception must not leave an active, actorless recipe."""
        import pyvista as pv

        import vibeview.app as app_mod
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader
        from vibeview.renderers.structure import StructureRenderer

        path = _structure_charges_qvf()
        reader = QVFReader(path)
        created = []
        plotter_type = app_mod.pv.Plotter

        def create_plotter(*args, **kwargs):
            plotter = plotter_type(*args, **kwargs)
            created.append(plotter)
            return plotter

        monkeypatch.setattr(app_mod.pv, "Plotter", create_plotter)
        try:
            app = create_app(reader)
            state = app.state

            def fail_after_partial_replacement(
                _renderer, current_plotter, *_args, **_kwargs
            ) -> None:
                current_plotter.add_mesh(
                    pv.Sphere(), name="atom_partial_overlay"
                )
                raise RuntimeError("structure replacement failed")

            monkeypatch.setattr(
                StructureRenderer,
                "add_to_plotter",
                fail_after_partial_replacement,
            )
            app.controller.activate_section("props0")

            assert state.atom_properties_active is False
            assert state.atom_properties_section_id is None
            assert state.status_message == (
                "Charge overlay error: structure replacement failed"
            )
            assert not any(
                str(name).startswith(("atom_", "bond_", "bonds_"))
                for name in created[0].actors
            )
        finally:
            if created:
                created[0].close()
            reader.close()
            path.unlink(missing_ok=True)

    @pytest.mark.parametrize(
        ("controller_name", "argument"),
        [("set_charge_kind", "loewdin"), ("toggle_color_by_charge", False)],
    )
    def test_selector_clears_recipe_after_generic_overlay_failure(
        self, monkeypatch, controller_name, argument
    ) -> None:
        """Direct charge controls own failures just like initial activation."""
        import vibeview.app as app_mod
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader
        from vibeview.renderers.structure import StructureRenderer

        path = _structure_charges_qvf()
        reader = QVFReader(path)
        created = []
        plotter_type = app_mod.pv.Plotter

        def create_plotter(*args, **kwargs):
            plotter = plotter_type(*args, **kwargs)
            created.append(plotter)
            return plotter

        monkeypatch.setattr(app_mod.pv, "Plotter", create_plotter)
        try:
            app = create_app(reader)
            state = app.state
            app.controller.activate_section("props0")
            assert state.atom_properties_active is True

            def fail_selector_replacement(*_args, **_kwargs) -> None:
                raise RuntimeError("selector replacement failed")

            monkeypatch.setattr(
                StructureRenderer,
                "add_to_plotter",
                fail_selector_replacement,
            )
            getattr(app.controller, controller_name)(argument)

            assert state.atom_properties_active is False
            assert state.atom_properties_section_id is None
            assert state.status_message == (
                "Charge overlay error: selector replacement failed"
            )
        finally:
            if created:
                created[0].close()
            reader.close()
            path.unlink(missing_ok=True)

    def test_selector_preflight_failure_removes_stale_overlay(
        self, monkeypatch
    ) -> None:
        """Label preparation failure rolls the old tint back to neutral CPK."""
        import vibeview.app as app_mod
        import vibeview.renderers.structure as structure_mod
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader

        path = _structure_charges_qvf()
        reader = QVFReader(path)
        created = []
        plotter_type = app_mod.pv.Plotter

        def create_plotter(*args, **kwargs):
            plotter = plotter_type(*args, **kwargs)
            created.append(plotter)
            return plotter

        monkeypatch.setattr(app_mod.pv, "Plotter", create_plotter)
        try:
            app = create_app(reader)
            state = app.state
            plotter = created[0]
            state.charge_kind = "hirshfeld"
            state.color_by_charge = True
            app.controller.activate_section("props0")
            assert state.atom_properties_active is True
            assert "atom_charge_labels" in plotter.actors
            charge_rgb = plotter.actors["atom_0_0"].prop.color.float_rgb
            assert charge_rgb[2] > charge_rgb[0]

            def fail_label_preflight(*_args, **_kwargs):
                raise RuntimeError("label preflight failed")

            monkeypatch.setattr(
                structure_mod, "build_label_mesh", fail_label_preflight
            )
            app.controller.toggle_color_by_charge(False)

            assert state.atom_properties_active is False
            assert state.atom_properties_section_id is None
            assert state.status_message == (
                "Charge overlay error: label preflight failed"
            )
            assert "atom_charge_labels" not in plotter.actors
            assert "atom_0_0" in plotter.actors
            cpk_rgb = plotter.actors["atom_0_0"].prop.color.float_rgb
            assert cpk_rgb[0] > cpk_rgb[2], (
                "neutral fallback kept the old negative-charge blue tint"
            )
        finally:
            if created:
                created[0].close()
            reader.close()
            path.unlink(missing_ok=True)


class TestMeasurement:
    """Interactive-pick measurement readout (distance/angle/dihedral)."""

    def test_distance_angle_dihedral(self) -> None:
        from vibeview.app import _measure_text
        from vibeview.qvf import QVFReader

        # Reuse the water structure fixture (O, H, H).
        reader = QVFReader(_structure_charges_qvf())
        s = reader.read_structure()
        # Distance O(0)-H(1).
        txt = _measure_text(s, [0, 1])
        assert "Distance O1–H2" in txt and "Å" in txt
        # Angle H(1)-O(0)-H(2).
        txt = _measure_text(s, [1, 0, 2])
        assert "Angle H2–O1–H3" in txt and "°" in txt
        # Empty selection → prompt.
        assert "click 2 atoms" in _measure_text(s, [])


class TestRunInfo:
    """The run summary (energy, convergence, method) lives in the manifest
    provenance block; _run_info_text surfaces it for the Run Info panel."""

    def _qvf_with_provenance(self, provenance: dict | None) -> Path:
        structure = json.dumps(
            {"atoms": [{"symbol": "H", "position": [0, 0, 0], "atomic_number": 1}], "pbc": [False, False, False]}
        ).encode()
        manifest = {
            "qvf_version": 1,
            "source": {"program": "vibe-qc", "version": "0.0", "calculation": "rhf/sto-3g"},
            "sections": [
                {"id": "structure", "kind": "structure", "members": {"structure": {"path": "s.json", "format": "json", "sha256": _sha256(structure)}}}
            ],
        }
        if provenance is not None:
            manifest["provenance"] = provenance
        tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
        with zipfile.ZipFile(tmp, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("s.json", structure)
        return Path(tmp.name)

    def test_formats_energy_and_convergence(self) -> None:
        from vibeview.app import _run_info_text
        from vibeview.qvf import QVFReader

        reader = QVFReader(self._qvf_with_provenance({
            "method": "rhf", "basis": "6-31g*", "charge": 0, "multiplicity": 1,
            "n_electrons": 16, "scf_converged": True,
            "scf_energy": {"value": -113.8631923, "units": "Eh"}, "wall_seconds": 0.38,
        }))
        text = _run_info_text(reader)
        assert "-113.86319" in text
        assert "converged" in text and "NOT CONVERGED" not in text
        assert "RHF" in text and "6-31g*" in text

    def test_flags_unconverged(self) -> None:
        from vibeview.app import _run_info_text
        from vibeview.qvf import QVFReader

        reader = QVFReader(self._qvf_with_provenance({"method": "rks", "scf_converged": False}))
        assert "NOT CONVERGED" in _run_info_text(reader)

    def test_no_provenance_is_empty(self) -> None:
        from vibeview.app import _run_info_text
        from vibeview.qvf import QVFReader

        assert _run_info_text(QVFReader(self._qvf_with_provenance(None))) == ""


class TestDOSRenderer:
    def test_total_dos_loads_and_renders(self) -> None:
        from vibeview.renderers.dos import DOSRenderer

        reader = QVFReader(_dos_total_qvf())
        section = reader.get_section("dos_total")
        renderer = DOSRenderer(section, reader)
        energies, dos = renderer.load()
        assert energies.shape == (32,)
        assert dos.shape == (32,)
        html = renderer.render_to_html()
        assert "Plotly.newPlot" in html

    def test_projected_dos_uses_channel_labels(self) -> None:
        from vibeview.renderers.dos import DOSRenderer

        reader = QVFReader(_dos_projected_qvf())
        section = reader.get_section("dos_pdos")
        renderer = DOSRenderer(section, reader)
        energies, dos = renderer.load()
        assert dos.shape == (3, 32)
        # Channel labels come from section-level metadata (model_extra),
        # NOT getattr(section, "channels") which silently returns [].
        assert [c["label"] for c in renderer._channels] == ["C-l0", "C-l1", "C-l2"]
        html = renderer.render_to_html()
        assert "C-l1" in html

    def test_bands_dos_combiner_keeps_both_draw_scripts(self) -> None:
        from vibeview.renderers.dos import DOSRenderer, make_bands_dos_html

        reader = QVFReader(_dos_total_qvf())
        renderer = DOSRenderer(reader.get_section("dos_total"), reader)
        frag = renderer.render_to_html(include_plotlyjs=False)
        combined = make_bands_dos_html(frag, frag)
        # Both fragments' draw scripts survive; Plotly loaded exactly once.
        assert combined.count("Plotly.newPlot") == 2
        assert combined.count("cdn.plot.ly") == 1
