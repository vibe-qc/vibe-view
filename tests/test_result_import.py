"""Result mappings, missing dependency, native precedence and real parser corpus."""
import builtins
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from vibeview.converters import convert_to_qvf, detect_format
from vibeview.qvf import QVFReader
from vibeview.result_import import parsed_to_qvf, result_to_qvf
from vibeview.units import HARTREE_TO_EV


def data(**kwargs):
    values = dict(atomcoords=np.array([[[0., 0., 0.]], [[0., 0., 1.]]]),
                  atomnos=[1], metadata={"package": "Example"}, charge=0, mult=2,
                  scfenergies=[-HARTREE_TO_EV, -2 * HARTREE_TO_EV])
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_trajectory_units_and_properties():
    parsed = data(vibfreqs=[100.], vibdisps=[[[0., 0., 1.]]], vibirs=[3.],
                  vibramans=[4.], etenergies=[8065.543937], etoscs=[.2],
                  atomcharges={"mulliken": [.1]}, moenergies=[[-2., 3.]], homos=[0],
                  enthalpy=-1., temperature=298.15, entropy=1e-4,
                  scfvalues=[[[.1, .2], [.01, .02]]])
    with QVFReader(parsed_to_qvf(parsed)) as reader:
        trajectory = reader.read_trajectory("result_trajectory")
        np.testing.assert_allclose(trajectory.energies, [-1., -2.])
        np.testing.assert_array_equal(trajectory.coords[:, 0, 2], [0, 1])
        assert reader.thermochemistry["enthalpy_eh"] == -1
        assert reader.thermochemistry["entropy_cal_mol_k"] == pytest.approx(62.7509474)
        iterations = reader.read_scf_history("result_scf").iterations
        assert iterations[1]["convergence_values"] == [.01, .02]
        assert reader.read_vibrations("result_vibrations").frequencies == [100.]


def test_energy_count_mismatch_not_attached_to_wrong_frames():
    with QVFReader(parsed_to_qvf(data(scfenergies=[-1.]))) as reader:
        assert reader.read_trajectory("result_trajectory").energies is None


def test_one_dimensional_scan():
    parsed = data(scanparm=[[1., 2.]], scanenergies=[-HARTREE_TO_EV, 0.],
                  scancoords=[[[0, 0, 0]], [[0, 0, 1]]], scannames=["bond"])
    with QVFReader(parsed_to_qvf(parsed)) as reader:
        path = reader.read_reaction_path("result_scan")
        assert path.reaction_coordinate == [1., 2.]
        assert path.energies == [-1., 0.]


def test_two_dimensional_scan_reorders_grid():
    parsed = data(scanparm=[[2., 1., 2., 1.], [4., 4., 3., 3.]],
                  scanenergies=np.array([24, 14, 23, 13])*HARTREE_TO_EV,
                  scancoords=np.zeros((4, 1, 3)), scannames=["a", "b"])
    with QVFReader(parsed_to_qvf(parsed)) as reader:
        surface = reader.read_scan_surface("result_scan")
        np.testing.assert_allclose(surface.energies, [[13, 14], [23, 24]])


def test_missing_optional_dependency(monkeypatch, tmp_path):
    original = builtins.__import__
    def blocked(name, *args, **kwargs):
        if name.startswith("cclib"):
            raise ImportError("absent")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(ValueError, match="cclib"):
        result_to_qvf(tmp_path / "output.log")


@pytest.mark.parametrize("suffix", [".out", ".log"])
def test_orca_precedes_generic(tmp_path, suffix):
    path = tmp_path / ("calculation" + suffix)
    path.write_text("* O   R   C   A *\n")
    assert detect_format(path) == "orca"


REFERENCES = json.loads((Path(__file__).parent / "data/cclib/references.json").read_text())


@pytest.mark.parametrize("reference", REFERENCES, ids=[r["file"] for r in REFERENCES])
def test_recorded_real_program_output(reference):
    root = os.environ.get("VIBEVIEW_CCLIB_CORPUS")
    if root is None:
        pytest.skip("Set VIBEVIEW_CCLIB_CORPUS to the hash-verified cclib corpus")
    import hashlib
    path = Path(root) / reference["file"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == reference["sha256"]
    with QVFReader(convert_to_qvf(path)) as reader:
        payload = reader.manifest.model_extra["provenance"]
        assert payload["scf_energies_hartree"][-1] == pytest.approx(
            reference["final_scf_ev"] / HARTREE_TO_EV, abs=1e-12)
        np.testing.assert_allclose(payload["vibrational_frequencies_cm-1"],
                                   reference["frequencies"], rtol=0, atol=1e-9)
        trajectories = [s for s in reader.sections if s.kind == "trajectory"]
        assert (len(reader.read_trajectory(trajectories[0].id).coords)
                if trajectories else 1) == reference["nframes"]


def test_incomplete_scan_grid_rejected():
    parsed = data(scanparm=[[1, 2, 3], [1, 2, 3]], scanenergies=[1, 2, 3],
                  scancoords=np.zeros((3, 1, 3)))
    with pytest.raises(ValueError, match="rectangular"):
        parsed_to_qvf(parsed)


def test_nonfinite_geometry_rejected():
    with pytest.raises(ValueError, match="Non-finite"):
        parsed_to_qvf(data(atomcoords=[[[float("nan"), 0, 0]]]))


XTB_OPT_LOG = """3
 energy: -5.070544440612 gnorm: 0.020515186283 xtb: 6.6.1 (8d0f1dd)
O     0.00000000    0.00000000   -0.38917000
H     0.76300000    0.00000000    0.19458500
H    -0.76300000    0.00000000    0.19458500
3
 energy: -5.070579108810 gnorm: 0.000312849217 xtb: 6.6.1 (8d0f1dd)
O     0.00000000    0.00000000   -0.39100000
H     0.77000000    0.00000000    0.19600000
H    -0.77000000    0.00000000    0.19600000
"""


@pytest.mark.parametrize("name", ["xtbopt.log", "xtbscan.log", "frames.out"])
def test_xtb_log_trajectory_opens_without_cclib(monkeypatch, tmp_path, name):
    """xtb's XYZ-frame logs open as trajectories with their energies (#146)."""
    original = builtins.__import__
    def blocked(module, *args, **kwargs):
        if module.startswith("cclib"):
            raise ImportError("absent")
        return original(module, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", blocked)
    path = tmp_path / name
    path.write_text(XTB_OPT_LOG)
    assert detect_format(path) == "results"
    with QVFReader(convert_to_qvf(path)) as reader:
        trajectory = reader.read_trajectory("xyz_trajectory")
        np.testing.assert_allclose(trajectory.energies, [-5.070544440612, -5.070579108810])
        assert trajectory.coords[-1, 1, 0] == pytest.approx(0.77)


def test_xyz_comment_energies_stay_explicit():
    """Only xtb's exact layout is read; other free text is not an energy."""
    from vibeview.xyz_trajectory import xyz_frames

    other = XTB_OPT_LOG.replace(" gnorm:", " grad:")
    with QVFReader(xyz_frames(other)) as reader:
        assert reader.read_trajectory("xyz_trajectory").energies is None


def test_non_xyz_log_still_goes_to_cclib(monkeypatch, tmp_path):
    original = builtins.__import__
    def blocked(module, *args, **kwargs):
        if module.startswith("cclib"):
            raise ImportError("absent")
        return original(module, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", blocked)
    path = tmp_path / "gaussian.log"
    path.write_text(" Entering Gaussian System, Link 0=g16\n 3\n O 0 0 0\n")
    with pytest.raises(ValueError, match="cclib"):
        result_to_qvf(path)
