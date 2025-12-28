"""座標にスケールを適用する effect。"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from grafix.core.effect_registry import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import RealizedGeometry

_CLOSED_ATOL = 1e-6

scale_meta = {
    "mode": ParamMeta(kind="choice", choices=("all", "by_line", "by_face")),
    "auto_center": ParamMeta(kind="bool"),
    "pivot": ParamMeta(kind="vec3", ui_min=-100.0, ui_max=100.0),
    "scale": ParamMeta(kind="vec3", ui_min=0.0, ui_max=10.0),
}


def _is_closed_polyline(vertices: np.ndarray) -> bool:
    if vertices.shape[0] < 2:
        return False
    return bool(np.allclose(vertices[0], vertices[-1], rtol=0.0, atol=_CLOSED_ATOL))


@effect(meta=scale_meta)
def scale(
    inputs: Sequence[RealizedGeometry],
    *,
    mode: str = "all",
    auto_center: bool = True,
    pivot: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> RealizedGeometry:
    """スケール変換を適用（auto_center 対応）。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        スケール対象の実体ジオメトリ列。通常は 1 要素。
    mode : {"all","by_line","by_face"}, default "all"
        `"all"` は入力全体を 1 つの中心でスケールする。
        `"by_line"` は開ポリラインごとに中心を維持してスケールする（閉曲線は対象外）。
        `"by_face"` は閉曲線ごとに中心を維持してスケールする（開ポリラインは対象外）。
    auto_center : bool, default True
        True なら平均座標を中心に使用。False なら `pivot` を使用（`mode="all"` のときのみ有効）。
    pivot : tuple[float, float, float], default (0.0,0.0,0.0)
        変換の中心（`mode="all"` かつ `auto_center=False` のとき有効）。
    scale : tuple[float, float, float], default (1.0,1.0,1.0)
        各軸の倍率。

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
    if base.coords.shape[0] == 0:
        return base

    mode_s = str(mode)
    if mode_s not in {"all", "by_line", "by_face"}:
        return base

    sx, sy, sz = float(scale[0]), float(scale[1]), float(scale[2])
    factors = np.array([sx, sy, sz], dtype=np.float64)

    if mode_s == "all":
        # 中心を決定（auto_center 優先）
        if auto_center:
            center = base.coords.astype(np.float64, copy=False).mean(axis=0)
        else:
            center = np.array(
                [float(pivot[0]), float(pivot[1]), float(pivot[2])],
                dtype=np.float64,
            )

        shifted = base.coords.astype(np.float64, copy=False) - center
        scaled = shifted * factors + center
        coords = scaled.astype(np.float32, copy=False)
        return RealizedGeometry(coords=coords, offsets=base.offsets)

    coords64 = base.coords.astype(np.float64, copy=True)
    offsets = base.offsets
    for i in range(int(offsets.size) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        if e <= s:
            continue

        v = coords64[s:e]
        is_closed = _is_closed_polyline(v)

        if mode_s == "by_line":
            if is_closed:
                continue
            center = v.mean(axis=0)
        else:  # mode_s == "by_face"
            if not is_closed:
                continue
            center = v[:-1].mean(axis=0)

        coords64[s:e] = (v - center) * factors + center

    coords = coords64.astype(np.float32, copy=False)
    return RealizedGeometry(coords=coords, offsets=offsets)
