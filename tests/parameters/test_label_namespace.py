from src.api import E, G
from src.parameters import ParamStore, ParameterKey
from src.parameters.context import parameter_context


def test_primitive_name_sets_label():
    store = ParamStore()

    with parameter_context(store=store):
        G(name="p1").circle(r=1.0)

    snap = store.snapshot()
    assert len(snap) == 1
    (key, (_meta, _state, _ordinal, label)) = next(iter(snap.items()))
    assert key.op == "circle"
    assert label == "p1"


def test_effect_name_sets_label():
    store = ParamStore()

    with parameter_context(store=store):
        # 単一 step でも chain 名が保存されることを確認
        builder = E(name="chain1").scale(s=2.0)
        builder(G.circle(r=1.0))

    snap = store.snapshot()
    # scale エントリの label を確認
    labels = {k.op: lbl for k, (_m, _s, _o, lbl) in snap.items()}
    assert labels.get("scale") == "chain1"


def test_name_overwrites_last_value():
    store = ParamStore()
    with parameter_context(store=store):
        G(name="first").circle(r=1.0)
        G(name="second").circle(r=1.0)  # same site_id -> last wins

    snap = store.snapshot()
    # 最後の指定 "second" がどこかに入っていることを確認
    labels = [lbl for _k, (_m, _s, _o, lbl) in snap.items()]
    assert "second" in labels
