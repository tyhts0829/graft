from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import replace

import pytest

from grafix.api import G
from grafix.core.layer import Layer, LayerStyleDefaults
from grafix.core.parameters import ParamStore
from grafix.core.pipeline import realize_scene
from grafix.core.realize import RealizeSession
from grafix.core.runtime_limits import RuntimeLimits
from grafix.runtime_config_loader import runtime_config
from grafix.interactive.runtime.mp_draw import DrawResult
from grafix.interactive.runtime.perf import PerfCollector
from grafix.interactive.runtime.scene_runner import SceneRunner
from tests.interactive.runtime.scene_runner_fixture import MpDrawFactoryFixture


_TEST_RUNTIME_CONFIG = replace(runtime_config(), font_dirs=())


@pytest.mark.parametrize(
    ("kwargs", "error", "match"),
    [
        ({"enabled": 1}, TypeError, "enabled"),
        ({"enabled": False, "print_every": "60"}, TypeError, "print_every"),
        ({"enabled": False, "print_every": 0}, ValueError, "print_every"),
        ({"enabled": False, "gpu_finish": 1}, TypeError, "gpu_finish"),
        ({"enabled": False, "top_n": 0}, ValueError, "top_n"),
        ({"enabled": False, "max_series": 1}, ValueError, "max_series"),
        ({"enabled": False, "console_output": 0}, TypeError, "console_output"),
        ({"enabled": False, "trace_path": object()}, TypeError, "trace_path"),
        ({"enabled": False, "trace_path": ""}, ValueError, "trace_path"),
        ({"enabled": False, "frame_deadline_ms": True}, TypeError, "frame_deadline"),
        ({"enabled": False, "frame_deadline_ms": 0.0}, ValueError, "frame_deadline"),
        (
            {"enabled": False, "frame_deadline_ms": float("nan")},
            ValueError,
            "frame_deadline",
        ),
        (
            {"enabled": False, "snapshot_callback": object()},
            TypeError,
            "snapshot_callback",
        ),
        (
            {"enabled": False, "defer_frame_finalize": 0},
            TypeError,
            "defer_frame_finalize",
        ),
    ],
)
def test_perf_collector_rejects_implicit_configuration_coercion(
    kwargs: dict[str, object],
    error: type[Exception],
    match: str,
) -> None:
    with pytest.raises(error, match=match):
        PerfCollector(**kwargs)  # type: ignore[arg-type]


