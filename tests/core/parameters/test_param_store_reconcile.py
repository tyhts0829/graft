from __future__ import annotations

import json

from grafix.core.parameters import ParamMeta, ParamStore, ParameterKey
from grafix.core.parameters.codec import dumps_param_store, loads_param_store
from grafix.core.parameters.context import parameter_context
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.layer_style import LAYER_STYLE_OP, layer_style_records
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.prune_ops import prune_groups, prune_stale_loaded_groups
from grafix.core.parameters.snapshot_ops import store_snapshot, store_snapshot_for_gui
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.core.parameters.invariants import assert_invariants


def _roundtrip_store(store: ParamStore) -> ParamStore:
    payload = dumps_param_store(store)
    # to_json は json.dumps なので、ここで破損していないことも軽く確認する。
    json.loads(payload)
    return loads_param_store(payload)


def _polyhedron_records(site_id: str) -> list[FrameParamRecord]:
    return [
        FrameParamRecord(
            key=ParameterKey(op="polyhedron", site_id=site_id, arg="type_index"),
            base=0,
            meta=ParamMeta(kind="int", ui_min=0, ui_max=4),
            explicit=False,
        ),
        FrameParamRecord(
            key=ParameterKey(op="polyhedron", site_id=site_id, arg="center"),
            base=(0.0, 0.0, 0.0),
            meta=ParamMeta(kind="vec3", ui_min=-500.0, ui_max=500.0),
            explicit=False,
        ),
        FrameParamRecord(
            key=ParameterKey(op="polyhedron", site_id=site_id, arg="scale"),
            base=(1.0, 1.0, 1.0),
            meta=ParamMeta(kind="vec3", ui_min=0.0, ui_max=200.0),
            explicit=False,
        ),
    ]


def _sphere_records(site_id: str) -> list[FrameParamRecord]:
    return [
        FrameParamRecord(
            key=ParameterKey(op="sphere", site_id=site_id, arg="type_index"),
            base=0,
            meta=ParamMeta(kind="int", ui_min=0, ui_max=3),
            explicit=False,
        ),
        FrameParamRecord(
            key=ParameterKey(op="sphere", site_id=site_id, arg="subdivisions"),
            base=0,
            meta=ParamMeta(kind="int", ui_min=0, ui_max=8),
            explicit=False,
        ),
    ]


def test_reconcile_migrates_state_and_hides_stale_group_in_gui_snapshot():
    old_site_id = "old-site"
    new_site_id = "new-site"

    original = ParamStore()
    merge_frame_params(original, _polyhedron_records(old_site_id))
    old_center_key = ParameterKey(op="polyhedron", site_id=old_site_id, arg="center")
    old_center_meta = original.get_meta(old_center_key)
    assert old_center_meta is not None
    update_state_from_ui(
        original,
        old_center_key,
        (12.0, 34.0, 56.0),
        meta=old_center_meta,
        override=True,
    )

    store = _roundtrip_store(original)

    # 新 site_id のグループを観測（=site_id がズレた状態を再現）
    merge_frame_params(store, _polyhedron_records(new_site_id))

    new_center_key = ParameterKey(op="polyhedron", site_id=new_site_id, arg="center")

    # ステートが新グループへ移っている（誤マッチ防止のため kind 一致が前提）。
    new_state = store.get_state(new_center_key)
    assert new_state is not None
    assert new_state.override is True
    assert tuple(new_state.ui_value) == (12.0, 34.0, 56.0)

    # GUI 表示用スナップショットでは旧グループが隠れる（増殖しない）。
    gui_snapshot = store_snapshot_for_gui(store)
    assert old_center_key not in gui_snapshot
    assert new_center_key in gui_snapshot
    assert_invariants(store)


def test_prune_stale_loaded_groups_removes_old_entries_on_save_path():
    old_site_id = "old-site"
    new_site_id = "new-site"

    original = ParamStore()
    merge_frame_params(original, _polyhedron_records(old_site_id))
    store = _roundtrip_store(original)

    merge_frame_params(store, _polyhedron_records(new_site_id))
    prune_stale_loaded_groups(store)

    full_snapshot = store_snapshot(store)
    assert ParameterKey(op="polyhedron", site_id=old_site_id, arg="center") not in full_snapshot
    assert ParameterKey(op="polyhedron", site_id=new_site_id, arg="center") in full_snapshot
    assert_invariants(store)


