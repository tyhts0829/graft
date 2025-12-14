"""
affine エフェクト（合成アフィン：スケール→回転→平行移動）

- ピボットを中心にスケール後、XYZ（Rz·Ry·Rx の合成）回転を一括適用し、最後にワールド座標で平行移動を加算する。

パラメータ（新API）:
- auto_center: True ならジオメトリの平均座標を中心に使用。False なら `pivot` を使用。
- pivot: `auto_center=False` のときの中心座標。
- rotation: (rx, ry, rz) [deg]。
- scale: (sx, sy, sz) 倍率。
- delta: (dx, dy, dz) [mm] 平行移動量。

注意:
- 既定の `delta=(0,0,0)` では従来どおり「スケール→回転」のみを適用。
"""

from __future__ import annotations

import numpy as np
from numba import njit  # type: ignore[attr-defined]

from common.types import Vec3
from engine.core.geometry import Geometry

from .registry import effect

PARAM_META = {
    "auto_center": {"type": "bool"},
    "pivot": {
        "type": "vec3",
        "min": (-300.0, -300.0, -300.0),
        "max": (300.0, 300.0, 300.0),
    },
    "rotation": {
        "type": "vec3",
        "min": (-180.0, -180.0, -180.0),
        "max": (180.0, 180.0, 180.0),
    },
    "scale": {"type": "vec3", "min": (0.25, 0.25, 0.25), "max": (4.0, 4.0, 4.0)},
    "delta": {
        "type": "vec3",
        "min": (0, 0, 0),
        "max": (200.0, 200.0, 200.0),
    },
}


@effect()
def affine(
    g: Geometry,
    *,
    auto_center: bool = True,
    pivot: Vec3 = (0.0, 0.0, 0.0),
    rotation: Vec3 = (0.0, 0.0, 0.0),
    scale: Vec3 = (1.0, 1.0, 1.0),
    delta: Vec3 = (0.0, 0.0, 0.0),
) -> Geometry:
    """スケール→回転→平行移動を適用（合成アフィン）。

    Parameters
    ----------
    g : Geometry
        入力ジオメトリ。各行が 1 本のポリラインを表す（`offsets` で区切る）。
    auto_center : bool, default True
        True のとき形状の平均座標を中心に使用。False のとき `pivot` を使用。
    pivot : tuple[float, float, float], default (0.0, 0.0, 0.0)
        `auto_center=False` のときの変換中心 [mm]。
    rotation : tuple[float, float, float], default (0.0, 0.0, 0.0)
        回転角 [deg]（X, Y, Z）。
    scale : tuple[float, float, float], default (0.5, 0.5, 0.5)
        スケール倍率（X, Y, Z）。
    delta : tuple[float, float, float], default (0.0, 0.0, 0.0)
        最後に適用する平行移動量 [mm]（ワールド座標）。
    """
    coords, offsets = g.as_arrays(copy=False)

    if len(coords) == 0:
        return Geometry(coords.copy(), offsets.copy())

    # 恒等変換なら早期リターン（中心の選択に依存しない）
    if (
        scale == (1, 1, 1)
        and abs(rotation[0]) < 1e-10
        and abs(rotation[1]) < 1e-10
        and abs(rotation[2]) < 1e-10
        and delta == (0, 0, 0)
    ):
        return Geometry(coords.copy(), offsets.copy())

    # 中心座標を決定
    if auto_center:
        center_np = coords.mean(axis=0).astype(np.float32)
    else:
        center_np = np.array(pivot, dtype=np.float32)
    scale_np = np.array(scale, dtype=np.float32)
    # degree で受け取り radian に変換
    rot_deg = np.array(rotation, dtype=np.float32)
    rotate_radians = np.deg2rad(rot_deg).astype(np.float32)
    translate_np = np.array(delta, dtype=np.float32)

    transformed_coords = _apply_combined_transform(
        coords, center_np, scale_np, rotate_radians, translate_np
    )
    return Geometry(transformed_coords, offsets.copy())


# UI 表示のためのメタ情報（RangeHint 構築に使用）
affine.__param_meta__ = PARAM_META


@njit(fastmath=True, cache=True)
def _apply_combined_transform(
    vertices: np.ndarray,
    center: np.ndarray,
    scale: np.ndarray,
    rotate: np.ndarray,
    translate: np.ndarray,
) -> np.ndarray:
    """頂点に組み合わせ変換を適用します。"""
    # 回転行列を一度だけ計算
    sx, sy, sz = np.sin(rotate)
    cx, cy, cz = np.cos(rotate)

    # Z * Y * X の結合行列を直接計算
    R = np.empty((3, 3), dtype=np.float32)
    R[0, 0] = cy * cz
    R[0, 1] = sx * sy * cz - cx * sz
    R[0, 2] = cx * sy * cz + sx * sz
    R[1, 0] = cy * sz
    R[1, 1] = sx * sy * sz + cx * cz
    R[1, 2] = cx * sy * sz - sx * cz
    R[2, 0] = -sy
    R[2, 1] = sx * cy
    R[2, 2] = cx * cy

    # 全頂点に変換を一度に適用
    # 手順: 中心へ移動 -> スケール -> 回転 -> 中心へ戻す -> 平行移動
    centered = vertices - center
    scaled = centered * scale
    rotated = scaled @ R.T
    transformed = rotated + center + translate

    return transformed
