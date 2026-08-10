from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from grafix import E, G, L, primitive, run


# Reference-reproduction adjustment block.  The 150 x 200 source board keeps
# the attached 3:4 composition intact; it is uniformly contained on A5 rather
# than being stretched.  Crop is deliberately zero so every registration mark
# in the source remains visible.
CANVAS_SIZE = (148, 210)
BACKGROUND_COLOR = (243 / 255, 242 / 255, 240 / 255)
LINE_THICKNESS = 0.001
SEED = 240517
LINE_COLORS = {"ink": (8 / 255, 8 / 255, 8 / 255)}
FILL_DENSITIES = {
    "typography": 1000.0,
    "solid": 1000.0,
}
FILL_MIN_SPACINGS = {
    "typography": 0.05,
    "solid": 0.05,
}
REFERENCE_SIZE = (150.0, 200.0)
LAYOUT_FIT_SCALE = min(
    CANVAS_SIZE[0] / REFERENCE_SIZE[0],
    CANVAS_SIZE[1] / REFERENCE_SIZE[1],
)
LAYOUT_CROP = (0.0, 0.0, 0.0, 0.0)
LAYOUT_OFFSET = (
    (CANVAS_SIZE[0] - REFERENCE_SIZE[0] * LAYOUT_FIT_SCALE) / 2.0,
    (CANVAS_SIZE[1] - REFERENCE_SIZE[1] * LAYOUT_FIT_SCALE) / 2.0,
)
DISPLAY_FONT = "/System/Library/Fonts/HelveticaNeue.ttc"
MONO_FONT = "/System/Library/Fonts/SFNSMono.ttf"
MONO_BOLD_FONT = "/System/Library/Fonts/Menlo.ttc"
TITLE_FONT_INDEX = 1
DISPLAY_REGULAR_INDEX = 0
DISPLAY_BOLD_INDEX = 1
MONO_REGULAR_INDEX = 0
MONO_BOLD_INDEX = 1


Point = tuple[float, float]
Polyline = list[Point]


def _pack(polylines: list[Polyline]) -> tuple[np.ndarray, np.ndarray]:
    """Pack explicit report linework into Grafix primitive buffers."""

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


def _ellipse(
    lines: list[Polyline], cx: float, cy: float, rx: float, ry: float, *, samples: int = 72
) -> None:
    points = [
        (
            cx + rx * math.cos(math.tau * index / samples),
            cy + ry * math.sin(math.tau * index / samples),
        )
        for index in range(samples)
    ]
    _polyline(lines, points, closed=True)


def _dotted_line(
    lines: list[Polyline], x0: float, x1: float, y: float, *, pitch: float = 0.86
) -> None:
    count = max(1, int((x1 - x0) / pitch))
    for index in range(count + 1):
        x = x0 + (x1 - x0) * index / count
        _line(lines, (x, y - 0.035), (x, y + 0.035))


def _dashed_circle(
    lines: list[Polyline], cx: float, cy: float, radius: float, *, dashes: int = 34
) -> None:
    for index in range(dashes):
        start = math.tau * index / dashes
        end = start + math.tau * 0.54 / dashes
        points = [
            (
                cx + radius * math.cos(start + (end - start) * step / 4.0),
                cy + radius * math.sin(start + (end - start) * step / 4.0),
            )
            for step in range(5)
        ]
        _polyline(lines, points)


def _arrow(lines: list[Polyline], x0: float, x1: float, y: float) -> None:
    _line(lines, (x0, y), (x1, y))
    _line(lines, (x1, y), (x1 - 0.82, y - 0.68))
    _line(lines, (x1, y), (x1 - 0.82, y + 0.68))


