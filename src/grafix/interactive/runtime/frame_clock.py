# どこで: `src/grafix/interactive/runtime/frame_clock.py`。
# 何を: `draw(t)` に渡すフレーム時刻 `t` の生成規則を提供する。
# なぜ: 「通常は実時間」「録画中は固定 fps のタイムライン」を分離して見通しを良くするため。

from __future__ import annotations

import time


class RealTimeClock:
    """実時間ベースのフレーム時計。

    Notes
    -----
    `t` は `perf_counter()` の差分（秒）。
    """

    def __init__(self, *, start_time: float) -> None:
        self._start_time = float(start_time)

    def t(self) -> float:
        """現在のフレーム時刻 `t`（秒）を返す。"""

        return float(time.perf_counter() - self._start_time)

    def tick(self) -> None:
        """フレームを進める（実時間では no-op）。"""

        return


class RecordingClock:
    """録画タイムラインのフレーム時計。

    Notes
    -----
    `t` は `t0 + frame_index/fps`。
    実時間と切り離し、録画データ側の fps を維持するために使う。
    """

    def __init__(self, *, t0: float, fps: float) -> None:
        _fps = float(fps)
        if _fps <= 0:
            raise ValueError("fps は正の値である必要がある")
        self._t0 = float(t0)
        self._fps = _fps
        self._frame_index = 0

    @property
    def fps(self) -> float:
        """録画 fps を返す。"""

        return float(self._fps)

    @property
    def frame_index(self) -> int:
        """現在のフレーム番号（0-based）を返す。"""

        return int(self._frame_index)

    def t(self) -> float:
        """現在のフレーム時刻 `t`（秒）を返す。"""

        return float(self._t0 + float(self._frame_index) / float(self._fps))

    def tick(self) -> None:
        """フレームを 1 つ進める。"""

        self._frame_index += 1

