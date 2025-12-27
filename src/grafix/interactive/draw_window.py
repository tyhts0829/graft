# どこで: `src/grafix/interactive/draw_window.py`。
# 何を: ライブ描画用の pyglet ウィンドウ生成を行う。
# なぜ: interactive 依存をこの層に閉じ込め、core/export をヘッドレスに保つため。

from __future__ import annotations

import pyglet
from pyglet.gl import Config
from pyglet.window import Window

from grafix.interactive.render_settings import RenderSettings


def create_draw_window(settings: RenderSettings) -> Window:
    """設定に基づき描画ウィンドウを生成する。"""
    # 線描画を滑らかにするために MSAA を有効化
    config = Config(double_buffer=True, sample_buffers=1, samples=4)  # type: ignore[abstract]
    canvas_w, canvas_h = settings.canvas_size
    window = pyglet.window.Window(  # type: ignore[abstract]
        width=int(canvas_w * settings.render_scale),
        height=int(canvas_h * settings.render_scale),
        resizable=False,
        caption="Grafix",
        config=config,
    )
    return window
