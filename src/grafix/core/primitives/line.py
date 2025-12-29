"""
どこで: `src/grafix/primitives/line.py`。線分プリミティブの実体生成。
何を: center/length/angle から XY 平面上の線分を構築する。
なぜ: 最小の一次元形状として、他の effect/合成の基礎にするため。
"""

from __future__ import annotations

import math

import numpy as np

from grafix.core.parameters.meta import ParamMeta
from grafix.core.primitive_registry import primitive
from grafix.core.realized_geometry import RealizedGeometry

line_meta = {
    "center": ParamMeta(kind="vec3", ui_min=0.0, ui_max=300.0),
    "length": ParamMeta(kind="float", ui_min=0.0, ui_max=200.0),
    "angle": ParamMeta(kind="float", ui_min=0.0, ui_max=360.0),
}


@primitive(meta=line_meta)
def line(
    *,
    center: tuple[float, float, float] = (0.0, 0.0, 0.0),
    length: float = 1.0,
    angle: float = 0.0,
) -> RealizedGeometry:
    """正規化済み引数から線分を生成する。

    Parameters
    ----------
    center : tuple[float, float, float], optional
        線分中心の座標 (cx, cy, cz)。
    length : float, optional
        線分の長さ。
    angle : float, optional
        回転角 [deg]。0° で +X 方向。

    Returns
    -------
    RealizedGeometry
        2 点の線分としての実体ジオメトリ（offsets=[0,2]）。
    """
    try:
        cx, cy, cz = center
    except Exception as exc:
        raise ValueError(
            "line の center は長さ 3 のシーケンスである必要がある"
        ) from exc

    length_f = float(length)
    angle_deg = float(angle)
    cx_f, cy_f, cz_f = float(cx), float(cy), float(cz)

    half = 0.5 * length_f
    theta = math.radians(angle_deg)
    dx = half * math.cos(theta)
    dy = half * math.sin(theta)

    coords = np.array(
        [
            [cx_f - dx, cy_f - dy, cz_f],
            [cx_f + dx, cy_f + dy, cz_f],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)
