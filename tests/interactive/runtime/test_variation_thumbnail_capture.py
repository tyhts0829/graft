from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from grafix.interactive.runtime.variation_thumbnail_capture import (
    make_variation_thumbnail_capture,
    variation_thumbnail_output_path,
    variation_thumbnail_size,
)


class _CaptureService:
    def __init__(self, *, published_path: Path) -> None:
        self.published_path = published_path
        self.calls: list[tuple[object, Path, bool, tuple[int, int] | None]] = []

    def export(
        self,
        frame: object,
        path: str | Path,
        *,
        overwrite: bool,
        output_size: tuple[int, int] | None,
    ) -> SimpleNamespace:
        self.calls.append((frame, Path(path), overwrite, output_size))
        return SimpleNamespace(path=self.published_path)


def test_thumbnail_path_and_size_share_export_filename_policy(tmp_path: Path) -> None:
    base = tmp_path / "sketch_800x400.png"

    assert variation_thumbnail_output_path(base, "  A/B candidate  ") == (
        tmp_path / "sketch_800x400_A_B_candidate.png"
    )
    assert variation_thumbnail_output_path(base, " /// ") == (
        tmp_path / "sketch_800x400_variation.png"
    )
    assert variation_thumbnail_size((800, 400)) == (320, 160)


def test_thumbnail_capture_reads_live_frame_and_returns_published_path(
    tmp_path: Path,
) -> None:
    first_frame = object()
    second_frame = object()
    frames = iter((first_frame, second_frame))
    published_path = tmp_path / "piece_candidate_001.png"
    service = _CaptureService(published_path=published_path)
    capture = make_variation_thumbnail_capture(
        cast(Any, service),
        frame_provider=lambda: cast(Any, next(frames)),
        base_path=tmp_path / "piece.png",
        canvas_size=(300, 200),
    )

    assert capture("candidate") == published_path
    assert capture("candidate") == published_path
    assert service.calls == [
        (first_frame, tmp_path / "piece_candidate.png", False, (320, 213)),
        (second_frame, tmp_path / "piece_candidate.png", False, (320, 213)),
    ]


def test_thumbnail_capture_rejects_missing_live_frame(tmp_path: Path) -> None:
    service = _CaptureService(published_path=tmp_path / "unused.png")
    capture = make_variation_thumbnail_capture(
        cast(Any, service),
        frame_provider=lambda: None,
        base_path=tmp_path / "piece.png",
        canvas_size=(100, 100),
    )

    with pytest.raises(RuntimeError, match="No rendered frame"):
        capture("candidate")

    assert service.calls == []
