"""partition effect のサイト密度制御に関するテスト群。"""

from __future__ import annotations

import hashlib
import importlib

import numpy as np
import pytest
from shapely.errors import GEOSException  # type: ignore[import-untyped]

from grafix.api import E, G
from grafix.core.effects.partition import partition as partition_impl
from grafix.core.operation_authoring import primitive
from grafix.core.realize import realize
from grafix.core.realized_geometry import GeomTuple, RealizedGeometry

partition_module = importlib.import_module("grafix.core.effects.partition")


def _packed_digest(coords: np.ndarray, offsets: np.ndarray) -> str:
    return hashlib.sha256(coords.tobytes() + offsets.tobytes()).hexdigest()


@primitive
def partition_test_square() -> GeomTuple:
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
    return coords, offsets


def test_partition_rejects_invalid_control_ranges() -> None:
    base = realize(G.partition_test_square())
    geometry = (base.coords, base.offsets)

    with pytest.raises(ValueError, match="site_count"):
        partition_impl(geometry, site_count=0)
    with pytest.raises(ValueError, match="site_density_base"):
        partition_impl(geometry, site_density_base=(1.1, 0.0, 0.0))


def test_partition_treats_polygon_geos_failure_as_invalid_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = realize(G.partition_test_square())

    def raise_domain_error(*_args: object, **_kwargs: object) -> None:
        raise GEOSException("invalid polygon")

    monkeypatch.setattr(partition_module, "Polygon", raise_domain_error)
    coords, offsets = partition_impl((base.coords, base.offsets))

    assert coords is base.coords
    assert offsets is base.offsets


def test_partition_does_not_hide_internal_polygon_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = realize(G.partition_test_square())

    def raise_internal_error(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("internal bug")

    monkeypatch.setattr(partition_module, "Polygon", raise_internal_error)
    with pytest.raises(RuntimeError, match="internal bug"):
        partition_impl((base.coords, base.offsets))


@primitive
def partition_test_donut() -> GeomTuple:
    """XY 平面上の外周+穴（2 リング）を返す。"""
    coords = np.array(
        [
            # outer
            [-2.0, -2.0, 0.0],
            [2.0, -2.0, 0.0],
            [2.0, 2.0, 0.0],
            [-2.0, 2.0, 0.0],
            # hole
            [-0.5, -0.5, 0.0],
            [0.5, -0.5, 0.0],
            [0.5, 0.5, 0.0],
            [-0.5, 0.5, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, 4, 8], dtype=np.int32)
    return coords, offsets


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


def test_partition_seeded_output_characterization() -> None:
    base = realize(G.partition_test_square())

    coords, offsets = partition_impl(
        (base.coords, base.offsets),
        mode="merge",
        site_count=7,
        seed=13,
        site_density_base=(0.4, 0.0, 0.0),
        site_density_slope=(0.3, 0.0, 0.0),
    )

    assert offsets.tolist() == [0, 6, 13, 19, 25, 31, 36, 41]
    assert _packed_digest(coords, offsets) == (
        "db90a7db07b361e5bc18b40b5bbdf7d5c22f4f1b3b1d578b3d4969e18dd4f5c8"
    )


def test_partition_site_sampling_rng_order_characterization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = realize(G.partition_test_square())
    frame = partition_module.canonical_planar_frame(base.coords, base.offsets)
    _rings, polygons = partition_module._polygon_inputs(
        frame.project(base.coords),
        base.offsets,
    )
    region = polygons[0]
    pivot, inv_extent = partition_module._density_space(
        base.coords,
        auto_center=True,
        pivot=(0.0, 0.0, 0.0),
    )

    def sample(*, density_enabled: bool) -> list[tuple[float, float]]:
        return partition_module._sample_region_sites(
            region,
            site_count=2,
            rng=np.random.default_rng(0),
            frame=frame,
            density_enabled=density_enabled,
            density_pivot=pivot,
            density_inv_extent=inv_extent,
            density_base=(0.5, 0.0, 0.0),
            density_slope=(0.5, 0.0, 0.0),
        )

    assert sample(density_enabled=False) == [
        (0.2739233746429086, 0.7550578117435922),
        (-0.4604265724722594, -0.8263702555702117),
    ]
    assert sample(density_enabled=True) == [
        (0.6265404784005448, 0.5983927594322296),
        (0.8255111545554434, -0.35542655052033645),
    ]

    monkeypatch.setattr(
        partition_module,
        "_density_probabilities",
        lambda xy, **_kwargs: np.zeros(xy.shape[0], dtype=np.float64),
    )
    assert sample(density_enabled=True) == [
        (-0.6711454126571654, -0.13897396486411395),
        (0.5899407772980261, 0.3851904219957534),
    ]


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


def test_partition_mode_group_preserves_hole_region() -> None:
    g = G.partition_test_donut()

    r_ring = realize(E.partition(mode="ring", site_count=25, seed=0)(g))
    r_merge = realize(E.partition(mode="merge", site_count=25, seed=0)(g))
    r_group = realize(E.partition(mode="group", site_count=25, seed=0)(g))

    def _has_vertex_inside_hole(r: RealizedGeometry) -> bool:
        coords = r.coords
        inside = (np.abs(coords[:, 0]) < 0.4) & (np.abs(coords[:, 1]) < 0.4)
        return bool(np.any(inside))

    assert _has_vertex_inside_hole(r_ring)
    assert not _has_vertex_inside_hole(r_merge)
    assert not _has_vertex_inside_hole(r_group)
