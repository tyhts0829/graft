"""
displace エフェクト（Perlin ノイズ変位）

- 3D Perlin ノイズを各頂点に加算し、表面/線を粗く揺らします。
- x/y/z の 3 成分を別位相で生成するため、等方的な乱れになります。

主なパラメータ:
- amplitude_mm: 変位量 [mm]。
- spatial_freq: 空間周波数。float なら等方、Vec3 なら各軸別周波数。
- amplitude_gradient: 振幅の軸方向グラデーション係数。
- frequency_gradient: 周波数の軸方向グラデーション係数。
- t_sec: 時間オフセット。値を変えるとアニメ的に流れるノイズへ。

実装メモ:
- Numba 最適化された `perlin_core` を使用し、Permutation/Gradient は `util.constants.NOISE_CONST` を参照。
- 入力が空または変位 0 の場合はコピーを返す純関数です。
"""

from __future__ import annotations

import numpy as np
from numba import njit  # type: ignore[attr-defined]

from common.param_utils import ensure_vec3
from common.types import Vec3
from engine.core.geometry import Geometry
from util.constants import NOISE_CONST

from .registry import effect

# ノイズ位相進行の係数（freq と独立）
# 目的: noise(pos * freq + phase) の phase を time 起因で滑らかに進行させる。
PHASE_SPEED: float = 10.0
PHASE_SEED: float = 1000.0
MIN_GRADIENT_AMPLITUDE_FACTOR: float = 0.1
GX = 40
FGX = 40


@njit(fastmath=True, cache=True)
def fade(t):
    """Perlinノイズ用のフェード関数.

    引数:
        t: 入力値。

    返り値:
        フェード処理された値。
    """
    return t * t * t * (t * (t * 6 - 15) + 10)


@njit(fastmath=True, cache=True)
def lerp(a, b, t):
    """線形補間.

    引数:
        a: 開始値。
        b: 終了値。
        t: 補間パラメータ。

    返り値:
        補間された値。
    """
    return a + t * (b - a)


@njit(fastmath=True, cache=True)
def grad(hash_val, x, y, z, grad3_array):
    """勾配ベクトル計算.

    引数:
        hash_val: ハッシュ値。
        x: X座標。
        y: Y座標。
        z: Z座標。
        grad3_array: 勾配ベクトル配列。

    返り値:
        計算された勾配値。
    """
    # 安全なインデックスアクセス
    idx = int(hash_val) % 12
    g = grad3_array[idx]
    return g[0] * x + g[1] * y + g[2] * z


