"""
Purpose:
    平面閉mask内でGray-Scott反応拡散を進め、濃度等値線を閉曲線として生成する。
Use when:
    simulation初期条件、mask境界条件、contour抽出、またはpreview workを変更する場合。
Constraints:
    - mask ring群をeven-odd領域として扱い、非平面・空領域・grid拒否ではemptyを返す。
    - 初期noiseをseedで決定し、出力contourのclosureと元のPlanarFrameを維持する。
    - draftのgrid coarsen・step capは診断し、final評価の要求pitch・stepsへ持ち込まない。
"""

from __future__ import annotations

import numpy as np
from numba import get_num_threads, njit, prange  # type: ignore[attr-defined, import-untyped]

from grafix.core.operation_authoring import effect
from grafix.core.operation_diagnostics import (
    emit_operation_diagnostic,
    grid_spec_from_bbox_with_diagnostic,
)
from grafix.core.parameters.meta import ParamMeta
from grafix.core.preview_quality import current_preview_quality
from grafix.core.realized_geometry import GeomTuple

from grafix.core.geometry_kernels.grid import DEFAULT_MAX_GRID_POINTS
from grafix.core.geometry_kernels.marching import marching_squares_loops
from grafix.core.geometry_kernels.packed import (
    empty_packed_geometry,
    pack_polylines,
)
from grafix.core.geometry_kernels.planar import (
    PlanarFrame,
    planarity_threshold,
)
from grafix.core.geometry_kernels.raster import (
    scanline_evenodd_mask,
    squared_euclidean_distance_transform,
)

MAX_GRID_POINTS = DEFAULT_MAX_GRID_POINTS
DRAFT_MAX_GRID_POINTS = 262_144
DRAFT_MAX_STEPS = 600
DRAFT_MAX_POINT_STEPS = 14_000_000
_PARALLEL_MIN_GRID_POINTS = 65_536
_PARALLEL_MIN_STEPS = 8
_BOUNDARY_CHOICES = ("noflux", "dirichlet")


reaction_diffusion_meta = {
    "grid_pitch": ParamMeta(
        kind="float",
        ui_min=0.2,
        ui_max=2.0,
        description="反応拡散を計算する二次元グリッドの間隔。",
    ),
    "steps": ParamMeta(
        kind="int",
        ui_min=0,
        ui_max=10_000,
        description="Gray-Scott 反応拡散モデルを更新する反復回数。",
    ),
    "du": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=1.0,
        description="Gray-Scott モデルの U 成分に適用する拡散係数。",
    ),
    "dv": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=1.0,
        description="Gray-Scott モデルの V 成分に適用する拡散係数。",
    ),
    "feed": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=0.1,
        description="Gray-Scott モデルへ U 成分を供給する反応係数。",
    ),
    "kill": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=0.1,
        description="Gray-Scott モデルから V 成分を除去する反応係数。",
    ),
    "dt": ParamMeta(
        kind="float",
        ui_min=0.1,
        ui_max=2.0,
        description="反応と拡散を数値積分するときの時間刻み。",
    ),
    "seed": ParamMeta(
        kind="int",
        ui_min=0,
        ui_max=9999,
        description="初期濃度分布を再現可能にする乱数シード。",
    ),
    "seed_radius": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=200.0,
        description="初期状態で中心に配置する V 成分ブロブの半径。",
    ),
    "noise": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=0.1,
        description="初期状態の V 成分へ加える一様ノイズの最大量。",
    ),
    "level": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=1.0,
        description="計算後の V 成分から輪郭を抽出する等値レベル。",
    ),
    "min_points": ParamMeta(
        kind="int",
        ui_min=4,
        ui_max=200,
        description="抽出した輪郭を出力対象とする最小頂点数。",
    ),
    "boundary": ParamMeta(
        kind="choice",
        choices=_BOUNDARY_CHOICES,
        description="マスク境界を濃度勾配なし、または外側濃度固定として扱う。",
    ),
}


