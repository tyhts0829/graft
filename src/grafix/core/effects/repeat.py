"""入力ジオメトリを複製し、各コピーへ変換を補間適用する effect。"""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
from numba import njit  # type: ignore[attr-defined, import-untyped]

from grafix.core.operation_authoring import effect
from grafix.core.operation_schema import UiVisiblePred
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import GeomTuple
from grafix.core.resource_budget import ensure_geometry_output

repeat_meta = {
    "layout": ParamMeta(
        kind="choice",
        choices=("grid", "radial"),
        description="複製を直線状に並べるか、同心円状に並べるか選ぶ。",
    ),
    "count": ParamMeta(
        kind="int",
        ui_min=0,
        ui_max=100,
        description="直線配置で元の入力に追加する複製の数。",
    ),
    "radius": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=300.0,
        description="放射配置で複製を並べる最外周の半径。",
    ),
    "theta": ParamMeta(
        kind="float",
        ui_min=-180.0,
        ui_max=180.0,
        description="放射配置を開始する角度を度単位で指定する。",
    ),
    "n_theta": ParamMeta(
        kind="int",
        ui_min=1,
        ui_max=64,
        description="放射配置の各リングに並べる周方向の複製数。",
    ),
    "n_radius": ParamMeta(
        kind="int",
        ui_min=1,
        ui_max=64,
        description="放射配置で中心から外周までに作る半径方向の配置数。",
    ),
    "cumulative_scale": ParamMeta(
        kind="bool",
        description="複製ごとのスケール補間に指定したカーブを適用する。",
    ),
    "cumulative_offset": ParamMeta(
        kind="bool",
        description="直線配置のオフセット補間に指定したカーブを適用する。",
    ),
    "cumulative_rotate": ParamMeta(
        kind="bool",
        description="複製ごとの回転補間に指定したカーブを適用する。",
    ),
    "offset": ParamMeta(
        kind="vec3",
        ui_min=-100.0,
        ui_max=100.0,
        description="直線配置で最後の複製に到達する各軸の移動量。",
    ),
    "rotation_step": ParamMeta(
        kind="vec3",
        ui_min=-180.0,
        ui_max=180.0,
        description="最後の複製へ到達するまでに補間する各軸の回転角を度単位で指定する。",
    ),
    "scale": ParamMeta(
        kind="vec3",
        ui_min=0.25,
        ui_max=4.0,
        description="最後の複製へ到達するまでに補間する各軸の倍率。",
    ),
    "curve": ParamMeta(
        kind="float",
        ui_min=0.1,
        ui_max=5.0,
        description="有効にした累積変換の補間速度を決める指数。",
    ),
    "auto_center": ParamMeta(
        kind="bool",
        description="入力頂点の平均座標を複製変換の中心として使用する。",
    ),
    "pivot": ParamMeta(
        kind="vec3",
        ui_min=-100.0,
        ui_max=100.0,
        description="自動中心が無効な場合に複製変換の中心とする点。",
    ),
}

def _layout_is(name: str) -> UiVisiblePred:
    def _pred(v: Mapping[str, object]) -> bool:
        return v.get("layout", "grid") == name

    return _pred


def _curve_visible(v: Mapping[str, object]) -> bool:
    layout = v.get("layout", "grid")
    if layout == "radial":
        return v.get("cumulative_scale") is True or v.get("cumulative_rotate") is True
    return (
        v.get("cumulative_scale") is True
        or v.get("cumulative_offset") is True
        or v.get("cumulative_rotate") is True
    )


