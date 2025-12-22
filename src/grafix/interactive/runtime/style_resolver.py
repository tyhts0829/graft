# どこで: `src/grafix/interactive/runtime/style_resolver.py`。
# 何を: ParamStore の style エントリから、そのフレームの背景色/線色/線幅を確定する。
# なぜ: DrawWindowSystem の冒頭ロジックを分離し、仕様の置き場所を明確にするため。

from __future__ import annotations

from dataclasses import dataclass

from grafix.core.parameters import ParamStore
from grafix.core.parameters.style_ops import ensure_style_entries
from grafix.core.parameters.style import (
    coerce_rgb255,
    rgb01_to_rgb255,
    rgb255_to_rgb01,
    style_key,
)


@dataclass(frozen=True, slots=True)
class FrameStyle:
    """1 フレーム分の style 解決結果。"""

    bg_color_rgb01: tuple[float, float, float]
    global_line_color_rgb01: tuple[float, float, float]
    global_thickness: float


class StyleResolver:
    """ParamStore の style キーから、そのフレームの style を解決する。"""

    def __init__(
        self,
        store: ParamStore,
        *,
        base_background_color_rgb01: tuple[float, float, float],
        base_global_thickness: float,
        base_global_line_color_rgb01: tuple[float, float, float],
    ) -> None:
        ensure_style_entries(
            store,
            background_color_rgb01=base_background_color_rgb01,
            global_thickness=float(base_global_thickness),
            global_line_color_rgb01=base_global_line_color_rgb01,
        )

        self._store = store
        self._key_background = style_key("background_color")
        self._key_thickness = style_key("global_thickness")
        self._key_line_color = style_key("global_line_color")

        self._base_background_rgb255 = rgb01_to_rgb255(base_background_color_rgb01)
        self._base_thickness = float(base_global_thickness)
        self._base_line_color_rgb255 = rgb01_to_rgb255(base_global_line_color_rgb01)

    def resolve(self) -> FrameStyle:
        """そのフレームで使う style を返す。"""

        bg_state = self._store.get_state(self._key_background)
        bg255 = (
            self._base_background_rgb255
            if bg_state is None or not bg_state.override
            else coerce_rgb255(bg_state.ui_value)
        )

        line_state = self._store.get_state(self._key_line_color)
        line255 = (
            self._base_line_color_rgb255
            if line_state is None or not line_state.override
            else coerce_rgb255(line_state.ui_value)
        )

        thickness_state = self._store.get_state(self._key_thickness)
        thickness = (
            float(self._base_thickness)
            if thickness_state is None or not thickness_state.override
            else float(thickness_state.ui_value)
        )

        return FrameStyle(
            bg_color_rgb01=rgb255_to_rgb01(bg255),
            global_line_color_rgb01=rgb255_to_rgb01(line255),
            global_thickness=float(thickness),
        )
