"""
Purpose:
    planar ring群のoccupancy、境界、Euclidean距離場を共有raster規約で構築する。
Use when:
    closed regionのmask、hole、SDF、または境界距離を使うeffectを実装する場合。
Constraints:
    - 複数ringの内外判定はeven-odd規則とし、holeを独立した塗り領域へ変えない。
    - ringは閉じたlocal XY境界として扱い、origin、pitch、row/column axisを一貫させる。
    - grid budgetは呼び出し側で確定し、このkernel内で無制限な解像度を選ばない。
"""

from __future__ import annotations

import math

import numpy as np
from numba import njit  # type: ignore[attr-defined, import-untyped]

_EDT_INF = 1e20


@njit(cache=True)
def _scanline_evenodd_mask_nb(
    ys: np.ndarray,
    origin_x: float,
    pitch: float,
    nx: int,
    ring_vertices: np.ndarray,
    ring_offsets: np.ndarray,
    ring_mins: np.ndarray,
    ring_maxs: np.ndarray,
) -> np.ndarray:
    ny = int(ys.shape[0])
    inside = np.zeros((ny, int(nx)), dtype=np.uint8)
    n_rings = int(ring_offsets.shape[0]) - 1
    intersections = np.empty((int(ring_vertices.shape[0]),), dtype=np.float64)

    for row in range(ny):
        y = float(ys[row])
        count = 0
        for ring_index in range(n_rings):
            if y < float(ring_mins[ring_index, 1]) or y > float(
                ring_maxs[ring_index, 1]
            ):
                continue
            start = int(ring_offsets[ring_index])
            stop = int(ring_offsets[ring_index + 1])
            for vertex_index in range(start, stop - 1):
                ay = float(ring_vertices[vertex_index, 1])
                by = float(ring_vertices[vertex_index + 1, 1])
                if (ay > y) == (by > y):
                    continue
                ax = float(ring_vertices[vertex_index, 0])
                bx = float(ring_vertices[vertex_index + 1, 0])
                intersections[count] = ax + (y - ay) * (bx - ax) / (by - ay)
                count += 1

        if count < 2:
            continue
        intersections[:count].sort()
        for pair_index in range(0, count - 1, 2):
            left = float(intersections[pair_index])
            right = float(intersections[pair_index + 1])
            if right <= left:
                continue
            first = int(math.ceil((left - float(origin_x)) / float(pitch)))
            last = int(math.ceil((right - float(origin_x)) / float(pitch)))
            if first < 0:
                first = 0
            if last > int(nx):
                last = int(nx)
            for column in range(first, last):
                inside[row, column] = 1
    return inside


def scanline_evenodd_mask(
    ys: np.ndarray,
    *,
    origin_x: float,
    pitch: float,
    nx: int,
    ring_vertices: np.ndarray,
    ring_offsets: np.ndarray,
    ring_mins: np.ndarray,
    ring_maxs: np.ndarray,
) -> np.ndarray:
    """packed ring群をeven-odd規則で等間隔gridへscanline rasterizeする。"""

    return _scanline_evenodd_mask_nb(
        ys,
        float(origin_x),
        float(pitch),
        int(nx),
        ring_vertices,
        ring_offsets,
        ring_mins,
        ring_maxs,
    )


@njit(cache=True)
def _round_grid_index_nb(value: float) -> int:
    if value >= 0.0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


