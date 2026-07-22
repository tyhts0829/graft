"""project-local primitive を定義する example。"""

from __future__ import annotations

import numpy as np

from grafix import primitive, run
from grafix.api import G
from grafix.core.realized_geometry import GeomTuple
from grafix.core.scene import SceneItem


@primitive(
    meta={
        "size": {
            "kind": "float",
            "ui_min": 10.0,
            "ui_max": 140.0,
            "description": "菱形の頂点間の幅と高さ。",
        },
        "center": {
            "kind": "vec3",
            "ui_min": 0.0,
            "ui_max": 300.0,
            "description": "菱形の中心座標 (x, y, z)。",
        },
    }
)
def diamond(
    *,
    size: float = 80.0,
    center: tuple[float, float, float] = (150.0, 150.0, 0.0),
) -> GeomTuple:
    """指定中心の菱形を生成する。"""

    half = float(size) * 0.5
    cx, cy, cz = (float(value) for value in center)
    coords = np.asarray(
        [
            (cx, cy - half, cz),
            (cx + half, cy, cz),
            (cx, cy + half, cz),
            (cx - half, cy, cz),
            (cx, cy - half, cz),
        ],
        dtype=np.float32,
    )
    offsets = np.asarray([0, len(coords)], dtype=np.int32)
    return coords, offsets


def draw(t: float) -> SceneItem:
    """時刻 ``t`` の scene を返す。"""

    _ = t
    # project-local stub は ``python -m grafix stub`` で生成すると補完へ追加される。
    return G.diamond(size=100.0)  # type: ignore[attr-defined]


if __name__ == "__main__":
    run(draw, canvas_size=(300, 300))
