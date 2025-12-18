from __future__ import annotations

"""
概要（アルゴリズム要約）
- 入力曲線を XY 平面へ射影
- 乱択的に候補線を生成し（距離最大などの基準）、交点/連結を評価
- 弾性緩和（反復）でウェブ構造を安定化
- 元の 3D 姿勢に戻して合成
"""

import math

import numpy as np
from numba import njit, types  # type: ignore[attr-defined]
from numba.typed import List  # type: ignore[attr-defined]

from engine.core.geometry import Geometry
from util.geom3d_ops import transform_back, transform_to_xy_plane

from .registry import effect

PARAM_META = {
    "num_candidate_lines": {"type": "integer", "min": 0, "max": 500, "step": 1},
    "relaxation_iterations": {"type": "integer", "min": 0, "max": 50, "step": 1},
    "step": {"type": "number", "min": 0.0, "max": 0.5},
}


@effect()
def weave(
    g: Geometry,
    *,
    num_candidate_lines: int = 100,
    relaxation_iterations: int = 15,
    step: float = 0.125,
) -> Geometry:
    """形状にウェブ状のストリング構造を追加。

    Parameters
    ----------
    g : Geometry
        入力ジオメトリ。
    num_candidate_lines : int, default 100
        候補線本数（0–500 にクランプ）。
    relaxation_iterations : int, default 15
        弾性緩和の反復回数（0–50 にクランプ）。
    step : float, default 0.125
        1 ステップの移動係数（0.0–0.5）。
    """
    MAX_NUM_CANDIDATE_LINES = 500
    MAX_RELAXATION_ITERATIONS = 50
    MAX_STEP = 0.5

    coords, offsets = g.as_arrays(copy=False)
    result_lines: list[np.ndarray] = []
    num_lines = int(num_candidate_lines)
    num_lines = max(0, min(MAX_NUM_CANDIDATE_LINES, num_lines))
    iterations = int(relaxation_iterations)
    iterations = max(0, min(MAX_RELAXATION_ITERATIONS, iterations))
    step_size = float(step)
    if step_size < 0.0:
        step_size = 0.0
    if step_size > MAX_STEP:
        step_size = MAX_STEP

    # STEP 1: 各ポリラインに対してウェブ構造を生成
    for i in range(len(offsets) - 1):
        vertices = coords[offsets[i] : offsets[i + 1]]
        if len(vertices) < 3:
            result_lines.append(vertices)
            continue
        result_lines.extend(
            _webify_single_polygon(
                vertices,
                num_candidate_lines=num_lines,
                relaxation_iterations=iterations,
                step=step_size,
            )
        )

    if not result_lines:
        return Geometry(coords.copy(), offsets.copy())
    return Geometry.from_lines(result_lines)


weave.__param_meta__ = PARAM_META


def _webify_single_polygon(
    vertices: np.ndarray,
    *,
    num_candidate_lines: int,
    relaxation_iterations: int,
    step: float,
) -> list[np.ndarray]:
    """単一の閉曲線からウェブ状のストリング群を生成して 3D に戻す。

    - 2D へ射影 → ウェブ生成 → 3D へ戻す、の高レベル手順をひとまとめにする。
    - 数値計算の詳細は numba 最適化済みのローレベル関数群に委譲する。
    """
    transformed, R, z = transform_to_xy_plane(vertices)
    polylines_xy = create_web(
        transformed,
        num_candidate_lines=num_candidate_lines,
        relaxation_iterations=relaxation_iterations,
        step=step,
    )
    out: list[np.ndarray] = []
    for poly in polylines_xy:
        out.append(transform_back(poly, R, z))
    return out


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
    """
    指定された cl と seed に基づいて候補線を生成する。
    """
    N = closed_curve.shape[0]
    # 擬似乱数生成（GLSL的テクニック）
    seed1 = cl * 12.9898 + seed + 78.233
    r1 = fract(math.sin(seed1) * 43758.5453)
    seed2 = cl * 93.9898 + seed + 12.345
    r2 = fract(math.sin(seed2) * 43758.5453)
    seed3 = cl * 45.1234 + seed + 98.765
    r3 = fract(math.sin(seed3) * 43758.5453)
    seed4 = cl * 67.8901 + seed + 23.456
    r4 = fract(math.sin(seed4) * 43758.5453)

    # A点の生成：r1でインデックス、r2で補間
    idx1 = int(r1 * N)
    next_idx1 = (idx1 + 1) % N
    A_x = closed_curve[idx1, 0] * (1 - r2) + closed_curve[next_idx1, 0] * r2
    A_y = closed_curve[idx1, 1] * (1 - r2) + closed_curve[next_idx1, 1] * r2

    # B点の生成：r3でインデックス、r4で補間
    idx2 = int(r3 * N)
    next_idx2 = (idx2 + 1) % N
    B_x = closed_curve[idx2, 0] * (1 - r4) + closed_curve[next_idx2, 0] * r4
    B_y = closed_curve[idx2, 1] * (1 - r4) + closed_curve[next_idx2, 1] * r4

    return A_x, A_y, B_x, B_y


