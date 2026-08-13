"""
Purpose:
    polyline形状を許容誤差内に保ちながら、既存頂点から冗長な標本を削減する。
Use when:
    resampleではなく頂点削減、RDP誤差、またはopen/closed topologyを変更する場合。
Constraints:
    - open線の両端を保持し、closed線は3個以上の固有頂点とexact closureを維持する。
    - 新しい補間点を作らず、入力頂点の部分列として出力する。
    - scratchと出力量を大規模配列の確保前にbudget検査する。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from grafix.core.operation_authoring import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import GeomTuple
from grafix.core.resource_budget import ensure_geometry_output

from grafix.core.geometry_kernels.resample import RESAMPLE_CLOSED_DISTANCE_EPS

_SCRATCH_BYTES_PER_VERTEX = 64
_SCRATCH_BYTES_PER_LINE = 16
_CLOSED_CHOICES = ("auto", "open", "closed")


simplify_meta = {
    "tolerance": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=10.0,
        description="元の線からこの XYZ 距離以内に収まる範囲で頂点を削減する。",
    ),
    "closed": ParamMeta(
        kind="choice",
        choices=_CLOSED_CHOICES,
        description="開曲線、閉曲線、端点距離による自動判定から簡略化方式を選ぶ。",
    ),
}


@dataclass(frozen=True, slots=True)
class _SimplifyLinePlan:
    """1 本の線について出力確保前に選んだ入力頂点 index。"""

    indices: np.ndarray
    append_closure: bool
    changed: bool

    @property
    def output_count(self) -> int:
        """この線が出力する頂点数。"""

        return int(self.indices.size) + int(self.append_closure)


def _point_segment_distance_sq(point: np.ndarray, start: np.ndarray, end: np.ndarray) -> float:
    """3 次元の点と有限線分の距離の二乗を float64 で返す。"""

    sx = float(start[0])
    sy = float(start[1])
    sz = float(start[2])
    dx = float(end[0]) - sx
    dy = float(end[1]) - sy
    dz = float(end[2]) - sz
    px = float(point[0]) - sx
    py = float(point[1]) - sy
    pz = float(point[2]) - sz
    denominator = dx * dx + dy * dy + dz * dz
    if denominator <= 0.0:
        return px * px + py * py + pz * pz

    projection = (px * dx + py * dy + pz * dz) / denominator
    projection = min(1.0, max(0.0, projection))
    rx = px - projection * dx
    ry = py - projection * dy
    rz = pz - projection * dz
    return rx * rx + ry * ry + rz * rz


def _rdp_keep_indices(points: np.ndarray, tolerance_sq: float) -> np.ndarray:
    """iterative RDP で残す local index を入力順に返す。"""

    count = int(points.shape[0])
    if count <= 2:
        return np.arange(count, dtype=np.int64)

    keep: np.ndarray = np.zeros((count,), dtype=np.bool_)
    keep[0] = True
    keep[count - 1] = True

    # 1 区間を 2 個の int64 で表す。保留区間数は頂点数を超えない。
    stack: np.ndarray = np.empty((count, 2), dtype=np.int64)
    stack_size = 1
    stack[0, 0] = 0
    stack[0, 1] = count - 1

    while stack_size:
        stack_size -= 1
        start_index = int(stack[stack_size, 0])
        end_index = int(stack[stack_size, 1])
        if end_index <= start_index + 1:
            continue

        best_index = -1
        best_distance_sq = -1.0
        segment_start = points[start_index]
        segment_end = points[end_index]
        # 小さい index から走査し、同距離では先に見つけた頂点を維持する。
        for point_index in range(start_index + 1, end_index):
            distance_sq = _point_segment_distance_sq(
                points[point_index],
                segment_start,
                segment_end,
            )
            if distance_sq > best_distance_sq:
                best_distance_sq = distance_sq
                best_index = point_index

        # tolerance 境界上の点は削除する（距離が厳密に大きい点だけを残す）。
        if best_index < 0 or best_distance_sq <= tolerance_sq:
            continue

        keep[best_index] = True
        if best_index > start_index + 1:
            stack[stack_size, 0] = start_index
            stack[stack_size, 1] = best_index
            stack_size += 1
        if end_index > best_index + 1:
            stack[stack_size, 0] = best_index
            stack[stack_size, 1] = end_index
            stack_size += 1

    return np.flatnonzero(keep).astype(np.int64, copy=False)


def _endpoints_are_near(points: np.ndarray) -> bool:
    """既存 resample 系と同じ距離しきい値で端点近接を判定する。"""

    delta = points[-1].astype(np.float64) - points[0].astype(np.float64)
    distance_sq = float(np.dot(delta, delta))
    return distance_sq <= RESAMPLE_CLOSED_DISTANCE_EPS * RESAMPLE_CLOSED_DISTANCE_EPS


def _coordinate_key(point: np.ndarray) -> tuple[float, float, float]:
    """有限な float32 座標を exact unique 判定用 key にする。"""

    return float(point[0]), float(point[1]), float(point[2])


def _ensure_three_unique_indices(points: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """有効 ring が 3 個未満の固有頂点へ潰れないよう補う。"""

    selected = [int(index) for index in indices]
    selected_keys = {_coordinate_key(points[index]) for index in selected}
    if len(selected_keys) >= 3:
        return indices

    start = points[int(indices[0])]
    end = points[int(indices[-1])]
    while len(selected_keys) < 3:
        best_index = -1
        best_distance_sq = -1.0
        for index in range(int(points.shape[0])):
            key = _coordinate_key(points[index])
            if key in selected_keys:
                continue
            distance_sq = _point_segment_distance_sq(points[index], start, end)
            if distance_sq > best_distance_sq:
                best_distance_sq = distance_sq
                best_index = index
        if best_index < 0:
            break
        selected.append(best_index)
        selected_keys.add(_coordinate_key(points[best_index]))

    selected.sort()
    return np.asarray(selected, dtype=np.int64)


def _closed_keep_indices(points: np.ndarray, tolerance_sq: float) -> np.ndarray:
    """ring を seam と最遠 anchor 間の 2 arc に分けて簡略化する。"""

    count = int(points.shape[0])
    seam = points[0]
    anchor_index = 1
    anchor_distance_sq = -1.0
    for index in range(1, count):
        delta = points[index].astype(np.float64) - seam.astype(np.float64)
        distance_sq = float(np.dot(delta, delta))
        # 同距離では小さい入力 index を維持する。
        if distance_sq > anchor_distance_sq:
            anchor_distance_sq = distance_sq
            anchor_index = index

    first_arc = points[: anchor_index + 1]
    first_keep = _rdp_keep_indices(first_arc, tolerance_sq)

    second_source_indices = np.concatenate(
        (
            np.arange(anchor_index, count, dtype=np.int64),
            np.asarray([0], dtype=np.int64),
        )
    )
    second_arc = points[second_source_indices]
    second_keep_positions = _rdp_keep_indices(second_arc, tolerance_sq)
    second_keep = second_source_indices[second_keep_positions]

    # first arc の anchor と second arc の anchor/seam は重複させない。
    combined = np.concatenate((first_keep, second_keep[1:-1])).astype(
        np.int64,
        copy=False,
    )
    return _ensure_three_unique_indices(points, combined)


def _plan_open_line(points: np.ndarray, tolerance_sq: float) -> _SimplifyLinePlan:
    indices = _rdp_keep_indices(points, tolerance_sq)
    return _SimplifyLinePlan(
        indices=indices,
        append_closure=False,
        changed=int(indices.size) != int(points.shape[0]),
    )


def _plan_closed_line(
    points: np.ndarray,
    *,
    tolerance_sq: float,
    near_closed: bool,
) -> _SimplifyLinePlan:
    sample_count = int(points.shape[0]) - int(near_closed)
    samples = points[:sample_count]
    unique_count = len({_coordinate_key(point) for point in samples})
    if sample_count < 3 or unique_count < 3:
        return _SimplifyLinePlan(
            indices=np.arange(int(points.shape[0]), dtype=np.int64),
            append_closure=False,
            changed=False,
        )

    indices = _closed_keep_indices(samples, tolerance_sq)
    exact_closure = near_closed and bool(np.array_equal(points[-1], points[0]))
    unchanged = (
        exact_closure
        and int(indices.size) == sample_count
        and int(points.shape[0]) == sample_count + 1
    )
    return _SimplifyLinePlan(
        indices=indices,
        append_closure=True,
        changed=not unchanged,
    )


@effect(meta=simplify_meta)
def simplify(
    g: GeomTuple,
    *,
    tolerance: float = 0.05,
    closed: str = "auto",
) -> GeomTuple:
    """許容誤差内でポリラインの頂点数を減らす。

    XYZ の point-to-segment 距離を用いた iterative
    Ramer-Douglas-Peucker 法で、入力頂点の一部だけを選ぶ。閉曲線は入力先頭と
    そこから最遠の頂点を anchor とする 2 本の arc に分け、巨大な許容誤差でも
    3 個以上の固有頂点を維持する。

    Parameters
    ----------
    g : tuple[np.ndarray, np.ndarray]
        簡略化する実体ジオメトリ（coords, offsets）。
    tolerance : float, default 0.05
        元の線から許容する最大 XYZ 距離。0 なら入力をそのまま返す。
    closed : {"auto", "open", "closed"}, default "auto"
        ``"open"`` は開曲線として両端を残し、``"closed"`` は 3 点以上の線を
        閉曲線として扱う。``"auto"`` は端点距離が 0.01 以下なら閉曲線とみなす。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        簡略化後の実体ジオメトリ（coords, offsets）。

    Raises
    ------
    ValueError
        `tolerance` が負の場合。
    """

    if tolerance < 0.0:
        raise ValueError("simplify の tolerance は 0 以上である必要がある")

    coords, offsets = g
    if coords.shape[0] == 0 or tolerance == 0.0:
        return coords, offsets

    line_count = max(0, int(offsets.size) - 1)
    if line_count == 0:
        return coords, offsets

    # RDP の keep mask / int64 stack / selected indices に加え、閉曲線の二つの
    # arc を作る advanced-index copy と一時 index を含む peak scratch を、
    # 最初の O(N) 配列確保より前に保守的に検査する。line ごとの planning 済み
    # index を保持しながら次の line を処理する場合も全頂点 64 bytes 内へ収まる。
    scratch_bytes = (
        int(coords.shape[0]) * _SCRATCH_BYTES_PER_VERTEX
        + line_count * _SCRATCH_BYTES_PER_LINE
    )
    ensure_geometry_output(
        "simplify",
        vertices=0,
        lines=0,
        scratch_bytes=scratch_bytes,
        hint="入力頂点数を減らすか、resample を先に適用してください",
    )

    tolerance_sq = tolerance * tolerance
    plans: list[_SimplifyLinePlan] = []
    changed = False
    total_output_vertices = 0

    for line_index in range(line_count):
        start = int(offsets[line_index])
        stop = int(offsets[line_index + 1])
        points = coords[start:stop]
        point_count = int(points.shape[0])

        near_closed = point_count >= 3 and _endpoints_are_near(points)
        use_closed = point_count >= 3 and (
            closed == "closed" or (closed == "auto" and near_closed)
        )
        if use_closed:
            plan = _plan_closed_line(
                points,
                tolerance_sq=tolerance_sq,
                near_closed=near_closed,
            )
        else:
            plan = _plan_open_line(points, tolerance_sq)

        plans.append(plan)
        changed = changed or plan.changed
        total_output_vertices += plan.output_count

    if not changed:
        return coords, offsets

    ensure_geometry_output(
        "simplify",
        vertices=total_output_vertices,
        lines=line_count,
        scratch_bytes=scratch_bytes,
        hint="tolerance を大きくすると出力頂点数を減らせます",
    )

    coords_out: np.ndarray = np.empty(
        (total_output_vertices, 3),
        dtype=np.float32,
    )
    offsets_out: np.ndarray = np.empty((line_count + 1,), dtype=np.int32)
    offsets_out[0] = 0
    write_at = 0
    for line_index, plan in enumerate(plans):
        start = int(offsets[line_index])
        selected_count = int(plan.indices.size)
        next_at = write_at + selected_count
        if selected_count:
            coords_out[write_at:next_at] = coords[start + plan.indices]
        if plan.append_closure:
            coords_out[next_at] = coords_out[write_at]
            next_at += 1
        write_at = next_at
        offsets_out[line_index + 1] = np.int32(write_at)

    return coords_out, offsets_out


__all__ = ["simplify", "simplify_meta"]
