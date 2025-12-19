"""trim effect（区間トリム）の実体変換に関するテスト群。"""

from __future__ import annotations

import numpy as np

from grafix.api import E, G
from grafix.core.primitive_registry import primitive
from grafix.core.realize import realize
from grafix.core.realized_geometry import RealizedGeometry


@primitive
def trim_test_line_0_10() -> RealizedGeometry:
    """X 軸上の 2 点直線（0→10）を返す。"""
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, coords.shape[0]], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def trim_test_two_lines() -> RealizedGeometry:
    """2 本の 2 点直線（X と Y）を返す。"""
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            [0.0, 20.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, 2, 4], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def trim_test_empty() -> RealizedGeometry:
    """空のジオメトリを返す。"""
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def trim_test_tiny_line() -> RealizedGeometry:
    """極端に短い 2 点直線を返す（全線が消えるケースの確認用）。"""
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [1e-9, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, coords.shape[0]], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def test_trim_interpolates_endpoints_on_simple_line() -> None:
    g = G.trim_test_line_0_10()
    out = realize(E.trim(start_param=0.25, end_param=0.75)(g))

    assert out.offsets.tolist() == [0, 2]
    assert out.coords.shape == (2, 3)
    np.testing.assert_allclose(out.coords[0], [2.5, 0.0, 0.0], rtol=0.0, atol=1e-6)
    np.testing.assert_allclose(out.coords[1], [7.5, 0.0, 0.0], rtol=0.0, atol=1e-6)


def test_trim_noop_for_full_range() -> None:
    g = G.trim_test_line_0_10()
    base = realize(g)
    out = realize(E.trim(start_param=0.0, end_param=1.0)(g))

    np.testing.assert_allclose(out.coords, base.coords, rtol=0.0, atol=0.0)
    assert out.offsets.tolist() == base.offsets.tolist()


def test_trim_noop_when_start_ge_end() -> None:
    g = G.trim_test_line_0_10()
    base = realize(g)
    out = realize(E.trim(start_param=0.9, end_param=0.2)(g))

    np.testing.assert_allclose(out.coords, base.coords, rtol=0.0, atol=0.0)
    assert out.offsets.tolist() == base.offsets.tolist()


def test_trim_clamps_params() -> None:
    g = G.trim_test_line_0_10()
    out = realize(E.trim(start_param=-1.0, end_param=0.5)(g))

    assert out.offsets.tolist() == [0, 2]
    np.testing.assert_allclose(out.coords[0], [0.0, 0.0, 0.0], rtol=0.0, atol=0.0)
    np.testing.assert_allclose(out.coords[1], [5.0, 0.0, 0.0], rtol=0.0, atol=1e-6)


def test_trim_handles_multiple_polylines_independently() -> None:
    g = G.trim_test_two_lines()
    out = realize(E.trim(start_param=0.5, end_param=1.0)(g))

    assert out.offsets.tolist() == [0, 2, 4]
    assert out.coords.shape == (4, 3)
    np.testing.assert_allclose(out.coords[0], [5.0, 0.0, 0.0], rtol=0.0, atol=1e-6)
    np.testing.assert_allclose(out.coords[1], [10.0, 0.0, 0.0], rtol=0.0, atol=1e-6)
    np.testing.assert_allclose(out.coords[2], [0.0, 10.0, 0.0], rtol=0.0, atol=1e-6)
    np.testing.assert_allclose(out.coords[3], [0.0, 20.0, 0.0], rtol=0.0, atol=1e-6)


def test_trim_empty_geometry_is_noop() -> None:
    g = G.trim_test_empty()
    out = realize(E.trim(start_param=0.25, end_param=0.75)(g))

    assert out.coords.shape == (0, 3)
    assert out.offsets.tolist() == [0]


def test_trim_noop_when_all_lines_would_disappear() -> None:
    g = G.trim_test_tiny_line()
    base = realize(g)
    out = realize(E.trim(start_param=0.1, end_param=0.2)(g))

    np.testing.assert_allclose(out.coords, base.coords, rtol=0.0, atol=0.0)
    assert out.offsets.tolist() == base.offsets.tolist()
