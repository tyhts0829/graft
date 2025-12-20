"""
どこで: `tools/benchmarks/effect_benchmark_report.py`。
何を: 複数 run のベンチ結果（`data/output/benchmarks/runs/*.json`）から時系列 HTML レポートを生成する。
なぜ: 最適化前後の改善度合いを、ケース別×effect 別の折れ線で把握するため。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.benchmarks.report import render_timeseries_report_html

_RUN_ID_FORMAT = "%Y%m%d_%H%M%S"


@dataclass(frozen=True, slots=True)
class _Run:
    run_id: str
    dt: datetime
    meta: dict[str, Any]
    cases: dict[str, dict[str, Any]]
    means_ms: dict[str, dict[str, float]]
    effect_names: list[str]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    out_root = Path(args.out).expanduser().resolve()
    runs_dir = out_root / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    if bool(args.migrate_legacy):
        _migrate_legacy_results(out_root=out_root, runs_dir=runs_dir)

    report = build_timeseries_report(
        runs_dir=runs_dir,
        cases=_split_csv(args.cases),
        effects=_split_csv(args.effects),
        skip=_split_csv(args.skip),
        top=int(args.top),
    )
    html = render_timeseries_report_html(report)

    output_path = Path(args.output).expanduser().resolve() if args.output else (out_root / "report.html")
    output_path.write_text(html, encoding="utf-8")
    print(f"[grafix-bench] wrote: {output_path}")  # noqa: T201
    return 0


def build_timeseries_report(
    *,
    runs_dir: Path,
    cases: set[str] | None,
    effects: set[str] | None,
    skip: set[str] | None,
    top: int,
) -> dict[str, Any]:
    runs = _load_runs(runs_dir)
    if not runs:
        raise SystemExit(f"no runs found: {runs_dir}")

    latest = runs[-1]
    case_list = list(latest.cases.values())
    if cases:
        case_list = [c for c in case_list if str(c.get("id")) in cases]

    effect_names = list(latest.effect_names)
    if effects:
        effect_names = [e for e in effect_names if e in effects]
    if skip:
        effect_names = [e for e in effect_names if e not in skip]

    run_rows = []
    for r in runs:
        run_rows.append(
            {
                "run_id": r.run_id,
                "created_at": r.meta.get("created_at", ""),
                "git_sha": r.meta.get("git_sha", ""),
            }
        )

    chart_specs: list[dict[str, Any]] = []
    for case in case_list:
        case_id = str(case.get("id", ""))
        if not case_id:
            continue

        series: dict[str, list[float | None]] = {}
        for eff in effect_names:
            pts: list[float | None] = []
            for r in runs:
                v = r.means_ms.get(case_id, {}).get(eff)
                pts.append(float(v) if v is not None else None)
            series[eff] = pts

        latest_values: list[tuple[str, float]] = []
        latest_means = latest.means_ms.get(case_id, {})
        for eff in effect_names:
            v = latest_means.get(eff)
            if v is None:
                continue
            latest_values.append((eff, float(v)))
        latest_values.sort(key=lambda kv: kv[1], reverse=True)

        visible: set[str]
        if top <= 0:
            visible = set(effect_names)
        else:
            visible = {eff for eff, _v in latest_values[:top]}

        ordered_effects = [eff for eff, _v in latest_values] + [e for e in effect_names if e not in {k for k, _v in latest_values}]

        datasets = []
        for eff in ordered_effects:
            color = _color_for_label(eff)
            datasets.append(
                {
                    "label": eff,
                    "data": series.get(eff, []),
                    "borderColor": color,
                    "backgroundColor": color,
                    "pointStyle": "circle",
                    "tension": 0.2,
                    "hidden": bool(eff not in visible),
                }
            )

        table_rows = []
        for eff in ordered_effects:
            pts = series.get(eff, [])
            first = next((v for v in pts if v is not None), None)
            last = next((v for v in reversed(pts) if v is not None), None)
            ratio = ""
            if first is not None and last is not None and float(first) > 0.0:
                ratio = f"{float(last) / float(first):.3f}x"
            table_rows.append(
                {
                    "effect": eff,
                    "first_ms": first,
                    "last_ms": last,
                    "ratio": ratio,
                }
            )

        chart_specs.append(
            {
                "case_id": case_id,
                "case_label": str(case.get("label", case_id)),
                "case_description": str(case.get("description", "")),
                "n_vertices": case.get("n_vertices", ""),
                "n_lines": case.get("n_lines", ""),
                "closed_lines": case.get("closed_lines", ""),
                "datasets": datasets,
                "table": table_rows,
            }
        )

    report_meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "runs": len(runs),
        "first_run": runs[0].run_id,
        "last_run": runs[-1].run_id,
    }
    return {
        "meta": report_meta,
        "runs": run_rows,
        "cases": [{"id": c.get("id", ""), "label": c.get("label", "")} for c in case_list],
        "charts": chart_specs,
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="effect_benchmark_report")
    p.add_argument("--out", default="data/output/benchmarks", help="入力ルート（runs/ 配下を読む）")
    p.add_argument("--output", default="", help="出力 HTML（省略時: <out>/report.html）")
    p.add_argument("--cases", default="", help="ケース id をカンマ区切りで指定（例: ring_big）")
    p.add_argument("--effects", default="", help="effect をカンマ区切りで指定（例: scale,rotate）")
    p.add_argument("--skip", default="", help="除外する effect をカンマ区切りで指定")
    p.add_argument("--top", type=int, default=10, help="最新 run の遅い順で上位 N 本を初期表示（0 で全表示）")
    p.add_argument(
        "--migrate-legacy",
        action="store_true",
        help="旧形式（<out>/<run_id>/results.json）を runs/<run_id>.json に移行する",
    )
    return p.parse_args(argv)


def _split_csv(value: str) -> set[str] | None:
    items = {s.strip() for s in str(value).split(",") if s.strip()}
    return items or None


def _parse_run_id(run_id: str) -> datetime | None:
    try:
        return datetime.strptime(run_id, _RUN_ID_FORMAT)
    except ValueError:
        return None


def _load_runs(runs_dir: Path) -> list[_Run]:
    runs: list[_Run] = []
    for fp in sorted(runs_dir.glob("*.json")):
        dt = _parse_run_id(fp.stem)
        if dt is None:
            continue
        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue

        meta = dict(raw.get("meta", {}))
        cases = {str(c.get("id", "")): dict(c) for c in raw.get("cases", []) if str(c.get("id", ""))}
        effect_names: list[str] = []
        seen_effects: set[str] = set()
        means_ms: dict[str, dict[str, float]] = {}
        for eff in raw.get("effects", []):
            name = str(eff.get("name", ""))
            if not name:
                continue
            if name not in seen_effects:
                seen_effects.add(name)
                effect_names.append(name)
            for case_id, res in dict(eff.get("results", {})).items():
                if str(res.get("status", "")) != "ok":
                    continue
                try:
                    mean_ms = float(res.get("mean_ms", 0.0))
                except Exception:
                    continue
                means_ms.setdefault(str(case_id), {})[name] = mean_ms

        runs.append(
            _Run(
                run_id=fp.stem,
                dt=dt,
                meta=meta,
                cases=cases,
                means_ms=means_ms,
                effect_names=effect_names,
            )
        )

    runs.sort(key=lambda r: r.dt)
    return runs


def _color_for_label(label: str) -> str:
    # label から安定に色相を作る（見た目が単調になりにくい HSL）。
    h = 0
    for ch in label:
        h = (h * 131 + ord(ch)) % 360
    return f"hsl({h}, 70%, 60%)"


def _migrate_legacy_results(*, out_root: Path, runs_dir: Path) -> None:
    for d in sorted(out_root.iterdir()):
        if not d.is_dir():
            continue
        dt = _parse_run_id(d.name)
        if dt is None:
            continue
        src = d / "results.json"
        if not src.is_file():
            continue
        dst = runs_dir / f"{d.name}.json"
        if dst.exists():
            continue
        src.replace(dst)


if __name__ == "__main__":
    raise SystemExit(main())
