# どこで: `src/grafix/interactive/runtime/window_loop.py`。
# 何を: pyglet の複数ウィンドウを 1 つの手動ループで回すための最小ランナーを提供する。
# なぜ: イベント処理→描画→flip の順序を 1 箇所に集約し、追加機能で制御フローが崩れるのを防ぐため。

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import pyglet


@dataclass(frozen=True, slots=True)
class WindowTask:
    """1つの pyglet window と「flip しない描画関数」を束ねる。"""

    # 注: pyglet の Window 型は環境/バージョン差があるため Any に寄せる。
    window: Any

    # 1フレーム分の描画処理（back buffer へ描くだけ）。
    # `switch_to()` / `flip()` は MultiWindowLoop 側が担当する前提。
    draw_frame: Callable[[], None]


class MultiWindowLoop:
    """複数ウィンドウを同一ループで回す。

    `draw_frame()` は各ウィンドウの back buffer へ描画するだけにし、`flip()` はこのループが行う。
    """

    def __init__(
        self,
        tasks: list[WindowTask],
        *,
        fps: float,
        on_frame_start: Callable[[], None] | None = None,
    ) -> None:
        """ループを初期化する。

        Parameters
        ----------
        tasks : list[WindowTask]
            1 フレームごとに描画したいウィンドウと描画処理。
        fps : float
            目標フレームレート。`<=0` の場合はスロットリングしない。
            `>0` の場合、ループ末尾で sleep して目標に近づける。
        on_frame_start : Callable[[], None] | None
            各フレーム冒頭に呼ぶコールバック。計測などの用途を想定する。
        """

        self._tasks = list(tasks)
        self._fps = float(fps)
        self._on_frame_start = on_frame_start

    def run(self) -> None:
        """ウィンドウが閉じられるまでループを実行する。"""

        # on_close ハンドラから停止させるためのフラグ。
        # （pyglet.app.run() を使わず手動ループにしているので、自前で停止条件を持つ）
        running = True
        frame_dt = 1.0 / self._fps if self._fps > 0 else 0.0
        next_frame_time = time.perf_counter()

        def stop_loop(*_: object) -> None:
            # pyglet の on_close から呼ばれるコールバックは引数が来る場合があるため *args を受ける。
            nonlocal running
            running = False

        # どれかのウィンドウを閉じたら、ループ全体を止める。
        for task in self._tasks:
            task.window.push_handlers(on_close=stop_loop)

        # 1フレームは大きく「clock 更新 → イベント処理 → 描画 → flip → sleep」の順。
        # イベント処理と描画をまとめて制御することで、複数ウィンドウ間での更新競合（点滅など）を避ける。
        while running:
            on_frame_start = self._on_frame_start
            if on_frame_start is not None:
                on_frame_start()

            # pyglet の clock を進める（内部タイムスタンプの更新など）。
            pyglet.clock.tick()

            # --- イベント処理（入力/ウィンドウ操作など）---
            # window ごとに OpenGL コンテキストを切り替えてから dispatch する。
            # （バックエンドによっては現在コンテキストを前提にする処理が混ざり得るため）
            for task in self._tasks:
                task.window.switch_to()
                task.window.dispatch_events()

            # on_close で stop_loop() が呼ばれていたらここで抜ける。
            if not running:
                break

            # has_exit が立った場合も終了する（on_close 以外の経路で exit になるケースを拾う）。
            if any(task.window.has_exit for task in self._tasks):
                break

            # --- 描画 ---
            # 重要: ここでは「描画→flip」を window 単位で必ず 1 回だけ行う。
            # サブシステム側が勝手に flip すると、空フレームが挟まって点滅しやすくなる。
            for task in self._tasks:
                task.window.switch_to()
                task.draw_frame()
                task.window.flip()

            # 目標 FPS の簡易スロットリング。
            # 重いフレームで既に遅れている場合は余計に sleep しない。
            if frame_dt > 0.0:
                next_frame_time += frame_dt
                now = time.perf_counter()
                sleep_time = next_frame_time - now
                if sleep_time > 0.0:
                    time.sleep(sleep_time)
                else:
                    next_frame_time = now
