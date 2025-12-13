# どこで: `src/app/parameter_gui/table.py`。
# 何を: ParameterRow を 4 列テーブルとして描画し、更新後の行モデルを返す。
# なぜ: テーブルの UI レイアウトを 1 箇所に閉じ込め、store 反映や backend と分離するため。

from __future__ import annotations

from src.parameters.view import ParameterRow

from .widgets import render_value_widget

COLUMN_WEIGHTS_DEFAULT = (0.20, 0.60, 0.15, 0.20)


def _row_visible_label(row: ParameterRow) -> str:
    """行の表示ラベル（`op#ordinal`）を返す。"""

    return f"{row.op}#{row.ordinal}"


def _row_id(row: ParameterRow) -> str:
    """ImGui の `push_id()` 用に、行の安定 ID を返す。"""

    return f"{row.op}#{row.ordinal}:{row.arg}"


def render_parameter_row_4cols(row: ParameterRow) -> tuple[bool, ParameterRow]:
    """1 行（1 key）を 4 列テーブルとして描画し、更新後の row を返す。

    Columns
    -------
    1. label : op#ordinal
    2. control : kind に応じたウィジェット
    3. min-max : ui_min/ui_max
    4. cc override : cc_key/override

    Returns
    -------
    changed : bool
        いずれかの UI 値が変更された場合 True。
    row : ParameterRow
        変更を反映した新しい行モデル。
    """

    import imgui  # type: ignore[import-untyped]

    # この 1 行（= 1 key）で何かが変更されたかの集計フラグ。
    changed_any = False

    # ParameterRow は immutable（frozen）なので、まずは更新候補をローカル変数として持つ。
    ui_value = row.ui_value
    ui_min = row.ui_min
    ui_max = row.ui_max
    cc_key = row.cc_key
    override = row.override

    cc_key_width = 30
    width_spacer = 4

    # テーブル内のウィジェット ID が行ごとに衝突しないよう、push_id でスコープを切る。
    # ここで `row.arg` まで含めているのは、同じ op#ordinal でも arg が異なる可能性があるため。
    imgui.push_id(_row_id(row))
    try:
        # 以降の描画は「この行」に対して行う。
        imgui.table_next_row()

        # --- Column 1: label（op#ordinal のみ表示）---
        imgui.table_set_column_index(0)
        imgui.text(_row_visible_label(row))

        # --- Column 2: control（kind に応じたウィジェット）---
        # slider の visible label はテーブルの label 列で代替するため、
        # ウィジェット側は "##value" を使って非表示にしている。
        imgui.table_set_column_index(1)
        imgui.set_next_item_width(-1)  # 残り幅いっぱい
        changed, value = render_value_widget(row)
        if changed:
            changed_any = True
            ui_value = value

        # --- Column 3: min-max（ui_min/ui_max）---
        imgui.table_set_column_index(2)
        # min-max スライダーは float/vec3/int のみ表示可能。
        if row.kind in {"float", "vec3"}:
            min_display = -1.0 if ui_min is None else float(ui_min)
            max_display = 1.0 if ui_max is None else float(ui_max)
            imgui.set_next_item_width(-1)
            changed_range, min_display, max_display = imgui.drag_float_range2(
                "##ui_range",
                float(min_display),  # current_min
                float(max_display),  # current_max
                0.1,  # speed
                0.0,  # min_value
                0.0,  # max_value
                "%.1f",  # format
                None,
            )
            if changed_range:
                changed_any = True
                ui_min = float(min_display)
                ui_max = float(max_display)
        elif row.kind == "int":
            min_display_i = -10 if ui_min is None else int(ui_min)
            max_display_i = 10 if ui_max is None else int(ui_max)
            imgui.set_next_item_width(-1)
            changed_range, min_display_i, max_display_i = imgui.drag_int_range2(
                "##ui_range",
                int(min_display_i),  # current_min
                int(max_display_i),  # current_max
                0.1,  # speed
                0,  # min_value
                0,  # max_value
            )
            if changed_range:
                changed_any = True
                ui_min = int(min_display_i)
                ui_max = int(max_display_i)

        # --- Column 4: cc override（cc_key/override）---
        imgui.table_set_column_index(3)
        if row.kind in {"bool", "string", "choice"}:
            pass
        elif row.kind == "vec3":
            if isinstance(cc_key, tuple):
                a, b, c = cc_key
                v0 = -1 if a is None else int(a)
                v1 = -1 if b is None else int(b)
                v2 = -1 if c is None else int(c)
            else:
                v0, v1, v2 = -1, -1, -1

            changed_cc, out = imgui.input_int3("##cc_key", int(v0), int(v1), int(v2))
            if changed_cc:
                changed_any = True
                cc_tuple = (
                    None if out[0] < 0 else int(out[0]),
                    None if out[1] < 0 else int(out[1]),
                    None if out[2] < 0 else int(out[2]),
                )
                cc_key = None if cc_tuple == (None, None, None) else cc_tuple
            imgui.same_line(0.0, width_spacer)
            clicked_override, override = imgui.checkbox("##override", bool(override))
            if clicked_override:
                changed_any = True
        else:
            cc_display = -1 if not isinstance(cc_key, int) else int(cc_key)

            imgui.push_item_width(cc_key_width * 0.88)
            changed_cc, cc_display = imgui.input_int("##cc_key", int(cc_display), 0, 0)
            imgui.pop_item_width()

            imgui.same_line(0.0, width_spacer)
            clicked_override, override = imgui.checkbox("##override", bool(override))

            if changed_cc:
                changed_any = True
                cc_key = None if cc_display < 0 else int(cc_display)
            if clicked_override:
                changed_any = True
    finally:
        # push_id と必ず対になるよう finally で pop_id する。
        imgui.pop_id()

    # ローカル変数へ反映した結果を、新しい ParameterRow として返す。
    updated = ParameterRow(
        label=row.label,
        op=row.op,
        arg=row.arg,
        kind=row.kind,
        ui_value=ui_value,
        ui_min=ui_min,
        ui_max=ui_max,
        choices=row.choices,
        cc_key=cc_key,
        override=override,
        ordinal=row.ordinal,
    )

    return changed_any, updated


