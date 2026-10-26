"""Tests for the kind registry — the four-rule partial-support contract."""

from __future__ import annotations

from vibeview.kinds import SUPPORTED_KINDS, VOLUME_KINDS, classify_section


class TestSupportedKinds:
    """Rule 1: supported kinds are declared explicitly."""

    def test_structure_is_supported(self) -> None:
        assert "structure" in SUPPORTED_KINDS

    def test_volume_density_is_supported(self) -> None:
        assert "volume.density" in SUPPORTED_KINDS

    def test_volume_orbital_is_supported(self) -> None:
        assert "volume.orbital" in SUPPORTED_KINDS

    def test_bands_is_supported(self) -> None:
        assert "bands" in SUPPORTED_KINDS

    def test_spectra_ir_is_supported(self) -> None:
        assert "spectra.ir" in SUPPORTED_KINDS

    def test_trajectory_is_supported(self) -> None:
        assert "trajectory" in SUPPORTED_KINDS

    def test_vibrations_is_supported(self) -> None:
        assert "vibrations" in SUPPORTED_KINDS

    def test_atom_properties_is_supported(self) -> None:
        assert "atom_properties" in SUPPORTED_KINDS

    def test_supported_kinds_is_frozenset(self) -> None:
        assert isinstance(SUPPORTED_KINDS, frozenset)


class TestClassifySection:
    """Rule 2 + 3: unknown kinds and vendor namespaces."""

    def test_supported_kind_returns_rendered(self) -> None:
        status, detail = classify_section("structure")
        assert status == "rendered"
        assert detail is None

    def test_unknown_kind_returns_skipped_unsupported(self) -> None:
        status, detail = classify_section("some.future.kind")
        assert status == "skipped"
        assert detail == "unsupported"

    def test_vendor_namespace_is_skipped_with_vendor_name(self) -> None:
        status, detail = classify_section("x_orca.orbitals")
        assert status == "skipped"
        assert "vendor namespace" in detail
        assert "orca" in detail

    def test_vendor_namespace_with_dots(self) -> None:
        status, detail = classify_section("x_vasp.charge_density")
        assert status == "skipped"
        assert "vendor namespace" in detail
        assert "vasp" in detail

    def test_bare_x_prefix_is_skipped_with_unknown(self) -> None:
        # This is a pathological input — shouldn't appear in real files
        status, detail = classify_section("x_")
        assert status == "skipped"


class TestLazyKinds:
    def test_volume_sections_are_lazy(self) -> None:
        assert "volume.density" in VOLUME_KINDS
        assert "volume.orbital" in VOLUME_KINDS

    def test_structure_is_not_lazy(self) -> None:
        assert "structure" not in VOLUME_KINDS

    def test_bands_is_not_lazy(self) -> None:
        assert "bands" not in VOLUME_KINDS


class TestKindsModule:
    def test_classify_section_rendered(self):
        from vibeview.kinds import classify_section

        status, detail = classify_section("volume.density")
        assert status == "rendered"
        assert detail is None

    def test_classify_structure_rendered(self):
        from vibeview.kinds import classify_section

        status, detail = classify_section("structure")
        assert status == "rendered"
        assert detail is None

    def test_classify_unknown_returns_skipped(self):
        from vibeview.kinds import classify_section

        status, detail = classify_section("nonexistent.kind")
        assert status == "skipped"
        assert detail is not None

    def test_classify_vendor_namespace(self):
        from vibeview.kinds import classify_section

        status, detail = classify_section("vendor.something")
        assert status in ("skipped", "error")
