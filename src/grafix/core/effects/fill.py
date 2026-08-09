"""
ハッチ塗りつぶし effect。

閉領域（外周＋穴）に対して偶奇規則で内部を判定し、指定角度のハッチ線分を生成する。
3D 入力は一度 XY 平面へ整列して 2D で処理し、生成した線分を元の姿勢へ戻す。
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from numba import njit  # type: ignore[attr-defined]

from grafix.core.geometry_kernels.packed import (
    empty_packed_geometry,
    pack_polylines,
)
from grafix.core.geometry_kernels.planar import (
    PlanarFrame,
    planarity_threshold,
)
from grafix.core.operation_authoring import effect
from grafix.core.operation_diagnostics import emit_operation_diagnostic
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import GeomTuple

# 生成する塗り線の最大本数（密度の上限）。
MAX_FILL_LINES = 1000
_SCANLINE_SAFE_ABS_MIN = np.float32(2.0**-40)

fill_meta = {
    "angle_sets": ParamMeta(
        kind="int",
        ui_min=1,
        ui_max=6,
        description="180 度を等分して重ねるハッチング方向の数。",
    ),
    "angle": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=180.0,
        description="ハッチング方向群の基準角を度単位で指定する。",
    ),
    "density": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=float(MAX_FILL_LINES),
        description="領域を埋めるハッチング線の密度を指定する。",
    ),
    "min_spacing": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=10.0,
        description=(
            "同一方向の隣接ハッチ走査線に適用する、fill 評価時の作業平面の "
            "scene 座標単位での最小ピッチ。標準 2D plot では通常 mm に対応し、0 で無効。"
        ),
    ),
    "spacing_gradient": ParamMeta(
        kind="float",
        ui_min=-4.0,
        ui_max=4.0,
        description="スキャン方向に沿ってハッチング線の間隔を変化させる。",
    ),
    "remove_boundary": ParamMeta(
        kind="bool",
        description="塗り線だけを残し、入力された境界線を出力から除く。",
    ),
}


def _emit_fill_fallback(original: str, *, reason: str) -> None:
    """閉領域を生成できない入力のboundary-only fallbackを通知する。"""

    emit_operation_diagnostic(
        op="fill.input",
        original_value=original,
        effective_value="boundary_only",
        reason=reason,
    )


def _polygon_area_abs(vertices: np.ndarray) -> float:
    """2D ポリゴンの面積絶対値を返す（閉じは仮定しない）。"""
    # Shoelace formula（頂点列が「閉じている/いない」どちらでも動く）。
    # fill の用途では、向き（符号）ではなく面積スケール比較に使うため絶対値。
    if vertices.shape[0] < 3:
        return 0.0
    x = vertices[:, 0].astype(np.float64, copy=False)
    y = vertices[:, 1].astype(np.float64, copy=False)
    return float(0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _is_degenerate_fill_input(coords_2d_all: np.ndarray, offsets: np.ndarray) -> bool:
    """fill の入力が「面」を持たない退化形状かを判定する。"""
    # fill は閉領域の内部をスキャンして線分化するため、面積がほぼ 0 の入力では意味がない。
    # そのまま処理すると spacing が極小になり、巨大なスキャンライン配列を確保して落ちることがある。
    ring_indices = [
        i
        for i in range(int(offsets.size) - 1)
        if int(offsets[i + 1]) - int(offsets[i]) >= 3
    ]
    if not ring_indices:
        return True

    # 面積スケールは bbox 対角長^2 に比例するので、相対閾値で「ほぼ 0」を判定する。
    rel = 1e-12
    for i in ring_indices:
        s = int(offsets[i])
        e = int(offsets[i + 1])
        poly = coords_2d_all[s:e]
        if poly.shape[0] < 3:
            continue

        p64 = poly.astype(np.float64, copy=False)
        mins = np.min(p64, axis=0)
        maxs = np.max(p64, axis=0)
        dx = float(maxs[0] - mins[0])
        dy = float(maxs[1] - mins[1])
        diag_sq = dx * dx + dy * dy
        if not np.isfinite(diag_sq) or diag_sq <= 0.0:
            continue

        area = _polygon_area_abs(poly)
        if area > diag_sq * rel:
            return False

    return True


@njit(cache=True)  # type: ignore[misc]
def _polygon_area_abs_coords_njit(
    coords_2d_all: np.ndarray, start: int, end: int
) -> float:
    """2D ポリゴンの面積絶対値を返す（閉じは仮定しない、Numba 版）。"""
    n = int(end - start)
    if n < 3:
        return 0.0
    area2 = 0.0
    x1 = float(coords_2d_all[end - 1, 0])
    y1 = float(coords_2d_all[end - 1, 1])
    for i in range(start, end):
        x2 = float(coords_2d_all[i, 0])
        y2 = float(coords_2d_all[i, 1])
        area2 += x1 * y2 - x2 * y1
        x1, y1 = x2, y2
    if area2 < 0.0:
        area2 = -area2
    return 0.5 * area2


@njit(cache=True)  # type: ignore[misc]
def _point_in_polygon_coords_njit(
    coords_2d_all: np.ndarray,
    start: int,
    end: int,
    x: float,
    y: float,
) -> bool:
    """点が多角形内部にあるかを返す（境界上は False 扱い、Numba 版）。"""
    n = int(end - start)
    if n < 3:
        return False

    # 境界上を明示的に除外してから、偶奇レイキャストで内部判定する。
    eps = 1e-6

    x1 = float(coords_2d_all[end - 1, 0])
    y1 = float(coords_2d_all[end - 1, 1])
    for k in range(n):
        i = int(start + k)
        x2 = float(coords_2d_all[i, 0])
        y2 = float(coords_2d_all[i, 1])

        if abs(x - x2) <= eps and abs(y - y2) <= eps:
            return False

        dx = x2 - x1
        dy = y2 - y1

        min_x = x1 if x1 < x2 else x2
        max_x = x2 if x1 < x2 else x1
        min_y = y1 if y1 < y2 else y2
        max_y = y2 if y1 < y2 else y1
        if (
            x >= min_x - eps
            and x <= max_x + eps
            and y >= min_y - eps
            and y <= max_y + eps
        ):
            cross = dx * (y - y1) - dy * (x - x1)
            tol = eps * (abs(dx) + abs(dy))
            if tol < eps:
                tol = eps
            if abs(cross) <= tol:
                dot = (x - x1) * (x - x2) + (y - y1) * (y - y2)
                if dot <= eps * eps:
                    return False

        x1, y1 = x2, y2

    inside = False
    x1 = float(coords_2d_all[end - 1, 0])
    y1 = float(coords_2d_all[end - 1, 1])
    for k in range(n):
        i = int(start + k)
        x2 = float(coords_2d_all[i, 0])
        y2 = float(coords_2d_all[i, 1])
        if (y1 > y) != (y2 > y):
            x_int = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < x_int:
                inside = not inside
        x1, y1 = x2, y2
    return inside


@njit(cache=True)  # type: ignore[misc]
def _evenodd_parent_outer_njit(
    coords_2d_all: np.ndarray,
    ring_start: np.ndarray,
    ring_end: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """even-odd の outer 判定と、hole の親 outer を返す（Numba 版）。"""
    r = int(ring_start.shape[0])
    area_abs = np.empty((r,), dtype=np.float64)
    contains_count = np.zeros((r,), dtype=np.int32)
    parent_min = np.full((r,), -1, dtype=np.int32)
    parent_area = np.full((r,), 1e308, dtype=np.float64)

    min_x = np.empty((r,), dtype=np.float32)
    max_x = np.empty((r,), dtype=np.float32)
    min_y = np.empty((r,), dtype=np.float32)
    max_y = np.empty((r,), dtype=np.float32)

    for i in range(r):
        s = int(ring_start[i])
        e = int(ring_end[i])
        area_abs[i] = _polygon_area_abs_coords_njit(coords_2d_all, s, e)

        bx0 = float(coords_2d_all[s, 0])
        bx1 = bx0
        by0 = float(coords_2d_all[s, 1])
        by1 = by0
        for k in range(s + 1, e):
            xk = float(coords_2d_all[k, 0])
            yk = float(coords_2d_all[k, 1])
            if xk < bx0:
                bx0 = xk
            elif xk > bx1:
                bx1 = xk
            if yk < by0:
                by0 = yk
            elif yk > by1:
                by1 = yk
        min_x[i] = np.float32(bx0)
        max_x[i] = np.float32(bx1)
        min_y[i] = np.float32(by0)
        max_y[i] = np.float32(by1)

    eps = 1e-6
    for i in range(r):
        s_i = int(ring_start[i])
        x = float(coords_2d_all[s_i, 0])
        y = float(coords_2d_all[s_i, 1])
        for j in range(r):
            if i == j:
                continue
            if (
                x < float(min_x[j]) - eps
                or x > float(max_x[j]) + eps
                or y < float(min_y[j]) - eps
                or y > float(max_y[j]) + eps
            ):
                continue
            if _point_in_polygon_coords_njit(
                coords_2d_all,
                int(ring_start[j]),
                int(ring_end[j]),
                x,
                y,
            ):
                contains_count[i] += 1
                a = float(area_abs[j])
                if a < float(parent_area[i]):
                    parent_area[i] = a
                    parent_min[i] = int(j)

    is_outer = np.zeros((r,), dtype=np.uint8)
    for i in range(r):
        if contains_count[i] % 2 == 0:
            is_outer[i] = 1

    parent_outer = np.full((r,), -1, dtype=np.int32)
    for i in range(r):
        if is_outer[i] == 1:
            continue
        p = int(parent_min[i])
        hops = 0
        while p != -1 and is_outer[p] == 0 and hops < r:
            p = int(parent_min[p])
            hops += 1
        if hops >= r:
            p = -1
        parent_outer[i] = np.int32(p)

    return is_outer, parent_outer


def _build_evenodd_groups(
    coords_2d_all: np.ndarray, offsets: np.ndarray
) -> list[list[int]]:
    """外周＋穴を even-odd でグルーピングし、[outer, hole...] のリストを返す。"""
    # 目的:
    # - 入力が「外周 + 穴 + 穴の穴 + ...」の入れ子になっていても、
    #   偶奇規則（even-odd）で「外環ごとに穴をぶら下げた集合」を作る。
    #
    # 実装方針:
    # 1) 各リングの代表点（第1頂点）を取り、他リングへの内包関係を判定する。
    # 2) 「内包している外側リングの個数」の偶奇で outer/hole を決める。
    # 3) hole は、それを含む outer のうち「面積が最小のもの」にぶら下げる。
    # 4) outer が見つからない hole は単独グループに落とし、リングが脱落しないようにする。
    ring_indices: list[int] = []
    ring_start: list[int] = []
    ring_end: list[int] = []
    for i in range(int(offsets.size) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        if e - s >= 3:
            ring_indices.append(int(i))
            ring_start.append(s)
            ring_end.append(e)
    if not ring_indices:
        return []

    ring_ids = np.asarray(ring_indices, dtype=np.int32)
    ring_start_arr = np.asarray(ring_start, dtype=np.int32)
    ring_end_arr = np.asarray(ring_end, dtype=np.int32)

    coords2d = np.asarray(coords_2d_all, dtype=np.float32)
    is_outer_u8, parent_outer = _evenodd_parent_outer_njit(
        coords2d,
        ring_start_arr,
        ring_end_arr,
    )

    outer_ring_list = [i for i in range(int(ring_ids.shape[0])) if bool(is_outer_u8[i])]
    groups: dict[int, list[int]] = {oi: [oi] for oi in outer_ring_list}
    orphan_keys: list[int] = []

    for i in range(int(ring_ids.shape[0])):
        if bool(is_outer_u8[i]):
            continue
        p = int(parent_outer[i])
        if p >= 0 and p != i:
            groups.setdefault(p, []).append(i)
        else:
            # 数値誤差や入力の歪みで outer が見つからない場合でも、ここで脱落させない。
            groups.setdefault(i, []).append(i)
            orphan_keys.append(i)

    # 出力順は安定化する: outer は入力順、各グループ内の ring index も昇順。
    ordered: list[list[int]] = []
    for oi in outer_ring_list:
        members = groups.get(oi, [oi])
        members_sorted = sorted(members, key=lambda idx: int(ring_ids[idx]))
        ordered.append([int(ring_ids[idx]) for idx in members_sorted])
    for key in orphan_keys:
        members = groups.get(key, [key])
        members_sorted = sorted(members, key=lambda idx: int(ring_ids[idx]))
        ordered.append([int(ring_ids[idx]) for idx in members_sorted])
    return ordered


def _spacing_from_height(height: float, density: float) -> float:
    """高さと密度から線間隔を算出する（round(density) 本相当）。"""
    # density は「本数そのもの」ではなく「本数スケール」。
    # 高さから spacing を決めることで、図形サイズや angle の回転に対して見かけ密度を安定化する。
    num_lines = int(round(float(density)))
    if num_lines < 2:
        num_lines = 2
    if num_lines > MAX_FILL_LINES:
        num_lines = MAX_FILL_LINES
    if height <= 0.0:
        return 0.0
    return float(height) / float(num_lines)


def _generate_y_values(
    min_y: float,
    max_y: float,
    base_spacing: float,
    spacing_gradient: float,
    min_spacing: float = 0.0,
) -> np.ndarray:
    """スキャンライン Y 値列を生成する（max_y は含めない）。"""
    # 入力は「回転後の作業座標」での min/max。
    # 返す y は「交点計算を行う水平スキャンライン」の列。
    if not np.isfinite(base_spacing) or base_spacing <= 0.0:
        return np.empty(0, dtype=np.float32)
    if max_y <= min_y:
        return np.empty(0, dtype=np.float32)

    # スキャンラインが頂点/辺上に一致すると交点が退化しやすい。
    # half-step でオフセットして内部をサンプリングし、小さなポリゴンでも 1 本は出るようにする。
    start = float(min_y) + 0.5 * float(base_spacing)
    if start >= max_y:
        mid = 0.5 * (float(min_y) + float(max_y))
        return np.asarray([mid], dtype=np.float32)

    if abs(spacing_gradient) < 1e-6:
        # 等間隔
        step = max(base_spacing, min_spacing)
        return np.arange(start, max_y, step, dtype=np.float32)

    height = max_y - min_y
    k = spacing_gradient

    if abs(k) < 1e-3:
        c = 1.0
    else:
        # exp 勾配の平均間隔が base_spacing 付近に収まるよう正規化係数を入れる。
        c = k / (2.0 * float(np.sinh(k / 2.0)))

    y_values: list[float] = []
    y = float(start)
    min_step = max(base_spacing * 1e-3, min_spacing)
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


@njit(cache=True)  # type: ignore[misc]
def _sort_intersections_numpy_order(
    values: np.ndarray,
    count: int,
) -> None:
    """NumPy sort と同じく、同値の -0.0 を +0.0 より前へ並べる。"""

    values[:count].sort()
    zero_start = -1
    zero_count = 0
    negative_zero_count = 0
    for index in range(count):
        value = values[index]
        if value == np.float32(0.0):
            if zero_start < 0:
                zero_start = index
            zero_count += 1
            if np.signbit(value):
                negative_zero_count += 1

    if zero_start < 0 or negative_zero_count == 0:
        return
    for index in range(negative_zero_count):
        values[zero_start + index] = np.float32(-0.0)
    for index in range(negative_zero_count, zero_count):
        values[zero_start + index] = np.float32(0.0)


@njit(cache=True)  # type: ignore[misc]
def _scanline_endpoints_njit(
    ex1: np.ndarray,
    ey1: np.ndarray,
    ey2: np.ndarray,
    edx: np.ndarray,
    edy: np.ndarray,
    y_values: np.ndarray,
) -> np.ndarray:
    """全 scanline の even-odd 交点を packed endpoint 配列へ詰める。"""

    edge_count = int(ex1.shape[0])
    scratch = np.empty((edge_count,), dtype=np.float32)
    segment_count = 0

    # 出力を過剰確保しないため、現行と同じ交点式と sort で本数を先に数える。
    for y_index in range(int(y_values.shape[0])):
        y = y_values[y_index]
        intersection_count = 0
        for edge_index in range(edge_count):
            y1 = ey1[edge_index]
            y2 = ey2[edge_index]
            dy = edy[edge_index]
            if dy == np.float32(0.0):
                continue
            if not ((y1 <= y and y < y2) or (y2 <= y and y < y1)):
                continue
            scratch[intersection_count] = (
                ex1[edge_index] + (y - y1) * edx[edge_index] / dy
            )
            intersection_count += 1

        if intersection_count < 2:
            continue
        _sort_intersections_numpy_order(scratch, intersection_count)
        for pair_index in range(0, intersection_count - 1, 2):
            if scratch[pair_index + 1] - scratch[pair_index] > np.float32(1e-9):
                segment_count += 1

    endpoints = np.empty((2 * segment_count, 2), dtype=np.float32)
    cursor = 0
    for y_index in range(int(y_values.shape[0])):
        y = y_values[y_index]
        intersection_count = 0
        for edge_index in range(edge_count):
            y1 = ey1[edge_index]
            y2 = ey2[edge_index]
            dy = edy[edge_index]
            if dy == np.float32(0.0):
                continue
            if not ((y1 <= y and y < y2) or (y2 <= y and y < y1)):
                continue
            scratch[intersection_count] = (
                ex1[edge_index] + (y - y1) * edx[edge_index] / dy
            )
            intersection_count += 1

        if intersection_count < 2:
            continue
        _sort_intersections_numpy_order(scratch, intersection_count)
        for pair_index in range(0, intersection_count - 1, 2):
            x_a = scratch[pair_index]
            x_b = scratch[pair_index + 1]
            if not (x_b - x_a > np.float32(1e-9)):
                continue
            endpoints[cursor, 0] = x_a
            endpoints[cursor, 1] = y
            endpoints[cursor + 1, 0] = x_b
            endpoints[cursor + 1, 1] = y
            cursor += 2

    return endpoints


@njit(cache=True)  # type: ignore[misc]
def _scanline_array_bounds(values: np.ndarray) -> tuple[bool, float, float]:
    """finite/通常値判定と、非ゼロ最小絶対値・最大絶対値を返す。"""

    min_nonzero = np.inf
    max_abs = 0.0
    for index in range(int(values.shape[0])):
        value = np.float64(values[index])
        if not np.isfinite(value):
            return False, 0.0, 0.0
        value_abs = abs(value)
        if value_abs > max_abs:
            max_abs = value_abs
        if value_abs != 0.0:
            if value_abs < float(_SCANLINE_SAFE_ABS_MIN):
                return False, 0.0, 0.0
            if value_abs < min_nonzero:
                min_nonzero = value_abs
    return True, min_nonzero, max_abs


@njit(cache=True)  # type: ignore[misc]
def _scanline_arithmetic_is_safe(
    ex1: np.ndarray,
    ey1: np.ndarray,
    ex2: np.ndarray,
    ey2: np.ndarray,
    edx: np.ndarray,
    edy: np.ndarray,
    y_values: np.ndarray,
) -> bool:
    """Numba 経路で NumPy の浮動小数点通知を省略しない通常範囲かを返す。"""

    ex1_safe, _, ex1_max = _scanline_array_bounds(ex1)
    ey1_safe, _, ey1_max = _scanline_array_bounds(ey1)
    ex2_safe, _, _ = _scanline_array_bounds(ex2)
    ey2_safe, _, _ = _scanline_array_bounds(ey2)
    edx_safe, _, edx_max = _scanline_array_bounds(edx)
    edy_safe, edy_min, _ = _scanline_array_bounds(edy)
    y_safe, _, y_max = _scanline_array_bounds(y_values)
    if not (
        ex1_safe
        and ey1_safe
        and ex2_safe
        and ey2_safe
        and edx_safe
        and edy_safe
        and y_safe
    ):
        return False
    if edy_min == np.inf or y_values.size == 0:
        return True

    float32_limit = float(np.finfo(np.float32).max) / 4.0
    y_delta_bound = y_max + ey1_max
    product_bound = y_delta_bound * edx_max
    quotient_bound = product_bound / edy_min
    intersection_bound = ex1_max + quotient_bound
    return bool(
        y_delta_bound <= float32_limit
        and product_bound <= float32_limit
        and quotient_bound <= float32_limit
        and intersection_bound <= float32_limit
    )


def _scanline_endpoints_numpy(
    ex1: np.ndarray,
    ey1: np.ndarray,
    ey2: np.ndarray,
    edx: np.ndarray,
    edy: np.ndarray,
    y_values: np.ndarray,
) -> np.ndarray:
    """浮動小数点境界用に strict な NumPy scanline 演算を実行する。"""

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
        valid_count = 0
        for pair_index in range(0, int(xs_sorted.size) - 1, 2):
            if float(xs_sorted[pair_index + 1] - xs_sorted[pair_index]) > 1e-9:
                valid_count += 1
        if valid_count > 0:
            scanlines.append((y, xs_sorted))
            segment_count += valid_count

    endpoints = np.empty((2 * segment_count, 2), dtype=np.float32)
    cursor = 0
    for y, xs_sorted in scanlines:
        for pair_index in range(0, int(xs_sorted.size) - 1, 2):
            x_a = xs_sorted[pair_index]
            x_b = xs_sorted[pair_index + 1]
            if float(x_b - x_a) <= 1e-9:
                continue
            endpoints[cursor, 0] = x_a
            endpoints[cursor, 1] = y
            endpoints[cursor + 1, 0] = x_b
            endpoints[cursor + 1, 1] = y
            cursor += 2
    return endpoints


def _generate_line_fill_evenodd_multi(
    coords_2d: np.ndarray,
    offsets: np.ndarray,
    *,
    density: float,
    angle_rad: float,
    spacing_override: float | None,
    min_spacing: float,
    spacing_gradient: float,
) -> np.ndarray:
    """複数輪郭のハッチ端点をline順のpacked ``(2*n, 2)`` で返す。"""
    # 目的:
    # - 複数輪郭（外周＋穴）をまとめて扱い、even-odd で内側区間だけを線分化する。
    #
    # 手順:
    # 1) 角度を打ち消す方向に回転し、ハッチが水平になる作業座標を作る。
    # 2) y=const のスキャンライン列を生成する。
    # 3) 各スキャンラインとポリゴン辺の交点 x を集め、ソートして [x0,x1],[x2,x3]... を線分にする。
    # 4) 回転した場合は線分を元角度に戻す。
    if density <= 0.0 or offsets.size <= 1 or coords_2d.size == 0:
        return np.empty((0, 2), dtype=np.float32)

    c2 = coords_2d.astype(np.float32, copy=False)
    center = np.mean(c2, axis=0)
    work = c2
    rot_fwd: np.ndarray | None = None

    if angle_rad != 0.0:
        # ポリゴンを -angle 回転 → 作業座標ではハッチが水平（y 方向スキャン）になる。
        cos_inv = float(np.cos(-angle_rad))
        sin_inv = float(np.sin(-angle_rad))
        rot_inv = np.array([[cos_inv, -sin_inv], [sin_inv, cos_inv]], dtype=np.float32)
        work = (c2 - center) @ rot_inv.T + center
        # 線分を元角度へ戻す回転は、先に作っておく（スキャン毎に再計算しない）。
        cos_fwd = float(np.cos(angle_rad))
        sin_fwd = float(np.sin(angle_rad))
        rot_fwd = np.array([[cos_fwd, -sin_fwd], [sin_fwd, cos_fwd]], dtype=np.float32)

    ref_height = float(np.max(c2[:, 1]) - np.min(c2[:, 1]))
    if ref_height <= 0.0:
        return np.empty((0, 2), dtype=np.float32)

    min_y = float(np.min(work[:, 1]))
    max_y = float(np.max(work[:, 1]))

    spacing = (
        float(spacing_override)
        if spacing_override is not None
        else _spacing_from_height(ref_height, density)
    )
    if not np.isfinite(spacing) or spacing <= 0.0:
        return np.empty((0, 2), dtype=np.float32)

    y_values = _generate_y_values(
        min_y,
        max_y,
        spacing,
        float(spacing_gradient),
        min_spacing,
    )

    # 全輪郭の辺を 1 つの配列へ集約する（交点計算のベクトル化）。
    edges_list: list[np.ndarray] = []
    for i in range(int(offsets.size) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        if e - s < 2:
            continue
        poly = work[s:e]
        if poly.shape[0] < 2:
            continue
        nxt = np.roll(poly, -1, axis=0)
        # [x1,y1,x2,y2] の形にしておくと、交点 x を一括計算しやすい。
        edges_list.append(np.concatenate([poly, nxt], axis=1))

    if not edges_list:
        return np.empty((0, 2), dtype=np.float32)
    edges = np.concatenate(edges_list, axis=0).astype(np.float32, copy=False)
    ex1 = edges[:, 0]
    ey1 = edges[:, 1]
    ex2 = edges[:, 2]
    ey2 = edges[:, 3]
    edy = ey2 - ey1
    edx = ex2 - ex1

    if _scanline_arithmetic_is_safe(
        ex1,
        ey1,
        ex2,
        ey2,
        edx,
        edy,
        y_values,
    ):
        endpoints = _scanline_endpoints_njit(
            ex1,
            ey1,
            ey2,
            edx,
            edy,
            y_values,
        )
    else:
        endpoints = _scanline_endpoints_numpy(
            ex1,
            ey1,
            ey2,
            edx,
            edy,
            y_values,
        )
    if rot_fwd is not None and endpoints.size > 0:
        endpoints[:] = (endpoints - center) @ rot_fwd.T + center
    return endpoints


def _pack_planar_fill_chunks(
    chunks: Sequence[np.ndarray], frame: PlanarFrame
) -> GeomTuple:
    """local boundaryとpacked hatchを詰め、world変換を一度だけ適用する。"""

    if not chunks:
        return empty_packed_geometry()
    total_vertices = sum(int(chunk.shape[0]) for chunk in chunks)
    total_lines = sum(
        1 if int(chunk.shape[1]) == 3 else int(chunk.shape[0]) // 2 for chunk in chunks
    )
    local = np.zeros((total_vertices, 3), dtype=np.float64)
    offsets = np.empty((total_lines + 1,), dtype=np.int32)
    offsets[0] = 0
    vertex_cursor = 0
    line_cursor = 0
    for chunk in chunks:
        count = int(chunk.shape[0])
        if int(chunk.shape[1]) == 3:
            local[vertex_cursor : vertex_cursor + count] = chunk
            line_cursor += 1
            offsets[line_cursor] = np.int32(vertex_cursor + count)
        else:
            local[vertex_cursor : vertex_cursor + count, :2] = chunk
            line_count = count // 2
            offsets[line_cursor + 1 : line_cursor + line_count + 1] = (
                vertex_cursor + np.arange(2, count + 1, 2, dtype=np.int32)
            )
            line_cursor += line_count
        vertex_cursor += count
    coords = frame.to_world(local).astype(np.float32, copy=False)
    return coords, offsets


@effect(meta=fill_meta)
def fill(
    g: GeomTuple,
    *,
    angle_sets: int = 1,
    angle: float = 45.0,
    density: float = 35.0,
    min_spacing: float = 0.05,
    spacing_gradient: float = 0.0,
    remove_boundary: bool = False,
) -> GeomTuple:
    """閉領域をハッチングで塗りつぶす。

    Parameters
    ----------
    g : tuple[np.ndarray, np.ndarray]
        入力実体ジオメトリ（coords, offsets）。
    angle_sets : int, default 1
        方向本数。1=単方向、2=90°クロス、3=60°間隔、...（180°を等分）。
    angle : float, default 45.0
        基準角 [deg]。
    density : float, default 35.0
        密度スケール。
        `round(density)` 本相当の間隔を基準高さから算出する。0 では塗り線を生成しない。
    min_spacing : float, default 0.0
        同一方向の隣接ハッチ走査線に適用する最小ピッチ。
        fill 評価時の作業平面上の scene 座標単位で指定し、0.0 で無効になる。
        標準的な 2D plot では、scene 座標単位は通常 mm と一致する。
    spacing_gradient : float, default 0.0
        -4 以上 4 以下の、スキャン方向に沿った線間隔勾配。
        0.0 で一様間隔。
    remove_boundary : bool, default False
        True なら入力境界（入力ポリライン）を出力から除去する。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        境界線（必要なら）と塗り線を含む実体ジオメトリ（coords, offsets）。

    Raises
    ------
    ValueError
        `angle_sets` が 1 未満、`density` が負、`min_spacing` が有限な 0 以上の
        値でない、または `spacing_gradient` が -4 から 4 の範囲外の場合。

    Notes
    -----
    `min_spacing` が保証するのは、同じ planar filled region と hatch angle family
    に属する、連続する異なる走査線レベル間の垂直な中心線ピッチである。
    異なる region や angle family、境界線、別の fill 呼び出しとの距離は保証しない。
    fill 後の縮小、非一様変形、warp、3D 投影は、最終出力上のピッチを縮め得る。
    """
    if angle_sets < 1:
        raise ValueError("fill の angle_sets は 1 以上である必要がある")
    if density < 0.0:
        raise ValueError("fill の density は 0 以上である必要がある")
    min_spacing = float(min_spacing)
    if not np.isfinite(min_spacing) or min_spacing < 0.0:
        raise ValueError("fill の min_spacing は有限な 0 以上の値である必要がある")
    if not -4.0 <= spacing_gradient <= 4.0:
        raise ValueError("fill の spacing_gradient は -4 以上 4 以下である必要がある")

    base_angle_rad = float(np.deg2rad(angle))

    # 返すジオメトリの構造:
    # - remove_boundary=False: 入力境界ポリライン（そのまま）+ 生成したハッチ線分
    # - remove_boundary=True : ハッチ線分のみ
    coords, offsets = g
    if coords.shape[0] == 0:
        return coords, offsets

    # ハッチ角は 180° を k 分割（π/k）する。
    # （0° と 180° は同方向扱いなので 2π ではなく π）
    # 1) 全体がほぼ平面なら、外周＋穴をグルーピングして even-odd 塗りを行う。
    # 3D -> XY 平面への整列で 2D 化し、生成した線分を元姿勢へ戻す。
    global_frame = PlanarFrame.from_points(coords, offsets)
    if global_frame.is_planar(planarity_threshold(coords)):
        coords_xy_all = global_frame.to_local(coords)
        coords2d_all = coords_xy_all[:, :2].astype(np.float32, copy=False)
        if _is_degenerate_fill_input(coords2d_all, offsets):
            _emit_fill_fallback(
                "degenerate_planar_input",
                reason="fill requires a non-degenerate closed planar region",
            )
            return coords, offsets
        groups = _build_evenodd_groups(coords2d_all, offsets)

        out_lines: list[np.ndarray]
        if not groups:
            # ループが無い（またはリング条件を満たさない）場合は境界の有無だけを反映して返す。
            _emit_fill_fallback(
                "no_closed_rings",
                reason="fill could not build a closed even-odd region",
            )
            out_lines = []
            for poly_i in range(int(offsets.size) - 1):
                if remove_boundary:
                    continue
                s = int(offsets[poly_i])
                e = int(offsets[poly_i + 1])
                out_lines.append(coords[s:e])
            return pack_polylines(out_lines)

        ref_height_global = float(
            np.max(coords2d_all[:, 1]) - np.min(coords2d_all[:, 1])
        )
        if ref_height_global <= 0.0:
            # グループの実体が無い場合は境界のみを返す。
            out_lines = []
            for ring_indices in groups:
                if remove_boundary:
                    continue
                for ring_i in ring_indices:
                    s = int(offsets[ring_i])
                    e = int(offsets[ring_i + 1])
                    out_lines.append(coords[s:e])
            return pack_polylines(out_lines)

        out_chunks: list[np.ndarray] = []
        for ring_indices in groups:
            # 境界保持（グループ単位）
            if not remove_boundary:
                for ring_i in ring_indices:
                    s = int(offsets[ring_i])
                    e = int(offsets[ring_i + 1])
                    out_chunks.append(coords_xy_all[s:e])

            # density<=0 は「塗り線無し」。境界だけで終わる。
            if density <= 0.0:
                continue

            # global では「全体の参照高さ」から spacing を決め、グループ間で見かけ密度が揃うようにする。
            base_spacing = _spacing_from_height(ref_height_global, density)
            if base_spacing <= 0.0:
                continue

            parts: list[np.ndarray] = []
            g_offsets = np.zeros((len(ring_indices) + 1,), dtype=np.int32)
            acc = 0
            for j, ring_i in enumerate(ring_indices):
                s = int(offsets[ring_i])
                e = int(offsets[ring_i + 1])
                poly = coords2d_all[s:e]
                if poly.shape[0] < 2:
                    continue
                parts.append(poly)
                acc += int(poly.shape[0])
                g_offsets[j + 1] = acc
            if not parts or g_offsets[-1] <= 0:
                continue

            # group の輪郭を 1 本の coords + offsets へ畳んで、交点計算を一括化する。
            g_coords2d = np.concatenate(parts, axis=0)
            for i in range(angle_sets):
                ang_i = base_angle_rad + (np.pi / angle_sets) * i
                endpoints = _generate_line_fill_evenodd_multi(
                    g_coords2d,
                    g_offsets,
                    density=density,
                    angle_rad=float(ang_i),
                    spacing_override=float(base_spacing),
                    min_spacing=min_spacing,
                    spacing_gradient=spacing_gradient,
                )
                if endpoints.size > 0:
                    out_chunks.append(endpoints)

        return _pack_planar_fill_chunks(out_chunks, global_frame)

    # 2) 全体が非平面なら、各ポリラインごとに「平面なら塗り、非平面なら境界のみ」とする。
    # この経路では外環＋穴の統合は行わない（グローバルな平面が取れないため）。
    out_lines = []
    for poly_i in range(int(offsets.size) - 1):
        s = int(offsets[poly_i])
        e = int(offsets[poly_i + 1])
        vertices = coords[s:e]

        if vertices.shape[0] < 3:
            # 退化入力はそのまま返す（remove_boundary の有無に関わらず no-op）。
            _emit_fill_fallback(
                "polyline_with_fewer_than_three_points",
                reason="fill requires at least three points per boundary",
            )
            out_lines.append(vertices)
            continue

        frame = PlanarFrame.from_points(vertices)
        if not frame.valid:
            # 点・直線は閉領域を定義しないため、remove_boundary に関係なく no-op。
            _emit_fill_fallback(
                "invalid_planar_frame",
                reason="fill could not determine a plane for the boundary",
            )
            out_lines.append(vertices)
            continue
        if not frame.is_planar(planarity_threshold(vertices)):
            _emit_fill_fallback(
                "nonplanar_boundary",
                reason="fill only hatches planar boundaries",
            )
            if not remove_boundary:
                out_lines.append(vertices)
            continue
        vxy = frame.to_local(vertices)

        coords2d = vxy[:, :2].astype(np.float32, copy=False)
        if _is_degenerate_fill_input(
            coords2d, np.array([0, coords2d.shape[0]], dtype=np.int32)
        ):
            # 退化入力はそのまま返す（remove_boundary / density の有無に関わらず no-op）。
            _emit_fill_fallback(
                "degenerate_boundary",
                reason="fill requires a non-degenerate closed boundary",
            )
            out_lines.append(vertices)
            continue

        if not remove_boundary:
            out_lines.append(vertices)

        if density <= 0.0:
            continue

        ref_height = float(np.max(coords2d[:, 1]) - np.min(coords2d[:, 1]))
        base_spacing = _spacing_from_height(ref_height, density)
        if base_spacing <= 0.0:
            continue

        poly_offsets = np.array([0, coords2d.shape[0]], dtype=np.int32)
        hatch_chunks: list[np.ndarray] = []
        for i in range(angle_sets):
            ang_i = base_angle_rad + (np.pi / angle_sets) * i
            endpoints = _generate_line_fill_evenodd_multi(
                coords2d,
                poly_offsets,
                density=density,
                angle_rad=float(ang_i),
                spacing_override=float(base_spacing),
                min_spacing=min_spacing,
                spacing_gradient=spacing_gradient,
            )
            if endpoints.size > 0:
                hatch_chunks.append(endpoints)
        if hatch_chunks:
            local_hatch = np.zeros(
                (sum(int(chunk.shape[0]) for chunk in hatch_chunks), 3),
                dtype=np.float64,
            )
            cursor = 0
            for chunk in hatch_chunks:
                count = int(chunk.shape[0])
                local_hatch[cursor : cursor + count, :2] = chunk
                cursor += count
            world_hatch = frame.to_world(local_hatch).astype(np.float32, copy=False)
            out_lines.extend(
                world_hatch[start : start + 2]
                for start in range(0, int(world_hatch.shape[0]), 2)
            )

    return pack_polylines(out_lines)
