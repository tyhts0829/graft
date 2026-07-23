# どこで: `src/grafix/core/parameters/reconcile_ops.py`。
# 何を: loaded/observed の差分を再リンクし、グループの migrate を適用する。
# なぜ: site_id の揺れを吸収し、GUI の増殖と調整値の喪失を抑えるため。

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .collapsed_header import CollapsedHeaderKey, group_collapsed_header_keys
from .identity import GroupKey, group_key
from .key import ParameterKey
from .labels import ParamLabels
from .meta import ParamMeta
from .ordinals import GroupOrdinals
from .reconcile import (
    ReconcileOrphan,
    build_group_fingerprints,
    plan_group_reconciliation,
)
from .runtime import ParamStoreRuntime
from .state import ParamState, ParamStateSnapshot
from .store import ParamStore

if TYPE_CHECKING:
    from .history import ParamStoreHistory


@dataclass(slots=True)
class _ReconcileState:
    """live store から独立して migration を完成させる planning state。"""

    states: dict[ParameterKey, ParamState]
    meta: dict[ParameterKey, ParamMeta]
    explicit_by_key: dict[ParameterKey, bool]
    labels: ParamLabels
    ordinals: GroupOrdinals
    collapsed_headers: set[CollapsedHeaderKey]
    locked_keys: set[ParameterKey]
    favorite_keys: set[ParameterKey]
    runtime: ParamStoreRuntime


@dataclass(frozen=True, slots=True)
class _ReconcilePlanResult:
    """detached reconciliation が最終的に変更した domain。"""

    persistent_changed: bool
    runtime_changed: bool
    value_keys: tuple[ParameterKey, ...]

    @property
    def changed(self) -> bool:
        return self.persistent_changed or self.runtime_changed


def _planning_state(store: ParamStore) -> _ReconcileState:
    read = store._read()
    return _ReconcileState(
        states=read.states(),
        meta=read.all_meta(),
        explicit_by_key=read.all_explicit(),
        labels=read.labels(),
        ordinals=read.ordinals(),
        collapsed_headers=set(read.collapsed_headers()),
        locked_keys=set(read.locked_keys()),
        favorite_keys=set(read.favorite_keys()),
        runtime=read.runtime(),
    )


def _commit_reconcile(
    store: ParamStore,
    *,
    expected_revision: int,
    state: _ReconcileState,
    favorites_before: frozenset[ParameterKey],
    persistent_changed: bool,
    value_keys: tuple[ParameterKey, ...],
) -> None:
    mutation = store._mutation()
    if not persistent_changed:
        mutation.commit_runtime(
            expected_revision=expected_revision,
            runtime=state.runtime,
        )
        return
    mutation.commit_reconcile(
        expected_revision=expected_revision,
        states=state.states,
        meta=state.meta,
        explicit_by_key=state.explicit_by_key,
        labels=state.labels,
        ordinals=state.ordinals,
        collapsed_headers=state.collapsed_headers,
        locked_keys=state.locked_keys,
        favorite_keys=state.favorite_keys,
        runtime=state.runtime,
        favorites_changed=state.favorite_keys != favorites_before,
        value_keys=value_keys,
    )


def _state_snapshots(
    state: _ReconcileState,
) -> dict[ParameterKey, ParamStateSnapshot]:
    return {
        key: ParamStateSnapshot.from_state(value)
        for key, value in state.states.items()
    }


def _persistent_signature(state: _ReconcileState) -> tuple[object, ...]:
    """migration が変更し得る永続 domain を immutable 値へ固定する。"""

    return (
        _state_snapshots(state),
        dict(state.meta),
        dict(state.explicit_by_key),
        state.labels.as_dict(),
        state.ordinals.as_dict(),
        frozenset(state.collapsed_headers),
        frozenset(state.locked_keys),
        frozenset(state.favorite_keys),
    )


def _changed_existing_state_keys(
    before: dict[ParameterKey, ParamStateSnapshot],
    state: _ReconcileState,
) -> tuple[ParameterKey, ...]:
    """構造追加・削除を除き、既存 ParamState の実差分だけを返す。"""

    return tuple(
        sorted(
            (
                key
                for key in before.keys() & state.states.keys()
                if before[key] != ParamStateSnapshot.from_state(state.states[key])
            ),
            key=lambda key: (key.op, key.site_id, key.arg),
        )
    )


def _reconcile_runtime_signature(runtime: ParamStoreRuntime) -> tuple[object, ...]:
    """reconciliation が所有する runtime domain を比較可能な値へ固定する。"""

    return (
        frozenset(runtime.loaded_groups),
        frozenset(runtime.reconcile_applied),
        dict(runtime.reconcile_orphans),
    )


