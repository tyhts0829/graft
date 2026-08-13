"""
Purpose:
    Geometry の大規模配列を確保する前と、評価後の実測 aggregate で共通 resource 上限を強制する。
Use when:
    allocation-heavy operation、scene aggregate limit、packed geometry の int32/byte 容量検査を追加・変更する場合。
Constraints:
    - NumPy 配列を確保する前に Python int で見積もり、scratch 必要量も明示する。
    - UI slider からの値だけでなく、code から直接渡された引数にも同じ上限を適用する。
    - coords/offsets の packed contract が持つ int32 容量を runtime budget と独立に越えさせない。
"""

from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass
from typing import Iterator

from grafix.core.value_validation import exact_integer, exact_string


DEFAULT_MAX_OUTPUT_VERTICES = 10_000_000
DEFAULT_MAX_OUTPUT_LINES = 2_000_000
DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024 * 1024
_MAX_INT32 = (1 << 31) - 1


class ResourceLimitError(ValueError):
    """operation の見積もりが許可された resource budget を超えた。"""


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    """1 operation が生成してよい geometry の上限。

    UI の slider range とは異なり、この値はコードから直接渡された引数にも適用する。
    ``max_output_bytes`` は最終 ``coords(float32[N,3])`` と
    ``offsets(int32[M+1])`` の最低必要量を検査する。operation 固有の scratch 配列は
    ``ensure_geometry_output(..., scratch_bytes=...)`` で追加できる。
    """

    max_output_vertices: int = DEFAULT_MAX_OUTPUT_VERTICES
    max_output_lines: int = DEFAULT_MAX_OUTPUT_LINES
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES

    def __post_init__(self) -> None:
        for name, value in (
            ("max_output_vertices", self.max_output_vertices),
            ("max_output_lines", self.max_output_lines),
            ("max_output_bytes", self.max_output_bytes),
        ):
            object.__setattr__(
                self,
                name,
                exact_integer(value, name=name, minimum=0),
            )


DEFAULT_RESOURCE_BUDGET = ResourceBudget()
_CURRENT_RESOURCE_BUDGET: contextvars.ContextVar[ResourceBudget] = contextvars.ContextVar(
    "grafix_resource_budget",
    default=DEFAULT_RESOURCE_BUDGET,
)


def current_resource_budget() -> ResourceBudget:
    """現在の evaluation context に適用される budget を返す。"""

    return _CURRENT_RESOURCE_BUDGET.get()


@contextlib.contextmanager
def resource_budget_context(budget: ResourceBudget) -> Iterator[None]:
    """この context 内の operation に ``budget`` を適用する。"""

    if not isinstance(budget, ResourceBudget):
        raise TypeError("budget は ResourceBudget である必要がある")
    token = _CURRENT_RESOURCE_BUDGET.set(budget)
    try:
        yield
    finally:
        _CURRENT_RESOURCE_BUDGET.reset(token)


def _estimated_geometry_bytes(*, vertices: int, lines: int, scratch_bytes: int) -> int:
    # Python の int で計算し、NumPy の固定幅整数へ落とす前に検査する。
    return vertices * 3 * 4 + (lines + 1) * 4 + scratch_bytes


def _ensure_resource_usage_validated(
    op: str,
    *,
    vertices: int,
    lines: int,
    byte_size: int,
    hint: str | None,
    budget: ResourceBudget | None,
) -> None:
    """検証済みの resource 使用量を active budget と比較する。"""

    active_budget = current_resource_budget() if budget is None else budget
    if not isinstance(active_budget, ResourceBudget):
        raise TypeError("budget は ResourceBudget である必要があります")

    exceeded: list[str] = []
    if vertices > _MAX_INT32:
        exceeded.append(f"vertices={vertices:,} > int32 capacity {_MAX_INT32:,}")
    if lines + 1 > _MAX_INT32:
        exceeded.append(f"offsets={lines + 1:,} > int32 capacity {_MAX_INT32:,}")
    if vertices > active_budget.max_output_vertices:
        exceeded.append(
            f"vertices={vertices:,} > {active_budget.max_output_vertices:,}"
        )
    if lines > active_budget.max_output_lines:
        exceeded.append(f"lines={lines:,} > {active_budget.max_output_lines:,}")
    if byte_size > active_budget.max_output_bytes:
        exceeded.append(
            f"estimated_bytes={byte_size:,} > {active_budget.max_output_bytes:,}"
        )
    if not exceeded:
        return

    suffix = "" if not hint else f"; {hint}"
    raise ResourceLimitError(
        f"{op}: resource budget を超えるため配列を確保しません: "
        + ", ".join(exceeded)
        + suffix
    )


def ensure_geometry_output(
    op: str,
    *,
    vertices: int,
    lines: int,
    scratch_bytes: int = 0,
    hint: str | None = None,
) -> None:
    """大規模配列を確保する前に output plan を共通上限で検査する。"""

    op_s = exact_string(op, name="op")
    hint_s = None if hint is None else exact_string(hint, name="hint")
    vertices_i = exact_integer(vertices, name="vertices", minimum=0)
    lines_i = exact_integer(lines, name="lines", minimum=0)
    scratch_i = exact_integer(scratch_bytes, name="scratch_bytes", minimum=0)

    estimated_bytes = _estimated_geometry_bytes(
        vertices=vertices_i,
        lines=lines_i,
        scratch_bytes=scratch_i,
    )

    _ensure_resource_usage_validated(
        op_s,
        vertices=vertices_i,
        lines=lines_i,
        byte_size=estimated_bytes,
        hint=hint_s,
        budget=None,
    )


def ensure_resource_usage(
    op: str,
    *,
    vertices: int,
    lines: int,
    byte_size: int,
    hint: str | None = None,
    budget: ResourceBudget | None = None,
) -> None:
    """operation または scene の実測 aggregate 使用量を検査する。"""

    op_s = exact_string(op, name="op")
    hint_s = None if hint is None else exact_string(hint, name="hint")
    vertices_i = exact_integer(vertices, name="vertices", minimum=0)
    lines_i = exact_integer(lines, name="lines", minimum=0)
    byte_size_i = exact_integer(byte_size, name="byte_size", minimum=0)
    _ensure_resource_usage_validated(
        op_s,
        vertices=vertices_i,
        lines=lines_i,
        byte_size=byte_size_i,
        hint=hint_s,
        budget=budget,
    )


__all__ = [
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_MAX_OUTPUT_LINES",
    "DEFAULT_MAX_OUTPUT_VERTICES",
    "DEFAULT_RESOURCE_BUDGET",
    "ResourceBudget",
    "ResourceLimitError",
    "current_resource_budget",
    "ensure_geometry_output",
    "ensure_resource_usage",
    "resource_budget_context",
]
