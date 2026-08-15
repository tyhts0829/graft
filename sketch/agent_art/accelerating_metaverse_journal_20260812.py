from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from grafix import A4, E, G, L, primitive, run

# Reference-reproduction adjustment block.  The photographed page is already
# A5-proportioned; the surrounding phone viewer is intentionally excluded.
CANVAS_SIZE = A4
BACKGROUND_COLOR = (233 / 255, 229 / 255, 218 / 255)
LINE_THICKNESS = 0.001
SEED = 120826
LINE_COLORS = {"ink": (35 / 255, 30 / 255, 31 / 255)}
FILL_DENSITIES = {
    "title": 1000.0,
    "body": 165.0,
    "solid": 1000.0,
}
FILL_MIN_SPACINGS = {
    "title": 0.038,
    "typography": 0.045,
    "solid": 0.045,
}
REFERENCE_SIZE = (148.0, 210.0)
HORIZONTAL_SHIFT = 2.34
LAYOUT_FIT_SCALE = min(
    CANVAS_SIZE[0] / REFERENCE_SIZE[0],
    CANVAS_SIZE[1] / REFERENCE_SIZE[1],
)
LAYOUT_CROP = (0.0, 0.0, 0.0, 0.0)
LAYOUT_OFFSET = (
    (CANVAS_SIZE[0] - REFERENCE_SIZE[0] * LAYOUT_FIT_SCALE) / 2.0 + HORIZONTAL_SHIFT,
    (CANVAS_SIZE[1] - REFERENCE_SIZE[1] * LAYOUT_FIT_SCALE) / 2.0,
)

# All dimensions below are millimetres measured from the attached A5 page.
ELLIPSE_TOP = 20.0
ELLIPSE_BOTTOM = 120.0
ELLIPSE_LEFT = 24.0
ELLIPSE_RADII_X = (12.5, 25.0, 37.5, 50.0)
DASH_LENGTH = 1.25
DASH_GAP = 0.75
TITLE_POSITION = (5.25, 139.27)
TITLE_SCALE = 8.1
TITLE_X_SCALE = 0.906
TITLE_LINE_HEIGHT = 0.89
BODY_POSITION = (74.25, 141.35)
BODY_SCALE = 2.40
BODY_LINE_HEIGHT = 1.14

SERIF_FONT = "/System/Library/Fonts/Supplemental/Times New Roman.ttf"
TITLE_FONT = "/System/Library/Fonts/Supplemental/PTSerif.ttc"

Point = tuple[float, float]
Polyline = list[Point]

BODY_TEXT = (
    "i.        INCREASING SPEED, MOMENTUM, or progress. In\n"
    "physics, it refers to the rate of change of velocity over time. In\n"
    "business and innovation, it describes the act of rapidly\n"
    "advancing growth, development, or adoption of new\n"
    "technologies, ideas, or systems.\n"
    "\n"
    "ii.       A COLLECTIVE VIRTUAL SPACE where physical and\n"
    "digital realities merge. Encompasses persistent, immersive\n"
    "environments—often 3D or augmented—where users interact\n"
    "through avatars, assets, and experiences. Viewed as the next\n"
    "evolution of the internet: spatial, social, and sensory.\n"
    "\n"
    "In all seriousness, the only major (and this is major) downside\n"
    "to digital art is technological risk. You are entrusting your work\n"
    "to be maintained on a specific blockchain or server. If there is a\n"
    "security breach, a hack, then your artwork could be deleted.\n"
    "You could reupload the piece of art once the platform has been\n"
    "secured, but it would lose all of the history attached to that\n"
    "specific piece: not the end of the world, but far from ideal. This\n"
    "is why choosing the right technology platform for digital art."
)


def _pack(polylines: list[Polyline]) -> tuple[np.ndarray, np.ndarray]:
    """Pack measured page linework into Grafix primitive buffers."""

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


def _polyline(
    lines: list[Polyline], points: list[Point], *, closed: bool = False
) -> None:
    lines.append([*points, points[0]] if closed and points else points)


