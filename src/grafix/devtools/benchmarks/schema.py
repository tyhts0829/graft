"""
Purpose:
    benchmark schema v4 の immutable value・compatibility identity・statistics・contract と strict JSON/child protocol codec の正本を定義する。
Use when:
    persisted run/child payload、schema version、case/environment compatibility、sample/metric/contract validation を変更・調査する場合。
Constraints:
    - unknown/missing field・非有限値・不整合な status/statistics/contract を拒否し、legacy payload を推測で v4 に接続しない。
    - source identity と比較互換性を分け、case/environment key は対応する identity field から再計算して照合する。
    - raw sample、metric、contract を source of truth とし、保存済み要約/passed flag の改ざんを検知する。
Side effects:
    run JSON を読み込み、新規 run は atomic no-clobber で書き込む。
"""

from __future__ import annotations

import json
import hashlib
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from statistics import median
from typing import Any, TypeAlias, cast

from grafix.file_io import atomic_write_text_no_clobber
from grafix.devtools.benchmarks import BENCHMARK_SCHEMA_VERSION

_TAIL_MIN_SAMPLES = 20
_CASE_STATUSES = {
    "ok",
    "contract-failure",
    "error",
    "timeout",
    "skipped",
    "resource-limit",
}
_CHECKSUM_POLICIES = {"exact"}
_METRIC_KINDS = {"counter", "gauge", "distribution"}
_METRIC_PHASES = {"setup", "warmup", "measure", "drag", "settle"}
_CONTRACT_SEVERITIES = {"hard", "soft"}
_CONTRACT_COMPARATORS = {"eq", "ne", "lt", "le", "gt", "ge"}
_MEASURED_STATUSES = {"ok", "contract-failure"}


class BenchmarkSchemaError(ValueError):
    """未対応または不正な benchmark JSON を表す。"""


JsonScalar: TypeAlias = str | bool | int | float | None


@dataclass(frozen=True, slots=True, eq=False)
class FrozenJsonObject(Mapping[str, "FrozenJsonValue"]):
    """JSON object の canonical かつ immutable な表現。"""

    _items: tuple[tuple[str, "FrozenJsonValue"], ...] = ()

    def __post_init__(self) -> None:
        keys = tuple(key for key, _value in self._items)
        if any(type(key) is not str for key in keys):
            raise BenchmarkSchemaError("JSON object keys must be exact strings")
        if len(set(keys)) != len(keys):
            raise BenchmarkSchemaError("JSON object keys must be unique")
        object.__setattr__(
            self,
            "_items",
            tuple(
                sorted(
                    ((key, _freeze_json_value(value)) for key, value in self._items),
                    key=lambda item: item[0],
                )
            ),
        )

    def __getitem__(self, key: str) -> FrozenJsonValue:
        for candidate, value in self._items:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _value in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping):
            return False
        return dict(self.items()) == dict(other.items())

    def __hash__(self) -> int:
        return hash(self._items)


FrozenJsonValue: TypeAlias = JsonScalar | tuple["FrozenJsonValue", ...] | FrozenJsonObject


def freeze_json_object(value: Mapping[str, object]) -> FrozenJsonObject:
    """JSON object tree を canonical immutable representation に固定する。"""

    if isinstance(value, FrozenJsonObject):
        return value
    return FrozenJsonObject(tuple((key, _freeze_json_value(item)) for key, item in value.items()))


def materialize_json_object(value: FrozenJsonObject) -> dict[str, Any]:
    """immutable JSON object を独立した plain ``dict`` / ``list`` tree に戻す。"""

    return cast(dict[str, Any], _json_data(value))


