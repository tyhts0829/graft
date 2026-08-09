"""仮想点群から閉じたDelaunay三角形領域を生成するPrimitive。"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from shapely import MultiPoint, delaunay_triangles  # type: ignore[import-untyped]
from shapely.errors import GEOSException  # type: ignore[import-untyped]

from grafix.core.geometry_kernels.packed import empty_packed_geometry
from grafix.core.operation_authoring import primitive
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import GeomTuple
from grafix.core.resource_budget import ResourceLimitError, ensure_geometry_output
from grafix.core.value_validation import exact_integer, finite_real

_MAX_SITE_COUNT = 10_000
_MAX_CANDIDATE_DISTANCE_EVALUATIONS = 64_000_000
_FLOAT32_MAX = float(np.finfo(np.float32).max)
_AREA_EPSILON_FACTOR = 64.0


delaunay_meta = {
    "width": ParamMeta(
        kind="float",
        ui_min=1.0,
        ui_max=300.0,
        display_name="Width",
        description="出力faceを残すnominal矩形領域の幅を指定します。",
        unit="mm",
        step=1.0,
        category="Layout",
    ),
    "height": ParamMeta(
        kind="float",
        ui_min=1.0,
        ui_max=300.0,
        display_name="Height",
        description="出力faceを残すnominal矩形領域の高さを指定します。",
        unit="mm",
        step=1.0,
        category="Layout",
    ),
    "site_count": ParamMeta(
        kind="int",
        ui_min=8,
        ui_max=500,
        display_name="Site Count",
        description="元矩形内の目標site数を指定し、仮想siteの面密度を調整します。",
        step=1.0,
        category="Sites",
    ),
    "seed": ParamMeta(
        kind="int",
        ui_min=0,
        ui_max=1_000_000,
        display_name="Site Seed",
        description="仮想siteの配置を決める再現可能な非負のseedです。",
        step=1.0,
        category="Sites",
    ),
    "candidates": ParamMeta(
        kind="int",
        ui_min=1,
        ui_max=32,
        display_name="Candidates",
        description="site追加時に比較する候補数を指定し、点間隔の均一さを調整します。",
        step=1.0,
        category="Sites",
    ),
    "guard_band": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=4.0,
        display_name="Guard Band",
        description="元矩形の各辺へ追加する余白をnominal site pitchの倍数で指定します。",
        step=0.25,
        category="Sites",
    ),
    "center": ParamMeta(
        kind="vec3",
        ui_min=-300.0,
        ui_max=300.0,
        display_name="Center",
        description="nominal矩形と出力面の中心となるXYZ座標を指定します。",
        unit="mm",
        category="Layout",
    ),
}


def _positive_finite(value: object, *, name: str) -> float:
    """正の有限な実数を検証して返す。"""

    return finite_real(
        value,
        name=f"delaunay: {name}",
        minimum=0.0,
        minimum_inclusive=False,
    )


def _nonnegative_finite(value: object, *, name: str) -> float:
    """0以上の有限な実数を検証して返す。"""

    return finite_real(value, name=f"delaunay: {name}", minimum=0.0)


def _integer_at_least(value: object, *, name: str, minimum: int) -> int:
    """boolを除く下限付き整数を検証して返す。"""

    return exact_integer(value, name=f"delaunay: {name}", minimum=minimum)


def _center_value(value: object) -> tuple[float, float, float]:
    """有限な3成分tupleを中心座標として返す。"""

    if type(value) is not tuple or len(value) != 3:
        raise TypeError("delaunay: center は3要素のtupleである必要があります")
    return tuple(
        finite_real(component, name=f"delaunay: center[{index}]")
        for index, component in enumerate(value)
    )  # type: ignore[return-value]


def _validate_float32_domain(
    *,
    width: float,
    height: float,
    center: tuple[float, float, float],
) -> None:
    """散布領域が有限なfloat32平面として表現できることを検査する。"""

    cx, cy, cz = center
    bounds = (
        cx - 0.5 * width,
        cx + 0.5 * width,
        cy - 0.5 * height,
        cy + 0.5 * height,
        cz,
    )
    values = (width, height, *bounds)
    if any(not math.isfinite(value) or abs(value) > _FLOAT32_MAX for value in values):
        raise ValueError(
            "delaunay: width、height、出力boundsはfloat32の有限範囲内である必要があります"
        )

    bounds32 = np.asarray(bounds, dtype=np.float32)
    if bounds32[0] == bounds32[1] or bounds32[2] == bounds32[3]:
        raise ValueError(
            "delaunay: widthまたはheightが出力位置でfloat32座標として表現できません"
        )


def _sampling_scratch_bytes(*, site_count: int, candidates: int) -> int:
    """site生成とfloat32量子化で同時に保持する配列の概算byte数を返す。"""

    sites_bytes = site_count * 2 * 8
    quantized_sites_bytes = site_count * 2 * 4
    quantization_peak = 2 * sites_bytes + quantized_sites_bytes
    if candidates == 1:
        return quantization_peak
    previous_count = site_count - 1
    candidate_bytes = candidates * 2 * 8
    squared_delta_bytes = candidates * previous_count * 2 * 8
    distances_bytes = candidates * previous_count * 8
    minima_bytes = candidates * 8
    sampling_peak = (
        sites_bytes
        + candidate_bytes
        + squared_delta_bytes
        + distances_bytes
        + minima_bytes
    )
    return max(sampling_peak, quantization_peak)


def _face_filter_scratch_bytes(*, face_count: int) -> int:
    """bounds内faceの選別に使うboolean配列のpeak byte数を返す。"""

    # vertex比較 (T, 3)、axis集約 (T,)、累積mask (T,) を同時に保持する。
    return face_count * 5


@dataclass(frozen=True, slots=True)
class _SamplingPlan:
    """guard bandを含むsite散布計画。"""

    nominal_pitch: float
    guard_margin: float
    expanded_width: float
    expanded_height: float
    expanded_site_count: int


def _sampling_plan(
    *,
    width: float,
    height: float,
    site_count: int,
    guard_band: float,
) -> _SamplingPlan:
    """nominal密度を保ったguard band付きsite散布計画を返す。"""

    nominal_area = width * height
    nominal_pitch = math.sqrt(nominal_area / site_count)
    guard_margin = guard_band * nominal_pitch
    expanded_width = width + 2.0 * guard_margin
    expanded_height = height + 2.0 * guard_margin
    values = (nominal_pitch, guard_margin, expanded_width, expanded_height)
    if any(not math.isfinite(value) for value in values):
        raise ValueError(
            "delaunay: guard_bandによる拡張領域は有限である必要があります"
        )

    if guard_band == 0.0:
        expanded_site_count = site_count
    else:
        expanded_area = expanded_width * expanded_height
        estimated_count = site_count * expanded_area / nominal_area
        if not math.isfinite(estimated_count):
            raise ResourceLimitError(
                "delaunay: expanded_site_countが有限範囲を超えています"
            )
        expanded_site_count = math.ceil(estimated_count)

    return _SamplingPlan(
        nominal_pitch=nominal_pitch,
        guard_margin=guard_margin,
        expanded_width=expanded_width,
        expanded_height=expanded_height,
        expanded_site_count=expanded_site_count,
    )


def _best_candidate_sites(
    *,
    width: float,
    height: float,
    site_count: int,
    seed: int,
    candidates: int,
) -> np.ndarray:
    """矩形内へprefix-stableなbest-candidate site列を生成する。"""

    rng = np.random.default_rng(seed)
    scale = np.asarray((width, height), dtype=np.float64)
    lower = -0.5 * scale

    if candidates == 1:
        sites = rng.random((site_count, 2), dtype=np.float64)
        sites *= scale
        sites += lower
        return sites

    sites = np.empty((site_count, 2), dtype=np.float64)
    sites[0] = rng.random(2, dtype=np.float64) * scale + lower
    for index in range(1, site_count):
        candidate_points = rng.random((candidates, 2), dtype=np.float64)
        candidate_points *= scale
        candidate_points += lower

        squared_delta = candidate_points[:, None, :] - sites[None, :index, :]
        np.square(squared_delta, out=squared_delta)
        squared_distances = squared_delta.sum(axis=2)
        nearest_squared_distance = squared_distances.min(axis=1)
        sites[index] = candidate_points[int(np.argmax(nearest_squared_distance))]
    return sites


def _empty_faces() -> np.ndarray:
    """標準shapeの空face配列を返す。"""

    return np.empty((0, 3, 2), dtype=np.float64)


def _canonical_triangle(
    vertices: np.ndarray,
    *,
    twice_area_threshold: float,
) -> tuple[tuple[float, ...], np.ndarray] | None:
    """三頂点をCCWかつ辞書順最小頂点始点へ正規化する。"""

    triangle = np.asarray(vertices, dtype=np.float64)
    if triangle.shape != (3, 2) or not np.isfinite(triangle).all():
        return None
    triangle = triangle.copy()
    triangle[triangle == 0.0] = 0.0

    vertex_keys = {
        (float(vertex[0]), float(vertex[1])) for vertex in triangle
    }
    if len(vertex_keys) != 3:
        return None

    edge_a = triangle[1] - triangle[0]
    edge_b = triangle[2] - triangle[0]
    twice_area = float(edge_a[0] * edge_b[1] - edge_a[1] * edge_b[0])
    if not math.isfinite(twice_area) or abs(twice_area) <= twice_area_threshold:
        return None
    if twice_area < 0.0:
        triangle = triangle[[0, 2, 1]]

    start = min(
        range(3),
        key=lambda index: (float(triangle[index, 0]), float(triangle[index, 1])),
    )
    triangle = np.roll(triangle, -start, axis=0)
    key = tuple(float(value) for value in triangle.ravel())
    return key, triangle


def _canonical_delaunay_faces(sites: np.ndarray) -> np.ndarray:
    """site列を決定的なCCW Delaunay三角形列へ変換する。

    Parameters
    ----------
    sites : np.ndarray
        shape ``(N, 2)`` の有限なXY site列。入力順と完全一致する重複は
        triangulation結果へ影響しない。

    Returns
    -------
    np.ndarray
        shape ``(T, 3, 2)`` のfloat64配列。各faceはCCWで辞書順最小の
        頂点から始まり、face列もcanonical key順に並ぶ。
    """

    points = np.asarray(sites, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 2:
        raise ValueError("delaunay: sites はshape (N, 2)である必要があります")
    if not np.isfinite(points).all():
        raise ValueError("delaunay: sites は有限値だけを含む必要があります")
    if points.shape[0] < 3:
        return _empty_faces()

    points = points.copy()
    points[points == 0.0] = 0.0
    order = np.lexsort((points[:, 1], points[:, 0]))
    ordered = points[order]
    keep = np.ones((ordered.shape[0],), dtype=np.bool_)
    keep[1:] = np.any(ordered[1:] != ordered[:-1], axis=1)
    unique_points = ordered[keep]
    if unique_points.shape[0] < 3:
        return _empty_faces()

    span_x = float(np.ptp(unique_points[:, 0]))
    span_y = float(np.ptp(unique_points[:, 1]))
    scale = max(span_x, span_y)
    if not math.isfinite(scale) or scale <= 0.0:
        return _empty_faces()
    twice_area_threshold = (
        _AREA_EPSILON_FACTOR * float(np.finfo(np.float64).eps) * scale * scale
    )

    try:
        result = delaunay_triangles(
            MultiPoint(unique_points),
            tolerance=0.0,
            only_edges=False,
        )
    except GEOSException as exc:
        raise ValueError("delaunay: GEOSがsite群を三角形分割できませんでした") from exc

    if result.geom_type != "GeometryCollection":
        raise ValueError("delaunay: GEOSが予期しないDelaunay geometryを返しました")

    by_key: dict[tuple[float, ...], np.ndarray] = {}
    for polygon in result.geoms:
        if polygon.geom_type != "Polygon":
            raise ValueError("delaunay: GEOSが三角形以外のgeometryを返しました")
        ring = np.asarray(polygon.exterior.coords, dtype=np.float64)
        if ring.shape != (4, 2) or not np.array_equal(ring[0], ring[-1]):
            raise ValueError("delaunay: GEOSが三頂点でないfaceを返しました")
        canonical = _canonical_triangle(
            ring[:3],
            twice_area_threshold=twice_area_threshold,
        )
        if canonical is not None:
            key, triangle = canonical
            by_key.setdefault(key, triangle)

    if not by_key:
        return _empty_faces()
    return np.stack([by_key[key] for key in sorted(by_key)], axis=0)


def _float32_sites(
    sites: np.ndarray,
    *,
    center: tuple[float, float, float],
) -> np.ndarray:
    """siteを出力平面へ移し、Geometryと同じfloat32精度へ量子化する。"""

    translated = sites + np.asarray(center[:2], dtype=np.float64)
    return np.ascontiguousarray(translated, dtype=np.float32)


def _faces_inside_bounds(
    faces: np.ndarray,
    *,
    width: float,
    height: float,
    center: tuple[float, float, float],
) -> np.ndarray:
    """三頂点すべてがnominal矩形内にあるfaceだけを返す。"""

    face_array = np.asarray(faces)
    if face_array.ndim != 3 or face_array.shape[1:] != (3, 2):
        raise ValueError("delaunay: faces はshape (T, 3, 2)である必要があります")
    if face_array.shape[0] == 0:
        return face_array.copy()

    cx, cy, _ = center
    bounds = np.asarray(
        (
            cx - 0.5 * width,
            cx + 0.5 * width,
            cy - 0.5 * height,
            cy + 0.5 * height,
        ),
        dtype=np.float32,
    )
    inside = np.ones((face_array.shape[0],), dtype=np.bool_)
    inside &= np.all(face_array[:, :, 0] >= bounds[0], axis=1)
    inside &= np.all(face_array[:, :, 0] <= bounds[1], axis=1)
    inside &= np.all(face_array[:, :, 1] >= bounds[2], axis=1)
    inside &= np.all(face_array[:, :, 1] <= bounds[3], axis=1)
    return np.ascontiguousarray(face_array[inside])


@primitive(meta=delaunay_meta)
def delaunay(
    *,
    width: float = 80.0,
    height: float = 100.0,
    site_count: int = 48,
    seed: int = 0,
    candidates: int = 8,
    guard_band: float = 2.0,
    center: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> GeomTuple:
    """仮想site群から独立した閉Delaunay三角形領域を生成する。

    Parameters
    ----------
    width, height : float, optional
        出力faceを残すnominal矩形領域の幅と高さ。
    site_count : int, optional
        元矩形内に対する目標site数。3以上を指定する。
    seed : int, optional
        site配置を再現する非負整数。
    candidates : int, optional
        二つ目以降のsiteごとに比較する候補数。1では一様乱数散布となり、
        大きいほどsite間隔が均一になる。
    guard_band : float, optional
        元矩形の各辺へ追加するguard幅。nominal site pitchの倍数で指定する。
    center : tuple[float, float, float], optional
        nominal矩形と出力面の中心となる ``(x, y, z)`` 座標。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        各三角形をCCWの閉polyline ``[a, b, c, a]`` として詰めたGeometry。

    Notes
    -----
    拡張矩形へ元矩形と同じ目標密度でsiteを散布し、三頂点すべてが元矩形内に
    あるfaceだけを残す。出力輪郭は矩形より内側へ後退してよく、矩形全域の被覆は
    保証しない。低い ``site_count`` でfaceが残らない場合は空Geometryを返す。
    siteは出力Geometryと同じfloat32精度へ量子化してから三角形分割するため、
    大きな ``center`` でも丸め後の座標上でface同士が面積を持って重ならない。
    隣接faceの共有辺は、それぞれの閉polylineへ一度ずつ含まれる。境界線を除いて
    各領域をハッチングする場合は ``E.fill(remove_boundary=True)`` と合成する。
    矩形四隅はsiteへ自動追加しない。
    """

    width_f = _positive_finite(width, name="width")
    height_f = _positive_finite(height, name="height")
    site_count_i = _integer_at_least(site_count, name="site_count", minimum=3)
    if site_count_i > _MAX_SITE_COUNT:
        raise ResourceLimitError(
            "delaunay: site_countが処理上限を超えています: "
            f"{site_count_i:,} > {_MAX_SITE_COUNT:,}"
        )
    seed_i = _integer_at_least(seed, name="seed", minimum=0)
    candidates_i = _integer_at_least(candidates, name="candidates", minimum=1)
    guard_band_f = _nonnegative_finite(guard_band, name="guard_band")
    center_f = _center_value(center)
    _validate_float32_domain(width=width_f, height=height_f, center=center_f)

    sampling_plan = _sampling_plan(
        width=width_f,
        height=height_f,
        site_count=site_count_i,
        guard_band=guard_band_f,
    )
    _validate_float32_domain(
        width=sampling_plan.expanded_width,
        height=sampling_plan.expanded_height,
        center=center_f,
    )
    expanded_site_count = sampling_plan.expanded_site_count
    if expanded_site_count > _MAX_SITE_COUNT:
        raise ResourceLimitError(
            "delaunay: expanded_site_countが処理上限を超えています: "
            f"{expanded_site_count:,} > {_MAX_SITE_COUNT:,}; "
            "site_countまたはguard_bandを減らしてください"
        )

    candidate_work = (
        0
        if candidates_i == 1
        else candidates_i * expanded_site_count * (expanded_site_count - 1) // 2
    )
    if candidates_i > 1 and candidate_work > _MAX_CANDIDATE_DISTANCE_EVALUATIONS:
        raise ResourceLimitError(
            "delaunay: candidate距離評価量が処理上限を超えています: "
            f"{candidate_work:,} > {_MAX_CANDIDATE_DISTANCE_EVALUATIONS:,}; "
            "site_count、candidates、guard_bandのいずれかを減らしてください"
        )

    maximum_face_count = 2 * expanded_site_count - 5
    ensure_geometry_output(
        "delaunay",
        vertices=4 * maximum_face_count,
        lines=maximum_face_count,
        scratch_bytes=_sampling_scratch_bytes(
            site_count=expanded_site_count,
            candidates=candidates_i,
        )
        + _face_filter_scratch_bytes(face_count=maximum_face_count),
        hint="site_count、candidates、guard_bandのいずれかを減らしてください",
    )

    sites = _best_candidate_sites(
        width=sampling_plan.expanded_width,
        height=sampling_plan.expanded_height,
        site_count=expanded_site_count,
        seed=seed_i,
        candidates=candidates_i,
    )
    output_sites = _float32_sites(sites, center=center_f)
    faces = _canonical_delaunay_faces(output_sites)
    faces = _faces_inside_bounds(
        faces,
        width=width_f,
        height=height_f,
        center=center_f,
    )
    faces32 = np.ascontiguousarray(faces, dtype=np.float32)
    face_count = int(faces32.shape[0])
    if face_count == 0:
        return empty_packed_geometry()

    ensure_geometry_output(
        "delaunay",
        vertices=4 * face_count,
        lines=face_count,
    )
    coords_by_face = np.empty((face_count, 4, 3), dtype=np.float32)
    coords_by_face[:, :3, :2] = faces32
    coords_by_face[:, :3, 2] = np.float32(center_f[2])
    coords_by_face[:, 3] = coords_by_face[:, 0]
    coords = coords_by_face.reshape((-1, 3))
    offsets = np.arange(0, 4 * face_count + 1, 4, dtype=np.int32)
    return coords, offsets


__all__ = ["delaunay", "delaunay_meta"]