@njit(cache=True)
def _rasterize_ring_boundary_nb(
    boundary: np.ndarray,
    ring_vertices: np.ndarray,
    ring_offsets: np.ndarray,
    origin_x: float,
    origin_y: float,
    inverse_pitch: float,
) -> None:
    ny = int(boundary.shape[0])
    nx = int(boundary.shape[1])
    for ring_index in range(int(ring_offsets.shape[0]) - 1):
        start = int(ring_offsets[ring_index])
        stop = int(ring_offsets[ring_index + 1])
        for vertex_index in range(start, stop - 1):
            ax = float(ring_vertices[vertex_index, 0])
            ay = float(ring_vertices[vertex_index, 1])
            bx = float(ring_vertices[vertex_index + 1, 0])
            by = float(ring_vertices[vertex_index + 1, 1])

            x0 = _round_grid_index_nb((ax - origin_x) * inverse_pitch)
            y0 = _round_grid_index_nb((ay - origin_y) * inverse_pitch)
            x1 = _round_grid_index_nb((bx - origin_x) * inverse_pitch)
            y1 = _round_grid_index_nb((by - origin_y) * inverse_pitch)
            dx = abs(int(x1 - x0))
            dy = abs(int(y1 - y0))
            step_x = 1 if x0 < x1 else -1
            step_y = 1 if y0 < y1 else -1
            error = dx - dy

            while True:
                if 0 <= x0 < nx and 0 <= y0 < ny:
                    boundary[y0, x0] = 1
                if x0 == x1 and y0 == y1:
                    break
                doubled_error = 2 * error
                if doubled_error > -dy:
                    error -= dy
                    x0 += step_x
                if doubled_error < dx:
                    error += dx
                    y0 += step_y


@njit(cache=True)
def _add_mask_boundary_nb(boundary: np.ndarray, inside: np.ndarray) -> None:
    ny = int(inside.shape[0])
    nx = int(inside.shape[1])
    for row in range(ny):
        for column in range(nx):
            value = int(inside[row, column])
            if column + 1 < nx and value != int(inside[row, column + 1]):
                boundary[row, column] = 1
                boundary[row, column + 1] = 1
            if row + 1 < ny and value != int(inside[row + 1, column]):
                boundary[row, column] = 1
                boundary[row + 1, column] = 1


def rasterize_ring_boundary_mask(
    shape: tuple[int, int],
    *,
    ring_vertices: np.ndarray,
    ring_offsets: np.ndarray,
    origin_x: float,
    origin_y: float,
    pitch: float,
    inside: np.ndarray | None = None,
) -> np.ndarray:
    """packed ring線分と任意のinside差分からEDT seed maskを作る。"""

    boundary = np.zeros((int(shape[0]), int(shape[1])), dtype=np.uint8)
    _rasterize_ring_boundary_nb(
        boundary,
        ring_vertices,
        ring_offsets,
        float(origin_x),
        float(origin_y),
        1.0 / float(pitch),
    )
    if inside is not None:
        _add_mask_boundary_nb(boundary, inside)
    return boundary


@njit(cache=True)
def _squared_edt_1d_nb(
    source: np.ndarray,
    output: np.ndarray,
    locations: np.ndarray,
    boundaries: np.ndarray,
) -> None:
    size = int(source.shape[0])
    first = -1
    for index in range(size):
        if float(source[index]) < float(_EDT_INF):
            first = index
            break
    if first < 0:
        for index in range(size):
            output[index] = float(_EDT_INF)
        return

    envelope_index = 0
    locations[0] = np.int64(first)
    boundaries[0] = -1e30
    boundaries[1] = 1e30
    for query in range(first + 1, size):
        query_value = float(source[query])
        if query_value >= float(_EDT_INF):
            continue
        while True:
            location = int(locations[envelope_index])
            location_value = float(source[location])
            intersection = (
                (query_value + float(query * query))
                - (location_value + float(location * location))
            ) / (2.0 * float(query - location))
            if intersection <= float(boundaries[envelope_index]):
                envelope_index -= 1
                if envelope_index < 0:
                    envelope_index = 0
                    break
                continue
            break
        envelope_index += 1
        locations[envelope_index] = np.int64(query)
        boundaries[envelope_index] = float(intersection)
        boundaries[envelope_index + 1] = 1e30

    envelope_index = 0
    for query in range(size):
        while float(boundaries[envelope_index + 1]) < float(query):
            envelope_index += 1
        location = int(locations[envelope_index])
        delta = float(query - location)
        output[query] = delta * delta + float(source[location])