def test_op_change_hides_loaded_group_in_gui_snapshot_and_prunes_on_save_path():
    # polyhedron -> sphere の差し替えで、旧 op のグループが GUI/保存に残らないこと。
    poly_site_id = "poly-site"
    sphere_site_id = "sphere-site"

    original = ParamStore()
    merge_frame_params(original, _polyhedron_records(poly_site_id))
    store = _roundtrip_store(original)

    merge_frame_params(store, _sphere_records(sphere_site_id))

    gui_snapshot = store_snapshot_for_gui(store)
    assert all(k.op != "polyhedron" for k in gui_snapshot.keys())
    assert any(k.op == "sphere" for k in gui_snapshot.keys())

    prune_stale_loaded_groups(store)
    full_snapshot = store_snapshot(store)
    assert all(k.op != "polyhedron" for k in full_snapshot.keys())
    assert_invariants(store)


def test_layer_style_site_id_change_hides_loaded_group_and_prunes_on_save_path():
    old_site_id = "old-layer"
    new_site_id = "new-layer"

    original = ParamStore()
    merge_frame_params(
        original,
        layer_style_records(
            layer_site_id=old_site_id,
            base_line_thickness=0.01,
            base_line_color_rgb01=(1.0, 0.0, 0.0),
            explicit_line_thickness=False,
            explicit_line_color=False,
        ),
    )
    store = _roundtrip_store(original)

    merge_frame_params(
        store,
        layer_style_records(
            layer_site_id=new_site_id,
            base_line_thickness=0.01,
            base_line_color_rgb01=(1.0, 0.0, 0.0),
            explicit_line_thickness=False,
            explicit_line_color=False,
        ),
    )

    gui_snapshot = store_snapshot_for_gui(store)
    layer_sites = {k.site_id for k in gui_snapshot.keys() if k.op == LAYER_STYLE_OP}
    assert layer_sites == {new_site_id}

    prune_stale_loaded_groups(store)
    full_snapshot = store_snapshot(store)
    layer_sites_full = {k.site_id for k in full_snapshot.keys() if k.op == LAYER_STYLE_OP}
    assert old_site_id not in layer_sites_full
    assert_invariants(store)


def test_from_json_compacts_ordinals_across_all_ops():
    payload = json.dumps(
        {
            "ordinals": {
                LAYER_STYLE_OP: {"layer-site": 3},
                "polyhedron": {"a": 2, "b": 5},
            }
        }
    )
    store = loads_param_store(payload)
    assert store.get_ordinal(LAYER_STYLE_OP, "layer-site") == 1
    assert store.get_ordinal("polyhedron", "a") == 1
    assert store.get_ordinal("polyhedron", "b") == 2
    assert_invariants(store)


def test_reconcile_does_not_migrate_when_ambiguous():
    # 同じ op/args を持つ stale が複数あるときは誤マッチを避けて移行しない。
    original = ParamStore()
    old_a_records = _polyhedron_records("old-a")
    old_b_records = _polyhedron_records("old-b")
    merge_frame_params(original, old_a_records)
    merge_frame_params(original, old_b_records)

    key_old_a = ParameterKey(op="polyhedron", site_id="old-a", arg="type_index")
    meta_old_a = next(rec.meta for rec in old_a_records if rec.key == key_old_a)
    update_state_from_ui(original, key_old_a, 1, meta=meta_old_a, override=True)

    key_old_b = ParameterKey(op="polyhedron", site_id="old-b", arg="type_index")
    meta_old_b = next(rec.meta for rec in old_b_records if rec.key == key_old_b)
    update_state_from_ui(original, key_old_b, 4, meta=meta_old_b, override=True)

    store = _roundtrip_store(original)

    fresh_site_id = "new"
    merge_frame_params(store, _polyhedron_records(fresh_site_id))

    fresh_key = ParameterKey(op="polyhedron", site_id=fresh_site_id, arg="type_index")
    state = store.get_state(fresh_key)
    assert state is not None
    # base=0 のまま（どちらの stale にも寄せない）
    assert state.ui_value == 0
    assert_invariants(store)


def test_prune_removes_stale_effect_steps_and_unused_chain_ordinals():
    old_step_site_id = "old-step"
    old_chain_id = "old-chain"
    new_step_site_id = "new-step"
    new_chain_id = "new-chain"

    original = ParamStore()
    old_key = ParameterKey(op="rotate", site_id=old_step_site_id, arg="angle")
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=360.0)
    merge_frame_params(
        original,
        [
            FrameParamRecord(
                key=old_key,
                base=0.0,
                meta=meta,
                explicit=True,
                chain_id=old_chain_id,
                step_index=0,
            )
        ],
    )
    update_state_from_ui(original, old_key, 90.0, meta=meta, override=True)

    store = _roundtrip_store(original)

    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=ParameterKey(op="rotate", site_id=new_step_site_id, arg="angle"),
                base=0.0,
                meta=meta,
                explicit=True,
                chain_id=new_chain_id,
                step_index=0,
            )
        ],
    )

    prune_stale_loaded_groups(store)

    assert store.get_effect_step("rotate", old_step_site_id) is None
    assert old_chain_id not in store.chain_ordinals()
    assert new_chain_id in store.chain_ordinals()
    assert_invariants(store)


