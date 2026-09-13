"""TREXIO backend, import-route and independent orbital-value regressions."""

from __future__ import annotations

import base64
import io
import json
import math
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner

from vibeview.cli import main
from vibeview.converters import convert_to_qvf, detect_format, format_capabilities
from vibeview.import_workflow import execute_import, plan_imports
from vibeview.qvf import QVFReader
from vibeview.trexio_import import trexio_to_qvf


@pytest.fixture
def trexio():
    return pytest.importorskip("trexio")


@pytest.fixture(params=["text", "hdf5"])
def dataset(request, trexio, tmp_path):
    backend = trexio.TREXIO_TEXT if request.param == "text" else trexio.TREXIO_HDF5
    path = tmp_path / ("calculation" if request.param == "text" else "calculation.hdf5")

    def write(fields=None):
        values = {
            "nucleus_num": 2,
            "nucleus_coord": [[0.0, 0.0, 0.0], [1.0, -2.0, 3.0]],
            "nucleus_charge": [6.0, 1.0],
            "nucleus_label": ["O1", "H2"],
        }
        values.update(fields or {})
        with trexio.File(str(path), "w", backend) as handle:
            for name, value in values.items():
                if value is not None:
                    getattr(trexio, f"write_{name}")(handle, value)
        return path

    return write


