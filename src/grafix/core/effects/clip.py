"""
Purpose:
    baseの開polylineを、別入力の平面閉領域の内側または外側へ切り分ける。
Use when:
    二入力clip、maskの穴、coplanarity、またはoutline追加の挙動を変更する場合。
Constraints:
    - 第1入力を被clip線、第2入力をframeとeven-odd領域を定めるmaskとして扱い、役割を逆転しない。
    - baseとmaskが同一平面にない、または有効な閉maskを得られない場合はbaseを変更しない。
    - clip結果はopen pathとして保ち、draw_outlineだけが閉mask輪郭を追加する。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import pyclipper  # type: ignore[import-not-found, import-untyped]

from grafix.core.operation_authoring import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import GeomTuple

from grafix.core.geometry_kernels.packed import (
    empty_packed_geometry,
    pack_polylines,
)
from grafix.core.geometry_kernels.planar import (
    PlanarFrame,
    planarity_threshold,
)

# `ndarray.tolist()` は各座標を Python object 化する。1 vertex を 384 bytes、
# 1 line を 256 bytes と保守的に見積もっても、list/tuple/int と量子化配列の
# 追加 peak が約 7 MiB に収まる上限にする。
_BATCH_PATH_MAX_TOTAL_VERTICES = 16_384
_BATCH_PATH_MAX_TOTAL_LINES = 4_096
_MODE_CHOICES = ("inside", "outside")

clip_meta = {
    "mode": ParamMeta(
        kind="choice",
        choices=_MODE_CHOICES,
        description="マスクの内側と外側のどちらを残すか選ぶ。",
    ),
    "draw_outline": ParamMeta(
        kind="bool",
        description="クリップ結果にマスク輪郭を加えて出力する。",
    ),
}


def _remove_consecutive_duplicates(
    path: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    if len(path) < 2:
        return path
    out = [path[0]]
    for pt in path[1:]:
        if pt != out[-1]:
            out.append(pt)
    return out


def _to_int_path_open(xy: np.ndarray, scale: int) -> list[tuple[int, int]] | None:
    if xy.shape[0] < 2:
        return None
    scaled = np.rint(xy.astype(np.float64, copy=False) * float(scale)).astype(
        np.int64, copy=False
    )
    path = [(int(p[0]), int(p[1])) for p in scaled]
    path = _remove_consecutive_duplicates(path)
    if len(path) < 2:
        return None
    if path[0] == path[-1]:
        path = path[:-1]
    return path if len(path) >= 2 else None


def _to_int_path_ring(xy: np.ndarray, scale: int) -> list[tuple[int, int]] | None:
    if xy.shape[0] < 3:
        return None
    scaled = np.rint(xy.astype(np.float64, copy=False) * float(scale)).astype(
        np.int64, copy=False
    )
    path = [(int(p[0]), int(p[1])) for p in scaled]
    path = _remove_consecutive_duplicates(path)
    if len(path) < 3:
        return None
    if path[0] == path[-1]:
        path = path[:-1]
    return path if len(path) >= 3 else None


def _can_batch_quantize(xy: np.ndarray, scale: int) -> bool:
    # Clipper の整数座標域に収まる値だけを一括量子化する。
    with np.errstate(all="ignore"):
        minimum = float(np.min(xy))
        maximum = float(np.max(xy))
    if not math.isfinite(minimum) or not math.isfinite(maximum):
        return False
    max_abs = max(abs(minimum), abs(maximum))
    return max_abs <= float(1 << 61) / float(scale)


def _int_paths_from_scaled(
    scaled: np.ndarray,
    offsets: np.ndarray,
    *,
    min_vertices: int,
) -> list[list[tuple[int, int]]]:
    """量子化済み packed XY を入力順の Clipper path に変換する。"""

    points = scaled.tolist()
    bounds = offsets.tolist()
    paths: list[list[tuple[int, int]]] = []
    for start, stop in zip(bounds, bounds[1:]):
        if stop - start < min_vertices:
            continue
        path: list[tuple[int, int]] = []
        previous: tuple[int, int] | None = None
        for index in range(start, stop):
            values = points[index]
            point = (values[0], values[1])
            if point != previous:
                path.append(point)
                previous = point
        if len(path) < min_vertices:
            continue
        if path[0] == path[-1]:
            path = path[:-1]
        if len(path) >= min_vertices:
            paths.append(path)
    return paths


def _restore_and_pack_int_paths(
    paths: Sequence[Sequence[Sequence[int]]],
    *,
    frame: PlanarFrame,
    scale: int,
) -> GeomTuple:
    """Clipper path を一括して world 座標の packed geometry へ戻す。"""

    valid_paths = tuple(path for path in paths if len(path) >= 2)
    if not valid_paths:
        return empty_packed_geometry()

    counts = np.fromiter(
        (len(path) for path in valid_paths),
        dtype=np.int64,
        count=len(valid_paths),
    )
    offsets64 = np.empty((len(valid_paths) + 1,), dtype=np.int64)
    offsets64[0] = 0
    np.cumsum(counts, out=offsets64[1:])
    total_vertices = int(offsets64[-1])
    if total_vertices > int(np.iinfo(np.int32).max):
        raise ValueError("packed geometry の頂点数が int32 上限を超えている")

    local = np.zeros((total_vertices, 3), dtype=np.float64)
    if bool(np.all(counts == counts[0])):
        local[:, 0:2] = np.asarray(valid_paths, dtype=np.float64).reshape(
            total_vertices, 2
        )
    else:
        for index, path in enumerate(valid_paths):
            start = int(offsets64[index])
            stop = int(offsets64[index + 1])
            local[start:stop, 0:2] = np.asarray(path, dtype=np.float64)
    local[:, 0:2] /= float(scale)

    restored = frame.to_world(local)
    return restored.astype(np.float32, copy=False), offsets64.astype(np.int32)


@effect(meta=clip_meta, n_inputs=2)
def clip(
    base: GeomTuple,
    mask: GeomTuple,
    *,
    mode: str = "inside",  # "inside" | "outside"
    draw_outline: bool = False,
) -> GeomTuple:
    """XY 平面へ整列した上で、閉曲線マスクで線分列をクリップする。

    Parameters
    ----------
    base : tuple[np.ndarray, np.ndarray]
        被切り抜き対象（coords, offsets）。
    mask : tuple[np.ndarray, np.ndarray]
        閉曲線マスク（coords, offsets）。
    mode : str, default "inside"
        `"inside"` はマスク内側だけ残す。`"outside"` は外側だけ残す。
    draw_outline : bool, default False
        True のとき、マスク輪郭を追加で出力に含める。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        クリップ後の実体ジオメトリ（coords, offsets）。
    """
    scale_i = 1000
    base_coords, base_offsets = base
    mask_coords, mask_offsets = mask
    if base_coords.shape[0] == 0:
        return base_coords, base_offsets
    if mask_coords.shape[0] == 0:
        return base_coords, base_offsets

    frame = PlanarFrame.from_points(mask_coords, mask_offsets)
    threshold = planarity_threshold(mask_coords)
    if not frame.is_planar(threshold):
        return base_coords, base_offsets

    aligned_base = frame.to_local(base_coords)
    aligned_mask = frame.to_local(mask_coords)

    if float(np.max(np.abs(aligned_base[:, 2]))) > threshold:
        return base_coords, base_offsets

    # 少数 path は per-line 処理の方が速いため、多数 path だけ一括化する。
    many_paths = int(base_offsets.size) + int(mask_offsets.size) >= 34
    total_input_vertices = int(base_coords.shape[0]) + int(mask_coords.shape[0])
    total_input_lines = max(0, int(base_offsets.size) - 1) + max(
        0,
        int(mask_offsets.size) - 1,
    )
    bounded_batch_input = (
        total_input_vertices <= _BATCH_PATH_MAX_TOTAL_VERTICES
        and total_input_lines <= _BATCH_PATH_MAX_TOTAL_LINES
    )
    batch_candidate = many_paths and bounded_batch_input
    batch_paths = batch_candidate and (
        _can_batch_quantize(aligned_base[:, 0:2], scale_i)
        and _can_batch_quantize(aligned_mask[:, 0:2], scale_i)
    )
    if batch_paths:
        scaled_base = np.rint(aligned_base[:, 0:2] * float(scale_i)).astype(
            np.int64, copy=False
        )
        scaled_mask = np.rint(aligned_mask[:, 0:2] * float(scale_i)).astype(
            np.int64, copy=False
        )
        subject_paths = _int_paths_from_scaled(
            scaled_base,
            base_offsets,
            min_vertices=2,
        )
        clip_paths = _int_paths_from_scaled(
            scaled_mask,
            mask_offsets,
            min_vertices=3,
        )
    else:
        subject_paths = []
        for i in range(int(base_offsets.size) - 1):
            s = int(base_offsets[i])
            e = int(base_offsets[i + 1])
            path = _to_int_path_open(aligned_base[s:e, 0:2], scale_i)
            if path is not None:
                subject_paths.append(path)

        clip_paths = []
        for i in range(int(mask_offsets.size) - 1):
            s = int(mask_offsets[i])
            e = int(mask_offsets[i + 1])
            path = _to_int_path_ring(aligned_mask[s:e, 0:2], scale_i)
            if path is not None:
                clip_paths.append(path)

    if not clip_paths:
        return base_coords, base_offsets
    outline_lines: list[np.ndarray] = []
    if draw_outline:
        for ring in clip_paths:
            if len(ring) < 3:
                continue
            xy = np.asarray(ring + [ring[0]], dtype=np.float64) / float(scale_i)
            v = np.zeros((xy.shape[0], 3), dtype=np.float64)
            v[:, 0:2] = xy
            restored = frame.to_world(v)
            outline_lines.append(restored)

    if not subject_paths:
        if outline_lines:
            return pack_polylines(outline_lines)
        return base_coords, base_offsets

    pc = pyclipper.Pyclipper()  # type: ignore[attr-defined]
    pc.AddPaths(subject_paths, pyclipper.PT_SUBJECT, False)  # type: ignore[attr-defined]
    pc.AddPaths(clip_paths, pyclipper.PT_CLIP, True)  # type: ignore[attr-defined]

    cliptype = (
        pyclipper.CT_INTERSECTION if mode == "inside" else pyclipper.CT_DIFFERENCE  # type: ignore[attr-defined]
    )
    polytree = pc.Execute2(cliptype, pyclipper.PFT_EVENODD, pyclipper.PFT_EVENODD)  # type: ignore[attr-defined]
    out_paths = pyclipper.OpenPathsFromPolyTree(polytree)  # type: ignore[attr-defined]

    if not out_paths:
        if outline_lines:
            return pack_polylines(outline_lines)
        return empty_packed_geometry()

    if batch_paths and not draw_outline:
        return _restore_and_pack_int_paths(
            out_paths,
            frame=frame,
            scale=scale_i,
        )

    out_lines: list[np.ndarray] = []
    for path in out_paths:
        if len(path) < 2:  # type: ignore
            continue
        xy = np.asarray(path, dtype=np.float64) / float(scale_i)
        v = np.zeros((xy.shape[0], 3), dtype=np.float64)
        v[:, 0:2] = xy
        restored = frame.to_world(v)
        out_lines.append(restored)

    out_lines.extend(outline_lines)
    if not out_lines:
        return empty_packed_geometry()
    return pack_polylines(out_lines)
