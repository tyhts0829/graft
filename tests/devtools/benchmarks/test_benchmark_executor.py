from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from collections.abc import Iterator

import pytest

from grafix.devtools.benchmarks import (
    executor,
)
from grafix.devtools.benchmarks.catalog import (
    case_definitions,
)
from grafix.devtools.benchmarks.schema import (
    BenchmarkOutput,
    CaseResult,
    Metric,
    case_result_to_dict,
    evaluate_contract,
)


def test_isolated_process_timeout_kills_the_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class FakeProcess:
        pid = 4242
        returncode = -signal.SIGKILL

        def communicate(self, *, timeout: float | None = None) -> tuple[str, str]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise subprocess.TimeoutExpired(
                    ["benchmark-child"],
                    0.0 if timeout is None else timeout,
                )
            return "", ""

    started: dict[str, object] = {}

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        started["command"] = command
        started.update(kwargs)
        return FakeProcess()

    killed: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(executor.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        os,
        "killpg",
        lambda pid, sig: killed.append((pid, sig)),
    )

    with pytest.raises(subprocess.TimeoutExpired):
        executor.run_isolated_process(
            ["benchmark-child"],
            timeout=0.1,
            env={},
        )

    assert started["start_new_session"] is True
    assert killed == [(4242, signal.SIGKILL)]
    assert calls == 2


def test_isolated_process_base_exception_kills_and_reaps_the_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class Cancelled(BaseException):
        pass

    class FakeProcess:
        pid = 4243
        returncode = -signal.SIGKILL

        def communicate(self, *, timeout: float | None = None) -> tuple[str, str]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise Cancelled
            return "", ""

    monkeypatch.setattr(
        executor.subprocess,
        "Popen",
        lambda _command, **_kwargs: FakeProcess(),
    )
    killed: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        executor.os,
        "killpg",
        lambda pid, sig: killed.append((pid, sig)),
    )

    with pytest.raises(Cancelled):
        executor.run_isolated_process(
            ["benchmark-child"],
            timeout=0.1,
            env={},
        )

    assert killed == [(4243, signal.SIGKILL)]
    assert calls == 2


def test_isolated_process_preserves_primary_when_killpg_fails_and_still_reaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    direct_kills = 0
    alive = True

    class Cancelled(BaseException):
        pass

    primary = Cancelled("cancelled")

    class FakeProcess:
        pid = 4244
        returncode = None

        def communicate(self, *, timeout: float | None = None) -> tuple[str, str]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise primary
            if alive:
                raise AssertionError("live child would block communicate")
            return "", ""

        def kill(self) -> None:
            nonlocal alive, direct_kills
            direct_kills += 1
            alive = False

    def fail_killpg(_pid: int, _sig: signal.Signals) -> None:
        raise PermissionError("killpg denied")

    monkeypatch.setattr(
        executor.subprocess,
        "Popen",
        lambda _command, **_kwargs: FakeProcess(),
    )
    monkeypatch.setattr(executor.os, "killpg", fail_killpg)

    with pytest.raises(Cancelled) as caught:
        executor.run_isolated_process(
            ["benchmark-child"],
            timeout=0.1,
            env={},
        )

    assert caught.value is primary
    assert calls == 2
    assert direct_kills == 1
    assert alive is False
    assert caught.value.__notes__ == [
        "benchmark child cleanup failed (kill process group): PermissionError: killpg denied"
    ]


def test_isolated_process_preserves_primary_when_reap_communicate_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    waits = 0

    class Cancelled(BaseException):
        pass

    class ReapInterrupted(BaseException):
        pass

    primary = Cancelled("cancelled")

    class FakeProcess:
        pid = 4245
        returncode = -signal.SIGKILL

        def communicate(self, *, timeout: float | None = None) -> tuple[str, str]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise primary
            raise ReapInterrupted("reap interrupted")

        def wait(self, *, timeout: float | None = None) -> int:
            nonlocal waits
            waits += 1
            assert timeout is not None
            return self.returncode

    monkeypatch.setattr(
        executor.subprocess,
        "Popen",
        lambda _command, **_kwargs: FakeProcess(),
    )
    monkeypatch.setattr(executor.os, "killpg", lambda _pid, _sig: None)

    with pytest.raises(Cancelled) as caught:
        executor.run_isolated_process(
            ["benchmark-child"],
            timeout=0.1,
            env={},
        )

    assert caught.value is primary
    assert calls == 2
    assert waits == 1
    assert caught.value.__notes__ == [
        "benchmark child cleanup failed (communicate while reaping): "
        "ReapInterrupted: reap interrupted"
    ]


