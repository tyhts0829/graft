"""fill effect のハッチ生成に関するテスト群。"""

from __future__ import annotations

import numpy as np
import pytest

from grafix.api import E, G
from grafix.core.effects.fill import (
    _build_evenodd_groups,
    _generate_line_fill_evenodd_multi,
    _generate_y_values,
    _pack_planar_fill_chunks,
    _point_in_polygon_coords_njit,
    _polygon_area_abs,
    _scanline_endpoints_njit,
    _spacing_from_density,
    fill as fill_effect,
)
from grafix.core.geometry_kernels.planar import PlanarFrame, planarity_threshold
from grafix.core.operation_authoring import primitive
from grafix.core.realize import RealizeError, RealizeSession, realize
from grafix.core.realized_geometry import GeomTuple, RealizedGeometry


@primitive
def fill_test_square() -> GeomTuple:
    """一辺 10 の正方形（閉ポリライン）を返す。"""
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
            [0.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, coords.shape[0]], dtype=np.int32)
    return coords, offsets


@primitive
def fill_test_rectangle(
    width: float,
    height: float,
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> GeomTuple:
    """指定した原点を左下とする軸平行の閉じた長方形を返す。"""
    x, y, z = origin
    coords = np.asarray(
        [
            [x, y, z],
            [x + width, y, z],
            [x + width, y + height, z],
            [x, y + height, z],
            [x, y, z],
        ],
        dtype=np.float32,
    )
    offsets = np.asarray([0, coords.shape[0]], dtype=np.int32)
    return coords, offsets


@primitive
def fill_test_concave_u() -> GeomTuple:
    """24 x 24のU字型を一つのconcave ringとして返す。"""
    coords = np.asarray(
        [
            [0.0, 0.0, 0.0],
            [24.0, 0.0, 0.0],
            [24.0, 24.0, 0.0],
            [16.0, 24.0, 0.0],
            [16.0, 8.0, 0.0],
            [8.0, 8.0, 0.0],
            [8.0, 24.0, 0.0],
            [0.0, 24.0, 0.0],
            [0.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    return coords, np.asarray([0, coords.shape[0]], dtype=np.int32)


@primitive
def fill_test_tilted_collinear_start() -> GeomTuple:
    """先頭3点が共線で、z=x+y の傾斜平面上にある閉ポリラインを返す。"""
    coords = np.asarray(
        [
            [0.0, 0.0, 0.0],
            [5.0, 0.0, 5.0],
            [10.0, 0.0, 10.0],
            [10.0, 10.0, 20.0],
            [0.0, 10.0, 10.0],
            [0.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.asarray([0, coords.shape[0]], dtype=np.int32)
    return coords, offsets


@primitive
def fill_test_square_with_hole() -> GeomTuple:
    """外周+穴（2 輪郭）の正方形を返す。"""
    outer = np.array(
        [
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
            [0.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    hole = np.array(
        [
            [3.0, 3.0, 0.0],
            [7.0, 3.0, 0.0],
            [7.0, 7.0, 0.0],
            [3.0, 7.0, 0.0],
            [3.0, 3.0, 0.0],
        ],
        dtype=np.float32,
    )
    coords = np.concatenate([outer, hole], axis=0)
    offsets = np.array([0, outer.shape[0], outer.shape[0] + hole.shape[0]], dtype=np.int32)
    return coords, offsets


@primitive
def fill_test_two_squares() -> GeomTuple:
    """X 方向に離れた、一辺 10 の正方形を 2 個返す。"""
    first = np.array(
        [
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
            [0.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    second = first + np.array([20.0, 0.0, 0.0], dtype=np.float32)
    coords = np.concatenate([first, second], axis=0)
    offsets = np.array([0, first.shape[0], coords.shape[0]], dtype=np.int32)
    return coords, offsets


@primitive
def fill_test_two_planar_faces() -> GeomTuple:
    """全体では非平面になる一辺10のXY面と一辺40の斜面を返す。"""
    first = np.array(
        [
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
            [0.0, 0.0, 0.0],
        ],
        dtype=np.float64,
    )
    origin = np.array([30.0, 0.0, 5.0], dtype=np.float64)
    axis_u = np.array([1.0, 0.0, 1.0], dtype=np.float64) / np.sqrt(2.0)
    axis_v = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    local = np.array(
        [[0.0, 0.0], [40.0, 0.0], [40.0, 40.0], [0.0, 40.0], [0.0, 0.0]],
        dtype=np.float64,
    )
    second = origin + local[:, :1] * axis_u + local[:, 1:] * axis_v
    coords = np.concatenate([first, second], axis=0).astype(np.float32)
    offsets = np.array([0, first.shape[0], coords.shape[0]], dtype=np.int32)
    return coords, offsets


@primitive
def fill_test_empty() -> GeomTuple:
    """空のジオメトリを返す。"""
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return coords, offsets


def _iter_polylines(realized: RealizedGeometry):
    offsets = realized.offsets
    for i in range(len(offsets) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        yield realized.coords[s:e]


def _point_inside(point: np.ndarray, polygon: np.ndarray) -> bool:
    return bool(
        _point_in_polygon_coords_njit(
            polygon,
            0,
            int(polygon.shape[0]),
            float(point[0]),
            float(point[1]),
        )
    )


def _generate_y_values_legacy_reference(
    min_y: float,
    max_y: float,
    base_spacing: float,
    spacing_gradient: float,
) -> np.ndarray:
    """``min_spacing`` 導入前の走査 level 算術を凍結して再現する。"""
    if not np.isfinite(base_spacing) or base_spacing <= 0.0:
        return np.empty(0, dtype=np.float32)
    if max_y <= min_y:
        return np.empty(0, dtype=np.float32)

    start = float(min_y) + 0.5 * float(base_spacing)
    if start >= max_y:
        mid = 0.5 * (float(min_y) + float(max_y))
        return np.asarray([mid], dtype=np.float32)

    if abs(spacing_gradient) < 1e-6:
        return np.arange(start, max_y, base_spacing, dtype=np.float32)

    height = max_y - min_y
    k = spacing_gradient
    if abs(k) < 1e-3:
        c = 1.0
    else:
        c = k / (2.0 * float(np.sinh(k / 2.0)))

    y_values: list[float] = []
    y = float(start)
    min_step = base_spacing * 1e-3
    while y < max_y:
        t = (y - min_y) / height
        factor = c * float(np.exp(k * (t - 0.5)))
        step = base_spacing * max(factor, 0.0)
        if step < min_step:
            step = min_step
        y_values.append(y)
        y += step
    if not y_values:
        mid = 0.5 * (float(min_y) + float(max_y))
        return np.asarray([mid], dtype=np.float32)
    return np.asarray(y_values, dtype=np.float32)


def _level_tolerance(levels: np.ndarray, reference_spacing: float) -> float:
    """float32 level の絶対座標と基準pitchに応じた比較許容差を返す。"""
    assert levels.size > 0
    assert reference_spacing > 0.0
    scale = max(1.0, float(np.max(np.abs(levels))))
    tolerance = max(
        float(np.finfo(np.float32).eps) * scale * 16.0,
        abs(float(reference_spacing)) * 2e-5,
    )
    assert 0.0 < tolerance < reference_spacing * 0.1
    return tolerance


def _distinct_levels(
    levels: np.ndarray,
    *,
    tolerance: float,
) -> np.ndarray:
    """hole 分割などで重複した近接 level を一つにまとめる。"""
    ordered = np.sort(np.asarray(levels, dtype=np.float64))
    if ordered.size == 0:
        return ordered
    distinct = [float(ordered[0])]
    for value in ordered[1:]:
        if float(value) - distinct[-1] > tolerance:
            distinct.append(float(value))
    return np.asarray(distinct, dtype=np.float64)


def _assert_minimum_level_spacing(
    levels: np.ndarray,
    min_spacing: float,
) -> None:
    """2 本以上の distinct level が下限以上の間隔を持つことを検証する。"""
    assert levels.size >= 2, "pitch を測れる distinct scanline level が必要"
    tolerance = _level_tolerance(levels, min_spacing)
    differences = np.diff(levels.astype(np.float64, copy=False))
    assert np.all(differences > 0.0)
    assert np.all(differences >= min_spacing - tolerance)


def _assert_uniform_level_spacing(
    levels: np.ndarray,
    expected_spacing: float,
) -> None:
    """distinct level が指定pitchで等間隔に並ぶことを検証する。"""
    assert levels.size >= 2, "pitch を測れる distinct scanline level が必要"
    tolerance = _level_tolerance(levels, expected_spacing)
    differences = np.diff(levels.astype(np.float64, copy=False))
    assert np.all(differences > 0.0)
    np.testing.assert_allclose(
        differences,
        expected_spacing,
        rtol=0.0,
        atol=tolerance,
    )


def _projected_family_levels_from_polylines(
    polylines: list[np.ndarray],
    *,
    angle_deg: float,
    reference_spacing: float,
) -> np.ndarray:
    """指定方向の 2 点 hatch を選び、法線方向の distinct level を返す。"""
    angle_rad = float(np.deg2rad(angle_deg))
    tangent = np.array(
        [np.cos(angle_rad), np.sin(angle_rad), 0.0],
        dtype=np.float64,
    )
    normal = np.array(
        [-np.sin(angle_rad), np.cos(angle_rad), 0.0],
        dtype=np.float64,
    )
    levels: list[float] = []
    for segment in polylines:
        if segment.shape != (2, 3):
            continue
        direction = segment[1].astype(np.float64) - segment[0].astype(np.float64)
        length = float(np.linalg.norm(direction))
        if length <= 1e-9:
            continue
        if abs(float(np.dot(direction / length, tangent))) < 0.999:
            continue
        midpoint = np.mean(segment.astype(np.float64), axis=0)
        levels.append(float(np.dot(midpoint, normal)))

    raw = np.asarray(levels, dtype=np.float64)
    assert raw.size > 0, f"{angle_deg} 度 family の hatch が必要"
    tolerance = _level_tolerance(raw, reference_spacing)
    return _distinct_levels(raw, tolerance=tolerance)


def _projected_family_levels(
    realized: RealizedGeometry,
    *,
    angle_deg: float,
    reference_spacing: float,
) -> np.ndarray:
    """実体化済みgeometryから指定方向のdistinct levelを返す。"""
    return _projected_family_levels_from_polylines(
        list(_iter_polylines(realized)),
        angle_deg=angle_deg,
        reference_spacing=reference_spacing,
    )


def _projected_normal_span(coords: np.ndarray, angle_deg: float) -> float:
    """指定hatch familyの法線方向へ入力座標を投影したspanを返す。"""
    angle_rad = float(np.deg2rad(angle_deg))
    normal = np.asarray(
        [-np.sin(angle_rad), np.cos(angle_rad), 0.0],
        dtype=np.float64,
    )
    projected = coords.astype(np.float64, copy=False) @ normal
    return float(np.max(projected) - np.min(projected))


def _scanline_endpoints_reference(
    ex1: np.ndarray,
    ey1: np.ndarray,
    ex2: np.ndarray,
    ey2: np.ndarray,
    edx: np.ndarray,
    edy: np.ndarray,
    y_values: np.ndarray,
) -> np.ndarray:
    """高速化前と同じ NumPy 演算順で scanline endpoint を生成する。"""

    scanlines: list[tuple[np.float32, np.ndarray]] = []
    segment_count = 0
    for y in y_values:
        yy = float(y)
        mask = ((ey1 <= yy) & (yy < ey2)) | ((ey2 <= yy) & (yy < ey1))
        mask &= edy != 0.0
        if not np.any(mask):
            continue
        xs = ex1[mask] + (yy - ey1[mask]) * edx[mask] / edy[mask]
        if xs.size < 2:
            continue
        xs_sorted = np.sort(xs.astype(np.float32, copy=False))
        valid_count = sum(
            float(xs_sorted[index + 1] - xs_sorted[index]) > 1e-9
            for index in range(0, int(xs_sorted.size) - 1, 2)
        )
        if valid_count:
            scanlines.append((y, xs_sorted))
            segment_count += valid_count

    endpoints = np.empty((2 * segment_count, 2), dtype=np.float32)
    cursor = 0
    for y, xs_sorted in scanlines:
        for index in range(0, int(xs_sorted.size) - 1, 2):
            x_a = xs_sorted[index]
            x_b = xs_sorted[index + 1]
            if float(x_b - x_a) <= 1e-9:
                continue
            endpoints[cursor] = (x_a, y)
            endpoints[cursor + 1] = (x_b, y)
            cursor += 2
    return endpoints


@pytest.mark.parametrize("spacing_gradient", [-4.0, 0.0, 4.0])
def test_fill_y_values_apply_min_spacing_to_every_step(
    spacing_gradient: float,
) -> None:
    min_spacing = 0.75
    levels = _generate_y_values(
        0.0,
        10.0,
        0.1,
        spacing_gradient,
        min_spacing,
    )

    assert levels[0] == np.float32(0.05)
    _assert_minimum_level_spacing(levels, min_spacing)


@pytest.mark.parametrize("spacing_gradient", [-4.0, 0.0, 4.0])
def test_fill_y_values_zero_floor_matches_frozen_legacy_arithmetic(
    spacing_gradient: float,
) -> None:
    expected = _generate_y_values_legacy_reference(
        -1.25,
        8.75,
        0.37,
        spacing_gradient,
    )
    actual = _generate_y_values(
        -1.25,
        8.75,
        0.37,
        spacing_gradient,
        0.0,
    )

    assert expected.size >= 2
    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("spacing_gradient", [-4.0, 0.0, 4.0])
def test_fill_y_values_inactive_positive_floor_preserves_legacy_levels(
    spacing_gradient: float,
) -> None:
    expected = _generate_y_values_legacy_reference(
        0.0,
        10.0,
        0.4,
        spacing_gradient,
    )
    legacy_differences = np.diff(expected.astype(np.float64))
    assert legacy_differences.size > 0
    min_spacing = float(np.min(legacy_differences)) * 0.25
    actual = _generate_y_values(
        0.0,
        10.0,
        0.4,
        spacing_gradient,
        min_spacing,
    )

    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("spacing_gradient", [-4.0, 0.0, 4.0])
def test_fill_y_values_floor_larger_than_extent_returns_one_level(
    spacing_gradient: float,
) -> None:
    levels = _generate_y_values(
        0.0,
        1.0,
        0.25,
        spacing_gradient,
        2.0,
    )

    np.testing.assert_array_equal(levels, np.array([0.125], dtype=np.float32))


def test_fill_scanline_kernel_is_bitwise_equal_to_numpy_reference() -> None:
    outer = np.asarray(
        [[0, 0], [10, 0], [10, 10], [0, 10]],
        dtype=np.float32,
    )
    hole = np.asarray(
        [[3, 3], [7, 3], [7, 7], [3, 7]],
        dtype=np.float32,
    )
    edges = []
    for ring in (outer, hole):
        following = np.roll(ring, -1, axis=0)
        edges.append(np.concatenate([ring, following], axis=1))
    packed = np.concatenate(edges, axis=0).astype(np.float32, copy=False)
    ex1 = packed[:, 0]
    ey1 = packed[:, 1]
    ex2 = packed[:, 2]
    ey2 = packed[:, 3]
    edx = ex2 - ex1
    edy = ey2 - ey1
    y_values = np.asarray(
        [-1.0, 0.0, 0.5, 3.0, 3.5, 6.5, 7.0, 9.5, 10.0],
        dtype=np.float32,
    )

    expected = _scanline_endpoints_reference(
        ex1,
        ey1,
        ex2,
        ey2,
        edx,
        edy,
        y_values,
    )
    actual = _scanline_endpoints_njit(
        ex1,
        ey1,
        ey2,
        edx,
        edy,
        y_values,
    )

    np.testing.assert_array_equal(actual, expected)

    rng = np.random.default_rng(20260719)
    for _ in range(16):
        starts = rng.normal(size=(37, 2)).astype(np.float32)
        ends = rng.normal(size=(37, 2)).astype(np.float32)
        ends[::7, 1] = starts[::7, 1]
        ex1 = starts[:, 0]
        ey1 = starts[:, 1]
        ex2 = ends[:, 0]
        ey2 = ends[:, 1]
        edx = ex2 - ex1
        edy = ey2 - ey1
        y_values = np.sort(
            rng.uniform(-2.0, 2.0, size=23).astype(np.float32)
        )
        expected = _scanline_endpoints_reference(
            ex1,
            ey1,
            ex2,
            ey2,
            edx,
            edy,
            y_values,
        )
        actual = _scanline_endpoints_njit(
            ex1,
            ey1,
            ey2,
            edx,
            edy,
            y_values,
        )
        np.testing.assert_array_equal(actual, expected)


def test_fill_scanline_kernel_matches_numpy_signed_zero_sort_order() -> None:
    tiny = np.asarray(0x00000001, dtype=np.uint32).view(np.float32)
    small = np.asarray(0x00800000, dtype=np.uint32).view(np.float32)
    ex1 = np.array([-1.0, -tiny, -small, -0.0], dtype=np.float32)
    ex2 = np.array([2.0, -small, small, -tiny], dtype=np.float32)
    ey1 = np.zeros((4,), dtype=np.float32)
    ey2 = np.ones((4,), dtype=np.float32)
    edx = ex2 - ex1
    edy = ey2 - ey1
    y_values = np.array([0.5], dtype=np.float32)

    expected = _scanline_endpoints_reference(
        ex1,
        ey1,
        ex2,
        ey2,
        edx,
        edy,
        y_values,
    )
    actual = _scanline_endpoints_njit(
        ex1,
        ey1,
        ey2,
        edx,
        edy,
        y_values,
    )

    np.testing.assert_array_equal(
        actual.view(np.uint32),
        expected.view(np.uint32),
    )


def test_fill_scanline_kernel_does_not_overwrite_for_nan_pair_width() -> None:
    ex1 = np.array([-np.inf, 0.0, 1.0, np.inf], dtype=np.float32)
    ex2 = ex1.copy()
    ey1 = np.zeros((4,), dtype=np.float32)
    ey2 = np.ones((4,), dtype=np.float32)
    with np.errstate(all="ignore"):
        edx = ex2 - ex1
    edy = ey2 - ey1
    y_values = np.array([0.5], dtype=np.float32)

    with np.errstate(all="ignore"):
        actual = _scanline_endpoints_njit(
            ex1,
            ey1,
            ey2,
            edx,
            edy,
            y_values,
        )

    assert actual.shape == (2, 2)
    np.testing.assert_array_equal(
        actual,
        np.array([[0.0, 0.5], [1.0, 0.5]], dtype=np.float32),
    )


def test_fill_scanline_overflow_preserves_numpy_exception() -> None:
    coords = np.array(
        [
            [0.0, 0.0],
            [1.0e38, 0.0],
            [0.0, 100.0],
            [0.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, 4], dtype=np.int32)

    with np.errstate(over="raise", invalid="ignore"), pytest.raises(
        FloatingPointError,
        match="overflow encountered in multiply",
    ):
        # 固定100契約のdensity=2に対応するS=50。work-Yは100なので2 levelsに留める。
        _generate_line_fill_evenodd_multi(
            coords,
            offsets,
            base_spacing=50.0,
            angle_rad=0.0,
            min_spacing=0.0,
            spacing_gradient=0.0,
        )


def test_fill_square_density_10_uses_fixed_100_unit_reference() -> None:
    g = G.fill_test_square()
    filled = E.fill(
        angle_sets=1,
        angle=0.0,
        density=10.0,
        min_spacing=0.0,
        remove_boundary=True,
    )(g)
    realized = realize(filled)

    assert realized.offsets.tolist() == [0, 2]
    np.testing.assert_allclose(
        realized.coords,
        np.asarray([[0.0, 5.0, 0.0], [10.0, 5.0, 0.0]], dtype=np.float32),
        rtol=0.0,
        atol=1e-6,
    )


@pytest.mark.parametrize(
    ("density", "expected_spacing"),
    [
        (0.0, 0.0),
        (0.1, 50.0),
        (2.49, 50.0),
        (2.51, 100.0 / 3.0),
        (25.0, 4.0),
        (35.0, 100.0 / 35.0),
        (1000.0, 0.1),
        (1000.6, 0.1),
    ],
)
def test_fill_density_to_spacing_uses_fixed_reference_and_legacy_quantization(
    density: float,
    expected_spacing: float,
) -> None:
    assert _spacing_from_density(density) == pytest.approx(expected_spacing)


def test_fill_density_25_produces_four_scene_unit_pitch() -> None:
    source = G.fill_test_rectangle(width=20.0, height=20.0)
    realized = realize(
        E.fill(
            angle_sets=1,
            angle=0.0,
            density=25.0,
            min_spacing=0.0,
            spacing_gradient=0.0,
            remove_boundary=True,
        )(source)
    )

    levels = _projected_family_levels(
        realized,
        angle_deg=0.0,
        reference_spacing=4.0,
    )
    np.testing.assert_allclose(
        levels,
        np.asarray([2.0, 6.0, 10.0, 14.0, 18.0]),
        rtol=0.0,
        atol=_level_tolerance(levels, 4.0),
    )
    _assert_uniform_level_spacing(levels, 4.0)


def test_fill_separate_one_and_four_x_calls_keep_same_pitch() -> None:
    angle_deg = 37.0
    spacing = 4.0
    sources = [
        G.fill_test_rectangle(width=20.0, height=20.0),
        G.fill_test_rectangle(
            width=80.0,
            height=80.0,
            origin=(120.0, -60.0, 0.0),
        ),
    ]

    level_counts: list[int] = []
    projected_spans: list[float] = []
    for source in sources:
        boundary = realize(source)
        realized = realize(
            E.fill(
                angle_sets=1,
                angle=angle_deg,
                density=25.0,
                min_spacing=0.0,
                spacing_gradient=0.0,
                remove_boundary=True,
            )(source)
        )
        levels = _projected_family_levels(
            realized,
            angle_deg=angle_deg,
            reference_spacing=spacing,
        )
        _assert_uniform_level_spacing(levels, spacing)
        span = _projected_normal_span(boundary.coords, angle_deg)
        assert abs(float(levels.size) - span / spacing) <= 1.0
        level_counts.append(int(levels.size))
        projected_spans.append(span)

    assert projected_spans[1] == pytest.approx(4.0 * projected_spans[0])
    assert abs(level_counts[1] - 4 * level_counts[0]) <= 1


def test_fill_remote_group_does_not_change_existing_group_pitch_or_phase() -> None:
    spacing = 4.0
    small = G.fill_test_rectangle(width=20.0, height=20.0)
    remote = G.fill_test_rectangle(
        width=80.0,
        height=80.0,
        origin=(200.0, 100.0, 0.0),
    )
    fill = E.fill(
        angle_sets=1,
        angle=0.0,
        density=25.0,
        min_spacing=0.0,
        spacing_gradient=0.0,
        remove_boundary=True,
    )
    separate = realize(fill(small))
    combined = realize(fill(small + remote))

    combined_segments = list(_iter_polylines(combined))
    small_segments = [
        segment
        for segment in combined_segments
        if float(np.mean(segment[:, 0])) < 100.0
    ]
    remote_segments = [
        segment
        for segment in combined_segments
        if float(np.mean(segment[:, 0])) > 100.0
    ]
    separate_levels = _projected_family_levels(
        separate,
        angle_deg=0.0,
        reference_spacing=spacing,
    )
    combined_small_levels = _projected_family_levels_from_polylines(
        small_segments,
        angle_deg=0.0,
        reference_spacing=spacing,
    )
    combined_remote_levels = _projected_family_levels_from_polylines(
        remote_segments,
        angle_deg=0.0,
        reference_spacing=spacing,
    )

    _assert_uniform_level_spacing(combined_small_levels, spacing)
    _assert_uniform_level_spacing(combined_remote_levels, spacing)
    np.testing.assert_allclose(
        combined_small_levels,
        separate_levels,
        rtol=0.0,
        atol=_level_tolerance(combined_small_levels, spacing),
    )


def test_fill_one_two_and_four_x_packed_groups_share_fixed_pitch() -> None:
    spacing = 4.0
    sources = [
        G.fill_test_rectangle(width=20.0, height=20.0),
        G.fill_test_rectangle(width=40.0, height=40.0, origin=(100.0, 0.0, 0.0)),
        G.fill_test_rectangle(width=80.0, height=80.0, origin=(240.0, 0.0, 0.0)),
    ]
    realized = realize(
        E.fill(
            angle_sets=1,
            angle=0.0,
            density=25.0,
            min_spacing=0.0,
            spacing_gradient=0.0,
            remove_boundary=True,
        )(sources[0] + sources[1] + sources[2])
    )
    segments = list(_iter_polylines(realized))
    groups = [
        [segment for segment in segments if float(np.mean(segment[:, 0])) < 50.0],
        [
            segment
            for segment in segments
            if 50.0 < float(np.mean(segment[:, 0])) < 200.0
        ],
        [segment for segment in segments if float(np.mean(segment[:, 0])) > 200.0],
    ]

    level_counts: list[int] = []
    for group in groups:
        levels = _projected_family_levels_from_polylines(
            group,
            angle_deg=0.0,
            reference_spacing=spacing,
        )
        _assert_uniform_level_spacing(levels, spacing)
        level_counts.append(int(levels.size))

    assert level_counts == [5, 10, 20]


def test_fill_span_smaller_than_pitch_keeps_single_midpoint_level() -> None:
    realized = realize(
        E.fill(
            angle_sets=1,
            angle=0.0,
            density=25.0,
            min_spacing=0.0,
            spacing_gradient=0.0,
            remove_boundary=True,
        )(G.fill_test_rectangle(width=2.0, height=2.0))
    )

    levels = _projected_family_levels(
        realized,
        angle_deg=0.0,
        reference_spacing=4.0,
    )
    np.testing.assert_allclose(levels, np.asarray([1.0]), rtol=0.0, atol=1e-6)


def test_fill_fixed_pitch_and_count_follow_projected_span_across_angles() -> None:
    spacing = 4.0
    source = G.fill_test_rectangle(width=64.0, height=16.0)
    boundary = realize(source)
    counts: dict[float, int] = {}

    for angle_deg in (0.0, 15.0, 30.0, 37.0, 45.0, 60.0, 75.0, 90.0):
        realized = realize(
            E.fill(
                angle_sets=1,
                angle=angle_deg,
                density=25.0,
                min_spacing=0.0,
                spacing_gradient=0.0,
                remove_boundary=True,
            )(source)
        )
        levels = _projected_family_levels(
            realized,
            angle_deg=angle_deg,
            reference_spacing=spacing,
        )
        _assert_uniform_level_spacing(levels, spacing)
        projected_span = _projected_normal_span(boundary.coords, angle_deg)
        assert abs(float(levels.size) - projected_span / spacing) <= 1.0
        counts[angle_deg] = int(levels.size)

    assert counts[90.0] >= 3 * counts[0.0]


@pytest.mark.parametrize("angle_sets", [2, 3])
def test_fill_each_angle_family_uses_fixed_pitch(angle_sets: int) -> None:
    spacing = 4.0
    base_angle = 13.0
    realized = realize(
        E.fill(
            angle_sets=angle_sets,
            angle=base_angle,
            density=25.0,
            min_spacing=0.0,
            spacing_gradient=0.0,
            remove_boundary=True,
        )(G.fill_test_rectangle(width=40.0, height=40.0))
    )

    for family_index in range(angle_sets):
        levels = _projected_family_levels(
            realized,
            angle_deg=base_angle + 180.0 * family_index / angle_sets,
            reference_spacing=spacing,
        )
        _assert_uniform_level_spacing(levels, spacing)


def test_fill_angle_count_tracks_projected_span_at_shared_measured_pitch() -> None:
    """angle補正を入れず、投影幅に応じて本数が変わる既存契約を守る。"""
    source = G.fill_test_rectangle(width=64.0, height=16.0)
    boundary = realize(source)
    measured_pitches: list[float] = []
    counts: dict[float, int] = {}

    for angle_deg in (0.0, 37.0, 90.0):
        realized = realize(
            E.fill(
                angle_sets=1,
                angle=angle_deg,
                density=25.0,
                min_spacing=0.0,
                spacing_gradient=0.0,
                remove_boundary=True,
            )(source)
        )
        levels = _projected_family_levels(
            realized,
            angle_deg=angle_deg,
            reference_spacing=1.0,
        )
        differences = np.diff(levels)
        assert differences.size > 0
        pitch = float(np.median(differences))
        np.testing.assert_allclose(
            differences,
            pitch,
            rtol=0.0,
            atol=_level_tolerance(levels, pitch),
        )
        projected_span = _projected_normal_span(boundary.coords, angle_deg)
        assert abs(float(levels.size) - projected_span / pitch) <= 1.0
        measured_pitches.append(pitch)
        counts[angle_deg] = int(levels.size)

    np.testing.assert_allclose(
        measured_pitches,
        measured_pitches[0],
        rtol=0.0,
        atol=1e-4,
    )
    assert counts[90.0] >= 3 * counts[0.0]


def test_fill_min_spacing_reduces_high_density_square_and_bounds_pitch() -> None:
    source = G.fill_test_square()
    legacy = realize(
        E.fill(
            angle_sets=1,
            angle=0.0,
            density=1000.0,
            remove_boundary=True,
        )(source)
    )
    min_spacing = 0.75
    floored = realize(
        E.fill(
            angle_sets=1,
            angle=0.0,
            density=1000.0,
            min_spacing=min_spacing,
            remove_boundary=True,
        )(source)
    )

    assert len(floored.offsets) < len(legacy.offsets)
    levels = _projected_family_levels(
        floored,
        angle_deg=0.0,
        reference_spacing=min_spacing,
    )
    _assert_minimum_level_spacing(levels, min_spacing)


@pytest.mark.parametrize("spacing_gradient", [-4.0, 4.0])
def test_fill_min_spacing_bounds_rotated_gradient_family(
    spacing_gradient: float,
) -> None:
    min_spacing = 0.6
    realized = realize(
        E.fill(
            angle_sets=1,
            angle=37.0,
            density=1000.0,
            min_spacing=min_spacing,
            spacing_gradient=spacing_gradient,
            remove_boundary=True,
        )(G.fill_test_square())
    )

    levels = _projected_family_levels(
        realized,
        angle_deg=37.0,
        reference_spacing=min_spacing,
    )
    _assert_minimum_level_spacing(levels, min_spacing)


@pytest.mark.parametrize(
    ("source_name", "fill_kwargs"),
    [
        (
            "square",
            {
                "angle_sets": 1,
                "angle": 37.0,
                "density": 17.0,
                "spacing_gradient": 0.0,
                "remove_boundary": True,
            },
        ),
        (
            "hole",
            {
                "angle_sets": 1,
                "angle": 0.0,
                "density": 19.0,
                "spacing_gradient": 2.5,
                "remove_boundary": True,
            },
        ),
        (
            "tilted",
            {
                "angle_sets": 2,
                "angle": 13.0,
                "density": 11.0,
                "spacing_gradient": -2.0,
                "remove_boundary": True,
            },
        ),
    ],
)
def test_fill_omitted_and_explicit_default_min_spacing_are_array_exact(
    source_name: str,
    fill_kwargs: dict[str, int | float | bool],
) -> None:
    sources = {
        "square": G.fill_test_square,
        "hole": G.fill_test_square_with_hole,
        "tilted": G.fill_test_tilted_collinear_start,
    }
    source = sources[source_name]()
    omitted = realize(E.fill(**fill_kwargs)(source))
    explicit = realize(E.fill(min_spacing=0.05, **fill_kwargs)(source))

    np.testing.assert_array_equal(explicit.coords, omitted.coords)
    np.testing.assert_array_equal(explicit.offsets, omitted.offsets)


def test_fill_default_min_spacing_is_observable_for_dense_gradient() -> None:
    source = G.fill_test_square()
    fill_kwargs = {
        "angle_sets": 1,
        "angle": 37.0,
        "density": 1000.0,
        "spacing_gradient": 4.0,
        "remove_boundary": True,
    }

    omitted = realize(E.fill(**fill_kwargs)(source))
    explicit_default = realize(E.fill(min_spacing=0.05, **fill_kwargs)(source))
    disabled_floor = realize(E.fill(min_spacing=0.0, **fill_kwargs)(source))

    np.testing.assert_array_equal(explicit_default.coords, omitted.coords)
    np.testing.assert_array_equal(explicit_default.offsets, omitted.offsets)
    assert not np.array_equal(disabled_floor.coords, omitted.coords)
    assert not np.array_equal(disabled_floor.offsets, omitted.offsets)


def test_fill_inactive_positive_min_spacing_preserves_public_output() -> None:
    source = G.fill_test_square()
    legacy = realize(
        E.fill(
            angle_sets=1,
            angle=0.0,
            density=10.0,
            remove_boundary=True,
        )(source)
    )
    inactive_floor = realize(
        E.fill(
            angle_sets=1,
            angle=0.0,
            density=10.0,
            min_spacing=0.2,
            remove_boundary=True,
        )(source)
    )

    np.testing.assert_array_equal(inactive_floor.coords, legacy.coords)
    np.testing.assert_array_equal(inactive_floor.offsets, legacy.offsets)


def test_fill_positive_min_spacing_is_deterministic_across_sessions() -> None:
    def make_geometry():
        return E.fill(
            angle_sets=1,
            angle=37.0,
            density=1000.0,
            min_spacing=0.6,
            spacing_gradient=4.0,
            remove_boundary=True,
        )(G.fill_test_square())

    with RealizeSession() as first_session:
        first = first_session.realize(make_geometry())
    with RealizeSession() as second_session:
        second = second_session.realize(make_geometry())

    np.testing.assert_array_equal(second.coords, first.coords)
    np.testing.assert_array_equal(second.offsets, first.offsets)


def test_fill_min_spacing_is_checked_per_angle_family() -> None:
    min_spacing = 0.75
    base_angle = 17.0
    realized = realize(
        E.fill(
            angle_sets=2,
            angle=base_angle,
            density=1000.0,
            min_spacing=min_spacing,
            remove_boundary=True,
        )(G.fill_test_square())
    )

    for angle_deg in (base_angle, base_angle + 90.0):
        levels = _projected_family_levels(
            realized,
            angle_deg=angle_deg,
            reference_spacing=min_spacing,
        )
        _assert_minimum_level_spacing(levels, min_spacing)


def test_fill_uses_all_points_for_tilted_ring_with_collinear_start() -> None:
    filled = E.fill(angle_sets=1, angle=0.0, density=8.0, remove_boundary=True)(
        G.fill_test_tilted_collinear_start()
    )
    realized = realize(filled)

    assert realized.offsets.size > 1
    np.testing.assert_allclose(
        realized.coords[:, 2],
        realized.coords[:, 0] + realized.coords[:, 1],
        rtol=0.0,
        atol=2e-5,
    )


def _fill_two_nonplanar_face_levels(
    *,
    density: float,
    min_spacing: float,
    reference_spacing: float,
) -> list[np.ndarray]:
    """nonplanar local pathを通し、faceごとのdistinct levelを返す。"""
    source = realize(G.fill_test_two_planar_faces())
    global_frame = PlanarFrame.from_points(source.coords, source.offsets)
    assert global_frame.valid
    assert not global_frame.is_planar(planarity_threshold(source.coords))

    face_frames: list[PlanarFrame] = []
    for index in range(2):
        start = int(source.offsets[index])
        stop = int(source.offsets[index + 1])
        face = source.coords[start:stop]
        frame = PlanarFrame.from_points(face)
        assert frame.valid
        assert frame.is_planar(planarity_threshold(face))
        face_frames.append(frame)

    filled = realize(
        E.fill(
            angle_sets=1,
            angle=0.0,
            density=density,
            min_spacing=min_spacing,
            spacing_gradient=0.0,
            remove_boundary=True,
        )(G.fill_test_two_planar_faces())
    )
    segments_by_face: list[list[np.ndarray]] = [[], []]
    for segment in _iter_polylines(filled):
        midpoint_x = float(np.mean(segment[:, 0]))
        face_index = 0 if midpoint_x < 20.0 else 1
        segments_by_face[face_index].append(segment)

    levels_by_face: list[np.ndarray] = []
    for frame, segments in zip(face_frames, segments_by_face, strict=True):
        assert len(segments) >= 2
        local = frame.to_local(np.concatenate(segments, axis=0))
        np.testing.assert_allclose(local[:, 2], 0.0, rtol=0.0, atol=2e-5)
        local_segments = local.reshape(-1, 2, 3)
        raw_levels = np.mean(local_segments[:, :, 1], axis=1)
        tolerance = _level_tolerance(raw_levels, reference_spacing)
        levels = _distinct_levels(raw_levels, tolerance=tolerance)
        levels_by_face.append(levels)
    return levels_by_face


def test_fill_min_spacing_applies_in_each_nonplanar_local_frame() -> None:
    min_spacing = 0.75
    levels_by_face = _fill_two_nonplanar_face_levels(
        density=1000.0,
        min_spacing=min_spacing,
        reference_spacing=min_spacing,
    )

    for levels in levels_by_face:
        _assert_minimum_level_spacing(levels, min_spacing)


def test_fill_fixed_density_pitch_is_shared_by_different_size_nonplanar_faces() -> None:
    spacing = 4.0
    levels_by_face = _fill_two_nonplanar_face_levels(
        density=25.0,
        min_spacing=0.0,
        reference_spacing=spacing,
    )

    assert [int(levels.size) for levels in levels_by_face] == [2, 10]
    for levels in levels_by_face:
        _assert_uniform_level_spacing(levels, spacing)


def test_fill_packed_hatch_offsets_use_two_vertex_stride() -> None:
    frame = PlanarFrame.from_points(
        np.asarray([[0, 0, 0], [4, 0, 0], [4, 4, 0], [0, 4, 0], [0, 0, 0]])
    )
    boundary = frame.to_local(
        np.asarray([[0, 0, 0], [4, 0, 0], [4, 4, 0], [0, 4, 0], [0, 0, 0]])
    )
    hatch = np.asarray([[0, 1], [4, 1], [0, 2], [4, 2], [0, 3], [4, 3]], dtype=np.float32)

    coords, offsets = _pack_planar_fill_chunks([boundary, hatch], frame)

    assert coords.dtype == np.float32
    assert offsets.tolist() == [0, 5, 7, 9, 11]
    np.testing.assert_array_equal(np.diff(offsets[1:]), np.full((3,), 2, dtype=np.int32))


def test_fill_remove_boundary_false_keeps_input() -> None:
    g = G.fill_test_square()
    filled = E.fill(
        angle_sets=1,
        angle=0.0,
        density=10.0,
        min_spacing=0.0,
        remove_boundary=False,
    )(g)
    realized = realize(filled)

    assert len(realized.offsets) - 1 == 2
    first = next(_iter_polylines(realized))
    np.testing.assert_allclose(first, realize(g).coords, rtol=0.0, atol=1e-6)


def test_fill_min_spacing_keeps_boundary_as_array_exact_prefix() -> None:
    source = G.fill_test_square()
    boundary = realize(source)
    filled = realize(
        E.fill(
            angle_sets=1,
            angle=29.0,
            density=1000.0,
            min_spacing=0.75,
            remove_boundary=False,
        )(source)
    )

    vertex_count = int(boundary.coords.shape[0])
    assert filled.offsets[:2].tolist() == [0, vertex_count]
    np.testing.assert_array_equal(filled.coords[:vertex_count], boundary.coords)


def test_fill_density_zero_still_generates_no_hatch_with_positive_floor() -> None:
    filled = realize(
        E.fill(
            angle_sets=2,
            angle=0.0,
            density=0.0,
            min_spacing=0.75,
            remove_boundary=True,
        )(G.fill_test_square())
    )

    assert filled.coords.shape == (0, 3)
    assert filled.offsets.tolist() == [0]


def test_fill_outer_with_hole_avoids_hole_region() -> None:
    g = G.fill_test_square_with_hole()
    filled = E.fill(
        angle_sets=1,
        angle=0.0,
        density=10.0,
        min_spacing=0.0,
        remove_boundary=True,
    )(g)
    realized = realize(filled)

    # 固定100契約ではdensity=10のpitchは10。y=5の1 levelが穴で2分割される。
    assert len(realized.offsets) - 1 == 2

    for seg in _iter_polylines(realized):
        mid = seg.mean(axis=0)
        assert not (3.0 < float(mid[0]) < 7.0 and 3.0 < float(mid[1]) < 7.0)


def test_fill_hole_split_levels_keep_fixed_nominal_pitch() -> None:
    spacing = 4.0
    realized = realize(
        E.fill(
            angle_sets=1,
            angle=0.0,
            density=25.0,
            min_spacing=0.0,
            spacing_gradient=0.0,
            remove_boundary=True,
        )(G.fill_test_square_with_hole())
    )
    segments = list(_iter_polylines(realized))
    levels = _projected_family_levels_from_polylines(
        segments,
        angle_deg=0.0,
        reference_spacing=spacing,
    )

    assert len(segments) > int(levels.size), "holeで同一levelが分割される必要がある"
    _assert_uniform_level_spacing(levels, spacing)


def test_fill_concave_split_levels_keep_fixed_nominal_pitch() -> None:
    spacing = 4.0
    realized = realize(
        E.fill(
            angle_sets=1,
            angle=0.0,
            density=25.0,
            min_spacing=0.0,
            spacing_gradient=0.0,
            remove_boundary=True,
        )(G.fill_test_concave_u())
    )
    segments = list(_iter_polylines(realized))
    levels = _projected_family_levels_from_polylines(
        segments,
        angle_deg=0.0,
        reference_spacing=spacing,
    )

    assert len(segments) > int(levels.size), "concavityで同一levelが分割される必要がある"
    _assert_uniform_level_spacing(levels, spacing)


def test_fill_min_spacing_deduplicates_hole_split_levels_before_pitch_check() -> None:
    min_spacing = 0.75
    realized = realize(
        E.fill(
            angle_sets=1,
            angle=0.0,
            density=1000.0,
            min_spacing=min_spacing,
            remove_boundary=True,
        )(G.fill_test_square_with_hole())
    )
    segments = list(_iter_polylines(realized))
    raw_levels = np.asarray(
        [float(np.mean(segment[:, 1])) for segment in segments],
        dtype=np.float64,
    )
    tolerance = _level_tolerance(raw_levels, min_spacing)
    ordered = np.sort(raw_levels)
    level_groups: list[list[float]] = []
    for level in ordered:
        if not level_groups or float(level) - level_groups[-1][-1] > tolerance:
            level_groups.append([float(level)])
        else:
            level_groups[-1].append(float(level))

    assert any(
        len(group) >= 2 and 3.0 < float(np.mean(group)) < 7.0
        for group in level_groups
    ), "hole 帯で同じ scanline が 2 segment に分割される必要がある"
    distinct = np.asarray([float(np.mean(group)) for group in level_groups])
    _assert_minimum_level_spacing(distinct, min_spacing)

    for segment in segments:
        midpoint = np.mean(segment, axis=0)
        assert not (
            3.0 < float(midpoint[0]) < 7.0
            and 3.0 < float(midpoint[1]) < 7.0
        )


def test_fill_min_spacing_is_checked_within_each_outer_group() -> None:
    min_spacing = 0.75
    realized = realize(
        E.fill(
            angle_sets=1,
            angle=0.0,
            density=1000.0,
            min_spacing=min_spacing,
            remove_boundary=True,
        )(G.fill_test_two_squares())
    )
    levels_by_group: list[list[float]] = [[], []]
    for segment in _iter_polylines(realized):
        midpoint = np.mean(segment.astype(np.float64), axis=0)
        if float(midpoint[0]) < 15.0:
            levels_by_group[0].append(float(midpoint[1]))
        else:
            assert float(midpoint[0]) > 15.0
            levels_by_group[1].append(float(midpoint[1]))

    for raw_levels in levels_by_group:
        raw = np.asarray(raw_levels, dtype=np.float64)
        assert raw.size > 0
        tolerance = _level_tolerance(raw, min_spacing)
        levels = _distinct_levels(raw, tolerance=tolerance)
        _assert_minimum_level_spacing(levels, min_spacing)


def test_fill_evenodd_grouping_groups_square_with_hole() -> None:
    g = G.fill_test_square_with_hole()
    base = realize(g)
    coords2d = base.coords[:, :2].astype(np.float32, copy=False)
    assert _build_evenodd_groups(coords2d, base.offsets) == [[0, 1]]


def test_fill_evenodd_grouping_does_not_treat_touching_polygons_as_hole() -> None:
    # 隣接セル（共有辺/頂点）の代表点が「境界上」に載るケースを想定し、
    # グルーピングが誤って hole 扱いしないことを担保する。
    outer = np.array(
        [
            [0.0, 0.0],
            [2.0, 0.0],
            [2.0, 2.0],
            [0.0, 2.0],
            [0.0, 0.0],
        ],
        dtype=np.float32,
    )
    touching = np.array(
        [
            [0.0, 0.5],
            [-1.0, 0.5],
            [-1.0, 1.5],
            [0.0, 1.5],
            [0.0, 0.5],
        ],
        dtype=np.float32,
    )
    coords2d = np.concatenate([outer, touching], axis=0)
    offsets = np.array([0, outer.shape[0], outer.shape[0] + touching.shape[0]], dtype=np.int32)

    assert _build_evenodd_groups(coords2d, offsets) == [[0], [1]]


def test_point_in_polygon_treats_boundary_as_outside() -> None:
    poly = np.array(
        [
            [0.0, 0.0],
            [10.0, 0.0],
            [10.0, 10.0],
            [0.0, 10.0],
            [0.0, 0.0],
        ],
        dtype=np.float32,
    )

    assert _point_inside(np.array([5.0, 5.0], dtype=np.float32), poly)
    assert not _point_inside(np.array([0.0, 5.0], dtype=np.float32), poly)
    assert not _point_inside(np.array([0.0, 0.0], dtype=np.float32), poly)
    assert not _point_inside(np.array([15.0, 5.0], dtype=np.float32), poly)


def test_fill_text_o_respects_hole() -> None:
    g = G.text(text="o", font="GoogleSans-Regular.ttf", scale=100.0)
    boundary = realize(g)
    coords2d = boundary.coords[:, :2].astype(np.float32, copy=False)
    groups = _build_evenodd_groups(coords2d, boundary.offsets)

    hole_poly: np.ndarray | None = None
    for group in groups:
        if len(group) < 2:
            continue
        areas: list[tuple[float, int]] = []
        for ring_i in group:
            s = int(boundary.offsets[ring_i])
            e = int(boundary.offsets[ring_i + 1])
            areas.append((_polygon_area_abs(coords2d[s:e]), int(ring_i)))
        areas.sort(key=lambda t: t[0])
        hole_i = areas[0][1]
        s = int(boundary.offsets[hole_i])
        e = int(boundary.offsets[hole_i + 1])
        hole_poly = coords2d[s:e]
        break

    assert hole_poly is not None
    x0 = float(np.min(hole_poly[:, 0]))
    x1 = float(np.max(hole_poly[:, 0]))
    y0 = float(np.min(hole_poly[:, 1]))
    y1 = float(np.max(hole_poly[:, 1]))
    probe = np.array([(x0 + x1) * 0.5, (y0 + y1) * 0.5], dtype=np.float32)
    assert _point_inside(probe, hole_poly)

    filled = realize(E.fill(angle_sets=1, angle=0.0, density=25.0, remove_boundary=True)(g))
    assert filled.coords.shape[0] > 0
    for seg in _iter_polylines(filled):
        if seg.shape != (2, 3):
            continue
        mid = seg.mean(axis=0)
        assert not _point_inside(mid[:2].astype(np.float32, copy=False), hole_poly)


def test_fill_empty_geometry_is_noop() -> None:
    g = G.fill_test_empty()
    filled = E.fill(angle_sets=2, angle=0.0, density=10.0, remove_boundary=True)(g)
    realized = realize(filled)

    assert realized.coords.shape == (0, 3)
    assert realized.offsets.tolist() == [0]


@pytest.mark.parametrize(
    ("kwargs", "parameter"),
    [
        ({"angle_sets": 0}, "angle_sets"),
        ({"density": -0.1}, "density"),
        ({"min_spacing": -0.1}, "min_spacing"),
        ({"spacing_gradient": -4.1}, "spacing_gradient"),
        ({"spacing_gradient": 4.1}, "spacing_gradient"),
    ],
)
def test_fill_rejects_invalid_parameters_before_empty_input(
    kwargs: dict[str, int | float],
    parameter: str,
) -> None:
    with pytest.raises(RealizeError) as exc_info:
        realize(E.fill(**kwargs)(G.fill_test_empty()))

    assert isinstance(exc_info.value.__cause__, ValueError)
    assert parameter in str(exc_info.value.__cause__)


@pytest.mark.parametrize(
    "invalid",
    [-0.1, float("nan"), float("inf"), -float("inf")],
)
def test_fill_direct_evaluator_rejects_invalid_density_before_empty_input(
    invalid: float,
) -> None:
    empty = (
        np.empty((0, 3), dtype=np.float32),
        np.zeros((1,), dtype=np.int32),
    )

    with pytest.raises(ValueError, match="density"):
        fill_effect(empty, density=invalid, min_spacing=0.0)


@pytest.mark.parametrize("invalid", [-0.1, float("nan"), float("inf"), -float("inf")])
def test_fill_direct_evaluator_rejects_invalid_min_spacing_before_empty_input(
    invalid: float,
) -> None:
    empty = (
        np.empty((0, 3), dtype=np.float32),
        np.zeros((1,), dtype=np.int32),
    )

    with pytest.raises(ValueError, match="min_spacing"):
        fill_effect(empty, min_spacing=invalid)


@pytest.mark.parametrize(
    "invalid",
    [True, "0.5", [0.5], (0.5,), float("nan"), float("inf"), -float("inf")],
)
def test_fill_public_schema_rejects_invalid_min_spacing(invalid: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        E.fill(min_spacing=invalid)


def test_fill_public_schema_canonicalizes_integer_min_spacing_to_float() -> None:
    step = E.fill(min_spacing=2).steps[0]
    value = dict(step.args)["min_spacing"]

    assert value == 2.0
    assert type(value) is float


def test_fill_degenerate_input_is_noop() -> None:
    g = E.scale(scale=(50.0, 0.0, 1.0))(G.polygon(scale=1.0))
    base = realize(g)

    # 退化入力（面積ほぼ 0）は fill を適用してもそのまま返す（remove_boundary も無視する）。
    filled = E.fill(angle_sets=1, angle=45.0, density=10.0, remove_boundary=True)(g)
    realized = realize(filled)

    np.testing.assert_allclose(realized.coords, base.coords, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == base.offsets.tolist()


def _hatch_direction(realized: RealizedGeometry) -> np.ndarray:
    dirs: list[np.ndarray] = []
    for seg in _iter_polylines(realized):
        if seg.shape[0] < 2:
            continue
        d = seg[-1] - seg[0]
        n = float(np.linalg.norm(d))
        if n <= 1e-9:
            continue
        d = d / n
        idx = int(np.argmax(np.abs(d)))
        if float(d[idx]) < 0.0:
            d = -d
        dirs.append(d.astype(np.float64, copy=False))
    if not dirs:
        raise AssertionError("塗り線が生成されていない")
    mean = np.mean(np.stack(dirs, axis=0), axis=0)
    mean_n = float(np.linalg.norm(mean))
    if mean_n <= 0.0:
        raise AssertionError("塗り線方向の計算に失敗した")
    return (mean / mean_n).astype(np.float64, copy=False)


def test_fill_hatch_direction_is_stable_under_rotation() -> None:
    g = G.fill_test_square()

    prev_dir: np.ndarray | None = None
    for deg in np.linspace(0.0, 60.0, 31):
        rot = (float(deg), float(deg), float(deg))
        filled = (
            E.affine(rotation=rot)
            .fill(angle_sets=1, angle=45.0, density=10.0, remove_boundary=True)(g)
        )
        realized = realize(filled)
        d = _hatch_direction(realized)
        if prev_dir is not None:
            dot = float(abs(np.dot(prev_dir, d)))
            assert dot > 0.5
        prev_dir = d


def test_fill_hatch_attaches_under_z_rotation() -> None:
    g = G.fill_test_square()

    base = realize(E.fill(angle_sets=1, angle=45.0, density=10.0, remove_boundary=True)(g))
    base_dir = _hatch_direction(base)

    for deg in [0.0, 15.0, 30.0, 60.0, 120.0]:
        filled = (
            E.affine(rotation=(0.0, 0.0, float(deg)))
            .fill(angle_sets=1, angle=45.0, density=10.0, remove_boundary=True)(g)
        )
        realized = realize(filled)
        d = _hatch_direction(realized)

        th = np.deg2rad(float(deg))
        c = float(np.cos(-th))
        s = float(np.sin(-th))
        rz = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)
        d_local = rz @ d
        assert float(abs(np.dot(d_local, base_dir))) > 0.99
