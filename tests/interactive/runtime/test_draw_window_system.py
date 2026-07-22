from __future__ import annotations

# ruff: noqa: E402 -- pyglet option must be set before importing DrawWindowSystem.

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest
import pyglet

# Unit tests construct DrawWindowSystem without a real window. Disable pyglet's import-time
# shadow context so the test remains usable in CI/headless sessions.
pyglet.options["shadow_window"] = False

from grafix.api.render import RenderOptions
from grafix.export.capture_publish import capture_manifest_path_for
from grafix.core.capture_provenance import CaptureProvenance
from grafix.core.evaluation_context import (
    EMPTY_EXTERNAL_DEPENDENCIES_FINGERPRINT,
    EvaluationFingerprint,
)
from grafix.export.capture_provenance import CaptureProvenanceBuilder
from grafix.core.export_format import ExportFormat
from grafix.core.geometry import Geometry
from grafix.core.layer import Layer, LayerStyleDefaults
from grafix.export.output_paths import VersionedPathAllocator
from grafix.core.parameters import (
    EffectStepTopology,
    FrameEffectChainRecord,
    ParamStore,
)
from grafix.core.parameters.effect_order_ops import merge_frame_effect_chains
from grafix.core.pipeline import RealizedLayer
from grafix.core.realize import GeometryCacheKey
from grafix.core.realized_geometry import RealizedGeometry
from grafix.core.runtime_config import current_runtime_config
from grafix.runtime_config_loader import runtime_config
from grafix.core.runtime_limits import RuntimeLimits
from grafix.interactive.midi import MidiSession
from grafix.interactive.midi.midi_controller import CcSnapshotLoadResult
from grafix.interactive.runtime.draw_window_system import DrawWindowSystem
import grafix.interactive.runtime.draw_window_system as draw_window_module
from grafix.interactive.runtime.capture_queue import CaptureQueue
import grafix.interactive.runtime.capture_queue as capture_queue_module
from grafix.interactive.runtime.export_job_system import (
    CaptureExportSnapshot,
    ExportJobResult,
    ExportJobStatus,
    ExportQueueFullError,
    ExportQueueStatus,
    FrameExportSnapshot,
)
from grafix.interactive.transport import TransportClock
from grafix.interactive.runtime.monitor import RuntimeMonitor
from grafix.interactive.runtime.perf import PerfCollector
from grafix.interactive.runtime.presented_frame import PresentedFrameState
from grafix.interactive.runtime.source_reload import SourceReloadResult
from grafix.export import capture as capture_module
from grafix.export.capture import CaptureService
from tests.interactive.runtime.draw_window_system_fixture import (
    make_draw_window_system as _make_initialized_system,
)


_DEFAULTS = LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01)


def _provenance_draw(_t: float) -> list[object]:
    return []


def _provenance_builder_for(store: ParamStore) -> CaptureProvenanceBuilder:
    return CaptureProvenanceBuilder(
        _provenance_draw,
        config=runtime_config(),
        parameter_source="code",
        parameter_store_path=None,
        parameter_load_provenance=store.load_provenance,
        seed=1847,
    )


def _reset_presented_frame(
    system: DrawWindowSystem,
    *,
    store: ParamStore,
    builder: object | None = None,
    layers: list[RealizedLayer] | None = None,
    t: float | None = None,
) -> PresentedFrameState:
    state = PresentedFrameState(
        store=store,
        provenance_builder=cast(
            Any,
            _provenance_builder_for(store) if builder is None else builder,
        ),
    )
    if layers is not None or t is not None:
        state.accept_evaluation([] if layers is None else layers, realized_t=t)
    system._presented_frame = state
    return state


def _set_export_snapshot(
    system: DrawWindowSystem,
    snapshot: FrameExportSnapshot | None,
) -> None:
    """CaptureQueue 単体寄りの test で表示済み snapshot を注入する。"""

    state = system._presented_frame
    cast(Any, state)._export_snapshot = snapshot
    cast(Any, state)._export_provenance_token = None
    if snapshot is not None:
        cast(Any, state)._layers = snapshot.layers
        cast(Any, state)._t = snapshot.t


def _install_capture_queue(
    system: DrawWindowSystem,
    *,
    jobs: object | None = None,
    service: CaptureService | None = None,
    svg_path: Path | None = None,
    png_path: Path | None = None,
    gcode_path: Path | None = None,
    monitor: object | None = None,
) -> CaptureQueue:
    """CaptureQueue owner を fake job/path と組み合わせる test composition。"""

    capture_service = (
        CaptureService(path_allocator=VersionedPathAllocator()) if service is None else service
    )
    system._capture_service = capture_service
    queue = CaptureQueue(
        capture_service=capture_service,
        runtime_limits=RuntimeLimits(),
        svg_output_path=(Path(system._svg_output_path) if svg_path is None else svg_path),
        png_output_path=(Path(system._png_output_path) if png_path is None else png_path),
        gcode_output_path=(Path(system._gcode_output_path) if gcode_path is None else gcode_path),
        png_scale=system._effective_config.png_scale,
        current_snapshot=lambda: system._presented_frame.export_snapshot,
        capture_current_frame=lambda: system.final_capture_frame(),
        materialize_snapshot=system._presented_frame.materialize_capture_snapshot,
        shutdown_snapshot=lambda: system._shutdown_export_snapshot(),
        monitor=cast(Any, system._monitor if monitor is None else monitor),
        export_jobs=cast(Any, _FakeExportJobs() if jobs is None else jobs),
    )
    system._capture_queue = queue
    return queue


def _realized_layer(*, site_id: str) -> RealizedLayer:
    geometry = Geometry.create("draw-window-test-geometry")
    return RealizedLayer(
        layer=Layer(geometry=geometry, site_id=site_id),
        realized=RealizedGeometry(
            coords=np.asarray(
                ((0.0, 0.0, 0.0), (1.0, 1.0, 0.0)),
                dtype=np.float32,
            ),
            offsets=np.asarray((0, 2), dtype=np.int32),
        ),
        cache_key=GeometryCacheKey(
            geometry_id=geometry.id,
            evaluation=EvaluationFingerprint("0" * 64),
            external_dependencies=EMPTY_EXTERNAL_DEPENDENCIES_FINGERPRINT,
        ),
        color=(0.0, 0.0, 0.0),
        thickness=0.01,
    )


@pytest.mark.parametrize(
    ("fps", "error_type"),
    [
        (60, TypeError),
        (True, TypeError),
        ("60", TypeError),
        (float("inf"), ValueError),
        (float("nan"), ValueError),
    ],
)
def test_draw_window_requires_canonical_finite_float_fps(
    fps: object,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type, match="fps"):
        DrawWindowSystem(
            lambda _t: None,
            options=RenderOptions(),
            render_scale=1.0,
            store=ParamStore(),
            effective_config=runtime_config(),
            fps=fps,  # type: ignore[arg-type]
        )


