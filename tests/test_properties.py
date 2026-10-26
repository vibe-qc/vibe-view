"""``properties.*`` sections as tables, with a dipole arrow for the electric kind (#161)."""

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
from vibeview.renderers.properties import (
    PROPERTY_PREFIX,
    ROOT_DIPOLE_NAME,
    born_charge_sum,
    dipole_anchor,
    dipole_arrow_mesh,
    is_mechanically_stable,
    property_rows,
    summary,
)
from vibeview.units import BOHR_TO_ANGSTROM

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

_WATER = {
    "atoms": [
        {"symbol": "O", "position": [0.0, 0.0, 0.1173], "atomic_number": 8},
        {"symbol": "H", "position": [0.0, 0.7572, -0.4692], "atomic_number": 1},
        {"symbol": "H", "position": [0.0, -0.7572, -0.4692], "atomic_number": 1},
    ],
}
_ELECTRIC = {
    "origin": "center_of_mass",
    "dipole": {"vector": [0.0, 0.0, -2.0], "total": 2.0},
    "quadrupole": {"matrix": np.diag([-1.0, 1.5, -0.5]).tolist(), "traceless": True,
                   "convention": "buckingham"},
    "polarizability": {"matrix": np.diag([8.0, 9.0, 10.0]).tolist(), "isotropic": 9.0},
}
_CUBIC = [[36.4, 8.8, 8.8, 0, 0, 0], [8.8, 36.4, 8.8, 0, 0, 0], [8.8, 8.8, 36.4, 0, 0, 0],
          [0, 0, 0, 8.0, 0, 0], [0, 0, 0, 0, 8.0, 0], [0, 0, 0, 0, 0, 8.0]]


def _archive(
    tmp_path: Path, kind: str, payload: dict[str, Any], *, root_dipole: bool = False,
    structures: int = 1,
) -> Path:
    sections: list[Any] = [
        (f"structure{i or ''}", "structure",
         {"structure": ("json", f"s{i}.json", json.dumps(_WATER).encode())})
        for i in range(structures)
    ]
    sections.append(("props", kind, {
        "properties": ("json", "props.json", json.dumps(payload).encode()),
    }))
    path = build_qvf(tmp_path / "props.qvf", sections)
    if root_dipole:
        import zipfile

        with zipfile.ZipFile(path) as zf:
            members = {n: zf.read(n) for n in zf.namelist()}
        manifest = json.loads(members["manifest.json"])
        manifest["dipole_moment"] = {"total_debye": 2.0, "vector_debye": [0.0, 0.0, -2.0]}
        members["manifest.json"] = json.dumps(manifest).encode()
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in members.items():
                zf.writestr(name, data)
    return path


def _read(tmp_path: Path, kind: str, payload: dict[str, Any], **kwargs: Any) -> Any:
    with QVFReader(_archive(tmp_path, kind, payload, **kwargs)) as reader:
        return reader.read_properties("props")


# ── reader ──────────────────────────────────────────────────────────────────


def test_reader_resolves_the_structure_and_keeps_the_payload(tmp_path: Path) -> None:
    data = _read(tmp_path, "properties.electric", _ELECTRIC)
    assert data.kind == "properties.electric"
    assert data.structure_id == "structure"
    assert data.payload["dipole"]["vector"] == [0.0, 0.0, -2.0]
    assert _read(tmp_path, "properties.electric", _ELECTRIC, structures=2).structure_id is None


@pytest.mark.parametrize(
    ("kind", "payload", "message"),
    [
        ("properties.electric", {"dipole": {"vector": [0, 1]}}, "dipole.vector"),
        ("properties.electric", {"dipole": {"total": 1.0}}, "needs its vector"),
        ("properties.elastic", {"stiffness": [[1, 2], [2, 1]]}, "stiffness"),
        ("properties.dielectric", {"electronic": np.eye(3).tolist(),
                                   "born_charges": [np.eye(3).tolist()]}, "1 tensors for 3"),
        ("properties.dielectric", {"electronic": np.eye(3).tolist(),
                                   "piezoelectric": np.zeros((3, 3)).tolist()}, "piezoelectric"),
    ],
)
def test_reader_refuses_wrong_shapes(
    tmp_path: Path, kind: str, payload: dict[str, Any], message: str
) -> None:
    # The vendored schema refuses some payloads at open; the reader the rest.
    with pytest.raises(QVFError, match=message), QVFReader(
        _archive(tmp_path, kind, payload)
    ) as reader:
        reader.read_properties("props")


