import pytest

from src.parameters import ParameterKey, ParamMeta, ParamState, ParamStore, parameter_context, resolve_params


def test_override_priority_and_quantize():
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="s1", arg="r")
    store._states[key] = ParamState(override=True, ui_value=0.26, min=0.0, max=1.0, cc=None)  # type: ignore[attr-defined]
    store.get_ordinal("circle", "s1")

    meta = {"r": ParamMeta(kind="float", ui_min=0.0, ui_max=1.0, step=0.1)}
    params = {"r": 0.05}

    with parameter_context(store=store, cc_snapshot=None):
        resolved, _ = resolve_params(op="circle", params=params, meta=meta, site_id="s1")

    assert resolved["r"] == pytest.approx(0.3)  # 0.26 が step=0.1 で量子化


def test_base_used_when_no_state():
    store = ParamStore()
    meta = {"r": ParamMeta(kind="float", ui_min=0.0, ui_max=1.0, step=0.1)}
    params = {"r": 0.4}

    with parameter_context(store=store, cc_snapshot=None):
        resolved, _ = resolve_params(op="circle", params=params, meta=meta, site_id="s2")

    assert resolved["r"] == 0.4


def test_no_clamp_even_if_outside_ui_range():
    store = ParamStore()
    meta = {"cx": ParamMeta(kind="float", ui_min=-1.0, ui_max=1.0, step=0.1)}
    params = {"cx": 100.0}

    with parameter_context(store=store, cc_snapshot=None):
        resolved, _ = resolve_params(op="circle", params=params, meta=meta, site_id="s3")

    assert resolved["cx"] == 100.0
