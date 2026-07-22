"""座標にスケールを適用する effect。"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from grafix.core.operation_authoring import effect
from grafix.core.operation_schema import UiVisiblePred
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import GeomTuple

_CLOSED_ATOL = 1e-6
# 1 本では slice loop が速く、8 本では bulk 経路が明確に速くなる実測結果に基づく。
_BULK_LINE_THRESHOLD = 8
# 閉判定・中心計算用の一時配列をこの line 数以下に抑える。
_BULK_LINE_CHUNK = 8192
# 可変長 line の gather 用一時配列をこの頂点数以下に抑える。
_BULK_VERTEX_CHUNK = 8192
# 大きな (N, 3) buffer は軸ごとに処理すると broadcast より cache 効率がよい。
_AXIS_WISE_VERTEX_THRESHOLD = 512
_SPLIT_UFUNC_SAFE_ABS_MAX = 1.0e100
_SPLIT_UFUNC_SAFE_ABS_MIN = 1.0e-100

scale_meta = {
    "mode": ParamMeta(
        kind="choice",
        choices=("all", "by_line", "by_face"),
        description="入力全体、開いた線ごと、または閉じた面ごとのどの単位で拡縮するか選ぶ。",
    ),
    "auto_center": ParamMeta(
        kind="bool",
        description="入力全体を拡縮するときに頂点の平均座標を中心として使用する。",
    ),
    "pivot": ParamMeta(
        kind="vec3",
        ui_min=-100.0,
        ui_max=100.0,
        description="入力全体を拡縮するとき、自動中心が無効な場合に使用する中心点。",
    ),
    "scale": ParamMeta(
        kind="vec3",
        ui_min=0.0,
        ui_max=10.0,
        description="選択した中心を基準に適用する各軸の倍率。",
    ),
}

def _mode_is(name: str) -> UiVisiblePred:
    def _pred(v: Mapping[str, object]) -> bool:
        return v.get("mode", "all") == name

    return _pred


scale_ui_visible = {
    "auto_center": _mode_is("all"),
    "pivot": lambda v: v.get("mode", "all") == "all"
    and v.get("auto_center", True) is False,
}


def _is_closed_polyline(vertices: np.ndarray) -> bool:
    if vertices.shape[0] < 2:
        return False
    return bool(np.allclose(vertices[0], vertices[-1], rtol=0.0, atol=_CLOSED_ATOL))


def _scale_polylines_loop(
    coords64: np.ndarray,
    offsets: np.ndarray,
    *,
    mode: str,
    factors: np.ndarray,
) -> None:
    """少数 line を slice 単位で処理する。"""
    for i in range(int(offsets.size) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        if e <= s:
            continue

        vertices = coords64[s:e]
        is_closed = _is_closed_polyline(vertices)

        if mode == "by_line":
            if is_closed:
                continue
            center = vertices.mean(axis=0)
        else:
            if not is_closed:
                continue
            center = vertices[:-1].mean(axis=0)

        coords64[s:e] = (vertices - center) * factors + center


def _can_scale_polylines_in_bulk(
    *,
    line_count: int,
    factors: np.ndarray,
) -> bool:
    """bulk 経路が有利かつ分割 ufunc に安全な値域かを返す。"""
    return bool(
        line_count >= _BULK_LINE_THRESHOLD
        and _split_ufunc_values_are_safe(factors)
    )


def _split_ufunc_values_are_safe(values: np.ndarray) -> bool:
    """分割 ufunc でも warning/callback 回数が変わらない通常範囲かを返す。"""

    values_abs = np.abs(values)
    return bool(
        np.isfinite(values).all()
        and np.all(
            (values_abs == 0.0)
            | (
                (values_abs >= _SPLIT_UFUNC_SAFE_ABS_MIN)
                & (values_abs <= _SPLIT_UFUNC_SAFE_ABS_MAX)
            )
        )
    )


def _scale_vertex_block(
    vertices: np.ndarray,
    *,
    mode: str,
    factors: np.ndarray,
) -> None:
    """同じ頂点数の line 群を演算順を変えずに in-place 変換する。"""
    center_source = vertices if mode == "by_line" else vertices[:, :-1]
    centers = center_source.mean(axis=1)
    vertices -= centers[:, None, :]
    vertices *= factors
    vertices += centers[:, None, :]


def _scale_polylines_bulk(
    coords64: np.ndarray,
    offsets: np.ndarray,
    *,
    mode: str,
    factors: np.ndarray,
) -> None:
    """多数の canonical line の閉判定・中心計算・変換をまとめて行う。"""
    line_count = int(offsets.size) - 1
    for first_line in range(0, line_count, _BULK_LINE_CHUNK):
        last_line = min(first_line + _BULK_LINE_CHUNK, line_count)
        _scale_polyline_chunk(
            coords64,
            offsets,
            first_line=first_line,
            last_line=last_line,
            mode=mode,
            factors=factors,
        )


def _scale_polyline_chunk(
    coords64: np.ndarray,
    offsets: np.ndarray,
    *,
    first_line: int,
    last_line: int,
    mode: str,
    factors: np.ndarray,
) -> None:
    """line 範囲を bounded memory で bulk 変換する。"""

    starts = offsets[first_line:last_line]
    ends = offsets[first_line + 1 : last_line + 1]
    lengths = ends - starts

    is_closed = np.zeros(lengths.shape, dtype=np.bool_)
    closable_lines = np.flatnonzero(lengths >= 2)
    if closable_lines.size:
        first_vertices = coords64[starts[closable_lines]]
        last_vertices = coords64[ends[closable_lines] - 1]
        is_closed[closable_lines] = np.all(
            np.isclose(
                first_vertices,
                last_vertices,
                rtol=0.0,
                atol=_CLOSED_ATOL,
                equal_nan=False,
            ),
            axis=1,
        )

    nonempty = lengths > 0
    target_mask = nonempty & (~is_closed if mode == "by_line" else is_closed)
    target_lines = np.flatnonzero(target_mask)
    if target_lines.size == 0:
        return

    # 同じ長さの全 line が対象なら、packed buffer をそのまま 3-D view にする。
    # gather が不要なため、代表的な many-short-lines case の一時メモリも増えない。
    if target_lines.size == lengths.size and np.all(lengths == lengths[0]):
        vertex_start = int(starts[0])
        vertex_end = int(ends[-1])
        vertices = coords64[vertex_start:vertex_end].reshape(
            int(lengths.size),
            int(lengths[0]),
            3,
        )
        _scale_vertex_block(vertices, mode=mode, factors=factors)
        return

    target_lengths = lengths[target_lines]
    for length_raw in np.unique(target_lengths):
        length = int(length_raw)
        same_length_lines = target_lines[target_lengths == length_raw]

        # 巨大 line は index/gather 配列を作らず、slice 演算を用いる。
        if length > _BULK_VERTEX_CHUNK:
            for line_index in same_length_lines:
                s = int(starts[line_index])
                e = int(ends[line_index])
                vertices = coords64[s:e]
                center_source = vertices if mode == "by_line" else vertices[:-1]
                center = center_source.mean(axis=0)
                coords64[s:e] = (vertices - center) * factors + center
            continue

        lines_per_chunk = max(1, _BULK_VERTEX_CHUNK // length)
        relative_indices = np.arange(length, dtype=np.intp)
        for chunk_start in range(0, int(same_length_lines.size), lines_per_chunk):
            chunk_lines = same_length_lines[chunk_start : chunk_start + lines_per_chunk]
            vertex_indices = (
                starts[chunk_lines].astype(np.intp, copy=False)[:, None] + relative_indices
            )
            vertices = coords64[vertex_indices]
            _scale_vertex_block(vertices, mode=mode, factors=factors)
            coords64[vertex_indices] = vertices


@effect(meta=scale_meta, ui_visible=scale_ui_visible)
def scale(
    g: GeomTuple,
    *,
    mode: str = "all",
    auto_center: bool = True,
    pivot: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> GeomTuple:
    """スケール変換を適用（auto_center 対応）。

    Parameters
    ----------
    g : tuple[np.ndarray, np.ndarray]
        スケール対象の実体ジオメトリ（coords, offsets）。
    mode : {"all","by_line","by_face"}, default "all"
        `"all"` は入力全体を 1 つの中心でスケールする。
        `"by_line"` は開ポリラインごとに中心を維持してスケールする（閉曲線は対象外）。
        `"by_face"` は閉曲線ごとに中心を維持してスケールする（開ポリラインは対象外）。
    auto_center : bool, default True
        True なら平均座標を中心に使用。False なら `pivot` を使用（`mode="all"` のときのみ有効）。
    pivot : tuple[float, float, float], default (0.0,0.0,0.0)
        変換の中心（`mode="all"` かつ `auto_center=False` のとき有効）。
    scale : tuple[float, float, float], default (1.0,1.0,1.0)
        各軸の倍率。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        スケール後の実体ジオメトリ（coords, offsets）。
    """
    coords, offsets = g
    if coords.shape[0] == 0:
        return coords, offsets

    sx, sy, sz = scale
    if sx == 1.0 and sy == 1.0 and sz == 1.0:
        return coords, offsets

    factors = np.array([sx, sy, sz], dtype=np.float64)

    if mode == "all":
        # 中心を決定（auto_center 優先）
        if auto_center:
            coords64 = coords.astype(np.float64, copy=True)
            center = coords64.mean(axis=0)
        else:
            center = np.asarray(pivot, dtype=np.float64)
            coords64 = coords.astype(np.float64, copy=True)

        if (
            coords64.shape[0] >= _AXIS_WISE_VERTEX_THRESHOLD
            and _split_ufunc_values_are_safe(factors)
            and _split_ufunc_values_are_safe(center)
        ):
            for axis in range(3):
                axis_coords = coords64[:, axis]
                axis_coords -= center[axis]
                axis_coords *= factors[axis]
                axis_coords += center[axis]
        else:
            coords64 -= center
            coords64 *= factors
            coords64 += center
        coords_out = coords64.astype(np.float32, copy=False)
        return coords_out, offsets

    coords64 = coords.astype(np.float64, copy=True)
    line_count = int(offsets.size) - 1
    if _can_scale_polylines_in_bulk(
        line_count=line_count,
        factors=factors,
    ):
        _scale_polylines_bulk(coords64, offsets, mode=mode, factors=factors)
    else:
        _scale_polylines_loop(coords64, offsets, mode=mode, factors=factors)

    coords_out = coords64.astype(np.float32, copy=False)
    return coords_out, offsets