def test_isolated_process_preserves_primary_when_all_reap_attempts_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    waits = 0

    class Cancelled(BaseException):
        pass

    primary = Cancelled("cancelled")

    class FakeProcess:
        pid = 4246
        returncode = None

        def communicate(self, *, timeout: float | None = None) -> tuple[str, str]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise primary
            raise RuntimeError("communicate failed")

        def wait(self, *, timeout: float | None = None) -> int:
            nonlocal waits
            waits += 1
            assert timeout is not None
            raise RuntimeError("wait failed")

    monkeypatch.setattr(
        executor.subprocess,
        "Popen",
        lambda _command, **_kwargs: FakeProcess(),
    )
    monkeypatch.setattr(executor.os, "killpg", lambda _pid, _sig: None)

    with pytest.raises(Cancelled) as caught:
        executor.run_isolated_process(
            ["benchmark-child"],
            timeout=0.1,
            env={},
        )

    assert caught.value is primary
    assert calls == 2
    assert waits == 1
    assert caught.value.__notes__ == [
        "benchmark child cleanup failed (communicate while reaping): "
        "RuntimeError: communicate failed",
        "benchmark child cleanup failed (wait while reaping): RuntimeError: wait failed",
    ]


def test_isolated_process_bounds_reap_when_group_and_direct_kill_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    communicate_timeouts: list[float | None] = []
    wait_timeouts: list[float | None] = []

    class Cancelled(BaseException):
        pass

    primary = Cancelled("cancelled")

    class FakeProcess:
        pid = 4247
        returncode = None

        def communicate(self, *, timeout: float | None = None) -> tuple[str, str]:
            communicate_timeouts.append(timeout)
            if len(communicate_timeouts) == 1:
                raise primary
            raise subprocess.TimeoutExpired(["benchmark-child"], timeout)

        def kill(self) -> None:
            raise PermissionError("direct kill denied")

        def wait(self, *, timeout: float | None = None) -> int:
            wait_timeouts.append(timeout)
            raise subprocess.TimeoutExpired(["benchmark-child"], timeout)

    def fail_killpg(_pid: int, _sig: signal.Signals) -> None:
        raise PermissionError("killpg denied")

    monkeypatch.setattr(
        executor.subprocess,
        "Popen",
        lambda _command, **_kwargs: FakeProcess(),
    )
    monkeypatch.setattr(executor.os, "killpg", fail_killpg)

    with pytest.raises(Cancelled) as caught:
        executor.run_isolated_process(
            ["benchmark-child"],
            timeout=0.1,
            env={},
        )

    assert caught.value is primary
    assert communicate_timeouts[0] == 0.1
    assert communicate_timeouts[1] is not None
    assert wait_timeouts == [communicate_timeouts[1]]
    assert caught.value.__notes__ == [
        "benchmark child cleanup failed (kill process group): PermissionError: killpg denied",
        "benchmark child cleanup failed (kill child process): PermissionError: direct kill denied",
        "benchmark child cleanup failed (communicate while reaping): "
        "TimeoutExpired: Command '['benchmark-child']' timed out after 1.0 seconds",
        "benchmark child cleanup failed (wait while reaping): "
        "TimeoutExpired: Command '['benchmark-child']' timed out after 1.0 seconds",
    ]


