"""``wavefunction.bloch`` orbitals evaluated on demand (#162)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from click.testing import CliRunner

from tests._qvf_corpus import qvf_corpus_dir, requires_qvf_corpus
from tests.conftest import build_qvf
from vibeview.cli import main
from vibeview.qvf import QVFError, QVFReader
from vibeview.renderers.bloch import (
    BlochWavefunctionRenderer,
    bvk_cells,
    kpoint_label,
    opening_orbital,
    orbital_rows,
)
from vibeview.units import BOHR_TO_ANGSTROM

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

_A = 2.0  # Å, the chain's period along x
_ALPHA = 0.4166
_KPOINTS = [[0.0, 0.0, 0.0], [0.5, 0.0, 0.0], [0.25, 0.0, 0.0]]


def _coefficients() -> np.ndarray:
    """[n_k, 1, 2, 2, 2]: bonding then antibonding at each k, with a phase twist."""
    c = np.zeros((3, 1, 2, 2, 2))
    for k in range(3):
        twist = np.exp(1j * 0.3 * k)
        for m, sign in enumerate((1.0, -1.0)):
            values = np.array([1.0, sign]) * twist / np.sqrt(2)
            c[k, 0, m, :, 0] = values.real
            c[k, 0, m, :, 1] = values.imag
    return c


def _archive(
    tmp_path: Path, *, pbc: list[bool] | None = None, mesh: list[int] | None = None,
    coefficients: np.ndarray | None = None, n_mo_per_k: list[int] | None = None,
) -> Path:
    structure = {
        "atoms": [
            {"symbol": "H", "position": [0.0, 0.0, 0.0], "atomic_number": 1},
            {"symbol": "H", "position": [0.8, 0.0, 0.0], "atomic_number": 1},
        ],
        "pbc": pbc or [True, False, False],
        "lattice_vectors": [[_A, 0, 0], [0, 10.0, 0], [0, 0, 10.0]],
    }
    basis = {"structure_ref": "structure", "pure": True, "n_ao": 2, "shells": [
        {"center": i, "l": 0, "exponents": [_ALPHA], "coefficients": [1.0]} for i in range(2)
    ]}
    kpoints: dict[str, Any] = {"kpoints": _KPOINTS, "labels": ["G", "X", ""]}
    if mesh is not None:
        kpoints["mesh"] = mesh
    meta: dict[str, Any] = {"spin": "restricted", "n_spin": 1, "n_mo": 2, "n_ao": 2,
                            "complex": True, "k_point_convention": "fractional"}
    if n_mo_per_k is not None:
        meta["n_mo_per_k"] = n_mo_per_k
    c = _coefficients() if coefficients is None else coefficients
    energies = np.array([[[-0.6, 0.4]], [[-0.5, 0.5]], [[-0.55, 0.45]]])
    occupations = np.array([[[2.0, 0.0]]] * 3)
    return build_qvf(tmp_path / "bloch.qvf", [
        ("structure", "structure",
         {"structure": ("json", "s.json", json.dumps(structure).encode())}),
        ("wf", "wavefunction.bloch", {
            "basis": ("json", "wf/basis.json", json.dumps(basis).encode()),
            "kpoints": ("json", "wf/k.json", json.dumps(kpoints).encode()),
            "mo_metadata": ("json", "wf/mo.json", json.dumps(meta).encode()),
            "mo_coefficients": ("binary", "wf/c.bin", c.tobytes(), "float64", list(c.shape)),
            "energies": ("binary", "wf/e.bin", energies.tobytes(), "float64", [3, 1, 2]),
            "occupations": ("binary", "wf/o.bin", occupations.tobytes(), "float64", [3, 1, 2]),
        }),
    ])


def _renderer(reader: QVFReader) -> BlochWavefunctionRenderer:
    return BlochWavefunctionRenderer(reader.get_section("wf"), reader)


# ── reader ──────────────────────────────────────────────────────────────────


def test_reader_returns_the_orbital_set(tmp_path: Path) -> None:
    with QVFReader(_archive(tmp_path, mesh=[4, 1, 1])) as reader:
        data = reader.read_wavefunction_bloch("wf")
    assert data.structure_id == "structure"
    assert data.coefficients.shape == (3, 1, 2, 2)
    assert data.coefficients.dtype == np.complex128
    assert data.labels == ["G", "X", ""] and data.mesh == [4, 1, 1]
    assert data.n_mo_per_k == [2, 2, 2]
    assert kpoint_label(data, 1) == "X (0.5, 0, 0)" and kpoint_label(data, 2) == "(0.25, 0, 0)"


def test_reader_refuses_a_molecule_and_wrong_shapes(tmp_path: Path) -> None:
    with QVFReader(_archive(tmp_path, pbc=[False, False, False])) as reader, pytest.raises(
        QVFError, match="no periodic axis"
    ):
        reader.read_wavefunction_bloch("wf")
    (tmp_path / "b").mkdir()
    wrong = np.zeros((3, 1, 2, 3, 2))
    with QVFReader(_archive(tmp_path / "b", coefficients=wrong)) as reader, pytest.raises(
        QVFError, match="mo_coefficients has shape"
    ):
        reader.read_wavefunction_bloch("wf")


def test_padding_orbitals_are_left_out(tmp_path: Path) -> None:
    with QVFReader(_archive(tmp_path, n_mo_per_k=[2, 1, 2])) as reader:
        data = reader.read_wavefunction_bloch("wf")
        rows = orbital_rows(data)
        assert len(rows) == 5  # X keeps one orbital
        with pytest.raises(ValueError, match="k-point 1 has 1"):
            _renderer(reader).evaluate(1, 0, 1, n_per_dim=8)


# ── the Bloch sum ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(("kpoint", "k1"), [(0, 0.0), (1, 0.5), (2, 0.25)])
def test_blochs_theorem_holds(tmp_path: Path, kpoint: int, k1: float) -> None:
    """ψ_k(r + a₁) = e^{2πi k₁} ψ_k(r): the grid's first and last x-planes are a₁ apart."""
    with QVFReader(_archive(tmp_path)) as reader:
        _grid, values, dropped = _renderer(reader).evaluate(kpoint, 0, 0, n_per_dim=20)
    first, last = values[0], values[-1]
    significant = np.abs(first) > 1e-3 * np.abs(first).max()
    np.testing.assert_allclose(
        last[significant], first[significant] * np.exp(2j * np.pi * k1), rtol=1e-6, atol=1e-9
    )
    assert dropped == 0.0