class _CountingProvenanceBuilder:
    def __init__(self, draw: object, store: ParamStore) -> None:
        self.inner = CaptureProvenanceBuilder(
            cast(Any, draw),
            config=runtime_config(),
            parameter_source="code",
            parameter_store_path=None,
            parameter_load_provenance=store.load_provenance,
            seed=1847,
        )
        self.calls: list[dict[str, object]] = []

    def frame(self, store: ParamStore, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return self.inner.frame(store, **cast(Any, kwargs))


def _capture_provenance(t: float) -> CaptureProvenance:
    store = ParamStore()
    return CaptureProvenanceBuilder(
        _provenance_draw,
        config=runtime_config(),
        parameter_source="code",
        parameter_store_path=None,
        parameter_load_provenance=store.load_provenance,
        seed=1847,
    ).frame(
        store,
        t=float(t),
        frame_index=0,
        quality="final",
        origin="interactive",
    )


def _install_capture_context(system: DrawWindowSystem) -> None:
    store = ParamStore()
    system._store = store
    system._effective_config = runtime_config()
    _reset_presented_frame(system, store=store)


def test_source_reload_rolls_back_when_worker_swap_fails() -> None:
    def replacement_draw(_t: float) -> list[object]:
        return []

    result = SourceReloadResult(
        status="reloaded",
        generation=3,
        draw=replacement_draw,
        source="/tmp/sketch.py",
    )

    observed_configs: list[object] = []

    class _Controller:
        def __init__(self) -> None:
            self.rollback_calls: list[int] = []

        def poll(self, *, force: bool, retain_rollback: bool) -> SourceReloadResult:
            assert force is True
            assert retain_rollback is True
            observed_configs.append(current_runtime_config())
            return result

        def rollback_generation(self, generation: int) -> object:
            self.rollback_calls.append(int(generation))
            return replacement_draw

    class _RejectingRunner:
        def replace_draw(
            self,
            draw: object,
            *,
            definitions: object | None = None,
        ) -> None:
            assert draw is replacement_draw
            assert definitions is None
            raise RuntimeError("worker did not start")

    controller = _Controller()
    monitor = RuntimeMonitor()
    system = _make_initialized_system()
    system._source_reload = cast(Any, controller)
    system._scene_runner = cast(Any, _RejectingRunner())
    system._monitor = monitor

    assert system._poll_source_reload(force=True) is False
    assert observed_configs == [system._effective_config]
    assert controller.rollback_calls == [3]
    diagnostics = monitor.diagnostic_center.snapshot()
    assert len(diagnostics) == 1
    assert diagnostics[0].category == "reload"
    assert {action.action_id for action in diagnostics[0].actions} == {
        "copy",
        "open",
        "retry",
    }


def test_successful_source_reload_begins_effect_chain_generation() -> None:
    def replacement_draw(_t: float) -> list[object]:
        return []

    result = SourceReloadResult(
        status="reloaded",
        generation=4,
        draw=replacement_draw,
        source="/tmp/sketch.py",
    )

    class _Controller:
        def __init__(self) -> None:
            self.accepted: list[int] = []

        def poll(self, *, force: bool, retain_rollback: bool) -> SourceReloadResult:
            assert force is True
            assert retain_rollback is True
            return result

        def accept_generation(self, generation: int) -> None:
            self.accepted.append(int(generation))

    class _Runner:
        def replace_draw(
            self,
            draw: object,
            *,
            definitions: object | None = None,
        ) -> None:
            assert draw is replacement_draw
            assert definitions is None

    store = ParamStore()
    assert merge_frame_effect_chains(
        store,
        [
            FrameEffectChainRecord(
                chain_id="old-generation-chain",
                steps=(
                    EffectStepTopology(
                        op="scale",
                        site_id="old-generation-site",
                        n_inputs=1,
                        code_index=0,
                    ),
                ),
            )
        ],
        observation_complete=False,
    )
    controller = _Controller()
    system = _make_initialized_system()
    system._source_reload = cast(Any, controller)
    system._scene_runner = cast(Any, _Runner())
    system._store = store
    system._monitor = None

    assert system._poll_source_reload(force=True) is True
    assert controller.accepted == [4]
    # reload自体ではlast-good GUI stateを保持し、最初の成功evaluationで確定する。
    assert "old-generation-chain" in store.effect_chain_topologies()
    assert merge_frame_effect_chains(
        store,
        [],
        observation_complete=True,
    )
    assert "old-generation-chain" not in store.effect_chain_topologies()


class _FakeMonitor:
    def __init__(self) -> None:
        self.frame_errors: list[str | None] = []
        self.frame_error_details: list[tuple[str, str | None]] = []

    def set_frame_error(
        self,
        message: str | None,
        *,
        details: str = "",
        source: str | None = None,
    ) -> None:
        self.frame_errors.append(message)
        self.frame_error_details.append((details, source))


class _FakeSceneRunner:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = outcomes
        self.last_evaluation_succeeded: bool | None = None
        self.last_evaluation_t: float | None = None
        self.last_realized_t: float | None = None
        self.last_realized_snapshot_revision: int | None = None
        self.last_realized_frame_id: int | None = None
        self.last_output_updated = False
        self.is_waiting_for_fresh_result = False
        self.realized_t_override: float | None = None
        self.snapshot_revision_override: int | None = None
        self.run_kwargs: list[dict[str, object]] = []

    def run(self, *args: object, **kwargs: object) -> list[RealizedLayer]:
        self.run_kwargs.append(dict(kwargs))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            self.last_evaluation_succeeded = False
            self.last_evaluation_t = None
            self.last_realized_t = None
            self.last_realized_snapshot_revision = None
            self.last_realized_frame_id = None
            self.last_output_updated = False
            self.is_waiting_for_fresh_result = False
            raise outcome
        layers, status = cast(tuple[list[RealizedLayer], bool | None], outcome)
        self.last_evaluation_succeeded = status
        self.last_output_updated = status is True
        self.is_waiting_for_fresh_result = status is None
        self.last_evaluation_t = None
        if status is True:
            t = (
                float(args[0])
                if self.realized_t_override is None
                else float(self.realized_t_override)
            )
            store = cast(ParamStore, kwargs["store"])
            self.last_evaluation_t = t
            self.last_realized_t = t
            self.last_realized_snapshot_revision = (
                int(store.revision)
                if self.snapshot_revision_override is None
                else int(self.snapshot_revision_override)
            )
            self.last_realized_frame_id = None
        return layers


def _make_scene_only_system(
    *, runner: _FakeSceneRunner, monitor: _FakeMonitor, last_good: list[RealizedLayer]
) -> DrawWindowSystem:
    system = _make_initialized_system()
    system._scene_runner = cast(Any, runner)
    store = ParamStore()
    system._store = store
    system._monitor = cast(Any, monitor)
    _reset_presented_frame(system, store=store, layers=last_good)
    system._last_frame_error = None
    return system


def test_scene_error_renders_last_good_until_a_new_success(
    caplog: pytest.LogCaptureFixture,
) -> None:
    last_good = cast(list[RealizedLayer], [object()])
    recovered = cast(list[RealizedLayer], [object()])
    runner = _FakeSceneRunner(
        [
            ValueError("broken sketch"),
            (last_good, None),  # mp-draw result waiting: not a recovery yet
            (recovered, True),
        ]
    )
    monitor = _FakeMonitor()
    system = _make_scene_only_system(
        runner=runner,
        monitor=monitor,
        last_good=last_good,
    )

    failed_frame = system._evaluate_scene(
        0.0,
        cc_snapshot=None,
        defaults=_DEFAULTS,
        recording=False,
        quality="draft",
    )
    assert failed_frame == tuple(last_good)
    assert system._presented_frame.layers == tuple(last_good)
    assert system._last_frame_error == "ValueError: broken sketch"
    assert monitor.frame_errors == ["ValueError: broken sketch"]
    assert "ValueError: broken sketch" in monitor.frame_error_details[0][0]
    assert monitor.frame_error_details[0][1] is not None
    assert "rendering the last successful frame" in caplog.text

    pending_frame = system._evaluate_scene(
        0.1,
        cc_snapshot=None,
        defaults=_DEFAULTS,
        recording=False,
        quality="draft",
    )
    assert pending_frame == tuple(last_good)
    assert system._last_frame_error == "ValueError: broken sketch"
    assert monitor.frame_errors == ["ValueError: broken sketch"]

    recovered_frame = system._evaluate_scene(
        0.2,
        cc_snapshot=None,
        defaults=_DEFAULTS,
        recording=False,
        quality="draft",
    )
    assert recovered_frame == tuple(recovered)
    assert system._presented_frame.layers == tuple(recovered)
    assert system._last_frame_error is None
    assert monitor.frame_errors == ["ValueError: broken sketch", None]


def test_frame_error_summary_uses_actionable_root_cause() -> None:
    try:
        try:
            raise ValueError("samples を減らしてください")
        except ValueError as cause:
            raise RuntimeError("Geometry evaluation failed") from cause
    except RuntimeError as error:
        summary = DrawWindowSystem._frame_error_summary(error)

    assert summary == "ValueError: samples を減らしてください"


def test_scene_error_logs_each_distinct_summary_only_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    last_good = cast(list[RealizedLayer], [object()])
    runner = _FakeSceneRunner(
        [
            ValueError("same failure"),
            ValueError("same failure"),
            TypeError("different failure"),
        ]
    )
    monitor = _FakeMonitor()
    system = _make_scene_only_system(
        runner=runner,
        monitor=monitor,
        last_good=last_good,
    )

    for t in (0.0, 0.1, 0.2):
        assert (
            system._evaluate_scene(
                t,
                cc_snapshot=None,
                defaults=_DEFAULTS,
                recording=False,
                quality="draft",
            )
            == tuple(last_good)
        )

    messages = [
        record.message
        for record in caplog.records
        if "rendering the last successful frame" in record.message
    ]
    assert len(messages) == 2
    assert system._last_frame_error == "TypeError: different failure"


def test_last_good_frame_keeps_the_mp_worker_evaluation_time() -> None:
    realized = cast(list[RealizedLayer], [object()])
    runner = _FakeSceneRunner([(realized, True)])
    runner.realized_t_override = 0.25
    system = _make_scene_only_system(
        runner=runner,
        monitor=_FakeMonitor(),
        last_good=[],
    )

    system._evaluate_scene(
        1.0,
        cc_snapshot=None,
        defaults=_DEFAULTS,
        recording=False,
        quality="draft",
    )

    assert system._presented_frame.t == pytest.approx(0.25)


def test_batched_mp_success_updates_capture_time_without_clearing_later_error() -> None:
    """realized success の t は取り込み、より新しい error 表示は保つ。"""

    realized = cast(list[RealizedLayer], [object()])
    runner = _FakeSceneRunner([(realized, None)])
    runner.last_realized_t = 0.25
    system = _make_scene_only_system(
        runner=runner,
        monitor=_FakeMonitor(),
        last_good=cast(list[RealizedLayer], [object()]),
    )
    system._presented_frame.accept_evaluation(
        list(system._presented_frame.layers),
        realized_t=0.1,
    )
    system._last_frame_error = "ValueError: later frame failed"

    returned = system._evaluate_scene(
        1.0,
        cc_snapshot=None,
        defaults=_DEFAULTS,
        recording=False,
        quality="draft",
    )

    assert returned == tuple(realized)
    # SVG/PNG/G-code manifest が参照する時刻は、transport t=1.0 や
    # error frame の t ではなく、実際に描画出力となった success t。
    assert system._presented_frame.t == pytest.approx(0.25)
    # frame 0.25 は error より新しい回復ではないため、banner は消さない。
    assert system._last_frame_error == "ValueError: later frame failed"


def test_scene_evaluation_passes_current_transport_epoch() -> None:
    runner = _FakeSceneRunner([([], True)])
    system = _make_scene_only_system(
        runner=runner,
        monitor=_FakeMonitor(),
        last_good=[],
    )
    system._clock = TransportClock(
        start_time=10.0,
        time_source=lambda: 10.0,
        playing=False,
    )
    system._clock.seek(3.0)

    system._evaluate_scene(
        3.0,
        cc_snapshot=None,
        defaults=_DEFAULTS,
        recording=False,
        quality="draft",
    )

    assert runner.run_kwargs[-1]["transport_epoch"] == system._clock.epoch


def test_midi_frame_snapshot_distinguishes_live_frozen_and_disabled() -> None:
    class Midi:
        def __init__(self) -> None:
            self.poll_calls = 0
            self.snapshot_load_result = CcSnapshotLoadResult(
                values=(),
                status="missing",
                source=Path("snapshot.json"),
            )

        def poll_pending(self) -> int:
            self.poll_calls += 1
            return 0

        def snapshot(self) -> dict[int, float]:
            return {7: 0.75}

    system = _make_initialized_system()
    midi = Midi()
    system._midi_session = MidiSession(
        controller=cast(Any, midi),
        snapshot_load_result=midi.snapshot_load_result,
    )

    live = system._midi_frame_snapshot()
    assert live is not None
    assert live.source == "midi_live"
    assert live[7] == pytest.approx(0.75)
    assert midi.poll_calls == 1

    system._midi_session = MidiSession(
        controller=None,
        snapshot_load_result=CcSnapshotLoadResult(
            values=((7, 0.25),),
            status="loaded",
            source=Path("snapshot.json"),
        ),
        discard_persisted_snapshot=lambda: None,
    )
    frozen = system._midi_frame_snapshot()
    assert frozen is not None
    assert frozen.source == "midi_frozen"
    assert frozen[7] == pytest.approx(0.25)

    system._midi_session = None
    assert system._midi_frame_snapshot() is None


def test_capture_snapshot_re_evaluates_with_final_quality(tmp_path: Path) -> None:
    runner = _FakeSceneRunner([([], True)])
    system = _make_scene_only_system(
        runner=runner,
        monitor=_FakeMonitor(),
        last_good=[],
    )
    provenance_builder = _CountingProvenanceBuilder(
        _provenance_draw,
        system._store,
    )
    _reset_presented_frame(
        system,
        store=system._store,
        builder=provenance_builder,
        t=0.0,
    )
    system._midi_session = None
    clock = SimpleNamespace(t=lambda: 1.25, epoch=0)
    system._clock = cast(Any, clock)
    system._recording_session = cast(
        Any,
        _FrameRecordingSession(_FakeRecording(), clock),
    )
    system._options = cast(Any, SimpleNamespace(canvas_size=(100, 80)))
    system._style = cast(
        Any,
        SimpleNamespace(
            resolve=lambda: SimpleNamespace(
                bg_color_rgb01=(1.0, 1.0, 1.0),
                global_line_color_rgb01=(0.0, 0.0, 0.0),
                global_thickness=0.01,
            )
        ),
    )
    snapshot = system.final_capture_frame()

    assert runner.run_kwargs[-1]["quality"] == "final"
    assert isinstance(snapshot, FrameExportSnapshot)
    assert snapshot.t == pytest.approx(1.25)
    assert snapshot.canvas_size == (100, 80)
    assert snapshot.provenance is not None
    assert snapshot.provenance.frame.quality == "final"
    assert snapshot.provenance.frame.frame_index == 0
    assert len(provenance_builder.calls) == 1

    captured = CaptureService().export(snapshot, tmp_path / "thumbnail.svg")
    manifest = json.loads(captured.manifest_path.read_text(encoding="utf-8"))

    assert captured.path.is_file()
    assert manifest["frame"]["t"] == pytest.approx(1.25)
    assert manifest["output"]["canvas_size"] == {"width": 100, "height": 80}


class _RenderFailure(RuntimeError):
    pass


class _FakeRenderer:
    def __init__(self) -> None:
        self.mesh_upload_count = 0

    def apply_runtime_limits(self, _limits: object) -> None:
        pass

    def begin_frame(
        self,
        _width: int,
        _height: int,
        *,
        background_color: tuple[float, float, float],
    ) -> None:
        del background_color
        pass

    def read_frame_rgb24(self, _width: int, _height: int) -> bytes:
        return b"rgb"

    def render_layer(self, **_kwargs: object) -> object:
        raise _RenderFailure("GL render failed")

    def finish_dynamic_frame(self, _slot_count: int) -> None:
        pass


class _CollectingRenderer(_FakeRenderer):
    def __init__(self) -> None:
        super().__init__()
        self.render_calls: list[dict[str, object]] = []

    def render_layer(self, **kwargs: object) -> object:
        self.render_calls.append(dict(kwargs))
        return SimpleNamespace(draw_vertices=0, draw_lines=0)


class _FakeStyleResolver:
    def resolve(self) -> object:
        return SimpleNamespace(
            bg_color_rgb01=(1.0, 1.0, 1.0),
            global_line_color_rgb01=(0.0, 0.0, 0.0),
            global_thickness=0.01,
        )


class _FakeRecording:
    is_recording = False


class _FrameRecordingSession:
    """DWS frame-order test 用の、録画 owner 最小 spy。"""

    def __init__(self, recording: object, clock: object) -> None:
        self.recording = recording
        self.clock = clock
        self.first_provenance: CaptureProvenance | None = None

    @property
    def is_recording(self) -> bool:
        return bool(getattr(self.recording, "is_recording"))

    @property
    def needs_first_provenance(self) -> bool:
        return self.is_recording and self.first_provenance is None

    def frame_time(self) -> float:
        if not self.is_recording:
            return float(cast(Any, self.clock).t())
        t = float(cast(Any, self.recording).t())
        cast(Any, self.clock).synchronize(t)
        return t

    def record_presented_frame(
        self,
        *,
        fresh: bool,
        read_frame_rgb24: object,
        provenance: CaptureProvenance | None,
        error: str | None,
    ) -> None:
        recording = cast(Any, self.recording)
        if fresh:
            recording.write_frame(cast(Any, read_frame_rgb24)())
            if self.first_provenance is None:
                self.first_provenance = provenance
            return
        recording.pause_frame(error or "Scene evaluation did not produce a fresh frame")


def _make_provenance_preview_system(
    *,
    frame_count: int,
    draw: object = _provenance_draw,
    recording: object | None = None,
    clock: object | None = None,
) -> tuple[DrawWindowSystem, _CountingProvenanceBuilder]:
    store = ParamStore()
    builder = _CountingProvenanceBuilder(draw, store)
    runner = _FakeSceneRunner([([], True) for _ in range(frame_count)])
    system = _make_initialized_system()
    system._perf = PerfCollector(enabled=False)
    system._source_reload = None
    system._midi_session = None
    system._renderer = cast(Any, _FakeRenderer())
    system._framebuffer_size = lambda: (100, 100)
    system._style = cast(Any, _FakeStyleResolver())
    frame_recording = _FakeRecording() if recording is None else recording
    frame_clock = (
        SimpleNamespace(
            t=lambda: 2.5,
            epoch=0,
            is_playing=False,
            speed=1.0,
        )
        if clock is None
        else clock
    )
    system._clock = cast(Any, frame_clock)
    system._recording_session = cast(
        Any,
        _FrameRecordingSession(frame_recording, frame_clock),
    )
    system._scene_runner = cast(Any, runner)
    system._store = store
    system._options = SimpleNamespace(canvas_size=(100, 100))
    _reset_presented_frame(system, store=store, builder=builder, layers=[], t=0.0)
    system._last_frame_error = None
    system._monitor = None
    system._effective_config = runtime_config()
    return system, builder


def test_changed_preview_does_not_materialize_provenance() -> None:
    system, builder = _make_provenance_preview_system(frame_count=120)

    for _ in range(120):
        # slider edit と同じく、毎 fresh frame で store revision を変える。
        system._store._touch()
        system.draw_frame()

    assert builder.calls == []
    assert system._presented_frame.provenance_frame_index == 120
    assert system._presented_frame.export_snapshot is not None
    assert system._presented_frame.export_snapshot.provenance is None
    token = system._presented_frame.provenance_token
    assert token is not None
    assert token.store_revision == system._store.revision
    assert token.frame_index == 119


def test_shutdown_re_evaluates_when_preview_parameter_revision_is_stale() -> None:
    system, builder = _make_provenance_preview_system(frame_count=2)
    system._store._touch()
    # MP worker が一つ前の parameter revision を表示した状況を再現する。
    system._scene_runner.snapshot_revision_override = 0

    system.draw_frame()

    preview = system._presented_frame.export_snapshot
    token = system._presented_frame.provenance_token
    assert preview is not None and preview.provenance is None
    assert token is not None
    assert token.store_revision == 0
    assert token.store_revision != system._store.revision
    assert builder.calls == []

    capture = system._shutdown_export_snapshot()

    assert capture.provenance is not None
    assert capture.provenance.frame.quality == "final"
    assert capture.provenance.frame.parameters.revision == system._store.revision
    assert system._scene_runner.run_kwargs[-1]["quality"] == "final"
    assert len(builder.calls) == 1


def test_draw_frame_marks_only_new_results_as_fresh_renderer_admission() -> None:
    realized = _realized_layer(site_id="held-result")
    system, _builder = _make_provenance_preview_system(frame_count=0)
    runner = _FakeSceneRunner(
        [
            ([realized], True),
            ([realized], None),
        ]
    )
    runner.last_realized_snapshot_revision = 0
    renderer = _CollectingRenderer()
    system._scene_runner = cast(Any, runner)
    system._renderer = cast(Any, renderer)
    system._perf = PerfCollector(enabled=True, console_output=False)

    system.draw_frame()
    system.draw_frame()

    assert [call["scene_serial"] for call in renderer.render_calls] == [1, 1]
    assert [call["snapshot_revision"] for call in renderer.render_calls] == [0, 0]
    snapshot = system._perf.snapshot()
    assert snapshot.preview_samples == 2
    assert snapshot.preview_fresh_results == 1
    assert snapshot.preview_max_consecutive_stale_frames == 1


def test_draw_frame_skips_layer_render_before_first_mp_result() -> None:
    system, _builder = _make_provenance_preview_system(frame_count=0)
    runner = _FakeSceneRunner([([], None)])
    renderer = _CollectingRenderer()
    system._scene_runner = cast(Any, runner)
    system._renderer = cast(Any, renderer)

    system.draw_frame()

    assert renderer.render_calls == []
    assert system._presented_frame.scene_serial == 0
    assert system._presented_frame.export_snapshot is None


def test_gl_render_error_is_not_swallowed_by_scene_error_boundary() -> None:
    realized = _realized_layer(site_id="render-error")
    runner = _FakeSceneRunner([([realized], True)])
    system = _make_initialized_system()
    system._perf = PerfCollector(enabled=False)
    system._midi_session = None
    system._renderer = cast(Any, _FakeRenderer())
    system._framebuffer_size = lambda: (100, 100)
    system._style = cast(Any, _FakeStyleResolver())
    clock = SimpleNamespace(t=lambda: 0.0, epoch=0)
    system._clock = cast(Any, clock)
    system._recording_session = cast(
        Any,
        _FrameRecordingSession(_FakeRecording(), clock),
    )
    system._scene_runner = cast(Any, runner)
    store = ParamStore()
    system._store = store
    _reset_presented_frame(system, store=store, layers=[])
    system._last_frame_error = None
    system._monitor = None

    with pytest.raises(_RenderFailure, match="GL render failed"):
        system.draw_frame()


def test_recording_frame_mirrors_time_without_advancing_transport_epoch() -> None:
    class Clock:
        epoch = 7
        is_playing = False
        speed = 1.0

        def __init__(self) -> None:
            self.synchronized: list[float] = []

        def synchronize(self, t: float) -> None:
            self.synchronized.append(float(t))

        def seek(self, _t: float) -> None:
            raise AssertionError("recording mirror must not seek/increment epoch")

    class Recording:
        is_recording = True

        def t(self) -> float:
            return 2.25

        def write_frame(self, _frame_rgb24: bytes) -> None:
            pass

    clock = Clock()
    runner = _FakeSceneRunner([([], True)])
    system = _make_initialized_system()
    system._perf = PerfCollector(enabled=False)
    system._midi_session = None
    system._renderer = cast(Any, _FakeRenderer())
    system._framebuffer_size = lambda: (100, 100)
    system._style = cast(Any, _FakeStyleResolver())
    system._clock = cast(Any, clock)
    system._recording_session = cast(
        Any,
        _FrameRecordingSession(Recording(), clock),
    )
    system._scene_runner = cast(Any, runner)
    store = ParamStore()
    system._store = store
    system._options = SimpleNamespace(canvas_size=(100, 100))
    _reset_presented_frame(system, store=store, layers=[], t=0.0)
    system._last_frame_error = None
    system._monitor = None

    system.draw_frame()

    assert clock.synchronized == [2.25]
    assert runner.run_kwargs[-1]["transport_epoch"] == 7


def test_recording_materializes_only_the_first_fresh_frame_provenance() -> None:
    class Clock:
        epoch = 4
        is_playing = False
        speed = 1.0

        def synchronize(self, _t: float) -> None:
            return

    class Recording:
        is_recording = True

        def __init__(self) -> None:
            self.write_calls = 0

        def t(self) -> float:
            return 3.25

        def write_frame(self, _frame_rgb24: bytes) -> None:
            self.write_calls += 1

    recording = Recording()
    system, builder = _make_provenance_preview_system(
        frame_count=2,
        recording=recording,
        clock=Clock(),
    )
    recording_session = cast(_FrameRecordingSession, system._recording_session)

    system.draw_frame()
    system.draw_frame()

    assert recording.write_calls == 2
    assert len(builder.calls) == 1
    capture_provenance = recording_session.first_provenance
    assert capture_provenance is not None
    assert capture_provenance.frame.t == pytest.approx(3.25)
    assert capture_provenance.frame.frame_index == 0
    assert capture_provenance.frame.quality == "final"
    assert system._presented_frame.provenance_frame_index == 2
    assert system._presented_frame.export_snapshot is not None
    assert system._presented_frame.export_snapshot.provenance is None


def test_recording_scene_error_pauses_without_writing_last_good_frame() -> None:
    class Clock:
        epoch = 3
        is_playing = False
        speed = 1.0

        def synchronize(self, _t: float) -> None:
            return

    class Recording:
        is_recording = True

        def __init__(self) -> None:
            self.write_calls = 0
            self.paused_errors: list[str] = []

        def t(self) -> float:
            return 4.0

        def write_frame(self, _frame_rgb24: bytes) -> None:
            self.write_calls += 1

        def pause_frame(self, error: str) -> None:
            self.paused_errors.append(error)

    previous_snapshot = FrameExportSnapshot(
        layers=(),
        canvas_size=(100, 100),
        background_color_rgb01=(1.0, 1.0, 1.0),
        t=3.5,
    )
    recording = Recording()
    runner = _FakeSceneRunner([ValueError("broken recording scene")])
    system = _make_initialized_system()
    system._perf = PerfCollector(enabled=False)
    system._midi_session = None
    system._renderer = cast(Any, _FakeRenderer())
    system._framebuffer_size = lambda: (100, 100)
    system._style = cast(Any, _FakeStyleResolver())
    clock = Clock()
    system._clock = cast(Any, clock)
    system._recording_session = cast(
        Any,
        _FrameRecordingSession(recording, clock),
    )
    system._scene_runner = cast(Any, runner)
    store = ParamStore()
    system._store = store
    system._options = SimpleNamespace(canvas_size=(100, 100))
    _reset_presented_frame(system, store=store, layers=[], t=3.5)
    _set_export_snapshot(system, previous_snapshot)
    system._last_frame_error = None
    system._monitor = None

    system.draw_frame()

    assert recording.write_calls == 0
    assert recording.paused_errors == ["ValueError: broken recording scene"]
    assert recording.t() == pytest.approx(4.0)
    assert system._presented_frame.export_snapshot is previous_snapshot


def test_transport_shortcuts_pause_step_reset_and_change_speed() -> None:
    system = _make_initialized_system()
    system._fps = 20.0
    system._clock = TransportClock(
        start_time=10.0,
        time_source=lambda: 10.0,
        initial_t=1.0,
    )

    system._on_key_press(pyglet.window.key.SPACE, 0)
    assert system.transport.is_playing is False
    assert system.transport.t() == pytest.approx(1.0)

    system._on_key_press(pyglet.window.key.RIGHT, 0)
    assert system.transport.t() == pytest.approx(1.05)
    system._on_key_press(pyglet.window.key.LEFT, 0)
    assert system.transport.t() == pytest.approx(1.0)

    system._on_key_press(pyglet.window.key.BRACKETRIGHT, 0)
    assert system.transport.speed == pytest.approx(2.0)
    system._on_key_press(pyglet.window.key.BRACKETLEFT, 0)
    assert system.transport.speed == pytest.approx(1.0)

    system._on_key_press(pyglet.window.key.HOME, 0)
    assert system.transport.t() == pytest.approx(0.0)


def test_svg_captures_are_versioned_and_write_a_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_export_svg(_layers: object, path: Path, *, canvas_size: tuple[int, int]) -> Path:
        assert canvas_size == (320, 240)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<svg/>", encoding="utf-8")
        return path

    monkeypatch.setattr(capture_module, "export_svg", fake_export_svg)
    system = _make_initialized_system()
    system._options = SimpleNamespace(canvas_size=(320, 240))
    service = CaptureService(path_allocator=VersionedPathAllocator())
    snapshot = FrameExportSnapshot(
        layers=(),
        canvas_size=(320, 240),
        background_color_rgb01=(1.0, 1.0, 1.0),
        t=1.25,
        provenance=_capture_provenance(1.25),
    )
    _set_export_snapshot(system, snapshot)

    queue = _install_capture_queue(
        system,
        service=service,
        svg_path=tmp_path / "piece.svg",
    )
    first = queue.save_svg(snapshot)
    second = queue.save_svg(snapshot)

    assert first == tmp_path / "piece.svg"
    assert second == tmp_path / "piece_001.svg"
    manifest_path = capture_manifest_path_for(first)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["frame"]["t"] == pytest.approx(1.25)
    assert payload["output"]["format"] == "svg"
    assert payload["output"]["artifact_paths"] == [str(first)]


def test_direct_svg_after_source_reload_keeps_the_visible_frame_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def old_draw(_t: float) -> list[object]:
        return []

    def new_draw(_t: float) -> list[object]:
        return []

    setattr(old_draw, "__grafix_source_bytes__", b"old source generation")
    setattr(new_draw, "__grafix_source_bytes__", b"new source generation")

    def fake_export_svg(
        _layers: object,
        path: Path,
        *,
        canvas_size: tuple[int, int],
    ) -> Path:
        assert canvas_size == (100, 100)
        path.write_text("<svg/>", encoding="utf-8")
        return path

    system, old_builder = _make_provenance_preview_system(
        frame_count=1,
        draw=old_draw,
    )
    system.draw_frame()
    visible = system._presented_frame.export_snapshot
    assert visible is not None and visible.provenance is None

    new_builder = _CountingProvenanceBuilder(new_draw, system._store)

    class Controller:
        def __init__(self) -> None:
            self.accepted: list[int] = []

        def poll(
            self,
            *,
            force: bool,
            retain_rollback: bool,
        ) -> SourceReloadResult:
            assert force is True
            assert retain_rollback is True
            return SourceReloadResult(
                status="reloaded",
                generation=1,
                draw=new_draw,
                source=str(tmp_path / "sketch.py"),
            )

        def accept_generation(self, generation: int) -> None:
            self.accepted.append(int(generation))

    controller = Controller()
    system._source_reload = cast(Any, controller)
    replaced: list[object] = []
    system._scene_runner.replace_draw = (
        lambda draw, *, definitions=None: replaced.append((draw, definitions))
    )
    monkeypatch.setattr(
        system,
        "_new_provenance_builder",
        lambda _draw: new_builder,
    )

    assert system._poll_source_reload(force=True) is True
    assert controller.accepted == [1]
    assert replaced == [(new_draw, None)]
    assert system._presented_frame.provenance_builder is new_builder

    monkeypatch.setattr(capture_module, "export_svg", fake_export_svg)
    service = CaptureService(path_allocator=VersionedPathAllocator())
    queue = _install_capture_queue(
        system,
        service=service,
        svg_path=tmp_path / "piece.svg",
    )

    saved = queue.save_svg(visible)

    assert saved == tmp_path / "piece.svg"
    assert len(old_builder.calls) == 1
    assert new_builder.calls == []
    payload = json.loads(capture_manifest_path_for(saved).read_text(encoding="utf-8"))
    assert payload["source"]["hash"]["value"] == old_builder.inner.session.source.sha256
    assert payload["frame"]["index"] == 0
    assert payload["frame"]["quality"] == "draft"


def test_svg_request_before_first_draw_uses_first_visible_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exported_layers: list[tuple[RealizedLayer, ...]] = []

    def fake_export_svg(layers: object, path: Path, *, canvas_size: tuple[int, int]) -> Path:
        assert canvas_size == (320, 240)
        exported_layers.append(cast(tuple[RealizedLayer, ...], tuple(layers)))
        path.write_text("<svg>visible</svg>", encoding="utf-8")
        return path

    monkeypatch.setattr(capture_module, "export_svg", fake_export_svg)
    monitor_updates: list[dict[str, object]] = []
    system = _make_initialized_system()
    system._options = SimpleNamespace(canvas_size=(999, 999))
    service = CaptureService(path_allocator=VersionedPathAllocator())
    system._presented_frame.accept_evaluation([], realized_t=-1.0)
    _set_export_snapshot(system, None)
    monitor = cast(
        Any,
        SimpleNamespace(set_capture_queue=lambda **kwargs: monitor_updates.append(dict(kwargs))),
    )
    queue = _install_capture_queue(
        system,
        jobs=_FakeExportJobs(),
        service=service,
        svg_path=tmp_path / "piece.svg",
        monitor=monitor,
    )
    visible_layer = _realized_layer(site_id="first-visible")
    first_visible = FrameExportSnapshot(
        layers=(visible_layer,),
        canvas_size=(320, 240),
        background_color_rgb01=(0.1, 0.2, 0.3),
        t=2.75,
        provenance=_capture_provenance(2.75),
    )

    system._on_key_press(pyglet.window.key.S, 0)

    assert not (tmp_path / "piece.svg").exists()
    assert queue.pending_count == 1
    assert monitor_updates[-1]["request_count"] == 1
    assert "Saved SVG" not in capsys.readouterr().out

    assert queue.bind_presented_frame(first_visible) == 1

    saved = tmp_path / "piece.svg"
    assert saved.read_text(encoding="utf-8") == "<svg>visible</svg>"
    assert exported_layers == [(visible_layer,)]
    assert not queue.has_pending_intents
    assert monitor_updates[-1]["request_count"] == 0
    payload = json.loads(capture_manifest_path_for(saved).read_text(encoding="utf-8"))
    assert payload["frame"]["t"] == pytest.approx(2.75)
    assert payload["output"]["canvas_size"] == {"width": 320, "height": 240}
    assert payload["output"]["artifact_paths"] == [str(saved)]
    assert f"Saved SVG: {saved}" in capsys.readouterr().out


def test_svg_key_after_first_draw_saves_keypress_snapshot_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exported_layers: list[tuple[RealizedLayer, ...]] = []

    def fake_export_svg(layers: object, path: Path, *, canvas_size: tuple[int, int]) -> Path:
        exported_layers.append(cast(tuple[RealizedLayer, ...], tuple(layers)))
        path.write_text("<svg/>", encoding="utf-8")
        return path

    monkeypatch.setattr(capture_module, "export_svg", fake_export_svg)
    visible_layer = _realized_layer(site_id="keypress-visible")
    visible = FrameExportSnapshot(
        layers=(visible_layer,),
        canvas_size=(160, 120),
        background_color_rgb01=(1.0, 1.0, 1.0),
        t=1.5,
        provenance=_capture_provenance(1.5),
    )
    system = _make_initialized_system()
    system._options = SimpleNamespace(canvas_size=(999, 999))
    service = CaptureService(path_allocator=VersionedPathAllocator())
    _set_export_snapshot(system, visible)
    system.final_capture_frame = lambda: visible
    system._monitor = None
    queue = _install_capture_queue(
        system,
        jobs=_FakeExportJobs(),
        service=service,
        svg_path=tmp_path / "piece.svg",
    )

    system._on_key_press(pyglet.window.key.S, 0)

    saved = tmp_path / "piece.svg"
    assert saved.exists()
    assert exported_layers == [(visible_layer,)]
    assert not queue.has_pending_intents
    payload = json.loads(capture_manifest_path_for(saved).read_text(encoding="utf-8"))
    assert payload["frame"]["t"] == pytest.approx(1.5)
    assert payload["output"]["canvas_size"] == {"width": 160, "height": 120}


@pytest.mark.parametrize("collision_kind", ["artifact", "manifest"])
def test_svg_late_collision_preserves_external_file_and_retries_next_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collision_kind: str,
) -> None:
    def fake_export_svg(_layers: object, path: Path, *, canvas_size: tuple[int, int]) -> Path:
        assert canvas_size == (320, 240)
        path.write_bytes(b"new svg")
        return path

    real_publish = capture_module.publish_capture_generation
    collided_path: Path | None = None
    first_call = True

    def publish_with_late_collision(**kwargs: Any) -> object:
        nonlocal collided_path, first_call
        if first_call:
            first_call = False
            artifact = cast(tuple[Path, ...], kwargs["artifact_paths"])[0]
            collided_path = (
                artifact if collision_kind == "artifact" else cast(Path, kwargs["manifest_path"])
            )
            collided_path.write_bytes(b"external")
        return real_publish(**kwargs)

    monkeypatch.setattr(capture_module, "export_svg", fake_export_svg)
    monkeypatch.setattr(
        capture_module,
        "publish_capture_generation",
        publish_with_late_collision,
    )
    system = _make_initialized_system()
    system._options = SimpleNamespace(canvas_size=(320, 240))
    service = CaptureService(path_allocator=VersionedPathAllocator())
    snapshot = FrameExportSnapshot(
        layers=(),
        canvas_size=(320, 240),
        background_color_rgb01=(1.0, 1.0, 1.0),
        t=4.5,
        provenance=_capture_provenance(4.5),
    )
    _set_export_snapshot(system, snapshot)

    queue = _install_capture_queue(
        system,
        service=service,
        svg_path=tmp_path / "piece.svg",
    )
    saved = queue.save_svg(snapshot)

    assert saved == tmp_path / "piece_001.svg"
    assert collided_path is not None
    assert collided_path.read_bytes() == b"external"
    if collision_kind == "manifest":
        # manifest publish failure後、今回 link したbase artifactだけをrollbackする。
        assert not (tmp_path / "piece.svg").exists()
    assert saved.read_bytes() == b"new svg"
    payload = json.loads(capture_manifest_path_for(saved).read_text(encoding="utf-8"))
    assert payload["frame"]["t"] == pytest.approx(4.5)
    assert payload["output"]["artifact_paths"] == [str(saved)]