@njit(cache=True)
def _squared_edt_2d_nb(features: np.ndarray) -> np.ndarray:
    ny = int(features.shape[0])
    nx = int(features.shape[1])
    horizontal = np.empty((ny, nx), dtype=np.float64)
    source_row = np.empty((nx,), dtype=np.float64)
    output_row = np.empty((nx,), dtype=np.float64)
    locations = np.empty((nx,), dtype=np.int64)
    boundaries = np.empty((nx + 1,), dtype=np.float64)

    for row in range(ny):
        has_feature = False
        for column in range(nx):
            if int(features[row, column]) != 0:
                source_row[column] = 0.0
                has_feature = True
            else:
                source_row[column] = float(_EDT_INF)
        if not has_feature:
            for column in range(nx):
                horizontal[row, column] = float(_EDT_INF)
            continue
        _squared_edt_1d_nb(source_row, output_row, locations, boundaries)
        for column in range(nx):
            horizontal[row, column] = output_row[column]

    squared_distance = np.empty((ny, nx), dtype=np.float64)
    source_column = np.empty((ny,), dtype=np.float64)
    output_column = np.empty((ny,), dtype=np.float64)
    column_locations = np.empty((ny,), dtype=np.int64)
    column_boundaries = np.empty((ny + 1,), dtype=np.float64)
    for column in range(nx):
        has_feature = False
        for row in range(ny):
            value = float(horizontal[row, column])
            source_column[row] = value
            if value < float(_EDT_INF):
                has_feature = True
        if not has_feature:
            for row in range(ny):
                squared_distance[row, column] = float(_EDT_INF)
            continue
        _squared_edt_1d_nb(
            source_column,
            output_column,
            column_locations,
            column_boundaries,
        )
        for row in range(ny):
            squared_distance[row, column] = output_column[row]
    return squared_distance


def squared_euclidean_distance_transform(features: np.ndarray) -> np.ndarray:
    """非zero featureまでの2D squared Euclidean distanceを2-passで返す。"""

    values = np.asarray(features)
    if values.ndim != 2:
        raise ValueError("features は2次元配列である必要がある")
    return _squared_edt_2d_nb(values)


@njit(cache=True)
def _signed_distance_grid_edt_nb(
    xs: np.ndarray,
    ys: np.ndarray,
    ring_vertices: np.ndarray,
    ring_offsets: np.ndarray,
    ring_mins: np.ndarray,
    ring_maxs: np.ndarray,
    max_distance: float,
    gamma: float,
    pitch: float,
) -> np.ndarray:
    ny = int(ys.shape[0])
    nx = int(xs.shape[0])
    origin_x = float(xs[0])
    origin_y = float(ys[0])
    inside = _scanline_evenodd_mask_nb(
        ys,
        origin_x,
        pitch,
        nx,
        ring_vertices,
        ring_offsets,
        ring_mins,
        ring_maxs,
    )
    boundary = np.zeros((ny, nx), dtype=np.uint8)
    _rasterize_ring_boundary_nb(
        boundary,
        ring_vertices,
        ring_offsets,
        origin_x,
        origin_y,
        1.0 / pitch,
    )
    _add_mask_boundary_nb(boundary, inside)
    squared_distance = _squared_edt_2d_nb(boundary)

    sdf = np.empty((ny, nx), dtype=np.float64)
    for row in range(ny):
        for column in range(nx):
            distance = math.sqrt(float(squared_distance[row, column])) * pitch
            if max_distance > 0.0 and gamma != 1.0:
                normalized = distance / max_distance
                if normalized < 0.0:
                    normalized = 0.0
                distance = max_distance * math.pow(normalized, gamma)
            if int(inside[row, column]) != 0:
                distance = -distance
            sdf[row, column] = distance
    return sdf


def signed_distance_grid_edt(
    xs: np.ndarray,
    ys: np.ndarray,
    *,
    ring_vertices: np.ndarray,
    ring_offsets: np.ndarray,
    ring_mins: np.ndarray,
    ring_maxs: np.ndarray,
    pitch: float,
    max_distance: float = 0.0,
    gamma: float = 1.0,
) -> np.ndarray:
    """packed ring群からscanline・boundary raster・2-pass EDTでSDFを作る。"""

    return _signed_distance_grid_edt_nb(
        xs,
        ys,
        ring_vertices,
        ring_offsets,
        ring_mins,
        ring_maxs,
        float(max_distance),
        float(gamma),
        float(pitch),
    )
