"""ポリライン列を dash/gap パターンで切り出し、破線に変換する effect。"""

from __future__ import annotations

from typing import Sequence

import numpy as np
from numba import njit  # type: ignore[import-untyped]

from grafix.core.effect_registry import effect
from grafix.core.realized_geometry import RealizedGeometry
from grafix.core.parameters.meta import ParamMeta

dash_meta = {
    "dash_length": ParamMeta(kind="float", ui_min=0.0, ui_max=100.0),
    "gap_length": ParamMeta(kind="float", ui_min=0.0, ui_max=100.0),
    "offset": ParamMeta(kind="float", ui_min=0.0, ui_max=100.0),
    "offset_jitter": ParamMeta(kind="float", ui_min=0.0, ui_max=100.0),
}


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _as_float_cycle(value: float | Sequence[float]) -> tuple[float, ...]:
    """float または float 列を「サイクル可能なタプル」に正規化する。"""
    # `np.ndarray` は `collections.abc.Sequence` を満たさないため個別扱いする。
    if isinstance(value, np.ndarray):
        if value.ndim == 0:
            return (float(value),)
        if value.size <= 0:
            raise ValueError("空のシーケンスは指定できません")
        return tuple(float(v) for v in value.ravel())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) <= 0:
            raise ValueError("空のシーケンスは指定できません")
        return tuple(float(v) for v in value)
    return (float(value),)


@effect(meta=dash_meta)
def dash(
    inputs: Sequence[RealizedGeometry],
    *,
    dash_length: float | Sequence[float] = 6.0,
    gap_length: float | Sequence[float] = 3.0,
    offset: float | Sequence[float] = 0.0,
    offset_jitter: float = 0.0,
) -> RealizedGeometry:
    """連続線を破線に変換する。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        入力実体ジオメトリ列。通常は 1 要素。
    dash_length : float | Sequence[float], default 6.0
        ダッシュ（描画区間）の長さ [mm]。
        シーケンス指定時は 1 本のポリライン内でダッシュごとにサイクル適用する。
    gap_length : float | Sequence[float], default 3.0
        ギャップ（非描画区間）の長さ [mm]。
        シーケンス指定時は 1 本のポリライン内でダッシュごとにサイクル適用する。
    offset : float | Sequence[float], default 0.0
        パターン位相オフセット [mm]。正の値で開始位相が前方へシフトする。
        シーケンス指定時は入力ポリラインごとにサイクル適用する。
    offset_jitter : float, default 0.0
        ポリラインごとに offset に加えるジッター量 [mm]。
        `[-offset_jitter, +offset_jitter]` の一様乱数で、0 以下は無効。

    Returns
    -------
    RealizedGeometry
        破線化済み実体ジオメトリ（各ダッシュが 1 ポリラインになる）。

    Notes
    -----
    旧仕様踏襲:
    - `dash_length + gap_length <= 0` は no-op。
    - 全長が 0 または頂点数 < 2 の線は原線を保持する。
    - offset は「位相」として扱い、開始が部分ダッシュになり得る。
    - offset_jitter は決定的な RNG（seed=0）で生成し、再現性を優先する。
    - シーケンス指定はコードからの指定を想定する（parameter_gui の編集対象にはしない）。
    """
    if not inputs:
        return _empty_geometry()

    base = inputs[0]
    coords = base.coords
    offsets = base.offsets
    if coords.shape[0] == 0:
        return base

    dash_seq = _as_float_cycle(dash_length)
    gap_seq = _as_float_cycle(gap_length)
    dash_arr = np.asarray(dash_seq, dtype=np.float64)
    gap_arr = np.asarray(gap_seq, dtype=np.float64)
    if not np.all(np.isfinite(dash_arr)) or not np.all(np.isfinite(gap_arr)):
        return base
    if np.any(dash_arr < 0.0) or np.any(gap_arr < 0.0):
        return base

    # 単一値指定は旧仕様の no-op 判定を維持する。
    if dash_arr.size == 1 and gap_arr.size == 1:
        # dash_len が 0 でも pattern が正なら「ダッシュ無し → 原線維持」に落ちる（旧仕様）。
        pattern = float(dash_arr[0] + gap_arr[0])
        if not np.isfinite(pattern) or pattern <= 0.0:
            return base

    # 線ごとの offset にランダム量を加える（決定的な RNG を使用、旧仕様）。
    offset_seq = _as_float_cycle(offset)
    jitter_scale = float(offset_jitter)
    if not np.isfinite(jitter_scale) or jitter_scale <= 0.0:
        jitter_scale = 0.0

    n_lines = int(offsets.size) - 1
    if n_lines <= 0:
        return base

    line_offset_arr = np.empty(n_lines, dtype=np.float64)
    rng = np.random.default_rng(0) if jitter_scale > 0.0 else None
    for li in range(n_lines):
        base_off = float(offset_seq[li % len(offset_seq)])
        if not np.isfinite(base_off) or base_off < 0.0:
            base_off = 0.0
        if rng is not None:
            base_off += float(rng.uniform(-jitter_scale, jitter_scale))
            if base_off < 0.0:
                base_off = 0.0
        line_offset_arr[li] = base_off

    # ---- 2 パス実装（count → fill） ---------------------------------------
    total_out_vertices = 0
    total_out_lines = 0
    for li in range(n_lines):
        v = coords[offsets[li] : offsets[li + 1]]
        tv, tl = _count_line(
            v.astype(np.float32, copy=False),
            dash_arr,
            gap_arr,
            float(line_offset_arr[li]),
        )
        total_out_vertices += int(tv)
        total_out_lines += int(tl)

    if total_out_lines == 0:
        return base

    out_coords = np.empty((total_out_vertices, 3), dtype=np.float32)
    out_offsets = np.empty((total_out_lines + 1,), dtype=np.int32)
    out_offsets[0] = 0

    vc = 0
    oc = 1
    for li in range(n_lines):
        v = coords[offsets[li] : offsets[li + 1]]
        vc, oc = _fill_line(
            v.astype(np.float32, copy=False),
            dash_arr,
            gap_arr,
            float(line_offset_arr[li]),
            out_coords,
            out_offsets,
            int(vc),
            int(oc),
        )

    if oc < out_offsets.shape[0]:
        out_offsets[oc:] = vc
    return RealizedGeometry(coords=out_coords, offsets=out_offsets)


