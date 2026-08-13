"""
Purpose:
    canvas、safe margin、trimの外周を他のlayout guideと合成できるpresetとして提供する。
Use when:
    紙面境界、余白、安全領域、trim位置を可視化するガイドが必要なとき。
Constraints:
    - canvas、safe、trimはcommon helperと同じ左上基準rect・offset座標を使う。
    - axes指定を各outlineとcenter lineへ一貫して適用する。
    - marginが非zeroならshow_margin=Falseでもsafe boundaryを失わない。
    - trim boundaryはshow_trimが明示された場合だけ追加する。
See:
    sketch/presets/layout/common.py
"""

from __future__ import annotations

from collections.abc import Mapping

from grafix import preset

from .common import (
    CANVAS_SIZE,
    META_COMMON,
    _center_lines,
    _finish,
    _has_margin,
    _inset_rect,
    _rect_from_canvas,
    _rect_outline,
)

meta: dict[str, Mapping[str, object]] = {
    **META_COMMON,
    "border": {
        "kind": "bool",
        "description": "キャンバス外周を示す境界線を描く。",
    },
    "show_margin": {
        "kind": "bool",
        "description": "各 margin を差し引いた安全領域の外周線を描く。",
    },
    "trim": {
        "kind": "float",
        "ui_min": 0.0,
        "ui_max": 100.0,
        "description": "キャンバス外周からトリム線までの均等な内側距離を指定する。",
    },
    "show_trim": {
        "kind": "bool",
        "description": "指定した trim 距離にトリム外周線を描く。",
    },
}

LAYOUT_BOUNDS_UI_VISIBLE = {
    "trim": lambda v: bool(v.get("show_trim")),
}

@preset(meta=meta, ui_visible=LAYOUT_BOUNDS_UI_VISIBLE)
def layout_bounds(
    *,
    canvas_w: float = float(CANVAS_SIZE[0]),
    canvas_h: float = float(CANVAS_SIZE[1]),
    axes: str = "both",
    margin_l: float = 0.0,
    margin_r: float = 0.0,
    margin_t: float = 0.0,
    margin_b: float = 0.0,
    show_center: bool = False,
    border: bool = True,
    show_margin: bool = False,
    trim: float = 0.0,
    show_trim: bool = False,
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0),
):
    """外枠（canvas / safe / trim）を描く。"""
    axes = str(axes)
    _ox, _oy, oz = offset
    z = float(oz)

    canvas_rect = _rect_from_canvas(canvas_w=canvas_w, canvas_h=canvas_h, offset=offset)
    safe_rect = _inset_rect(
        canvas_rect,
        left=margin_l,
        right=margin_r,
        top=margin_t,
        bottom=margin_b,
    )
    trim_rect = _inset_rect(canvas_rect, left=trim, right=trim, top=trim, bottom=trim)

    out: list[object] = []
    if bool(border):
        out.extend(_rect_outline(canvas_rect, axes=axes, z=z))
    if bool(show_margin) or _has_margin(margin_l=margin_l, margin_r=margin_r, margin_t=margin_t, margin_b=margin_b):
        out.extend(_rect_outline(safe_rect, axes=axes, z=z))
    if bool(show_trim):
        out.extend(_rect_outline(trim_rect, axes=axes, z=z))
    if bool(show_center):
        out.extend(_center_lines(safe_rect, axes=axes, z=z))

    return _finish(geoms=out, offset=offset)
