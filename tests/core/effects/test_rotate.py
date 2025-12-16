"""rotate effect の実体変換に関するテスト群。"""

from __future__ import annotations

import numpy as np

from src.api import E, G
from src.core.primitive_registry import primitive
from src.core.realize import realize
from src.core.realized_geometry import RealizedGeometry


@primitive
def rotate_test_line3() -> RealizedGeometry:
    """x 軸上の 3 点ポリラインを返す。"""
    coords = np.array([[1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [3.0, 0.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 3], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def rotate_test_line2() -> RealizedGeometry:
    """x 軸上の 2 点ポリラインを返す。"""
    coords = np.array([[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def rotate_test_line_centered() -> RealizedGeometry:
    """中心 (2,0,0) を持つ 2 点ポリラインを返す。"""
    coords = np.array([[1.0, 0.0, 0.0], [3.0, 0.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def rotate_test_empty() -> RealizedGeometry:
    """空のジオメトリを返す。"""
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def test_rotate_z_90_about_origin() -> None:
    g = G.rotate_test_line3()
    rotated = E.rotate(auto_center=False, pivot=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 90.0))(g)
    realized = realize(rotated)

    expected = np.array([[0.0, 1.0, 0.0], [0.0, 2.0, 0.0], [0.0, 3.0, 0.0]], dtype=np.float32)
    np.testing.assert_allclose(realized.coords, expected, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == [0, 3]


def test_rotate_auto_center_ignores_pivot() -> None:
    g = G.rotate_test_line_centered()
    rotated = E.rotate(auto_center=True, pivot=(100.0, 0.0, 0.0), rotation=(0.0, 0.0, 180.0))(g)
    realized = realize(rotated)

    expected = np.array([[3.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float32)
    np.testing.assert_allclose(realized.coords, expected, rtol=0.0, atol=1e-6)


def test_rotate_pivot_used_when_auto_center_false() -> None:
    g = G.rotate_test_line2()
    rotated = E.rotate(auto_center=False, pivot=(1.0, 0.0, 0.0), rotation=(0.0, 0.0, 90.0))(g)
    realized = realize(rotated)

    expected = np.array([[1.0, 0.0, 0.0], [1.0, 1.0, 0.0]], dtype=np.float32)
    np.testing.assert_allclose(realized.coords, expected, rtol=0.0, atol=1e-6)


def test_rotate_empty_geometry_is_noop() -> None:
    g = G.rotate_test_empty()
    rotated = E.rotate(rotation=(10.0, 20.0, 30.0))(g)
    realized = realize(rotated)

    assert realized.coords.shape == (0, 3)
    assert realized.offsets.tolist() == [0]

