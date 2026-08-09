from __future__ import annotations

from dataclasses import replace

import pytest

from grafix.devtools.benchmarks.report_model import (
    build_report_view_model,
    run_compatibility_issues,
    select_baseline,
)
from grafix.devtools.benchmarks.schema import (
    BenchmarkRun,
    CaseResult,
    CaseSpec,
    ContractResult,
    EnvironmentFingerprint,
    RunMeta,
    Sample,
    SourceIdentity,
    case_compatibility_key,
    evaluate_contract,
    freeze_json_object,
    summarize_samples,
)

_EMPTY = freeze_json_object({})


def _case(
    case_id: str = "case.alpha",
    *,
    sample_values_ns: tuple[int, ...] | None = (1_000_000,),
    status: str = "ok",
    contracts: tuple[ContractResult, ...] = (),
    self_sampling: bool = False,
) -> CaseResult:
    spec = CaseSpec(
        case_id=case_id,
        version=1,
        label=case_id,
        category=case_id.split(".", maxsplit=1)[0],
        suite="pipeline",
        fixture="fixture",
        parameters=_EMPTY,
        seed=0,
        source_sha256="source",
        compatibility_key=case_compatibility_key(
            case_id=case_id,
            version=1,
            fixture="fixture",
            parameters=_EMPTY,
            seed=0,
            source_sha256="source",
        ),
        self_sampling=self_sampling,
    )
    samples = (
        ()
        if sample_values_ns is None
        else tuple(Sample(elapsed_ns=value, iterations=1) for value in sample_values_ns)
    )
    return CaseResult(
        spec=spec,
        status=status,
        samples=samples,
        stats=None if not samples else summarize_samples(samples),
        checksum=None if not samples else "checksum",
        checksum_kind=None if not samples else "exact",
        contracts=contracts,
        error="synthetic failure" if status == "error" else None,
    )


def _run(
    run_id: str,
    *,
    cases: tuple[CaseResult, ...] | None = None,
    index: int = 0,
    environment_key: str = "environment",
    mode: str = "warm",
    samples: int = 3,
    warmup: int = 1,
    target_ns: int = 1_000,
) -> BenchmarkRun:
    return BenchmarkRun(
        meta=RunMeta(
            run_id=run_id,
            created_at=f"2026-08-09T00:{index:02d}:00+00:00",
            suite="pipeline",
            profile="short",
            mode=mode,
            seed=0,
            samples=samples,
            warmup=warmup,
            target_ns=target_ns,
            timeout_seconds=120.0,
        ),
        source=SourceIdentity(commit=run_id, dirty=False, diff_sha256=""),
        environment=EnvironmentFingerprint(
            compatibility_key=environment_key,
            values=_EMPTY,
            unavailable=_EMPTY,
        ),
        cases=(_case(),) if cases is None else cases,
    )


def test_baseline_is_nearest_run_with_full_run_compatibility() -> None:
    oldest = _run("oldest", index=0)
    nearest = _run("nearest-compatible", index=1)
    incompatible = _run("newer-incompatible", index=2, mode="process-cold")
    head = _run("head", index=3)

    assert select_baseline(head, (oldest, nearest, incompatible)) == nearest

    assert run_compatibility_issues(
        _run("environment", environment_key="other"),
        head,
    ) == ("environment differs",)
    assert run_compatibility_issues(
        _run("case-set", cases=(_case(), _case("case.beta"))),
        head,
    ) == ("case set differs",)

    changed_definition = replace(
        _case(),
        spec=replace(_case().spec, compatibility_key="changed-definition"),
    )
    assert run_compatibility_issues(
        _run("definition", cases=(changed_definition,)),
        head,
    ) == ("case.alpha: case definition differs",)
    assert run_compatibility_issues(
        _run("settings", samples=4),
        head,
    ) == ("measurement settings differ: samples",)

    assert run_compatibility_issues(
        _run("empty-cold", cases=(), mode="process-cold"),
        _run("empty-warm", cases=()),
    ) == ("measurement mode differs",)


