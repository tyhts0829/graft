"""schema v4 run を offline HTML/SVG report にまとめる。"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Protocol, cast

from grafix.file_io import atomic_write_text
from grafix.devtools.benchmarks.report_model import (
    ReportViewModel,
    build_report_view_model,
    comparison_delta_lookup,
)
from grafix.devtools.benchmarks.schema import (
    BenchmarkRun,
    BenchmarkSchemaError,
    ContractResult,
    Metric,
    case_result_to_dict,
    read_benchmark_run,
)


class BenchmarkReportDependencyError(RuntimeError):
    """可視化用 optional dependency が導入されていないことを表す。"""


class _ChartModule(Protocol):
    def build_chart_specs(self, model: ReportViewModel) -> dict[str, dict[str, Any]]: ...

    def offline_vega_runtime(self) -> str: ...

    def render_overview_svg(self, model: ReportViewModel) -> str: ...

    def script_json(self, value: object) -> str: ...


@dataclass(frozen=True, slots=True)
class LoadedRuns:
    """有効 run と、黙って捨てない load warning。"""

    runs: tuple[BenchmarkRun, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReportArtifacts:
    """benchmark report が生成した artifact と入力 run。"""

    report_path: Path
    overview_path: Path
    warnings_path: Path
    loaded: LoadedRuns


def load_runs(runs_dir: str | Path) -> LoadedRuns:
    """directory 内の全 JSON を読み、壊れた run と contract を warning にする。"""

    directory = Path(runs_dir)
    runs: list[BenchmarkRun] = []
    warnings: list[str] = []
    for path in sorted(directory.glob("*.json")):
        try:
            run = read_benchmark_run(path)
            runs.append(run)
            warnings.extend(f"{path}: {warning}" for warning in run.warnings)
            for result in run.cases:
                warnings.extend(
                    (
                        f"{path}: {result.spec.case_id}: "
                        f"{contract.severity} contract failed: "
                        f"{contract.contract_id}: {contract.reason}"
                    )
                    for contract in result.contracts
                    if not contract.passed
                )
        except BenchmarkSchemaError as exc:
            warnings.append(str(exc))
    runs.sort(key=lambda run: (run.meta.created_at, run.meta.run_id))
    return LoadedRuns(runs=tuple(runs), warnings=tuple(warnings))


def write_report(out_root: str | Path) -> ReportArtifacts:
    """report.html、overview.svg、warnings.json を atomic に生成する。"""

    root = Path(out_root).expanduser().resolve()
    loaded = load_runs(root / "runs")
    model = build_report_view_model(
        loaded.runs,
        warning_count=len(loaded.warnings),
    )
    charts = _load_chart_module()
    chart_specs = charts.build_chart_specs(model)
    report_text = _render_report_html(
        loaded,
        model=model,
        chart_specs=chart_specs,
        runtime=charts.offline_vega_runtime(),
        specs_json=charts.script_json(chart_specs),
    )
    overview_text = charts.render_overview_svg(model)
    warnings_text = _warnings_json(loaded)

    report_path = root / "report.html"
    overview_path = root / "overview.svg"
    warnings_path = root / "warnings.json"
    atomic_write_text(report_path, report_text)
    atomic_write_text(overview_path, overview_text)
    atomic_write_text(warnings_path, warnings_text)
    return ReportArtifacts(
        report_path=report_path,
        overview_path=overview_path,
        warnings_path=warnings_path,
        loaded=loaded,
    )


def render_report_html(loaded: LoadedRuns) -> str:
    """offline runtime と検証済み chart spec を含む自己完結 HTML を返す。"""

    model = build_report_view_model(
        loaded.runs,
        warning_count=len(loaded.warnings),
    )
    charts = _load_chart_module()
    chart_specs = charts.build_chart_specs(model)
    return _render_report_html(
        loaded,
        model=model,
        chart_specs=chart_specs,
        runtime=charts.offline_vega_runtime(),
        specs_json=charts.script_json(chart_specs),
    )


def _render_report_html(
    loaded: LoadedRuns,
    *,
    model: ReportViewModel,
    chart_specs: dict[str, dict[str, Any]],
    runtime: str,
    specs_json: str,
) -> str:
    warning_items = "".join(f"<li>{escape(warning)}</li>" for warning in loaded.warnings)
    if not warning_items:
        warning_items = "<li>none</li>"
    warning_class = "warnings" if loaded.warnings else "warnings warnings-clear"
    table_body = _run_rows(loaded.runs, model=model)
    if not table_body:
        table_body = '<tr><td colspan="14">有効な schema v4 run がありません。</td></tr>'
    scaling_body = _scaling_rows(loaded.runs)
    chart_sections = _chart_sections(tuple(chart_specs))
    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Grafix benchmark report</title>
  <style>
    :root {{
      --ink: #172033; --muted: #5d697c; --line: #d8deea; --panel: #f7f9fc;
      --blue: #315d8c; --green: #168267; --red: #b94855; --amber: #9a6500;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      font: 14px system-ui, -apple-system, sans-serif; margin: 0; color: var(--ink);
      background: #eef2f7;
    }}
    main {{ max-width: 1240px; margin: 0 auto; padding: 32px 24px 72px; }}
    h1 {{ margin: 0; font-size: clamp(26px, 4vw, 42px); letter-spacing: -0.03em; }}
    h2 {{ margin: 36px 0 8px; font-size: 20px; }}
    p {{ color: var(--muted); }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    small {{ color: #526075; }}
    .hero {{
      padding: 28px; border: 1px solid #d2dae7; border-radius: 18px; background: #fff;
      box-shadow: 0 12px 35px rgba(27, 43, 68, 0.07);
    }}
    .eyebrow {{ color: var(--blue); font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }}
    .meta {{ margin: 10px 0 0; color: var(--muted); }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-top: 22px; }}
    .card {{ padding: 14px 16px; border-radius: 12px; background: var(--panel); border: 1px solid #e0e6ef; }}
    .card strong {{ display: block; font-size: 22px; margin-top: 5px; }}
    .card span {{ color: var(--muted); font-size: 12px; }}
    .baseline {{ margin: 16px 0 0; padding: 11px 14px; border-left: 4px solid var(--blue); background: #edf4fb; }}
    .chart-panel {{ margin-top: 16px; padding: 20px; border: 1px solid #d8e0eb; border-radius: 16px; background: #fff; }}
    .chart {{ width: 100%; max-width: 100%; overflow-x: auto; min-height: 92px; }}
    .chart.vega-embed {{ display: block; width: 100%; }}
    .chart-error {{ color: var(--red); padding: 16px; background: #fff1f2; border-radius: 8px; }}
    .warnings {{ border: 1px solid #e6b95c; background: #fff8e8; padding: 10px 18px; border-radius: 12px; }}
    .warnings-clear {{ border-color: #a9d5bd; background: #effaf4; }}
    .warnings ul {{ margin: 8px 0; padding-left: 20px; }}
    details.report-details {{ margin-top: 28px; padding: 0 18px 18px; background: #fff; border: 1px solid #d8e0eb; border-radius: 14px; }}
    details.report-details > summary {{ cursor: pointer; font-weight: 700; padding: 18px 0; }}
    .table-wrap {{ overflow: auto; max-height: 72vh; border: 1px solid #e1e6ee; }}
    table {{ border-collapse: collapse; width: 100%; min-width: 1120px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; }}
    th {{ position: sticky; top: 0; z-index: 1; background: #f3f6fb; white-space: nowrap; }}
    .hash {{ display: inline-block; max-width: 10rem; overflow: hidden; text-overflow: ellipsis; }}
    .pass, .status-ok {{ color: #176b3a; font-weight: 650; }}
    .fail, .status-fail {{ color: #a32929; font-weight: 700; }}
    .soft-fail {{ color: var(--amber); font-weight: 650; }}
    @media (max-width: 680px) {{ main {{ padding: 18px 12px 48px; }} .hero, .chart-panel {{ padding: 16px; }} }}
  </style>
  <script type="text/javascript">{runtime}</script>
</head>
<body>
<main>
  {_summary_html(model)}
  <h2>Visual overview</h2>
  <p>互換な計測だけを比較します。差分は観測値であり、統計的有意差を意味しません。</p>
  {chart_sections}
  <h2>Warnings</h2>
  <section class="{warning_class}"><ul>{warning_items}</ul></section>
  <details class="report-details">
    <summary>Detailed runs ({len(loaded.runs)} valid runs)</summary>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>run</th><th>source</th><th>case</th><th>category</th>
          <th>status</th><th>checksum</th><th>contracts</th>
          <th>median ms</th><th>compatible Δ</th><th>MAD ms</th>
          <th>p95 ms</th><th>p99 ms</th><th>RSS delta MiB</th><th>metrics</th>
        </tr></thead>
        <tbody>{table_body}</tbody>
      </table>
    </div>
  </details>
  <details class="report-details">
    <summary>Scaling cases</summary>
    <div class="table-wrap">
      <table>
        <thead><tr><th>run</th><th>case</th><th>parameters</th><th>median ms</th></tr></thead>
        <tbody>{scaling_body}</tbody>
      </table>
    </div>
  </details>
</main>
<script id="benchmark-chart-specs" type="application/json">{specs_json}</script>
<script type="text/javascript">
(() => {{
  const element = document.getElementById("benchmark-chart-specs");
  const specs = JSON.parse(element.textContent);
  for (const [elementId, spec] of Object.entries(specs)) {{
    const target = document.getElementById(elementId);
    window.vegaEmbed(target, spec, {{renderer: "svg", actions: false}}).catch(error => {{
      target.classList.add("chart-error");
      target.textContent = `Chart rendering failed: ${{error.message}}`;
    }});
  }}
}})();
</script>
</body>
</html>
"""


