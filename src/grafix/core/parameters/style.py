"""
どこで: `src/grafix/core/parameters/style.py`。
何を: 描画用 “Style” の ParamStore キーと変換ユーティリティを定義する。
なぜ: GUI と描画側で同じ識別子を共有し、Style 編集を素直に実装するため。
"""

from __future__ import annotations

from typing import Any, cast

from .key import ParameterKey

STYLE_OP = "__style__"
STYLE_SITE_ID = "__global__"

STYLE_BACKGROUND_COLOR = "background_color"
STYLE_GLOBAL_THICKNESS = "global_thickness"
STYLE_GLOBAL_LINE_COLOR = "global_line_color"


def style_key(arg: str) -> ParameterKey:
    """Style 用の ParameterKey を返す。"""

    return ParameterKey(op=STYLE_OP, site_id=STYLE_SITE_ID, arg=str(arg))


def coerce_rgb255(value: object) -> tuple[int, int, int]:
    """値を RGB255 タプル `(r, g, b)`（0..255）に正規化して返す。

    Parameters
    ----------
    value : object
        `(r, g, b)` の 3 要素シーケンス。

    Returns
    -------
    tuple[int, int, int]
        `int()` 化 + 0..255 clamp 済みの RGB。

    Raises
    ------
    ValueError
        長さ 3 のシーケンスでない場合。
    """

    r: object
    g: object
    b: object
    try:
        r, g, b = value  # type: ignore[misc]
    except Exception as exc:
        raise ValueError(f"rgb value must be a length-3 sequence: {value!r}") from exc

    def _clamp(v: object) -> int:
        iv = int(cast(Any, v))
        return 0 if iv < 0 else 255 if iv > 255 else iv

    return _clamp(r), _clamp(g), _clamp(b)


def rgb01_to_rgb255(rgb: tuple[float, float, float]) -> tuple[int, int, int]:
    """0..1 float の RGB を 0..255 int の RGB に変換して返す。"""

    r, g, b = rgb
    out: list[int] = []
    for v in (r, g, b):
        fv = float(v)
        fv = 0.0 if fv < 0.0 else 1.0 if fv > 1.0 else fv
        out.append(int(round(fv * 255.0)))
    return int(out[0]), int(out[1]), int(out[2])


def rgb255_to_rgb01(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """0..255 int の RGB を 0..1 float の RGB に変換して返す。"""

    r, g, b = rgb
    return float(r) / 255.0, float(g) / 255.0, float(b) / 255.0
