from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from grafix import E, G, L, primitive, run

# A5 portrait in millimetres.  The reference artwork is authored on a 200 x 250
# design board, then fitted uniformly to A5 so its original 4:5 proportions are
# preserved instead of being stretched to the paper ratio.
CANVAS = (148, 210)
DESIGN_CANVAS = (200.0, 250.0)
A5_FIT_SCALE = CANVAS[0] / DESIGN_CANVAS[0]
A5_FIT_OFFSET_Y = (CANVAS[1] - DESIGN_CANVAS[1] * A5_FIT_SCALE) / 2.0
SEED = 1203
FONT = "/System/Library/Fonts/HelveticaNeue.ttc"
PAPER = (234 / 255, 233 / 255, 229 / 255)

Point = tuple[float, float]
Polyline = list[Point]


def _pack(lines: list[Polyline]) -> tuple[np.ndarray, np.ndarray]:
    values: list[tuple[float, float, float]] = []
    offsets = [0]
    for line in lines:
        if len(line) < 2:
            continue
        values.extend((float(x), float(y), 0.0) for x, y in line)
        offsets.append(len(values))
    coords = np.asarray(values, dtype=np.float32).reshape((-1, 3))
    index = np.asarray(offsets, dtype=np.int32)
    return np.ascontiguousarray(coords), np.ascontiguousarray(index)


def _line(lines: list[Polyline], a: Point, b: Point) -> None:
    lines.append([a, b])


def _poly(lines: list[Polyline], points: list[Point], *, closed: bool = False) -> None:
    if closed and points:
        lines.append([*points, points[0]])
    else:
        lines.append(points)


def _rect(lines: list[Polyline], x0: float, y0: float, x1: float, y1: float) -> None:
    _poly(lines, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], closed=True)


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
    points: list[Point] = []
    for cx, cy, start in (
        (x1 - radius, y0 + radius, -90.0),
        (x1 - radius, y1 - radius, 0.0),
        (x0 + radius, y1 - radius, 90.0),
        (x0 + radius, y0 + radius, 180.0),
    ):
        for i in range(samples + 1):
            angle = math.radians(start + 90.0 * i / samples)
            points.append(
                (cx + radius * math.cos(angle), cy + radius * math.sin(angle))
            )
    _poly(lines, points, closed=True)


def _circle(
    lines: list[Polyline], cx: float, cy: float, radius: float, *, samples: int = 72
) -> None:
    points = [
        (
            cx + radius * math.cos(math.tau * i / samples),
            cy + radius * math.sin(math.tau * i / samples),
        )
        for i in range(samples)
    ]
    _poly(lines, points, closed=True)


def _filled_circle(
    lines: list[Polyline], cx: float, cy: float, radius: float, *, spacing: float = 0.12
) -> None:
    _circle(lines, cx, cy, radius, samples=48)
    count = max(1, int(math.ceil(2.0 * radius / spacing)))
    for i in range(count + 1):
        y = cy - radius + 2.0 * radius * i / count
        half = math.sqrt(max(0.0, radius * radius - (y - cy) ** 2))
        _line(lines, (cx - half, y), (cx + half, y))


def _filled_rect(
    lines: list[Polyline],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    spacing: float = 0.12,
) -> None:
    _rect(lines, x0, y0, x1, y1)
    count = max(1, int(math.ceil((x1 - x0) / spacing)))
    for i in range(count + 1):
        x = x0 + (x1 - x0) * i / count
        _line(lines, (x, y0), (x, y1))


def _grid(
    lines: list[Polyline],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    nx: int,
    ny: int,
) -> None:
    for i in range(nx + 1):
        x = x0 + (x1 - x0) * i / nx
        _line(lines, (x, y0), (x, y1))
    for j in range(ny + 1):
        y = y0 + (y1 - y0) * j / ny
        _line(lines, (x0, y), (x1, y))


def _quadratic(a: Point, b: Point, c: Point, samples: int = 30) -> Polyline:
    points: Polyline = []
    for i in range(samples + 1):
        t = i / samples
        u = 1.0 - t
        points.append(
            (
                u * u * a[0] + 2.0 * u * t * b[0] + t * t * c[0],
                u * u * a[1] + 2.0 * u * t * b[1] + t * t * c[1],
            )
        )
    return points


def _catmull_rom(control: list[Point], samples_per_span: int = 20) -> Polyline:
    if len(control) < 2:
        return control[:]
    result: Polyline = []
    for i in range(len(control) - 1):
        p0 = control[max(0, i - 1)]
        p1 = control[i]
        p2 = control[i + 1]
        p3 = control[min(len(control) - 1, i + 2)]
        for j in range(samples_per_span):
            t = j / samples_per_span
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * (
                2.0 * p1[0]
                + (-p0[0] + p2[0]) * t
                + (2.0 * p0[0] - 5.0 * p1[0] + 4.0 * p2[0] - p3[0]) * t2
                + (-p0[0] + 3.0 * p1[0] - 3.0 * p2[0] + p3[0]) * t3
            )
            y = 0.5 * (
                2.0 * p1[1]
                + (-p0[1] + p2[1]) * t
                + (2.0 * p0[1] - 5.0 * p1[1] + 4.0 * p2[1] - p3[1]) * t2
                + (-p0[1] + 3.0 * p1[1] - 3.0 * p2[1] + p3[1]) * t3
            )
            result.append((x, y))
    result.append(control[-1])
    return result


