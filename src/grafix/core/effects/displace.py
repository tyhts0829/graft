"""3D ノイズ由来の変位を各頂点へ加え、線を有機的に揺らす effect。"""

from __future__ import annotations

import numpy as np
from numba import njit  # type: ignore[attr-defined, import-untyped]

from grafix.core.operation_authoring import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import GeomTuple

_GRADIENT_PROFILES = ("linear", "radial")

displace_meta = {
    "amplitude": ParamMeta(
        kind="vec3",
        ui_min=0.0,
        ui_max=50.0,
        description="ノイズによる変位の最大量を軸ごとに指定する。",
    ),
    "spatial_freq": ParamMeta(
        kind="vec3",
        ui_min=0.0,
        ui_max=0.1,
        description="変位ノイズの空間周波数を軸ごとに指定する。",
    ),
    "amplitude_gradient": ParamMeta(
        kind="vec3",
        ui_min=-4.0,
        ui_max=4.0,
        description="位置に応じて変位振幅を変化させる勾配を軸ごとに指定する。",
    ),
    "frequency_gradient": ParamMeta(
        kind="vec3",
        ui_min=-4.0,
        ui_max=4.0,
        description="位置に応じて空間周波数を変化させる勾配を軸ごとに指定する。",
    ),
    "gradient_center_offset": ParamMeta(
        kind="vec3",
        ui_min=-1.0,
        ui_max=1.0,
        description="勾配の中心をバウンディングボックス正規化座標でずらす。",
    ),
    "gradient_profile": ParamMeta(
        kind="choice",
        choices=_GRADIENT_PROFILES,
        description="勾配を軸方向の線形変化と中心からの放射変化から選ぶ。",
    ),
    "gradient_radius": ParamMeta(
        kind="vec3",
        ui_min=0.05,
        ui_max=1.0,
        description="放射勾配の正規化距離を定める半径を軸ごとに指定する。",
    ),
    "min_gradient_factor": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=0.5,
        description="勾配から得る振幅と周波数の倍率を下限で制限する。",
    ),
    "max_gradient_factor": ParamMeta(
        kind="float",
        ui_min=1.0,
        ui_max=4.0,
        description="勾配から得る振幅と周波数の倍率を上限で制限する。",
    ),
    "t": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=1.0,
        description="ノイズの位相を時間方向へ進めて変位を流動させる。",
    ),
}

displace_ui_visible = {
    "gradient_radius": lambda v: v.get("gradient_profile", "linear") == "radial",
}

# ノイズ位相進行の係数（freq と独立）。
# 目的: noise(pos * freq + phase) の phase を time 起因で滑らかに進行させる。
PHASE_SPEED: float = 10.0
PHASE_SEED: float = 1000.0

# 勾配適用時に 0 へ落としきらず「最低でもこの係数までは残す」ための下限。
MIN_GRADIENT_FACTOR_DEFAULT: float = 0.1

# 勾配計算に用いる固定スケール。
GX = 40
FGX = 40


