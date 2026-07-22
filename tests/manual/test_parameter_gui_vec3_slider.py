"""
どこで: tests/manual/test_parameter_gui_vec3_slider.py。
何を: `src/app/parameter_gui.py` の vec3 スライダーを 1 行だけ表示する手動スモーク。
なぜ: kind=vec3 のディスパッチとテーブルの挙動を 1 行ずつデバッグ確認するため。
"""

from __future__ import annotations

from _runner import PygletImGuiContext, run_pyglet_imgui

from grafix.core.parameters.view import ParameterRow
from grafix.interactive.parameter_gui import TableRenderInput, render_parameter_table
from grafix.interactive.parameter_gui.catalog import current_parameter_gui_catalog
from grafix.interactive.parameter_gui.group_blocks import group_layout_from_rows
from grafix.interactive.parameter_gui.session_state import WidgetSessionState


def main() -> None:
    """vec3 スライダーが描画でき、値が更新できることを確認する。"""
    catalog = current_parameter_gui_catalog()
    row = ParameterRow(
        label="1:v",
        op="demo",
        site_id="demo:0",
        arg="v",
        kind="vec3",
        ui_value=(0.0, 0.0, 0.0),
        ui_min=None,
        ui_max=None,
        choices=None,
        cc_key=None,
        override=True,
        ordinal=1,
    )
    group_layout = group_layout_from_rows([row], catalog=catalog)
    widget_state = WidgetSessionState()

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
        model_rows = [row]
        edits = render_parameter_table(
            TableRenderInput(
                group_layout=tuple(group_layout),
                model_rows=tuple(model_rows),
                catalog=catalog,
                collapsed_headers=frozenset(),
            ),
            widget_state=widget_state,
        )
        row = edits.rows[0]
        imgui_mod.end()

    run_pyglet_imgui(
        draw_ui,
        caption="parameter gui smoke (vec3 slider)",
        width=800,
        height=240,
    )


if __name__ == "__main__":
    main()
