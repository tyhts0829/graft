# どこで: `src/render/index_buffer.py`。
# 何を: RealizedGeometry.offsets から GL_LINES 用インデックス配列を生成する。
# なぜ: インデックス生成を純粋関数として切り出し、テストしやすくするため。

from __future__ import annotations

import numpy as np

from src.render.line_mesh import LineMesh


def build_line_indices(offsets: np.ndarray) -> np.ndarray:
    """RealizedGeometry.offsets から GL_LINES 用インデックス配列を生成する。"""
    indices: list[int] = []
    for i in range(len(offsets) - 1):
        start = int(offsets[i])
        end = int(offsets[i + 1])
        if end - start < 2:
            continue
        for k in range(start, end - 1):
            indices.append(k)
            indices.append(k + 1)
        if i < len(offsets) - 2:
            indices.append(LineMesh.PRIMITIVE_RESTART_INDEX)
    if not indices:
        return np.zeros((0,), dtype=np.uint32)
    return np.asarray(indices, dtype=np.uint32)
