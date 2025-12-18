"""scale effect の実体変換に関するテスト群。"""

from __future__ import annotations

import numpy as np

from grafix.api import E, G
from grafix.core.primitive_registry import primitive
from grafix.core.realize import realize
from grafix.core.realized_geometry import RealizedGeometry


@primitive
def scale_test_line2_xy() -> RealizedGeometry:
    """xy 平面上の 2 点ポリラインを返す。"""
    coords = np.array([[1.0, 2.0, 0.0], [3.0, 4.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def scale_test_line_centered_x() -> RealizedGeometry:
    """中心 (2,0,0) を持つ 2 点ポリラインを返す。"""
    coords = np.array([[1.0, 0.0, 0.0], [3.0, 0.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def scale_test_empty() -> RealizedGeometry:
    """空のジオメトリを返す。"""
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def test_scale_about_origin() -> None:
    g = G.scale_test_line2_xy()
    scaled = E.scale(auto_center=False, pivot=(0.0, 0.0, 0.0), scale=(2.0, 0.5, 1.0))(g)
    realized = realize(scaled)

    expected = np.array([[2.0, 1.0, 0.0], [6.0, 2.0, 0.0]], dtype=np.float32)
    np.testing.assert_allclose(realized.coords, expected, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == [0, 2]


def test_scale_auto_center_ignores_pivot() -> None:
    g = G.scale_test_line_centered_x()
    scaled = E.scale(auto_center=True, pivot=(100.0, 0.0, 0.0), scale=(2.0, 1.0, 1.0))(g)
    realized = realize(scaled)

    expected = np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0]], dtype=np.float32)
    np.testing.assert_allclose(realized.coords, expected, rtol=0.0, atol=1e-6)


def test_scale_pivot_used_when_auto_center_false() -> None:
    g = G.scale_test_line_centered_x()
    scaled = E.scale(auto_center=False, pivot=(1.0, 0.0, 0.0), scale=(2.0, 1.0, 1.0))(g)
    realized = realize(scaled)

    expected = np.array([[1.0, 0.0, 0.0], [5.0, 0.0, 0.0]], dtype=np.float32)
    np.testing.assert_allclose(realized.coords, expected, rtol=0.0, atol=1e-6)


def test_scale_empty_geometry_is_noop() -> None:
    g = G.scale_test_empty()
    scaled = E.scale(scale=(2.0, 2.0, 2.0))(g)
    realized = realize(scaled)

    assert realized.coords.shape == (0, 3)
    assert realized.offsets.tolist() == [0]

