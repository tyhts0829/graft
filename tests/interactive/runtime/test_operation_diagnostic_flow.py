from __future__ import annotations

import time

import pytest

from grafix.core.geometry import Geometry
from grafix.core.layer import LayerStyleDefaults
from grafix.core.operation_diagnostics import (
    OperationDiagnostic,
    emit_operation_diagnostic,
)
from grafix.core.parameters import ParamStore
from grafix.runtime_config_loader import runtime_config
from grafix.interactive.diagnostics import DiagnosticCenter
from grafix.interactive.runtime.mp_draw import DrawResult, MpDraw
from grafix.interactive.runtime.perf import PerfCollector
from grafix.interactive.runtime.scene_runner import SceneRunner
from tests.interactive.runtime.scene_runner_fixture import MpDrawFactoryFixture


def _diagnostic_draw(_t: float) -> Geometry:
    emit_operation_diagnostic(
        op="worker-example",
        original_value=12,
        effective_value=10,
        reason="worker value was clamped",
    )
    return Geometry.create(op="concat")


def _defaults() -> LayerStyleDefaults:
    return LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01)


def test_sync_scene_runner_publishes_to_common_diagnostic_center() -> None:
    center = DiagnosticCenter()
    runner = SceneRunner(
        _diagnostic_draw,
        perf=PerfCollector(enabled=False),
        n_worker=0,
        diagnostic_center=center,
        effective_config=runtime_config(),
    )
    try:
        runner.run(
            0.0,
            store=ParamStore(),
            cc_snapshot=None,
            defaults=_defaults(),
            recording=False,
            transport_epoch=0,
            quality="draft",
        )
    finally:
        runner.close()

    assert len(runner.last_operation_diagnostics) == 1
    event = center.snapshot()[0]
    assert event.category == "operation"
    assert event.summary == "worker-example: worker value was clamped"
    assert "original=12" in event.details
    assert "effective=10" in event.details


def test_mp_draw_result_carries_worker_diagnostics_separately() -> None:
    mp_draw = MpDraw(
        _diagnostic_draw,
        n_worker=1,
        effective_config=runtime_config(),
    )
    try:
        mp_draw.submit(
            t=0.0,
            snapshot_revision=0,
            snapshot={},
            effect_order_snapshot={},
            epoch=0,
            quality="draft",
        )
        deadline = time.monotonic() + 8.0
        result = None
        while time.monotonic() < deadline:
            result = mp_draw.poll_latest()
            if result is not None:
                break
            time.sleep(0.01)
        if result is None:
            pytest.fail("mp-draw result timeout")

        assert result.error is None
        assert result.records == ()
        assert len(result.diagnostics) == 1
        assert result.diagnostics[0].op == "worker-example"
    finally:
        mp_draw.close()


class _WorkerResult:
    def __init__(self, result: DrawResult) -> None:
        self._result = result
        self._published = False
        self.last_submitted_frame_id = int(result.frame_id)

    def submit(self, **_kwargs: object) -> None:
        return None

    def poll_latest(self) -> DrawResult | None:
        if self._published:
            return None
        self._published = True
        return self._result

    def latest_successful_result(self) -> DrawResult:
        return self._result

    def begin_epoch(self, epoch: int | None = None) -> int:
        return 0 if epoch is None else int(epoch)

    def close(self) -> None:
        return None


def test_scene_runner_merges_worker_payload_before_center_publish() -> None:
    payload = OperationDiagnostic(
        op="worker-only",
        original_value=4_000_001,
        effective_value=None,
        reason="grid was rejected",
    )
    worker_result = DrawResult(
        frame_id=1,
        layers=(),
        records=(),
        labels=(),
        t=1.0,
        epoch=0,
        generation=0,
        snapshot_revision=0,
        effect_chains=(),
        diagnostics=(payload,),
    )
    center = DiagnosticCenter()
    runner = SceneRunner(
        lambda _t: Geometry.create(op="concat"),
        perf=PerfCollector(enabled=False),
        n_worker=1,
        diagnostic_center=center,
        effective_config=runtime_config(),
        mp_draw_factory=MpDrawFactoryFixture(_WorkerResult(worker_result)),
    )
    try:
        assert (
            runner.run(
                1.0,
                store=ParamStore(),
                cc_snapshot=None,
                defaults=_defaults(),
                recording=False,
                transport_epoch=0,
                quality="draft",
            )
            == []
        )
    finally:
        runner.close()

    assert runner.last_operation_diagnostics == (payload,)
    assert center.snapshot()[0].summary == "worker-only: grid was rejected"


def test_operation_diagnostic_rejects_implicit_payload_conversion() -> None:
    with pytest.raises(TypeError):
        OperationDiagnostic(
            op=object(),  # type: ignore[arg-type]
            original_value=(),
            effective_value=(),
            reason="invalid op",
        )
    with pytest.raises(TypeError):
        OperationDiagnostic(
            op="sample",
            original_value=[1],  # type: ignore[arg-type]
            effective_value=(),
            reason="invalid value",
        )