def _ellipse_points(cx: float, cy: float, rx: float, ry: float) -> Polyline:
    return [
        (
            cx + rx * math.cos(math.tau * index / 256.0),
            cy + ry * math.sin(math.tau * index / 256.0),
        )
        for index in range(256)
    ]


@lru_cache(maxsize=1)
def _page_linework_data() -> tuple[np.ndarray, np.ndarray]:
    lines: list[Polyline] = []
    cy = (ELLIPSE_TOP + ELLIPSE_BOTTOM) / 2.0
    ry = (ELLIPSE_BOTTOM - ELLIPSE_TOP) / 2.0

    # The first and third orbital contours are continuous.  All four ellipses
    # share the same left tangent, top, and bottom, exactly as in the page.
    for rx in (ELLIPSE_RADII_X[0], ELLIPSE_RADII_X[2]):
        _polyline(
            lines,
            _ellipse_points(ELLIPSE_LEFT + rx, cy, rx, ry),
            closed=True,
        )

    # Two fine rules divide the graphic, editorial copy, and footer.
    lines.extend(
        (
            [(5.24, 136.72), (138.18, 136.72)],
            [(5.24, 202.34), (138.18, 202.34)],
            [(22.92, 202.34), (22.92, 207.00)],
            [(120.06, 202.34), (120.06, 207.00)],
        )
    )
    return _pack(lines)


@primitive
def page_linework() -> tuple[np.ndarray, np.ndarray]:
    """Return the continuous ellipses, rules, and footer dividers."""

    coords, offsets = _page_linework_data()
    return coords.copy(), offsets.copy()


def _combine(geometries: list | tuple):
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
    font: str = SERIF_FONT,
    align: str = "left",
    line_height: float = 1.0,
    suffix: str = "",
):
    summary = " ".join(text.split())[:24]
    return G(name=f"Text / {summary}").text(
        text=text,
        font=font,
        font_index=0,
        text_align=align,
        letter_spacing_em=0.0,
        line_height=line_height,
        quality=0.42,
        center=(x, y, 0.0),
        scale=scale,
        key="accelerating-metaverse-type",
        instance_key=f"{x:.3f}:{y:.3f}:{scale:.3f}:{suffix}:{text}",
    )


def _dashed_ellipses():
    cy = (ELLIPSE_TOP + ELLIPSE_BOTTOM) / 2.0
    ry = (ELLIPSE_BOTTOM - ELLIPSE_TOP) / 2.0
    geometries = []
    for index, rx in enumerate((ELLIPSE_RADII_X[1], ELLIPSE_RADII_X[3])):
        ellipse = G(name=f"Dashed ellipse / {index + 1}").ellipse(
            radius_x=rx,
            radius_y=ry,
            segments=320,
            center=(ELLIPSE_LEFT + rx, cy, 0.0),
            key="accelerating-metaverse-dashed-ellipse",
            instance_key=index,
        )
        geometries.append(
            E(name=f"Dash pattern / ellipse {index + 1}").dash(
                dash_length=DASH_LENGTH,
                gap_length=DASH_GAP,
                offset=0.0,
                offset_jitter=0.0,
                key="accelerating-metaverse-dash-pattern",
                instance_key=index,
            )(ellipse)
        )
    return _combine(geometries)


def _solid_marks():
    marks = [
        G.circle(radius=1.55, segments=72, center=(73.74, 80.98, 0.0)),
        G.circle(radius=1.55, segments=72, center=(92.66, 116.42, 0.0)),
    ]
    return _combine(marks)


