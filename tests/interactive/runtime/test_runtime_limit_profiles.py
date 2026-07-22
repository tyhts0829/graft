from __future__ import annotations

import pytest

from grafix import G
from grafix.core.layer import LayerStyleDefaults
from grafix.core.parameters import ParamStore
from grafix.core.resource_budget import ResourceBudget
from grafix.core.runtime_limits import RuntimeLimitProfiles, RuntimeLimits
from grafix.runtime_config_loader import runtime_config
from grafix.interactive.diagnostics import DiagnosticCenter
from grafix.interactive.runtime.export_job_system import ExportJobSystem
from grafix.interactive.runtime.perf import PerfCollector
from grafix.interactive.runtime.scene_runner import SceneRunner


def _budget(vertices: int) -> ResourceBudget:
    return ResourceBudget(
        max_output_vertices=vertices,
        max_output_lines=100,
        max_output_bytes=1_000_000,
    )


def _draw_two_layers(_t: float):
    return [
        G.polygon(n_sides=3, key="profile-first"),
        G.polygon(n_sides=3, key="profile-second"),
    ]


def _draw_one_layer(_t: float):
    return G.polygon(n_sides=3, key="profile-cache-limit")


def test_scene_runner_selects_preview_and_final_limit_profiles() -> None:
    preview = RuntimeLimits(
        per_operation=_budget(10),
        scene=_budget(7),
    )
    final = RuntimeLimits(
        per_operation=_budget(10),
        scene=_budget(8),
    )
    center = DiagnosticCenter()
    runner = SceneRunner(
        _draw_two_layers,
        perf=PerfCollector(enabled=False),
        n_worker=0,
        runtime_limit_profiles=RuntimeLimitProfiles(
            preview=preview,
            final=final,
        ),
        diagnostic_center=center,
        effective_config=runtime_config(),
    )
    try:
        with pytest.raises(Exception):
            runner.run(
                0.0,
                store=ParamStore(),
                cc_snapshot=None,
                defaults=LayerStyleDefaults(
                    color=(0.0, 0.0, 0.0), thickness=0.01
                ),
                recording=False,
                transport_epoch=0,
                quality="draft",
            )

        events = center.snapshot()
        assert len(events) == 1
        assert events[0].category == "resource"
        assert "scene aggregate" in events[0].summary

        layers = runner.run(
            0.0,
            store=ParamStore(),
            cc_snapshot=None,
            defaults=LayerStyleDefaults(
                color=(0.0, 0.0, 0.0), thickness=0.01
            ),
            recording=False,
            transport_epoch=0,
            quality="final",
        )
        assert len(layers) == 2
    finally:
        runner.close()


def test_export_job_system_uses_capture_queue_runtime_limits() -> None:
    limits = RuntimeLimits(
        capture_queue_pending_jobs=2,
        capture_queue_bytes=12345,
    )
    jobs = ExportJobSystem(runtime_limits=limits)
    try:
        assert jobs.request_limit == 3  # in-flight 1 + pending 2
        assert jobs.max_retained_bytes == 12345
    finally:
        jobs.close()


def test_cpu_cache_limit_reaches_common_diagnostic_center() -> None:
    limits = RuntimeLimits(
        per_operation=_budget(10),
        scene=_budget(10),
        cpu_cache_bytes=0,
    )
    center = DiagnosticCenter()
    runner = SceneRunner(
        _draw_one_layer,
        perf=PerfCollector(enabled=False),
        n_worker=0,
        runtime_limit_profiles=RuntimeLimitProfiles(
            preview=limits,
            final=limits,
        ),
        diagnostic_center=center,
        effective_config=runtime_config(),
    )
    try:
        runner.run(
            0.0,
            store=ParamStore(),
            cc_snapshot=None,
            defaults=LayerStyleDefaults(
                color=(0.0, 0.0, 0.0), thickness=0.01
            ),
            recording=False,
            transport_epoch=0,
            quality="draft",
        )
    finally:
        runner.close()

    events = center.snapshot()
    assert len(events) == 1
    assert events[0].category == "operation"
    assert "runtime.cpu_cache" in events[0].summary
