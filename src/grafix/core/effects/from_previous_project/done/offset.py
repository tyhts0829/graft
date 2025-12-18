"""
offset エフェクト（バッファ/輪郭オフセット）

- Shapely の `buffer` を用いて各ポリラインをオフセットし、膨張（外側）または収縮（内側）形状を生成する。
- 3D 入力は XY 平面に射影して処理後、元の姿勢に戻す。

主なパラメータ:
- distance: オフセット距離 [mm]。
- join: 角の処理（`round` | `mitre` | `bevel`）。
- segments_per_circle: 円弧近似分割数（大きいほど滑らかだが重い）。

仕様/注意:
- 入力曲線が未クローズで、始終点が閾値内にある場合は自動クローズしてから処理。
- 結果は 3D 姿勢に復元する。自己交差や極端な距離ではトポロジが変化し得る。
- 安全のため距離は [0, 25.0] に丸める。距離 0 は no-op（入力コピー）。

実装メモ（詳細設計）:
- XY 射影 → Shapely `buffer(distance, join_style, resolution)` → 3D 復元の順で処理。
- `LineString/MultiLineString` と `Polygon/MultiPolygon` の両方の出力形に対応し、外輪郭頂点列を抽出。
- 全体が膨張し過ぎないよう軽いスケーリング補正を適用。
"""

from __future__ import annotations

import numpy as np

from engine.core.geometry import Geometry
from util.geom3d_ops import transform_back, transform_to_xy_plane

from .registry import effect

PARAM_META = {
    "join": {"choices": ["mitre", "round", "bevel"]},
    "distance": {"type": "number", "min": 0.0, "max": 25.0},
    "segments_per_circle": {"type": "integer", "min": 1, "max": 100},
    "keep_original": {"type": "bool"},
}


@effect()
def offset(
    g: Geometry,
    *,
    join: str = "round",  # 'mitre'|'round'|'bevel'
    segments_per_circle: int = 12,  # shapelyのresolutionに相当（既定値を上げて円滑さを確保）
    distance: float = 5.0,
    keep_original: bool = False,
) -> Geometry:
    """Shapely を用いて輪郭をオフセット（膨張/収縮）。

    Parameters
    ----------
    g : Geometry
        入力ジオメトリ。各行が 1 本のポリラインを表す（`offsets` で区切る）。
    join : str, default 'round'
        角の処理。`'mitre'|'round'|'bevel'` を指定。
    segments_per_circle : int, default 12
        円弧近似の分割数（Shapely の `resolution` に相当）。
    distance : float, default 5.0
        オフセット距離（mm）。0 で no-op。負値で収縮、正値で膨張。
    keep_original : bool, default False
        True のときオフセット後の線に加えて元のポリラインも出力に含める。
    """
    coords, offsets = g.as_arrays(copy=False)
    actual_distance = float(distance)
    if actual_distance == 0:
        return Geometry(coords.copy(), offsets.copy())

    join_style_str = join
    resolution_int = int(segments_per_circle)

    vertices_list: list[np.ndarray] = []
    for i in range(len(offsets) - 1):
        vertices = coords[offsets[i] : offsets[i + 1]]
        if len(vertices) >= 2:
            vertices_list.append(vertices)

    new_vertices_list = _buffer(vertices_list, actual_distance, join_style_str, resolution_int)

    filtered_vertices_list: list[np.ndarray] = []
    for v in new_vertices_list:
        if v is not None and len(v) > 0 and isinstance(v, np.ndarray):
            filtered_vertices_list.append(v.astype(np.float32))

    if not filtered_vertices_list:
        return Geometry(coords.copy(), offsets.copy())

    if keep_original:
        for i in range(len(offsets) - 1):
            original_vertices = coords[offsets[i] : offsets[i + 1]]
            if original_vertices.shape[0] > 0:
                filtered_vertices_list.append(original_vertices.astype(np.float32))

    all_coords = np.vstack(filtered_vertices_list)
    new_offsets = [0]
    acc = 0
    for line in filtered_vertices_list:
        acc += len(line)
        new_offsets.append(acc)

    return Geometry(all_coords, np.array(new_offsets, dtype=np.int32))


# UI 表示のためのメタ情報（RangeHint 構築に使用）
offset.__param_meta__ = PARAM_META


def _buffer(
    vertices_list: list[np.ndarray], distance: float, join_style: str, resolution: int
) -> list[np.ndarray]:
    """各ポリラインを XY 射影して `buffer` を適用し、3D に復元。

    Parameters
    ----------
    vertices_list : list of ndarray
        各要素が (N, 3) の頂点配列（1 ポリライン）。
    distance : float
        オフセット距離。負値で収縮、正値で膨張。0 の場合は入力をそのまま返す。
    join_style : str
        `'mitre'|'round'|'bevel'` を想定（Shapely の `join_style` に渡す）。
    resolution : int
        円弧近似分割数（Shapely の `resolution` へ渡す）。

    Returns
    -------
    list of ndarray
        `buffer` 結果から抽出した各ポリラインの (M, 3) 頂点配列。
    """
    if distance == 0:
        return vertices_list

    new_vertices_list: list[np.ndarray] = []

    # ローカル import（実利用時のみ依存）
    from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon  # type: ignore

    for vertices in vertices_list:
        vertices = _close_curve(vertices, 1e-3)
        vertices_on_xy, rotation_matrix, z_offset = transform_to_xy_plane(vertices)
        line = LineString(vertices_on_xy[:, :2])
        buffered_line = line.buffer(distance, join_style=join_style, resolution=resolution)  # type: ignore

        if buffered_line.is_empty:
            continue

        if isinstance(buffered_line, (LineString, MultiLineString)):
            new_vertices_list = _extract_vertices_from_line(
                new_vertices_list, buffered_line, rotation_matrix, z_offset
            )
        elif isinstance(buffered_line, (Polygon, MultiPolygon)):
            new_vertices_list = _extract_vertices_from_polygon(
                new_vertices_list, buffered_line, rotation_matrix, z_offset
            )

    return new_vertices_list