repeat_ui_visible = {
    # grid 配置
    "count": _layout_is("grid"),
    "cumulative_offset": _layout_is("grid"),
    "offset": _layout_is("grid"),
    # radial 配置
    "radius": _layout_is("radial"),
    "theta": _layout_is("radial"),
    "n_theta": _layout_is("radial"),
    "n_radius": _layout_is("radial"),
    # 共通
    "curve": _curve_visible,
    "pivot": lambda v: v.get("auto_center", True) is False,
}


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _repeat_fill_all(
    base_coords: np.ndarray,
    base_offsets_tail: np.ndarray,
    n_dups: int,
    curve: float,
    cumulative_scale: bool,
    cumulative_offset: bool,
    cumulative_rotate: bool,
    center: np.ndarray,
    out_coords: np.ndarray,
    out_offsets: np.ndarray,
    offset_end: np.ndarray,
    scale_end: np.ndarray,
    rotate_end: np.ndarray,
) -> None:
    """repeat の全コピーを 1 カーネルで生成する。"""
    n_vertices = base_coords.shape[0]
    n_lines = base_offsets_tail.shape[0]
    copies = n_dups + 1

    out_offsets[0] = 0

    cx = float(center[0])
    cy = float(center[1])
    cz = float(center[2])

    off_end_x = float(offset_end[0])
    off_end_y = float(offset_end[1])
    off_end_z = float(offset_end[2])
    scale_end_x = float(scale_end[0])
    scale_end_y = float(scale_end[1])
    scale_end_z = float(scale_end[2])
    rot_end_x = float(rotate_end[0])
    rot_end_y = float(rotate_end[1])
    rot_end_z = float(rotate_end[2])
    curve_f = float(curve)

    for k in range(copies):
        v_start = k * n_vertices

        o_start = 1 + k * n_lines
        for li in range(n_lines):
            out_offsets[o_start + li] = int(base_offsets_tail[li]) + int(v_start)

        if k == 0:
            for i in range(n_vertices):
                out_coords[v_start + i, 0] = base_coords[i, 0]
                out_coords[v_start + i, 1] = base_coords[i, 1]
                out_coords[v_start + i, 2] = base_coords[i, 2]
            continue

        t = float(k) / float(n_dups)
        if cumulative_scale or cumulative_offset or cumulative_rotate:
            t_curve = t**curve_f
        else:
            t_curve = t

        t_scale = t_curve if cumulative_scale else t
        t_offset = t_curve if cumulative_offset else t
        t_rotate = t_curve if cumulative_rotate else t

        sx = 1.0 + (scale_end_x - 1.0) * t_scale
        sy = 1.0 + (scale_end_y - 1.0) * t_scale
        sz = 1.0 + (scale_end_z - 1.0) * t_scale

        ox = off_end_x * t_offset
        oy = off_end_y * t_offset
        oz = off_end_z * t_offset

        rx = rot_end_x * t_rotate
        ry = rot_end_y * t_rotate
        rz = rot_end_z * t_rotate

        sin_x = math.sin(rx)
        cos_x = math.cos(rx)
        sin_y = math.sin(ry)
        cos_y = math.cos(ry)
        sin_z = math.sin(rz)
        cos_z = math.cos(rz)

        r00 = cos_y * cos_z
        r01 = sin_x * sin_y * cos_z - cos_x * sin_z
        r02 = cos_x * sin_y * cos_z + sin_x * sin_z
        r10 = cos_y * sin_z
        r11 = sin_x * sin_y * sin_z + cos_x * cos_z
        r12 = cos_x * sin_y * sin_z - sin_x * cos_z
        r20 = -sin_y
        r21 = sin_x * cos_y
        r22 = cos_x * cos_y

        for i in range(n_vertices):
            x = (float(base_coords[i, 0]) - cx) * sx
            y = (float(base_coords[i, 1]) - cy) * sy
            z = (float(base_coords[i, 2]) - cz) * sz

            rx0 = x * r00 + y * r01 + z * r02
            ry0 = x * r10 + y * r11 + z * r12
            rz0 = x * r20 + y * r21 + z * r22

            out_coords[v_start + i, 0] = rx0 + cx + ox
            out_coords[v_start + i, 1] = ry0 + cy + oy
            out_coords[v_start + i, 2] = rz0 + cz + oz


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _repeat_fill_radial(
    base_coords: np.ndarray,
    base_offsets_tail: np.ndarray,
    curve: float,
    cumulative_scale: bool,
    cumulative_rotate: bool,
    center: np.ndarray,
    out_coords: np.ndarray,
    out_offsets: np.ndarray,
    radius: float,
    theta0: float,
    n_theta: int,
    n_radius: int,
    scale_end: np.ndarray,
    rotate_end: np.ndarray,
) -> None:
    """radial レイアウトの全コピーを 1 カーネルで生成する。"""
    n_vertices = base_coords.shape[0]
    n_lines = base_offsets_tail.shape[0]

    out_offsets[0] = 0

    cx = float(center[0])
    cy = float(center[1])
    cz = float(center[2])

    scale_end_x = float(scale_end[0])
    scale_end_y = float(scale_end[1])
    scale_end_z = float(scale_end[2])
    rot_end_x = float(rotate_end[0])
    rot_end_y = float(rotate_end[1])
    rot_end_z = float(rotate_end[2])

    curve_f = float(curve)
    two_pi = 2.0 * math.pi

    if n_radius <= 1:
        copies = n_theta
    else:
        copies = 1 + (n_radius - 1) * n_theta
    denom_copy = float(copies - 1) if copies > 1 else 1.0

    k = 0
    n_rings = 1 if n_radius <= 1 else n_radius
    denom_r = float(n_radius - 1) if n_radius > 1 else 1.0
    for ring_i in range(n_rings):
        ring_t = float(ring_i) / denom_r if n_radius > 1 else 1.0
        r = float(radius) * ring_t
        n_j = n_theta if (n_radius <= 1 or ring_i != 0) else 1
        for j in range(n_j):
            angle = theta0 + two_pi * float(j) / float(n_theta)
            ox = r * math.cos(angle)
            oy = r * math.sin(angle)

            t = 1.0 if copies <= 1 else float(k) / denom_copy
            t_curve = t**curve_f if (cumulative_scale or cumulative_rotate) else t
            t_scale = t_curve if cumulative_scale else t
            t_rotate = t_curve if cumulative_rotate else t

            sx = 1.0 + (scale_end_x - 1.0) * t_scale
            sy = 1.0 + (scale_end_y - 1.0) * t_scale
            sz = 1.0 + (scale_end_z - 1.0) * t_scale

            rx = rot_end_x * t_rotate
            ry = rot_end_y * t_rotate
            rz = rot_end_z * t_rotate

            sin_x = math.sin(rx)
            cos_x = math.cos(rx)
            sin_y = math.sin(ry)
            cos_y = math.cos(ry)
            sin_z = math.sin(rz)
            cos_z = math.cos(rz)

            r00 = cos_y * cos_z
            r01 = sin_x * sin_y * cos_z - cos_x * sin_z
            r02 = cos_x * sin_y * cos_z + sin_x * sin_z
            r10 = cos_y * sin_z
            r11 = sin_x * sin_y * sin_z + cos_x * cos_z
            r12 = cos_x * sin_y * sin_z - sin_x * cos_z
            r20 = -sin_y
            r21 = sin_x * cos_y
            r22 = cos_x * cos_y

            v_start = k * n_vertices
            o_start = 1 + k * n_lines
            for li in range(n_lines):
                out_offsets[o_start + li] = int(base_offsets_tail[li]) + int(v_start)

            for i in range(n_vertices):
                x = (float(base_coords[i, 0]) - cx) * sx
                y = (float(base_coords[i, 1]) - cy) * sy
                z = (float(base_coords[i, 2]) - cz) * sz

                rx0 = x * r00 + y * r01 + z * r02
                ry0 = x * r10 + y * r11 + z * r12
                rz0 = x * r20 + y * r21 + z * r22

                out_coords[v_start + i, 0] = rx0 + cx + ox
                out_coords[v_start + i, 1] = ry0 + cy + oy
                out_coords[v_start + i, 2] = rz0 + cz
            k += 1


