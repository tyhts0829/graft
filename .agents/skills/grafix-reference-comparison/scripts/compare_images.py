#!/usr/bin/env python3
"""参照画像と現在のGrafix PNGを同一座標へ揃えて差分画像を生成する。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--alignment",
        choices=("fit", "exact"),
        default="fit",
        help="fitは参照を現行キャンバスへ等比配置、exactは同寸を要求する",
    )
    parser.add_argument(
        "--reference-crop",
        type=int,
        nargs=4,
        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
    )
    parser.add_argument(
        "--ink-threshold",
        type=float,
        default=205.0,
        help="このグレースケール値より暗い成分をインクとして強調する",
    )
    parser.add_argument(
        "--mask-threshold",
        type=int,
        default=35,
        help="IoU算出用インクマスクの0..255閾値",
    )
    return parser.parse_args()


def _fit_reference(reference: Image.Image, size: tuple[int, int]) -> Image.Image:
    width, height = size
    scale = min(width / reference.width, height / reference.height)
    fitted_size = (round(reference.width * scale), round(reference.height * scale))
    fitted = reference.resize(fitted_size, Image.Resampling.LANCZOS)
    background = Image.new("RGB", size, _paper_color(reference))
    background.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
    return background


def _paper_color(image: Image.Image) -> tuple[int, int, int]:
    array = np.asarray(image.convert("RGB"), dtype=np.uint8)
    pixels = array.reshape(-1, 3)
    lightness = pixels.mean(axis=1)
    paper = pixels[lightness >= np.percentile(lightness, 55.0)]
    return tuple(int(value) for value in np.median(paper, axis=0))


def _ink_strength(image: Image.Image, threshold: float) -> np.ndarray:
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    strength = np.clip((threshold - gray) / max(threshold - 60.0, 1.0), 0.0, 1.0)
    strength[strength < 0.10] = 0.0
    return np.rint(strength * 255.0).astype(np.uint8)


def _font(size: int, *, medium: bool = False) -> ImageFont.ImageFont:
    root = Path(__file__).resolve().parents[4]
    name = "NotoSansJP-Medium.ttf" if medium else "NotoSansJP-Regular.ttf"
    candidate = root / "src/grafix/resource/font/Noto_Sans_JP/static" / name
    if candidate.is_file():
        return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def _color_overlay(reference: np.ndarray, current: np.ndarray) -> Image.Image:
    height, width = reference.shape
    canvas = np.full((height, width, 3), 247.0, dtype=np.float32)
    canvas[:, :, 0] -= reference * 0.78
    canvas[:, :, 1] -= current * 0.72
    canvas[:, :, 2] -= reference * 0.08 + current * 0.08
    overlap = np.minimum(reference, current).astype(np.float32)
    canvas -= overlap[:, :, None] * 0.22
    image = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), "RGB")

    draw = ImageDraw.Draw(image)
    width_box = min(width - 24, 638)
    draw.rounded_rectangle(
        (12, 12, 12 + width_box, 86), radius=8, fill=(250, 249, 247), outline=(60, 60, 60), width=2
    )
    label_font = _font(18, medium=True)
    note_font = _font(14)
    items = (
        ((0, 190, 210), "REFERENCE / 参照"),
        ((230, 0, 150), "CURRENT / 現行"),
        ((35, 25, 100), "OVERLAP / 一致"),
    )
    x = 28
    for color, label in items:
        draw.rectangle((x, 28, x + 22, 50), fill=color)
        draw.text((x + 30, 24), label, font=label_font, fill=(25, 25, 25))
        x += 205
    draw.text((28, 58), "色が単独で見える箇所ほど差が大きい", font=note_font, fill=(65, 65, 65))
    return image


def _heatmap(reference: np.ndarray, current: np.ndarray) -> Image.Image:
    difference = np.abs(reference.astype(np.int16) - current.astype(np.int16)).astype(np.uint8)
    heat = np.zeros((*difference.shape, 3), dtype=np.uint8)
    heat[:, :, 0] = np.clip(difference.astype(np.int16) * 2, 0, 255)
    heat[:, :, 1] = np.clip((difference.astype(np.int16) - 40) * 2, 0, 220)
    heat[:, :, 2] = 30
    heat[difference < 12] = 245
    return Image.fromarray(heat, "RGB")


def _side_by_side(reference: Image.Image, current: Image.Image) -> Image.Image:
    width, height = current.size
    side = Image.new("RGB", (width * 2, height), (255, 255, 255))
    side.paste(reference, (0, 0))
    side.paste(current, (width, 0))
    draw = ImageDraw.Draw(side)
    draw.line((width, 0, width, height), fill=(220, 30, 30), width=3)
    font = _font(16, medium=True)
    for x, label in ((12, "REFERENCE"), (width + 12, "CURRENT")):
        draw.rectangle((x, 12, x + 238, 61), fill=(250, 250, 250))
        draw.text((x + 12, 23), label, font=font, fill=(0, 0, 0))
    return side


def main() -> int:
    args = _parse_args()
    reference = Image.open(args.reference).convert("RGB")
    current = Image.open(args.current).convert("RGB")
    if args.reference_crop is not None:
        reference = reference.crop(tuple(args.reference_crop))

    if args.alignment == "exact":
        if reference.size != current.size:
            raise ValueError(
                f"exact alignment requires equal image sizes: {reference.size} != {current.size}"
            )
        aligned_reference = reference
    else:
        aligned_reference = _fit_reference(reference, current.size)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    reference_ink = _ink_strength(aligned_reference, args.ink_threshold)
    current_ink = _ink_strength(current, args.ink_threshold)
    reference_mask = reference_ink > args.mask_threshold
    current_mask = current_ink > args.mask_threshold
    intersection = int(np.logical_and(reference_mask, current_mask).sum())
    union = int(np.logical_or(reference_mask, current_mask).sum())
    reference_pixels = int(reference_mask.sum())
    current_pixels = int(current_mask.sum())

    outputs = {
        "aligned_reference": args.out_dir / "reference_fitted.png",
        "color_overlay": args.out_dir / "color_overlay.png",
        "difference_heatmap": args.out_dir / "difference_heatmap.png",
        "side_by_side": args.out_dir / "side_by_side.png",
        "report": args.out_dir / "comparison_report.json",
    }
    aligned_reference.save(outputs["aligned_reference"])
    _color_overlay(reference_ink, current_ink).save(outputs["color_overlay"])
    _heatmap(reference_ink, current_ink).save(outputs["difference_heatmap"])
    _side_by_side(aligned_reference, current).save(outputs["side_by_side"])

    report = {
        "reference": str(args.reference.resolve()),
        "current": str(args.current.resolve()),
        "alignment": args.alignment,
        "canvas_size_px": list(current.size),
        "ink_iou": intersection / union if union else 1.0,
        "reference_ink_pixels": reference_pixels,
        "current_ink_pixels": current_pixels,
        "current_to_reference_ink_ratio": (
            current_pixels / reference_pixels if reference_pixels else None
        ),
        "outputs": {key: str(path.resolve()) for key, path in outputs.items() if key != "report"},
    }
    outputs["report"].write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
