from __future__ import annotations

import math
import random
from functools import lru_cache

import numpy as np

from grafix import E, G, L, primitive, run

# Reference-reproduction adjustment block.  The supplied photograph is already
# almost exactly A5 portrait, so the layout is fitted uniformly with only the
# sub-millimetre vertical remainder centered; no independent x/y stretch is used.
CANVAS_SIZE = (148, 210)
BACKGROUND_COLOR = (233 / 255, 229 / 255, 218 / 255)
LINE_THICKNESS = 0.001
SEED = 8842
LINE_COLORS = {"ink": (30 / 255, 30 / 255, 28 / 255)}
RULE_HALF_WIDTH = 0.052
FILL_DENSITIES = {
    "typography": 650.0,
    "solid": 800.0,
}
FILL_MIN_SPACINGS = {
    "typography": 0.08,
    "solid": 0.065,
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

HELVETICA_FONT = "/System/Library/Fonts/HelveticaNeue.ttc"
REGULAR_FONT = (HELVETICA_FONT, 0)
MEDIUM_FONT = (HELVETICA_FONT, 10)
BOLD_FONT = (HELVETICA_FONT, 1)
LIGHT_FONT = (HELVETICA_FONT, 7)
THIN_FONT = (HELVETICA_FONT, 12)
MONO_FONT = "/System/Library/Fonts/SFNSMono.ttf"
JP_FONT = "NotoSansJP-Light.ttf"
JP_MEDIUM_FONT = "NotoSansJP-Regular.ttf"
JP_BOLD_FONT = "NotoSansJP-Bold.ttf"

# The photographed main code is a 29-module symbol.  Sampling its module
# centres after the red/cyan registration preserves the visible data pattern,
# rather than substituting a merely QR-like random texture.
MAIN_QR_PATTERN = (
    "########.###.######...#######",
    "#.....###.##..##.###..#.....#",
    "#.###.#..###.##..###..#.###.#",
    "#.###.#.#.###...###.###.###.#",
    "#.###.######.###..##..#.###.#",
    "#.....##.#..########.##.....#",
    "########.####.####.#.########",
    "........#..##.########.......",
    "#.########..#######.#.#######",
    "#############.#.########.###.",
    "############.#######.##.###..",
    "###.####...#########..#.###..",
    "#...#####.##..#..#.###..#.###",
    "#...###..#####.#.....###.###.",
    "..####.#.####.####.#..#...##.",
    "############.....#.#######.##",
    ".###.#####.##.###.##.####..##",
    "#.#.###.#######.##..###.##...",
    "##..###.####.#.#########...##",
    "#..###..#.########.##.#.###.#",
    "#.#.####.#.#.####..#.#####...",
    ".......##########.####..##.##",
    "##########..##..####.##.#.#..",
    "#.....#.####..#####.##..##.#.",
    "#.###.##.####.###..#.####.###",
    "#.###.####.###...##.##..#####",
    "#.###.#######.#..#.#...##....",
    "#.....####.###..#######.###..",
    "########.#...####...#....#...",
)
SMALL_QR_PATTERN = (
    "#######..#.#.#.#..#######",
    "#.....#..#.###.#..#.....#",
    "#.###.#..#.#..##..#.###.#",
    "#.###.#...#.#.#...#.###.#",
    "#.###.#..#.#####..#.###.#",
    "#.....#...#####...#.....#",
    "#######...#.#.#...#######",
    "...........###...........",
    ".#...##.###.####.#.......",
    "###..###############.####",
    "###..##..#........#.##.##",
    "##.##...##.###.#..##..#..",
    "...####.....####.#....##.",
    "..####...########.##.##..",
    "##.##.#############.#####",
    "..####..#.########.#####.",
    ".###.####..##.##.#####.#.",
    "..........######....###..",
    "#######..#.##.....#.#..##",
    "#.....#...#.####....#.##.",
    "#.###.#..##############..",
    "#.###.#..#.######....#.#.",
    "#.###.#...##########.#.#.",
    "#.....#..##.##.##.#######",
    "#######..#..##..###..##..",
)
HEADER_BARCODE_PATTERN = ".#####.#.....##########....#########.##....#.....##.####...##...##..#...###..##.#.##.#..##...###.....##..#..####.....#####.."
TICKET_BARCODE_PATTERN = ".##.###...#####.##....###.####.###..#.###.###.###...#..#####.#..######..#########..#..#####.....#####.....###..#####...#####.######...#...####.#######.....#..####..#####...#.##.##.##..#####.#####...##########...###.###.#...######...###.###......#####..####.#."
SERIAL_BARCODE_PATTERN = ".....##.###.....####..#.....#..####...###..#......###.#..#..#.##.##.##..##........####..##....###..#.###..##..#..#..##.#...#.####...#..#..#####.#....###.##.###.#....###...#..####...##....#....####..##.###..........##...#..###.###..."
BATCH_BARCODE_PATTERN = (
    ".######.....############..##.###...####.......####.....########."
)
CODE_BARCODE_PATTERN = "...#####.....#########....############..####....########.#...##.##..###.####..###...###########.......###...######."


Point = tuple[float, float]
Polyline = list[Point]


def _pack(polylines: list[Polyline]) -> tuple[np.ndarray, np.ndarray]:
    """Pack the explicit poster linework into Grafix primitive buffers."""

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
    lines: list[Polyline], cx: float, cy: float, radius: float, *, samples: int = 64
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


def _ellipse(
    lines: list[Polyline],
    cx: float,
    cy: float,
    radius_x: float,
    radius_y: float,
    *,
    samples: int = 64,
) -> None:
    _polyline(
        lines,
        [
            (
                cx + radius_x * math.cos(math.tau * index / samples),
                cy + radius_y * math.sin(math.tau * index / samples),
            )
            for index in range(samples)
        ],
        closed=True,
    )


def _arc(
    lines: list[Polyline],
    cx: float,
    cy: float,
    radius: float,
    start_degrees: float,
    end_degrees: float,
    *,
    samples: int = 24,
) -> None:
    start = math.radians(start_degrees)
    end = math.radians(end_degrees)
    _polyline(
        lines,
        [
            (
                cx + radius * math.cos(start + (end - start) * index / samples),
                cy + radius * math.sin(start + (end - start) * index / samples),
            )
            for index in range(samples + 1)
        ],
    )


def _bezier(
    lines: list[Polyline],
    p0: Point,
    p1: Point,
    p2: Point,
    p3: Point,
    *,
    samples: int = 40,
) -> None:
    points: list[Point] = []
    for index in range(samples + 1):
        t = index / samples
        u = 1.0 - t
        points.append(
            (
                u**3 * p0[0]
                + 3.0 * u * u * t * p1[0]
                + 3.0 * u * t * t * p2[0]
                + t**3 * p3[0],
                u**3 * p0[1]
                + 3.0 * u * u * t * p1[1]
                + 3.0 * u * t * t * p2[1]
                + t**3 * p3[1],
            )
        )
    _polyline(lines, points)


def _crosshair(
    lines: list[Polyline],
    cx: float,
    cy: float,
    radius: float,
    *,
    doubled: bool = False,
) -> None:
    offsets = (-0.045, 0.045) if doubled else (0.0,)
    for offset in offsets:
        _circle(lines, cx, cy, radius * 0.38 + offset, samples=40)
        _line(lines, (cx - radius, cy + offset), (cx + radius, cy + offset))
        _line(lines, (cx + offset, cy - radius), (cx + offset, cy + radius))


def _corner_frame(
    lines: list[Polyline], cx: float, cy: float, width: float, height: float, arm: float
) -> None:
    x0, x1 = cx - width / 2.0, cx + width / 2.0
    y0, y1 = cy - height / 2.0, cy + height / 2.0
    for x, sx in ((x0, 1.0), (x1, -1.0)):
        for y, sy in ((y0, 1.0), (y1, -1.0)):
            _line(lines, (x, y), (x + sx * arm, y))
            _line(lines, (x, y), (x, y + sy * arm))


def _plus(lines: list[Polyline], cx: float, cy: float, size: float = 0.7) -> None:
    _line(lines, (cx - size, cy), (cx + size, cy))
    _line(lines, (cx, cy - size), (cx, cy + size))


def _solid_rect_lines(
    lines: list[Polyline],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    pitch: float = 0.075,
) -> None:
    """Build a visually solid black patch from plotter-safe parallel strokes."""

    if x1 <= x0 or y1 <= y0:
        return
    count = max(1, int(math.ceil((y1 - y0) / pitch)))
    for index in range(count + 1):
        y = y0 + (y1 - y0) * index / count
        _line(lines, (x0, y), (x1, y))


def _barcode(
    lines: list[Polyline],
    x0: float,
    y0: float,
    width: float,
    height: float,
    *,
    seed: int,
    unit: float = 0.16,
    fill_pitch: float = 0.070,
) -> None:
    rng = random.Random(seed)
    x = x0
    draw_bar = True
    while x < x0 + width:
        multiple = rng.choice((1, 1, 1, 2, 2, 3, 4))
        run = unit * multiple
        x1 = min(x + run, x0 + width)
        if draw_bar:
            count = max(1, int(math.ceil((x1 - x) / fill_pitch)))
            for index in range(count + 1):
                xx = x + (x1 - x) * index / count
                _line(lines, (xx, y0), (xx, y0 + height))
        x = x1
        draw_bar = not draw_bar


def _reference_barcode(
    lines: list[Polyline],
    x0: float,
    y0: float,
    width: float,
    height: float,
    pattern: str,
    *,
    fill_pitch: float,
) -> None:
    """Draw the registered one-pixel bar sequence sampled from the photograph."""

    module = width / len(pattern)
    index = 0
    while index < len(pattern):
        if pattern[index] != "#":
            index += 1
            continue
        end = index + 1
        while end < len(pattern) and pattern[end] == "#":
            end += 1
        left = x0 + index * module
        right = x0 + end * module
        count = max(1, int(math.ceil((right - left) / fill_pitch)))
        for stroke in range(count):
            x = left + (stroke + 0.5) * (right - left) / count
            _line(lines, (x, y0), (x, y0 + height))
        index = end


def _qr_matrix(size: int, seed: int) -> list[list[bool]]:
    grid = [[False for _ in range(size)] for _ in range(size)]
    reserved = [[False for _ in range(size)] for _ in range(size)]

    def finder(left: int, top: int) -> None:
        for yy in range(-1, 8):
            for xx in range(-1, 8):
                gx, gy = left + xx, top + yy
                if not (0 <= gx < size and 0 <= gy < size):
                    continue
                reserved[gy][gx] = True
                if 0 <= xx <= 6 and 0 <= yy <= 6:
                    grid[gy][gx] = (
                        xx in (0, 6) or yy in (0, 6) or (2 <= xx <= 4 and 2 <= yy <= 4)
                    )

    finder(0, 0)
    finder(size - 7, 0)
    finder(0, size - 7)
    for index in range(8, size - 8):
        reserved[6][index] = True
        reserved[index][6] = True
        grid[6][index] = index % 2 == 0
        grid[index][6] = index % 2 == 0

    rng = random.Random(seed)
    for y in range(size):
        for x in range(size):
            if reserved[y][x]:
                continue
            grid[y][x] = ((x * 3 + y * 5 + x * y + seed) % 11 < 6) ^ (
                rng.random() < 0.10
            )
    return grid


def _qr(
    lines: list[Polyline],
    x0: float,
    y0: float,
    side: float,
    *,
    size: int,
    seed: int,
    pattern: tuple[str, ...] | None = None,
    fill_pitch: float = 0.075,
) -> None:
    if pattern is None:
        matrix = _qr_matrix(size, seed)
    else:
        if len(pattern) != size or any(len(row) != size for row in pattern):
            raise ValueError("QR reference pattern must be a square module matrix")
        matrix = [[cell == "#" for cell in row] for row in pattern]
    module = side / size
    for row in range(size):
        for column in range(size):
            if not matrix[row][column]:
                continue
            inset = module * 0.035
            _solid_rect_lines(
                lines,
                x0 + column * module + inset,
                y0 + row * module + inset,
                x0 + (column + 1) * module - inset,
                y0 + (row + 1) * module - inset,
                pitch=min(fill_pitch, module / 5.0),
            )


def _component_icon(lines: list[Polyline], index: int, cx: float, cy: float) -> None:
    if index == 0:
        _rect(lines, cx - 1.45, cy - 1.45, cx + 1.45, cy + 1.45)
        for dx, dy in (
            (-0.72, -0.72),
            (0.0, -0.72),
            (0.72, -0.72),
            (-0.72, 0.0),
            (0.0, 0.0),
            (0.72, 0.72),
            (-0.72, 0.72),
        ):
            _solid_rect_lines(
                lines,
                cx + dx - 0.18,
                cy + dy - 0.18,
                cx + dx + 0.18,
                cy + dy + 0.18,
                pitch=0.06,
            )
    elif index == 1:
        _circle(lines, cx, cy, 1.3, samples=48)
        # Registered icon: a compact yin-yang/information mark, not a plain disc.
        for dx in np.arange(0.02, 1.27, 0.075):
            extent = math.sqrt(max(0.0, 1.25**2 - dx**2))
            x = cx + float(dx)
            hole_y = cy + 0.42
            hole_radius = 0.23
            if abs(dx - 0.36) < hole_radius:
                half = math.sqrt(hole_radius**2 - (dx - 0.36) ** 2)
                _line(lines, (x, cy - extent), (x, hole_y - half))
                _line(lines, (x, hole_y + half), (x, cy + extent))
            else:
                _line(lines, (x, cy - extent), (x, cy + extent))
        for dx in np.arange(-0.82, 0.03, 0.075):
            extent = math.sqrt(max(0.0, 0.72**2 - (dx + 0.10) ** 2))
            x = cx + float(dx)
            _line(lines, (x, cy + 0.42 - extent), (x, cy + 0.42 + extent))
        _circle(lines, cx - 0.36, cy - 0.42, 0.20, samples=20)
    elif index == 2:
        for offset in (-0.13, 0.0, 0.13):
            _line(
                lines,
                (cx - 0.90 + offset, cy - 1.25),
                (cx - 0.90 + offset, cy + 1.25),
            )
            _line(
                lines,
                (cx - 0.90 + offset, cy - 1.25),
                (cx + 0.90 + offset, cy + 1.25),
            )
            _line(
                lines,
                (cx + 0.90 + offset, cy - 1.25),
                (cx + 0.90 + offset, cy + 1.25),
            )
    elif index == 3:
        _corner_frame(lines, cx, cy, 3.0, 3.0, 0.65)
        for dx, dy in ((-1.18, -1.18), (1.18, -1.18), (-1.18, 1.18), (1.18, 1.18)):
            _solid_rect_lines(
                lines,
                cx + dx - 0.28,
                cy + dy - 0.28,
                cx + dx + 0.28,
                cy + dy + 0.28,
                pitch=0.06,
            )
        _solid_rect_lines(lines, cx - 0.24, cy - 0.24, cx + 0.24, cy + 0.24, pitch=0.06)
    else:
        for offset in (-0.12, 0.0, 0.12):
            _line(
                lines, (cx - 1.15 + offset, cy + 1.15), (cx + 1.15 + offset, cy - 1.15)
            )
            _line(
                lines, (cx - 1.25 + offset, cy + 0.45), (cx + 0.45 + offset, cy - 1.25)
            )
        _line(lines, (cx - 0.85, cy - 0.2), (cx + 0.85, cy - 0.2))


@lru_cache(maxsize=1)
def _poster_linework_data() -> tuple[np.ndarray, np.ndarray]:
    lines: list[Polyline] = []

    # Registration marks and compact global symbol.
    _crosshair(lines, 6.15, 6.82, 3.70)
    _crosshair(lines, 6.15, 202.25, 3.15)
    _crosshair(lines, 141.65, 202.25, 3.15)
    # Header globe: the original uses an outer circle, one meridian, a straight
    # equator, and two shallow latitude chords.  A complete horizontal ellipse
    # makes the sides pinch into an eye shape, so keep the latitudes independent.
    globe_x, globe_y, globe_r = 141.80, 6.81, 3.28
    for stroke_offset in (-0.035, 0.035):
        _circle(lines, globe_x, globe_y, globe_r + stroke_offset, samples=84)
        _ellipse(
            lines,
            globe_x,
            globe_y,
            1.58 + stroke_offset,
            globe_r,
            samples=72,
        )
        _line(
            lines,
            (globe_x - globe_r, globe_y + stroke_offset),
            (globe_x + globe_r, globe_y + stroke_offset),
        )
        _bezier(
            lines,
            (globe_x - 2.86, globe_y - 1.58 + stroke_offset),
            (globe_x - 1.10, globe_y - 1.20 + stroke_offset),
            (globe_x + 1.10, globe_y - 1.20 + stroke_offset),
            (globe_x + 2.86, globe_y - 1.58 + stroke_offset),
            samples=28,
        )
        _bezier(
            lines,
            (globe_x - 2.86, globe_y + 1.58 + stroke_offset),
            (globe_x - 1.10, globe_y + 1.20 + stroke_offset),
            (globe_x + 1.10, globe_y + 1.20 + stroke_offset),
            (globe_x + 2.86, globe_y + 1.58 + stroke_offset),
            samples=28,
        )

    # Primary page grid.
    for x0, x1, y in (
        (10.95, 137.74, 12.09),
        (5.70, 143.10, 71.07),
        (5.70, 143.10, 82.92),
        (5.70, 143.10, 125.46),
        (5.70, 143.10, 138.82),
        (5.70, 143.10, 169.18),
        (5.70, 137.74, 196.29),
    ):
        for offset in (-RULE_HALF_WIDTH, RULE_HALF_WIDTH):
            _line(lines, (x0, y + offset), (x1, y + offset))
    for x, y0, y1 in (
        (15.68, 3.35, 82.92),
        (76.51, 3.35, 71.07),
        (103.42, 12.09, 71.07),
        (31.89, 71.07, 82.92),
        (81.38, 70.95, 82.92),
        (95.58, 70.95, 82.92),
        (121.92, 70.95, 82.92),
        (43.55, 82.92, 125.46),
        (87.65, 82.92, 125.46),
        (128.76, 82.92, 125.46),
        (8.15, 125.46, 138.82),
        (54.88, 125.46, 138.82),
        (74.69, 125.46, 138.82),
        (100.43, 125.46, 138.82),
        (121.78, 125.46, 138.82),
        (46.28, 138.82, 169.18),
        (84.38, 138.82, 169.18),
        (113.30, 138.82, 169.18),
        (35.12, 169.18, 196.29),
        (60.58, 175.30, 190.62),
        (89.51, 175.30, 190.62),
        (115.86, 175.30, 190.62),
    ):
        for offset in (-RULE_HALF_WIDTH, RULE_HALF_WIDTH):
            _line(lines, (x + offset, y0), (x + offset, y1))

    # Hairline leaders in the top metadata strip.
    _line(lines, (34.50, 6.70), (40.75, 6.70))
    _reference_barcode(
        lines,
        79.00,
        4.85,
        20.34,
        3.20,
        HEADER_BARCODE_PATTERN,
        fill_pitch=0.093,
    )

    # Circular declaration, small dividers, and publisher stripes.
    _line(lines, (29.45, 41.70), (30.43, 44.63))
    _line(lines, (61.34, 42.82), (60.52, 45.61))
    _plus(lines, 45.35, 47.25, 0.62)
    for x in (24.85, 38.55, 53.20):
        _line(lines, (x, 64.30), (x + 0.85, 64.30))
    for index in range(5):
        x = 67.80 + index * 0.92
        _line(lines, (x, 66.10), (x + 1.10, 64.25))

    # Status dots and crop-bracket motif beside the main QR panel.
    for index in range(6):
        radius = 0.48 if index < 3 else 0.36
        _circle(lines, 143.12, 23.05 + index * 2.92, radius, samples=28)
    _corner_frame(lines, 141.30, 64.75, 5.45, 5.45, 1.25)
    _qr(
        lines,
        107.95,
        46.14,
        14.15,
        size=29,
        seed=8842,
        pattern=MAIN_QR_PATTERN,
        fill_pitch=0.080,
    )

    # Ticket strip barcode and modest separators.
    _reference_barcode(
        lines,
        36.05,
        74.98,
        42.48,
        3.95,
        TICKET_BARCODE_PATTERN,
        fill_pitch=0.068,
    )

    # Central decision diagram: four bowed boundaries and crossed systems paths.
    for cx, cy in ((49.67, 89.25), (81.08, 89.25), (49.67, 119.45), (81.08, 119.45)):
        _plus(lines, cx, cy, 0.58)
    top_left = (53.62, 95.18)
    bottom_left = (53.62, 113.85)
    top_right = (76.48, 95.18)
    bottom_right = (76.48, 113.85)
    _bezier(lines, top_left, (49.8, 101.0), (49.8, 108.0), bottom_left)
    _bezier(lines, top_right, (80.3, 101.0), (80.3, 108.0), bottom_right)
    _bezier(lines, top_left, (59.8, 106.5), (70.3, 102.6), bottom_right)
    _bezier(lines, bottom_left, (59.8, 102.5), (70.3, 106.6), top_right)
    _arc(lines, 65.05, 105.0, 15.6, 53, 72, samples=10)
    _arc(lines, 65.05, 105.0, 15.6, 108, 128, samples=10)

    # The left title uses one continuous rule before the single CJK character.
    _line(lines, (9.72, 103.45), (22.72, 103.45))

    # Component legend symbols.
    for index, cy in enumerate((94.55, 100.55, 106.55, 112.55, 118.55)):
        _component_icon(lines, index, 94.88, cy)

    # Serial / batch / routing row.
    _reference_barcode(
        lines,
        11.45,
        133.88,
        38.05,
        2.45,
        SERIAL_BARCODE_PATTERN,
        fill_pitch=0.093,
    )
    _reference_barcode(
        lines,
        58.55,
        134.35,
        10.50,
        1.85,
        BATCH_BARCODE_PATTERN,
        fill_pitch=0.082,
    )
    _line(lines, (85.846, 133.55), (87.322, 133.55))
    _line(lines, (87.322, 133.55), (86.88, 133.30))
    _line(lines, (87.322, 133.55), (86.88, 133.80))

    # Alignment guide with crop corners, target, and fragmented scan bars.
    _corner_frame(lines, 20.62, 155.18, 23.54, 16.84, 2.25)
    _corner_frame(lines, 20.62, 155.18, 23.66, 16.96, 2.25)
    for offset in (-0.045, 0.045):
        _circle(lines, 18.10, 154.91, 3.95 + offset, samples=64)
        _line(
            lines,
            (18.10 - 4.72, 154.91 + offset),
            (18.10 + 4.72, 154.91 + offset),
        )
        _line(
            lines,
            (18.10 + offset, 154.91 - 5.00),
            (18.10 + offset, 154.91 + 5.00),
        )
    scanlines = (
        (36.14, 40.74, 147.44),
        (37.95, 40.74, 148.10),
        (36.14, 40.57, 148.76),
        (36.14, 40.74, 149.41),
        (36.14, 40.74, 149.90),
        (36.14, 40.90, 150.40),
        (36.14, 38.60, 152.04),
        (37.13, 40.90, 152.53),
        (36.14, 40.90, 153.10),
        (36.14, 40.90, 153.68),
        (37.13, 39.42, 155.48),
        (36.31, 40.74, 155.97),
        (36.14, 40.90, 156.55),
        (37.62, 39.10, 157.12),
        (37.46, 40.74, 158.84),
        (36.47, 40.74, 159.58),
        (36.14, 40.90, 160.32),
        (36.14, 40.90, 161.06),
        (36.14, 40.74, 161.88),
        (36.14, 40.90, 162.54),
        (37.62, 40.74, 163.19),
        (36.14, 40.90, 163.93),
    )
    for x0, x1, y in scanlines:
        _line(lines, (x0, y - 0.045), (x1, y - 0.045))
        _line(lines, (x0, y + 0.045), (x1, y + 0.045))

    # Notes marker, five direction glyphs, code marks, and small barcode.
    _plus(lines, 65.15, 164.35, 0.58)
    for index, x in enumerate((91.15, 95.75, 100.35, 104.95)):
        if index == 0:
            points = [(x - 1.0, 147.1), (x + 0.8, 145.25), (x + 0.8, 147.3)]
        elif index == 1:
            points = [(x - 1.0, 145.25), (x + 0.85, 147.1), (x + 0.85, 145.1)]
        elif index == 2:
            points = [(x - 0.9, 145.1), (x - 0.9, 147.15), (x + 0.95, 145.3)]
        else:
            points = [(x + 0.9, 145.1), (x - 0.9, 145.1), (x + 0.9, 147.15)]
        for offset in (-0.045, 0.045):
            _polyline(lines, [(px, py + offset) for px, py in points])
    for index, x in enumerate((91.2, 94.2, 97.2, 100.2, 103.2, 106.2)):
        if index in (0, 2):
            _polyline(
                lines,
                [
                    (x, 150.78),
                    (x + 0.37, 151.15),
                    (x, 151.52),
                    (x - 0.37, 151.15),
                ],
                closed=True,
            )
        elif index == 1:
            _plus(lines, x, 151.15, 0.38)
        elif index == 4:
            _circle(lines, x, 151.15, 0.35, samples=24)
        else:
            _rect(lines, x - 0.35, 150.80, x + 0.35, 151.50)
    _line(lines, (88.25, 155.35), (109.50, 155.35))
    _reference_barcode(
        lines,
        89.55,
        163.90,
        18.86,
        1.97,
        CODE_BARCODE_PATTERN,
        fill_pitch=0.080,
    )

    # Circular effectiveness seal.
    _arc(lines, 128.0, 154.1, 9.30, 160, 202, samples=14)
    _arc(lines, 128.0, 154.1, 9.30, -20, 22, samples=14)

    # Access-level bar, distribution bullets, sign-off QR and footer details.
    _rect(lines, 65.22, 184.15, 83.30, 186.02)
    _line(lines, (71.25, 184.15), (71.25, 186.02))
    _line(lines, (77.28, 184.15), (77.28, 186.02))
    _circle(lines, 94.55, 184.75, 0.28, samples=18)
    _line(lines, (94.12, 188.30), (94.98, 188.30))
    _rect(lines, 121.734, 178.992, 131.578, 189.164)
    _qr(
        lines,
        122.506,
        179.846,
        8.30,
        size=25,
        seed=3025,
        pattern=SMALL_QR_PATTERN,
        fill_pitch=0.095,
    )
    for index, x in enumerate((85.05, 88.25, 91.45)):
        _circle(lines, x, 202.45, 0.31 - 0.06 * index, samples=20)

    return _pack(lines)


@primitive
def poster_linework() -> tuple[np.ndarray, np.ndarray]:
    """Return the page grid, marks, codes, diagrams, and technical icons."""

    coords, offsets = _poster_linework_data()
    return coords.copy(), offsets.copy()


def _text_name(text: str, x: float, y: float) -> str:
    summary = " ".join(text.split())
    if len(summary) > 28:
        summary = f"{summary[:25]}..."
    return f"Text / {summary} @ {x:.1f},{y:.1f}"


def _text_key(text: str, x: float, y: float, suffix: str = "") -> str:
    return f"{'|'.join(text.splitlines())}:{x:.3f}:{y:.3f}:{suffix}"


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
        resolved_font, resolved_font_index = font
    else:
        resolved_font, resolved_font_index = font, 0
    return G(name=_text_name(text, x, y)).text(
        text=text,
        font=resolved_font,
        font_index=resolved_font_index,
        text_align=align,
        letter_spacing_em=spacing,
        line_height=line_height,
        quality=0.36,
        center=(x, y, 0.0),
        scale=scale,
        key="information-infrastructure-type",
        instance_key=_text_key(text, x, y, suffix),
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
    return E(name=f"Rotated text / {text[:18]}").affine(
        auto_center=False,
        pivot=(0.0, 0.0, 0.0),
        rotation=(0.0, 0.0, angle),
        scale=(1.0, 1.0, 1.0),
        delta=(x, y, 0.0),
        key="information-infrastructure-type-rotation",
        instance_key=_text_key(text, x, y, f"{angle}-{suffix}"),
    )(geometry)


def _rotated_glyph(
    character: str,
    x: float,
    y: float,
    scale: float,
    angle: float,
    *,
    font,
    suffix: str,
):
    geometry = _text(
        character,
        x,
        y,
        scale,
        font=font,
        align="center",
        suffix=f"arc-{suffix}",
    )
    return E(name=f"Arc glyph / {character}").rotate(
        auto_center=True,
        rotation=(0.0, 0.0, angle),
        key="information-arc-glyph-rotation",
        instance_key=_text_key(character, x, y, f"{angle}-{suffix}"),
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
    radius_y: float | None = None,
    tangent_offset: float,
    font=MEDIUM_FONT,
    suffix: str,
    word_gap: float = 2.0,
    follow_ellipse_tangent: bool = False,
    equal_arc_length: bool = False,
) -> list:
    geometries = []
    resolved_radius_y = radius if radius_y is None else radius_y
    if not text:
        return geometries

    placements: list[tuple[int, str, float]] = []
    if equal_arc_length:
        # Equal angle increments bunch glyphs near an ellipse's narrow ends.
        # Invert a dense cumulative-length table so every character slot,
        # including word spaces, advances by the same physical path distance.
        sample_angles = np.linspace(start_degrees, end_degrees, 2049)
        sample_radians = np.radians(sample_angles)
        sample_x = radius * np.cos(sample_radians)
        sample_y = resolved_radius_y * np.sin(sample_radians)
        segment_lengths = np.hypot(np.diff(sample_x), np.diff(sample_y))
        cumulative = np.concatenate(([0.0], np.cumsum(segment_lengths)))
        targets = np.linspace(0.0, cumulative[-1], len(text))
        slot_angles = np.interp(targets, cumulative, sample_angles)
        placements = [
            (index, character, float(slot_angles[index]))
            for index, character in enumerate(text)
            if character != " "
        ]
    else:
        visible = [
            (index, character)
            for index, character in enumerate(text)
            if character != " "
        ]
        if not visible:
            return geometries
        gap_weights = [
            word_gap if right_index - left_index > 1 else 1.0
            for (left_index, _), (right_index, _) in zip(visible, visible[1:])
        ]
        total_weight = sum(gap_weights)
        positions = [0.0]
        for weight in gap_weights:
            positions.append(positions[-1] + weight)
        for (source_index, character), position in zip(visible, positions):
            progress = 0.5 if total_weight == 0.0 else position / total_weight
            angle = start_degrees + (end_degrees - start_degrees) * progress
            placements.append((source_index, character, angle))

    for visible_index, (source_index, character, angle) in enumerate(placements):
        radians = math.radians(angle)
        x = cx + radius * math.cos(radians)
        y = cy + resolved_radius_y * math.sin(radians) - scale * 0.42
        rotation = angle + tangent_offset
        if follow_ellipse_tangent:
            direction = 1.0 if end_degrees >= start_degrees else -1.0
            tangent_x = -radius * math.sin(radians) * direction
            tangent_y = resolved_radius_y * math.cos(radians) * direction
            rotation = math.degrees(math.atan2(tangent_y, tangent_x))
        geometries.append(
            _rotated_glyph(
                character,
                x,
                y,
                scale,
                rotation,
                font=font,
                suffix=f"{suffix}-{source_index}-{visible_index}",
            )
        )
    return geometries


def _combine(geometries: list | tuple):
    result = geometries[0]
    for geometry in geometries[1:]:
        result = result + geometry
    return result


def _headline_text_geometry():
    """Build the two large vertical headlines once, without offset duplicates."""

    return _combine(
        [
            _rotated_text(
                "INFORMATION",
                92.69,
                15.62,
                3.43,
                90.0,
                font=MEDIUM_FONT,
                spacing=0.15,
                suffix="information",
            ),
            _rotated_text(
                "AS INFRASTRUCTURE",
                87.43,
                15.86,
                3.42,
                90.0,
                font=MEDIUM_FONT,
                spacing=0.17,
                suffix="infrastructure",
            ),
        ]
    )


def _body_text_geometry():
    items = [
        # Top metadata and circular manifesto.
        _text("SYS. INFRASTRUCTURE", 17.45, 5.80, 1.40, font=LIGHT_FONT, spacing=0.025),
        _text("LAYOUT EXPERIMENT", 42.10, 5.80, 1.35, font=LIGHT_FONT, spacing=0.025),
        _text("REF. NO.   8842-A", 103.25, 5.80, 1.24, font=LIGHT_FONT, spacing=0.045),
        _rotated_text(
            "SET   •   PRACTICE   •   BUILD",
            10.65,
            21.70,
            1.25,
            90.0,
            font=REGULAR_FONT,
            spacing=0.24,
            suffix="side-practice",
        ),
        _text(
            "CLARITY\nIS A\nKIND OF\nCARE",
            45.68,
            29.80,
            1.98,
            font=MEDIUM_FONT,
            align="center",
            spacing=0.15,
            line_height=1.58,
        ),
        _text("用", 24.15, 63.56, 1.10, font=JP_MEDIUM_FONT),
        _text("設計服務", 29.05, 63.50, 1.10, font=JP_MEDIUM_FONT, spacing=0.08),
        _text("設計改善系統", 40.15, 63.50, 1.10, font=JP_MEDIUM_FONT, spacing=0.06),
        _text("系統支持人", 55.05, 63.50, 1.10, font=JP_MEDIUM_FONT, spacing=0.07),
        # Rotated infrastructure title is filled separately with the solid
        # treatment so its large counters stay clean rather than cross-hatched.
        _rotated_text(
            "（實驗印刷機設備）",
            98.11,
            43.70,
            1.42,
            90.0,
            font=JP_FONT,
            spacing=0.72,
            suffix="jp-vertical",
        ),
        _text("V.1.0", 95.20, 65.55, 1.24, font=LIGHT_FONT),
        # Right identification panel.
        _text("ID. MHV-RST-2025-01", 107.93, 18.92, 2.10, font=THIN_FONT, spacing=0.10),
        _text("CLASS", 107.98, 29.38, 1.38, font=THIN_FONT, spacing=0.03),
        _text("I-UTILITY", 117.85, 29.38, 1.38, font=THIN_FONT, spacing=0.11),
        _text("LEVEL", 107.98, 32.34, 1.38, font=THIN_FONT, spacing=0.03),
        _text("1/3", 117.85, 32.34, 1.38, font=THIN_FONT, spacing=0.11),
        _text("STATUS", 107.98, 35.30, 1.38, font=THIN_FONT, spacing=0.03),
        _text("ACTIVE", 117.85, 35.30, 1.38, font=THIN_FONT, spacing=0.11),
        _text("UPDATED", 107.98, 38.26, 1.38, font=THIN_FONT, spacing=0.03),
        _text("2025.05.18", 117.85, 38.26, 1.38, font=THIN_FONT, spacing=0.11),
        _text(
            "CHK: 19A2    APP: 7Z", 107.82, 64.42, 1.05, font=THIN_FONT, spacing=0.25
        ),
        # Ticket strip.
        _text(
            "PRIORITY\nSTANDARD", 17.88, 74.95, 1.72, font=THIN_FONT, line_height=1.22
        ),
        _text("ROUTE CODE", 83.54, 74.64, 1.35, font=THIN_FONT, spacing=0.05),
        _text("X9-A", 83.54, 76.72, 2.25, font=THIN_FONT, spacing=0.10),
        _text("DESTINATION", 100.32, 75.12, 1.45, font=THIN_FONT, spacing=0.06),
        _text("GLOBAL / NODE", 100.32, 77.37, 1.55, font=THIN_FONT, spacing=0.08),
        _text("FAL 11", 125.21, 75.60, 3.40, font=LIGHT_FONT),
        _text(
            "ZONE\n3025",
            141.39,
            75.76,
            1.50,
            font=LIGHT_FONT,
            align="center",
            line_height=1.22,
        ),
        # Left title block.
        _text(
            "LAYOUT\nDESIGN\nSYSTEMS",
            9.86,
            89.79,
            3.15,
            font=THIN_FONT,
            line_height=1.13,
        ),
        _text("文", 23.90, 101.62, 2.30, font=JP_FONT),
        _text(
            "本編 排（合集）", 9.54, 111.31, 2.50, font=JP_MEDIUM_FONT, spacing=0.055
        ),
        _text("DESIGN BY NEAR.", 9.88, 115.89, 1.20, font=THIN_FONT, spacing=0.09),
        # Central decision system diagram.
        _text(
            "WHAT IS\nNEEDED",
            65.05,
            93.85,
            1.35,
            font=REGULAR_FONT,
            align="center",
            line_height=1.20,
        ),
        _text(
            "REMOVE\nNOISE",
            54.80,
            102.48,
            1.30,
            font=REGULAR_FONT,
            align="center",
            line_height=1.22,
        ),
        _text(
            "RESPECT\nCONTEXT",
            75.30,
            102.48,
            1.30,
            font=REGULAR_FONT,
            align="center",
            line_height=1.22,
        ),
        _text(
            "LEAVE ROOM\nTO ADAPT",
            65.05,
            112.68,
            1.35,
            font=REGULAR_FONT,
            align="center",
            line_height=1.22,
        ),
        # Components list.
        _text("COMPONENTS", 93.35, 87.12, 1.55, font=LIGHT_FONT),
        _text("結構", 99.45, 93.30, 1.50, font=JP_FONT),
        _text("STRUCTURE", 105.30, 93.33, 1.45, font=LIGHT_FONT),
        _text("S-01", 120.55, 93.33, 1.40, font=LIGHT_FONT),
        _text("資訊", 99.45, 99.30, 1.50, font=JP_FONT),
        _text("INFORMATION", 105.30, 99.33, 1.45, font=LIGHT_FONT),
        _text("I-02", 120.55, 99.33, 1.40, font=LIGHT_FONT),
        _text("流動", 99.45, 105.30, 1.50, font=JP_FONT),
        _text("FLOW", 105.30, 105.33, 1.45, font=LIGHT_FONT),
        _text("F-03", 120.55, 105.33, 1.40, font=LIGHT_FONT),
        _text("界面", 99.45, 111.30, 1.50, font=JP_FONT),
        _text("INTERFACE", 105.30, 111.33, 1.45, font=LIGHT_FONT),
        _text("U-04", 120.55, 111.33, 1.40, font=LIGHT_FONT),
        _text("維護", 99.45, 117.30, 1.50, font=JP_FONT),
        _text("MAINTENANCE", 105.30, 117.33, 1.45, font=LIGHT_FONT),
        _text("M-05", 120.55, 117.33, 1.40, font=LIGHT_FONT),
        _rotated_text(
            "TOOLS FOR ORDER",
            140.67,
            90.90,
            2.91,
            90.0,
            font=LIGHT_FONT,
            spacing=0.02,
            suffix="tools-order",
        ),
        _rotated_text(
            "NOT CONTROL",
            136.36,
            92.87,
            2.95,
            90.0,
            font=LIGHT_FONT,
            spacing=0.02,
            suffix="not-control",
        ),
        # Logistics row.
        _text("SERIAL NUMBER", 11.61, 127.64, 1.58, font=THIN_FONT),
        _text("9281 7341 4151", 11.61, 130.22, 2.25, font=THIN_FONT, spacing=0.03),
        _text("BATCH", 58.64, 127.64, 1.50, font=THIN_FONT),
        _text("25-05-A", 58.48, 130.55, 2.35, font=THIN_FONT),
        _text("MFG. LOCATION", 80.35, 127.64, 1.40, font=LIGHT_FONT),
        _text("HKG", 81.025, 132.404, 1.82, font=LIGHT_FONT),
        _text("TYO", 88.988, 132.404, 1.82, font=LIGHT_FONT),
        _text("WEIGHT", 105.75, 127.64, 1.30, font=LIGHT_FONT),
        _text("0.42 KG", 105.75, 132.28, 2.30, font=LIGHT_FONT),
        _text("♻", 128.92, 129.32, 4.75, font=JP_MEDIUM_FONT, align="center"),
        _text("PAPER\nONLY", 133.85, 131.20, 1.25, font=REGULAR_FONT, line_height=1.18),
        # Alignment / notes / code / seal row.
        _text("ALIGNMENT GUIDE", 8.80, 143.30, 1.55, font=REGULAR_FONT),
        _text("NOTES", 62.10, 143.45, 1.25, font=THIN_FONT),
        _text(
            "SYSTEMS OUTLIVE INTERFACES.\nDESIGN FOR CHANGE.\nDOCUMENT THE WHY.\nLEAVE THINGS\nBETTER THAN FOUND.",
            65.15,
            147.10,
            1.58,
            font=THIN_FONT,
            align="center",
            line_height=1.85,
        ),
        _text("CODE INDEX", 88.30, 157.45, 1.15, font=THIN_FONT),
        _text("A1   B7   C3   D9   E2", 88.40, 160.60, 1.22, font=THIN_FONT),
        _text(
            "簡單\n有效",
            128.00,
            150.00,
            2.55,
            font=JP_MEDIUM_FONT,
            align="center",
            line_height=1.48,
        ),
        # Document control row.
        _rotated_text(
            "DOCUMENT TYPE",
            9.20,
            174.55,
            1.30,
            90.0,
            font=THIN_FONT,
            spacing=0.23,
            suffix="document-type",
        ),
        _text(
            "SYSTEM\nLAYOUT\nMANUAL",
            13.18,
            174.05,
            2.90,
            font=LIGHT_FONT,
            line_height=1.17,
        ),
        _text("REV. A", 13.18, 188.95, 1.52, font=MEDIUM_FONT),
        _text("DOC. ID", 41.70, 175.05, 1.30, font=THIN_FONT),
        _text("NASGT-001", 41.70, 178.70, 2.20, font=THIN_FONT),
        _text("–", 41.70, 182.55, 1.35, font=THIN_FONT),
        _text("2025", 41.70, 186.50, 2.15, font=THIN_FONT),
        _text("ACCESS LEVEL", 67.40, 175.05, 1.30, font=THIN_FONT),
        _text("L1", 67.40, 180.30, 1.34, font=THIN_FONT),
        _text("L2", 74.20, 180.30, 1.34, font=THIN_FONT),
        _text("L3", 81.00, 180.30, 1.34, font=THIN_FONT),
        _text(
            "RESTRICTED",
            74.25,
            187.90,
            1.38,
            font=REGULAR_FONT,
            align="center",
            spacing=0.05,
        ),
        _text("DISTRIBUTION", 95.60, 175.05, 1.30, font=THIN_FONT),
        _text("INT", 96.45, 180.65, 1.35, font=THIN_FONT),
        _text("EXT", 96.45, 184.20, 1.35, font=THIN_FONT),
        _text("PUB", 96.45, 187.75, 1.35, font=THIN_FONT),
        _text("Internal", 102.55, 180.65, 1.65, font=THIN_FONT),
        _text("External", 102.55, 184.20, 1.65, font=THIN_FONT),
        _text("Public", 102.55, 187.75, 1.65, font=THIN_FONT),
        _text("SIGN OFF", 121.65, 175.05, 1.30, font=THIN_FONT),
        _rotated_text(
            "N E A R",
            134.25,
            180.10,
            1.05,
            90.0,
            font=THIN_FONT,
            spacing=0.34,
            suffix="near-side",
        ),
        _text("ARCHIVE  ∞", 121.70, 192.30, 1.40, font=THIN_FONT, spacing=0.10),
        # Footer.
        _text(
            "NEAR STUDIO\nSYSTEMIC DESIGN PRACTICE",
            12.90,
            201.05,
            1.50,
            font=THIN_FONT,
            spacing=0.035,
            line_height=1.26,
        ),
        _text(
            "WWW.NEAR.STUDIO",
            70.95,
            201.85,
            1.25,
            font=THIN_FONT,
            align="center",
            spacing=0.18,
        ),
        _text(
            "MADE TO BE UNDERSTOOD.\nBUILT TO BE USED.",
            129.75,
            201.10,
            1.45,
            font=THIN_FONT,
            align="right",
            spacing=0.04,
            line_height=1.25,
        ),
    ]
    items.extend(
        _arc_text(
            "GOOD SYSTEMS MAKE QUIET THINGS POSSIBLE",
            45.68,
            39.20,
            16.50,
            181.0,
            362.0,
            1.60,
            radius_y=20.05,
            tangent_offset=90.0,
            font=BOLD_FONT,
            suffix="upper-manifesto",
            follow_ellipse_tangent=True,
            equal_arc_length=True,
        )
    )
    items.extend(
        _arc_text(
            "DESIGN FOR USE, NOT FOR SHOW",
            45.65,
            37.33,
            16.50,
            150.0,
            32.3,
            1.48,
            radius_y=18.10,
            tangent_offset=-90.0,
            font=BOLD_FONT,
            suffix="lower-manifesto",
            follow_ellipse_tangent=True,
            equal_arc_length=True,
        )
    )
    items.extend(
        _arc_text(
            "SYSTEMS ARE SHAPES",
            65.05,
            104.39,
            14.60,
            228.0,
            312.0,
            1.28,
            radius_y=15.62,
            tangent_offset=90.0,
            font=MEDIUM_FONT,
            suffix="systems-are",
        )
    )
    items.extend(
        _arc_text(
            "OF DECISIONS",
            65.21,
            103.73,
            11.80,
            118.0,
            62.0,
            1.25,
            radius_y=15.52,
            tangent_offset=-90.0,
            font=BOLD_FONT,
            suffix="of-decisions",
        )
    )
    items.extend(
        _arc_text(
            "SIMPLE IS NOT EASY",
            128.00,
            154.10,
            9.30,
            197.0,
            343.0,
            1.08,
            tangent_offset=90.0,
            font=BOLD_FONT,
            suffix="simple-not-easy",
        )
    )
    items.extend(
        _arc_text(
            "EFFECTIVE IS NOT LUCK",
            128.00,
            154.10,
            9.30,
            163.0,
            17.0,
            1.04,
            tangent_offset=-90.0,
            font=BOLD_FONT,
            suffix="effective-not-luck",
        )
    )
    return _combine(items)


def _solid_geometry():
    shapes = [
        *(
            G.rect(
                width=0.34,
                height=1.86,
                angle=34.0,
                center=(68.12 + index * 0.70, 65.18, 0.0),
                key="publisher-stripe",
                instance_key=index,
            )
            for index in range(5)
        ),
        G.circle(radius=0.43, segments=34, center=(143.12, 23.05, 0.0)),
        G.circle(radius=0.43, segments=34, center=(143.12, 25.97, 0.0)),
        G.circle(radius=0.43, segments=34, center=(143.12, 28.89, 0.0)),
        G.circle(radius=0.25, segments=30, center=(105.75, 87.82, 0.0)),
        G.circle(radius=0.30, segments=24, center=(94.55, 181.20, 0.0)),
        G.rect(width=0.62, height=0.62, angle=45.0, center=(91.20, 151.15, 0.0)),
        G.rect(width=0.72, height=0.18, center=(94.20, 151.15, 0.0)),
        G.rect(width=0.18, height=0.72, center=(94.20, 151.15, 0.0)),
        G.rect(width=0.62, height=0.62, angle=45.0, center=(97.20, 151.15, 0.0)),
        G.rect(width=0.82, height=0.82, center=(100.20, 151.15, 0.0)),
        G.circle(radius=0.40, segments=30, center=(103.20, 151.15, 0.0)),
        G.rect(width=0.82, height=0.82, center=(106.20, 151.15, 0.0)),
        G.rect(width=6.02, height=1.72, center=(74.26, 185.08, 0.0)),
        G.rect(width=13.25, height=0.82, center=(19.80, 191.72, 0.0)),
        G.circle(radius=0.31, segments=28, center=(85.05, 202.45, 0.0)),
        G.circle(radius=0.25, segments=28, center=(88.25, 202.45, 0.0)),
    ]
    return _combine(shapes)


def _cutout_label(
    text: str,
    *,
    center: Point,
    width: float,
    height: float,
    scale: float,
    key: str,
    font=BOLD_FONT,
    baseline_factor: float = 0.48,
):
    card = G(name=f"Black label / {key}").rect(
        width=width,
        height=height,
        center=(center[0], center[1], 0.0),
        key="information-black-label",
        instance_key=key,
    )
    filled = E(name=f"Black label fill / {key}").fill(
        angle_sets=6,
        angle=45.0,
        density=FILL_DENSITIES["solid"],
        min_spacing=FILL_MIN_SPACINGS["solid"],
        remove_boundary=False,
        key="information-black-label-fill",
        instance_key=key,
    )(card)
    cutout = _text(
        text,
        center[0],
        center[1] - scale * baseline_factor,
        scale,
        font=font,
        align="center",
        spacing=0.06,
        suffix=f"cutout-{key}",
    )
    return E(name=f"White cutout type / {key}").clip(
        mode="outside",
        draw_outline=False,
        key="information-black-label-cutout",
        instance_key=key,
    )(filled, cutout)


def draw(t: float):
    del t
    fit = E(name="Reference photograph / uniform A5 fit").affine(
        auto_center=False,
        pivot=(0.0, 0.0, 0.0),
        scale=(LAYOUT_FIT_SCALE, LAYOUT_FIT_SCALE, 1.0),
        delta=(LAYOUT_OFFSET[0], LAYOUT_OFFSET[1], 0.0),
        key="information-reference-to-a5-fit",
    )

    typography = E(name="All poster typography / dense black fill").fill(
        angle_sets=2,
        angle=30.0,
        density=FILL_DENSITIES["typography"],
        min_spacing=FILL_MIN_SPACINGS["typography"],
        remove_boundary=False,
        key="information-poster-type-fill",
    )(_body_text_geometry())
    headlines = E(name="Vertical headlines / clean solid fill").fill(
        angle_sets=6,
        angle=45.0,
        density=FILL_DENSITIES["solid"],
        min_spacing=FILL_MIN_SPACINGS["solid"],
        remove_boundary=False,
        key="information-headline-solid-fill",
    )(_headline_text_geometry())
    solids = E(name="Black accents / dense fill").fill(
        angle_sets=6,
        angle=45.0,
        density=FILL_DENSITIES["solid"],
        min_spacing=FILL_MIN_SPACINGS["solid"],
        remove_boundary=False,
        key="information-solid-accent-fill",
    )(_solid_geometry())
    labels = (
        _cutout_label(
            "P",
            center=(10.582, 76.863),
            width=5.25,
            height=5.25,
            scale=5.10,
            key="priority-p",
            baseline_factor=0.609,
        ),
        _cutout_label(
            "INTERNAL USE ONLY",
            center=(119.70, 24.70),
            width=23.75,
            height=2.55,
            scale=1.45,
            key="internal-use",
            font=BOLD_FONT,
        ),
    )

    geometry = (
        fit(G(name="Technical manual linework").poster_linework()),
        fit(typography),
        fit(headlines),
        fit(solids),
        *(fit(label) for label in labels),
    )
    return L(name="Information infrastructure manual / single ink layer").layer(
        geometry,
        color=LINE_COLORS["ink"],
        thickness=LINE_THICKNESS,
    )


if __name__ == "__main__":
    run(
        draw,
        run_id="information_infrastructure_manual_20260810",
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
