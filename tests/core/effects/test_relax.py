"""relax effect の実体変換に関するテスト群。"""

from __future__ import annotations

import numpy as np

from grafix.api import E, G
from grafix.core.primitive_registry import primitive
from grafix.core.realize import realize
from grafix.core.realized_geometry import RealizedGeometry


@primitive
def relax_test_chain5() -> RealizedGeometry:
    """固定点が存在する鎖状グラフを返す（中央点が移動可能）。"""
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 2.0, 0.0],
            [2.0, 1.0, 0.0],
            [3.0, -2.0, 0.0],
            [4.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, coords.shape[0]], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def relax_test_shared_point() -> RealizedGeometry:
    """共有点を 2 回出現させたポリライン集合を返す。"""
    coords = np.array(
        [
            [-1.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            [1.0, 1.0, 0.0],
            [2.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, 2, 5], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def test_relax_zero_iterations_is_noop() -> None:
    g = G.relax_test_chain5()
    base = realize(g)
    out = realize(E.relax(relaxation_iterations=0, step=0.5)(g))
    assert out is base


def test_relax_moves_only_non_fixed_nodes() -> None:
    g = G.relax_test_chain5()
    base = realize(g)
    out = realize(E.relax(relaxation_iterations=1, step=0.5)(g))

    np.testing.assert_allclose(out.coords[0], base.coords[0], rtol=0.0, atol=1e-6)
    np.testing.assert_allclose(out.coords[1], base.coords[1], rtol=0.0, atol=1e-6)
    np.testing.assert_allclose(out.coords[3], base.coords[3], rtol=0.0, atol=1e-6)
    np.testing.assert_allclose(out.coords[4], base.coords[4], rtol=0.0, atol=1e-6)

    expected_center = np.array([2.0, 0.0, 0.0], dtype=np.float32)
    np.testing.assert_allclose(out.coords[2], expected_center, rtol=0.0, atol=1e-6)


def test_relax_keeps_shared_points_identical() -> None:
    g = G.relax_test_shared_point()
    out = realize(E.relax(relaxation_iterations=1, step=0.5)(g))

    np.testing.assert_allclose(out.coords[1], out.coords[2], rtol=0.0, atol=1e-6)
    np.testing.assert_allclose(out.coords[1], [0.0, 0.5, 0.0], rtol=0.0, atol=1e-6)
    assert out.offsets.tolist() == [0, 2, 5]

