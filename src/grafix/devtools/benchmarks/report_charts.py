"""Altair/Vega-Lite による履歴中心の benchmark report chart。"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from functools import cache
from pathlib import Path
from typing import Any, cast

import altair as alt
import vl_convert as vlc

from grafix.devtools.benchmarks.report_model import ReportViewModel

_CHART_WIDTH = 920
_BLUE = "#315d8c"
_YELLOW = "#c49332"
_ORANGE = "#c06f2c"
_RED = "#b94855"
_GRAY = "#99a3b2"
_INK = "#172033"
_GRID = "#dbe2ec"
_REPORT_LABEL_LIMIT = 190
_STATIC_LABEL_LIMIT = 390
_OVERVIEW_CASE_LIMIT = 12
_STATIC_CASE_LIMIT = 6
_COVERAGE_KEY_SEPARATOR = "\u241f"


def build_chart_specs(model: ReportViewModel) -> dict[str, dict[str, Any]]:
    """interactive report に埋め込む検証済み Vega-Lite spec を返す。"""

    charts = {
        "history-chart": build_history_chart(model),
        "case-overview-chart": build_case_overview_chart(model),
        "guardrail-chart": build_guardrail_chart(model),
    }
    return {element_id: chart.to_dict(validate=True) for element_id, chart in charts.items()}


def build_history_chart(model: ReportViewModel) -> alt.TopLevelMixin:
    """case selector を共有する性能履歴と run coverage を返す。"""

    cases = _case_options(model)
    if not cases:
        return _message_chart(
            "Performance history",
            "No measured schema v4 history is available.",
            responsive=True,
        )

    default_case = _default_case_id(model, cases)
    case_selector = _case_selector(cases, default_case, bound=False)
    performance = _performance_history_panel(model, case_selector, responsive=True)
    coverage = _coverage_panel(model, case_selector, responsive=True)
    chart = (
        alt.vconcat(
            cast(Any, performance),
            cast(Any, coverage),
            spacing=14,
            params=[case_selector.param],
        )
        .resolve_scale(x="shared", color="independent")
        .properties(background="#ffffff")
    )
    return _theme(chart)


def build_coverage_chart(model: ReportViewModel) -> alt.TopLevelMixin:
    """単独表示できる case selector 付き run coverage strip を返す。"""

    cases = _case_options(model)
    if not cases:
        return _message_chart(
            "Run coverage",
            "No run coverage is available.",
            responsive=True,
        )
    case_selector = _case_selector(cases, _default_case_id(model, cases))
    coverage = cast(Any, _coverage_panel(model, case_selector, responsive=True))
    return _theme(coverage.add_params(case_selector))


def build_case_overview_chart(
    model: ReportViewModel,
    *,
    interactive: bool = True,
    themed: bool = True,
    max_cases: int | None = None,
) -> alt.TopLevelMixin:
    """current cohort を case ごとの独立 y 軸で並べる。"""

    limit = max_cases or (_OVERVIEW_CASE_LIMIT if interactive else _STATIC_CASE_LIMIT)
    records, groups, shown, total = _overview_records(
        model,
        grouped=interactive,
        limit=limit,
    )
    if not records:
        return _message_chart(
            "Case trend overview",
            "No current compatible performance observations are available.",
            responsive=interactive,
            themed=themed,
        )

    source: alt.Chart = alt.Chart(alt.InlineData(values=records))
    params: list[alt.Parameter] = []
    if interactive:
        category = alt.param(
            name="overview_category",
            value="All",
            bind=alt.binding_select(options=groups, name="Category: "),
        )
        params.append(category)

    x = alt.X(
        "created_at:T",
        title="UTC",
        scale=alt.Scale(type="utc"),
        axis=alt.Axis(gridColor=_GRID, labelOverlap=True),
    )
    y = alt.Y(
        "median_ms:Q",
        title="Median (ms)",
        scale=alt.Scale(zero=False),
        axis=alt.Axis(gridColor=_GRID, tickCount=4),
    )
    whiskers = source.mark_rule(color="#7891af", opacity=0.72).encode(
        x=x,
        y=alt.Y("mad_low_ms:Q", title="Median (ms)", scale=alt.Scale(zero=False)),
        y2=alt.Y2("mad_high_ms:Q"),
    )
    line = source.transform_filter(alt.datum.status == "ok").mark_line(
        color=_BLUE,
        strokeWidth=1.8,
    ).encode(
        x=x,
        y=y,
        detail=[alt.Detail("cohort_id:N"), alt.Detail("segment_id:N")],
        order=alt.Order("run_index:Q"),
    )
    points = source.mark_point(filled=True, size=42).encode(
        x=x,
        y=y,
        color=alt.Color(
            "point_state:N",
            scale=alt.Scale(
                domain=["observation", "failure"],
                range=[_BLUE, _RED],
            ),
            legend=None,
        ),
        tooltip=_history_tooltips(),
    )
    panels = (
        alt.layer(whiskers, line, points)
        .transform_lookup(
            lookup="cohort_id",
            from_=alt.LookupData(
                data=alt.InlineData(values=_cohort_records(model)),
                key="cohort_id",
                fields=["cohort_label", "mode", "measurement_label"],
            ),
        )
        .properties(
            width=280 if interactive else 430,
            height=125 if interactive else 135,
        )
        .facet(
            facet=alt.Facet(
                "panel_label:N",
                title=None,
                sort=alt.EncodingSortField(field="rank", op="min", order="ascending"),
                header=alt.Header(
                    labelAlign="left",
                    labelAnchor="start",
                    labelLimit=(_REPORT_LABEL_LIMIT if interactive else _STATIC_LABEL_LIMIT),
                    labelFont="Google Sans",
                    labelFontSize=12,
                ),
            ),
            columns=3 if interactive else 2,
        )
        .resolve_scale(y="independent")
        .properties(
            title={
                "text": "Case trend overview",
                "subtitle": (
                    f"{shown} / {total} cases shown · current cohorts ranked by compatible observations. "
                    "Each panel has its own y scale."
                ),
            }
        )
    )
    if params:
        panels = (
            panels.transform_calculate(
                rank=(
                    "overview_category === 'All' ? "
                    "datum.global_rank : datum.category_rank"
                )
            )
            .transform_filter(
                "(overview_category === 'All' && datum.in_global) || "
                "(datum.category === overview_category && datum.in_category)"
            )
            .add_params(*params)
        )
    return _theme(panels) if themed else panels


def _static_case_overview_chart(model: ReportViewModel) -> alt.TopLevelMixin:
    """facet の clip に依存せず、ラベル付きの静的 small multiples を返す。"""

    records, _groups, shown, total = _overview_records(
        model,
        grouped=False,
        limit=_STATIC_CASE_LIMIT,
    )
    if not records:
        return _message_chart(
            "Case trend overview",
            "No current compatible performance observations are available.",
            themed=False,
        )

    rows_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        rows_by_case[str(record["case_id"])].append(record)
    ordered_cases = sorted(
        rows_by_case,
        key=lambda case_id: (
            min(int(row["global_rank"]) for row in rows_by_case[case_id]),
            case_id,
        ),
    )
    cohort_records = _cohort_records(model)
    panels: list[alt.TopLevelMixin] = []
    for case_id in ordered_cases:
        case_rows = rows_by_case[case_id]
        source = alt.Chart(alt.InlineData(values=case_rows))
        x = alt.X(
            "created_at:T",
            title="UTC",
            scale=alt.Scale(type="utc"),
            axis=alt.Axis(gridColor=_GRID, labelOverlap=True),
        )
        y = alt.Y(
            "median_ms:Q",
            title="Median (ms)",
            scale=alt.Scale(zero=False, padding=22),
            axis=alt.Axis(gridColor=_GRID, tickCount=4),
        )
        whiskers = source.mark_rule(color="#7891af", opacity=0.72).encode(
            x=x,
            y=alt.Y("mad_low_ms:Q", title="Median (ms)", scale=alt.Scale(zero=False)),
            y2=alt.Y2("mad_high_ms:Q"),
        )
        line = source.transform_filter(alt.datum.status == "ok").mark_line(
            color=_BLUE,
            strokeWidth=1.8,
        ).encode(
            x=x,
            y=y,
            detail=[alt.Detail("cohort_id:N"), alt.Detail("segment_id:N")],
            order=alt.Order("run_index:Q"),
        )
        points = source.mark_point(filled=True, size=42, color=_BLUE).encode(
            x=x,
            y=y,
            tooltip=_history_tooltips(),
        )
        panel_label = (
            source.transform_aggregate(panel_label="max(panel_label)")
            .mark_text(
                align="left",
                baseline="top",
                color=_INK,
                font="Google Sans",
                fontSize=12,
                fontWeight="bold",
                limit=_STATIC_LABEL_LIMIT,
            )
            .encode(
                x=alt.value(4),
                y=alt.value(4),
                text=alt.Text("panel_label:N"),
            )
        )
        panel = (
            alt.layer(whiskers, line, points, panel_label)
            .transform_lookup(
                lookup="cohort_id",
                from_=alt.LookupData(
                    data=alt.InlineData(values=cohort_records),
                    key="cohort_id",
                    fields=["cohort_label", "mode", "measurement_label"],
                ),
            )
            .properties(
                width=430,
                height=155,
            )
        )
        panels.append(panel)

    chart_rows = [
        alt.hconcat(
            *(cast(Any, panel) for panel in panels[index : index + 2]),
            spacing=38,
        ).resolve_scale(y="independent")
        for index in range(0, len(panels), 2)
    ]
    heading = (
        alt.Chart(
            alt.InlineData(
                values=[
                    {"line": "Case trend overview", "row": 0, "size": 18},
                    {
                        "line": (
                            f"{shown} / {total} cases shown · current cohorts ranked by "
                            "compatible observations · independent y scales"
                        ),
                        "row": 1,
                        "size": 12,
                    },
                ]
            )
        )
        .mark_text(align="left", baseline="top", color=_INK)
        .encode(
            x=alt.value(4),
            y=alt.Y("row:O", axis=None, sort="ascending"),
            text="line:N",
            size=alt.Size("size:Q", legend=None, scale=None),
        )
        .properties(width=_CHART_WIDTH, height=54)
    )
    return alt.vconcat(
        cast(Any, heading),
        *(cast(Any, row) for row in chart_rows),
        spacing=32,
    ).resolve_scale(y="independent")


def build_guardrail_chart(
    model: ReportViewModel,
    *,
    interactive: bool = True,
    themed: bool = True,
) -> alt.TopLevelMixin:
    """同じ contract 定義ごとの actual / limit 推移を返す。"""

    records = _guardrail_records(model)
    if not records:
        return _message_chart(
            "Guardrail history",
            "No numeric directional soft-contract history is available.",
            responsive=interactive,
            themed=themed,
        )

    series = sorted({(row["guardrail_key"], row["guardrail_label"]) for row in records})
    latest_keys = {
        row["guardrail_key"] for row in records if row["is_latest"]
    }
    counts = Counter(row["guardrail_key"] for row in records)
    default_key = min(
        (key for key, _label in series),
        key=lambda key: (key not in latest_keys, -counts[key], key),
    )
    selector = alt.param(
        name="guardrail_series",
        value=default_key,
        bind=alt.binding_select(
            options=[key for key, _label in series],
            labels=[label for _key, label in series],
            name="Guardrail: ",
        ),
    )
    source = alt.Chart(alt.InlineData(values=records)).transform_filter(
        alt.datum.guardrail_key == selector
    )
    x = alt.X(
        "created_at:T",
        title="UTC",
        scale=alt.Scale(type="utc"),
        axis=alt.Axis(gridColor=_GRID),
    )
    y = alt.Y(
        "load_ratio:Q",
        title="Actual / limit (1.0 = threshold)",
        scale=alt.Scale(zero=False),
        axis=alt.Axis(gridColor=_GRID),
    )
    line = source.mark_line(strokeWidth=2).encode(
        x=x,
        y=y,
        detail=[alt.Detail("cohort_id:N"), alt.Detail("segment_id:N")],
        order=alt.Order("run_index:Q"),
        color=alt.Color(
            "cohort_state:N",
            scale=alt.Scale(domain=["current", "past"], range=[_BLUE, _GRAY]),
            legend=alt.Legend(title="Contract definition", orient="bottom"),
        ),
    )
    points = source.mark_point(filled=True, size=62).encode(
        x=x,
        y=y,
        color=alt.Color(
            "result:N",
            scale=alt.Scale(domain=["pass", "fail"], range=[_BLUE, _RED]),
            legend=alt.Legend(title="Result", orient="bottom"),
        ),
        tooltip=[
            alt.Tooltip("created_at:N", title="UTC"),
            alt.Tooltip("run_id:N", title="Run"),
            alt.Tooltip("case_id:N", title="Case"),
            alt.Tooltip("contract_id:N", title="Contract"),
            alt.Tooltip("cohort_label:N", title="Definition"),
            alt.Tooltip("actual:Q", title="Actual", format=".6g"),
            alt.Tooltip("limit:Q", title="Limit", format=".6g"),
            alt.Tooltip("load_ratio:Q", title="Actual / limit", format=".3f"),
            alt.Tooltip("reason:N", title="Reason"),
        ],
    )
    latest_label = source.transform_filter(alt.datum.is_latest).mark_text(
        align="left",
        dx=8,
        dy=-9,
        color=_INK,
        fontSize=11,
    ).encode(
        x=x,
        y=y,
        text=alt.Text("load_label:N"),
    )
    threshold = (
        alt.Chart(alt.InlineData(values=[{"limit": 1.0}]))
        .mark_rule(color=_RED, strokeDash=[5, 3], strokeWidth=1.6)
        .encode(y="limit:Q")
    )
    chart = (
        alt.layer(line, threshold, points, latest_label)
        .resolve_scale(color="independent")
        .add_params(selector)
        .properties(
            title={
                "text": "Guardrail history",
                "subtitle": (
                    "Actual / limit = 1.0 is the threshold; the comparator defines the pass side. "
                    "Definition changes form separate series."
                ),
            },
            width="container" if interactive else _CHART_WIDTH,
            height=270,
        )
    )
    return _theme(chart) if themed else chart


def build_overview_chart(model: ReportViewModel) -> alt.TopLevelMixin:
    """browser runtime 不要の履歴中心 overview.svg 用 chart を返す。"""

    overview = (
        alt.vconcat(
            cast(Any, _summary_chart(model, themed=False)),
            cast(
                Any,
                _static_case_overview_chart(model),
            ),
            cast(Any, _static_footer_chart(model, themed=False)),
            spacing=28,
        )
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


def _performance_history_panel(
    model: ReportViewModel,
    case_selector: alt.Parameter,
    *,
    responsive: bool,
) -> alt.TopLevelMixin:
    records = _history_records(model)
    if not records:
        return _message_chart(
            "Performance history",
            "No measured performance history is available.",
            responsive=responsive,
            themed=False,
        )

    cohort_records = _cohort_records(model)
    source = alt.Chart(alt.InlineData(values=records))
    history_color = alt.Color(
        "point_state:N",
        scale=alt.Scale(
            domain=["current", "past", "failure"],
            range=[_BLUE, _GRAY, _RED],
        ),
        legend=alt.Legend(title="Compatibility cohort", orient="bottom"),
    )
    x = alt.X(
        "created_at:T",
        title="UTC",
        scale=alt.Scale(type="utc"),
        axis=alt.Axis(gridColor=_GRID),
    )
    y = alt.Y(
        "median_ms:Q",
        title="Median per iteration (ms)",
        scale=alt.Scale(zero=False),
        axis=alt.Axis(gridColor=_GRID),
    )
    whiskers = source.mark_rule(strokeWidth=1.5, opacity=0.82).encode(
        x=x,
        y=alt.Y(
            "mad_low_ms:Q",
            title="Median per iteration (ms)",
            scale=alt.Scale(zero=False),
        ),
        y2=alt.Y2("mad_high_ms:Q"),
        color=history_color,
    )
    line = source.transform_filter(alt.datum.status == "ok").mark_line(
        strokeWidth=2.2,
    ).encode(
        x=x,
        y=y,
        detail=[alt.Detail("cohort_id:N"), alt.Detail("segment_id:N")],
        order=alt.Order("run_index:Q"),
        color=history_color,
    )
    points = source.mark_point(filled=True, size=64).encode(
        x=x,
        y=y,
        color=history_color,
        shape=alt.Shape(
            "output_state:N",
            scale=alt.Scale(
                domain=["stable", "changed"],
                range=["circle", "diamond"],
            ),
            legend=alt.Legend(title="Checksum", orient="bottom"),
        ),
        tooltip=_history_tooltips(),
    )
    latest_label = (
        source.transform_calculate(
            display_latest_label=(
                "isValid(datum.latest_value_label) ? datum.latest_value_label : ''"
            )
        )
        .mark_text(
            align="left",
            dx=8,
            dy=-10,
            color=_INK,
            fontSize=11,
        )
        .encode(
            x=x,
            y=y,
            text=alt.Text("display_latest_label:N"),
        )
    )
    one_point = (
        source.transform_calculate(
            display_trend_note="isValid(datum.trend_note) ? datum.trend_note : ''"
        )
        .mark_text(
            align="left",
            dx=8,
            dy=12,
            color="#5d697c",
            fontSize=10,
        )
        .encode(
            x=x,
            y=y,
            text=alt.Text("display_trend_note:N"),
        )
    )
    panels = (
        alt.layer(whiskers, line, points, latest_label, one_point)
        .transform_lookup(
            lookup="cohort_id",
            from_=alt.LookupData(
                data=alt.InlineData(values=cohort_records),
                key="cohort_id",
                fields=[
                    "cohort_label",
                    "cohort_state",
                    "cohort_rank",
                    "mode",
                    "measurement_label",
                ],
            ),
        )
        .properties(
            width="container" if responsive else _CHART_WIDTH,
            height=190,
        )
        .facet(
            row=alt.Row(
                "cohort_label:N",
                title=None,
                sort=alt.EncodingSortField(field="cohort_rank", op="min", order="ascending"),
                header=alt.Header(
                    labelAngle=0,
                    labelAlign="left",
                    labelAnchor="start",
                    labelOrient="top",
                    labelLimit=(720 if responsive else _STATIC_LABEL_LIMIT),
                    labelFont="Google Sans",
                    labelFontSize=11,
                ),
            ),
        )
        .resolve_scale(y="independent")
        .transform_filter(alt.datum.case_id == case_selector)
        .properties(
            title={
                "text": "Performance history",
                "subtitle": [
                    "Median per run; whiskers show MAD.",
                    "MAD is within-run sample variability, not a confidence interval.",
                    "Each panel is one compatible cohort; failures and output changes break lines.",
                ],
            },
        )
    )
    return panels


def _coverage_panel(
    model: ReportViewModel,
    case_selector: alt.Parameter,
    *,
    responsive: bool,
) -> alt.TopLevelMixin:
    timeline = _timeline_records(model)
    if not timeline:
        return _message_chart(
            "Run coverage",
            "No coverage classification is available.",
            responsive=responsive,
            themed=False,
        )

    observations = _coverage_records(model)
    cohort_records = _cohort_records(model)
    source = (
        alt.Chart(alt.InlineData(values=timeline))
        .transform_calculate(
            coverage_key=(
                f"datum.run_id + '{_COVERAGE_KEY_SEPARATOR}' + {case_selector.name}"
            )
        )
        .transform_lookup(
            lookup="coverage_key",
            from_=alt.LookupData(
                data=alt.InlineData(values=observations),
                key="coverage_key",
                fields=[
                    "observed_state",
                    "observed_reason",
                    "observed_cohort_id",
                ],
            ),
        )
        .transform_lookup(
            lookup="observed_cohort_id",
            from_=alt.LookupData(
                data=alt.InlineData(values=cohort_records),
                key="cohort_id",
                fields=["cohort_label"],
            ),
        )
        .transform_calculate(
            state=(
                "isValid(datum.observed_state) ? datum.observed_state : 'missing'"
            ),
            reason=(
                "isValid(datum.observed_reason) ? datum.observed_reason : "
                "datum.observed_state === 'current' ? "
                "'included in the current compatibility cohort' : "
                "datum.observed_state === 'output-changed' ? "
                "'checksum changed within the current cohort' : "
                "'case is not part of this run'"
            ),
            cohort_label=(
                "isValid(datum.cohort_label) ? datum.cohort_label : "
                "datum.observed_state === 'other-cohort' ? "
                "'another cohort · no measured timing' : "
                "isValid(datum.observed_state) ? "
                "'current cohort · no measured timing' : 'not measured'"
            ),
        )
    )
    return source.mark_tick(thickness=10, size=28).encode(
        x=alt.X(
            "created_at:T",
            title=None,
            scale=alt.Scale(type="utc"),
            axis=alt.Axis(labels=False, ticks=False, grid=False),
        ),
        y=alt.Y("strip:N", title=None, axis=None),
        color=alt.Color(
            "state:N",
            scale=alt.Scale(
                domain=[
                    "current",
                    "output-changed",
                    "other-cohort",
                    "missing",
                    "failure",
                ],
                range=[_BLUE, _ORANGE, _YELLOW, _GRAY, _RED],
            ),
            legend=alt.Legend(title="Run coverage", orient="bottom"),
        ),
        tooltip=[
            alt.Tooltip("created_at:N", title="UTC"),
            alt.Tooltip("run_id:N", title="Run"),
            alt.Tooltip("state:N", title="Classification"),
            alt.Tooltip("reason:N", title="Reason"),
            alt.Tooltip("cohort_label:N", title="Cohort"),
        ],
    ).properties(
        title={
            "text": "Run coverage",
            "subtitle": (
                "Blue: current cohort · orange: checksum changed · yellow: another cohort · "
                "gray: absent · red: current-cohort failure."
            ),
        },
        width="container" if responsive else _CHART_WIDTH,
        height=34,
    )


def _case_options(model: ReportViewModel) -> list[dict[str, object]]:
    return [
        {
            "case_id": row.case_id,
            "label": row.label,
            "category": row.category,
            "selector_label": row.selector_label,
            "latest": not row.historical,
        }
        for row in model.cases
    ]


def _case_selector(
    cases: list[dict[str, object]],
    default_case: str,
    *,
    bound: bool = True,
) -> alt.Parameter:
    binding = (
        alt.binding_select(
            options=[str(row["case_id"]) for row in cases],
            labels=[str(row["selector_label"]) for row in cases],
            name="Case: ",
        )
        if bound
        else alt.Undefined
    )
    return alt.param(
        name="history_case",
        value=default_case,
        bind=binding,
    )


def _default_case_id(
    model: ReportViewModel,
    cases: list[dict[str, object]],
) -> str:
    counts = Counter(
        row.case_id
        for row in model.history
        if row.is_current and row.status == "ok"
    )
    measured = {case_id for case_id, count in counts.items() if count > 0}
    latest = {str(row["case_id"]) for row in cases if row["latest"]}
    candidates = (latest & measured) or measured or latest or {
        str(row["case_id"]) for row in cases
    }
    return min(candidates, key=lambda case_id: (-counts[case_id], case_id))


def _history_records(model: ReportViewModel) -> list[dict[str, Any]]:
    observations = Counter(
        (row.case_id, row.cohort_id)
        for row in model.history
        if row.status == "ok"
    )
    current_latest: dict[str, int] = {}
    for row in model.history:
        if row.is_current and row.status == "ok":
            current_latest[row.case_id] = max(
                row.run_index,
                current_latest.get(row.case_id, row.run_index),
            )
    records: list[dict[str, Any]] = []
    for row in model.history:
        cohort_state = "current" if row.is_current else "past"
        record: dict[str, Any] = {
            "case_id": row.case_id,
            "label": row.label,
            "category": row.category,
            "cohort_id": row.cohort_id,
            "is_current": row.is_current,
            "run_id": row.run_id,
            "created_at": row.created_at,
            "run_index": row.run_index,
            "segment_id": row.segment_id,
            "status": row.status,
            "median_ms": row.median_ms,
            "mad_ms": row.mad_ms,
            "mad_low_ms": row.mad_low_ms,
            "mad_high_ms": row.mad_high_ms,
            "p95_ms": row.p95_ms,
            "p99_ms": row.p99_ms,
            "sample_count": row.sample_count,
            "source_commit": row.source_commit,
            "source_dirty": row.source_dirty,
            "checksum": row.checksum,
            "checksum_changed": row.checksum_changed,
            "output_state": "changed" if row.checksum_changed else "stable",
            "point_state": (
                "failure" if row.status != "ok" else cohort_state
            ),
            "observation_count": observations[(row.case_id, row.cohort_id)],
        }
        if (
            row.is_current
            and row.status == "ok"
            and current_latest.get(row.case_id) == row.run_index
        ):
            record["latest_value_label"] = f"{row.median_ms:.6f} ms"
        if (
            row.is_current
            and row.status == "ok"
            and observations[(row.case_id, row.cohort_id)] == 1
        ):
            record["trend_note"] = "1 compatible observation; trend unavailable"
        records.append(record)
    return records


def _cohort_records(model: ReportViewModel) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for row in model.history:
        records[row.cohort_id] = {
            "cohort_id": row.cohort_id,
            "cohort_label": row.cohort_label,
            "cohort_state": "current" if row.is_current else "past",
            "cohort_rank": 0 if row.is_current else 1,
            "mode": row.mode,
            "measurement_label": row.measurement_label,
        }
    return [records[cohort_id] for cohort_id in sorted(records)]


def _coverage_records(model: ReportViewModel) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in model.coverage:
        record = {
            "coverage_key": f"{row.run_id}{_COVERAGE_KEY_SEPARATOR}{row.case_id}",
            "observed_state": row.state,
            "observed_cohort_id": row.cohort_id,
        }
        if row.state not in {"current", "output-changed"}:
            record["observed_reason"] = row.reason
        records.append(record)
    return records


def _timeline_records(model: ReportViewModel) -> list[dict[str, Any]]:
    return [
        {
            "run_id": row.run_id,
            "created_at": row.created_at,
            "run_index": row.run_index,
            "is_latest": row.run_id == model.latest_run_id,
            "strip": "runs",
        }
        for row in model.timeline
    ]


def _overview_records(
    model: ReportViewModel,
    *,
    grouped: bool,
    limit: int,
) -> tuple[list[dict[str, Any]], list[str], int, int]:
    history = [row for row in _history_records(model) if row["is_current"]]
    case_categories = {
        str(row["case_id"]): str(row["category"])
        for row in history
    }
    all_cases = sorted(case_categories)
    categories = sorted(set(case_categories.values()))
    groups = ["All", *categories] if grouped else ["All"]
    counts = Counter(
        str(row["case_id"])
        for row in history
        if row["status"] == "ok"
    )

    def ranked(candidates: list[str]) -> list[str]:
        return sorted(candidates, key=lambda case_id: (-counts[case_id], case_id))[:limit]

    global_selected = ranked(all_cases)
    category_selected = {
        category: ranked(
            [case_id for case_id in all_cases if case_categories[case_id] == category]
        )
        for category in categories
    }
    selected_union = set(global_selected)
    if grouped:
        selected_union.update(
            case_id
            for selected in category_selected.values()
            for case_id in selected
        )
    global_ranks = {case_id: rank for rank, case_id in enumerate(global_selected)}
    category_ranks = {
        case_id: rank
        for selected in category_selected.values()
        for rank, case_id in enumerate(selected)
    }
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        case_id = str(row["case_id"])
        if case_id in selected_union:
            by_case[case_id].append(row)

    records: list[dict[str, Any]] = []
    for case_id in sorted(selected_union):
        rows = by_case[case_id]
        first = min(rows, key=lambda row: (row["run_index"], row["run_id"]))
        latest = max(rows, key=lambda row: (row["run_index"], row["run_id"]))
        panel_label = (
            f"{latest['label']} · {counts[case_id]} obs · "
            f"{float(first['median_ms']):.6f} → {float(latest['median_ms']):.6f} ms · "
            f"{case_id}"
        )
        for row in rows:
            records.append(
                {
                    **row,
                    "global_rank": global_ranks.get(case_id, limit),
                    "category_rank": category_ranks.get(case_id, limit),
                    "rank": global_ranks.get(case_id, limit),
                    "in_global": case_id in global_ranks,
                    "in_category": case_id in category_ranks,
                    "panel_label": panel_label,
                    "point_state": (
                        "observation" if row["status"] == "ok" else "failure"
                    ),
                }
            )
    return records, groups, len(global_selected), len(all_cases)


def _guardrail_records(model: ReportViewModel) -> list[dict[str, Any]]:
    return [
        {
            "case_id": row.case_id,
            "contract_id": row.contract_id,
            "guardrail_key": f"{row.case_id}\u241f{row.contract_id}",
            "guardrail_label": f"{row.case_id} · {row.contract_id}",
            "cohort_id": row.cohort_id,
            "cohort_label": row.cohort_label,
            "cohort_state": "current" if row.is_current else "past",
            "segment_id": row.segment_id,
            "run_id": row.run_id,
            "created_at": row.created_at,
            "run_index": row.run_index,
            "passed": row.passed,
            "result": "pass" if row.passed else "fail",
            "actual": row.actual,
            "limit": row.limit,
            "load_ratio": row.load_ratio,
            "load_label": f"{row.load_ratio:.3f}",
            "direction": row.direction,
            "reason": row.reason,
            "is_latest": row.run_id == model.latest_run_id,
        }
        for row in model.guardrails
    ]


def _history_tooltips() -> list[alt.Tooltip]:
    return [
        alt.Tooltip("created_at:N", title="UTC"),
        alt.Tooltip("run_id:N", title="Run"),
        alt.Tooltip("source_commit:N", title="Commit"),
        alt.Tooltip("source_dirty:N", title="Dirty"),
        alt.Tooltip("case_id:N", title="Case"),
        alt.Tooltip("status:N", title="Status"),
        alt.Tooltip("cohort_label:N", title="Cohort"),
        alt.Tooltip("mode:N", title="Mode"),
        alt.Tooltip("measurement_label:N", title="Measurement"),
        alt.Tooltip("checksum:N", title="Checksum"),
        alt.Tooltip("checksum_changed:N", title="Output changed"),
        alt.Tooltip("median_ms:Q", title="Median ms", format=".6f"),
        alt.Tooltip("mad_ms:Q", title="MAD ms", format=".6f"),
        alt.Tooltip("p95_ms:Q", title="p95 ms", format=".6f"),
        alt.Tooltip("p99_ms:Q", title="p99 ms", format=".6f"),
        alt.Tooltip("sample_count:Q", title="Samples"),
    ]


def _summary_chart(
    model: ReportViewModel,
    *,
    themed: bool = True,
) -> alt.TopLevelMixin:
    summary = model.summary
    history = model.history_summary
    if summary is None:
        lines = [
            "Grafix benchmark history",
            "No dated valid schema v4 run is available.",
            (
                f"Loaded: {history.run_count} runs · {history.unique_case_count} cases · "
                f"{history.cohort_count} cohorts"
            ),
        ]
    else:
        statuses = ", ".join(f"{status} {count}" for status, count in summary.status_counts)
        period = (
            summary.created_at
            if history.start_at is None or history.end_at is None
            else f"{history.start_at} — {history.end_at}"
        )
        lines = [
            "Grafix benchmark history",
            f"Period: {period}",
            (
                f"History: {history.run_count} runs ({history.temporal_run_count} dated) · "
                f"{history.unique_case_count} cases · {history.cohort_count} cohorts · "
                f"{history.trend_cohort_count} with 2+ observations"
            ),
            f"Latest: {summary.run_id} · {summary.source[:12]} · {statuses}",
            (
                f"Latest contracts: hard {summary.hard_passed}/{summary.hard_total}, "
                f"soft {summary.soft_passed}/{summary.soft_total} · "
                f"warnings {history.latest_warning_count} latest / "
                f"{history.historical_warning_count} historical"
            ),
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


def _static_footer_chart(
    model: ReportViewModel,
    *,
    themed: bool = True,
) -> alt.TopLevelMixin:
    latest_failures = [
        row
        for row in model.guardrails
        if row.run_id == model.latest_run_id and not row.passed
    ]
    if latest_failures:
        guardrail_line = "Latest guardrail failures: " + ", ".join(
            f"{row.case_id} · {row.contract_id}" for row in latest_failures[:4]
        )
    else:
        guardrail_line = "Latest guardrails: no failure in directional soft contracts."
    warning_count = model.history_summary.latest_warning_count
    lines = [
        guardrail_line,
        f"Latest warnings: {warning_count}",
        "Selection: latest current cohorts with the most compatible successful observations.",
    ]
    chart = (
        alt.Chart(
            alt.InlineData(
                values=[{"line": line, "row": index} for index, line in enumerate(lines)]
            )
        )
        .mark_text(align="left", baseline="top", fontSize=12, color="#5d697c")
        .encode(
            x=alt.value(4),
            y=alt.Y("row:O", axis=None, sort="ascending"),
            text="line:N",
        )
        .properties(width=_CHART_WIDTH, height=78)
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
    "build_case_overview_chart",
    "build_chart_specs",
    "build_coverage_chart",
    "build_guardrail_chart",
    "build_history_chart",
    "build_overview_chart",
    "offline_vega_runtime",
    "render_overview_svg",
    "script_json",
]
