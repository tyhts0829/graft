from src.parameters import FrameParamRecord, ParamMeta, ParamState, ParamStore, ParameterKey


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

    store.store_frame_params([record])

    snap = store.snapshot()
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
    store._states[key] = ParamState(ui_value=1.0)  # type: ignore[attr-defined]

    snap = store.snapshot()
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
    store.store_frame_params([record])
    store._states[key].override = True  # type: ignore[attr-defined]

    payload = store.to_json()
    loaded = ParamStore.from_json(payload)

    snap = loaded.snapshot()
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
    store.store_frame_params([record])
    store._states[key].cc_key = (1, None, 3)  # type: ignore[attr-defined]

    payload = store.to_json()
    loaded = ParamStore.from_json(payload)

    snap = loaded.snapshot()
    _meta, state, _ordinal, _label = snap[key]
    assert state.cc_key == (1, None, 3)
