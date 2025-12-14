# どこで: `src/app/draw_window.py`。
# 何を: ライブ描画用の pyglet ウィンドウ生成とタイマー管理を行う。
# なぜ: ウィンドウ関連の責務を render から切り出し、run のオーケストレーションを簡潔にするため。

from __future__ import annotations

import pyglet
from pyglet.gl import Config
from pyglet.window import Window

from src.render.render_settings import RenderSettings


def create_draw_window(settings: RenderSettings) -> Window:
    """設定に基づき描画ウィンドウを生成する。"""
    # 線描画を滑らかにするために MSAA を有効化
    config = Config(double_buffer=True, sample_buffers=1, samples=4, vsync=True)
    canvas_w, canvas_h = settings.canvas_size
    window = pyglet.window.Window(
        width=int(canvas_w * settings.render_scale),
        height=int(canvas_h * settings.render_scale),
        resizable=False,
        caption="Graft",
        config=config,
    )
    return window


def schedule_tick(tick_fn, *, fps: float = 60.0) -> None:
    """一定周期で tick_fn を呼ぶタイマーを登録する。"""
    pyglet.clock.schedule_interval(tick_fn, 1.0 / fps)


def unschedule_tick(tick_fn) -> None:
    """tick_fn のタイマー登録を解除する。"""
    pyglet.clock.unschedule(tick_fn)
