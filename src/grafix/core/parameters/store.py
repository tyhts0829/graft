# どこで: `src/grafix/core/parameters/store.py`。
# 何を: ParamStore（永続データの核）を定義する。
# なぜ: God-object 化を避け、周辺ロジック（ordinal/reconcile/永続化など）を別モジュールへ分離するため。

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from types import MappingProxyType, TracebackType
from typing import TYPE_CHECKING

from .adjustment_snapshot import (
    ParameterAdjustment,
    ParameterAdjustmentPatch,
    ParameterAdjustmentSnapshot,
    _ParameterAdjustmentSlot,
)
from .collapsed_header import (
    CollapsedHeaderKey,
    STYLE_COLLAPSED_HEADER_KEY,
    effect_chain_collapsed_header_key,
    group_collapsed_header_keys,
)
from .effects import EffectChainIndex, EffectOrder, EffectStepTopology
from .key import ParameterKey
from .labels import ParamLabels
from .meta import ParamMeta
from .ordinals import GroupOrdinals
from .runtime import ParamRuntimeView, ParamStoreRuntime
from .source import ValueSource
from .state import ParamState, ParamStateSnapshot

if TYPE_CHECKING:
    from .variations import Variation


@dataclass(frozen=True, slots=True)
class _TransientParamStoreState:
    """Transient rollback が所有する ParamStore の論理状態。"""

    states: dict[ParameterKey, ParamState]
    meta: dict[ParameterKey, ParamMeta]
    explicit_by_key: dict[ParameterKey, bool]
    labels: ParamLabels
    ordinals: GroupOrdinals
    effects: EffectChainIndex
    collapsed_headers: set[CollapsedHeaderKey]
    locked_keys: set[ParameterKey]
    favorite_keys: set[ParameterKey]
    variations: dict[str, Variation]
    runtime: ParamStoreRuntime
    revision: int
    table_revision: int
    value_revision: int
    style_revision: int
    favorite_revision: int
    value_change_log: deque[tuple[int, tuple[ParameterKey, ...]]]


class _ParamStoreRead:
    """ParamStore が所有する、参照を漏らさない内部 read port。"""

    __slots__ = ("_store",)

    def __init__(self, store: ParamStore) -> None:
        self._store = store

    @property
    def revision(self) -> int:
        return self._store.revision

    def state(self, key: ParameterKey) -> ParamState | None:
        state = self._store._states.get(key)
        return None if state is None else ParamState(**vars(state))

    def states(self) -> dict[ParameterKey, ParamState]:
        return {
            key: ParamState(**vars(state))
            for key, state in self._store._states.items()
        }

    def state_snapshots(self) -> dict[ParameterKey, ParamStateSnapshot]:
        return {
            key: ParamStateSnapshot.from_state(state)
            for key, state in self._store._states.items()
        }

    def meta(self, key: ParameterKey) -> ParamMeta | None:
        return self._store._meta.get(key)

    def all_meta(self) -> dict[ParameterKey, ParamMeta]:
        return dict(self._store._meta)

    def explicit(self, key: ParameterKey) -> bool | None:
        return self._store._explicit_by_key.get(key)

    def all_explicit(self) -> dict[ParameterKey, bool]:
        return dict(self._store._explicit_by_key)

    def parameter_keys(self) -> frozenset[ParameterKey]:
        return frozenset(
            set(self._store._states)
            | set(self._store._meta)
            | set(self._store._explicit_by_key)
        )

    def labels(self) -> ParamLabels:
        labels = ParamLabels()
        labels.replace(self._store._labels.as_dict())
        return labels

    def label_items(self) -> dict[tuple[str, str], str]:
        return self._store._labels.as_dict()

    def ordinals(self) -> GroupOrdinals:
        ordinals = GroupOrdinals()
        ordinals.replace(self._store._ordinals.as_dict())
        return ordinals

    def ordinal_items(self) -> dict[str, dict[str, int]]:
        return self._store._ordinals.as_dict()

    def effects(self) -> EffectChainIndex:
        return deepcopy(self._store._effects)

    def collapsed_headers(self) -> frozenset[CollapsedHeaderKey]:
        return frozenset(self._store._collapsed_headers)

    def locked_keys(self) -> frozenset[ParameterKey]:
        return frozenset(self._store._locked_keys)

    def favorite_keys(self) -> frozenset[ParameterKey]:
        return self._store._favorite_keys_snapshot()

    def favorite_keys_tuple(self) -> tuple[ParameterKey, ...]:
        return self._store._favorite_keys_tuple()

    def variations(self) -> tuple[Variation, ...]:
        return tuple(self._store._variations.values())

    def variation(self, name: str) -> Variation | None:
        return self._store._variations.get(name)

    def variations_by_name(self) -> dict[str, Variation]:
        return dict(self._store._variations)

    def runtime(self) -> ParamStoreRuntime:
        return deepcopy(self._store._runtime)

    def runtime_token(self) -> int:
        """merge cache 用の opaque identity token を返す。"""

        return id(self._store._runtime)

    def snapshot_rows(
        self,
    ) -> dict[
        ParameterKey,
        tuple[ParamMeta, ParamStateSnapshot, int, str | None],
    ]:
        """snapshot 構築に必要な immutable row を一度に固定する。"""

        rows: dict[
            ParameterKey,
            tuple[ParamMeta, ParamStateSnapshot, int, str | None],
        ] = {}
        store = self._store
        for key, state in store._states.items():
            meta = store._meta.get(key)
            if meta is None:
                continue
            ordinal = store._ordinals.get(key.op, key.site_id)
            if ordinal is None:
                raise RuntimeError(
                    "ParamStore の不変条件違反: ordinal が未割り当ての group がある"
                    f": op={key.op!r}, site_id={key.site_id!r}"
                )
            rows[key] = (
                meta,
                ParamStateSnapshot.from_state(state),
                int(ordinal),
                store._labels.get(key.op, key.site_id),
            )
        return rows

    def snapshot_row(
        self,
        key: ParameterKey,
    ) -> tuple[ParamMeta, ParamStateSnapshot, int, str | None] | None:
        store = self._store
        state = store._states.get(key)
        meta = store._meta.get(key)
        if state is None or meta is None:
            return None
        ordinal = store._ordinals.get(key.op, key.site_id)
        if ordinal is None:
            return None
        return (
            meta,
            ParamStateSnapshot.from_state(state),
            int(ordinal),
            store._labels.get(key.op, key.site_id),
        )

    def visible_groups(
        self,
    ) -> tuple[frozenset[tuple[str, str]], frozenset[tuple[str, str]]]:
        runtime = self._store._runtime
        return (
            frozenset(runtime.loaded_groups),
            frozenset(runtime.observed_groups),
        )

    def reconcile_status(self) -> tuple[bool, bool, bool]:
        """reconcile の early-exit 判定だけを allocation 無しで返す。"""

        runtime = self._store._runtime
        return (
            bool(runtime.loaded_groups),
            bool(runtime.observed_groups),
            bool(runtime.reconcile_orphans),
        )


