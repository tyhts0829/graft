"""ポリライン（線/面）を条件で間引き、選択されたものだけを残す effect。"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from grafix.core.effect_registry import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import RealizedGeometry

drop_meta = {
    "interval": ParamMeta(kind="int", ui_min=0, ui_max=100),
    "offset": ParamMeta(kind="int", ui_min=0, ui_max=100),
    "min_length": ParamMeta(kind="float", ui_min=-1.0, ui_max=200.0),
    "max_length": ParamMeta(kind="float", ui_min=-1.0, ui_max=200.0),
    "probability": ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
    "by": ParamMeta(kind="choice", choices=("line", "face")),
    "keep_mode": ParamMeta(kind="choice", choices=("drop", "keep")),
    "seed": ParamMeta(kind="int", ui_min=0, ui_max=2**31 - 1),
}


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _compute_line_lengths(coords: np.ndarray, offsets: np.ndarray) -> np.ndarray:
    """各ポリラインの長さを返す。"""
    n_lines = max(0, int(offsets.size) - 1)
    lengths = np.zeros((n_lines,), dtype=np.float64)
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


@effect(meta=drop_meta)
def drop(
    inputs: Sequence[RealizedGeometry],
    *,
    interval: int = 0,
    offset: int = 0,
    min_length: float = -1.0,
    max_length: float = -1.0,
    probability: float = 0.0,
    by: str = "line",  # "line" | "face"
    seed: int = 0,
    keep_mode: str = "drop",  # "drop" | "keep"
) -> RealizedGeometry:
    """線や面を条件で間引く。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        入力実体ジオメトリ列。通常は 1 要素。
    interval : int, default 0
        線インデックスに対する間引きステップ。1 以上で有効、0 で無効。
    offset : int, default 0
        interval 判定の開始オフセット。
    min_length : float, default -1.0
        この長さ以下の線を対象とする。0 以上で有効、-1 で無効。
    max_length : float, default -1.0
        この長さ以上の線を対象とする。0 以上で有効、-1 で無効。
    probability : float, default 0.0
        各線を確率的に対象とする比率。0.0〜1.0。0.0 は無効。
    by : str, default "line"
        判定単位。現行実装では "line" と "face" は同じ扱い（線単位）。
    seed : int, default 0
        probability 使用時の乱数シード。同じ引数なら決定的に同じ線が選ばれる。
    keep_mode : str, default "drop"
        "drop": 条件に一致した線を捨てる。"keep": 条件に一致した線だけを残す。

    Returns
    -------
    RealizedGeometry
        条件適用後の実体ジオメトリ。
    """
    if not inputs:
        return _empty_geometry()

    base = inputs[0]
    coords = base.coords
    offsets = base.offsets
    if coords.shape[0] == 0:
        return base

    n_lines = int(offsets.size) - 1
    if n_lines <= 0:
        return base

    interval_i = int(interval)
    eff_interval = interval_i if interval_i >= 1 else None
    offset_i = int(offset)

    min_length_f = float(min_length)
    max_length_f = float(max_length)
    use_min = min_length_f >= 0.0
    use_max = max_length_f >= 0.0

    eff_prob = float(probability)
    if eff_prob <= 0.0:
        eff_prob = 0.0

    if eff_interval is None and not use_min and not use_max and eff_prob == 0.0:
        return base

    # face は将来拡張用。現時点では line と同じ扱い。
    _ = by

    lengths: np.ndarray | None = None
    if use_min or use_max:
        lengths = _compute_line_lengths(coords, offsets)

    rng = None
    if eff_prob > 0.0:
        rng = np.random.default_rng(int(seed))

    keep_mask = np.zeros((n_lines,), dtype=bool)
    for i in range(n_lines):
        cond = False

        if eff_interval is not None:
            cond = cond or (((i - offset_i) % eff_interval) == 0)

        if lengths is not None:
            L = float(lengths[i])
            if use_min and L <= min_length_f:
                cond = True
            if use_max and L >= max_length_f:
                cond = True

        # 旧仕様に合わせて、乱数は全行で消費する（他条件の有無で結果が変わらないようにする）。
        if rng is not None:
            if float(rng.random()) < eff_prob:
                cond = True

        if keep_mode == "drop":
            keep_mask[i] = not cond
        else:
            keep_mask[i] = cond

    if not np.any(keep_mask):
        return _empty_geometry()

    out_coords_list: list[np.ndarray] = []
    out_offsets_list: list[int] = [0]
    cursor = 0

    for i in range(n_lines):
        if not keep_mask[i]:
            continue
        start = int(offsets[i])
        end = int(offsets[i + 1])
        if end <= start:
            continue
        seg = coords[start:end]
        out_coords_list.append(seg)
        cursor += int(seg.shape[0])
        out_offsets_list.append(cursor)

    if len(out_offsets_list) == 1:
        return _empty_geometry()

    out_coords = np.concatenate(out_coords_list, axis=0)
    out_offsets = np.asarray(out_offsets_list, dtype=np.int32)
    return RealizedGeometry(coords=out_coords, offsets=out_offsets)
