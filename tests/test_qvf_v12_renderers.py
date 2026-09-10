"""Tests for QVF v1.2 renderers — bond_orders, QTAIM, fat bands."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pytest


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_qvf(sections: list[dict], files: dict[str, bytes]) -> Path:
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "1.2", "calculation": "test"},
        "sections": sections,
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for path, data in files.items():
            zf.writestr(path, data)
    return Path(tmp.name)


class TestBondOrdersRenderer:
    def test_renders_table(self) -> None:
        from vibeview.qvf import QVFReader
        from vibeview.renderers.bond_orders import BondOrdersRenderer

        bo_json = json.dumps(
            {
                "method": "mayer",
                "pairs": [
                    {
                        "i": 0,
                        "j": 1,
                        "order": 0.98,
                        "distance_ang": 1.42,
                        "symbol_i": "O",
                        "symbol_j": "H",
                    },
                    {
                        "i": 0,
                        "j": 2,
                        "order": 0.03,
                        "distance_ang": 2.85,
                        "symbol_i": "O",
                        "symbol_j": "H",
                    },
                    {"i": 1, "j": 2, "order": 0.01, "distance_ang": 1.51},
                ],
            }
        ).encode()
        path = _make_qvf(
            [
                {
                    "id": "bo",
                    "kind": "bond_orders",
                    "members": {
                        "bond_orders": {
                            "path": "bo.json",
                            "format": "json",
                            "sha256": _sha(bo_json),
                        }
                    },
                }
            ],
            {"bo.json": bo_json},
        )
        try:
            reader = QVFReader(path)
            section = reader.get_section("bo")
            renderer = BondOrdersRenderer(section, reader)
            html = renderer.render_to_html()
            assert "Mayer" in html
            assert "O1" in html
            assert "0.980" in html
            assert "1.42" in html
            # Pairs below 0.05 threshold are filtered out.
            assert "0.030" not in html
            assert "0.010" not in html
        finally:
            path.unlink()

    def test_empty_renders_message(self) -> None:
        from vibeview.qvf import QVFReader
        from vibeview.renderers.bond_orders import BondOrdersRenderer

        bo_json = json.dumps({"method": "wiberg", "pairs": []}).encode()
        path = _make_qvf(
            [
                {
                    "id": "bo",
                    "kind": "bond_orders",
                    "members": {
                        "bond_orders": {
                            "path": "bo.json",
                            "format": "json",
                            "sha256": _sha(bo_json),
                        }
                    },
                }
            ],
            {"bo.json": bo_json},
        )
        try:
            reader = QVFReader(path)
            section = reader.get_section("bo")
            renderer = BondOrdersRenderer(section, reader)
            html = renderer.render_to_html()
            assert "No bond-order data" in html
        finally:
            path.unlink()


class TestQTAIMRenderer:
    def test_renders_critical_points(self) -> None:
        import pyvista as pv

        from vibeview.qvf import QVFReader
        from vibeview.renderers.qtaim import QTAIMRenderer

        qtaim_json = json.dumps(
            {
                "points": [
                    {"type": "bcp", "position": [0.7, 0.0, 0.0], "rho": 0.26, "laplacian": -0.54},
                    {"type": "rcp", "position": [0.0, 0.0, 0.5], "rho": 0.08, "laplacian": 0.12},
                ],
                "bond_paths": [
                    {"atoms": [0, 1], "path": [[0.0, 0.0, 0.0], [0.35, 0.0, 0.0], [0.7, 0.0, 0.0]]},
                ],
            }
        ).encode()
        path = _make_qvf(
            [
                {
                    "id": "qtaim",
                    "kind": "topology.qtaim",
                    "members": {
                        "critical_points": {
                            "path": "cp.json",
                            "format": "json",
                            "sha256": _sha(qtaim_json),
                        }
                    },
                }
            ],
            {"cp.json": qtaim_json},
        )
        try:
            reader = QVFReader(path)
            section = reader.get_section("qtaim")
            renderer = QTAIMRenderer(section, reader)
            plotter = pv.Plotter(off_screen=True)
            msg = renderer.add_to_plotter(plotter)
            assert "2 critical points" in msg
            assert "1 bond paths" in msg
            # Check CP spheres were added.
            cp_actors = [k for k in plotter.actors if str(k).startswith("qtaim_cp_")]
            assert len(cp_actors) == 2
            # Check bond path was added.
            path_actors = [k for k in plotter.actors if str(k).startswith("qtaim_path_")]
            assert len(path_actors) == 1
        finally:
            path.unlink()

    def _scalars_qvf(self):
        """Two BCPs whose Laplacian signs put them on opposite sides of
        Bader's shared/closed-shell divide, plus one non-BCP."""
        qtaim_json = json.dumps(
            {
                "points": [
                    {
                        "type": "bcp",
                        "position": [0.7, 0.0, 0.0],
                        "rho": 2.55,
                        "laplacian": -62.98,
                        "ellipticity": 0.039,
                        "atom_pair": [0, 1],
                    },
                    {
                        "type": "bcp",
                        "position": [0.0, 0.0, 1.6],
                        "rho": 0.035,
                        "laplacian": 0.99,
                        "ellipticity": 4.15,
                        "atom_pair": [1, 2],
                    },
                    {"type": "rcp", "position": [0.0, 0.0, 0.5],
                     "rho": 0.08, "laplacian": 0.12},
                ],
                "bond_paths": [],
            }
        ).encode()
        return _make_qvf(
            [
                {
                    "id": "qtaim",
                    "kind": "topology.qtaim",
                    "members": {
                        "critical_points": {
                            "path": "cp.json",
                            "format": "json",
                            "sha256": _sha(qtaim_json),
                        }
                    },
                }
            ],
            {"cp.json": qtaim_json},
        )

    def test_bcp_colour_follows_the_laplacian_sign(self) -> None:
        """The scalars were previously discarded, so every BCP rendered the
        same orange regardless of what kind of interaction it marked."""
        import pyvista as pv

        from vibeview.qvf import QVFReader
        from vibeview.renderers.qtaim import (
            _CLOSED_SHELL_COLOUR,
            _SHARED_SHELL_COLOUR,
            QTAIMRenderer,
        )

        path = self._scalars_qvf()
        try:
            reader = QVFReader(path)
            renderer = QTAIMRenderer(reader.get_section("qtaim"), reader)
            plotter = pv.Plotter(off_screen=True)
            msg = renderer.add_to_plotter(plotter)
            assert "1 shared-shell, 1 closed-shell" in msg
            assert _SHARED_SHELL_COLOUR != _CLOSED_SHELL_COLOUR
        finally:
            path.unlink()

    def test_html_table_reports_the_scalars_and_the_verdict(self) -> None:
        from vibeview.qvf import QVFReader
        from vibeview.renderers.qtaim import QTAIMRenderer

        path = self._scalars_qvf()
        try:
            reader = QVFReader(path)
            renderer = QTAIMRenderer(reader.get_section("qtaim"), reader)
            html = renderer.render_to_html(["O", "H", "H"])

            # Only bond critical points get a row; the RCP is not a bond.
            # (count the body rows -- <thead> contributes a <tr> of its own)
            body = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
            assert body.count("<tr>") == 2
            # Atom labels are 1-based and element-tagged.
            assert "O1&ndash;H2" in html or "O1–H2" in html
            # The numbers themselves.
            assert "2.5500" in html
            assert "-62.9800" in html
            assert "0.039" in html
            # And Bader's classification, both ways round.
            assert "shared-shell" in html
            assert "closed-shell" in html
        finally:
            path.unlink()

    def test_html_degrades_without_atom_symbols(self) -> None:
        from vibeview.qvf import QVFReader
        from vibeview.renderers.qtaim import QTAIMRenderer

        path = self._scalars_qvf()
        try:
            reader = QVFReader(path)
            renderer = QTAIMRenderer(reader.get_section("qtaim"), reader)
            html = renderer.render_to_html(None)
            assert "atom 1" in html
            assert "shared-shell" in html
        finally:
            path.unlink()

    def test_no_bond_paths(self) -> None:
        import pyvista as pv

        from vibeview.qvf import QVFReader
        from vibeview.renderers.qtaim import QTAIMRenderer

        qtaim_json = json.dumps(
            {
                "points": [
                    {"type": "ncp", "position": [0.0, 0.0, 0.0], "rho": 100.0, "laplacian": -500.0}
                ],
            }
        ).encode()
        path = _make_qvf(
            [
                {
                    "id": "qtaim",
                    "kind": "topology.qtaim",
                    "members": {
                        "critical_points": {
                            "path": "cp.json",
                            "format": "json",
                            "sha256": _sha(qtaim_json),
                        }
                    },
                }
            ],
            {"cp.json": qtaim_json},
        )
        try:
            reader = QVFReader(path)
            section = reader.get_section("qtaim")
            renderer = QTAIMRenderer(section, reader)
            plotter = pv.Plotter(off_screen=True)
            msg = renderer.add_to_plotter(plotter)
            assert "1 critical points" in msg
            assert "bond paths" not in msg
        finally:
            path.unlink()


