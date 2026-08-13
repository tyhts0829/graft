from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from grafix import A4, E, G, L, primitive, run

# Reference-reproduction adjustment block.  The supplied photograph is fitted
# uniformly to A5; its slight 905:1280 aspect-ratio difference is not stretched.
CANVAS_SIZE = A4
BACKGROUND_COLOR = (238 / 255, 231 / 255, 219 / 255)
LINE_THICKNESS = 0.001
SEED = 2704
LINE_COLORS = {"ink": (42 / 255, 42 / 255, 42 / 255)}
FILL_DENSITIES = {
    "typography": 760.0,
    "solid": 1000.0,
}
FILL_MIN_SPACINGS = {
    "typography": 0.065,
    "solid": 0.050,
}
REFERENCE_SIZE = (905.0 * 210.0 / 1280.0, 210.0)
LAYOUT_FIT_SCALE = min(
    CANVAS_SIZE[0] / REFERENCE_SIZE[0],
    CANVAS_SIZE[1] / REFERENCE_SIZE[1],
)
LAYOUT_CROP = (0.0, 0.0, 0.0, 0.0)
LAYOUT_OFFSET = (
    (CANVAS_SIZE[0] - REFERENCE_SIZE[0] * LAYOUT_FIT_SCALE) / 2.0,
    (CANVAS_SIZE[1] - REFERENCE_SIZE[1] * LAYOUT_FIT_SCALE) / 2.0,
)
TOP_CIRCLE_CENTER = (76.78, 45.98)
TOP_CIRCLE_RADIUS = 16.91
TOP_CIRCLE_TANGENT_Y = TOP_CIRCLE_CENTER[1] - TOP_CIRCLE_RADIUS
CENTRAL_CROSS = (78.99, 109.76)
CENTRAL_RADIUS_X = CENTRAL_CROSS[0] - 59.36
CENTRAL_RADIUS_Y = CENTRAL_CROSS[1] - 88.92

HELVETICA_FONT = "/System/Library/Fonts/HelveticaNeue.ttc"
REGULAR_FONT = (HELVETICA_FONT, 0)
MEDIUM_FONT = (HELVETICA_FONT, 10)
BOLD_FONT = (HELVETICA_FONT, 1)

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


def _polyline(
    lines: list[Polyline], points: list[Point], *, closed: bool = False
) -> None:
    lines.append([*points, points[0]] if closed and points else points)


def _rect(lines: list[Polyline], x0: float, y0: float, x1: float, y1: float) -> None:
    _polyline(lines, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], closed=True)


def _circle(
    lines: list[Polyline], cx: float, cy: float, radius: float, *, samples: int = 96
) -> None:
    _polyline(
        lines,
        [
            (
                cx + radius * math.cos(math.tau * index / samples),
                cy + radius * math.sin(math.tau * index / samples),
            )
            for index in range(samples)
        ],
        closed=True,
    )


def _plus(lines: list[Polyline], cx: float, cy: float, half: float) -> None:
    _line(lines, (cx - half, cy), (cx + half, cy))
    _line(lines, (cx, cy - half), (cx, cy + half))


def _dot(lines: list[Polyline], cx: float, cy: float, radius: float = 0.32) -> None:
    _circle(lines, cx, cy, radius, samples=24)


