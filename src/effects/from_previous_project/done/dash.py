"""
dash エフェクト（破線化）

- 各ポリラインを一定のダッシュ長とギャップ長で切り出し、破線の集合へ変換する。

主なパラメータ:
- dash_length: ダッシュ区間の長さ [mm]。
- gap_length: ギャップ区間の長さ [mm]。

仕様/注意:
- 端部は補間により部分ダッシュになり得る。全長が 0 または頂点数 < 2 の線は原線を保持。
- 長さ単位は座標系の実寸（mm 相当）。
- `dash_length + gap_length <= 0` または非有限値は no-op（入力コピーを返す）。

実装メモ（詳細設計）:
- 2 パス（count/fill）+ 前方確保。端点探索・補間は配列化し、ラグド結合のみ最小限の Python ループ。
- 弧長・補間は float64 で計算し、出力座標は float32 に統一。
- 端点探索は `np.searchsorted(s, ·, side='left')` を使用。0 除算は `eps=1e-12` で回避。
- 任意の Numba 加速に対応（存在時は既定で有効）。`PXD_USE_NUMBA_DASH=0` で無効化可能。
"""

from __future__ import annotations

import numpy as np
from numba import njit  # type: ignore[attr-defined]

from engine.core.geometry import Geometry

from .registry import effect

PARAM_META = {
    "dash_length": {"type": "number", "min": 0.0, "max": 100.0},
    "gap_length": {"type": "number", "min": 0.0, "max": 100.0},
    "offset": {"type": "number", "min": 0.0, "max": 100.0},
    "offset_random": {"type": "number", "min": 0.0, "max": 100.0},
}


@effect()
def dash(
    g: Geometry,
    *,
    dash_length: float | list[float] | tuple[float, ...] = 6.0,
    gap_length: float | list[float] | tuple[float, ...] = 3.0,
    offset: float | list[float] | tuple[float, ...] = 0.0,
    offset_random: float = 0.0,
) -> Geometry:
    """連続線を破線に変換。

    Parameters
    ----------
    g : Geometry
        入力ジオメトリ。各行が 1 本のポリラインを表す（`offsets` で区切る）。
    dash_length : float | list[float] | tuple[float, ...], default 6.0
        ダッシュ（描画区間）の長さ。配列指定時は `(dash[i], gap[i])` を順番に適用し、末尾まで行ったら先頭へ戻る。
    gap_length : float | list[float] | tuple[float, ...], default 3.0
        ギャップ（非描画区間）の長さ。配列指定時は `dash_length` と同様にサイクル適用する。
    offset : float | list[float] | tuple[float, ...], default 0.0
        パターンの開始位置オフセット [mm]。0.0 で常に実線から開始し、正の値でパターンを前方へシフトする。
    offset_random : float, default 0.0
        各ポリラインごとに offset に加えるランダム量の最大値 [mm]。0.0 で無効。
    """
    coords, offsets = g.as_arrays(copy=False)
    if coords.shape[0] == 0:
        return Geometry(coords.copy(), offsets.copy())

    n_lines = len(offsets) - 1

    def _as_float_seq(x: float | list[float] | tuple[float, ...], name: str) -> list[float]:
        if isinstance(x, (int, float, np.floating)):
            return [float(x)]
        if isinstance(x, (list, tuple)):
            if not x:
                raise ValueError(f"{name} に空の list/tuple は指定できません")
            return [float(v) for v in x]
        raise TypeError(f"{name} は float または list/tuple[float] を指定してください")

    dash_seq = _as_float_seq(dash_length, "dash_length")
    gap_seq = _as_float_seq(gap_length, "gap_length")
    offset_seq = _as_float_seq(offset, "offset")

    dash_arr = np.asarray(dash_seq, dtype=np.float64)
    gap_arr = np.asarray(gap_seq, dtype=np.float64)
    offset_arr = np.asarray(offset_seq, dtype=np.float64)

    n_dash = dash_arr.shape[0]
    n_gap = gap_arr.shape[0]
    n_off = offset_arr.shape[0]
    if n_dash == 0 or n_gap == 0 or n_off == 0:
        return Geometry(coords.copy(), offsets.copy())

    # 全パターンが有限かつ pattern=d+g>0 であることを事前検証（1つでも不正なら no-op）
    max_len = max(n_dash, n_gap)
    for i in range(max_len):
        d = float(dash_arr[i % n_dash])
        g = float(gap_arr[i % n_gap])
        pattern = d + g
        if not np.isfinite(pattern) or pattern <= 0.0:
            return Geometry(coords.copy(), offsets.copy())

    # offset は負値・非有限値を 0 にクランプ
    offset_arr = np.where(np.isfinite(offset_arr), offset_arr, 0.0)
    offset_arr = np.maximum(offset_arr, 0.0)

    # 線ごとの offset にランダム量を加える（決定的な RNG を使用）
    jitter_scale = float(offset_random)
    if not np.isfinite(jitter_scale) or jitter_scale <= 0.0:
        jitter_scale = 0.0

    if n_lines > 0:
        line_offset_arr = np.empty(n_lines, dtype=np.float64)
        if jitter_scale > 0.0:
            rng = np.random.default_rng(0)
            for li in range(n_lines):
                base = float(offset_arr[li % n_off])
                jitter = rng.uniform(-jitter_scale, jitter_scale)
                value = base + jitter
                if value < 0.0:
                    value = 0.0
                line_offset_arr[li] = value
        else:
            for li in range(n_lines):
                line_offset_arr[li] = float(offset_arr[li % n_off])
    else:
        line_offset_arr = np.empty(0, dtype=np.float64)

    # ---- JIT/Python 共通の 2 パス実装（count → fill） -----------------------
    # 第1パス（行数・頂点数をカウント）
    total_out_vertices = 0
    total_out_lines = 0
    for li in range(n_lines):
        v = coords[offsets[li] : offsets[li + 1]]
        off = float(line_offset_arr[li])
        tv, tl = _count_line(
            v.astype(np.float32, copy=False),
            dash_arr,
            gap_arr,
            off,
        )  # type: ignore[arg-type]
        total_out_vertices += int(tv)
        total_out_lines += int(tl)
    if total_out_lines == 0:
        return Geometry(coords.copy(), offsets.copy())
    out_coords = np.empty((total_out_vertices, 3), dtype=np.float32)
    out_offsets = np.empty((total_out_lines + 1,), dtype=np.int32)
    out_offsets[0] = 0
    vc = 0
    oc = 1
    # 第2パス（書き込み）
    for li in range(n_lines):
        v = coords[offsets[li] : offsets[li + 1]]
        off = float(line_offset_arr[li])
        vc, oc = _fill_line(  # type: ignore[arg-type]
            v.astype(np.float32, copy=False),
            dash_arr,
            gap_arr,
            off,
            out_coords,
            out_offsets,
            vc,
            oc,
        )
    if oc < out_offsets.shape[0]:
        out_offsets[oc:] = vc
    return Geometry(out_coords, out_offsets)