def _dashed(points: Polyline, on: float = 1.15, off: float = 0.78) -> list[Polyline]:
    if len(points) < 2:
        return []
    output: list[Polyline] = []
    current: Polyline = []
    drawing = True
    remaining = on
    for start, end in zip(points, points[1:]):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            continue
        position = 0.0
        while position < length - 1e-9:
            step = min(remaining, length - position)
            t0 = position / length
            t1 = (position + step) / length
            a = (start[0] + dx * t0, start[1] + dy * t0)
            b = (start[0] + dx * t1, start[1] + dy * t1)
            if drawing:
                if not current:
                    current = [a]
                current.append(b)
            position += step
            remaining -= step
            if remaining <= 1e-9:
                if drawing and len(current) >= 2:
                    output.append(current)
                    current = []
                drawing = not drawing
                remaining = on if drawing else off
    if drawing and len(current) >= 2:
        output.append(current)
    return output


def _subtract_interval(
    intervals: list[tuple[float, float]], cut0: float, cut1: float
) -> list[tuple[float, float]]:
    output: list[tuple[float, float]] = []
    for start, end in intervals:
        if cut1 <= start or cut0 >= end:
            output.append((start, end))
            continue
        if start < cut0:
            output.append((start, min(end, cut0)))
        if cut1 < end:
            output.append((max(start, cut1), end))
    return output


def _segment_outside_circles(
    a: Point, b: Point, circles: list[tuple[float, float, float]]
) -> list[Polyline]:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    aa = dx * dx + dy * dy
    if aa <= 1e-12:
        return []
    intervals = [(0.0, 1.0)]
    for cx, cy, radius in circles:
        ox = a[0] - cx
        oy = a[1] - cy
        bb = 2.0 * (ox * dx + oy * dy)
        cc = ox * ox + oy * oy - radius * radius
        discriminant = bb * bb - 4.0 * aa * cc
        if discriminant < 0.0:
            if (a[0] - cx) ** 2 + (a[1] - cy) ** 2 < radius * radius:
                intervals = []
            continue
        root = math.sqrt(max(0.0, discriminant))
        t0 = (-bb - root) / (2.0 * aa)
        t1 = (-bb + root) / (2.0 * aa)
        intervals = _subtract_interval(intervals, max(0.0, t0), min(1.0, t1))
    return [
        [
            (a[0] + dx * start, a[1] + dy * start),
            (a[0] + dx * end, a[1] + dy * end),
        ]
        for start, end in intervals
        if end - start > 1e-7
    ]


def _clipped_polyline(
    points: Polyline, circles: list[tuple[float, float, float]]
) -> list[Polyline]:
    output: list[Polyline] = []
    for a, b in zip(points, points[1:]):
        output.extend(_segment_outside_circles(a, b, circles))
    return output


def _hatch_rect(
    lines: list[Polyline],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    spacing: float,
    slope: float = 0.0,
) -> None:
    if abs(slope) < 1e-9:
        count = max(1, int(math.ceil((x1 - x0) / spacing)))
        for i in range(count + 1):
            x = x0 + (x1 - x0) * i / count
            _line(lines, (x, y0), (x, y1))
        return

    # Parallel diagonals clipped analytically against the rectangle.
    height = y1 - y0
    shift = abs(height / slope)
    start = x0 - shift
    count = max(1, int(math.ceil((x1 - start) / spacing)))
    for i in range(count + 1):
        base_x = start + i * (x1 - start) / count
        candidates: list[Point] = []
        for y in (y0, y1):
            x = base_x + (y - y0) / slope
            if x0 - 1e-9 <= x <= x1 + 1e-9:
                candidates.append((x, y))
        for x in (x0, x1):
            y = y0 + slope * (x - base_x)
            if y0 - 1e-9 <= y <= y1 + 1e-9:
                candidates.append((x, y))
        unique: list[Point] = []
        for point in candidates:
            if not any(
                math.hypot(point[0] - q[0], point[1] - q[1]) < 1e-7 for q in unique
            ):
                unique.append(point)
        if len(unique) >= 2:
            _line(lines, unique[0], unique[1])


def _halftone(
    lines: list[Polyline],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    pitch: float,
    length: float = 0.07,
) -> None:
    rows = max(1, int((y1 - y0) / pitch))
    cols = max(1, int((x1 - x0) / pitch))
    for row in range(rows + 1):
        y = y0 + (y1 - y0) * row / rows
        offset = 0.5 * pitch if row % 2 else 0.0
        for col in range(cols + 1):
            x = x0 + (x1 - x0) * col / cols + offset
            if x <= x1:
                _line(lines, (x - length, y), (x + length, y))


def _registration_mark(lines: list[Polyline], cx: float, cy: float) -> None:
    _circle(lines, cx, cy, 1.05, samples=36)
    _circle(lines, cx, cy, 1.85, samples=48)
    _line(lines, (cx - 3.0, cy), (cx + 3.0, cy))
    _line(lines, (cx, cy - 3.0), (cx, cy + 3.0))


