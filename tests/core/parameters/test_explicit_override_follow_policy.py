from __future__ import annotations

import json

from grafix.core.parameters import ParamMeta, ParamStore, ParameterKey
from grafix.core.parameters.frame_params import FrameParamRecord


def test_override_follows_implicit_to_explicit_change_when_still_default():
    store = ParamStore()
    key = ParameterKey(op="polyhedron", site_id="site", arg="type_index")
    meta = ParamMeta(kind="int", ui_min=0, ui_max=4)

    store.store_frame_params([FrameParamRecord(key=key, base=0, meta=meta, explicit=False)])
    st0 = store.get_state(key)
    assert st0 is not None
    assert st0.override is True  # implicit の既定

    store.store_frame_params([FrameParamRecord(key=key, base=1, meta=meta, explicit=True)])
    st1 = store.get_state(key)
    assert st1 is not None
    assert st1.override is False  # explicit の既定へ追従


def test_override_does_not_change_when_prev_explicit_is_unknown_old_json():
    key = ParameterKey(op="polyhedron", site_id="site", arg="type_index")
    meta = ParamMeta(kind="int", ui_min=0, ui_max=4)

    store = ParamStore()
    store.ensure_state(key, base_value=0).override = True
    store.set_meta(key, meta)

    payload_obj = json.loads(store.to_json())
    payload_obj.pop("explicit", None)  # 旧 JSON を模擬（explicit 情報なし）
    loaded = ParamStore.from_json(json.dumps(payload_obj))

    loaded.store_frame_params([FrameParamRecord(key=key, base=1, meta=meta, explicit=True)])
    st = loaded.get_state(key)
    assert st is not None
    assert st.override is True  # unknown の場合は勝手に切り替えない


def test_override_follows_across_site_id_migration_with_reconcile():
    meta = ParamMeta(kind="int", ui_min=0, ui_max=4)

    old_key = ParameterKey(op="polyhedron", site_id="old-site", arg="type_index")
    original = ParamStore()
    original.store_frame_params(
        [FrameParamRecord(key=old_key, base=0, meta=meta, explicit=False)]
    )
    original_state = original.get_state(old_key)
    assert original_state is not None
    assert original_state.override is True

    # 永続化ロード相当（loaded_groups を持つ状態にする）
    store = ParamStore.from_json(original.to_json())

    new_key = ParameterKey(op="polyhedron", site_id="new-site", arg="type_index")
    store.store_frame_params(
        [FrameParamRecord(key=new_key, base=1, meta=meta, explicit=True)]
    )

    st = store.get_state(new_key)
    assert st is not None
    assert st.override is False


def test_explicit_override_is_reset_to_false_on_json_roundtrip():
    store = ParamStore()
    key = ParameterKey(op="polyhedron", site_id="site", arg="type_index")
    meta = ParamMeta(kind="int", ui_min=0, ui_max=4)

    store.store_frame_params([FrameParamRecord(key=key, base=0, meta=meta, explicit=True)])
    state = store.get_state(key)
    assert state is not None
    state.override = True
    state.ui_value = 3

    loaded = ParamStore.from_json(store.to_json())
    loaded_state = loaded.get_state(key)
    assert loaded_state is not None
    assert loaded_state.override is False
    assert loaded_state.ui_value == 3


def test_implicit_override_is_preserved_on_json_roundtrip():
    store = ParamStore()
    key = ParameterKey(op="polyhedron", site_id="site", arg="type_index")
    meta = ParamMeta(kind="int", ui_min=0, ui_max=4)

    store.store_frame_params([FrameParamRecord(key=key, base=0, meta=meta, explicit=False)])
    state = store.get_state(key)
    assert state is not None
    state.override = False
    state.ui_value = 2

    loaded = ParamStore.from_json(store.to_json())
    loaded_state = loaded.get_state(key)
    assert loaded_state is not None
    assert loaded_state.override is False
    assert loaded_state.ui_value == 2


def test_removing_explicit_allows_override_true_again_after_one_frame_merge():
    store = ParamStore()
    key = ParameterKey(op="polyhedron", site_id="site", arg="type_index")
    meta = ParamMeta(kind="int", ui_min=0, ui_max=4)

    store.store_frame_params([FrameParamRecord(key=key, base=0, meta=meta, explicit=True)])
    state = store.get_state(key)
    assert state is not None
    state.override = True

    loaded = ParamStore.from_json(store.to_json())
    loaded_state = loaded.get_state(key)
    assert loaded_state is not None
    assert loaded_state.override is False  # 再起動時は explicit の既定

    loaded.store_frame_params([FrameParamRecord(key=key, base=0, meta=meta, explicit=False)])
    assert loaded.get_state(key) is not None
    assert loaded.get_state(key).override is True
