from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from xml.etree import ElementTree

from grafix.devtools.benchmarks.report_charts import (
    build_case_overview_chart,
    build_chart_specs,
    build_guardrail_chart,
    build_history_chart,
    build_overview_chart,
    render_overview_svg,
    script_json,
)


def _row(**values: object) -> SimpleNamespace:
    return SimpleNamespace(**values)


def _history(
    *,
    case_id: str = "system.example",
    label: str = "System example",
    category: str = "system",
    cohort_id: str = "current-cohort",
    cohort_label: str = "current · warm · samples 3",
    run_id: str,
    created_at: str,
    run_index: int,
    segment_id: int = 0,
    status: str = "ok",
    median_ms: float = 1.0,
    mad_ms: float = 0.1,
    is_current: bool = True,
    is_latest: bool = False,
) -> SimpleNamespace:
    return _row(
        case_id=case_id,
        label=label,
        category=category,
        cohort_id=cohort_id,
        cohort_label=cohort_label,
        run_id=run_id,
        created_at=created_at,
        run_index=run_index,
        segment_id=segment_id,
        status=status,
        median_ms=median_ms,
        mad_ms=mad_ms,
        mad_low_ms=max(0.0, median_ms - mad_ms),
        mad_high_ms=median_ms + mad_ms,
        p95_ms=None,
        p99_ms=None,
        sample_count=3,
        source_commit=f"commit-{run_id}",
        source_dirty=False,
        mode="warm",
        measurement_label="samples=3, warmup=1, target=1ms, gc=on, timeout=120s",
        checksum=f"checksum-{cohort_id}",
        checksum_changed=False,
        is_current=is_current,
        is_latest=is_latest,
    )


def _coverage(
    *,
    case_id: str = "system.example",
    label: str = "System example",
    category: str = "system",
    cohort_id: str | None = "current-cohort",
    cohort_label: str = "current · warm · samples 3",
    run_id: str,
    created_at: str,
    run_index: int,
    state: str,
    reason: str,
    status: str | None = "ok",
    is_current: bool = True,
    is_latest: bool = False,
) -> SimpleNamespace:
    return _row(
        case_id=case_id,
        label=label,
        category=category,
        cohort_id=cohort_id,
        cohort_label=cohort_label,
        run_id=run_id,
        created_at=created_at,
        run_index=run_index,
        state=state,
        reason=reason,
        status=status,
        is_current=is_current,
        is_latest=is_latest,
    )


def _timeline(*, run_id: str, created_at: str, run_index: int) -> SimpleNamespace:
    return _row(run_id=run_id, created_at=created_at, run_index=run_index)


def _guardrail(
    *,
    case_id: str = "system.example",
    contract_id: str = "latency",
    run_id: str,
    created_at: str,
    run_index: int,
    load_ratio: float,
    passed: bool,
    is_latest: bool = False,
) -> SimpleNamespace:
    return _row(
        case_id=case_id,
        contract_id=contract_id,
        cohort_id=f"{case_id}-latency-v1",
        cohort_label="le 50 ms · soft",
        segment_id=0,
        run_id=run_id,
        created_at=created_at,
        run_index=run_index,
        comparator="le",
        passed=passed,
        actual=load_ratio * 50.0,
        limit=50.0,
        load_ratio=load_ratio,
        direction="lower is better",
        reason="latency ceiling",
        severity="soft",
        is_current=True,
        is_latest=is_latest,
    )


