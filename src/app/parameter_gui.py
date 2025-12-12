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
        label="##value",
        value=value,
        min_value=min_value,
        max_value=max_value,
    )


_KIND_TO_WIDGET: dict[str, WidgetFn] = {
    "float": widget_float_slider,
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

        # 入力ウィジェットに渡す表示用の値へ変換する（None のときは既定レンジに寄せる）。
        min_display = -1.0 if ui_min is None else float(ui_min)
        max_display = 1.0 if ui_max is None else float(ui_max)
        cc_display = -1 if cc_key is None else int(cc_key)

        # ui_min
        # imgui.text("min")
        imgui.same_line()
        imgui.push_item_width(60)
        changed_min, min_display = imgui.input_float(
            label="##ui_min",
            value=float(min_display),
            format="%.1f",
        )
        imgui.pop_item_width()

        # ui_max
        imgui.same_line()
        imgui.push_item_width(60)
        changed_max, max_display = imgui.input_float(
            label="##ui_max",
            value=float(max_display),
            format="%.1f",
        )
        imgui.pop_item_width()

        # cc_key（負の値は None として扱う）
        imgui.same_line()
        imgui.push_item_width(60)
        changed_cc, cc_display = imgui.input_int(
            label="##cc_key", value=int(cc_display)
        )
        imgui.pop_item_width()

        # override（checkbox の戻り値は clicked, state。clicked を changed として扱う）
        imgui.same_line()
        clicked_override, override = imgui.checkbox(
            label="##override", state=bool(override)
        )

        # 変更があった項目のみ、row のフィールドへ反映する。
        if changed_min:
            changed_any = True
            ui_min = float(min_display)
        if changed_max:
            changed_any = True
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
    return changed_any, updated


def render_parameter_table(rows: list[ParameterRow]) -> tuple[bool, list[ParameterRow]]:
    """ParameterRow の列を 3 列テーブルとして描画し、更新後の rows を返す。"""

    import imgui

    changed_any = False
    updated_rows: list[ParameterRow] = []

    if not imgui.begin_table("##parameters", 3):
        return False, rows

    try:
        imgui.table_setup_column("label")
        imgui.table_setup_column("control")
        imgui.table_setup_column("min, max, cc, override")
        imgui.table_headers_row()

        for row in rows:
            row_changed, updated = render_parameter_row_3cols(row)
            changed_any = changed_any or row_changed
            updated_rows.append(updated)
    finally:
        imgui.end_table()

    return changed_any, updated_rows