def _title_typography():
    title = _text(
        "Accelerating\nthe metaverse.",
        0.0,
        0.0,
        TITLE_SCALE,
        font=TITLE_FONT,
        line_height=TITLE_LINE_HEIGHT,
        suffix="title",
    )
    title = E(name="Title / measured editorial proportions").affine(
        auto_center=False,
        pivot=(0.0, 0.0, 0.0),
        scale=(TITLE_X_SCALE, 1.0, 1.0),
        delta=(TITLE_POSITION[0], TITLE_POSITION[1], 0.0),
        key="accelerating-metaverse-title-proportion",
    )(title)
    return _combine(
        [
            title,
            _text("(i)", 47.15, 139.67, 2.25, suffix="title-note-i"),
            _text("(ii)", 52.35, 146.70, 2.25, suffix="title-note-ii"),
        ]
    )


def _body_typography():
    return _combine(
        [
            _text("• Journal Entry", 5.05, 4.65, 1.55, suffix="eyebrow"),
            _text(
                BODY_TEXT,
                BODY_POSITION[0],
                BODY_POSITION[1],
                BODY_SCALE,
                line_height=BODY_LINE_HEIGHT,
                suffix="body",
            ),
            _text("14: 09 / HESST", 5.25, 203.10, 1.20, suffix="footer-left-1"),
            _text("27   05   2024", 5.25, 205.08, 1.20, suffix="footer-left-2"),
            _text(". Themes", 28.30, 203.10, 1.20, suffix="footer-mid-1"),
            _text(
                "Risk   NFTs   Investments",
                28.30,
                205.08,
                1.08,
                suffix="footer-mid-2",
            ),
            _text(
                "[loc: pgs]",
                138.18,
                203.10,
                1.20,
                align="right",
                suffix="footer-right-1",
            ),
            _text(
                "P: 00098", 138.18, 205.08, 1.20, align="right", suffix="footer-right-2"
            ),
        ]
    )


def draw(t: float):
    del t
    fit = E(name="Reference page / exact A5 fit").affine(
        auto_center=False,
        pivot=(0.0, 0.0, 0.0),
        scale=(LAYOUT_FIT_SCALE, LAYOUT_FIT_SCALE, 1.0),
        delta=(LAYOUT_OFFSET[0], LAYOUT_OFFSET[1], 0.0),
        key="accelerating-metaverse-a5-fit",
    )
    title_fill = E(name="Display typography / dense ink fill").fill(
        angle_sets=6,
        angle=45.0,
        density=FILL_DENSITIES["title"],
        min_spacing=FILL_MIN_SPACINGS["title"],
        remove_boundary=False,
        key="accelerating-metaverse-title-fill",
    )(_title_typography())
    body_fill = E(name="Editorial typography / printed ink fill").fill(
        angle_sets=6,
        angle=45.0,
        density=FILL_DENSITIES["body"],
        min_spacing=FILL_MIN_SPACINGS["typography"],
        remove_boundary=False,
        key="accelerating-metaverse-body-fill",
    )(_body_typography())
    mark_fill = E(name="Circular marks / dense ink fill").fill(
        angle_sets=6,
        angle=45.0,
        density=FILL_DENSITIES["solid"],
        min_spacing=FILL_MIN_SPACINGS["solid"],
        remove_boundary=False,
        key="accelerating-metaverse-mark-fill",
    )(_solid_marks())

    linework = E(name="Photographed ink / fine line weight").bold(
        count=3,
        radius=0.055,
        seed=SEED,
        key="accelerating-metaverse-line-weight",
    )(G(name="Continuous ellipses and page rules").page_linework())
    dashes = E(name="Photographed ink / dashed line weight").bold(
        count=3,
        radius=0.055,
        seed=SEED + 1,
        key="accelerating-metaverse-dash-weight",
    )(_dashed_ellipses())

    geometry = (
        fit(linework),
        fit(dashes),
        fit(title_fill),
        fit(body_fill),
        fit(mark_fill),
    )
    return L(name="Accelerating the metaverse / single ink layer").layer(
        geometry,
        color=LINE_COLORS["ink"],
        thickness=LINE_THICKNESS,
    )


if __name__ == "__main__":
    run(
        draw,
        run_id="accelerating_metaverse_journal_20260812",
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
