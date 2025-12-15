"""subdivide effect の線細分化に関するテスト群。"""

from __future__ import annotations

import numpy as np

from src.api import E, G
from src.core.primitive_registry import primitive
from src.core.realize import realize
from src.core.realized_geometry import RealizedGeometry


@primitive
def subdivide_test_line_0_10() -> RealizedGeometry:
    """x 軸上の 2 点ポリライン（長さ 10）を返す。"""
    coords = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def subdivide_test_short_segment() -> RealizedGeometry:
    """最短セグメント長ガード確認用の極短 2 点ポリラインを返す。"""
    coords = np.array([[0.0, 0.0, 0.0], [0.005, 0.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def subdivide_test_two_lines() -> RealizedGeometry:
    """2 本の独立ポリライン（長さ 10 と 2）を返す。"""
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            [2.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, 2, 4], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def subdivide_test_empty() -> RealizedGeometry:
    """空ジオメトリを返す。"""
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _iter_polylines(realized: RealizedGeometry):
    offsets = realized.offsets
    for i in range(len(offsets) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        yield realized.coords[s:e]


def test_subdivide_inserts_midpoint() -> None:
    g = G.subdivide_test_line_0_10()
    realized = realize(E.subdivide(subdivisions=1)(g))

    polylines = list(_iter_polylines(realized))
    assert len(polylines) == 1
    line = polylines[0]
    assert line.shape == (3, 3)
    np.testing.assert_allclose(line[:, 0], [0.0, 5.0, 10.0], rtol=0.0, atol=1e-6)


def test_subdivide_two_iterations_increases_vertex_count() -> None:
    g = G.subdivide_test_line_0_10()
    realized = realize(E.subdivide(subdivisions=2)(g))

    polylines = list(_iter_polylines(realized))
    assert len(polylines) == 1
    line = polylines[0]
    assert line.shape == (5, 3)
    np.testing.assert_allclose(line[:, 0], [0.0, 2.5, 5.0, 7.5, 10.0], rtol=0.0, atol=1e-6)


def test_subdivide_default_is_noop() -> None:
    g = G.subdivide_test_line_0_10()
    base = realize(g)
    realized = realize(E.subdivide()(g))

    np.testing.assert_allclose(realized.coords, base.coords, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == base.offsets.tolist()


def test_subdivide_short_segment_guard_is_noop() -> None:
    g = G.subdivide_test_short_segment()
    base = realize(g)
    realized = realize(E.subdivide(subdivisions=10)(g))

    np.testing.assert_allclose(realized.coords, base.coords, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == base.offsets.tolist()


def test_subdivide_multiple_polylines_preserves_offsets() -> None:
    g = G.subdivide_test_two_lines()
    realized = realize(E.subdivide(subdivisions=1)(g))

    polylines = list(_iter_polylines(realized))
    assert len(polylines) == 2
    assert realized.offsets.tolist() == [0, 3, 6]
    np.testing.assert_allclose(polylines[0][:, 0], [0.0, 5.0, 10.0], rtol=0.0, atol=1e-6)
    np.testing.assert_allclose(polylines[1][:, 0], [0.0, 1.0, 2.0], rtol=0.0, atol=1e-6)


def test_subdivide_empty_geometry_is_noop() -> None:
    g = G.subdivide_test_empty()
    realized = realize(E.subdivide(subdivisions=1)(g))

    assert realized.coords.shape == (0, 3)
    assert realized.offsets.tolist() == [0]

