from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from grafix.interactive.parameter_gui.variation_thumbnail import (
    draw_variation_thumbnail_status,
)


def test_variation_thumbnail_missing_status(tmp_path: Path) -> None:
    messages: list[str] = []
    imgui = SimpleNamespace(text_disabled=messages.append)
    missing = tmp_path / "missing.png"
    draw_variation_thumbnail_status(imgui, missing)
    missing.write_bytes(b"png")
    draw_variation_thumbnail_status(imgui, missing)

    assert messages == [
        f"Thumbnail unavailable (missing): {missing}",
        "Thumbnail: missing.png",
    ]