@lru_cache(maxsize=1)
def _poster_linework_data() -> tuple[np.ndarray, np.ndarray]:
    lines: list[Polyline] = []

    # Long construction rules and their registration points.
    _line(lines, (6.4, TOP_CIRCLE_TANGENT_Y), (78.9, TOP_CIRCLE_TANGENT_Y))
    _line(lines, (14.3, 15.6), (14.3, 198.1))
    # Stop at the lower construction rule so this guide does not cut through
    # the circular quality statement near the foot of the poster.
    _line(lines, (59.36, 13.8), (59.36, 130.4))
    # Leave this guide blank while it crosses the black semicircle.  The
    # matching gap is also cut from the fill below, so the paper itself forms
    # a clean white registration line through the plane.
    _line(lines, (102.05, 6.4), (102.05, 44.78))
    _line(lines, (102.05, 89.02), (102.05, 164.88))
    _line(lines, (53.5, 88.8), (140.3, 88.8))
    # The central horizontal guide becomes a white channel through the black
    # modular block, so only its paper-side segments are drawn in ink.
    _line(lines, (7.4, 130.4), (69.50, 130.4))
    _line(lines, (102.22, 130.4), (141.4, 130.4))
    _line(lines, (36.4, 164.8), (111.5, 164.8))
    # The note rail terminates at the central rule; extending it farther would
    # bisect the circular balance statement below.
    _line(lines, (130.7, 42.5), (130.7, 88.8))
    _line(lines, (44.15, 130.4), (44.15, 203.0))
    # Likewise, omit the portion crossing the lower black planes.
    _line(lines, (78.99, 88.8), (78.99, 130.22))
    _line(lines, (78.99, 165.05), (78.99, 203.5))

    # Short horizontal rules beneath the numbered and text blocks.
    _line(lines, (68.7, 24.2), (75.7, 24.2))
    _line(lines, (18.5, 108.1), (22.6, 108.1))
    _line(lines, (18.5, 138.7), (21.0, 138.7))
    _line(lines, (92.2, 175.5), (95.0, 175.5))
    _line(lines, (27.7, 190.5), (30.0, 190.5))

    # Crosshair marks.
    _plus(lines, 14.3, 11.8, 1.45)
    _plus(lines, 36.4, 49.3, 2.75)
    _plus(lines, 130.7, 15.8, 2.1)
    _plus(lines, 121.0, 109.3, 2.2)
    _plus(lines, 61.8, 183.1, 2.25)

    # Fine quarter-circle contour left visible inside the central module.
    arc = [
        (
            CENTRAL_CROSS[0] - CENTRAL_RADIUS_X * math.cos(math.radians(angle)),
            CENTRAL_CROSS[1] + CENTRAL_RADIUS_Y * math.sin(math.radians(angle)),
        )
        for angle in np.linspace(0.0, 90.0, 65)
    ]
    _polyline(lines, arc)

    # Elements legend: symbols are outlines here and become dense through fill.
    _circle(lines, 19.6, 142.6, 1.04, samples=36)
    _rect(lines, 18.65, 151.9, 20.55, 153.9)
    # VOLUME is a quarter-disc outline; its interior is filled with other solids.
    quarter = [(18.75, 160.4), (18.75, 157.8)]
    quarter.extend(
        (
            18.75 + 2.55 * math.cos(math.radians(angle)),
            160.35 + 2.55 * math.sin(math.radians(angle)),
        )
        for angle in np.linspace(-90.0, 0.0, 25)
    )
    quarter.append((18.75, 160.4))
    _polyline(lines, quarter, closed=True)

    # Bottom-right four-by-four point grid; four points are hollow.
    hollow = {(1, 1), (2, 1), (2, 2), (3, 2), (3, 3)}
    for row in range(4):
        for column in range(4):
            if (column, row) in hollow:
                cx = 114.8 + 4.4 * column
                cy = 185.5 + 3.8 * row
                _circle(lines, cx, cy, 0.44, samples=28)
                continue

    return _pack(lines)


@primitive
def poster_linework() -> tuple[np.ndarray, np.ndarray]:
    """Return measured rules, registration marks, and thin contours."""

    coords, offsets = _poster_linework_data()
    return coords.copy(), offsets.copy()


def _combine(geometries: list | tuple):
    if not geometries:
        raise ValueError("geometry list must not be empty")
    result = geometries[0]
    for geometry in geometries[1:]:
        result = result + geometry
    return result


def _text(
    text: str,
    x: float,
    y: float,
    scale: float,
    *,
    font=REGULAR_FONT,
    align: str = "left",
    spacing: float = 0.0,
    line_height: float = 1.18,
    suffix: str = "",
):
    if isinstance(font, tuple):
        font_path, font_index = font
    else:
        font_path, font_index = font, 0
    summary = " ".join(text.split())[:28]
    return G(name=f"Text / {summary}").text(
        text=text,
        font=font_path,
        font_index=font_index,
        text_align=align,
        letter_spacing_em=spacing,
        line_height=line_height,
        quality=0.38,
        center=(x, y, 0.0),
        scale=scale,
        key="form-follows-focus-type",
        instance_key=f"{x:.3f}:{y:.3f}:{scale:.3f}:{suffix}:{text}",
    )


