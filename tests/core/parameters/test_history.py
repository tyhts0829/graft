from __future__ import annotations

from typing import Any

import pytest

from grafix.core.parameters.collapsed_header import primitive_collapsed_header_key
from grafix.core.parameters.effects import EffectStepTopology
from grafix.core.parameters.effect_order_ops import set_effect_order
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.history import ParamSnapshotSlots, ParamStoreHistory
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.meta_ops import set_meta
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.ui_ops import update_state_from_ui
from tests.param_store_test_support import record_effect_chain


FLOAT_META = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)
EFFECT_CODE_ORDER = (("scale", "scale-site"), ("rotate", "rotate-site"))
EFFECT_UI_ORDER = tuple(reversed(EFFECT_CODE_ORDER))


def _add_effect_chain(store: ParamStore) -> None:
    assert record_effect_chain(
        store,
        chain_id="chain-order",
        steps=(
            EffectStepTopology("scale", "scale-site", 1, 0),
            EffectStepTopology("rotate", "rotate-site", 1, 1),
        ),
    )


def _set_effect_order(
    store: ParamStore,
    order: tuple[tuple[str, str], ...],
) -> None:
    assert set_effect_order(store, chain_id="chain-order", order=order)


def _add_parameter(
    store: ParamStore,
    *,
    value: float = 0.25,
    site_id: str = "site-1",
    arg: str = "r",
) -> ParameterKey:
    key = ParameterKey(op="circle", site_id=site_id, arg=arg)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=value,
                meta=FLOAT_META,
                effective=value,
                source="code",
                explicit=False,
            )
        ],
    )
    return key


def _set_value(store: ParamStore, key: ParameterKey, value: float) -> None:
    ok, error = update_state_from_ui(store, key, value, meta=FLOAT_META)
    assert ok is True and error is None


def _value(store: ParamStore, key: ParameterKey) -> Any:
    state = store.get_state(key)
    assert state is not None
    return state.ui_value


def test_history_coalesces_short_changes_from_same_source() -> None:
    now = [0.0]
    store = ParamStore()
    key = _add_parameter(store)
    history = ParamStoreHistory(store, coalesce_seconds=0.5, clock=lambda: now[0])

    _set_value(store, key, 0.4)
    assert history.record_change(source=(key, "slider")) is True
    now[0] = 0.2
    _set_value(store, key, 0.6)
    assert history.record_change(source=(key, "slider")) is True

    assert history.undo_depth == 1
    assert history.undo() is True
    assert _value(store, key) == 0.25
    assert history.redo() is True
    assert _value(store, key) == 0.6


def test_patch_transaction_avoids_full_capture_and_coalesces_one_key(
    monkeypatch,
) -> None:
    now = [0.0]
    store = ParamStore()
    key = _add_parameter(store)
    history = ParamStoreHistory(store, coalesce_seconds=0.5, clock=lambda: now[0])

    def fail_full_capture():
        raise AssertionError("single-key patch must not capture the full store")

    monkeypatch.setattr(
        store,
        "capture_adjustment_snapshot",
        fail_full_capture,
    )
    with history.transaction(source="parameter_gui", patch=True):
        _set_value(store, key, 0.4)
    now[0] = 0.2
    with history.transaction(source="parameter_gui", patch=True):
        _set_value(store, key, 0.6)

    assert history.undo_depth == 1
    monkeypatch.undo()
    assert history.undo() is True
    assert _value(store, key) == 0.25
    assert history.redo() is True
    assert _value(store, key) == 0.6


def test_patch_history_respects_kind_change_after_code_reload() -> None:
    store = ParamStore()
    key = _add_parameter(store)
    history = ParamStoreHistory(store)

    with history.transaction(source="parameter_gui", patch=True):
        _set_value(store, key, 0.8)

    set_meta(store, key, ParamMeta(kind="int", ui_min=0, ui_max=10))
    assert history.undo() is False
    assert _value(store, key) == 0.8


