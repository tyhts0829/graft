from __future__ import annotations

import importlib
from types import ModuleType

import numpy as np
import pytest

from grafix.core.realized_geometry import GeomTuple


def _two_open_lines() -> GeomTuple:
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


def _square(*, size: float = 10.0) -> GeomTuple:
    coords = np.asarray(
        [
            [0.0, 0.0, 0.0],
            [size, 0.0, 0.0],
            [size, size, 0.0],
            [0.0, size, 0.0],
            [0.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.asarray([0, 5], dtype=np.int32)
    return coords, offsets


@pytest.mark.parametrize(
    ("module_name", "effect_name", "extra_kwargs"),
    [
        ("grafix.core.effects.lowpass", "lowpass", {}),
        ("grafix.core.effects.highpass", "highpass", {"gain": 1.0}),
    ],
)
def test_filter_cap_overflow_is_whole_geometry_noop_before_resample_allocation(
    module_name: str,
    effect_name: str,
    extra_kwargs: dict[str, float],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module(module_name)
    coords, offsets = _two_open_lines()
    monkeypatch.setattr(module, "MAX_TOTAL_VERTICES", 4)

    def unexpected_allocation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("resample output allocation must not run")

    monkeypatch.setattr(module, "resample_polylines", unexpected_allocation)
    effect = getattr(module, effect_name)
    out_coords, out_offsets = effect(
        (coords, offsets),
        step=1.0,
        sigma=1.0,
        closed="open",
        **extra_kwargs,
    )

    assert out_coords is coords
    assert out_offsets is offsets


@pytest.mark.parametrize(
    ("module_name", "effect_name", "extra_kwargs"),
    [
        ("grafix.core.effects.lowpass", "lowpass", {}),
        ("grafix.core.effects.highpass", "highpass", {"gain": 1.0}),
    ],
)
def test_filter_cap_boundary_keeps_every_line(
    module_name: str,
    effect_name: str,
    extra_kwargs: dict[str, float],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module(module_name)
    coords, offsets = _two_open_lines()
    monkeypatch.setattr(module, "MAX_TOTAL_VERTICES", 5)

    effect = getattr(module, effect_name)
    out_coords, out_offsets = effect(
        (coords, offsets),
        step=1.0,
        sigma=1.0,
        closed="open",
        **extra_kwargs,
    )

    assert out_coords.shape == (5, 3)
    assert out_offsets.tolist() == [0, 3, 5]
    assert np.all(np.isfinite(out_coords))


@pytest.mark.parametrize(
    ("module_name", "effect_name", "extra_kwargs"),
    [
        ("grafix.core.effects.lowpass", "lowpass", {}),
        ("grafix.core.effects.highpass", "highpass", {"gain": 1.0}),
    ],
)
def test_closed_filter_skips_consecutive_duplicate_segments(
    module_name: str,
    effect_name: str,
    extra_kwargs: dict[str, float],
) -> None:
    module = importlib.import_module(module_name)
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

    effect = getattr(module, effect_name)
    out_coords, out_offsets = effect(
        (coords, offsets),
        step=1.0,
        sigma=0.5,
        closed="closed",
        **extra_kwargs,
    )

    assert out_offsets.tolist() == [0, 9]
    assert np.all(np.isfinite(out_coords))
    np.testing.assert_array_equal(out_coords[0], out_coords[-1])
    assert np.unique(out_coords[:-1, :2], axis=0).shape[0] > 1


def _fail_if_called(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("grid allocation/evaluation must not run")


@pytest.mark.parametrize(
    ("module_name", "effect_name", "guarded_name", "expected_noop"),
    [
        (
            "grafix.core.effects.metaball",
            "metaball",
            "_evaluate_field_grid_numba",
            True,
        ),
            (
                "grafix.core.effects.isocontour",
                "isocontour",
                "signed_distance_grid_edt",
                False,
            ),
            (
                "grafix.core.effects.reaction_diffusion",
                "reaction_diffusion",
                "scanline_evenodd_mask",
                False,
            ),
    ],
)
def test_grid_effect_rejects_over_cap_before_grid_evaluation(
    module_name: str,
    effect_name: str,
    guarded_name: str,
    expected_noop: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module: ModuleType = importlib.import_module(module_name)
    coords, offsets = _square()
    monkeypatch.setattr(module, "MAX_GRID_POINTS", 4)
    monkeypatch.setattr(module, guarded_name, _fail_if_called)

    effect = getattr(module, effect_name)
    if effect_name == "metaball":
        out_coords, out_offsets = effect((coords, offsets), radius=1.0, grid_pitch=0.5)
    elif effect_name == "isocontour":
        out_coords, out_offsets = effect(
            (coords, offsets), spacing=2.0, max_dist=2.0, grid_pitch=0.5
        )
    else:
        out_coords, out_offsets = effect((coords, offsets), grid_pitch=0.5, steps=0)

    if expected_noop:
        assert out_coords is coords
        assert out_offsets is offsets
    else:
        assert out_coords.shape == (0, 3)
        assert out_offsets.tolist() == [0]


def test_growth_sdf_grid_coarsens_before_allocating() -> None:
    growth = importlib.import_module("grafix.core.effects.growth")
    coords, _offsets = _square(size=100.0)
    ring_vertices = coords[:, :2].astype(np.float64)
    ring_offsets = np.asarray([0, ring_vertices.shape[0]], dtype=np.int32)
    ring_mins = np.asarray([[0.0, 0.0]], dtype=np.float64)
    ring_maxs = np.asarray([[100.0, 100.0]], dtype=np.float64)

    sdf, origin_x, origin_y, effective_pitch = growth._build_sdf_grid(
        ring_vertices,
        ring_offsets,
        ring_mins,
        ring_maxs,
        pitch_hint=1.0,
        pad=0.0,
        max_points=25,
    )

    assert sdf.size <= 25
    assert effective_pitch > 1.0
    assert (origin_x, origin_y) == (0.0, 0.0)