@njit(fastmath=True, cache=True)
def generate_best_candidate_line_from_curve_nb(closed_curve, cl, base_seed, n_attempts):
    """
    同じ cl に対して、base_seed から始め n_attempts 回シードを変化させて候補線を生成し、
    2点間の距離が最大となる候補線を選択する。
    """
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
    for it in range(iterations):
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
    """
    辞書ではなく配列を用いて隣接リスト表現を構築する。
    返り値:
        - adjacency: adjacency[i] がノード i の隣接ノードを格納する 2D 配列（空きは -1）。
        - degrees: 各ノードの次数配列。
    """
    # まず各ノードの次数を数える
    degrees = np.zeros(num_nodes, dtype=np.int32)
    for i in range(edges.shape[0]):
        a, b = edges[i, 0], edges[i, 1]
        degrees[a] += 1
        degrees[b] += 1

    # 隣接配列を確保（最大次数は適度と仮定、例: 10）
    max_degree = 10
    adjacency = np.full((num_nodes, max_degree), -1, dtype=np.int32)
    adj_counts = np.zeros(num_nodes, dtype=np.int32)

    # 隣接配列を埋める
    for i in range(edges.shape[0]):
        a, b = edges[i, 0], edges[i, 1]
        adjacency[a, adj_counts[a]] = b
        adj_counts[a] += 1
        adjacency[b, adj_counts[b]] = a
        adj_counts[b] += 1

    return adjacency, degrees


@njit(fastmath=True, cache=True)
def edge_key_hash(a, b, num_nodes):
    """無向辺（順序なし組）の一意ハッシュを作成する。"""
    if a > b:
        a, b = b, a
    return a * num_nodes + b


@njit(fastmath=True, cache=True)
def mark_edge_visited(visited_edges, a, b, num_nodes):
    """辺を訪問済みにマークする。"""
    hash_val = edge_key_hash(a, b, num_nodes)
    visited_edges[hash_val] = True


@njit(fastmath=True, cache=True)
def is_edge_visited(visited_edges, a, b, num_nodes):
    """辺が訪問済みかを判定する。"""
    hash_val = edge_key_hash(a, b, num_nodes)
    return visited_edges[hash_val]


@njit(fastmath=True, cache=True)
def trace_chain(
    start, first_neighbor, adjacency, degrees, visited_edges, num_nodes, max_chain_length=10000
):
    """
    開始ノードから first_neighbor を経由して鎖を辿る。
    返り値は鎖を表す配列とその長さ。
    """
    chain = np.empty(max_chain_length, dtype=np.int32)
    chain[0] = start
    chain[1] = first_neighbor
    chain_length = 2

    mark_edge_visited(visited_edges, start, first_neighbor, num_nodes)

    prev = start
    current = first_neighbor

    while degrees[current] == 2 and chain_length < max_chain_length:
        # 直前ノード以外の次ノードを探す
        next_node = -1
        for i in range(adjacency.shape[1]):
            neighbor = adjacency[current, i]
            if neighbor == -1:
                break
            if neighbor != prev:
                next_node = neighbor
                break

        if next_node == -1:
            break

        if is_edge_visited(visited_edges, current, next_node, num_nodes):
            break

        chain[chain_length] = next_node
        chain_length += 1
        mark_edge_visited(visited_edges, current, next_node, num_nodes)

        prev = current
        current = next_node

    return chain[:chain_length], chain_length