def _gto_fields(pure=True):
    counts = [2 * ell + 1 if pure else (ell + 1) * (ell + 2) // 2 for ell in range(4)]
    # Interleave shells in both the primitive and AO maps to catch importers
    # which assume that either array consists of contiguous shell blocks.
    ao_shell = [
        ell for component in range(max(counts)) for ell in range(4) if component < counts[ell]
    ]
    n_ao = len(ao_shell)
    return {
        "basis_type": "Gaussian",
        "basis_shell_num": 4,
        "basis_prim_num": 8,
        "basis_nucleus_index": [0, 1, 0, 1],
        "basis_shell_ang_mom": [0, 1, 2, 3],
        "basis_shell_index": [0, 1, 2, 3, 0, 1, 2, 3],
        "basis_exponent": np.linspace(0.4, 1.8, 8),
        "basis_coefficient": [0.2, 0.5, -0.7, 0.8, -0.3, 0.9, 0.4, -0.2],
        "basis_prim_factor": np.linspace(0.6, 1.5, 8),
        "basis_shell_factor": [0.7, 1.3, 0.9, 1.1],
        "ao_cartesian": int(not pure),
        "ao_num": n_ao,
        "ao_shell": ao_shell,
        "ao_normalization": np.linspace(0.3, 1.6, n_ao),
        "mo_num": n_ao + 1,
        "mo_coefficient": np.vstack([np.eye(n_ao), np.linspace(-0.8, 1.2, n_ao)]),
        "mo_energy": np.linspace(-1.0, 0.5, n_ao + 1),
        "mo_occupation": [2.0, 2.0] + [0.0] * (n_ao - 1),
    }


def _trexio_angular(ell, pure, x, y, z):
    """Independent polynomials in TREXIO order, from the format specification."""
    if not pure:
        return [
            x**i * y**j * z ** (ell - i - j)
            for i in range(ell, -1, -1)
            for j in range(ell - i, -1, -1)
        ]
    r2 = x * x + y * y + z * z
    return (
        [np.ones_like(x)],
        [z, x, y],
        [
            (3 * z * z - r2) / 2,
            math.sqrt(3) * x * z,
            math.sqrt(3) * y * z,
            math.sqrt(3) * (x * x - y * y) / 2,
            math.sqrt(3) * x * y,
        ],
        [
            z * (5 * z * z - 3 * r2) / 2,
            math.sqrt(6) * x * (5 * z * z - r2) / 4,
            math.sqrt(6) * y * (5 * z * z - r2) / 4,
            math.sqrt(15) * z * (x * x - y * y) / 2,
            math.sqrt(15) * x * y * z,
            math.sqrt(10) * x * (x * x - 3 * y * y) / 4,
            math.sqrt(10) * y * (3 * x * x - y * y) / 4,
        ],
    )[ell]


def test_structure_units_elements_and_checksums(dataset):
    path = dataset()
    before = (
        {p.name: p.read_bytes() for p in path.iterdir()} if path.is_dir() else path.read_bytes()
    )
    assert detect_format(path) == "trexio"
    with QVFReader(convert_to_qvf(path)) as reader:
        structure = reader.read_structure()
        assert [atom.symbol for atom in structure.atoms] == ["O", "H"]
        assert [atom.atomic_number for atom in structure.atoms] == [8, 1]
        np.testing.assert_allclose(
            structure.atoms[1].position, np.array([1, -2, 3]) * 0.529177210903
        )
        assert not any(structure.pbc)
        assert [section.kind for section in reader.sections] == ["structure"]
        for section in reader.sections:
            for member in section.members.values():
                reader._verify_and_read(member)
    after = {p.name: p.read_bytes() for p in path.iterdir()} if path.is_dir() else path.read_bytes()
    assert before == after


def test_geometry_without_labels_uses_nuclear_charge(dataset):
    with QVFReader(trexio_to_qvf(dataset({"nucleus_label": None}))) as reader:
        assert [atom.symbol for atom in reader.read_structure().atoms] == ["C", "H"]


def test_ecp_core_charge_restores_element_without_labels(dataset):
    fields = {"nucleus_label": None, "ecp_z_core": [2, 0]}
    with QVFReader(trexio_to_qvf(dataset(fields))) as reader:
        assert [atom.symbol for atom in reader.read_structure().atoms] == ["O", "H"]
        assert [atom.atomic_number for atom in reader.read_structure().atoms] == [8, 1]


def test_periodic_cell_vectors_stay_rows(dataset):
    cell = np.array([[4, 0, 0], [1, 5, 0], [2, 3, 6]], dtype=float)
    fields = {f"cell_{axis}": vector for axis, vector in zip("abc", cell, strict=True)}
    fields["pbc_periodic"] = 1
    with QVFReader(trexio_to_qvf(dataset(fields))) as reader:
        structure = reader.read_structure()
        assert structure.pbc == (True, True, True)
        assert structure.dim == 3
        np.testing.assert_allclose(structure.lattice_vectors, cell * 0.529177210903)


@pytest.mark.parametrize("pure", [True, False])
def test_orbital_values_match_trexio_definition(dataset, pure):
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    fields = _gto_fields(pure)
    with QVFReader(trexio_to_qvf(dataset(fields))) as reader:
        renderer = WavefunctionRenderer(reader.get_section("wavefunction"), reader)
        wf = renderer.load()
        assert wf.pure == pure
        np.testing.assert_allclose(wf.energies, fields["mo_energy"])
        np.testing.assert_allclose(wf.occupations, fields["mo_occupation"])
        positions = np.array([[0, 0, 0], [1, -2, 3]], dtype=float)
        axes = [np.array([0.17, 0.68]), np.array([-0.91, 0.32]), np.array([0.27, 1.2])]
        grid = np.meshgrid(*axes, indexing="ij")
        ao_values = []
        component = [0] * 4
        for ao, shell in enumerate(fields["ao_shell"]):
            x, y, z = [
                grid[i] - positions[fields["basis_nucleus_index"][shell], i] for i in range(3)
            ]
            radial = (
                sum(
                    fields["basis_coefficient"][p]
                    * fields["basis_prim_factor"][p]
                    * np.exp(-fields["basis_exponent"][p] * (x * x + y * y + z * z))
                    for p, owner in enumerate(fields["basis_shell_index"])
                    if owner == shell
                )
                * fields["basis_shell_factor"][shell]
            )
            angular = _trexio_angular(shell, pure, x, y, z)[component[shell]]
            ao_values.append(fields["ao_normalization"][ao] * radial * angular)
            component[shell] += 1
        expected = np.einsum("ma,axyz->mxyz", fields["mo_coefficient"], ao_values)
        for index, coefficients in enumerate(wf.mo_coefficients):
            actual = renderer._evaluate_on_grid(wf, coefficients, positions, *axes)
            np.testing.assert_allclose(actual, expected[index], rtol=1e-12, atol=1e-13)
        grid_data, volume = renderer.evaluate_mo(0, n_per_dim=12)
        assert volume.shape == grid_data.shape == (12, 12, 12)
        assert np.all(np.isfinite(volume)) and np.any(volume)
        assert "HOMO" in renderer.mo_table()[1]["title"]


def test_unrestricted_spin_rows_keep_their_metadata(dataset):
    fields = _gto_fields()
    n_mo = fields["mo_num"]
    fields["mo_spin"] = np.arange(n_mo) % 2
    fields["mo_occupation"] = np.linspace(0.1, 0.9, n_mo)
    fields["mo_symmetry"] = [f"sym{i}" for i in range(n_mo)]
    fields["mo_type"] = "Natural"
    with QVFReader(trexio_to_qvf(dataset(fields))) as reader:
        wf = reader.read_wavefunction_gto("wavefunction")
        assert wf.spin == "unrestricted" and wf.orbital_kind == "natural"
        assert wf.occupation_semantics == "electron_occupation"
        np.testing.assert_allclose(wf.alpha_energies, fields["mo_energy"][::2])
        np.testing.assert_allclose(wf.beta_energies, fields["mo_energy"][1::2])
        np.testing.assert_allclose(wf.alpha_occupations, fields["mo_occupation"][::2])
        np.testing.assert_allclose(wf.beta_occupations, fields["mo_occupation"][1::2])
        assert wf.symmetry_labels == fields["mo_symmetry"][::2]
        assert wf.symmetry_labels_beta == fields["mo_symmetry"][1::2]
        assert wf.mo_coefficients_alpha.shape == ((n_mo + 1) // 2, fields["ao_num"])
        assert wf.mo_coefficients_beta.shape == (n_mo // 2, fields["ao_num"])


def test_missing_orbital_metadata_is_not_invented(dataset):
    fields = _gto_fields()
    fields.update(mo_energy=None, mo_occupation=None)
    with QVFReader(trexio_to_qvf(dataset(fields))) as reader:
        from vibeview.renderers.wavefunction import WavefunctionRenderer

        renderer = WavefunctionRenderer(reader.get_section("wavefunction"), reader)
        assert all(
            row["energy_eh"] is None and row["occupation"] is None and "HOMO" not in row["title"]
            for row in renderer.mo_table()
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"basis_type": "Slater"}, "Gaussian basis"),
        ({"basis_shell_ang_mom": [0, 1, 2, 4]}, "s, p, d and f"),
        ({"basis_prim_factor": None}, "basis.prim_factor"),
        ({"basis_exponent": [-0.5] * 8}, "exponents must be positive"),
        ({"basis_shell_index": [4] * 8}, "out-of-range"),
        ({"basis_nucleus_index": [2] * 4}, "out-of-range"),
        ({"ao_shell": [0] * 16}, "needs primitives and"),
        ({"ao_normalization": [float("nan")] * 16}, "finite values"),
        ({"mo_coefficient_im": np.ones((17, 16))}, "mo_coefficient_im"),
        ({"basis_r_power": [0, 1, 0, 0]}, "basis_r_power"),
        ({"mo_spin": [2] * 17}, "mo_spin"),
        ({"pbc_k_point_num": 1, "pbc_k_point": [[0, 0, 0]]}, "k-point orbitals"),
    ],
)
def test_unsupported_or_malformed_wavefunctions_fail_explicitly(dataset, overrides, message):
    fields = _gto_fields()
    fields.update(overrides)
    with pytest.raises(ValueError, match=message):
        trexio_to_qvf(dataset(fields))


