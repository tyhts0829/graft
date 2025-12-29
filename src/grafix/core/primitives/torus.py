"""
どこで: `src/grafix/primitives/torus.py`。トーラスプリミティブの実体生成。
何を: major/minor 半径と分割数から、子午線+緯線の閉ポリライン列を構築する。
なぜ: 3D プリミティブの基本形状として、回転や変形 effect の入力に使えるようにするため。
"""

from __future__ import annotations

import math

import numpy as np

from grafix.core.parameters.meta import ParamMeta
from grafix.core.primitive_registry import primitive
from grafix.core.realized_geometry import RealizedGeometry

torus_meta = {
    "major_radius": ParamMeta(kind="float", ui_min=-100.0, ui_max=100.0),
    "minor_radius": ParamMeta(kind="float", ui_min=-100.0, ui_max=100.0),
    "major_segments": ParamMeta(kind="int", ui_min=3, ui_max=256),
    "minor_segments": ParamMeta(kind="int", ui_min=3, ui_max=256),
    "center": ParamMeta(kind="vec3", ui_min=0.0, ui_max=300.0),
    "scale": ParamMeta(kind="float", ui_min=0.0, ui_max=200.0),
}


@primitive(meta=torus_meta)
def torus(
    *,
    major_radius: float = 1.0,
    minor_radius: float = 0.5,
    major_segments: int = 32,
    minor_segments: int = 16,
    center: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: float = 1.0,
) -> RealizedGeometry:
    """トーラスのワイヤーフレーム（子午線+緯線）を生成する。

    Parameters
    ----------
    major_radius : float, optional
        大半径。
    minor_radius : float, optional
        小半径。
    major_segments : int, optional
        major 方向の分割数。3 未満は 3 にクランプする。
    minor_segments : int, optional
        minor 方向の分割数。3 未満は 3 にクランプする。
    center : tuple[float, float, float], optional
        平行移動ベクトル (cx, cy, cz)。
    scale : float, optional
        等方スケール倍率 s。縦横比変更は effect を使用する。

    Returns
    -------
    RealizedGeometry
        子午線 `major_segments` 本と緯線 `minor_segments` 本からなる閉ポリライン列。
    """
    major_r = float(major_radius)
    minor_r = float(minor_radius)

    major_n = int(round(float(major_segments)))
    if major_n < 3:
        major_n = 3
    minor_n = int(round(float(minor_segments)))
    if minor_n < 3:
        minor_n = 3

    try:
        cx, cy, cz = center
    except Exception as exc:
        raise ValueError(
            "torus の center は長さ 3 のシーケンスである必要がある"
        ) from exc
    try:
        s_f = float(scale)
    except Exception as exc:
        raise ValueError("torus の scale は float である必要がある") from exc

    theta = np.linspace(
        0.0,
        2.0 * math.pi,
        num=major_n,
        endpoint=False,
        dtype=np.float32,
    )
    phi = np.linspace(
        0.0,
        2.0 * math.pi,
        num=minor_n,
        endpoint=False,
        dtype=np.float32,
    )

    cos_theta = np.cos(theta, dtype=np.float32)
    sin_theta = np.sin(theta, dtype=np.float32)
    cos_phi = np.cos(phi, dtype=np.float32)
    sin_phi = np.sin(phi, dtype=np.float32)

    major_r32 = np.float32(major_r)
    minor_r32 = np.float32(minor_r)

    # --- 子午線（major 角ごとに 1 本）---
    r_phi = major_r32 + minor_r32 * cos_phi  # (minor_n,)
    x_m = r_phi[None, :] * cos_theta[:, None]  # (major_n, minor_n)
    y_m = r_phi[None, :] * sin_theta[:, None]
    z_phi = minor_r32 * sin_phi
    z_m = np.broadcast_to(z_phi, x_m.shape)
    coords_m = np.stack([x_m, y_m, z_m], axis=2)
    coords_m = np.concatenate([coords_m, coords_m[:, :1, :]], axis=1)
    coords_m = coords_m.reshape(-1, 3)

    # --- 緯線（minor 角ごとに 1 本）---
    r_ring = major_r32 + minor_r32 * cos_phi  # (minor_n,)
    x_p = r_ring[:, None] * cos_theta[None, :]  # (minor_n, major_n)
    y_p = r_ring[:, None] * sin_theta[None, :]
    z_p = (minor_r32 * sin_phi)[:, None]
    z_p = np.broadcast_to(z_p, x_p.shape)
    coords_p = np.stack([x_p, y_p, z_p], axis=2)
    coords_p = np.concatenate([coords_p, coords_p[:, :1, :]], axis=1)
    coords_p = coords_p.reshape(-1, 3)

    coords = np.concatenate([coords_m, coords_p], axis=0).astype(np.float32, copy=False)

    meridian_len = minor_n + 1
    parallel_len = major_n + 1
    polyline_count = major_n + minor_n
    offsets = np.empty((polyline_count + 1,), dtype=np.int32)
    offsets[0] = 0
    offsets[1 : major_n + 1] = np.arange(1, major_n + 1, dtype=np.int32) * np.int32(
        meridian_len
    )
    base = np.int32(major_n * meridian_len)
    offsets[major_n + 1 :] = base + np.arange(
        1, minor_n + 1, dtype=np.int32
    ) * np.int32(parallel_len)

    cx_f, cy_f, cz_f = float(cx), float(cy), float(cz)
    if (cx_f, cy_f, cz_f) != (0.0, 0.0, 0.0) or s_f != 1.0:
        center_vec = np.array([cx_f, cy_f, cz_f], dtype=np.float32)
        coords = coords * np.float32(s_f) + center_vec

    return RealizedGeometry(coords=coords, offsets=offsets)
