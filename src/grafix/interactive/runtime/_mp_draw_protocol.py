"""
Purpose:
    mp-drawのprocess境界を通るprivate messageと、そのwire validation契約を定義する。
Use when:
    task/result schema、snapshot ACK、stale判定用identifier、worker error表現を変更するとき。
Constraints:
    - messageは一つのMpDraw lifetime内専用で、永続形式やpublic互換契約にしない。
    - process境界では暗黙変換を避け、containerと値のexact typeを検証する。
    - frame_id、epoch、generation、snapshot_revisionの意味を統合しない。
    - error resultは成功時のdomain payloadを保持しない。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from grafix.core.layer import Layer
from grafix.core.operation_diagnostics import OperationDiagnostic
from grafix.core.parameters import (
    EffectOrderSnapshot,
    FrameEffectChainRecord,
    FrameLabelRecord,
    FrameParamRecord,
)
from grafix.core.parameters.snapshot_ops import ParamSnapshot
from grafix.core.parameters.source import MidiFrameSnapshot
from grafix.core.preview_quality import PreviewQuality
from grafix.core.value_validation import (
    exact_integer,
    exact_string,
    exact_string_choice,
    finite_real,
)


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

    def __reduce__(
        self,
    ) -> tuple[object, tuple[str, int | None, int | None, str | None]]:
        """spawn/Queue 境界でも構造化された worker 情報を保つ。"""

        return (
            _restore_mp_draw_worker_error,
            (self.worker, self.pid, self.exitcode, self.detail),
        )


def _restore_mp_draw_worker_error(
    worker: str,
    pid: int | None,
    exitcode: int | None,
    detail: str | None,
) -> MpDrawWorkerError:
    return MpDrawWorkerError(
        worker=worker,
        pid=pid,
        exitcode=exitcode,
        detail=detail,
    )


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


@dataclass(frozen=True, slots=True, kw_only=True)
class MpDrawStats:
    """親 process が一時点で保持する immutable telemetry snapshot。"""

    snapshot_broadcast_count: int
    last_submitted_frame_id: int
    snapshot_ack_count: int
    snapshot_payload_copy_count: int
    worker_snapshot_revisions: tuple[tuple[int, int], ...]
    ready_worker_pids: frozenset[int]
    last_snapshot_ack: tuple[int, int, str] | None
    rejected_task_count: int
    task_enqueue_count: int
    task_drop_count: int
    completed_result_count: int
    current_epoch: int
    generation: int
    restart_count: int
    last_restart_reason: str | None
    evaluation_timeout: float | None
    stale_result_count: int
    last_stale_result: tuple[int, int, int] | None
    stale_generation_result_count: int
    last_stale_generation_result: tuple[int, int, int] | None
    pending_snapshot_update_count: int
    queued_snapshot_update_count: int
    last_rejection: tuple[int, int | None, str] | None


_WorkerMessage = DrawResult | _WorkerReady | _SnapshotAck | _TaskRejected | _TaskStarted


def decode_worker_message(value: object) -> _WorkerMessage:
    """result queue から受信した exact message 型だけを返す。"""

    if type(value) not in (
        DrawResult,
        _WorkerReady,
        _SnapshotAck,
        _TaskRejected,
        _TaskStarted,
    ):
        raise TypeError("worker message の型が不正です")
    return cast(_WorkerMessage, value)


def decode_draw_task(value: object) -> _DrawTask | None:
    """task queue の exact task または終了 sentinel を返す。"""

    if value is None:
        return None
    if type(value) is not _DrawTask:
        raise TypeError("draw task の型が不正です")
    return cast(_DrawTask, value)


def decode_snapshot_update(value: object) -> _SnapshotUpdate:
    """control queue の exact snapshot update を返す。"""

    if type(value) is not _SnapshotUpdate:
        raise TypeError("snapshot update の型が不正です")
    return cast(_SnapshotUpdate, value)


__all__ = ["DrawResult", "MpDrawStats", "MpDrawWorkerError"]
