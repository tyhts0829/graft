"""
Purpose:
    RGB24 frame列をffmpegへ送り、公開可能なfsync済み動画stagingへ確定する。
Use when:
    ffmpeg invocation、frame寸法、finish timeout、process/temp cleanupを変更するとき。
Constraints:
    - ffmpegにfinal pathを直接開かせず、完成stagingのpublishは上位capture境界へ委ねる。
    - 入力frameは開始時のframebuffer寸法とexact byte lengthを維持する。
    - finishはdeadline内で終了を待ち、timeout/error時もterminate/kill/reapを試みる。
    - 正常終了とfile fsyncの後だけstaging ownershipを呼び出し側へ移す。
    - abortは未確定tempを公開せず、processとpipeを冪等に回収する。
Side effects:
    ffmpeg subprocessと同一directory上のprivate一時動画を作成・削除する。
"""

from __future__ import annotations

import math
import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from grafix.export.output_paths import output_path_for_draw
from grafix.core.runtime_config import RuntimeConfig
from grafix.core.value_validation import (
    exact_string,
    finite_real,
    positive_integer_pair,
)


DEFAULT_VIDEO_FINALIZE_TIMEOUT_S = 30.0
_DEFAULT_PROCESS_ABORT_TIMEOUT_S = 4.0
_MAX_PROCESS_ABORT_RESERVE_S = 1.0
_MIN_PROCESS_REAP_GRACE_S = 0.5


def default_video_output_path(
    draw: Callable[[float], object],
    *,
    config: RuntimeConfig,
    run_id: str | None = None,
    ext: str = "mp4",
) -> Path:
    """draw の定義元に基づく動画の既定保存パスを返す。

    Notes
    -----
    パスは `output/{kind}/` 配下で sketch_dir のサブディレクトリ構造をミラーする。
    """

    suffix = exact_string(ext, name="ext")
    if (
        not suffix
        or suffix != suffix.strip()
        or suffix.startswith(".")
        or "/" in suffix
        or "\\" in suffix
    ):
        raise ValueError("ext は '.' なしの空でない拡張子名で指定してください")
    return output_path_for_draw(
        kind="video",
        ext=suffix,
        draw=draw,
        run_id=run_id,
        config=config,
    )


def _ffmpeg_command(
    *,
    output_path: Path,
    size: tuple[int, int],
    fps: float,
) -> list[str]:
    if not isinstance(output_path, Path):
        raise TypeError("output_path は Path である必要があります")
    width, height = positive_integer_pair(size, name="size")
    frame_rate = finite_real(
        fps,
        name="fps",
        minimum=0.0,
        minimum_inclusive=False,
    )
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        str(frame_rate),
        "-i",
        "-",
        "-vf",
        "vflip,pad=ceil(iw/2)*2:ceil(ih/2)*2",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]


def _temporary_video_output_path(output_path: Path) -> Path:
    """最終出力と同じディレクトリに、コンテナ拡張子を保った一時パスを作る。"""

    suffix = output_path.suffix or ".tmp"
    fd, raw_path = tempfile.mkstemp(
        prefix=f".{output_path.stem}.recording-",
        suffix=suffix,
        dir=output_path.parent,
    )
    os.close(fd)
    return Path(raw_path)


def _fsync_file(path: Path) -> None:
    """encoder が閉じた完成 temp の内容を publish 前に永続化する。"""

    with path.open("rb") as file_obj:
        os.fsync(file_obj.fileno())


def _abort_process(
    proc: subprocess.Popen[bytes],
    *,
    timeout_s: float = _DEFAULT_PROCESS_ABORT_TIMEOUT_S,
) -> None:
    """encoder を best-effort で停止・回収し、pipe descriptor も閉じる。"""

    timeout = float(timeout_s)
    if not math.isfinite(timeout) or timeout < 0.0:
        timeout = 0.0
    deadline = time.monotonic() + timeout

    def remaining() -> float:
        return max(0.0, deadline - time.monotonic())

    # communicate() 自体が KeyboardInterrupt / I/O error で失敗した場合にも、
    # ffmpeg を orphan/zombie として残さない。cleanup error は元の error を隠さない。
    if getattr(proc, "returncode", None) is None:
        try:
            proc.terminate()
        except BaseException:
            pass
    try:
        # terminate が無視されても kill/reap 用に半分の budget を残す。
        proc.wait(timeout=remaining() / 2.0)
    except BaseException:
        pass
    if getattr(proc, "returncode", None) is None:
        try:
            proc.kill()
        except BaseException:
            pass
        try:
            proc.wait(timeout=remaining())
        except BaseException:
            pass

    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(proc, name, None)
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except BaseException:
                pass


