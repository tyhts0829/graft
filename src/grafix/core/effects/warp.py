"""
Purpose:
    平面閉maskのsigned distanceを使い、base線へlens変換または境界方向の変位を与える。
Use when:
    二入力の局所warp、inside profile、境界へのattract・repelを変更する場合。
Constraints:
    - 第1入力を変形対象、第2入力をframeとeven-odd距離場を定めるmaskとして扱う。
    - baseとmaskが同一平面にない、または閉ringを得られない場合はbaseを変更しない。
    - baseのpolyline境界を保ち、show_maskとkeep_originalは変形結果への追加出力に限定する。
"""

from __future__ import annotations

import math

import numpy as np
from numba import (  # type: ignore[import-untyped, attr-defined]
    get_num_threads,
    njit,
    prange,
)

from grafix.core.operation_authoring import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import GeomTuple, concat_geom_tuples

from grafix.core.geometry_kernels.planar import (
    PlanarFrame,
    extract_planar_rings,
    pack_planar_rings,
    planarity_threshold,
)

_AUTO_CLOSE_THRESHOLD_DEFAULT = 1e-3
_LENS_OPTIMIZED_MIN_POINT_SEGMENTS = 100_000
_LENS_OPTIMIZED_MIN_BASE_POINTS = 256
_LENS_EDGE_SCRATCH_BYTES_PER_SEGMENT = 7 * np.dtype(np.float64).itemsize
_LENS_MAX_EDGE_SCRATCH_BYTES = 8 * 1024 * 1024
_MODE_CHOICES = ("lens", "attract")
_KIND_CHOICES = ("scale", "rotate", "shear", "swirl")
_PROFILE_CHOICES = ("band", "ramp")
_DIRECTION_CHOICES = ("attract", "repel")

warp_meta = {
    "mode": ParamMeta(
        kind="choice",
        choices=_MODE_CHOICES,
        description="マスク近傍で座標変換をブレンドするか、境界へ吸着または反発させるか選ぶ。",
    ),
    "strength": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=2.0,
        description="選択したワープ変形を元の座標へブレンドする強さ。",
    ),
    "show_mask": ParamMeta(
        kind="bool",
        description="変形結果に位置確認用のマスク輪郭を加えて出力する。",
    ),
    "keep_original": ParamMeta(
        kind="bool",
        description="変形結果に比較用の元の入力線を加えて出力する。",
    ),
    # lens
    "kind": ParamMeta(
        kind="choice",
        choices=_KIND_CHOICES,
        description="レンズモードでマスク領域へブレンドする座標変換の種類。",
    ),
    "profile": ParamMeta(
        kind="choice",
        choices=_PROFILE_CHOICES,
        description=(
            "距離幅が正のときに使うレンズ強度の形状。band は境界と遷移幅の"
            "両端で 0、中間で最大となり、ramp は境界から離れるほど増加する。"
        ),
    ),
    "band": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=200.0,
        description=(
            "レンズ強度をマスク境界から遷移させる距離幅。"
            "0 では距離遷移を使わず対象全体へ一様に適用する。"
        ),
    ),
    "inside_only": ParamMeta(
        kind="bool",
        description="レンズ変形をマスクの内側にある頂点だけへ適用する。",
    ),
    "auto_center": ParamMeta(
        kind="bool",
        description="マスクのバウンディングボックス中心をレンズ変換の中心にする。",
    ),
    "pivot": ParamMeta(
        kind="vec3",
        ui_min=-100.0,
        ui_max=100.0,
        description="自動中心が無効な場合にレンズ変換の中心とする点。",
    ),
    "scale": ParamMeta(
        kind="float",
        ui_min=0.5,
        ui_max=3.0,
        description="レンズのスケール変換で中心からの距離へ適用する倍率。",
    ),
    "angle": ParamMeta(
        kind="float",
        ui_min=-180.0,
        ui_max=180.0,
        description="レンズの回転または渦巻き変換で適用する角度を度単位で指定する。",
    ),
    "shear": ParamMeta(
        kind="vec3",
        ui_min=-1.0,
        ui_max=1.0,
        description="レンズのシアー変換で X と Y 方向へ適用する係数。",
    ),
    # attract
    "direction": ParamMeta(
        kind="choice",
        choices=_DIRECTION_CHOICES,
        description="頂点をマスク境界へ引き寄せるか、境界から遠ざけるか選ぶ。",
    ),
    "bias": ParamMeta(
        kind="float",
        ui_min=-50.0,
        ui_max=50.0,
        description="吸着または反発の目標位置をマスク境界からずらす符号付き距離。",
    ),
    "snap_band": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=200.0,
        description="吸着または反発の対象とする目標距離からの最大差。0 で制限なし。",
    ),
    "falloff": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=200.0,
        description="吸着または反発の強さを目標位置からの距離で減衰させる尺度。0 で減衰なし。",
    ),
}

