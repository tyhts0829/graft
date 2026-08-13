"""
Purpose:
    interactive preview用のresizableなpyglet/GL windowを生成するleaf factoryを提供する。
Use when:
    previewの初期size、MSAA、minimum size、またはwindow生成失敗時cleanupを変更する場合。
Constraints:
    - canvasの論理sizeとpreview pixel sizeをrender scaleでのみ接続する。
    - 部分構築に失敗したwindowを同じlifecycle helperで閉じる。
Side effects:
    native windowとGL contextを作成する。
"""

from __future__ import annotations

import pyglet
from pyglet.gl import Config
from pyglet.window import Window

from grafix.core.render_options import RenderOptions
from grafix.interactive.pyglet_window_lifecycle import close_pyglet_window

MINIMUM_DRAW_WINDOW_WIDTH = 320
MINIMUM_DRAW_WINDOW_HEIGHT = 320


def create_draw_window(options: RenderOptions, *, render_scale: float) -> Window:
    """設定に基づき描画ウィンドウを生成する。"""
    # 線描画を滑らかにするために MSAA を有効化
    config = Config(double_buffer=True, sample_buffers=1, samples=4)  # type: ignore[abstract]
    canvas_w, canvas_h = options.canvas_size
    window_width = int(canvas_w * render_scale)
    window_height = int(canvas_h * render_scale)
    window = pyglet.window.Window(  # type: ignore[abstract]
        width=window_width,
        height=window_height,
        # viewport は DrawWindowSystem が毎 frame framebuffer size へ同期する。
        # 小さな画面や作業配置に合わせて preview を調整できるようにする。
        resizable=True,
        caption="Grafix",
        config=config,
    )
    try:
        window.set_minimum_size(
            min(MINIMUM_DRAW_WINDOW_WIDTH, window_width),
            min(MINIMUM_DRAW_WINDOW_HEIGHT, window_height),
        )
    except BaseException:
        try:
            close_pyglet_window(window)
        except BaseException:
            pass
        raise
    return window
