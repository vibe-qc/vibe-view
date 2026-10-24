"""Gaussian/Molden AO ordering and component normalization into QVF A.1.

Gaussian formchk documentation and Molden's format specification define the
source orders. QVF Cartesian AOs use a shell-wide axial primitive norm, while
fchk uses individually normalized Cartesian components. Molden uses the
shell-wide convention; the explicit switch keeps the two contracts separate.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

_CART = {
    0: [(0, 0, 0)],
    1: [(1, 0, 0), (0, 1, 0), (0, 0, 1)],
    2: [(2, 0, 0), (0, 2, 0), (0, 0, 2), (1, 1, 0), (1, 0, 1), (0, 1, 1)],
    3: [(3, 0, 0), (0, 3, 0), (0, 0, 3), (1, 2, 0), (2, 1, 0),
        (2, 0, 1), (1, 0, 2), (0, 1, 2), (0, 2, 1), (1, 1, 1)],
}


def _df(n: int) -> int:
    return math.prod(range(n, 0, -2))


def ao_transform(shells: list[dict[str, Any]], *, normalized_components: bool = True
                 ) -> tuple[list[int], np.ndarray]:
    """Return source indices/scales for C_qvf = C_source[:, indices] * scales."""
    order: list[int] = []
    scales: list[float] = []
    offset = 0
    for shell in shells:
        angular = int(shell['l'])
        if angular not in _CART:
            raise ValueError("Native wavefunction imports support s, p, d and f shells; "
                             "export a cube for higher angular momentum")
        if shell['pure'] and angular > 1:
            source_m = [0] + [m for k in range(1, angular+1) for m in (k, -k)]
            local = [source_m.index(m) for m in range(-angular, angular+1)]
            factor = [1.] * len(local)
        elif shell['pure'] and angular == 1:
            local, factor = [1, 2, 0], [1.] * 3  # x,y,z -> y,z,x
        else:
            target = [(i, j, angular-i-j) for i in range(angular, -1, -1)
                      for j in range(angular-i, -1, -1)]
            local = [_CART[angular].index(powers) for powers in target]
            factor = ([math.sqrt(_df(2*angular-1)/math.prod(_df(2*k-1) for k in powers))
                       for powers in target] if normalized_components else [1.] * len(target))
        order.extend(offset+i for i in local)
        scales.extend(factor)
        offset += len(local)
    return order, np.asarray(scales)


def validate_shell(center: int, angular: int, pure: bool,
                   exponents: list[float], coefficients: list[float], n_atoms: int
                   ) -> dict[str, Any]:
    if center < 0 or center >= n_atoms:
        raise ValueError("Basis shell atom index is outside the structure")
    alpha, coeff = np.asarray(exponents), np.asarray(coefficients)
    if (alpha.ndim != 1 or len(alpha) == 0 or coeff.shape != alpha.shape
            or not np.isfinite(alpha).all() or not np.isfinite(coeff).all()
            or np.any(alpha <= 0) or not np.any(coeff)):
        raise ValueError("Basis shell needs positive finite exponents "
                         "and finite nonzero coefficients")
    shell = {"center": center, "l": angular, "pure": bool(pure),
             "exponents": alpha.tolist(), "coefficients": coeff.tolist()}
    ao_transform([shell])
    return shell
