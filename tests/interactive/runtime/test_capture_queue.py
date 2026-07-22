from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from grafix.core.export_format import ExportFormat
from grafix.core.parameters import ParamStore
from grafix.runtime_config_loader import runtime_config
from grafix.core.runtime_limits import RuntimeLimits
from grafix.export.capture import CaptureService
from grafix.export.capture_provenance import CaptureProvenanceBuilder
from grafix.export.output_paths import VersionedPathAllocator
from grafix.interactive.diagnostics import DiagnosticEvent
from grafix.interactive.runtime.capture_queue import CaptureQueue
from grafix.interactive.runtime.export_job_system import (
    CaptureExportSnapshot,
    ExportJobResult,
    ExportJobStatus,
    ExportQueueFullError,
    ExportQueueStatus,
    FrameExportSnapshot,
)


def _draw(_t: float) -> list[object]:
    return []


def _snapshot(t: float = 2.5) -> CaptureExportSnapshot:
    store = ParamStore()
    provenance = CaptureProvenanceBuilder(
        _draw,
        config=runtime_config(),
        parameter_source="code",
        parameter_store_path=None,
        parameter_load_provenance=store.load_provenance,
        seed=1847,
    ).frame(
        store,
        t=t,
        frame_index=0,
        quality="final",
        origin="interactive",
    )
    return CaptureExportSnapshot(
        layers=(),
        canvas_size=(100, 80),
        background_color_rgb01=(1.0, 1.0, 1.0),
        t=t,
        provenance=provenance,
        gcode_params=runtime_config().gcode,
    )


class _Jobs:
    def __init__(self) -> None:
        self.submissions: list[dict[str, object]] = []
        self.results: list[ExportJobResult] = []
        self.rejection: ExportQueueFullError | None = None
        self.cancel_calls = 0
        self.close_calls = 0
        self.stuck = False

    @property
    def queue_status(self) -> ExportQueueStatus:
        return ExportQueueStatus(
            request_count=len(self.submissions),
            request_limit=17,
            retained_bytes=0,
            byte_limit=1024,
        )

    @property
    def has_work(self) -> bool:
        return self.stuck

    def ensure_can_submit(self, _snapshot: FrameExportSnapshot) -> None:
        if self.rejection is not None:
            raise self.rejection

    def submit(self, **kwargs: object) -> object:
        self.submissions.append(dict(kwargs))
        return SimpleNamespace(job_id=len(self.submissions))

    def poll(self) -> list[ExportJobResult]:
        results, self.results = self.results, []
        return results

    def cancel(self, _job_id: int | None = None) -> bool:
        self.cancel_calls += 1
        self.stuck = False
        return True

    def close(self) -> None:
        self.close_calls += 1


class _Monitor:
    def __init__(self) -> None:
        self.queue_updates: list[dict[str, object]] = []
        self.diagnostics: list[DiagnosticEvent] = []

    def set_capture_queue(self, **kwargs: object) -> None:
        self.queue_updates.append(dict(kwargs))

    def publish_diagnostic(self, event: DiagnosticEvent) -> DiagnosticEvent:
        self.diagnostics.append(event)
        return event


def _queue(
    tmp_path: Path,
    *,
    jobs: object,
    current: list[FrameExportSnapshot | None],
    final: Sequence[FrameExportSnapshot],
    monitor: _Monitor | None = None,
    output: list[str] | None = None,
) -> CaptureQueue:
    service = CaptureService(path_allocator=VersionedPathAllocator())
    messages = [] if output is None else output
    return CaptureQueue(
        capture_service=service,
        runtime_limits=RuntimeLimits(),
        svg_output_path=tmp_path / "piece.svg",
        png_output_path=tmp_path / "piece.png",
        gcode_output_path=tmp_path / "piece.gcode",
        png_scale=2.0,
        current_snapshot=lambda: current[0],
        capture_current_frame=lambda: final[0],
        materialize_snapshot=lambda snapshot: snapshot,
        shutdown_snapshot=lambda: final[0],
        monitor=monitor,
        export_jobs=cast(Any, jobs),
        announce=messages.append,
        poll_interval_s=0.0,
    )


def test_capture_intent_fifo_trace_matches_the_coordinator_contract(
    tmp_path: Path,
) -> None:
    """分割前の observable trace: intent は置換せず同一 frame へ FIFO 結合する。"""

    snapshot = _snapshot()
    jobs = _Jobs()
    monitor = _Monitor()
    output: list[str] = []
    queue = _queue(
        tmp_path,
        jobs=jobs,
        current=[None],
        final=[snapshot],
        monitor=monitor,
        output=output,
    )

    assert queue.request(ExportFormat.PNG)
    assert queue.request(ExportFormat.GCODE)
    assert queue.request(ExportFormat.PNG)
    assert queue.pending_count == 3
    assert jobs.submissions == []

    assert queue.bind_presented_frame(snapshot) == 3

    assert [item["format"] for item in jobs.submissions] == [
        ExportFormat.PNG,
        ExportFormat.GCODE,
        ExportFormat.PNG,
    ]
    assert [item["snapshot"] for item in jobs.submissions] == [snapshot] * 3
    assert [item["output_path"] for item in jobs.submissions] == [
        tmp_path / "piece.png",
        tmp_path / "piece.gcode",
        tmp_path / "piece_001.png",
    ]
    assert output == [
        f"Exporting PNG: {tmp_path / 'piece.png'}",
        f"Exporting G-code: {tmp_path / 'piece.gcode'}",
        f"Exporting PNG: {tmp_path / 'piece_001.png'}",
    ]
    assert monitor.queue_updates[0]["request_count"] == 1
    assert monitor.queue_updates[-1]["request_count"] == 3


