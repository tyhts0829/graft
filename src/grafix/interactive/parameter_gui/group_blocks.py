# どこで: `src/grafix/interactive/parameter_gui/group_blocks.py`。
# 何を: `ParameterRow` 列を “連続する group” ごとのブロックへ分割する純粋関数を提供する。
# なぜ: `collapsing_header` をテーブル外に出して全幅表示するために、描画単位（ブロック）を先に組み立てたいから。

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping

from grafix.core.parameters.view import ParameterRow

from .grouping import group_info_for_row


@dataclass(frozen=True, slots=True)
class GroupBlockItem:
    """グループ内の 1 行ぶんの描画情報。"""

    row: ParameterRow
    visible_label: str


@dataclass(frozen=True, slots=True)
class GroupBlock:
    """連続する group（Style/Primitive/Effect chain）を 1 ブロックとして表す。"""

    group_id: tuple[str, object]
    header_id: str
    header: str | None
    items: list[GroupBlockItem]


def group_blocks_from_rows(
    rows: list[ParameterRow],
    *,
    primitive_header_by_group: Mapping[tuple[str, int], str] | None = None,
    layer_style_name_by_site_id: Mapping[str, str] | None = None,
    effect_chain_header_by_id: Mapping[str, str] | None = None,
    step_info_by_site: Mapping[tuple[str, str], tuple[str, int]] | None = None,
    effect_step_ordinal_by_site: Mapping[tuple[str, str], int] | None = None,
) -> list[GroupBlock]:
    """rows を “連続する group_id” ごとのブロックへ分割して返す。"""

    out: list[GroupBlock] = []

    current_group_id: tuple[str, object] | None = None
    current_header_id: str | None = None
    current_header: str | None = None
    current_items: list[GroupBlockItem] = []

    def _flush() -> None:
        nonlocal current_group_id, current_header_id, current_header, current_items
        if current_group_id is None or current_header_id is None:
            return
        out.append(
            GroupBlock(
                group_id=current_group_id,
                header_id=current_header_id,
                header=current_header,
                items=current_items,
            )
        )
        current_group_id = None
        current_header_id = None
        current_header = None
        current_items = []

    for row in rows:
        info = group_info_for_row(
            row,
            primitive_header_by_group=primitive_header_by_group,
            layer_style_name_by_site_id=layer_style_name_by_site_id,
            effect_chain_header_by_id=effect_chain_header_by_id,
            step_info_by_site=step_info_by_site,
            effect_step_ordinal_by_site=effect_step_ordinal_by_site,
        )

        if info.group_id != current_group_id:
            _flush()
            current_group_id = info.group_id
            current_header_id = info.header_id
            current_header = info.header
            current_items = []

        current_items.append(
            GroupBlockItem(
                row=row,
                visible_label=str(info.visible_label),
            )
        )

    _flush()
    return out