def test_patch_after_bulk_does_not_mutate_bulk_redo_target() -> None:
    store = ParamStore()
    key = _add_parameter(store)
    history = ParamStoreHistory(store)

    with history.transaction(source="bulk"):
        _set_value(store, key, 0.4)
    with history.transaction(source="parameter_gui", patch=True):
        _set_value(store, key, 0.8)

    assert history.undo() is True
    assert _value(store, key) == 0.4
    assert history.undo() is True
    assert _value(store, key) == 0.25
    assert history.redo() is True
    assert _value(store, key) == 0.4
    assert history.redo() is True
    assert _value(store, key) == 0.8


def test_patch_transactions_for_different_keys_do_not_coalesce() -> None:
    store = ParamStore()
    first = _add_parameter(store, site_id="site-1", arg="a")
    second = _add_parameter(store, site_id="site-2", arg="b")
    history = ParamStoreHistory(store, coalesce_seconds=1.0, clock=lambda: 0.0)

    with history.transaction(source="parameter_gui", patch=True):
        _set_value(store, first, 0.4)
    with history.transaction(source="parameter_gui", patch=True):
        _set_value(store, second, 0.6)

    assert history.undo_depth == 2
    assert history.undo() is True
    assert _value(store, first) == 0.4
    assert _value(store, second) == 0.25


def test_history_separates_sources_and_changes_outside_window() -> None:
    now = [0.0]
    store = ParamStore()
    key = _add_parameter(store)
    history = ParamStoreHistory(store, coalesce_seconds=0.5, clock=lambda: now[0])

    _set_value(store, key, 0.3)
    history.record_change(source="slider")
    now[0] = 0.1
    _set_value(store, key, 0.4)
    history.record_change(source="text-input")
    now[0] = 1.0
    _set_value(store, key, 0.5)
    history.record_change(source="text-input")

    assert history.undo_depth == 3
    assert history.undo() is True
    assert _value(store, key) == 0.4
    assert history.undo() is True
    assert _value(store, key) == 0.3


def test_history_is_bounded_and_new_branch_discards_redo() -> None:
    store = ParamStore()
    key = _add_parameter(store)
    history = ParamStoreHistory(store, capacity=2, coalesce_seconds=0.0)

    for index, value in enumerate((0.3, 0.4, 0.5), start=1):
        _set_value(store, key, value)
        history.record_change(source=f"change-{index}")

    assert history.undo_depth == 2
    assert history.undo() is True
    assert _value(store, key) == 0.4
    assert history.undo() is True
    assert _value(store, key) == 0.3
    assert history.undo() is False

    assert history.redo() is True
    assert _value(store, key) == 0.4
    _set_value(store, key, 0.9)
    history.record_change(source="branch")
    assert history.can_redo is False


def test_transaction_adopts_parameter_discovery_before_user_edit() -> None:
    store = ParamStore()
    history = ParamStoreHistory(store)
    key = _add_parameter(store)

    with history.transaction(source=(key, "slider")):
        _set_value(store, key, 0.8)

    assert history.undo() is True
    # transaction 開始前の discovery は残り、UI 値だけが戻る。
    assert _value(store, key) == 0.25


def test_undo_preserves_a_parameter_discovered_after_the_recorded_edit() -> None:
    store = ParamStore()
    edited_key = _add_parameter(store)
    history = ParamStoreHistory(store)

    _set_value(store, edited_key, 0.8)
    assert history.record_change(source="edit-p") is True

    discovered_key = _add_parameter(
        store,
        value=0.4,
        site_id="site-2",
        arg="q",
    )
    _set_value(store, discovered_key, 0.9)
    discovered_meta = store.get_meta(discovered_key)

    assert history.undo() is True
    assert _value(store, edited_key) == 0.25
    assert _value(store, discovered_key) == 0.9
    assert store.get_meta(discovered_key) == discovered_meta
    assert store.get_ordinal(discovered_key.op, discovered_key.site_id) is not None


