from graft.core.parameters import ParamMeta, ParamStore, parameter_context, resolve_params


def test_merge_creates_state_and_ordinal():
    store = ParamStore()
    meta = {"r": ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)}
    params = {"r": 0.5}

    with parameter_context(store=store, cc_snapshot=None):
        resolve_params(op="circle", params=params, meta=meta, site_id="site-a")

    # after context exit, frame_params merged
    keys = list(store._states.keys())  # type: ignore[attr-defined]
    assert len(keys) == 1
    state = store._states[keys[0]]  # type: ignore[attr-defined]
    assert state.ui_value == 0.5
    # ordinal should be assigned
    assert store.get_ordinal("circle", "site-a") == 1
