"""
Purpose:
    font outlineの曲線contourを、text geometryが扱う直線segment列へ変換する。
Use when:
    glyph曲線のsampling品質、輪郭closure、またはfontTools pen境界を変更する場合。
Constraints:
    - move・end・closeによるcontour境界を保ち、closed contourの終点を始点へ戻す。
    - quadraticとcubicの終点をexactに残し、同じ品質指定から決定的なsegment列を作る。
    - font-spaceの近似segment長という契約をtext側のquality変換と一致させる。
"""

from __future__ import annotations

import math
from typing import Any, cast

from fontTools.misc.bezierTools import (  # type: ignore[import-untyped]
    calcQuadraticArcLength,
    quadraticPointAtT,
)
from fontTools.pens.basePen import BasePen  # type: ignore[import-untyped]
from fontTools.pens.recordingPen import RecordingPen  # type: ignore[import-untyped]

_Point = tuple[float, float]
_CUBIC_LENGTH_SAMPLES = 10


def _distance(left: _Point, right: _Point) -> float:
    return math.sqrt(
        (left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2
    )


def _interpolate(left: _Point, right: _Point, factor: float) -> _Point:
    return (
        left[0] + (right[0] - left[0]) * factor,
        left[1] + (right[1] - left[1]) * factor,
    )


def _midpoint(left: _Point, right: _Point) -> _Point:
    return (
        0.5 * (left[0] + right[0]),
        0.5 * (left[1] + right[1]),
    )


def _cubic_polynomial_point(
    factor: float,
    start: _Point,
    control_1: _Point,
    control_2: _Point,
    end: _Point,
) -> _Point:
    cx = (control_1[0] - start[0]) * 3.0
    cy = (control_1[1] - start[1]) * 3.0
    bx = (control_2[0] - control_1[0]) * 3.0 - cx
    by = (control_2[1] - control_1[1]) * 3.0 - cy
    ax = end[0] - start[0] - cx - bx
    ay = end[1] - start[1] - cy - by
    factor_2 = factor * factor
    factor_3 = factor**3
    return (
        ax * factor_3 + bx * factor_2 + cx * factor + start[0],
        ay * factor_3 + by * factor_2 + cy * factor + start[1],
    )


def _cubic_point(
    factor: float,
    start: _Point,
    control_1: _Point,
    control_2: _Point,
    end: _Point,
) -> _Point:
    if factor == 1.0:
        return end
    if factor == 0.5:
        left = _midpoint(start, control_1)
        middle = _midpoint(control_1, control_2)
        right = _midpoint(control_2, end)
        return _midpoint(_midpoint(left, middle), _midpoint(middle, right))
    return _cubic_polynomial_point(
        factor,
        start,
        control_1,
        control_2,
        end,
    )


def _estimate_cubic_length(
    start: _Point,
    control_1: _Point,
    control_2: _Point,
    end: _Point,
) -> float:
    length = 0.0
    previous = start
    step = 1.0 / _CUBIC_LENGTH_SAMPLES
    for index in range(1, _CUBIC_LENGTH_SAMPLES + 1):
        point = _cubic_polynomial_point(
            index * step,
            start,
            control_1,
            control_2,
            end,
        )
        length += _distance(previous, point)
        previous = point
    return length


class _AdaptiveFlattenPen(BasePen):
    """概算弧長に応じた数の線分を出力 pen へ渡す。"""

    def __init__(self, output_pen: Any, *, segment_length: float) -> None:
        super().__init__()
        self._output_pen = output_pen
        self._segment_length = float(segment_length)
        self._current_point: _Point | None = None
        self._first_point: _Point | None = None

    def _steps(self, length: float) -> int:
        return max(1, int(round(float(length) / self._segment_length)))

    def _moveTo(self, point: _Point) -> None:
        self._output_pen.moveTo(point)
        self._current_point = point
        self._first_point = point

    def _lineTo(self, point: _Point) -> None:
        current = cast(_Point, self._current_point)
        if point == current:
            return
        steps = self._steps(_distance(current, point))
        step = 1.0 / steps
        for index in range(1, steps + 1):
            self._output_pen.lineTo(
                _interpolate(current, point, index * step)
            )
        self._current_point = point

    def _curveToOne(
        self,
        control_1: _Point,
        control_2: _Point,
        end: _Point,
    ) -> None:
        start = cast(_Point, self._current_point)
        if control_1 == start and control_2 == end:
            self._lineTo(end)
            return
        steps = self._steps(
            _estimate_cubic_length(start, control_1, control_2, end)
        )
        step = 1.0 / steps
        for index in range(1, steps + 1):
            self._output_pen.lineTo(
                _cubic_point(
                    index * step,
                    start,
                    control_1,
                    control_2,
                    end,
                )
            )
        self._current_point = end

    def _qCurveToOne(self, control: _Point, end: _Point) -> None:
        start = cast(_Point, self._current_point)
        if control == start or control == end:
            self._lineTo(end)
            return
        steps = self._steps(calcQuadraticArcLength(start, control, end))
        step = 1.0 / steps
        for index in range(1, steps + 1):
            self._output_pen.lineTo(
                quadraticPointAtT(start, control, end, index * step)
            )
        self._current_point = end

    def _closePath(self) -> None:
        self.lineTo(cast(_Point, self._first_point))
        self._output_pen.closePath()
        self._current_point = None
        self._first_point = None

    def _endPath(self) -> None:
        self._output_pen.endPath()
        self._current_point = None
        self._first_point = None


def flatten_recording(
    recording: Any,
    *,
    approximate_segment_length: float,
) -> tuple:
    """recording pen の輪郭を move/line/close コマンドへ平坦化する。"""

    output = RecordingPen()
    recording.replay(
        _AdaptiveFlattenPen(
            output,
            segment_length=float(approximate_segment_length),
        )
    )
    return tuple(output.value)
