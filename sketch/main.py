from __future__ import annotations

import math

import numpy as np
from shapely import MultiPoint, delaunay_triangles, get_coordinates

from grafix import G, L, primitive


CANVAS = (210, 148)
SEED = 73624


Point = tuple[float, float]
Polyline = list[Point]


def _pack(polylines: list[Polyline]) -> tuple[np.ndarray, np.ndarray]:
    """Convert explicit two-dimensional polylines to Grafix primitive buffers."""
    values: list[tuple[float, float, float]] = []
    offsets_values: list[int] = [0]
    for polyline in polylines:
        if len(polyline) < 2:
            continue
        for x, y in polyline:
            values.append((float(x), float(y), 0.0))
        offsets_values.append(len(values))

    coords = np.asarray(values, dtype=np.float32)
    offsets = np.asarray(offsets_values, dtype=np.int32)
    return np.ascontiguousarray(coords), np.ascontiguousarray(offsets)


def _boundary_point(theta: float) -> Point:
    """Irregular rounded-rectangle rim with the exact technical bbox."""
    cosine = math.cos(theta)
    sine = math.sin(theta)
    power = 2.0 / 5.2
    ux = math.copysign(abs(cosine) ** power, cosine)
    uy = math.copysign(abs(sine) ** power, sine)
    x = 105.0 + 96.0 * ux
    y = 62.0 + 51.0 * uy
    # Irregularity vanishes at the four extrema, preserving x=9..201, y=11..113.
    x += (1.0 - abs(ux)) * (1.55 * math.sin(3.0 * theta + 0.4))
    x += (1.0 - abs(ux)) * (0.55 * math.sin(8.0 * theta - 0.7))
    y += (1.0 - abs(uy)) * (1.05 * math.sin(5.0 * theta - 0.25))
    y += (1.0 - abs(uy)) * (0.38 * math.cos(9.0 * theta + 0.5))
    return (min(201.0, max(9.0, x)), min(113.0, max(11.0, y)))


def _inside_domain(x: float, y: float) -> bool:
    """Warped superellipse membership test matching the rounded rectangle."""
    shifted_x = x - 105.0 - 0.78 * math.sin((y - 62.0) / 10.5)
    shifted_y = y - 62.0 - 0.48 * math.sin((x - 105.0) / 14.0)
    nx = abs(shifted_x / 96.0)
    ny = abs(shifted_y / 51.0)
    angle = math.atan2(shifted_y / 51.0, shifted_x / 96.0)
    rim = 0.973 + 0.009 * math.sin(5.0 * angle + 0.3)
    rim += 0.004 * math.sin(11.0 * angle - 0.8)
    return nx**5.2 + ny**5.2 < rim


def _horizontal_rim_x(y: float, side: float) -> float:
    """Return an inset left/right superellipse boundary at a given y."""
    normalized_y = min(0.999, abs((y - 62.0) / 51.0))
    extent = 96.0 * max(0.0, 1.0 - normalized_y**5.2) ** (1.0 / 5.2)
    wobble = 0.55 * math.sin(y * 0.31) * (1.0 - normalized_y)
    return 105.0 + side * (extent - 0.45) + wobble


def _adaptive_points(seed: int) -> np.ndarray:
    """Create one reproducible, nonuniform 868-vertex rounded-field cloud."""
    rng = np.random.default_rng(seed)
    points: list[tuple[float, float]] = []

    # A finely sampled noncircular rim constrains the otherwise true Delaunay hull.
    for index in range(104):
        theta = math.tau * index / 104.0
        points.append(_boundary_point(theta))

    # Broad irregular coverage, sampled without a latent rectangular lattice.
    while len(points) < 608:
        x = float(rng.uniform(9.4, 200.6))
        y = float(rng.uniform(11.4, 112.6))
        if _inside_domain(x, y):
            points.append((x, y))

    # The central anisotropic collision band supplies adaptive mesh density.
    while len(points) < 748:
        x = float(rng.normal(103.0, 24.0))
        y = float(rng.normal(62.0, 7.0))
        if _inside_domain(x, y):
            points.append((x, y))

    # Three smaller halos tighten the triangles only near the singularities.
    for cx, cy, sx, sy, target in (
        (48.5, 32.0, 9.0, 6.0, 788),
        (159.0, 45.5, 10.0, 6.5, 828),
        (96.5, 94.0, 10.5, 6.0, 868),
    ):
        while len(points) < target:
            x = float(rng.normal(cx, sx))
            y = float(rng.normal(cy, sy))
            if _inside_domain(x, y):
                points.append((x, y))

    return np.ascontiguousarray(np.asarray(points, dtype=np.float64))


