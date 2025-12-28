"""
どこで: `src/grafix/export/image.py`。
何を: SVG を外部ラスタライザ（resvg）で PNG に変換して保存する関数を提供する。
なぜ: SVG を正（ソース）として保存し、PNG は高解像度で再生成できる導線を用意するため。
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from grafix.core.parameters.persistence import default_param_store_path
from grafix.core.runtime_config import output_root_dir, runtime_config
from grafix.core.pipeline import RealizedLayer
from grafix.core.parameters.style import rgb01_to_rgb255
from grafix.export.svg import export_svg


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
    SVG を正として保存し、PNG は resvg でラスタライズして生成する。
    """
    _path = Path(path)
    suffix = _path.suffix.lower()

    if suffix == ".svg":
        if canvas_size is None:
            raise ValueError("canvas_size=None は未対応（現在は必須）")
        return export_svg(layers, _path, canvas_size=canvas_size)

    if suffix == ".png":
        if canvas_size is None:
            raise ValueError("canvas_size=None は未対応（現在は必須）")
        svg_path = _path.with_suffix(".svg")
        export_svg(layers, svg_path, canvas_size=canvas_size)
        return rasterize_svg_to_png(
            svg_path,
            _path,
            output_size=png_output_size(canvas_size),
            background_color_rgb01=background_color,
        )

    raise ValueError(f"未対応の画像フォーマット: {suffix!r}")


def default_png_output_path(draw: Callable[[float], object]) -> Path:
    """draw の定義元に基づく PNG の既定保存パスを返す。

    Notes
    -----
    パスは `{output_root}/png/{script_stem}.png`。
    `script_stem` は ParamStore 永続化と同一の算出規則。
    """

    script_stem = default_param_store_path(draw).stem
    return output_root_dir() / "png" / f"{script_stem}.png"


def png_output_size(canvas_size: tuple[int, int]) -> tuple[int, int]:
    """canvas_size を基準に PNG 出力ピクセルサイズを返す。"""

    canvas_w, canvas_h = canvas_size
    if int(canvas_w) <= 0 or int(canvas_h) <= 0:
        raise ValueError("canvas_size は正の (width, height) である必要がある")
    scale = float(runtime_config().png_scale)
    return int(int(canvas_w) * scale), int(int(canvas_h) * scale)


def _rgb01_to_hex(rgb01: tuple[float, float, float]) -> str:
    r, g, b = rgb01_to_rgb255(rgb01)
    return f"#{r:02X}{g:02X}{b:02X}"


def _resvg_command(
    *,
    input_svg: Path,
    output_png: Path,
    output_size: tuple[int, int],
    background_color_rgb01: tuple[float, float, float],
) -> list[str]:
    out_w, out_h = output_size
    if int(out_w) <= 0 or int(out_h) <= 0:
        raise ValueError("output_size は正の (width, height) である必要がある")
    return [
        "resvg",
        "--width",
        str(int(out_w)),
        "--height",
        str(int(out_h)),
        "--background",
        _rgb01_to_hex(background_color_rgb01),
        str(input_svg),
        str(output_png),
    ]


def rasterize_svg_to_png(
    svg_path: str | Path,
    png_path: str | Path,
    *,
    output_size: tuple[int, int],
    background_color_rgb01: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> Path:
    """SVG を PNG として保存する。

    Parameters
    ----------
    svg_path : str or Path
        入力 SVG パス。
    png_path : str or Path
        出力 PNG パス。
    output_size : tuple[int, int]
        出力 PNG の (width, height) ピクセルサイズ。
    background_color_rgb01 : tuple[float, float, float]
        背景色 RGB（0..1）。既定は白。

    Returns
    -------
    Path
        出力 PNG パス（正規化済み）。

    Raises
    ------
    RuntimeError
        resvg が見つからない、またはラスタライズに失敗した場合。
    """

    _svg_path = Path(svg_path)
    _png_path = Path(png_path)
    _png_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = _resvg_command(
        input_svg=_svg_path,
        output_png=_png_path,
        output_size=output_size,
        background_color_rgb01=background_color_rgb01,
    )
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as e:
        raise RuntimeError(
            "resvg が見つかりません（`resvg` をインストールして PATH を通してください）"
        ) from e

    if proc.returncode != 0:
        details = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"resvg が失敗しました (code={proc.returncode}). {details}".strip())

    return _png_path
