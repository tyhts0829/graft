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
    "mode": ParamMeta(kind="choice", choices=("merge", "group", "ring")),
    "site_count": ParamMeta(kind="int", ui_min=1, ui_max=500),
    # imgui.slider_int は内部で「min/max が int32 の半分レンジ以内」を要求するため、
    # GUI 用レンジは控えめにし、必要ならコード側で任意の seed を指定する。
    "seed": ParamMeta(kind="int", ui_min=0, ui_max=1_073_741_823),
    "site_density_base": ParamMeta(kind="vec3", ui_min=0.0, ui_max=1.0),
    "site_density_slope": ParamMeta(kind="vec3", ui_min=-1.0, ui_max=1.0),
    "auto_center": ParamMeta(kind="bool"),
    "pivot": ParamMeta(kind="vec3", ui_min=-100.0, ui_max=100.0),
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


def _combine_evenodd(polys, Polygon):  # type: ignore[no-untyped-def]
    if not polys:
        return None

    if len(polys) == 1:
        return polys[0]

    if len(polys) == 2:
        a, b = polys
        if a.geom_type == "Polygon" and b.geom_type == "Polygon" and a.contains(b):
            try:
                return Polygon(a.exterior.coords, holes=[b.exterior.coords])
            except Exception:
                return a.symmetric_difference(b)
        if a.geom_type == "Polygon" and b.geom_type == "Polygon" and b.contains(a):
            try:
                return Polygon(b.exterior.coords, holes=[a.exterior.coords])
            except Exception:
                return a.symmetric_difference(b)
        if a.disjoint(b):
            return a.union(b)
        return a.symmetric_difference(b)

    region = None
    for poly in polys:
        region = poly if region is None else region.symmetric_difference(poly)
    return region


def _build_evenodd_groups(polys, rings_2d, Point):  # type: ignore[no-untyped-def]
    """外周＋穴を even-odd でグルーピングし、[outer, hole...] のインデックス列を返す。"""
    n = int(len(polys))
    if n == 0:
        return []
    if n != int(len(rings_2d)):
        raise ValueError("polys と rings_2d のサイズが一致しない")

    rep_pts = [(float(ring[0, 0]), float(ring[0, 1])) for ring in rings_2d]
    areas = [float(getattr(poly, "area", 0.0)) for poly in polys]

    contains_count = [0] * n
    for i in range(n):
        x, y = rep_pts[i]
        pt = Point(x, y)
        count = 0
        for j in range(n):
            if j == i:
                continue
            try:
                if polys[j].contains(pt):
                    count += 1
            except Exception:
                continue
        contains_count[i] = count

    is_outer = [(c % 2) == 0 for c in contains_count]
    outer_ids = [i for i in range(n) if is_outer[i]]

    parent_outer = [-1] * n
    for i in range(n):
        if is_outer[i]:
            continue
        x, y = rep_pts[i]
        pt = Point(x, y)
        best = -1
        best_area = float("inf")
        for j in outer_ids:
            if j == i:
                continue
            try:
                if polys[j].contains(pt):
                    a = float(areas[j])
                    if a < best_area:
                        best_area = a
                        best = j
            except Exception:
                continue
        parent_outer[i] = best

    groups = {oi: [oi] for oi in outer_ids}
    orphan_keys: list[int] = []
    for i in range(n):
        if is_outer[i]:
            continue
        p = int(parent_outer[i])
        if p >= 0 and p != i:
            groups.setdefault(p, [p]).append(i)
        else:
            groups[i] = [i]
            orphan_keys.append(i)

    ordered: list[list[int]] = []
    for oi in outer_ids:
        members = sorted(groups.get(oi, [oi]))
        ordered.append(members)
    for key in orphan_keys:
        members = sorted(groups.get(key, [key]))
        ordered.append(members)
    return ordered


