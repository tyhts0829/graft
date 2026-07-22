"""mp-draw の起動・first result・steady throughput を sync と比較する。"""

from __future__ import annotations

import statistics
import time
from functools import partial
from pathlib import Path
from typing import Any, cast

from grafix.api import E, G
from grafix.core.geometry import Geometry
from grafix.core.parameters import ParamStore, parameter_context
from grafix.core.parameters.context import parameter_context_from_snapshot
from grafix.core.parameters.snapshot_ops import ParamSnapshot, store_snapshot
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.core.scene import normalize_scene
from grafix.runtime_config_loader import runtime_config
from grafix.devtools.benchmarks.definition import CaseDefinition, define_case
from grafix.devtools.benchmarks.metrics import (
    counter_metric,
    gauge_metric,
    percentile_summary_metrics,
    summary_metrics,
)
from grafix.devtools.benchmarks.schema import (
    BenchmarkOutput,
    ContractResult,
    Metric,
    evaluate_contract,
)
from grafix.interactive.runtime.mp_draw import MpDraw

_RESULT_TIMEOUT_S = 30.0
_SLIDER_FRESH_RESULT_TARGET = 0.90
_SLIDER_MAX_STALE_FRAMES_TARGET = 2
_SLIDER_REVISION_LAG_P95_TARGET = 2.0
_SLIDER_INPUT_RESULT_P95_MS_TARGET = 50.0
_SLIDER_FINAL_REVISION_MS_TARGET = 100.0


def case_definitions() -> tuple[CaseDefinition, ...]:
    """Multiprocessing draw benchmark cases を返す。"""

    return (
        define_case(
            "mp.draw.light",
            "MpDraw light sync / worker",
            category="mp",
            suite="mp",
            fixture="normalized_scene",
            parameters={"repeats": 1, "steady_frames": 8, "heavy_iterations": 1_000},
            tags=("multiprocessing", "draw-normalize"),
            selectable_suites=("mp",),
            setup=setup_passthrough,
            workload=workload_mp_draw,
            support_source_files=(Path(__file__),),
            self_sampling=True,
        ),
        define_case(
            "mp.draw.slider_churn",
            "MpDraw 1-worker slider revision churn",
            category="mp",
            suite="mp",
            fixture="light_translate_scale_slider",
            parameters={"frames": 120, "frame_interval_s": 1.0 / 60.0},
            tags=("multiprocessing", "slider", "revision-churn", "input-to-result"),
            selectable_suites=("mp",),
            setup=setup_passthrough,
            workload=workload_mp_slider_churn,
            support_source_files=(Path(__file__),),
            self_sampling=True,
        ),
    )


def setup_passthrough(parameters: dict[str, Any], _seed: int) -> object:
    """JSON parameter をそのまま workload state にする。"""

    return parameters


def light_draw(_t: float) -> Geometry:
    """Queue/dispatch overhead を見るための最小 draw。"""

    return Geometry.create(op="concat")


def heavy_draw(t: float, *, iterations: int = 100_000) -> Geometry:
    """約1フレーム分のCPU workを行う決定的draw。"""

    value = int(float(t) * 1_000.0) & 0xFFFFFFFF
    for index in range(max(1, int(iterations))):
        value = ((value * 1_664_525) + (index ^ 1_013_904_223)) & 0xFFFFFFFF
    if value < 0:  # pragma: no cover - loop が消去されないことを明示する到達不能分岐
        raise AssertionError(value)
    return Geometry.create(op="concat")


def light_translate_draw(_t: float) -> Geometry:
    """slider churn 用の 2 頂点 translate scene。"""

    line = G.line(
        center=(0.0, 0.0, 0.0),
        length=1.0,
        key="mp-slider-line",
    )
    return E.translate(
        delta=(0.0, 0.0, 0.0),
        key="mp-slider-translate",
    )(line)


def light_scale_draw(_t: float) -> Geometry:
    """slider churn 用の 2 頂点 scale scene。"""

    line = G.line(
        center=(0.0, 0.0, 0.0),
        length=1.0,
        key="mp-slider-line",
    )
    return E.scale(
        mode="all",
        auto_center=False,
        pivot=(0.0, 0.0, 0.0),
        scale=(1.0, 1.0, 1.0),
        key="mp-slider-scale",
    )(line)


