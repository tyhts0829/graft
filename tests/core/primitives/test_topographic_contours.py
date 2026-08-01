from __future__ import annotations

import numpy as np
import pytest

from grafix import E, G
from grafix.core.primitives.topographic_contours import topographic_contours
from grafix.core.realize import realize
from grafix.core.resource_budget import (
    ResourceBudget,
    ResourceLimitError,
    resource_budget_context,
)


def _small(**overrides: object) -> tuple[np.ndarray, np.ndarray]:
    arguments: dict[str, object] = {
        "width": 24.0,
        "height": 30.0,
        "grid_pitch": 1.0,
        "level_count": 8,
    }
    arguments.update(overrides)
    return topographic_contours(**arguments)  # type: ignore[arg-type]


def test_topographic_contours_returns_standard_connected_geometry() -> None:
    coords, offsets = _small(center=(10.0, 20.0, 3.0))

    assert coords.dtype == np.float32
    assert offsets.dtype == np.int32
    assert coords.flags.c_contiguous and coords.flags.writeable
    assert offsets.flags.c_contiguous and offsets.flags.writeable
    assert coords.shape[1] == 3
    assert offsets[0] == 0 and offsets[-1] == coords.shape[0]
    assert np.isfinite(coords).all()
    assert coords.shape[0] > 0
    assert float(coords[:, 0].min()) >= 10.0 - 12.0 - 1e-5
    assert float(coords[:, 0].max()) <= 10.0 + 12.0 + 1e-5
    assert float(coords[:, 1].min()) >= 20.0 - 15.0 - 1e-5
    assert float(coords[:, 1].max()) <= 20.0 + 15.0 + 1e-5
    np.testing.assert_array_equal(coords[:, 2], np.float32(3.0))

    lengths = np.diff(offsets)
    assert np.any(lengths > 2)
    raw_segment_limit = 2 * 24 * 30 * 8
    assert int(offsets.size - 1) < raw_segment_limit // 10


def test_topographic_contours_is_byte_deterministic_and_returns_fresh_arrays() -> None:
    first_coords, first_offsets = _small()
    second_coords, second_offsets = _small()

    np.testing.assert_array_equal(second_coords, first_coords)
    np.testing.assert_array_equal(second_offsets, first_offsets)
    assert second_coords is not first_coords
    assert second_offsets is not first_offsets


def test_topographic_contours_phase_is_periodic_in_degrees() -> None:
    first_coords, first_offsets = _small(phase=37.0)
    second_coords, second_offsets = _small(phase=397.0)

    np.testing.assert_array_equal(second_coords, first_coords)
    np.testing.assert_array_equal(second_offsets, first_offsets)


def test_topographic_contours_zero_frequency_disables_warp() -> None:
    first_coords, first_offsets = _small(
        field_warp=0.0,
        warp_frequency=0.0,
        phase=0.0,
    )
    second_coords, second_offsets = _small(
        field_warp=3.0,
        warp_frequency=0.0,
        phase=45.0,
    )

    np.testing.assert_array_equal(second_coords, first_coords)
    np.testing.assert_array_equal(second_offsets, first_offsets)


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("seed", 272),
        ("focus_count", 4),
        ("focus_spread", 1.3),
        ("level_count", 7),
        ("field_warp", 0.0),
        ("warp_frequency", 1.6),
        ("phase", 73.0),
        ("grid_pitch", 1.5),
    ),
)
def test_topographic_contours_parameters_change_output(name: str, value: object) -> None:
    base_coords, base_offsets = _small()
    changed_coords, changed_offsets = _small(**{name: value})

    assert not (
        np.array_equal(changed_coords, base_coords)
        and np.array_equal(changed_offsets, base_offsets)
    )


@pytest.mark.parametrize(
    "arguments",
    (
        {"width": 0.0},
        {"height": -1.0},
        {"seed": -1},
        {"focus_count": 0},
        {"focus_spread": 0.0},
        {"level_count": 0},
        {"field_warp": -1.0},
        {"warp_frequency": -1.0},
        {"phase": float("nan")},
        {"grid_pitch": 0.0},
        {"center": (0.0, float("inf"), 0.0)},
    ),
)
def test_topographic_contours_rejects_invalid_parameters(
    arguments: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        _small(**arguments)


def test_topographic_contours_rejects_grid_before_large_allocation() -> None:
    with pytest.raises(ResourceLimitError, match="sampling grid"):
        topographic_contours(width=10_000.0, height=10_000.0, grid_pitch=0.01)


def test_topographic_contours_rejects_excessive_focus_count_on_tiny_grid() -> None:
    with pytest.raises(ResourceLimitError, match="focus_count"):
        topographic_contours(
            width=1.0,
            height=1.0,
            grid_pitch=1.0,
            focus_count=1_000_000,
        )


def test_topographic_contours_rejects_excessive_field_work() -> None:
    with pytest.raises(ResourceLimitError, match="field評価量"):
        topographic_contours(
            width=200.0,
            height=200.0,
            grid_pitch=1.0,
            focus_count=4_096,
        )


def test_topographic_contours_rejects_excessive_level_work() -> None:
    with pytest.raises(ResourceLimitError, match="level_count"):
        topographic_contours(
            width=1.0,
            height=1.0,
            grid_pitch=1.0,
            level_count=1_000_000,
        )


@pytest.mark.parametrize(
    "arguments",
    (
        {"center": (0.0, 0.0, 1.0e40)},
        {"center": (1.0e40, 0.0, 0.0)},
        {"width": 1.0e40, "grid_pitch": 1.0e40},
    ),
)
def test_topographic_contours_rejects_noncanonical_float32_bounds(
    arguments: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="float32"):
        topographic_contours(**arguments)  # type: ignore[arg-type]


def test_topographic_contours_honors_active_geometry_budget() -> None:
    budget = ResourceBudget(
        max_output_vertices=100,
        max_output_lines=100,
        max_output_bytes=10_000,
    )
    with resource_budget_context(budget):
        with pytest.raises(ResourceLimitError, match="resource budget"):
            _small()


def test_topographic_contours_activate_false_returns_empty_geometry() -> None:
    realized = realize(G.topographic_contours(activate=False))

    assert realized.coords.shape == (0, 3)
    np.testing.assert_array_equal(realized.offsets, [0])


def test_topographic_contours_composes_with_transform_and_clip_effects() -> None:
    contours = G.topographic_contours(
        width=24.0,
        height=30.0,
        grid_pitch=1.0,
        level_count=8,
    )
    transformed = E.rotate(rotation=(0.0, 0.0, 15.0))(
        E.translate(delta=(3.0, -2.0, 0.0))(contours)
    )
    clipped = E.clip()(contours, G.rect(width=18.0, height=22.0))

    transformed_realized = realize(transformed)
    clipped_realized = realize(clipped)
    assert transformed_realized.coords.shape[0] > 0
    assert clipped_realized.coords.shape[0] > 0
    assert transformed_realized.coords.dtype == np.float32
    assert clipped_realized.coords.dtype == np.float32
