"""``forces`` drawn as arrows and ``hessian`` as a frequency table (#159)."""

from __future__ import annotations

import json
import math
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
from vibeview.renderers.derivatives import (
    FORCE_PREFIX,
    forces_rows,
    forces_summary,
    hessian_frequencies,
    hessian_masses,
    pressure_gpa,
)
from vibeview.units import GPA_PER_HARTREE_BOHR3, HESSIAN_EIGENVALUE_TO_CM1

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

_GRADIENT = np.array([[0.0, 0.0, 0.02], [0.0, 0.0, -0.02]])
_K = 0.5  # hartree/bohr², the diatomic's force constant
_MASSES = [1.008, 18.998]  # H–F


def _structure(symbols: list[str]) -> bytes:
    numbers = {"H": 1, "F": 9, "Xe": 54}
    atoms = [
        {"symbol": s, "position": [0.0, 0.0, z], "atomic_number": numbers[s]}
        for s, z in zip(symbols, [-0.46, 0.46], strict=True)
    ]
    return json.dumps({"atoms": atoms}).encode()


def _diatomic_hessian(k: float = _K) -> np.ndarray:
    """A spring of constant ``k`` along z between two atoms: 6×6, rank 1."""
    h = np.zeros((6, 6))
    h[2, 2] = h[5, 5] = k
    h[2, 5] = h[5, 2] = -k
    return h


def _archive(
    tmp_path: Path,
    *,
    forces_meta: dict[str, Any] | None = None,
    hessian_meta: dict[str, Any] | None = None,
    gradient: np.ndarray = _GRADIENT,
    stress: np.ndarray | None = None,
    hessian: np.ndarray | None = None,
    symbols: list[str] | None = None,
    extra_structure: bool = False,
) -> Path:
    sections: list[Any] = [
        ("structure", "structure",
         {"structure": ("json", "structure.json", _structure(symbols or ["H", "F"]))}),
    ]
    if extra_structure:
        sections.append(("other", "structure",
                         {"structure": ("json", "other.json", _structure(symbols or ["H", "F"]))}))
    force_members: dict[str, Any] = {
        "gradient": ("binary", "f/g.bin", gradient.tobytes(), "float64", list(gradient.shape)),
    }
    if stress is not None:
        force_members["stress"] = ("binary", "f/s.bin", stress.tobytes(), "float64", [3, 3])
    if forces_meta is not None:
        force_members["meta"] = ("json", "f/meta.json", json.dumps(forces_meta).encode())
    sections.append(("forces", "forces", force_members))
    matrix = _diatomic_hessian() if hessian is None else hessian
    hessian_members: dict[str, Any] = {
        "matrix": ("binary", "h/m.bin", matrix.tobytes(), "float64", list(matrix.shape)),
    }
    if hessian_meta is not None:
        hessian_members["meta"] = ("json", "h/meta.json", json.dumps(hessian_meta).encode())
    sections.append(("hessian", "hessian", hessian_members))
    return build_qvf(tmp_path / "deriv.qvf", sections)


# ── readers ─────────────────────────────────────────────────────────────────


def test_forces_reader_negates_for_display_and_keeps_the_gradient(tmp_path: Path) -> None:
    with QVFReader(_archive(tmp_path, forces_meta={"method": "rks"})) as reader:
        data = reader.read_forces("forces")
    np.testing.assert_allclose(data.gradient, _GRADIENT)
    np.testing.assert_allclose(data.forces, -_GRADIENT)
    assert (data.structure_id, data.method) == ("structure", "rks")


def test_the_meta_structure_ref_names_the_structure(tmp_path: Path) -> None:
    path = _archive(tmp_path, forces_meta={"structure_ref": "other"}, extra_structure=True)
    with QVFReader(path) as reader:
        assert reader.read_forces("forces").structure_id == "other"


def test_two_structures_need_a_structure_ref(tmp_path: Path) -> None:
    with QVFReader(_archive(tmp_path, extra_structure=True)) as reader, pytest.raises(
        QVFError, match="name the structure"
    ):
        reader.read_forces("forces")


def test_rows_must_match_the_structure(tmp_path: Path) -> None:
    gradient = np.zeros((3, 3))
    with QVFReader(_archive(tmp_path, gradient=gradient)) as reader, pytest.raises(
        QVFError, match=r"expected \(2, 3\)"
    ):
        reader.read_forces("forces")