def _pack_mask_rings_xy(
    aligned_mask: np.ndarray, offsets: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    rings: list[np.ndarray] = []
    for i in range(int(offsets.size) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        if e - s < 3:
            continue
        pts = aligned_mask[s:e, 0:2]
        if pts.shape[0] >= 3:
            values = pts.astype(np.float64, copy=False)
            if not np.all(values[0] == values[-1]):
                values = np.concatenate([values, values[:1]], axis=0)
            rings.append(values)

    if not rings:
        return None

    total = 0
    for pts in rings:
        total += int(pts.shape[0])

    vertices = np.empty((total, 2), dtype=np.float64)
    ring_offsets = np.empty((len(rings) + 1,), dtype=np.int32)
    ring_mins = np.empty((len(rings), 2), dtype=np.float64)
    ring_maxs = np.empty((len(rings), 2), dtype=np.float64)
    ring_offsets[0] = 0
    cursor = 0
    for i, pts in enumerate(rings):
        n = int(pts.shape[0])
        vertices[cursor : cursor + n] = pts
        cursor += n
        ring_offsets[i + 1] = np.int32(cursor)
        ring_mins[i] = np.min(pts, axis=0)
        ring_maxs[i] = np.max(pts, axis=0)

    return vertices, ring_offsets, ring_mins, ring_maxs



@njit(cache=True)
def _gray_scott_simulate_masked_serial(
    u0: np.ndarray,
    v0: np.ndarray,
    mask: np.ndarray,
    *,
    steps: int,
    du: float,
    dv: float,
    feed: float,
    kill: float,
    dt: float,
    boundary: int,  # 0: noflux, 1: dirichlet
) -> np.ndarray:
    ny = int(u0.shape[0])
    nx = int(u0.shape[1])
    u = u0.copy()
    v = v0.copy()
    u2 = np.empty_like(u)
    v2 = np.empty_like(v)

    u_out = 1.0
    v_out = 0.0

    if steps > 0:
        for j in range(ny):
            for i in range(nx):
                if mask[j, i] != 0:
                    continue
                u[j, i] = u_out
                v[j, i] = v_out
                u2[j, i] = u_out
                v2[j, i] = v_out

    for _ in range(steps):
        for j in range(ny):
            for i in range(nx):
                if mask[j, i] == 0:
                    continue

                uc = float(u[j, i])
                vc = float(v[j, i])

                # up
                if j - 1 < 0 or mask[j - 1, i] == 0:
                    uu = uc if boundary == 0 else u_out
                    vu = vc if boundary == 0 else v_out
                else:
                    uu = float(u[j - 1, i])
                    vu = float(v[j - 1, i])

                # down
                if j + 1 >= ny or mask[j + 1, i] == 0:
                    ud = uc if boundary == 0 else u_out
                    vd = vc if boundary == 0 else v_out
                else:
                    ud = float(u[j + 1, i])
                    vd = float(v[j + 1, i])

                # left
                if i - 1 < 0 or mask[j, i - 1] == 0:
                    ul = uc if boundary == 0 else u_out
                    vl = vc if boundary == 0 else v_out
                else:
                    ul = float(u[j, i - 1])
                    vl = float(v[j, i - 1])

                # right
                if i + 1 >= nx or mask[j, i + 1] == 0:
                    ur = uc if boundary == 0 else u_out
                    vr = vc if boundary == 0 else v_out
                else:
                    ur = float(u[j, i + 1])
                    vr = float(v[j, i + 1])

                lap_u = (uu + ud + ul + ur) - 4.0 * uc
                lap_v = (vu + vd + vl + vr) - 4.0 * vc

                uvv = uc * vc * vc
                du_term = float(du) * lap_u - uvv + float(feed) * (1.0 - uc)
                dv_term = float(dv) * lap_v + uvv - (float(feed) + float(kill)) * vc

                un = uc + du_term * float(dt)
                vn = vc + dv_term * float(dt)

                if un < 0.0:
                    un = 0.0
                elif un > 1.0:
                    un = 1.0
                if vn < 0.0:
                    vn = 0.0
                elif vn > 1.0:
                    vn = 1.0

                u2[j, i] = un
                v2[j, i] = vn

        u, u2 = u2, u
        v, v2 = v2, v

    return v


@njit(cache=True, parallel=True)
def _gray_scott_simulate_masked_parallel(
    u0: np.ndarray,
    v0: np.ndarray,
    mask: np.ndarray,
    *,
    steps: int,
    du: float,
    dv: float,
    feed: float,
    kill: float,
    dt: float,
    boundary: int,  # 0: noflux, 1: dirichlet
) -> np.ndarray:
    ny = int(u0.shape[0])
    nx = int(u0.shape[1])
    u = u0.copy()
    v = v0.copy()
    if steps <= 0:
        return v
    u2 = np.empty_like(u)
    v2 = np.empty_like(v)

    u_out = 1.0
    v_out = 0.0

    # active と 4 近傍の状態は step 間で不変なので、mask 走査を最初の 1 回に集約する。
    neighbor_flags = np.zeros((ny, nx), dtype=np.uint8)
    for j in range(ny):
        for i in range(nx):
            if mask[j, i] == 0:
                u[j, i] = u_out
                v[j, i] = v_out
                u2[j, i] = u_out
                v2[j, i] = v_out
                continue
            flags = 16
            if j - 1 >= 0 and mask[j - 1, i] != 0:
                flags |= 1
            if j + 1 < ny and mask[j + 1, i] != 0:
                flags |= 2
            if i - 1 >= 0 and mask[j, i - 1] != 0:
                flags |= 4
            if i + 1 < nx and mask[j, i + 1] != 0:
                flags |= 8
            neighbor_flags[j, i] = flags

    for _ in range(steps):
        # 各 cell は読み取り元とは別の ping-pong buffer だけへ書き込む。
        for j in prange(ny):
            for i in range(nx):
                flags = int(neighbor_flags[j, i])
                if flags & 16 == 0:
                    continue

                uc = float(u[j, i])
                vc = float(v[j, i])

                # up
                if flags & 1 == 0:
                    uu = uc if boundary == 0 else u_out
                    vu = vc if boundary == 0 else v_out
                else:
                    uu = float(u[j - 1, i])
                    vu = float(v[j - 1, i])

                # down
                if flags & 2 == 0:
                    ud = uc if boundary == 0 else u_out
                    vd = vc if boundary == 0 else v_out
                else:
                    ud = float(u[j + 1, i])
                    vd = float(v[j + 1, i])

                # left
                if flags & 4 == 0:
                    ul = uc if boundary == 0 else u_out
                    vl = vc if boundary == 0 else v_out
                else:
                    ul = float(u[j, i - 1])
                    vl = float(v[j, i - 1])

                # right
                if flags & 8 == 0:
                    ur = uc if boundary == 0 else u_out
                    vr = vc if boundary == 0 else v_out
                else:
                    ur = float(u[j, i + 1])
                    vr = float(v[j, i + 1])

                lap_u = (uu + ud + ul + ur) - 4.0 * uc
                lap_v = (vu + vd + vl + vr) - 4.0 * vc

                uvv = uc * vc * vc
                du_term = float(du) * lap_u - uvv + float(feed) * (1.0 - uc)
                dv_term = float(dv) * lap_v + uvv - (float(feed) + float(kill)) * vc

                un = uc + du_term * float(dt)
                vn = vc + dv_term * float(dt)

                if un < 0.0:
                    un = 0.0
                elif un > 1.0:
                    un = 1.0
                if vn < 0.0:
                    vn = 0.0
                elif vn > 1.0:
                    vn = 1.0

                u2[j, i] = un
                v2[j, i] = vn

        u, u2 = u2, u
        v, v2 = v2, v

    return v


def _gray_scott_simulate_masked(
    u0: np.ndarray,
    v0: np.ndarray,
    mask: np.ndarray,
    *,
    steps: int,
    du: float,
    dv: float,
    feed: float,
    kill: float,
    dt: float,
    boundary: int,
) -> np.ndarray:
    use_parallel = (
        get_num_threads() > 1
        and int(u0.shape[0]) >= 2
        and int(u0.size) >= _PARALLEL_MIN_GRID_POINTS
        and steps >= _PARALLEL_MIN_STEPS
        and bool(np.isfinite(u0).all())
        and bool(np.isfinite(v0).all())
    )
    simulate = (
        _gray_scott_simulate_masked_parallel
        if use_parallel
        else _gray_scott_simulate_masked_serial
    )
    return simulate(
        u0,
        v0,
        mask,
        steps=steps,
        du=du,
        dv=dv,
        feed=feed,
        kill=kill,
        dt=dt,
        boundary=boundary,
    )



@effect(meta=reaction_diffusion_meta, n_inputs=1)
def reaction_diffusion(
    mask: GeomTuple,
    *,
    grid_pitch: float = 0.6,
    steps: int = 4500,
    du: float = 0.16,
    dv: float = 0.08,
    feed: float = 0.035,
    kill: float = 0.062,
    dt: float = 1.0,
    seed: int = 0,
    seed_radius: float = 10.0,
    noise: float = 0.02,
    level: float = 0.2,
    min_points: int = 16,
    boundary: str = "noflux",  # "noflux" | "dirichlet"
) -> GeomTuple:
    """閉曲線マスク内で反応拡散を走らせ、線として出力する。

    Parameters
    ----------
    mask : tuple[np.ndarray, np.ndarray]
        閉曲線（複数可）からなるマスク（coords, offsets）。
    grid_pitch : float, default 0.6
        計算グリッドのピッチ（出力座標系の長さ単位）。
    steps : int, default 4500
        Gray-Scott の反復回数。
    du, dv : float, default 0.16, 0.08
        拡散係数。
    feed, kill : float, default 0.035, 0.062
        反応パラメータ。
    dt : float, default 1.0
        時間刻み。
    seed : int, default 0
        乱数シード（初期条件用）。
    seed_radius : float, default 10.0
        中心ブロブの半径（0 ならブロブ無し）。
    noise : float, default 0.02
        初期ノイズ量（V に一様ノイズを加える）。
    level : float, default 0.2
        等値線の閾値。
    min_points : int, default 16
        出力するポリラインの最小点数。
    boundary : str, default "noflux"
        マスク境界の扱い。`"noflux"` は法線方向の勾配 0、`"dirichlet"` は外側を (u=1,v=0) 固定。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        生成されたポリライン列（coords, offsets）。
    """
    if grid_pitch <= 0.0:
        raise ValueError("reaction_diffusion: grid_pitch は正である必要がある")
    if steps < 0:
        raise ValueError("reaction_diffusion: steps は 0 以上である必要がある")
    if du < 0.0 or dv < 0.0:
        raise ValueError("reaction_diffusion: du/dv は 0 以上である必要がある")
    if feed < 0.0 or kill < 0.0:
        raise ValueError("reaction_diffusion: feed/kill は 0 以上である必要がある")
    if dt <= 0.0:
        raise ValueError("reaction_diffusion: dt は正である必要がある")
    if seed < 0:
        raise ValueError("reaction_diffusion: seed は 0 以上である必要がある")
    if seed_radius < 0.0:
        raise ValueError("reaction_diffusion: seed_radius は 0 以上である必要がある")
    if noise < 0.0:
        raise ValueError("reaction_diffusion: noise は 0 以上である必要がある")
    if not 0.0 <= level <= 1.0:
        raise ValueError("reaction_diffusion: level は 0.0 以上 1.0 以下である必要がある")
    if min_points < 1:
        raise ValueError("reaction_diffusion: min_points は 1 以上である必要がある")

    mask_coords, mask_offsets = mask
    if mask_coords.shape[0] == 0:
        return empty_packed_geometry()

    pitch = grid_pitch

    frame = PlanarFrame.from_points(mask_coords, mask_offsets)
    if not frame.is_planar(planarity_threshold(mask_coords)):
        return empty_packed_geometry()

    aligned_mask = frame.to_local(mask_coords)

    packed = _pack_mask_rings_xy(aligned_mask, mask_offsets)
    if packed is None:
        return empty_packed_geometry()
    ring_vertices, ring_offsets, ring_mins, ring_maxs = packed

    mins = np.min(ring_vertices, axis=0)
    maxs = np.max(ring_vertices, axis=0)

    margin = 2.0 * pitch
    quality = current_preview_quality()
    if quality == "draft":
        draft_step_target = max(0, min(steps, DRAFT_MAX_STEPS))
        draft_point_limit = (
            DRAFT_MAX_GRID_POINTS
            if draft_step_target == 0
            else min(
                DRAFT_MAX_GRID_POINTS,
                max(4, DRAFT_MAX_POINT_STEPS // draft_step_target),
            )
        )
    else:
        draft_point_limit = DRAFT_MAX_GRID_POINTS
    grid = grid_spec_from_bbox_with_diagnostic(
        mins,
        maxs,
        pitch=pitch,
        padding=margin,
        max_points=(draft_point_limit if quality == "draft" else MAX_GRID_POINTS),
        overflow=("coarsen" if quality == "draft" else "reject"),
    )
    if grid is None:
        return empty_packed_geometry()
    xs, ys = grid.coordinates()
    nx = grid.nx
    ny = grid.ny
    pitch = grid.pitch
    if quality == "draft" and grid.coarsened:
        emit_operation_diagnostic(
            op="reaction_diffusion.grid_pitch",
            original_value=grid.requested_pitch,
            effective_value=grid.pitch,
            reason=(
                "draft preview coarsened the simulation grid to keep points × steps "
                "within budget; final capture keeps the requested pitch"
            ),
            severity="info",
        )

    domain_mask = scanline_evenodd_mask(
        ys.astype(np.float64, copy=False),
        origin_x=grid.origin_x,
        pitch=pitch,
        nx=nx,
        ring_vertices=ring_vertices,
        ring_offsets=ring_offsets,
        ring_mins=ring_mins,
        ring_maxs=ring_maxs,
    )
    if int(np.sum(domain_mask)) == 0:
        return empty_packed_geometry()

    rng = np.random.default_rng(seed)
    u0 = np.ones((ny, nx), dtype=np.float32)
    v0 = np.zeros((ny, nx), dtype=np.float32)

    mask_bool = domain_mask.astype(bool, copy=False)
    if noise > 0.0:
        v0[mask_bool] = (rng.random(int(np.sum(mask_bool))) - 0.5).astype(np.float32) * (
            2.0 * noise
        )

    r = seed_radius
    if r > 0.0:
        jj, ii = np.nonzero(domain_mask)
        cy = int(np.rint(np.mean(jj)))
        cx = int(np.rint(np.mean(ii)))
        if quality == "draft" and domain_mask[cy, cx] == 0:
            outside = (domain_mask == 0).astype(np.uint8, copy=False)
            clearance_sq = squared_euclidean_distance_transform(outside)
            clearance_sq[domain_mask == 0] = -1.0
            relocated = np.unravel_index(
                int(np.argmax(clearance_sq)),
                clearance_sq.shape,
            )
            cy, cx = int(relocated[0]), int(relocated[1])
        rr = int(np.ceil(r / pitch))
        y_min = max(0, cy - rr)
        y_max = min(ny - 1, cy + rr)
        x_min = max(0, cx - rr)
        x_max = min(nx - 1, cx + rr)
        r2 = (r / pitch) * (r / pitch)
        for y in range(y_min, y_max + 1):
            dy = float(y - cy)
            for x in range(x_min, x_max + 1):
                if domain_mask[y, x] == 0:
                    continue
                dx = float(x - cx)
                if dx * dx + dy * dy <= r2:
                    u0[y, x] = 0.0
                    v0[y, x] = 1.0

    boundary_i = 0 if boundary == "noflux" else 1
    if quality == "draft":
        work_budget_steps = DRAFT_MAX_POINT_STEPS // max(1, grid.point_count)
        effective_steps = min(
            steps,
            DRAFT_MAX_STEPS,
            work_budget_steps,
        )
    else:
        effective_steps = steps
    if effective_steps != steps:
        emit_operation_diagnostic(
            op="reaction_diffusion.steps",
            original_value=steps,
            effective_value=effective_steps,
            reason=(
                "draft preview capped points × steps work; final capture keeps the "
                "requested value"
            ),
            severity="info",
        )
    v_final = _gray_scott_simulate_masked(
        u0,
        v0,
        domain_mask,
        steps=effective_steps,
        du=du,
        dv=dv,
        feed=feed,
        kill=kill,
        dt=dt,
        boundary=boundary_i,
    )

    out_lines: list[np.ndarray] = []

    field = v_final.astype(np.float64, copy=False)
    loops = marching_squares_loops(
        field,
        origin_x=grid.origin_x,
        origin_y=grid.origin_y,
        pitch=pitch,
        level=level,
        mask=domain_mask,
    )

    for pts_xy in loops:
        if pts_xy.shape[0] < min_points:
            continue
        if pts_xy.shape[0] >= 2 and not np.all(pts_xy[0] == pts_xy[-1]):
            pts_xy = np.concatenate([pts_xy, pts_xy[:1]], axis=0)
        v3 = np.zeros((pts_xy.shape[0], 3), dtype=np.float64)
        v3[:, 0:2] = pts_xy
        out = frame.to_world(v3).astype(np.float32, copy=False)
        out_lines.append(out)

    return pack_polylines(out_lines)


__all__ = ["reaction_diffusion"]
