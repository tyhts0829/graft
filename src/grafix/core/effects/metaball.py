"""閉曲線群を距離場でブレンドし、等値線（輪郭）を生成する effect。"""

from __future__ import annotations

import math

import numpy as np
from numba import (  # type: ignore[attr-defined, import-untyped]
    get_num_threads,
    njit,
    prange,
)

from grafix.core.operation_authoring import effect
from grafix.core.operation_diagnostics import (
    emit_operation_diagnostic,
    grid_spec_from_bbox_with_diagnostic,
)
from grafix.core.parameters.meta import ParamMeta
from grafix.core.preview_quality import current_preview_quality
from grafix.core.realized_geometry import GeomTuple
from grafix.core.geometry_kernels.grid import DEFAULT_MAX_GRID_POINTS
from grafix.core.geometry_kernels.marching import marching_squares_loops
from grafix.core.geometry_kernels.packed import pack_polylines
from grafix.core.geometry_kernels.planar import (
    PlanarFrame,
    PlanarRing,
    extract_planar_rings,
    pack_planar_rings,
    planarity_threshold,
)
from grafix.core.geometry_kernels.raster import scanline_evenodd_mask

_AUTO_CLOSE_THRESHOLD_DEFAULT = 1e-3
MAX_GRID_POINTS = DEFAULT_MAX_GRID_POINTS
DRAFT_MAX_GRID_POINTS = 16_384
DRAFT_MIN_RING_SEGMENTS = 8
DRAFT_MAX_POINT_SEGMENTS = 12_000_000
_PARALLEL_FIELD_WORK_THRESHOLD = 100_000
_PACKED_FIELD_MIN_GRID_POINTS = 256
_PACKED_FIELD_SEGMENT_BYTES = 5 * np.dtype(np.float64).itemsize
_PACKED_FIELD_OFFSET_BYTES = np.dtype(np.int64).itemsize
_PACKED_FIELD_MAX_SEGMENT_SCRATCH_BYTES = 8 * 1024 * 1024
_PACKED_FIELD_MAX_ROW_SCRATCH_BYTES = 8 * 1024 * 1024


metaball_meta = {
    "radius": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=50.0,
        description="離れた入力輪郭どうしを滑らかにつなぐ影響半径。",
    ),
    "threshold": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=5.0,
        description="合成した距離場から出力輪郭を取り出す等値レベル。",
    ),
    "grid_pitch": ParamMeta(
        kind="float",
        ui_min=0.1,
        ui_max=10.0,
        description="距離場を評価する二次元グリッドの間隔。",
    ),
    "auto_close_threshold": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=5.0,
        description="入力線の端点を自動的に閉じるとみなす最大距離。",
    ),
    "output": ParamMeta(
        kind="choice",
        choices=("exterior", "both"),
        description="生成した形状の外周だけ、または外周と穴の両方を出力する。",
    ),
    "keep_original": ParamMeta(
        kind="bool",
        description="生成した輪郭に元の入力線を加えて出力する。",
    ),
}


def _draft_ring_segment_floor(ring: PlanarRing) -> int:
    return min(
        max(0, int(ring.vertices.shape[0]) - 1),
        DRAFT_MIN_RING_SEGMENTS,
    )


def _ring_length_for_draft(vertices: np.ndarray) -> float:
    delta = vertices[1:] - vertices[:-1]
    return float(np.sum(np.sqrt(np.sum(delta * delta, axis=1))))


def _draft_ring_segment_target(ring: PlanarRing, *, step: float) -> int:
    original_segments = max(0, int(ring.vertices.shape[0]) - 1)
    floor_segments = _draft_ring_segment_floor(ring)
    if original_segments <= floor_segments:
        return original_segments
    total_length = _ring_length_for_draft(ring.vertices)
    if not math.isfinite(total_length) or total_length <= 0.0:
        return floor_segments
    return min(
        original_segments,
        max(floor_segments, int(math.ceil(total_length / float(step)))),
    )


