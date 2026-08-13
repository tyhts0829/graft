from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np

from grafix import E, G, L, primitive, run

# Reference-reproduction adjustment block.  The supplied artwork is a 4:5
# editorial board.  It is uniformly contained on A5 (rather than stretched),
# leaving the unavoidable equal top and bottom margins explicit here.
CANVAS_SIZE = (148, 210)
BACKGROUND_COLOR = (250 / 255, 247 / 255, 242 / 255)
LINE_THICKNESS = 0.001
SEED = 712
LINE_COLORS = {"ink": (31 / 255, 30 / 255, 29 / 255)}
FILL_DENSITIES = {
    "typography": 900.0,
    "solid": 900.0,
}
FILL_MIN_SPACINGS = {
    "typography": 0.04,
    "solid": 0.04,
}
REFERENCE_SIZE = (160.0, 200.0)
LAYOUT_FIT_SCALE = min(
    CANVAS_SIZE[0] / REFERENCE_SIZE[0],
    CANVAS_SIZE[1] / REFERENCE_SIZE[1],
)
LAYOUT_CROP = (0.0, 0.0, 0.0, 0.0)
LAYOUT_OFFSET = (
    (CANVAS_SIZE[0] - REFERENCE_SIZE[0] * LAYOUT_FIT_SCALE) / 2.0,
    (CANVAS_SIZE[1] - REFERENCE_SIZE[1] * LAYOUT_FIT_SCALE) / 2.0,
)

_ROOT = Path(__file__).resolve().parents[2]
SERIF_FONT = str(_ROOT / "data/input/font/Supplemental/Didot.ttc")
SANS_FONT = str(_ROOT / "data/input/font/Avenir Next.ttc")
BODY_FONT = str(_ROOT / "data/input/font/Avenir.ttc")
SERIF_REGULAR = 0
SANS_BOLD = 0
SANS_MEDIUM = 5
SANS_REGULAR = 7
BODY_BOOK = 0
BODY_ROMAN = 11
TEXT_WEIGHT_BOLD_RADIUS = 0.0
TEXT_WEIGHT_SEMIBOLD_RADIUS = 0.0


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
    if closed and points:
        lines.append([*points, points[0]])
    else:
        lines.append(points)


def _circle(
    lines: list[Polyline], cx: float, cy: float, radius: float, *, samples: int = 72
) -> None:
    points = [
        (
            cx + radius * math.cos(math.tau * index / samples),
            cy + radius * math.sin(math.tau * index / samples),
        )
        for index in range(samples)
    ]
    _polyline(lines, points, closed=True)


def _ellipse_points(
    cx: float, cy: float, rx: float, ry: float, *, samples: int = 720
) -> Polyline:
    return [
        (
            cx + rx * math.cos(math.tau * index / samples),
            cy + ry * math.sin(math.tau * index / samples),
        )
        for index in range(samples + 1)
    ]


def _dashed_polyline(
    points: Polyline, *, dash_length: float, gap_length: float
) -> list[Polyline]:
    if len(points) < 2:
        return []
    output: list[Polyline] = []
    current: Polyline = []
    drawing = True
    remaining = dash_length
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


def _dashed_ellipse(
    lines: list[Polyline],
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    *,
    dash_length: float = 0.78,
    gap_length: float = 0.58,
) -> None:
    lines.extend(
        _dashed_polyline(
            _ellipse_points(cx, cy, rx, ry),
            dash_length=dash_length,
            gap_length=gap_length,
        )
    )


def _arrowhead(lines: list[Polyline], tip: Point, left: Point, right: Point) -> None:
    _line(lines, tip, left)
    _line(lines, tip, right)


