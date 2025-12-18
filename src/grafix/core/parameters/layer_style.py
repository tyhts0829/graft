"""
どこで: `src/grafix/core/parameters/layer_style.py`。
何を: Layer ごとの line_thickness/line_color を ParamStore で表現するキーと観測レコード生成ヘルパを定義する。
なぜ: Layer style も primitive/effect と同じく「観測→フレーム終端でマージ」の流れに統合するため。
"""

from __future__ import annotations

from .frame_params import FrameParamRecord
from .key import ParameterKey
from .meta import ParamMeta
from .style import rgb01_to_rgb255

LAYER_STYLE_OP = "__layer_style__"

LAYER_STYLE_LINE_THICKNESS = "line_thickness"
LAYER_STYLE_LINE_COLOR = "line_color"

LAYER_STYLE_THICKNESS_META = ParamMeta(kind="float", ui_min=1e-6, ui_max=0.01)
LAYER_STYLE_COLOR_META = ParamMeta(kind="rgb", ui_min=0, ui_max=255)


def layer_style_key(layer_site_id: str, arg: str) -> ParameterKey:
    """Layer style 用の ParameterKey を返す。"""

    return ParameterKey(op=LAYER_STYLE_OP, site_id=str(layer_site_id), arg=str(arg))


def layer_style_records(
    *,
    layer_site_id: str,
    base_line_thickness: float,
    base_line_color_rgb01: tuple[float, float, float],
    explicit_line_thickness: bool,
    explicit_line_color: bool,
) -> list[FrameParamRecord]:
    """Layer style の観測レコード（line_thickness/line_color）を返す。"""

    thickness_key = layer_style_key(layer_site_id, LAYER_STYLE_LINE_THICKNESS)
    color_key = layer_style_key(layer_site_id, LAYER_STYLE_LINE_COLOR)

    return [
        FrameParamRecord(
            key=thickness_key,
            base=float(base_line_thickness),
            meta=LAYER_STYLE_THICKNESS_META,
            explicit=bool(explicit_line_thickness),
        ),
        FrameParamRecord(
            key=color_key,
            base=rgb01_to_rgb255(base_line_color_rgb01),
            meta=LAYER_STYLE_COLOR_META,
            explicit=bool(explicit_line_color),
        ),
    ]
