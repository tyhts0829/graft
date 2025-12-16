"""
どこで: `src/graft/export/image.py`。
何を: realize 済みシーンを画像として保存する関数を提供する（当面はスタブ）。
なぜ: interactive を使わないヘッドレス出力の導線を先に固定するため。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from graft.core.pipeline import RealizedLayer


def export_image(
    layers: Sequence[RealizedLayer],
    path: str | Path,
    *,
    canvas_size: tuple[int, int] | None = None,
    background_color: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> Path:
    """Layer 列を画像として保存する。

    Notes
    -----
    画像フォーマット（png など）の選択は path 拡張子へ委譲する想定とする（将来実装）。
    """
    _path = Path(path)
    _background_color = background_color
    _canvas_size = canvas_size
    raise NotImplementedError("画像 export は未実装（API の骨格のみ）")