class _FakeExportJobs:
    def __init__(self) -> None:
        self.submissions: list[dict[str, object]] = []
        self.results: list[ExportJobResult] = []
        self.accepting = True

    def submit(self, **kwargs: object) -> object:
        self.submissions.append(dict(kwargs))
        return SimpleNamespace(job_id=len(self.submissions))

    @property
    def queue_status(self) -> ExportQueueStatus:
        return ExportQueueStatus(
            request_count=len(self.submissions),
            request_limit=17,
            retained_bytes=0,
            byte_limit=1,
        )

    @property
    def has_work(self) -> bool:
        return False

    def ensure_can_submit(self, _snapshot: CaptureExportSnapshot) -> None:
        if not self.accepting:
            raise ExportQueueFullError(
                reason="count",
                request_count=len(self.submissions),
                request_limit=17,
                retained_bytes=0,
                requested_bytes=0,
                byte_limit=1,
            )

    def poll(self) -> list[ExportJobResult]:
        results, self.results = self.results, []
        return results

    def cancel(self, _job_id: int | None = None) -> bool:
        return False

    def close(self) -> None:
        return None


def test_pending_capture_materializes_the_first_fresh_frame_once(
    tmp_path: Path,
) -> None:
    system, builder = _make_provenance_preview_system(frame_count=1)
    jobs = _FakeExportJobs()
    queue = _install_capture_queue(
        system,
        jobs=jobs,
        png_path=tmp_path / "piece.png",
        gcode_path=tmp_path / "piece.gcode",
    )

    assert queue.request(ExportFormat.PNG) is True
    system.draw_frame()

    assert len(builder.calls) == 1
    assert len(jobs.submissions) == 1
    captured = cast(FrameExportSnapshot, jobs.submissions[0]["snapshot"])
    assert captured.provenance is not None
    assert captured.provenance.frame.t == pytest.approx(2.5)
    assert captured.provenance.frame.frame_index == 0
    assert captured.provenance.frame.quality == "final"
    expected = builder.inner.frame(
        system._store,
        t=2.5,
        frame_index=0,
        quality="final",
        origin="interactive",
    )
    assert captured.provenance == expected
    assert system._presented_frame.export_snapshot is not None
    assert system._presented_frame.export_snapshot.provenance is None
    assert not queue.has_pending_intents


