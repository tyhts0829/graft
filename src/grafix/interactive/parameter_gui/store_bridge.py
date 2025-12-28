# どこで: `src/grafix/interactive/parameter_gui/store_bridge.py`。
# 何を: ParamStore snapshot と UI 行モデル（ParameterRow）の差分を反映する。
# なぜ: 「描画」と「永続状態の更新」を分離し、依存方向を単純化するため。

from __future__ import annotations

import logging
from collections.abc import Mapping

from grafix.core.effect_registry import effect_registry
from grafix.core.primitive_registry import primitive_registry
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.layer_style import LAYER_STYLE_OP
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.meta_ops import set_meta
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.style import STYLE_OP
from grafix.core.parameters.snapshot_ops import store_snapshot_for_gui
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.core.parameters.view import ParameterRow, rows_from_snapshot

from .labeling import primitive_header_display_names_from_snapshot
from .labeling import (
    effect_chain_header_display_names_from_snapshot,
    effect_step_ordinals_by_site,
)
from .midi_learn import MidiLearnState
from .table import COLUMN_WEIGHTS_DEFAULT, render_parameter_table

_logger = logging.getLogger(__name__)


def _row_identity(row: ParameterRow) -> tuple[str, int, str]:
    """store snapshot と突き合わせるための行識別子（op, ordinal, arg）を返す。"""

    return row.op, int(row.ordinal), row.arg


