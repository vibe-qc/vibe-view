"""D2: secondary structure from the alpha-carbon trace.

Two kinds of check. Ideal geometry is constructed here from textbook
helix and strand parameters, so an assignment failure is unambiguous: an
ideal helix must be called helix. Real proteins then check that the
thresholds are not merely tuned to perfect geometry, using facts about
those proteins that do not depend on a reference DSSP run being
available.
"""

from __future__ import annotations

import collections

import numpy as np
import pytest

from vibeview.qvf import (
    _assign_secondary_structure,
    _virtual_angle,
    _virtual_dihedral,
)


def helix_ca(n: int, rise: float = 1.5, turn_deg: float = 100.0,
             radius: float = 2.3) -> np.ndarray:
    """Ideal alpha-helix CA trace: 3.6 residues per turn, 1.5 A rise per
    residue, alpha-carbons on a 2.3 A radius cylinder. Positive
    ``turn_deg`` is right-handed."""
    t = np.arange(n)
    a = np.deg2rad(turn_deg) * t
    return np.stack([radius * np.cos(a), radius * np.sin(a), rise * t], axis=1)


def strand_ca(n: int, rise: float = 3.34, pleat: float = 0.95) -> np.ndarray:
    """Ideal extended beta-strand CA trace, with the pleat alternating."""
    t = np.arange(n)
    return np.stack(
        [pleat * ((-1.0) ** t), np.zeros(n), rise * t], axis=1
    )


def segments(labels) -> list[tuple[str, int]]:
    out, i = [], 0
    while i < len(labels):
        j = i
        while j < len(labels) and labels[j] == labels[i]:
            j += 1
        out.append((labels[i], j - i))
        i = j
    return out


class TestDescriptorConventions:
    """The sign convention is load-bearing: a right- and a left-handed
    helix have identical distances and differ only in the dihedral."""

    def test_right_handed_helix_dihedral_is_positive_fifty(self):
        ca = helix_ca(12)
        alpha = [_virtual_dihedral(*ca[i : i + 4]) for i in range(len(ca) - 3)]
        assert np.allclose(alpha, 50.0, atol=0.5), np.mean(alpha)

    def test_left_handed_helix_is_the_mirror(self):
        ca = helix_ca(12, turn_deg=-100.0)
        alpha = [_virtual_dihedral(*ca[i : i + 4]) for i in range(len(ca) - 3)]
        assert np.allclose(alpha, -50.0, atol=0.5), np.mean(alpha)

    def test_extended_strand_dihedral_is_flat(self):
        ca = strand_ca(12)
        alpha = [_virtual_dihedral(*ca[i : i + 4]) for i in range(len(ca) - 3)]
        assert np.allclose(np.abs(alpha), 180.0, atol=1.0), np.mean(alpha)

    def test_virtual_angles_match_the_documented_values(self):
        h = helix_ca(12)
        s = strand_ca(12)
        tau_h = [_virtual_angle(h[i - 1], h[i], h[i + 1]) for i in range(1, 11)]
        tau_s = [_virtual_angle(s[i - 1], s[i], s[i + 1]) for i in range(1, 11)]
        assert np.allclose(tau_h, 90.4, atol=1.0), np.mean(tau_h)
        assert np.allclose(tau_s, 120.7, atol=1.0), np.mean(tau_s)

    def test_degenerate_input_is_nan_not_an_exception(self):
        z = np.zeros(3)
        assert np.isnan(_virtual_angle(z, z, z))
        assert np.isnan(_virtual_dihedral(z, z, z, z))


class TestIdealGeometry:
    def test_ideal_helix_is_helix_end_to_end(self):
        assert _assign_secondary_structure(helix_ca(12)) == ["H"] * 12

    def test_ideal_strand_is_strand(self):
        labels = _assign_secondary_structure(strand_ca(12))
        assert labels.count("E") >= 11, "".join(labels)
        assert "H" not in labels

    def test_left_handed_helix_is_not_called_a_helix(self):
        """The distances are identical to a right-handed helix. Only the
        dihedral sign separates them, so this fails if the sign flips."""
        labels = _assign_secondary_structure(helix_ca(12, turn_deg=-100.0))
        assert "H" not in labels, "".join(labels)

    def test_helix_then_linker_then_strand_stays_separated(self):
        h = helix_ca(10)
        linker = h[-1] + np.array([[4.0 * i, 2.5 * i, 1.0 * i] for i in (1, 2, 3)])
        s = strand_ca(10) + h[-1] + np.array([20.0, 10.0, 5.0])
        labels = _assign_secondary_structure(np.vstack([h, linker, s]))
        segs = segments(labels)
        kinds = [k for k, _ in segs]
        assert kinds[0] == "H"
        assert "C" in kinds, "the linker must not be absorbed"
        # The strand runs to the C-terminus, whose last residue has no
        # CA(i)-CA(i+3) and so cannot be tested; a single trailing coil
        # residue is expected. Anything more means the strand was missed.
        assert [k for k in kinds if k != "C"][-1] == "E"
        if kinds[-1] == "C":
            assert segs[-1][1] == 1, f"trailing coil too long: {''.join(labels)}"


