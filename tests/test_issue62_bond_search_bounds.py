"""vibe-view#62: the bond search has bounded work on archives the reader accepts.

The reproducers from the issue, each of which used to hang or allocate
gigabytes with no message:

* R1: an MgO 6x6x6 supercell (1728 atoms) written with the unit-cell
  lattice; 1.21 million "bonds" in 962 MB.
* R2: 1500 atoms spread over 30 Å in a 2.9 Å cell; 1.26 million bonds, 1.8 GB.
* R3: 4000 atoms within 0.5 Å; past 3 GB within 4 s.
* R4: a 1,000,000-character section id; ``show --info`` did not finish.

Each case now ends quickly, under a memory guard, with one clear error. The
reader reports it as a section error when the structure is read, before
anything tries to draw it.
"""

from __future__ import annotations

import json
import time
import tracemalloc

import numpy as np
import pytest
from click.testing import CliRunner

from tests._qvf_builders import build_qvf
from vibeview.bonds import BondSearchLimitError, check_bond_search, infer_bonds
from vibeview.qvf import QVFError, QVFReader

_MGO_A = 4.212
# Far below the gigabytes the unbounded search reached, far above what the
# refusal needs (about 70 MB of numpy work for R2).
_MEMORY_GUARD = 300_000_000
_TIME_GUARD_S = 10.0


def _mgo(reps: int) -> tuple[np.ndarray, list[int]]:
    """Rocksalt MgO, ``reps`` conventional cells per axis."""
    fcc = np.array([[0, 0, 0], [0.5, 0.5, 0], [0.5, 0, 0.5], [0, 0.5, 0.5]])
    pos, z = [], []
    for cell in np.ndindex(reps, reps, reps):
        for f in fcc:
            pos.append((f + cell) * _MGO_A)
            z.append(12)
            pos.append((f + cell + [0.5, 0, 0]) * _MGO_A)
            z.append(8)
    return np.array(pos), z


def _r1():
    pos, z = _mgo(6)
    return pos, z, np.eye(3) * _MGO_A, (True, True, True)


def _r2():
    rng = np.random.default_rng(0)
    return rng.uniform(0, 30.0, (1500, 3)), [6] * 1500, np.eye(3) * 2.9, (True, True, True)


def _r3():
    rng = np.random.default_rng(0)
    return rng.uniform(0, 0.5, (4000, 3)), [6] * 4000, None, (False, False, False)


_CASES = {"R1": _r1, "R2": _r2, "R3": _r3}


class _Guard:
    """Fail when the block allocates more than the guard or runs too long."""

    def __enter__(self):
        tracemalloc.start()
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *_exc):
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        elapsed = time.perf_counter() - self.t0
        assert peak < _MEMORY_GUARD, f"peak {peak / 1e6:.0f} MB"
        assert elapsed < _TIME_GUARD_S, f"took {elapsed:.1f} s"
        return False


# ── the bond search ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", list(_CASES))
@pytest.mark.parametrize("fn", [infer_bonds, check_bond_search], ids=["infer", "check"])
def test_reproducer_is_refused_under_the_guard(case, fn):
    pos, z, lattice, pbc = _CASES[case]()
    with _Guard(), pytest.raises(BondSearchLimitError, match="too dense or lattice inconsistent"):
        fn(pos, z, lattice=lattice, pbc=pbc)


def test_the_same_supercell_with_its_own_lattice_still_bonds():
    """R1's atoms are fine; only the lattice was wrong."""
    pos, z = _mgo(6)
    lattice = np.eye(3) * _MGO_A * 6
    with _Guard():
        check_bond_search(pos, z, lattice=lattice, pbc=(True, True, True))
        bonds = infer_bonds(pos, z, lattice=lattice, pbc=(True, True, True))
    assert len(bonds) == 5184  # 6 neighbours per ion, each bond counted once


def test_the_r1_message_names_the_likely_producer_bug():
    pos, z, lattice, pbc = _r1()
    with pytest.raises(BondSearchLimitError) as excinfo:
        infer_bonds(pos, z, lattice=lattice, pbc=pbc)
    assert "supercell written with its unit cell" in str(excinfo.value)


# ── the reader and the CLI ──────────────────────────────────────────────────


def _archive(tmp_path, pos, z, lattice, pbc, name="bad.qvf"):
    payload = {
        "atoms": [
            {"symbol": "Mg" if n == 12 else ("O" if n == 8 else "C"),
             "atomic_number": int(n), "position": [float(v) for v in p]}
            for p, n in zip(pos, z, strict=True)
        ],
        "pbc": list(pbc),
    }
    if lattice is not None:
        payload["lattice_vectors"] = np.asarray(lattice).tolist()
    blob = json.dumps(payload).encode()
    return build_qvf(
        tmp_path / name, [("structure", "structure", {"structure": ("json", "s.json", blob)})]
    )


@pytest.mark.parametrize("case", list(_CASES))
def test_the_reader_reports_a_section_error(case, tmp_path):
    path = _archive(tmp_path, *_CASES[case]())
    with QVFReader(path) as reader, _Guard():
        with pytest.raises(QVFError, match="too dense or lattice inconsistent"):
            reader.read_structure()
        assert "too dense or lattice inconsistent" in (reader.section_error("structure") or "")


@pytest.mark.parametrize("command", [["show"], ["capture"], ["export", "--format", "xyz"]])
def test_cli_commands_fail_fast_with_the_reason(command, tmp_path):
    from vibeview.cli import main

    path = _archive(tmp_path, *_r1())
    start = time.perf_counter()
    result = CliRunner().invoke(main, [*command, str(path)])
    assert time.perf_counter() - start < _TIME_GUARD_S
    assert result.exit_code != 0
    assert "too dense or lattice inconsistent" in result.output
    assert "Traceback" not in result.output


# ── R4: a huge section id ───────────────────────────────────────────────────


def test_show_info_with_a_million_character_section_id(tmp_path):
    from vibeview.tui import show as show_mod

    atom = json.dumps(
        {"atoms": [{"symbol": "H", "atomic_number": 1, "position": [0, 0, 0]}], "pbc": [False] * 3}
    ).encode()
    long_id = "x" * 1_000_000
    path = build_qvf(
        tmp_path / "long.qvf",
        [
            ("structure", "structure", {"structure": ("json", "s.json", atom)}),
            (long_id, "structure", {"structure": ("json", "t.json", atom)}),
        ],
    )
    start = time.perf_counter()
    text = show_mod.show(path, None, size=(100, 40), plain=True, info=True)
    assert time.perf_counter() - start < _TIME_GUARD_S
    # The id is cut to 200 characters (rich may wrap them) and says so.
    assert "… (+999,800 chars)" in text
    assert len(text) < 10_000