def test_perf_collector_from_env_requires_exact_bool_flags() -> None:
    with pytest.raises(TypeError, match="enabled_by_default"):
        PerfCollector.from_env(enabled_by_default=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="defer_frame_finalize"):
        PerfCollector.from_env(defer_frame_finalize=0)  # type: ignore[arg-type]


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize(
    ("invoke", "error", "match"),
    [
        (lambda perf: perf.section(1), TypeError, "name"),
        (lambda perf: perf.section(""), ValueError, "非空文字列"),
        (lambda perf: perf.section(" scene"), ValueError, "前後空白"),
        (
            lambda perf: perf.finish_frame(deadline_elapsed_ns="1"),
            TypeError,
            "deadline_elapsed_ns",
        ),
        (
            lambda perf: perf.finish_frame(deadline_elapsed_ns=-1),
            ValueError,
            "deadline_elapsed_ns",
        ),
        (lambda perf: perf.record_event(1), TypeError, "name"),
        (lambda perf: perf.record_event(""), ValueError, "非空文字列"),
        (
            lambda perf: perf.record_event("event", frame_id="1"),
            TypeError,
            "frame_id",
        ),
        (
            lambda perf: perf.record_event("event", frame_id=-1),
            ValueError,
            "frame_id",
        ),
        (
            lambda perf: perf.record_event("event", revision=1.0),
            TypeError,
            "revision",
        ),
        (
            lambda perf: perf.record_event("event", revision=-1),
            ValueError,
            "revision",
        ),
        (
            lambda perf: perf.record_event("event", timestamp_ns=True),
            TypeError,
            "timestamp_ns",
        ),
        (
            lambda perf: perf.record_event("event", timestamp_ns=-1),
            ValueError,
            "timestamp_ns",
        ),
        (
            lambda perf: perf.record_operation(object(), 1),
            TypeError,
            "name",
        ),
        (
            lambda perf: perf.record_operation("operation", "1"),
            TypeError,
            "elapsed_ns",
        ),
        (
            lambda perf: perf.record_operation("operation", -1),
            ValueError,
            "elapsed_ns",
        ),
        (
            lambda perf: perf.record_duration(object(), 1),
            TypeError,
            "name",
        ),
        (
            lambda perf: perf.record_duration("duration", 1.0),
            TypeError,
            "elapsed_ns",
        ),
        (
            lambda perf: perf.record_duration("duration", -1),
            ValueError,
            "elapsed_ns",
        ),
        (
            lambda perf: perf.record_layer(object(), 1),
            TypeError,
            "name",
        ),
        (
            lambda perf: perf.record_layer("layer", True),
            TypeError,
            "elapsed_ns",
        ),
        (
            lambda perf: perf.record_layer("layer", -1),
            ValueError,
            "elapsed_ns",
        ),
        (
            lambda perf: perf.record_cache(hits=True),
            TypeError,
            "hits",
        ),
        (
            lambda perf: perf.record_cache(misses=1.0),
            TypeError,
            "misses",
        ),
        (
            lambda perf: perf.record_cache(evictions="1"),
            TypeError,
            "evictions",
        ),
        (
            lambda perf: perf.record_cache(hits=-1),
            ValueError,
            "hits",
        ),
        (
            lambda perf: perf.record_cache(misses=-1),
            ValueError,
            "misses",
        ),
        (
            lambda perf: perf.record_cache(evictions=-1),
            ValueError,
            "evictions",
        ),
        (
            lambda perf: perf.record_worker_lag("1"),
            TypeError,
            "lag_ms",
        ),
        (
            lambda perf: perf.record_worker_lag(True),
            TypeError,
            "lag_ms",
        ),
        (
            lambda perf: perf.record_worker_lag(float("nan")),
            ValueError,
            "lag_ms",
        ),
        (
            lambda perf: perf.record_worker_lag(float("inf")),
            ValueError,
            "lag_ms",
        ),
        (
            lambda perf: perf.record_worker_lag(-1.0),
            ValueError,
            "lag_ms",
        ),
        (
            lambda perf: perf.record_preview_result(
                requested_revision="1",
                presented_revision=None,
                fresh=False,
            ),
            TypeError,
            "requested_revision",
        ),
        (
            lambda perf: perf.record_preview_result(
                requested_revision=-1,
                presented_revision=None,
                fresh=False,
            ),
            ValueError,
            "requested_revision",
        ),
        (
            lambda perf: perf.record_preview_result(
                requested_revision=1,
                presented_revision="0",
                fresh=False,
            ),
            TypeError,
            "presented_revision",
        ),
        (
            lambda perf: perf.record_preview_result(
                requested_revision=1,
                presented_revision=-1,
                fresh=False,
            ),
            ValueError,
            "presented_revision",
        ),
        (
            lambda perf: perf.record_preview_result(
                requested_revision=1,
                presented_revision=0,
                fresh=1,
            ),
            TypeError,
            "fresh",
        ),
        (
            lambda perf: perf.record_preview_result(
                requested_revision=1,
                presented_revision=2,
                fresh=False,
            ),
            ValueError,
            "presented_revision",
        ),
    ],
)
def test_perf_recording_apis_reject_noncanonical_values_even_when_disabled(
    enabled: bool,
    invoke: Callable[[PerfCollector], object],
    error: type[Exception],
    match: str,
) -> None:
    perf = PerfCollector(enabled=enabled, console_output=False)

    with pytest.raises(error, match=match):
        invoke(perf)


def test_record_event_rejects_negative_causal_latency_instead_of_clamping() -> None:
    perf = PerfCollector(enabled=True, console_output=False)
    perf.record_event(
        "parameter_revision_created",
        revision=1,
        timestamp_ns=10,
    )

    with pytest.raises(ValueError, match="timestamp_ns"):
        perf.record_event(
            "preview_presented",
            revision=1,
            timestamp_ns=9,
        )


