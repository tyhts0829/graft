"""variation thumbnail の GUI 表示だけを提供する。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def draw_variation_thumbnail_status(imgui: Any, path: Path) -> None:
    """texture backend がなくても thumbnail の有無を表示する。"""

    thumbnail_path = Path(path)
    if thumbnail_path.is_file():
        imgui.text_disabled(f"Thumbnail: {thumbnail_path.name}")
    else:
        imgui.text_disabled(f"Thumbnail unavailable (missing): {thumbnail_path}")


__all__ = ["draw_variation_thumbnail_status"]
