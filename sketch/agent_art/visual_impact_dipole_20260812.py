from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np

from grafix import E, G, L, primitive, run

# Reference-reproduction adjustment block.  The supplied 4:5 board is fitted
# uniformly to A5 by width.  Its paper colour continues through the 12.5 mm
# bands above and below, preserving every element without geometric stretching.
CANVAS_SIZE = (148, 210)
BACKGROUND_COLOR = (237 / 255, 236 / 255, 234 / 255)
LINE_THICKNESS = 0.001
SEED = 20260812
LINE_COLORS = {
    "ink": (42 / 255, 42 / 255, 42 / 255),
    "field": (72 / 255, 72 / 255, 72 / 255),
    "construction": (202 / 255, 201 / 255, 198 / 255),
}
FILL_DENSITIES = {
    "solid": 1000.0,
    "typography": 1000.0,
}
FILL_MIN_SPACINGS = {
    "solid": 0.035,
    "typography": 0.035,
}
REFERENCE_SIZE = (1024.0, 1280.0)
LAYOUT_FIT_SCALE = CANVAS_SIZE[0] / REFERENCE_SIZE[0]
LAYOUT_CROP = (0.0, 0.0, 0.0, 0.0)
LAYOUT_OFFSET = (
    0.0,
    (CANVAS_SIZE[1] - REFERENCE_SIZE[1] * LAYOUT_FIT_SCALE) / 2.0,
)
SPHERE_CENTER = (511.0, 586.0)
SPHERE_RADIUS = 400.0
SPHERE_AXIS_PROJECTION = 292.5
SPHERE_DEPTH_PROJECTION = math.sqrt(
    SPHERE_RADIUS * SPHERE_RADIUS
    - SPHERE_AXIS_PROJECTION * SPHERE_AXIS_PROJECTION
)
SPHERE_MAJOR_ROTATIONS = (
    0.0,
    -24.0,
    24.0,
    -45.0,
    45.0,
    -63.0,
    63.0,
    -76.0,
    76.0,
    -84.0,
    84.0,
)
SPHERE_MINOR_ROTATIONS = (
    -12.0,
    12.0,
    -34.0,
    34.0,
    -55.0,
    55.0,
    -70.0,
    70.0,
    -80.0,
    80.0,
)
SPHERE_PARALLEL_OFFSETS = (-0.78, -0.55, -0.28, 0.28, 0.55, 0.78)

_ROOT = Path(__file__).resolve().parents[2]
MONO_FONT = str(_ROOT / "data/input/font/GeistMono-Light.ttf")
MASTHEAD_FONT = str(_ROOT / "data/input/font/GeistMono-Light.ttf")
SANS_FONT = str(_ROOT / "data/input/font/HelveticaNeue.ttc")
SANS_REGULAR_INDEX = 0

Point = tuple[float, float]
Polyline = list[Point]


def _pack(polylines: list[Polyline]) -> tuple[np.ndarray, np.ndarray]:
    values: list[tuple[float, float, float]] = []
    offsets = [0]
    for polyline in polylines:
        if len(polyline) < 2:
            continue
        values.extend((float(x), float(y), 0.0) for x, y in polyline)
        offsets.append(len(values))
    coords = np.asarray(values, dtype=np.float32).reshape((-1, 3))
    indices = np.asarray(offsets, dtype=np.int32)
    return np.ascontiguousarray(coords), np.ascontiguousarray(indices)


def _line(lines: list[Polyline], a: Point, b: Point) -> None:
    lines.append([a, b])


def _polyline(lines: list[Polyline], points: Polyline, *, closed: bool = False) -> None:
    lines.append([*points, points[0]] if closed and points else points)


def _ellipse_points(
    cx: float,
    cy: float,
    radius_x: float,
    radius_y: float,
    *,
    samples: int = 720,
) -> Polyline:
    return [
        (
            cx + radius_x * math.cos(math.tau * index / samples),
            cy + radius_y * math.sin(math.tau * index / samples),
        )
        for index in range(samples + 1)
    ]


def _dashed_polyline(
    points: Polyline,
    *,
    dash_length: float,
    gap_length: float,
    phase: float = 0.0,
) -> list[Polyline]:
    if len(points) < 2:
        return []
    pattern = dash_length + gap_length
    phase = phase % pattern
    drawing = phase < dash_length
    remaining = (dash_length if drawing else pattern) - phase
    output: list[Polyline] = []
    current: Polyline = []
    for start, end in zip(points, points[1:]):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.hypot(dx, dy)
        if length <= 1e-12:
            continue
        travelled = 0.0
        while travelled < length - 1e-12:
            step = min(remaining, length - travelled)
            t0 = travelled / length
            t1 = (travelled + step) / length
            a = (start[0] + dx * t0, start[1] + dy * t0)
            b = (start[0] + dx * t1, start[1] + dy * t1)
            if drawing:
                if not current:
                    current = [a]
                current.append(b)
            travelled += step
            remaining -= step
            if remaining <= 1e-10:
                if drawing and len(current) >= 2:
                    output.append(current)
                    current = []
                drawing = not drawing
                remaining = dash_length if drawing else gap_length
    if drawing and len(current) >= 2:
        output.append(current)
    return output


