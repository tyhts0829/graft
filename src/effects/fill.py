from __future__ import annotations

"""
閉領域のハッチ塗り（外環＋穴の偶奇規則、複数方向、共通間隔）
テキストや多輪郭形状で「穴」を正しく抜きつつ、回転やスケールの影響を受けにくい安定した塗り密度を得る。

処理の流れ（開発者向け）
1) パラメータ正規化
   - `density/angle/angle_sets` はスカラまたは配列（図形グループごとにサイクル適用）。

2) 共平面判定と XY 整列（重要）
   - 各リングを試し、十分平面なリングから姿勢（回転 R と z オフセット）を推定。
   - 見つからない場合は PCA（SVD）で全体の法線を推定（最小特異ベクトル）。
   - 得られた姿勢で「全体」を XY に整列し、z 残差が絶対/相対閾値以内なら「共平面」とみなす。

3) 共平面経路（穴の塗り分け）
   - グルーピング: `util.polygon_grouping.build_evenodd_groups` で外環＋穴の集合へ分解。
     - 内包判定は「重心」ではなく各リングの代表点（第1頂点）で行う。ドーナツや非凸外周で重心が穴側に落ちる破綻を避けるため。
   - 共通間隔: 全体の未回転高さから 1 本あたりの `spacing` を算出し、全グループで共通に使用。
     - 小さな島（例: 句読点/ドット）でも見かけ密度が過剰にならない（本数ではなく間隔基準）。
   - 各グループに対し、`angle_sets` 本の方向で `_generate_line_fill_evenodd_multi` を実行（XY 空間でスキャンライン→偶奇規則で区間化）。
   - 生成した 2D 線分を `transform_back` で元姿勢の 3D に戻す。
   - `remove_boundary=False` なら元の輪郭線も先頭に残す。

4) 非共平面フォールバック
   - 各ポリゴンを個別に平面性チェックし、平面なら単一ポリゴン用のスキャンでハッチ、そうでなければ境界のみ返す（空出力回避）。
   - この経路では穴の偶奇統合は行わない（グローバルに共平面でない場合のみ到達）。

実装メモ
- 線本数のスケール `density` は 2..MAX（200）にクランプして `spacing` を求める（`_spacing_from_height`）。
- 平面性の閾値は絶対/相対の最大。
- 方向本数 `angle_sets=k` のとき、`angle + i*(pi/k)`（i=0..k-1）で等間隔回転。
- グループ順と各リングの順序は入力順を基準に安定化している。
"""


from typing import Iterable

import numpy as np
from numba import njit  # type: ignore[attr-defined]

from engine.core.geometry import Geometry
from util.geom3d_frame import choose_coplanar_frame
from util.geom3d_ops import transform_back, transform_to_xy_plane
from util.polygon_grouping import build_evenodd_groups

from .registry import effect

# 塗りつぶし線の最大密度（最大本数）
MAX_FILL_LINES = 1000
NONPLANAR_EPS_ABS = 1e-6
NONPLANAR_EPS_REL = 1e-5
PARAM_META = {
    "angle_sets": {"type": "integer", "min": 1, "max": 6, "step": 1},
    "density": {"type": "number", "min": 0.0, "max": MAX_FILL_LINES, "step": 1.0},
    "spacing_gradient": {"type": "number", "min": -5.0, "max": 5.0, "step": 0.1},
    "angle": {"type": "number", "min": 0.0, "max": 180.0},
    "remove_boundary": {"type": "boolean"},
}