def _summary_html(model: ReportViewModel) -> str:
    summary = model.summary
    if summary is None:
        return """
  <header class="hero">
    <div class="eyebrow">schema v4</div>
    <h1>Grafix benchmark</h1>
    <p class="meta">No valid run is available.</p>
  </header>"""
    statuses = ", ".join(f"{escape(status)} {count}" for status, count in summary.status_counts)
    status_counts = dict(summary.status_counts)
    ok_count = status_counts.get("ok", 0)
    failed_count = summary.case_count - ok_count
    failed_statuses = (
        ", ".join(f"{status} {count}" for status, count in summary.status_counts if status != "ok")
        or "none"
    )
    return f"""
  <header class="hero">
    <div class="eyebrow">schema v4 · latest run</div>
    <h1>Grafix benchmark</h1>
    <p class="meta"><code>{escape(summary.run_id)}</code> · {escape(summary.created_at)}</p>
    <p class="meta">source <code>{escape(summary.source[:12])}</code> ·
      {escape(summary.suite)} / {escape(summary.profile)} / {escape(summary.mode)}</p>
    <div class="cards">
      {_summary_card("Cases", str(summary.case_count), statuses)}
      {_summary_card("OK", str(ok_count), "measured successfully")}
      {_summary_card("Failures", str(failed_count), failed_statuses)}
      {_summary_card("Hard contracts", f"{summary.hard_passed}/{summary.hard_total}", "passed")}
      {_summary_card("Soft contracts", f"{summary.soft_passed}/{summary.soft_total}", "passed")}
      {_summary_card("Warnings", str(summary.warning_count), "across loaded runs")}
    </div>
    <p class="baseline">{escape(model.baseline_message)}</p>
  </header>"""


