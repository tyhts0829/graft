from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np

from engine.core.geometry import Geometry

from .registry import shape


@lru_cache(maxsize=128)
def _sphere_latlon(subdivisions: int, mode: int = 2) -> list[np.ndarray]:
    """緯度経度線のみで球ワイヤーフレームを生成。

    引数:
        subdivisions: 細分化レベル（0–5）
        mode: 線の種類（0: 緯度, 1: 経度, 2: 両方）

    返り値:
        線分列の頂点配列リスト（float32, 半径0.5）
    """
    R = 0.5
    pi = np.pi
    two_pi = 2.0 * np.pi

    subdivisions_i = int(subdivisions)

    def _clamp_mode(m: int) -> int:
        m = int(m)
        if m < 0:
            return 0
        if m > 2:
            return 2
        return m

    def _compute_layout(s: int) -> tuple[int, int, int, int]:
        """レイアウト（分割数）を決定。

        戻り値: (equator_segments, meridian_samples, latitude_rings, min_segments_at_lat)
        """
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
        return eq_segments, meridian_samples, lat_rings, min_segments_lat

    _mode = _clamp_mode(mode)
    eq_segments, meridian_samples, lat_rings, min_segments_lat = _compute_layout(subdivisions_i)
    target_step_equator = two_pi * R / float(eq_segments)

    vertices_list: list[np.ndarray] = []

    # 経度線（極→極）
    if _mode in (1, 2):
        lat_vals = np.linspace(0.0, pi, meridian_samples + 1, dtype=np.float32)
        sin_lat = np.sin(lat_vals)
        cos_lat = np.cos(lat_vals)

        # 経度線の“本数”は mode=1/2 いずれも緯度リング数と同程度に揃える
        meridian_lines = max(8, lat_rings)
        stride = max(1, eq_segments // max(1, meridian_lines))
        for j in range(0, eq_segments, stride):
            lon = two_pi * j / eq_segments
            cos_lon = np.float32(np.cos(lon))
            sin_lon = np.float32(np.sin(lon))
            x = (sin_lat * cos_lon * R).astype(np.float32)
            y = (sin_lat * sin_lon * R).astype(np.float32)
            z = (cos_lat * R).astype(np.float32)
            line = np.stack((x, y, z), axis=1).astype(np.float32)
            vertices_list.append(line)

    # 緯度リング（周方向）
    if _mode in (0, 2):
        for i in range(1, lat_rings):  # 極は除外
            lat = pi * i / lat_rings
            r = abs(np.sin(lat)) * R
            if r <= 1e-9:
                continue
            segments_at_lat = int(np.ceil((two_pi * r) / max(1e-9, target_step_equator)))
            segments_at_lat = max(min_segments_lat, segments_at_lat)

            angles = np.linspace(0.0, two_pi, segments_at_lat + 1, dtype=np.float32)
            x = (np.cos(angles) * r).astype(np.float32)
            y = (np.sin(angles) * r).astype(np.float32)
            z = np.full_like(x, fill_value=np.cos(lat) * R, dtype=np.float32)
            ring = np.stack((x, y, z), axis=1).astype(np.float32)
            vertices_list.append(ring)

    return vertices_list


@lru_cache(maxsize=128)
def _sphere_zigzag(subdivisions: int) -> list[np.ndarray]:
    """ジグザグ（螺旋）スタイルで球を生成。

    - 交差密度を上げるため、位相をずらした複数ストランドの螺旋を生成。
    - subdivisions が大きい場合でも過度に重くならないよう、
      1周あたりの点数を控えめにしつつストランド数で密度を確保。
    """
    s = int(subdivisions)
    total_rotations = 8 + 4 * s

    # ストランド数（位相オフセットで複数の螺旋）
    if s <= 0:
        strand_count = 2
    elif s == 1:
        strand_count = 3
    elif s == 2:
        strand_count = 4
    else:
        strand_count = 4

    # 1周あたりの点数（控えめに上限を設ける）
    base_ppr = 64 + 16 * min(s, 2)  # 64, 80, 96, ...（>=2 は 96）
    effective_ppr = base_ppr if strand_count <= 2 else max(48, base_ppr - 24)

    vertices_list: list[np.ndarray] = []

    for k in range(strand_count):
        phase = 2.0 * np.pi * (k / float(strand_count))
        points = int(total_rotations * effective_ppr)
        # t: 0→1 の等間隔。これに応じて y を 1→-1 へ線形に落とす。
        t = np.linspace(0.0, 1.0, points, dtype=np.float32)
        y = 1.0 - 2.0 * t
        radius = np.sqrt(np.maximum(0.0, 1.0 - y * y))
        theta = 2.0 * np.pi * total_rotations * t + phase
        x = np.cos(theta) * radius * 0.5
        z = np.sin(theta) * radius * 0.5
        y = y * 0.5

        vertices = np.stack(
            (x.astype(np.float32), y.astype(np.float32), z.astype(np.float32)), axis=1
        )

        # 短い線分列へ変換（隣接ペア）
        for i in range(len(vertices) - 1):
            seg = np.array([vertices[i], vertices[i + 1]], dtype=np.float32)
            vertices_list.append(seg)

    return vertices_list


@lru_cache(maxsize=128)
def _sphere_icosphere(subdivisions: int) -> list[np.ndarray]:
    """アイコスフィア手法（階層細分化）で球を生成します。

    引数:
        subdivisions: 細分化レベル（0–5）

    返り値:
        アイコスフィア用の頂点配列リスト
    """
    # アイコサヘドロンの頂点から開始
    phi = (1 + np.sqrt(5)) / 2  # 黄金比

    # アイコサヘドロンの12頂点
    base_vertices = np.array(
        [
            [-1, phi, 0],
            [1, phi, 0],
            [-1, -phi, 0],
            [1, -phi, 0],
            [0, -1, phi],
            [0, 1, phi],
            [0, -1, -phi],
            [0, 1, -phi],
            [phi, 0, -1],
            [phi, 0, 1],
            [-phi, 0, -1],
            [-phi, 0, 1],
        ],
        dtype=np.float32,
    )

    # 頂点を単位球に正規化
    norms = np.linalg.norm(base_vertices, axis=1, keepdims=True)
    base_vertices = base_vertices / norms * 0.5

    # アイコサヘドロンの三角面
    base_faces = [
        # 上部キャップ（三角形）
        (0, 11, 5),
        (0, 5, 1),
        (0, 1, 7),
        (0, 7, 10),
        (0, 10, 11),
        # 下部キャップ（三角形）
        (3, 9, 4),
        (3, 4, 2),
        (3, 2, 6),
        (3, 6, 8),
        (3, 8, 9),
        # 中央帯（三角形）
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

    def subdivide_triangle(v1, v2, v3, level):
        """三角形を再帰的に細分化して小三角形へ分割。"""
        if level <= 0:
            # ベースケース: 三角形の辺を返す
            return [(v1, v2), (v2, v3), (v3, v1)]

        # 中点を計算し球面へ射影
        def midpoint_on_sphere(p1, p2):
            mid = (p1 + p2) / 2
            norm = np.linalg.norm(mid)
            return mid / norm * 0.5  # 半径 0.5 の球へ射影

        m1 = midpoint_on_sphere(v1, v2)
        m2 = midpoint_on_sphere(v2, v3)
        m3 = midpoint_on_sphere(v3, v1)

        # 4つの新しい三角形を再帰細分化
        edges = []
        edges.extend(subdivide_triangle(v1, m1, m3, level - 1))
        edges.extend(subdivide_triangle(m1, v2, m2, level - 1))
        edges.extend(subdivide_triangle(m3, m2, v3, level - 1))
        edges.extend(subdivide_triangle(m1, m2, m3, level - 1))

        return edges

    # すべての辺（細分化込み）を生成
    all_edges = []
    for face in base_faces:
        v1, v2, v3 = base_vertices[face[0]], base_vertices[face[1]], base_vertices[face[2]]
        edges = subdivide_triangle(v1, v2, v3, subdivisions)
        all_edges.extend(edges)

    # 辺を頂点配列へ変換（重複削除）
    vertices_list = []
    seen_edges = set()

    for edge in all_edges:
        # 辺のハッシュ可能な表現を作成
        edge_key = tuple(sorted([tuple(edge[0]), tuple(edge[1])]))

        if edge_key not in seen_edges:
            seen_edges.add(edge_key)
            line = np.array([edge[0], edge[1]], dtype=np.float32)
            vertices_list.append(line)

    return vertices_list


@lru_cache(maxsize=128)
def _sphere_rings(subdivisions: int, mode: int = 2) -> list[np.ndarray]:
    """水平リングで球を生成します。

    引数:
        subdivisions: 細分化レベル（0–5）
        mode: リング種類選択（0: 横リングのみ, 1: 縦リングのみ, 2: 両方）

    返り値:
        リングの頂点配列リスト
    """
    R = 0.5
    ring_count = 5 + 12 * subdivisions  # 各軸の分割数（縦方向のスライス数）

    # 周方向密度: 赤道の分割数を基準に決定（latlon と整合）
    equator_segments = max(16, 64 * (int(subdivisions) + 1))
    if int(subdivisions) <= 0:
        # 最小 subdiv の周方向のカクつきを緩和（過度な増加は避ける）
        equator_segments = max(equator_segments, 160)
    target_step_equator = 2 * np.pi * R / float(equator_segments)
    min_segments = 24 if int(subdivisions) <= 0 else 8

    vertices_list = []

    # 正規化
    _mode = int(mode)
    if _mode < 0:
        _mode = 0
    elif _mode > 2:
        _mode = 2

    # 高さごとに水平リング（Y 一定の XY 面の円）を作成
    if _mode in (0, 2):
        for i in range(ring_count):
            # 高さは -R から R
            y = -R + (i / (ring_count - 1)) * (2 * R)
            if abs(y) <= R:
                radius = float(np.sqrt(max(0.0, R * R - y * y)))
                if radius <= 1e-9:
                    continue
                segments = int(np.ceil((2 * np.pi * radius) / max(1e-9, target_step_equator)))
                segments = max(min_segments, segments)
                angles = np.linspace(0.0, 2 * np.pi, segments + 1, dtype=np.float32)
                x_vals = (radius * np.cos(angles)).astype(np.float32)
                z_vals = (radius * np.sin(angles)).astype(np.float32)
                y_arr = np.full_like(x_vals, fill_value=np.float32(y))
                ring_points = np.stack((x_vals, y_arr, z_vals), axis=1).astype(np.float32)
                vertices_list.append(ring_points)

    # X 軸に垂直なリング（YZ 平面の円）
    if _mode in (1, 2):
        for i in range(ring_count):
            # X 位置は -R から R
            x = -R + (i / (ring_count - 1)) * (2 * R)
            if abs(x) <= R:
                radius = float(np.sqrt(max(0.0, R * R - x * x)))
                if radius <= 1e-9:
                    continue
                segments = int(np.ceil((2 * np.pi * radius) / max(1e-9, target_step_equator)))
                segments = max(min_segments, segments)
                angles = np.linspace(0.0, 2 * np.pi, segments + 1, dtype=np.float32)
                y_vals = (radius * np.cos(angles)).astype(np.float32)
                z_vals = (radius * np.sin(angles)).astype(np.float32)
                x_arr = np.full_like(y_vals, fill_value=np.float32(x))
                ring_points = np.stack((x_arr, y_vals, z_vals), axis=1).astype(np.float32)
                vertices_list.append(ring_points)

    # Z 軸に垂直なリング（XY 平面の円）
    if _mode in (1, 2):
        for i in range(ring_count):
            # Z 位置は -R から R
            z = -R + (i / (ring_count - 1)) * (2 * R)
            if abs(z) <= R:
                radius = float(np.sqrt(max(0.0, R * R - z * z)))
                if radius <= 1e-9:
                    continue
                segments = int(np.ceil((2 * np.pi * radius) / max(1e-9, target_step_equator)))
                segments = max(min_segments, segments)
                angles = np.linspace(0.0, 2 * np.pi, segments + 1, dtype=np.float32)
                x_vals = (radius * np.cos(angles)).astype(np.float32)
                y_vals = (radius * np.sin(angles)).astype(np.float32)
                z_arr = np.full_like(x_vals, fill_value=np.float32(z))
                ring_points = np.stack((x_vals, y_vals, z_arr), axis=1).astype(np.float32)
                vertices_list.append(ring_points)

    return vertices_list


# UI/インデックス用の型順序（0..N-1）
_STYLE_ORDER = ["latlon", "zigzag", "icosphere", "rings"]


@shape
def sphere(
    *,
    subdivisions: int = 1,
    sphere_type: int = 0,
    mode: int = 2,
    **_params: Any,
) -> Geometry:
    """半径1の球を生成します（関数版）。

    引数:
        subdivisions: 細分化レベル（0–5）
        sphere_type: 描画スタイル（int, 0..3）
            0: 緯経線（latlon）
            1: ジグザグ（zigzag）
            2: アイコスフィア（icosphere）
            3: リング（rings）

    返り値:
        球のジオメトリを含む Geometry
    """
    # MIN_SUBDIVISIONS = 0
    # MAX_SUBDIVISIONS = 5
    # subdivisions_int = int(round(subdivisions))
    # if subdivisions_int < MIN_SUBDIVISIONS:
    #     subdivisions_int = MIN_SUBDIVISIONS
    # if subdivisions_int > MAX_SUBDIVISIONS:
    #     subdivisions_int = MAX_SUBDIVISIONS

    # sphere_type に応じて生成方式を選択（整数インデックス制御）
    idx = int(sphere_type)
    if idx < 0:
        idx = 0
    elif idx >= len(_STYLE_ORDER):
        idx = len(_STYLE_ORDER) - 1

    builders = (
        _sphere_latlon,
        _sphere_zigzag,
        _sphere_icosphere,
        _sphere_rings,
    )
    if idx == 0:
        vertices_list = _sphere_latlon(subdivisions, int(mode))
    elif idx == 3:
        vertices_list = _sphere_rings(subdivisions, int(mode))
    else:
        vertices_list = builders[idx](subdivisions)

    return Geometry.from_lines(vertices_list)


sphere.__param_meta__ = {
    "subdivisions": {"type": "integer", "min": 0, "max": 7, "step": 1},
    "sphere_type": {"type": "integer", "min": 0, "max": len(_STYLE_ORDER) - 1, "step": 1},
    # mode は latlon / rings スタイル専用（0: 横/緯度のみ, 1: 縦/経度のみ, 2: 両方）
    "mode": {"type": "integer", "min": 0, "max": 2, "step": 1},
}
