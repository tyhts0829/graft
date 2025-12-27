"""
被切り抜きポリライン列を、閉曲線マスクの内側/外側だけにクリップする effect。

入力:
- inputs[0]: 被切り抜き（開いたポリライン列を想定）
- inputs[1]: マスク（閉ループ列）

処理:
- マスクの代表リングから姿勢（平面）を推定し、両入力を XY 平面へ整列して 2D クリップする。
- 結果のポリラインを元の姿勢へ戻して出力する。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
import pyclipper  # type: ignore[import-not-found, import-untyped]

from grafix.core.effect_registry import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import RealizedGeometry

from .util import transform_back, transform_to_xy_plane

clip_meta = {
    "mode": ParamMeta(kind="choice", choices=("inside", "outside")),
    "draw_outline": ParamMeta(kind="bool"),
}

_PLANAR_EPS_ABS = 1e-6
_PLANAR_EPS_REL = 1e-5


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _planarity_threshold(points: np.ndarray) -> float:
    if points.size == 0:
        return float(_PLANAR_EPS_ABS)
    p = points.astype(np.float64, copy=False)
    mins = np.min(p, axis=0)
    maxs = np.max(p, axis=0)
    diag = float(np.linalg.norm(maxs - mins))
    return max(float(_PLANAR_EPS_ABS), float(_PLANAR_EPS_REL) * diag)


def _apply_alignment(
    coords: np.ndarray, rotation_matrix: np.ndarray, z_offset: float
) -> np.ndarray:
    aligned = coords.astype(np.float64, copy=False) @ rotation_matrix.T
    aligned[:, 2] -= float(z_offset)
    return aligned


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


def _lines_to_realized_geometry(lines: Sequence[np.ndarray]) -> RealizedGeometry:
    if not lines:
        return _empty_geometry()

    coords_list: list[np.ndarray] = []
    offsets = np.zeros((len(lines) + 1,), dtype=np.int32)
    cursor = 0
    for i, line in enumerate(lines):
        v = np.asarray(line)
        if v.ndim != 2 or v.shape[1] != 3:
            raise ValueError("clip: polyline は shape (N,3) が必要")
        coords_list.append(v.astype(np.float32, copy=False))
        cursor += int(v.shape[0])
        offsets[i + 1] = cursor

    coords = (
        np.concatenate(coords_list, axis=0)
        if coords_list
        else np.zeros((0, 3), np.float32)
    )
    return RealizedGeometry(coords=coords, offsets=offsets)


def _pick_representative_ring(mask: RealizedGeometry) -> np.ndarray | None:
    coords = mask.coords
    offsets = mask.offsets
    for i in range(int(offsets.size) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        if e - s >= 3:
            return coords[s:e]
    return None


@effect(meta=clip_meta, n_inputs=2)
def clip(
    inputs: Sequence[RealizedGeometry],
    *,
    mode: str = "inside",  # "inside" | "outside"
    draw_outline: bool = False,
) -> RealizedGeometry:
    """XY 平面へ整列した上で、閉曲線マスクで線分列をクリップする。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        `inputs[0]` が被切り抜き、`inputs[1]` が閉曲線マスク。
    mode : str, default "inside"
        `"inside"` はマスク内側だけ残す。`"outside"` は外側だけ残す。
    draw_outline : bool, default False
        True のとき、マスク輪郭を追加で出力に含める。

    Returns
    -------
    RealizedGeometry
        クリップ後の実体ジオメトリ。
    """
    scale_i = 1000
    draw_outline_b = bool(draw_outline)
    if not inputs:
        return _empty_geometry()
    if len(inputs) < 2:
        return inputs[0]

    base = inputs[0]
    mask = inputs[1]
    if base.coords.shape[0] == 0:
        return base
    if mask.coords.shape[0] == 0:
        return base

    rep = _pick_representative_ring(mask)
    if rep is None:
        return base

    _rep_aligned, rotation_matrix, z_offset = transform_to_xy_plane(rep)

    aligned_base = _apply_alignment(base.coords, rotation_matrix, z_offset)
    aligned_mask = _apply_alignment(mask.coords, rotation_matrix, z_offset)

    threshold = _planarity_threshold(rep)
    if float(np.max(np.abs(aligned_mask[:, 2]))) > threshold:
        return base
    if float(np.max(np.abs(aligned_base[:, 2]))) > threshold:
        return base

    mode_s = str(mode)
    if mode_s not in {"inside", "outside"}:
        return base

    subject_paths: list[list[tuple[int, int]]] = []
    for i in range(int(base.offsets.size) - 1):
        s = int(base.offsets[i])
        e = int(base.offsets[i + 1])
        path = _to_int_path_open(aligned_base[s:e, 0:2], scale_i)
        if path is not None:
            subject_paths.append(path)

    clip_paths: list[list[tuple[int, int]]] = []
    for i in range(int(mask.offsets.size) - 1):
        s = int(mask.offsets[i])
        e = int(mask.offsets[i + 1])
        path = _to_int_path_ring(aligned_mask[s:e, 0:2], scale_i)
        if path is not None:
            clip_paths.append(path)

    if not clip_paths:
        return base
    outline_lines: list[np.ndarray] = []
    if draw_outline_b:
        for ring in clip_paths:
            if len(ring) < 3:
                continue
            xy = np.asarray(ring + [ring[0]], dtype=np.float64) / float(scale_i)
            v = np.zeros((xy.shape[0], 3), dtype=np.float64)
            v[:, 0:2] = xy
            restored = transform_back(v, rotation_matrix, float(z_offset))
            outline_lines.append(restored)

    if not subject_paths:
        if outline_lines:
            return _lines_to_realized_geometry(outline_lines)
        return base

    pc = pyclipper.Pyclipper()  # type: ignore[attr-defined]
    pc.AddPaths(subject_paths, pyclipper.PT_SUBJECT, False)  # type: ignore[attr-defined]
    pc.AddPaths(clip_paths, pyclipper.PT_CLIP, True)  # type: ignore[attr-defined]

    cliptype = (
        pyclipper.CT_INTERSECTION if mode_s == "inside" else pyclipper.CT_DIFFERENCE  # type: ignore[attr-defined]
    )
    polytree = pc.Execute2(cliptype, pyclipper.PFT_EVENODD, pyclipper.PFT_EVENODD)  # type: ignore[attr-defined]
    out_paths = pyclipper.OpenPathsFromPolyTree(polytree)  # type: ignore[attr-defined]

    if not out_paths:
        if outline_lines:
            return _lines_to_realized_geometry(outline_lines)
        return _empty_geometry()

    out_lines: list[np.ndarray] = []
    for path in out_paths:
        if len(path) < 2:  # type: ignore
            continue
        xy = np.asarray(path, dtype=np.float64) / float(scale_i)
        v = np.zeros((xy.shape[0], 3), dtype=np.float64)
        v[:, 0:2] = xy
        restored = transform_back(v, rotation_matrix, float(z_offset))
        out_lines.append(restored)

    out_lines.extend(outline_lines)
    if not out_lines:
        return _empty_geometry()
    return _lines_to_realized_geometry(out_lines)