def test_sigint_cancellation_leaves_no_benchmark_child_processes(tmp_path: Path) -> None:
    pid_path = tmp_path / "child-pids.txt"
    child_code = "\n".join(
        (
            "import os, subprocess, sys, time",
            "grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])",
            "open(sys.argv[1], 'w', encoding='utf-8').write(f'{os.getpid()} {grandchild.pid}')",
            "time.sleep(30)",
        )
    )
    helper_code = "\n".join(
        (
            "import os, sys",
            "from grafix.devtools.benchmarks.executor import run_isolated_process",
            "command = [sys.executable, '-c', sys.argv[2], sys.argv[1]]",
            "run_isolated_process(command, timeout=30.0, env=dict(os.environ))",
        )
    )
    helper = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", helper_code, str(pid_path), child_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=dict(os.environ),
    )
    child_pids: tuple[int, ...] = ()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not pid_path.is_file():
            if helper.poll() is not None:
                break
            time.sleep(0.01)
        assert pid_path.is_file(), helper.communicate(timeout=1.0)
        child_pids = tuple(int(value) for value in pid_path.read_text().split())

        os.kill(helper.pid, signal.SIGINT)
        _stdout, stderr = helper.communicate(timeout=5.0)

        assert helper.returncode != 0
        assert "KeyboardInterrupt" in stderr
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and any(_process_exists(pid) for pid in child_pids):
            time.sleep(0.01)
        assert not any(_process_exists(pid) for pid in child_pids)
    finally:
        if helper.poll() is None:
            helper.kill()
            helper.communicate()
        for pid in child_pids:
            if _process_exists(pid):
                os.kill(pid, signal.SIGKILL)


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_child_result_must_match_the_requested_case_spec() -> None:
    definition = next(
        definition
        for definition in case_definitions()
        if definition.case_id == "core.concat_recipe.parts_10"
    )
    expected = definition.spec(seed=0)
    wrong = CaseResult(
        spec=replace(expected, label="wrong case"),
        status="error",
        error="synthetic",
    )

    with pytest.raises(ValueError, match="case spec differs"):
        executor.validated_child_result(
            json.loads(json.dumps(case_result_to_dict(wrong))),
            expected_spec=expected,
        )