def _resample_ring_for_draft(
    vertices: np.ndarray, *, target_segments: int
) -> np.ndarray:
    """リングを指定 segment 数以下の決定的な等弧長サンプルへまとめる。"""

    points = vertices.astype(np.float64, copy=False)
    original_segments = int(points.shape[0]) - 1
    target = max(3, min(int(target_segments), original_segments))
    if original_segments <= target:
        return points

    segment_delta = points[1:] - points[:-1]
    segment_lengths = np.sqrt(np.sum(segment_delta * segment_delta, axis=1))
    positive = segment_lengths > 0.0
    if int(np.count_nonzero(positive)) < 3:
        indices = (
            np.arange(target, dtype=np.int64) * original_segments // target
        )
        sampled = points[:-1][indices]
        return np.concatenate([sampled, sampled[:1]], axis=0)

    starts = points[:-1][positive]
    deltas = segment_delta[positive]
    lengths = segment_lengths[positive]
    total_length = float(np.sum(lengths))
    if not math.isfinite(total_length) or total_length <= 0.0:
        indices = (
            np.arange(target, dtype=np.int64) * original_segments // target
        )
        sampled = points[:-1][indices]
        return np.concatenate([sampled, sampled[:1]], axis=0)

    cumulative = np.empty((int(lengths.size) + 1,), dtype=np.float64)
    cumulative[0] = 0.0
    np.cumsum(lengths, out=cumulative[1:])
    targets = total_length * (
        np.arange(target, dtype=np.float64) / float(target)
    )
    segment_indices = np.searchsorted(cumulative, targets, side="right") - 1
    np.clip(segment_indices, 0, int(lengths.size) - 1, out=segment_indices)
    fractions = (
        targets - cumulative[segment_indices]
    ) / lengths[segment_indices]
    sampled = starts[segment_indices] + fractions[:, None] * deltas[segment_indices]
    return np.concatenate([sampled, sampled[:1]], axis=0)


def _simplify_rings_for_draft(
    rings: list[PlanarRing],
    *,
    pitch: float,
    max_segments: int,
) -> tuple[list[PlanarRing], int, int, int, int]:
    original_ring_count = len(rings)
    original_segments = sum(
        max(0, int(ring.vertices.shape[0]) - 1) for ring in rings
    )
    segment_limit = max(3, int(max_segments))
    floor_total = sum(_draft_ring_segment_floor(ring) for ring in rings)
    selected = rings
    if floor_total > segment_limit:
        max_ring_count = max(
            1,
            segment_limit // DRAFT_MIN_RING_SEGMENTS,
        )
        index_count = min(len(rings), max_ring_count)
        indices = np.linspace(
            0,
            len(rings) - 1,
            num=index_count,
            dtype=np.int64,
        )
        selected = []
        remaining = segment_limit
        for index in indices:
            ring = rings[int(index)]
            floor_segments = _draft_ring_segment_floor(ring)
            if floor_segments > remaining:
                continue
            selected.append(ring)
            remaining -= floor_segments

    target_step = float(pitch)
    target_counts = [
        _draft_ring_segment_target(ring, step=target_step) for ring in selected
    ]
    if sum(target_counts) > segment_limit:
        low = target_step
        high = target_step
        while sum(
            _draft_ring_segment_target(ring, step=high) for ring in selected
        ) > segment_limit:
            high *= 2.0
        for _ in range(64):
            middle = low + 0.5 * (high - low)
            if middle == low or middle == high:
                break
            count = sum(
                _draft_ring_segment_target(ring, step=middle)
                for ring in selected
            )
            if count > segment_limit:
                low = middle
            else:
                high = middle
        target_counts = [
            _draft_ring_segment_target(ring, step=high) for ring in selected
        ]

    simplified: list[PlanarRing] = []
    effective_segments = 0
    for ring, target_segments in zip(selected, target_counts, strict=True):
        vertices = _resample_ring_for_draft(
            ring.vertices,
            target_segments=target_segments,
        )
        effective_segments += max(0, int(vertices.shape[0]) - 1)
        if vertices is ring.vertices:
            simplified.append(ring)
        else:
            simplified.append(
                PlanarRing(
                    vertices=vertices,
                    mins=np.min(vertices, axis=0),
                    maxs=np.max(vertices, axis=0),
                )
            )
    return (
        simplified,
        original_segments,
        effective_segments,
        original_ring_count,
        len(simplified),
    )