def run_mp_slider_churn_benchmarks(
    *,
    frames: int = 120,
    frame_interval_s: float = 1.0 / 60.0,
) -> dict[str, Any]:
    """1-worker preview の stable/changing revision 応答を測定する。

    wall time は環境依存なので結果へ保存するだけとし、revision の単調進行、
    最終 revision、最終 Geometry checksum を正しさの指標として返す。
    """

    frame_count = max(1, int(frames))
    interval = max(0.0, float(frame_interval_s))
    cases: dict[str, Any] = {}
    for case_id, draw, op, arg in (
        ("light_translate", light_translate_draw, "translate", "delta"),
        ("light_scale", light_scale_draw, "scale", "scale"),
    ):
        cases[case_id] = {
            "stable": _measure_slider_sequence(
                draw,
                op=op,
                arg=arg,
                frames=frame_count,
                frame_interval_s=interval,
                changing=False,
            ),
            "changing": _measure_slider_sequence(
                draw,
                op=op,
                arg=arg,
                frames=frame_count,
                frame_interval_s=interval,
                changing=True,
            ),
        }

    ratios = [
        float(mode["fresh_result_ratio"]) for case in cases.values() for mode in case.values()
    ]
    progress_contract_met = all(
        bool(mode["progress_contract_met"]) for case in cases.values() for mode in case.values()
    )
    return {
        "id": "mp_draw_slider_churn",
        "label": "MpDraw 1-worker slider revision churn",
        "category": "mp",
        "status": "ok" if progress_contract_met else "regression",
        "mean_ms": statistics.fmean(
            float(mode["elapsed_ms"]) for case in cases.values() for mode in case.values()
        ),
        "median_ms": statistics.median(
            float(mode["elapsed_ms"]) for case in cases.values() for mode in case.values()
        ),
        "p95_ms": max(
            float(mode["elapsed_ms"]) for case in cases.values() for mode in case.values()
        ),
        "n": len(ratios),
        "output": {
            "frames": frame_count,
            "frame_interval_s": interval,
            "n_worker": 1,
            "measurement_scope": "draw + normalize_scene (realize excluded)",
            "interactive_targets": {
                "fresh_result_ratio_min": _SLIDER_FRESH_RESULT_TARGET,
                "max_consecutive_stale_frames": (_SLIDER_MAX_STALE_FRAMES_TARGET),
                "revision_lag_p95_max": _SLIDER_REVISION_LAG_P95_TARGET,
                "input_to_result_p95_ms_max": (_SLIDER_INPUT_RESULT_P95_MS_TARGET),
                "final_revision_latency_ms_max": (_SLIDER_FINAL_REVISION_MS_TARGET),
            },
            "progress_contract_met": progress_contract_met,
        },
        "cases": cases,
    }


