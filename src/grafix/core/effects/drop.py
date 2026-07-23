"""ポリライン（線/面）を条件で間引き、選択されたものだけを残す effect。"""

from __future__ import annotations

import numpy as np

from grafix.core.operation_authoring import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import GeomTuple
from grafix.core.geometry_kernels.packed import empty_packed_geometry

# 高速 path の float64 中間配列・bool mask を 1 line あたり 192 bytes と
# 保守的に見積もっても、追加 peak が 8 MiB 未満に収まる上限にする。
_TWO_POINT_FAST_PATH_MIN_LINES = 64
_TWO_POINT_FAST_PATH_MAX_LINES = 32_768

drop_meta = {
    "interval": ParamMeta(
        kind="int",
        ui_min=0,
        ui_max=100,
        description="線または面をインデックス順に一定間隔で対象にする。1 以上で有効、0 で無効。",
    ),
    "index_offset": ParamMeta(
        kind="int",
        ui_min=0,
        ui_max=100,
        description="インデックスによる間引き判定の開始位置をずらす。",
    ),
    "min_length": ParamMeta(
        kind="float",
        ui_min=-1.0,
        ui_max=200.0,
        description="0 以上のとき、この長さ以下の線または面を対象にする。負値で無効。",
    ),
    "max_length": ParamMeta(
        kind="float",
        ui_min=-1.0,
        ui_max=200.0,
        description="0 以上のとき、この長さ以上の線または面を対象にする。負値で無効。",
    ),
    "probability_base": ParamMeta(
        kind="vec3",
        ui_min=0.0,
        ui_max=1.0,
        description="バウンディングボックス中心での選択確率を軸ごとに指定する。",
    ),
    "probability_slope": ParamMeta(
        kind="vec3",
        ui_min=-1.0,
        ui_max=1.0,
        description="正規化した各軸位置に対する選択確率の勾配。",
    ),
    "by": ParamMeta(
        kind="choice",
        choices=("line", "face"),
        description="選択と除去をポリライン単位または閉じた面単位で行う。",
    ),
    "keep_mode": ParamMeta(
        kind="choice",
        choices=("drop", "keep"),
        description="条件に一致した要素を除去するか、一致した要素だけ残すか選ぶ。",
    ),
    "seed": ParamMeta(
        kind="int",
        ui_min=0,
        ui_max=2**31 - 1,
        description="確率による選択結果を再現可能にする乱数シード。",
    ),
}


def _compute_polyline_lengths(
    coords: np.ndarray, offsets: np.ndarray, *, close: bool
) -> np.ndarray:
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
        L = float(seg_len.sum())
        if close and v.shape[0] >= 3:
            d = v[0] - v[-1]
            L += float(np.sqrt(np.dot(d, d)))
        lengths[i] = L
    return lengths


def _has_uniform_two_point_lines(
    coords: np.ndarray,
    offsets: np.ndarray,
    *,
    n_lines: int,
) -> bool:
    """標準 packed geometry が 2 点 line だけで構成されるかを返す。"""

    if (
        n_lines < _TWO_POINT_FAST_PATH_MIN_LINES
        or n_lines > _TWO_POINT_FAST_PATH_MAX_LINES
        or coords.shape != (2 * n_lines, 3)
    ):
        return False
    expected_offsets = np.arange(0, 2 * n_lines + 1, 2, dtype=np.int32)
    return bool(np.array_equal(offsets, expected_offsets))


def _pack_uniform_two_point_lines(
    coords: np.ndarray,
    keep_mask: np.ndarray,
) -> GeomTuple:
    """2 点 line の選択結果を入力順の exact-size 配列へ詰める。"""

    kept_count = int(np.count_nonzero(keep_mask))
    if kept_count <= 0:
        return empty_packed_geometry()

    point_mask = np.repeat(keep_mask, 2)
    out_coords = coords[point_mask]
    out_offsets = np.arange(0, 2 * kept_count + 1, 2, dtype=np.int32)
    return out_coords, out_offsets


def _probability_parameters(
    probability_base: tuple[float, float, float],
    probability_slope: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float], bool]:
    """確率 field の clamp 済み係数と有効状態を返す。"""

    def clamp_component(value: float) -> float:
        # 比較分岐により finite な範囲外値だけを clamp する。NaN は従来どおり
        # 保持し、後段の確率比較を常に False にする。
        if value < 0.0:
            return 0.0
        if value > 1.0:
            return 1.0
        return value

    base = tuple(clamp_component(value) for value in probability_base)
    slope = tuple(probability_slope)
    enabled = any(value != 0.0 for value in (*base, *slope))
    return (base[0], base[1], base[2]), (slope[0], slope[1], slope[2]), enabled


