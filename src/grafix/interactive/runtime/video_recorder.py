# どこで: `src/grafix/interactive/runtime/video_recorder.py`。
# 何を: ffmpeg に raw RGB フレームを流し、動画として保存する最小録画器を提供する。
# なぜ: interactive プレビューを滑らかな動画として残せるようにするため。

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from grafix.core.parameters.persistence import default_param_store_path
from grafix.core.runtime_config import output_root_dir


def default_video_output_path(draw: Callable[[float], object], *, ext: str = "mp4") -> Path:
    """draw の定義元に基づく動画の既定保存パスを返す。

    Notes
    -----
    パスは `{output_root}/video/{script_stem}.{ext}`。
    `script_stem` は ParamStore 永続化と同一の算出規則。
    """

    script_stem = default_param_store_path(draw).stem
    suffix = str(ext).lstrip(".") or "mp4"
    return output_root_dir() / "video" / f"{script_stem}.{suffix}"


def _ffmpeg_command(
    *,
    output_path: Path,
    size: tuple[int, int],
    fps: float,
) -> list[str]:
    width, height = size
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
        f"{int(width)}x{int(height)}",
        "-framerate",
        str(float(fps)),
        "-i",
        "-",
        "-vf",
        "vflip",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]


class VideoRecorder:
    """raw RGB フレーム列を動画へ保存する録画器。"""

    def __init__(
        self,
        *,
        output_path: Path,
        size: tuple[int, int],
        fps: float,
    ) -> None:
        """録画器を初期化して ffmpeg を起動する。"""

        _output_path = Path(output_path)
        _output_path.parent.mkdir(parents=True, exist_ok=True)

        _fps = float(fps)
        if _fps <= 0:
            raise ValueError("fps は正の値である必要がある")

        width, height = size
        if int(width) <= 0 or int(height) <= 0:
            raise ValueError("size は正の (width, height) である必要がある")

        self.path = _output_path
        self.size = (int(width), int(height))
        self.fps = _fps
        self._frame_bytes = self.size[0] * self.size[1] * 3
        self._proc: subprocess.Popen[bytes] | None = None

        cmd = _ffmpeg_command(output_path=self.path, size=self.size, fps=self.fps)
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as e:
            raise RuntimeError("ffmpeg が見つかりません（PATH を確認してください）") from e

        if self._proc.stdin is None:
            raise RuntimeError("ffmpeg stdin pipe の作成に失敗しました")

    def write_frame_rgb24(self, frame: bytes) -> None:
        """1 フレーム分の RGB24 バイト列を書き込む。"""

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
        stdin.write(frame)

    def close(self) -> None:
        """録画を終了し、ffmpeg を待つ。"""

        proc = self._proc
        if proc is None:
            return

        try:
            # communicate() は stdin を flush してから close する。
            # 先に stdin.close() すると Python 3.12 では flush-of-closed で落ちる場合があるため、
            # ここでは input=b"" で EOF を送って終了させる。
            _stdout, stderr = proc.communicate(input=b"")
        finally:
            self._proc = None

        if proc.returncode != 0:
            details = ""
            if stderr:
                details = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"ffmpeg が失敗しました (code={proc.returncode}). {details}".strip()
            )
