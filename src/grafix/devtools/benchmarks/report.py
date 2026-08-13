"""
Purpose:
    蓄積した schema v4 run を互換 cohort ごとの履歴として読み、offline HTML/SVG と warning audit にまとめる。
Use when:
    benchmark history の load・cohort visualization・warning 分類・offline report artifact 生成を変更・調査する場合。
Constraints:
    - strict な base/head 判定は ``compare.py`` に任せ、report は互換性の異なる観測を1本の trend へ接続しない。
    - 壊れた JSON、unsupported schema、duplicate run ID、contract failure を黙って捨てず warning/audit artifact へ残す。
    - HTML は chart spec/runtime を inline 化し、表示時の CDN/network へ依存させない。
Side effects:
    ``runs/*.json`` と optional chart module を読み、HTML・SVG・warnings JSON を atomic に書き込む。
"""

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
    chronological_runs,
    latest_valid_run,
    parse_created_at_utc,
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


class BenchmarkReportGenerationError(RuntimeError):
    """chart spec または静的 artifact の生成失敗を表す。"""


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
    latest_warnings: tuple[str, ...] = ()
    historical_warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReportArtifacts:
    """benchmark report が生成した artifact と入力 run。"""

    report_path: Path
    overview_path: Path
    warnings_path: Path
    loaded: LoadedRuns
    latest_run_id: str | None
    history_warnings: tuple[str, ...]


def load_runs(runs_dir: str | Path) -> LoadedRuns:
    """全 JSON を読み、壊れた入力・重複 ID・contract 違反を warning にする。"""

    directory = Path(runs_dir)
    decoded_runs: list[tuple[Path, BenchmarkRun]] = []
    warning_entries: list[tuple[str | None, str]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            run = read_benchmark_run(path)
            decoded_runs.append((path, run))
        except BenchmarkSchemaError as exc:
            warning_entries.append((None, str(exc)))

    runs_by_id: dict[str, list[tuple[Path, BenchmarkRun]]] = {}
    for path, run in decoded_runs:
        runs_by_id.setdefault(run.meta.run_id, []).append((path, run))

    runs: list[BenchmarkRun] = []
    for run_id, copies in sorted(runs_by_id.items()):
        if len(copies) > 1:
            paths = ", ".join(str(path) for path, _ in copies)
            warning_entries.append(
                (
                    None,
                    f"duplicate benchmark run_id {run_id!r}; "
                    f"excluded all copies: {paths}",
                )
            )
            continue
        path, run = copies[0]
        runs.append(run)
        warning_entries.extend(
            (run.meta.run_id, f"{path}: {warning}")
            for warning in run.warnings
        )
        for result in run.cases:
            warning_entries.extend(
                (
                    run.meta.run_id,
                    (
                        f"{path}: {result.spec.case_id}: "
                        f"{contract.severity} contract failed: "
                        f"{contract.contract_id}: {contract.reason}"
                    ),
                )
                for contract in result.contracts
                if not contract.passed
            )

    dated_runs = chronological_runs(tuple(runs))
    invalid_timestamp_runs = tuple(
        sorted(
            (run for run in runs if parse_created_at_utc(run.meta.created_at) is None),
            key=lambda run: run.meta.run_id,
        )
    )
    ordered_runs = invalid_timestamp_runs + dated_runs
    latest = latest_valid_run(ordered_runs)
    latest_run_id = None if latest is None else latest.meta.run_id
    latest_warnings = tuple(
        message
        for run_id, message in warning_entries
        if latest_run_id is not None and run_id == latest_run_id
    )
    historical_warnings = tuple(
        message
        for run_id, message in warning_entries
        if latest_run_id is None or run_id != latest_run_id
    )
    return LoadedRuns(
        runs=ordered_runs,
        warnings=tuple(message for _, message in warning_entries),
        latest_warnings=latest_warnings,
        historical_warnings=historical_warnings,
    )


def write_report(out_root: str | Path) -> ReportArtifacts:
    """report.html、overview.svg、warnings.json を atomic に生成する。"""

    root = Path(out_root).expanduser().resolve()
    loaded = load_runs(root / "runs")
    model = _build_view_model(loaded)
    charts = _load_chart_module()
    try:
        chart_specs = charts.build_chart_specs(model)
        report_text = _render_report_html(
            loaded,
            model=model,
            chart_specs=chart_specs,
            runtime=charts.offline_vega_runtime(),
            specs_json=charts.script_json(chart_specs),
        )
        overview_text = charts.render_overview_svg(model)
    except (ValueError, RuntimeError) as exc:
        raise BenchmarkReportGenerationError(
            f"could not generate benchmark report charts: {exc}"
        ) from exc
    warnings_text = _warnings_json(
        loaded,
        model_history_warnings=model.history_warnings,
    )

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
        latest_run_id=model.latest_run_id,
        history_warnings=model.history_warnings,
    )


