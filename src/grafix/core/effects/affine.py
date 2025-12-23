"""スケール・回転・平行移動を一括で適用する effect。

回転中心は `auto_center=True` のとき入力の平均座標、`False` のとき `pivot` を使用する。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from grafix.core.effect_registry import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import RealizedGeometry

affine_meta = {
    "auto_center": ParamMeta(kind="bool"),
    "pivot": ParamMeta(kind="vec3", ui_min=-100.0, ui_max=100.0),
    "rotation": ParamMeta(kind="vec3", ui_min=-180.0, ui_max=180.0),
    "scale": ParamMeta(kind="vec3", ui_min=0.25, ui_max=4.0),
    "delta": ParamMeta(kind="vec3", ui_min=-100.0, ui_max=100.0),
}


@effect(meta=affine_meta)
def affine(
    inputs: Sequence[RealizedGeometry],
    *,
    auto_center: bool = True,
    pivot: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
    delta: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> RealizedGeometry:
    """スケール→回転→平行移動を適用する（合成アフィン変換）。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        変換対象の実体ジオメトリ列。通常は 1 要素。
    auto_center : bool, default True
        True なら頂点の平均座標を中心に使用する。
    pivot : tuple[float, float, float], default (0.0,0.0,0.0)
        `auto_center=False` のときの変換中心。
    rotation : tuple[float, float, float], default (0.0,0.0,0.0)
        各軸の回転角 [deg]（rx, ry, rz）。
    scale : tuple[float, float, float], default (1.0,1.0,1.0)
        各軸の倍率（sx, sy, sz）。
    delta : tuple[float, float, float], default (0.0,0.0,0.0)
        最後に適用する平行移動量 [mm]（dx, dy, dz）。

    Returns
    -------
    RealizedGeometry
        変換後の実体ジオメトリ。

    Notes
    -----
    回転は旧仕様（Rz・Ry・Rx の合成）を踏襲する。
    """
    if not inputs:
        coords = np.zeros((0, 3), dtype=np.float32)
        offsets = np.zeros((1,), dtype=np.int32)
        return RealizedGeometry(coords=coords, offsets=offsets)

    base = inputs[0]
    if base.coords.shape[0] == 0:
        return base

    sx, sy, sz = float(scale[0]), float(scale[1]), float(scale[2])
    rx_deg, ry_deg, rz_deg = float(rotation[0]), float(rotation[1]), float(rotation[2])
    dx, dy, dz = float(delta[0]), float(delta[1]), float(delta[2])

    if (
        sx == 1.0
        and sy == 1.0
        and sz == 1.0
        and rx_deg == 0.0
        and ry_deg == 0.0
        and rz_deg == 0.0
        and dx == 0.0
        and dy == 0.0
        and dz == 0.0
    ):
        return base

    coords64 = base.coords.astype(np.float64, copy=False)
    if auto_center:
        center = coords64.mean(axis=0)
    else:
        center = np.array(
            [float(pivot[0]), float(pivot[1]), float(pivot[2])],
            dtype=np.float64,
        )

    centered = coords64 - center
    scaled = centered * np.array([sx, sy, sz], dtype=np.float64)

    rx, ry, rz = np.deg2rad([rx_deg, ry_deg, rz_deg]).astype(np.float64)
    sin_x, sin_y, sin_z = np.sin([rx, ry, rz])
    cos_x, cos_y, cos_z = np.cos([rx, ry, rz])

    rot = np.empty((3, 3), dtype=np.float64)
    rot[0, 0] = cos_y * cos_z
    rot[0, 1] = sin_x * sin_y * cos_z - cos_x * sin_z
    rot[0, 2] = cos_x * sin_y * cos_z + sin_x * sin_z
    rot[1, 0] = cos_y * sin_z
    rot[1, 1] = sin_x * sin_y * sin_z + cos_x * cos_z
    rot[1, 2] = cos_x * sin_y * sin_z - sin_x * cos_z
    rot[2, 0] = -sin_y
    rot[2, 1] = sin_x * cos_y
    rot[2, 2] = cos_x * cos_y

    rotated = scaled @ rot.T
    transformed = rotated + center + np.array([dx, dy, dz], dtype=np.float64)
    coords = transformed.astype(np.float32, copy=False)
    return RealizedGeometry(coords=coords, offsets=base.offsets)
