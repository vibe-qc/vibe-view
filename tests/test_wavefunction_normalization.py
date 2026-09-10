"""Normalization + AO-ordering guards for the on-the-fly MO evaluator.

Regression for the CRITICAL double-normalization bug (audit 2026-05-31,
finding A2-01): ``_primitive_norm`` already supplies the *complete*
spherical-GTO normalization, yet ``_spherical_factors`` additionally
applied the full surface normalization ``sqrt((2l+1)/4π)`` — an
l-dependent error that made every AO's self-overlap ``(2l+1)/4π`` instead
of 1, mis-scaling s/p/d/f contributions relative to each other and
distorting the rendered orbital shape. The Cartesian path was wrong in the
opposite direction (``x²`` self-overlap 2, ``x³`` self-overlap 6).

These tests integrate ⟨χ|χ⟩ for a single normalized primitive on a fine
Cartesian grid (in bohr, the evaluator's native units) and assert the
expected self-overlap for every component of l = 0..3, plus within-shell
orthogonality — which together pin both the magnitude and the angular form.

The expected Cartesian self-overlap is **not** 1 per component (2026-08-05).
QVF spec Appendix A.1 normalizes a shell by a single ``N_i`` taken from the
total ``l``, so only the axial components come out at unit norm and a d
shell has ``<xy|xy> = 1/3``. This file previously asserted unit norm for
every Cartesian component, which is what allowed the reader to carry a
per-component correction the format does not specify — rendering mixed
Cartesian densities 3× too large.
"""

from __future__ import annotations

import numpy as np

from vibeview.qvf import BasisShell
from vibeview.renderers.wavefunction import (
    WavefunctionRenderer,
    _primitive_norm,
)

_ALPHA = 0.8
_N = 160
_L = 8.0  # half-box in bohr; exp(-0.8·64) is negligible at the edge


def _grid():
    x = np.linspace(-_L, _L, _N)
    d = float(x[1] - x[0])
    gx, gy, gz = np.meshgrid(x, x, x, indexing="ij")
    return gx, gy, gz, d


def _ao_components(l: int, pure: bool):
    """Return the list of normalized AO grids for one shell of momentum l."""
    gx, gy, gz, d = _grid()
    r2 = gx * gx + gy * gy + gz * gz
    radial = _primitive_norm(l, _ALPHA) * np.exp(-_ALPHA * r2)
    shell = BasisShell(
        center=0,
        l=l,
        exponents=np.array([_ALPHA]),
        coefficients=np.array([1.0]),
        pure=pure,
    )
    # _angular_factors does not use ``self``; call unbound with None.
    angs = WavefunctionRenderer._angular_factors(None, shell, gx, gy, gz)
    return [radial * a for a in angs], d


def test_pure_ao_self_overlap_is_unit():
    for l in range(4):
        aos, d = _ao_components(l, pure=True)
        assert len(aos) == 2 * l + 1
        for m, ao in enumerate(aos):
            ov = float(np.sum(ao * ao) * d**3)
            assert abs(ov - 1.0) < 0.01, f"pure l={l} m={m} self-overlap={ov}"


def _cart_triples(l: int):
    """libint lexicographic (i, j, k): i descending, then j descending."""
    return [(i, j, l - i - j) for i in range(l, -1, -1) for j in range(l - i, -1, -1)]


def _df_odd(m: int) -> float:
    """``m!!`` for odd m; ``(-1)!! = 1``."""
    r = 1.0
    while m > 1:
        r *= m
        m -= 2
    return r


def test_cartesian_ao_self_overlap_follows_spec_a1():
    """Cartesian self-overlap must be ``N_i``'s, NOT unit per component.

    QVF spec Appendix A.1 fixes one normalization per shell, from the total
    ``l`` -- the norm of the axial ``(l,0,0)`` component. A mixed component
    is then *not* unit-normalized; its self-overlap is

        ((2i-1)!!(2j-1)!!(2k-1)!!) / (2l-1)!!

    so a d shell gives ``<xx|xx> = 1`` and ``<xy|xy> = 1/3``.

    This reader previously multiplied in the reciprocal square root to force
    every component to 1. That contradicted the coefficients producers
    actually write, scaling mixed Cartesian AOs by sqrt(3) for d_xy and the
    rendered density by 3. Asserting unit norm here is what let that stand,
    so the assertion is inverted rather than deleted.
    """
    for l in range(4):
        aos, d = _ao_components(l, pure=False)
        triples = _cart_triples(l)
        assert len(aos) == (l + 1) * (l + 2) // 2 == len(triples)
        for (i, j, k), ao in zip(triples, aos):
            expected = (
                _df_odd(2 * i - 1) * _df_odd(2 * j - 1) * _df_odd(2 * k - 1)
            ) / _df_odd(2 * l - 1)
            ov = float(np.sum(ao * ao) * d**3)
            assert abs(ov - expected) < 0.01, (
                f"cart l={l} ({i},{j},{k}) self-overlap={ov}, expected {expected}"
            )