@lru_cache(maxsize=1)
def _linework_data() -> tuple[np.ndarray, np.ndarray]:
    lines: list[Polyline] = []

    # Page rules and the quiet column structure.
    for x0, y0, x1, y1 in (
        (6.9, 10.15, 153.0, 10.15),
        (7.0, 99.35, 152.8, 99.35),
        (7.0, 166.40, 147.0, 166.40),
        (7.0, 187.55, 152.8, 187.55),
    ):
        _line(lines, (x0, y0), (x1, y1))

    for x in (32.2, 52.3, 70.3, 89.9, 110.2, 130.2):
        _line(lines, (x, 104.0), (x, 161.25))
    for x in (66.1, 119.25):
        _line(lines, (x, 169.3), (x, 184.1))
    for x in (28.9, 78.55, 127.55):
        _line(lines, (x, 188.3), (x, 194.85))

    # Four coaxial cycles in the hero: every loop shares the same left focus.
    _polyline(lines, _ellipse_points(71.25, 51.25, 10.31, 35.31), closed=False)
    _dashed_ellipse(lines, 81.25, 51.25, 20.31, 35.31)
    _polyline(lines, _ellipse_points(91.25, 51.25, 30.31, 35.31), closed=False)
    _dashed_ellipse(lines, 101.25, 51.25, 40.31, 35.31)

    # Stage 01: open possibilities, with the committed node supplied as fill.
    for row in range(3):
        for column in range(3):
            if (row, column) == (2, 2):
                continue
            _circle(lines, 37.45 + 4.20 * column, 114.85 + 4.15 * row, 1.48)

    # Stage 02: awareness is a broken boundary.
    _dashed_ellipse(
        lines,
        60.60,
        119.00,
        6.05,
        6.05,
        dash_length=0.56,
        gap_length=0.46,
    )

    # Stage 03: one claimed position moving around a clear orbit.
    _circle(lines, 80.00, 119.05, 6.10, samples=112)

    # Stage 05: action resolves as a small collision of directional marks.
    for a, b in (
        ((116.15, 113.55), (118.35, 122.90)),
        ((119.15, 112.85), (117.20, 120.30)),
        ((121.25, 113.15), (120.60, 121.05)),
        ((122.45, 115.05), (126.10, 115.45)),
        ((122.00, 118.05), (126.10, 117.72)),
        ((116.20, 117.75), (121.75, 123.55)),
        ((115.10, 120.25), (120.50, 125.00)),
        ((115.35, 124.25), (119.65, 118.75)),
        ((118.15, 121.90), (124.45, 124.15)),
        ((120.85, 121.30), (125.65, 119.60)),
    ):
        _line(lines, a, b)

    # Stage 06: two selves inside one containing boundary.
    _circle(lines, 139.90, 119.05, 6.05, samples=112)

    # The vertical iterative connectors.
    stage_x = (41.65, 60.60, 80.00, 100.05, 120.05, 139.90)
    for x in stage_x:
        _line(lines, (x, 129.90), (x, 140.45))

    # Bottom row: space, ownership, direction, expression, and wholeness.
    _circle(lines, 60.60, 154.85, 6.00, samples=112)
    _circle(lines, 80.00, 154.85, 6.05, samples=112)
    _circle(lines, 100.05, 154.85, 6.05, samples=112)

    sun_center = (120.05, 154.85)
    _circle(lines, *sun_center, 1.38, samples=64)
    sun_lengths = (5.8, 6.0, 5.1, 5.6, 5.3, 6.0, 5.8, 5.2, 6.1, 5.7, 5.0, 5.8, 5.4, 5.8)
    for index, length in enumerate(sun_lengths):
        angle = -math.pi / 2 + math.tau * index / len(sun_lengths)
        _line(
            lines,
            (
                sun_center[0] + 1.45 * math.cos(angle),
                sun_center[1] + 1.45 * math.sin(angle),
            ),
            (
                sun_center[0] + length * math.cos(angle),
                sun_center[1] + length * math.sin(angle),
            ),
        )
    _circle(lines, 139.90, 154.85, 6.05, samples=112)

    # The legend uses two outlines before the two committed filled states.
    _circle(lines, 127.55, 173.85, 0.72, samples=36)
    _circle(lines, 127.55, 176.80, 0.72, samples=36)

    return _pack(lines)


@primitive
def becoming_system_linework() -> tuple[np.ndarray, np.ndarray]:
    """Return the poster rules, cycles, connectors, and open diagram marks."""

    coords, offsets = _linework_data()
    return coords.copy(), offsets.copy()


def _text_name(text: str, x: float, y: float) -> str:
    summary = " ".join(text.split())
    if len(summary) > 34:
        summary = f"{summary[:31]}..."
    return f"Text / {summary} @ {x:.1f},{y:.1f}"


def _text_key(text: str, x: float, y: float) -> str:
    return f"{'|'.join(text.splitlines())}:{x:.3f}:{y:.3f}"


def _text(
    text: str,
    x: float,
    y: float,
    scale: float,
    *,
    font: str = SANS_FONT,
    font_index: int = SANS_REGULAR,
    align: str = "left",
    spacing: float = 0.0,
    line_height: float = 1.2,
    weight_radius: float = 0.0,
):
    geometry = G(name=_text_name(text, x, y)).text(
        text=text,
        font=font,
        font_index=font_index,
        text_align=align,
        letter_spacing_em=spacing,
        line_height=line_height,
        quality=0.44,
        center=(x, y, 0.0),
        scale=scale,
        key="becoming-system-text",
        instance_key=_text_key(text, x, y),
    )
    if weight_radius <= 0.0:
        return geometry
    return E(name=f"Typography weight / {text[:24]}").bold(
        count=10,
        radius=weight_radius,
        seed=SEED,
        key="typography-weight",
        instance_key=_text_key(text, x, y),
    )(geometry)


