from __future__ import annotations

import pytest

from grafix.devtools.benchmarks.parameter_hotpath_benchmark import (
    make_parameter_hot_path_scenario,
    parameter_snapshot_model_workload,
    parameter_store_fixture,
    run_parameter_hot_path_scenario,
)
from grafix.devtools.benchmarks.catalog import case_definitions


@pytest.mark.parametrize(
    ("operation", "expected_metric"),
    (
        ("layout_reuse", "parameter_layout.stable_reuse"),
        ("merge_runtime_one", "parameter_merge.runtime_one"),
        ("merge_steady", "parameter_merge.steady"),
        ("snapshot_one", "parameter_snapshot.one_key"),
        ("visibility_default", "parameter_visibility.default"),
        ("visibility_search", "parameter_visibility.search"),
        ("favorite_view", "parameter_favorites.stable_view"),
    ),
)
def test_parameter_hotpath_scenario_has_typed_distribution_and_contracts(
    operation: str,
    expected_metric: str,
) -> None:
    result = run_parameter_hot_path_scenario(
        make_parameter_hot_path_scenario(
            {
                "operation": operation,
                "rows": 32,
                "samples": 4,
            }
        )
    )

    metrics = {metric.name: metric for metric in result.metrics}
    distribution = metrics[expected_metric]
    assert distribution.kind == "distribution"
    assert distribution.unit == "ms"
    assert distribution.distribution is not None
    assert distribution.distribution.count == 4
    assert all(contract.passed for contract in result.contracts if contract.severity == "hard")


def test_parameter_hotpath_registry_scopes_reference_and_soak_cases() -> None:
    definitions = {definition.case_id: definition for definition in case_definitions()}

    merge = definitions["runtime.parameter_merge.rows_1000.change_steady"]
    runtime_one = definitions[
        "runtime.parameter_merge.rows_1000.change_runtime_one"
    ]
    snapshot = definitions["runtime.parameter_snapshot.rows_10000.change_one"]
    layout = definitions["gui.parameter_layout.rows_10000"]
    visibility = definitions["gui.parameter_visibility.rows_10000.mode_search"]
    favorites = definitions["gui.parameter_favorites.rows_10000"]

    assert merge.parameters == {
        "operation": "merge_steady",
        "rows": 1_000,
        "samples": 24,
    }
    assert merge.selectable_suites == ("parameters",)
    assert runtime_one.parameters == {
        "operation": "merge_runtime_one",
        "rows": 1_000,
        "samples": 200,
    }
    assert runtime_one.selectable_suites == ("parameters",)
    assert layout.parameters["operation"] == "layout_reuse"
    assert layout.selectable_suites == ("parameters", "soak")
    assert snapshot.selectable_suites == ("parameters", "soak")
    assert visibility.parameters["operation"] == "visibility_search"
    assert visibility.self_sampling is True
    assert favorites.parameters["operation"] == "favorite_view"
    assert favorites.selectable_suites == ("parameters", "soak")


def test_parameter_layout_benchmark_uses_canonical_layout_model() -> None:
    result = run_parameter_hot_path_scenario(
        make_parameter_hot_path_scenario(
            {
                "operation": "layout_reuse",
                "rows": 32,
                "samples": 4,
            }
        )
    )

    metrics = {metric.name for metric in result.metrics}
    assert "parameter_layout.build" in metrics
    assert "parameter_layout.legacy_regroup" not in metrics
    assert result.value["built_blocks"] == result.value["layout_blocks"]
    assert "legacy_blocks" not in result.value


def test_parameter_hotpath_scenario_rejects_unknown_operation() -> None:
    with pytest.raises(ValueError, match="未対応"):
        make_parameter_hot_path_scenario(
            {
                "operation": "unknown",
                "rows": 32,
                "samples": 4,
            }
        )


def test_parameter_snapshot_model_reports_stable_model_cache() -> None:
    store = parameter_store_fixture(rows=12)
    model = parameter_snapshot_model_workload(store, frames=5)

    assert model["output"]["rows"] == 12
    assert model["output"]["snapshot_entries"] == 12
    assert model["output"]["render_calls"] == 5
    assert model["output"]["model_builds"] == 1
    assert model["cache"]["hits"] == 4
    assert model["cache"]["misses"] == 1
