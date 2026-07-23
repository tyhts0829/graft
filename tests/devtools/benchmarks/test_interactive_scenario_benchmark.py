from __future__ import annotations

from typing import Any

from grafix.devtools.benchmarks import interactive_scenario_benchmark as scenario_module
from grafix.devtools.benchmarks.interactive_scenario_benchmark import (
    make_interactive_slider_scenario,
    run_interactive_slider_scenario,
)


def _sync_parameters() -> dict[str, object]:
    return {
        "rows": 32,
        "workers": 0,
        "warmup_frames": 2,
        "drag_frames": 6,
        "settle_frames": 2,
        "frame_interval_s": 0.0,
        "settle_timeout_s": 0.5,
        "latency_guardrail_ms": 50.0,
    }


def test_sync_slider_scenario_reports_phases_progress_and_exact_output() -> None:
    result = run_interactive_slider_scenario(make_interactive_slider_scenario(_sync_parameters()))

    assert result.value["scope"] == ("hosted:fake-gui+scene-runner+fake-gl+present-marker")
    assert result.value["final_input_revision_delta"] == 6
    assert (
        result.value["final_presented_revision_delta"] == result.value["final_input_revision_delta"]
    )
    assert result.value["final_mesh_checksum"] == result.value["expected_final_mesh_checksum"]
    assert all(contract.passed for contract in result.contracts if contract.severity == "hard")

    metric_by_name = {metric.name: metric for metric in result.metrics}
    for phase in ("warmup", "drag", "settle"):
        metric = metric_by_name[f"ux01.frame_duration.{phase}"]
        assert metric.phase == phase
        assert metric.distribution is not None
        assert metric.distribution.count >= 1
    latency = metric_by_name["ux01.input_to_present"]
    assert latency.phase == "drag"
    assert latency.distribution is not None
    assert latency.distribution.count == 6
    assert metric_by_name["ux01.final_input_revision_delta"].value == 6


def test_slider_measurement_reuses_setup_config_and_definitions(
    monkeypatch,
) -> None:
    """UX timer 内で config discovery/candidate load を繰り返さない。"""

    from grafix import authoring_loader as loader_module
    from grafix.interactive.parameter_gui import catalog as catalog_module

    scenario = make_interactive_slider_scenario(_sync_parameters())

    def unexpected_call(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("measurement 内で composition を再構築しました")

    monkeypatch.setattr(scenario_module, "runtime_config", unexpected_call)
    monkeypatch.setattr(
        loader_module,
        "load_config_authoring_definitions",
        unexpected_call,
    )
    monkeypatch.setattr(
        catalog_module,
        "current_parameter_gui_catalog",
        unexpected_call,
    )

    result = run_interactive_slider_scenario(scenario)

    assert all(contract.passed for contract in result.contracts if contract.severity == "hard")


def test_runner_registry_exposes_sync_smoke_and_worker_interactive_cases() -> None:
    from grafix.devtools.benchmarks.catalog import case_definitions

    definitions = {definition.case_id: definition for definition in case_definitions()}
    sync = definitions["interactive.slider.input_to_present.rows_32.workers_0"]
    worker = definitions["interactive.slider.input_to_present.rows_32.workers_1"]
    large_table = definitions["interactive.slider.input_to_present.rows_1000.workers_0"]

    assert sync.parameters["rows"] == 32
    assert sync.parameters["workers"] == 0
    assert sync.self_sampling is True
    assert {"smoke", "interactive"} <= set(sync.selectable_suites)
    assert worker.parameters["workers"] == 1
    assert worker.parameters["frame_interval_s"] == 1.0 / 60.0
    assert worker.selectable_suites == ("interactive",)
    assert large_table.parameters["rows"] == 1_000
    assert large_table.parameters["workers"] == 0
    assert large_table.selectable_suites == ("interactive",)


def test_one_worker_slider_scenario_reaches_the_final_revision() -> None:
    parameters = _sync_parameters()
    parameters.update(
        {
            "workers": 1,
            "frame_interval_s": 1.0 / 60.0,
            "settle_timeout_s": 2.0,
        }
    )

    result = run_interactive_slider_scenario(make_interactive_slider_scenario(parameters))

    assert all(contract.passed for contract in result.contracts if contract.severity == "hard")
    assert (
        result.value["final_presented_revision_delta"] == result.value["final_input_revision_delta"]
    )
    latency = next(metric for metric in result.metrics if metric.name == "ux01.input_to_present")
    assert latency.distribution is not None
    assert latency.distribution.count == parameters["drag_frames"]
    fresh_contract = next(
        contract
        for contract in result.contracts
        if contract.contract_id == "ux01.guardrail.fresh_ratio"
    )
    assert fresh_contract.passed


def test_hosted_renderer_does_not_advance_scene_serial_for_stale_redisplay(
    monkeypatch,
) -> None:
    real_scene_runner = scenario_module.SceneRunner

    class _AlternatingFreshSceneRunner:
        def __init__(self, *args: object, **kwargs: object) -> None:
            kwargs["n_worker"] = 0
            self._inner = real_scene_runner(*args, **kwargs)
            self._calls = 0
            self._layers: list[Any] = []
            self.last_realized_snapshot_revision: int | None = None
            self.last_evaluation_succeeded: bool | None = None
            self.last_output_updated = False

        def run(self, *args: object, **kwargs: object) -> list[Any]:
            self._calls += 1
            if self._calls % 2 == 1 or not self._layers:
                self._layers = self._inner.run(*args, **kwargs)
                self.last_realized_snapshot_revision = self._inner.last_realized_snapshot_revision
                self.last_evaluation_succeeded = True
                self.last_output_updated = True
            else:
                self.last_evaluation_succeeded = None
                self.last_output_updated = False
            return self._layers

        def close(self) -> None:
            self._inner.close()

    monkeypatch.setattr(
        scenario_module,
        "SceneRunner",
        _AlternatingFreshSceneRunner,
    )
    result = run_interactive_slider_scenario(make_interactive_slider_scenario(_sync_parameters()))
    metrics = {metric.name: metric.value for metric in result.metrics}

    assert metrics["ux01.fresh_present_frames"] < 8
    assert metrics["ux01.scene_serial_advances"] == metrics["ux01.fresh_present_frames"]
    contract = next(
        item
        for item in result.contracts
        if item.contract_id == "ux01.semantic.scene_serial_is_fresh_only"
    )
    assert contract.passed
