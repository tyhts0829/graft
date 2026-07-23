"""MpDraw の高頻度 submit、snapshot ACK、revision churn の process stress 契約。"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pytest

from grafix.core.geometry import Geometry
from grafix.interactive.runtime._mp_draw_protocol import _DrawTask, _SnapshotUpdate
from grafix.interactive.runtime.mp_draw import DrawResult, MpDraw
from grafix.runtime_config_loader import runtime_config

pytestmark = pytest.mark.integration


_WAIT_TIMEOUT_S = 8.0


_EFFECTIVE_CONFIG = runtime_config()


def _mp_draw(draw: Any, **kwargs: Any) -> MpDraw:
    return MpDraw(draw, effective_config=_EFFECTIVE_CONFIG, **kwargs)


def _empty_draw(_t: float) -> Geometry:
    return Geometry.create(op="concat")


def _wait_for_result(mp_draw: MpDraw) -> DrawResult:
    deadline = time.monotonic() + _WAIT_TIMEOUT_S
    while time.monotonic() < deadline:
        result = mp_draw.poll_latest()
        if result is not None:
            return result
        time.sleep(0.01)
    pytest.fail("mp-draw result timeout")


def _wait_until(predicate: Callable[[], bool], *, message: str) -> None:
    deadline = time.monotonic() + _WAIT_TIMEOUT_S
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail(message)


def test_600_stable_frames_broadcast_snapshot_only_once() -> None:
    mp_draw = _mp_draw(_empty_draw, n_worker=2)
    try:
        for frame in range(600):
            mp_draw.submit(
                t=float(frame),
                snapshot_revision=7,
                snapshot={},
                effect_order_snapshot={},
                epoch=0,
                quality="draft",
            )

        result = _wait_for_result(mp_draw)
        assert result.error is None
        assert mp_draw.stats.completed_result_count >= 1
        assert mp_draw.stats.snapshot_broadcast_count == 1
        assert set(dict(mp_draw.stats.worker_snapshot_revisions).values()) == {7}
        assert mp_draw.stats.snapshot_ack_count >= 2

        for frame in range(600, 660):
            mp_draw.submit(
                t=float(frame),
                snapshot_revision=7,
                snapshot={},
                effect_order_snapshot={},
                epoch=0,
                quality="draft",
            )
        assert mp_draw.stats.snapshot_broadcast_count == 1

        mp_draw.submit(
            t=661.0,
            snapshot_revision=8,
            snapshot={},
            effect_order_snapshot={},
            epoch=0,
            quality="draft",
        )

        def revision_8_was_acked() -> bool:
            mp_draw.poll_latest()
            return set(dict(mp_draw.stats.worker_snapshot_revisions).values()) == {8}

        _wait_until(
            revision_8_was_acked,
            message="snapshot revision 8 ack timeout",
        )
        assert mp_draw.stats.snapshot_broadcast_count == 2
    finally:
        mp_draw.close()


def test_single_worker_uses_task_snapshot_without_duplicate_control_broadcast() -> None:
    mp_draw = _mp_draw(_empty_draw, n_worker=1)
    try:
        for frame in range(30):
            mp_draw.submit(
                t=float(frame),
                snapshot_revision=7,
                snapshot={},
                effect_order_snapshot={},
                epoch=0,
                quality="draft",
            )

        result = _wait_for_result(mp_draw)
        assert result.error is None
        assert result.snapshot_revision == 7
        assert mp_draw.stats.snapshot_broadcast_count == 0
        assert mp_draw.stats.snapshot_payload_copy_count == 1
        assert set(dict(mp_draw.stats.worker_snapshot_revisions).values()) == {7}

        mp_draw.submit(
            t=31.0,
            snapshot_revision=8,
            snapshot={},
            effect_order_snapshot={},
            epoch=0,
            quality="draft",
        )
        latest: DrawResult | None = None
        deadline = time.monotonic() + _WAIT_TIMEOUT_S
        while time.monotonic() < deadline:
            candidate = mp_draw.poll_latest()
            if candidate is not None and candidate.snapshot_revision == 8:
                latest = candidate
                break
            time.sleep(0.005)
        assert latest is not None
        assert latest.snapshot_revision == 8
        assert mp_draw.stats.snapshot_broadcast_count == 0
        assert mp_draw.stats.snapshot_payload_copy_count == 2
    finally:
        mp_draw.close()


def test_mp_draw_emits_revision_and_frame_causal_events() -> None:
    events: list[tuple[str, int | None, int | None]] = []

    def record_event(
        name: str,
        *,
        frame_id: int | None = None,
        revision: int | None = None,
    ) -> None:
        events.append((name, frame_id, revision))

    mp_draw = _mp_draw(
        _empty_draw,
        n_worker=1,
        event_callback=record_event,
    )
    try:
        mp_draw.submit(
            t=0.0,
            snapshot_revision=9,
            snapshot={},
            effect_order_snapshot={},
            epoch=0,
            quality="draft",
        )
        submitted_frame_id = mp_draw.stats.last_submitted_frame_id
        result = _wait_for_result(mp_draw)

        assert result.frame_id == submitted_frame_id
        assert (
            "parameter_snapshot_built",
            None,
            9,
        ) in events
        assert (
            "mp_snapshot_sent",
            submitted_frame_id,
            9,
        ) in events
        assert ("mp_snapshot_applied", None, 9) in events
        assert (
            "mp_task_started",
            submitted_frame_id,
            9,
        ) in events
    finally:
        mp_draw.close()


def test_worker_rejects_unknown_and_stale_revision_and_acks_stale_update() -> None:
    mp_draw = _mp_draw(_empty_draw, n_worker=2)
    try:
        mp_draw.submit(
            t=0.0,
            snapshot_revision=5,
            snapshot={},
            effect_order_snapshot={},
            epoch=0,
            quality="draft",
        )
        _wait_for_result(mp_draw)
        assert set(dict(mp_draw.stats.worker_snapshot_revisions).values()) == {5}

        rejected_before = mp_draw.stats.rejected_task_count
        mp_draw._task_q.put(
            _DrawTask(
                frame_id=10_001,
                t=0.0,
                snapshot_revision=6,
                cc_snapshot=None,
                snapshot=None,
                effect_order_snapshot=None,
                epoch=0,
                generation=0,
                quality="draft",
            )
        )

        def unknown_was_rejected() -> bool:
            mp_draw.poll_latest()
            return mp_draw.stats.rejected_task_count > rejected_before

        _wait_until(unknown_was_rejected, message="unknown revision rejection timeout")
        assert mp_draw.stats.last_rejection == (6, 5, "unknown")

        rejected_before = mp_draw.stats.rejected_task_count
        mp_draw._task_q.put(
            _DrawTask(
                frame_id=10_002,
                t=0.0,
                snapshot_revision=4,
                cc_snapshot=None,
                snapshot=None,
                effect_order_snapshot=None,
                epoch=0,
                generation=0,
                quality="draft",
            )
        )

        def stale_was_rejected() -> bool:
            mp_draw.poll_latest()
            return mp_draw.stats.rejected_task_count > rejected_before

        _wait_until(stale_was_rejected, message="stale revision rejection timeout")
        assert mp_draw.stats.last_rejection == (4, 5, "stale")

        ack_before = mp_draw.stats.snapshot_ack_count
        mp_draw._control_qs[0].put(
            _SnapshotUpdate(
                revision=4,
                snapshot={},
                effect_order_snapshot={},
                generation=mp_draw.generation,
            )
        )

        def stale_was_acked() -> bool:
            mp_draw.poll_latest()
            return mp_draw.stats.snapshot_ack_count > ack_before

        _wait_until(stale_was_acked, message="stale snapshot ack timeout")
        assert mp_draw.stats.last_snapshot_ack == (4, 5, "stale")
    finally:
        mp_draw.close()


def test_rapid_revision_changes_keep_snapshot_control_backlog_bounded() -> None:
    mp_draw = _mp_draw(_empty_draw, n_worker=2)
    try:
        for revision in range(1, 201):
            mp_draw.submit(
                t=float(revision),
                snapshot_revision=revision,
                snapshot={},
                effect_order_snapshot={},
                epoch=0,
                quality="draft",
            )
            assert mp_draw.stats.pending_snapshot_update_count <= 2
            assert mp_draw.stats.queued_snapshot_update_count <= 2

        def final_revision_was_acked() -> bool:
            mp_draw.poll_latest()
            return set(dict(mp_draw.stats.worker_snapshot_revisions).values()) == {200}

        _wait_until(
            final_revision_was_acked,
            message="latest snapshot revision ack timeout",
        )
        assert mp_draw.stats.snapshot_broadcast_count == 200
        assert mp_draw.stats.pending_snapshot_update_count == 0
        assert mp_draw.stats.queued_snapshot_update_count == 0
        assert mp_draw.stats.rejected_task_count == 0
    finally:
        mp_draw.close()


@pytest.mark.parametrize("n_worker", [1, 2])
def test_revision_churn_keeps_results_moving_and_reaches_latest(
    n_worker: int,
) -> None:
    """GUI と同じ submit→poll 順でも revision ACK が draw を飢餓させない。"""

    mp_draw = _mp_draw(_empty_draw, n_worker=n_worker)
    revisions_during_drag: list[int] = []
    try:
        for revision in range(1, 61):
            mp_draw.submit(
                t=float(revision),
                snapshot_revision=revision,
                snapshot={},
                effect_order_snapshot={},
                epoch=0,
                quality="draft",
            )
            result = mp_draw.poll_latest()
            if result is not None:
                revisions_during_drag.append(int(result.snapshot_revision))
            assert mp_draw.stats.pending_snapshot_update_count <= n_worker
            assert mp_draw.stats.queued_snapshot_update_count <= n_worker
            time.sleep(0.005)

        # wall time そのものではなく、連続 edit 中にも評価が前進することを契約にする。
        assert len(revisions_during_drag) >= 2
        assert revisions_during_drag == sorted(set(revisions_during_drag))

        final_result: DrawResult | None = None
        deadline = time.monotonic() + _WAIT_TIMEOUT_S
        while time.monotonic() < deadline:
            mp_draw.submit(
                t=60.0,
                snapshot_revision=60,
                snapshot={},
                effect_order_snapshot={},
                epoch=0,
                quality="draft",
            )
            result = mp_draw.poll_latest()
            if result is not None and int(result.snapshot_revision) == 60:
                final_result = result
                break
            time.sleep(0.005)

        assert final_result is not None
        assert final_result.error is None
        assert mp_draw.stats.rejected_task_count == 0
    finally:
        mp_draw.close()
