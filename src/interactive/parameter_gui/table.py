# どこで: `src/interactive/parameter_gui/table.py`。
# 何を: ParameterRow を 4 列テーブルとして描画し、更新後の行モデルを返す。
# なぜ: テーブルの UI レイアウトを 1 箇所に閉じ込め、store 反映や backend と分離するため。

from __future__ import annotations

from collections.abc import Mapping

from src.core.parameters.view import ParameterRow

from .group_blocks import group_blocks_from_rows
from .labeling import format_param_row_label
from .rules import ui_rules_for_row
from .widgets import render_value_widget

COLUMN_WEIGHTS_DEFAULT = (0.20, 0.60, 0.15, 0.20)


def _row_visible_label(row: ParameterRow) -> str:
    """行の表示ラベル（`op#ordinal arg`）を返す。"""

    return format_param_row_label(row.op, int(row.ordinal), row.arg)


def _row_id(row: ParameterRow) -> str:
    """ImGui の `push_id()` 用に、行の安定 ID を返す。"""

    return f"{row.op}#{row.ordinal}:{row.arg}"


def _render_label_cell(imgui, *, row_label: str) -> None:
    """label 列を描画する。"""

    imgui.table_set_column_index(0)
    imgui.text(str(row_label))


def _render_control_cell(imgui, row: ParameterRow) -> tuple[bool, object]:
    """control 列を描画し、(changed, ui_value) を返す。"""

    imgui.table_set_column_index(1)
    imgui.set_next_item_width(-1)  # 残り幅いっぱい
    return render_value_widget(row)


def _render_minmax_cell(
    imgui,
    *,
    rules,
    ui_min: object | None,
    ui_max: object | None,
) -> tuple[bool, object | None, object | None]:
    """min-max 列を描画し、(changed, ui_min, ui_max) を返す。"""

    imgui.table_set_column_index(2)

    if rules.minmax == "float_range":
        min_display = -1.0 if ui_min is None else float(ui_min)
        max_display = 1.0 if ui_max is None else float(ui_max)
        imgui.set_next_item_width(-1)
        changed, min_display, max_display = imgui.drag_float_range2(
            "##ui_range",
            float(min_display),  # current_min
            float(max_display),  # current_max
            0.1,  # speed
            0.0,  # min_value
            0.0,  # max_value
            "%.1f",  # format
            None,
        )
        if not changed:
            return False, ui_min, ui_max
        return True, float(min_display), float(max_display)

    if rules.minmax == "int_range":
        min_display_i = -10 if ui_min is None else int(ui_min)
        max_display_i = 10 if ui_max is None else int(ui_max)
        imgui.set_next_item_width(-1)
        changed, min_display_i, max_display_i = imgui.drag_int_range2(
            "##ui_range",
            int(min_display_i),  # current_min
            int(max_display_i),  # current_max
            0.1,  # speed
            0,  # min_value
            0,  # max_value
        )
        if not changed:
            return False, ui_min, ui_max
        return True, int(min_display_i), int(max_display_i)

    return False, ui_min, ui_max


def _render_cc_cell(
    imgui,
    *,
    rules,
    cc_key: object,
    override: bool,
    cc_key_width: int,
    width_spacer: int,
) -> tuple[bool, object, bool]:
    """cc/override 列を描画し、(changed, cc_key, override) を返す。"""

    imgui.table_set_column_index(3)

    changed_any = False

    if rules.cc_key == "none":
        return False, cc_key, override

    if rules.cc_key == "int3":
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

        if rules.show_override:
            imgui.same_line(0.0, width_spacer)
            clicked_override, override = imgui.checkbox("##override", bool(override))
            if clicked_override:
                changed_any = True

        return changed_any, cc_key, bool(override)

    cc_display = -1 if not isinstance(cc_key, int) else int(cc_key)

    imgui.push_item_width(cc_key_width * 0.88)
    changed_cc, cc_display = imgui.input_int("##cc_key", int(cc_display), 0, 0)
    imgui.pop_item_width()

    clicked_override = False
    if rules.show_override:
        imgui.same_line(0.0, width_spacer)
        clicked_override, override = imgui.checkbox("##override", bool(override))

    if changed_cc:
        changed_any = True
        cc_key = None if cc_display < 0 else int(cc_display)
    if clicked_override:
        changed_any = True

    return changed_any, cc_key, bool(override)


