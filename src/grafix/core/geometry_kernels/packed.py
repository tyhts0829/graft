"""
Purpose:
    polyline列をcoreが共有するcanonical packed geometry表現へ集約する。
Use when:
    effectやprimitiveのline列をRealizedGeometry互換bufferへ変換する場合。
Constraints:
    - coordsをfloat32の(N, 3)、offsetsをint32で表し、先頭0・末尾Nを保つ。
    - 入力line順と空lineを含む境界をoffsetsへそのまま保持する。
    - int32頂点上限を確保前に拒否し、出力bufferを入力arrayのmutable aliasにしない。
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def empty_packed_geometry() -> tuple[np.ndarray, np.ndarray]:
    """空のpacked geometryを標準dtypeで返す。"""

    return np.zeros((0, 3), dtype=np.float32), np.zeros((1,), dtype=np.int32)


def pack_polylines(lines: Sequence[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """shape ``(N,3)`` のpolyline列をfloat32/int32のpacked geometryへ詰める。"""

    if not lines:
        return empty_packed_geometry()
    arrays = tuple(np.asarray(line) for line in lines)
    if any(array.ndim != 2 or array.shape[1] != 3 for array in arrays):
        raise ValueError("polyline は shape (N,3) の配列である必要がある")

    counts = np.fromiter((array.shape[0] for array in arrays), dtype=np.int64)
    offsets64 = np.empty((len(arrays) + 1,), dtype=np.int64)
    offsets64[0] = 0
    np.cumsum(counts, out=offsets64[1:])
    if int(offsets64[-1]) > int(np.iinfo(np.int32).max):
        raise ValueError("packed geometry の頂点数が int32 上限を超えている")

    coords = np.empty((int(offsets64[-1]), 3), dtype=np.float32)
    for index, array in enumerate(arrays):
        start = int(offsets64[index])
        stop = int(offsets64[index + 1])
        coords[start:stop] = array
    return coords, offsets64.astype(np.int32)
