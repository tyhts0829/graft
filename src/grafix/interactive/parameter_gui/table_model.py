"""
Purpose:
    ParamStore構造とGUI catalogから、revision単位で再利用できる静的table modelを構築する。
Use when:
    row順、group layout、effect chain可動性、またはmodel cache keyを変更する場合。
Constraints:
    - modelはstoreのtable revisionとimmutable catalog内でのみ再利用する。
    - multi-input effectをchain先頭に保ち、不完全・filtered topologyを並べ替え可能にしない。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Set as AbstractSet
from dataclasses import dataclass, replace
from typing import Literal, TypeAlias
from weakref import WeakKeyDictionary

from grafix.core.parameters.effects import EffectStepKey, EffectStepTopology
from grafix.core.parameters.identity import group_key, identity_string
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.snapshot_ops import ParamSnapshot, store_snapshot
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.view import ParameterRow
from grafix.core.value_validation import exact_integer

from .catalog import ParameterGuiCatalog
from .group_blocks import GroupBlockLayout

ParameterTableCacheKey: TypeAlias = tuple[int, ParameterGuiCatalog]

EFFECT_ORDER_DUPLICATE_REASON = (
    "Effect step identity is duplicated; assign a unique key or instance_key."
)
EFFECT_ORDER_FILTERED_REASON = "Clear filters to reorder the complete effect chain."
EFFECT_ORDER_INCOMPLETE_REASON = (
    "This chain includes effect steps that are not visible in the Parameter GUI."
)
EFFECT_ORDER_MULTI_INPUT_REASON = "A multi-input effect must remain the first step in its chain."
EFFECT_ORDER_SINGLE_STEP_REASON = "Add at least two effect steps to reorder this chain."
EFFECT_ORDER_TOPOLOGY_REASON = "Effect topology is incomplete; render a successful frame first."


@dataclass(frozen=True, slots=True)
class EffectChainTableState:
    """Parameter table が必要とする、1 effect chain の並べ替え状態。"""

    chain_id: str
    steps: tuple[EffectStepKey, ...]
    n_inputs: tuple[int, ...]
    order_overridden: bool
    disabled_reason: str | None = None

    def __post_init__(self) -> None:
        identity_string(self.chain_id, name="EffectChainTableState.chain_id")
        if not isinstance(self.steps, tuple):
            raise TypeError("EffectChainTableState.steps は tuple である必要があります")
        steps = tuple(group_key(step, name="effect step") for step in self.steps)
        if not isinstance(self.n_inputs, tuple):
            raise TypeError("EffectChainTableState.n_inputs は tuple である必要があります")
        n_inputs = tuple(
            exact_integer(value, name="n_inputs", minimum=1) for value in self.n_inputs
        )
        if len(steps) != len(n_inputs):
            raise ValueError("steps と n_inputs の要素数が一致しません")
        if not isinstance(self.order_overridden, bool):
            raise TypeError("order_overridden は bool である必要があります")
        if self.disabled_reason is not None and not isinstance(
            self.disabled_reason,
            str,
        ):
            raise TypeError("disabled_reason は str または None である必要があります")
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "n_inputs", n_inputs)

    def step_index(self, step: EffectStepKey) -> int | None:
        """effective order 内の index を返す。未登録または重複時は None。"""

        normalized = group_key(step, name="step")
        matches = [index for index, candidate in enumerate(self.steps) if candidate == normalized]
        return matches[0] if len(matches) == 1 else None

    def is_pinned(self, step: EffectStepKey) -> bool:
        """multi-input 制約により移動できないstepならTrueを返す。"""

        index = self.step_index(step)
        return index is not None and self.n_inputs[index] > 1

    def can_move(
        self,
        source: EffectStepKey,
        target: EffectStepKey,
        placement: str,
    ) -> bool:
        """source を target の前後へ移動できるか返す。"""

        if self.disabled_reason is not None or placement not in {"before", "after"}:
            return False
        source_index = self.step_index(source)
        target_index = self.step_index(target)
        if source_index is None or target_index is None or source_index == target_index:
            return False
        if self.n_inputs[source_index] > 1:
            return False

        order = list(self.steps)
        moving = order.pop(source_index)
        insertion_index = order.index(self.steps[target_index])
        if placement == "after":
            insertion_index += 1
        order.insert(insertion_index, moving)
        if tuple(order) == self.steps:
            return False

        n_inputs_by_step = dict(zip(self.steps, self.n_inputs, strict=True))
        return all(n_inputs_by_step[step] <= 1 or index == 0 for index, step in enumerate(order))

    def neighbor_move(
        self,
        source: EffectStepKey,
        *,
        direction: int,
    ) -> tuple[EffectStepKey, Literal["before", "after"]] | None:
        """Move Up/Down用の(target, placement)を返す。"""

        source_index = self.step_index(source)
        direction_value = exact_integer(direction, name="direction")
        if source_index is None or direction_value not in {-1, 1}:
            return None
        target_index = source_index + direction_value
        if target_index < 0 or target_index >= len(self.steps):
            return None
        target = self.steps[target_index]
        placement: Literal["before", "after"] = "before" if direction_value < 0 else "after"
        if not self.can_move(source, target, placement):
            return None
        return target, placement

    def for_visible_steps(
        self,
        visible_steps: AbstractSet[EffectStepKey],
    ) -> EffectChainTableState:
        """filter後にstepが欠けるchainを並べ替え不可にした状態を返す。"""

        if self.disabled_reason is not None:
            return self
        normalized_visible = {group_key(step, name="visible effect step") for step in visible_steps}
        if normalized_visible == set(self.steps):
            return self
        return replace(self, disabled_reason=EFFECT_ORDER_FILTERED_REASON)


def effect_chain_table_states(
    *,
    topologies: Mapping[str, tuple[EffectStepTopology, ...]],
    step_info_by_site: Mapping[EffectStepKey, tuple[str, int]],
    order_overrides: Mapping[str, tuple[EffectStepKey, ...]],
    gui_steps_by_chain: Mapping[str, AbstractSet[EffectStepKey]],
) -> Mapping[str, EffectChainTableState]:
    """core topologyとGUI-visible stepからchain描画状態を構築する。"""

    states: dict[str, EffectChainTableState] = {}
    for chain_id, topology in topologies.items():
        identity_string(chain_id, name="chain_id")
        code_steps = tuple(step.key for step in topology)
        n_inputs_by_step = {step.key: step.n_inputs for step in topology}
        duplicate = any(count > 1 for count in Counter(code_steps).values())

        indexed_effective: list[tuple[int, int, EffectStepKey]] = []
        for code_index, step in enumerate(code_steps):
            info = step_info_by_site.get(step)
            if info is None or info[0] != chain_id:
                indexed_effective = []
                break
            indexed_effective.append((info[1], code_index, step))
        if len(indexed_effective) == len(code_steps):
            effective_steps = tuple(step for _index, _code_index, step in sorted(indexed_effective))
        else:
            effective_steps = code_steps

        disabled_reason: str | None = None
        if duplicate:
            disabled_reason = EFFECT_ORDER_DUPLICATE_REASON
        elif len(effective_steps) < 2:
            disabled_reason = EFFECT_ORDER_SINGLE_STEP_REASON
        elif set(effective_steps) != set(code_steps):
            disabled_reason = EFFECT_ORDER_TOPOLOGY_REASON
        else:
            gui_steps = {
                group_key(step, name="GUI effect step")
                for step in gui_steps_by_chain.get(chain_id, frozenset())
            }
            if gui_steps != set(code_steps):
                disabled_reason = EFFECT_ORDER_INCOMPLETE_REASON

        n_inputs = tuple(n_inputs_by_step.get(step, 1) for step in effective_steps)
        if disabled_reason is None and any(
            count > 1 and index != 0 for index, count in enumerate(n_inputs)
        ):
            disabled_reason = EFFECT_ORDER_MULTI_INPUT_REASON

        states[chain_id] = EffectChainTableState(
            chain_id=chain_id,
            steps=effective_steps,
            n_inputs=n_inputs,
            order_overridden=chain_id in order_overrides,
            disabled_reason=disabled_reason,
        )
    return states


@dataclass(frozen=True, slots=True)
class ParameterTableModel:
    """store revision と session catalog にだけ依存する不変な表示構造。

    MIDI の最新値、effective 値、active/loaded 状態などフレームごとの動的値は
    意図的に含めない。呼び出し側が描画直前に合成することで、行の構築・分類・
    並べ替えを毎フレーム繰り返さずに済む。
    """

    cache_key: ParameterTableCacheKey
    catalog: ParameterGuiCatalog
    value_revision: int
    snapshot: ParamSnapshot
    rows: tuple[ParameterRow, ...]
    keys: tuple[ParameterKey, ...]
    search_corpus_by_row: tuple[str, ...]
    group_layout: tuple[GroupBlockLayout, ...]
    row_index_by_key: Mapping[ParameterKey, int]
    row_indices_by_group: Mapping[tuple[str, str], tuple[int, ...]]
    raw_label_by_site: Mapping[tuple[str, str], str]
    primitive_header_by_group: Mapping[tuple[str, int], str]
    layer_style_name_by_site_id: Mapping[str, str]
    effect_chain_header_by_id: Mapping[str, str]
    step_info_by_site: Mapping[tuple[str, str], tuple[str, int]]
    effect_step_ordinal_by_site: Mapping[tuple[str, str], int]
    effect_chain_state_by_id: Mapping[str, EffectChainTableState]


ModelBuilder: TypeAlias = Callable[
    [ParamStore, ParamSnapshot, ParameterTableCacheKey], ParameterTableModel
]
ModelRefresher: TypeAlias = Callable[
    [ParamStore, ParameterTableModel, frozenset[ParameterKey]],
    ParameterTableModel,
]


class ParameterTableModelCache:
    """ParamStore ごとに直近 1 revision のモデルだけを保持する。"""

    def __init__(self) -> None:
        self._models: WeakKeyDictionary[ParamStore, ParameterTableModel] = WeakKeyDictionary()
        self._build_count = 0

    @property
    def build_count(self) -> int:
        """この cache がモデルを構築した回数を返す。"""

        return int(self._build_count)

    def get_or_build(
        self,
        store: ParamStore,
        *,
        catalog: ParameterGuiCatalog,
        builder: ModelBuilder,
        refresher: ModelRefresher,
    ) -> ParameterTableModel:
        """store revision/catalog generation が同じなら動的値だけを更新する。"""

        if type(catalog) is not ParameterGuiCatalog:
            raise TypeError("catalog は exact ParameterGuiCatalog である必要があります")
        cache_key: ParameterTableCacheKey = (
            int(store.table_revision),
            catalog,
        )
        cached = self._models.get(store)
        if cached is not None and cached.cache_key == cache_key:
            changed_keys = store.value_changes_since(cached.value_revision)
            if changed_keys is not None:
                if not changed_keys:
                    return cached
                refreshed = refresher(store, cached, changed_keys)
                self._models[store] = refreshed
                return refreshed

        model = builder(store, store_snapshot(store), cache_key)
        self._models[store] = model
        self._build_count += 1
        return model

    def clear(self) -> None:
        """全 store の cached model と計測値を破棄する。"""

        self._models.clear()
        self._build_count = 0


__all__ = [
    "EFFECT_ORDER_DUPLICATE_REASON",
    "EFFECT_ORDER_FILTERED_REASON",
    "EFFECT_ORDER_INCOMPLETE_REASON",
    "EFFECT_ORDER_MULTI_INPUT_REASON",
    "EFFECT_ORDER_SINGLE_STEP_REASON",
    "EFFECT_ORDER_TOPOLOGY_REASON",
    "EffectChainTableState",
    "ParameterTableCacheKey",
    "ParameterTableModel",
    "ParameterTableModelCache",
    "effect_chain_table_states",
]
