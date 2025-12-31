"""入力ポリラインを複数回描画（複製）し、太線風の見た目を作る effect。"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from grafix.core.effect_registry import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import RealizedGeometry

bold_meta = {
    "count": ParamMeta(kind="int", ui_min=1, ui_max=10),
    "radius": ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
    "seed": ParamMeta(kind="int", ui_min=0, ui_max=2**31 - 1),
}


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _sample_offsets_xy(*, rng: np.random.Generator, n: int, radius: float) -> np.ndarray:
    """半径 radius の一様円盤から (dx, dy) を n 個サンプリングする。"""
    if n <= 0:
        return np.zeros((0, 2), dtype=np.float64)

    u = rng.random(n)
    v = rng.random(n)
    r = float(radius) * np.sqrt(u)
    theta = 2.0 * math.pi * v
    dx = r * np.cos(theta)
    dy = r * np.sin(theta)
    return np.stack([dx, dy], axis=1).astype(np.float64, copy=False)


@effect(meta=bold_meta)
def bold(
    inputs: Sequence[RealizedGeometry],
    *,
    count: int = 5,
    radius: float = 0.5,
    seed: int = 0,
) -> RealizedGeometry:
    """入力を複製して太線風にする。

    同じ線を少しずつずらして複数回描画することで、インクの重なりによる
    太線の見た目を作る。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        入力実体ジオメトリ列。通常は 1 要素。
    count : int, default 5
        出力ストローク数（元の線を 1 本含む）。1 以下で no-op。
    radius : float, default 0.5
        ずらし量の最大半径 [mm] 相当（XY のみ）。0 以下で no-op。
    seed : int, default 0
        ずらし量生成の乱数シード（決定性のため）。

    Returns
    -------
    RealizedGeometry
        複製後の実体ジオメトリ。

    Notes
    -----
    - ずらし量は半径 `radius` の一様円盤からサンプルする。
    - z は維持し、XY の平行移動のみ適用する。
    - 最初の 1 本（k=0）はオフセット 0 で入力と一致する。
    """
    if not inputs:
        return _empty_geometry()

    base = inputs[0]
    if base.coords.shape[0] == 0:
        return base

    copies = int(count)
    if copies <= 1:
        return base

    r = float(radius)
    if not np.isfinite(r) or r <= 0.0:
        return base

    n_vertices = int(base.coords.shape[0])
    n_lines = int(base.offsets.size) - 1
    if n_vertices <= 0 or n_lines <= 0:
        return base

    rng = np.random.default_rng(int(seed))
    offsets_xy = np.zeros((copies, 2), dtype=np.float64)
    offsets_xy[1:] = _sample_offsets_xy(rng=rng, n=copies - 1, radius=r)

    base_coords64 = base.coords.astype(np.float64, copy=False)
    out_coords64 = np.empty((n_vertices * copies, 3), dtype=np.float64)
    for k in range(copies):
        s = k * n_vertices
        e = s + n_vertices
        out_coords64[s:e] = base_coords64
        out_coords64[s:e, 0] += offsets_xy[k, 0]
        out_coords64[s:e, 1] += offsets_xy[k, 1]

    tail = base.offsets[1:].astype(np.int64, copy=False)
    out_offsets = np.empty((n_lines * copies + 1,), dtype=np.int32)
    out_offsets[0] = 0
    for k in range(copies):
        start = 1 + k * n_lines
        end = start + n_lines
        out_offsets[start:end] = (tail + k * n_vertices).astype(np.int32, copy=False)

    coords = out_coords64.astype(np.float32, copy=False)
    return RealizedGeometry(coords=coords, offsets=out_offsets)


__all__ = ["bold", "bold_meta"]
