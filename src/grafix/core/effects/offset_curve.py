"""
Purpose:
    平面polylineの進行方向を基準に、左右へ一定距離の平行曲線群を生成する。
Use when:
    line bufferではなく片側offset、複数level、またはopen/closed joinを扱う場合。
Constraints:
    - left/rightを入力方向で定義し、closed ringではwindingとの関係を維持する。
    - 全入力を同一の有限平面に置き、linear入力には決定的なprincipal planeを使う。
    - open/closed状態を出力へ保ち、反復GEOS処理と出力量を確保前にbudget検査する。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry  # type: ignore[import-not-found, import-untyped]

from grafix.core.operation_authoring import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import GeomTuple
from grafix.core.resource_budget import ensure_geometry_output

from grafix.core.geometry_kernels.packed import empty_packed_geometry
from grafix.core.geometry_kernels.planar import (
    PlanarFrame,
    canonical_planar_frame,
    planarity_threshold,
)
from grafix.core.geometry_kernels.resample import RESAMPLE_CLOSED_DISTANCE_EPS

_QUAD_SEGS = 16
_MITRE_LIMIT = 5.0
_POINT_EPS = 1e-12

offset_curve_meta = {
    "distance": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=25.0,
        description="隣り合う平行曲線どうしの距離。",
    ),
    "side": ParamMeta(
        kind="choice",
        choices=("left", "right", "both"),
        description="入力ポリラインの進行方向を基準に生成する側を選ぶ。",
    ),
    "count": ParamMeta(
        kind="int",
        ui_min=1,
        ui_max=20,
        description="各側へ距離の整数倍で生成する平行曲線の本数。",
    ),
    "join": ParamMeta(
        kind="choice",
        choices=("round", "mitre", "bevel"),
        description="平行曲線の角を接続する形状を選ぶ。",
    ),
    "keep_original": ParamMeta(
        kind="bool",
        description="生成した平行曲線の後ろへ元のポリラインを追加する。",
    ),
}


def _remove_consecutive_duplicates(points: np.ndarray) -> np.ndarray:
    if points.shape[0] < 2:
        return points
    delta = np.diff(points, axis=0)
    keep = np.ones((points.shape[0],), dtype=bool)
    keep[1:] = np.sum(delta * delta, axis=1) > _POINT_EPS * _POINT_EPS
    return points[keep]


def _signed_area(points: np.ndarray) -> float:
    if points.shape[0] < 3:
        return 0.0
    following = np.roll(points, -1, axis=0)
    return 0.5 * float(
        np.sum(
            points[:, 0] * following[:, 1]
            - following[:, 0] * points[:, 1],
            dtype=np.float64,
        )
    )


def _prepare_source_line(
    line: np.ndarray,
    *,
    closed: bool,
) -> np.ndarray | None:
    clean = _remove_consecutive_duplicates(line.astype(np.float64, copy=False))
    if clean.shape[0] < 2:
        return None
    if not closed:
        return clean

    if float(np.linalg.norm(clean[0] - clean[-1])) <= RESAMPLE_CLOSED_DISTANCE_EPS:
        clean = clean[:-1]
    if clean.shape[0] < 3:
        return None

    # 閉曲線の seam を線分の中点へ置く。GEOS が seam の join を省略しても、
    # 最後に補う closure が元線分と平行な正しい offset segment になる。
    edge_index = -1
    for index in range(clean.shape[0]):
        following = (index + 1) % clean.shape[0]
        if float(np.linalg.norm(clean[following] - clean[index])) > _POINT_EPS:
            edge_index = index
            break
    if edge_index < 0:
        return None

    following = (edge_index + 1) % clean.shape[0]
    midpoint = 0.5 * (clean[edge_index] + clean[following])
    order = [
        *((following + step) % clean.shape[0] for step in range(clean.shape[0])),
    ]
    core = clean[np.asarray(order, dtype=np.intp)]
    return np.concatenate((midpoint[None, :], core, midpoint[None, :]), axis=0)


def _extract_line_arrays(geometry: BaseGeometry) -> list[np.ndarray]:
    if bool(geometry.is_empty):
        return []
    geometry_type = str(geometry.geom_type)
    if geometry_type in {"LineString", "LinearRing"}:
        points = np.asarray(geometry.coords, dtype=np.float64)
        return [points] if points.shape[0] >= 2 else []

    children = getattr(geometry, "geoms", None)
    if children is None:
        return []
    result: list[np.ndarray] = []
    for child in children:
        result.extend(_extract_line_arrays(child))
    return result


def _path_position(point: np.ndarray, source: np.ndarray) -> float:
    segments = np.diff(source, axis=0)
    lengths = np.linalg.norm(segments, axis=1)
    cumulative = 0.0
    best_distance = float("inf")
    best_position = 0.0
    for index, (segment, length) in enumerate(zip(segments, lengths)):
        length_f = float(length)
        if length_f <= _POINT_EPS:
            continue
        ratio = float(np.dot(point - source[index], segment)) / (length_f * length_f)
        ratio = min(1.0, max(0.0, ratio))
        projected = source[index] + ratio * segment
        distance = float(np.dot(point - projected, point - projected))
        position = cumulative + ratio * length_f
        if distance < best_distance or (
            distance == best_distance and position < best_position
        ):
            best_distance = distance
            best_position = position
        cumulative += length_f
    return best_position


def _normalize_open_fragment(
    points: np.ndarray,
    *,
    source: np.ndarray,
) -> tuple[np.ndarray, float] | None:
    clean = _remove_consecutive_duplicates(points)
    if clean.shape[0] < 2:
        return None

    start_position = _path_position(clean[0], source)
    end_position = _path_position(clean[-1], source)
    if start_position > end_position:
        clean = clean[::-1].copy()
        start_position, end_position = end_position, start_position
    elif start_position == end_position:
        forward_cost = float(
            np.sum((clean[0] - source[0]) ** 2)
            + np.sum((clean[-1] - source[-1]) ** 2)
        )
        reverse_cost = float(
            np.sum((clean[-1] - source[0]) ** 2)
            + np.sum((clean[0] - source[-1]) ** 2)
        )
        if reverse_cost < forward_cost:
            clean = clean[::-1].copy()
    return clean, start_position


def _normalize_closed_fragment(
    points: np.ndarray,
    *,
    source_area: float,
) -> np.ndarray | None:
    clean = _remove_consecutive_duplicates(points)
    if clean.shape[0] > 1 and float(np.linalg.norm(clean[0] - clean[-1])) <= _POINT_EPS:
        clean = clean[:-1]
    if clean.shape[0] < 3:
        return None

    area = _signed_area(clean)
    if area == 0.0:
        return None
    if (area > 0.0) != (source_area > 0.0):
        clean = clean[::-1].copy()

    seam = min(
        range(clean.shape[0]),
        key=lambda index: (float(clean[index, 0]), float(clean[index, 1]), index),
    )
    clean = np.concatenate((clean[seam:], clean[:seam]), axis=0)
    return np.concatenate((clean, clean[:1]), axis=0)


def _fragment_key(points: np.ndarray, *, start_position: float) -> tuple[object, ...]:
    mins = np.min(points, axis=0)
    maxs = np.max(points, axis=0)
    return (
        float(start_position),
        float(mins[0]),
        float(mins[1]),
        float(maxs[0]),
        float(maxs[1]),
        tuple((float(point[0]), float(point[1])) for point in points),
    )


def _normalized_fragments(
    geometry: BaseGeometry,
    *,
    source: np.ndarray,
    closed: bool,
) -> list[np.ndarray]:
    candidates = _extract_line_arrays(geometry)
    normalized: list[tuple[tuple[object, ...], np.ndarray]] = []
    if closed:
        source_core = source[:-1]
        source_area = _signed_area(source_core)
        if source_area == 0.0:
            return []
        for candidate in candidates:
            fragment = _normalize_closed_fragment(
                candidate,
                source_area=source_area,
            )
            if fragment is None:
                continue
            normalized.append(
                (_fragment_key(fragment, start_position=0.0), fragment)
            )
    else:
        for candidate in candidates:
            result = _normalize_open_fragment(candidate, source=source)
            if result is None:
                continue
            fragment, start_position = result
            normalized.append(
                (
                    _fragment_key(fragment, start_position=start_position),
                    fragment,
                )
            )
    normalized.sort(key=lambda item: item[0])
    return [fragment for _key, fragment in normalized]


def _pack_output(
    generated: list[np.ndarray],
    originals: list[np.ndarray],
    *,
    frame: PlanarFrame,
) -> GeomTuple:
    line_count = len(generated) + len(originals)
    if line_count == 0:
        return empty_packed_geometry()

    total_vertices = sum(int(line.shape[0]) for line in generated) + sum(
        int(line.shape[0]) for line in originals
    )
    _ensure_offset_output(
        generated_vertices=sum(int(line.shape[0]) for line in generated),
        generated_lines=len(generated),
        original_vertices=sum(int(line.shape[0]) for line in originals),
        original_lines=len(originals),
    )

    coords = np.empty((total_vertices, 3), dtype=np.float32)
    offsets = np.empty((line_count + 1,), dtype=np.int32)
    offsets[0] = 0
    cursor = 0
    line_index = 0
    for points in generated:
        local = np.zeros((points.shape[0], 3), dtype=np.float64)
        local[:, :2] = points
        restored = frame.to_world(local)
        next_cursor = cursor + int(points.shape[0])
        coords[cursor:next_cursor] = restored
        cursor = next_cursor
        line_index += 1
        offsets[line_index] = cursor
    for points in originals:
        next_cursor = cursor + int(points.shape[0])
        coords[cursor:next_cursor] = points
        cursor = next_cursor
        line_index += 1
        offsets[line_index] = cursor
    return coords, offsets


def _ensure_offset_output(
    *,
    generated_vertices: int,
    generated_lines: int,
    original_vertices: int,
    original_lines: int,
) -> None:
    """現在までの生成結果を保持・packできるか即時に検査する。"""

    ensure_geometry_output(
        "offset_curve",
        vertices=int(generated_vertices) + int(original_vertices),
        lines=int(generated_lines) + int(original_lines),
        scratch_bytes=int(generated_vertices) * 3 * 8 * 2,
        hint="count、distance、または入力 geometry の複雑さを減らしてください",
    )


@effect(meta=offset_curve_meta)
def offset_curve(
    g: GeomTuple,
    *,
    distance: float = 1.0,
    side: str = "both",  # "left" | "right" | "both"
    count: int = 1,
    join: str = "round",  # "round" | "mitre" | "bevel"
    keep_original: bool = False,
) -> GeomTuple:
    """平面ポリラインの入力方向を基準に平行曲線を生成する。

    Parameters
    ----------
    g : tuple[np.ndarray, np.ndarray]
        入力実体ジオメトリ（coords, offsets）。
    distance : float, default 1.0
        隣り合う平行曲線どうしの正の有限な距離。
    side : str, default "both"
        入力方向を基準とする `"left"`、`"right"`、または `"both"`。
    count : int, default 1
        各側へ `distance` の整数倍で生成する正の本数。
    join : str, default "round"
        角の接続形状。`"round"`、`"mitre"`、`"bevel"` のいずれか。
    keep_original : bool, default False
        True のとき、生成結果の後ろへ元のポリラインを追加する。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        生成した平行曲線の packed geometry。

    Notes
    -----
    純粋な 3D 直線は平面が一意でないため、直線方向の最大絶対成分が正になる向きを
    local X とする。直線へ最も直交する world 軸を Z、Y、X の優先順で選び、
    最大絶対成分が正になる法線へ固定した principal plane を使う。閉曲線の
    left/right は入力 winding に対して定義し、出力を明示的に閉じる。
    """

    if distance <= 0.0:
        raise ValueError("offset_curve: distance は正の有限値である必要がある")
    if count <= 0:
        raise ValueError("offset_curve: count は正の整数である必要がある")
    maximum_distance = distance * count
    if not math.isfinite(maximum_distance):
        raise ValueError("offset_curve: distance * count は有限である必要がある")

    coords, offsets = g
    if coords.shape[0] == 0:
        return coords, offsets

    frame = canonical_planar_frame(coords, offsets, allow_linear=True)
    threshold = planarity_threshold(coords)
    if not frame.is_planar(threshold):
        raise ValueError(
            "offset_curve: 入力は有限な同一平面上にある必要がある"
            f"（status={frame.status}, residual={frame.residual:.6g}）"
        )
    local = frame.to_local(coords)

    from shapely.geometry import (  # type: ignore[import-not-found, import-untyped]
        LineString,
    )

    requested_sides = (
        ("left", "right")
        if side == "both"
        else (side,)
    )
    originals: list[np.ndarray] = []
    if keep_original:
        for line_index in range(int(offsets.size) - 1):
            start = int(offsets[line_index])
            stop = int(offsets[line_index + 1])
            if stop > start:
                originals.append(coords[start:stop])
    original_vertices = sum(int(line.shape[0]) for line in originals)
    if originals:
        _ensure_offset_output(
            generated_vertices=0,
            generated_lines=0,
            original_vertices=original_vertices,
            original_lines=len(originals),
        )

    prepared_sources: list[tuple[np.ndarray, bool]] = []
    for line_index in range(int(offsets.size) - 1):
        start = int(offsets[line_index])
        stop = int(offsets[line_index + 1])
        world_line = coords[start:stop]
        local_line = local[start:stop, :2]
        if local_line.shape[0] < 2:
            continue

        closed = (
            local_line.shape[0] >= 4
            and float(np.linalg.norm(world_line[0] - world_line[-1]))
            <= RESAMPLE_CLOSED_DISTANCE_EPS
        )
        source = _prepare_source_line(local_line, closed=closed)
        if source is not None:
            prepared_sources.append((source, closed))

    # GEOS が empty を返す自己交差線でも count 回の呼び出しが無制限に続かないよう、
    # 各試行が source と同程度の頂点列を返す保守的な work/output plan を先に検査する。
    attempt_multiplier = count * len(requested_sides)
    planned_lines = len(prepared_sources) * attempt_multiplier
    planned_vertices = (
        sum(int(source.shape[0]) for source, _closed in prepared_sources)
        * attempt_multiplier
    )
    _ensure_offset_output(
        generated_vertices=planned_vertices,
        generated_lines=planned_lines,
        original_vertices=original_vertices,
        original_lines=len(originals),
    )

    generated: list[np.ndarray] = []
    generated_vertices = 0
    for source, closed in prepared_sources:
        line_string = LineString(source)
        for level in range(1, count + 1):
            magnitude = distance * float(level)
            for selected_side in requested_sides:
                signed_distance = magnitude if selected_side == "left" else -magnitude
                offset = line_string.offset_curve(
                    signed_distance,
                    quad_segs=_QUAD_SEGS,
                    join_style=join,
                    mitre_limit=_MITRE_LIMIT,
                )
                fragments = _normalized_fragments(
                    offset,
                    source=source,
                    closed=closed,
                )
                fragment_vertices = sum(
                    int(fragment.shape[0]) for fragment in fragments
                )
                _ensure_offset_output(
                    generated_vertices=generated_vertices + fragment_vertices,
                    generated_lines=len(generated) + len(fragments),
                    original_vertices=original_vertices,
                    original_lines=len(originals),
                )
                generated.extend(fragments)
                generated_vertices += fragment_vertices

    return _pack_output(generated, originals, frame=frame)


__all__ = ["offset_curve", "offset_curve_meta"]
