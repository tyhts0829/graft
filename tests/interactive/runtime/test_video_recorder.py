from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from grafix.runtime_config_loader import runtime_config
from grafix.interactive.runtime import video_recorder
from grafix.interactive.runtime.video_recorder import (
    VideoRecorder,
    _ffmpeg_command,
    default_video_output_path,
)


def test_default_video_output_path_uses_data_dir_and_script_stem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))

    def draw(t: float) -> None:
        return None

    path = default_video_output_path(draw, config=runtime_config())
    assert path.parts[0] == "data"
    assert path.parts[1] == "output"
    assert path.parts[2] == "video"
    assert path.name == f"{Path(__file__).stem}.mp4"
    assert path.suffix == ".mp4"


def test_default_video_output_path_requires_explicit_config() -> None:
    with pytest.raises(TypeError, match="config"):
        default_video_output_path(lambda _t: None)  # type: ignore[call-arg]


@pytest.mark.parametrize("ext", [1, None, b"mp4"])
def test_default_video_output_path_rejects_non_string_extension(
    ext: object,
) -> None:
    with pytest.raises(TypeError, match="ext"):
        default_video_output_path(
            lambda _t: None,
            ext=ext,  # type: ignore[arg-type]
            config=runtime_config(),
        )


@pytest.mark.parametrize("ext", ["", ".mp4", " mp4", "video/mp4"])
def test_default_video_output_path_requires_canonical_extension(ext: str) -> None:
    with pytest.raises(ValueError, match="ext"):
        default_video_output_path(
            lambda _t: None,
            ext=ext,
            config=runtime_config(),
        )


def test_ffmpeg_command_contains_expected_rawvideo_args():
    cmd = _ffmpeg_command(output_path=Path("out.mp4"), size=(320, 240), fps=60.0)

    assert cmd[0] == "ffmpeg"
    assert "-f" in cmd
    assert "rawvideo" in cmd
    assert "-pix_fmt" in cmd
    assert "rgb24" in cmd
    assert "-video_size" in cmd
    assert "320x240" in cmd
    assert "-framerate" in cmd
    assert "60.0" in cmd
    assert "-vf" in cmd
    assert cmd[cmd.index("-vf") + 1] == "vflip,pad=ceil(iw/2)*2:ceil(ih/2)*2"
    assert cmd[-1] == "out.mp4"


