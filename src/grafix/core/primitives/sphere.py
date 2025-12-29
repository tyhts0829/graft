"""
どこで: `src/grafix/primitives/sphere.py`。球プリミティブの実体生成。
何を: 4 つのスタイル（latlon/zigzag/icosphere/rings）で球のポリライン列を生成して返す。
なぜ: 3D 座標を持つ基本形状として、回転などの effect と組み合わせて使うため。
"""

from __future__ import annotations

import math

import numpy as np

from grafix.core.parameters.meta import ParamMeta
from grafix.core.primitive_registry import primitive
from grafix.core.realized_geometry import RealizedGeometry

_RADIUS = 0.5
_MIN_SUBDIVISIONS = 0
_MAX_SUBDIVISIONS = 5

_STYLE_ORDER = ["latlon", "zigzag", "icosphere", "rings"]

sphere_meta = {
    "subdivisions": ParamMeta(
        kind="int", ui_min=_MIN_SUBDIVISIONS, ui_max=_MAX_SUBDIVISIONS
    ),
    "type_index": ParamMeta(kind="int", ui_min=0, ui_max=len(_STYLE_ORDER) - 1),
    # mode は latlon / rings スタイル専用（0: 横/緯度のみ, 1: 縦/経度のみ, 2: 両方）
    "mode": ParamMeta(kind="int", ui_min=0, ui_max=2),
    "center": ParamMeta(kind="vec3", ui_min=0.0, ui_max=300.0),
    "scale": ParamMeta(kind="float", ui_min=0.0, ui_max=200.0),
}


def _clamp_int(value: int | float, lo: int, hi: int) -> int:
    v = int(round(float(value)))
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _polylines_to_realized(
    polylines: list[np.ndarray],
    *,
    center: tuple[float, float, float],
    scale: float,
) -> RealizedGeometry:
    """ポリライン列を RealizedGeometry に変換する。"""
    filtered: list[np.ndarray] = []
    lengths: list[int] = []
    for i, line in enumerate(polylines):
        arr = np.asarray(line, dtype=np.float32)
        if arr.ndim != 2 or arr.shape[1] != 3:
            raise ValueError(
                "sphere の各ポリラインは shape (N,3) の配列である必要がある"
                f": index={i}, shape={arr.shape}"
            )
        if arr.shape[0] == 0:
            continue
        filtered.append(arr)
        lengths.append(int(arr.shape[0]))

    if not filtered:
        coords = np.zeros((0, 3), dtype=np.float32)
        offsets = np.zeros((1,), dtype=np.int32)
        return RealizedGeometry(coords=coords, offsets=offsets)

    coords = np.concatenate(filtered, axis=0).astype(np.float32, copy=False)
    offsets = np.zeros(len(filtered) + 1, dtype=np.int32)
    offsets[1:] = np.cumsum(np.asarray(lengths, dtype=np.int32), dtype=np.int32)

    cx, cy, cz = float(center[0]), float(center[1]), float(center[2])
    s_f = float(scale)
    if s_f != 1.0:
        coords *= np.float32(s_f)
    if (cx, cy, cz) != (0.0, 0.0, 0.0):
        coords += np.array([cx, cy, cz], dtype=np.float32)

    return RealizedGeometry(coords=coords, offsets=offsets)


