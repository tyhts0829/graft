"""
Purpose:
    table rendererのimmutable edit intentを、ParamStore commandと履歴transactionへcommitする。
Use when:
    row編集、MIDI割当、collapse、effect order、またはUndo単位を変更する場合。
Constraints:
    - 描画に使ったtable viewと返却rowsの対応を検証してからmutationする。
    - 責務の異なるeditを適切なhistory境界へ分け、store private stateを直接変更しない。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, replace

from grafix.core.parameters.edit_commands import ParameterEdit, apply_parameter_edits
from grafix.core.parameters.effect_order_ops import move_effect_step, reset_effect_order
from grafix.core.parameters.favorites import favorite_parameter_key_set
from grafix.core.parameters.history import ParamStoreHistory
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.snapshot_ops import ParamSnapshot, store_snapshot
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.view import ParameterRow, rows_from_snapshot

from .midi_learn import MidiLearnState
from .session_state import WidgetSessionState
from .table import (
    EffectOrderCommand,
    TableEdits,
    TableRenderInput,
    parameter_group_collapse_keys,
    render_parameter_table,
)
from .table_view import ParameterTableView


@dataclass(frozen=True, slots=True)
class TableCommitResult:
    """renderer output と store commit の結果。"""

    changed: bool
    edits: TableEdits

    @property
    def midi_learn_state(self) -> MidiLearnState | None:
        """次 frame に渡す immutable MIDI learn state を返す。"""

        return self.edits.midi_learn_state


def _apply_updated_rows_to_store(
    store: ParamStore,
    snapshot: ParamSnapshot,
    rows_before: Sequence[ParameterRow],
    rows_after: Sequence[ParameterRow],
) -> bool:
    """rows の変更を一つの core command として ParamStore に反映する。

    - ui_min/ui_max の変更は最終 meta command に反映する
    - ui_value/override/cc_key/favorite は一つの batch command にまとめる
    """

    def _cc_set(
        cc_key: int | tuple[int | None, int | None, int | None] | None,
    ) -> set[int]:
        # cc_key は scalar(int) または vec3/rgb 用の (a,b,c) を取り得る。
        # 「割当解除（CC が減った）」判定を set 差分でシンプルにするため、集合へ正規化する。
        #
        # - None            : 未割当（空集合）
        # - int             : {cc}
        # - (a,b,c)         : {a,b,c}（None 成分は除外）
        #
        # ここで例外処理を厚くしないのは、
        # cc_key の型は update_state_from_ui / UI 側で既に正規化されている前提のため。
        if cc_key is None:
            return set()
        if isinstance(cc_key, int):
            return {cc_key}
        return {v for v in cc_key if v is not None}

    reset_font_index_for: set[tuple[str, str]] = set()
    commands: dict[ParameterKey, ParameterEdit] = {}

    for before, after in zip(rows_before, rows_after, strict=True):
        # renderer は未変更 row の identity を維持する。changed frame でも
        # ほぼ全行を読み直さず、実際に更新された row だけ store へ反映する。
        if before is after or before == after:
            continue
        key = ParameterKey(
            op=before.op,
            site_id=before.site_id,
            arg=before.arg,
        )
        entry = snapshot.get(key)
        if entry is None:
            continue
        meta = entry[0]
        effective_meta = meta

        if after.ui_min != before.ui_min or after.ui_max != before.ui_max:
            effective_meta = replace(
                meta,
                ui_min=after.ui_min,
                ui_max=after.ui_max,
            )

        ui_value = after.ui_value
        override = bool(after.override)
        if (
            after.ui_value != before.ui_value
            or after.override != before.override
            or after.cc_key != before.cc_key
        ):
            cc_removed = False
            if after.cc_key != before.cc_key:
                before_cc = _cc_set(before.cc_key)
                after_cc = _cc_set(after.cc_key)
                removed = before_cc - after_cc
                added = after_cc - before_cc
                cc_removed = bool(removed) and not bool(added)

            baked_effective = (
                store.last_effective_value(key) if cc_removed and not after.reset_to_code else None
            )
            if baked_effective is not None:
                ui_value = baked_effective
                override = True

        commands[key] = ParameterEdit(
            key=key,
            meta=effective_meta,
            ui_value=ui_value,
            override=override,
            cc_key=after.cc_key,
            favorite=bool(after.favorite),
        )

        if (
            key.op == "text"
            and key.arg == "font"
            and after.ui_value != before.ui_value
            and str(after.ui_value).strip().lower().endswith(".ttc")
        ):
            reset_font_index_for.add((key.op, key.site_id))

    for op, site_id in sorted(reset_font_index_for):
        font_index_key = ParameterKey(
            op=op,
            site_id=site_id,
            arg="font_index",
        )
        entry = snapshot.get(font_index_key)
        if entry is None:
            continue
        font_index_meta, font_index_state, _ordinal, _label = entry
        commands[font_index_key] = ParameterEdit(
            key=font_index_key,
            meta=font_index_meta,
            ui_value=0,
            override=True,
            cc_key=font_index_state.cc_key,
            favorite=font_index_key in favorite_parameter_key_set(store),
        )

    return bool(apply_parameter_edits(store, tuple(commands.values())))


def apply_effect_order_command(
    store: ParamStore,
    command: EffectOrderCommand,
) -> bool:
    """renderer command を core の effect order operation へ渡す。"""

    if command.kind == "reset":
        return reset_effect_order(store, chain_id=command.chain_id)
    if command.source is None or command.target is None or command.placement is None:
        raise ValueError("move command requires source, target, and placement")
    return move_effect_step(
        store,
        chain_id=command.chain_id,
        source=command.source,
        target=command.target,
        placement=command.placement,
    )


def set_all_parameter_groups_collapsed(
    store: ParamStore,
    table_view: ParameterTableView,
    *,
    collapsed: bool,
) -> bool:
    """現在の parameter group を一括で折りたたみ、または展開する。"""

    if not isinstance(collapsed, bool):
        raise TypeError("collapsed must be a bool")

    model = table_view.model
    collapse_keys = parameter_group_collapse_keys(
        list(model.rows),
        group_layout=model.group_layout,
    )
    return bool(store.set_all_collapsed(collapse_keys, collapsed=collapsed))


def clear_all_midi_assignments(
    store: ParamStore,
    *,
    history: ParamStoreHistory | None = None,
) -> bool:
    """すべての MIDI CC 割当を、一つの履歴単位として解除する。"""

    snapshot = store_snapshot(store)
    rows_before = rows_from_snapshot(snapshot)
    if not any(row.cc_key is not None for row in rows_before):
        return False

    rows_after = [row if row.cc_key is None else replace(row, cc_key=None) for row in rows_before]
    transaction = (
        history.transaction(source="clear_all_midi") if history is not None else nullcontext()
    )
    with transaction:
        return _apply_updated_rows_to_store(store, snapshot, rows_before, rows_after)


def _rows_for_table_view(
    table_view: ParameterTableView,
) -> tuple[tuple[ParameterRow, ...], tuple[ParameterRow, ...]]:
    """renderer 用全行と、layout と同順の visible 行を返す。"""

    model = table_view.model
    render_rows = list(model.rows)
    view_rows: list[ParameterRow] = []
    for index in table_view.visible_row_indices:
        row = model.rows[index]
        favorite = model.keys[index] in table_view.favorite_keys
        visible_row = row if bool(row.favorite) == favorite else replace(row, favorite=favorite)
        render_rows[index] = visible_row
        view_rows.append(visible_row)
    return tuple(render_rows), tuple(view_rows)


def commit_table_edits(
    store: ParamStore,
    *,
    table_view: ParameterTableView,
    edits: TableEdits,
    history: ParamStoreHistory | None = None,
) -> bool:
    """renderer の immutable result を責務別の history 単位で commit する。"""

    if not isinstance(edits, TableEdits):
        raise TypeError("edits must be a TableEdits")
    _render_rows, rows_before = _rows_for_table_view(table_view)
    if len(rows_before) != len(edits.rows):
        raise ValueError("TableEdits.rows does not match the rendered layout")

    changed_any = False
    changed_pairs = tuple(
        (before, after)
        for before, after in zip(rows_before, edits.rows, strict=True)
        if before is not after and before != after
    )
    if changed_pairs:
        changed_keys = tuple(
            ParameterKey(row.op, row.site_id, row.arg) for row, _after in changed_pairs
        )
        midi_changed = any(before.cc_key != after.cc_key for before, after in changed_pairs)
        discrete = midi_changed or len(changed_pairs) > 1
        if history is not None and discrete:
            history.break_coalescing()
        source: object = (
            ("parameter_midi", changed_keys)
            if midi_changed
            else (
                ("parameter_table", changed_keys[0])
                if len(changed_keys) == 1
                else ("parameter_table_multi", changed_keys)
            )
        )
        transaction = (
            history.transaction(source=source, patch=True) if history is not None else nullcontext()
        )
        with transaction:
            changed_any = _apply_updated_rows_to_store(
                store,
                table_view.model.snapshot,
                rows_before,
                edits.rows,
            )
        if history is not None and discrete:
            history.break_coalescing()

    collapsed_before = store.collapsed_headers()
    if edits.collapsed_headers != collapsed_before:
        if history is not None:
            history.break_coalescing()
        collapse_transaction = (
            history.transaction(source="parameter_table_collapse", patch=True)
            if history is not None
            else nullcontext()
        )
        with collapse_transaction:
            collapse_changed = store.replace_collapsed_headers(edits.collapsed_headers)
        changed_any = collapse_changed or changed_any
        if history is not None:
            history.break_coalescing()

    for command in edits.effect_order_commands:
        if history is not None:
            history.break_coalescing()
        effect_transaction = (
            history.transaction(
                source=("effect_order", command.chain_id),
                patch=False,
            )
            if history is not None
            else nullcontext()
        )
        with effect_transaction:
            effect_changed = apply_effect_order_command(store, command)
        changed_any = effect_changed or changed_any
        if history is not None:
            history.break_coalescing()

    return changed_any


def render_store_parameter_table(
    store: ParamStore,
    *,
    table_view: ParameterTableView,
    widget_state: WidgetSessionState,
    metric_scale: float | None = None,
    midi_learn_state: MidiLearnState | None = None,
    midi_last_cc_change: tuple[int, int] | None = None,
    on_help_row: Callable[[ParameterRow, bool], None] | None = None,
    history: ParamStoreHistory | None = None,
) -> TableCommitResult:
    """store snapshot を描画し、返された edit を core command で commit する。"""

    model = table_view.model
    render_rows, _view_rows = _rows_for_table_view(table_view)

    runtime = store.runtime_view()
    edits = render_parameter_table(
        TableRenderInput(
            group_layout=table_view.group_layout,
            model_rows=render_rows,
            catalog=model.catalog,
            metric_scale=metric_scale,
            step_info_by_site=model.step_info_by_site,
            effect_chain_state_by_id=table_view.effect_chain_state_by_id,
            last_effective_by_key=runtime.last_effective_by_key,
            last_source_by_key=runtime.last_source_by_key,
            raw_label_by_site=model.raw_label_by_site,
            midi_learn_state=midi_learn_state,
            midi_last_cc_change=midi_last_cc_change,
            collapsed_headers=store.collapsed_headers(),
        ),
        widget_state=widget_state,
        on_help_row=on_help_row,
    )
    changed = commit_table_edits(
        store,
        table_view=table_view,
        edits=edits,
        history=history,
    )
    return TableCommitResult(changed=changed, edits=edits)


__all__ = [
    "TableCommitResult",
    "apply_effect_order_command",
    "clear_all_midi_assignments",
    "commit_table_edits",
    "render_store_parameter_table",
    "set_all_parameter_groups_collapsed",
]
