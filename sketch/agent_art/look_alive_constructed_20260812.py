from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from grafix import E, G, L, primitive, run

# Reference-reproduction adjustment block.  The complete photographed square is
# fitted uniformly to the width of A5 and centred vertically.  Unlike the retained
# scanline version, this file reconstructs the poster from editable vector parts.
CANVAS_SIZE = (148, 210)
BACKGROUND_COLOR = (1.0, 1.0, 1.0)
LINE_THICKNESS = 0.001
SEED = 8122026
LINE_COLORS = {
    "frame_black": (0.0, 0.0, 0.0),
    "paper": (217 / 255, 205 / 255, 191 / 255),
    "sky": (143 / 255, 180 / 255, 192 / 255),
    "haze": (179 / 255, 192 / 255, 195 / 255),
    "ochre": (180 / 255, 128 / 255, 84 / 255),
    "earth": (109 / 255, 83 / 255, 62 / 255),
    "ground_dark": (44 / 255, 29 / 255, 19 / 255),
    "caption": (190 / 255, 139 / 255, 78 / 255),
    "ink": (15 / 255, 14 / 255, 13 / 255),
    "cloth": (237 / 255, 238 / 255, 235 / 255),
    "cloth_pale": (205 / 255, 214 / 255, 214 / 255),
    "cloth_shadow": (171 / 255, 171 / 255, 168 / 255),
    "highlight": (1.0, 1.0, 1.0),
    "fold": (137 / 255, 147 / 255, 149 / 255),
    "orange": (236 / 255, 117 / 255, 61 / 255),
    "cyan": (96 / 255, 207 / 255, 250 / 255),
}
FILL_DENSITIES = {
    "solid": 1000.0,
    "typography": 1000.0,
    "photographic": 760.0,
    "translucent": 380.0,
}
FILL_MIN_SPACINGS = {
    "solid": 0.040,
    "typography": 0.045,
    "photographic": 0.075,
    "translucent": 0.150,
}
REFERENCE_SIZE = (1179.0, 1149.0)
LAYOUT_FIT_SCALE = min(
    CANVAS_SIZE[0] / REFERENCE_SIZE[0],
    CANVAS_SIZE[1] / REFERENCE_SIZE[1],
)
LAYOUT_CROP = (0.0, 0.0, 0.0, 0.0)
LAYOUT_OFFSET = (
    (CANVAS_SIZE[0] - REFERENCE_SIZE[0] * LAYOUT_FIT_SCALE) / 2.0,
    (CANVAS_SIZE[1] - REFERENCE_SIZE[1] * LAYOUT_FIT_SCALE) / 2.0,
)

TITLE_FONT = "/System/Library/Fonts/Supplemental/Rockwell.ttc"
BODY_FONT = "/System/Library/Fonts/HelveticaNeue.ttc"
MONO_FONT = "/System/Library/Fonts/SFNSMono.ttf"

BODY_TEXT = (
        "La resiliencia o entereza es la capacidad para adaptarse a las\n"
        "situaciones adversas con resultados positivos. Sin embargo, el concepto\n"
        "ha experimentado cambios importantes desde la década de los sesenta.\n"
        "En un principio se interpretó como una condición innata luego se enfocó\n"
        "en los factores no solo individuales, sino también familiares y\n"
    "comunitarios y actualmente en los culturales."
)
MICRO_TEXT = (
    "Un ser vivo u organismo es un conjunto\n"
    "material de organización compleja,\n"
    "en la que intervienen sistemas de\n"
    "comunicación molecular que lo\n"
    "relacionan internamente y con el\n"
    "medio ambiente en un intercambio de\n"
    "materia y energía de una forma\n"
    "ordenada"
)

Point = tuple[float, float]
Polyline = list[Point]


def _p(x: float, y: float) -> Point:
    return (
        LAYOUT_OFFSET[0] + x * LAYOUT_FIT_SCALE,
        LAYOUT_OFFSET[1] + y * LAYOUT_FIT_SCALE,
    )


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


def _line(lines: list[Polyline], a: tuple[float, float], b: tuple[float, float]) -> None:
    lines.append([_p(*a), _p(*b)])


def _polyline(
    lines: list[Polyline], points: list[tuple[float, float]], *, closed: bool = False
) -> None:
    mapped = [_p(x, y) for x, y in points]
    lines.append([*mapped, mapped[0]] if closed and mapped else mapped)


