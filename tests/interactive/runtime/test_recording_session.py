from __future__ import annotations

# ruff: noqa: E402 -- pyglet option must be set before importing RecordingSession.

from collections.abc import Iterator
from contextlib import contextmanager
import json
from pathlib import Path

import pyglet
import pytest

pyglet.options["shadow_window"] = False

from grafix.core.capture_manifest import RecordingManifest
from grafix.core.capture_provenance import CaptureProvenance
from grafix.core.parameters import ParamStore
from grafix.runtime_config_loader import runtime_config
from grafix.export.capture import CaptureService
from grafix.export.capture_provenance import CaptureProvenanceBuilder
from grafix.export.capture_publish import (
    PublishedCaptureGeneration,
    capture_manifest_path_for,
)
from grafix.export.output_paths import VersionedPathAllocator
import grafix.interactive.runtime.recording_session as recording_session_module
from grafix.interactive.runtime.recording_session import RecordingSession
from grafix.interactive.runtime.recording_system import StagedVideoCapture
from grafix.interactive.transport import TransportClock


def _draw(_t: float) -> tuple[object, ...]:
    return ()


_PROVENANCE_STORE = ParamStore()
_PROVENANCE_BUILDER = CaptureProvenanceBuilder(
    _draw,
    config=runtime_config(),
    parameter_source="code",
    parameter_store_path=None,
    parameter_load_provenance="primary",
    seed=1847,
)


def _provenance(t: float) -> CaptureProvenance:
    return _PROVENANCE_BUILDER.frame(
        _PROVENANCE_STORE,
        t=float(t),
        frame_index=0,
        quality="final",
        origin="interactive",
    )


class _Window:
    def __init__(self) -> None:
        self.width = 640
        self.height = 480
        self.framebuffer_size = (1280, 960)
        self.calls: list[tuple[str, int, int]] = []

    def get_framebuffer_size(self) -> tuple[int, int]:
        return self.framebuffer_size

    def set_minimum_size(self, width: int, height: int) -> None:
        self.calls.append(("minimum", int(width), int(height)))

    def set_maximum_size(self, width: int, height: int) -> None:
        self.calls.append(("maximum", int(width), int(height)))


class _Recording:
    def __init__(self, *, trace: list[str] | None = None) -> None:
        self.is_recording = False
        self.current_t = 0.0
        self.output_path: Path | None = None
        self.framebuffer_size = (0, 0)
        self.trace = [] if trace is None else trace
        self.stop_timeout_s: float | None = None
        self.stop_reason: str | None = None
        self.abort_reason: str | None = None

    def start(
        self,
        *,
        framebuffer_size: tuple[int, int],
        t0: float,
        output_path: Path,
    ) -> None:
        self.trace.append("start")
        self.framebuffer_size = framebuffer_size
        self.current_t = float(t0)
        self.output_path = output_path
        self.is_recording = True

    def t(self) -> float:
        return float(self.current_t)

    def write_frame(self, _frame_rgb24: bytes) -> None:
        self.trace.append("write")

    def pause_frame(self, error: str) -> None:
        self.trace.append(f"pause:{error}")

    def stop(
        self,
        *,
        timeout_s: float,
        stop_reason: str,
        abort_reason: str | None,
    ) -> StagedVideoCapture | None:
        self.trace.append("stop")
        self.stop_timeout_s = float(timeout_s)
        self.stop_reason = stop_reason
        self.abort_reason = abort_reason
        self.is_recording = False
        output_path = self.output_path
        if output_path is None:
            return None
        staging_path = output_path.with_name(f".{output_path.name}.recording")
        staging_path.write_bytes(b"encoded-once")
        return StagedVideoCapture(
            staging_path=staging_path,
            output_path=output_path,
            framebuffer_size=self.framebuffer_size,
            recording=RecordingManifest(
                fps=60.0,
                frame_count=1,
                stop_reason=stop_reason,
                abort_reason=abort_reason,
            ),
        )


def _session(
    tmp_path: Path,
    *,
    recording: _Recording,
    window: _Window | None = None,
    playing: bool = False,
    capture_service: CaptureService | None = None,
    provenance_calls: list[float] | None = None,
    frame_section: object | None = None,
) -> tuple[RecordingSession, TransportClock, _Window]:
    target_window = _Window() if window is None else window
    transport = TransportClock(
        start_time=10.0,
        time_source=lambda: 10.0,
        initial_t=2.0,
        playing=playing,
    )
    calls = [] if provenance_calls is None else provenance_calls

    def provenance_for_t(t: float) -> CaptureProvenance:
        calls.append(float(t))
        return _provenance(t)

    kwargs: dict[str, object] = {}
    if frame_section is not None:
        kwargs["frame_section"] = frame_section
    session = RecordingSession(
        fps=60.0,
        capture_service=(
            CaptureService(path_allocator=VersionedPathAllocator())
            if capture_service is None
            else capture_service
        ),
        output_path=tmp_path / "piece.mp4",
        canvas_size=(100, 80),
        transport=transport,
        window=target_window,
        provenance_for_t=provenance_for_t,
        recording_system=recording,
        **kwargs,  # type: ignore[arg-type]
    )
    return session, transport, target_window


