from __future__ import annotations

import multiprocessing as mp
import os
import json
import queue
import time
import weakref
from collections import deque
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import numpy as np

from grafix.export.capture_publish import capture_manifest_path_for
from grafix.core.evaluation_context import (
    EMPTY_EXTERNAL_DEPENDENCIES_FINGERPRINT,
    EvaluationFingerprint,
)
from grafix.core.export_format import ExportFormat
from grafix.export.capture_provenance import CaptureProvenanceBuilder
from grafix.core.geometry import Geometry
from grafix.core.layer import Layer
from grafix.core.parameters import ParamStore
from grafix.core.pipeline import RealizedLayer
from grafix.core.realize import GeometryCacheKey
from grafix.core.realized_geometry import RealizedGeometry
from grafix.runtime_config_loader import runtime_config
from grafix.core.runtime_limits import RuntimeLimits
from grafix.export import capture as capture_module
from grafix.interactive.runtime import export_job_system
from grafix.interactive.runtime.export_job_system import (
    CaptureExportSnapshot,
    ExportJob,
    ExportJobResult,
    ExportJobStatus,
    ExportJobSystem,
    ExportQueueFullError,
    FrameExportSnapshot,
    estimate_snapshot_retained_bytes,
)

pytestmark = pytest.mark.integration

_WAIT_TIMEOUT_S = 8.0


def _provenance_draw(_t: float) -> tuple[object, ...]:
    return ()


_PROVENANCE_STORE = ParamStore()
_PROVENANCE_BUILDER = CaptureProvenanceBuilder(
    _provenance_draw,
    config=runtime_config(),
    parameter_source="code",
    parameter_store_path=None,
    parameter_load_provenance=_PROVENANCE_STORE.load_provenance,
)


def _provenance(*, t: float = 0.0):
    return _PROVENANCE_BUILDER.frame(
        _PROVENANCE_STORE,
        t=t,
        frame_index=0,
        quality="final",
        origin="interactive",
    )


def _snapshot() -> CaptureExportSnapshot:
    return CaptureExportSnapshot(
        layers=(),
        canvas_size=(100, 80),
        background_color_rgb01=(1.0, 1.0, 1.0),
        t=0.0,
        provenance=_provenance(),
        gcode_params=runtime_config().gcode,
    )


def _sized_snapshot(byte_size: int) -> CaptureExportSnapshot:
    target_bytes = int(byte_size)
    vertex_count, remainder = divmod(target_bytes - 8, 12)
    if vertex_count < 0 or remainder:
        raise ValueError("byte_size は 12 * vertex_count + 8 で表せる必要があります")
    geometry = Geometry.create("export-job-test-geometry")
    realized = RealizedGeometry(
        coords=np.zeros((vertex_count, 3), dtype=np.float32),
        offsets=np.asarray((0, vertex_count), dtype=np.int32),
    )
    layer = RealizedLayer(
        layer=Layer(geometry=geometry, site_id="sized-layer"),
        realized=realized,
        cache_key=GeometryCacheKey(
            geometry_id=geometry.id,
            evaluation=EvaluationFingerprint("0" * 64),
            external_dependencies=EMPTY_EXTERNAL_DEPENDENCIES_FINGERPRINT,
        ),
        color=(0.0, 0.0, 0.0),
        thickness=0.01,
    )
    return CaptureExportSnapshot(
        layers=(layer,),
        canvas_size=(100, 80),
        background_color_rgb01=(1.0, 1.0, 1.0),
        t=0.0,
        provenance=_provenance(),
        gcode_params=runtime_config().gcode,
    )


def test_snapshot_retained_bytes_deduplicates_shared_realized_arrays() -> None:
    snapshot = _sized_snapshot(80)
    first = snapshot.layers[0]
    shared = RealizedLayer(
        layer=Layer(
            geometry=first.layer.geometry,
            site_id="shared-realized-layer",
        ),
        realized=first.realized,
        cache_key=first.cache_key,
        color=first.color,
        thickness=first.thickness,
    )

    duplicated = replace(snapshot, layers=(first, shared))

    assert estimate_snapshot_retained_bytes(duplicated) == 80


def test_frame_export_snapshot_rejects_non_finite_capture_time() -> None:
    with pytest.raises(ValueError, match="t"):
        FrameExportSnapshot(
            layers=(),
            canvas_size=(10, 10),
            background_color_rgb01=(1.0, 1.0, 1.0),
            t=float("nan"),
        )


@pytest.mark.parametrize(
    ("field", "value", "error_match"),
    [
        ("layers", [], "layers"),
        ("layers", (object(),), "layers"),
        ("canvas_size", [10, 10], "canvas_size"),
        ("canvas_size", (True, 10), "canvas_size"),
        ("canvas_size", (0, 10), "canvas_size"),
        ("background_color_rgb01", [1.0, 1.0, 1.0], "background_color_rgb01"),
        ("background_color_rgb01", (1.1, 1.0, 1.0), "background_color_rgb01"),
        (
            "background_color_rgb01",
            (float("nan"), 1.0, 1.0),
            "background_color_rgb01",
        ),
        ("background_color_rgb01", (True, 1.0, 1.0), "background_color_rgb01"),
        ("t", True, "t"),
        ("t", "0.0", "t"),
        ("t", float("inf"), "t"),
        ("provenance", object(), "provenance"),
        ("gcode_params", object(), "gcode_params"),
    ],
)
def test_frame_export_snapshot_rejects_implicit_dto_coercion(
    field: str,
    value: Any,
    error_match: str,
) -> None:
    values: dict[str, Any] = {
        "layers": (),
        "canvas_size": (10, 10),
        "background_color_rgb01": (1.0, 1.0, 1.0),
        "t": 0.0,
    }
    values[field] = value

    with pytest.raises((TypeError, ValueError), match=error_match):
        FrameExportSnapshot(**values)