def test_pipeline_measurement_uses_the_setup_evaluation_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """timed workload 内で config discovery/catalog composition をやり直さない。"""

    from grafix.core import operation_catalog as catalog_module
    from grafix import runtime_config_loader as config_module

    definition = next(
        definition
        for definition in case_definitions()
        if definition.case_id == "pipeline.draw_realize_indices.small"
    )
    state = definition.setup(dict(definition.parameters), 0)

    def unexpected_call(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("measurement 内で composition を再構築しました")

    monkeypatch.setattr(config_module, "runtime_config", unexpected_call)
    monkeypatch.setattr(
        catalog_module,
        "compose_operation_catalogs",
        unexpected_call,
    )

    output = definition.workload(state)

    assert isinstance(output, BenchmarkOutput)
    assert output.value["coords"].shape[0] > 0


def test_typed_metric_output_preserves_hard_contract_failure() -> None:
    definition = next(
        definition
        for definition in case_definitions()
        if definition.case_id == "core.concat_recipe.parts_10"
    )
    failed = evaluate_contract(
        contract_id="synthetic.hard",
        severity="hard",
        actual=False,
        comparator="eq",
        limit=True,
        reason="synthetic hard guardrail",
    )
    failing_definition = replace(
        definition,
        setup=lambda _parameters, _seed: None,
        workload=lambda _state: BenchmarkOutput(
            value={"ok": True},
            metrics=(
                Metric(
                    name="interactive_target_met",
                    kind="gauge",
                    unit="boolean",
                    phase="measure",
                    scope="test",
                    value=False,
                ),
            ),
            contracts=(failed,),
        ),
    )
    result = executor.measure_in_process(
        failing_definition,
        spec=definition.spec(seed=0),
        seed=0,
        mode="warm",
        samples=1,
        warmup=0,
        target_ns=0,
        disable_gc=False,
    )

    assert result.status == "contract-failure"
    assert result.samples
    assert result.checksum
    assert result.contracts == (failed,)
    assert "synthetic.hard" in (result.error or "")


@pytest.mark.parametrize(
    "stage",
    ["setup", "context", "warmup", "workload", "postprocess"],
)
def test_import_error_at_any_benchmark_stage_is_an_error(stage: str) -> None:
    definition = next(
        item for item in case_definitions() if item.case_id == "core.concat_recipe.parts_10"
    )

    def fail_import(*_args: object, **_kwargs: object) -> object:
        raise ImportError(f"{stage} import failed")

    @contextmanager
    def failing_context(_state: object) -> Iterator[None]:
        raise ImportError("context import failed")
        yield

    calls = 0

    def workload(_state: object) -> object:
        nonlocal calls
        calls += 1
        if stage == "warmup" and calls == 1:
            raise ImportError("warmup import failed")
        if stage == "workload":
            raise ImportError("workload import failed")
        return {"ok": True}

    tested = replace(
        definition,
        setup=(fail_import if stage == "setup" else lambda _params, _seed: None),
        workload=workload,
        postprocess=(
            fail_import
            if stage == "postprocess"
            else lambda _state, output: BenchmarkOutput(value=output)
        ),
        measurement_context=(failing_context if stage == "context" else None),
    )
    result = executor.measure_in_process(
        tested,
        spec=definition.spec(seed=0),
        seed=0,
        mode="warm",
        samples=1,
        warmup=1 if stage == "warmup" else 0,
        target_ns=0,
        disable_gc=False,
    )

    assert result.status == "error"
    assert "ImportError" in (result.error or "")


def test_measurement_context_receives_workload_error() -> None:
    definition = next(
        item for item in case_definitions() if item.case_id == "core.concat_recipe.parts_10"
    )
    received: list[type[BaseException] | None] = []

    class RecordingContext:
        def __enter__(self) -> None:
            return None

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _traceback: object,
        ) -> bool:
            received.append(exc_type)
            return False

    def workload(_state: object) -> object:
        raise RuntimeError("workload failed")

    result = executor.measure_in_process(
        replace(
            definition,
            setup=lambda _parameters, _seed: None,
            workload=workload,
            measurement_context=lambda _state: RecordingContext(),
        ),
        spec=definition.spec(seed=0),
        seed=0,
        mode="warm",
        samples=1,
        warmup=0,
        target_ns=0,
        disable_gc=False,
    )

    assert result.status == "error"
    assert received == [RuntimeError]


def test_measurement_context_exit_error_is_a_case_error() -> None:
    definition = next(
        item for item in case_definitions() if item.case_id == "core.concat_recipe.parts_10"
    )

    class FailingExitContext:
        def __enter__(self) -> None:
            return None

        def __exit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _traceback: object,
        ) -> bool:
            raise RuntimeError("context exit failed")

    result = executor.measure_in_process(
        replace(
            definition,
            setup=lambda _parameters, _seed: None,
            workload=lambda _state: BenchmarkOutput(value={"ok": True}),
            postprocess=None,
            measurement_context=lambda _state: FailingExitContext(),
        ),
        spec=definition.spec(seed=0),
        seed=0,
        mode="warm",
        samples=1,
        warmup=0,
        target_ns=0,
        disable_gc=False,
    )

    assert result.status == "error"
    assert result.error == "RuntimeError: context exit failed"


def test_measurement_context_cannot_suppress_a_benchmark_error() -> None:
    definition = next(
        item for item in case_definitions() if item.case_id == "core.concat_recipe.parts_10"
    )

    @contextmanager
    def suppressing_context(_state: object) -> Iterator[None]:
        try:
            yield
        except RuntimeError:
            return

    def workload(_state: object) -> object:
        raise RuntimeError("workload failed")

    result = executor.measure_in_process(
        replace(
            definition,
            setup=lambda _parameters, _seed: None,
            workload=workload,
            measurement_context=suppressing_context,
        ),
        spec=definition.spec(seed=0),
        seed=0,
        mode="warm",
        samples=1,
        warmup=0,
        target_ns=0,
        disable_gc=False,
    )

    assert result.status == "error"
    assert result.error == "RuntimeError: workload failed"


