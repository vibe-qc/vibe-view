"""U1 — libint AO parity for the `wavefunction.gto` MO evaluator.

The renderer hand-codes real solid harmonics (`_spherical_factors`) plus a GTO
primitive normalization (`_primitive_norm`). The existing
`test_wavefunction_normalization` pins only AO self-overlap + within-shell
orthonormality — both invariant under a per-`m` sign flip or an `m`-permutation,
so they cannot catch a *convention* mismatch (an orbital lobe rendered in the
wrong octant) or a radial mis-normalization.

This test compares the renderer's AO evaluation **element-wise** against
vibe-qc's libint `evaluate_ao` (the same ground truth the producer's `.cube` /
`volume.orbital` grids come from), for s/p/d/f shells, on an **asymmetric,
mixed-sign** grid so any sign / octant / ordering or radial error surfaces.
Requires the native `vibeqc` core (skipped otherwise).

Coefficient convention (important): QVF spec Appendix A.1 fixes the primitive
normalization `N_i`, and the renderer implements it (it multiplies each
primitive by `_primitive_norm`). libint's own `shell.coefficients` are for
*un-normalized* primitives (they already fold `N_i` in), so to drive the
renderer with spec-compliant input we divide them by `_primitive_norm` here —
precisely what a spec-conformant producer must write, and what the bundled
producer `_basis_shell_payload` does. (An earlier revision of this docstring
said the producer wrote libint's coefficients verbatim, per
`vibe-view/AUDIT_2026-06-03.md` § 5 U1; that producer-side defect has since
been fixed, so the note survives only as history.)

Both shell kinds are covered, and that matters. `N_i` depends only on the
total `l`, so for Cartesian shells the mixed components are deliberately not
unit-normalized. Asserting only the spherical case is exactly what let the
renderer carry a bogus per-component correction until 2026-08-05 (see
`test_wavefunction_normalization`): a pure-only parity test cannot see it,
because the solid-harmonic transform absorbs the factor.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from vibeview.qvf import BasisShell
from vibeview.renderers.wavefunction import WavefunctionRenderer, _primitive_norm

vq = pytest.importorskip("vibeqc")


def _f_capable_basis():
    """A single carbon at the origin with an s/p/d/f basis (lmax ≥ 3)."""
    from vibeqc import Atom, BasisSet, Molecule

    mol = Molecule([Atom(6, [0.0, 0.0, 0.0])])
    for name in ("cc-pvtz", "def2-tzvp", "cc-pvqz"):
        try:
            b = BasisSet(mol, name)
        except Exception:  # noqa: BLE001 — basis not available in this build
            continue
        if b.shells() and max(int(s.l) for s in b.shells()) >= 3:
            return b
    return None


def test_renderer_aos_match_libint_evaluate_ao():
    """Every AO the renderer evaluates equals libint's `evaluate_ao` to 1e-6 —
    pinning the solid-harmonic sign/octant/order convention *and* the radial
    normalization for s, p, d, and f shells."""
    basis = _f_capable_basis()
    if basis is None:
        pytest.skip("no s/p/d/f basis available (cc-pvtz / def2-tzvp / cc-pvqz)")

    shells_native = list(basis.shells())
    assert max(int(s.l) for s in shells_native) >= 3, "test must reach f (l=3)"
    assert all(bool(s.pure) for s in shells_native), "expect pure spherical AOs"

    # Asymmetric, mixed-sign grid (bohr): distinct |x|,|y|,|z| and both signs so
    # an x↔y swap, an octant error, or a single sign flip cannot pass unnoticed.
    xs = np.array([-1.3, 0.4, 1.1])
    ys = np.array([-0.7, 0.9, 1.5])
    zs = np.array([-1.1, 0.3, 0.8])
    gi, gj, gk = np.meshgrid(xs, ys, zs, indexing="ij")
    pts = np.stack([gi.ravel(), gj.ravel(), gk.ravel()], axis=1)  # C-order ↔ [i,j,k].ravel()

    n_ao = sum(2 * int(s.l) + 1 for s in shells_native)
    chi = np.asarray(vq.evaluate_ao(basis, pts), dtype=float)
    if chi.shape == (pts.shape[0], n_ao):
        chi = chi.T
    assert chi.shape == (n_ao, pts.shape[0]), f"unexpected evaluate_ao shape {chi.shape}"

    # Convert libint coefficients to the QVF § 4.6 convention (normalized
    # primitives) so the renderer's `× _primitive_norm` reproduces libint.
    shells = []
    for s in shells_native:
        ell = int(s.l)
        exps = np.asarray(s.exponents, dtype=float)
        coeffs = np.asarray(s.coefficients, dtype=float) / np.array(
            [_primitive_norm(ell, float(a)) for a in exps]
        )
        shells.append(
            BasisShell(center=int(s.atom_index), l=ell, exponents=exps,
                       coefficients=coeffs, pure=True)
        )

    wf = SimpleNamespace(shells=shells)
    rend = WavefunctionRenderer.__new__(WavefunctionRenderer)  # exercise the real evaluator
    atom_pos_bohr = np.zeros((1, 3))

    for i in range(n_ao):
        unit_mo = np.zeros(n_ao)
        unit_mo[i] = 1.0  # selecting AO i: the evaluated "MO" is exactly that AO
        ao = rend._evaluate_on_grid(wf, unit_mo, atom_pos_bohr, xs, ys, zs).ravel()
        np.testing.assert_allclose(
            ao, chi[i], atol=1e-6, rtol=0.0,
            err_msg=f"AO {i}: renderer disagrees with libint evaluate_ao "
                    "(sign / octant / ordering / normalization)",
        )


def _cartesian_basis():
    """One carbon at the origin carrying Cartesian s/p/d/f shells.

    Built from explicit ``ShellInfo`` because every named basis forces
    ``set_pure(true)`` (cpp/src/basis.cpp), so the Cartesian path is
    unreachable through ``BasisSet(mol, name)``.
    """
    from vibeqc import Atom, Molecule
    from vibeqc._vibeqc_core import BasisSet, ShellInfo

    mol = Molecule([Atom(6, [0.0, 0.0, 0.0])])
    shells = []
    for ell, alpha in ((0, 1.7), (1, 1.1), (2, 0.85), (3, 0.6)):
        s = ShellInfo()
        s.atom_index = 0
        s.l = ell
        s.pure = False
        s.exponents = [alpha]
        s.coefficients = [1.0]
        s.origin = [0.0, 0.0, 0.0]
        shells.append(s)
    # coefficients_pre_normalized=False so libint embeds N_i, matching what
    # a named basis carries and what the pure test above assumes.
    return BasisSet(mol, shells, "<cart-parity>", False)


def test_renderer_cartesian_aos_match_libint_evaluate_ao():
    """The Cartesian half of the parity contract, element-wise against libint.

    Regression for the 2026-08-05 per-component normalization bug: the
    renderer multiplied every Cartesian component by
    ``sqrt((2l-1)!!/((2i-1)!!(2j-1)!!(2k-1)!!))`` to force unit norm, which
    QVF spec A.1 does not specify. Mixed components came out sqrt(3) high for
    d_xy and the rendered density 3x. The spherical test above could not see
    it, so this one exists.
    """
    basis = _cartesian_basis()
    shells_native = list(basis.shells())
    assert not any(bool(s.pure) for s in shells_native), "expect Cartesian AOs"
    assert max(int(s.l) for s in shells_native) >= 3, "test must reach f (l=3)"

    xs = np.array([-1.3, 0.4, 1.1])
    ys = np.array([-0.7, 0.9, 1.5])
    zs = np.array([-1.1, 0.3, 0.8])
    gi, gj, gk = np.meshgrid(xs, ys, zs, indexing="ij")
    pts = np.stack([gi.ravel(), gj.ravel(), gk.ravel()], axis=1)

    n_ao = sum((int(s.l) + 1) * (int(s.l) + 2) // 2 for s in shells_native)
    assert n_ao == basis.nbasis
    chi = np.asarray(vq.evaluate_ao(basis, pts), dtype=float)
    if chi.shape == (pts.shape[0], n_ao):
        chi = chi.T
    assert chi.shape == (n_ao, pts.shape[0]), f"unexpected evaluate_ao shape {chi.shape}"

    shells = []
    for s in shells_native:
        ell = int(s.l)
        exps = np.asarray(s.exponents, dtype=float)
        coeffs = np.asarray(s.coefficients, dtype=float) / np.array(
            [_primitive_norm(ell, float(a)) for a in exps]
        )
        shells.append(
            BasisShell(center=int(s.atom_index), l=ell, exponents=exps,
                       coefficients=coeffs, pure=False)
        )

    wf = SimpleNamespace(shells=shells)
    rend = WavefunctionRenderer.__new__(WavefunctionRenderer)
    atom_pos_bohr = np.zeros((1, 3))

    for i in range(n_ao):
        unit_mo = np.zeros(n_ao)
        unit_mo[i] = 1.0
        ao = rend._evaluate_on_grid(wf, unit_mo, atom_pos_bohr, xs, ys, zs).ravel()
        np.testing.assert_allclose(
            ao, chi[i], atol=1e-6, rtol=0.0,
            err_msg=f"Cartesian AO {i}: renderer disagrees with libint "
                    "evaluate_ao (sign / octant / ordering / normalization)",
        )