def render_report_html(loaded: LoadedRuns) -> str:
    """offline runtime と検証済み chart spec を含む自己完結 HTML を返す。"""

    model = _build_view_model(loaded)
    charts = _load_chart_module()
    try:
        chart_specs = charts.build_chart_specs(model)
        return _render_report_html(
            loaded,
            model=model,
            chart_specs=chart_specs,
            runtime=charts.offline_vega_runtime(),
            specs_json=charts.script_json(chart_specs),
        )
    except (ValueError, RuntimeError) as exc:
        raise BenchmarkReportGenerationError(
            f"could not generate benchmark report charts: {exc}"
        ) from exc


def _render_report_html(
    loaded: LoadedRuns,
    *,
    model: ReportViewModel,
    chart_specs: dict[str, dict[str, Any]],
    runtime: str,
    specs_json: str,
) -> str:
    latest_warnings, loaded_historical_warnings = _warning_groups(loaded)
    historical_warnings = loaded_historical_warnings + model.history_warnings
    table_body = _run_rows(loaded.runs)
    if not table_body:
        table_body = '<tr><td colspan="13">有効な schema v4 run がありません。</td></tr>'
    scaling_body = _scaling_rows(loaded.runs)
    chart_sections = _chart_sections(tuple(chart_specs), model=model)
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
    .scope {{ margin: 16px 0 0; padding: 11px 14px; border-left: 4px solid var(--blue); background: #edf4fb; }}
    .chart-panel {{ margin-top: 16px; padding: 20px; border: 1px solid #d8e0eb; border-radius: 16px; background: #fff; }}
    .history-controls {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: end; margin: 14px 0 6px; }}
    .history-controls label {{ display: grid; gap: 5px; color: var(--muted); font-size: 12px; font-weight: 650; }}
    .history-controls select {{ min-height: 36px; max-width: min(100%, 720px); padding: 6px 30px 6px 9px; color: var(--ink); background: #fff; border: 1px solid #bcc7d7; border-radius: 8px; }}
    .history-controls .case-control {{ flex: 1 1 420px; }}
    .history-controls .case-control select {{ width: 100%; }}
    .history-selection-note {{ flex-basis: 100%; margin: 2px 0 0; padding: 9px 12px; color: #8b2f38; background: #fff1f2; border-radius: 8px; }}
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
  <p class="scope">同じ case・環境・計測条件の観測だけを線で接続します。MAD は run 内 sample のばらつきであり、信頼区間ではありません。</p>
  {chart_sections}
  {_warning_section("Latest warnings", latest_warnings, section_id="latest-warnings")}
  {_warning_section(
        "Historical warnings",
        historical_warnings,
        section_id="historical-warnings",
    )}
  <details class="report-details">
    <summary>Detailed runs ({len(loaded.runs)} valid runs)</summary>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>run</th><th>source</th><th>case</th><th>category</th>
          <th>status</th><th>checksum</th><th>contracts</th>
          <th>median ms</th><th>MAD ms</th>
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
  const bindHistoryControls = view => {{
    const categorySelect = document.getElementById("history-category-control");
    const caseSelect = document.getElementById("history-case-control");
    const note = document.getElementById("history-selection-note");
    if (!categorySelect || !caseSelect || !note) return;
    const options = Array.from(caseSelect.options);
    const updateNote = () => {{
      const option = caseSelect.selectedOptions[0];
      const measured = option && option.dataset.measured === "true";
      note.hidden = measured;
      note.textContent = measured ? "" :
        "この case には時系列化できる timing observation がありません。下の run coverage で failure または欠測を確認してください。";
    }};
    const selectCase = () => {{
      updateNote();
      void view.signal("history_case", caseSelect.value).runAsync();
    }};
    const applyCategory = () => {{
      const category = categorySelect.value;
      for (const option of options) {{
        option.hidden = Boolean(category) && option.dataset.category !== category;
      }}
      if (caseSelect.selectedOptions[0]?.hidden) {{
        const firstVisible = options.find(option => !option.hidden);
        if (firstVisible) caseSelect.value = firstVisible.value;
      }}
      selectCase();
    }};
    const activeCase = view.signal("history_case");
    if (options.some(option => option.value === activeCase)) caseSelect.value = activeCase;
    updateNote();
    categorySelect.addEventListener("change", applyCategory);
    caseSelect.addEventListener("change", selectCase);
  }};
  for (const [elementId, spec] of Object.entries(specs)) {{
    const target = document.getElementById(elementId);
    window.vegaEmbed(target, spec, {{renderer: "svg", actions: false}})
      .then(result => {{
        if (elementId === "history-chart") bindHistoryControls(result.view);
      }})
      .catch(error => {{
        target.classList.add("chart-error");
        target.textContent = `Chart rendering failed: ${{error.message}}`;
      }});
  }}
}})();
</script>
</body>
</html>
"""


def _build_view_model(loaded: LoadedRuns) -> ReportViewModel:
    latest_warnings, historical_warnings = _warning_groups(loaded)
    return build_report_view_model(
        loaded.runs,
        latest_warning_count=len(latest_warnings),
        historical_warning_count=len(historical_warnings),
    )


def _summary_html(model: ReportViewModel) -> str:
    summary = model.summary
    history = model.history_summary
    if summary is None:
        period = _history_period(history.start_at, history.end_at)
        return f"""
  <header class="hero">
    <div class="eyebrow">schema v4 · performance history</div>
    <h1>Grafix benchmark history</h1>
    <p class="meta">{escape(period)} · No dated valid run is available.</p>
    <div class="cards">
      {_summary_card("Runs", str(history.run_count), f"{history.temporal_run_count} dated")}
      {_summary_card("Cases", str(history.unique_case_count), "across loaded history")}
      {_summary_card("Cohorts", str(history.cohort_count), "compatible series")}
      {_summary_card("Trend cohorts", str(history.trend_cohort_count), "2+ observations")}
      {_summary_card("Latest warnings", str(history.latest_warning_count), "current run")}
      {_summary_card("Historical warnings", str(history.historical_warning_count), "audit trail")}
    </div>
  </header>"""
    statuses = ", ".join(f"{escape(status)} {count}" for status, count in summary.status_counts)
    period = _history_period(history.start_at, history.end_at)
    return f"""
  <header class="hero">
    <div class="eyebrow">schema v4 · performance history</div>
    <h1>Grafix benchmark history</h1>
    <p class="meta">{escape(period)}</p>
    <p class="meta">latest <code>{escape(summary.run_id)}</code> · {escape(summary.created_at)} ·
      source <code>{escape(summary.source[:12])}</code> ·
      {escape(summary.suite)} / {escape(summary.profile)} / {escape(summary.mode)}</p>
    <div class="cards">
      {_summary_card("Runs", str(history.run_count), f"{history.temporal_run_count} dated")}
      {_summary_card("Cases", str(history.unique_case_count), "across loaded history")}
      {_summary_card("Cohorts", str(history.cohort_count), "compatible series")}
      {_summary_card("Trend cohorts", str(history.trend_cohort_count), "2+ observations")}
      {_summary_card("Latest cases", str(summary.case_count), statuses)}
      {_summary_card("Hard contracts", f"{summary.hard_passed}/{summary.hard_total}", "passed")}
      {_summary_card("Soft contracts", f"{summary.soft_passed}/{summary.soft_total}", "passed")}
      {_summary_card("Latest warnings", str(history.latest_warning_count), "current run")}
      {_summary_card("Historical warnings", str(history.historical_warning_count), "audit trail")}
    </div>
  </header>"""


def _history_period(start_at: str | None, end_at: str | None) -> str:
    if start_at is None or end_at is None:
        return "No UTC history range"
    if start_at == end_at:
        return start_at
    return f"{start_at} → {end_at}"


def _summary_card(label: str, value: str, detail: str) -> str:
    return (
        '<div class="card">'
        f"<span>{escape(label)}</span><strong>{escape(value)}</strong>"
        f"<span>{escape(detail)}</span></div>"
    )


def _warning_groups(loaded: LoadedRuns) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if loaded.latest_warnings or loaded.historical_warnings or not loaded.warnings:
        return loaded.latest_warnings, loaded.historical_warnings
    # 手組みの LoadedRuns では run との対応が不明なため historical として扱う。
    return (), loaded.warnings


def _warning_section(
    title: str,
    warnings: tuple[str, ...],
    *,
    section_id: str,
) -> str:
    items = "".join(f"<li>{escape(warning)}</li>" for warning in warnings) or "<li>none</li>"
    css_class = "warnings" if warnings else "warnings warnings-clear"
    return (
        f'<section id="{escape(section_id)}">'
        f"<h2>{escape(title)}</h2>"
        f'<div class="{css_class}"><ul>{items}</ul></div>'
        "</section>"
    )


def _chart_sections(
    element_ids: tuple[str, ...],
    *,
    model: ReportViewModel,
) -> str:
    labels = {
        "history-chart": (
            "Performance history",
            "選択した case の median、MAD whisker、run coverage を UTC の時系列で表示します。",
        ),
        "case-overview-chart": (
            "Case trend overview",
            "case ごとに独立した縦軸で current cohort の形を俯瞰します。",
        ),
        "guardrail-chart": (
            "Guardrail history",
            "同じ contract 定義の actual / limit を追跡します。1.0 が閾値で、pass 側は comparator に依存します。",
        ),
    }
    sections: list[str] = []
    for element_id in element_ids:
        title, description = labels.get(element_id, (element_id, ""))
        controls = _history_controls(model) if element_id == "history-chart" else ""
        sections.append(
            '<section class="chart-panel">'
            f"<h2>{escape(title)}</h2>"
            f"<p>{escape(description)}</p>"
            f"{controls}"
            f'<div class="chart" id="{escape(element_id)}"></div>'
            "</section>"
        )
    return "\n".join(sections)


def _history_controls(model: ReportViewModel) -> str:
    if not model.cases:
        return ""
    measured_case_ids = {point.case_id for point in model.history}
    categories = sorted({case.category for case in model.cases})
    category_options = ['<option value="">All</option>'] + [
        f'<option value="{escape(category, quote=True)}">{escape(category)}</option>'
        for category in categories
    ]
    case_options = [
        (
            f'<option value="{escape(case.case_id, quote=True)}" '
            f'data-category="{escape(case.category, quote=True)}" '
            f'data-measured="{str(case.case_id in measured_case_ids).lower()}">'
            f"{escape(case.selector_label)}</option>"
        )
        for case in model.cases
    ]
    return (
        '<div class="history-controls">'
        '<label>Category<select id="history-category-control">'
        f"{''.join(category_options)}</select></label>"
        '<label class="case-control">Case<select id="history-case-control">'
        f"{''.join(case_options)}</select></label>"
        '<p class="history-selection-note" id="history-selection-note" '
        'role="status" aria-live="polite" hidden></p>'
        "</div>"
    )


def _run_rows(runs: tuple[BenchmarkRun, ...]) -> str:
    rows: list[str] = []
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
                f"<td>{escape(run.meta.run_id)}<br><small>{escape(run.meta.created_at)}</small></td>"
                f"<td>{escape(source[:12])}</td>"
                f"<td><code>{escape(result.spec.case_id)}</code><br>"
                f"<small>{escape(result.spec.label)}</small></td>"
                f"<td>{escape(result.spec.category)}</td>"
                f'<td class="{_status_class(result.status)}">{escape(result.status)}</td>'
                f"<td>{checksum_html}</td>"
                f"<td>{_contract_summary(hard_contracts, soft_contracts)}</td>"
                f"<td>{median_ms}</td><td>{mad_ms}</td>"
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


def _warnings_json(
    loaded: LoadedRuns,
    *,
    model_history_warnings: tuple[str, ...],
) -> str:
    latest_warnings, loaded_historical_warnings = _warning_groups(loaded)
    historical_warnings = loaded_historical_warnings + model_history_warnings
    warnings = latest_warnings + historical_warnings
    return (
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "valid_runs": len(loaded.runs),
                "warning_count": len(warnings),
                "warnings": list(warnings),
                "latest_warning_count": len(latest_warnings),
                "latest_warnings": list(latest_warnings),
                "historical_warning_count": len(historical_warnings),
                "historical_warnings": list(historical_warnings),
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
    "BenchmarkReportGenerationError",
    "LoadedRuns",
    "ReportArtifacts",
    "load_runs",
    "render_report_html",
    "write_report",
]
