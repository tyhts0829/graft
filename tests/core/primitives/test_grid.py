"""grid プリミティブのポリライン形状に関するテスト群。"""

from __future__ import annotations

import numpy as np

from grafix.core.geometry import Geometry
from grafix.core.realize import realize
from grafix.core.primitives import grid as _grid_module  # noqa: F401


def test_grid_line_count_and_offsets() -> None:
    """縦線 nx 本 + 横線 ny 本の 2 点線分列になる。"""
    nx, ny = 3, 2
    g = Geometry.create("grid", params={"nx": nx, "ny": ny})
    realized = realize(g)

    line_count = nx + ny
    assert realized.coords.shape == (2 * line_count, 3)
    assert realized.offsets.tolist() == list(range(0, 2 * line_count + 1, 2))

    expected_x = np.linspace(-0.5, 0.5, num=nx, dtype=np.float32)
    expected_y = np.linspace(-0.5, 0.5, num=ny, dtype=np.float32)

    # vertical
    np.testing.assert_allclose(realized.coords[0 : 2 * nx : 2, 0], expected_x)
    np.testing.assert_allclose(realized.coords[1 : 2 * nx : 2, 0], expected_x)
    np.testing.assert_allclose(realized.coords[0 : 2 * nx : 2, 1], -0.5)
    np.testing.assert_allclose(realized.coords[1 : 2 * nx : 2, 1], 0.5)
    np.testing.assert_allclose(realized.coords[0 : 2 * nx, 2], 0.0)

    # horizontal
    base = 2 * nx
    np.testing.assert_allclose(realized.coords[base : base + 2 * ny : 2, 0], -0.5)
    np.testing.assert_allclose(realized.coords[base + 1 : base + 2 * ny : 2, 0], 0.5)
    np.testing.assert_allclose(realized.coords[base : base + 2 * ny : 2, 1], expected_y)
    np.testing.assert_allclose(
        realized.coords[base + 1 : base + 2 * ny : 2, 1], expected_y
    )
    np.testing.assert_allclose(realized.coords[base : base + 2 * ny, 2], 0.0)


def test_grid_applies_center_and_scale() -> None:
    """center/scale が座標に適用される。"""
    g = Geometry.create(
        "grid",
        params={
            "nx": 1,
            "ny": 1,
            "center": (1.0, 2.0, 3.0),
            "scale": 2.0,
        },
    )
    realized = realize(g)

    assert realized.coords.shape == (4, 3)
    np.testing.assert_allclose(realized.coords[0], (0.0, 1.0, 3.0))
    np.testing.assert_allclose(realized.coords[1], (0.0, 3.0, 3.0))
    np.testing.assert_allclose(realized.coords[2], (0.0, 1.0, 3.0))
    np.testing.assert_allclose(realized.coords[3], (2.0, 1.0, 3.0))


def test_grid_is_empty_when_both_zero() -> None:
    """nx=0 かつ ny=0 のとき空ジオメトリを返す。"""
    g = Geometry.create("grid", params={"nx": 0, "ny": 0})
    realized = realize(g)

    assert realized.coords.shape == (0, 3)
    assert realized.offsets.tolist() == [0]
