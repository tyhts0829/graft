# どこで: `src/grafix/interactive/parameter_gui/gui.py`。
# 何を: ParamStore を pyimgui で編集するための最小 GUI（初期化/1フレーム描画/破棄）を提供する。
# なぜ: 依存の重いライフサイクル管理を 1 箇所に閉じ込め、他モジュールを純粋に保つため。

from __future__ import annotations

from pathlib import Path
from typing import Any

from grafix.core.font_resolver import default_font_path
from grafix.core.parameters.store import ParamStore
from grafix.interactive.midi import MidiController

from .midi_learn import MidiLearnState
from .monitor_bar import render_monitor_bar
from .pyglet_backend import (
    DEFAULT_WINDOW_TARGET_FRAMEBUFFER_WIDTH_PX,
    _create_imgui_pyglet_renderer,
    _sync_imgui_io_for_window,
)
from .store_bridge import render_store_parameter_table
from .table import COLUMN_WEIGHTS_DEFAULT


def _default_gui_font_path() -> Path | None:
    try:
        return default_font_path()
    except Exception:
        return None


_DEFAULT_GUI_FONT_PATH = _default_gui_font_path()
_GUI_FONT_SIZE_BASE_PX = 12.0


def _compute_window_backing_scale(gui_window: Any) -> float:
    """ウィンドウの backing scale（DPI 倍率）を返す。"""

    scale = getattr(gui_window, "scale", None)
    if scale is not None:
        return float(max(float(scale), 1.0))

    get_pixel_ratio = getattr(gui_window, "get_pixel_ratio", None)
    if callable(get_pixel_ratio):
        return float(max(float(get_pixel_ratio()), 1.0))  # type: ignore[call-arg]

    return 1.0


