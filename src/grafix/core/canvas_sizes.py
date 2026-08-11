"""Grafix で頻用する論理キャンバス寸法を定義する。

``SQUARE`` は300 logical units四方の標準的な正方形キャンバスである。
ISO A系列は公称mm寸法を ``(width, height)`` の整数tupleで表し、短い名前を
縦向き、``_LANDSCAPE`` 付きの名前を横向きとする。
"""

from __future__ import annotations

from typing import Final

A2: Final[tuple[int, int]] = (420, 594)
A2_LANDSCAPE: Final[tuple[int, int]] = (A2[1], A2[0])
A3: Final[tuple[int, int]] = (297, 420)
A3_LANDSCAPE: Final[tuple[int, int]] = (A3[1], A3[0])
A4: Final[tuple[int, int]] = (210, 297)
A4_LANDSCAPE: Final[tuple[int, int]] = (A4[1], A4[0])
A5: Final[tuple[int, int]] = (148, 210)
A5_LANDSCAPE: Final[tuple[int, int]] = (A5[1], A5[0])
A6: Final[tuple[int, int]] = (105, 148)
A6_LANDSCAPE: Final[tuple[int, int]] = (A6[1], A6[0])
SQUARE: Final[tuple[int, int]] = (300, 300)

__all__ = [
    "A2",
    "A2_LANDSCAPE",
    "A3",
    "A3_LANDSCAPE",
    "A4",
    "A4_LANDSCAPE",
    "A5",
    "A5_LANDSCAPE",
    "A6",
    "A6_LANDSCAPE",
    "SQUARE",
]
