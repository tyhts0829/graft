"""
どこで: `tools/benchmarks/report.py`。
何を: effect ベンチ結果（JSON）から HTML レポートを生成する。
なぜ: ケース別に「遅い effect のランキング」を横棒グラフで素早く確認するため。
"""

from __future__ import annotations

from html import escape
from typing import Any


def render_report_html(results: dict[str, Any]) -> str:
    """ベンチ結果辞書から HTML を生成して返す。"""
    meta: dict[str, Any] = dict(results.get("meta", {}))
    cases: list[dict[str, Any]] = list(results.get("cases", []))
    effects: list[dict[str, Any]] = list(results.get("effects", []))

    head = _render_head(title=f"grafix effect benchmark - {meta.get('run_id', '')}")
    body = []
    body.append("<h1>grafix effect benchmark</h1>")
    body.append(_render_meta(meta))
    body.append(_render_case_index(cases))

    # ケース別ランキング（横棒）を作る。
    for case in cases:
        case_id = str(case.get("id"))
        case_label = str(case.get("label", case_id))
        body.append(f"<h2 id=\"case-{escape(case_id)}\">Case: {escape(case_label)}</h2>")
        body.append(_render_case_meta(case))

        rows: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        errored: list[dict[str, Any]] = []

        for eff in effects:
            name = str(eff.get("name", ""))
            res_map = dict(eff.get("results", {}))
            res = dict(res_map.get(case_id, {}))
            status = str(res.get("status", ""))
            row = {"effect": name, **res}
            if status == "ok":
                rows.append(row)
            elif status == "skipped":
                skipped.append(row)
            elif status:
                errored.append(row)
            else:
                errored.append({"effect": name, "status": "error", "error": "missing result"})

        rows.sort(key=lambda r: float(r.get("mean_ms", 0.0)), reverse=True)

        body.append(_render_bar_ranking(case_id=case_id, rows=rows))
        body.append(_render_table(case_id=case_id, rows=rows, skipped=skipped, errored=errored))

    # 末尾に JSON へのリンク（ファイルとして開く用途）
    json_name = escape(str(meta.get("json_filename", "results.json")))
    body.append("<hr />")
    body.append("<p>")
    body.append(f"<a href=\"{json_name}\">results.json</a>")
    body.append("</p>")

    return head + "\n<body>\n" + "\n".join(body) + "\n</body>\n</html>\n"


