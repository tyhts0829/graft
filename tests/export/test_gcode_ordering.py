from __future__ import annotations

import numpy as np
import pytest

from grafix.export.gcode import _Stroke, _order_strokes_in_layer


def _reference_order(
    strokes: list[_Stroke],
    *,
    allow_reverse: bool,
) -> list[tuple[_Stroke, bool]]:
    if not strokes:
        return []
    ordered = [(strokes[0], False)]
    current = strokes[0].end_q
    remaining = list(strokes[1:])
    while remaining:
        best_position = 0
        best_reverse = False
        best_key: tuple[int, tuple[int, int], int] | None = None
        for position, stroke in enumerate(remaining):
            dx = int(stroke.start_q[0]) - int(current[0])
            dy = int(stroke.start_q[1]) - int(current[1])
            key = (dx * dx + dy * dy, (stroke.poly_idx, stroke.seg_idx), 0)
            if best_key is None or key < best_key:
                best_key = key
                best_position = position
                best_reverse = False
            if allow_reverse:
                dx = int(stroke.end_q[0]) - int(current[0])
                dy = int(stroke.end_q[1]) - int(current[1])
                reverse_key = (dx * dx + dy * dy, (stroke.poly_idx, stroke.seg_idx), 1)
                if best_key is None or reverse_key < best_key:
                    best_key = reverse_key
                    best_position = position
                    best_reverse = True
        chosen = remaining.pop(best_position)
        ordered.append((chosen, best_reverse))
        current = chosen.start_q if best_reverse else chosen.end_q
    return ordered


def _random_strokes(*, n: int, seed: int) -> list[_Stroke]:
    rng = np.random.default_rng(seed)
    endpoints = rng.integers(-1_000, 1_001, size=(n, 2, 2), dtype=np.int64)
    strokes = []
    for index in range(n):
        start = (int(endpoints[index, 0, 0]), int(endpoints[index, 0, 1]))
        end = (int(endpoints[index, 1, 0]), int(endpoints[index, 1, 1]))
        strokes.append(
            _Stroke(
                poly_idx=index // 3,
                seg_idx=index % 3,
                points_canvas=[(float(start[0]), float(start[1])), (float(end[0]), float(end[1]))],
                start_q=start,
                end_q=end,
            )
        )
    return strokes


@pytest.mark.parametrize("allow_reverse", [False, True])
@pytest.mark.parametrize("n", [0, 1, 2, 7, 31, 200])
def test_spatial_stroke_order_matches_reference(n: int, allow_reverse: bool) -> None:
    strokes = _random_strokes(n=n, seed=10_000 + n)

    expected = _reference_order(strokes, allow_reverse=allow_reverse)
    actual = _order_strokes_in_layer(strokes, allow_reverse=allow_reverse)

    expected_keys = [(strokes.index(stroke), reverse) for stroke, reverse in expected]
    actual_keys = [(strokes.index(stroke), reverse) for stroke, reverse in actual]
    assert actual_keys == expected_keys


@pytest.mark.parametrize("allow_reverse", [False, True])
def test_spatial_stroke_order_preserves_input_order_for_complete_ties(
    allow_reverse: bool,
) -> None:
    strokes = [
        _Stroke(
            poly_idx=0,
            seg_idx=0,
            points_canvas=[(0.0, 0.0), (0.0, 0.0)],
            start_q=(0, 0),
            end_q=(0, 0),
        )
        for _ in range(20)
    ]

    actual = _order_strokes_in_layer(strokes, allow_reverse=allow_reverse)

    assert [id(stroke) for stroke, _ in actual] == [id(stroke) for stroke in strokes]
    assert [reverse for _, reverse in actual] == [False] * len(strokes)


def test_stroke_order_uses_one_layer_wide_candidate_set_across_source_polylines() -> None:
    strokes = [
        _Stroke(
            poly_idx=7,
            seg_idx=0,
            points_canvas=[(0.0, 0.0), (0.0, 1.0)],
            start_q=(0, 0),
            end_q=(0, 1),
        ),
        _Stroke(
            poly_idx=9,
            seg_idx=0,
            points_canvas=[(100.0, 0.0), (100.0, 1.0)],
            start_q=(100, 0),
            end_q=(100, 1),
        ),
        _Stroke(
            poly_idx=8,
            seg_idx=0,
            points_canvas=[(1.0, 1.0), (1.0, 2.0)],
            start_q=(1, 1),
            end_q=(1, 2),
        ),
    ]

    ordered = _order_strokes_in_layer(strokes, allow_reverse=True)

    assert [(stroke.poly_idx, reverse) for stroke, reverse in ordered] == [
        (7, False),
        (8, False),
        (9, True),
    ]
    assert ordered[0][0] is strokes[0]
