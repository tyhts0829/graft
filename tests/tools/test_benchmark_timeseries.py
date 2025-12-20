"""tools.benchmarks.effect_benchmark_report の時系列集約に関するテスト。"""

from __future__ import annotations

import json
from pathlib import Path

from tools.benchmarks.effect_benchmark_report import build_timeseries_report


def _write_run(fp: Path, *, run_id: str, scale_ms: float, rotate_ms: float) -> None:
    fp.write_text(
        json.dumps(
            {
                "meta": {
                    "run_id": run_id,
                    "created_at": "2025-12-20T00:00:00",
                    "git_sha": "deadbeef" * 5,
                },
                "cases": [
                    {
                        "id": "polyline_long",
                        "label": "polyline (50k verts)",
                        "description": "1 本の長い折れ線",
                        "n_vertices": 50_000,
                        "n_lines": 1,
                        "closed_lines": 0,
                    }
                ],
                "effects": [
                    {
                        "name": "scale",
                        "results": {
                            "polyline_long": {
                                "status": "ok",
                                "mean_ms": scale_ms,
                                "stdev_ms": 0.0,
                                "min_ms": scale_ms,
                                "max_ms": scale_ms,
                                "n": 10,
                            }
                        },
                    },
                    {
                        "name": "rotate",
                        "results": {
                            "polyline_long": {
                                "status": "ok",
                                "mean_ms": rotate_ms,
                                "stdev_ms": 0.0,
                                "min_ms": rotate_ms,
                                "max_ms": rotate_ms,
                                "n": 10,
                            }
                        },
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_build_timeseries_report_orders_runs_and_hides_non_top(tmp_path: Path) -> None:
    """run を日時順に並べ、top=N で初期表示が絞られる。"""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    _write_run(
        runs_dir / "20251220_010000.json",
        run_id="20251220_010000",
        scale_ms=10.0,
        rotate_ms=2.0,
    )
    _write_run(
        runs_dir / "20251220_020000.json",
        run_id="20251220_020000",
        scale_ms=5.0,
        rotate_ms=3.0,
    )

    report = build_timeseries_report(
        runs_dir=runs_dir,
        cases=None,
        effects=None,
        skip=None,
        top=1,
    )

    assert report["meta"]["first_run"] == "20251220_010000"
    assert report["meta"]["last_run"] == "20251220_020000"

    charts = report["charts"]
    assert len(charts) == 1
    chart = charts[0]

    datasets = chart["datasets"]
    assert [d["label"] for d in datasets] == ["scale", "rotate"]
    assert datasets[0]["data"] == [10.0, 5.0]
    assert datasets[1]["data"] == [2.0, 3.0]
    assert datasets[0]["hidden"] is False
    assert datasets[1]["hidden"] is True

    table = chart["table"]
    assert table[0]["effect"] == "scale"
    assert table[0]["ratio"] == "0.500x"
    assert table[1]["effect"] == "rotate"
    assert table[1]["ratio"] == "1.500x"

