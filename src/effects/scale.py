from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from src.core.effect_registry import effect
from src.core.realized_geometry import RealizedGeometry


@effect
def scale(
    inputs: Sequence[RealizedGeometry],
    args: tuple[tuple[str, Any], ...],
) -> RealizedGeometry:
    """正規化済み引数を用いてジオメトリをスケールする。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        スケール対象の実体ジオメトリ列。通常は 1 要素。
    args : tuple[tuple[str, Any], ...]
        (名前, 値) の正規化済み引数タプル。

    Returns
    -------
    RealizedGeometry
        スケール後の実体ジオメトリ。
    """
    if not inputs:
        coords = np.zeros((0, 3), dtype=np.float32)
        offsets = np.zeros((1,), dtype=np.int32)
        return RealizedGeometry(coords=coords, offsets=offsets)

    base = inputs[0]
    params = dict(args)

    sx = float(params.get("sx", params.get("s", 1.0)))
    sy = float(params.get("sy", params.get("s", 1.0)))
    sz = float(params.get("sz", 1.0))

    coords = base.coords.copy()
    coords.setflags(write=True)
    coords[:, 0] *= sx
    coords[:, 1] *= sy
    coords[:, 2] *= sz

    return RealizedGeometry(coords=coords, offsets=base.offsets)
