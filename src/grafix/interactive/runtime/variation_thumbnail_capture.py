"""variation thumbnail の capture policy を runtime service へ適合する。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from grafix.core.value_validation import positive_integer_pair
from grafix.export.capture import CaptureFrame, CaptureService
from grafix.export.variation_batch import portable_filename_component
from grafix.interactive.parameter_gui.variation_panel import (
    VariationThumbnailArtifact,
)

_THUMBNAIL_LONG_EDGE = 320


def variation_thumbnail_output_path(base_path: Path, name: str) -> Path:
    """portable な variation 名を付けた PNG 保存先を返す。"""

    if not isinstance(base_path, Path):
        raise TypeError("base_path は Path である必要があります")
    component = portable_filename_component(name)
    return base_path.with_name(
        f"{base_path.stem}_{component}{base_path.suffix}"
    )


def variation_thumbnail_size(canvas_size: tuple[int, int]) -> tuple[int, int]:
    """canvas 比率を保つ長辺320pxの thumbnail 寸法を返す。"""

    width, height = positive_integer_pair(canvas_size, name="canvas_size")
    scale = _THUMBNAIL_LONG_EDGE / float(max(width, height))
    return (
        max(1, int(round(width * scale))),
        max(1, int(round(height * scale))),
    )


def make_variation_thumbnail_capture(
    capture_service: CaptureService,
    *,
    frame_provider: Callable[[], CaptureFrame | None],
    base_path: Path,
    canvas_size: tuple[int, int],
) -> Callable[[str], VariationThumbnailArtifact]:
    """live frame を rollback ownership 付き no-clobber PNG として保存する。"""

    if not callable(frame_provider):
        raise TypeError("frame_provider は callable である必要があります")
    output_size = variation_thumbnail_size(canvas_size)

    def capture(name: str) -> VariationThumbnailArtifact:
        frame = frame_provider()
        if frame is None:
            raise RuntimeError("No rendered frame is available for a thumbnail.")
        return capture_service._export_owned(
            frame,
            variation_thumbnail_output_path(base_path, name),
            overwrite=False,
            split_gcode_layers=False,
            output_size=output_size,
            gcode_params=None,
        )

    return capture


__all__ = [
    "make_variation_thumbnail_capture",
    "variation_thumbnail_output_path",
    "variation_thumbnail_size",
]
