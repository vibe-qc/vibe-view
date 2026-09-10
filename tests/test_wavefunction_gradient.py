"""AO gradient kernel: the monomial table and the analytic derivative.

This is infrastructure with no user-facing consumer yet. It exists because
every density-derivative property -- ELF, LOL, NCI/RDG, the density
Laplacian -- needs grad(psi), and all four renderers already exist in
vibe-view with nothing to feed them.

Two properties are worth more than the rest and are tested first:

1. The monomial table reproduces the angular factors the renderer already
   uses, exactly. The table is a *second* representation of functions that
   ``test_wavefunction_normalization.py`` already pins; if the two ever
   disagree the table is wrong, because the existing one is the one that has
   been rendering correct orbitals.
2. The analytic gradient matches central finite differences of the existing
   value path. That is the check that a hand-derived derivative cannot fake.
"""

from __future__ import annotations

import math
import os

import numpy as np
import pytest

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

from vibeview.qvf import BasisShell, QVFReader, WavefunctionGTOData  # noqa: E402
from vibeview.renderers.wavefunction import (  # noqa: E402
    WavefunctionRenderer,
    _angular_terms,
    _cartesian_factors,
    _differentiate_terms,
    _evaluate_terms,
    _spherical_factors,
)

ANGSTROM_TO_BOHR = 1.8897261254578281


# ── the monomial table against the functions it mirrors ──────────────────


@pytest.mark.parametrize("l", [0, 1, 2, 3])
@pytest.mark.parametrize("pure", [True, False])
def test_monomial_table_reproduces_the_angular_factors(l, pure):
    rng = np.random.default_rng(0)
    x, y, z = (rng.normal(size=300) for _ in range(3))
    shell = BasisShell(
        center=0,
        l=l,
        exponents=np.array([1.0]),
        coefficients=np.array([1.0]),
        pure=pure,
    )
    if pure:
        racah = math.sqrt(4.0 * math.pi / (2 * l + 1))
        reference = [racah * f for f in _spherical_factors(l, x, y, z)]
    else:
        reference = list(_cartesian_factors(l, x, y, z))

    produced = [_evaluate_terms(terms, x, y, z) for terms in _angular_terms(shell)]
    assert len(produced) == len(reference)
    for got, want in zip(produced, reference, strict=True):
        assert np.abs(got - want).max() < 1e-12


def test_differentiate_terms_is_the_power_rule():
    # 3 x^2 y z^0  ->  d/dx = 6 x y
    assert _differentiate_terms([(3.0, 2, 1, 0)], 0) == [(6.0, 1, 1, 0)]
    # no dependence on the differentiated axis -> the term vanishes
    assert _differentiate_terms([(3.0, 2, 1, 0)], 2) == []
    # linear term differentiates to a constant monomial
    assert _differentiate_terms([(2.5, 0, 0, 1)], 2) == [(2.5, 0, 0, 0)]


def test_solid_harmonics_are_harmonic():
    """A property the closed forms must satisfy and a typo would break:
    real solid harmonics obey del^2 S = 0. Checked through the same table
    the gradient is built from, by differentiating twice."""
    rng = np.random.default_rng(3)
    x, y, z = (rng.normal(size=200) for _ in range(3))
    for l in range(4):
        shell = BasisShell(
            center=0, l=l,
            exponents=np.array([1.0]), coefficients=np.array([1.0]), pure=True,
        )
        for terms in _angular_terms(shell):
            laplacian = np.zeros_like(x)
            for axis in range(3):
                second = _differentiate_terms(
                    _differentiate_terms(terms, axis), axis
                )
                laplacian += _evaluate_terms(second, x, y, z)
            assert np.abs(laplacian).max() < 1e-10, f"l={l} not harmonic"


# ── the gradient against finite differences ──────────────────────────────


def _synthetic_renderer(shells, coeffs):
    """A renderer over a hand-built wavefunction, so the Cartesian path can
    be exercised -- ``BasisSet(mol, name)`` forces pure shells, so no real
    QVF file contains a Cartesian one."""
    n_ao = sum(
        (2 * s.l + 1) if s.pure else (s.l + 1) * (s.l + 2) // 2 for s in shells
    )
    wf = WavefunctionGTOData(
        structure_ref="structure",
        pure=all(s.pure for s in shells),
        n_ao=n_ao,
        shells=shells,
        spin="restricted",
        orbital_kind="canonical",
        energies=np.zeros(1),
        occupations=np.full(1, 2.0),
        symmetry_labels=None,
        alpha_energies=None,
        alpha_occupations=None,
        beta_energies=None,
        beta_occupations=None,
        mo_coefficients=np.asarray(coeffs, dtype=float).reshape(1, n_ao),
        mo_coefficients_alpha=None,
        mo_coefficients_beta=None,
    )
    renderer = WavefunctionRenderer.__new__(WavefunctionRenderer)
    renderer._wf = wf
    renderer.last_dropped_l_fraction = 0.0
    renderer.last_dropped_l_max = 0
    return renderer, wf


