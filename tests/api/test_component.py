import pytest

from grafix import E, G, P, preset
from grafix.core.geometry import Geometry
from grafix.core.parameters import ParamStore
from grafix.core.parameters.context import parameter_context
from grafix.core.parameters.snapshot_ops import store_snapshot
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.core.realize import realize
from grafix.core.scene import normalize_scene


def test_component_records_only_public_params_and_mutes_internal() -> None:
    store = ParamStore()

    meta = {"x": {"kind": "float", "ui_min": 0.0, "ui_max": 10.0}}

    @preset(meta=meta)
    def component_records(*, x: float = 1.0) -> Geometry:
        geometry = G(name="internal").polygon(n_sides=6)
        return E(name="internal_eff").affine(delta=(0.0, 0.0, 0.0))(geometry)

    with parameter_context(store=store):
        component_records(x=2.0)

    snap = store_snapshot(store)
    preset_entries = [(k, v) for k, v in snap.items() if k.op == "preset.component_records"]
    assert {k.arg for k, _v in preset_entries} == {"activate", "x"}

    # 関数本体内の G/E は mute されるので、内部 primitive/effect は ParamStore に出ない。
    assert all(k.op != "polygon" for k in snap.keys())
    assert all(k.op != "affine" for k in snap.keys())
    assert all(op != "polygon" for op, _site_id in store._read().label_items())
    assert all(op != "affine" for op, _site_id in store._read().label_items())


def test_component_passes_resolved_params_to_function() -> None:
    store = ParamStore()

    meta = {"x": {"kind": "float", "ui_min": 0.0, "ui_max": 10.0}}

    @preset(meta=meta)
    def component_resolves(*, x: float = 1.0) -> Geometry:
        return Geometry.create(op="concat", params={"x": float(x)})

    def _call() -> Geometry:
        return component_resolves(x=1.0)

    with parameter_context(store=store):
        assert dict(_call().args)["x"] == 1.0

    snap = store_snapshot(store)
    key = next(k for k in snap.keys() if k.op == "preset.component_resolves" and k.arg == "x")
    meta_x = snap[key][0]

    ok, err = update_state_from_ui(store, key, 3.0, meta=meta_x, override=True)
    assert ok and err is None

    with parameter_context(store=store):
        assert dict(_call().args)["x"] == 3.0


def test_component_key_splits_instances_from_same_callsite() -> None:
    store = ParamStore()
    meta = {"x": {"kind": "float", "ui_min": 0.0, "ui_max": 10.0}}

    @preset(meta=meta)
    def component_key_split(*, x: float = 1.0) -> Geometry:
        return Geometry.create(op="concat", params={"x": float(x)})

    with parameter_context(store=store):
        for i in range(2):
            P(key=i).component_key_split()

    snap = store_snapshot(store)
    site_ids = {k.site_id for k in snap.keys() if k.op == "preset.component_key_split"}
    assert len(site_ids) == 2


def test_component_instance_key_and_shared_control_repeated_groups() -> None:
    meta = {"x": {"kind": "float", "ui_min": 0.0, "ui_max": 10.0}}
    @preset(meta=meta)
    def component_instance_identity(*, x: float = 1.0) -> Geometry:
        return Geometry.create(op="concat", params={"x": float(x)})

    individual_store = ParamStore()
    with parameter_context(store=individual_store):
        [P(instance_key=i).component_instance_identity() for i in range(3)]
    individual_sites = {
        key.site_id
        for key in store_snapshot(individual_store)
        if key.op == "preset.component_instance_identity"
    }
    assert len(individual_sites) == 3

    shared_store = ParamStore()
    with parameter_context(store=shared_store):
        for _i in range(3):
            P(shared=True).component_instance_identity()
    shared_sites = {
        key.site_id
        for key in store_snapshot(shared_store)
        if key.op == "preset.component_instance_identity"
    }
    assert len(shared_sites) == 1


def test_component_meta_dict_spec_rejects_unknown_key() -> None:
    meta = {"x": {"kind": "float", "bad": 123}}

    with pytest.raises(ValueError, match="未知キー"):

        @preset(meta=meta)
        def component_bad_meta(*, x: float = 1.0) -> Geometry:
            return Geometry.create(op="concat", params={"x": float(x)})


def test_component_deactivated_returns_normalizable_empty_scene() -> None:
    @preset(meta={})
    def component_disable_contract() -> Geometry:
        return Geometry.create(op="concat", params={"active": True})

    layers = normalize_scene(component_disable_contract(activate=False))

    assert len(layers) == 1
    assert layers[0].geometry.op == "concat"
    assert layers[0].geometry.args == ()

    realized = realize(layers[0].geometry)
    assert realized.coords.shape == (0, 3)
    assert realized.offsets.tolist() == [0]


def test_component_requires_exact_bool_activate() -> None:
    @preset(meta={})
    def component_strict_activate() -> Geometry:
        return Geometry.create(op="concat", params={"active": True})

    for invalid in ("false", "true", 0, 1, None):
        with pytest.raises(TypeError, match="exact bool"):
            component_strict_activate(activate=invalid)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="exact bool"):
            P.component_strict_activate(activate=invalid)
