"""
ハッチ塗りつぶし effect。

閉領域（外周＋穴）に対して偶奇規則で内部を判定し、指定角度のハッチ線分を生成する。
3D 入力は一度 XY 平面へ整列して 2D で処理し、生成した線分を元の姿勢へ戻す。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
from numba import njit  # type: ignore[attr-defined]

from grafix.core.effect_registry import effect
from grafix.core.realized_geometry import RealizedGeometry
from .util import transform_back, transform_to_xy_plane
from grafix.core.parameters.meta import ParamMeta

# 生成する塗り線の最大本数（密度の上限）。
MAX_FILL_LINES = 1000
NONPLANAR_EPS_ABS = 1e-6
NONPLANAR_EPS_REL = 1e-5

fill_meta = {
    "angle_sets": ParamMeta(kind="int", ui_min=1, ui_max=6),
    "angle": ParamMeta(kind="float", ui_min=0.0, ui_max=180.0),
    "density": ParamMeta(kind="float", ui_min=0.0, ui_max=float(MAX_FILL_LINES)),
    "spacing_gradient": ParamMeta(kind="float", ui_min=-5.0, ui_max=5.0),
    "remove_boundary": ParamMeta(kind="bool"),
}


def _empty_geometry() -> RealizedGeometry:
    # RealizedGeometry の「空」表現。
    # offsets は常に先頭 0 を 1 つ持つ（ポリライン 0 本の意味）。
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _as_float_cycle(value: float | Sequence[float]) -> tuple[float, ...]:
    """float または float 列を「サイクル可能なタプル」に正規化する。"""
    # `np.ndarray` は `collections.abc.Sequence` を満たさないため個別扱いする。
    if isinstance(value, np.ndarray):
        if value.ndim == 0:
            return (float(value),)
        if value.size <= 0:
            raise ValueError("空のシーケンスは指定できません")
        return tuple(float(v) for v in value.ravel())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) <= 0:
            raise ValueError("空のシーケンスは指定できません")
        return tuple(float(v) for v in value)
    return (float(value),)


def _as_int_cycle(value: int | Sequence[int]) -> tuple[int, ...]:
    """int または int 列を「サイクル可能なタプル」に正規化する。"""
    if isinstance(value, np.ndarray):
        if value.ndim == 0:
            return (int(value),)
        if value.size <= 0:
            raise ValueError("空のシーケンスは指定できません")
        return tuple(int(v) for v in value.ravel())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) <= 0:
            raise ValueError("空のシーケンスは指定できません")
        return tuple(int(v) for v in value)
    return (int(value),)


def _as_bool_cycle(value: bool | Sequence[bool]) -> tuple[bool, ...]:
    """bool または bool 列を「サイクル可能なタプル」に正規化する。"""
    if isinstance(value, np.ndarray):
        if value.ndim == 0:
            return (bool(value),)
        if value.size <= 0:
            raise ValueError("空のシーケンスは指定できません")
        return tuple(bool(v) for v in value.ravel())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) <= 0:
            raise ValueError("空のシーケンスは指定できません")
        return tuple(bool(v) for v in value)
    return (bool(value),)


def _planarity_threshold(points: np.ndarray) -> float:
    """点群スケールに基づく平面性判定の閾値を返す。"""
    # スケール依存の許容誤差:
    # - 小さな形状では絶対誤差（eps_abs）が支配
    # - 大きな形状では相対誤差（eps_rel * 対角長）が支配
    if points.size == 0:
        return float(NONPLANAR_EPS_ABS)
    p = points.astype(np.float64, copy=False)
    mins = np.min(p, axis=0)
    maxs = np.max(p, axis=0)
    diag = float(np.linalg.norm(maxs - mins))
    return max(float(NONPLANAR_EPS_ABS), float(NONPLANAR_EPS_REL) * diag)


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


def _point_in_polygon(point: np.ndarray, polygon: np.ndarray) -> bool:
    """点が多角形内部にあるかを返す（境界上は False 扱い）。"""
    # 目的:
    # - even-odd のグルーピング（外環/穴の判定）に使う。
    # - partition 後のセルは隣接し、代表点が「他セルの境界上」に乗りやすい。
    #   このとき境界上を inside 扱いすると hole 誤判定が起きるため、境界は必ず False にする。
    x = float(point[0])
    y = float(point[1])
    return bool(_point_in_polygon_njit(polygon, x, y))


@njit(cache=True)  # type: ignore[misc]
def _point_in_polygon_njit(polygon: np.ndarray, x: float, y: float) -> bool:
    """点が多角形内部にあるかを返す（境界上は False 扱い、Numba 版）。"""
    n = int(polygon.shape[0])
    if n < 3:
        return False

    # 境界上を明示的に除外してから、偶奇レイキャストで内部判定する。
    eps = 1e-6

    x1 = float(polygon[n - 1, 0])
    y1 = float(polygon[n - 1, 1])
    for i in range(n):
        x2 = float(polygon[i, 0])
        y2 = float(polygon[i, 1])

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
    x1 = float(polygon[n - 1, 0])
    y1 = float(polygon[n - 1, 1])
    for i in range(n):
        x2 = float(polygon[i, 0])
        y2 = float(polygon[i, 1])
        if (y1 > y) != (y2 > y):
            x_int = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < x_int:
                inside = not inside
        x1, y1 = x2, y2
    return inside


def _estimate_global_xy_transform_pca(
    coords: np.ndarray,
    offsets: np.ndarray,
    *,
    threshold: float,
) -> tuple[np.ndarray, np.ndarray, float] | None:
    """全点から平面を推定し、XY 平面へ整列した座標と変換を返す。

    - PCA（最小分散軸）で法線を推定し、Z 軸へ合わせる回転を作る。
    - 面内回転は「代表リングの最長辺」を +X に合わせて固定する。
    - 返す座標は z=0 に寄せる（代表リング先頭点の z を 0 に合わせる）。
    """
    # ここで返す aligned_all は「全点を同一平面へ倒した座標」。
    # fill の本体では、この 2D 座標で:
    # - 外環＋穴の even-odd グルーピング
    # - ハッチ線分生成（水平スキャンライン）
    # を行い、最後に transform_back で元の 3D 姿勢へ戻す。
    if coords.shape[0] < 3 or offsets.size <= 1:
        return None

    # 1) PCA で平面法線を推定（最小分散軸）。
    coords64 = coords.astype(np.float64, copy=False)
    centroid = np.mean(coords64, axis=0)
    centered = coords64 - centroid
    cov = centered.T @ centered

    _, eigvecs = np.linalg.eigh(cov)
    if eigvecs.shape != (3, 3):
        return None

    normal = eigvecs[:, 0]
    n_norm = float(np.linalg.norm(normal))
    if not np.isfinite(n_norm) or n_norm <= 0.0:
        return None
    normal = normal / n_norm

    # 法線の符号は不定なので、Z が正になるように揃える（姿勢の一意化）。
    if float(normal[2]) < 0.0:
        normal = -normal

    # 2) 推定法線を +Z に合わせる回転を作る。
    z_axis = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    rotation_axis = np.cross(normal, z_axis)
    axis_norm = float(np.linalg.norm(rotation_axis))
    if axis_norm <= 1e-12:
        r0 = np.eye(3, dtype=np.float64)
    else:
        rotation_axis = rotation_axis / axis_norm
        cos_theta = float(np.dot(normal, z_axis))
        cos_theta = float(np.clip(cos_theta, -1.0, 1.0))
        angle = float(np.arccos(cos_theta))

        k = np.zeros((3, 3), dtype=np.float64)
        k[0, 1] = -rotation_axis[2]
        k[0, 2] = rotation_axis[1]
        k[1, 0] = rotation_axis[2]
        k[1, 2] = -rotation_axis[0]
        k[2, 0] = -rotation_axis[1]
        k[2, 1] = rotation_axis[0]

        r0 = np.eye(3, dtype=np.float64) + np.sin(angle) * k + (1.0 - np.cos(angle)) * (k @ k)

    ring_indices = [
        i
        for i in range(int(offsets.size) - 1)
        if int(offsets[i + 1]) - int(offsets[i]) >= 3
    ]
    if not ring_indices:
        return None

    # 3) 面内回転の自由度を潰す（ハッチ方向の安定化）。
    # 面を Z に倒しただけだと、XY 平面内で任意回転できてしまい、描画がフレームごとに揺れる。
    # 代表リング（面積最大）の「最初に見つかった非ゼロ辺」を +X に合わせる。
    ref_ring_i = ring_indices[0]
    ref_area = -1.0
    for ring_i in ring_indices:
        start = int(offsets[ring_i])
        end = int(offsets[ring_i + 1])
        poly = coords64[start:end]
        if poly.shape[0] < 3:
            continue
        aligned0 = poly @ r0.T
        a = _polygon_area_abs(aligned0[:, :2])
        if a > ref_area:
            ref_area = a
            ref_ring_i = ring_i

    s_ref = int(offsets[ref_ring_i])
    e_ref = int(offsets[ref_ring_i + 1])
    ref_poly = coords64[s_ref:e_ref]
    if ref_poly.shape[0] < 2:
        return None

    aligned_ref = ref_poly @ r0.T
    xy = aligned_ref[:, :2]
    phi: float | None = None
    for i in range(int(xy.shape[0]) - 1):
        dx = float(xy[i + 1, 0] - xy[i, 0])
        dy = float(xy[i + 1, 1] - xy[i, 1])
        if dx * dx + dy * dy > 1e-12:
            phi = float(np.arctan2(dy, dx))
            break
    if phi is None:
        return None

    cos_phi = float(np.cos(phi))
    sin_phi = float(np.sin(phi))
    rz = np.eye(3, dtype=np.float64)
    rz[0, 0] = cos_phi
    rz[0, 1] = sin_phi
    rz[1, 0] = -sin_phi
    rz[1, 1] = cos_phi

    # 4) 最終回転を適用し、z=0 近傍へ寄せる（残差チェックを簡単にする）。
    rot = rz @ r0
    aligned_all = coords64 @ rot.T
    z_offset = float(aligned_all[s_ref, 2])
    aligned_all[:, 2] -= z_offset
    residual = float(np.max(np.abs(aligned_all[:, 2])))
    if residual > threshold:
        return None
    return aligned_all, rot, z_offset


@njit(cache=True)  # type: ignore[misc]
def _polygon_area_abs_coords_njit(coords_2d_all: np.ndarray, start: int, end: int) -> float:
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


def _build_evenodd_groups(coords_2d_all: np.ndarray, offsets: np.ndarray) -> list[list[int]]:
    """外周＋穴を even-odd でグルーピングし、[outer, hole...] のリストを返す。"""
    # 目的:
    # - 入力が「外周 + 穴 + 穴の穴 + ...」の入れ子になっていても、
    #   偶奇規則（even-odd）で「外環ごとに穴をぶら下げた集合」を作る。
    #
    # 実装方針（旧実装に寄せたもの）:
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
    """高さと密度から線間隔を算出する（旧仕様: round(density) 本相当）。"""
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
    min_y: float, max_y: float, base_spacing: float, spacing_gradient: float
) -> np.ndarray:
    """旧仕様のスキャンライン Y 値列を生成する（max_y は含めない）。"""
    # 入力は「回転後の作業座標」での min/max。
    # 返す y は「交点計算を行う水平スキャンライン」の列。
    if not np.isfinite(base_spacing) or base_spacing <= 0.0:
        return np.empty(0, dtype=np.float32)
    if not np.isfinite(spacing_gradient):
        spacing_gradient = 0.0
    if max_y <= min_y:
        return np.empty(0, dtype=np.float32)

    # スキャンラインが頂点/辺上に一致すると交点が退化しやすい。
    # half-step でオフセットして内部をサンプリングし、小さなポリゴンでも 1 本は出るようにする。
    start = float(min_y) + 0.5 * float(base_spacing)
    if start >= max_y:
        mid = 0.5 * (float(min_y) + float(max_y))
        return np.asarray([mid], dtype=np.float32)

    if abs(spacing_gradient) < 1e-6:
        # 等間隔（旧仕様）
        return np.arange(start, max_y, base_spacing, dtype=np.float32)

    height = max_y - min_y
    k = float(spacing_gradient)
    if k > 4.0:
        k = 4.0
    elif k < -4.0:
        k = -4.0

    if abs(k) < 1e-3:
        c = 1.0
    else:
        # exp 勾配の平均間隔が base_spacing 付近に収まるよう正規化係数を入れる。
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


def _generate_line_fill_evenodd_multi(
    coords_2d: np.ndarray,
    offsets: np.ndarray,
    *,
    density: float,
    angle_rad: float,
    spacing_override: float | None,
    spacing_gradient: float,
) -> list[np.ndarray]:
    """複数輪郭を偶奇規則でまとめてハッチングする（2D 平面前提）。"""
    # 目的:
    # - 複数輪郭（外周＋穴）をまとめて扱い、even-odd で内側区間だけを線分化する。
    #
    # 手順:
    # 1) 角度を打ち消す方向に回転し、ハッチが水平になる作業座標を作る。
    # 2) y=const のスキャンライン列を生成する。
    # 3) 各スキャンラインとポリゴン辺の交点 x を集め、ソートして [x0,x1],[x2,x3]... を線分にする。
    # 4) 回転した場合は線分を元角度に戻す。
    if density <= 0.0 or offsets.size <= 1 or coords_2d.size == 0:
        return []

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
        return []

    min_y = float(np.min(work[:, 1]))
    max_y = float(np.max(work[:, 1]))

    spacing = float(spacing_override) if spacing_override is not None else _spacing_from_height(ref_height, density)
    if not np.isfinite(spacing) or spacing <= 0.0:
        return []

    y_values = _generate_y_values(min_y, max_y, spacing, float(spacing_gradient))
    out_lines: list[np.ndarray] = []

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
        return []
    edges = np.concatenate(edges_list, axis=0).astype(np.float32, copy=False)
    ex1 = edges[:, 0]
    ey1 = edges[:, 1]
    ex2 = edges[:, 2]
    ey2 = edges[:, 3]
    edy = ey2 - ey1
    edx = ex2 - ex1

    for y in y_values:
        yy = float(y)
        # 半開区間で交差判定し、頂点での二重カウントを抑える。
        mask = ((ey1 <= yy) & (yy < ey2)) | ((ey2 <= yy) & (yy < ey1))
        mask &= edy != 0.0
        if not np.any(mask):
            continue

        # 交点の x 座標（線形補間）。水平辺は除外済み。
        xs = ex1[mask] + (yy - ey1[mask]) * edx[mask] / edy[mask]
        if xs.size < 2:
            continue

        # even-odd: ソートした交点を 2 個ずつペアにして内側区間を得る。
        xs_sorted = np.sort(xs.astype(np.float32, copy=False))
        for j in range(0, int(xs_sorted.size) - 1, 2):
            x_a = float(xs_sorted[j])
            x_b = float(xs_sorted[j + 1])
            if x_b - x_a <= 1e-9:
                continue
            seg2d = np.array([[x_a, float(y)], [x_b, float(y)]], dtype=np.float32)
            if rot_fwd is not None:
                # 作業座標 → 元角度へ戻す。
                seg2d = (seg2d - center) @ rot_fwd.T + center
            out_lines.append(seg2d)
    return out_lines


def _lines_to_realized(lines: Sequence[np.ndarray]) -> RealizedGeometry:
    """ポリライン列を RealizedGeometry にまとめる。"""
    if not lines:
        return _empty_geometry()

    # RealizedGeometry は「coords 1 本 + offsets」で複数ポリラインを表現する。
    # ここでは lines を連結し、各線の終端 index を offsets に積む。
    coords_list: list[np.ndarray] = []
    offsets = np.zeros((len(lines) + 1,), dtype=np.int32)
    acc = 0
    for i, line in enumerate(lines):
        ln = np.asarray(line)
        if ln.ndim != 2 or ln.shape[1] != 3:
            raise ValueError("lines は shape (N,3) の配列列である必要がある")
        coords_list.append(ln.astype(np.float32, copy=False))
        acc += int(ln.shape[0])
        offsets[i + 1] = acc
    coords = np.concatenate(coords_list, axis=0) if coords_list else np.zeros((0, 3), dtype=np.float32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@effect(meta=fill_meta)
def fill(
    inputs: Sequence[RealizedGeometry],
    *,
    angle_sets: int | Sequence[int] = 1,
    angle: float | Sequence[float] = 45.0,
    density: float | Sequence[float] = 35.0,
    spacing_gradient: float | Sequence[float] = 0.0,
    remove_boundary: bool | Sequence[bool] = False,
) -> RealizedGeometry:
    """閉領域をハッチングで塗りつぶす。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        入力実体ジオメトリ列。通常は 1 要素。
    angle_sets : int | Sequence[int], default 1
        方向本数（シーケンス指定時はグループごとにサイクル適用）。1=単方向、2=90°クロス、3=60°間隔、...（180°を等分）。
    angle : float | Sequence[float], default 45.0
        基準角 [deg]（シーケンス指定時はグループごとにサイクル適用）。
    density : float | Sequence[float], default 35.0
        旧仕様の密度スケール（シーケンス指定時はグループごとにサイクル適用）。
        `round(density)` 本相当の間隔を基準高さから算出する。0 以下はそのグループでは塗り線を生成しない。
    spacing_gradient : float | Sequence[float], default 0.0
        スキャン方向に沿った線間隔勾配（シーケンス指定時はグループごとにサイクル適用）。
        0.0 で一様間隔。
    remove_boundary : bool | Sequence[bool], default False
        True なら入力境界（入力ポリライン）を出力から除去する（シーケンス指定時はグループごとにサイクル適用）。

    Returns
    -------
    RealizedGeometry
        境界線（必要なら）と塗り線を含む実体ジオメトリ。

    Notes
    -----
    シーケンス指定はコードからの指定を想定する（parameter_gui の編集対象にはしない）。
    """
    angle_sets_seq = _as_int_cycle(angle_sets)
    angle_seq = _as_float_cycle(angle)
    density_seq = _as_float_cycle(density)
    spacing_gradient_seq = _as_float_cycle(spacing_gradient)
    remove_boundary_seq = _as_bool_cycle(remove_boundary)

    # 返すジオメトリの構造:
    # - remove_boundary=False: 入力境界ポリライン（そのまま）+ 生成したハッチ線分
    # - remove_boundary=True : ハッチ線分のみ
    if not inputs:
        return _empty_geometry()

    base = inputs[0]
    if base.coords.shape[0] == 0:
        return base

    # ハッチ角は 180° を k 分割（π/k）する。
    # （0° と 180° は同方向扱いなので 2π ではなく π）
    # 1) 全体がほぼ平面なら、外周＋穴をグルーピングして even-odd 塗りを行う。
    # 3D -> XY 平面への整列で 2D 化し、生成した線分を元姿勢へ戻す。
    global_threshold = _planarity_threshold(base.coords)
    global_est = _estimate_global_xy_transform_pca(
        base.coords,
        base.offsets,
        threshold=float(global_threshold),
    )
    if global_est is not None:
        coords_xy_all, rot_global, z_global = global_est
        coords2d_all = coords_xy_all[:, :2].astype(np.float32, copy=False)
        if _is_degenerate_fill_input(coords2d_all, base.offsets):
            return base
        groups = _build_evenodd_groups(coords2d_all, base.offsets)

        out_lines: list[np.ndarray]
        if not groups:
            # ループが無い（またはリング条件を満たさない）場合は境界の有無だけを反映して返す。
            out_lines = []
            for poly_i in range(int(base.offsets.size) - 1):
                if bool(remove_boundary_seq[poly_i % len(remove_boundary_seq)]):
                    continue
                s = int(base.offsets[poly_i])
                e = int(base.offsets[poly_i + 1])
                out_lines.append(base.coords[s:e])
            return _lines_to_realized(out_lines)

        ref_height_global = float(np.max(coords2d_all[:, 1]) - np.min(coords2d_all[:, 1]))
        if ref_height_global <= 0.0:
            # グループの実体が無い場合は境界のみを返す。
            out_lines = []
            for gi, ring_indices in enumerate(groups):
                if bool(remove_boundary_seq[gi % len(remove_boundary_seq)]):
                    continue
                for ring_i in ring_indices:
                    s = int(base.offsets[ring_i])
                    e = int(base.offsets[ring_i + 1])
                    out_lines.append(base.coords[s:e])
            return _lines_to_realized(out_lines)

        out_lines = []
        for gi, ring_indices in enumerate(groups):
            # groupwise cycle
            remove_boundary_i = bool(remove_boundary_seq[gi % len(remove_boundary_seq)])
            density_i = float(density_seq[gi % len(density_seq)])
            grad_i = float(spacing_gradient_seq[gi % len(spacing_gradient_seq)])
            base_angle_rad = float(np.deg2rad(float(angle_seq[gi % len(angle_seq)])))
            k_i = int(angle_sets_seq[gi % len(angle_sets_seq)])
            if k_i < 1:
                k_i = 1

            # 境界保持（グループ単位）
            if not remove_boundary_i:
                for ring_i in ring_indices:
                    s = int(base.offsets[ring_i])
                    e = int(base.offsets[ring_i + 1])
                    out_lines.append(base.coords[s:e])

            # density<=0 は「塗り線無し」。境界だけで終わる。
            if not np.isfinite(density_i) or density_i <= 0.0:
                continue

            # global では「全体の参照高さ」から spacing を決め、グループ間で見かけ密度が揃うようにする。
            base_spacing = _spacing_from_height(ref_height_global, density_i)
            if base_spacing <= 0.0:
                continue

            parts: list[np.ndarray] = []
            g_offsets = np.zeros((len(ring_indices) + 1,), dtype=np.int32)
            acc = 0
            for j, ring_i in enumerate(ring_indices):
                s = int(base.offsets[ring_i])
                e = int(base.offsets[ring_i + 1])
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
            for i in range(k_i):
                ang_i = base_angle_rad + (np.pi / k_i) * i
                segs2d = _generate_line_fill_evenodd_multi(
                    g_coords2d,
                    g_offsets,
                    density=density_i,
                    angle_rad=float(ang_i),
                    spacing_override=float(base_spacing),
                    spacing_gradient=float(grad_i),
                )
                for seg in segs2d:
                    seg3 = np.zeros((int(seg.shape[0]), 3), dtype=np.float32)
                    seg3[:, :2] = seg
                    out_lines.append(transform_back(seg3, rot_global, z_global))

        return _lines_to_realized(out_lines)

    # 2) 全体が非平面なら、各ポリラインごとに「平面なら塗り、非平面なら境界のみ」とする。
    # この経路では外環＋穴の統合は行わない（グローバルな平面が取れないため）。
    out_lines = []
    for poly_i in range(int(base.offsets.size) - 1):
        s = int(base.offsets[poly_i])
        e = int(base.offsets[poly_i + 1])
        vertices = base.coords[s:e]

        density_i = float(density_seq[poly_i % len(density_seq)])
        if vertices.shape[0] < 3:
            # 退化入力はそのまま返す（remove_boundary の有無に関わらず no-op）。
            out_lines.append(vertices)
            continue

        vxy, rot, z_off = transform_to_xy_plane(vertices)
        residual = float(np.max(np.abs(vxy[:, 2].astype(np.float64, copy=False))))
        if residual > _planarity_threshold(vertices):
            # non-planar では「各ポリライン」をグループとみなし、poly_i でサイクルする。
            if not bool(remove_boundary_seq[poly_i % len(remove_boundary_seq)]):
                out_lines.append(vertices)
            continue

        coords2d = vxy[:, :2].astype(np.float32, copy=False)
        if _is_degenerate_fill_input(coords2d, np.array([0, coords2d.shape[0]], dtype=np.int32)):
            # 退化入力はそのまま返す（remove_boundary / density の有無に関わらず no-op）。
            out_lines.append(vertices)
            continue

        # non-planar では「各ポリライン」をグループとみなし、poly_i でサイクルする。
        if not bool(remove_boundary_seq[poly_i % len(remove_boundary_seq)]):
            out_lines.append(vertices)

        if not np.isfinite(density_i) or density_i <= 0.0:
            continue

        ref_height = float(np.max(coords2d[:, 1]) - np.min(coords2d[:, 1]))
        base_spacing = _spacing_from_height(ref_height, density_i)
        if base_spacing <= 0.0:
            continue

        offsets = np.array([0, coords2d.shape[0]], dtype=np.int32)
        k_i = int(angle_sets_seq[poly_i % len(angle_sets_seq)])
        if k_i < 1:
            k_i = 1
        base_angle_rad = float(np.deg2rad(float(angle_seq[poly_i % len(angle_seq)])))
        grad_i = float(spacing_gradient_seq[poly_i % len(spacing_gradient_seq)])
        for i in range(k_i):
            ang_i = base_angle_rad + (np.pi / k_i) * i
            segs2d = _generate_line_fill_evenodd_multi(
                coords2d,
                offsets,
                density=density_i,
                angle_rad=float(ang_i),
                spacing_override=float(base_spacing),
                spacing_gradient=float(grad_i),
            )
            for seg in segs2d:
                seg3 = np.zeros((int(seg.shape[0]), 3), dtype=np.float32)
                seg3[:, :2] = seg
                out_lines.append(transform_back(seg3, rot, float(z_off)))

    return _lines_to_realized(out_lines)
