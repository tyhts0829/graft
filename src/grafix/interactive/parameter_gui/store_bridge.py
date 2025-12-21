# どこで: `src/grafix/interactive/parameter_gui/store_bridge.py`。
# 何を: ParamStore snapshot と UI 行モデル（ParameterRow）の差分を反映する。
# なぜ: 「描画」と「永続状態の更新」を分離し、依存方向を単純化するため。

from __future__ import annotations

from collections.abc import Mapping

from grafix.core.effect_registry import effect_registry
from grafix.core.primitive_registry import primitive_registry
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.layer_style import LAYER_STYLE_OP
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.style import STYLE_OP
from grafix.core.parameters.store_ops import store_snapshot_for_gui
from grafix.core.parameters.view import ParameterRow, rows_from_snapshot, update_state_from_ui

from .labeling import primitive_header_display_names_from_snapshot
from .labeling import (
    effect_chain_header_display_names_from_snapshot,
    effect_step_ordinals_by_site,
)
from .midi_learn import MidiLearnState
from .table import COLUMN_WEIGHTS_DEFAULT, render_parameter_table


def _row_identity(row: ParameterRow) -> tuple[str, int, str]:
    """store snapshot と突き合わせるための行識別子（op, ordinal, arg）を返す。"""

    return row.op, int(row.ordinal), row.arg


def _order_rows_for_display(
    rows: list[ParameterRow],
    *,
    step_info_by_site: Mapping[tuple[str, str], tuple[str, int]],
    chain_ordinal_by_id: Mapping[str, int],
) -> list[ParameterRow]:
    """GUI 表示順に並び替えた rows を返す。"""

    style_global_rows: list[ParameterRow] = []
    style_layer_rows: list[ParameterRow] = []
    primitive_rows: list[ParameterRow] = []
    effect_rows: list[ParameterRow] = []
    other_rows: list[ParameterRow] = []
    for row in rows:
        if row.op == STYLE_OP:
            style_global_rows.append(row)
        elif row.op == LAYER_STYLE_OP:
            style_layer_rows.append(row)
        elif row.op in primitive_registry:
            primitive_rows.append(row)
        elif row.op in effect_registry:
            effect_rows.append(row)
        else:
            other_rows.append(row)

    # Style（global）は固定の表示順に寄せる（background → thickness → line_color）。
    style_order = {
        "background_color": 0,
        "global_thickness": 1,
        "global_line_color": 2,
    }
    style_global_rows.sort(key=lambda r: (style_order.get(r.arg, 999), r.arg))

    # Style（layer）は layer ordinal 順に、line_thickness → line_color の順で出す。
    layer_style_order = {"line_thickness": 0, "line_color": 1}
    style_layer_rows.sort(
        key=lambda r: (int(r.ordinal), layer_style_order.get(r.arg, 999), r.arg)
    )

    def _effect_sort_key(row: ParameterRow) -> tuple[int, int, str]:
        info = step_info_by_site.get((row.op, row.site_id))
        if info is None:
            return (10**9, 10**9, row.arg)
        chain_id, step_index = info
        chain_ordinal = int(chain_ordinal_by_id.get(chain_id, 0))
        return (chain_ordinal, int(step_index), row.arg)

    # Effect 行を “チェーン順→ステップ順→arg” で並び替える。
    effect_rows.sort(key=_effect_sort_key)

    # 最終的な表示順: style → primitive → effect → other
    return style_global_rows + style_layer_rows + primitive_rows + effect_rows + other_rows


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

    reset_font_index_for: set[tuple[str, int]] = set()

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

        if (
            key.op == "text"
            and key.arg == "font"
            and after.ui_value != before.ui_value
            and str(after.ui_value).strip().lower().endswith(".ttc")
        ):
            reset_font_index_for.add((str(key.op), int(after.ordinal)))

    for op, ordinal in sorted(reset_font_index_for):
        entry = entry_by_identity.get((str(op), int(ordinal), "font_index"))
        if entry is None:
            continue
        font_index_key, font_index_meta = entry
        update_state_from_ui(
            store,
            font_index_key,
            0,
            meta=font_index_meta,
            override=True,
        )


def render_store_parameter_table(
    store: ParamStore,
    *,
    column_weights: tuple[float, float, float, float] = COLUMN_WEIGHTS_DEFAULT,
    midi_learn_state: MidiLearnState | None = None,
    midi_last_cc_change: tuple[int, int] | None = None,
) -> bool:
    """ParamStore の snapshot を 4 列テーブルとして描画し、変更を store に反映する。"""

    # --- 1) フレーム時点の ParamStore を “読む” ---
    #
    # snapshot は (key -> (meta, state, ordinal, label)) の辞書。
    # - key: (op, site_id, arg)
    # - ordinal: GUI 用の連番（primitive/effect どちらも op ごとの連番）
    # - label: G(name=...) / E(name=...) / L(name=...) が付与した表示名（op, site_id 単位）
    snapshot = store_snapshot_for_gui(store)

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
    step_info_by_site = store.effects.step_info_by_site()
    # chain_id ごとの ordinal は GUI の “effect#N” デフォルト名に使う。
    chain_ordinal_by_id = store.effects.chain_ordinals()
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

    # 最終的な表示順: style → primitive → effect → other
    # ※ この rows_before の順番が、そのまま GUI の並び順になる。
    rows_before = _order_rows_for_display(
        rows_before_raw,
        step_info_by_site=step_info_by_site,
        chain_ordinal_by_id=chain_ordinal_by_id,
    )

    layer_style_name_by_site_id: dict[str, str] = {}
    for key, (_meta, _state, _ordinal, label) in snapshot.items():
        if key.op != LAYER_STYLE_OP:
            continue
        site_id = str(key.site_id)
        if site_id in layer_style_name_by_site_id:
            continue
        layer_style_name_by_site_id[site_id] = str(label) if label else "layer"

    # --- 6) 描画（imgui）→ UI 入力を反映した更新 rows を受け取る ---
    # render_parameter_table は
    # - (グループ境界でヘッダ行を描画)
    # - 各行の UI を描画し、更新後の ParameterRow を返す
    # という純粋 “ビュー” 相当の関数。
    changed, rows_after = render_parameter_table(
        rows_before,
        column_weights=column_weights,
        primitive_header_by_group=primitive_header_by_group,
        layer_style_name_by_site_id=layer_style_name_by_site_id,
        effect_chain_header_by_id=effect_chain_header_by_id,
        step_info_by_site=step_info_by_site,
        effect_step_ordinal_by_site=effect_step_ordinal_by_site,
        midi_learn_state=midi_learn_state,
        midi_last_cc_change=midi_last_cc_change,
    )

    # --- 7) 変更があった場合だけ store へ反映 ---
    # rows_before/rows_after は 1:1 対応している前提（render_parameter_table が維持する）。
    if changed:
        _apply_updated_rows_to_store(store, snapshot, rows_before, rows_after)
    return changed
