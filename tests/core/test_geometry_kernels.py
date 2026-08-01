from __future__ import annotations

import numpy as np
import pytest

from grafix.core.geometry_kernels.grid import plan_grid_from_bbox
from grafix.core.geometry_kernels.marching import (
    marching_squares_loops,
    marching_squares_paths,
)
from grafix.core.geometry_kernels.packed import (
    empty_packed_geometry,
    pack_polylines,
)
from grafix.core.geometry_kernels.planar import (
    PlanarFrame,
    PlanarRing,
    canonical_planar_frame,
    close_curve,
    extract_planar_rings,
    pack_planar_rings,
    planarity_threshold,
)
from grafix.core.geometry_kernels.raster import (
    rasterize_ring_boundary_mask,
    scanline_evenodd_mask,
    signed_distance_grid_edt,
    squared_euclidean_distance_transform,
)
from grafix.core.geometry_kernels.resample import (
    ResamplePlan,
    resample_polylines,
)


def _two_open_lines() -> tuple[np.ndarray, np.ndarray]:
    coords = np.asarray(
        [
            [0.0, 0.0, 0.0],
            [2.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [11.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.asarray([0, 2, 4], dtype=np.int32)
    return coords, offsets


def test_empty_packed_geometry_uses_canonical_contract() -> None:
    coords, offsets = empty_packed_geometry()

    assert coords.shape == (0, 3)
    assert coords.dtype == np.float32
    np.testing.assert_array_equal(offsets, np.asarray([0], dtype=np.int32))


def test_pack_polylines_preserves_order_empty_lines_and_standard_dtypes() -> None:
    first = np.asarray([[0.0, 1.0, 2.0], [3.0, 4.0, 5.0]], dtype=np.float64)
    empty = np.empty((0, 3), dtype=np.float32)
    last = np.asarray([[6.0, 7.0, 8.0]], dtype=np.float32)

    coords, offsets = pack_polylines([first, empty, last])

    assert coords.dtype == np.float32
    assert offsets.dtype == np.int32
    assert offsets.tolist() == [0, 2, 2, 3]
    np.testing.assert_array_equal(
        coords,
        np.asarray([[0, 1, 2], [3, 4, 5], [6, 7, 8]], dtype=np.float32),
    )


@pytest.mark.parametrize(
    "line",
    (
        np.empty((2,), dtype=np.float32),
        np.empty((2, 2), dtype=np.float32),
        np.empty((2, 4), dtype=np.float32),
    ),
)
def test_pack_polylines_rejects_non_xyz_lines(line: np.ndarray) -> None:
    with pytest.raises(ValueError, match=r"shape \(N,3\)"):
        pack_polylines([line])


def test_planarity_threshold_uses_fixed_floor_and_bbox_scale() -> None:
    assert planarity_threshold(np.empty((0, 3), dtype=np.float32)) == 1e-6

    points = np.asarray([[0.0, 0.0, 0.0], [3.0, 4.0, 0.0]], dtype=np.float32)

    assert planarity_threshold(points) == pytest.approx(5e-5)


def test_close_curve_preserves_open_identity_and_reallocates_closed_curve() -> None:
    short = np.asarray([[0.0, 0.0, 0.0]], dtype=np.float32)
    open_with_matching_xy = np.asarray(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 0.01]],
        dtype=np.float32,
    )
    closed = np.asarray(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
        dtype=np.float32,
    )

    assert close_curve(short, 1e-3) is short
    assert close_curve(open_with_matching_xy, 1e-3) is open_with_matching_xy

    result = close_curve(closed, 1e-3)

    assert result is not closed
    assert not np.shares_memory(result, closed)
    assert result.dtype == closed.dtype
    np.testing.assert_array_equal(result, closed)


def test_extract_planar_rings_filters_and_preserves_input_order() -> None:
    lines = [
        np.asarray([[90, 90, 0], [91, 91, 0]], dtype=np.float32),
        np.asarray(
            [[0, 0, 0], [2, 0, 0], [2, 2, 0], [0, 2, 0], [0, 0, 0]],
            dtype=np.float32,
        ),
        np.asarray(
            [
                [10, 0, 0],
                [12, 0, 0],
                [12, 2, 0],
                [10, 2, 0],
                [10, 0, 5e-4],
            ],
            dtype=np.float32,
        ),
        np.asarray(
            [[20, 0, 0], [22, 0, 0], [22, 2, 0], [20, 2, 0]],
            dtype=np.float32,
        ),
        np.asarray([[30, 0, 0], [31, 0, 0], [30, 0, 0]], dtype=np.float32),
    ]
    coords = np.concatenate(lines, axis=0)
    offsets = np.asarray(
        [0, *np.cumsum([line.shape[0] for line in lines])],
        dtype=np.int32,
    )

    rings = extract_planar_rings(
        coords,
        offsets,
        auto_close_threshold=1e-3,
    )

    assert len(rings) == 2
    assert [ring.vertices.dtype for ring in rings] == [np.float64, np.float64]
    assert [ring.vertices.shape for ring in rings] == [(5, 2), (5, 2)]
    np.testing.assert_array_equal(rings[0].mins, np.asarray([0.0, 0.0]))
    np.testing.assert_array_equal(rings[0].maxs, np.asarray([2.0, 2.0]))
    np.testing.assert_array_equal(rings[1].mins, np.asarray([10.0, 0.0]))
    np.testing.assert_array_equal(rings[1].maxs, np.asarray([12.0, 2.0]))
    for ring in rings:
        np.testing.assert_array_equal(ring.vertices[0], ring.vertices[-1])


def test_pack_planar_rings_preserves_buffers_and_allocates_fresh_arrays() -> None:
    rings = [
        PlanarRing(
            vertices=np.asarray([[0, 0], [1, 0], [0, 0]], dtype=np.float32),
            mins=np.asarray([0, 0], dtype=np.float32),
            maxs=np.asarray([1, 0], dtype=np.float32),
        ),
        PlanarRing(
            vertices=np.asarray([[2, 3], [4, 5]], dtype=np.float64),
            mins=np.asarray([2, 3], dtype=np.float64),
            maxs=np.asarray([4, 5], dtype=np.float64),
        ),
    ]

    first = pack_planar_rings(rings)
    second = pack_planar_rings(rings)

    vertices, offsets, mins, maxs = first
    assert vertices.dtype == np.float64
    assert offsets.dtype == np.int32
    assert mins.dtype == np.float64
    assert maxs.dtype == np.float64
    assert offsets.tolist() == [0, 3, 5]
    np.testing.assert_array_equal(
        vertices,
        np.asarray([[0, 0], [1, 0], [0, 0], [2, 3], [4, 5]], dtype=np.float64),
    )
    np.testing.assert_array_equal(mins, np.asarray([[0, 0], [2, 3]]))
    np.testing.assert_array_equal(maxs, np.asarray([[1, 0], [4, 5]]))
    for array, fresh in zip(first, second, strict=True):
        assert array.flags.c_contiguous
        assert array.flags.owndata
        assert not np.shares_memory(array, fresh)

    empty_vertices, empty_offsets, empty_mins, empty_maxs = pack_planar_rings([])
    assert empty_vertices.shape == (0, 2)
    assert empty_offsets.tolist() == [0]
    assert empty_mins.shape == (0, 2)
    assert empty_maxs.shape == (0, 2)


def test_resample_plan_counts_all_lines_before_allocation_at_cap_boundary() -> None:
    coords, offsets = _two_open_lines()

    fitting = ResamplePlan.from_geometry(
        coords,
        offsets,
        step=1.0,
        closed="open",
        max_vertices=5,
    )
    overflowing = ResamplePlan.from_geometry(
        coords,
        offsets,
        step=1.0,
        closed="open",
        max_vertices=4,
    )

    assert fitting.fits
    assert fitting.total_vertices == 5
    assert not overflowing.fits
    assert overflowing.total_vertices == 5

    sampled, sampled_offsets = resample_polylines(coords, fitting)
    assert sampled_offsets.tolist() == [0, 3, 5]
    np.testing.assert_allclose(
        sampled[:, 0],
        np.asarray([0.0, 1.0, 2.0, 10.0, 11.0], dtype=np.float32),
        rtol=0.0,
        atol=0.0,
    )


def test_closed_resample_skips_consecutive_zero_length_segments() -> None:
    coords = np.asarray(
        [
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            [2.0, 0.0, 0.0],
            [2.0, 2.0, 0.0],
            [0.0, 2.0, 0.0],
            [0.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.asarray([0, coords.shape[0]], dtype=np.int32)
    plan = ResamplePlan.from_geometry(
        coords,
        offsets,
        step=1.0,
        closed="closed",
        max_vertices=100,
    )

    sampled, sampled_offsets = resample_polylines(coords, plan)

    assert sampled_offsets.tolist() == [0, 9]
    np.testing.assert_array_equal(
        sampled,
        np.asarray(
            [
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [2.0, 0.0, 0.0],
                [2.0, 1.0, 0.0],
                [2.0, 2.0, 0.0],
                [1.0, 2.0, 0.0],
                [0.0, 2.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0],
            ],
            dtype=np.float32,
        ),
    )


@pytest.mark.parametrize("step", [2.0, 4.0, 1e100])
def test_closed_resample_large_step_keeps_three_distinct_ring_samples(
    step: float,
) -> None:
    coords = np.asarray(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [1.0, 1.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.asarray([0, coords.shape[0]], dtype=np.int32)
    plan = ResamplePlan.from_geometry(
        coords,
        offsets,
        step=step,
        closed="closed",
        max_vertices=100,
    )

    sampled, sampled_offsets = resample_polylines(coords, plan)

    assert sampled_offsets.tolist() == [0, 4]
    assert np.unique(sampled[:-1], axis=0).shape[0] == 3
    np.testing.assert_array_equal(sampled[0], sampled[-1])


def test_grid_plan_rejects_first_point_over_cap() -> None:
    fitting_plan = plan_grid_from_bbox(
        (0.0, 0.0),
        (2.0, 1.0),
        pitch=1.0,
        max_points=6,
        overflow="reject",
    )
    overflowing_plan = plan_grid_from_bbox(
        (0.0, 0.0),
        (2.0, 1.0),
        pitch=1.0,
        max_points=5,
        overflow="reject",
    )

    fitting = fitting_plan.spec
    assert fitting is not None
    assert (fitting.nx, fitting.ny, fitting.point_count) == (3, 2, 6)
    assert fitting_plan.diagnostic is None
    assert overflowing_plan.spec is None
    assert overflowing_plan.diagnostic is not None
    assert (
        overflowing_plan.diagnostic.reason
        == "requested grid exceeded the point limit and was rejected"
    )


def test_grid_plan_coarsens_both_axes_with_one_pitch() -> None:
    plan = plan_grid_from_bbox(
        (0.0, 0.0),
        (100.0, 100.0),
        pitch=1.0,
        max_points=100,
        overflow="coarsen",
    )

    grid = plan.spec
    assert grid is not None
    assert grid.coarsened
    assert grid.point_count <= 100
    assert plan.diagnostic is not None
    assert plan.diagnostic.original_value == 1.0
    assert plan.diagnostic.effective_value == grid.pitch
    xs, ys = grid.coordinates()
    np.testing.assert_allclose(np.diff(xs), grid.pitch, rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(np.diff(ys), grid.pitch, rtol=1e-12, atol=1e-12)


def test_grid_plan_rejects_degenerate_unpadded_bbox() -> None:
    plan = plan_grid_from_bbox(
        (1.0, 2.0),
        (1.0, 2.0),
        pitch=1.0,
        max_points=100,
    )

    assert plan.spec is None
    assert plan.diagnostic is not None
    assert plan.diagnostic.reason == "degenerate grid bounds were rejected"


def test_planar_frame_uses_all_points_when_first_three_are_collinear() -> None:
    points = np.asarray(
        [
            [0.0, 0.0, 0.0],
            [0.5, 0.0, 0.5],
            [1.0, 0.0, 1.0],
            [1.0, 1.0, 2.0],
            [0.0, 1.0, 1.0],
            [0.0, 0.0, 0.0],
        ],
        dtype=np.float64,
    )
    offsets = np.asarray([0, points.shape[0]], dtype=np.int32)

    frame = PlanarFrame.from_points(points, offsets)
    local = frame.to_local(points)

    assert frame.status == "planar"
    assert frame.rank == 2
    assert frame.is_planar(1e-12)
    expected_normal = np.asarray([-1.0, -1.0, 1.0]) / np.sqrt(3.0)
    np.testing.assert_allclose(frame.normal, expected_normal, atol=1e-12)
    np.testing.assert_allclose(local[:, 2], 0.0, atol=1e-12)
    assert local[1, 0] > local[0, 0]
    np.testing.assert_allclose(local[1, 1:], local[0, 1:], atol=1e-12)


def test_planar_frame_ignores_consecutive_duplicates_and_explicit_closure() -> None:
    first = np.asarray(
        [
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            [4.0, 0.0, 0.0],
            [4.0, 3.0, 0.0],
            [0.0, 3.0, 0.0],
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
        ],
        dtype=np.float64,
    )
    second = np.asarray(
        [
            [1.0, 1.0, 0.0],
            [2.0, 1.0, 0.0],
            [2.0, 2.0, 0.0],
            [1.0, 2.0, 0.0],
            [1.0, 1.0, 0.0],
        ],
        dtype=np.float64,
    )
    points = np.concatenate([first, second], axis=0)
    offsets = np.asarray([0, first.shape[0], points.shape[0]], dtype=np.int32)

    frame = PlanarFrame.from_points(points, offsets)

    assert frame.status == "planar"
    np.testing.assert_allclose(frame.normal, [0.0, 0.0, 1.0], atol=1e-12)
    np.testing.assert_allclose(frame.to_local(points)[:, 2], 0.0, atol=1e-12)


def test_planar_frame_preserves_newell_normal_sign_for_plus_and_minus_z() -> None:
    plus_z = np.asarray(
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 0]],
        dtype=np.float64,
    )
    minus_z = plus_z[::-1].copy()

    plus_frame = PlanarFrame.from_points(plus_z)
    minus_frame = PlanarFrame.from_points(minus_z)

    np.testing.assert_allclose(plus_frame.normal, [0.0, 0.0, 1.0], atol=1e-12)
    np.testing.assert_allclose(minus_frame.normal, [0.0, 0.0, -1.0], atol=1e-12)
    assert np.linalg.det(plus_frame.basis) == pytest.approx(1.0)
    assert np.linalg.det(minus_frame.basis) == pytest.approx(1.0)


def test_planar_frame_tilted_axis_is_deterministic() -> None:
    points = np.asarray(
        [
            [3.0, -2.0, 5.0],
            [5.0, -2.0, 7.0],
            [5.0, 1.0, 10.0],
            [3.0, 1.0, 8.0],
            [3.0, -2.0, 5.0],
        ],
        dtype=np.float64,
    )

    first = PlanarFrame.from_points(points)
    second = PlanarFrame.from_points(points.copy())
    edge_local = first.to_local(points[1:2] - points[0] + first.origin)[0]

    np.testing.assert_allclose(first.basis, second.basis, rtol=0.0, atol=0.0)
    assert edge_local[0] > 0.0
    np.testing.assert_allclose(edge_local[1:], 0.0, atol=1e-12)


@pytest.mark.parametrize(
    ("origin", "scale", "atol"),
    [
        (np.asarray([0.0, 0.0, 0.0]), 1e-9, 1e-20),
        (np.asarray([1e12, 2e12, 3e12]), 100.0, 1e-3),
    ],
    ids=("small", "large"),
)
def test_planar_frame_roundtrip_across_coordinate_scales(
    origin: np.ndarray, scale: float, atol: float
) -> None:
    unit = np.asarray(
        [[0, 0, 0], [1, 0, 1], [1, 1, 2], [0, 1, 1], [0, 0, 0]],
        dtype=np.float64,
    )
    points = origin + scale * unit

    frame = PlanarFrame.from_points(points)
    local = frame.to_local(points)
    projected = frame.project(points)
    restored = frame.to_world(local)
    lifted = frame.lift(projected)

    assert frame.is_planar(max(1e-20, scale * 1e-10))
    np.testing.assert_allclose(frame.inverse, frame.basis.T, rtol=0.0, atol=1e-15)
    np.testing.assert_allclose(projected, local[:, :2], rtol=0.0, atol=atol)
    np.testing.assert_allclose(restored, points, rtol=0.0, atol=atol)
    np.testing.assert_allclose(lifted, points, rtol=0.0, atol=atol)


def test_planar_frame_reports_degenerate_inputs_without_transforming() -> None:
    point = PlanarFrame.from_points(np.asarray([[1.0, 2.0, 3.0]]))
    line = PlanarFrame.from_points(
        np.asarray([[0.0, 0.0, 0.0], [1.0, 2.0, 3.0], [2.0, 4.0, 6.0]])
    )

    assert (point.status, point.rank, point.valid) == ("point", 0, False)
    assert (line.status, line.rank, line.valid) == ("linear", 1, False)
    with pytest.raises(ValueError, match="無効な PlanarFrame"):
        line.to_local(np.zeros((1, 3)))


def test_planar_frame_reports_spatial_residual() -> None:
    points = np.asarray(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    )

    frame = PlanarFrame.from_points(points)

    assert frame.status == "spatial"
    assert frame.rank == 3
    assert frame.residual > 0.1
    assert not frame.is_planar(1e-3)


def test_canonical_planar_frame_is_winding_and_seam_independent() -> None:
    ring = np.asarray(
        [
            [1.0, 2.0, 3.0],
            [5.0, 2.0, 3.0],
            [5.0, 6.0, 3.0],
            [1.0, 6.0, 3.0],
            [1.0, 2.0, 3.0],
        ],
        dtype=np.float64,
    )
    reversed_ring = ring[::-1].copy()
    unique = ring[:-1]
    shifted_unique = np.concatenate([unique[2:], unique[:2]], axis=0)
    shifted_ring = np.concatenate([shifted_unique, shifted_unique[:1]], axis=0)

    expected = canonical_planar_frame(ring)
    reversed_frame = canonical_planar_frame(reversed_ring)
    shifted_frame = canonical_planar_frame(shifted_ring)

    assert expected.is_planar(1e-12)
    np.testing.assert_array_equal(expected.origin, reversed_frame.origin)
    np.testing.assert_array_equal(expected.basis, reversed_frame.basis)
    np.testing.assert_array_equal(expected.origin, shifted_frame.origin)
    np.testing.assert_array_equal(expected.basis, shifted_frame.basis)
    np.testing.assert_array_equal(expected.normal, np.asarray([0.0, 0.0, 1.0]))


def test_canonical_planar_frame_adds_direction_independent_linear_plane() -> None:
    points = np.asarray(
        [[1.0, 2.0, 3.0], [4.0, 2.0, 3.0], [8.0, 2.0, 3.0]],
        dtype=np.float64,
    )

    forward = canonical_planar_frame(points, allow_linear=True)
    backward = canonical_planar_frame(points[::-1], allow_linear=True)

    assert (forward.status, forward.rank, forward.valid) == ("planar", 2, True)
    assert forward.is_planar(1e-12)
    np.testing.assert_array_equal(forward.origin, backward.origin)
    np.testing.assert_array_equal(forward.basis, backward.basis)
    np.testing.assert_array_equal(forward.normal, np.asarray([0.0, 0.0, 1.0]))
    np.testing.assert_allclose(forward.to_local(points)[:, 2], 0.0, atol=1e-12)
    np.testing.assert_allclose(forward.to_world(forward.to_local(points)), points, atol=1e-12)


def test_canonical_planar_frame_oblique_ties_ignore_line_order_and_winding() -> None:
    outer = np.asarray(
        [
            [0.0, 0.0, 0.0],
            [2.0, 2.0, 0.0],
            [2.0, 2.0, 2.0],
            [0.0, 0.0, 2.0],
            [0.0, 0.0, 0.0],
        ],
        dtype=np.float64,
    )
    inner = np.asarray(
        [
            [0.5, 0.5, 0.5],
            [1.5, 1.5, 0.5],
            [1.5, 1.5, 1.5],
            [0.5, 0.5, 1.5],
            [0.5, 0.5, 0.5],
        ],
        dtype=np.float64,
    )
    first_points = np.concatenate((outer, inner), axis=0)
    first_offsets = np.asarray([0, outer.shape[0], first_points.shape[0]], dtype=np.int32)
    second_points = np.concatenate((inner[::-1], outer[::-1]), axis=0)
    second_offsets = np.asarray([0, inner.shape[0], second_points.shape[0]], dtype=np.int32)

    first = canonical_planar_frame(first_points, first_offsets)
    second = canonical_planar_frame(second_points, second_offsets)

    assert first.is_planar(1e-12)
    assert second.is_planar(1e-12)
    np.testing.assert_allclose(first.origin, second.origin, rtol=0.0, atol=1e-15)
    np.testing.assert_allclose(first.basis, second.basis, rtol=0.0, atol=1e-15)
    assert first.normal[0] > 0.0
    assert first.normal[1] < 0.0


def test_canonical_planar_frame_three_way_axis_tie_uses_world_x_priority() -> None:
    u = np.asarray([1.0, -1.0, 0.0], dtype=np.float64)
    v = np.asarray([1.0, 1.0, -2.0], dtype=np.float64)

    def ring(origin: np.ndarray, scale: float) -> np.ndarray:
        return np.stack(
            (
                origin,
                origin + scale * u,
                origin + scale * (u + v),
                origin + scale * v,
                origin,
            ),
            axis=0,
        )

    outer = ring(np.zeros((3,), dtype=np.float64), 4.0)
    inner = ring(0.75 * (u + v), 1.0)
    first_points = np.concatenate((outer, inner), axis=0)
    first_offsets = np.asarray([0, 5, 10], dtype=np.int32)
    second_points = np.concatenate((inner[::-1], outer[::-1]), axis=0)
    second_offsets = np.asarray([0, 5, 10], dtype=np.int32)

    first = canonical_planar_frame(first_points, first_offsets)
    second = canonical_planar_frame(second_points, second_offsets)

    np.testing.assert_allclose(first.origin, second.origin, rtol=0.0, atol=1e-15)
    np.testing.assert_allclose(first.basis, second.basis, rtol=0.0, atol=1e-15)
    np.testing.assert_allclose(
        first.normal,
        np.full((3,), 1.0 / np.sqrt(3.0)),
        rtol=0.0,
        atol=1e-15,
    )
    projected_world_x = np.asarray([2.0, -1.0, -1.0]) / np.sqrt(6.0)
    np.testing.assert_allclose(
        first.basis[0],
        projected_world_x,
        rtol=0.0,
        atol=1e-15,
    )


def test_canonical_planar_frame_does_not_hide_spatial_residual() -> None:
    points = np.asarray(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    )

    frame = canonical_planar_frame(points, allow_linear=True)

    assert frame.status == "spatial"
    assert frame.rank == 3
    assert not frame.is_planar(1e-3)


def test_planar_frame_clean_packed_many_short_lines_matches_cleaning_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import grafix.core.geometry_kernels.planar as module

    line_count = 200
    points = np.empty((line_count * 2, 3), dtype=np.float64)
    starts = np.arange(line_count, dtype=np.float64)
    points[0::2, 0] = starts
    points[0::2, 1] = starts % 7.0
    points[0::2, 2] = 0.0
    points[1::2] = points[0::2] + np.asarray([0.25, 0.1, 0.0])
    offsets = np.arange(0, points.shape[0] + 1, 2, dtype=np.int32)

    with monkeypatch.context() as fallback_patch:
        fallback_patch.setattr(
            module,
            "_packed_clean_frame_offsets",
            lambda *_args, **_kwargs: None,
        )
        expected = module.PlanarFrame.from_points(points, offsets)

    def fail_cleaning_fallback(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("clean packed input unexpectedly used the fallback")

    with monkeypatch.context() as fast_patch:
        fast_patch.setattr(
            module,
            "_clean_frame_lines",
            fail_cleaning_fallback,
        )
        actual = module.PlanarFrame.from_points(points, offsets)

    assert actual.status == expected.status
    assert actual.rank == expected.rank
    assert actual.residual == expected.residual
    np.testing.assert_array_equal(actual.origin, expected.origin)
    np.testing.assert_array_equal(actual.basis, expected.basis)
    np.testing.assert_array_equal(actual.inverse, expected.inverse)


def _pack_test_rings(
    rings: list[np.ndarray],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    vertices = np.concatenate(rings, axis=0).astype(np.float64, copy=False)
    counts = np.asarray([ring.shape[0] for ring in rings], dtype=np.int32)
    offsets = np.concatenate(
        [np.zeros((1,), dtype=np.int32), np.cumsum(counts, dtype=np.int32)]
    )
    mins = np.stack([np.min(ring, axis=0) for ring in rings], axis=0)
    maxs = np.stack([np.max(ring, axis=0) for ring in rings], axis=0)
    return vertices, offsets, mins, maxs


def test_planar_grid_scanline_handles_hole_multiple_rings_and_boundaries() -> None:
    outer = np.asarray([[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], dtype=np.float64)
    hole = np.asarray([[3, 3], [3, 7], [7, 7], [7, 3], [3, 3]], dtype=np.float64)
    island = np.asarray([[12, 1], [14, 1], [14, 3], [12, 3], [12, 1]], dtype=np.float64)
    vertices, offsets, mins, maxs = _pack_test_rings([outer, hole, island])
    axis = np.arange(-1.0, 16.0, dtype=np.float64)

    inside = scanline_evenodd_mask(
        axis,
        origin_x=float(axis[0]),
        pitch=1.0,
        nx=int(axis.size),
        ring_vertices=vertices,
        ring_offsets=offsets,
        ring_mins=mins,
        ring_maxs=maxs,
    )

    def value(x: int, y: int) -> int:
        return int(inside[y + 1, x + 1])

    assert value(1, 1) == 1
    assert value(5, 5) == 0
    assert value(13, 2) == 1
    assert value(-1, 1) == 0
    assert value(0, 5) == 1
    assert value(10, 5) == 0


def test_planar_grid_boundary_raster_and_two_pass_edt() -> None:
    ring = np.asarray([[1, 1], [4, 1], [4, 4], [1, 4], [1, 1]], dtype=np.float64)
    vertices, offsets, mins, maxs = _pack_test_rings([ring])
    axis = np.arange(6.0, dtype=np.float64)
    inside = scanline_evenodd_mask(
        axis,
        origin_x=0.0,
        pitch=1.0,
        nx=6,
        ring_vertices=vertices,
        ring_offsets=offsets,
        ring_mins=mins,
        ring_maxs=maxs,
    )
    boundary = rasterize_ring_boundary_mask(
        (6, 6),
        ring_vertices=vertices,
        ring_offsets=offsets,
        origin_x=0.0,
        origin_y=0.0,
        pitch=1.0,
        inside=inside,
    )

    assert boundary[1, 1] == 1
    assert boundary[4, 4] == 1
    assert boundary[2, 2] == 0

    feature = np.zeros((5, 5), dtype=np.uint8)
    feature[2, 2] = 1
    squared = squared_euclidean_distance_transform(feature)
    yy, xx = np.indices(feature.shape)
    expected = (xx - 2) ** 2 + (yy - 2) ** 2
    np.testing.assert_array_equal(squared, expected)


def test_planar_grid_signed_distance_preserves_evenodd_hole_sign() -> None:
    outer = np.asarray([[0, 0], [8, 0], [8, 8], [0, 8], [0, 0]], dtype=np.float64)
    hole = np.asarray([[3, 3], [3, 5], [5, 5], [5, 3], [3, 3]], dtype=np.float64)
    vertices, offsets, mins, maxs = _pack_test_rings([outer, hole])
    axis = np.arange(-2.0, 11.0, dtype=np.float64)

    sdf = signed_distance_grid_edt(
        axis,
        axis,
        ring_vertices=vertices,
        ring_offsets=offsets,
        ring_mins=mins,
        ring_maxs=maxs,
        pitch=1.0,
    )

    assert sdf[3, 3] < 0.0  # world (1, 1): outer shell
    assert sdf[6, 6] >= 0.0  # world (4, 4): hole
    assert sdf[1, 1] >= 0.0  # world (-1, -1): exterior


def test_planar_grid_marching_squares_level_matches_zero_level_parity() -> None:
    axis = np.arange(-3.0, 3.5, 0.5, dtype=np.float64)
    xx, yy = np.meshgrid(axis, axis)
    field = xx * xx + yy * yy

    level_loops = marching_squares_loops(
        field,
        origin_x=float(axis[0]),
        origin_y=float(axis[0]),
        pitch=0.5,
        level=4.0,
    )
    zero_loops = marching_squares_loops(
        field - 4.0,
        origin_x=float(axis[0]),
        origin_y=float(axis[0]),
        pitch=0.5,
    )

    assert len(level_loops) == len(zero_loops) == 1
    np.testing.assert_array_equal(level_loops[0], zero_loops[0])
    np.testing.assert_array_equal(level_loops[0][0], level_loops[0][-1])


def test_planar_grid_marching_squares_paths_stitches_open_rectangular_grid() -> None:
    field = np.tile(np.asarray([0.0, 1.0]), (3, 1))

    paths = marching_squares_paths(
        field,
        origin_x=10.0,
        origin_y=-5.0,
        pitch_x=4.0,
        pitch_y=3.0,
        level=0.5,
    )

    assert len(paths) == 1
    np.testing.assert_array_equal(
        paths[0],
        np.asarray([[12.0, -5.0], [12.0, -2.0], [12.0, 1.0]]),
    )
    assert not np.array_equal(paths[0][0], paths[0][-1])


def test_planar_grid_marching_squares_paths_emits_open_before_closed() -> None:
    field = -np.ones((7, 9), dtype=np.float64)
    field[0, :] = 1.0
    field[4, 2] = 1.0

    first = marching_squares_paths(
        field,
        origin_x=0.0,
        origin_y=0.0,
        pitch_x=1.0,
    )
    second = marching_squares_paths(
        field.copy(),
        origin_x=0.0,
        origin_y=0.0,
        pitch_x=1.0,
    )

    assert len(first) == len(second) == 2
    assert not np.array_equal(first[0][0], first[0][-1])
    np.testing.assert_array_equal(first[1][0], first[1][-1])
    for actual, repeated in zip(first, second, strict=True):
        np.testing.assert_array_equal(actual, repeated)


def test_planar_grid_marching_squares_paths_preserves_saddle_pairing() -> None:
    field = np.asarray([[1.0, -1.0], [-1.0, 1.0]])

    paths = marching_squares_paths(
        field,
        origin_x=0.0,
        origin_y=0.0,
        pitch_x=1.0,
    )

    assert len(paths) == 2
    np.testing.assert_array_equal(paths[0], [[0.5, 0.0], [1.0, 0.5]])
    np.testing.assert_array_equal(paths[1], [[0.5, 1.0], [0.0, 0.5]])


def test_planar_grid_marching_squares_paths_preserves_mask_and_sample_range() -> None:
    field = np.tile(np.asarray([-1.0, 1.0]), (4, 1))
    mask = np.ones(field.shape, dtype=np.uint8)
    mask[0, 0] = 0
    sample_field = np.tile(np.arange(4.0)[:, None], (1, 2))
    expected = np.asarray([[0.5, 1.0], [0.5, 2.0], [0.5, 3.0]])

    masked = marching_squares_paths(
        field,
        origin_x=0.0,
        origin_y=0.0,
        pitch_x=1.0,
        mask=mask,
    )
    sampled = marching_squares_paths(
        field,
        origin_x=0.0,
        origin_y=0.0,
        pitch_x=1.0,
        sample_field=sample_field,
        sample_range=(1.0, 3.0),
    )

    assert len(masked) == len(sampled) == 1
    np.testing.assert_array_equal(masked[0], expected)
    np.testing.assert_array_equal(sampled[0], expected)
