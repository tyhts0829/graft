from __future__ import annotations

import math

import numpy as np

from grafix import E, G, L, primitive, run


CANVAS = (905, 1280)
SEED = 87024

PAPER = (0.94, 0.925, 0.89)
PAPER_LIGHT = (0.968, 0.958, 0.928)
GRAPHITE = (0.045, 0.05, 0.048)
DARK_GRAY = (0.20, 0.21, 0.20)
MID_GRAY = (0.46, 0.46, 0.43)
PALE_GRAY = (0.70, 0.69, 0.64)


def _pack(paths: list[list[tuple[float, float, float]]]) -> tuple[np.ndarray, np.ndarray]:
    """複数の折れ線を custom primitive 用の正規形へまとめる。"""

    arrays = [np.asarray(path, dtype=np.float32) for path in paths]
    coords = np.ascontiguousarray(np.concatenate(arrays, axis=0), dtype=np.float32)
    lengths = np.asarray([len(path) for path in arrays], dtype=np.int32)
    offsets = np.empty(len(arrays) + 1, dtype=np.int32)
    offsets[0] = 0
    np.cumsum(lengths, out=offsets[1:])
    return coords, np.ascontiguousarray(offsets, dtype=np.int32)


def _segment(
    x0: float, y0: float, x1: float, y1: float
) -> list[tuple[float, float, float]]:
    return [(x0, y0, 0.0), (x1, y1, 0.0)]


@primitive
def measured_paper_field() -> tuple[np.ndarray, np.ndarray]:
    """密な走査線でキャンバスを暖白の紙色にする。"""

    paths: list[list[tuple[float, float, float]]] = []
    for y in np.arange(-4.0, CANVAS[1] + 4.1, 3.0):
        paths.append(_segment(-7.0, float(y), CANVAS[0] + 7.0, float(y)))
    return _pack(paths)


@primitive
def left_disc_hatch() -> tuple[np.ndarray, np.ndarray]:
    """主円盤の左半分を極細水平線でほぼ黒く埋める。"""

    cx, cy, radius = 442.0, 480.0, 198.0
    paths: list[list[tuple[float, float, float]]] = []
    for y in np.arange(cy - radius + 0.5, cy + radius, 1.18):
        half = math.sqrt(max(0.0, radius * radius - (float(y) - cy) ** 2))
        if half > 0.7:
            paths.append(_segment(cx - half, float(y), cx + 0.35, float(y)))
    return _pack(paths)


@primitive
def right_disc_hatch() -> tuple[np.ndarray, np.ndarray]:
    """主円盤の右半分を中間灰の水平ハッチで埋める。"""

    cx, cy, radius = 442.0, 480.0, 198.0
    paths: list[list[tuple[float, float, float]]] = []
    for y in np.arange(cy - radius + 0.8, cy + radius, 1.55):
        half = math.sqrt(max(0.0, radius * radius - (float(y) - cy) ** 2))
        if half > 0.7:
            paths.append(_segment(cx - 0.35, float(y), cx + half, float(y)))
    return _pack(paths)


@primitive
def inner_disc_knockout() -> tuple[np.ndarray, np.ndarray]:
    """重なった主円盤を暖かな白円で測量図のように切り抜く。"""

    cx, cy, radius = 470.0, 530.0, 145.0
    paths: list[list[tuple[float, float, float]]] = []
    for y in np.arange(cy - radius - 2.0, cy + radius + 2.1, 2.45):
        half = math.sqrt(max(0.0, (radius + 1.6) ** 2 - (float(y) - cy) ** 2))
        if half > 0.5:
            paths.append(_segment(cx - half, float(y), cx + half, float(y)))
    return _pack(paths)


@primitive
def left_disc_dot_lattice() -> tuple[np.ndarray, np.ndarray]:
    """暗部に規則正しい微小な測点格子を置く。"""

    cx, cy, radius = 442.0, 480.0, 198.0
    paths: list[list[tuple[float, float, float]]] = []
    for row, y in enumerate(np.arange(294.0, 670.0, 8.15)):
        x_shift = 0.0 if row % 2 == 0 else 1.75
        for x in np.arange(254.0 + x_shift, 438.0, 7.0):
            if (float(x) - cx) ** 2 + (float(y) - cy) ** 2 < (radius - 5.0) ** 2:
                paths.append(_segment(float(x) - 0.22, float(y), float(x) + 0.22, float(y)))
    return _pack(paths)


