"""ポリラインの全長に対する正規化位置で区間を切り出し、指定部分だけを残す effect。"""

from __future__ import annotations

from typing import Sequence

import numpy as np
from numba import njit  # type: ignore[import-untyped, attr-defined]

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

    coords_list: list[np.ndarray] = []
    offsets = np.zeros((len(lines) + 1,), dtype=np.int32)
    cursor = 0
    for i, line in enumerate(lines):
        v = np.asarray(line)
        if v.ndim != 2 or v.shape[1] != 3:
            raise ValueError("trim: polyline は shape (N,3) が必要")
        coords_list.append(v.astype(np.float32, copy=False))
        cursor += int(v.shape[0])
        offsets[i + 1] = cursor

    coords = np.concatenate(coords_list, axis=0) if coords_list else np.zeros((0, 3), np.float32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _build_arc_length_nb(v: np.ndarray) -> np.ndarray:
    n = v.shape[0]
    s = np.empty(n, dtype=np.float64)
    s[0] = 0.0
    for j in range(n - 1):
        dx = v[j + 1, 0] - v[j, 0]
        dy = v[j + 1, 1] - v[j, 1]
        dz = v[j + 1, 2] - v[j, 2]
        s[j + 1] = s[j] + np.sqrt(dx * dx + dy * dy + dz * dz)
    return s


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _lower_bound(a: np.ndarray, x: float) -> int:
    lo = 0
    hi = a.shape[0]
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _upper_bound(a: np.ndarray, x: float) -> int:
    lo = 0
    hi = a.shape[0]
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    return lo


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _interpolate_at_distance_nb(
    vertices: np.ndarray,
    distances: np.ndarray,
    target_dist: float,
) -> tuple[float, float, float]:
    if target_dist <= 0.0:
        return float(vertices[0, 0]), float(vertices[0, 1]), float(vertices[0, 2])

    total = float(distances[distances.shape[0] - 1])
    if target_dist >= total:
        n1 = vertices.shape[0] - 1
        return float(vertices[n1, 0]), float(vertices[n1, 1]), float(vertices[n1, 2])

    j = _lower_bound(distances, float(target_dist))
    if j <= 0:
        return float(vertices[0, 0]), float(vertices[0, 1]), float(vertices[0, 2])
    if j >= distances.shape[0]:
        n1 = vertices.shape[0] - 1
        return float(vertices[n1, 0]), float(vertices[n1, 1]), float(vertices[n1, 2])

    i = j - 1
    d0 = float(distances[i])
    d1 = float(distances[i + 1])
    seg_len = d1 - d0
    if seg_len == 0.0:
        return float(vertices[i, 0]), float(vertices[i, 1]), float(vertices[i, 2])

    t = (float(target_dist) - d0) / seg_len
    x0 = float(vertices[i, 0])
    y0 = float(vertices[i, 1])
    z0 = float(vertices[i, 2])
    x1 = float(vertices[i + 1, 0])
    y1 = float(vertices[i + 1, 1])
    z1 = float(vertices[i + 1, 2])
    return x0 + t * (x1 - x0), y0 + t * (y1 - y0), z0 + t * (z1 - z0)


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _allclose3(a0: float, a1: float, a2: float, b0: float, b1: float, b2: float) -> bool:
    rtol = 1e-05
    atol = 1e-08
    if np.abs(a0 - b0) > (atol + rtol * np.abs(b0)):
        return False
    if np.abs(a1 - b1) > (atol + rtol * np.abs(b1)):
        return False
    if np.abs(a2 - b2) > (atol + rtol * np.abs(b2)):
        return False
    return True


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _trim_polyline_nb(
    vertices: np.ndarray,
    start_param: float,
    end_param: float,
) -> np.ndarray:
    n = vertices.shape[0]
    distances = _build_arc_length_nb(vertices)
    total = float(distances[n - 1])
    if total == 0.0:
        out = np.empty((n, 3), dtype=np.float32)
        for j in range(n):
            out[j, 0] = vertices[j, 0]
            out[j, 1] = vertices[j, 1]
            out[j, 2] = vertices[j, 2]
        return out

    start_dist = float(start_param) * total
    end_dist = float(end_param) * total

    sx, sy, sz = _interpolate_at_distance_nb(vertices, distances, start_dist)
    ex, ey, ez = _interpolate_at_distance_nb(vertices, distances, end_dist)

    start_i = _upper_bound(distances, start_dist)
    end_i = _lower_bound(distances, end_dist)
    if end_i < start_i:
        interior_count = 0
    else:
        interior_count = end_i - start_i

    last_x = sx
    last_y = sy
    last_z = sz
    if interior_count > 0:
        li = end_i - 1
        last_x = float(vertices[li, 0])
        last_y = float(vertices[li, 1])
        last_z = float(vertices[li, 2])

    add_end = not _allclose3(last_x, last_y, last_z, ex, ey, ez)
    out_n = 1 + interior_count + (1 if add_end else 0)
    if out_n < 2:
        return np.empty((0, 3), dtype=np.float32)

    out = np.empty((out_n, 3), dtype=np.float32)
    out[0, 0] = np.float32(sx)
    out[0, 1] = np.float32(sy)
    out[0, 2] = np.float32(sz)

    for j in range(interior_count):
        src = start_i + j
        dst = 1 + j
        out[dst, 0] = vertices[src, 0]
        out[dst, 1] = vertices[src, 1]
        out[dst, 2] = vertices[src, 2]

    if add_end:
        di = out_n - 1
        out[di, 0] = np.float32(ex)
        out[di, 1] = np.float32(ey)
        out[di, 2] = np.float32(ez)

    return out


def _trim_polyline(vertices: np.ndarray, start_param: float, end_param: float) -> np.ndarray | None:
    if vertices.shape[0] < 2:
        return vertices

    out = _trim_polyline_nb(vertices.astype(np.float32, copy=False), start_param, end_param)
    if out.shape[0] < 2:
        return None
    return out


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
