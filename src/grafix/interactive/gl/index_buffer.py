"""
Purpose:
    packed polyline境界を、GL line-strip用indexと描画統計へ変換するpure adapterを提供する。
Use when:
    RealizedGeometry offsetsとprimitive-restart indexの対応を変更する場合。
Constraints:
    - polyline境界と頂点順を保持し、空lineを描画対象へ数えない。
    - GPU resourceを所有せず、CPU上のcanonical indexだけを返す。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numba import njit  # type: ignore[attr-defined]

from grafix.interactive.gl.line_mesh import LineMesh


@dataclass(frozen=True, slots=True)
class LineIndexStats:
    """描画対象の polyline 由来の簡易統計。"""

    draw_vertices: int
    draw_lines: int


def build_line_indices_and_stats(offsets: np.ndarray) -> tuple[np.ndarray, LineIndexStats]:
    """RealizedGeometry.offsets から indices と描画統計をまとめて生成する。"""

    offsets_i32 = np.asarray(offsets, dtype=np.int32)
    if offsets_i32.size < 2:
        return np.zeros((0,), dtype=np.uint32), LineIndexStats(draw_vertices=0, draw_lines=0)
    indices, vertices, lines = _build_line_strip_indices_and_stats_numba(
        offsets_i32,
        np.uint32(LineMesh.PRIMITIVE_RESTART_INDEX),
    )
    indices.setflags(write=False)
    return indices, LineIndexStats(draw_vertices=int(vertices), draw_lines=int(lines))


@njit(cache=True)  # type: ignore[misc]
def _build_line_strip_indices_and_stats_numba(
    offsets: np.ndarray,
    restart_index: np.uint32,
) -> tuple[np.ndarray, int, int]:
    """GL_LINE_STRIP + primitive restart 用の indices と (vertices, lines) を生成する（Numba 版）。"""
    n = offsets.shape[0]
    if n < 2:
        return np.empty((0,), dtype=np.uint32), 0, 0

    total_vertices = 0
    polyline_count = 0
    for i in range(n - 1):
        length = offsets[i + 1] - offsets[i]
        if length >= 2:
            total_vertices += length
            polyline_count += 1

    if polyline_count == 0:
        return np.empty((0,), dtype=np.uint32), 0, 0

    total_count = total_vertices + (polyline_count - 1)
    out = np.empty((total_count,), dtype=np.uint32)

    cursor = 0
    emitted_any = False
    for i in range(n - 1):
        start = offsets[i]
        end = offsets[i + 1]
        length = end - start
        if length < 2:
            continue

        if emitted_any:
            out[cursor] = restart_index
            cursor += 1

        for j in range(length):
            out[cursor] = start + j
            cursor += 1

        emitted_any = True

    return out, total_vertices, polyline_count
