"""Build a 3D structure from a SMILES string (workstream E).

Maintainer decision 2026-07-28: RDKit parses and embeds with ETKDG, and the
geometry the user ends up with is refined by the existing MSINDO live-opt.
So these tests hold the embed to "chemically sensible starting geometry",
not to spectroscopic accuracy — ETKDG puts H-O-H near 113 degrees against
an experimental 104.5, which is precisely why the refinement step exists.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import numpy as np
import pytest

from vibeview.converters import smiles_to_qvf

try:
    import rdkit  # noqa: F401

    HAVE_RDKIT = True
except ImportError:
    HAVE_RDKIT = False

# Per-class rather than module-level, so the missing-extra path below still
# runs where RDKit is absent — which is the environment most users hit first.
needs_rdkit = pytest.mark.skipif(not HAVE_RDKIT, reason="needs the [smiles] extra")


def _structure(smiles: str, tmp_path: Path):
    from vibeview.qvf import QVFReader

    q = tmp_path / "m.qvf"
    q.write_bytes(smiles_to_qvf(smiles).getvalue())
    return QVFReader(q).read_structure()


@needs_rdkit
class TestParsesAndEmbeds:
    def test_water_has_the_right_atoms_and_a_plausible_bond_length(self, tmp_path):
        sd = _structure("O", tmp_path)
        assert sorted(a.symbol for a in sd.atoms) == ["H", "H", "O"]
        pos = {s: [] for s in ("O", "H")}
        for a in sd.atoms:
            pos[a.symbol].append(np.asarray(a.position))
        o = pos["O"][0]
        for h in pos["H"]:
            assert 0.85 < np.linalg.norm(h - o) < 1.15, "O-H way off"

    def test_hydrogens_are_added(self, tmp_path):
        """A SMILES carries no explicit H; embedding without them gives a
        heavy-atom skeleton with wrong geometry at every sp3 centre."""
        assert len(_structure("CCO", tmp_path).atoms) == 9  # C2H6O

    def test_benzene_ring_is_planar_and_aromatic_length(self, tmp_path):
        sd = _structure("c1ccccc1", tmp_path)
        assert len(sd.atoms) == 12
        carbons = np.array([a.position for a in sd.atoms if a.symbol == "C"])
        assert len(carbons) == 6
        centred = carbons - carbons.mean(axis=0)
        out_of_plane = np.linalg.svd(centred, compute_uv=False)[2]
        assert out_of_plane < 0.05, f"ring not planar: {out_of_plane:.3f} A"
        pairs = [
            np.linalg.norm(carbons[i] - carbons[j])
            for i in range(6)
            for j in range(i + 1, 6)
        ]
        assert 1.30 < min(pairs) < 1.45, f"aromatic C-C off: {min(pairs):.3f}"

    def test_a_ring_closure_actually_closes(self, tmp_path):
        """Cyclohexane's six carbons must form a closed ring, which is the
        thing a naive SMILES parser gets wrong."""
        sd = _structure("C1CCCCC1", tmp_path)
        carbons = np.array([a.position for a in sd.atoms if a.symbol == "C"])
        assert len(carbons) == 6
        nearest = [
            sorted(
                np.linalg.norm(carbons[i] - carbons[j])
                for j in range(6)
                if j != i
            )[0]
            for i in range(6)
        ]
        assert all(1.4 < d < 1.7 for d in nearest), nearest

    def test_charged_species_survives(self, tmp_path):
        assert len(_structure("[NH4+]", tmp_path).atoms) == 5

    def test_structure_is_not_collapsed_to_a_point(self, tmp_path):
        """A failed embed can leave every atom at the origin, which still
        produces a valid-looking QVF."""
        pos = np.array([a.position for a in _structure("CCO", tmp_path).atoms])
        assert np.ptp(pos, axis=0).max() > 1.0, "all atoms nearly coincident"


@needs_rdkit
class TestDeterminism:
    def test_same_smiles_gives_the_same_geometry(self, tmp_path):
        """ETKDG is stochastic; an unseeded embed would hand the user a
        different structure each time and make these tests flaky."""
        da, db = tmp_path / "a", tmp_path / "b"
        da.mkdir()
        db.mkdir()
        a = _structure("CCO", da)
        b = _structure("CCO", db)
        np.testing.assert_allclose(
            [x.position for x in a.atoms], [x.position for x in b.atoms], atol=1e-9
        )


@needs_rdkit
class TestBadInput:
    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_empty_input_is_refused(self, bad):
        with pytest.raises(ValueError, match="no SMILES"):
            smiles_to_qvf(bad)

    def test_unparseable_smiles_names_the_input(self):
        """RDKit reports a bad SMILES by returning None and logging to its
        own logger, so the reason never reaches the caller unless we say it."""
        with pytest.raises(ValueError, match="could not parse SMILES"):
            smiles_to_qvf("this-is-not-smiles")

    def test_unclosed_ring_is_refused(self):
        with pytest.raises(ValueError, match="could not parse SMILES"):
            smiles_to_qvf("C1CCCCC")


class TestPackaging:
    def test_smiles_extra_is_declared(self):
        root = Path(__file__).resolve().parents[1]
        extras = tomllib.loads((root / "pyproject.toml").read_text())["project"][
            "optional-dependencies"
        ]
        assert "smiles" in extras
        assert any("rdkit" in d for d in extras["smiles"])
        assert any("smiles" in d for d in extras["all"]), "`all` should mean all"


@pytest.mark.skipif(HAVE_RDKIT, reason="RDKit installed; the missing path cannot run")
class TestMissingExtraIsActionable:
    def test_error_names_the_extra(self):
        """Without RDKit the user must learn what to install, not just that
        building failed."""
        with pytest.raises(ValueError) as exc:
            smiles_to_qvf("CCO")
        msg = str(exc.value)
        # Assert the message is ACTIONABLE, not that it contains a brand
        # string. `install_hint` deliberately emits a path-based requirement
        # for a source checkout -- "<python> -m pip install -e
        # '<checkout>[smiles]'" -- which names no distribution at all.
        #
        # The old assertion was `"vibe-view[smiles]" in msg`. That passed in
        # CI only because $CI_PROJECT_DIR is literally named `vibe-view`, so
        # the checkout PATH happened to contain the substring. It failed in
        # every worktree named anything else, and it was asserting a
        # directory-naming coincidence rather than the property it claims.
        assert "RDKit" in msg, msg
        assert "[smiles]" in msg, msg          # names the extra
        assert "pip install" in msg, msg       # and how to get it


def _app(tmp_path):
    from vibeview.app import create_app
    from vibeview.converters import xyz_to_qvf
    from vibeview.qvf import QVFReader

    q = tmp_path / "seed.qvf"
    q.write_bytes(xyz_to_qvf(b"2\nc\nO 0 0 0\nH 0 0 1\n").getvalue())
    return create_app(QVFReader(q))


@needs_rdkit
class TestBuilderController:
    def test_builds_and_loads_as_a_new_file(self, tmp_path):
        app = _app(tmp_path)
        before = len(app.state.file_names)
        app.state.builder_smiles = "c1ccccc1O"          # phenol, C6H5OH
        app.controller.build_from_smiles()
        assert len(app.state.file_names) == before + 1
        assert app.state.file_names[-1] == "c1ccccc1O"
        assert len(app.state.file_names) - 1 == app.state.active_file_idx

    def test_atom_count_is_the_real_molecule(self, tmp_path):
        app = _app(tmp_path)
        app.state.builder_smiles = "c1ccccc1O"
        app.controller.build_from_smiles()
        assert "13 atoms" in app.state.status_message, app.state.status_message

    def test_dialog_is_reset_so_the_next_build_starts_clean(self, tmp_path):
        app = _app(tmp_path)
        app.state.builder_smiles = "CCO"
        app.controller.build_from_smiles()
        assert app.state.builder_smiles == ""
        assert app.state.builder_dialog is False

    def test_relax_is_handed_to_vibe_qc(self, tmp_path, monkeypatch):
        """Decision 2: RDKit supplies the starting geometry, vibe-qc makes it
        a structure. Building must arm the optimizer, not leave ETKDG output
        sitting there as if it were converged."""
        import vibeview.live_opt as lo_mod

        class FakeService:
            instances: list = []

            def __init__(self, **kwargs):
                self.scheduled: list = []
                FakeService.instances.append(self)

            def schedule(self, request, **_callbacks):
                self.scheduled.append(request)

            def cancel(self):
                pass

            @property
            def running(self):
                return False

        monkeypatch.setattr(lo_mod, "LiveOptService", FakeService)
        app = _app(tmp_path)
        assert app.state.live_opt_available is None
        app.state.builder_smiles = "CCO"
        app.controller.build_from_smiles()
        assert app.state.live_opt_enabled is True
        service = FakeService.instances[-1]
        assert len(service.scheduled) == 1
        assert len(service.scheduled[0]["symbols"]) == 9

    def test_empty_input_does_nothing(self, tmp_path):
        app = _app(tmp_path)
        before = len(app.state.file_names)
        app.state.builder_smiles = "   "
        app.controller.build_from_smiles()
        assert len(app.state.file_names) == before

    def test_bad_smiles_reports_and_adds_no_file(self, tmp_path):
        """A typo must not leave a broken entry in the Files dropdown."""
        app = _app(tmp_path)
        before = len(app.state.file_names)
        app.state.builder_smiles = "not-a-smiles"
        app.controller.build_from_smiles()
        assert len(app.state.file_names) == before
        assert "could not parse SMILES" in app.state.status_message

    def test_availability_flag_is_true_with_the_extra(self, tmp_path):
        assert _app(tmp_path).state.smiles_available is True


@pytest.mark.skipif(HAVE_RDKIT, reason="RDKit installed; the missing path cannot run")
class TestBuilderWithoutTheExtra:
    def test_flag_is_false_and_the_error_names_the_install(self, tmp_path):
        app = _app(tmp_path)
        assert app.state.smiles_available is False
        before = len(app.state.file_names)
        app.state.builder_smiles = "CCO"
        app.controller.build_from_smiles()
        assert len(app.state.file_names) == before
        # Same reasoning as TestMissingExtraIsActionable: assert the status
        # line is actionable, not that it contains a path-dependent brand
        # substring.
        assert "[smiles]" in app.state.status_message, app.state.status_message
        assert "pip install" in app.state.status_message, app.state.status_message