@njit(cache=True)
def _evaluate_field_grid_baseline_numba(
    xs: np.ndarray,
    ys: np.ndarray,
    ring_vertices: np.ndarray,
    ring_offsets: np.ndarray,
    inside_mask: np.ndarray,
    inv_r2: float,
) -> np.ndarray:
    """格子点・リング・線分の固定順で評価する scratch-free exact path。"""

    ny = int(ys.shape[0])
    nx = int(xs.shape[0])
    n_rings = int(ring_offsets.shape[0]) - 1

    out = np.zeros((ny, nx), dtype=np.float64)
    for j in range(ny):
        y = float(ys[j])
        for i in range(nx):
            x = float(xs[i])
            val = 0.0

            for ri in range(n_rings):
                s = int(ring_offsets[ri])
                e = int(ring_offsets[ri + 1])

                min_ds = 1e300
                for k in range(s, e - 1):
                    ax = float(ring_vertices[k, 0])
                    ay = float(ring_vertices[k, 1])
                    bx = float(ring_vertices[k + 1, 0])
                    by = float(ring_vertices[k + 1, 1])

                    dx = bx - ax
                    dy = by - ay
                    denom = dx * dx + dy * dy
                    if denom <= 0.0:
                        ds = (x - ax) * (x - ax) + (y - ay) * (y - ay)
                    else:
                        t = ((x - ax) * dx + (y - ay) * dy) / denom
                        if t < 0.0:
                            t = 0.0
                        elif t > 1.0:
                            t = 1.0
                        cx = ax + t * dx
                        cy = ay + t * dy
                        ds = (x - cx) * (x - cx) + (y - cy) * (y - cy)
                    if ds < min_ds:
                        min_ds = ds

                val += math.exp(-min_ds * inv_r2)

            val += float(inside_mask[j, i])
            out[j, i] = val

    return out


@njit(inline="always")
def _pack_field_segments_numba(
    ring_vertices: np.ndarray,
    ring_offsets: np.ndarray,
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
]:
    n_rings = int(ring_offsets.shape[0]) - 1
    n_segments = 0
    for ri in range(n_rings):
        n_segments += max(
            0,
            int(ring_offsets[ri + 1]) - int(ring_offsets[ri]) - 1,
        )
    segment_offsets = np.empty((n_rings + 1,), dtype=np.int64)
    segment_ax = np.empty((n_segments,), dtype=np.float64)
    segment_ay = np.empty((n_segments,), dtype=np.float64)
    segment_dx = np.empty((n_segments,), dtype=np.float64)
    segment_dy = np.empty((n_segments,), dtype=np.float64)
    segment_denom = np.empty((n_segments,), dtype=np.float64)
    segment_offsets[0] = 0
    cursor = 0
    for ri in range(n_rings):
        start = int(ring_offsets[ri])
        stop = int(ring_offsets[ri + 1])
        for k in range(start, stop - 1):
            ax = float(ring_vertices[k, 0])
            ay = float(ring_vertices[k, 1])
            dx = float(ring_vertices[k + 1, 0]) - ax
            dy = float(ring_vertices[k + 1, 1]) - ay
            segment_ax[cursor] = ax
            segment_ay[cursor] = ay
            segment_dx[cursor] = dx
            segment_dy[cursor] = dy
            segment_denom[cursor] = dx * dx + dy * dy
            cursor += 1
        segment_offsets[ri + 1] = cursor

    return (
        segment_offsets,
        segment_ax,
        segment_ay,
        segment_dx,
        segment_dy,
        segment_denom,
    )


@njit(inline="always")
def _evaluate_field_row_numba(
    xs: np.ndarray,
    y: float,
    segment_offsets: np.ndarray,
    segment_ax: np.ndarray,
    segment_ay: np.ndarray,
    segment_dx: np.ndarray,
    segment_dy: np.ndarray,
    segment_denom: np.ndarray,
    inside_row: np.ndarray,
    minimum_row: np.ndarray,
    out_row: np.ndarray,
    inv_r2: float,
) -> None:
    nx = int(xs.shape[0])
    n_rings = int(segment_offsets.shape[0]) - 1
    for ri in range(n_rings):
        for i in range(nx):
            minimum_row[i] = 1e300

        start = int(segment_offsets[ri])
        stop = int(segment_offsets[ri + 1])
        for segment_index in range(start, stop):
            ax = float(segment_ax[segment_index])
            ay = float(segment_ay[segment_index])
            dx = float(segment_dx[segment_index])
            dy = float(segment_dy[segment_index])
            denominator = float(segment_denom[segment_index])
            for i in range(nx):
                x = float(xs[i])
                if denominator <= 0.0:
                    distance_sq = (x - ax) * (x - ax) + (y - ay) * (y - ay)
                else:
                    position = ((x - ax) * dx + (y - ay) * dy) / denominator
                    if position < 0.0:
                        position = 0.0
                    elif position > 1.0:
                        position = 1.0
                    closest_x = ax + position * dx
                    closest_y = ay + position * dy
                    distance_sq = (x - closest_x) * (x - closest_x) + (y - closest_y) * (
                        y - closest_y
                    )
                if distance_sq < minimum_row[i]:
                    minimum_row[i] = distance_sq

        for i in range(nx):
            out_row[i] += math.exp(-minimum_row[i] * inv_r2)

    for i in range(nx):
        out_row[i] += float(inside_row[i])


