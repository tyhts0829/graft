"""閉曲線にウェブ（糸）状の線分ネットワークを生成する effect。

入力ポリラインを一度 XY 平面へ整列し、境界上の 2 点をランダムに結ぶ候補線を繰り返し追加する。
各候補線は境界エッジを交点で分割し、交点同士をエッジで接続することでグラフとして構築する。
最後に簡易な弾性緩和（Laplacian 的な平滑化）で内部点を調整し、元の 3D 姿勢へ復元する。
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from numba import njit, types  # type: ignore[import-untyped]
from numba.typed import List  # type: ignore[attr-defined]

from grafix.core.effect_registry import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import RealizedGeometry
from .util import transform_back, transform_to_xy_plane

weave_meta = {
    "num_candidate_lines": ParamMeta(kind="int", ui_min=0, ui_max=500),
    "relaxation_iterations": ParamMeta(kind="int", ui_min=0, ui_max=50),
    "step": ParamMeta(kind="float", ui_min=0.0, ui_max=0.5),
}

MAX_NUM_CANDIDATE_LINES = 500
MAX_RELAXATION_ITERATIONS = 50
MAX_STEP = 0.5


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _iter_polylines(realized: RealizedGeometry):
    offsets = realized.offsets
    for i in range(int(offsets.size) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        yield realized.coords[s:e]


def _lines_to_realized(lines: Sequence[np.ndarray]) -> RealizedGeometry:
    """ポリライン列を RealizedGeometry にまとめる。"""
    if not lines:
        return _empty_geometry()

    coords_list: list[np.ndarray] = []
    offsets = np.zeros((len(lines) + 1,), dtype=np.int32)
    acc = 0
    for i, line in enumerate(lines):
        ln = np.asarray(line)
        if ln.ndim != 2 or ln.shape[1] != 3:
            raise ValueError("lines は shape (N,3) の配列列である必要がある")
        coords_list.append(ln.astype(np.float32, copy=False))
        acc += int(ln.shape[0])
        offsets[i + 1] = acc
    coords = (
        np.concatenate(coords_list, axis=0) if coords_list else np.zeros((0, 3), dtype=np.float32)
    )
    return RealizedGeometry(coords=coords, offsets=offsets)


def _is_closed_polyline(vertices: np.ndarray) -> bool:
    if vertices.shape[0] < 2:
        return False
    return bool(np.allclose(vertices[0], vertices[-1], rtol=0.0, atol=1e-6))


@effect(meta=weave_meta)
def weave(
    inputs: Sequence[RealizedGeometry],
    *,
    num_candidate_lines: int = 100,
    relaxation_iterations: int = 15,
    step: float = 0.125,
) -> RealizedGeometry:
    """入力閉曲線からウェブ状の線分ネットワークを生成する。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        入力実体ジオメトリ列。通常は 1 要素。
    num_candidate_lines : int, default 100
        候補線本数（0–500 にクランプ）。
    relaxation_iterations : int, default 15
        弾性緩和の反復回数（0–50 にクランプ）。
    step : float, default 0.125
        1 ステップの移動係数（0.0–0.5 にクランプ）。

    Returns
    -------
    RealizedGeometry
        ウェブ構造を含むポリライン集合。

    Notes
    -----
    開ポリライン（始点と終点が一致しない線）は対象外とし、そのまま返す。
    """
    if not inputs:
        return _empty_geometry()

    base = inputs[0]
    if base.coords.shape[0] == 0:
        return base

    num_lines = int(num_candidate_lines)
    num_lines = max(0, min(MAX_NUM_CANDIDATE_LINES, num_lines))

    iterations = int(relaxation_iterations)
    iterations = max(0, min(MAX_RELAXATION_ITERATIONS, iterations))

    step_size = float(step)
    if step_size < 0.0:
        step_size = 0.0
    if step_size > MAX_STEP:
        step_size = MAX_STEP

    out_lines: list[np.ndarray] = []
    did_webify = False
    for vertices in _iter_polylines(base):
        if vertices.shape[0] < 3:
            out_lines.append(vertices)
            continue
        if not _is_closed_polyline(vertices):
            out_lines.append(vertices)
            continue
        did_webify = True
        out_lines.extend(
            _webify_single_polyline(
                vertices,
                num_candidate_lines=num_lines,
                relaxation_iterations=iterations,
                step=step_size,
            )
        )

    if not did_webify:
        return base
    return _lines_to_realized(out_lines)


def _webify_single_polyline(
    vertices: np.ndarray,
    *,
    num_candidate_lines: int,
    relaxation_iterations: int,
    step: float,
) -> list[np.ndarray]:
    """単一ポリラインからウェブ状の線分群を生成して 3D に戻す。"""
    transformed, R, z = transform_to_xy_plane(vertices)
    polylines_xy = create_web(
        transformed,
        num_candidate_lines=num_candidate_lines,
        relaxation_iterations=relaxation_iterations,
        step=step,
    )
    return [transform_back(poly, R, z) for poly in polylines_xy]


@njit(fastmath=True, cache=True)
def line_segment_intersection_nb(Ax, Ay, Bx, By, p0x, p0y, p1x, p1y):
    r_x = Bx - Ax
    r_y = By - Ay
    s_x = p1x - p0x
    s_y = p1y - p0y
    rxs = r_x * s_y - r_y * s_x
    if abs(rxs) < 1e-8:
        return False, 0.0, 0.0, 0.0
    qp_x = p0x - Ax
    qp_y = p0y - Ay
    t = (qp_x * s_y - qp_y * s_x) / rxs
    u = (qp_x * r_y - qp_y * r_x) / rxs
    if t < 0 or t > 1 or u < 0 or u > 1:
        return False, 0.0, 0.0, 0.0
    inter_x = Ax + t * r_x
    inter_y = Ay + t * r_y
    return True, t, inter_x, inter_y


@njit(fastmath=True, cache=True, inline="always")
def fract(x):
    return x - math.floor(x)


@njit(fastmath=True, cache=True)
def generate_candidate_line_from_curve_nb(closed_curve, cl, seed):
    """指定された cl と seed に基づいて候補線（2 点）を生成する。"""
    N = closed_curve.shape[0]
    seed1 = cl * 12.9898 + seed + 78.233
    r1 = fract(math.sin(seed1) * 43758.5453)
    seed2 = cl * 93.9898 + seed + 12.345
    r2 = fract(math.sin(seed2) * 43758.5453)
    seed3 = cl * 45.1234 + seed + 98.765
    r3 = fract(math.sin(seed3) * 43758.5453)
    seed4 = cl * 67.8901 + seed + 23.456
    r4 = fract(math.sin(seed4) * 43758.5453)

    idx1 = int(r1 * N)
    next_idx1 = (idx1 + 1) % N
    A_x = closed_curve[idx1, 0] * (1 - r2) + closed_curve[next_idx1, 0] * r2
    A_y = closed_curve[idx1, 1] * (1 - r2) + closed_curve[next_idx1, 1] * r2

    idx2 = int(r3 * N)
    next_idx2 = (idx2 + 1) % N
    B_x = closed_curve[idx2, 0] * (1 - r4) + closed_curve[next_idx2, 0] * r4
    B_y = closed_curve[idx2, 1] * (1 - r4) + closed_curve[next_idx2, 1] * r4

    return A_x, A_y, B_x, B_y


@njit(fastmath=True, cache=True)
def generate_best_candidate_line_from_curve_nb(closed_curve, cl, base_seed, n_attempts):
    """同じ cl に対し、2 点間距離が最大となる候補線を選択する。"""
    best_dist2 = -1.0
    best_A_x = 0.0
    best_A_y = 0.0
    best_B_x = 0.0
    best_B_y = 0.0
    for i in range(n_attempts):
        current_seed = base_seed + i
        A_x, A_y, B_x, B_y = generate_candidate_line_from_curve_nb(closed_curve, cl, current_seed)
        dx = B_x - A_x
        dy = B_y - A_y
        dist2 = dx * dx + dy * dy
        if dist2 > best_dist2:
            best_dist2 = dist2
            best_A_x = A_x
            best_A_y = A_y
            best_B_x = B_x
            best_B_y = B_y
    return best_A_x, best_A_y, best_B_x, best_B_y


@njit(fastmath=True, cache=True)
def elastic_relaxation_nb(positions, edges, fixed, iterations, step):
    n = positions.shape[0]
    for _it in range(iterations):
        forces = np.zeros((n, 2), dtype=positions.dtype)
        m = edges.shape[0]
        for e in range(m):
            i = edges[e, 0]
            j = edges[e, 1]
            diff0 = positions[j, 0] - positions[i, 0]
            diff1 = positions[j, 1] - positions[i, 1]
            forces[i, 0] += diff0
            forces[i, 1] += diff1
            forces[j, 0] -= diff0
            forces[j, 1] -= diff1

        max_force = 10.0
        for i in range(n):
            fx = forces[i, 0]
            fy = forces[i, 1]
            norm = np.sqrt(fx * fx + fy * fy)
            if norm > max_force:
                scale = max_force / norm
                forces[i, 0] *= scale
                forces[i, 1] *= scale

        for i in range(n):
            if not fixed[i]:
                positions[i, 0] += step * forces[i, 0]
                positions[i, 1] += step * forces[i, 1]
    return positions


@njit(fastmath=True, cache=True)
def build_adjacency_arrays(num_nodes, edges):
    """辞書ではなく配列で隣接リストを構築する。

    Notes
    -----
    visited 管理を O(num_nodes^2) から O(num_edges) に落とすため、
    近傍ノードだけでなく「その近傍へ接続する edge_id」も同じ並びで保持する。
    """
    degrees = np.zeros(num_nodes, dtype=np.int32)
    for i in range(edges.shape[0]):
        a, b = edges[i, 0], edges[i, 1]
        degrees[a] += 1
        degrees[b] += 1

    max_degree = 0
    for i in range(num_nodes):
        d = degrees[i]
        if d > max_degree:
            max_degree = d

    adjacency = np.full((num_nodes, max_degree), -1, dtype=np.int32)
    adjacency_edge_ids = np.full((num_nodes, max_degree), -1, dtype=np.int32)
    adj_counts = np.zeros(num_nodes, dtype=np.int32)

    for i in range(edges.shape[0]):
        a, b = edges[i, 0], edges[i, 1]
        slot_a = adj_counts[a]
        adjacency[a, slot_a] = b
        adjacency_edge_ids[a, slot_a] = i
        adj_counts[a] += 1

        slot_b = adj_counts[b]
        adjacency[b, slot_b] = a
        adjacency_edge_ids[b, slot_b] = i
        adj_counts[b] += 1

    return adjacency, adjacency_edge_ids, degrees


@njit(fastmath=True, cache=True)
def trace_chain(
    start,
    first_neighbor,
    first_edge_id,
    adjacency,
    adjacency_edge_ids,
    degrees,
    visited_edges,
    max_chain_length=10000,
):
    chain = np.empty(max_chain_length, dtype=np.int32)
    chain[0] = start
    chain[1] = first_neighbor
    chain_length = 2

    visited_edges[first_edge_id] = True

    prev = start
    current = first_neighbor

    while degrees[current] == 2 and chain_length < max_chain_length:
        next_node = -1
        next_edge_id = -1
        for i in range(adjacency.shape[1]):
            neighbor = adjacency[current, i]
            if neighbor == -1:
                break
            if neighbor == prev:
                continue
            edge_id = adjacency_edge_ids[current, i]
            if visited_edges[edge_id]:
                continue
            next_node = neighbor
            next_edge_id = edge_id
            break

        if next_node == -1:
            break

        chain[chain_length] = next_node
        chain_length += 1
        visited_edges[next_edge_id] = True

        prev = current
        current = next_node

    return chain[:chain_length], chain_length


@njit(fastmath=True, cache=True)
def trace_cycle(start, adjacency, adjacency_edge_ids, visited_edges, max_cycle_length=10000):
    cycle = np.empty(max_cycle_length, dtype=np.int32)
    cycle[0] = start
    cycle_length = 1

    first_neighbor = -1
    first_edge_id = -1
    for i in range(adjacency.shape[1]):
        neighbor = adjacency[start, i]
        if neighbor == -1:
            break
        edge_id = adjacency_edge_ids[start, i]
        if not visited_edges[edge_id]:
            first_neighbor = neighbor
            first_edge_id = edge_id
            break

    if first_neighbor == -1:
        return cycle[:0], 0

    cycle[1] = first_neighbor
    cycle_length = 2
    visited_edges[first_edge_id] = True

    prev = start
    current = first_neighbor

    while cycle_length < max_cycle_length:
        next_node = -1
        next_edge_id = -1
        for i in range(adjacency.shape[1]):
            neighbor = adjacency[current, i]
            if neighbor == -1:
                break
            if neighbor == prev:
                continue
            edge_id = adjacency_edge_ids[current, i]
            if visited_edges[edge_id]:
                continue
            next_node = neighbor
            next_edge_id = edge_id
            break

        if next_node == -1:
            for i in range(adjacency.shape[1]):
                neighbor = adjacency[current, i]
                if neighbor == -1:
                    break
                if neighbor == start:
                    edge_id = adjacency_edge_ids[current, i]
                    visited_edges[edge_id] = True
                    return cycle[:cycle_length], cycle_length
            break

        if next_node == start:
            visited_edges[next_edge_id] = True
            return cycle[:cycle_length], cycle_length

        cycle[cycle_length] = next_node
        cycle_length += 1
        visited_edges[next_edge_id] = True

        prev = current
        current = next_node

    return cycle[:cycle_length], cycle_length


@njit(
    types.ListType(types.float64[:, :])(types.float64[:, :], types.int64[:, :]),
    fastmath=True,
    cache=True,
)
def merge_edges_into_polylines(nodes, edges):
    """ノード集合とエッジから連結成分をポリラインへ変換する。"""
    num_nodes = nodes.shape[0]
    adjacency, adjacency_edge_ids, degrees = build_adjacency_arrays(num_nodes, edges)

    visited_edges = np.zeros(edges.shape[0], dtype=np.bool_)
    polylines = List.empty_list(types.float64[:, :])

    for i in range(num_nodes):
        if degrees[i] != 2:
            for j in range(adjacency.shape[1]):
                neighbor = adjacency[i, j]
                if neighbor == -1:
                    break
                edge_id = adjacency_edge_ids[i, j]
                if visited_edges[edge_id]:
                    continue
                chain, chain_length = trace_chain(
                    i,
                    neighbor,
                    edge_id,
                    adjacency,
                    adjacency_edge_ids,
                    degrees,
                    visited_edges,
                )
                if chain_length >= 2:
                    polyline = np.empty((chain_length, 3), dtype=np.float64)
                    for k in range(chain_length):
                        node_idx = chain[k]
                        polyline[k, 0] = nodes[node_idx, 0]
                        polyline[k, 1] = nodes[node_idx, 1]
                        polyline[k, 2] = 0.0
                    polylines.append(polyline)

    for i in range(num_nodes):
        if degrees[i] == 2:
            has_unvisited = False
            for j in range(adjacency.shape[1]):
                edge_id = adjacency_edge_ids[i, j]
                if edge_id == -1:
                    break
                if not visited_edges[edge_id]:
                    has_unvisited = True
                    break

            if has_unvisited:
                cycle, cycle_length = trace_cycle(i, adjacency, adjacency_edge_ids, visited_edges)
                if cycle_length >= 2:
                    polyline = np.empty((cycle_length, 3), dtype=np.float64)
                    for k in range(cycle_length):
                        node_idx = cycle[k]
                        polyline[k, 0] = nodes[node_idx, 0]
                        polyline[k, 1] = nodes[node_idx, 1]
                        polyline[k, 2] = 0.0
                    polylines.append(polyline)

    return polylines


@njit(fastmath=True, cache=True)
def create_web_nb(closed_curve, num_candidate_lines, relaxation_iterations, step):
    n = closed_curve.shape[0]
    max_nodes = n + 2 * num_candidate_lines
    nodes = np.zeros((max_nodes, 3), dtype=np.float64)
    for i in range(n):
        nodes[i, 0] = closed_curve[i, 0]
        nodes[i, 1] = closed_curve[i, 1]
        nodes[i, 2] = 0.0
    current_n = n

    max_edges = n + 5 * num_candidate_lines
    edges = np.zeros((max_edges, 2), dtype=np.int64)
    valid_edges = np.zeros(max_edges, dtype=np.bool_)
    for i in range(n):
        edges[i, 0] = i
        edges[i, 1] = (i + 1) % n
        valid_edges[i] = True
    current_m = n

    for cl in range(num_candidate_lines):
        A_x, A_y, B_x, B_y = generate_best_candidate_line_from_curve_nb(
            closed_curve, cl, base_seed=0, n_attempts=2
        )
        max_int = 20
        t_vals = np.empty(max_int, dtype=np.float64)
        edge_indices = np.empty(max_int, dtype=np.int64)
        int_x = np.empty(max_int, dtype=np.float64)
        int_y = np.empty(max_int, dtype=np.float64)
        count = 0

        for e in range(current_m):
            if not valid_edges[e]:
                continue
            i = edges[e, 0]
            j = edges[e, 1]
            p0x = nodes[i, 0]
            p0y = nodes[i, 1]
            p1x = nodes[j, 0]
            p1y = nodes[j, 1]
            hit, t_val, ix, iy = line_segment_intersection_nb(
                A_x, A_y, B_x, B_y, p0x, p0y, p1x, p1y
            )
            if hit and count < max_int:
                t_vals[count] = t_val
                edge_indices[count] = e
                int_x[count] = ix
                int_y[count] = iy
                count += 1

        if count >= 2:
            min1 = 1.0e9
            min2 = 1.0e9
            idx1 = -1
            idx2 = -1
            for k in range(count):
                if t_vals[k] < min1:
                    min2 = min1
                    idx2 = idx1
                    min1 = t_vals[k]
                    idx1 = k
                elif t_vals[k] < min2:
                    min2 = t_vals[k]
                    idx2 = k

            if idx1 >= 0 and idx2 >= 0:
                e1 = edge_indices[idx1]
                i1 = edges[e1, 0]
                j1 = edges[e1, 1]
                valid_edges[e1] = False
                new_node1 = current_n
                nodes[new_node1, 0] = int_x[idx1]
                nodes[new_node1, 1] = int_y[idx1]
                nodes[new_node1, 2] = 0.0
                current_n += 1
                edges[current_m, 0] = i1
                edges[current_m, 1] = new_node1
                valid_edges[current_m] = True
                current_m += 1
                edges[current_m, 0] = new_node1
                edges[current_m, 1] = j1
                valid_edges[current_m] = True
                current_m += 1

                e2 = edge_indices[idx2]
                i2 = edges[e2, 0]
                j2 = edges[e2, 1]
                valid_edges[e2] = False
                new_node2 = current_n
                nodes[new_node2, 0] = int_x[idx2]
                nodes[new_node2, 1] = int_y[idx2]
                nodes[new_node2, 2] = 0.0
                current_n += 1
                edges[current_m, 0] = i2
                edges[current_m, 1] = new_node2
                valid_edges[current_m] = True
                current_m += 1
                edges[current_m, 0] = new_node2
                edges[current_m, 1] = j2
                valid_edges[current_m] = True
                current_m += 1

                edges[current_m, 0] = new_node1
                edges[current_m, 1] = new_node2
                valid_edges[current_m] = True
                current_m += 1

    valid_count = 0
    for e in range(current_m):
        if valid_edges[e]:
            valid_count += 1
    valid_edges_arr = np.empty((valid_count, 2), dtype=np.int64)
    idx = 0
    for e in range(current_m):
        if valid_edges[e]:
            valid_edges_arr[idx, 0] = edges[e, 0]
            valid_edges_arr[idx, 1] = edges[e, 1]
            idx += 1

    fixed = np.zeros(current_n, dtype=np.bool_)
    for i in range(n):
        fixed[i] = True

    positions = nodes[:current_n, 0:2].copy()
    positions = elastic_relaxation_nb(positions, valid_edges_arr, fixed, relaxation_iterations, step)
    nodes[:current_n, 0:2] = positions

    return nodes[:current_n], valid_edges_arr


def create_web(
    closed_curve: np.ndarray,
    *,
    num_candidate_lines: int = 10,
    relaxation_iterations: int = 20,
    step: float = 0.1,
) -> list[np.ndarray]:
    """XY 平面上の閉曲線からウェブ状のポリライン列を生成する。"""
    nodes, edges = create_web_nb(closed_curve, num_candidate_lines, relaxation_iterations, step)
    polylines_numba = merge_edges_into_polylines(nodes, edges)
    return list(polylines_numba)
