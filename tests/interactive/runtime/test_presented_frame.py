from __future__ import annotations

from typing import Any, cast

import pytest

from grafix.core.capture_provenance import CaptureProvenance
from grafix.core.parameters import ParameterCaptureState, ParamStore
from grafix.core.pipeline import RealizedLayer
from grafix.runtime_config_loader import runtime_config
from grafix.export.capture_provenance import CaptureProvenanceBuilder
from grafix.interactive.runtime.presented_frame import (
    FramePublication,
    PresentedFrame,
    PresentedFrameState,
)
from tests.param_store_test_support import publish_structure_change_for_test


def _draw(_t: float) -> list[object]:
    return []


class _ProvenanceBuilderSpy:
    def __init__(self, store: ParamStore, *, source: bytes = b"presented-frame") -> None:
        draw = _draw
        setattr(draw, "__grafix_source_bytes__", source)
        self._builder = CaptureProvenanceBuilder(
            draw,
            config=runtime_config(),
            parameter_state=ParameterCaptureState("code", "primary"),
            parameter_store_path=None,
        )
        self.calls: list[dict[str, object]] = []

    def frame(self, store: ParamStore, **kwargs: object) -> CaptureProvenance:
        self.calls.append(dict(kwargs))
        return self._builder.frame(store, **cast(Any, kwargs))


def _state() -> tuple[PresentedFrameState, ParamStore, _ProvenanceBuilderSpy]:
    store = ParamStore()
    builder = _ProvenanceBuilderSpy(store)
    return (
        PresentedFrameState(
            store=store,
            provenance_builder=cast(Any, builder),
        ),
        store,
        builder,
    )


def _publish(
    state: PresentedFrameState,
    *,
    fresh: bool,
    revision: int | None,
    capture_pending: bool = False,
    recording_needs_provenance: bool = False,
) -> tuple[PresentedFrame, FramePublication]:
    frame = state.prepare(
        fresh=fresh,
        snapshot_revision=revision,
        frame_id=17 if fresh else None,
    )
    publication = state.publish(
        frame,
        quality="final" if capture_pending or recording_needs_provenance else "draft",
        canvas_size=(100, 80),
        background_color_rgb01=(1.0, 1.0, 1.0),
        gcode_params=runtime_config().gcode,
        capture_pending=capture_pending,
        recording_needs_provenance=recording_needs_provenance,
    )
    return frame, publication


def test_presentation_transition_matrix_keeps_one_frame_lifetime() -> None:
    state, _store, builder = _state()

    initial, initial_publication = _publish(
        state,
        fresh=False,
        revision=None,
    )
    assert initial.snapshot_revision is None
    assert initial.scene_serial == 0
    assert initial_publication.provenance is None
    assert state.export_snapshot is None

    state.accept_evaluation([], realized_t=1.25)
    fresh, _publication = _publish(state, fresh=True, revision=3)
    snapshot = state.export_snapshot
    token = state.provenance_token
    assert fresh.t == pytest.approx(1.25)
    assert fresh.scene_serial == 1
    assert state.snapshot_revision == 3
    assert state.frame_id == 17
    assert snapshot is not None and snapshot.t == pytest.approx(1.25)
    assert token is not None and token.frame_index == 0
    assert builder.calls == []

    held, held_publication = _publish(state, fresh=False, revision=None)
    assert held.snapshot_revision == 3
    assert held.scene_serial == 1
    assert held_publication.provenance is None
    assert state.frame_id is None
    assert state.export_snapshot is snapshot
    assert state.provenance_token is token

    state.accept_evaluation([], realized_t=2.5)
    second, _publication = _publish(state, fresh=True, revision=4)
    assert second.scene_serial == 2
    assert state.provenance_frame_index == 2
    assert state.export_snapshot is not snapshot


def test_fresh_presentation_requires_snapshot_revision() -> None:
    state, _store, _builder = _state()

    with pytest.raises(RuntimeError, match="snapshot revision"):
        state.prepare(fresh=True, snapshot_revision=None, frame_id=1)


@pytest.mark.parametrize(
    ("capture_pending", "recording_needs_provenance", "has_capture_snapshot"),
    [
        (False, False, False),
        (False, True, False),
        (True, False, True),
        (True, True, True),
    ],
)
def test_fresh_publication_materializes_only_required_provenance(
    capture_pending: bool,
    recording_needs_provenance: bool,
    has_capture_snapshot: bool,
) -> None:
    state, _store, builder = _state()
    state.accept_evaluation([], realized_t=3.0)

    _frame, publication = _publish(
        state,
        fresh=True,
        revision=0,
        capture_pending=capture_pending,
        recording_needs_provenance=recording_needs_provenance,
    )

    expected_materializations = int(capture_pending or recording_needs_provenance)
    assert len(builder.calls) == expected_materializations
    assert (publication.provenance is not None) is bool(expected_materializations)
    assert (publication.capture_snapshot is not None) is has_capture_snapshot
    assert state.export_snapshot is not None
    assert state.export_snapshot.provenance is None


def test_stale_preview_token_requires_final_reevaluation_at_shutdown() -> None:
    state, store, builder = _state()
    state.accept_evaluation([], realized_t=4.0)
    _publish(state, fresh=True, revision=0)
    snapshot = state.export_snapshot
    assert snapshot is not None
    publish_structure_change_for_test(store)

    with pytest.raises(RuntimeError, match="parameters changed"):
        state.materialize_capture_snapshot(snapshot)
    assert (
        state.capture_snapshot_for_shutdown(
            canvas_size=(100, 80),
            background_color_rgb01=(1.0, 1.0, 1.0),
            gcode_params=runtime_config().gcode,
        )
        is None
    )
    assert builder.calls == []


def test_visible_snapshot_keeps_its_source_generation_after_reload() -> None:
    state, store, old_builder = _state()
    state.accept_evaluation([], realized_t=5.0)
    _publish(state, fresh=True, revision=0)
    snapshot = state.export_snapshot
    assert snapshot is not None
    new_builder = _ProvenanceBuilderSpy(store, source=b"new-generation")

    state.replace_provenance_builder(cast(Any, new_builder))
    capture = state.materialize_capture_snapshot(snapshot)

    assert capture.provenance.frame.frame_index == 0
    assert len(old_builder.calls) == 1
    assert new_builder.calls == []


def test_failed_evaluation_can_reuse_last_accepted_layers_and_time() -> None:
    state, _store, _builder = _state()
    layer = cast(RealizedLayer, object())
    state.accept_evaluation([layer], realized_t=6.0)

    frame = state.prepare(fresh=False, snapshot_revision=9, frame_id=None)

    assert frame.layers == (layer,)
    assert frame.t == pytest.approx(6.0)
    assert frame.scene_serial == 0