def test_finish_error_includes_recording_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailedProcess:
        stdin = object()
        returncode = 1

        def communicate(self, *, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            assert input == b""
            return b"", b"encoder failed"

    output_path = tmp_path / "failed.mp4"
    output_path.write_bytes(b"existing-complete-video")
    ffmpeg_paths: list[Path] = []

    def fake_popen(cmd: list[str], **kwargs: object) -> FailedProcess:
        ffmpeg_paths.append(Path(cmd[-1]))
        return FailedProcess()

    monkeypatch.setattr(video_recorder.subprocess, "Popen", fake_popen)
    recorder = VideoRecorder(output_path=output_path, size=(3, 5), fps=24.0)

    with pytest.raises(RuntimeError) as exc_info:
        recorder.finish()

    message = str(exc_info.value)
    assert f"path={output_path}" in message
    assert "input_size=3x5" in message
    assert "fps=24" in message
    assert "encoder failed" in message
    assert output_path.read_bytes() == b"existing-complete-video"
    assert len(ffmpeg_paths) == 1
    assert ffmpeg_paths[0] != output_path
    assert not ffmpeg_paths[0].exists()


def test_finish_transfers_fsynced_staging_without_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class SuccessfulProcess:
        stdin = object()
        returncode = 0

        def communicate(self, *, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            return b"", b""

    output_path = tmp_path / "transactional.mp4"
    fsynced: list[Path] = []

    def fake_popen(cmd: list[str], **_kwargs: object) -> SuccessfulProcess:
        Path(cmd[-1]).write_bytes(b"complete-video")
        return SuccessfulProcess()

    monkeypatch.setattr(video_recorder.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        video_recorder,
        "_fsync_file",
        lambda path: fsynced.append(Path(path)),
    )
    recorder = VideoRecorder(
        output_path=output_path,
        size=(4, 6),
        fps=30.0,
    )

    staging_path = recorder.finish()

    assert fsynced == [staging_path]
    assert staging_path.read_bytes() == b"complete-video"
    assert not output_path.exists()
    staging_path.unlink()


def test_missing_stdin_pipe_aborts_and_reaps_spawned_encoder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class MissingStdinProcess:
        stdin = None
        stdout = None
        stderr = None
        returncode: int | None = None

        def __init__(self) -> None:
            self.calls: list[str] = []

        def terminate(self) -> None:
            self.calls.append("terminate")
            self.returncode = -15

        def wait(self, *, timeout: float) -> int:
            self.calls.append(f"wait:{timeout:g}")
            return int(self.returncode or 0)

    process = MissingStdinProcess()
    monkeypatch.setattr(video_recorder.subprocess, "Popen", lambda *_a, **_k: process)

    with pytest.raises(RuntimeError, match="stdin pipe"):
        VideoRecorder(output_path=tmp_path / "movie.mp4", size=(4, 6), fps=30.0)

    assert process.calls == ["terminate", "wait:2"]
    assert list(tmp_path.iterdir()) == []


def test_communicate_failure_aborts_encoder_and_preserves_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    communicate_error = KeyboardInterrupt("interrupted")

    class InterruptedProcess:
        stdin = object()
        stdout = None
        stderr = None
        returncode: int | None = None

        def __init__(self) -> None:
            self.calls: list[str] = []

        def communicate(self, *, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            assert input == b""
            self.calls.append("communicate")
            raise communicate_error

        def terminate(self) -> None:
            self.calls.append("terminate")
            self.returncode = -15

        def wait(self, *, timeout: float) -> int:
            self.calls.append(f"wait:{timeout:g}")
            return int(self.returncode or 0)

    process = InterruptedProcess()

    def fake_popen(cmd: list[str], **_kwargs: object) -> InterruptedProcess:
        Path(cmd[-1]).write_bytes(b"partial")
        return process

    monkeypatch.setattr(video_recorder.subprocess, "Popen", fake_popen)
    recorder = VideoRecorder(
        output_path=tmp_path / "movie.mp4",
        size=(4, 6),
        fps=30.0,
    )

    with pytest.raises(KeyboardInterrupt, match="interrupted") as exc_info:
        recorder.finish()

    assert exc_info.value is communicate_error
    assert process.calls == ["communicate", "terminate", "wait:2"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("timeout_s", [0.0, 1.25])
def test_finish_timeout_terminates_kills_reaps_and_removes_partial_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timeout_s: float
) -> None:
    class Pipe:
        def __init__(self, name: str, calls: list[str]) -> None:
            self._name = name
            self._calls = calls

        def close(self) -> None:
            self._calls.append(f"close:{self._name}")

    class HungProcess:
        returncode: int | None = None

        def __init__(self) -> None:
            self.calls: list[str] = []
            self.stdin = Pipe("stdin", self.calls)
            self.stdout = Pipe("stdout", self.calls)
            self.stderr = Pipe("stderr", self.calls)
            self.killed = False

        def communicate(
            self, *, input: bytes, timeout: float
        ) -> tuple[bytes, bytes]:
            assert input == b""
            self.calls.append(f"communicate:{timeout:g}")
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)

        def terminate(self) -> None:
            self.calls.append("terminate")

        def kill(self) -> None:
            self.calls.append("kill")
            self.killed = True

        def wait(self, *, timeout: float) -> int:
            self.calls.append(f"wait:{timeout:g}")
            if not self.killed:
                raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)
            self.returncode = -9
            return -9

    process = HungProcess()
    output_path = tmp_path / "hung.mp4"

    def fake_popen(cmd: list[str], **_kwargs: object) -> HungProcess:
        Path(cmd[-1]).write_bytes(b"partial-video")
        return process

    monkeypatch.setattr(video_recorder.subprocess, "Popen", fake_popen)
    recorder = VideoRecorder(output_path=output_path, size=(4, 6), fps=30.0)

    with pytest.raises(TimeoutError) as exc_info:
        recorder.finish(timeout_s=timeout_s)

    assert isinstance(exc_info.value.__cause__, subprocess.TimeoutExpired)
    assert f"timeout={timeout_s:g}s" in str(exc_info.value)
    communicate_timeout = float(process.calls[0].partition(":")[2])
    assert process.calls[1] == "terminate"
    assert process.calls[2].startswith("wait:")
    assert process.calls[3] == "kill"
    assert process.calls[4].startswith("wait:")
    assert process.calls[5:] == [
        "close:stdin",
        "close:stdout",
        "close:stderr",
    ]
    assert 0.0 <= communicate_timeout <= timeout_s
    cleanup_limit = max(timeout_s, 0.5)
    assert 0.0 < float(process.calls[2].partition(":")[2]) <= cleanup_limit
    assert 0.0 < float(process.calls[4].partition(":")[2]) <= cleanup_limit
    assert not output_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_abort_reaps_encoder_without_publishing_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class RunningProcess:
        stdin = object()
        stdout = None
        stderr = None
        returncode: int | None = None

        def __init__(self) -> None:
            self.calls: list[str] = []

        def terminate(self) -> None:
            self.calls.append("terminate")
            self.returncode = -15

        def wait(self, *, timeout: float) -> int:
            self.calls.append(f"wait:{timeout:g}")
            return int(self.returncode or 0)

    process = RunningProcess()
    output_path = tmp_path / "movie.mp4"

    def fake_popen(cmd: list[str], **_kwargs: object) -> RunningProcess:
        Path(cmd[-1]).write_bytes(b"partial")
        return process

    monkeypatch.setattr(video_recorder.subprocess, "Popen", fake_popen)
    recorder = VideoRecorder(output_path=output_path, size=(4, 6), fps=30.0)

    recorder.abort()
    recorder.abort()

    assert process.calls == ["terminate", "wait:2"]
    assert not output_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_fsync_interrupt_cleans_staging_and_preserves_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class SuccessfulProcess:
        stdin = object()
        returncode = 0

        def communicate(self, *, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            return b"", b""

    interrupt = KeyboardInterrupt("file fsync interrupted")

    def fake_popen(cmd: list[str], **_kwargs: object) -> SuccessfulProcess:
        Path(cmd[-1]).write_bytes(b"complete-video")
        return SuccessfulProcess()

    monkeypatch.setattr(video_recorder.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        video_recorder,
        "_fsync_file",
        lambda _path: (_ for _ in ()).throw(interrupt),
    )
    recorder = VideoRecorder(
        output_path=tmp_path / "movie.mp4",
        size=(4, 6),
        fps=30.0,
    )

    with pytest.raises(KeyboardInterrupt, match="file fsync interrupted") as exc_info:
        recorder.finish()

    assert exc_info.value is interrupt
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("fps", [float("nan"), float("inf"), float("-inf")])
def test_video_recorder_rejects_non_finite_fps_before_creating_temp(
    tmp_path: Path, fps: float
) -> None:
    with pytest.raises(ValueError, match="fps"):
        VideoRecorder(output_path=tmp_path / "movie.mp4", size=(4, 6), fps=fps)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"output_path": "movie.mp4"}, TypeError),
        ({"size": [4, 6]}, TypeError),
        ({"size": (4.0, 6)}, TypeError),
        ({"size": (True, 6)}, TypeError),
        ({"size": (0, 6)}, ValueError),
        ({"fps": "30"}, TypeError),
        ({"fps": True}, TypeError),
        ({"fps": 0.0}, ValueError),
    ],
)
def test_video_recorder_rejects_implicit_constructor_coercion_before_io(
    tmp_path: Path,
    kwargs: dict[str, object],
    error: type[Exception],
) -> None:
    values: dict[str, object] = {
        "output_path": tmp_path / "movie.mp4",
        "size": (4, 6),
        "fps": 30.0,
    }
    values.update(kwargs)
    with pytest.raises(error):
        VideoRecorder(**values)  # type: ignore[arg-type]
    assert list(tmp_path.iterdir()) == []


