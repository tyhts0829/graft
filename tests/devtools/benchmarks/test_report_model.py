from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from grafix.devtools.benchmarks.report_model import (
    build_report_view_model,
    chronological_runs,
    latest_valid_run,
    measurement_signature,
    parse_created_at_utc,
    trend_cohort_key,
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
    label: str | None = None,
    sample_values_ns: tuple[int, ...] | None = (1_000_000,),
    status: str = "ok",
    checksum: str = "checksum",
    source_sha256: str = "source",
    contracts: tuple[ContractResult, ...] = (),
    self_sampling: bool = False,
) -> CaseResult:
    spec = CaseSpec(
        case_id=case_id,
        version=1,
        label=case_id if label is None else label,
        category=case_id.split(".", maxsplit=1)[0],
        suite="pipeline",
        fixture="fixture",
        parameters=_EMPTY,
        seed=0,
        source_sha256=source_sha256,
        compatibility_key=case_compatibility_key(
            case_id=case_id,
            version=1,
            fixture="fixture",
            parameters=_EMPTY,
            seed=0,
            source_sha256=source_sha256,
            self_sampling=self_sampling,
        ),
        tags=("smoke",) if case_id == "case.alpha" else (),
        self_sampling=self_sampling,
    )
    measured = status in {"ok", "contract-failure"}
    values = sample_values_ns if measured and sample_values_ns is not None else ()
    samples = tuple(Sample(elapsed_ns=value, iterations=1) for value in values)
    return CaseResult(
        spec=spec,
        status=status,
        samples=samples,
        stats=summarize_samples(samples) if samples else None,
        checksum=checksum if measured else None,
        checksum_kind="exact" if measured else None,
        contracts=contracts if measured else (),
        error=None if status == "ok" else f"synthetic {status}",
    )


def _run(
    run_id: str,
    *,
    cases: tuple[CaseResult, ...] | None = None,
    created_at: str = "2026-08-09T00:00:00+00:00",
    suite: str = "pipeline",
    environment_key: str = "environment",
    mode: str = "warm",
    samples: int = 3,
    warmup: int = 1,
    target_ns: int = 1_000,
    disable_gc: bool = False,
    timeout_seconds: float = 120.0,
) -> BenchmarkRun:
    return BenchmarkRun(
        meta=RunMeta(
            run_id=run_id,
            created_at=created_at,
            suite=suite,
            profile="short",
            mode=mode,
            seed=0,
            samples=samples,
            warmup=warmup,
            target_ns=target_ns,
            disable_gc=disable_gc,
            timeout_seconds=timeout_seconds,
        ),
        source=SourceIdentity(commit=run_id, dirty=False, diff_sha256=""),
        environment=EnvironmentFingerprint(
            compatibility_key=environment_key,
            values=_EMPTY,
            unavailable=_EMPTY,
        ),
        cases=(_case(),) if cases is None else cases,
    )


def test_history_joins_shared_case_across_suites_and_ignores_missing_run() -> None:
    first = _run("first", created_at="2026-08-09T00:00:00Z", suite="all")
    unrelated = _run(
        "unrelated",
        created_at="2026-08-09T00:01:00Z",
        suite="targeted",
        cases=(_case("case.beta"),),
    )
    latest = _run(
        "latest",
        created_at="2026-08-09T00:02:00Z",
        suite="smoke",
        cases=(_case(sample_values_ns=(2_000_000,)),),
    )

    model = build_report_view_model((first, unrelated, latest))
    alpha = [point for point in model.history if point.case_id == "case.alpha"]
    coverage = [row for row in model.coverage if row.case_id == "case.alpha"]

    assert [point.run_id for point in alpha] == ["first", "latest"]
    assert [point.segment_id for point in alpha] == [0, 0]
    assert len({point.cohort_id for point in alpha}) == 1
    assert [row.state for row in coverage] == ["current", "current"]
    assert [row.run_id for row in model.timeline] == ["first", "unrelated", "latest"]
    assert {case.case_id for case in model.cases} == {"case.alpha", "case.beta"}
    assert next(case for case in model.cases if case.case_id == "case.beta").historical


