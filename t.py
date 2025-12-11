"""
どこで: tests/manual/test_pyglet_imgui_windows.py。
何を: pyglet 描画ウィンドウと pyimgui パラメータ GUI を別ウィンドウで同時に動かすスモークテスト。
なぜ: 別ウィンドウ構成での共存可否を短時間で確認するため。
"""

from __future__ import annotations

import os
import time
from types import ModuleType
from typing import Any, Callable

import pytest


def _import_gui_modules() -> tuple[ModuleType, ModuleType]:
    """pyglet と imgui を読み込み、失敗時はスキップする。"""

    try:
        import imgui
        import pyglet
    except Exception as exc:  # pragma: no cover - スキップ経路のみ
        pytest.skip(f"pyglet または pyimgui を import できない: {exc}")
    return pyglet, imgui


def _require_display(pyglet_mod: ModuleType) -> None:
    """最小ウィンドウが作れない環境では早期にスキップする。"""

    try:
        test_window = pyglet_mod.window.Window(
            width=1,
            height=1,
            visible=False,
            caption="display probe",
            config=None,
        )
    except Exception as exc:  # pragma: no cover - スキップ経路のみ
        pytest.skip(f"ディスプレイが取得できないためスキップ: {exc}")
    else:
        test_window.close()


def _create_renderer(imgui_pyglet: ModuleType, gui_window) -> object:
    """pyimgui の pyglet レンダラーを生成する。"""

    factory: Callable | None = getattr(imgui_pyglet, "create_renderer", None)
    if callable(factory):
        return factory(gui_window)
    return imgui_pyglet.PygletRenderer(gui_window)


@pytest.mark.skipif(
    os.environ.get("RUN_GUI_TEST") != "1",
    reason="手動 GUI スモークは RUN_GUI_TEST=1 を指定したときだけ実行する。",
)
def test_pyglet_draw_window_and_imgui_gui_can_coexist() -> None:
    """pyglet の描画と pyimgui GUI が別ウィンドウで同居できることを短時間確認する。"""

    pyglet_mod, imgui_mod = _import_gui_modules()
    pyglet_mod.options["vsync"] = True
    _require_display(pyglet_mod)

    try:
        from imgui.integrations import pyglet as imgui_pyglet
    except Exception as exc:  # pragma: no cover - スキップ経路のみ
        pytest.skip(f"pyimgui の pyglet 統合を初期化できない: {exc}")

    # ImGui コンテキスト作成と IO 設定
    gui_context = imgui_mod.create_context()
    io = imgui_mod.get_io()
    # レイアウト保存を無効化して、変な位置に飛ぶのを防ぐ
    io.ini_file_name = None
    imgui_mod.style_colors_light()
    imgui_mod.set_current_context(gui_context)

    gl_cfg = pyglet_mod.gl.Config(double_buffer=True, sample_buffers=1, samples=4)
    draw_window = pyglet_mod.window.Window(
        width=480,
        height=360,
        caption="draw window",
        resizable=False,
        vsync=True,
        config=gl_cfg,
    )
    draw_window.clearcolor = (0.96, 0.97, 1.0, 1.0)
    gui_window = pyglet_mod.window.Window(
        width=520,
        height=400,
        caption="parameter gui",
        resizable=False,
        vsync=True,
        config=gl_cfg,
    )
    gui_window.clearcolor = (0.97, 0.97, 0.97, 1.0)

    renderer: Any = _create_renderer(imgui_pyglet, gui_window)
    font_texture = getattr(renderer, "refresh_font_texture", None)
    if callable(font_texture):
        font_texture()

    batch = pyglet_mod.graphics.Batch()
    circle = pyglet_mod.shapes.Circle(
        x=draw_window.width // 2,
        y=draw_window.height // 2,
        radius=60.0,
        color=(64, 160, 255),
        batch=batch,
    )

    running = True
    frames = 0
    radius = circle.radius
    start = time.monotonic()

    def stop_loop(*_: object) -> None:
        nonlocal running
        running = False

    draw_window.push_handlers(on_close=stop_loop)
    gui_window.push_handlers(on_close=stop_loop)

    timeout = 60
    try:
        while running and (time.monotonic() - start) < timeout:
            # 両ウィンドウのイベント処理
            for wnd in (draw_window, gui_window):
                wnd.switch_to()
                wnd.dispatch_events()

            # 左側: 円の描画
            circle.radius = radius
            draw_window.switch_to()
            draw_window.clear()
            batch.draw()
            draw_window.flip()

            # 右側: ImGui GUI
            gui_window.switch_to()
            gui_window.clear()

            renderer.process_inputs()
            # pyglet バックエンドに任せていてもよいが、
            # 明示的に display_size を合わせておく
            io.display_size = gui_window.width, gui_window.height

            imgui_mod.new_frame()

            panel_w, panel_h = 360, 220
            pos_x = (gui_window.width - panel_w) * 0.5
            pos_y = (gui_window.height - panel_h) * 0.5
            imgui_mod.set_next_window_position(pos_x, pos_y, condition=imgui_mod.ALWAYS)
            imgui_mod.set_next_window_size(panel_w, panel_h, condition=imgui_mod.ALWAYS)

            imgui_mod.begin(
                "Controls",
                flags=imgui_mod.WINDOW_NO_RESIZE | imgui_mod.WINDOW_NO_COLLAPSE,
            )
            imgui_mod.text("スライダーで左の円の半径を変更")
            _, radius = imgui_mod.slider_float("radius", radius, 20.0, 140.0)
            imgui_mod.text(f"frame={frames}")
            imgui_mod.text(f"radius={radius:.1f}")
            if imgui_mod.button("Quit"):
                stop_loop()
            imgui_mod.end()

            imgui_mod.render()
            renderer.render(imgui_mod.get_draw_data())
            gui_window.flip()

            frames += 1
            time.sleep(1 / 60)
    finally:
        shutdown = getattr(renderer, "shutdown", None)
        if callable(shutdown):
            shutdown()
        # コンテキストが生きている場合だけ破棄
        current = imgui_mod.get_current_context()
        if current is not None:
            imgui_mod.set_current_context(gui_context)
            imgui_mod.destroy_context(gui_context)
        draw_window.close()
        gui_window.close()

    assert frames > 0
