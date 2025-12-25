# どこで: `src/grafix/interactive/gl/index_buffer.py`。
# 何を: RealizedGeometry.offsets から GL_LINE_STRIP 用インデックス配列を生成する。
# なぜ: インデックス生成を純粋関数として切り出し、テストしやすくするため。

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from numba import njit  # type: ignore[attr-defined]

from grafix.interactive.gl.line_mesh import LineMesh


@dataclass(frozen=True, slots=True)
class LineIndexStats:
    """描画対象の polyline 由来の簡易統計。"""

    draw_vertices: int
    draw_lines: int


def build_line_indices(offsets: np.ndarray) -> np.ndarray:
    """RealizedGeometry.offsets から GL_LINE_STRIP 用インデックス配列を生成する。

    Notes
    -----
    - indices は offsets の内容だけで決まるため、内容ベースで LRU キャッシュする。
    - 複数ポリラインを 1 draw call で描くため、polyline 間に PRIMITIVE_RESTART_INDEX を挿入する。
    """
    offsets_i32 = np.asarray(offsets, dtype=np.int32)
    if offsets_i32.size < 2:
        return np.zeros((0,), dtype=np.uint32)
    return _build_line_strip_indices_cached(offsets_i32.tobytes())


def build_line_indices_and_stats(offsets: np.ndarray) -> tuple[np.ndarray, LineIndexStats]:
    """RealizedGeometry.offsets から indices と描画統計をまとめて生成する。"""

    offsets_i32 = np.asarray(offsets, dtype=np.int32)
    if offsets_i32.size < 2:
        return np.zeros((0,), dtype=np.uint32), LineIndexStats(draw_vertices=0, draw_lines=0)
    return _build_line_strip_indices_and_stats_cached(offsets_i32.tobytes())


@lru_cache(maxsize=64)
def _build_line_strip_indices_cached(offsets_bytes: bytes) -> np.ndarray:
    offsets = np.frombuffer(offsets_bytes, dtype=np.int32)
    out = _build_line_strip_indices_numba(
        offsets,
        np.uint32(LineMesh.PRIMITIVE_RESTART_INDEX),
    )
    out.setflags(write=False)
    return out


@lru_cache(maxsize=64)
def _build_line_strip_indices_and_stats_cached(
    offsets_bytes: bytes,
) -> tuple[np.ndarray, LineIndexStats]:
    offsets = np.frombuffer(offsets_bytes, dtype=np.int32)
    indices, vertices, lines = _build_line_strip_indices_and_stats_numba(
        offsets,
        np.uint32(LineMesh.PRIMITIVE_RESTART_INDEX),
    )
    indices.setflags(write=False)
    return indices, LineIndexStats(draw_vertices=int(vertices), draw_lines=int(lines))


@njit(cache=True)  # type: ignore[misc]
def _build_line_strip_indices_numba(offsets: np.ndarray, restart_index: np.uint32) -> np.ndarray:
    """GL_LINE_STRIP + primitive restart 用の indices を生成する（Numba 版）。"""
    n = offsets.shape[0]
    if n < 2:
        return np.empty((0,), dtype=np.uint32)

    total_vertices = 0
    polyline_count = 0
    for i in range(n - 1):
        length = offsets[i + 1] - offsets[i]
        if length >= 2:
            total_vertices += length
            polyline_count += 1

    if polyline_count == 0:
        return np.empty((0,), dtype=np.uint32)

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

    return out


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
