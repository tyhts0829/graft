"""partition effect（Voronoi 分割）の実体変換に関するテスト群。"""

from __future__ import annotations

import numpy as np

from src.api import G
from src.core.primitive_registry import primitive
from src.core.realize import realize
from src.core.realized_geometry import RealizedGeometry
from src.effects.partition import partition as partition_impl
from src.effects.rotate import rotate as rotate_impl


@primitive
def partition_test_square_0_10() -> RealizedGeometry:
    """XY 平面上の 10x10 正方形（閉ループ）を返す。"""
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


def _iter_polylines(realized: RealizedGeometry):
    offsets = realized.offsets
    for i in range(len(offsets) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        yield realized.coords[s:e]


def test_partition_produces_closed_loops() -> None:
    base = realize(G.partition_test_square_0_10())
    out = partition_impl([base], site_count=12, seed=0)

    assert out.coords.shape[1] == 3
    assert out.offsets.size >= 2
    assert out.coords.shape[0] > 0

    loops = list(_iter_polylines(out))
    assert loops
    for loop in loops:
        assert loop.shape[0] >= 4
        np.testing.assert_allclose(loop[0], loop[-1], rtol=0.0, atol=1e-6)


def test_partition_deterministic_for_same_inputs() -> None:
    base = realize(G.partition_test_square_0_10())

    out1 = partition_impl([base], site_count=32, seed=123)
    out2 = partition_impl([base], site_count=32, seed=123)

    np.testing.assert_allclose(out1.coords, out2.coords, rtol=0.0, atol=0.0)
    assert out1.offsets.tolist() == out2.offsets.tolist()


def test_partition_works_for_tilted_input_plane() -> None:
    base = realize(G.partition_test_square_0_10())
    tilted = rotate_impl([base], auto_center=True, rotation=(35.0, 20.0, 0.0))
    out = partition_impl([tilted], site_count=12, seed=0)

    assert out.coords.shape[0] > 0
    assert float(np.max(np.abs(out.coords[:, 2]))) > 1e-3