class _ParamStoreMutation:
    """検証・allocation 済み replacement だけを確定する内部 write port。

    commit method は live container を返さない。呼び出し側は read port の copy
    上で plan を完成させ、history の変更前観測も ``prepare_history`` で先に
    終える。commit 区間は revision の再確認、参照 swap、counter/cache 更新だけ
    に限定する。
    """

    __slots__ = ("_store",)

    def __init__(self, store: ParamStore) -> None:
        self._store = store

    def prepare_history(
        self,
        *,
        expected_revision: int,
        keys: tuple[ParameterKey, ...] = (),
        headers: frozenset[CollapsedHeaderKey] | None = None,
        observe_headers: bool = False,
    ) -> None:
        """commit より前に history observer へ変更前値を渡す。"""

        self._require_revision(expected_revision)
        for key in keys:
            self._store._observe_history_key_before(key)
        if observe_headers:
            self._store._observe_history_headers_before(headers)

    def commit_parameter_state(
        self,
        *,
        expected_revision: int,
        states: dict[ParameterKey, ParamState],
        meta: dict[ParameterKey, ParamMeta],
        explicit_by_key: dict[ParameterKey, bool],
        structure: bool,
        value_keys: tuple[ParameterKey, ...],
    ) -> None:
        self._replace(
            expected_revision=expected_revision,
            states=states,
            meta=meta,
            explicit_by_key=explicit_by_key,
            structure=structure,
            value_keys=value_keys,
        )

    def commit_existing_parameter_state(
        self,
        *,
        expected_revision: int,
        key: ParameterKey,
        state: ParamState,
        value_changed: bool,
    ) -> None:
        """既存 key の完成済み ParamState object だけを差し替える。"""

        self._require_revision(expected_revision)
        self._store._states[key] = state
        self._store._commit_prepared_mutation(
            touched=True,
            structure=False,
            value_keys=(key,) if value_changed else (),
            favorites=False,
        )

    def commit_adjustment_values(
        self,
        *,
        expected_revision: int,
        states: dict[ParameterKey, ParamState],
        value_keys: tuple[ParameterKey, ...],
    ) -> None:
        self._replace(
            expected_revision=expected_revision,
            states=states,
            structure=False,
            value_keys=value_keys,
        )

    def commit_adjustment_restore(
        self,
        *,
        expected_revision: int,
        states: dict[ParameterKey, ParamState],
        meta: dict[ParameterKey, ParamMeta],
        effects: EffectChainIndex,
        collapsed_headers: set[CollapsedHeaderKey],
        structure: bool,
        value_keys: tuple[ParameterKey, ...],
    ) -> None:
        self._replace(
            expected_revision=expected_revision,
            states=states,
            meta=meta,
            effects=effects,
            collapsed_headers=collapsed_headers,
            structure=structure,
            value_keys=value_keys,
        )

    def commit_adjustment_patch(
        self,
        *,
        expected_revision: int,
        states: dict[ParameterKey, ParamState],
        meta: dict[ParameterKey, ParamMeta],
        collapsed_headers: set[CollapsedHeaderKey],
        structure: bool,
        value_keys: tuple[ParameterKey, ...],
    ) -> None:
        self._replace(
            expected_revision=expected_revision,
            states=states,
            meta=meta,
            collapsed_headers=collapsed_headers,
            structure=structure,
            value_keys=value_keys,
        )

    def commit_collapsed_headers(
        self,
        *,
        expected_revision: int,
        collapsed_headers: set[CollapsedHeaderKey],
    ) -> None:
        self._replace(
            expected_revision=expected_revision,
            collapsed_headers=collapsed_headers,
            structure=False,
        )

    def commit_meta(
        self,
        *,
        expected_revision: int,
        meta: dict[ParameterKey, ParamMeta],
    ) -> None:
        self._replace(
            expected_revision=expected_revision,
            meta=meta,
            structure=True,
        )

    def commit_parameter_edits(
        self,
        *,
        expected_revision: int,
        states: dict[ParameterKey, ParamState],
        meta: dict[ParameterKey, ParamMeta],
        explicit_by_key: dict[ParameterKey, bool],
        favorite_keys: set[ParameterKey],
        structure: bool,
        value_keys: tuple[ParameterKey, ...],
        favorites_changed: bool,
    ) -> None:
        self._replace(
            expected_revision=expected_revision,
            states=states,
            meta=meta,
            explicit_by_key=explicit_by_key,
            favorite_keys=favorite_keys,
            structure=structure,
            value_keys=value_keys,
            favorites_changed=favorites_changed,
        )

    def commit_labels(
        self,
        *,
        expected_revision: int,
        labels: ParamLabels,
    ) -> None:
        self._replace(
            expected_revision=expected_revision,
            labels=labels,
            structure=True,
        )

    def commit_effects(
        self,
        *,
        expected_revision: int,
        effects: EffectChainIndex,
        collapsed_headers: set[CollapsedHeaderKey],
    ) -> None:
        self._replace(
            expected_revision=expected_revision,
            effects=effects,
            collapsed_headers=collapsed_headers,
            structure=True,
        )

    def commit_effect_observation_start(
        self,
        *,
        expected_revision: int,
        effects: EffectChainIndex,
    ) -> None:
        """公開状態を変えない observation marker だけを置換する。"""

        self._require_revision(expected_revision)
        self._store._effects = effects

    def commit_favorites(
        self,
        *,
        expected_revision: int,
        favorite_keys: set[ParameterKey],
    ) -> None:
        self._replace(
            expected_revision=expected_revision,
            favorite_keys=favorite_keys,
            favorites_changed=True,
        )

    def commit_locks(
        self,
        *,
        expected_revision: int,
        locked_keys: set[ParameterKey],
    ) -> None:
        self._replace(
            expected_revision=expected_revision,
            locked_keys=locked_keys,
            structure=False,
        )

    def commit_variations(
        self,
        *,
        expected_revision: int,
        variations: dict[str, Variation],
    ) -> None:
        self._replace(
            expected_revision=expected_revision,
            variations=variations,
            structure=False,
        )

    def commit_style(
        self,
        *,
        expected_revision: int,
        states: dict[ParameterKey, ParamState],
        meta: dict[ParameterKey, ParamMeta],
        explicit_by_key: dict[ParameterKey, bool],
        ordinals: GroupOrdinals,
        value_keys: tuple[ParameterKey, ...],
    ) -> None:
        self._replace(
            expected_revision=expected_revision,
            states=states,
            meta=meta,
            explicit_by_key=explicit_by_key,
            ordinals=ordinals,
            structure=True,
            value_keys=value_keys,
        )

    def commit_decoded(
        self,
        *,
        expected_revision: int,
        states: dict[ParameterKey, ParamState],
        meta: dict[ParameterKey, ParamMeta],
        explicit_by_key: dict[ParameterKey, bool],
        labels: ParamLabels,
        ordinals: GroupOrdinals,
        effects: EffectChainIndex,
        collapsed_headers: set[CollapsedHeaderKey],
        locked_keys: set[ParameterKey],
        favorite_keys: set[ParameterKey],
        variations: dict[str, Variation],
        runtime: ParamStoreRuntime,
    ) -> None:
        self._replace(
            expected_revision=expected_revision,
            states=states,
            meta=meta,
            explicit_by_key=explicit_by_key,
            labels=labels,
            ordinals=ordinals,
            effects=effects,
            collapsed_headers=collapsed_headers,
            locked_keys=locked_keys,
            favorite_keys=favorite_keys,
            variations=variations,
            runtime=runtime,
            structure=True,
            favorites_changed=bool(favorite_keys),
        )

    def commit_prune(
        self,
        *,
        expected_revision: int,
        states: dict[ParameterKey, ParamState],
        meta: dict[ParameterKey, ParamMeta],
        explicit_by_key: dict[ParameterKey, bool],
        labels: ParamLabels,
        ordinals: GroupOrdinals,
        effects: EffectChainIndex,
        collapsed_headers: set[CollapsedHeaderKey],
        locked_keys: set[ParameterKey],
        favorite_keys: set[ParameterKey],
        runtime: ParamStoreRuntime,
        favorites_changed: bool,
    ) -> None:
        self._replace(
            expected_revision=expected_revision,
            states=states,
            meta=meta,
            explicit_by_key=explicit_by_key,
            labels=labels,
            ordinals=ordinals,
            effects=effects,
            collapsed_headers=collapsed_headers,
            locked_keys=locked_keys,
            favorite_keys=favorite_keys,
            runtime=runtime,
            structure=True,
            favorites_changed=favorites_changed,
        )

    def commit_parameter_prune(
        self,
        *,
        expected_revision: int,
        states: dict[ParameterKey, ParamState],
        meta: dict[ParameterKey, ParamMeta],
        explicit_by_key: dict[ParameterKey, bool],
        locked_keys: set[ParameterKey],
        favorite_keys: set[ParameterKey],
        favorites_changed: bool,
    ) -> None:
        self._replace(
            expected_revision=expected_revision,
            states=states,
            meta=meta,
            explicit_by_key=explicit_by_key,
            locked_keys=locked_keys,
            favorite_keys=favorite_keys,
            structure=True,
            favorites_changed=favorites_changed,
        )

    def commit_reconcile(
        self,
        *,
        expected_revision: int,
        states: dict[ParameterKey, ParamState],
        meta: dict[ParameterKey, ParamMeta],
        explicit_by_key: dict[ParameterKey, bool],
        labels: ParamLabels,
        ordinals: GroupOrdinals,
        collapsed_headers: set[CollapsedHeaderKey],
        locked_keys: set[ParameterKey],
        favorite_keys: set[ParameterKey],
        runtime: ParamStoreRuntime,
        favorites_changed: bool,
        value_keys: tuple[ParameterKey, ...],
    ) -> None:
        self._require_revision(expected_revision)
        store = self._store
        store._states = states
        store._meta = meta
        store._explicit_by_key = explicit_by_key
        store._labels = labels
        store._ordinals = ordinals
        store._collapsed_headers = collapsed_headers
        store._locked_keys = locked_keys
        store._favorite_keys_data = favorite_keys
        self._install_runtime_contents(
            store._runtime,
            runtime,
            value_keys=(),
        )
        store._commit_prepared_mutation(
            touched=True,
            structure=True,
            value_keys=value_keys,
            favorites=favorites_changed,
        )

    def commit_merge(
        self,
        *,
        expected_revision: int,
        states: dict[ParameterKey, ParamState],
        meta: dict[ParameterKey, ParamMeta],
        explicit_by_key: dict[ParameterKey, bool],
        labels: ParamLabels,
        ordinals: GroupOrdinals,
        collapsed_headers: set[CollapsedHeaderKey],
        locked_keys: set[ParameterKey],
        favorite_keys: set[ParameterKey],
        runtime: ParamStoreRuntime,
        runtime_value_keys: tuple[ParameterKey, ...],
        structure: bool,
        value_keys: tuple[ParameterKey, ...],
        favorites_changed: bool,
    ) -> None:
        self._require_revision(expected_revision)
        store = self._store
        store._states = states
        store._meta = meta
        store._explicit_by_key = explicit_by_key
        store._labels = labels
        store._ordinals = ordinals
        store._collapsed_headers = collapsed_headers
        store._locked_keys = locked_keys
        store._favorite_keys_data = favorite_keys
        self._install_runtime_contents(
            store._runtime,
            runtime,
            value_keys=runtime_value_keys,
        )
        store._commit_prepared_mutation(
            touched=True,
            structure=structure,
            value_keys=value_keys,
            favorites=favorites_changed,
        )

    def commit_full_replacement(
        self,
        *,
        expected_revision: int,
        states: dict[ParameterKey, ParamState],
        meta: dict[ParameterKey, ParamMeta],
        explicit_by_key: dict[ParameterKey, bool],
        labels: ParamLabels,
        ordinals: GroupOrdinals,
        effects: EffectChainIndex,
        collapsed_headers: set[CollapsedHeaderKey],
        locked_keys: set[ParameterKey],
        favorite_keys: set[ParameterKey],
        variations: dict[str, Variation],
        runtime: ParamStoreRuntime,
    ) -> None:
        """全 domain と counter/cache を replacement として一度に確定する。"""

        self._require_revision(expected_revision)
        store = self._store
        next_effective_revision = store._runtime.effective_revision + 1
        next_visibility_revision = store._runtime.visibility_revision + 1

        store._states = states
        store._meta = meta
        store._explicit_by_key = explicit_by_key
        store._labels = labels
        store._ordinals = ordinals
        store._effects = effects
        store._collapsed_headers = collapsed_headers
        store._locked_keys = locked_keys
        store._favorite_keys_data = favorite_keys
        store._variations = variations
        store._runtime = runtime

        store._revision += 1
        store._table_revision += 1
        store._value_revision += 1
        store._style_revision += 1
        store._favorite_revision += 1
        store._favorite_snapshot_revision = -1
        store._favorite_snapshot = frozenset()
        store._favorite_tuple = ()
        store._value_change_log.clear()
        store._snapshot_cache_revision = -1
        store._snapshot_cache_value_revision = -1
        store._snapshot_cache_rebuilt_entries = 0
        store._snapshot_cache = None

        runtime.effective_revision = next_effective_revision
        runtime._effective_change_revision = -1
        runtime._effective_changed_keys = ()
        runtime._visibility_tracker.revision = next_visibility_revision

    def commit_runtime(
        self,
        *,
        expected_revision: int,
        runtime: ParamStoreRuntime,
        runtime_value_keys: tuple[ParameterKey, ...] = (),
    ) -> None:
        self._require_revision(expected_revision)
        self._install_runtime_contents(
            self._store._runtime,
            runtime,
            value_keys=runtime_value_keys,
        )

    def commit_runtime_value_patch(
        self,
        *,
        expected_revision: int,
        expected_effective_revision: int,
        updates: tuple[tuple[ParameterKey, object, ValueSource], ...],
        changed_keys: tuple[ParameterKey, ...],
    ) -> None:
        """完成済み sparse effective/source patch だけを runtime へ反映する。"""

        self._require_revision(expected_revision)
        runtime = self._store._runtime
        if runtime.effective_revision != expected_effective_revision:
            raise RuntimeError(
                "parameter runtime changed while planning mutation"
            )
        next_effective_revision = expected_effective_revision + 1
        for key, effective, source in updates:
            runtime.last_effective_by_key[key] = effective
            runtime.last_source_by_key[key] = source
        runtime.effective_revision = next_effective_revision
        runtime._effective_change_revision = next_effective_revision
        runtime._effective_changed_keys = changed_keys

    @staticmethod
    def _install_runtime_contents(
        target: ParamStoreRuntime,
        source: ParamStoreRuntime,
        *,
        value_keys: tuple[ParameterKey, ...],
    ) -> None:
        """完成済み runtime plan を既存 identity へ failure-free に移す。"""

        tracker = target._visibility_tracker
        loaded_groups = source.loaded_groups
        observed_groups = source.observed_groups
        loaded_groups.bind(tracker)  # type: ignore[attr-defined]
        observed_groups.bind(tracker)  # type: ignore[attr-defined]
        object.__setattr__(target, "loaded_groups", loaded_groups)
        object.__setattr__(target, "observed_groups", observed_groups)
        target.reconcile_applied = source.reconcile_applied
        target.display_order_by_group = source.display_order_by_group
        target.next_display_order = source.next_display_order
        target.warned_unknown_args = source.warned_unknown_args
        for key in value_keys:
            if key in source.last_effective_by_key:
                target.last_effective_by_key[key] = (
                    source.last_effective_by_key[key]
                )
            else:
                target.last_effective_by_key.pop(key, None)
            if key in source.last_source_by_key:
                target.last_source_by_key[key] = source.last_source_by_key[key]
            else:
                target.last_source_by_key.pop(key, None)
        target.reconcile_orphans = source.reconcile_orphans
        target.effective_revision = source.effective_revision
        target._effective_change_revision = source._effective_change_revision
        target._effective_changed_keys = source._effective_changed_keys
        tracker.revision = source.visibility_revision

    def _replace(
        self,
        *,
        expected_revision: int,
        states: dict[ParameterKey, ParamState] | None = None,
        meta: dict[ParameterKey, ParamMeta] | None = None,
        explicit_by_key: dict[ParameterKey, bool] | None = None,
        labels: ParamLabels | None = None,
        ordinals: GroupOrdinals | None = None,
        effects: EffectChainIndex | None = None,
        collapsed_headers: set[CollapsedHeaderKey] | None = None,
        locked_keys: set[ParameterKey] | None = None,
        favorite_keys: set[ParameterKey] | None = None,
        variations: dict[str, Variation] | None = None,
        runtime: ParamStoreRuntime | None = None,
        structure: bool = False,
        value_keys: tuple[ParameterKey, ...] = (),
        favorites_changed: bool = False,
    ) -> None:
        """完成済み replacement を swap する。validation/callback は行わない。"""

        self._require_revision(expected_revision)
        store = self._store
        if states is not None:
            store._states = states
        if meta is not None:
            store._meta = meta
        if explicit_by_key is not None:
            store._explicit_by_key = explicit_by_key
        if labels is not None:
            store._labels = labels
        if ordinals is not None:
            store._ordinals = ordinals
        if effects is not None:
            store._effects = effects
        if collapsed_headers is not None:
            store._collapsed_headers = collapsed_headers
        if locked_keys is not None:
            store._locked_keys = locked_keys
        if favorite_keys is not None:
            store._favorite_keys_data = favorite_keys
        if variations is not None:
            store._variations = variations
        if runtime is not None:
            store._runtime = runtime
        store._commit_prepared_mutation(
            touched=bool(
                structure
                or value_keys
                or (
                    states is not None
                    or meta is not None
                    or explicit_by_key is not None
                    or labels is not None
                    or ordinals is not None
                    or effects is not None
                    or collapsed_headers is not None
                    or locked_keys is not None
                    or variations is not None
                )
            ),
            structure=structure,
            value_keys=value_keys,
            favorites=favorites_changed,
        )

    def _require_revision(self, expected_revision: int) -> None:
        if self._store.revision != expected_revision:
            raise RuntimeError("parameter store changed while planning mutation")