def test_profiler_collects_bounded_operation_layer_and_cache_snapshot() -> None:
    perf = PerfCollector(
        enabled=True,
        console_output=False,
        top_n=2,
        max_series=4,
    )
    geometry = G.polygon(n_sides=5)
    defaults = LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01)

    with RealizeSession(profiler=perf) as session:
        with perf.frame():
            realize_scene(
                lambda _t: Layer(geometry, site_id="ink", name="Ink"),
                0.0,
                defaults,
                config=_TEST_RUNTIME_CONFIG,
                session=session,
            )
        with perf.frame():
            realize_scene(
                lambda _t: Layer(geometry, site_id="ink", name="Ink"),
                1.0,
                defaults,
                config=_TEST_RUNTIME_CONFIG,
                session=session,
            )

    snapshot = perf.snapshot()
    assert snapshot.frame_count == 2
    assert len(snapshot.operations) <= 2
    assert len(snapshot.layers) <= 2
    assert snapshot.operations[0].name == "polygon"
    assert snapshot.layers[0].name == "Ink"
    assert snapshot.cache_hits >= 1
    assert snapshot.cache_misses >= 1
    assert snapshot.cache_hit_rate == pytest.approx(0.5)


def test_profiler_snapshot_discards_unbounded_dynamic_series() -> None:
    perf = PerfCollector(
        enabled=True,
        console_output=False,
        top_n=3,
        max_series=4,
    )

    with perf.frame():
        for index in range(100):
            perf.record_operation(f"dynamic-{index}", index + 1)
            perf.record_layer(f"layer-{index}", index + 1)

    snapshot = perf.snapshot()
    assert len(snapshot.operations) <= 3
    assert len(snapshot.layers) <= 3
    assert all(item.name != "<other>" for item in snapshot.operations)
    assert all(item.name != "<other>" for item in snapshot.layers)


def test_realize_cache_eviction_is_forwarded_to_profiler() -> None:
    perf = PerfCollector(enabled=True, console_output=False)
    defaults = LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01)
    first = G.polygon(n_sides=5)
    second = G.polygon(n_sides=6)

    with RealizeSession(
        runtime_limits=RuntimeLimits(cpu_cache_bytes=100),
        profiler=perf,
    ) as session:
        with perf.frame():
            realize_scene(
                lambda _t: first,
                0.0,
                defaults,
                config=_TEST_RUNTIME_CONFIG,
                session=session,
            )
        with perf.frame():
            realize_scene(
                lambda _t: second,
                1.0,
                defaults,
                config=_TEST_RUNTIME_CONFIG,
                session=session,
            )

    assert perf.snapshot().cache_evictions >= 1


class _LaggedMpDraw:
    def __init__(self, result: DrawResult) -> None:
        self._result = result
        self._published = False
        self.last_submitted_frame_id = 0

    def submit(self, **_kwargs: object) -> None:
        self.last_submitted_frame_id += 1
        return

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
        return


def test_scene_runner_records_worker_submit_to_result_lag() -> None:
    perf = PerfCollector(enabled=True, console_output=False)
    geometry = G.polygon(n_sides=5)
    result = DrawResult(
        frame_id=1,
        t=0.0,
        epoch=0,
        generation=0,
        snapshot_revision=0,
        layers=(Layer(geometry, site_id="ink", name="Ink"),),
        records=(),
        labels=(),
        effect_chains=(),
        worker_lag_ms=24.5,
    )
    factory = MpDrawFactoryFixture(_LaggedMpDraw(result))
    runner = SceneRunner(
        lambda _t: geometry,
        perf=perf,
        n_worker=1,
        effective_config=runtime_config(),
        mp_draw_factory=factory,
    )
    assert factory.calls[0].event_callback is not None
    try:
        with perf.frame():
            runner.run(
                0.0,
                store=ParamStore(),
                cc_snapshot=None,
                defaults=LayerStyleDefaults(
                    color=(0.0, 0.0, 0.0),
                    thickness=0.01,
                ),
                recording=False,
                transport_epoch=0,
                quality="draft",
            )
    finally:
        runner.close()

    snapshot = perf.snapshot()
    assert snapshot.worker_lag_samples == 1
    assert snapshot.worker_lag_ms == pytest.approx(24.5)


