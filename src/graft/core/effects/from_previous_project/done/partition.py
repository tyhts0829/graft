from __future__ import annotations

"""
どこで: `effects.partition`（Geometry→Geometry の純関数エフェクト）
何を: 共平面な形状の偶奇領域（外環 XOR 穴）を Voronoi 図で分割し、閉ループを返す。
なぜ: `affine` で傾けた入力でも、穴を尊重した安定なパーティション結果を得るため。

処理の流れ（開発者向け）
1) 共平面フレーム選択 → 全体 XY 整列
   - 各リングに対する `transform_to_xy_plane` の z 残差で姿勢を推定。見つからなければ PCA(SVD) で法線推定。
   - 絶対/相対閾値で共平面判定（XY 限定ではない）。
2) 偶奇領域の構築（XY 空間）
   - Shapely あり: 各リング Polygon を `symmetric_difference` で XOR 合成（穴を自然に除外）。
   - Shapely なし: 耳切り三角化→重心の偶奇判定でセル選別（近似）。
3) Voronoi 分割（Shapely あり）
   - `site_count` 個の点を領域 bounds 内でサンプリングし、`voronoi_diagram(..., edges=False)` のセルを領域と交差。
   - 各 Polygon の外周を抽出し、`transform_back` で 3D に戻す。
4) フォールバック
   - Shapely 無: 2) の三角セルを 3D に戻して返す。
   - 非共平面: 安全側で入力をコピー。
"""

from typing import Any, Iterable, cast

import numpy as np

from engine.core.geometry import Geometry
from util.geom3d_frame import choose_coplanar_frame
from util.geom3d_ops import transform_back, transform_to_xy_plane

from .registry import effect

PARAM_META = {
    "site_count": {"type": "integer", "min": 12, "max": 500, "step": 1},
    "seed": {"type": "integer", "min": 0, "max": 2_147_483_647, "step": 1},
}
# ── 幾何ヘルパ ──────────────────────────────────────────────────────────────


