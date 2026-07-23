from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

import grafix.core.parameters.merge_ops as merge_ops
from grafix.core.parameters.codec import (
    dumps_param_store,
    loads_param_store_result,
)
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.prune_ops import prune_groups
from grafix.core.parameters.reconcile_ops import migrate_group
from grafix.core.parameters.snapshot_ops import store_snapshot
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.core.parameters.variations import (
    create_variation,
    set_parameters_locked,
)


def _record(index: int) -> FrameParamRecord:
    return FrameParamRecord(
        key=ParameterKey(
            op="line",
            site_id=f"site-{index}",
            arg="length",
        ),
        base=float(index),
        meta=ParamMeta(kind="float", ui_min=0.0, ui_max=100.0),
        explicit=False,
        effective=float(index),
        source="code",
    )


def _revisions(store: ParamStore) -> tuple[int, int, int, int, int, int, int]:
    return (
        store.revision,
        store.table_revision,
        store.value_revision,
        store.style_revision,
        store.favorite_revision,
        store.effective_revision,
        store.runtime_view().visibility_revision,
    )


def test_merge_planning_failure_keeps_all_store_domains_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ParamStore()
    original = _record(0)
    merge_ops.merge_frame_params(store, [original])
    before_snapshot = dict(store_snapshot(store))
    before_runtime = store.runtime_view()
    before_revisions = (
        store.revision,
        store.table_revision,
        store.value_revision,
        store.effective_revision,
    )

    real_canonicalize = merge_ops.canonicalize_ui_value

    def fail_for_second(value: object, meta: ParamMeta) -> object:
        if value == 2.0:
            raise RuntimeError("planning failed")
        return real_canonicalize(value, meta)

    monkeypatch.setattr(merge_ops, "canonicalize_ui_value", fail_for_second)
    with pytest.raises(RuntimeError, match="planning failed"):
        merge_ops.merge_frame_params(
            store,
            [
                replace(original, effective=10.0, source="ui"),
                _record(1),
                _record(2),
            ],
        )

    assert dict(store_snapshot(store)) == before_snapshot
    assert store.runtime_view() == before_runtime
    assert (
        store.revision,
        store.table_revision,
        store.value_revision,
        store.effective_revision,
    ) == before_revisions


def test_read_port_returns_detached_mutable_components() -> None:
    store = ParamStore()
    record = _record(0)
    merge_ops.merge_frame_params(store, [record])

    read = store._read()
    states = read.states()
    runtime = read.runtime()
    states[record.key].ui_value = 99.0
    runtime.last_effective_by_key[record.key] = 99.0

    assert store.get_state(record.key).ui_value == 0.0  # type: ignore[union-attr]
    assert store.last_effective_value(record.key) == 0.0


def test_merge_history_observer_failure_invalidates_the_detached_cache() -> None:
    store = ParamStore()
    original = _record(0)
    merge_ops.merge_frame_params(store, [original])
    changed = replace(
        original,
        base=1,
        effective=1,
        meta=ParamMeta(kind="int", ui_min=0, ui_max=100),
    )
    before_snapshot = dict(store_snapshot(store))
    before_runtime = store.runtime_view()
    before_revision = store.revision

    def fail_history(_key: ParameterKey) -> None:
        raise RuntimeError("history failed")

    store._begin_history_patch_capture(
        observe_key=fail_history,
        observe_headers=lambda _headers: None,
    )
    try:
        with pytest.raises(RuntimeError, match="history failed"):
            merge_ops.merge_frame_params(store, [changed])
    finally:
        store._end_history_patch_capture()

    assert dict(store_snapshot(store)) == before_snapshot
    assert store.runtime_view() == before_runtime
    assert store.revision == before_revision

    merge_ops.merge_frame_params(store, [changed])

    assert store.get_meta(original.key) == changed.meta
    assert store.last_effective_value(original.key) == 1


