#!/usr/bin/env python3
"""参照画像再現 Sketch を固定契約どおりに描画して PNG 保存する。"""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path

from grafix import Frame, RenderOptions, render, save

A5_PORTRAIT = (148, 210)
REQUIRED_LINE_THICKNESS = 0.001
SKETCH_MODULE_PREFIX = "sketch.agent_art."


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="sketch.agent_art の単一 Sketch を検証して PNG 保存する。"
    )
    parser.add_argument(
        "--module",
        required=True,
        help="描画対象 module（sketch.agent_art.<name>）",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="出力 PNG path",
    )
    parser.add_argument("--t", type=float, default=0.0, help="描画時刻（既定: 0.0）")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存の出力 generation を置換する",
    )
    return parser.parse_args(argv)


def _validate_module_contract(sketch: object) -> None:
    if sketch.CANVAS_SIZE != A5_PORTRAIT:  # type: ignore[attr-defined]
        raise ValueError(f"CANVAS_SIZE は {A5_PORTRAIT} に固定してください")
    if sketch.LINE_THICKNESS != REQUIRED_LINE_THICKNESS:  # type: ignore[attr-defined]
        raise ValueError(
            f"LINE_THICKNESS は {REQUIRED_LINE_THICKNESS} に固定してください"
        )


def _validate_frame(frame: Frame) -> None:
    if frame.canvas_size != A5_PORTRAIT:
        raise ValueError(f"render 後の canvas_size が {A5_PORTRAIT} ではありません")

    layers = frame.layers
    if not layers:
        raise ValueError("Sketch は1件以上の Layer を返す必要があります")

    if any(layer.layer.thickness != REQUIRED_LINE_THICKNESS for layer in layers):
        raise ValueError(
            "すべての L.layer(..., thickness=LINE_THICKNESS) で線幅を明示してください"
        )
    if any(layer.thickness != REQUIRED_LINE_THICKNESS for layer in layers):
        raise ValueError(
            f"すべての Layer の最終線幅は {REQUIRED_LINE_THICKNESS} である必要があります"
        )

    colors = {layer.color for layer in layers}
    if len(colors) != len(layers):
        raise ValueError("Layer 数は一意な線色数と一致する必要があります")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.module.startswith(SKETCH_MODULE_PREFIX):
        raise ValueError(
            f"module は {SKETCH_MODULE_PREFIX}<name> の形式で指定してください"
        )
    if args.out.suffix.lower() != ".png":
        raise ValueError("--out は .png path を指定してください")

    sketch = importlib.import_module(args.module)
    _validate_module_contract(sketch)

    frame = render(
        sketch.draw,
        args.t,
        options=RenderOptions(
            canvas_size=sketch.CANVAS_SIZE,
            background_color=sketch.BACKGROUND_COLOR,
            line_thickness=sketch.LINE_THICKNESS,
        ),
        parameter_source="code",
    )
    _validate_frame(frame)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    result = save(frame, args.out, overwrite=args.overwrite)
    if not result.path.is_file():
        raise RuntimeError(f"PNG が保存されませんでした: {result.path}")
    print(result.path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