def _order_rows_for_display(
    rows: list[ParameterRow],
    *,
    step_info_by_site: Mapping[tuple[str, str], tuple[str, int]],
    display_order_by_group: Mapping[tuple[str, str], int],
) -> list[ParameterRow]:
    """GUI 表示順に並び替えた rows を返す。"""

    # この関数は「表示の読みやすさ」と「フレーム間の安定性」を優先して並べ替える。
    #
    # - style は “いつでも先頭” かつ固定順（ユーザーが探すことが多い）
    # - primitive/effect/other は “コードに現れた順” を基本にする
    #   （= display_order_by_group で安定化された順序）
    # - effect は “チェーン単位” にまとめる（折りたたみの単位を壊さない）

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

    def _display_order(row: ParameterRow) -> int:
        # display_order_by_group は (op, site_id) 単位の「観測順（コード順）」の近似。
        # 見つからない場合は末尾へ回す（未知 group / 互換性のための保険）。
        return int(display_order_by_group.get((row.op, row.site_id), 10**9))

    # --- Non-style: Primitive / Effect chain / other を “ブロック” として並べる ---
    #
    # - Primitive は (op, ordinal) 単位
    # - Effect は chain_id 単位（折りたたみ維持）
    # - other は (op, site_id) 単位（最小限）

    primitive_arg_index_by_op: dict[str, dict[str, int]] = {}
    effect_arg_index_by_op: dict[str, dict[str, int]] = {}

    def _primitive_arg_index(op: str, arg: str) -> int:
        if op not in primitive_arg_index_by_op:
            order = primitive_registry.get_param_order(op)
            primitive_arg_index_by_op[op] = {a: i for i, a in enumerate(order)}
        index_by_arg = primitive_arg_index_by_op[op]
        return int(index_by_arg.get(arg, 10**9))

    def _effect_arg_index(op: str, arg: str) -> int:
        if op not in effect_arg_index_by_op:
            order = effect_registry.get_param_order(op)
            effect_arg_index_by_op[op] = {a: i for i, a in enumerate(order)}
        index_by_arg = effect_arg_index_by_op[op]
        return int(index_by_arg.get(arg, 10**9))

    primitive_blocks: dict[tuple[str, int], list[ParameterRow]] = {}
    for row in primitive_rows:
        # primitive は 1 つの呼び出し（site_id）に対して複数 arg 行がぶら下がる。
        # GUI では `circle#3` のように op と ordinal でまとまりを認識するため、
        # ブロックキーも (op, ordinal) に寄せる。
        primitive_blocks.setdefault((row.op, int(row.ordinal)), []).append(row)

    effect_blocks: dict[str, list[ParameterRow]] = {}
    effect_fallback_rows: list[ParameterRow] = []
    for row in effect_rows:
        # effect は `step_info_by_site` で「どのチェーンの何番目か」を引ける。
        # 引けないものは（不整合/旧データなど）other へフォールバックし、表示は崩さない。
        info = step_info_by_site.get((row.op, row.site_id))
        if info is None:
            effect_fallback_rows.append(row)
            continue
        chain_id, _step_index = info
        effect_blocks.setdefault(str(chain_id), []).append(row)

    other_blocks: dict[tuple[str, str], list[ParameterRow]] = {}
    for row in other_rows + effect_fallback_rows:
        # other は「最小限のまとまり」として (op, site_id) 単位にする。
        # （primitive/effect と違い、意味的なグルーピング規則が無い想定）
        other_blocks.setdefault((row.op, row.site_id), []).append(row)

    # effect チェーンは “チェーン内の各ステップの display_order” を持つが、
    # チェーン全体としては「最初に現れたステップの位置」に寄せて並べたい。
    # そのため chain_id ごとに min(display_order) を求め、チェーンの並び順に使う。
    chain_min_display_order: dict[str, int] = {}
    for (op, site_id), (chain_id, _step_index) in step_info_by_site.items():
        order = int(display_order_by_group.get((str(op), str(site_id)), 10**9))
        prev = chain_min_display_order.get(str(chain_id))
        if prev is None or order < prev:
            chain_min_display_order[str(chain_id)] = int(order)

    blocks: list[tuple[tuple[int, int, str], list[ParameterRow]]] = []

    for primitive_key, block_rows in primitive_blocks.items():
        op, ordinal = primitive_key
        # primitive ブロックの位置は、そのブロック内行の display_order の最小値に寄せる。
        # （同一 primitive 呼び出し内で arg 行の順序は固定だが、念のため min を取る）
        order = min(_display_order(r) for r in block_rows)
        blocks.append(
            (
                (int(order), 0, f"{op}#{int(ordinal)}"),
                sorted(
                    block_rows,
                    key=lambda r: (_primitive_arg_index(op, str(r.arg)), str(r.arg)),
                ),
            )
        )

    def _step_sort_key(r: ParameterRow) -> tuple[int, int, str]:
        # チェーン内では step_index（= effect 呼び出し順）を優先し、
        # 同一 step 内は arg 名で安定に並べる。
        info = step_info_by_site.get((r.op, r.site_id))
        if info is None:
            # effect_blocks の対象は step_info がある前提だが、
            # ここは保険として「末尾へ回す」だけに留める（過度に防御しない）。
            return (10**9, 10**9, str(r.arg))
        _cid, step_index = info
        return (
            int(step_index),
            _effect_arg_index(str(r.op), str(r.arg)),
            str(r.arg),
        )

    for chain_id, block_rows in effect_blocks.items():
        # effect チェーンの “ブロック位置” はチェーン内最小の display_order に寄せる。
        order = int(chain_min_display_order.get(chain_id, 10**9))
        blocks.append(
            (
                (int(order), 1, str(chain_id)),
                sorted(block_rows, key=_step_sort_key),
            )
        )

    for other_key, block_rows in other_blocks.items():
        op, site_id = other_key
        # other ブロックも primitive 同様、ブロック内の min(display_order) に寄せる。
        order = min(_display_order(r) for r in block_rows)
        if op in effect_registry:
            ordered = sorted(
                block_rows,
                key=lambda r: (_effect_arg_index(op, str(r.arg)), str(r.arg)),
            )
        else:
            ordered = sorted(block_rows, key=lambda r: str(r.arg))
        blocks.append(
            (
                (int(order), 2, f"{op}:{site_id}"),
                ordered,
            )
        )

    out_non_style: list[ParameterRow] = []
    for _sort_key, block_rows in sorted(blocks, key=lambda item: item[0]):
        # blocks の sort_key は (display_order, kind_rank, stable_id)。
        # - display_order : 基本の並び（コード順）
        # - kind_rank     : 同順序なら primitive -> effect -> other の順で出す
        # - stable_id     : 同順序のときも決定的にする（set/dict の揺れを潰す）
        out_non_style.extend(block_rows)

    # 最終的な表示順: style（global/layer 固定） → non-style（コード順 = display_order）
    return style_global_rows + style_layer_rows + out_non_style


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
            return {int(cc_key)}
        return {int(v) for v in cc_key if v is not None}

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
            set_meta(store, key, effective_meta)

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
                store._runtime_ref().last_effective_by_key.get(key)
                if cc_removed
                else None
            )
            if baked_effective is not None:
                update_state_from_ui(
                    store,
                    key,
                    baked_effective,
                    meta=effective_meta,
                    override=True,
                    cc_key=after.cc_key,
                )
            else:
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
        display_order_by_group=store._runtime_ref().display_order_by_group,
    )

    # --- 3) Effect のチェーン境界/順序を取得し、ヘッダ表示名（E(name=...)）を解決 ---
    #
    # effect は “チェーン” 単位のヘッダが欲しいので、
    # ParamStore が保持する (op, site_id) -> (chain_id, step_index) を参照する。
    # - chain_id: チェーン識別子（EffectBuilder 生成時の site_id）
    # - step_index: チェーン内のステップ順序（E.scale().rotate()... の順番）
    step_info_by_site = store.effect_steps()
    # チェーンヘッダの表示名:
    # - E(name=...) があればそれ
    # - 無ければ effect#N（N は表示順=観測順）
    # - 同名衝突は表示専用に name#1/#2
    effect_chain_header_by_id = effect_chain_header_display_names_from_snapshot(
        snapshot,
        step_info_by_site=step_info_by_site,
        display_order_by_group=store._runtime_ref().display_order_by_group,
        is_effect_op=lambda op: op in effect_registry,
    )
    # “チェーン内の同一 op 連番”:
    # E.scale().rotate().scale() なら scale#1, rotate#1, scale#2 を作るための辞書。
    effect_step_ordinal_by_site = effect_step_ordinals_by_site(step_info_by_site)

    # --- 4) snapshot -> UI 行モデルへ変換（純粋関数）---
    # rows は元々 (op, ordinal, arg) でソートされるが、
    # Effect については「チェーン順/ステップ順」で見せたいので、ここで並び替える。
    rows_before_raw = rows_from_snapshot(snapshot)
    runtime = store._runtime_ref()

    primitive_known_args_by_op: dict[str, set[str]] = {}
    effect_known_args_by_op: dict[str, set[str]] = {}

    unknown_args_new: set[tuple[str, str]] = set()
    rows_before_raw_filtered: list[ParameterRow] = []
    for row in rows_before_raw:
        op = str(row.op)
        arg = str(row.arg)

        if op in primitive_registry:
            known_args = primitive_known_args_by_op.get(op)
            if known_args is None:
                known_args = set(primitive_registry.get_meta(op).keys())
                primitive_known_args_by_op[op] = known_args
            if arg not in known_args:
                pair = (op, arg)
                if pair not in runtime.warned_unknown_args:
                    runtime.warned_unknown_args.add(pair)
                    unknown_args_new.add(pair)
                continue

        elif op in effect_registry:
            known_args = effect_known_args_by_op.get(op)
            if known_args is None:
                known_args = set(effect_registry.get_meta(op).keys())
                effect_known_args_by_op[op] = known_args
            if arg not in known_args:
                pair = (op, arg)
                if pair not in runtime.warned_unknown_args:
                    runtime.warned_unknown_args.add(pair)
                    unknown_args_new.add(pair)
                continue

        rows_before_raw_filtered.append(row)

    if unknown_args_new:
        pairs = ", ".join(f"{op}.{arg}" for op, arg in sorted(unknown_args_new))
        _logger.warning("未登録引数を無視します（次回保存で削除）: %s", pairs)
    rows_before_raw = rows_before_raw_filtered

    # 最終的な表示順: style → primitive → effect → other
    # ※ この rows_before の順番が、そのまま GUI の並び順になる。
    rows_before = _order_rows_for_display(
        rows_before_raw,
        step_info_by_site=step_info_by_site,
        display_order_by_group=store._runtime_ref().display_order_by_group,
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
        collapsed_headers=store._collapsed_headers_ref(),
    )

    # --- 7) 変更があった場合だけ store へ反映 ---
    # rows_before/rows_after は 1:1 対応している前提（render_parameter_table が維持する）。
    if changed:
        _apply_updated_rows_to_store(store, snapshot, rows_before, rows_after)
    return changed