@effect()
def fill(
    g: Geometry,
    *,
    angle_sets: int | list[int] | tuple[int, ...] = 1,
    angle: float | list[float] | tuple[float, ...] = 45.0,
    density: float | list[float] | tuple[float, ...] = 35.0,
    spacing_gradient: float | list[float] | tuple[float, ...] = 0.0,
    remove_boundary: bool = False,
) -> Geometry:
    """閉じた形状をハッチングで塗りつぶし（純関数）。

    Parameters
    ----------
    g : Geometry
        入力ジオメトリ。各行が 1 本のポリライン。
    angle_sets : int | list[int] | tuple[int, ...], default 1
        ハッチ方向の本数（1=単方向, 2=90°クロス, 3=60°間隔, ...）。
        配列指定時は図形（グループ）ごとに順番適用し、長さに応じてサイクルする。
    angle : float | list[float] | tuple[float, ...], default 45.0
        ハッチ角 [deg]。配列指定時は図形（グループ）ごとに順番適用し、長さに応じてサイクルする。
        共平面の場合は外環＋穴のグループ単位で適用。
    density : float | list[float] | tuple[float, ...], default 35.0
        ハッチ密度（本数のスケール）。配列指定時は図形（グループ）ごとに順番適用し、長さに応じてサイクルする。
        0 以下はその図形では no-op。最大は内部定数（200）。
    spacing_gradient : float | list[float] | tuple[float, ...], default 0.0
        スキャン方向に沿った線間隔の勾配。0.0 で一様間隔。
        正の値でスキャン軸の +側に行くほど線間隔が広がり、負の値で -側に行くほど広がる。
        絶対値が大きいほど変化が強くなり、配列指定時は図形（グループ）ごとに順番適用し、長さに応じてサイクルする。
    remove_boundary : bool, default False
        元の閉じた輪郭線を出力から除去する。False で輪郭を残す。

    Notes
    -----
    共平面入力では角度ごとのスキャン方向スパンから `spacing` を決定するため、affine の Z 回転に
    対しても実生成本数は概ね一定（`round(density)`、端数の影響で ±1 程度の誤差あり）。
    非共平面の入力では輪郭ごとの個別処理を行い、このとき各ポリラインが十分に平面で
    ないと判定された場合は塗りをスキップして元の境界のみを返す。
    """
    coords, offsets = g.as_arrays(copy=False)

    def _as_float_seq(x: float | Iterable[float]) -> list[float]:
        if isinstance(x, (int, float, np.floating)):
            return [float(x)]
        if isinstance(x, (list, tuple)):
            return [float(v) for v in x]
        # その他 Iterable は受け取らない（仕様上 list/tuple のみ）
        raise TypeError(
            "angle/density/spacing_gradient は float または list/tuple[float] を指定してください"
        )

    density_seq = _as_float_seq(density)  # type: ignore[arg-type]
    angle_seq_deg = _as_float_seq(angle)  # type: ignore[arg-type]
    # degree で受け取り radian に変換
    angle_seq = [float(np.deg2rad(a)) for a in angle_seq_deg]
    spacing_gradient_seq = _as_float_seq(spacing_gradient)  # type: ignore[arg-type]

    def _as_int_seq(x: int | Iterable[int]) -> list[int]:
        if isinstance(x, (int, np.integer)):
            return [int(x)]
        if isinstance(x, (list, tuple)):
            return [int(v) for v in x]
        raise TypeError("angle_sets は int または list/tuple[int] を指定してください")

    angle_sets_seq = _as_int_seq(angle_sets)  # type: ignore[arg-type]

    if offsets.size <= 1:
        return Geometry(coords.copy(), offsets.copy())

    if all(d <= 0.0 for d in density_seq):
        return Geometry(coords.copy(), offsets.copy())

    # angle_sets は図形（グループ）ごとに決定する（配列時はサイクル）

    planar_global, v2d_all, R_all, z_all, ref_height_global = choose_coplanar_frame(coords, offsets)

    if planar_global:
        results: list[np.ndarray] = []

        # 1) 境界保持（必要時）
        if not remove_boundary:
            for i in range(len(offsets) - 1):
                results.append(coords[offsets[i] : offsets[i + 1]].copy())

        # 2) 外環＋穴のグループ化（XYへ整列した座標で評価）
        groups = build_evenodd_groups(v2d_all, offsets)
        if ref_height_global <= 0:
            return Geometry(coords.copy(), offsets.copy())

        # 3) 共通間隔で各グループにハッチを適用（XY空間で生成→元姿勢へ戻す）
        for gi, ring_indices in enumerate(groups):
            d = density_seq[gi % len(density_seq)]
            if d <= 0.0:
                continue
            d = max(0.0, min(MAX_FILL_LINES, float(d)))
            grad = spacing_gradient_seq[gi % len(spacing_gradient_seq)]
            # グループの頂点配列にまとめ直し
            lines: list[np.ndarray] = []
            for idx in ring_indices:
                s, e = int(offsets[idx]), int(offsets[idx + 1])
                lines.append(v2d_all[s:e])
            if not lines:
                continue
            g_coords = np.concatenate(lines, axis=0)
            g_offsets = np.zeros(len(lines) + 1, dtype=np.int32)
            acc = 0
            for i, ln in enumerate(lines):
                acc += ln.shape[0]
                g_offsets[i + 1] = acc

            base_ang = angle_seq[gi % len(angle_seq)]
            k_i = angle_sets_seq[gi % len(angle_sets_seq)]
            k_i = int(k_i) if int(k_i) > 0 else 1
            # 全体の参照高さと density から共通間隔を決定（各グループで共通の“見かけ密度”）
            base_spacing = _spacing_from_height(ref_height_global, float(d))
            if base_spacing <= 0.0:
                continue
            for i in range(k_i):
                ang_i = float(base_ang) + (np.pi / k_i) * i
                segs_xy = _generate_line_fill_evenodd_multi(
                    g_coords,
                    g_offsets,
                    d,
                    ang_i,
                    spacing_override=base_spacing,
                    spacing_gradient=float(grad),
                )
                for seg in segs_xy:
                    results.append(transform_back(seg, R_all, z_all))

        return Geometry.from_lines(results)

    # 非平面は従来通りポリゴン個別に処理

    filled_results: list[np.ndarray] = []
    for i in range(len(offsets) - 1):
        vertices = coords[offsets[i] : offsets[i + 1]]

        d = density_seq[i % len(density_seq)]
        grad = spacing_gradient_seq[i % len(spacing_gradient_seq)]
        if d <= 0.0:
            # 塗り線無し、境界だけ保持
            if not remove_boundary:
                filled_results.append(vertices)
            continue
        base_ang = angle_seq[i % len(angle_seq)]
        k_i = angle_sets_seq[i % len(angle_sets_seq)]
        k_i = int(k_i) if int(k_i) > 0 else 1
        filled_results.extend(
            _fill_single_polygon(
                vertices,
                angle_sets=k_i,
                density=max(0.0, min(MAX_FILL_LINES, float(d))),
                angle=float(base_ang),
                spacing_gradient=float(grad),
                remove_boundary=remove_boundary,
            )
        )

    if not filled_results:
        return Geometry(coords.copy(), offsets.copy())

    return Geometry.from_lines(filled_results)


