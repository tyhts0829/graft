"""Altair/Vega-Lite による benchmark report chart と offline artifact。"""

from __future__ import annotations

import json
from dataclasses import asdict
from functools import cache
from pathlib import Path
from typing import Any

import altair as alt
import vl_convert as vlc

from grafix.devtools.benchmarks.report_model import (
    DeltaView,
    ReportViewModel,
    TimingView,
)

_CHART_WIDTH = 920
_POSITIVE = "#b94855"
_NEGATIVE = "#168267"
_NEUTRAL = "#788394"
_BLUE = "#315d8c"
_INK = "#172033"
_GRID = "#dbe2ec"
_MAX_ROWS = 20
_REPORT_LABEL_LIMIT = 190
_STATIC_LABEL_LIMIT = 390


def build_chart_specs(model: ReportViewModel) -> dict[str, dict[str, Any]]:
    """interactive report に埋め込む検証済み Vega-Lite spec を返す。"""

    charts = {
        "regression-chart": build_regression_chart(model),
        "history-chart": build_history_chart(model),
        "timing-chart": build_timing_chart(model),
        "guardrail-chart": build_guardrail_chart(model),
    }
    return {element_id: chart.to_dict(validate=True) for element_id, chart in charts.items()}


def build_regression_chart(
    model: ReportViewModel,
    *,
    interactive: bool = True,
    themed: bool = True,
) -> alt.TopLevelMixin:
    """最新 run と baseline の差分を 0 中心の横棒で返す。"""

    if not model.deltas:
        message = model.baseline_message
        if model.baseline_run_id is not None:
            message += " No comparable measured case has a positive baseline median."
        return _message_chart(
            "Regression / improvement",
            message,
            responsive=interactive,
            themed=themed,
        )

    records, groups = _delta_records(model.deltas, grouped=interactive)
    source: alt.Chart = alt.Chart(alt.InlineData(values=records))
    params: list[alt.Parameter] = []
    if interactive:
        category = alt.param(
            name="regression_category",
            value="All",
            bind=alt.binding_select(options=groups, name="Category: "),
        )
        source = source.transform_filter(alt.datum.filter_group == category)
        params.append(category)

    bars = source.mark_bar(cornerRadiusEnd=3).encode(
        x=alt.X(
            "delta_fraction:Q",
            title="Change (head / base - 1)",
            axis=alt.Axis(format="+.1%", gridColor=_GRID),
        ),
        y=alt.Y(
            "case_id:N",
            title=None,
            sort=alt.EncodingSortField(field="rank", op="min", order="ascending"),
            axis=alt.Axis(labelLimit=(_REPORT_LABEL_LIMIT if interactive else _STATIC_LABEL_LIMIT)),
        ),
        color=alt.Color(
            "direction:N",
            scale=alt.Scale(
                domain=["regression", "unchanged", "improvement"],
                range=[_POSITIVE, _NEUTRAL, _NEGATIVE],
            ),
            legend=None,
        ),
        tooltip=[
            alt.Tooltip("case_id:N", title="Case"),
            alt.Tooltip("category:N", title="Category"),
            alt.Tooltip("base_run_id:N", title="Base run"),
            alt.Tooltip("head_run_id:N", title="Head run"),
            alt.Tooltip("base_median_ms:Q", title="Base median", format=".6f"),
            alt.Tooltip("head_median_ms:Q", title="Head median", format=".6f"),
            alt.Tooltip("base_mad_ms:Q", title="Base MAD", format=".6f"),
            alt.Tooltip("head_mad_ms:Q", title="Head MAD", format=".6f"),
            alt.Tooltip("delta_fraction:Q", title="Change", format="+.2%"),
        ],
    )
    zero = (
        alt.Chart(alt.InlineData(values=[{"zero": 0.0}]))
        .mark_rule(color="#687385", strokeWidth=1)
        .encode(x="zero:Q")
    )
    chart = (bars + zero).properties(
        title={
            "text": "Regression / improvement",
            "subtitle": [
                model.baseline_message,
                "Positive is slower; negative is faster. This is an observation, not significance.",
            ],
        },
        width="container" if interactive else _CHART_WIDTH,
        height=max(160, min(520, 23 * len(records))),
    )
    if params:
        chart = chart.add_params(*params)
    return _theme(chart) if themed else chart