def _rotated_text(
    text: str,
    x: float,
    y: float,
    scale: float,
    angle: float,
    *,
    font=REGULAR_FONT,
    align: str = "left",
    spacing: float = 0.0,
    line_height: float = 1.18,
    suffix: str = "",
):
    geometry = _text(
        text,
        0.0,
        0.0,
        scale,
        font=font,
        align=align,
        spacing=spacing,
        line_height=line_height,
        suffix=f"rot-{angle}-{suffix}",
    )
    return E(name=f"Rotate type / {text[:18]}").affine(
        auto_center=False,
        pivot=(0.0, 0.0, 0.0),
        rotation=(0.0, 0.0, angle),
        scale=(1.0, 1.0, 1.0),
        delta=(x, y, 0.0),
        key="form-follows-focus-type-rotation",
        instance_key=f"{x:.3f}:{y:.3f}:{angle:.3f}:{suffix}:{text}",
    )(geometry)


def _arc_text(
    text: str,
    cx: float,
    cy: float,
    radius: float,
    start_degrees: float,
    end_degrees: float,
    scale: float,
    *,
    tangent_offset: float,
    font=MEDIUM_FONT,
    suffix: str,
    word_gap: float = 1.65,
) -> list:
    visible = [(i, char) for i, char in enumerate(text) if char != " "]
    if not visible:
        return []
    weights = [
        word_gap if right_i - left_i > 1 else 1.0
        for (left_i, _), (right_i, _) in zip(visible, visible[1:])
    ]
    positions = [0.0]
    for weight in weights:
        positions.append(positions[-1] + weight)
    total = positions[-1] if positions else 0.0
    geometries = []
    for visible_index, ((source_index, character), position) in enumerate(
        zip(visible, positions)
    ):
        progress = 0.5 if total == 0.0 else position / total
        angle = start_degrees + (end_degrees - start_degrees) * progress
        radians = math.radians(angle)
        x = cx + radius * math.cos(radians)
        y = cy + radius * math.sin(radians) - scale * 0.42
        glyph = _text(
            character,
            x,
            y,
            scale,
            font=font,
            align="center",
            suffix=f"arc-{suffix}-{source_index}",
        )
        geometries.append(
            E(name=f"Arc glyph / {character}").rotate(
                auto_center=True,
                rotation=(0.0, 0.0, angle + tangent_offset),
                key="form-follows-focus-arc-type-rotation",
                instance_key=f"{suffix}:{visible_index}:{angle:.3f}",
            )(glyph)
        )
    return geometries