def _render_head(*, title: str) -> str:
    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(title)}</title>
  <style>
    :root {{
      --bg: #0b1020;
      --panel: #121a33;
      --text: #e7ecff;
      --muted: #aab3d6;
      --grid: rgba(255,255,255,0.08);
      --bar: #4aa3ff;
      --bar2: #7fdbca;
      --warn: #ffcc66;
      --err: #ff6b6b;
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      --sans: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji";
    }}
    body {{
      margin: 0;
      padding: 28px;
      font-family: var(--sans);
      background: linear-gradient(180deg, var(--bg), #070a14);
      color: var(--text);
    }}
    a {{ color: var(--bar); }}
    h1 {{ margin: 0 0 8px 0; font-size: 24px; }}
    h2 {{ margin: 28px 0 10px 0; font-size: 18px; }}
    .muted {{ color: var(--muted); }}
    .panel {{
      background: rgba(18,26,51,0.9);
      border: 1px solid var(--grid);
      border-radius: 12px;
      padding: 12px 14px;
      margin: 10px 0;
      backdrop-filter: blur(10px);
    }}
    .mono {{ font-family: var(--mono); }}
    .case-index a {{ margin-right: 10px; }}
    .bars {{
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }}
    .barrow {{
      display: grid;
      grid-template-columns: 240px 1fr 120px;
      align-items: center;
      gap: 10px;
    }}
    .barlabel {{
      font-family: var(--mono);
      font-size: 12px;
      color: var(--text);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .barwrap {{
      height: 14px;
      border: 1px solid var(--grid);
      background: rgba(255,255,255,0.04);
      border-radius: 8px;
      overflow: hidden;
    }}
    .bar {{
      height: 100%;
      background: linear-gradient(90deg, var(--bar), var(--bar2));
      width: 0%;
    }}
    .barvalue {{
      font-family: var(--mono);
      font-size: 12px;
      color: var(--muted);
      text-align: right;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
      font-family: var(--mono);
      font-size: 12px;
    }}
    th, td {{
      border-bottom: 1px solid var(--grid);
      padding: 8px 6px;
      text-align: left;
      vertical-align: top;
    }}
    th {{ color: var(--muted); font-weight: 600; }}
    .status-ok {{ color: var(--bar2); }}
    .status-skipped {{ color: var(--warn); }}
    .status-error {{ color: var(--err); }}
    code {{
      font-family: var(--mono);
      background: rgba(255,255,255,0.06);
      padding: 2px 6px;
      border-radius: 6px;
    }}
  </style>
</head>
"""


def _render_meta(meta: dict[str, Any]) -> str:
    items: list[str] = []
    for key in (
        "run_id",
        "created_at",
        "python",
        "platform",
        "repeats",
        "warmup",
        "seed",
        "out_dir",
    ):
        if key in meta:
            items.append(f"<div><span class=\"muted\">{escape(key)}</span>: <span class=\"mono\">{escape(str(meta[key]))}</span></div>")
    if not items:
        return ""
    return "<div class=\"panel\">" + "\n".join(items) + "</div>"


def _render_case_index(cases: list[dict[str, Any]]) -> str:
    links: list[str] = []
    for case in cases:
        cid = str(case.get("id", ""))
        label = str(case.get("label", cid))
        if not cid:
            continue
        links.append(f"<a href=\"#case-{escape(cid)}\">{escape(label)}</a>")
    if not links:
        return ""
    return "<div class=\"panel case-index\"><div class=\"muted\">Cases</div>" + " ".join(links) + "</div>"


def _render_case_meta(case: dict[str, Any]) -> str:
    desc = escape(str(case.get("description", "")))
    n_vertices = escape(str(case.get("n_vertices", "")))
    n_lines = escape(str(case.get("n_lines", "")))
    closed = escape(str(case.get("closed_lines", "")))
    parts = [
        f"<div class=\"muted\">{desc}</div>" if desc else "",
        "<div style=\"margin-top:6px\" class=\"mono\">"
        f"verts={n_vertices} lines={n_lines} closed_lines={closed}"
        "</div>",
    ]
    return "<div class=\"panel\">" + "\n".join(p for p in parts if p) + "</div>"


def _render_bar_ranking(*, case_id: str, rows: list[dict[str, Any]]) -> str:
    ok = [r for r in rows if r.get("status") == "ok"]
    if not ok:
        return "<div class=\"panel muted\">計測結果なし</div>"

    max_ms = max(float(r.get("mean_ms", 0.0)) for r in ok)
    if max_ms <= 0.0:
        max_ms = 1.0

    out = []
    out.append("<div class=\"panel\">")
    out.append("<div class=\"muted\">Ranking (mean ms, slowest first)</div>")
    out.append("<div class=\"bars\">")
    for r in ok:
        name = escape(str(r.get("effect", "")))
        mean_ms = float(r.get("mean_ms", 0.0))
        pct = 100.0 * mean_ms / max_ms
        out.append(
            "<div class=\"barrow\">"
            f"<div class=\"barlabel\">{name}</div>"
            "<div class=\"barwrap\">"
            f"<div class=\"bar\" style=\"width: {pct:.2f}%\"></div>"
            "</div>"
            f"<div class=\"barvalue\">{mean_ms:.3f} ms</div>"
            "</div>"
        )
    out.append("</div></div>")
    return "\n".join(out)


def _render_table(
    *,
    case_id: str,
    rows: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    errored: list[dict[str, Any]],
) -> str:
    out = []
    out.append("<div class=\"panel\">")
    out.append("<div class=\"muted\">Details</div>")
    out.append("<table>")
    out.append(
        "<tr>"
        "<th>effect</th>"
        "<th>status</th>"
        "<th>mean_ms</th>"
        "<th>stdev_ms</th>"
        "<th>min_ms</th>"
        "<th>max_ms</th>"
        "<th>n</th>"
        "<th>note</th>"
        "</tr>"
    )

    def add_row(r: dict[str, Any]) -> None:
        name = escape(str(r.get("effect", "")))
        status = str(r.get("status", ""))
        cls = (
            "status-ok"
            if status == "ok"
            else "status-skipped"
            if status == "skipped"
            else "status-error"
        )
        note = escape(str(r.get("error", r.get("note", "")) or ""))
        out.append(
            "<tr>"
            f"<td>{name}</td>"
            f"<td class=\"{cls}\">{escape(status)}</td>"
            f"<td>{_fmt_num(r.get('mean_ms'))}</td>"
            f"<td>{_fmt_num(r.get('stdev_ms'))}</td>"
            f"<td>{_fmt_num(r.get('min_ms'))}</td>"
            f"<td>{_fmt_num(r.get('max_ms'))}</td>"
            f"<td>{escape(str(r.get('n', '')))}</td>"
            f"<td>{note}</td>"
            "</tr>"
        )

    for r in rows:
        add_row(r)
    for r in skipped:
        add_row(r)
    for r in errored:
        add_row(r)

    out.append("</table></div>")
    return "\n".join(out)


def _fmt_num(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.3f}"
    except Exception:
        return escape(str(value))
