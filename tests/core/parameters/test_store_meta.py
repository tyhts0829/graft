import json

import pytest

from grafix.core.parameters import FrameParamRecord, ParamMeta, ParamStore, ParameterKey
from grafix.core.parameters.codec import (
    decode_param_store_result,
    dumps_param_store,
    encode_param_store,
    loads_param_store_result,
)
from grafix.core.parameters.collapsed_header import primitive_collapsed_header_key
from grafix.core.parameters.favorites import (
    favorite_parameter_key_set,
    set_parameters_favorite,
)
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.invariants import assert_invariants
from grafix.core.parameters.snapshot_ops import store_snapshot
from grafix.core.parameters.state import ParamState, ParamStateSnapshot
from grafix.core.parameters.ui_ops import update_state_from_ui
from tests.param_store_test_support import mutate_runtime, runtime_state


def test_snapshot_includes_meta_state_and_ordinal():
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="site-1", arg="r")
    record = FrameParamRecord(
        key=key,
        base=0.5,
        meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
        effective=0.5,
        source="code",
        explicit=True,
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
        effective=0.1,
        source="code",
        explicit=False,
    )
    merge_frame_params(store, [record])
    update_state_from_ui(store, key, 0.1, meta=record.meta, override=True)

    payload = dumps_param_store(store)
    loaded = loads_param_store_result(payload).store

    snap = store_snapshot(loaded)
    meta, state, ordinal, label = snap[key]
    assert meta.kind == "float"
    assert meta.ui_min == -1.0
    assert meta.ui_max == 1.0
    assert state.override is True
    assert state.ui_value == 0.1
    assert ordinal == 1
    assert_invariants(loaded)


def test_codec_roundtrip_emits_json_arrays_and_restores_vec3_cc_key_tuple():
    store = ParamStore()
    key = ParameterKey(op="scale", site_id="site-v", arg="p")
    record = FrameParamRecord(
        key=key,
        base=(0.0, 0.0, 0.0),
        meta=ParamMeta(kind="vec3", ui_min=-1.0, ui_max=1.0),
        effective=(0.0, 0.0, 0.0),
        source="code",
        explicit=True,
    )
    merge_frame_params(store, [record])
    stored_meta = store.get_meta(key)
    assert stored_meta is not None
    update_state_from_ui(store, key, (0.0, 0.0, 0.0), meta=stored_meta, cc_key=(1, None, 3))

    payload = encode_param_store(store)
    state_payload = payload["states"][0]
    assert state_payload["ui_value"] == [0.0, 0.0, 0.0]
    assert state_payload["cc_key"] == [1, None, 3]

    loaded = decode_param_store_result(payload).store

    snap = store_snapshot(loaded)
    _meta, state, _ordinal, _label = snap[key]
    assert state.ui_value == (0.0, 0.0, 0.0)
    assert state.cc_key == (1, None, 3)
    assert_invariants(loaded)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        (
            "ui_value",
            (0.0, 0.0, 0.0),
            "ui_value must be a three-item list",
        ),
        (
            "cc_key",
            (1, None, 3),
            "cc_key must be an int, a three-item list, or null",
        ),
    ],
)
def test_direct_decode_rejects_python_tuple_for_json_array_fields(
    field: str,
    value: object,
    reason: str,
) -> None:
    store = ParamStore()
    key = ParameterKey(op="scale", site_id="strict-json-array", arg="p")
    meta = ParamMeta(kind="vec3", ui_min=-1.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=(0.0, 0.0, 0.0),
                meta=meta,
                effective=(0.0, 0.0, 0.0),
                source="code",
                explicit=True,
            )
        ],
    )
    payload = encode_param_store(store)
    payload["states"][0][field] = value

    result = decode_param_store_result(payload)

    assert result.store.get_state(key) is None
    assert any(
        issue.section == "states" and issue.index == 0 and reason in issue.reason
        for issue in result.issues
    )


def test_json_roundtrip_canonicalizes_rgb_ui_value_to_tuple():
    store = ParamStore()
    key = ParameterKey(op="style", site_id="site-rgb", arg="color")
    meta = ParamMeta(kind="rgb", ui_min=0, ui_max=255)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=(0, 0, 0),
                meta=meta,
                effective=(0, 0, 0),
                source="code",
                explicit=True,
            )
        ],
    )
    update_state_from_ui(store, key, (1, 2, 3), meta=meta, override=True)

    loaded = loads_param_store_result(dumps_param_store(store)).store
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


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported parameter kind"):
        ParamMeta(kind="__unknown__")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "explicit",
    [
        1,
        0,
    ],
)
def test_frame_record_rejects_non_bool_explicit(explicit: object) -> None:
    key = ParameterKey(op="circle", site_id="strict-flags", arg="r")
    with pytest.raises(TypeError, match="bool"):
        FrameParamRecord(
            key=key,
            base=1.0,
            meta=ParamMeta(kind="float"),
            effective=1.0,
            source="code",
            explicit=explicit,  # type: ignore[arg-type]
        )


