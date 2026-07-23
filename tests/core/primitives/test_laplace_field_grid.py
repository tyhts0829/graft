"""laplace_field_grid プリミティブの基本動作テスト。"""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from grafix import G
from grafix.core.primitives.laplace_field_grid import (
    _split_by_mask,
)
from grafix.core.realize import RealizeError, realize
from grafix.core.primitives import laplace_field_grid as _laplace_field_grid_module  # noqa: F401


def _packed_digest(coords: np.ndarray, offsets: np.ndarray) -> str:
    return hashlib.sha256(coords.tobytes() + offsets.tobytes()).hexdigest()


def _assert_realized_basic_invariants(coords: np.ndarray, offsets: np.ndarray) -> None:
    assert coords.ndim == 2
    assert coords.shape[1] == 3
    assert offsets.ndim == 1
    assert offsets.size >= 1
    assert int(offsets[0]) == 0
    assert int(offsets[-1]) == int(coords.shape[0])
    assert np.all(np.diff(offsets.astype(np.int64)) >= 0)
    assert np.isfinite(coords).all()


@pytest.mark.parametrize("preset", ["cylinder_uniform", "mobius", "exp"])
def test_laplace_field_grid_runs_and_finite(preset: str) -> None:
    """各 preset で例外なく実行でき、NaN/Inf を含まない。"""
    params: dict[str, object] = {
        "preset": preset,
        "u_min": -4.0,
        "u_max": 4.0,
        "v_min": -4.0,
        "v_max": 4.0,
        "n_u": 8,
        "n_v": 8,
        "samples": 200,
    }
    if preset == "cylinder_uniform":
        params.update(
            {
                "a": 1.0,
                "U": 1.0,
                "gap": 0.01,
                "draw_boundary": True,
                "boundary_samples": 200,
            }
        )
    elif preset == "mobius":
        params.update(
            {
                "alpha_re": 1.0,
                "alpha_im": 0.0,
                "beta_re": 0.2,
                "beta_im": 0.1,
                "gamma_re": 0.05,
                "gamma_im": 0.0,
                "delta_re": 1.0,
                "delta_im": 0.0,
            }
        )
    else:  # preset == "exp"
        params.update({"k_re": 0.35, "k_im": 0.6})

    g = G.laplace_field_grid(**params)
    realized = realize(g)
    _assert_realized_basic_invariants(realized.coords, realized.offsets)
    assert realized.coords.shape[0] > 0


def test_laplace_field_grid_cylinder_respects_gap_mask() -> None:
    """cylinder_uniform で円内部（gap 込み）に点が侵入しない。"""
    a = 1.0
    gap = 0.02
    g = G.laplace_field_grid(
        preset="cylinder_uniform",
        u_min=-4.0,
        u_max=4.0,
        v_min=-4.0,
        v_max=4.0,
        n_u=10,
        n_v=10,
        samples=250,
        a=a,
        U=1.0,
        gap=gap,
        draw_boundary=False,
    )
    realized = realize(g)
    _assert_realized_basic_invariants(realized.coords, realized.offsets)

    r = np.hypot(realized.coords[:, 0], realized.coords[:, 1])
    assert float(np.min(r)) >= a * (1.0 + gap) - 1e-5


def test_laplace_field_grid_rejects_invalid_samples() -> None:
    """samples<2 は ValueError。"""
    g = G.laplace_field_grid(preset="exp", n_u=2, n_v=2, samples=1)
    with pytest.raises(RealizeError):
        realize(g)


@pytest.mark.parametrize(
    ("kwargs", "parameter"),
    [
        ({"u_min": 1.0, "u_max": -1.0}, "u_min"),
        ({"v_min": 1.0, "v_max": -1.0}, "v_min"),
    ],
)
def test_laplace_field_grid_rejects_reversed_ranges(
    kwargs: dict[str, float],
    parameter: str,
) -> None:
    with pytest.raises(RealizeError) as exc_info:
        realize(G.laplace_field_grid(preset="exp", **kwargs))

    assert isinstance(exc_info.value.__cause__, ValueError)
    assert parameter in str(exc_info.value.__cause__)