@njit(cache=True)
def _evaluate_field_grid_serial_numba(
    xs: np.ndarray,
    ys: np.ndarray,
    ring_vertices: np.ndarray,
    ring_offsets: np.ndarray,
    inside_mask: np.ndarray,
    inv_r2: float,
) -> np.ndarray:
    ny = int(ys.shape[0])
    nx = int(xs.shape[0])
    segments = _pack_field_segments_numba(ring_vertices, ring_offsets)
    out = np.zeros((ny, nx), dtype=np.float64)
    minimum_row = np.empty((nx,), dtype=np.float64)
    for j in range(ny):
        _evaluate_field_row_numba(
            xs,
            float(ys[j]),
            segments[0],
            segments[1],
            segments[2],
            segments[3],
            segments[4],
            segments[5],
            inside_mask[j],
            minimum_row,
            out[j],
            inv_r2,
        )
    return out


@njit(cache=True, parallel=True)
def _evaluate_field_grid_parallel_numba(
    xs: np.ndarray,
    ys: np.ndarray,
    ring_vertices: np.ndarray,
    ring_offsets: np.ndarray,
    inside_mask: np.ndarray,
    inv_r2: float,
) -> np.ndarray:
    ny = int(ys.shape[0])
    nx = int(xs.shape[0])
    segments = _pack_field_segments_numba(ring_vertices, ring_offsets)
    out = np.zeros((ny, nx), dtype=np.float64)
    for j in prange(ny):
        # Numba はこの allocation を loop 外へ hoist し、worker ごとに再利用する。
        minimum_row = np.empty((nx,), dtype=np.float64)
        _evaluate_field_row_numba(
            xs,
            float(ys[j]),
            segments[0],
            segments[1],
            segments[2],
            segments[3],
            segments[4],
            segments[5],
            inside_mask[j],
            minimum_row,
            out[j],
            inv_r2,
        )
    return out


def _packed_field_segment_scratch_bytes(
    *,
    segment_count: int,
    ring_count: int,
) -> int:
    """segment invariant pack の常駐 scratch byte 数を返す。"""

    return (
        max(0, int(segment_count)) * _PACKED_FIELD_SEGMENT_BYTES
        + (max(0, int(ring_count)) + 1) * _PACKED_FIELD_OFFSET_BYTES
    )


def _use_packed_field_path(
    *,
    nx: int,
    ny: int,
    segment_count: int,
    ring_count: int,
) -> bool:
    """pack 構築コストと追加メモリが有利な範囲だけ高速経路を許可する。"""

    grid_points = max(0, int(nx)) * max(0, int(ny))
    if grid_points < _PACKED_FIELD_MIN_GRID_POINTS:
        return False
    if (
        _packed_field_segment_scratch_bytes(
            segment_count=segment_count,
            ring_count=ring_count,
        )
        > _PACKED_FIELD_MAX_SEGMENT_SCRATCH_BYTES
    ):
        return False
    return (
        max(0, int(nx)) * np.dtype(np.float64).itemsize
        <= _PACKED_FIELD_MAX_ROW_SCRATCH_BYTES
    )


