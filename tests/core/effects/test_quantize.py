"""quantize effect の実体変換に関するテスト群。"""

from __future__ import annotations

import numpy as np

from grafix.api import E, G
from grafix.core.primitive_registry import primitive
from grafix.core.realize import realize
from grafix.core.realized_geometry import RealizedGeometry


@primitive
def quantize_test_halves_xyz() -> RealizedGeometry:
    """0.5 境界を含む座標を持つ 4 点ポリラインを返す。"""
    coords = np.array(
        [
            [-0.5, 0.5, 1.5],
            [0.49, -0.49, -1.5],
            [-1.49, 1.49, -0.5],
            [1.0, -2.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, 4], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def quantize_test_vec_step_xyz() -> RealizedGeometry:
    """軸別ステップ検証用の 2 点ポリラインを返す。"""
    coords = np.array([[2.4, 0.74, -0.6], [1.0, 0.25, 0.5]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def test_quantize_half_away_from_zero_xyz() -> None:
    g = G.quantize_test_halves_xyz()
    snapped = E.quantize(step=(1.0, 1.0, 1.0))(g)
    realized = realize(snapped)

    expected = np.array(
        [
            [-1.0, 1.0, 2.0],
            [0.0, 0.0, -2.0],
            [-1.0, 1.0, -1.0],
            [1.0, -2.0, 0.0],
        ],
        dtype=np.float32,
    )
    np.testing.assert_allclose(realized.coords, expected, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == [0, 4]


def test_quantize_vec_step_xyz() -> None:
    g = G.quantize_test_vec_step_xyz()
    snapped = E.quantize(step=(2.0, 0.5, 1.0))(g)
    realized = realize(snapped)

    expected = np.array([[2.0, 0.5, -1.0], [2.0, 0.5, 1.0]], dtype=np.float32)
    np.testing.assert_allclose(realized.coords, expected, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == [0, 2]


def test_quantize_step_non_positive_is_noop() -> None:
    g = G.quantize_test_vec_step_xyz()
    snapped = E.quantize(step=(0.0, 0.5, 1.0))(g)
    realized = realize(snapped)

    expected = np.array([[2.4, 0.74, -0.6], [1.0, 0.25, 0.5]], dtype=np.float32)
    np.testing.assert_allclose(realized.coords, expected, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == [0, 2]

