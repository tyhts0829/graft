"""ポリラインの全長に対する正規化位置で区間を切り出し、指定部分だけを残す effect。"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from grafix.core.effect_registry import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import RealizedGeometry

trim_meta = {
    "start_param": ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
    "end_param": ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
}


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _clamp01(value: float) -> float:
    v = float(value)
    if not np.isfinite(v):
        return 0.0
    if v <= 0.0:
        return 0.0
    if v >= 1.0:
        return 1.0
    return v


def _lines_to_realized(lines: Sequence[np.ndarray]) -> RealizedGeometry:
    if not lines:
        return _empty_geometry()

    offsets: list[int] = [0]
    coords_list: list[np.ndarray] = []
    cursor = 0
    for line in lines:
        v = np.asarray(line)
        if v.ndim != 2 or v.shape[1] != 3:
            raise ValueError("trim: polyline は shape (N,3) が必要")
        coords_list.append(v.astype(np.float32, copy=False))
        cursor += int(v.shape[0])
        offsets.append(cursor)

    coords = np.concatenate(coords_list, axis=0) if coords_list else np.zeros((0, 3), np.float32)
    return RealizedGeometry(coords=coords, offsets=np.asarray(offsets, dtype=np.int32))


def _build_arc_length(vertices: np.ndarray) -> np.ndarray:
    n = int(vertices.shape[0])
    distances = np.empty((n,), dtype=np.float64)
    distances[0] = 0.0
    for i in range(n - 1):
        d = vertices[i + 1].astype(np.float64) - vertices[i].astype(np.float64)
        distances[i + 1] = distances[i] + float(np.linalg.norm(d))
    return distances


def _interpolate_at_distance(
    vertices: np.ndarray,
    distances: np.ndarray,
    target_dist: float,
) -> np.ndarray | None:
    if target_dist <= 0.0:
        return vertices[0]
    total = float(distances[-1])
    if target_dist >= total:
        return vertices[-1]

    for i in range(int(distances.shape[0]) - 1):
        d0 = float(distances[i])
        d1 = float(distances[i + 1])
        if d0 <= target_dist <= d1:
            seg_len = d1 - d0
            if seg_len == 0.0:
                return vertices[i]
            t = (target_dist - d0) / seg_len
            return vertices[i] + float(t) * (vertices[i + 1] - vertices[i])
    return None


def _trim_polyline(vertices: np.ndarray, start_param: float, end_param: float) -> np.ndarray | None:
    if vertices.shape[0] < 2:
        return vertices

    distances = _build_arc_length(vertices)
    total_length = float(distances[-1])
    if total_length == 0.0:
        return vertices

    start_dist = float(start_param) * total_length
    end_dist = float(end_param) * total_length

    trimmed_vertices: list[np.ndarray] = []
    start_point = _interpolate_at_distance(vertices, distances, start_dist)
    if start_point is not None:
        trimmed_vertices.append(start_point)

    for i in range(int(distances.shape[0])):
        dist_val = float(distances[i])
        if start_dist < dist_val < end_dist:
            trimmed_vertices.append(vertices[i])

    end_point = _interpolate_at_distance(vertices, distances, end_dist)
    if end_point is not None and (
        not trimmed_vertices or not np.allclose(trimmed_vertices[-1], end_point)
    ):
        trimmed_vertices.append(end_point)

    if len(trimmed_vertices) < 2:
        return None
    return np.asarray(trimmed_vertices, dtype=np.float32)


@effect(meta=trim_meta)
def trim(
    inputs: Sequence[RealizedGeometry],
    *,
    start_param: float = 0.1,
    end_param: float = 0.5,
) -> RealizedGeometry:
    """ポリライン列を正規化弧長の区間でトリムする。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        入力実体ジオメトリ列。通常は 1 要素。
    start_param : float, default 0.1
        開始位置（0.0–1.0）。
    end_param : float, default 0.5
        終了位置（0.0–1.0）。`start_param` より大きい値を指定。

    Returns
    -------
    RealizedGeometry
        トリム後の実体ジオメトリ。

    Notes
    -----
    旧仕様踏襲:
    - `start_param >= end_param` は no-op（入力を返す）。
    - トリム後に 2 点未満になる線は捨てる。
    - ただし、全線が捨てられた場合は no-op（入力を返す）。
    """
    if not inputs:
        return _empty_geometry()

    base = inputs[0]
    coords = base.coords
    offsets = base.offsets
    if coords.shape[0] == 0:
        return base

    sp = _clamp01(float(start_param))
    ep = _clamp01(float(end_param))
    if sp >= ep:
        return base

    results: list[np.ndarray] = []
    n_lines = int(offsets.size) - 1
    for i in range(n_lines):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        line = coords[s:e]
        if line.shape[0] < 2:
            results.append(line)
            continue

        trimmed = _trim_polyline(line, sp, ep)
        if trimmed is not None and trimmed.shape[0] >= 2:
            results.append(trimmed)

    if not results:
        return base

    out = _lines_to_realized(results)
    if out.coords.shape[0] == 0 and base.coords.shape[0] != 0:
        return base
    return out


__all__ = ["trim", "trim_meta"]

