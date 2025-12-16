"""
repeat エフェクト（配列複製）

- 入力のポリライン群を複製して規則的な配列を作る。
- 各複製に対して、ピボット基準のスケール、XYZ 回転、平行移動を
  「始点/終点 + カーブ（t**curve）」で決定する。
- スケール・平行移動・回転ごとに、線形補間のみ（完全非累積）かカーブ付き補間かを
  個別のフラグで制御する。

主なパラメータ:
- count: 複製回数。0 で変化なし。
- offset: 終点オフセット [mm]。始点 0 から offset までを補間。
- rotation_step: 終点角度 [deg]。始点 0 から rotation_step までを補間（Z→Y→X の順で回転）。
- scale: 終点スケール。始点 1 から scale までを補間。
- cumulative_scale / cumulative_offset / cumulative_rotate:
  - False のとき線形補間（t = i / count）。
  - True のときイージングカーブ（t' = t**curve）で「累積感」のある変化を出す。
- curve: カーブの形状を決める実数。1 で線形、1 より大きいと終盤に変化が集中。
- pivot/auto_center: 変換の中心。

注意:
- `Geometry.from_lines` で offsets を再構築するため、複数線でも安全。
- 複製数やスケール・回転を大きくすると頂点数が増え描画コストが上がる。

使用例: `(E.pipeline.repeat(count=4, offset=(30, 0, 0)).build())(g)`
"""

from __future__ import annotations


import numpy as np
from numba import njit  # type: ignore[attr-defined]

from common.types import Vec3
from engine.core.geometry import Geometry

from .registry import effect

PARAM_META = {
    "auto_center": {"type": "bool"},
    "cumulative_scale": {"type": "bool"},
    "cumulative_offset": {"type": "bool"},
    "cumulative_rotate": {"type": "bool"},
    "count": {"type": "integer", "min": 0, "max": 100, "step": 1},
    "offset": {
        "type": "vec3",
        "min": (-300.0, -300.0, -300.0),
        "max": (300.0, 300.0, 300.0),
    },
    "rotation_step": {
        "type": "vec3",
        "min": (-180.0, -180.0, -180.0),
        "max": (180.0, 180.0, 180.0),
    },
    "scale": {"type": "vec3", "min": (0.5, 0.5, 0.5), "max": (1.5, 1.5, 1.5)},
    "curve": {"type": "float", "min": 0.1, "max": 5.0, "step": 0.1},
    "pivot": {
        "type": "vec3",
        "min": (-300.0, -300.0, -300.0),
        "max": (300.0, 300.0, 300.0),
    },
}