def _model() -> SimpleNamespace:
    first_at = "2026-08-07T00:00:00+00:00"
    middle_at = "2026-08-08T00:00:00+00:00"
    latest_at = "2026-08-09T00:00:00+00:00"
    return _row(
        summary=_row(
            run_id="latest",
            created_at=latest_at,
            source="abcdef",
            suite="pipeline",
            profile="short",
            mode="warm",
            case_count=1,
            status_counts=(("ok", 1),),
            warning_count=0,
            hard_passed=0,
            hard_total=0,
            soft_passed=1,
            soft_total=1,
        ),
        history_summary=_row(
            start_at="2026-08-06T00:00:00+00:00",
            end_at=latest_at,
            run_count=4,
            temporal_run_count=4,
            unique_case_count=2,
            cohort_count=3,
            trend_cohort_count=1,
            latest_warning_count=0,
            historical_warning_count=0,
        ),
        latest_run_id="latest",
        cases=(
            _row(
                case_id="effect.historical",
                label="Historical effect",
                category="effect",
                tags=(),
                selector_label="effect / Historical effect · effect.historical · historical",
                current_cohort_id="effect-cohort",
                cohort_count=1,
                successful_observation_count=1,
                historical=True,
            ),
            _row(
                case_id="system.example",
                label="System example",
                category="system",
                tags=(),
                selector_label="system / System example · system.example",
                current_cohort_id="current-cohort",
                cohort_count=2,
                successful_observation_count=2,
                historical=False,
            ),
        ),
        history=(
            _history(run_id="first", created_at=first_at, run_index=0),
            _history(
                run_id="failed",
                created_at=middle_at,
                run_index=1,
                segment_id=1,
                status="contract-failure",
                median_ms=1.3,
            ),
            _history(
                run_id="latest",
                created_at=latest_at,
                run_index=2,
                segment_id=2,
                median_ms=1.25,
                mad_ms=0.12,
                is_latest=True,
            ),
            _history(
                cohort_id="past-cohort",
                cohort_label="past · process-cold",
                run_id="past",
                created_at="2026-08-06T00:00:00+00:00",
                run_index=-1,
                median_ms=1.8,
                is_current=False,
            ),
            _history(
                case_id="effect.historical",
                label="Historical effect",
                category="effect",
                cohort_id="effect-cohort",
                cohort_label="current · warm",
                run_id="first",
                created_at=first_at,
                run_index=0,
                median_ms=4.0,
                is_latest=False,
            ),
        ),
        timeline=(
            _timeline(
                run_id="past",
                created_at="2026-08-06T00:00:00+00:00",
                run_index=-1,
            ),
            _timeline(run_id="first", created_at=first_at, run_index=0),
            _timeline(run_id="failed", created_at=middle_at, run_index=1),
            _timeline(run_id="latest", created_at=latest_at, run_index=2),
        ),
        coverage=(
            _coverage(
                run_id="first",
                created_at=first_at,
                run_index=0,
                state="current",
                reason="compatible observation",
            ),
            _coverage(
                run_id="failed",
                created_at=middle_at,
                run_index=1,
                state="failure",
                reason="contract failure",
            ),
            _coverage(
                run_id="latest",
                created_at=latest_at,
                run_index=2,
                state="current",
                reason="compatible observation",
                is_latest=True,
            ),
            _coverage(
                case_id="effect.historical",
                label="Historical effect",
                category="effect",
                cohort_id="effect-cohort",
                cohort_label="current · warm",
                run_id="first",
                created_at=first_at,
                run_index=0,
                state="current",
                reason="compatible observation",
            ),
        ),
        guardrails=(
            _guardrail(
                run_id="first",
                created_at=first_at,
                run_index=0,
                load_ratio=0.5,
                passed=True,
            ),
            _guardrail(
                run_id="latest",
                created_at=latest_at,
                run_index=2,
                load_ratio=1.1,
                passed=False,
                is_latest=True,
            ),
            _guardrail(
                case_id="system.other",
                run_id="latest",
                created_at=latest_at,
                run_index=2,
                load_ratio=0.7,
                passed=True,
                is_latest=True,
            ),
        ),
    )


def test_all_interactive_chart_specs_are_history_first_and_valid() -> None:
    specs = build_chart_specs(_model())

    assert list(specs) == [
        "history-chart",
        "case-overview-chart",
        "guardrail-chart",
    ]
    for spec in specs.values():
        assert spec["$schema"].endswith(".json")
        json.dumps(spec, allow_nan=False)

    serialized = json.dumps(specs, sort_keys=True)
    assert "Performance history" in serialized
    assert "history_case" in serialized
    assert "mad_low_ms" in serialized
    assert "median_ms" in serialized
    assert "segment_id" in serialized
    assert "source_commit" in serialized
    assert '"type": "utc"' in serialized
    assert "Run coverage" in serialized
    assert "case is not part of this run" in serialized
    assert "overview_category" in serialized
    assert "load_ratio" in serialized
    assert "guardrail_series" in serialized
    assert "regression" not in serialized.lower()
    assert "delta_fraction" not in serialized
    assert "history_mode" not in serialized
    assert "From first positive" not in serialized