def _delaunay_edge_pairs(seed: int) -> list[tuple[Point, Point]]:
    points = _adaptive_points(seed)
    point_values: list[tuple[float, float]] = []
    for x, y in points:
        point_values.append((float(x), float(y)))

    unique_edges = delaunay_triangles(MultiPoint(point_values), only_edges=True)
    pairs: list[tuple[Point, Point]] = []
    for edge in unique_edges.geoms:
        xy = get_coordinates(edge)
        if xy.shape[0] >= 2:
            p0 = (float(xy[0, 0]), float(xy[0, 1]))
            p1 = (float(xy[-1, 0]), float(xy[-1, 1]))
            pairs.append((p0, p1))
    return pairs


def _catmull_rom(anchors: list[Point], samples_per_span: int = 14) -> Polyline:
    """Sample a smooth polyline through every supplied anchor."""
    result: Polyline = []
    last = len(anchors) - 1
    for span in range(last):
        p0 = anchors[max(0, span - 1)]
        p1 = anchors[span]
        p2 = anchors[span + 1]
        p3 = anchors[min(last, span + 2)]
        for step in range(samples_per_span):
            u = step / float(samples_per_span)
            u2 = u * u
            u3 = u2 * u
            x = 0.5 * (
                2.0 * p1[0]
                + (-p0[0] + p2[0]) * u
                + (2.0 * p0[0] - 5.0 * p1[0] + 4.0 * p2[0] - p3[0]) * u2
                + (-p0[0] + 3.0 * p1[0] - 3.0 * p2[0] + p3[0]) * u3
            )
            y = 0.5 * (
                2.0 * p1[1]
                + (-p0[1] + p2[1]) * u
                + (2.0 * p0[1] - 5.0 * p1[1] + 4.0 * p2[1] - p3[1]) * u2
                + (-p0[1] + 3.0 * p1[1] - 3.0 * p2[1] + p3[1]) * u3
            )
            result.append((x, y))
    result.append(anchors[-1])
    return result


def _cubic(p0: Point, p1: Point, p2: Point, p3: Point, count: int) -> Polyline:
    values: Polyline = []
    for index in range(count):
        u = index / float(count - 1)
        v = 1.0 - u
        x = v**3 * p0[0] + 3.0 * v * v * u * p1[0]
        x += 3.0 * v * u * u * p2[0] + u**3 * p3[0]
        y = v**3 * p0[1] + 3.0 * v * v * u * p1[1]
        y += 3.0 * v * u * u * p2[1] + u**3 * p3[1]
        values.append((x, y))
    return values


def _quadratic(p0: Point, control: Point, p1: Point, count: int = 34) -> Polyline:
    values: Polyline = []
    for index in range(count):
        u = index / float(count - 1)
        v = 1.0 - u
        x = v * v * p0[0] + 2.0 * v * u * control[0] + u * u * p1[0]
        y = v * v * p0[1] + 2.0 * v * u * control[1] + u * u * p1[1]
        values.append((x, y))
    return values


def _loop_from_cubics(
    tip: Point,
    c1: Point,
    c2: Point,
    back: Point,
    c3: Point,
    c4: Point,
    scale: float,
) -> Polyline:
    def scaled(point: Point) -> Point:
        return (
            tip[0] + scale * (point[0] - tip[0]),
            tip[1] + scale * (point[1] - tip[1]),
        )

    first = _cubic(tip, scaled(c1), scaled(c2), scaled(back), 43)
    second = _cubic(scaled(back), scaled(c3), scaled(c4), tip, 43)
    return first + second[1:]


