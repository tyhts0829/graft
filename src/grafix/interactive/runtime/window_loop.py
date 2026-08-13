"""
Purpose:
    previewとInspectorを一つのpyglet app loopで順序付けてpresentする。
Use when:
    multi-window event配送、frame scheduling、flip、close requestの意味を変更するとき。
Constraints:
    - taskのdraw_frameはback bufferだけを描き、switch/on_draw/refresh/flipはWindow.drawへ委ねる。
    - close requestはtask固有handlerで処理し、default handlerに先行してcontextを破棄させない。
    - 非表示または閉じたwindowを描画対象に含めない。
    - 登録したschedule callbackはloop終了時に必ずunscheduleする。
Side effects:
    pygletのglobal event loop、clock callback、window event handlerを操作する。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import pyglet

from grafix.core.value_validation import finite_real


@dataclass(frozen=True, slots=True)
class WindowTask:
    """1つの pyglet window と描画・close の方針を束ねる。"""

    # 注: pyglet の Window 型は環境/バージョン差があるため Any に寄せる。
    window: Any

    # 1フレーム分の描画処理（back buffer へ描くだけ）。
    # `switch_to()` / `flip()` は pyglet（`Window.draw()`）が担当する前提。
    draw_frame: Callable[[], None]

    # OS の close request を受けたときの方針。preview は app exit、
    # Inspector は context を破棄せず hide するなど、配線側が明示する。
    on_close: Callable[[], None]

    # `Window.draw()` は on_draw と flip を含む。完了後の経過時間を受け取る。
    on_presented: Callable[[int], None] | None = None

    def __post_init__(self) -> None:
        if not callable(self.draw_frame):
            raise TypeError("draw_frame は callable である必要があります")
        if not callable(self.on_close):
            raise TypeError("on_close は callable である必要があります")
        if self.on_presented is not None and not callable(self.on_presented):
            raise TypeError(
                "on_presented は callable または None である必要があります"
            )


class MultiWindowLoop:
    """複数ウィンドウを同一ループで回す。

    `draw_frame()` は各ウィンドウの back buffer へ描画するだけにし、`flip()` は pyglet が行う。
    """

    def __init__(
        self,
        tasks: tuple[WindowTask, ...],
        *,
        fps: float,
        on_frame_start: Callable[[], None] | None = None,
        on_frame_finished: Callable[[int], None] | None = None,
        on_scheduler_jitter: Callable[[int], None] | None = None,
    ) -> None:
        """ループを初期化する。

        Parameters
        ----------
        tasks : tuple[WindowTask, ...]
            1 フレームごとに描画したいウィンドウと描画処理。
        fps : float
            目標フレームレート。`<=0` の場合はスロットリングしない。
            `>0` の場合、`pyglet.clock.schedule_interval` で描画頻度を制御する。
        on_frame_start : Callable[[], None] | None
            各フレーム冒頭に呼ぶコールバック。計測などの用途を想定する。
        on_frame_finished : Callable[[int], None] | None
            全 window の draw / flip 完了後に、tick 全体の経過 ns を渡す。
        on_scheduler_jitter : Callable[[int], None] | None
            連続 tick の開始間隔と目標間隔の絶対差 ns を渡す。
        """

        if type(tasks) is not tuple or any(
            not isinstance(task, WindowTask)
            for task in tasks
        ):
            raise TypeError("tasks は WindowTask の tuple である必要があります")
        if not tasks:
            raise ValueError("tasks は1件以上必要です")
        frame_rate = finite_real(fps, name="fps")
        for name, callback in (
            ("on_frame_start", on_frame_start),
            ("on_frame_finished", on_frame_finished),
            ("on_scheduler_jitter", on_scheduler_jitter),
        ):
            if callback is not None and not callable(callback):
                raise TypeError(
                    f"{name} は callable または None である必要があります"
                )

        self._tasks = tasks
        self._fps = frame_rate
        self._on_frame_start = on_frame_start
        self._on_frame_finished = on_frame_finished
        self._on_scheduler_jitter = on_scheduler_jitter

    def run(self) -> None:
        """ウィンドウが閉じられるまでループを実行する。"""

        tasks = self._tasks

        def close_handler(task: WindowTask) -> Callable[..., object]:
            def handle_close(*_: object) -> object:
                # EVENT_HANDLED を返さないと default on_close が context/window を
                # 直ちに破棄し、runner の teardown より先になる場合がある。
                task.on_close()
                return pyglet.event.EVENT_HANDLED

            return handle_close

        # close の結果は task ごとに異なる。default handler には委ねない。
        for task in tasks:
            task.window.push_handlers(on_close=close_handler(task))

        # 各ウィンドウの on_draw で、そのウィンドウの描画処理を行う。
        for task in tasks:
            task.window.push_handlers(on_draw=task.draw_frame)

        # 1フレームは大きく「frame start → Window.draw（on_draw→flip）」の順で進める。
        # Window.draw は switch_to / on_draw / on_refresh / flip をまとめて行う。
        previous_frame_started_ns: int | None = None
        target_interval_ns = (
            0
            if self._fps <= 0.0
            else int(round(1_000_000_000.0 / self._fps))
        )

        def draw_all(dt: float) -> None:
            nonlocal previous_frame_started_ns
            loop_started_ns = time.perf_counter_ns()
            previous = previous_frame_started_ns
            previous_frame_started_ns = loop_started_ns
            on_scheduler_jitter = self._on_scheduler_jitter
            if (
                on_scheduler_jitter is not None
                and previous is not None
                and target_interval_ns > 0
            ):
                on_scheduler_jitter(
                    abs(
                        loop_started_ns
                        - previous
                        - target_interval_ns
                    )
                )
            on_frame_start = self._on_frame_start
            if on_frame_start is not None:
                on_frame_start()

            try:
                for task in tasks:
                    # 閉じられた window と hide 中の window は描画しない。
                    if task.window not in pyglet.app.windows or not bool(
                        task.window.visible
                    ):
                        continue
                    window_started_ns = time.perf_counter_ns()
                    task.window.draw(dt)
                    on_presented = task.on_presented
                    if on_presented is not None:
                        on_presented(
                            time.perf_counter_ns() - window_started_ns
                        )
            finally:
                on_frame_finished = self._on_frame_finished
                if on_frame_finished is not None:
                    on_frame_finished(
                        time.perf_counter_ns() - loop_started_ns
                    )

        # fps<=0 は「スロットリング無し（可能な限り回す）」として扱う。
        if self._fps <= 0:
            pyglet.clock.schedule(draw_all)
        else:
            pyglet.clock.schedule_interval(draw_all, 1.0 / self._fps)

        try:
            pyglet.app.run(interval=None)
        finally:
            pyglet.clock.unschedule(draw_all)