class _DrainingExportJobs:
    """内部 pending も受理し、poll ごとに 1 件成功させる close-drain 用 fake。"""

    def __init__(self) -> None:
        self.submissions: list[dict[str, object]] = []
        self._active: list[tuple[int, dict[str, object]]] = []
        self.close_calls = 0

    @property
    def has_work(self) -> bool:
        return bool(self._active)

    def ensure_can_submit(self, _snapshot: CaptureExportSnapshot) -> None:
        return None

    def submit(self, **kwargs: object) -> object:
        job_id = len(self.submissions) + 1
        submission = dict(kwargs)
        self.submissions.append(submission)
        self._active.append((job_id, submission))
        return SimpleNamespace(job_id=job_id)

    def poll(self) -> list[ExportJobResult]:
        if not self._active:
            return []
        job_id, submission = self._active.pop(0)
        return [
            ExportJobResult(
                job_id=job_id,
                format=cast(ExportFormat, submission["format"]),
                status=ExportJobStatus.SUCCESS,
                output_path=cast(Path, submission["output_path"]),
                paths=(),
            )
        ]

    def close(self) -> None:
        assert not self._active
        self.close_calls += 1


def test_async_capture_reservations_do_not_collide_before_files_exist(
    tmp_path: Path,
) -> None:
    jobs = _FakeExportJobs()
    system = _make_initialized_system()
    system._effective_config = runtime_config()
    system._options = SimpleNamespace(canvas_size=(100, 80))
    queue = _install_capture_queue(
        system,
        jobs=jobs,
        png_path=tmp_path / "piece.png",
        gcode_path=tmp_path / "piece.gcode",
    )
    snapshot = FrameExportSnapshot(
        layers=(),
        canvas_size=(100, 80),
        background_color_rgb01=(1.0, 1.0, 1.0),
        t=2.5,
        provenance=_capture_provenance(2.5),
    )

    queue.request(ExportFormat.PNG)
    queue.request(ExportFormat.PNG)
    queue.bind_presented_frame(snapshot)

    paths = [cast(Path, submission["output_path"]) for submission in jobs.submissions]
    assert paths == [tmp_path / "piece.png", tmp_path / "piece_001.png"]

    completed_path = paths[0]
    jobs.results.append(
        ExportJobResult(
            job_id=1,
            format=ExportFormat.PNG,
            status=ExportJobStatus.SUCCESS,
            output_path=completed_path,
            paths=(completed_path,),
        )
    )
    queue.poll()
    # Production ExportJobSystem は artifact + manifest を親側 transaction で
    # 公開する。この fake は path reservation/FIFO だけを検査する。