# ── rows, summaries, checks ─────────────────────────────────────────────────


def test_electric_rows_and_summary(tmp_path: Path) -> None:
    data = _read(tmp_path, "properties.electric", _ELECTRIC)
    rows = property_rows(data)
    assert rows[:4] == [["dipole", "x", 0.0, "D"], ["dipole", "y", 0.0, "D"],
                        ["dipole", "z", -2.0, "D"], ["dipole", "total", 2.0, "D"]]
    assert ["quadrupole", "yy", 1.5, "D·Å"] in rows
    assert ["polarizability", "isotropic", 9.0, "a.u."] in rows
    assert summary(data) == "|μ| 2 D, isotropic α 9 a.u., quadrupole"


def test_elastic_rows_stability_and_moduli(tmp_path: Path) -> None:
    data = _read(tmp_path, "properties.elastic",
                 {"stiffness": _CUBIC, "bulk_modulus_gpa": 18.0, "average": "hill"})
    rows = property_rows(data)
    assert rows[0] == ["stiffness", "C11", 36.4, "GPa"]
    assert ["bulk modulus", "hill", 18.0, "GPa"] in rows
    assert is_mechanically_stable(_CUBIC)
    unstable = np.array(_CUBIC)
    unstable[3, 3] = -1.0
    assert not is_mechanically_stable(unstable)
    assert summary(data).startswith("mechanically stable, K 18 GPa")


def test_dielectric_born_charges_sum_and_rows(tmp_path: Path) -> None:
    charges = [np.diag([2.0] * 3).tolist(), np.diag([-1.0] * 3).tolist(),
               np.diag([-1.0] * 3).tolist()]
    data = _read(tmp_path, "properties.dielectric", {
        "electronic": np.diag([2.6] * 3).tolist(), "born_charges": charges,
    })
    np.testing.assert_allclose(born_charge_sum(charges), np.zeros((3, 3)))
    rows = property_rows(data, ["O", "H", "H"])
    assert ["born_charge", "Z*(H2) xx", -1.0, "e"] in rows
    assert summary(data) == "ε∞ 2.6, Born charges for 3 atoms"


# ── dipole anchor and arrow ─────────────────────────────────────────────────


def test_dipole_anchors() -> None:
    from vibeview.qvf import _atom_from_json

    atoms = [_atom_from_json(a) for a in _WATER["atoms"]]
    positions = np.array([a.position for a in atoms])
    np.testing.assert_allclose(dipole_anchor([1.0, 0.0, 0.0], atoms), [BOHR_TO_ANGSTROM, 0, 0])
    np.testing.assert_allclose(dipole_anchor("origin", atoms), np.zeros(3))
    nuclear = dipole_anchor("center_of_nuclear_charge", atoms)
    np.testing.assert_allclose(nuclear, (positions * [[8], [1], [1]]).sum(0) / 10)
    mass = dipole_anchor("center_of_mass", atoms)
    assert mass[2] > positions[:, 2].mean()  # pulled towards the heavy O
    np.testing.assert_allclose(dipole_anchor("somewhere", atoms), positions.mean(0))


def test_dipole_arrow_scale() -> None:
    mesh = dipole_arrow_mesh(np.zeros(3), np.array([0.0, 0.0, -2.0]))
    assert mesh is not None
    assert mesh.bounds[4] == pytest.approx(-0.6)  # 0.3 Å per debye
    long = dipole_arrow_mesh(np.zeros(3), np.array([0.0, 0.0, 20.0]))
    assert long is not None and long.bounds[5] == pytest.approx(3.0)  # capped at 3 Å
    assert dipole_arrow_mesh(np.zeros(3), np.zeros(3)) is None