def _body_typography():
    items = [
        _text(
            "STUDY\nIN FORM\nAND ORDER",
            18.2,
            13.6,
            1.48,
            font=MEDIUM_FONT,
            spacing=0.08,
            line_height=1.62,
            suffix="top-left",
        ),
        _text(
            "N°",
            68.7,
            13.8,
            1.75,
            font=MEDIUM_FONT,
            spacing=0.04,
            suffix="number-label",
        ),
        _text("27", 68.7, 17.0, 6.10, font=REGULAR_FONT, spacing=0.00, suffix="number"),
        _text(
            "STRUCTURE\nBALANCE\nRHYTHM\nCLARITY",
            106.2,
            13.9,
            1.16,
            font=MEDIUM_FONT,
            spacing=0.16,
            line_height=1.60,
            suffix="top-right",
        ),
        _text(
            "FORM\nFOLLOWS\nFOCUS",
            18.4,
            79.7,
            6.88,
            font=REGULAR_FONT,
            spacing=-0.055,
            line_height=1.21,
            suffix="headline",
        ),
        _text(
            "A VISUAL STUDY\nIN SYSTEMS,\nSHAPE AND\nHARMONY.",
            18.4,
            112.0,
            1.40,
            font=MEDIUM_FONT,
            spacing=0.17,
            line_height=1.48,
            suffix="intro",
        ),
        _text(
            "ELEMENTS",
            18.4,
            135.9,
            1.05,
            font=MEDIUM_FONT,
            spacing=0.12,
            suffix="elements",
        ),
        _text(
            "POINT", 23.6, 141.7, 1.05, font=MEDIUM_FONT, spacing=0.11, suffix="point"
        ),
        _text("LINE", 23.6, 147.0, 1.05, font=MEDIUM_FONT, spacing=0.11, suffix="line"),
        _text(
            "PLANE", 23.6, 152.3, 1.05, font=MEDIUM_FONT, spacing=0.11, suffix="plane"
        ),
        _text(
            "VOLUME", 23.6, 157.5, 1.05, font=MEDIUM_FONT, spacing=0.11, suffix="volume"
        ),
        _text(
            "DIRECTION",
            27.2,
            187.7,
            1.00,
            font=MEDIUM_FONT,
            spacing=0.10,
            suffix="direction",
        ),
        _text("INFO", 92.3, 173.6, 1.00, font=MEDIUM_FONT, spacing=0.12, suffix="info"),
        _text(
            "DATE\n25 / 06 / 2025\n\nLOCATION\nUNKNOWN\n\nMEDIUM\nPRINT",
            92.3,
            179.0,
            1.00,
            font=MEDIUM_FONT,
            spacing=0.06,
            line_height=1.45,
            suffix="metadata",
        ),
        _text(
            "04",
            126.50,
            137.00,
            4.70,
            font=REGULAR_FONT,
            spacing=0.08,
            suffix="zero-four",
        ),
        _text(
            "COMPOSE\nREDUCE\nREFINE\nREPEAT",
            126.2,
            149.0,
            1.14,
            font=MEDIUM_FONT,
            spacing=0.10,
            line_height=1.65,
            suffix="verbs",
        ),
        _rotated_text(
            "DESIGN NOTE",
            133.9,
            42.2,
            1.45,
            90.0,
            font=MEDIUM_FONT,
            spacing=0.13,
            suffix="design-note",
        ),
        _rotated_text(
            "System creates freedom.\nClarity creates impact.",
            136.5,
            61.6,
            0.86,
            90.0,
            font=REGULAR_FONT,
            line_height=1.30,
            suffix="design-body",
        ),
        _rotated_text(
            "GRID / 08",
            11.0,
            195.2,
            1.27,
            -90.0,
            font=MEDIUM_FONT,
            spacing=0.15,
            suffix="grid",
        ),
        _rotated_text(
            "STUDY IN SYSTEMS   —   001",
            139.7,
            199.0,
            1.26,
            -90.0,
            font=MEDIUM_FONT,
            spacing=0.13,
            suffix="study",
        ),
    ]
    items.extend(
        _arc_text(
            "GOOD DESIGN IS · DESIGN · AS LITTLE ·",
            36.4,
            49.3,
            14.7,
            208.0,
            550.0,
            2.05,
            tangent_offset=90.0,
            font=MEDIUM_FONT,
            suffix="design-ring",
            word_gap=1.65,
        )
    )
    # In the reference, AS POSSIBLE sits on a second, wider shallow lower arc.
    items.extend(
        _arc_text(
            "AS POSSIBLE",
            36.4,
            49.3,
            18.6,
            129.0,
            57.0,
            2.15,
            tangent_offset=-90.0,
            font=MEDIUM_FONT,
            suffix="as-possible",
            word_gap=1.65,
        )
    )
    items.extend(
        _arc_text(
            "BALANCE IS NOT SOMETHING YOU FIND · IT'S SOMETHING · IT'S",
            121.0,
            109.3,
            14.6,
            160.0,
            520.0,
            1.30,
            tangent_offset=90.0,
            font=MEDIUM_FONT,
            suffix="balance",
            word_gap=1.8,
        )
    )
    items.extend(
        _arc_text(
            "SIMPLE · INTENTIONAL · TIMELESS · RELEVANT ·",
            61.8,
            183.1,
            13.6,
            240.0,
            600.0,
            1.42,
            tangent_offset=90.0,
            font=MEDIUM_FONT,
            suffix="qualities",
            word_gap=1.8,
        )
    )
    return _combine(items)