def _tangent_arrowhead(
    lines: list[Polyline], cx: float, cy: float, radius: float, angle: float
) -> None:
    tip = (cx + radius * math.cos(angle), cy + radius * math.sin(angle))
    tangent = angle + math.pi / 2.0
    back = (tip[0] - 1.45 * math.cos(tangent), tip[1] - 1.45 * math.sin(tangent))
    normal = tangent + math.pi / 2.0
    _line(
        lines,
        tip,
        (back[0] + 0.62 * math.cos(normal), back[1] + 0.62 * math.sin(normal)),
    )
    _line(
        lines,
        tip,
        (back[0] - 0.62 * math.cos(normal), back[1] - 0.62 * math.sin(normal)),
    )


@lru_cache(maxsize=1)
def _report_linework_data() -> tuple[np.ndarray, np.ndarray]:
    lines: list[Polyline] = []

    # Registration marks and the five horizontal information bands.
    for x0, x1 in ((4.5, 6.4), (144.0, 146.0)):
        _line(lines, (x0, 5.45), (x1, 5.45))
        _line(lines, (x0, 194.55), (x1, 194.55))
    for y in (38.9, 59.7, 126.1, 155.5, 181.7):
        _line(lines, (4.5, y), (146.0, y))

    # Small underlines below each section label.
    for y in (14.05, 43.20, 64.20, 77.95, 128.75, 158.15, 184.85):
        _line(lines, (6.55, y), (8.35, y))

    # Summary leaders, legend, and divider.
    _dotted_line(lines, 33.4, 74.7, 46.9)
    _dotted_line(lines, 35.8, 74.7, 53.35)
    _line(lines, (117.4, 41.05), (117.4, 57.55))
    _circle(lines, 121.75, 44.4, 0.78, samples=36)
    _circle(lines, 121.75, 52.35, 0.88, samples=40)
    _circle(lines, 121.75, 52.35, 0.39, samples=32)

    # Transition arrows.
    for x0, x1 in ((28.9, 31.6), (56.5, 59.3), (84.5, 87.3), (113.8, 116.6)):
        _arrow(lines, x0, x1, 104.45)

    # 01 FRAGMENTED: separated open nodes and isolated strokes.
    fragmented_open = (
        (17.35, 97.65, 1.52),
        (9.05, 103.85, 1.48),
        (23.45, 102.65, 1.48),
        (19.45, 106.65, 1.30),
        (17.15, 111.00, 1.50),
    )
    for cx, cy, radius in fragmented_open:
        _circle(lines, cx, cy, radius, samples=52)
    for a, b in (
        ((10.7, 98.0), (12.25, 101.1)),
        ((19.4, 98.7), (21.7, 96.55)),
        ((11.35, 108.65), (9.0, 105.1)),
        ((22.95, 108.75), (23.0, 112.15)),
        ((11.7, 108.0), (15.45, 107.35)),
    ):
        _line(lines, a, b)

    # 02 AWARE: an incomplete-looking dashed circumference.
    _dashed_circle(lines, 43.6, 105.3, 8.7, dashes=32)

    # 03 ACCOUNTABLE: a fine orbit and two directional arrowheads.
    _circle(lines, 72.35, 105.1, 8.65, samples=108)
    _tangent_arrowhead(lines, 72.35, 105.1, 8.65, -1.35)
    _tangent_arrowhead(lines, 72.35, 105.1, 8.65, 1.85)

    # 04 CONNECTED: tightly nested paths make one clean, heavier plotted bond.
    for index in range(18):
        radius = 8.08 + 0.50 * index / 17.0
        _circle(lines, 100.75, 104.9, radius, samples=120)

    # 05 INTEGRATED: twelve spokes terminating in open nodes.
    for index in range(12):
        angle = -math.pi / 2.0 + math.tau * index / 12.0
        inner = 2.45
        outer = 9.82
        _line(
            lines,
            (
                131.65 + inner * math.cos(angle),
                105.20 + inner * math.sin(angle),
            ),
            (
                131.65 + outer * math.cos(angle),
                105.20 + outer * math.sin(angle),
            ),
        )
        _circle(
            lines,
            131.65 + outer * math.cos(angle),
            105.20 + outer * math.sin(angle),
            0.68,
            samples=28,
        )

    # Isolation-to-integration baseline.
    _line(lines, (6.6, 119.55), (142.55, 119.55))
    _line(lines, (6.6, 118.55), (6.6, 120.55))
    _line(lines, (142.55, 118.55), (142.55, 120.55))

    # Five layer cells and their diagrams.
    for x in (48.9, 71.9, 96.1, 120.5):
        _line(lines, (x, 129.0), (x, 150.55))
    _circle(lines, 37.5, 139.0, 5.8, samples=96)
    _ellipse(lines, 57.5, 139.0, 4.3, 4.0, samples=86)
    _ellipse(lines, 62.5, 139.0, 4.3, 4.0, samples=86)
    _circle(lines, 82.95, 139.0, 5.75, samples=96)
    for radius in (2.45, 3.65, 4.90, 6.25):
        _dashed_circle(lines, 107.35, 139.0, radius, dashes=max(20, int(radius * 8)))
    for index in range(12):
        angle = math.tau * index / 12.0
        _line(
            lines,
            (
                131.55 + 2.0 * math.cos(angle),
                139.0 + 2.0 * math.sin(angle),
            ),
            (
                131.55 + 6.9 * math.cos(angle),
                139.0 + 6.9 * math.sin(angle),
            ),
        )
    _circle(lines, 131.55, 139.0, 1.85, samples=64)

    # Metrics dotted leaders and numeric axis.
    for y in (162.0, 165.1, 168.2, 171.3, 174.4):
        _dotted_line(lines, 41.6, 61.4, y, pitch=0.72)
        _dotted_line(lines, 123.7, 131.3, y, pitch=0.72)
        _line(lines, (77.95, y), (79.15, y))
        _line(lines, (79.15, y), (78.72, y - 0.35))
        _line(lines, (79.15, y), (78.72, y + 0.35))
    _line(lines, (95.35, 176.55), (123.2, 176.55))
    for x in (95.35, 109.28, 123.2):
        _line(lines, (x, 176.55), (x, 177.25))

    # Footer leaders, coordinates mark, and relational-system icon.
    _dotted_line(lines, 11.2, 27.4, 192.95, pitch=0.62)
    _dotted_line(lines, 11.2, 27.4, 194.75, pitch=0.62)
    _line(lines, (116.0, 195.1), (117.9, 195.1))
    return _pack(lines)


