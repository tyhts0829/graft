"""
Purpose:
    Parameter GUIのpyglet window、ImGui context、renderer、IO同期をbackend境界へ隔離する。
Use when:
    GUI window生成、DPI/input同期、font texture、frame begin/render、またはcloseを変更する場合。
Constraints:
    - frame順を`IO sync -> new_frame -> render`に保つ。
    - renderer/font/ImGui resourceを所有window contextが生存中に解放する。
Side effects:
    native window、ImGui context、clipboard callback、GL resourceを操作する。
"""

from __future__ import annotations

from typing import Any, Protocol

from grafix.core.lifecycle import CleanupErrors
from grafix.interactive.pyglet_window_lifecycle import (
    activate_pyglet_window_context,
    close_pyglet_window,
)

DEFAULT_WINDOW_WIDTH = 1100
DEFAULT_WINDOW_HEIGHT = 1000
# 3 固定列を保ったまま Value slider に約 175 px を残せる下限。
MINIMUM_PARAMETER_GUI_WINDOW_WIDTH = 760
MINIMUM_PARAMETER_GUI_WINDOW_HEIGHT = 480


class ImguiPygletRenderer(Protocol):
    """Grafix の pyglet 描画ループが使う renderer 契約。"""

    def process_inputs(self) -> None: ...

    def render(self, draw_data: Any) -> None: ...

    def refresh_font_texture(self) -> None: ...

    def shutdown(self) -> None: ...


class ImguiContentRegion(Protocol):
    """利用可能な ImGui content 幅を公開する最小契約。"""

    def get_content_region_available_width(self) -> float: ...


def content_region_available_width(imgui_mod: ImguiContentRegion) -> float:
    """現在の ImGui window/table cell で利用可能な幅を返す。"""

    return max(0.0, float(imgui_mod.get_content_region_available_width()))