def test_one_key_runtime_merge_does_not_build_a_full_store_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ParamStore()
    original = _record(0)
    merge_ops.merge_frame_params(store, [original])
    snapshot = store_snapshot(store)
    runtime_token = store._read().runtime_token()
    revision = store.revision
    effective_revision = store.effective_revision

    def reject_full_plan(_store: ParamStore) -> object:
        raise AssertionError("runtime-only merge must stay sparse")

    monkeypatch.setattr(merge_ops, "_new_plan", reject_full_plan)
    merge_ops.merge_frame_params(
        store,
        [replace(original, effective=0.75, source="midi_live")],
    )

    assert store.revision == revision
    assert store.effective_revision == effective_revision + 1
    assert store._read().runtime_token() == runtime_token
    assert store_snapshot(store) is snapshot
    assert store.last_effective_value(original.key) == 0.75
    assert store.runtime_view().last_source_by_key[original.key] == "midi_live"
    assert store.effective_changes_since(effective_revision) == frozenset(
        {original.key}
    )


@pytest.mark.parametrize(
    "command",
    [
        lambda store: prune_groups(store, {("ghost", "site")}),
        lambda store: migrate_group(
            store,
            ("ghost", "old"),
            ("ghost", "new"),
        ),
        lambda store: migrate_group(
            store,
            ("ghost", "same"),
            ("ghost", "same"),
        ),
    ],
)
def test_empty_domain_commands_keep_every_revision_stable(
    command: Callable[[ParamStore], None],
) -> None:
    store = ParamStore()
    snapshot = store_snapshot(store)
    favorites = store._read().favorite_keys()
    runtime_token = store._read().runtime_token()
    before = (
        store.revision,
        store.table_revision,
        store.value_revision,
        store.style_revision,
        store.favorite_revision,
        store.effective_revision,
    )

    command(store)

    assert (
        store.revision,
        store.table_revision,
        store.value_revision,
        store.style_revision,
        store.favorite_revision,
        store.effective_revision,
    ) == before
    assert store_snapshot(store) is snapshot
    assert store._read().favorite_keys() is favorites
    assert store._read().runtime_token() == runtime_token


def test_duplicate_structural_records_with_same_final_state_are_a_no_op() -> None:
    store = ParamStore()
    original = _record(0)
    merge_ops.merge_frame_params(store, [original])
    snapshot = store_snapshot(store)
    runtime_token = store._read().runtime_token()
    revisions = _revisions(store)
    as_int = replace(
        original,
        base=0,
        effective=0,
        meta=ParamMeta(kind="int", ui_min=0, ui_max=100),
    )

    merge_ops.merge_frame_params(store, [as_int, original])

    assert _revisions(store) == revisions
    assert store_snapshot(store) is snapshot
    assert store._read().runtime_token() == runtime_token


def test_kind_change_marks_the_existing_state_as_a_value_change() -> None:
    store = ParamStore()
    original = _record(0)
    merge_ops.merge_frame_params(store, [original])
    ok, error = update_state_from_ui(
        store,
        original.key,
        9.9,
        meta=original.meta,
        override=True,
    )
    assert ok is True
    assert error is None
    before = _revisions(store)

    merge_ops.merge_frame_params(
        store,
        [
            replace(
                original,
                base=2,
                effective=2,
                meta=ParamMeta(kind="int", ui_min=0, ui_max=100),
            )
        ],
    )

    after = _revisions(store)
    assert after[0] == before[0] + 1
    assert after[1] == before[1] + 1
    assert after[2] == before[2] + 1


def test_explicit_follow_policy_marks_the_override_as_a_value_change() -> None:
    store = ParamStore()
    explicit = replace(_record(0), explicit=True)
    merge_ops.merge_frame_params(store, [explicit])
    before = _revisions(store)

    merge_ops.merge_frame_params(store, [replace(explicit, explicit=False)])

    state = store.get_state(explicit.key)
    assert state is not None
    assert state.override is True
    after = _revisions(store)
    assert after[0] == before[0] + 1
    assert after[1] == before[1] + 1
    assert after[2] == before[2] + 1