def test_laplace_field_grid_allows_degenerate_ranges() -> None:
    realized = realize(
        G.laplace_field_grid(
            preset="exp",
            u_min=0.0,
            u_max=0.0,
            v_min=0.0,
            v_max=0.0,
            n_u=1,
            n_v=1,
            samples=2,
        )
    )

    _assert_realized_basic_invariants(realized.coords, realized.offsets)


def test_laplace_field_grid_allows_a_zero() -> None:
    """a=0 でも例外にならない（円柱なし＝一様場の退化ケース）。"""
    g = G.laplace_field_grid(
        preset="cylinder_uniform",
        u_min=-2.0,
        u_max=2.0,
        v_min=-2.0,
        v_max=2.0,
        n_u=6,
        n_v=6,
        samples=120,
        a=0.0,
        U=1.0,
        gap=0.02,
        draw_boundary=True,
    )
    realized = realize(g)
    _assert_realized_basic_invariants(realized.coords, realized.offsets)
    assert realized.coords.shape[0] > 0


def test_laplace_field_grid_allows_U_zero() -> None:
    """U=0 でも例外にならない（線は省略され、境界のみ描画される）。"""
    g = G.laplace_field_grid(
        preset="cylinder_uniform",
        u_min=-2.0,
        u_max=2.0,
        v_min=-2.0,
        v_max=2.0,
        n_u=6,
        n_v=6,
        samples=120,
        a=1.0,
        U=0.0,
        gap=0.02,
        draw_boundary=True,
        boundary_samples=200,
    )
    realized = realize(g)
    _assert_realized_basic_invariants(realized.coords, realized.offsets)
    assert realized.coords.shape[0] > 0


def test_laplace_field_grid_presets_are_distinct() -> None:
    """preset の切り替えで座標分布が変わる（退行防止の最小チェック）。"""

    base: dict[str, object] = {
        "u_min": -3.0,
        "u_max": 3.0,
        "v_min": -3.0,
        "v_max": 3.0,
        "n_u": 6,
        "n_v": 6,
        "samples": 120,
    }

    g_cyl = G.laplace_field_grid(
        **base,
        preset="cylinder_uniform",
        a=1.0,
        U=1.0,
        gap=0.01,
        draw_boundary=False,
    )
    g_mob = G.laplace_field_grid(
        **base,
        preset="mobius",
        alpha_re=1.0,
        alpha_im=0.0,
        beta_re=0.3,
        beta_im=0.2,
        gamma_re=0.1,
        gamma_im=0.0,
        delta_re=1.0,
        delta_im=0.0,
    )
    g_exp = G.laplace_field_grid(**base, preset="exp", k_re=0.4, k_im=0.8)

    r_cyl = realize(g_cyl)
    r_mob = realize(g_mob)
    r_exp = realize(g_exp)

    def bbox(coords: np.ndarray) -> np.ndarray:
        mins = np.min(coords[:, 0:2], axis=0)
        maxs = np.max(coords[:, 0:2], axis=0)
        return np.concatenate([mins, maxs], axis=0)

    b_cyl = bbox(r_cyl.coords)
    b_mob = bbox(r_mob.coords)
    b_exp = bbox(r_exp.coords)

    assert not np.allclose(b_cyl, b_mob)
    assert not np.allclose(b_cyl, b_exp)
    assert not np.allclose(b_mob, b_exp)


