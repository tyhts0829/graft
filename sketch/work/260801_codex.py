"""FAULT GARDEN / PLATE 03 — reference-faithful geological herbarium.

The original BSP composition is retained, while the specimen, topographic
field, print colours, and typographic hierarchy are traced more closely from
``concept_03_fault_garden.png``.  The drawing is static; ``t`` is accepted only
to satisfy the Grafix draw protocol.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from grafix import E, G, L, primitive, run

CANVAS = (148, 210)
SEED = 271
VARIANT = 2
FAN_ORIGIN = (93.2, 205.2)
FAN_RADIUS = 13.6
FAN_ANGLE_VALUES = (30, 60, 90, 120, 150)

DISPLAY_FONT = "/System/Library/Fonts/Avenir Next Condensed.ttc"
MONO_FONT = "/System/Library/Fonts/Menlo.ttc"

PAPER = (0.831, 0.831, 0.788)
INK = (0.047, 0.061, 0.050)
OIL_GREEN = (0.112, 0.216, 0.220)
VERMILION = (0.815, 0.345, 0.260)
PLOT_THICKNESS = 0.001


@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) * 0.5, (self.y0 + self.y1) * 0.5)


@dataclass(frozen=True)
class Layout:
    leaves: tuple[tuple[str, Rect], ...]
    nodes: tuple[tuple[str, Rect], ...]
    edges: tuple[tuple[str, str], ...]
    seams: tuple[tuple[tuple[float, float], tuple[float, float]], ...]

    def leaf_map(self) -> dict[str, Rect]:
        return dict(self.leaves)

    def node_map(self) -> dict[str, Rect]:
        return dict(self.nodes)


# Every split is a relative decision.  Small seed-dependent perturbations turn
# this fixed grammar into related field states without moving any copied pixel.
_SPLITS: dict[str, tuple[str, float]] = {
    "": ("h", 0.5556),
    "T": ("h", 0.5258),
    "TT": ("v", 0.1759),
    "TTR": ("v", 0.4083),
    "TB": ("v", 0.2678),
    "TBR": ("v", 0.5535),
    "B": ("h", 0.5008),
    "BT": ("v", 0.5124),
    "BB": ("v", 0.4101),
}

_VOID_PATH = "TTL"


def _ratio(path: str, base: float, *, seed: int, variant: int) -> float:
    del path, seed, variant
    return base


@lru_cache(maxsize=16)
def _layout(seed: int, variant: int) -> Layout:
    leaves: list[tuple[str, Rect]] = []
    nodes: list[tuple[str, Rect]] = []
    edges: list[tuple[str, str]] = []
    seams: list[tuple[tuple[float, float], tuple[float, float]]] = []

    def visit(path: str, rect: Rect) -> None:
        split = _SPLITS.get(path)
        if split is None:
            leaves.append((path, rect))
            return

        nodes.append((path, rect))
        orientation, base = split
        ratio = _ratio(path, base, seed=seed, variant=variant)
        if orientation == "h":
            y = rect.y0 + rect.height * ratio
            first = Rect(rect.x0, rect.y0, rect.x1, y)
            second = Rect(rect.x0, y, rect.x1, rect.y1)
            first_path, second_path = f"{path}T", f"{path}B"
            # T/TT separates the omitted upper-left field.  Its exposed piece
            # is drawn by the L-shaped outer boundary, so keep only the active
            # internal portion here.
            if path != "T":
                seams.append(((rect.x0, y), (rect.x1, y)))
        else:
            x = rect.x0 + rect.width * ratio
            first = Rect(rect.x0, rect.y0, x, rect.y1)
            second = Rect(x, rect.y0, rect.x1, rect.y1)
            first_path, second_path = f"{path}L", f"{path}R"
            if path != "TT":
                seams.append(((x, rect.y0), (x, rect.y1)))

        edges.extend(((path, first_path), (path, second_path)))
        visit(first_path, first)
        visit(second_path, second)

    visit("", Rect(47.5, 6.2, 135.8, 185.25))

    leaf_map = dict(leaves)
    void = leaf_map[_VOID_PATH]
    # The lower edge of the omitted cell continues as the top-left exposure;
    # its remaining right-hand span is the internal seam between active cells.
    seams.append(((void.x1, void.y1), (135.8, void.y1)))

    return Layout(tuple(leaves), tuple(nodes), tuple(edges), tuple(seams))


def _pack(lines: list[list[tuple[float, float]]]) -> tuple[np.ndarray, np.ndarray]:
    coords: list[tuple[float, float, float]] = []
    offsets = [0]
    for line in lines:
        if len(line) < 2:
            continue
        coords.extend((float(x), float(y), 0.0) for x, y in line)
        offsets.append(len(coords))
    return (
        np.ascontiguousarray(coords, dtype=np.float32).reshape((-1, 3)),
        np.ascontiguousarray(offsets, dtype=np.int32),
    )


def _circle(
    center: tuple[float, float], radius: float, samples: int = 28
) -> list[tuple[float, float]]:
    cx, cy = center
    return [
        (
            cx + radius * math.cos(2.0 * math.pi * index / samples),
            cy + radius * math.sin(2.0 * math.pi * index / samples),
        )
        for index in range(samples + 1)
    ]


def _inset(rect: Rect, amount: float = 0.75) -> Rect:
    return Rect(
        rect.x0 + amount,
        rect.y0 + amount,
        rect.x1 - amount,
        rect.y1 - amount,
    )


def _strata_lines(
    rect: Rect,
    *,
    spacing: float,
    amplitude: float,
    phase: float,
    step_kink: bool = False,
) -> list[list[tuple[float, float]]]:
    if spacing <= 0.0:
        raise ValueError("strata spacing must be positive")
    region = _inset(rect, 0.75)
    lines: list[list[tuple[float, float]]] = []
    y = region.y0 + 0.25
    row = 0
    while y <= region.y1 - 0.2:
        if step_kink:
            kink = region.x0 + region.width * (
                0.43 + 0.06 * math.sin(row * 0.37 + phase)
            )
            shift = amplitude * (0.18 + 0.82 / (1.0 + math.exp(-(row - 18) * 0.28)))
            lines.append(
                [
                    (region.x0, y),
                    (kink - 1.2, y),
                    (kink + 1.1, min(region.y1, y + shift)),
                    (region.x1, min(region.y1, y + shift)),
                ]
            )
        else:
            points: list[tuple[float, float]] = []
            for x in np.linspace(region.x0, region.x1, 40):
                envelope = math.sin(
                    math.pi * (float(x) - region.x0) / max(1e-6, region.width)
                )
                yy = y + amplitude * envelope * math.sin(
                    float(x) * 0.17 + row * 0.13 + phase
                )
                yy += 0.34 * amplitude * math.sin(float(x) * 0.043 - row * 0.21)
                points.append((float(x), min(region.y1, max(region.y0, yy))))
            lines.append(points)
        y += spacing
        row += 1
    return lines


def _vertical_hatch(
    rect: Rect,
    spacing: float,
    phase: float,
    amplitude: float,
    frequency: float,
) -> list[list[tuple[float, float]]]:
    if spacing <= 0.0:
        raise ValueError("vertical line spacing must be positive")
    region = _inset(rect, 0.75)
    lines: list[list[tuple[float, float]]] = []
    x = region.x0 + 0.3
    column = 0
    while x <= region.x1 - 0.2:
        drift = amplitude * math.sin(column * frequency + phase)
        lines.append([(x, region.y0), (x + drift, region.y1)])
        x += spacing
        column += 1
    return lines


def _stipple(
    rect: Rect,
    *,
    seed: int,
    spacing: float = 1.72,
    length_scale: float = 1.0,
    angle_spread: float = 0.28,
    jitter_scale: float = 1.0,
) -> list[list[tuple[float, float]]]:
    if spacing <= 0.0:
        raise ValueError("short-line spacing must be positive")
    if angle_spread < 0.0:
        raise ValueError("short-line angle spread must be non-negative")
    region = _inset(rect, 1.0)
    rng = np.random.default_rng(seed)
    lines: list[list[tuple[float, float]]] = []
    y = region.y0 + 1.2
    row = 0
    while y < region.y1 - 0.7:
        x = region.x0 + 1.0 + (row % 2) * spacing * 0.43
        while x < region.x1 - 0.7:
            angle = float(rng.uniform(-angle_spread, angle_spread))
            length = float(rng.uniform(0.42, 0.92)) * length_scale
            jx = float(rng.uniform(-0.20, 0.20)) * jitter_scale
            jy = float(rng.uniform(-0.18, 0.18)) * jitter_scale
            dx = math.sin(angle) * length * 0.5
            dy = math.cos(angle) * length * 0.5
            lines.append([(x + jx - dx, y + jy - dy), (x + jx + dx, y + jy + dy)])
            x += spacing
        y += spacing * 0.92
        row += 1
    return lines


def _dense_mass(
    rect: Rect, spacing: float, phase: float, wobble: float
) -> list[list[tuple[float, float]]]:
    if spacing <= 0.0:
        raise ValueError("dense mass spacing must be positive")
    region = _inset(rect, 0.55)
    lines: list[list[tuple[float, float]]] = []
    y = region.y0
    row = 0
    while y <= region.y1:
        offset = wobble * math.sin(row * 0.31 + phase)
        lines.append([(region.x0, y), (region.x1, min(region.y1, y + offset))])
        y += spacing
        row += 1
    return lines


def _sparse_ticks(
    rect: Rect,
    seed: int,
    *,
    density: float,
    length_scale: float,
    rotation: float,
) -> list[list[tuple[float, float]]]:
    if density < 0.0:
        raise ValueError("short-line density must be non-negative")
    region = _inset(rect, 1.15)
    rng = np.random.default_rng(seed)
    lines: list[list[tuple[float, float]]] = []
    base_count = max(28, int(region.width * region.height / 23.0))
    count = max(1, int(round(base_count * density)))
    for _ in range(count):
        x = float(rng.uniform(region.x0, region.x1))
        y = float(rng.uniform(region.y0, region.y1))
        length = float(rng.uniform(0.45, 1.15)) * length_scale
        angle = float(rng.choice((-0.45, 0.18, 0.72))) + rotation
        lines.append(
            [(x, y), (x + length * math.cos(angle), y + length * math.sin(angle))]
        )
    return lines


def _horizontal_guides(
    rect: Rect, spacing: float = 3.2
) -> list[list[tuple[float, float]]]:
    if spacing <= 0.0:
        raise ValueError("horizontal guide spacing must be positive")
    region = _inset(rect, 0.55)
    y = region.y0 + 1.4
    lines: list[list[tuple[float, float]]] = []
    while y < region.y1:
        lines.append([(region.x0, y), (region.x1, y)])
        y += spacing
    return lines


def _branch_parts(seed: int, variant: int) -> tuple[
    list[list[tuple[float, float]]],
    list[list[tuple[float, float]]],
    list[list[tuple[float, float]]],
    list[list[tuple[float, float]]],
]:
    """Return the reference-traced specimen as plotter-native polylines."""

    del seed, variant

    major = [
        [
            (93.17, 185.23),
            (93.10, 181.99),
            (93.10, 173.83),
            (95.48, 168.34),
            (96.75, 163.41),
            (95.34, 155.53),
            (92.82, 151.31),
            (93.10, 146.52),
            (93.38, 144.83),
            (93.80, 136.25),
            (97.45, 130.76),
            (97.87, 130.62),
            (98.01, 125.83),
            (99.42, 123.58),
            (100.26, 121.19),
            (99.98, 114.43),
            (97.17, 108.52),
            (97.31, 106.55),
            (97.45, 104.86),
            (97.73, 101.76),
            (95.48, 94.58),
            (95.20, 87.12),
            (98.29, 80.51),
            (98.15, 75.44),
            (96.05, 71.22),
            (95.91, 69.25),
            (96.47, 67.14),
            (101.24, 64.60),
            (103.91, 59.26),
            (103.77, 57.71),
            (103.77, 50.53),
            (105.59, 48.28),
            (109.95, 46.87),
            (110.09, 46.45),
            (110.65, 42.51),
            (112.90, 37.58),
            (110.65, 31.95),
            (114.58, 30.40),
            (118.23, 25.48),
            (123.43, 24.21),
            (128.06, 17.17),
            (129.24, 15.61),
        ]
    ]
    spine = major[0]
    major = [spine[:9], spine[8:29], spine[28:]]

    top_branches = [
        [
            (110.65, 31.95),
            (110.23, 28.57),
            (107.70, 25.62),
            (106.86, 22.80),
            (106.44, 21.65),
        ],
        [(112.90, 37.58), (114.72, 37.30), (117.46, 35.96)],
        [
            (110.09, 46.45),
            (111.21, 46.03),
            (111.77, 45.74),
            (114.30, 44.34),
            (118.37, 44.34),
            (120.76, 42.65),
            (123.01, 41.10),
        ],
        [(103.77, 50.53), (103.35, 49.12), (102.65, 47.14)],
        [
            (95.91, 69.25),
            (90.57, 65.31),
            (88.88, 59.26),
            (88.60, 57.99),
            (87.90, 55.60),
            (89.45, 52.50),
            (88.60, 48.28),
            (88.60, 44.62),
            (88.39, 42.58),
        ],
        [(87.90, 55.60), (86.78, 54.61), (86.08, 53.63), (85.18, 52.59)],
        [(110.79, 59.26), (111.49, 57.64), (112.47, 55.60)],
    ]

    central_branches = [
        [
            (95.20, 87.12),
            (94.22, 86.70),
            (89.73, 82.76),
            (89.45, 80.23),
            (86.22, 77.69),
            (85.80, 74.46),
            (84.81, 73.47),
            (82.33, 70.81),
        ],
        [(89.73, 82.76), (87.50, 83.00), (85.65, 83.32)],
        [
            (98.29, 80.51),
            (102.65, 79.66),
            (103.63, 78.54),
            (103.91, 77.55),
            (102.79, 75.30),
            (102.93, 73.75),
            (102.63, 71.38),
        ],
        [
            (97.73, 101.76),
            (98.43, 101.62),
            (101.66, 98.67),
            (102.79, 96.98),
            (102.22, 92.75),
            (102.00, 90.60),
        ],
    ]

    red_branches = [
        [
            (112.47, 55.60),
            (111.49, 57.64),
            (110.79, 59.26),
            (109.24, 61.37),
            (109.24, 61.93),
            (109.67, 66.72),
            (111.49, 70.09),
            (110.93, 74.60),
            (107.70, 77.84),
        ],
        [(111.49, 70.09), (114.58, 68.40), (115.84, 65.87), (116.62, 64.67)],
        [
            (108.12, 106.41),
            (108.68, 105.14),
            (111.49, 102.75),
            (112.05, 96.98),
            (113.60, 95.01),
            (116.55, 92.75),
            (118.93, 92.05),
            (120.06, 89.80),
            (122.44, 87.41),
            (122.87, 83.32),
            (124.55, 79.81),
            (124.97, 75.58),
            (127.50, 72.49),
            (128.45, 71.50),
        ],
        [
            (111.49, 102.75),
            (115.28, 102.04),
            (118.23, 102.18),
            (121.60, 99.93),
            (122.52, 99.45),
        ],
        [(116.55, 92.75), (116.83, 89.10), (116.83, 88.00)],
        [(118.93, 92.05), (123.29, 91.63), (126.66, 88.11), (128.03, 86.80)],
    ]

    lower_branches = [
        [
            (100.26, 121.19),
            (100.96, 121.05),
            (106.58, 116.12),
            (106.58, 110.49),
            (108.12, 106.41),
        ],
        [
            (97.87, 130.62),
            (102.50, 130.76),
            (104.61, 127.66),
            (107.28, 125.69),
            (110.79, 125.27),
            (114.02, 121.61),
            (114.02, 118.37),
            (115.14, 115.70),
            (115.82, 114.29),
        ],
        [
            (96.75, 163.41),
            (97.59, 163.27),
            (98.57, 161.86),
            (100.26, 160.46),
            (101.66, 157.22),
            (101.10, 152.01),
            (102.36, 149.76),
            (104.47, 148.91),
            (106.30, 146.52),
        ],
        [
            (106.30, 146.52),
            (107.56, 144.97),
            (108.68, 143.14),
            (108.68, 139.77),
            (111.07, 134.98),
            (111.48, 133.99),
        ],
        [
            (106.30, 146.52),
            (110.65, 144.97),
            (112.19, 143.71),
            (115.70, 143.57),
            (118.37, 140.61),
            (119.07, 139.20),
            (119.21, 136.11),
            (121.74, 132.73),
            (123.01, 130.19),
            (124.34, 128.01),
        ],
        [(101.66, 157.22), (104.89, 156.37), (106.58, 154.68), (108.12, 153.45)],
        [
            (93.10, 181.99),
            (93.94, 181.85),
            (96.33, 179.03),
            (98.15, 178.19),
            (100.54, 175.52),
            (105.59, 174.39),
            (107.98, 170.17),
        ],
        [(107.98, 170.17), (112.19, 169.04), (114.58, 165.38), (116.27, 161.02)],
        [(107.98, 170.17), (107.84, 167.21), (108.82, 164.68), (109.29, 162.86)],
    ]

    wave_branches = [
        [
            (93.80, 136.25),
            (92.25, 137.94),
            (88.88, 133.85),
            (88.74, 129.35),
            (86.08, 126.25),
            (84.39, 122.59),
            (84.25, 118.79),
            (85.80, 115.27),
            (85.09, 111.47),
            (86.36, 108.94),
            (86.08, 106.27),
            (85.94, 104.86),
            (86.22, 101.62),
            (85.65, 99.51),
            (83.13, 97.40),
            (81.44, 94.44),
            (80.70, 93.19),
        ],
        [
            (84.39, 122.59),
            (83.83, 122.59),
            (80.88, 120.62),
            (77.09, 120.48),
            (74.00, 117.39),
            (73.05, 116.40),
        ],
        [(88.74, 129.35), (89.59, 128.08), (89.52, 125.65)],
        [
            (92.82, 151.31),
            (87.62, 146.10),
            (86.50, 144.97),
            (85.37, 142.72),
            (83.13, 140.89),
            (78.49, 139.91),
            (77.37, 138.78),
            (76.81, 137.94),
            (77.09, 134.98),
            (76.53, 133.15),
            (76.06, 131.76),
        ],
        [
            (86.08, 106.27),
            (85.37, 105.14),
            (82.99, 104.30),
            (82.14, 103.59),
            (80.97, 102.26),
        ],
    ]

    secondary = (
        top_branches + central_branches + red_branches + lower_branches + wave_branches
    )

    def dashed(
        start: tuple[float, float], end: tuple[float, float]
    ) -> list[list[tuple[float, float]]]:
        x0, y0 = start
        x1, y1 = end
        length = math.hypot(x1 - x0, y1 - y0)
        ux = (x1 - x0) / length
        uy = (y1 - y0) / length
        lines: list[list[tuple[float, float]]] = []
        cursor = 0.0
        while cursor < length:
            stop = min(length, cursor + 0.42)
            lines.append(
                [(x0 + ux * cursor, y0 + uy * cursor), (x0 + ux * stop, y0 + uy * stop)]
            )
            cursor += 0.80
        return lines

    tertiary: list[list[tuple[float, float]]] = []
    for start, end in (
        ((103.63, 77.90), (107.70, 77.84)),
        ((102.79, 96.98), (112.05, 96.98)),
        ((100.30, 124.00), (113.80, 128.00)),
    ):
        tertiary.extend(dashed(start, end))

    terminals = [
        ((88.39, 42.58), 0.82),
        ((85.18, 52.59), 0.78),
        ((106.44, 21.65), 0.84),
        ((129.24, 15.61), 1.18),
        ((117.46, 35.96), 0.55),
        ((123.01, 41.10), 0.65),
        ((102.65, 47.14), 0.58),
        ((112.47, 55.60), 0.67),
        ((82.33, 70.81), 0.68),
        ((85.65, 83.32), 0.48),
        ((80.70, 93.19), 1.02),
        ((80.97, 102.26), 0.48),
        ((102.63, 71.38), 0.58),
        ((102.00, 90.60), 0.57),
        ((73.05, 116.40), 0.72),
        ((89.52, 125.65), 0.48),
        ((76.06, 131.76), 0.55),
        ((115.82, 114.29), 0.66),
        ((124.34, 128.01), 0.68),
        ((111.48, 133.99), 0.60),
        ((108.12, 153.45), 0.66),
        ((116.27, 161.02), 0.64),
        ((109.29, 162.86), 0.61),
        ((116.62, 64.67), 0.66),
        ((128.45, 71.50), 0.68),
        ((128.03, 86.80), 1.04),
        ((116.83, 88.00), 0.60),
        ((122.52, 99.45), 0.66),
    ]
    markers: list[list[tuple[float, float]]] = []
    for point, radius in terminals:
        markers.append(_circle(point, max(0.62, radius * 1.25), 32))
        markers.append(_circle(point, 0.24, 16))
    for radius in (0.72, 0.48, 0.24):
        markers.append(_circle((93.17, 185.23), radius, 26))
    return major, secondary, tertiary, markers


def _guide_lines(layout: Layout) -> list[list[tuple[float, float]]]:
    leaf_map = layout.leaf_map()
    void = leaf_map[_VOID_PATH]
    lines: list[list[tuple[float, float]]] = []

    # L-shaped exposed perimeter of the active BSP region.
    lines.append(
        [
            (void.x1, 6.2),
            (135.8, 6.2),
            (135.8, 185.25),
            (47.5, 185.25),
            (47.5, void.y1),
            (void.x1, void.y1),
            (void.x1, 6.2),
        ]
    )
    for a, b in layout.seams:
        lines.append([a, b])
    return lines


def _registration() -> list[list[tuple[float, float]]]:
    lines: list[list[tuple[float, float]]] = []
    for x, y in ((47.5, 6.5), (141.0, 6.5), (8.3, 100.0), (141.0, 201.2)):
        lines.extend(([(x - 1.7, y), (x + 1.7, y)], [(x, y - 1.7), (x, y + 1.7)]))
    lines.extend(
        (
            [(6.8, 10.2), (13.4, 10.2)],
            [(6.8, 137.8), (9.0, 137.8)],
            [(6.8, 155.8), (9.0, 155.8)],
            [(6.8, 178.7), (9.0, 178.7)],
        )
    )

    # Right depth scale.
    lines.append([(140.0, 15.0), (140.0, 186.0)])
    # Five-unit minor ticks place every 25-unit label on a major tick.
    for index in range(31):
        y = 186.0 - index * (171.0 / 30.0)
        tick = 1.45 if index % 5 == 0 else 0.75
        lines.append([(140.0 - tick, y), (140.0, y)])

    # Bottom distance scale, 0..150 mm.
    lines.append([(7.0, 201.0), (36.0, 201.0)])
    for index in range(16):
        x = 7.0 + index * (29.0 / 15.0)
        tick = 1.8 if index % 5 == 0 else 0.85
        lines.append([(x, 201.0), (x, 201.0 - tick)])

    # Orientation fan uses the same five allowed growth headings as the tree.
    origin = FAN_ORIGIN
    arc: list[tuple[float, float]] = []
    for degree in np.linspace(
        180.0 + FAN_ANGLE_VALUES[0], 180.0 + FAN_ANGLE_VALUES[-1], 61
    ):
        angle = math.radians(float(degree))
        arc.append(
            (
                origin[0] + FAN_RADIUS * math.cos(angle),
                origin[1] + FAN_RADIUS * math.sin(angle),
            )
        )
    lines.append(arc)
    # Values are protractor angles measured from the left baseline.
    for value in FAN_ANGLE_VALUES:
        angle = math.radians(180.0 + value)
        lines.append(
            [
                origin,
                (
                    origin[0] + FAN_RADIUS * math.cos(angle),
                    origin[1] + FAN_RADIUS * math.sin(angle),
                ),
            ]
        )
    lines.append(_circle(origin, 0.45, 18))
    return lines


@primitive(
    meta={
        "spacing": {
            "kind": "float",
            "ui_min": 0.10,
            "ui_max": 1.20,
            "display_name": "Line Spacing",
            "description": "Distance between plotter lines; smaller values make the red field denser.",
            "unit": "mm",
            "step": 0.01,
            "format": "%.2f",
            "category": "Field Pattern",
            "recommended_range": (0.16, 0.50),
        },
        "wobble": {
            "kind": "float",
            "ui_min": 0.0,
            "ui_max": 0.80,
            "display_name": "Line Wobble",
            "description": "Vertical displacement of each red fill line.",
            "unit": "mm",
            "step": 0.01,
            "format": "%.2f",
            "category": "Field Pattern",
            "recommended_range": (0.01, 0.30),
        },
        "phase_offset": {
            "kind": "float",
            "ui_min": -3.14,
            "ui_max": 3.14,
            "display_name": "Phase",
            "description": "Phase offset applied to the red line wobble.",
            "unit": "rad",
            "step": 0.05,
            "format": "%.2f",
            "category": "Field Pattern",
            "advanced": True,
        },
    }
)
def fault_garden_red_area(
    *,
    seed: int = SEED,
    variant: int = VARIANT,
    spacing: float = 0.22,
    wobble: float = 0.10,
    phase_offset: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """赤色領域を構成する高密度な水平線群を生成する。

    Parameters
    ----------
    seed : int, optional
        模様の基準位相に使用するシード。
    variant : int, optional
        模様の基準位相に加えるバリエーション番号。
    spacing : float, optional
        隣接する線の間隔。小さいほど領域が濃くなる。
    wobble : float, optional
        各水平線へ加える垂直方向の揺れ幅。
    phase_offset : float, optional
        基準位相へ加えるオフセット。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        Grafix形式の座標配列とオフセット配列。
    """

    cells = _layout(int(seed), int(variant)).leaf_map()
    phase = float(seed) * 0.017 + float(variant) * 0.43 + float(phase_offset)
    return _pack(
        _dense_mass(
            cells["TBRR"],
            spacing=float(spacing),
            phase=phase,
            wobble=float(wobble),
        )
    )


@primitive(
    meta={
        "spacing": {
            "kind": "float",
            "ui_min": 0.10,
            "ui_max": 1.20,
            "display_name": "Line Spacing",
            "description": "Distance between plotter lines; smaller values make the green field denser.",
            "unit": "mm",
            "step": 0.01,
            "format": "%.2f",
            "category": "Field Pattern",
            "recommended_range": (0.16, 0.50),
        },
        "wobble": {
            "kind": "float",
            "ui_min": 0.0,
            "ui_max": 0.80,
            "display_name": "Line Wobble",
            "description": "Vertical displacement of each green fill line.",
            "unit": "mm",
            "step": 0.01,
            "format": "%.2f",
            "category": "Field Pattern",
            "recommended_range": (0.01, 0.30),
        },
        "guide_spacing": {
            "kind": "float",
            "ui_min": 1.0,
            "ui_max": 12.0,
            "display_name": "Guide Spacing",
            "description": "Distance between the wider horizontal guide lines.",
            "unit": "mm",
            "step": 0.10,
            "format": "%.2f",
            "category": "Field Pattern",
            "recommended_range": (2.0, 6.0),
        },
        "phase_offset": {
            "kind": "float",
            "ui_min": -3.14,
            "ui_max": 3.14,
            "display_name": "Phase",
            "description": "Phase offset applied to the green line wobble.",
            "unit": "rad",
            "step": 0.05,
            "format": "%.2f",
            "category": "Field Pattern",
            "advanced": True,
        },
    }
)
def fault_garden_green_area(
    *,
    seed: int = SEED,
    variant: int = VARIANT,
    spacing: float = 0.19,
    wobble: float = 0.10,
    guide_spacing: float = 3.2,
    phase_offset: float = 0.4,
) -> tuple[np.ndarray, np.ndarray]:
    """緑色領域の高密度線と計測ガイド線を生成する。

    Parameters
    ----------
    seed : int, optional
        模様の基準位相に使用するシード。
    variant : int, optional
        模様の基準位相に加えるバリエーション番号。
    spacing : float, optional
        隣接する高密度線の間隔。小さいほど領域が濃くなる。
    wobble : float, optional
        各水平線へ加える垂直方向の揺れ幅。
    guide_spacing : float, optional
        太い間隔で配置する計測ガイド線同士の距離。
    phase_offset : float, optional
        基準位相へ加えるオフセット。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        Grafix形式の座標配列とオフセット配列。
    """

    cells = _layout(int(seed), int(variant)).leaf_map()
    phase = float(seed) * 0.017 + float(variant) * 0.43 + float(phase_offset)
    lines = _dense_mass(
        cells["BBL"],
        spacing=float(spacing),
        phase=phase,
        wobble=float(wobble),
    )
    lines += _horizontal_guides(cells["BBL"], spacing=float(guide_spacing))
    return _pack(lines)


@primitive(
    meta={
        "spacing": {
            "kind": "float",
            "ui_min": 0.25,
            "ui_max": 3.0,
            "display_name": "Wave Spacing",
            "description": "Distance between adjacent strata lines; smaller values increase density.",
            "unit": "mm",
            "step": 0.01,
            "format": "%.2f",
            "category": "Field Pattern",
            "recommended_range": (0.50, 1.50),
        },
        "amplitude": {
            "kind": "float",
            "ui_min": 0.0,
            "ui_max": 3.0,
            "display_name": "Wave Amplitude",
            "description": "Vertical height of the strata deformation.",
            "unit": "mm",
            "step": 0.05,
            "format": "%.2f",
            "category": "Field Pattern",
            "recommended_range": (0.40, 1.60),
        },
        "phase_offset": {
            "kind": "float",
            "ui_min": -3.14,
            "ui_max": 3.14,
            "display_name": "Phase",
            "description": "Horizontal phase offset of the strata pattern.",
            "unit": "rad",
            "step": 0.05,
            "format": "%.2f",
            "category": "Field Pattern",
            "advanced": True,
        },
        "step_kink": {
            "kind": "bool",
            "display_name": "Stepped Profile",
            "description": "Use a stepped geological displacement instead of smooth waves.",
            "category": "Field Pattern",
        },
    }
)
def fault_garden_wave_pattern(
    *,
    cell_path: str = "BTL",
    seed: int = SEED,
    variant: int = VARIANT,
    spacing: float = 0.76,
    amplitude: float = 1.22,
    phase_offset: float = 0.0,
    step_kink: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """GUIから独立調整できる一つの地層波形を生成する。

    Parameters
    ----------
    cell_path : str, optional
        BSPレイアウト内で波形を配置するセルの識別子。
    seed : int, optional
        模様の基準位相に使用するシード。
    variant : int, optional
        模様の基準位相に加えるバリエーション番号。
    spacing : float, optional
        隣接する波形線の間隔。
    amplitude : float, optional
        波形または段差の垂直方向の高さ。
    phase_offset : float, optional
        基準位相へ加えるオフセット。
    step_kink : bool, optional
        Trueの場合は滑らかな波形ではなく段差形状を生成する。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        Grafix形式の座標配列とオフセット配列。
    """

    cells = _layout(int(seed), int(variant)).leaf_map()
    phase = float(seed) * 0.017 + float(variant) * 0.43 + float(phase_offset)
    return _pack(
        _strata_lines(
            cells[cell_path],
            spacing=float(spacing),
            amplitude=float(amplitude),
            phase=phase,
            step_kink=bool(step_kink),
        )
    )


@primitive(
    meta={
        "spacing": {
            "kind": "float",
            "ui_min": 0.20,
            "ui_max": 2.0,
            "display_name": "Line Spacing",
            "description": "Distance between adjacent vertical plotter lines.",
            "unit": "mm",
            "step": 0.01,
            "format": "%.2f",
            "category": "Field Pattern",
            "recommended_range": (0.40, 1.20),
        },
        "amplitude": {
            "kind": "float",
            "ui_min": 0.0,
            "ui_max": 1.50,
            "display_name": "Wave Drift",
            "description": "Horizontal displacement at the bottom of each vertical line.",
            "unit": "mm",
            "step": 0.01,
            "format": "%.2f",
            "category": "Field Pattern",
            "recommended_range": (0.05, 0.60),
        },
        "frequency": {
            "kind": "float",
            "ui_min": 0.05,
            "ui_max": 2.0,
            "display_name": "Wave Frequency",
            "description": "Rate at which the line drift oscillates across the field.",
            "unit": "rad/line",
            "step": 0.01,
            "format": "%.2f",
            "category": "Field Pattern",
            "recommended_range": (0.25, 0.90),
        },
        "phase_offset": {
            "kind": "float",
            "ui_min": -3.14,
            "ui_max": 3.14,
            "display_name": "Phase",
            "description": "Phase offset of the vertical-line wave.",
            "unit": "rad",
            "step": 0.05,
            "format": "%.2f",
            "category": "Field Pattern",
            "advanced": True,
        },
    }
)
def fault_garden_vertical_line_wave(
    *,
    seed: int = SEED,
    variant: int = VARIANT,
    spacing: float = 0.67,
    amplitude: float = 0.24,
    frequency: float = 0.55,
    phase_offset: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """縦線で構成する波形フィールドを生成する。

    Parameters
    ----------
    seed : int, optional
        模様の基準位相に使用するシード。
    variant : int, optional
        模様の基準位相に加えるバリエーション番号。
    spacing : float, optional
        隣接する縦線の間隔。
    amplitude : float, optional
        各縦線の終端へ加える水平方向の変位幅。
    frequency : float, optional
        列方向に変位を反復する周波数。
    phase_offset : float, optional
        基準位相へ加えるオフセット。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        Grafix形式の座標配列とオフセット配列。
    """

    cells = _layout(int(seed), int(variant)).leaf_map()
    phase = float(seed) * 0.017 + float(variant) * 0.43 + float(phase_offset)
    return _pack(
        _vertical_hatch(
            cells["BTR"],
            spacing=float(spacing),
            phase=phase,
            amplitude=float(amplitude),
            frequency=float(frequency),
        )
    )


@primitive(
    meta={
        "spacing": {
            "kind": "float",
            "ui_min": 0.60,
            "ui_max": 5.0,
            "display_name": "Mark Spacing",
            "description": "Distance between the regularly scattered short lines.",
            "unit": "mm",
            "step": 0.01,
            "format": "%.2f",
            "category": "Field Pattern",
            "recommended_range": (1.20, 2.80),
        },
        "length_scale": {
            "kind": "float",
            "ui_min": 0.25,
            "ui_max": 2.50,
            "display_name": "Mark Length",
            "description": "Scale applied to the length of every short line.",
            "unit": "×",
            "step": 0.01,
            "format": "%.2f",
            "category": "Field Pattern",
            "recommended_range": (0.60, 1.50),
        },
        "angle_spread": {
            "kind": "float",
            "ui_min": 0.0,
            "ui_max": 1.20,
            "display_name": "Angle Spread",
            "description": "Maximum angular deviation of each short line.",
            "unit": "rad",
            "step": 0.01,
            "format": "%.2f",
            "category": "Field Pattern",
            "recommended_range": (0.05, 0.60),
        },
        "jitter_scale": {
            "kind": "float",
            "ui_min": 0.0,
            "ui_max": 2.50,
            "display_name": "Position Jitter",
            "description": "Scale applied to the random displacement of each mark.",
            "unit": "×",
            "step": 0.01,
            "format": "%.2f",
            "category": "Field Pattern",
            "recommended_range": (0.50, 1.50),
            "advanced": True,
        },
    }
)
def fault_garden_central_dash_scatter(
    *,
    seed: int = SEED,
    variant: int = VARIANT,
    spacing: float = 1.72,
    length_scale: float = 1.0,
    angle_spread: float = 0.28,
    jitter_scale: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """中央領域へ格子状に短線を散布する。

    Parameters
    ----------
    seed : int, optional
        短線の向き、長さ、位置の乱数シード。
    variant : int, optional
        BSPレイアウトを選択するバリエーション番号。
    spacing : float, optional
        短線を配置する格子の間隔。
    length_scale : float, optional
        短線の長さへ掛ける倍率。
    angle_spread : float, optional
        短線の角度を散らす最大幅。
    jitter_scale : float, optional
        格子位置へ加えるランダム変位の倍率。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        Grafix形式の座標配列とオフセット配列。
    """

    cells = _layout(int(seed), int(variant)).leaf_map()
    return _pack(
        _stipple(
            cells["TBRL"],
            seed=int(seed) + 31,
            spacing=float(spacing),
            length_scale=float(length_scale),
            angle_spread=float(angle_spread),
            jitter_scale=float(jitter_scale),
        )
    )


@primitive(
    meta={
        "density": {
            "kind": "float",
            "ui_min": 0.10,
            "ui_max": 3.0,
            "display_name": "Mark Density",
            "description": "Multiplier applied to the number of lower-right short lines.",
            "unit": "×",
            "step": 0.01,
            "format": "%.2f",
            "category": "Field Pattern",
            "recommended_range": (0.50, 1.75),
        },
        "length_scale": {
            "kind": "float",
            "ui_min": 0.25,
            "ui_max": 2.50,
            "display_name": "Mark Length",
            "description": "Scale applied to the length of every short line.",
            "unit": "×",
            "step": 0.01,
            "format": "%.2f",
            "category": "Field Pattern",
            "recommended_range": (0.60, 1.50),
        },
        "rotation": {
            "kind": "float",
            "ui_min": -3.14,
            "ui_max": 3.14,
            "display_name": "Rotation",
            "description": "Rotation added to the three allowed mark headings.",
            "unit": "rad",
            "step": 0.05,
            "format": "%.2f",
            "category": "Field Pattern",
        },
    }
)
def fault_garden_lower_right_dash_scatter(
    *,
    seed: int = SEED,
    variant: int = VARIANT,
    density: float = 1.0,
    length_scale: float = 1.0,
    rotation: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """右下領域へランダムな短線を散布する。

    Parameters
    ----------
    seed : int, optional
        短線の位置、長さ、向きの乱数シード。
    variant : int, optional
        BSPレイアウトを選択するバリエーション番号。
    density : float, optional
        基準本数へ掛ける短線密度の倍率。
    length_scale : float, optional
        短線の長さへ掛ける倍率。
    rotation : float, optional
        三つの基準角度へ加える回転量。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        Grafix形式の座標配列とオフセット配列。
    """

    cells = _layout(int(seed), int(variant)).leaf_map()
    return _pack(
        _sparse_ticks(
            cells["BBR"],
            seed=int(seed) + 79,
            density=float(density),
            length_scale=float(length_scale),
            rotation=float(rotation),
        )
    )


@primitive
def fault_garden_geometry(
    *, part: int = 0, seed: int = SEED, variant: int = VARIANT
) -> tuple[np.ndarray, np.ndarray]:
    """Return one semantic line family of the recursive atlas."""

    layout = _layout(int(seed), int(variant))
    lines: list[list[tuple[float, float]]]

    if part == 0:
        lines = _guide_lines(layout)
    elif part == 7:
        major, _secondary, _tertiary, _markers = _branch_parts(int(seed), int(variant))
        lines = [major[0]]
    elif part == 8:
        _major, secondary, _tertiary, _markers = _branch_parts(int(seed), int(variant))
        lines = secondary
    elif part == 9:
        _major, _secondary, tertiary, _markers = _branch_parts(int(seed), int(variant))
        lines = tertiary
    elif part == 10:
        _major, _secondary, _tertiary, markers = _branch_parts(int(seed), int(variant))
        lines = markers
    elif part == 11:
        lines = _registration()
    elif part == 12:
        lines = [[(6.3, 162.0), (11.1, 162.0)]]
    elif part == 13:
        lines = [[(6.3, 165.1), (11.1, 165.1)]]
    elif part == 14:
        lines = [[(6.3, 168.2), (11.1, 168.2)]]
    elif part == 15:
        lines = [[(6.3, 171.3), (11.1, 171.3)]]
    elif part == 17:
        major, _secondary, _tertiary, _markers = _branch_parts(int(seed), int(variant))
        lines = [major[1]]
    elif part == 18:
        major, _secondary, _tertiary, _markers = _branch_parts(int(seed), int(variant))
        lines = [major[2]]
    else:
        raise ValueError(f"unknown fault garden part: {part}")
    return _pack(lines)


def _text(
    content: str,
    *,
    x: float,
    y: float,
    scale: float,
    font: str = MONO_FONT,
    font_index: int = 0,
    spacing: float = 0.03,
    line_height: float = 1.25,
    align: str = "left",
):
    return G.text(
        text=content,
        font=font,
        font_index=font_index,
        text_align=align,
        letter_spacing_em=spacing,
        line_height=line_height,
        quality=0.62,
        center=(x, y, 0.0),
        scale=scale,
    )


def _labels():
    title_outline = _text(
        "FAULT GARDEN",
        x=5.3,
        y=90.2,
        scale=4.65,
        font=DISPLAY_FONT,
        font_index=10,
        spacing=0.72,
    )
    title = E(name="Title Fill").fill(
        # activate=False,
        angle_sets=1,
        angle=0.0,
        density=0,
        spacing_gradient=0.0,
        remove_boundary=False,
        key="title_fill",
    )(title_outline)
    title = E(name="Title Rotation").rotate(
        auto_center=False,
        pivot=(5.3, 90.2, 0.0),
        rotation=(0.0, 0.0, -90.0),
        key="title_rotation",
    )(title)

    plate_outline = _text(
        "PLATE 03",
        x=6.3,
        y=4.9,
        scale=2.05,
        font=DISPLAY_FONT,
        font_index=7,
        spacing=0.10,
    )
    plate = E(name="Plate Label Fill").fill(
        angle_sets=2,
        angle=0.0,
        density=0,
        spacing_gradient=0.0,
        remove_boundary=False,
        key="plate_label_fill",
    )(plate_outline)
    cut = _text(
        f"CUT 03\nSEED {SEED:03d}\nSCALE 1:5",
        x=6.3,
        y=124.7,
        scale=1.95,
        font=MONO_FONT,
        font_index=0,
        spacing=0.02,
        line_height=1.43,
    )
    metadata = _text(
        "REGION   N7°32' / E14°18'\n"
        "STRATUM  IIIb\n"
        "EPOCH    LATE Pleistocene\n"
        "METHOD   Deterministic",
        x=6.3,
        y=139.8,
        scale=1.14,
        font=MONO_FONT,
        font_index=0,
        spacing=0.0,
        line_height=1.56,
    )
    # Keep the legend columns independent.  Space-padding made the labels drift
    # left and compressed the rows because glyph advances are font-dependent.
    rule = [
        _text(
            "RULE SET",
            x=6.3,
            y=158.0,
            scale=1.14,
            font=MONO_FONT,
            font_index=0,
            spacing=0.0,
        ),
        _text(
            "BOUNDARY\nPRIMARY\nSECONDARY\nTERTIARY",
            x=14.7,
            y=161.2,
            scale=1.14,
            font=MONO_FONT,
            font_index=0,
            spacing=0.0,
            line_height=2.72,
        ),
        _text(
            "1.00\n0.50\n0.25\n0.13",
            x=25.2,
            y=161.2,
            scale=1.14,
            font=MONO_FONT,
            font_index=0,
            spacing=0.0,
            line_height=2.72,
        ),
    ]
    right_scale = [
        _text(
            value,
            x=141.0,
            y=y,
            scale=1.23,
            font=MONO_FONT,
            font_index=0,
            spacing=0.0,
        )
        for value, y in (
            ("150", 14.3),
            ("125", 42.8),
            ("100", 71.3),
            ("75", 99.8),
            ("50", 128.3),
            ("25", 156.8),
            ("0mm", 185.3),
        )
    ]
    bottom_scale = [
        _text(
            value,
            x=x,
            y=197.25,
            scale=1.16,
            font=MONO_FONT,
            font_index=0,
            spacing=0.0,
            align="center",
        )
        # Match the advance-box center to each long ruler tick.
        for value, x in (
            ("0", 7.0),
            ("50", 7.0 + 29.0 / 3.0),
            ("100", 7.0 + 58.0 / 3.0),
            ("150mm", 36.0),
        )
    ]
    angles = [
        _text(
            f"{value}°",
            x=x,
            y=y,
            scale=1.05,
            font=MONO_FONT,
            font_index=0,
            spacing=0.0,
            align="center",
        )
        # Each glyph center lies on its ray extension at radius 15.6.
        for value, (x, y) in zip(
            FAN_ANGLE_VALUES,
            (
                (79.7315, 196.8076),
                (85.4426, 191.0976),
                (93.2441, 189.0076),
                (101.0233, 191.0976),
                (106.7333, 196.8076),
            ),
            strict=True,
        )
    ]
    cut_fill = E(name="Cut Block Fill").fill(
        angle_sets=1,
        angle=0.0,
        density=0,
        spacing_gradient=0.0,
        remove_boundary=False,
        key="cut_block_fill",
    )
    metadata_fill = E(name="Metadata Fill").fill(
        angle_sets=1,
        angle=0.0,
        density=0,
        spacing_gradient=0.0,
        remove_boundary=False,
        key="metadata_fill",
    )
    rule_heading_fill = E(name="Rule Heading Fill").fill(
        angle_sets=1,
        angle=0.0,
        density=0,
        spacing_gradient=0.0,
        remove_boundary=False,
        key="rule_heading_fill",
    )
    rule_columns_fill = E(name="Rule Columns Fill").fill(
        angle_sets=1,
        angle=0.0,
        density=0,
        spacing_gradient=0.0,
        remove_boundary=False,
        key="rule_columns_fill",
    )
    scale_label_fill = E(name="Scale and Angle Labels Fill").fill(
        angle_sets=1,
        angle=0.0,
        density=0,
        spacing_gradient=0.0,
        remove_boundary=False,
        key="scale_and_angle_labels_fill",
    )
    return (
        title,
        plate,
        cut_fill(cut),
        metadata_fill(metadata),
        [rule_heading_fill(rule[0])] + [rule_columns_fill(label) for label in rule[1:]],
        [scale_label_fill(label) for label in right_scale],
        [scale_label_fill(label) for label in bottom_scale],
        [scale_label_fill(label) for label in angles],
    )


def draw(t: float):
    del t
    title, plate, cut, metadata, rule, right_scale, bottom_scale, angles = _labels()

    def geometry(part: int):
        return G.fault_garden_geometry(
            part=part, seed=SEED, variant=VARIANT, key=f"fault-garden-{part}"
        )

    red_area = G(name="Red Area Fill").fault_garden_red_area(
        seed=SEED,
        variant=VARIANT,
        spacing=0.22,
        wobble=0.10,
        phase_offset=0.0,
        key="red_area_fill",
    )
    green_area = G(name="Green Area Fill and Guides").fault_garden_green_area(
        seed=SEED,
        variant=VARIANT,
        spacing=0.19,
        wobble=0.10,
        guide_spacing=3.2,
        phase_offset=0.4,
        key="green_area_fill_and_guides",
    )
    upper_waves = G(name="Upper Stepped Waves").fault_garden_wave_pattern(
        cell_path="TTRL",
        seed=SEED,
        variant=VARIANT,
        spacing=0.74,
        amplitude=1.18,
        phase_offset=0.0,
        step_kink=True,
        key="upper_stepped_waves",
    )
    lower_waves = G(name="Lower Smooth Waves").fault_garden_wave_pattern(
        cell_path="BTL",
        seed=SEED,
        variant=VARIANT,
        spacing=0.76,
        amplitude=1.22,
        phase_offset=0.9,
        step_kink=False,
        key="lower_smooth_waves",
    )
    vertical_line_wave = G(name="Vertical Line Wave").fault_garden_vertical_line_wave(
        seed=SEED,
        variant=VARIANT,
        spacing=0.67,
        amplitude=0.24,
        frequency=0.55,
        phase_offset=0.0,
        key="vertical_line_wave",
    )
    contour_region = _inset(
        _layout(SEED, VARIANT).leaf_map()["TTRR"],
        0.85,
    )
    contours = G(name="Topographic Contours").topographic_contours(
        width=contour_region.width,
        height=contour_region.height,
        seed=SEED,
        focus_count=6,
        level_count=13,
        focus_spread=1.0,
        field_warp=1.0,
        warp_frequency=1.0,
        phase=165.0,
        grid_pitch=0.55,
        center=(
            0.5 * (contour_region.x0 + contour_region.x1),
            0.5 * (contour_region.y0 + contour_region.y1),
            0.0,
        ),
        key="topographic_contours",
    )
    central_scatter = G(
        name="Central Short-Line Scatter"
    ).fault_garden_central_dash_scatter(
        seed=SEED,
        variant=VARIANT,
        spacing=1.72,
        length_scale=1.0,
        angle_spread=0.28,
        jitter_scale=1.0,
        key="central_short_line_scatter",
    )
    lower_right_scatter = G(
        name="Lower-Right Short-Line Scatter"
    ).fault_garden_lower_right_dash_scatter(
        seed=SEED,
        variant=VARIANT,
        density=1.0,
        length_scale=1.0,
        rotation=0.0,
        key="lower_right_short_line_scatter",
    )

    root_growth = E(name="Root Growth Emphasis").bold(
        count=7,
        radius=0.22,
        seed=SEED,
        key="root_growth_emphasis",
    )(geometry(7))
    trunk_growth = E(name="Trunk Growth Emphasis").bold(
        count=5,
        radius=0.15,
        seed=SEED + 3,
        key="trunk_growth_emphasis",
    )(geometry(17))
    canopy_growth = E(name="Canopy Growth Emphasis").bold(
        count=3,
        radius=0.10,
        seed=SEED + 4,
        key="canopy_growth_emphasis",
    )(geometry(18))
    secondary = E(name="Secondary Growth Emphasis").bold(
        count=3,
        radius=0.11,
        seed=SEED + 1,
        key="secondary_growth_emphasis",
    )(geometry(8))
    markers = E(name="Sample Node Emphasis").bold(
        count=3,
        radius=0.07,
        seed=SEED + 2,
        key="sample_node_emphasis",
    )(geometry(10))

    # Keep the plotter workflow to one layer per pen colour.  The geometry
    # hierarchy remains in the marks themselves (for example E.bold emits
    # several fine strokes), while every physical pen stroke uses Grafix's
    # default 0.001 line thickness.
    ink = (
        upper_waves,
        lower_waves,
        vertical_line_wave,
        contours,
        central_scatter,
        lower_right_scatter,
        geometry(0),
        root_growth,
        trunk_growth,
        canopy_growth,
        secondary,
        geometry(9),
        markers,
        geometry(11),
        geometry(12),
        geometry(13),
        geometry(14),
        geometry(15),
        title,
        plate,
        cut,
        metadata,
        *rule,
        *right_scale,
        *bottom_scale,
        *angles,
    )
    return (
        L("oil-green").layer(
            green_area, color=OIL_GREEN, thickness=PLOT_THICKNESS
        ),
        L("vermilion").layer(
            red_area, color=VERMILION, thickness=PLOT_THICKNESS
        ),
        L("ink").layer(ink, color=INK, thickness=PLOT_THICKNESS),
    )


if __name__ == "__main__":
    run(
        draw,
        canvas_size=CANVAS,
        render_scale=6.0,
        background_color=PAPER,
        parameter_gui=True,
        parameter_persistence=False,
        midi_port_name=None,
        n_worker=2,
        seed=SEED,
    )