def _circle(
    lines: list[Polyline],
    cx: float,
    cy: float,
    radius: float,
    *,
    samples: int = 96,
) -> None:
    _polyline(lines, _ellipse_points(cx, cy, radius, radius, samples=samples), closed=False)


def _rounded_rect(
    lines: list[Polyline],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius: float,
    *,
    samples: int = 10,
) -> None:
    points: Polyline = []
    for cx, cy, start in (
        (x1 - radius, y0 + radius, -90.0),
        (x1 - radius, y1 - radius, 0.0),
        (x0 + radius, y1 - radius, 90.0),
        (x0 + radius, y0 + radius, 180.0),
    ):
        for index in range(samples + 1):
            angle = math.radians(start + 90.0 * index / samples)
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    _polyline(lines, points, closed=True)


def _sphere_great_circle(
    cx: float,
    cy: float,
    rotation: float,
    *,
    samples: int = 900,
) -> Polyline:
    """同一の真球に属する大円を正投影して返す。"""

    phi = math.radians(rotation)
    projected_depth = SPHERE_DEPTH_PROJECTION * math.sin(phi)
    projected_height = SPHERE_RADIUS * math.cos(phi)
    return [
        (
            cx
            + SPHERE_AXIS_PROJECTION * math.cos(math.tau * index / samples)
            + projected_depth * math.sin(math.tau * index / samples),
            cy + projected_height * math.sin(math.tau * index / samples),
        )
        for index in range(samples + 1)
    ]


def _sphere_parallel(
    cx: float,
    cy: float,
    offset: float,
    *,
    samples: int = 720,
) -> Polyline:
    """同じ真球の回転軸に直交する小円を正投影して返す。"""

    radius = math.sqrt(max(0.0, 1.0 - offset * offset))
    return [
        (
            cx
            + SPHERE_AXIS_PROJECTION * offset
            + SPHERE_DEPTH_PROJECTION
            * radius
            * math.cos(math.tau * index / samples),
            cy
            + SPHERE_RADIUS
            * radius
            * math.sin(math.tau * index / samples),
        )
        for index in range(samples + 1)
    ]


def _add_construction_grid(lines: list[Polyline]) -> None:
    # The visible plate is a 12 x 15 system of 60 px cells.  Every cell carries
    # a diagonal cross and an inscribed circle in addition to the shared rules.
    x_values = [152.0 + 60.0 * index for index in range(13)]
    y_values = [146.0 + 61.8 * index for index in range(16)]
    for x in x_values:
        _line(lines, (x, y_values[0]), (x, y_values[-1]))
    for y in y_values:
        _line(lines, (x_values[0], y), (x_values[-1], y))
    for column in range(12):
        x0, x1 = x_values[column], x_values[column + 1]
        cx = (x0 + x1) * 0.5
        for row in range(15):
            y0, y1 = y_values[row], y_values[row + 1]
            cy = (y0 + y1) * 0.5
            _line(lines, (x0, y0), (x1, y1))
            _line(lines, (x1, y0), (x0, y1))
            radius_x = (x1 - x0) * 0.5
            radius_y = (y1 - y0) * 0.5
            ellipse = _ellipse_points(cx, cy, radius_x, radius_y, samples=64)
            _polyline(lines, ellipse)


