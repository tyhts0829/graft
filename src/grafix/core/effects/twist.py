"""位置に応じて指定軸回りにねじる effect。"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from grafix.core.effect_registry import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import RealizedGeometry

twist_meta = {
    "auto_center": ParamMeta(kind="bool"),
    "pivot": ParamMeta(kind="vec3", ui_min=-500.0, ui_max=500.0),
    "angle": ParamMeta(kind="float", ui_min=0.0, ui_max=360.0),
    "axis": ParamMeta(kind="choice", choices=("x", "y", "z")),
}


@effect(meta=twist_meta)
def twist(
    inputs: Sequence[RealizedGeometry],
    *,
    auto_center: bool = True,
    pivot: tuple[float, float, float] = (0.0, 0.0, 0.0),
    angle: float = 60.0,
    axis: str = "y",
) -> RealizedGeometry:
    """位置に応じて軸回りにねじる（中心付近は 0）。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        入力実体ジオメトリ列。通常は 1 要素。
    auto_center : bool, default True
        True なら平均座標を回転中心に使用。False なら `pivot` を使用。
    pivot : tuple[float, float, float], default (0.0,0.0,0.0)
        ねじり軸（指定軸に平行な直線）の通過点（`auto_center=False` のとき有効）。
    angle : float, default 60.0
        最大ねじれ角 [deg]。
    axis : str, default "y"
        ねじれ軸（`"x"|"y"|"z"`）。

    Returns
    -------
    RealizedGeometry
        ねじり適用後の実体ジオメトリ。
    """
    if not inputs:
        coords = np.zeros((0, 3), dtype=np.float32)
        offsets = np.zeros((1,), dtype=np.int32)
        return RealizedGeometry(coords=coords, offsets=offsets)

    base = inputs[0]
    if base.coords.shape[0] == 0:
        return base

    max_rad = float(np.deg2rad(float(angle)))
    if max_rad == 0.0:
        return base

    ax = str(axis).lower()
    axis_idx_by_name = {"x": 0, "y": 1, "z": 2}
    axis_idx = axis_idx_by_name.get(ax)
    if axis_idx is None:
        raise ValueError(f"axis は 'x'|'y'|'z' を指定する必要がある: {axis!r}")

    coords = base.coords
    if auto_center:
        center = coords.astype(np.float64, copy=False).mean(axis=0)
    else:
        center = np.array(
            [float(pivot[0]), float(pivot[1]), float(pivot[2])],
            dtype=np.float64,
        )

    lo = float(coords[:, axis_idx].min())
    hi = float(coords[:, axis_idx].max())
    rng = hi - lo
    if rng <= 1e-9:
        return base

    # 各頂点の正規化位置 t = 0..1
    t = (coords[:, axis_idx].astype(np.float64) - lo) / rng
    # -max..+max に分布させる（中心 0）
    twist_rad = (t - 0.5) * 2.0 * max_rad

    c = np.cos(twist_rad)
    s = np.sin(twist_rad)

    out = coords.astype(np.float64, copy=True)
    if ax == "y":
        x = out[:, 0] - float(center[0])
        z = out[:, 2] - float(center[2])
        out[:, 0] = x * c - z * s + float(center[0])
        out[:, 2] = x * s + z * c + float(center[2])
    elif ax == "x":
        y = out[:, 1] - float(center[1])
        z = out[:, 2] - float(center[2])
        out[:, 1] = y * c - z * s + float(center[1])
        out[:, 2] = y * s + z * c + float(center[2])
    else:  # "z"
        x = out[:, 0] - float(center[0])
        y = out[:, 1] - float(center[1])
        out[:, 0] = x * c - y * s + float(center[0])
        out[:, 1] = x * s + y * c + float(center[1])

    return RealizedGeometry(coords=out.astype(np.float32, copy=False), offsets=base.offsets)


__all__ = ["twist"]
