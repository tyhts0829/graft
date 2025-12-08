"""
どこで: `src/api/run.py`。公開 API のランナー実装。
何を: pyglet + ModernGL を使い、`draw(t)` が返す Geometry をウィンドウに描画するランナーを提供する。
なぜ: `main.py` を実行して実際に線をプレビューできる経路を用意するため。
"""

from __future__ import annotations

import time
from typing import Callable

import pyglet

from src.core.geometry import Geometry
from src.core.realize import realize
from src.app.draw_window import create_draw_window, schedule_tick, unschedule_tick
from src.render.draw_renderer import DrawRenderer
from src.render.index_buffer import build_line_indices
from src.render.render_settings import RenderSettings


def run(
    draw: Callable[[float], Geometry],
    *,
    background_color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
    line_thickness: float = 0.01,
    line_color: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0),
    render_scale: float = 1.0,
    canvas_size: tuple[int, int] = (800, 800),
) -> None:
    """`draw(t)` で生成される Geometry を pyglet ウィンドウへ描画する。

    Parameters
    ----------
    draw : Callable[[float], Geometry]
        フレーム時刻 t を受け取り Geometry を返すコールバック。
    background_color : tuple[float, float, float, float]
        背景色 RGBA。既定は白。
    line_thickness : float
        プレビュー用の線幅（クリップ空間での最終幅）。Layer.thickness 未指定時の基準値。
    line_color : tuple[float, float, float, float]
        線色 RGBA。既定は黒。
    render_scale : float
        キャンバス寸法に掛けるピクセル倍率。高精細プレビュー用。
    canvas_size : tuple[int, int]
        キャンバス寸法（任意単位）。投影行列生成とウィンドウサイズ決定に使用。
    """

    settings = RenderSettings(
        background_color=background_color,
        line_thickness=line_thickness,
        line_color=line_color,
        render_scale=render_scale,
        canvas_size=canvas_size,
    )

    window = create_draw_window(settings)
    renderer = DrawRenderer(window, settings)

    start_time = time.perf_counter()
    closed = False

    def render_frame() -> None:
        t = time.perf_counter() - start_time
        geometry = draw(t)
        realized = realize(geometry)

        indices = build_line_indices(realized.offsets)
        renderer.render(realized, indices, settings)

    def on_draw() -> None:
        renderer.viewport(window.width, window.height)
        renderer.clear(settings.background_color)
        render_frame()

    def on_resize(width: int, height: int) -> None:
        renderer.viewport(width, height)

    def on_close() -> None:
        nonlocal closed
        closed = True
        unschedule_tick(tick)
        pyglet.app.exit()

    def tick(dt: float) -> None:
        if closed or window.has_exit:
            return
        window.switch_to()
        try:
            on_draw()
        except Exception:
            pyglet.app.exit()
            raise
        window.flip()

    window.push_handlers(on_draw=on_draw, on_close=on_close, on_resize=on_resize)
    schedule_tick(tick, fps=60.0)
    try:
        pyglet.app.run()
    finally:
        renderer.release()
        window.close()