# Perlin ノイズ用定数（Ken Perlin improved noise の標準テーブル）。
_PERM_256 = [
    151,
    160,
    137,
    91,
    90,
    15,
    131,
    13,
    201,
    95,
    96,
    53,
    194,
    233,
    7,
    225,
    140,
    36,
    103,
    30,
    69,
    142,
    8,
    99,
    37,
    240,
    21,
    10,
    23,
    190,
    6,
    148,
    247,
    120,
    234,
    75,
    0,
    26,
    197,
    62,
    94,
    252,
    219,
    203,
    117,
    35,
    11,
    32,
    57,
    177,
    33,
    88,
    237,
    149,
    56,
    87,
    174,
    20,
    125,
    136,
    171,
    168,
    68,
    175,
    74,
    165,
    71,
    134,
    139,
    48,
    27,
    166,
    77,
    146,
    158,
    231,
    83,
    111,
    229,
    122,
    60,
    211,
    133,
    230,
    220,
    105,
    92,
    41,
    55,
    46,
    245,
    40,
    244,
    102,
    143,
    54,
    65,
    25,
    63,
    161,
    1,
    216,
    80,
    73,
    209,
    76,
    132,
    187,
    208,
    89,
    18,
    169,
    200,
    196,
    135,
    130,
    116,
    188,
    159,
    86,
    164,
    100,
    109,
    198,
    173,
    186,
    3,
    64,
    52,
    217,
    226,
    250,
    124,
    123,
    5,
    202,
    38,
    147,
    118,
    126,
    255,
    82,
    85,
    212,
    207,
    206,
    59,
    227,
    47,
    16,
    58,
    17,
    182,
    189,
    28,
    42,
    223,
    183,
    170,
    213,
    119,
    248,
    152,
    2,
    44,
    154,
    163,
    70,
    221,
    153,
    101,
    155,
    167,
    43,
    172,
    9,
    129,
    22,
    39,
    253,
    19,
    98,
    108,
    110,
    79,
    113,
    224,
    232,
    178,
    185,
    112,
    104,
    218,
    246,
    97,
    228,
    251,
    34,
    242,
    193,
    238,
    210,
    144,
    12,
    191,
    179,
    162,
    241,
    81,
    51,
    145,
    235,
    249,
    14,
    239,
    107,
    49,
    192,
    214,
    31,
    181,
    199,
    106,
    157,
    184,
    84,
    204,
    176,
    115,
    121,
    50,
    45,
    127,
    4,
    150,
    254,
    138,
    236,
    205,
    93,
    222,
    114,
    67,
    29,
    24,
    72,
    243,
    141,
    128,
    195,
    78,
    66,
    215,
    61,
    156,
    180,
]

_GRAD3_12 = [
    [1, 1, 0],
    [-1, 1, 0],
    [1, -1, 0],
    [-1, -1, 0],
    [1, 0, 1],
    [-1, 0, 1],
    [1, 0, -1],
    [-1, 0, -1],
    [0, 1, 1],
    [0, -1, 1],
    [0, 1, -1],
    [0, -1, -1],
]

NOISE_PERMUTATION_TABLE = np.asarray(_PERM_256, dtype=np.int32)
NOISE_PERMUTATION_TABLE = np.concatenate(
    [NOISE_PERMUTATION_TABLE, NOISE_PERMUTATION_TABLE]
)
NOISE_GRADIENTS_3D = np.asarray(_GRAD3_12, dtype=np.float32)


@njit(fastmath=True, cache=True)
def fade(t: float) -> float:
    """Perlin ノイズ用のフェード関数。"""
    return t * t * t * (t * (t * 6 - 15) + 10)


@njit(fastmath=True, cache=True)
def lerp(a: float, b: float, t: float) -> float:
    """線形補間。"""
    return a + t * (b - a)


@njit(fastmath=True, cache=True)
def grad(
    hash_val: int,
    x: float,
    y: float,
    z: float,
    grad3_array: np.ndarray,
) -> float:
    """勾配ベクトル計算。"""
    idx = int(hash_val) % 12
    g = grad3_array[idx]
    return g[0] * x + g[1] * y + g[2] * z


@njit(fastmath=True, cache=True)
def perlin_noise_3d(
    x: float,
    y: float,
    z: float,
    perm_table: np.ndarray,
    grad3_array: np.ndarray,
) -> float:
    """3 次元 Perlin ノイズ生成。"""
    X = int(np.floor(x)) & 255
    Y = int(np.floor(y)) & 255
    Z = int(np.floor(z)) & 255

    x -= np.floor(x)
    y -= np.floor(y)
    z -= np.floor(z)

    u = fade(x)
    v = fade(y)
    w = fade(z)

    A = perm_table[X] + Y
    AA = perm_table[A & 511] + Z
    AB = perm_table[(A + 1) & 511] + Z
    B = perm_table[(X + 1) & 255] + Y
    BA = perm_table[B & 511] + Z
    BB = perm_table[(B + 1) & 511] + Z

    gAA = grad(perm_table[AA & 511], x, y, z, grad3_array)
    gBA = grad(perm_table[BA & 511], x - 1, y, z, grad3_array)
    gAB = grad(perm_table[AB & 511], x, y - 1, z, grad3_array)
    gBB = grad(perm_table[BB & 511], x - 1, y - 1, z, grad3_array)
    gAA1 = grad(perm_table[(AA + 1) & 511], x, y, z - 1, grad3_array)
    gBA1 = grad(perm_table[(BA + 1) & 511], x - 1, y, z - 1, grad3_array)
    gAB1 = grad(perm_table[(AB + 1) & 511], x, y - 1, z - 1, grad3_array)
    gBB1 = grad(perm_table[(BB + 1) & 511], x - 1, y - 1, z - 1, grad3_array)

    return lerp(
        lerp(lerp(gAA, gBA, u), lerp(gAB, gBB, u), v),
        lerp(lerp(gAA1, gBA1, u), lerp(gAB1, gBB1, u), v),
        w,
    )


