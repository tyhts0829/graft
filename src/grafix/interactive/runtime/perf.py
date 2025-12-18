"""
どこで: `src/grafix/interactive/runtime/perf.py`。
何を: interactive 描画向けの最小区間計測（集計 + 周期出力）を提供する。
なぜ: ボトルネックが CPU（indices/realize など）か GPU/転送かを切り分けるため。
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator


def _env_flag(name: str) -> bool:
    value = os.environ.get(name)
    if value is None:
        return False
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return int(default)
    try:
        return int(value)
    except Exception:
        return int(default)


class _PerfSection:
    def __init__(self, perf: "PerfCollector", name: str) -> None:
        self._perf = perf
        self._name = str(name)
        self._t0_ns = 0

    def __enter__(self) -> None:
        self._t0_ns = time.perf_counter_ns()

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        dt = time.perf_counter_ns() - self._t0_ns
        self._perf._add(self._name, int(dt))


class PerfCollector:
    """フレーム区間計測の集計器。

    Notes
    -----
    無効時は全メソッドが軽量 no-op として振る舞う。
    """

    def __init__(
        self,
        *,
        enabled: bool,
        print_every: int = 60,
        gpu_finish: bool = False,
    ) -> None:
        self.enabled = bool(enabled)
        self.print_every = int(print_every) if int(print_every) > 0 else 60
        self.gpu_finish = bool(gpu_finish)

        self._window_frames = 0
        self._sum_ns: dict[str, int] = {}
        self._calls: dict[str, int] = {}

    @classmethod
    def from_env(cls) -> "PerfCollector":
        """環境変数から設定して作成する。

        - `GRAFIX_PERF=1` で有効化する。
        - `GRAFIX_PERF_EVERY=60` で何フレームごとに出力するかを指定する。
        - `GRAFIX_PERF_GPU_FINISH=1` で `ctx.finish()` を含む GPU 同期計測を有効化する。
        """
        return cls(
            enabled=_env_flag("GRAFIX_PERF"),
            print_every=_env_int("GRAFIX_PERF_EVERY", 60),
            gpu_finish=_env_flag("GRAFIX_PERF_GPU_FINISH"),
        )

    def section(self, name: str) -> contextlib.AbstractContextManager[None]:
        """`with` で囲った区間の時間を加算する。"""
        if not self.enabled:
            return contextlib.nullcontext()
        return _PerfSection(self, str(name))

    @contextlib.contextmanager
    def frame(self) -> Iterator[None]:
        """1フレーム全体の計測と周期出力を行う。"""
        if not self.enabled:
            yield
            return

        t0 = time.perf_counter_ns()
        try:
            yield
        finally:
            self._add("frame", int(time.perf_counter_ns() - t0))
            self._window_frames += 1
            if self._window_frames % self.print_every == 0:
                self._print_and_reset()

    def _add(self, name: str, dt_ns: int) -> None:
        self._sum_ns[name] = int(self._sum_ns.get(name, 0)) + int(dt_ns)
        self._calls[name] = int(self._calls.get(name, 0)) + 1

    def _print_and_reset(self) -> None:
        frames = int(self._window_frames)
        if frames <= 0:
            return

        def _ms(total_ns: int) -> float:
            return float(total_ns) / float(frames) / 1_000_000.0

        parts: list[str] = []
        frame_ns = int(self._sum_ns.get("frame", 0))
        parts.append(f"frame={_ms(frame_ns):.3f}ms")

        for name in sorted(k for k in self._sum_ns.keys() if k != "frame"):
            total_ns = int(self._sum_ns[name])
            calls = int(self._calls.get(name, 0))
            calls_per_frame = float(calls) / float(frames)
            if calls_per_frame >= 1.5:
                parts.append(f"{name}={_ms(total_ns):.3f}ms ({calls_per_frame:.1f}x)")
            else:
                parts.append(f"{name}={_ms(total_ns):.3f}ms")

        print("[grafix-perf]", " ".join(parts))

        self._window_frames = 0
        self._sum_ns.clear()
        self._calls.clear()
