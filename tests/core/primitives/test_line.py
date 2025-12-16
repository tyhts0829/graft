"""line プリミティブの線分形状に関するテスト群。"""

from __future__ import annotations

import numpy as np

from graft.core.geometry import Geometry
from graft.core.realize import realize
from graft.core.primitives import line as _line_module  # noqa: F401


def test_line_default_is_centered_unit_segment() -> None:
    """デフォルトは長さ 1・原点中心・+X 方向の線分になる。"""
    realized = realize(Geometry.create("line"))
    assert realized.coords.shape == (2, 3)
    assert realized.offsets.tolist() == [0, 2]
    np.testing.assert_allclose(realized.coords[0], [-0.5, 0.0, 0.0], rtol=0.0, atol=1e-6)
    np.testing.assert_allclose(realized.coords[1], [0.5, 0.0, 0.0], rtol=0.0, atol=1e-6)


def test_line_angle_rotates_segment_on_xy_plane() -> None:
    """angle[deg] により XY 平面上で回転する。"""
    realized = realize(Geometry.create("line", params={"length": 1.0, "angle": 90.0}))
    np.testing.assert_allclose(realized.coords[0], [0.0, -0.5, 0.0], rtol=0.0, atol=1e-6)
    np.testing.assert_allclose(realized.coords[1], [0.0, 0.5, 0.0], rtol=0.0, atol=1e-6)


def test_line_center_moves_segment_and_preserves_z() -> None:
    """center が平行移動として作用し、z は center[2] に固定される。"""
    realized = realize(
        Geometry.create(
            "line",
            params={"length": 1.0, "angle": 0.0, "center": (10.0, 20.0, 30.0)},
        )
    )
    np.testing.assert_allclose(realized.coords[0], [9.5, 20.0, 30.0], rtol=0.0, atol=1e-6)
    np.testing.assert_allclose(realized.coords[1], [10.5, 20.0, 30.0], rtol=0.0, atol=1e-6)


def test_line_zero_length_returns_two_identical_points() -> None:
    """length==0 のとき 2 点（同一点）として返す。"""
    realized = realize(Geometry.create("line", params={"length": 0.0, "center": (1.0, 2.0, 3.0)}))
    np.testing.assert_array_equal(realized.coords[0], realized.coords[1])
    np.testing.assert_allclose(realized.coords[0], [1.0, 2.0, 3.0], rtol=0.0, atol=1e-6)