class PygletImguiBackend:
    """ImGui context、pyglet renderer、frame lifecycle を所有する。"""

    def __init__(self, window: Any) -> None:
        self._window = window
        self._imgui: Any | None = None
        self._context: Any | None = None
        self._renderer: ImguiPygletRenderer | None = None
        self._closed = False
        try:
            import imgui

            self._imgui = imgui
            self._context = imgui.create_context()
            self.activate_context()
            self._install_clipboard_callbacks()
            self._renderer = self._create_renderer(window)
        except BaseException:
            try:
                self.close()
            except BaseException:
                pass
            raise

    @staticmethod
    def _create_renderer(window: Any) -> ImguiPygletRenderer:
        """所有 window 用 programmable renderer を作成する。"""

        from imgui.integrations.pyglet import PygletProgrammablePipelineRenderer

        return PygletProgrammablePipelineRenderer(window)

    def _install_clipboard_callbacks(self) -> None:
        """所有 context の clipboard callback を OS と接続する。"""

        io = self.imgui.get_io()
        if io.get_clipboard_text_fn is not None or io.set_clipboard_text_fn is not None:
            return

        import sys

        if sys.platform != "darwin":
            return

        import subprocess

        def set_clipboard_text(text: str) -> None:
            subprocess.run(["pbcopy"], input=str(text), text=True, check=False)

        def get_clipboard_text() -> str:
            result = subprocess.run(
                ["pbpaste"], capture_output=True, text=True, check=False
            )
            return str(result.stdout)

        io.set_clipboard_text_fn = set_clipboard_text
        io.get_clipboard_text_fn = get_clipboard_text

    def _sync_io(self, *, dt: float) -> None:
        """IO を所有 window の現 frame 状態へ同期する。"""

        io = self.imgui.get_io()
        io.delta_time = max(float(dt), 1e-4)
        fb_w, fb_h = self._window.get_framebuffer_size()
        win_w, win_h = self._window.width, self._window.height
        io.display_size = (float(win_w), float(win_h))
        io.display_fb_scale = (
            float(fb_w) / float(max(1, win_w)),
            float(fb_h) / float(max(1, win_h)),
        )

    @property
    def imgui(self) -> Any:
        """この backend が所有する pyimgui module を返す。"""

        imgui = self._imgui
        if imgui is None:
            raise RuntimeError("ImGui backend は初期化されていません")
        return imgui

    def activate_context(self) -> None:
        """所有する ImGui context を current にする。"""

        context = self._context
        if context is None:
            raise RuntimeError("ImGui context は初期化されていません")
        self.imgui.set_current_context(context)

    def begin_frame(self, dt: float) -> None:
        """現在 window の IO を同期して ImGui frame を開始する。"""

        if self._closed:
            raise RuntimeError("ImGui backend は close 済みです")
        self.activate_context()
        imgui = self.imgui
        self._sync_io(dt=float(dt))

        # pyglet integration は wheel delta をそのまま入れるため、この frame 分だけ
        # Grafix の操作方向へ反転し、極端な event burst を bounded にする。
        io = imgui.get_io()
        io.mouse_wheel = max(-0.5, min(0.5, -float(io.mouse_wheel)))
        imgui.new_frame()

    def refresh_font_texture(self) -> None:
        """所有 renderer の font atlas texture を更新する。"""

        renderer = self._renderer
        if renderer is None:
            raise RuntimeError("ImGui renderer は初期化されていません")
        self.activate_context()
        renderer.refresh_font_texture()

    def render(self) -> None:
        """ImGui draw data を構築し、所有 window の GL context へ描画する。"""

        if self._closed:
            raise RuntimeError("ImGui backend は close 済みです")
        self.activate_context()
        imgui = self.imgui
        imgui.render()

        import pyglet

        pyglet.gl.glClearColor(0.12, 0.12, 0.12, 1.0)
        self._window.clear()
        renderer = self._renderer
        if renderer is None:
            raise RuntimeError("ImGui renderer は初期化されていません")
        renderer.render(imgui.get_draw_data())

    def close(self) -> None:
        """renderer と context を逆順で一度だけ解放する。"""

        if self._closed:
            return
        self._closed = True
        errors = CleanupErrors()
        context_active = False
        try:
            context_active = activate_pyglet_window_context(self._window)
        except BaseException as error:
            errors.record(error, "activate ImGui GL context")
        renderer = self._renderer
        if renderer is not None and context_active:
            errors.attempt(renderer.shutdown)
        imgui = self._imgui
        context = self._context
        if imgui is not None and context is not None:
            errors.attempt(lambda: imgui.destroy_context(context))
        self._renderer = None
        self._context = None
        errors.raise_if_any()


def create_parameter_gui_window(
    *,
    width: int = DEFAULT_WINDOW_WIDTH,
    height: int = DEFAULT_WINDOW_HEIGHT,
    caption: str = "Grafix Inspector",
    vsync: bool = False,
) -> Any:
    """Parameter GUI 用の pyglet ウィンドウを生成する。"""

    import pyglet

    gl_cfg = pyglet.gl.Config(  # type: ignore[abstract]
        double_buffer=True,
        sample_buffers=1,
        samples=4,
    )
    window = pyglet.window.Window(  # type: ignore[abstract]
        width=int(width),
        height=int(height),
        caption=str(caption),
        # logical window size はユーザーが調整できる状態を保つ。
        # Retina 対応は framebuffer scale / font atlas 側で行い、
        # backing scale によって logical width を縮めない。
        resizable=True,
        vsync=bool(vsync),
        config=gl_cfg,
    )
    try:
        window.set_minimum_size(
            MINIMUM_PARAMETER_GUI_WINDOW_WIDTH,
            MINIMUM_PARAMETER_GUI_WINDOW_HEIGHT,
        )
    except BaseException:
        try:
            close_pyglet_window(window)
        except BaseException:
            pass
        raise
    return window