def test_incompatible_history_is_preserved_as_separate_cohorts() -> None:
    old_environment = _run("old-env", environment_key="old")
    changed_definition = _run(
        "old-definition",
        created_at="2026-08-09T00:01:00Z",
        cases=(_case(source_sha256="old-source"),),
    )
    current = _run("current", created_at="2026-08-09T00:02:00Z")

    model = build_report_view_model((old_environment, changed_definition, current))
    points = [point for point in model.history if point.case_id == "case.alpha"]

    assert len(points) == 3
    assert len({point.cohort_id for point in points}) == 3
    assert [point.is_current for point in points] == [False, False, True]
    assert model.history_summary.cohort_count == 3
    reasons = [row.reason for row in model.coverage if row.state == "other-cohort"]
    assert "environment differs" in reasons
    assert "case definition differs" in reasons


def test_case_metadata_comes_from_chronological_latest_occurrence() -> None:
    old = _run(
        "old",
        created_at="2026-08-09T00:00:00Z",
        cases=(_case(label="Old label"),),
    )
    latest = _run(
        "latest",
        created_at="2026-08-09T00:01:00Z",
        cases=(_case(label="Latest label"),),
    )

    model = build_report_view_model((latest, old))

    assert model.cases[0].label == "Latest label"
    assert "Latest label" in model.cases[0].selector_label


def test_measurement_signature_uses_effective_self_sampling_policy() -> None:
    normal = _case()
    normal_base = _run("normal-base", cases=(normal,), samples=3)
    normal_head = _run(
        "normal-head",
        cases=(normal,),
        created_at="2026-08-09T00:01:00Z",
        samples=20,
    )
    assert trend_cohort_key(normal_base, normal) != trend_cohort_key(normal_head, normal)

    self_sampling = _case(self_sampling=True)
    self_base = _run("self-base", cases=(self_sampling,), samples=3, warmup=1)
    self_head = _run(
        "self-head",
        cases=(self_sampling,),
        created_at="2026-08-09T00:01:00Z",
        samples=20,
        warmup=5,
        target_ns=99_000,
    )
    assert measurement_signature(self_base, self_sampling) == (
        ("disable_gc", False),
        ("timeout_seconds", 120.0),
    )
    assert trend_cohort_key(self_base, self_sampling) == trend_cohort_key(
        self_head,
        self_sampling,
    )

    self_gc = _run("self-gc", cases=(self_sampling,), disable_gc=True)
    self_timeout = _run("self-timeout", cases=(self_sampling,), timeout_seconds=30.0)
    assert trend_cohort_key(self_base, self_sampling) != trend_cohort_key(
        self_gc,
        self_sampling,
    )
    assert trend_cohort_key(self_base, self_sampling) != trend_cohort_key(
        self_timeout,
        self_sampling,
    )


def test_failure_contract_failure_and_checksum_change_split_segments() -> None:
    failed_hard = evaluate_contract(
        contract_id="hard.output",
        severity="hard",
        actual=False,
        comparator="eq",
        limit=True,
        reason="output contract",
    )
    runs = (
        _run("first", cases=(_case(checksum="a"),)),
        _run(
            "error",
            created_at="2026-08-09T00:01:00Z",
            cases=(_case(status="error", sample_values_ns=None),),
        ),
        _run(
            "after-error",
            created_at="2026-08-09T00:02:00Z",
            cases=(_case(checksum="a"),),
        ),
        _run(
            "changed-output",
            created_at="2026-08-09T00:03:00Z",
            cases=(_case(checksum="b"),),
        ),
        _run(
            "contract-failure",
            created_at="2026-08-09T00:04:00Z",
            cases=(
                _case(
                    status="contract-failure",
                    checksum="b",
                    contracts=(failed_hard,),
                ),
            ),
        ),
        _run(
            "recovered",
            created_at="2026-08-09T00:05:00Z",
            cases=(_case(checksum="b"),),
        ),
    )

    model = build_report_view_model(runs)
    points = model.history

    assert [point.run_id for point in points] == [
        "first",
        "after-error",
        "changed-output",
        "contract-failure",
        "recovered",
    ]
    assert [point.segment_id for point in points] == [0, 1, 2, 3, 4]
    assert [point.checksum_changed for point in points] == [False, False, True, False, False]
    failure_coverage = [row for row in model.coverage if row.state == "failure"]
    assert [row.run_id for row in failure_coverage] == ["error", "contract-failure"]
    changed_coverage = [row for row in model.coverage if row.state == "output-changed"]
    assert [row.run_id for row in changed_coverage] == ["changed-output"]