warp_ui_visible = {
    # lens
    "kind": lambda v: v.get("mode", "lens") == "lens",
    "profile": lambda v: v.get("mode", "lens") == "lens",
    "band": lambda v: v.get("mode", "lens") == "lens",
    "inside_only": lambda v: v.get("mode", "lens") == "lens",
    "auto_center": lambda v: v.get("mode", "lens") == "lens",
    "pivot": lambda v: v.get("mode", "lens") == "lens"
    and v.get("auto_center", True) is False,
    "scale": lambda v: v.get("mode", "lens") == "lens"
    and v.get("kind", "scale") == "scale",
    "angle": lambda v: v.get("mode", "lens") == "lens"
    and v.get("kind", "scale") in {"rotate", "swirl"},
    "shear": lambda v: v.get("mode", "lens") == "lens"
    and v.get("kind", "scale") == "shear",
    # attract
    "direction": lambda v: v.get("mode", "lens") == "attract",
    "bias": lambda v: v.get("mode", "lens") == "attract",
    "snap_band": lambda v: v.get("mode", "lens") == "attract",
    "falloff": lambda v: v.get("mode", "lens") == "attract",
}


def _lens_edge_scratch_bytes(edge_count: int) -> int:
    """lens 用 edge invariant pack の常駐 scratch byte 数を返す。"""

    return (
        max(0, int(edge_count))
        * _LENS_EDGE_SCRATCH_BYTES_PER_SEGMENT
    )


def _use_optimized_lens_path(
    *,
    base_point_count: int,
    segment_count: int,
    edge_count: int,
) -> bool:
    """crossover と scratch 上限を満たす場合だけ lens 高速経路を使う。"""

    n_points = max(0, int(base_point_count))
    n_segments = max(0, int(segment_count))
    return (
        n_points >= _LENS_OPTIMIZED_MIN_BASE_POINTS
        and n_points * n_segments >= _LENS_OPTIMIZED_MIN_POINT_SEGMENTS
        and _lens_edge_scratch_bytes(edge_count)
        <= _LENS_MAX_EDGE_SCRATCH_BYTES
    )


