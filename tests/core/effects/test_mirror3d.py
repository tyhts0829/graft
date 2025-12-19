"""mirror3d effect（3D 放射状ミラー）の実体変換に関するテスト群。"""

from __future__ import annotations

import pytest
import numpy as np

from grafix.api import E, G
from grafix.core.primitive_registry import primitive
from grafix.core.realize import realize
from grafix.core.realized_geometry import RealizedGeometry


@primitive
def mirror3d_test_line_in_wedge_posz() -> RealizedGeometry:
    """くさび内（azimuth）にある 2 点ポリライン（z>0）。"""
    coords = np.array([[-2.0, 1.5, 5.0], [-1.5, 1.6, 6.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def mirror3d_test_line_in_wedge_negz() -> RealizedGeometry:
    """くさび内（azimuth）にある 2 点ポリライン（z<0）。"""
    coords = np.array([[-2.0, 1.5, -5.0], [-1.5, 1.6, -6.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def mirror3d_test_line_pos_octant() -> RealizedGeometry:
    """正の八分体（polyhedral のソース領域）内にある 2 点ポリライン。"""
    coords = np.array([[1.1, 2.2, 3.3], [4.4, 5.5, 6.6]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _iter_polylines(realized: RealizedGeometry):
    offsets = realized.offsets
    for i in range(len(offsets) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        yield realized.coords[s:e]


def test_mirror3d_azimuth_produces_2n_polylines_and_preserves_z() -> None:
    g = G.mirror3d_test_line_in_wedge_posz()
    out = realize(
        E.mirror3d(
            mode="azimuth",
            n_azimuth=3,
            center=(0.0, 0.0, 0.0),
            axis=(0.0, 0.0, 1.0),
            phi0=0.0,
            mirror_equator=False,
            show_planes=False,
        )(g)
    )

    polylines = list(_iter_polylines(out))
    assert len(polylines) == 6
    assert all(p.shape == (2, 3) for p in polylines)
    for p in polylines:
        np.testing.assert_allclose(p[:, 2], [5.0, 6.0], rtol=0.0, atol=1e-6)


def test_mirror3d_azimuth_n1_produces_two_polylines() -> None:
    g = G.mirror3d_test_line_in_wedge_posz()
    out = realize(E.mirror3d(mode="azimuth", n_azimuth=1, show_planes=False)(g))
    polylines = list(_iter_polylines(out))
    assert len(polylines) == 2


def test_mirror3d_equator_source_side_selects_halfspace() -> None:
    g = G.mirror3d_test_line_in_wedge_negz()

    # z<0 のみを持つ入力に対して「正側をソース」にすると、何も残らない。
    out_pos = realize(
        E.mirror3d(
            mode="azimuth",
            n_azimuth=3,
            center=(0.0, 0.0, 0.0),
            axis=(0.0, 0.0, 1.0),
            phi0=0.0,
            mirror_equator=True,
            source_side=True,
            show_planes=False,
        )(g)
    )
    assert out_pos.coords.shape == (0, 3)
    assert out_pos.offsets.shape == (1,)

    # 「負側をソース」にすると、2n から赤道ミラーで倍になり 4n になる。
    out_neg = realize(
        E.mirror3d(
            mode="azimuth",
            n_azimuth=3,
            center=(0.0, 0.0, 0.0),
            axis=(0.0, 0.0, 1.0),
            phi0=0.0,
            mirror_equator=True,
            source_side=False,
            show_planes=False,
        )(g)
    )

    polylines = list(_iter_polylines(out_neg))
    assert len(polylines) == 12
    z0 = np.array([float(p[0, 2]) for p in polylines], dtype=np.float64)
    assert np.any(z0 < 0.0)
    assert np.any(z0 > 0.0)


@pytest.mark.parametrize(
    ("group", "expected"),
    [
        ("T", 12),
        ("O", 24),
        ("I", 60),
    ],
)
def test_mirror3d_polyhedral_rotation_group_sizes(group: str, expected: int) -> None:
    g = G.mirror3d_test_line_pos_octant()
    out = realize(
        E.mirror3d(
            mode="polyhedral",
            group=group,
            center=(0.0, 0.0, 0.0),
            use_reflection=False,
            show_planes=False,
        )(g)
    )
    polylines = list(_iter_polylines(out))
    assert len(polylines) == expected


def test_mirror3d_polyhedral_use_reflection_doubles() -> None:
    g = G.mirror3d_test_line_pos_octant()
    out = realize(
        E.mirror3d(
            mode="polyhedral",
            group="T",
            center=(0.0, 0.0, 0.0),
            use_reflection=True,
            show_planes=False,
        )(g)
    )
    polylines = list(_iter_polylines(out))
    assert len(polylines) == 24


def test_mirror3d_show_planes_adds_lines() -> None:
    g = G.mirror3d_test_line_in_wedge_posz()
    out0 = realize(E.mirror3d(mode="azimuth", n_azimuth=3, show_planes=False)(g))
    out1 = realize(E.mirror3d(mode="azimuth", n_azimuth=3, show_planes=True)(g))

    n0 = len(list(_iter_polylines(out0)))
    n1 = len(list(_iter_polylines(out1)))
    assert n1 > n0
