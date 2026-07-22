"""metaball effect のテスト群。"""

from __future__ import annotations

import numpy as np
import pytest

from grafix.api import E, G
from grafix.core.effects.metaball import metaball as metaball_impl
from grafix.core.operation_diagnostics import operation_diagnostic_context
from grafix.core.preview_quality import preview_quality_context
from grafix.core.operation_authoring import primitive
from grafix.core.realize import realize
from grafix.core.realized_geometry import GeomTuple


def _circle_xy(*, center: tuple[float, float], r: float, n: int = 96) -> np.ndarray:
    cx, cy = float(center[0]), float(center[1])
    t = np.linspace(0.0, 2.0 * np.pi, int(n), endpoint=False, dtype=np.float64)
    x = cx + float(r) * np.cos(t)
    y = cy + float(r) * np.sin(t)
    z = np.zeros_like(x)
    coords = np.stack([x, y, z], axis=1).astype(np.float32, copy=False)
    return np.concatenate([coords, coords[0:1]], axis=0)


@primitive
def metaball_test_two_circles_near_xy() -> GeomTuple:
    a = _circle_xy(center=(-12.0, 0.0), r=10.0)
    b = _circle_xy(center=(12.0, 0.0), r=10.0)
    coords = np.concatenate([a, b], axis=0).astype(np.float32, copy=False)
    offsets = np.array([0, int(a.shape[0]), int(a.shape[0] + b.shape[0])], dtype=np.int32)
    return coords, offsets


@primitive
def metaball_test_two_circles_far_xy() -> GeomTuple:
    a = _circle_xy(center=(-30.0, 0.0), r=10.0)
    b = _circle_xy(center=(30.0, 0.0), r=10.0)
    coords = np.concatenate([a, b], axis=0).astype(np.float32, copy=False)
    offsets = np.array([0, int(a.shape[0]), int(a.shape[0] + b.shape[0])], dtype=np.int32)
    return coords, offsets


@primitive
def metaball_test_donut_xy() -> GeomTuple:
    outer = _circle_xy(center=(0.0, 0.0), r=10.0)
    inner = _circle_xy(center=(0.0, 0.0), r=5.0)
    coords = np.concatenate([outer, inner], axis=0).astype(np.float32, copy=False)
    offsets = np.array(
        [0, int(outer.shape[0]), int(outer.shape[0] + inner.shape[0])],
        dtype=np.int32,
    )
    return coords, offsets


@primitive
def metaball_test_nonplanar_ring() -> GeomTuple:
    ring = _circle_xy(center=(0.0, 0.0), r=10.0, n=48).astype(np.float64, copy=True)
    ring[:, 2] = np.linspace(0.0, 1.0, int(ring.shape[0]), dtype=np.float64)
    coords = ring.astype(np.float32, copy=False)
    offsets = np.array([0, int(coords.shape[0])], dtype=np.int32)
    return coords, offsets


def test_metaball_connects_near_circles() -> None:
    g = G.metaball_test_two_circles_near_xy()
    out = E.metaball(radius=3.0, threshold=1.0, grid_pitch=0.5)(g)
    realized = realize(out)

    assert int(realized.offsets.size) == 2
    assert int(realized.coords.shape[0]) > 10
    np.testing.assert_allclose(realized.coords[:, 2], 0.0, rtol=0.0, atol=1e-4)


def test_metaball_does_not_connect_far_circles() -> None:
    g = G.metaball_test_two_circles_far_xy()
    out = E.metaball(radius=3.0, threshold=1.0, grid_pitch=0.5)(g)
    realized = realize(out)

    assert int(realized.offsets.size) == 3
    assert int(realized.coords.shape[0]) > 10
    np.testing.assert_allclose(realized.coords[:, 2], 0.0, rtol=0.0, atol=1e-4)


def test_metaball_rejects_nonpositive_radius() -> None:
    base = realize(G.metaball_test_two_circles_near_xy())

    with pytest.raises(ValueError, match="radius"):
        metaball_impl(
            (base.coords, base.offsets),
            radius=0.0,
            threshold=1.0,
            grid_pitch=0.5,
        )


def test_metaball_rejects_nonpositive_threshold() -> None:
    base = realize(G.metaball_test_two_circles_near_xy())

    with pytest.raises(ValueError, match="threshold"):
        metaball_impl(
            (base.coords, base.offsets),
            radius=3.0,
            threshold=0.0,
            grid_pitch=0.5,
        )


def test_metaball_nonplanar_is_noop() -> None:
    g = G.metaball_test_nonplanar_ring()
    base = realize(g)
    out = E.metaball(radius=3.0, threshold=1.0, grid_pitch=0.5)(g)
    realized = realize(out)

    assert realized.offsets.tolist() == base.offsets.tolist()
    np.testing.assert_allclose(realized.coords, base.coords, rtol=0.0, atol=1e-6)


