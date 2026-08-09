from __future__ import annotations

import json
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from grafix.devtools.benchmarks.report import (
    LoadedRuns,
    load_runs,
    render_report_html,
    write_report,
)
from grafix.devtools.benchmarks.schema import (
    BenchmarkRun,
    CaseResult,
    CaseSpec,
    EnvironmentFingerprint,
    Metric,
    RunMeta,
    Sample,
    SourceIdentity,
    case_compatibility_key,
    environment_compatibility_key,
    evaluate_contract,
    freeze_json_object,
    summarize_samples,
    write_benchmark_run,
)


class _ExternalResourceParser(HTMLParser):
    """HTML の resource 属性に remote URL がないことだけを検査する。"""

    def __init__(self) -> None:
        super().__init__()
        self.remote_resources: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del tag
        for name, value in attrs:
            if (
                name in {"src", "href", "poster"}
                and value is not None
                and value.startswith(("http://", "https://", "//"))
            ):
                self.remote_resources.append(value)


def _write_valid_run(
    runs_dir: Path,
    *,
    warnings: tuple[str, ...] = (),
    contract_actual: float = 12.5,
    label: str = "System example",
) -> None:
    empty_object = freeze_json_object({})
    spec = CaseSpec(
        case_id="system.example",
        version=1,
        label=label,
        category="system",
        suite="pipeline",
        fixture="fixture",
        parameters=empty_object,
        seed=0,
        source_sha256="source",
        compatibility_key=case_compatibility_key(
            case_id="system.example",
            version=1,
            fixture="fixture",
            parameters=empty_object,
            seed=0,
            source_sha256="source",
        ),
        tags=("scaling",),
    )
    sample = Sample(elapsed_ns=1_250_000, iterations=1)
    run = BenchmarkRun(
        meta=RunMeta(
            run_id="valid",
            created_at="2026-07-17T00:00:00+00:00",
            suite="pipeline",
            profile="short",
            mode="warm",
            seed=0,
            samples=1,
            warmup=0,
            target_ns=0,
            timeout_seconds=120.0,
        ),
        source=SourceIdentity(commit="abcdef", dirty=False, diff_sha256=""),
        environment=EnvironmentFingerprint(
            compatibility_key=environment_compatibility_key(
                empty_object,
                empty_object,
            ),
            values=empty_object,
            unavailable=empty_object,
        ),
        cases=(
            CaseResult(
                spec=spec,
                status="ok",
                samples=(sample,),
                stats=summarize_samples([sample]),
                checksum="checksum",
                checksum_kind="exact",
                setup_rss_bytes=1 * 1024 * 1024,
                baseline_rss_bytes=1 * 1024 * 1024,
                peak_rss_bytes=3 * 1024 * 1024,
                peak_rss_delta_bytes=2 * 1024 * 1024,
                metrics=(
                    Metric(
                        name="input_to_present_ms",
                        kind="gauge",
                        unit="ms",
                        phase="drag",
                        scope="scenario",
                        value=12.5,
                    ),
                ),
                contracts=(
                    evaluate_contract(
                        contract_id="ux.input_to_present",
                        severity="soft",
                        actual=contract_actual,
                        comparator="le",
                        limit=50.0,
                        reason="input-to-present remains within target",
                    ),
                ),
            ),
        ),
        warnings=warnings,
    )
    write_benchmark_run(runs_dir / "valid.json", run)


