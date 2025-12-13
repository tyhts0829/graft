# どこで: `src/app/parameter_gui/store_bridge.py`。
# 何を: ParamStore snapshot と UI 行モデル（ParameterRow）の差分を反映する。
# なぜ: 「描画」と「永続状態の更新」を分離し、依存方向を単純化するため。

from __future__ import annotations

from collections.abc import Mapping

from src.core.primitive_registry import primitive_registry
from src.parameters.key import ParameterKey
from src.parameters.meta import ParamMeta
from src.parameters.store import ParamStore
from src.parameters.view import ParameterRow, rows_from_snapshot, update_state_from_ui

from .labeling import primitive_header_display_names_from_snapshot
from .table import COLUMN_WEIGHTS_DEFAULT, render_parameter_table


def _row_identity(row: ParameterRow) -> tuple[str, int, str]:
    """store snapshot と突き合わせるための行識別子（op, ordinal, arg）を返す。"""

    return row.op, int(row.ordinal), row.arg


def _apply_updated_rows_to_store(
    store: ParamStore,
    snapshot: Mapping[ParameterKey, tuple[ParamMeta, object, int, str | None]],
    rows_before: list[ParameterRow],
    rows_after: list[ParameterRow],
) -> None:
    """rows の変更を ParamStore に反映する。

    - ui_min/ui_max の変更は meta に反映する
    - ui_value/override/cc_key の変更は `update_state_from_ui` 経由で反映する
    """

    entry_by_identity: dict[tuple[str, int, str], tuple[ParameterKey, ParamMeta]] = {}
    for key, (meta, _state, ordinal, _label) in snapshot.items():
        entry_by_identity[(key.op, int(ordinal), key.arg)] = (key, meta)

    for before, after in zip(rows_before, rows_after, strict=True):
        key, meta = entry_by_identity[_row_identity(before)]
        effective_meta = meta

        if after.ui_min != before.ui_min or after.ui_max != before.ui_max:
            effective_meta = ParamMeta(
                kind=meta.kind,
                ui_min=after.ui_min,
                ui_max=after.ui_max,
                choices=meta.choices,
            )
            store.set_meta(key, effective_meta)

        if (
            after.ui_value != before.ui_value
            or after.override != before.override
            or after.cc_key != before.cc_key
        ):
            update_state_from_ui(
                store,
                key,
                after.ui_value,
                meta=effective_meta,
                override=after.override,
                cc_key=after.cc_key,
            )


def render_store_parameter_table(
    store: ParamStore,
    *,
    column_weights: tuple[float, float, float, float] = COLUMN_WEIGHTS_DEFAULT,
) -> bool:
    """ParamStore の snapshot を 4 列テーブルとして描画し、変更を store に反映する。"""

    snapshot = store.snapshot()
    primitive_header_by_group = primitive_header_display_names_from_snapshot(
        snapshot,
        is_primitive_op=lambda op: op in primitive_registry,
    )
    rows_before = rows_from_snapshot(snapshot)
    changed, rows_after = render_parameter_table(
        rows_before,
        column_weights=column_weights,
        primitive_header_by_group=primitive_header_by_group,
    )
    if changed:
        _apply_updated_rows_to_store(store, snapshot, rows_before, rows_after)
    return changed