@primitive
def diagonal_survey_bundles() -> tuple[np.ndarray, np.ndarray]:
    """指定された二本の測量方向に平行な髪線束を生成する。"""

    paths: list[list[tuple[float, float, float]]] = []

    def add_bundle(
        start: tuple[float, float],
        end: tuple[float, float],
        offsets: tuple[float, ...],
    ) -> None:
        x0, y0 = start
        x1, y1 = end
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        nx, ny = -dy / length, dx / length
        for index, offset in enumerate(offsets):
            trim_start = float((index % 3) * 2.2)
            trim_end = float(((index + 1) % 4) * 2.8)
            ux, uy = dx / length, dy / length
            paths.append(
                _segment(
                    x0 + nx * offset + ux * trim_start,
                    y0 + ny * offset + uy * trim_start,
                    x1 + nx * offset - ux * trim_end,
                    y1 + ny * offset - uy * trim_end,
                )
            )

    add_bundle((250.0, 635.0), (782.0, 383.0), (-8.0, -5.4, -3.2, -1.2, 0.6, 2.6, 5.1, 8.1))
    add_bundle((25.0, 1080.0), (402.0, 855.0), (-6.2, -3.8, -1.5, 0.7, 3.2, 6.0))
    return _pack(paths)


@primitive
def datum_axes_and_ticks() -> tuple[np.ndarray, np.ndarray]:
    """全高の基準軸、補助軸、円周の短い目盛をまとめる。"""

    paths: list[list[tuple[float, float, float]]] = [
        _segment(445.0, -8.0, 445.0, 1288.0),
        _segment(441.5, 42.0, 448.5, 42.0),
        _segment(441.5, 1233.0, 448.5, 1233.0),
        _segment(490.0, 202.0, 490.0, 1183.0),
    ]

    for y in range(78, 1241, 29):
        tick = 3.8 if y % 58 == 0 else 2.2
        paths.append(_segment(445.0 - tick, float(y), 445.0 + tick, float(y)))

    for angle in range(0, 360, 15):
        a = math.radians(float(angle))
        inner = 198.0 - (7.0 if angle % 45 == 0 else 3.5)
        outer = 198.0 + (7.0 if angle % 45 == 0 else 3.5)
        paths.append(
            _segment(
                442.0 + inner * math.cos(a),
                480.0 + inner * math.sin(a),
                442.0 + outer * math.cos(a),
                480.0 + outer * math.sin(a),
            )
        )
    return _pack(paths)


@primitive
def lower_measurement_field() -> tuple[np.ndarray, np.ndarray]:
    """中央下へ落ちる微細な縦線と点列を作る。"""

    paths: list[list[tuple[float, float, float]]] = []
    columns = (
        (414.0, 744.0, 950.0),
        (422.0, 772.0, 1028.0),
        (430.0, 730.0, 1087.0),
        (437.0, 756.0, 985.0),
        (453.0, 718.0, 1128.0),
        (461.0, 780.0, 948.0),
        (471.0, 744.0, 1066.0),
        (480.0, 762.0, 1153.0),
        (501.0, 732.0, 1016.0),
        (511.0, 790.0, 1102.0),
        (522.0, 748.0, 970.0),
    )
    for index, (x, y0, y1) in enumerate(columns):
        paths.append(_segment(x, y0, x, y1))
        cap = 3.0 + float(index % 3)
        paths.append(_segment(x - cap, y0, x + cap, y0))
        paths.append(_segment(x - 2.0, y1, x + 2.0, y1))

    for col, x in enumerate((418.0, 434.0, 457.0, 476.0, 505.0, 519.0)):
        for row, y in enumerate(np.arange(793.0 + col * 3.0, 1084.0, 13.0)):
            if (row + col) % 4 != 1:
                paths.append(_segment(x - 0.35, float(y), x + 0.35, float(y)))

    for y, x0, x1 in (
        (815.0, 402.0, 535.0),
        (869.0, 424.0, 552.0),
        (936.0, 391.0, 514.0),
        (1035.0, 409.0, 540.0),
    ):
        paths.append(_segment(x0, y, x1, y))
    return _pack(paths)