@primitive
def report_linework() -> tuple[np.ndarray, np.ndarray]:
    """Return the monochrome rules, diagrams, leaders, and registration marks."""

    coords, offsets = _report_linework_data()
    return coords.copy(), offsets.copy()


def _text_name(text: str, x: float, y: float) -> str:
    summary = " ".join(text.split())
    if len(summary) > 30:
        summary = f"{summary[:27]}..."
    return f"Text / {summary} @ {x:.1f},{y:.1f}"


def _text_key(text: str, x: float, y: float) -> str:
    return f"{'|'.join(text.splitlines())}:{x:.3f}:{y:.3f}"


def _text(
    text: str,
    x: float,
    y: float,
    scale: float,
    *,
    font: str | None = None,
    font_index: int = MONO_REGULAR_INDEX,
    align: str = "left",
    spacing: float = 0.0,
    line_height: float = 1.18,
):
    resolved_font = font
    resolved_font_index = font_index
    if resolved_font is None:
        if font_index == MONO_BOLD_INDEX:
            resolved_font = MONO_BOLD_FONT
        else:
            resolved_font = MONO_FONT
            resolved_font_index = MONO_REGULAR_INDEX
    return G(name=_text_name(text, x, y)).text(
        text=text,
        font=resolved_font,
        font_index=resolved_font_index,
        text_align=align,
        letter_spacing_em=spacing,
        line_height=line_height,
        quality=0.38,
        center=(x, y, 0.0),
        scale=scale,
        key="reference-report-text",
        instance_key=_text_key(text, x, y),
    )