def test_cartesian_axial_components_are_unit_normalized():
    """The axial components are the ones A.1's ``N_i`` is defined against,
    so they -- and only they -- come out at unit norm."""
    for l in range(4):
        aos, d = _ao_components(l, pure=False)
        for (i, j, k), ao in zip(_cart_triples(l), aos):
            if max(i, j, k) != l:
                continue
            ov = float(np.sum(ao * ao) * d**3)
            assert abs(ov - 1.0) < 0.01, f"axial l={l} ({i},{j},{k}) -> {ov}"


def test_pure_within_shell_components_orthonormal():
    """The (2l+1) real solid harmonics of one shell are orthonormal — the
    Gram matrix is the identity. This pins the angular *form*, not just the
    magnitude, so a wrong harmonic would be caught even if it happened to be
    unit-norm. (Cartesian components of the same l are deliberately *not*
    mutually orthogonal — e.g. ⟨x²|y²⟩ = 1/3 — which is exactly why the
    spherical basis is preferred; we only assert their unit self-overlap.)"""
    for l in range(4):
        aos, d = _ao_components(l, pure=True)
        n = len(aos)
        gram = np.empty((n, n))
        for a in range(n):
            for b in range(n):
                gram[a, b] = float(np.sum(aos[a] * aos[b]) * d**3)
        np.testing.assert_allclose(gram, np.eye(n), atol=0.02)


def _unrestricted_wf_reader():
    """In-memory unrestricted wavefunction.gto: 2 AOs (s on two atoms),
    alpha MO0 = AO0, beta MO0 = AO1 — so spin routing is observable."""
    import hashlib
    import json
    import tempfile
    import zipfile
    from pathlib import Path

    from vibeview.qvf import QVFReader

    def sha(b):
        return hashlib.sha256(b).hexdigest()

    structure = json.dumps({
        "atoms": [
            {"symbol": "H", "position": [0.0, 0.0, 0.0], "atomic_number": 1},
            {"symbol": "H", "position": [2.0, 0.0, 0.0], "atomic_number": 1},
        ],
        "pbc": [False, False, False],
    }).encode()
    basis = json.dumps({
        "structure_ref": "structure", "pure": True, "n_ao": 2,
        "shells": [
            {"center": 0, "l": 0, "exponents": [0.8], "coefficients": [1.0]},
            {"center": 1, "l": 0, "exponents": [0.8], "coefficients": [1.0]},
        ],
    }).encode()
    mo_meta = json.dumps({
        "n_ao": 2, "spin": "unrestricted", "orbital_kind": "canonical",
        "alpha": {"energies": [-0.6, 0.2], "occupations": [1.0, 0.0],
                  "symmetry_labels": ["Ag", "B1u"]},
        "beta": {"energies": [-0.5, 0.3], "occupations": [1.0, 0.0],
                 "symmetry_labels": ["B2g", "B3u"]},
    }).encode()
    a_c = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float64).tobytes()  # alpha MO0=AO0
    b_c = np.array([[0.0, 1.0], [1.0, 0.0]], dtype=np.float64).tobytes()  # beta  MO0=AO1
    sections = [
        {"id": "structure", "kind": "structure",
         "members": {"structure": {"path": "s.json", "format": "json", "sha256": sha(structure)}}},
        {"id": "wf", "kind": "wavefunction.gto", "members": {
            "basis": {"path": "b.json", "format": "json", "sha256": sha(basis)},
            "mo_metadata": {"path": "m.json", "format": "json", "sha256": sha(mo_meta)},
            "mo_coefficients_alpha": {"path": "a.dat", "format": "binary", "dtype": "float64", "shape": [2, 2], "sha256": sha(a_c)},
            "mo_coefficients_beta": {"path": "bc.dat", "format": "binary", "dtype": "float64", "shape": [2, 2], "sha256": sha(b_c)},
        }},
    ]
    manifest = {"qvf_version": 1, "source": {"program": "vibe-qc", "version": "0", "calculation": "t"}, "sections": sections}
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for p, d in {"s.json": structure, "b.json": basis, "m.json": mo_meta, "a.dat": a_c, "bc.dat": b_c}.items():
            zf.writestr(p, d)
    return QVFReader(Path(tmp.name)), Path(tmp.name)