def test_async_export_failure_is_published_to_shared_diagnostic_center(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "piece.png"
    jobs = _FakeExportJobs()
    jobs.results.append(
        ExportJobResult(
            job_id=1,
            format=ExportFormat.PNG,
            status=ExportJobStatus.ERROR,
            output_path=output_path,
            paths=(),
            error="resvg failed",
        )
    )
    system = _make_initialized_system()
    monitor = RuntimeMonitor()
    queue = _install_capture_queue(system, jobs=jobs, monitor=monitor)

    queue.poll()

    diagnostics = monitor.diagnostic_center.snapshot()
    assert len(diagnostics) == 1
    event = diagnostics[0]
    assert event.category == "export"
    assert event.source == str(output_path)
    assert event.details == "resvg failed"
    assert tuple(action.action_id for action in event.actions) == ("copy",)


def test_each_rapid_capture_key_press_becomes_a_fifo_submission(
    tmp_path: Path,
) -> None:
    jobs = _FakeExportJobs()
    system = _make_initialized_system()
    system._effective_config = runtime_config()
    system._options = SimpleNamespace(canvas_size=(100, 80))
    queue = _install_capture_queue(
        system,
        jobs=jobs,
        png_path=tmp_path / "piece.png",
        gcode_path=tmp_path / "piece.gcode",
    )
    snapshot = FrameExportSnapshot(
        layers=(),
        canvas_size=(100, 80),
        background_color_rgb01=(1.0, 1.0, 1.0),
        t=2.5,
        provenance=_capture_provenance(2.5),
    )

    system._on_key_press(pyglet.window.key.P, 0)
    system._on_key_press(pyglet.window.key.G, 0)
    system._on_key_press(pyglet.window.key.P, 0)
    queue.bind_presented_frame(snapshot)

    assert [submission["format"] for submission in jobs.submissions] == [
        ExportFormat.PNG,
        ExportFormat.GCODE,
        ExportFormat.PNG,
    ]
    assert [submission["output_path"] for submission in jobs.submissions] == [
        tmp_path / "piece.png",
        tmp_path / "piece.gcode",
        tmp_path / "piece_001.png",
    ]
    assert not queue.has_pending_intents


def test_capture_request_uses_frame_visible_at_keypress(tmp_path: Path) -> None:
    jobs = _FakeExportJobs()
    system = _make_initialized_system()
    system._effective_config = runtime_config()
    system._options = SimpleNamespace(canvas_size=(100, 80))
    queue = _install_capture_queue(
        system,
        jobs=jobs,
        png_path=tmp_path / "piece.png",
        gcode_path=tmp_path / "piece.gcode",
    )
    visible_a = CaptureExportSnapshot(
        layers=(),
        canvas_size=(100, 80),
        background_color_rgb01=(1.0, 1.0, 1.0),
        t=1.25,
        provenance=_capture_provenance(1.25),
    )
    later_b = FrameExportSnapshot(
        layers=(),
        canvas_size=(100, 80),
        background_color_rgb01=(0.0, 0.0, 0.0),
        t=9.0,
        provenance=_capture_provenance(9.0),
    )
    _set_export_snapshot(system, visible_a)
    system.final_capture_frame = lambda: visible_a

    assert queue.request(ExportFormat.PNG)
    # 次の draw が B になっても、既に ExportJobSystem が保持する A を変更しない。
    _set_export_snapshot(system, later_b)
    queue.bind_presented_frame(later_b)

    assert len(jobs.submissions) == 1
    assert jobs.submissions[0]["snapshot"] is visible_a
    assert cast(FrameExportSnapshot, jobs.submissions[0]["snapshot"]).t == pytest.approx(1.25)


def test_paused_repeated_capture_shares_the_same_visible_snapshot(
    tmp_path: Path,
) -> None:
    jobs = _FakeExportJobs()
    system = _make_initialized_system()
    system._effective_config = runtime_config()
    system._options = SimpleNamespace(canvas_size=(100, 80))
    queue = _install_capture_queue(
        system,
        jobs=jobs,
        png_path=tmp_path / "piece.png",
        gcode_path=tmp_path / "piece.gcode",
    )
    visible = CaptureExportSnapshot(
        layers=(),
        canvas_size=(100, 80),
        background_color_rgb01=(0.5, 0.5, 0.5),
        t=3.0,
        provenance=_capture_provenance(3.0),
    )
    _set_export_snapshot(system, visible)
    system.final_capture_frame = lambda: visible

    assert all(queue.request(ExportFormat.PNG) for _ in range(3))

    assert [submission["snapshot"] for submission in jobs.submissions] == [
        visible,
        visible,
        visible,
    ]


def test_full_export_queue_rejects_capture_without_reserving_a_path(
    tmp_path: Path,
) -> None:
    jobs = _FakeExportJobs()
    jobs.accepting = False
    system = _make_initialized_system()
    system._effective_config = runtime_config()
    system._options = SimpleNamespace(canvas_size=(100, 80))
    queue = _install_capture_queue(
        system,
        jobs=jobs,
        png_path=tmp_path / "piece.png",
        gcode_path=tmp_path / "piece.gcode",
    )
    queue.request(ExportFormat.PNG)
    snapshot = FrameExportSnapshot(
        layers=(),
        canvas_size=(100, 80),
        background_color_rgb01=(1.0, 1.0, 1.0),
        t=2.5,
        provenance=_capture_provenance(2.5),
    )

    queue.bind_presented_frame(snapshot)
    assert not jobs.submissions
    assert not queue.has_pending_intents

    jobs.accepting = True
    later_snapshot = CaptureExportSnapshot(
        layers=(),
        canvas_size=(100, 80),
        background_color_rgb01=(0.0, 0.0, 0.0),
        t=99.0,
        provenance=_capture_provenance(99.0),
    )
    _set_export_snapshot(system, later_snapshot)
    system.final_capture_frame = lambda: later_snapshot
    assert queue.request(ExportFormat.PNG)
    assert jobs.submissions[0]["output_path"] == tmp_path / "piece.png"
    assert jobs.submissions[0]["snapshot"] is later_snapshot


def test_deferred_capture_intents_are_bounded_and_rejection_is_visible(
    capsys: pytest.CaptureFixture[str],
) -> None:
    system = _make_initialized_system()
    queue = system._capture_queue

    accepted = [queue.request(ExportFormat.PNG) for _ in range(18)]

    assert accepted == [True] * 17 + [False]
    assert queue.pending_count == 17
    output = capsys.readouterr().out
    assert "Capture rejected: PNG" in output
    assert "requests=17/17" in output


def test_pre_frame_capture_limit_is_shared_by_svg_png_and_gcode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    system = _make_initialized_system()
    _set_export_snapshot(system, None)
    system._monitor = None
    queue = system._capture_queue

    for _ in range(8):
        system._on_key_press(pyglet.window.key.S, 0)
    accepted_png = [queue.request(ExportFormat.PNG) for _ in range(9)]
    rejected_gcode = queue.request(ExportFormat.GCODE)

    assert accepted_png == [True] * 9
    assert rejected_gcode is False
    assert queue.pending_count == 17
    output = capsys.readouterr().out
    assert "Capture rejected: G-code" in output
    assert "requests=17/17" in output


def test_close_drains_unbound_capture_requests_in_fifo_order(
    tmp_path: Path,
) -> None:
    jobs = _DrainingExportJobs()
    system = _make_initialized_system()
    system._effective_config = runtime_config()
    system._options = SimpleNamespace(canvas_size=(100, 80))
    queue = _install_capture_queue(
        system,
        jobs=jobs,
        png_path=tmp_path / "piece.png",
        gcode_path=tmp_path / "piece.gcode",
    )
    last_displayed_snapshot = CaptureExportSnapshot(
        layers=(),
        canvas_size=(100, 80),
        background_color_rgb01=(0.0, 0.0, 0.0),
        t=9.0,
        provenance=_capture_provenance(9.0),
    )
    _set_export_snapshot(system, None)
    queue.request(ExportFormat.PNG)
    queue.request(ExportFormat.GCODE)
    _set_export_snapshot(system, last_displayed_snapshot)
    system._midi_session = None
    system._scene_runner = cast(Any, SimpleNamespace(close=lambda: None))
    system._renderer = cast(Any, SimpleNamespace(release=lambda: None))
    system.window = cast(
        Any,
        SimpleNamespace(switch_to=lambda: None, close=lambda: None),
    )

    system.close()
    system.close()

    assert [submission["format"] for submission in jobs.submissions] == [
        ExportFormat.PNG,
        ExportFormat.GCODE,
    ]
    assert [submission["snapshot"] for submission in jobs.submissions] == [
        last_displayed_snapshot,
        last_displayed_snapshot,
    ]
    assert not queue.has_pending_intents
    assert jobs.close_calls == 1


def test_close_releases_remaining_resources_and_raises_first_cleanup_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []
    first_error = RuntimeError("export close failed")

    class FaultyExportJobs:
        has_work = False

        def poll(self) -> list[ExportJobResult]:
            calls.append("poll exports")
            return []

        def close(self) -> None:
            calls.append("close exports")
            raise first_error

    class Midi:
        def __init__(self) -> None:
            self.snapshot_load_result = CcSnapshotLoadResult(
                values=(),
                status="missing",
                source=Path("snapshot.json"),
            )

        def save(self) -> None:
            calls.append("save midi")

        def close(self) -> None:
            calls.append("close midi")

    def stop_recording(*, timeout_s: float, stop_reason: str) -> None:
        assert stop_reason == "shutdown"
        calls.append("stop recording")

    def close_scene() -> None:
        calls.append("close scene")
        raise RuntimeError("secondary scene close failure")

    system = _make_initialized_system()
    _install_capture_queue(system, jobs=FaultyExportJobs())
    system._recording_session = cast(
        Any,
        SimpleNamespace(close=stop_recording),
    )
    midi = Midi()
    system._midi_session = MidiSession(
        controller=cast(Any, midi),
        snapshot_load_result=midi.snapshot_load_result,
    )
    system._scene_runner = cast(Any, SimpleNamespace(close=close_scene))
    system._renderer = cast(
        Any,
        SimpleNamespace(release=lambda: calls.append("release renderer")),
    )

    def switch_context() -> None:
        calls.append("switch context")
        raise RuntimeError("secondary context activation failure")

    system.window = cast(
        Any,
        SimpleNamespace(
            switch_to=switch_context,
            close=lambda: calls.append("close window"),
        ),
    )

    with pytest.raises(RuntimeError, match="export close failed") as exc_info:
        system.close()

    assert exc_info.value is first_error
    assert calls == [
        "stop recording",
        "poll exports",
        "close exports",
        "poll exports",
        "save midi",
        "close midi",
        "close scene",
        "switch context",
        "switch context",
        "close window",
    ]
    logged = [record.getMessage() for record in caplog.records]
    assert all("close capture queue" not in message for message in logged)
    assert any("close scene runner" in message for message in logged)
    assert any("activate draw GL context" in message for message in logged)
    system.close()


def test_export_shutdown_deadline_cancels_remaining_work_explicitly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class StuckExportJobs:
        has_work = True

        def __init__(self) -> None:
            self.cancel_calls = 0

        def poll(self) -> list[ExportJobResult]:
            return []

        def cancel(self) -> bool:
            self.cancel_calls += 1
            self.has_work = False
            return True

    jobs = StuckExportJobs()
    system = _make_initialized_system()
    queue = _install_capture_queue(system, jobs=jobs)

    completed = queue.drain(timeout_s=0.0)

    assert completed is False
    assert jobs.cancel_calls == 1
    assert "Capture shutdown deadline reached" in capsys.readouterr().out


def test_close_shares_capture_deadline_and_cancels_exports_after_video_timeout(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StuckExportJobs:
        has_work = True

        def __init__(self) -> None:
            self.cancel_calls = 0
            self.close_calls = 0

        def poll(self) -> list[ExportJobResult]:
            return []

        def cancel(self) -> bool:
            self.cancel_calls += 1
            self.has_work = False
            return True

        def close(self) -> None:
            self.close_calls += 1

    now = [10.0]
    monkeypatch.setattr(draw_window_module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(capture_queue_module.time, "monotonic", lambda: now[0])
    timeout_error = TimeoutError("video encoder timed out")
    video_timeouts: list[float] = []

    def stop_recording(*, timeout_s: float, stop_reason: str) -> None:
        assert stop_reason == "shutdown"
        video_timeouts.append(float(timeout_s))
        # ffmpeg finalize が capture 全体 budget を使い切った想定。
        now[0] = 15.0
        raise timeout_error

    jobs = StuckExportJobs()
    system = _make_initialized_system()
    _install_capture_queue(system, jobs=jobs)
    system._recording_session = cast(
        Any,
        SimpleNamespace(close=stop_recording),
    )
    system._midi_session = None
    system._scene_runner = cast(Any, SimpleNamespace(close=lambda: None))
    system._renderer = cast(Any, SimpleNamespace(release=lambda: None))
    system.window = cast(
        Any,
        SimpleNamespace(switch_to=lambda: None, close=lambda: None),
    )

    with pytest.raises(TimeoutError, match="video encoder") as exc_info:
        system.close(timeout_s=5.0)

    assert exc_info.value is timeout_error
    assert video_timeouts == [5.0]
    assert jobs.cancel_calls == 1
    assert jobs.close_calls == 1
    assert "Capture shutdown deadline reached" in capsys.readouterr().out


def test_close_switches_to_draw_context_before_renderer_release() -> None:
    calls: list[str] = []
    system = _make_initialized_system()
    _install_capture_queue(
        system,
        jobs=SimpleNamespace(
            has_work=False,
            poll=lambda: [],
            close=lambda: calls.append("close exports"),
        ),
    )
    system._midi_session = None
    system._scene_runner = cast(Any, SimpleNamespace(close=lambda: calls.append("close scene")))
    system._renderer = cast(Any, SimpleNamespace(release=lambda: calls.append("release renderer")))
    system.window = cast(
        Any,
        SimpleNamespace(
            switch_to=lambda: calls.append("switch context"),
            close=lambda: calls.append("close window"),
        ),
    )

    system.close()

    assert calls[-4:] == [
        "switch context",
        "release renderer",
        "switch context",
        "close window",
    ]


def test_partial_draw_window_initialization_releases_acquired_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Window:
        def push_handlers(self, **_kwargs: object) -> None:
            calls.append("push handlers")

        def switch_to(self) -> None:
            calls.append("switch context")

        def close(self) -> None:
            calls.append("close window")

    class Renderer:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            calls.append("create renderer")

        def release(self) -> None:
            calls.append("release renderer")

    class Jobs:
        def __init__(self, **_kwargs: object) -> None:
            calls.append("create exports")

        def close(self) -> None:
            calls.append("close exports")

    monkeypatch.setattr(
        draw_window_module,
        "create_draw_window",
        lambda _options, *, render_scale: Window(),
    )
    monkeypatch.setattr(draw_window_module, "DrawRenderer", Renderer)
    monkeypatch.setattr(
        draw_window_module,
        "output_path_for_draw",
        lambda **kwargs: tmp_path / f"piece.{kwargs['ext']}",
    )
    monkeypatch.setattr(
        draw_window_module,
        "default_png_output_path",
        lambda *_args, **_kwargs: tmp_path / "piece.png",
    )
    monkeypatch.setattr(
        draw_window_module,
        "default_video_output_path",
        lambda *_args, **_kwargs: tmp_path / "piece.mp4",
    )
    monkeypatch.setattr(capture_queue_module, "ExportJobSystem", Jobs)

    def fail_scene_runner(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("scene runner initialization failed")

    monkeypatch.setattr(draw_window_module, "SceneRunner", fail_scene_runner)

    with pytest.raises(RuntimeError, match="scene runner initialization failed"):
        DrawWindowSystem(
            lambda _t: None,
            options=RenderOptions(
                canvas_size=(100, 80),
                line_thickness=_DEFAULTS.thickness,
                line_color=_DEFAULTS.color,
            ),
            render_scale=1.0,
            store=ParamStore(),
            effective_config=runtime_config(),
            n_worker=0,
        )

    assert calls[-5:] == [
        "close exports",
        "switch context",
        "release renderer",
        "switch context",
        "close window",
    ]


def test_layer_gcode_allocator_avoids_stale_layer_files(tmp_path: Path) -> None:
    existing = tmp_path / "piece_layer001_ink.gcode"
    existing.write_text("old", encoding="utf-8")
    system = _make_initialized_system()
    system._capture_service = CaptureService(path_allocator=VersionedPathAllocator())
    system._gcode_output_path = tmp_path / "piece.gcode"

    allocated = system._capture_service.reserve_path(
        system._gcode_output_path,
        split_gcode_layers=True,
    )

    assert allocated == tmp_path / "piece_001.gcode"
    assert existing.read_text(encoding="utf-8") == "old"



class _RecordingSessionSpy:
    def __init__(self) -> None:
        self.is_recording = False
        self.calls: list[tuple[object, ...]] = []

    def start(self) -> None:
        self.calls.append(("start",))
        self.is_recording = True

    def stop(
        self,
        *,
        timeout_s: float,
        stop_reason: str,
        abort_reason: str | None,
    ) -> None:
        self.calls.append(("stop", float(timeout_s), stop_reason, abort_reason))
        self.is_recording = False


def test_video_shortcut_and_methods_only_delegate_to_recording_session() -> None:
    system = _make_initialized_system()
    recording = _RecordingSessionSpy()
    system._recording_session = cast(Any, recording)

    system._on_key_press(pyglet.window.key.V, 0)
    system._on_key_press(pyglet.window.key.V, 0)
    system.start_video_recording()
    system.stop_video_recording(
        timeout_s=2.5,
        stop_reason="shutdown",
        abort_reason="application_close",
    )

    assert recording.calls == [
        ("start",),
        ("stop", 30.0, "user_stop", None),
        ("start",),
        ("stop", 2.5, "shutdown", "application_close"),
    ]
