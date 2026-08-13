"""
Purpose:
    入力anchorを通る単一のcentripetal Catmull–Rom曲線をopenまたはclosedで生成する。
Use when:
    arbitrary point列の補間、端点接線、tension、またはcurve samplingを変更する場合。
Constraints:
    - anchor順を保ち、連続重複だけを除いて各anchorを補間結果へexactに残す。
    - open曲線は両端を保持し、closed曲線は循環接線と末尾のexact closureを維持する。
    - finite float32座標と出力頂点数を確保前に検査する。
"""

from __future__ import annotations

import math
import numpy as np

from grafix.core.parameters.meta import ParamMeta
from grafix.core.operation_authoring import primitive
from grafix.core.realized_geometry import GeomTuple
from grafix.core.resource_budget import ensure_geometry_output

from ._shape_utils import point3

_FLOAT32_MAX = float(np.finfo(np.float32).max)

spline_meta = {
    "closed": ParamMeta(
        kind="bool",
        description="最後のanchorから最初のanchorまでを補間し、曲線を閉じます。",
    ),
    "tension": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=1.0,
        description="0を標準形、1をanchor間の直線形とする接線の張力を指定します。",
    ),
    "segments_per_span": ParamMeta(
        kind="int",
        ui_min=1,
        ui_max=512,
        description="隣り合うanchor間を近似する直線セグメントの数を指定します。",
    ),
}


def _segment_count(value: int) -> int:
    """spanごとのsegment数を検証する。"""

    if value < 1:
        raise ValueError(
            "spline の segments_per_span は 1 以上である必要がある"
        )
    return value


def _normalize_anchors(
    points: tuple[tuple[float, ...], ...],
) -> list[tuple[float, float, float]]:
    """入力順を保ったまま、連続する同一点を1点へまとめる。"""

    anchors: list[tuple[float, float, float]] = []
    for value in points:
        point = point3(value, op="spline", name="points")
        if not all(
            math.isfinite(component) and abs(component) <= _FLOAT32_MAX
            for component in point
        ):
            raise ValueError(
                "spline の points はfloat32範囲内の有限な座標である必要がある"
            )
        if not anchors or point != anchors[-1]:
            anchors.append(point)
    return anchors


def _validate_samples(samples: np.ndarray) -> None:
    """float32へwarningなしで格納できる補間結果かを検査する。"""

    if not bool(np.all(np.isfinite(samples))) or bool(
        np.any(np.abs(samples) > _FLOAT32_MAX)
    ):
        raise ValueError(
            "spline の補間結果はfloat32範囲内の有限な座標である必要がある"
        )


def _centripetal_delta(first: np.ndarray, second: np.ndarray) -> float:
    """隣接anchor間のcentripetal knot差を返す。"""

    delta = second - first
    chord_length = math.hypot(
        float(delta[0]),
        float(delta[1]),
        float(delta[2]),
    )
    knot_delta = math.sqrt(chord_length)
    if not math.isfinite(knot_delta) or knot_delta <= 0.0:
        raise ValueError(
            "spline の異なる隣接点には有限で正の距離が必要です"
        )
    return knot_delta


def _span_tangents(
    previous: np.ndarray | None,
    start: np.ndarray,
    end: np.ndarray,
    following: np.ndarray | None,
    *,
    tension_scale: float,
) -> tuple[np.ndarray, np.ndarray]:
    """1 spanの非一様Catmull–Rom接線をnormalized parameterで返す。"""

    delta = _centripetal_delta(start, end)

    if previous is None:
        tangent_start = end - start
    else:
        delta_previous = _centripetal_delta(previous, start)
        tangent_start = delta * (
            (start - previous) / delta_previous
            - (end - previous) / (delta_previous + delta)
            + (end - start) / delta
        )

    if following is None:
        tangent_end = end - start
    else:
        delta_following = _centripetal_delta(end, following)
        tangent_end = delta * (
            (end - start) / delta
            - (following - start) / (delta + delta_following)
            + (following - end) / delta_following
        )

    if tension_scale != 1.0:
        tangent_start *= tension_scale
        tangent_end *= tension_scale
    return tangent_start, tangent_end


