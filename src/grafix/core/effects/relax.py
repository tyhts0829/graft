"""線分ネットワークをグラフとして扱い、簡易な弾性緩和で形を整える effect。"""

from __future__ import annotations

from typing import Sequence

import numpy as np
from numba import njit  # type: ignore[import-untyped]

from grafix.core.effect_registry import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import RealizedGeometry

relax_meta = {
    "relaxation_iterations": ParamMeta(kind="int", ui_min=0, ui_max=50),
    "step": ParamMeta(kind="float", ui_min=0.0, ui_max=0.5),
}

MAX_RELAXATION_ITERATIONS = 50
MAX_STEP = 0.5


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _iter_ranges(offsets: np.ndarray):
    for i in range(int(offsets.size) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        yield s, e


def _build_nodes(coords: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """座標配列からユニークノードと頂点→ノード対応を作る（完全一致のみ）。"""
    index_by_xyz: dict[tuple[float, float, float], int] = {}
    vertex_to_node = np.empty((coords.shape[0],), dtype=np.int64)
    nodes: list[tuple[float, float, float]] = []

    for i in range(int(coords.shape[0])):
        key = (float(coords[i, 0]), float(coords[i, 1]), float(coords[i, 2]))
        idx = index_by_xyz.get(key)
        if idx is None:
            idx = len(nodes)
            index_by_xyz[key] = idx
            nodes.append(key)
        vertex_to_node[i] = int(idx)

    nodes_arr = np.asarray(nodes, dtype=np.float64)
    return nodes_arr, vertex_to_node


def _build_edges(offsets: np.ndarray, vertex_to_node: np.ndarray) -> np.ndarray:
    edges: set[tuple[int, int]] = set()
    for s, e in _iter_ranges(offsets):
        for i in range(int(s), int(e) - 1):
            a = int(vertex_to_node[i])
            b = int(vertex_to_node[i + 1])
            if a == b:
                continue
            if a < b:
                edges.add((a, b))
            else:
                edges.add((b, a))

    if not edges:
        return np.zeros((0, 2), dtype=np.int64)
    return np.asarray(sorted(edges), dtype=np.int64)


def _build_adjacency(num_nodes: int, edges: np.ndarray) -> list[list[int]]:
    adjacency: list[list[int]] = [[] for _ in range(int(num_nodes))]
    for k in range(int(edges.shape[0])):
        a = int(edges[k, 0])
        b = int(edges[k, 1])
        adjacency[a].append(b)
        adjacency[b].append(a)
    return adjacency


def _compute_fixed(nodes: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """固定点マスクを作る（次数!=2 + 連結成分の min/max）。"""
    num_nodes = int(nodes.shape[0])
    degrees = np.zeros((num_nodes,), dtype=np.int64)
    for k in range(int(edges.shape[0])):
        degrees[int(edges[k, 0])] += 1
        degrees[int(edges[k, 1])] += 1

    fixed = degrees != 2

    adjacency = _build_adjacency(num_nodes, edges)
    visited = np.zeros((num_nodes,), dtype=np.bool_)
    stack: list[int] = []

    for start in range(num_nodes):
        if visited[start]:
            continue
        visited[start] = True
        stack.append(int(start))
        component: list[int] = []
        while stack:
            i = stack.pop()
            component.append(int(i))
            for nb in adjacency[i]:
                if visited[nb]:
                    continue
                visited[nb] = True
                stack.append(int(nb))

        comp_nodes = nodes[np.asarray(component, dtype=np.int64)]
        if comp_nodes.shape[0] == 0:
            continue

        for axis in range(3):
            min_local = int(np.argmin(comp_nodes[:, axis]))
            max_local = int(np.argmax(comp_nodes[:, axis]))
            fixed[component[min_local]] = True
            fixed[component[max_local]] = True

    return fixed.astype(np.bool_, copy=False)


@njit(fastmath=True, cache=True)
def _elastic_relaxation_nb(positions, edges, fixed, iterations, step):
    n = positions.shape[0]
    for _it in range(iterations):
        forces = np.zeros((n, 3), dtype=positions.dtype)
        m = edges.shape[0]
        for e in range(m):
            i = edges[e, 0]
            j = edges[e, 1]
            diff0 = positions[j, 0] - positions[i, 0]
            diff1 = positions[j, 1] - positions[i, 1]
            diff2 = positions[j, 2] - positions[i, 2]
            forces[i, 0] += diff0
            forces[i, 1] += diff1
            forces[i, 2] += diff2
            forces[j, 0] -= diff0
            forces[j, 1] -= diff1
            forces[j, 2] -= diff2

        max_force = 10.0
        for i in range(n):
            fx = forces[i, 0]
            fy = forces[i, 1]
            fz = forces[i, 2]
            norm = np.sqrt(fx * fx + fy * fy + fz * fz)
            if norm > max_force:
                scale = max_force / norm
                forces[i, 0] *= scale
                forces[i, 1] *= scale
                forces[i, 2] *= scale

        for i in range(n):
            if not fixed[i]:
                positions[i, 0] += step * forces[i, 0]
                positions[i, 1] += step * forces[i, 1]
                positions[i, 2] += step * forces[i, 2]
    return positions


@effect(meta=relax_meta)
def relax(
    inputs: Sequence[RealizedGeometry],
    *,
    relaxation_iterations: int = 15,
    step: float = 0.125,
) -> RealizedGeometry:
    """線分ネットワークをグラフとして弾性緩和する。

    入力ポリライン群を 1 つの無向グラフとして扱い、同一点は共有ノードとして束ねる。
    端点/分岐（次数!=2）と、各連結成分の座標 min/max を固定し、残りの点を平滑化する。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        変形対象の実体ジオメトリ列。通常は 1 要素。
    relaxation_iterations : int, default 15
        反復回数（0–50 にクランプ）。
    step : float, default 0.125
        1 ステップの移動係数（0.0–0.5 にクランプ）。

    Returns
    -------
    RealizedGeometry
        緩和後の実体ジオメトリ。
    """
    if not inputs:
        return _empty_geometry()

    base = inputs[0]
    if base.coords.shape[0] == 0:
        return base

    iterations = int(relaxation_iterations)
    iterations = max(0, min(MAX_RELAXATION_ITERATIONS, iterations))

    step_size = float(step)
    if step_size < 0.0:
        step_size = 0.0
    if step_size > MAX_STEP:
        step_size = MAX_STEP

    if iterations == 0 or step_size == 0.0:
        return base

    nodes, vertex_to_node = _build_nodes(base.coords)
    edges = _build_edges(base.offsets, vertex_to_node)
    if edges.shape[0] == 0 or nodes.shape[0] == 0:
        return base

    fixed = _compute_fixed(nodes, edges)
    positions = nodes.copy()
    positions = _elastic_relaxation_nb(positions, edges, fixed, iterations, step_size)

    out_coords = positions[vertex_to_node].astype(np.float32, copy=False)
    return RealizedGeometry(coords=out_coords, offsets=base.offsets)


__all__ = ["relax", "relax_meta"]
