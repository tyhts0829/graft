"""
どこで: tests/manual/_runner.py。
何を: pyglet + pyimgui の手動 GUI テスト用の共通ランナー。
なぜ: 初期化・ループ・Retina 対応の重複を減らすため。
"""

from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

warnings.filterwarnings(
    "ignore",
    message="distutils Version classes are deprecated",
    category=DeprecationWarning,
)


class PygletImGuiContext:
    """手動 GUI ランナーから UI 描画へ渡すコンテキスト。

    Attributes
    ----------
    pyglet_mod:
        pyglet モジュール。
    imgui_mod:
        imgui モジュール。
    window:
        GUI 用の pyglet window。
    dt:
        前フレームからの経過秒。
    frame:
        0 始まりのフレーム番号。
    """

    def __init__(
        self,
        *,
        pyglet_mod: ModuleType,
        imgui_mod: ModuleType,
        window: object,
        stop: Callable[[], None],
    ) -> None:
        self.pyglet_mod = pyglet_mod
        self.imgui_mod = imgui_mod
        self.window = window
        self.dt = 0.0
        self.frame = 0
        self._stop = stop

    def stop(self) -> None:
        """イベントループを停止する。"""

        self._stop()


def _import_gui_modules() -> tuple[ModuleType, ModuleType, ModuleType]:
    """pyglet/imgui を読み込み、統合モジュールも返す。"""

    try:
        import imgui
        import pyglet
    except Exception as exc:
        raise SystemExit(f"pyglet または pyimgui を import できない: {exc}")

    try:
        from imgui.integrations import pyglet as imgui_pyglet
    except Exception as exc:
        raise SystemExit(f"pyimgui の pyglet 統合を初期化できない: {exc}")

    return pyglet, imgui, imgui_pyglet


def _require_display(pyglet_mod: ModuleType) -> None:
    """最小ウィンドウが作れない環境では早期に終了する。"""

    try:
        test_window = pyglet_mod.window.Window(
            width=1,
            height=1,
            visible=False,
            caption="display probe",
            config=None,
        )
    except Exception as exc:
        raise SystemExit(f"ディスプレイが取得できないため終了: {exc}")
    else:
        test_window.close()


def _create_renderer(imgui_pyglet: ModuleType, gui_window: object) -> object:
    """pyimgui の pyglet レンダラーを生成する。"""

    factory: Callable[..., object] | None = getattr(imgui_pyglet, "create_renderer", None)
    if callable(factory):
        return factory(gui_window)
    return imgui_pyglet.PygletRenderer(gui_window)


def run_pyglet_imgui(
    draw_ui: Callable[[PygletImGuiContext], None],
    *,
    caption: str,
    width: int,
    height: int,
    fps: float = 60.0,
    clear_color: tuple[float, float, float, float] = (0.12, 0.12, 0.12, 1.0),
    vsync: bool = True,
    resizable: bool = False,
) -> None:
    """pyglet + pyimgui の手動 UI を 1 ウィンドウで実行する。

    Parameters
    ----------
    draw_ui:
        `imgui.new_frame()` の後に呼ばれる UI 描画関数。
    caption:
        ウィンドウタイトル。
    width:
        ウィンドウ幅（論理ピクセル）。
    height:
        ウィンドウ高さ（論理ピクセル）。
    fps:
        目標フレームレート。0 の場合スリープしない。
    clear_color:
        背景色（0-1 RGBA）。
    vsync:
        垂直同期の有効/無効。
    resizable:
        リサイズ可否。
    """

    pyglet_mod, imgui_mod, imgui_pyglet = _import_gui_modules()
    pyglet_mod.options["vsync"] = vsync
    _require_display(pyglet_mod)

    gui_context = imgui_mod.create_context()
    imgui_mod.style_colors_dark()
    imgui_mod.set_current_context(gui_context)

    gl_cfg = pyglet_mod.gl.Config(double_buffer=True, sample_buffers=1, samples=4)
    window = pyglet_mod.window.Window(
        width=width,
        height=height,
        caption=caption,
        resizable=resizable,
        vsync=vsync,
        config=gl_cfg,
    )
    window.clearcolor = clear_color

    renderer: Any = _create_renderer(imgui_pyglet, window)
    font_texture = getattr(renderer, "refresh_font_texture", None)
    if callable(font_texture):
        font_texture()

    running = True
    prev_time = time.monotonic()

    def stop_loop(*_: object) -> None:
        nonlocal running
        running = False

    ctx = PygletImGuiContext(
        pyglet_mod=pyglet_mod,
        imgui_mod=imgui_mod,
        window=window,
        stop=stop_loop,
    )
    window.push_handlers(on_close=stop_loop)

    try:
        while running:
            now = time.monotonic()
            ctx.dt = now - prev_time
            prev_time = now

            pyglet_mod.clock.tick()
            window.switch_to()
            window.dispatch_events()

            renderer.process_inputs()
            imgui_mod.new_frame()

            io = imgui_mod.get_io()
            io.delta_time = max(ctx.dt, 1e-4)
            fb_w, fb_h = window.get_framebuffer_size()
            win_w, win_h = window.width, window.height
            io.display_size = (float(win_w), float(win_h))
            io.display_fb_scale = (
                float(fb_w) / float(win_w),
                float(fb_h) / float(win_h),
            )

            draw_ui(ctx)

            imgui_mod.render()
            window.clear()
            renderer.render(imgui_mod.get_draw_data())
            window.flip()

            ctx.frame += 1
            if fps > 0:
                time.sleep(1 / fps)
    finally:
        shutdown = getattr(renderer, "shutdown", None)
        if callable(shutdown):
            shutdown()
        imgui_mod.destroy_context(gui_context)
        window.close()
