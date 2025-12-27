from __future__ import annotations

from dataclasses import replace

from grafix.core.parameters import ParamMeta, ParamStore, ParameterKey, rows_from_snapshot
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.snapshot_ops import store_snapshot_for_gui
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.interactive.parameter_gui.store_bridge import _apply_updated_rows_to_store


def test_cc_unassign_bakes_scalar_effective_and_enables_override() -> None:
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="s1", arg="r")
    meta_r = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.0,
                meta=meta_r,
                explicit=True,
            )
        ],
    )
    stored_meta = store.get_meta(key)
    assert stored_meta is not None
    update_state_from_ui(store, key, 0.1, meta=stored_meta, override=False, cc_key=12)
    store._runtime_ref().last_effective_by_key[key] = 0.75

    snapshot = store_snapshot_for_gui(store)
    rows_before = rows_from_snapshot(snapshot)
    rows_after = [replace(rows_before[0], cc_key=None)]
    _apply_updated_rows_to_store(store, snapshot, rows_before, rows_after)

    state = store.get_state(key)
    assert state is not None
    assert state.cc_key is None
    assert state.override is True
    assert state.ui_value == 0.75


def test_cc_component_unassign_bakes_vec3_effective_and_keeps_other_cc() -> None:
    store = ParamStore()
    key = ParameterKey(op="scale", site_id="sv1", arg="p")
    meta_p = ParamMeta(kind="vec3", ui_min=-1.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=(0.0, 0.0, 0.0),
                meta=meta_p,
                explicit=True,
            )
        ],
    )
    stored_meta = store.get_meta(key)
    assert stored_meta is not None
    update_state_from_ui(
        store,
        key,
        (0.0, 0.0, 0.0),
        meta=stored_meta,
        override=False,
        cc_key=(10, 11, 12),
    )
    store._runtime_ref().last_effective_by_key[key] = (-1.0, 0.25, 1.0)

    snapshot = store_snapshot_for_gui(store)
    rows_before = rows_from_snapshot(snapshot)
    rows_after = [replace(rows_before[0], cc_key=(10, None, 12))]
    _apply_updated_rows_to_store(store, snapshot, rows_before, rows_after)

    state = store.get_state(key)
    assert state is not None
    assert state.cc_key == (10, None, 12)
    assert state.override is True
    assert state.ui_value == (-1.0, 0.25, 1.0)


def test_cc_reassign_does_not_bake_effective() -> None:
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="s2", arg="r")
    meta_r = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.0,
                meta=meta_r,
                explicit=True,
            )
        ],
    )
    stored_meta = store.get_meta(key)
    assert stored_meta is not None
    update_state_from_ui(store, key, 0.1, meta=stored_meta, override=False, cc_key=12)
    store._runtime_ref().last_effective_by_key[key] = 0.75

    snapshot = store_snapshot_for_gui(store)
    rows_before = rows_from_snapshot(snapshot)
    rows_after = [replace(rows_before[0], cc_key=64)]
    _apply_updated_rows_to_store(store, snapshot, rows_before, rows_after)

    state = store.get_state(key)
    assert state is not None
    assert state.cc_key == 64
    assert state.override is False
    assert state.ui_value == 0.1

