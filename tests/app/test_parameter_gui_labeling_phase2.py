from src.api import E, G
from src.app.parameter_gui.labeling import (
    effect_chain_header_display_names_from_snapshot,
    effect_step_ordinals_by_site,
    format_param_row_label,
)
from src.core.effect_registry import effect_registry
from src.parameters import ParamStore, ParameterKey, parameter_context


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

    snap = store.snapshot()
    chain_header_by_id = effect_chain_header_display_names_from_snapshot(
        snap,
        step_info_by_site=step_info_by_site,
        chain_ordinal_by_id=store.chain_ordinals(),
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

