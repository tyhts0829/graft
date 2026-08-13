"""
Purpose:
    named variation を一つの headless render session から thumbnail/contact sheet/summary の batch generation として出力する CLI を提供する。
Use when:
    ``python -m grafix variations`` の選択・thumbnail・parameter source・partial failure と process exit の配線を変更・調査する場合。
Constraints:
    - request 順・unknown variation・item ごとの rollback/partial failure は API owner、workspace/publish transaction は export owner に留める。
    - batch 内では同じ ``RenderSession`` の config/catalog/resource を再利用する。
    - item failure を隠さず summary と stderr に残し、1件でも失敗した batch は非0で終了する。
Side effects:
    config/parameter を読み、target module を import し、batch directory generation を書き込む。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from grafix.api.render import ExportFormat, RenderOptions, RenderSession
from grafix.api.variation_batch import render_variation_batch
from grafix.core.runtime_config import bind_runtime_config
from grafix.runtime_config_loader import load_runtime_config
from grafix.devtools.export_frame import _parameter_source, _resolve_callable


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m grafix variations")
    parser.add_argument(
        "--callable",
        required=True,
        help="module:attr 形式（例: sketch.main:draw）",
    )
    parser.add_argument(
        "--out-dir",
        required=True,
        help="no-clobber batch directory を作る親ディレクトリ",
    )
    parser.add_argument(
        "--name",
        action="append",
        dest="names",
        default=None,
        help="render する variation 名。複数回指定可（省略時は全件）",
    )
    parser.add_argument(
        "--canvas",
        nargs=2,
        type=int,
        default=(800, 800),
        metavar=("W", "H"),
        help="canvas_size (width height)。既定: 800 800",
    )
    parser.add_argument(
        "--t",
        type=float,
        default=0.0,
        help="variation に時刻が無い場合の draw(t)。既定: 0.0",
    )
    parser.add_argument(
        "--thumbnail-format",
        choices=("png", "svg"),
        default="png",
        help="thumbnail 形式。既定: png",
    )
    parser.add_argument(
        "--thumbnail-size",
        nargs=2,
        type=int,
        default=(320, 320),
        metavar=("W", "H"),
        help="PNG解像度/contact sheet表示サイズ。既定: 320 320",
    )
    parser.add_argument(
        "--columns",
        type=int,
        default=None,
        help="contact sheet の列数（省略時は件数から決定）",
    )
    parser.add_argument(
        "--batch-name",
        default="variations",
        help="--out-dir 内の batch directory 名。既定: variations",
    )
    parser.add_argument(
        "--parameter-source",
        default="saved",
        metavar="{saved,recovery,code}|PATH",
        help="named variations の読込元。既定: saved",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="saved/recovery ParamStore の run_id suffix",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="config.yaml のパス（指定した場合は探索より優先）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存 batch generation を明示的に置換する",
    )
    args = parser.parse_args(argv)
    if args.columns is not None and int(args.columns) <= 0:
        parser.error("--columns は正の整数で指定してください")
    if any(int(value) <= 0 for value in args.thumbnail_size):
        parser.error("--thumbnail-size は正の整数で指定してください")
    return args


def main(argv: list[str] | None = None) -> int:
    """Named variation batch を実行し、partial failure 時は 1 を返す。"""

    args = _parse_args(sys.argv[1:] if argv is None else argv)
    config = load_runtime_config(args.config)
    with bind_runtime_config(config):
        draw = _resolve_callable(args.callable)
    canvas_w, canvas_h = args.canvas
    with RenderSession(
        draw,
        options=RenderOptions(canvas_size=(int(canvas_w), int(canvas_h))),
        parameter_source=_parameter_source(args.parameter_source),
        config=config,
        run_id=args.run_id,
    ) as session:
        result = render_variation_batch(
            session,
            Path(args.out_dir),
            variation_names=(None if args.names is None else tuple(args.names)),
            default_t=float(args.t),
            thumbnail_format=ExportFormat(str(args.thumbnail_format)),
            thumbnail_size=(
                int(args.thumbnail_size[0]),
                int(args.thumbnail_size[1]),
            ),
            columns=args.columns,
            batch_name=str(args.batch_name),
            overwrite=bool(args.overwrite),
        )

    print(f"Batch: {result.output_directory}")
    print(
        f"{result.success_count} succeeded, {result.failure_count} failed · "
        f"Contact sheet: {result.contact_sheet_path} · Summary: {result.summary_path}"
    )
    for item in result.items:
        if item.status == "failed":
            print(
                f"{item.variation_name}: {item.error_type}: {item.error_message}",
                file=sys.stderr,
            )
    return 0 if result.failure_count == 0 else 1


__all__ = ["main"]
