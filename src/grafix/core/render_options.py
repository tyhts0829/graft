"""
Purpose:
    headless と interactive が共有する logical canvas、背景、Layer の既定 style を immutable value に正規化する。
Use when:
    render session の canvas/style 契約、色入力の正規化、preview/export の共通既定値を変更・調査する場合。
Constraints:
    - ``canvas_size`` は logical 座標であり、G-code の machine placement/anchor policy をここに混ぜない。
    - line thickness は canvas 短辺に対する比率として扱う。
    - named color は固定した基本色集合で解決し、OS/外部 color database に依存させない。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from math import isfinite
from numbers import Integral, Real
from typing import TypeAlias, cast

from grafix.core.value_validation import finite_real, positive_integer_pair

RGB01: TypeAlias = tuple[float, float, float]
RGB8: TypeAlias = tuple[int, int, int]


# 依存ライブラリや OS の色データベースに結果を左右されない、CSS の基本色名。
# 名前は case-insensitive とし、space / hyphen は入力時に無視する。
_NAMED_COLOR_RGB8: dict[str, RGB8] = {
    "aqua": (0, 255, 255),
    "black": (0, 0, 0),
    "blue": (0, 0, 255),
    "brown": (165, 42, 42),
    "coral": (255, 127, 80),
    "cyan": (0, 255, 255),
    "darkgray": (169, 169, 169),
    "darkgrey": (169, 169, 169),
    "fuchsia": (255, 0, 255),
    "gold": (255, 215, 0),
    "gray": (128, 128, 128),
    "green": (0, 128, 0),
    "grey": (128, 128, 128),
    "indigo": (75, 0, 130),
    "lightgray": (211, 211, 211),
    "lightgrey": (211, 211, 211),
    "lime": (0, 255, 0),
    "magenta": (255, 0, 255),
    "maroon": (128, 0, 0),
    "navy": (0, 0, 128),
    "olive": (128, 128, 0),
    "orange": (255, 165, 0),
    "pink": (255, 192, 203),
    "purple": (128, 0, 128),
    "rebeccapurple": (102, 51, 153),
    "red": (255, 0, 0),
    "silver": (192, 192, 192),
    "teal": (0, 128, 128),
    "violet": (238, 130, 238),
    "white": (255, 255, 255),
    "yellow": (255, 255, 0),
}


def _rgb8_to_rgb01(rgb8: RGB8) -> RGB01:
    return tuple(float(channel) / 255.0 for channel in rgb8)  # type: ignore[return-value]


def _normalize_color(value: object) -> RGB01:
    if isinstance(value, Color):
        return value.rgb01

    if isinstance(value, str):
        text = value.strip()
        if text.startswith("#"):
            digits = text[1:]
            if len(digits) == 3:
                digits = "".join(channel * 2 for channel in digits)
            if len(digits) != 6:
                raise ValueError("hex color は #RGB または #RRGGBB で指定してください")
            try:
                rgb8 = tuple(int(digits[index : index + 2], 16) for index in (0, 2, 4))
            except ValueError as exc:
                raise ValueError(f"不正な hex color です: {value!r}") from exc
            return _rgb8_to_rgb01(rgb8)  # type: ignore[arg-type]

        name = "".join(character for character in text.casefold() if character not in " -_")
        try:
            return _rgb8_to_rgb01(_NAMED_COLOR_RGB8[name])
        except KeyError as exc:
            raise ValueError(f"未対応の named color です: {value!r}") from exc

    if isinstance(value, (bytes, bytearray)):
        raise TypeError("color は hex、named color、RGB8、RGB01 のいずれかで指定してください")
    try:
        channels: tuple[object, ...] = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(
            "color は hex、named color、RGB8、RGB01 のいずれかで指定してください"
        ) from exc
    if len(channels) != 3:
        raise ValueError("RGB color は 3 要素である必要があります")
    if any(isinstance(channel, bool) for channel in channels):
        raise TypeError("RGB channel に bool は使用できません")

    if all(isinstance(channel, Integral) for channel in channels):
        rgb8 = tuple(int(cast(Integral, channel)) for channel in channels)
        if any(channel < 0 or channel > 255 for channel in rgb8):
            raise ValueError("RGB8 channel は 0..255 の範囲で指定してください")
        return _rgb8_to_rgb01(rgb8)  # type: ignore[arg-type]

    if not all(isinstance(channel, Real) for channel in channels):
        raise TypeError("RGB channel はすべて int または float で指定してください")
    rgb01 = tuple(float(cast(Real, channel)) for channel in channels)
    if any(not isfinite(channel) or channel < 0.0 or channel > 1.0 for channel in rgb01):
        raise ValueError("RGB01 channel は有限な 0.0..1.0 の範囲で指定してください")
    return rgb01  # type: ignore[return-value]


@dataclass(frozen=True, slots=True, init=False)
class Color:
    """hex、named color、RGB8、RGB01 を RGB01 へ正規化した不変色。

    Parameters
    ----------
    value : Color, str or Sequence[int | float]
        ``"#09f"`` / ``"#0099ff"``、基本 named color、0..255 の整数 RGB、
        または 0.0..1.0 の float RGB。整数列は RGB8、float 列は RGB01 と解釈する。
    """

    rgb01: RGB01

    def __init__(self, value: ColorInput) -> None:
        object.__setattr__(self, "rgb01", _normalize_color(value))

    def __iter__(self) -> Iterator[float]:
        """RGB01 channel を順に返す。"""

        return iter(self.rgb01)

    def __len__(self) -> int:
        return 3

    def __getitem__(self, index: int) -> float:
        return self.rgb01[index]


ColorInput: TypeAlias = Color | str | Sequence[int | float]

_WHITE = Color("white")
_BLACK = Color("black")


@dataclass(frozen=True, slots=True, init=False)
class RenderOptions:
    """preview/export が共有する不変の描画設定。

    Parameters
    ----------
    canvas_size : tuple[int, int]
        論理キャンバスの ``(width, height)``。
    background_color : ColorInput
        背景色。``Color`` と同じ入口で受け、内部では RGB01 に正規化する。
    line_color : ColorInput
        Layer に色指定がない場合の線色。
    line_thickness : float
        Layer に太さ指定がない場合の線幅。値はキャンバス短辺に対する比率であり、
        既定 ``0.001`` は短辺の 0.1% に相当する。
    """

    canvas_size: tuple[int, int]
    background_color: Color
    line_color: Color
    line_thickness: float

    def __init__(
        self,
        *,
        canvas_size: tuple[int, int] = (800, 800),
        background_color: ColorInput = _WHITE,
        line_color: ColorInput = _BLACK,
        line_thickness: float = 0.001,
    ) -> None:
        size = positive_integer_pair(canvas_size, name="canvas_size")
        thickness = finite_real(line_thickness, name="line_thickness")
        if thickness <= 0.0:
            raise ValueError("line_thickness は正の有限値である必要があります")

        object.__setattr__(self, "canvas_size", size)
        object.__setattr__(self, "background_color", Color(background_color))
        object.__setattr__(self, "line_color", Color(line_color))
        object.__setattr__(self, "line_thickness", thickness)


__all__ = [
    "Color",
    "ColorInput",
    "RGB01",
    "RGB8",
    "RenderOptions",
]