@effect(meta=repeat_meta, ui_visible=repeat_ui_visible)
def repeat(
    g: GeomTuple,
    *,
    layout: str = "grid",
    count: int = 3,
    radius: float = 0.0,
    theta: float = 0.0,
    n_theta: int = 6,
    n_radius: int = 1,
    cumulative_scale: bool = False,
    cumulative_offset: bool = False,
    cumulative_rotate: bool = False,
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rotation_step: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
    curve: float = 1.0,
    auto_center: bool = True,
    pivot: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> GeomTuple:
    """入力ジオメトリを複製して、規則的な配列を作る。

    Parameters
    ----------
    g : tuple[np.ndarray, np.ndarray]
        入力の実体ジオメトリ（coords, offsets）。
    layout : {"grid","radial"}, default "grid"
        `"grid"` は `count/offset` による直交配置（現状維持）。
        `"radial"` は `radius/theta/n_theta/n_radius` による円形（放射）配置。
    count : int, default 3
        複製回数。0 で no-op（入力をそのまま返す）。
        `layout="radial"` のときは無視される（コピー数は n_theta/n_radius で決まる）。
    radius : float, default 0.0
        `layout="radial"` の外周半径 [mm]。
    theta : float, default 0.0
        `layout="radial"` の開始角 [deg]。
    n_theta : int, default 6
        `layout="radial"` の周方向配置数。
    n_radius : int, default 1
        `layout="radial"` の半径方向配置数。
    cumulative_scale : bool, default False
        True のときスケール補間にカーブ（t' = t**curve）を用いる。
    cumulative_offset : bool, default False
        True のときオフセット補間にカーブ（t' = t**curve）を用いる。
        `layout="radial"` のときは無視される。
    cumulative_rotate : bool, default False
        True のとき回転補間にカーブ（t' = t**curve）を用いる。
    offset : tuple[float, float, float], default (0.0, 0.0, 0.0)
        終点オフセット [mm]。始点 0 から offset までを補間する。
        `layout="radial"` のときは無視される。
    rotation_step : tuple[float, float, float], default (0.0, 0.0, 0.0)
        終点回転角 [deg]（rx, ry, rz）。始点 0 から rotation_step までを補間する。
    scale : tuple[float, float, float], default (1.0, 1.0, 1.0)
        終点スケール倍率（sx, sy, sz）。始点 1 から scale までを補間する。
    curve : float, default 1.0
        カーブ係数。1.0 で線形、1 より大きいと終盤に変化が集中する。
    auto_center : bool, default True
        True なら平均座標を中心に使用。False なら `pivot` を使用。
    pivot : tuple[float, float, float], default (0.0, 0.0, 0.0)
        `auto_center=False` のときの変換中心 [mm]。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        複製後の実体ジオメトリ（coords, offsets）。

    Notes
    -----
    変換順序は「中心移動 → スケール → 回転 → 平行移動 → 中心に戻す」。
    回転は Rz・Ry・Rx の順に合成する。
    `layout="radial"` のとき、スケール/回転の補間パラメータ t は生成されるコピーの順序で 0→1 に変化する（位相でも変化する）。
    """
    if count < 0:
        raise ValueError("repeat: count は 0 以上である必要がある")
    if n_theta <= 0:
        raise ValueError("repeat: n_theta は正の整数である必要がある")
    if n_radius <= 0:
        raise ValueError("repeat: n_radius は正の整数である必要がある")

    if radius < 0.0:
        raise ValueError("repeat: radius は 0 以上の有限値である必要がある")
    if curve < 0.1:
        raise ValueError("repeat: curve は 0.1 以上の有限値である必要がある")

    coords, offsets = g
    if coords.shape[0] == 0:
        return coords, offsets

    n_vertices = int(coords.shape[0])
    n_lines = int(offsets.size) - 1
    if n_lines <= 0:
        return coords, offsets

    if auto_center:
        center = coords.astype(np.float64, copy=False).mean(axis=0)
    else:
        center = np.asarray(pivot, dtype=np.float64)

    center32 = np.asarray(center, dtype=np.float32)
    scale_end = np.asarray(scale, dtype=np.float32)
    rotate_end_deg = np.asarray(rotation_step, dtype=np.float32)
    rotate_end = np.deg2rad(rotate_end_deg).astype(np.float32, copy=False)

    base_tail = offsets[1:]

    if layout == "grid":
        n_dups = count
        if n_dups == 0:
            return coords, offsets

        offset_end = np.asarray(offset, dtype=np.float32)

        copies = n_dups + 1
        ensure_geometry_output(
            "repeat",
            vertices=n_vertices * copies,
            lines=n_lines * copies,
            hint="count または入力 geometry の複雑さを減らしてください",
        )
        out_coords = np.empty((n_vertices * copies, 3), dtype=np.float32)
        out_offsets = np.empty((n_lines * copies + 1,), dtype=np.int32)
        _repeat_fill_all(
            coords,
            base_tail,
            n_dups,
            curve,
            cumulative_scale,
            cumulative_offset,
            cumulative_rotate,
            center32,
            out_coords,
            out_offsets,
            offset_end,
            scale_end,
            rotate_end,
        )
        return out_coords, out_offsets

    if n_radius == 1:
        copies = n_theta
    else:
        copies = 1 + (n_radius - 1) * n_theta

    ensure_geometry_output(
        "repeat",
        vertices=n_vertices * copies,
        lines=n_lines * copies,
        hint="n_theta/n_radius または入力 geometry の複雑さを減らしてください",
    )

    theta_rad = float(np.deg2rad(theta))

    out_coords = np.empty((n_vertices * copies, 3), dtype=np.float32)
    out_offsets = np.empty((n_lines * copies + 1,), dtype=np.int32)
    _repeat_fill_radial(
        coords,
        base_tail,
        curve,
        cumulative_scale,
        cumulative_rotate,
        center32,
        out_coords,
        out_offsets,
        radius,
        float(theta_rad),
        n_theta,
        n_radius,
        scale_end,
        rotate_end,
    )
    return out_coords, out_offsets