def _panel_one(groups: dict[str, list[Polyline]]) -> None:
    grid = groups["grid"]
    main = groups["main"]
    heavy = groups["heavy"]
    mist = groups["mist"]

    _grid(grid, 21.7, 50.4, 93.6, 107.6, 20, 16)
    _halftone(mist, 47.5, 50.8, 65.5, 107.2, pitch=0.82, length=0.045)
    cx, cy = 57.4, 79.1
    # The five rings are measured directly from the 1024 px reference.
    for radius in (6.2, 10.8, 15.6, 20.7, 26.3):
        _circle(main, cx, cy, radius, samples=112)
    _line(main, (21.7, cy), (93.6, cy))
    _line(main, (cx, 50.4), (cx, 107.6))
    for index, x in enumerate(np.linspace(27.0, 87.8, 35)):
        length = 2.7 if index % 8 == 0 else 1.45 if index % 4 == 0 else 0.75
        _line(main, (x, cy - length / 2.0), (x, cy + length / 2.0))
    _filled_circle(mist, cx, cy, 3.25, spacing=0.10)
    _filled_circle(heavy, cx, cy, 1.22, spacing=0.1)

    for start, knee, end in (
        ((73.2, 59.6), (76.0, 57.4), (92.5, 57.4)),
        ((69.0, 66.0), (75.8, 62.3), (92.5, 62.3)),
        ((65.8, 73.2), (75.8, 67.2), (92.5, 67.2)),
    ):
        _poly(main, [start, knee, end])

    _line(main, (21.7, 117.0), (49.2, 117.0))
    _line(main, (65.6, 117.0), (93.6, 117.0))


def _panel_two(groups: dict[str, list[Polyline]]) -> None:
    main = groups["main"]
    mist = groups["mist"]
    heavy = groups["heavy"]

    x0, x1 = 110.2, 179.9
    y_top, y_zero, y_bottom = 54.7, 70.3, 87.4
    _hatch_rect(mist, 136.7, y_top, 149.5, y_bottom, spacing=0.24)
    _halftone(mist, 136.9, y_top + 0.2, 149.3, y_bottom - 0.2, pitch=0.75, length=0.035)
    _line(main, (x0, 50.4), (x0, 93.8))
    _line(main, (x0, 93.8), (x1, 93.8))
    _line(main, (x0, y_bottom), (x1, y_bottom))
    _line(main, (x0, y_zero), (x1, y_zero))
    main.extend(_dashed([(x0, y_top), (x1, y_top)], on=1.0, off=0.78))
    main.extend(_dashed([(x0, y_bottom), (138.3, y_bottom)], on=0.95, off=0.72))
    for x in (x0, (x0 + x1) / 2.0, x1):
        _line(main, (x, 93.2), (x, 94.4))

    solid = _catmull_rom(
        [
            (110.2, 70.0),
            (121.9, 85.4),
            (137.3, 54.5),
            (151.4, 85.4),
            (160.0, 68.0),
            (170.0, 78.3),
            (179.7, 68.8),
        ],
        24,
    )
    _poly(main, solid)
    dashed_wave = _catmull_rom(
        [
            (110.5, 56.5),
            (119.9, 82.4),
            (131.1, 63.9),
            (141.0, 80.0),
            (147.0, 69.0),
            (156.0, 84.0),
            (168.0, 56.8),
            (179.5, 86.7),
        ],
        24,
    )
    main.extend(_dashed(dashed_wave, on=1.1, off=0.82))

    center = (113.4, 109.8)
    radii = (
        8.0,
        7.2,
        7.7,
        6.7,
        7.3,
        6.4,
        7.4,
        6.8,
        7.9,
        6.5,
        7.4,
        6.8,
        8.0,
        7.1,
        7.7,
        6.8,
    )
    for i, radius in enumerate(radii):
        angle = math.tau * i / len(radii)
        _line(
            heavy,
            center,
            (
                center[0] + radius * math.cos(angle),
                center[1] + radius * math.sin(angle),
            ),
        )

    y0, y1 = 104.7, 114.7
    count = 78
    for i in range(count):
        t = i / (count - 1)
        x = 126.7 + 53.3 * (1.0 - (1.0 - t) ** 2.22)
        _line(heavy, (x, y0), (x, y1))
    _line(heavy, (126.7, y0), (180.0, y0))
    _line(heavy, (126.7, y1), (180.0, y1))