def _dash_polyline(polyline: Polyline, on: int = 5, off: int = 3) -> list[Polyline]:
    pieces: list[Polyline] = []
    cursor = 0
    while cursor < len(polyline) - 1:
        end = min(len(polyline), cursor + on + 1)
        if end - cursor >= 2:
            pieces.append(polyline[cursor:end])
        cursor += on + off
    return pieces


@primitive
def ricci_mesh(*, seed: int = SEED) -> tuple[np.ndarray, np.ndarray]:
    polylines: list[Polyline] = []
    for p0, p1 in _delaunay_edge_pairs(seed):
        polylines.append([p0, p1])
    return _pack(polylines)


@primitive
def crossing_chord_mesh(*, seed: int = SEED) -> tuple[np.ndarray, np.ndarray]:
    """Longer alternate Delaunay edges crossing the fine current topology."""
    points = _adaptive_points(seed + 503)
    sparse_values: list[tuple[float, float]] = []
    for index in range(0, points.shape[0], 2):
        sparse_values.append((float(points[index, 0]), float(points[index, 1])))
    edges = delaunay_triangles(MultiPoint(sparse_values), only_edges=True)
    polylines: list[Polyline] = []
    for index, edge in enumerate(edges.geoms):
        # The retained irregular subset raises total mesh density without a dark wash.
        if index % 4 == 0:
            continue
        xy = get_coordinates(edge)
        if xy.shape[0] >= 2:
            polylines.append(
                [
                    (float(xy[0, 0]), float(xy[0, 1])),
                    (float(xy[-1, 0]), float(xy[-1, 1])),
                ]
            )
    return _pack(polylines)


@primitive
def collision_triangles(*, seed: int = SEED) -> tuple[np.ndarray, np.ndarray]:
    """Mixed-scale micro-triangulation across the central saddle band."""
    rng = np.random.default_rng(seed + 911)
    values: list[tuple[float, float]] = []
    # Irregular band rim makes the local Delaunay patch end without a box.
    for index in range(34):
        theta = math.tau * index / 34.0
        radius_x = 35.0 + 2.2 * math.sin(5.0 * theta)
        radius_y = 11.5 + 1.1 * math.cos(3.0 * theta)
        values.append((103.0 + radius_x * math.cos(theta), 62.0 + radius_y * math.sin(theta)))
    while len(values) < 214:
        x = float(rng.normal(103.0, 18.5))
        y = float(rng.normal(62.0, 5.2))
        if 69.0 < x < 138.0 and 49.0 < y < 75.0:
            values.append((x, y))
    edges = delaunay_triangles(MultiPoint(values), only_edges=True)
    polylines: list[Polyline] = []
    for edge in edges.geoms:
        xy = get_coordinates(edge)
        if xy.shape[0] >= 2:
            polylines.append(
                [
                    (float(xy[0, 0]), float(xy[0, 1])),
                    (float(xy[-1, 0]), float(xy[-1, 1])),
                ]
            )
    return _pack(polylines)


@primitive
def historic_topology(*, seed: int = SEED) -> tuple[np.ndarray, np.ndarray]:
    """A displaced, dashed subset of an earlier triangulation state."""
    polylines: list[Polyline] = []
    for index, (p0, p1) in enumerate(_delaunay_edge_pairs(seed + 137)):
        if (index * 41 + 17) % 11 != 0:
            continue
        dx = p1[0] - p0[0]
        dy = p1[1] - p0[1]
        length = math.hypot(dx, dy)
        if length < 1.2:
            continue
        ox = -dy / length * 0.22
        oy = dx / length * 0.22
        for start in (0.08, 0.43, 0.78):
            finish = min(0.96, start + 0.13)
            a = (p0[0] + dx * start + ox, p0[1] + dy * start + oy)
            b = (p0[0] + dx * finish + ox, p0[1] + dy * finish + oy)
            polylines.append([a, b])
    return _pack(polylines)


