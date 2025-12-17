# どこで: `src/graft/interactive/gl/index_buffer.py`。
# 何を: RealizedGeometry.offsets から GL_LINES 用インデックス配列を生成する。
# なぜ: インデックス生成を純粋関数として切り出し、テストしやすくするため。

from __future__ import annotations

from functools import lru_cache

import numpy as np

from graft.interactive.gl.line_mesh import LineMesh


def build_line_indices(offsets: np.ndarray) -> np.ndarray:
    """RealizedGeometry.offsets から GL_LINES 用インデックス配列を生成する。

    Notes
    -----
    - indices は offsets の内容だけで決まるため、内容ベースで LRU キャッシュする。
    - キャッシュミス時も「頂点ごとの Python ループ」を避け、ポリライン単位の処理に寄せる。
    """
    offsets_i32 = np.asarray(offsets, dtype=np.int32)
    if offsets_i32.size < 2:
        return np.zeros((0,), dtype=np.uint32)
    return _build_line_indices_cached(offsets_i32.tobytes())


@lru_cache(maxsize=64)
def _build_line_indices_cached(offsets_bytes: bytes) -> np.ndarray:
    offsets = np.frombuffer(offsets_bytes, dtype=np.int32)
    if offsets.size < 2:
        return np.zeros((0,), dtype=np.uint32)

    lengths = offsets[1:] - offsets[:-1]
    edges = np.maximum(0, lengths - 1).astype(np.int64, copy=False)

    total_edges = int(edges.sum())
    if total_edges <= 0:
        return np.zeros((0,), dtype=np.uint32)

    restarts = int(np.count_nonzero(edges[:-1] > 0)) if edges.size > 1 else 0
    total_count = 2 * total_edges + restarts

    out = np.empty((total_count,), dtype=np.uint32)
    cursor = 0
    for i in range(edges.size):
        e = int(edges[i])
        if e <= 0:
            continue
        start = int(offsets[i])
        base = np.arange(start, start + e, dtype=np.uint32)
        out[cursor : cursor + 2 * e : 2] = base
        out[cursor + 1 : cursor + 2 * e : 2] = base + 1
        cursor += 2 * e
        if i < offsets.size - 2:
            out[cursor] = LineMesh.PRIMITIVE_RESTART_INDEX
            cursor += 1

    assert cursor == total_count

    out.setflags(write=False)
    return out
