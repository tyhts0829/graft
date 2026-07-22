"""閉曲線マスクの内側で差分成長を走らせ、「内側の襞」の線を生成する effect。

入力のマスク（閉曲線リング）内に複数の小さな閉ループを種として配置し、
点追加（目標間隔への再分割）+ 隣接スプリング + 近接反発を反復する。

境界付近では、外向き成分を取り除く（slide）/反射する（bounce）ことで、
マスク境界に沿った折れ・流れが出るようにする。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numba import njit, prange  # type: ignore[attr-defined, import-untyped]

from grafix.core.operation_authoring import effect
from grafix.core.operation_diagnostics import (
    emit_operation_diagnostic,
    grid_spec_from_bbox_with_diagnostic,
)
from grafix.core.parameters.meta import ParamMeta
from grafix.core.preview_quality import current_preview_quality
from grafix.core.realized_geometry import GeomTuple, concat_geom_tuples

from grafix.core.geometry_kernels.packed import (
    empty_packed_geometry,
    pack_polylines,
)
from grafix.core.geometry_kernels.planar import (
    PlanarFrame,
    PlanarRing,
    extract_planar_rings,
    pack_planar_rings,
    planarity_threshold,
)
from grafix.core.geometry_kernels.raster import signed_distance_grid_edt

_AUTO_CLOSE_THRESHOLD_DEFAULT = 1e-3

_MAX_ITERS = 10_000
_MAX_TOTAL_POINTS = 200_000
_MAX_POINTS_PER_RING = 20_000
DRAFT_MAX_ITERS = 32
DRAFT_MAX_TOTAL_POINTS = 4_096
DRAFT_MAX_FORCE_GRID_CELLS = 65_536

_BOUNDARY_PUSH_GAIN = 0.1
_MAX_SDF_GRID_POINTS = 1_000_000


growth_meta = {
    "seed_count": ParamMeta(
        kind="int",
        ui_min=0,
        ui_max=64,
        description="マスク内に初期配置して成長させるループの数。",
    ),
    "target_spacing": ParamMeta(
        kind="float",
        ui_min=0.25,
        ui_max=10.0,
        description="成長中の再分割と反発力の基準にする目標点間隔。",
    ),
    "boundary_avoid": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=4.0,
        description="マスク境界に近づいた点を内側へ押し戻す強さ。",
    ),
    "boundary_mode": ParamMeta(
        kind="choice",
        choices=("slide", "bounce"),
        description="境界へ達した点を境界沿いに流すか、内側へ反射させるか選ぶ。",
    ),
    "iters": ParamMeta(
        kind="int",
        ui_min=0,
        ui_max=2000,
        description="ループの成長シミュレーションを更新する反復回数。",
    ),
    "seed": ParamMeta(
        kind="int",
        ui_min=0,
        ui_max=9999,
        description="初期ループの配置を再現可能にする乱数シード。",
    ),
    "show_mask": ParamMeta(
        kind="bool",
        description="成長結果に入力マスクを加えて出力する。",
    ),
}


@dataclass(frozen=True, slots=True)
class _GrowthSimulationResult:
    rings: list[np.ndarray]
    iterations: int
    max_total_points: int
    point_budget_hit: bool
    rejected_total_points: int | None
    force_grid_requested_cells: int = 0
    force_grid_effective_cells: int = 0


def _ring_total_length(vertices: np.ndarray) -> float:
    if vertices.shape[0] < 2:
        return 0.0
    v = vertices.astype(np.float64, copy=False)
    d = v[1:] - v[:-1]
    seg = np.sqrt(d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1])
    return float(np.sum(seg))


def _resample_ring_closed(vertices: np.ndarray, *, step_hint: float) -> np.ndarray:
    pts = vertices.astype(np.float64, copy=False)
    if pts.shape[0] < 4:
        return pts

    total = _ring_total_length(pts)
    if total <= 0.0:
        return pts

    step = float(step_hint)
    if not np.isfinite(step) or step <= 0.0:
        return pts

    n_segments = int(math.ceil(total / step))
    n_segments = max(8, n_segments)
    step_exact = total / float(n_segments)

    out = np.empty((n_segments + 1, 2), dtype=np.float64)
    out[0] = pts[0]

    seg_i = 0
    dist_acc = 0.0
    ax = float(pts[0, 0])
    ay = float(pts[0, 1])
    bx = float(pts[1, 0])
    by = float(pts[1, 1])
    dx = bx - ax
    dy = by - ay
    seg_len = float(math.sqrt(dx * dx + dy * dy))

    for out_i in range(1, n_segments):
        target = float(out_i) * step_exact
        while dist_acc + seg_len < target and seg_len > 0.0:
            dist_acc += seg_len
            seg_i += 1
            if seg_i >= pts.shape[0] - 1:
                seg_i = pts.shape[0] - 2
                break
            ax = float(pts[seg_i, 0])
            ay = float(pts[seg_i, 1])
            bx = float(pts[seg_i + 1, 0])
            by = float(pts[seg_i + 1, 1])
            dx = bx - ax
            dy = by - ay
            seg_len = float(math.sqrt(dx * dx + dy * dy))

        if seg_len <= 0.0:
            out[out_i, 0] = ax
            out[out_i, 1] = ay
            continue

        t = (target - dist_acc) / seg_len
        out[out_i, 0] = ax + t * dx
        out[out_i, 1] = ay + t * dy

    out[-1] = out[0]
    return out


def _simplify_rings_for_sdf(
    rings: list[PlanarRing],
    *,
    step_sdf: float,
) -> list[PlanarRing]:
    step = float(step_sdf)
    if not np.isfinite(step) or step <= 0.0:
        return rings

    out: list[PlanarRing] = []
    for ring in rings:
        v = ring.vertices
        n = int(v.shape[0])
        if n < 4:
            out.append(ring)
            continue

        total = _ring_total_length(v)
        if total <= 0.0:
            out.append(ring)
            continue

        desired_segments = max(8, int(math.ceil(total / step)))
        current_segments = n - 1
        if current_segments <= int(desired_segments * 1.25):
            out.append(ring)
            continue

        v2 = _resample_ring_closed(v, step_hint=step)
        mins = np.min(v2, axis=0)
        maxs = np.max(v2, axis=0)
        out.append(PlanarRing(vertices=v2, mins=mins, maxs=maxs))

    return out


def _build_sdf_grid(
    ring_vertices: np.ndarray,
    ring_offsets: np.ndarray,
    ring_mins: np.ndarray,
    ring_maxs: np.ndarray,
    *,
    pitch_hint: float,
    pad: float,
    max_points: int,
) -> tuple[np.ndarray, float, float, float]:
    """SDF を 2D グリッドに前計算する（以降は bilinear 参照）。"""
    pitch0 = float(pitch_hint)
    if not np.isfinite(pitch0) or pitch0 <= 0.0:
        pitch0 = 1.0

    pad0 = float(pad)
    if not np.isfinite(pad0) or pad0 < 0.0:
        pad0 = 0.0

    bbox_min = np.min(ring_mins.astype(np.float64, copy=False), axis=0)
    bbox_max = np.max(ring_maxs.astype(np.float64, copy=False), axis=0)
    grid = grid_spec_from_bbox_with_diagnostic(
        bbox_min,
        bbox_max,
        pitch=pitch0,
        padding=pad0,
        max_points=max_points,
        overflow="coarsen",
    )
    if grid is None:
        empty = np.zeros((2, 2), dtype=np.float64)
        return empty, 0.0, 0.0, 1.0
    x0 = grid.origin_x
    y0 = grid.origin_y
    pitch = grid.pitch
    xs, ys = grid.coordinates()
    sdf = signed_distance_grid_edt(
        xs,
        ys,
        ring_vertices=ring_vertices,
        ring_offsets=ring_offsets,
        ring_mins=ring_mins,
        ring_maxs=ring_maxs,
        pitch=pitch,
    )
    return sdf, float(x0), float(y0), float(pitch)


@njit(cache=True, fastmath=True)
def _sample_sdf_grid_numba(
    points_xy: np.ndarray,
    sdf: np.ndarray,
    origin_x: float,
    origin_y: float,
    pitch: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = int(points_xy.shape[0])
    ny = int(sdf.shape[0])
    nx = int(sdf.shape[1])
    out_d = np.empty((n,), dtype=np.float64)
    out_gx = np.empty((n,), dtype=np.float64)
    out_gy = np.empty((n,), dtype=np.float64)

    inv = 1.0 / float(pitch)
    for i in range(n):
        fx = (float(points_xy[i, 0]) - float(origin_x)) * inv
        fy = (float(points_xy[i, 1]) - float(origin_y)) * inv

        ix = int(math.floor(fx))
        iy = int(math.floor(fy))
        tx = fx - float(ix)
        ty = fy - float(iy)

        if ix < 0:
            ix = 0
            tx = 0.0
        elif ix > nx - 2:
            ix = nx - 2
            tx = 1.0

        if iy < 0:
            iy = 0
            ty = 0.0
        elif iy > ny - 2:
            iy = ny - 2
            ty = 1.0

        d00 = float(sdf[iy, ix])
        d10 = float(sdf[iy, ix + 1])
        d01 = float(sdf[iy + 1, ix])
        d11 = float(sdf[iy + 1, ix + 1])

        omt = 1.0 - tx
        onu = 1.0 - ty
        d0 = omt * d00 + tx * d10
        d1 = omt * d01 + tx * d11
        d = onu * d0 + ty * d1

        # bilinear の勾配（セル内で線形）。signed distance の増加方向（外向き）。
        ddx = (onu * (d10 - d00) + ty * (d11 - d01)) * inv
        ddy = (omt * (d01 - d00) + tx * (d11 - d10)) * inv

        gnorm = math.sqrt(ddx * ddx + ddy * ddy)
        if gnorm > 1e-12:
            gx = ddx / gnorm
            gy = ddy / gnorm
        else:
            gx = 0.0
            gy = 0.0

        out_d[i] = d
        out_gx[i] = gx
        out_gy[i] = gy

    return out_d, out_gx, out_gy


@njit(cache=True, parallel=True, fastmath=True)
def _evaluate_growth_sdf_points_numba(
    points_xy: np.ndarray,
    ring_vertices: np.ndarray,
    ring_offsets: np.ndarray,
    ring_mins: np.ndarray,
    ring_maxs: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """growth の seed 判定用 SDF と外向き法線を返す。

    多数の離れた ring を扱うため bbox 下限で走査を省略し、growth の反復用途に
    合わせて fastmath を使う。warp の bit-exact な基準 kernel とは数値契約が異なる。
    """
    n = int(points_xy.shape[0])
    n_rings = int(ring_offsets.shape[0]) - 1

    out_d = np.empty((n,), dtype=np.float64)
    out_gx = np.empty((n,), dtype=np.float64)
    out_gy = np.empty((n,), dtype=np.float64)

    for i in prange(n):
        x = float(points_xy[i, 0])
        y = float(points_xy[i, 1])

        min_ds = 1e300
        qx = 0.0
        qy = 0.0
        inside_parity = 0

        for ri in range(n_rings):
            s = int(ring_offsets[ri])
            e = int(ring_offsets[ri + 1])

            minx = float(ring_mins[ri, 0])
            maxx = float(ring_maxs[ri, 0])
            miny = float(ring_mins[ri, 1])
            maxy = float(ring_maxs[ri, 1])

            inside_possible = x >= minx and x <= maxx and y >= miny and y <= maxy
            if not inside_possible:
                dx0 = minx - x if x < minx else (x - maxx if x > maxx else 0.0)
                dy0 = miny - y if y < miny else (y - maxy if y > maxy else 0.0)
                if dx0 * dx0 + dy0 * dy0 >= min_ds:
                    continue

            inside = 0
            for k in range(s, e - 1):
                ax = float(ring_vertices[k, 0])
                ay = float(ring_vertices[k, 1])
                bx = float(ring_vertices[k + 1, 0])
                by = float(ring_vertices[k + 1, 1])

                dx = bx - ax
                dy = by - ay
                denom = dx * dx + dy * dy
                if denom <= 0.0:
                    cx = ax
                    cy = ay
                    ds = (x - ax) * (x - ax) + (y - ay) * (y - ay)
                else:
                    t = ((x - ax) * dx + (y - ay) * dy) / denom
                    if t < 0.0:
                        t = 0.0
                    elif t > 1.0:
                        t = 1.0
                    cx = ax + t * dx
                    cy = ay + t * dy
                    ds = (x - cx) * (x - cx) + (y - cy) * (y - cy)

                if ds < min_ds:
                    min_ds = ds
                    qx = cx
                    qy = cy

                if inside_possible and ((ay > y) != (by > y)):
                    x_int = ax + (y - ay) * (bx - ax) / (by - ay)
                    if x < x_int:
                        inside ^= 1

            inside_parity ^= inside

        dist = math.sqrt(min_ds)
        if dist > 1e-12:
            gx = (x - qx) / dist
            gy = (y - qy) / dist
        else:
            gx = 0.0
            gy = 0.0

        d = dist
        if inside_parity != 0:
            d = -dist
            gx = -gx
            gy = -gy

        out_d[i] = d
        out_gx[i] = gx
        out_gy[i] = gy

    return out_d, out_gx, out_gy


def _insert_points_ring_xy(points: np.ndarray, target_spacing: float) -> np.ndarray:
    n = int(points.shape[0])
    if n < 3:
        return points
    if n >= _MAX_POINTS_PER_RING:
        return points

    spacing = float(target_spacing)
    if not np.isfinite(spacing) or spacing <= 0.0:
        return points

    nxt = np.roll(points, -1, axis=0)
    d = nxt - points
    dists = np.sqrt(d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1])
    desired = np.ceil(dists / spacing).astype(np.int64, copy=False)
    desired = np.maximum(desired, 1)

    total = int(np.sum(desired))
    if total == n:
        return points
    if total > _MAX_POINTS_PER_RING:
        extra_allowed = _MAX_POINTS_PER_RING - n
        segments = np.ones((n,), dtype=np.int64)
        for i in range(n):
            extra = int(desired[i]) - 1
            if extra <= 0:
                continue
            take = extra if extra <= extra_allowed else extra_allowed
            segments[i] = 1 + int(take)
            extra_allowed -= int(take)
            if extra_allowed <= 0:
                break
    else:
        segments = desired

    out_n = int(np.sum(segments))
    out = np.empty((out_n, 2), dtype=points.dtype)
    cursor = 0
    for i in range(n):
        p = points[i]
        q = points[i + 1] if i + 1 < n else points[0]
        seg = int(segments[i])
        out[cursor] = p
        cursor += 1
        for k in range(1, seg):
            t = float(k) / float(seg)
            out[cursor, 0] = p[0] * (1.0 - t) + q[0] * t
            out[cursor, 1] = p[1] * (1.0 - t) + q[1] * t
            cursor += 1
    return out


def _bounded_spatial_hash_grid(
    points_xy: np.ndarray,
    *,
    requested_cell: float,
    max_cells: int,
) -> tuple[float, int, int, int]:
    """draft の spatial hash を確保前に上限内へ決定的に粗粒化する。"""

    cell = float(requested_cell)
    limit = max(1, int(max_cells))
    min_x = float(np.min(points_xy[:, 0]))
    max_x = float(np.max(points_xy[:, 0]))
    min_y = float(np.min(points_xy[:, 1]))
    max_y = float(np.max(points_xy[:, 1]))
    span_x = max(0.0, max_x - min_x)
    span_y = max(0.0, max_y - min_y)

    def shape(candidate: float) -> tuple[int, int, int]:
        width = max(1, int(math.floor(span_x / candidate)) + 1)
        height = max(1, int(math.floor(span_y / candidate)) + 1)
        return width, height, width * height

    requested_width, requested_height, requested_cells = shape(cell)
    if requested_cells <= limit:
        return cell, requested_width, requested_height, requested_cells

    low = cell
    high = max(
        cell,
        max(span_x, span_y) / math.sqrt(float(limit)),
    )
    high_width, high_height, high_cells = shape(high)
    while high_cells > limit:
        high *= 2.0
        high_width, high_height, high_cells = shape(high)

    for _ in range(64):
        middle = low + 0.5 * (high - low)
        if middle == low or middle == high:
            break
        middle_width, middle_height, middle_cells = shape(middle)
        if middle_cells > limit:
            low = middle
        else:
            high = middle
            high_width = middle_width
            high_height = middle_height

    return high, high_width, high_height, requested_cells


@njit(cache=True, fastmath=True)
def _compute_forces_numba(
    points_xy: np.ndarray,
    next_idx: np.ndarray,
    prev_idx: np.ndarray,
    target_spacing: float,
    repel_strength: float,
    repel_radius: float,
    grid_cell_size: float = 0.0,
    bounded_grid_width: int = 0,
    bounded_grid_height: int = 0,
) -> np.ndarray:
    n = int(points_xy.shape[0])
    forces = np.zeros((n, 2), dtype=np.float64)

    # 隣接スプリング（目標間隔へ寄せる）
    for i in range(n):
        j = int(next_idx[i])
        dx = float(points_xy[j, 0] - points_xy[i, 0])
        dy = float(points_xy[j, 1] - points_xy[i, 1])
        d2 = dx * dx + dy * dy
        if d2 <= 1e-12:
            continue
        dist = math.sqrt(d2)
        inv = 1.0 / dist
        dirx = dx * inv
        diry = dy * inv
        delta = dist - float(target_spacing)
        fx = dirx * delta
        fy = diry * delta
        forces[i, 0] += fx
        forces[i, 1] += fy
        forces[j, 0] -= fx
        forces[j, 1] -= fy

    if repel_strength <= 0.0 or repel_radius <= 0.0 or n < 2:
        return forces

    # グリッド分割（cell size = repel_radius）
    minx = float(points_xy[0, 0])
    maxx = float(points_xy[0, 0])
    miny = float(points_xy[0, 1])
    maxy = float(points_xy[0, 1])
    for i in range(1, n):
        x = float(points_xy[i, 0])
        y = float(points_xy[i, 1])
        if x < minx:
            minx = x
        if x > maxx:
            maxx = x
        if y < miny:
            miny = y
        if y > maxy:
            maxy = y

    cell = (
        float(grid_cell_size)
        if float(grid_cell_size) > 0.0
        else float(repel_radius)
    )
    if cell <= 0.0:
        return forces

    grid_w = int(bounded_grid_width)
    grid_h = int(bounded_grid_height)
    if grid_w <= 0:
        grid_w = int(math.floor((maxx - minx) / cell)) + 1
    if grid_h <= 0:
        grid_h = int(math.floor((maxy - miny) / cell)) + 1
    if grid_w < 1:
        grid_w = 1
    if grid_h < 1:
        grid_h = 1

    n_cells = int(grid_w * grid_h)
    head = np.full((n_cells,), -1, dtype=np.int32)
    nxt = np.empty((n,), dtype=np.int32)

    for i in range(n):
        cx = int((float(points_xy[i, 0]) - minx) / cell)
        cy = int((float(points_xy[i, 1]) - miny) / cell)
        if cx < 0:
            cx = 0
        if cy < 0:
            cy = 0
        if cx >= grid_w:
            cx = grid_w - 1
        if cy >= grid_h:
            cy = grid_h - 1
        ci = int(cx + cy * grid_w)
        nxt[i] = head[ci]
        head[ci] = np.int32(i)

    r2 = float(repel_radius) * float(repel_radius)

    for i in range(n):
        xi = float(points_xy[i, 0])
        yi = float(points_xy[i, 1])
        cx = int((xi - minx) / cell)
        cy = int((yi - miny) / cell)
        if cx < 0:
            cx = 0
        if cy < 0:
            cy = 0
        if cx >= grid_w:
            cx = grid_w - 1
        if cy >= grid_h:
            cy = grid_h - 1

        for oy in range(-1, 2):
            ny = cy + oy
            if ny < 0 or ny >= grid_h:
                continue
            for ox in range(-1, 2):
                nx = cx + ox
                if nx < 0 or nx >= grid_w:
                    continue
                ci = int(nx + ny * grid_w)
                j = int(head[ci])
                while j != -1:
                    if j > i:
                        if j == int(prev_idx[i]) or j == int(next_idx[i]):
                            j = int(nxt[j])
                            continue
                        if i == int(prev_idx[j]) or i == int(next_idx[j]):
                            j = int(nxt[j])
                            continue

                        dx = xi - float(points_xy[j, 0])
                        dy = yi - float(points_xy[j, 1])
                        d2 = dx * dx + dy * dy
                        if d2 > 1e-12 and d2 < r2:
                            dist = math.sqrt(d2)
                            inv = 1.0 / dist
                            w = (float(repel_radius) - dist) / float(repel_radius)
                            fx = dx * inv * w * float(repel_strength)
                            fy = dy * inv * w * float(repel_strength)
                            forces[i, 0] += fx
                            forces[i, 1] += fy
                            forces[j, 0] -= fx
                            forces[j, 1] -= fy

                    j = int(nxt[j])

    # 1 点あたりの最大力を抑える（発散回避）
    max_force = float(target_spacing) * 5.0
    if max_force > 0.0:
        max_f2 = max_force * max_force
        for i in range(n):
            fx = float(forces[i, 0])
            fy = float(forces[i, 1])
            f2 = fx * fx + fy * fy
            if f2 > max_f2:
                inv = max_force / math.sqrt(f2)
                forces[i, 0] = fx * inv
                forces[i, 1] = fy * inv

    return forces


@njit(cache=True)
def _apply_boundary_constraints_numba(
    points_xy: np.ndarray,
    disp_xy: np.ndarray,
    d: np.ndarray,
    gx: np.ndarray,
    gy: np.ndarray,
    target_spacing: float,
    boundary_avoid: float,
    boundary_mode: int,  # 0 slide / 1 bounce
) -> np.ndarray:
    n = int(points_xy.shape[0])
    out = np.empty_like(points_xy)

    spacing = float(target_spacing)
    eps = 1e-3
    margin = spacing * 2.0

    for i in range(n):
        nx = float(gx[i])
        ny = float(gy[i])
        di = float(d[i])

        dx = float(disp_xy[i, 0])
        dy = float(disp_xy[i, 1])

        if nx == 0.0 and ny == 0.0:
            out[i, 0] = float(points_xy[i, 0]) + dx
            out[i, 1] = float(points_xy[i, 1]) + dy
            continue

        # 既に外側なら、まず境界の内側へ押し戻す
        if di >= 0.0:
            dx -= (di + eps) * nx
            dy -= (di + eps) * ny
            di = -eps

        # 境界近傍でのふるまい（slide / bounce）
        if margin > 0.0 and di > -margin:
            out_comp = dx * nx + dy * ny
            if out_comp > 0.0:
                if boundary_mode == 0:
                    dx -= out_comp * nx
                    dy -= out_comp * ny
                else:
                    dx -= 2.0 * out_comp * nx
                    dy -= 2.0 * out_comp * ny

            t = (di + margin) / margin  # 0..1
            if t > 0.0 and boundary_avoid > 0.0 and spacing > 0.0:
                push = float(boundary_avoid) * t * spacing * float(_BOUNDARY_PUSH_GAIN)
                dx -= push * nx
                dy -= push * ny

        # 走り抜け防止：外向き成分を「距離以内」に制限
        out_comp = dx * nx + dy * ny
        allowed = -di - eps
        if allowed < 0.0:
            allowed = 0.0
        if out_comp > allowed:
            adj = out_comp - allowed
            dx -= adj * nx
            dy -= adj * ny

        out[i, 0] = float(points_xy[i, 0]) + dx
        out[i, 1] = float(points_xy[i, 1]) + dy

    return out


def _build_prev_next(n_points: int, ring_offsets: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    prev_idx = np.arange(-1, n_points - 1, dtype=np.int32)
    next_idx = np.arange(1, n_points + 1, dtype=np.int32)
    for ri in range(int(ring_offsets.shape[0]) - 1):
        s = int(ring_offsets[ri])
        e = int(ring_offsets[ri + 1])
        if e - s < 3:
            continue
        prev_idx[s] = np.int32(e - 1)
        next_idx[e - 1] = np.int32(s)
    return prev_idx, next_idx


def _make_seed_ring_xy(
    rng: np.random.Generator,
    center_xy: np.ndarray,
    *,
    target_spacing: float,
) -> np.ndarray:
    spacing = float(target_spacing)
    r0 = spacing * 2.0
    circumference = 2.0 * math.pi * r0
    n = int(math.ceil(circumference / max(spacing, 1e-6)))
    n = max(8, min(64, n))

    theta0 = float(rng.uniform(0.0, 2.0 * math.pi))
    angles = theta0 + (2.0 * math.pi) * (np.arange(n, dtype=np.float64) / float(n))

    jitter = float(spacing) * 0.08
    radial = r0 + rng.normal(0.0, jitter, size=(n,))

    x = float(center_xy[0]) + radial * np.cos(angles)
    y = float(center_xy[1]) + radial * np.sin(angles)
    return np.stack([x, y], axis=1).astype(np.float64, copy=False)


def _sample_seed_centers_xy(
    rng: np.random.Generator,
    *,
    seed_count: int,
    bbox_min: np.ndarray,
    bbox_max: np.ndarray,
    ring_vertices: np.ndarray,
    ring_offsets: np.ndarray,
    ring_mins: np.ndarray,
    ring_maxs: np.ndarray,
    min_margin: float,
) -> list[np.ndarray]:
    out: list[np.ndarray] = []
    remaining = int(seed_count)
    if remaining <= 0:
        return out

    bx0 = float(bbox_min[0])
    by0 = float(bbox_min[1])
    bx1 = float(bbox_max[0])
    by1 = float(bbox_max[1])

    if not np.isfinite(bx0) or not np.isfinite(by0) or not np.isfinite(bx1) or not np.isfinite(by1):
        return out

    for _attempt in range(20):
        if remaining <= 0:
            break
        batch = max(256, remaining * 64)
        xs = rng.uniform(bx0, bx1, size=(batch,))
        ys = rng.uniform(by0, by1, size=(batch,))
        pts = np.stack([xs, ys], axis=1).astype(np.float64, copy=False)
        d, _gx, _gy = _evaluate_growth_sdf_points_numba(
            pts, ring_vertices, ring_offsets, ring_mins, ring_maxs
        )
        for i in range(batch):
            if float(d[i]) < -float(min_margin):
                out.append(pts[i].copy())
                remaining -= 1
                if remaining <= 0:
                    break

    return out


def _simulate_growth_in_mask_xy(
    rings_xy: list[np.ndarray],
    *,
    target_spacing: float,
    boundary_avoid: float,
    boundary_mode: str,
    iters: int,
    sdf: np.ndarray,
    sdf_origin_x: float,
    sdf_origin_y: float,
    sdf_pitch: float,
    max_total_points: int | None = None,
    max_force_grid_cells: int | None = None,
) -> _GrowthSimulationResult:
    spacing = float(target_spacing)
    if not np.isfinite(spacing) or spacing <= 0.0:
        return _GrowthSimulationResult([], 0, 0, False, None)

    iters_i = int(iters)
    if iters_i < 0:
        iters_i = 0
    if iters_i > _MAX_ITERS:
        iters_i = _MAX_ITERS

    if iters_i == 0 or not rings_xy:
        initial_points = int(sum(int(r.shape[0]) for r in rings_xy))
        return _GrowthSimulationResult(rings_xy, 0, initial_points, False, None)

    mode_s = str(boundary_mode)
    if mode_s not in {"slide", "bounce"}:
        return _GrowthSimulationResult([], 0, 0, False, None)
    mode_i = 0 if mode_s == "slide" else 1

    avoid_f = float(boundary_avoid)
    if not np.isfinite(avoid_f) or avoid_f < 0.0:
        avoid_f = 0.0

    repel_radius = spacing * 2.0
    repel_strength = 1.0
    step = 0.15

    rings = [r.astype(np.float64, copy=True) for r in rings_xy]
    point_limit = (
        _MAX_TOTAL_POINTS
        if max_total_points is None
        else max(1, min(int(max_total_points), _MAX_TOTAL_POINTS))
    )
    executed_iterations = 0
    max_points_observed = int(sum(int(r.shape[0]) for r in rings))
    point_budget_hit = max_total_points is not None and max_points_observed > point_limit
    rejected_total_points: int | None = (
        max_points_observed if point_budget_hit else None
    )
    force_grid_requested_cells = 0
    force_grid_effective_cells = 0

    for _it in range(iters_i):
        total_points = int(sum(int(r.shape[0]) for r in rings))
        if total_points <= 0:
            return _GrowthSimulationResult(
                [],
                executed_iterations,
                max_points_observed,
                point_budget_hit,
                rejected_total_points,
                force_grid_requested_cells,
                force_grid_effective_cells,
            )

        if total_points < point_limit:
            expanded_rings = [_insert_points_ring_xy(r, spacing) for r in rings]
            expanded_points = int(sum(int(r.shape[0]) for r in expanded_rings))
            if max_total_points is None or expanded_points <= point_limit:
                rings = expanded_rings
            else:
                point_budget_hit = True
                rejected_total_points = max(
                    expanded_points,
                    0 if rejected_total_points is None else rejected_total_points,
                )

        total_points = int(sum(int(r.shape[0]) for r in rings))
        if total_points <= 0:
            return _GrowthSimulationResult(
                [],
                executed_iterations,
                max_points_observed,
                point_budget_hit,
                rejected_total_points,
                force_grid_requested_cells,
                force_grid_effective_cells,
            )
        max_points_observed = max(max_points_observed, total_points)

        # flatten
        points = np.empty((total_points, 2), dtype=np.float64)
        roff = np.zeros((len(rings) + 1,), dtype=np.int32)
        cursor = 0
        for i, ring in enumerate(rings):
            m = int(ring.shape[0])
            points[cursor : cursor + m] = ring
            cursor += m
            roff[i + 1] = np.int32(cursor)

        prev_idx, next_idx = _build_prev_next(total_points, roff)

        if max_force_grid_cells is None:
            forces = _compute_forces_numba(
                points,
                next_idx=next_idx,
                prev_idx=prev_idx,
                target_spacing=spacing,
                repel_strength=repel_strength,
                repel_radius=repel_radius,
            )
        else:
            (
                grid_cell_size,
                grid_width,
                grid_height,
                requested_grid_cells,
            ) = _bounded_spatial_hash_grid(
                points,
                requested_cell=repel_radius,
                max_cells=max_force_grid_cells,
            )
            force_grid_requested_cells = max(
                force_grid_requested_cells,
                requested_grid_cells,
            )
            force_grid_effective_cells = max(
                force_grid_effective_cells,
                grid_width * grid_height,
            )
            forces = _compute_forces_numba(
                points,
                next_idx=next_idx,
                prev_idx=prev_idx,
                target_spacing=spacing,
                repel_strength=repel_strength,
                repel_radius=repel_radius,
                grid_cell_size=grid_cell_size,
                bounded_grid_width=grid_width,
                bounded_grid_height=grid_height,
            )

        disp = forces * float(step)

        d, gx, gy = _sample_sdf_grid_numba(
            points, sdf, float(sdf_origin_x), float(sdf_origin_y), float(sdf_pitch)
        )
        points = _apply_boundary_constraints_numba(
            points,
            disp,
            d,
            gx,
            gy,
            target_spacing=spacing,
            boundary_avoid=avoid_f,
            boundary_mode=mode_i,
        )

        # scatter
        out_rings: list[np.ndarray] = []
        for ri in range(int(roff.shape[0]) - 1):
            s = int(roff[ri])
            e = int(roff[ri + 1])
            if e - s >= 3:
                out_rings.append(points[s:e].copy())
        rings = out_rings
        executed_iterations += 1

    return _GrowthSimulationResult(
        rings,
        executed_iterations,
        max_points_observed,
        point_budget_hit,
        rejected_total_points,
        force_grid_requested_cells,
        force_grid_effective_cells,
    )


@effect(meta=growth_meta, n_inputs=1)
def growth(
    mask: GeomTuple,
    *,
    seed_count: int = 12,
    target_spacing: float = 2.0,
    boundary_avoid: float = 1.0,
    boundary_mode: str = "slide",  # "slide" | "bounce"
    iters: int = 250,
    seed: int = 0,
    show_mask: bool = False,
) -> GeomTuple:
    """マスク内で差分成長を行い、襞のような閉曲線群を生成する。

    Parameters
    ----------
    mask : tuple[np.ndarray, np.ndarray]
        閉曲線マスク（リング列）を想定する入力（coords, offsets）。
    seed_count : int, default 12
        0 以上の、マスク内へ配置する seed（初期ループ）数。
    target_spacing : float, default 2.0
        正の目標点間隔 [mm]。再分割と力のスケールに用いる。
    boundary_avoid : float, default 1.0
        境界近傍で内側へ押し戻す強さ（0 で無効）。
    boundary_mode : str, default "slide"
        `"slide"` は境界で外向き成分を除去し、沿って流れる。
        `"bounce"` は境界で外向き成分を反射し、跳ね返る。
    iters : int, default 250
        0 以上の反復回数。0 の場合は（seed_count>0 でも）生成せず empty を返す。
    seed : int, default 0
        0 以上の乱数 seed（seed 配置の再現性のため）。
    show_mask : bool, default False
        True のとき、出力に入力 mask を追加で含める。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        生成された閉曲線群（coords, offsets）。
        `show_mask=True` の場合は mask も含む。
        入力が不正（リング抽出できない/非平面など）の場合は empty。

    Raises
    ------
    ValueError
        seed、seed 数、反復回数、境界回避強度が負、または目標点間隔が 0 以下の場合。
    """
    if seed < 0:
        raise ValueError("growth の seed は 0 以上である必要がある")
    if target_spacing <= 0.0:
        raise ValueError("growth の target_spacing は 0 より大きい必要がある")
    if seed_count < 0:
        raise ValueError("growth の seed_count は 0 以上である必要がある")
    if iters < 0:
        raise ValueError("growth の iters は 0 以上である必要がある")
    if boundary_avoid < 0.0:
        raise ValueError("growth の boundary_avoid は 0 以上である必要がある")

    mask_coords, mask_offsets = mask
    if mask_coords.shape[0] == 0:
        emit_operation_diagnostic(
            op="growth.mask",
            original_value="empty",
            effective_value="empty_output",
            reason="growth requires a non-empty closed planar mask",
        )
        return empty_packed_geometry()

    frame = PlanarFrame.from_points(mask_coords, mask_offsets)
    if not frame.valid:
        emit_operation_diagnostic(
            op="growth.mask",
            original_value="invalid_planar_frame",
            effective_value="empty_output",
            reason="growth requires a closed mask with a well-defined plane",
        )
        return empty_packed_geometry()
    if not frame.is_planar(planarity_threshold(mask_coords)):
        emit_operation_diagnostic(
            op="growth.mask",
            original_value="nonplanar",
            effective_value="empty_output",
            reason="growth requires a planar mask",
        )
        return empty_packed_geometry()

    aligned_mask = frame.to_local(mask_coords)

    rings = extract_planar_rings(
        aligned_mask,
        mask_offsets,
        auto_close_threshold=float(_AUTO_CLOSE_THRESHOLD_DEFAULT),
    )
    if not rings:
        emit_operation_diagnostic(
            op="growth.mask",
            original_value="no_closed_rings",
            effective_value="empty_output",
            reason="growth could not extract a closed ring from the mask",
        )
        return empty_packed_geometry()

    spacing = target_spacing

    step_sdf = max(spacing, 0.5)
    rings = _simplify_rings_for_sdf(rings, step_sdf=step_sdf)

    ring_vertices, ring_offsets, ring_mins, ring_maxs = pack_planar_rings(rings)

    sdf_pad = max(spacing * 6.0, 2.0)
    sdf, sdf_origin_x, sdf_origin_y, sdf_pitch = _build_sdf_grid(
        ring_vertices,
        ring_offsets,
        ring_mins,
        ring_maxs,
        pitch_hint=step_sdf,
        pad=sdf_pad,
        max_points=_MAX_SDF_GRID_POINTS,
    )

    bbox_min = np.min(ring_mins, axis=0)
    bbox_max = np.max(ring_maxs, axis=0)

    seed_count_i = seed_count

    requested_iters = iters
    iters_i = requested_iters
    if iters_i > _MAX_ITERS:
        iters_i = _MAX_ITERS
    if iters_i != requested_iters:
        emit_operation_diagnostic(
            op="growth.iters",
            original_value=requested_iters,
            effective_value=iters_i,
            reason="growth iterations was clamped to the supported range",
        )
    quality = current_preview_quality()
    if quality == "draft" and iters_i > DRAFT_MAX_ITERS:
        before_draft_cap = iters_i
        iters_i = DRAFT_MAX_ITERS
        emit_operation_diagnostic(
            op="growth.iters",
            original_value=before_draft_cap,
            effective_value=iters_i,
            reason=(
                "draft preview capped simulation iterations; final capture keeps "
                "the requested value"
            ),
            severity="info",
        )

    effective_boundary_avoid = boundary_avoid

    boundary_mode_s = boundary_mode

    if seed_count_i == 0 or iters_i == 0:
        out = empty_packed_geometry()
        return concat_geom_tuples(out, mask) if show_mask else out

    rng = np.random.default_rng(seed)
    centers = _sample_seed_centers_xy(
        rng,
        seed_count=seed_count_i,
        bbox_min=bbox_min,
        bbox_max=bbox_max,
        ring_vertices=ring_vertices,
        ring_offsets=ring_offsets,
        ring_mins=ring_mins,
        ring_maxs=ring_maxs,
        min_margin=spacing * 2.0,
    )
    if len(centers) < seed_count_i:
        centers.extend(
            _sample_seed_centers_xy(
                rng,
                seed_count=seed_count_i - len(centers),
                bbox_min=bbox_min,
                bbox_max=bbox_max,
                ring_vertices=ring_vertices,
                ring_offsets=ring_offsets,
                ring_mins=ring_mins,
                ring_maxs=ring_maxs,
                min_margin=0.0,
            )
        )
    if len(centers) < seed_count_i:
        emit_operation_diagnostic(
            op="growth.seed_count",
            original_value=seed_count_i,
            effective_value=len(centers),
            reason="the mask could not accommodate every requested seed",
        )

    rings_xy: list[np.ndarray] = []
    for c in centers:
        rings_xy.append(_make_seed_ring_xy(rng, c, target_spacing=spacing))

    simulation = _simulate_growth_in_mask_xy(
        rings_xy,
        target_spacing=spacing,
        boundary_avoid=effective_boundary_avoid,
        boundary_mode=boundary_mode_s,
        iters=iters_i,
        sdf=sdf,
        sdf_origin_x=sdf_origin_x,
        sdf_origin_y=sdf_origin_y,
        sdf_pitch=sdf_pitch,
        max_total_points=(DRAFT_MAX_TOTAL_POINTS if quality == "draft" else None),
        max_force_grid_cells=(
            DRAFT_MAX_FORCE_GRID_CELLS if quality == "draft" else None
        ),
    )
    if simulation.point_budget_hit:
        emit_operation_diagnostic(
            op="growth.total_points",
            original_value=simulation.rejected_total_points,
            effective_value=simulation.max_total_points,
            reason=(
                "draft preview stopped point insertion at the total-point budget; "
                "final capture keeps the full simulation"
            ),
            severity="info",
        )
    if (
        simulation.force_grid_requested_cells
        > simulation.force_grid_effective_cells
    ):
        emit_operation_diagnostic(
            op="growth.force_grid_cells",
            original_value=simulation.force_grid_requested_cells,
            effective_value=simulation.force_grid_effective_cells,
            reason=(
                "draft preview coarsened the repulsion spatial hash before "
                "allocation; final capture keeps the requested spacing"
            ),
            severity="info",
        )
    out_rings_xy = simulation.rings

    lines_out: list[np.ndarray] = []
    for ring_xy in out_rings_xy:
        if ring_xy.shape[0] < 3:
            continue
        pts3 = np.empty((int(ring_xy.shape[0]), 3), dtype=np.float64)
        pts3[:, 0:2] = ring_xy
        pts3[:, 2] = 0.0
        back = frame.to_world(pts3)
        closed = np.concatenate([back, back[:1]], axis=0)
        lines_out.append(closed.astype(np.float32, copy=False))

    if len(lines_out) < len(out_rings_xy):
        emit_operation_diagnostic(
            op="growth.output",
            original_value=len(out_rings_xy),
            effective_value=len(lines_out),
            reason="degenerate grown rings were omitted from the output",
        )

    out = pack_polylines(lines_out)
    if show_mask:
        out = concat_geom_tuples(out, mask)
    return out


__all__ = ["growth", "growth_meta"]
