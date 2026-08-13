"""
Purpose:
    sketch source を interactive application へ配線し、必要な場合だけ transactional watch reload を有効にする CLI 境界を提供する。
Use when:
    ``python -m grafix run`` の引数、source reload 接続、worker/MIDI/GUI 起動・終了 code を変更・調査する場合。
Constraints:
    - watch candidate は全体の構築に成功したときだけ live generation へ採用し、失敗時は last-good を保つ。
    - MIDI 無効化 token は exact ``--midi-port none`` とし、``--workers 0`` の同期評価と混同しない。
    - config は CLI 入口で一度解決し、reload や application 内で ambient 再探索しない。
Side effects:
    sketch を import/exec し、filesystem watch、window、MIDI、draw worker を起動することがある。
"""

from __future__ import annotations

import argparse
import contextlib
from pathlib import Path
import sys
import traceback
from typing import Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m grafix run",
        description="sketch.pyのdraw(t)をinteractive previewで実行します。",
    )
    parser.add_argument("sketch", type=Path, help="draw(t)を定義したPython source")
    parser.add_argument(
        "--watch",
        action="store_true",
        help="mtime pollingで変更を検出し、成功時だけlive runtimeへ反映する",
    )
    parser.add_argument("--config", type=Path, default=None, help="config.yaml path")
    parser.add_argument("--run-id", default=None, help="output/session識別子")
    parser.add_argument(
        "--parameter-gui",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Inspector/Parameter GUIを表示する",
    )
    parser.add_argument(
        "--parameter-persistence",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="parameter autosave/recoveryを有効にする",
    )
    parser.add_argument(
        "--midi-port",
        default="auto",
        help="MIDI port名。exact 'none' だけが無効化token",
    )
    parser.add_argument("--midi-mode", choices=("7bit", "14bit"), default="7bit")
    parser.add_argument("--workers", type=int, default=1, help="draw worker数。0は同期")
    parser.add_argument(
        "--evaluation-timeout",
        type=float,
        default=5.0,
        help="background draw timeout秒",
    )
    parser.add_argument("--fps", type=float, default=60.0, help="preview/recording fps")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI引数を解釈してinteractive runnerを起動する。"""

    args = _parser().parse_args(None if argv is None else list(argv))
    if args.workers < 0:
        _parser().error("--workersは0以上である必要があります")
    if args.evaluation_timeout <= 0.0:
        _parser().error("--evaluation-timeoutは正の値である必要があります")

    from grafix.api._runner_application import _run_interactive_application
    from grafix.runtime_config_loader import runtime_config_with_fallback
    from grafix.interactive.runtime.source_reload import (
        SourceReloadController,
        source_reload_context,
    )

    try:
        effective_config, config_fallback = runtime_config_with_fallback(args.config)
        with SourceReloadController(
            args.sketch,
            config=effective_config,
        ) as controller:
            midi_port_name = (
                None
                if args.midi_port == "none"
                else args.midi_port
            )
            watch_context = (
                source_reload_context(controller)
                if bool(args.watch)
                else contextlib.nullcontext()
            )
            with watch_context:
                _run_interactive_application(
                    controller.draw,
                    config=effective_config,
                    config_fallback=config_fallback,
                    run_id=args.run_id,
                    parameter_gui=bool(args.parameter_gui),
                    parameter_persistence=bool(args.parameter_persistence),
                    midi_port_name=midi_port_name,
                    midi_mode=str(args.midi_mode),
                    n_worker=int(args.workers),
                    evaluation_timeout=float(args.evaluation_timeout),
                    fps=float(args.fps),
                )
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(
            "".join(traceback.format_exception_only(type(exc), exc)).strip(),
            file=sys.stderr,
        )
        return 1
    return 0


__all__ = ["main"]