def test_failure_in_another_cohort_is_a_compatibility_boundary() -> None:
    old_failure = _run(
        "old-failure",
        environment_key="old",
        cases=(_case(status="error", sample_values_ns=None),),
    )
    current = _run("current", created_at="2026-08-09T00:01:00Z")

    model = build_report_view_model((old_failure, current))
    old_coverage = next(row for row in model.coverage if row.run_id == "old-failure")

    assert old_coverage.state == "other-cohort"
    assert "environment differs" in old_coverage.reason
    assert "status is error" in old_coverage.reason


def test_chronology_normalizes_offsets_and_warns_about_invalid_timestamp() -> None:
    earlier = _run("z", created_at="2026-08-09T00:30:00+01:00")
    same_b = _run("b", created_at="2026-08-09T00:00:00Z")
    same_a = _run("a", created_at="2026-08-09T00:00:00+00:00")
    invalid = _run("invalid", created_at="not-a-time")
    input_runs = (invalid, same_b, earlier, same_a)

    assert parse_created_at_utc("2026-08-09T00:30:00+01:00") == datetime(
        2026,
        8,
        8,
        23,
        30,
        tzinfo=timezone.utc,
    )
    assert parse_created_at_utc("2026-08-09T00:00:00") is None
    assert [run.meta.run_id for run in chronological_runs(input_runs)] == ["z", "a", "b"]
    assert latest_valid_run(input_runs) == same_b

    model = build_report_view_model(input_runs, warning_count=2)

    assert model.latest_run_id == "b"
    assert model.history_summary.run_count == 4
    assert model.history_summary.temporal_run_count == 3
    assert model.history_summary.historical_warning_count == 3
    assert model.history_warnings == (
        "invalid: invalid created_at for history: 'not-a-time'",
    )
    assert all(point.run_id != "invalid" for point in model.history)


def test_zero_median_and_mad_are_kept_as_finite_observation() -> None:
    model = build_report_view_model((_run("zero", cases=(_case(sample_values_ns=(0,)),)),))

    assert len(model.history) == 1
    point = model.history[0]
    assert (point.median_ms, point.mad_ms, point.mad_low_ms, point.mad_high_ms) == (
        0.0,
        0.0,
        0.0,
        0.0,
    )


def test_guardrail_history_separates_contract_definition_and_tracks_ratio() -> None:
    low = evaluate_contract(
        contract_id="latency",
        severity="soft",
        actual=25.0,
        comparator="le",
        limit=50.0,
        reason="latency ceiling",
    )
    exceeded = evaluate_contract(
        contract_id="latency",
        severity="soft",
        actual=75.0,
        comparator="le",
        limit=50.0,
        reason="latency ceiling",
    )
    changed_limit = evaluate_contract(
        contract_id="latency",
        severity="soft",
        actual=60.0,
        comparator="le",
        limit=100.0,
        reason="new latency ceiling",
    )
    runs = (
        _run("first", cases=(_case(contracts=(low,)),)),
        _run(
            "second",
            created_at="2026-08-09T00:01:00Z",
            cases=(_case(contracts=(exceeded,)),),
        ),
        _run(
            "new-limit",
            created_at="2026-08-09T00:02:00Z",
            cases=(_case(contracts=(changed_limit,)),),
        ),
    )

    model = build_report_view_model(runs)

    assert [row.load_ratio for row in model.guardrails] == pytest.approx([0.6, 0.5, 1.5])
    assert len({row.cohort_id for row in model.guardrails}) == 2
    assert [row.is_current for row in model.guardrails] == [True, False, False]


def test_guardrail_ratio_is_actual_over_limit_for_zero_higher_is_better() -> None:
    floor = evaluate_contract(
        contract_id="freshness",
        severity="soft",
        actual=0.0,
        comparator="ge",
        limit=0.9,
        reason="freshness floor",
    )

    model = build_report_view_model((_run("zero", cases=(_case(contracts=(floor,)),)),))

    assert len(model.guardrails) == 1
    row = model.guardrails[0]
    assert row.load_ratio == pytest.approx(0.0)
    assert row.load_ratio == pytest.approx(row.actual / row.limit)
    assert row.direction == "higher is better"


