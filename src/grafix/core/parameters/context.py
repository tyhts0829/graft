# どこで: `src/grafix/core/parameters/context.py`。
# 何を: フレーム単位で param_snapshot / frame_params / cc_snapshot を固定するコンテキストマネージャを提供する。
# なぜ: draw 中の値解決を決定的にし、並列実行でも状態が漏れないようにするため。

from __future__ import annotations

import contextlib
import contextvars
from typing import Iterator

from .frame_params import FrameParamsBuffer
from .store import ParamStore
from .labels_ops import merge_frame_labels
from .merge_ops import merge_frame_params
from .snapshot_ops import ParamSnapshot, store_snapshot

_EMPTY_SNAPSHOT: ParamSnapshot = {}
_param_snapshot_var: contextvars.ContextVar[ParamSnapshot] = contextvars.ContextVar(
    "param_snapshot", default=_EMPTY_SNAPSHOT
)
_frame_params_var: contextvars.ContextVar[FrameParamsBuffer | None] = (
    contextvars.ContextVar("frame_params", default=None)
)
_cc_snapshot_var: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "cc_snapshot", default=None
)
_store_var: contextvars.ContextVar[ParamStore | None] = contextvars.ContextVar(
    "param_store", default=None
)


def current_param_snapshot() -> ParamSnapshot:
    snapshot = _param_snapshot_var.get(_EMPTY_SNAPSHOT)
    return snapshot if snapshot else {}


def current_frame_params() -> FrameParamsBuffer | None:
    return _frame_params_var.get()


def current_cc_snapshot() -> dict | None:
    return _cc_snapshot_var.get()


def current_param_store() -> ParamStore | None:
    """現在の ParamStore を返す（GUI/label 設定用）。"""
    return _store_var.get()


@contextlib.contextmanager
def parameter_context(
    store: ParamStore, cc_snapshot: dict | None = None
) -> Iterator[None]:
    """フレーム境界で param_snapshot / frame_params を固定するコンテキストマネージャ。"""

    snapshot = store_snapshot(store)
    frame_params = FrameParamsBuffer()

    t1 = _param_snapshot_var.set(snapshot)
    t2 = _frame_params_var.set(frame_params)
    t3 = _cc_snapshot_var.set(cc_snapshot)
    t4 = _store_var.set(store)
    try:
        yield
    finally:
        # フレーム終了時に観測結果を ParamStore へ保存
        merge_frame_labels(store, frame_params.labels)
        merge_frame_params(store, frame_params.records)
        _param_snapshot_var.reset(t1)
        _frame_params_var.reset(t2)
        _cc_snapshot_var.reset(t3)
        _store_var.reset(t4)


@contextlib.contextmanager
def parameter_context_from_snapshot(
    snapshot: ParamSnapshot, cc_snapshot: dict | None = None
) -> Iterator[FrameParamsBuffer]:
    """ParamStore を持たずに snapshot/frame_params を固定する（worker 用）。"""

    frame_params = FrameParamsBuffer()

    t1 = _param_snapshot_var.set(snapshot)
    t2 = _frame_params_var.set(frame_params)
    t3 = _cc_snapshot_var.set(cc_snapshot)
    t4 = _store_var.set(None)
    try:
        yield frame_params
    finally:
        _param_snapshot_var.reset(t1)
        _frame_params_var.reset(t2)
        _cc_snapshot_var.reset(t3)
        _store_var.reset(t4)