@njit(fastmath=True, cache=True)
def perlin_noise_3d(x, y, z, perm_table, grad3_array):
    """3次元Perlinノイズ生成"""
    # セル空間上の位置を求める
    X = int(np.floor(x)) & 255
    Y = int(np.floor(y)) & 255
    Z = int(np.floor(z)) & 255

    # 内部点（小数部分）
    x -= np.floor(x)
    y -= np.floor(y)
    z -= np.floor(z)

    # フェード関数
    u = fade(x)
    v = fade(y)
    w = fade(z)

    # Numba最適化: 直接インデックス演算を使用（配列長は未使用）

    A = perm_table[X] + Y
    AA = perm_table[A & 511] + Z  # 511 = 2*256-1
    AB = perm_table[(A + 1) & 511] + Z
    B = perm_table[(X + 1) & 255] + Y
    BA = perm_table[B & 511] + Z
    BB = perm_table[(B + 1) & 511] + Z

    # 8つのコーナーでのグラディエントドット
    gAA = grad(perm_table[AA & 511], x, y, z, grad3_array)
    gBA = grad(perm_table[BA & 511], x - 1, y, z, grad3_array)
    gAB = grad(perm_table[AB & 511], x, y - 1, z, grad3_array)
    gBB = grad(perm_table[BB & 511], x - 1, y - 1, z, grad3_array)
    gAA1 = grad(perm_table[(AA + 1) & 511], x, y, z - 1, grad3_array)
    gBA1 = grad(perm_table[(BA + 1) & 511], x - 1, y, z - 1, grad3_array)
    gAB1 = grad(perm_table[(AB + 1) & 511], x, y - 1, z - 1, grad3_array)
    gBB1 = grad(perm_table[(BB + 1) & 511], x - 1, y - 1, z - 1, grad3_array)

    # trilinear補間
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
    """コア Perlin ノイズ計算（3次元頂点専用）

    入力空間変換は noise(pos * freq + phase)。phase は freq に非依存。
    """
    n = vertices.shape[0]

    if n == 0:
        return np.zeros((0, 3), dtype=np.float32)

    result = np.zeros((n, 3), dtype=np.float32)
    for i in range(n):
        # スケーリング後に位相を加算（freq と位相を分離）
        x = vertices[i, 0] * frequency[0] + phase[0]
        y = vertices[i, 1] * frequency[1] + phase[1]
        z = vertices[i, 2] * frequency[2] + phase[2]
        # 3成分のノイズをオフセットを変えて生成
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
    """座標配列にPerlinノイズを適用します。

    入力は noise(pos * freq + phase) によって評価され、phase は time に依存し、freq には非依存。
    これにより、spatial_freq 変更時の位相ジャンプを抑制する。amplitude/frequency の
    gradient は軸ごとの 0..1 正規化座標に対して線形係数を掛け、min/max factor で
    ソフトクランプする。
    """
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

    # 勾配が実質ゼロなら従来の一様振幅と同じ経路を通す
    if not has_amp_grad and not has_freq_grad:
        noise_offset = perlin_core(coords, frequency, phase_tuple, perm_table, grad3_array)

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

        noise_offset = perlin_core(coords, frequency, phase_tuple, perm_table, grad3_array)

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
        ny = perlin_noise_3d(px + offset1, py + offset1, pz + offset1, perm_table, grad3_array)
        nz = perlin_noise_3d(px + offset2, py + offset2, pz + offset2, perm_table, grad3_array)

        result[i, 0] = x + nx * ax * amp_fx
        result[i, 1] = y + ny * ay * amp_fy
        result[i, 2] = z + nz * az * amp_fz

    return result


# Perlinノイズ用のPermutationテーブルを作成
# 命名: 定数は UPPER_SNAKE_CASE（互換のため旧名も残す）
NOISE_PERMUTATION_TABLE = np.array(NOISE_CONST["PERM"], dtype=np.int32)
NOISE_PERMUTATION_TABLE = np.concatenate(
    [NOISE_PERMUTATION_TABLE, NOISE_PERMUTATION_TABLE]
)  # 0-255 を2回連結

# Perlinノイズで使用するグラディエントベクトル
NOISE_GRADIENTS_3D = np.array(NOISE_CONST["GRAD3"], dtype=np.float32)


