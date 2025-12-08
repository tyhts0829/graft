"""
どこで: `src/primitives/circle.py`。円プリミティブの実体生成。
何を: 中心・半径・分割数から円ポリラインを構築する。
なぜ: プレビューとエクスポートで再利用できる基本図形を提供するため。
"""

from __future__ import annotations

import math

import numpy as np

from src.core.primitive_registry import primitive
from src.core.realized_geometry import RealizedGeometry


@primitive
def circle(
    *,
    r: float = 1.0,
    cx: float = 0.0,
    cy: float = 0.0,
    segments: int = 64,
) -> RealizedGeometry:
    """正規化済み引数から円のポリラインを生成する。

    Parameters
    ----------
    r : float, optional
        半径。
    cx : float, optional
        中心の x 座標。
    cy : float, optional
        中心の y 座標。
    segments : int, optional
        近似に用いる分割数（線分数）。

    Returns
    -------
    RealizedGeometry
        開始点を終端に重ねた閉じたポリラインとしての円。
    """
    r = float(r)
    cx = float(cx)
    cy = float(cy)
    segments = int(segments)
    if segments < 3:
        raise ValueError("circle の segments は 3 以上である必要がある")

    angles = np.linspace(
        0.0,
        2.0 * math.pi,
        num=segments,
        endpoint=False,
        dtype=np.float32,
    )
    x = cx + r * np.cos(angles, dtype=np.float32)
    y = cy + r * np.sin(angles, dtype=np.float32)
    coords = np.stack([x, y, np.zeros_like(x)], axis=1).astype(
        np.float32,
        copy=False,
    )
    # 先頭頂点を終端に複製してポリラインを閉じる。
    coords = np.concatenate([coords, coords[:1]], axis=0)
    offsets = np.array([0, coords.shape[0]], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)
