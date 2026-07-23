from __future__ import annotations

from copy import deepcopy

import pytest

from grafix.core.parameters.codec import encode_param_store
from grafix.core.parameters.collapsed_header import primitive_collapsed_header_key
from grafix.core.parameters.favorites import set_parameters_favorite
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.history import ParamStoreHistory
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.snapshot_ops import store_snapshot
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.core.parameters.variations import (
    create_variation,
    delete_variation,
    set_parameters_locked,
)
from tests.param_store_test_support import mutate_runtime, runtime_state


_KEY = ParameterKey("rollback", "main", "amount")
_EXTRA_KEY = ParameterKey("rollback", "extra", "amount")
_META = ParamMeta(kind="float", ui_min=0.0, ui_max=10.0)


class _StopBatch(BaseException):
    pass


def _merge(store: ParamStore, key: ParameterKey, value: float) -> None:
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=value,
                meta=_META,
                effective=value,
                source="code",
                explicit=True,
            )
        ],
    )


def _store() -> ParamStore:
    store = ParamStore()
    _merge(store, _KEY, 1.0)
    set_parameters_favorite(store, (_KEY,), favorite=True)
    set_parameters_locked(store, (_KEY,), locked=True)
    store.set_collapsed(
        primitive_collapsed_header_key((_KEY.op, _KEY.site_id)),
        collapsed=True,
    )
    create_variation(store, "baseline", seed=11, created_at=1.0)

    def update_runtime(runtime: object) -> None:
        runtime.loaded_groups.add(("rollback", "loaded"))  # type: ignore[attr-defined]
        runtime.warned_unknown_args.add(("rollback", "unknown"))  # type: ignore[attr-defined]
        runtime.reconcile_applied.add(  # type: ignore[attr-defined]
            (("rollback", "old"), ("rollback", "new"))
        )

    mutate_runtime(store, update_runtime)
    return store


def _logical_state(store: ParamStore) -> object:
    runtime = runtime_state(store)
    return deepcopy(
        (
            encode_param_store(store, preserve_explicit_overrides=True),
            store.revision,
            store.table_revision,
            store.value_revision,
            store.style_revision,
            store.favorite_revision,
            tuple(store._value_change_log),
            frozenset(runtime.loaded_groups),
            frozenset(runtime.observed_groups),
            frozenset(runtime.reconcile_applied),
            dict(runtime.display_order_by_group),
            runtime.next_display_order,
            dict(runtime.last_effective_by_key),
            frozenset(runtime.warned_unknown_args),
            dict(runtime.last_source_by_key),
            dict(runtime.reconcile_orphans),
            runtime.effective_revision,
            runtime.visibility_revision,
            runtime._effective_change_revision,
            runtime._effective_changed_keys,
        )
    )


def _mutate_every_logical_area(store: ParamStore) -> None:
    ok, error = update_state_from_ui(
        store,
        _KEY,
        8.0,
        meta=_META,
        override=True,
        cc_key=12,
    )
    assert ok is True and error is None
    _merge(store, _EXTRA_KEY, 4.0)
    set_parameters_favorite(store, (_KEY,), favorite=False)
    set_parameters_locked(store, (_KEY,), locked=False)
    assert delete_variation(store, "baseline") is True
    create_variation(store, "transient", seed=22, created_at=2.0)
    store.replace_collapsed_headers(())

    def update_runtime(runtime: object) -> None:
        runtime.loaded_groups.add(("rollback", "transient"))  # type: ignore[attr-defined]
        runtime.observed_groups.add(("rollback", "transient"))  # type: ignore[attr-defined]
        runtime.reconcile_applied.clear()  # type: ignore[attr-defined]
        runtime.display_order_by_group[("rollback", "manual")] = 999  # type: ignore[attr-defined]
        runtime.next_display_order = 1_000  # type: ignore[attr-defined]
        runtime.last_effective_by_key[_KEY] = 9.0  # type: ignore[attr-defined]
        runtime.warned_unknown_args.add(("rollback", "transient"))  # type: ignore[attr-defined]
        runtime.last_source_by_key[_KEY] = "ui"  # type: ignore[attr-defined]
        runtime.reconcile_orphans.clear()  # type: ignore[attr-defined]
        runtime.record_effective_changes((_KEY,))  # type: ignore[attr-defined]

    mutate_runtime(store, update_runtime)


