from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from grafix import E, G, L, primitive, run

# Reference-reproduction adjustment block.  The source photograph is fitted
# uniformly to A5; its near-A5 aspect ratio is not stretched or cropped.
CANVAS_SIZE = (148, 210)
BACKGROUND_COLOR = (250 / 255, 246 / 255, 239 / 255)
LINE_THICKNESS = 0.001
SEED = 70524
LINE_COLORS = {
    "ink": (9 / 255, 10 / 255, 10 / 255),
    "red": (194 / 255, 43 / 255, 47 / 255),
}
FILL_DENSITIES = {
    "typography": 1000.0,
    "solid": 1000.0,
}
FILL_MIN_SPACINGS = {
    "typography": 0.05,
    "solid": 0.05,
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
DISPLAY_FONT = "/System/Library/Fonts/NewYork.ttf"
SANS_FONT = "/System/Library/Fonts/HelveticaNeue.ttc"
SANS_REGULAR_INDEX = 0
SANS_MEDIUM_INDEX = 0
GRID_BOUNDS = (9.62, 40.18, 138.58, 164.05)
GRID_SHAPE = (102, 98)
FLOW_SOURCE = (80.92, 101.88)
FLOW_ATTRACTOR = (124.0, 101.88)
FLOW_INTEGRATION_STEPS = 6
FLOW_TRAVEL_TIME = 1.25

Point = tuple[float, float]
Polyline = list[Point]


def _pack(polylines: list[Polyline]) -> tuple[np.ndarray, np.ndarray]:
    """Pack explicit linework into Grafix primitive buffers."""

    vertices: list[tuple[float, float, float]] = []
    offsets = [0]
    for polyline in polylines:
        if len(polyline) < 2:
            continue
        vertices.extend((float(x), float(y), 0.0) for x, y in polyline)
        offsets.append(len(vertices))
    coords = np.asarray(vertices, dtype=np.float32).reshape((-1, 3))
    index = np.asarray(offsets, dtype=np.int32)
    return np.ascontiguousarray(coords), np.ascontiguousarray(index)


def _line(lines: list[Polyline], a: Point, b: Point) -> None:
    lines.append([a, b])


def _ellipse(
    lines: list[Polyline],
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    *,
    angle: float = 0.0,
    samples: int = 240,
) -> None:
    theta = math.radians(angle)
    cos_theta = math.cos(theta)
    sin_theta = math.sin(theta)
    points: Polyline = []
    for index in range(samples):
        phase = math.tau * index / samples
        x = rx * math.cos(phase)
        y = ry * math.sin(phase)
        points.append(
            (
                cx + x * cos_theta - y * sin_theta,
                cy + x * sin_theta + y * cos_theta,
            )
        )
    lines.append([*points, points[0]])


def _dashed(polyline: Polyline, dash: float, gap: float) -> list[Polyline]:
    """Split a sampled polyline into deterministic dash strokes."""

    if len(polyline) < 2:
        return []
    output: list[Polyline] = []
    drawing = True
    remaining = dash
    current: Polyline = []
    for start, end in zip(polyline, polyline[1:]):
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
                remaining = dash if drawing else gap
    if drawing and len(current) >= 2:
        output.append(current)
    return output


def _smoothstep(value: float) -> float:
    """Return a cubic 0..1 easing value for an already normalized input."""

    amount = min(1.0, max(0.0, value))
    return amount * amount * (3.0 - 2.0 * amount)


def _flow_velocity(x: float, y: float) -> tuple[float, float, float]:
    """Evaluate the shared source-to-attractor flow used by dots and strokes."""

    source_x, source_y = FLOW_SOURCE
    attractor_x, attractor_y = FLOW_ATTRACTOR
    dx = x - source_x
    dy = y - source_y

    # Compact support preserves the exact square lattice outside the flow.
    normalized_radius = math.sqrt((dx / 59.0) ** 2 + (dy / 39.0) ** 2)
    envelope = _smoothstep((1.0 - normalized_radius) / 0.28)
    if envelope <= 0.0:
        return 0.0, 0.0, 0.0

    # An anisotropic source opens the lattice, with a subtle clockwise curl.
    radius = math.sqrt(dx * dx + (1.10 * dy) ** 2 + 1.5**2)
    curl = 0.22 * math.exp(-0.5 * (radius / 24.0) ** 2)
    source_x_velocity = dx / radius - curl * 1.10 * dy / radius
    source_y_velocity = 0.92 * dy / radius + curl * dx / radius

    # The right half smoothly changes from an expanding field to a narrow jet.
    pull = 1.0 / (1.0 + math.exp(-(x - 94.5) / 6.5))
    wake_width = 11.0 + 0.20 * max(attractor_x - x, 0.0)
    wake_envelope = math.exp(-0.5 * (dy / wake_width) ** 2)
    target_x_velocity = math.tanh((attractor_x - x) / 10.0)
    target_y_velocity = -0.85 * math.tanh(dy / 8.5)

    velocity_x = envelope * (
        (1.0 - pull) * 1.15 * source_x_velocity
        + pull * wake_envelope * 1.80 * target_x_velocity
    )
    velocity_y = envelope * (
        (1.0 - pull) * 1.15 * source_y_velocity
        + pull * wake_envelope * 1.80 * target_y_velocity
    )
    return velocity_x, velocity_y, math.hypot(velocity_x, velocity_y)


def _advect_grid_point(
    x: float,
    y: float,
    *,
    travel: float = 1.0,
) -> tuple[float, float, float]:
    """Move one exact lattice point through the shared flow by a fixed time."""

    position_x = x
    position_y = y
    step = FLOW_TRAVEL_TIME * travel / FLOW_INTEGRATION_STEPS
    for _ in range(FLOW_INTEGRATION_STEPS):
        velocity_x, velocity_y, _ = _flow_velocity(position_x, position_y)
        midpoint_x = position_x + 0.5 * step * velocity_x
        midpoint_y = position_y + 0.5 * step * velocity_y
        midpoint_velocity_x, midpoint_velocity_y, _ = _flow_velocity(
            midpoint_x,
            midpoint_y,
        )
        position_x += midpoint_velocity_x * step
        position_y += midpoint_velocity_y * step
    displacement = math.hypot(position_x - x, position_y - y)
    return position_x, position_y, displacement


@lru_cache(maxsize=1)
def _flowing_grid_data() -> tuple[np.ndarray, np.ndarray]:
    """Advect a strict regular lattice and render its moved point positions."""

    lines: list[Polyline] = []
    x0, y0, x1, y1 = GRID_BOUNDS
    nx, ny = GRID_SHAPE
    for row in range(ny):
        source_y = y0 + (y1 - y0) * row / (ny - 1)
        for col in range(nx):
            source_x = x0 + (x1 - x0) * col / (nx - 1)
            half_length = 0.075 if (row + col) % 7 else 0.092
            start_x, start_y, _ = _advect_grid_point(
                source_x - half_length,
                source_y,
            )
            end_x, end_y, _ = _advect_grid_point(
                source_x + half_length,
                source_y,
            )
            _line(lines, (start_x, start_y), (end_x, end_y))
    return _pack(lines)


@primitive
def vectors_change_flowing_grid() -> tuple[np.ndarray, np.ndarray]:
    """Return grid points after deterministic advection through the field."""

    coords, offsets = _flowing_grid_data()
    return coords.copy(), offsets.copy()


@lru_cache(maxsize=1)
def _poster_linework_data() -> tuple[np.ndarray, np.ndarray]:
    lines: list[Polyline] = []

    # Header and footer rules.
    _line(lines, (6.05, 14.07), (142.28, 14.07))
    _line(lines, (6.25, 171.56), (141.50, 171.56))
    _line(lines, (39.25, 173.25), (39.25, 192.55))
    _line(lines, (110.55, 173.25), (110.55, 192.55))

    # Horizontal datum, slightly heavier in the source through broken overlays.
    _line(lines, (6.87, 101.89), (141.79, 101.89))

    # Three measured orbital paths: two broad cycles and the tall dark orbit.
    _ellipse(lines, 73.80, 103.25, 50.25, 55.68, angle=-0.7)
    _ellipse(lines, 73.64, 100.55, 46.65, 42.18, angle=1.4)
    _ellipse(lines, 45.72, 101.57, 21.73, 59.07, angle=-0.03)

    # Dashed influence cycle crossing the top and bottom nodes.
    dashed_ellipse: Polyline = []
    cx, cy, rx, ry, angle = 63.15, 101.88, 39.40, 53.75, math.radians(-0.8)
    ca, sa = math.cos(angle), math.sin(angle)
    for index in range(401):
        phase = math.tau * index / 400
        x, y = rx * math.cos(phase), ry * math.sin(phase)
        dashed_ellipse.append((cx + x * ca - y * sa, cy + x * sa + y * ca))
    lines.extend(_dashed(dashed_ellipse, 0.68, 0.72))

    # Footer table hairlines.
    for y in (177.65, 182.65, 187.65):
        _line(lines, (114.65, y), (141.25, y))

    # Legend symbols.
    _line(lines, (67.45, 178.70), (73.20, 178.70))
    for x in np.linspace(86.20, 92.10, 5):
        _line(lines, (float(x), 178.70), (float(x + 0.62), 178.70))

    # Lower-left 5 x 5 point key.
    for row in range(5):
        for col in range(5):
            x = 6.45 + col * 1.47
            y = 197.60 + row * 1.55
            _line(lines, (x - 0.12, y), (x + 0.12, y))

    # Footer globe mark.
    globe_cx, globe_cy, globe_r = 137.18, 201.03, 3.03
    _ellipse(lines, globe_cx, globe_cy, globe_r, globe_r, samples=96)
    _line(lines, (globe_cx - globe_r, globe_cy), (globe_cx + globe_r, globe_cy))
    _ellipse(lines, globe_cx, globe_cy, globe_r * 0.49, globe_r, samples=80)
    for latitude in (-1.45, 1.45):
        chord = math.sqrt(globe_r * globe_r - latitude * latitude)
        _line(
            lines,
            (globe_cx - chord, globe_cy + latitude),
            (globe_cx + chord, globe_cy + latitude),
        )

    return _pack(lines)


@primitive
def vectors_change_linework() -> tuple[np.ndarray, np.ndarray]:
    """Return the rules, dot matrix, orbits, datum, and footer symbols."""

    coords, offsets = _poster_linework_data()
    return coords.copy(), offsets.copy()


@lru_cache(maxsize=1)
def _vector_field_data() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(SEED)
    lines: list[Polyline] = []

    # An anisotropic field: short tangent/radial dashes flow from the left,
    # expand around the focus, then compress into the dark right-hand attractor.
    focus_x, focus_y = FLOW_SOURCE
    for row in range(59):
        y_base = 72.5 + row * (57.5 / 58.0)
        for col in range(85):
            x_base = 35.0 + col * (93.0 / 84.0)
            x, y, _ = _advect_grid_point(x_base, y_base, travel=1.12)
            x += rng.uniform(-0.12, 0.12)
            y += rng.uniform(-0.10, 0.10)
            dx = x - focus_x
            dy = y - focus_y
            radius = math.hypot(dx, dy) + 1e-6

            # Elliptical envelope, open on the right and faint at far left.
            envelope = math.exp(-((dx / 49.0) ** 2 + (dy / 31.0) ** 2) * 1.15)
            right_bias = 0.23 + 0.77 / (1.0 + math.exp(-(x - 64.0) / 11.0))
            if rng.random() > min(0.98, envelope * right_bias * 1.42):
                continue

            # Every mark follows the same field that moved its underlying grid.
            vx, vy, speed = _flow_velocity(x, y)
            if speed > 1e-9:
                vx /= speed
                vy /= speed
            pull = 1.0 / (1.0 + math.exp(-(x - 98.0) / 7.0))
            angle = math.atan2(vy, vx) + rng.normal(0.0, 0.10)

            length = 0.45 + 0.49 * envelope + 0.42 * pull
            length *= rng.uniform(0.68, 1.28)
            # Break marks into pairs to imitate the reference's fine double dashes.
            gap = 0.14
            half = length * 0.5
            ux, uy = math.cos(angle), math.sin(angle)
            cx = x + ux * rng.uniform(-0.12, 0.12)
            cy = y + uy * rng.uniform(-0.12, 0.12)
            if length > 0.70 and rng.random() < 0.72:
                a0 = -half
                a1 = -gap * 0.5
                b0 = gap * 0.5
                b1 = half
                _line(lines, (cx + ux * a0, cy + uy * a0), (cx + ux * a1, cy + uy * a1))
                _line(lines, (cx + ux * b0, cy + uy * b0), (cx + ux * b1, cy + uy * b1))
            else:
                _line(lines, (cx - ux * half, cy - uy * half), (cx + ux * half, cy + uy * half))

    # Dense layered wake around the right attractor.
    for index in range(2200):
        x = rng.normal(116.8, 8.2)
        y_spread = 0.55 + max(0.0, 125.0 - x) * 0.105
        y = focus_y + rng.normal(0.0, y_spread)
        if x < 91.0 or x > 128.2 or y < 76.0 or y > 129.0:
            continue
        taper = max(0.0, 1.0 - abs(y - focus_y) / max(1.0, y_spread * 2.7))
        length = rng.uniform(0.65, 1.75) * (0.65 + 0.55 * taper)
        angle = rng.normal(0.0, 0.075 + 0.075 * (1.0 - taper))
        ux, uy = math.cos(angle), math.sin(angle)
        _line(lines, (x - ux * length / 2, y - uy * length / 2), (x + ux * length / 2, y + uy * length / 2))

    # Sparse particles extending to the left of the structured field.
    for _ in range(780):
        x = rng.uniform(22.0, 77.0)
        y = rng.normal(focus_y, 15.5 + 0.18 * (77.0 - x))
        if not (62.0 < y < 142.0):
            continue
        length = rng.uniform(0.18, 0.48)
        angle = math.atan2(y - focus_y, x - focus_x) + rng.normal(0.0, 0.2)
        ux, uy = math.cos(angle), math.sin(angle)
        _line(lines, (x - ux * length / 2, y - uy * length / 2), (x + ux * length / 2, y + uy * length / 2))

    return _pack(lines)


@primitive
def vectors_change_field() -> tuple[np.ndarray, np.ndarray]:
    """Return the deterministic emergent vector field."""

    coords, offsets = _vector_field_data()
    return coords.copy(), offsets.copy()


def _text(
    content: str,
    x: float,
    y: float,
    scale: float,
    *,
    font: str = SANS_FONT,
    font_index: int = SANS_REGULAR_INDEX,
    align: str = "left",
    spacing: float = 0.0,
    line_height: float = 1.22,
):
    return G(name=f"Text / {' '.join(content.split())[:32]}").text(
        text=content,
        font=font,
        font_index=font_index,
        text_align=align,
        letter_spacing_em=spacing,
        line_height=line_height,
        quality=0.55,
        center=(x, y, 0.0),
        scale=scale,
        key="vectors-change-text",
        instance_key=f"{content}|{x:.3f}|{y:.3f}",
    )


def _combine(geometries: list | tuple):
    result = geometries[0]
    for geometry in geometries[1:]:
        result = result + geometry
    return result


def _typography_geometry():
    text_items = (
        # Header microcopy.
        _text("Field Notebook", 6.08, 6.55, 1.22, font_index=SANS_MEDIUM_INDEX),
        _text("No. 07", 6.08, 8.72, 1.22, font_index=SANS_MEDIUM_INDEX),
        _text(
            "OBSERVE\nMODEL\nEXTEND",
            141.80,
            6.48,
            1.13,
            font_index=SANS_MEDIUM_INDEX,
            align="right",
            spacing=0.07,
            line_height=1.18,
        ),
        # Display title and the small footnote marker.
        _text("Vectors of Change,", 6.16, 18.70, 5.76, font=DISPLAY_FONT),
        _text("Orbits of Intent", 6.16, 26.15, 5.76, font=DISPLAY_FONT),
        _text("(a)", 51.40, 28.30, 1.25, font=SANS_FONT, font_index=SANS_MEDIUM_INDEX),
        _text(
            "A study of directional fields,\nemergent order, and the\nmomentum of collective\nbecoming.",
            101.75,
            21.75,
            1.72,
            font_index=SANS_MEDIUM_INDEX,
            line_height=1.22,
        ),
        # Footer left column.
        _text("PRINCIPLE", 6.25, 174.15, 0.96, font_index=SANS_MEDIUM_INDEX, spacing=0.05),
        _text("01 — 03", 6.25, 177.20, 2.45, font=DISPLAY_FONT),
        _text(
            "Systems evolve through\niterative feedback between\ndirection and constraint.",
            6.25,
            183.05,
            1.10,
            line_height=1.35,
        ),
        # Legend.
        _text("NODES", 49.65, 177.55, 0.99, font_index=SANS_MEDIUM_INDEX, spacing=0.07),
        _text("Anchors of attention\nPoints of agency", 46.20, 182.45, 1.08, line_height=1.40),
        _text("ORBITS", 73.60, 177.55, 0.99, font_index=SANS_MEDIUM_INDEX, spacing=0.07),
        _text("Paths of influence\nCycles of exchange", 67.45, 182.45, 1.08, line_height=1.40),
        _text("FIELD", 94.35, 177.55, 0.99, font_index=SANS_MEDIUM_INDEX, spacing=0.07),
        _text("Emergent direction\nFrom many, one flow", 89.20, 182.45, 1.08, line_height=1.40),
        # Right metadata table.
        _text("SCALE", 114.60, 174.35, 0.94, font_index=SANS_MEDIUM_INDEX, spacing=0.05),
        _text("1 — ∞", 129.55, 174.25, 1.05, font_index=SANS_MEDIUM_INDEX),
        _text("MODEL", 114.60, 179.35, 0.94, font_index=SANS_MEDIUM_INDEX, spacing=0.05),
        _text("Open", 129.55, 179.20, 1.05),
        _text("REFERENCE", 114.60, 184.35, 0.94, font_index=SANS_MEDIUM_INDEX, spacing=0.03),
        _text("N/A", 129.55, 184.20, 1.05),
        _text("DATE", 114.60, 189.35, 0.94, font_index=SANS_MEDIUM_INDEX, spacing=0.05),
        _text("∞ / 05 / 24", 129.55, 189.20, 1.05),
        # Lower margin notes.
        _text(
            "Cartography is not territory.\nBut it can reveal its tendencies.",
            16.00,
            198.35,
            0.90,
            line_height=1.33,
        ),
        _text("∴ 39.8283° N, 98.5795° W", 63.85, 203.15, 0.88, align="center", spacing=0.02),
    )
    return _combine(text_items)


def _solid_black_geometry():
    shapes = [
        G.circle(radius=1.33, segments=72, center=(23.88, 101.89, 0.0)),
        G.circle(radius=0.92, segments=72, center=(67.38, 101.89, 0.0)),
        G.circle(radius=1.35, segments=72, center=(73.76, 48.42, 0.0)),
        G.circle(radius=1.12, segments=72, center=(70.65, 155.86, 0.0)),
        G.circle(radius=1.38, segments=72, center=(133.94, 101.89, 0.0)),
        G.circle(radius=1.20, segments=72, center=(47.30, 178.65, 0.0)),
    ]
    return _combine(shapes)


def draw(t: float):
    del t
    fit = E(name="Reference photograph / uniform A5 contain").affine(
        auto_center=False,
        pivot=(0.0, 0.0, 0.0),
        scale=(LAYOUT_FIT_SCALE, LAYOUT_FIT_SCALE, 1.0),
        delta=(LAYOUT_OFFSET[0], LAYOUT_OFFSET[1], 0.0),
        key="reference-a5-fit",
    )

    typography = E(name="Typography / dense plotted fill").fill(
        angle_sets=2,
        angle=45.0,
        density=FILL_DENSITIES["typography"],
        min_spacing=FILL_MIN_SPACINGS["typography"],
        remove_boundary=False,
        key="typography-fill",
    )(_typography_geometry())
    solids = E(name="Nodes / dense plotted fill").fill(
        angle_sets=6,
        angle=45.0,
        density=FILL_DENSITIES["solid"],
        min_spacing=FILL_MIN_SPACINGS["solid"],
        remove_boundary=False,
        key="solid-fill",
    )(_solid_black_geometry())
    red_mark = E(name="Notebook index mark / red fill").fill(
        angle_sets=4,
        angle=45.0,
        density=FILL_DENSITIES["solid"],
        min_spacing=FILL_MIN_SPACINGS["solid"],
        remove_boundary=False,
        key="red-mark-fill",
    )(
        G.rect(width=0.88, height=0.88, center=(13.48, 9.93, 0.0))
    )

    black_geometry = (
        fit(
            G(
                name="Rules, dot matrix, orbital paths, and footer marks"
            ).vectors_change_linework()
        ),
        fit(G(name="Regular lattice / advected through field").vectors_change_flowing_grid()),
        fit(G(name="Emergent directional vector field").vectors_change_field()),
        fit(typography),
        fit(solids),
    )
    return (
        L(name="Black ink / field notebook").layer(
            black_geometry,
            color=LINE_COLORS["ink"],
            thickness=LINE_THICKNESS,
        ),
        L(name="Red ink / notebook index").layer(
            fit(red_mark),
            color=LINE_COLORS["red"],
            thickness=LINE_THICKNESS,
        ),
    )


if __name__ == "__main__":
    run(
        draw,
        run_id="vectors_of_change_20260812",
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
