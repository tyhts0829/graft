"""3D ノイズ由来の変位を各頂点へ加え、線を有機的に揺らす effect。"""

from __future__ import annotations

from typing import Sequence

import numpy as np
from numba import njit  # type: ignore[import-untyped]

from grafix.core.effect_registry import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import RealizedGeometry

displace_meta = {
    "amplitude": ParamMeta(kind="vec3", ui_min=0.0, ui_max=50.0),
    "spatial_freq": ParamMeta(kind="vec3", ui_min=0.0, ui_max=0.1),
    "amplitude_gradient": ParamMeta(kind="vec3", ui_min=-4.0, ui_max=4.0),
    "frequency_gradient": ParamMeta(kind="vec3", ui_min=-4.0, ui_max=4.0),
    "min_gradient_factor": ParamMeta(kind="float", ui_min=0.0, ui_max=0.5),
    "max_gradient_factor": ParamMeta(kind="float", ui_min=1.0, ui_max=4.0),
    "t": ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
}

# ノイズ位相進行の係数（freq と独立）。
# 目的: noise(pos * freq + phase) の phase を time 起因で滑らかに進行させる。
PHASE_SPEED: float = 10.0
PHASE_SEED: float = 1000.0

# 勾配適用時に 0 へ落としきらず「最低でもこの係数までは残す」ための下限。
MIN_GRADIENT_FACTOR_DEFAULT: float = 0.1

# 勾配パラメータの内部クランプ（旧仕様の踏襲）。
GX = 40
FGX = 40


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


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
def fade(t):
    """Perlin ノイズ用のフェード関数。"""
    return t * t * t * (t * (t * 6 - 15) + 10)


@njit(fastmath=True, cache=True)
def lerp(a, b, t):
    """線形補間。"""
    return a + t * (b - a)


@njit(fastmath=True, cache=True)
def grad(hash_val, x, y, z, grad3_array):
    """勾配ベクトル計算。"""
    idx = int(hash_val) % 12
    g = grad3_array[idx]
    return g[0] * x + g[1] * y + g[2] * z


@njit(fastmath=True, cache=True)
def perlin_noise_3d(x, y, z, perm_table, grad3_array):
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
    frequency: tuple,
    phase: tuple,
    perm_table: np.ndarray,
    grad3_array: np.ndarray,
):
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


