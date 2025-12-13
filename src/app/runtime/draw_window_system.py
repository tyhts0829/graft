# どこで: `src/app/runtime/draw_window_system.py`。
# 何を: `draw(t)` が返すシーンを描画ウィンドウへ描画するサブシステムを提供する。
# なぜ: `src/api/run.py` の `run()` を「配線」に寄せ、描画責務を独立させるため。

from __future__ import annotations

import time
from typing import Callable

from src.app.draw_window import create_draw_window
from src.parameters import ParamStore, parameter_context
from src.render.draw_renderer import DrawRenderer
from src.render.frame_pipeline import render_scene
from src.render.layer import LayerStyleDefaults
from src.render.render_settings import RenderSettings
from src.render.scene import SceneItem


class DrawWindowSystem:
    """描画（メインウィンドウ）のサブシステム。"""

    def __init__(
        self,
        draw: Callable[[float], SceneItem],
        *,
        settings: RenderSettings,
        defaults: LayerStyleDefaults,
        store: ParamStore,
    ) -> None:
        """描画用の window/renderer を初期化する。"""

        # 設定/既定スタイル/draw 関数/ParamStore は 1 フレームごとに参照するため保持しておく。
        self._settings = settings
        self._defaults = defaults
        self._draw = draw
        self._store = store

        # 描画用の pyglet window を作成し、その window の OpenGL コンテキストに紐づく renderer を作る。
        self.window = create_draw_window(settings)
        self._renderer = DrawRenderer(self.window, settings)

        # draw(t) に渡す t の基準時刻。
        self._start_time = time.perf_counter()

    def draw_frame(self) -> None:
        """1 フレーム分の描画を行う（`flip()` は呼ばない）。"""

        # 注: 呼び出し側（MultiWindowLoop）が事前に self.window.switch_to() 済みである前提。
        # その前提が崩れると、別 window のコンテキストへ描いてしまう可能性がある。

        # 現在のウィンドウサイズに合わせてビューポートを更新する。
        self._renderer.viewport(self.window.width, self.window.height)

        # まず背景色でクリアし、その上にシーンを描く。
        self._renderer.clear(self._settings.background_color)

        # このフレームの経過秒 t を算出する。
        t = time.perf_counter() - self._start_time

        # ParamStore を参照するコンテキストで draw(t)→scene を解決し、renderer へ流す。
        # `parameter_context` は「このフレームで参照されるパラメータ」をスコープ内へ閉じ込めるための仕組み。
        with parameter_context(self._store, cc_snapshot=None):
            render_scene(self._draw, t, self._defaults, self._renderer)

    def close(self) -> None:
        """GPU / window 資源を解放する。"""

        # renderer が保持している GPU リソースを破棄してから window を閉じる。
        self._renderer.release()
        self.window.close()
