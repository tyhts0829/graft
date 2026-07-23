"""閉ループ群を Voronoi 図で分割し、部分領域の閉ループ群を返す effect。"""

from __future__ import annotations

import numpy as np
import shapely  # type: ignore[import-untyped]
from shapely.errors import GEOSException  # type: ignore[import-untyped]
from shapely.geometry import (  # type: ignore[import-untyped]
    GeometryCollection,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry  # type: ignore[import-untyped]
from shapely.ops import voronoi_diagram  # type: ignore[import-untyped]

from grafix.core.operation_authoring import effect
from grafix.core.realized_geometry import GeomTuple
from grafix.core.parameters.meta import ParamMeta
from grafix.core.geometry_kernels.packed import pack_polylines
from grafix.core.geometry_kernels.planar import (
    PlanarFrame,
    canonical_planar_frame,
    planarity_threshold,
)

partition_meta = {
    "mode": ParamMeta(
        kind="choice",
        choices=("merge", "group", "ring"),
        description="入力リングを統合、穴を含む領域単位、または個別リングとして分割する。",
    ),
    "site_count": ParamMeta(
        kind="int",
        ui_min=1,
        ui_max=500,
        description="領域をボロノイ分割するために配置するサイトの数。",
    ),
    # imgui.slider_int は内部で「min/max が int32 の半分レンジ以内」を要求するため、
    # GUI 用レンジは控えめにし、必要ならコード側で任意の seed を指定する。
    "seed": ParamMeta(
        kind="int",
        ui_min=0,
        ui_max=1_073_741_823,
        description="ボロノイサイトの配置を再現可能にする乱数シード。",
    ),
    "site_density_base": ParamMeta(
        kind="vec3",
        ui_min=0.0,
        ui_max=1.0,
        description=(
            "基準点におけるサイト採用確率を軸ごとに指定する。"
            "勾配も含めた全成分が 0 なら密度制御を無効にする。"
        ),
    ),
    "site_density_slope": ParamMeta(
        kind="vec3",
        ui_min=-1.0,
        ui_max=1.0,
        description=(
            "正規化した各軸位置に対するサイト採用確率の勾配。"
            "基準確率も含めた全成分が 0 なら密度制御を無効にする。"
        ),
    ),
    "auto_center": ParamMeta(
        kind="bool",
        description="入力のバウンディングボックス中心を密度勾配の基準点にする。",
    ),
    "pivot": ParamMeta(
        kind="vec3",
        ui_min=-100.0,
        ui_max=100.0,
        description="自動中心が無効な場合に密度勾配の基準とする点。",
    ),
}

partition_ui_visible = {
    "pivot": lambda v: v.get("auto_center", True) is False,
}

def _ensure_closed_2d(loop: np.ndarray) -> np.ndarray:
    if loop.shape[0] == 0:
        return loop
    if loop.shape[0] >= 2 and np.allclose(loop[0], loop[-1], rtol=0.0, atol=1e-6):
        return loop
    return np.concatenate([loop, loop[:1]], axis=0)


def _collect_polygon_exteriors(geom: BaseGeometry) -> list[np.ndarray]:
    """Shapely geometry から Polygon 外周を ndarray で抽出する（holes は無視）。"""
    try:
        if geom.is_empty:
            return []
    except GEOSException:
        return []

    if isinstance(geom, Polygon):
        coords = np.asarray(geom.exterior.coords, dtype=np.float32)
        return [coords]

    out: list[np.ndarray] = []
    if isinstance(geom, (MultiPolygon, GeometryCollection)):
        for child in geom.geoms:
            out.extend(_collect_polygon_exteriors(child))
    return out


def _combine_evenodd(polys: list[BaseGeometry]) -> BaseGeometry | None:
    if not polys:
        return None

    if len(polys) == 1:
        return polys[0]

    if len(polys) == 2:
        a, b = polys
        if a.geom_type == "Polygon" and b.geom_type == "Polygon" and a.contains(b):
            try:
                return Polygon(a.exterior.coords, holes=[b.exterior.coords])
            except (GEOSException, ValueError):
                return a.symmetric_difference(b)
        if a.geom_type == "Polygon" and b.geom_type == "Polygon" and b.contains(a):
            try:
                return Polygon(b.exterior.coords, holes=[a.exterior.coords])
            except (GEOSException, ValueError):
                return a.symmetric_difference(b)
        if a.disjoint(b):
            return a.union(b)
        return a.symmetric_difference(b)

    region = None
    for poly in polys:
        region = poly if region is None else region.symmetric_difference(poly)
    return region


def _build_evenodd_groups(
    polys: list[BaseGeometry],
    rings_2d: list[np.ndarray],
) -> list[list[int]]:
    """外周＋穴を even-odd でグルーピングし、[outer, hole...] のインデックス列を返す。"""
    n = int(len(polys))
    if n == 0:
        return []
    if n != int(len(rings_2d)):
        raise ValueError("polys と rings_2d のサイズが一致しない")

    rep_pts = [(float(ring[0, 0]), float(ring[0, 1])) for ring in rings_2d]
    areas = [float(poly.area) for poly in polys]

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
            except GEOSException:
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
            except GEOSException:
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


def _polygon_inputs(
    coords_2d: np.ndarray,
    offsets: np.ndarray,
) -> tuple[list[np.ndarray], list[BaseGeometry]]:
    """packed input から有効な閉 ring と polygon を入力順に作る。"""

    rings: list[np.ndarray] = []
    polygons: list[BaseGeometry] = []
    for line_index in range(int(offsets.size) - 1):
        start = int(offsets[line_index])
        end = int(offsets[line_index + 1])
        ring = coords_2d[start:end]
        if ring.shape[0] < 3:
            continue
        closed_ring = _ensure_closed_2d(ring)
        try:
            polygon = Polygon(closed_ring)
            if not polygon.is_valid:
                polygon = polygon.buffer(0)
        except (GEOSException, ValueError):
            continue
        if polygon.is_empty:
            continue
        rings.append(closed_ring)
        polygons.append(polygon)
    return rings, polygons


def _partition_regions(
    polygons: list[BaseGeometry],
    rings: list[np.ndarray],
    *,
    mode: str,
) -> list[BaseGeometry]:
    """mode に従って Voronoi clipping 対象領域を作る。"""

    if mode == "ring":
        return list(polygons)
    if mode == "group":
        regions: list[BaseGeometry] = []
        for group_indices in _build_evenodd_groups(polygons, rings):
            region = _combine_evenodd([polygons[index] for index in group_indices])
            if region is not None and not region.is_empty:
                regions.append(region)
        return regions
    region = _combine_evenodd(polygons)
    if region is None or region.is_empty:
        return []
    return [region]


def _density_space(
    coords: np.ndarray,
    *,
    auto_center: bool,
    pivot: tuple[float, float, float],
) -> tuple[np.ndarray, np.ndarray]:
    """density 勾配の pivot と正規化用逆 bbox 半径を返す。"""

    mins = np.min(coords, axis=0).astype(np.float64, copy=False)
    maxs = np.max(coords, axis=0).astype(np.float64, copy=False)
    bbox_center = (mins + maxs) * 0.5
    extent = (maxs - mins) * 0.5
    inv_extent = np.zeros((3,), dtype=np.float64)
    for axis in range(3):
        half_extent = float(extent[axis])
        inv_extent[axis] = 0.0 if half_extent < 1e-9 else 1.0 / half_extent
    pivot_3d = bbox_center if auto_center else np.asarray(pivot, dtype=np.float64)
    return pivot_3d, inv_extent


def _density_probabilities(
    xy: np.ndarray,
    *,
    frame: PlanarFrame,
    pivot: np.ndarray,
    inv_extent: np.ndarray,
    base: tuple[float, float, float],
    slope: tuple[float, float, float],
) -> np.ndarray:
    """平面上の候補点ごとの合成採用確率を返す。"""

    points_3d = frame.lift(xy)
    normalized = (points_3d - pivot[None, :]) * inv_extent[None, :]
    normalized = np.clip(normalized, -1.0, 1.0)
    p_x = np.clip(base[0] + slope[0] * normalized[:, 0], 0.0, 1.0)
    p_y = np.clip(base[1] + slope[1] * normalized[:, 1], 0.0, 1.0)
    p_z = np.clip(base[2] + slope[2] * normalized[:, 2], 0.0, 1.0)
    return 1.0 - (1.0 - p_x) * (1.0 - p_y) * (1.0 - p_z)


def _sample_region_sites(
    region: BaseGeometry,
    *,
    site_count: int,
    rng: np.random.Generator,
    frame: PlanarFrame,
    density_enabled: bool,
    density_pivot: np.ndarray,
    density_inv_extent: np.ndarray,
    density_base: tuple[float, float, float],
    density_slope: tuple[float, float, float],
) -> list[tuple[float, float]]:
    """region 内の Voronoi site を density 採用と一様 top-up で作る。"""

    min_x, min_y, max_x, max_y = region.bounds
    width = float(max_x) - float(min_x)
    height = float(max_y) - float(min_y)
    points: list[tuple[float, float]] = []
    if width > 0.0 and height > 0.0:
        trials_per_phase = max(1000, site_count * 50)
        batch_size = max(256, site_count * 20)

        def append_points(xs: np.ndarray, ys: np.ndarray) -> None:
            need = site_count - len(points)
            if need <= 0:
                return
            for x, y in zip(xs[:need], ys[:need], strict=False):
                points.append((float(x), float(y)))

        trials_left = trials_per_phase
        while len(points) < site_count and trials_left > 0:
            count = min(batch_size, trials_left)
            xs = float(min_x) + rng.random(count) * width
            ys = float(min_y) + rng.random(count) * height
            inside = shapely.contains_xy(region, xs, ys)
            if not np.any(inside):
                trials_left -= count
                continue
            inside_xs = xs[inside]
            inside_ys = ys[inside]
            if density_enabled:
                xy = np.stack([inside_xs, inside_ys], axis=1).astype(
                    np.float64,
                    copy=False,
                )
                probabilities = _density_probabilities(
                    xy,
                    frame=frame,
                    pivot=density_pivot,
                    inv_extent=density_inv_extent,
                    base=density_base,
                    slope=density_slope,
                )
                take = rng.random(int(probabilities.shape[0])) < probabilities
                append_points(inside_xs[take], inside_ys[take])
            else:
                append_points(inside_xs, inside_ys)
            trials_left -= count

        # density 採用で不足した分だけ、一様サンプリングで同じ順序の top-up を行う。
        if density_enabled and len(points) < site_count:
            trials_left = trials_per_phase
            while len(points) < site_count and trials_left > 0:
                count = min(batch_size, trials_left)
                xs = float(min_x) + rng.random(count) * width
                ys = float(min_y) + rng.random(count) * height
                inside = shapely.contains_xy(region, xs, ys)
                if np.any(inside):
                    append_points(xs[inside], ys[inside])
                trials_left -= count

    if points:
        return points
    try:
        representative = region.representative_point()
    except GEOSException:
        return []
    return [(float(representative.x), float(representative.y))]


def _voronoi_region_loops(
    region: BaseGeometry,
    sites: list[tuple[float, float]],
) -> list[np.ndarray]:
    """site の Voronoi cell を region で clip して外周を返す。"""

    if len(sites) <= 1:
        return _collect_polygon_exteriors(region)
    try:
        diagram = voronoi_diagram(
            MultiPoint(sites),
            envelope=region.envelope,
            edges=False,
        )
    except GEOSException:
        return []
    loops: list[np.ndarray] = []
    for cell in diagram.geoms:
        try:
            intersection = cell.intersection(region)
        except GEOSException:
            continue
        if not intersection.is_empty:
            loops.extend(_collect_polygon_exteriors(intersection))
    return loops


def _pack_partition_loops(
    loops: list[np.ndarray],
    *,
    frame: PlanarFrame,
) -> GeomTuple | None:
    """有効な loop を centroid 順に整列し packed 3D geometry に戻す。"""

    valid_loops = [loop for loop in loops if loop.shape[0] >= 4]
    if not valid_loops:
        return None

    def sort_key(loop: np.ndarray) -> tuple[float, float]:
        center = loop[:-1].astype(np.float64, copy=False).mean(axis=0)
        return float(center[0]), float(center[1])

    valid_loops.sort(key=sort_key)
    lines = [frame.lift(loop[:, :2]) for loop in valid_loops]
    return pack_polylines([line for line in lines if line.shape[0] > 0])


@effect(meta=partition_meta, ui_visible=partition_ui_visible)
def partition(
    g: GeomTuple,
    *,
    mode: str = "merge",
    site_count: int = 12,
    seed: int = 0,
    site_density_base: tuple[float, float, float] = (0.0, 0.0, 0.0),
    site_density_slope: tuple[float, float, float] = (0.0, 0.0, 0.0),
    auto_center: bool = True,
    pivot: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> GeomTuple:
    """偶奇規則の平面領域を Voronoi 分割し、閉ループ群を返す。

    Parameters
    ----------
    g : tuple[np.ndarray, np.ndarray]
        入力の実体ジオメトリ（coords, offsets）。各ポリラインが閉ループ（リング）を表す想定。
    site_count : int, default 12
        Voronoi の正のサイト数。
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
    tuple[np.ndarray, np.ndarray]
        分割セルの外周を並べた実体ジオメトリ（coords, offsets）。

    Notes
    -----
    rank 2 以上かつ最大平面残差が
    ``max(1e-6, 1e-5 * bbox_diagonal)`` 以下の有限入力だけを処理する。
    非共平面または linear な入力は射影せず no-op として返す。
    """
    if site_count <= 0:
        raise ValueError("partition: site_count は正の整数である必要がある")
    if not all(0.0 <= value <= 1.0 for value in site_density_base):
        raise ValueError(
            "partition: site_density_base の各要素は 0.0 以上 1.0 以下である必要がある"
        )
    if seed < 0:
        raise ValueError("partition: seed は 0 以上である必要がある")

    coords, offsets = g
    if coords.shape[0] == 0:
        return coords, offsets
    frame = canonical_planar_frame(coords, offsets)
    if not frame.is_planar(planarity_threshold(coords)):
        return coords, offsets

    rings, polygons = _polygon_inputs(frame.project(coords), offsets)
    if not polygons:
        return coords, offsets
    regions = _partition_regions(polygons, rings, mode=mode)
    if not regions:
        return coords, offsets

    density_enabled = any(
        value != 0.0 for value in (*site_density_base, *site_density_slope)
    )
    if density_enabled:
        density_pivot, density_inv_extent = _density_space(
            coords,
            auto_center=auto_center,
            pivot=pivot,
        )
    else:
        density_pivot = np.zeros((3,), dtype=np.float64)
        density_inv_extent = np.zeros((3,), dtype=np.float64)

    rng = np.random.default_rng(seed)
    loops: list[np.ndarray] = []
    for region in regions:
        sites = _sample_region_sites(
            region,
            site_count=site_count,
            rng=rng,
            frame=frame,
            density_enabled=density_enabled,
            density_pivot=density_pivot,
            density_inv_extent=density_inv_extent,
            density_base=site_density_base,
            density_slope=site_density_slope,
        )
        if sites:
            loops.extend(_voronoi_region_loops(region, sites))

    packed = _pack_partition_loops(loops, frame=frame)
    return (coords, offsets) if packed is None else packed
