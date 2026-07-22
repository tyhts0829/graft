from __future__ import annotations

from grafix.devtools.benchmarks.parameter_edit_benchmark import (
    make_parameter_edit_scenario,
    run_parameter_edit_scenario,
)
from grafix.devtools.benchmarks.catalog import case_definitions
from grafix.devtools.benchmarks.runner import run_case_isolated


def _parameters(*, rows: int = 100, changed_frames: int = 4) -> dict[str, int]:
    return {
        "rows": rows,
        "changed_frames": changed_frames,
    }


def test_single_key_changed_frame_reports_split_timing_and_hard_contracts() -> None:
    result = run_parameter_edit_scenario(make_parameter_edit_scenario(_parameters()))

    assert result.value == {
        "scope": "core+parameter-table-model(no-imgui)",
        "rows": 100,
        "changed_frames": 4,
        "single_key_changed": True,
        "single_row_refreshed": True,
        "structure_reused": True,
        "undo_correct": True,
        "redo_correct": True,
        "real_imgui_measured": False,
        "rss_or_allocations_measured": False,
    }
    assert result.contracts
    assert all(contract.passed for contract in result.contracts if contract.severity == "hard")

    metrics = {metric.name: metric for metric in result.metrics}
    for name in (
        "param_edit.changed_frame.total",
        "param_edit.changed_frame.history_patch",
        "param_edit.changed_frame.state_apply",
        "param_edit.changed_frame.value_sparse_refresh",
        "param_edit.changed_frame.table_structure_model_reuse",
        "param_edit.changed_frame.value_overlay_view",
    ):
        metric = metrics[name]
        assert metric.kind == "distribution"
        assert metric.unit == "ms"
        assert metric.phase == "drag"
        assert metric.distribution is not None
        assert metric.distribution.count == 4

    assert metrics["param_edit.changed_frame.full_snapshot_captures"].value == 0
    assert metrics["param_edit.changed_frame.table_model_builds"].value == 0
    assert metrics["param_edit.changed_frame.max_changed_keys"].value == 1
    assert metrics["param_edit.changed_frame.max_changed_row_identities"].value == 1
    assert metrics["param_edit.changed_frame.revision_delta"].value == 4
    assert metrics["param_edit.changed_frame.table_revision_delta"].value == 0
    assert metrics["param_edit.changed_frame.value_revision_delta"].value == 4
    assert metrics["param_edit.undo_redo.total_revision_delta"].value == 6


def test_parameter_edit_scenario_is_reusable_after_undo_redo() -> None:
    scenario = make_parameter_edit_scenario(_parameters())

    first = run_parameter_edit_scenario(scenario)
    second = run_parameter_edit_scenario(scenario)

    assert first.value == second.value
    assert all(contract.passed for contract in second.contracts)


def test_registry_scopes_parameter_edit_cases_for_smoke_gui_and_soak() -> None:
    definitions = {definition.case_id: definition for definition in case_definitions()}
    small = definitions["gui.parameter_edit.rows_100"]
    medium = definitions["gui.parameter_edit.rows_1000"]
    large = definitions["gui.parameter_edit.rows_10000"]

    assert small.parameters == {"rows": 100, "changed_frames": 12}
    assert small.selectable_suites == ("smoke", "gui")
    assert medium.parameters == {"rows": 1_000, "changed_frames": 12}
    assert medium.selectable_suites == ("gui",)
    assert large.parameters == {"rows": 10_000, "changed_frames": 6}
    assert large.selectable_suites == ("soak",)
    assert all("PARAM-01" in definition.tags for definition in (small, medium, large))


def test_formal_case_returns_typed_metrics_and_passes_contracts() -> None:
    definition = next(
        definition
        for definition in case_definitions()
        if definition.case_id == "gui.parameter_edit.rows_100"
    )

    result = run_case_isolated(
        definition,
        seed=0,
        mode="warm",
        samples=1,
        warmup=0,
        target_ns=0,
        disable_gc=False,
        timeout_seconds=30.0,
    )

    assert result.status == "ok", result.error
    assert result.metrics
    assert all(metric.kind in {"counter", "gauge", "distribution"} for metric in result.metrics)
    assert result.contracts
    assert all(contract.passed for contract in result.contracts)