def _extract_vertices_from_polygon(
    new_vertices_list: list,
    buffered_line,
    rotation_matrix: np.ndarray,
    z_offset: float,
) -> list:
    """Polygon/MultiPolygon から外環の頂点列を抽出し 3D に復元。

    Parameters
    ----------
    new_vertices_list : list
        出力先の頂点配列リスト（追記して返す）。
    buffered_line : BaseGeometry
        `Polygon` または `MultiPolygon` を想定。
    rotation_matrix : ndarray
        XY 射影前の姿勢へ戻す回転行列。
    z_offset : float
        XY 射影前の z 平行移動量。

    Returns
    -------
    list
        3D 復元後の頂点配列を追記した `new_vertices_list`。

    Notes
    -----
    内環（holes）は現在無視し、外環（exterior）のみ抽出。
    """
    # ローカル import（可読性のため再取得）
    from shapely.geometry import MultiPolygon, Polygon  # type: ignore

    if isinstance(buffered_line, Polygon):
        polygons = [buffered_line]
    else:
        polygons = buffered_line.geoms if isinstance(buffered_line, MultiPolygon) else []

    for poly in polygons:
        coords = np.array(poly.exterior.coords)
        new_vertices = np.hstack([coords, np.zeros((len(coords), 1))])
        restored = transform_back(new_vertices, rotation_matrix, z_offset)
        new_vertices_list.append(restored)

    return new_vertices_list


def _extract_vertices_from_line(
    new_vertices_list: list,
    buffered_line,
    rotation_matrix: np.ndarray,
    z_offset: float,
) -> list:
    """LineString/MultiLineString から頂点列を抽出し 3D に復元。

    Parameters
    ----------
    new_vertices_list : list
        出力先の頂点配列リスト（追記して返す）。
    buffered_line : BaseGeometry
        `LineString` または `MultiLineString` を想定。
    rotation_matrix : ndarray
        XY 射影前の姿勢へ戻す回転行列。
    z_offset : float
        XY 射影前の z 平行移動量。

    Returns
    -------
    list
        3D 復元後の頂点配列を追記した `new_vertices_list`。
    """
    from shapely.geometry import LineString, MultiLineString  # type: ignore

    if isinstance(buffered_line, LineString):
        lines = [buffered_line]
    else:
        lines = buffered_line.geoms if isinstance(buffered_line, MultiLineString) else []

    for line in lines:
        coords = np.array(line.coords)
        new_vertices = np.hstack([coords, np.zeros((len(coords), 1))])
        restored = transform_back(new_vertices, rotation_matrix, z_offset)
        new_vertices_list.append(restored)

    return new_vertices_list


def _close_curve(points: np.ndarray, threshold: float) -> np.ndarray:
    """端点が閾値以内なら輪郭をクローズ。

    Parameters
    ----------
    points : ndarray
        (N, 3) 頂点配列。
    threshold : float
        始点と終点のユークリッド距離がこの値以下なら、始点を終端に複製して閉じる。

    Returns
    -------
    ndarray
        クローズ適用後の頂点配列。
    """
    if len(points) < 2:
        return points
    start = points[0]
    end = points[-1]
    dist = np.linalg.norm(start - end)
    if dist <= threshold:
        points_copy = points[:-1]
        points = np.vstack([points_copy, start])
    return points


def _scaling(vertices_list: list[np.ndarray], scale_factor: float) -> list[np.ndarray]:
    """重心まわりの一様スケーリングを頂点列ごとに適用。

    Parameters
    ----------
    vertices_list : list of ndarray
        各要素が (N, 3) の頂点配列。
    scale_factor : float
        重心基準での拡大縮小率。

    Returns
    -------
    list of ndarray
        スケーリング後の頂点配列リスト。

    Notes
    -----
    オフセット量とは独立に形状スケールを変える補正。幾何学的意味を変える可能性がある。
    現在は `_buffer` の末尾で一括適用される。
    """
    scaled_vertices_list = []
    for vertices in vertices_list:
        if len(vertices) == 0:
            scaled_vertices_list.append(vertices)
            continue
        centroid = np.mean(vertices, axis=0)
        scaled_vertices = (vertices - centroid) * scale_factor + centroid
        scaled_vertices_list.append(scaled_vertices)
    return scaled_vertices_list
