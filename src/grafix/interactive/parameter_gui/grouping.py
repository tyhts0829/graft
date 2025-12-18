# どこで: `src/grafix/interactive/parameter_gui/grouping.py`。
# 何を: Parameter GUI の「グルーピング（Style/Primitive/Effect など）と表示ラベル決定」を純粋関数として提供する。
# なぜ: `table.py` の分岐を薄くし、挙動を unit test で担保しやすくするため。

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from grafix.core.parameters.layer_style import LAYER_STYLE_OP
from grafix.core.parameters.style import STYLE_OP
from grafix.core.parameters.view import ParameterRow

from .labeling import format_layer_style_row_label, format_param_row_label


@dataclass(frozen=True, slots=True)
class GroupInfo:
    """GUI のヘッダ行と、行ラベルの決定結果。"""

    group_id: tuple[str, Any]
    header_id: str
    header: str | None
    visible_label: str


def group_info_for_row(
    row: ParameterRow,
    *,
    primitive_header_by_group: Mapping[tuple[str, int], str] | None = None,
    layer_style_name_by_site_id: Mapping[str, str] | None = None,
    effect_chain_header_by_id: Mapping[str, str] | None = None,
    step_info_by_site: Mapping[tuple[str, str], tuple[str, int]] | None = None,
    effect_step_ordinal_by_site: Mapping[tuple[str, str], int] | None = None,
) -> GroupInfo:
    """行から group/header/visible_label を決定して返す。"""

    # --- Style（global + layer_style） ---
    if row.op in {STYLE_OP, LAYER_STYLE_OP}:
        if row.op == STYLE_OP:
            visible_label = str(row.arg)
        else:
            layer_name = (
                "layer"
                if layer_style_name_by_site_id is None
                else str(layer_style_name_by_site_id.get(row.site_id, "layer"))
            )
            visible_label = format_layer_style_row_label(
                layer_name, int(row.ordinal), row.arg
            )
        return GroupInfo(
            group_id=("style", "global"),
            header_id="style",
            header="Style",
            visible_label=visible_label,
        )

    # 現在行が effect ステップに紐づくかどうか（(op, site_id) → (chain_id, step_index)）。
    step_key = (row.op, row.site_id)
    step_info = None if step_info_by_site is None else step_info_by_site.get(step_key)

    # --- Effect（chain_id でグループ化） ---
    if step_info is not None:
        chain_id, _step_index = step_info
        header = (
            None
            if effect_chain_header_by_id is None
            else effect_chain_header_by_id.get(chain_id)
        )
        step_ordinal = int(row.ordinal)
        if effect_step_ordinal_by_site is not None:
            step_ordinal = int(effect_step_ordinal_by_site.get(step_key, step_ordinal))
        visible_label = format_param_row_label(row.op, step_ordinal, row.arg)
        return GroupInfo(
            group_id=("effect_chain", chain_id),
            header_id=f"effect_chain:{chain_id}",
            header=header,
            visible_label=visible_label,
        )

    # --- Primitive（(op, ordinal) でグループ化） ---
    group_key = (row.op, int(row.ordinal))
    header = (
        None
        if primitive_header_by_group is None
        else primitive_header_by_group.get(group_key)
    )
    visible_label = format_param_row_label(row.op, int(row.ordinal), row.arg)
    return GroupInfo(
        group_id=("primitive", group_key),
        header_id=f"primitive:{group_key[0]}#{group_key[1]}",
        header=header,
        visible_label=visible_label,
    )