def test_video_recorder_rejects_implicit_frame_and_timeout_coercion() -> None:
    recorder = object.__new__(VideoRecorder)
    with pytest.raises(TypeError, match="frame"):
        recorder.write_frame_rgb24(bytearray())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="timeout_s"):
        recorder.finish(timeout_s="1")  # type: ignore[arg-type]


def test_ffmpeg_spawn_failure_cleans_temporary_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing_ffmpeg(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(video_recorder.subprocess, "Popen", missing_ffmpeg)

    with pytest.raises(RuntimeError, match="ffmpeg が見つかりません"):
        VideoRecorder(output_path=tmp_path / "movie.mp4", size=(4, 6), fps=30.0)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe が必要",
)
def test_video_recorder_encodes_odd_input_size_with_even_padding(tmp_path: Path) -> None:
    output_path = tmp_path / "odd.mp4"
    recorder = VideoRecorder(output_path=output_path, size=(3, 5), fps=1.0)
    # GL readback と同じ bottom-up 行順。vflip 後は明るい最終行が動画の先頭行になる。
    frame = b"".join(bytes([value]) * (3 * 3) for value in (0, 50, 100, 150, 250))
    recorder.write_frame_rgb24(frame)
    staging_path = recorder.finish()
    assert staging_path is not None
    assert not output_path.exists()

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(staging_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert probe.stdout.strip() == "4x6"

    decoded = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(staging_path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    row_bytes = 4 * 3
    top_drawn = decoded[:row_bytes][: 3 * 3]
    bottom_drawn = decoded[4 * row_bytes : 5 * row_bytes][: 3 * 3]
    assert sum(top_drawn) > sum(bottom_drawn)
