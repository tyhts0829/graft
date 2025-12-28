"""buffer effect の実体変換に関するテスト群。"""

from __future__ import annotations

import numpy as np

from grafix.api import E, G
from grafix.core.primitive_registry import primitive
from grafix.core.realize import realize
from grafix.core.realized_geometry import RealizedGeometry


@primitive
def buffer_test_segment_xy() -> RealizedGeometry:
    """xy 平面上の 2 点線分を返す。"""
    coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def buffer_test_segment_xz() -> RealizedGeometry:
    """xz 平面上の 2 点線分を返す（y=0 固定）。"""
    coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 1.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def test_buffer_distance_zero_is_noop() -> None:
    g = G.buffer_test_segment_xy()
    out = E.buffer(distance=0.0)(g)
    realized = realize(out)

    expected = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float32)
    np.testing.assert_allclose(realized.coords, expected, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == [0, 2]


def test_buffer_expands_bounds_xy() -> None:
    g = G.buffer_test_segment_xy()
    out = E.buffer(distance=0.1, quad_segs=4, join="round")(g)
    realized = realize(out)

    assert realized.offsets.tolist() == [0, int(realized.coords.shape[0])]
    assert realized.coords.shape[0] > 2

    xs = realized.coords[:, 0]
    ys = realized.coords[:, 1]
    zs = realized.coords[:, 2]
    assert float(xs.min()) < -0.09
    assert float(xs.max()) > 1.09
    assert float(ys.min()) < -0.09
    assert float(ys.max()) > 0.09
    np.testing.assert_allclose(zs, 0.0, rtol=0.0, atol=1e-6)


def test_buffer_keep_original_appends_input() -> None:
    g = G.buffer_test_segment_xy()
    out = E.buffer(distance=0.1, quad_segs=4, keep_original=True)(g)
    realized = realize(out)

    assert int(realized.offsets.size) == 3  # buffered + original
    s1 = int(realized.offsets[1])
    e1 = int(realized.offsets[2])
    original = realized.coords[s1:e1]

    expected = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float32)
    np.testing.assert_allclose(original, expected, rtol=0.0, atol=1e-6)


def test_buffer_preserves_plane_for_xz_segment() -> None:
    g = G.buffer_test_segment_xz()
    out = E.buffer(distance=0.1, quad_segs=4)(g)
    realized = realize(out)

    ys = realized.coords[:, 1]
    np.testing.assert_allclose(ys, 0.0, rtol=0.0, atol=1e-6)