def _measure_slider_sequence(
    draw: Any,
    *,
    op: str,
    arg: str,
    frames: int,
    frame_interval_s: float,
    changing: bool,
) -> dict[str, Any]:
    store = ParamStore()
    with parameter_context(store):
        normalize_scene(draw(0.0))
    key = next(key for key in store_snapshot(store) if key.op == op and key.arg == arg)
    meta = store.get_meta(key)
    if meta is None:
        raise RuntimeError(f"slider benchmark metadata is missing: {op}.{arg}")

    frame_submitted_at: dict[int, float] = {}
    received_revisions: list[int] = []
    revision_lags: list[float] = []
    input_to_result_ms: list[float] = []
    fresh_frames = 0
    consecutive_stale = 0
    max_consecutive_stale = 0

    mp_draw = MpDraw(draw, n_worker=1, effective_config=runtime_config())
    started_at = time.monotonic()
    final_snapshot: ParamSnapshot = store_snapshot(store)
    final_revision = int(store.revision)
    final_created_at = started_at
    final_result = None
    submitted = 0
    try:
        for frame in range(int(frames)):
            frame_started_at = time.monotonic()
            if changing:
                ratio = float(frame + 1) / float(max(1, frames))
                ui_value: object
                if op == "translate":
                    ui_value = (ratio * 10.0, 0.0, 0.0)
                else:
                    ui_value = (1.0 + ratio, 1.0 + ratio, 1.0)
                ok, error = update_state_from_ui(
                    store,
                    key,
                    ui_value,
                    meta=meta,
                    override=True,
                )
                if not ok:
                    raise RuntimeError(f"slider benchmark update failed: {op}.{arg}: {error}")

            final_snapshot = store_snapshot(store)
            final_revision = int(store.revision)
            final_created_at = time.monotonic()
            frame_submitted_at[submitted + 1] = final_created_at
            mp_draw.submit(
                t=float(frame),
                snapshot_revision=final_revision,
                snapshot=final_snapshot,
                effect_order_snapshot={},
                epoch=0,
                quality="draft",
            )
            submitted += 1
            result = mp_draw.poll_latest()
            if result is None:
                consecutive_stale += 1
                max_consecutive_stale = max(max_consecutive_stale, consecutive_stale)
            else:
                consecutive_stale = 0
                fresh_frames += 1
                result_revision = int(result.snapshot_revision)
                received_revisions.append(result_revision)
                revision_lags.append(float(max(0, final_revision - result_revision)))
                created_at = frame_submitted_at.get(int(result.frame_id))
                if created_at is not None:
                    input_to_result_ms.append(max(0.0, (time.monotonic() - created_at) * 1_000.0))

            sleep_s = float(frame_interval_s) - (time.monotonic() - frame_started_at)
            if sleep_s > 0.0:
                time.sleep(sleep_s)

        expected_checksum = _scene_checksum(draw, final_snapshot)
        deadline = time.monotonic() + _RESULT_TIMEOUT_S
        while time.monotonic() < deadline:
            result = mp_draw.poll_latest()
            if result is not None:
                result_revision = int(result.snapshot_revision)
                received_revisions.append(result_revision)
                if result_revision == final_revision:
                    final_result = result
                    break
            mp_draw.submit(
                t=float(frames),
                snapshot_revision=final_revision,
                snapshot=final_snapshot,
                effect_order_snapshot={},
                epoch=0,
                quality="draft",
            )
            submitted += 1
            time.sleep(0.0002)
        if final_result is None:
            raise TimeoutError("mp-draw slider final revision timeout")

        final_checksum = tuple(str(layer.geometry.id) for layer in final_result.layers)
        final_latency_ms = max(0.0, (time.monotonic() - final_created_at) * 1_000.0)
        monotonic_revisions = received_revisions == sorted(received_revisions)
        fresh_result_ratio = float(fresh_frames) / float(max(1, frames))
        revision_lag = _summarize_distribution(revision_lags)
        input_to_result = _summarize_distribution(input_to_result_ms)
        minimum_progress_results = max(1, (int(frames) + 1) // 2)
        maximum_progress_streak = max(1, (int(frames) + 3) // 4)
        progress_contract_met = (
            fresh_frames >= minimum_progress_results
            and max_consecutive_stale <= maximum_progress_streak
            and monotonic_revisions
            and int(final_result.snapshot_revision) == final_revision
            and final_checksum == expected_checksum
            and mp_draw.rejected_task_count == 0
        )
        interactive_target_met = (
            fresh_result_ratio >= _SLIDER_FRESH_RESULT_TARGET
            and max_consecutive_stale <= _SLIDER_MAX_STALE_FRAMES_TARGET
            and float(revision_lag["p95"]) <= _SLIDER_REVISION_LAG_P95_TARGET
            and float(input_to_result["p95"]) <= _SLIDER_INPUT_RESULT_P95_MS_TARGET
            and final_latency_ms <= _SLIDER_FINAL_REVISION_MS_TARGET
            and progress_contract_met
        )
        return {
            "fresh_result_ratio": fresh_result_ratio,
            "fresh_results_during_drag": fresh_frames,
            "max_consecutive_stale_frames": max_consecutive_stale,
            "revision_lag": revision_lag,
            "input_to_result_ms": input_to_result,
            "final_revision_latency_ms": final_latency_ms,
            "first_result_revision": (None if not received_revisions else received_revisions[0]),
            "last_result_revision": int(final_result.snapshot_revision),
            "final_input_revision": final_revision,
            "result_revisions_monotonic": monotonic_revisions,
            "final_geometry_checksum": final_checksum,
            "expected_geometry_checksum": expected_checksum,
            "checksum_matches_sync": final_checksum == expected_checksum,
            "snapshot_broadcasts": mp_draw.snapshot_broadcast_count,
            "snapshot_payload_copies": mp_draw.snapshot_payload_copy_count,
            "snapshot_acks": mp_draw.snapshot_ack_count,
            "submitted_tasks": submitted,
            "enqueued_tasks": mp_draw.task_enqueue_count,
            "dropped_tasks": mp_draw.task_drop_count,
            "completed_results": mp_draw.completed_result_count,
            "rejected_tasks": mp_draw.rejected_task_count,
            "progress_contract_met": progress_contract_met,
            "interactive_target_met": interactive_target_met,
            "elapsed_ms": max(0.0, (time.monotonic() - started_at) * 1_000.0),
        }
    finally:
        mp_draw.close()


def _scene_checksum(draw: Any, snapshot: ParamSnapshot) -> tuple[str, ...]:
    with parameter_context_from_snapshot(snapshot):
        layers = normalize_scene(draw(0.0))
    return tuple(str(layer.geometry.id) for layer in layers)


def run_mp_draw_benchmarks(
    *,
    repeats: int,
    steady_frames: int,
    heavy_iterations: int,
    n_worker: int = 4,
) -> dict[str, Any]:
    """light/heavy drawをsync 1本とMpDraw worker群で比較する。"""

    repeat_count = max(1, int(repeats))
    frame_count = max(int(n_worker), int(steady_frames))
    worker_count = max(2, int(n_worker))
    cases: dict[str, Any] = {}

    for case_id, draw in (
        ("light", light_draw),
        (
            "heavy",
            partial(heavy_draw, iterations=int(heavy_iterations)),
        ),
    ):
        sync_samples = [_measure_sync(draw, steady_frames=frame_count) for _ in range(repeat_count)]
        mp_samples = [
            _measure_mp(
                draw,
                n_worker=worker_count,
                steady_frames=frame_count,
            )
            for _ in range(repeat_count)
        ]
        sync_summary = _summarize_mode(sync_samples)
        mp_summary = _summarize_mode(mp_samples)
        sync_fps = float(sync_summary["steady_latest_fps"]["median"])
        mp_fps = float(mp_summary["steady_latest_fps"]["median"])
        cases[case_id] = {
            "sync_n1": sync_summary,
            f"mp_n{worker_count}": mp_summary,
            "mp_to_sync_steady_ratio": 0.0 if sync_fps <= 0.0 else mp_fps / sync_fps,
        }

    total_samples_ms = [
        float(sample["startup_ms"] + sample["first_result_ms"] + sample["steady_ms"])
        for case in cases.values()
        for mode_name, mode in case.items()
        if str(mode_name).startswith(("sync_", "mp_")) and isinstance(mode, dict)
        for sample in mode["samples"]
    ]
    total = _summarize(total_samples_ms)
    return {
        "id": "mp_draw_n_worker",
        "label": "MpDraw sync n=1 vs multiprocessing",
        "category": "system",
        "status": "ok",
        "mean_ms": total["mean"],
        "median_ms": total["median"],
        "p95_ms": total["p95"],
        "n": total["n"],
        "output": {
            "steady_frames": frame_count,
            "heavy_iterations": int(heavy_iterations),
            "n_worker": worker_count,
            "measurement_scope": "draw + normalize_scene (realize excluded)",
        },
        "cases": cases,
    }


def _measure_sync(draw: Any, *, steady_frames: int) -> dict[str, float | int]:
    first_started = time.perf_counter_ns()
    normalize_scene(draw(0.0))
    first_ns = time.perf_counter_ns() - first_started

    steady_started = time.perf_counter_ns()
    for frame in range(int(steady_frames)):
        normalize_scene(draw(float(frame + 1)))
    steady_ns = time.perf_counter_ns() - steady_started
    return {
        "startup_ms": 0.0,
        "first_result_ms": first_ns / 1_000_000.0,
        "steady_ms": steady_ns / 1_000_000.0,
        "steady_latest_fps": float(steady_frames) * 1_000_000_000.0 / steady_ns,
        "submitted_frames": int(steady_frames),
        "completed_results": int(steady_frames),
    }


def _measure_mp(
    draw: Any,
    *,
    n_worker: int,
    steady_frames: int,
) -> dict[str, float | int]:
    effective_config = runtime_config()
    startup_started = time.perf_counter_ns()
    mp_draw = MpDraw(
        draw,
        n_worker=int(n_worker),
        effective_config=effective_config,
    )
    startup_ns = time.perf_counter_ns() - startup_started
    try:
        first_started = time.perf_counter_ns()
        mp_draw.submit(
            t=0.0,
            snapshot_revision=0,
            snapshot={},
            effect_order_snapshot={},
            epoch=0,
            quality="draft",
        )
        _wait_for_completed(mp_draw, target=1)
        first_ns = time.perf_counter_ns() - first_started

        baseline = mp_draw.completed_result_count
        submitted = 0
        steady_started = time.perf_counter_ns()
        deadline = time.monotonic() + _RESULT_TIMEOUT_S
        while mp_draw.completed_result_count - baseline < int(steady_frames):
            mp_draw.poll_latest()
            completed = mp_draw.completed_result_count - baseline
            while submitted < int(steady_frames) and submitted - completed < int(n_worker):
                mp_draw.submit(
                    t=float(submitted + 1),
                    snapshot_revision=0,
                    snapshot={},
                    effect_order_snapshot={},
                    epoch=0,
                    quality="draft",
                )
                submitted += 1
                completed = mp_draw.completed_result_count - baseline
            if time.monotonic() >= deadline:
                raise TimeoutError("mp-draw steady result timeout")
            if mp_draw.completed_result_count - baseline == completed:
                time.sleep(0.0002)
        steady_ns = time.perf_counter_ns() - steady_started
        completed = mp_draw.completed_result_count - baseline
        return {
            "startup_ms": startup_ns / 1_000_000.0,
            "first_result_ms": first_ns / 1_000_000.0,
            "steady_ms": steady_ns / 1_000_000.0,
            "steady_latest_fps": float(completed) * 1_000_000_000.0 / steady_ns,
            "submitted_frames": submitted,
            "completed_results": completed,
        }
    finally:
        mp_draw.close()


def _wait_for_completed(mp_draw: MpDraw, *, target: int) -> None:
    deadline = time.monotonic() + _RESULT_TIMEOUT_S
    while mp_draw.completed_result_count < int(target):
        mp_draw.poll_latest()
        if time.monotonic() >= deadline:
            raise TimeoutError("mp-draw first result timeout")
        time.sleep(0.0002)


def _summarize_mode(samples: list[dict[str, float | int]]) -> dict[str, Any]:
    return {
        "startup_ms": _summarize([float(sample["startup_ms"]) for sample in samples]),
        "first_result_ms": _summarize([float(sample["first_result_ms"]) for sample in samples]),
        "steady_ms": _summarize([float(sample["steady_ms"]) for sample in samples]),
        "steady_latest_fps": _summarize([float(sample["steady_latest_fps"]) for sample in samples]),
        "samples": samples,
    }


def _summarize(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {"mean": 0.0, "median": 0.0, "p95": 0.0, "n": 0}
    p95_index = min(len(ordered) - 1, max(0, int(0.95 * len(ordered))))
    return {
        "mean": statistics.fmean(ordered),
        "median": statistics.median(ordered),
        "p95": ordered[p95_index],
        "n": len(ordered),
    }


def _summarize_distribution(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {
            "median": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "max": 0.0,
            "n": 0,
        }

    def percentile(fraction: float) -> float:
        index = min(
            len(ordered) - 1,
            max(0, int(float(fraction) * len(ordered))),
        )
        return ordered[index]

    return {
        "median": statistics.median(ordered),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": ordered[-1],
        "n": len(ordered),
    }


def workload_mp_draw(state: object) -> BenchmarkOutput:
    payload = run_mp_draw_benchmarks(
        repeats=int(state["repeats"]),  # type: ignore[index]
        steady_frames=int(state["steady_frames"]),  # type: ignore[index]
        heavy_iterations=int(state["heavy_iterations"]),  # type: ignore[index]
        n_worker=2,
    )
    output = cast(dict[str, Any], payload["output"])
    metrics: list[Metric] = [
        gauge_metric(
            "mean_ms",
            float(payload["mean_ms"]),
            unit="ms",
            phase="measure",
            scope="mp_draw",
        ),
        gauge_metric(
            "median_ms",
            float(payload["median_ms"]),
            unit="ms",
            phase="measure",
            scope="mp_draw",
        ),
        gauge_metric(
            "p95_ms",
            float(payload["p95_ms"]),
            unit="ms",
            phase="measure",
            scope="mp_draw",
        ),
        counter_metric(
            "samples",
            int(payload["n"]),
            unit="count",
            phase="measure",
            scope="mp_draw",
        ),
        counter_metric(
            "steady_frames",
            int(output["steady_frames"]),
            unit="count",
            phase="measure",
            scope="mp_draw",
        ),
        counter_metric(
            "heavy_iterations",
            int(output["heavy_iterations"]),
            unit="count",
            phase="measure",
            scope="mp_draw",
        ),
        counter_metric(
            "n_worker",
            int(output["n_worker"]),
            unit="count",
            phase="measure",
            scope="mp_draw",
        ),
        gauge_metric(
            "measurement_scope",
            str(output["measurement_scope"]),
            unit="text",
            phase="measure",
            scope="mp_draw",
        ),
    ]
    for case_id, case in cast(dict[str, Any], payload["cases"]).items():
        metrics.append(
            gauge_metric(
                f"cases.{case_id}.mp_to_sync_steady_ratio",
                float(case["mp_to_sync_steady_ratio"]),
                unit="ratio",
                phase="measure",
                scope="mp_draw",
            )
        )
        for mode_name in ("sync_n1", f"mp_n{int(output['n_worker'])}"):
            mode = cast(dict[str, Any], case[mode_name])
            for summary_name, unit in (
                ("startup_ms", "ms"),
                ("first_result_ms", "ms"),
                ("steady_ms", "ms"),
                ("steady_latest_fps", "frames_per_second"),
            ):
                metrics.extend(
                    summary_metrics(
                        f"cases.{case_id}.{mode_name}.{summary_name}",
                        cast(dict[str, Any], mode[summary_name]),
                        unit=unit,
                        phase="measure",
                        scope="mp_draw",
                    )
                )
    return BenchmarkOutput(value=output, metrics=tuple(metrics))


def workload_mp_slider_churn(state: object) -> BenchmarkOutput:
    parameters = cast(dict[str, Any], state)
    payload = run_mp_slider_churn_benchmarks(
        frames=int(parameters["frames"]),
        frame_interval_s=float(parameters["frame_interval_s"]),
    )
    contracts: list[ContractResult] = []
    metrics: list[Metric] = [
        gauge_metric(
            "mean_ms",
            float(payload["mean_ms"]),
            unit="ms",
            phase="measure",
            scope="mp_slider",
        ),
        gauge_metric(
            "median_ms",
            float(payload["median_ms"]),
            unit="ms",
            phase="measure",
            scope="mp_slider",
        ),
        gauge_metric(
            "p95_ms",
            float(payload["p95_ms"]),
            unit="ms",
            phase="measure",
            scope="mp_slider",
        ),
        counter_metric(
            "samples",
            int(payload["n"]),
            unit="count",
            phase="measure",
            scope="mp_slider",
        ),
    ]
    for case_id, modes in cast(dict[str, Any], payload["cases"]).items():
        for mode_name, mode in cast(dict[str, Any], modes).items():
            prefix = f"{case_id}.{mode_name}"
            metric_prefix = f"cases.{prefix}"
            phase = "drag" if mode_name == "changing" else "settle"
            contracts.append(
                evaluate_contract(
                    contract_id=f"mp.slider.{prefix}.progress",
                    severity="hard",
                    actual=bool(mode["progress_contract_met"]),
                    comparator="eq",
                    limit=True,
                    reason=("revision、checksum、queue progress の invariant を満たす"),
                )
            )
            contracts.append(
                evaluate_contract(
                    contract_id=f"mp.slider.{prefix}.interactive_target",
                    severity="hard",
                    actual=bool(mode["interactive_target_met"]),
                    comparator="eq",
                    limit=True,
                    reason="slider の interactive latency target を満たす",
                )
            )
            for name, unit in (
                ("fresh_result_ratio", "ratio"),
                ("final_revision_latency_ms", "ms"),
                ("elapsed_ms", "ms"),
            ):
                metrics.append(
                    gauge_metric(
                        f"{metric_prefix}.{name}",
                        float(mode[name]),
                        unit=unit,
                        phase=phase,
                        scope="mp_slider",
                    )
                )
            for name in (
                "fresh_results_during_drag",
                "max_consecutive_stale_frames",
                "last_result_revision",
                "final_input_revision",
                "snapshot_broadcasts",
                "snapshot_payload_copies",
                "snapshot_acks",
                "submitted_tasks",
                "enqueued_tasks",
                "dropped_tasks",
                "completed_results",
                "rejected_tasks",
            ):
                metrics.append(
                    counter_metric(
                        f"{metric_prefix}.{name}",
                        int(mode[name]),
                        unit="count",
                        phase=phase,
                        scope="mp_slider",
                    )
                )
            for name in (
                "result_revisions_monotonic",
                "checksum_matches_sync",
                "progress_contract_met",
                "interactive_target_met",
            ):
                metrics.append(
                    gauge_metric(
                        f"{metric_prefix}.{name}",
                        bool(mode[name]),
                        unit="boolean",
                        phase=phase,
                        scope="mp_slider",
                    )
                )
            metrics.extend(
                percentile_summary_metrics(
                    f"{metric_prefix}.revision_lag",
                    cast(dict[str, Any], mode["revision_lag"]),
                    unit="revisions",
                    phase=phase,
                    scope="mp_slider",
                )
            )
            metrics.extend(
                percentile_summary_metrics(
                    f"{metric_prefix}.input_to_result_ms",
                    cast(dict[str, Any], mode["input_to_result_ms"]),
                    unit="ms",
                    phase=phase,
                    scope="mp_slider",
                )
            )
    output = cast(dict[str, Any], payload["output"])
    metrics.extend(
        (
            counter_metric(
                "frames",
                int(output["frames"]),
                unit="count",
                phase="measure",
                scope="mp_slider",
            ),
            gauge_metric(
                "frame_interval_s",
                float(output["frame_interval_s"]),
                unit="s",
                phase="measure",
                scope="mp_slider",
            ),
            counter_metric(
                "n_worker",
                int(output["n_worker"]),
                unit="count",
                phase="measure",
                scope="mp_slider",
            ),
            gauge_metric(
                "measurement_scope",
                str(output["measurement_scope"]),
                unit="text",
                phase="measure",
                scope="mp_slider",
            ),
            gauge_metric(
                "progress_contract_met",
                bool(output["progress_contract_met"]),
                unit="boolean",
                phase="measure",
                scope="mp_slider",
            ),
        )
    )
    return BenchmarkOutput(
        value=output,
        metrics=tuple(metrics),
        contracts=tuple(contracts),
    )


__all__ = [
    "case_definitions",
    "heavy_draw",
    "light_draw",
    "light_scale_draw",
    "light_translate_draw",
    "run_mp_draw_benchmarks",
    "run_mp_slider_churn_benchmarks",
]