@njit(fastmath=True, cache=True)
def perlin_core(
    vertices: np.ndarray,
    frequency: tuple[float, float, float],
    phase: tuple[np.float32, np.float32, np.float32],
    perm_table: np.ndarray,
    grad3_array: np.ndarray,
) -> np.ndarray:
    """コア Perlin ノイズ計算（3 次元頂点専用）。

    入力空間変換は noise(pos * freq + phase)。phase は freq に非依存。
    """
    n = vertices.shape[0]
    if n == 0:
        return np.zeros((0, 3), dtype=np.float32)

    result = np.zeros((n, 3), dtype=np.float32)
    for i in range(n):
        x = vertices[i, 0] * frequency[0] + phase[0]
        y = vertices[i, 1] * frequency[1] + phase[1]
        z = vertices[i, 2] * frequency[2] + phase[2]

        nx = perlin_noise_3d(x, y, z, perm_table, grad3_array)
        ny = perlin_noise_3d(x + 100.0, y + 100.0, z + 100.0, perm_table, grad3_array)
        nz = perlin_noise_3d(x + 200.0, y + 200.0, z + 200.0, perm_table, grad3_array)
        result[i, 0] = np.float32(nx)
        result[i, 1] = np.float32(ny)
        result[i, 2] = np.float32(nz)

    return result


@njit(fastmath=True, inline="always")
def _clamp_gradient_coefficient(value: np.float32, limit: float) -> np.float32:
    """gradient coefficient を symmetric limit へ clamp する。"""

    if value > limit:
        return np.float32(limit)
    if value < -limit:
        return np.float32(-limit)
    return value


@njit(fastmath=True, inline="always")
def _normalized_bbox_position(
    value: np.float32,
    minimum: np.float32,
    span: np.float32,
) -> float:
    """bbox 軸上の位置を 0..1 へ正規化する。退化軸は中心を返す。"""

    if span > 1e-9:
        return float((value - minimum) / span)
    return 0.5


@njit(fastmath=True, inline="always")
def _gradient_factors(
    tx: float,
    ty: float,
    tz: float,
    gx: np.float32,
    gy: np.float32,
    gz: np.float32,
    cx: np.float32,
    cy: np.float32,
    cz: np.float32,
    profile_mode: int,
    inv_rx: np.float32,
    inv_ry: np.float32,
    inv_rz: np.float32,
    minimum: np.float32,
    maximum: np.float32,
) -> tuple[float, float, float]:
    """linear/radial profile を評価し、gradient factor 範囲へ clamp する。"""

    if profile_mode == 0:
        raw_x = 1.0 + gx * (tx - cx)
        raw_y = 1.0 + gy * (ty - cy)
        raw_z = 1.0 + gz * (tz - cz)
    else:
        dx = (tx - cx) * inv_rx
        dy = (ty - cy) * inv_ry
        dz = (tz - cz) * inv_rz
        distance = np.sqrt(dx * dx + dy * dy + dz * dz)
        raw_x = 1.0 - gx * distance
        raw_y = 1.0 - gy * distance
        raw_z = 1.0 - gz * distance

    if raw_x < 0.0:
        raw_x = 0.0  # type: ignore[assignment]
    if raw_y < 0.0:
        raw_y = 0.0  # type: ignore[assignment]
    if raw_z < 0.0:
        raw_z = 0.0  # type: ignore[assignment]

    one_minus_minimum = np.float32(1.0) - minimum
    factor_x = minimum + one_minus_minimum * raw_x
    factor_y = minimum + one_minus_minimum * raw_y
    factor_z = minimum + one_minus_minimum * raw_z
    if factor_x > maximum:
        factor_x = maximum
    if factor_y > maximum:
        factor_y = maximum
    if factor_z > maximum:
        factor_z = maximum
    return float(factor_x), float(factor_y), float(factor_z)


