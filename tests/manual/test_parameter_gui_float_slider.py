"""
どこで: tests/manual/test_parameter_gui_float_slider.py。
何を: `src/app/parameter_gui.py` の float スライダーを 1 行だけ表示する手動スモーク。
なぜ: kind ディスパッチを 1 行ずつデバッグしながら確認するため。
"""

from __future__ import annotations

from _runner import PygletImGuiContext, run_pyglet_imgui

from src.interactive.parameter_gui import render_parameter_table
from src.core.parameters.view import ParameterRow


def main() -> None:
    """float スライダーが描画でき、値が更新できることを確認する。"""
    row = ParameterRow(
        label="1:value",
        op="demo",
        site_id="demo:0",
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

    def draw_ui(ctx: PygletImGuiContext) -> None:
        nonlocal row

        imgui_mod = ctx.imgui_mod
        win_w, win_h = ctx.window.width, ctx.window.height
        imgui_mod.set_next_window_position(0, 0)
        imgui_mod.set_next_window_size(win_w, win_h)
        imgui_mod.begin(
            "Parameter GUI smoke",
            flags=imgui_mod.WINDOW_NO_RESIZE | imgui_mod.WINDOW_NO_COLLAPSE,
        )
        _, rows = render_parameter_table([row])
        row = rows[0]
        imgui_mod.end()

    run_pyglet_imgui(
        draw_ui,
        caption="parameter gui smoke (float slider)",
        width=800,
        height=240,
    )


if __name__ == "__main__":
    main()
