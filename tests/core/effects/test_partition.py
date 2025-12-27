"""partition effect のサイト密度制御に関するテスト群。"""

from __future__ import annotations

import numpy as np
import pytest

from grafix.api import E, G
from grafix.core.primitive_registry import primitive
from grafix.core.realize import realize
from grafix.core.realized_geometry import RealizedGeometry

pytest.importorskip("shapely")


@primitive
def partition_test_square() -> RealizedGeometry:
    """XY 平面上の矩形ループを 1 本返す。"""
    coords = np.array(
        [
            [-1.0, -1.0, 0.0],
            [1.0, -1.0, 0.0],
            [1.0, 1.0, 0.0],
            [-1.0, 1.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, 4], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _loop_centroid_xs(g: RealizedGeometry) -> np.ndarray:
    xs: list[float] = []
    offsets = g.offsets
    for i in range(int(offsets.size) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        loop = g.coords[s:e]
        if loop.shape[0] <= 0:
            continue
        if loop.shape[0] >= 2 and np.allclose(loop[0], loop[-1], rtol=0.0, atol=1e-6):
            body = loop[:-1]
        else:
            body = loop
        if body.shape[0] <= 0:
            continue
        c = body.astype(np.float64, copy=False).mean(axis=0)
        xs.append(float(c[0]))
    return np.asarray(xs, dtype=np.float64)


def test_partition_site_density_bias_shifts_mean_x() -> None:
    g = G.partition_test_square()

    r_uniform = realize(E.partition(site_count=25, seed=0)(g))
    xs_uniform = _loop_centroid_xs(r_uniform)
    assert xs_uniform.size >= 5

    r_biased = realize(
        E.partition(
            site_count=25,
            seed=0,
            site_density_base=(0.5, 0.0, 0.0),
            site_density_slope=(0.5, 0.0, 0.0),
        )(g)
    )
    xs_biased = _loop_centroid_xs(r_biased)
    assert xs_biased.size >= 5

    assert float(xs_biased.mean()) > float(xs_uniform.mean()) + 0.05


def test_partition_pivot_affects_bias_only_when_auto_center_off() -> None:
    g = G.partition_test_square()

    r_auto_a = realize(
        E.partition(
            site_count=25,
            seed=0,
            site_density_base=(0.5, 0.0, 0.0),
            site_density_slope=(0.5, 0.0, 0.0),
            auto_center=True,
            pivot=(0.5, 0.0, 0.0),
        )(g)
    )
    r_auto_b = realize(
        E.partition(
            site_count=25,
            seed=0,
            site_density_base=(0.5, 0.0, 0.0),
            site_density_slope=(0.5, 0.0, 0.0),
            auto_center=True,
            pivot=(-0.5, 0.0, 0.0),
        )(g)
    )
    np.testing.assert_allclose(r_auto_a.coords, r_auto_b.coords, rtol=0.0, atol=0.0)
    assert r_auto_a.offsets.tolist() == r_auto_b.offsets.tolist()

    r_pivot_pos = realize(
        E.partition(
            site_count=25,
            seed=0,
            site_density_base=(0.0, 0.0, 0.0),
            site_density_slope=(1.0, 0.0, 0.0),
            auto_center=False,
            pivot=(0.5, 0.0, 0.0),
        )(g)
    )
    r_pivot_neg = realize(
        E.partition(
            site_count=25,
            seed=0,
            site_density_base=(0.0, 0.0, 0.0),
            site_density_slope=(1.0, 0.0, 0.0),
            auto_center=False,
            pivot=(-0.5, 0.0, 0.0),
        )(g)
    )

    xs_pos = _loop_centroid_xs(r_pivot_pos)
    xs_neg = _loop_centroid_xs(r_pivot_neg)
    assert xs_pos.size >= 5
    assert xs_neg.size >= 5

    assert float(xs_pos.mean()) > float(xs_neg.mean()) + 0.05
