from __future__ import annotations

"""
どこで: `util` のポリゴングルーピング補助。
何を: 複数の閉ループ（外環・内環）から偶奇規則のグループ（外環ごとに穴をぶら下げた集合）を構築する。
なぜ: `effects.fill` 等で穴のある形状を正しくハッチ処理するため。

提供関数:
- `build_evenodd_groups(coords, offsets) -> list[list[int]]`
    - 入力の `coords (N,3)` / `offsets (M+1,)` に対し、各リングの代表点で包含関係を判定し、
      外環ごとにグループ（[outer, hole, hole, ...]）を返す。

実装メモ:
- 内包判定は重心ではなく「代表点（第1頂点）」を用いる。重心は非凸やドーナツで破綻しやすいため。
- on-edge の取り扱いはレイキャスティング実装の半開区間条件に依存（既存仕様を踏襲）。
"""

from typing import List

import numpy as np
from numba import njit


@njit(cache=True)
def point_in_polygon_njit(polygon: np.ndarray, x: float, y: float) -> bool:
    """レイキャスティングで点の内外を判定（Numba最適化）。

    半開区間の条件で辺上近傍の反転を抑制する。閉ループを前提に `% n` で終端を接続する。
    """
    n = len(polygon)
    inside = False

    p1x, p1y = polygon[0, 0], polygon[0, 1]
    for i in range(1, n + 1):
        p2x, p2y = polygon[i % n, 0], polygon[i % n, 1]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y

    return inside


def _polygon_area_signed_2d(poly2d: np.ndarray) -> float:
    x = poly2d[:, 0]
    y = poly2d[:, 1]
    s = 0.0
    n = poly2d.shape[0]
    for i in range(n):
        j = (i + 1) % n
        s += float(x[i] * y[j] - x[j] * y[i])
    return 0.5 * s


def _polygon_centroid_2d(poly2d: np.ndarray) -> tuple[float, float]:
    a = _polygon_area_signed_2d(poly2d)
    if abs(a) < 1e-12:
        c = np.mean(poly2d, axis=0)
        return float(c[0]), float(c[1])
    cx = 0.0
    cy = 0.0
    n = poly2d.shape[0]
    for i in range(n):
        j = (i + 1) % n
        cross = float(poly2d[i, 0] * poly2d[j, 1] - poly2d[j, 0] * poly2d[i, 1])
        cx += (poly2d[i, 0] + poly2d[j, 0]) * cross
        cy += (poly2d[i, 1] + poly2d[j, 1]) * cross
    f = 1.0 / (6.0 * a)
    return float(cx * f), float(cy * f)


def build_evenodd_groups(coords: np.ndarray, offsets: np.ndarray) -> list[list[int]]:
    """偶奇規則に基づき、外環ごとに穴をぶら下げたグループを構築する。

    引数:
        coords: 形状 `(N, 3)` の座標配列（XY のみ使用）。
        offsets: 各ポリラインの開始 index（長さ M+1、末尾は N）。

    返り値:
        各グループはリング index のリスト（先頭が外環、その後に穴）。
        グループ順は入力に現れた外環の順。外環が見つからない場合は単独グループで返す。
    """
    n = len(offsets) - 1
    if n <= 0:
        return []

    polys2d: List[np.ndarray] = []
    reps: List[tuple[float, float]] = []
    areas_abs: List[float] = []
    for i in range(n):
        s, e = int(offsets[i]), int(offsets[i + 1])
        p2d = coords[s:e, :2].astype(np.float32, copy=False)
        polys2d.append(p2d)
        if p2d.shape[0] > 0:
            reps.append((float(p2d[0, 0]), float(p2d[0, 1])))
        else:
            reps.append((float("nan"), float("nan")))
        areas_abs.append(abs(_polygon_area_signed_2d(p2d)))

    containers: List[list[int]] = [[] for _ in range(n)]
    for i in range(n):
        rx, ry = reps[i]
        for j in range(n):
            if i == j:
                continue
            if point_in_polygon_njit(polys2d[j], float(rx), float(ry)):
                containers[i].append(j)

    # 偶奇（包含数が偶数なら外環）
    is_outer = [(len(containers[i]) % 2) == 0 for i in range(n)]
    outer_indices = [i for i in range(n) if is_outer[i]]
    groups: dict[int, list[int]] = {oi: [oi] for oi in outer_indices}

    for i in range(n):
        if is_outer[i]:
            continue
        cands = [j for j in outer_indices if j in containers[i]]
        if cands:
            j_best = min(cands, key=lambda j: areas_abs[j])
            groups.setdefault(j_best, []).append(i)
        else:
            groups.setdefault(i, []).append(i)

    ordered: list[list[int]] = []
    for oi in outer_indices:
        ring_is = groups.get(oi, [oi])
        ordered.append(sorted(ring_is))
    for k, v in groups.items():
        if k not in outer_indices:
            ordered.append(sorted(v))
    return ordered
