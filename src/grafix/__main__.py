"""
Purpose:
    `python -m grafix`のsubcommandを各devtoolへ遅延dispatchする。
Use when:
    package CLIのcommand追加、削除、または子parserへの引数委譲を変更する場合。
Constraints:
    - subcommand実装は選択後にだけimportし、未使用のGUIやtool依存を初期化しない。
    - 親子parser境界の`--`を除き、残りの引数を子CLIへそのまま渡す。
"""

from __future__ import annotations

import argparse
import sys


def _delegated_args(rest: list[str]) -> list[str]:
    """親 parser と子 parser の境界に置かれた ``--`` を除く。"""

    args = list(rest)
    if args and args[0] == "--":
        return args[1:]
    return args


def main(argv: list[str] | None = None) -> int:
    """Grafix CLI の subcommand を実行する。

    Parameters
    ----------
    argv : list[str] or None, optional
        CLI 引数。None の場合は ``sys.argv`` を使う。

    Returns
    -------
    int
        subcommand の process exit code。
    """

    if argv is None:
        argv = sys.argv[1:]

    p = argparse.ArgumentParser(prog="python -m grafix")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser(
        "benchmark",
        help="比較可能な性能 case の計測・比較・report 生成",
        add_help=False,
    )
    sub.add_parser(
        "export",
        help="draw(t) を headless で SVG/PNG/G-code に書き出す",
        add_help=False,
    )
    sub.add_parser(
        "variations",
        help="named variations を thumbnail/contact sheet に一括書き出しする",
        add_help=False,
    )
    sub.add_parser(
        "run",
        help="sketch.pyをinteractive previewで実行し、任意で変更をwatchする",
        add_help=False,
    )
    sub.add_parser(
        "stub",
        help="project-local な grafix.api stub を生成する",
        add_help=False,
    )
    sub.add_parser(
        "init",
        help="最小 Grafix project を既存file非上書きで作る",
        add_help=False,
    )
    sub.add_parser(
        "examples",
        help="同梱 example の一覧表示・コピー",
        add_help=False,
    )
    sub.add_parser(
        "doctor",
        help="GL・外部command・MIDI・font・出力先を診断する",
        add_help=False,
    )
    sub.add_parser(
        "list",
        help="組み込み effect / primitive を一覧表示する",
        add_help=False,
    )
    sub.add_parser(
        "describe",
        help="operation の説明・引数・source を表示する",
        add_help=False,
    )
    sub.add_parser(
        "config",
        help="runtime config の validation / effective value 表示",
        add_help=False,
    )

    args, rest = p.parse_known_args(argv)

    if args.cmd == "benchmark":
        from grafix.devtools.benchmarks import cli

        return int(cli.main(_delegated_args(rest)))

    if args.cmd == "stub":
        from grafix.devtools import generate_stub

        return int(generate_stub.main(_delegated_args(rest)))

    if args.cmd == "init":
        from grafix.devtools import onboarding

        return int(onboarding.main_init(_delegated_args(rest)))

    if args.cmd == "examples":
        from grafix.devtools import onboarding

        return int(onboarding.main_examples(_delegated_args(rest)))

    if args.cmd == "doctor":
        from grafix.devtools import doctor

        return int(doctor.main(_delegated_args(rest)))

    if args.cmd == "export":
        from grafix.devtools import export_frame

        return int(export_frame.main(_delegated_args(rest)))

    if args.cmd == "variations":
        from grafix.devtools import variation_batch

        return int(variation_batch.main(_delegated_args(rest)))

    if args.cmd == "run":
        from grafix.devtools import run_sketch

        return int(run_sketch.main(_delegated_args(rest)))

    if args.cmd == "list":
        from grafix.devtools import list_builtins

        return int(list_builtins.main(_delegated_args(rest)))

    if args.cmd == "describe":
        from grafix.devtools import describe_op

        return int(describe_op.main(_delegated_args(rest)))

    if args.cmd == "config":
        from grafix.devtools import config_cli

        return int(config_cli.main(_delegated_args(rest)))

    raise AssertionError(f"unknown cmd: {args.cmd!r}")


if __name__ == "__main__":
    raise SystemExit(main())
