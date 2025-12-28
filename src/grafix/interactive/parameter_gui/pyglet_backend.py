# どこで: `src/grafix/interactive/parameter_gui/pyglet_backend.py`。
# 何を: pyglet + imgui の backend（window 生成 / renderer 作成 / IO 同期）を提供する。
# なぜ: GUI の描画ループ（ParameterGUI）から、backend 固有の処理を分離するため。

from __future__ import annotations

from typing import Any

DEFAULT_WINDOW_WIDTH = 800
DEFAULT_WINDOW_HEIGHT = 1000
# Retina(2x) を基準にしたターゲット framebuffer 幅（外部モニタでの見切れ対策）。
DEFAULT_WINDOW_TARGET_FRAMEBUFFER_WIDTH_PX = DEFAULT_WINDOW_WIDTH * 1.5


def _create_imgui_pyglet_renderer(imgui_pyglet_mod: Any, gui_window: Any) -> Any:
    """pyglet 用の ImGui renderer を作成する。"""

    factory = getattr(imgui_pyglet_mod, "create_renderer", None)
    if callable(factory):
        return factory(gui_window)
    renderer_type = getattr(imgui_pyglet_mod, "PygletRenderer", None)
    if renderer_type is None:
        raise RuntimeError("imgui.integrations.pyglet renderer is unavailable")
    return renderer_type(gui_window)


def _sync_imgui_io_for_window(imgui_mod: Any, gui_window: Any, *, dt: float) -> None:
    """ImGui IO をウィンドウ状態（サイズ/Retina スケール/Δt）に同期する。"""

    io = imgui_mod.get_io()
    io.delta_time = max(float(dt), 1e-4)

    fb_w, fb_h = gui_window.get_framebuffer_size()
    win_w, win_h = gui_window.width, gui_window.height
    io.display_size = (float(win_w), float(win_h))
    io.display_fb_scale = (
        float(fb_w) / float(max(1, win_w)),
        float(fb_h) / float(max(1, win_h)),
    )


def create_parameter_gui_window(
    *,
    width: int = DEFAULT_WINDOW_WIDTH,
    height: int = DEFAULT_WINDOW_HEIGHT,
    caption: str = "Parameter GUI",
    vsync: bool = False,
) -> Any:
    """Parameter GUI 用の pyglet ウィンドウを生成する。"""

    import pyglet

    gl_cfg = pyglet.gl.Config(  # type: ignore[abstract]
        double_buffer=True,
        sample_buffers=1,
        samples=4,
    )
    return pyglet.window.Window(  # type: ignore[abstract]
        width=int(width),
        height=int(height),
        caption=str(caption),
        resizable=False,
        vsync=bool(vsync),
        config=gl_cfg,
    )
