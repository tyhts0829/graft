"""各セグメントへ中点挿入を繰り返し、頂点密度を増やす effect。"""

from __future__ import annotations

from typing import Sequence

import numpy as np
from numba import njit  # type: ignore[import-untyped]

from grafix.core.effect_registry import effect
from grafix.core.realized_geometry import RealizedGeometry
from grafix.core.parameters.meta import ParamMeta

# 旧仕様（from_previous_project/subdivide.py）を踏襲した停止条件/上限。
MAX_SUBDIVISIONS = 10
MIN_SEG_LEN = 0.01
MIN_SEG_LEN_SQ = float(MIN_SEG_LEN * MIN_SEG_LEN)
MAX_TOTAL_VERTICES = 10_000_000

subdivide_meta = {
    "subdivisions": ParamMeta(kind="int", ui_min=0, ui_max=MAX_SUBDIVISIONS),
}


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@effect(meta=subdivide_meta)
def subdivide(
    inputs: Sequence[RealizedGeometry],
    *,
    subdivisions: int = 0,
) -> RealizedGeometry:
    """中点挿入で線を細分化する。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        入力実体ジオメトリ列。通常は 1 要素。
    subdivisions : int, default 0
        細分回数。0 以下は no-op。上限は 10。

    Returns
    -------
    RealizedGeometry
        細分化後の実体ジオメトリ。

    Notes
    -----
    旧仕様踏襲:
    - 初期状態で最短セグメント長が `MIN_SEG_LEN` 未満なら、そのポリラインは細分化しない。
    - 細分化の途中で最短セグメント長が `MIN_SEG_LEN` 未満になった場合、そこで反復を停止する。
    - 出力合計頂点数が `MAX_TOTAL_VERTICES` を超えないようにガードする。
    """
    if not inputs:
        return _empty_geometry()

    base = inputs[0]
    coords = base.coords
    offsets = base.offsets
    if coords.shape[0] == 0:
        return base

    divisions = int(subdivisions)
    if divisions <= 0:
        return base
    if divisions > MAX_SUBDIVISIONS:
        divisions = MAX_SUBDIVISIONS
    if divisions <= 0:
        return base

    n_lines = int(offsets.size) - 1
    if n_lines <= 0:
        return base

    out_lines: list[np.ndarray] = []
    total_vertices = 0
    for li in range(n_lines):
        s = int(offsets[li])
        e = int(offsets[li + 1])
        vertices = coords[s:e]

        base_n = int(vertices.shape[0])
        remaining = MAX_TOTAL_VERTICES - total_vertices
        if remaining <= 0 or remaining < base_n:
            break

        subdivided = _subdivide_core(vertices, divisions, int(remaining))
        out_lines.append(subdivided)
        total_vertices += int(subdivided.shape[0])

    if not out_lines:
        return _empty_geometry()

    offsets_out = np.zeros((len(out_lines) + 1,), dtype=np.int32)
    acc = 0
    coords_list: list[np.ndarray] = []
    for i, line in enumerate(out_lines):
        line32 = np.asarray(line, dtype=np.float32)
        coords_list.append(line32)
        acc += int(line32.shape[0])
        offsets_out[i + 1] = acc

    coords_out = (
        np.concatenate(coords_list, axis=0) if coords_list else np.zeros((0, 3), dtype=np.float32)
    )
    return RealizedGeometry(coords=coords_out, offsets=offsets_out)


@njit(fastmath=True, cache=True)
def _subdivide_core(vertices: np.ndarray, subdivisions: int, max_vertices: int) -> np.ndarray:
    """単一頂点配列の細分化処理（旧仕様踏襲の Numba 経路）。"""
    n0 = vertices.shape[0]
    if n0 < 2 or subdivisions <= 0:
        return vertices

    d0 = vertices[1:] - vertices[:-1]
    if d0.shape[0] > 0:
        dsq0 = d0[:, 0] * d0[:, 0] + d0[:, 1] * d0[:, 1] + d0[:, 2] * d0[:, 2]
        if np.min(dsq0) < MIN_SEG_LEN_SQ:  # type: ignore[operator]
            return vertices

    subdivisions = subdivisions if subdivisions <= MAX_SUBDIVISIONS else MAX_SUBDIVISIONS

    result = vertices.copy()
    for _ in range(subdivisions):
        n = result.shape[0]
        if n < 2:
            break

        new_n = 2 * n - 1
        if max_vertices > 0 and new_n > max_vertices:
            break

        new_vertices = np.empty((new_n, result.shape[1]), dtype=result.dtype)
        new_vertices[::2] = result
        new_vertices[1::2] = (result[:-1] + result[1:]) / 2
        result = new_vertices

        d = result[1:] - result[:-1]
        if d.shape[0] > 0:
            dsq = d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1] + d[:, 2] * d[:, 2]
            if np.min(dsq) < MIN_SEG_LEN_SQ:  # type: ignore[operator]
                break

    return result
