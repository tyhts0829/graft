"""mirror effect（対称ミラー）の実体変換に関するテスト群。"""

from __future__ import annotations

import numpy as np

from graft.api import E, G
from graft.core.primitive_registry import primitive
from graft.core.realize import realize
from graft.core.realized_geometry import RealizedGeometry


@primitive
def mirror_test_cross_x0() -> RealizedGeometry:
    """x=0 を跨ぐ 2 点ポリラインを返す（z は非整数）。"""
    coords = np.array([[-1.0, 0.0, 1.0], [1.0, 0.0, 2.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def mirror_test_quadrant_pp() -> RealizedGeometry:
    """(+x,+y) 象限の 2 点ポリラインを返す。"""
    coords = np.array([[1.0, 2.0, 3.0], [4.0, 6.0, 7.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def mirror_test_wedge_n3() -> RealizedGeometry:
    """n=3 の楔内にある短い 2 点ポリラインを返す。"""
    coords = np.array([[2.0, 0.2, 5.0], [2.0, 0.4, 6.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _iter_polylines(realized: RealizedGeometry):
    offsets = realized.offsets
    for i in range(len(offsets) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        yield realized.coords[s:e]


def test_mirror_n1_clips_and_reflects_across_x_plane() -> None:
    g = G.mirror_test_cross_x0()
    mirrored = realize(E.mirror(n_mirror=1, cx=0.0, source_positive_x=True, show_planes=False)(g))

    polylines = list(_iter_polylines(mirrored))
    assert len(polylines) == 2
    assert [p.shape[0] for p in polylines] == [2, 2]

    p0, p1 = polylines
    np.testing.assert_allclose(p0[0], [0.0, 0.0, 1.5], rtol=0.0, atol=1e-6)
    np.testing.assert_allclose(p0[1], [1.0, 0.0, 2.0], rtol=0.0, atol=1e-6)
    np.testing.assert_allclose(p1[0], [0.0, 0.0, 1.5], rtol=0.0, atol=1e-6)
    np.testing.assert_allclose(p1[1], [-1.0, 0.0, 2.0], rtol=0.0, atol=1e-6)


def test_mirror_n2_generates_four_quadrants_and_preserves_z() -> None:
    g = G.mirror_test_quadrant_pp()
    mirrored = realize(
        E.mirror(
            n_mirror=2,
            cx=0.0,
            cy=0.0,
            source_positive_x=True,
            source_positive_y=True,
            show_planes=False,
        )(g)
    )

    polylines = list(_iter_polylines(mirrored))
    assert len(polylines) == 4

    endpoints = {(float(p[0, 0]), float(p[0, 1]), float(p[1, 0]), float(p[1, 1])) for p in polylines}
    assert endpoints == {
        (1.0, 2.0, 4.0, 6.0),
        (-1.0, 2.0, -4.0, 6.0),
        (1.0, -2.0, 4.0, -6.0),
        (-1.0, -2.0, -4.0, -6.0),
    }
    for p in polylines:
        np.testing.assert_allclose(p[:, 2], [3.0, 7.0], rtol=0.0, atol=1e-6)


def test_mirror_n3_produces_2n_polylines() -> None:
    g = G.mirror_test_wedge_n3()
    mirrored = realize(E.mirror(n_mirror=3, cx=0.0, cy=0.0, show_planes=False)(g))

    polylines = list(_iter_polylines(mirrored))
    assert len(polylines) == 6
    assert all(p.shape == (2, 3) for p in polylines)