def test_structured_json_trace_works_without_gui(tmp_path) -> None:
    trace_path = tmp_path / "performance.jsonl"
    perf = PerfCollector(
        enabled=True,
        console_output=False,
        print_every=1,
        trace_path=trace_path,
    )

    with perf.frame():
        with perf.section("scene"):
            pass
        perf.record_operation("relax", 2_000_000)
        perf.record_layer("Ink", 3_000_000)
        perf.record_cache(hits=3, misses=1, evictions=2)
        perf.record_worker_lag(12.5)
        perf.record_preview_result(
            requested_revision=8,
            presented_revision=6,
            fresh=False,
        )
        perf.record_event(
            "parameter_revision_created",
            frame_id=4,
            revision=8,
        )
    perf.close()

    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    header, payload, footer = records
    assert header["record_type"] == "header"
    assert header["config"]["sample_limit"] == 256
    assert payload["schema"] == "grafix.performance.trace.v2"
    assert payload["frame_index"] == 1
    assert payload["frame_timing"]["p95_ms"] >= 0.0
    event = payload["events"][0]
    assert event["name"] == "parameter_revision_created"
    assert event["frame_id"] == 4
    assert event["revision"] == 8
    assert isinstance(event["timestamp_ns"], int)
    assert payload["operations"][0]["name"] == "relax"
    assert payload["layers"][0]["name"] == "Ink"
    assert payload["cache"] == {
        "evictions": 2,
        "hit_rate": 0.75,
        "hits": 3,
        "misses": 1,
    }
    assert payload["worker"]["average_lag_ms"] == pytest.approx(12.5)
    assert payload["preview"] == {
        "average_revision_lag": 2.0,
        "fresh_result_ratio": 0.0,
        "fresh_results": 0,
        "max_consecutive_stale_frames": 1,
        "max_revision_lag": 2,
        "revision_lag_samples": 1,
        "samples": 1,
    }
    assert footer["record_type"] == "footer"
    assert footer["frame_index"] == 1
    assert footer["records"] == 1
    assert footer["dropped_records"] == 0


def test_trace_close_flushes_partial_window(tmp_path) -> None:
    trace_path = tmp_path / "partial.jsonl"
    perf = PerfCollector(
        enabled=True,
        console_output=False,
        print_every=60,
        trace_path=trace_path,
    )

    with perf.frame():
        perf.record_event("draw_submitted", frame_id=7, revision=3)
    perf.close()

    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert [record.get("record_type", "window") for record in records] == [
        "header",
        "window",
        "footer",
    ]
    assert records[1]["frame_count"] == 1
    assert records[1]["events"][0]["frame_id"] == 7
    assert records[2]["records"] == 1


def test_trace_close_stops_writer_after_pending_frame_finalize_failure(tmp_path) -> None:
    trace_path = tmp_path / "failed-finalize.jsonl"
    root_error = RuntimeError("snapshot callback failed")

    def fail_snapshot_callback(_snapshot: object) -> None:
        raise root_error

    perf = PerfCollector(
        enabled=True,
        console_output=False,
        trace_path=trace_path,
        snapshot_callback=fail_snapshot_callback,
        defer_frame_finalize=True,
    )
    writer = perf._trace_writer
    assert writer is not None
    with perf.frame():
        pass

    with pytest.raises(RuntimeError) as exc_info:
        perf.close()

    assert exc_info.value is root_error
    assert perf._trace_writer is None
    assert not writer._thread.is_alive()
    perf.close()


def test_deferred_frame_boundary_keeps_present_and_full_loop_in_same_record(
    tmp_path,
) -> None:
    trace_path = tmp_path / "present-boundary.jsonl"
    perf = PerfCollector(
        enabled=True,
        console_output=False,
        print_every=1,
        trace_path=trace_path,
        defer_frame_finalize=True,
    )

    with perf.frame():
        perf.record_event(
            "parameter_revision_created",
            revision=3,
            timestamp_ns=1_000_000,
        )
    perf.record_duration("preview_draw_flip", 4_000_000)
    perf.record_event(
        "preview_presented",
        frame_id=9,
        revision=3,
        timestamp_ns=8_000_000,
    )
    perf.record_duration("full_loop", 9_000_000)
    perf.finish_frame(deadline_elapsed_ns=9_000_000)
    perf.close()

    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert len(records) == 3
    window = records[1]
    assert window["frame_count"] == 1
    assert {event["name"] for event in window["events"]} == {
        "parameter_revision_created",
        "preview_presented",
    }
    assert {timing["name"] for timing in window["duration_timing"]} == {
        "preview_core",
        "preview_draw_flip",
        "full_loop",
    }
    assert window["input_to_present"]["samples"] == 1


