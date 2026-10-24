"""Lossless ORCA cube integration and optional executable boundaries."""

from pathlib import Path

import numpy as np
import pytest

from vibeview.orca_density import attach_cube, available_densities, executable, generate_cube
from vibeview.qvf import QVFReader
from vibeview.qvf_writer import structure_archive


def cube(x=0):
    return (
        f"ORCA density\nElectron density\n1 0 0 0\n2 1 0 0\n2 0 1 0\n2 0 0 1\n"
        f"1 1 {x} 0 0\n0.123456789123 1 2 3 4 5 6 7\n"
    ).encode()


def reader():
    builder = structure_archive(
        [dict(symbol="H", atomic_number=1, position=[0.0, 0.0, 0.0])], [False, False, False]
    )
    builder.add_json(
        "excited",
        "excited_states",
        "states",
        {
            "states": [{"index": 1, "excitation_energy": 2.0, "excitation_energy_units": "eV"}],
        },
    )
    return QVFReader(builder.to_bytesio())


def test_absent_orca_has_actionable_hint(monkeypatch):
    monkeypatch.setattr("vibeview.orca_density.shutil.which", lambda _: None)
    with pytest.raises(ValueError, match="ORCA_PLOT"):
        executable()


def test_missing_container_is_explicit(monkeypatch, tmp_path):
    monkeypatch.setattr("vibeview.orca_density.executable", lambda: "orca_plot")
    with pytest.raises(ValueError, match=".densities"):
        available_densities(tmp_path / "water.gbw")


def test_cube_values_geometry_and_excited_state_link_survive_save(tmp_path):
    with reader() as original:
        archive, matched, sid = attach_cube(original, cube(), kind="transition", state_index=1)
        assert matched
        saved = tmp_path / "result.qvf"
        saved.write_bytes(archive.getvalue())
    with QVFReader(saved) as result:
        values = result.read_volume_data(sid)
        assert values.dtype == np.float64
        np.testing.assert_array_equal(values.flatten(), [0.123456789123, 1, 2, 3, 4, 5, 6, 7])
        assert result.get_section(sid).kind == "volume.difference"
        assert result._read_json_member("excited", "states")["states"][0]["density_ref"] == sid


def test_mismatch_opens_separate_archive_without_contaminating_calculation():
    with reader() as original:
        archive, matched, message = attach_cube(original, cube(1))
        assert not matched
        assert "opened separately" in message
        assert len(original.sections) == 2
    with QVFReader(archive) as result:
        assert [s.kind for s in result.sections] == ["structure", "volume.density"]


@pytest.mark.parametrize("resolution,margin", [(1, 3.0), (300, 3.0), (20, 0), (20, float("nan"))])
def test_invalid_grid_rejected_before_process(monkeypatch, resolution, margin):
    monkeypatch.setattr("vibeview.orca_density.executable", lambda: "orca_plot")
    with pytest.raises(ValueError, match="grid points"):
        generate_cube(
            Path("none.gbw"), "none.scfp", np.zeros((1, 3)), resolution=resolution, margin=margin
        )


def test_plot_uses_private_copies_and_removes_temporary_directory(monkeypatch, tmp_path):
    gbw = tmp_path / "water.gbw"
    gbw.write_bytes(b"gbw")
    gbw.with_suffix(".densities").write_bytes(b"density-container")
    gbw.with_suffix(".densitiesinfo").write_bytes(b"density-index")
    monkeypatch.setattr("vibeview.orca_density.executable", lambda: "orca_plot")
    monkeypatch.setattr("vibeview.orca_density.available_densities", lambda _: ["water.scfp"])
    directories = []

    def run(tool, directory, arguments):
        directories.append(directory)
        assert (directory / "water.gbw").read_bytes() == b"gbw"
        request = (directory / "request.plot").read_text().splitlines()
        assert request[:6] == ["2", "7", "0 0", "0", "water.scfp", "result.cube"]
        assert request[11] == "20 20 20"
        assert float(request[12].split()[0]) == pytest.approx(-2 / 0.529177210903)
        (directory / "water.densities").write_bytes(b"ORCA incidental mutation")
        (directory / "result.cube").write_bytes(cube())
        return ""

    monkeypatch.setattr("vibeview.orca_density._run", run)
    assert generate_cube(gbw, "water.scfp", np.zeros((1, 3)), resolution=20, margin=2) == cube()
    assert gbw.with_suffix(".densities").read_bytes() == b"density-container"
    assert all(not directory.exists() for directory in directories)


def test_controller_generates_attaches_and_downloads_saved_archive(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from vibeview.app.files import FilesControllers

    state = SimpleNamespace(
        orca_plot_running=False,
        orca_plot_gbw="water.gbw",
        orca_plot_density="water.scfp",
        orca_plot_kind="density",
        orca_plot_resolution=20,
        orca_plot_margin=2.0,
        orca_plot_orbital=0,
        orca_plot_state=1,
        orca_plot_vector=1,
        orca_plot_subtract="",
        flush=Mock(),
    )
    download = Mock()
    with reader() as original:
        viewer = SimpleNamespace(
            reader=original,
            server=SimpleNamespace(state=state),
            exports=SimpleNamespace(_offer_download=download),
        )
        controller = FilesControllers(viewer)
        monkeypatch.setattr(
            controller, "_adopt_reader", lambda result: setattr(viewer, "reader", result)
        )
        monkeypatch.setattr("vibeview.orca_density.generate_cube", lambda *args, **kwargs: cube())
        controller.orca_density_generate()
        assert state.status_message == "Density added to calculation"
        assert not state.orca_plot_running
        assert len(viewer.reader.sections) == 3
        controller.orca_density_save()
        import io

        with QVFReader(io.BytesIO(download.call_args.args[0])) as saved:
            assert saved.read_volume_data(state.selected_section).flat[0] == 0.123456789123
        viewer.reader.close()


def test_invalid_external_menu_output_is_bounded(tmp_path):
    import sys

    from vibeview.orca_density import _run

    script = "import sys,time;sys.stdout.write('x'*2100000);sys.stdout.flush();time.sleep(10)"
    with pytest.raises(ValueError, match="excessive diagnostics"):
        _run(sys.executable, tmp_path, ["-c", script])


def test_stored_transition_matrix_uses_native_requested_margin(monkeypatch, tmp_path):
    gbw = tmp_path / "water.gbw"
    gbw.write_bytes(b"gbw")
    density = "Tdens-RPA-0-0-0-1"
    monkeypatch.setattr("vibeview.orca_density.executable", lambda: "orca_plot")
    monkeypatch.setattr("vibeview.orca_density.available_densities", lambda _: [density])

    def run(tool, directory, arguments):
        request = (directory / "request.plot").read_text().splitlines()
        assert request[0] == "2"
        assert request[4] == density
        assert request[11] == "20 20 20"
        assert float(request[12].split()[0]) == pytest.approx(-5.25 / 0.529177210903)
        (directory / "result.cube").write_bytes(cube())
        return ""

    monkeypatch.setattr("vibeview.orca_density._run", run)
    assert (
        generate_cube(gbw, density, np.zeros((1, 3)), resolution=20, margin=5.25, kind="transition")
        == cube()
    )