def test_snapshot_slots_capture_restore_and_reuse() -> None:
    store = ParamStore()
    key = _add_parameter(store)
    slots = ParamSnapshotSlots(store)

    slots.capture("A")
    _set_value(store, key, 0.8)
    slots.capture("B")

    assert slots.available_slots == ("A", "B")
    assert slots.restore("A") is True
    assert _value(store, key) == 0.25
    assert slots.restore("B") is True
    assert _value(store, key) == 0.8
    assert slots.restore("A") is True
    assert _value(store, key) == 0.25

    slots.clear("B")
    assert slots.restore("B") is False
    with pytest.raises(ValueError, match="A.*B"):
        slots.capture("C")  # type: ignore[arg-type]


def test_snapshot_restore_preserves_a_newly_discovered_adjusted_parameter() -> None:
    store = ParamStore()
    original_key = _add_parameter(store)
    slots = ParamSnapshotSlots(store)
    slots.capture("A")

    discovered_key = _add_parameter(
        store,
        value=0.4,
        site_id="site-2",
        arg="q",
    )
    _set_value(store, original_key, 0.8)
    _set_value(store, discovered_key, 0.9)
    header = primitive_collapsed_header_key(("circle", "site-2"))
    store.set_collapsed(header, collapsed=True)

    assert slots.restore("A") is True
    assert _value(store, original_key) == 0.25
    assert _value(store, discovered_key) == 0.9
    # Snapshot A 作成後に発見した header の GUI 状態も壊さない。
    assert header in store.collapsed_headers()


def test_snapshot_restore_same_state_is_noop_and_does_not_add_history() -> None:
    store = ParamStore()
    _add_parameter(store)
    history = ParamStoreHistory(store)
    slots = ParamSnapshotSlots(store)
    slots.capture("A")
    revision_before = store.revision
    undo_depth_before = history.undo_depth

    with history.transaction(source=("snapshot", "A")):
        assert slots.restore("A") is False

    assert store.revision == revision_before
    assert history.undo_depth == undo_depth_before


def test_history_validates_configuration() -> None:
    with pytest.raises(ValueError, match="capacity"):
        ParamStoreHistory(ParamStore(), capacity=0)
    with pytest.raises(ValueError, match="coalesce"):
        ParamStoreHistory(ParamStore(), coalesce_seconds=-0.1)


def test_effect_order_is_one_full_history_operation_and_ab_state() -> None:
    store = ParamStore()
    _add_effect_chain(store)
    history = ParamStoreHistory(store)
    slots = ParamSnapshotSlots(store)
    slots.capture("A")

    with history.transaction(source=("effect-reorder", "chain-order")):
        _set_effect_order(store, EFFECT_UI_ORDER)
    slots.capture("B")

    assert history.undo_depth == 1
    assert history.undo() is True
    assert store._read().effects().effective_order("chain-order") == EFFECT_CODE_ORDER
    assert history.redo() is True
    assert store._read().effects().effective_order("chain-order") == EFFECT_UI_ORDER

    assert slots.restore("A") is True
    assert store._read().effects().effective_order("chain-order") == EFFECT_CODE_ORDER
    assert slots.restore("B") is True
    assert store._read().effects().effective_order("chain-order") == EFFECT_UI_ORDER


def test_synchronize_is_noop_without_external_changes() -> None:
    now = [0.0]
    store = ParamStore()
    key = _add_parameter(store)
    history = ParamStoreHistory(store, coalesce_seconds=0.5, clock=lambda: now[0])

    _set_value(store, key, 0.4)
    history.record_change(source="slider")
    # GUI frame 毎に呼んでも、変更が無ければ coalesce を切らない。
    assert history.synchronize() is False
    now[0] = 0.2
    _set_value(store, key, 0.6)
    history.record_change(source="slider")

    assert history.undo_depth == 1