def _sphere_latlon(subdivisions: int, mode: int) -> list[np.ndarray]:
    """緯度/経度線のポリライン列を生成する。"""
    pi = math.pi
    two_pi = 2.0 * math.pi

    s = int(subdivisions)
    m = int(mode)
    if m < 0:
        m = 0
    elif m > 2:
        m = 2

    eq_segments = max(16, 64 * (s + 1))
    if s <= 0:
        eq_segments = max(eq_segments, 160)
    meridian_samples = max(12, eq_segments // 2)
    if s <= 0:
        lat_rings = max(4, meridian_samples // 4)
        min_segments_lat = 24
    else:
        lat_rings = meridian_samples
        min_segments_lat = 8
    target_step_equator = two_pi * _RADIUS / float(eq_segments)

    polylines: list[np.ndarray] = []

    # 経度線（極→極）
    if m in (1, 2):
        lat_vals = np.linspace(0.0, pi, meridian_samples + 1, dtype=np.float32)
        sin_lat = np.sin(lat_vals)
        cos_lat = np.cos(lat_vals)

        meridian_lines = max(8, lat_rings)
        stride = max(1, eq_segments // max(1, meridian_lines))
        for j in range(0, eq_segments, stride):
            lon = two_pi * j / eq_segments
            cos_lon = np.float32(np.cos(lon))
            sin_lon = np.float32(np.sin(lon))
            x = (sin_lat * cos_lon * _RADIUS).astype(np.float32)
            y = (sin_lat * sin_lon * _RADIUS).astype(np.float32)
            z = (cos_lat * _RADIUS).astype(np.float32)
            line = np.stack((x, y, z), axis=1).astype(np.float32)
            polylines.append(line)

    # 緯度リング（周方向）
    if m in (0, 2):
        for i in range(1, lat_rings):  # 極は除外
            lat = pi * i / lat_rings
            r = abs(math.sin(lat)) * _RADIUS
            if r <= 1e-9:
                continue
            segments_at_lat = int(
                np.ceil((two_pi * r) / max(1e-9, target_step_equator))
            )
            segments_at_lat = max(min_segments_lat, segments_at_lat)

            angles = np.linspace(0.0, two_pi, segments_at_lat + 1, dtype=np.float32)
            x = (np.cos(angles) * r).astype(np.float32)
            y = (np.sin(angles) * r).astype(np.float32)
            z = np.full_like(x, fill_value=np.cos(lat) * _RADIUS, dtype=np.float32)
            ring = np.stack((x, y, z), axis=1).astype(np.float32)
            polylines.append(ring)

    return polylines


def _sphere_zigzag(subdivisions: int) -> list[np.ndarray]:
    """螺旋（ジグザグ）スタイルのポリライン列を生成する。"""
    s = int(subdivisions)
    total_rotations = 8 + 4 * s

    if s <= 0:
        strand_count = 2
    elif s == 1:
        strand_count = 3
    else:
        strand_count = 4

    base_ppr = 64 + 16 * min(s, 2)  # 64, 80, 96, ...
    points_per_rotation = base_ppr if strand_count <= 2 else max(48, base_ppr - 24)

    polylines: list[np.ndarray] = []
    for k in range(strand_count):
        phase = 2.0 * math.pi * (k / float(strand_count))
        points = int(total_rotations * points_per_rotation)
        t = np.linspace(0.0, 1.0, points, dtype=np.float32)

        y = 1.0 - 2.0 * t
        radius = np.sqrt(np.maximum(0.0, 1.0 - y * y))
        theta = 2.0 * math.pi * total_rotations * t + phase

        x = np.cos(theta) * radius * _RADIUS
        z = np.sin(theta) * radius * _RADIUS
        y = y * _RADIUS

        polyline = np.stack(
            (x.astype(np.float32), y.astype(np.float32), z.astype(np.float32)),
            axis=1,
        )
        polylines.append(polyline)

    return polylines


def _sphere_icosphere(subdivisions: int) -> list[np.ndarray]:
    """アイコスフィア手法（階層細分化）で球ワイヤーを生成する。"""

    phi = (1.0 + math.sqrt(5.0)) / 2.0
    base_vertices = np.array(
        [
            [-1.0, phi, 0.0],
            [1.0, phi, 0.0],
            [-1.0, -phi, 0.0],
            [1.0, -phi, 0.0],
            [0.0, -1.0, phi],
            [0.0, 1.0, phi],
            [0.0, -1.0, -phi],
            [0.0, 1.0, -phi],
            [phi, 0.0, -1.0],
            [phi, 0.0, 1.0],
            [-phi, 0.0, -1.0],
            [-phi, 0.0, 1.0],
        ],
        dtype=np.float32,
    )
    norms = np.linalg.norm(base_vertices, axis=1, keepdims=True)
    base_vertices = base_vertices / norms * np.float32(_RADIUS)

    base_faces = [
        (0, 11, 5),
        (0, 5, 1),
        (0, 1, 7),
        (0, 7, 10),
        (0, 10, 11),
        (3, 9, 4),
        (3, 4, 2),
        (3, 2, 6),
        (3, 6, 8),
        (3, 8, 9),
        (1, 5, 9),
        (5, 11, 4),
        (11, 10, 2),
        (10, 7, 6),
        (7, 1, 8),
        (9, 5, 4),
        (4, 11, 2),
        (2, 10, 6),
        (6, 7, 8),
        (8, 1, 9),
    ]

    def midpoint_on_sphere(p1: np.ndarray, p2: np.ndarray) -> np.ndarray:
        mid = (p1 + p2) * np.float32(0.5)
        norm = float(np.linalg.norm(mid))
        if norm <= 0.0:
            return np.array([0.0, 0.0, 0.0], dtype=np.float32)
        return mid / np.float32(norm) * np.float32(_RADIUS)

    def subdivide_triangle(v1: np.ndarray, v2: np.ndarray, v3: np.ndarray, level: int):
        if level <= 0:
            return [(v1, v2), (v2, v3), (v3, v1)]

        m1 = midpoint_on_sphere(v1, v2)
        m2 = midpoint_on_sphere(v2, v3)
        m3 = midpoint_on_sphere(v3, v1)

        edges = []
        edges.extend(subdivide_triangle(v1, m1, m3, level - 1))
        edges.extend(subdivide_triangle(m1, v2, m2, level - 1))
        edges.extend(subdivide_triangle(m3, m2, v3, level - 1))
        edges.extend(subdivide_triangle(m1, m2, m3, level - 1))
        return edges

    all_edges: list[tuple[np.ndarray, np.ndarray]] = []
    for a, b, c in base_faces:
        v1, v2, v3 = base_vertices[a], base_vertices[b], base_vertices[c]
        all_edges.extend(subdivide_triangle(v1, v2, v3, int(subdivisions)))

    seen: set[tuple[tuple[float, float, float], tuple[float, float, float]]] = set()
    polylines: list[np.ndarray] = []
    for p0, p1 in all_edges:
        k0 = (float(p0[0]), float(p0[1]), float(p0[2]))
        k1 = (float(p1[0]), float(p1[1]), float(p1[2]))
        key = (k0, k1) if k0 <= k1 else (k1, k0)
        if key in seen:
            continue
        seen.add(key)
        polylines.append(np.array([p0, p1], dtype=np.float32))

    return polylines


def _sphere_rings(subdivisions: int, mode: int) -> list[np.ndarray]:
    """3 軸リング（水平+縦リング）スタイルのポリライン列を生成する。"""
    s = int(subdivisions)
    m = int(mode)
    if m < 0:
        m = 0
    elif m > 2:
        m = 2

    ring_count = 5 + 12 * s

    equator_segments = max(16, 64 * (s + 1))
    if s <= 0:
        equator_segments = max(equator_segments, 160)
    target_step_equator = 2.0 * math.pi * _RADIUS / float(equator_segments)
    min_segments = 24 if s <= 0 else 8

    polylines: list[np.ndarray] = []

    # 高さごとに水平リング（Y 一定の XZ 面の円）
    if m in (0, 2):
        for i in range(ring_count):
            y_pos = -_RADIUS + (i / (ring_count - 1)) * (2.0 * _RADIUS)
            radius = float(math.sqrt(max(0.0, _RADIUS * _RADIUS - y_pos * y_pos)))
            if radius <= 1e-9:
                continue
            segments = int(
                np.ceil((2.0 * math.pi * radius) / max(1e-9, target_step_equator))
            )
            segments = max(min_segments, segments)
            angles = np.linspace(0.0, 2.0 * math.pi, segments + 1, dtype=np.float32)
            xs = (radius * np.cos(angles)).astype(np.float32)
            zs = (radius * np.sin(angles)).astype(np.float32)
            ys = np.full_like(xs, fill_value=np.float32(y_pos))
            polylines.append(np.stack((xs, ys, zs), axis=1).astype(np.float32))

    # 縦リング（X 固定の YZ 円 / Z 固定の XY 円）
    if m in (1, 2):
        for i in range(ring_count):
            x_pos = -_RADIUS + (i / (ring_count - 1)) * (2.0 * _RADIUS)
            radius = float(math.sqrt(max(0.0, _RADIUS * _RADIUS - x_pos * x_pos)))
            if radius <= 1e-9:
                continue
            segments = int(
                np.ceil((2.0 * math.pi * radius) / max(1e-9, target_step_equator))
            )
            segments = max(min_segments, segments)
            angles = np.linspace(0.0, 2.0 * math.pi, segments + 1, dtype=np.float32)
            ys = (radius * np.cos(angles)).astype(np.float32)
            zs = (radius * np.sin(angles)).astype(np.float32)
            xs = np.full_like(ys, fill_value=np.float32(x_pos))
            polylines.append(np.stack((xs, ys, zs), axis=1).astype(np.float32))

        for i in range(ring_count):
            z_pos = -_RADIUS + (i / (ring_count - 1)) * (2.0 * _RADIUS)
            radius = float(math.sqrt(max(0.0, _RADIUS * _RADIUS - z_pos * z_pos)))
            if radius <= 1e-9:
                continue
            segments = int(
                np.ceil((2.0 * math.pi * radius) / max(1e-9, target_step_equator))
            )
            segments = max(min_segments, segments)
            angles = np.linspace(0.0, 2.0 * math.pi, segments + 1, dtype=np.float32)
            xs = (radius * np.cos(angles)).astype(np.float32)
            ys = (radius * np.sin(angles)).astype(np.float32)
            zs = np.full_like(xs, fill_value=np.float32(z_pos))
            polylines.append(np.stack((xs, ys, zs), axis=1).astype(np.float32))

    return polylines


@primitive(meta=sphere_meta)
def sphere(
    *,
    subdivisions: int | float = 1,
    type_index: int | float = 0,
    mode: int | float = 2,
    center: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: float = 1.0,
) -> RealizedGeometry:
    """球のワイヤーフレームをポリライン列として生成する。

    Parameters
    ----------
    subdivisions : int | float, optional
        細分化レベル（0..5 にクランプ）。
    type_index : int | float, optional
        スタイル選択（0..3 にクランプ）。
        0=latlon, 1=zigzag, 2=icosphere, 3=rings。
    mode : int | float, optional
        latlon/rings 用の線種（0: 横/緯度のみ, 1: 縦/経度のみ, 2: 両方）。範囲外はクランプ。
    center : tuple[float, float, float], optional
        平行移動ベクトル (cx, cy, cz)。
    scale : float, optional
        等方スケール倍率 s。縦横比変更は effect を使用する。

    Returns
    -------
    RealizedGeometry
        球ワイヤーフレームの実体ジオメトリ。
    """
    s = _clamp_int(subdivisions, _MIN_SUBDIVISIONS, _MAX_SUBDIVISIONS)
    idx = _clamp_int(type_index, 0, len(_STYLE_ORDER) - 1)
    m = _clamp_int(mode, 0, 2)

    try:
        cx, cy, cz = center
    except Exception as exc:
        raise ValueError(
            "sphere の center は長さ 3 のシーケンスである必要がある"
        ) from exc
    try:
        s_f = float(scale)
    except Exception as exc:
        raise ValueError("sphere の scale は float である必要がある") from exc

    if idx == 0:
        polylines = _sphere_latlon(s, m)
    elif idx == 1:
        polylines = _sphere_zigzag(s)
    elif idx == 2:
        polylines = _sphere_icosphere(s)
    else:
        polylines = _sphere_rings(s, m)

    return _polylines_to_realized(polylines, center=(cx, cy, cz), scale=s_f)


__all__ = ["sphere", "sphere_meta"]