@primitive
def descending_black_blocks() -> tuple[np.ndarray, np.ndarray]:
    """縦の測点列へ小さな黒い方形を落とす。"""

    paths: list[list[tuple[float, float, float]]] = []
    blocks = (
        (438.0, 786.0, 5.0, 5.0),
        (486.0, 842.0, 7.0, 7.0),
        (463.0, 909.0, 4.5, 6.0),
        (508.0, 982.0, 6.0, 6.0),
        (433.0, 1049.0, 4.0, 8.0),
        (479.0, 1119.0, 5.0, 5.0),
    )
    for cx, cy, width, height in blocks:
        for y in np.arange(cy - height / 2.0, cy + height / 2.0 + 0.1, 1.05):
            paths.append(_segment(cx - width / 2.0, float(y), cx + width / 2.0, float(y)))
    return _pack(paths)


@primitive
def marginal_calibration_marks() -> tuple[np.ndarray, np.ndarray]:
    """余白の小型スケール、座標括弧、交点記号を置く。"""

    paths: list[list[tuple[float, float, float]]] = []
    # Upper-left scale.
    paths.extend(
        [
            _segment(66.0, 118.0, 185.0, 118.0),
            _segment(66.0, 114.0, 66.0, 122.0),
            _segment(185.0, 114.0, 185.0, 122.0),
            _segment(64.0, 248.0, 64.0, 322.0),
            _segment(60.0, 248.0, 68.0, 248.0),
            _segment(60.0, 322.0, 68.0, 322.0),
        ]
    )
    for x in np.linspace(66.0, 185.0, 13):
        height = 4.5 if abs((x - 66.0) % 29.75) < 0.2 else 2.4
        paths.append(_segment(float(x), 118.0 - height, float(x), 118.0 + height))

    # Sparse coordinate brackets and crosshairs.
    for x, y in ((215.0, 635.0), (782.0, 383.0), (402.0, 855.0), (446.0, 680.0), (491.0, 620.0)):
        paths.append(_segment(x - 5.0, y, x + 5.0, y))
        paths.append(_segment(x, y - 5.0, x, y + 5.0))

    paths.extend(
        [
            _segment(741.0, 708.0, 830.0, 708.0),
            _segment(741.0, 708.0, 741.0, 721.0),
            _segment(830.0, 708.0, 830.0, 721.0),
            _segment(91.0, 1134.0, 237.0, 1134.0),
            _segment(91.0, 1129.0, 91.0, 1139.0),
            _segment(237.0, 1129.0, 237.0, 1139.0),
        ]
    )
    return _pack(paths)


