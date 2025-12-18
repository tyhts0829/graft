"""
subdivide エフェクト（線の細分化）

- 各ポリラインの全セグメントへ中点挿入を繰り返し、滑らかさと頂点密度を上げる。
- 最小セグメント長と最大回数でガードし、過剰分割や極短線での暴走を防止する。

Parameters
----------
g : Geometry
    入力ジオメトリ。各行が 1 本のポリラインを表す（`offsets` で区切る）。
subdivisions : int, default 5
    分割回数（0–10 でクランプ）。0 以下は no-op。

Returns
-------
Geometry
    分割後のジオメトリ。`Geometry.from_lines` で正規化され、dtype/offsets の不変条件を満たす。

Notes
-----
- 極短セグメントを含む線は、最短セグメント長が閾値を下回るとそこで分割を停止（sqrt 回避のため二乗距離で判定）。
- 頂点数は概ね `2^d * (n-1) + 1` に増加するため、大きな d ではコストが増える点に留意。
- 合計頂点数の上限ガード（`MAX_TOTAL_VERTICES=10_000_000`）に達した時点で以降のライン処理を打ち切る。
"""

from __future__ import annotations

import numpy as np
from numba import njit  # type: ignore[attr-defined]

from engine.core.geometry import Geometry

from .registry import effect

# モジュール定数（停止条件/回数上限）
MAX_SUBDIVISIONS = 10
MIN_SEG_LEN = 0.01
MIN_SEG_LEN_SQ = float(MIN_SEG_LEN * MIN_SEG_LEN)
# 出力合計頂点数の上限（ガード）
MAX_TOTAL_VERTICES = 10_000_000
PARAM_META = {
    "subdivisions": {"type": "integer", "min": 0, "max": MAX_SUBDIVISIONS, "step": 1},
}


@effect()
def subdivide(g: Geometry, *, subdivisions: int = 0) -> Geometry:
    """中間点を追加して線を細分化。

    Parameters
    ----------
    g : Geometry
        入力ジオメトリ。
    subdivisions : int, default 5
        分割回数（0 で no-op, 10 にクランプ）。
    """
    coords, offsets = g.as_arrays(copy=False)
    if subdivisions <= 0:
        return Geometry(coords.copy(), offsets.copy())

    divisions = int(subdivisions)
    if divisions > MAX_SUBDIVISIONS:
        divisions = MAX_SUBDIVISIONS
    if divisions <= 0:
        return Geometry(coords.copy(), offsets.copy())

    result: list[np.ndarray] = []
    total_vertices = 0
    for i in range(len(offsets) - 1):
        vertices = coords[offsets[i] : offsets[i + 1]]
        base_n = int(vertices.shape[0])
        # 合計上限ガード: 残余許容量が基礎点数を下回る場合は以降を打ち切り
        remaining = MAX_TOTAL_VERTICES - total_vertices
        if remaining <= 0 or remaining < base_n:
            break
        # 各ラインに対する上限を渡し、反復途中で停止可能にする
        subdivided = _subdivide_core(vertices, divisions, int(remaining))
        result.append(subdivided)
        total_vertices += int(subdivided.shape[0])

    return Geometry.from_lines(result)


subdivide.__param_meta__ = PARAM_META


@njit(fastmath=True, cache=True)
def _subdivide_core(vertices: np.ndarray, subdivisions: int, max_vertices: int) -> np.ndarray:
    """単一頂点配列の細分化処理（Numba 最適化）。

    - 全セグメントの最小二乗距離が `MIN_SEG_LEN_SQ` 未満なら分割を停止。
    - 分割回数は `MAX_SUBDIVISIONS` でクランプ。
    """
    n0 = vertices.shape[0]
    if n0 < 2 or subdivisions <= 0:
        return vertices

    # 初期の全体最小セグ長チェック（平方長で比較、sqrt 回避）
    d0 = vertices[1:] - vertices[:-1]
    if d0.shape[0] > 0:
        dsq0 = d0[:, 0] * d0[:, 0] + d0[:, 1] * d0[:, 1] + d0[:, 2] * d0[:, 2]
        if np.min(dsq0) < MIN_SEG_LEN_SQ:  # type: ignore[operator]
            return vertices

    # 分割回数制限 - フリーズ防止
    subdivisions = subdivisions if subdivisions <= MAX_SUBDIVISIONS else MAX_SUBDIVISIONS

    result = vertices.copy()
    for _ in range(subdivisions):
        n = result.shape[0]
        if n < 2:
            break
        # 上限を超える見込みならここで停止
        new_n = 2 * n - 1
        if max_vertices > 0 and new_n > max_vertices:
            break
        new_vertices = np.empty((new_n, result.shape[1]), dtype=result.dtype)

        # 偶数インデックスに元の頂点を配置
        new_vertices[::2] = result

        # 奇数インデックスに中点を配置
        new_vertices[1::2] = (result[:-1] + result[1:]) / 2

        result = new_vertices

        # 再度、全体最小セグ長による停止判定
        d = result[1:] - result[:-1]
        if d.shape[0] > 0:
            dsq = d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1] + d[:, 2] * d[:, 2]
            if np.min(dsq) < MIN_SEG_LEN_SQ:  # type: ignore[operator]
                break

    return result
