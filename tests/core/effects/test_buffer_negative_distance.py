"""buffer effect の distance<0（内側輪郭）に関するテスト群。"""

from __future__ import annotations

import numpy as np

from grafix.api import E, G
from grafix.core.primitive_registry import primitive
from grafix.core.realize import realize
from grafix.core.realized_geometry import RealizedGeometry


@primitive
def buffer_negative_test_square_ring_xy() -> RealizedGeometry:
    """XY 平面上の正方形リング（閉曲線）を 1 本返す。"""
    coords = np.array(
        [
            [-1.0, -1.0, 0.0],
            [1.0, -1.0, 0.0],
            [1.0, 1.0, 0.0],
            [-1.0, 1.0, 0.0],
            [-1.0, -1.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, 5], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def buffer_negative_test_segment_xy() -> RealizedGeometry:
    """XY 平面上の 2 点線分を返す。"""
    coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def test_buffer_negative_distance_square_yields_inner_outline() -> None:
    g = G.buffer_negative_test_square_ring_xy()
    out = E.buffer(distance=-0.1, quad_segs=4, join="round")(g)
    realized = realize(out)

    assert realized.offsets.tolist() == [0, int(realized.coords.shape[0])]
    assert realized.coords.shape[0] > 2

    xs = realized.coords[:, 0]
    ys = realized.coords[:, 1]
    zs = realized.coords[:, 2]
    assert float(xs.min()) > -0.95
    assert float(xs.max()) < 0.95
    assert float(ys.min()) > -0.95
    assert float(ys.max()) < 0.95
    np.testing.assert_allclose(zs, 0.0, rtol=0.0, atol=1e-6)


def test_buffer_distance_sign_changes_outline_side() -> None:
    g = G.buffer_negative_test_square_ring_xy()

    r_pos = realize(E.buffer(distance=0.1, quad_segs=4, join="round")(g))
    xs_pos = r_pos.coords[:, 0]
    ys_pos = r_pos.coords[:, 1]
    assert float(xs_pos.min()) < -1.05
    assert float(xs_pos.max()) > 1.05
    assert float(ys_pos.min()) < -1.05
    assert float(ys_pos.max()) > 1.05

    r_neg = realize(E.buffer(distance=-0.1, quad_segs=4, join="round")(g))
    xs_neg = r_neg.coords[:, 0]
    ys_neg = r_neg.coords[:, 1]
    assert float(xs_neg.min()) > -0.95
    assert float(xs_neg.max()) < 0.95
    assert float(ys_neg.min()) > -0.95
    assert float(ys_neg.max()) < 0.95


def test_buffer_negative_distance_open_segment_is_empty() -> None:
    g = G.buffer_negative_test_segment_xy()
    out = E.buffer(distance=-0.1, quad_segs=4)(g)
    realized = realize(out)

    assert realized.coords.shape == (0, 3)
    assert realized.offsets.tolist() == [0]


def test_buffer_negative_distance_keep_original_appends_input() -> None:
    g = G.buffer_negative_test_segment_xy()
    out = E.buffer(distance=-0.1, quad_segs=4, keep_original=True)(g)
    realized = realize(out)

    expected = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float32)
    np.testing.assert_allclose(realized.coords, expected, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == [0, 2]
