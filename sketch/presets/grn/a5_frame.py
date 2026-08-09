"""
どこで: `sketch/presets/grn/a5_frame.py`。
何を: A5 向けのテンプレート枠（layout + template）を生成する preset。
なぜ: サンプル集の “テンプレフォーマット枠” を 1 つの呼び出しにまとめるため。
"""

from __future__ import annotations

from collections.abc import Mapping

from grafix import E, G, L, P, preset

CANVAS_SIZE = (148, 210)  # A5 (mm)


def _rgb255_to_rgb01(rgb255: tuple[int, int, int]) -> tuple[float, float, float]:
    r, g, b = rgb255
    return float(r) / 255.0, float(g) / 255.0, float(b) / 255.0


meta: dict[str, Mapping[str, object]] = {
    "show_layout": {
        "kind": "bool",
        "description": "A5 テンプレートのカラム・行グリッドを表示する。",
    },
    "layout_color_rgb255": {
        "kind": "rgb",
        "ui_min": 0,
        "ui_max": 255,
        "description": "レイアウトグリッドの線色を RGB で指定する。",
    },
    "number_text": {
        "kind": "str",
        "description": "ヘッダーへ配置する作品番号の文字列を指定する。",
    },
    "explanation_text": {
        "kind": "str",
        "description": "ヘッダー右側へ配置するコード説明文を指定する。",
    },
    "explanation_density": {
        "kind": "float",
        "ui_min": 0.0,
        "ui_max": 1000.0,
        "description": "コード説明文を塗るハッチング線の密度を指定する。",
    },
    "template_color_rgb255": {
        "kind": "rgb",
        "ui_min": 0,
        "ui_max": 255,
        "description": "ヘッダー内の罫線・文字・バーの線色を RGB で指定する。",
    },
}

GRN_A5_FRAME_UI_VISIBLE = {
    "layout_color_rgb255": lambda v: bool(v.get("show_layout")),
}


@preset(meta=meta, ui_visible=GRN_A5_FRAME_UI_VISIBLE)
def grn_a5_frame(
    *,
    show_layout: bool = True,
    layout_color_rgb255: tuple[int, int, int] = (191, 191, 191),
    number_text: str = "1",
    explanation_text: str = "G.polygon()\nE.repeat().displace()",
    explanation_density: float = 450.0,
    template_color_rgb255: tuple[int, int, int] = (0, 0, 0),
):
    layout_geom = P.layout_grid_system(
        activate=bool(show_layout),
        canvas_w=float(CANVAS_SIZE[0]),
        canvas_h=float(CANVAS_SIZE[1]),
        axes="both",
        margin_l=12.0,
        margin_r=12.0,
        margin_t=12.0,
        margin_b=12.0,
        show_center=False,
        cols=5,
        rows=8,
        gutter_x=4.0,
        gutter_y=4.0,
        show_column_centers=False,
        show_baseline=False,
        baseline_step=3.959,
        baseline_offset=0.0,
        offset=(0.0, 0.0, 0.0),
    )

    layout = L(name="layout").layer(
        layout_geom,
        color=_rgb255_to_rgb01(layout_color_rgb255),
    )

    line = G.line(
        activate=True,
        center=(11.5, 174.5, 0.0),
        anchor="left",
        length=124.5,
        angle=0.0,
    )

    series_name = G.text(
        activate=True,
        text="Grafix\nDesign\nStudies",
        font="Helvetica.ttc",
        font_index=0,
        text_align="left",
        letter_spacing_em=0.0,
        line_height=1.1,
        use_bounding_box=False,
        quality=0.5,
        center=(11.538, 178.022, 0.0),
        scale=6.5,
    )
    series_name = E.fill(
        activate=True,
        angle_sets=1,
        angle=45.0,
        density=838.488,
        spacing_gradient=0.0,
        remove_boundary=False,
        min_spacing=0.05,
    )(series_name)

    number = G.text(
        activate=True,
        text=str(number_text),
        font="Helvetica.ttc",
        font_index=0,
        text_align="left",
        letter_spacing_em=0.0,
        line_height=1.2,
        use_bounding_box=False,
        quality=0.5,
        center=(63.0, 178.022, 0.0),
        scale=4.553,
    )
    number = E.fill(
        activate=True,
        angle_sets=1,
        angle=45.0,
        density=100.0,
        spacing_gradient=0.0,
        remove_boundary=False,
    )(number)

    code = G.text(
        activate=True,
        text=str(explanation_text),
        font="HackGen35-Regular.ttf",
        font_index=0,
        text_align="right",
        letter_spacing_em=0.0,
        line_height=1.2,
        use_bounding_box=True,
        box_width=46.907000000000004,
        box_height=20.103,
        show_bounding_box=False,
        quality=0.5,
        center=(136.0, 178.022, 0.0),
        scale=2.9210000000000003,
    )
    code = E.fill(
        activate=True,
        angle_sets=1,
        angle=45.0,
        density=explanation_density,
        spacing_gradient=0.0,
        remove_boundary=False,
    )(code)

    bar = G.polygon(
        activate=True,
        n_sides=4,
        phase=45.0,
        sweep=360.0,
        center=(126.923, 197.5, 0.0),
        scale=5.155,
    )
    bar = (
        E.scale(
            activate=True,
            mode="all",
            auto_center=True,
            pivot=(0.0, 0.0, 0.0),
            scale=(5.824, 0.22, 1.0),
        ).fill(
            activate=True,
            angle_sets=1,
            angle=45.0,
            density=80,
            spacing_gradient=0.0,
            remove_boundary=False,
        )
    )(bar)

    template = L(name="template").layer(
        [
            line,
            series_name,
            number,
            code,
            bar,
        ],
        color=_rgb255_to_rgb01(template_color_rgb255),
    )
    return layout, template
