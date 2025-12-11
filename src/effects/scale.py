from __future__ import annotations

from typing import Sequence

import numpy as np

from src.core.effect_registry import effect
from src.core.realized_geometry import RealizedGeometry
from src.parameters.meta import ParamMeta


scale_meta = {
    "s": ParamMeta(kind="float", ui_min=0.0, ui_max=10.0),
    "sx": ParamMeta(kind="float", ui_min=0.0, ui_max=10.0),
    "sy": ParamMeta(kind="float", ui_min=0.0, ui_max=10.0),
    "sz": ParamMeta(kind="float", ui_min=0.0, ui_max=10.0),
}


@effect(meta=scale_meta)
def scale(
    inputs: Sequence[RealizedGeometry],
    *,
    s: float | None = None,
    sx: float | None = None,
    sy: float | None = None,
    sz: float = 1.0,
) -> RealizedGeometry:
    """正規化済み引数を用いてジオメトリをスケールする。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        スケール対象の実体ジオメトリ列。通常は 1 要素。
    s : float or None, optional
        x, y に共通のスケール係数。sx, sy が指定された場合はそちらを優先する。
    sx : float or None, optional
        x 方向スケール。省略時は s を使用し、さらに省略時は 1.0。
    sy : float or None, optional
        y 方向スケール。省略時は s を使用し、さらに省略時は 1.0。
    sz : float, optional
        z 方向スケール。

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

    s_value = 1.0 if s is None else float(s)
    sx_value = float(sx) if sx is not None else s_value
    sy_value = float(sy) if sy is not None else s_value
    sz_value = float(sz)

    coords = base.coords.copy()
    coords.setflags(write=True)
    coords[:, 0] *= sx_value
    coords[:, 1] *= sy_value
    coords[:, 2] *= sz_value

    return RealizedGeometry(coords=coords, offsets=base.offsets)
