"""
Purpose:
    polyline集合を共有nodeの無向graphとして緩和し、線networkの形を整える。
Use when:
    junctionを共有するnetwork smoothing、固定node、またはiteration制限を変更する場合。
Constraints:
    - exactに同じ座標を同一nodeとし、polyline topologyとoffset境界を変更しない。
    - endpoint・junctionと各連結成分の座標extremaを固定してnetworkの骨格を保つ。
    - iterationとstepのclampを診断し、edgeを作れない入力は変更しない。
"""

from __future__ import annotations

import numpy as np
from numba import njit  # type: ignore[attr-defined, import-untyped]

from grafix.core.operation_authoring import effect
from grafix.core.operation_diagnostics import emit_operation_diagnostic
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import GeomTuple

relax_meta = {
    "relaxation_iterations": ParamMeta(
        kind="int",
        ui_min=0,
        ui_max=50,
        description="点列のばらつきをならす弾性緩和の反復回数。",
    ),
    "step": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=0.5,
        description="各緩和ステップで頂点を移動させる係数。",
    ),
}

MAX_RELAXATION_ITERATIONS = 50
MAX_STEP = 0.5
_PYTHON_LIST_SCRATCH_BUDGET_BYTES = 8 * 1024 * 1024
# CPython の scalar object、list slot、allocator の端数を合わせた保守的な見積もり。
_PYTHON_SCALAR_LIST_ITEM_BYTES = 40
# ``ndarray.tolist()`` が作る外側 slot、2 要素 list、Python int 2 個の見積もり。
_PYTHON_EDGE_LIST_ROW_BYTES = 160
_PYTHON_SCALAR_LIST_ITEM_LIMIT = (
    _PYTHON_LIST_SCRATCH_BUDGET_BYTES // _PYTHON_SCALAR_LIST_ITEM_BYTES
)
_PYTHON_EDGE_LIST_ROW_LIMIT = (
    _PYTHON_LIST_SCRATCH_BUDGET_BYTES // _PYTHON_EDGE_LIST_ROW_BYTES
)
_PYTHON_VISITED_LIST_NODE_LIMIT = (
    (_PYTHON_LIST_SCRATCH_BUDGET_BYTES - 64) // 8
)


