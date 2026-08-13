"""
Purpose:
    font textとasemic textが共有するem単位の改行・行揃え・box配置契約を定義する。
Use when:
    二つのtext primitiveでwrap、advance、alignment、またはbounding boxを揃える場合。
Constraints:
    - layoutはgeometry生成やfont resourceから独立したem座標で計算する。
    - 行幅、配置原点、boxのalignment基準をtextとasemicで一致させる。
    - wrap後の行順を保ち、新しい行の先頭へ空白を持ち越さない。
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np


def measure_line_width_em(
    line: str,
    *,
    char_advance_em: Callable[[str], float],
    letter_spacing_em: float,
) -> float:
    """文字ごとの送り幅と字間から 1 行の幅を em 単位で返す。"""
    if not line:
        return 0.0

    spacing = float(letter_spacing_em)
    width = 0.0
    for char in line:
        width += char_advance_em(char) + spacing
    return float(width - spacing)


def wrap_line_by_width_em(
    line: str,
    *,
    max_width_em: float,
    char_advance_em: Callable[[str], float],
    letter_spacing_em: float,
) -> list[str]:
    """1 行を指定幅で折り返し、折返し後の行頭空白を除く。"""
    if max_width_em <= 0.0:
        return [line]
    if not line:
        return [""]

    spacing = float(letter_spacing_em)
    width_limit = float(max_width_em)
    length = len(line)
    wrapped: list[str] = []
    index = 0
    segment_start = 0
    segment_width = 0.0
    segment_length = 0
    last_space: int | None = None

    while index < length:
        char = line[index]
        increment = char_advance_em(char) + (
            spacing if segment_length > 0 else 0.0
        )

        if segment_length > 0 and segment_width + increment > width_limit:
            if last_space is not None and last_space > segment_start:
                wrapped.append(line[segment_start:last_space])
                segment_start = last_space + 1
            else:
                wrapped.append(line[segment_start:index])
                segment_start = index

            while segment_start < length and line[segment_start] == " ":
                segment_start += 1
            index = segment_start
            segment_width = 0.0
            segment_length = 0
            last_space = None
            continue

        if char == " ":
            last_space = index
        segment_width += increment
        segment_length += 1
        index += 1

    if segment_start < length:
        wrapped.append(line[segment_start:])
    return wrapped


def aligned_line_origin_em(width_em: float, align: str) -> float:
    """alignment に対応する行の X 原点を em 単位で返す。"""
    if align == "left":
        return 0.0
    if align == "center":
        return -float(width_em) / 2.0
    if align == "right":
        return -float(width_em)
    raise ValueError(f"未対応の text alignment: {align!r}")


def bounding_box_polylines_em(
    *, width_em: float, height_em: float, align: str
) -> tuple[np.ndarray, ...]:
    """alignment を反映した矩形枠を 4 本の float32 ポリラインで返す。"""
    width = float(width_em)
    if align == "left":
        x0, x1 = 0.0, width
    elif align == "center":
        x0, x1 = -width / 2.0, width / 2.0
    elif align == "right":
        x0, x1 = -width, 0.0
    else:
        raise ValueError(f"未対応の text alignment: {align!r}")

    y0 = 0.0
    y1 = float(height_em)
    z0 = 0.0
    return (
        np.asarray([[x0, y0, z0], [x1, y0, z0]], dtype=np.float32),
        np.asarray([[x1, y0, z0], [x1, y1, z0]], dtype=np.float32),
        np.asarray([[x1, y1, z0], [x0, y1, z0]], dtype=np.float32),
        np.asarray([[x0, y1, z0], [x0, y0, z0]], dtype=np.float32),
    )


__all__ = [
    "aligned_line_origin_em",
    "bounding_box_polylines_em",
    "measure_line_width_em",
    "wrap_line_by_width_em",
]
