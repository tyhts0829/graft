"""
Purpose:
    解決済み benchmark definition の in-process/fresh-process 計測、calibration、timeout、child lifecycle を所有する。
Use when:
    measurement boundary、warm/cold sampling、child request/result protocol、timeout/kill/reap、RSS 計測を変更・調査する場合。
Constraints:
    - executor は catalog/workload provider を import せず、caller が解決した ``CaseDefinition`` だけを実行する。
    - timed loop と semantic postprocess/checksum を分け、child 結果の CaseSpec が parent request と exact に一致することを検証する。
    - timeout に限らずあらゆる ``BaseException`` で process group の kill/reap を試み、cleanup error で primary error を置き換えない。
Side effects:
    temporary request/result file を作り、fresh process group を起動・強制終了することがある。
"""

from __future__ import annotations

import gc
import json
import os
import resource
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from pathlib import Path
from typing import Any, cast

from grafix.core.resource_budget import ResourceLimitError
from grafix.devtools.benchmarks.definition import CaseDefinition
from grafix.devtools.benchmarks.metrics import (
    aggregate_measured_outputs,
    canonical_checksum,
    merge_cold_results,
)
from grafix.devtools.benchmarks.schema import (
    BenchmarkOutput,
    CaseResult,
    CaseSpec,
    Sample,
    case_result_from_dict,
    case_result_to_dict,
    summarize_samples,
)
from grafix.file_io import atomic_write_text

DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_CALIBRATION_ITERATIONS = 1 << 20
_CLEANUP_REAP_TIMEOUT_SECONDS = 1.0

ChildCommandFactory = Callable[[Path, Path], Sequence[str]]


