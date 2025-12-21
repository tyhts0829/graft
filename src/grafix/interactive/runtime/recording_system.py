# どこで: `src/grafix/interactive/runtime/recording_system.py`。
# 何を: V キー録画の開始/停止/フレーム書き込みを担当する。
# なぜ: DrawWindowSystem の状態変数群を分離し、責務を明確化するため。

from __future__ import annotations

from pathlib import Path

from grafix.interactive.runtime.frame_clock import RecordingClock
from grafix.interactive.runtime.video_recorder import VideoRecorder


class VideoRecordingSystem:
    """動画録画の最小ステートマシン。"""

    def __init__(self, *, output_path: Path, fps: float) -> None:
        self._output_path = Path(output_path)
        self._fps = float(fps)
        self._recorder: VideoRecorder | None = None
        self._clock: RecordingClock | None = None
        self._size = (0, 0)

    @property
    def is_recording(self) -> bool:
        """録画中なら True を返す。"""

        return self._recorder is not None

    def t(self) -> float:
        """録画タイムライン上の `t`（秒）を返す。"""

        clock = self._clock
        if clock is None:
            raise RuntimeError("録画は開始されていません")
        return float(clock.t())

    def start(self, *, framebuffer_size: tuple[int, int], t0: float) -> None:
        """録画を開始する。"""

        if self._recorder is not None:
            return
        if self._fps <= 0:
            raise ValueError("録画には fps > 0 が必要です")

        w, h = framebuffer_size
        self._size = (int(w), int(h))
        self._recorder = VideoRecorder(
            output_path=self._output_path,
            size=self._size,
            fps=self._fps,
        )
        self._clock = RecordingClock(t0=float(t0), fps=self._fps)
        print(f"Started video recording: {self._output_path} (fps={self._fps:g})")

    def write_frame(self, screen: object) -> None:
        """現在の screen 内容を 1 フレームとして書き込む。"""

        recorder = self._recorder
        clock = self._clock
        if recorder is None or clock is None:
            return

        w, h = self._size
        frame = screen.read(  # type: ignore[attr-defined]
            viewport=(0, 0, int(w), int(h)),
            components=3,
            alignment=1,
        )
        recorder.write_frame_rgb24(frame)
        clock.tick()

    def stop(self) -> None:
        """録画を終了する。"""

        recorder = self._recorder
        clock = self._clock
        if recorder is None or clock is None:
            return

        self._recorder = None
        frames = int(clock.frame_index)
        seconds = frames / float(self._fps) if self._fps > 0 else 0.0
        try:
            recorder.close()
        finally:
            self._clock = None
            self._size = (0, 0)
        print(f"Saved video: {recorder.path} (frames={frames}, seconds={seconds:.3f})")

