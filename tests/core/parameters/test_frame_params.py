from grafix.core.parameters import ParamMeta, ParamStore, parameter_context, resolve_params
from grafix.core.parameters.invariants import assert_invariants
from grafix.core.parameters.snapshot_ops import store_snapshot


def test_merge_creates_state_and_ordinal():
    store = ParamStore()
    meta = {"r": ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)}
    params = {"r": 0.5}

    with parameter_context(store=store, cc_snapshot=None):
        resolve_params(op="circle", params=params, meta=meta, site_id="site-a")

    # after context exit, frame_params merged
    snap = store_snapshot(store)
    assert len(snap) == 1
    (_key, (_meta, state, ordinal, _label)) = next(iter(snap.items()))
    assert state.ui_value == 0.5
    assert ordinal == 1
    assert_invariants(store)
