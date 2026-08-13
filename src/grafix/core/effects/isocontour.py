"""
Purpose:
    平面閉領域のsigned distanceから、指定間隔の内側・外側等値線群を生成する。
Use when:
    mask由来の反復offset表現、SDF level範囲、またはcontour samplingを変更する場合。
Constraints:
    - maskの閉ringだけをeven-odd領域として使い、open線は無視する。
    - 非平面・ringなし・grid上限超過ではemptyを返し、入力を別の平面へ推測投影しない。
    - 生成contourは元のPlanarFrameへ戻し、明示的なclosed loopとして保つ。
"""

from __future__ import annotations

import numpy as np

from grafix.core.operation_authoring import effect
from grafix.core.operation_diagnostics import grid_spec_from_bbox_with_diagnostic
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import GeomTuple

from grafix.core.geometry_kernels.grid import DEFAULT_MAX_GRID_POINTS
from grafix.core.geometry_kernels.marching import marching_squares_loops
from grafix.core.geometry_kernels.packed import (
    empty_packed_geometry,
    pack_polylines,
)
from grafix.core.geometry_kernels.planar import (
    PlanarFrame,
    extract_planar_rings,
    pack_planar_rings,
    planarity_threshold,
)
from grafix.core.geometry_kernels.raster import signed_distance_grid_edt

MAX_GRID_POINTS = DEFAULT_MAX_GRID_POINTS

_AUTO_CLOSE_THRESHOLD_DEFAULT = 1e-3
_MODE_CHOICES = ("inside", "outside", "both")


isocontour_meta = {
    "spacing": ParamMeta(
        kind="float",
        ui_min=0.2,
        ui_max=10.0,
        description="隣り合う符号付き距離の等値線どうしの間隔。",
    ),
    "phase": ParamMeta(
        kind="float",
        ui_min=-10.0,
        ui_max=10.0,
        description="等値線レベル全体を距離方向へずらす位相。",
    ),
    "max_dist": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=200.0,
        description="入力境界から等値線を抽出する最大距離。",
    ),
    "mode": ParamMeta(
        kind="choice",
        choices=_MODE_CHOICES,
        description="入力境界の内側、外側、または両側のどこから等値線を抽出するか選ぶ。",
    ),
    "grid_pitch": ParamMeta(
        kind="float",
        ui_min=0.1,
        ui_max=5.0,
        description="符号付き距離場を評価する二次元グリッドの間隔。",
    ),
    "gamma": ParamMeta(
        kind="float",
        ui_min=0.3,
        ui_max=3.0,
        description="抽出範囲を保ったまま等値線の距離分布を非線形に変形する指数。",
    ),
    "level_step": ParamMeta(
        kind="int",
        ui_min=1,
        ui_max=20,
        description="生成した等値線を何本ごとに一つ残すか指定する。",
    ),
    "auto_close_threshold": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=5.0,
        description="入力線の端点を自動的に閉じるとみなす最大距離。",
    ),
    "keep_original": ParamMeta(
        kind="bool",
        description="生成した等値線に元の入力線を加えて出力する。",
    ),
}


