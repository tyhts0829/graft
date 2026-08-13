"""
Purpose:
    mp-draw親側のACK、latest-wins、epoch/generation判定を純粋なstate遷移として保持する。
Use when:
    Queue混雑時の置換、snapshot伝播、stale result、last-good選択を検討するとき。
Constraints:
    - process、Queue、thread、clock、closeなどのI/O・resource lifecycleを持ち込まない。
    - epochまたはgenerationが古い結果を現在の表示候補へ昇格させない。
    - latest receivedとlatest successfulを分離し、後続失敗でlast-goodを失わない。
    - pending task/updateはboundedなlatest slotとして扱い、無制限履歴にしない。
"""

from __future__ import annotations

from grafix.interactive.runtime._mp_draw_protocol import (
    DrawResult,
    _DrawTask,
    _SnapshotAck,
    _SnapshotUpdate,
    _TaskRejected,
)


class _MpDrawState:
    """ACK、latest-wins、stale 判定だけを持つ副作用なしの親側 state。

    process、Queue、thread、clock、close は所有しない。I/O の結果を明示的な
    transition method へ渡し、次に Queue へ書く値は query として返す。
    """

    def __init__(self) -> None:
        self.current_epoch = 0
        self.latest_received: DrawResult | None = None
        self.latest_successful: DrawResult | None = None
        self.completed_result_count = 0
        self.stale_result_count = 0
        self.last_stale_result: tuple[int, int, int] | None = None
        self.stale_generation_result_count = 0
        self.last_stale_generation_result: tuple[int, int, int] | None = None
        self.snapshot_ack_count = 0
        self.last_snapshot_ack: _SnapshotAck | None = None
        self.task_enqueue_count = 0
        self.task_drop_count = 0
        self.rejected_task_count = 0
        self.last_rejection: _TaskRejected | None = None
        self.last_published_frame_id = 0
        self._ready_worker_pids: set[int] = set()
        self._worker_snapshot_revisions: dict[int, int] = {}
        self._control_index_by_pid: dict[int, int] = {}
        self._pending_snapshot_updates: dict[int, _SnapshotUpdate] = {}
        self._queued_snapshot_revisions: dict[int, int] = {}
        self._pending_task: _DrawTask | None = None

    def reset_generation(self) -> None:
        """worker 世代にだけ属する ACK/task state を空にする。"""

        self._ready_worker_pids.clear()
        self._worker_snapshot_revisions.clear()
        self.clear_queue_state()

    def clear_queue_state(self) -> None:
        """Queue endpoint と同じ寿命の親側 slot を空にする。"""

        self._control_index_by_pid.clear()
        self._pending_snapshot_updates.clear()
        self._queued_snapshot_revisions.clear()
        self._pending_task = None

    def register_worker(self, *, pid: int, control_index: int) -> None:
        """起動済み worker と専用 control queue の対応を記録する。"""

        self._control_index_by_pid[pid] = control_index

    def mark_worker_ready(self, pid: int) -> None:
        """ready message を適用する。"""

        self._ready_worker_pids.add(pid)

    @property
    def ready_worker_pids(self) -> frozenset[int]:
        return frozenset(self._ready_worker_pids)

    def apply_snapshot_ack(self, ack: _SnapshotAck) -> None:
        """ACK を適用し、既知 revision と pending latest を前進させる。"""

        pid = ack.pid
        applied = ack.applied_revision
        previous = self._worker_snapshot_revisions.get(pid)
        if previous is None or applied > previous:
            self._worker_snapshot_revisions[pid] = applied
        self.snapshot_ack_count += 1
        self.last_snapshot_ack = ack

        control_index = self._control_index_by_pid.get(pid)
        if control_index is None:
            return
        queued_revision = self._queued_snapshot_revisions.get(control_index)
        if queued_revision == ack.requested_revision:
            self._queued_snapshot_revisions.pop(control_index, None)
        pending = self._pending_snapshot_updates.get(control_index)
        if pending is not None and pending.revision <= applied:
            self._pending_snapshot_updates.pop(control_index, None)

    def queue_snapshot_update(
        self,
        update: _SnapshotUpdate,
        *,
        worker_count: int,
    ) -> None:
        """worker ごとの親側 pending slot を同じ latest update へ置換する。"""

        for index in range(worker_count):
            self._pending_snapshot_updates[index] = update

    def snapshot_updates_ready_to_send(self) -> tuple[tuple[int, _SnapshotUpdate], ...]:
        """control queue が空と判明している pending update を返す。"""

        return tuple(
            (index, update)
            for index, update in self._pending_snapshot_updates.items()
            if index not in self._queued_snapshot_revisions
        )

    def mark_snapshot_update_queued(self, *, index: int, revision: int) -> None:
        """指定 worker の control queue が ACK 待ちになったことを記録する。"""

        self._queued_snapshot_revisions[index] = revision

    def workers_have_revision(self, revision: int) -> bool:
        """ready worker 全員が同じ revision を ACK 済みなら True。"""

        expected = self._ready_worker_pids
        return bool(expected) and all(
            self._worker_snapshot_revisions.get(pid) == revision for pid in expected
        )

    @property
    def worker_snapshot_revisions(self) -> dict[int, int]:
        return dict(self._worker_snapshot_revisions)

    @property
    def pending_snapshot_update_count(self) -> int:
        return len(self._pending_snapshot_updates)

    @property
    def queued_snapshot_update_count(self) -> int:
        return len(self._queued_snapshot_revisions)

    def replace_pending_task(self, task: _DrawTask) -> None:
        """親側 latest task slot を置換し、旧 slot があれば drop と数える。"""

        if self._pending_task is not None:
            self.task_drop_count += 1
        self._pending_task = task

    @property
    def pending_task(self) -> _DrawTask | None:
        return self._pending_task

    def mark_pending_task_enqueued(self, task: _DrawTask) -> None:
        """現在の latest task が enqueue 済みなら slot を空にする。"""

        if self._pending_task is task:
            self._pending_task = None
        self.task_enqueue_count += 1

    def record_queue_drop(self) -> None:
        self.task_drop_count += 1

    def record_rejection(self, rejection: _TaskRejected) -> None:
        self.rejected_task_count += 1
        self.last_rejection = rejection

    def begin_epoch(self, requested: int) -> bool:
        """epoch を前進させ、旧 timeline の表示候補と pending task を捨てる。"""

        if requested < self.current_epoch:
            raise ValueError(
                "epoch は現在値以上である必要があります: "
                f"current={self.current_epoch}, got={requested}"
            )
        if requested == self.current_epoch:
            return False
        self.current_epoch = requested
        self.latest_received = None
        self.latest_successful = None
        self._pending_task = None
        return True

    def reset_received_for_generation(self) -> None:
        """restart 後に旧世代の受信候補だけを外す。"""

        self.latest_received = None

    def record_stale_generation(
        self,
        result: DrawResult,
        *,
        current_generation: int,
    ) -> None:
        self.stale_generation_result_count += 1
        self.last_stale_generation_result = (
            result.frame_id,
            result.generation,
            current_generation,
        )

    def accept_result(self, result: DrawResult) -> bool:
        """current epoch の結果を採用し、stale なら False を返す。"""

        self.completed_result_count += 1
        if result.epoch != self.current_epoch:
            self.stale_result_count += 1
            self.last_stale_result = (
                result.frame_id,
                result.epoch,
                self.current_epoch,
            )
            return False
        if self.latest_received is None or result.frame_id > self.latest_received.frame_id:
            self.latest_received = result
        if result.error is None and (
            self.latest_successful is None
            or result.frame_id > self.latest_successful.frame_id
        ):
            self.latest_successful = result
        return True

    def publish_latest(self) -> DrawResult | None:
        """未公開の最新結果だけを一度返す。"""

        latest = self.latest_received
        if latest is None or latest.frame_id <= self.last_published_frame_id:
            return None
        self.last_published_frame_id = latest.frame_id
        return latest

__all__: list[str] = []