@effect()
def partition(
    g: Geometry,
    *,
    site_count: int = 12,
    seed: int = 0,
) -> Geometry:
    """平面内の領域（偶奇規則）を Voronoi で分割し閉ループ群を返す（Shapely 必須）。

    Parameters
    ----------
    g : Geometry
        入力ジオメトリ（各行が 1 本のポリライン）。
    site_count : int, default 12
        サイト数（Voronoi 用）。
    seed : int, default 0
        乱数シード（再現性）。
    """
    coords, offsets = g.as_arrays(copy=False)
    if offsets.size <= 1:
        return Geometry(coords.copy(), offsets.copy())

    rng = np.random.default_rng(int(seed))

    # 共平面なら全体を XY に整列して処理（傾き下でも有効）
    planar, v2d_all, R_all, z_all, _ref_h = choose_coplanar_frame(coords, offsets)
    shapely_ok = False
    if planar:
        # Shapely があれば Voronoi ベース、無ければ偶奇三角分割でフォールバック
        try:
            from shapely.geometry import MultiPoint  # type: ignore
            from shapely.geometry import Polygon  # type: ignore
            from shapely.geometry import Point as _SPoint  # type: ignore
            from shapely.ops import voronoi_diagram as _voronoi_diagram  # type: ignore

            shapely_ok = True
        except Exception:
            shapely_ok = False

    if planar and shapely_ok:
        rings_xy: list[np.ndarray] = []
        for i in range(len(offsets) - 1):
            ring = v2d_all[offsets[i] : offsets[i + 1]].astype(np.float32, copy=False)
            if ring.shape[0] < 3:
                continue
            ring = _ensure_closed(ring)
            rings_xy.append(ring[:, :2])

        # 偶奇規則の領域（XOR）
        region = None
        for ring2d in rings_xy:
            try:
                poly = Polygon(ring2d)
                if not poly.is_valid:
                    poly = poly.buffer(0)
            except Exception:
                continue
            region = poly if region is None else region.symmetric_difference(poly)
        if region is None or region.is_empty:
            return Geometry(coords.copy(), offsets.copy())

        # サイト生成（random のみ）
        minx, miny, maxx, maxy = region.bounds
        pts: list[tuple[float, float]] = []
        trials = max(100, int(site_count) * 20)
        target = max(1, int(site_count))
        while len(pts) < target and trials > 0:
            rx = float(minx) + float(rng.random()) * float(maxx - minx)
            ry = float(miny) + float(rng.random()) * float(maxy - miny)
            if region.contains(_SPoint(rx, ry)):
                pts.append((rx, ry))
            trials -= 1
        if not pts:
            try:
                c = region.representative_point()
                pts = [(float(c.x), float(c.y))]
            except Exception:
                return Geometry(coords.copy(), offsets.copy())
        pts2d = np.asarray(pts, dtype=np.float32)

        # Voronoi → 交差 → 外周抽出 → 3D 復元
        mp = MultiPoint(pts2d)
        vd = _voronoi_diagram(mp, envelope=region.envelope, edges=False)  # type: ignore[arg-type]
        out_lines: list[np.ndarray] = []
        for cell in getattr(vd, "geoms", []):  # type: ignore[attr-defined]
            try:
                inter = cell.intersection(region)
                if inter.is_empty:
                    continue
            except Exception:
                continue
            loops = _collect_polygon_exteriors(inter)
            for loop in loops:
                # XY → 元姿勢へ戻す
                out_lines.append(transform_back(loop.astype(np.float32), R_all, z_all))
        return _numpy_lines_to_geometry(out_lines)

    # フォールバック（非平面 or Shapely 無）
    if planar and not shapely_ok:
        # Shapely 無時は偶奇三角分割で近似 → 3D 復元
        rings_fallback_xy: list[np.ndarray] = []
        for i in range(len(offsets) - 1):
            ring = v2d_all[offsets[i] : offsets[i + 1]].astype(np.float32, copy=False)
            if ring.shape[0] < 3:
                continue
            ring = _ensure_closed(ring)
            rings_fallback_xy.append(ring)
        tris = _triangulate_evenodd_region_xy_without_shapely(rings_fallback_xy)
        if not tris:
            return Geometry(coords.copy(), offsets.copy())
        out_lines = [transform_back(t.astype(np.float32), R_all, z_all) for t in tris]
        return _numpy_lines_to_geometry(out_lines)
    if not planar:
        return Geometry(coords.copy(), offsets.copy())
    # 冪等フォールバック（到達しない想定）
    return Geometry(coords.copy(), offsets.copy())


# UI RangeHint（量子化粒度は step を設定）
cast(Any, partition).__param_meta__ = PARAM_META


