"""vibe-view#63: root causes that reached the CLI's last-resort catch.

The #51 catch turned each of these into a tidy ``Error: <Type>: ...``, but the
failure itself was still there. Each is now fixed where it happens:

* C1: ``stats`` aborted on a dangling ``*.qvf`` symlink (FileNotFoundError).
* C2: ``table --kind atom_properties`` raised IndexError on charge arrays of
  different lengths; the reader now refuses them.
* C3: ``capture -s <bands>`` raised a matplotlib ValueError on +-inf or
  +-DBL_MAX eigenvalues; the reader now refuses them.
* C4: ``supercell`` raised a JSON ValueError on a -DBL_MAX lattice with pbc
  false on every axis.
* ``info`` passed OSC, BEL and CSI sequences from archive strings to the
  terminal.
* Nits: ``validate`` said OK for a member whose declared shape does not
  match its bytes, and a 200 kB exception message was printed in full.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner

from tests._qvf_builders import build_qvf, vibe_qc_source
from vibeview.cli import main
from vibeview.qvf import QVFError, QVFReader

_DBL_MAX = float(np.finfo(float).max)


def _structure(pbc=(False, False, False), lattice=None, atoms=None) -> bytes:
    payload: dict = {
        "atoms": atoms
        or [
            {"symbol": "O", "atomic_number": 8, "position": [0.0, 0.0, 0.1173]},
            {"symbol": "H", "atomic_number": 1, "position": [0.0, 0.7572, -0.4692]},
            {"symbol": "H", "atomic_number": 1, "position": [0.0, -0.7572, -0.4692]},
        ],
        "pbc": list(pbc),
    }
    if lattice is not None:
        payload["lattice_vectors"] = lattice
    return json.dumps(payload).encode()


def _archive(tmp_path: Path, sections, name="a.qvf", source=None) -> Path:
    return build_qvf(tmp_path / name, sections, source=source)


def _run(*args: str):
    return CliRunner().invoke(main, [*args])


def _no_last_resort(result) -> None:
    """The root cause is handled: no ``Error: <ExceptionType>:`` report."""
    for name in ("FileNotFoundError", "IndexError", "ValueError", "LinAlgError"):
        assert f"{name}:" not in result.output, result.output
    assert "Traceback" not in result.output


# ── C1: stats and a dangling symlink ────────────────────────────────────────


def test_stats_skips_a_dangling_symlink(tmp_path):
    _archive(
        tmp_path, [("structure", "structure", {"structure": ("json", "s.json", _structure())})]
    )
    (tmp_path / "broken.qvf").symlink_to(tmp_path / "gone.qvf")

    result = _run("stats", str(tmp_path))
    assert result.exit_code == 0, result.output
    _no_last_resort(result)
    assert "Files:      2" in result.output
    assert "Unreadable: 1 (broken.qvf)" in result.output
    assert "Converged:     0/1" in result.output


# ── C2: charge arrays of different lengths ──────────────────────────────────


def _charges_archive(tmp_path, mulliken, loewdin):
    m = np.asarray(mulliken, dtype="<f8").tobytes()
    lo = np.asarray(loewdin, dtype="<f8").tobytes()
    return _archive(
        tmp_path,
        [
            ("structure", "structure", {"structure": ("json", "s.json", _structure())}),
            (
                "props",
                "atom_properties",
                {
                    "mulliken_charge": ("binary", "p/m.bin", m, "float64", [len(mulliken)]),
                    "loewdin_charge": ("binary", "p/l.bin", lo, "float64", [len(loewdin)]),
                },
            ),
        ],
    )


def test_mismatched_charge_arrays_are_a_section_error(tmp_path):
    path = _charges_archive(tmp_path, [-0.8, 0.4, 0.4], [-0.6, 0.3])
    with QVFReader(path) as reader:
        with pytest.raises(QVFError, match="one shared length"):
            reader.read_atom_properties("props")
        assert "one shared length" in (reader.section_error("props") or "")

    result = _run("table", str(path), "--kind", "atom_properties")
    assert result.exit_code == 1
    _no_last_resort(result)
    assert "mulliken_charge [3], loewdin_charge [2]" in result.output


def test_matching_charge_arrays_still_tabulate(tmp_path):
    path = _charges_archive(tmp_path, [-0.8, 0.4, 0.4], [-0.6, 0.3, 0.3])
    result = _run("table", str(path), "--kind", "atom_properties")
    assert result.exit_code == 0, result.output
    assert "-0.8" in result.output and "-0.6" in result.output


# ── C3: bands with non-finite or overflowing eigenvalues ────────────────────


def _bands_archive(tmp_path, eigenvalues, fermi=0.1):
    eig = np.asarray(eigenvalues, dtype="<f8")
    kpath = {"n_kpoints": eig.shape[1], "n_bands": eig.shape[2], "n_spin": 1, "segments": []}
    if fermi is not None:
        kpath["fermi"] = fermi
    return _archive(
        tmp_path,
        [
            (
                "bands0",
                "bands",
                {
                    "kpath": ("json", "b/k.json", json.dumps(kpath).encode()),
                    "eigenvalues": ("binary", "b/e.bin", eig.tobytes(), "float64", list(eig.shape)),
                },
            )
        ],
    )


def _eig(bad=None):
    eig = np.linspace(-5.0, 5.0, 20).reshape(1, 10, 2)
    return eig if bad is None else bad(eig)


_BAD_BANDS = {
    "+inf": lambda e: np.where(e > 0, np.inf, e),
    "-inf": lambda e: np.where(e == e.min(), -np.inf, e),
    "nan": lambda e: np.where(e == e.max(), np.nan, e),
    "+-DBL_MAX": lambda e: np.where(e > 0, _DBL_MAX, -_DBL_MAX),
}


@pytest.mark.parametrize("fermi", [None, 0.1])
@pytest.mark.parametrize("case", list(_BAD_BANDS))
def test_unplottable_eigenvalues_are_refused(tmp_path, case, fermi):
    path = _bands_archive(tmp_path, _eig(_BAD_BANDS[case]), fermi=fermi)
    with QVFReader(path) as reader, pytest.raises(QVFError, match="eigenvalues must be finite"):
        reader.read_bands("bands0")

    result = _run("capture", str(path), "-s", "bands0", "-o", str(tmp_path / "b.png"))
    assert result.exit_code == 1
    _no_last_resort(result)
    assert "eigenvalues must be finite" in result.output


@pytest.mark.parametrize("fermi", [float("inf"), "high", True])
def test_a_fermi_level_must_be_a_finite_number(tmp_path, fermi):
    path = _bands_archive(tmp_path, _eig(), fermi=fermi)
    with QVFReader(path) as reader, pytest.raises(QVFError, match="fermi must be a finite"):
        reader.read_bands("bands0")


def test_ordinary_bands_still_capture(tmp_path):
    path = _bands_archive(tmp_path, _eig())
    result = _run("capture", str(path), "-s", "bands0", "-o", str(tmp_path / "b.png"))
    assert result.exit_code == 0, result.output
    assert (tmp_path / "b.png").stat().st_size > 0


# ── C4: supercell ───────────────────────────────────────────────────────────


def _cell_archive(tmp_path, pbc, lattice, name="c.qvf"):
    atoms = [{"symbol": "C", "atomic_number": 6, "position": [0.0, 0.0, 0.0]}]
    payload = _structure(pbc, lattice, atoms)
    return _archive(
        tmp_path,
        [("structure", "structure", {"structure": ("json", "s.json", payload)})],
        name=name,
    )


def test_supercell_refuses_a_lattice_without_a_periodic_axis(tmp_path):
    path = _cell_archive(tmp_path, (False,) * 3, [[-_DBL_MAX, 0, 0], [0, 1, 0], [0, 0, 1]])
    result = _run("supercell", str(path), "--nx", "2")
    assert result.exit_code == 1
    _no_last_resort(result)
    assert "Not a periodic system" in result.output


def test_supercell_refuses_to_replicate_along_vacuum(tmp_path):
    lattice = [[2.5, 0, 0], [0, 2.5, 0], [0, 0, 20.0]]
    path = _cell_archive(tmp_path, (True, True, False), lattice)
    result = _run("supercell", str(path), "--nz", "2")
    assert result.exit_code == 1
    assert "non-periodic axis c" in result.output
    # Replicating along the periodic axes still works.
    ok = _run("supercell", str(path), "--nx", "2", "--ny", "2", "-o", str(tmp_path / "s.qvf"))
    assert ok.exit_code == 0, ok.output
    with QVFReader(tmp_path / "s.qvf") as reader:
        assert len(reader.read_structure().atoms) == 4


def test_supercell_refuses_an_overflowing_request_before_building(tmp_path):
    # A cell the reader accepts (volume 1e300), scaled past double range.
    path = _cell_archive(tmp_path, (True,) * 3, [[1e100, 0, 0], [0, 1e100, 0], [0, 0, 1e100]])
    result = _run("supercell", str(path), "--nx", str(10**250))
    assert result.exit_code == 1
    _no_last_resort(result)
    assert "overflows" in result.output


def test_supercell_rejects_a_zero_factor(tmp_path):
    path = _cell_archive(tmp_path, (True,) * 3, [[2.5, 0, 0], [0, 2.5, 0], [0, 0, 2.5]])
    result = _run("supercell", str(path), "--nx", "0")
    assert result.exit_code == 2  # a usage error, not an empty structure
    assert "--nx" in result.output


# ── info and terminal escapes ───────────────────────────────────────────────


_ESCAPES = "\x1b]0;owned\x07\x1b[2J\x9b31m"


def test_info_shows_control_characters_instead_of_sending_them(tmp_path):
    path = _archive(
        tmp_path,
        # Section ids are schema-limited to [A-Za-z0-9_.-]; producer strings
        # such as the calculation name are not.
        [("structure", "structure", {"structure": ("json", "s.json", _structure())})],
        source=vibe_qc_source("0", f"calc{_ESCAPES}"),
    )
    for args in ([], ["--short"]):
        result = _run("info", str(path), *args)
        assert result.exit_code == 0, result.output
        for ch in ("\x1b", "\x07", "\x9b"):
            assert ch not in result.output
        assert "calc\\x1b]0;owned\\x07\\x1b[2J\\x9b31m" in result.output

    # JSON keeps the real string; JSON escaping already makes it inert.
    data = json.loads(_run("info", str(path), "--json").output)
    assert data["calculation"] == f"calc{_ESCAPES}"


def test_terminal_safe_keeps_ordinary_text():
    from vibeview.cli_common import terminal_safe

    assert terminal_safe("H₂O\tÅ — ok") == "H₂O\tÅ — ok"
    assert terminal_safe("a\nb\x00\x7f") == "a\\x0ab\\x00\\x7f"


# ── nits ────────────────────────────────────────────────────────────────────


def test_validate_checks_a_declared_shape_against_the_bytes(tmp_path):
    three = np.array([-0.8, 0.4, 0.4], dtype="<f8").tobytes()
    path = _archive(
        tmp_path,
        [
            ("structure", "structure", {"structure": ("json", "s.json", _structure())}),
            (
                "props",
                "atom_properties",
                {"mulliken_charge": ("binary", "p/m.bin", three, "float64", [10**12])},
            ),
        ],
    )
    result = _run("validate", str(path))
    assert result.exit_code == 1
    assert "8,000,000,000,000 bytes" in result.output and "holds 24" in result.output

    from vibeview.api import validate

    report = validate(path)
    assert report["valid"] is False
    assert (report["section"], report["member"]) == ("props", "mulliken_charge")


def test_validate_leaves_opaque_binary_members_alone(tmp_path):
    """A binary member without dtype or shape is raw bytes of any length,
    such as the 11-byte run.record log in the slice fixture."""
    from tests.test_slice_metadata import archive
    from vibeview.api import validate

    raw, _manifest = archive()
    path = tmp_path / "record.qvf"
    path.write_bytes(raw)
    assert validate(path)["valid"] is True


def test_validate_still_passes_a_consistent_archive(tmp_path):
    path = _charges_archive(tmp_path, [-0.8, 0.4, 0.4], [-0.6, 0.3, 0.3])
    from vibeview.api import validate

    assert validate(path)["valid"] is True


def test_a_huge_error_message_is_clipped(monkeypatch):
    from vibeview import cli

    @cli.main.command("boom-63")
    def boom() -> None:
        raise RuntimeError("x" * 200_000)

    try:
        result = _run("boom-63")
    finally:
        cli.main.commands.pop("boom-63", None)
    assert result.exit_code == 1
    assert len(result.output) < 3000
    # 200,000 x's plus "RuntimeError: ", less the 2000 shown.
    assert "198,014 more characters" in result.output
    assert "--debug" in result.output
