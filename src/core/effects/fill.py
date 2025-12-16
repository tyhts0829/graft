"""
ハッチ塗りつぶし effect。

閉領域（外周＋穴）に対して偶奇規則で内部を判定し、指定角度のハッチ線分を生成する。
3D 入力は一度 XY 平面へ整列して 2D で処理し、生成した線分を元の姿勢へ戻す。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from src.core.effect_registry import effect
from src.core.realized_geometry import RealizedGeometry
from .util import transform_back, transform_to_xy_plane
from src.core.parameters.meta import ParamMeta

# 生成する塗り線の最大本数（密度の上限）。
MAX_FILL_LINES = 1000
NONPLANAR_EPS_ABS = 1e-6
NONPLANAR_EPS_REL = 1e-5

fill_meta = {
    "angle_sets": ParamMeta(kind="int", ui_min=1, ui_max=6),
    "angle": ParamMeta(kind="float", ui_min=0.0, ui_max=180.0),
    "density": ParamMeta(kind="float", ui_min=0.0, ui_max=float(MAX_FILL_LINES)),
    "spacing_gradient": ParamMeta(kind="float", ui_min=-5.0, ui_max=5.0),
    "remove_boundary": ParamMeta(kind="bool"),
}


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _planarity_threshold(points: np.ndarray) -> float:
    """点群スケールに基づく平面性判定の閾値を返す。"""
    if points.size == 0:
        return float(NONPLANAR_EPS_ABS)
    p = points.astype(np.float64, copy=False)
    mins = np.min(p, axis=0)
    maxs = np.max(p, axis=0)
    diag = float(np.linalg.norm(maxs - mins))
    return max(float(NONPLANAR_EPS_ABS), float(NONPLANAR_EPS_REL) * diag)


def _polygon_area_abs(vertices: np.ndarray) -> float:
    """2D ポリゴンの面積絶対値を返す（閉じは仮定しない）。"""
    if vertices.shape[0] < 3:
        return 0.0
    x = vertices[:, 0].astype(np.float64, copy=False)
    y = vertices[:, 1].astype(np.float64, copy=False)
    return float(0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _point_in_polygon(point: np.ndarray, polygon: np.ndarray) -> bool:
    """点が多角形内部にあるかを返す（境界上は False 扱い）。"""
    x = float(point[0])
    y = float(point[1])
    n = int(polygon.shape[0])
    if n < 3:
        return False

    inside = False
    x1 = float(polygon[-1, 0])
    y1 = float(polygon[-1, 1])
    for i in range(n):
        x2 = float(polygon[i, 0])
        y2 = float(polygon[i, 1])
        # y を跨ぐ辺だけを見る（水平辺は除外）。
        if (y1 > y) != (y2 > y):
            # 交点の x 座標
            x_int = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < x_int:
                inside = not inside
        x1, y1 = x2, y2
    return inside


def _build_evenodd_groups(coords_2d_all: np.ndarray, offsets: np.ndarray) -> list[list[int]]:
    """外周＋穴を even-odd でグルーピングし、[outer, hole...] のリストを返す。"""
    ring_indices = [
        i
        for i in range(int(offsets.size) - 1)
        if int(offsets[i + 1]) - int(offsets[i]) >= 3
    ]
    if not ring_indices:
        return []

    rings = {}
    rep = {}
    area = {}
    for i in ring_indices:
        s = int(offsets[i])
        e = int(offsets[i + 1])
        poly = coords_2d_all[s:e]
        rings[i] = poly
        rep[i] = poly[0]
        area[i] = _polygon_area_abs(poly)

    contains: dict[int, set[int]] = {j: set() for j in ring_indices}
    depth: dict[int, int] = {i: 0 for i in ring_indices}

    for i in ring_indices:
        for j in ring_indices:
            if i == j:
                continue
            if _point_in_polygon(rep[i], rings[j]):
                contains[j].add(i)
                depth[i] += 1

    parent: dict[int, int | None] = {i: None for i in ring_indices}
    for i in ring_indices:
        if depth[i] <= 0:
            continue
        candidates = [
            j
            for j in ring_indices
            if j != i and depth.get(j, 0) == depth[i] - 1 and i in contains[j]
        ]
        if not candidates:
            continue
        parent[i] = min(candidates, key=lambda j: area.get(j, 0.0))

    children: dict[int, list[int]] = {i: [] for i in ring_indices}
    for i in ring_indices:
        p = parent[i]
        if p is None:
            continue
        children[p].append(i)

    groups: list[list[int]] = []
    for i in sorted(ring_indices):
        if depth[i] % 2 != 0:
            continue
        holes = sorted(children.get(i, []))
        groups.append([i, *holes])
    return groups


def _spacing_from_height(height: float, density: float) -> float:
    """高さと密度から線間隔を算出する（旧仕様: round(density) 本相当）。"""
    num_lines = int(round(float(density)))
    if num_lines < 2:
        num_lines = 2
    if num_lines > MAX_FILL_LINES:
        num_lines = MAX_FILL_LINES
    if height <= 0.0:
        return 0.0
    return float(height) / float(num_lines)


def _generate_y_values(
    min_y: float, max_y: float, base_spacing: float, spacing_gradient: float
) -> np.ndarray:
    """旧仕様のスキャンライン Y 値列を生成する（max_y は含めない）。"""
    if not np.isfinite(base_spacing) or base_spacing <= 0.0:
        return np.empty(0, dtype=np.float32)
    if not np.isfinite(spacing_gradient):
        spacing_gradient = 0.0
    if max_y <= min_y:
        return np.empty(0, dtype=np.float32)

    if abs(spacing_gradient) < 1e-6:
        return np.arange(min_y, max_y, base_spacing, dtype=np.float32)

    height = max_y - min_y
    k = float(spacing_gradient)
    if k > 4.0:
        k = 4.0
    elif k < -4.0:
        k = -4.0

    if abs(k) < 1e-3:
        c = 1.0
    else:
        c = k / (2.0 * float(np.sinh(k / 2.0)))

    y_values: list[float] = []
    y = float(min_y)
    min_step = base_spacing * 1e-3
    while y < max_y:
        t = (y - min_y) / height
        factor = c * float(np.exp(k * (t - 0.5)))
        step = base_spacing * max(factor, 0.0)
        if step < min_step:
            step = min_step
        y_values.append(y)
        y += step
    return np.asarray(y_values, dtype=np.float32)


def _find_line_intersections(polygon: np.ndarray, y: float) -> list[float]:
    """水平線 y とポリゴンの交点 x 座標列を返す（旧仕様条件）。"""
    n = int(polygon.shape[0])
    if n < 2:
        return []

    out: list[float] = []
    for i in range(n):
        p1 = polygon[i]
        p2 = polygon[(i + 1) % n]
        y1 = float(p1[1])
        y2 = float(p2[1])
        if (y1 <= y < y2) or (y2 <= y < y1):
            if y2 == y1:
                continue
            x1 = float(p1[0])
            x2 = float(p2[0])
            x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            out.append(float(x))
    return out


def _generate_line_fill_evenodd_multi(
    coords_2d: np.ndarray,
    offsets: np.ndarray,
    *,
    density: float,
    angle_rad: float,
    spacing_override: float | None,
    spacing_gradient: float,
) -> list[np.ndarray]:
    """複数輪郭を偶奇規則でまとめてハッチングする（2D 平面前提）。"""
    if density <= 0.0 or offsets.size <= 1 or coords_2d.size == 0:
        return []

    c2 = coords_2d.astype(np.float32, copy=False)
    center = np.mean(c2, axis=0)
    work = c2
    rot_fwd: np.ndarray | None = None

    if angle_rad != 0.0:
        cos_inv = float(np.cos(-angle_rad))
        sin_inv = float(np.sin(-angle_rad))
        rot_inv = np.array([[cos_inv, -sin_inv], [sin_inv, cos_inv]], dtype=np.float32)
        work = (c2 - center) @ rot_inv.T + center
        cos_fwd = float(np.cos(angle_rad))
        sin_fwd = float(np.sin(angle_rad))
        rot_fwd = np.array([[cos_fwd, -sin_fwd], [sin_fwd, cos_fwd]], dtype=np.float32)

    ref_height = float(np.max(c2[:, 1]) - np.min(c2[:, 1]))
    if ref_height <= 0.0:
        return []

    min_y = float(np.min(work[:, 1]))
    max_y = float(np.max(work[:, 1]))

    spacing = float(spacing_override) if spacing_override is not None else _spacing_from_height(ref_height, density)
    if not np.isfinite(spacing) or spacing <= 0.0:
        return []

    y_values = _generate_y_values(min_y, max_y, spacing, float(spacing_gradient))
    out_lines: list[np.ndarray] = []

    for y in y_values:
        intersections_all: list[float] = []
        for i in range(int(offsets.size) - 1):
            s = int(offsets[i])
            e = int(offsets[i + 1])
            if e - s < 2:
                continue
            poly = work[s:e]
            intersections_all.extend(_find_line_intersections(poly, float(y)))

        if len(intersections_all) < 2:
            continue

        xs_sorted = np.sort(np.asarray(intersections_all, dtype=np.float32))
        for j in range(0, int(xs_sorted.size) - 1, 2):
            x1 = float(xs_sorted[j])
            x2 = float(xs_sorted[j + 1])
            if x2 - x1 <= 1e-9:
                continue
            seg2d = np.array([[x1, float(y)], [x2, float(y)]], dtype=np.float32)
            if rot_fwd is not None:
                seg2d = (seg2d - center) @ rot_fwd.T + center
            out_lines.append(seg2d)
    return out_lines


def _lines_to_realized(lines: Sequence[np.ndarray]) -> RealizedGeometry:
    """ポリライン列を RealizedGeometry にまとめる。"""
    if not lines:
        return _empty_geometry()

    coords_list: list[np.ndarray] = []
    offsets = np.zeros((len(lines) + 1,), dtype=np.int32)
    acc = 0
    for i, line in enumerate(lines):
        ln = np.asarray(line)
        if ln.ndim != 2 or ln.shape[1] != 3:
            raise ValueError("lines は shape (N,3) の配列列である必要がある")
        coords_list.append(ln.astype(np.float32, copy=False))
        acc += int(ln.shape[0])
        offsets[i + 1] = acc
    coords = np.concatenate(coords_list, axis=0) if coords_list else np.zeros((0, 3), dtype=np.float32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@effect(meta=fill_meta)
def fill(
    inputs: Sequence[RealizedGeometry],
    *,
    angle_sets: int = 1,
    angle: float = 45.0,
    density: float = 35.0,
    spacing_gradient: float = 0.0,
    remove_boundary: bool = False,
) -> RealizedGeometry:
    """閉領域をハッチングで塗りつぶす。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        入力実体ジオメトリ列。通常は 1 要素。
    angle_sets : int, default 1
        方向本数。1=単方向、2=90°クロス、3=60°間隔、...（180°を等分）。
    angle : float, default 45.0
        基準角 [deg]。
    density : float, default 35.0
        旧仕様の密度スケール。`round(density)` 本相当の間隔を基準高さから算出する。
        0 以下は塗り線を生成しない。
    spacing_gradient : float, default 0.0
        スキャン方向に沿った線間隔勾配。0.0 で一様間隔。
    remove_boundary : bool, default False
        True なら入力境界（入力ポリライン）を出力から除去する。

    Returns
    -------
    RealizedGeometry
        境界線（必要なら）と塗り線を含む実体ジオメトリ。
    """
    if not inputs:
        return _empty_geometry()

    base = inputs[0]
    if base.coords.shape[0] == 0:
        return base

    density = float(density)
    if density <= 0.0:
        return base if not remove_boundary else _empty_geometry()

    # 出力は「境界（必要なら）→塗り線」の順に積む。
    out_lines: list[np.ndarray] = []
    if not remove_boundary:
        for i in range(int(base.offsets.size) - 1):
            s = int(base.offsets[i])
            e = int(base.offsets[i + 1])
            out_lines.append(base.coords[s:e])

    k = int(angle_sets)
    if k < 1:
        k = 1

    # 1) 全体がほぼ平面なら、外周＋穴をグルーピングして even-odd 塗りを行う。
    # 3D -> XY 平面への整列で 2D 化し、生成した線分を元姿勢へ戻す。
    planar_global = False
    coords_xy_all: np.ndarray | None = None
    rot_global: np.ndarray | None = None
    z_global: float = 0.0

    global_threshold = _planarity_threshold(base.coords)
    for poly_i in range(int(base.offsets.size) - 1):
        s = int(base.offsets[poly_i])
        e = int(base.offsets[poly_i + 1])
        if e - s < 3:
            continue
        vertices = base.coords[s:e]
        _vxy, rot, z_off = transform_to_xy_plane(vertices)

        coords64 = base.coords.astype(np.float64, copy=False)
        aligned = coords64 @ rot.T
        aligned[:, 2] -= float(z_off)
        residual = float(np.max(np.abs(aligned[:, 2])))
        if residual <= global_threshold:
            planar_global = True
            coords_xy_all = aligned
            rot_global = rot
            z_global = float(z_off)
            break

    if planar_global and coords_xy_all is not None and rot_global is not None:
        coords2d_all = coords_xy_all[:, :2].astype(np.float32, copy=False)
        groups = _build_evenodd_groups(coords2d_all, base.offsets)
        if not groups:
            return _lines_to_realized(out_lines)

        ref_height_global = float(np.max(coords2d_all[:, 1]) - np.min(coords2d_all[:, 1]))
        if ref_height_global <= 0.0:
            return _lines_to_realized(out_lines)

        base_angle_rad = float(np.deg2rad(float(angle)))
        for ring_indices in groups:
            base_spacing = _spacing_from_height(ref_height_global, density)
            if base_spacing <= 0.0:
                continue

            parts: list[np.ndarray] = []
            g_offsets = np.zeros((len(ring_indices) + 1,), dtype=np.int32)
            acc = 0
            for j, ring_i in enumerate(ring_indices):
                s = int(base.offsets[ring_i])
                e = int(base.offsets[ring_i + 1])
                poly = coords2d_all[s:e]
                if poly.shape[0] < 2:
                    continue
                parts.append(poly)
                acc += int(poly.shape[0])
                g_offsets[j + 1] = acc
            if not parts or g_offsets[-1] <= 0:
                continue

            g_coords2d = np.concatenate(parts, axis=0)
            for i in range(k):
                ang_i = base_angle_rad + (np.pi / k) * i
                segs2d = _generate_line_fill_evenodd_multi(
                    g_coords2d,
                    g_offsets,
                    density=density,
                    angle_rad=float(ang_i),
                    spacing_override=float(base_spacing),
                    spacing_gradient=float(spacing_gradient),
                )
                for seg in segs2d:
                    seg3 = np.concatenate(
                        [seg, np.zeros((int(seg.shape[0]), 1), dtype=np.float32)], axis=1
                    )
                    out_lines.append(transform_back(seg3, rot_global, z_global))

        return _lines_to_realized(out_lines)

    # 2) 全体が非平面なら、各ポリラインごとに「平面なら塗り、非平面なら境界のみ」とする。
    base_angle_rad = float(np.deg2rad(float(angle)))
    for poly_i in range(int(base.offsets.size) - 1):
        s = int(base.offsets[poly_i])
        e = int(base.offsets[poly_i + 1])
        vertices = base.coords[s:e]
        if vertices.shape[0] < 3:
            continue

        vxy, rot, z_off = transform_to_xy_plane(vertices)
        residual = float(np.max(np.abs(vxy[:, 2].astype(np.float64, copy=False))))
        if residual > _planarity_threshold(vertices):
            continue

        coords2d = vxy[:, :2].astype(np.float32, copy=False)
        ref_height = float(np.max(coords2d[:, 1]) - np.min(coords2d[:, 1]))
        base_spacing = _spacing_from_height(ref_height, density)
        if base_spacing <= 0.0:
            continue

        offsets = np.array([0, coords2d.shape[0]], dtype=np.int32)
        for i in range(k):
            ang_i = base_angle_rad + (np.pi / k) * i
            segs2d = _generate_line_fill_evenodd_multi(
                coords2d,
                offsets,
                density=density,
                angle_rad=float(ang_i),
                spacing_override=float(base_spacing),
                spacing_gradient=float(spacing_gradient),
            )
            for seg in segs2d:
                seg3 = np.concatenate(
                    [seg, np.zeros((int(seg.shape[0]), 1), dtype=np.float32)], axis=1
                )
                out_lines.append(transform_back(seg3, rot, float(z_off)))

    return _lines_to_realized(out_lines)