def test_laplace_field_grid_fixed_output_characterization() -> None:
    realized = realize(
        G.laplace_field_grid(
            preset="exp",
            u_min=-1.5,
            u_max=1.5,
            v_min=-1.0,
            v_max=1.0,
            n_u=3,
            n_v=3,
            samples=12,
            center=(0.2, -0.3, 0.4),
            scale=0.75,
            rotate=17.0,
            clip=True,
            clip_xmin=-2.0,
            clip_xmax=2.0,
            clip_ymin=-2.0,
            clip_ymax=2.0,
            k_re=0.4,
            k_im=0.6,
        )
    )

    assert realized.offsets.tolist() == [0, 12, 24, 36, 46, 58, 70]
    assert _packed_digest(realized.coords, realized.offsets) == (
        "295c99056d5bb11ec8ffc3dac74bb0d6b42508ba6eaaf8f26f6aba51888c156d"
    )


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        (
            {
                "preset": "cylinder_uniform",
                "u_min": -1.0,
                "u_max": 1.0,
                "v_min": -1.0,
                "v_max": 1.0,
                "n_u": 2,
                "n_v": 2,
                "samples": 2,
                "a": 0.0,
                "U": 1.0,
                "draw_boundary": False,
            },
            [
                [-1.0, -1.0, 0.0],
                [-1.0, 1.0, 0.0],
                [1.0, -1.0, 0.0],
                [1.0, 1.0, 0.0],
                [-1.0, -1.0, 0.0],
                [1.0, -1.0, 0.0],
                [-1.0, 1.0, 0.0],
                [1.0, 1.0, 0.0],
            ],
        ),
        (
            {
                "preset": "mobius",
                "u_min": -1.0,
                "u_max": 1.0,
                "v_min": -1.0,
                "v_max": 1.0,
                "n_u": 2,
                "n_v": 2,
                "samples": 2,
                "beta_re": 1.0,
            },
            [
                [0.0, -1.0, 0.0],
                [0.0, 1.0, 0.0],
                [2.0, -1.0, 0.0],
                [2.0, 1.0, 0.0],
                [0.0, -1.0, 0.0],
                [2.0, -1.0, 0.0],
                [0.0, 1.0, 0.0],
                [2.0, 1.0, 0.0],
            ],
        ),
        (
            {
                "preset": "exp",
                "u_min": 0.0,
                "u_max": np.log(2.0),
                "v_min": 0.0,
                "v_max": 0.0,
                "n_u": 2,
                "n_v": 1,
                "samples": 2,
                "k_re": 1.0,
                "k_im": 0.0,
            },
            [
                [1.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [2.0, 0.0, 0.0],
                [2.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [2.0, 0.0, 0.0],
            ],
        ),
    ],
)
def test_laplace_field_grid_small_preset_characterization(
    params: dict[str, object],
    expected: list[list[float]],
) -> None:
    realized = realize(G.laplace_field_grid(**params))

    np.testing.assert_allclose(
        realized.coords,
        np.asarray(expected, dtype=np.float32),
        rtol=0.0,
        atol=1e-6,
    )
    assert realized.offsets.tolist() == list(range(0, len(expected) + 1, 2))


def test_laplace_field_grid_appends_boundary_after_grid_lines() -> None:
    realized = realize(
        G.laplace_field_grid(
            preset="cylinder_uniform",
            u_min=4.0,
            u_max=4.0,
            v_min=-1.0,
            v_max=1.0,
            n_u=1,
            n_v=0,
            samples=3,
            a=0.5,
            U=1.0,
            gap=0.0,
            draw_boundary=True,
            boundary_samples=5,
        )
    )

    expected_boundary = np.array(
        [
            [0.5, 0.0, 0.0],
            [0.0, 0.5, 0.0],
            [-0.5, 0.0, 0.0],
            [0.0, -0.5, 0.0],
            [0.5, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    assert realized.offsets.tolist() == [0, 3, 8]
    np.testing.assert_allclose(
        realized.coords[3:],
        expected_boundary,
        rtol=0.0,
        atol=1e-6,
    )


@pytest.mark.parametrize(
    "mask",
    [
        np.ones(12, dtype=np.bool_),
        np.zeros(12, dtype=np.bool_),
        np.array(
            [False, True, True, False, True, False, True, True, True, False],
            dtype=np.bool_,
        ),
    ],
)
def test_split_by_mask_fast_paths_preserve_runs(mask: np.ndarray) -> None:
    """all/none/mixedの各経路が2点以上のTrue runだけを順番に返す。"""

    points = np.arange(mask.size * 3, dtype=np.float64).reshape((-1, 3))
    expected: list[np.ndarray] = []
    start = -1
    for index, keep in enumerate(mask):
        if bool(keep) and start < 0:
            start = index
        elif not bool(keep) and start >= 0:
            if index - start >= 2:
                expected.append(points[start:index])
            start = -1
    if start >= 0 and mask.size - start >= 2:
        expected.append(points[start:])

    actual = _split_by_mask(points, mask)
    assert len(actual) == len(expected)
    for actual_piece, expected_piece in zip(actual, expected, strict=True):
        np.testing.assert_array_equal(actual_piece, expected_piece)
        assert np.shares_memory(actual_piece, points)