class TestFatBands:
    def test_plain_bands_no_projections(self) -> None:
        from vibeview.qvf import QVFReader
        from vibeview.renderers.bands import BandsRenderer

        kpath_json = json.dumps({"segments": [], "fermi_energy_ev": -4.7}).encode()
        eig = np.zeros((1, 10, 5), dtype=np.float64)
        eig_bin = eig.tobytes()
        path = _make_qvf(
            [
                {
                    "id": "bands",
                    "kind": "bands",
                    "members": {
                        "kpath": {"path": "kp.json", "format": "json", "sha256": _sha(kpath_json)},
                        "eigenvalues": {
                            "path": "eig.bin",
                            "format": "binary",
                            "dtype": "float64",
                            "shape": [1, 10, 5],
                            "sha256": _sha(eig_bin),
                        },
                    },
                }
            ],
            {"kp.json": kpath_json, "eig.bin": eig_bin},
        )
        try:
            reader = QVFReader(path)
            section = reader.get_section("bands")
            renderer = BandsRenderer(section, reader)
            html = renderer.render_to_html()
            assert "Band Structure" in html
        finally:
            path.unlink()

    def test_fat_bands_with_projections(self) -> None:
        from vibeview.qvf import QVFReader
        from vibeview.renderers.bands import BandsRenderer

        kpath_json = json.dumps(
            {
                "segments": [],
                "fermi_energy_ev": -4.7,
                "channels": ["Si-3s", "Si-3p", "O-2p"],
            }
        ).encode()
        eig = np.zeros((1, 10, 3), dtype=np.float64)
        proj = np.zeros((10, 3, 3), dtype=np.float64)
        # Make channel 0 dominant for first 5 kpts, channel 1 for rest.
        proj[:5, :, 0] = 1.0
        proj[5:, :, 1] = 1.0
        eig_bin = eig.tobytes()
        proj_bin = proj.tobytes()
        path = _make_qvf(
            [
                {
                    "id": "bands",
                    "kind": "bands",
                    "members": {
                        "kpath": {"path": "kp.json", "format": "json", "sha256": _sha(kpath_json)},
                        "eigenvalues": {
                            "path": "eig.bin",
                            "format": "binary",
                            "dtype": "float64",
                            "shape": [1, 10, 3],
                            "sha256": _sha(eig_bin),
                        },
                        "projections": {
                            "path": "proj.bin",
                            "format": "binary",
                            "dtype": "float64",
                            "shape": [10, 3, 3],
                            "sha256": _sha(proj_bin),
                        },
                    },
                }
            ],
            {"kp.json": kpath_json, "eig.bin": eig_bin, "proj.bin": proj_bin},
        )
        try:
            reader = QVFReader(path)
            section = reader.get_section("bands")
            renderer = BandsRenderer(section, reader)
            html = renderer.render_to_html()
            assert "Si-3s" in html
            assert "Si-3p" in html
            assert "O-2p" in html
            # Should have legend entries.
            assert "showlegend" in html.lower()
        finally:
            path.unlink()


