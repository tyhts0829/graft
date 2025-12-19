"""
どこで: `src/grafix/interactive/runtime/mp_draw.py`。
何を: `draw(t)` を別プロセスで実行し、結果（Layer/観測レコード）を Queue 経由で受け渡す。
なぜ: draw が支配的なスケッチでも、メイン（イベント処理 + GL）を詰まらせずに描画を継続するため。
"""

from __future__ import annotations

import multiprocessing as mp
import multiprocessing.process as mp_process
import queue
import traceback
from dataclasses import dataclass
from typing import Callable

from grafix.core.layer import Layer
from grafix.core.parameters import FrameLabelRecord, FrameParamRecord
from grafix.core.parameters.context import parameter_context_from_snapshot
from grafix.core.scene import SceneItem, normalize_scene


@dataclass(frozen=True, slots=True)
class _DrawTask:
    frame_id: int
    t: float
    snapshot: dict
    cc_snapshot: dict[int, float] | None


@dataclass(frozen=True, slots=True)
class DrawResult:
    frame_id: int
    layers: list[Layer]
    records: list[FrameParamRecord]
    labels: list[FrameLabelRecord]
    error: str | None = None


def _draw_worker_main(
    task_q: "mp.queues.Queue[_DrawTask | None]",
    result_q: "mp.queues.Queue[DrawResult]",
    draw: Callable[[float], SceneItem],
) -> None:
    # built-in op の登録（registry）を確実に行う。
    # draw 側が `from grafix.api import G/E` を行っていないケースでも動くようにする。
    import grafix.api.effects  # noqa: F401
    import grafix.api.primitives  # noqa: F401

    while True:
        task = task_q.get()
        if task is None:
            return
        try:
            with parameter_context_from_snapshot(
                task.snapshot, cc_snapshot=task.cc_snapshot
            ) as frame_params:
                scene = draw(float(task.t))
                layers = normalize_scene(scene)
            result_q.put(
                DrawResult(
                    frame_id=int(task.frame_id),
                    layers=layers,
                    records=list(frame_params.records),
                    labels=list(frame_params.labels),
                    error=None,
                )
            )
        except Exception:
            result_q.put(
                DrawResult(
                    frame_id=int(task.frame_id),
                    layers=[],
                    records=[],
                    labels=[],
                    error=traceback.format_exc(),
                )
            )


class MpDraw:
    """draw(t) を別プロセスで実行する最小実装。"""

    def __init__(self, draw: Callable[[float], SceneItem], *, n_worker: int) -> None:
        if int(n_worker) < 2:
            raise ValueError("n_worker は 2 以上である必要がある")

        self._ctx = mp.get_context("spawn")
        self._task_q: mp.Queue[_DrawTask | None] = self._ctx.Queue(maxsize=int(n_worker))
        self._result_q: mp.Queue[DrawResult] = self._ctx.Queue()
        self._procs: list[mp_process.BaseProcess] = []

        self._next_frame_id = 0
        self._latest: DrawResult | None = None
        self._last_published_frame_id = 0

        try:
            for i in range(int(n_worker)):
                proc = self._ctx.Process(
                    target=_draw_worker_main,
                    args=(self._task_q, self._result_q, draw),
                    name=f"grafix-mp-draw-{i}",
                )
                proc.start()
                self._procs.append(proc)
        except Exception as exc:
            self.close()
            raise RuntimeError(
                "mp-draw の worker 起動に失敗しました。"
                "draw がモジュールトップレベル定義で picklable か、"
                "スケッチ側が __main__ ガードを持つか確認してください。"
            ) from exc

    def submit(
        self, *, t: float, snapshot: dict, cc_snapshot: dict[int, float] | None = None
    ) -> None:
        self._next_frame_id += 1
        task = _DrawTask(
            frame_id=self._next_frame_id,
            t=float(t),
            snapshot=snapshot,
            cc_snapshot=cc_snapshot,
        )
        try:
            self._task_q.put_nowait(task)
        except queue.Full:
            try:
                _ = self._task_q.get_nowait()
            except queue.Empty:
                return
            try:
                self._task_q.put_nowait(task)
            except queue.Full:
                return

    def poll_latest(self) -> DrawResult | None:
        best: DrawResult | None = None
        while True:
            try:
                res = self._result_q.get_nowait()
            except queue.Empty:
                break
            if best is None or int(res.frame_id) > int(best.frame_id):
                best = res

        if best is None:
            return None

        if self._latest is None or int(best.frame_id) > int(self._latest.frame_id):
            self._latest = best

        if int(self._latest.frame_id) <= int(self._last_published_frame_id):
            return None

        self._last_published_frame_id = int(self._latest.frame_id)
        return self._latest

    def latest_layers(self) -> list[Layer] | None:
        if self._latest is None or self._latest.error is not None:
            return None
        return self._latest.layers

    def close(self) -> None:
        if not self._procs:
            return

        for _ in self._procs:
            try:
                self._task_q.put_nowait(None)
            except Exception:
                pass

        for proc in self._procs:
            try:
                proc.join(timeout=1.0)
            except Exception:
                pass

        for proc in self._procs:
            if proc.is_alive():
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.join(timeout=1.0)
                except Exception:
                    pass

        self._procs.clear()
