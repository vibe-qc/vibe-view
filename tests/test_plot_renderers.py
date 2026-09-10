"""Regression tests for the 2D plot renderers (bands / DOS / spectra).

Covers audit findings:
* A4-01 — DOS honours fermi_energy_ev (Fermi line / referencing) and stays
  readable when the producer ships absolute energies with an out-of-range
  Fermi level.
* A4-02 — bands energy axis is always eV, never mislabeled "a.u." when
  fermi == 0.0.
* A4-03 — ECD/VCD signed (negative Cotton) bands are NOT dropped.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import numpy as np

from vibeview.qvf import QVFReader


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _make_qvf(sections: list[dict], files: dict[str, bytes]) -> Path:
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0", "calculation": "t"},
        "sections": sections,
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for p, d in files.items():
            zf.writestr(p, d)
    return Path(tmp.name)


def _dos_qvf(energies: np.ndarray, fermi: float) -> Path:
    dos = np.exp(-((energies - energies.mean()) ** 2)).astype(np.float64)
    e = energies.astype(np.float64)
    sec = {
        "id": "dos_total", "kind": "dos.total", "n_spin": 1,
        "fermi_energy_ev": float(fermi),
        "members": {
            "energies": {"path": "d/e.bin", "format": "binary", "dtype": "float64",
                         "shape": [len(e)], "sha256": _sha(e.tobytes())},
            "dos": {"path": "d/d.bin", "format": "binary", "dtype": "float64",
                    "shape": [len(dos)], "sha256": _sha(dos.tobytes())},
        },
    }
    return _make_qvf([sec], {"d/e.bin": e.tobytes(), "d/d.bin": dos.tobytes()})


def test_dos_fermi_referenced_when_in_range():
    from vibeview.renderers.dos import DOSRenderer
    path = _dos_qvf(np.linspace(-5.0, 5.0, 32), fermi=-2.3)
    try:
        reader = QVFReader(path)
        html = DOSRenderer(reader.get_section("dos_total"), reader).render_to_html()
        assert "E_F (eV)" in html           # axis relabeled ⇒ Fermi consumed
        assert "CC3333" in html             # Fermi reference line drawn
    finally:
        path.unlink()


def test_dos_absolute_energies_out_of_range_fermi_stays_readable():
    """The committed NaCl showcase ships absolute energies with E_F far
    outside the DOS window. We must NOT shift the data off-screen; instead
    surface E_F in the axis label."""
    from vibeview.renderers.dos import DOSRenderer
    path = _dos_qvf(np.linspace(-2805.0, 67.0, 64), fermi=4659.0)
    try:
        reader = QVFReader(path)
        html = DOSRenderer(reader.get_section("dos_total"), reader).render_to_html()
        assert "outside range" in html
        assert "E − E_F" not in html  # not Fermi-referenced (would be unreadable)
    finally:
        path.unlink()


def _bands_qvf(fermi: float) -> Path:
    n_k, n_b = 10, 4
    eig = (np.linspace(-3, 3, n_k * n_b).reshape(1, n_k, n_b)).astype(np.float64)
    kpath = json.dumps({"n_kpoints": n_k, "n_bands": n_b, "n_spin": 1,
                        "fermi": float(fermi), "segments": []}).encode()
    sec = {
        "id": "bands0", "kind": "bands",
        "members": {
            "kpath": {"path": "b/k.json", "format": "json", "sha256": _sha(kpath)},
            "eigenvalues": {"path": "b/e.bin", "format": "binary", "dtype": "float64",
                            "shape": [1, n_k, n_b], "sha256": _sha(eig.tobytes())},
        },
    }
    return _make_qvf([sec], {"b/k.json": kpath, "b/e.bin": eig.tobytes()})


def test_bands_axis_is_ev_not_au_when_fermi_zero():
    """A4-02: eigenvalues are always eV; fermi==0.0 must not flip the label
    to 'a.u.' (the old `if fermi` truthiness bug)."""
    from vibeview.renderers.bands import BandsRenderer
    path = _bands_qvf(fermi=0.0)
    try:
        reader = QVFReader(path)
        html = BandsRenderer(reader.get_section("bands0"), reader).render_to_html()
        assert "a.u." not in html
        assert "eV" in html
    finally:
        path.unlink()


def _bands_and_dos_qvf() -> Path:
    """One archive carrying both a bands and a dos.total section."""
    n_k, n_b = 20, 4
    eig = np.linspace(-3, 3, n_k * n_b).reshape(1, n_k, n_b).astype(np.float64)
    kpath = json.dumps({"n_kpoints": n_k, "n_bands": n_b, "n_spin": 1, "fermi": 0.5,
                        "segments": [{"label_start": "G", "label_end": "X", "n_points": n_k}]}).encode()
    npts = 50
    en = np.linspace(-3, 3, npts).astype(np.float64)
    d = np.exp(-en ** 2).astype(np.float64)
    sections = [
        {"id": "bands0", "kind": "bands", "members": {
            "kpath": {"path": "b/k.json", "format": "json", "sha256": _sha(kpath)},
            "eigenvalues": {"path": "b/e.bin", "format": "binary", "dtype": "float64",
                            "shape": [1, n_k, n_b], "sha256": _sha(eig.tobytes())}}},
        {"id": "dos_total", "kind": "dos.total", "n_spin": 1, "fermi_energy_ev": 0.5, "members": {
            "energies": {"path": "d/e.bin", "format": "binary", "dtype": "float64",
                         "shape": [npts], "sha256": _sha(en.tobytes())},
            "dos": {"path": "d/d.bin", "format": "binary", "dtype": "float64",
                    "shape": [npts], "sha256": _sha(d.tobytes())}}},
    ]
    return _make_qvf(sections, {"b/k.json": kpath, "b/e.bin": eig.tobytes(),
                                "d/e.bin": en.tobytes(), "d/d.bin": d.tobytes()})


def test_bands_dos_combined_shares_one_energy_axis():
    """A4-04: bands + DOS render as a SINGLE figure with a shared energy
    axis (not two independent side-by-side plots), with one Fermi line."""
    from vibeview.renderers.bands import BandsRenderer
    from vibeview.renderers.dos import DOSRenderer, render_bands_dos_combined

    path = _bands_and_dos_qvf()
    try:
        reader = QVFReader(path)
        html = render_bands_dos_combined(
            BandsRenderer(reader.get_section("bands0"), reader),
            DOSRenderer(reader.get_section("dos_total"), reader),
        )
        assert html.count("Plotly.newPlot") == 1     # ONE figure, not two
        assert "xaxis2" in html                        # two subplots
        assert "matches" in html or "anchor" in html   # shared y-axis
        assert "E_F (eV)" in html.replace("\\u2212", "-")  # Fermi-referenced axis
        # both band lines (≥ n_bands) and a DOS trace are present
        assert html.count('"mode":"lines"') >= 5
    finally:
        path.unlink()


def test_ecd_negative_cotton_bands_are_rendered():
    """A4-03: signed rotatory strengths — the broadened envelope must dip
    negative for a negative band, not be clipped at zero."""
    from vibeview.renderers.spectra import _broadened_envelope
    freqs = np.array([200.0, 250.0, 300.0])
    intens = np.array([5.0, -8.0, 3.0])  # middle band is a negative Cotton band
    grid = np.linspace(150.0, 350.0, 400)
    env = _broadened_envelope(freqs, intens, gamma=5.0, x_grid=grid)
    assert env.min() < -1.0, "negative Cotton band was dropped"
    assert env.max() > 1.0, "positive bands still present"


# ── phonon_bands / phonon_dos (deferred-kind renderers, 2026-06) ───────────


def _phonon_bands_qvf(soft: bool = False) -> Path:
    """phonon_bands: q-path JSON + frequencies [n_q, n_modes] in cm^-1."""
    n_q, n_modes = 12, 6
    base = np.linspace(0.0, 400.0, n_q)
    freq = np.stack([base + 50.0 * m for m in range(n_modes)], axis=1).astype(np.float64)
    if soft:
        freq[0, 0] = -25.0  # a soft (imaginary) acoustic mode at Gamma
    qpath = json.dumps(
        {
            "n_modes": n_modes, "n_atoms": 2, "has_eigenvectors": False,
            "segments": [{"label_start": "G", "label_end": "X", "n_points": n_q}],
        }
    ).encode()
    sec = {
        "id": "phonon_bands", "kind": "phonon_bands",
        "members": {
            "qpath": {"path": "p/q.json", "format": "json", "sha256": _sha(qpath)},
            "frequencies": {"path": "p/f.bin", "format": "binary", "dtype": "float64",
                            "shape": [n_q, n_modes], "sha256": _sha(freq.tobytes())},
        },
    }
    return _make_qvf([sec], {"p/q.json": qpath, "p/f.bin": freq.tobytes()})


def test_phonon_bands_renders_frequencies_in_cm1():
    from vibeview.renderers.phonon import PhononBandsRenderer
    path = _phonon_bands_qvf()
    try:
        reader = QVFReader(path)
        html = PhononBandsRenderer(reader.get_section("phonon_bands"), reader).render_to_html()
        assert "Frequency" in html and "cm" in html   # cm^-1 frequency axis
        assert "q-point" in html
        assert html.count('"mode":"lines"') >= 6       # one trace per mode
    finally:
        path.unlink()


def test_phonon_bands_soft_mode_preserved():
    """A negative (imaginary) branch must survive the read and be plotted, not
    clamped — soft modes are a dynamical instability the user must see."""
    from vibeview.renderers.phonon import PhononBandsRenderer
    path = _phonon_bands_qvf(soft=True)
    try:
        reader = QVFReader(path)
        r = PhononBandsRenderer(reader.get_section("phonon_bands"), reader)
        assert float(r.load().frequencies.min()) < 0.0   # not clamped on read
        assert "Plotly.newPlot" in r.render_to_html()     # renders without error
    finally:
        path.unlink()


def _phonon_dos_qvf() -> Path:
    n = 64
    freq = np.linspace(0.0, 500.0, n).astype(np.float64)
    dos = np.exp(-((freq - 250.0) ** 2) / 5000.0).astype(np.float64)
    meta = json.dumps({"smearing": 5.0, "n_atoms": 2}).encode()
    sec = {
        "id": "phonon_dos", "kind": "phonon_dos",
        "members": {
            "meta": {"path": "p/m.json", "format": "json", "sha256": _sha(meta)},
            "frequencies": {"path": "p/df.bin", "format": "binary", "dtype": "float64",
                            "shape": [n], "sha256": _sha(freq.tobytes())},
            "dos": {"path": "p/dd.bin", "format": "binary", "dtype": "float64",
                    "shape": [n], "sha256": _sha(dos.tobytes())},
        },
    }
    return _make_qvf([sec], {"p/m.json": meta, "p/df.bin": freq.tobytes(),
                             "p/dd.bin": dos.tobytes()})


def test_phonon_dos_renders():
    from vibeview.renderers.phonon import PhononDOSRenderer
    path = _phonon_dos_qvf()
    try:
        reader = QVFReader(path)
        html = PhononDOSRenderer(reader.get_section("phonon_dos"), reader).render_to_html()
        assert "Phonon DOS" in html
        assert "states" in html                   # DOS y-axis units (states / cm^-1)
        assert '"fill":"tozeroy"' in html
    finally:
        path.unlink()


def test_phonon_kinds_classify_as_rendered():
    """Wiring guard: the two phonon kinds moved DEFERRED -> rendered."""
    from vibeview.kinds import DEFERRED_KINDS, classify_section
    for k in ("phonon_bands", "phonon_dos"):
        assert classify_section(k) == ("rendered", None)
        assert k not in DEFERRED_KINDS


def test_phonon_activate_helpers_set_panel():
    """The app's `_activate_phonon_*` helpers (reached from activate_section's
    dispatch, which mirrors the bands/dos branches) load the renderer and set
    the shared phonon_html side panel + its title."""
    import types

    from vibeview.app import _activate_phonon_bands, _activate_phonon_dos

    state = types.SimpleNamespace(phonon_html=None, phonon_title="", status_message="")
    pb, pd = _phonon_bands_qvf(), _phonon_dos_qvf()
    try:
        rb = QVFReader(pb)
        _activate_phonon_bands(rb, state, rb.get_section("phonon_bands"))
        assert state.phonon_html and "Plotly.newPlot" in state.phonon_html
        assert state.phonon_title == "Phonon Band Structure"

        rd = QVFReader(pd)
        _activate_phonon_dos(rd, state, rd.get_section("phonon_dos"))
        assert state.phonon_html and "Phonon DOS" in state.phonon_html
        assert state.phonon_title == "Phonon DOS"
    finally:
        pb.unlink()
        pd.unlink()


# ── equation_of_state (deferred-kind renderer, 2026-06) ────────────────────


def _eos_qvf(model: str = "birch_murnaghan") -> Path:
    """equation_of_state: V-E points (sampled on the BM3 curve) + fit params."""
    from vibeview.renderers.eos import _birch_murnaghan_energy
    vols = np.linspace(100.0, 120.0, 9).astype(np.float64)
    engs = _birch_murnaghan_energy(vols, 110.0, -5000.0, 75.0, 4.0).astype(np.float64)
    fit = json.dumps(
        {"model": model, "V0": 110.0, "E0": -5000.0, "B0": 75.0, "B0_prime": 4.0}
    ).encode()
    sec = {
        "id": "eos", "kind": "equation_of_state",
        "members": {
            "volumes": {"path": "e/v.bin", "format": "binary", "dtype": "float64",
                        "shape": [len(vols)], "sha256": _sha(vols.tobytes())},
            "energies": {"path": "e/e.bin", "format": "binary", "dtype": "float64",
                         "shape": [len(engs)], "sha256": _sha(engs.tobytes())},
            "fit": {"path": "e/fit.json", "format": "json", "sha256": _sha(fit)},
        },
    }
    return _make_qvf([sec], {"e/v.bin": vols.tobytes(), "e/e.bin": engs.tobytes(),
                             "e/fit.json": fit})


def test_eos_renders_points_and_birch_fit():
    from vibeview.renderers.eos import EquationOfStateRenderer
    path = _eos_qvf()
    try:
        reader = QVFReader(path)
        html = EquationOfStateRenderer(reader.get_section("eos"), reader).render_to_html()
        assert "Equation of State" in html
        assert "Volume" in html and "Energy" in html
        assert "Birch-Murnaghan fit" in html         # fitted curve trace
        assert "Birch (1947)" in html                # user-facing citation
        assert "Computed" in html                    # data-point trace
        assert html.count('"mode":"markers"') >= 2   # points + V0 marker
        assert html.count('"mode":"lines"') >= 1     # the fitted curve
    finally:
        path.unlink()


def test_eos_birch_murnaghan_formula():
    """3rd-order Birch-Murnaghan energy form (Birch, Phys. Rev. 71, 809, 1947),
    in eV / Angstrom^3 / GPa, validated at known points."""
    from vibeview.renderers.eos import _birch_murnaghan_energy
    # E(V0) == E0 exactly (the bracket vanishes at V == V0).
    assert abs(
        _birch_murnaghan_energy(np.array([110.0]), 110.0, -5000.0, 75.0, 4.0)[0] + 5000.0
    ) < 1e-9
    # Hand-computed reference at V = 100 Angstrom^3 (compression): E ~ -4999.7507 eV.
    e100 = _birch_murnaghan_energy(np.array([100.0]), 110.0, -5000.0, 75.0, 4.0)[0]
    assert abs(e100 - (-4999.7507)) < 2e-3
    # Convex about V0: both sides rise above E0.
    e120 = _birch_murnaghan_energy(np.array([120.0]), 110.0, -5000.0, 75.0, 4.0)[0]
    assert e100 > -5000.0 and e120 > -5000.0


def test_eos_murnaghan_model_selected():
    from vibeview.renderers.eos import EquationOfStateRenderer
    path = _eos_qvf(model="murnaghan")
    try:
        reader = QVFReader(path)
        html = EquationOfStateRenderer(reader.get_section("eos"), reader).render_to_html()
        assert "Murnaghan fit" in html
        assert "Murnaghan (1944)" in html
    finally:
        path.unlink()


def test_eos_classify_and_activate():
    import types

    from vibeview.app import _activate_eos
    from vibeview.kinds import DEFERRED_KINDS, classify_section

    assert classify_section("equation_of_state") == ("rendered", None)
    assert "equation_of_state" not in DEFERRED_KINDS

    state = types.SimpleNamespace(eos_html=None, eos_title="", status_message="")
    path = _eos_qvf()
    try:
        reader = QVFReader(path)
        _activate_eos(reader, state, reader.get_section("eos"))
        assert state.eos_html and "Plotly.newPlot" in state.eos_html
        assert state.eos_title == "Equation of State"
    finally:
        path.unlink()


# ── volume.potential (deferred-kind renderer, 2026-06) ─────────────────────


def test_volume_potential_wired_signed_and_diverging():
    """volume.potential routes to the shared volume renderer, is SUPPORTED (not
    deferred), and defaults to a diverging colormap. Its signed ±-lobe rendering
    is exercised end-to-end by test_audit_2026_06_03's parametrized both-lobes
    test."""
    from vibeview.kinds import DEFERRED_KINDS, classify_section
    from vibeview.viewer_defaults import _default_for_kind

    assert classify_section("volume.potential") == ("rendered", None)
    assert "volume.potential" not in DEFERRED_KINDS
    assert _default_for_kind("volume.potential").colormap in ("coolwarm", "RdBu")


# ── volume.rdg / NCI (deferred-kind renderer, 2026-06) ─────────────────────


def _gaussian(n: int = 21, well: bool = False, sigma: float = 1.0) -> np.ndarray:
    """A radial Gaussian bump (density maximum at centre), or, with well=True,
    a Gaussian well 1 - 0.5*bump (density minimum at centre)."""
    xs = (np.arange(n) - (n - 1) / 2) * 0.3
    X, Y, Z = np.meshgrid(xs, xs, xs, indexing="ij")
    g = np.exp(-(X**2 + Y**2 + Z**2) / (2 * sigma**2))
    return (1.0 - 0.5 * g if well else g).astype(np.float64)


def test_sign_lambda2_rho_maximum_is_negative():
    """At a density maximum every Hessian eigenvalue is negative, so λ₂ < 0 and
    the NCI coloring field sign(λ₂)ρ = -ρ < 0 (Johnson et al. 2010)."""
    from vibeview.renderers.nci import sign_lambda2_rho
    rho = _gaussian(well=False)
    field = sign_lambda2_rho(rho, np.diag([0.3, 0.3, 0.3]))
    c = 10  # centre of the n=21 grid
    assert field[c, c, c] < 0
    assert np.isclose(field[c, c, c], -rho[c, c, c])  # sign is exactly -1


def test_sign_lambda2_rho_minimum_is_positive():
    """At a density minimum every eigenvalue is positive, so λ₂ > 0 and
    sign(λ₂)ρ = +ρ > 0."""
    from vibeview.renderers.nci import sign_lambda2_rho
    rho = _gaussian(well=True)
    field = sign_lambda2_rho(rho, np.diag([0.3, 0.3, 0.3]))
    c = 10
    assert field[c, c, c] > 0
    assert np.isclose(field[c, c, c], rho[c, c, c])


def _nci_qvf(with_density: bool = True) -> Path:
    """A volume.rdg section (RDG ramping 0→1 along z, so a 0.3 isosurface
    exists) + optionally a co-present volume.density (a Gaussian bump)."""
    n = 16
    grid = json.dumps({
        "origin": [-2.25, -2.25, -2.25],
        "voxel_vectors": [[0.3, 0, 0], [0, 0.3, 0], [0, 0, 0.3]],
        "shape": [n, n, n],
    }).encode()
    rdg = np.broadcast_to(
        np.linspace(0.0, 1.0, n, dtype=np.float32), (n, n, n)
    ).copy()
    secs = [{
        "id": "rdg", "kind": "volume.rdg", "members": {
            "grid": {"path": "r/g.json", "format": "json", "sha256": _sha(grid)},
            "data": {"path": "r/d.bin", "format": "binary", "dtype": "float32",
                     "shape": [n, n, n], "sha256": _sha(rdg.tobytes())},
        }}]
    files = {"r/g.json": grid, "r/d.bin": rdg.tobytes()}
    if with_density:
        xs = (np.arange(n) - (n - 1) / 2) * 0.3
        X, Y, Z = np.meshgrid(xs, xs, xs, indexing="ij")
        rho = np.exp(-(X**2 + Y**2 + Z**2) / 2.0).astype(np.float32)
        secs.append({
            "id": "dens", "kind": "volume.density", "members": {
                "grid": {"path": "d/g.json", "format": "json", "sha256": _sha(grid)},
                "data": {"path": "d/d.bin", "format": "binary", "dtype": "float32",
                         "shape": [n, n, n], "sha256": _sha(rho.tobytes())},
            }})
        files["d/g.json"] = grid
        files["d/d.bin"] = rho.tobytes()
    return _make_qvf(secs, files)


def _render_rdg(reader):
    import types

    import pyvista as pv

    from vibeview.app import _rebuild_volume
    from vibeview.viewer_defaults import ViewerState

    plotter = pv.Plotter(off_screen=True)
    vs = ViewerState()
    state = types.SimpleNamespace(
        active_volume_id=None, volume_loaded=False, status_message="",
        clip_enabled=False,
    )
    _rebuild_volume(reader, plotter, vs, state, "rdg")
    return plotter, state


def test_nci_colored_surface_with_density():
    path = _nci_qvf(with_density=True)
    try:
        plotter, state = _render_rdg(QVFReader(path))
        assert "NCI" in state.status_message
        assert "uncolored" not in state.status_message   # density present → colored
        assert "volume_rdg" in plotter.actors            # surface actor added
    finally:
        path.unlink()


def test_nci_uncolored_without_density():
    path = _nci_qvf(with_density=False)
    try:
        plotter, state = _render_rdg(QVFReader(path))
        assert "NCI" in state.status_message
        assert "uncolored" in state.status_message       # no density → fallback
        assert "volume_rdg" in plotter.actors
    finally:
        path.unlink()


def test_volume_rdg_wired():
    from vibeview.kinds import DEFERRED_KINDS, classify_section

    assert classify_section("volume.rdg") == ("rendered", None)
    assert "volume.rdg" not in DEFERRED_KINDS


# ── fermi_surface (deferred-kind renderer, 2026-06) ────────────────────────


def test_reciprocal_lattice_satisfies_2pi_delta():
    """b_i · a_j = 2π δ_ij for an orthorhombic and a non-orthogonal cell."""
    from vibeview.renderers.fermi import reciprocal_lattice
    for a in (
        np.array([[4.2, 0, 0], [0, 4.0, 0], [0, 0, 3.8]]),
        np.array([[3.0, 0, 0], [1.5, 2.6, 0], [0.0, 0.0, 5.0]]),
    ):
        b = reciprocal_lattice(a)
        assert np.allclose(b @ a.T, 2 * np.pi * np.eye(3), atol=1e-9)


def _free_electron_energies(nk: int, a: np.ndarray, k0: float) -> np.ndarray:
    """E(k) - E_F = |k|² - k0² on a γ-centred MP mesh (minimum-image folded),
    so the E=E_F isosurface is a sphere of radius k0 centred on γ."""
    from vibeview.renderers.fermi import reciprocal_lattice
    recip = reciprocal_lattice(a)

    def mi(m: int, n: int) -> int:
        return m if m <= n // 2 else m - n

    e = np.empty((nk, nk, nk, 1), dtype=np.float64)
    for i in range(nk):
        for j in range(nk):
            for k in range(nk):
                f = np.array([mi(i, nk) / nk, mi(j, nk) / nk, mi(k, nk) / nk])
                kc = f @ recip
                e[i, j, k, 0] = float(kc @ kc) - k0**2
    return e


def test_build_fermi_sheets_free_electron_sphere():
    from vibeview.renderers.fermi import build_fermi_sheets
    nk, k0 = 16, 0.5
    a = np.eye(3) * 4.0
    e = _free_electron_energies(nk, a, k0)
    sheets, recip = build_fermi_sheets(e, a)
    assert len(sheets) == 1
    pts = sheets[0][1].points
    assert pts.shape[0] > 50  # a real surface, not a degenerate blob
    radii = np.linalg.norm(pts, axis=1)
    # The free-electron Fermi surface is a sphere of radius k0 centred at γ.
    assert np.allclose(radii, k0, atol=0.15)


def _fermi_qvf(nk: int = 10, k0: float = 0.5) -> Path:
    a = np.eye(3) * 4.0
    e = _free_electron_energies(nk, a, k0)
    mesh = json.dumps({
        "nk1": nk, "nk2": nk, "nk3": nk, "n_spin": 1,
        "fermi_energy_ev": -4.71, "band_indices": [3],
        "lattice_vectors": a.tolist(),
    }).encode()
    sec = {
        "id": "fermi0", "kind": "fermi_surface", "members": {
            "mesh": {"path": "f/m.json", "format": "json", "sha256": _sha(mesh)},
            "energies": {"path": "f/e.bin", "format": "binary", "dtype": "float64",
                         "shape": [nk, nk, nk, 1], "sha256": _sha(e.tobytes())},
        }}
    return _make_qvf([sec], {"f/m.json": mesh, "f/e.bin": e.tobytes()})


def test_fermi_activate_renders_sheets_and_clears_structure():
    import types

    import pyvista as pv

    from vibeview.app import _activate_fermi
    from vibeview.viewer_defaults import ViewerState

    path = _fermi_qvf()
    try:
        reader = QVFReader(path)
        plotter = pv.Plotter(off_screen=True)
        plotter.add_mesh(pv.Sphere(), name="atom_group_0")  # a real-space actor
        state = types.SimpleNamespace(
            status_message="", structure_hidden=False, active_volume_id="x",
            fermi_selected_bands=None,
        )
        _activate_fermi(reader, plotter, ViewerState(), state, reader.get_section("fermi0"))
        assert "Fermi surface" in state.status_message
        assert state.structure_hidden is True
        assert "atom_group_0" not in plotter.actors          # real-space cleared
        assert "fermi_cell" in plotter.actors                # reciprocal cell drawn
        assert any(n.startswith("fermi_band_") for n in plotter.actors)  # a sheet
    finally:
        path.unlink()


def test_fermi_band_selection_filters_sheets():
    """The band selector (fermi_selected_bands) controls which sheets draw, and
    _activate_fermi publishes the per-band options for the right-panel select."""
    import types

    import pyvista as pv

    from vibeview.app import _activate_fermi
    from vibeview.viewer_defaults import ViewerState

    path = _fermi_qvf()  # one band, label 3
    try:
        reader = QVFReader(path)
        sec = reader.get_section("fermi0")

        # Deselect all bands: only the reciprocal-cell wireframe remains.
        p0 = pv.Plotter(off_screen=True)
        s0 = types.SimpleNamespace(
            status_message="", structure_hidden=False, active_volume_id=None,
            fermi_selected_bands=[],
        )
        _activate_fermi(reader, p0, ViewerState(), s0, sec)
        assert not any(n.startswith("fermi_band_") for n in p0.actors)
        assert "fermi_cell" in p0.actors
        assert s0.fermi_band_options == [{"title": "Band 3", "value": 3}]

        # Select band 3: its sheet returns.
        p1 = pv.Plotter(off_screen=True)
        s1 = types.SimpleNamespace(
            status_message="", structure_hidden=False, active_volume_id=None,
            fermi_selected_bands=[3],
        )
        _activate_fermi(reader, p1, ViewerState(), s1, sec)
        assert "fermi_band_3" in p1.actors
    finally:
        path.unlink()


def test_fermi_surface_wired():
    from vibeview.kinds import DEFERRED_KINDS, classify_section

    assert classify_section("fermi_surface") == ("rendered", None)
    assert DEFERRED_KINDS == frozenset()  # all six promoted kinds now render


# ── Phase D1: compare / overlay mode (2026-06) ─────────────────────────────


_SYM2Z = {"H": 1, "C": 6, "N": 7, "O": 8}


def _structure_qvf(atoms) -> Path:
    """A structure-only .qvf. `atoms` = [(symbol, [x, y, z]), ...]."""
    body = json.dumps({
        "atoms": [
            {"symbol": s, "position": list(p), "atomic_number": _SYM2Z.get(s, 1)}
            for s, p in atoms
        ]
    }).encode()
    sec = {"id": "structure", "kind": "structure", "members": {
        "structure": {"path": "s/s.json", "format": "json", "sha256": _sha(body)}}}
    return _make_qvf([sec], {"s/s.json": body})


def test_compare_overlay_draws_per_file_actors():
    import pyvista as pv

    from vibeview.app import _render_compare_overlay

    pa = _structure_qvf([("O", [0, 0, 0]), ("H", [0.96, 0, 0]), ("H", [-0.24, 0.93, 0])])
    pb = _structure_qvf([("O", [0.2, 0, 0]), ("H", [1.1, 0, 0]), ("H", [-0.1, 0.95, 0])])
    try:
        readers = [QVFReader(pa), QVFReader(pb)]
        plotter = pv.Plotter(off_screen=True)
        legend = _render_compare_overlay(plotter, readers)
        assert len(legend) == 2
        assert "cmp_0" in plotter.actors and "cmp_1" in plotter.actors
        assert legend[0]["color"] != legend[1]["color"]
        assert all("name" in e and "color" in e for e in legend)
        # A re-render clears the prior overlay actors (no stale duplicates).
        legend2 = _render_compare_overlay(plotter, readers[:1])
        assert len(legend2) == 1
        assert "cmp_1" not in plotter.actors
    finally:
        pa.unlink()
        pb.unlink()


def _rot_z(theta: float) -> np.ndarray:
    c, s = np.cos(theta), np.sin(theta)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def test_kabsch_recovers_known_rotation():
    """A structure rotated + translated by a known proper rotation superposes
    back to RMSD ~ 0 (Kabsch 1976)."""
    from vibeview.align import kabsch_fit
    p = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]])
    q = p @ _rot_z(np.pi / 2).T + np.array([3.0, -2.0, 1.0])
    aligned, val = kabsch_fit(p, q)
    assert val < 1e-9
    assert np.allclose(aligned, q, atol=1e-9)


def test_kabsch_identity_is_zero():
    from vibeview.align import kabsch_fit
    p = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1.2]])
    _aligned, val = kabsch_fit(p, p.copy())
    assert val < 1e-12


def test_kabsch_no_reflection():
    """A mirror image must NOT align to RMSD 0 — the determinant guard returns a
    proper rotation, never a roto-reflection that maps a chiral set onto its
    enantiomer."""
    from vibeview.align import kabsch_fit
    p = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    q = p.copy()
    q[:, 2] *= -1.0  # mirror through the xy-plane (improper)
    _aligned, val = kabsch_fit(p, q)
    assert val > 0.1


def test_kabsch_shape_mismatch_raises():
    import pytest

    from vibeview.align import kabsch_fit
    with pytest.raises(ValueError):
        kabsch_fit(np.zeros((3, 3)), np.zeros((4, 3)))


def test_compare_overlay_alignment_reduces_rmsd():
    """With align=True a rotated/translated copy of the same molecule is
    Kabsch-superposed back onto the reference (RMSD ~ 0); without it, no RMSD is
    computed."""
    import pyvista as pv

    from vibeview.app import _render_compare_overlay

    base = [("O", [0, 0, 0]), ("H", [0.96, 0, 0]), ("H", [-0.24, 0.93, 0])]
    rot = _rot_z(0.5)
    moved = [
        (s, list(np.asarray(p, dtype=float) @ rot.T + np.array([2.0, 1.0, 0.5])))
        for s, p in base
    ]
    pa = _structure_qvf(base)
    pb = _structure_qvf(moved)
    try:
        readers = [QVFReader(pa), QVFReader(pb)]
        plotter = pv.Plotter(off_screen=True)
        leg0 = _render_compare_overlay(plotter, readers, align=False)
        assert all(e["rmsd"] is None for e in leg0)
        leg1 = _render_compare_overlay(plotter, readers, align=True)
        assert leg1[0]["rmsd"] == 0.0          # the reference file
        assert leg1[1]["rmsd"] < 1e-6          # rotated copy recovers
        assert "RMSD" in leg1[1]["label"]
    finally:
        pa.unlink()
        pb.unlink()


# ── Phase D2: density difference (2026-06) ─────────────────────────────────


def _density_qvf(data: np.ndarray, voxel: float = 0.4) -> Path:
    """A .qvf with a volume.density section (`data`) + a 1-atom structure."""
    n = data.shape
    grid = json.dumps({
        "origin": [0, 0, 0],
        "voxel_vectors": [[voxel, 0, 0], [0, voxel, 0], [0, 0, voxel]],
        "shape": list(n),
    }).encode()
    blob = data.astype(np.float32).tobytes()
    body = json.dumps(
        {"atoms": [{"symbol": "H", "position": [0, 0, 0], "atomic_number": 1}]}
    ).encode()
    secs = [
        {"id": "structure", "kind": "structure", "members": {
            "structure": {"path": "s.json", "format": "json", "sha256": _sha(body)}}},
        {"id": "density", "kind": "volume.density", "members": {
            "grid": {"path": "g.json", "format": "json", "sha256": _sha(grid)},
            "data": {"path": "d.bin", "format": "binary", "dtype": "float32",
                     "shape": list(n), "sha256": _sha(blob)}}},
    ]
    return _make_qvf(secs, {"s.json": body, "g.json": grid, "d.bin": blob})


def _density_grids(pa: Path, pb: Path):
    from vibeview.renderers.volume import VolumeRenderer
    out = []
    for p in (pa, pb):
        r = QVFReader(p)
        sec = next(s for s in r.sections if s.kind == "volume.density")
        vr = VolumeRenderer(sec, r)
        out.append((vr.load_grid(), vr.load_data()))
    return out


def test_compute_volume_difference_same_grid():
    from vibeview.app import compute_volume_difference
    pa = _density_qvf(np.ones((6, 6, 6), dtype=np.float32))
    pb = _density_qvf(np.full((6, 6, 6), 0.3, dtype=np.float32))
    try:
        (ga, da), (gb, db) = _density_grids(pa, pb)
        delta = compute_volume_difference(ga, da, gb, db)
        assert np.allclose(delta, 0.7)
    finally:
        pa.unlink()
        pb.unlink()


def test_compute_volume_difference_grid_mismatch_raises():
    import pytest

    from vibeview.app import compute_volume_difference
    pa = _density_qvf(np.ones((6, 6, 6), dtype=np.float32))
    pb = _density_qvf(np.ones((5, 5, 5), dtype=np.float32))
    try:
        (ga, da), (gb, db) = _density_grids(pa, pb)
        with pytest.raises(ValueError):
            compute_volume_difference(ga, da, gb, db)
    finally:
        pa.unlink()
        pb.unlink()


def test_density_diff_renders_signed_isosurface():
    import types

    import pyvista as pv

    from vibeview.app import _activate_density_diff
    from vibeview.viewer_defaults import ViewerState

    xs = np.linspace(-2, 2, 16)
    g = np.meshgrid(xs, xs, xs, indexing="ij")
    bump = np.exp(-(g[0] ** 2 + g[1] ** 2 + g[2] ** 2)).astype(np.float32)  # A
    flat = np.zeros((16, 16, 16), dtype=np.float32)                        # B
    pa = _density_qvf(bump, voxel=4 / 16)
    pb = _density_qvf(flat, voxel=4 / 16)
    try:
        readers = [QVFReader(pa), QVFReader(pb)]
        plotter = pv.Plotter(off_screen=True)
        state = types.SimpleNamespace(
            status_message="", compare_mode=False, compare_legend=[],
            active_volume_id=None, structure_hidden=False,
        )
        _activate_density_diff(plotter, ViewerState(), state, readers, 0, 1)
        assert "Density difference" in state.status_message
        # A > B everywhere the bump is non-zero -> the accumulation (red) lobe.
        assert "volume_diff_pos" in plotter.actors
    finally:
        pa.unlink()
        pb.unlink()


# ── Phase E3: density from wavefunction.gto (2026-06) ───────────────────────


def test_wf_density_integrates_to_n_electrons(tables_qvf):
    """rho = sum_i occ_i |psi_i|^2 integrates to the archive's own electron
    count; validates the on-the-fly density kernel.

    The expected N is read from the wavefunction's occupations rather than
    hardcoded, so the test states the invariant (integral == sum of
    occupations) instead of a magic number tied to one example file. It
    previously asserted "10 electrons" against a gitignored water archive
    and therefore skipped in CI entirely.
    """
    import pytest

    from vibeview.renderers.wavefunction import WavefunctionRenderer

    reader = QVFReader(tables_qvf)
    sec = next((s for s in reader.sections if s.kind == "wavefunction.gto"), None)
    assert sec is not None, "fixture must carry a wavefunction.gto section"

    meta = reader._read_json_member(sec.id, "mo_metadata")
    expected = float(sum(meta["occupations"]))
    assert expected > 0

    grid, rho, n_elec = WavefunctionRenderer(sec, reader).evaluate_density(n_per_dim=60)
    assert rho.shape == (60, 60, 60)
    assert float(rho.min()) >= -1e-6          # the density is non-negative
    # A finite grid undercounts sharp core density by a few percent, so
    # allow a one-sided shortfall but no overshoot beyond rounding.
    assert 0.90 * expected < n_elec < 1.03 * expected, (
        f"integrated {n_elec} electrons, expected about {expected}"
    )


# ── Spin density (rho_alpha - rho_beta) ─────────────────────────────────────


def _unrestricted_qvf(tmp_path, multiplicity: int, atoms):
    """A real UHF archive; spin density is only defined for one."""
    import pytest

    vibeqc = pytest.importorskip("vibeqc", reason="needs vibe-qc")
    import os
    from pathlib import Path

    molecule = vibeqc.Molecule(
        [vibeqc.Atom(z, pos) for z, pos in atoms],
        charge=0,
        multiplicity=multiplicity,
    )
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        vibeqc.run_job(
            molecule=molecule, basis="sto-3g", method="uhf", localize=False
        )
    finally:
        os.chdir(cwd)
    return str(next(Path(tmp_path).glob("*.qvf")))


def test_spin_density_integrates_to_the_number_of_unpaired_electrons(tmp_path):
    """The invariant that makes this checkable: int(rho_a - rho_b) dV is
    N_alpha - N_beta exactly -- 2 for a triplet -- and unlike the total
    density it does NOT suffer the core-cusp undercount, because the core
    contributions cancel between the two spin channels.
    """
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    bohr = 1.8897261254578281
    path = _unrestricted_qvf(
        tmp_path, 3, [(8, [0.0, 0.0, 0.0]), (8, [0.0, 0.0, 1.208 * bohr])]
    )
    reader = QVFReader(path)
    sec = next(s for s in reader.sections if s.kind == "wavefunction.gto")

    grid, spin, n_unpaired = WavefunctionRenderer(sec, reader).evaluate_spin_density(
        n_per_dim=60
    )
    assert spin.shape == (60, 60, 60)
    assert abs(n_unpaired - 2.0) < 0.05, n_unpaired
    # Spin polarisation puts genuine negative lobes in an O2 spin density;
    # a purely non-negative result would mean the beta block never entered.
    assert float(spin.min()) < -1e-4
    assert float(spin.max()) > 1e-3


def test_spin_density_refuses_a_restricted_wavefunction(tables_qvf):
    """A restricted wavefunction has rho_alpha == rho_beta, so the difference
    is identically zero; plotting it would be noise about 0 presented as a
    result. Refuse instead."""
    import pytest

    from vibeview.renderers.wavefunction import WavefunctionRenderer

    reader = QVFReader(tables_qvf)
    sec = next(s for s in reader.sections if s.kind == "wavefunction.gto")
    with pytest.raises(ValueError, match="unrestricted"):
        WavefunctionRenderer(sec, reader).evaluate_spin_density(n_per_dim=20)


def test_total_density_is_unchanged_by_the_spin_refactor(tables_qvf):
    """evaluate_density and evaluate_spin_density now share one grid pass;
    guard that the total density did not pick up the beta sign flip."""
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    reader = QVFReader(tables_qvf)
    sec = next(s for s in reader.sections if s.kind == "wavefunction.gto")
    meta = reader._read_json_member(sec.id, "mo_metadata")
    expected = float(sum(meta["occupations"]))
    _grid, rho, n_elec = WavefunctionRenderer(sec, reader).evaluate_density(
        n_per_dim=40
    )
    assert float(rho.min()) >= -1e-6
    assert 0.85 * expected < n_elec < 1.03 * expected
