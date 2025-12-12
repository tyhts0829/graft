# どこで: `src/app/parameter_gui.py`。
# 何を: ParamStore の行モデル（ParameterRow）を pyimgui のウィジェットに対応付けて描画する。
# なぜ: kind ごとに関数を分離し、手動スモークで 1 行ずつ確認できるようにするため。

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.parameters.view import ParameterRow

WidgetFn = Callable[[ParameterRow], tuple[bool, Any]]


def _row_visible_label(row: ParameterRow) -> str:
    return f"{row.op}#{row.ordinal}"


def _row_id(row: ParameterRow) -> str:
    return f"{row.op}#{row.ordinal}:{row.arg}"


def _float_slider_range(row: ParameterRow) -> tuple[float, float]:
    """float スライダーのレンジ (min, max) を返す。

    ui_min/ui_max が None の場合は -1.0..1.0 にフォールバックする。
    """

    min_value = -1.0 if row.ui_min is None else float(row.ui_min)
    max_value = 1.0 if row.ui_max is None else float(row.ui_max)
    if min_value >= max_value:
        raise ValueError(
            f"ui_min must be < ui_max for float slider: {min_value} >= {max_value}"
        )
    return min_value, max_value


def _int_slider_range(row: ParameterRow) -> tuple[int, int]:
    """int スライダーのレンジ (min, max) を返す。

    ui_min/ui_max が None の場合は -10..10 にフォールバックする。
    """

    min_value = -10 if row.ui_min is None else int(row.ui_min)
    max_value = 10 if row.ui_max is None else int(row.ui_max)
    if min_value >= max_value:
        raise ValueError(
            f"ui_min must be < ui_max for int slider: {min_value} >= {max_value}"
        )
    return min_value, max_value


def _as_float3(value: Any) -> tuple[float, float, float]:
    try:
        x, y, z = value  # type: ignore[misc]
    except Exception as exc:
        raise ValueError(
            f"vec3 ui_value must be a length-3 sequence: {value!r}"
        ) from exc
    return float(x), float(y), float(z)


def widget_float_slider(row: ParameterRow) -> tuple[bool, float]:
    """kind=float のスライダーを描画し、(changed, value) を返す。

    Parameters
    ----------
    row : ParameterRow
        kind=float の行モデル。

    Returns
    -------
    changed : bool
        値が変更された場合 True。
    value : float
        変更後の値。

    Raises
    ------
    ValueError
        ui_min >= ui_max の場合。
    """

    import imgui

    value = float(row.ui_value)
    min_value, max_value = _float_slider_range(row)
    return imgui.slider_float(
        "##value", float(value), float(min_value), float(max_value)
    )


def widget_int_slider(row: ParameterRow) -> tuple[bool, int]:
    """kind=int のスライダーを描画し、(changed, value) を返す。"""

    import imgui

    value = int(row.ui_value)
    min_value, max_value = _int_slider_range(row)
    return imgui.slider_int("##value", int(value), int(min_value), int(max_value))


def widget_vec3_slider(row: ParameterRow) -> tuple[bool, tuple[float, float, float]]:
    """kind=vec3 のスライダーを描画し、(changed, value) を返す。"""

    import imgui

    value0, value1, value2 = _as_float3(row.ui_value)
    min_value, max_value = _float_slider_range(row)
    changed, out = imgui.slider_float3(
        "##value",
        float(value0),
        float(value1),
        float(value2),
        float(min_value),
        float(max_value),
    )
    return changed, _as_float3(out)


def widget_bool_checkbox(row: ParameterRow) -> tuple[bool, bool]:
    """kind=bool のチェックボックスを描画し、(changed, value) を返す。"""

    import imgui

    clicked, state = imgui.checkbox("##value", bool(row.ui_value))
    return clicked, bool(state)


_KIND_TO_WIDGET: dict[str, WidgetFn] = {
    "float": widget_float_slider,
    "int": widget_int_slider,
    "vec3": widget_vec3_slider,
    "bool": widget_bool_checkbox,
}


def render_value_widget(row: ParameterRow) -> tuple[bool, Any]:
    """row.kind に応じたウィジェットを描画し、(changed, value) を返す。

    Parameters
    ----------
    row : ParameterRow
        GUI 行モデル。

    Returns
    -------
    changed : bool
        値が変更された場合 True。
    value : Any
        変更後の値。

    Raises
    ------
    ValueError
        未知 kind の場合。
    """

    fn = _KIND_TO_WIDGET.get(row.kind)
    if fn is None:
        raise ValueError(f"unknown kind: {row.kind}")
    return fn(row)


def widget_registry() -> dict[str, WidgetFn]:
    """kind→widget 関数マップのコピーを返す。"""

    return dict(_KIND_TO_WIDGET)


