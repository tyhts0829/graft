"""
どこで: `src/api/run.py`。公開 API のランナー実装。
何を: pyglet + ModernGL を使い、`draw(t)` が返す Geometry/Layer/シーンをウィンドウに描画するランナーを提供する。
なぜ: `main.py` を実行して実際に線をプレビューできる経路を用意するため。
"""

from __future__ import annotations

import time
from typing import Callable

import pyglet

from src.app.draw_window import create_draw_window
from src.parameters import ParamStore, parameter_context
from src.render.draw_renderer import DrawRenderer
from src.render.frame_pipeline import render_scene
from src.render.layer import LayerStyleDefaults
from src.render.render_settings import RenderSettings
from src.render.scene import SceneItem


def run(
    draw: Callable[[float], SceneItem],
    *,
    background_color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
    line_thickness: float = 0.01,
    line_color: tuple[float, float, float] = (0.0, 0.0, 0.0),
    render_scale: float = 1.0,
    canvas_size: tuple[int, int] = (800, 800),
    parameter_gui: bool = True,
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
    parameter_gui : bool
        True の場合、別ウィンドウで Parameter GUI を起動し、ParamStore を編集できるようにする。

    Returns
    -------
    None
        どちらかのウィンドウを閉じると制御を返す。
    """

    # vsync を有効にし、モニタのリフレッシュに同期してフレーム更新する。
    # ちらつき/ティアリングを抑えられる一方で、環境によっては実効 FPS が抑えられる。
    pyglet.options["vsync"] = True

    # レンダリング設定をまとめて構築する（ウィンドウサイズ/背景色/描画スケールなど）。
    settings = RenderSettings(
        background_color=background_color,
        line_thickness=line_thickness,
        line_color=line_color,
        render_scale=render_scale,
        canvas_size=canvas_size,
    )

    # draw(t) が返す Layer 側で style 未指定のときに使う既定値。
    defaults = LayerStyleDefaults(color=line_color, thickness=line_thickness)

    # メインの描画用ウィンドウと、描画器（ModernGL/GL リソースを内部で保持）を用意する。
    window = create_draw_window(settings)
    renderer = DrawRenderer(window, settings)

    # パラメータは描画と GUI で共有する。
    # GUI で値を変更すると、次フレーム以降の parameter_context 参照に反映される。
    param_store = ParamStore()

    # Parameter GUI（別ウィンドウ）は任意。無効時は import 自体を避ける。
    param_gui = None
    gui_window = None

    if parameter_gui:
        # GUI は pyimgui を使うため依存が重い。必要なときだけ遅延 import する。
        from src.app.parameter_gui import ParameterGUI, create_parameter_gui_window

        gui_window = create_parameter_gui_window()
        param_gui = ParameterGUI(gui_window, store=param_store)

    # t の基準時刻。描画ごとに経過秒を計算して draw(t) に渡す。
    start_time = time.perf_counter()

    # どちらかのウィンドウが閉じられたらループを止める。
    running = True

    def stop_loop(*_: object) -> None:
        # pyglet の on_close ハンドラは引数が来る場合があるため *args で受ける。
        nonlocal running
        running = False

    def render_frame() -> None:
        """現在時刻に応じたシーンを生成しレンダリングする。"""

        # このフレームの時刻 t を算出し、ParamStore を参照するコンテキストで描画する。
        # parameter_context は「このフレームのパラメータ解決」をスコープ内に閉じ込めるための仕組み。
        t = time.perf_counter() - start_time
        with parameter_context(param_store, cc_snapshot=None):
            render_scene(draw, t, defaults, renderer)

    # どちらのウィンドウを閉じても同じ終了経路へ寄せる（片方だけ残るのを防ぐ）。
    window.push_handlers(on_close=stop_loop)
    if gui_window is not None:
        gui_window.push_handlers(on_close=stop_loop)

    try:
        # pyglet.app.run() に任せず、ここで 2 つのウィンドウを同一ループで回す。
        # 目的: 「イベント処理 → 描画 → flip」の順序を 1 箇所に集約し、画面更新の競合（点滅）を避ける。
        while running:
            # pyglet の clock を進める（内部タイムスタンプ更新など）。
            pyglet.clock.tick()

            # OS イベント（入力/ウィンドウ操作）を両方のウィンドウで処理する。
            # switch_to() は OpenGL コンテキストを切り替える。特に GUI 側は入力処理が GL 状態に依存し得る。
            for wnd in (window, gui_window):
                if wnd is None:
                    continue
                wnd.switch_to()
                wnd.dispatch_events()

            # どちらかが終了状態なら抜ける（on_close 以外の経路も拾う）。
            if window.has_exit or (gui_window is not None and gui_window.has_exit):
                break

            # --- メイン描画ウィンドウ（プレビュー）---
            window.switch_to()
            # ウィンドウサイズに応じてビューポートを設定し、背景をクリアしてからシーンを描く。
            renderer.viewport(window.width, window.height)
            renderer.clear(settings.background_color)
            render_frame()
            # ここで初めて画面に反映する（このウィンドウの flip はこの 1 箇所に限定する）。
            window.flip()

            if param_gui is not None:
                # --- Parameter GUI ウィンドウ ---
                # GUI 側も「clear → render → flip」を 1 回だけ行う。
                param_gui.draw_frame()

            # 目標 60fps の簡易スロットリング。
            # 正確なフレーム制御よりも「読みやすさ/安定」を優先する（vsync 環境では自然に同期されやすい）。
            time.sleep(1.0 / 60.0)
    finally:
        # イベントループ終了時は明示的に GPU / Window 資源を解放する
        if param_gui is not None:
            # ParameterGUI.close() は内部でウィンドウ close も行う。
            param_gui.close()
        elif gui_window is not None:
            # GUI を作ったが wrapper が無い場合の後始末。
            gui_window.close()
        renderer.release()
        window.close()
