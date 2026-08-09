"""``python -m grafix benchmark`` の schema v4 CLI。"""

from __future__ import annotations

import argparse
import json
import math
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

from grafix.file_io import atomic_write_text
from grafix.devtools.benchmarks.compare import compare_run_files
from grafix.devtools.benchmarks.environment import (
    collect_environment_fingerprint,
    collect_source_identity,
)
from grafix.devtools.benchmarks.report import (
    BenchmarkReportDependencyError,
    write_report,
)
from grafix.devtools.benchmarks.catalog import (
    case_definitions,
    select_case_definitions,
)
from grafix.devtools.benchmarks.runner import run_case_isolated
from grafix.devtools.benchmarks.schema import (
    BenchmarkRun,
    RunMeta,
    freeze_json_object,
    materialize_json_object,
    write_benchmark_run,
)

_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_PROFILE_DEFAULTS = {
    "smoke": {"samples": 3, "warmup": 1, "target_ns": 1_000_000},
    "short": {"samples": 20, "warmup": 2, "target_ns": 10_000_000},
    "long": {"samples": 30, "warmup": 3, "target_ns": 50_000_000},
}


def main(argv: list[str] | None = None) -> int:
    """benchmark subcommand を実行する。"""

    args_list = list(sys.argv[1:] if argv is None else argv)
    parser = _parser()
    args = parser.parse_args(args_list)
    if args.action == "list":
        return _list_cases(args)
    if args.action == "run":
        return _run(args, argv=args_list)
    if args.action == "compare":
        return _compare(args)
    if args.action == "report":
        return _report(args)
    raise AssertionError(f"unknown benchmark action: {args.action!r}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m grafix benchmark")
    subparsers = parser.add_subparsers(dest="action", required=True)

    list_parser = subparsers.add_parser("list", help="case registry を表示する")
    list_parser.add_argument("--suite", action="append", default=[])
    list_parser.add_argument("--json", action="store_true", dest="as_json")

    run_parser = subparsers.add_parser("run", help="case ごとに fresh process で計測する")
    selection = run_parser.add_mutually_exclusive_group()
    selection.add_argument("--suite", action="append", default=[])
    selection.add_argument("--case", action="append", default=[])
    run_parser.add_argument(
        "--profile",
        choices=tuple(_PROFILE_DEFAULTS),
        default="short",
    )
    run_parser.add_argument(
        "--mode",
        choices=("warm", "process-cold", "compile-cold"),
        default="warm",
    )
    run_parser.add_argument("--samples", type=int)
    run_parser.add_argument("--warmup", type=int)
    run_parser.add_argument("--target-ms", type=float)
    run_parser.add_argument("--timeout", type=float, default=120.0)
    run_parser.add_argument("--seed", type=int, default=0)
    run_parser.add_argument("--disable-gc", action="store_true")
    run_parser.add_argument("--run-id", default="")
    run_parser.add_argument("--out", default="data/output/benchmarks")

    compare_parser = subparsers.add_parser("compare", help="base/head run を比較する")
    compare_parser.add_argument("base")
    compare_parser.add_argument("head")
    compare_parser.add_argument("--allow-incompatible", action="store_true")
    compare_parser.add_argument(
        "--metric",
        action="append",
        default=[],
        help="比較する typed metric 名。複数回またはカンマ区切りで指定する",
    )
    compare_parser.add_argument("--output")

    report_parser = subparsers.add_parser("report", help="offline HTML report を生成する")
    report_parser.add_argument("--out", default="data/output/benchmarks")
    return parser