def _add_field(lines: list[Polyline]) -> None:
    cx, cy = SPHERE_CENTER

    # These curves are exact orthographic projections of great circles on one
    # 3-D sphere.  Splitting each principal circle at its two projected poles
    # resets the dash phase there, producing the source's symmetric starbursts.
    for rotation in SPHERE_MAJOR_ROTATIONS:
        points = _sphere_great_circle(cx, cy, rotation)
        midpoint = len(points) // 2
        for half in (points[: midpoint + 1], points[midpoint:]):
            lines.extend(
                _dashed_polyline(
                    half,
                    dash_length=8.5,
                    gap_length=7.0,
                    phase=0.0,
                )
            )

    # Fine intermediate great circles retain the delicate dotted mesh.
    for index, rotation in enumerate(SPHERE_MINOR_ROTATIONS):
        lines.extend(
            _dashed_polyline(
                _sphere_great_circle(cx, cy, rotation),
                dash_length=1.3,
                gap_length=2.8,
                phase=index * 0.7,
            )
        )

    # Six true small circles expose the tilted depth axis of the sphere.
    for index, offset in enumerate(SPHERE_PARALLEL_OFFSETS):
        lines.extend(
            _dashed_polyline(
                _sphere_parallel(cx, cy, offset),
                dash_length=1.3,
                gap_length=2.8,
                phase=index * 0.7,
            )
        )

    # The projected silhouette of a true sphere is a true circle.  Its broken
    # contour keeps the photographed construction-line character.
    lines.extend(
        _dashed_polyline(
            _ellipse_points(
                cx,
                cy,
                SPHERE_RADIUS,
                SPHERE_RADIUS,
                samples=900,
            ),
            dash_length=4.6,
            gap_length=4.0,
        )
    )

    # Projected equator axis and the two antipodal poles shared by every great
    # circle; all dimensions are derived from the same sphere model.
    _line(lines, (cx - SPHERE_RADIUS, cy), (cx + SPHERE_RADIUS, cy))
    for fx in (
        cx - SPHERE_AXIS_PROJECTION,
        cx + SPHERE_AXIS_PROJECTION,
    ):
        _line(lines, (fx - 4.8, cy), (fx + 4.8, cy))
        _line(lines, (fx, cy - 4.8), (fx, cy + 4.8))


def _add_corner_marks(lines: list[Polyline]) -> None:
    for x, y in ((152.0, 146.0), (870.0, 146.0), (152.0, 1073.0), (870.0, 1073.0)):
        _line(lines, (x - 3.0, y), (x + 3.0, y))
        _line(lines, (x, y - 3.0), (x, y + 3.0))


@lru_cache(maxsize=1)
def _construction_data() -> tuple[np.ndarray, np.ndarray]:
    lines: list[Polyline] = []
    _add_construction_grid(lines)
    _add_corner_marks(lines)
    return _pack(lines)


@lru_cache(maxsize=1)
def _field_data() -> tuple[np.ndarray, np.ndarray]:
    lines: list[Polyline] = []
    _add_field(lines)
    return _pack(lines)


@primitive
def visual_impact_construction() -> tuple[np.ndarray, np.ndarray]:
    """淡い幾何構成罫を返す。"""

    coords, offsets = _construction_data()
    return coords.copy(), offsets.copy()


@primitive
def visual_impact_field() -> tuple[np.ndarray, np.ndarray]:
    """真球の大円・小円と投影軸を返す。"""

    coords, offsets = _field_data()
    return coords.copy(), offsets.copy()


@lru_cache(maxsize=1)
def _identity_marks_data() -> tuple[np.ndarray, np.ndarray]:
    lines: list[Polyline] = []
    for x0, y0, x1, y1 in (
        (833, 42, 846, 56),
        (819, 55, 835, 68),
        (846, 55, 859, 69),
        (833, 68, 847, 82),
        (859, 68, 873, 82),
        (819, 81, 835, 95),
        (846, 81, 860, 95),
        (833, 94, 847, 109),
        (41.5, 1222.0, 47.0, 1228.0),
        (36.0, 1227.0, 42.5, 1233.0),
        (46.5, 1227.0, 52.0, 1233.0),
        (41.5, 1232.0, 47.0, 1238.0),
    ):
        radius = 5.6 if y0 < 200.0 else 2.1
        _rounded_rect(lines, x0, y0, x1, y1, radius, samples=8)
    return _pack(lines)


@primitive
def visual_impact_identity_marks() -> tuple[np.ndarray, np.ndarray]:
    """ヘッダーとフッターの丸い識別記号を返す。"""

    coords, offsets = _identity_marks_data()
    return coords.copy(), offsets.copy()


LEFT_SILHOUETTE = (
    (454.0, 314.0),
    (496.0, 334.0),
    (504.0, 379.0),
    (484.0, 407.0),
    (415.0, 434.0),
    (402.0, 483.0),
    (423.0, 516.0),
    (493.0, 545.0),
    (506.0, 597.0),
    (485.0, 637.0),
    (414.0, 667.0),
    (401.0, 707.0),
    (423.0, 750.0),
    (481.0, 766.0),
    (503.0, 792.0),
    (498.0, 839.0),
    (461.0, 861.0),
    (417.0, 842.0),
    (403.0, 787.0),
    (384.0, 763.0),
    (313.0, 748.0),
    (288.0, 706.0),
    (301.0, 666.0),
    (379.0, 635.0),
    (401.0, 592.0),
    (377.0, 538.0),
    (300.0, 508.0),
    (292.0, 451.0),
    (321.0, 422.0),
    (387.0, 409.0),
    (416.0, 334.0),
)


