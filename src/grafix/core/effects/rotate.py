"""座標に XYZ 回転を適用する effect。"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from grafix.core.effect_registry import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import RealizedGeometry

rotate_meta = {
    "auto_center": ParamMeta(kind="bool"),
    "pivot": ParamMeta(kind="vec3", ui_min=-100.0, ui_max=100.0),
    "rotation": ParamMeta(kind="vec3", ui_min=-180.0, ui_max=180.0),
}


@effect(meta=rotate_meta)
def rotate(
    inputs: Sequence[RealizedGeometry],
    *,
    auto_center: bool = True,
    pivot: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> RealizedGeometry:
    """回転（auto_center / pivot 対応、degree 入力）。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        回転対象の実体ジオメトリ列。通常は 1 要素。
    auto_center : bool, default True
        True なら頂点の平均座標を中心に使用。False なら `pivot` を使用。
    pivot : tuple[float, float, float], default (0.0,0.0,0.0)
        回転の中心（`auto_center=False` のとき有効）。
    rotation : tuple[float, float, float], default (0.0, 0.0, 0.0)
        各軸の回転角 [deg]（rx, ry, rz）。

    Returns
    -------
    RealizedGeometry
        回転後の実体ジオメトリ。
    """
    if not inputs:
        coords = np.zeros((0, 3), dtype=np.float32)
        offsets = np.zeros((1,), dtype=np.int32)
        return RealizedGeometry(coords=coords, offsets=offsets)

    base = inputs[0]
    if base.coords.shape[0] == 0:
        return base

    rx_deg, ry_deg, rz_deg = float(rotation[0]), float(rotation[1]), float(rotation[2])
    rx, ry, rz = np.deg2rad([rx_deg, ry_deg, rz_deg]).astype(np.float64)

    if auto_center:
        center = base.coords.astype(np.float64, copy=False).mean(axis=0)
    else:
        center = np.array(
            [float(pivot[0]), float(pivot[1]), float(pivot[2])],
            dtype=np.float64,
        )

    cx, sx = np.cos(rx), np.sin(rx)
    cy, sy = np.cos(ry), np.sin(ry)
    cz, sz = np.cos(rz), np.sin(rz)

    rx_mat = np.array(
        [[1.0, 0.0, 0.0], [0.0, cx, -sx], [0.0, sx, cx]],
        dtype=np.float64,
    )
    ry_mat = np.array(
        [[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]],
        dtype=np.float64,
    )
    rz_mat = np.array(
        [[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    # 適用順序: x → y → z（row-vector のため転置で適用）
    rot = rz_mat @ ry_mat @ rx_mat

    shifted = base.coords.astype(np.float64, copy=False) - center
    rotated = shifted @ rot.T + center
    coords = rotated.astype(np.float32, copy=False)
    return RealizedGeometry(coords=coords, offsets=base.offsets)
