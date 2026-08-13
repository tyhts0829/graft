"""
Purpose:
    benchmark catalog と process executor を接続し、fresh child が case ID を解決できる最小の composition/entrypoint を提供する。
Use when:
    public isolated-run API、parent/child command 配線、child 側の case 解決を変更・調査する場合。
Constraints:
    - 公開 surface は ``run_case_isolated`` だけとする。
    - workload、metrics、measurement/process supervision の実装を runner へ戻さない。
    - 旧 runner private symbol の alias/re-export shim を作らない。
See:
    architecture.md §12
"""

from __future__ import annotations

import sys
from pathlib import Path

from grafix.devtools.benchmarks.catalog import definition_for_case
from grafix.devtools.benchmarks.definition import CaseDefinition
from grafix.devtools.benchmarks.executor import (
    DEFAULT_TIMEOUT_SECONDS,
    execute_case_isolated,
    execute_child_request,
    read_child_request,
)
from grafix.devtools.benchmarks.schema import CaseResult


def run_case_isolated(
    definition: CaseDefinition,
    *,
    seed: int,
    mode: str,
    samples: int,
    warmup: int,
    target_ns: int,
    disable_gc: bool,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> CaseResult:
    """一つの定義を benchmark child entrypoint で隔離実行する。"""

    return execute_case_isolated(
        definition,
        seed=seed,
        mode=mode,
        samples=samples,
        warmup=warmup,
        target_ns=target_ns,
        disable_gc=disable_gc,
        timeout_seconds=timeout_seconds,
        child_command=_child_command,
    )


def _child_command(request_path: Path, result_path: Path) -> tuple[str, ...]:
    return (
        sys.executable,
        "-m",
        "grafix.devtools.benchmarks.runner",
        "--child",
        str(request_path),
        str(result_path),
    )


def _child_main(request_path: Path, result_path: Path) -> int:
    request = read_child_request(request_path)
    definition = definition_for_case(str(request["case_id"]))
    return execute_child_request(
        definition,
        request=request,
        result_path=result_path,
    )


def _main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] != "--child":
        raise SystemExit("runner is an internal child entry point")
    return _child_main(Path(argv[1]), Path(argv[2]))


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))


__all__ = ["run_case_isolated"]
