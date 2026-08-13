"""
Purpose:
    録画中のtransport、window制約、encoder staging、capture公開を一つのsessionとして所有する。
Use when:
    録画開始停止、frame時刻、resize防止、完成動画のpublish/recoveryを変更するとき。
Constraints:
    - 録画開始時にwindow寸法とtransportを固定し、失敗・停止時にも必ず復元を試みる。
    - freshかつ表示済みのframeだけを書き、失敗/stale frameでは録画clockを進めない。
    - provenanceは実際に書き込めた最初のfresh frameから録画世代へ固定する。
    - encode完了後のstagingを再encodeせず公開し、publish失敗時は回収可能なstagingを残す。
    - transportの不連続epochは開始成功時と終了復元時にだけ進める。
Side effects:
    window制約、transport、ffmpeg、filesystem上の動画世代を変更する。
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Protocol

from grafix.core.capture_provenance import CaptureProvenance
from grafix.core.lifecycle import CleanupErrors
from grafix.export.capture import CaptureService
from grafix.interactive.draw_window import (
    MINIMUM_DRAW_WINDOW_HEIGHT,
    MINIMUM_DRAW_WINDOW_WIDTH,
)
from grafix.interactive.runtime.recording_system import (
    StagedVideoCapture,
    VideoRecordingSystem,
)
from grafix.interactive.runtime.video_recorder import DEFAULT_VIDEO_FINALIZE_TIMEOUT_S
from grafix.interactive.transport import TransportClock


_logger = logging.getLogger(__name__)

# pyglet には maximum constraint を未設定へ戻す共通 API がない。通常の
# preview で到達しない十分大きな上限を、録画後の実質的な制約解除に使う。
_RESTORED_DRAW_WINDOW_MAX_SIZE = 1_000_000


class _RecordingWindow(Protocol):
    """録画 session が draw window に要求する最小契約。"""

    width: int
    height: int

    def get_framebuffer_size(self) -> tuple[int, int]: ...

    def set_minimum_size(self, width: int, height: int) -> None: ...

    def set_maximum_size(self, width: int, height: int) -> None: ...


class _RecordingSystem(Protocol):
    """録画 application owner が利用する encoder state machine 契約。"""

    @property
    def is_recording(self) -> bool: ...

    def t(self) -> float: ...

    def start(
        self,
        *,
        framebuffer_size: tuple[int, int],
        t0: float,
        output_path: Path,
    ) -> None: ...

    def write_frame(self, frame_rgb24: bytes) -> None: ...

    def pause_frame(self, error: str) -> None: ...

    def stop(
        self,
        *,
        timeout_s: float,
        stop_reason: str,
        abort_reason: str | None,
    ) -> StagedVideoCapture | None: ...


_FrameSectionFactory = Callable[[], AbstractContextManager[object]]
_ProvenanceFactory = Callable[[float], CaptureProvenance]


@dataclass(slots=True)
class _ActiveCapture:
    """録画開始時刻と、実際に書き込めた最初の frame provenance。"""

    t0: float
    provenance: CaptureProvenance | None = None


class RecordingSession:
    """録画に関わる transport、window、encode、publish を一体で所有する。

    ``DrawWindowSystem`` は start/stop と表示済み frame を渡す順序だけを配線し、
    fixed-fps timeline や completed staging の回収規則には触れない。
    """

    def __init__(
        self,
        *,
        fps: float,
        capture_service: CaptureService,
        output_path: Path,
        canvas_size: tuple[int, int],
        transport: TransportClock,
        window: _RecordingWindow,
        provenance_for_t: _ProvenanceFactory,
        frame_section: _FrameSectionFactory | None = None,
        recording_system: _RecordingSystem | None = None,
    ) -> None:
        if not isinstance(capture_service, CaptureService):
            raise TypeError("capture_service は CaptureService である必要があります")
        if not isinstance(output_path, Path):
            raise TypeError("output_path は Path である必要があります")
        if not callable(provenance_for_t):
            raise TypeError("provenance_for_t は callable である必要があります")
        if frame_section is not None and not callable(frame_section):
            raise TypeError("frame_section は callable である必要があります")

        self._recording: _RecordingSystem = (
            VideoRecordingSystem(fps=fps)
            if recording_system is None
            else recording_system
        )
        self._capture_service = capture_service
        self._output_path = output_path
        self._canvas_size = (int(canvas_size[0]), int(canvas_size[1]))
        self._transport = transport
        self._window = window
        self._provenance_for_t = provenance_for_t
        self._frame_section = nullcontext if frame_section is None else frame_section
        self._capture: _ActiveCapture | None = None
        self._preview_was_playing: bool | None = None
        self._window_constraints_locked = False

    @property
    def is_recording(self) -> bool:
        """encoder が frame を受け付ける録画中状態なら ``True``。"""

        return bool(self._recording.is_recording)

    @property
    def needs_first_provenance(self) -> bool:
        """次の fresh frame で録画 provenance を固定する必要があるか返す。"""

        capture = self._capture
        return bool(
            self._recording.is_recording
            and capture is not None
            and capture.provenance is None
        )

    def frame_time(self) -> float:
        """現在 frame の時刻を返し、録画中は preview transport へ連続同期する。"""

        if not self._recording.is_recording:
            return float(self._transport.t())
        t = float(self._recording.t())
        # seek() は epoch を進めるため、同一録画区間の mirror には synchronize を使う。
        self._transport.synchronize(t)
        return t

    def start(self) -> None:
        """window と transport を録画用に固定して encoder を開始する。"""

        if self._recording.is_recording:
            return
        was_playing = bool(self._transport.is_playing)
        try:
            self._lock_window_size()
            fb_w, fb_h = self._window.get_framebuffer_size()
            framebuffer_size = (int(fb_w), int(fb_h))
            self._transport.pause()
            t0 = float(self._transport.t())
            path = self._capture_service.reserve_path(self._output_path)
            self._recording.start(
                framebuffer_size=framebuffer_size,
                t0=t0,
                output_path=path,
            )
        except BaseException:
            if was_playing:
                try:
                    self._transport.play()
                except BaseException:
                    _logger.exception(
                        "Failed to restore transport after recording start failure"
                    )
            try:
                self._restore_window_constraints()
            except BaseException:
                _logger.exception(
                    "Failed to restore draw window constraints after recording start failure"
                )
            raise

        # fixed-fps timeline へ切り替わる不連続境界は、開始成功後に一度だけ進める。
        self._transport.mark_discontinuity()
        self._preview_was_playing = was_playing
        self._capture = _ActiveCapture(t0=t0)

    def record_presented_frame(
        self,
        *,
        fresh: bool,
        read_frame_rgb24: Callable[[], bytes],
        provenance: CaptureProvenance | None,
        error: str | None,
    ) -> None:
        """fresh frame は一度だけ書き、stale frame は clock を進めず記録する。"""

        if type(fresh) is not bool:
            raise TypeError("fresh は bool である必要があります")
        if not callable(read_frame_rgb24):
            raise TypeError("read_frame_rgb24 は callable である必要があります")
        if not self._recording.is_recording:
            return

        if not fresh:
            detail = error or "Scene evaluation did not produce a fresh frame"
            self._recording.pause_frame(detail)
            return

        capture = self._capture
        if capture is None:
            raise RuntimeError("録画中の capture metadata がありません")
        if capture.provenance is None and provenance is None:
            raise RuntimeError("録画の最初の fresh frame provenance がありません")

        with self._frame_section():
            frame_rgb24 = read_frame_rgb24()
            self._recording.write_frame(frame_rgb24)
        # write に成功した最初の frame だけを録画世代の provenance とする。
        if capture.provenance is None:
            capture.provenance = provenance

    def stop(
        self,
        *,
        timeout_s: float = DEFAULT_VIDEO_FINALIZE_TIMEOUT_S,
        stop_reason: str = "user_stop",
        abort_reason: str | None = None,
    ) -> None:
        """encoder を確定し、transport/window を復元して一世代を公開する。"""

        capture = self._capture
        was_playing = bool(self._preview_was_playing)
        end_t = (
            float(self._recording.t())
            if self._recording.is_recording
            else float(self._transport.t())
        )
        staged_capture: StagedVideoCapture | None = None
        errors = CleanupErrors(report_secondary=_logger.exception)
        try:
            staged_capture = self._recording.stop(
                timeout_s=timeout_s,
                stop_reason=stop_reason,
                abort_reason=abort_reason,
            )
        except BaseException as exc:
            errors.record(exc)

        self._capture = None
        self._preview_was_playing = None

        def restore_transport() -> None:
            # seek は録画終了という不連続境界の epoch も一度進める。
            self._transport.seek(end_t)
            if was_playing:
                self._transport.play()

        errors.attempt(
            restore_transport,
            "Failed to restore transport after recording stop failure",
        )
        errors.attempt(
            self._restore_window_constraints,
            "Failed to restore draw window constraints after recording stop failure",
        )
        if staged_capture is None:
            errors.raise_if_any()
            return
        if capture is None:
            recovery = Path(staged_capture.staging_path)
            raise RuntimeError(
                "録画metadataが失われたため公開できません。完成動画は回収可能です: "
                f"recovery={recovery}"
            )

        self._publish_completed_capture(staged_capture, capture=capture)
        errors.raise_if_any()

    def close(
        self,
        *,
        timeout_s: float = DEFAULT_VIDEO_FINALIZE_TIMEOUT_S,
        stop_reason: str = "shutdown",
    ) -> None:
        """active な録画があれば completed staging まで確定する。"""

        if self._recording.is_recording:
            self.stop(timeout_s=timeout_s, stop_reason=stop_reason)

    def _lock_window_size(self) -> None:
        """録画開始時の logical size で draw window を固定する。"""

        width = max(1, int(self._window.width))
        height = max(1, int(self._window.height))
        self._window_constraints_locked = True
        try:
            self._window.set_minimum_size(width, height)
            self._window.set_maximum_size(width, height)
        except BaseException:
            try:
                self._restore_window_constraints()
            except BaseException:
                _logger.exception(
                    "Failed to restore partially applied recording window constraints"
                )
            raise

    def _restore_window_constraints(self) -> None:
        """録画用の固定サイズ制約を通常 preview 用へ戻す。"""

        if not self._window_constraints_locked:
            return
        errors = CleanupErrors()
        # maximum を先に緩め、minimum 復元との中間状態で platform resize を避ける。
        errors.attempt(
            lambda: self._window.set_maximum_size(
                _RESTORED_DRAW_WINDOW_MAX_SIZE,
                _RESTORED_DRAW_WINDOW_MAX_SIZE,
            )
        )
        errors.attempt(
            lambda: self._window.set_minimum_size(
                MINIMUM_DRAW_WINDOW_WIDTH,
                MINIMUM_DRAW_WINDOW_HEIGHT,
            )
        )
        # 復元に失敗した場合、次の stop/close で再試行できるよう locked を保つ。
        errors.raise_if_any()
        self._window_constraints_locked = False

    def _publish_completed_capture(
        self,
        staged_capture: StagedVideoCapture,
        *,
        capture: _ActiveCapture,
    ) -> None:
        """encode 済み staging を再 encode せず artifact+manifest として公開する。"""

        staging_path = Path(staged_capture.staging_path)
        candidate = Path(staged_capture.output_path)
        provenance = capture.provenance
        if provenance is None:
            provenance = self._provenance_for_t(float(capture.t0))

        published = False
        try:
            try:
                generation = self._capture_service.publish_recording_staged_with_retry(
                    staging_path,
                    self._output_path,
                    initial_path=candidate,
                    t=float(capture.t0),
                    canvas_size=self._canvas_size,
                    provenance=provenance,
                    output_size=staged_capture.framebuffer_size,
                    recording=staged_capture.recording,
                )
            except FileExistsError as exc:
                raise FileExistsError(f"{exc}; recovery={staging_path}") from exc
            candidate = generation.artifact_paths[0]
            published = True
            print(f"Saved video: {candidate}")
        except BaseException:
            # completed staging を残し、再 encode せず recovery できるようにする。
            _logger.exception(
                "Video capture publish failed; completed staging retained: %s",
                staging_path,
            )
            raise
        finally:
            if published:
                try:
                    staging_path.unlink(missing_ok=True)
                except OSError:
                    # generation は fsync 済みで公開済み。cleanup failure は成功を覆さない。
                    _logger.exception(
                        "Failed to remove published video staging: %s",
                        staging_path,
                    )


__all__ = ["RecordingSession"]