def _solid_geometry():
    right_semicircle = E(name="Right semicircle").boolean(
        mode="intersection", key="form-right-semicircle"
    )(
        G.circle(
            radius=29.47,
            segments=192,
            center=(97.52, 74.23, 0.0),
            key="form-semicircle-circle",
        ),
        G.rect(
            width=31.0,
            height=43.97,
            center=(111.66, 66.935, 0.0),
            key="form-semicircle-mask",
        ),
    )
    right_semicircle = E(name="White vertical channel / semicircle").boolean(
        mode="difference", key="form-semicircle-white-channel"
    )(
        right_semicircle,
        G.rect(
            width=0.34,
            height=44.5,
            center=(102.05, 66.9, 0.0),
            key="form-semicircle-channel-mask",
        ),
    )

    central_blocks = E(name="Central black L / union").boolean(
        mode="union", key="form-central-block-union"
    )(
        G.rect(
            width=23.06,
            height=20.83,
            center=(90.52, 120.175, 0.0),
            key="form-solid",
            instance_key="middle-block",
        ),
        G.rect(
            width=32.38,
            height=34.29,
            center=(85.86, 147.735, 0.0),
            key="form-solid",
            instance_key="lower-blocks",
        ),
    )
    central_blocks = E(name="White horizontal channel / central blocks").boolean(
        mode="difference", key="form-central-horizontal-channel"
    )(
        central_blocks,
        G.rect(
            width=32.8,
            height=0.34,
            center=(85.86, 130.4, 0.0),
            key="form-central-horizontal-channel-mask",
        ),
    )
    central_blocks = E(name="White vertical channel / central blocks").boolean(
        mode="difference", key="form-central-vertical-channel"
    )(
        central_blocks,
        G.rect(
            width=0.34,
            height=35.0,
            center=(78.99, 147.65, 0.0),
            key="form-central-vertical-channel-mask",
        ),
    )

    shapes = [
        # Filled registration points at construction-line intersections.
        *(
            G.circle(
                radius=radius,
                segments=32,
                center=(x, y, 0.0),
                key="form-registration-dot",
                instance_key=index,
            )
            for index, (x, y, radius) in enumerate(
                (
                    (14.3, TOP_CIRCLE_TANGENT_Y, 0.38),
                    (130.7, 42.5, 0.38),
                    (130.7, 88.8, 0.38),
                    (130.50, 130.51, 0.44),
                    (14.3, 195.1, 0.38),
                )
            )
        ),
        # Reference-weight underline beneath 04.  These small display marks
        # belong to the printed ink, rather than the hairline guide network.
        G.rect(
            width=6.71,
            height=0.34,
            center=(129.77, 143.23, 0.0),
            key="form-display-mark",
            instance_key="zero-four-underline",
        ),
        # Reference-weight direction arrow.
        G.rect(
            width=2.05,
            height=0.28,
            center=(30.555, 194.91, 0.0),
            key="form-display-mark",
            instance_key="direction-arrow-head-horizontal",
        ),
        G.rect(
            width=0.28,
            height=1.97,
            center=(31.58, 195.895, 0.0),
            key="form-display-mark",
            instance_key="direction-arrow-head-vertical",
        ),
        G.rect(
            width=4.23,
            height=0.28,
            angle=-44.2,
            center=(30.065, 196.385, 0.0),
            key="form-display-mark",
            instance_key="direction-arrow-shaft",
        ),
        # Top circle.
        G.circle(
            radius=TOP_CIRCLE_RADIUS,
            segments=192,
            center=(*TOP_CIRCLE_CENTER, 0.0),
            key="form-solid",
            instance_key="top-circle",
        ),
        # Right half-disc with a paper-white registration channel.
        right_semicircle,
        # Upper-left quarter-disc in the central four-square module.
        E(name="Central quarter disc").boolean(
            mode="intersection", key="form-central-quarter"
        )(
            G.ellipse(
                radius_x=CENTRAL_RADIUS_X,
                radius_y=CENTRAL_RADIUS_Y,
                segments=128,
                center=(*CENTRAL_CROSS, 0.0),
                key="form-quarter-ellipse",
            ),
            G.rect(
                width=CENTRAL_RADIUS_X,
                height=CENTRAL_RADIUS_Y,
                center=(
                    CENTRAL_CROSS[0] - CENTRAL_RADIUS_X / 2.0,
                    CENTRAL_CROSS[1] - CENTRAL_RADIUS_Y / 2.0,
                    0.0,
                ),
                key="form-quarter-mask",
            ),
        ),
        # Black modular blocks with paper-white registration channels.
        central_blocks,
        # Elements legend solid symbols.
        G.circle(
            radius=0.98,
            segments=36,
            center=(19.6, 142.6, 0.0),
            key="form-solid",
            instance_key="legend-dot",
        ),
        G.rect(
            width=0.55,
            height=2.7,
            center=(19.65, 147.8, 0.0),
            key="form-solid",
            instance_key="legend-line",
        ),
        G.rect(
            width=2.0,
            height=2.0,
            center=(19.6, 153.0, 0.0),
            key="form-solid",
            instance_key="legend-plane",
        ),
    ]

    # Nine narrow vertical bars beneath the circle.
    upper_bar_bounds = (
        (67.05, 68.03),
        (69.50, 70.48),
        (71.96, 72.94),
        (74.41, 75.40),
        (76.86, 77.84),
        (79.31, 80.46),
        (81.93, 82.91),
        (84.38, 85.36),
        (86.84, 87.82),
    )
    for index, (x0, x1) in enumerate(upper_bar_bounds):
        shapes.append(
            G.rect(
                width=x1 - x0,
                height=25.26,
                center=((x0 + x1) / 2.0, 76.29, 0.0),
                key="form-upper-bars",
                instance_key=index,
            )
        )

    # Six horizontal bars below the long rule.
    lower_bar_bounds = (
        (130.59, 132.56),
        (134.70, 136.34),
        (138.30, 139.95),
        (142.08, 143.72),
        (145.85, 147.49),
        (149.63, 151.27),
    )
    for index, (y0, y1) in enumerate(lower_bar_bounds):
        shapes.append(
            G.rect(
                width=23.05,
                height=y1 - y0,
                center=(55.675, (y0 + y1) / 2.0, 0.0),
                key="form-lower-bars",
                instance_key=index,
            )
        )

    # Filled members of the dot matrix; three intentionally stay hollow.
    hollow = {(1, 1), (2, 1), (2, 2), (3, 2), (3, 3)}
    for row in range(4):
        for column in range(4):
            if (column, row) in hollow:
                continue
            shapes.append(
                G.circle(
                    radius=0.42,
                    segments=28,
                    center=(114.8 + 4.4 * column, 185.5 + 3.8 * row, 0.0),
                    key="form-dot-matrix",
                    instance_key=f"{column}:{row}",
                )
            )

    # Fill the quarter-disc legend symbol.
    shapes.append(
        E(name="Legend volume quarter").boolean(
            mode="intersection", key="form-legend-quarter"
        )(
            G.circle(
                radius=2.55,
                segments=48,
                center=(18.75, 160.35, 0.0),
                key="form-legend-circle",
            ),
            G.rect(
                width=2.55,
                height=2.55,
                center=(20.025, 159.075, 0.0),
                key="form-legend-mask",
            ),
        )
    )
    return _combine(shapes)