# ── browser ─────────────────────────────────────────────────────────────────


def test_browser_draws_the_dipole_and_hides_the_root_arrow(tmp_path: Path) -> None:
    from vibeview.app import create_app

    app = create_app(QVFReader(_archive(tmp_path, "properties.electric", _ELECTRIC,
                                        root_dipole=True)))
    state, ctrl = app.state, app.controller
    viewer = ctrl.activate_section.func.__self__.viewer
    root = viewer.plotter.actors[ROOT_DIPOLE_NAME]
    assert root.GetVisibility()
    ctrl.activate_section("props")
    assert state.properties_title == "Electric Properties"
    assert "Quadrupole" in state.properties_html
    assert f"{PROPERTY_PREFIX}arrow" in viewer.plotter.actors
    assert not root.GetVisibility()
    ctrl.activate_section("structure")
    assert f"{PROPERTY_PREFIX}arrow" not in viewer.plotter.actors
    assert viewer.plotter.actors[ROOT_DIPOLE_NAME].GetVisibility()


def test_browser_elastic_and_dielectric_panels(tmp_path: Path) -> None:
    from vibeview.app import create_app

    app = create_app(QVFReader(_archive(tmp_path, "properties.elastic", {"stiffness": _CUBIC})))
    app.controller.activate_section("props")
    assert "mechanically stable" in app.state.properties_html
    assert app.state.status_message == "Elastic Constants: mechanically stable"


# ── table, capture, show ────────────────────────────────────────────────────


def test_table_cli(tmp_path: Path) -> None:
    path = str(_archive(tmp_path, "properties.electric", _ELECTRIC))
    result = CliRunner().invoke(
        main, ["table", path, "--kind", "properties.electric", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert rows[2] == {"quantity": "dipole", "component": "z", "value": -2.0, "unit": "D"}


def test_capture_draws_an_electric_dipole_and_refuses_the_rest(tmp_path: Path) -> None:
    electric = str(_archive(tmp_path, "properties.electric", _ELECTRIC))
    out = tmp_path / "dipole.png"
    drawn = CliRunner().invoke(main, ["capture", electric, "-s", "props", "-o", str(out)])
    assert drawn.exit_code == 0, drawn.output
    assert "|μ| 2 D" in drawn.output and out.stat().st_size > 0
    (tmp_path / "e").mkdir()
    elastic = str(_archive(tmp_path / "e", "properties.elastic", {"stiffness": _CUBIC}))
    refused = CliRunner().invoke(main, ["capture", elastic, "-s", "props",
                                        "-o", str(tmp_path / "x.png")])
    assert refused.exit_code == 1
    assert "--kind properties.elastic" in refused.output


def test_show_points_at_the_table(tmp_path: Path) -> None:
    from vibeview.tui.show import show

    text = show(_archive(tmp_path, "properties.dielectric", {"electronic": np.eye(3).tolist()}),
                "props", size=(40, 12), plain=True)
    assert "--kind properties.dielectric" in text


@requires_qvf_corpus("properties_molecular.qvf", "properties_periodic.qvf")
def test_corpus_properties() -> None:
    corpus = qvf_corpus_dir()
    assert corpus is not None
    with QVFReader(corpus / "properties_molecular.qvf") as reader:
        electric = reader.read_properties("electric")
        assert electric.payload["dipole"]["vector"] == reader.dipole_moment["vector_debye"]
    with QVFReader(corpus / "properties_periodic.qvf") as reader:
        elastic = reader.read_properties("elastic")
        dielectric = reader.read_properties("dielectric")
    assert is_mechanically_stable(elastic.payload["stiffness"])
    np.testing.assert_allclose(born_charge_sum(dielectric.payload["born_charges"]), 0.0)
    assert len(property_rows(dielectric)) == 63
