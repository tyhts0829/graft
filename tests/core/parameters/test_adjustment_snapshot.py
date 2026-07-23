from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from grafix.core.parameters.collapsed_header import effect_chain_collapsed_header_key
from grafix.core.parameters.effect_order_ops import (
    merge_frame_effect_chains,
    reset_effect_order,
    set_effect_order,
)
from grafix.core.parameters.frame_params import (
    FrameEffectChainRecord,
    FrameParamRecord,
)
from grafix.core.parameters.effects import EffectStepTopology
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.labels_ops import set_label
from grafix.core.parameters.adjustment_snapshot import (
    ParameterAdjustment,
    ParameterAdjustmentSnapshot,
)
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.meta_ops import set_meta
from grafix.core.parameters.snapshot_ops import store_snapshot
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.state import ParamStateSnapshot
from grafix.core.parameters.style import STYLE_GLOBAL_THICKNESS, style_key
from grafix.core.parameters.style_ops import ensure_style_entries
from grafix.core.parameters.ui_ops import update_state_from_ui
from tests.param_store_test_support import (
    mutate_runtime,
    record_effect_chain,
    replace_parameter_state_for_test,
    runtime_state,
    set_explicit_for_test,
)


EFFECT_CODE_ORDER = (("scale", "scale-site"), ("rotate", "rotate-site"))
EFFECT_UI_ORDER = tuple(reversed(EFFECT_CODE_ORDER))


def _record_effect_chain(store: ParamStore) -> None:
    changed = record_effect_chain(
        store,
        chain_id="chain-order",
        steps=(
            EffectStepTopology("scale", "scale-site", 1, 0),
            EffectStepTopology("rotate", "rotate-site", 1, 1),
        ),
    )
    assert changed


def _populated_store() -> tuple[ParamStore, ParameterKey]:
    store = ParamStore()
    key = ParameterKey(op="wobble", site_id="site-1", arg="amount")
    merge_frame_effect_chains(
        store,
        [
            FrameEffectChainRecord(
                chain_id="chain-1",
                steps=(EffectStepTopology("wobble", "site-1", 1, 0),),
            )
        ],
        observation_complete=False,
    )
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.25,
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
                effective=0.25,
                source="code",
                explicit=True,
            )
        ],
    )
    update_state_from_ui(
        store,
        key,
        0.75,
        meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
        override=True,
        cc_key=17,
    )
    set_label(store, op=key.op, site_id=key.site_id, label="primary wobble")
    store.set_collapsed(
        effect_chain_collapsed_header_key("chain-1"),
        collapsed=True,
    )
    return store, key


def test_snapshot_restores_gui_state_but_keeps_code_owned_structure_and_runtime() -> None:
    store, key = _populated_store()
    mutate_runtime(
        store,
        lambda runtime: runtime.loaded_groups.add(("runtime", "before")),
    )
    snapshot = store.capture_adjustment_snapshot()

    # GUI-owned 状態と code-owned 状態を両方変更する。
    replace_parameter_state_for_test(
        store,
        key,
        ui_value=0.1,
        override=False,
        cc_key=None,
    )
    set_meta(
        store,
        key,
        ParamMeta(
            kind="float",
            ui_min=-10.0,
            ui_max=10.0,
        ),
    )
    set_explicit_for_test(store, key, False)
    set_label(store, op=key.op, site_id=key.site_id, label="current code label")
    store.replace_collapsed_headers(())

    # runtime は capture 後に進んだ内容も維持する。
    mutate_runtime(
        store,
        lambda runtime: runtime.loaded_groups.add(("runtime", "after")),
    )
    runtime_identity = store._read().runtime_token()
    revision_before_restore = store.revision
    assert store.apply_adjustment_snapshot(snapshot) is True

    restored = store.get_state(key)
    assert restored is not None
    assert restored.ui_value == 0.75
    assert restored.override is True
    assert restored.cc_key == 17
    # range は GUI-owned なので戻るが、kind は現在の code を保つ。
    assert store.get_meta(key) == ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=1.0,
    )
    assert store._read().explicit(key) is False
    assert store.get_label(key.op, key.site_id) == "current code label"
    assert store.get_ordinal(key.op, key.site_id) == 1
    assert store.get_effect_step(key.op, key.site_id) == ("chain-1", 0)
    assert store.chain_ordinals() == {"chain-1": 1}
    assert store.collapsed_headers() == {
        effect_chain_collapsed_header_key("chain-1")
    }
    assert store._read().runtime_token() == runtime_identity
    assert runtime_state(store).loaded_groups == {
        ("runtime", "before"),
        ("runtime", "after"),
    }
    assert store.revision > revision_before_restore


