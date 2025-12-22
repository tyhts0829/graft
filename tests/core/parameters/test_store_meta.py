import json

from grafix.core.parameters import FrameParamRecord, ParamMeta, ParamStore, ParameterKey
from grafix.core.parameters.codec import dumps_param_store, loads_param_store
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.invariants import assert_invariants
from grafix.core.parameters.snapshot_ops import store_snapshot
from grafix.core.parameters.ui_ops import update_state_from_ui


def test_snapshot_includes_meta_state_and_ordinal():
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="site-1", arg="r")
    record = FrameParamRecord(
        key=key,
        base=0.5,
        meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
        effective=0.5,
        source="base",
    )

    merge_frame_params(store, [record])

    snap = store_snapshot(store)
    assert key in snap
    meta, state, ordinal, label = snap[key]
    assert meta.kind == "float"
    assert meta.ui_min == 0.0
    assert state.ui_value == 0.5
    assert state.override is False
    assert ordinal == 1
    assert_invariants(store)


def test_snapshot_omits_state_without_meta():
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="site-2", arg="r")
    # meta を登録せず state だけ作る（UI 先行で値が入るケースを模擬）。
    update_state_from_ui(
        store,
        key,
        1.0,
        meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
        override=True,
    )

    snap = store_snapshot(store)
    assert key not in snap
    assert_invariants(store)


def test_json_roundtrip_preserves_meta_and_state():
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="site-3", arg="r")
    record = FrameParamRecord(
        key=key,
        base=0.1,
        meta=ParamMeta(kind="float", ui_min=-1.0, ui_max=1.0, choices=None),
        explicit=False,
    )
    merge_frame_params(store, [record])
    update_state_from_ui(store, key, 0.1, meta=record.meta, override=True)

    payload = dumps_param_store(store)
    loaded = loads_param_store(payload)

    snap = store_snapshot(loaded)
    meta, state, ordinal, label = snap[key]
    assert meta.kind == "float"
    assert meta.ui_min == -1.0
    assert meta.ui_max == 1.0
    assert state.override is True
    assert state.ui_value == 0.1
    assert ordinal == 1
    assert_invariants(loaded)


def test_json_roundtrip_preserves_vec3_cc_key_tuple():
    store = ParamStore()
    key = ParameterKey(op="scale", site_id="site-v", arg="p")
    record = FrameParamRecord(
        key=key,
        base=(0.0, 0.0, 0.0),
        meta=ParamMeta(kind="vec3", ui_min=-1.0, ui_max=1.0),
    )
    merge_frame_params(store, [record])
    stored_meta = store.get_meta(key)
    assert stored_meta is not None
    update_state_from_ui(store, key, (0.0, 0.0, 0.0), meta=stored_meta, cc_key=(1, None, 3))

    payload = dumps_param_store(store)
    loaded = loads_param_store(payload)

    snap = store_snapshot(loaded)
    _meta, state, _ordinal, _label = snap[key]
    assert state.ui_value == (0.0, 0.0, 0.0)
    assert state.cc_key == (1, None, 3)
    assert_invariants(loaded)


def test_json_roundtrip_canonicalizes_rgb_ui_value_to_tuple():
    store = ParamStore()
    key = ParameterKey(op="style", site_id="site-rgb", arg="color")
    meta = ParamMeta(kind="rgb", ui_min=0, ui_max=255)
    merge_frame_params(
        store,
        [FrameParamRecord(key=key, base=(0, 0, 0), meta=meta)],
    )
    update_state_from_ui(store, key, (1, 2, 3), meta=meta, override=True)

    loaded = loads_param_store(dumps_param_store(store))
    snap = store_snapshot(loaded)
    _meta, state, _ordinal, _label = snap[key]
    assert state.ui_value == (1, 2, 3)
    assert isinstance(state.ui_value, tuple)
    assert_invariants(loaded)


def test_encode_drops_state_without_meta():
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="site-no-meta", arg="r")
    update_state_from_ui(
        store,
        key,
        1.0,
        meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
        override=True,
    )

    payload_obj = json.loads(dumps_param_store(store))
    assert payload_obj.get("states", []) == []


def test_unknown_kind_ui_value_is_stringified_to_avoid_reference_leak():
    store = ParamStore()
    key = ParameterKey(op="unknown", site_id="site-unk", arg="x")
    meta = ParamMeta(kind="__unknown__", ui_min=None, ui_max=None)
    merge_frame_params(store, [FrameParamRecord(key=key, base={"init": []}, meta=meta)])

    ok, err = update_state_from_ui(store, key, {"k": [1, 2, 3]}, meta=meta, override=True)
    assert ok is True
    assert err is None

    state = store.get_state(key)
    assert state is not None
    assert isinstance(state.ui_value, str)
    assert_invariants(store)