def _finite_difference_check(renderer, wf, coeffs, atom_pos, probes, h=1e-5):
    worst = 0.0
    for point in probes:
        axes = [np.array([point[i]]) for i in range(3)]
        _value, grad = renderer._evaluate_gradient_on_grid(
            wf, coeffs, atom_pos, *axes
        )
        analytic = np.array([grad[a][0, 0, 0] for a in range(3)])
        numeric = []
        for a in range(3):
            up, down = point.copy(), point.copy()
            up[a] += h
            down[a] -= h
            v_up = renderer._evaluate_on_grid(
                wf, coeffs, atom_pos, *[np.array([up[i]]) for i in range(3)]
            )[0, 0, 0]
            v_dn = renderer._evaluate_on_grid(
                wf, coeffs, atom_pos, *[np.array([down[i]]) for i in range(3)]
            )[0, 0, 0]
            numeric.append((v_up - v_dn) / (2 * h))
        numeric = np.array(numeric)
        scale = max(float(np.abs(numeric).max()), 1e-8)
        worst = max(worst, float(np.abs(analytic - numeric).max() / scale))
    return worst


@pytest.mark.parametrize("l", [0, 1, 2, 3])
@pytest.mark.parametrize("pure", [True, False])
def test_gradient_matches_finite_differences_per_shell(l, pure):
    """Every l and both shell types, one shell at a time so a failure names
    the culprit instead of pointing at a whole basis."""
    shell = BasisShell(
        center=0,
        l=l,
        exponents=np.array([2.7, 0.6]),
        coefficients=np.array([0.4, 0.7]),
        pure=pure,
    )
    n_ao = (2 * l + 1) if pure else (l + 1) * (l + 2) // 2
    rng = np.random.default_rng(11 + l)
    coeffs = rng.normal(size=n_ao)
    renderer, wf = _synthetic_renderer([shell], coeffs)
    atom_pos = np.zeros((1, 3))
    probes = rng.normal(scale=0.9, size=(8, 3))
    worst = _finite_difference_check(renderer, wf, coeffs, atom_pos, probes)
    assert worst < 1e-6, f"l={l} pure={pure}: relative error {worst:.2e}"


def test_gradient_matches_finite_differences_on_a_real_wavefunction(tmp_path):
    """End to end on a converged UHF wavefunction whose basis carries
    s, p, d and f shells together, with two off-origin centres."""
    vibeqc = pytest.importorskip("vibeqc", reason="needs vibe-qc")
    from pathlib import Path

    molecule = vibeqc.Molecule(
        [
            vibeqc.Atom(6, [0.0, 0.0, 0.0]),
            vibeqc.Atom(7, [0.0, 0.0, 1.17 * ANGSTROM_TO_BOHR]),
        ],
        charge=0,
        multiplicity=2,
    )
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        vibeqc.run_job(
            molecule=molecule, basis="cc-pvtz", method="uhf", localize=False
        )
    finally:
        os.chdir(cwd)

    reader = QVFReader(str(next(Path(tmp_path).glob("*.qvf"))))
    section = next(s for s in reader.sections if s.kind == "wavefunction.gto")
    renderer = WavefunctionRenderer(section, reader)
    wf = renderer.load()
    assert {s.l for s in wf.shells} >= {0, 1, 2, 3}, "fixture must reach f shells"

    atom_pos = renderer._atom_positions_bohr(wf.structure_ref)
    coeffs = (
        wf.mo_coefficients_alpha
        if wf.mo_coefficients_alpha is not None
        else wf.mo_coefficients
    )
    rng = np.random.default_rng(5)
    probes = atom_pos.mean(axis=0) + rng.normal(scale=1.2, size=(6, 3))
    for mo in (0, 3, 6):
        worst = _finite_difference_check(
            renderer, wf, coeffs[mo], atom_pos, probes
        )
        assert worst < 1e-6, f"MO {mo}: relative error {worst:.2e}"


