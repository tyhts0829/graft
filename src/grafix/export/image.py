"""
どこで: `src/grafix/export/image.py`。
何を: SVG を中間表現として外部ラスタライザ（resvg）で PNG に変換する関数を提供する。
なぜ: ベクター描画と同じ形状を、指定解像度の PNG として安全に保存するため。
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from grafix.file_io import atomic_output_path
from grafix.export.output_paths import output_path_for_draw
from grafix.core.parameters.style import rgb01_to_rgb255
from grafix.core.runtime_config import RuntimeConfig
from grafix.core.value_validation import finite_real, positive_integer_pair

_DEFAULT_RESVG_TIMEOUT_S = 30.0


def default_png_output_path(
    draw: Callable[[float], object],
    *,
    scale: float,
    canvas_size: tuple[int, int],
    config: RuntimeConfig,
    run_id: str | None = None,
) -> Path:
    """draw の定義元に基づく PNG の既定保存パスを返す。

    Notes
    -----
    パスは `output/{kind}/` 配下で sketch_dir のサブディレクトリ構造をミラーする。
    """

    out_w, out_h = png_output_size(canvas_size, scale=scale)
    return output_path_for_draw(
        kind="png",
        ext="png",
        draw=draw,
        run_id=run_id,
        canvas_size=(out_w, out_h),
        config=config,
    )


def png_output_size(
    canvas_size: tuple[int, int],
    *,
    scale: float,
) -> tuple[int, int]:
    """canvas_size と明示 scale から PNG 出力ピクセルサイズを返す。"""

    canvas_w, canvas_h = positive_integer_pair(canvas_size, name="canvas_size")
    effective_scale = finite_real(
        scale,
        name="scale",
        minimum=0.0,
        minimum_inclusive=False,
    )
    output_size = (
        int(canvas_w * effective_scale),
        int(canvas_h * effective_scale),
    )
    if output_size[0] <= 0 or output_size[1] <= 0:
        raise ValueError("scale 適用後の output_size は正である必要があります")
    return output_size


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
    out_w, out_h = positive_integer_pair(output_size, name="output_size")
    return [
        "resvg",
        "--width",
        str(out_w),
        "--height",
        str(out_h),
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
    timeout_s: float = _DEFAULT_RESVG_TIMEOUT_S,
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
    timeout_s : float
        resvg process の最大実行秒数。

    Returns
    -------
    Path
        出力 PNG パス（正規化済み）。

    Raises
    ------
    RuntimeError
        resvg が見つからない、またはラスタライズに失敗した場合。
    TimeoutError
        resvg が `timeout_s` 秒以内に終了しなかった場合。
    """

    _svg_path = Path(svg_path)
    _png_path = Path(png_path)
    timeout = finite_real(
        timeout_s,
        name="timeout_s",
        minimum=0.0,
        minimum_inclusive=False,
    )
    _png_path.parent.mkdir(parents=True, exist_ok=True)

    with atomic_output_path(_png_path) as temp_png_path:
        cmd = _resvg_command(
            input_svg=_svg_path,
            output_png=temp_png_path,
            output_size=output_size,
            background_color_rgb01=background_color_rgb01,
        )
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                "resvg が見つかりません（`resvg` をインストールして PATH を通してください）"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise TimeoutError(f"resvg が {timeout:g} 秒以内に終了しませんでした") from e

        if proc.returncode != 0:
            details = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(
                f"resvg が失敗しました (code={proc.returncode}). {details}".strip()
            )

    return _png_path
