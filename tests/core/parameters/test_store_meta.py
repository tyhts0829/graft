from grafix.core.parameters import FrameParamRecord, ParamMeta, ParamState, ParamStore, ParameterKey
from grafix.core.parameters.codec import dumps_param_store, loads_param_store
from grafix.core.parameters.store_ops import merge_frame_params, store_snapshot


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


def test_snapshot_omits_state_without_meta():
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="site-2", arg="r")
    # meta 未設定のまま state だけ作る
    store.states[key] = ParamState(ui_value=1.0)

    snap = store_snapshot(store)
    assert key not in snap


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
    store.states[key].override = True

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


def test_json_roundtrip_preserves_vec3_cc_key_tuple():
    store = ParamStore()
    key = ParameterKey(op="scale", site_id="site-v", arg="p")
    record = FrameParamRecord(
        key=key,
        base=(0.0, 0.0, 0.0),
        meta=ParamMeta(kind="vec3", ui_min=-1.0, ui_max=1.0),
    )
    merge_frame_params(store, [record])
    store.states[key].cc_key = (1, None, 3)

    payload = dumps_param_store(store)
    loaded = loads_param_store(payload)

    snap = store_snapshot(loaded)
    _meta, state, _ordinal, _label = snap[key]
    assert state.cc_key == (1, None, 3)
