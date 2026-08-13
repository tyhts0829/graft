"""
Purpose:
    scalar gridの等値線を共有Marching Squares規約で抽出し、連続pathへ決定的に縫合する。
Use when:
    isocontour、SDF、simulationなどからopen chainまたはclosed loopを生成する場合。
Constraints:
    - grid edge identityに基づく安定した出力順を維持する。
    - open chainをclosed loopより先に返し、closed loopは始点と終点を一致させる。
    - field、mask、sample fieldのgrid shapeとorigin/pitch規約を混同しない。
"""

from __future__ import annotations

import math

import numpy as np
from numba import njit  # type: ignore[attr-defined, import-untyped]


@njit(cache=True)
def _build_edge_neighbors_nb(
    node_count: int, edges_a: np.ndarray, edges_b: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    neighbors = np.full((node_count, 2), -1, dtype=np.int64)
    degree = np.zeros((node_count,), dtype=np.int32)
    for edge_index in range(int(edges_a.shape[0])):
        a = int(edges_a[edge_index])
        b = int(edges_b[edge_index])
        degree_a = int(degree[a])
        if degree_a < 2:
            neighbors[a, degree_a] = b
        degree[a] = degree_a + 1
        degree_b = int(degree[b])
        if degree_b < 2:
            neighbors[b, degree_b] = a
        degree[b] = degree_b + 1
    return neighbors, degree


@njit(cache=True)
def _collect_edge_cycles_nb(
    neighbors: np.ndarray, degree: np.ndarray
) -> tuple[np.ndarray, np.ndarray, int]:
    node_count = int(neighbors.shape[0])
    visited = np.zeros((node_count,), dtype=np.uint8)
    starts = np.empty((node_count,), dtype=np.int64)
    lengths = np.empty((node_count,), dtype=np.int32)
    cycle_count = 0
    for start in range(node_count):
        if int(degree[start]) != 2 or int(visited[start]) != 0:
            continue
        if int(neighbors[start, 0]) < 0 or int(neighbors[start, 1]) < 0:
            visited[start] = 1
            continue
        if int(neighbors[start, 0]) == int(neighbors[start, 1]):
            visited[start] = 1
            continue

        previous = -1
        current = start
        length = 0
        valid = True
        while True:
            if int(visited[current]) != 0:
                if current == start:
                    break
                valid = False
                break
            visited[current] = 1
            length += 1
            first = int(neighbors[current, 0])
            second = int(neighbors[current, 1])
            following = first if first != previous else second
            if following < 0 or int(degree[following]) != 2 or following == previous:
                valid = False
                break
            previous = current
            current = following
            if current == start:
                break
        if valid and length >= 3:
            starts[cycle_count] = start
            lengths[cycle_count] = length
            cycle_count += 1
    return starts, lengths, cycle_count


@njit(cache=True)
def _pack_edge_cycles_nb(
    neighbors: np.ndarray,
    starts: np.ndarray,
    lengths: np.ndarray,
    cycle_count: int,
) -> tuple[np.ndarray, np.ndarray]:
    offsets = np.empty((cycle_count + 1,), dtype=np.int32)
    offsets[0] = 0
    total = 0
    for cycle_index in range(cycle_count):
        total += int(lengths[cycle_index]) + 1
        offsets[cycle_index + 1] = total
    indices = np.empty((total,), dtype=np.int64)
    cursor = 0
    for cycle_index in range(cycle_count):
        start = int(starts[cycle_index])
        previous = -1
        current = start
        for _ in range(int(lengths[cycle_index])):
            indices[cursor] = current
            cursor += 1
            first = int(neighbors[current, 0])
            second = int(neighbors[current, 1])
            following = first if first != previous else second
            previous = current
            current = following
        indices[cursor] = start
        cursor += 1
    return indices, offsets


@njit(cache=True)
def _compact_grid_edge_ids_nb(
    edges_a: np.ndarray, edges_b: np.ndarray, total_edge_count: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    segment_count = int(edges_a.shape[0])
    used = np.zeros((total_edge_count,), dtype=np.uint8)
    for segment_index in range(segment_count):
        used[int(edges_a[segment_index])] = 1
        used[int(edges_b[segment_index])] = 1
    node_count = 0
    for edge_id in range(total_edge_count):
        node_count += int(used[edge_id])

    node_edge_ids = np.empty((node_count,), dtype=np.int32)
    edge_to_node = np.full((total_edge_count,), -1, dtype=np.int32)
    cursor = 0
    for edge_id in range(total_edge_count):
        if int(used[edge_id]) != 0:
            edge_to_node[edge_id] = cursor
            node_edge_ids[cursor] = edge_id
            cursor += 1
    compact_a = np.empty((segment_count,), dtype=np.int32)
    compact_b = np.empty((segment_count,), dtype=np.int32)
    for segment_index in range(segment_count):
        compact_a[segment_index] = edge_to_node[int(edges_a[segment_index])]
        compact_b[segment_index] = edge_to_node[int(edges_b[segment_index])]
    return node_edge_ids, compact_a, compact_b


@njit(cache=True)
def _grid_edge_nodes_to_xy_nb(
    node_edge_ids: np.ndarray,
    horizontal_t: np.ndarray,
    vertical_t: np.ndarray,
    origin_x: float,
    origin_y: float,
    pitch: float,
    nx: int,
    ny: int,
) -> np.ndarray:
    horizontal_count = ny * (nx - 1)
    output = np.empty((int(node_edge_ids.shape[0]), 2), dtype=np.float64)
    for node_index in range(int(node_edge_ids.shape[0])):
        edge_id = int(node_edge_ids[node_index])
        if edge_id < horizontal_count:
            row = edge_id // (nx - 1)
            column = edge_id - row * (nx - 1)
            output[node_index, 0] = origin_x + pitch * (
                float(column) + float(horizontal_t[edge_id])
            )
            output[node_index, 1] = origin_y + pitch * float(row)
        else:
            local_id = edge_id - horizontal_count
            row = local_id // nx
            column = local_id - row * nx
            output[node_index, 0] = origin_x + pitch * float(column)
            output[node_index, 1] = origin_y + pitch * (
                float(row) + float(vertical_t[local_id])
            )
    return output


@njit(cache=True)
def _grid_edge_nodes_to_xy_rect_nb(
    node_edge_ids: np.ndarray,
    horizontal_t: np.ndarray,
    vertical_t: np.ndarray,
    origin_x: float,
    origin_y: float,
    pitch_x: float,
    pitch_y: float,
    nx: int,
    ny: int,
) -> np.ndarray:
    horizontal_count = ny * (nx - 1)
    output = np.empty((int(node_edge_ids.shape[0]), 2), dtype=np.float64)
    for node_index in range(int(node_edge_ids.shape[0])):
        edge_id = int(node_edge_ids[node_index])
        if edge_id < horizontal_count:
            row = edge_id // (nx - 1)
            column = edge_id - row * (nx - 1)
            output[node_index, 0] = origin_x + pitch_x * (
                float(column) + float(horizontal_t[edge_id])
            )
            output[node_index, 1] = origin_y + pitch_y * float(row)
        else:
            local_id = edge_id - horizontal_count
            row = local_id // nx
            column = local_id - row * nx
            output[node_index, 0] = origin_x + pitch_x * float(column)
            output[node_index, 1] = origin_y + pitch_y * (
                float(row) + float(vertical_t[local_id])
            )
    return output


@njit(cache=True)
def _next_path_neighbor_nb(
    neighbors: np.ndarray, current: int, previous: int
) -> int:
    first = int(neighbors[current, 0])
    second = int(neighbors[current, 1])
    if previous < 0:
        if first < 0:
            return second
        if second < 0:
            return first
        return first if first < second else second
    if first >= 0 and first != previous:
        return first
    if second >= 0 and second != previous:
        return second
    return -1


@njit(cache=True)
def _collect_edge_paths_nb(
    neighbors: np.ndarray, degree: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    node_count = int(neighbors.shape[0])
    visited = np.zeros((node_count,), dtype=np.uint8)
    starts = np.empty((node_count,), dtype=np.int64)
    lengths = np.empty((node_count,), dtype=np.int32)
    closed = np.zeros((node_count,), dtype=np.uint8)
    path_count = 0

    # compact node id はgrid edge idの昇順なので、走査順がopen chainの始点と出力順を
    # 決定する。open chainはclosed loopより先に収集する。
    for start in range(node_count):
        if int(degree[start]) != 1 or int(visited[start]) != 0:
            continue
        previous = -1
        current = start
        length = 0
        while current >= 0 and int(visited[current]) == 0:
            visited[current] = 1
            length += 1
            following = _next_path_neighbor_nb(neighbors, current, previous)
            previous = current
            current = following
        if length >= 2:
            starts[path_count] = start
            lengths[path_count] = length
            path_count += 1

    # 最初の走査後に残る正しい連結成分は、全nodeのdegreeが2のcycleになる。
    for start in range(node_count):
        if int(degree[start]) != 2 or int(visited[start]) != 0:
            continue
        previous = -1
        current = start
        length = 0
        valid = True
        while True:
            if int(visited[current]) != 0:
                if current == start:
                    break
                valid = False
                break
            visited[current] = 1
            length += 1
            following = _next_path_neighbor_nb(neighbors, current, previous)
            if following < 0 or int(degree[following]) != 2:
                valid = False
                break
            previous = current
            current = following
        if valid and length >= 3:
            starts[path_count] = start
            lengths[path_count] = length
            closed[path_count] = 1
            path_count += 1
    return starts, lengths, closed, path_count


@njit(cache=True)
def _pack_edge_paths_nb(
    neighbors: np.ndarray,
    starts: np.ndarray,
    lengths: np.ndarray,
    closed: np.ndarray,
    path_count: int,
) -> tuple[np.ndarray, np.ndarray]:
    offsets = np.empty((path_count + 1,), dtype=np.int32)
    offsets[0] = 0
    total = 0
    for path_index in range(path_count):
        total += int(lengths[path_index]) + int(closed[path_index])
        offsets[path_index + 1] = total

    indices = np.empty((total,), dtype=np.int64)
    cursor = 0
    for path_index in range(path_count):
        start = int(starts[path_index])
        previous = -1
        current = start
        for _ in range(int(lengths[path_index])):
            indices[cursor] = current
            cursor += 1
            following = _next_path_neighbor_nb(neighbors, current, previous)
            previous = current
            current = following
        if int(closed[path_index]) != 0:
            indices[cursor] = start
            cursor += 1
    return indices, offsets


@njit(cache=True)
def _interpolate_level_nb(a: float, b: float, level: float) -> float:
    denominator = b - a
    if denominator == 0.0:
        return 0.5
    value = (level - a) / denominator
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


@njit(cache=True)
def _count_marching_segments_nb(
    field: np.ndarray,
    level: float,
    mask: np.ndarray,
    use_mask: bool,
    samples: np.ndarray,
    use_sample_range: bool,
    sample_min: float,
    sample_max: float,
) -> int:
    ny = int(field.shape[0])
    nx = int(field.shape[1])
    count = 0
    for row in range(ny - 1):
        for column in range(nx - 1):
            if use_mask and (
                int(mask[row, column]) == 0
                or int(mask[row, column + 1]) == 0
                or int(mask[row + 1, column + 1]) == 0
                or int(mask[row + 1, column]) == 0
            ):
                continue
            v00 = float(field[row, column])
            v10 = float(field[row, column + 1])
            v11 = float(field[row + 1, column + 1])
            v01 = float(field[row + 1, column])
            b0 = v00 >= level
            b1 = v10 >= level
            b2 = v11 >= level
            b3 = v01 >= level
            case = (1 if b0 else 0) | (2 if b1 else 0) | (4 if b2 else 0) | (
                8 if b3 else 0
            )
            if case == 0 or case == 15:
                continue

            valid = 0
            if b0 != b1:
                interpolation = _interpolate_level_nb(v00, v10, level)
                sample = float(samples[row, column]) + interpolation * float(
                    samples[row, column + 1] - samples[row, column]
                )
                if not use_sample_range or sample_min <= sample <= sample_max:
                    valid += 1
            if b1 != b2:
                interpolation = _interpolate_level_nb(v10, v11, level)
                sample = float(samples[row, column + 1]) + interpolation * float(
                    samples[row + 1, column + 1] - samples[row, column + 1]
                )
                if not use_sample_range or sample_min <= sample <= sample_max:
                    valid += 1
            if b3 != b2:
                interpolation = _interpolate_level_nb(v01, v11, level)
                sample = float(samples[row + 1, column]) + interpolation * float(
                    samples[row + 1, column + 1] - samples[row + 1, column]
                )
                if not use_sample_range or sample_min <= sample <= sample_max:
                    valid += 1
            if b0 != b3:
                interpolation = _interpolate_level_nb(v00, v01, level)
                sample = float(samples[row, column]) + interpolation * float(
                    samples[row + 1, column] - samples[row, column]
                )
                if not use_sample_range or sample_min <= sample <= sample_max:
                    valid += 1
            if valid == 2:
                count += 1
            elif valid == 4:
                count += 2
    return count


@njit(cache=True)
def _fill_marching_segments_nb(
    field: np.ndarray,
    level: float,
    mask: np.ndarray,
    use_mask: bool,
    samples: np.ndarray,
    use_sample_range: bool,
    sample_min: float,
    sample_max: float,
    edges_a: np.ndarray,
    edges_b: np.ndarray,
    horizontal_t: np.ndarray,
    vertical_t: np.ndarray,
) -> int:
    ny = int(field.shape[0])
    nx = int(field.shape[1])
    horizontal_count = ny * (nx - 1)
    cursor = 0
    for row in range(ny - 1):
        for column in range(nx - 1):
            if use_mask and (
                int(mask[row, column]) == 0
                or int(mask[row, column + 1]) == 0
                or int(mask[row + 1, column + 1]) == 0
                or int(mask[row + 1, column]) == 0
            ):
                continue
            v00 = float(field[row, column])
            v10 = float(field[row, column + 1])
            v11 = float(field[row + 1, column + 1])
            v01 = float(field[row + 1, column])
            b0 = v00 >= level
            b1 = v10 >= level
            b2 = v11 >= level
            b3 = v01 >= level
            case = (1 if b0 else 0) | (2 if b1 else 0) | (4 if b2 else 0) | (
                8 if b3 else 0
            )
            if case == 0 or case == 15:
                continue

            present0 = False
            present1 = False
            present2 = False
            present3 = False
            id0 = np.int32(0)
            id1 = np.int32(0)
            id2 = np.int32(0)
            id3 = np.int32(0)
            if b0 != b1:
                interpolation = _interpolate_level_nb(v00, v10, level)
                sample = float(samples[row, column]) + interpolation * float(
                    samples[row, column + 1] - samples[row, column]
                )
                if not use_sample_range or sample_min <= sample <= sample_max:
                    present0 = True
                    edge_id = row * (nx - 1) + column
                    id0 = np.int32(edge_id)
                    horizontal_t[edge_id] = np.float32(interpolation)
            if b1 != b2:
                interpolation = _interpolate_level_nb(v10, v11, level)
                sample = float(samples[row, column + 1]) + interpolation * float(
                    samples[row + 1, column + 1] - samples[row, column + 1]
                )
                if not use_sample_range or sample_min <= sample <= sample_max:
                    present1 = True
                    edge_id = row * nx + column + 1
                    id1 = np.int32(horizontal_count + edge_id)
                    vertical_t[edge_id] = np.float32(interpolation)
            if b3 != b2:
                interpolation = _interpolate_level_nb(v01, v11, level)
                sample = float(samples[row + 1, column]) + interpolation * float(
                    samples[row + 1, column + 1] - samples[row + 1, column]
                )
                if not use_sample_range or sample_min <= sample <= sample_max:
                    present2 = True
                    edge_id = (row + 1) * (nx - 1) + column
                    id2 = np.int32(edge_id)
                    horizontal_t[edge_id] = np.float32(interpolation)
            if b0 != b3:
                interpolation = _interpolate_level_nb(v00, v01, level)
                sample = float(samples[row, column]) + interpolation * float(
                    samples[row + 1, column] - samples[row, column]
                )
                if not use_sample_range or sample_min <= sample <= sample_max:
                    present3 = True
                    edge_id = row * nx + column
                    id3 = np.int32(horizontal_count + edge_id)
                    vertical_t[edge_id] = np.float32(interpolation)

            point_count = int(present0) + int(present1) + int(present2) + int(present3)
            if point_count == 2:
                first = np.int32(0)
                second = np.int32(0)
                found = False
                if present0:
                    first = id0
                    found = True
                if present1:
                    if found:
                        second = id1
                    else:
                        first = id1
                        found = True
                if present2:
                    if found:
                        second = id2
                    else:
                        first = id2
                        found = True
                if present3:
                    if found:
                        second = id3
                    else:
                        first = id3
                edges_a[cursor] = first
                edges_b[cursor] = second
                cursor += 1
                continue
            if point_count != 4:
                continue

            center_inside = 0.25 * (v00 + v10 + v11 + v01) >= level
            if case == 5:
                if center_inside:
                    edges_a[cursor] = id0
                    edges_b[cursor] = id1
                    cursor += 1
                    edges_a[cursor] = id2
                    edges_b[cursor] = id3
                else:
                    edges_a[cursor] = id0
                    edges_b[cursor] = id3
                    cursor += 1
                    edges_a[cursor] = id1
                    edges_b[cursor] = id2
                cursor += 1
                continue
            if case == 10:
                if center_inside:
                    edges_a[cursor] = id0
                    edges_b[cursor] = id3
                    cursor += 1
                    edges_a[cursor] = id1
                    edges_b[cursor] = id2
                else:
                    edges_a[cursor] = id0
                    edges_b[cursor] = id1
                    cursor += 1
                    edges_a[cursor] = id2
                    edges_b[cursor] = id3
                cursor += 1
                continue
            edges_a[cursor] = id0
            edges_b[cursor] = id1
            cursor += 1
            edges_a[cursor] = id2
            edges_b[cursor] = id3
            cursor += 1
    return cursor


def _stitch_grid_edges(
    edges_a: np.ndarray,
    edges_b: np.ndarray,
    *,
    horizontal_t: np.ndarray,
    vertical_t: np.ndarray,
    origin_x: float,
    origin_y: float,
    pitch: float,
    nx: int,
    ny: int,
) -> list[np.ndarray]:
    if edges_a.size == 0:
        return []
    nondegenerate = edges_a != edges_b
    edges_a = edges_a[nondegenerate]
    edges_b = edges_b[nondegenerate]
    if edges_a.size == 0:
        return []
    total_edge_count = ny * (nx - 1) + (ny - 1) * nx
    node_edge_ids, compact_a, compact_b = _compact_grid_edge_ids_nb(
        edges_a.astype(np.int32, copy=False),
        edges_b.astype(np.int32, copy=False),
        total_edge_count,
    )
    node_xy = _grid_edge_nodes_to_xy_nb(
        node_edge_ids,
        horizontal_t,
        vertical_t,
        origin_x,
        origin_y,
        pitch,
        nx,
        ny,
    )
    neighbors, degree = _build_edge_neighbors_nb(
        int(node_xy.shape[0]), compact_a, compact_b
    )
    starts, lengths, cycle_count = _collect_edge_cycles_nb(neighbors, degree)
    if cycle_count <= 0:
        return []
    indices, offsets = _pack_edge_cycles_nb(
        neighbors, starts, lengths, int(cycle_count)
    )
    return [
        node_xy[indices[int(offsets[index]) : int(offsets[index + 1])]]
        for index in range(int(offsets.size) - 1)
        if int(offsets[index + 1]) - int(offsets[index]) >= 4
    ]


def _stitch_grid_paths(
    edges_a: np.ndarray,
    edges_b: np.ndarray,
    *,
    horizontal_t: np.ndarray,
    vertical_t: np.ndarray,
    origin_x: float,
    origin_y: float,
    pitch_x: float,
    pitch_y: float,
    nx: int,
    ny: int,
) -> list[np.ndarray]:
    if edges_a.size == 0:
        return []
    nondegenerate = edges_a != edges_b
    edges_a = edges_a[nondegenerate]
    edges_b = edges_b[nondegenerate]
    if edges_a.size == 0:
        return []
    total_edge_count = ny * (nx - 1) + (ny - 1) * nx
    node_edge_ids, compact_a, compact_b = _compact_grid_edge_ids_nb(
        edges_a.astype(np.int32, copy=False),
        edges_b.astype(np.int32, copy=False),
        total_edge_count,
    )
    node_xy = _grid_edge_nodes_to_xy_rect_nb(
        node_edge_ids,
        horizontal_t,
        vertical_t,
        origin_x,
        origin_y,
        pitch_x,
        pitch_y,
        nx,
        ny,
    )
    neighbors, degree = _build_edge_neighbors_nb(
        int(node_xy.shape[0]), compact_a, compact_b
    )
    starts, lengths, closed, path_count = _collect_edge_paths_nb(neighbors, degree)
    if path_count <= 0:
        return []
    indices, offsets = _pack_edge_paths_nb(
        neighbors, starts, lengths, closed, int(path_count)
    )
    return [
        node_xy[indices[int(offsets[index]) : int(offsets[index + 1])]]
        for index in range(int(offsets.size) - 1)
        if int(offsets[index + 1]) - int(offsets[index]) >= 2
    ]


def marching_squares_paths(
    field: np.ndarray,
    *,
    origin_x: float,
    origin_y: float,
    pitch_x: float,
    pitch_y: float | None = None,
    level: float = 0.0,
    mask: np.ndarray | None = None,
    sample_field: np.ndarray | None = None,
    sample_range: tuple[float, float] | None = None,
) -> list[np.ndarray]:
    """矩形gridのMarching Squaresをopen chainとclosed loopへ縫合する。

    Parameters
    ----------
    field : np.ndarray
        各grid点のscalar値を保持する2次元配列。
    origin_x : float
        grid左下点のX座標。
    origin_y : float
        grid左下点のY座標。
    pitch_x : float
        X方向のgrid間隔。
    pitch_y : float | None, optional
        Y方向のgrid間隔。省略時は ``pitch_x`` と同じ値を使う。
    level : float, optional
        抽出する等値線のscalar値。
    mask : np.ndarray | None, optional
        有効なgrid点を非ゼロで表す ``field`` と同じshapeの配列。
    sample_field : np.ndarray | None, optional
        ``sample_range`` の判定に使う、 ``field`` と同じshapeのscalar値。
    sample_range : tuple[float, float] | None, optional
        線分端点で補間した ``sample_field`` の許容範囲。

    Returns
    -------
    list[np.ndarray]
        ``(N, 2)`` の座標配列のリスト。open chainをgrid edge id順で先に返し、
        closed loopは始点と終点を一致させてその後に返す。

    Raises
    ------
    ValueError
        ``mask`` または有効な ``sample_field`` のshapeが ``field`` と異なる場合。
    """

    values = np.asarray(field)
    if values.ndim != 2 or values.shape[0] < 2 or values.shape[1] < 2:
        return []
    use_mask = mask is not None
    mask_values = np.empty((0, 0), dtype=np.uint8) if mask is None else np.asarray(mask)
    if use_mask and mask_values.shape != values.shape:
        raise ValueError("mask と field の shape は一致する必要がある")
    use_sample_range = sample_field is not None and sample_range is not None
    samples = values if sample_field is None else np.asarray(sample_field)
    if use_sample_range and samples.shape != values.shape:
        raise ValueError("sample_field と field の shape は一致する必要がある")
    sample_min, sample_max = (
        (-math.inf, math.inf) if sample_range is None else sample_range
    )
    segment_count = _count_marching_segments_nb(
        values,
        float(level),
        mask_values,
        bool(use_mask),
        samples,
        bool(use_sample_range),
        float(sample_min),
        float(sample_max),
    )
    if segment_count <= 0:
        return []

    ny, nx = int(values.shape[0]), int(values.shape[1])
    horizontal_count = ny * (nx - 1)
    vertical_count = (ny - 1) * nx
    edges_a = np.empty((segment_count,), dtype=np.int32)
    edges_b = np.empty((segment_count,), dtype=np.int32)
    horizontal_t = np.full((horizontal_count,), -1.0, dtype=np.float32)
    vertical_t = np.full((vertical_count,), -1.0, dtype=np.float32)
    filled = _fill_marching_segments_nb(
        values,
        float(level),
        mask_values,
        bool(use_mask),
        samples,
        bool(use_sample_range),
        float(sample_min),
        float(sample_max),
        edges_a,
        edges_b,
        horizontal_t,
        vertical_t,
    )
    effective_pitch_y = float(pitch_x) if pitch_y is None else float(pitch_y)
    return _stitch_grid_paths(
        edges_a[:filled],
        edges_b[:filled],
        horizontal_t=horizontal_t,
        vertical_t=vertical_t,
        origin_x=float(origin_x),
        origin_y=float(origin_y),
        pitch_x=float(pitch_x),
        pitch_y=effective_pitch_y,
        nx=nx,
        ny=ny,
    )


def marching_squares_loops(
    field: np.ndarray,
    *,
    origin_x: float,
    origin_y: float,
    pitch: float,
    level: float = 0.0,
    mask: np.ndarray | None = None,
    sample_field: np.ndarray | None = None,
    sample_range: tuple[float, float] | None = None,
) -> list[np.ndarray]:
    """等間隔gridのMarching Squaresをedge-idで縫合し閉loop列を返す。"""

    values = np.asarray(field)
    if values.ndim != 2 or values.shape[0] < 2 or values.shape[1] < 2:
        return []
    use_mask = mask is not None
    mask_values = np.empty((0, 0), dtype=np.uint8) if mask is None else np.asarray(mask)
    if use_mask and mask_values.shape != values.shape:
        raise ValueError("mask と field の shape は一致する必要がある")
    use_sample_range = sample_field is not None and sample_range is not None
    samples = values if sample_field is None else np.asarray(sample_field)
    if use_sample_range and samples.shape != values.shape:
        raise ValueError("sample_field と field の shape は一致する必要がある")
    sample_min, sample_max = (
        (-math.inf, math.inf) if sample_range is None else sample_range
    )
    segment_count = _count_marching_segments_nb(
        values,
        float(level),
        mask_values,
        bool(use_mask),
        samples,
        bool(use_sample_range),
        float(sample_min),
        float(sample_max),
    )
    if segment_count <= 0:
        return []

    ny, nx = int(values.shape[0]), int(values.shape[1])
    horizontal_count = ny * (nx - 1)
    vertical_count = (ny - 1) * nx
    edges_a = np.empty((segment_count,), dtype=np.int32)
    edges_b = np.empty((segment_count,), dtype=np.int32)
    horizontal_t = np.full((horizontal_count,), -1.0, dtype=np.float32)
    vertical_t = np.full((vertical_count,), -1.0, dtype=np.float32)
    filled = _fill_marching_segments_nb(
        values,
        float(level),
        mask_values,
        bool(use_mask),
        samples,
        bool(use_sample_range),
        float(sample_min),
        float(sample_max),
        edges_a,
        edges_b,
        horizontal_t,
        vertical_t,
    )
    return _stitch_grid_edges(
        edges_a[:filled],
        edges_b[:filled],
        horizontal_t=horizontal_t,
        vertical_t=vertical_t,
        origin_x=float(origin_x),
        origin_y=float(origin_y),
        pitch=float(pitch),
        nx=nx,
        ny=ny,
    )