# UI 表示のためのメタ情報（RangeHint 構築に使用）
fill.__param_meta__ = PARAM_META


def _generate_line_fill(
    vertices: np.ndarray, density: float, angle: float = 0.0, spacing_gradient: float = 0.0
) -> list[np.ndarray]:
    """平行線塗りつぶしパターンを生成します。"""
    # 処理を簡素化するため XY 平面へ射影
    vertices_2d, rotation_matrix, z_offset = transform_to_xy_plane(vertices)

    # 2D 座標（未回転）
    coords_2d = vertices_2d[:, :2]

    # 角度が指定されている場合はポリゴンを回転（交点計算用の作業座標）
    if angle != 0.0:
        # 逆回転（作業座標へ）
        cos_inv, sin_inv = np.cos(-angle), np.sin(-angle)
        center_inv = np.mean(coords_2d, axis=0)
        coords_2d_centered = coords_2d - center_inv
        rot2_inv = np.array([[cos_inv, -sin_inv], [sin_inv, cos_inv]])
        work_2d = coords_2d_centered @ rot2_inv.T + center_inv
        # 正回転（生成した水平線を元角度へ戻す）を事前計算
        cos_fwd, sin_fwd = np.cos(angle), np.sin(angle)
        center_fwd = np.mean(vertices_2d[:, :2], axis=0)
        rot2_fwd = np.array([[cos_fwd, -sin_fwd], [sin_fwd, cos_fwd]])
    else:
        work_2d = coords_2d

    # 参照間隔用の“未回転”高さ（角度に依存しない間隔を実現）
    _min_y_ref = float(np.min(coords_2d[:, 1]))
    _max_y_ref = float(np.max(coords_2d[:, 1]))
    ref_height = _max_y_ref - _min_y_ref
    if ref_height <= 0:
        return []

    # 実際のスキャン範囲は作業座標（角度考慮）
    min_y = float(np.min(work_2d[:, 1]))
    max_y = float(np.max(work_2d[:, 1]))

    # 密度に基づいて線間隔を算出（逆数的: 0=疎, 1=密）
    # density=1.0 で MAX_FILL_LINES 本、density=0.0 でごく少数
    if density <= 0:
        return []

    # 間隔計算: 間隔が小さいほど線が多い
    # density=1.0 → バウンディングボックス内に MAX_FILL_LINES 本を想定
    # density=0.1 → より少ない本数
    spacing = _spacing_from_height(ref_height, float(density))
    if spacing <= 0:
        return []

    # スキャンラインの Y 座標を生成（spacing_gradient=0 なら一様間隔）
    y_values = _generate_y_values(min_y, max_y, spacing, float(spacing_gradient))
    if y_values.size == 0:
        return []
    fill_lines = []

    # 性能向上のためバッチ処理で交点計算（作業座標 = 角度考慮後）
    intersection_results = generate_line_intersections_batch(work_2d, y_values)

    for y, intersections in intersection_results:
        # 交点をソートし線分を生成
        intersections_sorted = np.sort(intersections)
        for i in range(0, len(intersections_sorted) - 1, 2):
            if i + 1 < len(intersections_sorted):
                x1, x2 = intersections_sorted[i], intersections_sorted[i + 1]
                line_2d = np.array([[x1, y], [x2, y]], dtype=np.float32)

                # 必要に応じて正方向の回転を適用
                if angle != 0.0:
                    line_2d = (line_2d - center_fwd) @ rot2_fwd.T + center_fwd

                # 3D に戻す
                line_3d = np.hstack([line_2d, np.zeros((2, 1), dtype=np.float32)])

                # 元の姿勢に戻す
                line_final = transform_back(line_3d, rotation_matrix, z_offset)
                fill_lines.append(line_final)

    return fill_lines


