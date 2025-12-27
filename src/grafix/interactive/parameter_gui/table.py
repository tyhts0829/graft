# どこで: `src/grafix/interactive/parameter_gui/table.py`。
# 何を: ParameterRow を 4 列テーブルとして描画し、更新後の行モデルを返す。
# なぜ: テーブルの UI レイアウトを 1 箇所に閉じ込め、store 反映や backend と分離するため。

from __future__ import annotations

from collections.abc import Mapping

from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.view import ParameterRow

from .group_blocks import group_blocks_from_rows
from .group_blocks import GroupBlock
from .labeling import format_param_row_label
from .midi_learn import MidiLearnState
from .rules import ui_rules_for_row
from .widgets import render_value_widget

COLUMN_WEIGHTS_DEFAULT = (0.20, 0.60, 0.15, 0.20)

GROUP_HEADER_BASE_COLORS_RGBA: dict[str, tuple[int, int, int, int]] = {
    "style": (51, 102, 217, 140),
    "primitive": (152, 74, 74, 140),
    "effect": (53, 117, 76, 140),
}


def _clamp01(x: float) -> float:
    """0..1 に clamp した値を返す。"""

    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return float(x)


def _rgba01_from_rgba255(
    rgba: tuple[int, int, int, int],
) -> tuple[float, float, float, float]:
    """0..255 の RGBA を 0..1 の RGBA に変換して返す。"""

    r, g, b, a = rgba
    return (
        _clamp01(float(r) / 255.0),
        _clamp01(float(g) / 255.0),
        _clamp01(float(b) / 255.0),
        _clamp01(float(a) / 255.0),
    )


def _derive_header_colors(
    base: tuple[float, float, float, float],
) -> tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]:
    """(normal, hovered, active) のヘッダ色を base から作る。"""

    normal = base

    def _tint_towards_white(
        rgba: tuple[float, float, float, float],
        *,
        t: float,
        alpha_add: float,
    ) -> tuple[float, float, float, float]:
        t = _clamp01(float(t))
        r, g, b, a = rgba
        return (
            _clamp01(r * (1.0 - t) + 1.0 * t),
            _clamp01(g * (1.0 - t) + 1.0 * t),
            _clamp01(b * (1.0 - t) + 1.0 * t),
            _clamp01(a + float(alpha_add)),
        )

    # hover/active の色は base から自動導出する（白方向へ補間 + alpha を少し増やす）。
    hovered = _tint_towards_white(base, t=0.12, alpha_add=0.08)
    active = _tint_towards_white(base, t=0.22, alpha_add=0.14)
    return normal, hovered, active


def _header_kind_for_group_id(group_id: tuple[str, object]) -> str | None:
    """GroupBlock.group_id からヘッダ種別（style/primitive/effect）を返す。"""

    group_type = str(group_id[0])
    if group_type == "effect_chain":
        return "effect"
    if group_type in {"style", "primitive"}:
        return group_type
    return None