def test_partial_queue_creation_closes_the_first_queue() -> None:
    calls: list[str] = []

    class Queue:
        def cancel_join_thread(self) -> None:
            calls.append("cancel")

        def close(self) -> None:
            calls.append("close")

        def join_thread(self) -> None:
            calls.append("join")

    class Context:
        def __init__(self) -> None:
            self.count = 0

        def Queue(self, **_kwargs: object) -> object:  # noqa: N802 - multiprocessing API
            self.count += 1
            if self.count == 1:
                return Queue()
            raise RuntimeError("second queue failed")

    system = object.__new__(ExportJobSystem)
    system._ctx = cast(Any, Context())

    with pytest.raises(RuntimeError, match="second queue failed"):
        system._create_queues()

    assert calls == ["cancel", "close", "join"]


def test_close_queue_attempts_every_step_and_raises_first_base_exception() -> None:
    calls: list[str] = []

    class CleanupFault(BaseException):
        pass

    first_error = CleanupFault("cancel failed")

    class Queue:
        def cancel_join_thread(self) -> None:
            calls.append("cancel")
            raise first_error

        def close(self) -> None:
            calls.append("close")
            raise CleanupFault("close failed")

        def join_thread(self) -> None:
            calls.append("join")

    with pytest.raises(CleanupFault) as exc_info:
        ExportJobSystem._close_queue(cast(Any, Queue()), cancel=True)

    assert exc_info.value is first_error
    assert calls == ["cancel", "close", "join"]


def test_close_queues_continues_with_result_queue_after_task_queue_failure() -> None:
    calls: list[str] = []

    class CleanupFault(BaseException):
        pass

    first_error = CleanupFault("task cancel failed")

    class Queue:
        def __init__(self, name: str, *, fail_cancel: bool = False) -> None:
            self.name = name
            self.fail_cancel = fail_cancel

        def cancel_join_thread(self) -> None:
            calls.append(f"{self.name}.cancel")
            if self.fail_cancel:
                raise first_error

        def close(self) -> None:
            calls.append(f"{self.name}.close")

        def join_thread(self) -> None:
            calls.append(f"{self.name}.join")

    system = object.__new__(ExportJobSystem)
    system._task_q = cast(Any, Queue("task", fail_cancel=True))
    system._result_q = cast(Any, Queue("result"))

    with pytest.raises(CleanupFault) as exc_info:
        system._close_queues(cancel_pending=True)

    assert exc_info.value is first_error
    assert calls == [
        "task.cancel",
        "task.close",
        "task.join",
        "result.close",
        "result.join",
    ]


def test_join_process_preserves_escalation_order_after_cleanup_failures() -> None:
    calls: list[str] = []

    class CleanupFault(BaseException):
        pass

    first_error = CleanupFault("initial join failed")

    class Process:
        def __init__(self) -> None:
            self.join_count = 0
            self.alive = iter((True, True, False))

        def join(self, *, timeout: float) -> None:
            assert timeout == export_job_system._WORKER_JOIN_TIMEOUT_S
            self.join_count += 1
            calls.append(f"join:{self.join_count}")
            if self.join_count == 1:
                raise first_error

        def is_alive(self) -> bool:
            calls.append("is_alive")
            return next(self.alive)

        def terminate(self) -> None:
            calls.append("terminate")
            raise CleanupFault("terminate failed")

        def kill(self) -> None:
            calls.append("kill")

        def close(self) -> None:
            calls.append("close")
            raise CleanupFault("process close failed")

    with pytest.raises(CleanupFault) as exc_info:
        ExportJobSystem._join_process(cast(Any, Process()))

    assert exc_info.value is first_error
    assert calls == [
        "join:1",
        "is_alive",
        "terminate",
        "join:2",
        "is_alive",
        "kill",
        "join:3",
        "is_alive",
        "close",
    ]


def test_replace_worker_attempts_join_queues_and_recreation_after_failures() -> None:
    calls: list[str] = []

    class CleanupFault(BaseException):
        pass

    first_error = CleanupFault("terminate failed")

    class Process:
        def is_alive(self) -> bool:
            calls.append("is_alive")
            return True

        def terminate(self) -> None:
            calls.append("terminate")
            raise first_error

    system = object.__new__(ExportJobSystem)
    system._proc = cast(Any, Process())

    def fail_join(_proc: object) -> None:
        calls.append("join")
        raise CleanupFault("join failed")

    def fail_close_queues(*, cancel_pending: bool) -> None:
        assert cancel_pending is True
        calls.append("close queues")
        raise CleanupFault("queue close failed")

    def create_queues() -> None:
        calls.append("create queues")

    system._join_process = fail_join
    system._close_queues = fail_close_queues
    system._create_queues = create_queues

    with pytest.raises(CleanupFault) as exc_info:
        system._replace_worker()

    assert exc_info.value is first_error
    assert calls == [
        "is_alive",
        "terminate",
        "join",
        "close queues",
        "create queues",
    ]
    assert system._proc is None