def _combine(geometries: list | tuple):
    result = geometries[0]
    for geometry in geometries[1:]:
        result = result + geometry
    return result


def _headline_geometry():
    return _combine(
        (
            _text(
                "Becoming",
                7.0,
                67.70,
                8.72,
                font=SERIF_FONT,
                font_index=SERIF_REGULAR,
            ),
            _text(
                "is a System.",
                7.0,
                77.35,
                8.72,
                font=SERIF_FONT,
                font_index=SERIF_REGULAR,
                spacing=-0.065,
            ),
        )
    )


def _body_typography_geometry():
    text: list = [
        _text(
            "RESEARCH NOTE  ·  07",
            7.0,
            6.15,
            1.43,
            font_index=SANS_MEDIUM,
            spacing=0.22,
            weight_radius=TEXT_WEIGHT_SEMIBOLD_RADIUS,
        ),
        _text(
            "SYSTEMS & SELF",
            153.0,
            6.15,
            1.43,
            font_index=SANS_MEDIUM,
            align="right",
            spacing=0.30,
            weight_radius=TEXT_WEIGHT_SEMIBOLD_RADIUS,
        ),
        _text(
            "A WORKING VIEW",
            7.0,
            16.65,
            1.32,
            font_index=SANS_MEDIUM,
            spacing=0.20,
            weight_radius=TEXT_WEIGHT_SEMIBOLD_RADIUS,
        ),
        _text(
            "Identity is not fixed.\n"
            "It is a pattern in motion—\n"
            "shaped by attention,\n"
            "environment, and time.\n"
            "Through cycles of\n"
            "awareness and action,\n"
            "we reorganize,\n"
            "recalibrate,\n"
            "and become.",
            7.0,
            22.55,
            1.78,
            font=SANS_FONT,
            font_index=SANS_REGULAR,
            line_height=1.74,
        ),
        _text(
            "A FIELD GUIDE TO INTERNAL EVOLUTION",
            7.0,
            91.80,
            1.68,
            font_index=SANS_MEDIUM,
            spacing=0.14,
            weight_radius=TEXT_WEIGHT_BOLD_RADIUS,
        ),
        _text(
            "THE CYCLE OF GROWTH",
            7.0,
            103.80,
            1.35,
            font_index=SANS_MEDIUM,
            spacing=0.25,
            weight_radius=TEXT_WEIGHT_SEMIBOLD_RADIUS,
        ),
    ]

    # Left-hand cycle index.
    index_rows = (
        ("01", "Perceive\nNotice what is.", 109.80),
        ("02", "Separate\nCreate space\nfrom automatic\npatterns.", 116.95),
        ("03", "Claim\nTake responsibility\nfor your part.", 127.75),
        ("04", "Commit\nChoose a direction\nwith intention.", 136.50),
        ("05", "Act\nMake it real\nin the world.", 145.35),
        ("06", "Integrate\nAssimilate and\nexpand the self.", 154.35),
    )
    for number, copy, y in index_rows:
        text.append(
            _text(
                number,
                7.0,
                y,
                1.18,
                font_index=SANS_MEDIUM,
                weight_radius=TEXT_WEIGHT_SEMIBOLD_RADIUS,
            )
        )
        text.append(
            _text(
                copy,
                11.05,
                y,
                1.18,
                font=SANS_FONT,
                font_index=SANS_REGULAR,
                line_height=1.24,
            )
        )

    stage_centers = (41.65, 60.60, 80.00, 100.05, 120.05, 139.90)
    stage_names = ("Perceive", "Separate", "Claim", "Commit", "Act", "Integrate")
    outcomes = ("Awareness", "Space", "Ownership", "Direction", "Expression", "Wholeness")
    for index, (x, stage, outcome) in enumerate(
        zip(stage_centers, stage_names, outcomes), start=1
    ):
        text.append(
            _text(
                f"{index:02d}",
                x,
                103.75,
                1.55,
                font_index=SANS_MEDIUM,
                align="center",
                weight_radius=TEXT_WEIGHT_SEMIBOLD_RADIUS,
            )
        )
        text.append(
            _text(
                stage,
                x,
                106.90,
                1.84,
                font_index=SANS_MEDIUM,
                align="center",
                weight_radius=TEXT_WEIGHT_SEMIBOLD_RADIUS,
            )
        )
        text.append(
            _text(
                outcome,
                x,
                143.80,
                1.78,
                font_index=SANS_MEDIUM,
                align="center",
                weight_radius=TEXT_WEIGHT_SEMIBOLD_RADIUS,
            )
        )

    text.extend(
        (
            _text(
                "HOW IT WORKS",
                7.5,
                169.15,
                1.40,
                font_index=SANS_MEDIUM,
                spacing=0.25,
                weight_radius=TEXT_WEIGHT_SEMIBOLD_RADIUS,
            ),
            _text(
                "The system is iterative. Each loop refines perception,\n"
                "clarifies intention, and deepens capability.\n"
                "You do not move in a straight line—\n"
                "you spiral, returning to previous stages\n"
                "with greater clarity and context.",
                7.55,
                172.85,
                1.58,
                font=SANS_FONT,
                font_index=SANS_REGULAR,
                line_height=1.34,
            ),
            _text(
                "PRINCIPLES",
                73.6,
                169.15,
                1.40,
                font_index=SANS_MEDIUM,
                spacing=0.25,
                weight_radius=TEXT_WEIGHT_SEMIBOLD_RADIUS,
            ),
            _text(
                "·  Everything is connected.\n"
                "·  Small actions compound.\n"
                "·  Clarity precedes momentum.\n"
                "·  Integration is the goal.",
                73.55,
                172.90,
                1.55,
                font=SANS_FONT,
                font_index=SANS_REGULAR,
                line_height=1.46,
            ),
            _text("Potential", 131.0, 172.95, 1.37, font=SANS_FONT, font_index=SANS_REGULAR),
            _text("Transition", 131.0, 175.90, 1.37, font=SANS_FONT, font_index=SANS_REGULAR),
            _text("Commitment", 131.0, 178.85, 1.37, font=SANS_FONT, font_index=SANS_REGULAR),
            _text("Integration", 131.0, 181.80, 1.37, font=SANS_FONT, font_index=SANS_REGULAR),
        )
    )

    # Footer metadata.
    footer = (
        ("ISSUE 07 · VOL 02", 7.0, 189.25, 1.22, SANS_MEDIUM, 0.10),
        ("24 MAY 2024", 7.0, 192.15, 1.23, SANS_REGULAR, 0.0),
        ("TOPIC", 33.7, 189.25, 1.22, SANS_MEDIUM, 0.10),
        ("Inner Systems · Growth · Identity", 33.7, 192.15, 1.23, SANS_REGULAR, 0.0),
        ("NOTE", 82.6, 189.25, 1.22, SANS_MEDIUM, 0.10),
        (
            "For reflective use and iterative practice.",
            82.6,
            192.15,
            1.23,
            SANS_REGULAR,
            0.0,
        ),
        ("PLATE 07/12", 131.4, 189.25, 1.22, SANS_MEDIUM, 0.10),
        ("Archive ID: RS-0702", 131.4, 192.15, 1.23, SANS_REGULAR, 0.0),
    )
    for label, x, y, scale, font_index, spacing in footer:
        footer_font = SANS_FONT
        text.append(
            _text(
                label,
                x,
                y,
                scale,
                font=footer_font,
                font_index=font_index,
                spacing=spacing,
            )
        )

    return _combine(text)