def _collapse_key_for_block(block: GroupBlock) -> str | None:
    """ブロックの折りたたみ永続キーを返す。"""

    group_type = str(block.group_id[0])
    if group_type == "style":
        return "style:global"
    if group_type == "effect_chain":
        chain_id = str(block.group_id[1])
        return f"effect_chain:{chain_id}"
    if group_type == "primitive":
        if not block.items:
            return None
        row0 = block.items[0].row
        return f"primitive:{row0.op}:{row0.site_id}"
    return None


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
    ui_min: float | int | None,
    ui_max: float | int | None,
) -> tuple[bool, float | int | None, float | int | None]:
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
    row: ParameterRow,
    rules,
    cc_key: int | tuple[int | None, int | None, int | None] | None,
    override: bool,
    cc_key_width: int,
    width_spacer: int,
    midi_learn_state: MidiLearnState | None,
    midi_last_cc_change: tuple[int, int] | None,
) -> tuple[bool, int | tuple[int | None, int | None, int | None] | None, bool]:
    """cc/override 列を描画し、(changed, cc_key, override) を返す。"""

    imgui.table_set_column_index(3)

    changed_any = False

    if rules.cc_key == "none":
        clicked_override = False
        if rules.show_override:
            clicked_override, override = imgui.checkbox("##override", bool(override))
            if clicked_override:
                changed_any = True
        return changed_any, cc_key, bool(override)

    def _set_scalar(current: object, value: int | None) -> int | None:
        if value is None:
            return None
        return int(value)

    def _set_component(
        current: object, *, index: int, value: int | None
    ) -> tuple[int | None, int | None, int | None] | None:
        if isinstance(current, tuple):
            a, b, c = current
        else:
            a, b, c = None, None, None
        items = [a, b, c]
        items[int(index)] = None if value is None else int(value)
        out = (items[0], items[1], items[2])
        return None if out == (None, None, None) else out

    def _key_for_row(target_row: ParameterRow) -> ParameterKey:
        return ParameterKey(
            op=target_row.op,
            site_id=target_row.site_id,
            arg=target_row.arg,
        )

    def _is_active(*, key: ParameterKey, component: int | None) -> bool:
        state = midi_learn_state
        if state is None:
            return False
        return state.active_target == key and state.active_component == component

    def _enter_learn(*, key: ParameterKey, component: int | None) -> None:
        state = midi_learn_state
        if state is None:
            return
        state.active_target = key
        state.active_component = component
        state.last_seen_cc_seq = (
            0 if midi_last_cc_change is None else int(midi_last_cc_change[0])
        )

    def _cancel_learn() -> None:
        state = midi_learn_state
        if state is None:
            return
        state.active_target = None
        state.active_component = None

    key = _key_for_row(row)

    if rules.cc_key == "int3":
        button_width = float(cc_key_width * 1.6)

        current_tuple = cc_key if isinstance(cc_key, tuple) else (None, None, None)
        for i in range(3):
            component_cc = current_tuple[i]
            active = _is_active(key=key, component=int(i))

            if (
                active
                and midi_learn_state is not None
                and midi_last_cc_change is not None
            ):
                seq, learned_cc = midi_last_cc_change
                if int(seq) > int(midi_learn_state.last_seen_cc_seq):
                    cc_key = _set_component(cc_key, index=int(i), value=int(learned_cc))
                    midi_learn_state.last_seen_cc_seq = int(seq)
                    _cancel_learn()
                    changed_any = True
                    current_tuple = (
                        cc_key if isinstance(cc_key, tuple) else (None, None, None)
                    )
                    component_cc = current_tuple[i]
                    active = False

            if active:
                label_text = "..."
            elif component_cc is None:
                label_text = ""
            else:
                label_text = str(int(component_cc))

            clicked = imgui.button(f"{label_text}##cc_learn_{i}", button_width)
            if clicked:
                # 新規操作で learn は 1 件に限定する（別ターゲットがあればキャンセル）。
                if (
                    midi_learn_state is not None
                    and midi_learn_state.active_target is not None
                    and not active
                ):
                    _cancel_learn()

                if active:
                    _cancel_learn()
                elif component_cc is not None:
                    cc_key = _set_component(cc_key, index=int(i), value=None)
                    changed_any = True
                else:
                    _enter_learn(key=key, component=int(i))

            if i < 2:
                imgui.same_line(0.0, float(width_spacer))

    else:
        current_cc = cc_key if isinstance(cc_key, int) else None
        active = _is_active(key=key, component=None)

        if active and midi_learn_state is not None and midi_last_cc_change is not None:
            seq, learned_cc = midi_last_cc_change
            if int(seq) > int(midi_learn_state.last_seen_cc_seq):
                cc_key = _set_scalar(cc_key, int(learned_cc))
                midi_learn_state.last_seen_cc_seq = int(seq)
                _cancel_learn()
                changed_any = True
                current_cc = cc_key if isinstance(cc_key, int) else None
                active = False

        if active:
            label_text = "Listening..."
        elif current_cc is None:
            label_text = ""
        else:
            label_text = str(int(current_cc))

        clicked = imgui.button(
            f"{label_text}##cc_learn", float(cc_key_width * 1.8) * 0.88
        )
        if clicked:
            if (
                midi_learn_state is not None
                and midi_learn_state.active_target is not None
                and not active
            ):
                _cancel_learn()

            if active:
                _cancel_learn()
            elif current_cc is not None:
                cc_key = None
                changed_any = True
            else:
                _enter_learn(key=key, component=None)

    clicked_override = False
    if rules.show_override:
        imgui.same_line(0.0, width_spacer)
        clicked_override, override = imgui.checkbox("##override", bool(override))
        if clicked_override:
            changed_any = True

    return changed_any, cc_key, bool(override)


def render_parameter_row_4cols(
    row: ParameterRow,
    *,
    visible_label: str | None = None,
    midi_learn_state: MidiLearnState | None = None,
    midi_last_cc_change: tuple[int, int] | None = None,
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
            row=row,
            rules=rules,
            cc_key=cc_key,
            override=bool(override),
            cc_key_width=cc_key_width,
            width_spacer=width_spacer,
            midi_learn_state=midi_learn_state,
            midi_last_cc_change=midi_last_cc_change,
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
    midi_learn_state: MidiLearnState | None = None,
    midi_last_cc_change: tuple[int, int] | None = None,
    collapsed_headers: set[str] | None = None,
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
                collapse_key = (
                    None if collapsed_headers is None else _collapse_key_for_block(block)
                )
                if collapsed_headers is not None and collapse_key is not None:
                    want_open = collapse_key not in collapsed_headers
                    set_next_item_open = getattr(imgui, "set_next_item_open", None)
                    if callable(set_next_item_open):
                        cond_always = getattr(imgui, "ALWAYS", None)
                        try:
                            if cond_always is None:
                                set_next_item_open(bool(want_open))
                            else:
                                set_next_item_open(bool(want_open), cond_always)
                        except TypeError:
                            set_next_item_open(bool(want_open))

                color_count = 0
                header_kind = _header_kind_for_group_id(block.group_id)
                if header_kind is not None:
                    base_rgba255 = GROUP_HEADER_BASE_COLORS_RGBA.get(header_kind)
                    if base_rgba255 is not None:
                        base = _rgba01_from_rgba255(base_rgba255)
                        normal, hovered, active = _derive_header_colors(base)
                        imgui.push_style_color(imgui.COLOR_HEADER, *normal)
                        imgui.push_style_color(imgui.COLOR_HEADER_HOVERED, *hovered)
                        imgui.push_style_color(imgui.COLOR_HEADER_ACTIVE, *active)
                        color_count = 3
                try:
                    group_open, _visible = imgui.collapsing_header(
                        f"{block.header}##group_header",
                        None,
                        flags=imgui.TREE_NODE_DEFAULT_OPEN,
                    )
                finally:
                    if color_count:
                        imgui.pop_style_color(color_count)

                if collapsed_headers is not None and collapse_key is not None:
                    if group_open:
                        collapsed_headers.discard(collapse_key)
                    else:
                        collapsed_headers.add(collapse_key)

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
                        midi_learn_state=midi_learn_state,
                        midi_last_cc_change=midi_last_cc_change,
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
