"""
drop エフェクト（線や面の間引き）

- 各ポリラインを単位（line/face）として判定し、条件に一致した線のみを残す/捨てる。
- 座標は変更せず、`Geometry.offsets` の構成だけを変えるシンプルな加工。

主なパラメータ:
- interval: 線インデックスを一定間隔で選択するステップ。
- min_length / max_length: 線長に基づくフィルタ。
- probability: 各線を確率的に drop する比率。
"""

from __future__ import annotations

from typing import Literal

import numpy as np

from engine.core.geometry import Geometry

from .registry import effect

PARAM_META = {
    "interval": {"type": "integer", "min": 1, "max": 100, "step": 1},
    "offset": {"type": "integer", "min": 0, "max": 100, "step": 1},
    "min_length": {"type": "number", "min": 0.0, "max": 200.0, "step": 0.1},
    "max_length": {"type": "number", "min": 0.0, "max": 200.0, "step": 0.1},
    "probability": {"type": "number", "min": 0.0, "max": 1.0, "step": 0.01},
    "by": {"choices": ["line", "face"]},
    "keep_mode": {"choices": ["keep", "drop"]},
    "seed": {"type": "integer", "min": 0, "max": 2**31 - 1, "step": 1},
}


@effect()
def drop(
    g: Geometry,
    *,
    interval: int | None = None,
    offset: int = 0,
    min_length: float | None = None,
    max_length: float | None = None,
    probability: float = 0.0,
    by: Literal["line", "face"] = "line",
    seed: int | None = None,
    keep_mode: Literal["keep", "drop"] = "drop",
) -> Geometry:
    """線や面を条件で間引く。

    Parameters
    ----------
    g : Geometry
        入力ジオメトリ。各行が 1 本のポリラインを表す（`offsets` で区切る）。
    interval : int | None, default None
        線インデックスに対する間引きステップ。1 以上で有効。
    offset : int, default 0
        interval 判定の開始オフセット。
    min_length : float | None, default None
        この長さ以下の線を対象とする。単位は座標系の長さ（mm 相当）。
    max_length : float | None, default None
        この長さ以上の線を対象とする。単位は座標系の長さ（mm 相当）。
    probability : float, default 0.0
        各線を確率的に対象とする。0.0〜1.0。0.0 は無効。
    by : {'line', 'face'}, default 'line'
        判定単位。現行実装では 'line' のみ特別扱いし、'face' も線単位で判定。
    seed : int | None, default None
        probability 使用時の乱数シード。同じ引数なら決定的に同じ線が選ばれる。
    keep_mode : {'keep', 'drop'}, default 'drop'
        'drop' の場合は条件に一致した線を捨てる。'keep' の場合は条件に一致した線だけを残す。
    """
    coords, offsets = g.as_arrays(copy=False)
    if coords.shape[0] == 0 or offsets.size <= 1:
        return Geometry(coords.copy(), offsets.copy())

    n_lines = offsets.size - 1

    eff_interval = interval if interval is not None and interval >= 1 else None
    eff_prob = probability if probability > 0.0 else 0.0
    use_min = min_length is not None
    use_max = max_length is not None

    if eff_interval is None and not use_min and not use_max and eff_prob == 0.0:
        return Geometry(coords.copy(), offsets.copy())

    # face は将来拡張用。現時点では line と同じ扱い。
    _ = by

    lengths: np.ndarray | None = None
    if use_min or use_max:
        lengths = _compute_line_lengths(coords, offsets)

    rng = None
    if eff_prob > 0.0:
        rng = np.random.default_rng(seed if seed is not None else 0)

    keep_mask = np.zeros(n_lines, dtype=bool)

    for i in range(n_lines):
        cond = False

        if eff_interval is not None:
            if eff_interval > 0:
                cond_interval = ((i - offset) % eff_interval) == 0
                cond = cond or cond_interval

        if lengths is not None:
            L = float(lengths[i])
            if use_min and L <= float(min_length):  # type: ignore[arg-type]
                cond = True
            if use_max and L >= float(max_length):  # type: ignore[arg-type]
                cond = True

        if rng is not None and eff_prob > 0.0:
            if rng.random() < eff_prob:
                cond = True

        if keep_mode == "drop":
            keep_mask[i] = not cond
        else:
            keep_mask[i] = cond

    if not np.any(keep_mask):
        # 全て drop された場合は空ジオメトリを返す。
        empty_coords = np.empty((0, 3), dtype=coords.dtype)
        empty_offsets = np.array([0], dtype=offsets.dtype)
        return Geometry(empty_coords, empty_offsets)

    # 新しい coords/offsets を構築
    out_lengths = []
    for i in range(n_lines):
        if not keep_mask[i]:
            continue
        start = int(offsets[i])
        end = int(offsets[i + 1])
        out_lengths.append(max(0, end - start))

    total_vertices = int(sum(out_lengths))
    out_coords = np.empty((total_vertices, 3), dtype=coords.dtype)
    out_offsets = np.empty(len(out_lengths) + 1, dtype=offsets.dtype)
    out_offsets[0] = 0

    ci = 0
    oi = 0
    for i in range(n_lines):
        if not keep_mask[i]:
            continue
        start = int(offsets[i])
        end = int(offsets[i + 1])
        n = max(0, end - start)
        if n == 0:
            continue
        out_coords[ci : ci + n] = coords[start:end]
        ci += n
        oi += 1
        out_offsets[oi] = ci

    if oi + 1 != out_offsets.size:
        out_offsets = out_offsets[: oi + 1]

    return Geometry(out_coords, out_offsets)


drop.__param_meta__ = PARAM_META


def _compute_line_lengths(coords: np.ndarray, offsets: np.ndarray) -> np.ndarray:
    """各ポリラインの長さを返す。"""
    n_lines = max(0, offsets.size - 1)
    lengths = np.zeros(n_lines, dtype=np.float64)
    for i in range(n_lines):
        start = int(offsets[i])
        end = int(offsets[i + 1])
        if end - start <= 1:
            lengths[i] = 0.0
            continue
        v = coords[start:end].astype(np.float64, copy=False)
        diff = v[1:] - v[:-1]
        seg_len = np.sqrt(np.sum(diff * diff, axis=1))
        lengths[i] = float(seg_len.sum())
    return lengths