class ParameterGUI:
    """pyimgui で ParamStore を編集するための最小 GUI。

    `draw_frame()` を呼ぶことで 1 フレーム分の UI を描画する。
    """

    def __init__(
        self,
        gui_window: Any,
        *,
        store: ParamStore,
        midi_controller: MidiController | None = None,
        monitor: Any | None = None,
        title: str = "Parameters",
        column_weights: tuple[float, float, float, float] = COLUMN_WEIGHTS_DEFAULT,
    ) -> None:
        """GUI の初期化（ImGui コンテキスト / renderer 作成）。"""

        import imgui  # type: ignore[import-untyped]

        # imgui の pyglet backend は環境によって import 経路が揺れるため、明示的にここで解決する。
        try:
            from imgui.integrations import (
                pyglet as imgui_pyglet,  # type: ignore[import-untyped]
            )
        except Exception as exc:
            raise RuntimeError(f"imgui.integrations.pyglet を import できない: {exc}")

        # GUI の描画対象となるウィンドウと、編集対象の ParamStore を保持する。
        self._window = gui_window
        self._store = store
        self._midi_controller = midi_controller
        self._monitor = monitor
        self._midi_learn_state = MidiLearnState()
        self._title = str(title)
        self._column_weights = column_weights
        self._sync_window_width_for_scale()

        # ImGui は「グローバルな current context」を前提にするため、自前コンテキストを作って切り替えながら使う。
        self._imgui = imgui
        self._context = imgui.create_context()
        imgui.style_colors_dark()
        imgui.set_current_context(self._context)

        # pyglet は環境によって「座標系が backing pixel」になり得る。
        # その場合、Retina では物理サイズが小さく見えるため、フォント生成 px を DPI で補正する。
        imgui.get_io().font_global_scale = 1.0

        # ImGui の draw_data を実際に OpenGL へ流す renderer を作る。
        # ここで作られた renderer は内部に GL リソースを保持する。
        self._renderer = _create_imgui_pyglet_renderer(imgui_pyglet, gui_window)

        self._custom_font_path = _DEFAULT_GUI_FONT_PATH
        self._font_backing_scale: float | None = None
        self._sync_font_for_window()

        import time

        # ImGui に渡す delta_time 用の前回時刻。
        self._prev_time = time.monotonic()
        self._closed = False

    def _sync_window_width_for_scale(self) -> None:
        """backing scale に合わせてウィンドウ幅を同期する。"""

        target_fb_width = float(DEFAULT_WINDOW_TARGET_FRAMEBUFFER_WIDTH_PX)
        backing_scale = _compute_window_backing_scale(self._window)
        desired_width = int(round(target_fb_width / backing_scale))

        req_w, req_h = self._window.get_requested_size()
        if int(req_w) == int(desired_width):
            return
        self._window.set_size(int(desired_width), int(req_h))

    def _sync_font_for_window(self) -> None:
        """ウィンドウの backing scale に合わせてフォントを同期する。"""

        if self._custom_font_path is None:
            return

        backing_scale = _compute_window_backing_scale(self._window)
        if self._font_backing_scale == backing_scale:
            return

        io = self._imgui.get_io()
        io.fonts.clear()
        io.fonts.add_font_from_file_ttf(
            str(self._custom_font_path),
            float(_GUI_FONT_SIZE_BASE_PX * backing_scale),
        )

        refresh_font = getattr(self._renderer, "refresh_font_texture", None)
        if callable(refresh_font):
            refresh_font()

        self._font_backing_scale = backing_scale

    def draw_frame(self) -> bool:
        """1 フレーム分の GUI を描画し、変更があれば store に反映する。

        `flip()` は呼ばない。呼び出し側が `window.flip()` を担当する。
        """

        # close() 済みなら何もしない。
        if self._closed:
            return False

        import time

        # 前フレームからの経過秒（ImGui の IO に渡す）。
        now = time.monotonic()
        dt = now - self._prev_time
        self._prev_time = now

        imgui = self._imgui

        self._sync_window_width_for_scale()

        # 以降の ImGui 呼び出しはこのインスタンスの context を対象にする。
        imgui.set_current_context(self._context)
        self._sync_font_for_window()

        # 注: 呼び出し側（pyglet.window.Window.draw）が事前に `self._window.switch_to()` 済みである前提。
        # ここで switch_to() を呼ぶと責務が分散し、点滅の原因（複数箇所での画面更新）になりやすい。

        # 注: imgui.integrations.pyglet の process_inputs() は内部で pyglet.clock.tick() を呼ぶ。
        # `pyglet.app.run()` 駆動時にこれを呼ぶと clock が二重に進みやすいので、ここでは呼ばない。
        # 入力イベント自体は pyglet のイベント配送で io に反映される前提。

        # Parameter GUI のスクロール方向を反転する。
        # pyglet backend は `io.mouse_wheel = scroll` をそのまま入れるため、
        # ここで「このフレームのホイールΔ」だけ符号反転して扱う。
        io = imgui.get_io()
        io.mouse_wheel = float(-float(io.mouse_wheel))

        # --- ImGui フレーム開始 ---
        imgui.new_frame()

        # Δt / Retina スケール / サイズなどをウィンドウ状態に同期する。
        _sync_imgui_io_for_window(imgui, self._window, dt=dt)

        # GUI は 1 ウィンドウで全面表示する（位置/サイズ固定）。
        imgui.set_next_window_position(0, 0)
        imgui.set_next_window_size(self._window.width, self._window.height)
        imgui.begin(
            self._title,
            flags=imgui.WINDOW_NO_RESIZE
            | imgui.WINDOW_NO_COLLAPSE
            | imgui.WINDOW_NO_TITLE_BAR,
        )

        monitor = self._monitor
        if monitor is not None:
            midi = self._midi_controller
            render_monitor_bar(
                imgui,
                monitor.snapshot(),
                midi_port_name=None if midi is None else str(midi.port_name),
            )

        # ParamStore の表だけをスクロール領域に閉じ込め、監視バーは常に見えるようにする。
        imgui.begin_child("##parameter_table_scroll", 0, 0, border=False)
        try:
            # ParamStore をテーブルとして描画し、編集結果を store に反映する。
            changed = render_store_parameter_table(
                self._store,
                column_weights=self._column_weights,
                midi_learn_state=self._midi_learn_state,
                midi_last_cc_change=(
                    None
                    if self._midi_controller is None
                    else self._midi_controller.last_cc_change
                ),
            )
        finally:
            imgui.end_child()
        imgui.end()

        # --- ImGui フレーム終了（draw_data 構築）---
        imgui.render()

        import pyglet

        # 背景をダークグレーでクリアし、その上に ImGui の draw_data を描く。
        pyglet.gl.glClearColor(0.12, 0.12, 0.12, 1.0)
        self._window.clear()
        self._renderer.render(imgui.get_draw_data())
        # `flip()` は MultiWindowLoop が担当する（ここでは呼ばない）。
        return changed

    def close(self) -> None:
        """GUI を終了し、コンテキストとウィンドウを破棄する。"""

        # 二重 close を許容する（呼び出し側の finally から安全に呼べるようにする）。
        if self._closed:
            return
        self._closed = True

        # backend が持つ GL リソースを破棄し、ImGui context を破棄してから window を閉じる。
        shutdown = getattr(self._renderer, "shutdown", None)
        if callable(shutdown):
            shutdown()
        self._imgui.destroy_context(self._context)
        self._window.close()