# ── 偶奇規則ベースの多輪郭塗り（平面XY向け） ─────────────────────────────────
def _is_polygon_planar(
    vertices: np.ndarray, *, eps_abs: float = NONPLANAR_EPS_ABS, eps_rel: float = NONPLANAR_EPS_REL
) -> bool:
    """単一ポリラインが“ほぼ平面”かを簡易判定する。

    - `transform_to_xy_plane` で先頭3点が張る平面へ整列し、z 残差の最大値を評価する。
    - 閾値は絶対/相対の最大値（相対は元座標のバウンディングボックス対角でスケール不変）。
    """
    if vertices.shape[0] < 3:
        return False
    v = np.asarray(vertices, dtype=np.float32)
    v2d, _R, _z = transform_to_xy_plane(v)
    z = v2d[:, 2]
    z_span = float(np.max(np.abs(z))) if z.size else 0.0
    # 元座標の対角長
    mins = np.min(v, axis=0)
    maxs = np.max(v, axis=0)
    diag = float(np.sqrt(np.sum((maxs - mins) ** 2)))
    threshold = max(float(eps_abs), float(eps_rel) * diag)
    return z_span <= threshold


def _spacing_from_height(height: float, density: float) -> float:
    """高さと密度から一定間隔を算出（ライン本数は2..MAXでクランプ）。"""
    num_lines = int(round(float(density)))
    if num_lines < 2:
        num_lines = 2
    if num_lines > MAX_FILL_LINES:
        num_lines = MAX_FILL_LINES
    if height <= 0 or num_lines <= 0:
        return 0.0
    return float(height) / float(num_lines)