def test_report_keeps_broken_and_unsupported_runs_as_warnings(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_valid_run(runs_dir)
    (runs_dir / "broken.json").write_text("{broken", encoding="utf-8")
    (runs_dir / "v2.json").write_text(
        json.dumps({"schema_version": 2}),
        encoding="utf-8",
    )

    loaded = load_runs(runs_dir)
    assert len(loaded.runs) == 1
    assert len(loaded.warnings) == 2
    assert any("broken.json" in warning for warning in loaded.warnings)
    assert any("v2.json" in warning for warning in loaded.warnings)

    html = render_report_html(loaded)
    assert "System example" in html
    assert "system.example" in html
    assert "1.250000" in html
    assert "2.00" in html
    assert "soft: <span" in html
    assert "input_to_present_ms" in html
    assert ".chart.vega-embed" in html
    resource_parser = _ExternalResourceParser()
    resource_parser.feed(html)
    assert resource_parser.remote_resources == []
    assert "broken.json" in html

    artifacts = write_report(tmp_path)
    assert artifacts.loaded == loaded
    assert artifacts.report_path.is_file()
    assert artifacts.overview_path.is_file()
    assert artifacts.warnings_path.is_file()
    svg_root = ElementTree.parse(artifacts.overview_path).getroot()
    assert svg_root.tag.endswith("svg")
    warning_payload = json.loads(artifacts.warnings_path.read_text(encoding="utf-8"))
    assert warning_payload["valid_runs"] == 1
    assert warning_payload["warning_count"] == 2


def test_report_escapes_script_breakout_in_chart_data(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_valid_run(
        runs_dir,
        label='</script><script id="benchmark-report-breakout">',
    )

    html = render_report_html(load_runs(runs_dir))

    assert 'id="benchmark-report-breakout"' not in html
    assert "benchmark-report-breakout" in html
    assert r"\u003c/script\u003e\u003cscript" in html


def test_report_includes_warnings_from_valid_runs(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_valid_run(runs_dir, warnings=("system.example: skipped",))

    loaded = load_runs(runs_dir)

    assert len(loaded.runs) == 1
    assert len(loaded.warnings) == 1
    assert "system.example: skipped" in loaded.warnings[0]


def test_report_warns_on_soft_contract_without_failing_case(
    tmp_path: Path,
) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_valid_run(runs_dir, contract_actual=75.0)

    loaded = load_runs(runs_dir)
    html = render_report_html(loaded)

    assert loaded.runs[0].cases[0].status == "ok"
    assert any("soft contract failed" in warning for warning in loaded.warnings)
    assert "soft-fail" in html


def test_report_delta_requires_compatible_mode_and_settings(
    tmp_path: Path,
) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_valid_run(runs_dir)
    base = load_runs(runs_dir).runs[0]
    slower_sample = Sample(elapsed_ns=2_500_000, iterations=1)
    slower_result = replace(
        base.cases[0],
        samples=(slower_sample,),
        stats=summarize_samples((slower_sample,)),
    )
    incompatible = replace(
        base,
        meta=replace(
            base.meta,
            run_id="incompatible",
            created_at="2026-07-17T00:01:00+00:00",
            mode="process-cold",
        ),
        cases=(slower_result,),
    )

    html = render_report_html(LoadedRuns((base, incompatible), ()))

    assert "+100.0%" not in html

    self_sampling_result = replace(
        base.cases[0], spec=replace(base.cases[0].spec, self_sampling=True)
    )
    self_sampling_base = replace(base, cases=(self_sampling_result,))
    compatible_head = replace(
        base,
        meta=replace(
            base.meta,
            run_id="compatible",
            created_at="2026-07-17T00:01:00+00:00",
            samples=20,
            warmup=3,
            target_ns=250_000_000,
        ),
        cases=(
            replace(
                slower_result,
                spec=replace(slower_result.spec, self_sampling=True),
            ),
        ),
    )

    html = render_report_html(LoadedRuns((self_sampling_base, compatible_head), ()))

    assert "+100.0%" in html


def test_report_prioritizes_ux_metrics_and_shows_contract_operands(
    tmp_path: Path,
) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_valid_run(runs_dir)
    run = load_runs(runs_dir).runs[0]
    background_metrics = tuple(
        Metric(
            name=f"background.metric.{index}",
            kind="gauge",
            unit="count",
            phase="measure",
            scope="case",
            value=float(index),
        )
        for index in range(10)
    )
    ux_metric = Metric(
        name="ux01.input_to_present_ms",
        kind="gauge",
        unit="ms",
        phase="drag",
        scope="scenario",
        value=12.5,
    )
    run = replace(
        run,
        cases=(
            replace(
                run.cases[0],
                metrics=background_metrics + (ux_metric,),
            ),
        ),
    )

    html = render_report_html(LoadedRuns((run,), ()))

    assert "ux01.input_to_present_ms" in html
    assert "background.metric.9" not in html
    assert "actual=12.5 le limit=50.0" in html
