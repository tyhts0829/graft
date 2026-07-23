"""``python -m grafix export`` の共通 render/capture CLI。"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from grafix.api.export import save
from grafix.api.render import (
    ExportFormat,
    ExportResult,
    ParameterLoadMode,
    RenderOptions,
    RenderSession,
)
from grafix.export.output_paths import output_path_for_draw
from grafix.core.runtime_config import RuntimeConfig, bind_runtime_config
from grafix.runtime_config_loader import load_runtime_config
from grafix.export.image import default_png_output_path


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m grafix export")
    parser.add_argument(
        "--callable",
        required=True,
        help="module:attr 形式（例: sketch.main:draw）",
    )
    parser.add_argument(
        "--t",
        nargs="+",
        type=float,
        default=None,
        help="draw(t) に渡す時刻（複数指定可、既定: 0.0）",
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
        "--format",
        type=ExportFormat,
        choices=tuple(ExportFormat),
        default=None,
        help="出力形式。--out 指定時は suffix から推論し、省略時は PNG",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="出力 path（.svg/.png/.gcode、--t が 1 つのときのみ）",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="出力ディレクトリ（省略時: config の既定出力先）",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="既定出力 path と saved/recovery ParamStore の run_id suffix",
    )
    parser.add_argument(
        "--parameter-source",
        default="code",
        metavar="{code,saved,recovery}|PATH",
        help="parameter 読み込み元（既定: code。暗黙ファイルを読まない）",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="config.yaml のパス（指定した場合は探索より優先）",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="作品の再現用 seed（乱数 global state は変更せず manifest に記録）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="要求 path の既存 artifact/manifest generation を明示的に置換する",
    )

    args = parser.parse_args(argv)
    ts = [0.0] if args.t is None else list(args.t)
    if args.out is not None and args.out_dir is not None:
        parser.error("--out と --out-dir は同時に指定できません")
    if args.out is not None and len(ts) != 1:
        parser.error(
            "--out は --t が 1 つのときだけ指定できます（複数枚は --out-dir を使ってください）"
        )

    if args.out is None:
        args.export_format = (
            ExportFormat.PNG if args.format is None else args.format
        )
    else:
        try:
            args.export_format = ExportFormat.resolve(args.out, args.format)
        except ValueError as exc:
            parser.error(str(exc))
    return args


def _resolve_callable(spec: str) -> Callable[[float], Any]:
    module_name, separator, attr_path = str(spec).partition(":")
    if not separator or not module_name.strip() or not attr_path.strip():
        raise ValueError("--callable は module:attr 形式で指定してください")

    module = importlib.import_module(module_name)
    output: Any = module
    for part in attr_path.split("."):
        output = getattr(output, part)
    if not callable(output):
        raise TypeError(f"指定された callable が呼び出し可能ではありません: {spec!r}")
    return output


def _parameter_source(value: str) -> ParameterLoadMode:
    """CLI 文字列を明示 parameter source または Path に変換する。"""

    text = str(value).strip()
    keyword = text.casefold()
    if keyword in {"code", "saved", "recovery"}:
        return cast(ParameterLoadMode, keyword)
    if not text:
        raise ValueError("--parameter-source は空にできません")
    return Path(text)


def _frame_output_paths(base_path: Path, *, n_frames: int) -> list[Path]:
    n = int(n_frames)
    if n <= 0:
        return []
    if n == 1:
        return [base_path]

    width = max(3, len(str(n)))
    return [
        base_path.with_name(f"{base_path.stem}_f{index:0{width}d}{base_path.suffix}")
        for index in range(1, n + 1)
    ]


def _default_output_path(
    draw: Callable[[float], Any],
    *,
    export_format: ExportFormat,
    run_id: str | None,
    canvas_size: tuple[int, int],
    png_scale: float,
    config: RuntimeConfig,
) -> Path:
    """形式ごとの既存出力規則を使って要求 base path を返す。"""

    if export_format is ExportFormat.PNG:
        return default_png_output_path(
            draw,
            scale=png_scale,
            run_id=run_id,
            canvas_size=canvas_size,
            config=config,
        )
    return output_path_for_draw(
        kind=export_format.value,
        ext=export_format.value,
        draw=draw,
        run_id=run_id,
        canvas_size=canvas_size,
        config=config,
    )


def _print_result(*, t: float, result: ExportResult) -> None:
    """要求 path ではなく CaptureService が確定した実 path を表示する。"""

    print(f"Saved {result.format.value.upper()}: {result.path} (t={float(t)})")
    print(f"Manifest: {result.manifest_path}")


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    args = _parse_args(argv)
    config = load_runtime_config(args.config)
    with bind_runtime_config(config):
        draw = _resolve_callable(args.callable)
    ts = [0.0] if args.t is None else [float(t) for t in args.t]
    canvas_w, canvas_h = args.canvas
    canvas_size = (int(canvas_w), int(canvas_h))
    run_id = None if args.run_id is None else str(args.run_id)
    parameter_source = _parameter_source(args.parameter_source)
    export_format = cast(ExportFormat, args.export_format)

    with RenderSession(
        draw,
        options=RenderOptions(canvas_size=canvas_size),
        parameter_source=parameter_source,
        config=config,
        run_id=run_id,
        seed=args.seed,
    ) as session:
        explicit_path = None if args.out is None else Path(str(args.out))
        base_path = (
            _default_output_path(
                draw,
                export_format=export_format,
                run_id=run_id,
                canvas_size=canvas_size,
                png_scale=session.config.png_scale,
                config=session.config,
            )
            if explicit_path is None
            else explicit_path
        )
        if args.out_dir is not None:
            base_path = Path(str(args.out_dir)) / base_path.name

        paths = _frame_output_paths(base_path, n_frames=len(ts))
        for frame_t, path in zip(ts, paths, strict=True):
            frame = session.render(frame_t)
            result = save(frame, path, overwrite=bool(args.overwrite))
            _print_result(t=frame_t, result=result)

    return 0


__all__ = ["main"]
