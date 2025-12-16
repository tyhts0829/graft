"""affine effect の実体変換に関するテスト群。"""

from __future__ import annotations

import numpy as np

from graft.api import E, G
from graft.core.primitive_registry import primitive
from graft.core.realize import realize
from graft.core.realized_geometry import RealizedGeometry


@primitive
def affine_test_two_points_xy() -> RealizedGeometry:
    """xy 平面上の 2 点を返す。"""
    coords = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def affine_test_line_centered_x() -> RealizedGeometry:
    """中心 (2,0,0) を持つ 2 点ポリラインを返す。"""
    coords = np.array([[1.0, 0.0, 0.0], [3.0, 0.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def affine_test_line2_x() -> RealizedGeometry:
    """x 軸上の 2 点ポリラインを返す。"""
    coords = np.array([[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def affine_test_empty() -> RealizedGeometry:
    """空のジオメトリを返す。"""
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def test_affine_scale_rotate_translate_about_origin() -> None:
    g = G.affine_test_two_points_xy()
    transformed = E.affine(
        auto_center=False,
        pivot=(0.0, 0.0, 0.0),
        scale=(2.0, 3.0, 1.0),
        rotation=(0.0, 0.0, 90.0),
        delta=(10.0, 20.0, 0.0),
    )(g)
    realized = realize(transformed)

    expected = np.array([[10.0, 22.0, 0.0], [7.0, 20.0, 0.0]], dtype=np.float32)
    np.testing.assert_allclose(realized.coords, expected, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == [0, 2]


def test_affine_auto_center_ignores_pivot() -> None:
    g = G.affine_test_line_centered_x()
    transformed = E.affine(
        auto_center=True,
        pivot=(100.0, 0.0, 0.0),
        rotation=(0.0, 0.0, 180.0),
    )(g)
    realized = realize(transformed)

    expected = np.array([[3.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float32)
    np.testing.assert_allclose(realized.coords, expected, rtol=0.0, atol=1e-6)


def test_affine_pivot_used_when_auto_center_false() -> None:
    g = G.affine_test_line2_x()
    transformed = E.affine(
        auto_center=False,
        pivot=(1.0, 0.0, 0.0),
        scale=(2.0, 1.0, 1.0),
        rotation=(0.0, 0.0, 90.0),
    )(g)
    realized = realize(transformed)

    expected = np.array([[1.0, 0.0, 0.0], [1.0, 2.0, 0.0]], dtype=np.float32)
    np.testing.assert_allclose(realized.coords, expected, rtol=0.0, atol=1e-6)


def test_affine_empty_geometry_is_noop() -> None:
    g = G.affine_test_empty()
    transformed = E.affine(
        scale=(2.0, 3.0, 4.0),
        rotation=(10.0, 20.0, 30.0),
        delta=(1.0, 2.0, 3.0),
    )(g)
    realized = realize(transformed)

    assert realized.coords.shape == (0, 3)
    assert realized.offsets.tolist() == [0]