def test_gradient_pass_returns_the_same_values_as_the_value_pass():
    """The gradient evaluator recomputes psi; it must not drift from the
    established value path, which is what every rendered orbital uses."""
    shells = [
        BasisShell(center=0, l=0, exponents=np.array([3.4, 0.5]),
                   coefficients=np.array([0.3, 0.8]), pure=True),
        BasisShell(center=1, l=2, exponents=np.array([1.1]),
                   coefficients=np.array([1.0]), pure=True),
    ]
    rng = np.random.default_rng(7)
    coeffs = rng.normal(size=1 + 5)
    renderer, wf = _synthetic_renderer(shells, coeffs)
    atom_pos = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.9]])
    axes = [np.linspace(-2.0, 2.0, 9) for _ in range(3)]

    from_value = renderer._evaluate_on_grid(wf, coeffs, atom_pos, *axes)
    from_gradient, _grad = renderer._evaluate_gradient_on_grid(
        wf, coeffs, atom_pos, *axes
    )
    assert np.abs(from_value - from_gradient).max() < 1e-12


# ── ELF (Becke & Edgecombe 1990) ─────────────────────────────────────────


def _neon_reader(tmp_path):
    vibeqc = pytest.importorskip("vibeqc", reason="needs vibe-qc")
    from pathlib import Path

    molecule = vibeqc.Molecule(
        [vibeqc.Atom(10, [0.0, 0.0, 0.0])], charge=0, multiplicity=1
    )
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        vibeqc.run_job(
            molecule=molecule, basis="cc-pvtz", method="rhf", localize=False
        )
    finally:
        os.chdir(cwd)
    return QVFReader(str(next(Path(tmp_path).glob("*.qvf"))))


def test_elf_reproduces_beckes_neon_shell_structure(tmp_path):
    """Published target: Becke & Edgecombe, J. Chem. Phys. 92, 5397 (1990),
    Fig. 1 -- the ELF radial profile of Ne.

    The figure shows ELF ~ 1 at the nucleus (K shell), a minimum of about
    0.15 near log10(r/a0) = -0.55, an L-shell maximum of about 0.86 near
    log10(r/a0) = -0.05, and a decay to 0.

    This pins the *convention* as much as the code: Becke's tau has no 1/2
    and pairs with (3/5)(6 pi^2)^(2/3), and using the Thomas-Fermi constant
    instead would move these numbers.
    """
    reader = _neon_reader(tmp_path)
    section = next(s for s in reader.sections if s.kind == "wavefunction.gto")
    renderer = WavefunctionRenderer(section, reader)
    wf = renderer.load()
    atom_pos = renderer._atom_positions_bohr(wf.structure_ref)

    radii = np.logspace(-1.5, 1.0, 140)
    xs = atom_pos[0][0] + radii
    ys = np.array([atom_pos[0][1]])
    zs = np.array([atom_pos[0][2]])

    coeffs = wf.mo_coefficients
    occ = np.asarray(wf.occupations, dtype=float)
    rho = np.zeros((len(radii), 1, 1))
    grad_rho = np.zeros((3, len(radii), 1, 1))
    tau = np.zeros((len(radii), 1, 1))
    for i in range(coeffs.shape[0]):
        if i >= occ.size or occ[i] <= 1e-3:
            continue
        weight = float(occ[i]) * 0.5
        psi, grad = renderer._evaluate_gradient_on_grid(
            wf, coeffs[i], atom_pos, xs, ys, zs
        )
        rho += weight * psi * psi
        for axis in range(3):
            grad_rho[axis] += weight * 2.0 * psi * grad[axis]
        tau += weight * np.sum(grad * grad, axis=0)

    safe = np.maximum(rho, 1e-8)
    d_sigma = tau - 0.25 * np.sum(grad_rho * grad_rho, axis=0) / safe
    d_uniform = (3.0 / 5.0) * (6.0 * math.pi**2) ** (2.0 / 3.0) * safe ** (5.0 / 3.0)
    chi = d_sigma / np.maximum(d_uniform, 1e-300)
    elf = np.clip(np.where(rho > 1e-8, 1.0 / (1.0 + chi * chi), 0.0), 0.0, 1.0).ravel()
    log_r = np.log10(radii)

    assert elf[0] > 0.97, "K shell should be near-perfectly localized"
    i_min = int(np.argmin(elf[:90]))
    i_max = int(np.argmax(elf[i_min:110])) + i_min
    assert 0.10 < elf[i_min] < 0.22, f"K/L minimum {elf[i_min]:.3f}, want ~0.15"
    assert -0.75 < log_r[i_min] < -0.35, log_r[i_min]
    assert 0.78 < elf[i_max] < 0.94, f"L maximum {elf[i_max]:.3f}, want ~0.86"
    assert -0.30 < log_r[i_max] < 0.15, log_r[i_max]
    assert elf[-1] < 0.05, "ELF must decay in the tail"