def _generate_y_values(
    min_y: float, max_y: float, base_spacing: float, spacing_gradient: float
) -> np.ndarray:
    """スキャン範囲と勾配からスキャンラインの Y 座標列を生成する。"""
    if not np.isfinite(base_spacing) or base_spacing <= 0.0:
        return np.empty(0, dtype=np.float32)
    if not np.isfinite(spacing_gradient):
        spacing_gradient = 0.0
    if max_y <= min_y:
        return np.empty(0, dtype=np.float32)

    # 勾配ゼロは元の一様間隔と同じ
    if abs(spacing_gradient) < 1e-6:
        return np.arange(min_y, max_y, base_spacing, dtype=np.float32)

    height = max_y - min_y

    # 勾配の暴走を抑えるためソフトクランプ
    k = float(spacing_gradient)
    if k > 4.0:
        k = 4.0
    elif k < -4.0:
        k = -4.0

    # 平均間隔がおおよそ一定になるように正規化係数を導入
    if abs(k) < 1e-3:
        c = 1.0
    else:
        c = k / (2.0 * float(np.sinh(k / 2.0)))

    y_values: list[float] = []
    y = float(min_y)
    # 細かすぎる間隔への発散を避けるための下限
    min_step = base_spacing * 1e-3

    while y < max_y:
        t = (y - min_y) / height  # 0..1
        factor = c * float(np.exp(k * (t - 0.5)))
        step = base_spacing * max(factor, 0.0)
        if step < min_step:
            step = min_step

        y_values.append(y)
        y += step

    return np.asarray(y_values, dtype=np.float32)


def _scan_span_for_angle_xy(coords_2d: np.ndarray, angle: float) -> float:
    """XY 座標群に対し、角度 `angle` のスキャン方向（y'=x*sinθ + y*cosθ）のスパン長を返す。

    - 入力は XY 整列済みの 2D 座標を想定。
    - 戻り値は max(y')-min(y')。点数が不足するなどで無効な場合は 0.0。
    """
    if coords_2d.size == 0:
        return 0.0
    s = float(np.sin(angle))
    c = float(np.cos(angle))
    y_proj = coords_2d[:, 0] * s + coords_2d[:, 1] * c
    return float(np.max(y_proj) - np.min(y_proj)) if y_proj.size else 0.0


