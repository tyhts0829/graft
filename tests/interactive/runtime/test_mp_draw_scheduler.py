"""MpDraw scheduler の enqueue、epoch、result drain、cleanup 状態遷移。"""

from __future__ import annotations

import multiprocessing as mp
import queue
from collections.abc import Iterator
from typing import Any, cast

import pytest

from grafix.core.geometry import Geometry
from grafix.core.scene import normalize_scene
from grafix.interactive.runtime.mp_draw import (
    DrawResult,
    MpDraw,
    _DrawTask,
)
from grafix.runtime_config_loader import runtime_config

_EFFECTIVE_CONFIG = runtime_config()


def _mp_draw(draw: Any, **kwargs: Any) -> MpDraw:
    return MpDraw(draw, effective_config=_EFFECTIVE_CONFIG, **kwargs)


def _empty_draw(_t: float) -> Geometry:
    return Geometry.create(op="concat")


class _InMemoryMpQueue(queue.Queue[object]):
    """multiprocessing.Queue の lifecycle を備えた同期 test queue。"""

    def __init__(self, *, maxsize: int = 0) -> None:
        super().__init__(maxsize=maxsize)
        self.close_calls = 0
        self.join_thread_calls = 0
        self.cancel_join_thread_calls = 0

    def close(self) -> None:
        self.close_calls += 1

    def join_thread(self) -> None:
        self.join_thread_calls += 1

    def cancel_join_thread(self) -> None:
        self.cancel_join_thread_calls += 1


class _InMemoryContext:
    def Queue(self, *, maxsize: int = 0) -> _InMemoryMpQueue:
        return _InMemoryMpQueue(maxsize=maxsize)


@pytest.fixture
def initialized_mp_draw(monkeypatch: pytest.MonkeyPatch) -> Iterator[MpDraw]:
    """同期 queue を使い、worker だけ起動せず初期化した MpDraw を返す。"""

    context = _InMemoryContext()

    def get_in_memory_context(method: str) -> _InMemoryContext:
        assert method == "spawn"
        return context

    def skip_worker_start(_mp_draw: MpDraw, *, wait_ready: bool) -> None:
        assert isinstance(wait_ready, bool)

    monkeypatch.setattr(mp, "get_context", get_in_memory_context)
    monkeypatch.setattr(MpDraw, "_start_generation", skip_worker_start)
    mp_draw = _mp_draw(_empty_draw, n_worker=1)
    try:
        yield mp_draw
    finally:
        mp_draw.close()


def test_mp_draw_rejects_zero_workers() -> None:
    with pytest.raises(ValueError, match="1 以上"):
        _mp_draw(_empty_draw, n_worker=0)


@pytest.mark.parametrize("n_worker", [True, 1.0, "1"])
def test_mp_draw_rejects_implicitly_convertible_worker_count(
    n_worker: object,
) -> None:
    with pytest.raises(TypeError, match="n_worker.*int"):
        _mp_draw(_empty_draw, n_worker=n_worker)  # type: ignore[arg-type]


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("inf"), float("nan")])
def test_mp_draw_rejects_invalid_evaluation_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="evaluation_timeout"):
        _mp_draw(_empty_draw, n_worker=1, evaluation_timeout=timeout)


