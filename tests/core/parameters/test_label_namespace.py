from graft.api import E, G
from graft.core.parameters import ParamStore, ParameterKey
from graft.core.parameters.context import parameter_context


def test_primitive_name_sets_label():
    store = ParamStore()

    with parameter_context(store=store):
        G(name="p1").circle(r=1.0)

    snap = store.snapshot()
    circle_entries = [(k, v) for k, v in snap.items() if k.op == "circle"]
    circle_args = {k.arg for k, _v in circle_entries}
    circle_site_ids = {k.site_id for k, _v in circle_entries}
    circle_labels = {lbl for _k, (_m, _s, _o, lbl) in circle_entries}

    assert circle_args == {"r", "cx", "cy", "segments"}
    assert circle_site_ids and len(circle_site_ids) == 1
    assert circle_labels == {"p1"}


def test_effect_name_sets_label():
    store = ParamStore()

    with parameter_context(store=store):
        # 単一 step でも chain 名が保存されることを確認
        builder = E(name="chain1").scale(scale=(2.0, 2.0, 2.0))
        builder(G.circle(r=1.0))

    snap = store.snapshot()
    # scale エントリの label を確認
    scale_labels = {lbl for k, (_m, _s, _o, lbl) in snap.items() if k.op == "scale"}
    assert scale_labels == {"chain1"}


def test_name_overwrites_last_value():
    store = ParamStore()
    with parameter_context(store=store):
        G(name="first").circle(r=1.0)
        G(name="second").circle(r=1.0)  # same site_id -> last wins

    snap = store.snapshot()
    # 最後の指定 "second" がどこかに入っていることを確認
    labels = [lbl for _k, (_m, _s, _o, lbl) in snap.items()]
    assert "second" in labels