def _snapshot_for_reconciliation(
    state: _ReconcileState,
) -> dict[
    ParameterKey,
    tuple[ParamMeta, ParamStateSnapshot, int, str | None],
]:
    """detached state から fingerprint 用 snapshot を構築する。"""

    snapshot: dict[
        ParameterKey,
        tuple[ParamMeta, ParamStateSnapshot, int, str | None],
    ] = {}
    for key, parameter_state in state.states.items():
        meta = state.meta.get(key)
        if meta is None:
            continue
        ordinal = state.ordinals.get(key.op, key.site_id)
        if ordinal is None:
            raise RuntimeError(
                "ParamStore の不変条件違反: ordinal が未割り当ての group がある"
                f": op={key.op!r}, site_id={key.site_id!r}"
            )
        snapshot[key] = (
            meta,
            ParamStateSnapshot.from_state(parameter_state),
            int(ordinal),
            state.labels.get(key.op, key.site_id),
        )
    return snapshot


def _plan_loaded_group_reconciliation(
    state: _ReconcileState,
) -> _ReconcilePlanResult:
    """loaded/observed 差分を detached state 上だけで完成させる。"""

    persistent_before = _persistent_signature(state)
    state_before = _state_snapshots(state)
    runtime = state.runtime
    runtime_before = _reconcile_runtime_signature(runtime)

    # orphan は現在の loaded/observed 集合から毎回導出する runtime state。
    runtime.reconcile_orphans.clear()
    if runtime.loaded_groups and runtime.observed_groups:
        from .style import STYLE_OP

        loaded_targets = {
            (op, site_id)
            for op, site_id in runtime.loaded_groups
            if op != STYLE_OP
        }
        observed_targets = {
            (op, site_id)
            for op, site_id in runtime.observed_groups
            if op != STYLE_OP
        }
        fresh = observed_targets - loaded_targets
        stale = loaded_targets - observed_targets
        fresh_ops = {op for op, _site_id in fresh}
        already_migrated_old = {
            old_group for old_group, _new_group in runtime.reconcile_applied
        }
        stale_candidates = {
            group
            for group in stale
            if group[0] in fresh_ops and group not in already_migrated_old
        }
        if fresh and stale_candidates:
            fingerprints = build_group_fingerprints(
                _snapshot_for_reconciliation(state)
            )
            plan = plan_group_reconciliation(
                stale=sorted(stale_candidates),
                fresh=sorted(fresh),
                fingerprints=fingerprints,
            )
            for old_group, new_group in plan.matches:
                pair = (old_group, new_group)
                if pair in runtime.reconcile_applied:
                    continue
                _migrate_group(state, old_group, new_group)
                runtime.reconcile_applied.add(pair)
                runtime.loaded_groups.add(new_group)
            runtime.reconcile_orphans.update(
                {orphan.new_group: orphan for orphan in plan.orphans}
            )

    return _ReconcilePlanResult(
        persistent_changed=_persistent_signature(state) != persistent_before,
        runtime_changed=(
            _reconcile_runtime_signature(runtime) != runtime_before
        ),
        value_keys=_changed_existing_state_keys(state_before, state),
    )


def reconcile_loaded_groups_for_runtime(store: ParamStore) -> None:
    """ロード済みグループと観測済みグループの差分を再リンクする（削除はしない）。"""

    base_revision = store.revision
    has_loaded, has_observed, has_orphans = store._read().reconcile_status()
    if not has_orphans and (not has_loaded or not has_observed):
        return

    state = _planning_state(store)
    favorites_before = frozenset(state.favorite_keys)
    result = _plan_loaded_group_reconciliation(state)
    if not result.changed:
        return
    _commit_reconcile(
        store,
        expected_revision=base_revision,
        state=state,
        favorites_before=favorites_before,
        persistent_changed=result.persistent_changed,
        value_keys=result.value_keys,
    )


def list_reconcile_orphans(store: ParamStore) -> tuple[ReconcileOrphan, ...]:
    """現在の runtime に残る曖昧な再リンク候補を安定順で返す。"""

    if not isinstance(store, ParamStore):
        raise TypeError("store must be a ParamStore")
    values = store._read().runtime().reconcile_orphans.values()
    return tuple(sorted(values, key=lambda orphan: orphan.new_group))


def manual_migrate_orphan(
    store: ParamStore,
    old_group: GroupKey,
    new_group: GroupKey,
    *,
    history: ParamStoreHistory | None = None,
) -> None:
    """orphan の旧候補 1 件を現在 group へ手動 migrate する。"""

    if not isinstance(store, ParamStore):
        raise TypeError("store must be a ParamStore")
    if history is not None and history._store is not store:
        raise ValueError("history must belong to the same ParamStore")

    normalized_old = group_key(old_group, name="old_group")
    normalized_new = group_key(new_group, name="new_group")

    def apply() -> None:
        base_revision = store.revision
        state = _planning_state(store)
        runtime = state.runtime
        orphan = runtime.reconcile_orphans.get(normalized_new)
        if orphan is None:
            raise KeyError(
                f"reconcile orphan が存在しません: {normalized_new!r}"
            )
        if normalized_old not in orphan.candidate_old_groups:
            raise ValueError(
                "old_group は orphan の候補ではありません: "
                f"{normalized_old!r}"
            )
        if any(
            applied_old == normalized_old and applied_new != normalized_new
            for applied_old, applied_new in runtime.reconcile_applied
        ):
            raise ValueError(
                "old_group は既に別 group へ migrate 済みです: "
                f"{normalized_old!r}"
            )

        favorites_before = frozenset(state.favorite_keys)
        persistent_before = _persistent_signature(state)
        state_before = _state_snapshots(state)
        _migrate_group(state, normalized_old, normalized_new)
        runtime.reconcile_applied.add((normalized_old, normalized_new))
        runtime.loaded_groups.add(normalized_new)
        runtime.reconcile_orphans.pop(normalized_new, None)

        for target, other in tuple(runtime.reconcile_orphans.items()):
            remaining = tuple(
                candidate
                for candidate in other.candidate_old_groups
                if candidate != normalized_old
            )
            if not remaining:
                runtime.reconcile_orphans.pop(target, None)
            elif remaining != other.candidate_old_groups:
                runtime.reconcile_orphans[target] = replace(
                    other,
                    candidate_old_groups=remaining,
                )

        _commit_reconcile(
            store,
            expected_revision=base_revision,
            state=state,
            favorites_before=favorites_before,
            persistent_changed=(
                _persistent_signature(state) != persistent_before
            ),
            value_keys=_changed_existing_state_keys(state_before, state),
        )

    if history is None:
        apply()
    else:
        history.break_coalescing()
        with history.transaction(
            source=("manual-reconcile", normalized_old, normalized_new)
        ):
            apply()


