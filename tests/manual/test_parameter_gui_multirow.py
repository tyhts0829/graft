"""
どこで: tests/manual/test_parameter_gui_multirow.py。
何を: bool / choice / string / float / int / vec3 の 6 行を 1 つの 4 列テーブルに表示する手動スモーク。
なぜ: 実際の GUI に近い「複数行」のレイアウト崩れや ID 衝突を早期に検知するため。
"""

from __future__ import annotations

from _runner import PygletImGuiContext, run_pyglet_imgui

from src.app.parameter_gui import render_parameter_table
from src.parameters.view import ParameterRow


def main() -> None:
    """複数行のテーブルが描画でき、操作しても落ちないことを確認する。"""
    rows = [
        ParameterRow(
            label="1:enabled",
            op="demo",
            site_id="demo:0",
            arg="enabled",
            kind="bool",
            ui_value=False,
            ui_min=None,
            ui_max=None,
            choices=None,
            cc_key=None,
            override=True,
            ordinal=1,
        ),
        ParameterRow(
            label="2:mode",
            op="demo",
            site_id="demo:1",
            arg="mode",
            kind="choice",
            ui_value="green",
            ui_min=None,
            ui_max=None,
            choices=["red", "green", "blue"],
            cc_key=None,
            override=True,
            ordinal=2,
        ),
        ParameterRow(
            label="3:text",
            op="demo",
            site_id="demo:2",
            arg="text",
            kind="string",
            ui_value="hello",
            ui_min=None,
            ui_max=None,
            choices=None,
            cc_key=None,
            override=True,
            ordinal=3,
        ),
        ParameterRow(
            label="4:gain",
            op="demo",
            site_id="demo:3",
            arg="gain",
            kind="float",
            ui_value=0.0,
            ui_min=None,
            ui_max=None,
            choices=None,
            cc_key=None,
            override=True,
            ordinal=4,
        ),
        ParameterRow(
            label="5:count",
            op="demo",
            site_id="demo:4",
            arg="count",
            kind="int",
            ui_value=0,
            ui_min=None,
            ui_max=None,
            choices=None,
            cc_key=None,
            override=True,
            ordinal=5,
        ),
        ParameterRow(
            label="6:offset",
            op="demo",
            site_id="demo:5",
            arg="offset",
            kind="vec3",
            ui_value=(0.0, 0.0, 0.0),
            ui_min=None,
            ui_max=None,
            choices=None,
            cc_key=None,
            override=True,
            ordinal=6,
        ),
    ]

    def draw_ui(ctx: PygletImGuiContext) -> None:
        nonlocal rows

        imgui_mod = ctx.imgui_mod
        win_w, win_h = ctx.window.width, ctx.window.height
        imgui_mod.set_next_window_position(0, 0)
        imgui_mod.set_next_window_size(win_w, win_h)
        imgui_mod.begin(
            "Parameter GUI smoke",
            flags=imgui_mod.WINDOW_NO_RESIZE | imgui_mod.WINDOW_NO_COLLAPSE,
        )

        _, rows = render_parameter_table(rows)
        if imgui_mod.button("Quit"):
            ctx.stop()

        imgui_mod.end()

    run_pyglet_imgui(
        draw_ui,
        caption="parameter gui smoke (multirow)",
        width=800,
        height=440,
    )


if __name__ == "__main__":
    main()