def test_effect_chain_ordinals_do_not_duplicate_after_removing_first_chain():
    store = ParamStore()
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)

    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=ParameterKey(op="scale", site_id="s0", arg="x"),
                base=0.0,
                meta=meta,
                explicit=True,
                chain_id="c1",
                step_index=0,
            ),
            FrameParamRecord(
                key=ParameterKey(op="rotate", site_id="s1", arg="x"),
                base=0.0,
                meta=meta,
                explicit=True,
                chain_id="c2",
                step_index=0,
            ),
        ],
    )

    # 最初のチェーン（ordinal=1）を消しても、残ったチェーンの ordinal は維持され得る。
    # その後の新規採番で重複しないことが重要。
    prune_groups(store, [("scale", "s0")])
    assert store.chain_ordinals() == {"c2": 2}

    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=ParameterKey(op="translate", site_id="s2", arg="x"),
                base=0.0,
                meta=meta,
                explicit=True,
                chain_id="c3",
                step_index=0,
            )
        ],
    )
    chain_ordinals = store.chain_ordinals()
    assert chain_ordinals["c2"] == 2
    assert chain_ordinals["c3"] == 3
    assert len(set(chain_ordinals.values())) == len(chain_ordinals)
    assert_invariants(store)


def test_load_repairs_duplicate_chain_ordinals():
    payload = json.dumps(
        {
            "ordinals": {"scale": {"s0": 1}, "rotate": {"s1": 1}},
            "effect_steps": [
                {
                    "op": "scale",
                    "site_id": "s0",
                    "chain_id": "a",
                    "step_index": 0,
                },
                {
                    "op": "rotate",
                    "site_id": "s1",
                    "chain_id": "b",
                    "step_index": 0,
                },
            ],
            "chain_ordinals": {"a": 1, "b": 1},  # 重複（壊れた過去データを模擬）
        }
    )
    store = loads_param_store(payload)
    chain_ordinals = store.chain_ordinals()
    assert chain_ordinals["a"] == 1
    assert chain_ordinals["b"] == 2
    assert len(set(chain_ordinals.values())) == len(chain_ordinals)
    assert_invariants(store)


def _compile_draw(source: str):
    ns: dict[str, object] = {}
    exec(compile(source, filename="main.py", mode="exec"), ns)
    return ns["draw"]


def test_reconcile_handles_g_polyhedron_kwargs_edit_without_gui_duplication():
    # 1回目: G.polyhedron() を実行し、GUI 相当の調整値を保存する。
    draw_v1 = _compile_draw(
        """
from grafix.api.primitives import G

def draw():
    G.polyhedron()
"""
    )

    store_v1 = ParamStore()
    with parameter_context(store_v1):
        draw_v1()

    # 何か 1 つ値を変えたことにする（=GUI 調整）。
    snap_v1 = store_snapshot(store_v1)
    key_v1 = next(
        k
        for k in snap_v1.keys()
        if k.op == "polyhedron" and k.arg == "type_index"
    )
    meta_v1, _state_v1, _ordinal_v1, _label_v1 = snap_v1[key_v1]
    update_state_from_ui(store_v1, key_v1, 3, meta=meta_v1, override=True)

    store = _roundtrip_store(store_v1)

    # 2回目: 同じ draw のつもりで kwargs を明示（バイトコードが変わって site_id が変わり得る）。
    draw_v2 = _compile_draw(
        """
from grafix.api.primitives import G

def draw():
    G.polyhedron(type_index=2)
"""
    )

    with parameter_context(store):
        draw_v2()

    # GUI 表示では増殖しない（polyhedron の site_id は 1 つだけ見える）。
    gui_snapshot = store_snapshot_for_gui(store)
    poly_sites = {k.site_id for k in gui_snapshot.keys() if k.op == "polyhedron"}
    assert len(poly_sites) == 1

    # 値が引き継がれている（曖昧ではないので migrate される）。
    type_key = next(k for k in gui_snapshot.keys() if k.op == "polyhedron" and k.arg == "type_index")
    st = store.get_state(type_key)
    assert st is not None
    assert st.ui_value == 3
    assert_invariants(store)