def test_metaball_outputs_holes_for_donut() -> None:
    g = G.metaball_test_donut_xy()
    out = E.metaball(radius=1.0, threshold=1.0, grid_pitch=0.5)(g)
    realized = realize(out)

    assert int(realized.offsets.size) >= 3
    np.testing.assert_allclose(realized.coords[:, 2], 0.0, rtol=0.0, atol=1e-4)

    widths: list[float] = []
    for i in range(int(realized.offsets.size) - 1):
        s = int(realized.offsets[i])
        e = int(realized.offsets[i + 1])
        line = realized.coords[s:e, :2].astype(np.float64, copy=False)
        if line.shape[0] < 4:
            continue
        mins = np.min(line, axis=0)
        maxs = np.max(line, axis=0)
        widths.append(float(np.max(maxs - mins)))

    assert widths
    assert max(widths) > 1.5 * min(widths)


def test_metaball_output_exterior_filters_holes() -> None:
    g = G.metaball_test_donut_xy()
    both = realize(E.metaball(radius=1.0, threshold=1.0, grid_pitch=0.5, output="both")(g))
    ext = realize(E.metaball(radius=1.0, threshold=1.0, grid_pitch=0.5, output="exterior")(g))

    assert int(ext.offsets.size) >= 2
    assert int(ext.offsets.size) < int(both.offsets.size)

    widths: list[float] = []
    for i in range(int(ext.offsets.size) - 1):
        s = int(ext.offsets[i])
        e = int(ext.offsets[i + 1])
        line = ext.coords[s:e, :2].astype(np.float64, copy=False)
        if line.shape[0] < 4:
            continue
        mins = np.min(line, axis=0)
        maxs = np.max(line, axis=0)
        widths.append(float(np.max(maxs - mins)))

    assert widths
    assert min(widths) > 10.0


def test_metaball_draft_coarsens_grid_and_resamples_ring_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import grafix.core.effects.metaball as module

    seen_work: list[tuple[int, int]] = []

    def evaluate(
        xs: np.ndarray,
        ys: np.ndarray,
        ring_vertices: np.ndarray,
        ring_offsets: np.ndarray,
        inside_mask: np.ndarray,
        _inv_r2: float,
    ) -> np.ndarray:
        segment_count = int(ring_vertices.shape[0]) - (
            int(ring_offsets.shape[0]) - 1
        )
        seen_work.append((int(xs.size) * int(ys.size), segment_count))
        return inside_mask.astype(np.float64, copy=True)

    monkeypatch.setattr(module, "_evaluate_field_grid_numba", evaluate)
    coords = _circle_xy(center=(0.0, 0.0), r=100.0, n=1024)
    geometry = (coords, np.asarray([0, coords.shape[0]], dtype=np.int32))
    kwargs = {"radius": 3.0, "threshold": 0.5, "grid_pitch": 0.25}

    with operation_diagnostic_context() as diagnostics:
        with preview_quality_context("draft"):
            first = module.metaball(geometry, **kwargs)
    with preview_quality_context("draft"):
        second = module.metaball(geometry, **kwargs)
    with preview_quality_context("final"):
        module.metaball(geometry, **kwargs)

    assert seen_work[0] == seen_work[1]
    assert seen_work[0][0] <= module.DRAFT_MAX_GRID_POINTS
    assert seen_work[0][1] < 1024
    assert seen_work[2][0] > seen_work[0][0]
    assert seen_work[2][1] == 1024
    np.testing.assert_array_equal(first[0], second[0])
    np.testing.assert_array_equal(first[1], second[1])
    assert any(
        item.op == "metaball.grid_pitch"
        and item.original_value == 0.25
        and float(item.effective_value) > 0.25
        for item in diagnostics.snapshot()
    )
    assert any(
        item.op == "metaball.ring_segments"
        and item.original_value == 1024
        and int(item.effective_value) < 1024
        for item in diagnostics.snapshot()
    )


def test_metaball_draft_bounds_cells_times_segments_for_many_dense_rings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import grafix.core.effects.metaball as module

    rings = [
        _circle_xy(
            center=(5.0 * float(index % 16), 5.0 * float(index // 16)),
            r=1.0,
            n=128,
        )
        for index in range(128)
    ]
    coords = np.concatenate(rings, axis=0)
    counts = np.asarray([ring.shape[0] for ring in rings], dtype=np.int32)
    offsets = np.concatenate(
        [
            np.zeros((1,), dtype=np.int32),
            np.cumsum(counts, dtype=np.int32),
        ]
    )
    seen_work: list[int] = []

    def evaluate(
        xs: np.ndarray,
        ys: np.ndarray,
        ring_vertices: np.ndarray,
        ring_offsets: np.ndarray,
        inside_mask: np.ndarray,
        _inv_r2: float,
    ) -> np.ndarray:
        segment_count = int(ring_vertices.shape[0]) - (
            int(ring_offsets.shape[0]) - 1
        )
        seen_work.append(int(xs.size) * int(ys.size) * segment_count)
        return inside_mask.astype(np.float64, copy=True)

    monkeypatch.setattr(module, "_evaluate_field_grid_numba", evaluate)
    with operation_diagnostic_context() as diagnostics:
        with preview_quality_context("draft"):
            module.metaball(
                (coords, offsets),
                radius=1.0,
                threshold=0.5,
                grid_pitch=0.05,
            )

    assert seen_work
    assert seen_work[0] <= module.DRAFT_MAX_POINT_SEGMENTS
    assert any(
        item.op == "metaball.point_segments"
        and int(item.effective_value) <= module.DRAFT_MAX_POINT_SEGMENTS
        and int(item.original_value) > int(item.effective_value)
        for item in diagnostics.snapshot()
    )
