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


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _subdivide_midpoints(vertices: np.ndarray, subdivisions: int) -> np.ndarray:
    """各セグメントへ中点挿入を繰り返す（旧 extrude の仕様踏襲）。"""
    v = vertices
    for _ in range(int(subdivisions)):
        if v.shape[0] < 2:
            break
        n = int(v.shape[0])
        out = np.empty((2 * n - 1, 3), dtype=np.float32)
        out[::2] = v
        out[1::2] = (v[:-1] + v[1:]) / 2.0
        v = out
    return v


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
    norm = float(np.linalg.norm(extrude_vec))
    if norm > MAX_DISTANCE:
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

    lines: list[np.ndarray] = []
    for i in range(int(offsets.size) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        line = coords[s:e]
        if line.shape[0] < 2:
            continue
        v = np.asarray(line, dtype=np.float32)
        if subdivisions_int > 0:
            v = _subdivide_midpoints(v, subdivisions_int)
        lines.append(v)

    if not lines:
        return base

    out_lines: list[np.ndarray] = []
    out_lines.extend(lines)

    use_auto_center = center_mode == "auto"
    scale64 = float(scale_clamped)
    extrude64 = extrude_vec.astype(np.float64, copy=False)

    for line in lines:
        extruded_base = line.astype(np.float64, copy=False) + extrude64
        if use_auto_center:
            centroid = extruded_base.mean(axis=0)
            extruded_line64 = (extruded_base - centroid) * scale64 + centroid
        else:
            extruded_line64 = extruded_base * scale64

        extruded_line = extruded_line64.astype(np.float32, copy=False)
        out_lines.append(extruded_line)

        for j in range(int(line.shape[0])):
            if np.allclose(line[j], extruded_line[j], atol=_CONNECT_ATOL):
                continue
            seg = np.asarray([line[j], extruded_line[j]], dtype=np.float32)
            out_lines.append(seg)

    out_coords = np.concatenate(out_lines, axis=0).astype(np.float32, copy=False)
    out_offsets = np.empty((len(out_lines) + 1,), dtype=np.int32)
    out_offsets[0] = 0
    acc = 0
    for i, line in enumerate(out_lines):
        acc += int(line.shape[0])
        out_offsets[i + 1] = acc

    return RealizedGeometry(coords=out_coords, offsets=out_offsets)
