"""二つの閉曲線群を平面領域として結合・交差・差分・排他的論理和する effect。"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pyclipper  # type: ignore[import-not-found, import-untyped]

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

_CLIPPER_SCALE = 1000
_CLIPPER_COORD_LIMIT = 1 << 61
_CLOSE_DISTANCE_EPS = 0.01

boolean_meta = {
    "mode": ParamMeta(
        kind="choice",
        choices=("union", "intersection", "difference", "xor"),
        description="二つの閉曲線領域へ適用する集合演算を選ぶ。",
    ),
}


def _closed_world_lines(g: GeomTuple, *, label: str) -> list[np.ndarray]:
    """入力から厳密に検証した明示閉鎖 ring を抽出する。"""

    coords, offsets = g
    lines: list[np.ndarray] = []
    for line_index in range(int(offsets.size) - 1):
        start = int(offsets[line_index])
        stop = int(offsets[line_index + 1])
        line = coords[start:stop]
        if line.shape[0] == 0:
            continue
        if line.shape[0] < 4:
            raise ValueError(
                f"boolean: {label} の line {line_index} は "
                "3 個以上の固有頂点を持つ閉曲線である必要がある"
            )

        endpoint_distance = float(
            np.linalg.norm(
                line[0].astype(np.float64, copy=False)
                - line[-1].astype(np.float64, copy=False)
            )
        )
        if endpoint_distance > _CLOSE_DISTANCE_EPS:
            raise ValueError(
                f"boolean: {label} の line {line_index} は閉じていない"
            )

        closed = line.astype(np.float64, copy=True)
        closed[-1] = closed[0]
        if np.unique(closed[:-1], axis=0).shape[0] < 3:
            raise ValueError(
                f"boolean: {label} の line {line_index} は "
                "3 個以上の固有頂点を持つ必要がある"
            )
        lines.append(closed)
    return lines


def _pack_frame_input(
    first: Sequence[np.ndarray],
    second: Sequence[np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    lines = (*first, *second)
    total_vertices = sum(int(line.shape[0]) for line in lines)
    coords = np.empty((total_vertices, 3), dtype=np.float64)
    offsets = np.empty((len(lines) + 1,), dtype=np.int32)
    offsets[0] = 0

    cursor = 0
    for index, line in enumerate(lines):
        next_cursor = cursor + int(line.shape[0])
        coords[cursor:next_cursor] = line
        offsets[index + 1] = next_cursor
        cursor = next_cursor
    return coords, offsets


def _remove_consecutive_duplicates(
    path: Sequence[Sequence[int]] | np.ndarray,
) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for raw_point in path:
        point = (int(raw_point[0]), int(raw_point[1]))
        if not result or point != result[-1]:
            result.append(point)
    if len(result) > 1 and result[0] == result[-1]:
        result.pop()
    return result


def _signed_area_twice(path: Sequence[tuple[int, int]]) -> int:
    area = 0
    for index, point in enumerate(path):
        following = path[(index + 1) % len(path)]
        area += point[0] * following[1] - following[0] * point[1]
    return int(area)


def _to_clipper_paths(
    local: np.ndarray,
    offsets: np.ndarray,
    *,
    label: str,
) -> list[list[tuple[int, int]]]:
    xy = local[:, :2]
    maximum = float(np.max(np.abs(xy))) if xy.size else 0.0
    if not np.isfinite(maximum) or maximum * _CLIPPER_SCALE >= _CLIPPER_COORD_LIMIT:
        raise ValueError(f"boolean: {label} の座標が Clipper の表現範囲を超えている")

    scaled = np.rint(xy * float(_CLIPPER_SCALE)).astype(np.int64, copy=False)
    paths: list[list[tuple[int, int]]] = []
    for line_index in range(int(offsets.size) - 1):
        start = int(offsets[line_index])
        stop = int(offsets[line_index + 1])
        path = _remove_consecutive_duplicates(scaled[start:stop])
        if len(path) < 3 or len(set(path)) < 3 or _signed_area_twice(path) == 0:
            raise ValueError(
                f"boolean: {label} の line {line_index} は "
                "量子化後に面積を持つ閉曲線である必要がある"
            )
        paths.append(path)
    return paths


def _execute_polytree(
    subject_paths: Sequence[Sequence[tuple[int, int]]],
    clip_paths: Sequence[Sequence[tuple[int, int]]],
    *,
    clip_type: int,
) -> pyclipper.PyPolyNode:
    clipper = pyclipper.Pyclipper()  # type: ignore[attr-defined]
    if subject_paths:
        clipper.AddPaths(subject_paths, pyclipper.PT_SUBJECT, True)  # type: ignore[attr-defined]
    if clip_paths:
        clipper.AddPaths(clip_paths, pyclipper.PT_CLIP, True)  # type: ignore[attr-defined]
    return clipper.Execute2(  # type: ignore[no-any-return]
        clip_type,
        pyclipper.PFT_EVENODD,  # type: ignore[attr-defined]
        pyclipper.PFT_EVENODD,  # type: ignore[attr-defined]
    )


def _canonical_ring(
    contour: Sequence[Sequence[int]],
    *,
    is_hole: bool,
) -> tuple[tuple[int, int], ...]:
    path = _remove_consecutive_duplicates(contour)
    if len(path) < 3:
        return ()

    area = _signed_area_twice(path)
    should_be_positive = not bool(is_hole)
    if (area > 0) != should_be_positive:
        path.reverse()

    seam = min(range(len(path)), key=lambda index: (path[index], index))
    rotated = path[seam:] + path[:seam]
    return tuple(rotated)


def _canonical_tree_paths(
    root: pyclipper.PyPolyNode,
) -> list[tuple[tuple[int, int], ...]]:
    """PolyTree を親優先かつ sibling 間で決定的な ring 列へ変換する。"""

    def visit(node: pyclipper.PyPolyNode) -> tuple[
        tuple[int, tuple[tuple[int, int], ...]],
        list[tuple[tuple[int, int], ...]],
    ]:
        ring = _canonical_ring(node.Contour, is_hole=bool(node.IsHole))
        children = [visit(child) for child in node.Childs]
        children.sort(key=lambda item: item[0])

        flattened: list[tuple[tuple[int, int], ...]] = []
        if ring:
            flattened.append(ring)
        for _key, child_paths in children:
            flattened.extend(child_paths)
        key = (-abs(_signed_area_twice(ring)) if ring else 0, ring)
        return key, flattened

    children = [visit(child) for child in root.Childs]
    children.sort(key=lambda item: item[0])
    result: list[tuple[tuple[int, int], ...]] = []
    for _key, paths in children:
        result.extend(paths)
    return result


def _restore_rings(
    paths: Sequence[Sequence[tuple[int, int]]],
    *,
    frame: PlanarFrame,
) -> GeomTuple:
    if not paths:
        return empty_packed_geometry()

    counts = np.fromiter(
        (len(path) + 1 for path in paths),
        dtype=np.int64,
        count=len(paths),
    )
    total_vertices = int(np.sum(counts, dtype=np.int64))
    ensure_geometry_output(
        "boolean",
        vertices=total_vertices,
        lines=len(paths),
        scratch_bytes=total_vertices * 3 * 8 * 2,
        hint="入力 ring の複雑さを減らしてください",
    )

    local = np.zeros((total_vertices, 3), dtype=np.float64)
    offsets64 = np.empty((len(paths) + 1,), dtype=np.int64)
    offsets64[0] = 0
    cursor = 0
    for index, path in enumerate(paths):
        vertices = np.asarray((*path, path[0]), dtype=np.float64)
        next_cursor = cursor + int(vertices.shape[0])
        local[cursor:next_cursor, :2] = vertices / float(_CLIPPER_SCALE)
        offsets64[index + 1] = next_cursor
        cursor = next_cursor

    restored = frame.to_world(local)
    return restored.astype(np.float32, copy=False), offsets64.astype(np.int32)


@effect(meta=boolean_meta, n_inputs=2)
def boolean(
    a: GeomTuple,
    b: GeomTuple,
    *,
    mode: str = "union",  # "union" | "intersection" | "difference" | "xor"
) -> GeomTuple:
    """同一平面上の閉曲線群へ even-odd 規則の Boolean 演算を適用する。

    Parameters
    ----------
    a : tuple[np.ndarray, np.ndarray]
        第 1 入力の閉曲線群（coords, offsets）。
    b : tuple[np.ndarray, np.ndarray]
        第 2 入力の閉曲線群（coords, offsets）。
    mode : str, default "union"
        `"union"`、`"intersection"`、`"difference"`、`"xor"` のいずれか。
        `"difference"` は第 1 入力から第 2 入力を引く。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        集合演算後の明示的に閉じた ring 列。

    Notes
    -----
    - 各入力は端点距離 0.01 以下、かつ 3 個以上の固有頂点を持つ必要がある。
    - winding にかかわらず、各入力の ring 群を even-odd 領域として解釈する。
    - 二入力 effect のため、lazy API では effect chain の先頭で使用する。
    """

    mode_s = mode
    clip_types = {
        "union": pyclipper.CT_UNION,  # type: ignore[attr-defined]
        "intersection": pyclipper.CT_INTERSECTION,  # type: ignore[attr-defined]
        "difference": pyclipper.CT_DIFFERENCE,  # type: ignore[attr-defined]
        "xor": pyclipper.CT_XOR,  # type: ignore[attr-defined]
    }
    if mode_s not in clip_types:
        raise ValueError(f"boolean: 未知の mode: {mode_s!r}")

    first_lines = _closed_world_lines(a, label="第 1 入力")
    second_lines = _closed_world_lines(b, label="第 2 入力")
    if not first_lines and not second_lines:
        return empty_packed_geometry()

    frame_coords, frame_offsets = _pack_frame_input(first_lines, second_lines)
    frame = canonical_planar_frame(frame_coords, frame_offsets)
    threshold = planarity_threshold(frame_coords)
    if not frame.is_planar(threshold):
        raise ValueError(
            "boolean: 二入力は同一の有限な平面上にある必要がある"
            f"（status={frame.status}, residual={frame.residual:.6g}）"
        )

    local = frame.to_local(frame_coords)
    split = sum(int(line.shape[0]) for line in first_lines)
    first_offsets = frame_offsets[: len(first_lines) + 1]
    second_offsets = frame_offsets[len(first_lines) :] - split
    first_paths = _to_clipper_paths(
        local[:split],
        first_offsets,
        label="第 1 入力",
    )
    second_paths = _to_clipper_paths(
        local[split:],
        second_offsets,
        label="第 2 入力",
    )

    if not first_paths:
        if mode_s in {"intersection", "difference"}:
            return empty_packed_geometry()
        tree = _execute_polytree(
            second_paths,
            (),
            clip_type=pyclipper.CT_UNION,  # type: ignore[attr-defined]
        )
    elif not second_paths:
        if mode_s == "intersection":
            return empty_packed_geometry()
        tree = _execute_polytree(
            first_paths,
            (),
            clip_type=pyclipper.CT_UNION,  # type: ignore[attr-defined]
        )
    else:
        tree = _execute_polytree(
            first_paths,
            second_paths,
            clip_type=clip_types[mode_s],
        )

    return _restore_rings(_canonical_tree_paths(tree), frame=frame)


__all__ = ["boolean", "boolean_meta"]
