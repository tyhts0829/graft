"""A5 concert-poster facsimile drawn with one black and one red ink layer."""

from __future__ import annotations

from grafix import E, G, L, P

CANVAS = (148, 210)

PAPER = (247 / 255, 246 / 255, 242 / 255)
BLACK = (9 / 255, 9 / 255, 9 / 255)
RED = (240 / 255, 74 / 255, 36 / 255)

FONT_SANS = "HelveticaNeue.ttc"
FONT_REGULAR_INDEX = 0
FONT_BOLD_INDEX = 1
FONT_ULTRALIGHT_INDEX = 5
FONT_LIGHT_INDEX = 7
FONT_MEDIUM_INDEX = 10
FONT_THIN_INDEX = 12

# The hidden preset guide defines all repeated horizontal and vertical axes.
SHOW_LAYOUT_GRID = False
GRID_MARGIN_LEFT = 5.75
GRID_MARGIN_RIGHT = 5.85
GRID_MARGIN_TOP = 4.9833333333
GRID_MARGIN_BOTTOM = 20.61
GRID_COLUMNS = 9
GRID_ROWS = 13
GRID_GUTTER_X = 2.2
GRID_GUTTER_Y = 3.0822222222
GRID_COLUMN_WIDTH = (
    CANVAS[0]
    - GRID_MARGIN_LEFT
    - GRID_MARGIN_RIGHT
    - (GRID_COLUMNS - 1) * GRID_GUTTER_X
) / GRID_COLUMNS
GRID_COLUMN_PITCH = GRID_COLUMN_WIDTH + GRID_GUTTER_X
GRID_ROW_HEIGHT = (
    CANVAS[1] - GRID_MARGIN_TOP - GRID_MARGIN_BOTTOM - (GRID_ROWS - 1) * GRID_GUTTER_Y
) / GRID_ROWS
GRID_ROW_PITCH = GRID_ROW_HEIGHT + GRID_GUTTER_Y

FREE_BAR_RIGHT = GRID_MARGIN_LEFT + 5 * GRID_COLUMN_WIDTH + 4 * GRID_GUTTER_X + 1.03
COARSE_BAR_CENTER_X = GRID_MARGIN_LEFT + 5 * GRID_COLUMN_PITCH + GRID_COLUMN_WIDTH / 2
FINE_BAR_CENTER_X = GRID_MARGIN_LEFT + 6 * GRID_COLUMN_PITCH + GRID_COLUMN_WIDTH / 2
PILLAR_CENTER_X = GRID_MARGIN_LEFT + 7 * GRID_COLUMN_PITCH + GRID_COLUMN_WIDTH / 2
COARSE_BAR_WIDTH = 12.55
FINE_BAR_WIDTH = 13.95
PILLAR_WIDTH = 13.35
BARCODE_TOP = GRID_MARGIN_TOP + 3 * GRID_ROW_PITCH
BARCODE_BOTTOM = GRID_MARGIN_TOP + 12 * GRID_ROW_PITCH
FOOTER_RAIL_Y = GRID_MARGIN_TOP + 12 * GRID_ROW_PITCH + GRID_ROW_HEIGHT
DOT_AXIS_X = 141.25


def _solid(geometry, *, key: str, spacing: float = 0.02):
    """Fill closed built-in geometry with evenly spaced horizontal strokes."""

    return E.fill(
        angle_sets=1,
        angle=0.0,
        density=1000.0,
        min_spacing=spacing,
        spacing_gradient=0.0,
        remove_boundary=False,
        key=key,
    )(geometry)


def _filled(geometry, *, spacing: float = 0.02):
    """Turn a font outline into a dense, even-odd plotter fill."""

    return E.fill(
        angle_sets=1,
        angle=0.0,
        density=1000.0,
        min_spacing=spacing,
        spacing_gradient=0.0,
        remove_boundary=False,
    )(geometry)


