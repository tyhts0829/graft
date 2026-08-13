"""
Purpose:
    capture artifact、frame provenance、recording 統計を安定した JSON manifest として表す immutable domain value を定義する。
Use when:
    export manifest の schema、version、artifact family の検証契約を変更・調査する場合。
Constraints:
    - manifest の ``t`` は provenance が固定した frame 時刻と一致させる。
    - JSON payload の互換性を崩す変更では schema version を更新する。
    - path allocation、file I/O、publish/rollback policy を core value へ持ち込まない。
See:
    architecture.md §9
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from grafix.core.capture_provenance import CaptureProvenance
from grafix.core.value_validation import (
    exact_integer,
    exact_string,
    exact_string_choice,
    finite_real,
    positive_integer_pair,
)

CAPTURE_MANIFEST_SCHEMA_VERSION = 3


@dataclass(frozen=True, slots=True)
class RecordingManifest:
    """動画 recording の終端統計と明示 error policy。"""

    fps: float
    frame_count: int
    dropped_frame_count: int = 0
    duplicated_frame_count: int = 0
    error_count: int = 0
    error_policy: str = "pause"
    stop_reason: str | None = None
    abort_reason: str | None = None
    last_error: str | None = None

    def __post_init__(self) -> None:
        fps = finite_real(
            self.fps,
            name="recording fps",
            minimum=0.0,
            minimum_inclusive=False,
        )
        object.__setattr__(self, "fps", fps)
        for name in (
            "frame_count",
            "dropped_frame_count",
            "duplicated_frame_count",
            "error_count",
        ):
            value = exact_integer(getattr(self, name), name=name, minimum=0)
            object.__setattr__(self, name, value)
        policy = exact_string_choice(
            self.error_policy,
            name="recording error_policy",
            choices=("pause",),
        )
        object.__setattr__(self, "error_policy", policy)
        for name in ("stop_reason", "abort_reason", "last_error"):
            value = getattr(self, name)
            if value is not None:
                exact_string(value, name=name)

    def as_dict(self) -> dict[str, object]:
        return {
            "fps": self.fps,
            "frame_count": self.frame_count,
            "dropped_frame_count": self.dropped_frame_count,
            "duplicated_frame_count": self.duplicated_frame_count,
            "error_count": self.error_count,
            "error_policy": self.error_policy,
            "stop_reason": self.stop_reason,
            "abort_reason": self.abort_reason,
            "last_error": self.last_error,
        }


@dataclass(frozen=True, slots=True)
class CaptureManifest:
    """1 回の capture に対応する JSON 化可能な manifest v3。"""

    t: float
    canvas_size: tuple[int, int]
    format: str
    artifact_paths: tuple[Path, ...]
    provenance: CaptureProvenance
    output_size: tuple[int, int]
    recording: RecordingManifest | None = None

    def __post_init__(self) -> None:
        capture_t = finite_real(self.t, name="t")
        canvas_size = positive_integer_pair(self.canvas_size, name="canvas_size")

        artifact_format = exact_string(self.format, name="format")
        if not artifact_format:
            raise ValueError("format は空でない必要がある")
        if (
            artifact_format != artifact_format.strip()
            or artifact_format != artifact_format.casefold()
            or artifact_format.startswith(".")
        ):
            raise ValueError("format は小文字の拡張子名を '.' なしで指定してください")

        if not isinstance(self.artifact_paths, tuple):
            raise TypeError("artifact_paths は Path の tuple である必要があります")
        artifact_paths = self.artifact_paths
        if not artifact_paths:
            raise ValueError("artifact_paths は 1 件以上必要です")
        if any(not isinstance(path, Path) for path in artifact_paths):
            raise TypeError("artifact_paths は Path の tuple である必要があります")
        if any(not path.name for path in artifact_paths):
            raise ValueError("artifact_paths はファイル名を含む必要がある")

        output_size = positive_integer_pair(self.output_size, name="output_size")

        if not isinstance(self.provenance, CaptureProvenance):
            raise TypeError("provenance は CaptureProvenance である必要があります")
        if self.provenance.frame.t != capture_t:
            raise ValueError("provenance.frame.t は manifest.t と一致する必要があります")
        if self.recording is not None and not isinstance(
            self.recording,
            RecordingManifest,
        ):
            raise TypeError("recording は RecordingManifest または None である必要があります")

        object.__setattr__(self, "t", capture_t)
        object.__setattr__(self, "canvas_size", canvas_size)
        object.__setattr__(self, "format", artifact_format)
        object.__setattr__(self, "artifact_paths", artifact_paths)
        object.__setattr__(self, "output_size", output_size)

    def as_dict(self) -> dict[str, object]:
        """安定した JSON schema の dict を返す。"""

        width, height = self.canvas_size
        sections = self.provenance.manifest_sections()
        output: dict[str, object] = {
            "format": self.format,
            "artifact_paths": [str(path) for path in self.artifact_paths],
            "canvas_size": {"width": width, "height": height},
            "size": {"width": self.output_size[0], "height": self.output_size[1]},
        }
        payload = {
            "schema_version": CAPTURE_MANIFEST_SCHEMA_VERSION,
            **sections,
            "output": output,
            "recording": None if self.recording is None else self.recording.as_dict(),
        }
        return payload




__all__ = [
    "CAPTURE_MANIFEST_SCHEMA_VERSION",
    "CaptureManifest",
    "RecordingManifest",
]
