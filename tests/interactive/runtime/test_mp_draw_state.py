"""MpDraw の副作用を持たない ACK/latest/stale transition を検証する。"""

from __future__ import annotations

from grafix.interactive.runtime.mp_draw import (
    DrawResult,
    _DrawTask,
    _MpDrawState,
    _SnapshotAck,
    _SnapshotUpdate,
)


def _ack(*, pid: int, requested: int, applied: int) -> _SnapshotAck:
    return _SnapshotAck(
        worker=f"worker-{pid}",
        pid=pid,
        requested_revision=requested,
        applied_revision=applied,
        status="applied",
        generation=0,
    )


def _result(
    frame_id: int,
    *,
    epoch: int = 0,
    generation: int = 0,
    error: str | None = None,
) -> DrawResult:
    return DrawResult(
        frame_id=frame_id,
        t=float(frame_id),
        epoch=epoch,
        generation=generation,
        snapshot_revision=0,
        layers=(),
        records=(),
        labels=(),
        effect_chains=(),
        error=error,
    )


def _task(frame_id: int) -> _DrawTask:
    return _DrawTask(
        frame_id=frame_id,
        t=float(frame_id),
        snapshot_revision=0,
        cc_snapshot=None,
        snapshot={},
        effect_order_snapshot={},
        epoch=0,
        generation=0,
        quality="draft",
    )


def test_snapshot_ack_transition_tracks_known_and_pending_revisions() -> None:
    state = _MpDrawState()
    update = _SnapshotUpdate(
        revision=7,
        snapshot={},
        effect_order_snapshot={},
        generation=0,
    )
    for index, pid in enumerate((101, 202)):
        state.register_worker(pid=pid, control_index=index)
        state.mark_worker_ready(pid)
    state.queue_snapshot_update(update, worker_count=2)

    ready = state.snapshot_updates_ready_to_send()
    assert ready == ((0, update), (1, update))
    for index, item in ready:
        state.mark_snapshot_update_queued(index=index, revision=item.revision)

    state.apply_snapshot_ack(_ack(pid=101, requested=7, applied=7))
    assert state.worker_snapshot_revisions == {101: 7}
    assert state.pending_snapshot_update_count == 1
    assert state.queued_snapshot_update_count == 1
    assert state.workers_have_revision(7) is False

    state.apply_snapshot_ack(_ack(pid=202, requested=7, applied=7))
    assert state.worker_snapshot_revisions == {101: 7, 202: 7}
    assert state.pending_snapshot_update_count == 0
    assert state.queued_snapshot_update_count == 0
    assert state.snapshot_ack_count == 2
    assert state.workers_have_revision(7) is True


def test_stale_ack_does_not_regress_known_revision_or_drop_newer_pending() -> None:
    state = _MpDrawState()
    state.register_worker(pid=101, control_index=0)
    state.mark_worker_ready(101)
    state.apply_snapshot_ack(_ack(pid=101, requested=9, applied=9))
    update = _SnapshotUpdate(
        revision=11,
        snapshot={},
        effect_order_snapshot={},
        generation=0,
    )
    state.queue_snapshot_update(update, worker_count=1)

    state.apply_snapshot_ack(_ack(pid=101, requested=7, applied=7))

    assert state.worker_snapshot_revisions == {101: 9}
    assert state.pending_snapshot_update_count == 1
    assert state.workers_have_revision(7) is False


def test_latest_task_slot_replaces_old_task_without_owning_a_queue() -> None:
    state = _MpDrawState()
    first = _task(1)
    latest = _task(2)

    state.replace_pending_task(first)
    state.replace_pending_task(latest)

    assert state.pending_task is latest
    assert state.task_drop_count == 1
    state.mark_pending_task_enqueued(latest)
    assert state.pending_task is None
    assert state.task_enqueue_count == 1
    assert not hasattr(state, "close")
    assert not hasattr(state, "queue")
    assert not hasattr(state, "process")
    assert not hasattr(state, "thread")


def test_result_transition_keeps_success_fallback_and_publishes_latest_once() -> None:
    state = _MpDrawState()
    success = _result(1)
    failure = _result(2, error="draw failed")

    assert state.accept_result(success) is True
    assert state.accept_result(failure) is True
    assert state.latest_received is failure
    assert state.latest_successful is success
    assert state.publish_latest() is failure
    assert state.publish_latest() is None
    assert state.completed_result_count == 2


def test_epoch_and_generation_transitions_reject_stale_results() -> None:
    state = _MpDrawState()
    state.accept_result(_result(1))
    state.replace_pending_task(_task(2))

    assert state.begin_epoch(1) is True
    assert state.current_epoch == 1
    assert state.latest_received is None
    assert state.latest_successful is None
    assert state.pending_task is None

    stale_epoch = _result(3, epoch=0)
    assert state.accept_result(stale_epoch) is False
    assert state.last_stale_result == (3, 0, 1)

    stale_generation = _result(4, epoch=1, generation=8)
    state.record_stale_generation(stale_generation, current_generation=9)
    assert state.last_stale_generation_result == (4, 8, 9)
    assert state.stale_generation_result_count == 1
