"""circle プリミティブのポリライン形状に関するテスト群。"""

from __future__ import annotations

import numpy as np

from src.core.geometry import Geometry
from src.core.realize import realize
from src.primitives import circle as _circle_module  # noqa: F401


def test_circle_polyline_is_closed() -> None:
    """開始点を終端に重ねた閉じたポリラインになる。"""
    segments = 8
    g = Geometry.create("circle", params={"r": 1.0, "segments": segments})

    realized = realize(g)

    assert realized.coords.shape == (segments + 1, 3)
    assert realized.offsets.tolist() == [0, segments + 1]
    np.testing.assert_array_equal(realized.coords[0], realized.coords[-1])
