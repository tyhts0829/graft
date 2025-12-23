"""Geometry の + 演算子（concat）テスト。"""

from __future__ import annotations

import pytest

from grafix.core.geometry import Geometry


def _g(name: str) -> Geometry:
    return Geometry.create(name, params={"x": 1.0})


def test_add_creates_concat() -> None:
    a = _g("a")
    b = _g("b")
    c = a + b
    assert isinstance(c, Geometry)
    assert c.op == "concat"
    assert c.inputs == (a, b)
    assert c.args == ()


def test_add_flattens_concat_left_associative() -> None:
    a = _g("a")
    b = _g("b")
    c = _g("c")
    combined = (a + b) + c
    assert combined.op == "concat"
    assert combined.inputs == (a, b, c)


def test_add_flattens_concat_right_associative() -> None:
    a = _g("a")
    b = _g("b")
    c = _g("c")
    combined = a + (b + c)
    assert combined.op == "concat"
    assert combined.inputs == (a, b, c)


def test_sum_works() -> None:
    a = _g("a")
    b = _g("b")
    c = _g("c")
    combined = sum([a, b, c])
    assert combined.op == "concat"
    assert combined.inputs == (a, b, c)


def test_add_raises_on_invalid_type() -> None:
    a = _g("a")
    with pytest.raises(TypeError):
        _ = a + 1  # type: ignore[operator]
