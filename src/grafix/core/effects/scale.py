"""座標にスケールを適用する effect。"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from grafix.core.effect_registry import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import RealizedGeometry

scale_meta = {
    "auto_center": ParamMeta(kind="bool"),
    "pivot": ParamMeta(kind="vec3", ui_min=-100.0, ui_max=100.0),
    "scale": ParamMeta(kind="vec3", ui_min=1.0, ui_max=10.0),
}


@effect(meta=scale_meta)
def scale(
    inputs: Sequence[RealizedGeometry],
    *,
    auto_center: bool = True,
    pivot: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> RealizedGeometry:
    """スケール変換を適用（auto_center 対応）。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        スケール対象の実体ジオメトリ列。通常は 1 要素。
    auto_center : bool, default True
        True なら平均座標を中心に使用。False なら `pivot` を使用。
    pivot : tuple[float, float, float], default (0.0,0.0,0.0)
        変換の中心（`auto_center=False` のとき有効）。
    scale : tuple[float, float, float], default (1.0,1.0,1.0)
        各軸の倍率。

    Returns
    -------
    RealizedGeometry
        スケール後の実体ジオメトリ。
    """
    if not inputs:
        coords = np.zeros((0, 3), dtype=np.float32)
        offsets = np.zeros((1,), dtype=np.int32)
        return RealizedGeometry(coords=coords, offsets=offsets)

    base = inputs[0]
    if base.coords.shape[0] == 0:
        return base

    sx, sy, sz = float(scale[0]), float(scale[1]), float(scale[2])

    # 中心を決定（auto_center 優先）
    if auto_center:
        center = base.coords.astype(np.float64, copy=False).mean(axis=0)
    else:
        center = np.array(
            [float(pivot[0]), float(pivot[1]), float(pivot[2])],
            dtype=np.float64,
        )

    shifted = base.coords.astype(np.float64, copy=False) - center
    factors = np.array([sx, sy, sz], dtype=np.float64)
    scaled = shifted * factors + center
    coords = scaled.astype(np.float32, copy=False)
    return RealizedGeometry(coords=coords, offsets=base.offsets)