@njit(fastmath=True, cache=True)
def trace_cycle(start, adjacency, visited_edges, num_nodes, max_cycle_length=10000):
    """
    指定ノードからサイクルを追跡する。
    返り値はサイクル配列とその長さ。
    """
    cycle = np.empty(max_cycle_length, dtype=np.int32)
    cycle[0] = start
    cycle_length = 1

    # 最初の未訪問の隣接ノードを選ぶ
    first_neighbor = -1
    for i in range(adjacency.shape[1]):
        neighbor = adjacency[start, i]
        if neighbor == -1:
            break
        if not is_edge_visited(visited_edges, start, neighbor, num_nodes):
            first_neighbor = neighbor
            break

    if first_neighbor == -1:
        return cycle[:0], 0

    cycle[1] = first_neighbor
    cycle_length = 2
    mark_edge_visited(visited_edges, start, first_neighbor, num_nodes)

    prev = start
    current = first_neighbor

    while cycle_length < max_cycle_length:
        # 次の未訪問の隣接ノードを探す
        next_node = -1
        for i in range(adjacency.shape[1]):
            neighbor = adjacency[current, i]
            if neighbor == -1:
                break
            if neighbor != prev and not is_edge_visited(
                visited_edges, current, neighbor, num_nodes
            ):
                next_node = neighbor
                break

        if next_node == -1:
            # Try to close the cycle
            for i in range(adjacency.shape[1]):
                neighbor = adjacency[current, i]
                if neighbor == -1:
                    break
                if neighbor == start:
                    mark_edge_visited(visited_edges, current, start, num_nodes)
                    return cycle[:cycle_length], cycle_length
            break

        if next_node == start:
            # Cycle closed
            mark_edge_visited(visited_edges, current, start, num_nodes)
            return cycle[:cycle_length], cycle_length

        cycle[cycle_length] = next_node
        cycle_length += 1
        mark_edge_visited(visited_edges, current, next_node, num_nodes)

        prev = current
        current = next_node

    return cycle[:cycle_length], cycle_length


@njit(
    types.ListType(types.float64[:, :])(types.float64[:, :], types.int64[:, :]),
    fastmath=True,
    cache=True,
)
def merge_edges_into_polylines(nodes, edges):
    """
    Numba-compatible version of merge_edges_into_polylines.
    ノード集合とエッジリストから隣接エッジを連結して、
    ポリライン（np.array(shape=(N,3))）のリストとして返す。

    引数:
        nodes: ノード位置配列 (N, 3)。
        edges: エッジ接続配列 (M, 2)。

    返り値:
        各ポリラインを shape=(K, 3) の numpy 配列とするリスト。
    """
    num_nodes = nodes.shape[0]
    adjacency, degrees = build_adjacency_arrays(num_nodes, edges)

    # 訪問済み辺の管理（ハッシュ表を利用）
    max_edges = num_nodes * num_nodes  # Upper bound for hash table
    visited_edges = np.zeros(max_edges, dtype=np.bool_)

    polylines = List.empty_list(types.float64[:, :])

    # 端点・分岐点から鎖を処理
    for i in range(num_nodes):
        if degrees[i] != 2:  # Endpoint or branch
            # Check each neighbor
            for j in range(adjacency.shape[1]):
                neighbor = adjacency[i, j]
                if neighbor == -1:
                    break

                if not is_edge_visited(visited_edges, i, neighbor, num_nodes):
                    chain, chain_length = trace_chain(
                        i, neighbor, adjacency, degrees, visited_edges, num_nodes
                    )

                    if chain_length >= 2:
                        # 鎖をポリラインへ変換
                        polyline = np.empty((chain_length, 3), dtype=np.float64)
                        for k in range(chain_length):
                            node_idx = chain[k]
                            polyline[k, 0] = nodes[node_idx, 0]
                            polyline[k, 1] = nodes[node_idx, 1]
                            polyline[k, 2] = 0.0  # z=0
                        polylines.append(polyline)

    # 未処理のサイクルを処理
    for i in range(num_nodes):
        if degrees[i] == 2:  # Only process nodes that could be in cycles
            # このノードに未訪問辺があるか確認
            has_unvisited = False
            for j in range(adjacency.shape[1]):
                neighbor = adjacency[i, j]
                if neighbor == -1:
                    break
                if not is_edge_visited(visited_edges, i, neighbor, num_nodes):
                    has_unvisited = True
                    break

            if has_unvisited:
                cycle, cycle_length = trace_cycle(i, adjacency, visited_edges, num_nodes)

                if cycle_length >= 2:
                    # サイクルをポリラインへ変換
                    polyline = np.empty((cycle_length, 3), dtype=np.float64)
                    for k in range(cycle_length):
                        node_idx = cycle[k]
                        polyline[k, 0] = nodes[node_idx, 0]
                        polyline[k, 1] = nodes[node_idx, 1]
                        polyline[k, 2] = 0.0  # z=0
                    polylines.append(polyline)

    return polylines