def _evaluate_field_grid_numba(
    xs: np.ndarray,
    ys: np.ndarray,
    ring_vertices: np.ndarray,
    ring_offsets: np.ndarray,
    inside_mask: np.ndarray,
    inv_r2: float,
) -> np.ndarray:
    """resource gate 後、小仕事量は serial、大仕事量は行並列化する。"""

    n_rings = max(0, int(ring_offsets.shape[0]) - 1)
    n_segments = max(0, int(ring_vertices.shape[0]) - n_rings)
    nx = int(xs.shape[0])
    ny = int(ys.shape[0])
    if not _use_packed_field_path(
        nx=nx,
        ny=ny,
        segment_count=n_segments,
        ring_count=n_rings,
    ):
        return _evaluate_field_grid_baseline_numba(
            xs,
            ys,
            ring_vertices,
            ring_offsets,
            inside_mask,
            inv_r2,
        )

    work = nx * ny * n_segments
    thread_count = get_num_threads()
    parallel_row_scratch = (
        thread_count * nx * np.dtype(np.float64).itemsize
    )
    kernel = (
        _evaluate_field_grid_parallel_numba
        if (
            thread_count > 1
            and ny > 1
            and work >= _PARALLEL_FIELD_WORK_THRESHOLD
            and parallel_row_scratch
            <= _PACKED_FIELD_MAX_ROW_SCRATCH_BYTES
        )
        else _evaluate_field_grid_serial_numba
    )
    return kernel(
        xs,
        ys,
        ring_vertices,
        ring_offsets,
        inside_mask,
        inv_r2,
    )


