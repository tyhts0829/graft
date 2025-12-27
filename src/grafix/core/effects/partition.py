"""閉ループ群を Voronoi 図で分割し、部分領域の閉ループ群を返す effect。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from grafix.core.effect_registry import effect
from grafix.core.realized_geometry import RealizedGeometry
from grafix.core.parameters.meta import ParamMeta

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

    origin = points.mean(axis=0, dtype=np.float64)
    mins = np.min(points, axis=0)
    maxs = np.max(points, axis=0)
    diag = float(np.linalg.norm(maxs.astype(np.float64) - mins.astype(np.float64)))
    threshold = max(float(eps_abs), float(eps_rel) * diag)

    # Fast-path: 入力が XY 平面上（z 残差のみで共平面判定できる）なら、重い推定をスキップする。
    z = points[:, 2].astype(np.float64, copy=False)
    z_residual = float(np.max(np.abs(z - float(origin[2]))))
    if z_residual <= threshold:
        basis = _PlaneBasis(
            origin=np.asarray(origin, dtype=np.float64),
            u=np.array([1.0, 0.0, 0.0], dtype=np.float64),
            v=np.array([0.0, 1.0, 0.0], dtype=np.float64),
        )
        return True, basis

    # SVD（(N,3)）は重いので、3x3 共分散行列の固有分解で法線を推定する。
    x = points[:, 0]
    y = points[:, 1]
    zz = points[:, 2]
    sxx = float(np.sum(x * x, dtype=np.float64))
    sxy = float(np.sum(x * y, dtype=np.float64))
    sxz = float(np.sum(x * zz, dtype=np.float64))
    syy = float(np.sum(y * y, dtype=np.float64))
    syz = float(np.sum(y * zz, dtype=np.float64))
    szz = float(np.sum(zz * zz, dtype=np.float64))

    S = np.array(
        [
            [sxx, sxy, sxz],
            [sxy, syy, syz],
            [sxz, syz, szz],
        ],
        dtype=np.float64,
    )
    C = S - float(points.shape[0]) * np.outer(origin, origin)
    try:
        _w, v = np.linalg.eigh(C)
    except Exception:
        basis = _PlaneBasis(
            origin=np.asarray(origin, dtype=np.float64),
            u=np.array([1.0, 0.0, 0.0], dtype=np.float64),
            v=np.array([0.0, 1.0, 0.0], dtype=np.float64),
        )
        return False, basis

    normal = v[:, 0]
    n_norm = float(np.linalg.norm(normal))
    if not np.isfinite(n_norm) or n_norm <= 0.0:
        basis = _PlaneBasis(
            origin=np.asarray(origin, dtype=np.float64),
            u=np.array([1.0, 0.0, 0.0], dtype=np.float64),
            v=np.array([0.0, 1.0, 0.0], dtype=np.float64),
        )
        return False, basis
    normal = normal / n_norm

    d = points @ normal - float(np.dot(origin, normal))
    residual = float(np.max(np.abs(d)))
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
    # dtype 混在での暗黙キャスト（(N,3) の一時コピー）を避けるため、基底だけ float32 へ落とす。
    u = basis.u.astype(np.float32, copy=False)
    v = basis.v.astype(np.float32, copy=False)
    o = basis.origin.astype(np.float32, copy=False)

    x = points @ u - float(o @ u)
    y = points @ v - float(o @ v)
    return np.stack([x, y], axis=1).astype(np.float32, copy=False)


def _lift_to_3d(coords_2d: np.ndarray, basis: _PlaneBasis) -> np.ndarray:
    """2D 点群を 3D 空間へ戻す。"""
    xy = coords_2d.astype(np.float32, copy=False)
    u = basis.u.astype(np.float32, copy=False)
    v = basis.v.astype(np.float32, copy=False)
    o = basis.origin.astype(np.float32, copy=False)
    return o[None, :] + xy[:, 0:1] * u[None, :] + xy[:, 1:2] * v[None, :]


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
        import shapely  # type: ignore
        from shapely.geometry import MultiPoint, Polygon  # type: ignore
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

    polys = []
    for ring in rings_2d:
        try:
            poly = Polygon(ring)
            if not poly.is_valid:
                poly = poly.buffer(0)
        except Exception:
            continue
        if poly.is_empty:
            continue
        polys.append(poly)

    region = None
    if len(polys) == 1:
        region = polys[0]
    elif len(polys) == 2:
        a, b = polys
        if a.geom_type == "Polygon" and b.geom_type == "Polygon" and a.contains(b):
            try:
                region = Polygon(a.exterior.coords, holes=[b.exterior.coords])
            except Exception:
                region = a.symmetric_difference(b)
        elif a.geom_type == "Polygon" and b.geom_type == "Polygon" and b.contains(a):
            try:
                region = Polygon(b.exterior.coords, holes=[a.exterior.coords])
            except Exception:
                region = a.symmetric_difference(b)
        elif a.disjoint(b):
            region = a.union(b)
        else:
            region = a.symmetric_difference(b)
    else:
        for poly in polys:
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
        trials_left = max(1000, site_count * 50)
        batch = max(256, site_count * 20)
        while len(pts) < site_count and trials_left > 0:
            n = min(int(batch), int(trials_left))
            xs = float(minx) + rng.random(n) * width
            ys = float(miny) + rng.random(n) * height
            mask = shapely.contains_xy(region, xs, ys)

            if np.any(mask):
                accepted = np.stack([xs[mask], ys[mask]], axis=1)
                need = int(site_count) - len(pts)
                for x, y in accepted[:need]:
                    pts.append((float(x), float(y)))

            trials_left -= n

    if not pts:
        try:
            c = region.representative_point()
            pts = [(float(c.x), float(c.y))]
        except Exception:
            return base

    loops_2d: list[np.ndarray]
    if len(pts) <= 1:
        loops_2d = _collect_polygon_exteriors(region)
    else:
        mp = MultiPoint(pts)
        try:
            vd = voronoi_diagram(mp, envelope=region.envelope, edges=False)  # type: ignore[arg-type]
        except Exception:
            return base

        loops_2d = []
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
        c = loop[:-1].astype(np.float64, copy=False).mean(axis=0)
        return (float(c[0]), float(c[1]))

    loops_2d.sort(key=_sort_key)

    lines_3d = [_lift_to_3d(loop, basis) for loop in loops_2d]
    return _lines_to_realized_geometry(lines_3d)