def test_performance_and_coverage_share_one_case_selector() -> None:
    spec = build_history_chart(_model()).to_dict(validate=True)

    assert len(spec["vconcat"]) == 2
    assert [param["name"] for param in spec["params"]] == ["history_case"]
    assert spec["resolve"]["scale"]["x"] == "shared"
    performance = spec["vconcat"][0]
    assert performance["facet"]["row"]["field"] == "cohort_label"
    assert performance["facet"]["row"]["header"]["labelAngle"] == 0
    assert performance["resolve"]["scale"]["y"] == "independent"
    assert "bind" not in spec["params"][0]
    serialized = json.dumps(spec, ensure_ascii=False)
    assert "MAD is within-run sample variability, not a confidence interval" in serialized
    assert "1 compatible observation; trend unavailable" in serialized
    assert "checksum_changed" in serialized
    assert '"stable", "changed"' in serialized
    assert (
        '"current", "output-changed", "other-cohort", "missing", "failure"'
        in serialized
    )
    assert '"field": "created_at", "title": "UTC", "type": "nominal"' in serialized


def test_default_case_falls_back_from_latest_failure_to_measured_history() -> None:
    model = _model()
    model.cases = (
        _row(
            case_id="failure.only",
            label="Failure only",
            category="system",
            tags=(),
            selector_label="system / Failure only · failure.only",
            current_cohort_id="failure-cohort",
            cohort_count=1,
            successful_observation_count=0,
            historical=False,
        ),
        _row(
            case_id="effect.historical",
            label="Historical effect",
            category="effect",
            tags=(),
            selector_label="effect / Historical effect · effect.historical · historical",
            current_cohort_id="effect-cohort",
            cohort_count=1,
            successful_observation_count=1,
            historical=True,
        ),
    )
    model.history = (
        _history(
            case_id="effect.historical",
            label="Historical effect",
            category="effect",
            cohort_id="effect-cohort",
            run_id="first",
            created_at="2026-08-07T00:00:00+00:00",
            run_index=0,
        ),
    )
    model.coverage = (
        _coverage(
            case_id="failure.only",
            label="Failure only",
            category="system",
            cohort_id="failure-cohort",
            run_id="latest",
            created_at="2026-08-09T00:00:00+00:00",
            run_index=2,
            state="failure",
            reason="current cohort status is error",
            status="error",
        ),
        _coverage(
            case_id="effect.historical",
            label="Historical effect",
            category="effect",
            cohort_id="effect-cohort",
            run_id="first",
            created_at="2026-08-07T00:00:00+00:00",
            run_index=0,
            state="current",
            reason="compatible observation",
        ),
    )

    spec = build_history_chart(model).to_dict(validate=True)

    selector = spec["params"][0]
    assert selector["value"] == "effect.historical"
    assert "bind" not in selector


def test_coverage_spec_normalizes_sparse_archive_without_case_run_grid() -> None:
    run_count = 200
    case_count = 162
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    model = _model()
    model.cases = tuple(
        _row(
            case_id=f"case.{index:03d}",
            label=f"Case {index:03d}",
            category="synthetic",
            tags=(),
            selector_label=f"synthetic / Case {index:03d} · case.{index:03d}",
            current_cohort_id="cohort",
            cohort_count=1,
            successful_observation_count=2,
            historical=False,
        )
        for index in range(case_count)
    )
    timestamps = tuple(
        (start + timedelta(minutes=index)).isoformat()
        for index in range(run_count)
    )
    model.timeline = tuple(
        _timeline(
            run_id=f"run-{index:03d}",
            created_at=timestamps[index],
            run_index=index,
        )
        for index in range(run_count)
    )
    model.coverage = tuple(
        _coverage(
            case_id=f"case.{index % case_count:03d}",
            label=f"Case {index % case_count:03d}",
            category="synthetic",
            cohort_id="cohort",
            run_id=f"run-{index:03d}",
            created_at=timestamps[index],
            run_index=index,
            state="current",
            reason="compatible observation",
        )
        for index in range(run_count)
    )
    model.history = tuple(
        _history(
            case_id=f"case.{index % case_count:03d}",
            label=f"Case {index % case_count:03d}",
            category="synthetic",
            cohort_id="cohort",
            run_id=f"run-{index:03d}",
            created_at=timestamps[index],
            run_index=index,
        )
        for index in range(run_count)
    )

    spec = build_history_chart(model).to_dict(validate=True)
    coverage = spec["vconcat"][1]
    datasets = spec.get("datasets", {})

    def values(data: dict[str, object]) -> list[object]:
        if "values" in data:
            return data["values"]  # type: ignore[return-value]
        return datasets[str(data["name"])]

    lookup = next(transform for transform in coverage["transform"] if "lookup" in transform)
    timeline_values = values(coverage["data"])
    observation_values = values(lookup["from"]["data"])

    assert len(timeline_values) == run_count
    assert len(observation_values) == run_count
    assert max(map(len, datasets.values())) < run_count * case_count
    assert "bind" not in spec["params"][0]
    assert "isValid(datum.observed_state)" in json.dumps(coverage["transform"])

    overview = build_case_overview_chart(model).to_dict(validate=True)
    overview_datasets = overview.get("datasets", {})
    overview_values = (
        overview["data"]["values"]
        if "values" in overview["data"]
        else overview_datasets[overview["data"]["name"]]
    )
    unique_observations = {
        (str(row["case_id"]), str(row["run_id"]))
        for row in overview_values
    }
    assert len(overview_values) == len(unique_observations)


