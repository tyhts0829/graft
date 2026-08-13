"""
Purpose:
    重複する無向XYZ線分を一つにし、必要なら非分岐chainへ決定的に再構成する。
Use when:
    重複edge除去、端点同一視、またはplot用segment chainの正規化を扱う場合。
Constraints:
    - 同向・逆向きを同一edgeとし、座標と向きは入力で最初に現れたものを維持する。
    - 正のtoleranceは成分ごとの量子化gridであり、Euclidean距離や部分overlapを意味しない。
    - 交点分割を行わず、出力頂点・line数を確保前にbudget検査する。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from grafix.core.operation_authoring import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import GeomTuple
from grafix.core.resource_budget import ensure_geometry_output

deduplicate_meta = {
    "tolerance": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=0.1,
        description="線分端点を同一視する XYZ 格子の間隔。0 は座標の完全一致。",
    ),
    "merge_chains": ParamMeta(
        kind="bool",
        description="次数 2 の端点を通る連続線分を一本のポリラインへ結合する。",
    ),
}


@dataclass(frozen=True, slots=True)
class _Edge:
    """最初に現れた向きを保持する無向 edge。"""

    start: int
    end: int


def _round_half_away_from_zero(value: float) -> int:
    """有限値を half away from zero で整数へ丸める。"""

    magnitude = math.floor(abs(value) + 0.5)
    return magnitude if value >= 0.0 else -magnitude


def _endpoint_key(
    point: np.ndarray,
    *,
    tolerance: float,
) -> tuple[object, object, object]:
    """端点を exact key または XYZ 格子 key へ変換する。"""

    if tolerance == 0.0:
        return float(point[0]), float(point[1]), float(point[2])

    components: list[object] = []
    for raw_value in point:
        value = float(raw_value)
        scaled = value / tolerance
        if math.isfinite(scaled):
            components.append(_round_half_away_from_zero(scaled))
        else:
            # float32 の異なる有限値同士の間隔より tolerance が小さい場合、
            # 格子 index は float64 の範囲を超える。そこでのみ完全一致 key
            # へ退避しても、同じ格子 cell に入る点の組は変わらない。
            components.append(("exact", value))
    return components[0], components[1], components[2]


def _collect_unique_edges(
    coords: np.ndarray,
    offsets: np.ndarray,
    *,
    tolerance: float,
) -> tuple[list[tuple[float, float, float]], list[_Edge]]:
    """入力を一度走査し、first-wins の node と無向 edge を集める。"""

    node_ids: dict[tuple[object, object, object], int] = {}
    node_coords: list[tuple[float, float, float]] = []
    edge_ids: dict[tuple[int, int], int] = {}
    edges: list[_Edge] = []

    def intern(point: np.ndarray) -> int:
        key = _endpoint_key(point, tolerance=tolerance)
        known = node_ids.get(key)
        if known is not None:
            return known

        node_id = len(node_coords)
        node_ids[key] = node_id
        node_coords.append(
            (float(point[0]), float(point[1]), float(point[2]))
        )
        return node_id

    for line_index in range(int(offsets.size) - 1):
        start = int(offsets[line_index])
        stop = int(offsets[line_index + 1])
        for point_index in range(start, stop - 1):
            node_a = intern(coords[point_index])
            node_b = intern(coords[point_index + 1])
            if node_a == node_b:
                continue

            edge_key = (
                (node_a, node_b) if node_a < node_b else (node_b, node_a)
            )
            if edge_key in edge_ids:
                continue

            edge_ids[edge_key] = len(edges)
            edges.append(_Edge(start=node_a, end=node_b))

    return node_coords, edges


def _build_adjacency(
    node_count: int,
    edges: list[_Edge],
) -> list[list[int]]:
    """edge id 昇順の adjacency list を作る。"""

    adjacency: list[list[int]] = [[] for _ in range(node_count)]
    for edge_id, edge in enumerate(edges):
        adjacency[edge.start].append(edge_id)
        adjacency[edge.end].append(edge_id)
    return adjacency


def _other_endpoint(edge: _Edge, node_id: int) -> int:
    """edge の node_id ではない側を返す。"""

    return edge.end if edge.start == node_id else edge.start


def _walk_open_chain(
    first_edge_id: int,
    start_node: int,
    *,
    edges: list[_Edge],
    adjacency: list[list[int]],
    visited: list[bool],
) -> list[int]:
    """非 degree-2 node から maximal non-branching chain を辿る。"""

    chain = [start_node]
    current_node = start_node
    edge_id = first_edge_id

    while True:
        visited[edge_id] = True
        next_node = _other_endpoint(edges[edge_id], current_node)
        chain.append(next_node)
        if len(adjacency[next_node]) != 2:
            break

        next_edge_id = -1
        for candidate_id in adjacency[next_node]:
            if not visited[candidate_id]:
                next_edge_id = candidate_id
                break
        if next_edge_id < 0:
            break

        current_node = next_node
        edge_id = next_edge_id

    return chain


def _walk_cycle(
    first_edge_id: int,
    *,
    edges: list[_Edge],
    adjacency: list[list[int]],
    visited: list[bool],
) -> list[int]:
    """最小 edge id の元向きを使って degree-2 cycle を閉じる。"""

    first_edge = edges[first_edge_id]
    seam = first_edge.start
    cycle = [seam]
    current_node = seam
    edge_id = first_edge_id

    while True:
        visited[edge_id] = True
        next_node = _other_endpoint(edges[edge_id], current_node)
        cycle.append(next_node)
        if next_node == seam:
            break

        next_edge_id = -1
        for candidate_id in adjacency[next_node]:
            if not visited[candidate_id]:
                next_edge_id = candidate_id
                break
        if next_edge_id < 0:
            # simple undirected graph の degree-2 component では到達しない。
            break

        current_node = next_node
        edge_id = next_edge_id

    return cycle


def _merge_edges_into_chains(
    node_count: int,
    edges: list[_Edge],
) -> list[list[int]]:
    """unique edge を決定的な maximal non-branching chain へまとめる。"""

    adjacency = _build_adjacency(node_count, edges)
    visited = [False] * len(edges)
    chains: list[list[int]] = []

    # まず branch / endpoint に接する edge を first-seen 順に処理する。
    for edge_id, edge in enumerate(edges):
        if visited[edge_id]:
            continue
        start_degree = len(adjacency[edge.start])
        end_degree = len(adjacency[edge.end])
        if start_degree == 2 and end_degree == 2:
            continue

        if start_degree != 2:
            start_node = edge.start
        else:
            start_node = edge.end
        chains.append(
            _walk_open_chain(
                edge_id,
                start_node,
                edges=edges,
                adjacency=adjacency,
                visited=visited,
            )
        )

    # 残る component は全 node が degree 2 の cycle。
    for edge_id in range(len(edges)):
        if visited[edge_id]:
            continue
        chains.append(
            _walk_cycle(
                edge_id,
                edges=edges,
                adjacency=adjacency,
                visited=visited,
            )
        )

    return chains


def _pack_chains(
    node_coords: list[tuple[float, float, float]],
    chains: list[list[int]],
    *,
    output_vertices: int,
) -> GeomTuple:
    """中間の line 配列を作らず、chain を packed geometry へ直接書く。"""

    coords = np.empty((int(output_vertices), 3), dtype=np.float32)
    offsets = np.empty((len(chains) + 1,), dtype=np.int32)
    offsets[0] = 0
    cursor = 0
    for line_index, chain in enumerate(chains):
        for node_id in chain:
            coords[cursor] = node_coords[node_id]
            cursor += 1
        offsets[line_index + 1] = np.int32(cursor)
    return coords, offsets


@effect(meta=deduplicate_meta)
def deduplicate(
    g: GeomTuple,
    *,
    tolerance: float = 1e-4,
    merge_chains: bool = True,
) -> GeomTuple:
    """同一の無向線分を一つにまとめる。

    Parameters
    ----------
    g : tuple[np.ndarray, np.ndarray]
        入力実体ジオメトリ（coords, offsets）。
    tolerance : float, default 1e-4
        端点を同一視する XYZ 格子の間隔。0 は有限な float32 座標の完全一致。
        正値では各成分を half away from zero で格子 index へ量子化するため、
        ユークリッド距離がこの値以下であることを意味しない。0 以上が必要。
    merge_chains : bool, default True
        True なら degree 2 の node だけを通って連続する unique segment を
        maximal polyline へ結合する。False なら各 segment を 2 点の線として返す。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        重複除去後の実体ジオメトリ（coords, offsets）。

    Raises
    ------
    ValueError
        `tolerance` が負の場合。

    Notes
    -----
    - XYZ すべてを比較し、同向・逆向きの重複を同一視する。
    - node の座標と edge の向きは入力中で最初に現れたものを採用する。
    - 0 / 1 点 line と zero-length segment は出力しない。
    - 部分 overlap、交点分割、分割数が異なる線分列の同一視は行わない。
    """

    coords, offsets = g
    tol = tolerance
    if tol < 0.0:
        raise ValueError("deduplicate の tolerance は 0 以上である必要がある")
    node_coords, edges = _collect_unique_edges(
        coords,
        offsets,
        tolerance=tol,
    )
    if merge_chains:
        chains = _merge_edges_into_chains(len(node_coords), edges)
    else:
        chains = [[edge.start, edge.end] for edge in edges]

    output_vertices = sum(len(chain) for chain in chains)
    ensure_geometry_output(
        "deduplicate",
        vertices=output_vertices,
        lines=len(chains),
        hint="入力 geometry の線分数を減らしてください",
    )

    return _pack_chains(
        node_coords,
        chains,
        output_vertices=output_vertices,
    )
