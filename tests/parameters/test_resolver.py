import pytest

from src.parameters import ParameterKey, ParamMeta, ParamState, ParamStore, parameter_context, resolve_params


def test_override_priority_and_quantize():
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="s1", arg="r")
    store._states[key] = ParamState(override=True, ui_value=0.2604, cc_key=None)  # type: ignore[attr-defined]
    store._meta[key] = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)  # type: ignore[attr-defined]
    store.get_ordinal("circle", "s1")

    meta = {"r": ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)}
    params = {"r": 0.05}

    with parameter_context(store=store, cc_snapshot=None):
        resolved = resolve_params(op="circle", params=params, meta=meta, site_id="s1")

    assert resolved["r"] == pytest.approx(0.260)  # グローバル step=1e-3 で量子化


def test_base_used_when_no_state():
    store = ParamStore()
    meta = {"r": ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)}
    params = {"r": 0.4}

    with parameter_context(store=store, cc_snapshot=None):
        resolved = resolve_params(op="circle", params=params, meta=meta, site_id="s2")

    assert resolved["r"] == 0.4


def test_no_clamp_even_if_outside_ui_range():
    store = ParamStore()
    meta = {"cx": ParamMeta(kind="float", ui_min=-1.0, ui_max=1.0)}
    params = {"cx": 100.0}

    with parameter_context(store=store, cc_snapshot=None):
        resolved = resolve_params(op="circle", params=params, meta=meta, site_id="s3")

    assert resolved["cx"] == 100.0


def test_vec_quantized_per_component():
    store = ParamStore()
    meta = {"p": ParamMeta(kind="vec3", ui_min=None, ui_max=None)}
    params = {"p": (1.23456, -0.0012, 0.5)}

    with parameter_context(store=store, cc_snapshot=None):
        resolved = resolve_params(op="scale", params=params, meta=meta, site_id="sv")

    assert resolved["p"] == pytest.approx((1.235, -0.001, 0.5))


def test_vec3_cc_applies_per_component():
    store = ParamStore()
    key = ParameterKey(op="scale", site_id="sv2", arg="p")
    store._states[key] = ParamState(  # type: ignore[attr-defined]
        override=False,
        ui_value=(0.0, 0.0, 0.0),
        cc_key=(10, 11, 12),
    )
    store._meta[key] = ParamMeta(kind="vec3", ui_min=-1.0, ui_max=1.0)  # type: ignore[attr-defined]
    store.get_ordinal("scale", "sv2")

    meta = {"p": ParamMeta(kind="vec3", ui_min=-1.0, ui_max=1.0)}
    params = {"p": (0.0, 0.0, 0.0)}

    with parameter_context(store=store, cc_snapshot={10: 0.0, 11: 0.5, 12: 1.0}):
        resolved = resolve_params(op="scale", params=params, meta=meta, site_id="sv2")

    assert resolved["p"] == pytest.approx((-1.0, 0.0, 1.0))