def _combine(geometries: list | tuple):
    result = geometries[0]
    for geometry in geometries[1:]:
        result = result + geometry
    return result


def _title_geometry():
    return _combine(
        (
            _text(
                "FROM ISOLATION",
                37.1,
                9.30,
                9.15,
                font=DISPLAY_FONT,
                font_index=TITLE_FONT_INDEX,
            ),
            _text(
                "TO INTEGRATION",
                37.1,
                19.08,
                9.27,
                font=DISPLAY_FONT,
                font_index=TITLE_FONT_INDEX,
            ),
        )
    )


def _body_text_geometry():
    text_items = [
        # Top-left and top-right metadata.
        _text("REPORT NO. 24–05–17", 6.55, 6.75, 1.10),
        _text("SYSTEMS OBSERVATORY", 6.55, 8.75, 1.10),
        _text("[ INDEX ]", 6.55, 20.95, 1.12),
        _text("01  STATES\n02  TRANSITION\n03  METRICS\n04  NOTES", 6.55, 24.00, 1.10, line_height=1.55),
        _text("[ OBSERVER ]\nSYSTEMS LAB 7", 127.55, 6.75, 1.08, line_height=1.45),
        _text("[ DATE ]\n17 MAY 2024", 127.55, 13.20, 1.08, line_height=1.45),
        _text("[ VERSION ]\n1.0", 127.55, 19.65, 1.08, line_height=1.45),
        _text(
            "A SYSTEMIC TRANSITION OF THE SELF AND THE COLLECTIVE",
            75.0,
            33.62,
            2.00,
            align="center",
            spacing=0.095,
        ),
        # Summary band.
        _text("SUMMARY INDICATORS", 6.55, 40.80, 1.62, font_index=MONO_BOLD_INDEX),
        _text("LEVEL OF CONNECTION (MEAN)", 6.55, 46.15, 1.25),
        _text("SENSE OF BELONGING (MEAN)", 6.55, 52.60, 1.25),
        _text("Average increase in\nperceived connection\nafter integration.", 94.8, 44.25, 1.06, line_height=1.42),
        _text("Average increase in\nbelonging after\nintegration.", 94.8, 50.75, 1.06, line_height=1.42),
        _text("LOW / DISCONNECTED", 124.8, 43.72, 1.04),
        _text("ACTIVE / ENGAGED", 124.8, 47.72, 1.04),
        _text("INTEGRATED / ALIGNED", 124.8, 51.72, 1.04),
        # Overview.
        _text("OVERVIEW", 6.55, 61.70, 1.58, font_index=MONO_BOLD_INDEX),
        _text(
            "Isolation fragments attention, identity, and purpose. Through awareness, responsibility,\n"
            "and aligned action, the individual shifts from separation toward integration—within self,\n"
            "with others, and with the whole. Integration is not the end point, but a continuing practice\n"
            "of relationship.",
            31.55,
            63.10,
            1.60,
            line_height=1.22,
        ),
        # Transition title and five columns.
        _text("THE TRANSITION", 6.55, 75.78, 1.58, font_index=MONO_BOLD_INDEX),
        _text("01", 10.3, 80.85, 2.55, font=DISPLAY_FONT, font_index=DISPLAY_REGULAR_INDEX),
        _text("FRAGMENTED", 10.3, 84.40, 1.62, font_index=MONO_BOLD_INDEX),
        _text("Scattered attention.\nDisconnected from self\nand others.", 10.3, 86.60, 1.18, line_height=1.35),
        _text("02", 36.8, 80.85, 2.55, font=DISPLAY_FONT, font_index=DISPLAY_REGULAR_INDEX),
        _text("AWARE", 36.8, 84.40, 1.62, font_index=MONO_BOLD_INDEX),
        _text("Notices disconnection.\nBegins observing\npatterns.", 36.8, 86.60, 1.18, line_height=1.35),
        _text("03", 64.2, 80.85, 2.55, font=DISPLAY_FONT, font_index=DISPLAY_REGULAR_INDEX),
        _text("ACCOUNTABLE", 64.2, 84.40, 1.62, font_index=MONO_BOLD_INDEX),
        _text("Takes responsibility.\nChooses direction\nconsciously.", 64.2, 86.60, 1.18, line_height=1.35),
        _text("04", 94.2, 80.85, 2.55, font=DISPLAY_FONT, font_index=DISPLAY_REGULAR_INDEX),
        _text("CONNECTED", 94.2, 84.40, 1.62, font_index=MONO_BOLD_INDEX),
        _text("Builds relationships.\nEngages in reciprocal\nexchange.", 94.2, 86.60, 1.18, line_height=1.35),
        _text("05", 124.3, 80.85, 2.55, font=DISPLAY_FONT, font_index=DISPLAY_REGULAR_INDEX),
        _text("INTEGRATED", 124.3, 84.40, 1.62, font_index=MONO_BOLD_INDEX),
        _text("Aligned within.\nContributing to\nthe whole.", 124.3, 86.60, 1.18, line_height=1.35),
        _text("ISOLATION", 9.0, 121.25, 1.12, font_index=MONO_BOLD_INDEX),
        _text("INTEGRATION", 127.4, 121.25, 1.12, font_index=MONO_BOLD_INDEX),
        # Layer band.
        _text("LAYERS OF INTEGRATION", 6.55, 127.55, 1.52, font_index=MONO_BOLD_INDEX),
        _text("SELF", 37.5, 129.0, 1.27, align="center", font_index=MONO_BOLD_INDEX),
        _text("OTHERS", 60.0, 129.0, 1.27, align="center", font_index=MONO_BOLD_INDEX),
        _text("COMMUNITY", 82.95, 129.0, 1.27, align="center", font_index=MONO_BOLD_INDEX),
        _text("SYSTEMS", 107.35, 129.0, 1.27, align="center", font_index=MONO_BOLD_INDEX),
        _text("WHOLE", 131.55, 129.0, 1.27, align="center", font_index=MONO_BOLD_INDEX),
        _text("Clarity, regulation,\nand self-trust.", 37.5, 147.20, 1.08, align="center", line_height=1.34),
        _text("Empathy, trust,\nand communication.", 60.0, 147.20, 1.08, align="center", line_height=1.34),
        _text("Shared norms,\nsafety, and care.", 82.95, 147.20, 1.08, align="center", line_height=1.34),
        _text("Structures that\nsupport well-being.", 107.35, 147.20, 1.08, align="center", line_height=1.34),
        _text("Participation in\nsomething larger.", 131.55, 147.20, 1.08, align="center", line_height=1.34),
        # Metrics table.
        _text("INTEGRATION METRICS", 6.55, 156.95, 1.52, font_index=MONO_BOLD_INDEX),
        _text("BEFORE", 65.2, 157.35, 1.02, font_index=MONO_BOLD_INDEX),
        _text("AFTER", 87.0, 157.35, 1.02, font_index=MONO_BOLD_INDEX),
        _text("CHANGE", 132.0, 157.35, 1.02, font_index=MONO_BOLD_INDEX),
        _text("SELF-CLARITY\nRELATIONSHIP QUALITY\nCONTRIBUTION\nINNER PEACE\nLIFE SATISFACTION", 29.4, 161.25, 1.23, line_height=2.52),
        _text("0.31\n0.28\n0.19\n0.24\n0.22", 66.0, 161.25, 1.18, align="center", line_height=2.63),
        _text("0.87\n0.81\n0.76\n0.79\n0.84", 88.7, 161.25, 1.18, align="center", line_height=2.63),
        _text("+180%\n+189%\n+300%\n+229%\n+282%", 134.0, 161.25, 1.18, line_height=2.63),
        _text("0.00", 95.35, 177.25, 1.00, align="center"),
        _text("0.50", 109.28, 177.25, 1.00, align="center"),
        _text("1.00", 123.20, 177.25, 1.00, align="center"),
        # Notes and footer.
        _text("NOTES", 6.55, 183.25, 1.52, font_index=MONO_BOLD_INDEX),
        _text(
            "Integration is iterative. Relapse into isolation is a signal, not a failure.\n"
            "Return, realign, and continue.",
            31.55,
            184.25,
            1.18,
            line_height=1.36,
        ),
        _text("[ COORDINATES ]\n37.7749° N, 122.4194° W", 115.8, 184.25, 1.00, line_height=1.42),
        _text("[ SCALE ]\nVARIABLE", 115.8, 189.25, 1.00, line_height=1.42),
        _text("[ A ]", 6.55, 191.95, 1.00),
        _text("01–05", 27.8, 191.95, 1.00),
        _text("[ B ]", 6.55, 193.75, 1.00),
        _text("∞", 27.8, 193.75, 1.00),
        _text("SYSTEMS ARE RELATIONAL", 74.45, 195.75, 0.98, align="center", spacing=0.08),
    ]
    return _combine(text_items)


