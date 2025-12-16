"""閉ループ群を Voronoi 図で分割し、部分領域の閉ループ群を返す effect。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from src.core.effect_registry import effect
from src.core.realized_geometry import RealizedGeometry
from src.core.parameters.meta import ParamMeta

NONPLANAR_EPS_ABS = 1e-6
NONPLANAR_EPS_REL = 1e-5

partition_meta = {
    "site_count": ParamMeta(kind="int", ui_min=1, ui_max=500),
    # imgui.slider_int は内部で「min/max が int32 の半分レンジ以内」を要求するため、
    # GUI 用レンジは控えめにし、必要ならコード側で任意の seed を指定する。
    "seed": ParamMeta(kind="int", ui_min=0, ui_max=1_073_741_823),
}


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


def _fit_plane_basis(
    points: np.ndarray,
    *,
    eps_abs: float = NONPLANAR_EPS_ABS,
    eps_rel: float = NONPLANAR_EPS_REL,
) -> tuple[bool, _PlaneBasis]:
    """点群が“ほぼ平面”なら 2D 基底を返す。"""
    if points.shape[0] < 3:
        origin = points.mean(axis=0) if points.shape[0] else np.zeros((3,), dtype=np.float64)
        basis = _PlaneBasis(
            origin=np.asarray(origin, dtype=np.float64),
            u=np.array([1.0, 0.0, 0.0], dtype=np.float64),
            v=np.array([0.0, 1.0, 0.0], dtype=np.float64),
        )
        return False, basis

    p = points.astype(np.float64, copy=False)
    origin = p.mean(axis=0)
    centered = p - origin

    _u, _s, vh = np.linalg.svd(centered, full_matrices=False)
    normal = vh[-1]
    n_norm = float(np.linalg.norm(normal))
    if not np.isfinite(n_norm) or n_norm <= 0.0:
        basis = _PlaneBasis(
            origin=np.asarray(origin, dtype=np.float64),
            u=np.array([1.0, 0.0, 0.0], dtype=np.float64),
            v=np.array([0.0, 1.0, 0.0], dtype=np.float64),
        )
        return False, basis
    normal = normal / n_norm

    residual = np.max(np.abs(centered @ normal))
    mins = np.min(p, axis=0)
    maxs = np.max(p, axis=0)
    diag = float(np.linalg.norm(maxs - mins))
    threshold = max(float(eps_abs), float(eps_rel) * diag)
    planar = bool(residual <= threshold)

    # 基底の向きを安定させるため、world x 軸の平面内射影を u として採用する。
    # normal とほぼ平行な場合は y 軸へフォールバックする。
    ref = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    if abs(float(np.dot(ref, normal))) > 0.9:
        ref = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    u_axis = ref - float(np.dot(ref, normal)) * normal
    u_norm = float(np.linalg.norm(u_axis))
    if u_norm <= 0.0:
        ref = np.array([0.0, 0.0, 1.0], dtype=np.float64)
        u_axis = ref - float(np.dot(ref, normal)) * normal
        u_norm = float(np.linalg.norm(u_axis))
    u_axis = u_axis / u_norm
    v_axis = np.cross(normal, u_axis)

    basis = _PlaneBasis(origin=origin, u=u_axis, v=v_axis)
    return planar, basis


def _project_to_2d(points: np.ndarray, basis: _PlaneBasis) -> np.ndarray:
    """3D 点群を平面 2D 座標へ射影する。"""
    p = points.astype(np.float64, copy=False) - basis.origin
    x = p @ basis.u
    y = p @ basis.v
    return np.stack([x, y], axis=1).astype(np.float32, copy=False)


def _lift_to_3d(coords_2d: np.ndarray, basis: _PlaneBasis) -> np.ndarray:
    """2D 点群を 3D 空間へ戻す。"""
    xy = coords_2d.astype(np.float64, copy=False)
    return (
        basis.origin[None, :]
        + xy[:, 0:1] * basis.u[None, :]
        + xy[:, 1:2] * basis.v[None, :]
    ).astype(np.float32, copy=False)


def _ensure_closed_2d(loop: np.ndarray) -> np.ndarray:
    if loop.shape[0] == 0:
        return loop
    if loop.shape[0] >= 2 and np.allclose(loop[0], loop[-1], rtol=0.0, atol=1e-6):
        return loop
    return np.concatenate([loop, loop[:1]], axis=0)


def _lines_to_realized_geometry(lines: Sequence[np.ndarray]) -> RealizedGeometry:
    if not lines:
        return _empty_geometry()
    coords_list = [np.asarray(line, dtype=np.float32) for line in lines if line.shape[0] > 0]
    if not coords_list:
        return _empty_geometry()

    total = int(sum(int(a.shape[0]) for a in coords_list))
    coords = np.empty((total, 3), dtype=np.float32)
    offsets = np.empty((len(coords_list) + 1,), dtype=np.int32)
    offsets[0] = 0

    cursor = 0
    for i, arr in enumerate(coords_list, start=1):
        n = int(arr.shape[0])
        coords[cursor : cursor + n] = arr
        cursor += n
        offsets[i] = cursor
    return RealizedGeometry(coords=coords, offsets=offsets)


def _collect_polygon_exteriors(geom) -> list[np.ndarray]:  # type: ignore[no-untyped-def]
    """Shapely geometry から Polygon 外周を ndarray で抽出する（holes は無視）。"""
    try:
        if geom.is_empty:
            return []
    except Exception:
        return []

    gtype = getattr(geom, "geom_type", "")
    if gtype == "Polygon":
        coords = np.asarray(geom.exterior.coords, dtype=np.float32)
        return [coords]

    out: list[np.ndarray] = []
    for g in getattr(geom, "geoms", []):  # type: ignore[attr-defined]
        out.extend(_collect_polygon_exteriors(g))
    return out


@effect(meta=partition_meta)
def partition(
    inputs: Sequence[RealizedGeometry],
    *,
    site_count: int = 12,
    seed: int = 0,
) -> RealizedGeometry:
    """偶奇規則の平面領域を Voronoi 分割し、閉ループ群を返す。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        入力の実体ジオメトリ列。通常は 1 要素で、各ポリラインが閉ループ（リング）を表す。
    site_count : int, default 12
        Voronoi のサイト数。1 未満は 1 扱い。
    seed : int, default 0
        乱数シード（再現性）。

    Returns
    -------
    RealizedGeometry
        分割セルの外周を並べた実体ジオメトリ。

    Notes
    -----
    入力が非共平面の場合は no-op として入力を返す。
    """
    if not inputs:
        return _empty_geometry()

    base = inputs[0]
    if base.coords.shape[0] == 0:
        return base

    planar, basis = _fit_plane_basis(base.coords)
    if not planar:
        return base

    try:
        from shapely.geometry import MultiPoint, Point, Polygon  # type: ignore
        from shapely.ops import voronoi_diagram  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("partition effect は shapely が必要です") from exc

    coords_2d_all = _project_to_2d(base.coords, basis)

    rings_2d: list[np.ndarray] = []
    offsets = base.offsets
    for i in range(int(offsets.size) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        ring = coords_2d_all[s:e]
        if ring.shape[0] < 3:
            continue
        rings_2d.append(_ensure_closed_2d(ring))

    if not rings_2d:
        return base

    region = None
    for ring in rings_2d:
        try:
            poly = Polygon(ring)
            if not poly.is_valid:
                poly = poly.buffer(0)
        except Exception:
            continue
        if poly.is_empty:
            continue
        region = poly if region is None else region.symmetric_difference(poly)

    if region is None or region.is_empty:
        return base

    site_count = max(1, int(site_count))
    rng = np.random.default_rng(int(seed))

    minx, miny, maxx, maxy = region.bounds
    width = float(maxx) - float(minx)
    height = float(maxy) - float(miny)

    pts: list[tuple[float, float]] = []
    if width > 0.0 and height > 0.0:
        trials = max(1000, site_count * 50)
        while len(pts) < site_count and trials > 0:
            rx = float(minx) + float(rng.random()) * width
            ry = float(miny) + float(rng.random()) * height
            if region.covers(Point(rx, ry)):
                pts.append((rx, ry))
            trials -= 1

    if not pts:
        try:
            c = region.representative_point()
            pts = [(float(c.x), float(c.y))]
        except Exception:
            return base

    if len(pts) <= 1:
        loops_2d = _collect_polygon_exteriors(region)
    else:
        mp = MultiPoint(pts)
        try:
            vd = voronoi_diagram(mp, envelope=region.envelope, edges=False)  # type: ignore[arg-type]
        except Exception:
            return base

        loops_2d: list[np.ndarray] = []
        for cell in getattr(vd, "geoms", []):  # type: ignore[attr-defined]
            try:
                inter = cell.intersection(region)
            except Exception:
                continue
            if inter.is_empty:
                continue
            loops_2d.extend(_collect_polygon_exteriors(inter))

    loops_2d = [loop for loop in loops_2d if loop.shape[0] >= 4]
    if not loops_2d:
        return base

    def _sort_key(loop: np.ndarray) -> tuple[float, float]:
        pts = loop[:-1] if loop.shape[0] >= 2 and np.allclose(loop[0], loop[-1]) else loop
        c = pts.astype(np.float64, copy=False).mean(axis=0)
        return (float(c[0]), float(c[1]))

    loops_2d.sort(key=_sort_key)

    lines_3d = [_lift_to_3d(loop, basis) for loop in loops_2d]
    return _lines_to_realized_geometry(lines_3d)
