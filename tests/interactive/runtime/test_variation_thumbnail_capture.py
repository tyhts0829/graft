from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from grafix.core.gcode_params import GCodeParams
from grafix.export.capture import CaptureFrame
from grafix.interactive.parameter_gui.variation_panel import (
    VariationThumbnailArtifact,
)
from grafix.interactive.runtime.variation_thumbnail_capture import (
    make_variation_thumbnail_capture,
    variation_thumbnail_output_path,
    variation_thumbnail_size,
)


@dataclass(slots=True)
class _OwnedToken:
    path: Path
    discarded: bool = False

    def discard(self) -> None:
        self.discarded = True


class _OwnedExportSpy:
    def __init__(self, *, tokens: tuple[_OwnedToken, ...]) -> None:
        self.tokens = iter(tokens)
        self.calls: list[
            tuple[
                object,
                Path,
                bool,
                bool,
                tuple[int, int] | None,
                object,
            ]
        ] = []

    def __call__(
        self,
        frame: CaptureFrame,
        path: str | Path,
        *,
        overwrite: bool,
        split_gcode_layers: bool,
        output_size: tuple[int, int] | None,
        gcode_params: GCodeParams | None,
    ) -> VariationThumbnailArtifact:
        self.calls.append(
            (
                frame,
                Path(path),
                overwrite,
                split_gcode_layers,
                output_size,
                gcode_params,
            )
        )
        return next(self.tokens)


def test_thumbnail_path_and_size_share_export_filename_policy(tmp_path: Path) -> None:
    base = tmp_path / "sketch_800x400.png"

    assert variation_thumbnail_output_path(base, "  A/B candidate  ") == (
        tmp_path / "sketch_800x400_A_B_candidate.png"
    )
    assert variation_thumbnail_output_path(base, " /// ") == (
        tmp_path / "sketch_800x400_variation.png"
    )
    assert variation_thumbnail_size((800, 400)) == (320, 160)


def test_thumbnail_capture_reads_live_frame_and_returns_exact_owned_token(
    tmp_path: Path,
) -> None:
    first_frame = cast(CaptureFrame, object())
    second_frame = cast(CaptureFrame, object())
    frames = iter((first_frame, second_frame))
    first = _OwnedToken(tmp_path / "piece_candidate_001.png")
    second = _OwnedToken(tmp_path / "piece_candidate_002.png")
    export_owned = _OwnedExportSpy(tokens=(first, second))
    capture = make_variation_thumbnail_capture(
        export_owned,
        frame_provider=lambda: next(frames),
        base_path=tmp_path / "piece.png",
        canvas_size=(300, 200),
    )

    assert capture("candidate") is first
    assert capture("candidate") is second
    assert export_owned.calls == [
        (
            first_frame,
            tmp_path / "piece_candidate.png",
            False,
            False,
            (320, 213),
            None,
        ),
        (
            second_frame,
            tmp_path / "piece_candidate.png",
            False,
            False,
            (320, 213),
            None,
        ),
    ]


def test_thumbnail_capture_rejects_missing_live_frame(tmp_path: Path) -> None:
    export_owned = _OwnedExportSpy(
        tokens=(_OwnedToken(tmp_path / "unused.png"),),
    )
    capture = make_variation_thumbnail_capture(
        export_owned,
        frame_provider=lambda: None,
        base_path=tmp_path / "piece.png",
        canvas_size=(100, 100),
    )

    with pytest.raises(RuntimeError, match="No rendered frame"):
        capture("candidate")

    assert export_owned.calls == []