def test_the_image_sum_matches_a_direct_lattice_sum(tmp_path: Path) -> None:
    """Ratios of ψ at two grid points equal those of an independent sum over T."""
    with QVFReader(_archive(tmp_path)) as reader:
        grid, values, _ = _renderer(reader).evaluate(2, 0, 0, n_per_dim=12)
    coefficients = _coefficients()[2, 0, 0, :, 0] + 1j * _coefficients()[2, 0, 0, :, 1]
    centres = np.array([[0.0, 0, 0], [0.8, 0, 0]]) / BOHR_TO_ANGSTROM
    a1 = np.array([_A, 0, 0]) / BOHR_TO_ANGSTROM

    def direct(point_angstrom: np.ndarray) -> complex:
        r = point_angstrom / BOHR_TO_ANGSTROM
        total = 0j
        for n in range(-30, 31):
            for c, centre in zip(coefficients, centres, strict=True):
                d = r - centre - n * a1
                total += np.exp(2j * np.pi * 0.25 * n) * c * np.exp(-_ALPHA * d @ d)
        return total

    def point(i: int, j: int, k: int) -> np.ndarray:
        return grid.origin + np.array([i, j, k]) @ grid.voxel_vectors

    p, q = (3, 6, 6), (8, 5, 7)
    assert values[p] / values[q] == pytest.approx(direct(point(*p)) / direct(point(*q)), rel=1e-6)


def test_bvk_normalization_and_padded_vacuum(tmp_path: Path) -> None:
    with QVFReader(_archive(tmp_path)) as reader:
        grid, plain, _ = _renderer(reader).evaluate(0, 0, 0, n_per_dim=10)
    (tmp_path / "m").mkdir()
    with QVFReader(_archive(tmp_path / "m", mesh=[4, 1, 1])) as reader:
        data = reader.read_wavefunction_bloch("wf")
        _g, meshed, _ = _renderer(reader).evaluate(0, 0, 0, n_per_dim=10)
    assert bvk_cells(data, [True, False, False]) == 4
    np.testing.assert_allclose(meshed, plain / 2.0)
    # One cell along x; the atoms plus padding along the vacuum axes y and z.
    assert grid.origin[0] == pytest.approx(0.0)
    assert grid.voxel_vectors[0, 0] * 9 == pytest.approx(_A)
    assert grid.origin[1] < -1.0 and grid.origin[2] < -1.0


