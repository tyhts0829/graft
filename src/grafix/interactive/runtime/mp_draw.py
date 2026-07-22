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
import os
import queue
import time
import traceback
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Callable, Protocol, cast

from grafix.core.authoring_definitions import AuthoringDefinitionsSnapshot
from grafix.core.authoring_loader import (
    authoring_definitions_for_draw,
    load_authoring_definitions_recipe,
)
from grafix.core.authoring_recipe import AuthoringDefinitionsRecipe
from grafix.core.layer import Layer
from grafix.core.operation_diagnostics import (
    OperationDiagnostic,
    current_operation_diagnostics,
)
from grafix.core.parameters import (
    EffectOrderSnapshot,
    FrameEffectChainRecord,
    FrameLabelRecord,
    FrameParamRecord,
)
from grafix.core.parameters.context import parameter_context_from_snapshot
from grafix.core.parameters.snapshot_ops import ParamSnapshot, materialize_snapshot
from grafix.core.parameters.source import MidiFrameSnapshot
from grafix.core.operation_catalog import bind_operation_catalog
from grafix.core.preview_quality import PreviewQuality, preview_quality_context
from grafix.core.preset_catalog import bind_preset_catalog
from grafix.core.runtime_config import (
    RuntimeConfig,
    bind_runtime_config,
)
from grafix.core.scene import SceneItem, normalize_scene
from grafix.core.value_validation import (
    exact_integer,
    exact_string,
    exact_string_choice,
    finite_real,
)

_WORKER_READY_TIMEOUT_S = 10.0
_WORKER_JOIN_TIMEOUT_S = 1.0
_WORKER_RESTART_JOIN_TIMEOUT_S = 0.05
_MAX_SUBMITTED_TIMESTAMPS = 256


def _non_empty_string(value: object, *, name: str) -> str:
    """暗黙文字列化を行わず、空白だけでない文字列を返す。"""

    text = exact_string(value, name=name)
    if not text.strip():
        raise ValueError(f"{name} は空にできません")
    return text


def _preview_quality(value: object) -> PreviewQuality:
    """process 境界で受け付ける preview quality 一形を返す。"""

    return cast(
        PreviewQuality,
        exact_string_choice(
            value,
            name="quality",
            choices=("draft", "final"),
        ),
    )


def _require_mapping(value: object, *, name: str) -> None:
    """公開 submit が受け取る snapshot の Mapping 契約を検証する。"""

    if not isinstance(value, Mapping):
        raise TypeError(f"{name} は Mapping である必要があります")


def _require_plain_dict(value: object, *, name: str) -> None:
    """Queue に載せる materialize 済み snapshot の dict 契約を検証する。"""

    if type(value) is not dict:
        raise TypeError(f"{name} は plain dict である必要があります")


def _require_tuple_of(
    value: object,
    *,
    name: str,
    item_type: type[object],
) -> None:
    """process message の immutable tuple と要素型を検証する。"""

    if type(value) is not tuple or not all(isinstance(item, item_type) for item in value):
        raise TypeError(f"{name} は {item_type.__name__} の tuple である必要があります")


class _PerfEventCallback(Protocol):
    """親 process で観測した causal event を受け取る callback。"""

    def __call__(
        self,
        name: str,
        *,
        frame_id: int | None = None,
        revision: int | None = None,
    ) -> None: ...


class MpDrawWorkerError(RuntimeError):
    """mp-draw worker が予期せず終了したことを表す。"""

    def __init__(
        self,
        *,
        worker: str,
        pid: int | None,
        exitcode: int | None,
        detail: str | None = None,
    ) -> None:
        self.worker = _non_empty_string(worker, name="worker")
        self.pid = None if pid is None else exact_integer(pid, name="pid", minimum=1)
        self.exitcode = None if exitcode is None else exact_integer(exitcode, name="exitcode")
        self.detail = None if detail is None else exact_string(detail, name="detail")
        message = (
            "mp-draw worker が予期せず終了しました: "
            f"worker={self.worker!r}, pid={self.pid}, exitcode={self.exitcode}"
        )
        if self.detail is not None:
            message = f"{message} ({self.detail})"
        super().__init__(message)


