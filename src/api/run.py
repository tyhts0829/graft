"""
どこで: `src/api/run.py`。公開 API のランナー実装。
何を: pyglet + ModernGL を使い、`draw(t)` が返す Geometry/Layer/シーンをウィンドウに描画するランナーを提供する。
なぜ: `main.py` を実行して実際に線をプレビューできる経路を用意するため。
"""

from __future__ import annotations

import time
from typing import Callable

import pyglet

from src.app.draw_window import create_draw_window, schedule_tick, unschedule_tick
from src.core.realize import realize
from src.render.draw_renderer import DrawRenderer
from src.render.index_buffer import build_line_indices
from src.render.layer import LayerStyleDefaults, resolve_layer_style
from src.render.render_settings import RenderSettings
from src.render.scene import SceneItem, normalize_scene


def run(
    draw: Callable[[float], SceneItem],
    *,
    background_color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
    line_thickness: float = 0.01,
    line_color: tuple[float, float, float] = (0.0, 0.0, 0.0),
    render_scale: float = 1.0,
    canvas_size: tuple[int, int] = (800, 800),
) -> None:
    """pyglet ウィンドウを生成し `draw(t)` のシーンをリアルタイム描画する。

    Parameters
    ----------
    draw : Callable[[float], SceneItem]
        フレーム経過秒 t を受け取り Geometry / Layer / それらの列を返すコールバック。
    background_color : tuple[float, float, float, float]
        背景色 RGBA。既定は白。
    line_thickness : float
        プレビュー用線幅（ワールド単位）。Layer.thickness 未指定時の基準値。
    line_color : tuple[float, float, float]
        線色 RGB。既定は黒。
    render_scale : float
        キャンバス寸法に掛けるピクセル倍率。高精細プレビュー用。
    canvas_size : tuple[int, int]
        キャンバス寸法（任意単位）。投影行列生成とウィンドウサイズ決定に使用。

    Returns
    -------
    None
        pyglet イベントループ終了後に制御を返す。
    """

    settings = RenderSettings(
        background_color=background_color,
        line_thickness=line_thickness,
        line_color=line_color,
        render_scale=render_scale,
        canvas_size=canvas_size,
    )

    defaults = LayerStyleDefaults(color=line_color, thickness=line_thickness)
    window = create_draw_window(settings)
    renderer = DrawRenderer(window, settings)

    start_time = time.perf_counter()
    closed = False

    def render_frame() -> None:
        """現在時刻に応じたシーンを生成しレンダリングする。"""

        t = time.perf_counter() - start_time
        scene = draw(t)
        layers = normalize_scene(scene)

        for layer in layers:
            resolved = resolve_layer_style(layer, defaults)
            realized = realize(resolved.layer.geometry)
            indices = build_line_indices(realized.offsets)
            renderer.render(
                realized,
                indices,
                color=resolved.color,
                thickness=resolved.thickness,
            )

    def on_draw() -> None:
        """描画イベントごとにビューポートと背景を整えて描画する。"""

        renderer.viewport(window.width, window.height)
        renderer.clear(settings.background_color)
        render_frame()

    def on_resize(width: int, height: int) -> None:
        """ウィンドウサイズ変更のたびにビューポートを更新する。"""

        renderer.viewport(width, height)

    def on_close() -> None:
        """ウィンドウクローズ時にループを停止しリソースを解放する。"""

        nonlocal closed
        closed = True
        unschedule_tick(tick)
        pyglet.app.exit()

    def tick(dt: float) -> None:
        """pyglet スケジューラから呼ばれフレーム更新を駆動する。"""

        if closed or window.has_exit:
            return
        window.switch_to()
        try:
            on_draw()
        except Exception:
            pyglet.app.exit()
            raise
        window.flip()

    # pyglet イベントをローカルハンドラへ束ねる
    window.push_handlers(on_draw=on_draw, on_close=on_close, on_resize=on_resize)
    schedule_tick(tick, fps=60.0)
    try:
        pyglet.app.run()
    finally:
        # イベントループ終了時は明示的に GPU / Window 資源を解放する
        renderer.release()
        window.close()