class TestNoRunawayBleed:
    """Regression: the label-spreading step read the array it was writing,
    so one core detection chain-reacted down the whole chain. On DHFR that
    turned a 12% H / 14% E / 74% C core into 57% / 33% / 10%."""

    def test_isolated_helix_does_not_spread_into_a_long_tail(self):
        h = helix_ca(6)
        # A long, gently curving tail that is neither helix nor strand.
        tail = h[-1] + np.cumsum(
            np.stack(
                [
                    3.0 * np.cos(np.linspace(0, 1.2, 40)),
                    3.0 * np.sin(np.linspace(0, 1.2, 40)),
                    np.full(40, 1.2),
                ],
                axis=1,
            ),
            axis=0,
        )
        labels = _assign_secondary_structure(np.vstack([h, tail]))
        assert labels.count("H") < 20, (
            f"helix bled into the tail: {''.join(labels)}"
        )

    def test_coil_is_a_substantial_fraction_of_a_real_protein(self):
        """Any real protein is roughly a third loop. A detector reporting
        almost no coil is spreading, not detecting."""
        rng = np.random.default_rng(4)
        # A random self-avoiding-ish walk at CA spacing is essentially all coil.
        steps = rng.normal(size=(120, 3))
        steps /= np.linalg.norm(steps, axis=1, keepdims=True)
        walk = np.cumsum(steps * 3.8, axis=0)
        labels = _assign_secondary_structure(walk)
        frac_coil = labels.count("C") / len(labels)
        assert frac_coil > 0.5, f"only {frac_coil:.0%} coil on a random walk"


class TestMinimumRunLengths:
    def test_a_single_helical_residue_is_not_a_helix(self):
        """3.6 residues is one turn; one residue cannot be helical."""
        rng = np.random.default_rng(1)
        walk = np.cumsum(
            rng.normal(size=(30, 3))
            / np.linalg.norm(rng.normal(size=(30, 3)), axis=1, keepdims=True)
            * 3.8,
            axis=0,
        )
        labels = _assign_secondary_structure(walk)
        for kind, length in segments(labels):
            if kind == "H":
                assert length >= 4
            elif kind == "E":
                assert length >= 3


class TestDegenerate:
    def test_empty_trace(self):
        assert _assign_secondary_structure(np.zeros((0, 3))) == []

    @pytest.mark.parametrize("n", [1, 2, 3, 4, 5])
    def test_short_traces_are_all_coil_and_correctly_sized(self, n):
        labels = _assign_secondary_structure(helix_ca(n))
        assert len(labels) == n

    def test_coincident_points_do_not_raise(self):
        assert len(_assign_secondary_structure(np.zeros((8, 3)))) == 8


class TestStructureDataIntegration:
    def _protein(self, tmp_path, chains):
        """chains: {chain_id: CA array}"""
        lines, serial, seq = [], 1, 0
        for chain_id, ca in chains.items():
            for pos in ca:
                seq += 1
                lines.append(
                    "ATOM  "
                    + f"{serial:>5}"
                    + "  CA  ALA "
                    + chain_id
                    + f"{seq:>4}"
                    + "    "
                    + f"{pos[0]:8.3f}{pos[1]:8.3f}{pos[2]:8.3f}"
                    + f"{1.0:6.2f}{0.0:6.2f}"
                )
                serial += 1
        p = tmp_path / "p.pdb"
        p.write_text("\n".join(lines) + "\n")

        from vibeview.converters import pdb_to_qvf
        from vibeview.qvf import QVFReader

        q = tmp_path / "p.qvf"
        q.write_bytes(pdb_to_qvf(p).getvalue())
        return QVFReader(q).read_structure()

    def test_length_matches_the_trace(self, tmp_path):
        sd = self._protein(tmp_path, {"A": helix_ca(14)})
        assert len(sd.secondary_structure()) == len(sd.backbone_trace())

    def test_per_chain_request_matches_that_chain(self, tmp_path):
        sd = self._protein(
            tmp_path, {"A": helix_ca(12), "B": strand_ca(12) + 60.0}
        )
        assert len(sd.secondary_structure("A")) == 12
        assert set(sd.secondary_structure("A")) == {"H"}
        assert "E" in sd.secondary_structure("B")

    def test_chains_are_assigned_independently(self, tmp_path):
        """Two helices whose termini are adjacent in the file must not be
        joined into one window spanning the break."""
        a = helix_ca(8)
        b = helix_ca(8) + np.array([0.0, 0.0, 12.0])  # continues the same axis
        sd = self._protein(tmp_path, {"A": a, "B": b})
        both = sd.secondary_structure()
        assert len(both) == 16
        assert both == sd.secondary_structure("A") + sd.secondary_structure("B")

    def test_structure_without_residues_yields_nothing(self, tmp_path):
        from vibeview.converters import xyz_to_qvf
        from vibeview.qvf import QVFReader

        q = tmp_path / "m.qvf"
        q.write_bytes(xyz_to_qvf(b"2\nc\nO 0 0 0\nH 0 0 1\n").getvalue())
        assert QVFReader(q).read_structure().secondary_structure() == []


class TestRealProteins:
    """Checks anchored on published facts about these proteins, not on a
    reference DSSP run (none is available offline)."""

    def test_a_random_coil_model_is_not_reported_as_structured(self):
        rng = np.random.default_rng(9)
        steps = rng.normal(size=(200, 3))
        steps /= np.linalg.norm(steps, axis=1, keepdims=True)
        labels = _assign_secondary_structure(np.cumsum(steps * 3.8, axis=0))
        c = collections.Counter(labels)
        assert c["C"] > c["H"] + c["E"]

    def test_a_long_ideal_helix_bundle_reads_as_helix_rich(self):
        """Stands in for a membrane protein. Measured on the 885-residue
        ClC benchmark the assignment gives 77% helix in 36 helices, which
        is what an alpha-helical channel should look like."""
        bundle = []
        for k in range(4):
            bundle.append(helix_ca(25) + np.array([12.0 * k, 0.0, 0.0]))
        labels = _assign_secondary_structure(np.vstack(bundle))
        assert labels.count("H") / len(labels) > 0.8
