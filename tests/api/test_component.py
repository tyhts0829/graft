import pytest

from grafix import E, G, component
from grafix.core.parameters import ParamStore
from grafix.core.parameters.context import parameter_context
from grafix.core.parameters.snapshot_ops import store_snapshot
from grafix.core.parameters.ui_ops import update_state_from_ui


def test_component_records_only_public_params_and_mutes_internal() -> None:
    store = ParamStore()

    meta = {"x": {"kind": "float", "ui_min": 0.0, "ui_max": 10.0}}

    @component(meta=meta)
    def foo(*, x: float = 1.0, name=None, key=None) -> float:
        _g = G(name="internal").polygon(n_sides=6)
        _ = E(name="internal_eff").affine(delta=(0.0, 0.0, 0.0))(_g)
        return float(x)

    with parameter_context(store=store):
        foo(x=2.0)

    snap = store_snapshot(store)
    component_entries = [(k, v) for k, v in snap.items() if k.op == "component.foo"]
    assert {k.arg for k, _v in component_entries} == {"x"}

    # 関数本体内の G/E は mute されるので、内部 primitive/effect は ParamStore に出ない。
    assert all(k.op != "polygon" for k in snap.keys())
    assert all(k.op != "affine" for k in snap.keys())
    assert all(op != "polygon" for (op, _site_id) in store._labels_ref().as_dict())  # type: ignore[attr-defined]
    assert all(op != "affine" for (op, _site_id) in store._labels_ref().as_dict())  # type: ignore[attr-defined]


def test_component_passes_resolved_params_to_function() -> None:
    store = ParamStore()

    meta = {"x": {"kind": "float", "ui_min": 0.0, "ui_max": 10.0}}

    @component(meta=meta)
    def foo(*, x: float = 1.0, name=None, key=None) -> float:
        return float(x)

    def _call() -> float:
        return foo(x=1.0)

    with parameter_context(store=store):
        assert _call() == 1.0

    snap = store_snapshot(store)
    key = next(k for k in snap.keys() if k.op == "component.foo" and k.arg == "x")
    meta_x = snap[key][0]

    ok, err = update_state_from_ui(store, key, 3.0, meta=meta_x, override=True)
    assert ok and err is None

    with parameter_context(store=store):
        assert _call() == 3.0


def test_component_key_splits_instances_from_same_callsite() -> None:
    store = ParamStore()
    meta = {"x": {"kind": "float", "ui_min": 0.0, "ui_max": 10.0}}

    @component(meta=meta)
    def foo(*, x: float = 1.0, name=None, key=None) -> float:
        return float(x)

    with parameter_context(store=store):
        for i in range(2):
            foo(key=i)

    snap = store_snapshot(store)
    site_ids = {k.site_id for k in snap.keys() if k.op == "component.foo"}
    assert len(site_ids) == 2


def test_component_meta_dict_spec_rejects_unknown_key() -> None:
    meta = {"x": {"kind": "float", "bad": 123}}

    with pytest.raises(ValueError, match="未知キー"):

        @component(meta=meta)
        def foo(*, x: float = 1.0, name=None, key=None) -> float:
            return float(x)
