"""
どこで: `src/grafix/export/gcode.py`。
何を: realize 済みシーンを G-code として保存する関数を提供する（当面はスタブ）。
なぜ: ペンプロッタ向け出力を interactive 依存なしで追加できる余地を残すため。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from grafix.core.pipeline import RealizedLayer


def export_gcode(
    layers: Sequence[RealizedLayer],
    path: str | Path,
    *,
    feed_rate: float | None = None,
) -> Path:
    """Layer 列を G-code として保存する。"""
    _path = Path(path)
    _feed_rate = feed_rate
    raise NotImplementedError("G-code export は未実装（API の骨格のみ）")

