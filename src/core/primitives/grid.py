"""
どこで: `src/primitives/grid.py`。グリッドプリミティブの実体生成。
何を: 線の本数（nx, ny）と center/scale から 1x1 正方形グリッドを線分列として構築する。
なぜ: 背景ガイドやパターン生成の基本要素として再利用できる形で提供するため。
"""

from __future__ import annotations

import numpy as np

from src.core.primitive_registry import primitive
from src.core.realized_geometry import RealizedGeometry
from src.core.parameters.meta import ParamMeta


grid_meta = {
    "nx": ParamMeta(kind="int", ui_min=1, ui_max=500),
    "ny": ParamMeta(kind="int", ui_min=1, ui_max=500),
    "center": ParamMeta(kind="vec3", ui_min=-500.0, ui_max=500.0),
    "scale": ParamMeta(kind="vec3", ui_min=0.0, ui_max=200.0),
}


@primitive(meta=grid_meta)
def grid(
    *,
    nx: int | float = 20,
    ny: int | float = 20,
    center: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> RealizedGeometry:
    """グリッド（縦線 nx 本 + 横線 ny 本）を生成する。

    Parameters
    ----------
    nx : int | float, optional
        縦線の本数。
    ny : int | float, optional
        横線の本数。
    center : tuple[float, float, float], optional
        平行移動ベクトル (cx, cy, cz)。
    scale : tuple[float, float, float], optional
        成分ごとのスケール (sx, sy, sz)。

    Returns
    -------
    RealizedGeometry
        各線が 2 頂点からなるポリライン列としてのグリッド。
    """
    nx_i = int(nx)
    ny_i = int(ny)
    if nx_i < 0 or ny_i < 0:
        raise ValueError("grid の nx/ny は 0 以上である必要がある")

    if nx_i == 0 and ny_i == 0:
        coords = np.zeros((0, 3), dtype=np.float32)
        offsets = np.zeros((1,), dtype=np.int32)
        return RealizedGeometry(coords=coords, offsets=offsets)

    x_coords = (
        np.linspace(-0.5, 0.5, num=nx_i, dtype=np.float32)
        if nx_i > 0
        else np.empty((0,), dtype=np.float32)
    )
    y_coords = (
        np.linspace(-0.5, 0.5, num=ny_i, dtype=np.float32)
        if ny_i > 0
        else np.empty((0,), dtype=np.float32)
    )

    line_count = nx_i + ny_i
    lines = np.empty((line_count, 2, 3), dtype=np.float32)

    cursor = 0
    if nx_i > 0:
        vertical = lines[cursor : cursor + nx_i]
        vertical[:, 0, 0] = x_coords
        vertical[:, 1, 0] = x_coords
        vertical[:, 0, 1] = -0.5
        vertical[:, 1, 1] = 0.5
        vertical[:, :, 2] = 0.0
        cursor += nx_i

    if ny_i > 0:
        horizontal = lines[cursor : cursor + ny_i]
        horizontal[:, 0, 0] = -0.5
        horizontal[:, 1, 0] = 0.5
        horizontal[:, 0, 1] = y_coords
        horizontal[:, 1, 1] = y_coords
        horizontal[:, :, 2] = 0.0

    coords = lines.reshape((-1, 3))
    offsets = np.arange(0, coords.shape[0] + 1, 2, dtype=np.int32)

    try:
        cx, cy, cz = center
    except Exception as exc:
        raise ValueError(
            "grid の center は長さ 3 のシーケンスである必要がある"
        ) from exc
    try:
        sx, sy, sz = scale
    except Exception as exc:
        raise ValueError(
            "grid の scale は長さ 3 のシーケンスである必要がある"
        ) from exc

    cx_f, cy_f, cz_f = float(cx), float(cy), float(cz)
    sx_f, sy_f, sz_f = float(sx), float(sy), float(sz)
    if (cx_f, cy_f, cz_f) != (0.0, 0.0, 0.0) or (sx_f, sy_f, sz_f) != (
        1.0,
        1.0,
        1.0,
    ):
        center_vec = np.array([cx_f, cy_f, cz_f], dtype=np.float32)
        scale_vec = np.array([sx_f, sy_f, sz_f], dtype=np.float32)
        coords = coords * scale_vec + center_vec

    return RealizedGeometry(coords=coords, offsets=offsets)
