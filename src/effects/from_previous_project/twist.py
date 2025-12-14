"""
twist エフェクト（位置依存ねじり）

- 指定軸の最小/最大座標で正規化した位置 t∈[0,1] に応じて、
  -max..+max の回転角を割り当ててねじります（中心付近は 0）。

パラメータ:
- angle: 最大ねじれ角（度）。
- axis: 'x' | 'y' | 'z'。

注意:
- 軸方向の範囲が 0 の場合は無効果です。
"""

from __future__ import annotations

import math

import numpy as np

from engine.core.geometry import Geometry

from .registry import effect

PARAM_META = {
    "angle": {"type": "number", "min": 0.0, "max": 360.0},
    "axis": {"choices": ["x", "y", "z"]},
}


@effect()
def twist(g: Geometry, *, angle: float = 60.0, axis: str = "y") -> Geometry:
    """位置に応じて軸回りにねじる。

    Parameters
    ----------
    g : Geometry
        入力ジオメトリ。
    angle : float, default 60.0
        最大ねじれ角 [deg]。0 で no-op。
    axis : str, default 'y'
        ねじれ軸（'x'|'y'|'z'）。
    """
    coords, offsets = g.as_arrays(copy=False)
    if g.is_empty:
        return Geometry(coords.copy(), offsets.copy())

    ax = axis.lower()
    if ax not in ("x", "y", "z"):
        ax = "y"

    out = coords.astype(np.float32, copy=True)

    # 軸方向の範囲を計算
    axis_idx = {"x": 0, "y": 1, "z": 2}[ax]
    lo = float(coords[:, axis_idx].min())
    hi = float(coords[:, axis_idx].max())
    rng = hi - lo
    if rng <= 1e-9:
        return Geometry(out, offsets.copy())

    # 各頂点の正規化位置 t = 0..1
    t = (coords[:, axis_idx] - lo) / rng
    # degree で受け取り radian に変換
    max_rad = math.radians(float(angle))
    # -max..+max に分布させる（中心0）
    twist_rad = (t - 0.5) * 2.0 * max_rad

    # 回転適用
    if ax == "y":
        x = out[:, 0].copy()
        z = out[:, 2].copy()
        c = np.cos(twist_rad)
        s = np.sin(twist_rad)
        out[:, 0] = x * c - z * s
        out[:, 2] = x * s + z * c
    elif ax == "x":
        y = out[:, 1].copy()
        z = out[:, 2].copy()
        c = np.cos(twist_rad)
        s = np.sin(twist_rad)
        out[:, 1] = y * c - z * s
        out[:, 2] = y * s + z * c
    else:  # "z"
        x = out[:, 0].copy()
        y = out[:, 1].copy()
        c = np.cos(twist_rad)
        s = np.sin(twist_rad)
        out[:, 0] = x * c - y * s
        out[:, 1] = x * s + y * c

    return Geometry(out, offsets.copy())


twist.__param_meta__ = PARAM_META