def test_close_treats_full_idle_sentinel_queue_as_pending_work() -> None:
    calls: list[str] = []

    class Process:
        def is_alive(self) -> bool:
            calls.append("is_alive")
            return True

    class FullQueue:
        def put_nowait(self, value: object) -> None:
            assert value is None
            calls.append("signal")
            raise queue.Full

    system = object.__new__(ExportJobSystem)
    system._closed = False
    system._in_flight = None
    system._pending = deque()
    system._completed = deque()
    system._proc = cast(Any, Process())
    system._task_q = cast(Any, FullQueue())
    system._join_process = lambda _proc: calls.append("join")
    system._close_queues = lambda *, cancel_pending: calls.append(
        f"close queues:{cancel_pending}"
    )

    system.close()

    assert calls == [
        "is_alive",
        "signal",
        "join",
        "close queues:True",
    ]
    assert system._proc is None


def test_worker_releases_completed_job_snapshot_before_waiting_for_next(
    tmp_path: Path,
) -> None:
    """idle workerが直前の巨大geometryをloop localとして保持しない。"""

    class TaskQueue:
        def __init__(
            self,
            job: ExportJob,
            probe_ref: weakref.ReferenceType[Any],
        ) -> None:
            self._job: ExportJob | None = job
            self._probe_ref = probe_ref

        def get(self) -> ExportJob | None:
            job = self._job
            if job is not None:
                self._job = None
                return job
            # 2回目のgetは実workerならblocking待機に入る地点。この前に
            # loop local `job` が解放され、snapshot内のprobeも回収済みである。
            assert self._probe_ref() is None
            return None

        def close(self) -> None:
            return

        def join_thread(self) -> None:
            return

    class ResultQueue:
        def __init__(self) -> None:
            self.messages: list[object] = []

        def put(self, message: object) -> None:
            self.messages.append(message)

        def close(self) -> None:
            return

        def join_thread(self) -> None:
            return

    def make_task_queue() -> tuple[TaskQueue, weakref.ReferenceType[Any]]:
        snapshot = _sized_snapshot(80)
        probe_ref = weakref.ref(snapshot.layers[0].realized.coords)
        staging_dir = tmp_path / ".probe.export-1-test"
        staging_dir.mkdir()
        job = ExportJob(
            job_id=1,
            format=ExportFormat.GCODE,
            snapshot=snapshot,
            output_path=tmp_path / "probe.gcode",
            timeout_s=1.0,
            staging_dir=staging_dir,
        )
        return TaskQueue(job, probe_ref), probe_ref

    task_queue, probe_ref = make_task_queue()
    result_queue = ResultQueue()

    export_job_system._export_worker_main(  # noqa: SLF001 - worker lifecycle regression
        cast(Any, task_queue),
        cast(Any, result_queue),
        _success_backend,
    )

    assert probe_ref() is None
    assert any(isinstance(message, ExportJobResult) for message in result_queue.messages)


def _success_backend(job: ExportJob) -> tuple[Path, ...]:
    path = job.staging_dir / job.output_path.name
    path.write_bytes(b"export")
    return (path,)


def _two_second_backend(job: ExportJob) -> tuple[Path, ...]:
    paths = _success_backend(job)
    time.sleep(2.0)
    return paths


def _bounded_backend(job: ExportJob) -> tuple[Path, ...]:
    time.sleep(0.75)
    return _success_backend(job)


def _conditional_backend(job: ExportJob) -> tuple[Path, ...]:
    stem = job.output_path.stem
    if stem == "fail":
        raise ValueError("backend failure")
    if stem == "slow":
        paths = _success_backend(job)
        time.sleep(2.0)
        return paths
    if stem == "die":
        _success_backend(job)
        os._exit(7)
    return _success_backend(job)


def _outside_staging_backend(job: ExportJob) -> tuple[Path, ...]:
    return (job.output_path,)


def _wait_for_job(
    system: ExportJobSystem,
    job_id: int,
    *,
    collected: list[ExportJobResult] | None = None,
) -> ExportJobResult:
    results = [] if collected is None else collected
    deadline = time.monotonic() + _WAIT_TIMEOUT_S
    while time.monotonic() < deadline:
        results.extend(system.poll())
        for result in results:
            if result.job_id == job_id:
                return result
        time.sleep(0.01)
    pytest.fail(f"export job timeout: job_id={job_id}")


def test_export_messages_are_immutable(tmp_path: Path) -> None:
    snapshot = _snapshot()
    job = ExportJob(
        job_id=1,
        format=ExportFormat.PNG,
        snapshot=snapshot,
        output_path=tmp_path / "out.png",
        timeout_s=1.0,
        staging_dir=tmp_path / ".out.export-1-test",
        output_size=(100, 80),
    )
    result = ExportJobResult(
        job_id=1,
        format=ExportFormat.PNG,
        status=ExportJobStatus.SUCCESS,
        output_path=Path("out.png"),
    )

    with pytest.raises(FrozenInstanceError):
        snapshot.canvas_size = (1, 1)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        job.timeout_s = 2.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.status = ExportJobStatus.ERROR  # type: ignore[misc]


