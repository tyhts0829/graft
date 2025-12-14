"""
translate エフェクト（平行移動）

- 入力を XYZ のオフセットだけ移動する純関数です。
- オフセットがゼロ、または空ジオメトリの場合はコピーを返す高速経路があります。

パラメータ:
- delta: (dx, dy, dz) [mm]。
"""

from __future__ import annotations

from common.types import Vec3
from engine.core.geometry import Geometry

from .registry import effect

PARAM_META = {
    "delta": {
        "type": "vec3",
        "min": (-500.0, -500.0, -500.0),
        "max": (500.0, 500.0, 500.0),
    },
}


@effect()
def translate(
    g: Geometry,
    *,
    delta: Vec3 = (0.0, 0.0, 0.0),
) -> Geometry:
    """指定ベクトルで平行移動。

    Parameters
    ----------
    g : Geometry
        入力ジオメトリ。
    delta : tuple[float, float, float], default (0.0,0.0,0.0)
        平行移動量 [mm]。全成分 0 または空入力で no-op。
    """
    ox, oy, oz = float(delta[0]), float(delta[1]), float(delta[2])
    if ox == 0.0 and oy == 0.0 and oz == 0.0 or g.is_empty:
        coords, offsets = g.as_arrays(copy=False)
        return Geometry(coords.copy(), offsets.copy())
    return g.translate(ox, oy, oz)


translate.__param_meta__ = PARAM_META

# 後方互換クラスは廃止（関数APIのみ）
