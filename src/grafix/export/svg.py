"""
Purpose:
    realized layerを論理canvas座標のportableなvector captureとして保存する。
Use when:
    SVGの座標、layer/style境界、後段PNG変換の中間表現を変更するとき。
Constraints:
    - geometryを物理用紙座標へ変換せず、canvas座標と寸法を維持する。
    - layer、polyline、pathの順序と境界を暗黙に組み替えない。
    - styleはcapture時に解決済みのlayer値を反映し、未解決parameterへ戻らない。
Side effects:
    完成したSVG textを対象pathへatomicに書き込む。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TextIO

import numpy as np

from grafix.file_io import atomic_text_writer
from grafix.core.parameters.style import line_width_for_short_side, rgb01_to_rgb255
from grafix.core.pipeline import RealizedLayer

_SVG_NS = "http://www.w3.org/2000/svg"
_FLOAT_DECIMALS = 3
_PATH_POINT_CHUNK_SIZE = 1024


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


def _write_polyline_path(
    stream: TextIO,
    polyline_xy: np.ndarray,
    *,
    stroke: str,
    stroke_width: str,
) -> None:
    """1 polyline を固定サイズ chunk で SVG path として書く。"""

    stream.write(f'  <path d="M {_fmt(polyline_xy[0, 0])} {_fmt(polyline_xy[0, 1])}')
    for start in range(1, int(polyline_xy.shape[0]), _PATH_POINT_CHUNK_SIZE):
        chunk = polyline_xy[start : start + _PATH_POINT_CHUNK_SIZE]
        stream.write(
            "".join(f" L {_fmt(xy[0])} {_fmt(xy[1])}" for xy in chunk)
        )
    stream.write(
        f'" fill="none" stroke="{stroke}" '
        f'stroke-width="{stroke_width}" stroke-linecap="round" '
        'stroke-linejoin="round" />\n'
    )


def export_svg(
    layers: Sequence[RealizedLayer],
    path: str | Path,
    *,
    canvas_size: tuple[int, int],
) -> Path:
    """Layer 列を SVG として保存する。

    Parameters
    ----------
    layers : Sequence[RealizedLayer]
        realize 済みの Layer 列。
    path : str or Path
        出力先パス。
    canvas_size : tuple[int, int]
        キャンバス寸法。

    Returns
    -------
    Path
        保存先パス（正規化済み）。

    """
    _path = Path(path)
    canvas_w, canvas_h = canvas_size
    if canvas_w <= 0 or canvas_h <= 0:
        raise ValueError("canvas_size は正の値である必要がある")

    with atomic_text_writer(_path, newline="\n") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(
            f'<svg xmlns="{_SVG_NS}" viewBox="0 0 {int(canvas_w)} {int(canvas_h)}" '
            f'width="{int(canvas_w)}" height="{int(canvas_h)}">\n'
        )

        for layer in layers:
            stroke = _rgb01_to_hex(layer.color)
            stroke_width = _fmt(
                line_width_for_short_side(
                    layer.thickness,
                    (float(canvas_w), float(canvas_h)),
                )
            )
            coords = np.asarray(layer.realized.coords, dtype=np.float32)
            offsets = np.asarray(layer.realized.offsets, dtype=np.int32)

            for polyline_xy in _iter_polylines(coords=coords, offsets=offsets):
                _write_polyline_path(
                    f,
                    polyline_xy,
                    stroke=stroke,
                    stroke_width=stroke_width,
                )
        f.write("</svg>\n")

    return _path