def _panel_three(groups: dict[str, list[Polyline]]) -> None:
    grid = groups["grid"]
    main = groups["main"]
    mist = groups["mist"]
    heavy = groups["heavy"]

    left = (34.8, 168.2)
    top = (60.7, 152.5)
    right = (86.7, 168.2)
    bottom = (60.7, 183.0)
    for i in range(11):
        t = i / 10.0
        _line(
            main,
            (left[0] + (top[0] - left[0]) * t, left[1] + (top[1] - left[1]) * t),
            (
                bottom[0] + (right[0] - bottom[0]) * t,
                bottom[1] + (right[1] - bottom[1]) * t,
            ),
        )
        _line(
            main,
            (top[0] + (right[0] - top[0]) * t, top[1] + (right[1] - top[1]) * t),
            (left[0] + (bottom[0] - left[0]) * t, left[1] + (bottom[1] - left[1]) * t),
        )
    _poly(main, [left, top, right, bottom], closed=True)

    for x, y0, y1 in (
        (50.5, 169.0, 149.5),
        (60.7, 169.0, 145.8),
        (70.9, 169.2, 149.5),
    ):
        _line(main, (x, y0), (x, y1))
        _line(main, (x, y1), (x - 1.25, y1 + 1.55))
        _line(main, (x, y1), (x + 1.25, y1 + 1.55))

    # Bowed contour sheet and a denser offset plate underneath it.
    for i in range(19):
        t = i / 18.0
        start = (35.0 + 0.2 * t, 181.7 + 7.2 * t)
        end = (86.5 - 0.2 * t, 181.7 + 7.2 * t)
        middle = (60.7, 196.0 - 4.8 * t)
        _poly(mist if i % 2 else grid, _quadratic(start, middle, end, 42))
    _poly(main, [(35.0, 181.7), (60.7, 195.5), (86.5, 181.7)])
    _poly(main, [(35.0, 188.7), (60.7, 204.8), (86.5, 188.7)])
    _line(main, (35.0, 181.7), (35.0, 188.7))
    _line(main, (86.5, 181.7), (86.5, 188.7))
    _line(main, (60.7, 195.5), (60.7, 204.8))
    for offset in (1.0, 2.0):
        _poly(
            grid,
            [
                (35.0, 188.7 + offset),
                (60.7, 204.8 + offset * 0.58),
                (86.5, 188.7 + offset),
            ],
        )

    # Layer thickness ruler.
    _line(main, (27.1, 169.0), (27.1, 195.3))
    for y, length in (
        (169.0, 2.0),
        (174.9, 1.35),
        (180.8, 2.0),
        (186.7, 1.35),
        (192.6, 2.0),
        (195.3, 1.35),
    ):
        _line(main, (27.1 - length / 2.0, y), (27.1 + length / 2.0, y))

    # Nested-square, point scale and micro-grid specimens.
    _rect(main, 21.3, 206.5, 35.4, 219.9)
    cx, cy = 28.35, 213.2
    for i in range(11):
        rx = 6.1 - 0.48 * i
        ry = 5.75 - 0.45 * i
        phase = 0.0
        points: list[Point] = []
        for x, y in ((-rx, -ry), (rx, -ry), (rx, ry), (-rx, ry)):
            points.append(
                (
                    cx + x * math.cos(phase) - y * math.sin(phase),
                    cy + x * math.sin(phase) + y * math.cos(phase),
                )
            )
        _poly(main, points, closed=True)

    _rect(main, 39.3, 206.5, 76.4, 219.1)
    for x, radius in zip(
        (41.7, 46.6, 51.8, 58.3, 65.2, 72.5),
        (0.45, 0.90, 1.30, 1.85, 2.30, 2.70),
    ):
        _filled_circle(heavy, x, 211.1, radius, spacing=0.04)

    _rect(main, 79.5, 206.5, 94.3, 219.8)
    _grid(grid, 79.5, 206.5, 94.3, 219.8, 20, 18)


def _panel_four(groups: dict[str, list[Polyline]]) -> None:
    grid = groups["grid"]
    main = groups["main"]
    heavy = groups["heavy"]

    x0, y0, x1, y1 = 111.0, 153.5, 174.4, 195.3
    bubbles = [
        (120.5, 191.9, 3.0),
        (134.2, 190.6, 4.2),
        (149.0, 188.3, 5.7),
        (166.4, 185.5, 7.3),
    ]
    for i in range(21):
        x = x0 + (x1 - x0) * i / 20.0
        grid.extend(_segment_outside_circles((x, y0), (x, y1), bubbles))
    for j in range(16):
        y = y0 + (y1 - y0) * j / 15.0
        grid.extend(_segment_outside_circles((x0, y), (x1, y), bubbles))

    solid = _catmull_rom(
        [
            (111.0, 192.2),
            (126.5, 184.0),
            (140.2, 176.4),
            (155.0, 164.5),
            (174.2, 154.1),
        ],
        12,
    )
    main.extend(_clipped_polyline(solid, bubbles))
    dashed = _catmull_rom(
        [
            (111.0, 192.0),
            (126.0, 187.0),
            (140.0, 181.0),
            (155.0, 172.7),
            (174.0, 158.0),
        ],
        12,
    )
    for dash in _dashed(dashed, on=1.2, off=0.85):
        main.extend(_clipped_polyline(dash, bubbles))
    for cx, cy, radius in bubbles:
        _circle(main, cx, cy, radius, samples=80)

    # Eleven archival-density swatches, from dense ink to bare stock.
    sx0, sy0, sx1, sy1 = 105.5, 206.6, 153.3, 214.3
    _rect(main, sx0, sy0, sx1, sy1)
    width = (sx1 - sx0) / 11.0
    for i in range(11):
        left = sx0 + width * i
        if i:
            _line(grid, (left, sy0), (left, sy1))

    # Six contrast chips: cross-hatch, opposed hatch, black, and reserved paper.
    cx0, cy0, cx1, cy1 = 157.0, 206.6, 180.1, 219.3
    _rect(main, cx0, cy0, cx1, cy1)
    cell_w = (cx1 - cx0) / 3.0
    cell_h = (cy1 - cy0) / 2.0
    for i in (1, 2):
        _line(main, (cx0 + cell_w * i, cy0), (cx0 + cell_w * i, cy1))
    _line(main, (cx0, cy0 + cell_h), (cx1, cy0 + cell_h))
    _hatch_rect(
        heavy,
        cx0 + 0.08,
        cy0 + 0.08,
        cx0 + cell_w - 0.08,
        cy0 + cell_h - 0.08,
        spacing=0.82,
        slope=-1.0,
    )
    _hatch_rect(
        heavy,
        cx0 + 0.08,
        cy0 + cell_h + 0.08,
        cx0 + cell_w - 0.08,
        cy1 - 0.08,
        spacing=0.54,
        slope=-1.0,
    )
    _hatch_rect(
        heavy,
        cx0 + cell_w + 0.08,
        cy0 + 0.08,
        cx0 + 2.0 * cell_w - 0.08,
        cy0 + cell_h - 0.08,
        spacing=0.52,
        slope=1.0,
    )
    _hatch_rect(
        heavy,
        cx0 + cell_w + 0.08,
        cy0 + cell_h + 0.08,
        cx0 + 2.0 * cell_w - 0.08,
        cy1 - 0.08,
        spacing=0.82,
        slope=1.0,
    )
    _filled_rect(
        heavy,
        cx0 + 2.0 * cell_w + 0.08,
        cy0 + 0.08,
        cx1 - 0.08,
        cy0 + cell_h - 0.08,
        spacing=0.1,
    )


