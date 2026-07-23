"""ParamStore snapshot から immutable ParameterTableView を構築する。"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence, Set as AbstractSet
from dataclasses import dataclass, replace
from types import MappingProxyType
from weakref import WeakKeyDictionary

from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.favorites import favorite_parameter_key_set
from grafix.core.parameters.layer_style import LAYER_STYLE_OP
from grafix.core.parameters.meta import (
    ParamMeta,
    merge_code_meta_with_stored_gui_meta,
)
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.style import STYLE_OP
from grafix.core.parameters.snapshot_ops import (
    ParamSnapshot,
    ParamSnapshotEntry,
)
from grafix.core.parameters.view import (
    ParameterRow,
    canonicalize_ui_value_for_meta_change,
    rows_from_snapshot,
)

from .catalog import ParameterGuiCatalog
from .labeling import primitive_header_display_names_from_snapshot
from .labeling import (
    effect_chain_header_display_names_from_snapshot,
    effect_step_ordinals_by_site,
)
from .group_blocks import (
    GroupBlockLayout,
    group_layout_from_rows,
    visible_group_layout,
)
from .parameter_filter import (
    ParameterFilterState,
    matches_parameter_search_corpus,
    parameter_dynamic_search_corpus,
    parameter_row_has_midi_mapping,
    parameter_search_token_may_be_dynamic,
    parameter_search_tokens,
    parameter_static_search_corpus,
)
from .source_badge import source_badge_for_row
from .table_model import (
    EffectChainTableState,
    ParameterTableCacheKey,
    ParameterTableModel,
    ParameterTableModelCache,
    effect_chain_table_states,
)
from .visibility import active_mask_for_rows

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _ParameterDynamicSearchCacheKey:
    """source/MIDI search overlay の revision key。"""

    model_cache_key: ParameterTableCacheKey
    value_revision: int
    effective_revision: int


@dataclass(frozen=True, slots=True)
class _ParameterBaseVisibilityCacheKey:
    """filter から独立した active/loaded mask の revision key。"""

    model_cache_key: ParameterTableCacheKey
    value_revision: int
    show_inactive_params: bool
    effective_revision: int
    visibility_token: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _ParameterTableViewCacheKey:
    """view の可視性・filter 結果へ影響する revision/signature。"""

    model_cache_key: ParameterTableCacheKey
    value_revision: int
    show_inactive_params: bool
    filter_state: ParameterFilterState
    effective_revision: int
    favorite_revision: int
    visibility_token: tuple[object, ...]
    error_keys: frozenset[ParameterKey]
    favorite_keys: frozenset[ParameterKey]


@dataclass(frozen=True, slots=True)
class ParameterTableView:
    """1 GUI frame の filter 合成結果。"""

    model: ParameterTableModel
    visible_mask: tuple[bool, ...]
    visible_row_indices: tuple[int, ...]
    group_layout: tuple[GroupBlockLayout, ...]
    favorite_keys: frozenset[ParameterKey]
    filtered_count: int
    total_count: int
    effect_chain_state_by_id: Mapping[str, EffectChainTableState]

    @property
    def hidden_count(self) -> int:
        """visibility/filter により現在表示されない行数を返す。"""

        return max(0, int(self.total_count) - int(self.filtered_count))


class ParameterTableViewCache:
    """一つの GUI session が所有する table model/view cache。"""

    def __init__(self, catalog: ParameterGuiCatalog) -> None:
        if type(catalog) is not ParameterGuiCatalog:
            raise TypeError("catalog は exact ParameterGuiCatalog である必要があります")
        self._catalog = catalog
        self._models = ParameterTableModelCache()
        self._views: WeakKeyDictionary[
            ParamStore,
            tuple[_ParameterTableViewCacheKey, ParameterTableView],
        ] = WeakKeyDictionary()
        self._base_visibility: WeakKeyDictionary[
            ParamStore,
            tuple[_ParameterBaseVisibilityCacheKey, tuple[bool, ...]],
        ] = WeakKeyDictionary()
        self._dynamic_search_corpus: WeakKeyDictionary[
            ParamStore,
            tuple[_ParameterDynamicSearchCacheKey, tuple[str, ...]],
        ] = WeakKeyDictionary()
        self._view_build_count = 0

    @property
    def catalog(self) -> ParameterGuiCatalog:
        """この cache に固定された immutable catalog を返す。"""

        return self._catalog

    @property
    def model_build_count(self) -> int:
        """model を実際に構築した累積回数を返す。"""

        return self._models.build_count

    @property
    def view_build_count(self) -> int:
        """visibility/filter view を実際に構築した累積回数を返す。"""

        return int(self._view_build_count)

    def clear(self) -> None:
        """この session の derived table state と計測値だけを破棄する。"""

        self._models.clear()
        self._views.clear()
        self._base_visibility.clear()
        self._dynamic_search_corpus.clear()
        self._view_build_count = 0


def _order_rows_for_display(
    rows: list[ParameterRow],
    *,
    catalog: ParameterGuiCatalog,
    step_info_by_site: Mapping[tuple[str, str], tuple[str, int]],
    display_order_by_group: Mapping[tuple[str, str], int],
) -> list[ParameterRow]:
    """GUI 表示順に並び替えた rows を返す。"""

    if type(catalog) is not ParameterGuiCatalog:
        raise TypeError("catalog は exact ParameterGuiCatalog である必要があります")
    selected_catalog = catalog

    # この関数は「表示の読みやすさ」と「フレーム間の安定性」を優先して並べ替える。
    #
    # - style は “いつでも先頭” かつ固定順（ユーザーが探すことが多い）
    # - primitive/effect/other は “コードに現れた順” を基本にする
    #   （= display_order_by_group で安定化された順序）
    # - effect は “チェーン単位” にまとめる（折りたたみの単位を壊さない）

    style_global_rows: list[ParameterRow] = []
    style_layer_rows: list[ParameterRow] = []
    preset_rows: list[ParameterRow] = []
    primitive_rows: list[ParameterRow] = []
    effect_rows: list[ParameterRow] = []
    other_rows: list[ParameterRow] = []
    for row in rows:
        if row.op == STYLE_OP:
            style_global_rows.append(row)
        elif row.op == LAYER_STYLE_OP:
            style_layer_rows.append(row)
        elif selected_catalog.is_preset(row.op):
            preset_rows.append(row)
        elif selected_catalog.is_primitive_parameter(row.op):
            primitive_rows.append(row)
        elif selected_catalog.is_effect_parameter(row.op):
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
    style_layer_rows.sort(key=lambda r: (int(r.ordinal), layer_style_order.get(r.arg, 999), r.arg))

    def _display_order(row: ParameterRow) -> int:
        # display_order_by_group は (op, site_id) 単位の「観測順（コード順）」の近似。
        # 現在 frame で観測されない reconcile orphan は、編集・relink できるよう
        # 観測済み group の後ろにまとめて表示する。
        return int(display_order_by_group.get((row.op, row.site_id), 10**9))

    # --- Non-style: Preset / Primitive / Effect chain / other を “ブロック” として並べる ---
    #
    # - Preset は (op, ordinal) 単位
    # - Primitive は (op, ordinal) 単位
    # - Effect は chain_id 単位（折りたたみ維持）
    # - other は (op, site_id) 単位（最小限）

    arg_index_by_op: dict[str, dict[str, int]] = {}

    def _arg_index(op: str, arg: str) -> int:
        if op not in arg_index_by_op:
            entry = selected_catalog.resolve(op)
            order = () if entry is None else entry.schema.param_order
            arg_index_by_op[op] = {name: index for index, name in enumerate(order)}
        index_by_arg = arg_index_by_op[op]
        return int(index_by_arg.get(arg, 10**9))

    def _ordinal_blocks(
        source_rows: Sequence[ParameterRow],
    ) -> dict[tuple[str, int], list[ParameterRow]]:
        blocks_by_call: dict[tuple[str, int], list[ParameterRow]] = {}
        for row in source_rows:
            blocks_by_call.setdefault((row.op, int(row.ordinal)), []).append(row)
        return blocks_by_call

    preset_blocks = _ordinal_blocks(preset_rows)
    primitive_blocks = _ordinal_blocks(primitive_rows)

    effect_blocks: dict[str, list[ParameterRow]] = {}
    orphan_effect_rows: list[ParameterRow] = []
    for row in effect_rows:
        # effect は `step_info_by_site` で「どのチェーンの何番目か」を引ける。
        # 現在の effect chain に属さない行は reconcile orphan であり、relink
        # 操作へ到達できるよう site 単位の独立 block として表示する。
        info = step_info_by_site.get((row.op, row.site_id))
        if info is None:
            orphan_effect_rows.append(row)
            continue
        chain_id, _step_index = info
        effect_blocks.setdefault(chain_id, []).append(row)

    other_blocks: dict[tuple[str, str], list[ParameterRow]] = {}
    for row in other_rows + orphan_effect_rows:
        # other は「最小限のまとまり」として (op, site_id) 単位にする。
        # （primitive/effect と違い、意味的なグルーピング規則が無い想定）
        other_blocks.setdefault((row.op, row.site_id), []).append(row)

    # effect チェーンは “チェーン内の各ステップの display_order” を持つが、
    # チェーン全体としては「最初に現れたステップの位置」に寄せて並べたい。
    # そのため chain_id ごとに min(display_order) を求め、チェーンの並び順に使う。
    chain_min_display_order: dict[str, int] = {}
    for (op, site_id), (chain_id, _step_index) in step_info_by_site.items():
        order = int(display_order_by_group.get((op, site_id), 10**9))
        prev = chain_min_display_order.get(chain_id)
        if prev is None or order < prev:
            chain_min_display_order[chain_id] = int(order)

    blocks: list[tuple[tuple[int, int, str], list[ParameterRow]]] = []

    for kind_rank, blocks_by_call in ((0, preset_blocks), (1, primitive_blocks)):
        for (op, ordinal), block_rows in blocks_by_call.items():
            order = min(_display_order(row) for row in block_rows)
            blocks.append(
                (
                    (int(order), kind_rank, f"{op}#{int(ordinal)}"),
                    sorted(
                        block_rows,
                        key=lambda row: (_arg_index(op, row.arg), row.arg),
                    ),
                )
            )

    def _step_sort_key(r: ParameterRow) -> tuple[int, int, str]:
        # チェーン内では step_index（= effect 呼び出し順）を優先し、
        # 同一 step 内は arg 名で安定に並べる。
        _cid, step_index = step_info_by_site[(r.op, r.site_id)]
        return (
            int(step_index),
            _arg_index(r.op, r.arg),
            r.arg,
        )

    for chain_id, block_rows in effect_blocks.items():
        # effect チェーンの “ブロック位置” はチェーン内最小の display_order に寄せる。
        order = int(chain_min_display_order[chain_id])
        blocks.append(
            (
                (int(order), 2, chain_id),
                sorted(block_rows, key=_step_sort_key),
            )
        )

    for other_key, block_rows in other_blocks.items():
        op, site_id = other_key
        # other ブロックも primitive 同様、ブロック内の min(display_order) に寄せる。
        order = min(_display_order(r) for r in block_rows)
        if selected_catalog.is_effect_parameter(op):
            ordered = sorted(
                block_rows,
                key=lambda row: (_arg_index(op, row.arg), row.arg),
            )
        else:
            ordered = sorted(block_rows, key=lambda row: row.arg)
        blocks.append(
            (
                (int(order), 3, f"{op}:{site_id}"),
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


def _snapshot_with_catalog_meta(
    snapshot: ParamSnapshot,
    *,
    catalog: ParameterGuiCatalog,
) -> ParamSnapshot:
    """保存 state を維持しつつ code-owned metadata を session catalog へ追随させる。"""

    overrides: dict[ParameterKey, ParamSnapshotEntry] = {}
    for key, entry in snapshot.items():
        stored_meta, state, ordinal, label = entry
        op = key.op
        code_meta: ParamMeta | None
        code_base: object = ""
        catalog_entry = catalog.resolve(op)
        if catalog_entry is None:
            code_meta = None
        else:
            code_meta = catalog_entry.schema.meta.get(key.arg)
            code_base = catalog_entry.schema.defaults.get(key.arg, "")
        if code_meta is None:
            continue
        merged_meta = merge_code_meta_with_stored_gui_meta(
            code_meta,
            stored_meta,
        )
        if merged_meta == stored_meta:
            continue
        display_state = state
        if merged_meta.kind != stored_meta.kind:
            display_state = replace(
                state,
                ui_value=canonicalize_ui_value_for_meta_change(
                    state.ui_value,
                    code_base,
                    stored_meta,
                    merged_meta,
                ),
            )
        overrides[key] = (
            merged_meta,
            display_state,
            int(ordinal),
            label,
        )

    if not overrides:
        return snapshot
    updated = dict(snapshot)
    updated.update(overrides)
    return MappingProxyType(updated)


def _build_parameter_table_model(
    store: ParamStore,
    snapshot: ParamSnapshot,
    cache_key: ParameterTableCacheKey,
    *,
    catalog: ParameterGuiCatalog,
) -> ParameterTableModel:
    """snapshot から revision 内で不変なテーブル構造を 1 回だけ構築する。"""

    snapshot = _snapshot_with_catalog_meta(snapshot, catalog=catalog)
    raw_label_by_site: dict[tuple[str, str], str] = {}
    for key, (_meta, _state, _ordinal, label) in snapshot.items():
        op = key.op
        if catalog.resolve(op) is None and op != LAYER_STYLE_OP:
            continue
        if label is None:
            continue
        label_s = label.strip()
        if not label_s:
            continue
        raw_label_by_site.setdefault((op, key.site_id), label_s)

    runtime = store.runtime_view()
    primitive_header_by_group = primitive_header_display_names_from_snapshot(
        snapshot,
        is_primitive_op=lambda op: (catalog.is_primitive_parameter(op) or catalog.is_preset(op)),
        display_order_by_group=runtime.display_order_by_group,
    )

    step_info_by_site = store.effect_steps()
    effect_chain_header_by_id = effect_chain_header_display_names_from_snapshot(
        snapshot,
        step_info_by_site=step_info_by_site,
        display_order_by_group=runtime.display_order_by_group,
        is_effect_op=catalog.is_effect_parameter,
    )
    effect_step_ordinal_by_site = effect_step_ordinals_by_site(step_info_by_site)

    primitive_known_args_by_op: dict[str, set[str]] = {}
    preset_known_args_by_op: dict[str, set[str]] = {}
    effect_known_args_by_op: dict[str, set[str]] = {}
    unknown_args_new: set[tuple[str, str]] = set()
    filtered_rows: list[ParameterRow] = []

    for row in rows_from_snapshot(snapshot):
        op = row.op
        arg = row.arg

        entry = catalog.resolve(op)
        if entry is not None:
            if entry.kind == "preset":
                known_args = preset_known_args_by_op.get(op)
                target_cache = preset_known_args_by_op
            elif catalog.is_effect_parameter(op):
                known_args = effect_known_args_by_op.get(op)
                target_cache = effect_known_args_by_op
            else:
                known_args = primitive_known_args_by_op.get(op)
                target_cache = primitive_known_args_by_op
            if known_args is None:
                known_args = set(entry.schema.meta)
                target_cache[op] = known_args
            if arg not in known_args:
                unknown_args_new.add((op, arg))
                continue

        filtered_rows.append(row)

    newly_warned = store.record_unknown_argument_warnings(unknown_args_new)
    if newly_warned:
        pairs = ", ".join(f"{op}.{arg}" for op, arg in sorted(newly_warned))
        _logger.warning("未登録引数を無視します（次回保存で削除）: %s", pairs)

    rows = _order_rows_for_display(
        filtered_rows,
        catalog=catalog,
        step_info_by_site=step_info_by_site,
        display_order_by_group=runtime.display_order_by_group,
    )

    gui_steps_by_chain: dict[str, set[tuple[str, str]]] = {}
    for row in rows:
        step_key = (row.op, row.site_id)
        step_info = step_info_by_site.get(step_key)
        if step_info is None:
            continue
        gui_steps_by_chain.setdefault(step_info[0], set()).add(step_key)
    effect_chain_state_by_id = effect_chain_table_states(
        topologies=store.effect_chain_topologies(),
        step_info_by_site=step_info_by_site,
        order_overrides=store.effect_order_overrides(),
        gui_steps_by_chain=gui_steps_by_chain,
    )

    layer_style_name_by_site_id: dict[str, str] = {}
    for key, (_meta, _state, _ordinal, label) in snapshot.items():
        if key.op != LAYER_STYLE_OP:
            continue
        site_id = key.site_id
        layer_style_name_by_site_id.setdefault(site_id, label if label else "layer")

    group_layout = group_layout_from_rows(
        rows,
        catalog=catalog,
        primitive_header_by_group=primitive_header_by_group,
        layer_style_name_by_site_id=layer_style_name_by_site_id,
        effect_chain_header_by_id=effect_chain_header_by_id,
        step_info_by_site=step_info_by_site,
        effect_step_ordinal_by_site=effect_step_ordinal_by_site,
    )
    keys = tuple(
        ParameterKey(
            op=row.op,
            site_id=row.site_id,
            arg=row.arg,
        )
        for row in rows
    )
    search_labels = [""] * len(rows)
    for block in group_layout:
        for item in block.items:
            row = rows[item.row_index]
            raw_label = raw_label_by_site.get(
                (row.op, row.site_id),
                "",
            )
            search_labels[item.row_index] = " ".join(
                part
                for part in (
                    item.visible_label,
                    str(block.header or ""),
                    str(raw_label),
                )
                if part
            )
    search_corpus_by_row = tuple(
        parameter_static_search_corpus(row, search_labels[index]) for index, row in enumerate(rows)
    )
    mutable_row_indices_by_group: dict[tuple[str, str], list[int]] = {}
    for index, key in enumerate(keys):
        mutable_row_indices_by_group.setdefault(
            (key.op, key.site_id),
            [],
        ).append(index)

    return ParameterTableModel(
        cache_key=cache_key,
        catalog=catalog,
        value_revision=int(store.value_revision),
        snapshot=snapshot,
        rows=tuple(rows),
        keys=keys,
        search_corpus_by_row=search_corpus_by_row,
        group_layout=group_layout,
        row_index_by_key=MappingProxyType({key: index for index, key in enumerate(keys)}),
        row_indices_by_group=MappingProxyType(
            {group: tuple(indices) for group, indices in mutable_row_indices_by_group.items()}
        ),
        raw_label_by_site=MappingProxyType(raw_label_by_site),
        primitive_header_by_group=MappingProxyType(primitive_header_by_group),
        layer_style_name_by_site_id=MappingProxyType(layer_style_name_by_site_id),
        effect_chain_header_by_id=MappingProxyType(effect_chain_header_by_id),
        step_info_by_site=MappingProxyType(step_info_by_site),
        effect_step_ordinal_by_site=MappingProxyType(effect_step_ordinal_by_site),
        effect_chain_state_by_id=MappingProxyType(dict(effect_chain_state_by_id)),
    )


def _refresh_parameter_table_model_values(
    store: ParamStore,
    model: ParameterTableModel,
    changed_keys: frozenset[ParameterKey],
) -> ParameterTableModel:
    """既存モデルのうち、変更された行の value state だけを差し替える。"""

    rows: list[ParameterRow] | None = None
    for key in changed_keys:
        index = model.row_index_by_key.get(key)
        state = store.get_state(key)
        if index is None or state is None:
            continue
        current = model.rows[index] if rows is None else rows[index]
        updated = replace(
            current,
            ui_value=state.ui_value,
            override=bool(state.override),
            cc_key=state.cc_key,
            reset_to_code=False,
        )
        if updated == current:
            continue
        if rows is None:
            rows = list(model.rows)
        rows[index] = updated
    return replace(
        model,
        value_revision=int(store.value_revision),
        rows=model.rows if rows is None else tuple(rows),
    )


def _parameter_table_model_for_store(
    store: ParamStore,
    *,
    cache: ParameterTableViewCache,
) -> ParameterTableModel:
    """明示 session cache から store の最新 immutable model を返す。"""

    if not isinstance(cache, ParameterTableViewCache):
        raise TypeError("cache は ParameterTableViewCache である必要があります")
    selected_catalog = cache.catalog
    return cache._models.get_or_build(
        store,
        catalog=selected_catalog,
        builder=lambda current_store, snapshot, cache_key: _build_parameter_table_model(
            current_store,
            snapshot,
            cache_key,
            catalog=selected_catalog,
        ),
        refresher=_refresh_parameter_table_model_values,
    )


def _visible_mask_for_model(
    store: ParamStore,
    rows: Sequence[ParameterRow],
    *,
    catalog: ParameterGuiCatalog,
    show_inactive: bool,
    activity_mask: Sequence[bool] | None = None,
) -> list[bool]:
    """静的 rows に active/loaded などフレーム動的な可視性を合成する。"""

    runtime = store.runtime_view()
    if bool(show_inactive):
        active_mask = [True] * len(rows)
    else:
        if activity_mask is None:
            activity_mask = active_mask_for_rows(
                rows,
                catalog=catalog,
                show_inactive=False,
                last_effective_by_key=runtime.last_effective_by_key,
            )
        if len(activity_mask) != len(rows):
            raise ValueError("activity_mask は rows と同じ長さである必要があります")
        active_mask = [bool(active) for active in activity_mask]
    if not runtime.loaded_groups:
        return active_mask

    loaded = {(op, site_id) for op, site_id in runtime.loaded_groups if op != STYLE_OP}
    observed = {(op, site_id) for op, site_id in runtime.observed_groups if op != STYLE_OP}
    hidden_groups = loaded - observed
    if not hidden_groups:
        return active_mask
    return [
        visible and (row.op, row.site_id) not in hidden_groups
        for row, visible in zip(rows, active_mask, strict=True)
    ]


def _base_visible_mask_for_model(
    store: ParamStore,
    model: ParameterTableModel,
    *,
    cache: ParameterTableViewCache,
    show_inactive: bool,
) -> tuple[bool, ...]:
    """query/filter から独立した active/loaded mask を revision cache する。"""

    runtime = store.runtime_view()
    # show-inactive でも loaded-but-not-observed group は隠すため visibility token
    # は常に必要。value/effective は active 判定を行う場合だけ key に含める。
    cache_key = _ParameterBaseVisibilityCacheKey(
        model_cache_key=model.cache_key,
        value_revision=(-1 if show_inactive else int(model.value_revision)),
        show_inactive_params=bool(show_inactive),
        effective_revision=(-1 if show_inactive else int(runtime.effective_revision)),
        visibility_token=runtime.visibility_cache_token(),
    )
    cached = cache._base_visibility.get(store)
    if cached is not None and cached[0] == cache_key:
        return cached[1]

    activity_mask: Sequence[bool] | None = None
    if not show_inactive:
        activity_mask = active_mask_for_rows(
            model.rows,
            catalog=model.catalog,
            show_inactive=False,
            last_effective_by_key=runtime.last_effective_by_key,
        )
    mask = tuple(
        _visible_mask_for_model(
            store,
            model.rows,
            catalog=model.catalog,
            show_inactive=show_inactive,
            activity_mask=activity_mask,
        )
    )
    cache._base_visibility[store] = (cache_key, mask)
    return mask


def _parameter_table_view_from_mask(
    model: ParameterTableModel,
    visible_mask: Sequence[bool],
    *,
    favorite_keys: frozenset[ParameterKey],
) -> ParameterTableView:
    """mask と静的 model layout を描画用 view へまとめる。"""

    normalized_mask = tuple(bool(visible) for visible in visible_mask)
    visible_row_indices = tuple(index for index, visible in enumerate(normalized_mask) if visible)
    visible_steps_by_chain: dict[str, set[tuple[str, str]]] = {}
    for index in visible_row_indices:
        row = model.rows[index]
        step_key = (row.op, row.site_id)
        step_info = model.step_info_by_site.get(step_key)
        if step_info is None:
            continue
        visible_steps_by_chain.setdefault(step_info[0], set()).add(step_key)
    effect_chain_state_by_id = MappingProxyType(
        {
            chain_id: state.for_visible_steps(visible_steps_by_chain.get(chain_id, frozenset()))
            for chain_id, state in model.effect_chain_state_by_id.items()
        }
    )
    return ParameterTableView(
        model=model,
        visible_mask=normalized_mask,
        visible_row_indices=visible_row_indices,
        group_layout=visible_group_layout(model.group_layout, normalized_mask),
        favorite_keys=favorite_keys,
        filtered_count=len(visible_row_indices),
        total_count=len(normalized_mask),
        effect_chain_state_by_id=effect_chain_state_by_id,
    )


def _reuse_default_parameter_table_view(
    store: ParamStore,
    cached: tuple[_ParameterTableViewCacheKey, ParameterTableView],
    cache_key: _ParameterTableViewCacheKey,
    model: ParameterTableModel,
    *,
    favorite_keys: frozenset[ParameterKey],
) -> ParameterTableView | None:
    """default view の mask が不変なら、最新 value model だけ差し替える。"""

    previous_key, previous_view = cached
    default_filter = ParameterFilterState()
    if (
        previous_key.model_cache_key != cache_key.model_cache_key
        or previous_key.show_inactive_params != cache_key.show_inactive_params
        or previous_key.filter_state != default_filter
        or cache_key.filter_state != default_filter
        or previous_key.visibility_token != cache_key.visibility_token
    ):
        return None

    # show-inactive 時の default filter は value/effective/source/error/favorite
    # で mask が変わらない。loaded/observed は上の token 比較で検証済み。
    if cache_key.show_inactive_params:
        return replace(
            previous_view,
            model=model,
            favorite_keys=favorite_keys,
        )

    value_changes = store.value_changes_since(previous_key.value_revision)
    effective_changes = store.effective_changes_since(previous_key.effective_revision)
    if value_changes is None or effective_changes is None:
        return None
    changed_keys = value_changes | effective_changes
    if not _changed_groups_keep_default_mask(
        store,
        model,
        previous_view.visible_mask,
        changed_keys,
    ):
        return None
    return replace(
        previous_view,
        model=model,
        favorite_keys=favorite_keys,
    )


def _changed_groups_keep_default_mask(
    store: ParamStore,
    model: ParameterTableModel,
    previous_mask: Sequence[bool],
    changed_keys: AbstractSet[ParameterKey],
) -> bool:
    """変更 key の所属 group だけを再評価し、mask 同値性を返す。"""

    changed_groups: set[tuple[str, str]] = set()
    for key in changed_keys:
        if key not in model.row_index_by_key:
            return False
        changed_groups.add((key.op, key.site_id))

    for group in changed_groups:
        indices = model.row_indices_by_group.get(group)
        if indices is None:
            return False
        rows = [model.rows[index] for index in indices]
        current = _visible_mask_for_model(
            store,
            rows,
            catalog=model.catalog,
            show_inactive=False,
        )
        if any(
            bool(previous_mask[index]) != bool(visible)
            for index, visible in zip(indices, current, strict=True)
        ):
            return False
    return True


def _dynamic_search_corpus_for_model(
    store: ParamStore,
    model: ParameterTableModel,
    *,
    cache: ParameterTableViewCache,
) -> tuple[str, ...]:
    """source/MIDI 検索 overlay を revision 内で 1 回だけ構築する。"""

    runtime = store.runtime_view()
    cache_key = _ParameterDynamicSearchCacheKey(
        model_cache_key=model.cache_key,
        value_revision=int(model.value_revision),
        effective_revision=int(runtime.effective_revision),
    )
    cached = cache._dynamic_search_corpus.get(store)
    if cached is not None and cached[0] == cache_key:
        return cached[1]
    corpus_items: list[str] = []
    append_corpus = corpus_items.append
    last_source_by_key = runtime.last_source_by_key
    for row, key in zip(model.rows, model.keys, strict=True):
        source = source_badge_for_row(row, last_source_by_key.get(key))
        # MIDI 未割当が通常ケース。専用 helper の呼び出しと CC token 構築判定を
        # 省き、初回の動的 query でも 10,000 行を frame budget 内に収める。
        append_corpus(
            str(source).casefold()
            if row.cc_key is None
            else parameter_dynamic_search_corpus(row, source)
        )
    corpus = tuple(corpus_items)
    cache._dynamic_search_corpus[store] = (cache_key, corpus)
    return corpus


def parameter_table_view_for_store(
    store: ParamStore,
    *,
    cache: ParameterTableViewCache,
    show_inactive_params: bool,
    filter_state: ParameterFilterState | None = None,
    error_keys: AbstractSet[ParameterKey] = frozenset(),
    favorite_keys: AbstractSet[ParameterKey] | None = None,
) -> ParameterTableView:
    """既存 visibility と検索/filter を合成した immutable view を返す。"""

    if not isinstance(cache, ParameterTableViewCache):
        raise TypeError("cache は ParameterTableViewCache である必要があります")
    state = ParameterFilterState() if filter_state is None else filter_state
    favorites = (
        favorite_parameter_key_set(store) if favorite_keys is None else frozenset(favorite_keys)
    )
    model = _parameter_table_model_for_store(store, cache=cache)
    rows = model.rows
    runtime = store.runtime_view()
    normalized_error_keys = frozenset(error_keys)
    cache_key = _ParameterTableViewCacheKey(
        model_cache_key=model.cache_key,
        value_revision=int(model.value_revision),
        show_inactive_params=bool(show_inactive_params),
        filter_state=state,
        effective_revision=int(runtime.effective_revision),
        favorite_revision=int(store.favorite_revision),
        visibility_token=runtime.visibility_cache_token(),
        error_keys=normalized_error_keys,
        favorite_keys=favorites,
    )
    cached = cache._views.get(store)
    if cached is not None and cached[0] == cache_key and cached[1].model is model:
        return cached[1]
    if cached is not None:
        reused = _reuse_default_parameter_table_view(
            store,
            cached,
            cache_key,
            model,
            favorite_keys=favorites,
        )
        if reused is not None:
            cache._views[store] = (cache_key, reused)
            return reused

    # base visibility は query/filter の変更から独立しているため、検索文字を
    # 入力するたびに ui_visible rule を全件再評価しない。
    base_visible_mask = _base_visible_mask_for_model(
        store,
        model,
        cache=cache,
        show_inactive=bool(show_inactive_params),
    )
    activity_mask: Sequence[bool] | None = None
    if state.activity != "all":
        activity_mask = active_mask_for_rows(
            rows,
            catalog=model.catalog,
            show_inactive=False,
            last_effective_by_key=runtime.last_effective_by_key,
        )

    # 通常時（検索/filter 無し）は静的 search label/source record を組み立てず、
    # 大規模 scene の既定フレームコストを visibility 判定だけに抑える。
    if state == ParameterFilterState():
        view = _parameter_table_view_from_mask(
            model,
            base_visible_mask,
            favorite_keys=favorites,
        )
    else:
        query_tokens = parameter_search_tokens(state.query)
        query_only = (
            bool(query_tokens)
            and state.activity == "all"
            and not state.ui_override_only
            and not state.midi_mapped_only
            and not state.error_only
            and not state.favorite_only
        )
        static_query_only = query_only and all(
            not parameter_search_token_may_be_dynamic(token) for token in query_tokens
        )
        if static_query_only:
            # 静的 substring だけの通常検索は、row/source object を合成せず
            # casefold 済み corpus を tight comprehension で走査する。
            if len(query_tokens) == 1:
                token = query_tokens[0]
                visible_mask = tuple(
                    bool(base_visible and token in corpus)
                    for base_visible, corpus in zip(
                        base_visible_mask,
                        model.search_corpus_by_row,
                        strict=True,
                    )
                )
            else:
                visible_mask = tuple(
                    bool(base_visible and all(token in corpus for token in query_tokens))
                    for base_visible, corpus in zip(
                        base_visible_mask,
                        model.search_corpus_by_row,
                        strict=True,
                    )
                )
        elif query_only:
            dynamic_corpus_by_row = _dynamic_search_corpus_for_model(
                store,
                model,
                cache=cache,
            )
            if len(query_tokens) == 1:
                token = query_tokens[0]
                visible_mask = tuple(
                    bool(base_visible and (token in static_corpus or token in dynamic_corpus))
                    for base_visible, static_corpus, dynamic_corpus in zip(
                        base_visible_mask,
                        model.search_corpus_by_row,
                        dynamic_corpus_by_row,
                        strict=True,
                    )
                )
            else:
                visible_mask = tuple(
                    bool(
                        base_visible
                        and all(
                            token in static_corpus or token in dynamic_corpus
                            for token in query_tokens
                        )
                    )
                    for base_visible, static_corpus, dynamic_corpus in zip(
                        base_visible_mask,
                        model.search_corpus_by_row,
                        dynamic_corpus_by_row,
                        strict=True,
                    )
                )
        else:
            matches_filter: list[bool] = []
            for index, (row, key, static_corpus) in enumerate(
                zip(
                    rows,
                    model.keys,
                    model.search_corpus_by_row,
                    strict=True,
                )
            ):
                active = True if activity_mask is None else bool(activity_mask[index])
                matches = True
                if state.activity == "active" and not active:
                    matches = False
                elif state.activity == "inactive" and active:
                    matches = False
                elif state.ui_override_only and not bool(row.override):
                    matches = False
                elif state.midi_mapped_only and not parameter_row_has_midi_mapping(row):
                    matches = False
                elif state.error_only and key not in normalized_error_keys:
                    matches = False
                elif state.favorite_only and key not in favorites:
                    matches = False
                elif query_tokens:
                    source = source_badge_for_row(
                        row,
                        runtime.last_source_by_key.get(key),
                    )
                    matches = matches_parameter_search_corpus(
                        static_corpus,
                        row,
                        source,
                        query_tokens,
                    )
                matches_filter.append(matches)

            visible_mask = tuple(
                bool(base_visible and matches)
                for base_visible, matches in zip(
                    base_visible_mask,
                    matches_filter,
                    strict=True,
                )
            )
        view = _parameter_table_view_from_mask(
            model,
            visible_mask,
            favorite_keys=favorites,
        )

    cache._views[store] = (cache_key, view)
    cache._view_build_count += 1
    return view


__all__ = [
    "ParameterTableView",
    "ParameterTableViewCache",
    "parameter_table_view_for_store",
]