def test_hessian_reader_and_its_shape(tmp_path: Path) -> None:
    meta = {"atom_masses_amu": _MASSES, "projected": True}
    with QVFReader(_archive(tmp_path, hessian_meta=meta)) as reader:
        data = reader.read_hessian("hessian")
    assert data.matrix.shape == (6, 6)
    assert data.projected and not data.mass_weighted
    assert data.atom_masses_amu == _MASSES
    with QVFReader(_archive(tmp_path, hessian=np.zeros((3, 3)))) as reader, pytest.raises(
        QVFError, match=r"expected \(6, 6\)"
    ):
        reader.read_hessian("hessian")


def test_a_mass_weighted_hessian_needs_masses(tmp_path: Path) -> None:
    path = _archive(tmp_path, hessian_meta={"mass_weighted": True})
    with QVFReader(path) as reader, pytest.raises(QVFError, match="needs atom_masses_amu"):
        reader.read_hessian("hessian")


# ── forces summaries ────────────────────────────────────────────────────────


def test_forces_summary_rows_and_pressure(tmp_path: Path) -> None:
    stress = np.diag([-1e-5, -1e-5, -1e-5])
    with QVFReader(_archive(tmp_path, stress=stress)) as reader:
        data = reader.read_forces("forces")
    rows = forces_rows(data, ["H", "F"])
    assert rows[0] == [1, "H", 0.0, 0.0, -0.02, 0.02]
    assert pressure_gpa(data) == pytest.approx(1e-5 * GPA_PER_HARTREE_BOHR3)
    summary = forces_summary(data, ["H", "F"])
    assert summary.startswith("largest |F| 0.02 Eh/bohr on H1, RMS 0.02 Eh/bohr")
    assert "pressure" in summary


# ── frequencies ─────────────────────────────────────────────────────────────


def test_diatomic_frequency_is_sqrt_k_over_mu(tmp_path: Path) -> None:
    meta = {"atom_masses_amu": _MASSES}
    with QVFReader(_archive(tmp_path, hessian_meta=meta)) as reader:
        data = reader.read_hessian("hessian")
    wavenumbers, _eigenvalues, source = hessian_frequencies(data, [1, 9])
    assert wavenumbers is not None and source == "the file's atom masses"
    mu = _MASSES[0] * _MASSES[1] / sum(_MASSES)
    assert wavenumbers.max() == pytest.approx(math.sqrt(_K / mu) * HESSIAN_EIGENVALUE_TO_CM1)
    assert np.allclose(wavenumbers[:-1], 0.0, atol=1e-3)


def test_a_mass_weighted_matrix_is_not_weighted_again(tmp_path: Path) -> None:
    weights = np.repeat(_MASSES, 3)
    weighted = _diatomic_hessian() / np.sqrt(np.outer(weights, weights))
    meta = {"atom_masses_amu": _MASSES, "mass_weighted": True}
    with QVFReader(_archive(tmp_path, hessian=weighted, hessian_meta=meta)) as reader:
        data = reader.read_hessian("hessian")
    mu = _MASSES[0] * _MASSES[1] / sum(_MASSES)
    wavenumbers, _e, _s = hessian_frequencies(data, [1, 9])
    assert wavenumbers is not None
    assert wavenumbers.max() == pytest.approx(math.sqrt(_K / mu) * HESSIAN_EIGENVALUE_TO_CM1)


def test_masses_fall_back_to_standard_ones_or_refuse(tmp_path: Path) -> None:
    with QVFReader(_archive(tmp_path)) as reader:
        data = reader.read_hessian("hessian")
    masses, source = hessian_masses(data, [1, 9])
    assert source == "standard atomic masses"
    assert masses is not None and masses[0] == pytest.approx(1.008)
    none, reason = hessian_masses(data, [1, 54])
    assert none is None and "54" in reason


def test_an_imaginary_mode_is_negative(tmp_path: Path) -> None:
    with QVFReader(_archive(tmp_path, hessian=_diatomic_hessian(-_K))) as reader:
        data = reader.read_hessian("hessian")
    wavenumbers, _e, _s = hessian_frequencies(data, [1, 9])
    assert wavenumbers is not None and wavenumbers.min() < -100


@requires_qvf_corpus("forces_hessian.qvf")
def test_corpus_hessian_reproduces_its_vibrations() -> None:
    corpus = qvf_corpus_dir()
    assert corpus is not None
    with QVFReader(corpus / "forces_hessian.qvf") as reader:
        data = reader.read_hessian("hessian")
        numbers = [a.atomic_number for a in reader.read_structure().atoms]
        stored = sorted(float(f) for f in reader.read_vibrations("vibrations").frequencies)
        forces = reader.read_forces("forces")
    wavenumbers, _e, _s = hessian_frequencies(data, numbers)
    assert wavenumbers is not None
    np.testing.assert_allclose(sorted(wavenumbers)[-3:], stored[-3:], atol=0.01)
    np.testing.assert_allclose(forces.forces, -forces.gradient)


