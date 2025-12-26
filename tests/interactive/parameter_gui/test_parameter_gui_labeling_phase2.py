from grafix.api import E, G
from grafix.interactive.parameter_gui.labeling import (
    effect_chain_header_display_names_from_snapshot,
    effect_step_ordinals_by_site,
    format_param_row_label,
)
from grafix.core.effect_registry import effect_registry
from grafix.core.parameters import ParamStore, ParameterKey, parameter_context
from grafix.core.parameters.invariants import assert_invariants
from grafix.core.parameters.snapshot_ops import store_snapshot


def test_effect_chain_header_and_step_ordinals():
    store = ParamStore()

    with parameter_context(store):
        g = G.polygon()
        eff = E(name="xf").scale().rotate().scale()
        _out = eff(g)

    step_info_by_site = store.effect_steps()
    assert len(step_info_by_site) == 3

    chain_ids = {chain_id for chain_id, _step_index in step_info_by_site.values()}
    assert len(chain_ids) == 1
    (chain_id,) = tuple(chain_ids)

    steps = sorted(
        [(op, site_id, chain_id, step_index) for (op, site_id), (chain_id, step_index) in step_info_by_site.items()],
        key=lambda x: int(x[3]),
    )
    assert [int(step_index) for _op, _site, _cid, step_index in steps] == [0, 1, 2]
    assert [op for op, _site, _cid, _step_index in steps] == ["scale", "rotate", "scale"]

    step_ordinals = effect_step_ordinals_by_site(step_info_by_site)
    scale0_site = steps[0][1]
    rotate_site = steps[1][1]
    scale1_site = steps[2][1]
    assert step_ordinals[("scale", scale0_site)] == 1
    assert step_ordinals[("rotate", rotate_site)] == 1
    assert step_ordinals[("scale", scale1_site)] == 2

    snap = store_snapshot(store)
    display_order_by_group = {
        (op, site_id): i
        for i, (op, site_id, _cid, _step_index) in enumerate(steps, start=1)
    }
    chain_header_by_id = effect_chain_header_display_names_from_snapshot(
        snap,
        step_info_by_site=step_info_by_site,
        display_order_by_group=display_order_by_group,
        is_effect_op=lambda op: op in effect_registry,
    )
    assert chain_header_by_id == {chain_id: "xf"}

    key_scale0 = ParameterKey(op="scale", site_id=scale0_site, arg="auto_center")
    assert key_scale0 in snap
    assert (
        format_param_row_label("scale", step_ordinals[("scale", scale0_site)], "auto_center")
        == "scale#1 auto_center"
    )

    key_scale1 = ParameterKey(op="scale", site_id=scale1_site, arg="auto_center")
    assert key_scale1 in snap
    assert (
        format_param_row_label("scale", step_ordinals[("scale", scale1_site)], "auto_center")
        == "scale#2 auto_center"
    )
    assert_invariants(store)


def test_effect_chain_header_numbers_unnamed_chains_by_display_order():
    store = ParamStore()

    with parameter_context(store):
        g = G.polygon()
        named = E(name="xf").scale()
        unnamed1 = E.rotate()
        unnamed2 = E.translate()
        _out0 = unnamed1(g)
        _out1 = named(g)
        _out2 = unnamed2(g)

    snap = store_snapshot(store)
    step_info_by_site = store.effect_steps()

    rotate_site = next(site_id for (op, site_id) in step_info_by_site if op == "rotate")
    translate_site = next(
        site_id for (op, site_id) in step_info_by_site if op == "translate"
    )
    scale_site = next(site_id for (op, site_id) in step_info_by_site if op == "scale")

    rotate_chain_id = step_info_by_site[("rotate", rotate_site)][0]
    translate_chain_id = step_info_by_site[("translate", translate_site)][0]
    scale_chain_id = step_info_by_site[("scale", scale_site)][0]

    chain_header_by_id = effect_chain_header_display_names_from_snapshot(
        snap,
        step_info_by_site=step_info_by_site,
        display_order_by_group={
            ("rotate", rotate_site): 1,
            ("scale", scale_site): 2,
            ("translate", translate_site): 3,
        },
        is_effect_op=lambda op: op in effect_registry,
    )
    assert set(chain_header_by_id.values()) == {"xf", "effect#1", "effect#2"}
    assert chain_header_by_id[scale_chain_id] == "xf"
    assert chain_header_by_id[rotate_chain_id] == "effect#1"
    assert chain_header_by_id[translate_chain_id] == "effect#2"
    assert_invariants(store)
