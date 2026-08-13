"""
Purpose:
    成功frameのparameter観測から構造・runtime差分とreconcileを計画し、storeへ確定する。
Use when:
    parameter発見、metadata追従、effective source更新、またはframe merge性能を変更する場合。
Constraints:
    - live storeから独立してplanを完成させ、失敗時にstate、history、cacheを部分更新しない。
    - persistentな複数変更は一commitへまとめ、no-opではrevisionを進めない。
    - effective/sourceだけの変更はsparse commitとし、persistent revisionとruntime identityを保つ。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast
from weakref import WeakKeyDictionary

from .collapsed_header import CollapsedHeaderKey
from .frame_params import FrameParamRecord
from .key import ParameterKey
from .labels import ParamLabels
from .meta import ParamMeta, merge_code_meta_with_stored_gui_meta
from .ordinals import GroupOrdinals
from .reconcile_ops import (
    _ReconcileState,
    _changed_existing_state_keys,
    _persistent_signature,
    _plan_loaded_group_reconciliation,
    _state_snapshots,
)
from .runtime import ParamStoreRuntime
from .source import ValueSource
from .state import ParamState, ParamStateSnapshot
from .store import ParamStore
from .view import (
    canonicalize_ui_value,
    canonicalize_ui_value_for_meta_change,
)

_MISSING = object()


@dataclass(slots=True)
class _StableMergeEntry:
    """stable record の構造と直近 runtime 値をまとめた内部 cache entry。"""

    group: tuple[str, str]
    meta: ParamMeta
    explicit: bool
    last_effective: object
    last_source: object
    runtime_frame_token: int = 0
    runtime_before_effective: object = _MISSING
    runtime_before_source: object = _MISSING
    runtime_differs: bool = False
    explicit_frame_token: int = 0
    explicit_in_frame: bool = False


@dataclass(slots=True)
class _StableMergeCache:
    """ParamStore の table/runtime 世代に追従する merge 専用 cache。"""

    table_revision: int = -1
    runtime_token: int = -1
    next_frame_token: int = 0
    entries: dict[ParameterKey, _StableMergeEntry] = field(default_factory=dict)


@dataclass(slots=True)
class _MergePlan:
    """live store から独立した merge replacement。"""

    states: dict[ParameterKey, ParamState]
    meta: dict[ParameterKey, ParamMeta]
    explicit_by_key: dict[ParameterKey, bool]
    labels: ParamLabels
    ordinals: GroupOrdinals
    collapsed_headers: set[CollapsedHeaderKey]
    locked_keys: set[ParameterKey]
    favorite_keys: set[ParameterKey]
    runtime: ParamStoreRuntime
    persistent_before: tuple[object, ...]
    state_before: dict[ParameterKey, ParamStateSnapshot]
    favorite_keys_before: frozenset[ParameterKey]
    persistent_changed: bool = False
    runtime_changed: bool = False
    reconcile_needed: bool = False
    history_keys: list[ParameterKey] = field(default_factory=list)
    runtime_value_keys: tuple[ParameterKey, ...] = ()
    value_keys: tuple[ParameterKey, ...] = ()


# ParamStore 自身へ hot-path 専用 field を増やさず、store の寿命と一緒に破棄する。
_CACHE_BY_STORE: WeakKeyDictionary[ParamStore, _StableMergeCache] = WeakKeyDictionary()


def merge_frame_params(store: ParamStore, records: list[FrameParamRecord]) -> None:
    """フレームを plan してから一度だけ commit する。"""

    read = store._read()
    base_revision = store.revision
    base_effective_revision = store.effective_revision
    cache = _cache_for_store(store)
    runtime_token = read.runtime_token()
    cache_invalidated = (
        cache.table_revision != store.table_revision
        or cache.runtime_token != runtime_token
    )
    if cache_invalidated:
        cache.entries.clear()
        cache.table_revision = store.table_revision
        cache.runtime_token = runtime_token

    cache.next_frame_token += 1
    frame_token = cache.next_frame_token
    runtime_changes: list[tuple[ParameterKey, _StableMergeEntry]] = []
    explicit_changes: list[tuple[ParameterKey, _StableMergeEntry]] = []
    runtime_patch: tuple[
        tuple[ParameterKey, object, ValueSource],
        ...,
    ] = ()
    plan: _MergePlan | None = None

    try:
        for rec in records:
            key = rec.key
            entry = cache.entries.get(key)
            if (
                entry is None
                or merge_code_meta_with_stored_gui_meta(rec.meta, entry.meta)
                != entry.meta
            ):
                if plan is None:
                    plan = _new_plan(store)
                    plan.reconcile_needed = cache_invalidated
                entry = _plan_structural_record(plan, rec, entry)
                cache.entries[key] = entry

            _plan_runtime_observation(
                key=key,
                rec=rec,
                entry=entry,
                frame_token=frame_token,
                runtime_changes=runtime_changes,
            )
            _record_explicit_change(
                key=key,
                explicit=rec.explicit,
                entry=entry,
                frame_token=frame_token,
                explicit_changes=explicit_changes,
            )

        if explicit_changes:
            if plan is None:
                plan = _new_plan(store)
            if _apply_explicit_override_follow_policy(
                plan,
                {
                    key: entry.explicit_in_frame
                    for key, entry in explicit_changes
                    if entry.explicit_in_frame != entry.explicit
                },
            ):
                plan.persistent_changed = True
                plan.reconcile_needed = True

        effective_changes = tuple(
            (key, entry)
            for key, entry in runtime_changes
            if entry.runtime_differs
        )
        if effective_changes:
            if plan is None:
                runtime_patch = tuple(
                    (
                        key,
                        entry.last_effective,
                        cast(ValueSource, entry.last_source),
                    )
                    for key, entry in effective_changes
                )
            else:
                for key, entry in effective_changes:
                    plan.runtime.last_effective_by_key[key] = (
                        entry.last_effective
                    )
                    plan.runtime.last_source_by_key[key] = cast(
                        ValueSource,
                        entry.last_source,
                    )
                plan.runtime.record_effective_changes(
                    key for key, _entry in effective_changes
                )
                plan.runtime_value_keys = tuple(
                    key for key, _entry in effective_changes
                )
                plan.runtime_changed = True

        if plan is None and cache_invalidated:
            has_loaded, has_observed, has_orphans = read.reconcile_status()
            if has_orphans or (has_loaded and has_observed):
                plan = _new_plan(store)
                plan.reconcile_needed = True

        if plan is not None:
            reconcile_state = _reconcile_state(plan)
            reconcile_value_keys: tuple[ParameterKey, ...] = ()
            if plan.reconcile_needed:
                reconcile_result = _plan_loaded_group_reconciliation(
                    reconcile_state
                )
                plan.runtime_changed = (
                    plan.runtime_changed or reconcile_result.runtime_changed
                )
                reconcile_value_keys = reconcile_result.value_keys

            plan.persistent_changed = (
                _persistent_signature(reconcile_state)
                != plan.persistent_before
            )
            changed_existing = _changed_existing_state_keys(
                plan.state_before,
                reconcile_state,
            )
            plan.value_keys = tuple(
                sorted(
                    {*changed_existing, *reconcile_value_keys},
                    key=lambda key: (key.op, key.site_id, key.arg),
                )
            )
    except BaseException:
        # planning failure は live store を一切変更していない。cache は途中まで
        # 更新し得るため破棄し、次 frame に store snapshot から再構築する。
        _invalidate_cache(cache)
        raise

    try:
        if plan is not None and (plan.persistent_changed or plan.runtime_changed):
            mutation = store._mutation()
            history_keys = tuple(dict.fromkeys(plan.history_keys))
            if plan.persistent_changed and history_keys:
                mutation.prepare_history(
                    expected_revision=base_revision,
                    keys=history_keys,
                )
            if plan.persistent_changed:
                mutation.commit_merge(
                    expected_revision=base_revision,
                    states=plan.states,
                    meta=plan.meta,
                    explicit_by_key=plan.explicit_by_key,
                    labels=plan.labels,
                    ordinals=plan.ordinals,
                    collapsed_headers=plan.collapsed_headers,
                    locked_keys=plan.locked_keys,
                    favorite_keys=plan.favorite_keys,
                    runtime=plan.runtime,
                    runtime_value_keys=plan.runtime_value_keys,
                    structure=True,
                    value_keys=plan.value_keys,
                    favorites_changed=(
                        plan.favorite_keys != plan.favorite_keys_before
                    ),
                )
            else:
                mutation.commit_runtime(
                    expected_revision=base_revision,
                    runtime=plan.runtime,
                    runtime_value_keys=plan.runtime_value_keys,
                )
        elif runtime_patch:
            changed_keys = tuple(key for key, _effective, _source in runtime_patch)
            mutation = store._mutation()
            mutation.commit_runtime_value_patch(
                expected_revision=base_revision,
                expected_effective_revision=base_effective_revision,
                updates=runtime_patch,
                changed_keys=changed_keys,
            )
    except BaseException:
        # history observer または revision 再確認が失敗しても、
        # 次 frame は live store を唯一の正として cache を作り直す。
        _invalidate_cache(cache)
        raise

    for key, entry in explicit_changes:
        current_explicit = store._read().explicit(key)
        if current_explicit is not None:
            entry.explicit = current_explicit
    cache.table_revision = store.table_revision
    cache.runtime_token = store._read().runtime_token()


def _new_plan(store: ParamStore) -> _MergePlan:
    """必要になった時だけ store components を copy する。"""

    read = store._read()
    states = read.states()
    meta = read.all_meta()
    explicit_by_key = read.all_explicit()
    labels = read.labels()
    ordinals = read.ordinals()
    collapsed_headers = set(read.collapsed_headers())
    locked_keys = set(read.locked_keys())
    favorite_keys = set(read.favorite_keys())
    runtime = read.runtime()
    reconcile_state = _ReconcileState(
        states=states,
        meta=meta,
        explicit_by_key=explicit_by_key,
        labels=labels,
        ordinals=ordinals,
        collapsed_headers=collapsed_headers,
        locked_keys=locked_keys,
        favorite_keys=favorite_keys,
        runtime=runtime,
    )
    return _MergePlan(
        states=states,
        meta=meta,
        explicit_by_key=explicit_by_key,
        labels=labels,
        ordinals=ordinals,
        collapsed_headers=collapsed_headers,
        locked_keys=locked_keys,
        favorite_keys=favorite_keys,
        runtime=runtime,
        persistent_before=_persistent_signature(reconcile_state),
        state_before=_state_snapshots(reconcile_state),
        favorite_keys_before=frozenset(favorite_keys),
    )


def _reconcile_state(plan: _MergePlan) -> _ReconcileState:
    """merge plan の container を reconciliation view として束ねる。"""

    return _ReconcileState(
        states=plan.states,
        meta=plan.meta,
        explicit_by_key=plan.explicit_by_key,
        labels=plan.labels,
        ordinals=plan.ordinals,
        collapsed_headers=plan.collapsed_headers,
        locked_keys=plan.locked_keys,
        favorite_keys=plan.favorite_keys,
        runtime=plan.runtime,
    )


def _plan_structural_record(
    plan: _MergePlan,
    rec: FrameParamRecord,
    entry: _StableMergeEntry | None,
) -> _StableMergeEntry:
    """一 record の構造変更を detached plan に反映する。"""

    key = rec.key
    group = (key.op, key.site_id)
    runtime = plan.runtime
    if group not in runtime.observed_groups:
        runtime.observed_groups.add(group)
        plan.runtime_changed = True
        plan.reconcile_needed = True
    if group not in runtime.display_order_by_group:
        runtime.display_order_by_group[group] = int(runtime.next_display_order)
        runtime.next_display_order += 1
        plan.runtime_changed = True
        plan.reconcile_needed = True

    if plan.ordinals.get(key.op, key.site_id) is None:
        plan.ordinals.get_or_assign(key.op, key.site_id)
        plan.persistent_changed = True
        plan.reconcile_needed = True

    if key not in plan.states:
        plan.states[key] = ParamState(
            override=not rec.explicit,
            ui_value=canonicalize_ui_value(rec.base, rec.meta),
        )
        plan.explicit_by_key[key] = rec.explicit
        plan.persistent_changed = True
        plan.reconcile_needed = True

    existing_meta = plan.meta.get(key)
    desired_meta = (
        rec.meta
        if existing_meta is None
        else merge_code_meta_with_stored_gui_meta(rec.meta, existing_meta)
    )
    state = plan.states.get(key)
    if (
        state is not None
        and existing_meta is not None
        and desired_meta.kind != existing_meta.kind
    ):
        state.ui_value = canonicalize_ui_value_for_meta_change(
            state.ui_value,
            rec.base,
            existing_meta,
            desired_meta,
        )
        plan.history_keys.append(key)
    if existing_meta != desired_meta:
        plan.meta[key] = desired_meta
        plan.history_keys.append(key)
        plan.persistent_changed = True
        plan.reconcile_needed = True

    if entry is None:
        return _StableMergeEntry(
            group=group,
            meta=desired_meta,
            explicit=plan.explicit_by_key[key],
            last_effective=runtime.last_effective_by_key.get(key, _MISSING),
            last_source=runtime.last_source_by_key.get(key, _MISSING),
        )

    # duplicate key が同一 frame 中に構造を変えても frame-local 状態は保持する。
    entry.group = group
    entry.meta = desired_meta
    return entry


def _cache_for_store(store: ParamStore) -> _StableMergeCache:
    cache = _CACHE_BY_STORE.get(store)
    if cache is None:
        cache = _StableMergeCache()
        _CACHE_BY_STORE[store] = cache
    return cache


def _invalidate_cache(cache: _StableMergeCache) -> None:
    """途中まで更新した merge cache を次 frame で再構築させる。"""

    cache.entries.clear()
    cache.table_revision = -1
    cache.runtime_token = -1


def _same_runtime_value(left: object, right: object) -> bool:
    """runtime 値を比較し、非 scalar の曖昧な比較は変更として扱う。"""

    if left is right:
        return True
    try:
        return bool(left == right)
    except (TypeError, ValueError):
        return False


def _plan_runtime_observation(
    *,
    key: ParameterKey,
    rec: FrameParamRecord,
    entry: _StableMergeEntry,
    frame_token: int,
    runtime_changes: list[tuple[ParameterKey, _StableMergeEntry]],
) -> None:
    """effective/source の最終差分を cache 上だけで plan する。"""

    effective_changed = not _same_runtime_value(rec.effective, entry.last_effective)
    source_changed = not _same_runtime_value(rec.source, entry.last_source)
    if not effective_changed and not source_changed:
        return

    if entry.runtime_frame_token != frame_token:
        entry.runtime_frame_token = frame_token
        entry.runtime_before_effective = entry.last_effective
        entry.runtime_before_source = entry.last_source
        entry.runtime_differs = False
        runtime_changes.append((key, entry))

    if effective_changed:
        entry.last_effective = rec.effective
    if source_changed:
        entry.last_source = rec.source
    entry.runtime_differs = not (
        _same_runtime_value(entry.last_effective, entry.runtime_before_effective)
        and _same_runtime_value(entry.last_source, entry.runtime_before_source)
    )


def _record_explicit_change(
    *,
    key: ParameterKey,
    explicit: bool,
    entry: _StableMergeEntry,
    frame_token: int,
    explicit_changes: list[tuple[ParameterKey, _StableMergeEntry]],
) -> None:
    """explicit の最終差分だけを follow policy へ渡す。"""

    if entry.explicit_frame_token == frame_token:
        entry.explicit_in_frame = explicit
        return
    if entry.explicit == explicit:
        return
    entry.explicit_frame_token = frame_token
    entry.explicit_in_frame = explicit
    explicit_changes.append((key, entry))


def _apply_explicit_override_follow_policy(
    plan: _MergePlan,
    explicit_by_key_this_frame: dict[ParameterKey, bool],
) -> bool:
    """detached state 上で explicit/implicit follow policy を適用する。"""

    changed = False
    for key, new_explicit in explicit_by_key_this_frame.items():
        prev_explicit = plan.explicit_by_key[key]
        if prev_explicit == new_explicit:
            continue

        state = plan.states.get(key)
        if state is not None:
            default_override_prev = not prev_explicit
            default_override_new = not new_explicit
            if state.override == default_override_prev:
                state.override = default_override_new
        plan.explicit_by_key[key] = new_explicit
        changed = True
    return changed


__all__ = ["merge_frame_params"]