def _known_adjustment_headers(
    states: Mapping[ParameterKey, ParamState],
    effects: EffectChainIndex,
) -> set[CollapsedHeaderKey]:
    """現在の store 構造から存在し得る GUI header ID を返す。"""

    groups = {(key.op, key.site_id) for key in states}
    known: set[CollapsedHeaderKey] = set()
    style_ops = {"__style__", "__layer_style__"}
    if any(op in style_ops for op, _site_id in groups):
        known.add(STYLE_COLLAPSED_HEADER_KEY)
    for op, site_id in groups:
        if op in style_ops:
            continue
        # core は preset registry に依存しないため、同じ group が取り得る
        # primitive/preset の両 identity を保存する。
        known.update(group_collapsed_header_keys((op, site_id)))

    for group, (chain_id, _step_index) in effects.step_info_by_site().items():
        if group in groups:
            known.add(effect_chain_collapsed_header_key(chain_id))
    return known


class ParamStoreRollback:
    """一つの ParamStore に属する one-shot transient rollback scope。"""

    __slots__ = ("_active", "_state", "_store", "_used")

    def __init__(self, store: ParamStore) -> None:
        self._store = store
        self._state: _TransientParamStoreState | None = None
        self._active = False
        self._used = False

    def __enter__(self) -> ParamStoreRollback:
        """開始時の論理状態を退避し、この scope を有効にする。"""

        if self._used:
            raise RuntimeError("ParamStoreRollback is one-shot")
        self._used = True
        store = self._store
        store._begin_transient_rollback(self)
        try:
            self._state = store._capture_transient_state()
        except BaseException:
            store._end_transient_rollback(self)
            raise
        self._active = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """終了理由にかかわらず開始時の論理状態へ戻す。"""

        if not self._active:
            raise RuntimeError("ParamStoreRollback is not active")
        store = self._store
        try:
            store._restore_transient_rollback(self)
        finally:
            store._end_transient_rollback(self)
            self._active = False
            self._state = None


