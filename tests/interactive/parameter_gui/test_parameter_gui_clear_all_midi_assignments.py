from __future__ import annotations

from grafix.core.parameters import ParamMeta, ParamStore, ParameterKey
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.interactive.parameter_gui.table_commit import clear_all_midi_assignments
from tests.param_store_test_support import mutate_runtime


def test_clear_all_midi_assignments_bakes_effective_and_clears_cc_key() -> None:
    store = ParamStore()

    key_r = ParameterKey(op="circle", site_id="s1", arg="r")
    meta_r = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)

    key_p = ParameterKey(op="scale", site_id="sv1", arg="p")
    meta_p = ParamMeta(kind="vec3", ui_min=-1.0, ui_max=1.0)

    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key_r,
                base=0.0,
                meta=meta_r,
                effective=0.0,
                source="code",
                explicit=True,
            ),
            FrameParamRecord(
                key=key_p,
                base=(0.0, 0.0, 0.0),
                meta=meta_p,
                effective=(0.0, 0.0, 0.0),
                source="code",
                explicit=True,
            ),
        ],
    )

    stored_meta_r = store.get_meta(key_r)
    stored_meta_p = store.get_meta(key_p)
    assert stored_meta_r is not None
    assert stored_meta_p is not None

    update_state_from_ui(store, key_r, 0.1, meta=stored_meta_r, override=False, cc_key=12)
    update_state_from_ui(
        store,
        key_p,
        (0.0, 0.0, 0.0),
        meta=stored_meta_p,
        override=False,
        cc_key=(10, 11, 12),
    )
    def seed_effective(runtime: object) -> None:
        runtime.last_effective_by_key[key_r] = 0.75  # type: ignore[attr-defined]
        runtime.last_effective_by_key[key_p] = (  # type: ignore[attr-defined]
            -1.0,
            0.25,
            1.0,
        )

    mutate_runtime(store, seed_effective)

    changed = clear_all_midi_assignments(store)
    assert changed is True

    state_r = store.get_state(key_r)
    assert state_r is not None
    assert state_r.cc_key is None
    assert state_r.override is True
    assert state_r.ui_value == 0.75

    state_p = store.get_state(key_p)
    assert state_p is not None
    assert state_p.cc_key is None
    assert state_p.override is True
    assert state_p.ui_value == (-1.0, 0.25, 1.0)


def test_clear_all_midi_assignments_returns_false_when_no_assignments() -> None:
    store = ParamStore()

    key = ParameterKey(op="circle", site_id="s1", arg="r")
    meta_r = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.0,
                meta=meta_r,
                effective=0.0,
                source="code",
                explicit=True,
            ),
        ],
    )

    assert clear_all_midi_assignments(store) is False

    state = store.get_state(key)
    assert state is not None
    assert state.cc_key is None