def _freeze_json_value(value: object) -> FrozenJsonValue:
    if value is None or type(value) in {str, bool, int}:
        return cast(JsonScalar, value)
    if type(value) is float:
        if not math.isfinite(value):
            raise BenchmarkSchemaError("non-finite JSON number is not allowed")
        return value
    if isinstance(value, Mapping):
        return freeze_json_object(value)
    if type(value) in {list, tuple}:
        sequence = cast(list[object] | tuple[object, ...], value)
        return tuple(_freeze_json_value(item) for item in sequence)
    raise BenchmarkSchemaError(f"unsupported JSON value {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    """計測対象 source の識別情報。比較互換性とは分離する。"""

    commit: str | None
    dirty: bool | None
    diff_sha256: str | None
    unavailable_reason: str | None = None


@dataclass(frozen=True, slots=True)
class EnvironmentFingerprint:
    """比較に必要な実行環境と、その canonical key。"""

    compatibility_key: str
    values: FrozenJsonObject
    unavailable: FrozenJsonObject = field(default_factory=FrozenJsonObject)

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", freeze_json_object(self.values))
        object.__setattr__(
            self,
            "unavailable",
            freeze_json_object(self.unavailable),
        )


@dataclass(frozen=True, slots=True)
class RunMeta:
    """1 benchmark run に共通する設定。"""

    run_id: str
    created_at: str
    suite: str
    profile: str
    mode: str
    seed: int
    samples: int = 0
    warmup: int = 0
    target_ns: int = 0
    disable_gc: bool = False
    timeout_seconds: float = 0.0
    argv: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CaseSpec:
    """比較可能性を決める benchmark case 定義。"""

    case_id: str
    version: int
    label: str
    category: str
    suite: str
    fixture: str
    parameters: FrozenJsonObject
    seed: int
    source_sha256: str
    compatibility_key: str
    checksum_policy: str = "exact"
    tags: tuple[str, ...] = ()
    self_sampling: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parameters",
            freeze_json_object(self.parameters),
        )


@dataclass(frozen=True, slots=True)
class Sample:
    """反復回数を含む未丸めの wall-clock sample。"""

    elapsed_ns: int
    iterations: int

    @property
    def ns_per_iteration(self) -> float:
        return float(self.elapsed_ns) / float(self.iterations)


@dataclass(frozen=True, slots=True)
class SampleStats:
    """raw sample から得た要約。tail は十分な sample 数でのみ持つ。"""

    n: int
    median_ns: float
    mad_ns: float
    min_ns: float
    max_ns: float
    p95_ns: float | None
    p99_ns: float | None


@dataclass(frozen=True, slots=True)
class Distribution:
    """distribution metric の統計と、取得できる場合の raw sample。"""

    count: int
    min: float | None
    max: float | None
    median: float | None
    mad: float | None
    p95: float | None
    p99: float | None
    mean: float | None = None
    samples: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class Metric:
    """名前、単位、計測区間を持つ typed metric。"""

    name: str
    kind: str
    unit: str
    phase: str
    scope: str
    value: Any = None
    distribution: Distribution | None = None


@dataclass(frozen=True, slots=True)
class ContractResult:
    """benchmark contract の評価結果。"""

    contract_id: str
    severity: str
    passed: bool
    actual: Any
    comparator: str
    limit: Any
    reason: str


@dataclass(frozen=True, slots=True)
class BenchmarkOutput:
    """benchmark workload が runner へ返す共通出力。"""

    value: object
    metrics: tuple[Metric, ...] = ()
    contracts: tuple[ContractResult, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.metrics, tuple) or not all(
            isinstance(metric, Metric) for metric in self.metrics
        ):
            raise TypeError("metrics は Metric の tuple である必要があります")
        if not isinstance(self.contracts, tuple) or not all(
            isinstance(contract, ContractResult) for contract in self.contracts
        ):
            raise TypeError("contracts は ContractResult の tuple である必要があります")
        names = tuple(metric.name for metric in self.metrics)
        if len(set(names)) != len(names):
            raise ValueError("benchmark metric name は一意である必要があります")


@dataclass(frozen=True, slots=True)
class CaseResult:
    """隔離 process で得た 1 case の結果。"""

    spec: CaseSpec
    status: str
    samples: tuple[Sample, ...] = ()
    stats: SampleStats | None = None
    checksum: str | None = None
    checksum_kind: str | None = None
    setup_rss_bytes: int | None = None
    baseline_rss_bytes: int | None = None
    peak_rss_bytes: int | None = None
    peak_rss_delta_bytes: int | None = None
    metrics: tuple[Metric, ...] = ()
    contracts: tuple[ContractResult, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkRun:
    """schema v4 の top-level benchmark run。"""

    meta: RunMeta
    source: SourceIdentity
    environment: EnvironmentFingerprint
    cases: tuple[CaseResult, ...]
    warnings: tuple[str, ...] = ()
    schema_version: int = BENCHMARK_SCHEMA_VERSION


def summarize_samples(samples: list[Sample] | tuple[Sample, ...]) -> SampleStats:
    """sample を 1 iteration 当たりの ns へ正規化して要約する。"""

    if not samples:
        raise ValueError("samples は 1 件以上必要です")
    values = [sample.ns_per_iteration for sample in samples]
    if any(not math.isfinite(value) or value < 0.0 for value in values):
        raise ValueError("sample は有限な非負値である必要があります")
    center = float(median(values))
    deviations = [abs(value - center) for value in values]
    ordered = sorted(values)
    with_tail = len(ordered) >= _TAIL_MIN_SAMPLES
    return SampleStats(
        n=len(ordered),
        median_ns=center,
        mad_ns=float(median(deviations)),
        min_ns=float(ordered[0]),
        max_ns=float(ordered[-1]),
        p95_ns=_percentile(ordered, 0.95) if with_tail else None,
        p99_ns=_percentile(ordered, 0.99) if with_tail else None,
    )


def summarize_distribution(values: list[float] | tuple[float, ...]) -> Distribution:
    """有限な raw value を distribution metric として要約する。"""

    samples = tuple(float(value) for value in values)
    if not samples:
        return Distribution(
            count=0,
            min=None,
            max=None,
            median=None,
            mad=None,
            p95=None,
            p99=None,
            mean=None,
        )
    if any(not math.isfinite(value) for value in samples):
        raise ValueError("distribution sample は有限値である必要があります")
    ordered = sorted(samples)
    center = float(median(ordered))
    return Distribution(
        count=len(ordered),
        min=float(ordered[0]),
        max=float(ordered[-1]),
        median=center,
        mad=float(median(abs(value - center) for value in ordered)),
        p95=_percentile(ordered, 0.95),
        p99=_percentile(ordered, 0.99),
        mean=float(sum(ordered) / len(ordered)),
        samples=samples,
    )


def evaluate_contract(
    *,
    contract_id: str,
    severity: str,
    actual: object,
    comparator: str,
    limit: object,
    reason: str,
) -> ContractResult:
    """比較式を評価し、改ざん検出可能な ContractResult を作る。"""

    passed = _evaluate_comparison(actual, comparator, limit)
    return ContractResult(
        contract_id=str(contract_id),
        severity=str(severity),
        passed=passed,
        actual=actual,
        comparator=str(comparator),
        limit=limit,
        reason=str(reason),
    )


def benchmark_run_to_dict(run: BenchmarkRun) -> dict[str, Any]:
    """BenchmarkRun を JSON 化可能な mapping に変換する。"""

    return cast(dict[str, Any], _json_data(run))


def case_result_to_dict(result: CaseResult) -> dict[str, Any]:
    """CaseResult を child protocol 用 mapping に変換する。"""

    return cast(dict[str, Any], _json_data(result))


def case_result_from_dict(payload: object) -> CaseResult:
    """child protocol の CaseResult を厳格に復元する。"""

    result = _decode_case(payload, index=0)
    _validate_case_result(result, where="cases[0]")
    return result


def benchmark_run_from_dict(payload: object) -> BenchmarkRun:
    """未知 field と欠落 field を拒否して BenchmarkRun を復元する。"""

    run = _decode_benchmark_run(payload)
    _validate_run(run)
    return run


def _decode_benchmark_run(payload: object) -> BenchmarkRun:
    """JSON-compatible object を semantic validation 前まで復元する。"""

    root = _mapping(payload, "root")
    if "schema_version" not in root:
        raise BenchmarkSchemaError("root: missing=['schema_version']")
    version = _integer(root["schema_version"], "schema_version")
    if version != BENCHMARK_SCHEMA_VERSION:
        raise BenchmarkSchemaError(
            f"unsupported schema_version: {version} (expected {BENCHMARK_SCHEMA_VERSION})"
        )
    _keys(
        root,
        required={"schema_version", "meta", "source", "environment", "cases", "warnings"},
        where="root",
    )
    meta = _decode_meta(root["meta"])
    source = _decode_source(root["source"])
    environment = _decode_environment(root["environment"])
    cases_raw = _sequence(root["cases"], "cases")
    warnings_raw = _sequence(root["warnings"], "warnings")
    warnings = tuple(
        _string(value, f"warnings[{index}]") for index, value in enumerate(warnings_raw)
    )
    cases = tuple(_decode_case(value, index=index) for index, value in enumerate(cases_raw))
    return BenchmarkRun(
        meta=meta,
        source=source,
        environment=environment,
        cases=cases,
        warnings=warnings,
        schema_version=version,
    )


def environment_compatibility_key(
    values: FrozenJsonObject,
    unavailable: FrozenJsonObject,
) -> str:
    """environment identity の canonical SHA-256 を返す。"""

    return _sha256_json(freeze_json_object({"values": values, "unavailable": unavailable}))


def case_compatibility_key(
    *,
    case_id: str,
    version: int,
    fixture: str,
    parameters: FrozenJsonObject,
    seed: int,
    source_sha256: str,
    checksum_policy: str = "exact",
    self_sampling: bool = False,
) -> str:
    """case identity の canonical SHA-256 を返す。"""

    return _sha256_json(
        freeze_json_object(
            {
                "case_id": case_id,
                "version": version,
                "fixture": fixture,
                "parameters": parameters,
                "seed": seed,
                "source_sha256": source_sha256,
                "checksum_policy": checksum_policy,
                "self_sampling": self_sampling,
            }
        )
    )


def write_benchmark_run(path: str | Path, run: BenchmarkRun) -> None:
    """既存 run を上書きせず、JSON を atomic に保存する。"""

    destination = Path(path)
    _validate_run(run)
    try:
        text = (
            json.dumps(
                benchmark_run_to_dict(run),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        )
    except (TypeError, ValueError) as exc:
        raise BenchmarkSchemaError(f"run contains a non-JSON value: {exc}") from exc
    try:
        atomic_write_text_no_clobber(destination, text)
    except FileExistsError as exc:
        raise FileExistsError(f"benchmark run already exists: {destination}") from exc


def read_benchmark_run(path: str | Path) -> BenchmarkRun:
    """JSON file から schema v4 run を厳格に読む。"""

    source = Path(path)
    try:
        payload = json.loads(
            source.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise BenchmarkSchemaError(f"{source}: {type(exc).__name__}: {exc}") from exc
    try:
        return benchmark_run_from_dict(payload)
    except BenchmarkSchemaError as exc:
        raise BenchmarkSchemaError(f"{source}: {exc}") from exc


def _decode_meta(value: object) -> RunMeta:
    obj = _mapping(value, "meta")
    _keys(
        obj,
        required={
            "run_id",
            "created_at",
            "suite",
            "profile",
            "mode",
            "seed",
            "samples",
            "warmup",
            "target_ns",
            "disable_gc",
            "timeout_seconds",
            "argv",
        },
        where="meta",
    )
    argv = _sequence(obj["argv"], "meta.argv")
    return RunMeta(
        run_id=_string(obj["run_id"], "meta.run_id"),
        created_at=_string(obj["created_at"], "meta.created_at"),
        suite=_string(obj["suite"], "meta.suite"),
        profile=_string(obj["profile"], "meta.profile"),
        mode=_string(obj["mode"], "meta.mode"),
        seed=_integer(obj["seed"], "meta.seed"),
        samples=_integer(obj["samples"], "meta.samples"),
        warmup=_integer(obj["warmup"], "meta.warmup"),
        target_ns=_integer(obj["target_ns"], "meta.target_ns"),
        disable_gc=_boolean(obj["disable_gc"], "meta.disable_gc"),
        timeout_seconds=_number(obj["timeout_seconds"], "meta.timeout_seconds"),
        argv=tuple(_string(item, f"meta.argv[{index}]") for index, item in enumerate(argv)),
    )


def _decode_source(value: object) -> SourceIdentity:
    obj = _mapping(value, "source")
    _keys(
        obj,
        required={"commit", "dirty", "diff_sha256", "unavailable_reason"},
        where="source",
    )
    return SourceIdentity(
        commit=_optional_string(obj["commit"], "source.commit"),
        dirty=_optional_bool(obj["dirty"], "source.dirty"),
        diff_sha256=_optional_string(obj["diff_sha256"], "source.diff_sha256"),
        unavailable_reason=_optional_string(obj["unavailable_reason"], "source.unavailable_reason"),
    )


def _decode_environment(value: object) -> EnvironmentFingerprint:
    obj = _mapping(value, "environment")
    _keys(
        obj,
        required={"compatibility_key", "values", "unavailable"},
        where="environment",
    )
    values = freeze_json_object(_mapping(obj["values"], "environment.values"))
    unavailable = _mapping(obj["unavailable"], "environment.unavailable")
    return EnvironmentFingerprint(
        compatibility_key=_string(obj["compatibility_key"], "environment.compatibility_key"),
        values=values,
        unavailable=freeze_json_object(
            {
                _string(key, "environment.unavailable key"): _string(
                    item, f"environment.unavailable.{key}"
                )
                for key, item in unavailable.items()
            }
        ),
    )


def _decode_case(value: object, *, index: int) -> CaseResult:
    where = f"cases[{index}]"
    obj = _mapping(value, where)
    _keys(
        obj,
        required={
            "spec",
            "status",
            "samples",
            "stats",
            "checksum",
            "checksum_kind",
            "setup_rss_bytes",
            "baseline_rss_bytes",
            "peak_rss_bytes",
            "peak_rss_delta_bytes",
            "metrics",
            "contracts",
            "error",
        },
        where=where,
    )
    samples_raw = _sequence(obj["samples"], f"{where}.samples")
    samples = tuple(
        _decode_sample(item, where=f"{where}.samples[{sample_index}]")
        for sample_index, item in enumerate(samples_raw)
    )
    stats = None if obj["stats"] is None else _decode_stats(obj["stats"], where=f"{where}.stats")
    metrics_raw = _sequence(obj["metrics"], f"{where}.metrics")
    contracts_raw = _sequence(obj["contracts"], f"{where}.contracts")
    return CaseResult(
        spec=_decode_spec(obj["spec"], where=f"{where}.spec"),
        status=_string(obj["status"], f"{where}.status"),
        samples=samples,
        stats=stats,
        checksum=_optional_string(obj["checksum"], f"{where}.checksum"),
        checksum_kind=_optional_string(obj["checksum_kind"], f"{where}.checksum_kind"),
        setup_rss_bytes=_optional_integer(obj["setup_rss_bytes"], f"{where}.setup_rss_bytes"),
        baseline_rss_bytes=_optional_integer(
            obj["baseline_rss_bytes"], f"{where}.baseline_rss_bytes"
        ),
        peak_rss_bytes=_optional_integer(obj["peak_rss_bytes"], f"{where}.peak_rss_bytes"),
        peak_rss_delta_bytes=_optional_integer(
            obj["peak_rss_delta_bytes"], f"{where}.peak_rss_delta_bytes"
        ),
        metrics=tuple(
            _decode_metric(item, where=f"{where}.metrics[{metric_index}]")
            for metric_index, item in enumerate(metrics_raw)
        ),
        contracts=tuple(
            _decode_contract(
                item,
                where=f"{where}.contracts[{contract_index}]",
            )
            for contract_index, item in enumerate(contracts_raw)
        ),
        error=_optional_string(obj["error"], f"{where}.error"),
    )


def _decode_spec(value: object, *, where: str) -> CaseSpec:
    obj = _mapping(value, where)
    _keys(
        obj,
        required={
            "case_id",
            "version",
            "label",
            "category",
            "suite",
            "fixture",
            "parameters",
            "seed",
            "source_sha256",
            "compatibility_key",
            "checksum_policy",
            "tags",
            "self_sampling",
        },
        where=where,
    )
    tags = _sequence(obj["tags"], f"{where}.tags")
    return CaseSpec(
        case_id=_string(obj["case_id"], f"{where}.case_id"),
        version=_integer(obj["version"], f"{where}.version"),
        label=_string(obj["label"], f"{where}.label"),
        category=_string(obj["category"], f"{where}.category"),
        suite=_string(obj["suite"], f"{where}.suite"),
        fixture=_string(obj["fixture"], f"{where}.fixture"),
        parameters=freeze_json_object(_mapping(obj["parameters"], f"{where}.parameters")),
        seed=_integer(obj["seed"], f"{where}.seed"),
        source_sha256=_string(obj["source_sha256"], f"{where}.source_sha256"),
        compatibility_key=_string(obj["compatibility_key"], f"{where}.compatibility_key"),
        checksum_policy=_string(obj["checksum_policy"], f"{where}.checksum_policy"),
        tags=tuple(
            _string(item, f"{where}.tags[{tag_index}]") for tag_index, item in enumerate(tags)
        ),
        self_sampling=_boolean(
            obj["self_sampling"],
            f"{where}.self_sampling",
        ),
    )


def _decode_sample(value: object, *, where: str) -> Sample:
    obj = _mapping(value, where)
    _keys(obj, required={"elapsed_ns", "iterations"}, where=where)
    elapsed_ns = _integer(obj["elapsed_ns"], f"{where}.elapsed_ns")
    iterations = _integer(obj["iterations"], f"{where}.iterations")
    if elapsed_ns < 0 or iterations < 1:
        raise BenchmarkSchemaError(f"{where}: invalid elapsed_ns/iterations")
    return Sample(elapsed_ns=elapsed_ns, iterations=iterations)


def _decode_stats(value: object, *, where: str) -> SampleStats:
    obj = _mapping(value, where)
    _keys(
        obj,
        required={"n", "median_ns", "mad_ns", "min_ns", "max_ns", "p95_ns", "p99_ns"},
        where=where,
    )
    return SampleStats(
        n=_integer(obj["n"], f"{where}.n"),
        median_ns=_number(obj["median_ns"], f"{where}.median_ns"),
        mad_ns=_number(obj["mad_ns"], f"{where}.mad_ns"),
        min_ns=_number(obj["min_ns"], f"{where}.min_ns"),
        max_ns=_number(obj["max_ns"], f"{where}.max_ns"),
        p95_ns=_optional_number(obj["p95_ns"], f"{where}.p95_ns"),
        p99_ns=_optional_number(obj["p99_ns"], f"{where}.p99_ns"),
    )


def _decode_metric(value: object, *, where: str) -> Metric:
    obj = _mapping(value, where)
    _keys(
        obj,
        required={
            "name",
            "kind",
            "unit",
            "phase",
            "scope",
            "value",
            "distribution",
        },
        where=where,
    )
    distribution = (
        None
        if obj["distribution"] is None
        else _decode_distribution(
            obj["distribution"],
            where=f"{where}.distribution",
        )
    )
    return Metric(
        name=_string(obj["name"], f"{where}.name"),
        kind=_string(obj["kind"], f"{where}.kind"),
        unit=_string(obj["unit"], f"{where}.unit"),
        phase=_string(obj["phase"], f"{where}.phase"),
        scope=_string(obj["scope"], f"{where}.scope"),
        value=obj["value"],
        distribution=distribution,
    )


def _decode_distribution(value: object, *, where: str) -> Distribution:
    obj = _mapping(value, where)
    _keys(
        obj,
        required={
            "count",
            "min",
            "max",
            "median",
            "mad",
            "p95",
            "p99",
            "mean",
            "samples",
        },
        where=where,
    )
    samples_raw = _sequence(obj["samples"], f"{where}.samples")
    return Distribution(
        count=_integer(obj["count"], f"{where}.count"),
        min=_optional_number(obj["min"], f"{where}.min"),
        max=_optional_number(obj["max"], f"{where}.max"),
        median=_optional_number(obj["median"], f"{where}.median"),
        mad=_optional_number(obj["mad"], f"{where}.mad"),
        p95=_optional_number(obj["p95"], f"{where}.p95"),
        p99=_optional_number(obj["p99"], f"{where}.p99"),
        mean=_optional_number(obj["mean"], f"{where}.mean"),
        samples=tuple(
            _number(item, f"{where}.samples[{index}]") for index, item in enumerate(samples_raw)
        ),
    )


def _decode_contract(value: object, *, where: str) -> ContractResult:
    obj = _mapping(value, where)
    _keys(
        obj,
        required={
            "contract_id",
            "severity",
            "passed",
            "actual",
            "comparator",
            "limit",
            "reason",
        },
        where=where,
    )
    return ContractResult(
        contract_id=_string(obj["contract_id"], f"{where}.contract_id"),
        severity=_string(obj["severity"], f"{where}.severity"),
        passed=_boolean(obj["passed"], f"{where}.passed"),
        actual=_contract_operand(obj["actual"], f"{where}.actual"),
        comparator=_string(obj["comparator"], f"{where}.comparator"),
        limit=_contract_operand(obj["limit"], f"{where}.limit"),
        reason=_string(obj["reason"], f"{where}.reason"),
    )


def _percentile(ordered: list[float], fraction: float) -> float:
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * float(fraction)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower]) * (1.0 - weight) + float(ordered[upper]) * weight


def _validate_run(run: BenchmarkRun) -> None:
    payload = benchmark_run_to_dict(run)
    _validate_json_value(payload, where="root")
    # writer 側も decoder と同じ型規則を通し、「書けたが読めない」JSON を防ぐ。
    _decode_benchmark_run(json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False)))
    if run.schema_version != BENCHMARK_SCHEMA_VERSION:
        raise BenchmarkSchemaError(
            f"unsupported schema_version: {run.schema_version} "
            f"(expected {BENCHMARK_SCHEMA_VERSION})"
        )
    meta = run.meta
    if not all(
        (
            meta.run_id,
            meta.created_at,
            meta.suite,
            meta.profile,
            meta.mode,
        )
    ):
        raise BenchmarkSchemaError("meta: identity fields must not be empty")
    if meta.mode not in {"warm", "process-cold", "compile-cold"}:
        raise BenchmarkSchemaError(f"meta.mode: unsupported value {meta.mode!r}")
    if (
        meta.samples < 1
        or meta.warmup < 0
        or meta.target_ns < 0
        or not math.isfinite(meta.timeout_seconds)
        or meta.timeout_seconds <= 0.0
    ):
        raise BenchmarkSchemaError("meta: invalid measurement settings")
    if meta.mode != "warm" and (meta.warmup != 0 or meta.target_ns != 0):
        raise BenchmarkSchemaError("meta: cold mode requires warmup=0 and target_ns=0")

    expected_environment_key = environment_compatibility_key(
        run.environment.values,
        run.environment.unavailable,
    )
    if run.environment.compatibility_key != expected_environment_key:
        raise BenchmarkSchemaError(
            "environment.compatibility_key does not match its identity fields"
        )

    seen_case_ids: set[str] = set()
    for index, result in enumerate(run.cases):
        where = f"cases[{index}]"
        if result.spec.case_id in seen_case_ids:
            raise BenchmarkSchemaError(f"{where}.spec.case_id: duplicate {result.spec.case_id!r}")
        seen_case_ids.add(result.spec.case_id)
        if result.spec.seed != meta.seed:
            raise BenchmarkSchemaError(f"{where}.spec.seed differs from meta.seed")
        _validate_case_result(result, where=where)
        expected_samples = 1 if result.spec.self_sampling else meta.samples
        if result.status in _MEASURED_STATUSES and len(result.samples) != expected_samples:
            raise BenchmarkSchemaError(
                f"{where}.samples: count differs from effective sample policy"
            )


def _validate_case_result(result: CaseResult, *, where: str) -> None:
    _validate_json_value(case_result_to_dict(result), where=where)
    spec = result.spec
    if (
        not spec.case_id
        or spec.version < 1
        or not spec.label
        or not spec.category
        or not spec.suite
        or not spec.fixture
        or not spec.source_sha256
    ):
        raise BenchmarkSchemaError(f"{where}.spec: invalid identity fields")
    expected_case_key = case_compatibility_key(
        case_id=spec.case_id,
        version=spec.version,
        fixture=spec.fixture,
        parameters=spec.parameters,
        seed=spec.seed,
        source_sha256=spec.source_sha256,
        checksum_policy=spec.checksum_policy,
        self_sampling=spec.self_sampling,
    )
    if spec.compatibility_key != expected_case_key:
        raise BenchmarkSchemaError(
            f"{where}.spec.compatibility_key does not match its identity fields"
        )
    if spec.checksum_policy not in _CHECKSUM_POLICIES:
        raise BenchmarkSchemaError(
            f"{where}.spec.checksum_policy: unsupported value {spec.checksum_policy!r}"
        )
    if result.status not in _CASE_STATUSES:
        raise BenchmarkSchemaError(f"{where}.status: unsupported value {result.status!r}")
    seen_metrics: set[str] = set()
    for metric_index, metric in enumerate(result.metrics):
        metric_where = f"{where}.metrics[{metric_index}]"
        _validate_metric(metric, where=metric_where)
        if metric.name in seen_metrics:
            raise BenchmarkSchemaError(f"{metric_where}: duplicate metric name {metric.name!r}")
        seen_metrics.add(metric.name)
    seen_contracts: set[str] = set()
    for contract_index, contract in enumerate(result.contracts):
        contract_where = f"{where}.contracts[{contract_index}]"
        _validate_contract(contract, where=contract_where)
        if contract.contract_id in seen_contracts:
            raise BenchmarkSchemaError(
                f"{contract_where}: duplicate contract ID {contract.contract_id!r}"
            )
        seen_contracts.add(contract.contract_id)

    rss_values = (
        result.setup_rss_bytes,
        result.baseline_rss_bytes,
        result.peak_rss_bytes,
        result.peak_rss_delta_bytes,
    )
    if any(value is not None and value < 0 for value in rss_values):
        raise BenchmarkSchemaError(f"{where}: RSS fields must be non-negative")

    if result.status not in _MEASURED_STATUSES:
        if not result.error:
            raise BenchmarkSchemaError(f"{where}.error: non-ok result requires an error")
        if (
            result.samples
            or result.stats is not None
            or result.checksum is not None
            or result.checksum_kind is not None
            or result.metrics
            or result.contracts
        ):
            raise BenchmarkSchemaError(f"{where}: unmeasured result must not contain result data")
        return

    failed_hard = tuple(
        contract
        for contract in result.contracts
        if contract.severity == "hard" and not contract.passed
    )
    if result.status == "ok":
        if result.error is not None:
            raise BenchmarkSchemaError(f"{where}.error: ok result must not have an error")
        if failed_hard:
            raise BenchmarkSchemaError(
                f"{where}: failed hard contract requires contract-failure status"
            )
    else:
        if not failed_hard:
            raise BenchmarkSchemaError(f"{where}: contract-failure requires a failed hard contract")
        if not result.error:
            raise BenchmarkSchemaError(f"{where}.error: contract-failure requires an error")
    if not result.samples or result.stats is None:
        raise BenchmarkSchemaError(f"{where}: measured result requires samples and stats")
    if not result.checksum or not result.checksum_kind:
        raise BenchmarkSchemaError(f"{where}: measured result requires a checksum")
    expected_stats = summarize_samples(result.samples)
    if result.stats != expected_stats:
        raise BenchmarkSchemaError(f"{where}.stats does not match raw samples")
    if any(value is None for value in rss_values):
        raise BenchmarkSchemaError(f"{where}: measured result requires all RSS fields")
    baseline = result.baseline_rss_bytes
    peak = result.peak_rss_bytes
    delta = result.peak_rss_delta_bytes
    setup = result.setup_rss_bytes
    assert setup is not None and baseline is not None and peak is not None and delta is not None
    if setup > baseline or peak < baseline or delta != peak - baseline:
        raise BenchmarkSchemaError(f"{where}: setup/baseline/peak RSS fields are inconsistent")


def _validate_metric(metric: Metric, *, where: str) -> None:
    if not metric.name or not metric.unit or not metric.phase or not metric.scope:
        raise BenchmarkSchemaError(f"{where}: metric identity fields must not be empty")
    if metric.kind not in _METRIC_KINDS:
        raise BenchmarkSchemaError(f"{where}.kind: unsupported value {metric.kind!r}")
    if metric.phase not in _METRIC_PHASES:
        raise BenchmarkSchemaError(f"{where}.phase: unsupported value {metric.phase!r}")
    _validate_json_value(metric.value, where=f"{where}.value")
    if metric.kind == "distribution":
        if metric.value is not None or metric.distribution is None:
            raise BenchmarkSchemaError(
                f"{where}: distribution requires distribution data and value=None"
            )
        _validate_distribution(metric.distribution, where=f"{where}.distribution")
        return
    if metric.distribution is not None:
        raise BenchmarkSchemaError(f"{where}: {metric.kind} must not contain distribution data")
    if metric.kind == "counter":
        if (
            not isinstance(metric.value, (int, float))
            or isinstance(metric.value, bool)
            or not math.isfinite(float(metric.value))
            or float(metric.value) < 0.0
        ):
            raise BenchmarkSchemaError(
                f"{where}.value: counter requires a finite non-negative number"
            )
    elif not isinstance(metric.value, (str, bool, int, float)) or (
        isinstance(metric.value, float) and not math.isfinite(metric.value)
    ):
        raise BenchmarkSchemaError(f"{where}.value: gauge requires a finite scalar")


def _validate_distribution(distribution: Distribution, *, where: str) -> None:
    if distribution.count < 0:
        raise BenchmarkSchemaError(f"{where}.count must be non-negative")
    values = (
        distribution.min,
        distribution.max,
        distribution.median,
        distribution.mad,
        distribution.p95,
        distribution.p99,
        distribution.mean,
    )
    if any(value is not None and not math.isfinite(value) for value in values):
        raise BenchmarkSchemaError(f"{where}: statistics must be finite")
    if distribution.mad is not None and distribution.mad < 0.0:
        raise BenchmarkSchemaError(f"{where}.mad must be non-negative")
    if distribution.count == 0:
        if distribution.samples or any(value is not None for value in values):
            raise BenchmarkSchemaError(
                f"{where}: empty distribution must not contain samples or statistics"
            )
        return
    if any(value is None for value in values):
        raise BenchmarkSchemaError(f"{where}: non-empty distribution requires all statistics")
    minimum = distribution.min
    maximum = distribution.max
    median_value = distribution.median
    p95 = distribution.p95
    p99 = distribution.p99
    mean = distribution.mean
    assert (
        minimum is not None
        and maximum is not None
        and median_value is not None
        and p95 is not None
        and p99 is not None
        and mean is not None
    )
    if distribution.samples:
        expected = summarize_distribution(distribution.samples)
        if distribution != expected:
            raise BenchmarkSchemaError(f"{where}: statistics do not match raw samples")
    if not minimum <= median_value <= p95 <= p99 <= maximum:
        raise BenchmarkSchemaError(
            f"{where}: statistics must satisfy min <= median <= p95 <= p99 <= max"
        )
    if not minimum <= mean <= maximum:
        raise BenchmarkSchemaError(f"{where}: mean must be between min and max")


def _validate_contract(contract: ContractResult, *, where: str) -> None:
    if not contract.contract_id or not contract.reason:
        raise BenchmarkSchemaError(f"{where}: contract_id and reason must not be empty")
    if contract.severity not in _CONTRACT_SEVERITIES:
        raise BenchmarkSchemaError(f"{where}.severity: unsupported value {contract.severity!r}")
    if contract.comparator not in _CONTRACT_COMPARATORS:
        raise BenchmarkSchemaError(f"{where}.comparator: unsupported value {contract.comparator!r}")
    _contract_operand(contract.actual, f"{where}.actual")
    _contract_operand(contract.limit, f"{where}.limit")
    try:
        expected = _evaluate_comparison(
            contract.actual,
            contract.comparator,
            contract.limit,
        )
    except (TypeError, ValueError) as exc:
        raise BenchmarkSchemaError(f"{where}: invalid comparison: {exc}") from exc
    if contract.passed != expected:
        raise BenchmarkSchemaError(f"{where}.passed does not match actual/comparator/limit")


def _sha256_json(value: FrozenJsonValue) -> str:
    encoded = json.dumps(
        _json_data(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_data(value: object) -> object:
    """dataclass と frozen JSON tree を plain JSON data に変換する。"""

    if isinstance(value, FrozenJsonObject):
        return {key: _json_data(item) for key, item in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _json_data(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {key: _json_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_data(item) for item in value]
    return value


def _validate_json_value(value: object, *, where: str) -> None:
    """strict JSON で表現できる有限値だけを再帰的に許可する。"""

    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BenchmarkSchemaError(f"{where}: non-finite number is not allowed")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_json_value(item, where=f"{where}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise BenchmarkSchemaError(f"{where}: object keys must be strings")
            _validate_json_value(item, where=f"{where}.{key}")
        return
    raise BenchmarkSchemaError(f"{where}: unsupported JSON value {type(value).__name__}")


def _keys(obj: dict[str, Any], *, required: set[str], where: str) -> None:
    actual = set(obj)
    missing = sorted(required - actual)
    unknown = sorted(actual - required)
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing={missing}")
        if unknown:
            details.append(f"unknown={unknown}")
        raise BenchmarkSchemaError(f"{where}: {', '.join(details)}")


def _mapping(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise BenchmarkSchemaError(f"{where}: object is required")
    return value


def _sequence(value: object, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise BenchmarkSchemaError(f"{where}: array is required")
    return value


def _string(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise BenchmarkSchemaError(f"{where}: string is required")
    return value


def _optional_string(value: object, where: str) -> str | None:
    return None if value is None else _string(value, where)


def _integer(value: object, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise BenchmarkSchemaError(f"{where}: integer is required")
    return value


def _optional_integer(value: object, where: str) -> int | None:
    return None if value is None else _integer(value, where)


def _number(value: object, where: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise BenchmarkSchemaError(f"{where}: number is required")
    result = float(value)
    if not math.isfinite(result):
        raise BenchmarkSchemaError(f"{where}: finite number is required")
    return result


def _optional_number(value: object, where: str) -> float | None:
    return None if value is None else _number(value, where)


def _contract_operand(value: object, where: str) -> str | bool | int | float:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise BenchmarkSchemaError(f"{where}: contract operand must be a finite scalar")


def _evaluate_comparison(
    actual: object,
    comparator: str,
    limit: object,
) -> bool:
    if comparator not in _CONTRACT_COMPARATORS:
        raise ValueError(f"unknown comparator {comparator!r}")
    if comparator == "eq":
        return bool(actual == limit)
    if comparator == "ne":
        return bool(actual != limit)
    if (
        not isinstance(actual, (int, float))
        or isinstance(actual, bool)
        or not isinstance(limit, (int, float))
        or isinstance(limit, bool)
    ):
        raise TypeError(f"{comparator} requires numeric operands")
    left = float(actual)
    right = float(limit)
    if not math.isfinite(left) or not math.isfinite(right):
        raise ValueError("ordered comparison requires finite operands")
    if comparator == "lt":
        return left < right
    if comparator == "le":
        return left <= right
    if comparator == "gt":
        return left > right
    return left >= right


def _optional_bool(value: object, where: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise BenchmarkSchemaError(f"{where}: boolean is required")
    return value


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _boolean(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise BenchmarkSchemaError(f"{where}: boolean is required")
    return value


__all__ = [
    "BenchmarkOutput",
    "BenchmarkRun",
    "BenchmarkSchemaError",
    "CaseResult",
    "CaseSpec",
    "ContractResult",
    "Distribution",
    "EnvironmentFingerprint",
    "FrozenJsonObject",
    "Metric",
    "RunMeta",
    "Sample",
    "SampleStats",
    "SourceIdentity",
    "benchmark_run_from_dict",
    "benchmark_run_to_dict",
    "case_result_from_dict",
    "case_result_to_dict",
    "case_compatibility_key",
    "environment_compatibility_key",
    "evaluate_contract",
    "freeze_json_object",
    "materialize_json_object",
    "read_benchmark_run",
    "summarize_distribution",
    "summarize_samples",
    "write_benchmark_run",
]