@pytest.mark.parametrize("timeout", [True, "1", object()])
def test_mp_draw_rejects_non_real_evaluation_timeout(timeout: object) -> None:
    with pytest.raises(TypeError, match="evaluation_timeout"):
        _mp_draw(
            _empty_draw,
            n_worker=1,
            evaluation_timeout=timeout,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("epoch", [True, 1.0, "1"])
def test_begin_epoch_rejects_implicit_integer_conversion(
    initialized_mp_draw: MpDraw,
    epoch: object,
) -> None:
    with pytest.raises(TypeError, match="epoch"):
        initialized_mp_draw.begin_epoch(cast(Any, epoch))

    assert initialized_mp_draw.current_epoch == 0


def test_begin_epoch_rejects_negative_value(
    initialized_mp_draw: MpDraw,
) -> None:
    with pytest.raises(ValueError, match="epoch"):
        initialized_mp_draw.begin_epoch(-1)

    assert initialized_mp_draw.current_epoch == 0


class _StringSubclass(str):
    pass


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        pytest.param("t", True, TypeError, id="t-bool"),
        pytest.param("t", "0.0", TypeError, id="t-string"),
        pytest.param("t", float("inf"), ValueError, id="t-infinite"),
        pytest.param("snapshot_revision", True, TypeError, id="revision-bool"),
        pytest.param("snapshot_revision", 1.0, TypeError, id="revision-float"),
        pytest.param("snapshot_revision", "1", TypeError, id="revision-string"),
        pytest.param("snapshot_revision", -1, ValueError, id="revision-negative"),
        pytest.param("epoch", True, TypeError, id="epoch-bool"),
        pytest.param("epoch", 1.0, TypeError, id="epoch-float"),
        pytest.param("epoch", "1", TypeError, id="epoch-string"),
        pytest.param("quality", 1, TypeError, id="quality-non-string"),
        pytest.param(
            "quality",
            _StringSubclass("draft"),
            TypeError,
            id="quality-string-subclass",
        ),
        pytest.param("quality", "preview", ValueError, id="quality-unknown"),
    ],
)
def test_submit_rejects_noncanonical_scalars_before_enqueue(
    initialized_mp_draw: MpDraw,
    field: str,
    value: object,
    error_type: type[Exception],
) -> None:
    arguments: dict[str, object] = {
        "t": 0.0,
        "snapshot_revision": 0,
        "snapshot": {},
        "effect_order_snapshot": {},
        "cc_snapshot": None,
        "epoch": 0,
        "quality": "draft",
    }
    arguments[field] = value

    with pytest.raises(error_type, match=field):
        initialized_mp_draw.submit(**cast(Any, arguments))

    assert initialized_mp_draw.last_submitted_frame_id == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("snapshot", [], id="snapshot-list"),
        pytest.param(
            "effect_order_snapshot",
            [],
            id="effect-order-list",
        ),
        pytest.param("cc_snapshot", {}, id="cc-mapping"),
    ],
)
def test_submit_rejects_wrong_snapshot_composition_before_enqueue(
    initialized_mp_draw: MpDraw,
    field: str,
    value: object,
) -> None:
    arguments: dict[str, object] = {
        "t": 0.0,
        "snapshot_revision": 0,
        "snapshot": {},
        "effect_order_snapshot": {},
        "cc_snapshot": None,
        "epoch": 0,
        "quality": "draft",
    }
    arguments[field] = value

    with pytest.raises(TypeError, match=field):
        initialized_mp_draw.submit(**cast(Any, arguments))

    assert initialized_mp_draw.last_submitted_frame_id == 0


class _ConstructorFaultQueue:
    def __init__(self) -> None:
        self.close_calls = 0
        self.join_thread_calls = 0
        self.cancel_join_thread_calls = 0
        self.put_calls = 0

    def put(self, _value: object, *, timeout: float) -> None:
        assert timeout > 0.0
        self.put_calls += 1

    def close(self) -> None:
        self.close_calls += 1

    def join_thread(self) -> None:
        self.join_thread_calls += 1

    def cancel_join_thread(self) -> None:
        self.cancel_join_thread_calls += 1


class _ConstructorFaultProcess:
    def __init__(
        self,
        *,
        name: str,
        pid: int,
        fail_start: bool,
    ) -> None:
        self.name = name
        self._next_pid = int(pid)
        self._fail_start = bool(fail_start)
        self.pid: int | None = None
        self.exitcode: int | None = None
        self.start_calls = 0
        self.join_calls = 0
        self.terminate_calls = 0
        self.kill_calls = 0
        self._alive = False

    def start(self) -> None:
        self.start_calls += 1
        if self._fail_start:
            raise OSError("process start failed")
        self.pid = self._next_pid
        self._alive = True

    def is_alive(self) -> bool:
        return self._alive

    def join(self, *, timeout: float) -> None:
        assert timeout >= 0.0
        self.join_calls += 1

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._alive = False
        self.exitcode = -15

    def kill(self) -> None:
        self.kill_calls += 1
        self._alive = False
        self.exitcode = -9


class _ConstructorFaultContext:
    def __init__(
        self,
        *,
        queue_failure_call: int | None = None,
        process_start_failure_call: int | None = None,
    ) -> None:
        self.queue_failure_call = queue_failure_call
        self.process_start_failure_call = process_start_failure_call
        self.queues: list[_ConstructorFaultQueue] = []
        self.processes: list[_ConstructorFaultProcess] = []
        self._queue_calls = 0

    def Queue(self, *, maxsize: int = 0) -> _ConstructorFaultQueue:
        assert maxsize >= 0
        self._queue_calls += 1
        if self._queue_calls == self.queue_failure_call:
            raise OSError("queue creation failed")
        created = _ConstructorFaultQueue()
        self.queues.append(created)
        return created

    def Process(
        self,
        *,
        target: object,
        args: tuple[object, ...],
        name: str,
    ) -> _ConstructorFaultProcess:
        del target, args
        call = len(self.processes) + 1
        created = _ConstructorFaultProcess(
            name=name,
            pid=70_000 + call,
            fail_start=call == self.process_start_failure_call,
        )
        self.processes.append(created)
        return created


