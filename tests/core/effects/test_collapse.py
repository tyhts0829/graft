"""collapse effect の実体変換に関するテスト群。"""

from __future__ import annotations

import numpy as np

from src.api import E, G
from src.core.primitive_registry import primitive
from src.core.realize import realize
from src.core.realized_geometry import RealizedGeometry


@primitive
def collapse_test_line2_x_a() -> RealizedGeometry:
    """x 軸上の 2 点ポリラインを返す。"""
    coords = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def collapse_test_line2_x_b() -> RealizedGeometry:
    """`collapse_test_line2_x_a` と同一の 2 点ポリラインを返す。"""
    coords = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def collapse_test_zero_length_segment() -> RealizedGeometry:
    """ゼロ長セグメント（同一点2点）を返す。"""
    coords = np.array([[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _iter_segments(realized: RealizedGeometry):
    offsets = realized.offsets
    for i in range(int(offsets.size) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        yield realized.coords[s:e]


def test_collapse_subdivisions_zero_is_noop() -> None:
    g = G.collapse_test_line2_x_a()
    collapsed = E.collapse(intensity=5.0, subdivisions=0)(g)
    realized = realize(collapsed)

    expected = realize(g)
    np.testing.assert_allclose(realized.coords, expected.coords, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == expected.offsets.tolist()


def test_collapse_intensity_zero_is_noop() -> None:
    g = G.collapse_test_line2_x_a()
    collapsed = E.collapse(intensity=0.0, subdivisions=6)(g)
    realized = realize(collapsed)

    expected = realize(g)
    np.testing.assert_allclose(realized.coords, expected.coords, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == expected.offsets.tolist()


def test_collapse_outputs_non_connected_segments() -> None:
    g = G.collapse_test_line2_x_a()
    divisions = 4
    collapsed = E.collapse(intensity=1.0, subdivisions=divisions)(g)
    realized = realize(collapsed)

    assert len(realized.offsets) - 1 == divisions
    assert realized.coords.shape == (2 * divisions, 3)
    assert realized.offsets.tolist() == [0, 2, 4, 6, 8]

    # 各サブセグメントは 2 点で、平行移動量は 2 点で一致する。
    for seg in _iter_segments(realized):
        assert seg.shape == (2, 3)
        assert float(seg[0, 1]) == float(seg[1, 1])
        assert float(seg[0, 2]) == float(seg[1, 2])

    # 入力が x 軸直線のため、x 成分は線形分割位置のまま不変になる。
    expected_x = np.array([0.0, 2.5, 2.5, 5.0, 5.0, 7.5, 7.5, 10.0], dtype=np.float32)
    np.testing.assert_allclose(realized.coords[:, 0], expected_x, rtol=0.0, atol=1e-6)


def test_collapse_is_deterministic_for_same_input() -> None:
    g1 = G.collapse_test_line2_x_a()
    g2 = G.collapse_test_line2_x_b()

    divisions = 6
    collapsed1 = E.collapse(intensity=2.0, subdivisions=divisions)(g1)
    collapsed2 = E.collapse(intensity=2.0, subdivisions=divisions)(g2)
    r1 = realize(collapsed1)
    r2 = realize(collapsed2)

    np.testing.assert_allclose(r1.coords, r2.coords, rtol=0.0, atol=1e-6)
    assert r1.offsets.tolist() == r2.offsets.tolist()


def test_collapse_zero_length_segment_is_kept() -> None:
    g = G.collapse_test_zero_length_segment()
    collapsed = E.collapse(intensity=5.0, subdivisions=4)(g)
    realized = realize(collapsed)

    expected = realize(g)
    np.testing.assert_allclose(realized.coords, expected.coords, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == expected.offsets.tolist()

