"""G-code export（`grafix.export.gcode.export_gcode`）のテスト。"""

from __future__ import annotations

from math import hypot
from typing import Any

import numpy as np
import pytest

from grafix.core.evaluation_context import (
    EMPTY_EXTERNAL_DEPENDENCIES_FINGERPRINT,
    EvaluationFingerprint,
)
from grafix.core.gcode_params import GCodeParams as CoreGCodeParams
from grafix.core.geometry import Geometry
from grafix.core.layer import Layer
from grafix.core.pipeline import RealizedLayer
from grafix.core.realize import GeometryCacheKey
from grafix.core.realized_geometry import RealizedGeometry
from grafix.export.gcode import GCodeParams, export_gcode

_CALIBRATED_BOTTOM_RIGHT_MM = (302.019, 0.0)
_PREVIOUS_BOTTOM_RIGHT_MM = (302.019, 14.195)


def _realized_layer(
    *,
    coords: list[list[float]],
    offsets: list[int],
    gcode_optimize: bool | None = None,
) -> RealizedLayer:
    geometry = Geometry.create("gcode-test-geometry")
    layer = (
        Layer(geometry=geometry, site_id="layer:1")
        if gcode_optimize is None
        else Layer(
            geometry=geometry,
            site_id="layer:1",
            gcode_optimize=gcode_optimize,
        )
    )
    realized = RealizedGeometry(
        coords=np.asarray(coords, dtype=np.float32),
        offsets=np.asarray(offsets, dtype=np.int32),
    )
    return RealizedLayer(
        layer=layer,
        realized=realized,
        cache_key=GeometryCacheKey(
            geometry_id=geometry.id,
            evaluation=EvaluationFingerprint("0" * 64),
            external_dependencies=EMPTY_EXTERNAL_DEPENDENCIES_FINGERPRINT,
        ),
        color=(0.0, 0.0, 0.0),
        thickness=0.001,
    )


def _parse_xy(line: str) -> tuple[float, float] | None:
    if not line.startswith("G1 "):
        return None

    x: float | None = None
    y: float | None = None
    for tok in line.split():
        if tok.startswith("X"):
            x = float(tok[1:])
        elif tok.startswith("Y"):
            y = float(tok[1:])
    if x is None or y is None:
        return None
    return x, y


def _xy_moves(text: str) -> list[tuple[float, float]]:
    """G-code 中の XY 移動先を出力順で返す。"""

    return [xy for line in text.splitlines() if (xy := _parse_xy(line)) is not None]


def _pen_is_down_from_z(z: float, *, z_up: float, z_down: float) -> bool:
    return abs(float(z) - float(z_down)) <= abs(float(z) - float(z_up))


def _travel_distance(text: str, *, z_up: float, z_down: float) -> float:
    pen_is_down = True
    current_xy: tuple[float, float] | None = None
    travel = 0.0
    for line in text.splitlines():
        if line.startswith("G1 Z"):
            z_txt = line.split("Z", 1)[1].strip().split()[0]
            pen_is_down = _pen_is_down_from_z(float(z_txt), z_up=z_up, z_down=z_down)
            continue

        xy = _parse_xy(line)
        if xy is None:
            continue
        if current_xy is not None and not pen_is_down:
            travel += hypot(xy[0] - current_xy[0], xy[1] - current_xy[1])
        current_xy = xy
    return float(travel)


def test_gcode_params_is_the_public_reexport_of_the_core_type() -> None:
    assert GCodeParams is CoreGCodeParams


