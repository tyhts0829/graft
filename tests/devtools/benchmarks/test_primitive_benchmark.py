from __future__ import annotations

import numpy as np
import pytest

from grafix.core.evaluation_context import current_external_dependency
from grafix.core.font_resources import FontResources, ResolvedFontLease
from grafix.devtools.benchmarks.primitive_benchmark import (
    PrimitiveBenchmarkState,
    primitive_measurement_context,
    primitive_benchmark_cases,
    run_raw_primitive,
    setup_primitive_benchmark,
)
from grafix.devtools.benchmarks.catalog import (
    case_definitions,
    select_case_definitions,
)
from grafix.devtools.benchmarks.runner import run_case_isolated
from grafix.devtools.benchmarks.schema import Metric

_BUILTIN_PRIMITIVES = {
    "arc",
    "asemic",
    "bezier",
    "circle",
    "ellipse",
    "grid",
    "laplace_field_grid",
    "line",
    "lissajous",
    "lsystem",
    "polygon",
    "polyhedron",
    "polyline",
    "rect",
    "sphere",
    "spiral",
    "spline",
    "text",
    "topographic_contours",
    "torus",
    "wave",
}
_COMMON_METRICS = {
    "primitive",
    "quality",
    "n_vertices",
    "n_lines",
    "closed_lines",
    "output_bytes",
    "diagnostics",
}


def test_primitive_case_arguments_are_owned_and_read_only() -> None:
    case = primitive_benchmark_cases()[0]
    with pytest.raises(TypeError):
        case.arguments["radius"] = 999.0  # type: ignore[index]

    parameters = case.parameters()
    parameters["arguments"]["radius"] = 999.0
    assert case.arguments["radius"] == 120.0


def test_primitive_suite_covers_every_builtin_with_direct_actual_work() -> None:
    definitions = select_case_definitions(suites=("primitives",))

    assert {definition.parameters["primitive"] for definition in definitions} == (
        _BUILTIN_PRIMITIVES
    )
    assert len(definitions) == 26
    for definition in definitions:
        assert definition.category == "primitive"
        assert definition.suite == "primitives"
        assert "primitives" in definition.selectable_suites
        assert {"actual-work", "direct-raw", "exact-checksum"} <= set(definition.tags)
        assert definition.postprocess is not None

    cold_ids = {
        definition.case_id for definition in select_case_definitions(suites=("primitive-cold",))
    }
    assert cold_ids == {
        "primitive.asemic.cold_unique_bezier",
        "primitive.text.cold_unique_high_quality",
    }


@pytest.mark.parametrize(
    "case_id",
    tuple(definition.case_id for definition in select_case_definitions(suites=("primitives",))),
)
def test_each_primitive_direct_case_emits_common_metrics_and_hard_contracts(
    case_id: str,
) -> None:
    definition = next(
        definition
        for definition in select_case_definitions(suites=("primitives",))
        if definition.case_id == case_id
    )
    primitive = str(definition.parameters["primitive"])
    state = definition.setup(definition.materialize_parameters(), 20260719)
    assert isinstance(state, PrimitiveBenchmarkState)
    assert state.raw_function.__module__ == f"grafix.core.primitives.{primitive}"
    assert state.raw_function.__name__ == primitive

    with primitive_measurement_context(state):
        raw_output = definition.workload(state)
        assert isinstance(raw_output, tuple)
        assert len(raw_output) == 2
        coords, offsets = raw_output
        assert isinstance(coords, np.ndarray) and coords.flags.writeable
        assert isinstance(offsets, np.ndarray) and offsets.flags.writeable

        assert definition.postprocess is not None
        output = definition.postprocess(state, raw_output)
    assert output.value.coords.dtype == np.float32
    assert output.value.offsets.dtype == np.int32
    assert all(isinstance(metric, Metric) for metric in output.metrics)
    metrics = {metric.name: metric for metric in output.metrics}
    assert _COMMON_METRICS <= set(metrics)
    assert metrics["n_vertices"].kind == "counter"
    assert metrics["n_vertices"].unit == "count"
    assert metrics["output_bytes"].kind == "counter"
    assert metrics["output_bytes"].unit == "bytes"
    assert output.contracts
    assert all(contract.severity == "hard" for contract in output.contracts)
    assert all(contract.passed for contract in output.contracts)
    assert any(
        contract.contract_id == f"primitive.{primitive}.exact_checksum"
        for contract in output.contracts
    )