def test_group_migration_marks_the_target_state_as_a_value_change() -> None:
    store = ParamStore()
    old = _record(0)
    new = replace(
        _record(1),
        key=ParameterKey(op="line", site_id="site-new", arg="length"),
    )
    merge_ops.merge_frame_params(store, [old, new])
    ok, error = update_state_from_ui(
        store,
        old.key,
        9.0,
        meta=old.meta,
        override=True,
    )
    assert ok is True
    assert error is None
    before = _revisions(store)

    migrate_group(
        store,
        (old.key.op, old.key.site_id),
        (new.key.op, new.key.site_id),
    )

    migrated = store.get_state(new.key)
    assert migrated is not None
    assert migrated.ui_value == 9.0
    after = _revisions(store)
    assert after[0] == before[0] + 1
    assert after[1] == before[1] + 1
    assert after[2] == before[2] + 1


def test_merge_and_automatic_reconciliation_share_one_persistent_commit() -> None:
    original = ParamStore()
    old = _record(0)
    merge_ops.merge_frame_params(original, [old])
    ok, error = update_state_from_ui(
        original,
        old.key,
        9.0,
        meta=old.meta,
        override=True,
    )
    assert ok is True
    assert error is None
    store = loads_param_store_result(dumps_param_store(original)).store
    before = _revisions(store)
    new = replace(
        old,
        key=ParameterKey(op=old.key.op, site_id="site-new", arg=old.key.arg),
        base=2.0,
        effective=2.0,
    )

    merge_ops.merge_frame_params(store, [new])

    migrated = store.get_state(new.key)
    assert migrated is not None
    assert migrated.ui_value == 9.0
    after = _revisions(store)
    assert after[0] == before[0] + 1
    assert after[1] == before[1] + 1
    assert after[2] == before[2] + 1
    assert after[3] == before[3] + 1
    assert after[4] == before[4]
    assert after[5] == before[5] + 1


def test_first_observation_of_loaded_group_is_runtime_only() -> None:
    original = ParamStore()
    record = _record(0)
    merge_ops.merge_frame_params(original, [record])
    store = loads_param_store_result(dumps_param_store(original)).store
    snapshot = store_snapshot(store)
    runtime_token = store._read().runtime_token()
    before = _revisions(store)

    merge_ops.merge_frame_params(store, [record])

    after = _revisions(store)
    assert after[:5] == before[:5]
    assert after[5] == before[5] + 1
    assert after[6] == before[6] + 1
    assert store_snapshot(store) is snapshot
    assert store._read().runtime_token() == runtime_token


def test_locks_and_variations_do_not_invalidate_the_parameter_table() -> None:
    store = ParamStore()
    record = _record(0)
    merge_ops.merge_frame_params(store, [record])

    snapshot = store_snapshot(store)
    before = _revisions(store)
    assert set_parameters_locked(store, [record.key], locked=True) == (
        record.key,
    )
    after_lock = _revisions(store)
    assert after_lock[0] == before[0] + 1
    assert after_lock[1:] == before[1:]
    assert store_snapshot(store) is snapshot

    snapshot = store_snapshot(store)
    create_variation(store, "saved", created_at=1.0)
    after_variation = _revisions(store)
    assert after_variation[0] == after_lock[0] + 1
    assert after_variation[1:] == after_lock[1:]
    assert store_snapshot(store) is snapshot


def test_replace_contents_routes_the_commit_through_the_mutation_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ParamStore()
    record = _record(0)
    merge_ops.merge_frame_params(source, [record])
    target = ParamStore()
    mutation_type = type(target._mutation())
    original_commit = mutation_type.commit_full_replacement
    committed_revisions: list[int] = []

    def track_commit(mutation: object, **kwargs: object) -> None:
        committed_revisions.append(int(kwargs["expected_revision"]))
        original_commit(mutation, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        mutation_type,
        "commit_full_replacement",
        track_commit,
    )

    target.replace_contents_from(source)

    assert committed_revisions == [0]
    assert dict(store_snapshot(target)) == dict(store_snapshot(source))
    assert target.runtime_view().loaded_groups == source.runtime_view().loaded_groups