def _install_constructor_fault_context(
    monkeypatch: pytest.MonkeyPatch,
    context: _ConstructorFaultContext,
) -> None:
    monkeypatch.setattr(mp, "get_context", lambda method: context)


def test_constructor_failure_before_first_queue_preserves_root_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _ConstructorFaultContext(queue_failure_call=1)
    _install_constructor_fault_context(monkeypatch, context)

    with pytest.raises(RuntimeError) as exc_info:
        _mp_draw(_empty_draw, n_worker=1)

    assert isinstance(exc_info.value.__cause__, OSError)
    assert str(exc_info.value.__cause__) == "queue creation failed"
    assert context.queues == []
    assert context.processes == []


def test_constructor_failure_closes_every_partially_created_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # task queue と最初の control queue の後、2本目の control queue で失敗する。
    context = _ConstructorFaultContext(queue_failure_call=3)
    _install_constructor_fault_context(monkeypatch, context)

    with pytest.raises(RuntimeError) as exc_info:
        _mp_draw(_empty_draw, n_worker=2)

    assert isinstance(exc_info.value.__cause__, OSError)
    assert len(context.queues) == 2
    assert context.processes == []
    assert all(queue.close_calls == 1 for queue in context.queues)
    assert all(queue.join_thread_calls == 1 for queue in context.queues)


def test_constructor_failure_stops_partial_processes_and_closes_all_queues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _ConstructorFaultContext(process_start_failure_call=2)
    _install_constructor_fault_context(monkeypatch, context)
    active_children_before = {proc.pid for proc in mp.active_children() if proc.pid is not None}

    with pytest.raises(RuntimeError) as exc_info:
        _mp_draw(_empty_draw, n_worker=2)

    assert isinstance(exc_info.value.__cause__, OSError)
    assert str(exc_info.value.__cause__) == "process start failed"
    assert len(context.queues) == 4
    assert len(context.processes) == 2
    assert all(not process.is_alive() for process in context.processes)
    assert context.processes[0].terminate_calls == 1
    assert context.processes[1].start_calls == 1
    assert all(queue.close_calls == 1 for queue in context.queues)
    assert all(queue.join_thread_calls == 1 for queue in context.queues)
    assert all(queue.cancel_join_thread_calls == 1 for queue in context.queues)
    assert {
        proc.pid for proc in mp.active_children() if proc.pid is not None
    } == active_children_before


def test_single_slot_task_queue_drops_old_frame_and_keeps_latest(
    initialized_mp_draw: MpDraw,
) -> None:
    """1-worker 相当の満杯 queue では待機中の古い frame だけを置換する。"""

    old = _DrawTask(
        frame_id=1,
        t=1.0,
        snapshot_revision=7,
        cc_snapshot=None,
        snapshot=None,
        effect_order_snapshot=None,
        epoch=0,
        generation=0,
        quality="draft",
    )
    mp_draw = initialized_mp_draw
    task_q = mp_draw._task_q
    task_q.put(old)

    mp_draw.submit(
        t=2.0,
        snapshot_revision=7,
        snapshot={},
        effect_order_snapshot={},
        epoch=0,
        quality="draft",
    )

    latest = task_q.get_nowait()
    assert isinstance(latest, _DrawTask)
    assert latest.frame_id == 1
    assert latest.t == 2.0
    assert latest.snapshot_revision == 7
    assert mp_draw.task_enqueue_count == 1
    assert mp_draw.task_drop_count == 1


def test_batched_drain_keeps_success_time_when_a_later_result_is_an_error(
    initialized_mp_draw: MpDraw,
) -> None:
    """batch の終端 error と preview 用 success を同じ result として混同しない。"""

    successful = DrawResult(
        frame_id=10,
        t=0.25,
        epoch=0,
        generation=0,
        snapshot_revision=0,
        layers=tuple(normalize_scene(_empty_draw(0.25))),
        records=(),
        labels=(),
        effect_chains=(),
    )
    failed = DrawResult(
        frame_id=11,
        t=0.5,
        epoch=0,
        generation=0,
        snapshot_revision=0,
        layers=(),
        records=(),
        labels=(),
        effect_chains=(),
        error="later frame failed",
    )
    mp_draw = initialized_mp_draw
    mp_draw._result_q.put(successful)
    mp_draw._result_q.put(failed)
    latest_received = mp_draw.poll_latest()

    assert latest_received is failed
    latest_successful = mp_draw.latest_successful_result()
    assert latest_successful is successful
    assert latest_successful.t == pytest.approx(0.25)