@primitive
def vertex_ticks(*, seed: int = SEED) -> tuple[np.ndarray, np.ndarray]:
    points = _adaptive_points(seed)
    polylines: list[Polyline] = []
    for index, (x, y) in enumerate(points):
        angle = (index * 2.399963229728653 + float(x) * 0.071) % math.tau
        half = 0.13 + 0.055 * (0.5 + 0.5 * math.sin(index * 1.71))
        dx = half * math.cos(angle)
        dy = half * math.sin(angle)
        polylines.append([(float(x) - dx, float(y) - dy), (float(x) + dx, float(y) + dy)])
    return _pack(polylines)


@primitive
def outer_levels() -> tuple[np.ndarray, np.ndarray]:
    polylines: list[Polyline] = []
    for level in range(6):
        rx = 94.0 - 5.5 * level
        ry = 49.2 - 4.25 * level
        phase = 0.55 * level
        loop: Polyline = []
        for index in range(241):
            theta = math.tau * index / 240.0
            cosine = math.cos(theta)
            sine = math.sin(theta)
            ux = math.copysign(abs(cosine) ** (2.0 / 5.0), cosine)
            uy = math.copysign(abs(sine) ** (2.0 / 5.0), sine)
            wavex = (1.0 - abs(ux)) * 1.25 * math.sin(5.0 * theta + phase)
            wavey = (1.0 - abs(uy)) * 0.85 * math.cos(7.0 * theta - phase)
            x = 105.0 + rx * ux + wavex
            y = 62.0 + ry * uy + wavey
            loop.append((x, y))
        polylines.append(loop)
    return _pack(polylines)


@primitive
def geodesic_bundle() -> tuple[np.ndarray, np.ndarray]:
    polylines: list[Polyline] = []
    count = 68
    for index in range(count):
        fraction = (index + 0.5) / count
        q = 2.0 * fraction - 1.0
        start_y = 17.0 + 91.0 * fraction + 1.5 * math.sin(index * 0.71)
        end_y = 18.0 + 89.0 * (1.0 - fraction) + 1.2 * math.cos(index * 0.63)
        anchors = [
            (_horizontal_rim_x(start_y, -1.0), start_y),
            (48.5 + 0.20 * math.sin(index * 0.43), 32.0 + 0.67 * q),
            (77.0 + 0.9 * math.sin(index * 0.27), 51.0 - 2.3 * q),
            (103.0 + 0.28 * q, 62.0 + 0.31 * q),
            (130.0 + 0.7 * math.cos(index * 0.31), 53.0 + 2.2 * q),
            (159.0 + 0.21 * math.cos(index * 0.51), 45.5 - 0.67 * q),
            (_horizontal_rim_x(end_y, 1.0), end_y),
        ]
        polylines.append(_catmull_rom(anchors, samples_per_span=10))
    return _pack(polylines)


@primitive
def radial_fans() -> tuple[np.ndarray, np.ndarray]:
    polylines: list[Polyline] = []
    for focus_index, focus in enumerate(((48.5, 32.0), (159.0, 45.5))):
        for index in range(38):
            theta = math.tau * (index + 0.35 * focus_index) / 38.0
            target = _boundary_point(theta)
            dx = target[0] - focus[0]
            dy = target[1] - focus[1]
            length = max(1.0, math.hypot(dx, dy))
            bend = 2.1 * math.sin(3.0 * theta + focus_index * 1.7)
            control = (
                focus[0] + 0.48 * dx - dy / length * bend,
                focus[1] + 0.48 * dy + dx / length * bend,
            )
            polylines.append(_quadratic(focus, control, target))
    return _pack(polylines)


@primitive
def saddle_bottom_fan() -> tuple[np.ndarray, np.ndarray]:
    polylines: list[Polyline] = []
    count = 31
    for index in range(count):
        q = 2.0 * index / float(count - 1) - 1.0
        anchors = [
            (103.0 + 1.1 * q, 61.2 + 0.35 * q),
            (102.0 + 4.6 * q, 76.5),
            (96.5 + 0.72 * q, 94.0 + 0.48 * q),
            (96.5 + 20.5 * q, 112.0 - 1.5 * q * q),
        ]
        polylines.append(_catmull_rom(anchors, samples_per_span=13))
    return _pack(polylines)