def test_gcode_params_uses_recalibrated_default_bottom_right() -> None:
    assert GCodeParams().paper_bottom_right_mm == _CALIBRATED_BOTTOM_RIGHT_MM


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("travel_feed", True),
        ("travel_feed", "3000"),
        ("draw_feed", float("nan")),
        ("z_up", float("inf")),
        ("z_down", object()),
        ("paper_margin_mm", False),
        ("bridge_draw_distance", "0.5"),
    ],
)
def test_gcode_params_rejects_non_real_or_non_finite_numeric_values(
    field: str,
    value: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        GCodeParams(**{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("travel_feed", 0.0),
        ("draw_feed", -1.0),
        ("paper_margin_mm", -0.1),
        ("bridge_draw_distance", -0.1),
    ],
)
def test_gcode_params_rejects_values_outside_semantic_ranges(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError):
        GCodeParams(**{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["y_down", "optimize_travel", "allow_reverse"])
@pytest.mark.parametrize("value", [0, 1, "", object()])
def test_gcode_params_requires_exact_booleans(
    field: str,
    value: Any,
) -> None:
    with pytest.raises(TypeError):
        GCodeParams(**{field: value})


@pytest.mark.parametrize("value", [True, 1.0, "3", -1])
def test_gcode_params_requires_non_negative_integer_decimals(value: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        GCodeParams(decimals=value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("paper_bottom_right_mm", [0.0, 0.0]),
        ("paper_bottom_right_mm", (0.0,)),
        ("paper_bottom_right_mm", (0.0, float("nan"))),
        ("bed_x_range", [0.0, 1.0]),
        ("bed_x_range", (1.0, 1.0)),
        ("bed_y_range", (2.0, 1.0)),
    ],
)
def test_gcode_params_requires_finite_ordered_tuples(
    field: str,
    value: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        GCodeParams(**{field: value})  # type: ignore[arg-type]


def test_export_gcode_a5_keeps_x_and_moves_y_14_195_mm_to_zero_anchor(
    tmp_path,
) -> None:
    """A5 の X は保ち、旧 anchor 比で Y だけ 14.195 mm 下げる。"""

    layer = _realized_layer(
        coords=[
            [0.0, 0.0, 0.0],
            [74.0, 105.0, 0.0],
            [148.0, 210.0, 0.0],
        ],
        offsets=[0, 3],
    )
    params = GCodeParams(
        paper_bottom_right_mm=_CALIBRATED_BOTTOM_RIGHT_MM,
        y_down=True,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=False,
        bridge_draw_distance=None,
    )
    out_path = tmp_path / "a5-anchor.gcode"

    export_gcode([layer], out_path, canvas_size=(148.0, 210.0), params=params)

    assert _xy_moves(out_path.read_text(encoding="utf-8")) == [
        (154.019, 210.0),
        (228.019, 105.0),
        (302.019, 0.0),
    ]


def test_export_gcode_a4_moves_left_for_width_and_uses_zero_y_anchor(
    tmp_path,
) -> None:
    layer = _realized_layer(
        coords=[[105.0, 148.5, 0.0], [210.0, 297.0, 0.0]],
        offsets=[0, 2],
    )
    params = GCodeParams(
        paper_bottom_right_mm=_CALIBRATED_BOTTOM_RIGHT_MM,
        y_down=True,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=False,
        bridge_draw_distance=None,
    )
    out_path = tmp_path / "a4-anchor.gcode"

    export_gcode([layer], out_path, canvas_size=(210.0, 297.0), params=params)

    moves = _xy_moves(out_path.read_text(encoding="utf-8"))
    assert moves == [(197.019, 148.5), (302.019, 0.0)]
    assert moves[0][0] == 105.0 + 154.019 - 62.0
    assert moves[0][1] == 162.695 - 14.195


def test_export_gcode_zero_y_anchor_shifts_every_y_by_14_195_and_preserves_x(
    tmp_path,
) -> None:
    layer = _realized_layer(
        coords=[
            [0.0, 0.0, 0.0],
            [61.5, 78.25, 0.0],
            [123.0, 234.0, 0.0],
        ],
        offsets=[0, 3],
    )
    common: dict[str, Any] = dict(
        y_down=True,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=False,
        bridge_draw_distance=None,
    )
    previous_path = tmp_path / "previous-y-anchor.gcode"
    recalibrated_path = tmp_path / "recalibrated-y-anchor.gcode"

    export_gcode(
        [layer],
        previous_path,
        canvas_size=(123.0, 234.0),
        params=GCodeParams(
            paper_bottom_right_mm=_PREVIOUS_BOTTOM_RIGHT_MM,
            **common,
        ),
    )
    export_gcode(
        [layer],
        recalibrated_path,
        canvas_size=(123.0, 234.0),
        params=GCodeParams(
            paper_bottom_right_mm=_CALIBRATED_BOTTOM_RIGHT_MM,
            **common,
        ),
    )

    previous = _xy_moves(previous_path.read_text(encoding="utf-8"))
    recalibrated = _xy_moves(recalibrated_path.read_text(encoding="utf-8"))
    assert recalibrated == [(x, round(y - 14.195, 3)) for x, y in previous]


@pytest.mark.parametrize(
    "canvas_size",
    [(100.0, 100.0), (148.0, 210.0), (210.0, 297.0), (297.0, 420.0)],
)
def test_export_gcode_keeps_bottom_right_at_anchor_for_arbitrary_paper_sizes(
    tmp_path,
    *,
    canvas_size: tuple[float, float],
) -> None:
    width, height = canvas_size
    layer = _realized_layer(
        coords=[[width, height, 0.0], [width - 1.0, height - 1.0, 0.0]],
        offsets=[0, 2],
    )
    params = GCodeParams(
        paper_bottom_right_mm=_CALIBRATED_BOTTOM_RIGHT_MM,
        y_down=True,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=False,
        bridge_draw_distance=None,
    )
    out_path = tmp_path / f"anchor-{int(width)}x{int(height)}.gcode"

    export_gcode([layer], out_path, canvas_size=canvas_size, params=params)

    assert _xy_moves(out_path.read_text(encoding="utf-8")) == [
        (302.019, 0.0),
        (301.019, 1.0),
    ]


@pytest.mark.parametrize(
    ("y_down", "expected"),
    [
        (
            True,
            [
                (260.0, 70.0),
                (300.0, 70.0),
                (300.0, 10.0),
                (260.0, 10.0),
                (260.0, 70.0),
            ],
        ),
        (
            False,
            [
                (260.0, -50.0),
                (300.0, -50.0),
                (300.0, 10.0),
                (260.0, 10.0),
                (260.0, -50.0),
            ],
        ),
    ],
)
def test_export_gcode_maps_all_corners_without_x_flip_and_with_configured_y_direction(
    tmp_path,
    *,
    y_down: bool,
    expected: list[tuple[float, float]],
) -> None:
    """canvas の TL→TR→BR→BL 順で X 非反転と Y 方向契約を固定する。"""

    layer = _realized_layer(
        coords=[
            [0.0, 0.0, 0.0],
            [40.0, 0.0, 0.0],
            [40.0, 60.0, 0.0],
            [0.0, 60.0, 0.0],
            [0.0, 0.0, 0.0],
        ],
        offsets=[0, 5],
    )
    params = GCodeParams(
        paper_bottom_right_mm=(300.0, 10.0),
        y_down=y_down,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=False,
        bridge_draw_distance=None,
    )
    out_path = tmp_path / f"corners-y-down-{y_down}.gcode"

    export_gcode([layer], out_path, canvas_size=(40.0, 60.0), params=params)

    assert _xy_moves(out_path.read_text(encoding="utf-8")) == expected


def test_export_gcode_writes_file_and_is_deterministic(tmp_path) -> None:
    layers = [
        _realized_layer(
            coords=[
                [1.0, 1.0, 0.0],
                [2.0, 1.0, 0.0],
                [10.0, 10.0, 0.0],
                [11.0, 10.0, 0.0],
                [3.0, 1.0, 0.0],
                [4.0, 1.0, 0.0],
            ],
            offsets=[0, 2, 4, 6],
        )
    ]
    params = GCodeParams(
        paper_bottom_right_mm=(20.0, 20.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=True,
        allow_reverse=True,
    )

    a = tmp_path / "a.gcode"
    b = tmp_path / "b.gcode"
    export_gcode(layers, a, canvas_size=(20.0, 20.0), params=params)
    export_gcode(layers, b, canvas_size=(20.0, 20.0), params=params)

    assert a.read_bytes() == b.read_bytes()


def test_export_gcode_clips_to_paper_and_uses_pen_up_for_outside(tmp_path) -> None:
    layers = [
        _realized_layer(
            coords=[
                [1.0, 1.0, 0.0],
                [12.0, 1.0, 0.0],
                [12.0, 9.0, 0.0],
                [1.0, 9.0, 0.0],
            ],
            offsets=[0, 4],
        )
    ]
    params = GCodeParams(
        paper_bottom_right_mm=(10.0, 10.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
    )

    out_path = tmp_path / "out.gcode"
    export_gcode(layers, out_path, canvas_size=(10.0, 10.0), params=params)
    text = out_path.read_text(encoding="utf-8")

    assert "X12.000" not in text
    assert "Y12.000" not in text

    lines = text.splitlines()

    i_draw_to_exit = lines.index("G1 X10.000 Y1.000")
    i_travel_to_entry = lines.index("G1 X10.000 Y9.000")
    i_draw_after_entry = lines.index("G1 X1.000 Y9.000")

    assert i_draw_to_exit < i_travel_to_entry < i_draw_after_entry
    assert "G1 Z3.000" in lines[i_draw_to_exit + 1 : i_travel_to_entry]
    assert (
        f"G1 Z{float(params.z_down):.3f}"
        in lines[i_travel_to_entry + 1 : i_draw_after_entry]
    )


def test_export_gcode_allows_input_outside_bed_if_output_is_inside(tmp_path) -> None:
    layers = [
        _realized_layer(
            coords=[[-100.0, 5.0, 0.0], [5.0, 5.0, 0.0]],
            offsets=[0, 2],
        )
    ]
    params = GCodeParams(
        paper_bottom_right_mm=(10.0, 10.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
        bed_x_range=(0.0, 10.0),
        bed_y_range=(0.0, 10.0),
    )

    out_path = tmp_path / "out.gcode"
    export_gcode(layers, out_path, canvas_size=(10.0, 10.0), params=params)
    assert out_path.exists()


def test_export_gcode_raises_if_output_outside_bed(tmp_path) -> None:
    layers = [
        _realized_layer(
            coords=[[9.5, 1.0, 0.0], [9.5, 2.0, 0.0]],
            offsets=[0, 2],
        )
    ]
    params = GCodeParams(
        paper_bottom_right_mm=(10.0, 10.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
        bed_x_range=(0.0, 9.0),
        bed_y_range=(0.0, 10.0),
    )

    out_path = tmp_path / "out.gcode"
    with pytest.raises(ValueError):
        export_gcode(layers, out_path, canvas_size=(10.0, 10.0), params=params)


def test_export_gcode_optimize_travel_reorders_fragments_of_one_polyline(
    tmp_path,
) -> None:
    layers = [
        _realized_layer(
            coords=[
                [0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [-10.0, 1.0, 0.0],
                [-10.0, 110.0, 0.0],
                [90.0, 110.0, 0.0],
                [90.0, 2.0, 0.0],
                [90.0, 1.0, 0.0],
                [110.0, 1.0, 0.0],
                [110.0, 110.0, 0.0],
                [1.0, 110.0, 0.0],
                [1.0, 3.0, 0.0],
                [1.0, 2.0, 0.0],
            ],
            offsets=[0, 12],
        )
    ]
    params_no_opt = GCodeParams(
        paper_bottom_right_mm=(100.0, 100.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=False,
    )
    params_opt = GCodeParams(
        paper_bottom_right_mm=(100.0, 100.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=True,
    )

    a = tmp_path / "baseline.gcode"
    export_gcode(layers, a, canvas_size=(100.0, 100.0), params=params_no_opt)

    b = tmp_path / "optimized.gcode"
    export_gcode(layers, b, canvas_size=(100.0, 100.0), params=params_opt)

    travel_a = _travel_distance(
        a.read_text(encoding="utf-8"),
        z_up=params_no_opt.z_up,
        z_down=params_no_opt.z_down,
    )
    travel_b = _travel_distance(
        b.read_text(encoding="utf-8"), z_up=params_opt.z_up, z_down=params_opt.z_down
    )
    assert travel_b < travel_a


def test_export_gcode_optimize_travel_can_reverse_clipped_fragment(tmp_path) -> None:
    layers = [
        _realized_layer(
            coords=[
                [0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [-10.0, 1.0, 0.0],
                [-10.0, 110.0, 0.0],
                [90.0, 110.0, 0.0],
                [90.0, 2.0, 0.0],
                [90.0, 1.0, 0.0],
                [110.0, 1.0, 0.0],
                [110.0, 110.0, 0.0],
                [1.0, 110.0, 0.0],
                [1.0, 3.0, 0.0],
                [1.0, 2.0, 0.0],
            ],
            offsets=[0, 12],
        )
    ]
    params = GCodeParams(
        paper_bottom_right_mm=(100.0, 100.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
    )

    out_path = tmp_path / "out.gcode"
    export_gcode(layers, out_path, canvas_size=(100.0, 100.0), params=params)
    text = out_path.read_text(encoding="utf-8")

    stroke_comments = [
        line for line in text.splitlines() if line.startswith("; stroke polyline")
    ]
    assert stroke_comments[1].endswith("seg 2 reversed")


def test_export_gcode_draw_bridge_skips_pen_up_between_fragments_of_one_polyline(
    tmp_path,
) -> None:
    layers = [
        _realized_layer(
            coords=[
                [1.0, 1.0, 0.0],
                [10.0, 1.0, 0.0],
                [11.0, 1.0, 0.0],
                [11.0, 1.1, 0.0],
                [10.0, 1.1, 0.0],
                [2.0, 1.1, 0.0],
            ],
            offsets=[0, 6],
        )
    ]
    params = GCodeParams(
        paper_bottom_right_mm=(10.0, 10.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=False,
        bridge_draw_distance=0.2,
    )

    out_path = tmp_path / "out.gcode"
    export_gcode(layers, out_path, canvas_size=(10.0, 10.0), params=params)
    lines = out_path.read_text(encoding="utf-8").splitlines()

    i_end_first = lines.index("G1 X10.000 Y1.000")
    i_start_second = lines.index("G1 X10.000 Y1.100")
    assert i_end_first < i_start_second

    travel_feed = f"G1 F{int(round(float(params.travel_feed)))}"
    assert all(
        not (line.startswith("G1 Z") or line == travel_feed)
        for line in lines[i_end_first + 1 : i_start_second]
    )


def test_export_gcode_draw_bridge_disabled_uses_pen_up(tmp_path) -> None:
    layers = [
        _realized_layer(
            coords=[
                [1.0, 1.0, 0.0],
                [2.0, 1.0, 0.0],
                [2.1, 1.0, 0.0],
                [3.1, 1.0, 0.0],
            ],
            offsets=[0, 2, 4],
        )
    ]
    params = GCodeParams(
        paper_bottom_right_mm=(10.0, 10.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=False,
        bridge_draw_distance=None,
    )

    out_path = tmp_path / "out.gcode"
    export_gcode(layers, out_path, canvas_size=(10.0, 10.0), params=params)
    lines = out_path.read_text(encoding="utf-8").splitlines()

    i_end_first = lines.index("G1 X2.000 Y1.000")
    i_start_second = lines.index("G1 X2.100 Y1.000")
    assert i_end_first < i_start_second

    assert "G1 Z3.000" in lines[i_end_first + 1 : i_start_second]


def _stroke_poly_indices(text: str) -> list[int]:
    return [poly_idx for poly_idx, _, _ in _stroke_records(text)]


def _stroke_records(text: str) -> list[tuple[int, int, bool]]:
    out: list[tuple[int, int, bool]] = []
    for line in text.splitlines():
        if not line.startswith("; stroke polyline "):
            continue
        # "; stroke polyline {poly_idx} seg {seg_idx} ..."
        toks = line.split()
        if len(toks) < 6:
            continue
        out.append((int(toks[3]), int(toks[5]), toks[-1] == "reversed"))
    return out


def _stroke_records_by_layer(text: str) -> list[list[tuple[int, int, bool]]]:
    layers: list[list[tuple[int, int, bool]]] = []
    current: list[tuple[int, int, bool]] | None = None
    for line in text.splitlines():
        if line.startswith("; layer ") and line.endswith(" start"):
            current = []
            layers.append(current)
            continue
        if not line.startswith("; stroke polyline ") or current is None:
            continue
        toks = line.split()
        current.append((int(toks[3]), int(toks[5]), toks[-1] == "reversed"))
    return layers


def _undirected_pen_down_segments(
    text: str,
    *,
    z_up: float,
    z_down: float,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    pen_is_down = True
    current_xy: tuple[float, float] | None = None
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for line in text.splitlines():
        if line.startswith("G1 Z"):
            z_txt = line.split("Z", 1)[1].strip().split()[0]
            pen_is_down = _pen_is_down_from_z(float(z_txt), z_up=z_up, z_down=z_down)
            continue
        xy = _parse_xy(line)
        if xy is None:
            continue
        if current_xy is not None and pen_is_down and current_xy != xy:
            start, end = sorted((current_xy, xy))
            segments.append((start, end))
        current_xy = xy
    return sorted(segments)


def _bridge_lengths(text: str, *, z_up: float) -> list[float]:
    current_xy: tuple[float, float] | None = None
    awaiting_stroke_start = False
    lifted_since_comment = False
    lengths: list[float] = []
    for line in text.splitlines():
        if line.startswith("; stroke polyline "):
            awaiting_stroke_start = True
            lifted_since_comment = False
            continue
        if awaiting_stroke_start and line.startswith("G1 Z"):
            z_txt = line.split("Z", 1)[1].strip().split()[0]
            if abs(float(z_txt) - float(z_up)) < 1e-12:
                lifted_since_comment = True
            continue
        xy = _parse_xy(line)
        if xy is None:
            continue
        if (
            awaiting_stroke_start
            and not lifted_since_comment
            and current_xy is not None
            and current_xy != xy
        ):
            lengths.append(hypot(xy[0] - current_xy[0], xy[1] - current_xy[1]))
        awaiting_stroke_start = False
        current_xy = xy
    return lengths


@pytest.mark.parametrize(
    ("optimize_travel", "allow_reverse", "expected"),
    [
        (False, False, [(0, 0, False), (1, 0, False), (2, 0, False)]),
        (False, True, [(0, 0, False), (1, 0, False), (2, 0, False)]),
        (True, False, [(0, 0, False), (2, 0, False), (1, 0, False)]),
        (True, True, [(0, 0, False), (2, 0, False), (1, 0, True)]),
    ],
)
def test_export_gcode_applies_ordering_and_reverse_to_all_strokes_in_layer(
    tmp_path,
    *,
    optimize_travel: bool,
    allow_reverse: bool,
    expected: list[tuple[int, int, bool]],
) -> None:
    layer = _realized_layer(
        coords=[
            [0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [100.0, 0.0, 0.0],
            [100.0, 1.0, 0.0],
            [1.0, 1.0, 0.0],
            [1.0, 2.0, 0.0],
        ],
        offsets=[0, 2, 4, 6],
    )
    params = GCodeParams(
        paper_bottom_right_mm=(200.0, 200.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=optimize_travel,
        allow_reverse=allow_reverse,
        bridge_draw_distance=None,
    )

    out_path = tmp_path / "layer-order.gcode"
    export_gcode([layer], out_path, canvas_size=(200.0, 200.0), params=params)

    text = out_path.read_text(encoding="utf-8")
    assert _stroke_records(text) == expected
    assert "; source_polyline" not in text


def test_export_gcode_layer_master_false_disables_order_reverse_and_bridge(
    tmp_path,
) -> None:
    layer = _realized_layer(
        coords=[
            [0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [100.0, 0.0, 0.0],
            [100.0, 1.0, 0.0],
            [1.0, 1.0, 0.0],
            [1.0, 2.0, 0.0],
        ],
        offsets=[0, 2, 4, 6],
        gcode_optimize=False,
    )
    params = GCodeParams(
        paper_bottom_right_mm=(200.0, 200.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=True,
        allow_reverse=True,
        bridge_draw_distance=1e6,
    )

    out_path = tmp_path / "master-off.gcode"
    export_gcode([layer], out_path, canvas_size=(200.0, 200.0), params=params)
    text = out_path.read_text(encoding="utf-8")

    assert _stroke_records(text) == [
        (0, 0, False),
        (1, 0, False),
        (2, 0, False),
    ]
    assert text.splitlines().count("G1 Z3.000") == 3


@pytest.mark.parametrize(
    ("bridge_draw_distance", "expected_bridge"),
    [(None, False), (0.099, False), (0.1, False), (0.101, True)],
)
def test_export_gcode_bridge_crosses_source_boundary_with_strict_threshold(
    tmp_path,
    *,
    bridge_draw_distance: float | None,
    expected_bridge: bool,
) -> None:
    layer = _realized_layer(
        coords=[
            [1.0, 1.0, 0.0],
            [2.0, 1.0, 0.0],
            [2.1, 1.0, 0.0],
            [3.1, 1.0, 0.0],
        ],
        offsets=[0, 2, 4],
    )
    params = GCodeParams(
        travel_feed=4000.0,
        draw_feed=2000.0,
        paper_bottom_right_mm=(10.0, 10.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=False,
        allow_reverse=True,
        bridge_draw_distance=bridge_draw_distance,
    )

    out_path = tmp_path / "bridge-threshold.gcode"
    export_gcode([layer], out_path, canvas_size=(10.0, 10.0), params=params)
    lines = out_path.read_text(encoding="utf-8").splitlines()
    first_end = lines.index("G1 X2.000 Y1.000")
    second_start = lines.index("G1 X2.100 Y1.000")
    between = lines[first_end + 1 : second_start]

    assert ("G1 Z3.000" not in between) is expected_bridge
    assert ("G1 F4000" not in between) is expected_bridge


def test_export_gcode_bridge_uses_endpoint_after_reverse(tmp_path) -> None:
    layer = _realized_layer(
        coords=[
            [0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [10.0, 1.0, 0.0],
            [0.1, 1.0, 0.0],
        ],
        offsets=[0, 2, 4],
    )
    params = GCodeParams(
        paper_bottom_right_mm=(20.0, 20.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=True,
        allow_reverse=True,
        bridge_draw_distance=0.2,
    )

    out_path = tmp_path / "bridge-after-reverse.gcode"
    export_gcode([layer], out_path, canvas_size=(20.0, 20.0), params=params)
    lines = out_path.read_text(encoding="utf-8").splitlines()

    assert _stroke_records("\n".join(lines)) == [(0, 0, False), (1, 0, True)]
    first_end = lines.index("G1 X0.000 Y1.000")
    reversed_start = lines.index("G1 X0.100 Y1.000")
    assert "G1 Z3.000" not in lines[first_end + 1 : reversed_start]


def test_export_gcode_flattens_clipped_fragments_with_other_layer_strokes(
    tmp_path,
) -> None:
    layer = _realized_layer(
        coords=[
            [1.0, 1.0, 0.0],
            [11.0, 1.0, 0.0],
            [11.0, 1.1, 0.0],
            [10.0, 1.1, 0.0],
            [2.0, 1.1, 0.0],
            [10.0, 1.0, 0.0],
            [10.0, 1.05, 0.0],
        ],
        offsets=[0, 5, 7],
    )
    params = GCodeParams(
        paper_bottom_right_mm=(10.0, 10.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=True,
        allow_reverse=False,
        bridge_draw_distance=None,
    )

    out_path = tmp_path / "flat-fragments.gcode"
    export_gcode([layer], out_path, canvas_size=(10.0, 10.0), params=params)

    assert _stroke_records(out_path.read_text(encoding="utf-8")) == [
        (0, 0, False),
        (1, 0, False),
        (0, 1, False),
    ]


def test_export_gcode_applies_independent_optimization_policy_per_layer(
    tmp_path,
) -> None:
    coords = [
        [0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [100.0, 0.0, 0.0],
        [100.0, 1.0, 0.0],
        [1.0, 1.0, 0.0],
        [1.0, 2.0, 0.0],
    ]
    layers = [
        _realized_layer(coords=coords, offsets=[0, 2, 4, 6]),
        _realized_layer(
            coords=coords,
            offsets=[0, 2, 4, 6],
            gcode_optimize=False,
        ),
    ]
    params = GCodeParams(
        paper_bottom_right_mm=(200.0, 200.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=True,
        allow_reverse=True,
        bridge_draw_distance=1e6,
    )

    out_path = tmp_path / "layer-boundary.gcode"
    export_gcode(layers, out_path, canvas_size=(200.0, 200.0), params=params)
    text = out_path.read_text(encoding="utf-8")

    assert _stroke_records_by_layer(text) == [
        [(0, 0, False), (2, 0, False), (1, 0, True)],
        [(0, 0, False), (1, 0, False), (2, 0, False)],
    ]


def test_export_gcode_never_bridges_between_enabled_layers(tmp_path) -> None:
    layers = [
        _realized_layer(
            coords=[[1.0, 1.0, 0.0], [2.0, 1.0, 0.0]],
            offsets=[0, 2],
        ),
        _realized_layer(
            coords=[[2.1, 1.0, 0.0], [3.1, 1.0, 0.0]],
            offsets=[0, 2],
        ),
    ]
    params = GCodeParams(
        paper_bottom_right_mm=(10.0, 10.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=True,
        allow_reverse=True,
        bridge_draw_distance=1e6,
    )

    out_path = tmp_path / "enabled-layer-boundary.gcode"
    export_gcode(layers, out_path, canvas_size=(10.0, 10.0), params=params)
    lines = out_path.read_text(encoding="utf-8").splitlines()
    layer_1_start = lines.index("; layer 1 start")
    layer_1_first_xy = next(
        index
        for index in range(layer_1_start + 1, len(lines))
        if _parse_xy(lines[index]) is not None
    )
    assert "G1 Z3.000" in lines[layer_1_start + 1 : layer_1_first_xy]


def test_export_gcode_optimization_preserves_stroke_geometry_without_bridge(
    tmp_path,
) -> None:
    layer = _realized_layer(
        coords=[
            [0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [100.0, 0.0, 0.0],
            [100.0, 1.0, 0.0],
            [1.0, 1.0, 0.0],
            [1.0, 2.0, 0.0],
        ],
        offsets=[0, 2, 4, 6],
    )
    common = dict(
        paper_bottom_right_mm=(200.0, 200.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
        bridge_draw_distance=None,
    )
    baseline_params = GCodeParams(
        **common,
        optimize_travel=False,
        allow_reverse=False,
    )
    optimized_params = GCodeParams(
        **common,
        optimize_travel=True,
        allow_reverse=True,
    )
    baseline_path = tmp_path / "geometry-baseline.gcode"
    optimized_path = tmp_path / "geometry-optimized.gcode"
    export_gcode(
        [layer], baseline_path, canvas_size=(200.0, 200.0), params=baseline_params
    )
    export_gcode(
        [layer], optimized_path, canvas_size=(200.0, 200.0), params=optimized_params
    )

    baseline = baseline_path.read_text(encoding="utf-8")
    optimized = optimized_path.read_text(encoding="utf-8")
    baseline_ids = sorted(
        (poly_idx, seg_idx) for poly_idx, seg_idx, _ in _stroke_records(baseline)
    )
    optimized_ids = sorted(
        (poly_idx, seg_idx) for poly_idx, seg_idx, _ in _stroke_records(optimized)
    )
    assert baseline_ids == optimized_ids
    assert _undirected_pen_down_segments(
        baseline,
        z_up=baseline_params.z_up,
        z_down=baseline_params.z_down,
    ) == _undirected_pen_down_segments(
        optimized,
        z_up=optimized_params.z_up,
        z_down=optimized_params.z_down,
    )


def test_export_gcode_fill_like_layer_becomes_serpentine_and_bridgeable(
    tmp_path,
) -> None:
    n_strokes = 8
    coords: list[list[float]] = []
    offsets = [0]
    for index in range(n_strokes):
        y = 1.0 + index * 0.1
        coords.extend(([1.0, y, 0.0], [9.0, y, 0.0]))
        offsets.append(offsets[-1] + 2)

    optimized_layer = _realized_layer(coords=coords, offsets=offsets)
    disabled_layer = _realized_layer(
        coords=coords,
        offsets=offsets,
        gcode_optimize=False,
    )
    params_without_bridge = GCodeParams(
        paper_bottom_right_mm=(10.0, 10.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=True,
        allow_reverse=True,
        bridge_draw_distance=None,
    )
    params_with_bridge = GCodeParams(
        paper_bottom_right_mm=(10.0, 10.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=True,
        allow_reverse=True,
        bridge_draw_distance=0.101,
    )
    optimized_path = tmp_path / "fill-optimized.gcode"
    bridged_path = tmp_path / "fill-bridged.gcode"
    disabled_path = tmp_path / "fill-disabled.gcode"
    export_gcode(
        [optimized_layer],
        optimized_path,
        canvas_size=(10.0, 10.0),
        params=params_without_bridge,
    )
    export_gcode(
        [optimized_layer],
        bridged_path,
        canvas_size=(10.0, 10.0),
        params=params_with_bridge,
    )
    export_gcode(
        [disabled_layer],
        disabled_path,
        canvas_size=(10.0, 10.0),
        params=params_with_bridge,
    )

    optimized = optimized_path.read_text(encoding="utf-8")
    bridged = bridged_path.read_text(encoding="utf-8")
    disabled = disabled_path.read_text(encoding="utf-8")
    assert _stroke_records(optimized) == [
        (index, 0, bool(index % 2)) for index in range(n_strokes)
    ]
    assert _travel_distance(
        optimized,
        z_up=params_without_bridge.z_up,
        z_down=params_without_bridge.z_down,
    ) < _travel_distance(
        disabled,
        z_up=params_with_bridge.z_up,
        z_down=params_with_bridge.z_down,
    )
    assert bridged.splitlines().count("G1 Z3.000") == 1
    bridge_lengths = _bridge_lengths(bridged, z_up=params_with_bridge.z_up)
    assert len(bridge_lengths) == n_strokes - 1
    assert all(length < 0.101 for length in bridge_lengths)
    assert _stroke_records(disabled) == [
        (index, 0, False) for index in range(n_strokes)
    ]
    assert disabled.splitlines().count("G1 Z3.000") == n_strokes


@pytest.mark.parametrize("include_closed_boundary", [False, True])
def test_export_gcode_does_not_infer_face_groups_from_mixed_polylines(
    tmp_path,
    *,
    include_closed_boundary: bool,
) -> None:
    first = (
        [
            [0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [1.0, 1.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
        ]
        if include_closed_boundary
        else [[0.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    )
    polylines = [
        first,
        [[100.0, 0.0, 0.0], [100.0, 1.0, 0.0]],
        [[0.1, 0.0, 0.0], [0.1, 1.0, 0.0]],
    ]
    coords = [point for polyline in polylines for point in polyline]
    offsets = [0]
    for polyline in polylines:
        offsets.append(offsets[-1] + len(polyline))
    params = GCodeParams(
        paper_bottom_right_mm=(200.0, 200.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
        optimize_travel=True,
        allow_reverse=False,
        bridge_draw_distance=None,
    )

    out_path = tmp_path / f"mixed-polylines-{include_closed_boundary}.gcode"
    export_gcode(
        [_realized_layer(coords=coords, offsets=offsets)],
        out_path,
        canvas_size=(200.0, 200.0),
        params=params,
    )

    assert _stroke_poly_indices(out_path.read_text(encoding="utf-8")) == [0, 2, 1]


def test_export_gcode_default_and_explicit_true_layer_master_are_identical(
    tmp_path,
) -> None:
    coords = [
        [1.0, 1.0, 0.0],
        [2.0, 1.0, 0.0],
        [2.1, 1.0, 0.0],
        [3.1, 1.0, 0.0],
    ]
    params = GCodeParams(
        paper_bottom_right_mm=(10.0, 10.0),
        y_down=False,
        paper_margin_mm=0.0,
        decimals=3,
    )
    default_path = tmp_path / "default-master.gcode"
    explicit_path = tmp_path / "explicit-master.gcode"
    export_gcode(
        [_realized_layer(coords=coords, offsets=[0, 2, 4])],
        default_path,
        canvas_size=(10.0, 10.0),
        params=params,
    )
    export_gcode(
        [_realized_layer(coords=coords, offsets=[0, 2, 4], gcode_optimize=True)],
        explicit_path,
        canvas_size=(10.0, 10.0),
        params=params,
    )

    assert default_path.read_bytes() == explicit_path.read_bytes()


def test_export_gcode_requires_explicit_params(tmp_path) -> None:
    layers = [
        _realized_layer(
            coords=[
                [1.0, 1.0, 0.0],
                [2.0, 1.0, 0.0],
            ],
            offsets=[0, 2],
        )
    ]

    out_path = tmp_path / "out.gcode"
    with pytest.raises(TypeError, match="params"):
        export_gcode(layers, out_path, canvas_size=(10.0, 10.0))  # type: ignore[call-arg]

    assert not out_path.exists()