def migrate_group(store: ParamStore, old_group: GroupKey, new_group: GroupKey) -> None:
    """old_group の GUI 状態/メタを new_group へ可能な範囲で移す。"""

    base_revision = store.revision
    state = _planning_state(store)
    favorites_before = frozenset(state.favorite_keys)
    persistent_before = _persistent_signature(state)
    state_before = _state_snapshots(state)
    _migrate_group(state, old_group, new_group)
    if _persistent_signature(state) == persistent_before:
        return
    _commit_reconcile(
        store,
        expected_revision=base_revision,
        state=state,
        favorites_before=favorites_before,
        persistent_changed=True,
        value_keys=_changed_existing_state_keys(state_before, state),
    )


def _migrate_group(
    state: _ReconcileState,
    old_group: GroupKey,
    new_group: GroupKey,
) -> None:
    """detached planning state 上で一つの group migration を完成させる。"""

    old_op, old_site_id = group_key(old_group, name="old_group")
    new_op, new_site_id = group_key(new_group, name="new_group")
    if old_op != new_op:
        raise ValueError(f"op mismatch: {old_group!r} -> {new_group!r}")
    op = old_op

    old_label = state.labels.get(op, old_site_id)
    if old_label is not None and state.labels.get(op, new_site_id) is None:
        state.labels.set(op, new_site_id, old_label)
    state.ordinals.migrate(op, old_site_id, new_site_id)

    old_collapse_keys = group_collapsed_header_keys((op, old_site_id))
    new_collapse_keys = group_collapsed_header_keys((op, new_site_id))
    for old_collapse_key, new_collapse_key in zip(
        old_collapse_keys,
        new_collapse_keys,
        strict=True,
    ):
        if old_collapse_key in state.collapsed_headers:
            state.collapsed_headers.discard(old_collapse_key)
            state.collapsed_headers.add(new_collapse_key)

    for old_key in _group_keys(state, op=op, site_id=old_site_id):
        new_key = ParameterKey(op=op, site_id=new_site_id, arg=old_key.arg)
        old_meta = state.meta.get(old_key)
        new_meta = state.meta.get(new_key)
        if old_meta is None or new_meta is None or old_meta.kind != new_meta.kind:
            continue

        old_state = state.states.get(old_key)
        new_state = state.states.get(new_key)
        if old_state is not None and new_state is not None:
            old_explicit = state.explicit_by_key[old_key]
            new_explicit = state.explicit_by_key[new_key]
            old_override = old_state.override
            new_state.override = (
                not new_explicit
                if old_override == (not old_explicit)
                else old_override
            )
            new_state.ui_value = old_state.ui_value
            new_state.cc_key = old_state.cc_key

        if old_key in state.locked_keys:
            state.locked_keys.discard(old_key)
            state.locked_keys.add(new_key)
        if old_key in state.favorite_keys:
            state.favorite_keys.discard(old_key)
            state.favorite_keys.add(new_key)

        ui_min = (
            old_meta.ui_min if old_meta.ui_min is not None else new_meta.ui_min
        )
        ui_max = (
            old_meta.ui_max if old_meta.ui_max is not None else new_meta.ui_max
        )
        if ui_min != new_meta.ui_min or ui_max != new_meta.ui_max:
            state.meta[new_key] = replace(
                new_meta,
                ui_min=ui_min,
                ui_max=ui_max,
            )


def _group_keys(
    state: _ReconcileState,
    *,
    op: str,
    site_id: str,
) -> list[ParameterKey]:
    keys = (
        set(state.states)
        | set(state.meta)
        | state.locked_keys
        | state.favorite_keys
    )
    return sorted(
        (
            key
            for key in keys
            if key.op == op and key.site_id == site_id
        ),
        key=lambda key: key.arg,
    )


__all__ = [
    "list_reconcile_orphans",
    "manual_migrate_orphan",
    "migrate_group",
    "reconcile_loaded_groups_for_runtime",
]