def _combine(geometries: list | tuple):
    result = geometries[0]
    for geometry in geometries[1:]:
        result = result + geometry
    return result


def _solid_shapes_geometry():
    left = G(name="Central left / measured spline silhouette").spline(
        points=LEFT_SILHOUETTE,
        closed=True,
        tension=0.27,
        segments_per_span=12,
        key="visual-impact-left-silhouette",
    )
    right_points = tuple((1022.0 - x, y) for x, y in LEFT_SILHOUETTE)
    right = G(name="Central right / mirrored spline silhouette").spline(
        points=right_points,
        closed=True,
        tension=0.27,
        segments_per_span=12,
        key="visual-impact-right-silhouette",
    )
    circles = [
        G.circle(
            radius=radius,
            segments=160,
            center=(x, y, 0.0),
            key="visual-impact-terminal-circle",
            instance_key=index,
        )
        for index, (x, y, radius) in enumerate(
            (
                (456.7, 258.3, 51.1),
                (564.1, 258.1, 51.1),
                (456.3, 916.4, 50.8),
                (565.1, 916.4, 50.8),
            )
        )
    ]

    identity_marks = G(name="Header and footer identity marks").visual_impact_identity_marks()
    return _combine([left, right, *circles, identity_marks])


def _text(
    text: str,
    x: float,
    y: float,
    scale: float,
    *,
    font: str,
    font_index: int = 0,
    align: str = "left",
    spacing: float = 0.0,
    line_height: float = 1.2,
    suffix: str = "",
):
    return G(name=f"Text / {' '.join(text.split())[:30]}").text(
        text=text,
        font=font,
        font_index=font_index,
        text_align=align,
        letter_spacing_em=spacing,
        line_height=line_height,
        quality=0.42,
        center=(x, y, 0.0),
        scale=scale,
        key="visual-impact-typography",
        instance_key=f"{text}:{x:.3f}:{y:.3f}:{suffix}",
    )


def _rotated_glyph(
    character: str,
    x: float,
    y: float,
    scale: float,
    angle: float,
    *,
    suffix: str,
):
    glyph = _text(
        character,
        0.0,
        0.0,
        scale,
        font=MONO_FONT,
        align="center",
        suffix=suffix,
    )
    return E(name=f"Seal glyph / {character}").affine(
        auto_center=False,
        pivot=(0.0, 0.0, 0.0),
        rotation=(0.0, 0.0, angle),
        scale=(1.0, 1.0, 1.0),
        delta=(x, y, 0.0),
        key="visual-impact-seal-glyph",
        instance_key=suffix,
    )(glyph)


def _seal_text() -> list:
    text = "CONNECT · GOVERN · CREATE · INSPIRE · EVOLVE · "
    cx, cy, radius = 935.0, 1143.0, 43.0
    geometries = []
    for index, character in enumerate(text):
        if character == " ":
            continue
        angle = -90.0 + 360.0 * index / len(text)
        radians = math.radians(angle)
        x = cx + radius * math.cos(radians)
        y = cy + radius * math.sin(radians)
        geometries.append(
            _rotated_glyph(
                character,
                x,
                y,
                5.5,
                angle + 90.0,
                suffix=f"seal-{index}",
            )
        )
    return geometries


def _typography_geometry():
    items = [
        _text(
            "VISUAL\nIMPACT",
            40.0,
            30.5,
            29.5,
            font=MASTHEAD_FONT,
            line_height=1.02,
            spacing=0.42,
            suffix="masthead",
        ),
        _text(
            "S H A P E · C O N N E C T · I N S P I R E",
            40.0,
            108.0,
            8.1,
            font=MONO_FONT,
            suffix="strapline",
        ),
        _text(
            "SYSTEMS\nTHAT SHAPE\nTOMORROW",
            908.0,
            47.0,
            11.8,
            font=MONO_FONT,
            spacing=0.12,
            line_height=1.52,
            suffix="top-right",
        ),
        _text(
            "Your Gateway\nto the Future.",
            37.0,
            1018.0,
            26.2,
            font=SANS_FONT,
            font_index=4,
            line_height=1.07,
            suffix="gateway",
        ),
        _text(
            "Connecting ideas. Aligning systems.\nDesigning impact that resonates\nacross boundaries and builds\na better tomorrow.",
            38.0,
            1092.5,
            11.1,
            font=SANS_FONT,
            font_index=SANS_REGULAR_INDEX,
            line_height=1.55,
            suffix="body-copy",
        ),
        _text(
            "FIELD DIAGRAM: DIPOLE SYSTEM",
            702.0,
            1124.0,
            6.8,
            font=MONO_FONT,
            spacing=0.09,
            suffix="legend-head",
        ),
        _text(
            "POTENTIAL LINE\nFLUX LINE\nAXIS",
            744.0,
            1164.0,
            6.4,
            font=MONO_FONT,
            spacing=0.13,
            line_height=2.05,
            suffix="legend-labels",
        ),
        _text(
            "VISUAL IMPACT SYSTEMS",
            78.0,
            1221.0,
            6.6,
            font=MONO_FONT,
            spacing=0.38,
            suffix="footer-name",
        ),
        _text(
            "EST. 2025",
            262.0,
            1221.0,
            6.6,
            font=MONO_FONT,
            spacing=0.14,
            suffix="footer-date",
        ),
        *_seal_text(),
    ]
    return _combine(items)