@dataclass(frozen=True, slots=True, kw_only=True)
class _DrawTask:
    """親 process が検証済みの 1 フレーム分入力。

    Notes
    -----
    - private producer である :meth:`MpDraw.submit` が境界検証を所有する。
    - worker が revision を適用済みと確認できた通常時は snapshot を省略する。
    - 未確認時は snapshot を同梱し、control queue の ACK より先に評価を進める。
    - `frame_id` はメインプロセス側で単調増加し、結果の新旧判定に使う。
    """

    frame_id: int
    t: float
    snapshot_revision: int
    cc_snapshot: MidiFrameSnapshot | None
    snapshot: ParamSnapshot | None
    effect_order_snapshot: EffectOrderSnapshot | None
    epoch: int
    generation: int
    quality: PreviewQuality


@dataclass(frozen=True, slots=True, kw_only=True)
class _SnapshotUpdate:
    """worker ごとに broadcast する parameter snapshot 更新。"""

    revision: int
    snapshot: ParamSnapshot
    effect_order_snapshot: EffectOrderSnapshot
    generation: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "revision",
            exact_integer(self.revision, name="revision", minimum=0),
        )
        object.__setattr__(
            self,
            "generation",
            exact_integer(self.generation, name="generation", minimum=0),
        )
        _require_plain_dict(self.snapshot, name="snapshot")
        _require_plain_dict(
            self.effect_order_snapshot,
            name="effect_order_snapshot",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class _SnapshotAck:
    """worker が snapshot 更新を処理したことを親へ通知する。"""

    worker: str
    pid: int
    requested_revision: int
    applied_revision: int
    status: str
    generation: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "worker",
            _non_empty_string(self.worker, name="worker"),
        )
        object.__setattr__(
            self,
            "pid",
            exact_integer(self.pid, name="pid", minimum=1),
        )
        object.__setattr__(
            self,
            "requested_revision",
            exact_integer(
                self.requested_revision,
                name="requested_revision",
                minimum=0,
            ),
        )
        object.__setattr__(
            self,
            "applied_revision",
            exact_integer(
                self.applied_revision,
                name="applied_revision",
                minimum=0,
            ),
        )
        object.__setattr__(
            self,
            "status",
            exact_string_choice(
                self.status,
                name="status",
                choices=("applied", "current", "stale"),
            ),
        )
        object.__setattr__(
            self,
            "generation",
            exact_integer(self.generation, name="generation", minimum=0),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class _TaskRejected:
    """worker が未知または古い snapshot revision の task を拒否した通知。"""

    frame_id: int
    worker: str
    pid: int
    requested_revision: int
    applied_revision: int | None
    reason: str
    generation: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "frame_id",
            exact_integer(self.frame_id, name="frame_id", minimum=1),
        )
        object.__setattr__(
            self,
            "worker",
            _non_empty_string(self.worker, name="worker"),
        )
        object.__setattr__(
            self,
            "pid",
            exact_integer(self.pid, name="pid", minimum=1),
        )
        object.__setattr__(
            self,
            "requested_revision",
            exact_integer(
                self.requested_revision,
                name="requested_revision",
                minimum=0,
            ),
        )
        if self.applied_revision is not None:
            object.__setattr__(
                self,
                "applied_revision",
                exact_integer(
                    self.applied_revision,
                    name="applied_revision",
                    minimum=0,
                ),
            )
        object.__setattr__(
            self,
            "reason",
            exact_string_choice(
                self.reason,
                name="reason",
                choices=("unknown", "stale"),
            ),
        )
        object.__setattr__(
            self,
            "generation",
            exact_integer(self.generation, name="generation", minimum=0),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class _TaskStarted:
    """worker が evaluation を開始したことを親へ通知する。"""

    frame_id: int
    worker: str
    pid: int
    generation: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "frame_id",
            exact_integer(self.frame_id, name="frame_id", minimum=1),
        )
        object.__setattr__(
            self,
            "worker",
            _non_empty_string(self.worker, name="worker"),
        )
        object.__setattr__(
            self,
            "pid",
            exact_integer(self.pid, name="pid", minimum=1),
        )
        object.__setattr__(
            self,
            "generation",
            exact_integer(self.generation, name="generation", minimum=0),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class DrawResult:
    """worker からメインへ返す 1 フレーム分の結果。

    Notes
    -----
    - `layers` は `draw(t)` の戻り値を `normalize_scene()` で正規化したもの。
    - `records` / `labels` は draw 実行中に観測した parameter 情報で、メイン側の
      `FrameParamsBuffer` にマージして GUI/記録に使う（メインの ParamStore は触らない）。
    - `diagnostics` は operation の clamp/reject 等を表し、parameter 観測とは分離する。
    - `error` が非 None の場合、`layers/records/labels` は空で、`error` には
      `traceback.format_exc()` の文字列が入る。
    - `t` はこの結果を生成した task の時刻で、非同期 preview の capture metadata に使う。
    - `epoch` は transport discontinuity の識別子。現在より古い結果は親側で破棄する。
    - `generation` は timeout/restart をまたぐ worker 世代。旧世代の結果は親側で破棄する。
    - `snapshot_revision` は worker が実際に評価へ使った parameter snapshot の revision。
    """

    frame_id: int
    t: float
    epoch: int
    generation: int
    snapshot_revision: int
    layers: tuple[Layer, ...]
    records: tuple[FrameParamRecord, ...]
    labels: tuple[FrameLabelRecord, ...]
    effect_chains: tuple[FrameEffectChainRecord, ...]
    error: str | None = None
    worker_pid: int | None = None
    diagnostics: tuple[OperationDiagnostic, ...] = ()
    worker_lag_ms: float | None = None

    def __post_init__(self) -> None:
        """worker result の scalar と container shape を受信前に固定する。"""

        object.__setattr__(
            self,
            "frame_id",
            exact_integer(self.frame_id, name="frame_id", minimum=1),
        )
        object.__setattr__(self, "t", finite_real(self.t, name="t"))
        object.__setattr__(
            self,
            "epoch",
            exact_integer(self.epoch, name="epoch", minimum=0),
        )
        object.__setattr__(
            self,
            "generation",
            exact_integer(self.generation, name="generation", minimum=0),
        )
        object.__setattr__(
            self,
            "snapshot_revision",
            exact_integer(
                self.snapshot_revision,
                name="snapshot_revision",
                minimum=0,
            ),
        )
        _require_tuple_of(self.layers, name="layers", item_type=Layer)
        _require_tuple_of(
            self.records,
            name="records",
            item_type=FrameParamRecord,
        )
        _require_tuple_of(
            self.labels,
            name="labels",
            item_type=FrameLabelRecord,
        )
        _require_tuple_of(
            self.effect_chains,
            name="effect_chains",
            item_type=FrameEffectChainRecord,
        )
        if self.error is not None:
            object.__setattr__(
                self,
                "error",
                exact_string(self.error, name="error"),
            )
            if self.layers or self.records or self.labels or self.effect_chains:
                raise ValueError(
                    "error result の layers、records、labels、effect_chains "
                    "は空である必要があります"
                )
        if self.worker_pid is not None:
            object.__setattr__(
                self,
                "worker_pid",
                exact_integer(self.worker_pid, name="worker_pid", minimum=1),
            )
        if type(self.diagnostics) is not tuple or not all(
            isinstance(diagnostic, OperationDiagnostic) for diagnostic in self.diagnostics
        ):
            raise TypeError("diagnostics は OperationDiagnostic の tuple である必要があります")
        if self.worker_lag_ms is not None:
            object.__setattr__(
                self,
                "worker_lag_ms",
                finite_real(
                    self.worker_lag_ms,
                    name="worker_lag_ms",
                    minimum=0.0,
                ),
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class _WorkerReady:
    """worker の初期化完了を親プロセスへ通知するメッセージ。"""

    worker: str
    pid: int
    generation: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "worker",
            _non_empty_string(self.worker, name="worker"),
        )
        object.__setattr__(
            self,
            "pid",
            exact_integer(self.pid, name="pid", minimum=1),
        )
        object.__setattr__(
            self,
            "generation",
            exact_integer(self.generation, name="generation", minimum=0),
        )


_WorkerMessage = DrawResult | _WorkerReady | _SnapshotAck | _TaskRejected | _TaskStarted


class _MpDrawState:
    """ACK、latest-wins、stale 判定だけを持つ副作用なしの親側 state。

    process、Queue、thread、clock、close は所有しない。I/O の結果を明示的な
    transition method へ渡し、次に Queue へ書く値は query として返す。
    """

    def __init__(self) -> None:
        self.current_epoch = 0
        self.latest_received: DrawResult | None = None
        self.latest_successful: DrawResult | None = None
        self.completed_result_count = 0
        self.stale_result_count = 0
        self.last_stale_result: tuple[int, int, int] | None = None
        self.stale_generation_result_count = 0
        self.last_stale_generation_result: tuple[int, int, int] | None = None
        self.snapshot_ack_count = 0
        self.last_snapshot_ack: _SnapshotAck | None = None
        self.task_enqueue_count = 0
        self.task_drop_count = 0
        self.rejected_task_count = 0
        self.last_rejection: _TaskRejected | None = None
        self.last_published_frame_id = 0
        self._ready_worker_pids: set[int] = set()
        self._worker_snapshot_revisions: dict[int, int] = {}
        self._control_index_by_pid: dict[int, int] = {}
        self._pending_snapshot_updates: dict[int, _SnapshotUpdate] = {}
        self._queued_snapshot_revisions: dict[int, int] = {}
        self._pending_task: _DrawTask | None = None

    def reset_generation(self) -> None:
        """worker 世代にだけ属する ACK/task state を空にする。"""

        self._ready_worker_pids.clear()
        self._worker_snapshot_revisions.clear()
        self.clear_queue_state()

    def clear_queue_state(self) -> None:
        """Queue endpoint と同じ寿命の親側 slot を空にする。"""

        self._control_index_by_pid.clear()
        self._pending_snapshot_updates.clear()
        self._queued_snapshot_revisions.clear()
        self._pending_task = None

    def register_worker(self, *, pid: int, control_index: int) -> None:
        """起動済み worker と専用 control queue の対応を記録する。"""

        self._control_index_by_pid[pid] = control_index

    def mark_worker_ready(self, pid: int) -> None:
        """ready message を適用する。"""

        self._ready_worker_pids.add(pid)

    @property
    def ready_worker_pids(self) -> frozenset[int]:
        return frozenset(self._ready_worker_pids)

    def apply_snapshot_ack(self, ack: _SnapshotAck) -> None:
        """ACK を適用し、既知 revision と pending latest を前進させる。"""

        pid = ack.pid
        applied = ack.applied_revision
        previous = self._worker_snapshot_revisions.get(pid)
        if previous is None or applied > previous:
            self._worker_snapshot_revisions[pid] = applied
        self.snapshot_ack_count += 1
        self.last_snapshot_ack = ack

        control_index = self._control_index_by_pid.get(pid)
        if control_index is None:
            return
        queued_revision = self._queued_snapshot_revisions.get(control_index)
        if queued_revision == ack.requested_revision:
            self._queued_snapshot_revisions.pop(control_index, None)
        pending = self._pending_snapshot_updates.get(control_index)
        if pending is not None and pending.revision <= applied:
            self._pending_snapshot_updates.pop(control_index, None)

    def queue_snapshot_update(
        self,
        update: _SnapshotUpdate,
        *,
        worker_count: int,
    ) -> None:
        """worker ごとの親側 pending slot を同じ latest update へ置換する。"""

        for index in range(worker_count):
            self._pending_snapshot_updates[index] = update

    def snapshot_updates_ready_to_send(self) -> tuple[tuple[int, _SnapshotUpdate], ...]:
        """control queue が空と判明している pending update を返す。"""

        return tuple(
            (index, update)
            for index, update in self._pending_snapshot_updates.items()
            if index not in self._queued_snapshot_revisions
        )

    def mark_snapshot_update_queued(self, *, index: int, revision: int) -> None:
        """指定 worker の control queue が ACK 待ちになったことを記録する。"""

        self._queued_snapshot_revisions[index] = revision

    def workers_have_revision(self, revision: int) -> bool:
        """ready worker 全員が同じ revision を ACK 済みなら True。"""

        expected = self._ready_worker_pids
        return bool(expected) and all(
            self._worker_snapshot_revisions.get(pid) == revision for pid in expected
        )

    @property
    def worker_snapshot_revisions(self) -> dict[int, int]:
        return dict(self._worker_snapshot_revisions)

    @property
    def pending_snapshot_update_count(self) -> int:
        return len(self._pending_snapshot_updates)

    @property
    def queued_snapshot_update_count(self) -> int:
        return len(self._queued_snapshot_revisions)

    def replace_pending_task(self, task: _DrawTask) -> None:
        """親側 latest task slot を置換し、旧 slot があれば drop と数える。"""

        if self._pending_task is not None:
            self.task_drop_count += 1
        self._pending_task = task

    @property
    def pending_task(self) -> _DrawTask | None:
        return self._pending_task

    def mark_pending_task_enqueued(self, task: _DrawTask) -> None:
        """現在の latest task が enqueue 済みなら slot を空にする。"""

        if self._pending_task is task:
            self._pending_task = None
        self.task_enqueue_count += 1

    def record_queue_drop(self) -> None:
        self.task_drop_count += 1

    def record_rejection(self, rejection: _TaskRejected) -> None:
        self.rejected_task_count += 1
        self.last_rejection = rejection

    def begin_epoch(self, requested: int) -> bool:
        """epoch を前進させ、旧 timeline の表示候補と pending task を捨てる。"""

        if requested < self.current_epoch:
            raise ValueError(
                "epoch は現在値以上である必要があります: "
                f"current={self.current_epoch}, got={requested}"
            )
        if requested == self.current_epoch:
            return False
        self.current_epoch = requested
        self.latest_received = None
        self.latest_successful = None
        self._pending_task = None
        return True

    def reset_received_for_generation(self) -> None:
        """restart 後に旧世代の受信候補だけを外す。"""

        self.latest_received = None

    def record_stale_generation(
        self,
        result: DrawResult,
        *,
        current_generation: int,
    ) -> None:
        self.stale_generation_result_count += 1
        self.last_stale_generation_result = (
            result.frame_id,
            result.generation,
            current_generation,
        )

    def accept_result(self, result: DrawResult) -> bool:
        """current epoch の結果を採用し、stale なら False を返す。"""

        self.completed_result_count += 1
        if result.epoch != self.current_epoch:
            self.stale_result_count += 1
            self.last_stale_result = (
                result.frame_id,
                result.epoch,
                self.current_epoch,
            )
            return False
        if self.latest_received is None or result.frame_id > self.latest_received.frame_id:
            self.latest_received = result
        if result.error is None and (
            self.latest_successful is None
            or result.frame_id > self.latest_successful.frame_id
        ):
            self.latest_successful = result
        return True

    def publish_latest(self) -> DrawResult | None:
        """未公開の最新結果だけを一度返す。"""

        latest = self.latest_received
        if latest is None or latest.frame_id <= self.last_published_frame_id:
            return None
        self.last_published_frame_id = latest.frame_id
        return latest


def _draw_worker_main(
    task_q: mp_queues.Queue[_DrawTask | None],
    control_q: mp_queues.Queue[_SnapshotUpdate],
    result_q: mp_queues.Queue[_WorkerMessage],
    draw: Callable[[float], SceneItem],
    generation: int,
    effective_config: RuntimeConfig,
    authoring_recipe: AuthoringDefinitionsRecipe,
) -> None:
    """worker プロセスのエントリポイント。

    `task_q` から `_DrawTask` を受け取り、`draw(t)` を実行して `DrawResult` を `result_q`
    に返す。`task_q` に `None` が入ってきたら終了する。

    Notes
    -----
    worker は別プロセスなので、親プロセスの ParamStore には触れない。
    `parameter_context_from_snapshot()` で snapshot を固定し、観測結果だけを返す。
    """

    current = mp.current_process()
    worker = _non_empty_string(current.name, name="worker")
    pid = os.getpid()
    worker_generation = exact_integer(
        generation,
        name="generation",
        minimum=0,
    )
    # 親が capture した exact source recipe から immutable snapshot を再構築する。
    # config directory は worker 側で再走査しない。
    # ReloadedDraw は呼び出し中に、source bytes から再構築したより狭い candidate
    # catalog を内側へ束縛する。
    worker_definitions = load_authoring_definitions_recipe(authoring_recipe)
    result_q.put(_WorkerReady(worker=worker, pid=pid, generation=worker_generation))

    snapshot: ParamSnapshot | None = None
    effect_order_snapshot: EffectOrderSnapshot | None = None
    snapshot_revision: int | None = None

    def apply_snapshot(update: _SnapshotUpdate) -> None:
        """新しい snapshot だけを適用し、処理結果を必ず ack する。"""

        nonlocal snapshot, effect_order_snapshot, snapshot_revision
        if update.generation != worker_generation:
            return
        requested = update.revision
        if snapshot_revision is None or requested > snapshot_revision:
            snapshot = update.snapshot
            effect_order_snapshot = update.effect_order_snapshot
            snapshot_revision = requested
            status = "applied"
        elif requested == snapshot_revision:
            status = "current"
        else:
            status = "stale"
        assert snapshot_revision is not None
        result_q.put(
            _SnapshotAck(
                worker=worker,
                pid=pid,
                requested_revision=requested,
                applied_revision=snapshot_revision,
                status=status,
                generation=worker_generation,
            )
        )

    def drain_snapshot_updates() -> None:
        while True:
            try:
                update = control_q.get_nowait()
            except queue.Empty:
                return
            apply_snapshot(update)

    try:
        while True:
            drain_snapshot_updates()
            try:
                task = task_q.get(timeout=0.01)
            except queue.Empty:
                continue
            if task is None:
                return
            if task.generation != worker_generation:
                continue
            drain_snapshot_updates()
            requested_revision = task.snapshot_revision
            evaluation_snapshot = task.snapshot
            evaluation_effect_order_snapshot = task.effect_order_snapshot
            if task.snapshot is not None:
                # task と snapshot を同じ work item に束ねることで、slider drag 中に
                # control ACK が 1 revision 遅れても、この task の評価を開始できる。
                assert task.effect_order_snapshot is not None
                apply_snapshot(
                    _SnapshotUpdate(
                        revision=requested_revision,
                        snapshot=task.snapshot,
                        effect_order_snapshot=task.effect_order_snapshot,
                        generation=worker_generation,
                    )
                )
            elif snapshot_revision == requested_revision:
                evaluation_snapshot = snapshot
                evaluation_effect_order_snapshot = effect_order_snapshot
            if evaluation_snapshot is None:
                reason = (
                    "unknown"
                    if snapshot_revision is None or requested_revision > snapshot_revision
                    else "stale"
                )
                result_q.put(
                    _TaskRejected(
                        frame_id=task.frame_id,
                        worker=worker,
                        pid=pid,
                        requested_revision=requested_revision,
                        applied_revision=snapshot_revision,
                        reason=reason,
                        generation=worker_generation,
                    )
                )
                continue
            result_q.put(
                _TaskStarted(
                    frame_id=task.frame_id,
                    worker=worker,
                    pid=pid,
                    generation=worker_generation,
                )
            )
            frame_operation_diagnostics: tuple[OperationDiagnostic, ...] = ()
            try:
                # snapshot を固定したコンテキスト内で draw を実行することで、
                # GUI の状態（ParamStore）と独立に「このフレームで解決すべき値」を決定できる。
                with (
                    bind_operation_catalog(worker_definitions.operations),
                    bind_preset_catalog(worker_definitions.presets),
                    bind_runtime_config(effective_config),
                    preview_quality_context(task.quality),
                ):
                    with parameter_context_from_snapshot(
                        evaluation_snapshot,
                        cc_snapshot=task.cc_snapshot,
                        effect_order_snapshot=evaluation_effect_order_snapshot,
                    ) as frame_params:
                        try:
                            scene = draw(task.t)
                            layers = normalize_scene(scene)
                        finally:
                            frame_operation_diagnostics = current_operation_diagnostics()
                result_q.put(
                    DrawResult(
                        frame_id=task.frame_id,
                        layers=tuple(layers),
                        # frame_params は worker 内で作ったバッファなので、値だけをコピーして返す。
                        records=tuple(frame_params.records),
                        labels=tuple(frame_params.labels),
                        effect_chains=tuple(frame_params.effect_chains),
                        error=None,
                        t=task.t,
                        epoch=task.epoch,
                        generation=worker_generation,
                        worker_pid=pid,
                        diagnostics=frame_operation_diagnostics,
                        snapshot_revision=requested_revision,
                    )
                )
            except Exception:
                # 通常の draw 例外は失敗結果として返す。SystemExit 等で process 自体が
                # 終了した場合は、親側の health check が MpDrawWorkerError として検知する。
                result_q.put(
                    DrawResult(
                        frame_id=task.frame_id,
                        layers=(),
                        records=(),
                        labels=(),
                        effect_chains=(),
                        error=traceback.format_exc(),
                        t=task.t,
                        epoch=task.epoch,
                        generation=worker_generation,
                        worker_pid=pid,
                        diagnostics=frame_operation_diagnostics,
                        snapshot_revision=requested_revision,
                    )
                )
    finally:
        # Queue はプロセスごとに feeder thread を持ち得る。正常終了と SystemExit の
        # どちらでも、このプロセスが所有する endpoint を閉じて flush を待つ。
        for raw_queue in (task_q, control_q, result_q):
            worker_queue = cast(mp_queues.Queue[Any], raw_queue)
            try:
                worker_queue.close()
                worker_queue.join_thread()
            except (OSError, ValueError):
                pass


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
                message = self._result_q.get(timeout=min(0.05, remaining))
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
                message = self._result_q.get_nowait()
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
    def snapshot_broadcast_count(self) -> int:
        """snapshot 本体を broadcast した revision 数を返す。"""

        return self._snapshot_broadcast_count

    @property
    def last_submitted_frame_id(self) -> int:
        """親 process が最後に割り当てた frame ID を返す。"""

        return self._next_frame_id

    @property
    def snapshot_ack_count(self) -> int:
        """worker から受信した snapshot ack 数を返す。"""

        return self._state.snapshot_ack_count

    @property
    def snapshot_payload_copy_count(self) -> int:
        """queue 用 plain snapshot を構築した revision 数を返す。"""

        return self._snapshot_payload_copy_count

    @property
    def worker_snapshot_revisions(self) -> dict[int, int]:
        """worker pid ごとの適用済み snapshot revision を返す。"""

        return self._state.worker_snapshot_revisions

    @property
    def ready_worker_pids(self) -> frozenset[int]:
        """初期化を完了した現 worker 世代の pid を返す。"""

        return self._state.ready_worker_pids

    @property
    def last_snapshot_ack(self) -> tuple[int, int, str] | None:
        """直近 ack の (requested, applied, status) を返す。"""

        ack = self._state.last_snapshot_ack
        if ack is None:
            return None
        return ack.requested_revision, ack.applied_revision, ack.status

    @property
    def rejected_task_count(self) -> int:
        """未知または古い snapshot revision で拒否された task 数を返す。"""

        return self._state.rejected_task_count

    @property
    def task_enqueue_count(self) -> int:
        """task queue への投入に成功した回数を返す。"""

        return self._state.task_enqueue_count

    @property
    def task_drop_count(self) -> int:
        """latest-wins または epoch 境界で未開始 task を破棄した回数を返す。"""

        return self._state.task_drop_count

    @property
    def completed_result_count(self) -> int:
        """親が回収済みの DrawResult 総数を返す。"""

        return self._state.completed_result_count

    @property
    def current_epoch(self) -> int:
        """現在採用対象としている transport epoch を返す。"""

        return self._state.current_epoch

    @property
    def generation(self) -> int:
        """現在の worker 世代番号を返す。"""

        return self._generation

    @property
    def restart_count(self) -> int:
        """worker 世代を再起動した回数を返す。"""

        return self._restart_count

    @property
    def last_restart_reason(self) -> str | None:
        """直近の worker 再起動理由を返す。"""

        return self._last_restart_reason

    @property
    def evaluation_timeout(self) -> float | None:
        """現在設定されている evaluation timeout（秒）を返す。"""

        return self._evaluation_timeout

    @property
    def stale_result_count(self) -> int:
        """旧/未知 epoch のため表示候補から破棄した result 数を返す。"""

        return self._state.stale_result_count

    @property
    def last_stale_result(self) -> tuple[int, int, int] | None:
        """直近 stale result の `(frame_id, result_epoch, current_epoch)`。"""

        return self._state.last_stale_result

    @property
    def stale_generation_result_count(self) -> int:
        """旧 worker 世代のため破棄した result 数を返す。"""

        return self._state.stale_generation_result_count

    @property
    def last_stale_generation_result(self) -> tuple[int, int, int] | None:
        """直近の `(frame_id, result_generation, current_generation)`。"""

        return self._state.last_stale_generation_result

    @property
    def pending_snapshot_update_count(self) -> int:
        """親側で保持する worker 別 latest update 数を返す。"""

        return self._state.pending_snapshot_update_count

    @property
    def queued_snapshot_update_count(self) -> int:
        """control queue へ投入済みで ack 待ちの update 数を返す。"""

        return self._state.queued_snapshot_update_count

    @property
    def last_rejection(self) -> tuple[int, int | None, str] | None:
        """直近拒否の (requested, applied, reason) を返す。"""

        rejection = self._state.last_rejection
        if rejection is None:
            return None
        return (
            rejection.requested_revision,
            rejection.applied_revision,
            rejection.reason,
        )

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


__all__ = ["DrawResult", "MpDraw", "MpDrawWorkerError"]
