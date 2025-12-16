from graft.api import G
from graft.core.parameters import ParamStore, parameter_context


def _override_by_arg(store: ParamStore, *, op: str) -> dict[str, bool]:
    snap = store.snapshot()
    return {
        key.arg: bool(state.override)
        for key, (_meta, state, _ordinal, _label) in snap.items()
        if key.op == op
    }


def test_implicit_defaults_start_with_override_on():
    store = ParamStore()

    def callsite() -> None:
        G.circle()

    with parameter_context(store=store, cc_snapshot=None):
        callsite()

    assert _override_by_arg(store, op="circle") == {
        "r": True,
        "cx": True,
        "cy": True,
        "segments": True,
    }


def test_explicit_kwargs_start_with_override_off_for_those_args():
    store = ParamStore()

    def callsite() -> None:
        G.circle(cx=1.0)

    with parameter_context(store=store, cc_snapshot=None):
        callsite()

    assert _override_by_arg(store, op="circle") == {
        "r": True,
        "cx": False,
        "cy": True,
        "segments": True,
    }


def test_existing_state_is_not_overwritten_by_policy():
    store = ParamStore()

    def callsite() -> None:
        G.circle()

    with parameter_context(store=store, cc_snapshot=None):
        callsite()

    snap = store.snapshot()
    key_r = next(key for key in snap.keys() if key.op == "circle" and key.arg == "r")
    state_r = store.get_state(key_r)
    assert state_r is not None
    state_r.override = False

    with parameter_context(store=store, cc_snapshot=None):
        callsite()

    snap2 = store.snapshot()
    _meta, state2, _ordinal, _label = snap2[key_r]
    assert state2.override is False
