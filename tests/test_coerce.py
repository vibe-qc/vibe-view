"""renderers._coerce turns schema-free JSON fields into numbers without raising."""

from __future__ import annotations

import math

import numpy as np
import pytest

from vibeview.renderers._coerce import as_float, as_int, as_str_list, as_vector


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1.0),
        (2.5, 2.5),
        ("3.25", 3.25),
        (np.float32(0.5), 0.5),
        (np.int64(7), 7.0),
        (None, None),
        (True, None),
        ("abc", None),
        ([1.0], None),
        ({"a": 1}, None),
    ],
)
def test_as_float(value, expected) -> None:
    assert as_float(value) == expected


def test_as_float_default_and_nan() -> None:
    assert as_float(None, 0.0) == 0.0
    assert as_float("x", -1.0) == -1.0
    assert math.isnan(as_float("nan", 0.0))
    assert math.isnan(as_float(None, float("nan")))


@pytest.mark.parametrize(
    ("value", "expected"),
    [(3, 3), (3.9, 3), ("4", 4), ("4.5", 4), (np.int64(5), 5), (None, None), (True, None)],
)
def test_as_int(value, expected) -> None:
    assert as_int(value) == expected


def test_as_int_rejects_non_finite() -> None:
    assert as_int("nan", 0) == 0
    assert as_int(float("inf"), 9) == 9


def test_as_vector() -> None:
    assert as_vector([1, "2", 3.5]) == [1.0, 2.0, 3.5]
    assert as_vector((0, 1)) == [0.0, 1.0]
    assert as_vector([1, None]) is None
    assert as_vector("12") is None
    assert as_vector(None) is None


def test_as_str_list() -> None:
    assert as_str_list("a") == ["a"]
    assert as_str_list(["a", 1]) == ["a", "1"]
    assert as_str_list(None) == []
    assert as_str_list(True) == []
    assert as_str_list(3) == ["3"]


def test_no_module_keeps_a_private_coercer() -> None:
    from vibeview.renderers import bond_orders, qtaim
    from vibeview.tui import panes, plots

    for module in (bond_orders, qtaim, panes, plots):
        for name in ("_as_float", "_as_int", "_as_vector", "_num"):
            assert not hasattr(module, name), (module.__name__, name)