def test_frame_call_trace_writes_only_fresh_frames_and_fixes_first_provenance(
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    recording = _Recording(trace=trace)

    @contextmanager
    def frame_section() -> Iterator[None]:
        trace.append("video-enter")
        try:
            yield
        finally:
            trace.append("video-exit")

    session, transport, _window = _session(
        tmp_path,
        recording=recording,
        frame_section=frame_section,
    )
    first = _provenance(2.0)

    def read_frame(label: str, payload: bytes) -> bytes:
        trace.append(label)
        return payload

    session.start()
    recording.current_t = 2.25
    assert session.frame_time() == pytest.approx(2.25)
    assert transport.t() == pytest.approx(2.25)
    assert transport.epoch == 1
    assert session.needs_first_provenance is True

    session.record_presented_frame(
        fresh=True,
        read_frame_rgb24=lambda: read_frame("read", b"frame-1"),
        provenance=first,
        error=None,
    )
    assert session.needs_first_provenance is False
    session.record_presented_frame(
        fresh=False,
        read_frame_rgb24=lambda: read_frame("unexpected-read", b"stale"),
        provenance=None,
        error="ValueError: broken scene",
    )
    session.record_presented_frame(
        fresh=True,
        read_frame_rgb24=lambda: read_frame("read", b"frame-2"),
        provenance=None,
        error=None,
    )

    assert trace == [
        "start",
        "video-enter",
        "read",
        "write",
        "video-exit",
        "pause:ValueError: broken scene",
        "video-enter",
        "read",
        "write",
        "video-exit",
    ]


def test_start_failure_restores_playing_transport_and_window_constraints(
    tmp_path: Path,
) -> None:
    class FailedRecording(_Recording):
        def start(self, **_kwargs: object) -> None:
            raise RuntimeError("encoder start failed")

    session, transport, window = _session(
        tmp_path,
        recording=FailedRecording(),
        playing=True,
    )

    with pytest.raises(RuntimeError, match="encoder start failed"):
        session.start()

    assert transport.is_playing is True
    assert transport.epoch == 0
    assert session.is_recording is False
    assert window.calls == [
        ("minimum", 640, 480),
        ("maximum", 640, 480),
        (
            "maximum",
            recording_session_module._RESTORED_DRAW_WINDOW_MAX_SIZE,
            recording_session_module._RESTORED_DRAW_WINDOW_MAX_SIZE,
        ),
        ("minimum", 320, 320),
    ]


def test_stop_failure_still_restores_transport_and_window_constraints(
    tmp_path: Path,
) -> None:
    stop_error = TimeoutError("encoder stop failed")

    class FailedRecording(_Recording):
        def stop(self, **_kwargs: object) -> StagedVideoCapture | None:
            self.is_recording = False
            raise stop_error

    recording = FailedRecording()
    session, transport, window = _session(
        tmp_path,
        recording=recording,
        playing=True,
    )
    session.start()
    recording.current_t = 2.5

    with pytest.raises(TimeoutError) as exc_info:
        session.stop(timeout_s=0.25)

    assert exc_info.value is stop_error
    assert transport.t() == pytest.approx(2.5)
    assert transport.is_playing is True
    assert transport.epoch == 2
    assert window.calls[-2:] == [
        (
            "maximum",
            recording_session_module._RESTORED_DRAW_WINDOW_MAX_SIZE,
            recording_session_module._RESTORED_DRAW_WINDOW_MAX_SIZE,
        ),
        ("minimum", 320, 320),
    ]


def test_stop_publishes_completed_staging_before_reporting_restore_failure(
    tmp_path: Path,
) -> None:
    restore_error = RuntimeError("maximum restore failed")

    class FailedRestoreWindow(_Window):
        def set_maximum_size(self, width: int, height: int) -> None:
            super().set_maximum_size(width, height)
            if width == recording_session_module._RESTORED_DRAW_WINDOW_MAX_SIZE:
                raise restore_error

        def set_minimum_size(self, width: int, height: int) -> None:
            super().set_minimum_size(width, height)
            if width == 320:
                raise RuntimeError("minimum restore failed")

    recording = _Recording()
    session, _transport, _window = _session(
        tmp_path,
        recording=recording,
        window=FailedRestoreWindow(),
    )
    session.start()

    with pytest.raises(RuntimeError) as exc_info:
        session.stop()

    assert exc_info.value is restore_error
    assert (tmp_path / "piece.mp4").read_bytes() == b"encoded-once"
    assert capture_manifest_path_for(tmp_path / "piece.mp4").is_file()
    assert list(tmp_path.glob(".*.recording")) == []
    assert _window.calls[-2:] == [
        (
            "maximum",
            recording_session_module._RESTORED_DRAW_WINDOW_MAX_SIZE,
            recording_session_module._RESTORED_DRAW_WINDOW_MAX_SIZE,
        ),
        ("minimum", 320, 320),
    ]


def test_completed_video_is_versioned_and_published_without_reencoding(
    tmp_path: Path,
) -> None:
    base_path = tmp_path / "piece.mp4"
    base_path.write_bytes(b"old video")
    provenance_calls: list[float] = []
    recording = _Recording()
    session, transport, _window = _session(
        tmp_path,
        recording=recording,
        playing=False,
        provenance_calls=provenance_calls,
    )

    session.start()
    assert recording.output_path == tmp_path / "piece_001.mp4"
    recording.current_t = 2.5
    session.stop(stop_reason="shutdown", abort_reason="application_close")

    published = tmp_path / "piece_001.mp4"
    assert base_path.read_bytes() == b"old video"
    assert published.read_bytes() == b"encoded-once"
    assert provenance_calls == [2.0]
    assert transport.t() == pytest.approx(2.5)
    assert transport.is_playing is False
    payload = json.loads(
        capture_manifest_path_for(published).read_text(encoding="utf-8")
    )
    assert payload["frame"]["t"] == pytest.approx(2.0)
    assert payload["output"]["artifact_paths"] == [str(published)]
    assert payload["output"]["size"] == {"width": 1280, "height": 960}
    assert payload["recording"]["stop_reason"] == "shutdown"
    assert payload["recording"]["abort_reason"] == "application_close"


def test_successful_stop_restores_playing_state_and_uses_first_frame_provenance(
    tmp_path: Path,
) -> None:
    provenance_calls: list[float] = []
    recording = _Recording()
    session, transport, _window = _session(
        tmp_path,
        recording=recording,
        playing=True,
        provenance_calls=provenance_calls,
    )
    first = _provenance(2.0)
    session.start()
    session.record_presented_frame(
        fresh=True,
        read_frame_rgb24=lambda: b"first-frame",
        provenance=first,
        error=None,
    )
    recording.current_t = 2.5

    session.stop()

    assert provenance_calls == []
    assert transport.t() == pytest.approx(2.5)
    assert transport.is_playing is True
    assert transport.epoch == 2
    payload = json.loads(
        capture_manifest_path_for(tmp_path / "piece.mp4").read_text(encoding="utf-8")
    )
    assert payload["frame"]["t"] == pytest.approx(2.0)


def test_late_manifest_collision_retries_the_completed_staging_as_one_generation(
    tmp_path: Path,
) -> None:
    recording = _Recording()
    session, _transport, _window = _session(tmp_path, recording=recording)
    session.start()
    initial_path = recording.output_path
    assert initial_path is not None
    external_manifest = capture_manifest_path_for(initial_path)
    external_manifest.write_text("external", encoding="utf-8")

    session.stop()

    retried = tmp_path / "piece_001.mp4"
    assert not initial_path.exists()
    assert external_manifest.read_text(encoding="utf-8") == "external"
    assert retried.read_bytes() == b"encoded-once"
    assert capture_manifest_path_for(retried).is_file()


def test_publish_failure_retains_completed_staging_for_recovery(
    tmp_path: Path,
) -> None:
    publish_error = RuntimeError("publish failed")

    class FailedCaptureService(CaptureService):
        def publish_recording_staged_with_retry(
            self,
            staged_path: Path,
            base_path: Path,
            *,
            initial_path: Path | None = None,
            t: float,
            canvas_size: tuple[int, int],
            output_size: tuple[int, int],
            provenance: CaptureProvenance,
            recording: RecordingManifest,
        ) -> PublishedCaptureGeneration:
            del (
                staged_path,
                base_path,
                initial_path,
                t,
                canvas_size,
                output_size,
                provenance,
                recording,
            )
            raise publish_error

    recording = _Recording()
    session, _transport, _window = _session(
        tmp_path,
        recording=recording,
        capture_service=FailedCaptureService(),
    )
    session.start()
    staging_path = (tmp_path / "piece.mp4").with_name(".piece.mp4.recording")

    with pytest.raises(RuntimeError) as exc_info:
        session.stop()

    assert exc_info.value is publish_error
    assert staging_path.read_bytes() == b"encoded-once"
    assert not (tmp_path / "piece.mp4").exists()


def test_close_stops_active_recording_once(tmp_path: Path) -> None:
    recording = _Recording()
    session, _transport, _window = _session(tmp_path, recording=recording)
    session.start()

    session.close(timeout_s=1.25, stop_reason="shutdown")
    session.close(timeout_s=0.0, stop_reason="shutdown")

    assert recording.trace.count("stop") == 1
    assert recording.stop_timeout_s == pytest.approx(1.25)
    assert recording.stop_reason == "shutdown"