def _list_cases(args: argparse.Namespace) -> int:
    suites = _split_values(tuple(args.suite))
    if args.suite and not suites:
        print("--suite に空でない値が必要です", file=sys.stderr)  # noqa: T201
        return 2
    try:
        definitions = (
            select_case_definitions(
                suites=suites or ("all",),
            )
            if args.suite
            else case_definitions()
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)  # noqa: T201
        return 2
    entries = [
        {
            "id": definition.case_id,
            "label": definition.label,
            "category": definition.category,
            "suite": definition.suite,
            "selectable_suites": list(definition.selectable_suites),
            "fixture": definition.fixture,
            "parameters": definition.materialize_parameters(),
            "checksum_policy": definition.checksum_policy,
            "tags": list(definition.tags),
        }
        for definition in definitions
    ]
    if bool(args.as_json):
        print(json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True))  # noqa: T201
    else:
        for entry in entries:
            suite_names = ",".join(entry["selectable_suites"])
            print(  # noqa: T201
                f"{entry['id']:<45} [{suite_names}] {entry['label']}"
            )
    return 0


def _run(args: argparse.Namespace, *, argv: list[str]) -> int:
    selected_suites = _split_values(tuple(args.suite))
    case_ids = _split_values(tuple(args.case))
    if args.suite and not selected_suites:
        print("--suite に空でない値が必要です", file=sys.stderr)  # noqa: T201
        return 2
    if args.case and not case_ids:
        print("--case に空でない値が必要です", file=sys.stderr)  # noqa: T201
        return 2
    suites = selected_suites or ("smoke",)
    try:
        definitions = select_case_definitions(
            suites=suites,
            case_ids=case_ids,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)  # noqa: T201
        return 2
    if not definitions:
        print("selected benchmark cases are empty", file=sys.stderr)  # noqa: T201
        return 2

    profile_defaults = _PROFILE_DEFAULTS[str(args.profile)]
    samples = int(profile_defaults["samples"]) if args.samples is None else int(args.samples)
    warmup = int(profile_defaults["warmup"]) if args.warmup is None else int(args.warmup)
    target_ms = (
        float(profile_defaults["target_ns"]) / 1_000_000.0
        if args.target_ms is None
        else float(args.target_ms)
    )
    timeout_seconds = float(args.timeout)
    if not math.isfinite(target_ms) or not math.isfinite(timeout_seconds) or target_ms < 0.0:
        print("target は有限な非負値、timeout は有限値である必要があります", file=sys.stderr)  # noqa: T201
        return 2
    target_ns = (
        int(profile_defaults["target_ns"])
        if args.target_ms is None
        else int(target_ms * 1_000_000.0)
    )
    if str(args.mode) != "warm" and (args.warmup is not None or args.target_ms is not None):
        print(
            "cold mode では --warmup/--target-ms を指定できません",
            file=sys.stderr,
        )  # noqa: T201
        return 2
    if samples < 1 or warmup < 0 or target_ns < 0 or timeout_seconds <= 0:
        print("samples/timeout は正、warmup/target は非負である必要があります", file=sys.stderr)  # noqa: T201
        return 2

    run_id = _run_id(str(args.run_id))
    out_root = Path(args.out).expanduser().resolve()
    destination = out_root / "runs" / f"{run_id}.json"
    if destination.exists():
        print(f"benchmark run already exists: {destination}", file=sys.stderr)  # noqa: T201
        return 2

    results = []
    for index, definition in enumerate(definitions, start=1):
        print(  # noqa: T201
            f"[grafix-bench] {index}/{len(definitions)} {definition.case_id}"
        )
        result = run_case_isolated(
            definition,
            seed=int(args.seed),
            mode=str(args.mode),
            samples=samples,
            warmup=warmup,
            target_ns=target_ns,
            disable_gc=bool(args.disable_gc),
            timeout_seconds=timeout_seconds,
        )
        results.append(result)

    warnings = tuple(
        f"{result.spec.case_id}: {result.status}: {result.error or ''}".rstrip()
        for result in results
        if result.status != "ok"
    )
    run = BenchmarkRun(
        meta=RunMeta(
            run_id=run_id,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            suite="explicit-cases" if case_ids else ",".join(suites),
            profile=str(args.profile),
            mode=str(args.mode),
            seed=int(args.seed),
            samples=samples,
            warmup=warmup if str(args.mode) == "warm" else 0,
            target_ns=target_ns if str(args.mode) == "warm" else 0,
            disable_gc=bool(args.disable_gc),
            timeout_seconds=timeout_seconds,
            argv=tuple(argv),
        ),
        source=collect_source_identity(root=Path(__file__).resolve().parent),
        environment=collect_environment_fingerprint(
            environment_overrides=_effective_child_environment(mode=str(args.mode))
        ),
        cases=tuple(results),
        warnings=warnings,
    )
    try:
        write_benchmark_run(destination, run)
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)  # noqa: T201
        return 2
    print(f"[grafix-bench] wrote: {destination}")  # noqa: T201
    return int(bool(warnings))


