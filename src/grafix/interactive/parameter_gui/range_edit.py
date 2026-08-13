"""
Purpose:
    MIDI-linked parameter rangeの未commit previewと、一括commit用domain operationを定義する。
Use when:
    range shift/min/max計算、対象selection、またはhistory transactionを変更する場合。
Constraints:
    - previewはParamStoreを変更せず、originalとpending rangeをimmutable sessionに保持する。
    - commit時にcurrent metadata kindを再確認し、差分のある対象だけを一つの履歴単位で更新する。
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.meta_ops import set_meta
from grafix.core.parameters.snapshot_ops import store_snapshot_for_gui
from grafix.core.parameters.store import ParamStore

if TYPE_CHECKING:
    from grafix.core.parameters.history import ParamStoreHistory

RangeEditMode = Literal["shift", "min", "max"]


@dataclass(frozen=True, slots=True)
class RangeEditTarget:
    """明示Range Edit modeでpreviewする一つのparameter。"""

    key: ParameterKey
    label: str
    kind: str
    original_range: tuple[float | int, float | int]
    pending_range: tuple[float | int, float | int]


@dataclass(frozen=True, slots=True)
class RangeEditSession:
    """storeへ未commitのlinked range編集preview。"""

    mode: RangeEditMode
    cc: int
    targets: tuple[RangeEditTarget, ...]


def apply_range_shift(
    *,
    kind: str,
    ui_min: float | int,
    ui_max: float | int,
    delta: float,
    mode: RangeEditMode,
    sensitivity: float = 1.0,
) -> tuple[float | int, float | int]:
    """ui_min/ui_max を delta に応じて更新して返す。

    Parameters
    ----------
    kind
        `"float"` / `"int"` / `"vec3"` を想定する（`"vec3"` は float と同じ扱い）。
    ui_min
        現在の下限値。
    ui_max
        現在の上限値。
    delta
        入力の差分（回した方向）。正で増加、負で減少。
    mode
        `"shift"`: ui_min/ui_max を同量シフトする。
        `"min"`: ui_min のみ調整する。
        `"max"`: ui_max のみ調整する。
    sensitivity
        シフト量の係数。

    Returns
    -------
    (ui_min, ui_max)
        更新後の (ui_min, ui_max) を返す。`ui_min > ui_max` の場合は swap する。
    """

    if mode not in ("shift", "min", "max"):
        raise ValueError(f"unknown mode: {mode!r}")

    if kind == "int":
        lo = int(ui_min)
        hi = int(ui_max)
        width = float(hi - lo)
        shift_f = float(delta) * float(sensitivity) * width
        shift_i = int(round(shift_f))
        if shift_i == 0 and shift_f != 0.0:
            shift_i = 1 if shift_f > 0.0 else -1

        if mode == "shift":
            lo += int(shift_i)
            hi += int(shift_i)
        elif mode == "min":
            lo += int(shift_i)
        else:
            hi += int(shift_i)

        if lo > hi:
            lo, hi = hi, lo
        return int(lo), int(hi)

    lo_f = float(ui_min)
    hi_f = float(ui_max)
    width_f = float(hi_f - lo_f)
    shift = float(delta) * float(sensitivity) * width_f

    if mode == "shift":
        lo_f += float(shift)
        hi_f += float(shift)
    elif mode == "min":
        lo_f += float(shift)
    else:
        hi_f += float(shift)

    if lo_f > hi_f:
        lo_f, hi_f = hi_f, lo_f
    return float(lo_f), float(hi_f)


def range_edit_session_for_store(
    store: ParamStore,
    *,
    cc: int,
    mode: RangeEditMode,
) -> RangeEditSession | None:
    """指定CCへlinkした編集可能parameterからpreview sessionを作る。"""

    if mode not in ("shift", "min", "max"):
        raise ValueError(f"unknown mode: {mode!r}")
    cc_i = int(cc)
    targets: list[RangeEditTarget] = []
    disabled = {
        ("__style__", "global_thickness"),
        ("__layer_style__", "line_thickness"),
    }
    for key, (meta, state, ordinal, label) in store_snapshot_for_gui(store).items():
        assigned = state.cc_key
        if isinstance(assigned, int):
            matches = int(assigned) == cc_i
        elif assigned is None:
            matches = False
        else:
            matches = cc_i in {int(value) for value in assigned if value is not None}
        if not matches or (key.op, key.arg) in disabled:
            continue
        if meta.kind not in {"float", "int", "vec3"}:
            continue
        if meta.ui_min is None or meta.ui_max is None:
            continue
        value_range = (meta.ui_min, meta.ui_max)
        targets.append(
            RangeEditTarget(
                key=key,
                label=(
                    f"{key.op} {ordinal} · {meta.display_name or key.arg}"
                    if not label
                    else f"{label} · {meta.display_name or key.arg}"
                ),
                kind=str(meta.kind),
                original_range=value_range,
                pending_range=value_range,
            )
        )
    if not targets:
        return None
    targets.sort(key=lambda target: (target.key.op, target.key.site_id, target.key.arg))
    return RangeEditSession(mode=mode, cc=cc_i, targets=tuple(targets))


def preview_range_edit(
    session: RangeEditSession,
    *,
    delta: float,
) -> RangeEditSession:
    """deltaを現在previewへ適用し、storeを変えず新sessionを返す。"""

    targets = tuple(
        replace(
            target,
            pending_range=apply_range_shift(
                kind=target.kind,
                ui_min=target.pending_range[0],
                ui_max=target.pending_range[1],
                delta=float(delta),
                mode=session.mode,
            ),
        )
        for target in session.targets
    )
    return replace(session, targets=targets)


def apply_range_edit_session(
    store: ParamStore,
    session: RangeEditSession,
    *,
    history: ParamStoreHistory | None = None,
) -> tuple[ParameterKey, ...]:
    """previewの差分だけを一つのhistory transactionでcommitする。"""

    changed_targets = tuple(
        target
        for target in session.targets
        if target.pending_range != target.original_range
    )
    if not changed_targets:
        return ()
    if history is not None:
        history.break_coalescing()
    transaction = (
        history.transaction(source="midi_range_edit")
        if history is not None
        else nullcontext()
    )
    changed: list[ParameterKey] = []
    with transaction:
        for target in changed_targets:
            meta = store.get_meta(target.key)
            if meta is None or str(meta.kind) != target.kind:
                continue
            lo, hi = target.pending_range
            set_meta(store, target.key, replace(meta, ui_min=lo, ui_max=hi))
            changed.append(target.key)
    return tuple(changed)


__all__ = [
    "RangeEditMode",
    "RangeEditSession",
    "RangeEditTarget",
    "apply_range_edit_session",
    "apply_range_shift",
    "preview_range_edit",
    "range_edit_session_for_store",
]
