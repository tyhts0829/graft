"""dash effect の破線化に関するテスト群。"""

from __future__ import annotations

import numpy as np

from grafix.api import E, G
from grafix.core.primitive_registry import primitive
from grafix.core.realize import realize
from grafix.core.realized_geometry import RealizedGeometry


@primitive
def dash_test_line_0_10() -> RealizedGeometry:
    """x 軸上の 2 点ポリライン（長さ 10）を返す。"""
    coords = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def dash_test_empty() -> RealizedGeometry:
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


def test_dash_straight_line_generates_expected_segments() -> None:
    g = G.dash_test_line_0_10()
    dashed = E.dash(dash_length=2.0, gap_length=1.0, offset=0.0, offset_jitter=0.0)(g)
    realized = realize(dashed)

    segments = list(_iter_polylines(realized))
    assert len(segments) == 4

    expected = [(0.0, 2.0), (3.0, 5.0), (6.0, 8.0), (9.0, 10.0)]
    for seg, (x0, x1) in zip(segments, expected, strict=True):
        np.testing.assert_allclose([seg[0, 0], seg[-1, 0]], [x0, x1], rtol=0.0, atol=1e-6)
        np.testing.assert_allclose(seg[:, 1], 0.0, rtol=0.0, atol=1e-6)
        np.testing.assert_allclose(seg[:, 2], 0.0, rtol=0.0, atol=1e-6)


def test_dash_offset_shifts_phase() -> None:
    g = G.dash_test_line_0_10()
    dashed = E.dash(dash_length=2.0, gap_length=1.0, offset=1.0, offset_jitter=0.0)(g)
    realized = realize(dashed)

    segments = list(_iter_polylines(realized))
    assert len(segments) == 4

    expected = [(0.0, 1.0), (2.0, 4.0), (5.0, 7.0), (8.0, 10.0)]
    for seg, (x0, x1) in zip(segments, expected, strict=True):
        np.testing.assert_allclose([seg[0, 0], seg[-1, 0]], [x0, x1], rtol=0.0, atol=1e-6)


def test_dash_invalid_pattern_is_noop() -> None:
    g = G.dash_test_line_0_10()
    base = realize(g)

    dashed = E.dash(dash_length=2.0, gap_length=-1.0, offset=0.0, offset_jitter=0.0)(g)
    realized = realize(dashed)

    np.testing.assert_allclose(realized.coords, base.coords, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == base.offsets.tolist()


def test_dash_empty_geometry_is_noop() -> None:
    g = G.dash_test_empty()
    dashed = E.dash(dash_length=2.0, gap_length=1.0, offset=0.0, offset_jitter=0.0)(g)
    realized = realize(dashed)

    assert realized.coords.shape == (0, 3)
    assert realized.offsets.tolist() == [0]