def _assert_exactly_restored(
    store: ParamStore,
    *,
    baseline: object,
    original_runtime: object,
    original_snapshot: object,
) -> None:
    assert _logical_state(store) == baseline
    assert store._read().runtime_token() != original_runtime
    restored_snapshot = store_snapshot(store)
    assert restored_snapshot == original_snapshot
    assert restored_snapshot is not original_snapshot


def test_transient_rollback_restores_exact_state_after_normal_exit() -> None:
    store = _store()
    original_snapshot = store_snapshot(store)
    original_runtime = store._read().runtime_token()
    baseline = _logical_state(store)

    with store.begin_transient_rollback():
        _mutate_every_logical_area(store)
        assert _logical_state(store) != baseline

    _assert_exactly_restored(
        store,
        baseline=baseline,
        original_runtime=original_runtime,
        original_snapshot=original_snapshot,
    )


def test_transient_rollback_restores_exact_state_after_exception() -> None:
    store = _store()
    original_snapshot = store_snapshot(store)
    original_runtime = store._read().runtime_token()
    baseline = _logical_state(store)

    with pytest.raises(RuntimeError, match="item failed"):
        with store.begin_transient_rollback():
            _mutate_every_logical_area(store)
            raise RuntimeError("item failed")

    _assert_exactly_restored(
        store,
        baseline=baseline,
        original_runtime=original_runtime,
        original_snapshot=original_snapshot,
    )


def test_transient_rollback_restores_exact_state_after_base_exception() -> None:
    store = _store()
    original_snapshot = store_snapshot(store)
    original_runtime = store._read().runtime_token()
    baseline = _logical_state(store)

    with pytest.raises(_StopBatch):
        with store.begin_transient_rollback():
            _mutate_every_logical_area(store)
            raise _StopBatch

    _assert_exactly_restored(
        store,
        baseline=baseline,
        original_runtime=original_runtime,
        original_snapshot=original_snapshot,
    )


def test_transient_rollback_is_one_shot_and_rejects_nesting() -> None:
    store = _store()
    rollback = store.begin_transient_rollback()

    with rollback:
        with pytest.raises(RuntimeError, match="already active"):
            with store.begin_transient_rollback():
                pass

    with pytest.raises(RuntimeError, match="one-shot"):
        with rollback:
            pass


def test_transient_rollback_rejects_restore_through_another_store() -> None:
    owner = _store()
    other = _store()
    rollback = owner.begin_transient_rollback()

    with rollback:
        with pytest.raises(ValueError, match="belongs to a different ParamStore"):
            other._restore_transient_rollback(rollback)


@pytest.mark.parametrize("patch", [False, True])
def test_transient_rollback_rejects_active_history_transaction(patch: bool) -> None:
    store = _store()
    history = ParamStoreHistory(store)

    with history.transaction(source="test", patch=patch):
        with pytest.raises(RuntimeError, match="history transaction"):
            with store.begin_transient_rollback():
                pass

    assert history.undo_depth == 0
    assert history.redo_depth == 0


def test_history_transaction_rejects_active_transient_rollback() -> None:
    store = _store()
    history = ParamStoreHistory(store)

    with store.begin_transient_rollback():
        with pytest.raises(RuntimeError, match="transient rollback"):
            with history.transaction(source="test"):
                pass

    assert history.undo_depth == 0
    assert history.redo_depth == 0


def test_transient_rollback_does_not_add_history_events() -> None:
    store = _store()
    history = ParamStoreHistory(store)
    baseline = _logical_state(store)

    with store.begin_transient_rollback():
        _mutate_every_logical_area(store)

    assert _logical_state(store) == baseline
    assert history.undo_depth == 0
    assert history.redo_depth == 0
    assert history.record_change(source="after-rollback") is False
