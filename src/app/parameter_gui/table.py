# どこで: `src/app/parameter_gui/table.py`。
# 何を: ParameterRow を 4 列テーブルとして描画し、更新後の行モデルを返す。
# なぜ: テーブルの UI レイアウトを 1 箇所に閉じ込め、store 反映や backend と分離するため。

from __future__ import annotations

from collections.abc import Mapping

from src.parameters.view import ParameterRow

from .labeling import format_param_row_label
from .widgets import render_value_widget

COLUMN_WEIGHTS_DEFAULT = (0.20, 0.60, 0.15, 0.20)


def _row_visible_label(row: ParameterRow) -> str:
    """行の表示ラベル（`op#ordinal arg`）を返す。"""

    return format_param_row_label(row.op, int(row.ordinal), row.arg)


def _row_id(row: ParameterRow) -> str:
    """ImGui の `push_id()` 用に、行の安定 ID を返す。"""

    return f"{row.op}#{row.ordinal}:{row.arg}"


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

    # テーブル内のウィジェット ID が行ごとに衝突しないよう、push_id でスコープを切る。
    # ここで `row.arg` まで含めているのは、同じ op#ordinal でも arg が異なる可能性があるため。
    imgui.push_id(_row_id(row))
    try:
        # 以降の描画は「この行」に対して行う。
        imgui.table_next_row()

        # --- Column 1: label（op#ordinal のみ表示）---
        imgui.table_set_column_index(0)
        imgui.text(row_label)

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

    # `begin_table` は pyimgui のバージョン/バックエンドで返り値が揺れるため、
    # `.opened` 属性があればそれを使い、無ければ返り値自体を bool として扱う。
    table = imgui.begin_table("##parameters", 4, imgui.TABLE_SIZING_STRETCH_PROP)
    opened = getattr(table, "opened", table)
    if not opened:
        return False, rows

    try:
        # 4 列: label / control / min-max / cc
        # それぞれ「残り幅に対する比率」で伸縮させる。
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
        # カラム名（label/control/min-max/cc）をヘッダ行として描画する。
        imgui.table_headers_row()
        # 次の行へ進める（ヘッダの直後にボディを描くための 1 行目の準備）。
        # pyimgui の API は `table_next_row(row_flags, min_row_height)` なので `(0, 1)` を渡している。
        imgui.table_next_row(0, 1)

        # GUI 上の “グループ” は 2 種類ある。
        # - Primitive: (op, ordinal) 単位
        # - Effect: chain_id 単位（チェーンヘッダ → ステップ群）
        # rows は store_bridge 側で「Primitive 群 → Effect 群（チェーン順）」に並べ替え済みなので、
        # 同じ group は連続する前提で “境界” を検出する。
        prev_group_id: tuple[str, object] | None = None
        # 現在グループが open（展開）されているか。
        # グループが閉じている間は、その配下のパラメータ行を描画しない。
        group_open = True
        for row in rows:
            # 現在行が effect ステップに紐づくかどうか（(op, site_id) → (chain_id, step_index)）。
            step_key = (row.op, row.site_id)
            step_info = None if step_info_by_site is None else step_info_by_site.get(step_key)

            if step_info is not None:
                # --- effect: chain_id を group として扱う ---
                chain_id, _step_index = step_info
                group_id: tuple[str, object] = ("effect_chain", chain_id)
                header = (
                    None
                    if effect_chain_header_by_id is None
                    else effect_chain_header_by_id.get(chain_id)
                )
                # “同一チェーン内の同一 op” の出現回数で採番した label を使う。
                step_ordinal = row.ordinal
                if effect_step_ordinal_by_site is not None:
                    step_ordinal = int(effect_step_ordinal_by_site.get(step_key, row.ordinal))
                visible_label = format_param_row_label(row.op, int(step_ordinal), row.arg)
                header_id = f"effect_chain:{chain_id}"
            else:
                # --- primitive: (op, ordinal) を group として扱う ---
                group_key = (row.op, int(row.ordinal))
                group_id = ("primitive", group_key)
                header = (
                    None
                    if primitive_header_by_group is None
                    else primitive_header_by_group.get(group_key)
                )
                visible_label = format_param_row_label(row.op, int(row.ordinal), row.arg)
                header_id = f"primitive:{group_key[0]}#{group_key[1]}"

            if group_id != prev_group_id:
                # グループが切り替わったタイミングで “ヘッダ行” を 1 行だけ描画する。
                if header:
                    imgui.table_next_row()
                    imgui.table_set_column_index(0)
                    # 折りたたみ状態の永続化と ID 衝突回避のため、group 固有 ID で push_id する。
                    # さらに `##...` で visible text と internal id を分離する（同名ヘッダ対策）。
                    imgui.push_id(header_id)
                    try:
                        # collapsing_header は (expanded, visible) を返す。
                        # visible=None なので close ボタン無しで常に表示する。
                        group_open, _visible = imgui.collapsing_header(
                            f"{header}##group_header",
                            None,
                            flags=imgui.TREE_NODE_DEFAULT_OPEN,
                        )
                    finally:
                        imgui.pop_id()
                else:
                    group_open = True
                prev_group_id = group_id

            if not group_open:
                # 折りたたみ中は描画しないが、rows_after の長さを揃えるため “変更なし” として返す。
                updated_rows.append(row)
                continue

            # 展開中は通常どおり 1 行ぶんの 4 列テーブルを描画し、変更後 row を受け取る。
            row_changed, updated = render_parameter_row_4cols(
                row,
                visible_label=visible_label,
            )
            changed_any = changed_any or row_changed
            updated_rows.append(updated)
    finally:
        # begin_table と必ず対になる end_table を呼ぶ。
        imgui.end_table()

    # changed_any は「UI のどこかが変わったか」。
    # updated_rows は store へ差分適用するための “更新後” 行モデル列（rows と同じ長さ）。
    return changed_any, updated_rows