@effect()
def displace(
    g: Geometry,
    *,
    amplitude_mm: float | Vec3 = (8.0, 8.0, 8.0),
    spatial_freq: float | Vec3 = (0.04, 0.04, 0.04),
    amplitude_gradient: float | Vec3 = (0.0, 0.0, 0.0),
    frequency_gradient: float | Vec3 = (0.0, 0.0, 0.0),
    min_gradient_factor: float = MIN_GRADIENT_AMPLITUDE_FACTOR,
    max_gradient_factor: float = 2.0,
    t_sec: float = 0.0,
) -> Geometry:
    """3次元頂点に Perlin ノイズ変位を追加。

    Parameters
    ----------
    g : Geometry
        入力ジオメトリ。
    amplitude_mm : float | tuple[float, float, float], default (8.0, 8.0, 8.0)
        変位量 [mm]。float なら等方、Vec3 なら各軸別。0 または (0,0,0) で no-op。
    spatial_freq : float | tuple[float, float, float], default (0.04,0.04,0.04)
        空間周波数（float は等方、Vec3 で各軸別）。
    amplitude_gradient : float | tuple[float, float, float], default (0.0, 0.0, 0.0)
        各軸方向の振幅グラデーション係数。0 で一様振幅、正負で軸方向に増減させる。
    frequency_gradient : float | tuple[float, float, float], default (0.0, 0.0, 0.0)
        各軸方向の周波数グラデーション係数。0 で一様周波数、正負で軸方向に増減させる。
    min_gradient_factor : float, default 0.1
        勾配適用時の最小係数（0.0–1.0）。振幅/周波数とも 0.0 で完全に 0 まで落ちる挙動、1.0 に近づくほど勾配の効きが弱くなる。
    max_gradient_factor : float, default 2.0
        勾配適用時の最大係数。1.0 でベース値まで、2.0 で最大 2 倍まで増幅。
    t_sec : float, default 0.0
        時間オフセット（アニメ的ノイズの位相）。
    """
    coords, offsets = g.as_arrays(copy=False)

    # パラメータ解決（新形式のみ）
    amp_val = amplitude_mm
    freq_val = spatial_freq
    grad_val = amplitude_gradient
    freq_grad_val = frequency_gradient
    min_factor_val = float(min_gradient_factor)
    max_factor_val = float(max_gradient_factor)
    ti = float(t_sec)

    # 振幅の整形（float | Vec3 → Vec3）。単一値は全成分に拡張。
    if isinstance(amp_val, (int, float)):
        ax = ay = az = float(amp_val)
    else:
        ax, ay, az = ensure_vec3(tuple(float(x) for x in amp_val))  # type: ignore[arg-type]
    amp_tuple: tuple[float, float, float] = (ax, ay, az)

    # 振幅グラデーション係数の整形（float | Vec3 → Vec3）。単一値は全成分に拡張。
    if isinstance(grad_val, (int, float)):
        gx = gy = gz = float(grad_val)
    else:
        gx, gy, gz = ensure_vec3(tuple(float(x) for x in grad_val))  # type: ignore[arg-type]
    grad_tuple: tuple[float, float, float] = (gx, gy, gz)

    # 周波数グラデーション係数の整形（float | Vec3 → Vec3）。単一値は全成分に拡張。
    if isinstance(freq_grad_val, (int, float)):
        fgx = fgy = fgz = float(freq_grad_val)
    else:
        fgx, fgy, fgz = ensure_vec3(
            tuple(float(x) for x in freq_grad_val)
        )  # type: ignore[arg-type]
    freq_grad_tuple: tuple[float, float, float] = (fgx, fgy, fgz)

    # 周波数の整形（float | Vec3 → Vec3）。単一値は全成分に拡張。
    if isinstance(freq_val, (int, float)):
        fx = fy = fz = float(freq_val)
    else:
        fx, fy, fz = ensure_vec3(tuple(float(x) for x in freq_val))  # type: ignore[arg-type]
    freq_tuple: tuple[float, float, float] = (fx, fy, fz)

    # 最小係数は 0.0–1.0 にクランプ
    if min_factor_val < 0.0:
        min_factor_val = 0.0
    elif min_factor_val > 1.0:
        min_factor_val = 1.0

    # 最大係数は 1.0–4.0 の範囲にクランプし、min より小さくならないように調整
    if max_factor_val < 1.0:
        max_factor_val = 1.0
    elif max_factor_val > 4.0:
        max_factor_val = 4.0
    if max_factor_val < min_factor_val:
        max_factor_val = min_factor_val

    new_coords = _apply_noise_to_coords(
        coords,
        amp_tuple,
        grad_tuple,
        freq_tuple,
        freq_grad_tuple,
        ti,
        np.float32(min_factor_val),  # type: ignore[arg-type]
        np.float32(max_factor_val),  # type: ignore[arg-type]
        NOISE_PERMUTATION_TABLE,
        NOISE_GRADIENTS_3D,
    )
    return Geometry(new_coords, offsets.copy())


displace.__param_meta__ = {
    "amplitude_mm": {
        "type": "number",
        "min": (0.0, 0.0, 0.0),
        "max": (50.0, 50.0, 50.0),
    },
    "amplitude_gradient": {
        "type": "number",
        "min": (-4.0, -4.0, -4.0),
        "max": (4.0, 4.0, 4.0),
        "step": (0.1, 0.1, 0.1),
    },
    "frequency_gradient": {
        "type": "number",
        "min": (-4.0, -4.0, -4.0),
        "max": (4.0, 4.0, 4.0),
        "step": (0.1, 0.1, 0.1),
    },
    "min_gradient_factor": {
        "type": "number",
        "min": 0.0,
        "max": 0.5,
        "step": 0.05,
    },
    "max_gradient_factor": {
        "type": "number",
        "min": 1.0,
        "max": 4.0,
        "step": 0.1,
    },
    "spatial_freq": {
        "type": "number",
        "min": (0.0, 0.0, 0.0),
        "max": (0.1, 0.1, 0.1),
    },
    "t_sec": {"type": "number", "min": 0.0, "max": 1.0},
}