def _summary_card(label: str, value: str, detail: str) -> str:
    return (
        '<div class="card">'
        f"<span>{escape(label)}</span><strong>{escape(value)}</strong>"
        f"<span>{escape(detail)}</span></div>"
    )


def _chart_sections(element_ids: tuple[str, ...]) -> str:
    return "\n".join(
        (
            '<section class="chart-panel">'
            f'<div class="chart" id="{escape(element_id)}"></div>'
            "</section>"
        )
        for element_id in element_ids
    )


def _run_rows(runs: tuple[BenchmarkRun, ...], *, model: ReportViewModel) -> str:
    rows: list[str] = []
    delta_lookup = comparison_delta_lookup(model)
    for run in runs:
        source = run.source.commit or "unavailable"
        for result in run.cases:
            stats = result.stats
            median_ms = "" if stats is None else _milliseconds(stats.median_ns)
            mad_ms = "" if stats is None else _milliseconds(stats.mad_ns)
            p95_ms = "" if stats is None or stats.p95_ns is None else _milliseconds(stats.p95_ns)
            p99_ms = "" if stats is None or stats.p99_ns is None else _milliseconds(stats.p99_ns)
            rss_mib = (
                ""
                if result.peak_rss_delta_bytes is None
                else f"{result.peak_rss_delta_bytes / (1024.0 * 1024.0):.2f}"
            )
            delta = delta_lookup.get((run.meta.run_id, result.spec.case_id))
            delta_html = (
                ""
                if delta is None
                else (
                    f'<span title="base run: {escape(delta.base_run_id)}">'
                    f"{delta.delta_fraction * 100.0:+.1f}%</span>"
                )
            )
            hard_contracts = [
                contract for contract in result.contracts if contract.severity == "hard"
            ]
            soft_contracts = [
                contract for contract in result.contracts if contract.severity == "soft"
            ]
            checksum_html = (
                ""
                if result.checksum is None
                else (
                    f'<span class="pass">present</span><br>'
                    f'<small class="hash">{escape(result.checksum)}</small>'
                )
            )
            rows.append(
                "<tr>"
                f"<td>{escape(run.meta.run_id)}</td>"
                f"<td>{escape(source[:12])}</td>"
                f"<td><code>{escape(result.spec.case_id)}</code><br>"
                f"<small>{escape(result.spec.label)}</small></td>"
                f"<td>{escape(result.spec.category)}</td>"
                f'<td class="{_status_class(result.status)}">{escape(result.status)}</td>'
                f"<td>{checksum_html}</td>"
                f"<td>{_contract_summary(hard_contracts, soft_contracts)}</td>"
                f"<td>{median_ms}</td><td>{delta_html}</td><td>{mad_ms}</td>"
                f"<td>{p95_ms}</td><td>{p99_ms}</td><td>{rss_mib}</td>"
                f"<td>{_metrics_summary(result.metrics)}</td>"
                "</tr>"
            )
    return "\n".join(rows)