class TestBondOrdersEnrichment:
    """bond_orders section enriches the bond colouring pipeline."""

    def test_bond_orders_override_inferred(self) -> None:
        from vibeview.qvf import QVFReader

        struct_json = json.dumps(
            {
                "atoms": [
                    {"symbol": "O", "position": [0.0, 0.0, 0.117], "atomic_number": 8},
                    {"symbol": "H", "position": [0.0, 0.757, -0.469], "atomic_number": 1},
                    {"symbol": "H", "position": [0.0, -0.757, -0.469], "atomic_number": 1},
                ],
                "pbc": [False, False, False],
            }
        ).encode()
        bo_json = json.dumps(
            {
                "method": "mayer",
                "pairs": [
                    {"i": 0, "j": 1, "order": 1.23},
                    {"i": 0, "j": 2, "order": 0.87},
                ],
            }
        ).encode()
        path = _make_qvf(
            [
                {
                    "id": "structure",
                    "kind": "structure",
                    "members": {
                        "structure": {
                            "path": "s.json",
                            "format": "json",
                            "sha256": _sha(struct_json),
                        }
                    },
                },
                {
                    "id": "bo",
                    "kind": "bond_orders",
                    "members": {
                        "bond_orders": {
                            "path": "bo.json",
                            "format": "json",
                            "sha256": _sha(bo_json),
                        }
                    },
                },
            ],
            {"s.json": struct_json, "bo.json": bo_json},
        )
        try:
            reader = QVFReader(path)
            struct = reader.read_structure()
            bonds = reader.infer_bonds(struct)
            assert len(bonds) == 2
            orders = {frozenset([b[0], b[1]]): b[2] for b in bonds}
            assert abs(orders[frozenset([0, 1])] - 1.23) < 0.01
            assert abs(orders[frozenset([0, 2])] - 0.87) < 0.01
        finally:
            path.unlink()