def _compare(args: argparse.Namespace) -> int:
    metric_names = _split_values(tuple(args.metric))
    if args.metric and not metric_names:
        print("--metric に空でない値が必要です", file=sys.stderr)  # noqa: T201
        return 2
    try:
        comparison = compare_run_files(
            args.base,
            args.head,
            allow_incompatible=bool(args.allow_incompatible),
            metric_names=metric_names,
        )
    except (OSError, ValueError) as exc:
        print(f"benchmark compare failed: {exc}", file=sys.stderr)  # noqa: T201
        return 2
    payload = {
        "base_run_id": comparison.base_run_id,
        "head_run_id": comparison.head_run_id,
        "environment_compatible": comparison.environment_compatible,
        "rows": [materialize_json_object(freeze_json_object(row)) for row in comparison.rows],
        "warnings": list(comparison.warnings),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        atomic_write_text(args.output, text)
        print(f"[grafix-bench] wrote: {Path(args.output).resolve()}")  # noqa: T201
    else:
        print(text, end="")  # noqa: T201
    checksum_failure = any(
        row["base_status"] == "ok" and row["head_status"] == "ok" and not row["checksum_equal"]
        for row in comparison.rows
    )
    status_failure = any(
        row["base_status"] != "ok" or row["head_status"] != "ok" for row in comparison.rows
    )
    hard_contract_failure = any(
        not row["base_hard_contracts_passed"] or not row["head_hard_contracts_passed"]
        for row in comparison.rows
    )
    return int(checksum_failure or status_failure or hard_contract_failure)


def _report(args: argparse.Namespace) -> int:
    try:
        artifacts = write_report(args.out)
    except BenchmarkReportDependencyError as exc:
        print(str(exc), file=sys.stderr)  # noqa: T201
        return 2
    print(f"[grafix-bench] wrote: {artifacts.report_path}")  # noqa: T201
    print(f"[grafix-bench] wrote: {artifacts.overview_path}")  # noqa: T201
    print(f"[grafix-bench] wrote: {artifacts.warnings_path}")  # noqa: T201
    for warning in artifacts.loaded.warnings:
        print(f"[grafix-bench] warning: {warning}", file=sys.stderr)  # noqa: T201
    if not artifacts.loaded.runs:
        print("no valid schema v4 runs found", file=sys.stderr)  # noqa: T201
        return 2
    hard_contract_failure = any(
        contract.severity == "hard" and not contract.passed
        for run in artifacts.loaded.runs
        for result in run.cases
        for contract in result.contracts
    )
    return int(hard_contract_failure)


def _split_values(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(item.strip() for value in values for item in value.split(",") if item.strip())


def _run_id(explicit: str) -> str:
    if explicit:
        if _RUN_ID_PATTERN.fullmatch(explicit) is None:
            raise SystemExit("--run-id は英数字で始まる英数字・_・.・- の 128 文字以内です")
        return explicit
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return f"{timestamp}_{secrets.token_hex(3)}"


def _effective_child_environment(*, mode: str) -> dict[str, str | None]:
    """runner が child で上書きする環境値を fingerprint 用に正規化する。"""

    overrides: dict[str, str | None] = {
        "PYTHONHASHSEED": "0",
        "PYTHONPYCACHEPREFIX": "<isolated-empty>",
    }
    if mode == "compile-cold":
        overrides["NUMBA_CACHE_DIR"] = "<isolated-empty>"
    return overrides


__all__ = ["main"]