@njit(fastmath=True, cache=True)
def create_web_nb(closed_curve, num_candidate_lines, relaxation_iterations, step):
    # 初期ノード（閉曲線上の点）を配列として確保
    n = closed_curve.shape[0]
    max_nodes = n + 2 * num_candidate_lines  # 各候補線で最大2点追加
    nodes = np.zeros((max_nodes, 3), dtype=np.float64)
    for i in range(n):
        nodes[i, 0] = closed_curve[i, 0]
        nodes[i, 1] = closed_curve[i, 1]
        nodes[i, 2] = 0.0
    current_n = n

    # エッジ配列の初期化（各エッジは [始点, 終点]）
    max_edges = n + 5 * num_candidate_lines  # 分割ごとにエッジ数が増加
    edges = np.zeros((max_edges, 2), dtype=np.int64)
    valid_edges = np.zeros(max_edges, dtype=np.bool_)
    for i in range(n):
        edges[i, 0] = i
        edges[i, 1] = (i + 1) % n
        valid_edges[i] = True
    current_m = n

    # 候補線によるエッジ分割ループ
    for cl in range(num_candidate_lines):
        A_x, A_y, B_x, B_y = generate_best_candidate_line_from_curve_nb(
            closed_curve, cl, base_seed=0, n_attempts=2
        )
        # 各候補線との交点を格納（最大20個まで）
        max_int = 20
        t_vals = np.empty(max_int, dtype=np.float64)
        edge_indices = np.empty(max_int, dtype=np.int64)
        int_x = np.empty(max_int, dtype=np.float64)
        int_y = np.empty(max_int, dtype=np.float64)
        count = 0
        # 現在のすべてのエッジについて交点判定
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
            if hit:
                if count < max_int:
                    t_vals[count] = t_val
                    edge_indices[count] = e
                    int_x[count] = ix
                    int_y[count] = iy
                    count += 1
        # 交点が2点以上あれば、最もtが小さい2点を採用
        if count >= 2:
            # 単純な最小値探索
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
                # 1つ目の交点でエッジ分割
                e1 = edge_indices[idx1]
                i1 = edges[e1, 0]
                j1 = edges[e1, 1]
                valid_edges[e1] = False  # 元のエッジを無効化
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

                # 2つ目の交点でエッジ分割
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

                # 新たに新ノード同士を接続するエッジを追加
                edges[current_m, 0] = new_node1
                edges[current_m, 1] = new_node2
                valid_edges[current_m] = True
                current_m += 1

    # 最終的に有効なエッジのみ抽出
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

    # 固定ノード：閉曲線上の初期点は固定（以降の弾性平衡で動かさない）
    fixed = np.zeros(current_n, dtype=np.bool_)
    for i in range(n):
        fixed[i] = True

    # 弾性平衡シミュレーション（positionsは (n,2) 部分のみ更新）
    positions = nodes[:current_n, 0:2].copy()
    positions = elastic_relaxation_nb(
        positions, valid_edges_arr, fixed, relaxation_iterations, step
    )
    nodes[:current_n, 0:2] = positions

    return nodes[:current_n], valid_edges_arr


def create_web(closed_curve, num_candidate_lines=10, relaxation_iterations=20, step=0.1):
    """
    closed_curve: np.array(shape=(N,3)) （各点のz=0と仮定）
    """
    nodes, edges = create_web_nb(closed_curve, num_candidate_lines, relaxation_iterations, step)
    # ノードとエッジからポリライン（各頂点が[x,y,0]の形）を生成する
    polylines_numba = merge_edges_into_polylines(nodes, edges)
    # Numba の型付きリストを通常の Python リストへ変換
    polylines = list(polylines_numba)
    return polylines
