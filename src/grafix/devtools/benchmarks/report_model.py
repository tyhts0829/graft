"""Benchmark run から可視化用の有限・比較可能な view model を構築する。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from grafix.devtools.benchmarks.schema import (
    BenchmarkRun,
    CaseResult,
    ContractResult,
)

_MEASURED_STATUSES = frozenset(("ok", "contract-failure"))
_DIRECTIONAL_COMPARATORS = frozenset(("lt", "le", "gt", "ge"))


@dataclass(frozen=True, slots=True)
class SummaryView:
    """最新 run の状態を report 冒頭へ表示するための要約。"""

    run_id: str
    created_at: str
    source: str
    suite: str
    profile: str
    mode: str
    case_count: int
    status_counts: tuple[tuple[str, int], ...]
    warning_count: int
    hard_passed: int
    hard_total: int
    soft_passed: int
    soft_total: int


@dataclass(frozen=True, slots=True)
class DeltaView:
    """互換な base/head の 1 case 差分。"""

    base_run_id: str
    head_run_id: str
    case_id: str
    label: str
    category: str
    base_median_ms: float
    head_median_ms: float
    base_mad_ms: float
    head_mad_ms: float
    delta_fraction: float
    direction: str


@dataclass(frozen=True, slots=True)
class RunComparisonView:
    """1 run と、その直前の完全互換 run の比較。"""

    head_run_id: str
    base_run_id: str | None
    unavailable_reason: str | None
    deltas: tuple[DeltaView, ...]


@dataclass(frozen=True, slots=True)
class TimingView:
    """最新 run の 1 case timing。"""

    case_id: str
    label: str
    category: str
    status: str
    self_sampling: bool
    median_ms: float
    mad_ms: float
    p95_ms: float | None
    p99_ms: float | None


@dataclass(frozen=True, slots=True)
class HistoryView:
    """互換性境界で分割済みの case timing 時系列点。"""

    case_id: str
    label: str
    category: str
    run_id: str
    created_at: str
    run_index: int
    segment_id: int
    view_mode: str
    value: float
    low: float
    high: float
    unit: str
    value_label: str
    mad_label: str


@dataclass(frozen=True, slots=True)
class GuardrailView:
    """数値 soft contract の比較方向を揃えた bullet chart 用値。"""

    case_id: str
    contract_id: str
    comparator: str
    passed: bool
    actual: float
    limit: float
    load_ratio: float
    direction: str
    reason: str


@dataclass(frozen=True, slots=True)
class ReportViewModel:
    """report の表と chart が共有する不変 view model。"""

    summary: SummaryView | None
    latest_run_id: str | None
    baseline_run_id: str | None
    baseline_message: str
    comparisons: tuple[RunComparisonView, ...]
    deltas: tuple[DeltaView, ...]
    timings: tuple[TimingView, ...]
    history: tuple[HistoryView, ...]
    guardrails: tuple[GuardrailView, ...]


def build_report_view_model(
    runs: tuple[BenchmarkRun, ...],
    *,
    warning_count: int,
) -> ReportViewModel:
    """検証済み run から chart と表に共通の view model を構築する。"""

    if not runs:
        return ReportViewModel(
            summary=None,
            latest_run_id=None,
            baseline_run_id=None,
            baseline_message="No valid schema v4 run is available.",
            comparisons=(),
            deltas=(),
            timings=(),
            history=(),
            guardrails=(),
        )

    comparisons = tuple(_comparison_for_run(head, runs[:index]) for index, head in enumerate(runs))
    latest = runs[-1]
    latest_comparison = comparisons[-1]
    return ReportViewModel(
        summary=_summary_view(latest, warning_count=warning_count),
        latest_run_id=latest.meta.run_id,
        baseline_run_id=latest_comparison.base_run_id,
        baseline_message=(
            f"Compared with {latest_comparison.base_run_id}."
            if latest_comparison.base_run_id is not None
            else str(latest_comparison.unavailable_reason)
        ),
        comparisons=comparisons,
        deltas=latest_comparison.deltas,
        timings=_timing_views(latest),
        history=_history_views(runs, latest),
        guardrails=_guardrail_views(latest),
    )


def select_baseline(
    head: BenchmarkRun,
    candidates: tuple[BenchmarkRun, ...],
) -> BenchmarkRun | None:
    """head より前の run から最も近い完全互換 run を返す。"""

    return next(
        (
            candidate
            for candidate in reversed(candidates)
            if not run_compatibility_issues(candidate, head)
        ),
        None,
    )


def run_compatibility_issues(
    base: BenchmarkRun,
    head: BenchmarkRun,
) -> tuple[str, ...]:
    """全 case を同じ base/head として比較できない理由を返す。"""

    issues: list[str] = []
    if base.environment.compatibility_key != head.environment.compatibility_key:
        issues.append("environment differs")
    if base.meta.mode != head.meta.mode:
        issues.append("measurement mode differs")

    all_cases_self_sampling = bool(base.cases and head.cases) and all(
        result.spec.self_sampling for result in (*base.cases, *head.cases)
    )
    measurement_fields = (
        ("disable_gc", "timeout_seconds")
        if all_cases_self_sampling
        else (
            "samples",
            "warmup",
            "target_ns",
            "disable_gc",
            "timeout_seconds",
        )
    )
    differing_measurements = tuple(
        field
        for field in measurement_fields
        if getattr(base.meta, field) != getattr(head.meta, field)
    )
    if differing_measurements:
        issues.append("measurement settings differ: " + ", ".join(differing_measurements))

    base_cases = {result.spec.case_id: result for result in base.cases}
    head_cases = {result.spec.case_id: result for result in head.cases}
    if set(base_cases) != set(head_cases):
        issues.append("case set differs")
        return tuple(issues)

    for case_id in sorted(head_cases):
        base_result = base_cases[case_id]
        head_result = head_cases[case_id]
        if base_result.spec.compatibility_key != head_result.spec.compatibility_key:
            issues.append(f"{case_id}: case definition differs")
    return tuple(issues)


def measurement_compatible(
    base_run: BenchmarkRun,
    base_result: CaseResult,
    head_run: BenchmarkRun,
    head_result: CaseResult,
) -> bool:
    """case の計測値を同じ系列として扱えるか判定する。"""

    if base_run.meta.mode != head_run.meta.mode:
        return False
    fields = (
        ("disable_gc", "timeout_seconds")
        if base_result.spec.self_sampling and head_result.spec.self_sampling
        else (
            "samples",
            "warmup",
            "target_ns",
            "disable_gc",
            "timeout_seconds",
        )
    )
    return all(getattr(base_run.meta, field) == getattr(head_run.meta, field) for field in fields)


def comparison_delta_lookup(
    model: ReportViewModel,
) -> dict[tuple[str, str], DeltaView]:
    """詳細表から head run / case で差分を引くための mapping を返す。"""

    return {
        (comparison.head_run_id, delta.case_id): delta
        for comparison in model.comparisons
        for delta in comparison.deltas
    }


def _comparison_for_run(
    head: BenchmarkRun,
    candidates: tuple[BenchmarkRun, ...],
) -> RunComparisonView:
    base = select_baseline(head, candidates)
    if base is None:
        reason = (
            "No earlier run is available."
            if not candidates
            else (
                "No earlier run has the same environment, case definitions, "
                "and measurement settings."
            )
        )
        return RunComparisonView(
            head_run_id=head.meta.run_id,
            base_run_id=None,
            unavailable_reason=reason,
            deltas=(),
        )
    return RunComparisonView(
        head_run_id=head.meta.run_id,
        base_run_id=base.meta.run_id,
        unavailable_reason=None,
        deltas=_delta_views(base, head),
    )


def _summary_view(run: BenchmarkRun, *, warning_count: int) -> SummaryView:
    statuses: dict[str, int] = {}
    hard: list[ContractResult] = []
    soft: list[ContractResult] = []
    for result in run.cases:
        statuses[result.status] = statuses.get(result.status, 0) + 1
        hard.extend(contract for contract in result.contracts if contract.severity == "hard")
        soft.extend(contract for contract in result.contracts if contract.severity == "soft")
    status_order = ("ok", "contract-failure", "error", "timeout", "skipped")
    ordered_statuses = tuple(
        (status, statuses[status])
        for status in (*status_order, *sorted(set(statuses) - set(status_order)))
        if status in statuses
    )
    return SummaryView(
        run_id=run.meta.run_id,
        created_at=run.meta.created_at,
        source=run.source.commit or "unavailable",
        suite=run.meta.suite,
        profile=run.meta.profile,
        mode=run.meta.mode,
        case_count=len(run.cases),
        status_counts=ordered_statuses,
        warning_count=warning_count,
        hard_passed=sum(contract.passed for contract in hard),
        hard_total=len(hard),
        soft_passed=sum(contract.passed for contract in soft),
        soft_total=len(soft),
    )


def _delta_views(base: BenchmarkRun, head: BenchmarkRun) -> tuple[DeltaView, ...]:
    base_cases = {result.spec.case_id: result for result in base.cases}
    rows: list[DeltaView] = []
    for head_result in head.cases:
        base_result = base_cases[head_result.spec.case_id]
        if base_result.stats is None or head_result.stats is None:
            continue
        base_median = float(base_result.stats.median_ns) / 1_000_000.0
        head_median = float(head_result.stats.median_ns) / 1_000_000.0
        if base_median <= 0.0:
            continue
        delta = head_median / base_median - 1.0
        if not all(math.isfinite(value) for value in (base_median, head_median, delta)):
            continue
        direction = "regression" if delta > 0.0 else ("improvement" if delta < 0.0 else "unchanged")
        rows.append(
            DeltaView(
                base_run_id=base.meta.run_id,
                head_run_id=head.meta.run_id,
                case_id=head_result.spec.case_id,
                label=head_result.spec.label,
                category=head_result.spec.category,
                base_median_ms=base_median,
                head_median_ms=head_median,
                base_mad_ms=float(base_result.stats.mad_ns) / 1_000_000.0,
                head_mad_ms=float(head_result.stats.mad_ns) / 1_000_000.0,
                delta_fraction=delta,
                direction=direction,
            )
        )
    rows.sort(key=lambda row: (-row.delta_fraction, row.case_id))
    return tuple(rows)


def _timing_views(run: BenchmarkRun) -> tuple[TimingView, ...]:
    rows = [
        TimingView(
            case_id=result.spec.case_id,
            label=result.spec.label,
            category=result.spec.category,
            status=result.status,
            self_sampling=result.spec.self_sampling,
            median_ms=float(result.stats.median_ns) / 1_000_000.0,
            mad_ms=float(result.stats.mad_ns) / 1_000_000.0,
            p95_ms=(
                None if result.stats.p95_ns is None else float(result.stats.p95_ns) / 1_000_000.0
            ),
            p99_ms=(
                None if result.stats.p99_ns is None else float(result.stats.p99_ns) / 1_000_000.0
            ),
        )
        for result in run.cases
        if result.stats is not None and result.status in _MEASURED_STATUSES
    ]
    rows.sort(key=lambda row: (-row.median_ms, row.case_id))
    return tuple(rows)


def _history_views(
    runs: tuple[BenchmarkRun, ...],
    latest: BenchmarkRun,
) -> tuple[HistoryView, ...]:
    rows: list[HistoryView] = []
    latest_cases = {result.spec.case_id: result for result in latest.cases}
    for case_id in sorted(latest_cases):
        reference = latest_cases[case_id]
        segment_id = 0
        origin_ms: float | None = None
        for run_index, run in enumerate(runs):
            result = next(
                (candidate for candidate in run.cases if candidate.spec.case_id == case_id),
                None,
            )
            compatible = (
                result is not None
                and run.environment.compatibility_key == latest.environment.compatibility_key
                and result.spec.compatibility_key == reference.spec.compatibility_key
                and measurement_compatible(run, result, latest, reference)
                and result.status in _MEASURED_STATUSES
                and result.stats is not None
            )
            if not compatible or result is None or result.stats is None:
                segment_id += 1
                continue

            median_ms = float(result.stats.median_ns) / 1_000_000.0
            mad_ms = float(result.stats.mad_ns) / 1_000_000.0
            if not all(math.isfinite(value) and value >= 0.0 for value in (median_ms, mad_ms)):
                segment_id += 1
                continue
            if origin_ms is None and median_ms > 0.0:
                origin_ms = median_ms
            rows.append(
                _history_view(
                    reference=reference,
                    run=run,
                    run_index=run_index,
                    segment_id=segment_id,
                    view_mode="absolute",
                    value=median_ms,
                    low=max(0.0, median_ms - mad_ms),
                    high=median_ms + mad_ms,
                    unit="ms",
                    value_label=f"{median_ms:.6f} ms",
                    mad_label=f"{mad_ms:.6f} ms",
                )
            )
            if origin_ms is not None:
                value = (median_ms / origin_ms - 1.0) * 100.0
                low = (max(0.0, median_ms - mad_ms) / origin_ms - 1.0) * 100.0
                high = ((median_ms + mad_ms) / origin_ms - 1.0) * 100.0
                rows.append(
                    _history_view(
                        reference=reference,
                        run=run,
                        run_index=run_index,
                        segment_id=segment_id,
                        view_mode="relative",
                        value=value,
                        low=low,
                        high=high,
                        unit="%",
                        value_label=f"{value:+.2f}%",
                        mad_label=f"±{mad_ms / origin_ms * 100.0:.2f}%",
                    )
                )
    return tuple(rows)


def _history_view(
    *,
    reference: CaseResult,
    run: BenchmarkRun,
    run_index: int,
    segment_id: int,
    view_mode: str,
    value: float,
    low: float,
    high: float,
    unit: str,
    value_label: str,
    mad_label: str,
) -> HistoryView:
    return HistoryView(
        case_id=reference.spec.case_id,
        label=reference.spec.label,
        category=reference.spec.category,
        run_id=run.meta.run_id,
        created_at=run.meta.created_at,
        run_index=run_index,
        segment_id=segment_id,
        view_mode=view_mode,
        value=value,
        low=low,
        high=high,
        unit=unit,
        value_label=value_label,
        mad_label=mad_label,
    )


def _guardrail_views(run: BenchmarkRun) -> tuple[GuardrailView, ...]:
    rows: list[GuardrailView] = []
    for result in run.cases:
        for contract in result.contracts:
            row = _guardrail_view(result, contract)
            if row is not None:
                rows.append(row)
    rows.sort(key=lambda row: (row.passed, -row.load_ratio, row.contract_id))
    return tuple(rows)


def _guardrail_view(
    result: CaseResult,
    contract: ContractResult,
) -> GuardrailView | None:
    if (
        contract.severity != "soft"
        or contract.comparator not in _DIRECTIONAL_COMPARATORS
        or not _finite_number(contract.actual)
        or not _finite_number(contract.limit)
    ):
        return None
    actual = float(contract.actual)
    limit = float(contract.limit)
    if actual < 0.0 or limit <= 0.0:
        return None
    if contract.comparator in {"lt", "le"}:
        load_ratio = actual / limit
        direction = "lower is better"
    else:
        load_ratio = limit / actual if actual > 0.0 else 2.0
        direction = "higher is better"
    if not math.isfinite(load_ratio):
        return None
    return GuardrailView(
        case_id=result.spec.case_id,
        contract_id=contract.contract_id,
        comparator=contract.comparator,
        passed=contract.passed,
        actual=actual,
        limit=limit,
        load_ratio=load_ratio,
        direction=direction,
        reason=contract.reason,
    )


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


__all__ = [
    "DeltaView",
    "GuardrailView",
    "HistoryView",
    "ReportViewModel",
    "RunComparisonView",
    "SummaryView",
    "TimingView",
    "build_report_view_model",
    "comparison_delta_lookup",
    "measurement_compatible",
    "run_compatibility_issues",
    "select_baseline",
]
