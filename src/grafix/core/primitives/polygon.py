"""
どこで: `src/grafix/primitives/polygon.py`。正多角形プリミティブの実体生成。
何を: 辺数・位相・center/scale から正多角形ポリラインを構築する。
なぜ: プレビューとエクスポートで再利用できる基本図形を提供するため。
"""

from __future__ import annotations

import math

import numpy as np

from grafix.core.parameters.meta import ParamMeta
from grafix.core.primitive_registry import primitive
from grafix.core.realized_geometry import RealizedGeometry

polygon_meta = {
    "n_sides": ParamMeta(kind="int", ui_min=3, ui_max=128),
    "phase": ParamMeta(kind="float", ui_min=0.0, ui_max=360.0),
    "center": ParamMeta(kind="vec3", ui_min=0.0, ui_max=300.0),
    "scale": ParamMeta(kind="float", ui_min=0.0, ui_max=200.0),
}


@primitive(meta=polygon_meta)
def polygon(
    *,
    n_sides: int | float = 6,
    phase: float = 0.0,
    center: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: float = 1.0,
) -> RealizedGeometry:
    """正多角形の閉ポリラインを生成する。

    Parameters
    ----------
    n_sides : int | float, optional
        辺の数。3 未満は 3 にクランプする。
    phase : float, optional
        頂点開始角 [deg]。0° で +X 軸上に頂点を置く。
    center : tuple[float, float, float], optional
        平行移動ベクトル (cx, cy, cz)。
    scale : float, optional
        等方スケール倍率 s。縦横比変更は effect を使用する。

    Returns
    -------
    RealizedGeometry
        開始点を終端に重ねた閉じたポリラインとしての正多角形。
    """
    sides = int(round(float(n_sides)))
    if sides < 3:
        sides = 3

    phase_deg = float(phase)

    try:
        cx, cy, cz = center
    except Exception as exc:
        raise ValueError(
            "polygon の center は長さ 3 のシーケンスである必要がある"
        ) from exc
    try:
        s_f = float(scale)
    except Exception as exc:
        raise ValueError("polygon の scale は float である必要がある") from exc

    angles = np.linspace(
        0.0,
        2.0 * math.pi,
        num=sides,
        endpoint=False,
        dtype=np.float32,
    )
    if phase_deg != 0.0:
        angles = angles + np.deg2rad(np.float32(phase_deg))

    radius = np.float32(0.5)
    x = radius * np.cos(angles, dtype=np.float32)
    y = radius * np.sin(angles, dtype=np.float32)
    z = np.zeros_like(x)

    s32 = np.float32(s_f)
    x = x * s32 + np.float32(cx)
    y = y * s32 + np.float32(cy)
    z = z * s32 + np.float32(cz)

    coords = np.stack([x, y, z], axis=1).astype(np.float32, copy=False)
    # 先頭頂点を終端に複製してポリラインを閉じる。
    coords = np.concatenate([coords, coords[:1]], axis=0)
    offsets = np.array([0, coords.shape[0]], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)