def render_parameter_row_3cols(row: ParameterRow) -> tuple[bool, ParameterRow]:
    """1 行（1 key）を 3 列テーブルとして描画し、更新後の row を返す。

    Columns
    -------
    1. label : op#ordinal
    2. control : kind に応じたウィジェット
    3. meta : ui_min/ui_max/cc_key/override

    Returns
    -------
    changed : bool
        いずれかの UI 値が変更された場合 True。
    row : ParameterRow
        変更を反映した新しい行モデル。
    """

    import imgui

    # この 1 行（= 1 key）で何かが変更されたかの集計フラグ。
    changed_any = False

    # ParameterRow は immutable（frozen）なので、まずは更新候補をローカル変数として持つ。
    ui_value = row.ui_value
    ui_min = row.ui_min
    ui_max = row.ui_max
    cc_key = row.cc_key
    override = row.override

    # 幅定数
    UI_MIN_MAX_WIDTH = 64
    CC_KEY_WIDTH = 34
    WIDTH_SPACER = 4

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
        # slider の visible label はテーブルの label 列で代替するため、ウィジェット側は "##value" を使って非表示にしている。
        imgui.table_set_column_index(1)
        imgui.set_next_item_width(-1)  # 残り幅いっぱい
        changed, value = render_value_widget(row)
        if changed:
            changed_any = True
            ui_value = value

        # --- Column 3: meta（ui_min/ui_max/cc_key/override を横並びに配置）---
        imgui.table_set_column_index(2)

        # bool は meta（ui_min/ui_max/cc/override）を使わないので、3列目は空にする。
        if row.kind == "bool":
            pass
        else:
            cc_display = -1 if cc_key is None else int(cc_key)

            if row.kind == "int":
                # input_int は int のみ受けるので、表示用も int で扱う。
                min_display_i = -10 if ui_min is None else int(ui_min)
                max_display_i = 10 if ui_max is None else int(ui_max)

                imgui.push_item_width(UI_MIN_MAX_WIDTH)
                changed_min, min_display_i = imgui.input_int(
                    "##ui_min", int(min_display_i), 0, 0
                )
                imgui.pop_item_width()

                imgui.same_line(0.0, WIDTH_SPACER)
                imgui.push_item_width(UI_MIN_MAX_WIDTH)
                changed_max, max_display_i = imgui.input_int(
                    "##ui_max", int(max_display_i), 0, 0
                )
                imgui.pop_item_width()

            else:
                # float/vec3 は float レンジとして扱う。
                min_display = -1.0 if ui_min is None else float(ui_min)
                max_display = 1.0 if ui_max is None else float(ui_max)

                imgui.push_item_width(UI_MIN_MAX_WIDTH)
                changed_min, min_display = imgui.input_float(
                    "##ui_min", float(min_display), 0.0, 0.0, "%.1f"
                )
                imgui.pop_item_width()

                imgui.same_line(0.0, WIDTH_SPACER)
                imgui.push_item_width(UI_MIN_MAX_WIDTH)
                changed_max, max_display = imgui.input_float(
                    "##ui_max", float(max_display), 0.0, 0.0, "%.1f"
                )
                imgui.pop_item_width()

            # cc_key（負の値は None として扱う）
            imgui.same_line(0.0, WIDTH_SPACER)
            imgui.push_item_width(CC_KEY_WIDTH)
            changed_cc, cc_display = imgui.input_int("##cc_key", int(cc_display), 0, 0)
            imgui.pop_item_width()

            # override（checkbox の戻り値は clicked, state。clicked を changed として扱う）
            imgui.same_line(0.0, WIDTH_SPACER)
            clicked_override, override = imgui.checkbox("##override", bool(override))

            # 変更があった項目のみ、row のフィールドへ反映する。
            if changed_min:
                changed_any = True
                if row.kind == "int":
                    ui_min = int(min_display_i)
                else:
                    ui_min = float(min_display)
            if changed_max:
                changed_any = True
                if row.kind == "int":
                    ui_max = int(max_display_i)
                else:
                    ui_max = float(max_display)
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

    # ここで例外にしておくと、ui_min/ui_max の不整合を早期に検知できる。
    if updated.kind == "float":
        _float_slider_range(updated)
    if updated.kind == "int":
        _int_slider_range(updated)
    if updated.kind == "vec3":
        _float_slider_range(updated)
    return changed_any, updated


def render_parameter_table(
    rows: list[ParameterRow],
    *,
    column_weights: tuple[float, float, float] = (0.20, 0.55, 0.25),
) -> tuple[bool, list[ParameterRow]]:
    """ParameterRow の列を 3 列テーブルとして描画し、更新後の rows を返す。"""

    import imgui

    label_weight, control_weight, meta_weight = column_weights
    if label_weight <= 0.0 or control_weight <= 0.0 or meta_weight <= 0.0:
        raise ValueError(f"column_weights must be > 0: {column_weights}")

    changed_any = False
    updated_rows: list[ParameterRow] = []

    table = imgui.begin_table("##parameters", 3, imgui.TABLE_SIZING_STRETCH_PROP)
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
            "min, max, cc, override",
            imgui.TABLE_COLUMN_WIDTH_STRETCH,
            float(meta_weight),
        )
        imgui.table_headers_row()

        for row in rows:
            row_changed, updated = render_parameter_row_3cols(row)
            changed_any = changed_any or row_changed
            updated_rows.append(updated)
    finally:
        imgui.end_table()

    return changed_any, updated_rows
