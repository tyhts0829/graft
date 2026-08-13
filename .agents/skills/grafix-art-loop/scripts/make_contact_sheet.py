#!/usr/bin/env python3
"""
Purpose:
    Grafix Art Loopの成功候補を、選定用の一枚のcontact sheetへ集約するCLI。
Use when:
    候補render後にvNN順の画像を同時比較し、winner選定へ渡すとき。
Constraints:
    - 入力はrun_dir直下のcandidates/vNN/out.pngだけをvariant番号順に扱う。
    - 元画像を拡大せずaspect ratioを保ち、出力長辺だけを指定上限へ縮小する。
    - 出力先はdata/output/png/codex_generated配下に限定する。
    - 候補が一件もない場合は空のsheetを生成せず失敗する。
Side effects:
    候補PNGを読み、選定用PNGをcodex_generated directoryへ保存する。
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ModuleNotFoundError as exc:
    raise SystemExit("error: Pillow is required (`pip install pillow`).") from exc


DEFAULT_MAX_LONG_EDGE = 2048
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_DIR = REPOSITORY_ROOT / "data" / "output" / "png" / "codex_generated"
OUTER_PADDING = 40
GRID_GAP = 24
LABEL_HEIGHT = 44
BACKGROUND_COLOR = (246, 244, 239)
LABEL_COLOR = (32, 32, 32)
VARIANT_PATTERN = re.compile(r"^v(\d+)$")


@dataclass(frozen=True)
class TileSource:
    """Contact sheet に配置する一つの候補画像。"""

    number: int
    label: str
    path: Path


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a contact sheet from candidates/vNN/out.png files."
    )
    parser.add_argument("--run-dir", type=Path, required=True, help="Art Loop run directory.")
    parser.add_argument(
        "--out",
        type=Path,
        help=(
            "Output PNG path. Relative paths are resolved from "
            "data/output/png/codex_generated. "
            "Defaults to <run_id>_contact_sheet.png in that directory."
        ),
    )
    parser.add_argument(
        "--max-long-edge",
        type=_positive_int,
        default=DEFAULT_MAX_LONG_EDGE,
        help=(
            "Maximum output long edge in pixels; images are never enlarged "
            f"(default: {DEFAULT_MAX_LONG_EDGE})."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print ordered inputs and output path without generating an image.",
    )
    return parser.parse_args()


def collect_sources(run_dir: Path) -> list[TileSource]:
    """`candidates/vNN/out.png` を variant 番号順に収集する。"""

    candidates_dir = run_dir / "candidates"
    if not candidates_dir.is_dir():
        raise ValueError(f"candidates directory not found: {candidates_dir}")

    sources: list[TileSource] = []
    for path in candidates_dir.glob("v*/out.png"):
        match = VARIANT_PATTERN.fullmatch(path.parent.name)
        if match is None or not path.is_file():
            continue
        sources.append(TileSource(number=int(match.group(1)), label=path.parent.name, path=path))

    sources.sort(key=lambda source: (source.number, source.label))
    return sources


def choose_grid(n_tiles: int) -> tuple[int, int]:
    """空きセルを抑えながら 16:9 に近いグリッドを選ぶ。"""

    if n_tiles <= 0:
        raise ValueError("n_tiles must be positive")

    target_ratio = 16 / 9
    best_cols = 1
    best_rows = n_tiles
    best_score = float("inf")
    for cols in range(1, n_tiles + 1):
        rows = math.ceil(n_tiles / cols)
        empty_cells = (cols * rows) - n_tiles
        ratio = cols / rows
        score = (empty_cells * 3.0) + abs(math.log(ratio / target_ratio))
        if score < best_score:
            best_cols = cols
            best_rows = rows
            best_score = score
    return best_cols, best_rows


def _fitted_size(
    width: int,
    height: int,
    max_width: int,
    max_height: int,
) -> tuple[int, int]:
    scale = min(max_width / width, max_height / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def render_sheet(sources: list[TileSource]) -> Image.Image:
    """候補画像を決定論的なグリッドへ配置する。"""

    if not sources:
        raise ValueError("no candidate out.png found")

    sizes: list[tuple[int, int]] = []
    for source in sources:
        with Image.open(source.path) as image:
            sizes.append(image.size)

    cell_width = max(width for width, _ in sizes)
    cell_height = max(height for _, height in sizes)
    cols, rows = choose_grid(len(sources))
    tile_height = LABEL_HEIGHT + cell_height
    canvas_width = (2 * OUTER_PADDING) + (cols * cell_width) + ((cols - 1) * GRID_GAP)
    canvas_height = (2 * OUTER_PADDING) + (rows * tile_height) + ((rows - 1) * GRID_GAP)

    canvas = Image.new("RGB", (canvas_width, canvas_height), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()

    for index, source in enumerate(sources):
        row, col = divmod(index, cols)
        base_x = OUTER_PADDING + col * (cell_width + GRID_GAP)
        base_y = OUTER_PADDING + row * (tile_height + GRID_GAP)
        draw.text((base_x + 8, base_y + 10), source.label, fill=LABEL_COLOR, font=font)

        with Image.open(source.path) as image:
            rgba = image.convert("RGBA")
            fitted_size = _fitted_size(rgba.width, rgba.height, cell_width, cell_height)
            if fitted_size != rgba.size:
                rgba = rgba.resize(fitted_size, Image.Resampling.LANCZOS)

        paste_x = base_x + ((cell_width - rgba.width) // 2)
        paste_y = base_y + LABEL_HEIGHT + ((cell_height - rgba.height) // 2)
        canvas.paste(rgba, (paste_x, paste_y), rgba)

    return canvas


def limit_long_edge(image: Image.Image, max_long_edge: int) -> Image.Image:
    """長辺が上限を超える場合だけ縮小する。"""

    long_edge = max(image.size)
    if long_edge <= max_long_edge:
        return image

    scale = max_long_edge / long_edge
    new_size = tuple(max(1, round(dimension * scale)) for dimension in image.size)
    return image.resize(new_size, Image.Resampling.LANCZOS)


def resolve_output_path(run_dir: Path, requested: Path | None) -> Path:
    """出力先を既定の codex_generated directory 配下に限定して返す。"""

    output_dir = DEFAULT_OUTPUT_DIR.resolve()
    if requested is None:
        output = output_dir / f"{run_dir.name}_contact_sheet.png"
    else:
        requested = requested.expanduser()
        output = requested if requested.is_absolute() else output_dir / requested

    output = output.resolve()
    if not output.is_relative_to(output_dir):
        raise ValueError(f"output must be inside output directory: {output_dir}")
    return output


def main() -> int:
    args = _parse_args()
    try:
        run_dir = args.run_dir.expanduser().resolve()
        if not run_dir.is_dir():
            raise ValueError(f"run directory not found: {run_dir}")

        sources = collect_sources(run_dir)
        if not sources:
            raise ValueError(f"no candidate out.png found under: {run_dir / 'candidates'}")

        output = resolve_output_path(run_dir, args.out)
        print(f"sources={len(sources)} out={output} max_long_edge={args.max_long_edge}")
        for index, source in enumerate(sources, start=1):
            print(f"{index:02d} {source.label} {source.path}")

        if args.dry_run:
            return 0

        output.parent.mkdir(parents=True, exist_ok=True)
        sheet = limit_long_edge(render_sheet(sources), args.max_long_edge)
        sheet.save(output, format="PNG")
        print(f"saved={output} size={sheet.width}x{sheet.height}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