def test_snapshot_merge_preserves_a_parameter_discovered_after_capture() -> None:
    store, key = _populated_store()
    snapshot = store.capture_adjustment_snapshot()

    new_key = ParameterKey(op="wobble", site_id="site-2", arg="frequency")
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=new_key,
                base=2.0,
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=8.0),
                effective=2.0,
                source="code",
                explicit=False,
            )
        ],
    )
    update_state_from_ui(
        store,
        new_key,
        4.5,
        meta=ParamMeta(kind="float", ui_min=0.0, ui_max=8.0),
        override=True,
        cc_key=23,
    )
    update_state_from_ui(
        store,
        key,
        0.1,
        meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
    )

    assert store.apply_adjustment_snapshot(snapshot) is True
    assert store.get_state(key).ui_value == 0.75  # type: ignore[union-attr]
    discovered = store.get_state(new_key)
    assert discovered is not None
    assert discovered.ui_value == 4.5
    assert discovered.override is True
    assert discovered.cc_key == 23
    assert store.get_meta(new_key) == ParamMeta(kind="float", ui_min=0.0, ui_max=8.0)


def test_snapshot_skips_a_key_whose_current_code_kind_changed() -> None:
    store, key = _populated_store()
    snapshot = store.capture_adjustment_snapshot()
    replace_parameter_state_for_test(
        store,
        key,
        ui_value=7,
        override=False,
        cc_key=None,
    )
    set_meta(store, key, ParamMeta(kind="int", ui_min=0, ui_max=10))

    revision_before = store.revision
    assert store.apply_adjustment_snapshot(snapshot) is False
    assert store.revision == revision_before
    assert store.get_state(key).ui_value == 7  # type: ignore[union-attr]
    assert store.get_meta(key) == ParamMeta(kind="int", ui_min=0, ui_max=10)


def test_snapshot_is_immutable_and_can_be_restored_more_than_once() -> None:
    store, key = _populated_store()
    replace_parameter_state_for_test(store, key, ui_value=(1, 2))
    snapshot = store.capture_adjustment_snapshot()

    replace_parameter_state_for_test(store, key, ui_value=(1, 2, 3))
    assert store.apply_adjustment_snapshot(snapshot) is True
    restored = store.get_state(key)
    assert restored is not None
    assert restored.ui_value == (1, 2)

    replace_parameter_state_for_test(store, key, ui_value=(99,))
    assert store.apply_adjustment_snapshot(snapshot) is True
    restored_again = store.get_state(key)
    assert restored_again is not None
    assert restored_again.ui_value == (1, 2)


def test_snapshot_queries_expose_only_frozen_values() -> None:
    store, key = _populated_store()

    snapshot = store.capture_adjustment_snapshot()
    adjustment = snapshot.get(key)

    assert isinstance(snapshot.items(), tuple)
    assert adjustment is not None
    assert type(adjustment.state) is ParamStateSnapshot
    with pytest.raises(FrozenInstanceError):
        adjustment.state.override = False  # type: ignore[misc]


def test_snapshot_apply_publishes_one_fully_applied_revision(monkeypatch) -> None:
    store, first_key = _populated_store()
    second_key = ParameterKey(op="wobble", site_id="site-2", arg="amount")
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=second_key,
                base=0.4,
                meta=meta,
                effective=0.4,
                source="code",
                explicit=False,
            )
        ],
    )
    snapshot = store.capture_adjustment_snapshot()
    assert update_state_from_ui(store, first_key, 0.1, meta=meta)[0]
    assert update_state_from_ui(store, second_key, 0.9, meta=meta)[0]
    revision = store.revision

    published_values: list[tuple[object, object]] = []
    original_commit = store._commit_prepared_mutation

    def observe_commit(**kwargs: object) -> None:
        first = store.get_state(first_key)
        second = store.get_state(second_key)
        assert first is not None and second is not None
        published_values.append((first.ui_value, second.ui_value))
        original_commit(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "_commit_prepared_mutation", observe_commit)

    assert store.apply_adjustment_snapshot(snapshot) is True
    assert published_values == [(0.75, 0.4)]
    assert store.revision == revision + 1


def test_restore_invalidates_cached_snapshot() -> None:
    store, key = _populated_store()
    snapshot = store.capture_adjustment_snapshot()
    before = store_snapshot(store)

    update_state_from_ui(
        store,
        key,
        0.2,
        meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
    )
    assert store.apply_adjustment_snapshot(snapshot) is True
    after = store_snapshot(store)

    assert after is not before
    assert after[key][1].ui_value == 0.75


