"""
wobble エフェクト（ゆらぎ）

- サイン波によるゆらぎを各頂点へ加え、線を手書き風にたわませます。
- 実装は各軸ごとの `sin(2π f * axis + 位相)` を加算する方式で、2D/3D に対応します。

パラメータ:
- amplitude [mm], frequency (float または Vec3), phase [deg]。
"""

from __future__ import annotations

import numpy as np

from common.param_utils import ensure_vec3
from common.types import Vec3
from engine.core.geometry import Geometry

from .registry import effect

PARAM_META = {
    "amplitude": {"type": "number", "min": 0.0, "max": 20.0},
    "frequency": {
        "type": "number",
        "min": (0.0, 0.0, 0.0),
        "max": (0.2, 0.2, 0.2),
    },
    "phase": {"type": "number", "min": 0.0, "max": 360.0},
}


@effect()
def wobble(
    g: Geometry,
    *,
    amplitude: float = 2.0,
    frequency: float | Vec3 = (0.1, 0.1, 0.1),
    phase: float = 0.0,
) -> Geometry:
    """線にウォブル/波の歪みを追加（純関数）。

    既定値の方針（2025-09-06 更新・再調整）:
        - amplitude=2.5mm, frequency=0.02, phase=0.0 を既定とし、
          300mm 正方キャンバス中央の立方体に適用したときに「効果が分かる最小限」を狙う。

    引数:
        amplitude: 変位量（座標単位, mm 相当）。
        frequency: 空間周波数 [cycles per unit]。float なら全軸同一、タプルは (fx, fy, fz)。
        phase: 位相 [deg]。
    """
    coords, offsets = g.as_arrays(copy=False)

    # frequency をタプルに整形（係数スケーリングは廃止）
    if isinstance(frequency, (int, float)):
        f = float(frequency)
        freq_tuple = (f, f, f)
    elif isinstance(frequency, (list, tuple)):
        fx, fy, fz = ensure_vec3(tuple(float(x) for x in frequency))
        freq_tuple = (fx, fy, fz)
    else:
        freq_tuple = (0.1, 0.1, 0.1)

    if len(coords) == 0:
        return Geometry(coords.copy(), offsets.copy())

    vertices_list = [coords[offsets[i] : offsets[i + 1]] for i in range(len(offsets) - 1)]
    phase_rad = float(np.deg2rad(phase))
    wobbled_vertices = _wobble_vertices(vertices_list, float(amplitude), freq_tuple, phase_rad)
    if not wobbled_vertices:
        return Geometry(coords.copy(), offsets.copy())
    return Geometry.from_lines(wobbled_vertices)


wobble.__param_meta__ = PARAM_META


def _wobble_vertices(
    vertices_list: list[np.ndarray], amplitude: float, frequency: Vec3, phase: float
) -> list[np.ndarray]:
    """各頂点に対してサイン波によるゆらぎ（wobble）を加える内部関数。"""
    new_vertices_list = []
    for vertices in vertices_list:
        if len(vertices) == 0:
            new_vertices_list.append(vertices)
            continue

        new_vertices = vertices.astype(np.float32).copy()
        # ベクトル化された計算
        # x軸方向のゆらぎ
        new_vertices[:, 0] += amplitude * np.sin(
            2 * np.pi * frequency[0] * new_vertices[:, 0] + phase
        )
        # y軸方向のゆらぎ
        new_vertices[:, 1] += amplitude * np.sin(
            2 * np.pi * frequency[1] * new_vertices[:, 1] + phase
        )
        # z軸方向のゆらぎ（2D の場合は 0 のまま）
        if new_vertices.shape[1] > 2:
            new_vertices[:, 2] += amplitude * np.sin(
                2 * np.pi * frequency[2] * new_vertices[:, 2] + phase
            )
        new_vertices_list.append(new_vertices)
    return new_vertices_list