def build_history_chart(model: ReportViewModel) -> alt.TopLevelMixin:
    """case selector 付きの median/MAD 時系列を返す。"""

    if not model.history:
        return _message_chart("Case history", "No compatible measured history is available.")

    records = [asdict(row) for row in model.history]
    case_ids = sorted({row.case_id for row in model.history})
    absolute_counts = {
        case_id: sum(
            row.case_id == case_id and row.view_mode == "absolute" for row in model.history
        )
        for case_id in case_ids
    }
    default_case = min(case_ids, key=lambda case_id: (-absolute_counts[case_id], case_id))
    case_selector = alt.param(
        name="history_case",
        value=default_case,
        bind=alt.binding_select(options=case_ids, name="Case: "),
    )
    mode_selector = alt.param(
        name="history_mode",
        value="absolute",
        bind=alt.binding_radio(
            options=["absolute", "relative"],
            labels=["Absolute ms", "From first positive"],
            name="Mode: ",
        ),
    )
    source = (
        alt.Chart(alt.InlineData(values=records))
        .transform_filter(alt.datum.case_id == case_selector)
        .transform_filter(alt.datum.view_mode == mode_selector)
    )
    x = alt.X("created_at:T", title=None, axis=alt.Axis(gridColor=_GRID))
    detail = [alt.Detail("case_id:N"), alt.Detail("segment_id:N")]
    band = source.mark_area(opacity=0.18, color="#6f91bc").encode(
        x=x,
        y=alt.Y("low:Q", title="Value (ms or %)", axis=alt.Axis(gridColor=_GRID)),
        y2=alt.Y2("high:Q"),
        detail=detail,
        order=alt.Order("run_index:Q"),
    )
    line = source.mark_line(strokeWidth=2.2, color=_BLUE).encode(
        x=x,
        y=alt.Y("value:Q", title="Value (ms or %)", axis=alt.Axis(gridColor=_GRID)),
        detail=detail,
        order=alt.Order("run_index:Q"),
    )
    points = source.mark_point(filled=True, size=55, color=_BLUE).encode(
        x=x,
        y="value:Q",
        tooltip=[
            alt.Tooltip("run_id:N", title="Run"),
            alt.Tooltip("case_id:N", title="Case"),
            alt.Tooltip("value_label:N", title="Median"),
            alt.Tooltip("mad_label:N", title="MAD"),
        ],
    )
    return _theme(
        alt.layer(band, line, points)
        .add_params(case_selector, mode_selector)
        .properties(
            title={
                "text": "Case history",
                "subtitle": (
                    "Lines stop at missing, failed, or incompatible runs. "
                    "Relative view starts at the first positive median."
                ),
            },
            width="container",
            height=300,
        )
    )


