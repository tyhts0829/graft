"""
どこで: tests/manual/test_parameter_gui_float_slider.py。
何を: `src/app/parameter_gui.py` の float スライダーを 1 行だけ表示する手動スモーク。
なぜ: kind ディスパッチを 1 行ずつデバッグしながら確認するため。
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


def _import_gui_modules() -> tuple[ModuleType, ModuleType]:
    """pyglet と imgui を読み込み、失敗時は終了する。"""

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
        test_window.close()


def _create_renderer(imgui_pyglet: ModuleType, gui_window) -> object:
    """pyimgui の pyglet レンダラーを生成する。"""

    factory: Callable | None = getattr(imgui_pyglet, "create_renderer", None)
    if callable(factory):
        return factory(gui_window)
    return imgui_pyglet.PygletRenderer(gui_window)


def main() -> None:
    """float スライダーが描画でき、値が更新できることを確認する。"""

    pyglet_mod, imgui_mod = _import_gui_modules()
    pyglet_mod.options["vsync"] = True
    _require_display(pyglet_mod)
    try:
        from imgui.integrations import pyglet as imgui_pyglet
    except Exception as exc:
        raise SystemExit(f"pyimgui の pyglet 統合を初期化できない: {exc}")

    from src.app.parameter_gui import render_parameter_table
    from src.parameters.view import ParameterRow

    gui_context = imgui_mod.create_context()
    imgui_mod.style_colors_dark()
    imgui_mod.set_current_context(gui_context)

    gl_cfg = pyglet_mod.gl.Config(double_buffer=True, sample_buffers=1, samples=4)
    window = pyglet_mod.window.Window(
        width=800,
        height=240,
        caption="parameter gui smoke (float slider)",
        resizable=False,
        vsync=True,
        config=gl_cfg,
    )
    window.clearcolor = (0.97, 0.97, 0.97, 1.0)

    renderer: Any = _create_renderer(imgui_pyglet, window)
    font_texture = getattr(renderer, "refresh_font_texture", None)
    if callable(font_texture):
        font_texture()

    running = True
    frames = 0
    row = ParameterRow(
        label="1:value",
        op="demo",
        arg="value",
        kind="float",
        ui_value=0.0,
        ui_min=None,
        ui_max=None,
        choices=None,
        cc_key=None,
        override=True,
        ordinal=1,
    )
    prev_time = time.monotonic()

    def stop_loop(*_: object) -> None:
        nonlocal running
        running = False

    window.push_handlers(on_close=stop_loop)

    try:
        while running:
            now = time.monotonic()
            dt = now - prev_time
            prev_time = now

            pyglet_mod.clock.tick()  # OS イベントをポーリング
            window.switch_to()
            window.dispatch_events()

            window.switch_to()
            renderer.process_inputs()
            imgui_mod.new_frame()

            # pyglet 2.x + macOS での Retina 差異を吸収するため、display_size と fb_scale を手動で上書きする
            io = imgui_mod.get_io()
            io.delta_time = max(dt, 1e-4)
            fb_w, fb_h = window.get_framebuffer_size()
            win_w, win_h = window.width, window.height
            io.display_size = (float(win_w), float(win_h))
            io.display_fb_scale = (
                float(fb_w) / float(win_w),
                float(fb_h) / float(win_h),
            )

            imgui_mod.set_next_window_position(0, 0)
            imgui_mod.set_next_window_size(win_w, win_h)
            imgui_mod.begin(
                "Parameter GUI smoke",
                flags=imgui_mod.WINDOW_NO_RESIZE | imgui_mod.WINDOW_NO_COLLAPSE,
            )

            _, rows = render_parameter_table([row])
            row = rows[0]
            imgui_mod.end()
            imgui_mod.render()

            pyglet_mod.gl.glClearColor(0.12, 0.12, 0.12, 1.0)
            window.clear()
            renderer.render(imgui_mod.get_draw_data())
            window.flip()

            frames += 1
            time.sleep(1 / 60)
    finally:
        shutdown = getattr(renderer, "shutdown", None)
        if callable(shutdown):
            shutdown()
        imgui_mod.destroy_context(gui_context)
        window.close()


if __name__ == "__main__":
    main()
