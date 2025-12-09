# どこで: `src/render/render_settings.py`。
# 何を: ランタイム描画設定の束を表すデータクラスを定義する。
# なぜ: `run` の引数を簡潔に保ちつつ、描画関連の設定を一元管理するため。

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RenderSettings:
    """リアルタイム描画に用いる設定値の集合。"""

    background_color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    line_thickness: float = 0.01
    line_color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    render_scale: float = 1.0
    canvas_size: tuple[int, int] = (800, 800)