def build_timing_chart(
    model: ReportViewModel,
    *,
    interactive: bool = True,
    themed: bool = True,
) -> alt.TopLevelMixin:
    """最新 run の timing overview を対数軸で返す。"""

    if not model.timings:
        return _message_chart(
            "Latest timing overview",
            "No measured timing is available.",
            responsive=interactive,
            themed=themed,
        )

    records, groups = _timing_records(model.timings, grouped=interactive)
    source: alt.Chart = alt.Chart(alt.InlineData(values=records))
    params: list[alt.Parameter] = []
    if interactive:
        category = alt.param(
            name="timing_category",
            value="All",
            bind=alt.binding_select(options=groups, name="Category: "),
        )
        source = source.transform_filter(alt.datum.filter_group == category)
        params.append(category)
    bars = source.mark_bar(cornerRadiusEnd=3).encode(
        x=alt.X(
            "plot_median_ms:Q",
            title="Median per iteration (ms, log scale)",
            scale=alt.Scale(type="log"),
            axis=alt.Axis(gridColor=_GRID),
        ),
        x2=alt.X2("plot_floor_ms:Q"),
        y=alt.Y(
            "case_id:N",
            title=None,
            sort=alt.EncodingSortField(field="rank", op="min", order="ascending"),
            axis=alt.Axis(labelLimit=(_REPORT_LABEL_LIMIT if interactive else _STATIC_LABEL_LIMIT)),
        ),
        color=alt.Color(
            "status:N",
            scale=alt.Scale(
                domain=["ok", "contract-failure"],
                range=[_BLUE, _POSITIVE],
            ),
            legend=alt.Legend(title="Status", orient="bottom"),
        ),
        opacity=alt.condition("datum.self_sampling", alt.value(0.62), alt.value(1.0)),
        tooltip=[
            alt.Tooltip("case_id:N", title="Case"),
            alt.Tooltip("category:N", title="Category"),
            alt.Tooltip("status:N", title="Status"),
            alt.Tooltip("self_sampling:N", title="Self sampling"),
            alt.Tooltip("median_ms:Q", title="Median ms", format=".6f"),
            alt.Tooltip("mad_ms:Q", title="MAD ms", format=".6f"),
            alt.Tooltip("p95_ms:Q", title="p95 ms", format=".6f"),
            alt.Tooltip("p99_ms:Q", title="p99 ms", format=".6f"),
        ],
    )
    chart = bars.properties(
        title={
            "text": "Latest timing overview",
            "subtitle": (
                "Absolute times from different workloads do not directly define optimization priority."
            ),
        },
        width="container" if interactive else _CHART_WIDTH,
        height=max(180, min(540, 23 * len(records))),
    )
    if params:
        chart = chart.add_params(*params)
    return _theme(chart) if themed else chart


def build_guardrail_chart(
    model: ReportViewModel,
    *,
    interactive: bool = True,
    themed: bool = True,
) -> alt.TopLevelMixin:
    """directional soft contract を閾値 1 の bullet chart で返す。"""

    if not model.guardrails:
        return _message_chart(
            "Soft contract guardrails",
            "No numeric directional soft contract is available.",
            responsive=interactive,
            themed=themed,
        )
    rows = list(model.guardrails[:_MAX_ROWS])
    maximum = max(1.15, max(row.load_ratio for row in rows) * 1.08)
    records = [
        {
            **asdict(row),
            "rank": index,
            "capacity": maximum,
            "guardrail_label": f"{row.case_id} · {row.contract_id}",
            "result": "pass" if row.passed else "fail",
        }
        for index, row in enumerate(rows)
    ]
    source = alt.Chart(alt.InlineData(values=records))
    background = source.mark_bar(color="#e6ebf2", cornerRadiusEnd=3).encode(
        x=alt.X("capacity:Q", title="Guardrail load (1.0 = limit)"),
        y=alt.Y(
            "guardrail_label:N",
            title=None,
            sort=alt.EncodingSortField(field="rank", op="min", order="ascending"),
            axis=alt.Axis(labelLimit=(_REPORT_LABEL_LIMIT if interactive else _STATIC_LABEL_LIMIT)),
        ),
    )
    bars = source.mark_bar(cornerRadiusEnd=3).encode(
        x=alt.X("load_ratio:Q", title="Guardrail load (1.0 = limit)"),
        y=alt.Y(
            "guardrail_label:N",
            title=None,
            sort=alt.EncodingSortField(field="rank", op="min", order="ascending"),
        ),
        color=alt.Color(
            "result:N",
            scale=alt.Scale(domain=["pass", "fail"], range=[_NEGATIVE, _POSITIVE]),
            legend=None,
        ),
        tooltip=[
            alt.Tooltip("case_id:N", title="Case"),
            alt.Tooltip("contract_id:N", title="Contract"),
            alt.Tooltip("direction:N", title="Direction"),
            alt.Tooltip("actual:Q", title="Actual", format=".6g"),
            alt.Tooltip("comparator:N", title="Comparator"),
            alt.Tooltip("limit:Q", title="Limit", format=".6g"),
            alt.Tooltip("load_ratio:Q", title="Load", format=".3f"),
            alt.Tooltip("reason:N", title="Reason"),
        ],
    )
    threshold = (
        alt.Chart(alt.InlineData(values=[{"limit": 1.0}]))
        .mark_rule(color="#6c3740", strokeDash=[5, 3], strokeWidth=2)
        .encode(x="limit:Q")
    )
    chart = (background + bars + threshold).properties(
        title={
            "text": "Soft contract guardrails",
            "subtitle": "Values at or beyond 1.0 exhaust the directional limit.",
        },
        width="container" if interactive else _CHART_WIDTH,
        height=max(140, min(480, 25 * len(records))),
    )
    return _theme(chart) if themed else chart


