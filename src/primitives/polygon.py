from __future__ import annotations

from typing import Any

import numpy as np

from engine.core.geometry import Geometry

from .registry import shape


def _polygon_cached(n_sides: int) -> np.ndarray:
    """多角形の頂点配列を生成します。

    引数:
        n_sides: 辺の数。

    返り値:
        頂点配列（最初の頂点を末尾に複製して閉ループ化）。
    """
    # 頂点座標を計算
    t = np.linspace(0, 2 * np.pi, n_sides, endpoint=False)
    x = np.cos(t) * 0.5
    y = np.sin(t) * 0.5
    z = np.zeros_like(x)
    vertices = np.stack([x, y, z], axis=1).astype(np.float32)

    # 最初の頂点を末尾に追加して閉ループにする
    vertices = np.append(vertices, vertices[0:1], axis=0)

    return vertices


@shape
def polygon(n_sides: int | float = 6, *, phase: float = 0.0, **params: Any) -> Geometry:
    """直径 1 の円に内接する正多角形を生成します。

    引数:
        n_sides: 辺の数。
        phase: 頂点開始角（度数法）。0 で +X 軸上に頂点を置く。
    """
    MIN_SIDES = 3
    MAX_SIDES = 120

    sides = int(round(float(n_sides)))
    sides = max(MIN_SIDES, min(MAX_SIDES, sides))

    vertices = _polygon_cached(sides)
    if phase:
        theta = float(phase) * np.pi / 180.0
        cos_t = np.cos(theta)
        sin_t = np.sin(theta)
        x = vertices[:, 0]
        y = vertices[:, 1]
        rotated_x = x * cos_t - y * sin_t
        rotated_y = x * sin_t + y * cos_t
        vertices = vertices.copy()
        vertices[:, 0] = rotated_x
        vertices[:, 1] = rotated_y
    return Geometry.from_lines([vertices])


polygon.__param_meta__ = {
    "n_sides": {"type": "integer", "min": 3, "max": 120, "step": 1},
    "phase": {"type": "number", "min": 0.0, "max": 360.0, "step": 1.0},
}