def _filled_shapes_geometry():
    shapes: list = []

    def filled_circle(cx: float, cy: float, radius: float, key: str) -> None:
        shapes.append(
            G.circle(
                radius=radius,
                segments=64,
                center=(cx, cy, 0.0),
                key="filled-circle",
                instance_key=key,
            )
        )

    # Hero and right-edge cadence.
    filled_circle(101.25, 58.75, 1.25, "hero-center")
    for index, y in enumerate((17.81, 46.41, 68.75, 87.50, 167.03)):
        filled_circle(151.72, y, 1.25, f"right-edge-{index}")

    # Top stage diagrams.
    filled_circle(45.85, 123.15, 1.45, "perceive-commitment")
    filled_circle(76.15, 114.75, 1.45, "claim-node")
    filled_circle(118.10, 120.60, 1.48, "action-node")
    filled_circle(137.70, 121.30, 3.25, "integration-lower")
    filled_circle(142.20, 116.75, 3.25, "integration-upper")

    outer = G.circle(radius=6.10, segments=128, center=(100.05, 119.05, 0.0))
    inner = G.circle(radius=3.15, segments=112, center=(100.05, 119.05, 0.0))
    shapes.append(
        E(name="Commit / annulus").boolean(
            mode="difference",
            key="commit-annulus",
        )(outer, inner)
    )

    # Connector terminals.
    for column, x in enumerate((41.65, 60.60, 80.00, 100.05, 120.05, 139.90)):
        filled_circle(x, 129.90, 0.43, f"connector-{column}-top")
        filled_circle(x, 140.45, 0.43, f"connector-{column}-bottom")

    # Bottom stage diagrams.
    for row in range(3):
        for column in range(3):
            filled_circle(
                37.45 + 4.20 * column,
                150.70 + 4.15 * row,
                1.42,
                f"awareness-{row}-{column}",
            )
    filled_circle(76.15, 150.65, 1.45, "ownership-first")
    filled_circle(83.85, 159.05, 1.45, "ownership-second")
    filled_circle(100.05, 154.85, 1.72, "direction-center")

    # Wholeness: a loose constellation rather than a mechanical grid.
    wholeness_dots = (
        (136.65, 150.55),
        (140.00, 149.55),
        (143.55, 150.35),
        (134.95, 153.25),
        (138.10, 152.70),
        (141.35, 152.25),
        (144.80, 153.45),
        (136.35, 155.20),
        (139.35, 154.45),
        (142.45, 155.00),
        (145.00, 156.10),
        (134.95, 157.20),
        (138.00, 157.70),
        (141.15, 157.35),
        (143.75, 158.30),
        (136.40, 159.55),
        (139.80, 160.20),
        (142.25, 160.05),
    )
    for index, (x, y) in enumerate(wholeness_dots):
        filled_circle(x, y, 0.30, f"wholeness-{index}")

    # Filled legend states.
    filled_circle(127.55, 179.75, 0.72, "legend-commitment")
    filled_circle(127.55, 182.70, 0.72, "legend-integration")

    # The three orbit arrowheads are compact solid wedges in the reference.
    for index, points in enumerate(
        (
            ((77.65, 113.42), (78.95, 113.08), (78.75, 114.12)),
            ((83.18, 149.70), (81.88, 150.00), (82.62, 150.92)),
            ((76.78, 160.02), (78.08, 159.72), (77.36, 158.80)),
        )
    ):
        shapes.append(
            G.polyline(
                points=points,
                closed=True,
                key="orbit-arrowhead",
                instance_key=index,
            )
        )

    return _combine(shapes)


