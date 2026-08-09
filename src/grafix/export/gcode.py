"""
どこで: `src/grafix/export/gcode.py`。
何を: realize 済みシーンを G-code として保存する関数を提供する。
なぜ: ペンプロッタ向け出力を interactive 依存なしで追加できるようにするため。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from math import hypot
from math import isqrt
from pathlib import Path
from typing import TextIO

import numpy as np

from grafix.file_io import atomic_text_writer
from grafix.core.gcode_params import GCodeParams
from grafix.core.pipeline import RealizedLayer

# --- 実装全体の前提（概要）---
#
# 入力（RealizedLayer）は「連結 coords 配列 + offsets（polyline 境界）」を持つ。
# 本モジュールはそれを G-code（主に G1）へ落とし込むため、以下の順で処理する。
#
# 1) 紙の安全領域（paper_margin_mm）を決める
# 2) polyline を安全領域へクリップし、紙内に残る連続区間（stroke）へ分割する
# 3) layer 内の全 stroke を（任意で）並び替える（optimize_travel）
# 4) 最終順序の隣接 stroke 間が十分短い場合だけ、描画で繋ぐ（bridge_draw_distance）
#    - これは「移動距離短縮」ではなく「線を足す」トレードオフである点に注意
#    - ordering / bridge の境界は元 polyline ではなく layer とする
# 5) move ごとに (canvas -> machine) 変換 → 丸め → bed 範囲検証 → `G1 X.. Y..` を出す
#
# 決定性（同一入力→同一出力）のための工夫:
# - 数値は常に固定小数フォーマット（_fmt_float）
# - bed 検証は「実際に出力する値（丸め後）」に対して行う（_quantize_xy→_validate_bed_xy）
# - stroke の距離比較は量子化（整数）し、タイブレーク規則を固定する


def _fmt_float(value: float, *, decimals: int) -> str:
    """小数を固定桁の文字列にして返す。

    Notes
    -----
    G-code はテキストであり、出力が少しでも揺れると差分比較が難しくなる。
    そのため常に固定桁でフォーマットし、`-0.000` のような表現だけを `0.000` に正規化する。
    """

    # G-code は単なるテキストなので、同じ値でも表記揺れがあると差分が読めなくなる。
    # ここでは「固定小数・固定桁」で文字列化し、出力の決定性を最優先する。
    text = f"{float(value):.{int(decimals)}f}"
    if text.startswith("-0") and float(text) == 0.0:
        # `-0.000` は数値としては 0 なので、見た目だけの符号差を潰して diff を安定させる。
        return text[1:]
    return text


def _is_inside_rect(
    xy: tuple[float, float], rect: tuple[float, float, float, float]
) -> bool:
    """点が矩形（閉区間）に含まれるなら True を返す。"""

    # クリップと整合するよう「閉区間（境界を含む）」で判定する。
    # 紙境界上の点は描画許可としないと、出入口で意図せず途切れやすい。
    x, y = xy
    x_min, x_max, y_min, y_max = rect
    return (x_min <= x <= x_max) and (y_min <= y <= y_max)


def _clip_segment_to_rect(
    p0: tuple[float, float],
    p1: tuple[float, float],
    rect: tuple[float, float, float, float],
    *,
    eps: float = 1e-12,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """線分を矩形へクリップし、内側部分の端点 2 点を返す。"""

    # Liang–Barsky line clipping を使う。
    # 線分をパラメータ表現 `P(u)=P0 + u*(P1-P0), u in [0,1]` にして、
    # 矩形の 4 辺に対する不等式制約を u の範囲 `[u1, u2]` として更新する。
    #
    # 重要: この関数は「交点 1 点だけ」になるような極短線分を None 扱いにする。
    # クリップの境界交点が連続して出るケースで、無意味なゼロ移動 G1 を増やさないため。

    x0, y0 = p0
    x1, y1 = p1
    x_min, x_max, y_min, y_max = rect

    dx = x1 - x0
    dy = y1 - y0

    # 可視区間の u 範囲（最初は線分全体）。
    # 以降の制約更新で、矩形内に残る u の範囲へ狭めていく。
    u1 = 0.0
    u2 = 1.0

    # 各辺の不等式を `p*u <= q` の形に落とし込む。
    #   x >= x_min  -> -dx*u <= x0 - x_min
    #   x <= x_max  ->  dx*u <= x_max - x0
    #   y >= y_min  -> -dy*u <= y0 - y_min
    #   y <= y_max  ->  dy*u <= y_max - y0
    p = (-dx, dx, -dy, dy)
    q = (x0 - x_min, x_max - x0, y0 - y_min, y_max - y0)

    for pi, qi in zip(p, q):
        # 方向成分が 0 に近い（= 辺と平行）場合:
        # - qi < 0 なら「矩形の外側に平行」なので交差なし
        # - それ以外は u 制約を更新しない
        if abs(pi) < eps:
            if qi < 0.0:
                return None
            continue

        # 辺との交点に対応する u 値。
        # u の許容範囲を狭めるだけなので、交点座標は最後に 1 回だけ計算する。
        r = qi / pi
        if pi < 0.0:
            # こちら側は下限更新（u >= r）。
            if r > u2:
                return None
            if r > u1:
                u1 = r
        else:
            # こちら側は上限更新（u <= r）。
            if r < u1:
                return None
            if r < u2:
                u2 = r

    if u1 > u2:
        return None

    # クリップ後の端点（u1/u2 が更新された線分）。
    # u1==u2 に近い場合は「点」に潰れるので後段で None 扱いにする。
    ax = x0 + u1 * dx
    ay = y0 + u1 * dy
    bx = x0 + u2 * dx
    by = y0 + u2 * dy

    # 交点が 1 点に潰れてしまう場合は「線分なし」として扱う。
    if hypot(bx - ax, by - ay) < eps:
        return None

    return (ax, ay), (bx, by)


def _append_point(
    points: list[tuple[float, float]],
    xy: tuple[float, float],
    *,
    eps: float = 1e-9,
) -> None:
    """直前点と同一（極近傍）なら追加せず、そうでなければ追加する。"""

    # クリップ処理では境界交点が連続しやすく、
    # そのまま出力すると「同一点へ移動する G1」が増えてファイルが読みにくくなる。
    # ここでは微小差を許容して点を間引き、G-code の冗長さを抑える。
    if points:
        x0, y0 = points[-1]
        x1, y1 = xy
        if hypot(x1 - x0, y1 - y0) < eps:
            return
    points.append(xy)


def _clip_polyline_to_rect(
    polyline_xy: np.ndarray,
    rect: tuple[float, float, float, float],
) -> list[list[tuple[float, float]]]:
    """polyline を矩形へクリップし、紙内に残る polyline 群を返す。"""

    # 方針:
    # - 元 polyline を「連続点の線分列」として扱う
    # - 各線分を矩形へクリップする
    # - クリップ結果が連続する範囲を 1 つの polyline としてまとめ、断絶があれば分割する
    #
    # 出力は「紙内に残る polyline 群」なので、紙外を跨ぐ部分はここで断ち切られる。
    # これにより export 側は「分断された区間は必ずペンアップ移動」を簡単に実現できる。

    if polyline_xy.shape[0] < 2:
        return []

    out: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []

    for i in range(int(polyline_xy.shape[0]) - 1):
        # 元線分の端点。
        p0 = (float(polyline_xy[i, 0]), float(polyline_xy[i, 1]))
        p1 = (float(polyline_xy[i + 1, 0]), float(polyline_xy[i + 1, 1]))
        clipped = _clip_segment_to_rect(p0, p1, rect)

        if clipped is None:
            # 今の線分は紙内に寄与しないので、溜めていた polyline を確定してリセットする。
            if current and len(current) >= 2:
                out.append(current)
            current = []
            continue

        a, b = clipped
        if not current:
            # 新しい紙内 polyline を開始する。
            current = []
            _append_point(current, a)
            _append_point(current, b)
        else:
            if hypot(a[0] - current[-1][0], a[1] - current[-1][1]) > 1e-9:
                # 連続性が崩れた（紙外を跨いだ / 別辺から再侵入した等）ので分割する。
                if len(current) >= 2:
                    out.append(current)
                current = []
                _append_point(current, a)
                _append_point(current, b)
            else:
                # 直前点から連続しているので末尾へ延長する。
                _append_point(current, b)

        if not _is_inside_rect(p1, rect):
            # 次の元点が紙外なら「ここで紙外へ出た」ことになるので flush する。
            # これにより export 側で「次の紙内復帰は必ずペンアップ移動」とできる。
            # （紙外を跨いで直線で繋ぐと、紙の外周をショートカットして事故りやすい）
            if current and len(current) >= 2:
                out.append(current)
            current = []

    if current and len(current) >= 2:
        out.append(current)

    return out


def _paper_safe_rect(
    canvas_size: tuple[float, float],
    *,
    paper_margin_mm: float,
) -> tuple[float, float, float, float]:
    """紙（canvas）の安全領域矩形 `[x_min, x_max] × [y_min, y_max]` を返す。"""

    # ここでの矩形は canvas 座標系（= ユーザーが描く座標系）で定義する。
    # y_down/origin などの機械座標変換は「クリップ後」に適用する。
    w, h = canvas_size
    m = float(paper_margin_mm)
    if m < 0:
        raise ValueError("paper_margin_mm は 0 以上である必要がある")
    if m * 2 >= w or m * 2 >= h:
        # 余白が大きすぎると安全領域が空になるため、クリップが常に消える。
        raise ValueError("paper_margin_mm が大きすぎます（安全領域が空になります）")
    return (m, w - m, m, h - m)


def _canvas_to_machine_xy(
    xy: tuple[float, float],
    *,
    params: GCodeParams,
    canvas_size: tuple[float, float],
) -> tuple[float, float]:
    """canvas 座標（紙座標）を machine 座標へ変換して返す。

    Notes
    -----
    変換順序は「Y 反転 → origin 加算」。
    """

    # 注意: 距離（mm）は Y 反転や origin 平行移動では変わらない（等長変換）。
    # そのため「距離評価（最適化）」は canvas 座標系のままでも成立する。
    x, y = xy
    if params.y_down:
        # y_down=True の場合:
        # canvas は `(0,0)` が左上・Y が下向き、の感覚で描けるようにしつつ、
        # 出力は `y -> (H - y)` で反転して機械座標へ合わせる。
        canvas_h = (
            float(params.canvas_height_mm)
            if params.canvas_height_mm is not None
            else float(canvas_size[1])
        )
        y = canvas_h - y

    # origin は「機械原点との差」を吸収するためのオフセット。
    ox, oy = params.origin
    return x + float(ox), y + float(oy)


def _quantize_xy(xy: tuple[float, float], *, decimals: int) -> tuple[float, float]:
    """出力用に XY を丸めて返す。"""

    # G-code の範囲検証は「実際に出力する値」で行いたいので、
    # 丸めは文字列化より先に行い、以降は丸め後座標を正とする。
    # ここで丸めておくと `move_xy()` が「同一点なら出力しない」判定も安定する。
    x, y = xy
    return (
        float(round(float(x), int(decimals))),
        float(round(float(y), int(decimals))),
    )


def _validate_bed_xy(
    xy: tuple[float, float],
    *,
    bed_x_range: tuple[float, float] | None,
    bed_y_range: tuple[float, float] | None,
) -> None:
    """ベッド範囲（3D プリンタの安全領域）を検証する。

    Notes
    -----
    この検証は「入力の頂点が範囲外かどうか」ではなく、
    **実際に出力する `G1 X.. Y..` の移動先**が範囲外かどうかだけを確認する。
    （紙クリップ後の出力が安全なら、入力が大きくても許容する）
    """

    # 範囲検証は危険側（機械破損）に倒すので、範囲外は即例外とする。
    # 一方で、検証対象は「紙クリップ + 変換 + 丸め後の出力点」に限定する。
    if bed_x_range is None and bed_y_range is None:
        return

    x, y = xy
    if bed_x_range is not None:
        x_min, x_max = bed_x_range
        if not (float(x_min) <= x <= float(x_max)):
            raise ValueError("G-code 出力が bed_x_range の範囲外です")
    if bed_y_range is not None:
        y_min, y_max = bed_y_range
        if not (float(y_min) <= y <= float(y_max)):
            raise ValueError("G-code 出力が bed_y_range の範囲外です")


@dataclass(frozen=True, slots=True)
class _Stroke:
    """レイヤ内の 1 ストローク（紙内に残る連続区間）を表す。

    Notes
    -----
    points_canvas:
        canvas 座標系の点列（この順に辿ると「描画線」になる）。
    start_q / end_q:
        距離比較のための量子化座標（整数）。
        浮動小数の微小差で順序が揺れないよう、一定の分解能で丸めた値を使う。
    """

    poly_idx: int
    seg_idx: int
    points_canvas: list[tuple[float, float]]
    start_q: tuple[int, int]
    end_q: tuple[int, int]


def _order_strokes_in_layer(
    strokes: list[_Stroke],
    *,
    allow_reverse: bool,
) -> list[tuple[_Stroke, bool]]:
    """レイヤ内のストローク順を決めて返す。

    Notes
    -----
    - 先頭ストロークは入力順の先頭固定（反転もしない）。
    - 以降は貪欲法で、直前ストローク終点からのペンアップ距離が最短となる次ストロークを選ぶ。
    - 距離が同じ場合は `(poly_idx, seg_idx)` の昇順で安定化し、反転同距離なら元向きを優先する。
    """

    # 入力順そのものにも意味がある場合があるため、先頭 stroke は固定する。
    # （例: レイヤの “最初の一筆” を作者が意図的に決めているケース）
    if not strokes:
        return []
    if len(strokes) == 1:
        return [(strokes[0], False)]

    ordered: list[tuple[_Stroke, bool]] = [(strokes[0], False)]
    current_end_q = strokes[0].end_q
    endpoints = _StrokeEndpointGrid(strokes, allow_reverse=allow_reverse)
    endpoints.remove(0)

    while len(ordered) < len(strokes):
        stroke_index, reverse = endpoints.nearest(current_end_q)
        endpoints.remove(stroke_index)
        chosen = strokes[stroke_index]
        ordered.append((chosen, reverse))
        current_end_q = chosen.start_q if reverse else chosen.end_q

    return ordered


class _StrokeEndpointGrid:
    """削除可能な stroke endpoint に対する正確な最近傍 index。"""

    def __init__(self, strokes: Sequence[_Stroke], *, allow_reverse: bool) -> None:
        self._strokes = strokes
        self._allow_reverse = bool(allow_reverse)
        points = [stroke.start_q for stroke in strokes]
        if self._allow_reverse:
            points.extend(stroke.end_q for stroke in strokes)

        xs = [int(point[0]) for point in points]
        ys = [int(point[1]) for point in points]
        self._origin_x = min(xs)
        self._origin_y = min(ys)
        max_span = max(max(xs) - self._origin_x + 1, max(ys) - self._origin_y + 1)
        target_cells_per_axis = isqrt(max(0, len(points) - 1)) + 1
        self._cell_size = max(
            1,
            (max_span + target_cells_per_axis - 1) // target_cells_per_axis,
        )

        self._cells: dict[tuple[int, int], set[int]] = {}
        self._point_cells: dict[int, tuple[int, int]] = {}
        for stroke_index, stroke in enumerate(strokes):
            self._add_endpoint(stroke_index, False, stroke.start_q)
            if self._allow_reverse:
                self._add_endpoint(stroke_index, True, stroke.end_q)

        cell_keys = tuple(self._cells)
        self._min_cell_x = min(cell[0] for cell in cell_keys)
        self._max_cell_x = max(cell[0] for cell in cell_keys)
        self._min_cell_y = min(cell[1] for cell in cell_keys)
        self._max_cell_y = max(cell[1] for cell in cell_keys)

    def _cell_for(self, point: tuple[int, int]) -> tuple[int, int]:
        return (
            (int(point[0]) - self._origin_x) // self._cell_size,
            (int(point[1]) - self._origin_y) // self._cell_size,
        )

    def _add_endpoint(
        self,
        stroke_index: int,
        reverse: bool,
        point: tuple[int, int],
    ) -> None:
        endpoint_id = 2 * int(stroke_index) + int(reverse)
        cell = self._cell_for(point)
        self._cells.setdefault(cell, set()).add(endpoint_id)
        self._point_cells[endpoint_id] = cell

    def remove(self, stroke_index: int) -> None:
        """stroke の両 endpoint を index から削除する。"""

        endpoint_ids = [2 * int(stroke_index)]
        if self._allow_reverse:
            endpoint_ids.append(2 * int(stroke_index) + 1)
        for endpoint_id in endpoint_ids:
            cell = self._point_cells.pop(endpoint_id)
            endpoints = self._cells[cell]
            endpoints.remove(endpoint_id)
            if not endpoints:
                del self._cells[cell]

    def nearest(self, point: tuple[int, int]) -> tuple[int, bool]:
        """距離と既存 tie-break が最小の active endpoint を返す。"""

        query_cell_x, query_cell_y = self._cell_for(point)
        max_radius = max(
            abs(query_cell_x - self._min_cell_x),
            abs(query_cell_x - self._max_cell_x),
            abs(query_cell_y - self._min_cell_y),
            abs(query_cell_y - self._max_cell_y),
        )
        best_key: tuple[int, int, int, int, int] | None = None
        best_result: tuple[int, bool] | None = None

        for radius in range(max_radius + 1):
            for cell in _iter_cell_ring(query_cell_x, query_cell_y, radius):
                for endpoint_id in self._cells.get(cell, ()):
                    stroke_index, reverse_i = divmod(endpoint_id, 2)
                    stroke = self._strokes[stroke_index]
                    endpoint = stroke.end_q if reverse_i else stroke.start_q
                    dx = int(endpoint[0]) - int(point[0])
                    dy = int(endpoint[1]) - int(point[1])
                    key = (
                        dx * dx + dy * dy,
                        int(stroke.poly_idx),
                        int(stroke.seg_idx),
                        int(reverse_i),
                        int(stroke_index),
                    )
                    if best_key is None or key < best_key:
                        best_key = key
                        best_result = (stroke_index, bool(reverse_i))

            if best_key is not None:
                outside_distance = self._distance_to_unsearched_cells(
                    point=point,
                    query_cell=(query_cell_x, query_cell_y),
                    radius=radius,
                )
                if outside_distance is None or outside_distance * outside_distance > best_key[0]:
                    assert best_result is not None
                    return best_result

        assert best_result is not None
        return best_result

    def _distance_to_unsearched_cells(
        self,
        *,
        point: tuple[int, int],
        query_cell: tuple[int, int],
        radius: int,
    ) -> int | None:
        """未探索領域までの距離の下界を返す。全 cell 探索済みなら None。"""

        qx, qy = query_cell
        x, y = int(point[0]), int(point[1])
        distances: list[int] = []
        if qx - radius > self._min_cell_x:
            left = self._origin_x + (qx - radius) * self._cell_size
            distances.append(x - left)
        if qx + radius < self._max_cell_x:
            right = self._origin_x + (qx + radius + 1) * self._cell_size
            distances.append(right - x)
        if qy - radius > self._min_cell_y:
            bottom = self._origin_y + (qy - radius) * self._cell_size
            distances.append(y - bottom)
        if qy + radius < self._max_cell_y:
            top = self._origin_y + (qy + radius + 1) * self._cell_size
            distances.append(top - y)
        return min(distances) if distances else None


def _iter_cell_ring(
    center_x: int,
    center_y: int,
    radius: int,
) -> Iterable[tuple[int, int]]:
    """Chebyshev 距離 ``radius`` の cell だけを重複なく列挙する。"""

    if radius == 0:
        yield center_x, center_y
        return

    low_x = center_x - radius
    high_x = center_x + radius
    low_y = center_y - radius
    high_y = center_y + radius
    for x in range(low_x, high_x + 1):
        yield x, low_y
        yield x, high_y
    for y in range(low_y + 1, high_y):
        yield low_x, y
        yield high_x, y


_GCODE_HEADER = (
    "; ====== Header ======",
    "G21 ; Set units to millimeters",
    "G90 ; Absolute positioning",
    "G28 ; Home all axes",
    "M107 ; Turn off fan",
    "M420 S1 Z10; Enable bed leveling matrix",
    "; ====== Body ======",
)


def _validated_canvas(canvas_size: tuple[float, float]) -> tuple[float, float]:
    """正の有限 canvas size を float tuple として返す。"""

    canvas = (float(canvas_size[0]), float(canvas_size[1]))
    if not np.isfinite(canvas).all() or canvas[0] <= 0.0 or canvas[1] <= 0.0:
        raise ValueError("canvas_size は正の有限な (width, height) である必要がある")
    return canvas


def _collect_strokes_in_layer(
    layer: RealizedLayer,
    *,
    safe_rect: tuple[float, float, float, float],
    scale: int,
) -> list[_Stroke]:
    """1 layer を clip し、全 stroke を入力順の flat list で返す。"""

    coords = np.asarray(layer.realized.coords, dtype=np.float64)
    offsets = np.asarray(layer.realized.offsets, dtype=np.int32)
    strokes: list[_Stroke] = []

    for poly_idx, (start, end) in enumerate(zip(offsets[:-1], offsets[1:])):
        start_i, end_i = int(start), int(end)
        if end_i - start_i < 2:
            continue
        polyline = np.ascontiguousarray(coords[start_i:end_i, :2], dtype=np.float64)
        for seg_idx, points in enumerate(_clip_polyline_to_rect(polyline, safe_rect)):
            if len(points) < 2:
                continue
            start_xy, end_xy = points[0], points[-1]
            strokes.append(
                _Stroke(
                    poly_idx=poly_idx,
                    seg_idx=seg_idx,
                    points_canvas=points,
                    start_q=(
                        int(round(float(start_xy[0]) * scale)),
                        int(round(float(start_xy[1]) * scale)),
                    ),
                    end_q=(
                        int(round(float(end_xy[0]) * scale)),
                        int(round(float(end_xy[1]) * scale)),
                    ),
                )
            )
    return strokes


class _GCodeEmitter:
    """座標検証と冗長命令抑制を所有する G-code dialect emitter。"""

    def __init__(
        self,
        *,
        stream: TextIO,
        params: GCodeParams,
        canvas: tuple[float, float],
    ) -> None:
        self._stream = stream
        self.params = params
        self.canvas = canvas
        self.decimals = int(params.decimals)
        self._pen_is_down = True
        self._current_feed: int | None = None
        self._current_xy: tuple[float, float] | None = None
        for line in _GCODE_HEADER:
            self.write_line(line)

    def write_line(self, line: str) -> None:
        """G-code/comment 1行を逐次出力する。"""

        self._stream.write(line)
        self._stream.write("\n")

    def set_pen_down(self, down: bool) -> None:
        """必要な場合だけ pen Z command を追加する。"""

        if bool(down) == self._pen_is_down:
            return
        self._pen_is_down = bool(down)
        z = float(self.params.z_down if down else self.params.z_up)
        self.write_line(
            f"G1 Z{_fmt_float(round(z, self.decimals), decimals=self.decimals)}"
        )

    def set_feed(self, feed: int) -> None:
        """必要な場合だけ feed command を追加する。"""

        if self._current_feed == int(feed):
            return
        self._current_feed = int(feed)
        self.write_line(f"G1 F{int(feed)}")

    def move_xy(self, point: tuple[float, float]) -> None:
        """canvas point を安全検証し、重複しない XY command として追加する。"""

        machine = _canvas_to_machine_xy(
            point,
            params=self.params,
            canvas_size=self.canvas,
        )
        quantized = _quantize_xy(machine, decimals=self.decimals)
        _validate_bed_xy(
            quantized,
            bed_x_range=self.params.bed_x_range,
            bed_y_range=self.params.bed_y_range,
        )
        if self._current_xy is not None and hypot(
            quantized[0] - self._current_xy[0],
            quantized[1] - self._current_xy[1],
        ) < 1e-12:
            return
        self._current_xy = quantized
        x_text = _fmt_float(quantized[0], decimals=self.decimals)
        y_text = _fmt_float(quantized[1], decimals=self.decimals)
        self.write_line(f"G1 X{x_text} Y{y_text}")

    def finish(self) -> None:
        """安全な最終 Z command を追加する。"""

        final_z = round(float(self.params.z_up + 20), self.decimals)
        self.write_line("; ====== Footer ======")
        self.write_line(f"G1 Z{_fmt_float(final_z, decimals=self.decimals)}")


def _emit_layer_strokes(
    emitter: _GCodeEmitter,
    ordered: Sequence[tuple[_Stroke, bool]],
    *,
    bridge_draw_distance: float | None,
    scale: int,
    travel_feed: int,
    draw_feed: int,
) -> None:
    """順序確定済みの 1 layer の stroke 列を emitter へ送る。"""

    current_end_q: tuple[int, int] | None = None
    for stroke, reversed_ in ordered:
        points = stroke.points_canvas
        if len(points) < 2:
            continue
        emitter.write_line(
            f"; stroke polyline {stroke.poly_idx} seg {stroke.seg_idx}"
            f"{' reversed' if reversed_ else ''}"
        )
        if reversed_:
            start_xy = points[-1]
            rest: Iterable[tuple[float, float]] = reversed(points[:-1])
            start_q, end_q = stroke.end_q, stroke.start_q
        else:
            start_xy = points[0]
            rest = points[1:]
            start_q, end_q = stroke.start_q, stroke.end_q

        draw_bridge = False
        if bridge_draw_distance is not None and current_end_q is not None:
            dx = int(start_q[0]) - int(current_end_q[0])
            dy = int(start_q[1]) - int(current_end_q[1])
            threshold = float(bridge_draw_distance) * float(scale)
            draw_bridge = float(dx * dx + dy * dy) < threshold * threshold

        if draw_bridge:
            emitter.set_pen_down(True)
            emitter.set_feed(draw_feed)
        else:
            emitter.set_pen_down(False)
            emitter.set_feed(travel_feed)
            emitter.move_xy(start_xy)
            emitter.set_pen_down(True)
            emitter.set_feed(draw_feed)
        emitter.move_xy(start_xy)
        for point in rest:
            emitter.move_xy(point)
        current_end_q = end_q


def export_gcode(
    layers: Sequence[RealizedLayer],
    path: str | Path,
    *,
    canvas_size: tuple[float, float],
    params: GCodeParams,
) -> Path:
    """Layer 列を決定的な G-code として保存する。

    Parameters
    ----------
    layers : Sequence[RealizedLayer]
        realize 済みの Layer 列。
    path : str or Path
        出力先パス。
    canvas_size : tuple[float, float]
        紙サイズ（mm）として扱うキャンバス寸法 ``(width, height)``。
    params : GCodeParams
        session/config 境界で確定した出力パラメータ。

    Returns
    -------
    Path
        保存先パス。

    Notes
    -----
    clip後のstrokeはLayerごとにflat化し、source polyline境界を越えて並べ替え、
    反転、短距離bridgeを適用する。``Layer.gcode_optimize=False``の場合は、そのLayerで
    3処理をすべて無効にする。いずれの処理もLayer境界は越えない。
    """

    destination = Path(path)
    canvas = _validated_canvas(canvas_size)
    safe_rect = _paper_safe_rect(
        canvas,
        paper_margin_mm=float(params.paper_margin_mm),
    )
    bridge_distance = params.bridge_draw_distance
    if bridge_distance is not None and float(bridge_distance) < 0.0:
        raise ValueError("bridge_draw_distance は 0 以上である必要がある")

    scale = 10 ** int(params.decimals)
    travel_feed = int(round(float(params.travel_feed)))
    draw_feed = int(round(float(params.draw_feed)))
    with atomic_text_writer(destination, newline="\n") as stream:
        emitter = _GCodeEmitter(stream=stream, params=params, canvas=canvas)

        for layer_index, layer in enumerate(layers):
            emitter.write_line(f"; layer {layer_index} start")
            layer_optimization_enabled = layer.layer.gcode_optimize
            optimize_travel = layer_optimization_enabled and params.optimize_travel
            allow_reverse = optimize_travel and params.allow_reverse
            effective_bridge_distance = (
                bridge_distance if layer_optimization_enabled else None
            )

            strokes = _collect_strokes_in_layer(
                layer,
                safe_rect=safe_rect,
                scale=scale,
            )
            ordered = (
                _order_strokes_in_layer(strokes, allow_reverse=allow_reverse)
                if optimize_travel
                else [(stroke, False) for stroke in strokes]
            )
            _emit_layer_strokes(
                emitter,
                ordered,
                bridge_draw_distance=effective_bridge_distance,
                scale=scale,
                travel_feed=travel_feed,
                draw_feed=draw_feed,
            )
            emitter.write_line(f"; layer {layer_index} end")

        emitter.finish()
    return destination


__all__ = ["GCodeParams", "export_gcode"]
