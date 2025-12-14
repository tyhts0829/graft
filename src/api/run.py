"""
どこで: `src/api/run.py`。公開 API のランナー実装。
何を: pyglet + ModernGL を使い、`draw(t)` が返す Geometry/Layer/シーンをウィンドウに描画するランナーを提供する。
なぜ: `main.py` を実行して実際に線をプレビューできる経路を用意するため。
"""

from __future__ import annotations

from typing import Callable

import pyglet

from src.app.runtime.draw_window_system import DrawWindowSystem
from src.app.runtime.window_loop import MultiWindowLoop, WindowTask
from src.parameters import ParamStore
from src.render.layer import LayerStyleDefaults
from src.render.render_settings import RenderSettings
from src.render.scene import SceneItem

DRAW_WINDOW_POS = (500, 100)
PARAMETER_GUI_POS = (1800, 100)


def run(
    draw: Callable[[float], SceneItem],
    *,
    background_color: tuple[float, float, float] = (1.0, 1.0, 1.0),
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
    background_color : tuple[float, float, float]
        背景色 RGB。alpha は 1.0 固定。既定は白。
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

    # pyglet の Window 作成前にオプションを設定する。
    # （vsync はウィンドウ作成時に参照される想定のため、ここで固定しておく）
    pyglet.options["vsync"] = True

    # 描画の見た目/サイズに関わる設定値をまとめる。
    settings = RenderSettings(
        background_color=background_color,
        line_thickness=line_thickness,
        line_color=line_color,
        render_scale=render_scale,
        canvas_size=canvas_size,
    )

    # Layer 側で style 未指定のときに使う既定値（プレビューの見た目）。
    defaults = LayerStyleDefaults(color=line_color, thickness=line_thickness)

    # パラメータは「描画」と「GUI」で共有する。
    # GUI で値を変えると、次フレーム以降の parameter_context 参照に反映される。
    param_store = ParamStore()

    # --- サブシステムの組み立て ---
    # 描画ウィンドウは常に有効（メイン描画）。
    draw_window = DrawWindowSystem(
        draw,
        settings=settings,
        defaults=defaults,
        store=param_store,
    )
    draw_window.window.set_location(*DRAW_WINDOW_POS)

    # `systems` は teardown 用（close 順もここで管理する）。
    systems = [draw_window]

    # `tasks` はループ駆動用（イベント処理→描画→flip の対象）。
    tasks = [WindowTask(window=draw_window.window, draw_frame=draw_window.draw_frame)]

    if parameter_gui:
        # Parameter GUI は依存が重い（pyimgui）ので、使うときだけ遅延 import する。
        from src.app.runtime.parameter_gui_system import ParameterGUIWindowSystem

        gui = ParameterGUIWindowSystem(store=param_store)
        gui.window.set_location(*PARAMETER_GUI_POS)
        systems.append(gui)
        tasks.append(WindowTask(window=gui.window, draw_frame=gui.draw_frame))

    # --- ループの実行 ---
    # ここで複数ウィンドウを 1 つの手動ループで回す。
    # 目的: `flip()` を 1 箇所へ集約し、画面更新の競合（点滅）を避ける。
    loop = MultiWindowLoop(tasks, fps=60.0)
    try:
        loop.run()
    finally:
        # 例外でも確実に後始末する。
        # 作成順の逆で閉じることで、後に作ったサブシステム（GUI など）から先に破棄できる。
        for system in reversed(systems):
            system.close()
