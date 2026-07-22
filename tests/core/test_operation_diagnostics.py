from __future__ import annotations

import numpy as np
import pytest

from grafix.core.effects.subdivide import MAX_SUBDIVISIONS, subdivide
from grafix.core.geometry_kernels.grid import plan_grid_from_bbox
from grafix.core.operation_diagnostics import (
    current_operation_diagnostics,
    emit_operation_diagnostic,
    grid_spec_from_bbox_with_diagnostic,
    operation_diagnostic_context,
)


def _line() -> tuple[np.ndarray, np.ndarray]:
    return (
        np.asarray([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]], dtype=np.float32),
        np.asarray([0, 2], dtype=np.int32),
    )


def _float32_stop_boundary_line() -> tuple[np.ndarray, np.ndarray]:
    endpoint = np.nextafter(np.float32(0.32), np.float32(np.inf))
    return (
        np.asarray([[0.0, 0.0, 0.0], [endpoint, 0.0, 0.0]], dtype=np.float32),
        np.asarray([0, 2], dtype=np.int32),
    )


def test_operation_diagnostic_context_isolated_and_deduplicated() -> None:
    with operation_diagnostic_context() as buffer:
        for _ in range(2):
            emit_operation_diagnostic(
                op="example",
                original_value=12,
                effective_value=10,
                reason="clamped",
            )

        assert len(buffer) == 1
        assert current_operation_diagnostics() == buffer.snapshot()

    assert current_operation_diagnostics() == ()


def test_subdivide_normal_path_has_no_diagnostic() -> None:
    with operation_diagnostic_context() as buffer:
        subdivide(_line(), subdivisions=1)

    assert buffer.snapshot() == ()


def test_subdivide_clamp_emits_one_original_and_effective_payload() -> None:
    requested = MAX_SUBDIVISIONS + 4
    with operation_diagnostic_context() as buffer:
        subdivide(_line(), subdivisions=requested)

    assert len(buffer) == 1
    diagnostic = buffer.snapshot()[0]
    assert diagnostic.op == "subdivide"
    assert diagnostic.original_value == requested
    assert diagnostic.effective_value == MAX_SUBDIVISIONS
    assert "clamped" in diagnostic.reason
    assert diagnostic.severity == "warning"


def test_subdivide_negative_value_raises_without_diagnostic() -> None:
    with operation_diagnostic_context() as buffer:
        with pytest.raises(ValueError, match="subdivisions"):
            subdivide(_line(), subdivisions=-2)

    assert buffer.snapshot() == ()


def test_subdivide_float32_early_stop_reports_actual_effective_level() -> None:
    with operation_diagnostic_context() as buffer:
        coords, offsets = subdivide(
            _float32_stop_boundary_line(),
            subdivisions=10,
        )

    assert coords.shape == (33, 3)
    assert offsets.tolist() == [0, 33]
    assert len(buffer) == 1
    diagnostic = buffer.snapshot()[0]
    assert diagnostic.original_value == 10
    assert diagnostic.effective_value == 5
    assert "minimum segment length stopped" in diagnostic.reason


def test_grid_planner_is_pure_on_normal_path() -> None:
    with operation_diagnostic_context() as buffer:
        plan = plan_grid_from_bbox(
            (0.0, 0.0),
            (2.0, 1.0),
            pitch=1.0,
            max_points=6,
            overflow="reject",
        )

    grid = plan.spec
    assert grid is not None
    assert plan.diagnostic is None
    assert buffer.snapshot() == ()


def test_grid_planner_returns_rejection_without_emitting() -> None:
    with operation_diagnostic_context() as buffer:
        plan = plan_grid_from_bbox(
            (0.0, 0.0),
            (2.0, 1.0),
            pitch=1.0,
            max_points=5,
            overflow="reject",
        )

    assert plan.spec is None
    assert plan.diagnostic is not None
    assert plan.diagnostic.original_value == (1.0, 6, 5, "reject")
    assert plan.diagnostic.effective_value is None
    assert (
        plan.diagnostic.reason
        == "requested grid exceeded the point limit and was rejected"
    )
    assert plan.diagnostic.severity == "warning"
    assert buffer.snapshot() == ()


def test_effect_side_grid_wrapper_emits_rejection_diagnostic() -> None:
    with operation_diagnostic_context() as buffer:
        grid = grid_spec_from_bbox_with_diagnostic(
            (0.0, 0.0),
            (2.0, 1.0),
            pitch=1.0,
            padding=0.0,
            max_points=5,
            overflow="reject",
        )

    assert grid is None
    assert len(buffer) == 1
    diagnostic = buffer.snapshot()[0]
    assert diagnostic.op == "GridSpec.from_bbox"
    assert diagnostic.original_value == (1.0, 6, 5, "reject")
    assert diagnostic.effective_value is None
    assert (
        diagnostic.reason
        == "requested grid exceeded the point limit and was rejected"
    )
    assert diagnostic.severity == "warning"


def test_grid_repeated_non_finite_rejection_is_deduplicated() -> None:
    with operation_diagnostic_context() as buffer:
        for _ in range(2):
            assert (
                grid_spec_from_bbox_with_diagnostic(
                    (0.0, 0.0),
                    (2.0, 1.0),
                    pitch=float("nan"),
                    padding=0.0,
                    max_points=4_000_000,
                    overflow="reject",
                )
                is None
            )

    assert len(buffer) == 1


def test_grid_coarsen_emits_one_diagnostic() -> None:
    with operation_diagnostic_context() as buffer:
        grid = grid_spec_from_bbox_with_diagnostic(
            (0.0, 0.0),
            (100.0, 100.0),
            pitch=1.0,
            padding=0.0,
            max_points=100,
            overflow="coarsen",
        )

    assert grid is not None
    assert len(buffer) == 1
    diagnostic = buffer.snapshot()[0]
    assert diagnostic.original_value == 1.0
    assert diagnostic.effective_value == grid.pitch
    assert diagnostic.reason == "grid pitch was coarsened to satisfy the point limit"
    assert diagnostic.severity == "warning"