def draw(t: float):
    _ = t

    # Paper and tonal fields.
    paper = G.measured_paper_field()
    left_hatch = G.left_disc_hatch()
    right_hatch = G.right_disc_hatch()
    knockout = G.inner_disc_knockout()
    dots = G.left_disc_dot_lattice()

    # Precisely located circular construction.
    large_circle = G.circle(radius=270.0, segments=640, center=(448.0, 387.0, 0.0))
    primary_circle = G.circle(radius=198.0, segments=640, center=(442.0, 480.0, 0.0))
    primary_echo = G.circle(radius=202.5, segments=640, center=(442.0, 480.0, 0.0))
    inner_circle = G.circle(radius=145.0, segments=560, center=(470.0, 530.0, 0.0))
    lower_circle = G.circle(radius=176.0, segments=600, center=(446.0, 680.0, 0.0))
    orbit_circle = G.circle(radius=220.0, segments=640, center=(491.0, 620.0, 0.0))
    orbit_echo = G.circle(radius=216.0, segments=640, center=(491.0, 620.0, 0.0))

    bundles = G.diagonal_survey_bundles()
    datum = G.datum_axes_and_ticks()
    strong_vertical = G.line(
        center=(490.0, 640.0, 0.0),
        anchor="center",
        length=1060.0,
        angle=90.0,
    )
    lower_field = G.lower_measurement_field()
    blocks = G.descending_black_blocks()
    margins = G.marginal_calibration_marks()

    # Native Grafix text keeps the marks typographic rather than diagrammatic glyph hacks.
    label_upper_left = G.text(
        text="DATUM 01 / R270\n44.8  :  38.7",
        scale=7.4,
        quality=0.28,
        letter_spacing_em=0.12,
        line_height=1.35,
        center=(66.0, 132.0, 0.0),
    )
    label_left_mid = G.text(
        text="M—442.480\nHALF / FIELD",
        scale=6.8,
        quality=0.24,
        letter_spacing_em=0.14,
        line_height=1.28,
        center=(70.0, 352.0, 0.0),
    )
    label_right = E.rotate(rotation=(0.0, 0.0, 90.0))(
        G.text(
            text="MEASURED  RECONSTRUCTION  /  87024  /  V01",
            scale=6.8,
            quality=0.24,
            letter_spacing_em=0.16,
            center=(740.0, 240.0, 0.0),
        )
    )
    label_orbit = G.text(
        text="R220  /  AXIS 490\n+35° 12′",
        scale=6.8,
        quality=0.24,
        letter_spacing_em=0.12,
        line_height=1.3,
        center=(744.0, 726.0, 0.0),
    )
    label_lower_vertical = E.rotate(rotation=(0.0, 0.0, 90.0))(
        G.text(
            text="DATUM 445 / 12 DIV",
            scale=6.2,
            quality=0.22,
            letter_spacing_em=0.18,
            center=(389.0, 835.0, 0.0),
        )
    )
    label_bottom = G.text(
        text="RECONSTRUCTION FIELD\nSCALE  1 : 08     N—87024",
        scale=7.2,
        quality=0.26,
        letter_spacing_em=0.13,
        line_height=1.32,
        center=(91.0, 1150.0, 0.0),
    )

    wave_top = G.wave(
        kind="sine",
        length=154.0,
        amplitude=4.0,
        cycles=13.0,
        samples=420,
        center=(265.0, 73.0, 0.0),
    )
    wave_right = G.wave(
        kind="sine",
        length=170.0,
        amplitude=3.6,
        cycles=17.0,
        samples=520,
        angle=90.0,
        center=(824.0, 1014.0, 0.0),
    )

    return (
        L("warm paper").layer(paper, color=PAPER, thickness=0.005),
        L("left near-black hatch").layer(left_hatch, color=GRAPHITE, thickness=0.00135),
        L("right gray hatch").layer(right_hatch, color=MID_GRAY, thickness=0.00105),
        L("inner warm-white knockout").layer(knockout, color=PAPER_LIGHT, thickness=0.0048),
        L("large pale circle").layer(large_circle, color=PALE_GRAY, thickness=0.00045),
        L("primary echo").layer(primary_echo, color=PALE_GRAY, thickness=0.00035),
        L("primary rim").layer(primary_circle, color=GRAPHITE, thickness=0.00095),
        L("inner rim").layer(inner_circle, color=DARK_GRAY, thickness=0.00075),
        L("lower circle").layer(lower_circle, color=DARK_GRAY, thickness=0.00055),
        L("orbital echo").layer(orbit_echo, color=PALE_GRAY, thickness=0.00035),
        L("orbital circle").layer(orbit_circle, color=MID_GRAY, thickness=0.00055),
        L("negative dot lattice").layer(dots, color=PALE_GRAY, thickness=0.00085),
        L("diagonal bundles").layer(bundles, color=DARK_GRAY, thickness=0.00042),
        L("datum and ticks").layer(datum, color=GRAPHITE, thickness=0.00062),
        L("strong vertical axis").layer(strong_vertical, color=GRAPHITE, thickness=0.0023),
        L("lower measurements").layer(lower_field, color=DARK_GRAY, thickness=0.00040),
        L("black blocks").layer(blocks, color=GRAPHITE, thickness=0.00125),
        L("marginal calibration").layer(margins, color=DARK_GRAY, thickness=0.00042),
        L("upper waveform").layer(wave_top, color=MID_GRAY, thickness=0.00038),
        L("right waveform").layer(wave_right, color=MID_GRAY, thickness=0.00038),
        L("upper-left label").layer(label_upper_left, color=DARK_GRAY, thickness=0.00042),
        L("left label").layer(label_left_mid, color=MID_GRAY, thickness=0.00038),
        L("right vertical label").layer(label_right, color=DARK_GRAY, thickness=0.00042),
        L("orbit label").layer(label_orbit, color=MID_GRAY, thickness=0.00040),
        L("lower vertical label").layer(label_lower_vertical, color=DARK_GRAY, thickness=0.00038),
        L("bottom label").layer(label_bottom, color=DARK_GRAY, thickness=0.00042),
    )


if __name__ == "__main__":
    run(
        draw,
        canvas_size=CANVAS,
        render_scale=3.0,
        background_color=PAPER,
        parameter_gui=True,
        parameter_persistence=False,
    )