@effect()
def repeat(
    g: Geometry,
    *,
    count: int = 3,
    cumulative_scale: bool = False,
    cumulative_offset: bool = False,
    cumulative_rotate: bool = False,
    offset: Vec3 = (0.0, 0.0, 0.0),
    rotation_step: Vec3 = (0.0, 0.0, 0.0),
    scale: Vec3 = (1.0, 1.0, 1.0),
    curve: float = 1.0,
    auto_center: bool = True,
    pivot: Vec3 = (0.0, 0.0, 0.0),
) -> Geometry:
    """入力のコピーを配列状に生成。

    Parameters
    ----------
    g : Geometry
        入力ジオメトリ。各行が 1 本のポリラインを表す（`offsets` で区切る）。
    count : int, default 3
        複製回数。0 で変化なし（no-op）。上限は `MAX_DUPLICATES`。
    cumulative_scale : bool, default False
        True のときスケール補間にカーブ（t' = t**curve）を用いる。False のとき 1→scale を線形補間する。
    cumulative_offset : bool, default False
        True のときオフセット補間にカーブ（t' = t**curve）を用いる。False のとき 0→offset を線形補間する。
    cumulative_rotate : bool, default False
        True のとき回転補間にカーブ（t' = t**curve）を用いる。False のとき 0→rotation_step を線形補間する。
    offset : tuple[float, float, float], default (0.0, 0.0, 0.0)
        終点オフセット [mm]。始点 0 から offset までを補間する。
    rotation_step : tuple[float, float, float], default (0.0, 0.0, 0.0)
        終点回転角 [deg]（X, Y, Z）。始点 0 から rotation_step までを補間する。
    scale : tuple[float, float, float], default (0.8, 0.8, 0.8)
        終点スケール倍率（X, Y, Z）。始点 1 から scale までを補間する。
    curve : float, default 1.0
        カーブの形状。1.0 で線形。1 より大きいと終盤に変化が集中し“累積感”が強くなる。
    auto_center : bool, default True
        True のとき形状の平均座標を中心に使用。False のとき `pivot` を使用。
    pivot : tuple[float, float, float], default (0.0, 0.0, 0.0)
        `auto_center=False` のときの変換中心 [mm]。
    """
    coords, offsets = g.as_arrays(copy=False)
    n_int = int(count)
    if n_int <= 0 or g.is_empty or offsets.size <= 1:
        return Geometry(coords.copy(), offsets.copy())

    # 中心座標を決定（affine の方針に合わせる）
    if auto_center:
        center_np = coords.mean(axis=0).astype(np.float32)
    else:
        center_np = np.array(pivot, dtype=np.float32)
    offset_np = np.array(offset, dtype=np.float32)
    scale_np = np.array(scale, dtype=np.float32)

    # degree で受け取り radian に変換
    rot_step_deg = np.array(rotation_step, dtype=np.float32)
    rotate_radians = np.deg2rad(rot_step_deg).astype(np.float32)

    # 生成する線のリスト（Geometry.from_lines で正しい offsets を構築）
    lines: list[np.ndarray] = []

    # 元の線を追加
    for i in range(len(offsets) - 1):
        lines.append(coords[offsets[i] : offsets[i + 1]].copy())

    # curve は 0 付近で不安定になるため、最小値をクランプ
    curve_clamped = float(max(curve, 0.1))

    base_scale = np.array([1.0, 1.0, 1.0], dtype=np.float32)

    for n in range(1, n_int + 1):
        t = n / float(n_int)

        if cumulative_scale:
            t_scale = t**curve_clamped
        else:
            t_scale = t

        if cumulative_offset:
            t_offset = t**curve_clamped
        else:
            t_offset = t

        if cumulative_rotate:
            t_rotate = t**curve_clamped
        else:
            t_rotate = t

        scale_step = base_scale + (scale_np - base_scale) * t_scale
        offset_step = offset_np * t_offset
        rotate_step = rotate_radians * t_rotate

        transformed_coords = _apply_transform_to_coords(
            coords,
            center_np,
            scale_step.astype(np.float32),
            rotate_step.astype(np.float32),
            offset_step.astype(np.float32),
        )
        for i in range(len(offsets) - 1):
            lines.append(transformed_coords[offsets[i] : offsets[i + 1]].copy())

    return Geometry.from_lines(lines)


# UI 表示のためのメタ情報（RangeHint 構築に使用）
repeat.__param_meta__ = PARAM_META


@njit(fastmath=True, cache=True)
def _apply_transform_to_coords(
    coords: np.ndarray,
    center: np.ndarray,
    scale: np.ndarray,
    rotate: np.ndarray,
    offset: np.ndarray,
) -> np.ndarray:
    """座標に変換を適用します（中心移動 -> スケール -> 回転 -> オフセット -> 中心に戻す）。"""
    # 回転行列を計算
    sx, sy, sz = np.sin(rotate)
    cx, cy, cz = np.cos(rotate)

    # Z * Y * X の結合行列
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

    # 中心を原点に移動
    centered = coords - center
    # スケール適用
    scaled = centered * scale
    # 回転適用
    rotated = scaled @ R.T
    # オフセット適用
    offset_applied = rotated + offset
    # 中心に戻す
    transformed = offset_applied + center

    return transformed
