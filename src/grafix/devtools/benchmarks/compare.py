"""
Purpose:
    2つの schema v4 run を厳格な before/after として比べ、timingーchecksumーtyped metric・contract の差を機械可読にする。
Use when:
    特定 base/head の回帰判定、comparison compatibility、metric/contract 対応付けを変更・調査する場合。
Constraints:
    - source revision の差は比較目的上許すが、environment・mode・measurement settings・case compatibility の差は既定で拒否する。
    - ``allow_incompatible`` は warning 付き調査に限り、正式な比較で不一致を隠すために使わない。
    - metric は name/phase/scope/kind/unit、contract は ID と定義、checksum は kind と digest が対応する場合だけ比べる。
Side effects:
    file 入口では2つの run JSON を読み込む。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from grafix.devtools.benchmarks.schema import (
    BenchmarkRun,
    ContractResult,
    Metric,
    freeze_json_object,
    read_benchmark_run,
)


class IncompatibleBenchmarkError(ValueError):
    """既定では比較してはいけない run/case の組を表す。"""


@dataclass(frozen=True, slots=True)
class BenchmarkComparison:
    """機械可読な benchmark 比較結果。"""

    base_run_id: str
    head_run_id: str
    environment_compatible: bool
    rows: tuple[Mapping[str, object], ...]
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "rows",
            tuple(freeze_json_object(row) for row in self.rows),
        )


def compare_run_files(
    base_path: str | Path,
    head_path: str | Path,
    *,
    allow_incompatible: bool = False,
    metric_names: tuple[str, ...] = (),
) -> BenchmarkComparison:
    """2 run を読み、source identity を無視して同一環境・case を比較する。"""

    base = read_benchmark_run(base_path)
    head = read_benchmark_run(head_path)
    return compare_runs(
        base,
        head,
        allow_incompatible=allow_incompatible,
        metric_names=metric_names,
    )


def compare_runs(
    base: BenchmarkRun,
    head: BenchmarkRun,
    *,
    allow_incompatible: bool = False,
    metric_names: tuple[str, ...] = (),
) -> BenchmarkComparison:
    """timing、指定 metric、checksum、contract を比較する。"""

    requested_metrics = tuple(dict.fromkeys(str(name) for name in metric_names))
    if any(not name for name in requested_metrics):
        raise ValueError("metric name must not be empty")

    warnings: list[str] = []
    environment_compatible = (
        base.environment.compatibility_key == head.environment.compatibility_key
    )
    if not environment_compatible:
        warnings.append("environment compatibility key differs")
    if base.meta.mode != head.meta.mode:
        warnings.append(f"measurement mode differs: {base.meta.mode} != {head.meta.mode}")
        environment_compatible = False
    all_cases_self_sampling = bool(base.cases and head.cases) and all(
        result.spec.self_sampling for result in (*base.cases, *head.cases)
    )
    measurement_fields = (
        (
            "disable_gc",
            "timeout_seconds",
        )
        if all_cases_self_sampling
        else (
            "samples",
            "warmup",
            "target_ns",
            "disable_gc",
            "timeout_seconds",
        )
    )
    differing_measurements = [
        name for name in measurement_fields if getattr(base.meta, name) != getattr(head.meta, name)
    ]
    if differing_measurements:
        warnings.append("measurement settings differ: " + ", ".join(differing_measurements))
        environment_compatible = False
    if not environment_compatible and not allow_incompatible:
        raise IncompatibleBenchmarkError("; ".join(warnings))

    base_cases = {result.spec.case_id: result for result in base.cases}
    head_cases = {result.spec.case_id: result for result in head.cases}
    missing_head = sorted(set(base_cases) - set(head_cases))
    missing_base = sorted(set(head_cases) - set(base_cases))
    if missing_head:
        warnings.append(f"head is missing cases: {', '.join(missing_head)}")
    if missing_base:
        warnings.append(f"base is missing cases: {', '.join(missing_base)}")
    if (missing_head or missing_base) and not allow_incompatible:
        raise IncompatibleBenchmarkError("; ".join(warnings))

    rows: list[dict[str, Any]] = []
    incompatibilities: list[str] = []
    for case_id in sorted(set(base_cases) & set(head_cases)):
        base_result = base_cases[case_id]
        head_result = head_cases[case_id]
        case_compatible = base_result.spec.compatibility_key == head_result.spec.compatibility_key
        if not case_compatible:
            incompatibilities.append(f"{case_id}: case compatibility key differs")

        both_measured = base_result.status in {"ok", "contract-failure"} and head_result.status in {
            "ok",
            "contract-failure",
        }
        if both_measured:
            metric_warnings, metric_rows = _compare_metrics(
                base_result.metrics,
                head_result.metrics,
                requested=requested_metrics,
            )
            incompatibilities.extend(f"{case_id}: {warning}" for warning in metric_warnings)
            contract_warnings, contract_rows = _compare_contracts(
                base_result.contracts,
                head_result.contracts,
            )
            incompatibilities.extend(f"{case_id}: {warning}" for warning in contract_warnings)
        else:
            # error/timeout 等は metric/contract を持たないのが正常である。
            # 定義欠落として比較を拒否せず、status の変化を row に残す。
            metric_warnings, metric_rows = [], []
            contract_warnings, contract_rows = [], []

        base_stats = base_result.stats
        head_stats = head_result.stats
        base_ns = None if base_stats is None else base_stats.median_ns
        head_ns = None if head_stats is None else head_stats.median_ns
        checksum_kind_equal = (
            base_result.checksum_kind is not None
            and base_result.checksum_kind == head_result.checksum_kind
        )
        rows.append(
            {
                "case_id": case_id,
                "label": head_result.spec.label,
                "compatible": (case_compatible and not metric_warnings and not contract_warnings),
                "base_status": base_result.status,
                "head_status": head_result.status,
                "base_median_ns": base_ns,
                "head_median_ns": head_ns,
                "ratio": _ratio(base_ns, head_ns),
                "base_mad_ns": None if base_stats is None else base_stats.mad_ns,
                "head_mad_ns": None if head_stats is None else head_stats.mad_ns,
                "base_p95_ns": None if base_stats is None else base_stats.p95_ns,
                "head_p95_ns": None if head_stats is None else head_stats.p95_ns,
                "p95_ratio": _ratio(
                    None if base_stats is None else base_stats.p95_ns,
                    None if head_stats is None else head_stats.p95_ns,
                ),
                "base_p99_ns": None if base_stats is None else base_stats.p99_ns,
                "head_p99_ns": None if head_stats is None else head_stats.p99_ns,
                "p99_ratio": _ratio(
                    None if base_stats is None else base_stats.p99_ns,
                    None if head_stats is None else head_stats.p99_ns,
                ),
                "checksum_equal": (
                    checksum_kind_equal
                    and base_result.checksum is not None
                    and base_result.checksum == head_result.checksum
                ),
                "checksum_kind_equal": checksum_kind_equal,
                "base_checksum_kind": base_result.checksum_kind,
                "head_checksum_kind": head_result.checksum_kind,
                "base_peak_rss_delta_bytes": base_result.peak_rss_delta_bytes,
                "head_peak_rss_delta_bytes": head_result.peak_rss_delta_bytes,
                "metrics": metric_rows,
                "contracts": contract_rows,
                "base_hard_contracts_passed": _contracts_passed(
                    base_result.contracts,
                    severity="hard",
                ),
                "head_hard_contracts_passed": _contracts_passed(
                    head_result.contracts,
                    severity="hard",
                ),
                "base_soft_contracts_passed": _contracts_passed(
                    base_result.contracts,
                    severity="soft",
                ),
                "head_soft_contracts_passed": _contracts_passed(
                    head_result.contracts,
                    severity="soft",
                ),
            }
        )

    if incompatibilities:
        warnings.extend(incompatibilities)
        if not allow_incompatible:
            raise IncompatibleBenchmarkError("; ".join(warnings))

    return BenchmarkComparison(
        base_run_id=base.meta.run_id,
        head_run_id=head.meta.run_id,
        environment_compatible=environment_compatible,
        rows=tuple(rows),
        warnings=tuple(warnings),
    )


def _compare_metrics(
    base_metrics: tuple[Metric, ...],
    head_metrics: tuple[Metric, ...],
    *,
    requested: tuple[str, ...],
) -> tuple[list[str], list[dict[str, Any]]]:
    base_by_identity = {
        (metric.name, metric.phase, metric.scope): metric for metric in base_metrics
    }
    head_by_identity = {
        (metric.name, metric.phase, metric.scope): metric for metric in head_metrics
    }
    warnings: list[str] = []
    missing_head = sorted(set(base_by_identity) - set(head_by_identity))
    missing_base = sorted(set(head_by_identity) - set(base_by_identity))
    if missing_head:
        warnings.append(
            "head is missing metrics: " + ", ".join(_metric_label(item) for item in missing_head)
        )
    if missing_base:
        warnings.append(
            "base is missing metrics: " + ", ".join(_metric_label(item) for item in missing_base)
        )

    rows: list[dict[str, Any]] = []
    found_names: set[str] = set()
    for identity in sorted(set(base_by_identity) & set(head_by_identity)):
        base = base_by_identity[identity]
        head = head_by_identity[identity]
        if base.kind != head.kind or base.unit != head.unit:
            warnings.append(
                f"metric {_metric_label(identity)} kind/unit differs: "
                f"{base.kind}/{base.unit} != {head.kind}/{head.unit}"
            )
            continue
        if requested and base.name not in requested:
            continue
        found_names.add(base.name)
        rows.append(_metric_comparison(base, head))
    missing_requested = sorted(set(requested) - found_names)
    if missing_requested:
        warnings.append("requested metrics are missing: " + ", ".join(missing_requested))
    return warnings, rows


def _compare_contracts(
    base_contracts: tuple[ContractResult, ...],
    head_contracts: tuple[ContractResult, ...],
) -> tuple[list[str], list[dict[str, Any]]]:
    base_by_id = {contract.contract_id: contract for contract in base_contracts}
    head_by_id = {contract.contract_id: contract for contract in head_contracts}
    warnings: list[str] = []
    missing_head = sorted(set(base_by_id) - set(head_by_id))
    missing_base = sorted(set(head_by_id) - set(base_by_id))
    if missing_head:
        warnings.append("head is missing contracts: " + ", ".join(missing_head))
    if missing_base:
        warnings.append("base is missing contracts: " + ", ".join(missing_base))
    rows: list[dict[str, Any]] = []
    for contract_id in sorted(set(base_by_id) & set(head_by_id)):
        base = base_by_id[contract_id]
        head = head_by_id[contract_id]
        if (
            base.severity != head.severity
            or base.comparator != head.comparator
            or base.limit != head.limit
            or base.reason != head.reason
        ):
            warnings.append(f"contract {contract_id} definition differs")
            continue
        rows.append(
            {
                "contract_id": contract_id,
                "severity": head.severity,
                "comparator": head.comparator,
                "limit": head.limit,
                "reason": head.reason,
                "base_actual": base.actual,
                "head_actual": head.actual,
                "base_passed": base.passed,
                "head_passed": head.passed,
            }
        )
    return warnings, rows


def _metric_comparison(base: Metric, head: Metric) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": head.name,
        "kind": head.kind,
        "unit": head.unit,
        "phase": head.phase,
        "scope": head.scope,
        "base_value": base.value,
        "head_value": head.value,
        "ratio": _ratio(base.value, head.value),
        "base_distribution": (None if base.distribution is None else asdict(base.distribution)),
        "head_distribution": (None if head.distribution is None else asdict(head.distribution)),
    }
    if base.distribution is not None and head.distribution is not None:
        row["median_ratio"] = _ratio(
            base.distribution.median,
            head.distribution.median,
        )
        row["p95_ratio"] = _ratio(
            base.distribution.p95,
            head.distribution.p95,
        )
        row["p99_ratio"] = _ratio(
            base.distribution.p99,
            head.distribution.p99,
        )
    return row


def _ratio(base: object, head: object) -> float | None:
    if (
        not isinstance(base, (int, float))
        or isinstance(base, bool)
        or not isinstance(head, (int, float))
        or isinstance(head, bool)
        or float(base) <= 0.0
    ):
        return None
    return float(head) / float(base)


def _contracts_passed(
    contracts: tuple[ContractResult, ...],
    *,
    severity: str,
) -> bool:
    return all(contract.passed for contract in contracts if contract.severity == severity)


def _metric_label(identity: tuple[str, str, str]) -> str:
    name, phase, scope = identity
    return f"{name}[{phase}/{scope}]"


__all__ = [
    "BenchmarkComparison",
    "IncompatibleBenchmarkError",
    "compare_run_files",
    "compare_runs",
]
