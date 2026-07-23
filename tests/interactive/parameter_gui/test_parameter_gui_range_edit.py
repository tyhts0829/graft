from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.history import ParamStoreHistory
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.store import ParamStore
from grafix.interactive.parameter_gui.range_edit import (
    apply_range_edit_session,
    apply_range_shift,
    preview_range_edit,
    range_edit_session_for_store,
)
from tests.param_store_test_support import replace_parameter_state_for_test


def _add_range_parameter(
    store: ParamStore,
    *,
    arg: str,
    cc_key: int | tuple[int | None, int | None, int | None] | None,
    kind: str = "float",
) -> ParameterKey:
    key = ParameterKey(op="wave", site_id="site", arg=arg)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.5,
                meta=ParamMeta(kind=kind, ui_min=0.0, ui_max=2.0),
                effective=0.5,
                source="code",
                explicit=False,
            )
        ],
    )
    replace_parameter_state_for_test(store, key, cc_key=cc_key)
    return key


def test_apply_range_shift_float_shift():
    ui_min, ui_max = apply_range_shift(
        kind="float",
        ui_min=0.0,
        ui_max=2.0,
        delta=0.25,
        mode="shift",
        sensitivity=1.0,
    )
    assert ui_min == 0.5
    assert ui_max == 2.5


def test_apply_range_shift_float_max_only():
    ui_min, ui_max = apply_range_shift(
        kind="float",
        ui_min=0.0,
        ui_max=2.0,
        delta=0.25,
        mode="max",
        sensitivity=1.0,
    )
    assert ui_min == 0.0
    assert ui_max == 2.5


def test_apply_range_shift_float_min_only():
    ui_min, ui_max = apply_range_shift(
        kind="float",
        ui_min=0.0,
        ui_max=2.0,
        delta=0.25,
        mode="min",
        sensitivity=1.0,
    )
    assert ui_min == 0.5
    assert ui_max == 2.0


def test_apply_range_shift_float_swaps_when_crossing():
    ui_min, ui_max = apply_range_shift(
        kind="float",
        ui_min=0.0,
        ui_max=1.0,
        delta=-2.0,
        mode="max",
        sensitivity=1.0,
    )
    assert ui_min == -1.0
    assert ui_max == 0.0


def test_apply_range_shift_int_uses_at_least_one_step():
    ui_min, ui_max = apply_range_shift(
        kind="int",
        ui_min=0,
        ui_max=10,
        delta=1.0 / 127.0,
        mode="shift",
        sensitivity=1.0,
    )
    assert ui_min == 1
    assert ui_max == 11


def test_apply_range_shift_int_swaps_when_crossing():
    ui_min, ui_max = apply_range_shift(
        kind="int",
        ui_min=0,
        ui_max=10,
        delta=-2.0,
        mode="max",
        sensitivity=1.0,
    )
    assert ui_min == -10
    assert ui_max == 0


def test_range_edit_preview_does_not_mutate_store_until_apply() -> None:
    store = ParamStore()
    key = _add_range_parameter(store, arg="amount", cc_key=7)
    session = range_edit_session_for_store(store, cc=7, mode="shift")
    assert session is not None

    preview = preview_range_edit(session, delta=0.25)
    assert preview.targets[0].pending_range == (0.5, 2.5)
    assert store.get_meta(key) == ParamMeta(kind="float", ui_min=0.0, ui_max=2.0)

    changed = apply_range_edit_session(store, preview)
    assert changed == (key,)
    assert store.get_meta(key) == ParamMeta(kind="float", ui_min=0.5, ui_max=2.5)


def test_range_edit_links_all_matching_targets_and_apply_is_one_undo_step() -> None:
    store = ParamStore()
    first = _add_range_parameter(store, arg="first", cc_key=11)
    second = _add_range_parameter(store, arg="second", cc_key=(3, 11, None))
    _add_range_parameter(store, arg="other", cc_key=12)
    history = ParamStoreHistory(store)

    session = range_edit_session_for_store(store, cc=11, mode="max")
    assert session is not None
    assert tuple(target.key for target in session.targets) == (first, second)
    preview = preview_range_edit(session, delta=0.5)
    assert apply_range_edit_session(store, preview, history=history) == (first, second)
    assert history.undo_depth == 1
    assert store.get_meta(first).ui_max == 3.0  # type: ignore[union-attr]
    assert store.get_meta(second).ui_max == 3.0  # type: ignore[union-attr]

    assert history.undo() is True
    assert store.get_meta(first).ui_max == 2.0  # type: ignore[union-attr]
    assert store.get_meta(second).ui_max == 2.0  # type: ignore[union-attr]


def test_range_edit_cancel_is_drop_only_and_no_matching_target_returns_none() -> None:
    store = ParamStore()
    key = _add_range_parameter(store, arg="amount", cc_key=7)
    session = range_edit_session_for_store(store, cc=7, mode="min")
    assert session is not None
    _preview = preview_range_edit(session, delta=0.5)

    # Cancel は preview を破棄するだけで、store revision/rangeを変更しない。
    assert store.get_meta(key).ui_min == 0.0  # type: ignore[union-attr]
    assert range_edit_session_for_store(store, cc=99, mode="shift") is None