def test_export_job_requires_keyword_arguments(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        ExportJob(  # type: ignore[misc]
            1,
            ExportFormat.PNG,
            _snapshot(),
            tmp_path / "out.png",
            1.0,
            tmp_path / ".out.export-1-test",
            False,
            (100, 80),
        )


def test_export_job_rejects_preview_snapshot_at_capture_boundary(
    tmp_path: Path,
) -> None:
    preview = FrameExportSnapshot(
        layers=(),
        canvas_size=(100, 80),
        background_color_rgb01=(1.0, 1.0, 1.0),
        t=0.0,
        provenance=_provenance(),
    )

    with pytest.raises(TypeError, match="CaptureExportSnapshot"):
        ExportJob(
            job_id=1,
            format=ExportFormat.PNG,
            snapshot=cast(Any, preview),
            output_path=tmp_path / "out.png",
            timeout_s=1.0,
            staging_dir=tmp_path / ".out.export-1-test",
            output_size=(100, 80),
        )


def test_export_job_requires_explicit_gcode_params(tmp_path: Path) -> None:
    snapshot = replace(_snapshot(), gcode_params=None)

    with pytest.raises(ValueError, match="gcode_params"):
        ExportJob(
            job_id=1,
            format=ExportFormat.GCODE,
            snapshot=snapshot,
            output_path=tmp_path / "out.gcode",
            timeout_s=1.0,
            staging_dir=tmp_path / ".out.export-1-test",
        )


@pytest.mark.parametrize(
    ("field", "value", "error_match"),
    [
        ("job_id", True, "job_id"),
        ("job_id", 1.0, "job_id"),
        ("job_id", "1", "job_id"),
        ("job_id", 0, "job_id"),
        ("output_path", "out.gcode", "output_path"),
        ("staging_dir", ".out.export-1-test", "staging_dir"),
        ("timeout_s", True, "timeout_s"),
        ("timeout_s", "1.0", "timeout_s"),
        ("timeout_s", 0.0, "timeout_s"),
        ("timeout_s", float("nan"), "timeout_s"),
        ("deadline_monotonic", True, "deadline_monotonic"),
        ("deadline_monotonic", "1.0", "deadline_monotonic"),
        ("deadline_monotonic", float("inf"), "deadline_monotonic"),
    ],
)
def test_export_job_rejects_implicit_dto_coercion(
    field: str,
    value: Any,
    error_match: str,
    tmp_path: Path,
) -> None:
    values: dict[str, Any] = {
        "job_id": 1,
        "format": ExportFormat.GCODE,
        "snapshot": _snapshot(),
        "output_path": tmp_path / "out.gcode",
        "timeout_s": 1.0,
        "staging_dir": tmp_path / ".out.export-1-test",
    }
    values[field] = value

    with pytest.raises((TypeError, ValueError), match=error_match):
        ExportJob(**values)


def test_export_messages_require_enum_fields(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="ExportFormat"):
        ExportJob(
            job_id=1,
            format=cast(Any, "png"),
            snapshot=_snapshot(),
            output_path=tmp_path / "out.png",
            timeout_s=1.0,
            staging_dir=tmp_path / ".out.export-1-test",
            output_size=(100, 80),
        )
    with pytest.raises(TypeError, match="ExportFormat"):
        ExportJobResult(
            job_id=1,
            format=cast(Any, "png"),
            status=ExportJobStatus.SUCCESS,
            output_path=tmp_path / "out.png",
        )
    with pytest.raises(TypeError, match="ExportJobStatus"):
        ExportJobResult(
            job_id=1,
            format=ExportFormat.PNG,
            status=cast(Any, "success"),
            output_path=tmp_path / "out.png",
        )


@pytest.mark.parametrize(
    ("field", "value", "error_match"),
    [
        ("job_id", True, "job_id"),
        ("job_id", 1.0, "job_id"),
        ("job_id", "1", "job_id"),
        ("job_id", 0, "job_id"),
        ("output_path", "out.png", "output_path"),
        ("paths", [], "paths"),
        ("paths", ("out.png",), "paths"),
        ("error", object(), "error"),
        ("worker_pid", True, "worker_pid"),
        ("worker_pid", 0, "worker_pid"),
        ("worker_exitcode", 1.0, "worker_exitcode"),
        ("manifest_path", "out.png.json", "manifest_path"),
    ],
)
def test_export_job_result_rejects_implicit_dto_coercion(
    field: str,
    value: Any,
    error_match: str,
    tmp_path: Path,
) -> None:
    values: dict[str, Any] = {
        "job_id": 1,
        "format": ExportFormat.PNG,
        "status": ExportJobStatus.SUCCESS,
        "output_path": tmp_path / "out.png",
    }
    values[field] = value

    with pytest.raises((TypeError, ValueError), match=error_match):
        ExportJobResult(**values)


@pytest.mark.parametrize(
    ("format", "output_size"),
    [
        (ExportFormat.PNG, None),
        (ExportFormat.GCODE, (100, 80)),
    ],
)
def test_export_job_requires_output_size_exactly_for_png(
    tmp_path: Path,
    format: ExportFormat,
    output_size: tuple[int, int] | None,
) -> None:
    with pytest.raises(ValueError, match="output_size"):
        ExportJob(
            job_id=1,
            format=format,
            snapshot=_snapshot(),
            output_path=tmp_path / f"out.{format.value}",
            timeout_s=1.0,
            staging_dir=tmp_path / ".out.export-1-test",
            output_size=output_size,
        )


@pytest.mark.parametrize(
    ("format", "output_size"),
    [
        (ExportFormat.PNG, None),
        (ExportFormat.GCODE, (100, 80)),
    ],
)
def test_submit_rejects_output_size_mismatch_before_staging(
    tmp_path: Path,
    format: ExportFormat,
    output_size: tuple[int, int] | None,
) -> None:
    system = ExportJobSystem()
    try:
        with pytest.raises(ValueError, match="output_size"):
            system.submit(
                format=format,
                snapshot=_snapshot(),
                output_path=tmp_path / f"out.{format.value}",
                output_size=output_size,
            )

        assert system.has_work is False
        assert not tuple(tmp_path.glob(".*.export-*"))
    finally:
        system.close()


def test_submit_rejects_string_format_before_staging(tmp_path: Path) -> None:
    system = ExportJobSystem()
    try:
        with pytest.raises(TypeError, match="ExportFormat"):
            system.submit(
                format=cast(Any, "png"),
                snapshot=_snapshot(),
                output_path=tmp_path / "out.png",
                output_size=(100, 80),
            )

        assert system.has_work is False
        assert not tuple(tmp_path.glob(".*.export-*"))
    finally:
        system.close()


@pytest.mark.parametrize("value", [1, "true"])
def test_export_job_requires_exact_split_layers_bool(
    tmp_path: Path,
    value: object,
) -> None:
    with pytest.raises(TypeError, match="split_gcode_layers"):
        ExportJob(
            job_id=1,
            format=ExportFormat.GCODE,
            snapshot=_snapshot(),
            output_path=tmp_path / "out.gcode",
            timeout_s=1.0,
            staging_dir=tmp_path / ".out.export-1-test",
            split_gcode_layers=cast(Any, value),
        )


def test_submit_rejects_layer_split_for_non_gcode_before_staging(
    tmp_path: Path,
) -> None:
    system = ExportJobSystem()
    try:
        with pytest.raises(ValueError, match="split_gcode_layers"):
            system.submit(
                format=ExportFormat.PNG,
                snapshot=_snapshot(),
                output_path=tmp_path / "out.png",
                split_gcode_layers=True,
                output_size=(100, 80),
            )
        assert not tuple(tmp_path.glob(".*.export-*"))
    finally:
        system.close()


def test_default_backend_delegates_encode_and_publish_to_capture_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    output_path = tmp_path / "frame.gcode"
    staging_dir = tmp_path / ".capture.export-1-test"
    staged_path = staging_dir / output_path.name
    manifest_path = capture_manifest_path_for(output_path)
    calls: list[tuple[str, object]] = []

    class CaptureServiceSpy:
        def encode(self, frame: object, path: object, **kwargs: object) -> tuple[Path, ...]:
            assert frame is snapshot
            assert Path(cast(Any, path)) == staged_path
            assert kwargs["format"] is ExportFormat.GCODE
            calls.append(("encode", path))
            return (staged_path,)

        def publish_staged_with_retry(
            self,
            frame: object,
            path: object,
            staged_paths: object,
            **kwargs: object,
        ) -> SimpleNamespace:
            assert frame is snapshot
            assert Path(cast(Any, path)) == output_path
            assert tuple(cast(Any, staged_paths)) == (staged_path,)
            assert kwargs == {
                "initial_path": output_path,
                "format": ExportFormat.GCODE,
                "split_gcode_layers": False,
                "output_size": None,
            }
            calls.append(("publish", path))
            return SimpleNamespace(
                artifact_paths=(output_path,),
                manifest_path=manifest_path,
            )

    monkeypatch.setattr(export_job_system, "_CAPTURE_SERVICE", CaptureServiceSpy())
    job = ExportJob(
        job_id=1,
        format=ExportFormat.GCODE,
        snapshot=snapshot,
        output_path=output_path,
        timeout_s=1.0,
        staging_dir=staging_dir,
    )

    staged = export_job_system._execute_export_job(job)
    committed = export_job_system._commit_staged_outputs(job, staged)

    assert committed == ((output_path,), manifest_path)
    assert calls == [("encode", staged_path), ("publish", output_path)]


def test_two_second_backend_does_not_block_frame_polling(tmp_path: Path) -> None:
    system = ExportJobSystem(backend=_two_second_backend, default_timeout_s=5.0)
    try:
        job = system.submit(
            format=ExportFormat.PNG,
            snapshot=_snapshot(),
            output_path=tmp_path / "slow.png",
            output_size=(100, 80),
        )

        frame_ticks = 0
        early_results: list[ExportJobResult] = []
        frame_deadline = time.monotonic() + 0.3
        while time.monotonic() < frame_deadline:
            before_poll = time.monotonic()
            early_results.extend(system.poll())
            assert time.monotonic() - before_poll < 0.1
            frame_ticks += 1
            time.sleep(0.01)

        assert frame_ticks >= 20
        assert all(result.job_id != job.job_id for result in early_results)
        result = _wait_for_job(system, job.job_id, collected=early_results)
        assert result.status is ExportJobStatus.SUCCESS
        assert result.paths == (job.output_path,)
        assert job.output_path.read_bytes() == b"export"
        assert not job.staging_dir.exists()
    finally:
        system.close()


def test_repeated_submit_uses_a_bounded_fifo_without_replacing_jobs(
    tmp_path: Path,
) -> None:
    system = ExportJobSystem(
        backend=_bounded_backend,
        default_timeout_s=5.0,
        runtime_limits=RuntimeLimits(capture_queue_pending_jobs=3),
    )
    jobs: list[ExportJob] = []
    collected: list[ExportJobResult] = []
    try:
        for index in range(4):
            job = system.submit(
                format=ExportFormat.PNG,
                snapshot=_snapshot(),
                output_path=tmp_path / f"frame_{index}.png",
                output_size=(100, 80),
            )
            jobs.append(job)

        assert system.in_flight_job == jobs[0]
        assert system.pending_job == jobs[1]
        assert system.pending_job_count == 3
        assert system.can_submit is False
        assert system.has_work is True
        with pytest.raises(ExportQueueFullError, match="満杯"):
            system.submit(
                format=ExportFormat.PNG,
                snapshot=_snapshot(),
                output_path=tmp_path / "rejected.png",
                output_size=(100, 80),
            )

        last_result = _wait_for_job(system, jobs[-1].job_id, collected=collected)
        assert last_result.status is ExportJobStatus.SUCCESS
        assert [
            result.job_id
            for result in collected
            if result.status is ExportJobStatus.SUCCESS
        ] == [job.job_id for job in jobs]
        assert system.has_work is False
    finally:
        system.close()


def test_capture_queue_enforces_aggregate_bytes_and_shares_same_snapshot(
    tmp_path: Path,
) -> None:
    snapshot = _sized_snapshot(80)
    another_snapshot = _sized_snapshot(80)
    system = ExportJobSystem(
        backend=_bounded_backend,
        default_timeout_s=5.0,
        runtime_limits=RuntimeLimits(
            capture_queue_pending_jobs=3,
            # raw geometry 80 bytes x (parent + serialization + worker) 3 copies。
            capture_queue_bytes=300,
        ),
    )
    try:
        system.submit(
            format=ExportFormat.GCODE,
            snapshot=snapshot,
            output_path=tmp_path / "one.gcode",
        )
        # 同じ immutable snapshot の連続 capture は親 geometry 参照を共有する。
        system.submit(
            format=ExportFormat.PNG,
            snapshot=snapshot,
            output_path=tmp_path / "two.png",
            output_size=(100, 80),
        )

        assert system.queue_status.request_count == 2
        assert system.queue_status.retained_bytes == 240
        with pytest.raises(ExportQueueFullError) as exc_info:
            system.submit(
                format=ExportFormat.GCODE,
                snapshot=another_snapshot,
                output_path=tmp_path / "rejected.gcode",
            )

        error = exc_info.value
        assert error.reason == "bytes"
        assert error.retained_bytes == 240
        assert error.requested_bytes == 240
        assert error.byte_limit == 300
        assert "requests=2/4" in str(error)
        assert not (tmp_path / "rejected.gcode").exists()

        assert system.cancel()
        assert system.retained_bytes == 0
        assert system.request_count == 0
    finally:
        system.close()


def test_capture_queue_releases_byte_budget_after_success(tmp_path: Path) -> None:
    first_snapshot = _sized_snapshot(80)
    second_snapshot = _sized_snapshot(80)
    system = ExportJobSystem(
        backend=_success_backend,
        default_timeout_s=5.0,
        runtime_limits=RuntimeLimits(
            capture_queue_pending_jobs=0,
            capture_queue_bytes=240,
        ),
    )
    try:
        first = system.submit(
            format=ExportFormat.GCODE,
            snapshot=first_snapshot,
            output_path=tmp_path / "first.gcode",
        )
        assert system.retained_bytes == 240
        assert _wait_for_job(system, first.job_id).status is ExportJobStatus.SUCCESS
        assert system.retained_bytes == 0
        assert system.request_count == 0

        # 終端 result 後は以前の geometry が budget を占有し続けず、
        # 同じ上限の別 snapshot を受理できる。
        second = system.submit(
            format=ExportFormat.GCODE,
            snapshot=second_snapshot,
            output_path=tmp_path / "second.gcode",
        )
        assert _wait_for_job(system, second.job_id).status is ExportJobStatus.SUCCESS
        assert system.retained_bytes == 0
        assert system.request_count == 0
    finally:
        system.close()


def test_cancel_rejects_implicit_job_id_coercion() -> None:
    system = ExportJobSystem(backend=_success_backend)
    try:
        for job_id, error in (
            (True, TypeError),
            (1.0, TypeError),
            ("1", TypeError),
            (0, ValueError),
        ):
            with pytest.raises(error, match="job_id"):
                system.cancel(job_id)  # type: ignore[arg-type]
    finally:
        system.close()


def test_backend_error_is_reported_and_worker_remains_usable(tmp_path: Path) -> None:
    system = ExportJobSystem(backend=_conditional_backend)
    try:
        failed = system.submit(
            format=ExportFormat.GCODE,
            snapshot=_snapshot(),
            output_path=tmp_path / "fail.gcode",
        )
        failed_result = _wait_for_job(system, failed.job_id)
        assert failed_result.status is ExportJobStatus.ERROR
        assert "backend failure" in (failed_result.error or "")

        succeeded = system.submit(
            format=ExportFormat.GCODE,
            snapshot=_snapshot(),
            output_path=tmp_path / "ok.gcode",
        )
        assert _wait_for_job(system, succeeded.job_id).status is ExportJobStatus.SUCCESS
    finally:
        system.close()


def test_custom_backend_cannot_return_a_path_outside_its_staging_directory(
    tmp_path: Path,
) -> None:
    system = ExportJobSystem(backend=_outside_staging_backend)
    try:
        job = system.submit(
            format=ExportFormat.GCODE,
            snapshot=_snapshot(),
            output_path=tmp_path / "outside.gcode",
        )

        result = _wait_for_job(system, job.job_id)

        assert result.status is ExportJobStatus.ERROR
        assert "staging directory 内の path" in (result.error or "")
        assert not job.output_path.exists()
        assert not job.staging_dir.exists()
    finally:
        system.close()


def test_default_worker_exports_gcode(tmp_path: Path) -> None:
    system = ExportJobSystem()
    try:
        output_path = tmp_path / "frame.gcode"
        job = system.submit(
            format=ExportFormat.GCODE,
            snapshot=_snapshot(),
            output_path=output_path,
        )

        result = _wait_for_job(system, job.job_id)
        assert result.status is ExportJobStatus.SUCCESS
        assert result.paths == (output_path,)
        assert result.manifest_path == capture_manifest_path_for(output_path)
        assert output_path.is_file()
        assert result.manifest_path.is_file()
    finally:
        system.close()


def test_default_worker_uses_parent_gcode_params_recorded_in_manifest(
    tmp_path: Path,
) -> None:
    """spawn worker が独自 config を再探索せず、親 snapshot の設定を使う。"""

    gcode_params = replace(
        runtime_config().gcode,
        z_up=17.0,
        decimals=1,
    )
    effective_config = replace(runtime_config(), gcode=gcode_params)

    def draw(_t: float) -> tuple[object, ...]:
        return ()

    store = ParamStore()
    provenance = CaptureProvenanceBuilder(
        draw,
        config=effective_config,
        parameter_source="code",
        parameter_store_path=None,
        parameter_load_provenance=store.load_provenance,
    ).frame(
        store,
        t=0.0,
        frame_index=0,
        quality="final",
        origin="interactive",
    )
    snapshot = CaptureExportSnapshot(
        layers=(),
        canvas_size=(100, 80),
        background_color_rgb01=(1.0, 1.0, 1.0),
        t=0.0,
        provenance=provenance,
        gcode_params=gcode_params,
    )
    system = ExportJobSystem()
    try:
        output_path = tmp_path / "parent-config.gcode"
        job = system.submit(
            format=ExportFormat.GCODE,
            snapshot=snapshot,
            output_path=output_path,
        )

        result = _wait_for_job(system, job.job_id)

        assert result.status is ExportJobStatus.SUCCESS
        assert "G1 Z37.0" in output_path.read_text(encoding="utf-8")
        assert result.manifest_path is not None
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        assert manifest["config"]["effective"]["gcode"]["z_up"] == 17.0
        assert manifest["config"]["effective"]["gcode"]["decimals"] == 1
    finally:
        system.close()


def test_default_worker_reports_png_backend_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    system = ExportJobSystem()
    try:
        output_path = tmp_path / "frame.png"
        svg_path = tmp_path / "frame.svg"
        svg_path.write_text("saved-by-s-key", encoding="utf-8")
        job = system.submit(
            format=ExportFormat.PNG,
            snapshot=_snapshot(),
            output_path=output_path,
            output_size=(100, 80),
        )

        result = _wait_for_job(system, job.job_id)
        assert result.status is ExportJobStatus.ERROR
        assert "resvg が見つかりません" in (result.error or "")
        assert svg_path.read_text(encoding="utf-8") == "saved-by-s-key"
        assert not output_path.exists()
    finally:
        system.close()


@pytest.mark.parametrize("raster_succeeds", [True, False])
def test_png_job_uses_private_svg_and_always_cleans_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raster_succeeds: bool,
) -> None:
    public_svg = tmp_path / "frame.svg"
    public_svg.write_text("saved-by-s-key", encoding="utf-8")
    intermediate_paths: list[Path] = []

    def fake_export_svg(*args: object, **kwargs: object) -> Path:
        svg_path = Path(args[1])
        intermediate_paths.append(svg_path)
        svg_path.write_text("png-intermediate", encoding="utf-8")
        return svg_path

    def fake_rasterize(svg_path: Path, png_path: Path, **kwargs: object) -> Path:
        assert Path(svg_path).read_text(encoding="utf-8") == "png-intermediate"
        if not raster_succeeds:
            raise RuntimeError("raster failed")
        Path(png_path).write_bytes(b"png")
        return Path(png_path)

    monkeypatch.setattr(capture_module, "export_svg", fake_export_svg)
    monkeypatch.setattr(capture_module, "rasterize_svg_to_png", fake_rasterize)
    job = ExportJob(
        job_id=1,
        format=ExportFormat.PNG,
        snapshot=_snapshot(),
        output_path=tmp_path / "frame.png",
        timeout_s=1.0,
        staging_dir=tmp_path / ".frame.export-1-test",
        output_size=(100, 80),
    )

    if raster_succeeds:
        assert export_job_system._execute_export_job(job) == (
            job.staging_dir / job.output_path.name,
        )
    else:
        with pytest.raises(RuntimeError, match="raster failed"):
            export_job_system._execute_export_job(job)

    assert len(intermediate_paths) == 1
    assert intermediate_paths[0] != public_svg
    assert not intermediate_paths[0].exists()
    assert not intermediate_paths[0].parent.exists()
    assert public_svg.read_text(encoding="utf-8") == "saved-by-s-key"