def _pack_loops_xy(loops_xy: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """可変長ループ列を Numba 入力用の連結バッファへパックする。"""
    n = len(loops_xy)
    total = 0
    for pts in loops_xy:
        total += int(pts.shape[0])

    vertices = np.empty((total, 2), dtype=np.float64)
    offsets = np.empty((n + 1,), dtype=np.int32)
    offsets[0] = 0
    cursor = 0
    for i, pts in enumerate(loops_xy):
        v = pts.astype(np.float64, copy=False)
        m = int(v.shape[0])
        vertices[cursor : cursor + m] = v
        cursor += m
        offsets[i + 1] = np.int32(cursor)
    return vertices, offsets


@njit(cache=True)
def _exterior_loop_mask_numba(
    field: np.ndarray,
    x0: float,
    y0: float,
    pitch: float,
    level: float,
    loop_vertices: np.ndarray,
    loop_offsets: np.ndarray,
) -> np.ndarray:
    ny = int(field.shape[0])
    nx = int(field.shape[1])
    n_loops = int(loop_offsets.shape[0]) - 1
    out = np.zeros((n_loops,), dtype=np.uint8)

    if ny <= 0 or nx <= 0:
        return out
    if pitch <= 0.0 or not math.isfinite(pitch):
        return out

    eps = 0.5 * float(pitch)
    for li in range(n_loops):
        s = int(loop_offsets[li])
        e = int(loop_offsets[li + 1])
        if e - s < 4:
            continue

        area2 = 0.0
        for k in range(s, e - 1):
            x1 = float(loop_vertices[k, 0])
            y1 = float(loop_vertices[k, 1])
            x2 = float(loop_vertices[k + 1, 0])
            y2 = float(loop_vertices[k + 1, 1])
            area2 += x1 * y2 - y1 * x2

        if area2 == 0.0 or not math.isfinite(area2):
            continue
        ccw = area2 > 0.0

        k0 = -1
        longest_edge_sq = 1e-12
        for k in range(s, e - 1):
            dx = float(loop_vertices[k + 1, 0] - loop_vertices[k, 0])
            dy = float(loop_vertices[k + 1, 1] - loop_vertices[k, 1])
            edge_sq = dx * dx + dy * dy
            if edge_sq > longest_edge_sq:
                longest_edge_sq = edge_sq
                k0 = k
        if k0 < 0:
            continue

        dx = float(loop_vertices[k0 + 1, 0] - loop_vertices[k0, 0])
        dy = float(loop_vertices[k0 + 1, 1] - loop_vertices[k0, 1])
        if ccw:
            nx_in, ny_in = -dy, dx
        else:
            nx_in, ny_in = dy, -dx
        n_norm = math.sqrt(nx_in * nx_in + ny_in * ny_in)
        if n_norm <= 0.0 or not math.isfinite(n_norm):
            continue
        nx_in /= n_norm
        ny_in /= n_norm

        xin = float(loop_vertices[k0, 0]) + float(nx_in) * eps
        yin = float(loop_vertices[k0, 1]) + float(ny_in) * eps
        fx = (xin - float(x0)) / float(pitch)
        fy = (yin - float(y0)) / float(pitch)
        ii = int(math.floor(fx))
        jj = int(math.floor(fy))
        tx = fx - float(ii)
        ty = fy - float(jj)
        if ii < 0:
            ii = 0
            tx = 0.0
        elif ii >= nx - 1:
            ii = nx - 2
            tx = 1.0
        if jj < 0:
            jj = 0
            ty = 0.0
        elif jj >= ny - 1:
            jj = ny - 2
            ty = 1.0

        value0 = (1.0 - tx) * float(field[jj, ii]) + tx * float(field[jj, ii + 1])
        value1 = (1.0 - tx) * float(field[jj + 1, ii]) + tx * float(
            field[jj + 1, ii + 1]
        )
        if (1.0 - ty) * value0 + ty * value1 >= float(level):
            out[li] = 1

    return out


def _filter_exterior_loops(
    loops_xy: list[np.ndarray],
    *,
    field: np.ndarray,
    x0: float,
    y0: float,
    pitch: float,
    level: float,
) -> list[np.ndarray]:
    """等値線ループ列から外周（exterior）のみ抽出する。"""
    if not loops_xy:
        return []

    loop_vertices, loop_offsets = _pack_loops_xy(loops_xy)
    mask_u8 = _exterior_loop_mask_numba(
        field.astype(np.float64, copy=False),
        float(x0),
        float(y0),
        float(pitch),
        float(level),
        loop_vertices,
        loop_offsets,
    )
    return [pts for i, pts in enumerate(loops_xy) if bool(mask_u8[int(i)])]


@effect(meta=metaball_meta)
def metaball(
    g: GeomTuple,
    *,
    radius: float = 3.0,
    threshold: float = 1.0,
    grid_pitch: float = 0.5,
    auto_close_threshold: float = _AUTO_CLOSE_THRESHOLD_DEFAULT,
    output: str = "both",  # "exterior" | "both"
    keep_original: bool = False,
) -> GeomTuple:
    """閉曲線群をメタボール的に接続し、輪郭（外周＋穴）を生成する。

    入力 `inputs[0]` の全ポリラインを走査し、閉曲線（端点が近ければ自動クローズ）を
    face として検知して対象にする。開曲線は無視する。

    Parameters
    ----------
    g : tuple[np.ndarray, np.ndarray]
        入力実体ジオメトリ（coords, offsets）。
    radius : float, default 3.0
        接続の届く距離（falloff 半径）[mm]。大きいほど繋がりやすい。
    threshold : float, default 1.0
        等値線レベル。`1.0` 付近が基準（内側項 + 距離場の合成）。
    grid_pitch : float, default 0.5
        距離場を評価する 2D グリッドのピッチ [mm]。
    auto_close_threshold : float, default 1e-3
        端点距離がこの値以下なら閉曲線扱いとして自動で閉じる [mm]。
    output : str, default "both"
        出力輪郭の選択。

        - `"both"`: 外周＋穴（holes）を出力
        - `"exterior"`: 外周のみ出力
    keep_original : bool, default False
        True のとき、生成結果に加えて元のポリラインも出力に含める。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        生成した輪郭（外周＋穴）を含む実体ジオメトリ（coords, offsets）。
    """
    r = radius
    if r <= 0.0:
        raise ValueError("metaball: radius は正の有限値である必要がある")

    level = threshold
    if level <= 0.0:
        raise ValueError("metaball: threshold は正の有限値である必要がある")

    pitch = grid_pitch
    if pitch <= 0.0:
        raise ValueError("metaball: grid_pitch は正の有限値である必要がある")

    output_s = output

    auto_close = auto_close_threshold
    if auto_close < 0.0:
        raise ValueError(
            "metaball: auto_close_threshold は 0 以上の有限値である必要がある"
        )

    coords, offsets = g
    if coords.shape[0] == 0:
        return coords, offsets

    frame = PlanarFrame.from_points(coords, offsets)
    if not frame.is_planar(planarity_threshold(coords)):
        return coords, offsets
    coords_xy_all = frame.to_local(coords)

    rings = extract_planar_rings(
        coords_xy_all,
        offsets,
        auto_close_threshold=auto_close,
    )
    if not rings:
        return coords, offsets

    mins = np.min(np.stack([r0.mins for r0 in rings], axis=0), axis=0)
    maxs = np.max(np.stack([r0.maxs for r0 in rings], axis=0), axis=0)

    margin = 2.0 * r + 2.0 * pitch
    quality = current_preview_quality()
    if quality == "draft":
        minimum_segments = sum(
            _draft_ring_segment_floor(ring) for ring in rings
        )
        draft_grid_limit = min(
            DRAFT_MAX_GRID_POINTS,
            max(
                4,
                DRAFT_MAX_POINT_SEGMENTS // max(1, minimum_segments),
            ),
        )
    else:
        draft_grid_limit = DRAFT_MAX_GRID_POINTS
    grid = grid_spec_from_bbox_with_diagnostic(
        mins,
        maxs,
        pitch=pitch,
        padding=margin,
        max_points=(draft_grid_limit if quality == "draft" else MAX_GRID_POINTS),
        overflow=("coarsen" if quality == "draft" else "reject"),
    )
    if grid is None:
        return coords, offsets
    xs, ys = grid.coordinates()
    pitch = grid.pitch
    if quality == "draft" and grid.coarsened:
        emit_operation_diagnostic(
            op="metaball.grid_pitch",
            original_value=grid.requested_pitch,
            effective_value=grid.pitch,
            reason=(
                "draft preview coarsened the field grid; final capture keeps the "
                "requested pitch"
            ),
            severity="info",
        )

    if quality == "draft":
        (
            rings,
            original_segments,
            effective_segments,
            original_ring_count,
            effective_ring_count,
        ) = _simplify_rings_for_draft(
            rings,
            pitch=pitch,
            max_segments=DRAFT_MAX_POINT_SEGMENTS // grid.point_count,
        )
        if effective_ring_count != original_ring_count:
            emit_operation_diagnostic(
                op="metaball.rings",
                original_value=original_ring_count,
                effective_value=effective_ring_count,
                reason=(
                    "draft preview sampled the ring set to keep field work bounded; "
                    "final capture keeps every input ring"
                ),
                severity="info",
            )
        if effective_segments != original_segments:
            emit_operation_diagnostic(
                op="metaball.ring_segments",
                original_value=original_segments,
                effective_value=effective_segments,
                reason=(
                    "draft preview resampled sub-grid ring detail; final capture "
                    "keeps every input segment"
                ),
                severity="info",
            )
        original_work = grid.point_count * original_segments
        effective_work = grid.point_count * effective_segments
        if effective_work != original_work:
            emit_operation_diagnostic(
                op="metaball.point_segments",
                original_value=original_work,
                effective_value=effective_work,
                reason=(
                    "draft preview bounded points × segments field work; final "
                    "capture keeps full ring detail"
                ),
                severity="info",
            )
    ring_vertices, ring_offsets, ring_mins, ring_maxs = pack_planar_rings(rings)
    inside_mask = scanline_evenodd_mask(
        ys,
        origin_x=grid.origin_x,
        pitch=pitch,
        nx=grid.nx,
        ring_vertices=ring_vertices,
        ring_offsets=ring_offsets,
        ring_mins=ring_mins,
        ring_maxs=ring_maxs,
    )
    inv_r2 = 1.0 / (r * r)
    field2 = _evaluate_field_grid_numba(
        xs.astype(np.float64, copy=False),
        ys.astype(np.float64, copy=False),
        ring_vertices,
        ring_offsets,
        inside_mask,
        float(inv_r2),
    )

    loops_xy = marching_squares_loops(
        field2,
        origin_x=grid.origin_x,
        origin_y=grid.origin_y,
        pitch=pitch,
        level=level,
    )

    if output_s == "exterior":
        loops_xy = _filter_exterior_loops(
            loops_xy,
            field=field2,
            x0=float(xs[0]),
            y0=float(ys[0]),
            pitch=float(pitch),
            level=float(level),
        )

    out_lines: list[np.ndarray] = []
    for pts_xy in loops_xy:
        v3 = np.zeros((pts_xy.shape[0], 3), dtype=np.float64)
        v3[:, 0:2] = pts_xy
        out = frame.to_world(v3).astype(np.float32, copy=False)
        out_lines.append(out)

    if keep_original:
        for i in range(int(offsets.size) - 1):
            s = int(offsets[i])
            e = int(offsets[i + 1])
            original = coords[s:e]
            if original.shape[0] > 0:
                out_lines.append(original.astype(np.float32, copy=False))

    return pack_polylines(out_lines)