def test_qtaim_sidebar_activation_renders_the_panel(tmp_path):
    """A sidebar click must render QTAIM, not only select an empty section."""
    pytest.importorskip("trame")
    from tests.conftest import build_qvf
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    structure = {"atoms": [
        {"symbol": "H", "atomic_number": 1, "position": [0, 0, 0]},
        {"symbol": "H", "atomic_number": 1, "position": [0.74, 0, 0]},
    ]}
    topology = {"points": [
        {"type": "bcp", "position": [0.37, 0, 0], "rho": 0.26, "laplacian": -0.54},
    ], "bond_paths": [
        {"atoms": [0, 1], "path": [[0, 0, 0], [0.37, 0, 0], [0.74, 0, 0]]},
    ]}
    path = build_qvf(tmp_path / "qtaim.qvf", [
        ("structure", "structure", {
            "structure": ("json", "structure.json", json.dumps(structure).encode()),
        }),
        ("qtaim", "topology.qtaim", {
            "critical_points": ("json", "qtaim.json", json.dumps(topology).encode()),
        }),
    ])
    with QVFReader(path) as reader:
        app = create_app(reader)
        app.controller.activate_section("qtaim")
        assert "1 critical points" in app.state.status_message
        assert "1 bond paths" in app.state.status_message
        assert "0.26" in app.state.properties_html
        assert app.state.properties_title == "QTAIM Topology"
        app.controller.activate_section("structure")
        assert not app.state.properties_html
