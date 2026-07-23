"""
どこで: `src/grafix/interactive/runtime/mp_draw.py`。
何を: `draw(t)` を別プロセスで実行し、結果（Layer/観測レコード）を Queue 経由で受け渡す。
なぜ: draw が支配的なスケッチでも、メイン（イベント処理 + GL）を詰まらせずに描画を継続するため。

メインフロー
------------
1. メインプロセスが `submit()` でタスク（t と snapshot revision）を enqueue する。
   - task queue は有限なので、詰まったら古いタスクを捨てて「最新優先」にする。
   - snapshot 本体は revision 変更時だけ worker 別 control queue へ broadcast する。
   - worker の revision 適用を未確認なら task 自体にも snapshot を同梱し、ACK 待ちを
     draw 開始の barrier にしない。
2. worker プロセスが task と同じ revision の snapshot を固定して `draw(t)` を実行する。
3. worker が `DrawResult` を返し、メインは `poll_latest()` で最も新しい結果だけを採用する。
4. メインは受け取った `layers` を描画パイプライン（例: `realize_scene()`）へ渡して表示/出力する。
   - worker は draw/normalize までで、realize は行わない（mp-draw は realize の並列化ではない）。

設計上のポイント
----------------
- multiprocessing は `"spawn"` を使うため、worker は「空の Python」から起動する。
  そのため `draw` は picklable（通常はモジュールトップレベル定義）である必要がある。
- `draw` の通常例外は traceback 文字列を `DrawResult.error` に載せて返す。
- worker は初期化後に ready message を返す。`SystemExit`、native crash など process 自体の
  異常終了は submit/poll 共通の health check が `MpDrawWorkerError` として通知する。
- evaluation timeout は hung worker 世代を terminate/restart し、直近の成功結果を保持する。
- parameter 観測（FrameParamRecord/FrameLabelRecord）は worker で収集して返し、
  メイン側で当該フレームの `FrameParamsBuffer` にマージして使う（例: SceneRunner）。
"""

from __future__ import annotations

import multiprocessing as mp
import multiprocessing.process as mp_process
import multiprocessing.queues as mp_queues
import queue
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Protocol

from grafix.authoring_loader import authoring_definitions_for_draw
from grafix.core.authoring_definitions import AuthoringDefinitionsSnapshot
from grafix.core.layer import Layer
from grafix.core.parameters import EffectOrderSnapshot
from grafix.core.parameters.snapshot_ops import ParamSnapshot, materialize_snapshot
from grafix.core.parameters.source import MidiFrameSnapshot
from grafix.core.preview_quality import PreviewQuality
from grafix.core.runtime_config import RuntimeConfig
from grafix.core.scene import SceneItem
from grafix.core.value_validation import exact_integer, exact_string, finite_real
from grafix.interactive.runtime._mp_draw_protocol import (
    DrawResult,
    MpDrawStats,
    MpDrawWorkerError,
    _DrawTask,
    _SnapshotAck,
    _SnapshotUpdate,
    _TaskRejected,
    _TaskStarted,
    _WorkerMessage,
    _WorkerReady,
    decode_worker_message,
    _preview_quality,
    _require_mapping,
)
from grafix.interactive.runtime._mp_draw_state import _MpDrawState
from grafix.interactive.runtime._mp_draw_worker import _draw_worker_main

_WORKER_READY_TIMEOUT_S = 10.0
_WORKER_JOIN_TIMEOUT_S = 1.0
_WORKER_RESTART_JOIN_TIMEOUT_S = 0.05
_MAX_SUBMITTED_TIMESTAMPS = 256


class _PerfEventCallback(Protocol):
    """親 process で観測した causal event を受け取る callback。"""

    def __call__(
        self,
        name: str,
        *,
        frame_id: int | None = None,
        revision: int | None = None,
    ) -> None: ...


