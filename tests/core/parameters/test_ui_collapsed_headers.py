from __future__ import annotations

import json

from grafix.core.parameters import ParamMeta, ParamStore, ParameterKey
from grafix.core.parameters.collapsed_header import (
    STYLE_COLLAPSED_HEADER_KEY,
    effect_chain_collapsed_header_key,
    preset_collapsed_header_key,
    primitive_collapsed_header_key,
)
from grafix.core.parameters.codec import (
    dumps_param_store,
    loads_param_store_result,
)
from grafix.core.parameters.effect_order_ops import merge_frame_effect_chains
from grafix.core.parameters.effects import EffectStepTopology
from grafix.core.parameters.frame_params import (
    FrameEffectChainRecord,
    FrameParamRecord,
)
from grafix.core.parameters.invariants import assert_invariants
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.prune_ops import prune_groups


def _polyhedron_records(site_id: str) -> list[FrameParamRecord]:
    meta = ParamMeta(kind="int", ui_min=0, ui_max=4)
    return [
        FrameParamRecord(
            key=ParameterKey(op="polyhedron", site_id=site_id, arg="type_index"),
            base=0,
            meta=meta,
            effective=0,
            source="code",
            explicit=False,
        )
    ]


def test_ui_collapsed_headers_are_preserved_on_json_roundtrip():
    store = ParamStore()
    store.set_all_collapsed(
        {
            STYLE_COLLAPSED_HEADER_KEY,
            primitive_collapsed_header_key(("circle", "c:1")),
            preset_collapsed_header_key(("preset.logo", "p:1")),
            effect_chain_collapsed_header_key("chain:1"),
        },
        collapsed=True,
    )

    loaded = loads_param_store_result(dumps_param_store(store)).store
    assert loaded.collapsed_headers() == {
        STYLE_COLLAPSED_HEADER_KEY,
        primitive_collapsed_header_key(("circle", "c:1")),
        preset_collapsed_header_key(("preset.logo", "p:1")),
        effect_chain_collapsed_header_key("chain:1"),
    }
    assert_invariants(loaded)


def test_ui_collapsed_headers_use_v4_tagged_records():
    store = ParamStore()
    store.set_all_collapsed(
        {
            STYLE_COLLAPSED_HEADER_KEY,
            primitive_collapsed_header_key(("circle", "site")),
            preset_collapsed_header_key(("preset.logo", "site")),
            effect_chain_collapsed_header_key("chain"),
        },
        collapsed=True,
    )

    payload = json.loads(dumps_param_store(store))

    assert payload["schema_version"] == 4
    assert payload["ui"]["collapsed_headers"] == [
        {"kind": "effect_chain", "chain_id": "chain"},
        {"kind": "preset", "op": "preset.logo", "site_id": "site"},
        {"kind": "primitive", "op": "circle", "site_id": "site"},
        {"kind": "style"},
    ]


def test_invalid_collapsed_header_record_is_diagnosed_and_dropped():
    payload = json.loads(dumps_param_store(ParamStore()))
    payload["ui"]["collapsed_headers"] = [
        "primitive:circle:site",
        {"kind": "primitive", "op": "circle", "site_id": "site"},
        {
            "kind": "primitive",
            "op": "circle",
            "site_id": "site",
            "legacy": True,
        },
    ]

    result = loads_param_store_result(json.dumps(payload))

    assert result.store.collapsed_headers() == {
        primitive_collapsed_header_key(("circle", "site"))
    }
    assert [
        issue.index
        for issue in result.issues
        if issue.section == "ui.collapsed_headers"
    ] == [0, 2]


def test_reconcile_migrates_collapsed_header_state_for_primitive_groups():
    old_site_id = "old-site"
    new_site_id = "new-site"

    original = ParamStore()
    merge_frame_params(original, _polyhedron_records(old_site_id))
    old_header = primitive_collapsed_header_key(("polyhedron", old_site_id))
    new_header = primitive_collapsed_header_key(("polyhedron", new_site_id))
    original.set_collapsed(old_header, collapsed=True)

    # 永続化ロード相当（loaded_groups を持つ状態にする）
    store = loads_param_store_result(dumps_param_store(original)).store

    # 新 site_id のグループを観測（=site_id がズレた状態を再現）
    merge_frame_params(store, _polyhedron_records(new_site_id))

    collapsed = store.collapsed_headers()
    assert old_header not in collapsed
    assert new_header in collapsed
    assert_invariants(store)


def test_reconcile_migrates_collapsed_header_state_for_preset_groups():
    old_site_id = "old-site"
    new_site_id = "new-site"
    original = ParamStore()
    merge_frame_params(original, _polyhedron_records(old_site_id))
    old_header = preset_collapsed_header_key(("polyhedron", old_site_id))
    new_header = preset_collapsed_header_key(("polyhedron", new_site_id))
    original.set_collapsed(old_header, collapsed=True)

    store = loads_param_store_result(dumps_param_store(original)).store
    merge_frame_params(store, _polyhedron_records(new_site_id))

    assert old_header not in store.collapsed_headers()
    assert new_header in store.collapsed_headers()
    assert_invariants(store)


def test_prune_removes_collapsed_header_state_for_removed_groups_and_unused_chains():
    store = ParamStore()
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)

    merge_frame_params(store, _polyhedron_records("p0"))
    primitive_header = primitive_collapsed_header_key(("polyhedron", "p0"))
    preset_header = preset_collapsed_header_key(("polyhedron", "p0"))
    store.set_all_collapsed(
        {primitive_header, preset_header},
        collapsed=True,
    )

    merge_frame_effect_chains(
        store,
        [
            FrameEffectChainRecord(
                chain_id="c1",
                steps=(EffectStepTopology("scale", "s0", 1, 0),),
            )
        ],
        observation_complete=False,
    )
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=ParameterKey(op="scale", site_id="s0", arg="x"),
                base=0.0,
                meta=meta,
                effective=0.0,
                source="code",
                explicit=True,
            )
        ],
    )
    effect_header = effect_chain_collapsed_header_key("c1")
    store.set_collapsed(effect_header, collapsed=True)

    prune_groups(store, [("polyhedron", "p0"), ("scale", "s0")])

    collapsed = store.collapsed_headers()
    assert primitive_header not in collapsed
    assert preset_header not in collapsed
    assert effect_header not in collapsed
    assert_invariants(store)
