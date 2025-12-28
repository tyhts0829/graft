"""座標をグリッドへ量子化（スナップ）する effect。"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from grafix.core.effect_registry import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import RealizedGeometry

quantize_meta = {
    "step": ParamMeta(kind="vec3", ui_min=0.0, ui_max=10.0),
}


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _round_half_away_from_zero(values: np.ndarray) -> np.ndarray:
    """0.5 境界を絶対値方向へ丸める（half away from zero）。"""
    return np.sign(values) * np.floor(np.abs(values) + 0.5)


@effect(meta=quantize_meta)
def quantize(
    inputs: Sequence[RealizedGeometry],
    *,
    step: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> RealizedGeometry:
    """頂点座標を各軸のステップ幅で量子化する（XYZ）。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        入力実体ジオメトリ列。通常は 1 要素。
    step : tuple[float, float, float], default (1.0, 1.0, 1.0)
        各軸の格子間隔 (sx, sy, sz)。いずれかが 0 以下なら no-op。

    Returns
    -------
    RealizedGeometry
        量子化後の実体ジオメトリ（頂点数と offsets は維持）。

    Notes
    -----
    丸め規則は half away from zero:
    - +0.5 は +1 側
    - -0.5 は -1 側
    """
    if not inputs:
        return _empty_geometry()

    base = inputs[0]
    if base.coords.shape[0] == 0:
        return base

    sx, sy, sz = float(step[0]), float(step[1]), float(step[2])
    if sx <= 0.0 or sy <= 0.0 or sz <= 0.0:
        return base

    step_vec = np.array([sx, sy, sz], dtype=np.float64)
    coords64 = base.coords.astype(np.float64, copy=False)
    q = coords64 / step_vec
    q_rounded = _round_half_away_from_zero(q)
    snapped64 = q_rounded * step_vec
    coords_out = snapped64.astype(np.float32, copy=False)
    return RealizedGeometry(coords=coords_out, offsets=base.offsets)