def build_overview_chart(model: ReportViewModel) -> alt.TopLevelMixin:
    """browser runtime 不要の overview.svg 用 chart を返す。"""

    summary = _summary_chart(model, themed=False)
    performance = (
        build_regression_chart(model, interactive=False, themed=False)
        if model.deltas
        else build_timing_chart(model, interactive=False, themed=False)
    )
    charts: list[Any] = [summary, performance]
    if model.guardrails:
        charts.append(build_guardrail_chart(model, interactive=False, themed=False))
    overview = (
        alt.vconcat(*charts, spacing=30)
        .resolve_scale(color="independent")
        .properties(background="#ffffff")
    )
    return _theme(overview)


def render_overview_svg(model: ReportViewModel) -> str:
    """同梱 font を登録し、overview chart を自己完結 SVG に変換する。"""

    _register_fonts()
    spec = build_overview_chart(model).to_dict(validate=True)
    return str(
        vlc.vegalite_to_svg(
            spec,
            vl_version=_vl_version(),
            allowed_base_urls=[],
        )
    )


@cache
def offline_vega_runtime() -> str:
    """Vega/Vega-Lite/Vega-Embed を含む offline JavaScript bundle を返す。"""

    return str(
        vlc.javascript_bundle(
            ("window.vegaEmbed=vegaEmbed;window.vegaLite=vegaLite;window.vega=vega;"),
            vl_version=_vl_version(),
        )
    )


