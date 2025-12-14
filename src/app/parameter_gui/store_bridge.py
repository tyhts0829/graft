# どこで: `src/app/parameter_gui/store_bridge.py`。
# 何を: ParamStore snapshot と UI 行モデル（ParameterRow）の差分を反映する。
# なぜ: 「描画」と「永続状態の更新」を分離し、依存方向を単純化するため。

from __future__ import annotations

from collections.abc import Mapping

from src.core.effect_registry import effect_registry
from src.core.primitive_registry import primitive_registry
from src.parameters.key import ParameterKey
from src.parameters.meta import ParamMeta
from src.parameters.store import ParamStore
from src.parameters.style import STYLE_OP
from src.parameters.view import ParameterRow, rows_from_snapshot, update_state_from_ui

from .labeling import primitive_header_display_names_from_snapshot
from .labeling import (
    effect_chain_header_display_names_from_snapshot,
    effect_step_ordinals_by_site,
)
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

    # --- 1) フレーム時点の ParamStore を “読む” ---
    #
    # snapshot は (key -> (meta, state, ordinal, label)) の辞書。
    # - key: (op, site_id, arg)
    # - ordinal: GUI 用の連番（primitive/effect どちらも op ごとの連番）
    # - label: G(name=...) / E(name=...) が付与した表示名（op, site_id 単位）
    snapshot = store.snapshot()

    # --- 2) Primitive のヘッダ表示名（G(name=...)）を解決 ---
    # snapshot の label を “Primitive グループ” (op, ordinal) へ対応付ける。
    # 同名衝突は表示専用に name#1/#2 を付与して区別する（永続化ラベルは変更しない）。
    primitive_header_by_group = primitive_header_display_names_from_snapshot(
        snapshot,
        is_primitive_op=lambda op: op in primitive_registry,
    )

    # --- 3) Effect のチェーン境界/順序を取得し、ヘッダ表示名（E(name=...)）を解決 ---
    #
    # effect は “チェーン” 単位のヘッダが欲しいので、
    # ParamStore が保持する (op, site_id) -> (chain_id, step_index) を参照する。
    # - chain_id: チェーン識別子（EffectBuilder 生成時の site_id）
    # - step_index: チェーン内のステップ順序（E.scale().rotate()... の順番）
    step_info_by_site = store.effect_steps()
    # chain_id ごとの ordinal は GUI の “effect#N” デフォルト名に使う。
    chain_ordinal_by_id = store.chain_ordinals()
    # チェーンヘッダの表示名:
    # - E(name=...) があればそれ
    # - 無ければ effect#N（N は chain_ordinal）
    # - 同名衝突は表示専用に name#1/#2
    effect_chain_header_by_id = effect_chain_header_display_names_from_snapshot(
        snapshot,
        step_info_by_site=step_info_by_site,
        chain_ordinal_by_id=chain_ordinal_by_id,
        is_effect_op=lambda op: op in effect_registry,
    )
    # “チェーン内の同一 op 連番”:
    # E.scale().rotate().scale() なら scale#1, rotate#1, scale#2 を作るための辞書。
    effect_step_ordinal_by_site = effect_step_ordinals_by_site(step_info_by_site)

    # --- 4) snapshot -> UI 行モデルへ変換（純粋関数）---
    # rows は元々 (op, ordinal, arg) でソートされるが、
    # Effect については「チェーン順/ステップ順」で見せたいので、ここで並び替える。
    rows_before_raw = rows_from_snapshot(snapshot)

    # --- 5) 表示順のために 3 つへ分類 ---
    # 1) style 行（最上段に出したい）
    # 2) primitive 群（先に見せたい）
    # 3) effect 群（チェーン順に並べたい）
    # 4) その他（将来の拡張/ユーザー定義 op など）
    style_rows: list[ParameterRow] = []
    primitive_rows: list[ParameterRow] = []
    effect_rows: list[ParameterRow] = []
    other_rows: list[ParameterRow] = []
    for row in rows_before_raw:
        if row.op == STYLE_OP:
            style_rows.append(row)
        elif row.op in primitive_registry:
            primitive_rows.append(row)
        elif row.op in effect_registry:
            effect_rows.append(row)
        else:
            other_rows.append(row)

    # Style は固定の表示順に寄せる（background → thickness → line_color）。
    style_order = {
        "background_color": 0,
        "global_thickness": 1,
        "global_line_color": 2,
    }
    style_rows.sort(key=lambda r: (style_order.get(r.arg, 999), r.arg))

    def _effect_sort_key(row: ParameterRow) -> tuple[int, int, str]:
        # effect ステップは chain_id / step_index を使って “チェーン順→ステップ順” に並べる。
        # 同一ステップ内の各 arg は、見やすさのため arg 名で安定ソートする。
        info = step_info_by_site.get((row.op, row.site_id))
        if info is None:
            # step 情報が無い行は末尾へ寄せる（= 観測されていない/旧データなど）。
            return (10**9, 10**9, row.arg)
        chain_id, step_index = info
        chain_ordinal = int(chain_ordinal_by_id.get(chain_id, 0))
        return (chain_ordinal, int(step_index), row.arg)

    # Effect 行を “チェーン順→ステップ順→arg” で並び替える。
    effect_rows.sort(key=_effect_sort_key)
    # 最終的な表示順: style → primitive → effect → other
    # ※ この rows_before の順番が、そのまま GUI の並び順になる。
    rows_before = style_rows + primitive_rows + effect_rows + other_rows

    # --- 6) 描画（imgui）→ UI 入力を反映した更新 rows を受け取る ---
    # render_parameter_table は
    # - (グループ境界でヘッダ行を描画)
    # - 各行の UI を描画し、更新後の ParameterRow を返す
    # という純粋 “ビュー” 相当の関数。
    changed, rows_after = render_parameter_table(
        rows_before,
        column_weights=column_weights,
        primitive_header_by_group=primitive_header_by_group,
        effect_chain_header_by_id=effect_chain_header_by_id,
        step_info_by_site=step_info_by_site,
        effect_step_ordinal_by_site=effect_step_ordinal_by_site,
    )

    # --- 7) 変更があった場合だけ store へ反映 ---
    # rows_before/rows_after は 1:1 対応している前提（render_parameter_table が維持する）。
    if changed:
        _apply_updated_rows_to_store(store, snapshot, rows_before, rows_after)
    return changed