def _scale_text_x(
    geometry,
    *,
    center,
    factor: float,
    key: str,
    height_factor: float = 1.0,
):
    """Fit a text outline around its authored origin without moving its width."""

    return E.affine(
        auto_center=False,
        pivot=center,
        scale=(factor, height_factor, 1.0),
        key=key,
    )(geometry)


def draw(t: float):
    """Render the static facsimile; ``t`` is intentionally ignored."""

    _ = t

    # Toggleable construction guide.  Final artwork keeps it hidden, while
    # every barcode coordinate below is derived from the same column system.
    layout_grid = P.layout_grid_system(
        activate=SHOW_LAYOUT_GRID,
        canvas_w=float(CANVAS[0]),
        canvas_h=float(CANVAS[1]),
        axes="both",
        margin_l=GRID_MARGIN_LEFT,
        margin_r=GRID_MARGIN_RIGHT,
        margin_t=GRID_MARGIN_TOP,
        margin_b=GRID_MARGIN_BOTTOM,
        show_center=False,
        cols=GRID_COLUMNS,
        rows=GRID_ROWS,
        gutter_x=GRID_GUTTER_X,
        gutter_y=GRID_GUTTER_Y,
        show_column_centers=False,
        show_baseline=False,
        baseline_step=GRID_ROW_PITCH,
        baseline_offset=0.0,
        offset=(0.0, 0.0, 0.0),
    )

    # Repeated solid modules use the built-in rect -> fill -> repeat chain.
    upper_cross_motif = _solid(
        G.rect(
            width=3.8,
            height=0.65,
            center=(0.0, -0.025, 0.0),
            key="upper cross horizontal",
        )
        + G.rect(
            width=0.68,
            height=4.1,
            center=(0.0, 0.0, 0.0),
            key="upper cross vertical",
        ),
        key="upper cross fill",
    )
    upper_crosses = E.repeat(
        count=1,
        offset=(5.35, 0.0, 0.0),
        key="upper cross repeat",
    )(
        E.translate(
            delta=(7.85, 8.29, 0.0),
            key="upper cross position",
        )(upper_cross_motif)
    )

    black_dot_a = _solid(
        G.circle(
            radius=0.415,
            segments=16,
            center=(DOT_AXIS_X, 13.39, 0.0),
            key="black dot upper",
        ),
        key="black dot upper fill",
    )
    black_dot_b = _solid(
        G.circle(
            radius=0.415,
            segments=16,
            center=(DOT_AXIS_X, 20.66, 0.0),
            key="black dot lower",
        ),
        key="black dot lower fill",
    )
    black_dots = E.repeat(
        count=1,
        offset=(0.0, 2.43, 0.0),
        key="black upper dot pair",
    )(black_dot_a) + E.repeat(
        count=1,
        offset=(0.0, 2.44, 0.0),
        key="black lower dot pair",
    )(
        black_dot_b
    )

    # These widths deliberately remain irregular, so each source rect is
    # explicit; one built-in fill handles the combined closed geometry.
    free_bar_outlines = (
        G.rect(width=24.0, height=2.1, center=(69.1, 73.2, 0.0), key="free bar 01")
        + G.rect(width=12.0, height=2.1, center=(75.1, 78.8, 0.0), key="free bar 02")
        + G.rect(width=29.0, height=2.1, center=(66.6, 84.4, 0.0), key="free bar 03")
        + G.rect(width=14.0, height=2.1, center=(74.1, 90.0, 0.0), key="free bar 04")
        + G.rect(width=21.5, height=2.1, center=(70.35, 95.6, 0.0), key="free bar 05")
        + G.rect(width=12.0, height=2.1, center=(75.1, 101.2, 0.0), key="free bar 06")
        + G.rect(width=27.5, height=2.1, center=(67.35, 106.8, 0.0), key="free bar 07")
        + G.rect(width=19.2, height=2.1, center=(71.5, 112.4, 0.0), key="free bar 08")
        + G.rect(width=24.8, height=2.1, center=(68.7, 118.0, 0.0), key="free bar 09")
        + G.rect(width=13.5, height=2.1, center=(74.35, 123.6, 0.0), key="free bar 10")
        + G.rect(width=28.5, height=2.1, center=(66.85, 129.2, 0.0), key="free bar 11")
        + G.rect(width=24.8, height=2.1, center=(68.7, 134.8, 0.0), key="free bar 12")
        + G.rect(width=21.5, height=2.1, center=(70.35, 140.4, 0.0), key="free bar 13")
        + G.rect(width=13.8, height=2.1, center=(74.2, 146.0, 0.0), key="free bar 14")
        + G.rect(width=27.0, height=2.1, center=(67.6, 151.6, 0.0), key="free bar 15")
        + G.rect(width=18.2, height=2.1, center=(72.0, 157.2, 0.0), key="free bar 16")
        + G.rect(width=23.8, height=2.1, center=(69.2, 162.8, 0.0), key="free bar 17")
        + G.rect(width=12.0, height=2.1, center=(75.1, 168.4, 0.0), key="free bar 18")
        + G.rect(width=19.3, height=2.1, center=(71.45, 174.0, 0.0), key="free bar 19")
    )
    free_bars = E.translate(
        delta=(FREE_BAR_RIGHT - 81.1, -0.0385, 0.0),
        key="free bar phase center",
    )(
        E.repeat(
            count=3,
            offset=(0.0, 0.077, 0.0),
            key="free bar fill phases",
        )(_solid(free_bar_outlines, key="free bar fill"))
    )

    coarse_bar = _solid(
        G.rect(
            width=COARSE_BAR_WIDTH,
            height=1.78,
            center=(
                COARSE_BAR_CENTER_X,
                BARCODE_TOP + 1.78 / 2,
                0.0,
            ),
            key="coarse equalizer unit",
        ),
        key="coarse equalizer fill",
    )
    coarse_bank = E.repeat(
        count=37,
        offset=(0.0, BARCODE_BOTTOM - BARCODE_TOP - 1.78, 0.0),
        key="coarse equalizer repeat",
    )(coarse_bar)

    pillar_top = E.translate(
        delta=(0.0, -0.0159, 0.0),
        key="pillar top phase center",
    )(
        E.repeat(
            count=2,
            offset=(0.0, 0.0318, 0.0),
            key="pillar top fill phases",
        )(
            _solid(
                G.rect(
                    width=PILLAR_WIDTH,
                    height=47.7182,
                    center=(PILLAR_CENTER_X, 72.125, 0.0),
                    key="pillar top",
                ),
                key="pillar top fill",
            )
        )
    )
    pillar_band_seed = _solid(
        G.rect(
            width=PILLAR_WIDTH,
            height=1.4,
            center=(PILLAR_CENTER_X, 96.684, 0.0),
            key="pillar gradient band seed",
        ),
        key="pillar gradient band seed fill",
    )
    pillar_bands = E.repeat(
        count=54,
        offset=(0.0, 80.743, 0.0),
        scale=(1.0, 0.89, 1.0),
        cumulative_offset=True,
        cumulative_scale=True,
        curve=1.03,
        auto_center=False,
        pivot=(PILLAR_CENTER_X, 96.684, 0.0),
        key="pillar gradient bands",
    )(pillar_band_seed)

    program_plus = _solid(
        G.rect(
            width=2.4,
            height=0.55,
            center=(44.46, 142.07, 0.0),
            key="program plus horizontal",
        )
        + G.rect(
            width=0.56,
            height=2.75,
            center=(44.46, 142.07, 0.0),
            key="program plus vertical",
        ),
        key="program plus fill",
    )
    footer_cross_motif = _solid(
        G.rect(
            width=3.1,
            height=0.55,
            center=(133.49, 197.9, 0.0),
            key="footer cross horizontal",
        )
        + G.rect(
            width=0.56,
            height=3.8,
            center=(133.49, 197.9, 0.0),
            key="footer cross vertical",
        ),
        key="footer cross fill",
    )
    footer_crosses = E.repeat(
        count=1,
        offset=(4.61, 0.0, 0.0),
        key="footer cross repeat",
    )(footer_cross_motif)

    date_frame_horizontal = E.repeat(
        count=1,
        offset=(0.0, 16.947, 0.0),
        key="date frame horizontal repeat",
    )(
        _solid(
            G.rect(
                width=35.109,
                height=0.12,
                center=(103.405, 6.624, 0.0),
                key="date frame top",
            ),
            key="date frame horizontal fill",
        )
    )
    date_frame_vertical = E.repeat(
        count=1,
        offset=(35.049, 0.0, 0.0),
        key="date frame vertical repeat",
    )(
        _solid(
            G.rect(
                width=0.06,
                height=17.067,
                center=(85.88, 15.0975, 0.0),
                key="date frame left",
            ),
            key="date frame vertical fill",
        )
    )
    date_frame = date_frame_horizontal + date_frame_vertical
    date_divider = _solid(
        G.rect(
            width=0.12,
            height=12.47,
            center=(102.0, 15.1, 0.0),
            key="date divider",
        ),
        key="date divider fill",
    )
    fine_line = _solid(
        G.rect(
            width=FINE_BAR_WIDTH,
            height=0.02,
            center=(FINE_BAR_CENTER_X, BARCODE_TOP + 0.01, 0.0),
            key="fine equalizer unit",
        ),
        key="fine equalizer fill",
    )
    fine_bank = E.repeat(
        count=64,
        offset=(0.0, BARCODE_BOTTOM - BARCODE_TOP - 0.02, 0.0),
        key="fine equalizer repeat",
    )(fine_line)
    program_frame_horizontal = E.repeat(
        count=1,
        offset=(0.0, 37.571, 0.0),
        key="program frame horizontal repeat",
    )(
        _solid(
            G.rect(
                width=31.411,
                height=0.12,
                center=(32.5555, 137.484, 0.0),
                key="program frame top",
            ),
            key="program frame horizontal fill",
        )
    )
    program_frame_vertical = E.repeat(
        count=1,
        offset=(31.291, 0.0, 0.0),
        key="program frame vertical repeat",
    )(
        _solid(
            G.rect(
                width=0.12,
                height=37.691,
                center=(16.91, 156.2695, 0.0),
                key="program frame left",
            ),
            key="program frame vertical fill",
        )
    )
    program_frame = program_frame_horizontal + program_frame_vertical
    program_rule = _solid(
        G.rect(
            width=27.0,
            height=0.1,
            center=(32.5, 161.6, 0.0),
            key="program rule",
        ),
        key="program rule fill",
    )
    right_rule = G.line(
        center=(140.55, 105.15, 0.0),
        length=6.5,
        angle=90.0,
        key="right detached rule",
    )
    footer_rail = G.line(
        center=(73.9, FOOTER_RAIL_Y, 0.0),
        length=135.3,
        angle=0.0,
        key="footer rail",
    )
    footer_divider = G.line(
        center=(46.33, 198.2, 0.0),
        length=14.4,
        angle=90.0,
        key="footer divider unit",
    )
    footer_dividers = E.repeat(
        count=1,
        offset=(34.75, 0.0, 0.0),
        key="footer divider repeat",
    )(footer_divider)

    red_plus = _solid(
        G.rect(
            width=1.8,
            height=0.5,
            center=(25.91, 107.8, 0.0),
            key="red plus horizontal",
        )
        + G.rect(
            width=0.5,
            height=2.0,
            center=(25.91, 107.8, 0.0),
            key="red plus vertical",
        ),
        key="red plus fill",
    )
    red_dot = _solid(
        G.circle(
            radius=0.43,
            segments=16,
            center=(DOT_AXIS_X, 18.21, 0.0),
            key="red dot",
        ),
        key="red dot fill",
    )
    chevron_left_pixel = _solid(
        G.rect(
            width=1.45,
            height=1.65,
            center=(39.1, 166.9, 0.0),
            key="chevron left pixel",
        ),
        key="chevron left pixel fill",
    )
    chevron_left = E.repeat(
        count=2,
        offset=(2.8, 3.2, 0.0),
        key="chevron left repeat",
    )(chevron_left_pixel)
    chevron_right_pixel = _solid(
        G.rect(
            width=1.45,
            height=1.65,
            center=(43.3, 168.5, 0.0),
            key="chevron right pixel",
        ),
        key="chevron right pixel fill",
    )
    chevron_right = E.repeat(
        count=1,
        offset=(1.4, -1.6, 0.0),
        key="chevron right repeat",
    )(chevron_right_pixel)

    red_rule_title = _solid(
        G.rect(
            width=6.2,
            height=0.48,
            center=(27.72, 73.52, 0.0),
            key="red title rule",
        ),
        key="red title rule fill",
    )
    red_rule_date = _solid(
        G.rect(
            width=2.95,
            height=0.2,
            center=(94.74, 19.77, 0.0),
            key="red date rule",
        ),
        key="red date rule fill",
    )
    red_rule_venue = _solid(
        G.rect(
            width=1.65,
            height=0.2,
            center=(106.3, 19.77, 0.0),
            key="red venue rule",
        ),
        key="red venue rule fill",
    )
    red_rule_left = _solid(
        G.rect(
            width=0.18,
            height=6.15,
            center=(7.15, 103.6, 0.0),
            key="red left rule",
        ),
        key="red left rule fill",
    )
    red_rule_ticket = _solid(
        G.rect(
            width=1.65,
            height=0.2,
            center=(9.18, 201.22, 0.0),
            key="red ticket rule",
        ),
        key="red ticket rule fill",
    )
    red_rule_info = _solid(
        G.rect(
            width=1.4,
            height=0.2,
            center=(54.85, 201.22, 0.0),
            key="red info rule",
        ),
        key="red info rule fill",
    )
    red_rule_visual = _solid(
        G.rect(
            width=1.55,
            height=0.2,
            center=(89.32, 201.38, 0.0),
            key="red visual rule",
        ),
        key="red visual rule fill",
    )

    # Width and anchors follow the reference, while per-group height factors
    # restore natural glyph proportions. Multiline leading is counter-scaled
    # so only the letterforms become shorter, not the baseline rhythm.
    adagio_center = (23.939, 14.905, 0.0)
    adagio = _scale_text_x(
        G.text(
            text="Adagio",
            font=FONT_SANS,
            font_index=FONT_BOLD_INDEX,
            quality=0.72,
            center=adagio_center,
            scale=18.9511,
        ),
        center=adagio_center,
        factor=0.8045,
        height_factor=0.9300,
        key="Adagio width fit",
    )
    ampersand_center = (23.724, 29.432, 0.0)
    ampersand = _scale_text_x(
        G.text(
            text="&",
            font=FONT_SANS,
            font_index=FONT_BOLD_INDEX,
            quality=0.72,
            center=ampersand_center,
            scale=19.1964,
        ),
        center=ampersand_center,
        factor=0.9071,
        height_factor=0.9300,
        key="ampersand width fit",
    )
    allegro_center = (23.94, 45.515, 0.0)
    allegro = _scale_text_x(
        G.text(
            text="Allegro",
            font=FONT_SANS,
            font_index=FONT_BOLD_INDEX,
            quality=0.72,
            center=allegro_center,
            scale=18.5901,
        ),
        center=allegro_center,
        factor=0.8252,
        height_factor=0.9300,
        key="Allegro width fit",
    )

    # Upper and central black copy.
    series_center = (25.087, 5.711, 0.0)
    series = _scale_text_x(
        G.text(
            text="SONIC PATTERNS\nLIVE SERIES",
            font=FONT_SANS,
            font_index=FONT_LIGHT_INDEX,
            letter_spacing_em=0.065,
            line_height=1.3023,
            quality=0.5,
            center=series_center,
            scale=2.2016,
        ),
        center=series_center,
        factor=0.7789,
        height_factor=0.8600,
        key="series width fit",
    )
    venue_center = (105.586, 8.98, 0.0)
    venue = _scale_text_x(
        G.text(
            text="DAEGU\nCONCERT\nHOUSE",
            font=FONT_SANS,
            font_index=FONT_LIGHT_INDEX,
            letter_spacing_em=0.055,
            line_height=1.4186,
            quality=0.5,
            center=venue_center,
            scale=2.268,
        ),
        center=venue_center,
        factor=0.77,
        height_factor=0.8600,
        key="venue width fit",
    )
    metro_center = (24.738, 79.904, 0.0)
    metro = _scale_text_x(
        G.text(
            text="metropolitan\norchestra",
            font=FONT_SANS,
            font_index=FONT_REGULAR_INDEX,
            line_height=1.1444,
            quality=0.6,
            center=metro_center,
            scale=4.1132,
        ),
        center=metro_center,
        factor=0.8195,
        height_factor=0.9000,
        key="metropolitan width fit",
    )
    classical_center = (25.043, 110.879, 0.0)
    classical = _scale_text_x(
        G.text(
            text="classical\nmusic\nin concert",
            font=FONT_SANS,
            font_index=FONT_REGULAR_INDEX,
            line_height=1.1333,
            quality=0.56,
            center=classical_center,
            scale=3.5602,
        ),
        center=classical_center,
        factor=0.8251,
        height_factor=0.9000,
        key="classical width fit",
    )

    # Left margin copy, fitted before rotation so letter proportions remain
    # deterministic while the final vertical bounds match the reference.
    live_music_center = (6.114, 35.115, 0.0)
    live_music = _scale_text_x(
        G.text(
            text="LIVE  MUSIC",
            font=FONT_SANS,
            font_index=FONT_LIGHT_INDEX,
            letter_spacing_em=0.13,
            quality=0.46,
            center=live_music_center,
            scale=1.5884,
        ),
        center=live_music_center,
        factor=1.3357,
        height_factor=0.9000,
        key="live music length fit",
    )
    live_music = E.rotate(
        auto_center=False,
        pivot=live_music_center,
        rotation=(0.0, 0.0, -90.0),
    )(live_music)
    concert_center = (6.085, 91.019, 0.0)
    concert = _scale_text_x(
        G.text(
            text="CONCERT",
            font=FONT_SANS,
            font_index=FONT_LIGHT_INDEX,
            letter_spacing_em=0.15,
            quality=0.46,
            center=concert_center,
            scale=1.6284,
        ),
        center=concert_center,
        factor=1.2647,
        height_factor=0.9000,
        key="concert length fit",
    )
    concert = E.rotate(
        auto_center=False,
        pivot=concert_center,
        rotation=(0.0, 0.0, -90.0),
    )(concert)
    spring_center = (6.049, 130.0, 0.0)
    spring = _scale_text_x(
        G.text(
            text="SPRING  2018",
            font=FONT_SANS,
            font_index=FONT_LIGHT_INDEX,
            letter_spacing_em=0.15,
            quality=0.46,
            center=spring_center,
            scale=1.7332,
        ),
        center=spring_center,
        factor=1.2466,
        height_factor=0.9000,
        key="spring length fit",
    )
    spring = E.rotate(
        auto_center=False,
        pivot=spring_center,
        rotation=(0.0, 0.0, -90.0),
    )(spring)

    # Program card.
    program_center = (19.0562, 141.7324, 0.0)
    program = _scale_text_x(
        G.text(
            text="PROGRAM",
            font=FONT_SANS,
            font_index=FONT_ULTRALIGHT_INDEX,
            letter_spacing_em=0.08,
            quality=0.45,
            center=program_center,
            scale=1.6637,
        ),
        center=program_center,
        factor=0.8751,
        height_factor=0.8800,
        key="program label width fit",
    )
    program_no_center = (18.926, 146.836, 0.0)
    program_no = _scale_text_x(
        G.text(
            text="08",
            font=FONT_SANS,
            font_index=FONT_BOLD_INDEX,
            quality=0.66,
            center=program_no_center,
            scale=8.8012,
        ),
        center=program_no_center,
        factor=0.8234,
        height_factor=0.9600,
        key="program number width fit",
    )
    program_time_center = (19.2411, 164.4972, 0.0)
    program_time = _scale_text_x(
        G.text(
            text="20:00\nTUE",
            font=FONT_SANS,
            font_index=FONT_THIN_INDEX,
            line_height=1.2727,
            quality=0.5,
            center=program_time_center,
            scale=2.4565,
        ),
        center=program_time_center,
        factor=0.8296,
        height_factor=0.8800,
        key="program time width fit",
    )

    # Right margin copy reads downward.
    dynamics_center = (141.711, 72.533, 0.0)
    dynamics = _scale_text_x(
        G.text(
            text="DYNAMICS IN TIME",
            font=FONT_SANS,
            font_index=FONT_LIGHT_INDEX,
            letter_spacing_em=0.12,
            quality=0.45,
            center=dynamics_center,
            scale=1.71,
        ),
        center=dynamics_center,
        factor=1.3212,
        height_factor=0.9000,
        key="dynamics length fit",
    )
    dynamics = E.rotate(
        auto_center=False,
        pivot=dynamics_center,
        rotation=(0.0, 0.0, 90.0),
    )(dynamics)
    volume_center = (141.655, 114.283, 0.0)
    volume = _scale_text_x(
        G.text(
            text="VOLUME / FREQUENCY",
            font=FONT_SANS,
            font_index=FONT_LIGHT_INDEX,
            letter_spacing_em=0.1,
            quality=0.45,
            center=volume_center,
            scale=1.7982,
        ),
        center=volume_center,
        factor=1.235,
        height_factor=0.9000,
        key="volume length fit",
    )
    volume = E.rotate(
        auto_center=False,
        pivot=volume_center,
        rotation=(0.0, 0.0, 90.0),
    )(volume)

    # Footer copy.
    tickets_center = (8.5774, 193.8399, 0.0)
    tickets = _scale_text_x(
        G.text(
            text="TICKETS\nINTERPARK.KR",
            font=FONT_SANS,
            font_index=FONT_ULTRALIGHT_INDEX,
            letter_spacing_em=0.14,
            line_height=1.3793,
            quality=0.45,
            center=tickets_center,
            scale=1.9233,
        ),
        center=tickets_center,
        factor=0.8576,
        height_factor=0.8700,
        key="tickets width fit",
    )
    info_center = (54.2278, 193.8312, 0.0)
    info = _scale_text_x(
        G.text(
            text="INFO\n070 1234 5678",
            font=FONT_SANS,
            font_index=FONT_ULTRALIGHT_INDEX,
            letter_spacing_em=0.14,
            line_height=1.3793,
            quality=0.45,
            center=info_center,
            scale=1.9341,
        ),
        center=info_center,
        factor=0.8148,
        height_factor=0.8700,
        key="info width fit",
    )
    visual_center = (88.7225, 193.8321, 0.0)
    visual = _scale_text_x(
        G.text(
            text="VISUAL SYSTEM\nOCTAVE MODULUS",
            font=FONT_SANS,
            font_index=FONT_ULTRALIGHT_INDEX,
            letter_spacing_em=0.11,
            line_height=1.3793,
            quality=0.45,
            center=visual_center,
            scale=1.9292,
        ),
        center=visual_center,
        factor=0.8592,
        height_factor=0.8700,
        key="visual credit width fit",
    )

    # Vermillion copy.
    date_center = (87.95, 8.16, 0.0)
    date = _scale_text_x(
        G.text(
            text="29\n05\n18",
            font=FONT_SANS,
            font_index=FONT_BOLD_INDEX,
            line_height=1.0698,
            quality=0.55,
            center=date_center,
            scale=4.4099,
        ),
        center=date_center,
        factor=0.733,
        height_factor=0.898,
        key="date width fit",
    )
    page_no_center = (138.533, 5.338, 0.0)
    page_no = _scale_text_x(
        G.text(
            text="01",
            font=FONT_SANS,
            font_index=FONT_BOLD_INDEX,
            quality=0.55,
            center=page_no_center,
            scale=4.0621,
        ),
        center=page_no_center,
        factor=0.8444,
        height_factor=1.0100,
        key="page number width fit",
    )
    works_center = (33.375, 148.333, 0.0)
    works = _scale_text_x(
        G.text(
            text="Works\nBy\n06\nComposers",
            font=FONT_SANS,
            font_index=FONT_MEDIUM_INDEX,
            line_height=1.1364,
            quality=0.5,
            center=works_center,
            scale=2.4367,
        ),
        center=works_center,
        factor=0.7831,
        height_factor=0.8800,
        key="works width fit",
    )
    roman_center = (141.934, 171.459, 0.0)
    roman = _scale_text_x(
        G.text(
            text="MMXVIII",
            font=FONT_SANS,
            font_index=FONT_LIGHT_INDEX,
            letter_spacing_em=0.1,
            quality=0.45,
            center=roman_center,
            scale=1.9952,
        ),
        center=roman_center,
        factor=1.2152,
        height_factor=0.9000,
        key="roman numeral length fit",
    )
    roman = E.rotate(
        auto_center=False,
        pivot=roman_center,
        rotation=(0.0, 0.0, 90.0),
    )(roman)

    headline_geometries = (
        _filled(adagio),
        _filled(ampersand),
        _filled(allegro),
        _filled(metro),
        _filled(classical),
        _filled(program_no),
    )
    black_copy = (
        _filled(series),
        _filled(venue),
        _filled(live_music),
        _filled(concert),
        _filled(spring),
        _filled(program),
        _filled(program_time),
        _filled(dynamics),
        _filled(volume),
        _filled(tickets),
        _filled(info),
        _filled(visual),
    )
    red_copy = (
        _filled(date),
        _filled(page_no),
        _filled(works),
        _filled(roman),
    )

    black_geometries = (
        layout_grid,
        upper_crosses,
        black_dots,
        free_bars,
        coarse_bank,
        pillar_top,
        pillar_bands,
        program_plus,
        footer_crosses,
        date_frame,
        date_divider,
        fine_bank,
        program_frame,
        program_rule,
        right_rule,
        footer_rail,
        footer_dividers,
        *headline_geometries,
        *black_copy,
    )
    red_geometries = (
        red_rule_title,
        red_rule_date,
        red_rule_venue,
        red_rule_left,
        red_rule_ticket,
        red_rule_info,
        red_rule_visual,
        red_plus,
        red_dot,
        chevron_left,
        chevron_right,
        *red_copy,
    )

    return (
        L("black").layer(
            black_geometries,
            color=BLACK,
            thickness=0.001,
        ),
        L("red").layer(
            red_geometries,
            color=RED,
            thickness=0.001,
        ),
    )


if __name__ == "__main__":
    from grafix import run

    run(
        draw,
        canvas_size=CANVAS,
        render_scale=3.0,
        background_color=PAPER,
        line_color=BLACK,
        n_worker=0,
        fps=20.0,
    )
y
