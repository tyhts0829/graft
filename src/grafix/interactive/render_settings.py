# どこで: `src/grafix/interactive/render_settings.py`。
# 何を: interactive 描画設定の束を表すデータクラスを定義する。
# なぜ: `run` の引数を簡潔に保ちつつ、interactive 側の設定を一元管理するため。

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RenderSettings:
    """リアルタイム描画に用いる設定値の集合。"""

    background_color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    line_thickness: float = 0.01
    line_color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    render_scale: float = 1.0
    canvas_size: tuple[int, int] = (800, 800)
