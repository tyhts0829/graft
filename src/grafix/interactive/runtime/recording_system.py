"""
Purpose:
    fixed-fps録画clock、encoder、frame/error統計をcompleted stagingへまとめる。
Use when:
    録画state machine、frame drop方針、RecordingManifest生成を変更するとき。
Constraints:
    - 録画clockはRGB frameのencoder書き込み成功後だけ進める。
    - stale/失敗frameはpauseとして記録し、重複frameで時間を埋めない。
    - stopはencoderをstateから切り離して必ず統計をresetし、公開前のstagingだけを返す。
    - manifestは同じ録画sessionの寸法、clock、error統計から確定する。
Side effects:
    ffmpeg recorderを起動・終了し、完成した一時動画を呼び出し側へ移譲する。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from grafix.core.capture_manifest import RecordingManifest
from grafix.core.value_validation import (
    exact_string,
    finite_real,
    positive_integer_pair,
)
from grafix.interactive.transport import RecordingClock
from grafix.interactive.runtime.video_recorder import (
    DEFAULT_VIDEO_FINALIZE_TIMEOUT_S,
    VideoRecorder,
)


@dataclass(frozen=True, slots=True)
class StagedVideoCapture:
    """encode 完了済みで、まだ final name へ公開していない動画。"""

    staging_path: Path
    output_path: Path
    framebuffer_size: tuple[int, int]
    recording: RecordingManifest

    def __post_init__(self) -> None:
        if not isinstance(self.staging_path, Path):
            raise TypeError("staging_path は Path である必要があります")
        if not isinstance(self.output_path, Path):
            raise TypeError("output_path は Path である必要があります")
        object.__setattr__(
            self,
            "framebuffer_size",
            positive_integer_pair(
                self.framebuffer_size,
                name="framebuffer_size",
            ),
        )
        if not isinstance(self.recording, RecordingManifest):
            raise TypeError("recording は RecordingManifest である必要があります")


class VideoRecordingSystem:
    """動画録画の最小ステートマシン。"""

    def __init__(self, *, fps: float) -> None:
        self._fps = finite_real(
            fps,
            name="fps",
            minimum=0.0,
            minimum_inclusive=False,
        )
        self._recorder: VideoRecorder | None = None
        self._clock: RecordingClock | None = None
        self._size = (0, 0)
        self._dropped_frame_count = 0
        self._duplicated_frame_count = 0
        self._error_count = 0
        self._last_error: str | None = None

    @property
    def is_recording(self) -> bool:
        """録画中なら True を返す。"""

        return self._recorder is not None

    def t(self) -> float:
        """録画タイムライン上の `t`（秒）を返す。"""

        clock = self._clock
        if clock is None:
            raise RuntimeError("録画は開始されていません")
        return clock.t()

    @property
    def frame_index(self) -> int:
        """正常に encoder へ渡した frame 数を返す。"""

        clock = self._clock
        return 0 if clock is None else clock.frame_index

    def start(
        self,
        *,
        framebuffer_size: tuple[int, int],
        t0: float,
        output_path: Path,
    ) -> None:
        """録画を開始する。"""

        size = positive_integer_pair(
            framebuffer_size,
            name="framebuffer_size",
        )
        start_t = finite_real(t0, name="t0")
        if not isinstance(output_path, Path):
            raise TypeError("output_path は Path である必要があります")
        if self._recorder is not None:
            return

        # clock/input validation を encoder 起動より先に済ませる。後続初期化が
        # 失敗して、起動済み ffmpeg だけが state 外へ leak する経路を作らない。
        clock = RecordingClock(t0=start_t, fps=self._fps)
        recorder = VideoRecorder(
            output_path=output_path,
            size=size,
            fps=self._fps,
        )
        self._size = size
        self._clock = clock
        self._recorder = recorder
        self._dropped_frame_count = 0
        self._duplicated_frame_count = 0
        self._error_count = 0
        self._last_error = None
        # user-visible notification も acquisition transaction に含める。stdout error/
        # KeyboardInterrupt がここで起きても、呼び出し側から見えない encoder を残さない。
        try:
            print(f"Started video recording: {recorder.path} (fps={self._fps:g})")
        except BaseException:
            self._recorder = None
            self._clock = None
            self._size = (0, 0)
            self._reset_statistics()
            recorder.abort()
            raise

    def write_frame(self, frame_rgb24: bytes) -> None:
        """renderer が読み出した RGB24 bytes を 1 フレームとして書き込む。"""

        recorder = self._recorder
        clock = self._clock
        if recorder is None or clock is None:
            return

        try:
            if not isinstance(frame_rgb24, bytes):
                raise TypeError("frame_rgb24 は bytes である必要があります")
            recorder.write_frame_rgb24(frame_rgb24)
        except Exception as exc:
            self.pause_frame(f"{type(exc).__name__}: {exc}")
            raise
        clock.tick()

    def pause_frame(self, error: str) -> None:
        """失敗した scene を動画へ書かず、録画 clock も進めずに記録する。"""

        detail = exact_string(error, name="error")
        if not detail:
            raise ValueError("error は空にできません")
        if self._recorder is None or self._clock is None:
            return
        self._dropped_frame_count += 1
        self._error_count += 1
        self._last_error = detail

    def _manifest(
        self,
        *,
        frame_count: int,
        stop_reason: str,
        abort_reason: str | None = None,
    ) -> RecordingManifest:
        """現在の統計を immutable manifest payload へ固定する。"""

        return RecordingManifest(
            fps=self._fps,
            frame_count=frame_count,
            dropped_frame_count=self._dropped_frame_count,
            duplicated_frame_count=self._duplicated_frame_count,
            error_count=self._error_count,
            error_policy="pause",
            stop_reason=stop_reason,
            abort_reason=abort_reason,
            last_error=self._last_error,
        )

    def _reset_statistics(self) -> None:
        self._dropped_frame_count = 0
        self._duplicated_frame_count = 0
        self._error_count = 0
        self._last_error = None

    def stop(
        self,
        *,
        timeout_s: float = DEFAULT_VIDEO_FINALIZE_TIMEOUT_S,
        stop_reason: str = "user_stop",
        abort_reason: str | None = None,
    ) -> StagedVideoCapture | None:
        """録画を終了し、artifact+manifest transaction 用の staging を返す。"""

        timeout = finite_real(timeout_s, name="timeout_s", minimum=0.0)
        exact_string(stop_reason, name="stop_reason")
        if abort_reason is not None:
            exact_string(abort_reason, name="abort_reason")
        recorder = self._recorder
        if recorder is None:
            self._clock = None
            self._size = (0, 0)
            self._reset_statistics()
            return None

        self._recorder = None
        clock = self._clock
        frame_count = 0 if clock is None else clock.frame_index
        size = self._size
        manifest = self._manifest(
            frame_count=frame_count,
            stop_reason=stop_reason,
            abort_reason=abort_reason,
        )
        try:
            staging_path = recorder.finish(timeout_s=timeout)
        except BaseException:
            # finish の契約外の早期失敗でも、system から切り離した encoder/temp を
            # 残さない。finish 自身の cleanup 後に呼んでも abort は冪等である。
            recorder.abort()
            raise
        finally:
            self._clock = None
            self._size = (0, 0)
            self._reset_statistics()
        return StagedVideoCapture(
            staging_path=staging_path,
            output_path=recorder.path,
            framebuffer_size=size,
            recording=manifest,
        )


__all__ = ["StagedVideoCapture", "VideoRecordingSystem"]
