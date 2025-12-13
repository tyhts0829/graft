# どこで: `src/app/parameter_gui/gui.py`。
# 何を: ParamStore を pyimgui で編集するための最小 GUI（初期化/1フレーム描画/破棄）を提供する。
# なぜ: 依存の重いライフサイクル管理を 1 箇所に閉じ込め、他モジュールを純粋に保つため。

from __future__ import annotations

from typing import Any

from src.parameters.store import ParamStore

from .pyglet_backend import _create_imgui_pyglet_renderer, _sync_imgui_io_for_window
from .store_bridge import render_store_parameter_table
from .table import COLUMN_WEIGHTS_DEFAULT


class ParameterGUI:
    """pyimgui で ParamStore を編集するための最小 GUI。

    `draw_frame()` を呼ぶことで 1 フレーム分の UI を描画する。
    """

    def __init__(
        self,
        gui_window: Any,
        *,
        store: ParamStore,
        title: str = "Parameters",
        column_weights: tuple[float, float, float, float] = COLUMN_WEIGHTS_DEFAULT,
    ) -> None:
        """GUI の初期化（ImGui コンテキスト / renderer 作成）。"""

        import imgui  # type: ignore[import-untyped]

        try:
            from imgui.integrations import pyglet as imgui_pyglet  # type: ignore[import-untyped]
        except Exception as exc:
            raise RuntimeError(f"imgui.integrations.pyglet を import できない: {exc}")

        self._window = gui_window
        self._store = store
        self._title = str(title)
        self._column_weights = column_weights

        self._imgui = imgui
        self._context = imgui.create_context()
        imgui.style_colors_dark()
        imgui.set_current_context(self._context)

        self._renderer = _create_imgui_pyglet_renderer(imgui_pyglet, gui_window)
        refresh_font = getattr(self._renderer, "refresh_font_texture", None)
        if callable(refresh_font):
            refresh_font()

        import time

        self._prev_time = time.monotonic()
        self._closed = False

    def draw_frame(self) -> bool:
        """1 フレーム分の GUI を描画し、変更があれば store に反映する。"""

        if self._closed:
            return False

        import time

        now = time.monotonic()
        dt = now - self._prev_time
        self._prev_time = now

        imgui = self._imgui
        imgui.set_current_context(self._context)

        self._window.switch_to()
        self._renderer.process_inputs()
        imgui.new_frame()
        _sync_imgui_io_for_window(imgui, self._window, dt=dt)

        imgui.set_next_window_position(0, 0)
        imgui.set_next_window_size(self._window.width, self._window.height)
        imgui.begin(
            self._title,
            flags=imgui.WINDOW_NO_RESIZE | imgui.WINDOW_NO_COLLAPSE,
        )
        changed = render_store_parameter_table(
            self._store,
            column_weights=self._column_weights,
        )
        imgui.end()
        imgui.render()

        import pyglet

        pyglet.gl.glClearColor(0.12, 0.12, 0.12, 1.0)
        self._window.clear()
        self._renderer.render(imgui.get_draw_data())
        self._window.flip()
        return changed

    def close(self) -> None:
        """GUI を終了し、コンテキストとウィンドウを破棄する。"""

        if self._closed:
            return
        self._closed = True

        shutdown = getattr(self._renderer, "shutdown", None)
        if callable(shutdown):
            shutdown()
        self._imgui.destroy_context(self._context)
        self._window.close()