def _build_ring_edge_invariants(
    ring_vertices: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """点に依存しない edge 差分と長さ二乗を一度だけ構築する。"""
    starts = ring_vertices[:-1]
    ends = ring_vertices[1:]
    edge_dx = ends[:, 0] - starts[:, 0]
    edge_dy = ends[:, 1] - starts[:, 1]
    edge_denom = np.empty_like(edge_dx)
    edge_bounds = np.empty((edge_dx.shape[0], 4), dtype=np.float64)
    np.multiply(edge_dx, edge_dx, out=edge_denom)
    np.multiply(edge_dy, edge_dy, out=edge_bounds[:, 0])
    np.add(edge_denom, edge_bounds[:, 0], out=edge_denom)
    np.minimum(starts[:, 0], ends[:, 0], out=edge_bounds[:, 0])
    np.maximum(starts[:, 0], ends[:, 0], out=edge_bounds[:, 1])
    np.minimum(starts[:, 1], ends[:, 1], out=edge_bounds[:, 2])
    np.maximum(starts[:, 1], ends[:, 1], out=edge_bounds[:, 3])
    return edge_dx, edge_dy, edge_denom, edge_bounds


@njit(cache=True, parallel=True)
def _evaluate_warp_sdf_points_numba(
    points_xy: np.ndarray,
    ring_vertices: np.ndarray,
    ring_offsets: np.ndarray,
    ring_mins: np.ndarray,
    ring_maxs: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """warp の汎用 SDF と外向き法線を bit 安定な基準経路で返す。

    attract は法線まで必要とし、lens の edge-invariant 高速経路は本 kernel の
    distance と bit 単位で一致する。growth 固有の fastmath/bbox 省略は適用しない。
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

            inside_possible = (
                x >= float(ring_mins[ri, 0])
                and x <= float(ring_maxs[ri, 0])
                and y >= float(ring_mins[ri, 1])
                and y <= float(ring_maxs[ri, 1])
            )

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


@njit(inline="always")
def _evaluate_signed_distance_point_numba(
    x: float,
    y: float,
    ring_vertices: np.ndarray,
    edge_dx: np.ndarray,
    edge_dy: np.ndarray,
    edge_denom: np.ndarray,
    edge_bounds: np.ndarray,
    ring_offsets: np.ndarray,
    ring_mins: np.ndarray,
    ring_maxs: np.ndarray,
) -> float:
    """1点の signed distance を既構築 edge invariant から求める。"""
    n_rings = int(ring_offsets.shape[0]) - 1

    min_ds = 1e300
    inside_parity = 0
    for ri in range(n_rings):
        s = int(ring_offsets[ri])
        e = int(ring_offsets[ri + 1])

        ring_dx = 0.0
        if x < float(ring_mins[ri, 0]):
            ring_dx = float(ring_mins[ri, 0]) - x
        elif x > float(ring_maxs[ri, 0]):
            ring_dx = x - float(ring_maxs[ri, 0])
        ring_dy = 0.0
        if y < float(ring_mins[ri, 1]):
            ring_dy = float(ring_mins[ri, 1]) - y
        elif y > float(ring_maxs[ri, 1]):
            ring_dy = y - float(ring_maxs[ri, 1])
        ring_lower_ds = ring_dx * ring_dx + ring_dy * ring_dy
        if ring_lower_ds > min_ds * (1.0 + 1e-12):
            continue

        inside_possible = (
            x >= float(ring_mins[ri, 0])
            and x <= float(ring_maxs[ri, 0])
            and y >= float(ring_mins[ri, 1])
            and y <= float(ring_maxs[ri, 1])
        )

        inside = 0
        for k in range(s, e - 1):
            ax = float(ring_vertices[k, 0])
            ay = float(ring_vertices[k, 1])
            by = float(ring_vertices[k + 1, 1])

            dx = edge_dx[k]
            dy = edge_dy[k]
            box_dx = 0.0
            if x < edge_bounds[k, 0]:
                box_dx = edge_bounds[k, 0] - x
            elif x > edge_bounds[k, 1]:
                box_dx = x - edge_bounds[k, 1]
            box_dy = 0.0
            if y < edge_bounds[k, 2]:
                box_dy = edge_bounds[k, 2] - y
            elif y > edge_bounds[k, 3]:
                box_dy = y - edge_bounds[k, 3]
            lower_ds = box_dx * box_dx + box_dy * box_dy

            if lower_ds <= min_ds * (1.0 + 1e-12):
                denom = edge_denom[k]
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

            if inside_possible and ((ay > y) != (by > y)):
                x_int = ax + (y - ay) * dx / dy
                if x < x_int:
                    inside ^= 1

        inside_parity ^= inside

    dist = math.sqrt(min_ds)
    return -dist if inside_parity != 0 else dist


@njit(cache=True)
def _evaluate_signed_distances_serial_numba(
    points_xy: np.ndarray,
    ring_vertices: np.ndarray,
    edge_dx: np.ndarray,
    edge_dy: np.ndarray,
    edge_denom: np.ndarray,
    edge_bounds: np.ndarray,
    ring_offsets: np.ndarray,
    ring_mins: np.ndarray,
    ring_maxs: np.ndarray,
) -> np.ndarray:
    n = int(points_xy.shape[0])
    out_d = np.empty((n,), dtype=np.float64)
    for i in range(n):
        out_d[i] = _evaluate_signed_distance_point_numba(
            float(points_xy[i, 0]),
            float(points_xy[i, 1]),
            ring_vertices,
            edge_dx,
            edge_dy,
            edge_denom,
            edge_bounds,
            ring_offsets,
            ring_mins,
            ring_maxs,
        )
    return out_d


@njit(cache=True, parallel=True)
def _evaluate_signed_distances_parallel_numba(
    points_xy: np.ndarray,
    ring_vertices: np.ndarray,
    edge_dx: np.ndarray,
    edge_dy: np.ndarray,
    edge_denom: np.ndarray,
    edge_bounds: np.ndarray,
    ring_offsets: np.ndarray,
    ring_mins: np.ndarray,
    ring_maxs: np.ndarray,
) -> np.ndarray:
    n = int(points_xy.shape[0])
    out_d = np.empty((n,), dtype=np.float64)
    for i in prange(n):
        out_d[i] = _evaluate_signed_distance_point_numba(
            float(points_xy[i, 0]),
            float(points_xy[i, 1]),
            ring_vertices,
            edge_dx,
            edge_dy,
            edge_denom,
            edge_bounds,
            ring_offsets,
            ring_mins,
            ring_maxs,
        )
    return out_d


def _evaluate_signed_distances_numba(
    points_xy: np.ndarray,
    ring_vertices: np.ndarray,
    edge_dx: np.ndarray,
    edge_dy: np.ndarray,
    edge_denom: np.ndarray,
    edge_bounds: np.ndarray,
    ring_offsets: np.ndarray,
    ring_mins: np.ndarray,
    ring_maxs: np.ndarray,
) -> np.ndarray:
    """thread 数に応じて未使用 kernel を compile せず距離だけを返す。"""

    kernel = (
        _evaluate_signed_distances_parallel_numba
        if get_num_threads() > 1
        else _evaluate_signed_distances_serial_numba
    )
    return kernel(
        points_xy,
        ring_vertices,
        edge_dx,
        edge_dy,
        edge_denom,
        edge_bounds,
        ring_offsets,
        ring_mins,
        ring_maxs,
    )


def _smoothstep(t: np.ndarray) -> np.ndarray:
    t0 = np.clip(t, 0.0, 1.0)
    return t0 * t0 * (3.0 - 2.0 * t0)


@effect(meta=warp_meta, ui_visible=warp_ui_visible, n_inputs=2)
def warp(
    base: GeomTuple,
    mask: GeomTuple,
    *,
    mode: str = "lens",  # "lens" | "attract"
    strength: float = 1.0,
    # lens
    kind: str = "scale",  # "scale" | "rotate" | "shear" | "swirl"
    profile: str = "band",  # "band" | "ramp"
    band: float = 20.0,
    inside_only: bool = True,
    auto_center: bool = True,
    pivot: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: float = 1.4,
    angle: float = 30.0,
    shear: tuple[float, float, float] = (0.2, 0.0, 0.0),
    # attract
    direction: str = "attract",  # "attract" | "repel"
    bias: float = 0.0,
    snap_band: float = 30.0,
    falloff: float = 12.0,
    # output
    show_mask: bool = False,
    keep_original: bool = False,
) -> GeomTuple:
    """マスク距離場で、入力線を lens/attract 変形する。

    Parameters
    ----------
    base : tuple[np.ndarray, np.ndarray]
        変形対象の入力（coords, offsets）。
    mask : tuple[np.ndarray, np.ndarray]
        マスク（閉曲線リング列を想定、coords, offsets）。
    mode : str, default "lens"
        `"lens"` は座標変換をブレンドして歪ませる。`"attract"` は境界へ吸着/反発する。
    strength : float, default 1.0
        変形の強さ（0..2 を想定）。
    kind : str, default "scale"
        `mode="lens"` のときの座標変換種別。
    profile : str, default "band"
        `mode="lens"` の距離プロファイル。
    band : float, default 20.0
        `mode="lens"` の距離スケール [mm]。0 はハード扱い。
    inside_only : bool, default True
        `mode="lens"` で mask 内側だけに効かせるか。
    auto_center : bool, default True
        `mode="lens"` の中心を mask AABB center にする。
    pivot : tuple[float, float, float], default (0.0,0.0,0.0)
        `auto_center=False` のときの中心。
    scale : float, default 1.4
        `kind="scale"` の倍率。
    angle : float, default 30.0
        `kind in {"rotate","swirl"}` の角度 [deg]。
    shear : tuple[float, float, float], default (0.2,0.0,0.0)
        `kind="shear"` の shear 係数（x,y を使用）。
    direction : str, default "attract"
        `mode="attract"` の向き（吸着/反発）。
    bias : float, default 0.0
        `mode="attract"` の目標 signed distance [mm]（0 で境界）。
    snap_band : float, default 30.0
        `mode="attract"` で変形対象にする `|d-bias|` の上限（0 で無制限）。
    falloff : float, default 12.0
        `mode="attract"` の距離減衰スケール [mm]（0 でフラット）。
    show_mask : bool, default False
        True のとき、mask 入力も出力に含める（位置確認用）。
    keep_original : bool, default False
        True のとき、元の base も出力に含める（比較用）。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        変形後の実体ジオメトリ（coords, offsets）。

    Raises
    ------
    ValueError
        `strength`、`band`、`snap_band`、`falloff` のいずれかが負の場合。
    """
    if strength < 0.0:
        raise ValueError("warp の strength は 0 以上である必要がある")
    if band < 0.0:
        raise ValueError("warp の band は 0 以上である必要がある")
    if snap_band < 0.0:
        raise ValueError("warp の snap_band は 0 以上である必要がある")
    if falloff < 0.0:
        raise ValueError("warp の falloff は 0 以上である必要がある")

    base_coords, base_offsets = base
    mask_coords, mask_offsets = mask

    def _with_extras(result: GeomTuple) -> GeomTuple:
        out_geoms: list[GeomTuple] = [result]
        if keep_original and result is not base:
            out_geoms.append(base)
        if show_mask:
            out_geoms.append(mask)
        return (
            concat_geom_tuples(*out_geoms)
            if len(out_geoms) > 1
            else out_geoms[0]
        )

    if base_coords.shape[0] == 0:
        return _with_extras(base)
    if mask_coords.shape[0] == 0:
        return _with_extras(base)

    if strength == 0.0:
        return _with_extras(base)

    # mode-specific params
    if mode == "lens":
        angle_rad = float(np.deg2rad(angle))
        shx, shy, _ = shear

        if kind == "scale" and scale == 1.0:
            return _with_extras(base)
        if kind in {"rotate", "swirl"} and angle_rad == 0.0:
            return _with_extras(base)
        if kind == "shear" and shx == 0.0 and shy == 0.0:
            return _with_extras(base)
    else:
        dir_sign = 1.0 if direction == "attract" else -1.0

    frame = PlanarFrame.from_points(mask_coords, mask_offsets)
    threshold = planarity_threshold(mask_coords)
    if not frame.is_planar(threshold):
        return _with_extras(base)

    aligned_base = frame.to_local(base_coords)
    aligned_mask = frame.to_local(mask_coords)

    if float(np.max(np.abs(aligned_base[:, 2]))) > threshold:
        return _with_extras(base)

    rings = extract_planar_rings(
        aligned_mask,
        mask_offsets,
        auto_close_threshold=float(_AUTO_CLOSE_THRESHOLD_DEFAULT),
    )
    if not rings:
        return _with_extras(base)

    ring_vertices, ring_offsets, ring_mins, ring_maxs = pack_planar_rings(rings)
    base_xy = aligned_base[:, 0:2].astype(np.float64, copy=False)

    if mode == "lens":
        n_segments = int(ring_vertices.shape[0]) - len(rings)
        if not _use_optimized_lens_path(
            base_point_count=int(base_xy.shape[0]),
            segment_count=n_segments,
            edge_count=max(0, int(ring_vertices.shape[0]) - 1),
        ):
            d, _, _ = _evaluate_warp_sdf_points_numba(
                base_xy,
                ring_vertices,
                ring_offsets,
                ring_mins,
                ring_maxs,
            )
        else:
            edge_dx, edge_dy, edge_denom, edge_bounds = (
                _build_ring_edge_invariants(ring_vertices)
            )
            d = _evaluate_signed_distances_numba(
                base_xy,
                ring_vertices,
                edge_dx,
                edge_dy,
                edge_denom,
                edge_bounds,
                ring_offsets,
                ring_mins,
                ring_maxs,
            )
        mins = np.min(np.stack([r0.mins for r0 in rings], axis=0), axis=0)
        maxs = np.max(np.stack([r0.maxs for r0 in rings], axis=0), axis=0)

        if auto_center:
            center2 = 0.5 * (mins + maxs)
        else:
            pivot3 = np.asarray((pivot,), dtype=np.float64)
            pivot_xy = frame.to_local(pivot3)[0, 0:2]
            center2 = pivot_xy.astype(np.float64, copy=False)

        if band <= 0.0:
            if inside_only:
                w = (d < 0.0).astype(np.float64)
            else:
                w = np.ones_like(d, dtype=np.float64)
        else:
            if inside_only:
                t = (-d) / band
            else:
                t = np.abs(d) / band
            s = _smoothstep(t)
            if profile == "ramp":
                w = s
            else:
                w = 4.0 * s * (1.0 - s)
            if inside_only:
                w[~(d < 0.0)] = 0.0

        max_w = float(np.max(w)) if w.size else 0.0
        if not math.isfinite(max_w) or max_w <= 0.0:
            return _with_extras(base)

        mix = (strength * w).astype(np.float64, copy=False)
        np.clip(mix, 0.0, 1e9, out=mix)

        v = base_xy - center2[None, :]

        if kind == "scale":
            target_xy = center2[None, :] + scale * v
        elif kind == "rotate":
            c = float(math.cos(angle_rad))
            s_ = float(math.sin(angle_rad))
            rx = c * v[:, 0] - s_ * v[:, 1]
            ry = s_ * v[:, 0] + c * v[:, 1]
            target_xy = np.stack([rx, ry], axis=1)
            target_xy += center2[None, :]
        elif kind == "shear":
            rx = v[:, 0] + float(shx) * v[:, 1]
            ry = float(shy) * v[:, 0] + v[:, 1]
            target_xy = np.stack([rx, ry], axis=1)
            target_xy += center2[None, :]
        else:  # kind == "swirl"
            span = maxs - mins
            r_ref = 0.5 * float(np.linalg.norm(span))
            if not math.isfinite(r_ref) or r_ref <= 1e-12:
                return _with_extras(base)
            r = np.sqrt(v[:, 0] * v[:, 0] + v[:, 1] * v[:, 1])
            theta = angle_rad * (r / r_ref)
            c = np.cos(theta)
            s_ = np.sin(theta)
            rx = c * v[:, 0] - s_ * v[:, 1]
            ry = s_ * v[:, 0] + c * v[:, 1]
            target_xy = np.stack([rx, ry], axis=1)
            target_xy += center2[None, :]

        target_xy -= base_xy
        target_xy *= mix[:, None]
        target_xy += base_xy
        out3 = np.empty((target_xy.shape[0], 3), dtype=np.float64)
        out3[:, 0:2] = target_xy
        out3[:, 2] = 0.0
        restored = frame.to_world(out3).astype(np.float32, copy=False)
        return _with_extras((restored, base_offsets))

    # mode == "attract"
    d, gx, gy = _evaluate_warp_sdf_points_numba(
        base_xy,
        ring_vertices,
        ring_offsets,
        ring_mins,
        ring_maxs,
    )
    delta = bias - d
    abs_delta = np.abs(delta)

    w = np.ones_like(delta, dtype=np.float64)
    if snap_band > 0.0:
        w = (abs_delta <= snap_band).astype(np.float64)
    if falloff > 0.0:
        w *= np.exp(-abs_delta / falloff)

    shift = dir_sign * strength * w * delta
    out_aligned = aligned_base.copy()
    out_aligned[:, 0] += shift * gx
    out_aligned[:, 1] += shift * gy

    out = frame.to_world(out_aligned).astype(np.float32, copy=False)
    return _with_extras((out, base_offsets))


__all__ = ["warp", "warp_meta"]