def test_opening_orbital_is_the_highest_occupied(tmp_path: Path) -> None:
    with QVFReader(_archive(tmp_path)) as reader:
        assert opening_orbital(reader.read_wavefunction_bloch("wf")) == 0


# ── browser ─────────────────────────────────────────────────────────────────


def _surfaces(plotter: Any) -> list[str]:
    return sorted(str(n) for n in plotter.actors if str(n).startswith("bloch_orbital_"))


def test_browser_opens_switches_and_clears(tmp_path: Path) -> None:
    from vibeview.app import create_app

    app = create_app(QVFReader(_archive(tmp_path)))
    state, ctrl = app.state, app.controller
    viewer = ctrl.bloch_changed.func.__self__.viewer
    ctrl.activate_section("wf")
    assert state.bloch_section_id == "wf"
    assert state.bloch_summary.startswith("orbital 1 at k = G (0, 0, 0): real part")
    assert len(state.bloch_kpoint_items) == 3 and len(state.bloch_orbital_items) == 2
    assert state.properties_title == "Bloch Orbitals"
    assert _surfaces(viewer.plotter)

    # Every control sends all five values (#122); a new k-point re-fits the level.
    ctrl.bloch_changed(1, 0, -1, "modulus", 0)
    assert state.bloch_summary.startswith("orbital 1 at k = X (0.5, 0, 0): |ψ| coloured by phase")
    assert _surfaces(viewer.plotter) == ["bloch_orbital_surface_modulus"]

    ctrl.activate_section("structure")
    assert state.bloch_section_id == ""
    assert not _surfaces(viewer.plotter)


def test_bookmarks_keep_the_orbital(tmp_path: Path) -> None:
    from vibeview.app import create_app

    app = create_app(QVFReader(_archive(tmp_path)))
    state, ctrl = app.state, app.controller
    ctrl.activate_section("wf")
    ctrl.bloch_changed(2, 0, 1, "imag", 0)
    ctrl.save_user_bookmark("antibonding")
    ctrl.bloch_changed(0, 0, 0, "real", 0)
    ctrl.apply_user_bookmark("antibonding")
    assert (state.bloch_kpoint, state.bloch_orbital, state.bloch_part) == (2, 1, "imag")
    assert state.bloch_summary.startswith("orbital 2 at k = (0.25, 0, 0): imaginary part")


# ── table, capture, show ────────────────────────────────────────────────────


def test_table_capture_and_show(tmp_path: Path) -> None:
    from vibeview.tui.show import show

    path = str(_archive(tmp_path))
    table = CliRunner().invoke(main, ["table", path, "--kind", "wavefunction.bloch",
                                      "--format", "json"])
    assert table.exit_code == 0, table.output
    assert json.loads(table.output)[0] == {
        "kpoint": 1, "label": "G", "spin": "—", "orbital": 1, "energy_eV": -16.3268,
        "occupation": 2.0,
    }
    out = tmp_path / "orbital.png"
    drawn = CliRunner().invoke(main, ["capture", path, "-s", "wf", "--part", "modulus",
                                      "-o", str(out)])
    assert drawn.exit_code == 0, drawn.output
    assert "orbital 1 at k = G" in drawn.output and out.stat().st_size > 0
    assert "--kind wavefunction.bloch" in show(path, "wf", size=(40, 12), plain=True)


@requires_qvf_corpus("bloch.qvf")
def test_corpus_chain_obeys_blochs_theorem() -> None:
    corpus = qvf_corpus_dir()
    assert corpus is not None
    with QVFReader(corpus / "bloch.qvf") as reader:
        renderer = BlochWavefunctionRenderer(reader.get_section("wf_bloch"), reader)
        data = renderer.load()
        assert data.spin == "unrestricted" and data.labels == ["G", "X"]
        for kpoint, sign in ((0, 1.0), (1, -1.0)):
            _grid, values, _ = renderer.evaluate(kpoint, 0, 0, n_per_dim=16)
            first, last = values[0], values[-1]
            significant = np.abs(first) > 1e-3 * np.abs(first).max()
            np.testing.assert_allclose(last[significant], sign * first[significant],
                                       rtol=1e-6, atol=1e-9)
