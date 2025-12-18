# どこで: `src/graft/interactive/gl/index_buffer.py`。
# 何を: RealizedGeometry.offsets から GL_LINES 用インデックス配列を生成する。
# なぜ: インデックス生成を純粋関数として切り出し、テストしやすくするため。

from __future__ import annotations

from functools import lru_cache

import numpy as np

from graft.interactive.gl.line_mesh import LineMesh


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


@lru_cache(maxsize=64)
def _build_line_strip_indices_cached(offsets_bytes: bytes) -> np.ndarray:
    offsets = np.frombuffer(offsets_bytes, dtype=np.int32)
    if offsets.size < 2:
        return np.zeros((0,), dtype=np.uint32)

    lengths = offsets[1:] - offsets[:-1]
    emitted = lengths >= 2
    polyline_count = int(np.count_nonzero(emitted))
    if polyline_count <= 0:
        return np.zeros((0,), dtype=np.uint32)

    total_vertices = int(lengths[emitted].astype(np.int64, copy=False).sum())
    total_count = total_vertices + (polyline_count - 1)
    out = np.empty((total_count,), dtype=np.uint32)

    cursor = 0
    emitted_any = False
    for i in range(lengths.size):
        length = int(lengths[i])
        if length < 2:
            continue
        start = int(offsets[i])
        end = start + length
        if emitted_any:
            out[cursor] = LineMesh.PRIMITIVE_RESTART_INDEX
            cursor += 1
        out[cursor : cursor + length] = np.arange(start, end, dtype=np.uint32)
        cursor += length
        emitted_any = True

    assert cursor == total_count

    out.setflags(write=False)
    return out
