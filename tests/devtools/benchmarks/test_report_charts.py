from __future__ import annotations

import json
from dataclasses import replace

from grafix.devtools.benchmarks.report_charts import (
    build_chart_specs,
    build_overview_chart,
    build_regression_chart,
    render_overview_svg,
    script_json,
)
from grafix.devtools.benchmarks.report_model import (
    DeltaView,
    GuardrailView,
    HistoryView,
    ReportViewModel,
    SummaryView,
    TimingView,
)


def _model() -> ReportViewModel:
    return ReportViewModel(
        summary=SummaryView(
            run_id="head",
            created_at="2026-08-09T00:01:00+00:00",
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
        latest_run_id="head",
        baseline_run_id="base",
        baseline_message="Compared with base.",
        comparisons=(),
        deltas=(
            DeltaView(
                base_run_id="base",
                head_run_id="head",
                case_id="system.example",
                label="System example",
                category="system",
                base_median_ms=1.0,
                head_median_ms=1.25,
                base_mad_ms=0.1,
                head_mad_ms=0.12,
                delta_fraction=0.25,
                direction="regression",
            ),
        ),
        timings=(
            TimingView(
                case_id="system.example",
                label="System example",
                category="system",
                status="ok",
                self_sampling=False,
                median_ms=1.25,
                mad_ms=0.12,
                p95_ms=None,
                p99_ms=None,
            ),
        ),
        history=(
            HistoryView(
                case_id="system.example",
                label="System example",
                category="system",
                run_id="head",
                created_at="2026-08-09T00:01:00+00:00",
                run_index=1,
                segment_id=0,
                view_mode="absolute",
                value=1.25,
                low=1.13,
                high=1.37,
                unit="ms",
                value_label="1.250000 ms",
                mad_label="0.120000 ms",
            ),
            HistoryView(
                case_id="system.example",
                label="System example",
                category="system",
                run_id="head",
                created_at="2026-08-09T00:01:00+00:00",
                run_index=1,
                segment_id=0,
                view_mode="relative",
                value=25.0,
                low=13.0,
                high=37.0,
                unit="%",
                value_label="+25.00%",
                mad_label="±12.00%",
            ),
        ),
        guardrails=(
            GuardrailView(
                case_id="system.example",
                contract_id="latency",
                comparator="le",
                passed=True,
                actual=25.0,
                limit=50.0,
                load_ratio=0.5,
                direction="lower is better",
                reason="latency ceiling",
            ),
        ),
    )


def test_all_interactive_chart_specs_pass_altair_validation() -> None:
    specs = build_chart_specs(_model())

    assert set(specs) == {
        "regression-chart",
        "history-chart",
        "timing-chart",
        "guardrail-chart",
    }
    for spec in specs.values():
        assert spec["$schema"].endswith(".json")
        json.dumps(spec, allow_nan=False)

    serialized = json.dumps(specs, sort_keys=True)
    assert "regression_category" in serialized
    assert "history_case" in serialized
    assert "history_mode" in serialized
    assert '"type": "log"' in serialized
    assert "plot_floor_ms" in serialized
    assert "segment_id" in serialized
    assert "load_ratio" in serialized


def test_overview_composition_passes_altair_validation() -> None:
    spec = build_overview_chart(_model()).to_dict(validate=True)

    assert spec["$schema"].endswith(".json")
    assert len(spec["vconcat"]) == 3
    assert spec["resolve"]["scale"]["color"] == "independent"
    assert spec["vconcat"][0]["encoding"]["x"] == {"value": 4}

    svg = render_overview_svg(_model())
    assert 'fill="#168267"' in svg


def test_empty_regression_chart_explains_missing_comparable_measurement() -> None:
    model = replace(
        _model(),
        deltas=(),
        baseline_run_id="base",
        baseline_message="Compared with base.",
    )

    spec = build_regression_chart(model).to_dict(validate=True)
    serialized = json.dumps(spec)

    assert "positive baseline median" in serialized
    assert spec["encoding"]["x"] == {"value": 4}


def test_guardrail_labels_remain_unique_across_cases() -> None:
    guardrail = _model().guardrails[0]
    model = replace(
        _model(),
        guardrails=(guardrail, replace(guardrail, case_id="system.other")),
    )

    serialized = json.dumps(
        build_chart_specs(model)["guardrail-chart"],
        ensure_ascii=False,
    )

    assert "system.example · latency" in serialized
    assert "system.other · latency" in serialized


def test_script_json_neutralizes_inline_script_breakout() -> None:
    value = {"label": "</script><script>alert('&')\u2028next"}

    encoded = script_json(value)

    assert "<" not in encoded
    assert ">" not in encoded
    assert "&" not in encoded
    assert "\u2028" not in encoded
    assert r"\u003c/script\u003e\u003cscript\u003e" in encoded
    assert json.loads(encoded) == value