dash.__param_meta__ = PARAM_META


# ── Kernels（numba があれば JIT、無ければそのまま Python 実行）──────────────
@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _build_arc_length(v: np.ndarray) -> tuple[np.ndarray, float]:
    """各頂点の弧長と全長を計算。Numba 内専用ヘルパ。"""
    n = v.shape[0]
    s = np.empty(n, dtype=np.float64)
    s[0] = 0.0
    for j in range(n - 1):
        dx = v[j + 1, 0] - v[j, 0]
        dy = v[j + 1, 1] - v[j, 1]
        dz = v[j + 1, 2] - v[j, 2]
        s[j + 1] = s[j] + np.sqrt(dx * dx + dy * dy + dz * dz)
    return s, s[n - 1]


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _project_segment_to_line(
    u_start: float,
    dash_len: float,
    offset: float,
    L: float,
    upper: float,
) -> tuple[bool, float, float]:
    """u 軸上のダッシュ区間を t 軸 [0, L] に射影する。"""
    u_end = u_start + dash_len
    if u_end > upper:
        u_end = upper

    if not (u_end > offset and u_start < upper):
        return False, 0.0, 0.0

    t_start = u_start
    if t_start < offset:
        t_start = offset
    t_end = u_end
    t_start = t_start - offset
    t_end = t_end - offset
    if t_end > L:
        t_end = L
    if t_end <= t_start:
        return False, 0.0, 0.0
    return True, t_start, t_end


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _copy_original_line(
    v: np.ndarray,
    out_c: np.ndarray,
    out_o: np.ndarray,
    vc0: int,
    oc0: int,
) -> tuple:
    """元の線をそのまま出力へコピーする。"""
    n = v.shape[0]
    vc = vc0
    oc = oc0
    for j in range(n):
        out_c[vc + j, 0] = v[j, 0]
        out_c[vc + j, 1] = v[j, 1]
        out_c[vc + j, 2] = v[j, 2]
    vc += n
    out_o[oc] = vc
    oc += 1
    return vc, oc


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _count_line(
    v: np.ndarray,
    dash_lengths: np.ndarray,
    gap_lengths: np.ndarray,
    offset: float,
) -> tuple:
    n = v.shape[0]
    if n < 2:
        return n, 1

    n_dash = dash_lengths.shape[0]
    n_gap = gap_lengths.shape[0]
    if n_dash == 0 or n_gap == 0:
        return n, 1

    s, L = _build_arc_length(v)
    if L <= 0.0 or not np.isfinite(L):
        return n, 1

    total_vertices = 0
    m = 0
    u_pos = 0.0
    di = 0
    gi = 0
    upper = L + offset

    while u_pos < upper:
        dash_len = dash_lengths[di]
        gap_len = gap_lengths[gi]
        pattern = dash_len + gap_len
        if pattern <= 0.0 or not np.isfinite(pattern):
            return n, 1

        has_seg, t_start, t_end = _project_segment_to_line(u_pos, dash_len, offset, L, upper)
        if has_seg:
            s_idx = np.searchsorted(s, t_start)
            e_idx = np.searchsorted(s, t_end)
            interior = e_idx - s_idx
            if interior < 0:
                interior = 0  # type: ignore[assignment]
            total_vertices += 2 + int(interior)
            m += 1

        u_pos += pattern
        di += 1
        if di >= n_dash:
            di = 0
        gi += 1
        if gi >= n_gap:
            gi = 0

    if m == 0:
        return n, 1

    return total_vertices, m


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _fill_line(
    v: np.ndarray,
    dash_lengths: np.ndarray,
    gap_lengths: np.ndarray,
    offset: float,
    out_c: np.ndarray,
    out_o: np.ndarray,
    vc0: int,
    oc0: int,
) -> tuple:
    n = v.shape[0]
    vc = vc0
    oc = oc0
    if n < 2:
        return _copy_original_line(v, out_c, out_o, vc, oc)

    n_dash = dash_lengths.shape[0]
    n_gap = gap_lengths.shape[0]
    if n_dash == 0 or n_gap == 0:
        return _copy_original_line(v, out_c, out_o, vc, oc)

    s, L = _build_arc_length(v)
    if L <= 0.0 or not np.isfinite(L):
        return _copy_original_line(v, out_c, out_o, vc, oc)

    u_pos = 0.0
    di = 0
    gi = 0
    upper = L + offset

    written = 0

    while u_pos < upper:
        dash_len = dash_lengths[di]
        gap_len = gap_lengths[gi]
        pattern = dash_len + gap_len
        if pattern <= 0.0 or not np.isfinite(pattern):
            return _copy_original_line(v, out_c, out_o, vc, oc)

        has_seg, t_start, t_end = _project_segment_to_line(u_pos, dash_len, offset, L, upper)
        if has_seg:
            s_idx = np.searchsorted(s, t_start)
            e_idx = np.searchsorted(s, t_end)

            # start 補間
            s0 = s_idx - 1
            if s0 < 0:
                s0 = 0  # type: ignore[assignment]
            s1 = s_idx
            den = s[s1] - s[s0]
            if den == 0.0:
                ts = 0.0
            else:
                ts = (t_start - s[s0]) / den
            x0 = v[s0, 0] + (v[s1, 0] - v[s0, 0]) * ts
            y0 = v[s0, 1] + (v[s1, 1] - v[s0, 1]) * ts
            z0 = v[s0, 2] + (v[s1, 2] - v[s0, 2]) * ts

            # end 補間
            e0 = e_idx - 1
            if e0 < 0:
                e0 = 0  # type: ignore[assignment]
            e1 = e_idx
            dene = s[e1] - s[e0]
            if dene == 0.0:
                te = 0.0
            else:
                te = (t_end - s[e0]) / dene
            x1 = v[e0, 0] + (v[e1, 0] - v[e0, 0]) * te
            y1 = v[e0, 1] + (v[e1, 1] - v[e0, 1]) * te
            z1 = v[e0, 2] + (v[e1, 2] - v[e0, 2]) * te

            written += 1

            # 書き込み（開始点）
            out_c[vc, 0] = np.float32(x0)
            out_c[vc, 1] = np.float32(y0)
            out_c[vc, 2] = np.float32(z0)
            vc += 1
            # 中間頂点
            if e_idx > s_idx:
                for k in range(s_idx, e_idx):
                    out_c[vc, 0] = v[k, 0]
                    out_c[vc, 1] = v[k, 1]
                    out_c[vc, 2] = v[k, 2]
                    vc += 1
            # 終端点
            out_c[vc, 0] = np.float32(x1)
            out_c[vc, 1] = np.float32(y1)
            out_c[vc, 2] = np.float32(z1)
            vc += 1
            # 行の終端 offset
            out_o[oc] = vc
            oc += 1

        u_pos += pattern
        di += 1
        if di >= n_dash:
            di = 0
        gi += 1
        if gi >= n_gap:
            gi = 0

    if written == 0:
        # ダッシュが1本も生成されない場合は元線をそのままコピーする
        return _copy_original_line(v, out_c, out_o, vc, oc)

    return vc, oc