class VideoRecorder:
    """raw RGB フレーム列を動画へ保存する録画器。

    入力 frame は ``size`` どおりの RGB24 とし、H.264 側では必要な場合だけ右端・下端を
    1 px pad して、yuv420p が要求する偶数寸法へ揃える。
    """

    def __init__(
        self,
        *,
        output_path: Path,
        size: tuple[int, int],
        fps: float,
    ) -> None:
        """録画器を初期化して ffmpeg を起動する。"""

        if not isinstance(output_path, Path):
            raise TypeError("output_path は Path である必要があります")
        normalized_size = positive_integer_pair(size, name="size")
        frame_rate = finite_real(
            fps,
            name="fps",
            minimum=0.0,
            minimum_inclusive=False,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)

        self.path = output_path
        self.size = normalized_size
        self.fps = frame_rate
        self._frame_bytes = self.size[0] * self.size[1] * 3
        self._proc: subprocess.Popen[bytes] | None = None
        # ffmpeg は final path を直接開かない。完成・fsync 済み staging の publish は
        # capture transaction が所有する。
        temporary_path = _temporary_video_output_path(self.path)
        self._temporary_path: Path | None = temporary_path

        cmd = _ffmpeg_command(
            output_path=temporary_path,
            size=self.size,
            fps=self.fps,
        )
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as e:
            self._remove_temporary_output()
            raise RuntimeError("ffmpeg が見つかりません（PATH を確認してください）") from e
        except BaseException:
            self._remove_temporary_output()
            raise

        if self._proc.stdin is None:
            _abort_process(self._proc)
            self._proc = None
            self._remove_temporary_output()
            raise RuntimeError("ffmpeg stdin pipe の作成に失敗しました")

    def _remove_temporary_output(self) -> None:
        """未確定の録画ファイルを best-effort で削除する。"""

        temporary_path = self._temporary_path
        self._temporary_path = None
        if temporary_path is None:
            return
        try:
            temporary_path.unlink(missing_ok=True)
        except BaseException:
            # cleanup 失敗で元の encoder error を隠さない。dotfile の一時ファイルは
            # 最終成果物と区別でき、次回録画が同じ名前を再利用することもない。
            pass

    def write_frame_rgb24(self, frame: bytes) -> None:
        """1 フレーム分の RGB24 バイト列を書き込む。"""

        if type(frame) is not bytes:
            raise TypeError("frame は bytes である必要があります")
        proc = self._proc
        if proc is None:
            raise RuntimeError("録画は終了しています")
        if len(frame) != self._frame_bytes:
            raise ValueError(
                f"frame bytes が想定サイズと一致しません: got={len(frame)}, expected={self._frame_bytes}"
            )
        stdin = proc.stdin
        if stdin is None:
            raise RuntimeError("ffmpeg stdin pipe が閉じられています")
        try:
            stdin.write(frame)
        except (BrokenPipeError, OSError) as e:
            width, height = self.size
            raise RuntimeError(
                "ffmpeg への frame 書き込みに失敗しました"
                f": path={self.path}, input_size={width}x{height}, fps={self.fps:g}"
            ) from e

    def abort(self) -> None:
        """未確定の録画を公開せず停止し、process/temp を回収する。"""

        proc = self._proc
        self._proc = None
        if proc is not None:
            _abort_process(proc)
        self._remove_temporary_output()

    def finish(
        self,
        *,
        timeout_s: float = DEFAULT_VIDEO_FINALIZE_TIMEOUT_S,
    ) -> Path:
        """ffmpeg を正常終了し、fsync 済み staging の所有権を呼び出し側へ移す。"""

        timeout = finite_real(timeout_s, name="timeout_s", minimum=0.0)

        proc = self._proc
        if proc is None:
            raise RuntimeError("録画はすでに終了しています")
        # communicate に全budgetを渡すと、期限ちょうどのTimeoutExpired後に
        # kill済みprocessをreapする時間が0になりzombieを残し得る。通常は
        # timeoutの一部（最大1秒）をabort/reap用に予約する。timeout=0でも
        # resource cleanupだけは短い固定grace内で完了を試す。
        abort_reserve = min(_MAX_PROCESS_ABORT_RESERVE_S, timeout / 2.0)
        communicate_timeout = max(0.0, timeout - abort_reserve)
        deadline = time.monotonic() + timeout

        def abort_budget() -> float:
            remaining = min(
                _DEFAULT_PROCESS_ABORT_TIMEOUT_S,
                max(0.0, deadline - time.monotonic()),
            )
            return max(_MIN_PROCESS_REAP_GRACE_S, remaining)

        try:
            # communicate() は stdin を flush してから close する。
            # 先に stdin.close() すると Python 3.12 では flush-of-closed で落ちる場合があるため、
            # ここでは input=b"" で EOF を送って終了させる。
            _stdout, stderr = proc.communicate(input=b"", timeout=communicate_timeout)
        except subprocess.TimeoutExpired as exc:
            # encoder の hang で UI shutdown を無限に塞がない。完成していない
            # temp は公開せず、process は terminate/kill/wait で回収を試みる。
            _abort_process(proc, timeout_s=abort_budget())
            self._remove_temporary_output()
            width, height = self.size
            raise TimeoutError(
                "ffmpeg の終了がタイムアウトしました"
                f": timeout={timeout:g}s, path={self.path},"
                f" input_size={width}x{height}, fps={self.fps:g}"
            ) from exc
        except BaseException:
            _abort_process(proc, timeout_s=abort_budget())
            self._remove_temporary_output()
            raise
        finally:
            self._proc = None

        if proc.returncode != 0:
            self._remove_temporary_output()
            details = ""
            if stderr:
                details = stderr.decode("utf-8", errors="replace").strip()
            width, height = self.size
            raise RuntimeError(
                "ffmpeg が失敗しました"
                f" (code={proc.returncode}, path={self.path},"
                f" input_size={width}x{height}, fps={self.fps:g}). {details}".strip()
            )

        temporary_path = self._temporary_path
        if temporary_path is None:
            raise RuntimeError("録画の一時出力パスが失われました")
        try:
            # ffmpeg の正常終了だけでは、直後の電源断に対する file durability は
            # 保証されない。完成 temp を fsync してから呼び出し側へ渡す。
            _fsync_file(temporary_path)
        except BaseException as exc:
            self._remove_temporary_output()
            if isinstance(exc, OSError):
                raise RuntimeError(
                    f"録画ファイルの永続化に失敗しました: path={self.path}"
                ) from exc
            raise
        self._temporary_path = None
        return temporary_path
