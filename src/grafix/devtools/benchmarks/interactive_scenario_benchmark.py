"""UX-01: parameter edit から表示準備完了までの hosted benchmark。"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import numpy as np

from grafix.core.authoring_definitions import AuthoringDefinitionsSnapshot
from grafix.authoring_loader import authoring_definitions_for_draw
from grafix.core.builtins import (
    ensure_builtin_effect_registered,
    ensure_builtin_primitive_registered,
)
from grafix.core.evaluation_context import EvaluationContext
from grafix.core.evaluation_config import EvaluationConfig
from grafix.core.geometry import Geometry
from grafix.core.layer import LayerStyleDefaults
from grafix.core.parameters.context import current_param_snapshot
from grafix.core.parameters.history import ParamStoreHistory
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.core.realize import RealizeSession
from grafix.core.runtime_config import RuntimeConfig
from grafix.runtime_config_loader import runtime_config
from grafix.devtools.benchmarks import renderer_benchmark
from grafix.devtools.benchmarks.definition import CaseDefinition, define_case
from grafix.devtools.benchmarks.parameter_hotpath_benchmark import (
    parameter_store_fixture,
)
from grafix.devtools.benchmarks.schema import (
    BenchmarkOutput,
    Metric,
    evaluate_contract,
    summarize_distribution,
)
from grafix.interactive.gl import draw_renderer as renderer_module
from grafix.interactive.gl.index_buffer import build_line_indices_and_stats
from grafix.interactive.parameter_gui.catalog import ParameterGuiCatalog
from grafix.interactive.parameter_gui.table_view import (
    ParameterTableViewCache,
    parameter_table_view_for_store,
)
from grafix.interactive.runtime.perf import PerfCollector
from grafix.interactive.runtime.scene_runner import SceneRunner

_SCOPE = "hosted:fake-gui+scene-runner+fake-gl+present-marker"
_SLIDER_KEY = ParameterKey(
    op="line",
    site_id="model-bench-000000",
    arg="length",
)
_BASE_LINE = Geometry.create(
    "line",
    params={"activate": True, "length": 2.0, "angle": 0.0},
)


def case_definitions() -> tuple[CaseDefinition, ...]:
    """UX-01 hosted slider benchmark cases を返す。"""

    def slider_case(
        case_id: str,
        label: str,
        *,
        parameters: dict[str, object],
        tags: tuple[str, ...],
        selectable_suites: tuple[str, ...],
    ) -> CaseDefinition:
        return define_case(
            case_id,
            label,
            category="interactive",
            suite="interactive",
            fixture="parameter_store_light_scale_slider",
            parameters=parameters,
            tags=tags,
            selectable_suites=selectable_suites,
            setup=setup_interactive_slider_scenario,
            workload=workload_interactive_slider_scenario,
            support_source_files=(
                Path(__file__),
                Path(__file__).with_name("parameter_hotpath_benchmark.py"),
                Path(__file__).with_name("renderer_benchmark.py"),
            ),
            self_sampling=True,
        )

    return (
        slider_case(
            "interactive.slider.input_to_present.rows_32.workers_0",
            "UX-01 hosted slider input-to-present / sync",
            parameters={
                "rows": 32,
                "workers": 0,
                "warmup_frames": 3,
                "drag_frames": 12,
                "settle_frames": 4,
                "frame_interval_s": 0.0,
                "settle_timeout_s": 1.0,
                "latency_guardrail_ms": 16.667,
            },
            tags=("UX-01", "input-to-present", "fake-gui", "fake-gl", "sync"),
            selectable_suites=("smoke", "interactive"),
        ),
        slider_case(
            "interactive.slider.input_to_present.rows_1000.workers_0",
            "UX-01 hosted slider input-to-present / 1,000 rows",
            parameters={
                "rows": 1_000,
                "workers": 0,
                "warmup_frames": 2,
                "drag_frames": 8,
                "settle_frames": 3,
                "frame_interval_s": 0.0,
                "settle_timeout_s": 1.0,
                "latency_guardrail_ms": 16.667,
            },
            tags=(
                "UX-01",
                "input-to-present",
                "fake-gui",
                "fake-gl",
                "large-parameter-table",
                "sync",
            ),
            selectable_suites=("interactive",),
        ),
        slider_case(
            "interactive.slider.input_to_present.rows_32.workers_1",
            "UX-01 hosted slider input-to-present / 1 worker",
            parameters={
                "rows": 32,
                "workers": 1,
                "warmup_frames": 4,
                "drag_frames": 12,
                "settle_frames": 6,
                "frame_interval_s": 1.0 / 60.0,
                "settle_timeout_s": 2.0,
                "latency_guardrail_ms": 50.0,
            },
            tags=("UX-01", "input-to-present", "fake-gui", "fake-gl", "multiprocessing"),
            selectable_suites=("interactive",),
        ),
    )


def setup_interactive_slider_scenario(
    parameters: dict[str, Any],
    _seed: int,
) -> object:
    """Hosted slider scenario を構築する。"""

    return make_interactive_slider_scenario(parameters)


def workload_interactive_slider_scenario(state: object) -> BenchmarkOutput:
    """Hosted slider scenario を一回実行する。"""

    if not isinstance(state, InteractiveSliderScenario):
        raise TypeError("interactive slider scenario state is invalid")
    return run_interactive_slider_scenario(state)


@dataclass(frozen=True, slots=True)
class InteractiveSliderScenario:
    """1 回の deterministic hosted scenario と固定済み composition。"""

    rows: int
    workers: int
    warmup_frames: int
    drag_frames: int
    settle_frames: int
    frame_interval_s: float
    settle_timeout_s: float
    latency_guardrail_ms: float
    expected_mesh_checksum: str
    effective_config: RuntimeConfig = field(repr=False, compare=False)
    definitions: AuthoringDefinitionsSnapshot = field(repr=False, compare=False)
    gui_catalog: ParameterGuiCatalog = field(repr=False, compare=False)


@dataclass(slots=True)
class _PhaseSamples:
    frame_ms: list[float]
    fake_gui_ms: list[float]
    scene_runner_ms: list[float]
    fake_gl_ms: list[float]

    @classmethod
    def create(cls) -> _PhaseSamples:
        return cls(frame_ms=[], fake_gui_ms=[], scene_runner_ms=[], fake_gl_ms=[])


def interactive_slider_draw(_t: float) -> Geometry:
    """固定 topology の軽い scale scene を current snapshot から作る。"""

    entry = current_param_snapshot().get(_SLIDER_KEY)
    scale_x = 1.0 if entry is None else float(entry[1].ui_value)
    return Geometry.create(
        "scale",
        inputs=(_BASE_LINE,),
        params={
            "activate": True,
            "mode": "all",
            "auto_center": False,
            "pivot": (0.0, 0.0, 0.0),
            "scale": (scale_x, 1.0, 1.0),
        },
    )


def make_interactive_slider_scenario(
    parameters: dict[str, Any],
) -> InteractiveSliderScenario:
    """parameter と measurement 外で固定する composition を構築する。"""

    ensure_builtin_primitive_registered("line")
    ensure_builtin_effect_registered("scale")
    drag_frames = int(parameters["drag_frames"])
    rows = int(parameters["rows"])
    workers = int(parameters["workers"])
    warmup_frames = int(parameters["warmup_frames"])
    settle_frames = int(parameters["settle_frames"])
    frame_interval_s = float(parameters["frame_interval_s"])
    settle_timeout_s = float(parameters["settle_timeout_s"])
    latency_guardrail_ms = float(parameters["latency_guardrail_ms"])
    if rows < 1:
        raise ValueError("rows は 1 以上である必要があります")
    if workers < 0:
        raise ValueError("workers は 0 以上である必要があります")
    if (
        min(
            warmup_frames,
            drag_frames,
            settle_frames,
        )
        < 1
    ):
        raise ValueError("各 phase の frame 数は 1 以上である必要があります")
    if frame_interval_s < 0.0 or settle_timeout_s <= 0.0:
        raise ValueError("frame interval/timeout が不正です")
    if latency_guardrail_ms <= 0.0:
        raise ValueError("latency guardrail は正である必要があります")

    # UX measurement は slider edit 後の hot path が対象。pure config discovery と
    # candidate load は application composition と同じく setup で一度だけ確定する。
    effective_config = runtime_config()
    definitions = authoring_definitions_for_draw(
        interactive_slider_draw,
        config=effective_config,
    )
    return InteractiveSliderScenario(
        rows=rows,
        workers=workers,
        warmup_frames=warmup_frames,
        drag_frames=drag_frames,
        settle_frames=settle_frames,
        frame_interval_s=frame_interval_s,
        settle_timeout_s=settle_timeout_s,
        latency_guardrail_ms=latency_guardrail_ms,
        expected_mesh_checksum=_expected_mesh_checksum(
            2.0 + float(drag_frames) * 0.05,
            config=effective_config,
            definitions=definitions,
        ),
        effective_config=effective_config,
        definitions=definitions,
        gui_catalog=ParameterGuiCatalog.capture(
            definitions.operations,
            definitions.presets,
        ),
    )


def run_interactive_slider_scenario(
    scenario: InteractiveSliderScenario,
) -> BenchmarkOutput:
    """stable→changing→stable の input-to-present scenario を実行する。

    実 ImGui、window、OpenGL driver は含めない。ParameterTableView の準備、
    SceneRunner の draw/realize、fake DrawRenderer の mesh upload を通した直後を
    hosted present marker とする。
    """

    ensure_builtin_primitive_registered("line")
    ensure_builtin_effect_registered("scale")

    store = parameter_store_fixture(rows=scenario.rows)
    table_cache = ParameterTableViewCache(scenario.gui_catalog)
    target_meta = store.get_meta(_SLIDER_KEY)
    if target_meta is None:
        raise RuntimeError("UX-01 slider parameter metadata is missing")
    history = ParamStoreHistory(store)
    defaults = LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01)
    perf = PerfCollector(enabled=False)
    runner = SceneRunner(
        interactive_slider_draw,
        perf=perf,
        n_worker=scenario.workers,
        effective_config=scenario.effective_config,
        definitions=scenario.definitions,
    )
    renderer = renderer_benchmark.fake_renderer()
    renderer_benchmark.BenchmarkFakeMesh.instances.clear()

    baseline_value = 2.0
    final_value = 2.0 + float(scenario.drag_frames) * 0.05
    expected_checksum = scenario.expected_mesh_checksum
    phases = {
        "warmup": _PhaseSamples.create(),
        "drag": _PhaseSamples.create(),
        "settle": _PhaseSamples.create(),
    }
    input_started_ns: dict[int, int] = {}
    input_to_present_ms: list[float] = []
    presented_input_revisions: set[int] = set()
    revision_lags: list[int] = []
    measured_present_frames = 0
    fresh_present_frames = 0
    measured_scene_serial_advances = 0
    visible_rows = 0
    last_mesh_checksum: str | None = None
    last_presented_revision: int | None = None
    scene_serial = 0
    frame_index = 0

    ok, error = update_state_from_ui(
        store,
        _SLIDER_KEY,
        baseline_value,
        meta=target_meta,
        override=True,
    )
    if not ok:
        raise RuntimeError(f"baseline parameter edit failed: {error}")

    def present_frame(phase: str) -> None:
        nonlocal frame_index
        nonlocal fresh_present_frames
        nonlocal measured_scene_serial_advances
        nonlocal last_mesh_checksum
        nonlocal last_presented_revision
        nonlocal measured_present_frames
        nonlocal scene_serial
        nonlocal visible_rows

        phase_samples = phases[phase]
        frame_started = time.perf_counter_ns()

        gui_started = time.perf_counter_ns()
        view = parameter_table_view_for_store(
            store,
            cache=table_cache,
            show_inactive_params=True,
        )
        visible_rows = int(sum(view.visible_mask))
        phase_samples.fake_gui_ms.append(_elapsed_ms(gui_started, time.perf_counter_ns()))

        scene_started = time.perf_counter_ns()
        layers = runner.run(
            float(frame_index) / 60.0,
            store=store,
            cc_snapshot=None,
            defaults=defaults,
            recording=False,
            transport_epoch=0,
            quality="draft",
        )
        phase_samples.scene_runner_ms.append(_elapsed_ms(scene_started, time.perf_counter_ns()))

        mesh_started = time.perf_counter_ns()
        presented_revision = runner.last_realized_snapshot_revision
        current_checksum: str | None = None
        if layers and presented_revision is not None:
            fresh_scene = bool(runner.last_output_updated)
            if fresh_scene:
                scene_serial += 1
                measured_scene_serial_advances += int(phase != "warmup")
            for layer_index, layer in enumerate(layers):
                mesh, _stats = renderer.prepare_layer_mesh(
                    layer.realized,
                    cache_key=layer.cache_key,
                    scene_serial=scene_serial,
                    snapshot_revision=presented_revision,
                    dynamic_slot=layer_index,
                )
                if mesh is None:
                    continue
                benchmark_mesh = cast(
                    renderer_benchmark.BenchmarkFakeMesh,
                    mesh,
                )
                vertices = benchmark_mesh.last_vertices
                indices = benchmark_mesh.last_indices
                if vertices is not None and indices is not None:
                    current_checksum = _mesh_checksum(vertices, indices)
        marker_ns = time.perf_counter_ns()
        phase_samples.fake_gl_ms.append(_elapsed_ms(mesh_started, marker_ns))
        phase_samples.frame_ms.append(_elapsed_ms(frame_started, marker_ns))
        frame_index += 1

        if presented_revision is None or not layers:
            return
        measured_present_frames += int(phase != "warmup")
        current_revision = int(store.revision)
        lag = current_revision - int(presented_revision)
        if phase != "warmup":
            revision_lags.append(lag)
        fresh_present_frames += int(phase != "warmup" and runner.last_evaluation_succeeded is True)
        last_presented_revision = int(presented_revision)
        if current_checksum is not None:
            last_mesh_checksum = current_checksum
        # latest-wins worker が中間 revision を省略しても、production trace と
        # 同じく「初めて同値以上が表示された時刻」へ全 pending input を結ぶ。
        for input_revision, started_ns in input_started_ns.items():
            if (
                input_revision > int(presented_revision)
                or input_revision in presented_input_revisions
            ):
                continue
            presented_input_revisions.add(input_revision)
            input_to_present_ms.append(_elapsed_ms(started_ns, marker_ns))

    try:
        with patch.object(
            renderer_module,
            "LineMesh",
            renderer_benchmark.BenchmarkFakeMesh,
        ):
            for _ in range(scenario.warmup_frames):
                present_frame("warmup")
                _pace(scenario.frame_interval_s)

            warmup_deadline = time.monotonic() + scenario.settle_timeout_s
            while (
                runner.last_realized_snapshot_revision != store.revision
                and time.monotonic() < warmup_deadline
            ):
                present_frame("warmup")
                _pace(max(scenario.frame_interval_s, 0.001))

            # 初回 scene の layer-style discovery は user edit ではない。実 GUI と
            # 同様に drag 開始前の履歴基準へ取り込み、changing phase を patch
            # transaction の hot path として測る。
            history.synchronize()
            start_revision = int(store.revision)
            for step in range(1, scenario.drag_frames + 1):
                edit_started_ns = time.perf_counter_ns()
                drag_value = baseline_value + float(step) * 0.05
                with history.transaction(source="ux01-slider", patch=True):
                    ok, error = update_state_from_ui(
                        store,
                        _SLIDER_KEY,
                        drag_value,
                        meta=target_meta,
                        override=True,
                    )
                if not ok:
                    raise RuntimeError(f"drag parameter edit failed: {error}")
                input_started_ns[int(store.revision)] = edit_started_ns
                present_frame("drag")
                _pace(scenario.frame_interval_s)

            final_input_revision = int(store.revision)
            for _ in range(scenario.settle_frames):
                present_frame("settle")
                _pace(scenario.frame_interval_s)

            settle_deadline = time.monotonic() + scenario.settle_timeout_s
            while (
                runner.last_realized_snapshot_revision != final_input_revision
                and time.monotonic() < settle_deadline
            ):
                present_frame("settle")
                _pace(max(scenario.frame_interval_s, 0.001))
    finally:
        runner.close()
        perf.close()

    final_presented_revision = -1 if last_presented_revision is None else last_presented_revision
    final_input_delta = final_input_revision - start_revision
    final_presented_delta = final_presented_revision - start_revision
    fresh_ratio = (
        0.0
        if measured_present_frames == 0
        else float(fresh_present_frames) / float(measured_present_frames)
    )
    max_revision_lag = max(revision_lags, default=0)
    min_revision_lag = min(revision_lags, default=0)
    model_builds = int(table_cache.model_build_count)
    dynamic_mesh_entries = len(renderer._dynamic_meshes)
    latency_distribution = summarize_distribution(input_to_present_ms)
    latency_p95 = (
        scenario.settle_timeout_s * 1_000.0
        if latency_distribution.p95 is None
        else float(latency_distribution.p95)
    )

    metrics: list[Metric] = []
    for phase in ("warmup", "drag", "settle"):
        samples = phases[phase]
        metrics.extend(
            (
                _distribution_metric(
                    f"ux01.frame_duration.{phase}",
                    phase,
                    samples.frame_ms,
                ),
                _distribution_metric(
                    f"ux01.fake_gui_prepare_duration.{phase}",
                    phase,
                    samples.fake_gui_ms,
                ),
                _distribution_metric(
                    f"ux01.scene_runner_duration.{phase}",
                    phase,
                    samples.scene_runner_ms,
                ),
                _distribution_metric(
                    f"ux01.fake_gl_mesh_duration.{phase}",
                    phase,
                    samples.fake_gl_ms,
                ),
            )
        )
    metrics.extend(
        (
            Metric(
                name="ux01.input_to_present",
                kind="distribution",
                unit="ms",
                phase="drag",
                scope=_SCOPE,
                distribution=latency_distribution,
            ),
            _gauge(
                "ux01.fresh_ratio",
                fresh_ratio,
                unit="ratio",
            ),
            _gauge(
                "ux01.max_revision_lag",
                max_revision_lag,
                unit="revisions",
            ),
            _gauge(
                "ux01.final_input_revision_delta",
                final_input_delta,
                unit="revisions",
            ),
            _gauge(
                "ux01.final_presented_revision_delta",
                final_presented_delta,
                unit="revisions",
            ),
            _counter("ux01.input_edits", scenario.drag_frames),
            _counter("ux01.fresh_present_frames", fresh_present_frames),
            _counter(
                "ux01.scene_serial_advances",
                measured_scene_serial_advances,
            ),
            _counter(
                "ux01.presented_input_revisions",
                len(presented_input_revisions),
            ),
            _counter("ux01.parameter_rows", visible_rows),
            _counter("ux01.table_model_builds", model_builds),
            _counter("ux01.dynamic_mesh_entries", dynamic_mesh_entries),
            Metric(
                name="ux01.final_mesh_checksum",
                kind="gauge",
                unit="sha256",
                phase="settle",
                scope=_SCOPE,
                value=last_mesh_checksum or "",
            ),
        )
    )

    contracts = (
        evaluate_contract(
            contract_id="ux01.progress.final_revision_presented",
            severity="hard",
            actual=final_presented_delta,
            comparator="eq",
            limit=final_input_delta,
            reason="settle 終了時に最後の input revision が表示準備済みである",
        ),
        evaluate_contract(
            contract_id="ux01.progress.presented_input",
            severity="hard",
            actual=len(presented_input_revisions),
            comparator="gt",
            limit=0,
            reason="drag 中または settle 中に少なくとも 1 input が表示へ進む",
        ),
        evaluate_contract(
            contract_id="ux01.progress.all_inputs_matched",
            severity="hard",
            actual=len(presented_input_revisions),
            comparator="eq",
            limit=scenario.drag_frames,
            reason="latest-winsで省略された中間inputも後続presentへ対応付ける",
        ),
        evaluate_contract(
            contract_id="ux01.progress.revision_not_ahead",
            severity="hard",
            actual=min_revision_lag,
            comparator="ge",
            limit=0,
            reason="表示 revision が入力済み store revision を追い越さない",
        ),
        evaluate_contract(
            contract_id="ux01.semantic.final_checksum",
            severity="hard",
            actual=last_mesh_checksum or "",
            comparator="eq",
            limit=expected_checksum,
            reason="最後に表示準備した mesh が最終 slider 値と一致する",
        ),
        evaluate_contract(
            contract_id="ux01.bounds.table_model_builds",
            severity="hard",
            actual=model_builds,
            comparator="le",
            limit=3,
            reason="値変更だけの drag で静的 table model を毎 frame 再構築しない",
        ),
        evaluate_contract(
            contract_id="ux01.bounds.dynamic_mesh_entries",
            severity="hard",
            actual=dynamic_mesh_entries,
            comparator="le",
            limit=1,
            reason="単一 layer scenario の dynamic mesh pool が有界である",
        ),
        evaluate_contract(
            contract_id="ux01.semantic.scene_serial_is_fresh_only",
            severity="hard",
            actual=measured_scene_serial_advances,
            comparator="eq",
            limit=fresh_present_frames,
            reason="production と同様に fresh scene だけ serial を進める",
        ),
        evaluate_contract(
            contract_id="ux01.guardrail.input_to_present_p95",
            severity="soft",
            actual=latency_p95,
            comparator="le",
            limit=scenario.latency_guardrail_ms,
            reason="hosted input-to-present p95 の回帰を通知する",
        ),
        evaluate_contract(
            contract_id="ux01.guardrail.fresh_ratio",
            severity="soft",
            actual=fresh_ratio,
            comparator="ge",
            limit=0.9,
            reason="drag/settle の表示 frame で新しい結果が継続的に到着する",
        ),
    )
    semantic_value: dict[str, object] = {
        "scenario": "UX-01",
        "scope": _SCOPE,
        "rows": scenario.rows,
        "workers": scenario.workers,
        "drag_inputs": scenario.drag_frames,
        "final_value": final_value,
        "final_input_revision_delta": final_input_delta,
        "final_presented_revision_delta": final_presented_delta,
        "final_mesh_checksum": last_mesh_checksum,
        "expected_final_mesh_checksum": expected_checksum,
    }
    return BenchmarkOutput(
        value=semantic_value,
        metrics=tuple(metrics),
        contracts=contracts,
    )


def _expected_mesh_checksum(
    scale_x: float,
    *,
    config: RuntimeConfig,
    definitions: AuthoringDefinitionsSnapshot,
) -> str:
    geometry = _scaled_line(float(scale_x))
    context = EvaluationContext(
        catalog=definitions.operations,
        quality="final",
        config=EvaluationConfig(font_dirs=config.font_dirs),
    )
    with RealizeSession(context=context) as session:
        realized = session.realize(geometry)
    indices, _stats = build_line_indices_and_stats(realized.offsets)
    return _mesh_checksum(realized.coords, indices)


def _scaled_line(scale_x: float) -> Geometry:
    return Geometry.create(
        "scale",
        inputs=(_BASE_LINE,),
        params={
            "activate": True,
            "mode": "all",
            "auto_center": False,
            "pivot": (0.0, 0.0, 0.0),
            "scale": (float(scale_x), 1.0, 1.0),
        },
    )


def _mesh_checksum(vertices: np.ndarray, indices: np.ndarray) -> str:
    digest = hashlib.sha256(b"grafix.ux01.fake-mesh.v1\0")
    for value in (vertices, indices):
        array = np.ascontiguousarray(value)
        digest.update(array.dtype.str.encode("ascii"))
        digest.update(str(tuple(array.shape)).encode("ascii"))
        digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def _elapsed_ms(started_ns: int, finished_ns: int) -> float:
    return float(finished_ns - started_ns) / 1_000_000.0


def _pace(interval_s: float) -> None:
    if interval_s > 0.0:
        time.sleep(interval_s)


def _distribution_metric(
    name: str,
    phase: str,
    samples: list[float],
) -> Metric:
    return Metric(
        name=name,
        kind="distribution",
        unit="ms",
        phase=phase,
        scope=_SCOPE,
        distribution=summarize_distribution(samples),
    )


def _gauge(name: str, value: int | float, *, unit: str) -> Metric:
    return Metric(
        name=name,
        kind="gauge",
        unit=unit,
        phase="settle",
        scope=_SCOPE,
        value=value,
    )


def _counter(name: str, value: int) -> Metric:
    return Metric(
        name=name,
        kind="counter",
        unit="count",
        phase="settle",
        scope=_SCOPE,
        value=int(value),
    )


__all__ = [
    "case_definitions",
    "InteractiveSliderScenario",
    "interactive_slider_draw",
    "make_interactive_slider_scenario",
    "run_interactive_slider_scenario",
]