def test_small_multiples_use_independent_y_scales_and_state_limit() -> None:
    spec = build_case_overview_chart(_model()).to_dict(validate=True)

    assert spec["resolve"]["scale"]["y"] == "independent"
    assert spec["columns"] == 3
    assert spec["transform"] == [
        {
            "calculate": (
                "overview_category === 'All' ? datum.global_rank : "
                "datum.category_rank"
            ),
            "as": "rank",
        },
        {
            "filter": (
                "(overview_category === 'All' && datum.in_global) || "
                "(datum.category === overview_category && datum.in_category)"
            )
        },
    ]
    assert all("filter" not in transform for transform in spec["spec"]["transform"])
    assert "2 / 2 cases shown" in json.dumps(spec)


def test_guardrail_history_keeps_case_contract_labels_unique() -> None:
    spec = build_guardrail_chart(_model()).to_dict(validate=True)
    serialized = json.dumps(spec, ensure_ascii=False)

    assert spec["resolve"]["scale"]["color"] == "independent"
    assert "system.example · latency" in serialized
    assert "system.other · latency" in serialized
    assert "Definition changes form separate series" in serialized
    assert "Actual / limit = 1.0 is the threshold" in serialized
    assert '"limit": 1.0' in serialized
    assert '"pass", "fail"' in serialized


def test_static_overview_is_history_first_and_renders_finite_svg() -> None:
    spec = build_overview_chart(_model()).to_dict(validate=True)

    assert len(spec["vconcat"]) == 3
    assert spec["resolve"]["scale"]["color"] == "independent"
    serialized = json.dumps(spec, allow_nan=False)
    assert "Grafix benchmark history" in serialized
    assert "6 cases" not in serialized
    assert "regression" not in serialized.lower()
    assert "Compared with" not in serialized

    svg = render_overview_svg(_model())
    ElementTree.fromstring(svg)
    assert "2026" in svg
    assert "Case trend overview" in svg
    assert ">System example ·" in svg
    assert "→" in svg
    assert ">undefined<" not in svg


def test_empty_history_has_explanatory_message() -> None:
    model = _row(
        summary=None,
        history_summary=_row(
            start_at=None,
            end_at=None,
            run_count=0,
            temporal_run_count=0,
            unique_case_count=0,
            cohort_count=0,
            trend_cohort_count=0,
            latest_warning_count=0,
            historical_warning_count=0,
        ),
        latest_run_id=None,
        cases=(),
        history=(),
        timeline=(),
        coverage=(),
        guardrails=(),
    )

    history = json.dumps(build_history_chart(model).to_dict(validate=True))
    overview = json.dumps(build_overview_chart(model).to_dict(validate=True))

    assert "No measured schema v4 history is available" in history
    assert "No dated valid schema v4 run is available" in overview


def test_script_json_neutralizes_inline_script_breakout() -> None:
    value = {"label": "</script><script>alert('&')\u2028next"}

    encoded = script_json(value)

    assert "<" not in encoded
    assert ">" not in encoded
    assert "&" not in encoded
    assert "\u2028" not in encoded
    assert r"\u003c/script\u003e\u003cscript\u003e" in encoded
    assert json.loads(encoded) == value