@primitive
def solid_tangent_contours() -> tuple[np.ndarray, np.ndarray]:
    polylines: list[Polyline] = []
    # Twenty-one solid loops visibly pinch into P1 from the upper-left lobe.
    for index in range(21):
        scale = 0.15 + index * 0.0425
        polylines.append(
            _loop_from_cubics(
                (48.5, 32.0),
                (46.5, 14.0),
                (27.0, 13.8),
                (18.5, 22.5),
                (19.5, 30.0),
                (37.0, 33.0),
                scale,
            )
        )

    # Twenty continuous peanut contours join upper and lower lobes exactly at P3.
    for index in range(20):
        scale = 0.15 + index * 0.045
        upper = _loop_from_cubics(
            (96.5, 94.0),
            (79.0, 93.0),
            (73.5, 77.0),
            (96.5, 66.5),
            (119.0, 75.5),
            (116.5, 91.5),
            scale,
        )
        lower = _loop_from_cubics(
            (96.5, 94.0),
            (77.5, 95.0),
            (75.0, 108.0),
            (96.5, 113.0),
            (119.5, 108.0),
            (116.5, 95.0),
            scale,
        )
        polylines.append(upper + lower[1:])
    return _pack(polylines)


@primitive
def dashed_tangent_contours() -> tuple[np.ndarray, np.ndarray]:
    polylines: list[Polyline] = []
    # Twenty dashed loops arrive at P2 through a distinct lower-left cusp.
    for index in range(20):
        scale = 0.15 + index * 0.045
        loop = _loop_from_cubics(
            (159.0, 45.5),
            (165.0, 14.0),
            (184.0, 15.8),
            (191.0, 24.0),
            (190.0, 39.0),
            (145.0, 54.0),
            scale,
        )
        polylines.extend(_dash_polyline(loop, on=4, off=3))
    return _pack(polylines)


@primitive
def singularity_marks() -> tuple[np.ndarray, np.ndarray]:
    polylines: list[Polyline] = []
    for focus_index, (cx, cy) in enumerate(((48.5, 32.0), (159.0, 45.5), (96.5, 94.0))):
        for ring_index, radius in enumerate((0.48, 0.88, 1.34)):
            ring: Polyline = []
            for index in range(33):
                theta = math.tau * index / 32.0
                ripple = 1.0 + 0.08 * math.sin(5.0 * theta + focus_index + ring_index)
                ring.append((cx + radius * ripple * math.cos(theta), cy + radius * ripple * math.sin(theta)))
            polylines.append(ring)
        for index in range(24):
            theta = math.tau * index / 24.0 + focus_index * 0.17
            inner = 0.14
            outer = 1.75 + 0.40 * (0.5 + 0.5 * math.sin(index * 2.17))
            polylines.append(
                [
                    (cx + inner * math.cos(theta), cy + inner * math.sin(theta)),
                    (cx + outer * math.cos(theta), cy + outer * math.sin(theta)),
                ]
            )

    # Open hyperbolic brackets identify the bridge without inventing a fourth focus.
    for sign in (-1.0, 1.0):
        upper: Polyline = []
        lower: Polyline = []
        for index in range(25):
            u = index / 24.0
            x = 103.0 + sign * (1.1 + 5.8 * u)
            y_offset = 0.45 + 2.2 * u * u
            upper.append((x, 62.0 - y_offset))
            lower.append((x, 62.0 + y_offset))
        polylines.append(upper)
        polylines.append(lower)
    return _pack(polylines)