def _filled_shapes_geometry():
    shapes = [
        # Title underline and legend's active state.
        G.rect(width=78.1, height=0.98, center=(76.75, 31.58, 0.0)),
        G.circle(radius=0.78, segments=48, center=(121.75, 48.35, 0.0)),
        # Transition nodes.
        G.circle(radius=1.72, segments=64, center=(15.0, 103.1, 0.0)),
        G.circle(radius=1.42, segments=52, center=(51.95, 104.65, 0.0)),
        G.circle(radius=1.55, segments=56, center=(65.35, 100.35, 0.0)),
        G.circle(radius=1.55, segments=56, center=(78.25, 110.70, 0.0)),
        G.circle(radius=1.58, segments=56, center=(100.75, 96.70, 0.0)),
        G.circle(radius=1.58, segments=56, center=(109.18, 104.90, 0.0)),
        G.circle(radius=1.58, segments=56, center=(100.75, 113.15, 0.0)),
        G.circle(radius=1.58, segments=56, center=(92.35, 104.90, 0.0)),
        G.circle(radius=2.42, segments=72, center=(131.65, 105.20, 0.0)),
        # Layer nodes.
        G.circle(radius=1.30, segments=48, center=(37.5, 139.0, 0.0)),
        G.circle(radius=1.28, segments=48, center=(107.35, 139.0, 0.0)),
        # Footer relational-system mark.
        G.rect(width=2.35, height=0.38, center=(74.45, 193.45, 0.0)),
        G.circle(radius=0.72, segments=42, center=(73.35, 193.45, 0.0)),
        G.circle(radius=0.72, segments=42, center=(75.55, 193.45, 0.0)),
    ]
    for index in range(6):
        angle = -math.pi / 2.0 + math.tau * index / 6.0
        shapes.append(
            G.circle(
                radius=1.00,
                segments=42,
                center=(
                    82.95 + 5.75 * math.cos(angle),
                    139.0 + 5.75 * math.sin(angle),
                    0.0,
                ),
                key="community-node",
                instance_key=index,
            )
        )
    # Integration metrics, proportional to their AFTER values.
    for index, (y, value) in enumerate(
        zip((162.0, 165.1, 168.2, 171.3, 174.4), (0.87, 0.89, 0.97, 0.92, 0.97))
    ):
        shapes.append(
            G.rect(
                width=28.0 * value,
                height=1.55,
                center=(95.35 + 14.0 * value, y, 0.0),
                key="integration-metric-bar",
                instance_key=index,
            )
        )

    # The black overlap between OTHERS is an actual lens, not another ink color.
    left = G.ellipse(radius_x=4.3, radius_y=4.0, center=(57.5, 139.0, 0.0))
    right = G.ellipse(radius_x=4.3, radius_y=4.0, center=(62.5, 139.0, 0.0))
    lens = E(name="Others / shared overlap").boolean(
        mode="intersection",
        key="others-overlap",
    )(left, right)
    shapes.append(lens)

    return _combine(shapes)


