"""
Purpose:
    operation 評価が要求値を clamp・縮小・省略した事実を、silent にせず小さな immutable diagnostic として残す。
Use when:
    operation の graceful degradation、frame 診断の dedupe、worker からの診断 merge を変更・調査する場合。
Constraints:
    - payload は bounded な scalar/小 tuple に限り、geometry や mutable object を保持させない。
    - buffer は evaluation context ごとに隔離し、安定 identity で insertion order を保ったまま dedupe する。
    - context 外の emit は process-global state を蓄積しない。
Side effects:
    active な evaluation-local buffer がある場合だけ diagnostic を追記する。
"""

from __future__ import annotations

import contextlib
import contextvars
import math
from collections import OrderedDict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

import numpy as np

from grafix.core.geometry_kernels.grid import (
    GridOverflowPolicy,
    GridSpec,
    plan_grid_from_bbox,
)
from grafix.core.value_validation import exact_string, exact_string_choice

OperationDiagnosticSeverity = Literal["info", "warning", "error"]
OperationDiagnosticScalar: TypeAlias = None | bool | int | float | str
OperationDiagnosticValue: TypeAlias = (
    OperationDiagnosticScalar | tuple[OperationDiagnosticScalar, ...]
)


def _normalize_value(value: object, *, field: str) -> OperationDiagnosticValue:
    if value is None or type(value) in {bool, int, float, str}:
        return cast(OperationDiagnosticScalar, value)
    if isinstance(value, tuple):
        if len(value) > 16:
            raise ValueError(f"{field} は最大 16 要素である必要があります")
        normalized: list[OperationDiagnosticScalar] = []
        for item in value:
            if item is not None and type(item) not in {bool, int, float, str}:
                raise TypeError(
                    f"{field} の要素は scalar である必要があります: {item!r}"
                )
            normalized.append(item)
        return tuple(normalized)
    raise TypeError(f"{field} は小さな scalar/tuple である必要があります")


def _identity_value(value: OperationDiagnosticValue) -> object:
    """NaN/Infinity を含んでも同じ payload が dedupe される表現へ変換する。"""

    if isinstance(value, tuple):
        return tuple(_identity_value(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return ("float", "nan")
        return ("float", "inf" if value > 0.0 else "-inf")
    return value


@dataclass(frozen=True, slots=True)
class OperationDiagnostic:
    """1 operation の要求値と実効値の差、および理由。"""

    op: str
    original_value: OperationDiagnosticValue
    effective_value: OperationDiagnosticValue
    reason: str
    severity: OperationDiagnosticSeverity = "warning"

    def __post_init__(self) -> None:
        op = exact_string(self.op, name="op")
        reason = exact_string(self.reason, name="reason")
        if not op.strip():
            raise ValueError("op は空にできません")
        if not reason.strip():
            raise ValueError("reason は空にできません")
        object.__setattr__(
            self,
            "severity",
            exact_string_choice(
                self.severity,
                name="severity",
                choices=("info", "warning", "error"),
            ),
        )
        object.__setattr__(
            self,
            "original_value",
            _normalize_value(self.original_value, field="original_value"),
        )
        object.__setattr__(
            self,
            "effective_value",
            _normalize_value(self.effective_value, field="effective_value"),
        )

    def identity(self) -> tuple[object, ...]:
        """frame内/DiagnosticCenter間のdedupeに使う安定identity。"""

        return (
            self.op,
            _identity_value(self.original_value),
            _identity_value(self.effective_value),
            self.reason,
            self.severity,
        )


class OperationDiagnosticBuffer:
    """1 evaluation 内を insertion order で dedupe する小さな buffer。"""

    def __init__(self) -> None:
        self._items: OrderedDict[tuple[object, ...], OperationDiagnostic] = OrderedDict()

    def add(self, diagnostic: OperationDiagnostic) -> None:
        if not isinstance(diagnostic, OperationDiagnostic):
            raise TypeError("diagnostic は OperationDiagnostic である必要があります")
        self._items.setdefault(diagnostic.identity(), diagnostic)

    def extend(self, diagnostics: Iterable[OperationDiagnostic]) -> None:
        for diagnostic in diagnostics:
            self.add(diagnostic)

    def snapshot(self) -> tuple[OperationDiagnostic, ...]:
        return tuple(self._items.values())

    def __len__(self) -> int:
        return len(self._items)


_operation_diagnostics_var: contextvars.ContextVar[
    OperationDiagnosticBuffer | None
] = contextvars.ContextVar("operation_diagnostics", default=None)


def emit_operation_diagnostic(
    *,
    op: str,
    original_value: OperationDiagnosticValue,
    effective_value: OperationDiagnosticValue,
    reason: str,
    severity: OperationDiagnosticSeverity = "warning",
) -> OperationDiagnostic:
    """payload を作り、evaluation context があればその frame へ記録する。"""

    diagnostic = OperationDiagnostic(
        op=op,
        original_value=original_value,
        effective_value=effective_value,
        reason=reason,
        severity=severity,
    )
    buffer = _operation_diagnostics_var.get()
    if buffer is not None:
        buffer.add(diagnostic)
    return diagnostic


def grid_spec_from_bbox_with_diagnostic(
    mins: Sequence[float] | np.ndarray,
    maxs: Sequence[float] | np.ndarray,
    *,
    pitch: float,
    padding: float,
    max_points: int,
    overflow: GridOverflowPolicy,
) -> GridSpec | None:
    """bbox の grid 計画を実行し、必要な診断を evaluation へ記録する。"""

    plan = plan_grid_from_bbox(
        mins,
        maxs,
        pitch=pitch,
        padding=padding,
        max_points=max_points,
        overflow=overflow,
    )
    diagnostic = plan.diagnostic
    if diagnostic is not None:
        emit_operation_diagnostic(
            op="GridSpec.from_bbox",
            original_value=diagnostic.original_value,
            effective_value=diagnostic.effective_value,
            reason=diagnostic.reason,
            severity=diagnostic.severity,
        )
    return plan.spec


def extend_operation_diagnostics(
    diagnostics: Iterable[OperationDiagnostic],
) -> None:
    """worker 等で収集済みの payload を現在 evaluation へマージする。"""

    buffer = _operation_diagnostics_var.get()
    if buffer is not None:
        buffer.extend(diagnostics)


def current_operation_diagnostics() -> tuple[OperationDiagnostic, ...]:
    """現在 evaluation の immutable snapshot を返す。context外では空。"""

    buffer = _operation_diagnostics_var.get()
    return () if buffer is None else buffer.snapshot()


@contextlib.contextmanager
def operation_diagnostic_context() -> Iterator[OperationDiagnosticBuffer]:
    """operation diagnostics を evaluation 単位に隔離して収集する。"""

    buffer = OperationDiagnosticBuffer()
    token = _operation_diagnostics_var.set(buffer)
    try:
        yield buffer
    finally:
        _operation_diagnostics_var.reset(token)


__all__ = [
    "OperationDiagnostic",
    "OperationDiagnosticBuffer",
    "OperationDiagnosticSeverity",
    "OperationDiagnosticValue",
    "current_operation_diagnostics",
    "emit_operation_diagnostic",
    "extend_operation_diagnostics",
    "grid_spec_from_bbox_with_diagnostic",
    "operation_diagnostic_context",
]