def test_timeout_cancels_job_and_restarts_worker(tmp_path: Path) -> None:
    system = ExportJobSystem(backend=_conditional_backend)
    try:
        timed_out = system.submit(
            format=ExportFormat.PNG,
            snapshot=_snapshot(),
            output_path=tmp_path / "slow.png",
            timeout_s=0.15,
            output_size=(100, 80),
        )
        timeout_result = _wait_for_job(system, timed_out.job_id)
        assert timeout_result.status is ExportJobStatus.TIMEOUT

        succeeded = system.submit(
            format=ExportFormat.PNG,
            snapshot=_snapshot(),
            output_path=tmp_path / "ok.png",
            output_size=(100, 80),
        )
        assert _wait_for_job(system, succeeded.job_id).status is ExportJobStatus.SUCCESS
    finally:
        system.close()


def test_cancel_in_flight_dispatches_pending_job(tmp_path: Path) -> None:
    system = ExportJobSystem(backend=_conditional_backend, default_timeout_s=5.0)
    try:
        slow = system.submit(
            format=ExportFormat.PNG,
            snapshot=_snapshot(),
            output_path=tmp_path / "slow.png",
            output_size=(100, 80),
        )
        pending = system.submit(
            format=ExportFormat.GCODE,
            snapshot=_snapshot(),
            output_path=tmp_path / "ok.gcode",
        )

        assert system.cancel(slow.job_id)
        results = system.poll()
        cancelled = next(result for result in results if result.job_id == slow.job_id)
        assert cancelled.status is ExportJobStatus.CANCELLED
        assert (
            _wait_for_job(system, pending.job_id, collected=results).status
            is ExportJobStatus.SUCCESS
        )
    finally:
        system.close()