def _circle(
    lines: list[Polyline], cx: float, cy: float, radius: float, samples: int = 48
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


def _rounded_rect_points(
    x0: float, y0: float, x1: float, y1: float, radius: float, samples: int = 7
) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for cx, cy, start in (
        (x1 - radius, y0 + radius, -90.0),
        (x1 - radius, y1 - radius, 0.0),
        (x0 + radius, y1 - radius, 90.0),
        (x0 + radius, y0 + radius, 180.0),
    ):
        for angle in np.linspace(start, start + 90.0, samples, endpoint=False):
            radians = math.radians(float(angle))
            result.append((cx + radius * math.cos(radians), cy + radius * math.sin(radians)))
    return result


def _catmull_rom_closed(points: list[tuple[float, float]], samples: int = 8) -> list[tuple[float, float]]:
    curve: list[tuple[float, float]] = []
    count = len(points)
    for index in range(count):
        p0 = np.asarray(points[(index - 1) % count], dtype=float)
        p1 = np.asarray(points[index], dtype=float)
        p2 = np.asarray(points[(index + 1) % count], dtype=float)
        p3 = np.asarray(points[(index + 2) % count], dtype=float)
        for value in np.linspace(0.0, 1.0, samples, endpoint=False):
            t2 = value * value
            t3 = t2 * value
            point = 0.5 * (
                2.0 * p1
                + (-p0 + p2) * value
                + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
                + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3
            )
            curve.append((float(point[0]), float(point[1])))
    return curve


def _quadratic(
    start: tuple[float, float], control: tuple[float, float], end: tuple[float, float], samples: int = 28
) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    a = np.asarray(start, dtype=float)
    b = np.asarray(control, dtype=float)
    c = np.asarray(end, dtype=float)
    for value in np.linspace(0.0, 1.0, samples):
        point = (1.0 - value) ** 2 * a + 2.0 * (1.0 - value) * value * b + value**2 * c
        result.append((float(point[0]), float(point[1])))
    return result


def _mesh_linework(lines: list[Polyline]) -> None:
    x0, y0, x1, y1 = 279.0, 173.0, 592.0, 240.0
    cy = (y0 + y1) / 2.0
    radius = (y1 - y0) / 2.0
    straight_left = x0 + radius
    straight_right = x1 - radius

    def vertical_bounds(x: float) -> tuple[float, float]:
        if x < straight_left:
            half = math.sqrt(max(0.0, radius**2 - (straight_left - x) ** 2))
        elif x > straight_right:
            half = math.sqrt(max(0.0, radius**2 - (x - straight_right) ** 2))
        else:
            half = radius
        return cy - half, cy + half

    def horizontal_bounds(y: float) -> tuple[float, float]:
        half = math.sqrt(max(0.0, radius**2 - (y - cy) ** 2))
        return straight_left - half, straight_right + half

    _polyline(lines, _rounded_rect_points(x0, y0, x1, y1, radius, samples=11), closed=True)
    for index, x in enumerate(np.linspace(x0 + 7.0, x1 - 7.0, 27)):
        top, bottom = vertical_bounds(float(x))
        points: list[tuple[float, float]] = []
        for value in np.linspace(0.0, 1.0, 17):
            y = top + (bottom - top) * float(value)
            bend = 5.0 * math.sin(math.pi * float(value)) * math.exp(-(x - x0) / 90.0)
            ripple = 1.0 * math.sin(index * 0.8 + value * math.tau)
            points.append((float(x + bend + ripple), y))
        _polyline(lines, points)
    for index, y in enumerate(np.linspace(y0 + 6.0, y1 - 6.0, 9)):
        left, right = horizontal_bounds(float(y))
        points = []
        for value in np.linspace(0.0, 1.0, 54):
            x = left + (right - left) * float(value)
            wave = 2.2 * math.sin(value * math.tau + index * 0.47)
            wave += 2.8 * math.exp(-(x - x0) / 65.0) * math.sin(value * math.pi)
            points.append((x, float(y + wave)))
        _polyline(lines, points)


def _contour_linework(lines: list[Polyline]) -> None:
    outer = [
        (144, 695), (205, 690), (274, 690), (318, 647), (359, 597),
        (380, 591), (430, 612), (523, 655), (558, 681), (565, 710),
        (516, 800), (494, 808), (228, 806), (204, 790), (144, 714),
    ]
    center = np.asarray((375.0, 727.0))
    for level, scale in enumerate(np.linspace(1.0, 0.12, 14)):
        points: list[tuple[float, float]] = []
        for index, point in enumerate(outer):
            vector = np.asarray(point) - center
            # Inner levels contract a little faster vertically, matching the
            # asymmetric nested island in the reference.
            warped = center + vector * np.asarray((scale, scale * (0.93 + 0.07 * scale)))
            warped += np.asarray((
                math.sin(index * 1.8 + level * 0.45) * 1.8 * scale,
                math.cos(index * 1.35 + level * 0.31) * 1.4 * scale,
            ))
            points.append((float(warped[0]), float(warped[1])))
        _polyline(lines, _catmull_rom_closed(points, samples=7), closed=True)
    _circle(lines, 375.0, 727.0, 2.3, samples=28)
    _circle(lines, 449.0, 648.0, 6.0, samples=38)
    _circle(lines, 449.0, 648.0, 1.5, samples=24)


def _ink_linework() -> list[Polyline]:
    lines: list[Polyline] = []

    # Two offset squares and their corresponding vertices form the wire cube.
    first = [(131, 134), (195, 134), (195, 197), (131, 197)]
    second = [(170, 173), (233, 173), (233, 236), (170, 236)]
    _polyline(lines, first, closed=True)
    _polyline(lines, second, closed=True)
    for a, b in zip(first, second):
        _line(lines, a, b)
    _line(lines, (131, 166), (195, 166))
    _line(lines, (163, 134), (163, 197))
    _line(lines, (170, 205), (233, 205))
    _line(lines, (202, 173), (202, 236))

    _mesh_linework(lines)
    _line(lines, (119, 267), (592, 267))
    _line(lines, (119, 481), (592, 481))

    for cx, cy in ((143, 516), (568, 516), (568, 588), (143, 835), (568, 835)):
        _line(lines, (cx - 7, cy), (cx + 7, cy))
        _line(lines, (cx, cy - 7), (cx, cy + 7))

    _contour_linework(lines)

    # Swatch rail and its measured dividers.
    _polyline(lines, [(118, 885), (592, 885), (592, 939), (118, 939)], closed=True)
    for x in (183, 211, 362, 452, 485, 541):
        _line(lines, (x, 885), (x, 939))

    # Photograph registration crosshair.
    _circle(lines, 1018, 145, 7.0, samples=34)
    _line(lines, (998, 145), (1038, 145))
    _line(lines, (1018, 125), (1018, 165))
    return lines


def _distant_stems() -> list[Polyline]:
    rng = np.random.default_rng(SEED + 31)
    lines: list[Polyline] = []
    for _ in range(230):
        root_x = float(rng.uniform(610, 795))
        root_y = float(rng.uniform(555, 730))
        height = float(rng.uniform(18, 120))
        drift = float(rng.normal(0.0, 16.0))
        points = [
            (root_x, root_y),
            (root_x + drift * 0.35, root_y - height * 0.52),
            (root_x + drift, root_y - height),
        ]
        _polyline(lines, points)
        if rng.random() < 0.65:
            branch_y = root_y - height * float(rng.uniform(0.35, 0.78))
            branch_x = root_x + drift * (root_y - branch_y) / height
            _polyline(
                lines,
                [(branch_x, branch_y), (branch_x + float(rng.uniform(-18, 18)), branch_y - float(rng.uniform(4, 18)))],
            )
    return lines


def _grass_linework(*, dark: bool) -> list[Polyline]:
    rng = np.random.default_rng(SEED + (71 if dark else 57))
    lines: list[Polyline] = []
    count = 430 if dark else 620
    for _ in range(count):
        if dark:
            root_x = float(rng.uniform(610, 1005))
            root_y = float(rng.uniform(720, 1038))
            length = float(rng.uniform(7, 34))
        else:
            root_x = float(rng.uniform(635, 1060))
            root_y = float(rng.uniform(565, 1010))
            length = float(rng.uniform(12, 78))
        if root_x < 760 and root_y < 650:
            continue
        lean = float(rng.uniform(-0.42, 0.55) * length)
        end_x = float(np.clip(root_x + lean, 612, 1059))
        end_y = float(max(115, root_y - length))
        middle = ((root_x + end_x) / 2.0 + float(rng.normal(0, 2.4)), (root_y + end_y) / 2.0)
        _polyline(lines, [(root_x, root_y), middle, (end_x, end_y)])
        if not dark and rng.random() < 0.28:
            _polyline(
                lines,
                [middle, (middle[0] + float(rng.uniform(-10, 10)), middle[1] - float(rng.uniform(3, 14)))],
            )
    return lines


def _fold_linework() -> list[Polyline]:
    lines: list[Polyline] = []
    apex = (831.0, 220.0)
    folds = (
        ((810, 360), (714, 665)), ((820, 390), (752, 695)),
        ((827, 410), (792, 704)), ((837, 420), (842, 712)),
        ((846, 405), (877, 698)), ((852, 390), (920, 678)),
        ((857, 360), (970, 654)),
    )
    for control, end in folds:
        _polyline(lines, _quadratic(apex, control, end, samples=34))
    lower_folds = (
        ((720, 680), (730, 805), (742, 910)),
        ((760, 700), (790, 820), (830, 916)),
        ((835, 710), (855, 820), (878, 900)),
        ((900, 690), (940, 760), (943, 852)),
    )
    for start, control, end in lower_folds:
        _polyline(lines, _quadratic(start, control, end, samples=30))
    # Broken hem and short crease network.
    for a, b in (
        ((656, 665), (715, 684)), ((715, 684), (787, 703)),
        ((787, 703), (851, 712)), ((851, 712), (920, 682)),
        ((695, 614), (736, 626)), ((724, 575), (770, 596)),
        ((765, 530), (808, 552)), ((850, 555), (905, 580)),
        ((868, 610), (930, 628)), ((746, 770), (780, 794)),
        ((795, 832), (830, 850)), ((865, 820), (900, 846)),
    ):
        _line(lines, a, b)
    return lines


def _highlight_linework() -> list[Polyline]:
    lines: list[Polyline] = []
    # Footer symbols and identity marks.
    _circle(lines, 165, 1001, 15.0, samples=58)
    _line(lines, (151, 1015), (179, 987))
    for cx in (266, 291, 315):
        _circle(lines, cx, 1001, 1.8, samples=22)
    _line(lines, (498, 982), (498, 1018))
    for cx, cy in ((531, 989), (556, 989), (531, 1012), (556, 1012)):
        _line(lines, (cx - 5, cy), (cx + 5, cy))
        _line(lines, (cx, cy - 5), (cx, cy + 5))

    # Lower photograph crosshair.
    _circle(lines, 650, 1001, 7.0, samples=32)
    _line(lines, (632, 1001), (668, 1001))
    _line(lines, (650, 983), (650, 1019))

    # Viewer mark retained because the supplied reference contains it.
    _circle(lines, 1085, 1063, 28.0, samples=64)
    _line(lines, (1100, 1040), (1112, 1028))
    _line(lines, (1100, 1086), (1112, 1098))
    star = []
    for index in range(8):
        radius = 13.0 if index % 2 == 0 else 4.5
        angle = -math.pi / 2.0 + index * math.pi / 4.0
        star.append((1106 + radius * math.cos(angle), 1040 + radius * math.sin(angle)))
    _polyline(lines, star, closed=True)
    return lines


@lru_cache(maxsize=8)
def _constructed_line_data(part: int) -> tuple[np.ndarray, np.ndarray]:
    families = {
        0: _ink_linework,
        1: _distant_stems,
        2: lambda: _grass_linework(dark=False),
        3: lambda: _grass_linework(dark=True),
        4: _fold_linework,
        5: _highlight_linework,
    }
    factory = families.get(part)
    if factory is None:
        return _pack([])
    return _pack(factory())


@primitive
def look_alive_constructed_lines(*, part: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Return one editable semantic family of the constructed poster linework."""

    coords, offsets = _constructed_line_data(int(part))
    return coords.copy(), offsets.copy()


def _shape(points: list[tuple[float, float]], *, key: str):
    return G(name=f"Shape / {key}").polyline(
        points=tuple(_p(x, y) for x, y in points),
        closed=True,
        key="look-alive-constructed-shape",
        instance_key=key,
    )


def _rounded_shape(
    x0: float, y0: float, x1: float, y1: float, radius: float, *, key: str
):
    return _shape(_rounded_rect_points(x0, y0, x1, y1, radius, samples=10), key=key)


def _rect_shape(x0: float, y0: float, x1: float, y1: float, *, key: str):
    center = _p((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    return G(name=f"Rectangle / {key}").rect(
        width=(x1 - x0) * LAYOUT_FIT_SCALE,
        height=(y1 - y0) * LAYOUT_FIT_SCALE,
        center=(*center, 0.0),
        key="look-alive-constructed-rect",
        instance_key=key,
    )


def _circle_shape(cx: float, cy: float, radius: float, *, key: str):
    center = _p(cx, cy)
    return G(name=f"Circle / {key}").circle(
        radius=radius * LAYOUT_FIT_SCALE,
        segments=64,
        center=(*center, 0.0),
        key="look-alive-constructed-circle",
        instance_key=key,
    )


def _text(
    text: str,
    x: float,
    y: float,
    scale_px: float,
    *,
    font: str = BODY_FONT,
    font_index: int = 0,
    line_height: float = 1.2,
    spacing: float = 0.0,
    key: str,
):
    position = _p(x, y)
    return G(name=f"Text / {key}").text(
        text=text,
        font=font,
        font_index=font_index,
        text_align="left",
        letter_spacing_em=spacing,
        line_height=line_height,
        quality=0.38,
        center=(*position, 0.0),
        scale=scale_px * LAYOUT_FIT_SCALE,
        key="look-alive-constructed-text",
        instance_key=key,
    )


def _fill(
    geometry,
    *,
    key: str,
    kind: str = "solid",
    angle: float = 45.0,
    angle_sets: int = 6,
):
    return E(name=f"Fill / {key}").fill(
        angle_sets=angle_sets,
        angle=angle,
        density=FILL_DENSITIES[kind],
        min_spacing=FILL_MIN_SPACINGS[kind],
        remove_boundary=False,
        key="look-alive-constructed-fill",
        instance_key=key,
    )(geometry)


def _line_family(part: int, name: str):
    return G(name=name).look_alive_constructed_lines(
        part=part,
        key="look-alive-constructed-lines",
        instance_key=f"part-{part}",
    )


def _frame_geometry():
    return _fill(
        _rect_shape(0, 0, 1179, 1149, key="reference-black-field"),
        key="reference-black-field",
    )


def _paper_geometry():
    return _fill(
        _rounded_shape(98, 81, 1080, 1063, 27, key="paper-card"),
        key="paper-card",
    )


def _sky_geometry():
    return _fill(
        _rounded_shape(610, 101, 1061, 1044, 17, key="photograph-base"),
        key="photograph-base",
        kind="photographic",
        angle=0.0,
        angle_sets=3,
    )


def _haze_geometry():
    shapes = [
        _shape(
            [(610, 360), (690, 345), (770, 370), (850, 405), (940, 430), (1061, 500), (1061, 760), (610, 760)],
            key="horizon-haze",
        ),
        _shape([(940, 500), (1004, 482), (1061, 505), (1061, 620), (1005, 598)], key="right-cloud"),
    ]
    fills = tuple(
        _fill(shape, key=f"haze-{index}", kind="photographic", angle=8.0, angle_sets=2)
        for index, shape in enumerate(shapes)
    )
    return (*fills, _line_family(1, "Distant field stems"))


def _ochre_geometry():
    ground = _shape(
        [
            (610, 640), (665, 610), (725, 594), (785, 620), (850, 654),
            (925, 680), (990, 620), (1061, 585), (1061, 1027), (1058, 1037),
            (1048, 1044), (624, 1044), (614, 1038), (610, 1027),
        ],
        key="dry-field",
    )
    return (
        _fill(ground, key="dry-field", kind="photographic", angle=23.0, angle_sets=3),
        _line_family(2, "Dry grass stalks"),
    )


def _earth_geometry():
    field = _shape(
        [
            (610, 728), (660, 686), (720, 700), (782, 760), (850, 812),
            (926, 850), (1000, 820), (1061, 780), (1061, 1027), (1057, 1038),
            (1047, 1044), (625, 1044), (614, 1037), (610, 1027),
        ],
        key="earth-field",
    )
    return _fill(field, key="earth-field", kind="photographic", angle=155.0, angle_sets=3)


def _dark_ground_geometry():
    path = _shape(
        [
            (610, 704), (660, 680), (708, 704), (744, 770), (784, 852),
            (850, 905), (936, 950), (1012, 974), (1061, 956), (1061, 1044),
            (610, 1044),
        ],
        key="dark-path",
    )
    shadow = _shape(
        [(690, 874), (770, 847), (866, 860), (950, 905), (1004, 966), (950, 994), (820, 975), (714, 928)],
        key="figure-shadow",
    )
    return (
        _fill(path, key="dark-path", kind="photographic", angle=5.0, angle_sets=3),
        _fill(shadow, key="figure-shadow", kind="photographic", angle=165.0, angle_sets=3),
        _line_family(3, "Dark field texture"),
    )


def _caption_geometry():
    return _fill(
        _rounded_shape(837, 977, 1051, 1030, 10, key="caption-panel"),
        key="caption-panel",
        kind="translucent",
        angle=0.0,
        angle_sets=2,
    )


def _ink_geometry():
    geometries = [_line_family(0, "Editorial linework")]

    # Eight bold arrows above the warped grid.
    for index in range(8):
        cx = 294.0 + index * 41.0
        geometries.append(
            _fill(
                _shape(
                    [
                        (cx - 11, 139), (cx + 1, 139), (cx - 3, 133),
                        (cx + 4, 133), (cx + 12, 143), (cx + 4, 153),
                        (cx - 3, 153), (cx + 1, 147), (cx - 11, 147),
                    ],
                    key=f"arrow-{index}",
                ),
                key=f"arrow-{index}",
            )
        )

    title = _text(
        "Look Alive_", 128, 292, 50,
        font=TITLE_FONT, font_index=2, line_height=1.0, spacing=-0.035, key="title",
    )
    body = _text(BODY_TEXT, 131, 360, 8.9, line_height=1.52, key="body-copy")
    micro = _text(MICRO_TEXT, 154, 546, 6.2, line_height=1.80, key="micro-copy")
    geometries.extend(
        (
            _fill(title, key="title", kind="typography", angle=30.0, angle_sets=4),
            _fill(body, key="body-copy", kind="typography", angle=25.0, angle_sets=2),
            _fill(micro, key="micro-copy", kind="typography", angle=25.0, angle_sets=2),
            _fill(_rounded_shape(118, 956, 592, 1043, 17, key="footer"), key="footer"),
            _fill(_circle_shape(1060, 495, 8.5, key="black-pin"), key="black-pin"),
            _fill(_rounded_shape(1011, 995, 1124, 1095, 11, key="viewer-overlay"), key="viewer-overlay", kind="translucent", angle=0.0, angle_sets=3),
        )
    )
    return tuple(geometries)


def _cloth_geometry():
    shapes = [
        # Rear hanging flaps are defined first so the broad upper drape can sit
        # naturally in front of them.
        _shape([(705, 680), (760, 700), (770, 790), (742, 913), (718, 865), (700, 786)], key="left-flap"),
        _shape([(752, 695), (835, 710), (850, 812), (830, 919), (790, 878), (760, 792)], key="center-flap"),
        _shape([(830, 706), (900, 690), (925, 778), (878, 903), (850, 854)], key="right-center-flap"),
        _shape([(900, 680), (970, 642), (1007, 790), (942, 858), (918, 796)], key="right-flap"),
        _shape(
            [
                (831, 219), (800, 230), (780, 260), (762, 300), (743, 350),
                (726, 420), (704, 500), (680, 580), (656, 665), (715, 684),
                (787, 703), (851, 712), (920, 682), (1007, 665), (985, 620),
                (969, 540), (953, 460), (931, 380), (906, 320), (885, 280),
                (857, 240),
            ],
            key="upper-drape",
        ),
    ]
    return tuple(
        _fill(shape, key=f"cloth-{index}", kind="photographic", angle=90.0, angle_sets=4)
        for index, shape in enumerate(shapes)
    )


def _cloth_pale_geometry():
    shapes = [
        _shape([(831, 220), (798, 255), (748, 395), (690, 590), (715, 656), (785, 680), (809, 520), (820, 340)], key="left-lit-plane"),
        _shape([(845, 232), (858, 300), (868, 470), (877, 685), (919, 674), (900, 450), (870, 292)], key="central-cool-plane"),
        _shape([(752, 700), (790, 728), (807, 832), (790, 878), (760, 792)], key="lower-cool-fold"),
    ]
    return tuple(
        _fill(shape, key=f"cloth-pale-{index}", kind="photographic", angle=85.0, angle_sets=3)
        for index, shape in enumerate(shapes)
    )


def _cloth_shadow_geometry():
    shapes = [
        _shape([(832, 220), (843, 250), (850, 405), (852, 708), (875, 698), (864, 460), (851, 280)], key="central-groove"),
        _shape([(850, 230), (884, 280), (931, 380), (985, 620), (920, 682), (884, 510), (864, 330)], key="right-shadow-plane"),
        _shape([(715, 684), (770, 704), (770, 790), (742, 913), (720, 865)], key="left-lower-shadow"),
        _shape([(830, 708), (900, 690), (925, 778), (878, 903), (852, 852)], key="lower-right-shadow"),
        _shape([(920, 682), (970, 642), (1007, 790), (942, 858), (918, 796)], key="far-right-shadow"),
    ]
    return tuple(
        _fill(shape, key=f"cloth-shadow-{index}", kind="photographic", angle=115.0, angle_sets=3)
        for index, shape in enumerate(shapes)
    )


def _highlight_geometry():
    geometries = [_line_family(5, "White identity and registration marks")]
    geometries.extend(
        (
            _fill(_shape([(820, 232), (796, 300), (760, 440), (704, 630), (733, 648), (793, 490), (822, 300)], key="drape-highlight-left"), key="drape-highlight-left", kind="photographic", angle=80.0, angle_sets=3),
            _fill(_shape([(830, 230), (821, 350), (817, 560), (826, 690), (840, 700), (838, 470)], key="drape-highlight-center"), key="drape-highlight-center", kind="photographic", angle=90.0, angle_sets=3),
            _fill(_text("Ø15", 149, 980, 31, font=MONO_FONT, line_height=1.0, key="footer-index"), key="footer-index", kind="typography", angle_sets=3),
            _fill(_text("D E S I G N E D   B Y\nJ E S U S\nL L A N O A R E", 342, 980, 5.8, font=MONO_FONT, line_height=1.55, key="footer-credit"), key="footer-credit", kind="typography", angle_sets=2),
            _fill(_text(MICRO_TEXT, 843, 982, 4.3, line_height=1.12, key="photo-caption"), key="photo-caption", kind="typography", angle_sets=2),
        )
    )
    return tuple(geometries)


def _fold_geometry():
    return _line_family(4, "Cloth fold drawing")


def _accent_geometry(*, cyan: bool):
    if cyan:
        return (
            _fill(_circle_shape(609, 737, 8.5, key="cyan-pin"), key="cyan-pin"),
            _fill(_rect_shape(184, 886, 210, 938, key="cyan-swatch"), key="cyan-swatch"),
        )
    return (
        _fill(_circle_shape(609, 321, 8.5, key="orange-pin"), key="orange-pin"),
        _fill(_rect_shape(486, 886, 540, 938, key="orange-swatch"), key="orange-swatch"),
    )


def draw(t: float):
    del t
    ordered = (
        ("frame_black", _frame_geometry()),
        ("paper", _paper_geometry()),
        ("sky", _sky_geometry()),
        ("haze", _haze_geometry()),
        ("ochre", _ochre_geometry()),
        ("earth", _earth_geometry()),
        ("ground_dark", _dark_ground_geometry()),
        ("ink", _ink_geometry()),
        ("cloth", _cloth_geometry()),
        ("cloth_pale", _cloth_pale_geometry()),
        ("cloth_shadow", _cloth_shadow_geometry()),
        ("fold", _fold_geometry()),
        ("caption", _caption_geometry()),
        ("highlight", _highlight_geometry()),
        ("orange", _accent_geometry(cyan=False)),
        ("cyan", _accent_geometry(cyan=True)),
    )
    return tuple(
        L(name=f"Look Alive constructed / {name}").layer(
            geometry,
            color=LINE_COLORS[name],
            thickness=LINE_THICKNESS,
        )
        for name, geometry in ordered
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
