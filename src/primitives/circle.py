from __future__ import annotations

import math
from typing import Any

import numpy as np

from src.core.primitive_registry import primitive
from src.core.realized_geometry import RealizedGeometry


@primitive
def circle(args: tuple[tuple[str, Any], ...]) -> RealizedGeometry:
    """正規化済み引数から円のポリラインを生成する。

    Parameters
    ----------
    args : tuple[tuple[str, Any], ...]
        (名前, 値) の正規化済み引数タプル。

    Returns
    -------
    RealizedGeometry
        単一ポリラインとしての円。
    """
    params = dict(args)
    r = float(params.get("r", 1.0))
    cx = float(params.get("cx", 0.0))
    cy = float(params.get("cy", 0.0))
    segments = int(params.get("segments", 64))
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
    offsets = np.array([0, coords.shape[0]], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)
