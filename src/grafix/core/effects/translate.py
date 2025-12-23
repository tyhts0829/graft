"""座標に XYZ オフセットを加算して平行移動する effect。"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from grafix.core.effect_registry import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import RealizedGeometry

translate_meta = {
    "delta": ParamMeta(kind="vec3", ui_min=-100.0, ui_max=100.0),
}


@effect(meta=translate_meta)
def translate(
    inputs: Sequence[RealizedGeometry],
    *,
    delta: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> RealizedGeometry:
    """平行移動（XYZ のオフセット加算）。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        平行移動対象の実体ジオメトリ列。通常は 1 要素。
    delta : tuple[float, float, float], default (0.0,0.0,0.0)
        平行移動量（dx, dy, dz）。

    Returns
    -------
    RealizedGeometry
        平行移動後の実体ジオメトリ。
    """
    if not inputs:
        coords = np.zeros((0, 3), dtype=np.float32)
        offsets = np.zeros((1,), dtype=np.int32)
        return RealizedGeometry(coords=coords, offsets=offsets)

    base = inputs[0]
    if base.coords.shape[0] == 0:
        return base

    dx, dy, dz = float(delta[0]), float(delta[1]), float(delta[2])
    if dx == 0.0 and dy == 0.0 and dz == 0.0:
        return base

    delta_vec = np.array([dx, dy, dz], dtype=np.float32)
    coords = base.coords + delta_vec
    return RealizedGeometry(coords=coords, offsets=base.offsets)