def test_elf_is_bounded_and_defined_everywhere(tmp_path):
    """Becke eq 14: 0 <= ELF <= 1. Vacuum is 0/0 analytically, so the one
    thing that must not happen is a NaN leaking into the isosurface."""
    reader = _neon_reader(tmp_path)
    section = next(s for s in reader.sections if s.kind == "wavefunction.gto")
    _grid, elf = WavefunctionRenderer(section, reader).evaluate_elf(n_per_dim=24)
    assert np.isfinite(elf).all(), "ELF produced non-finite values"
    assert float(elf.min()) >= 0.0
    assert float(elf.max()) <= 1.0
    # The L-shell maximum (~0.86) is what a coarse grid resolves. Ne's
    # ELF ~ 1 K-shell region sits inside r < 0.1 a0, and 24 points across a
    # +/-4 bohr box is a ~0.35 a0 spacing that never samples it -- see the
    # radial test above, which probes down to r = 0.03 a0 and does reach 1.0.
    assert float(elf.max()) > 0.8


# ── Hessian and NCI (Johnson et al. 2010) ────────────────────────────────


@pytest.mark.parametrize("l", [0, 1, 2, 3])
@pytest.mark.parametrize("pure", [True, False])
def test_hessian_matches_finite_differences_of_the_gradient(l, pure):
    """Second derivatives checked against central differences of the
    already-validated analytic gradient, per shell so a failure names the l."""
    pairs = ((0, 0), (0, 1), (0, 2), (1, 1), (1, 2), (2, 2))
    shell = BasisShell(
        center=0, l=l,
        exponents=np.array([2.1, 0.55]), coefficients=np.array([0.5, 0.6]),
        pure=pure,
    )
    n_ao = (2 * l + 1) if pure else (l + 1) * (l + 2) // 2
    rng = np.random.default_rng(20 + l)
    coeffs = rng.normal(size=n_ao)
    renderer, wf = _synthetic_renderer([shell], coeffs)
    atom_pos = np.zeros((1, 3))
    h = 1e-4
    worst = 0.0
    for point in rng.normal(scale=0.8, size=(5, 3)):
        axes = [np.array([point[i]]) for i in range(3)]
        _v, _g, hess = renderer._evaluate_hessian_on_grid(
            wf, coeffs, atom_pos, *axes
        )
        analytic = np.array([hess[i][0, 0, 0] for i in range(6)])
        numeric = []
        for a, b in pairs:
            up, dn = point.copy(), point.copy()
            up[b] += h
            dn[b] -= h
            _, g_up = renderer._evaluate_gradient_on_grid(
                wf, coeffs, atom_pos, *[np.array([up[i]]) for i in range(3)]
            )
            _, g_dn = renderer._evaluate_gradient_on_grid(
                wf, coeffs, atom_pos, *[np.array([dn[i]]) for i in range(3)]
            )
            numeric.append((g_up[a][0, 0, 0] - g_dn[a][0, 0, 0]) / (2 * h))
        numeric = np.array(numeric)
        scale = max(float(np.abs(numeric).max()), 1e-8)
        worst = max(worst, float(np.abs(analytic - numeric).max() / scale))
    assert worst < 1e-5, f"l={l} pure={pure}: relative error {worst:.2e}"