def test_restore_style_value_advances_style_without_rebuilding_table() -> None:
    store = ParamStore()
    ensure_style_entries(
        store,
        background_color_rgb01=(1.0, 1.0, 1.0),
        global_thickness=0.01,
        global_line_color_rgb01=(0.0, 0.0, 0.0),
    )
    key = style_key(STYLE_GLOBAL_THICKNESS)
    meta = store.get_meta(key)
    assert meta is not None
    snapshot = store.capture_adjustment_snapshot()
    assert update_state_from_ui(store, key, 0.005, meta=meta)[0]
    table_revision = store.table_revision
    style_revision = store.style_revision
    value_revision = store.value_revision

    assert store.apply_adjustment_snapshot(snapshot) is True
    assert store.table_revision == table_revision
    assert store.style_revision == style_revision + 1
    assert store.value_revision == value_revision + 1


def test_restoring_the_same_snapshot_is_a_revision_noop() -> None:
    store, _key = _populated_store()
    snapshot = store.capture_adjustment_snapshot()
    revision_before = store.revision

    assert store.apply_adjustment_snapshot(snapshot) is False
    assert store.revision == revision_before


def test_snapshot_restores_gui_effect_order_without_replacing_code_topology() -> None:
    store = ParamStore()
    _record_effect_chain(store)
    assert set_effect_order(
        store,
        chain_id="chain-order",
        order=EFFECT_UI_ORDER,
    )
    snapshot = store.capture_adjustment_snapshot()
    topology_before = store._read().effects().topology("chain-order")

    assert reset_effect_order(store, chain_id="chain-order")

    assert store.apply_adjustment_snapshot(snapshot) is True
    assert store._read().effects().effective_order("chain-order") == EFFECT_UI_ORDER
    assert store._read().effects().topology("chain-order") == topology_before


def test_snapshot_can_restore_code_order_and_skips_incompatible_topology() -> None:
    store = ParamStore()
    _record_effect_chain(store)
    code_order_snapshot = store.capture_adjustment_snapshot()
    assert set_effect_order(
        store,
        chain_id="chain-order",
        order=EFFECT_UI_ORDER,
    )

    assert store.apply_adjustment_snapshot(code_order_snapshot) is True
    assert store.effect_order_overrides() == {}
    assert set_effect_order(
        store,
        chain_id="chain-order",
        order=EFFECT_UI_ORDER,
    )
    reordered_snapshot = store.capture_adjustment_snapshot()
    assert record_effect_chain(
        store,
        chain_id="chain-order",
        steps=(
            EffectStepTopology("scale", "scale-site", 1, 0),
            EffectStepTopology("rotate", "rotate-site", 1, 1),
            EffectStepTopology("wobble", "wobble-site", 1, 2),
        ),
    )
    revision_before = store.revision

    assert store.apply_adjustment_snapshot(reordered_snapshot) is False
    assert store.revision == revision_before
    assert store.effect_order_overrides() == {}


def test_snapshot_does_not_restore_order_after_effect_arity_change() -> None:
    store = ParamStore()
    initial_topology = (
        EffectStepTopology("first", "first-site", 1, 0),
        EffectStepTopology("second", "second-site", 1, 1),
        EffectStepTopology("third", "third-site", 1, 2),
    )
    assert record_effect_chain(
        store,
        chain_id="arity-chain",
        steps=initial_topology,
    )
    assert set_effect_order(
        store,
        chain_id="arity-chain",
        order=(
            ("first", "first-site"),
            ("third", "third-site"),
            ("second", "second-site"),
        ),
    )
    snapshot = store.capture_adjustment_snapshot()

    assert record_effect_chain(
        store,
        chain_id="arity-chain",
        steps=(
            EffectStepTopology("first", "first-site", 2, 0),
            EffectStepTopology("second", "second-site", 1, 1),
            EffectStepTopology("third", "third-site", 1, 2),
        ),
    )
    revision = store.revision

    assert store.apply_adjustment_snapshot(snapshot) is False
    assert store.revision == revision
    assert store.effect_order_overrides() == {}


def test_snapshot_rejects_corrupt_non_bool_override() -> None:
    key = ParameterKey("line", "site", "length")

    with pytest.raises(TypeError, match="state.override"):
        ParameterAdjustmentSnapshot(
            adjustments={
                key: ParameterAdjustment(
                    state=ParamStateSnapshot(
                        override=1,  # type: ignore[arg-type]
                        ui_value=1.0,
                        cc_key=None,
                    ),
                    meta=ParamMeta(kind="float"),
                )
            },
            collapsed_by_header={},
            effect_order_state={},
            effect_topology_signatures={},
        )