def execute_case_isolated(
    definition: CaseDefinition,
    *,
    seed: int,
    mode: str,
    samples: int,
    warmup: int,
    target_ns: int,
    disable_gc: bool,
    child_command: ChildCommandFactory,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> CaseResult:
    """一つの case を fresh child process で実行する。"""

    if mode not in {"warm", "process-cold", "compile-cold"}:
        raise ValueError(f"unknown benchmark mode: {mode}")
    # Scenario workload は内部 frame の raw distribution と全 contract を
    # 1 output に集約する。外側で再実行して最後の output だけを採ると、
    # 先行 run の tail/failure が失われるため常に 1 semantic sample とする。
    sample_count = 1 if definition.self_sampling else max(1, int(samples))
    spec = definition.spec(seed=int(seed))
    if mode == "warm":
        return _run_child(
            definition=definition,
            spec=spec,
            seed=seed,
            mode=mode,
            samples=sample_count,
            warmup=max(0, int(warmup)),
            target_ns=max(0, int(target_ns)),
            disable_gc=disable_gc,
            child_command=child_command,
            timeout_seconds=timeout_seconds,
        )

    results = [
        _run_child(
            definition=definition,
            spec=spec,
            seed=seed,
            mode=mode,
            samples=1,
            warmup=0,
            target_ns=0,
            disable_gc=disable_gc,
            child_command=child_command,
            timeout_seconds=timeout_seconds,
        )
        for _ in range(sample_count)
    ]
    return merge_cold_results(spec=spec, results=results)


def read_child_request(request_path: Path) -> dict[str, Any]:
    """Parent が作った child request JSON object を読み込む。"""

    request = json.loads(request_path.read_text(encoding="utf-8"))
    if type(request) is not dict:
        raise ValueError("benchmark child request must be a JSON object")
    return cast(dict[str, Any], request)


def execute_child_request(
    definition: CaseDefinition,
    *,
    request: Mapping[str, Any],
    result_path: Path,
) -> int:
    """解決済み定義を process 内で計測し、result JSON を確定する。"""

    seed = int(request["seed"])
    spec = definition.spec(seed=seed)
    result = measure_in_process(
        definition,
        spec=spec,
        seed=seed,
        mode=str(request["mode"]),
        samples=max(1, int(request["samples"])),
        warmup=max(0, int(request["warmup"])),
        target_ns=max(0, int(request["target_ns"])),
        disable_gc=bool(request["disable_gc"]),
    )
    atomic_write_text(
        result_path,
        json.dumps(
            case_result_to_dict(result),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        ),
    )
    return 0


def measure_in_process(
    definition: CaseDefinition,
    *,
    spec: CaseSpec,
    seed: int,
    mode: str,
    samples: int,
    warmup: int,
    target_ns: int,
    disable_gc: bool,
) -> CaseResult:
    """解決済み定義を現在の process 内で計測する。"""

    try:
        state = definition.setup(definition.materialize_parameters(), int(seed))
        context = (
            nullcontext()
            if definition.measurement_context is None
            else definition.measurement_context(state)
        )
        suppressed: BaseException | None = None
        with context:
            try:
                return _measure_entered_context(
                    definition,
                    spec=spec,
                    state=state,
                    mode=mode,
                    samples=samples,
                    warmup=warmup,
                    target_ns=target_ns,
                    disable_gc=disable_gc,
                )
            except BaseException as exc:
                suppressed = exc
                raise
        assert suppressed is not None
        raise suppressed
    except ResourceLimitError as exc:
        return CaseResult(
            spec=spec,
            status="resource-limit",
            error=f"{type(exc).__name__}: {exc}",
        )
    except Exception as exc:  # noqa: BLE001
        primary = exc.__context__
        if primary is not None and not isinstance(primary, Exception):
            primary.add_note(
                f"measurement context teardown also failed: {type(exc).__name__}: {exc}"
            )
            raise primary
        return CaseResult(
            spec=spec,
            status="error",
            error=_exception_chain_text(exc),
        )


def _run_child(
    *,
    definition: CaseDefinition,
    spec: CaseSpec,
    seed: int,
    mode: str,
    samples: int,
    warmup: int,
    target_ns: int,
    disable_gc: bool,
    child_command: ChildCommandFactory,
    timeout_seconds: float,
) -> CaseResult:
    request = {
        "case_id": definition.case_id,
        "seed": int(seed),
        "mode": mode,
        "samples": int(samples),
        "warmup": int(warmup),
        "target_ns": int(target_ns),
        "disable_gc": bool(disable_gc),
    }
    with tempfile.TemporaryDirectory(prefix="grafix-benchmark-") as temp_name:
        temp = Path(temp_name)
        request_path = temp / "request.json"
        result_path = temp / "result.json"
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        environment = dict(os.environ)
        environment["PYTHONHASHSEED"] = "0"
        environment["PYTHONPYCACHEPREFIX"] = str(temp / "pycache")
        if mode == "compile-cold":
            cache_dir = temp / "numba-cache"
            cache_dir.mkdir()
            environment["NUMBA_CACHE_DIR"] = str(cache_dir)
        try:
            completed = run_isolated_process(
                list(child_command(request_path, result_path)),
                timeout=float(timeout_seconds),
                env=environment,
            )
        except subprocess.TimeoutExpired:
            return CaseResult(
                spec=spec,
                status="timeout",
                error=f"case exceeded {float(timeout_seconds):g}s",
            )
        if completed.returncode != 0 or not result_path.is_file():
            detail = completed.stderr.strip() or completed.stdout.strip()
            if len(detail) > 2_000:
                detail = detail[-2_000:]
            return CaseResult(
                spec=spec,
                status="error",
                error=(
                    f"child exited with code {completed.returncode}"
                    + (f": {detail}" if detail else "")
                ),
            )
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            return validated_child_result(payload, expected_spec=spec)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return CaseResult(
                spec=spec,
                status="error",
                error=f"invalid child result: {type(exc).__name__}: {exc}",
            )


def validated_child_result(
    payload: object,
    *,
    expected_spec: CaseSpec,
) -> CaseResult:
    """Child payload が request 時の case identity と完全一致することを確認する。"""

    result = case_result_from_dict(payload)
    if result.spec != expected_spec:
        raise ValueError("child result case spec differs from request")
    return result


def run_isolated_process(
    command: list[str],
    *,
    timeout: float,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """新しい process group で実行し、異常終了時は子孫を終了・回収する。"""

    process = subprocess.Popen(  # noqa: S603
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException as primary:
        _terminate_process_group(process, primary=primary)
        raise
    return subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout,
        stderr,
    )


def _terminate_process_group(
    process: subprocess.Popen[str],
    *,
    primary: BaseException,
) -> None:
    """計測 child の process group を強制終了し、元例外を保って reap する。"""

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except BaseException as exc:
        primary.add_note(_cleanup_failure_note("kill process group", exc))
        try:
            process.kill()
        except ProcessLookupError:
            pass
        except BaseException as kill_exc:
            primary.add_note(_cleanup_failure_note("kill child process", kill_exc))
    finally:
        try:
            process.communicate(timeout=_CLEANUP_REAP_TIMEOUT_SECONDS)
        except BaseException as exc:
            primary.add_note(_cleanup_failure_note("communicate while reaping", exc))
            try:
                process.wait(timeout=_CLEANUP_REAP_TIMEOUT_SECONDS)
            except BaseException as wait_exc:
                primary.add_note(_cleanup_failure_note("wait while reaping", wait_exc))


def _cleanup_failure_note(action: str, exc: BaseException) -> str:
    """Cleanup failure を primary exception の note 用文字列にする。"""

    return f"benchmark child cleanup failed ({action}): {type(exc).__name__}: {exc}"


def _exception_chain_text(exc: Exception) -> str:
    """例外 context を原因側から順に失わず表示する。"""

    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__context__
    chain.reverse()
    return "; while handling: ".join(f"{type(item).__name__}: {item}" for item in chain)


def _measure_entered_context(
    definition: CaseDefinition,
    *,
    spec: CaseSpec,
    state: object,
    mode: str,
    samples: int,
    warmup: int,
    target_ns: int,
    disable_gc: bool,
) -> CaseResult:
    """Measurement context 内で warmup/calibration/計測を実行する。"""

    setup_rss = _peak_rss_bytes()
    if definition.self_sampling:
        iterations = 1
        samples = 1
    elif mode == "warm":
        for _ in range(warmup):
            definition.workload(state)
        iterations = calibrate(
            definition.workload,
            state,
            target_ns=target_ns,
        )
    else:
        iterations = 1

    baseline_rss = _peak_rss_bytes()
    raw_samples: list[Sample] = []
    measured_outputs: list[BenchmarkOutput] = []
    semantic_checksum: tuple[str, str] | None = None
    output: BenchmarkOutput | None = None
    raw_output: object | None = None
    was_gc_enabled = gc.isenabled()
    if disable_gc and was_gc_enabled:
        gc.disable()
    try:
        for _ in range(samples):
            started = time.perf_counter_ns()
            for _iteration in range(iterations):
                raw_output = definition.workload(state)
            raw_samples.append(
                Sample(
                    elapsed_ns=time.perf_counter_ns() - started,
                    iterations=iterations,
                )
            )
            if raw_output is None:
                raise RuntimeError("benchmark workload returned no output")
            output = _postprocess_case_output(
                definition,
                state=state,
                raw_output=raw_output,
            )
            measured_outputs.append(
                BenchmarkOutput(
                    value=None,
                    metrics=output.metrics,
                    contracts=output.contracts,
                )
            )
            current_checksum = canonical_checksum(output.value)
            if semantic_checksum is None:
                semantic_checksum = current_checksum
            elif current_checksum != semantic_checksum:
                raise RuntimeError("warm samples produced different output checksums")
    finally:
        if disable_gc and was_gc_enabled:
            gc.enable()
    if output is None:
        raise RuntimeError("benchmark workload returned no output")
    output = aggregate_measured_outputs(
        measured_outputs,
        last=output,
    )
    peak_rss = _peak_rss_bytes()
    assert semantic_checksum is not None
    checksum, checksum_kind = semantic_checksum
    failed_hard = tuple(
        contract
        for contract in output.contracts
        if contract.severity == "hard" and not contract.passed
    )
    status = "contract-failure" if failed_hard else "ok"
    contract_error = (
        "failed hard contracts: "
        + "; ".join(f"{contract.contract_id}: {contract.reason}" for contract in failed_hard)
        if failed_hard
        else None
    )
    return CaseResult(
        spec=spec,
        status=status,
        samples=tuple(raw_samples),
        stats=summarize_samples(raw_samples),
        checksum=checksum,
        checksum_kind=checksum_kind,
        setup_rss_bytes=setup_rss,
        baseline_rss_bytes=baseline_rss,
        peak_rss_bytes=peak_rss,
        peak_rss_delta_bytes=max(0, peak_rss - baseline_rss),
        metrics=output.metrics,
        contracts=output.contracts,
        error=contract_error,
    )


def _postprocess_case_output(
    definition: CaseDefinition,
    *,
    state: object,
    raw_output: object,
) -> BenchmarkOutput:
    """Timed workload の raw output を計測区間外で semantic output にする。"""

    output = (
        definition.postprocess(state, raw_output)
        if definition.postprocess is not None
        else raw_output
    )
    if not isinstance(output, BenchmarkOutput):
        raise TypeError("benchmark workload must produce BenchmarkOutput")
    return output


def calibrate(
    workload: Callable[[object], object],
    state: object,
    *,
    target_ns: int,
) -> int:
    """Target duration を満たす反復回数を決める。"""

    if target_ns <= 0:
        return 1
    iterations = 1
    while True:
        started = time.perf_counter_ns()
        for _ in range(iterations):
            workload(state)
        elapsed = time.perf_counter_ns() - started
        if elapsed >= target_ns or iterations >= MAX_CALIBRATION_ITERATIONS:
            return iterations
        if elapsed <= 0:
            iterations *= 2
        else:
            estimate = max(iterations + 1, int(iterations * target_ns / elapsed))
            iterations = min(MAX_CALIBRATION_ITERATIONS, estimate)


def _peak_rss_bytes() -> int:
    rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return rss if sys.platform == "darwin" else rss * 1024


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "execute_case_isolated",
    "execute_child_request",
    "measure_in_process",
    "read_child_request",
]