@effect(meta=partition_meta)
def partition(
    inputs: Sequence[RealizedGeometry],
    *,
    mode: str = "merge",
    site_count: int = 12,
    seed: int = 0,
    site_density_base: tuple[float, float, float] = (0.0, 0.0, 0.0),
    site_density_slope: tuple[float, float, float] = (0.0, 0.0, 0.0),
    auto_center: bool = True,
    pivot: tuple[float, float, float] = (0.0, 0.0, 0.0),
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
    site_density_base : tuple[float, float, float], default (0.0, 0.0, 0.0)
        サイト密度（採用確率）の中心値（軸別）。各成分は 0.0〜1.0。
        全成分が 0.0 かつ `site_density_slope` が全て 0.0 の場合、密度制御は無効。
    site_density_slope : tuple[float, float, float], default (0.0, 0.0, 0.0)
        正規化座標 t∈[-1,+1] に対する密度勾配（軸別）。
    auto_center : bool, default True
        True のとき `pivot` を無視し、入力 bbox の中心を pivot として扱う。
    pivot : tuple[float, float, float], default (0.0, 0.0, 0.0)
        auto_center=False のときの pivot（ワールド座標）。
    mode : str, default "merge"
        入力リングの扱い。
        `"merge"` は全リングを 1 つの領域へ畳み込んでから分割する。
        `"group"` は even-odd で外周+穴をグループ化し、グループごとに分割する。
        `"ring"` は各リングを独立領域として扱い、リングごとに分割する（穴構造は無視）。

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
        from shapely.geometry import MultiPoint, Point, Polygon  # type: ignore
        from shapely.ops import voronoi_diagram  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("partition effect は shapely が必要です") from exc

    mode = str(mode)
    if mode not in ("merge", "group", "ring"):
        raise ValueError("partition の mode は 'merge'|'group'|'ring' のいずれかである必要がある")

    coords_2d_all = _project_to_2d(base.coords, basis)

    offsets = base.offsets
    rings_2d: list[np.ndarray] = []
    polys = []
    for i in range(int(offsets.size) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        ring = coords_2d_all[s:e]
        if ring.shape[0] < 3:
            continue
        ring_2d = _ensure_closed_2d(ring)
        try:
            poly = Polygon(ring_2d)
            if not poly.is_valid:
                poly = poly.buffer(0)
        except Exception:
            continue
        if poly.is_empty:
            continue
        rings_2d.append(ring_2d)
        polys.append(poly)

    if not polys:
        return base

    site_count = max(1, int(site_count))
    rng = np.random.default_rng(int(seed))

    regions = []
    if mode == "ring":
        regions = list(polys)
    elif mode == "group":
        groups = _build_evenodd_groups(polys, rings_2d, Point)
        for g in groups:
            region = _combine_evenodd([polys[i] for i in g], Polygon)
            if region is not None and not region.is_empty:
                regions.append(region)
    else:
        region = _combine_evenodd(polys, Polygon)
        if region is None or region.is_empty:
            return base
        regions = [region]

    try:
        base_x = float(site_density_base[0])
        base_y = float(site_density_base[1])
        base_z = float(site_density_base[2])
    except Exception:
        base_x = 0.0
        base_y = 0.0
        base_z = 0.0

    if not np.isfinite(base_x):
        base_x = 0.0
    if not np.isfinite(base_y):
        base_y = 0.0
    if not np.isfinite(base_z):
        base_z = 0.0

    if base_x < 0.0:
        base_x = 0.0
    elif base_x > 1.0:
        base_x = 1.0
    if base_y < 0.0:
        base_y = 0.0
    elif base_y > 1.0:
        base_y = 1.0
    if base_z < 0.0:
        base_z = 0.0
    elif base_z > 1.0:
        base_z = 1.0

    try:
        slope_x = float(site_density_slope[0])
        slope_y = float(site_density_slope[1])
        slope_z = float(site_density_slope[2])
    except Exception:
        slope_x = 0.0
        slope_y = 0.0
        slope_z = 0.0

    if not np.isfinite(slope_x):
        slope_x = 0.0
    if not np.isfinite(slope_y):
        slope_y = 0.0
    if not np.isfinite(slope_z):
        slope_z = 0.0

    density_enabled = (
        (base_x != 0.0)
        or (base_y != 0.0)
        or (base_z != 0.0)
        or (slope_x != 0.0)
        or (slope_y != 0.0)
        or (slope_z != 0.0)
    )

    if density_enabled:
        mins3 = np.min(base.coords, axis=0).astype(np.float64, copy=False)
        maxs3 = np.max(base.coords, axis=0).astype(np.float64, copy=False)
        bbox_center = (mins3 + maxs3) * 0.5
        extent3 = (maxs3 - mins3) * 0.5

        inv_extent3 = np.zeros((3,), dtype=np.float64)
        for k in range(3):
            extent_k = float(extent3[k])
            inv_extent3[k] = 0.0 if extent_k < 1e-9 else 1.0 / extent_k

        if auto_center:
            pivot3 = bbox_center
        else:
            try:
                pivot3 = np.array(
                    [float(pivot[0]), float(pivot[1]), float(pivot[2])],
                    dtype=np.float64,
                )
            except Exception:
                pivot3 = np.zeros((3,), dtype=np.float64)
            if not np.all(np.isfinite(pivot3)):
                pivot3 = np.zeros((3,), dtype=np.float64)

        o3 = basis.origin.astype(np.float64, copy=False)
        u3 = basis.u.astype(np.float64, copy=False)
        v3 = basis.v.astype(np.float64, copy=False)

        def _p_eff_for_xy(xy: np.ndarray) -> np.ndarray:
            p3 = o3[None, :] + xy[:, 0:1] * u3[None, :] + xy[:, 1:2] * v3[None, :]
            t = (p3 - pivot3[None, :]) * inv_extent3[None, :]
            t = np.clip(t, -1.0, 1.0)
            tx = t[:, 0]
            ty = t[:, 1]
            tz = t[:, 2]

            p_x = np.clip(base_x + slope_x * tx, 0.0, 1.0)
            p_y = np.clip(base_y + slope_y * ty, 0.0, 1.0)
            p_z = np.clip(base_z + slope_z * tz, 0.0, 1.0)
            return 1.0 - (1.0 - p_x) * (1.0 - p_y) * (1.0 - p_z)

    all_loops_2d: list[np.ndarray] = []
    for region in regions:
        minx, miny, maxx, maxy = region.bounds
        width = float(maxx) - float(minx)
        height = float(maxy) - float(miny)

        pts: list[tuple[float, float]] = []
        if width > 0.0 and height > 0.0:
            trials_per_phase = max(1000, site_count * 50)
            batch = max(256, site_count * 20)

            def _append_points(xs: np.ndarray, ys: np.ndarray) -> None:
                need = int(site_count) - len(pts)
                if need <= 0:
                    return
                for x, y in zip(xs[:need], ys[:need], strict=False):
                    pts.append((float(x), float(y)))

            trials_left = int(trials_per_phase)
            while len(pts) < site_count and trials_left > 0:
                n = min(int(batch), int(trials_left))
                xs = float(minx) + rng.random(n) * width
                ys = float(miny) + rng.random(n) * height
                inside = shapely.contains_xy(region, xs, ys)
                if not np.any(inside):
                    trials_left -= n
                    continue

                xs_in = xs[inside]
                ys_in = ys[inside]
                if density_enabled:
                    xy = np.stack([xs_in, ys_in], axis=1).astype(np.float64, copy=False)
                    p_eff = _p_eff_for_xy(xy)
                    take = rng.random(int(p_eff.shape[0])) < p_eff
                    _append_points(xs_in[take], ys_in[take])
                else:
                    _append_points(xs_in, ys_in)

                trials_left -= n

            # top-up: density で足りない場合は、一様サンプリングで埋めて site_count を満たす。
            if density_enabled and len(pts) < site_count:
                trials_left = int(trials_per_phase)
                while len(pts) < site_count and trials_left > 0:
                    n = min(int(batch), int(trials_left))
                    xs = float(minx) + rng.random(n) * width
                    ys = float(miny) + rng.random(n) * height
                    inside = shapely.contains_xy(region, xs, ys)
                    if np.any(inside):
                        _append_points(xs[inside], ys[inside])
                    trials_left -= n

        if not pts:
            try:
                c = region.representative_point()
                pts = [(float(c.x), float(c.y))]
            except Exception:
                continue

        if len(pts) <= 1:
            all_loops_2d.extend(_collect_polygon_exteriors(region))
            continue

        mp = MultiPoint(pts)
        try:
            vd = voronoi_diagram(mp, envelope=region.envelope, edges=False)  # type: ignore[arg-type]
        except Exception:
            continue

        for cell in getattr(vd, "geoms", []):  # type: ignore[attr-defined]
            try:
                inter = cell.intersection(region)
            except Exception:
                continue
            if inter.is_empty:
                continue
            all_loops_2d.extend(_collect_polygon_exteriors(inter))

    loops_2d = [loop for loop in all_loops_2d if loop.shape[0] >= 4]
    if not loops_2d:
        return base

    def _sort_key(loop: np.ndarray) -> tuple[float, float]:
        c = loop[:-1].astype(np.float64, copy=False).mean(axis=0)
        return (float(c[0]), float(c[1]))

    loops_2d.sort(key=_sort_key)

    lines_3d = [_lift_to_3d(loop, basis) for loop in loops_2d]
    return _lines_to_realized_geometry(lines_3d)
