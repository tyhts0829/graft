"""
Purpose:
    polylineのopen/closed topologyを保ちながら弧長標本密度を揃える共有kernelを提供する。
Use when:
    effectが頂点間隔を正規化する、または後段処理用のbounded resampleを必要とする場合。
Constraints:
    - open curveの両端を保持し、closed curveは出力の始点と終点をexactに一致させる。
    - 全lineの出力頂点数をplanで検査してから、packed出力を一度だけ確保する。
    - closed判定とduplicate seam除去を混同せず、line境界と入力順を維持する。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numba import njit  # type: ignore[attr-defined, import-untyped]

RESAMPLE_CLOSED_DISTANCE_EPS = 0.01
_RESAMPLE_COPY = 0
_RESAMPLE_OPEN = 1
_RESAMPLE_CLOSED = 2


@njit(cache=True, fastmath=True, inline="always")  # type: ignore[misc]
def reflect_index(index: int, size: int) -> int:
    """端点を重ねる reflect 境界条件の配列 index を返す。"""

    reflected = int(index)
    count = int(size)
    if count <= 1:
        return 0
    while reflected < 0 or reflected >= count:
        if reflected < 0:
            reflected = -reflected
        elif reflected >= count:
            reflected = 2 * count - 2 - reflected
    return int(reflected)


@dataclass(frozen=True, slots=True)
class ResampleLinePlan:
    """1本のpolylineについて確保前に算出した再sample仕様。"""

    input_start: int
    input_stop: int
    sample_stop: int
    output_start: int
    output_stop: int
    mode: int

    @property
    def closed(self) -> bool:
        """周期境界として処理するlineならTrue。"""

        return self.mode == _RESAMPLE_CLOSED


@dataclass(frozen=True, slots=True)
class ResamplePlan:
    """全polylineの出力数を確保前に検証した再sample計画。"""

    step: float
    lines: tuple[ResampleLinePlan, ...]
    total_vertices: int
    max_vertices: int

    @property
    def fits(self) -> bool:
        """出力頂点数が上限内ならTrue。"""

        return self.total_vertices <= self.max_vertices

    @classmethod
    def from_geometry(
        cls,
        coords: np.ndarray,
        offsets: np.ndarray,
        *,
        step: float,
        closed: str,
        max_vertices: int,
        closed_distance: float = RESAMPLE_CLOSED_DISTANCE_EPS,
    ) -> ResamplePlan:
        """全lineをcountし、出力配列を作らず計画だけを返す。"""

        step_size = float(step)
        if not math.isfinite(step_size) or step_size <= 0.0:
            raise ValueError("step は有限の正数である必要がある")

        closed_mode = str(closed)
        if closed_mode not in {"auto", "open", "closed"}:
            closed_mode = "auto"
        closed_distance_sq = float(closed_distance) * float(closed_distance)

        lines: list[ResampleLinePlan] = []
        output_cursor = 0
        for line_index in range(int(offsets.size) - 1):
            start = int(offsets[line_index])
            stop = int(offsets[line_index + 1])
            vertices = coords[start:stop]
            source_count = int(stop - start)
            sample_stop = stop
            mode = _RESAMPLE_COPY if source_count < 2 else _RESAMPLE_OPEN
            output_count = source_count

            near_closed = False
            if source_count >= 3 and closed_mode != "open":
                near_closed = _endpoints_within_distance(vertices, closed_distance_sq)
            use_closed = source_count >= 3 and (
                closed_mode == "closed" or (closed_mode == "auto" and near_closed)
            )

            if use_closed:
                sample_stop = stop - 1 if near_closed else stop
                sample_vertices = coords[start:sample_stop]
                if sample_vertices.shape[0] >= 3:
                    mode = _RESAMPLE_CLOSED
                    total_length = float(_total_length_closed_nb(sample_vertices))
                    sample_count = _closed_resample_count(
                        total_length=total_length,
                        step=step_size,
                        source_count=int(sample_vertices.shape[0]),
                        max_vertices=int(max_vertices),
                    )
                    output_count = sample_count + 1
                else:
                    sample_stop = stop

            if mode == _RESAMPLE_OPEN:
                total_length = float(_total_length_open_nb(vertices))
                output_count = _open_resample_count(
                    total_length=total_length,
                    step=step_size,
                    source_count=source_count,
                    max_vertices=int(max_vertices),
                )

            output_stop = output_cursor + int(output_count)
            lines.append(
                ResampleLinePlan(
                    input_start=start,
                    input_stop=stop,
                    sample_stop=sample_stop,
                    output_start=output_cursor,
                    output_stop=output_stop,
                    mode=mode,
                )
            )
            output_cursor = output_stop

        return cls(
            step=step_size,
            lines=tuple(lines),
            total_vertices=int(output_cursor),
            max_vertices=max(0, int(max_vertices)),
        )


def _endpoints_within_distance(vertices: np.ndarray, distance_sq: float) -> bool:
    with np.errstate(over="ignore", invalid="ignore"):
        dx = float(vertices[-1, 0] - vertices[0, 0])
        dy = float(vertices[-1, 1] - vertices[0, 1])
        dz = float(vertices[-1, 2] - vertices[0, 2])
    if not math.isfinite(dx):
        dx = float(vertices[-1, 0]) - float(vertices[0, 0])
    if not math.isfinite(dy):
        dy = float(vertices[-1, 1]) - float(vertices[0, 1])
    if not math.isfinite(dz):
        dz = float(vertices[-1, 2]) - float(vertices[0, 2])
    return dx * dx + dy * dy + dz * dz <= float(distance_sq)


def _open_resample_count(
    *, total_length: float, step: float, source_count: int, max_vertices: int
) -> int:
    if total_length <= 0.0:
        return int(source_count)
    ratio = total_length / step
    if not math.isfinite(ratio):
        return max(0, int(max_vertices)) + 1
    count = int(math.floor(ratio)) + 1
    if float((count - 1) * step) < total_length:
        count += 1
    return max(2, count)


def _closed_resample_count(
    *, total_length: float, step: float, source_count: int, max_vertices: int
) -> int:
    if total_length <= 0.0:
        return int(source_count)
    ratio = total_length / step
    if not math.isfinite(ratio):
        return max(0, int(max_vertices)) + 1
    return max(3, int(math.ceil(ratio)))


def build_gaussian_kernel(*, sigma_in_samples: float, max_radius: int) -> np.ndarray:
    """sample単位のsigmaから正規化済みGaussian kernelを作る。"""

    radius = int(math.ceil(3.0 * float(sigma_in_samples)))
    radius = min(max(1, radius), max(1, int(max_radius)))
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    weights = np.exp(-0.5 * (x / float(sigma_in_samples)) ** 2)
    weight_sum = float(np.sum(weights))
    if weight_sum > 0.0:
        weights = weights / weight_sum
    return weights.astype(np.float64, copy=False)


@njit(cache=True)
def _resample_needs_float64_nb(vertices: np.ndarray, closed: bool) -> bool:
    """float32 の距離二乗が overflow する入力なら True を返す。"""

    count = int(vertices.shape[0])
    edge_count = count if closed and count >= 2 else max(0, count - 1)
    for index in range(edge_count):
        following = index + 1
        if following >= count:
            following = 0
        dx = vertices[following, 0] - vertices[index, 0]
        dy = vertices[following, 1] - vertices[index, 1]
        dz = vertices[following, 2] - vertices[index, 2]
        distance_sq = dx * dx + dy * dy + dz * dz
        if not np.isfinite(distance_sq):
            return True
    return False


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _total_length_open_fast_nb(vertices: np.ndarray) -> float:
    total = 0.0
    for index in range(vertices.shape[0] - 1):
        dx = float(vertices[index + 1, 0] - vertices[index, 0])
        dy = float(vertices[index + 1, 1] - vertices[index, 1])
        dz = float(vertices[index + 1, 2] - vertices[index, 2])
        total += float(np.sqrt(dx * dx + dy * dy + dz * dz))
    return float(total)


@njit(cache=True)
def _total_length_open_wide_nb(vertices: np.ndarray) -> float:
    total = 0.0
    for index in range(vertices.shape[0] - 1):
        dx = np.float64(vertices[index + 1, 0]) - np.float64(vertices[index, 0])
        dy = np.float64(vertices[index + 1, 1]) - np.float64(vertices[index, 1])
        dz = np.float64(vertices[index + 1, 2]) - np.float64(vertices[index, 2])
        total += np.sqrt(dx * dx + dy * dy + dz * dz)
    return float(total)


@njit(cache=True)
def _total_length_open_nb(vertices: np.ndarray) -> float:
    if _resample_needs_float64_nb(vertices, False):
        return _total_length_open_wide_nb(vertices)
    return _total_length_open_fast_nb(vertices)


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _total_length_closed_fast_nb(vertices: np.ndarray) -> float:
    total = _total_length_open_fast_nb(vertices)
    count = int(vertices.shape[0])
    if count >= 2:
        dx = float(vertices[0, 0] - vertices[count - 1, 0])
        dy = float(vertices[0, 1] - vertices[count - 1, 1])
        dz = float(vertices[0, 2] - vertices[count - 1, 2])
        total += float(np.sqrt(dx * dx + dy * dy + dz * dz))
    return float(total)


@njit(cache=True)
def _total_length_closed_wide_nb(vertices: np.ndarray) -> float:
    total = _total_length_open_wide_nb(vertices)
    count = int(vertices.shape[0])
    if count >= 2:
        dx = np.float64(vertices[0, 0]) - np.float64(vertices[count - 1, 0])
        dy = np.float64(vertices[0, 1]) - np.float64(vertices[count - 1, 1])
        dz = np.float64(vertices[0, 2]) - np.float64(vertices[count - 1, 2])
        total += np.sqrt(dx * dx + dy * dy + dz * dz)
    return float(total)


@njit(cache=True)
def _total_length_closed_nb(vertices: np.ndarray) -> float:
    if _resample_needs_float64_nb(vertices, True):
        return _total_length_closed_wide_nb(vertices)
    return _total_length_closed_fast_nb(vertices)


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _resample_open_fast_into_nb(
    vertices: np.ndarray,
    step: float,
    out: np.ndarray,
) -> None:
    count = int(out.shape[0])
    source_count = int(vertices.shape[0])
    if source_count < 2 or _total_length_open_fast_nb(vertices) <= 0.0:
        out[:] = vertices
        return

    out[0] = vertices[0]
    out[count - 1] = vertices[source_count - 1]
    segment_index = 0
    distance_acc = 0.0
    sx = float(vertices[0, 0])
    sy = float(vertices[0, 1])
    sz = float(vertices[0, 2])
    ex = float(vertices[1, 0])
    ey = float(vertices[1, 1])
    ez = float(vertices[1, 2])
    dx = ex - sx
    dy = ey - sy
    dz = ez - sz
    segment_length = float(np.sqrt(dx * dx + dy * dy + dz * dz))
    target = float(step)
    output_index = 1

    while output_index < count - 1:
        if segment_length <= 0.0:
            segment_index += 1
            if segment_index >= source_count - 1:
                break
            sx = float(vertices[segment_index, 0])
            sy = float(vertices[segment_index, 1])
            sz = float(vertices[segment_index, 2])
            ex = float(vertices[segment_index + 1, 0])
            ey = float(vertices[segment_index + 1, 1])
            ez = float(vertices[segment_index + 1, 2])
            dx = ex - sx
            dy = ey - sy
            dz = ez - sz
            segment_length = float(np.sqrt(dx * dx + dy * dy + dz * dz))
            continue

        if distance_acc + segment_length >= target:
            t = (target - distance_acc) / segment_length
            out[output_index, 0] = np.float32(sx + t * dx)
            out[output_index, 1] = np.float32(sy + t * dy)
            out[output_index, 2] = np.float32(sz + t * dz)
            output_index += 1
            target += float(step)
        else:
            distance_acc += segment_length
            segment_index += 1
            if segment_index >= source_count - 1:
                break
            sx = ex
            sy = ey
            sz = ez
            ex = float(vertices[segment_index + 1, 0])
            ey = float(vertices[segment_index + 1, 1])
            ez = float(vertices[segment_index + 1, 2])
            dx = ex - sx
            dy = ey - sy
            dz = ez - sz
            segment_length = float(np.sqrt(dx * dx + dy * dy + dz * dz))


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _resample_open_matches_source_fast_nb(
    vertices: np.ndarray,
    step: float,
) -> bool:
    """通常kernelの各内部標本が入力頂点とbyte等価か確保せず判定する。"""

    count = int(vertices.shape[0])
    if count < 3:
        return True

    segment_index = 0
    distance_acc = 0.0
    sx = float(vertices[0, 0])
    sy = float(vertices[0, 1])
    sz = float(vertices[0, 2])
    ex = float(vertices[1, 0])
    ey = float(vertices[1, 1])
    ez = float(vertices[1, 2])
    dx = ex - sx
    dy = ey - sy
    dz = ez - sz
    segment_length = float(np.sqrt(dx * dx + dy * dy + dz * dz))
    target = float(step)
    output_index = 1

    while output_index < count - 1:
        if segment_length <= 0.0:
            segment_index += 1
            if segment_index >= count - 1:
                return False
            sx = float(vertices[segment_index, 0])
            sy = float(vertices[segment_index, 1])
            sz = float(vertices[segment_index, 2])
            ex = float(vertices[segment_index + 1, 0])
            ey = float(vertices[segment_index + 1, 1])
            ez = float(vertices[segment_index + 1, 2])
            dx = ex - sx
            dy = ey - sy
            dz = ez - sz
            segment_length = float(np.sqrt(dx * dx + dy * dy + dz * dz))
            continue

        if distance_acc + segment_length >= target:
            t = (target - distance_acc) / segment_length
            x = np.float32(sx + t * dx)
            y = np.float32(sy + t * dy)
            z = np.float32(sz + t * dz)
            expected_x = vertices[output_index, 0]
            expected_y = vertices[output_index, 1]
            expected_z = vertices[output_index, 2]
            if x != expected_x or y != expected_y or z != expected_z:
                return False
            if x == 0.0 and np.signbit(x) != np.signbit(expected_x):
                return False
            if y == 0.0 and np.signbit(y) != np.signbit(expected_y):
                return False
            if z == 0.0 and np.signbit(z) != np.signbit(expected_z):
                return False
            output_index += 1
            target += float(step)
        else:
            distance_acc += segment_length
            segment_index += 1
            if segment_index >= count - 1:
                return False
            sx = ex
            sy = ey
            sz = ez
            ex = float(vertices[segment_index + 1, 0])
            ey = float(vertices[segment_index + 1, 1])
            ez = float(vertices[segment_index + 1, 2])
            dx = ex - sx
            dy = ey - sy
            dz = ez - sz
            segment_length = float(np.sqrt(dx * dx + dy * dy + dz * dz))
    return True


@njit(cache=True)
def _resample_open_wide_into_nb(
    vertices: np.ndarray,
    step: float,
    out: np.ndarray,
) -> None:
    count = int(out.shape[0])
    source_count = int(vertices.shape[0])
    if source_count < 2 or _total_length_open_wide_nb(vertices) <= 0.0:
        out[:] = vertices
        return

    out[0] = vertices[0]
    out[count - 1] = vertices[source_count - 1]
    segment_index = 0
    distance_acc = 0.0
    sx = np.float64(vertices[0, 0])
    sy = np.float64(vertices[0, 1])
    sz = np.float64(vertices[0, 2])
    ex = np.float64(vertices[1, 0])
    ey = np.float64(vertices[1, 1])
    ez = np.float64(vertices[1, 2])
    dx = ex - sx
    dy = ey - sy
    dz = ez - sz
    segment_length = np.sqrt(dx * dx + dy * dy + dz * dz)
    target = float(step)
    output_index = 1

    while output_index < count - 1:
        if segment_length <= 0.0:
            segment_index += 1
            if segment_index >= source_count - 1:
                break
            sx = np.float64(vertices[segment_index, 0])
            sy = np.float64(vertices[segment_index, 1])
            sz = np.float64(vertices[segment_index, 2])
            ex = np.float64(vertices[segment_index + 1, 0])
            ey = np.float64(vertices[segment_index + 1, 1])
            ez = np.float64(vertices[segment_index + 1, 2])
            dx = ex - sx
            dy = ey - sy
            dz = ez - sz
            segment_length = np.sqrt(dx * dx + dy * dy + dz * dz)
            continue

        if distance_acc + segment_length >= target:
            t = (target - distance_acc) / segment_length
            out[output_index, 0] = np.float32(sx + t * dx)
            out[output_index, 1] = np.float32(sy + t * dy)
            out[output_index, 2] = np.float32(sz + t * dz)
            output_index += 1
            target += float(step)
        else:
            distance_acc += segment_length
            segment_index += 1
            if segment_index >= source_count - 1:
                break
            sx = ex
            sy = ey
            sz = ez
            ex = np.float64(vertices[segment_index + 1, 0])
            ey = np.float64(vertices[segment_index + 1, 1])
            ez = np.float64(vertices[segment_index + 1, 2])
            dx = ex - sx
            dy = ey - sy
            dz = ez - sz
            segment_length = np.sqrt(dx * dx + dy * dy + dz * dz)


@njit(cache=True)
def _resample_open_matches_source_wide_nb(
    vertices: np.ndarray,
    step: float,
) -> bool:
    """float64退避kernelの各内部標本が入力頂点とbyte等価か判定する。"""

    count = int(vertices.shape[0])
    if count < 3:
        return True

    segment_index = 0
    distance_acc = 0.0
    sx = np.float64(vertices[0, 0])
    sy = np.float64(vertices[0, 1])
    sz = np.float64(vertices[0, 2])
    ex = np.float64(vertices[1, 0])
    ey = np.float64(vertices[1, 1])
    ez = np.float64(vertices[1, 2])
    dx = ex - sx
    dy = ey - sy
    dz = ez - sz
    segment_length = np.sqrt(dx * dx + dy * dy + dz * dz)
    target = float(step)
    output_index = 1

    while output_index < count - 1:
        if segment_length <= 0.0:
            segment_index += 1
            if segment_index >= count - 1:
                return False
            sx = np.float64(vertices[segment_index, 0])
            sy = np.float64(vertices[segment_index, 1])
            sz = np.float64(vertices[segment_index, 2])
            ex = np.float64(vertices[segment_index + 1, 0])
            ey = np.float64(vertices[segment_index + 1, 1])
            ez = np.float64(vertices[segment_index + 1, 2])
            dx = ex - sx
            dy = ey - sy
            dz = ez - sz
            segment_length = np.sqrt(dx * dx + dy * dy + dz * dz)
            continue

        if distance_acc + segment_length >= target:
            t = (target - distance_acc) / segment_length
            x = np.float32(sx + t * dx)
            y = np.float32(sy + t * dy)
            z = np.float32(sz + t * dz)
            expected_x = vertices[output_index, 0]
            expected_y = vertices[output_index, 1]
            expected_z = vertices[output_index, 2]
            if x != expected_x or y != expected_y or z != expected_z:
                return False
            if x == 0.0 and np.signbit(x) != np.signbit(expected_x):
                return False
            if y == 0.0 and np.signbit(y) != np.signbit(expected_y):
                return False
            if z == 0.0 and np.signbit(z) != np.signbit(expected_z):
                return False
            output_index += 1
            target += float(step)
        else:
            distance_acc += segment_length
            segment_index += 1
            if segment_index >= count - 1:
                return False
            sx = ex
            sy = ey
            sz = ez
            ex = np.float64(vertices[segment_index + 1, 0])
            ey = np.float64(vertices[segment_index + 1, 1])
            ez = np.float64(vertices[segment_index + 1, 2])
            dx = ex - sx
            dy = ey - sy
            dz = ez - sz
            segment_length = np.sqrt(dx * dx + dy * dy + dz * dz)
    return True


@njit(cache=True)
def _resample_open_into_nb(
    vertices: np.ndarray,
    step: float,
    out: np.ndarray,
) -> None:
    if _resample_needs_float64_nb(vertices, False):
        _resample_open_wide_into_nb(vertices, step, out)
    else:
        _resample_open_fast_into_nb(vertices, step, out)


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _resample_closed_fast_into_nb(
    vertices: np.ndarray,
    step: float,
    out: np.ndarray,
) -> None:
    count = int(out.shape[0])
    source_count = int(vertices.shape[0])
    total_length = _total_length_closed_fast_nb(vertices)
    if total_length <= 0.0:
        out[:] = vertices
        return

    effective_step = float(step)
    if float(count - 1) * effective_step >= total_length:
        effective_step = total_length / float(count)

    out[0] = vertices[0]
    segment_index = 0
    distance_acc = 0.0
    sx = float(vertices[0, 0])
    sy = float(vertices[0, 1])
    sz = float(vertices[0, 2])
    ex = float(vertices[1, 0])
    ey = float(vertices[1, 1])
    ez = float(vertices[1, 2])
    dx = ex - sx
    dy = ey - sy
    dz = ez - sz
    segment_length = float(np.sqrt(dx * dx + dy * dy + dz * dz))

    for output_index in range(1, count):
        target = float(output_index) * effective_step
        while segment_length <= 0.0 or distance_acc + segment_length < target:
            if segment_length > 0.0:
                distance_acc += segment_length
            segment_index += 1
            if segment_index >= source_count:
                segment_index = 0
            sx = ex
            sy = ey
            sz = ez
            next_index = segment_index + 1
            if next_index >= source_count:
                next_index = 0
            ex = float(vertices[next_index, 0])
            ey = float(vertices[next_index, 1])
            ez = float(vertices[next_index, 2])
            dx = ex - sx
            dy = ey - sy
            dz = ez - sz
            segment_length = float(np.sqrt(dx * dx + dy * dy + dz * dz))

        if segment_length <= 0.0:
            out[output_index, 0] = np.float32(sx)
            out[output_index, 1] = np.float32(sy)
            out[output_index, 2] = np.float32(sz)
            continue

        t = (target - distance_acc) / segment_length
        out[output_index, 0] = np.float32(sx + t * dx)
        out[output_index, 1] = np.float32(sy + t * dy)
        out[output_index, 2] = np.float32(sz + t * dz)


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _resample_closed_matches_source_fast_nb(
    vertices: np.ndarray,
    step: float,
) -> bool:
    """通常の閉曲線kernelが入力頂点列を再現するか確保せず判定する。"""

    count = int(vertices.shape[0])
    total_length = _total_length_closed_fast_nb(vertices)
    if count < 3 or total_length <= 0.0:
        return True

    effective_step = float(step)
    if float(count - 1) * effective_step >= total_length:
        effective_step = total_length / float(count)

    segment_index = 0
    distance_acc = 0.0
    sx = float(vertices[0, 0])
    sy = float(vertices[0, 1])
    sz = float(vertices[0, 2])
    ex = float(vertices[1, 0])
    ey = float(vertices[1, 1])
    ez = float(vertices[1, 2])
    dx = ex - sx
    dy = ey - sy
    dz = ez - sz
    segment_length = float(np.sqrt(dx * dx + dy * dy + dz * dz))

    for output_index in range(1, count):
        target = float(output_index) * effective_step
        while segment_length <= 0.0 or distance_acc + segment_length < target:
            if segment_length > 0.0:
                distance_acc += segment_length
            segment_index += 1
            if segment_index >= count:
                segment_index = 0
            sx = ex
            sy = ey
            sz = ez
            next_index = segment_index + 1
            if next_index >= count:
                next_index = 0
            ex = float(vertices[next_index, 0])
            ey = float(vertices[next_index, 1])
            ez = float(vertices[next_index, 2])
            dx = ex - sx
            dy = ey - sy
            dz = ez - sz
            segment_length = float(np.sqrt(dx * dx + dy * dy + dz * dz))

        if segment_length <= 0.0:
            x = np.float32(sx)
            y = np.float32(sy)
            z = np.float32(sz)
        else:
            t = (target - distance_acc) / segment_length
            x = np.float32(sx + t * dx)
            y = np.float32(sy + t * dy)
            z = np.float32(sz + t * dz)

        expected_x = vertices[output_index, 0]
        expected_y = vertices[output_index, 1]
        expected_z = vertices[output_index, 2]
        if x != expected_x or y != expected_y or z != expected_z:
            return False
        if x == 0.0 and np.signbit(x) != np.signbit(expected_x):
            return False
        if y == 0.0 and np.signbit(y) != np.signbit(expected_y):
            return False
        if z == 0.0 and np.signbit(z) != np.signbit(expected_z):
            return False
    return True


@njit(cache=True)
def _resample_closed_wide_into_nb(
    vertices: np.ndarray,
    step: float,
    out: np.ndarray,
) -> None:
    count = int(out.shape[0])
    source_count = int(vertices.shape[0])
    total_length = _total_length_closed_wide_nb(vertices)
    if total_length <= 0.0:
        out[:] = vertices
        return

    effective_step = float(step)
    # closed の最小標本数は 3 なので、requested step が周長の半分以上では
    # 最後の標本位置が周長へ到達または超過する。その場合だけ周長を 3 等分以上
    # する実効間隔へ切り替え、seam への重複と巨大 step による多重周回を防ぐ。
    if float(count - 1) * effective_step >= total_length:
        effective_step = total_length / float(count)

    out[0] = vertices[0]
    segment_index = 0
    distance_acc = 0.0
    sx = np.float64(vertices[0, 0])
    sy = np.float64(vertices[0, 1])
    sz = np.float64(vertices[0, 2])
    ex = np.float64(vertices[1, 0])
    ey = np.float64(vertices[1, 1])
    ez = np.float64(vertices[1, 2])
    dx = ex - sx
    dy = ey - sy
    dz = ez - sz
    segment_length = np.sqrt(dx * dx + dy * dy + dz * dz)

    for output_index in range(1, count):
        target = float(output_index) * effective_step
        while segment_length <= 0.0 or distance_acc + segment_length < target:
            if segment_length > 0.0:
                distance_acc += segment_length
            segment_index += 1
            if segment_index >= source_count:
                segment_index = 0
            sx = ex
            sy = ey
            sz = ez
            next_index = segment_index + 1
            if next_index >= source_count:
                next_index = 0
            ex = np.float64(vertices[next_index, 0])
            ey = np.float64(vertices[next_index, 1])
            ez = np.float64(vertices[next_index, 2])
            dx = ex - sx
            dy = ey - sy
            dz = ez - sz
            segment_length = np.sqrt(dx * dx + dy * dy + dz * dz)

        if segment_length <= 0.0:
            out[output_index, 0] = np.float32(sx)
            out[output_index, 1] = np.float32(sy)
            out[output_index, 2] = np.float32(sz)
            continue

        t = (target - distance_acc) / segment_length
        out[output_index, 0] = np.float32(sx + t * dx)
        out[output_index, 1] = np.float32(sy + t * dy)
        out[output_index, 2] = np.float32(sz + t * dz)


@njit(cache=True)
def _resample_closed_matches_source_wide_nb(
    vertices: np.ndarray,
    step: float,
) -> bool:
    """float64退避の閉曲線kernelが入力頂点列を再現するか判定する。"""

    count = int(vertices.shape[0])
    total_length = _total_length_closed_wide_nb(vertices)
    if count < 3 or total_length <= 0.0:
        return True

    effective_step = float(step)
    if float(count - 1) * effective_step >= total_length:
        effective_step = total_length / float(count)

    segment_index = 0
    distance_acc = 0.0
    sx = np.float64(vertices[0, 0])
    sy = np.float64(vertices[0, 1])
    sz = np.float64(vertices[0, 2])
    ex = np.float64(vertices[1, 0])
    ey = np.float64(vertices[1, 1])
    ez = np.float64(vertices[1, 2])
    dx = ex - sx
    dy = ey - sy
    dz = ez - sz
    segment_length = np.sqrt(dx * dx + dy * dy + dz * dz)

    for output_index in range(1, count):
        target = float(output_index) * effective_step
        while segment_length <= 0.0 or distance_acc + segment_length < target:
            if segment_length > 0.0:
                distance_acc += segment_length
            segment_index += 1
            if segment_index >= count:
                segment_index = 0
            sx = ex
            sy = ey
            sz = ez
            next_index = segment_index + 1
            if next_index >= count:
                next_index = 0
            ex = np.float64(vertices[next_index, 0])
            ey = np.float64(vertices[next_index, 1])
            ez = np.float64(vertices[next_index, 2])
            dx = ex - sx
            dy = ey - sy
            dz = ez - sz
            segment_length = np.sqrt(dx * dx + dy * dy + dz * dz)

        if segment_length <= 0.0:
            x = np.float32(sx)
            y = np.float32(sy)
            z = np.float32(sz)
        else:
            t = (target - distance_acc) / segment_length
            x = np.float32(sx + t * dx)
            y = np.float32(sy + t * dy)
            z = np.float32(sz + t * dz)

        expected_x = vertices[output_index, 0]
        expected_y = vertices[output_index, 1]
        expected_z = vertices[output_index, 2]
        if x != expected_x or y != expected_y or z != expected_z:
            return False
        if x == 0.0 and np.signbit(x) != np.signbit(expected_x):
            return False
        if y == 0.0 and np.signbit(y) != np.signbit(expected_y):
            return False
        if z == 0.0 and np.signbit(z) != np.signbit(expected_z):
            return False
    return True


@njit(cache=True)
def _resample_closed_into_nb(
    vertices: np.ndarray,
    step: float,
    out: np.ndarray,
) -> None:
    if _resample_needs_float64_nb(vertices, True):
        _resample_closed_wide_into_nb(vertices, step, out)
    else:
        _resample_closed_fast_into_nb(vertices, step, out)


def resample_open_matches_source(vertices: np.ndarray, *, step: float) -> bool:
    """開曲線kernelが入力頂点列をbyte単位で再現するならTrueを返す。"""

    if bool(_resample_needs_float64_nb(vertices, False)):
        return bool(_resample_open_matches_source_wide_nb(vertices, float(step)))
    return bool(_resample_open_matches_source_fast_nb(vertices, float(step)))


def resample_closed_matches_source(vertices: np.ndarray, *, step: float) -> bool:
    """閉曲線kernelが入力頂点列をbyte単位で再現するならTrueを返す。"""

    if bool(_resample_needs_float64_nb(vertices, True)):
        return bool(_resample_closed_matches_source_wide_nb(vertices, float(step)))
    return bool(_resample_closed_matches_source_fast_nb(vertices, float(step)))


def resample_polylines(coords: np.ndarray, plan: ResamplePlan) -> tuple[np.ndarray, np.ndarray]:
    """上限検証済みplanに従い、packed polylineを一度だけ確保して再sampleする。"""

    if not plan.fits:
        raise ValueError("上限超過の ResamplePlan は実行できない")

    out_coords = np.empty((plan.total_vertices, 3), dtype=np.float32)
    out_offsets = np.empty((len(plan.lines) + 1,), dtype=np.int32)
    out_offsets[0] = 0
    for line_index, line in enumerate(plan.lines):
        source = coords[line.input_start : line.sample_stop]
        target = out_coords[line.output_start : line.output_stop]
        if line.mode == _RESAMPLE_COPY:
            target[:] = source
        elif line.mode == _RESAMPLE_CLOSED:
            _resample_closed_into_nb(source, float(plan.step), target[:-1])
            target[-1] = target[0]
        else:
            _resample_open_into_nb(source, float(plan.step), target)
        out_offsets[line_index + 1] = np.int32(line.output_stop)

    return out_coords, out_offsets