def test_cancel_pending_job_removes_its_reserved_staging_directory(
    tmp_path: Path,
) -> None:
    system = ExportJobSystem(backend=_conditional_backend, default_timeout_s=5.0)
    try:
        in_flight = system.submit(
            format=ExportFormat.PNG,
            snapshot=_snapshot(),
            output_path=tmp_path / "slow.png",
            output_size=(100, 80),
        )
        pending = system.submit(
            format=ExportFormat.GCODE,
            snapshot=_snapshot(),
            output_path=tmp_path / "pending.gcode",
        )
        assert in_flight.staging_dir.is_dir()
        assert pending.staging_dir.is_dir()

        assert system.cancel(pending.job_id)
        result = next(
            result
            for result in system.poll()
            if result.job_id == pending.job_id
        )

        assert result.status is ExportJobStatus.CANCELLED
        assert not pending.staging_dir.exists()
        assert in_flight.staging_dir.exists()
    finally:
        system.close()


def test_worker_death_is_reported_and_recovered(tmp_path: Path) -> None:
    system = ExportJobSystem(backend=_conditional_backend)
    try:
        died = system.submit(
            format=ExportFormat.GCODE,
            snapshot=_snapshot(),
            output_path=tmp_path / "die.gcode",
        )
        death_result = _wait_for_job(system, died.job_id)
        assert death_result.status is ExportJobStatus.WORKER_DIED
        assert death_result.worker_exitcode == 7
        assert death_result.worker_pid is not None
        assert not died.staging_dir.exists()

        succeeded = system.submit(
            format=ExportFormat.GCODE,
            snapshot=_snapshot(),
            output_path=tmp_path / "ok.gcode",
        )
        assert _wait_for_job(system, succeeded.job_id).status is ExportJobStatus.SUCCESS
    finally:
        system.close()