def render_parameter_row_4cols(
    row: ParameterRow, *, visible_label: str | None = None
) -> tuple[bool, ParameterRow]:
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

    row_label = _row_visible_label(row) if visible_label is None else str(visible_label)

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

    rules = ui_rules_for_row(row)

    # テーブル内のウィジェット ID が行ごとに衝突しないよう、push_id でスコープを切る。
    # ここで `row.arg` まで含めているのは、同じ op#ordinal でも arg が異なる可能性があるため。
    imgui.push_id(_row_id(row))
    try:
        # 以降の描画は「この行」に対して行う。
        imgui.table_next_row()

        # --- Column 1: label（op#ordinal のみ表示）---
        _render_label_cell(imgui, row_label=row_label)

        # --- Column 2: control（kind に応じたウィジェット）---
        # slider の visible label はテーブルの label 列で代替するため、
        # ウィジェット側は "##value" を使って非表示にしている。
        changed, value = _render_control_cell(imgui, row)
        if changed:
            changed_any = True
            ui_value = value

        # --- Column 3: min-max（ui_min/ui_max）---
        changed_range, ui_min, ui_max = _render_minmax_cell(
            imgui,
            rules=rules,
            ui_min=ui_min,
            ui_max=ui_max,
        )
        if changed_range:
            changed_any = True

        # --- Column 4: cc override（cc_key/override）---
        changed_cc, cc_key, override = _render_cc_cell(
            imgui,
            rules=rules,
            cc_key=cc_key,
            override=bool(override),
            cc_key_width=cc_key_width,
            width_spacer=width_spacer,
        )
        if changed_cc:
            changed_any = True
    finally:
        # push_id と必ず対になるよう finally で pop_id する。
        imgui.pop_id()

    # ローカル変数へ反映した結果を、新しい ParameterRow として返す。
    updated = ParameterRow(
        label=row.label,
        op=row.op,
        site_id=row.site_id,
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
    primitive_header_by_group: Mapping[tuple[str, int], str] | None = None,
    layer_style_name_by_site_id: Mapping[str, str] | None = None,
    effect_chain_header_by_id: Mapping[str, str] | None = None,
    step_info_by_site: Mapping[tuple[str, str], tuple[str, int]] | None = None,
    effect_step_ordinal_by_site: Mapping[tuple[str, str], int] | None = None,
) -> tuple[bool, list[ParameterRow]]:
    """ParameterRow の列を 4 列テーブルとして描画し、更新後の rows を返す。"""

    import imgui  # type: ignore[import-untyped]

    # 列幅は stretch 比率として使う（負/ゼロは imgui 的にも意味が無いのでエラーにする）。
    label_weight, control_weight, range_weight, meta_weight = column_weights
    if (
        label_weight <= 0.0
        or control_weight <= 0.0
        or range_weight <= 0.0
        or meta_weight <= 0.0
    ):
        raise ValueError(f"column_weights must be > 0: {column_weights}")

    # このテーブル（rows 全体）で変更があったかの集計。
    changed_any = False
    # 返り値として「更新後の row 群」を返すため、描画しながら新しい row を貯める。
    # 注: グループを折りたたんで行を描画しない場合でも、`rows_before` と 1:1 で揃える必要がある。
    #     （store_bridge が `zip(rows_before, rows_after, strict=True)` で差分適用するため）
    updated_rows: list[ParameterRow] = []

    # rows を “連続する group” ごとにブロック化する。
    # `collapsing_header` をテーブル外へ出すことで、ヘッダを全幅で表示できる。
    blocks = group_blocks_from_rows(
        rows,
        primitive_header_by_group=primitive_header_by_group,
        layer_style_name_by_site_id=layer_style_name_by_site_id,
        effect_chain_header_by_id=effect_chain_header_by_id,
        step_info_by_site=step_info_by_site,
        effect_step_ordinal_by_site=effect_step_ordinal_by_site,
    )

    # 列ヘッダ（label/control/min-max/cc）は繰り返すとノイズになるので、
    # 最初に開いたグループのテーブルで 1 回だけ描画する。
    drew_column_headers = False

    for block in blocks:
        # 折りたたみ状態の永続化と ID 衝突回避のため、group 固有 ID で push_id する。
        # - collapsing_header の state（open/close）
        # - begin_table の内部 ID
        # の両方をブロック単位で分離できる。
        imgui.push_id(block.header_id)
        try:
            # collapsing_header は (expanded, visible) を返す。
            # visible=None なので close ボタン無しで常に表示する。
            group_open = True
            if block.header:
                group_open, _visible = imgui.collapsing_header(
                    f"{block.header}##group_header",
                    None,
                    flags=imgui.TREE_NODE_DEFAULT_OPEN,
                )

            if not group_open:
                # 折りたたみ中は描画しないが、rows_after の長さを揃えるため “変更なし” として返す。
                for item in block.items:
                    updated_rows.append(item.row)
                continue

            # --- open のときだけ、当該グループの行を 4 列テーブルとして描く ---
            #
            # `begin_table` は pyimgui のバージョン/バックエンドで返り値が揺れるため、
            # `.opened` 属性があればそれを使い、無ければ返り値自体を bool として扱う。
            table = imgui.begin_table(
                "##parameters", 4, imgui.TABLE_SIZING_STRETCH_PROP
            )
            opened = getattr(table, "opened", table)
            if not opened:
                for item in block.items:
                    updated_rows.append(item.row)
                continue

            try:
                # 4 列: label / control / min-max / cc
                # それぞれ「残り幅に対する比率」で伸縮させる。
                imgui.table_setup_column(
                    "  label", imgui.TABLE_COLUMN_WIDTH_STRETCH, float(label_weight)
                )
                imgui.table_setup_column(
                    "  control",
                    imgui.TABLE_COLUMN_WIDTH_STRETCH,
                    float(control_weight),
                )
                imgui.table_setup_column(
                    "  min - max",
                    imgui.TABLE_COLUMN_WIDTH_STRETCH,
                    float(range_weight),
                )
                imgui.table_setup_column(
                    "  cc",
                    imgui.TABLE_COLUMN_WIDTH_STRETCH,
                    float(meta_weight),
                )
                if not drew_column_headers:
                    # カラム名（label/control/min-max/cc）をヘッダ行として描画する（1回だけ）。
                    imgui.table_headers_row()
                    drew_column_headers = True

                for item in block.items:
                    row_changed, updated = render_parameter_row_4cols(
                        item.row,
                        visible_label=item.visible_label,
                    )
                    changed_any = changed_any or row_changed
                    updated_rows.append(updated)
            finally:
                imgui.end_table()
        finally:
            imgui.pop_id()

    # changed_any は「UI のどこかが変わったか」。
    # updated_rows は store へ差分適用するための “更新後” 行モデル列（rows と同じ長さ）。
    return changed_any, updated_rows