def test_measurement_context_cannot_suppress_base_exception() -> None:
    definition = next(
        item for item in case_definitions() if item.case_id == "core.concat_recipe.parts_10"
    )

    class SuppressingContext:
        def __enter__(self) -> None:
            return None

        def __exit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _traceback: object,
        ) -> bool:
            return True

    def workload(_state: object) -> object:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        executor.measure_in_process(
            replace(
                definition,
                setup=lambda _parameters, _seed: None,
                workload=workload,
                measurement_context=lambda _state: SuppressingContext(),
            ),
            spec=definition.spec(seed=0),
            seed=0,
            mode="warm",
            samples=1,
            warmup=0,
            target_ns=0,
            disable_gc=False,
        )


def test_measurement_context_receives_and_does_not_swallow_base_exception() -> None:
    definition = next(
        item for item in case_definitions() if item.case_id == "core.concat_recipe.parts_10"
    )
    received: list[type[BaseException] | None] = []

    class RecordingContext:
        def __enter__(self) -> None:
            return None

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _traceback: object,
        ) -> bool:
            received.append(exc_type)
            return False

    def workload(_state: object) -> object:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        executor.measure_in_process(
            replace(
                definition,
                setup=lambda _parameters, _seed: None,
                workload=workload,
                measurement_context=lambda _state: RecordingContext(),
            ),
            spec=definition.spec(seed=0),
            seed=0,
            mode="warm",
            samples=1,
            warmup=0,
            target_ns=0,
            disable_gc=False,
        )

    assert received == [KeyboardInterrupt]


def test_measurement_context_preserves_primary_and_teardown_errors() -> None:
    definition = next(
        item for item in case_definitions() if item.case_id == "core.concat_recipe.parts_10"
    )

    class FailingExitContext:
        def __enter__(self) -> None:
            return None

        def __exit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _traceback: object,
        ) -> bool:
            raise RuntimeError("teardown failed")

    def workload(_state: object) -> object:
        raise ValueError("workload failed")

    result = executor.measure_in_process(
        replace(
            definition,
            setup=lambda _parameters, _seed: None,
            workload=workload,
            measurement_context=lambda _state: FailingExitContext(),
        ),
        spec=definition.spec(seed=0),
        seed=0,
        mode="warm",
        samples=1,
        warmup=0,
        target_ns=0,
        disable_gc=False,
    )

    assert result.status == "error"
    assert result.error == (
        "ValueError: workload failed; while handling: RuntimeError: teardown failed"
    )


def test_measurement_context_teardown_cannot_mask_base_exception() -> None:
    definition = next(
        item for item in case_definitions() if item.case_id == "core.concat_recipe.parts_10"
    )

    class FailingExitContext:
        def __enter__(self) -> None:
            return None

        def __exit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _traceback: object,
        ) -> bool:
            raise RuntimeError("teardown failed")

    def workload(_state: object) -> object:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt) as exc_info:
        executor.measure_in_process(
            replace(
                definition,
                setup=lambda _parameters, _seed: None,
                workload=workload,
                measurement_context=lambda _state: FailingExitContext(),
            ),
            spec=definition.spec(seed=0),
            seed=0,
            mode="warm",
            samples=1,
            warmup=0,
            target_ns=0,
            disable_gc=False,
        )

    assert exc_info.value.__notes__ == [
        "measurement context teardown also failed: RuntimeError: teardown failed"
    ]


def test_case_output_rejects_non_tuple_and_duplicate_metric_names() -> None:
    metric = Metric(
        name="value",
        kind="gauge",
        unit="count",
        phase="measure",
        scope="test",
        value=1,
    )
    with pytest.raises(TypeError, match="tuple"):
        BenchmarkOutput(
            value=None,
            metrics={"value": 1},  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="tuple"):
        BenchmarkOutput(
            value=None,
            metrics=[metric],  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="一意"):
        BenchmarkOutput(
            value=None,
            metrics=(metric, replace(metric, phase="settle")),
        )


