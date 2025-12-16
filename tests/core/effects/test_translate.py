"""translate effect の実体変換に関するテスト群。"""

from __future__ import annotations

import numpy as np

from graft.api import E, G
from graft.core.primitive_registry import primitive
from graft.core.realize import realize
from graft.core.realized_geometry import RealizedGeometry


@primitive
def translate_test_line2_xy() -> RealizedGeometry:
    """xy 平面上の 2 点ポリラインを返す。"""
    coords = np.array([[1.0, 2.0, 0.0], [3.0, 4.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def translate_test_empty() -> RealizedGeometry:
    """空のジオメトリを返す。"""
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def test_translate_adds_delta() -> None:
    g = G.translate_test_line2_xy()
    moved = E.translate(delta=(10.0, -2.0, 3.5))(g)
    realized = realize(moved)

    expected = np.array([[11.0, 0.0, 3.5], [13.0, 2.0, 3.5]], dtype=np.float32)
    np.testing.assert_allclose(realized.coords, expected, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == [0, 2]


def test_translate_zero_delta_is_noop() -> None:
    g = G.translate_test_line2_xy()
    moved = E.translate(delta=(0.0, 0.0, 0.0))(g)
    realized = realize(moved)

    expected = np.array([[1.0, 2.0, 0.0], [3.0, 4.0, 0.0]], dtype=np.float32)
    np.testing.assert_allclose(realized.coords, expected, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == [0, 2]


def test_translate_empty_geometry_is_noop() -> None:
    g = G.translate_test_empty()
    moved = E.translate(delta=(10.0, 20.0, 30.0))(g)
    realized = realize(moved)

    assert realized.coords.shape == (0, 3)
    assert realized.offsets.tolist() == [0]

