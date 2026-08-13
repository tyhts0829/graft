"""
Purpose:
    pygletとpyimguiの手動GUI検証へ、Retina対応を含む共通window loopとcleanup境界を提供する。
Use when:
    自動testでは判断しにくいinteractive widget、入力、描画を単一windowで目視確認するとき。
Constraints:
    - UI callbackはnew_frame後かつrender前に呼び、framebuffer scaleを毎frame反映する。
    - display利用不能時は曖昧に継続せず、明示的にmanual runを終了する。
    - rendererはactive GL context内で閉じ、ImGui context、windowの順で全resource cleanupを試みる。
    - cleanup失敗で元の実行例外を失わず、CleanupErrorsへ集約する。
Side effects:
    sys.pathをrepository srcへ向け、native window/GL/ImGui contextとevent loopを操作する。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from grafix.core.lifecycle import CleanupErrors  # noqa: E402
from grafix.interactive.pyglet_window_lifecycle import (  # noqa: E402
    activate_pyglet_window_context,
    close_pyglet_window,
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


def _import_gui_modules() -> tuple[ModuleType, ModuleType]:
    """pyglet/imgui を読み込む。"""

    try:
        import imgui
        import pyglet
    except Exception as exc:
        raise SystemExit(f"pyglet または pyimgui を import できない: {exc}")

    return pyglet, imgui


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
        close_pyglet_window(test_window)


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

    pyglet_mod, imgui_mod = _import_gui_modules()
    pyglet_mod.options["vsync"] = vsync
    _require_display(pyglet_mod)

    gui_context: Any | None = None
    window: Any | None = None
    renderer: Any | None = None
    root_error: BaseException | None = None
    try:
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

        from imgui.integrations.pyglet import PygletProgrammablePipelineRenderer

        renderer = PygletProgrammablePipelineRenderer(window)
        renderer.refresh_font_texture()

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
    except BaseException as error:
        root_error = error
    finally:
        errors = CleanupErrors(initial_error=root_error)
        context_active = False
        if renderer is not None and window is not None:
            try:
                context_active = activate_pyglet_window_context(window)
            except BaseException as error:
                errors.record(error, "activate manual ImGui context")
        if renderer is not None and context_active:
            errors.attempt(renderer.shutdown, "close manual ImGui renderer")
        if gui_context is not None:
            errors.attempt(
                lambda: imgui_mod.destroy_context(gui_context),
                "close manual ImGui context",
            )
        if window is not None:
            errors.attempt(lambda: close_pyglet_window(window), "close manual window")
        errors.raise_if_any()