def test_delta_ignores_zero_base_and_preserves_direction() -> None:
    base = _run(
        "base",
        cases=(
            _case("zero.base", sample_values_ns=(0,)),
            _case("case.regression", sample_values_ns=(1_000_000,)),
            _case("case.improvement", sample_values_ns=(2_000_000,)),
        ),
    )
    head = _run(
        "head",
        index=1,
        cases=(
            _case("zero.base", sample_values_ns=(1_000_000,)),
            _case("case.regression", sample_values_ns=(2_000_000,)),
            _case("case.improvement", sample_values_ns=(1_000_000,)),
        ),
    )

    model = build_report_view_model((base, head), warning_count=0)
    deltas = {row.case_id: row for row in model.deltas}

    assert model.baseline_run_id == "base"
    assert set(deltas) == {"case.regression", "case.improvement"}
    assert deltas["case.regression"].delta_fraction == pytest.approx(1.0)
    assert deltas["case.regression"].direction == "regression"
    assert deltas["case.improvement"].delta_fraction == pytest.approx(-0.5)
    assert deltas["case.improvement"].direction == "improvement"


def test_history_keeps_mad_band_and_splits_line_at_failed_run() -> None:
    first = _run(
        "first",
        cases=(_case(sample_values_ns=(900_000, 1_000_000, 1_100_000)),),
        index=0,
    )
    failed = _run(
        "failed",
        cases=(_case(sample_values_ns=None, status="error"),),
        index=1,
    )
    latest = _run(
        "latest",
        cases=(_case(sample_values_ns=(1_800_000, 2_000_000, 2_200_000)),),
        index=2,
    )

    model = build_report_view_model((first, failed, latest), warning_count=0)
    absolute = [row for row in model.history if row.view_mode == "absolute"]
    relative = [row for row in model.history if row.view_mode == "relative"]

    assert [row.run_id for row in absolute] == ["first", "latest"]
    assert [row.segment_id for row in absolute] == [0, 1]
    assert absolute[0].value == pytest.approx(1.0)
    assert (absolute[0].low, absolute[0].high) == pytest.approx((0.9, 1.1))
    assert [row.value for row in relative] == pytest.approx([0.0, 100.0])
    assert (relative[1].low, relative[1].high) == pytest.approx((80.0, 120.0))


def test_history_relative_view_starts_at_first_positive_median() -> None:
    zero = _run(
        "zero",
        cases=(_case(sample_values_ns=(0,)),),
        index=0,
    )
    positive = _run(
        "positive",
        cases=(_case(sample_values_ns=(1_000_000,)),),
        index=1,
    )

    model = build_report_view_model((zero, positive), warning_count=0)
    relative = [row for row in model.history if row.view_mode == "relative"]

    assert [row.run_id for row in relative] == ["positive"]
    assert relative[0].value == pytest.approx(0.0)


def test_guardrails_normalize_directional_soft_contracts_only() -> None:
    contracts = (
        evaluate_contract(
            contract_id="latency",
            severity="soft",
            actual=25.0,
            comparator="le",
            limit=50.0,
            reason="latency ceiling",
        ),
        evaluate_contract(
            contract_id="freshness",
            severity="soft",
            actual=80.0,
            comparator="ge",
            limit=100.0,
            reason="freshness floor",
        ),
        evaluate_contract(
            contract_id="equality",
            severity="soft",
            actual=1.0,
            comparator="eq",
            limit=1.0,
            reason="not directional",
        ),
        evaluate_contract(
            contract_id="boolean",
            severity="soft",
            actual=True,
            comparator="eq",
            limit=True,
            reason="not numeric",
        ),
        evaluate_contract(
            contract_id="hard-limit",
            severity="hard",
            actual=25.0,
            comparator="le",
            limit=50.0,
            reason="hard contract",
        ),
    )
    model = build_report_view_model(
        (_run("latest", cases=(_case(contracts=contracts),)),),
        warning_count=0,
    )
    guardrails = {row.contract_id: row for row in model.guardrails}

    assert set(guardrails) == {"latency", "freshness"}
    assert guardrails["latency"].load_ratio == pytest.approx(0.5)
    assert guardrails["latency"].direction == "lower is better"
    assert guardrails["freshness"].load_ratio == pytest.approx(1.25)
    assert guardrails["freshness"].direction == "higher is better"