def _milliseconds(value: float) -> str:
    return f"{value / 1_000_000.0:.6f}"


def _status_class(status: str) -> str:
    return "status-ok" if status == "ok" else "status-fail"


def _contract_summary(
    hard: list[ContractResult],
    soft: list[ContractResult],
) -> str:
    parts: list[str] = []
    for severity, contracts in (("hard", hard), ("soft", soft)):
        if not contracts:
            parts.append(f"{severity}: none")
            continue
        failed = [contract for contract in contracts if not contract.passed]
        css_class = "pass" if not failed else ("fail" if severity == "hard" else "soft-fail")
        label = "PASS" if not failed else "FAIL"
        title = "; ".join(f"{contract.contract_id}: {contract.reason}" for contract in failed)
        parts.append(
            f'<span>{severity}: <span class="{css_class}" title="{escape(title)}">'
            f"{label} ({len(contracts) - len(failed)}/{len(contracts)})</span></span>"
        )
        parts.extend(
            (
                f"<small><code>{escape(contract.contract_id)}</code>: "
                f"actual={escape(str(contract.actual))} "
                f"{escape(contract.comparator)} limit={escape(str(contract.limit))}</small>"
            )
            for contract in contracts
        )
    return "<br>".join(parts)


def _metrics_summary(metrics: tuple[Metric, ...]) -> str:
    rendered: list[str] = []
    ordered = sorted(
        enumerate(metrics),
        key=lambda item: (_metric_summary_priority(item[1]), item[0]),
    )
    for _, metric in ordered[:8]:
        if metric.distribution is not None:
            value = metric.distribution.median
            label = "median"
        else:
            value = metric.value
            label = "value"
        rendered.append(
            f"<code>{escape(metric.name)}</code> "
            f"{label}={escape(str(value))} {escape(metric.unit)} "
            f"<small>[{escape(metric.phase)}/{escape(metric.scope)}]</small>"
        )
    if len(metrics) > 8:
        rendered.append(f"<small>+{len(metrics) - 8} metrics</small>")
    return "<br>".join(rendered)


def _metric_summary_priority(metric: Metric) -> int:
    normalized = metric.name.lower().replace("_", ".")
    priorities = (
        "input.to.present",
        "fresh.ratio",
        "revision.lag",
        "changed.frame.total",
    )
    for priority, marker in enumerate(priorities):
        if marker in normalized:
            return priority
    return len(priorities)


def _scaling_rows(runs: tuple[BenchmarkRun, ...]) -> str:
    rows: list[str] = []
    for run in runs:
        for result in run.cases:
            if "scaling" not in result.spec.tags:
                continue
            parameters = case_result_to_dict(result)["spec"]["parameters"]
            median_ms = "" if result.stats is None else _milliseconds(result.stats.median_ns)
            rows.append(
                "<tr>"
                f"<td>{escape(run.meta.run_id)}</td>"
                f"<td><code>{escape(result.spec.case_id)}</code></td>"
                f"<td><code>{escape(json.dumps(parameters, sort_keys=True))}</code></td>"
                f"<td>{median_ms}</td>"
                "</tr>"
            )
    return "\n".join(rows) or '<tr><td colspan="4">scaling case はありません。</td></tr>'


def _warnings_json(loaded: LoadedRuns) -> str:
    return (
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "valid_runs": len(loaded.runs),
                "warning_count": len(loaded.warnings),
                "warnings": list(loaded.warnings),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _load_chart_module() -> _ChartModule:
    try:
        module = importlib.import_module("grafix.devtools.benchmarks.report_charts")
    except ModuleNotFoundError as exc:
        if exc.name in {"altair", "vl_convert"}:
            raise BenchmarkReportDependencyError(
                "benchmark report requires the 'benchmark-report' extra: "
                "pip install -e '.[benchmark-report]'"
            ) from exc
        raise
    return cast(_ChartModule, module)


__all__ = [
    "BenchmarkReportDependencyError",
    "LoadedRuns",
    "ReportArtifacts",
    "load_runs",
    "render_report_html",
    "write_report",
]