@lru_cache(maxsize=1)
def _annotation_data() -> tuple[np.ndarray, np.ndarray]:
    lines: list[Polyline] = []
    # Legend rules and samples.
    _line(lines, (702, 1150), (859, 1150))
    for dy in (-0.45, 0.45):
        _dashed_polyline_result = _dashed_polyline(
            [(702, 1170 + dy), (731, 1170 + dy)],
            dash_length=1.4,
            gap_length=2.1,
        )
        lines.extend(_dashed_polyline_result)
    _dashed = _dashed_polyline(
        [(702, 1190), (731, 1190)], dash_length=7.0, gap_length=6.0
    )
    lines.extend(_dashed)
    _line(lines, (702, 1210), (731, 1210))

    # Circular seal, central cross, footer separator.
    _circle(lines, 935.0, 1143.0, 43.0, samples=180)
    _circle(lines, 935.0, 1143.0, 42.2, samples=180)
    _line(lines, (925.0, 1143.0), (945.0, 1143.0))
    _line(lines, (935.0, 1133.0), (935.0, 1153.0))
    _line(lines, (236.0, 1220.0), (236.0, 1241.0))
    return _pack(lines)


@primitive
def visual_impact_annotations() -> tuple[np.ndarray, np.ndarray]:
    """凡例、円形シール、フッター罫を返す。"""

    coords, offsets = _annotation_data()
    return coords.copy(), offsets.copy()


def draw(t: float):
    del t
    fit = E(name="4:5 reference board / uniform A5 contain").affine(
        auto_center=False,
        pivot=(0.0, 0.0, 0.0),
        scale=(LAYOUT_FIT_SCALE, LAYOUT_FIT_SCALE, 1.0),
        delta=(LAYOUT_OFFSET[0], LAYOUT_OFFSET[1], 0.0),
        key="visual-impact-reference-fit",
    )

    solids = E(name="Central masses and identity marks / dense fill").fill(
        angle_sets=6,
        angle=45.0,
        density=FILL_DENSITIES["solid"],
        min_spacing=FILL_MIN_SPACINGS["solid"],
        remove_boundary=False,
        key="visual-impact-solid-fill",
    )(_solid_shapes_geometry())
    typography = E(name="Poster typography / dense fill").fill(
        angle_sets=6,
        angle=45.0,
        density=FILL_DENSITIES["typography"],
        min_spacing=FILL_MIN_SPACINGS["typography"],
        remove_boundary=False,
        key="visual-impact-type-fill",
    )(_typography_geometry())

    construction = fit(
        G(name="Pale modular construction grid").visual_impact_construction()
    )
    ink = (
        fit(solids),
        fit(G(name="Legend and seal linework").visual_impact_annotations()),
        fit(typography),
    )
    field = fit(G(name="True-sphere wireframe").visual_impact_field())
    return (
        L(name="Pale construction ink").layer(
            construction,
            color=LINE_COLORS["construction"],
            thickness=LINE_THICKNESS,
        ),
        L(name="Primary black ink").layer(
            ink,
            color=LINE_COLORS["ink"],
            thickness=LINE_THICKNESS,
        ),
        L(name="Spherical wireframe grey").layer(
            field,
            color=LINE_COLORS["field"],
            thickness=LINE_THICKNESS,
        ),
    )


if __name__ == "__main__":
    run(
        draw,
        canvas_size=CANVAS_SIZE,
        render_scale=4.0,
        background_color=BACKGROUND_COLOR,
        line_color=LINE_COLORS["ink"],
        line_thickness=LINE_THICKNESS,
        parameter_persistence=False,
        midi_port_name=None,
        n_worker=0,
        seed=SEED,
    )
