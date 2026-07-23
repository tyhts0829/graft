from __future__ import annotations

import pytest

from grafix.core.parameters import context as context_module
from grafix.core.parameters.context import (
    current_cc_snapshot,
    current_frame_params,
    current_param_snapshot,
    current_param_store,
    parameter_context,
)
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.source import MidiFrameSnapshot
from grafix.core.parameters.snapshot_ops import store_snapshot
from grafix.core.parameters.store import ParamStore
from tests.param_store_test_support import runtime_state


def _assert_current_context(
    *,
    store: ParamStore | None,
    frame_params: object | None,
    cc_snapshot: MidiFrameSnapshot | None,
    param_snapshot: object,
) -> None:
    assert current_param_store() is store
    assert current_frame_params() is frame_params
    assert current_cc_snapshot() is cc_snapshot
    assert current_param_snapshot() == param_snapshot


@pytest.mark.parametrize("failing_merge", ["merge_frame_labels", "merge_frame_params"])
def test_parameter_context_restores_outer_context_when_merge_fails(
    monkeypatch: pytest.MonkeyPatch,
    failing_merge: str,
) -> None:
    outer_store = ParamStore()
    inner_store = ParamStore()
    outer_cc = MidiFrameSnapshot.from_mapping({1: 0.25}, source="midi_live")
    inner_cc = MidiFrameSnapshot.from_mapping({2: 0.5}, source="midi_frozen")

    with parameter_context(outer_store, outer_cc):
        outer_frame = current_frame_params()
        outer_snapshot = current_param_snapshot()

        def fail(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("merge failed")

        with monkeypatch.context() as patch:
            patch.setattr(context_module, failing_merge, fail)
            with pytest.raises(RuntimeError, match="merge failed"):
                with parameter_context(inner_store, inner_cc):
                    assert current_param_store() is inner_store

        _assert_current_context(
            store=outer_store,
            frame_params=outer_frame,
            cc_snapshot=outer_cc,
            param_snapshot=outer_snapshot,
        )

    _assert_current_context(
        store=None,
        frame_params=None,
        cc_snapshot=None,
        param_snapshot={},
    )


def test_parameter_context_restores_state_when_body_raises() -> None:
    store = ParamStore()
    cc_snapshot = MidiFrameSnapshot.from_mapping({7: 0.75}, source="midi_live")

    with pytest.raises(ValueError, match="draw failed"):
        with parameter_context(store, cc_snapshot):
            assert current_param_store() is store
            assert current_cc_snapshot() is cc_snapshot
            raise ValueError("draw failed")

    _assert_current_context(
        store=None,
        frame_params=None,
        cc_snapshot=None,
        param_snapshot={},
    )


def test_parameter_context_rolls_back_frame_observations_when_body_raises() -> None:
    store = ParamStore()
    key = ParameterKey(op="temporary", site_id="failed-frame", arg="amount")

    with pytest.raises(ValueError, match="draw failed"):
        with parameter_context(store):
            frame_params = current_frame_params()
            assert frame_params is not None
            frame_params.record(
                key=key,
                base=0.5,
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
                effective=0.5,
                source="code",
                explicit=False,
            )
            frame_params.set_label(
                op=key.op,
                site_id=key.site_id,
                label="failed label",
            )
            raise ValueError("draw failed")

    assert key not in store_snapshot(store)
    assert store.get_label(key.op, key.site_id) is None
    assert (key.op, key.site_id) not in runtime_state(store).observed_groups
