"""
どこで: `src/effects/scale.py`。スケール effect の実体変換。
何を: RealizedGeometry の座標配列へスケールを適用する。
なぜ: effect チェーンと Parameter GUI の最小例として使うため。
"""

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
    s: float = 1.0,
    sx: float = 1.0,
    sy: float = 1.0,
    sz: float = 1.0,
) -> RealizedGeometry:
    """正規化済み引数を用いてジオメトリをスケールする。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        スケール対象の実体ジオメトリ列。通常は 1 要素。
    s : float, optional
        全軸に掛ける共通スケール係数。
    sx : float, optional
        x 方向の追加スケール係数（最終的な係数は `s * sx`）。
    sy : float, optional
        y 方向の追加スケール係数（最終的な係数は `s * sy`）。
    sz : float, optional
        z 方向の追加スケール係数（最終的な係数は `s * sz`）。

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

    s_value = float(s)
    sx_value = float(sx)
    sy_value = float(sy)
    sz_value = float(sz)

    coords = base.coords.copy()
    coords.setflags(write=True)
    coords[:, 0] *= s_value * sx_value
    coords[:, 1] *= s_value * sy_value
    coords[:, 2] *= s_value * sz_value

    return RealizedGeometry(coords=coords, offsets=base.offsets)
