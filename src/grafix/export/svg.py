"""
どこで: `src/grafix/export/svg.py`。
何を: realize 済みシーンを SVG として保存する関数を提供する。
なぜ: interactive 依存なしの最小 headless export（SVG）を用意し、反復可能にするため。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

import numpy as np

from grafix.core.parameters.style import rgb01_to_rgb255
from grafix.core.pipeline import RealizedLayer

_SVG_NS = "http://www.w3.org/2000/svg"
_FLOAT_DECIMALS = 3


def _fmt(value: float, *, decimals: int = _FLOAT_DECIMALS) -> str:
    """SVG 出力向けに float を決定的な文字列へ変換して返す。"""
    text = f"{float(value):.{int(decimals)}f}"
    if text.startswith("-0") and float(text) == 0.0:
        return text[1:]
    return text


def _rgb01_to_hex(rgb01: tuple[float, float, float]) -> str:
    """0..1 float RGB を #RRGGBB に変換して返す。"""
    r, g, b = rgb01_to_rgb255(rgb01)
    return f"#{r:02X}{g:02X}{b:02X}"


def _iter_polylines(*, coords: np.ndarray, offsets: np.ndarray) -> Iterator[np.ndarray]:
    """RealizedGeometry の coords/offsets から polyline（shape (N,2)）を列挙する。"""
    for start, end in zip(offsets[:-1], offsets[1:]):
        start_i = int(start)
        end_i = int(end)
        if end_i - start_i < 2:
            continue
        yield coords[start_i:end_i, :2]


def _polyline_to_d(polyline_xy: np.ndarray) -> str:
    """polyline（shape (N,2)）を SVG path の d 属性へ変換して返す。"""
    x0 = _fmt(polyline_xy[0, 0])
    y0 = _fmt(polyline_xy[0, 1])
    parts = [f"M {x0} {y0}"]
    for xy in polyline_xy[1:]:
        parts.append(f"L {_fmt(xy[0])} {_fmt(xy[1])}")
    return " ".join(parts)


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
        キャンバス寸法。現在は None を許容しない（将来 bbox 対応を追加する想定）。

    Returns
    -------
    Path
        保存先パス（正規化済み）。

    Raises
    ------
    ValueError
        canvas_size が None の場合。
    """
    _path = Path(path)
    if canvas_size is None:
        raise ValueError("canvas_size=None は未対応（現在は必須）")

    canvas_w, canvas_h = canvas_size
    if canvas_w <= 0 or canvas_h <= 0:
        raise ValueError("canvas_size は正の値である必要がある")

    # interactive の shader は clip 空間で線幅を扱うため、SVG では viewBox 単位へスケールする。
    stroke_scale = float(min(canvas_w, canvas_h)) / 2.0

    lines: list[str] = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append(
        (
            f'<svg xmlns="{_SVG_NS}" viewBox="0 0 {int(canvas_w)} {int(canvas_h)}" '
            f'width="{int(canvas_w)}" height="{int(canvas_h)}">'
        )
    )

    for layer in layers:
        stroke = _rgb01_to_hex(layer.color)
        stroke_width = _fmt(float(layer.thickness) * stroke_scale)
        coords = np.asarray(layer.realized.coords, dtype=np.float32)
        offsets = np.asarray(layer.realized.offsets, dtype=np.int32)

        for polyline_xy in _iter_polylines(coords=coords, offsets=offsets):
            d = _polyline_to_d(polyline_xy)
            lines.append(
                (
                    f'  <path d="{d}" fill="none" stroke="{stroke}" '
                    f'stroke-width="{stroke_width}" stroke-linecap="round" '
                    f'stroke-linejoin="round" />'
                )
            )

    lines.append("</svg>")

    _path.parent.mkdir(parents=True, exist_ok=True)
    with _path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    return _path