def draw(t: float):
    del t
    fit = E(name="Reference photograph / uniform A5 fit").affine(
        auto_center=False,
        pivot=(0.0, 0.0, 0.0),
        scale=(LAYOUT_FIT_SCALE, LAYOUT_FIT_SCALE, 1.0),
        delta=(LAYOUT_OFFSET[0], LAYOUT_OFFSET[1], 0.0),
        key="form-follows-focus-reference-fit",
    )

    typography = E(name="Poster typography / printed black fill").fill(
        angle_sets=3,
        angle=30.0,
        density=FILL_DENSITIES["typography"],
        min_spacing=FILL_MIN_SPACINGS["typography"],
        remove_boundary=False,
        key="form-follows-focus-type-fill",
    )(_body_typography())
    solids = E(name="Geometric planes / parallel-line fill").fill(
        angle_sets=1,
        angle=45.0,
        density=FILL_DENSITIES["solid"],
        min_spacing=FILL_MIN_SPACINGS["solid"],
        remove_boundary=False,
        key="form-follows-focus-solid-fill",
    )(_solid_geometry())

    geometry = (
        fit(G(name="Fine poster construction").poster_linework()),
        fit(typography),
        fit(solids),
    )
    return L(name="Form follows focus / single ink layer").layer(
        geometry,
        color=LINE_COLORS["ink"],
        thickness=LINE_THICKNESS,
    )


if __name__ == "__main__":
    run(
        draw,
        run_id="form_follows_focus_20260812",
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