class MpDraw:
    """`draw(t)` を worker プロセスで実行し、最新結果を保持する。

    このクラスは「リアルタイム性（UI を止めない）」を優先するため、
    タスク/結果ともに古いフレームは積極的に捨てる設計になっている。

    - `submit()` はノンブロッキングで、混雑時は古いタスクを捨てて最新を優先する。
    - `poll_latest()` は result queue を drain して最大 `frame_id` の結果のみ採用する。
    - `latest_layers()` は後続 frame が失敗しても直近成功結果の `layers` を保持する。
    """

    def __init__(
        self,
        draw: Callable[[float], SceneItem],
        *,
        n_worker: int,
        evaluation_timeout: float | None = 5.0,
        event_callback: _PerfEventCallback | None = None,
        effective_config: RuntimeConfig,
        definitions: AuthoringDefinitionsSnapshot | None = None,
    ) -> None:
        """worker 群を起動して mp-draw を開始する。

        Parameters
        ----------
        draw : Callable[[float], SceneItem]
            スケッチの描画関数。`spawn` で渡すため picklable である必要がある。
        n_worker : int
            worker 数。1 以上。
        evaluation_timeout : float | None
            1 回の `draw(t)` を待つ秒数。超過した worker 世代を破棄して再起動する。
            `None` の場合は timeout を無効にする。
        definitions : AuthoringDefinitionsSnapshot | None
            親 session が確定済みなら同じ snapshot を渡す。その recipe だけを
            spawn へ送り、callable catalog 自体は pickle しない。

        Raises
        ------
        ValueError
            `n_worker < 1` の場合。
        MpDrawWorkerError
            ready message より前に worker が終了した場合。
        RuntimeError
            process 自体の起動に失敗した場合。
        """

        worker_count = exact_integer(n_worker, name="n_worker", minimum=1)
        if evaluation_timeout is None:
            timeout = None
        else:
            timeout = finite_real(
                evaluation_timeout,
                name="evaluation_timeout",
                minimum=0.0,
                minimum_inclusive=False,
            )

        # `spawn` は macOS での安全側（fork しない）として選ぶ。
        # 代わりに、worker へ渡す `draw` は picklable である必要がある。
        self._ctx = mp.get_context("spawn")
        self._draw = draw
        self._n_worker = worker_count
        self._evaluation_timeout = timeout
        self._event_callback = event_callback
        if not isinstance(effective_config, RuntimeConfig):
            raise TypeError("effective_config は RuntimeConfig である必要があります")
        self._effective_config = effective_config
        selected_definitions = authoring_definitions_for_draw(
            draw,
            config=effective_config,
            definitions=definitions,
        )
        authoring_recipe = selected_definitions.recipe
        if authoring_recipe is None:
            raise ValueError(
                "mp-draw には親 generation の exact authoring recipe が必要です"
            )
        self._authoring_recipe = authoring_recipe
        self._generation = 0
        self._restart_count = 0
        self._last_restart_reason: str | None = None
        self._retired_procs: list[mp_process.BaseProcess] = []
        self._closed = False

        self._next_frame_id = 0
        self._state = _MpDrawState()
        self._snapshot_broadcast_count = 0
        self._snapshot_payload_copy_count = 0
        # latest-wins queue で結果が返らない task もあるため、submit 時刻は明示的に
        # bounded とする。worker lag はこの時刻から result 到着までを測る。
        self._submitted_at_by_frame: OrderedDict[int, float] = OrderedDict()
        self._submitted_revision_by_frame: OrderedDict[int, int] = OrderedDict()

        # generation resource の作成途中でも close() が参照できる空状態を先に作る。
        # Queue 本体は作成に成功した順に属性へ束縛し、partial failure 時にも
        # 取得済み endpoint だけを確実に閉じられるようにする。
        self._procs: list[mp_process.BaseProcess] = []
        self._control_qs: list[mp.Queue[_SnapshotUpdate]] = []
        self._active_tasks_by_pid: dict[int, tuple[int, float]] = {}
        self._snapshot_broadcast_revision: int | None = None
        self._snapshot_payload_revision: int | None = None
        self._snapshot_payload: ParamSnapshot | None = None
        self._effect_order_snapshot_payload: EffectOrderSnapshot | None = None

        try:
            self._create_generation_resources()
            self._start_generation(wait_ready=True)
        except MpDrawWorkerError:
            self._close_after_start_failure()
            raise
        except Exception as exc:
            # 起動途中で失敗しても worker を残さないように後始末する。
            self._close_after_start_failure()
            raise RuntimeError(
                "mp-draw の worker 起動に失敗しました。"
                "draw がモジュールトップレベル定義で picklable か、"
                "スケッチ側が __main__ ガードを持つか確認してください。"
            ) from exc
        except BaseException:
            self._close_after_start_failure()
            raise

    def _create_generation_resources(self) -> None:
        """現在世代専用の Queue と bookkeeping を作る。"""

        # 世代ごとに Queue を分離することで、terminate 済み worker が遅れて
        # 書き込んだ message を新世代が誤って採用する経路そのものを断つ。
        self._procs = []
        self._control_qs = []
        self._state.reset_generation()
        self._active_tasks_by_pid = {}
        self._snapshot_broadcast_revision = None
        self._snapshot_payload_revision = None
        self._snapshot_payload = None
        self._effect_order_snapshot_payload = None

        self._task_q = self._ctx.Queue(maxsize=self._n_worker)
        for _ in range(self._n_worker):
            self._control_qs.append(self._ctx.Queue(maxsize=1))
        self._result_q: mp.Queue[_WorkerMessage] = self._ctx.Queue()

    def _start_generation(self, *, wait_ready: bool) -> None:
        """現在世代の worker を起動する。restart 時は ready を待たない。"""

        generation = self._generation
        for i, control_q in enumerate(self._control_qs):
            proc = self._ctx.Process(
                target=_draw_worker_main,
                args=(
                    self._task_q,
                    control_q,
                    self._result_q,
                    self._draw,
                    generation,
                    self._effective_config,
                    self._authoring_recipe,
                ),
                name=f"grafix-mp-draw-g{generation}-{i}",
            )
            # start() 自体が失敗しても constructor cleanup がこの process object を
            # 回収対象として認識できるよう、開始前に所有リストへ登録する。
            self._procs.append(proc)
            proc.start()
            if proc.pid is not None:
                self._state.register_worker(pid=proc.pid, control_index=i)
        if wait_ready:
            self._await_workers_ready()

    def _close_after_start_failure(self) -> None:
        """起動失敗の根本例外を保ったまま、取得済み resource を全て解放する。"""

        try:
            self.close()
        except BaseException:
            # close() は各既知 endpoint/process の後始末を既に試す。constructor が
            # caller へ返らない境界では cleanup 例外より起動の根本例外を優先する。
            pass

    def _reap_retired_processes(self) -> None:
        """restart 済み process の終了状態を非ブロッキングで回収する。"""

        remaining: list[mp_process.BaseProcess] = []
        for proc in self._retired_procs:
            try:
                proc.join(timeout=0.0)
            except (AssertionError, OSError, ValueError):
                pass
            if proc.is_alive():
                remaining.append(proc)
        self._retired_procs = remaining

    def _stop_current_generation(self) -> None:
        """現在世代を有界時間で強制停止し、その Queue を閉じる。"""

        procs = list(self._procs)
        task_q = self._task_q
        control_qs = list(self._control_qs)
        result_q = self._result_q

        # hang 中の user draw は sentinel を読めないため、restart では最初から
        # terminate する。join の合計時間は短く固定し、UI event loop を塞がない。
        for proc in procs:
            if proc.is_alive():
                try:
                    proc.terminate()
                except (AttributeError, OSError, ValueError):
                    pass
        self._join_processes(procs, timeout=_WORKER_RESTART_JOIN_TIMEOUT_S)
        for proc in procs:
            if proc.is_alive():
                try:
                    proc.kill()
                except (AttributeError, OSError, ValueError):
                    pass
        self._join_processes(procs, timeout=_WORKER_RESTART_JOIN_TIMEOUT_S)
        self._retired_procs.extend(proc for proc in procs if proc.is_alive())

        self._procs.clear()
        self._close_queue(task_q, cancel_pending=True)
        for control_q in control_qs:
            self._close_queue(control_q, cancel_pending=True)
        self._close_queue(result_q, cancel_pending=True)
        del self._task_q
        self._control_qs.clear()
        del self._result_q

    def restart(self, reason: str) -> int:
        """worker 世代を破棄して非同期に再起動し、新しい世代番号を返す。

        直近の成功結果は preview fallback として保持する。一方、旧世代の未完了
        task/result/snapshot 状態は引き継がない。新 worker の ready 待ちは後続の
        `submit()` / `poll_latest()` に委ね、restart 自体を有界時間に保つ。
        """

        if self._closed:
            raise RuntimeError("MpDraw は close 済みです")
        normalized_reason = exact_string(reason, name="restart reason")
        if not normalized_reason or normalized_reason != normalized_reason.strip():
            raise ValueError("restart reason は空または前後空白を含む文字列にできません")

        self._stop_current_generation()
        self._generation += 1
        self._restart_count += 1
        self._last_restart_reason = normalized_reason
        self._state.reset_received_for_generation()
        self._submitted_at_by_frame.clear()
        self._submitted_revision_by_frame.clear()

        try:
            self._create_generation_resources()
            self._start_generation(wait_ready=False)
        except Exception as exc:
            self._close_after_start_failure()
            raise RuntimeError("mp-draw の worker 再起動に失敗しました") from exc
        except BaseException:
            self._close_after_start_failure()
            raise
        return self._generation

    def _restart_if_timed_out(self) -> bool:
        """実行中 task が deadline を超えた場合に世代を再起動する。"""

        timeout = self._evaluation_timeout
        if timeout is None:
            return False
        now = time.monotonic()
        for pid, (frame_id, started_at) in tuple(self._active_tasks_by_pid.items()):
            elapsed = now - started_at
            if elapsed < timeout:
                continue
            self.restart(
                f"evaluation timeout: frame_id={frame_id}, pid={pid}, limit={timeout:.3f}s"
            )
            return True
        return False

    def _await_workers_ready(self) -> None:
        """全 worker の初期化完了を待ち、起動直後の異常終了を検知する。"""

        expected = {proc.pid for proc in self._procs if proc.pid is not None}
        deadline = time.monotonic() + _WORKER_READY_TIMEOUT_S

        while self._state.ready_worker_pids != expected:
            self._check_health()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                pending = next(
                    proc
                    for proc in self._procs
                    if proc.pid is None or proc.pid not in self._state.ready_worker_pids
                )
                error = MpDrawWorkerError(
                    worker=pending.name,
                    pid=pending.pid,
                    exitcode=pending.exitcode,
                    detail="ready message timeout",
                )
                self.close()
                raise error

            try:
                message = decode_worker_message(
                    self._result_q.get(timeout=min(0.05, remaining))
                )
            except queue.Empty:
                continue

            if (
                isinstance(message, _WorkerReady)
                and message.generation == self._generation
                and message.pid in expected
            ):
                self._state.mark_worker_ready(message.pid)

        self._check_health()

    def _check_health(self) -> None:
        """全 worker が稼働中か確認し、異常終了なら残りも閉じて例外を送出する。"""

        if self._closed:
            raise RuntimeError("MpDraw は close 済みです")

        self._reap_retired_processes()

        for proc in self._procs:
            exitcode = proc.exitcode
            if exitcode is None:
                continue
            error = MpDrawWorkerError(
                worker=proc.name,
                pid=proc.pid,
                exitcode=exitcode,
            )
            # 1 worker でも失われた時点で処理能力・結果順序の契約が変わるため、
            # 残存 worker への暗黙 fallback は行わず、全体を停止して fail-fast する。
            self.close()
            raise error

    def _drain_result_queue(self) -> None:
        """result/control message を drain し、親側の状態へ反映する。"""

        while True:
            try:
                message = decode_worker_message(self._result_q.get_nowait())
            except queue.Empty:
                return

            if isinstance(message, DrawResult):
                submitted_at = self._submitted_at_by_frame.pop(
                    message.frame_id,
                    None,
                )
                if submitted_at is not None:
                    message = replace(
                        message,
                        worker_lag_ms=max(
                            0.0,
                            (time.monotonic() - submitted_at) * 1_000.0,
                        ),
                    )

            current_generation = self._generation
            message_generation = message.generation
            if message_generation != current_generation:
                if isinstance(message, DrawResult):
                    self._state.record_stale_generation(
                        message,
                        current_generation=current_generation,
                    )
                continue

            if isinstance(message, _WorkerReady):
                self._state.mark_worker_ready(message.pid)
            elif isinstance(message, _SnapshotAck):
                self._state.apply_snapshot_ack(message)
                self._record_event(
                    "mp_snapshot_applied",
                    revision=message.applied_revision,
                )
            elif isinstance(message, _TaskRejected):
                self._state.record_rejection(message)
            elif isinstance(message, _TaskStarted):
                self._active_tasks_by_pid[message.pid] = (
                    message.frame_id,
                    time.monotonic(),
                )
                self._record_event(
                    "mp_task_started",
                    frame_id=message.frame_id,
                    revision=self._submitted_revision_by_frame.get(message.frame_id),
                )
            else:
                worker_pid = message.worker_pid
                if worker_pid is not None:
                    active = self._active_tasks_by_pid.get(worker_pid)
                    if active is not None and active[0] == message.frame_id:
                        self._active_tasks_by_pid.pop(worker_pid, None)
                self._submitted_revision_by_frame.pop(
                    message.frame_id,
                    None,
                )
                if not self._state.accept_result(message):
                    # worker error であっても旧 timeline の結果なら現在の preview
                    # failure として通知しない。破棄理由は worker health error と
                    # 区別できるよう診断値へ残す。
                    continue

    def _plain_snapshot_for_revision(
        self,
        *,
        revision: int,
        snapshot: ParamSnapshot,
        effect_order_snapshot: EffectOrderSnapshot,
    ) -> tuple[ParamSnapshot, EffectOrderSnapshot]:
        """同じrevisionのqueue用parameter/order snapshotを一度だけ構築する。"""

        normalized_revision = exact_integer(
            revision,
            name="revision",
            minimum=0,
        )
        cached = self._snapshot_payload
        if cached is not None and self._snapshot_payload_revision == normalized_revision:
            cached_order = self._effect_order_snapshot_payload
            assert cached_order is not None
            return cached, cached_order
        payload: ParamSnapshot = materialize_snapshot(snapshot)
        order_payload: EffectOrderSnapshot = dict(effect_order_snapshot)
        self._snapshot_payload_revision = normalized_revision
        self._snapshot_payload = payload
        self._effect_order_snapshot_payload = order_payload
        self._snapshot_payload_copy_count += 1
        self._record_event(
            "parameter_snapshot_built",
            revision=normalized_revision,
        )
        return payload, order_payload

    def _broadcast_snapshot(
        self,
        *,
        revision: int,
        snapshot: ParamSnapshot,
        effect_order_snapshot: EffectOrderSnapshot,
    ) -> None:
        """snapshot 本体を各 worker へ 1 回ずつ配信する。"""

        # `snapshot` は submit() が revision ごとに一度だけ plain dict 化した payload。
        # task 同梱分と control 配布分で全 ParamStore を二重コピーしない。
        update = _SnapshotUpdate(
            revision=revision,
            snapshot=snapshot,
            effect_order_snapshot=effect_order_snapshot,
            generation=self._generation,
        )
        # worker ごとに「queue 内 1 件 + 親側 latest 1 件」だけを保持する。
        # 既に古い update が queue にある場合は、それが ack された後に latest を送る。
        self._state.queue_snapshot_update(
            update,
            worker_count=len(self._control_qs),
        )
        self._flush_snapshot_updates()
        self._snapshot_broadcast_revision = revision
        self._snapshot_broadcast_count += 1
        self._record_event(
            "mp_snapshot_sent",
            revision=revision,
        )

    def _record_event(
        self,
        name: str,
        *,
        frame_id: int | None = None,
        revision: int | None = None,
    ) -> None:
        """有効な callback があれば main-process event を転送する。"""

        callback = self._event_callback
        if callback is not None:
            callback(
                name,
                frame_id=frame_id,
                revision=revision,
            )

    def _flush_snapshot_updates(self) -> None:
        """各 worker の空いた control queue へ pending latest を 1 件だけ送る。"""

        for index, update in self._state.snapshot_updates_ready_to_send():
            try:
                self._control_qs[index].put_nowait(update)
            except queue.Full:
                # feeder/reader 間の短い race。次の submit/poll で再試行する。
                continue
            self._state.mark_snapshot_update_queued(
                index=index,
                revision=update.revision,
            )

    def _workers_have_revision(self, revision: int) -> bool:
        return self._state.workers_have_revision(revision)

    def _enqueue_latest(self, task: _DrawTask) -> bool:
        """有限 task queue へ latest-wins で投入し、成功したかを返す。"""

        try:
            self._task_q.put_nowait(task)
            self._state.mark_pending_task_enqueued(task)
            return True
        except queue.Full:
            try:
                dropped = self._task_q.get_nowait()
            except queue.Empty:
                # multiprocessing.Queue は feeder thread が pipe へ反映するまで、
                # `full()` 相当の semaphore と `get_nowait()` の可視性が一瞬ずれる。
                # 呼び出し側が task を pending latest として保持し、次の poll/submit
                # で再試行できるよう失敗を返す。
                return False
            if isinstance(dropped, _DrawTask):
                self._state.record_queue_drop()
            try:
                self._task_q.put_nowait(task)
                self._state.mark_pending_task_enqueued(task)
                return True
            except queue.Full:
                return False

    def _enqueue_pending(self) -> None:
        task = self._state.pending_task
        if task is None:
            return
        self._enqueue_latest(task)

    def begin_epoch(self, epoch: int | None = None) -> int:
        """新しい transport epoch へ進み、旧結果を表示候補から外す。

        Parameters
        ----------
        epoch:
            明示する場合は現在値以上でなければならない。省略時は 1 増やす。

        Returns
        -------
        int
            適用後の epoch。

        Notes
        -----
        実行中の worker task 自体は安全に中断できないため完了を許すが、その
        result は `_drain_result_queue()` で stale として破棄する。queue 内で
        まだ開始していない旧 task は best-effort で除去し、fresh task を優先する。
        """

        self._check_health()
        current = self._state.current_epoch
        requested = current + 1 if epoch is None else exact_integer(epoch, name="epoch", minimum=0)
        if requested <= current:
            self._state.begin_epoch(requested)
            return self._state.current_epoch

        # 既に到着済みの current result を accounting してから境界を進める。
        self._drain_result_queue()
        if not self._state.begin_epoch(requested):
            return self._state.current_epoch

        while True:
            try:
                queued = self._task_q.get_nowait()
            except queue.Empty:
                break
            if queued is None:
                # close sentinel を利用中に epoch 更新する呼び出しは通常ないが、
                # 見つけた場合は失わないよう戻す。
                try:
                    self._task_q.put_nowait(None)
                except queue.Full:
                    pass
                break
            self._state.record_queue_drop()
        return self._state.current_epoch

    def submit(
        self,
        *,
        t: float,
        snapshot_revision: int,
        snapshot: ParamSnapshot,
        effect_order_snapshot: EffectOrderSnapshot,
        cc_snapshot: MidiFrameSnapshot | None = None,
        epoch: int,
        quality: PreviewQuality,
    ) -> None:
        """このフレームの draw を worker に依頼する（ノンブロッキング）。

        Notes
        -----
        task queue が満杯のときは、最も古いタスクを 1 つ捨てて再投入する。
        それでも入らなければ、このフレームは諦める（UI を止めないことを優先）。

        Raises
        ------
        MpDrawWorkerError
            worker が予期せず終了していた場合。
        RuntimeError
            close 後に呼び出した場合。
        """

        self._check_health()
        try:
            render_t = finite_real(t, name="t")
            revision = exact_integer(
                snapshot_revision,
                name="snapshot_revision",
                minimum=0,
            )
            requested_epoch = exact_integer(epoch, name="epoch", minimum=0)
            preview_quality = _preview_quality(quality)
            _require_mapping(snapshot, name="snapshot")
            _require_mapping(
                effect_order_snapshot,
                name="effect_order_snapshot",
            )
            if cc_snapshot is not None and not isinstance(
                cc_snapshot,
                MidiFrameSnapshot,
            ):
                raise TypeError("cc_snapshot は MidiFrameSnapshot または None である必要があります")
            if requested_epoch < self._state.current_epoch:
                raise ValueError(
                    "古い epoch の draw task は投入できません: "
                    f"current={self._state.current_epoch}, got={requested_epoch}"
                )
            if requested_epoch > self._state.current_epoch:
                self.begin_epoch(requested_epoch)
            # 前フレームまでの ACK を先に反映する。ACK は snapshot 省略の最適化に
            # だけ使い、task enqueue の correctness barrier にはしない。
            self._drain_result_queue()
            self._restart_if_timed_out()
            self._flush_snapshot_updates()
            self._next_frame_id += 1
            worker_snapshot_confirmed = self._workers_have_revision(revision)
            needs_control_broadcast = (
                self._n_worker > 1 and self._snapshot_broadcast_revision != revision
            )
            payload = (
                None
                if worker_snapshot_confirmed and not needs_control_broadcast
                else self._plain_snapshot_for_revision(
                    revision=revision,
                    snapshot=snapshot,
                    effect_order_snapshot=effect_order_snapshot,
                )
            )
            parameter_payload = None if payload is None else payload[0]
            order_payload = None if payload is None else payload[1]
            task = _DrawTask(
                frame_id=self._next_frame_id,
                t=render_t,
                snapshot_revision=revision,
                cc_snapshot=cc_snapshot,
                snapshot=(None if worker_snapshot_confirmed else parameter_payload),
                effect_order_snapshot=(None if worker_snapshot_confirmed else order_payload),
                quality=preview_quality,
                epoch=requested_epoch,
                generation=self._generation,
            )
            submitted_at = self._submitted_at_by_frame
            submitted_at[task.frame_id] = time.monotonic()
            while len(submitted_at) > _MAX_SUBMITTED_TIMESTAMPS:
                expired_frame_id, _ = submitted_at.popitem(last=False)
                self._submitted_revision_by_frame.pop(expired_frame_id, None)
            self._submitted_revision_by_frame[task.frame_id] = revision
            if task.snapshot is not None and not needs_control_broadcast:
                # 1-worker の task-carried snapshot も control broadcast と同じ
                # causal stage として扱う。payload は task と同時に一度だけ送られる。
                self._record_event(
                    "mp_snapshot_sent",
                    frame_id=task.frame_id,
                    revision=revision,
                )
            if needs_control_broadcast:
                assert payload is not None
                self._broadcast_snapshot(
                    revision=revision,
                    snapshot=payload[0],
                    effect_order_snapshot=payload[1],
                )
            elif self._n_worker == 1:
                # 1 worker では task-carried snapshot の apply ACK だけで全 worker が
                # current になる。control queue へ同じ payload を重複送信しない。
                self._snapshot_broadcast_revision = revision

            # queue の feeder race で即時投入できなくても、最新 task を親側に
            # 1 件だけ保持し、次の submit/poll で再試行する。
            self._state.replace_pending_task(task)
            self._enqueue_pending()
        finally:
            # enqueue 中に worker が落ちても次の frame まで空画面で待たない。
            self._check_health()

    def poll_latest(self) -> DrawResult | None:
        """worker から届いた結果を回収し、最新フレームの結果があれば返す。

        Returns
        -------
        DrawResult | None
            前回呼び出し以降に「より新しい frame_id の結果」が届いていればそれを返す。
            何も届いていない場合や、届いたが既に返した frame_id の場合は None。

        Notes
        -----
        result queue を drain して最大 `frame_id` の結果だけを採用するため、
        中間フレームの結果は捨てられる（リアルタイム性優先）。

        Raises
        ------
        MpDrawWorkerError
            worker が予期せず終了していた場合。
        RuntimeError
            close 後に呼び出した場合。
        """

        self._check_health()

        self._drain_result_queue()
        self._restart_if_timed_out()
        self._flush_snapshot_updates()
        self._enqueue_pending()

        # drain 中に process が終了した場合も、その場で明示的に失敗させる。
        self._check_health()

        return self._state.publish_latest()

    @property
    def stats(self) -> MpDrawStats:
        """main-thread owner の現在状態を一つの immutable snapshot で返す。

        この class の command と同じ main thread から読む契約なので、統計専用の
        lock は持たない。method 内で参照した state/root field は一つの観測値として
        ``MpDrawStats`` へ固定される。
        """

        state = self._state
        ack = state.last_snapshot_ack
        last_snapshot_ack = (
            None
            if ack is None
            else (ack.requested_revision, ack.applied_revision, ack.status)
        )
        rejection = state.last_rejection
        last_rejection = (
            None
            if rejection is None
            else (
                rejection.requested_revision,
                rejection.applied_revision,
                rejection.reason,
            )
        )
        return MpDrawStats(
            snapshot_broadcast_count=self._snapshot_broadcast_count,
            last_submitted_frame_id=self._next_frame_id,
            snapshot_ack_count=state.snapshot_ack_count,
            snapshot_payload_copy_count=self._snapshot_payload_copy_count,
            worker_snapshot_revisions=tuple(
                sorted(state.worker_snapshot_revisions.items())
            ),
            ready_worker_pids=state.ready_worker_pids,
            last_snapshot_ack=last_snapshot_ack,
            rejected_task_count=state.rejected_task_count,
            task_enqueue_count=state.task_enqueue_count,
            task_drop_count=state.task_drop_count,
            completed_result_count=state.completed_result_count,
            current_epoch=state.current_epoch,
            generation=self._generation,
            restart_count=self._restart_count,
            last_restart_reason=self._last_restart_reason,
            evaluation_timeout=self._evaluation_timeout,
            stale_result_count=state.stale_result_count,
            last_stale_result=state.last_stale_result,
            stale_generation_result_count=state.stale_generation_result_count,
            last_stale_generation_result=state.last_stale_generation_result,
            pending_snapshot_update_count=state.pending_snapshot_update_count,
            queued_snapshot_update_count=state.queued_snapshot_update_count,
            last_rejection=last_rejection,
        )

    @property
    def current_epoch(self) -> int:
        """現在採用対象としている transport epoch を返す。"""

        return self._state.current_epoch

    @property
    def generation(self) -> int:
        """現在の worker 世代番号を返す。"""

        return self._generation

    @property
    def evaluation_timeout(self) -> float | None:
        """現在設定されている evaluation timeout（秒）を返す。"""

        return self._evaluation_timeout

    def latest_layers(self) -> tuple[Layer, ...] | None:
        """直近の成功結果の layers を返す（成功結果が未到着なら None）。

        error result は `poll_latest()` で通知するが、ここでは直前の成功結果を保持する。
        これにより一時的な user draw の失敗で preview が空になることを防ぐ。
        """

        latest = self._state.latest_successful
        if latest is None:
            return None
        return latest.layers

    def latest_successful_result(self) -> DrawResult | None:
        """直近の成功結果を、その入力時刻と共に返す。

        `poll_latest()` は後続の error result を返す場合があるが、
        preview/export は実際に採用した最新成功 frame の `t` を必要とする。
        layers と `t` の対応を分離せず取得できるよう、
        `latest_layers()` とは別に結果全体を公開する。
        """

        return self._state.latest_successful

    def _send_stop_tokens(self, count: int) -> None:
        """待機中の古い task を捨てながら、通常終了用 sentinel を送る。"""

        sent = 0
        deadline = time.monotonic() + 0.5
        while sent < count and time.monotonic() < deadline:
            try:
                self._task_q.put(None, timeout=0.05)
                sent += 1
            except queue.Full:
                try:
                    _ = self._task_q.get_nowait()
                except queue.Empty:
                    pass
            except (OSError, ValueError):
                return

    @staticmethod
    def _join_processes(procs: list[mp_process.BaseProcess], *, timeout: float) -> None:
        """複数 process を合計 `timeout` 秒まで待つ。"""

        deadline = time.monotonic() + timeout
        for proc in procs:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                proc.join(timeout=remaining)
            except (AssertionError, OSError, ValueError):
                pass

    @staticmethod
    def _close_queue(worker_queue: mp_queues.Queue[Any], *, cancel_pending: bool = False) -> None:
        """Queue endpoint と feeder thread を閉じる。"""

        if cancel_pending:
            # 読み手が異常終了した task queue は feeder が pipe write で詰まり得る。
            # 未処理 task は破棄対象なので、join を cancel して close を有界に保つ。
            try:
                worker_queue.cancel_join_thread()
            except (AttributeError, OSError, ValueError):
                pass
        try:
            worker_queue.close()
        except (OSError, ValueError):
            pass
        try:
            worker_queue.join_thread()
        except (AssertionError, OSError, ValueError):
            pass

    def close(self) -> None:
        """worker と Queue を終了する（複数回呼んでもよい）。"""

        if self._closed:
            return
        # health check より先に正常終了状態へ遷移し、sentinel による exitcode=0 を
        # 「予期せぬ worker death」と誤認しないようにする。
        self._closed = True

        procs = list(self._procs)
        retired_procs = list(self._retired_procs)
        all_procs = procs + retired_procs
        task_q = getattr(self, "_task_q", None)
        result_q = getattr(self, "_result_q", None)
        if task_q is not None:
            self._send_stop_tokens(sum(proc.is_alive() for proc in procs))
        self._join_processes(procs, timeout=_WORKER_JOIN_TIMEOUT_S)

        # draw が停止しない worker だけを強制終了する。
        for proc in all_procs:
            if proc.is_alive():
                try:
                    proc.terminate()
                except (AttributeError, OSError, ValueError):
                    pass
        self._join_processes(all_procs, timeout=_WORKER_JOIN_TIMEOUT_S)

        # terminate にも反応しない場合を残さない。kill 後は timeout 無し join を避け、
        # close 自体が UI thread を永久に止めないようにする。
        for proc in all_procs:
            if proc.is_alive():
                try:
                    proc.kill()
                except (AttributeError, OSError, ValueError):
                    pass
        self._join_processes(all_procs, timeout=_WORKER_JOIN_TIMEOUT_S)

        clean_shutdown = all(proc.exitcode == 0 for proc in procs)
        self._procs.clear()
        self._retired_procs.clear()
        if task_q is not None:
            self._close_queue(task_q, cancel_pending=not clean_shutdown)
        for control_q in self._control_qs:
            self._close_queue(control_q, cancel_pending=not clean_shutdown)
        self._control_qs.clear()
        self._state.clear_queue_state()
        if result_q is not None:
            self._close_queue(result_q, cancel_pending=not clean_shutdown)


__all__ = ["DrawResult", "MpDraw", "MpDrawStats", "MpDrawWorkerError"]
