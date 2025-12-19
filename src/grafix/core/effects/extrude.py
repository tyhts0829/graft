"""ポリラインを指定方向へ押し出し、複製線と側面エッジを生成する effect。

- 入力ポリラインを `delta` だけ平行移動した「複製線」を作る。
- 複製線に `scale` を適用できる（`center_mode` で中心を切り替え）。
- 元線と複製線の対応頂点を 2 点ポリラインで接続し、側面エッジ群を生成する。
- `subdivisions` により事前に中点挿入で頂点密度を増やせる。

旧仕様踏襲（重要）:
- `delta` の長さ / `scale` / `subdivisions` は上限付きでクランプする。
- `center_mode == "auto"` のときだけ重心中心スケールし、それ以外は原点中心スケール扱いとする。
- 図形に変化が無い引数（delta=(0,0,0) かつ scale=1 かつ subdivisions=0）は no-op として入力を返す。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
from numba import njit  # type: ignore[import-untyped]

from grafix.core.effect_registry import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import RealizedGeometry

MAX_DISTANCE = 200.0
MAX_SCALE = 3.0
MAX_SUBDIVISIONS = 8

extrude_meta = {
    "delta": ParamMeta(kind="vec3", ui_min=-MAX_DISTANCE, ui_max=MAX_DISTANCE),
    "scale": ParamMeta(kind="float", ui_min=0.0, ui_max=MAX_SCALE),
    "subdivisions": ParamMeta(kind="int", ui_min=0, ui_max=MAX_SUBDIVISIONS),
    "center_mode": ParamMeta(kind="choice", choices=("origin", "auto")),
}

_CONNECT_ATOL = 1e-8
_CONNECT_RTOL = 1e-5


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@njit(cache=True)
def _subdivide_midpoints(vertices: np.ndarray, subdivisions: int) -> np.ndarray:
    """各セグメントへ中点挿入を繰り返す（旧 extrude の仕様踏襲、Numba 実装）。"""
    if subdivisions <= 0 or vertices.shape[0] < 2:
        return vertices

    steps = 1 << int(subdivisions)
    n0 = int(vertices.shape[0])
    out_n = (n0 - 1) * steps + 1
    out = np.empty((out_n, 3), dtype=np.float32)

    out_i = 0
    for i in range(n0 - 1):
        ax = float(vertices[i, 0])
        ay = float(vertices[i, 1])
        az = float(vertices[i, 2])
        bx = float(vertices[i + 1, 0])
        by = float(vertices[i + 1, 1])
        bz = float(vertices[i + 1, 2])

        dx = (bx - ax) / float(steps)
        dy = (by - ay) / float(steps)
        dz = (bz - az) / float(steps)

        for t in range(steps):
            ft = float(t)
            out[out_i, 0] = np.float32(ax + dx * ft)
            out[out_i, 1] = np.float32(ay + dy * ft)
            out[out_i, 2] = np.float32(az + dz * ft)
            out_i += 1

    out[out_i, 0] = np.float32(vertices[n0 - 1, 0])
    out[out_i, 1] = np.float32(vertices[n0 - 1, 1])
    out[out_i, 2] = np.float32(vertices[n0 - 1, 2])
    return out


@effect(meta=extrude_meta)
def extrude(
    inputs: Sequence[RealizedGeometry],
    *,
    delta: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: float = 0.5,
    subdivisions: int = 4,
    center_mode: str = "auto",
) -> RealizedGeometry:
    """指定方向に押し出し、複製線と側面エッジを生成する。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        入力実体ジオメトリ列。通常は 1 要素。
    delta : tuple[float, float, float], default (0.0,0.0,0.0)
        押し出し量（dx, dy, dz）[mm]（長さは 0–200 にクランプ）。
    scale : float, default 0.5
        複製線に適用するスケール係数（0–3 にクランプ）。
    subdivisions : int, default 4
        中点挿入の細分回数（0–8 にクランプ）。
    center_mode : str, default "auto"
        "auto" のとき複製線の重心中心でスケールし、それ以外は原点中心でスケールする。

    Returns
    -------
    RealizedGeometry
        押し出し結果（元線・複製線・側面エッジ群を含む）。
    """
    if not inputs:
        return _empty_geometry()

    base = inputs[0]
    if base.coords.shape[0] == 0:
        return base

    scale_clamped = max(0.0, min(MAX_SCALE, float(scale)))

    subdivisions_int = int(subdivisions)
    if subdivisions_int < 0:
        subdivisions_int = 0
    if subdivisions_int > MAX_SUBDIVISIONS:
        subdivisions_int = MAX_SUBDIVISIONS

    dx, dy, dz = float(delta[0]), float(delta[1]), float(delta[2])
    extrude_vec = np.array([dx, dy, dz], dtype=np.float32)
    norm_sq = dx * dx + dy * dy + dz * dz
    if norm_sq > MAX_DISTANCE * MAX_DISTANCE:
        norm = float(np.sqrt(norm_sq))
        extrude_vec = extrude_vec * np.float32(MAX_DISTANCE / norm)

    if (
        subdivisions_int == 0
        and scale_clamped == 1.0
        and dx == 0.0
        and dy == 0.0
        and dz == 0.0
    ):
        return base

    coords = base.coords
    offsets = base.offsets
    if offsets.size < 2:
        return base

    use_auto_center = center_mode == "auto"
    scale32 = np.float32(scale_clamped)
    is_scale_one = scale_clamped == 1.0
    has_translation = dx != 0.0 or dy != 0.0 or dz != 0.0

    lines: list[np.ndarray] = []
    extruded_lines: list[np.ndarray] = []
    changed_masks: list[np.ndarray | None] = []
    changed_counts: list[int] = []

    # 1st pass: 入力ラインを抽出し、複製線と接続エッジ数を決める。
    for i in range(int(offsets.size) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        line = coords[s:e]
        if line.shape[0] < 2:
            continue

        v = np.asarray(line, dtype=np.float32)
        if subdivisions_int > 0:
            v = _subdivide_midpoints(v, subdivisions_int)

        if is_scale_one:
            v_ex = v + extrude_vec
            if has_translation:
                n_changed = int(v.shape[0])
                mask = None
            else:
                n_changed = 0
                mask = None
        else:
            if use_auto_center:
                centroid = v.mean(axis=0, dtype=np.float64).astype(np.float32, copy=False)
                v_ex = (v - centroid) * scale32 + centroid + extrude_vec
            else:
                v_ex = (v + extrude_vec) * scale32

            close = np.isclose(v, v_ex, rtol=_CONNECT_RTOL, atol=_CONNECT_ATOL)
            mask_all = ~np.all(close, axis=1)
            n_changed = int(mask_all.sum())
            if n_changed == 0 or n_changed == int(v.shape[0]):
                mask = None
            else:
                mask = mask_all

        lines.append(v)
        extruded_lines.append(np.asarray(v_ex, dtype=np.float32))
        changed_masks.append(mask)
        changed_counts.append(int(n_changed))

    n_lines = len(lines)
    if n_lines == 0:
        return base

    total_vertices = 0
    total_edges = 0
    for v, n_changed in zip(lines, changed_counts, strict=True):
        n = int(v.shape[0])
        total_vertices += 2 * n
        total_edges += int(n_changed)

    total_vertices += 2 * total_edges
    total_polylines = 2 * n_lines + total_edges

    out_coords = np.empty((total_vertices, 3), dtype=np.float32)
    out_offsets = np.empty((total_polylines + 1,), dtype=np.int32)
    out_offsets[0] = 0

    # 2nd pass: 旧実装の順序（全 original → 各 line の extruded + edges）で出力を詰める。
    vc = 0
    oc = 0

    for v in lines:
        n = int(v.shape[0])
        out_coords[vc : vc + n] = v
        vc += n
        oc += 1
        out_offsets[oc] = vc

    for v, v_ex, mask, n_changed in zip(
        lines, extruded_lines, changed_masks, changed_counts, strict=True
    ):
        n = int(v.shape[0])
        out_coords[vc : vc + n] = v_ex
        vc += n
        oc += 1
        out_offsets[oc] = vc

        m = int(n_changed)
        if m <= 0:
            continue

        edges_start = int(vc)
        edges_end = edges_start + 2 * m
        edges_view = out_coords[edges_start:edges_end]
        if mask is None and m == n:
            edges_view[0::2] = v
            edges_view[1::2] = v_ex
        else:
            assert mask is not None
            edges_view[0::2] = v[mask]
            edges_view[1::2] = v_ex[mask]

        vc = edges_end
        out_offsets[oc + 1 : oc + m + 1] = edges_start + 2 * np.arange(
            1, m + 1, dtype=np.int32
        )
        oc += m

    return RealizedGeometry(coords=out_coords, offsets=out_offsets)