@primitive
def reference_footer() -> tuple[np.ndarray, np.ndarray]:
    """参照図版に合わせた罫線、線種見本、continuation 軸を生成する。"""
    polylines: list[Polyline] = [[(11.0, 121.25), (100.0, 121.25)]]

    legend_rows = (125.25, 128.25, 131.25, 134.25)
    polylines.append([(56.0, legend_rows[0]), (63.0, legend_rows[0])])
    for x in (56.0, 58.1, 60.2, 62.3):
        polylines.append([(x, legend_rows[1]), (x + 1.05, legend_rows[1])])
    polylines.append(
        [
            (56.0 + 7.0 * i / 24.0, legend_rows[2] + 0.22 * math.sin(i / 24.0 * math.tau))
            for i in range(25)
        ]
    )
    for x in np.linspace(56.0, 63.0, 13):
        polylines.append([(float(x) - 0.10, legend_rows[3]), (float(x) + 0.10, legend_rows[3])])

    axis_y = 138.3
    polylines.append([(129.4, axis_y), (193.4, axis_y)])
    polylines.extend(
        [
            [(192.4, axis_y - 0.45), (193.4, axis_y)],
            [(192.4, axis_y + 0.45), (193.4, axis_y)],
        ]
    )
    return _pack(polylines)


@primitive
def delaunay_footer_disks(*, seed: int = SEED) -> tuple[np.ndarray, np.ndarray]:
    """Five small irregular Delaunay disks, each recomputed at a beta step."""
    polylines: list[Polyline] = []
    centers = (133.5, 146.8, 160.0, 173.7, 188.0)
    for disk_index, center_x in enumerate(centers):
        rng = np.random.default_rng(seed + 1200 + disk_index * 29)
        center_y = 128.2
        points: list[tuple[float, float]] = []
        for index in range(14):
            theta = math.tau * index / 14.0
            radius = 4.9 + 0.34 * math.sin(3.0 * theta + disk_index * 0.7)
            radius += 0.18 * math.cos(7.0 * theta - disk_index)
            points.append((center_x + radius * math.cos(theta), center_y + radius * math.sin(theta)))
        for _ in range(14):
            theta = float(rng.uniform(0.0, math.tau))
            radius = 4.15 * math.sqrt(float(rng.uniform(0.025, 0.92)))
            metric = 1.0 + 0.055 * (disk_index - 2)
            x = center_x + radius * math.cos(theta) * metric
            y = center_y + radius * math.sin(theta) / metric
            points.append((x, y))
        edges = delaunay_triangles(MultiPoint(points), only_edges=True)
        for edge in edges.geoms:
            xy = get_coordinates(edge)
            if xy.shape[0] >= 2:
                polylines.append(
                    [
                        (float(xy[0, 0]), float(xy[0, 1])),
                        (float(xy[-1, 0]), float(xy[-1, 1])),
                    ]
                )
        for index, (x, y) in enumerate(points):
            angle = index * 2.17 + disk_index
            dx = 0.11 * math.cos(angle)
            dy = 0.11 * math.sin(angle)
            polylines.append([(x - dx, y - dy), (x + dx, y + dy)])
    return _pack(polylines)