def _text_benchmark_state() -> PrimitiveBenchmarkState:
    case = next(case for case in primitive_benchmark_cases() if case.primitive == "text")
    return setup_primitive_benchmark(case.parameters(), 20260719)


def test_topographic_contours_case_reports_field_work_metrics() -> None:
    definition = next(
        definition
        for definition in select_case_definitions(suites=("primitives",))
        if definition.parameters["primitive"] == "topographic_contours"
    )
    state = definition.setup(definition.materialize_parameters(), 20260719)

    with primitive_measurement_context(state):
        raw_output = definition.workload(state)
        assert definition.postprocess is not None
        output = definition.postprocess(state, raw_output)
    metrics = {metric.name: metric for metric in output.metrics}

    work_metric_names = {
        "work.grid_points",
        "work.focus_count",
        "work.level_count",
        "work.output_paths",
    }
    assert all(metrics[name].kind == "counter" for name in work_metric_names)
    assert all(metrics[name].unit == "count" for name in work_metric_names)
    assert metrics["work.grid_points"].value == 81 * 101
    assert metrics["work.focus_count"].value == 6
    assert metrics["work.level_count"].value == 13
    assert metrics["work.output_paths"].value == metrics["n_lines"].value


def test_text_measurement_context_binds_lease_and_closes_owner() -> None:
    state = _text_benchmark_state()
    owner = state.font_resources
    lease = state.font_lease

    assert owner is not None
    assert lease is not None
    assert not owner.closed
    with primitive_measurement_context(state):
        assert current_external_dependency(ResolvedFontLease) is lease
        output = run_raw_primitive(state)
        assert isinstance(output, tuple)

    assert owner.closed
    with pytest.raises(RuntimeError, match="preflight"):
        current_external_dependency(ResolvedFontLease)


def test_text_measurement_context_closes_owner_after_workload_error() -> None:
    state = _text_benchmark_state()
    owner = state.font_resources
    assert owner is not None

    def fail(**_arguments: object) -> tuple[np.ndarray, np.ndarray]:
        raise RuntimeError("raw text failed")

    state.raw_function = fail
    with pytest.raises(RuntimeError, match="raw text failed"):
        with primitive_measurement_context(state):
            run_raw_primitive(state)

    assert owner.closed


def test_text_setup_closes_owner_when_font_resolve_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owners: list[FontResources] = []

    def fail_resolve(
        owner: FontResources,
        *_args: object,
        **_kwargs: object,
    ) -> ResolvedFontLease:
        owners.append(owner)
        raise RuntimeError("font resolve failed")

    monkeypatch.setattr(FontResources, "resolve", fail_resolve)
    case = next(case for case in primitive_benchmark_cases() if case.primitive == "text")

    with pytest.raises(RuntimeError, match="font resolve failed"):
        setup_primitive_benchmark(case.parameters(), 20260719)

    assert len(owners) == 1
    assert owners[0].closed


def test_primitive_case_runs_warm_in_isolated_process() -> None:
    definition = next(
        definition
        for definition in case_definitions()
        if definition.case_id == "primitive.arc.segments_512"
    )
    result = run_case_isolated(
        definition,
        seed=20260719,
        mode="warm",
        samples=2,
        warmup=1,
        target_ns=0,
        disable_gc=False,
        timeout_seconds=30.0,
    )

    assert result.status == "ok", result.error
    assert result.checksum_kind == "realized_geometry_exact_v1"
    assert result.checksum
    assert len(result.samples) == 2
    assert result.contracts
    assert all(contract.passed for contract in result.contracts)
    metrics = {metric.name: metric.value for metric in result.metrics}
    assert metrics["n_vertices"] == 513
    assert metrics["n_lines"] == 1
    assert metrics["diagnostics"] == 0
