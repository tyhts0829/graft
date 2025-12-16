"""polygon プリミティブのポリライン形状に関するテスト群。"""

from __future__ import annotations

import numpy as np

from graft.core.geometry import Geometry
from graft.core.realize import realize
from graft.core.primitives import polygon as _polygon_module  # noqa: F401


def test_polygon_polyline_is_closed() -> None:
    """開始点を終端に重ねた閉じたポリラインになる。"""
    sides = 5
    g = Geometry.create("polygon", params={"n_sides": sides})

    realized = realize(g)

    assert realized.coords.shape == (sides + 1, 3)
    assert realized.offsets.tolist() == [0, sides + 1]
    np.testing.assert_array_equal(realized.coords[0], realized.coords[-1])


def test_polygon_phase_rotates_first_vertex() -> None:
    """phase[deg] により頂点開始角が回転する。"""
    sides = 4

    g0 = Geometry.create("polygon", params={"n_sides": sides, "phase": 0.0})
    r0 = realize(g0)
    np.testing.assert_allclose(r0.coords[0], [0.5, 0.0, 0.0], rtol=0.0, atol=1e-6)

    g90 = Geometry.create("polygon", params={"n_sides": sides, "phase": 90.0})
    r90 = realize(g90)
    np.testing.assert_allclose(r90.coords[0], [0.0, 0.5, 0.0], rtol=0.0, atol=1e-6)


def test_polygon_center_and_scale_affect_coords() -> None:
    """center/scale が座標に反映される。"""
    g = Geometry.create(
        "polygon",
        params={
            "n_sides": 4,
            "center": (10.0, 20.0, 30.0),
            "scale": (2.0, 3.0, 4.0),
        },
    )
    realized = realize(g)
    np.testing.assert_allclose(realized.coords[0], [11.0, 20.0, 30.0], rtol=0.0, atol=1e-6)


def test_polygon_clamps_n_sides_lt_3() -> None:
    """n_sides < 3 は 3 にクランプされる。"""
    g = Geometry.create("polygon", params={"n_sides": 1})
    realized = realize(g)
    assert realized.coords.shape == (4, 3)
    assert realized.offsets.tolist() == [0, 4]