def _probability_space(coords: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """bbox 中心と正規化座標へ変換する逆半径を返す。"""

    min_v = coords.min(axis=0).astype(np.float64, copy=False)
    max_v = coords.max(axis=0).astype(np.float64, copy=False)
    center = (min_v + max_v) * 0.5
    extent = (max_v - min_v) * 0.5
    inv_extent = np.zeros((3,), dtype=np.float64)
    for axis in range(3):
        half_extent = float(extent[axis])
        inv_extent[axis] = 0.0 if half_extent < 1e-9 else 1.0 / half_extent
    return center, inv_extent


def _effective_probability_for_range(
    coords: np.ndarray,
    start: int,
    end: int,
    base: tuple[float, float, float],
    slope: tuple[float, float, float],
    center: np.ndarray,
    inv_extent: np.ndarray,
) -> float:
    """一つの packed range の centroid における合成確率を返す。"""

    base_x, base_y, base_z = base
    slope_x, slope_y, slope_z = slope
    if end <= start:
        p_x, p_y, p_z = base
    else:
        centroid = coords[start:end].mean(axis=0, dtype=np.float64)
        normalized = (centroid - center) * inv_extent
        tx = float(normalized[0])
        ty = float(normalized[1])
        tz = float(normalized[2])
        if tx < -1.0:
            tx = -1.0
        elif tx > 1.0:
            tx = 1.0
        if ty < -1.0:
            ty = -1.0
        elif ty > 1.0:
            ty = 1.0
        if tz < -1.0:
            tz = -1.0
        elif tz > 1.0:
            tz = 1.0
        p_x = base_x + slope_x * tx
        p_y = base_y + slope_y * ty
        p_z = base_z + slope_z * tz
        if p_x < 0.0:
            p_x = 0.0
        elif p_x > 1.0:
            p_x = 1.0
        if p_y < 0.0:
            p_y = 0.0
        elif p_y > 1.0:
            p_y = 1.0
        if p_z < 0.0:
            p_z = 0.0
        elif p_z > 1.0:
            p_z = 1.0
    return 1.0 - (1.0 - p_x) * (1.0 - p_y) * (1.0 - p_z)


def _select_uniform_two_point_lines(
    coords: np.ndarray,
    *,
    n_lines: int,
    interval: int | None,
    index_offset: int,
    min_length: float,
    max_length: float,
    use_min: bool,
    use_max: bool,
    base: tuple[float, float, float],
    slope: tuple[float, float, float],
    center: np.ndarray,
    inv_extent: np.ndarray,
    rng: np.random.Generator | None,
) -> np.ndarray:
    """一様な 2 点 line を vectorized path で選択する。"""

    selected = np.zeros((n_lines,), dtype=bool)
    if interval is not None and index_offset < n_lines:
        selected[index_offset::interval] = True

    points = coords.reshape(n_lines, 2, 3)
    if use_min or use_max:
        delta = np.subtract(points[:, 1, :], points[:, 0, :], dtype=np.float64)
        lengths = np.sqrt(np.sum(delta * delta, axis=1))
        if use_min:
            selected |= lengths <= min_length
        if use_max:
            selected |= lengths >= max_length

    if rng is not None:
        base_x, base_y, base_z = base
        slope_x, slope_y, slope_z = slope
        centroids = np.add(points[:, 0, :], points[:, 1, :], dtype=np.float64)
        centroids *= 0.5
        tx = (centroids[:, 0] - center[0]) * inv_extent[0]
        ty = (centroids[:, 1] - center[1]) * inv_extent[1]
        tz = (centroids[:, 2] - center[2]) * inv_extent[2]
        np.clip(tx, -1.0, 1.0, out=tx)
        np.clip(ty, -1.0, 1.0, out=ty)
        np.clip(tz, -1.0, 1.0, out=tz)

        p_x = base_x + slope_x * tx
        p_y = base_y + slope_y * ty
        p_z = base_z + slope_z * tz
        np.clip(p_x, 0.0, 1.0, out=p_x)
        np.clip(p_y, 0.0, 1.0, out=p_y)
        np.clip(p_z, 0.0, 1.0, out=p_z)
        p_eff = 1.0 - (1.0 - p_x) * (1.0 - p_y) * (1.0 - p_z)
        selected |= rng.random(n_lines) < p_eff
    return selected


def _select_lines(
    coords: np.ndarray,
    offsets: np.ndarray,
    *,
    interval: int | None,
    index_offset: int,
    min_length: float,
    max_length: float,
    use_min: bool,
    use_max: bool,
    base: tuple[float, float, float],
    slope: tuple[float, float, float],
    center: np.ndarray,
    inv_extent: np.ndarray,
    rng: np.random.Generator | None,
) -> np.ndarray:
    """generic packed line path の選択 mask を返す。"""

    n_lines = int(offsets.size) - 1
    lengths = (
        _compute_polyline_lengths(coords, offsets, close=False)
        if use_min or use_max
        else None
    )
    selected = np.zeros((n_lines,), dtype=bool)
    for line_index in range(n_lines):
        condition = interval is not None and (
            (line_index - index_offset) % interval == 0
        )
        if lengths is not None:
            length = float(lengths[line_index])
            condition = condition or (use_min and length <= min_length)
            condition = condition or (use_max and length >= max_length)
        # 他条件の有無で結果を変えないため、有効時は全 line で乱数を消費する。
        if rng is not None:
            probability = _effective_probability_for_range(
                coords,
                int(offsets[line_index]),
                int(offsets[line_index + 1]),
                base,
                slope,
                center,
                inv_extent,
            )
            probability_selected = float(rng.random()) < probability
            condition = condition or probability_selected
        selected[line_index] = condition
    return selected


def _select_faces(
    coords: np.ndarray,
    offsets: np.ndarray,
    *,
    interval: int | None,
    index_offset: int,
    min_length: float,
    max_length: float,
    use_min: bool,
    use_max: bool,
    base: tuple[float, float, float],
    slope: tuple[float, float, float],
    center: np.ndarray,
    inv_extent: np.ndarray,
    rng: np.random.Generator | None,
) -> np.ndarray | None:
    """3 点以上の face ring だけを選択する。face がなければ None を返す。"""

    n_lines = int(offsets.size) - 1
    if not any(
        int(offsets[index + 1]) - int(offsets[index]) >= 3
        for index in range(n_lines)
    ):
        return None
    lengths = (
        _compute_polyline_lengths(coords, offsets, close=True)
        if use_min or use_max
        else None
    )
    selected = np.zeros((n_lines,), dtype=bool)
    face_index = 0
    for line_index in range(n_lines):
        start = int(offsets[line_index])
        end = int(offsets[line_index + 1])
        if end - start < 3:
            continue
        condition = interval is not None and (
            (face_index - index_offset) % interval == 0
        )
        if lengths is not None:
            length = float(lengths[line_index])
            condition = condition or (use_min and length <= min_length)
            condition = condition or (use_max and length >= max_length)
        if rng is not None:
            probability = _effective_probability_for_range(
                coords,
                start,
                end,
                base,
                slope,
                center,
                inv_extent,
            )
            probability_selected = float(rng.random()) < probability
            condition = condition or probability_selected
        selected[line_index] = condition
        face_index += 1
    return selected


def _pack_lines(
    coords: np.ndarray,
    offsets: np.ndarray,
    keep_mask: np.ndarray,
) -> GeomTuple:
    """keep mask の packed ranges を入力順に詰め直す。"""

    out_coords: list[np.ndarray] = []
    out_offsets = [0]
    cursor = 0
    for line_index, keep in enumerate(keep_mask):
        if not keep:
            continue
        start = int(offsets[line_index])
        end = int(offsets[line_index + 1])
        if end <= start:
            continue
        segment = coords[start:end]
        out_coords.append(segment)
        cursor += int(segment.shape[0])
        out_offsets.append(cursor)
    if len(out_offsets) == 1:
        return empty_packed_geometry()
    return np.concatenate(out_coords, axis=0), np.asarray(out_offsets, dtype=np.int32)


@effect(meta=drop_meta)
def drop(
    g: GeomTuple,
    *,
    interval: int = 0,
    index_offset: int = 0,
    min_length: float = -1.0,
    max_length: float = -1.0,
    probability_base: tuple[float, float, float] = (0.0, 0.0, 0.0),
    probability_slope: tuple[float, float, float] = (0.0, 0.0, 0.0),
    by: str = "line",  # "line" | "face"
    seed: int = 0,
    keep_mode: str = "drop",  # "drop" | "keep"
) -> GeomTuple:
    """線や面を条件で間引く。

    Parameters
    ----------
    g : tuple[np.ndarray, np.ndarray]
        入力実体ジオメトリ（coords, offsets）。
    interval : int, default 0
        線インデックスに対する間引きステップ。1 以上で有効、0 で無効。
    index_offset : int, default 0
        interval 判定の開始オフセット。有効な interval に対して剰余へ正規化する。
    min_length : float, default -1.0
        この長さ以下の線を対象とする。0 以上で有効、0 未満で無効。
    max_length : float, default -1.0
        この長さ以上の線を対象とする。0 以上で有効、0 未満で無効。
    probability_base : tuple[float, float, float], default (0.0, 0.0, 0.0)
        ジオメトリ bbox の中心（正規化座標 t=0）における drop 確率（軸別）。
        各成分は 0.0〜1.0。有限な範囲外の値はクランプする。
    probability_slope : tuple[float, float, float], default (0.0, 0.0, 0.0)
        正規化座標 t∈[-1,+1] に対する確率勾配（軸別）。

        軸別確率を `p_axis = clamp(base_axis + slope_axis * t_axis, 0..1)` として作り、
        `p_eff = 1 - (1-p_x)(1-p_y)(1-p_z)`（OR のイメージ）で合成する。
    by : str, default "line"
        判定単位。

        "line":
            ポリラインごとに判定し、`offsets` 単位で drop/keep する。
            長さは開曲線としての線長（最後→最初は含めない）。
        "face":
            頂点数が 3 以上のポリラインを face ring とみなし、face 単位で drop/keep する。
            長さは閉曲線としての周長（最後→最初を含む）。
            頂点数が 2 以下のポリラインは常に残す（face 判定の対象外）。
    seed : int, default 0
        probability_* 使用時の乱数シード。同じ引数なら決定的に同じ線が選ばれる。
    keep_mode : str, default "drop"
        "drop": 条件に一致した線を捨てる。"keep": 条件に一致した線だけを残す。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        条件適用後の実体ジオメトリ（coords, offsets）。

    Raises
    ------
    ValueError
        `interval` または `seed` が負の場合。
    """
    if interval < 0:
        raise ValueError("drop: interval は 0 以上である必要がある")
    if seed < 0:
        raise ValueError("drop: seed は 0 以上である必要がある")

    effective_interval = interval if interval >= 1 else None
    effective_index_offset = index_offset
    if effective_interval is not None:
        effective_index_offset %= effective_interval
    use_min = min_length >= 0.0
    use_max = max_length >= 0.0
    base, slope, probability_enabled = _probability_parameters(
        probability_base,
        probability_slope,
    )

    coords, offsets = g
    n_lines = int(offsets.size) - 1
    if coords.shape[0] == 0 or n_lines <= 0:
        return coords, offsets
    if (
        effective_interval is None
        and not use_min
        and not use_max
        and not probability_enabled
    ):
        return coords, offsets

    rng = np.random.default_rng(seed) if probability_enabled else None
    if probability_enabled:
        center, inv_extent = _probability_space(coords)
    else:
        center = np.zeros((3,), dtype=np.float64)
        inv_extent = np.zeros((3,), dtype=np.float64)

    uniform_two_point_lines = by == "line" and _has_uniform_two_point_lines(
        coords,
        offsets,
        n_lines=n_lines,
    )
    if uniform_two_point_lines:
        selected = _select_uniform_two_point_lines(
            coords,
            n_lines=n_lines,
            interval=effective_interval,
            index_offset=effective_index_offset,
            min_length=min_length,
            max_length=max_length,
            use_min=use_min,
            use_max=use_max,
            base=base,
            slope=slope,
            center=center,
            inv_extent=inv_extent,
            rng=rng,
        )
        keep_mask = ~selected if keep_mode == "drop" else selected
        return _pack_uniform_two_point_lines(coords, keep_mask)

    if by == "line":
        selected = _select_lines(
            coords,
            offsets,
            interval=effective_interval,
            index_offset=effective_index_offset,
            min_length=min_length,
            max_length=max_length,
            use_min=use_min,
            use_max=use_max,
            base=base,
            slope=slope,
            center=center,
            inv_extent=inv_extent,
            rng=rng,
        )
        keep_mask = ~selected if keep_mode == "drop" else selected
    else:
        selected_faces = _select_faces(
            coords,
            offsets,
            interval=effective_interval,
            index_offset=effective_index_offset,
            min_length=min_length,
            max_length=max_length,
            use_min=use_min,
            use_max=use_max,
            base=base,
            slope=slope,
            center=center,
            inv_extent=inv_extent,
            rng=rng,
        )
        if selected_faces is None:
            return coords, offsets
        # face 対象外の 0〜2 点 line は常に残す。
        keep_mask = np.ones((n_lines,), dtype=bool)
        face_mask = np.diff(offsets) >= 3
        keep_mask[face_mask] = (
            ~selected_faces[face_mask]
            if keep_mode == "drop"
            else selected_faces[face_mask]
        )

    if not np.any(keep_mask):
        return empty_packed_geometry()
    return _pack_lines(coords, offsets, keep_mask)
