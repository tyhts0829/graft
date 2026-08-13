"""
Purpose:
    interactive runtimeからUIへ渡すperformance/monitor telemetryのimmutable契約を定義する。
Use when:
    profiler表示、runtime monitor、または複数subsystemの観測snapshotを変更する場合。
Constraints:
    - UIからcollectorやresource ownerへ到達させず、時点固定のvalueだけを公開する。
    - 同一表示単位では一度取得したsnapshotを使い、scalar値を別時点から混在させない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from grafix.core.value_validation import (
    exact_bool,
    exact_integer,
    exact_string,
    exact_string_choice,
    finite_real,
)
from grafix.interactive.diagnostics import DiagnosticCenter, DiagnosticEvent


def _optional_string(value: object, *, name: str) -> str | None:
    if value is None:
        return None
    return exact_string(value, name=name)


@dataclass(frozen=True, slots=True)
class PerfTiming:
    """1 区間名の bounded 集計値。"""

    name: str
    total_ms: float
    mean_ms: float
    per_frame_ms: float
    calls: int
    calls_per_frame: float

    def as_dict(self) -> dict[str, object]:
        """structured trace 用の JSON 互換値を返す。"""

        return {
            "name": self.name,
            "total_ms": self.total_ms,
            "mean_ms": self.mean_ms,
            "per_frame_ms": self.per_frame_ms,
            "calls": self.calls,
            "calls_per_frame": self.calls_per_frame,
        }


@dataclass(frozen=True, slots=True)
class PerfEvent:
    """parameter revision と描画段階を結ぶ bounded causal event。"""

    name: str
    timestamp_ns: int
    frame_id: int | None = None
    revision: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "timestamp_ns": self.timestamp_ns,
            "frame_id": self.frame_id,
            "revision": self.revision,
        }


@dataclass(frozen=True, slots=True)
class PerfDurationDistribution:
    """draw+flip / full loop など frame 外区間の bounded tail。"""

    name: str
    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "count": self.count,
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "p99_ms": self.p99_ms,
            "max_ms": self.max_ms,
        }


@dataclass(frozen=True, slots=True)
class PerfSnapshot:
    """Inspector と trace 出力で共有する小さな immutable snapshot。"""

    frame_index: int = 0
    frame_count: int = 0
    frame_ms: float = 0.0
    frame_p50_ms: float = 0.0
    frame_p95_ms: float = 0.0
    frame_p99_ms: float = 0.0
    frame_max_ms: float = 0.0
    frame_tail_samples: int = 0
    frame_deadline_misses: int = 0
    frame_max_consecutive_deadline_misses: int = 0
    sections: tuple[PerfTiming, ...] = ()
    duration_timing: tuple[PerfDurationDistribution, ...] = ()
    operations: tuple[PerfTiming, ...] = ()
    layers: tuple[PerfTiming, ...] = ()
    events: tuple[PerfEvent, ...] = ()
    trace_dropped_records: int = 0
    trace_dropped_events: int = 0
    trace_dropped_causal_inputs: int = 0
    trace_dropped_latency_samples: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    cache_evictions: int = 0
    worker_lag_samples: int = 0
    worker_lag_ms: float | None = None
    worker_lag_max_ms: float | None = None
    preview_samples: int = 0
    preview_fresh_results: int = 0
    preview_max_consecutive_stale_frames: int = 0
    preview_revision_lag_samples: int = 0
    preview_revision_lag: float | None = None
    preview_revision_lag_max: int | None = None
    input_to_present_samples: int = 0
    input_to_present_p50_ms: float | None = None
    input_to_present_p95_ms: float | None = None
    input_to_present_p99_ms: float | None = None
    input_to_present_max_ms: float | None = None

    @property
    def cache_hit_rate(self) -> float:
        """hit/miss の観測総数に対する hit 比率を返す。"""

        total = self.cache_hits + self.cache_misses
        return 0.0 if total <= 0 else self.cache_hits / total

    @property
    def preview_fresh_result_ratio(self) -> float:
        """preview 観測 frame に対する fresh result の比率を返す。"""

        samples = self.preview_samples
        return 0.0 if samples <= 0 else self.preview_fresh_results / samples

    def as_dict(self) -> dict[str, object]:
        """JSON Lines trace の 1 record に変換する。"""

        return {
            "schema": "grafix.performance.trace.v2",
            "frame_index": self.frame_index,
            "frame_count": self.frame_count,
            "frame_ms": self.frame_ms,
            "frame_timing": {
                "p50_ms": self.frame_p50_ms,
                "p95_ms": self.frame_p95_ms,
                "p99_ms": self.frame_p99_ms,
                "max_ms": self.frame_max_ms,
                "sample_count": self.frame_tail_samples,
                "deadline_misses": self.frame_deadline_misses,
                "max_consecutive_deadline_misses": (
                    self.frame_max_consecutive_deadline_misses
                ),
            },
            "sections": [item.as_dict() for item in self.sections],
            "duration_timing": [item.as_dict() for item in self.duration_timing],
            "operations": [item.as_dict() for item in self.operations],
            "layers": [item.as_dict() for item in self.layers],
            "events": [item.as_dict() for item in self.events],
            "trace": {
                "dropped_records": self.trace_dropped_records,
                "dropped_events": self.trace_dropped_events,
                "dropped_causal_inputs": self.trace_dropped_causal_inputs,
                "dropped_latency_samples": self.trace_dropped_latency_samples,
            },
            "cache": {
                "hits": self.cache_hits,
                "misses": self.cache_misses,
                "evictions": self.cache_evictions,
                "hit_rate": self.cache_hit_rate,
            },
            "worker": {
                "samples": self.worker_lag_samples,
                "average_lag_ms": self.worker_lag_ms,
                "max_lag_ms": self.worker_lag_max_ms,
            },
            "preview": {
                "samples": self.preview_samples,
                "fresh_results": self.preview_fresh_results,
                "fresh_result_ratio": self.preview_fresh_result_ratio,
                "max_consecutive_stale_frames": (
                    self.preview_max_consecutive_stale_frames
                ),
                "revision_lag_samples": self.preview_revision_lag_samples,
                "average_revision_lag": self.preview_revision_lag,
                "max_revision_lag": self.preview_revision_lag_max,
            },
            "input_to_present": {
                "samples": self.input_to_present_samples,
                "p50_ms": self.input_to_present_p50_ms,
                "p95_ms": self.input_to_present_p95_ms,
                "p99_ms": self.input_to_present_p99_ms,
                "max_ms": self.input_to_present_max_ms,
            },
        }


@dataclass(frozen=True, slots=True)
class MonitorSnapshot:
    """Parameter GUI に表示する監視値のスナップショット。"""

    fps: float
    cpu_percent: float
    rss_mb: float
    vertices: int
    lines: int
    frame_error: str | None = None
    transport_t: float = 0.0
    transport_requested_t: float = 0.0
    transport_waiting: bool = False
    transport_speed: float = 1.0
    transport_recording: bool = False
    capture_request_count: int = 0
    capture_request_limit: int = 0
    capture_retained_bytes: int = 0
    capture_byte_limit: int = 0
    capture_notice: str | None = None
    diagnostics: tuple[DiagnosticEvent, ...] = ()
    autosave_status: str = "clean"
    autosave_error: str | None = None
    recovered_session: bool = False
    profiler: PerfSnapshot | None = None

    def __post_init__(self) -> None:
        for field_name in ("fps", "cpu_percent", "rss_mb"):
            object.__setattr__(
                self,
                field_name,
                finite_real(getattr(self, field_name), name=field_name, minimum=0.0),
            )
        for field_name in (
            "vertices",
            "lines",
            "capture_request_count",
            "capture_request_limit",
            "capture_retained_bytes",
            "capture_byte_limit",
        ):
            object.__setattr__(
                self,
                field_name,
                exact_integer(getattr(self, field_name), name=field_name, minimum=0),
            )
        for field_name in ("transport_t", "transport_requested_t"):
            object.__setattr__(
                self,
                field_name,
                finite_real(getattr(self, field_name), name=field_name),
            )
        object.__setattr__(
            self,
            "transport_speed",
            finite_real(
                self.transport_speed,
                name="transport_speed",
                minimum=0.0,
                minimum_inclusive=False,
            ),
        )
        for field_name in (
            "transport_waiting",
            "transport_recording",
            "recovered_session",
        ):
            exact_bool(getattr(self, field_name), name=field_name)
        for field_name in ("frame_error", "capture_notice", "autosave_error"):
            _optional_string(getattr(self, field_name), name=field_name)
        exact_string_choice(
            self.autosave_status,
            name="autosave_status",
            choices=("clean", "dirty", "saving", "failed"),
        )
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(event, DiagnosticEvent) for event in self.diagnostics
        ):
            raise TypeError("diagnostics は DiagnosticEvent の tuple である必要があります")
        if self.profiler is not None and not isinstance(self.profiler, PerfSnapshot):
            raise TypeError("profiler は PerfSnapshot または None である必要があります")


class TelemetrySource(Protocol):
    """GUI が必要とする read-only telemetry source。"""

    @property
    def diagnostic_center(self) -> DiagnosticCenter: ...

    def snapshot(self) -> MonitorSnapshot: ...


__all__ = [
    "MonitorSnapshot",
    "PerfDurationDistribution",
    "PerfEvent",
    "PerfSnapshot",
    "PerfTiming",
    "TelemetrySource",
]
