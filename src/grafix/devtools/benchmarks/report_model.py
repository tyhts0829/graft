"""Benchmark run から履歴中心の有限な report view model を構築する。"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TypeAlias, cast

from grafix.devtools.benchmarks.schema import (
    BenchmarkRun,
    CaseResult,
    ContractResult,
    materialize_json_object,
)

_MEASURED_STATUSES = frozenset(("ok", "contract-failure"))
_DIRECTIONAL_COMPARATORS = frozenset(("lt", "le", "gt", "ge"))
_MeasurementSignature: TypeAlias = tuple[tuple[str, object], ...]
_CohortSignature: TypeAlias = tuple[str, str, str, _MeasurementSignature]


@dataclass(frozen=True, slots=True)
class SummaryView:
    """最新の時刻付き run の health summary。"""

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
class HistorySummaryView:
    """読み込んだ benchmark history の範囲と coverage。"""

    start_at: str | None
    end_at: str | None
    run_count: int
    temporal_run_count: int
    unique_case_count: int
    cohort_count: int
    trend_cohort_count: int
    latest_warning_count: int
    historical_warning_count: int


@dataclass(frozen=True, slots=True)
class TrendCaseView:
    """case selector と small multiples に使う case summary。"""

    case_id: str
    label: str
    category: str
    tags: tuple[str, ...]
    selector_label: str
    current_cohort_id: str | None
    cohort_count: int
    successful_observation_count: int
    historical: bool


@dataclass(frozen=True, slots=True)
class TrendPointView:
    """同一 compatibility cohort 内の 1 timing observation。"""

    case_id: str
    label: str
    category: str
    tags: tuple[str, ...]
    case_selector_label: str
    cohort_id: str
    cohort_label: str
    is_current: bool
    run_id: str
    created_at: str
    run_index: int
    segment_id: int
    status: str
    median_ms: float
    mad_ms: float
    mad_low_ms: float
    mad_high_ms: float
    p95_ms: float | None
    p99_ms: float | None
    sample_count: int
    source_commit: str
    source_dirty: bool | None
    mode: str
    measurement_label: str
    checksum: str
    checksum_changed: bool
    is_latest: bool


@dataclass(frozen=True, slots=True)
class CoveragePointView:
    """観測された case が run で採用・分離・失敗した理由。"""

    case_id: str
    case_selector_label: str
    run_id: str
    created_at: str
    run_index: int
    cohort_id: str | None
    state: str
    reason: str
    status: str
    is_current: bool


@dataclass(frozen=True, slots=True)
class RunTimelineView:
    """coverage の基準となる 1 run の時刻。"""

    run_id: str
    created_at: str
    run_index: int


@dataclass(frozen=True, slots=True)
class TimingView:
    """最新 run の補助的な 1 case timing。"""

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
class GuardrailTrendView:
    """同じ contract 定義を時系列で追う directional soft guardrail。"""

    case_id: str
    contract_id: str
    cohort_id: str
    cohort_label: str
    is_current: bool
    run_id: str
    created_at: str
    run_index: int
    segment_id: int
    passed: bool
    actual: float
    limit: float
    load_ratio: float
    direction: str
    reason: str


@dataclass(frozen=True, slots=True)
class ReportViewModel:
    """report の表と chart が共有する history-first view model。"""

    summary: SummaryView | None
    history_summary: HistorySummaryView
    latest_run_id: str | None
    cases: tuple[TrendCaseView, ...]
    history: tuple[TrendPointView, ...]
    timeline: tuple[RunTimelineView, ...]
    coverage: tuple[CoveragePointView, ...]
    timings: tuple[TimingView, ...]
    guardrails: tuple[GuardrailTrendView, ...]
    history_warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Occurrence:
    run: BenchmarkRun
    result: CaseResult
    run_index: int
    created_at: str
    signature: _CohortSignature
    cohort_id: str


@dataclass(frozen=True, slots=True)
class _GuardrailObservation:
    """chartable な 1 guardrail observation と系列内の線分番号。"""

    run: BenchmarkRun
    run_index: int
    result: CaseResult
    contract: ContractResult
    case_cohort_id: str
    series_id: str
    segment_id: int
    load_ratio: float
    direction: str


def parse_created_at_utc(value: str) -> datetime | None:
    """timezone 付き ISO 8601 timestamp を UTC datetime に正規化する。"""

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def chronological_runs(runs: tuple[BenchmarkRun, ...]) -> tuple[BenchmarkRun, ...]:
    """時刻を解釈できる run を UTC timestamp と run ID で安定 sort する。"""

    dated = (
        (timestamp, run.meta.run_id, run)
        for run in runs
        if (timestamp := parse_created_at_utc(run.meta.created_at)) is not None
    )
    return tuple(run for _timestamp, _run_id, run in sorted(dated, key=lambda row: row[:2]))


def latest_valid_run(runs: tuple[BenchmarkRun, ...]) -> BenchmarkRun | None:
    """時刻を解釈できる最新 run を返す。"""

    ordered = chronological_runs(runs)
    return None if not ordered else ordered[-1]


def measurement_signature(run: BenchmarkRun, result: CaseResult) -> _MeasurementSignature:
    """case の実効 measurement settings を canonical tuple にする。"""

    fields = (
        ("disable_gc", "timeout_seconds")
        if result.spec.self_sampling
        else (
            "samples",
            "warmup",
            "target_ns",
            "disable_gc",
            "timeout_seconds",
        )
    )
    return tuple((field, getattr(run.meta, field)) for field in fields)


def trend_cohort_key(run: BenchmarkRun, result: CaseResult) -> str:
    """case/environment/mode/実効設定から短い安定 cohort ID を返す。"""

    payload = (
        result.spec.case_id,
        result.spec.compatibility_key,
        run.environment.compatibility_key,
        run.meta.mode,
        measurement_signature(run, result),
    )
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]


def build_report_view_model(
    runs: tuple[BenchmarkRun, ...],
    *,
    warning_count: int = 0,
    latest_warning_count: int = 0,
    historical_warning_count: int | None = None,
) -> ReportViewModel:
    """検証済み schema v4 run から report view model を構築する。"""

    ordered = chronological_runs(runs)
    invalid_timestamp_warnings = tuple(
        f"{run.meta.run_id}: invalid created_at for history: {run.meta.created_at!r}"
        for run in runs
        if parse_created_at_utc(run.meta.created_at) is None
    )
    latest = None if not ordered else ordered[-1]
    occurrences, case_results = _index_occurrences(ordered)
    current_cohorts = {
        case_id: rows[-1].cohort_id
        for case_id, rows in case_results.items()
        if rows
    }
    cohort_labels = _cohort_labels(occurrences, current_cohorts=current_cohorts)
    history = _trend_points(
        occurrences,
        current_cohorts=current_cohorts,
        cohort_labels=cohort_labels,
        latest_run_id=None if latest is None else latest.meta.run_id,
        latest_case_ids=(
            frozenset() if latest is None else frozenset(result.spec.case_id for result in latest.cases)
        ),
    )
    cases = _trend_cases(
        ordered,
        case_results=case_results,
        current_cohorts=current_cohorts,
        history=history,
        latest=latest,
    )
    coverage = _coverage_points(
        ordered,
        cases=cases,
        current_cohorts=current_cohorts,
        occurrences=occurrences,
        history=history,
    )
    historical_count = (
        max(0, warning_count - latest_warning_count)
        if historical_warning_count is None
        else historical_warning_count
    )
    summary = HistorySummaryView(
        start_at=None if not ordered else _normalized_created_at(ordered[0]),
        end_at=None if not ordered else _normalized_created_at(ordered[-1]),
        run_count=len(runs),
        temporal_run_count=len(ordered),
        unique_case_count=len(cases),
        cohort_count=len(occurrences),
        trend_cohort_count=_trend_cohort_count(history),
        latest_warning_count=latest_warning_count,
        historical_warning_count=historical_count + len(invalid_timestamp_warnings),
    )
    return ReportViewModel(
        summary=(
            None
            if latest is None
            else _summary_view(latest, warning_count=latest_warning_count)
        ),
        history_summary=summary,
        latest_run_id=None if latest is None else latest.meta.run_id,
        cases=cases,
        history=history,
        timeline=_run_timeline(ordered),
        coverage=coverage,
        timings=() if latest is None else _timing_views(latest),
        guardrails=_guardrail_trends(
            ordered,
            cohort_labels=cohort_labels,
        ),
        history_warnings=invalid_timestamp_warnings,
    )


def _cohort_signature(run: BenchmarkRun, result: CaseResult) -> _CohortSignature:
    return (
        result.spec.compatibility_key,
        run.environment.compatibility_key,
        run.meta.mode,
        measurement_signature(run, result),
    )


def _index_occurrences(
    runs: tuple[BenchmarkRun, ...],
) -> tuple[dict[tuple[str, str], tuple[_Occurrence, ...]], dict[str, tuple[_Occurrence, ...]]]:
    by_cohort: dict[tuple[str, str], list[_Occurrence]] = defaultdict(list)
    by_case: dict[str, list[_Occurrence]] = defaultdict(list)
    for run_index, run in enumerate(runs):
        created_at = _normalized_created_at(run)
        for result in run.cases:
            cohort_id = trend_cohort_key(run, result)
            occurrence = _Occurrence(
                run=run,
                result=result,
                run_index=run_index,
                created_at=created_at,
                signature=_cohort_signature(run, result),
                cohort_id=cohort_id,
            )
            by_cohort[(result.spec.case_id, cohort_id)].append(occurrence)
            by_case[result.spec.case_id].append(occurrence)
    return (
        {key: tuple(value) for key, value in by_cohort.items()},
        {key: tuple(value) for key, value in by_case.items()},
    )


def _cohort_labels(
    occurrences: dict[tuple[str, str], tuple[_Occurrence, ...]],
    *,
    current_cohorts: dict[str, str],
) -> dict[tuple[str, str], str]:
    labels: dict[tuple[str, str], str] = {}
    for (case_id, cohort_id), rows in occurrences.items():
        reference = rows[-1]
        successful = sum(row.result.status == "ok" for row in rows)
        state = "current" if current_cohorts.get(case_id) == cohort_id else "past"
        environment = _environment_label(reference.run)
        measurement = _measurement_label(reference.run, reference.result)
        labels[(case_id, cohort_id)] = (
            f"{state} · {successful} ok · {environment} · "
            f"{reference.run.meta.mode} · {measurement}"
        )
    return labels


def _trend_points(
    occurrences: dict[tuple[str, str], tuple[_Occurrence, ...]],
    *,
    current_cohorts: dict[str, str],
    cohort_labels: dict[tuple[str, str], str],
    latest_run_id: str | None,
    latest_case_ids: frozenset[str],
) -> tuple[TrendPointView, ...]:
    points: list[TrendPointView] = []
    for key in sorted(occurrences):
        case_id, cohort_id = key
        rows = occurrences[key]
        segment_id = 0
        previous_checksum: str | None = None
        for occurrence in rows:
            result = occurrence.result
            if result.status not in _MEASURED_STATUSES or result.stats is None:
                segment_id += 1
                continue
            median_ms = float(result.stats.median_ns) / 1_000_000.0
            mad_ms = float(result.stats.mad_ns) / 1_000_000.0
            if not all(math.isfinite(value) and value >= 0.0 for value in (median_ms, mad_ms)):
                segment_id += 1
                continue
            checksum_changed = (
                previous_checksum is not None and result.checksum != previous_checksum
            )
            if checksum_changed or result.status != "ok":
                segment_id += 1
            points.append(
                TrendPointView(
                    case_id=case_id,
                    label=result.spec.label,
                    category=result.spec.category,
                    tags=result.spec.tags,
                    case_selector_label=_case_selector_label(
                        result,
                        historical=case_id not in latest_case_ids,
                    ),
                    cohort_id=cohort_id,
                    cohort_label=cohort_labels[key],
                    is_current=current_cohorts.get(case_id) == cohort_id,
                    run_id=occurrence.run.meta.run_id,
                    created_at=occurrence.created_at,
                    run_index=occurrence.run_index,
                    segment_id=segment_id,
                    status=result.status,
                    median_ms=median_ms,
                    mad_ms=mad_ms,
                    mad_low_ms=max(0.0, median_ms - mad_ms),
                    mad_high_ms=median_ms + mad_ms,
                    p95_ms=(
                        None
                        if result.stats.p95_ns is None
                        else float(result.stats.p95_ns) / 1_000_000.0
                    ),
                    p99_ms=(
                        None
                        if result.stats.p99_ns is None
                        else float(result.stats.p99_ns) / 1_000_000.0
                    ),
                    sample_count=result.stats.n,
                    source_commit=occurrence.run.source.commit or "unavailable",
                    source_dirty=occurrence.run.source.dirty,
                    mode=occurrence.run.meta.mode,
                    measurement_label=_measurement_label(occurrence.run, result),
                    checksum=result.checksum or "unavailable",
                    checksum_changed=checksum_changed,
                    is_latest=occurrence.run.meta.run_id == latest_run_id,
                )
            )
            previous_checksum = result.checksum
            if result.status != "ok":
                segment_id += 1
    points.sort(key=lambda row: (row.case_id, row.cohort_id, row.run_index, row.run_id))
    return tuple(points)


def _trend_cases(
    all_runs: tuple[BenchmarkRun, ...],
    *,
    case_results: dict[str, tuple[_Occurrence, ...]],
    current_cohorts: dict[str, str],
    history: tuple[TrendPointView, ...],
    latest: BenchmarkRun | None,
) -> tuple[TrendCaseView, ...]:
    latest_case_ids = set() if latest is None else {result.spec.case_id for result in latest.cases}
    latest_any_result: dict[str, CaseResult] = {}
    for run in all_runs:
        for result in run.cases:
            latest_any_result[result.spec.case_id] = result
    success_counts: dict[tuple[str, str], int] = defaultdict(int)
    for point in history:
        if point.status == "ok":
            success_counts[(point.case_id, point.cohort_id)] += 1
    rows: list[TrendCaseView] = []
    for case_id in sorted(latest_any_result):
        result = latest_any_result[case_id]
        historical = case_id not in latest_case_ids
        cohort_ids = {row.cohort_id for row in case_results.get(case_id, ())}
        current_cohort_id = current_cohorts.get(case_id)
        rows.append(
            TrendCaseView(
                case_id=case_id,
                label=result.spec.label,
                category=result.spec.category,
                tags=result.spec.tags,
                selector_label=_case_selector_label(result, historical=historical),
                current_cohort_id=current_cohort_id,
                cohort_count=len(cohort_ids),
                successful_observation_count=(
                    0
                    if current_cohort_id is None
                    else success_counts.get((case_id, current_cohort_id), 0)
                ),
                historical=historical,
            )
        )
    rows.sort(
        key=lambda row: (
            row.historical,
            -row.successful_observation_count,
            row.case_id,
        )
    )
    return tuple(rows)


def _coverage_points(
    runs: tuple[BenchmarkRun, ...],
    *,
    cases: tuple[TrendCaseView, ...],
    current_cohorts: dict[str, str],
    occurrences: dict[tuple[str, str], tuple[_Occurrence, ...]],
    history: tuple[TrendPointView, ...],
) -> tuple[CoveragePointView, ...]:
    occurrence_by_run_case = {
        (row.run.meta.run_id, row.result.spec.case_id): row
        for rows in occurrences.values()
        for row in rows
    }
    latest_signature = {
        case.case_id: occurrences[(case.case_id, case.current_cohort_id)][-1].signature
        for case in cases
        if case.current_cohort_id is not None
    }
    checksum_changes = {
        (point.run_id, point.case_id)
        for point in history
        if point.checksum_changed
    }
    cases_by_id = {case.case_id: case for case in cases}
    coverage_rows: list[CoveragePointView] = []
    for run_index, run in enumerate(runs):
        for result in run.cases:
            case_id = result.spec.case_id
            case = cases_by_id[case_id]
            occurrence = occurrence_by_run_case[(run.meta.run_id, case_id)]
            cohort_id = occurrence.cohort_id
            status = result.status
            is_current = current_cohorts.get(case_id) == cohort_id
            if not is_current:
                state = "other-cohort"
                difference = _cohort_difference_reason(
                    occurrence.signature,
                    latest_signature[case_id],
                )
                reason = (
                    difference
                    if status == "ok"
                    else f"{difference}; case status is {status}"
                )
            elif status != "ok":
                state = "failure"
                reason = f"current cohort status is {status}"
            elif (run.meta.run_id, case_id) in checksum_changes:
                state = "output-changed"
                reason = "checksum changed within the current cohort"
            else:
                state = "current"
                reason = "included in the current compatibility cohort"
            coverage_rows.append(
                CoveragePointView(
                    case_id=case_id,
                    case_selector_label=case.selector_label,
                    run_id=run.meta.run_id,
                    created_at=_normalized_created_at(run),
                    run_index=run_index,
                    cohort_id=cohort_id,
                    state=state,
                    reason=reason,
                    status=status,
                    is_current=is_current,
                )
            )
    return tuple(coverage_rows)


def _run_timeline(runs: tuple[BenchmarkRun, ...]) -> tuple[RunTimelineView, ...]:
    return tuple(
        RunTimelineView(
            run_id=run.meta.run_id,
            created_at=_normalized_created_at(run),
            run_index=run_index,
        )
        for run_index, run in enumerate(runs)
    )


def _guardrail_trends(
    runs: tuple[BenchmarkRun, ...],
    *,
    cohort_labels: dict[tuple[str, str], str],
) -> tuple[GuardrailTrendView, ...]:
    observations: list[_GuardrailObservation] = []
    latest_occurrences: dict[str, tuple[str, CaseResult]] = {}
    case_occurrence_count: dict[str, int] = defaultdict(int)
    latest_case_occurrence_by_series: dict[str, int] = {}
    segment_by_series: dict[str, int] = defaultdict(int)
    for run_index, run in enumerate(runs):
        for result in run.cases:
            case_id = result.spec.case_id
            case_occurrence_index = case_occurrence_count[case_id]
            case_occurrence_count[case_id] += 1
            case_cohort_id = trend_cohort_key(run, result)
            latest_occurrences[case_id] = (case_cohort_id, result)
            for contract in result.contracts:
                normalized = _normalized_guardrail(contract)
                if normalized is None:
                    continue
                load_ratio, direction = normalized
                series_id = _guardrail_cohort_id(case_cohort_id, contract)
                previous_occurrence = latest_case_occurrence_by_series.get(series_id)
                if (
                    previous_occurrence is not None
                    and case_occurrence_index != previous_occurrence + 1
                ):
                    segment_by_series[series_id] += 1
                observations.append(
                    _GuardrailObservation(
                        run=run,
                        run_index=run_index,
                        result=result,
                        contract=contract,
                        case_cohort_id=case_cohort_id,
                        series_id=series_id,
                        segment_id=segment_by_series[series_id],
                        load_ratio=load_ratio,
                        direction=direction,
                    )
                )
                latest_case_occurrence_by_series[series_id] = case_occurrence_index

    current_series = {
        _guardrail_cohort_id(case_cohort_id, contract)
        for case_cohort_id, result in latest_occurrences.values()
        for contract in result.contracts
        if _normalized_guardrail(contract) is not None
    }
    rows: list[GuardrailTrendView] = []
    for observation in observations:
        run = observation.run
        result = observation.result
        contract = observation.contract
        series_label = (
            f"{contract.contract_id} · {contract.comparator} {contract.limit} · "
            f"{contract.reason} · "
            f"{cohort_labels.get((result.spec.case_id, observation.case_cohort_id), observation.case_cohort_id)}"
        )
        rows.append(
            GuardrailTrendView(
                case_id=result.spec.case_id,
                contract_id=contract.contract_id,
                cohort_id=observation.series_id,
                cohort_label=series_label,
                is_current=observation.series_id in current_series,
                run_id=run.meta.run_id,
                created_at=_normalized_created_at(run),
                run_index=observation.run_index,
                segment_id=observation.segment_id,
                passed=contract.passed,
                actual=float(contract.actual),
                limit=float(contract.limit),
                load_ratio=observation.load_ratio,
                direction=observation.direction,
                reason=contract.reason,
            )
        )
    rows.sort(key=lambda row: (not row.is_current, row.case_id, row.contract_id, row.run_index))
    return tuple(rows)


def _guardrail_cohort_id(case_cohort_id: str, contract: ContractResult) -> str:
    payload = (
        case_cohort_id,
        contract.contract_id,
        contract.severity,
        contract.comparator,
        float(contract.limit),
        contract.reason,
    )
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]


def _normalized_guardrail(contract: ContractResult) -> tuple[float, str] | None:
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
    load_ratio = actual / limit
    direction = (
        "lower is better"
        if contract.comparator in {"lt", "le"}
        else "higher is better"
    )
    if not math.isfinite(load_ratio):
        return None
    return load_ratio, direction


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
        created_at=_normalized_created_at(run),
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


def _environment_label(run: BenchmarkRun) -> str:
    values = materialize_json_object(run.environment.values)
    hardware = values.get("hardware")
    python = values.get("python")
    cpu = hardware.get("cpu") if isinstance(hardware, dict) else None
    implementation = python.get("implementation") if isinstance(python, dict) else None
    version = python.get("version") if isinstance(python, dict) else None
    machine = str(cpu or "unknown CPU")
    runtime = " ".join(str(value) for value in (implementation, version) if value)
    return f"{machine} / {runtime or 'unknown Python'}"


def _measurement_label(run: BenchmarkRun, result: CaseResult) -> str:
    values = dict(measurement_signature(run, result))
    parts: list[str] = []
    if not result.spec.self_sampling:
        parts.extend(
            (
                f"samples={values['samples']}",
                f"warmup={values['warmup']}",
                f"target={float(cast(int | float, values['target_ns'])) / 1_000_000.0:g}ms",
            )
        )
    parts.extend(
        (
            f"gc={'off' if values['disable_gc'] else 'on'}",
            f"timeout={float(cast(int | float, values['timeout_seconds'])):g}s",
        )
    )
    return ", ".join(parts)


def _case_selector_label(result: CaseResult, *, historical: bool) -> str:
    suffix = " · historical" if historical else ""
    return (
        f"{result.spec.category} / {result.spec.label} · "
        f"{result.spec.case_id}{suffix}"
    )


def _cohort_difference_reason(
    candidate: _CohortSignature,
    current: _CohortSignature,
) -> str:
    reasons: list[str] = []
    if candidate[0] != current[0]:
        reasons.append("case definition differs")
    if candidate[1] != current[1]:
        reasons.append("environment differs")
    if candidate[2] != current[2]:
        reasons.append("measurement mode differs")
    if candidate[3] != current[3]:
        reasons.append("measurement settings differ")
    return ", ".join(reasons) or "different compatibility cohort"


def _trend_cohort_count(history: tuple[TrendPointView, ...]) -> int:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for point in history:
        if point.status == "ok":
            counts[(point.case_id, point.cohort_id)] += 1
    return sum(count >= 2 for count in counts.values())


def _normalized_created_at(run: BenchmarkRun) -> str:
    timestamp = parse_created_at_utc(run.meta.created_at)
    assert timestamp is not None
    return timestamp.isoformat().replace("+00:00", "Z")


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


__all__ = [
    "CoveragePointView",
    "GuardrailTrendView",
    "HistorySummaryView",
    "ReportViewModel",
    "RunTimelineView",
    "SummaryView",
    "TimingView",
    "TrendCaseView",
    "TrendPointView",
    "build_report_view_model",
    "chronological_runs",
    "latest_valid_run",
    "measurement_signature",
    "parse_created_at_utc",
    "trend_cohort_key",
]