def test_profiler_counts_causal_events_dropped_from_bounded_window() -> None:
    perf = PerfCollector(enabled=True, console_output=False)

    with perf.frame():
        for frame_id in range(4_100):
            perf.record_event("event", frame_id=frame_id)

    snapshot = perf.snapshot()
    assert snapshot.trace_dropped_events == 4


def test_profiler_counts_pending_input_and_latency_sample_eviction() -> None:
    perf = PerfCollector(
        enabled=True,
        console_output=False,
        print_every=10_000,
    )

    with perf.frame():
        for revision in range(4_097):
            perf.record_event(
                "parameter_revision_created",
                revision=revision,
                timestamp_ns=revision,
            )
        perf.record_event(
            "preview_presented",
            revision=4_096,
            timestamp_ns=5_000,
        )

    snapshot = perf.snapshot()
    assert snapshot.trace_dropped_causal_inputs == 1
    assert snapshot.trace_dropped_latency_samples == 3_840
    assert snapshot.input_to_present_samples == 256


def test_profiler_matches_only_the_ordered_causal_prefix() -> None:
    perf = PerfCollector(
        enabled=True,
        console_output=False,
        print_every=10_000,
    )

    with perf.frame():
        for revision in range(100, 105):
            perf.record_event(
                "parameter_revision_created",
                revision=revision,
                timestamp_ns=revision * 1_000,
            )
        perf.record_event(
            "preview_presented",
            revision=101,
            timestamp_ns=200_000,
        )

    snapshot = perf.snapshot()
    assert snapshot.input_to_present_samples == 2
    assert snapshot.input_to_present_p50_ms == pytest.approx(0.0995)

    with perf.frame():
        perf.record_event(
            "preview_presented",
            revision=104,
            timestamp_ns=300_000,
        )

    snapshot = perf.snapshot()
    assert snapshot.input_to_present_samples == 5
    assert snapshot.input_to_present_max_ms == pytest.approx(0.198)


def test_profiler_rejects_out_of_order_causal_input_revisions() -> None:
    perf = PerfCollector(enabled=True, console_output=False)

    perf.record_event(
        "parameter_revision_created",
        revision=2,
        timestamp_ns=1_000,
    )

    with pytest.raises(
        ValueError,
        match="causal input revision は単調非減少",
    ):
        perf.record_event(
            "parameter_revision_created",
            revision=1,
            timestamp_ns=2_000,
        )


def test_deferred_frame_tail_and_deadline_use_full_loop_duration() -> None:
    perf = PerfCollector(
        enabled=True,
        console_output=False,
        frame_deadline_ms=10.0,
        defer_frame_finalize=True,
    )

    with perf.frame():
        pass
    perf.record_duration("full_loop", 20_000_000)
    perf.finish_frame(deadline_elapsed_ns=20_000_000)

    snapshot = perf.snapshot()
    assert snapshot.frame_p50_ms == pytest.approx(20.0)
    assert snapshot.frame_deadline_misses == 1
    duration_by_name = {item.name: item for item in snapshot.duration_timing}
    assert duration_by_name["preview_core"].p50_ms < 20.0


def test_profiler_tracks_preview_freshness_revision_lag_and_stale_streaks() -> None:
    perf = PerfCollector(enabled=True, console_output=False)

    with perf.frame():
        perf.record_preview_result(
            requested_revision=10,
            presented_revision=8,
            fresh=False,
        )
        perf.record_preview_result(
            requested_revision=11,
            presented_revision=9,
            fresh=False,
        )
        perf.record_preview_result(
            requested_revision=12,
            presented_revision=12,
            fresh=True,
        )

    snapshot = perf.snapshot()
    assert snapshot.preview_samples == 3
    assert snapshot.preview_fresh_results == 1
    assert snapshot.preview_fresh_result_ratio == pytest.approx(1.0 / 3.0)
    assert snapshot.preview_max_consecutive_stale_frames == 2
    assert snapshot.preview_revision_lag_samples == 3
    assert snapshot.preview_revision_lag == pytest.approx(4.0 / 3.0)
    assert snapshot.preview_revision_lag_max == 2


def test_profiler_stale_streak_resets_with_the_aggregation_window() -> None:
    perf = PerfCollector(
        enabled=True,
        console_output=False,
        print_every=2,
    )

    for revision in (1, 2):
        with perf.frame():
            perf.record_preview_result(
                requested_revision=revision,
                presented_revision=0,
                fresh=False,
            )

    assert perf.snapshot().preview_max_consecutive_stale_frames == 2

    with perf.frame():
        perf.record_preview_result(
            requested_revision=3,
            presented_revision=0,
            fresh=False,
        )

    snapshot = perf.snapshot()
    assert snapshot.frame_count == 1
    assert snapshot.preview_max_consecutive_stale_frames == 1