# ── browser ─────────────────────────────────────────────────────────────────


def _arrows(plotter: Any) -> list[str]:
    return sorted(str(n) for n in plotter.actors if str(n).startswith(FORCE_PREFIX))


def test_browser_draws_force_arrows_and_clears_them(tmp_path: Path) -> None:
    from vibeview.app import create_app

    app = create_app(QVFReader(_archive(tmp_path)))
    state, ctrl = app.state, app.controller
    viewer = ctrl.force_arrows_changed.func.__self__.viewer
    ctrl.activate_section("forces")
    assert state.force_section_id == "forces"
    assert state.force_summary.startswith("largest |F| 0.02 Eh/bohr")
    assert state.properties_title == "Forces"
    assert _arrows(viewer.plotter) == [f"{FORCE_PREFIX}0", f"{FORCE_PREFIX}1"]
    # The H arrow points along −z, opposite the stored +z gradient: it
    # starts below H (z = −0.46 Å), just outside its sphere.
    assert viewer.plotter.actors[f"{FORCE_PREFIX}0"].GetBounds()[5] < -0.46

    ctrl.force_arrows_changed(2.0)
    assert state.force_arrow_length == 2.0
    ctrl.activate_section("structure")
    assert state.force_section_id == ""
    assert not _arrows(viewer.plotter)


def test_browser_hessian_panel_lists_frequencies(tmp_path: Path) -> None:
    from vibeview.app import create_app

    app = create_app(QVFReader(_archive(tmp_path, hessian_meta={"atom_masses_amu": _MASSES})))
    state, ctrl = app.state, app.controller
    ctrl.activate_section("hessian")
    assert state.properties_title == "Hessian"
    assert "Harmonic frequencies" in state.properties_html
    assert "not projected" in state.properties_html
    assert state.status_message.startswith("Hessian hessian: 6 × 6, highest frequency")


def test_bookmarks_keep_the_force_arrow_length(tmp_path: Path) -> None:
    from vibeview.app import create_app

    app = create_app(QVFReader(_archive(tmp_path)))
    state, ctrl = app.state, app.controller
    ctrl.activate_section("forces")
    ctrl.force_arrows_changed(3.0)
    ctrl.save_user_bookmark("long")
    ctrl.force_arrows_changed(1.0)
    ctrl.apply_user_bookmark("long")
    assert state.force_arrow_length == 3.0


# ── table, capture, show ────────────────────────────────────────────────────


def test_table_cli_lists_forces_and_frequencies(tmp_path: Path) -> None:
    path = str(_archive(tmp_path, hessian_meta={"atom_masses_amu": _MASSES}))
    forces = CliRunner().invoke(main, ["table", path, "--kind", "forces", "--format", "json"])
    assert forces.exit_code == 0, forces.output
    assert json.loads(forces.output)[0]["fz_Eh/bohr"] == -0.02
    hessian = CliRunner().invoke(main, ["table", path, "--kind", "hessian", "--format", "csv"])
    assert hessian.exit_code == 0, hessian.output
    assert hessian.output.splitlines()[0] == "mode,frequency_cm-1,eigenvalue_Eh/(bohr2·amu)"


def test_table_refuses_a_hessian_without_masses(tmp_path: Path) -> None:
    from vibeview.tables import extract_table

    with QVFReader(_archive(tmp_path, symbols=["H", "Xe"])) as reader, pytest.raises(
        ValueError, match="no tabulated mass"
    ):
        extract_table(reader, "hessian")


def test_capture_draws_forces_and_refuses_a_hessian(tmp_path: Path) -> None:
    path = str(_archive(tmp_path))
    out = tmp_path / "forces.png"
    drawn = CliRunner().invoke(main, ["capture", path, "-s", "forces", "-o", str(out)])
    assert drawn.exit_code == 0, drawn.output
    assert "largest |F|" in drawn.output and out.stat().st_size > 0
    refused = CliRunner().invoke(main, ["capture", path, "-s", "hessian",
                                        "-o", str(tmp_path / "h.png")])
    assert refused.exit_code == 1
    assert "--kind hessian" in refused.output


def test_show_points_at_the_table(tmp_path: Path) -> None:
    from vibeview.tui.show import show

    text = show(_archive(tmp_path), "forces", size=(40, 12), plain=True)
    assert "vibe-view table FILE --kind forces" in text