@pytest.mark.parametrize(
    "latest_contracts",
    (
        (),
        (
            evaluate_contract(
                contract_id="latency",
                severity="soft",
                actual=25.0,
                comparator="eq",
                limit=25.0,
                reason="non-directional latency contract",
            ),
        ),
        (
            evaluate_contract(
                contract_id="latency",
                severity="hard",
                actual=25.0,
                comparator="le",
                limit=50.0,
                reason="hard latency ceiling",
            ),
        ),
    ),
    ids=("removed", "non-directional", "hard"),
)
def test_latest_non_chartable_definition_does_not_keep_old_series_current(
    latest_contracts: tuple[ContractResult, ...],
) -> None:
    old_guardrail = evaluate_contract(
        contract_id="latency",
        severity="soft",
        actual=25.0,
        comparator="le",
        limit=50.0,
        reason="soft latency ceiling",
    )
    runs = (
        _run("old", cases=(_case(contracts=(old_guardrail,)),)),
        _run(
            "latest",
            created_at="2026-08-09T00:01:00Z",
            cases=(_case(contracts=latest_contracts),),
        ),
    )

    model = build_report_view_model(runs)

    assert len(model.guardrails) == 1
    assert not model.guardrails[0].is_current


def test_guardrail_ignores_absent_case_but_splits_segment_across_other_definition() -> None:
    original = evaluate_contract(
        contract_id="latency",
        severity="soft",
        actual=25.0,
        comparator="le",
        limit=50.0,
        reason="original latency ceiling",
    )
    renamed = evaluate_contract(
        contract_id="latency",
        severity="soft",
        actual=25.0,
        comparator="le",
        limit=50.0,
        reason="renamed latency ceiling",
    )
    runs = (
        _run("first", cases=(_case(contracts=(original,)),)),
        _run(
            "missing-case",
            created_at="2026-08-09T00:01:00Z",
            cases=(_case("case.beta"),),
        ),
        _run(
            "after-missing",
            created_at="2026-08-09T00:02:00Z",
            cases=(_case(contracts=(original,)),),
        ),
        _run(
            "renamed",
            created_at="2026-08-09T00:03:00Z",
            cases=(_case(contracts=(renamed,)),),
        ),
        _run(
            "restored",
            created_at="2026-08-09T00:04:00Z",
            cases=(_case(contracts=(original,)),),
        ),
    )

    model = build_report_view_model(runs)
    original_rows = [row for row in model.guardrails if row.reason == original.reason]
    renamed_rows = [row for row in model.guardrails if row.reason == renamed.reason]

    assert len({row.cohort_id for row in model.guardrails}) == 2
    assert [row.run_id for row in original_rows] == [
        "first",
        "after-missing",
        "restored",
    ]
    assert [row.segment_id for row in original_rows] == [0, 0, 1]
    assert all(row.is_current for row in original_rows)
    assert len(renamed_rows) == 1
    assert not renamed_rows[0].is_current


def test_guardrail_missing_from_same_case_occurrence_splits_segment() -> None:
    guardrail = evaluate_contract(
        contract_id="latency",
        severity="soft",
        actual=25.0,
        comparator="le",
        limit=50.0,
        reason="latency ceiling",
    )
    runs = (
        _run("first", cases=(_case(contracts=(guardrail,)),)),
        _run(
            "contract-absent",
            created_at="2026-08-09T00:01:00Z",
            cases=(_case(contracts=()),),
        ),
        _run(
            "restored",
            created_at="2026-08-09T00:02:00Z",
            cases=(_case(contracts=(guardrail,)),),
        ),
    )

    model = build_report_view_model(runs)

    assert [row.run_id for row in model.guardrails] == ["first", "restored"]
    assert [row.segment_id for row in model.guardrails] == [0, 1]


def test_large_archive_keeps_every_result_without_silent_cap() -> None:
    case_count = 50
    run_count = 250
    cases = tuple(_case(f"case.{index:03d}") for index in range(case_count))
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    runs = tuple(
        _run(
            f"run-{index:03d}",
            created_at=(start + timedelta(minutes=index)).isoformat(),
            cases=cases,
        )
        for index in range(run_count)
    )

    model = build_report_view_model(runs)

    assert len(model.history) == run_count * case_count
    assert len(model.coverage) == run_count * case_count
    assert len(model.timeline) == run_count
    assert model.history_summary.unique_case_count == case_count
    assert model.history_summary.trend_cohort_count == case_count
