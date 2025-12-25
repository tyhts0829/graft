# どこで: `src/grafix/interactive/runtime/monitor.py`。
# 何を: interactive 実行中の軽量メトリクス（FPS/CPU/RSS/頂点/ライン）を計測し、GUI 表示用スナップショットを提供する。
# なぜ: Parameter GUI 上で描画負荷を即座に把握できるようにするため。

from __future__ import annotations

from dataclasses import dataclass
import os
import time


@dataclass(frozen=True, slots=True)
class MonitorSnapshot:
    """Parameter GUI に表示する監視値のスナップショット。"""

    fps: float
    cpu_percent: float
    rss_mb: float
    vertices: int
    lines: int


class RuntimeMonitor:
    """interactive 実行中のメトリクスを軽量に集計する。"""

    def __init__(
        self,
        *,
        cpu_mem_sample_interval_s: float = 0.5,
        fps_sample_interval_s: float = 0.5,
    ) -> None:
        """監視を初期化する。

        Parameters
        ----------
        cpu_mem_sample_interval_s : float
            cpu/memory を psutil でサンプリングする最小間隔（秒）。
        fps_sample_interval_s : float
            FPS を更新する最小間隔（秒）。
        """

        self._cpu_mem_sample_interval_s = float(cpu_mem_sample_interval_s)

        self._fps_sample_interval_s = float(fps_sample_interval_s)
        self._fps = 0.0
        self._fps_window_t0: float | None = None
        self._fps_window_frames = 0

        self._last_sample_t: float | None = None
        self._last_cpu_total_s: float | None = None
        self._cpu_percent = 0.0
        self._rss_mb = 0.0

        self._vertices = 0
        self._lines = 0

        try:
            import psutil  # type: ignore[import-untyped]
        except Exception as exc:
            raise RuntimeError("RuntimeMonitor には psutil が必要です") from exc

        self._process = psutil.Process(int(os.getpid()))

    def tick_frame(self) -> None:
        """フレーム境界を通知し、FPS/CPU/Mem を更新する。"""

        now = time.perf_counter()

        # --- FPS ---
        if self._fps_window_t0 is None:
            self._fps_window_t0 = float(now)
            self._fps_window_frames = 0

        self._fps_window_frames += 1
        dt = float(now - float(self._fps_window_t0))
        if dt >= float(self._fps_sample_interval_s) and dt > 0.0:
            self._fps = float(self._fps_window_frames) / float(dt)
            self._fps_window_t0 = float(now)
            self._fps_window_frames = 0

        # --- CPU / Mem（一定周期）---
        last = self._last_sample_t
        if last is None:
            self._last_sample_t = float(now)
            self._last_cpu_total_s = float(self._cpu_total_s())
            self._rss_mb = float(self._rss_bytes()) / (1024.0 * 1024.0)
            return

        if float(now - last) < float(self._cpu_mem_sample_interval_s):
            return

        cpu_total_s = float(self._cpu_total_s())
        prev_cpu_total_s = float(self._last_cpu_total_s or 0.0)
        wall_dt = float(now - last)

        # 子プロセスが終了すると合算値が減ることがあるため、負の Δ は “リセット” 扱いにする。
        if cpu_total_s < prev_cpu_total_s:
            self._last_sample_t = float(now)
            self._last_cpu_total_s = float(cpu_total_s)
            self._rss_mb = float(self._rss_bytes()) / (1024.0 * 1024.0)
            return

        cpu_dt = float(cpu_total_s - prev_cpu_total_s)
        if wall_dt > 0.0:
            self._cpu_percent = 100.0 * cpu_dt / wall_dt

        self._rss_mb = float(self._rss_bytes()) / (1024.0 * 1024.0)
        self._last_sample_t = float(now)
        self._last_cpu_total_s = float(cpu_total_s)

    def set_draw_counts(self, *, vertices: int, lines: int) -> None:
        """描画対象の頂点数/ライン数（polyline 本数）を設定する。"""

        self._vertices = int(vertices)
        self._lines = int(lines)

    def snapshot(self) -> MonitorSnapshot:
        """現在の監視値をスナップショットとして返す。"""

        return MonitorSnapshot(
            fps=float(self._fps),
            cpu_percent=float(self._cpu_percent),
            rss_mb=float(self._rss_mb),
            vertices=int(self._vertices),
            lines=int(self._lines),
        )

    def _cpu_times_s(self, proc) -> float:
        t = proc.cpu_times()
        user = float(getattr(t, "user", 0.0))
        system = float(getattr(t, "system", 0.0))
        return float(user + system)

    def _cpu_total_s(self) -> float:
        total = float(self._cpu_times_s(self._process))
        for child in self._process.children(recursive=True):
            try:
                total += float(self._cpu_times_s(child))
            except Exception:
                continue
        return float(total)

    def _rss_bytes(self) -> int:
        total = int(self._process.memory_info().rss)
        for child in self._process.children(recursive=True):
            try:
                total += int(child.memory_info().rss)
            except Exception:
                continue
        return int(total)