def test_missing_geometry_fails_explicitly(dataset):
    with pytest.raises(ValueError, match="nucleus.coord"):
        trexio_to_qvf(dataset({"nucleus_coord": None}))


def test_real_backend_cli_import_and_validation(dataset, tmp_path):
    source = dataset(_gto_fields())
    destination = tmp_path / "imported.qvf"
    result = CliRunner().invoke(main, ["import", str(source), "-o", str(destination)])
    assert result.exit_code == 0, result.output
    assert "wavefunction.gto" in result.output
    validated = CliRunner().invoke(main, ["validate", str(destination)])
    assert validated.exit_code == 0, validated.output


def test_text_dataset_is_one_input_even_in_batch_scan(dataset, tmp_path):
    source = dataset()
    plans = plan_imports((source,))
    assert len(plans) == 1 and plans[0].source == source
    assert plans[0].destination == source.with_suffix(".qvf")
    # A parent directory with --from must still scan datasets, rather than
    # trying to open the batch root as a TREXIO file.
    batch = plan_imports((tmp_path,), output=tmp_path / "out", format_name="trexio")
    assert len(batch) == 1 and batch[0].source == source
    execute_import(batch[0])


def test_gui_directory_and_glob_discover_text_datasets(dataset, tmp_path):
    from vibeview.app import _scan_gui_directory, _scan_gui_glob

    source = dataset()
    for recursive in (False, True):
        assert _scan_gui_directory(tmp_path, recursive=recursive).candidates == (source,)
    assert _scan_gui_glob(str(tmp_path / "calculation*"), recursive=False).candidates == (source,)