@effect(meta=isocontour_meta, n_inputs=1)
def isocontour(
    mask: GeomTuple,
    *,
    spacing: float = 2.0,
    phase: float = 0.0,
    max_dist: float = 30.0,
    mode: str = "inside",  # "inside" | "outside" | "both"
    grid_pitch: float = 0.5,
    gamma: float = 1.0,
    level_step: int = 1,
    auto_close_threshold: float = _AUTO_CLOSE_THRESHOLD_DEFAULT,
    keep_original: bool = False,
) -> GeomTuple:
    """閉曲線群から等高線（等値線）を複数レベル抽出して出力する。

    Parameters
    ----------
    mask : tuple[np.ndarray, np.ndarray]
        閉曲線群（外周＋穴）を想定する入力（coords, offsets）。開曲線は無視する。
    spacing : float, default 2.0
        レベル間隔。
    phase : float, default 0.0
        レベルの位相（`0` だと境界 `SDF=0` が含まれる）。
    max_dist : float, default 30.0
        抽出範囲（`|SDF| <= max_dist`）。
    mode : str, default "inside"
        `"inside"` は内側のみ、`"outside"` は外側のみ、`"both"` は両側を抽出する。
    grid_pitch : float, default 0.5
        SDF 評価グリッドのピッチ。
    gamma : float, default 1.0
        距離の非線形（`1.0` は線形）。`max_dist` を基準に 0..max_dist を保ったままカーブを歪める。
    level_step : int, default 1
        レベルの間引き。`n` のとき「n 本に 1 本」だけ残す（`1` は全て）。
    auto_close_threshold : float, default 1e-3
        閉曲線とみなす端点距離閾値。
    keep_original : bool, default False
        True のとき、生成結果に加えて元の入力も出力に含める。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        抽出した等値線のポリライン列（coords, offsets）。
    """
    if grid_pitch <= 0.0:
        raise ValueError("isocontour: grid_pitch は正である必要がある")
    if spacing <= 0.0:
        raise ValueError("isocontour: spacing は正である必要がある")
    if max_dist < 0.0:
        raise ValueError("isocontour: max_dist は 0 以上である必要がある")
    if auto_close_threshold < 0.0:
        raise ValueError("isocontour: auto_close_threshold は 0 以上である必要がある")
    if gamma <= 0.0:
        raise ValueError("isocontour: gamma は正である必要がある")
    if level_step < 1:
        raise ValueError("isocontour: level_step は 1 以上である必要がある")

    mask_coords, mask_offsets = mask
    if mask_coords.shape[0] == 0:
        return empty_packed_geometry()

    pitch = grid_pitch

    frame = PlanarFrame.from_points(mask_coords, mask_offsets)
    if not frame.is_planar(planarity_threshold(mask_coords)):
        return empty_packed_geometry()
    coords_xy_all = frame.to_local(mask_coords)

    # 閉曲線のみを抽出（外周＋穴）。
    rings = extract_planar_rings(
        coords_xy_all,
        mask_offsets,
        auto_close_threshold=auto_close_threshold,
    )
    if not rings:
        return empty_packed_geometry()

    mins = np.min(np.stack([r0.mins for r0 in rings], axis=0), axis=0)
    maxs = np.max(np.stack([r0.maxs for r0 in rings], axis=0), axis=0)

    # SDF は「輪郭から max_dist だけ離れた範囲」まで必要なので、AABB を余裕を持って拡張する。
    margin = max_dist + 2.0 * pitch
    grid = grid_spec_from_bbox_with_diagnostic(
        mins,
        maxs,
        pitch=pitch,
        padding=margin,
        max_points=MAX_GRID_POINTS,
        overflow="reject",
    )
    if grid is None:
        return empty_packed_geometry()
    xs, ys = grid.coordinates()
    x0 = grid.origin_x
    y0 = grid.origin_y
    pitch = grid.pitch

    # 1) グリッド上で SDF を評価する（EDT: 近似 / `O(Ngrid)`）。
    ring_vertices, ring_offsets, ring_mins, ring_maxs = pack_planar_rings(rings)
    sdf = signed_distance_grid_edt(
        xs.astype(np.float64, copy=False),
        ys.astype(np.float64, copy=False),
        ring_vertices=ring_vertices,
        ring_offsets=ring_offsets,
        ring_mins=ring_mins,
        ring_maxs=ring_maxs,
        max_distance=max_dist,
        gamma=gamma,
        pitch=float(pitch),
    )

    # 2) `sin()` の 0 交差を取ることで複数レベルの等値線を一括抽出する。
    #    spacing_eff を大きくすると「間引き」になり、密度調整に使える。
    spacing_eff = spacing * level_step
    field = np.sin(np.pi * (sdf - phase) / spacing_eff).astype(
        np.float64,
        copy=False,
    )

    if mode == "inside":
        lo, hi = -max_dist, 0.0
    elif mode == "outside":
        lo, hi = 0.0, max_dist
    else:
        lo, hi = -max_dist, max_dist

    # 3) Marching Squares で線分を列挙し、端点の SDF が [lo, hi] に入るものだけ残す。
    loops_xy = marching_squares_loops(
        field,
        origin_x=float(x0),
        origin_y=float(y0),
        pitch=float(pitch),
        sample_field=sdf,
        sample_range=(float(lo), float(hi)),
    )

    out_lines: list[np.ndarray] = []
    for pts_xy in loops_xy:
        if pts_xy.shape[0] < 4:
            continue
        v3 = np.zeros((pts_xy.shape[0], 3), dtype=np.float64)
        v3[:, 0:2] = pts_xy
        # 元の 3D 平面へ戻し、出力は float32 に揃える。
        out = frame.to_world(v3).astype(np.float32, copy=False)
        out_lines.append(out)

    if keep_original:
        # 生成結果に元の入力を足すオプション（デバッグ・比較用途）。
        for i in range(int(mask_offsets.size) - 1):
            s = int(mask_offsets[i])
            e = int(mask_offsets[i + 1])
            original = mask_coords[s:e]
            if original.shape[0] > 0:
                out_lines.append(original.astype(np.float32, copy=False))

    return pack_polylines(out_lines)