def test_close_cancels_jobs_reaps_worker_and_is_idempotent(tmp_path: Path) -> None:
    system = ExportJobSystem(backend=_conditional_backend, default_timeout_s=5.0)
    in_flight = system.submit(
        format=ExportFormat.PNG,
        snapshot=_snapshot(),
        output_path=tmp_path / "slow.png",
        output_size=(100, 80),
    )
    pending = system.submit(
        format=ExportFormat.GCODE,
        snapshot=_snapshot(),
        output_path=tmp_path / "pending.gcode",
    )
    proc = system._proc
    proc_pid = None if proc is None else proc.pid
    assert in_flight.staging_dir.is_dir()
    assert pending.staging_dir.is_dir()

    system.close()
    system.close()

    results = system.poll()
    assert {result.job_id for result in results if result.status is ExportJobStatus.CANCELLED} == {
        in_flight.job_id,
        pending.job_id,
    }
    assert not in_flight.staging_dir.exists()
    assert not pending.staging_dir.exists()
    assert proc_pid is not None
    assert proc_pid not in {child.pid for child in mp.active_children()}
    with pytest.raises(RuntimeError, match="close 済み"):
        system.submit(
            format=ExportFormat.PNG,
            snapshot=_snapshot(),
            output_path=tmp_path / "after_close.png",
            output_size=(100, 80),
        )