def _generate_line_fill_evenodd_multi(
    coords: np.ndarray,
    offsets: np.ndarray,
    density: float,
    angle: float = 0.0,
    spacing_override: float | None = None,
    spacing_gradient: float = 0.0,
) -> list[np.ndarray]:
    """複数輪郭を偶奇規則でまとめてハッチング（XY 平面前提）。"""
    if density <= 0 or offsets.size <= 1:
        return []

    coords_2d = coords[:, :2].astype(np.float32, copy=False)

    # 角度回転のための中心（全体）
    center = np.mean(coords_2d, axis=0)
    work_2d = coords_2d
    rot2_fwd: np.ndarray | None = None
    if angle != 0.0:
        # 逆回転（作業座標へ）
        cos_inv, sin_inv = np.cos(-angle), np.sin(-angle)
        rot_inv = np.array([[cos_inv, -sin_inv], [sin_inv, cos_inv]], dtype=np.float32)
        work_2d = (coords_2d - center) @ rot_inv.T + center
        # 正回転（線分を戻す）
        cos_fwd, sin_fwd = np.cos(angle), np.sin(angle)
        rot2_fwd = np.array([[cos_fwd, -sin_fwd], [sin_fwd, cos_fwd]], dtype=np.float32)

    # 参照間隔用の“未回転”高さ（角度に依存しない間隔を実現）
    ref_min_y = float(np.min(coords_2d[:, 1]))
    ref_max_y = float(np.max(coords_2d[:, 1]))
    ref_height = ref_max_y - ref_min_y
    if ref_height <= 0:
        return []

    # 実スキャン範囲は作業座標系（角度考慮）
    min_y = float(np.min(work_2d[:, 1]))
    max_y = float(np.max(work_2d[:, 1]))

    if spacing_override is None:
        spacing = _spacing_from_height(ref_height, float(density))
    else:
        spacing = float(spacing_override)
    if not np.isfinite(spacing) or spacing <= 0:
        return []

    y_values = _generate_y_values(min_y, max_y, spacing, float(spacing_gradient))
    out_lines: list[np.ndarray] = []
    z0 = float(coords[0, 2])

    # スキャンライン毎に全輪郭から交点を収集し偶奇規則で区間を作成
    for y in y_values:
        # 交点収集
        intersections_all = []  # list[float]
        for i in range(len(offsets) - 1):
            if offsets[i + 1] - offsets[i] < 2:
                continue
            poly = work_2d[offsets[i] : offsets[i + 1]]
            xs = find_line_intersections_njit(poly, float(y))
            if len(xs) > 0:
                intersections_all.extend(xs.tolist())

        if len(intersections_all) < 2:
            continue

        xs_sorted = np.sort(np.asarray(intersections_all, dtype=np.float32))
        # ペアで塗り区間を生成（偶奇）
        for j in range(0, xs_sorted.size - 1, 2):
            x1 = float(xs_sorted[j])
            x2 = float(xs_sorted[j + 1])
            if x2 - x1 <= 1e-9:
                continue
            seg2d = np.array([[x1, y], [x2, y]], dtype=np.float32)
            if angle != 0.0 and rot2_fwd is not None:
                # 元の角度へ戻す（前計算した行列を使用）
                seg2d = (seg2d - center) @ rot2_fwd.T + center
            # 3D に（一定 z）
            seg3d = np.hstack([seg2d, np.full((2, 1), z0, dtype=np.float32)])
            out_lines.append(seg3d)

    return out_lines


def _fill_single_polygon(
    vertices: np.ndarray,
    *,
    angle_sets: int,
    density: float,
    angle: float,
    spacing_gradient: float,
    remove_boundary: bool,
) -> list[np.ndarray]:
    """単一ポリゴンに対して塗りつぶし線/ドットを生成し、元の輪郭と合わせて返す。"""
    if len(vertices) < 3:
        return [vertices]
    # 平面性が不足する場合は塗りをスキップ（境界のみ返す）
    if not _is_polygon_planar(vertices):
        return [vertices]

    out: list[np.ndarray] = [] if remove_boundary else [vertices]
    k = int(angle_sets) if int(angle_sets) > 0 else 1
    for i in range(k):
        ang_i = angle + (np.pi / k) * i
        out.extend(_generate_line_fill(vertices, density, ang_i, spacing_gradient=spacing_gradient))
    return out


# 後方互換クラスは廃止（関数APIのみ）


# 高速化のための Numba コンパイル関数群
@njit(cache=True)
def find_line_intersections_njit(polygon: np.ndarray, y: float) -> np.ndarray:
    """水平線とポリゴンエッジの交点を検索します（Numba最適化版）。"""
    n = len(polygon)
    intersections = np.full(n, -1.0)  # 無効値で事前確保
    count = 0

    for i in range(n):
        p1 = polygon[i]
        p2 = polygon[(i + 1) % n]

        # 線分が水平線と交差するか判定
        if (p1[1] <= y < p2[1]) or (p2[1] <= y < p1[1]):
            # 交点の x 座標を計算
            if p2[1] != p1[1]:  # 0 除算回避
                x = p1[0] + (y - p1[1]) * (p2[0] - p1[0]) / (p2[1] - p1[1])
                intersections[count] = x
                count += 1

    return intersections[:count]


@njit(cache=True)
def generate_line_intersections_batch(polygon: np.ndarray, y_values: np.ndarray) -> list:
    """複数のy値に対して交点を一括計算（Numba最適化版）。"""
    results = []
    for y in y_values:
        intersections = find_line_intersections_njit(polygon, y)
        if len(intersections) >= 2:
            results.append((y, intersections))
    return results


## 旧 dots 用の交点探索は削除（グルーピング関連は util/polygon_grouping.py へ移設）
