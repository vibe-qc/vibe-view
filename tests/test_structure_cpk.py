"""CPK colour / radius by atomic number.

Regression for the heavy-element rendering bug: the renderers derived the
atomic number from the element symbol via ``_symbol_to_num`` (which stops at
Ba, Z=56), so every heavier element rendered as the hot-pink fallback with a
flat default radius — even though the CPK colour table reaches Z=96 and the
parsed ``Atom.atomic_number`` was available all along.
"""

from __future__ import annotations

import pytest

from vibeview.renderers.structure import _DEFAULT_COLOR, cpk_color, cpk_radius


@pytest.mark.parametrize(
    "z,name", [(26, "Fe"), (29, "Cu"), (57, "La"), (74, "W"),
               (78, "Pt"), (79, "Au"), (82, "Pb"), (92, "U")]
)
def test_heavy_elements_get_real_cpk_colour(z, name):
    assert cpk_color(z) != _DEFAULT_COLOR, f"{name} (Z={z}) fell to hot-pink"


@pytest.mark.parametrize("z", [21, 26, 47, 74, 79, 92])
def test_heavy_elements_get_nonflat_radius(z):
    # Outside the small vdW table they used to all be 1.70 Å; now they vary.
    r = cpk_radius(z, 1.0)
    assert 0.5 < r < 3.6
    assert r != 1.70  # not the old flat default


def test_cpk_radius_scales():
    assert cpk_radius(6, 0.5) == pytest.approx(cpk_radius(6, 1.0) * 0.5)


def test_unknown_z_is_hot_pink_fallback():
    assert cpk_color(0) == _DEFAULT_COLOR
    assert cpk_color(200) == _DEFAULT_COLOR
