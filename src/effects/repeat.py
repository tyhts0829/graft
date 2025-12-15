"""
どこで: `src/effects/repeat.py`。repeat effect の実体変換。
何を: 入力ジオメトリを複製し、各コピーへスケール/回転/平行移動を補間適用する。
なぜ: 配列複製（反復配置）を effect として提供するため。
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from numba import njit  # type: ignore[attr-defined]

from src.core.effect_registry import effect
from src.core.realized_geometry import RealizedGeometry
from src.parameters.meta import ParamMeta

repeat_meta = {
    "count": ParamMeta(kind="int", ui_min=0, ui_max=100),
    "cumulative_scale": ParamMeta(kind="bool"),
    "cumulative_offset": ParamMeta(kind="bool"),
    "cumulative_rotate": ParamMeta(kind="bool"),
    "offset": ParamMeta(kind="vec3", ui_min=-500.0, ui_max=500.0),
    "rotation_step": ParamMeta(kind="vec3", ui_min=-180.0, ui_max=180.0),
    "scale": ParamMeta(kind="vec3", ui_min=0.25, ui_max=4.0),
    "curve": ParamMeta(kind="float", ui_min=0.1, ui_max=5.0),
    "auto_center": ParamMeta(kind="bool"),
    "pivot": ParamMeta(kind="vec3", ui_min=-500.0, ui_max=500.0),
}


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _apply_transform_to_coords_range(
    coords: np.ndarray,
    center: np.ndarray,
    scale: np.ndarray,
    rotate: np.ndarray,
    offset: np.ndarray,
    out_coords: np.ndarray,
    out_start: int,
) -> None:
    """中心移動→スケール→回転→平行移動→中心復帰を適用する。"""
    rx = float(rotate[0])
    ry = float(rotate[1])
    rz = float(rotate[2])

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

    cx = float(center[0])
    cy = float(center[1])
    cz = float(center[2])
    sx = float(scale[0])
    sy = float(scale[1])
    sz = float(scale[2])
    ox = float(offset[0])
    oy = float(offset[1])
    oz = float(offset[2])

    n = coords.shape[0]
    for i in range(n):
        x = (float(coords[i, 0]) - cx) * sx
        y = (float(coords[i, 1]) - cy) * sy
        z = (float(coords[i, 2]) - cz) * sz

        rx0 = x * r00 + y * r01 + z * r02
        ry0 = x * r10 + y * r11 + z * r12
        rz0 = x * r20 + y * r21 + z * r22

        out_coords[out_start + i, 0] = rx0 + cx + ox
        out_coords[out_start + i, 1] = ry0 + cy + oy
        out_coords[out_start + i, 2] = rz0 + cz + oz


@effect(meta=repeat_meta)
def repeat(
    inputs: Sequence[RealizedGeometry],
    *,
    count: int = 3,
    cumulative_scale: bool = False,
    cumulative_offset: bool = False,
    cumulative_rotate: bool = False,
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rotation_step: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
    curve: float = 1.0,
    auto_center: bool = True,
    pivot: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> RealizedGeometry:
    """入力ジオメトリを複製して、規則的な配列を作る。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        入力の実体ジオメトリ列。通常は 1 要素。
    count : int, default 3
        複製回数。0 以下で no-op（入力をそのまま返す）。
    cumulative_scale : bool, default False
        True のときスケール補間にカーブ（t' = t**curve）を用いる。
    cumulative_offset : bool, default False
        True のときオフセット補間にカーブ（t' = t**curve）を用いる。
    cumulative_rotate : bool, default False
        True のとき回転補間にカーブ（t' = t**curve）を用いる。
    offset : tuple[float, float, float], default (0.0, 0.0, 0.0)
        終点オフセット [mm]。始点 0 から offset までを補間する。
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
    RealizedGeometry
        複製後の実体ジオメトリ。

    Notes
    -----
    変換順序は「中心移動 → スケール → 回転 → 平行移動 → 中心に戻す」。
    回転は旧仕様（Rz・Ry・Rx の合成）を踏襲する。
    """
    if not inputs:
        return _empty_geometry()

    base = inputs[0]
    if base.coords.shape[0] == 0:
        return base

    n_dups = int(count)
    if n_dups <= 0:
        return base

    n_vertices = int(base.coords.shape[0])
    n_lines = int(base.offsets.size) - 1
    if n_lines <= 0:
        return base

    curve = float(curve)
    if not np.isfinite(curve):
        curve = 1.0
    if curve < 0.1:
        curve = 0.1

    if auto_center:
        center = base.coords.astype(np.float64, copy=False).mean(axis=0)
    else:
        center = np.array(
            [float(pivot[0]), float(pivot[1]), float(pivot[2])],
            dtype=np.float64,
        )

    center32 = np.asarray(center, dtype=np.float32)
    offset_end = np.array([float(offset[0]), float(offset[1]), float(offset[2])], dtype=np.float32)
    scale_end = np.array([float(scale[0]), float(scale[1]), float(scale[2])], dtype=np.float32)
    rotate_end_deg = np.array(
        [float(rotation_step[0]), float(rotation_step[1]), float(rotation_step[2])],
        dtype=np.float32,
    )
    rotate_end = np.deg2rad(rotate_end_deg).astype(np.float32, copy=False)

    copies = n_dups + 1
    out_coords = np.empty((n_vertices * copies, 3), dtype=np.float32)
    out_offsets = np.empty((n_lines * copies + 1,), dtype=np.int32)
    out_offsets[0] = 0

    base_tail = base.offsets[1:]
    base_scale = np.array([1.0, 1.0, 1.0], dtype=np.float32)
    for k in range(copies):
        v_start = k * n_vertices
        v_end = v_start + n_vertices
        if k == 0:
            out_coords[v_start:v_end] = base.coords
        else:
            t = k / float(n_dups)
            t_scale = t**curve if cumulative_scale else t
            t_offset = t**curve if cumulative_offset else t
            t_rotate = t**curve if cumulative_rotate else t

            scale_step = base_scale + (scale_end - base_scale) * np.float32(t_scale)
            offset_step = offset_end * np.float32(t_offset)
            rotate_step = rotate_end * np.float32(t_rotate)
            _apply_transform_to_coords_range(
                base.coords,
                center32,
                scale_step,
                rotate_step,
                offset_step,
                out_coords,
                int(v_start),
            )

        o_start = 1 + k * n_lines
        o_end = o_start + n_lines
        out_offsets[o_start:o_end] = base_tail + v_start

    return RealizedGeometry(coords=out_coords, offsets=out_offsets)
