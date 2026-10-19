"""vibe-view#66: three more root causes that reached the CLI last-resort catch.

Each payload passed the reader and failed in a renderer, so the CLI ended
with a tidy ``Error: <Type>: ...`` that was still a failure:

1. ``spectra.*`` with frequency and intensity lists of different lengths:
   ``show`` failed with ``ValueError: zip() argument 2 is shorter`` while
   broadening the envelope.
2. A ``wavefunction.gto`` shell whose exponent and coefficient counts
   differ: ``animate -k orbital`` failed with ``ValueError`` on the first
   shell it evaluated.
3. ``mo_coefficients`` of shape ``[0, n_ao]`` under an ``energies`` list
   that names an orbital: ``animate -k orbital`` counted the energies and
   failed with ``IndexError`` on MO index 0.

Now the reader refuses each as a section error naming the section, member
and shell, and the orbital animation counts orbitals from the coefficient
rows, as every other consumer does.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner

from tests._qvf_builders import build_qvf
from vibeview.cli import main
from vibeview.qvf import QVFError, QVFReader

_ATOMS = [
    {"symbol": "O", "atomic_number": 8, "position": [0.0, 0.0, 0.1173]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, 0.7572, -0.4692]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, -0.7572, -0.4692]},
]
_STRUCTURE = (
    "structure",
    "structure",
    {"structure": ("json", "s.json", json.dumps({"atoms": _ATOMS, "pbc": [False] * 3}).encode())},
)
_S_SHELL = {"center": 0, "l": 0, "exponents": [1.0], "coefficients": [1.0]}


def _spectra_archive(tmp_path: Path, payload: dict, kind: str = "spectra.generic") -> Path:
    spectrum = json.dumps(payload).encode()
    return build_qvf(
        tmp_path / "spec.qvf",
        [_STRUCTURE, ("spec", kind, {"spectrum": ("json", "spec.json", spectrum)})],
    )


def _gto_archive(
    tmp_path: Path,
    *,
    shells: list[dict] | None = None,
    n_ao: int | None = 1,
    coefficients=((1.0,),),
    energies: list[float] | None = None,
    occupations: list[float] | None = None,
    spin: str = "restricted",
    alpha: dict | None = None,
    beta: dict | None = None,
    name: str = "wf.qvf",
) -> Path:
    basis: dict = {"structure_ref": "structure", "pure": True, "shells": shells or [_S_SHELL]}
    if n_ao is not None:
        basis["n_ao"] = n_ao
    meta: dict = {"spin": spin, "orbital_kind": "canonical"}
    if spin == "unrestricted":
        meta["alpha"] = alpha or {"energies": [-0.5], "occupations": [1.0]}
        meta["beta"] = beta or {"energies": [-0.5], "occupations": [1.0]}
    else:
        meta["energies"] = [-0.5] if energies is None else energies
        meta["occupations"] = [2.0] if occupations is None else occupations
    members: dict = {
        "basis": ("json", "wf/basis.json", json.dumps(basis).encode()),
        "mo_metadata": ("json", "wf/mo.json", json.dumps(meta).encode()),
    }
    width = n_ao or 1
    if spin == "unrestricted":
        for label in ("alpha", "beta"):
            rows = np.asarray(coefficients, dtype=np.float64).reshape(-1, width)
            members[f"mo_coefficients_{label}"] = (
                "binary",
                f"wf/{label}.dat",
                rows.tobytes(),
                "float64",
                list(rows.shape),
            )
    else:
        rows = np.asarray(coefficients, dtype=np.float64).reshape(-1, width)
        members["mo_coefficients"] = (
            "binary",
            "wf/mo.dat",
            rows.tobytes(),
            "float64",
            list(rows.shape),
        )
    return build_qvf(tmp_path / name, [_STRUCTURE, ("wf", "wavefunction.gto", members)])


def _run(*args: str):
    return CliRunner().invoke(main, [*args])


def _no_last_resort(result) -> None:
    """The root cause is handled: no ``Error: <ExceptionType>:`` report."""
    for name in ("IndexError", "ValueError", "KeyError", "TypeError"):
        assert f"{name}:" not in result.output, result.output
    assert "Traceback" not in result.output


def _section_error(path: Path, section_id: str, read) -> str:
    """Read ``section_id`` through ``read``; the QVFError text, also recorded."""
    with QVFReader(path) as reader:
        with pytest.raises(QVFError) as excinfo:
            read(reader)
        recorded = reader.section_error(section_id)
    assert recorded == str(excinfo.value)
    assert f"in section {section_id!r}" in recorded
    return recorded


# ── 1. spectra: frequencies and intensities of different lengths ────────────


def test_spectra_length_mismatch_is_a_section_error(tmp_path):
    path = _spectra_archive(
        tmp_path, {"frequencies": [10.0, 1010.0, 2000.0], "intensities": [1.0, 0.5]}
    )
    text = _section_error(path, "spec", lambda r: r.read_spectra("spec"))
    assert text.endswith("frequencies [3] and intensities [2] must have one shared length")
    assert text.startswith("spectrum member in section 'spec': ")


def test_show_reports_the_spectra_section_error(tmp_path):
    path = _spectra_archive(
        tmp_path, {"frequencies": [10.0, 1010.0, 2000.0], "intensities": [1.0, 0.5]}
    )
    result = _run("show", str(path), "--all", "--plain", "--size", "40x12")
    assert result.exit_code == 0, result.output
    _no_last_resort(result)
    assert "zip()" not in result.output
    assert "frequencies [3] and intensities [2] must have one shared length" in result.output


def test_the_html_spectra_renderer_gets_the_section_error(tmp_path):
    """The browser and ``capture_spectra`` paths read through the same reader."""
    from vibeview.capture import capture_spectra

    path = _spectra_archive(
        tmp_path, {"frequencies": [10.0, 1010.0, 2000.0], "intensities": [1.0, 0.5]}
    )
    with QVFReader(path) as reader:
        with pytest.raises(QVFError, match="one shared length"):
            capture_spectra(reader, tmp_path / "spec.html", "spec")
        assert "one shared length" in (reader.section_error("spec") or "")
    assert not (tmp_path / "spec.html").exists()


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"frequencies": 10.0, "intensities": [1.0]}, "frequencies must be a flat list of numbers"),
        ({"frequencies": [[10.0]], "intensities": [[1.0]]}, "frequencies must be a flat list"),
        (
            {"frequencies": [10.0, float("inf")], "intensities": [1.0, 1.0]},
            "frequencies must be finite",
        ),
        ({"frequencies": [10.0], "intensities": [float("nan")]}, "intensities must be finite"),
        ({"frequencies": [10.0]}, "malformed payload (KeyError: 'intensities')"),
        ({"frequencies": [10.0], "intensities": ["x"]}, "malformed payload (ValueError"),
    ],
)
def test_other_malformed_spectra_are_section_errors(tmp_path, payload, expected):
    path = _spectra_archive(tmp_path, payload)
    text = _section_error(path, "spec", lambda r: r.read_spectra("spec"))
    assert expected in text


def test_spectra_payload_must_be_an_object(tmp_path):
    spectrum = json.dumps([10.0, 1.0]).encode()
    path = build_qvf(
        tmp_path / "spec.qvf",
        [_STRUCTURE, ("spec", "spectra.ir", {"spectrum": ("json", "spec.json", spectrum)})],
    )
    text = _section_error(path, "spec", lambda r: r.read_spectra("spec"))
    assert "payload is not a JSON object" in text


@pytest.mark.parametrize(
    "payload",
    [
        {"frequencies": [10.0, 1010.0], "intensities": [1.0, 0.5]},
        {"frequencies": [], "intensities": []},
    ],
)
def test_matching_spectra_still_read_and_show(tmp_path, payload):
    path = _spectra_archive(tmp_path, payload)
    with QVFReader(path) as reader:
        data = reader.read_spectra("spec")
        assert data.frequencies.tolist() == payload["frequencies"]
        assert data.intensities.tolist() == payload["intensities"]
        assert reader.section_error("spec") is None
    result = _run("show", str(path), "--all", "--plain", "--size", "40x12")
    assert result.exit_code == 0, result.output
    _no_last_resort(result)
    assert "spectra.generic" in result.output


# ── 2. a shell whose exponent and coefficient counts differ ─────────────────


def test_shell_count_mismatch_is_a_section_error(tmp_path):
    shell = {"center": 0, "l": 0, "exponents": [1.0, 0.5], "coefficients": [1.0]}
    path = _gto_archive(tmp_path, shells=[shell])
    text = _section_error(path, "wf", lambda r: r.read_wavefunction_gto("wf"))
    assert text.endswith("shell 0 (l=0, center=0): 2 exponents but 1 contraction coefficients")
    assert text.startswith("basis/mo_metadata member in section 'wf': ")


def test_animate_reports_the_shell_section_error(tmp_path):
    shell = {"center": 0, "l": 0, "exponents": [1.0, 0.5], "coefficients": [1.0]}
    path = _gto_archive(tmp_path, shells=[shell])
    result = _run(
        "animate", str(path), "-k", "orbital", "-f", "frames", "-o", str(tmp_path / "anim")
    )
    assert result.exit_code == 1
    _no_last_resort(result)
    assert "Error: basis/mo_metadata member in section 'wf': shell 0" in result.output
    assert "2 exponents but 1 contraction coefficients" in result.output


@pytest.mark.parametrize(
    "shell, expected",
    [
        (
            {"center": 0, "l": 0, "exponents": [], "coefficients": []},
            "shell 0 (l=0, center=0): needs at least one primitive",
        ),
        (
            {"center": 0, "l": 0, "exponents": [[1.0]], "coefficients": [[1.0]]},
            "must be flat lists of numbers",
        ),
        (
            {"center": 0, "l": 0, "exponents": [0.0], "coefficients": [1.0]},
            "exponents must be positive finite numbers",
        ),
        (
            {"center": 0, "l": 0, "exponents": [-1.0], "coefficients": [1.0]},
            "exponents must be positive finite numbers",
        ),
        (
            {"center": 0, "l": 0, "exponents": [1e400], "coefficients": [1.0]},
            "exponents must be positive finite numbers",
        ),
        (
            {"center": 0, "l": 0, "exponents": [1.0], "coefficients": [1e400]},
            "contraction coefficients must be finite",
        ),
        (
            {"center": -1, "l": 0, "exponents": [1.0], "coefficients": [1.0]},
            "center must be a non-negative atom index",
        ),
        (
            {"center": 0, "l": -1, "exponents": [1.0], "coefficients": [1.0]},
            "l must be non-negative",
        ),
        (
            {"center": 0.5, "l": 0, "exponents": [1.0], "coefficients": [1.0]},
            "shells[0].center must be an integer",
        ),
        (
            {"center": 0, "l": True, "exponents": [1.0], "coefficients": [1.0]},
            "shells[0].l must be an integer",
        ),
        ({"center": 0, "l": 0, "coefficients": [1.0]}, "malformed payload (KeyError: 'exponents')"),
    ],
)
def test_other_malformed_shells_are_section_errors(tmp_path, shell, expected):
    path = _gto_archive(tmp_path, shells=[shell])
    text = _section_error(path, "wf", lambda r: r.read_wavefunction_gto("wf"))
    assert expected in text


def test_shells_wider_than_n_ao_are_a_section_error(tmp_path):
    p_shell = {"center": 0, "l": 1, "exponents": [1.0], "coefficients": [1.0]}
    path = _gto_archive(tmp_path, shells=[_S_SHELL, p_shell], n_ao=1)
    text = _section_error(path, "wf", lambda r: r.read_wavefunction_gto("wf"))
    assert text.endswith("basis shells span 4 AOs but n_ao is 1")


def test_shells_wider_than_the_coefficients_are_a_section_error(tmp_path):
    cartesian_d = {"center": 0, "l": 2, "exponents": [1.0], "coefficients": [1.0], "pure": False}
    path = _gto_archive(tmp_path, shells=[cartesian_d], n_ao=None, coefficients=[[1.0]])
    text = _section_error(path, "wf", lambda r: r.read_wavefunction_gto("wf"))
    assert text.endswith("basis shells span 6 AOs but the MO coefficients have width 1")


def test_a_shell_list_that_is_not_a_list_of_objects_is_a_section_error(tmp_path):
    path = _gto_archive(tmp_path, shells=[[0, 0, [1.0], [1.0]]])
    text = _section_error(path, "wf", lambda r: r.read_wavefunction_gto("wf"))
    assert "payload is not a JSON object" in text


# ── 3. mo_coefficients with no rows under an energies list ──────────────────


def test_energies_longer_than_the_coefficient_rows_are_a_section_error(tmp_path):
    path = _gto_archive(tmp_path, coefficients=np.zeros((0, 1)), energies=[-0.5], occupations=[])
    text = _section_error(path, "wf", lambda r: r.read_wavefunction_gto("wf"))
    assert text.endswith("energies lists 1 orbitals but mo_coefficients has 0 rows")


def test_animate_reports_the_empty_coefficients_section_error(tmp_path):
    path = _gto_archive(tmp_path, coefficients=np.zeros((0, 1)), energies=[-0.5], occupations=[2.0])
    result = _run(
        "animate", str(path), "-k", "orbital", "-f", "frames", "-o", str(tmp_path / "anim")
    )
    assert result.exit_code == 1
    _no_last_resort(result)
    assert "energies lists 1 orbitals but mo_coefficients has 0 rows" in result.output


def test_occupations_longer_than_the_coefficient_rows_are_a_section_error(tmp_path):
    path = _gto_archive(tmp_path, coefficients=[[1.0]], energies=[-0.5], occupations=[2.0, 0.0])
    text = _section_error(path, "wf", lambda r: r.read_wavefunction_gto("wf"))
    assert text.endswith("occupations lists 2 orbitals but mo_coefficients has 1 rows")


def test_unrestricted_metadata_is_checked_per_spin(tmp_path):
    path = _gto_archive(
        tmp_path,
        spin="unrestricted",
        coefficients=[[1.0]],
        beta={"energies": [-0.5, -0.4], "occupations": [1.0]},
    )
    text = _section_error(path, "wf", lambda r: r.read_wavefunction_gto("wf"))
    assert text.endswith("beta.energies lists 2 orbitals but mo_coefficients_beta has 1 rows")


def test_a_shorter_energies_prefix_still_reads_and_animates_every_row(tmp_path):
    path = _gto_archive(tmp_path, coefficients=[[1.0], [0.5]], energies=[-0.5], occupations=[2.0])
    with QVFReader(path) as reader:
        wf = reader.read_wavefunction_gto("wf")
        assert wf.mo_coefficients is not None and wf.mo_coefficients.shape == (2, 1)
        assert reader.section_error("wf") is None
    out = tmp_path / "anim"
    result = _run("animate", str(path), "-k", "orbital", "-f", "frames", "-o", str(out))
    assert result.exit_code == 0, result.output
    _no_last_resort(result)
    # Two rows, so two frames: the animation counts orbitals as the renderer does.
    assert "Animation: 2 frames" in result.output


def test_a_well_formed_wavefunction_still_animates(tmp_path):
    path = _gto_archive(tmp_path)
    out = tmp_path / "anim"
    result = _run("animate", str(path), "-k", "orbital", "-f", "frames", "-o", str(out))
    assert result.exit_code == 0, result.output
    _no_last_resort(result)
    assert "Animation: 1 frames" in result.output