@lru_cache(maxsize=1)
def _archive_groups() -> tuple[tuple[np.ndarray, np.ndarray], ...]:
    groups: dict[str, list[Polyline]] = {
        "grain": [],
        "mist": [],
        "grid": [],
        "main": [],
        "heavy": [],
    }
    grain = groups["grain"]
    main = groups["main"]
    heavy = groups["heavy"]

    rng = np.random.default_rng(SEED)
    for _ in range(50000):
        x = float(rng.uniform(3.0, 197.0))
        y = float(rng.uniform(3.0, 247.0))
        length = float(rng.uniform(0.025, 0.16))
        angle = float(rng.uniform(0.0, math.tau))
        dx = length * math.cos(angle)
        dy = length * math.sin(angle)
        _line(grain, (x - dx / 2.0, y - dy / 2.0), (x + dx / 2.0, y + dy / 2.0))
    for _ in range(70):
        x = float(rng.uniform(5.0, 195.0))
        y = float(rng.uniform(4.0, 246.0))
        length = float(rng.uniform(0.28, 0.85))
        _line(grain, (x, y), (x + length, y + float(rng.normal(0.0, 0.035))))

    panels = (
        (17.2, 17.0, 98.2, 122.1),
        (101.8, 17.3, 183.3, 122.0),
        (17.3, 124.8, 98.1, 225.1),
        (101.9, 124.7, 183.2, 225.2),
    )
    for x0, y0, x1, y1 in panels:
        _rounded_rect(main, x0, y0, x1, y1, 4.5)

    _panel_one(groups)
    _panel_two(groups)
    _panel_three(groups)
    _panel_four(groups)

    for cx, cy in ((9.4, 10.2), (190.6, 10.2), (9.4, 219.6), (190.6, 219.6)):
        _registration_mark(main, cx, cy)

    # Split horizontal rulers leave room for the central printer's cross.
    for y, direction in ((10.1, -1.0), (231.1, 1.0)):
        for xa, xb in ((17.2, 96.0), (104.0, 183.2)):
            for i in range(91):
                x = xa + (xb - xa) * i / 90.0
                if i % 10 == 0:
                    length = 2.35
                elif i % 5 == 0:
                    length = 1.45
                else:
                    length = 0.62
                _line(main, (x, y), (x, y + direction * length))
    for cy in (10.1, 231.1):
        _line(heavy, (96.8, cy), (103.2, cy))
        _line(heavy, (100.0, cy - 3.2), (100.0, cy + 3.2))

    # Optical side bars made from hundreds of short ruled strokes.
    for side, (x0, x1) in enumerate(((8.45, 10.85), (189.15, 191.55))):
        for i in range(577):
            t = i / 576.0
            y = 22.0 + 144.0 * t
            if i % 58 == 0:
                inset = 0.0
            elif i % 10 == 0:
                inset = 0.28
            else:
                inset = 0.62
            _line(groups["grid"], (x0 + inset, y), (x1 - inset, y))
            if side == 1 and i % 3 == 0:
                _line(heavy, (x0 + inset, y), (x1 - inset, y))
            elif side == 0:
                gradient_step = max(2, int(round(9.0 - 7.0 * t)))
                if i % gradient_step == 0:
                    _line(heavy, (x0 + inset, y), (x1 - inset, y))
    for x in (6.1, 193.9):
        _line(main, (x - 0.9, 22.5), (x + 0.9, 22.5))
        _line(main, (x - 0.9, 23.1), (x + 0.9, 23.1))
        _line(main, (x - 0.9, 23.7), (x + 0.9, 23.7))

    # Bottom-right archival barcode.
    bx = 164.2
    for i in range(25):
        x = bx + 0.75 * i
        copies = 1 + (1 if i % 5 in (1, 2) else 0)
        for j in range(copies):
            _line(heavy, (x + 0.12 * j, 241.5), (x + 0.12 * j, 243.6))

    order = ("grain", "mist", "grid", "main", "heavy")
    return tuple(_pack(groups[name]) for name in order)