def _summary_card_geometry(text: str, *, center_y: float, key: str):
    card = G(name=f"Summary card / {key}").rect(
        width=17.0,
        height=4.75,
        center=(84.4, center_y, 0.0),
        key="summary-card",
        instance_key=key,
    )
    cutout = _text(
        text,
        84.4,
        center_y - 1.55,
        2.80,
        font=MONO_BOLD_FONT,
        font_index=MONO_BOLD_INDEX,
        align="center",
    )
    filled_card = E(name=f"Summary card / dense black fill / {key}").fill(
        angle_sets=6,
        angle=45.0,
        density=FILL_DENSITIES["solid"],
        min_spacing=FILL_MIN_SPACINGS["solid"],
        remove_boundary=False,
        key="summary-card-fill",
        instance_key=key,
    )(card)
    return E(name=f"Summary card / white type / {key}").clip(
        mode="outside",
        draw_outline=False,
        key="summary-card-cutout",
        instance_key=key,
    )(filled_card, cutout)


def draw(t: float):
    del t
    fit = E(name="3:4 reference / uniform A5 contain").affine(
        auto_center=False,
        pivot=(0.0, 0.0, 0.0),
        scale=(LAYOUT_FIT_SCALE, LAYOUT_FIT_SCALE, 1.0),
        delta=(LAYOUT_OFFSET[0], LAYOUT_OFFSET[1], 0.0),
        key="reference-to-a5-fit",
    )

    title = E(name="Headline / dense black fill").fill(
        angle_sets=6,
        angle=45.0,
        density=FILL_DENSITIES["typography"],
        min_spacing=FILL_MIN_SPACINGS["typography"],
        remove_boundary=False,
        key="headline-fill",
    )(_title_geometry())
    body_text = E(name="Report typography / dense black fill").fill(
        angle_sets=2,
        angle=45.0,
        density=FILL_DENSITIES["typography"],
        min_spacing=FILL_MIN_SPACINGS["typography"],
        remove_boundary=False,
        key="report-type-fill",
    )(_body_text_geometry())
    solid_shapes = E(name="Black nodes, rules, and metric bars / fill").fill(
        angle_sets=6,
        angle=45.0,
        density=FILL_DENSITIES["solid"],
        min_spacing=FILL_MIN_SPACINGS["solid"],
        remove_boundary=False,
        key="solid-shape-fill",
    )(_filled_shapes_geometry())
    summary_cards = tuple(
        _summary_card_geometry(text, center_y=center_y, key=key)
        for text, center_y, key in (
            ("+82.7%", 46.78, "connection"),
            ("+76.1%", 53.25, "belonging"),
        )
    )

    geometry = (
        fit(G(name="Technical report linework").report_linework()),
        fit(title),
        fit(body_text),
        fit(solid_shapes),
        *(fit(card) for card in summary_cards),
    )
    return L(name="Black systems report / single ink layer").layer(
        geometry,
        color=LINE_COLORS["ink"],
        thickness=LINE_THICKNESS,
    )


if __name__ == "__main__":
    run(
        draw,
        run_id="isolation_integration_report_20260810",
        canvas_size=CANVAS_SIZE,
        render_scale=4.0,
        background_color=BACKGROUND_COLOR,
        line_color=LINE_COLORS["ink"],
        line_thickness=LINE_THICKNESS,
        parameter_gui=False,
        parameter_persistence=False,
        midi_port_name=None,
        n_worker=0,
        seed=SEED,
    )