def _nci_at_point(renderer, wf, atom_pos, point):
    """rho, |grad rho|, s and the Hessian eigenvalues at one point."""
    pairs = ((0, 0), (0, 1), (0, 2), (1, 1), (1, 2), (2, 2))
    axes = [np.array([point[i]]) for i in range(3)]
    coeffs = wf.mo_coefficients
    occ = np.asarray(wf.occupations, dtype=float)
    rho = 0.0
    grad = np.zeros(3)
    hess = np.zeros(6)
    for i in range(coeffs.shape[0]):
        if i >= occ.size or occ[i] <= 1e-3:
            continue
        w = float(occ[i])
        psi, g, h = renderer._evaluate_hessian_on_grid(
            wf, coeffs[i], atom_pos, *axes
        )
        psi = psi[0, 0, 0]
        g = np.array([g[a][0, 0, 0] for a in range(3)])
        h = np.array([h[k][0, 0, 0] for k in range(6)])
        rho += w * psi * psi
        grad += w * 2.0 * psi * g
        for k, (a, b) in enumerate(pairs):
            hess[k] += w * 2.0 * (g[a] * g[b] + psi * h[k])
    matrix = np.zeros((3, 3))
    for k, (a, b) in enumerate(pairs):
        matrix[a, b] = hess[k]
        matrix[b, a] = hess[k]
    s = np.linalg.norm(grad) / (
        2.0 * (3.0 * math.pi**2) ** (1.0 / 3.0) * max(rho, 1e-12) ** (4.0 / 3.0)
    )
    return rho, float(np.linalg.norm(grad)), s, np.linalg.eigvalsh(matrix)


def test_nci_sign_of_lambda2_distinguishes_bonded_from_nonbonded(tmp_path):
    """Johnson et al. 2010 section 2.3, the whole basis of the method.

    A ring critical point is their nonbonded case: no bond critical point,
    so lambda2 > 0. Benzene's ring centre is the cheapest clean example, and
    D6h forces the two positive eigenvalues to be degenerate -- which is an
    independent check on the Hessian, not just on the sign.
    """
    vibeqc = pytest.importorskip("vibeqc", reason="needs vibe-qc")
    from pathlib import Path

    bohr = ANGSTROM_TO_BOHR
    r_c, r_h = 1.39 * bohr, 2.48 * bohr
    atoms = []
    for k in range(6):
        a = k * math.pi / 3.0
        atoms.append(vibeqc.Atom(6, [r_c * math.cos(a), r_c * math.sin(a), 0.0]))
        atoms.append(vibeqc.Atom(1, [r_h * math.cos(a), r_h * math.sin(a), 0.0]))
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        vibeqc.run_job(
            molecule=vibeqc.Molecule(atoms, charge=0, multiplicity=1),
            basis="sto-3g", method="rhf", localize=False,
        )
    finally:
        os.chdir(cwd)

    reader = QVFReader(str(next(Path(tmp_path).glob("*.qvf"))))
    section = next(s for s in reader.sections if s.kind == "wavefunction.gto")
    renderer = WavefunctionRenderer(section, reader)
    wf = renderer.load()
    atom_pos = renderer._atom_positions_bohr(wf.structure_ref)

    rho, grad_norm, s, eigenvalues = _nci_at_point(
        renderer, wf, atom_pos, atom_pos.mean(axis=0)
    )
    # It is a critical point: the gradient vanishes, so s vanishes with it.
    assert grad_norm < 1e-8, grad_norm
    assert s < 1e-4, s
    # Ring critical point: one negative, two positive.
    assert eigenvalues[0] < 0 < eigenvalues[1], eigenvalues
    assert eigenvalues[1] > 0 and eigenvalues[2] > 0
    # D6h degeneracy of the in-plane pair.
    assert abs(eigenvalues[1] - eigenvalues[2]) < 1e-6 * abs(eigenvalues[1]) + 1e-9
    # Low density, and the NCI colour is positive => nonbonded/steric.
    assert 0.0 < rho < 0.05
    assert np.sign(eigenvalues[1]) * rho > 0


def test_nci_outputs_are_finite_and_shaped(tmp_path):
    vibeqc = pytest.importorskip("vibeqc", reason="needs vibe-qc")
    from pathlib import Path

    molecule = vibeqc.Molecule(
        [
            vibeqc.Atom(8, [0.0, 0.0, 0.0]),
            vibeqc.Atom(1, [0.0, 0.757 * ANGSTROM_TO_BOHR, 0.587 * ANGSTROM_TO_BOHR]),
            vibeqc.Atom(1, [0.0, -0.757 * ANGSTROM_TO_BOHR, 0.587 * ANGSTROM_TO_BOHR]),
        ],
        charge=0, multiplicity=1,
    )
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        vibeqc.run_job(
            molecule=molecule, basis="sto-3g", method="rhf", localize=False
        )
    finally:
        os.chdir(cwd)
    reader = QVFReader(str(next(Path(tmp_path).glob("*.qvf"))))
    section = next(s for s in reader.sections if s.kind == "wavefunction.gto")
    _grid, s, signed_rho = WavefunctionRenderer(section, reader).evaluate_nci(
        n_per_dim=24
    )
    assert s.shape == signed_rho.shape == (24, 24, 24)
    assert np.isfinite(s).all(), "reduced gradient produced non-finite values"
    assert np.isfinite(signed_rho).all()
    assert float(s.min()) >= 0.0


