from grafix.api import E, G
from grafix.core.parameters import ParamStore, ParameterKey
from grafix.core.parameters.context import parameter_context
from grafix.core.parameters.snapshot_ops import store_snapshot


def test_primitive_name_sets_label():
    store = ParamStore()

    with parameter_context(store=store):
        G(name="p1").polygon(n_sides=6)

    snap = store_snapshot(store)
    polygon_entries = [(k, v) for k, v in snap.items() if k.op == "polygon"]
    polygon_args = {k.arg for k, _v in polygon_entries}
    polygon_site_ids = {k.site_id for k, _v in polygon_entries}
    polygon_labels = {lbl for _k, (_m, _s, _o, lbl) in polygon_entries}

    assert polygon_args == {"n_sides", "phase", "center", "scale"}
    assert polygon_site_ids and len(polygon_site_ids) == 1
    assert polygon_labels == {"p1"}


def test_effect_name_sets_label():
    store = ParamStore()

    with parameter_context(store=store):
        # 単一 step でも chain 名が保存されることを確認
        builder = E(name="chain1").scale(scale=(2.0, 2.0, 2.0))
        builder(G.polygon(n_sides=6))

    snap = store_snapshot(store)
    # scale エントリの label を確認
    scale_labels = {lbl for k, (_m, _s, _o, lbl) in snap.items() if k.op == "scale"}
    assert scale_labels == {"chain1"}


def test_name_overwrites_last_value():
    store = ParamStore()
    with parameter_context(store=store):
        G(name="first").polygon(n_sides=6)
        G(name="second").polygon(n_sides=6)  # same site_id -> last wins

    snap = store_snapshot(store)
    # 最後の指定 "second" がどこかに入っていることを確認
    labels = [lbl for _k, (_m, _s, _o, lbl) in snap.items()]
    assert "second" in labels