def test_hdf5_upload_bytes_and_streams(trexio, tmp_path):
    path = tmp_path / "structure.h5"
    with trexio.File(str(path), "w", trexio.TREXIO_HDF5) as handle:
        trexio.write_nucleus_num(handle, 1)
        trexio.write_nucleus_coord(handle, [[0, 0, 0]])
        trexio.write_nucleus_charge(handle, [2])
    for source in (path.read_bytes(), io.BytesIO(path.read_bytes())):
        with QVFReader(trexio_to_qvf(source)) as reader:
            assert reader.read_structure().atoms[0].symbol == "He"


def test_browser_hdf5_upload(trexio, tmp_path, water_qvf):
    from trame.app import get_server

    from vibeview.app import create_app

    path = tmp_path / "upload.h5"
    with trexio.File(str(path), "w", trexio.TREXIO_HDF5) as handle:
        trexio.write_nucleus_num(handle, 1)
        trexio.write_nucleus_coord(handle, [[0, 0, 0]])
        trexio.write_nucleus_charge(handle, [2])
    with QVFReader(water_qvf) as reader:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.controller.view_update = lambda *args, **kwargs: None
        server.state.load_file_name = path.name
        server.state.load_file_bytes = base64.b64encode(path.read_bytes()).decode()
        server.controller.trigger_fn("load_file_from_bytes")()
        assert "Loaded upload.h5" in server.state.status_message


def test_viewer_opens_dataset_by_server_path(dataset, water_qvf):
    from trame.app import get_server

    from vibeview.app import create_app

    path = dataset()
    with QVFReader(water_qvf) as reader:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.controller.view_update = lambda *args, **kwargs: None
        server.state.open_path = str(path)
        server.controller.open_path()
        assert "Opened 1 file(s)" in server.state.status_message


def test_web_directory_browser_links_to_dataset(dataset, tmp_path):
    import urllib.parse

    from vibeview.serve import _browse_dir, _openable

    path = dataset()
    assert _openable(path)
    listing = _browse_dir(tmp_path)
    assert "/open?file=" + urllib.parse.quote(str(path)) in listing
    assert "/browse?dir=" + urllib.parse.quote(str(path)) + '"' not in listing