# ── density Laplacian ────────────────────────────────────────────────────


def test_density_laplacian_matches_finite_differences_of_the_density(tmp_path):
    """del^2 rho is the trace of the Hessian already validated above, so the
    check here is that the *assembly* is right -- that the psi^2 product rule
    (2|grad psi|^2 + 2 psi del^2 psi) is applied correctly.

    Compared against a second-difference Laplacian of the density itself.
    The finite difference is the approximate side: its error converges as
    O(h^2) with no roundoff floor down to h = 1e-3, so a tolerance at
    h = 5e-3 is bounded by the FD, not by the analytic result.
    """
    vibeqc = pytest.importorskip("vibeqc", reason="needs vibe-qc")
    from pathlib import Path

    molecule = vibeqc.Molecule(
        [
            vibeqc.Atom(8, [0.0, 0.0, 0.0]),
            vibeqc.Atom(1, [0.0, 0.757 * ANGSTROM_TO_BOHR, 0.587 * ANGSTROM_TO_BOHR]),
            vibeqc.Atom(1, [0.0, -0.757 * ANGSTROM_TO_BOHR, 0.587 * ANGSTROM_TO_BOHR]),
        ],
        charge=0, multiplicity=1,
    )
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        vibeqc.run_job(
            molecule=molecule, basis="sto-3g", method="rhf", localize=False
        )
    finally:
        os.chdir(cwd)

    reader = QVFReader(str(next(Path(tmp_path).glob("*.qvf"))))
    section = next(s for s in reader.sections if s.kind == "wavefunction.gto")
    renderer = WavefunctionRenderer(section, reader)
    wf = renderer.load()
    atom_pos = renderer._atom_positions_bohr(wf.structure_ref)
    occ = np.asarray(wf.occupations, dtype=float)

    def density(point):
        axes = [np.array([point[i]]) for i in range(3)]
        total = 0.0
        for i in range(wf.mo_coefficients.shape[0]):
            if i >= occ.size or occ[i] <= 1e-3:
                continue
            v = renderer._evaluate_on_grid(
                wf, wf.mo_coefficients[i], atom_pos, *axes
            )[0, 0, 0]
            total += float(occ[i]) * v * v
        return total

    def analytic(point):
        axes = [np.array([point[i]]) for i in range(3)]
        total = 0.0
        for i in range(wf.mo_coefficients.shape[0]):
            if i >= occ.size or occ[i] <= 1e-3:
                continue
            psi, grad, hess = renderer._evaluate_hessian_on_grid(
                wf, wf.mo_coefficients[i], atom_pos, *axes
            )
            psi = psi[0, 0, 0]
            g = np.array([grad[a][0, 0, 0] for a in range(3)])
            trace = hess[0][0, 0, 0] + hess[3][0, 0, 0] + hess[5][0, 0, 0]
            total += float(occ[i]) * 2.0 * (float(g @ g) + psi * trace)
        return total

    rng = np.random.default_rng(2)
    h = 5e-3
    eye = np.eye(3)
    for point in atom_pos.mean(axis=0) + rng.normal(scale=1.0, size=(5, 3)):
        centre = density(point)
        numeric = sum(
            (density(point + h * eye[k]) - 2 * centre + density(point - h * eye[k]))
            / h**2
            for k in range(3)
        )
        relative = abs(analytic(point) - numeric) / max(abs(numeric), 1e-6)
        assert relative < 1e-3, relative


def test_density_laplacian_grid_is_finite_and_signed(tmp_path):
    """Bader's charge-concentration regions are the negative ones; a result
    with no negative region would mean the sign convention had flipped."""
    reader = _neon_reader(tmp_path)
    section = next(s for s in reader.sections if s.kind == "wavefunction.gto")
    _grid, laplacian = WavefunctionRenderer(
        section, reader
    ).evaluate_density_laplacian(n_per_dim=32)
    assert laplacian.shape == (32, 32, 32)
    assert np.isfinite(laplacian).all()
    assert float(laplacian.min()) < 0.0, "no charge-concentration region found"
    assert float(laplacian.max()) > 0.0
