"""buffer effect の union に関するテスト群。"""

from __future__ import annotations

import numpy as np

from grafix.api import E, G
from grafix.core.primitive_registry import primitive
from grafix.core.realize import realize
from grafix.core.realized_geometry import RealizedGeometry


@primitive
def buffer_union_test_two_segments_xy() -> RealizedGeometry:
    """XY 平面上の 2 本の線分（別ポリライン）を 1 つの RealizedGeometry で返す。"""
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.5, 0.0, 0.0],
            [1.5, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, 2, 4], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def test_buffer_union_false_keeps_two_outlines() -> None:
    g = G.buffer_union_test_two_segments_xy()
    out = E.buffer(distance=0.1, quad_segs=4, join="round", union=False)(g)
    realized = realize(out)

    assert int(realized.offsets.size) == 3
    assert int(realized.coords.shape[0]) > 2
    np.testing.assert_allclose(realized.coords[:, 2], 0.0, rtol=0.0, atol=1e-6)


def test_buffer_union_true_merges_into_single_outline() -> None:
    g = G.buffer_union_test_two_segments_xy()
    out = E.buffer(distance=0.1, quad_segs=4, join="round", union=True)(g)
    realized = realize(out)

    assert realized.offsets.tolist() == [0, int(realized.coords.shape[0])]
    assert int(realized.coords.shape[0]) > 2
    np.testing.assert_allclose(realized.coords[:, 2], 0.0, rtol=0.0, atol=1e-6)


def test_buffer_union_keep_original_appends_inputs() -> None:
    g = G.buffer_union_test_two_segments_xy()
    out = E.buffer(distance=0.1, quad_segs=4, join="round", union=True, keep_original=True)(g)
    realized = realize(out)

    assert int(realized.offsets.size) == 4  # union outline + 2 originals

    s1 = int(realized.offsets[1])
    s2 = int(realized.offsets[2])
    s3 = int(realized.offsets[3])
    original1 = realized.coords[s1:s2]
    original2 = realized.coords[s2:s3]

    expected1 = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float32)
    expected2 = np.array([[0.5, 0.0, 0.0], [1.5, 0.0, 0.0]], dtype=np.float32)
    np.testing.assert_allclose(original1, expected1, rtol=0.0, atol=1e-6)
    np.testing.assert_allclose(original2, expected2, rtol=0.0, atol=1e-6)
