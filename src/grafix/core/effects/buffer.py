"""ポリライン列を推定平面へ射影し、Shapely の buffer で輪郭を生成する effect。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from grafix.core.effect_registry import effect
from grafix.core.realized_geometry import RealizedGeometry
from grafix.core.parameters.meta import ParamMeta

buffer_meta = {
    "join": ParamMeta(kind="choice", choices=("mitre", "round", "bevel")),
    "distance": ParamMeta(kind="float", ui_min=-25.0, ui_max=25.0),
    "quad_segs": ParamMeta(kind="int", ui_min=1, ui_max=100),
    "keep_original": ParamMeta(kind="bool"),
}

_JOIN_STYLE_SET = {"mitre", "round", "bevel"}
_AUTO_CLOSE_THRESHOLD = 1e-3
_QUAD_SEGS_MAX = 256


@dataclass(frozen=True, slots=True)
class _PlaneBasis:
    """平面の 2D 基底を表現する（3D <-> 2D 変換用）。"""

    origin: np.ndarray  # (3,)
    u: np.ndarray  # (3,)
    v: np.ndarray  # (3,)


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _close_curve(points: np.ndarray, threshold: float) -> np.ndarray:
    """端点が閾値以内なら、始点を終端に複製して閉じる。"""
    if points.shape[0] < 2:
        return points
    dist = float(np.linalg.norm(points[0] - points[-1]))
    if dist <= float(threshold):
        return np.concatenate([points[:-1], points[0:1]], axis=0)
    return points


def _fit_plane_basis(points: np.ndarray) -> _PlaneBasis:
    """点群の推定平面を返す（2 点は安定な補助平面を作る）。"""
    p = points.astype(np.float64, copy=False)
    n = int(p.shape[0])

    if n <= 0:
        return _PlaneBasis(
            origin=np.zeros((3,), dtype=np.float64),
            u=np.array([1.0, 0.0, 0.0], dtype=np.float64),
            v=np.array([0.0, 1.0, 0.0], dtype=np.float64),
        )

    origin = p.mean(axis=0)
    if n == 1:
        return _PlaneBasis(
            origin=np.asarray(origin, dtype=np.float64),
            u=np.array([1.0, 0.0, 0.0], dtype=np.float64),
            v=np.array([0.0, 1.0, 0.0], dtype=np.float64),
        )

    if n == 2:
        d = p[1] - p[0]
        d_norm = float(np.linalg.norm(d))
        if not np.isfinite(d_norm) or d_norm <= 0.0:
            return _PlaneBasis(
                origin=np.asarray(origin, dtype=np.float64),
                u=np.array([1.0, 0.0, 0.0], dtype=np.float64),
                v=np.array([0.0, 1.0, 0.0], dtype=np.float64),
            )

        # 2 点だけの場合、平面は一意に決まらない。
        # 旧実装の「XY へ射影→復元」に近い体験に寄せるため、
        # もっとも変化が小さい軸を「法線」扱いして主平面（XY/XZ/YZ）へ寄せる。
        abs_d = np.abs(d)
        if float(abs_d[2]) <= float(abs_d[0]) and float(abs_d[2]) <= float(abs_d[1]):
            normal = np.array([0.0, 0.0, 1.0], dtype=np.float64)
        elif float(abs_d[1]) <= float(abs_d[0]) and float(abs_d[1]) <= float(abs_d[2]):
            normal = np.array([0.0, 1.0, 0.0], dtype=np.float64)
        else:
            normal = np.array([1.0, 0.0, 0.0], dtype=np.float64)

        d_in_plane = d - float(np.dot(d, normal)) * normal
        u_norm = float(np.linalg.norm(d_in_plane))
        if not np.isfinite(u_norm) or u_norm <= 0.0:
            return _PlaneBasis(
                origin=np.asarray(origin, dtype=np.float64),
                u=np.array([1.0, 0.0, 0.0], dtype=np.float64),
                v=np.array([0.0, 1.0, 0.0], dtype=np.float64),
            )
        u_axis = d_in_plane / u_norm
        v_axis = np.cross(normal, u_axis)

        return _PlaneBasis(origin=np.asarray(origin, dtype=np.float64), u=u_axis, v=v_axis)

    centered = p - origin
    _u, _s, vh = np.linalg.svd(centered, full_matrices=False)
    normal = vh[-1]
    n_norm = float(np.linalg.norm(normal))
    if not np.isfinite(n_norm) or n_norm <= 0.0:
        normal = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    else:
        normal = normal / n_norm

    ref = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    if abs(float(np.dot(ref, normal))) > 0.9:
        ref = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    u_axis = ref - float(np.dot(ref, normal)) * normal
    u_norm = float(np.linalg.norm(u_axis))
    if not np.isfinite(u_norm) or u_norm <= 0.0:
        ref = np.array([0.0, 0.0, 1.0], dtype=np.float64)
        u_axis = ref - float(np.dot(ref, normal)) * normal
        u_norm = float(np.linalg.norm(u_axis))
    u_axis = u_axis / u_norm
    v_axis = np.cross(normal, u_axis)

    return _PlaneBasis(origin=origin, u=u_axis, v=v_axis)


def _project_to_2d(points: np.ndarray, basis: _PlaneBasis) -> np.ndarray:
    p = points.astype(np.float64, copy=False) - basis.origin
    x = p @ basis.u
    y = p @ basis.v
    return np.stack([x, y], axis=1)


def _lift_to_3d(coords_2d: np.ndarray, basis: _PlaneBasis) -> np.ndarray:
    xy = coords_2d.astype(np.float64, copy=False)
    return basis.origin[None, :] + xy[:, 0:1] * basis.u[None, :] + xy[:, 1:2] * basis.v[None, :]


def _extract_vertices_2d(buffered, *, which: str) -> list[np.ndarray]:
    """Shapely geometry から輪郭頂点列（Nx2）を抽出して返す。"""
    if buffered.is_empty:
        return []

    # ローカル import（effect 未使用時に shapely import を避ける）
    from shapely.geometry import (  # type: ignore[import-not-found, import-untyped]
        LineString,
        MultiLineString,
        MultiPolygon,
        Polygon,
    )

    out: list[np.ndarray] = []
    if which == "exterior":
        if isinstance(buffered, Polygon):
            out.append(np.asarray(buffered.exterior.coords, dtype=np.float64))
            return out
        if isinstance(buffered, MultiPolygon):
            for poly in buffered.geoms:
                if not poly.is_empty:
                    out.append(np.asarray(poly.exterior.coords, dtype=np.float64))
            return out
        if isinstance(buffered, LineString):
            out.append(np.asarray(buffered.coords, dtype=np.float64))
            return out
        if isinstance(buffered, MultiLineString):
            for line in buffered.geoms:
                out.append(np.asarray(line.coords, dtype=np.float64))
            return out
    elif which == "interior":
        if isinstance(buffered, Polygon):
            for ring in buffered.interiors:
                out.append(np.asarray(ring.coords, dtype=np.float64))
            return out
        if isinstance(buffered, MultiPolygon):
            for poly in buffered.geoms:
                if poly.is_empty:
                    continue
                for ring in poly.interiors:
                    out.append(np.asarray(ring.coords, dtype=np.float64))
            return out
    else:
        raise ValueError(f"unknown which: {which!r}")

    # GeometryCollection 等の可能性は浅く処理する（未知型は黙って捨てる）。
    geoms = getattr(buffered, "geoms", None)
    if geoms is not None:
        for g in geoms:
            out.extend(_extract_vertices_2d(g, which=which))
    return out


@effect(meta=buffer_meta)
def buffer(
    inputs: Sequence[RealizedGeometry],
    *,
    join: str = "round",  # "mitre" | "round" | "bevel"
    quad_segs: int = 12,  # Shapely の quad_segs（1/4 円あたりの分割）
    distance: float = 5.0,
    keep_original: bool = False,
) -> RealizedGeometry:
    """Shapely の buffer を用いて輪郭を生成する。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        入力実体ジオメトリ列。通常は 1 要素。
    join : str, default "round"
        角の処理。`"mitre" | "round" | "bevel"` を指定。
    quad_segs : int, default 12
        円弧近似分割数（Shapely の `quad_segs` 相当）。
    distance : float, default 5.0
        buffer 距離 [mm]。

        - `distance > 0`: 外側輪郭（buffer 結果の exterior）
        - `distance < 0`: 内側輪郭（buffer 結果の holes / interiors）
        - `distance == 0`: no-op
    keep_original : bool, default False
        True のとき buffer 結果に加えて元のポリラインも出力に含める。

    Returns
    -------
    RealizedGeometry
        buffer 後の実体ジオメトリ。

    Notes
    -----
    旧実装の挙動を最小限で踏襲する:
    - 端点が近い線は自動で閉じる（閾値 `1e-3`）。
    - distance==0 は no-op 扱いとする。
    """
    if not inputs:
        return _empty_geometry()

    base = inputs[0]
    if base.coords.shape[0] == 0:
        return base

    d = float(distance)
    if not np.isfinite(d) or d == 0.0:
        return base
    abs_d = abs(d)

    join_style = str(join)
    if join_style not in _JOIN_STYLE_SET:
        return base

    quad_segs_i = int(quad_segs)
    if quad_segs_i < 1:
        quad_segs_i = 1
    if quad_segs_i > _QUAD_SEGS_MAX:
        quad_segs_i = _QUAD_SEGS_MAX

    # ローカル import（effect 未使用時に shapely import を避ける）
    from shapely.geometry import LineString  # type: ignore[import-not-found]

    coords = base.coords
    offsets = base.offsets

    out_lines: list[np.ndarray] = []
    for i in range(int(offsets.size) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        line3 = coords[s:e]
        if line3.shape[0] < 2:
            continue

        line3 = _close_curve(line3, _AUTO_CLOSE_THRESHOLD)
        basis = _fit_plane_basis(line3)
        line2 = _project_to_2d(line3, basis)

        buffered = LineString(line2).buffer(  # type: ignore[arg-type]
            abs_d,
            quad_segs=quad_segs_i,
            join_style=join_style,
        )
        which = "exterior" if d > 0.0 else "interior"
        for v2 in _extract_vertices_2d(buffered, which=which):
            if v2.shape[0] < 2:
                continue
            v3 = _lift_to_3d(v2[:, :2], basis).astype(np.float32, copy=False)
            out_lines.append(v3)

    if keep_original:
        for i in range(int(offsets.size) - 1):
            s = int(offsets[i])
            e = int(offsets[i + 1])
            original = coords[s:e]
            if original.shape[0] > 0:
                out_lines.append(original.astype(np.float32, copy=False))

    if not out_lines:
        return base if d > 0.0 else _empty_geometry()

    out_coords = np.concatenate(out_lines, axis=0).astype(np.float32, copy=False)
    out_offsets = np.empty((len(out_lines) + 1,), dtype=np.int32)
    out_offsets[0] = 0
    acc = 0
    for i, line in enumerate(out_lines):
        acc += int(line.shape[0])
        out_offsets[i + 1] = acc

    return RealizedGeometry(coords=out_coords, offsets=out_offsets)
