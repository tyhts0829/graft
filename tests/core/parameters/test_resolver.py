import pytest

from grafix.core.parameters import (
    ParameterKey,
    ParamMeta,
    ParamStore,
    parameter_context,
    resolve_params,
)
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.ui_ops import update_state_from_ui


def test_override_priority_and_quantize():
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="s1", arg="r")
    meta_r = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.05,
                meta=meta_r,
                explicit=True,
            )
        ],
    )
    stored_meta = store.get_meta(key)
    assert stored_meta is not None
    update_state_from_ui(store, key, 0.2604, meta=stored_meta, override=True)

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
    meta_p = ParamMeta(kind="vec3", ui_min=-1.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=(0.0, 0.0, 0.0),
                meta=meta_p,
                explicit=True,
            )
        ],
    )
    stored_meta = store.get_meta(key)
    assert stored_meta is not None
    update_state_from_ui(
        store,
        key,
        (0.0, 0.0, 0.0),
        meta=stored_meta,
        override=False,
        cc_key=(10, 11, 12),
    )

    meta = {"p": ParamMeta(kind="vec3", ui_min=-1.0, ui_max=1.0)}
    params = {"p": (0.0, 0.0, 0.0)}

    with parameter_context(store=store, cc_snapshot={10: 0.0, 11: 0.5, 12: 1.0}):
        resolved = resolve_params(op="scale", params=params, meta=meta, site_id="sv2")

    assert resolved["p"] == pytest.approx((-1.0, 0.0, 1.0))
