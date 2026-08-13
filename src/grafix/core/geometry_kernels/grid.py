"""
Purpose:
    raster・SDF系処理が使う等間隔2D gridを、巨大配列の確保前に安全に計画する。
Use when:
    bounding boxとpitchからbounded gridを構築するeffectや数値kernelを実装する場合。
Constraints:
    - point_countとmax_pointsを実確保前に評価し、overflow policyを迂回しない。
    - coarseningまたはrejectの結果を診断valueとして返し、この層からemitしない。
    - origin、pitch、X/Y axisの規約を下流raster kernelと一致させる。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

import numpy as np

DEFAULT_MAX_GRID_POINTS = 4_000_000

GridOverflowPolicy: TypeAlias = Literal["reject", "coarsen"]
GridPlanDiagnosticSeverity: TypeAlias = Literal["warning", "error"]
GridPlanDiagnosticScalar: TypeAlias = None | bool | int | float | str
GridPlanDiagnosticValue: TypeAlias = (
    GridPlanDiagnosticScalar | tuple[GridPlanDiagnosticScalar, ...]
)


def round_half_away_from_zero(values: np.ndarray) -> np.ndarray:
    """0.5 境界を絶対値方向へ丸める。"""

    return np.sign(values) * np.floor(np.abs(values) + 0.5)


def _grid_axis_count(*, span: float, pitch: float, limit: int) -> int:
    ratio = float(span) / float(pitch)
    if not math.isfinite(ratio) or ratio >= float(limit):
        return int(limit) + 1
    return max(2, int(math.ceil(ratio)) + 1)


@dataclass(frozen=True, slots=True)
class GridSpec:
    """確保前に上限を検証済みの等間隔2Dグリッド仕様。"""

    origin_x: float
    origin_y: float
    pitch: float
    nx: int
    ny: int
    requested_pitch: float

    @property
    def point_count(self) -> int:
        """グリッド点数を返す。"""

        return int(self.nx) * int(self.ny)

    @property
    def coarsened(self) -> bool:
        """上限へ収めるためpitchを拡大したかを返す。"""

        return self.pitch > self.requested_pitch

    def coordinates(self) -> tuple[np.ndarray, np.ndarray]:
        """X軸・Y軸の座標配列を確保して返す。"""

        xs = self.origin_x + self.pitch * np.arange(self.nx, dtype=np.float64)
        ys = self.origin_y + self.pitch * np.arange(self.ny, dtype=np.float64)
        return xs, ys


@dataclass(frozen=True, slots=True)
class GridPlanDiagnostic:
    """グリッド計画が呼び出し側へ返す副作用のない診断情報。"""

    original_value: GridPlanDiagnosticValue
    effective_value: GridPlanDiagnosticValue
    reason: str
    severity: GridPlanDiagnosticSeverity


@dataclass(frozen=True, slots=True)
class GridPlanResult:
    """グリッド仕様と、必要な場合だけ診断情報を保持する。"""

    spec: GridSpec | None
    diagnostic: GridPlanDiagnostic | None = None


def _failed_grid_plan(
    *,
    original_value: GridPlanDiagnosticValue,
    reason: str,
    severity: GridPlanDiagnosticSeverity,
) -> GridPlanResult:
    return GridPlanResult(
        spec=None,
        diagnostic=GridPlanDiagnostic(
            original_value=original_value,
            effective_value=None,
            reason=reason,
            severity=severity,
        ),
    )


def plan_grid_from_bbox(
    mins: Sequence[float] | np.ndarray,
    maxs: Sequence[float] | np.ndarray,
    *,
    pitch: float,
    padding: float = 0.0,
    max_points: int = DEFAULT_MAX_GRID_POINTS,
    overflow: GridOverflowPolicy = "reject",
) -> GridPlanResult:
    """bboxから上限内のgridと診断情報を副作用なしで計画する。"""

    if overflow not in {"reject", "coarsen"}:
        raise ValueError(f"未知の overflow policy: {overflow!r}")

    requested_pitch = float(pitch)
    pad = float(padding)
    limit = int(max_points)
    if (
        not math.isfinite(requested_pitch)
        or requested_pitch <= 0.0
        or not math.isfinite(pad)
        or pad < 0.0
        or limit < 4
    ):
        return _failed_grid_plan(
            original_value=(requested_pitch, pad, limit, overflow),
            reason="invalid grid pitch, padding, or point limit was rejected",
            severity="warning",
        )

    min_x = float(mins[0])
    min_y = float(mins[1])
    max_x = float(maxs[0])
    max_y = float(maxs[1])
    if not all(math.isfinite(v) for v in (min_x, min_y, max_x, max_y)):
        return _failed_grid_plan(
            original_value=(requested_pitch, pad, limit, overflow),
            reason="non-finite bounding box was rejected",
            severity="warning",
        )
    if max_x < min_x or max_y < min_y:
        return _failed_grid_plan(
            original_value=(requested_pitch, pad, limit, overflow),
            reason="inverted bounding box was rejected",
            severity="warning",
        )

    origin_x = min_x - pad
    origin_y = min_y - pad
    span_x = max_x - min_x + 2.0 * pad
    span_y = max_y - min_y + 2.0 * pad
    if (
        not all(math.isfinite(v) for v in (origin_x, origin_y, span_x, span_y))
        or span_x <= 0.0
        or span_y <= 0.0
    ):
        return _failed_grid_plan(
            original_value=(requested_pitch, pad, limit, overflow),
            reason="degenerate grid bounds were rejected",
            severity="warning",
        )

    def shape_for(candidate_pitch: float) -> tuple[int, int, int]:
        nx = _grid_axis_count(span=span_x, pitch=candidate_pitch, limit=limit)
        ny = _grid_axis_count(span=span_y, pitch=candidate_pitch, limit=limit)
        return nx, ny, int(nx) * int(ny)

    effective_pitch = requested_pitch
    nx, ny, points = shape_for(effective_pitch)
    if points > limit:
        if overflow == "reject":
            return _failed_grid_plan(
                original_value=(requested_pitch, points, limit, overflow),
                reason="requested grid exceeded the point limit and was rejected",
                severity="warning",
            )

        low = effective_pitch
        high = effective_pitch
        while True:
            high *= 2.0
            if not math.isfinite(high):
                return _failed_grid_plan(
                    original_value=(requested_pitch, points, limit, overflow),
                    reason="grid could not be coarsened to a finite pitch",
                    severity="error",
                )
            nx_high, ny_high, points_high = shape_for(high)
            if points_high <= limit:
                break

        for _ in range(64):
            middle = low + 0.5 * (high - low)
            if middle == low or middle == high:
                break
            _nx_mid, _ny_mid, points_mid = shape_for(middle)
            if points_mid > limit:
                low = middle
            else:
                high = middle

        effective_pitch = high
        nx, ny, points = shape_for(effective_pitch)
        if points > limit:
            return _failed_grid_plan(
                original_value=(requested_pitch, points, limit, overflow),
                reason="grid coarsening did not satisfy the point limit",
                severity="error",
            )

        diagnostic = GridPlanDiagnostic(
            original_value=requested_pitch,
            effective_value=effective_pitch,
            reason="grid pitch was coarsened to satisfy the point limit",
            severity="warning",
        )
    else:
        diagnostic = None

    return GridPlanResult(
        spec=GridSpec(
            origin_x=float(origin_x),
            origin_y=float(origin_y),
            pitch=float(effective_pitch),
            nx=int(nx),
            ny=int(ny),
            requested_pitch=float(requested_pitch),
        ),
        diagnostic=diagnostic,
    )