# ── Kernels（旧実装を踏襲）──────────────────────────────────────────────
@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _build_arc_length(v: np.ndarray) -> tuple[np.ndarray, float]:
    """各頂点の弧長と全長を計算する。"""
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
    length: float,
    upper: float,
) -> tuple[bool, float, float]:
    """u 軸上のダッシュ区間を t 軸 [0, length] に射影する。"""
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
    if t_end > length:
        t_end = length
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
) -> tuple[int, int]:
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
) -> tuple[int, int]:
    n = v.shape[0]
    if n < 2:
        return n, 1

    n_dash = dash_lengths.shape[0]
    n_gap = gap_lengths.shape[0]
    if n_dash == 0 or n_gap == 0:
        return n, 1

    s, length = _build_arc_length(v)
    if length <= 0.0 or not np.isfinite(length):
        return n, 1

    total_vertices = 0
    m = 0
    u_pos = 0.0
    di = 0
    gi = 0
    upper = length + offset

    while u_pos < upper:
        dash_len = dash_lengths[di]
        gap_len = gap_lengths[gi]
        pattern = dash_len + gap_len
        if pattern <= 0.0 or not np.isfinite(pattern):
            return n, 1

        has_seg, t_start, t_end = _project_segment_to_line(
            u_pos, dash_len, offset, length, upper
        )
        if has_seg:
            s_idx = int(np.searchsorted(s, t_start))
            e_idx = int(np.searchsorted(s, t_end))
            interior = e_idx - s_idx
            if interior < 0:
                interior = 0
            total_vertices += 2 + interior
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
) -> tuple[int, int]:
    n = v.shape[0]
    vc = vc0
    oc = oc0
    if n < 2:
        return _copy_original_line(v, out_c, out_o, vc, oc)

    n_dash = dash_lengths.shape[0]
    n_gap = gap_lengths.shape[0]
    if n_dash == 0 or n_gap == 0:
        return _copy_original_line(v, out_c, out_o, vc, oc)

    s, length = _build_arc_length(v)
    if length <= 0.0 or not np.isfinite(length):
        return _copy_original_line(v, out_c, out_o, vc, oc)

    u_pos = 0.0
    di = 0
    gi = 0
    upper = length + offset
    written = 0

    while u_pos < upper:
        dash_len = dash_lengths[di]
        gap_len = gap_lengths[gi]
        pattern = dash_len + gap_len
        if pattern <= 0.0 or not np.isfinite(pattern):
            return _copy_original_line(v, out_c, out_o, vc, oc)

        has_seg, t_start, t_end = _project_segment_to_line(
            u_pos, dash_len, offset, length, upper
        )
        if has_seg:
            s_idx = int(np.searchsorted(s, t_start))
            e_idx = int(np.searchsorted(s, t_end))

            # start 補間
            s0 = s_idx - 1
            if s0 < 0:
                s0 = 0
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
                e0 = 0
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
        return _copy_original_line(v, out_c, out_o, vc, oc)

    return vc, oc


__all__ = ["dash"]