def test_ui_update_rejects_non_bool_override() -> None:
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="strict-explicit", arg="r")

    ok, error = update_state_from_ui(
        store,
        key,
        1.0,
        meta=ParamMeta(kind="float"),
        override=1,  # type: ignore[arg-type]
    )

    assert ok is False
    assert error == "override must be an exact bool or None"
    assert store.get_state(key) is None


def test_snapshot_rejects_corrupt_non_bool_override() -> None:
    state = ParamState(override=True, ui_value=1.0)
    state.override = 1  # type: ignore[assignment]

    with pytest.raises(TypeError, match="state.override.*bool"):
        ParamStateSnapshot.from_state(state)


def test_snapshot_rejects_mutable_ui_value() -> None:
    state = ParamState(override=True, ui_value=1.0)
    state.ui_value = [1.0]

    with pytest.raises(TypeError, match="state.ui_value"):
        ParamStateSnapshot.from_state(state)


@pytest.mark.parametrize("cc_key", ([1, 2, 3], 128, (None, None, None)))
def test_snapshot_rejects_noncanonical_cc_key(cc_key: object) -> None:
    state = ParamState(override=True, ui_value=1.0)
    state.cc_key = cc_key  # type: ignore[assignment]

    with pytest.raises((TypeError, ValueError), match="MIDI CC"):
        ParamStateSnapshot.from_state(state)


def test_snapshot_direct_constructor_enforces_class_invariants() -> None:
    with pytest.raises(TypeError, match="state.override"):
        ParamStateSnapshot(override=1, ui_value=1.0, cc_key=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="state.ui_value"):
        ParamStateSnapshot(override=True, ui_value=[1.0], cc_key=None)
    with pytest.raises(TypeError, match="MIDI CC"):
        ParamStateSnapshot(
            override=True,
            ui_value=1.0,
            cc_key=[1, 2, 3],  # type: ignore[arg-type]
        )


def test_param_state_rejects_non_bool_override_at_construction() -> None:
    with pytest.raises(TypeError, match="override"):
        ParamState(override=1)  # type: ignore[arg-type]


def test_param_state_rejects_mutable_values_at_construction() -> None:
    with pytest.raises(TypeError, match="ui_value"):
        ParamState(ui_value=[1.0])
    with pytest.raises(TypeError, match="MIDI CC"):
        ParamState(ui_value=1.0, cc_key=[1, 2, 3])  # type: ignore[arg-type]


def test_replace_contents_is_deep_and_invalidates_all_revision_domains() -> None:
    target = ParamStore()
    source = ParamStore()
    target_key = ParameterKey("line", "target", "length")
    source_key = ParameterKey("line", "source", "length")
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=10.0)
    merge_frame_params(
        target,
        [
            FrameParamRecord(
                key=target_key,
                base=1.0,
                meta=meta,
                effective=1.0,
                source="code",
                explicit=False,
            )
        ],
    )
    merge_frame_params(
        source,
        [
            FrameParamRecord(
                key=source_key,
                base=2.0,
                meta=meta,
                effective=2.0,
                source="code",
                explicit=False,
            )
        ],
    )
    set_parameters_favorite(source, (source_key,), favorite=True)
    source.set_collapsed(
        primitive_collapsed_header_key((source_key.op, source_key.site_id)),
        collapsed=True,
    )
    mutate_runtime(
        source,
        lambda runtime: runtime.observed_groups.add(("line", "observed")),
    )

    old_snapshot = store_snapshot(target)
    old_runtime = target._read().runtime_token()
    old_runtime_state = runtime_state(target)
    revisions = (
        target.revision,
        target.table_revision,
        target.value_revision,
        target.style_revision,
        target.favorite_revision,
        old_runtime_state.effective_revision,
        old_runtime_state.visibility_revision,
    )

    target.replace_contents_from(source)

    assert target.get_state(target_key) is None
    assert target.get_state(source_key) is not None
    assert favorite_parameter_key_set(target) == frozenset({source_key})
    assert target.collapsed_headers() == {
        primitive_collapsed_header_key((source_key.op, source_key.site_id))
    }
    assert store_snapshot(target) is not old_snapshot
    assert target._read().runtime_token() != old_runtime
    target_runtime = runtime_state(target)
    assert (
        target.revision,
        target.table_revision,
        target.value_revision,
        target.style_revision,
        target.favorite_revision,
        target_runtime.effective_revision,
        target_runtime.visibility_revision,
    ) == tuple(value + 1 for value in revisions)
    assert target.value_changes_since(revisions[2]) is None
    assert target_runtime.effective_changes_since(revisions[5]) is None

    assert update_state_from_ui(
        source,
        source_key,
        9.0,
        meta=meta,
    )[0]
    assert target.get_state(source_key).ui_value == 2.0  # type: ignore[union-attr]