def test_result_drain_discards_old_epoch_and_keeps_diagnostic(
    initialized_mp_draw: MpDraw,
) -> None:
    stale_error = DrawResult(
        frame_id=10,
        layers=(),
        records=(),
        labels=(),
        error="old timeline failure",
        t=99.0,
        epoch=1,
        generation=0,
        snapshot_revision=0,
        effect_chains=(),
    )
    fresh = DrawResult(
        frame_id=11,
        layers=tuple(normalize_scene(_empty_draw(2.0))),
        records=(),
        labels=(),
        t=2.0,
        epoch=2,
        generation=0,
        snapshot_revision=0,
        effect_chains=(),
    )
    mp_draw = initialized_mp_draw
    assert mp_draw.begin_epoch(2) == 2
    mp_draw._result_q.put(stale_error)
    mp_draw._result_q.put(fresh)
    latest_received = mp_draw.poll_latest()

    assert latest_received is fresh
    assert mp_draw.latest_successful_result() is fresh
    assert mp_draw.completed_result_count == 2
    assert mp_draw.stale_result_count == 1
    assert mp_draw.last_stale_result == (10, 1, 2)


def test_result_drain_rejects_result_from_old_worker_generation(
    initialized_mp_draw: MpDraw,
) -> None:
    cached = DrawResult(
        frame_id=10,
        t=0.0,
        epoch=0,
        snapshot_revision=0,
        layers=tuple(normalize_scene(_empty_draw(0.0))),
        records=(),
        labels=(),
        effect_chains=(),
        generation=1,
    )
    stale = DrawResult(
        frame_id=999,
        t=9.0,
        epoch=0,
        snapshot_revision=0,
        layers=tuple(normalize_scene(_empty_draw(9.0))),
        records=(),
        labels=(),
        effect_chains=(),
        generation=0,
    )
    mp_draw = initialized_mp_draw
    assert mp_draw.restart("test generation boundary") == 1
    mp_draw._result_q.put(cached)
    assert mp_draw.poll_latest() is cached
    completed_before_stale = mp_draw.completed_result_count

    mp_draw._result_q.put(stale)
    assert mp_draw.poll_latest() is None

    assert mp_draw.latest_successful_result() is cached
    assert mp_draw.completed_result_count == completed_before_stale
    assert mp_draw.stale_generation_result_count == 1
    assert mp_draw.last_stale_generation_result == (999, 0, 1)


def test_begin_epoch_invalidates_cached_result_and_queued_task(
    initialized_mp_draw: MpDraw,
) -> None:
    cached = DrawResult(
        frame_id=3,
        layers=tuple(normalize_scene(_empty_draw(3.0))),
        records=(),
        labels=(),
        t=3.0,
        epoch=0,
        generation=0,
        snapshot_revision=0,
        effect_chains=(),
    )
    task = _DrawTask(
        frame_id=4,
        t=4.0,
        snapshot_revision=0,
        cc_snapshot=None,
        snapshot=None,
        effect_order_snapshot=None,
        epoch=0,
        generation=0,
        quality="draft",
    )

    mp_draw = initialized_mp_draw
    task_q = mp_draw._task_q
    task_q.put(task)
    mp_draw._result_q.put(cached)
    assert mp_draw.poll_latest() is cached

    assert mp_draw.begin_epoch(1) == 1
    assert mp_draw.current_epoch == 1
    assert mp_draw.poll_latest() is None
    assert mp_draw.latest_successful_result() is None
    assert task_q.empty()


def test_close_is_idempotent_and_joins_both_queue_threads(
    initialized_mp_draw: MpDraw,
) -> None:
    mp_draw = initialized_mp_draw
    task_q = mp_draw._task_q
    result_q = mp_draw._result_q
    assert isinstance(task_q, _InMemoryMpQueue)
    assert isinstance(result_q, _InMemoryMpQueue)

    mp_draw.close()
    mp_draw.close()

    assert task_q.close_calls == 1
    assert task_q.join_thread_calls == 1
    assert result_q.close_calls == 1
    assert result_q.join_thread_calls == 1
