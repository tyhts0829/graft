"""
どこで: `src/parameters/layer_style.py`。
何を: Layer ごとの line_thickness/line_color を ParamStore で表現するキーと初期化ヘルパを定義する。
なぜ: GUI と描画側で同じ識別子（layer_site_id）を共有し、Layer 単位の上書きを素直に実装するため。
"""

from __future__ import annotations

from src.parameters.key import ParameterKey
from src.parameters.meta import ParamMeta
from src.parameters.store import ParamStore
from src.parameters.style import rgb01_to_rgb255

LAYER_STYLE_OP = "__layer_style__"

LAYER_STYLE_LINE_THICKNESS = "line_thickness"
LAYER_STYLE_LINE_COLOR = "line_color"

LAYER_STYLE_THICKNESS_META = ParamMeta(kind="float", ui_min=1e-6, ui_max=0.01)
LAYER_STYLE_COLOR_META = ParamMeta(kind="rgb", ui_min=0, ui_max=255)


def layer_style_key(layer_site_id: str, arg: str) -> ParameterKey:
    """Layer style 用の ParameterKey を返す。"""

    return ParameterKey(op=LAYER_STYLE_OP, site_id=str(layer_site_id), arg=str(arg))


def ensure_layer_style_entries(
    store: ParamStore,
    *,
    layer_site_id: str,
    base_line_thickness: float,
    base_line_color_rgb01: tuple[float, float, float],
    initial_override_line_thickness: bool,
    initial_override_line_color: bool,
    label_name: str | None,
) -> None:
    """Layer style の行（line_thickness/line_color）を ParamStore に作成する。"""

    thickness_key = layer_style_key(layer_site_id, LAYER_STYLE_LINE_THICKNESS)
    color_key = layer_style_key(layer_site_id, LAYER_STYLE_LINE_COLOR)

    # meta が無いと snapshot に載らず、GUI 行としても見えない。
    if store.get_meta(thickness_key) is None:
        store.set_meta(thickness_key, LAYER_STYLE_THICKNESS_META)
    if store.get_meta(color_key) is None:
        store.set_meta(color_key, LAYER_STYLE_COLOR_META)

    store.ensure_state(
        thickness_key,
        base_value=float(base_line_thickness),
        initial_override=bool(initial_override_line_thickness),
    )
    store.ensure_state(
        color_key,
        base_value=rgb01_to_rgb255(base_line_color_rgb01),
        initial_override=bool(initial_override_line_color),
    )

    if label_name:
        store.set_label(LAYER_STYLE_OP, str(layer_site_id), str(label_name))