@njit(fastmath=True, cache=True)
def _apply_noise_to_coords(
    coords: np.ndarray,
    amplitude: tuple[float, float, float],
    amplitude_grad: tuple[float, float, float],
    frequency: tuple[float, float, float],
    frequency_grad: tuple[float, float, float],
    gradient_center_offset: tuple[float, float, float],
    gradient_profile_mode: int,
    gradient_radius: tuple[float, float, float],
    time: float,
    min_factor: float,
    max_factor: float,
    perm_table: np.ndarray,
    grad3_array: np.ndarray,
) -> np.ndarray:
    """座標配列に Perlin ノイズを適用する。"""
    if coords.size == 0:
        return coords.copy()

    ax = np.float32(amplitude[0])
    ay = np.float32(amplitude[1])
    az = np.float32(amplitude[2])

    if ax == 0.0 and ay == 0.0 and az == 0.0:
        return coords.copy()

    gx = np.float32(amplitude_grad[0])
    gy = np.float32(amplitude_grad[1])
    gz = np.float32(amplitude_grad[2])

    fgx = np.float32(frequency_grad[0])
    fgy = np.float32(frequency_grad[1])
    fgz = np.float32(frequency_grad[2])

    has_amp_grad = not (abs(gx) < 1e-6 and abs(gy) < 1e-6 and abs(gz) < 1e-6)
    has_freq_grad = not (abs(fgx) < 1e-6 and abs(fgy) < 1e-6 and abs(fgz) < 1e-6)

    half = np.float32(0.5)
    cx = half + np.float32(gradient_center_offset[0])
    cy = half + np.float32(gradient_center_offset[1])
    cz = half + np.float32(gradient_center_offset[2])

    if gradient_profile_mode == 1:
        inv_rx = np.float32(1.0) / np.float32(gradient_radius[0])
        inv_ry = np.float32(1.0) / np.float32(gradient_radius[1])
        inv_rz = np.float32(1.0) / np.float32(gradient_radius[2])
    else:
        inv_rx = np.float32(0.0)
        inv_ry = np.float32(0.0)
        inv_rz = np.float32(0.0)

    fx_base = np.float32(frequency[0])
    fy_base = np.float32(frequency[1])
    fz_base = np.float32(frequency[2])

    phase0 = np.float32(time * PHASE_SPEED + PHASE_SEED)
    phase_tuple = (phase0, phase0, phase0)

    if not has_amp_grad and not has_freq_grad:
        noise_offset = perlin_core(
            coords, frequency, phase_tuple, perm_table, grad3_array
        )

        n = coords.shape[0]
        result = np.empty_like(coords, dtype=np.float32)
        for i in range(n):
            result[i, 0] = coords[i, 0] + noise_offset[i, 0] * ax
            result[i, 1] = coords[i, 1] + noise_offset[i, 1] * ay
            result[i, 2] = coords[i, 2] + noise_offset[i, 2] * az

        return result

    if not has_freq_grad:
        gx = _clamp_gradient_coefficient(gx, FGX)
        gy = _clamp_gradient_coefficient(gy, FGX)
        gz = _clamp_gradient_coefficient(gz, FGX)

        noise_offset = perlin_core(
            coords, frequency, phase_tuple, perm_table, grad3_array
        )

        min_x = np.float32(np.min(coords[:, 0]))
        max_x = np.float32(np.max(coords[:, 0]))
        min_y = np.float32(np.min(coords[:, 1]))
        max_y = np.float32(np.max(coords[:, 1]))
        min_z = np.float32(np.min(coords[:, 2]))
        max_z = np.float32(np.max(coords[:, 2]))

        range_x = max_x - min_x
        range_y = max_y - min_y
        range_z = max_z - min_z

        maxf = np.float32(max_factor)
        eps = np.float32(min_factor)
        n = coords.shape[0]
        result = np.empty_like(coords, dtype=np.float32)
        for i in range(n):
            x = coords[i, 0]
            y = coords[i, 1]
            z = coords[i, 2]
            tx = _normalized_bbox_position(x, min_x, range_x)
            ty = _normalized_bbox_position(y, min_y, range_y)
            tz = _normalized_bbox_position(z, min_z, range_z)
            fx, fy, fz = _gradient_factors(
                tx,
                ty,
                tz,
                gx,
                gy,
                gz,
                cx,
                cy,
                cz,
                gradient_profile_mode,
                inv_rx,
                inv_ry,
                inv_rz,
                eps,
                maxf,
            )

            ax_i = ax * fx
            ay_i = ay * fy
            az_i = az * fz

            result[i, 0] = x + noise_offset[i, 0] * ax_i
            result[i, 1] = y + noise_offset[i, 1] * ay_i
            result[i, 2] = z + noise_offset[i, 2] * az_i

        return result

    if has_amp_grad:
        gx = _clamp_gradient_coefficient(gx, GX)
        gy = _clamp_gradient_coefficient(gy, GX)
        gz = _clamp_gradient_coefficient(gz, GX)

    fgx = _clamp_gradient_coefficient(fgx, FGX)
    fgy = _clamp_gradient_coefficient(fgy, FGX)
    fgz = _clamp_gradient_coefficient(fgz, FGX)

    min_x = np.float32(np.min(coords[:, 0]))
    max_x = np.float32(np.max(coords[:, 0]))
    min_y = np.float32(np.min(coords[:, 1]))
    max_y = np.float32(np.max(coords[:, 1]))
    min_z = np.float32(np.min(coords[:, 2]))
    max_z = np.float32(np.max(coords[:, 2]))

    range_x = max_x - min_x
    range_y = max_y - min_y
    range_z = max_z - min_z

    eps = np.float32(min_factor)
    maxf = np.float32(max_factor)

    offset1 = np.float32(100.0)
    offset2 = np.float32(200.0)
    n = coords.shape[0]
    result = np.empty_like(coords, dtype=np.float32)
    for i in range(n):
        x = coords[i, 0]
        y = coords[i, 1]
        z = coords[i, 2]

        tx = _normalized_bbox_position(x, min_x, range_x)
        ty = _normalized_bbox_position(y, min_y, range_y)
        tz = _normalized_bbox_position(z, min_z, range_z)

        amp_fx = np.float32(1.0)
        amp_fy = np.float32(1.0)
        amp_fz = np.float32(1.0)
        if has_amp_grad:
            amp_fx, amp_fy, amp_fz = _gradient_factors(  # type: ignore[assignment]
                tx,
                ty,
                tz,
                gx,
                gy,
                gz,
                cx,
                cy,
                cz,
                gradient_profile_mode,
                inv_rx,
                inv_ry,
                inv_rz,
                eps,
                maxf,
            )

        freq_fx, freq_fy, freq_fz = _gradient_factors(
            tx,
            ty,
            tz,
            fgx,
            fgy,
            fgz,
            cx,
            cy,
            cz,
            gradient_profile_mode,
            inv_rx,
            inv_ry,
            inv_rz,
            eps,
            maxf,
        )

        px = x * (fx_base * freq_fx) + phase0
        py = y * (fy_base * freq_fy) + phase0
        pz = z * (fz_base * freq_fz) + phase0

        nx = perlin_noise_3d(px, py, pz, perm_table, grad3_array)
        ny = perlin_noise_3d(
            px + offset1, py + offset1, pz + offset1, perm_table, grad3_array
        )
        nz = perlin_noise_3d(
            px + offset2, py + offset2, pz + offset2, perm_table, grad3_array
        )

        result[i, 0] = x + nx * ax * amp_fx
        result[i, 1] = y + ny * ay * amp_fy
        result[i, 2] = z + nz * az * amp_fz

    return result


