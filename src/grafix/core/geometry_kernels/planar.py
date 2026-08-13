"""
Purpose:
    3D geometryと局所XY平面の変換、および閉ring抽出をplanar effect間で共有する。
Use when:
    平面性判定、3D姿勢の復元、ring領域処理、またはcanonical frameが必要な場合。
Constraints:
    - world座標とlocal座標を混同せず、同じPlanarFrameでproject/liftを対にする。
    - canonical_planar_frameのwinding、seam、line順に依存しないidentityを維持する。
    - region処理へ渡すringは明示的に閉じ、open curveを暗黙の領域として扱わない。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

_PLANAR_EPS_ABS = 1e-6
_PLANAR_EPS_REL = 1e-5


@dataclass(frozen=True, slots=True)
class PlanarRing:
    """平面化済みの閉曲線と、その二次元境界を保持する。

    ``vertices`` は先頭点と末尾点が一致する ``(N, 2)`` float64 配列、
    ``mins`` と ``maxs`` はその ``(2,)`` float64 境界である。
    """

    vertices: np.ndarray
    mins: np.ndarray
    maxs: np.ndarray


def planarity_threshold(points: np.ndarray) -> float:
    """点群の大きさに応じた平面性判定の許容値を返す。"""

    if points.size == 0:
        return float(_PLANAR_EPS_ABS)
    p = points.astype(np.float64, copy=False)
    mins = np.min(p, axis=0)
    maxs = np.max(p, axis=0)
    diag = float(np.linalg.norm(maxs - mins))
    return max(float(_PLANAR_EPS_ABS), float(_PLANAR_EPS_REL) * diag)


def close_curve(points: np.ndarray, threshold: float) -> np.ndarray:
    """端点間の距離が閾値以内なら、終点を始点へ揃える。"""

    if points.shape[0] < 2:
        return points
    dist = float(np.linalg.norm(points[0] - points[-1]))
    if dist <= float(threshold):
        return np.concatenate([points[:-1], points[0:1]], axis=0)
    return points


def extract_planar_rings(
    coords: np.ndarray,
    offsets: np.ndarray,
    *,
    auto_close_threshold: float,
) -> list[PlanarRing]:
    """平面化済みのpacked geometryから閉曲線だけを入力順に抽出する。"""

    rings: list[PlanarRing] = []
    for i in range(int(offsets.size) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        poly3 = coords[s:e]
        if poly3.shape[0] < 3:
            continue

        closed3 = close_curve(poly3, float(auto_close_threshold))
        if closed3.shape[0] < 4:
            continue
        if not np.allclose(closed3[0], closed3[-1], rtol=0.0, atol=1e-12):
            continue

        vertices = closed3[:, :2].astype(np.float64, copy=False)
        rings.append(
            PlanarRing(
                vertices=vertices,
                mins=np.min(vertices, axis=0),
                maxs=np.max(vertices, axis=0),
            )
        )
    return rings


def pack_planar_rings(
    rings: list[PlanarRing],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """閉曲線列をNumbaへ渡す連結バッファへ入力順に詰める。"""

    n = len(rings)
    total = 0
    for ring in rings:
        total += int(ring.vertices.shape[0])

    ring_vertices = np.empty((total, 2), dtype=np.float64)
    ring_offsets = np.empty((n + 1,), dtype=np.int32)
    ring_mins = np.empty((n, 2), dtype=np.float64)
    ring_maxs = np.empty((n, 2), dtype=np.float64)

    ring_offsets[0] = 0
    cursor = 0
    for i, ring in enumerate(rings):
        vertices = ring.vertices.astype(np.float64, copy=False)
        count = int(vertices.shape[0])
        ring_vertices[cursor : cursor + count] = vertices
        cursor += count
        ring_offsets[i + 1] = np.int32(cursor)
        ring_mins[i] = ring.mins
        ring_maxs[i] = ring.maxs

    return ring_vertices, ring_offsets, ring_mins, ring_maxs

def _readonly_vector(value: np.ndarray, *, shape: tuple[int, ...]) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64).reshape(shape).copy()
    array.setflags(write=False)
    return array


def _invalid_planar_frame(*, status: str, rank: int, origin: np.ndarray | None = None) -> PlanarFrame:
    origin_array = np.zeros((3,), dtype=np.float64) if origin is None else origin
    invalid = np.full((3, 3), np.nan, dtype=np.float64)
    return PlanarFrame(
        origin=origin_array,
        basis=invalid,
        inverse=invalid,
        residual=float("inf"),
        rank=int(rank),
        status=status,
    )


def _frame_close_tolerance(points: np.ndarray) -> float:
    anchor = points[0]
    extent = float(np.max(np.abs(points - anchor))) if points.shape[0] else 0.0
    magnitude = float(np.max(np.abs(points))) if points.shape[0] else 0.0
    epsilon = float(np.finfo(np.float64).eps)
    tiny = float(np.finfo(np.float64).tiny)
    rounding = epsilon * max(magnitude, extent, tiny) * 32.0
    return max(extent * 1e-12, rounding)


def _clean_frame_lines(
    points: np.ndarray, offsets: np.ndarray | None, *, tolerance: float
) -> list[np.ndarray]:
    ranges: tuple[tuple[int, int], ...]
    if offsets is None:
        ranges = ((0, int(points.shape[0])),)
    else:
        ranges = tuple(
            (int(offsets[index]), int(offsets[index + 1]))
            for index in range(int(offsets.size) - 1)
        )

    lines: list[np.ndarray] = []
    tolerance_sq = float(tolerance) * float(tolerance)
    for start, stop in ranges:
        line = points[start:stop]
        if line.shape[0] == 0:
            continue
        if line.shape[0] > 1:
            delta = np.diff(line, axis=0)
            keep = np.ones((line.shape[0],), dtype=bool)
            keep[1:] = np.sum(delta * delta, axis=1) > tolerance_sq
            line = line[keep]
        if line.shape[0] > 1:
            close_delta = line[-1] - line[0]
            if float(np.dot(close_delta, close_delta)) <= tolerance_sq:
                line = line[:-1]
        if line.shape[0] > 0:
            lines.append(line)
    return lines


def _packed_clean_frame_offsets(
    points: np.ndarray,
    offsets: np.ndarray | None,
    *,
    tolerance: float,
) -> np.ndarray | None:
    """clean な packed 入力なら、追加の line 配列を作らず使える offsets を返す。"""

    point_count = int(points.shape[0])
    if offsets is None:
        packed_offsets = np.asarray([0, point_count], dtype=np.intp)
    else:
        source_offsets = np.asarray(offsets)
        if (
            source_offsets.ndim != 1
            or source_offsets.size < 2
            or not np.issubdtype(source_offsets.dtype, np.integer)
        ):
            return None
        packed_offsets = source_offsets.astype(np.intp, copy=False)

    if (
        int(packed_offsets[0]) != 0
        or int(packed_offsets[-1]) != point_count
    ):
        return None
    counts = np.diff(packed_offsets)
    if bool(np.any(counts <= 0)):
        return None

    tolerance_sq = float(tolerance) * float(tolerance)
    multi_point = counts > 1
    if bool(np.any(multi_point)):
        starts = packed_offsets[:-1][multi_point]
        stops = packed_offsets[1:][multi_point]
        close_delta = points[stops - 1] - points[starts]
        close_distance_sq = np.sum(close_delta * close_delta, axis=1)
        if bool(np.any(close_distance_sq <= tolerance_sq)):
            return None

    if point_count > 1:
        delta = points[1:] - points[:-1]
        distance_sq = np.sum(delta * delta, axis=1)
        boundaries = packed_offsets[1:-1] - 1
        if boundaries.size:
            distance_sq[boundaries] = np.inf
        if bool(np.any(distance_sq <= tolerance_sq)):
            return None
    return packed_offsets


def _newell_vector(points: np.ndarray, *, scale: float) -> np.ndarray:
    if points.shape[0] < 3:
        return np.zeros((3,), dtype=np.float64)
    local = (points - points[0]) / float(scale)
    following = np.roll(local, -1, axis=0)
    return np.sum(np.cross(local, following), axis=0).astype(np.float64, copy=False)


def _packed_newell_statistics(
    points: np.ndarray,
    offsets: np.ndarray,
    *,
    scale: float,
) -> tuple[np.ndarray, int, np.ndarray]:
    """packed line 群の Newell 合計・最大面積 line・そのベクトルを返す。"""

    line_count = int(offsets.size) - 1
    if line_count <= 64:
        total = np.zeros((3,), dtype=np.float64)
        reference_index = 0
        reference = np.zeros((3,), dtype=np.float64)
        reference_area = -1.0
        for index in range(line_count):
            start = int(offsets[index])
            stop = int(offsets[index + 1])
            newell = _newell_vector(points[start:stop], scale=scale)
            area = float(np.linalg.norm(newell))
            total += newell
            if area > reference_area:
                reference_index = index
                reference = newell
                reference_area = area
        return total, reference_index, reference

    counts = np.diff(offsets)
    if not bool(np.any(counts >= 3)):
        return (
            np.zeros((3,), dtype=np.float64),
            0,
            np.zeros((3,), dtype=np.float64),
        )

    anchors = np.repeat(points[offsets[:-1]], counts, axis=0)
    local = (points - anchors) / float(scale)
    contributions = np.zeros_like(local)
    contributions[:-1] = np.cross(local[:-1], local[1:])
    contributions[offsets[1:] - 1] = 0.0
    newells = np.add.reduceat(contributions, offsets[:-1], axis=0)
    newells[counts < 3] = 0.0

    areas = np.linalg.norm(newells, axis=1)
    reference_index = int(np.argmax(areas))
    return (
        np.sum(newells, axis=0).astype(np.float64, copy=False),
        reference_index,
        newells[reference_index],
    )


def _first_projected_packed_edge(
    points: np.ndarray,
    offsets: np.ndarray,
    *,
    normal: np.ndarray,
    reference_index: int,
    tolerance: float,
) -> np.ndarray | None:
    """reference line 優先のまま、packed buffer から最初の有効 edge を探す。"""

    line_count = int(offsets.size) - 1
    for order_index in range(line_count):
        line_index = (
            int(reference_index)
            if order_index == 0
            else order_index - 1
            if order_index <= int(reference_index)
            else order_index
        )
        start = int(offsets[line_index])
        stop = int(offsets[line_index + 1])
        for point_index in range(start, stop - 1):
            edge = points[point_index + 1] - points[point_index]
            projected = edge - float(np.dot(edge, normal)) * normal
            length = float(np.linalg.norm(projected))
            if length > float(tolerance):
                return projected / length
    return None


def _canonicalize_direction(vector: np.ndarray) -> np.ndarray:
    result = np.asarray(vector, dtype=np.float64)
    index = int(np.argmax(np.abs(result)))
    if float(result[index]) < 0.0:
        result = -result
    return result


def _stable_argmax(values: np.ndarray) -> int:
    """丸め誤差内の同率候補を小さい index 優先で選ぶ。"""

    array = np.asarray(values, dtype=np.float64)
    maximum = float(np.max(array))
    tolerance = max(
        1e-15,
        abs(maximum) * 1e-12,
        float(np.finfo(np.float64).eps) * 64.0,
    )
    for index, value in enumerate(array):
        if maximum - float(value) <= tolerance:
            return int(index)
    return int(np.argmax(array))


def _stable_canonicalize_direction(vector: np.ndarray) -> np.ndarray:
    """最大成分が丸め同率でも符号を決定的に固定する。"""

    result = np.asarray(vector, dtype=np.float64)
    index = _stable_argmax(np.abs(result))
    if float(result[index]) < 0.0:
        result = -result
    return result


def _cross3(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """3要素ベクトル同士の外積を返す。"""

    return np.asarray(
        (
            left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0],
        ),
        dtype=np.float64,
    )


@dataclass(frozen=True, slots=True)
class PlanarFrame:
    """全点から推定した、world座標と局所XY平面を結ぶ直交frame。"""

    origin: np.ndarray
    basis: np.ndarray
    inverse: np.ndarray
    residual: float
    rank: int
    status: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "origin", _readonly_vector(self.origin, shape=(3,)))
        object.__setattr__(self, "basis", _readonly_vector(self.basis, shape=(3, 3)))
        object.__setattr__(self, "inverse", _readonly_vector(self.inverse, shape=(3, 3)))

    @property
    def valid(self) -> bool:
        """2D平面を定義できるrankならTrue。"""

        return self.rank >= 2 and self.status in {"planar", "spatial"}

    @property
    def normal(self) -> np.ndarray:
        """world座標系の単位法線を返す。"""

        return self.basis[2]

    def is_planar(self, tolerance: float) -> bool:
        """有効かつ最大平面残差がtolerance以下ならTrue。"""

        threshold = float(tolerance)
        return self.valid and math.isfinite(threshold) and threshold >= 0.0 and self.residual <= threshold

    def to_local(self, points: np.ndarray) -> np.ndarray:
        """world座標をframeの局所座標へ変換する。"""

        if not self.valid:
            raise ValueError(f"無効な PlanarFrame は変換に使えない: {self.status}")
        values = np.asarray(points, dtype=np.float64)
        if values.shape[-1] != 3:
            raise ValueError("points は末尾shapeが3である必要がある")
        return (values - self.origin) @ self.inverse

    def to_world(self, points: np.ndarray) -> np.ndarray:
        """frameの局所座標をworld座標へ戻す。"""

        if not self.valid:
            raise ValueError(f"無効な PlanarFrame は変換に使えない: {self.status}")
        values = np.asarray(points, dtype=np.float64)
        if values.shape[-1] != 3:
            raise ValueError("points は末尾shapeが3である必要がある")
        return values @ self.basis + self.origin

    def project(self, points: np.ndarray) -> np.ndarray:
        """world座標をframeの局所XYへ射影する。"""

        if not self.valid:
            raise ValueError(f"無効な PlanarFrame は変換に使えない: {self.status}")
        values = np.asarray(points, dtype=np.float64)
        if values.shape[-1] != 3:
            raise ValueError("points は末尾shapeが3である必要がある")
        return (values - self.origin) @ self.inverse[:, :2]

    def lift(self, points: np.ndarray) -> np.ndarray:
        """frameの局所XY座標をworld座標へ持ち上げる。"""

        if not self.valid:
            raise ValueError(f"無効な PlanarFrame は変換に使えない: {self.status}")
        values = np.asarray(points, dtype=np.float64)
        if values.shape[-1] != 2:
            raise ValueError("points は末尾shapeが2である必要がある")
        return values @ self.basis[:2] + self.origin

    @classmethod
    def from_points(
        cls, points: np.ndarray, offsets: np.ndarray | None = None
    ) -> PlanarFrame:
        """全点PCAとringのNewell法から決定的なframeを推定する。"""

        values = np.asarray(points, dtype=np.float64)
        if values.ndim != 2 or values.shape[1] != 3:
            raise ValueError("points は shape (N,3) である必要がある")
        if values.shape[0] == 0:
            return _invalid_planar_frame(status="empty", rank=0)
        if not bool(np.all(np.isfinite(values))):
            return _invalid_planar_frame(status="nonfinite", rank=0)

        tolerance = _frame_close_tolerance(values)
        packed_offsets = _packed_clean_frame_offsets(
            values,
            offsets,
            tolerance=tolerance,
        )
        if packed_offsets is None:
            lines = _clean_frame_lines(values, offsets, tolerance=tolerance)
            if not lines:
                return _invalid_planar_frame(status="empty", rank=0)
            samples = np.concatenate(lines, axis=0)
        else:
            lines = None
            samples = values

        anchor = samples[0]
        relative = samples - anchor
        origin = anchor + np.mean(relative, axis=0)
        centered = relative - np.mean(relative, axis=0)
        scale = float(np.max(np.linalg.norm(centered, axis=1)))
        if not math.isfinite(scale) or scale <= tolerance:
            return _invalid_planar_frame(status="point", rank=0, origin=origin)

        normalized = centered / scale
        covariance = (normalized.T @ normalized) / float(max(1, normalized.shape[0]))
        try:
            eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        except np.linalg.LinAlgError:
            return _invalid_planar_frame(status="nonfinite", rank=0, origin=origin)
        if not bool(np.all(np.isfinite(eigenvalues))) or not bool(
            np.all(np.isfinite(eigenvectors))
        ):
            return _invalid_planar_frame(status="nonfinite", rank=0, origin=origin)

        largest = max(0.0, float(eigenvalues[-1]))
        eigen_tolerance = max(
            largest * 1e-12,
            np.finfo(np.float64).eps * largest * max(3, normalized.shape[0]) * 16.0,
        )
        rank = int(np.count_nonzero(eigenvalues > eigen_tolerance))
        if rank < 2:
            status = "point" if rank == 0 else "linear"
            return _invalid_planar_frame(status=status, rank=rank, origin=origin)

        normal = eigenvectors[:, 0].astype(np.float64, copy=True)
        normal /= float(np.linalg.norm(normal))

        reference_line: np.ndarray | None = None
        reference_index = 0
        if packed_offsets is not None:
            total_newell, reference_index, reference_newell = (
                _packed_newell_statistics(
                    values,
                    packed_offsets,
                    scale=scale,
                )
            )
        else:
            total_newell = np.zeros((3,), dtype=np.float64)
            reference_newell = np.zeros((3,), dtype=np.float64)
            reference_area = -1.0
            assert lines is not None
            for line in lines:
                if line.shape[0] < 3:
                    newell = np.zeros((3,), dtype=np.float64)
                else:
                    newell = _newell_vector(line, scale=scale)
                area = float(np.linalg.norm(newell))
                total_newell += newell
                if area > reference_area:
                    reference_area = area
                    reference_line = line
                    reference_newell = newell

        orientation = total_newell
        if float(np.linalg.norm(orientation)) <= 1e-14:
            orientation = reference_newell
        orientation_norm = float(np.linalg.norm(orientation))
        if orientation_norm > 1e-14:
            if float(np.dot(normal, orientation)) < 0.0:
                normal = -normal
        else:
            normal = _canonicalize_direction(normal)

        edge_tolerance = max(tolerance, scale * 1e-12)
        if packed_offsets is not None:
            x_axis = _first_projected_packed_edge(
                values,
                packed_offsets,
                normal=normal,
                reference_index=reference_index,
                tolerance=edge_tolerance,
            )
        else:
            assert lines is not None
            ordered_lines = (
                [reference_line, *(line for line in lines if line is not reference_line)]
                if reference_line is not None
                else lines
            )
            x_axis = None
            for line in ordered_lines:
                if line is None or line.shape[0] < 2:
                    continue
                for edge in np.diff(line, axis=0):
                    projected = edge - float(np.dot(edge, normal)) * normal
                    length = float(np.linalg.norm(projected))
                    if length > edge_tolerance:
                        x_axis = projected / length
                        break
                if x_axis is not None:
                    break

        if x_axis is None:
            fallback = eigenvectors[:, -1].astype(np.float64, copy=True)
            fallback -= float(np.dot(fallback, normal)) * normal
            fallback_norm = float(np.linalg.norm(fallback))
            if fallback_norm <= 1e-14:
                return _invalid_planar_frame(status="linear", rank=1, origin=origin)
            x_axis = _canonicalize_direction(fallback / fallback_norm)

        y_axis = _cross3(normal, x_axis)
        y_norm = float(np.linalg.norm(y_axis))
        if y_norm <= 1e-14:
            return _invalid_planar_frame(status="linear", rank=1, origin=origin)
        y_axis /= y_norm
        x_axis = _cross3(y_axis, normal)
        x_axis /= float(np.linalg.norm(x_axis))

        basis = np.stack([x_axis, y_axis, normal], axis=0)
        inverse = basis.T
        residual = float(np.max(np.abs((values - origin) @ normal)))
        status = "planar" if rank == 2 else "spatial"
        return cls(
            origin=origin,
            basis=basis,
            inverse=inverse,
            residual=residual,
            rank=rank,
            status=status,
        )


def _world_canonical_basis(normal: np.ndarray) -> np.ndarray | None:
    """単位法線から world axis 優先の右手直交基底を返す。"""

    canonical_normal = _stable_canonicalize_direction(normal)
    world_axes = np.eye(3, dtype=np.float64)
    projected = world_axes - np.outer(
        world_axes @ canonical_normal,
        canonical_normal,
    )
    lengths = np.linalg.norm(projected, axis=1)
    axis_index = _stable_argmax(lengths)
    axis_length = float(lengths[axis_index])
    if not math.isfinite(axis_length) or axis_length <= 1e-14:
        return None

    x_axis = projected[axis_index] / axis_length
    if float(np.dot(x_axis, world_axes[axis_index])) < 0.0:
        x_axis = -x_axis
    y_axis = _cross3(canonical_normal, x_axis)
    y_length = float(np.linalg.norm(y_axis))
    if not math.isfinite(y_length) or y_length <= 1e-14:
        return None
    y_axis /= y_length
    x_axis = _cross3(y_axis, canonical_normal)
    x_axis /= float(np.linalg.norm(x_axis))
    return np.stack([x_axis, y_axis, canonical_normal], axis=0)


def _axis_aligned_planar_frame(points: np.ndarray) -> PlanarFrame | None:
    """明らかな axis-aligned rank-2 平面を canonical frame 化する。"""

    extents = np.ptp(points, axis=0)
    constant_axes = np.flatnonzero(extents == 0.0)
    if constant_axes.size != 1:
        return None
    normal_index = int(constant_axes[0])
    plane_axes = tuple(index for index in range(3) if index != normal_index)
    planar = points[:, plane_axes] - points[0, plane_axes]
    scale = float(np.max(np.linalg.norm(planar, axis=1)))
    if not math.isfinite(scale) or scale <= 0.0:
        return None
    reference_index = int(np.argmax(np.linalg.norm(planar, axis=1)))
    reference = planar[reference_index]
    cross = reference[0] * planar[:, 1] - reference[1] * planar[:, 0]
    # 境界的な rank 判定は汎用 PCA に任せ、明白な面だけを高速化する。
    if float(np.max(np.abs(cross))) <= scale * scale * 1e-6:
        return None

    normal = np.zeros((3,), dtype=np.float64)
    normal[normal_index] = 1.0
    basis = _world_canonical_basis(normal)
    if basis is None:
        return None
    origin = normal * float(np.mean(points[:, normal_index]))
    return PlanarFrame(
        origin=origin,
        basis=basis,
        inverse=basis.T,
        residual=0.0,
        rank=2,
        status="planar",
    )


def _two_point_linear_frame(points: np.ndarray) -> PlanarFrame | None:
    """二点直線へ canonical principal plane を補う。"""

    direction = points[1] - points[0]
    length = float(np.linalg.norm(direction))
    if not math.isfinite(length) or length <= 0.0:
        return None
    x_axis = _stable_canonicalize_direction(direction / length)

    world_axes = np.asarray(
        (
            (0.0, 0.0, 1.0),
            (0.0, 1.0, 0.0),
            (1.0, 0.0, 0.0),
        ),
        dtype=np.float64,
    )
    projected = world_axes - np.outer(world_axes @ x_axis, x_axis)
    lengths = np.linalg.norm(projected, axis=1)
    axis_index = _stable_argmax(lengths)
    normal_length = float(lengths[axis_index])
    if not math.isfinite(normal_length) or normal_length <= 1e-14:
        return None
    normal = _stable_canonicalize_direction(
        projected[axis_index] / normal_length
    )

    y_axis = _cross3(normal, x_axis)
    y_length = float(np.linalg.norm(y_axis))
    if not math.isfinite(y_length) or y_length <= 1e-14:
        return None
    y_axis /= y_length
    x_axis = _cross3(y_axis, normal)
    x_axis /= float(np.linalg.norm(x_axis))

    center = np.mean(points, axis=0)
    origin = normal * float(np.dot(center, normal))
    residual = float(np.max(np.abs((points - origin) @ normal)))
    basis = np.stack([x_axis, y_axis, normal], axis=0)
    return PlanarFrame(
        origin=origin,
        basis=basis,
        inverse=basis.T,
        residual=residual,
        rank=2,
        status="planar",
    )


def canonical_planar_frame(
    points: np.ndarray,
    offsets: np.ndarray | None = None,
    *,
    allow_linear: bool = False,
) -> PlanarFrame:
    """入力順に依存しない world 基準の平面 frame を返す。

    ``PlanarFrame.from_points`` が推定した平面と残差を保ちながら、法線の符号と
    平面内の軸を固定 world axis から決め直す。ring の winding、seam、line 順を
    変えても同じ局所座標系が必要な平面演算で使う。

    ``allow_linear=True`` の場合は、rank 1 の点群に world axis を基準とする
    principal plane を補い、有効な rank 2 frame として返す。純粋な 3D 直線には
    平面が一意に定まらないため、この補完規則を明示的に選ぶ effect だけが使う。

    Parameters
    ----------
    points : np.ndarray
        shape ``(N, 3)`` の world 座標。
    offsets : np.ndarray or None, default None
        packed polyline の境界。省略時は全点を一本の線として扱う。
    allow_linear : bool, default False
        rank 1 の点群へ決定的な principal plane を補うか。

    Returns
    -------
    PlanarFrame
        world 基準へ canonicalize した frame。補完できない入力は
        ``PlanarFrame.from_points`` と同じ無効 frame を返す。
    """

    values = np.asarray(points, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 3:
        raise ValueError("points は shape (N,3) である必要がある")
    fast_path_offsets = offsets is None
    if offsets is not None:
        packed_offsets = np.asarray(offsets)
        fast_path_offsets = bool(
            packed_offsets.ndim == 1
            and packed_offsets.size >= 2
            and np.issubdtype(packed_offsets.dtype, np.integer)
            and int(packed_offsets[0]) == 0
            and int(packed_offsets[-1]) == values.shape[0]
            and np.all(np.diff(packed_offsets) > 0)
        )
    if (
        fast_path_offsets
        and values.shape[0]
        and bool(np.all(np.isfinite(values)))
    ):
        axis_aligned = _axis_aligned_planar_frame(values)
        if axis_aligned is not None:
            return axis_aligned
        if bool(allow_linear) and values.shape[0] == 2:
            two_point = _two_point_linear_frame(values)
            if two_point is not None:
                return two_point

    source = PlanarFrame.from_points(values, offsets)
    if source.valid:
        basis = _world_canonical_basis(source.normal.copy())
        if basis is None:
            return source

        # world 原点から推定平面へ下ろした垂線の足を使うことで、入力の seam や
        # line 順が変わっても平面内原点を動かさない。
        normal = basis[2]
        origin = normal * float(np.dot(source.origin, normal))
        return PlanarFrame(
            origin=origin,
            basis=basis,
            inverse=basis.T,
            residual=source.residual,
            rank=source.rank,
            status=source.status,
        )

    if not bool(allow_linear) or source.status != "linear":
        return source

    tolerance = _frame_close_tolerance(values)
    lines = _clean_frame_lines(values, offsets, tolerance=tolerance)
    if not lines:
        return source
    samples = np.concatenate(lines, axis=0)
    center = np.mean(samples, axis=0)
    centered = samples - center
    covariance = centered.T @ centered
    try:
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    except np.linalg.LinAlgError:
        return source
    if not bool(np.all(np.isfinite(eigenvalues))) or not bool(
        np.all(np.isfinite(eigenvectors))
    ):
        return source
    if float(eigenvalues[-1]) <= float(tolerance) * float(tolerance):
        return source

    x_axis = _stable_canonicalize_direction(
        eigenvectors[:, -1].astype(np.float64, copy=True)
    )
    x_length = float(np.linalg.norm(x_axis))
    if not math.isfinite(x_length) or x_length <= 1e-14:
        return source
    x_axis /= x_length

    # 直線に最も直交する world axis を選ぶ。同率時は Z, Y, X の順とし、
    # 入力頂点の向きを反転しても同じ principal plane を得る。
    world_axes = np.asarray(
        (
            (0.0, 0.0, 1.0),
            (0.0, 1.0, 0.0),
            (1.0, 0.0, 0.0),
        ),
        dtype=np.float64,
    )
    projected = world_axes - np.outer(world_axes @ x_axis, x_axis)
    lengths = np.linalg.norm(projected, axis=1)
    axis_index = _stable_argmax(lengths)
    normal_length = float(lengths[axis_index])
    if not math.isfinite(normal_length) or normal_length <= 1e-14:
        return source
    normal = _stable_canonicalize_direction(projected[axis_index] / normal_length)

    y_axis = _cross3(normal, x_axis)
    y_length = float(np.linalg.norm(y_axis))
    if not math.isfinite(y_length) or y_length <= 1e-14:
        return source
    y_axis /= y_length
    x_axis = _cross3(y_axis, normal)
    x_axis /= float(np.linalg.norm(x_axis))

    origin = normal * float(np.dot(center, normal))
    residual = float(np.max(np.abs((values - origin) @ normal)))
    basis = np.stack([x_axis, y_axis, normal], axis=0)
    return PlanarFrame(
        origin=origin,
        basis=basis,
        inverse=basis.T,
        residual=residual,
        rank=2,
        status="planar",
    )
