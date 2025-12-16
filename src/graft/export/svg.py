"""
どこで: `src/graft/export/svg.py`。
何を: realize 済みシーンを SVG として保存する関数を提供する（当面はスタブ）。
なぜ: 先に API の骨格を確定し、後から実装を差し替えられるようにするため。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from graft.core.pipeline import RealizedLayer


def export_svg(
    layers: Sequence[RealizedLayer],
    path: str | Path,
    *,
    canvas_size: tuple[int, int] | None = None,
) -> Path:
    """Layer 列を SVG として保存する。

    Parameters
    ----------
    layers : Sequence[RealizedLayer]
        realize 済みの Layer 列。
    path : str or Path
        出力先パス。
    canvas_size : tuple[int, int] or None, optional
        キャンバス寸法。None の場合は layers 由来の情報に委譲する（将来実装）。

    Returns
    -------
    Path
        保存先パス（正規化済み）。
    """
    _path = Path(path)
    raise NotImplementedError("SVG export は未実装（API の骨格のみ）")