def test_unrestricted_mo_table_has_unique_composite_values():
    """Regression for A2-03: alpha/beta blocks both run index 0..N, so the
    picker must key on a unique composite value, not the bare integer."""
    reader, path = _unrestricted_wf_reader()
    try:
        from vibeview.renderers.wavefunction import WavefunctionRenderer
        rows = WavefunctionRenderer(reader.get_section("wf"), reader).mo_table()
        values = [r["value"] for r in rows]
        assert values == ["alpha:0", "alpha:1", "beta:0", "beta:1"]
        assert len(set(values)) == len(values)  # unique
        assert all(r["title"] for r in rows)     # every row self-describing
        # HOMO marker present for each spin block's occupied frontier.
        assert sum("HOMO" in r["title"] for r in rows) == 2
    finally:
        path.unlink()


def test_unrestricted_per_spin_symmetry_labels():
    """Audit L7: β orbitals must carry their own symmetry labels, not inherit
    α's. The reader used to collapse the two lists (`alpha or beta`) into one
    and the picker indexed that single list for both spins."""
    reader, path = _unrestricted_wf_reader()
    try:
        from vibeview.renderers.wavefunction import WavefunctionRenderer

        # The reader keeps the two lists separate on the dataclass ...
        wf = reader.read_wavefunction_gto("wf")
        assert wf.symmetry_labels == ["Ag", "B1u"]
        assert wf.symmetry_labels_beta == ["B2g", "B3u"]
        # ... and the MO picker labels each spin from its own list.
        rows = WavefunctionRenderer(reader.get_section("wf"), reader).mo_table()
        labels = {r["value"]: r["label"] for r in rows}
        assert labels["alpha:0"] == "Ag"
        assert labels["alpha:1"] == "B1u"
        assert labels["beta:0"] == "B2g"  # was "Ag" before the L7 fix
        assert labels["beta:1"] == "B3u"  # was "B1u" before the L7 fix
    finally:
        path.unlink()


def test_unrestricted_spin_routing_renders_correct_orbital():
    """alpha MO0 lives on atom 0 (origin); beta MO0 lives on atom 1 (x=2 Å).
    The rendered peak must move with spin — proving the picker renders the
    selected spin, not always alpha."""
    reader, path = _unrestricted_wf_reader()
    try:
        from vibeview.renderers.wavefunction import WavefunctionRenderer
        r = WavefunctionRenderer(reader.get_section("wf"), reader)
        _, va = r.evaluate_mo(0, spin="alpha", n_per_dim=24)
        _, vb = r.evaluate_mo(0, spin="beta", n_per_dim=24)
        ia = np.unravel_index(np.argmax(np.abs(va)), va.shape)
        ib = np.unravel_index(np.argmax(np.abs(vb)), vb.shape)
        # alpha peaks near x=0 (low x index), beta near x=2 Å (higher x index)
        assert ia[0] < ib[0]
    finally:
        path.unlink()


def test_parse_mo_key():
    from vibeview.app import _parse_mo_key
    assert _parse_mo_key("alpha:3") == ("alpha", 3)
    assert _parse_mo_key("restricted:0") == ("restricted", 0)
    assert _parse_mo_key(5) == ("restricted", 5)   # legacy bare int
    assert _parse_mo_key(None) == ("restricted", 0)  # robust default


def test_d_z2_has_correct_angular_nodes():
    """The pure d (l=2) m=0 component is the real solid harmonic
    (3z²-r²)/2·(scaled): positive along z, negative in the xy plane. Guards
    that the fix preserved the harmonic, not just its normalization."""
    gx, gy, gz, _ = _grid()
    shell = BasisShell(
        center=0, l=2, exponents=np.array([_ALPHA]),
        coefficients=np.array([1.0]), pure=True,
    )
    angs = WavefunctionRenderer._angular_factors(None, shell, gx, gy, gz)
    dz2 = angs[2]  # m = 0 is the middle of the m=-2..+2 ordering
    # sample on-axis (z) vs in-plane (x): opposite sign for 3z²-r².
    on_z = float(dz2[_N // 2, _N // 2, -1])   # large +z
    on_x = float(dz2[-1, _N // 2, _N // 2])   # large +x
    assert on_z > 0.0 and on_x < 0.0
