"""
extrude エフェクト（押し出し）

- 入力ポリラインを指定方向へ距離 `distance` だけ平行移動した「複製線」を作り、
  元の線との対応頂点をエッジで接続して側面線群を形成します。
- 複製線にスケールを適用でき、事前細分化でエッジ密度を増やすことができます。

主なパラメータ:
- direction: 押し出し方向ベクトル。
- distance: 実距離 [mm]（0–200）。
- scale: 複製線のスケール係数（0–3）。
- subdivisions: 細分回数（0–5）。
- center_mode: 'auto' は複製線の重心基準、'origin' は原点基準でスケール。

注意:
- 入力各線の頂点数が増えるため、描画コストが上がります。
"""

from __future__ import annotations

from typing import Literal

import numpy as np

from common.types import Vec3
from engine.core.geometry import Geometry

from .registry import effect

MAX_DISTANCE = 200.0
MAX_SCALE = 3.0
MAX_SUBDIVISIONS = 8

PARAM_META = {
    "direction": {"type": "vec3"},
    "distance": {"type": "number", "min": 0.0, "max": MAX_DISTANCE},
    "scale": {"type": "number", "min": 0.0, "max": MAX_SCALE},
    "subdivisions": {"type": "integer", "min": 0, "max": MAX_SUBDIVISIONS, "step": 1},
    "center_mode": {"choices": ["origin", "auto"]},
}


@effect()
def extrude(
    g: Geometry,
    *,
    direction: Vec3 = (0.0, 0.0, 0.0),
    distance: float = 10.0,
    scale: float = 0.5,
    subdivisions: int = 4,
    center_mode: Literal["origin", "auto"] = "auto",
) -> Geometry:
    """2D/3Dポリラインを指定方向に押し出し、側面エッジを生成。

    Parameters
    ----------
    g : Geometry
        入力ジオメトリ。
    direction : tuple[float, float, float], default (0.0,0.0,0.0)
        押し出し方向ベクトル。
    distance : float, default 10.0
        実距離 [mm]。0 で平行移動なし。
    scale : float, default 0.5
        複製線のスケール係数。
    subdivisions : int, default 4
        細分回数（0–8 にクランプ）。
    center_mode : {'origin','auto'}, default 'auto'
        スケールの中心。
    """
    coords, offsets = g.as_arrays(copy=False)
    if g.is_empty or offsets.size < 2:
        return Geometry(coords.copy(), offsets.copy())

    distance_scaled = max(0.0, min(MAX_DISTANCE, float(distance)))
    scale_scaled = max(0.0, min(MAX_SCALE, float(scale)))
    subdivisions_int = int(subdivisions)
    if subdivisions_int < 0:
        subdivisions_int = 0
    if subdivisions_int > MAX_SUBDIVISIONS:
        subdivisions_int = MAX_SUBDIVISIONS

    direction_vec = np.asarray(direction, dtype=np.float32)
    norm = float(np.linalg.norm(direction_vec))
    if norm < 1e-9 or distance_scaled == 0.0:
        # 平行移動ベクトルはゼロにし、スケールのみ適用（NaN 汚染を避ける）
        extrude_vec = np.zeros(3, dtype=np.float32)
    else:
        extrude_vec = direction_vec * np.float32(distance_scaled / norm)

    # 入力ラインを抽出
    lines: list[np.ndarray] = []
    for i in range(len(offsets) - 1):
        start, end = int(offsets[i]), int(offsets[i + 1])
        line = coords[start:end]
        if len(line) >= 2:
            lines.append(line.astype(np.float32, copy=False))

    # 細分化
    if subdivisions_int > 0:
        subdivided: list[np.ndarray] = []
        for line in lines:
            current = line
            for _ in range(subdivisions_int):
                if len(current) < 2:
                    break
                new_vertices = [current[0]]
                for j in range(len(current) - 1):
                    mid = (current[j] + current[j + 1]) / 2
                    new_vertices.append(mid)
                    new_vertices.append(current[j + 1])
                current = np.asarray(new_vertices, dtype=np.float32)
            subdivided.append(current)
        lines = subdivided

    # 元のライン + 押し出しライン + 接続エッジを構築
    out_lines: list[np.ndarray] = []
    out_lines.extend(lines)

    for line in lines:
        extruded_base = line + extrude_vec
        if center_mode == "auto":
            centroid = extruded_base.mean(axis=0)
            extruded_line = (extruded_base - centroid) * np.float32(scale_scaled) + centroid
        else:
            extruded_line = extruded_base * np.float32(scale_scaled)
        out_lines.append(extruded_line.astype(np.float32, copy=False))
        for j in range(len(line)):
            # 退化（ゼロ長）接続エッジは追加しない
            if not np.allclose(line[j], extruded_line[j], atol=1e-8):
                seg = np.asarray([line[j], extruded_line[j]], dtype=np.float32)
                out_lines.append(seg)

    if not out_lines:
        return Geometry(coords.copy(), offsets.copy())

    new_coords = np.vstack(out_lines).astype(np.float32, copy=False)
    new_offsets_list: list[int] = [0]
    vertex_count = 0
    for ln in out_lines:
        vertex_count += len(ln)
        new_offsets_list.append(vertex_count)
    new_offsets = np.asarray(new_offsets_list, dtype=np.int32)

    return Geometry(new_coords, new_offsets)


# UI 表示のためのメタ情報（RangeHint 構築に使用）
extrude.__param_meta__ = PARAM_META