def test_warm_samples_preserve_an_earlier_hard_contract_failure() -> None:
    definition = next(
        definition
        for definition in case_definitions()
        if definition.case_id == "core.concat_recipe.parts_10"
    )
    calls = 0

    def workload(_state: object) -> BenchmarkOutput:
        nonlocal calls
        calls += 1
        contract = evaluate_contract(
            contract_id="synthetic.across-samples",
            severity="hard",
            actual=calls > 1,
            comparator="eq",
            limit=True,
            reason="all outer samples must pass",
        )
        return BenchmarkOutput(
            value={"stable": True},
            metrics=(
                Metric(
                    name="stable",
                    kind="gauge",
                    unit="count",
                    phase="measure",
                    scope="test",
                    value=1,
                ),
            ),
            contracts=(contract,),
        )

    result = executor.measure_in_process(
        replace(
            definition,
            setup=lambda _parameters, _seed: None,
            workload=workload,
        ),
        spec=definition.spec(seed=0),
        seed=0,
        mode="warm",
        samples=3,
        warmup=0,
        target_ns=0,
        disable_gc=False,
    )

    assert result.status == "contract-failure"
    assert result.contracts[0].passed is False


def test_measurement_context_wraps_warmup_samples_and_postprocess() -> None:
    definition = next(
        definition
        for definition in case_definitions()
        if definition.case_id == "core.concat_recipe.parts_10"
    )
    events: list[str] = []
    state = {"inside": False}

    @contextmanager
    def measurement_context(_state: object) -> Iterator[None]:
        events.append("enter")
        state["inside"] = True
        try:
            yield
        finally:
            state["inside"] = False
            events.append("exit")

    def workload(_state: object) -> object:
        assert state["inside"] is True
        events.append("workload")
        return {"stable": True}

    def postprocess(_state: object, output: object) -> BenchmarkOutput:
        assert state["inside"] is True
        events.append("postprocess")
        return BenchmarkOutput(value=output)

    result = executor.measure_in_process(
        replace(
            definition,
            setup=lambda _parameters, _seed: state,
            workload=workload,
            postprocess=postprocess,
            measurement_context=measurement_context,
        ),
        spec=definition.spec(seed=0),
        seed=0,
        mode="warm",
        samples=2,
        warmup=1,
        target_ns=0,
        disable_gc=False,
    )

    assert result.status == "ok", result.error
    assert events == [
        "enter",
        "workload",
        "workload",
        "postprocess",
        "workload",
        "postprocess",
        "exit",
    ]
    assert state["inside"] is False


def test_warm_samples_reject_semantic_or_typed_metric_drift() -> None:
    definition = next(
        definition
        for definition in case_definitions()
        if definition.case_id == "core.concat_recipe.parts_10"
    )
    calls = 0

    def changing_output(_state: object) -> BenchmarkOutput:
        nonlocal calls
        calls += 1
        return BenchmarkOutput(value={"sample": calls})

    checksum_result = executor.measure_in_process(
        replace(
            definition,
            setup=lambda _parameters, _seed: None,
            workload=changing_output,
        ),
        spec=definition.spec(seed=0),
        seed=0,
        mode="warm",
        samples=2,
        warmup=0,
        target_ns=0,
        disable_gc=False,
    )
    assert checksum_result.status == "error"
    assert "different output checksums" in (checksum_result.error or "")

    calls = 0

    def changing_metric(_state: object) -> BenchmarkOutput:
        nonlocal calls
        calls += 1
        return BenchmarkOutput(
            value={"stable": True},
            metrics=(
                Metric(
                    name="changing",
                    kind="gauge",
                    unit="count",
                    phase="measure",
                    scope="test",
                    value=calls,
                ),
            ),
        )

    metric_result = executor.measure_in_process(
        replace(
            definition,
            setup=lambda _parameters, _seed: None,
            workload=changing_metric,
        ),
        spec=definition.spec(seed=0),
        seed=0,
        mode="warm",
        samples=2,
        warmup=0,
        target_ns=0,
        disable_gc=False,
    )
    assert metric_result.status == "error"
    assert "typed metrics changed" in (metric_result.error or "")