@effect(meta=displace_meta, ui_visible=displace_ui_visible)
def displace(
    g: GeomTuple,
    *,
    amplitude: tuple[float, float, float] = (8.0, 8.0, 8.0),
    spatial_freq: tuple[float, float, float] = (0.04, 0.04, 0.04),
    amplitude_gradient: tuple[float, float, float] = (0.0, 0.0, 0.0),
    frequency_gradient: tuple[float, float, float] = (0.0, 0.0, 0.0),
    gradient_center_offset: tuple[float, float, float] = (0.0, 0.0, 0.0),
    gradient_profile: str = "linear",  # "linear" | "radial"
    gradient_radius: tuple[float, float, float] = (0.5, 0.5, 0.5),
    min_gradient_factor: float = MIN_GRADIENT_FACTOR_DEFAULT,
    max_gradient_factor: float = 2.0,
    t: float = 0.0,
) -> GeomTuple:
    """3D Perlin ノイズで頂点を変位する。

    Parameters
    ----------
    g : tuple[np.ndarray, np.ndarray]
        変位対象の実体ジオメトリ（coords, offsets）。
    amplitude : tuple[float, float, float], default (8.0, 8.0, 8.0)
        変位量 [mm]（各軸別）。
    spatial_freq : tuple[float, float, float], default (0.04, 0.04, 0.04)
        空間周波数（各軸別）。
    amplitude_gradient : tuple[float, float, float], default (0.0, 0.0, 0.0)
        振幅の軸方向グラデーション係数（各軸別）。
    frequency_gradient : tuple[float, float, float], default (0.0, 0.0, 0.0)
        周波数の軸方向グラデーション係数（各軸別）。
    gradient_center_offset : tuple[float, float, float], default (0.0, 0.0, 0.0)
        勾配計算の中心オフセット（bbox 正規化座標、各軸別）。
    gradient_profile : str, default "linear"
        勾配の形状。

        - `"linear"`: `raw = 1 + g * (t - c)`（一次、片側が強くなる）
        - `"radial"`: `raw = 1 - g * d`（中心からの距離で変化、等値線が円/楕円）
    gradient_radius : tuple[float, float, float], default (0.5, 0.5, 0.5)
        `"radial"` の距離 `d` を作るための正の半径（bbox 正規化座標、各軸別）。

        例: `gradient_radius=(0.5, 0.5, 0.5)` なら bbox 中心から各辺までが概ね半径 1 になる。
    min_gradient_factor : float, default 0.1
        勾配適用時の最小係数（0.0–1.0）。
    max_gradient_factor : float, default 2.0
        勾配適用時の最大係数（1.0–4.0）。
    t : float, default 0.0
        時間オフセット（位相）。値を変えるとノイズが流れる。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        変位後の実体ジオメトリ（coords, offsets）。

    Raises
    ------
    ValueError
        `gradient_profile="radial"` で `gradient_radius` の成分が 0 以下の場合。
    """
    if gradient_profile == "radial" and any(
        component <= 0.0 for component in gradient_radius
    ):
        raise ValueError(
            "displace の radial gradient_radius は全成分が 0 より大きい必要がある"
        )
    if not 0.0 <= min_gradient_factor <= 1.0:
        raise ValueError(
            "displace: min_gradient_factor は 0.0 以上 1.0 以下である必要がある"
        )
    if not 1.0 <= max_gradient_factor <= 4.0:
        raise ValueError(
            "displace: max_gradient_factor は 1.0 以上 4.0 以下である必要がある"
        )
    if max_gradient_factor < min_gradient_factor:
        raise ValueError(
            "displace: max_gradient_factor は min_gradient_factor 以上である必要がある"
        )

    coords, offsets = g
    if coords.shape[0] == 0:
        return coords, offsets

    ax, ay, az = amplitude
    if ax == 0.0 and ay == 0.0 and az == 0.0:
        return coords, offsets

    profile_mode = 1 if gradient_profile == "radial" else 0

    new_coords = _apply_noise_to_coords(
        coords,
        (ax, ay, az),
        amplitude_gradient,
        spatial_freq,
        frequency_gradient,
        gradient_center_offset,
        profile_mode,
        gradient_radius,
        t,
        np.float32(min_gradient_factor),  # type: ignore[arg-type]
        np.float32(max_gradient_factor),  # type: ignore[arg-type]
        NOISE_PERMUTATION_TABLE,
        NOISE_GRADIENTS_3D,
    )
    return new_coords, offsets