def test_close_cleans_staging_even_when_queue_teardown_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = ExportJobSystem()
    real_close_queues = system._close_queues
    staging_dir = tmp_path / ".capture.export-1-test"
    staging_dir.mkdir()
    (staging_dir / "partial.gcode").write_text("partial", encoding="utf-8")
    job = ExportJob(
        job_id=1,
        format=ExportFormat.GCODE,
        snapshot=_snapshot(),
        output_path=tmp_path / "capture.gcode",
        timeout_s=1.0,
        staging_dir=staging_dir,
    )
    system._in_flight = job
    teardown_error = RuntimeError("queue teardown failed")

    def fail_queue_teardown(*, cancel_pending: bool) -> None:
        assert cancel_pending is True
        raise teardown_error

    monkeypatch.setattr(system, "_close_queues", fail_queue_teardown)
    try:
        with pytest.raises(RuntimeError, match="queue teardown failed") as exc_info:
            system.close()

        assert exc_info.value is teardown_error
        assert not staging_dir.exists()
        # close は既に terminal 状態のため、二回目は例外を再送出しない。
        system.close()
    finally:
        # fault injection で閉じなかった実 Queue は test 側で回収する。
        real_close_queues(cancel_pending=True)


def test_cancel_cleans_staging_even_when_worker_replacement_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = ExportJobSystem()
    staging_dir = tmp_path / ".capture.export-1-test"
    staging_dir.mkdir()
    (staging_dir / "partial.gcode").write_text("partial", encoding="utf-8")
    job = ExportJob(
        job_id=1,
        format=ExportFormat.GCODE,
        snapshot=_snapshot(),
        output_path=tmp_path / "capture.gcode",
        timeout_s=1.0,
        staging_dir=staging_dir,
    )
    system._in_flight = job
    replacement_error = RuntimeError("worker replacement failed")

    monkeypatch.setattr(system, "_service", lambda: None)

    def fail_worker_replacement() -> None:
        raise replacement_error

    monkeypatch.setattr(system, "_replace_worker", fail_worker_replacement)
    try:
        with pytest.raises(RuntimeError, match="worker replacement failed") as exc_info:
            system.cancel(job.job_id)

        assert exc_info.value is replacement_error
        assert not staging_dir.exists()
    finally:
        system._closed = True
        system._close_queues(cancel_pending=True)
