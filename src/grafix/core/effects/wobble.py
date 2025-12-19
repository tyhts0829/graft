"""ポリラインの各頂点をサイン波でゆらし、手書き風のたわみを加える effect。"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from grafix.core.effect_registry import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import RealizedGeometry

wobble_meta = {
    "amplitude": ParamMeta(kind="vec3", ui_min=0.0, ui_max=20.0),
    "frequency": ParamMeta(kind="vec3", ui_min=0.0, ui_max=0.2),
    "phase": ParamMeta(kind="float", ui_min=0.0, ui_max=360.0),
}


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@effect(meta=wobble_meta)
def wobble(
    inputs: Sequence[RealizedGeometry],
    *,
    amplitude: tuple[float, float, float] = (2.0, 2.0, 2.0),
    frequency: tuple[float, float, float] = (0.1, 0.1, 0.1),
    phase: float = 0.0,
) -> RealizedGeometry:
    """各頂点へサイン波由来の変位を加える。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        変形対象の実体ジオメトリ列。通常は 1 要素。
    amplitude : tuple[float, float, float], default (2.0, 2.0, 2.0)
        変位量 [mm] 相当（各軸別）。
    frequency : tuple[float, float, float], default (0.1, 0.1, 0.1)
        空間周波数（各軸別）。
    phase : float, default 0.0
        位相 [deg]。

    Returns
    -------
    RealizedGeometry
        変形後の実体ジオメトリ。
    """
    if not inputs:
        return _empty_geometry()

    base = inputs[0]
    if base.coords.shape[0] == 0:
        return base

    ax = float(amplitude[0])
    ay = float(amplitude[1])
    az = float(amplitude[2])
    if ax == 0.0 and ay == 0.0 and az == 0.0:
        return base

    fx = float(frequency[0])
    fy = float(frequency[1])
    fz = float(frequency[2])
    phase_rad = float(np.deg2rad(float(phase)))

    v = base.coords.astype(np.float64, copy=False)
    out = v.copy()

    x = v[:, 0]
    y = v[:, 1]
    z = v[:, 2]
    out[:, 0] = x + ax * np.sin(2.0 * np.pi * fx * x + phase_rad)
    out[:, 1] = y + ay * np.sin(2.0 * np.pi * fy * y + phase_rad)
    out[:, 2] = z + az * np.sin(2.0 * np.pi * fz * z + phase_rad)

    coords = out.astype(np.float32, copy=False)
    return RealizedGeometry(coords=coords, offsets=base.offsets)


__all__ = ["wobble", "wobble_meta"]