def render_parameter_table(
    rows: list[ParameterRow],
    *,
    column_weights: tuple[float, float, float, float] = COLUMN_WEIGHTS_DEFAULT,
) -> tuple[bool, list[ParameterRow]]:
    """ParameterRow の列を 4 列テーブルとして描画し、更新後の rows を返す。"""

    import imgui  # type: ignore[import-untyped]

    label_weight, control_weight, range_weight, meta_weight = column_weights
    if (
        label_weight <= 0.0
        or control_weight <= 0.0
        or range_weight <= 0.0
        or meta_weight <= 0.0
    ):
        raise ValueError(f"column_weights must be > 0: {column_weights}")

    changed_any = False
    updated_rows: list[ParameterRow] = []

    table = imgui.begin_table("##parameters", 4, imgui.TABLE_SIZING_STRETCH_PROP)
    opened = getattr(table, "opened", table)
    if not opened:
        return False, rows

    try:
        imgui.table_setup_column(
            "label", imgui.TABLE_COLUMN_WIDTH_STRETCH, float(label_weight)
        )
        imgui.table_setup_column(
            "control", imgui.TABLE_COLUMN_WIDTH_STRETCH, float(control_weight)
        )
        imgui.table_setup_column(
            "min-max",
            imgui.TABLE_COLUMN_WIDTH_STRETCH,
            float(range_weight),
        )
        imgui.table_setup_column(
            "cc",
            imgui.TABLE_COLUMN_WIDTH_STRETCH,
            float(meta_weight),
        )
        imgui.table_headers_row()
        imgui.table_next_row(0, 1)

        for row in rows:
            row_changed, updated = render_parameter_row_4cols(row)
            changed_any = changed_any or row_changed
            updated_rows.append(updated)
    finally:
        imgui.end_table()

    return changed_any, updated_rows
