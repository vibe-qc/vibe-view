"""F1/F2: the ASE extra, and the format table the browse page shares.

Roadmap decision 4 is "hand-rolled core set, ASE for the long tail". The
defect this covers is not ASE itself but the *bookkeeping*: the browse
page filtered on its own extension list, so it offered a different set of
files than the opener could read.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from vibeview.converters import (
    _ASE_EXTENSIONS,
    _NATIVE_EXTENSIONS,
    _SUPPORTED_EXTENSIONS,
    convert_to_qvf,
    detect_format,
    is_supported_path,
)


class TestTheExtraIsDeclared:
    def test_pyproject_declares_an_ase_extra(self):
        root = Path(__file__).resolve().parents[1]
        data = tomllib.loads((root / "pyproject.toml").read_text())
        extras = data["project"]["optional-dependencies"]
        assert "ase" in extras, "F1: the [ase] extra must exist to be installable"
        assert any("ase" in dep for dep in extras["ase"])

    def test_all_pulls_the_ase_extra(self):
        root = Path(__file__).resolve().parents[1]
        data = tomllib.loads((root / "pyproject.toml").read_text())
        assert any(
            "ase" in dep for dep in data["project"]["optional-dependencies"]["all"]
        ), "`all` should mean all"


class TestDetection:
    @pytest.mark.parametrize("name", ["a.traj", "b.extxyz", "c.vasp", "d.poscar"])
    def test_ase_extensions_route_to_ase(self, name):
        assert detect_format(name) == "ase"

    @pytest.mark.parametrize("name", ["POSCAR", "CONTCAR"])
    def test_extensionless_vasp_files_are_detected(self, name):
        """VASP writes these with no suffix; suffix matching alone misses the
        commonest ASE input in a materials workflow."""
        assert detect_format(name) == "ase"

    @pytest.mark.parametrize("name", ["contcar.txt", "POSCAR.bak", "notes.txt", ""])
    def test_lookalikes_are_not_swept_in(self, name):
        assert detect_format(name) is None

    def test_native_formats_still_win_over_ase(self):
        """A .cif must use the hand-rolled reader, not ASE — otherwise the
        extra becomes required for a format we support natively."""
        assert detect_format("x.cif") == "cif"
        assert detect_format("x.xyz") == "xyz"

    def test_no_extension_is_listed_twice(self):
        """`.traj` was in both the native table and the ASE fallback."""
        assert len(set(_SUPPORTED_EXTENSIONS)) == len(_SUPPORTED_EXTENSIONS)
        assert not set(_NATIVE_EXTENSIONS) & set(_ASE_EXTENSIONS)


class TestBrowseAndOpenerAgree:
    """The audit's finding: the two lists had drifted apart."""

    def test_every_offered_extension_is_actually_openable(self):
        for ext in _SUPPORTED_EXTENSIONS:
            assert detect_format(f"sample{ext}") is not None, ext

    def test_ase_formats_are_offered_by_browse(self):
        for ext in _ASE_EXTENSIONS:
            assert ext in _SUPPORTED_EXTENSIONS, f"{ext} openable but never offered"

    def test_is_supported_path_matches_detect_format(self, tmp_path):
        for name in ("m.xyz", "POSCAR", "CONTCAR", "t.traj", "no.txt", "s.cif"):
            p = tmp_path / name
            p.write_text("x\n")
            assert is_supported_path(p) is (detect_format(p) is not None)

    def test_extensionless_files_survive_the_browse_filter(self, tmp_path):
        """Regression: the filter used endswith(ext), which drops POSCAR."""
        for name in ("POSCAR", "m.xyz", "notes.txt"):
            (tmp_path / name).write_text("x\n")
        offered = sorted(p.name for p in tmp_path.iterdir() if is_supported_path(p))
        assert offered == ["POSCAR", "m.xyz"]


class TestMissingExtraIsActionable:
    def test_error_names_the_extra_and_the_file(self, tmp_path):
        """Without ASE the user must learn what to install, not just that
        something failed."""
        try:
            import ase  # noqa: F401
        except ImportError:
            pass
        else:
            pytest.skip("ASE is installed; the missing-extra path cannot run")
        p = tmp_path / "POSCAR"
        p.write_text("dummy\n")
        with pytest.raises(ValueError) as exc:
            convert_to_qvf(p)
        msg = str(exc.value)
        assert "vibe-view[ase]" in msg, msg
        assert "POSCAR" in msg, msg


class TestRealAseRoundTrip:
    """Only runs where the extra is installed — the CI gate does not have it."""

    def test_extxyz_reads_through_ase(self, tmp_path):
        pytest.importorskip("ase", reason="needs the [ase] extra")
        from vibeview.qvf import QVFReader

        p = tmp_path / "m.extxyz"
        p.write_text('2\nProperties=species:S:1:pos:R:3\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74\n')
        out = tmp_path / "m.qvf"
        out.write_bytes(convert_to_qvf(p).getvalue())
        sd = QVFReader(out).read_structure()
        assert [a.symbol for a in sd.atoms] == ["H", "H"]