def script_json(value: object) -> str:
    """inline script element を閉じられない JSON text を返す。"""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _delta_records(
    rows: tuple[DeltaView, ...],
    *,
    grouped: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    groups = ["All", *sorted({row.category for row in rows})] if grouped else ["All"]
    records: list[dict[str, Any]] = []
    for group in groups:
        candidates = list(
            rows if group == "All" else (row for row in rows if row.category == group)
        )
        selected = _largest_changes(candidates)
        for rank, row in enumerate(selected):
            records.append({**asdict(row), "filter_group": group, "rank": rank})
    return records, groups


def _largest_changes(rows: list[DeltaView]) -> list[DeltaView]:
    regressions = sorted(
        (row for row in rows if row.delta_fraction > 0.0),
        key=lambda row: (-row.delta_fraction, row.case_id),
    )[: _MAX_ROWS // 2]
    improvements = sorted(
        (row for row in rows if row.delta_fraction < 0.0),
        key=lambda row: (row.delta_fraction, row.case_id),
    )[: _MAX_ROWS // 2]
    selected = regressions + improvements
    if not selected:
        selected = sorted(rows, key=lambda row: row.case_id)[:_MAX_ROWS]
    return sorted(selected, key=lambda row: (-row.delta_fraction, row.case_id))


def _timing_records(
    rows: tuple[TimingView, ...],
    *,
    grouped: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    positive = [row.median_ms for row in rows if row.median_ms > 0.0]
    zero_floor = min(positive) / 10.0 if positive else 1e-9
    groups = ["All", *sorted({row.category for row in rows})] if grouped else ["All"]
    records: list[dict[str, Any]] = []
    for group in groups:
        candidates = [row for row in rows if group == "All" or row.category == group][:_MAX_ROWS]
        for rank, row in enumerate(candidates):
            records.append(
                {
                    **asdict(row),
                    "filter_group": group,
                    "rank": rank,
                    "plot_median_ms": max(row.median_ms, zero_floor),
                    "plot_floor_ms": zero_floor,
                }
            )
    return records, groups


def _summary_chart(
    model: ReportViewModel,
    *,
    themed: bool = True,
) -> alt.TopLevelMixin:
    summary = model.summary
    if summary is None:
        lines = ["Grafix benchmark", "No valid schema v4 run is available."]
    else:
        statuses = ", ".join(f"{status} {count}" for status, count in summary.status_counts)
        lines = [
            "Grafix benchmark overview",
            f"Latest: {summary.run_id}",
            f"Source: {summary.source[:12]}  |  {summary.suite} / {summary.profile} / {summary.mode}",
            f"Cases: {summary.case_count}  |  {statuses}",
            (
                f"Contracts: hard {summary.hard_passed}/{summary.hard_total}, "
                f"soft {summary.soft_passed}/{summary.soft_total}  |  warnings {summary.warning_count}"
            ),
            model.baseline_message,
        ]
    records = [{"line": line, "row": index} for index, line in enumerate(lines)]
    chart = (
        alt.Chart(alt.InlineData(values=records))
        .mark_text(align="left", baseline="top", fontSize=15, color=_INK)
        .encode(
            x=alt.value(4),
            y=alt.Y("row:O", axis=None, sort="ascending"),
            text="line:N",
        )
        .properties(width=_CHART_WIDTH, height=max(90, len(lines) * 25))
    )
    return _theme(chart) if themed else chart


def _message_chart(
    title: str,
    message: str,
    *,
    responsive: bool = False,
    themed: bool = True,
) -> alt.TopLevelMixin:
    chart = (
        alt.Chart(alt.InlineData(values=[{"message": message}]))
        .mark_text(align="left", fontSize=14, color="#566274")
        .encode(x=alt.value(4), text="message:N")
        .properties(
            title=title,
            width="container" if responsive else _CHART_WIDTH,
            height=70,
        )
    )
    return _theme(chart) if themed else chart


def _theme(chart: alt.TopLevelMixin) -> alt.TopLevelMixin:
    return (
        chart.configure_axis(
            labelColor="#435067",
            titleColor="#435067",
            domainColor="#b9c3d1",
            tickColor="#b9c3d1",
            labelFont="Google Sans",
            titleFont="Google Sans",
        )
        .configure_title(
            color=_INK,
            font="Google Sans",
            fontSize=18,
            subtitleColor="#5d697c",
            subtitleFont="Google Sans",
            subtitleFontSize=12,
            anchor="start",
        )
        .configure_legend(
            labelFont="Google Sans",
            titleFont="Google Sans",
        )
        .configure_view(stroke=None)
    )


def _vl_version() -> str:
    return "_".join(str(alt.SCHEMA_VERSION).split(".")[:2])


@cache
def _register_fonts() -> None:
    package_root = Path(__file__).resolve().parents[2]
    for relative in (
        "resource/font/Google_Sans/static",
        "resource/font/Noto_Sans_JP/static",
    ):
        directory = package_root / relative
        if directory.is_dir():
            vlc.register_font_directory(str(directory))


__all__ = [
    "build_chart_specs",
    "build_guardrail_chart",
    "build_history_chart",
    "build_overview_chart",
    "build_regression_chart",
    "build_timing_chart",
    "offline_vega_runtime",
    "render_overview_svg",
    "script_json",
]
