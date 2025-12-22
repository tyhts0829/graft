from grafix.api import G
from grafix.core.parameters import ParamStore, parameter_context
from grafix.core.parameters.invariants import assert_invariants
from grafix.core.parameters.snapshot_ops import store_snapshot
from grafix.core.parameters.ui_ops import update_state_from_ui


def _override_by_arg(store: ParamStore, *, op: str) -> dict[str, bool]:
    snap = store_snapshot(store)
    return {
        key.arg: bool(state.override)
        for key, (_meta, state, _ordinal, _label) in snap.items()
        if key.op == op
    }


def test_implicit_defaults_start_with_override_on():
    store = ParamStore()

    def callsite() -> None:
        G.polygon()

    with parameter_context(store=store, cc_snapshot=None):
        callsite()

    assert _override_by_arg(store, op="polygon") == {
        "n_sides": True,
        "phase": True,
        "center": True,
        "scale": True,
    }
    assert_invariants(store)


def test_explicit_kwargs_start_with_override_off_for_those_args():
    store = ParamStore()

    def callsite() -> None:
        G.polygon(phase=45.0)

    with parameter_context(store=store, cc_snapshot=None):
        callsite()

    assert _override_by_arg(store, op="polygon") == {
        "n_sides": True,
        "phase": False,
        "center": True,
        "scale": True,
    }
    assert_invariants(store)


def test_existing_state_is_not_overwritten_by_policy():
    store = ParamStore()

    def callsite() -> None:
        G.polygon()

    with parameter_context(store=store, cc_snapshot=None):
        callsite()

    snap = store_snapshot(store)
    key_n_sides = next(
        key for key in snap.keys() if key.op == "polygon" and key.arg == "n_sides"
    )
    meta, state, _ordinal, _label = snap[key_n_sides]
    update_state_from_ui(store, key_n_sides, state.ui_value, meta=meta, override=False)

    with parameter_context(store=store, cc_snapshot=None):
        callsite()

    snap2 = store_snapshot(store)
    _meta, state2, _ordinal, _label = snap2[key_n_sides]
    assert state2.override is False
    assert_invariants(store)