def draw(t: float):
    del t
    footer_font = "/System/Library/Fonts/Menlo.ttc"
    levels = G.outer_levels()
    history = G.historic_topology(seed=SEED)
    mesh = G.ricci_mesh(seed=SEED)
    chords = G.crossing_chord_mesh(seed=SEED)
    collision = G.collision_triangles(seed=SEED)
    ticks = G.vertex_ticks(seed=SEED)
    fans = G.radial_fans()
    geodesics = G.geodesic_bundle()
    vertical = G.saddle_bottom_fan()
    solid_contours = G.solid_tangent_contours()
    dashed_contours = G.dashed_tangent_contours()
    marks = G.singularity_marks()
    footer = G.reference_footer()
    morphs = G.delaunay_footer_disks(seed=SEED)
    footer_title = G.text(
        text="CONCEPT 24 — CURVATURE SURGERY",
        font=footer_font,
        center=(11.0, 122.35, 0.0),
        scale=1.42,
        quality=0.58,
        letter_spacing_em=0.025,
    )
    footer_description = G.text(
        text="Discrete Conformal Ricci Flow\n+ Delaunay Edge Flip\n+ Nonlinear Metric Embedding",
        font=footer_font,
        center=(11.0, 126.15, 0.0),
        scale=0.92,
        quality=0.50,
        line_height=1.65,
    )
    footer_legend = G.text(
        text=(
            "current Delaunay mesh\n"
            "topology surgery (past edges)\n"
            "iso-u (conformal potential)\n"
            "curvature singularities"
        ),
        font=footer_font,
        center=(64.5, 124.25, 0.0),
        scale=0.82,
        quality=0.48,
        line_height=2.9,
    )
    footer_process = G.text(
        text=(
            "field (K*)\n"
            "↓\n"
            "metric (l_ij)\n"
            "↓\n"
            "curvature (K)\n"
            "↓\n"
            "topology (flip)\n"
            "↓\n"
            "embedding (R^2)"
        ),
        font=footer_font,
        center=(104.2, 121.8, 0.0),
        scale=0.78,
        quality=0.48,
        line_height=1.62,
    )
    footer_axis_left = G.text(
        text="0",
        font=footer_font,
        center=(127.8, 137.4, 0.0),
        scale=0.78,
        quality=0.45,
    )
    footer_axis_right = G.text(
        text="1",
        font=footer_font,
        center=(195.0, 137.4, 0.0),
        scale=0.78,
        quality=0.45,
    )
    footer_axis_caption = G.text(
        text="continuation (β)",
        font=footer_font,
        center=(153.0, 139.25, 0.0),
        scale=0.72,
        quality=0.45,
    )

    return (
        L("pale rounded Ricci levels").layer(levels, color=(0.84, 0.84, 0.81), thickness=0.00043),
        L("historic short topology").layer(history, color=(0.60, 0.60, 0.57), thickness=0.00036),
        L("alternate crossing chords").layer(chords, color=(0.47, 0.47, 0.44), thickness=0.00028),
        L("unique-edge Delaunay mesh").layer(mesh, color=(0.17, 0.17, 0.155), thickness=0.00031),
        L("central mixed triangles").layer(collision, color=(0.34, 0.34, 0.31), thickness=0.00027),
        L("irregular vertex dashes").layer(ticks, color=(0.10, 0.10, 0.085), thickness=0.00036),
        L("radial focus fans").layer(fans, color=(0.38, 0.38, 0.35), thickness=0.00032),
        L("saddle to bottom fan").layer(vertical, color=(0.31, 0.31, 0.28), thickness=0.00036),
        L("pale narrow geodesics").layer(geodesics, color=(0.43, 0.43, 0.40), thickness=0.00027),
        L("solid P1 and P3 contours").layer(solid_contours, color=(0.25, 0.25, 0.22), thickness=0.00038),
        L("dashed P2 contours").layer(dashed_contours, color=(0.43, 0.43, 0.39), thickness=0.00036),
        L("three overlap singularities").layer(marks, color=(0.04, 0.04, 0.03), thickness=0.00048),
        L("source technical footer").layer(footer, color=(0.10, 0.10, 0.085), thickness=0.00042),
        L("five Delaunay beta disks").layer(morphs, color=(0.22, 0.22, 0.19), thickness=0.00034),
        L("footer title").layer(footer_title, color=(0.01, 0.01, 0.01), thickness=0.00062),
        L("footer description").layer(
            footer_description,
            color=(0.06, 0.06, 0.055),
            thickness=0.00050,
        ),
        L("footer line legend").layer(
            footer_legend,
            color=(0.08, 0.08, 0.07),
            thickness=0.00048,
        ),
        L("footer process").layer(
            footer_process,
            color=(0.05, 0.05, 0.045),
            thickness=0.00048,
        ),
        L("footer axis labels").layer(
            footer_axis_left,
            color=(0.06, 0.06, 0.055),
            thickness=0.00046,
        ),
        L("footer axis right").layer(
            footer_axis_right,
            color=(0.06, 0.06, 0.055),
            thickness=0.00046,
        ),
        L("footer axis caption").layer(
            footer_axis_caption,
            color=(0.06, 0.06, 0.055),
            thickness=0.00046,
        ),
    )
