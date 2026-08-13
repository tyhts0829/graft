"""
Purpose:
    benchmark output の semantic checksum と typed metric/contract を、warm/cold measurement 間で比較可能な結果へ集約する。
Use when:
    output correctness identity、metric helper、warm sample validation、cold process の結果統合を変更・調査する場合。
Constraints:
    - Geometry/array checksum は semantic order・dtype・shape・bytes を保持し、object dtype や非決定的な値を曖昧に hash しない。
    - canonical JSON checksum は finite な strict value だけを受理する。
    - warm sample 間で metric/contract definition を変えず、途中の contract failure を最後の success で隠さない。
    - cold sample の checksum が一致しない場合は timing 結果として統合しない。
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, cast

import numpy as np

from grafix.core.geometry import Geometry
from grafix.core.realized_geometry import RealizedGeometry
from grafix.devtools.benchmarks.schema import (
    BenchmarkOutput,
    CaseResult,
    CaseSpec,
    ContractResult,
    Metric,
    summarize_samples,
)


def geometry_checksum(geometry: RealizedGeometry) -> str:
    """Geometry arrays を dtype・shape・bytes 込みで SHA-256 化する。"""

    digest = hashlib.sha256()
    digest.update(b"grafix.realized-geometry.checksum.v1\0")
    _hash_array(digest, geometry.coords)
    _hash_array(digest, geometry.offsets)
    return digest.hexdigest()


def canonical_checksum(value: object) -> tuple[str, str]:
    """Benchmark output を exact checksum 化する。"""

    if type(value) is RealizedGeometry:
        return geometry_checksum(value), "realized_geometry_exact_v1"
    if type(value) is Geometry:
        digest = hashlib.sha256(b"grafix.geometry.concat-semantics.v1\0")
        stack = [value]
        leaf_count = 0
        while stack:
            geometry = stack.pop()
            if geometry.op == "concat" and not geometry.args:
                stack.extend(reversed(geometry.inputs))
                continue
            digest.update(geometry.id.encode("ascii"))
            leaf_count += 1
        digest.update(leaf_count.to_bytes(8, "big"))
        return digest.hexdigest(), "geometry_concat_leaf_order_v1"
    normalized = _json_value(value)
    encoded = json.dumps(
        normalized,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), "canonical_json_sha256_v2"


def counter_metric(
    name: str,
    value: int | float,
    *,
    unit: str,
    phase: str,
    scope: str,
) -> Metric:
    """Producer が指定した identity で counter metric を作る。"""

    return Metric(
        name=name,
        kind="counter",
        unit=unit,
        phase=phase,
        scope=scope,
        value=value,
    )


def gauge_metric(
    name: str,
    value: str | bool | int | float,
    *,
    unit: str,
    phase: str,
    scope: str,
) -> Metric:
    """Producer が指定した identity で gauge metric を作る。"""

    return Metric(
        name=name,
        kind="gauge",
        unit=unit,
        phase=phase,
        scope=scope,
        value=value,
    )


def summary_metrics(
    name: str,
    summary: dict[str, Any],
    *,
    unit: str,
    phase: str,
    scope: str,
) -> tuple[Metric, ...]:
    """既知の mean/median/p95/n summary を独立した typed metric にする。"""

    return (
        gauge_metric(
            f"{name}.mean",
            float(summary["mean"]),
            unit=unit,
            phase=phase,
            scope=scope,
        ),
        gauge_metric(
            f"{name}.median",
            float(summary["median"]),
            unit=unit,
            phase=phase,
            scope=scope,
        ),
        gauge_metric(
            f"{name}.p95",
            float(summary["p95"]),
            unit=unit,
            phase=phase,
            scope=scope,
        ),
        counter_metric(
            f"{name}.samples",
            int(summary["n"]),
            unit="count",
            phase=phase,
            scope=scope,
        ),
    )


def percentile_summary_metrics(
    name: str,
    summary: dict[str, Any],
    *,
    unit: str,
    phase: str,
    scope: str,
) -> tuple[Metric, ...]:
    """既知の median/p95/p99/max/n summary を typed metric にする。"""

    return (
        *(
            gauge_metric(
                f"{name}.{statistic}",
                float(summary[statistic]),
                unit=unit,
                phase=phase,
                scope=scope,
            )
            for statistic in ("median", "p95", "p99", "max")
        ),
        counter_metric(
            f"{name}.samples",
            int(summary["n"]),
            unit="count",
            phase=phase,
            scope=scope,
        ),
    )


def cache_metrics(
    cache: dict[str, Any],
    *,
    name: str,
    phase: str,
    scope: str,
) -> tuple[Metric, ...]:
    """既知 cache counter schema を明示 identity の typed metric にする。"""

    metrics = [
        counter_metric(
            f"{name}.{field_name}",
            int(cache[field_name]),
            unit="count",
            phase=phase,
            scope=scope,
        )
        for field_name in ("hits", "misses", "evictions", "entries")
        if field_name in cache
    ]
    for field_name in ("bytes", "budget_bytes"):
        if field_name in cache:
            metrics.append(
                counter_metric(
                    f"{name}.{field_name}",
                    int(cache[field_name]),
                    unit="bytes",
                    phase=phase,
                    scope=scope,
                )
            )
    return tuple(metrics)


def summarize_nanoseconds(samples: list[int]) -> dict[str, float | int]:
    """Nanosecond samples を既存 benchmark payload の ms summary にする。"""

    ordered = sorted(int(sample) for sample in samples)
    if not ordered:
        return {"mean_ms": 0.0, "median_ms": 0.0, "p95_ms": 0.0, "n": 0}
    mean = float(sum(ordered)) / float(len(ordered))
    return {
        "mean_ms": mean / 1_000_000.0,
        "median_ms": _percentile(ordered, 0.5) / 1_000_000.0,
        "p95_ms": _percentile(ordered, 0.95) / 1_000_000.0,
        "n": len(ordered),
    }


def aggregate_measured_outputs(
    outputs: list[BenchmarkOutput],
    *,
    last: BenchmarkOutput,
) -> BenchmarkOutput:
    """Outer sample の typed output を検証し、失敗 contract を保持する。"""

    if not outputs:
        raise RuntimeError("benchmark workload returned no measured output")
    metrics = outputs[0].metrics
    if any(output.metrics != metrics for output in outputs[1:]):
        raise RuntimeError("typed metrics changed across warm samples")

    contract_ids = tuple(contract.contract_id for contract in outputs[0].contracts)
    if any(
        tuple(contract.contract_id for contract in output.contracts) != contract_ids
        for output in outputs[1:]
    ):
        raise RuntimeError("contract set changed across warm samples")

    contracts: list[ContractResult] = []
    for index, contract_id in enumerate(contract_ids):
        samples = [output.contracts[index] for output in outputs]
        reference = samples[0]
        identity = (
            reference.contract_id,
            reference.severity,
            reference.comparator,
            reference.limit,
            reference.reason,
        )
        if any(
            (
                sample.contract_id,
                sample.severity,
                sample.comparator,
                sample.limit,
                sample.reason,
            )
            != identity
            for sample in samples[1:]
        ):
            raise RuntimeError(f"contract definition changed across warm samples: {contract_id}")
        contracts.append(next((sample for sample in samples if not sample.passed), samples[-1]))

    return BenchmarkOutput(
        value=last.value,
        metrics=metrics,
        contracts=tuple(contracts),
    )


def merge_cold_results(
    *,
    spec: CaseSpec,
    results: list[CaseResult],
) -> CaseResult:
    """Fresh process ごとの cold result を一つの case result に集約する。"""

    failures = [result for result in results if result.status not in {"ok", "contract-failure"}]
    if failures:
        first = failures[0]
        return CaseResult(spec=spec, status=first.status, error=first.error)
    checksums = {result.checksum for result in results}
    checksum_kinds = {result.checksum_kind for result in results}
    if len(checksums) != 1 or len(checksum_kinds) != 1:
        return CaseResult(
            spec=spec,
            status="error",
            error="cold samples produced different output checksums",
        )
    samples = tuple(sample for result in results for sample in result.samples)
    rss_result = max(
        results,
        key=lambda result: result.peak_rss_delta_bytes or 0,
    )
    contract_result = next(
        (result for result in results if result.status == "contract-failure"),
        results[-1],
    )
    return CaseResult(
        spec=spec,
        status=contract_result.status,
        samples=samples,
        stats=summarize_samples(samples),
        checksum=results[0].checksum,
        checksum_kind=results[0].checksum_kind,
        setup_rss_bytes=rss_result.setup_rss_bytes,
        baseline_rss_bytes=rss_result.baseline_rss_bytes,
        peak_rss_bytes=rss_result.peak_rss_bytes,
        peak_rss_delta_bytes=rss_result.peak_rss_delta_bytes,
        metrics=contract_result.metrics,
        contracts=contract_result.contracts,
        error=contract_result.error,
    )


def _hash_array(digest: Any, array: np.ndarray) -> None:
    if array.dtype.hasobject:
        raise TypeError("object dtype array cannot be checksummed deterministically")
    if array.dtype.fields is not None or array.dtype.subdtype is not None:
        raise TypeError("structured array dtype is not a benchmark checksum value")
    contiguous = np.ascontiguousarray(array)
    digest.update(contiguous.dtype.str.encode("ascii"))
    digest.update(json.dumps(list(contiguous.shape), separators=(",", ":")).encode("ascii"))
    if contiguous.nbytes:
        digest.update(memoryview(cast(Any, contiguous)).cast("B"))


def _json_value(value: object) -> object:
    if type(value) is RealizedGeometry:
        return {
            "$grafix_checksum_type": "realized_geometry",
            "coords": _json_value(value.coords),
            "offsets": _json_value(value.offsets),
        }
    if type(value) is Geometry:
        return {
            "$grafix_checksum_type": "geometry",
            "geometry_id": value.id,
            "op": value.op,
        }
    if type(value) is np.ndarray:
        digest = hashlib.sha256()
        _hash_array(digest, value)
        return {
            "$grafix_checksum_type": "ndarray",
            "dtype": value.dtype.str,
            "shape": list(value.shape),
            "sha256": digest.hexdigest(),
        }
    if isinstance(value, np.generic):
        scalar = value.item()
        if isinstance(scalar, np.generic) or type(scalar) not in {
            bool,
            int,
            float,
            str,
            bytes,
        }:
            raise TypeError(f"unsupported NumPy benchmark checksum scalar: {type(value)!r}")
        return _json_value(scalar)
    if type(value) is bytes:
        return {
            "$grafix_checksum_type": "bytes",
            "hex": value.hex(),
        }
    if type(value) is dict:
        items: list[list[object]] = []
        for key in value:
            if type(key) is not str:
                raise TypeError("benchmark JSON object keys must be exact strings")
        for key in sorted(value):
            items.append([key, _json_value(value[key])])
        return {
            "$grafix_checksum_type": "mapping",
            "items": items,
        }
    if type(value) is list:
        return [_json_value(item) for item in value]
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("benchmark JSON numbers must be finite")
        return value
    raise TypeError(f"unsupported benchmark checksum value: {type(value)!r}")


def _percentile(ordered: list[int], fraction: float) -> float:
    if len(ordered) == 1:
        return float(ordered[0])
    position = float(len(ordered) - 1) * float(fraction)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - float(lower)
    return float(ordered[lower]) * (1.0 - weight) + float(ordered[upper]) * weight


__all__ = [
    "aggregate_measured_outputs",
    "cache_metrics",
    "canonical_checksum",
    "counter_metric",
    "gauge_metric",
    "geometry_checksum",
    "merge_cold_results",
    "percentile_summary_metrics",
    "summary_metrics",
    "summarize_nanoseconds",
]