class ParamStore:
    """ParameterKey -> ParamState を保持する永続ストア。

    Notes
    -----
    - このクラスは「永続データの入れ物」に寄せる。
    - parameter lock / favorite は永続 UI state として保持する。
    - 外部へはミュータブルな参照（ParamState）を渡さない。
      変更は ops 経由で行う想定とする。
    """

    def __init__(self) -> None:
        self._states: dict[ParameterKey, ParamState] = {}
        self._meta: dict[ParameterKey, ParamMeta] = {}
        self._explicit_by_key: dict[ParameterKey, bool] = {}

        self._labels = ParamLabels()
        self._ordinals = GroupOrdinals()
        self._effects = EffectChainIndex()
        self._collapsed_headers: set[CollapsedHeaderKey] = set()
        self._locked_keys: set[ParameterKey] = set()
        self._favorite_keys_data: set[ParameterKey] = set()
        self._variations: dict[str, Variation] = {}

        # 永続化しない実行時情報（loaded/observed/reconcile-applied）。
        self._runtime = ParamStoreRuntime()
        self._revision = 0
        self._table_revision = 0
        self._value_revision = 0
        self._style_revision = 0
        self._favorite_revision = 0
        self._favorite_snapshot_revision = -1
        self._favorite_snapshot: frozenset[ParameterKey] = frozenset()
        self._favorite_tuple: tuple[ParameterKey, ...] = ()
        self._value_change_log: deque[tuple[int, tuple[ParameterKey, ...]]] = deque(
            maxlen=4096
        )
        self._history_key_observer: Callable[[ParameterKey], None] | None = None
        self._history_headers_observer: (
            Callable[[frozenset[CollapsedHeaderKey] | None], None] | None
        ) = None
        self._history_transaction_owner: object | None = None
        self._active_transient_rollback: ParamStoreRollback | None = None
        self._snapshot_cache_revision = -1
        self._snapshot_cache_value_revision = -1
        self._snapshot_cache_rebuilt_entries = 0
        self._snapshot_cache: object | None = None
        self._read_port = _ParamStoreRead(self)
        self._mutation_port = _ParamStoreMutation(self)

    @property
    def revision(self) -> int:
        """snapshot/model に影響する永続状態の変更時だけ増える単調 revision。"""

        return self._revision

    @property
    def effective_revision(self) -> int:
        """直近 frame の effective/source snapshot が変わるたびに増える revision。"""

        return self._runtime.effective_revision

    @property
    def table_revision(self) -> int:
        """Parameter GUI の行構造・静的属性が変わったときだけ増える revision。"""

        return self._table_revision

    @property
    def value_revision(self) -> int:
        """既存 parameter の表示値が変わったときだけ増える revision。"""

        return self._value_revision

    @property
    def style_revision(self) -> int:
        """global/layer style の値または関連し得る構造が変わる revision。"""

        return self._style_revision

    @property
    def favorite_revision(self) -> int:
        """favorite 集合が変化したときだけ増える単調 revision。"""

        return self._favorite_revision

    def replace_contents_from(self, source: ParamStore) -> None:
        """object identity を保ち、別 store の全内容へ一度に置換する。

        Parameters
        ----------
        source : ParamStore
            置換後の内容を所有する別の store。

        Raises
        ------
        TypeError
            ``source`` が exact ``ParamStore`` でない場合。
        ValueError
            自分自身を ``source`` に指定した場合。
        RuntimeError
            history transaction の途中で置換しようとした場合。
        """

        if type(source) is not ParamStore:
            raise TypeError("source must be a ParamStore")
        if source is self:
            raise ValueError("source must be a different ParamStore")
        if (
            self._history_transaction_owner is not None
            or self._history_key_observer is not None
            or self._history_headers_observer is not None
        ):
            raise RuntimeError("cannot replace ParamStore during a history transaction")

        base_revision = self.revision
        (
            states,
            meta,
            explicit_by_key,
            labels,
            ordinals,
            effects,
            collapsed_headers,
            locked_keys,
            favorite_keys,
            variations,
            runtime,
        ) = deepcopy(
            (
                source._states,
                source._meta,
                source._explicit_by_key,
                source._labels,
                source._ordinals,
                source._effects,
                source._collapsed_headers,
                source._locked_keys,
                set(source._favorite_keys_snapshot()),
                source._variations,
                source._runtime,
            )
        )

        self._mutation().commit_full_replacement(
            expected_revision=base_revision,
            states=states,
            meta=meta,
            explicit_by_key=explicit_by_key,
            labels=labels,
            ordinals=ordinals,
            effects=effects,
            collapsed_headers=collapsed_headers,
            locked_keys=locked_keys,
            favorite_keys=favorite_keys,
            variations=variations,
            runtime=runtime,
        )

    def begin_transient_rollback(self) -> ParamStoreRollback:
        """終了時に現在の論理状態へ正確に戻す one-shot scope を返す。

        Returns
        -------
        ParamStoreRollback
            正常終了・例外終了の双方で開始時の状態へ戻す context manager。

        Notes
        -----
        この scope は variation batch のような一時評価用であり、Undo/Redo
        history には記録しない。scope と active history transaction は相互に
        nest できない。
        """

        return ParamStoreRollback(self)

    def runtime_view(self) -> ParamRuntimeView:
        """GUI が必要とする runtime 情報の read-only view を返す。"""

        runtime = self._runtime
        # key/value は FrameParamRecord 境界で canonical immutable value に
        # 固定済みなので、mapping 自体の浅い snapshot だけを所有すればよい。
        return ParamRuntimeView(
            loaded_groups=frozenset(runtime.loaded_groups),
            observed_groups=frozenset(runtime.observed_groups),
            display_order_by_group=MappingProxyType(
                dict(runtime.display_order_by_group)
            ),
            last_effective_by_key=MappingProxyType(
                dict(runtime.last_effective_by_key)
            ),
            last_source_by_key=MappingProxyType(
                dict(runtime.last_source_by_key)
            ),
            effective_revision=int(runtime.effective_revision),
            visibility_revision=int(runtime.visibility_revision),
        )

    def effective_changes_since(
        self,
        revision: int,
    ) -> frozenset[ParameterKey] | None:
        """指定 runtime revision 以降の effective/source 変更 key を返す。"""

        return self._runtime.effective_changes_since(revision)

    def last_effective_value(self, key: ParameterKey) -> object | None:
        """直近 frame の effective value を返す。未観測なら ``None``。"""

        return self._runtime.last_effective_by_key.get(key)

    def record_unknown_argument_warnings(
        self,
        pairs: Iterable[tuple[str, str]],
    ) -> frozenset[tuple[str, str]]:
        """未警告の operation/argument 組を記録し、新規分だけ返す。"""

        warned = self._runtime.warned_unknown_args
        new_pairs = frozenset(pairs) - warned
        warned.update(new_pairs)
        return new_pairs

    def collapsed_headers(self) -> frozenset[CollapsedHeaderKey]:
        """現在の折りたたみ header の immutable snapshot を返す。"""

        return frozenset(self._collapsed_headers)

    def set_collapsed(
        self,
        header: CollapsedHeaderKey,
        *,
        collapsed: bool,
    ) -> bool:
        """一つの header の折りたたみ状態を変更する。"""

        return bool(self.set_all_collapsed((header,), collapsed=collapsed))

    def set_all_collapsed(
        self,
        headers: Iterable[CollapsedHeaderKey],
        *,
        collapsed: bool,
    ) -> tuple[CollapsedHeaderKey, ...]:
        """複数 header を一括変更し、実際に変わった header を返す。"""

        if type(collapsed) is not bool:
            raise TypeError("collapsed must be an exact bool")
        ordered = tuple(dict.fromkeys(headers))
        if not all(isinstance(header, CollapsedHeaderKey) for header in ordered):
            raise TypeError("headers must contain only CollapsedHeaderKey values")
        base_revision = self.revision
        current = self._collapsed_headers
        changed = tuple(
            header
            for header in ordered
            if (header in current) != collapsed
        )
        if not changed:
            return ()
        before = frozenset(current)
        planned = set(current)
        if collapsed:
            planned.update(changed)
        else:
            planned.difference_update(changed)
        mutation = self._mutation()
        mutation.prepare_history(
            expected_revision=base_revision,
            headers=before,
            observe_headers=True,
        )
        mutation.commit_collapsed_headers(
            expected_revision=base_revision,
            collapsed_headers=planned,
        )
        return changed

    def replace_collapsed_headers(
        self,
        headers: Iterable[CollapsedHeaderKey],
    ) -> bool:
        """折りたたみ header 全体を一度の command として置換する。"""

        normalized = set(headers)
        if not all(isinstance(header, CollapsedHeaderKey) for header in normalized):
            raise TypeError("headers must contain only CollapsedHeaderKey values")
        before = frozenset(self._collapsed_headers)
        if normalized == self._collapsed_headers:
            return False
        base_revision = self.revision
        mutation = self._mutation()
        mutation.prepare_history(
            expected_revision=base_revision,
            headers=before,
            observe_headers=True,
        )
        mutation.commit_collapsed_headers(
            expected_revision=base_revision,
            collapsed_headers=normalized,
        )
        return True

    def variation_count(self) -> int:
        """保存済み named variation の件数を返す。"""

        return len(self._variations)

    def get_state(self, key: ParameterKey) -> ParamState | None:
        """登録済みの ParamState を返す。未登録なら None。"""

        state = self._states.get(key)
        if state is None:
            return None
        return ParamState(**vars(state))

    def get_meta(self, key: ParameterKey) -> ParamMeta | None:
        """登録済みの ParamMeta を返す。未登録なら None。"""

        return self._meta.get(key)

    def capture_adjustment_snapshot(self) -> ParameterAdjustmentSnapshot:
        """現在の GUI-owned adjustment を immutable snapshot で返す。"""

        known_headers = _known_adjustment_headers(self._states, self._effects)
        return ParameterAdjustmentSnapshot(
            adjustments={
                key: ParameterAdjustment(
                    state=ParamStateSnapshot.from_state(state),
                    meta=self._meta[key],
                )
                for key, state in self._states.items()
                if key in self._meta
            },
            collapsed_by_header={
                header: header in self._collapsed_headers
                for header in known_headers
            },
            effect_order_state=self._effects.order_state_by_chain(),
            effect_topology_signatures=self._effects.topology_signatures(),
        )

    def adjustment_snapshot_matches(
        self,
        snapshot: ParameterAdjustmentSnapshot,
    ) -> bool:
        """``snapshot`` の merge 適用で adjustment が変わらなければ True。"""

        if type(snapshot) is not ParameterAdjustmentSnapshot:
            raise TypeError("snapshot must be a ParameterAdjustmentSnapshot")

        for _key, saved, current_state, current_meta in self._applicable_adjustments(
            snapshot
        ):
            if (
                current_state.override != saved.state.override
                or current_state.ui_value != saved.state.ui_value
                or current_state.cc_key != saved.state.cc_key
                or current_meta.ui_min != saved.meta.ui_min
                or current_meta.ui_max != saved.meta.ui_max
            ):
                return False

        known_headers = _known_adjustment_headers(self._states, self._effects)
        for header, saved_collapsed in snapshot.collapsed_items():
            if header not in known_headers:
                continue
            if (header in self._collapsed_headers) != saved_collapsed:
                return False

        candidate_effects = deepcopy(self._effects)
        if candidate_effects.restore_order_state(
            dict(snapshot.effect_order_items()),
            topology_signatures=dict(snapshot.effect_topology_items()),
        ):
            return False
        return True

    def apply_adjustment_snapshot(
        self,
        snapshot: ParameterAdjustmentSnapshot,
    ) -> bool:
        """Snapshot を現在の code-owned 構造へ原子的に merge 適用する。"""

        if type(snapshot) is not ParameterAdjustmentSnapshot:
            raise TypeError("snapshot must be a ParameterAdjustmentSnapshot")

        base_revision = self.revision
        states = dict(self._states)
        meta_by_key = dict(self._meta)
        effects = deepcopy(self._effects)
        collapsed_headers = set(self._collapsed_headers)
        changed_value_keys: list[ParameterKey] = []
        structure_changed = False
        for key, saved in snapshot.items():
            current_state = self._states.get(key)
            current_meta = self._meta.get(key)
            if (
                current_state is None
                or current_meta is None
                or saved.meta.kind != current_meta.kind
            ):
                continue
            if (
                current_state.override != saved.state.override
                or current_state.ui_value != saved.state.ui_value
                or current_state.cc_key != saved.state.cc_key
            ):
                states[key] = ParamState(
                    override=saved.state.override,
                    ui_value=saved.state.ui_value,
                    cc_key=saved.state.cc_key,
                )
                changed_value_keys.append(key)

            if (
                current_meta.ui_min != saved.meta.ui_min
                or current_meta.ui_max != saved.meta.ui_max
            ):
                meta_by_key[key] = replace(
                    current_meta,
                    ui_min=saved.meta.ui_min,
                    ui_max=saved.meta.ui_max,
                )
                structure_changed = True

        known_headers = _known_adjustment_headers(self._states, self._effects)
        for header, saved_collapsed in snapshot.collapsed_items():
            if header not in known_headers:
                continue
            if saved_collapsed and header not in collapsed_headers:
                collapsed_headers.add(header)
                structure_changed = True
            elif not saved_collapsed and header in collapsed_headers:
                collapsed_headers.discard(header)
                structure_changed = True

        if effects.restore_order_state(
            dict(snapshot.effect_order_items()),
            topology_signatures=dict(snapshot.effect_topology_items()),
        ):
            structure_changed = True

        changed = structure_changed or bool(changed_value_keys)
        if changed:
            # Revision は過去値へ戻さず、restore 全体で一度だけ進める。
            self._mutation().commit_adjustment_restore(
                expected_revision=base_revision,
                states=states,
                meta=meta_by_key,
                effects=effects,
                collapsed_headers=collapsed_headers,
                structure=structure_changed,
                value_keys=tuple(changed_value_keys),
            )
        return changed

    def get_label(self, op: str, site_id: str) -> str | None:
        """(op, site_id) のラベルを返す。未登録なら None。"""

        return self._labels.get(op, site_id)

    def get_ordinal(self, op: str, site_id: str) -> int | None:
        """(op, site_id) の ordinal を返す。未登録なら None。"""

        return self._ordinals.get(op, site_id)

    def get_effect_step(self, op: str, site_id: str) -> tuple[str, int] | None:
        """(op, site_id) の effect ステップ情報を返す。未登録なら None。"""

        return self._effects.get_step(op, site_id)

    def effect_steps(self) -> dict[tuple[str, str], tuple[str, int]]:
        """(op, site_id) -> (chain_id, effective_index) のコピーを返す。"""

        return self._effects.step_info_by_site()

    def effect_chain_topologies(
        self,
    ) -> dict[str, tuple[EffectStepTopology, ...]]:
        """chain_id -> code topology のコピーを返す。"""

        return self._effects.topologies()

    def effect_order_overrides(self) -> dict[str, EffectOrder]:
        """chain_id -> GUI-owned order override のコピーを返す。"""

        return self._effects.order_overrides()

    def chain_ordinals(self) -> dict[str, int]:
        """chain_id -> ordinal のコピーを返す。"""

        return self._effects.chain_ordinals()

    # --- 内部 API（ops/codec からのみ利用する想定）---
    def _read(self) -> _ParamStoreRead:
        """core parameter module 用の参照を漏らさない read port を返す。"""

        return self._read_port

    def _mutation(self) -> _ParamStoreMutation:
        """core parameter module 用の限定 write port を返す。"""

        return self._mutation_port

    def _applicable_adjustments(
        self,
        snapshot: ParameterAdjustmentSnapshot,
    ) -> list[
        tuple[ParameterKey, ParameterAdjustment, ParamState, ParamMeta]
    ]:
        """現在の code-owned 構造へ安全に適用できる entry を返す。"""

        applicable: list[
            tuple[ParameterKey, ParameterAdjustment, ParamState, ParamMeta]
        ] = []
        for key, saved in snapshot.items():
            current_state = self._states.get(key)
            current_meta = self._meta.get(key)
            if current_state is None or current_meta is None:
                continue
            if saved.meta.kind != current_meta.kind:
                continue
            applicable.append((key, saved, current_state, current_meta))
        return applicable

    def _capture_adjustment_slot(
        self,
        key: ParameterKey,
    ) -> _ParameterAdjustmentSlot:
        """History patch 用に一 key の現在値を immutable 化する。"""

        state = self._states.get(key)
        return _ParameterAdjustmentSlot(
            state=None if state is None else ParamStateSnapshot.from_state(state),
            meta=self._meta.get(key),
        )

    def _apply_adjustment_patch(
        self,
        patch: ParameterAdjustmentPatch,
        *,
        after: bool,
    ) -> bool:
        """History patch の変更前または変更後を merge 適用する。"""

        if type(patch) is not ParameterAdjustmentPatch:
            raise TypeError("patch must be a ParameterAdjustmentPatch")

        base_revision = self.revision
        states = dict(self._states)
        meta_by_key = dict(self._meta)
        collapsed_headers = set(self._collapsed_headers)
        changed_value_keys: list[ParameterKey] = []
        meta_changed = False
        for key, saved in patch.parameter_items(after=after):
            saved_state = saved.state
            saved_meta = saved.meta
            current_state = self._states.get(key)
            current_meta = self._meta.get(key)
            if (
                saved_state is None
                or saved_meta is None
                or current_state is None
                or current_meta is None
                or saved_meta.kind != current_meta.kind
            ):
                continue

            state_changed = (
                current_state.override != saved_state.override
                or current_state.ui_value != saved_state.ui_value
                or current_state.cc_key != saved_state.cc_key
            )
            if state_changed:
                states[key] = ParamState(
                    override=saved_state.override,
                    ui_value=saved_state.ui_value,
                    cc_key=saved_state.cc_key,
                )
                changed_value_keys.append(key)

            if (
                current_meta.ui_min != saved_meta.ui_min
                or current_meta.ui_max != saved_meta.ui_max
            ):
                meta_by_key[key] = replace(
                    current_meta,
                    ui_min=saved_meta.ui_min,
                    ui_max=saved_meta.ui_max,
                )
                meta_changed = True

        headers_changed = False
        header_states = patch.collapsed_items(after=after)
        if header_states:
            known_headers = _known_adjustment_headers(self._states, self._effects)
            for header, should_collapse in header_states:
                if header not in known_headers:
                    continue
                if should_collapse and header not in collapsed_headers:
                    collapsed_headers.add(header)
                    headers_changed = True
                elif not should_collapse and header in collapsed_headers:
                    collapsed_headers.discard(header)
                    headers_changed = True

        if not changed_value_keys and not meta_changed and not headers_changed:
            return False
        self._mutation().commit_adjustment_patch(
            expected_revision=base_revision,
            states=states,
            meta=meta_by_key,
            collapsed_headers=collapsed_headers,
            structure=bool(meta_changed or headers_changed),
            value_keys=tuple(changed_value_keys),
        )
        return True

    def _apply_adjustment_values(
        self,
        adjustments: Iterable[tuple[ParameterKey, ParameterAdjustment]],
    ) -> tuple[ParameterKey, ...]:
        """GUI value/ownership/MIDI だけを一 revision で適用する。"""

        entries = tuple(adjustments)
        if any(type(key) is not ParameterKey for key, _adjustment in entries):
            raise TypeError("adjustment keys must be ParameterKey values")
        if any(
            type(adjustment) is not ParameterAdjustment
            for _key, adjustment in entries
        ):
            raise TypeError("adjustments must be ParameterAdjustment values")

        base_revision = self.revision
        states = dict(self._states)
        changed: list[ParameterKey] = []
        for key, adjustment in entries:
            current_state = states.get(key)
            current_meta = self._meta.get(key)
            if (
                current_state is None
                or current_meta is None
                or current_meta.kind != adjustment.meta.kind
            ):
                continue
            saved_state = adjustment.state
            if (
                current_state.override == saved_state.override
                and current_state.ui_value == saved_state.ui_value
                and current_state.cc_key == saved_state.cc_key
            ):
                continue
            states[key] = ParamState(
                override=saved_state.override,
                ui_value=saved_state.ui_value,
                cc_key=saved_state.cc_key,
            )
            changed.append(key)
        if changed:
            mutation = self._mutation()
            mutation.prepare_history(
                expected_revision=base_revision,
                keys=tuple(dict.fromkeys(changed)),
            )
            mutation.commit_adjustment_values(
                expected_revision=base_revision,
                states=states,
                value_keys=tuple(dict.fromkeys(changed)),
            )
        return tuple(changed)

    def _favorite_keys_snapshot(self) -> frozenset[ParameterKey]:
        """revision 内で同一 identity の immutable favorite 集合を返す。"""

        if self._favorite_snapshot_revision != self._favorite_revision:
            snapshot = frozenset(self._favorite_keys_data)
            self._favorite_snapshot = snapshot
            self._favorite_tuple = tuple(
                sorted(
                    snapshot,
                    key=lambda key: (key.op, key.site_id, key.arg),
                )
            )
            self._favorite_snapshot_revision = self._favorite_revision
        return self._favorite_snapshot

    def _favorite_keys_tuple(self) -> tuple[ParameterKey, ...]:
        self._favorite_keys_snapshot()
        return self._favorite_tuple

    def _commit_prepared_mutation(
        self,
        *,
        touched: bool,
        structure: bool,
        value_keys: tuple[ParameterKey, ...],
        favorites: bool,
    ) -> None:
        """検証・重複除去済みの mutation を revision/cache へ反映する。"""

        changed_keys = value_keys
        if not touched and not favorites:
            return
        self._revision += 1
        if favorites:
            self._favorite_revision += 1
            self._favorite_snapshot_revision = -1

        if structure:
            self._table_revision += 1
            # 構造変更には style parameter の追加・削除や復元も含まれる。
            # 呼び出し側が値 key を列挙できない bulk 経路でも、保持中 scene の
            # style overlay を取りこぼさないよう安全側へ倒す。
            self._style_revision += 1
        if changed_keys:
            self._value_revision += 1
            if not structure and any(
                key.op in {"__style__", "__layer_style__"}
                for key in changed_keys
            ):
                self._style_revision += 1
            self._value_change_log.append((self._value_revision, changed_keys))
        if structure:
            # label/meta/ordinal/entry cardinality が変わり得るため、value patch の
            # base としても使わない。value-only 変更では immutable な旧 snapshot
            # を次回差分構築の seed として保持する。
            self._snapshot_cache = None
            self._snapshot_cache_revision = -1
            self._snapshot_cache_value_revision = -1
        elif (
            not changed_keys
            and self._snapshot_cache is not None
            and self._snapshot_cache_value_revision == self._value_revision
        ):
            # collapse state など ParamSnapshot に含まれない変更では、同じ
            # immutable mapping をそのまま現 revision の cache として扱える。
            self._snapshot_cache_revision = self._revision

    def value_changes_since(
        self,
        revision: int,
    ) -> frozenset[ParameterKey] | None:
        """指定 value revision 以降の key を返す。log 欠落時は ``None``。"""

        since = int(revision)
        if since == self._value_revision:
            return frozenset()
        if since < 0 or since > self._value_revision:
            return None
        if not self._value_change_log:
            return None
        first_revision = self._value_change_log[0][0]
        if since < first_revision - 1:
            return None
        changed: set[ParameterKey] = set()
        for change_revision, keys in reversed(self._value_change_log):
            if change_revision <= since:
                break
            changed.update(keys)
        return frozenset(changed)

    def _capture_transient_state(self) -> _TransientParamStoreState:
        """observer/cache を除く論理状態と counter の独立 copy を返す。"""

        (
            states,
            meta,
            explicit_by_key,
            labels,
            ordinals,
            effects,
            collapsed_headers,
            locked_keys,
            favorite_keys,
            variations,
            runtime,
            value_change_log,
        ) = deepcopy(
            (
                self._states,
                self._meta,
                self._explicit_by_key,
                self._labels,
                self._ordinals,
                self._effects,
                self._collapsed_headers,
                self._locked_keys,
                self._favorite_keys_data,
                self._variations,
                self._runtime,
                self._value_change_log,
            )
        )
        return _TransientParamStoreState(
            states=states,
            meta=meta,
            explicit_by_key=explicit_by_key,
            labels=labels,
            ordinals=ordinals,
            effects=effects,
            collapsed_headers=collapsed_headers,
            locked_keys=locked_keys,
            favorite_keys=favorite_keys,
            variations=variations,
            runtime=runtime,
            revision=self._revision,
            table_revision=self._table_revision,
            value_revision=self._value_revision,
            style_revision=self._style_revision,
            favorite_revision=self._favorite_revision,
            value_change_log=value_change_log,
        )

    def _begin_transient_rollback(self, rollback: ParamStoreRollback) -> None:
        """rollback の owner/nesting を検証し、active scope として登録する。"""

        if rollback._store is not self:
            raise ValueError("rollback belongs to a different ParamStore")
        if self._history_transaction_owner is not None:
            raise RuntimeError(
                "cannot begin transient rollback during a history transaction"
            )
        if (
            self._history_key_observer is not None
            or self._history_headers_observer is not None
        ):
            raise RuntimeError(
                "cannot begin transient rollback during a history transaction"
            )
        if self._active_transient_rollback is not None:
            raise RuntimeError("transient rollback is already active")
        self._active_transient_rollback = rollback

    def _restore_transient_rollback(self, rollback: ParamStoreRollback) -> None:
        """owner の active rollback が保持する論理状態を直接復元する。"""

        if type(rollback) is not ParamStoreRollback:
            raise TypeError("rollback must be a ParamStoreRollback")
        if rollback._store is not self:
            raise ValueError("rollback belongs to a different ParamStore")
        if self._active_transient_rollback is not rollback or not rollback._active:
            raise RuntimeError("rollback is not active for this ParamStore")
        state = rollback._state
        if state is None:
            raise RuntimeError("rollback has no captured state")

        self._states = state.states
        self._meta = state.meta
        self._explicit_by_key = state.explicit_by_key
        self._labels = state.labels
        self._ordinals = state.ordinals
        self._effects = state.effects
        self._collapsed_headers = state.collapsed_headers
        self._locked_keys = state.locked_keys
        self._favorite_keys_data = state.favorite_keys
        self._variations = state.variations
        self._runtime = state.runtime
        self._revision = state.revision
        self._table_revision = state.table_revision
        self._value_revision = state.value_revision
        self._style_revision = state.style_revision
        self._favorite_revision = state.favorite_revision
        self._value_change_log = state.value_change_log

        # snapshot/favorite cache は scope 内で構築された値も開始前の値も再利用しない。
        # 復元した mutable state から次回 query 時に必ず再構築する。
        self._favorite_snapshot_revision = -1
        self._favorite_snapshot = frozenset()
        self._favorite_tuple = ()
        self._snapshot_cache_revision = -1
        self._snapshot_cache_value_revision = -1
        self._snapshot_cache_rebuilt_entries = 0
        self._snapshot_cache = None

    def _end_transient_rollback(self, rollback: ParamStoreRollback) -> None:
        """owner の active rollback marker を解除する。"""

        if rollback._store is not self:
            raise ValueError("rollback belongs to a different ParamStore")
        if self._active_transient_rollback is not rollback:
            raise RuntimeError("rollback is not active for this ParamStore")
        self._active_transient_rollback = None

    def _begin_history_transaction(self, owner: object) -> None:
        """history transaction owner を登録し、不正 nesting を拒否する。"""

        if self._active_transient_rollback is not None:
            raise RuntimeError(
                "cannot begin history transaction during a transient rollback"
            )
        if self._history_transaction_owner is not None:
            raise RuntimeError("history transaction is already active")
        self._history_transaction_owner = owner

    def _end_history_transaction(self, owner: object) -> None:
        """一致する history transaction owner の登録を解除する。"""

        if self._history_transaction_owner is not owner:
            raise RuntimeError("history transaction owner does not match")
        self._history_transaction_owner = None

    def _begin_history_patch_capture(
        self,
        *,
        observe_key: Callable[[ParameterKey], None],
        observe_headers: Callable[[frozenset[CollapsedHeaderKey] | None], None],
    ) -> None:
        """単一 GUI transaction の変更前値 observer を登録する。"""

        if self._history_key_observer is not None:
            raise RuntimeError("history patch capture is already active")
        self._history_key_observer = observe_key
        self._history_headers_observer = observe_headers

    def _end_history_patch_capture(self) -> None:
        """現在の GUI transaction observer を解除する。"""

        self._history_key_observer = None
        self._history_headers_observer = None

    def _observe_history_key_before(self, key: ParameterKey) -> None:
        observer = self._history_key_observer
        if observer is not None:
            observer(key)

    def _observe_history_headers_before(
        self,
        headers: frozenset[CollapsedHeaderKey] | None = None,
    ) -> None:
        observer = self._history_headers_observer
        if observer is not None:
            observer(headers)

    def _get_snapshot_cache(self) -> object | None:
        if self._snapshot_cache_revision != self._revision:
            return None
        return self._snapshot_cache

    def _get_snapshot_cache_seed(self) -> tuple[object, int] | None:
        """structure change 以降の immutable snapshot と value revision を返す。"""

        snapshot = self._snapshot_cache
        if snapshot is None or self._snapshot_cache_value_revision < 0:
            return None
        return snapshot, self._snapshot_cache_value_revision

    def _set_snapshot_cache(
        self,
        snapshot: object,
        *,
        rebuilt_entries: int,
    ) -> None:
        self._snapshot_cache = snapshot
        self._snapshot_cache_revision = self._revision
        self._snapshot_cache_value_revision = self._value_revision
        self._snapshot_cache_rebuilt_entries = int(rebuilt_entries)


__all__ = ["ParamStore", "ParamStoreRollback"]