def _ensure_closed(loop: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    if loop.shape[0] == 0:
        return loop
    p0 = loop[0]
    p1 = loop[-1]
    if float(np.linalg.norm(p0 - p1)) <= eps:
        return loop
    return np.vstack([loop, p0])


def _signed_area_2d(poly2d: np.ndarray) -> float:
    # Shoelace formula（閉路でも開路でも可）
    x = poly2d[:, 0]
    y = poly2d[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _is_ccw(poly2d: np.ndarray) -> bool:
    return _signed_area_2d(poly2d) > 0.0


def _point_in_triangle(pt: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> bool:
    # バリセン座標で包含判定
    v0 = c - a
    v1 = b - a
    v2 = pt - a
    den = float(v0[0] * v1[1] - v1[0] * v0[1])
    if abs(den) < 1e-12:
        return False
    u = float(v2[0] * v1[1] - v1[0] * v2[1]) / den
    v = float(v0[0] * v2[1] - v2[0] * v0[1]) / den
    return u >= -1e-12 and v >= -1e-12 and (u + v) <= 1.0 + 1e-12


def _remove_collinear(poly2d: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    if poly2d.shape[0] <= 3:
        return poly2d
    keep = [True] * poly2d.shape[0]
    for i in range(poly2d.shape[0]):
        a = poly2d[i - 1]
        b = poly2d[i]
        c = poly2d[(i + 1) % poly2d.shape[0]]
        area2 = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(area2) <= eps:
            keep[i] = False
    out = poly2d[np.array(keep, dtype=bool)]
    if out.shape[0] < 3:
        return poly2d
    return out


def _earclip_triangulate_2d(poly2d: np.ndarray) -> list[np.ndarray]:
    """単純ポリゴンの耳切り三角分割（出力は各三角の3点＋閉路用に先頭を複製）。"""
    # 前処理: 閉路終端を除去して開路表現に統一
    if poly2d.shape[0] >= 2 and np.allclose(poly2d[0], poly2d[-1]):
        verts = poly2d[:-1].copy()
    else:
        verts = poly2d.copy()

    if verts.shape[0] < 3:
        return []
    # 退化除去 + 反時計回り化
    verts = _remove_collinear(verts)
    if not _is_ccw(verts):
        verts = np.flip(verts, axis=0)

    n = verts.shape[0]
    idx = list(range(n))
    out: list[np.ndarray] = []

    def is_convex(i_prev: int, i_curr: int, i_next: int) -> bool:
        a = verts[i_prev]
        b = verts[i_curr]
        c = verts[i_next]
        cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return cross > 1e-12

    guard = 0
    while len(idx) > 3 and guard < 10_000:
        ear_found = False
        m = len(idx)
        for k in range(m):
            i_prev = idx[(k - 1) % m]
            i_curr = idx[k]
            i_next = idx[(k + 1) % m]
            if not is_convex(i_prev, i_curr, i_next):
                continue
            a, b, c = verts[i_prev], verts[i_curr], verts[i_next]
            # 他頂点が三角形内にないか
            ok = True
            for j in idx:
                if j in (i_prev, i_curr, i_next):
                    continue
                if _point_in_triangle(verts[j], a, b, c):
                    ok = False
                    break
            if not ok:
                continue
            # クリップ
            tri = np.vstack([a, b, c, a]).astype(np.float32)
            out.append(tri)
            del idx[k]
            ear_found = True
            break
        if not ear_found:
            # 数値的に耳が見つからない場合は安全側で打ち切る
            break
        guard += 1

    if len(idx) == 3:
        a, b, c = (verts[idx[0]], verts[idx[1]], verts[idx[2]])
        tri = np.vstack([a, b, c, a]).astype(np.float32)
        out.append(tri)
    return out


def _is_polygon_planar(vertices: np.ndarray, eps_abs: float = 1e-5, eps_rel: float = 1e-4) -> bool:
    if vertices.shape[0] < 3:
        return False
    v = vertices.astype(np.float32, copy=False)
    v2d, _R, _z = transform_to_xy_plane(v)
    z = v2d[:, 2]
    z_span = float(np.max(np.abs(z))) if z.size else 0.0
    mins = np.min(v, axis=0)
    maxs = np.max(v, axis=0)
    diag = float(np.sqrt(np.sum((maxs - mins) ** 2)))
    threshold = max(float(eps_abs), float(eps_rel) * diag)
    return z_span <= threshold


def _numpy_lines_to_geometry(lines: Iterable[np.ndarray]) -> Geometry:
    arrs = [np.asarray(l, dtype=np.float32) for l in lines if l is not None and len(l) > 0]
    if not arrs:
        return Geometry.from_lines([])
    return Geometry.from_lines(arrs)


def _triangulate_polygon_xy(poly2d_closed: np.ndarray) -> list[np.ndarray]:
    """ポリゴン（XY, 閉路）を三角形群に分割し、各三角を閉路で返す。"""
    try:
        from shapely.geometry import Polygon  # type: ignore
        from shapely.ops import triangulate as _triangulate  # type: ignore

        pg = Polygon(poly2d_closed[:, :2])
        tris = _triangulate(pg)
        out: list[np.ndarray] = []
        for t in tris:
            if t.is_empty:
                continue
            coords = np.array(t.exterior.coords, dtype=np.float32)
            out.append(np.hstack([coords, np.zeros((coords.shape[0], 1), dtype=np.float32)]))
        return out
    except Exception:
        # フォールバック（耳切り）
        pass
    # 耳切り
    tris2d = _earclip_triangulate_2d(poly2d_closed[:, :2].astype(np.float32))
    out = [np.hstack([t, np.zeros((t.shape[0], 1), dtype=np.float32)]) for t in tris2d]
    return out


def _voronoi_cells_xy(poly2d_closed: np.ndarray, points2d: np.ndarray) -> list[np.ndarray]:
    """Voronoi セルを生成し多角形と交差を取り、閉路で返す（Shapely 必須）。"""
    try:
        from shapely.geometry import MultiPoint, Polygon  # type: ignore
        from shapely.ops import voronoi_diagram as _voronoi_diagram  # type: ignore

        pg = Polygon(poly2d_closed[:, :2])
        mp = MultiPoint(points2d[:, :2])
        vd = _voronoi_diagram(mp, envelope=pg, edges=False)  # type: ignore[arg-type]
        cells: list[np.ndarray] = []
        for geom in getattr(vd, "geoms", []):  # type: ignore[attr-defined]
            try:
                inter = geom.intersection(pg)
                if inter.is_empty:
                    continue
                if inter.geom_type == "Polygon":
                    coords = np.array(inter.exterior.coords, dtype=np.float32)
                    cells.append(
                        np.hstack([coords, np.zeros((coords.shape[0], 1), dtype=np.float32)])
                    )
            except Exception:
                continue
        if not cells:
            return _triangulate_polygon_xy(poly2d_closed)
        return cells
    except Exception:
        return _triangulate_polygon_xy(poly2d_closed)


def _is_planar_xy_all(coords: np.ndarray, eps: float = 1e-6) -> bool:
    """全頂点の z がほぼ一定（XY 共平面）かを判定する。"""
    if coords.size == 0:
        return False
    z = coords[:, 2]
    return float(np.max(z) - np.min(z)) <= float(eps)


def _pnpoly_point_in(poly2d_closed: np.ndarray, pt: np.ndarray) -> bool:
    """レイキャストで点がポリゴン内かを判定（閉路/開路どちらにも耐える）。"""
    x, y = float(pt[0]), float(pt[1])
    p = poly2d_closed
    n = p.shape[0]
    inside = False
    for i in range(n - 1):
        x1, y1 = p[i, 0], p[i, 1]
        x2, y2 = p[i + 1, 0], p[i + 1, 1]
        if (y1 > y) != (y2 > y):
            xin = x1 + (y - y1) * (x2 - x1) / (y2 - y1 + 1e-12)
            if x <= xin:
                inside = not inside
    return inside


def _triangulate_evenodd_region_xy_without_shapely(rings_xy: list[np.ndarray]) -> list[np.ndarray]:
    """Shapely 不在時: 各リングを三角化し、偶奇規則でセル（三角）を選別。"""
    triangles: list[np.ndarray] = []
    for ring in rings_xy:
        tris = _triangulate_polygon_xy(ring)
        triangles.extend(tris)
    if not triangles:
        return []
    out: list[np.ndarray] = []
    for tri in triangles:
        c = np.mean(tri[:3, :2], axis=0)
        cnt = 0
        for ring in rings_xy:
            if _pnpoly_point_in(ring, c):
                cnt += 1
        if (cnt % 2) == 1:
            out.append(tri)
    return out


def _collect_polygon_exteriors(geom) -> list[np.ndarray]:  # type: ignore[no-untyped-def]
    """Shapely geometry から Polygon 外周を ndarray で抽出（holes は無視）。"""
    arrs: list[np.ndarray] = []
    try:
        if geom.is_empty:
            return arrs
    except Exception:
        return arrs
    gtype = getattr(geom, "geom_type", "")
    if gtype == "Polygon":
        coords = np.array(geom.exterior.coords, dtype=np.float32)
        arrs.append(np.hstack([coords, np.zeros((coords.shape[0], 1), dtype=np.float32)]))
        return arrs
    # MultiPolygon / GeometryCollection etc
    for g in getattr(geom, "geoms", []):  # type: ignore[attr-defined]
        arrs.extend(_collect_polygon_exteriors(g))
    return arrs
