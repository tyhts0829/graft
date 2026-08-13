"""
Purpose:
    一つのdraw frameで参照するparameter、effect順、MIDI値と観測bufferを固定する。
Use when:
    draw評価またはworker評価にparameter解決・観測のframe境界を設ける場合。
Constraints:
    - frame中はentry時のsnapshotだけを読み、live storeの後続変更を観測しない。
    - store-backed contextは成功frameだけをmergeし、失敗時の部分観測をcommitしない。
    - worker用snapshot contextはstoreへcommitせず、ContextVarを終了時に必ず復元する。
"""

from __future__ import annotations

import contextlib
import contextvars
from typing import Iterator

from grafix.core.operation_diagnostics import (
    OperationDiagnosticBuffer,
    operation_diagnostic_context,
)

from .frame_params import FrameParamsBuffer
from .store import ParamStore
from .effect_order_ops import (
    EffectOrderSnapshot,
    merge_frame_effect_chains,
    store_effect_order_snapshot,
)
from .labels_ops import merge_frame_labels
from .merge_ops import merge_frame_params
from .snapshot_ops import ParamSnapshot, store_snapshot
from .source import MidiFrameSnapshot

_EMPTY_SNAPSHOT: ParamSnapshot = {}
_param_snapshot_var: contextvars.ContextVar[ParamSnapshot] = contextvars.ContextVar(
    "param_snapshot", default=_EMPTY_SNAPSHOT
)
_EMPTY_EFFECT_ORDER_SNAPSHOT: EffectOrderSnapshot = {}
_effect_order_snapshot_var: contextvars.ContextVar[EffectOrderSnapshot] = (
    contextvars.ContextVar(
        "effect_order_snapshot",
        default=_EMPTY_EFFECT_ORDER_SNAPSHOT,
    )
)
_frame_params_var: contextvars.ContextVar[FrameParamsBuffer | None] = (
    contextvars.ContextVar("frame_params", default=None)
)
_cc_snapshot_var: contextvars.ContextVar[MidiFrameSnapshot | None] = contextvars.ContextVar(
    "cc_snapshot", default=None
)
_store_var: contextvars.ContextVar[ParamStore | None] = contextvars.ContextVar(
    "param_store", default=None
)
_param_recording_enabled_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "param_recording_enabled", default=True
)


def current_param_snapshot() -> ParamSnapshot:
    snapshot = _param_snapshot_var.get(_EMPTY_SNAPSHOT)
    return snapshot if snapshot else {}


def current_effect_order_snapshot() -> EffectOrderSnapshot:
    """現在frameに固定されたeffect order overrideを返す。"""

    snapshot = _effect_order_snapshot_var.get(_EMPTY_EFFECT_ORDER_SNAPSHOT)
    return snapshot if snapshot else {}


def current_frame_params() -> FrameParamsBuffer | None:
    return _frame_params_var.get()


def current_cc_snapshot() -> MidiFrameSnapshot | None:
    return _cc_snapshot_var.get()


def current_param_store() -> ParamStore | None:
    """現在の ParamStore を返す（GUI/label 設定用）。"""
    return _store_var.get()


def current_param_recording_enabled() -> bool:
    """現在の param 観測（GUI/永続化）を有効にするかどうかを返す。"""

    return bool(_param_recording_enabled_var.get(True))


@contextlib.contextmanager
def parameter_recording_muted() -> Iterator[None]:
    """このコンテキスト内で param 観測（record/label）を無効化する。"""

    token = _param_recording_enabled_var.set(False)
    try:
        yield
    finally:
        _param_recording_enabled_var.reset(token)


@contextlib.contextmanager
def parameter_context(
    store: ParamStore, cc_snapshot: MidiFrameSnapshot | None = None
) -> Iterator[OperationDiagnosticBuffer]:
    """フレーム境界で param_snapshot / frame_params を固定するコンテキストマネージャ。"""

    snapshot = store_snapshot(store)
    effect_order_snapshot = store_effect_order_snapshot(store)
    frame_params = FrameParamsBuffer()

    with operation_diagnostic_context() as operation_diagnostics:
        t1 = _param_snapshot_var.set(snapshot)
        t_effect = _effect_order_snapshot_var.set(effect_order_snapshot)
        t2 = _frame_params_var.set(frame_params)
        t3 = _cc_snapshot_var.set(cc_snapshot)
        t4 = _store_var.set(store)
        try:
            yield operation_diagnostics
        except BaseException:
            # draw が失敗した frame は、途中までの parameter 観測を反映しない。
            # operation diagnostics は呼び出し側が buffer から失敗理由として読める。
            raise
        else:
            # yield が正常完了した frame の観測結果だけを commit する。
            merge_frame_effect_chains(
                store,
                frame_params.effect_chains,
                observation_complete=(
                    frame_params.effect_chain_observation_complete
                ),
            )
            merge_frame_labels(store, frame_params.labels)
            merge_frame_params(store, frame_params.records)
        finally:
            # body/merge のどちらが失敗しても ContextVar は必ず元へ戻す。
            _store_var.reset(t4)
            _cc_snapshot_var.reset(t3)
            _frame_params_var.reset(t2)
            _effect_order_snapshot_var.reset(t_effect)
            _param_snapshot_var.reset(t1)


@contextlib.contextmanager
def parameter_context_from_snapshot(
    snapshot: ParamSnapshot,
    cc_snapshot: MidiFrameSnapshot | None = None,
    *,
    effect_order_snapshot: EffectOrderSnapshot | None = None,
) -> Iterator[FrameParamsBuffer]:
    """ParamStore を持たずに snapshot/frame_params を固定する（worker 用）。"""

    frame_params = FrameParamsBuffer()

    with operation_diagnostic_context():
        t1 = _param_snapshot_var.set(snapshot)
        t_effect = _effect_order_snapshot_var.set(
            {} if effect_order_snapshot is None else effect_order_snapshot
        )
        t2 = _frame_params_var.set(frame_params)
        t3 = _cc_snapshot_var.set(cc_snapshot)
        t4 = _store_var.set(None)
        try:
            yield frame_params
        finally:
            _store_var.reset(t4)
            _cc_snapshot_var.reset(t3)
            _frame_params_var.reset(t2)
            _effect_order_snapshot_var.reset(t_effect)
            _param_snapshot_var.reset(t1)
