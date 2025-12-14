"""fill effect のハッチ生成に関するテスト群。"""

from __future__ import annotations

import numpy as np

from src.api import E, G
from src.core.primitive_registry import primitive
from src.core.realize import realize
from src.core.realized_geometry import RealizedGeometry


@primitive
def fill_test_square() -> RealizedGeometry:
    """一辺 10 の正方形（閉ポリライン）を返す。"""
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
            [0.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, coords.shape[0]], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def fill_test_square_with_hole() -> RealizedGeometry:
    """外周+穴（2 輪郭）の正方形を返す。"""
    outer = np.array(
        [
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
            [0.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    hole = np.array(
        [
            [3.0, 3.0, 0.0],
            [7.0, 3.0, 0.0],
            [7.0, 7.0, 0.0],
            [3.0, 7.0, 0.0],
            [3.0, 3.0, 0.0],
        ],
        dtype=np.float32,
    )
    coords = np.concatenate([outer, hole], axis=0)
    offsets = np.array([0, outer.shape[0], outer.shape[0] + hole.shape[0]], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def fill_test_empty() -> RealizedGeometry:
    """空のジオメトリを返す。"""
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _iter_polylines(realized: RealizedGeometry):
    offsets = realized.offsets
    for i in range(len(offsets) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        yield realized.coords[s:e]


def test_fill_square_generates_expected_line_count() -> None:
    g = G.fill_test_square()
    filled = E.fill(angle_sets=1, angle=0.0, density=10.0, remove_boundary=True)(g)
    realized = realize(filled)

    assert len(realized.offsets) - 1 == 10
    assert realized.coords.shape == (20, 3)
    for seg in _iter_polylines(realized):
        assert seg.shape == (2, 3)
        assert float(seg[0, 1]) == float(seg[1, 1])


def test_fill_remove_boundary_false_keeps_input() -> None:
    g = G.fill_test_square()
    filled = E.fill(angle_sets=1, angle=0.0, density=10.0, remove_boundary=False)(g)
    realized = realize(filled)

    assert len(realized.offsets) - 1 == 11
    first = next(_iter_polylines(realized))
    np.testing.assert_allclose(first, realize(g).coords, rtol=0.0, atol=1e-6)


def test_fill_outer_with_hole_avoids_hole_region() -> None:
    g = G.fill_test_square_with_hole()
    filled = E.fill(angle_sets=1, angle=0.0, density=10.0, remove_boundary=True)(g)
    realized = realize(filled)

    # y=0..9 の 10 本のうち、穴の y 範囲 [3,7) に入る 4 本は 2 セグメントに分割される。
    assert len(realized.offsets) - 1 == 14

    for seg in _iter_polylines(realized):
        mid = seg.mean(axis=0)
        assert not (3.0 < float(mid[0]) < 7.0 and 3.0 < float(mid[1]) < 7.0)


def test_fill_empty_geometry_is_noop() -> None:
    g = G.fill_test_empty()
    filled = E.fill(angle_sets=2, angle=0.0, density=10.0, remove_boundary=True)(g)
    realized = realize(filled)

    assert realized.coords.shape == (0, 3)
    assert realized.offsets.tolist() == [0]