@primitive
def measurement_poster_lines(*, group: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """アーカイブ調の技術プレート線群を濃度別に返す。"""

    data = _archive_groups()
    index = max(0, min(len(data) - 1, int(group)))
    coords, offsets = data[index]
    return coords.copy(), offsets.copy()


def _text_gui_name(text: str, x: float, y: float) -> str:
    """Parameter GUI で判別しやすい短いテキスト名を返す。"""

    summary = " ".join(text.split())
    if len(summary) > 32:
        summary = f"{summary[:29]}..."
    return f"Text / {summary} @ {x:.1f},{y:.1f}"


def _text_gui_key(text: str, x: float, y: float) -> str:
    """同じヘルパー経由の各テキストへ安定した semantic key を与える。"""

    normalized = "|".join(text.splitlines())
    return f"poster-text:{normalized}:{x:.3f}:{y:.3f}"


def _text(
    text: str,
    x: float,
    y: float,
    scale: float,
    *,
    bold: bool = False,
    font_index: int | None = None,
    align: str = "left",
    line_height: float = 1.12,
    spacing: float = 0.0,
):
    return G(name=_text_gui_name(text, x, y)).text(
        text=text,
        font=FONT,
        font_index=(1 if bold else 0) if font_index is None else font_index,
        text_align=align,
        letter_spacing_em=spacing,
        line_height=line_height,
        quality=0.34,
        center=(x, y, 0.0),
        scale=scale,
        key="poster-text",
        instance_key=_text_gui_key(text, x, y),
    )


def _vertical_text(text: str, x: float, y: float, scale: float, *, bold: bool = False):
    geometry = _text(text, x, y, scale, bold=bold)
    return E(name=f"Rotate vertical text / {text} @ {x:.1f},{y:.1f}").rotate(
        auto_center=False,
        pivot=(x, y, 0.0),
        rotation=(0.0, 0.0, -90.0),
        key="vertical-text-rotation",
        instance_key=f"{text}:{x:.3f}:{y:.3f}",
    )(geometry)


def _join_text(items: list, *, name: str, key: str):
    geometry = items[0]
    for item in items[1:]:
        geometry = geometry + item
    return E(name=f"Fill typography / {name}").fill(
        angle_sets=2,
        angle=45.0,
        density=1000.0,
        min_spacing=0.045,
        remove_boundary=False,
        key="typography-fill",
        instance_key=key,
    )(geometry)


def _value_swatch_geometries() -> tuple:
    """11段階の無彩色スウォッチを均一な面として生成する。"""

    x0, y0, x1, y1 = 105.5, 206.6, 153.3, 214.3
    width = (x1 - x0) / 11.0
    geometries = []
    for index in range(11):
        value = index * 10
        geometry = G(name=f"Value swatch / {value:03d} / rectangle").rect(
            width=width - 0.04,
            height=(y1 - y0) - 0.04,
            center=(x0 + width * (index + 0.5), (y0 + y1) / 2.0, 0.0),
            key="value-swatch-rectangle",
            instance_key=index,
        )
        geometries.append(
            E(name=f"Value swatch / {value:03d} / fill").fill(
                angle_sets=1,
                angle=0.0,
                density=1000.0,
                min_spacing=0.015,
                remove_boundary=False,
                key="value-swatch-fill",
                instance_key=index,
            )(geometry)
        )
    return tuple(geometries)


def _text_groups() -> tuple:
    top_headers = [
        _text("01", 21.7, 20.0, 5.0, bold=True),
        _text("CALIBRATE\nPOSSIBILITY.", 21.7, 26.5, 3.42, line_height=0.98),
        _text(
            "DEFINE THE SPACE OF\nWHAT COULD BE. MEASURE\nCONDITIONS. ESTABLISH\nA REFERENCE FRAME.",
            21.7,
            35.1,
            1.92,
            font_index=10,
            line_height=1.12,
        ),
        _text("REF. 001", 93.7, 20.8, 1.66, font_index=10, align="right"),
        _text("02", 105.5, 20.0, 5.0, bold=True),
        _text("OBSERVE\nRHYTHMS.", 105.5, 26.5, 3.42, line_height=0.98),
        _text(
            "PATTERNS EMERGE AND\nRECEDE. MAP CYCLES\nOF INTENSITY, REST,\nAND RECOVERY.",
            105.5,
            35.1,
            1.92,
            font_index=10,
            line_height=1.12,
        ),
        _text("REF. 002", 179.6, 20.8, 1.66, font_index=10, align="right"),
    ]

    top_labels = [
        _text("N", 57.4, 46.5, 1.45, bold=True, align="center"),
        _text("S", 57.4, 109.1, 1.45, bold=True, align="center"),
        _text("W", 20.5, 78.0, 1.35, bold=True, align="center"),
        _text("E", 95.2, 78.0, 1.35, bold=True, align="center"),
        _text("OUTER LIMIT", 78.0, 56.4, 1.05, bold=True),
        _text("OPERATING RANGE", 77.8, 61.3, 1.05, bold=True),
        _text("CORE INTENT", 77.8, 66.2, 1.05, bold=True),
        _text("SCALE: 10u", 21.7, 109.6, 1.12, bold=True),
        _text("REFERENCE FIELD", 57.4, 116.2, 1.05, bold=True, align="center"),
        _vertical_text("LEVEL", 105.1, 74.9, 1.13, bold=True),
        _text("+", 107.1, 52.0, 1.40, bold=True),
        _text("0", 107.1, 68.8, 1.22, bold=True),
        _text("−", 107.1, 85.6, 1.22, bold=True),
        _text("HIGH ALERT", 115.3, 51.4, 1.08, bold=True),
        _text("DEEP REST", 115.3, 88.5, 1.08, bold=True),
        _text(
            "OPTIMAL\nWINDOW",
            143.0,
            81.9,
            1.02,
            bold=True,
            align="center",
            line_height=1.08,
        ),
        _text("SLEEP CYCLE DIAGRAM", 178.7, 89.0, 1.02, bold=True, align="right"),
        _text("0", 110.2, 95.0, 1.06, bold=True, align="center"),
        _text("12", 143.5, 95.0, 1.06, bold=True, align="center"),
        _text("24H", 179.3, 95.0, 1.06, bold=True, align="center"),
        _text("TIME", 145.0, 98.2, 1.08, bold=True, align="center"),
        _text("LOW", 126.8, 116.2, 1.08, bold=True),
        _text("LINE DENSITY STUDY", 153.3, 116.7, 1.02, bold=True, align="center"),
        _text("HIGH", 180.0, 116.2, 1.08, bold=True, align="right"),
    ]

    bottom_headers = [
        _text("03", 21.7, 128.1, 5.0, bold=True),
        _text("STRUCTURE\nAT THE FINEST SCALE.", 21.7, 134.7, 3.27, line_height=0.98),
        _text(
            "SMALL SHIFTS COMPOUND.\nDESIGN FOR RESILIENCE\nIN MICRO-DETAILS.",
            21.7,
            143.0,
            1.87,
            font_index=10,
            line_height=1.12,
        ),
        _text("REF. 003", 93.7, 128.9, 1.66, font_index=10, align="right"),
        _text("04", 105.5, 128.1, 5.0, bold=True),
        _text(
            "MEASURE CHANGE.\nITERATE WITH CARE.", 105.5, 134.7, 3.20, line_height=0.98
        ),
        _text(
            "TRACK PROGRESS OVER TIME.\nWHAT IS MEASURED\nCAN BE IMPROVED.",
            105.5,
            143.0,
            1.84,
            font_index=10,
            line_height=1.12,
        ),
        _text("REF. 004", 179.6, 128.9, 1.66, font_index=10, align="right"),
    ]

    bottom_labels = [
        _text("LAYER\nTHICKNESS", 20.8, 167.0, 0.88, bold=True, line_height=1.05),
        _text("100u", 20.8, 172.3, 1.08, bold=True),
        _text("10u", 21.8, 178.0, 1.08, bold=True),
        _text("1u", 22.5, 183.8, 1.08, bold=True),
        _text("0.1u", 21.1, 189.5, 1.08, bold=True),
        _text("0.01u", 20.2, 195.0, 1.08, bold=True),
        _text("0.1", 41.7, 215.2, 0.90, bold=True, align="center"),
        _text("0.2", 46.6, 215.2, 0.90, bold=True, align="center"),
        _text("0.4", 51.8, 215.2, 0.90, bold=True, align="center"),
        _text("0.6", 58.3, 215.2, 0.90, bold=True, align="center"),
        _text("0.8", 65.2, 215.2, 0.90, bold=True, align="center"),
        _text("1.0", 72.5, 215.2, 0.90, bold=True, align="center"),
        _text("POINT SIZE SCALE", 57.8, 220.0, 0.96, bold=True, align="center"),
        _text("MICRO GRID", 94.2, 220.0, 0.96, bold=True, align="right"),
        _text("MAX", 105.0, 152.8, 1.00, bold=True),
        _vertical_text("IMPACT", 105.0, 180.0, 1.08, bold=True),
        _text("MIN", 105.0, 194.3, 1.00, bold=True),
        _text("DAY", 120.5, 197.9, 0.98, bold=True, align="center"),
        _text("WEEK", 134.2, 197.9, 0.98, bold=True, align="center"),
        _text("MONTH", 149.0, 197.9, 0.98, bold=True, align="center"),
        _text("YEAR", 166.4, 197.9, 0.98, bold=True, align="center"),
        _text("TIME", 143.0, 201.6, 1.02, bold=True, align="center"),
        *[
            _text(
                str(value),
                105.5 + 47.8 * i / 10.0,
                216.1,
                0.88,
                bold=True,
                align="center",
            )
            for i, value in enumerate(range(0, 101, 10))
        ],
        _text("VALUE SCALE", 129.4, 220.0, 0.96, bold=True, align="center"),
        _text("CONTRAST TEST", 168.5, 220.0, 0.96, bold=True, align="center"),
    ]

    ruler_labels = [
        *[
            _text(
                label,
                x,
                12.4,
                1.45 if label != "10" else 1.32,
                bold=True,
                align="center",
            )
            for x, label in zip(
                (25.7, 35.0, 53.1, 70.3, 87.4), ("1", "2", "3", "3", "4")
            )
        ],
        *[
            _text(
                label,
                x,
                12.4,
                1.45 if label != "10" else 1.32,
                bold=True,
                align="center",
            )
            for x, label in zip(
                (113.1, 130.3, 147.5, 165.0, 182.5), ("6", "7", "8", "9", "10")
            )
        ],
        *[
            _text(
                label,
                x,
                227.2,
                1.40 if label != "10" else 1.30,
                bold=True,
                align="center",
            )
            for x, label in zip(
                (17.4, 25.8, 35.0, 53.1, 70.3, 87.4), ("1", "1", "2", "3", "3", "4")
            )
        ],
        *[
            _text(
                label,
                x,
                227.2,
                1.40 if label != "10" else 1.30,
                bold=True,
                align="center",
            )
            for x, label in zip(
                (113.1, 130.3, 147.5, 165.0, 182.5), ("6", "7", "8", "9", "10")
            )
        ],
        _text("+", 6.1, 35.3, 1.35, bold=True, align="center"),
        _text("+", 193.9, 35.3, 1.35, bold=True, align="center"),
    ]
    ruler_y = (49.6, 63.9, 78.2, 92.5, 106.8, 121.1, 135.4, 149.7, 164.0)
    for y, label in zip(ruler_y, ("2", "3", "4", "5", "6", "7", "8", "9", "10")):
        ruler_labels.append(
            _text(
                label,
                6.1,
                y,
                1.40 if label != "10" else 1.25,
                bold=True,
                align="center",
            )
        )
    for y, label in zip(ruler_y, ("2", "3", "7", "5", "6", "7", "8", "9", "10")):
        ruler_labels.append(
            _text(
                label,
                193.9,
                y,
                1.40 if label != "10" else 1.25,
                bold=True,
                align="center",
            )
        )

    footer = [
        _text(
            "SYSTEMS THINKING\nTHROUGH MEASUREMENT",
            17.4,
            238.7,
            1.42,
            bold=True,
            line_height=1.23,
        ),
        _text(
            "ANALYTIC TOOLKIT\nFOR ABSTRACT WORK",
            64.0,
            238.7,
            1.42,
            bold=True,
            line_height=1.23,
        ),
        _text("VERSION 1.0", 118.8, 242.0, 1.18, bold=True),
        _text("MMXXV", 144.7, 242.0, 1.18, bold=True),
    ]

    named_groups = (
        ("Top panel headers", "top-panel-headers", top_headers),
        ("Top panel annotations", "top-panel-annotations", top_labels),
        ("Bottom panel headers", "bottom-panel-headers", bottom_headers),
        ("Bottom panel annotations", "bottom-panel-annotations", bottom_labels),
        ("Outer ruler labels", "outer-ruler-labels", ruler_labels),
        ("Footer labels", "footer-labels", footer),
    )
    return tuple(
        _join_text(group, name=name, key=key) for name, key, group in named_groups
    )


def draw(t: float):
    del t
    fit_a5 = E(name="A5 portrait layout / artwork fit").affine(
        auto_center=False,
        pivot=(0.0, 0.0, 0.0),
        scale=(A5_FIT_SCALE, A5_FIT_SCALE, 1.0),
        delta=(0.0, A5_FIT_OFFSET_Y, 0.0),
        key="a5-portrait-artwork-fit",
    )
    grain = G(name="Paper fibers / archival grain").measurement_poster_lines(
        group=0,
        key="paper-fibers",
    )
    mist = G(name="Microfilm mist / pale construction").measurement_poster_lines(
        group=1,
        key="microfilm-mist",
    )
    grid = G(name="Fine ruled grids / all panels").measurement_poster_lines(
        group=2,
        key="fine-ruled-grids",
    )
    main = G(name="Technical plate / primary lines").measurement_poster_lines(
        group=3,
        key="technical-plate-lines",
    )
    heavy = G(name="Dense ink / marks and bars").measurement_poster_lines(
        group=4,
        key="dense-ink-marks",
    )
    text_groups = tuple(fit_a5(geometry) for geometry in _text_groups())
    value_swatches = tuple(fit_a5(geometry) for geometry in _value_swatch_geometries())
    grain = fit_a5(grain)
    mist = fit_a5(mist)
    grid = fit_a5(grid)
    main = fit_a5(main)
    heavy = fit_a5(heavy)
    swatch_tones = (0.03, 0.14, 0.25, 0.36, 0.47, 0.58, 0.67, 0.75, 0.82, 0.88, 0.94)

    return (
        L("paper fibers").layer(grain, color=(0.71, 0.69, 0.64), thickness=0.00014),
        L("microfilm mist").layer(mist, color=(0.56, 0.55, 0.51), thickness=0.00038),
        *(
            L(f"value swatch {index:02d}").layer(
                geometry,
                color=(tone, tone, tone),
                thickness=0.00160,
                instance_key=index,
            )
            for index, (geometry, tone) in enumerate(zip(value_swatches, swatch_tones))
        ),
        L("fine ruled grids").layer(grid, color=(0.32, 0.31, 0.28), thickness=0.00054),
        L("technical plate lines").layer(
            main, color=(0.035, 0.032, 0.028), thickness=0.00130
        ),
        L("dense ink marks").layer(
            heavy, color=(0.008, 0.007, 0.006), thickness=0.00180
        ),
        *(
            L(f"archival typography {index + 1}").layer(
                geometry,
                color=(0.008, 0.007, 0.006),
                thickness=0.00108,
            )
            for index, geometry in enumerate(text_groups)
        ),
    )


if __name__ == "__main__":
    run(
        draw,
        run_id="measurement_poster_a5",
        canvas_size=CANVAS,
        render_scale=6,
        background_color=PAPER,
        parameter_gui=True,
        parameter_persistence=True,
        midi_port_name=None,
        n_worker=0,
        seed=SEED,
    )
