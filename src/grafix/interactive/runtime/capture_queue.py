"""
Purpose:
    明示capture intentを表示済みframeへ束縛し、bounded FIFOと完了通知を一元管理する。
Use when:
    保存shortcut、first-frame binding、backpressure、shutdown drainを変更するとき。
Constraints:
    - 初回表示前のintentだけをboundedに保留し、同じ最初の表示frameへ束縛する。
    - 表示後のrequestはその時点のimmutable final snapshotを固定し、後のlive stateを参照しない。
    - accepted requestを暗黙置換せず、count/byte上限超過は明示的に拒否する。
    - provenance materializationと形式別保存はintentのFIFO順序を維持する。
    - closeは一つのdeadlineでdrainし、期限後だけ残件をcancelする。
Side effects:
    export worker、同期SVG保存、console/diagnostic/monitor通知を駆動する。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
import logging
from math import isfinite
from pathlib import Path
import time
import traceback
from typing import Protocol

from grafix.core.export_format import ExportFormat
from grafix.core.lifecycle import CleanupErrors
from grafix.core.runtime_limits import RuntimeLimits
from grafix.export.capture import CaptureService
from grafix.export.image import png_output_size
from grafix.interactive.diagnostics import (
    DiagnosticAction,
    DiagnosticEvent,
    DiagnosticSeverity,
)
from grafix.interactive.runtime.export_job_system import (
    CaptureExportSnapshot,
    ExportJobResult,
    ExportJobStatus,
    ExportJobSystem,
    ExportQueueFullError,
    ExportQueueStatus,
    FrameExportSnapshot,
)

DEFAULT_CAPTURE_SHUTDOWN_TIMEOUT_S = 30.0
_SHUTDOWN_POLL_INTERVAL_S = 0.01

_logger = logging.getLogger(__name__)


class _ExportJobs(Protocol):
    """CaptureQueue が使う ExportJobSystem の最小契約。"""

    @property
    def queue_status(self) -> ExportQueueStatus: ...

    @property
    def has_work(self) -> bool: ...

    def ensure_can_submit(self, snapshot: FrameExportSnapshot) -> None: ...

    def submit(self, **kwargs: object) -> object: ...

    def poll(self) -> list[ExportJobResult]: ...

    def cancel(self, job_id: int | None = None) -> bool: ...

    def close(self) -> None: ...


class CaptureQueueMonitor(Protocol):
    """RuntimeMonitor が満たす capture 通知先の構造的契約。"""

    def set_capture_queue(
        self,
        *,
        request_count: int,
        request_limit: int,
        retained_bytes: int,
        byte_limit: int,
        notice: str | None = None,
    ) -> None: ...

    def publish_diagnostic(self, event: DiagnosticEvent) -> DiagnosticEvent: ...


@dataclass(slots=True)
class _CaptureIntent:
    """一度の明示保存操作と、それが参照する immutable frame。"""

    format: ExportFormat
    split_gcode_layers: bool = False
    snapshot: FrameExportSnapshot | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.format, ExportFormat):
            raise TypeError("format は ExportFormat である必要があります")
        if type(self.split_gcode_layers) is not bool:
            raise TypeError("split_gcode_layers は bool である必要があります")
        if self.split_gcode_layers and self.format is not ExportFormat.GCODE:
            raise ValueError("split_gcode_layers は G-code export にのみ指定できます")


class CaptureQueue:
    """明示 capture を一つの FIFO/backpressure/lifecycle 契約で扱う。

    最初の表示 frame より前の key intent だけを小さな FIFO に保持する。表示後は
    final snapshot をその場で固定し、PNG/G-code は ``ExportJobSystem`` の count/byte
    admission へ直接渡す。SVG は同じ intent 順序と通知を共有しつつ同期保存する。
    """

    def __init__(
        self,
        *,
        capture_service: CaptureService,
        runtime_limits: RuntimeLimits,
        svg_output_path: Path,
        png_output_path: Path,
        gcode_output_path: Path,
        png_scale: float,
        current_snapshot: Callable[[], FrameExportSnapshot | None],
        capture_current_frame: Callable[[], FrameExportSnapshot],
        materialize_snapshot: Callable[[FrameExportSnapshot], FrameExportSnapshot],
        shutdown_snapshot: Callable[[], FrameExportSnapshot],
        monitor: CaptureQueueMonitor | None = None,
        export_jobs: _ExportJobs | None = None,
        announce: Callable[[str], object] = print,
        poll_interval_s: float = _SHUTDOWN_POLL_INTERVAL_S,
    ) -> None:
        if not isinstance(capture_service, CaptureService):
            raise TypeError("capture_service は CaptureService である必要があります")
        if not isinstance(runtime_limits, RuntimeLimits):
            raise TypeError("runtime_limits は RuntimeLimits である必要があります")
        interval = float(poll_interval_s)
        if not isfinite(interval) or interval < 0.0:
            raise ValueError("poll_interval_s は有限の 0 以上である必要があります")

        self._capture_service = capture_service
        self._svg_output_path = Path(svg_output_path)
        self._png_output_path = Path(png_output_path)
        self._gcode_output_path = Path(gcode_output_path)
        self._png_scale = float(png_scale)
        self._current_snapshot = current_snapshot
        self._capture_current_frame = capture_current_frame
        self._materialize_snapshot = materialize_snapshot
        self._shutdown_snapshot = shutdown_snapshot
        self._monitor = monitor
        self._announce = announce
        self._poll_interval_s = interval
        self._request_limit = int(runtime_limits.capture_queue_pending_jobs) + 1
        self._pending: deque[_CaptureIntent] = deque()
        self._notice: str | None = None
        self._closed = False
        self._jobs: ExportJobSystem | _ExportJobs = (
            ExportJobSystem(
                runtime_limits=runtime_limits,
                capture_service=capture_service,
            )
            if export_jobs is None
            else export_jobs
        )

    @property
    def has_pending_intents(self) -> bool:
        """最初の表示 frame への結合を待つ intent があるか返す。"""

        return bool(self._pending)

    @property
    def has_unbound_intents(self) -> bool:
        """まだ frame snapshot を持たない intent があるか返す。"""

        return any(intent.snapshot is None for intent in self._pending)

    @property
    def pending_count(self) -> int:
        """最初の表示 frame への結合を待つ intent 数を返す。"""

        return len(self._pending)

    @staticmethod
    def export_label(
        format: ExportFormat,
        split_gcode_layers: bool = False,
    ) -> str:
        """console/diagnostic で使う形式名を返す。"""

        if format is ExportFormat.SVG:
            return "SVG"
        if format is ExportFormat.PNG:
            return "PNG"
        if split_gcode_layers:
            return "G-code layers"
        return "G-code"

    def request(
        self,
        format: ExportFormat,
        *,
        split_gcode_layers: bool = False,
    ) -> bool:
        """一度の明示 capture を受け付け、受理できたか返す。"""

        if self._closed:
            raise RuntimeError("CaptureQueue は close 済みです")
        intent = _CaptureIntent(
            format=format,
            split_gcode_layers=split_gcode_layers,
        )
        if self._current_snapshot() is not None:
            try:
                intent.snapshot = self._capture_current_frame()
            except Exception as exc:
                self._publish_diagnostic(
                    summary=(
                        "Final capture evaluation failed: "
                        f"{self.export_label(format, split_gcode_layers)}"
                    ),
                    details="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                    severity="error",
                )
                return False
            return self._submit(intent)

        if len(self._pending) >= self._request_limit:
            label = self.export_label(format, split_gcode_layers)
            notice = (
                f"Capture rejected: {label}; before-first-frame requests="
                f"{len(self._pending)}/{self._request_limit}"
            )
            self._notice = notice
            self._announce(notice)
            self._publish_diagnostic(
                summary=notice,
                details=notice,
                severity="warning",
            )
            self._update_monitor()
            return False

        self._pending.append(intent)
        self._update_monitor()
        return True

    def save_svg(self, snapshot: FrameExportSnapshot) -> Path:
        """指定 snapshot を provenance 付き SVG generation として同期保存する。"""

        captured = self._capture_snapshot(snapshot)
        return self._capture_service.export(
            captured,
            self._svg_output_path,
        ).path

    def bind_presented_frame(self, snapshot: FrameExportSnapshot) -> int:
        """未結合 intent を同じ最初の表示 frame へ固定し、FIFO 順に投入する。"""

        if not self._pending:
            return 0
        bound_snapshot: FrameExportSnapshot = snapshot
        if self.has_unbound_intents:
            bound_snapshot = self._capture_snapshot(snapshot)
        for intent in self._pending:
            if intent.snapshot is None:
                intent.snapshot = bound_snapshot

        accepted = 0
        while self._pending:
            accepted += int(self._submit(self._pending.popleft()))
        self._update_monitor()
        return accepted

    def poll(self) -> tuple[ExportJobResult, ...]:
        """worker の終端結果を回収し、console/diagnostic/monitor へ通知する。"""

        results = tuple(self._jobs.poll())
        for result in results:
            label = self.export_label(result.format, result.split_gcode_layers)
            if result.status is ExportJobStatus.SUCCESS:
                if result.split_gcode_layers and not result.paths:
                    self._announce("No layers to export")
                for path in result.paths:
                    self._announce(f"Saved {label}: {path}")
                continue
            if result.status is ExportJobStatus.CANCELLED:
                self._announce(f"Cancelled {label}: {result.output_path}")
                self._publish_diagnostic(
                    summary=f"Cancelled {label} export",
                    details=f"output_path={result.output_path}",
                    severity="info",
                    source=result.output_path,
                )
                continue
            _logger.error(
                "Failed to save %s (%s): %s",
                label,
                result.status.value,
                result.output_path,
            )
            self._announce(
                f"Failed to save {label} ({result.status.value}): "
                f"{result.output_path}\n{result.error or ''}"
            )
            self._publish_diagnostic(
                summary=f"Failed to save {label} ({result.status.value})",
                details=result.error or "export worker returned no error details",
                severity="error",
                source=result.output_path,
            )
        self._update_monitor()
        return results

    def drain(self, *, timeout_s: float = DEFAULT_CAPTURE_SHUTDOWN_TIMEOUT_S) -> bool:
        """accepted capture を一つの deadline まで drain する。"""

        timeout = float(timeout_s)
        if not isfinite(timeout) or timeout < 0.0:
            raise ValueError("timeout_s は有限の 0 以上である必要があります")
        deadline = time.monotonic() + timeout

        shutdown_snapshot: FrameExportSnapshot | None = None
        if self.has_unbound_intents:
            shutdown_snapshot = self._shutdown_snapshot()
        elif self._pending:
            shutdown_snapshot = self._pending[0].snapshot

        while True:
            self.poll()
            if self._pending:
                assert shutdown_snapshot is not None
                self.bind_presented_frame(shutdown_snapshot)
            if not self._pending and not self._jobs.has_work:
                return True
            if time.monotonic() >= deadline:
                unsubmitted = len(self._pending)
                self._pending.clear()
                notice = (
                    "Capture shutdown deadline reached; cancelling remaining exports: "
                    f"unsubmitted={unsubmitted}, timeout={timeout:g}s"
                )
                self._notice = notice
                self._announce(notice)
                self._jobs.cancel()
                self.poll()
                self._update_monitor()
                return False
            time.sleep(self._poll_interval_s)

    def close(self, *, timeout_s: float = DEFAULT_CAPTURE_SHUTDOWN_TIMEOUT_S) -> None:
        """drain、worker close、終端通知を順に試し、所有 resource を閉じる。"""

        if self._closed:
            return
        errors = CleanupErrors()
        errors.attempt(lambda: self.drain(timeout_s=timeout_s))
        errors.attempt(self._jobs.close)
        errors.attempt(self.poll)
        self._closed = True
        errors.raise_if_any()

    def _capture_snapshot(
        self,
        snapshot: FrameExportSnapshot,
    ) -> CaptureExportSnapshot:
        if snapshot.provenance is None:
            snapshot = self._materialize_snapshot(snapshot)
        return CaptureExportSnapshot.from_snapshot(snapshot)

    def _submit(self, intent: _CaptureIntent) -> bool:
        snapshot = intent.snapshot
        assert snapshot is not None
        captured = self._capture_snapshot(snapshot)
        intent.snapshot = captured

        if intent.format is ExportFormat.SVG:
            try:
                path = self.save_svg(captured)
            except Exception as exc:
                self._publish_diagnostic(
                    summary="Failed to save SVG",
                    details="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                    severity="error",
                    source=self._svg_output_path,
                )
                return False
            self._notice = None
            self._update_monitor()
            self._announce(f"Saved SVG: {path}")
            return True

        try:
            self._jobs.ensure_can_submit(captured)
        except ExportQueueFullError as exc:
            self._report_rejection(intent, exc)
            return False

        base_output_path = (
            self._png_output_path if intent.format is ExportFormat.PNG else self._gcode_output_path
        )
        output_path = self._capture_service.reserve_path(
            base_output_path,
            split_gcode_layers=intent.split_gcode_layers,
        )
        try:
            if intent.format is ExportFormat.PNG:
                job = self._jobs.submit(
                    format=intent.format,
                    snapshot=captured,
                    output_path=output_path,
                    base_output_path=base_output_path,
                    output_size=png_output_size(
                        captured.canvas_size,
                        scale=self._png_scale,
                    ),
                )
            else:
                job = self._jobs.submit(
                    format=intent.format,
                    snapshot=captured,
                    output_path=output_path,
                    base_output_path=base_output_path,
                    split_gcode_layers=intent.split_gcode_layers,
                )
        except ExportQueueFullError as exc:
            self._report_rejection(intent, exc)
            return False
        except Exception as exc:
            self._publish_diagnostic(
                summary=(
                    "Failed to start "
                    f"{self.export_label(intent.format, intent.split_gcode_layers)} "
                    "export"
                ),
                details="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                severity="error",
                source=output_path,
            )
            return False

        # ``job`` の lifetime は ExportJobSystem が所有する。ここでは submission が
        # 成功した事実だけを通知し、完了時の情報は poll result から読む。
        del job
        self._notice = None
        self._update_monitor()
        if intent.format is ExportFormat.PNG:
            self._announce(f"Exporting PNG: {output_path}")
        elif intent.split_gcode_layers:
            self._announce(f"Exporting G-code per layer: {output_path.parent}")
        else:
            self._announce(f"Exporting G-code: {output_path}")
        return True

    def _report_rejection(
        self,
        intent: _CaptureIntent,
        error: ExportQueueFullError | None = None,
    ) -> None:
        detail = (
            str(error)
            if error is not None
            else "capture queue rejected: no admission slot available"
        )
        notice = (
            "Capture rejected: "
            f"{self.export_label(intent.format, intent.split_gcode_layers)}; {detail}"
        )
        self._notice = notice
        self._announce(notice)
        self._publish_diagnostic(
            summary=notice,
            details=detail,
            severity="warning",
        )
        self._update_monitor()

    def _update_monitor(self) -> None:
        monitor = self._monitor
        if monitor is None:
            return
        status = self._jobs.queue_status
        monitor.set_capture_queue(
            request_count=int(status.request_count) + len(self._pending),
            request_limit=int(status.request_limit),
            retained_bytes=int(status.retained_bytes),
            byte_limit=int(status.byte_limit),
            notice=self._notice,
        )

    def _publish_diagnostic(
        self,
        *,
        summary: str,
        details: str,
        severity: DiagnosticSeverity,
        source: str | Path | None = None,
    ) -> None:
        monitor = self._monitor
        if monitor is None:
            return
        monitor.publish_diagnostic(
            DiagnosticEvent(
                category="export",
                severity=severity,
                summary=summary,
                details=details,
                source=None if source is None else str(source),
                actions=(DiagnosticAction("copy", "Copy details"),),
                dedupe_key=f"export:{summary}:{source}",
            )
        )


__all__ = [
    "CaptureQueue",
    "CaptureQueueMonitor",
    "DEFAULT_CAPTURE_SHUTDOWN_TIMEOUT_S",
]