def draw(t: float):
    del t
    fit = E(name="4:5 reference / uniform A5 contain").affine(
        auto_center=False,
        pivot=(0.0, 0.0, 0.0),
        scale=(LAYOUT_FIT_SCALE, LAYOUT_FIT_SCALE, 1.0),
        delta=(LAYOUT_OFFSET[0], LAYOUT_OFFSET[1], 0.0),
        key="reference-to-a5-fit",
    )

    headline = E(name="Didot headline / dense ink fill").fill(
        angle_sets=6,
        angle=45.0,
        density=FILL_DENSITIES["typography"],
        min_spacing=FILL_MIN_SPACINGS["typography"],
        remove_boundary=False,
        key="headline-fill",
    )(_headline_geometry())
    body_type = E(name="Editorial typography / dense ink fill").fill(
        angle_sets=4,
        angle=45.0,
        density=FILL_DENSITIES["typography"],
        min_spacing=FILL_MIN_SPACINGS["typography"],
        remove_boundary=False,
        key="body-type-fill",
    )(_body_typography_geometry())
    solid_shapes = E(name="Black nodes and diagram masses / fill").fill(
        angle_sets=6,
        angle=45.0,
        density=FILL_DENSITIES["solid"],
        min_spacing=FILL_MIN_SPACINGS["solid"],
        remove_boundary=False,
        key="solid-shapes-fill",
    )(_filled_shapes_geometry())

    geometry = (
        fit(G(name="Editorial rules and systems diagrams").becoming_system_linework()),
        fit(headline),
        fit(body_type),
        fit(solid_shapes),
    )
    return L(name="Becoming is a System / single ink layer").layer(
        geometry,
        color=LINE_COLORS["ink"],
        thickness=LINE_THICKNESS,
    )


if __name__ == "__main__":
    run(
        draw,
        run_id="becoming_is_a_system_20260812",
        canvas_size=CANVAS_SIZE,
        render_scale=3.0,
        background_color=BACKGROUND_COLOR,
        line_color=LINE_COLORS["ink"],
        line_thickness=LINE_THICKNESS,
        parameter_persistence=False,
        midi_port_name=None,
        n_worker=0,
        seed=SEED,
    )