@njit(fastmath=True, cache=True)
def _apply_noise_to_coords(
    coords: np.ndarray,
    amplitude: tuple,
    amplitude_grad: tuple,
    frequency: tuple,
    frequency_grad: tuple,
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
        if gx > FGX:
            gx = np.float32(FGX)
        elif gx < -FGX:
            gx = np.float32(-FGX)
        if gy > FGX:
            gy = np.float32(FGX)
        elif gy < -FGX:
            gy = np.float32(-FGX)
        if gz > FGX:
            gz = np.float32(FGX)
        elif gz < -FGX:
            gz = np.float32(-FGX)

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
        one_minus_eps = np.float32(1.0) - eps
        n = coords.shape[0]
        result = np.empty_like(coords, dtype=np.float32)
        for i in range(n):
            x = coords[i, 0]
            y = coords[i, 1]
            z = coords[i, 2]

            if range_x > 1e-9:
                tx = (x - min_x) / range_x
            else:
                tx = 0.5
            if range_y > 1e-9:
                ty = (y - min_y) / range_y
            else:
                ty = 0.5
            if range_z > 1e-9:
                tz = (z - min_z) / range_z
            else:
                tz = 0.5

            fx_raw = 1.0 + gx * (tx - 0.5)
            fy_raw = 1.0 + gy * (ty - 0.5)
            fz_raw = 1.0 + gz * (tz - 0.5)

            if fx_raw < 0.0:
                fx_raw = 0.0
            if fy_raw < 0.0:
                fy_raw = 0.0
            if fz_raw < 0.0:
                fz_raw = 0.0

            fx = eps + one_minus_eps * fx_raw
            fy = eps + one_minus_eps * fy_raw
            fz = eps + one_minus_eps * fz_raw

            if fx > maxf:
                fx = maxf
            if fy > maxf:
                fy = maxf
            if fz > maxf:
                fz = maxf

            ax_i = ax * fx
            ay_i = ay * fy
            az_i = az * fz

            result[i, 0] = x + noise_offset[i, 0] * ax_i
            result[i, 1] = y + noise_offset[i, 1] * ay_i
            result[i, 2] = z + noise_offset[i, 2] * az_i

        return result

    if has_amp_grad:
        if gx > GX:
            gx = np.float32(GX)
        elif gx < -GX:
            gx = np.float32(-GX)
        if gy > GX:
            gy = np.float32(GX)
        elif gy < -GX:
            gy = np.float32(-GX)
        if gz > GX:
            gz = np.float32(GX)
        elif gz < -GX:
            gz = np.float32(-GX)

    if fgx > FGX:
        fgx = np.float32(FGX)
    elif fgx < -FGX:
        fgx = np.float32(-FGX)
    if fgy > FGX:
        fgy = np.float32(FGX)
    elif fgy < -FGX:
        fgy = np.float32(-FGX)
    if fgz > FGX:
        fgz = np.float32(FGX)
    elif fgz < -FGX:
        fgz = np.float32(-FGX)

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
    one_minus_eps = np.float32(1.0) - eps
    maxf = np.float32(max_factor)

    offset1 = np.float32(100.0)
    offset2 = np.float32(200.0)
    n = coords.shape[0]
    result = np.empty_like(coords, dtype=np.float32)
    for i in range(n):
        x = coords[i, 0]
        y = coords[i, 1]
        z = coords[i, 2]

        if range_x > 1e-9:
            tx = (x - min_x) / range_x
        else:
            tx = 0.5
        if range_y > 1e-9:
            ty = (y - min_y) / range_y
        else:
            ty = 0.5
        if range_z > 1e-9:
            tz = (z - min_z) / range_z
        else:
            tz = 0.5

        amp_fx = np.float32(1.0)
        amp_fy = np.float32(1.0)
        amp_fz = np.float32(1.0)
        if has_amp_grad:
            fx_raw = 1.0 + gx * (tx - 0.5)
            fy_raw = 1.0 + gy * (ty - 0.5)
            fz_raw = 1.0 + gz * (tz - 0.5)

            if fx_raw < 0.0:
                fx_raw = 0.0
            if fy_raw < 0.0:
                fy_raw = 0.0
            if fz_raw < 0.0:
                fz_raw = 0.0

            amp_fx = eps + one_minus_eps * fx_raw
            amp_fy = eps + one_minus_eps * fy_raw
            amp_fz = eps + one_minus_eps * fz_raw

            if amp_fx > maxf:
                amp_fx = maxf
            if amp_fy > maxf:
                amp_fy = maxf
            if amp_fz > maxf:
                amp_fz = maxf

        freq_fx_raw = 1.0 + fgx * (tx - 0.5)
        freq_fy_raw = 1.0 + fgy * (ty - 0.5)
        freq_fz_raw = 1.0 + fgz * (tz - 0.5)

        if freq_fx_raw < 0.0:
            freq_fx_raw = 0.0
        if freq_fy_raw < 0.0:
            freq_fy_raw = 0.0
        if freq_fz_raw < 0.0:
            freq_fz_raw = 0.0

        freq_fx = eps + one_minus_eps * freq_fx_raw
        freq_fy = eps + one_minus_eps * freq_fy_raw
        freq_fz = eps + one_minus_eps * freq_fz_raw

        if freq_fx > maxf:
            freq_fx = maxf
        if freq_fy > maxf:
            freq_fy = maxf
        if freq_fz > maxf:
            freq_fz = maxf

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


@effect(meta=displace_meta)
def displace(
    inputs: Sequence[RealizedGeometry],
    *,
    amplitude: tuple[float, float, float] = (8.0, 8.0, 8.0),
    spatial_freq: tuple[float, float, float] = (0.04, 0.04, 0.04),
    amplitude_gradient: tuple[float, float, float] = (0.0, 0.0, 0.0),
    frequency_gradient: tuple[float, float, float] = (0.0, 0.0, 0.0),
    min_gradient_factor: float = MIN_GRADIENT_FACTOR_DEFAULT,
    max_gradient_factor: float = 2.0,
    t: float = 0.0,
) -> RealizedGeometry:
    """3D Perlin ノイズで頂点を変位する。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        変位対象の実体ジオメトリ列。通常は 1 要素。
    amplitude : tuple[float, float, float], default (8.0, 8.0, 8.0)
        変位量 [mm]（各軸別）。
    spatial_freq : tuple[float, float, float], default (0.04, 0.04, 0.04)
        空間周波数（各軸別）。
    amplitude_gradient : tuple[float, float, float], default (0.0, 0.0, 0.0)
        振幅の軸方向グラデーション係数（各軸別）。
    frequency_gradient : tuple[float, float, float], default (0.0, 0.0, 0.0)
        周波数の軸方向グラデーション係数（各軸別）。
    min_gradient_factor : float, default 0.1
        勾配適用時の最小係数（0.0–1.0）。
    max_gradient_factor : float, default 2.0
        勾配適用時の最大係数（1.0–4.0）。
    t : float, default 0.0
        時間オフセット（位相）。値を変えるとノイズが流れる。

    Returns
    -------
    RealizedGeometry
        変位後の実体ジオメトリ。
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

    min_factor_val = float(min_gradient_factor)
    if min_factor_val < 0.0:
        min_factor_val = 0.0
    elif min_factor_val > 1.0:
        min_factor_val = 1.0

    max_factor_val = float(max_gradient_factor)
    if max_factor_val < 1.0:
        max_factor_val = 1.0
    elif max_factor_val > 4.0:
        max_factor_val = 4.0
    if max_factor_val < min_factor_val:
        max_factor_val = min_factor_val

    new_coords = _apply_noise_to_coords(
        base.coords,
        (ax, ay, az),
        (
            float(amplitude_gradient[0]),
            float(amplitude_gradient[1]),
            float(amplitude_gradient[2]),
        ),
        (
            float(spatial_freq[0]),
            float(spatial_freq[1]),
            float(spatial_freq[2]),
        ),
        (
            float(frequency_gradient[0]),
            float(frequency_gradient[1]),
            float(frequency_gradient[2]),
        ),
        float(t),
        np.float32(min_factor_val),  # type: ignore[arg-type]
        np.float32(max_factor_val),  # type: ignore[arg-type]
        NOISE_PERMUTATION_TABLE,
        NOISE_GRADIENTS_3D,
    )
    return RealizedGeometry(coords=new_coords, offsets=base.offsets)