def test_post_frame_request_freezes_final_snapshot_at_request_time(
    tmp_path: Path,
) -> None:
    visible = _snapshot(1.25)
    later = _snapshot(9.0)
    jobs = _Jobs()
    current: list[FrameExportSnapshot | None] = [visible]
    final = [visible]
    queue = _queue(tmp_path, jobs=jobs, current=current, final=final)

    assert queue.request(ExportFormat.PNG)
    current[0] = later
    final[0] = later

    assert jobs.submissions[0]["snapshot"] is visible
    assert not queue.has_pending_intents


def test_byte_rejection_happens_before_path_reservation(tmp_path: Path) -> None:
    snapshot = _snapshot()
    jobs = _Jobs()
    jobs.rejection = ExportQueueFullError(
        reason="bytes",
        request_count=0,
        request_limit=17,
        retained_bytes=900,
        requested_bytes=200,
        byte_limit=1024,
    )
    current: list[FrameExportSnapshot | None] = [snapshot]
    output: list[str] = []
    queue = _queue(
        tmp_path,
        jobs=jobs,
        current=current,
        final=[snapshot],
        output=output,
    )

    assert not queue.request(ExportFormat.PNG)
    jobs.rejection = None
    assert queue.request(ExportFormat.PNG)

    assert jobs.submissions[0]["output_path"] == tmp_path / "piece.png"
    assert "reason=bytes" in output[0]


def test_worker_failure_is_published_and_queue_pressure_is_refreshed(
    tmp_path: Path,
) -> None:
    jobs = _Jobs()
    monitor = _Monitor()
    output: list[str] = []
    failed_path = tmp_path / "piece.png"
    jobs.results.append(
        ExportJobResult(
            job_id=1,
            format=ExportFormat.PNG,
            status=ExportJobStatus.ERROR,
            output_path=failed_path,
            error="resvg failed",
        )
    )
    queue = _queue(
        tmp_path,
        jobs=jobs,
        current=[None],
        final=[_snapshot()],
        monitor=monitor,
        output=output,
    )

    results = queue.poll()

    assert results[0].status is ExportJobStatus.ERROR
    assert monitor.diagnostics[0].category == "export"
    assert monitor.diagnostics[0].source == str(failed_path)
    assert monitor.diagnostics[0].details == "resvg failed"
    assert "Failed to save PNG (error)" in output[0]
    assert monitor.queue_updates[-1]["request_count"] == 0


def test_shutdown_deadline_cancels_stuck_worker_explicitly(
    tmp_path: Path,
) -> None:
    jobs = _Jobs()
    jobs.stuck = True
    output: list[str] = []
    queue = _queue(
        tmp_path,
        jobs=jobs,
        current=[None],
        final=[_snapshot()],
        output=output,
    )

    assert queue.drain(timeout_s=0.0) is False
    assert jobs.cancel_calls == 1
    assert output == [
        "Capture shutdown deadline reached; cancelling remaining exports: unsubmitted=0, timeout=0s"
    ]


def test_close_drains_unbound_intents_before_closing_worker(
    tmp_path: Path,
) -> None:
    class DrainingJobs(_Jobs):
        @property
        def has_work(self) -> bool:
            return bool(self.submissions)

        def poll(self) -> list[ExportJobResult]:
            if not self.submissions:
                return []
            submission = self.submissions.pop(0)
            return [
                ExportJobResult(
                    job_id=1,
                    format=cast(ExportFormat, submission["format"]),
                    status=ExportJobStatus.SUCCESS,
                    output_path=cast(Path, submission["output_path"]),
                )
            ]

    snapshot = _snapshot()
    jobs = DrainingJobs()
    queue = _queue(
        tmp_path,
        jobs=jobs,
        current=[None],
        final=[snapshot],
    )
    assert queue.request(ExportFormat.PNG)
    assert queue.request(ExportFormat.GCODE)

    queue.close(timeout_s=1.0)
    queue.close(timeout_s=1.0)

    assert not queue.has_pending_intents
    assert jobs.close_calls == 1


def test_capture_intent_rejects_split_layers_for_non_gcode(tmp_path: Path) -> None:
    snapshot = _snapshot()
    queue = _queue(
        tmp_path,
        jobs=_Jobs(),
        current=[snapshot],
        final=[snapshot],
    )

    with pytest.raises(ValueError, match="G-code"):
        queue.request(ExportFormat.PNG, split_gcode_layers=True)