def test_cli_open_converts_dataset_before_launching_viewer(dataset, tmp_path, monkeypatch):
    import vibeview.cli as cli

    path = dataset(_gto_fields())
    opened = []

    def create_app(readers):
        opened.extend(readers)

    monkeypatch.setattr(cli, "_load_app", lambda: (create_app, lambda *args, **kwargs: None))
    monkeypatch.setattr(cli, "_abort_if_port_in_use", lambda *args: None)
    monkeypatch.setattr(cli, "_wait_until_bound_and_announce", lambda **kwargs: None)
    try:
        result = CliRunner().invoke(
            main, ["open", str(path), "--no-browser", "--log-file", str(tmp_path / "viewer.log")]
        )
        assert result.exit_code == 0, result.output
        assert len(opened) == 1
        assert opened[0].get_section("wavefunction").kind == "wavefunction.gto"
    finally:
        for reader in opened:
            reader.close()


def test_forced_import_of_hdf5_with_unknown_suffix(trexio, tmp_path):
    path = tmp_path / "orbitals.data"
    with trexio.File(str(path), "w", trexio.TREXIO_HDF5) as handle:
        trexio.write_nucleus_num(handle, 1)
        trexio.write_nucleus_coord(handle, [[0, 0, 0]])
        trexio.write_nucleus_charge(handle, [2])
    with QVFReader(convert_to_qvf(path, format_name="trexio")) as reader:
        assert reader.read_structure().atoms[0].symbol == "He"


def test_invalid_hdf5_reports_an_import_error(trexio, tmp_path):
    path = tmp_path / "broken.h5"
    path.write_bytes(b"not an HDF5 file")
    with pytest.raises(ValueError, match="Could not read TREXIO input"):
        trexio_to_qvf(path)


def test_desktop_routes_text_dataset_to_viewer(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to exercise the Electron file router")
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    for name in ("metadata.txt", "nucleus.txt"):
        (dataset / name).write_text("TREXIO group")
    ordinary = tmp_path / "results"
    ordinary.mkdir()
    source = Path(__file__).parents[1] / "electron" / "main.js"
    script = r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert/strict');
const [source, dataset, ordinary] = process.argv.slice(1);
const code = fs.readFileSync(source, 'utf8');
const urls = [], recent = [];
const context = {
  fs, path, SERVER_URL: 'http://localhost:8080',
  mainWindow: {loadURL: url => urls.push(url)},
  createWindow() {}, createMenu() {}, updateTitle() {},
  addRecentFile: path => recent.push(path),
  dialog: {showErrorBox: (...args) => assert.fail(args.join(' '))},
};
vm.createContext(context);
vm.runInContext(code.slice(code.indexOf('function isTrexioDirectory('),
                          code.indexOf('function updateTitle(')), context);
context.openFile(dataset);
context.openFolder(dataset);
context.openFile(ordinary);
context.openFolder(ordinary);
assert.deepEqual(urls.map(url => new URL(url).pathname), ['/open', '/open', '/browse', '/browse']);
assert.equal(new URL(urls[0]).searchParams.get('file'), dataset);
assert.deepEqual(recent, [dataset, dataset]);
"""
    result = subprocess.run(
        [node, "-e", script, str(source), str(dataset), str(ordinary)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_missing_dependency_is_actionable(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "trexio", None)
    with pytest.raises(ValueError, match=r"Install it with:.*\[trexio\]"):
        trexio_to_qvf(tmp_path / "test.hdf5")
    capability = next(row for row in format_capabilities() if row.format_name == "trexio")
    assert not capability.available and "[trexio]" in capability.install_hint


@pytest.mark.parametrize("filename", ["test.trexio", "test.h5", "test.HDF5"])
def test_format_detection_and_optional_registration(filename):
    assert detect_format(filename) == "trexio"
    from vibeview.importers import RESERVED_FORMAT_NAMES

    assert "trexio" in RESERVED_FORMAT_NAMES
    metadata = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    extras = metadata["project"]["optional-dependencies"]
    assert extras["trexio"] == ["trexio>=2.5"]
    assert "vibeview[trexio]" in extras["test"] and "vibeview[trexio]" in extras["all"]
    result = CliRunner().invoke(main, ["formats", "--json"])
    assert result.exit_code == 0
    row = next(
        row for row in json.loads(result.output)["formats"] if row["format_name"] == "trexio"
    )
    assert "wavefunction.gto" in row["data_kinds"]