def _hermite_basis(
    segments_per_span: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """終点を除く等間隔parameterに対するcubic Hermite基底を返す。"""

    parameter = np.arange(segments_per_span, dtype=np.float64)
    parameter /= float(segments_per_span)
    squared = parameter * parameter
    cubed = squared * parameter
    return (
        2.0 * cubed - 3.0 * squared + 1.0,
        cubed - 2.0 * squared + parameter,
        -2.0 * cubed + 3.0 * squared,
        cubed - squared,
    )


def _sample_two_anchors(
    coords: np.ndarray,
    anchors: np.ndarray,
    *,
    closed: bool,
    segments_per_span: int,
) -> None:
    """2 anchorを直線補間し、closedでは同じ線分を逆向きに戻る。"""

    parameter = np.arange(segments_per_span, dtype=np.float64)
    parameter /= float(segments_per_span)
    parameter = parameter[:, None]
    span_count = 2 if closed else 1
    for span_index in range(span_count):
        start = anchors[span_index % 2]
        end = anchors[(span_index + 1) % 2]
        begin = span_index * segments_per_span
        finish = begin + segments_per_span
        curve = start + parameter * (end - start)
        _validate_samples(curve)
        coords[begin:finish] = curve
        coords[begin] = start
    coords[-1] = coords[0] if closed else anchors[-1]


def _sample_spline(
    coords: np.ndarray,
    anchors: np.ndarray,
    *,
    closed: bool,
    tension_scale: float,
    segments_per_span: int,
) -> None:
    """3点以上のanchorをCatmull–Rom span順にsamplingする。"""

    h00, h10, h01, h11 = _hermite_basis(segments_per_span)
    anchor_count = int(anchors.shape[0])
    span_count = anchor_count if closed else anchor_count - 1

    for span_index in range(span_count):
        start_index = span_index
        end_index = (span_index + 1) % anchor_count
        previous = (
            anchors[(start_index - 1) % anchor_count]
            if closed or start_index > 0
            else None
        )
        following = (
            anchors[(end_index + 1) % anchor_count]
            if closed or end_index + 1 < anchor_count
            else None
        )
        start = anchors[start_index]
        end = anchors[end_index]
        tangent_start, tangent_end = _span_tangents(
            previous,
            start,
            end,
            following,
            tension_scale=tension_scale,
        )

        begin = span_index * segments_per_span
        finish = begin + segments_per_span
        curve = h00[:, None] * start
        curve += h10[:, None] * tangent_start
        curve += h01[:, None] * end
        curve += h11[:, None] * tangent_end
        _validate_samples(curve)
        coords[begin:finish] = curve
        # 共有anchorは次spanの先頭として1度だけ格納し、補間誤差を残さない。
        coords[begin] = start

    coords[-1] = coords[0] if closed else anchors[-1]


@primitive(meta=spline_meta)
def spline(
    *,
    points: tuple[tuple[float, ...], ...] = (
        (-0.5, 0.0),
        (-0.2, 0.3),
        (0.2, -0.3),
        (0.5, 0.0),
    ),
    closed: bool = False,
    tension: float = 0.0,
    segments_per_span: int = 16,
) -> GeomTuple:
    """anchor点列を通るcentripetal Catmull–Rom曲線を生成する。

    ``points`` はcode-owned引数で、Parameter GUIには表示しない。2次元点の
    Z座標は0へ正規化し、入力順を維持する。連続する同一点は補間前に1点へ
    まとめ、``closed=True`` のときは末尾に渡された始点の重複も取り除く。

    0点は空Geometry、1点および全点一致は1頂点のpolylineを返す。2点は
    直線補間し、closedの場合は2 spanで終点から始点へ同じ線分を戻る。
    3点以上ではcentripetal Catmull–Rom補間を用いる。各spanの共有anchorは
    重複させず、closed出力の末尾だけは先頭座標を厳密にコピーする。
    open曲線の両端接線には隣接anchorへの片側chordを使い、closed曲線では
    anchor列を循環させた前後neighborから接線を求める。

    Parameters
    ----------
    points : tuple[tuple[float, ...], ...], optional
        入力順に並べたfloat32範囲内の有限な2次元または3次元anchor座標。
    closed : bool, optional
        最後のanchorから最初のanchorまでを補間して曲線を閉じるか。
    tension : float, optional
        接線を縮める0以上1以下の張力。0が標準形、1がanchor間の直線形。
    segments_per_span : int, optional
        隣接anchor間1 spanを近似する線分数。1以上。

    Returns
    -------
    GeomTuple
        入力anchor順の単一polylineを表す座標配列とオフセット配列。

    Raises
    ------
    TypeError
        points または各 point の型・成分数が不正な場合。
    ValueError
        point の有限性・float32範囲、tension、segments_per_span、または補間結果の
        数値範囲が不正な場合。
    """

    count = _segment_count(segments_per_span)
    if not 0.0 <= tension <= 1.0:
        raise ValueError("spline の tension は有限な 0 以上 1 以下である必要がある")

    anchors = _normalize_anchors(points)
    if closed and len(anchors) > 1 and anchors[-1] == anchors[0]:
        anchors.pop()

    anchor_count = len(anchors)
    if anchor_count == 0:
        ensure_geometry_output("spline", vertices=0, lines=0)
        return np.empty((0, 3), dtype=np.float32), np.zeros(1, dtype=np.int32)

    if anchor_count == 1:
        ensure_geometry_output("spline", vertices=1, lines=1)
        with np.errstate(over="ignore", invalid="ignore", under="ignore"):
            coords = np.asarray(anchors, dtype=np.float32)
        return coords, np.array([0, 1], dtype=np.int32)

    span_count = anchor_count if closed else anchor_count - 1
    vertex_count = span_count * count + 1
    ensure_geometry_output(
        "spline",
        vertices=vertex_count,
        lines=1,
        # float64 anchor、Hermite基底、1 spanの演算用一時配列。
        scratch_bytes=anchor_count * 3 * 8 + count * 128,
        hint="points または segments_per_span を減らしてください",
    )

    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        anchor_array = np.asarray(anchors, dtype=np.float64)
        coords = np.empty((vertex_count, 3), dtype=np.float32)
        if anchor_count == 2:
            _sample_two_anchors(
                coords,
                anchor_array,
                closed=closed,
                segments_per_span=count,
            )
        else:
            _sample_spline(
                coords,
                anchor_array,
                closed=closed,
                tension_scale=1.0 - tension,
                segments_per_span=count,
            )
    offsets = np.array([0, vertex_count], dtype=np.int32)
    return coords, offsets


__all__ = ["spline", "spline_meta"]