def test_trace_path_enables_collection_from_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    trace_path = tmp_path / "headless.jsonl"
    monkeypatch.delenv("GRAFIX_PERF", raising=False)
    monkeypatch.setenv("GRAFIX_PERF_TRACE", str(trace_path))
    monkeypatch.setenv("GRAFIX_PERF_EVERY", "1")

    perf = PerfCollector.from_env()
    with perf.frame():
        perf.record_operation("circle", 1_000)
    perf.close()

    assert perf.enabled is True
    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert records[1]["operations"][0]["name"] == "circle"


def test_profiler_keeps_frame_tail_and_deadline_miss_distribution() -> None:
    perf = PerfCollector(
        enabled=True,
        console_output=False,
        frame_deadline_ms=0.1,
    )

    for _ in range(3):
        with perf.frame():
            time.sleep(0.001)

    snapshot = perf.snapshot()
    assert snapshot.frame_p50_ms > 0.1
    assert snapshot.frame_p95_ms >= snapshot.frame_p50_ms
    assert snapshot.frame_p99_ms >= snapshot.frame_p95_ms
    assert snapshot.frame_max_ms >= snapshot.frame_p99_ms
    assert snapshot.frame_deadline_misses == 3
    assert snapshot.frame_max_consecutive_deadline_misses == 3


def test_profiler_reports_bounded_tail_sample_count_separately() -> None:
    perf = PerfCollector(
        enabled=True,
        console_output=False,
        print_every=300,
    )

    for _ in range(300):
        with perf.frame():
            pass

    snapshot = perf.snapshot()
    assert snapshot.frame_count == 300
    assert snapshot.frame_tail_samples == 256


def test_profiler_tracks_window_tails_and_input_to_present() -> None:
    perf = PerfCollector(enabled=True, console_output=False)

    with perf.frame():
        perf.record_event(
            "parameter_revision_created",
            revision=10,
            timestamp_ns=1_000_000,
        )
        perf.record_event(
            "parameter_revision_created",
            revision=11,
            timestamp_ns=2_000_000,
        )
        perf.record_duration("preview_draw_flip", 4_000_000)
        perf.record_duration("preview_draw_flip", 8_000_000)
        perf.record_duration("parameter_gui_draw_flip", 3_000_000)
        perf.record_duration("full_loop", 12_000_000)
        perf.record_event(
            "preview_presented",
            frame_id=7,
            revision=11,
            timestamp_ns=12_000_000,
        )

    snapshot = perf.snapshot()
    duration_by_name = {item.name: item for item in snapshot.duration_timing}
    assert duration_by_name["preview_draw_flip"].count == 2
    assert duration_by_name["preview_draw_flip"].p50_ms == pytest.approx(6.0)
    assert duration_by_name["preview_draw_flip"].max_ms == pytest.approx(8.0)
    assert duration_by_name["parameter_gui_draw_flip"].p95_ms == pytest.approx(3.0)
    assert duration_by_name["full_loop"].p99_ms == pytest.approx(12.0)
    assert snapshot.input_to_present_samples == 2
    assert snapshot.input_to_present_p50_ms == pytest.approx(10.5)
    assert snapshot.input_to_present_p95_ms == pytest.approx(10.95)
    assert snapshot.input_to_present_max_ms == pytest.approx(11.0)


def test_profiler_matches_style_input_to_main_process_style_present() -> None:
    perf = PerfCollector(enabled=True, console_output=False)

    with perf.frame():
        perf.record_event(
            "parameter_style_revision_created",
            revision=12,
            timestamp_ns=1_000_000,
        )
        # geometry はまだ古くても、style overlay は同じ present で反映済み。
        perf.record_event(
            "preview_presented",
            revision=11,
            timestamp_ns=8_000_000,
        )
        perf.record_event(
            "preview_style_presented",
            revision=12,
            timestamp_ns=3_000_000,
        )

    snapshot = perf.snapshot()
    assert snapshot.input_to_present_samples == 1
    assert snapshot.input_to_present_p50_ms == pytest.approx(2.0)