def _build_nodes_array_scan(
    coords: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """baseline と同じ ndarray scalar 走査で node 対応を作る。"""

    index_by_xyz: dict[tuple[float, float, float], int] = {}
    vertex_to_node = np.empty((coords.shape[0],), dtype=np.int64)
    nodes: list[tuple[float, float, float]] = []

    for i in range(int(coords.shape[0])):
        key = (
            float(coords[i, 0]),
            float(coords[i, 1]),
            float(coords[i, 2]),
        )
        idx = index_by_xyz.get(key)
        if idx is None:
            idx = len(nodes)
            index_by_xyz[key] = idx
            nodes.append(key)
        vertex_to_node[i] = int(idx)

    nodes_arr = np.asarray(nodes, dtype=np.float64)
    return nodes_arr, vertex_to_node


def _build_nodes_list_scan(
    coords: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """小入力を Python scalar list へ一括変換して node 対応を作る。"""

    index_by_xyz: dict[tuple[float, float, float], int] = {}
    vertex_to_node = np.empty((coords.shape[0],), dtype=np.int64)
    nodes: list[tuple[float, float, float]] = []

    coordinate_rows = zip(
        coords[:, 0].tolist(),
        coords[:, 1].tolist(),
        coords[:, 2].tolist(),
    )
    for i, key in enumerate(coordinate_rows):
        idx = index_by_xyz.get(key)
        if idx is None:
            idx = len(nodes)
            index_by_xyz[key] = idx
            nodes.append(key)
        vertex_to_node[i] = int(idx)

    nodes_arr = np.asarray(nodes, dtype=np.float64)
    return nodes_arr, vertex_to_node


def _build_nodes(coords: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """座標配列からユニークノードと頂点→ノード対応を作る（完全一致のみ）。"""

    scalar_items = 3 * int(coords.shape[0])
    if scalar_items <= _PYTHON_SCALAR_LIST_ITEM_LIMIT:
        return _build_nodes_list_scan(coords)
    return _build_nodes_array_scan(coords)


def _build_edges_array_scan(
    offsets: np.ndarray,
    vertex_to_node: np.ndarray,
) -> np.ndarray:
    """baseline と同じ ndarray scalar 走査で edge 集合を作る。"""

    edges: set[tuple[int, int]] = set()
    for line_index in range(int(offsets.size) - 1):
        start = int(offsets[line_index])
        stop = int(offsets[line_index + 1])
        for i in range(start, stop - 1):
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


def _build_edges_list_scan(
    offsets: np.ndarray,
    vertex_to_node: np.ndarray,
) -> np.ndarray:
    """小入力を Python int list へ一括変換して edge 集合を作る。"""

    edges: set[tuple[int, int]] = set()
    offset_values = offsets.tolist()
    node_values = vertex_to_node.tolist()
    for line_index in range(len(offset_values) - 1):
        start = offset_values[line_index]
        stop = offset_values[line_index + 1]
        for i in range(start, stop - 1):
            a = node_values[i]
            b = node_values[i + 1]
            if a == b:
                continue
            if a < b:
                edges.add((a, b))
            else:
                edges.add((b, a))

    if not edges:
        return np.zeros((0, 2), dtype=np.int64)
    return np.asarray(sorted(edges), dtype=np.int64)


def _build_edges(offsets: np.ndarray, vertex_to_node: np.ndarray) -> np.ndarray:
    scalar_items = int(offsets.size) + int(vertex_to_node.size)
    if scalar_items <= _PYTHON_SCALAR_LIST_ITEM_LIMIT:
        return _build_edges_list_scan(offsets, vertex_to_node)
    return _build_edges_array_scan(offsets, vertex_to_node)


def _build_adjacency_array_scan(
    num_nodes: int,
    edges: np.ndarray,
) -> list[list[int]]:
    """baseline と同じ ndarray scalar 走査で隣接 list を作る。"""

    adjacency: list[list[int]] = [[] for _ in range(int(num_nodes))]
    for edge_index in range(int(edges.shape[0])):
        a = int(edges[edge_index, 0])
        b = int(edges[edge_index, 1])
        adjacency[a].append(b)
        adjacency[b].append(a)
    return adjacency


def _build_adjacency_list_scan(
    num_nodes: int,
    edges: np.ndarray,
) -> list[list[int]]:
    """小入力の edge を Python nested list 化して隣接 list を作る。"""

    adjacency: list[list[int]] = [[] for _ in range(int(num_nodes))]
    for a, b in edges.tolist():
        adjacency[a].append(b)
        adjacency[b].append(a)
    return adjacency


def _build_adjacency(num_nodes: int, edges: np.ndarray) -> list[list[int]]:
    if int(edges.shape[0]) <= _PYTHON_EDGE_LIST_ROW_LIMIT:
        return _build_adjacency_list_scan(num_nodes, edges)
    return _build_adjacency_array_scan(num_nodes, edges)


def _build_visited(num_nodes: int) -> list[bool] | np.ndarray:
    """小入力だけ Python bool list、大入力は baseline の ndarray を使う。"""

    if num_nodes <= _PYTHON_VISITED_LIST_NODE_LIMIT:
        return [False] * num_nodes
    return np.zeros((num_nodes,), dtype=np.bool_)


def _compute_fixed(nodes: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """固定点マスクを作る（次数!=2 + 連結成分の min/max）。"""
    num_nodes = int(nodes.shape[0])
    degrees = np.bincount(edges.reshape(-1), minlength=num_nodes)
    fixed = degrees != 2

    adjacency = _build_adjacency(num_nodes, edges)
    visited = _build_visited(num_nodes)
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
def _elastic_relaxation_nb(
    positions: np.ndarray,
    edges: np.ndarray,
    fixed: np.ndarray,
    iterations: int,
    step: float,
) -> np.ndarray:
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
    g: GeomTuple,
    *,
    relaxation_iterations: int = 15,
    step: float = 0.125,
) -> GeomTuple:
    """線分ネットワークをグラフとして弾性緩和する。

    入力ポリライン群を 1 つの無向グラフとして扱い、同一点は共有ノードとして束ねる。
    端点/分岐（次数!=2）と、各連結成分の座標 min/max を固定し、残りの点を平滑化する。

    Parameters
    ----------
    g : tuple[np.ndarray, np.ndarray]
        変形対象の実体ジオメトリ（coords, offsets）。
    relaxation_iterations : int, default 15
        反復回数。50 を超える値はクランプする。0 は no-op。
    step : float, default 0.125
        1 ステップの移動係数。0.5 を超える値はクランプする。0 は no-op。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        緩和後の実体ジオメトリ（coords, offsets）。

    Raises
    ------
    ValueError
        `relaxation_iterations` または `step` が負の場合。
    """
    if relaxation_iterations < 0:
        raise ValueError("relax の relaxation_iterations は 0 以上である必要がある")
    if step < 0.0:
        raise ValueError("relax の step は 0 以上である必要がある")

    coords, offsets = g
    if coords.shape[0] == 0:
        return coords, offsets

    requested_iterations = relaxation_iterations
    iterations = requested_iterations
    iterations = min(MAX_RELAXATION_ITERATIONS, iterations)
    if iterations != requested_iterations:
        emit_operation_diagnostic(
            op="relax.relaxation_iterations",
            original_value=requested_iterations,
            effective_value=iterations,
            reason="relaxation iterations was clamped to the supported range",
        )

    requested_step = step
    step_size = requested_step
    if step_size > MAX_STEP:
        step_size = MAX_STEP
    if step_size != requested_step:
        emit_operation_diagnostic(
            op="relax.step",
            original_value=requested_step,
            effective_value=step_size,
            reason="relaxation step was clamped to the supported range",
        )

    if iterations == 0 or step_size == 0.0:
        return coords, offsets

    nodes, vertex_to_node = _build_nodes(coords)
    edges = _build_edges(offsets, vertex_to_node)
    if edges.shape[0] == 0 or nodes.shape[0] == 0:
        emit_operation_diagnostic(
            op="relax.input",
            original_value="no_graph_edges",
            effective_value="input_unchanged",
            reason="relax requires a graph with at least one edge",
        )
        return coords, offsets

    fixed = _compute_fixed(nodes, edges)
    positions = nodes.copy()
    positions = _elastic_relaxation_nb(positions, edges, fixed, iterations, step_size)

    out_coords = positions[vertex_to_node].astype(np.float32, copy=False)
    return out_coords, offsets


__all__ = ["relax", "relax_meta"]
